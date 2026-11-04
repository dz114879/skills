"""Moved defenses and cross-file production/test reachability."""

import ast
from collections import Counter
from dataclasses import dataclass, field
import io
import os
from pathlib import Path, PurePosixPath
import re

from .ast_helpers import (
    CLASS_DEFINITION,
    MODULE_SCOPE,
    CandidateRecords,
    ReferenceCounts,
    allocation_batch,
    context_tokens,
    parameter_defaults,
    parameters,
    parse_source,
    scope_index,
    terminal_name,
    tree_index,
    unparse,
    walk,
)
from .probes_prod import CandidateProbes
from .registry import (
    PY10C_FRAMEWORK_DECORATORS,
    RULES,
    evidence_text,
    verify_text,
)
from .scope import (
    FileChange,
    excluded,
    git,
    intersects,
    raise_walk_error,
    role,
    touches,
)


def moved_defenses(
    before: dict[str, CandidateProbes],
    after: dict[str, CandidateProbes],
    changes: dict[str, FileChange],
):
    """Promote destination candidates in place by comparing signature presence per function.

    `before` and `after` map post-change paths to probes; `changes` maps them to
    their `FileChange`.
    """

    def index(snapshots):
        return {
            (path, candidate["symbol"], signature): candidate
            for path, probes in snapshots.items()
            for signature, candidate in probes.defenses
        }

    pre, post = index(before), index(after)
    surviving = {
        (path, symbol) for path, probes in after.items() for symbol in probes.functions
    }
    lost = {}
    for (path, symbol, signature), candidate in pre.items():
        if (path, symbol) in surviving and (path, symbol, signature) not in post:
            lost.setdefault(signature, []).append(
                evidence_text(
                    "PY10a_origin",
                    path=changes[path].old_path,
                    line=candidate["span"][0],
                    symbol=symbol,
                )
            )
    destinations = {}
    for path, probes in after.items():
        for signature, candidate in probes.defenses:
            identity = (path, candidate["symbol"], signature)
            if (
                signature in lost
                and identity not in pre
                and touches([candidate["span"]], changes[path].added_lines)
            ):
                destinations.setdefault(signature, {})[identity] = candidate

    for signature, found in destinations.items():
        # More new locations than functions that lost the signature is a coincidence.
        if len(found) > len(lost[signature]):
            continue
        rule, parts = signature
        for candidate in found.values():
            candidate["rule"] = "PY10a_moved_defense"
            candidate["change_tag"] = "moved-defense"
            candidate["evidence"] += evidence_text(
                "PY10a_moved_defense",
                rule=rule,
                signature=", ".join(parts),
                origins=", ".join(lost[signature]),
            )
            candidate["verify"] = verify_text(f"PY10a:{RULES[rule]['family']}")


def snapshot_sources(root, tip, excludes):
    if tip:
        entries = []
        for entry in git(root, "ls-tree", "-r", "-z", tip).split(b"\0")[:-1]:
            metadata, name = entry.split(b"\t", 1)
            mode, kind, oid = metadata.split()
            path = Path(os.fsdecode(name))
            if (
                kind == b"blob"
                and mode != b"120000"
                and path.suffix == ".py"
                and not excluded(path, excludes)
            ):
                entries.append((path.as_posix(), oid))
        if entries:
            batch = io.BytesIO(
                git(
                    root,
                    "cat-file",
                    "--batch",
                    input=b"".join(oid + b"\n" for _, oid in entries),
                )
            )
            for path, _ in entries:
                _, kind, size = batch.readline().split()
                raw = batch.read(int(size))
                batch.read(1)
                if b"\x00" not in raw:
                    yield path, raw
        return
    tracked = git(
        root,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
        allow_non_repo=True,
    )
    if tracked is not None:
        paths = {os.fsdecode(name) for name in tracked.split(b"\0")[:-1]}
    else:
        paths = set()
        for directory, dirs, names in os.walk(root, onerror=raise_walk_error):
            parent = Path(directory).relative_to(root)
            dirs[:] = [
                name
                for name in dirs
                if not excluded(parent / name, excludes)
                and not (root / parent / name).is_symlink()
            ]
            paths.update((parent / name).as_posix() for name in names)
    for name in sorted(paths):
        path = Path(name)
        target = root / path
        if (
            path.suffix != ".py"
            or excluded(path, excludes)
            or any((root / part).is_symlink() for part in (path, *path.parents))
            or not target.is_file()
        ):
            continue
        raw = target.read_bytes()
        if b"\x00" not in raw:
            yield name, raw


def target_name(name):
    return name is not None and len(name) >= 4 and not name.startswith("__")


