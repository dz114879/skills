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
    CandidateRecords,
    binding_names,
    context_tokens,
    local_walk,
    parameter_defaults,
    parameters,
    parse_source,
    terminal_name,
)
from .probes_prod import CandidateProbes
from .registry import (
    PY10A_BASE_SECTIONS,
    PY10C_FRAMEWORK_DECORATORS,
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
            candidate["verify"] = verify_text(
                "PY10a_moved_defense", base_section=PY10A_BASE_SECTIONS[rule]
            )


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
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and intersects(node, lines):
            name = terminal_name(node.func)
            if target_name(name):
                yield CallSite(name, file_role, ast.unparse(node), path, node.lineno)


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
    return any(
        context_tokens(decorator) & PY10C_FRAMEWORK_DECORATORS
        for decorator in node.decorator_list
    )


@dataclass
class ImportedAlias:
    targets: set[str]
    ambiguous: bool


@dataclass
class ReferenceCounts:
    calls: list[ast.Call] = field(default_factory=list)
    non_calls: int = 0


class CrossReferences(ast.NodeVisitor):
    def __init__(self, tree, targets):
        self.targets = targets
        self.symbols = []
        self.enclosing_functions = []
        self.methods = {}
        self.aliases = {}
        self.definitions = []
        self.references = {name: ReferenceCounts() for name in targets}
        self.recursive_calls = {}
        self.delegations = set()
        self.call_funcs = {
            node.func: node for node in ast.walk(tree) if isinstance(node, ast.Call)
        }
        self.visit_scope(tree)

    def visit_scope(self, node):
        previous = self.aliases
        self.aliases = previous.copy()
        local = [child for statement in node.body for child in local_walk(statement)]
        bindings = {}
        for child in local:
            for name in binding_names(child):
                bindings.setdefault(name, set()).add(child)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for param in parameters(node):
                bindings.setdefault(param.arg, set()).add(param)
        for name in bindings:
            self.aliases.pop(name, None)
        imports = {}
        for child in local:
            if isinstance(child, ast.ImportFrom):
                for alias in child.names:
                    if alias.name in self.targets:
                        imports.setdefault(alias.asname or alias.name, set()).add(
                            alias.name
                        )
        for name, targets in imports.items():
            self.aliases[name] = ImportedAlias(targets, len(bindings[name]) > 1)
        for statement in node.body:
            self.visit(statement)
        self.aliases = previous

    def visit_FunctionDef(self, node):
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns:
            self.visit(node.returns)
        self.symbols.append(node.name)
        if node.name in self.targets:
            self.definitions.append((node, ".".join(self.symbols)))
        self.enclosing_functions.append(node)
        self.visit_scope(node)
        self.enclosing_functions.pop()
        self.symbols.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        for expr in (*node.decorator_list, *node.bases, *node.keywords):
            self.visit(expr)
        self.methods.update(
            (child, node)
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        self.symbols.append(node.name)
        self.visit_scope(node)
        self.symbols.pop()

    def enclosing(self, name):
        return next(
            (
                function
                for function in reversed(self.enclosing_functions)
                if function.name == name
            ),
            None,
        )

    def record_recursion(self, node, owner):
        # Self-recursion never counts as a reachability reference. A call that
        # reaches `owner` itself (a bare name outside a class body, `self.`/`cls.`
        # on a method) still binds its parameters for `constant_defaults`.
        if node in self.call_funcs and isinstance(node, ast.Attribute) == (
            owner in self.methods
        ):
            self.recursive_calls.setdefault(owner, []).append(self.call_funcs[node])

    def reference(self, node, name, *, ambiguous=False):
        if name in self.targets:
            refs = self.references[name]
            if node in self.call_funcs and not ambiguous:
                refs.calls.append(self.call_funcs[node])
            else:
                refs.non_calls += 1

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            owner = self.enclosing(node.id)
            if owner is not None:
                self.record_recursion(node, owner)
            elif node.id in self.aliases:
                alias = self.aliases[node.id]
                for name in alias.targets:
                    self.reference(node, name, ambiguous=alias.ambiguous)
            else:
                self.reference(node, node.id)

    def visit_Attribute(self, node):
        owner = self.enclosing(node.attr)
        receiver = node.value
        if (
            owner is not None
            and isinstance(receiver, ast.Name)
            and receiver.id in {"self", "cls"}
        ):
            self.record_recursion(node, owner)
        else:
            if (
                owner is not None
                and node in self.call_funcs
                and isinstance(receiver, ast.Call)
                and isinstance(receiver.func, ast.Name)
                and receiver.func.id == "super"
            ):
                # A `super()` delegation reaches a base definition, never `owner`.
                self.delegations.add(self.call_funcs[node])
            self.reference(node, node.attr)
        self.generic_visit(node)


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
    if framework_hook(node):
        return []
    defaults = parameter_defaults(node)
    rebound = {
        name
        for statement in node.body
        for child in local_walk(statement)
        for name in binding_names(child)
    }
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
            calls=len(calls),
            value=value,
            clauses=clauses,
        )
        verify_variant = "PY10c_constant_default_single"
    else:
        params = ", ".join(
            evidence_text("PY10c_constant_parameter", name=name, value=value)
            for name, _, value in uniform
        )
        evidence = evidence_text(
            "PY10c_constant_default_bundle",
            parameters=params,
            calls=len(calls),
            clauses=clauses,
        )
        verify_variant = "PY10c_constant_default_bundle"
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
    scope = ModuleScope()
    for statement in local_walk(tree):
        if not isinstance(statement, ast.stmt):
            continue
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope.definitions.setdefault(statement.name, []).append(statement)
        elif isinstance(statement, ast.ImportFrom):
            for alias in statement.names:
                if alias.name != "*":
                    scope.imports.setdefault(alias.asname or alias.name, []).append(
                        (statement, alias.name)
                    )
        parts = list(local_walk(statement))
        if any(
            isinstance(child, ast.Name) and child.id == "__all__" for child in parts
        ):
            scope.exports.update(
                child.value
                for child in parts
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            )
    return scope


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
        ast.unparse(base)
        for base in enclosing_class.bases
        if terminal_name(base) != "object" and terminal_name(base) not in classes
    ]
    return evidence_text("PY10c_external_base", bases=", ".join(bases)) if bases else ""


def cross_reachability(root, tip, args, selected, lost, active_trees, errors):
    """`selected` maps changed (path, line) definitions to names; `lost` maps
    net-lost call names to their base sites."""
    targets = {*selected.values(), *lost}
    if not targets:
        return [], 0
    # Re-exporting modules (`from .core import Client; __all__ = [...]`) may not
    # mention a target name, so `__all__` admits them to the parsed set.
    pattern = re.compile(
        r"\b(?:"
        + "|".join(re.escape(name) for name in ["__all__", *sorted(targets)])
        + r")\b"
    )
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
        for name in targets:
            total = counts[name][file_role]
            total.calls.extend(refs.references[name].calls)
            total.non_calls += refs.references[name].non_calls
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
            definition_label="definition" if len(defs) == 1 else "definitions",
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
                verify_text(
                    "PY10c_cross_test" if tag == "cross-test" else "PY10c_orphaned"
                ),
            )
            candidates.append({**record.candidates[0], "path": path, "change_tag": tag})
    return candidates, len(paths)