@dataclass(frozen=True)
class CallSite:
    """A target-named call on a changed line of the diff."""

    name: str
    role: str
    source: str
    path: str
    line: int


def call_sites(tree, lines, path, file_role):
    for node in walk(tree):
        if isinstance(node, ast.Call) and intersects(node, lines):
            name = terminal_name(node.func)
            if target_name(name):
                yield CallSite(name, file_role, unparse(node), path, node.lineno)


def lost_calls(deleted, added):
    """Sorted base sites, by call name, of the (role, source) keys lost on balance."""
    losing = Counter((site.role, site.source) for site in deleted) - Counter(
        (site.role, site.source) for site in added
    )
    lost = {}
    for site in deleted:
        if (site.role, site.source) in losing:
            lost.setdefault(site.name, set()).add((site.path, site.line, site.role))
    return {name: sorted(sites) for name, sites in lost.items()}


def lost_call_clause(sites, changed):
    """Evidence clause naming the lost calls that triggered a definition."""
    if not sites:
        return ""
    clause = evidence_text(
        "PY10c_lost_calls",
        sites=", ".join(
            evidence_text("PY10c_lost_call_site", path=path, line=line, role=file_role)
            for path, line, file_role in sites
        ),
    )
    return clause if changed else clause + evidence_text("PY10c_definition_unchanged")


def framework_hook(node):
    cache = vars(node)
    if "_adc_framework_hook" not in cache:
        cache["_adc_framework_hook"] = any(
            context_tokens(decorator) & PY10C_FRAMEWORK_DECORATORS
            for decorator in node.decorator_list
        )
    return cache["_adc_framework_hook"]


@dataclass
class ImportedAlias:
    targets: set[str]
    ambiguous: bool


class CrossReferences:
    def __init__(self, tree, targets):
        index = tree_index(tree)
        self.targets = targets
        self.methods = index.methods
        self.definitions = [
            (node, symbol) for node, symbol in index.definitions if node.name in targets
        ]
        self.references = {}
        self.recursive_calls = index.recursive_calls
        self.delegations = index.delegations
        self.call_funcs = index.call_funcs
        self.include(index.attribute_references, targets)
        has_aliases = any(
            alias.name in targets
            for imports in index.scope_imports.values()
            for statement in imports
            for alias in statement.names
        )
        if not has_aliases:
            self.include(index.name_references, targets)
            return
        self.include(index.name_references, targets - index.name_uses.keys())
        environments = {}
        for scope in index.scopes:
            aliases = (
                {}
                if scope is MODULE_SCOPE
                else environments[index.scope_parents[scope]].copy()
            )
            bindings = index.scope_bindings[scope].copy()
            if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for param in parameters(scope):
                    bindings[param.arg] = bindings.get(param.arg, 0) + 1
            for name in bindings:
                aliases.pop(name, None)
            imports = {}
            for statement in index.scope_imports.get(scope, ()):
                for alias in statement.names:
                    if alias.name in targets:
                        imports.setdefault(alias.asname or alias.name, set()).add(
                            alias.name
                        )
            for name, imported in imports.items():
                aliases[name] = ImportedAlias(imported, bindings[name] > 1)
            environments[scope] = aliases
        for name, uses in index.name_uses.items():
            for node, scope in uses:
                alias = environments[scope].get(name)
                if alias is None:
                    self.reference(node, name, False)
                else:
                    for target in alias.targets:
                        self.reference(node, target, alias.ambiguous)

    def include(self, facts, names):
        for name in names & facts.keys():
            if name not in self.references:
                self.references[name] = ReferenceCounts()
            refs = self.references[name]
            refs.calls.extend(facts[name].calls)
            refs.non_calls += facts[name].non_calls

    def reference(self, node, name, ambiguous):
        if name in self.targets:
            if name not in self.references:
                self.references[name] = ReferenceCounts()
            refs = self.references[name]
            if node in self.call_funcs and not ambiguous:
                refs.calls.append(self.call_funcs[node])
            else:
                refs.non_calls += 1


def effective_arguments(node, call, forwarded):
    positional = node.args.posonlyargs + node.args.args
    defaults = parameter_defaults(node)
    if any(isinstance(arg, ast.Starred) for arg in call.args) or any(
        keyword.arg is None for keyword in call.keywords
    ):
        return None
    offset = int(
        isinstance(call.func, ast.Attribute)
        and bool(positional)
        and positional[0].arg in {"self", "cls"}
        and not any(
            terminal_name(dec.func if isinstance(dec, ast.Call) else dec)
            == "staticmethod"
            for dec in node.decorator_list
        )
    )
    if len(call.args) > len(positional) - offset and node.args.vararg is None:
        return None
    # Calls may omit defaulted parameters or spill extra args into *args.
    bound = {
        param.arg: value
        for param, value in zip(positional[offset:], call.args, strict=False)
    }
    if offset:
        bound[positional[0].arg] = call.func.value
    positional_only = {param.arg for param in node.args.posonlyargs}
    for keyword in call.keywords:
        name = keyword.arg
        if name in positional_only or name not in defaults:
            if node.args.kwarg is None:
                return None
        elif name in bound:
            return None
        else:
            bound[name] = keyword.value
    if any(default is None and name not in bound for name, default in defaults.items()):
        return None
    return {
        name: "<DEFAULT>"
        if name not in bound
        else repr(bound[name].value)
        if isinstance(bound[name], ast.Constant)
        else None
        for name, default in defaults.items()
        if default is not None
        and not (
            name in forwarded
            and name in bound
            and isinstance(bound[name], ast.Name)
            and bound[name].id == name
        )
    }


def constant_defaults(definition, calls, change_tag, clauses):
    path, source, node, symbol, recursive_calls = definition
    if not node.args.defaults and not any(node.args.kw_defaults):
        return []
    if framework_hook(node):
        return []
    defaults = parameter_defaults(node)
    rebound = scope_index(node).bindings
    forwarded = defaults.keys() - rebound
    bound = [
        *(effective_arguments(node, call, ()) for call in calls),
        *(effective_arguments(node, call, forwarded) for call in recursive_calls),
    ]
    if any(arguments is None for arguments in bound):
        return []
    uniform = []
    for name, default in defaults.items():
        if default is None:
            continue
        values = {arguments[name] for arguments in bound if name in arguments}
        if len(values) == 1 and None not in values:
            uniform.append((name, default, values.pop()))
    if not uniform:
        return []
    name, default, value = uniform[0]
    if len(uniform) == 1:
        evidence = evidence_text(
            "PY10c_constant_default_single",
            name=name,
            default=unparse(default),
            calls=len(calls),
            value=value,
            clauses=clauses,
        )
        verify_variant = "PY10c:default"
    else:
        params = ", ".join(
            evidence_text(
                "PY10c_constant_parameter",
                name=name,
                default=unparse(default),
                value=value,
            )
            for name, default, value in uniform
        )
        evidence = evidence_text(
            "PY10c_constant_default_bundle",
            parameters=params,
            calls=len(calls),
            clauses=clauses,
        )
        verify_variant = "PY10c:bundle"
    record = CandidateRecords(source)
    record.symbols = symbol.split(".")
    record.add(
        "PY10c_cross_test_reachability",
        default,
        evidence,
        verify_text(verify_variant),
        related=tuple(default for _, default, _ in uniform[1:]),
    )
    return [{**record.candidates[0], "path": path, "change_tag": change_tag}]


@dataclass
class ModuleScope:
    """Module-level `__all__` export names, definitions and static `from` imports."""

    exports: set[str] = field(default_factory=set)
    definitions: dict[str, list[ast.stmt]] = field(default_factory=dict)
    imports: dict[str, list[tuple[ast.ImportFrom, str]]] = field(default_factory=dict)


def module_scope(tree):
    index = tree_index(tree)
    return ModuleScope(
        index.module_exports, index.module_definitions, index.module_imports
    )


def import_origin(path, statement, paths):
    """Snapshot path of the module that `statement` in module `path` imports from."""
    # A module's package is its directory, for `a/b.py` and `a/b/__init__.py` alike.
    package = PurePosixPath(path).parent.parts
    if statement.level > len(package) + 1:
        return None
    base = package[: len(package) + 1 - statement.level] if statement.level else ()
    module = "/".join(
        (*base, *(statement.module.split(".") if statement.module else ()))
    )
    return next(
        (
            candidate
            for candidate in (f"{module}/__init__.py", f"{module}.py")
            if candidate in paths
        ),
        None,
    )


def public_contracts(scopes, paths):
    """Exported module-scope functions and public methods of exported classes."""
    public = set()
    pending = [(path, name) for path, scope in scopes.items() for name in scope.exports]
    seen = set()
    while pending:
        path, name = pending.pop()
        if (path, name) in seen or path not in scopes:
            continue
        seen.add((path, name))
        scope = scopes[path]
        for node in scope.definitions.get(name, []):
            if isinstance(node, ast.ClassDef):
                public.update(
                    child
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not child.name.startswith("_")
                )
            else:
                public.add(node)
        for statement, original in scope.imports.get(name, []):
            origin = import_origin(path, statement, paths)
            if origin is not None:
                pending.append((origin, original))
    return public


def external_base_clause(enclosing_class, classes):
    """Evidence clause listing enclosing-class bases the snapshot never defines."""
    if enclosing_class is None:
        return ""
    bases = [
        unparse(base)
        for base in enclosing_class.bases
        if terminal_name(base) != "object" and terminal_name(base) not in classes
    ]
    return evidence_text("PY10c_external_base", bases=", ".join(bases)) if bases else ""


WORDS = re.compile(r"\w+")


class TargetWords:
    def __init__(self, targets):
        self.words = frozenset(name for name in targets if WORDS.fullmatch(name))
        other = sorted(targets - self.words)
        self.other = (
            re.compile(r"\b(?:" + "|".join(map(re.escape, other)) + r")\b")
            if other
            else None
        )

    def search(self, source):
        return any(match[0] in self.words for match in WORDS.finditer(source)) or (
            self.other is not None and self.other.search(source) is not None
        )


@allocation_batch()
def cross_reachability(root, tip, args, selected, lost, active_trees, errors):
    """`selected` maps changed (path, line) definitions to names; `lost` maps
    net-lost call names to their base sites."""
    targets = {*selected.values(), *lost}
    if not targets:
        return [], 0
    # Re-exporting modules (`from .core import Client; __all__ = [...]`) may not
    # mention a target name, so `__all__` admits them to the parsed set.
    pattern = TargetWords({"__all__", *targets})
    definitions = {name: [] for name in targets}
    counts = {
        name: {"prod": ReferenceCounts(), "test": ReferenceCounts()} for name in targets
    }
    paths, classes = set(), set()
    scopes, method_classes = {}, {}
    delegations = set()
    for path, raw in snapshot_sources(root, tip, args.exclude):
        paths.add(path)
        # Raw text avoids a parse per file; a `class X` inside a string only
        # hides an external-base clue.
        classes.update(name.decode() for name in CLASS_DEFINITION.findall(raw))
        if path in active_trees:
            parsed = active_trees[path]
            if parsed and not pattern.search(parsed[0]):
                continue
        else:
            parsed = parse_source(raw, path, errors, pattern=pattern)
        if not parsed:
            continue
        source, tree = parsed
        file_role = role(Path(path), args.test_glob)
        refs = CrossReferences(tree, targets)
        for name, references in refs.references.items():
            total = counts[name][file_role]
            total.calls.extend(references.calls)
            total.non_calls += references.non_calls
        delegations.update(refs.delegations)
        if file_role == "prod":
            scopes[path] = module_scope(tree)
            method_classes.update(refs.methods)
            for node, symbol in refs.definitions:
                definitions[node.name].append(
                    (path, source, node, symbol, refs.recursive_calls.get(node, []))
                )
    public = public_contracts(scopes, paths)

    def emitted(path, node):
        # A diff emits changed definitions plus every namesake of a lost call;
        # a path audit keeps every same-named definition.
        return not args.base or (path, node.lineno) in selected or node.name in lost

    def clauses(path, node):
        return lost_call_clause(
            lost.get(node.name), (path, node.lineno) in selected
        ) + external_base_clause(method_classes.get(node), classes)

    candidates = []
    for name, defs in definitions.items():
        prod = counts[name]["prod"]
        test = counts[name]["test"]
        bindings = [call for call in prod.calls if call not in delegations]
        if (
            len(defs) == 1
            and defs[0][2] not in public
            and not prod.non_calls
            and bindings
            and emitted(defs[0][0], defs[0][2])
        ):
            candidates.extend(
                constant_defaults(
                    defs[0],
                    bindings,
                    "changed" if args.base else "path-audit",
                    clauses(defs[0][0], defs[0][2]),
                )
            )
        if prod.calls or prod.non_calls:
            continue
        tag = (
            "cross-test"
            if test.calls or test.non_calls
            else "orphaned"
            if name in lost
            else None
        )
        if tag is None:
            continue
        evidence = evidence_text(
            "PY10c_references",
            definitions=len(defs),
            prod_calls=len(prod.calls),
            prod_refs=prod.non_calls,
            test_calls=len(test.calls),
            test_refs=test.non_calls,
        )
        if len(defs) > 1:
            evidence += evidence_text(
                "PY10c_ambiguous_definitions", definitions=len(defs)
            )
        for path, source, node, symbol, _ in defs:
            if framework_hook(node) or node in public or not emitted(path, node):
                continue
            record = CandidateRecords(source)
            record.symbols = symbol.split(".")
            record.add(
                "PY10c_cross_test_reachability",
                node,
                evidence + clauses(path, node),
                verify_text("PY10c:reach"),
            )
            candidates.append({**record.candidates[0], "path": path, "change_tag": tag})
    return candidates, len(paths)
