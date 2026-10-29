#!/usr/bin/env python3
"""Read-only Python AST Candidate Extractor (Python 3.11+)."""

import argparse
import ast
from dataclasses import dataclass
import fnmatch
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tokenize
import sys


SKIP_SEGMENTS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "build",
    "dist",
    ".scratch",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
TEST_GLOBS = ("test_*.py", "*_test.py", "conftest.py", "*_test_support.py")
DIFF_FLAGS = (
    "--no-ext-diff",
    "--no-textconv",
    "-M",
    "--no-color",
    "--no-relative",
    "--src-prefix=a/",
    "--dst-prefix=b/",
)


class ScanError(Exception):
    """Invalid scan scope or failed Git operation."""


@dataclass
class FileChange:
    path: str
    added_lines: set[int] | None = None


def git(root, *args, allow_non_repo=False):
    result = subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(root), *args],
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"},
        capture_output=True,
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        if allow_non_repo and "not a git repository" in message:
            return None
        raise ScanError(
            f"git {' '.join(args)} failed: {message or 'exit ' + str(result.returncode)}"
        )
    return result.stdout


def worktree_root(directory, allow_non_repo=False):
    result = git(
        directory, "rev-parse", "--show-toplevel", allow_non_repo=allow_non_repo
    )
    return Path(os.fsdecode(result[:-1])).resolve() if result is not None else None


def resolve_ref(root, ref):
    return (
        git(
            root,
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            f"{ref}^{{commit}}",
        )
        .decode()
        .strip()
    )


def diff_scope(args):
    directory = Path(args.repo or ".").resolve()
    if not directory.is_dir():
        raise ScanError(f"Missing repository directory: {directory}")
    root = worktree_root(directory)
    base = resolve_ref(root, args.base)
    tip = resolve_ref(root, args.tip or "HEAD") if args.tip or args.merge_base else None
    if args.merge_base:
        base = git(root, "merge-base", base, tip).decode().strip()
    endpoints = [base, tip] if tip else [base]
    names = iter(
        git(root, "diff", *DIFF_FLAGS, "--name-status", "-z", *endpoints, "--").split(
            b"\0"
        )[:-1]
    )
    changes = []
    for status_bytes in names:
        status = status_bytes.decode()[0]
        old_path = os.fsdecode(next(names))
        path = os.fsdecode(next(names)) if status == "R" else old_path
        if (
            status == "D"
            or Path(path).suffix != ".py"
            or excluded(Path(path), args.exclude)
        ):
            continue
        patch = git(
            root,
            "diff",
            *DIFF_FLAGS,
            "--unified=0",
            "--inter-hunk-context=0",
            *endpoints,
            "--",
            *dict.fromkeys((old_path, path)),
        )
        if any(line.startswith(b"Binary files ") for line in patch.splitlines()):
            continue
        added = set()
        for match in re.finditer(
            rb"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", patch, re.MULTILINE
        ):
            start = int(match[1])
            count = int(match[2]) if match[2] is not None else 1
            added.update(range(start, start + count))
        changes.append(FileChange(path, added))
    if tip is None:
        for name in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(
            b"\0"
        )[:-1]:
            path = os.fsdecode(name)
            if Path(path).suffix == ".py" and not excluded(Path(path), args.exclude):
                changes.append(FileChange(path))
    return root, changes, tip


def matches(path, globs):
    return any(
        fnmatch.fnmatchcase(path.as_posix(), glob)
        or fnmatch.fnmatchcase(path.name, glob)
        for glob in globs
    )


def excluded(path, globs):
    return bool(SKIP_SEGMENTS.intersection(path.parts)) or matches(path, globs)


def role(path, test_globs):
    if {"test", "tests"}.intersection(path.parts) or matches(
        path, (*TEST_GLOBS, *test_globs)
    ):
        return "test"
    return "prod"


def raise_walk_error(error):
    raise error


def paths_scope(args):
    targets = [Path(path).resolve() for path in args.paths]
    for target in targets:
        if not target.exists():
            raise ScanError(f"Missing path: {target}")
    if args.repo:
        root = Path(args.repo).resolve()
        if not root.is_dir():
            raise ScanError(f"Missing repository directory: {root}")
        for target in targets:
            if not target.is_relative_to(root):
                raise ScanError(f"Path outside --repo: {target}")
    else:
        roots = {
            worktree_root(
                target if target.is_dir() else target.parent, allow_non_repo=True
            )
            for target in targets
        }
        if len(roots) == 1 and None not in roots:
            root = roots.pop()
        else:
            root = Path(os.path.commonpath(targets))
            if root.is_file():
                root = root.parent
    files = set()
    for target in targets:
        if excluded(target.relative_to(root), args.exclude):
            continue
        if target.is_dir():
            for directory, dirs, names in os.walk(target, onerror=raise_walk_error):
                parent = Path(directory)
                dirs[:] = [
                    name
                    for name in dirs
                    if not excluded((parent / name).relative_to(root), args.exclude)
                ]
                files.update(parent / name for name in names)
        else:
            files.add(target)
    return root, [
        FileChange(path.relative_to(root).as_posix())
        for path in sorted(files)
        if path.suffix == ".py"
        and not path.is_symlink()
        and not excluded(path.relative_to(root), args.exclude)
    ]


def assignments(node):
    for child in local_walk(node):
        if isinstance(child, ast.AnnAssign) and child.value is not None:
            yield child.target
        elif isinstance(child, ast.Assign):
            yield from child.targets


def bound_targets(target):
    if isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from bound_targets(element)
    elif isinstance(target, ast.Starred):
        yield from bound_targets(target.value)
    else:
        yield target


def declarations(tree):
    attributes = {}
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for statement in cls.body:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                targets = (
                    [statement.target]
                    if isinstance(statement, ast.AnnAssign)
                    else statement.targets
                )
                for target in targets:
                    for name in bound_targets(target):
                        if isinstance(name, ast.Name):
                            attributes.setdefault(name.id, []).append(
                                (cls.name, statement.lineno)
                            )
            elif (
                isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
                and statement.name == "__init__"
            ):
                for child in statement.body:
                    for target in assignments(child):
                        for attribute in bound_targets(target):
                            if (
                                isinstance(attribute, ast.Attribute)
                                and isinstance(attribute.value, ast.Name)
                                and attribute.value.id == "self"
                            ):
                                attributes.setdefault(attribute.attr, []).append(
                                    (cls.name, attribute.lineno)
                                )
    for clues in attributes.values():
        clues.sort(key=lambda clue: (clue[1], clue[0]))
    return attributes


def local_walk(node):
    yield node
    if not isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    ):
        for child in ast.iter_child_nodes(node):
            yield from local_walk(child)


def terminal_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return terminal_name(node.value)
    return None


def terminal_names(node):
    return {
        terminal_name(item)
        for item in (node.elts if isinstance(node, ast.Tuple) else [node])
    }


def irrefutable(pattern):
    if isinstance(pattern, ast.MatchAs):
        return pattern.pattern is None or irrefutable(pattern.pattern)
    if isinstance(pattern, ast.MatchOr):
        return any(irrefutable(child) for child in pattern.patterns)
    return False


def exits(body):
    """Explicit control-flow exits; only fallthrough reaches the next statement."""
    result = {"next"}
    for statement in body:
        if "next" not in result:
            break
        result = (result - {"next"}) | statement_exits(statement)
    return result


def statement_exits(node):
    if isinstance(node, ast.Raise):
        return {"bare" if node.exc is None else "raise"}
    if isinstance(node, (ast.Return, ast.Break, ast.Continue)):
        return {type(node).__name__.lower()}
    if isinstance(node, ast.If):
        return exits(node.body) | exits(node.orelse)
    if isinstance(node, (ast.Try, ast.TryStar)):
        pending = exits(node.body)
        if "next" in pending:
            pending = (pending - {"next"}) | exits(node.orelse)
        for handler in node.handlers:
            pending |= exits(handler.body)
        final = exits(node.finalbody)
        return (final - {"next"}) | (pending if "next" in final else set())
    if isinstance(node, (ast.With, ast.AsyncWith)):
        result = exits(node.body)
        # An unknown context manager can suppress a raise via __exit__.
        if result & {"bare", "raise"}:
            result.add("next")
        return result
    if isinstance(node, ast.Match):
        result = set()
        for case in node.cases:
            result |= exits(case.body)
            if case.guard is None and irrefutable(case.pattern):
                break
        else:
            result.add("next")
        return result
    if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
        # Loops do not prove a raise; preserve possible early returns as well.
        return {"next"} | (
            (exits(node.body) | exits(node.orelse)) - {"break", "continue"}
        )
    return {"next"}


def is_none(node):
    return isinstance(node, ast.Constant) and node.value is None


def none_check(node):
    if (
        isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and len(node.ops) == 1
        and isinstance(node.ops[0], (ast.Is, ast.IsNot))
        and is_none(node.comparators[0])
    ):
        return node.left.id
    return None


def literal_truth(node):
    if isinstance(node, ast.Constant) and node.value is not None:
        return bool(node.value)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        if not any(isinstance(item, ast.Starred) for item in node.elts):
            return bool(node.elts)
    if isinstance(node, ast.Dict) and all(key is not None for key in node.keys):
        return bool(node.keys)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"dict", "list", "set"}
        and not node.args
        and not node.keywords
    ):
        return False
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.UAdd, ast.USub))
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float, complex))
    ):
        return bool(node.operand.value)
    return None


def simple_assignment(node):
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        target = node.target
    else:
        return None
    if isinstance(target, ast.Name):
        return target.id, node.value
    return None


def get_call(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
    )


def parameters(node):
    return node.args.posonlyargs + node.args.args + node.args.kwonlyargs


def load_names(node):
    return [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    ]


def parameter_defaults(node):
    positional = node.args.posonlyargs + node.args.args
    values = [None] * (len(positional) - len(node.args.defaults)) + node.args.defaults
    return {
        param.arg: value
        for param, value in zip(parameters(node), values + node.args.kw_defaults)
    }


def prefix_boundary(node):
    return isinstance(
        node,
        (
            ast.If,
            ast.For,
            ast.AsyncFor,
            ast.While,
            ast.Try,
            ast.TryStar,
            ast.With,
            ast.AsyncWith,
            ast.Match,
            ast.Return,
            ast.Raise,
        ),
    ) or any(
        isinstance(child, (ast.Yield, ast.YieldFrom)) for child in local_walk(node)
    )


def concrete_annotation(node):
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return None
    excluded = {"Any", "object", "Optional", "Union", "None"}
    if any(
        terminal_name(child) in excluded
        or is_none(child)
        or (isinstance(child, ast.BinOp) and isinstance(child.op, ast.BitOr))
        for child in ast.walk(node)
    ):
        return None
    return terminal_name(node)


def binding_names(node):
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        return {node.id}
    if isinstance(
        node,
        (
            ast.FunctionDef,
            ast.AsyncFunctionDef,
            ast.ClassDef,
            ast.ExceptHandler,
            ast.MatchAs,
            ast.MatchStar,
        ),
    ):
        return {node.name} if node.name is not None else set()
    if isinstance(node, ast.MatchMapping):
        return {node.rest} if node.rest is not None else set()
    if isinstance(node, ast.alias):
        return {node.asname or node.name.split(".")[0]}
    return set()


class CandidateProbes(ast.NodeVisitor):
    def __init__(self, source, tree):
        self.source = source
        self.attributes = declarations(tree)
        self.imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.imported.update(
                    alias.asname or alias.name.split(".")[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                self.imported.update(alias.asname or alias.name for alias in node.names)
        self.symbols = []
        self.candidates = []

    def add(self, rule, node, evidence, verify, related=()):
        nodes = (node, *related)
        spans = [(item.lineno, item.end_lineno) for item in nodes]
        code = "; ".join(ast.get_source_segment(self.source, item) for item in nodes)
        self.candidates.append(
            {
                "rule": rule,
                "span": [
                    min(start for start, _ in spans),
                    max(end for _, end in spans),
                ],
                "symbol": ".".join(self.symbols) or "<module>",
                "code": " ".join(code.split())[:200],
                "evidence": evidence,
                "verify": verify,
                "_spans": spans,
            }
        )

    def generic_visit(self, node):
        for _, value in ast.iter_fields(node):
            if (
                isinstance(value, list)
                and value
                and all(isinstance(item, ast.stmt) for item in value)
            ):
                self.default_shadows(value)
        super().generic_visit(node)

    def default_shadows(self, body):
        for index, statement in enumerate(body):
            assignment = simple_assignment(statement)
            if assignment is None:
                continue
            name, value = assignment
            via_get = get_call(value) and len(value.args) == 2 and not value.keywords
            if via_get:
                fallback = value.args[1]
            elif isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or):
                fallback = value.values[-1]
            else:
                continue
            truth = literal_truth(fallback)
            if truth is None:
                continue
            for guard in body[index + 1 : index + 6]:
                if isinstance(guard, ast.If):
                    test = guard.test
                    matches = none_check(test) == name and isinstance(
                        test.ops[0], ast.Is
                    )
                    matches |= (
                        not via_get
                        and truth
                        and isinstance(test, ast.UnaryOp)
                        and isinstance(test.op, ast.Not)
                        and isinstance(test.operand, ast.Name)
                        and test.operand.id == name
                    )
                    if matches and exits(guard.body) & {"raise", "bare", "return"}:
                        note = (
                            " The .get() default shadows missing keys unless None is explicitly stored."
                            if via_get
                            else ""
                        )
                        self.add(
                            "PY03_default_shadows_check",
                            statement,
                            f"Default for '{name}' at line {statement.lineno} precedes "
                            f"required-value guard at line {guard.lineno}",
                            f"Does the immediate producer require '{name}' to be present, "
                            "making this default hide rejection, or is omission an accepted "
                            "boundary input (Counterweight)?" + note,
                            related=(guard.test, *guard.body),
                        )
                if any(name in binding_names(child) for child in local_walk(guard)):
                    break

    def visit_BoolOp(self, node):
        fallback = node.values[-1]
        truth = literal_truth(fallback)
        if (
            isinstance(node.op, ast.Or)
            and truth is not None
            and not (
                truth and isinstance(fallback, (ast.List, ast.Tuple, ast.Set, ast.Dict))
            )
            and any(
                isinstance(value, (ast.Subscript, ast.Attribute)) or get_call(value)
                for value in node.values[:-1]
            )
        ):
            verify = (
                'Does the immediate producer permit legitimate 0/""/False values that '
                "this fallback overwrites, breaching Semantic Fidelity, or does its "
                "boundary contract explicitly equate them (Counterweight)?"
                if truth
                else "Does the immediate producer guarantee this field, or distinguish "
                "missing/None from zero/empty state, making this fallback lossy; or is "
                "that equivalence part of the boundary contract (Counterweight)?"
            )
            self.add(
                "PY04_lossy_falsy_coalesce",
                node,
                f"Lookup coalesces to {ast.unparse(fallback)} at line {node.lineno}",
                verify,
            )
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self.symbols.append(node.name)
        self.generic_visit(node)
        self.symbols.pop()

    def visit_FunctionDef(self, node):
        self.symbols.append(node.name)
        self.dead_parameters(node)
        self.typed_parameters(node)
        self.generic_visit(node)
        self.symbols.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def typed_parameters(self, node):
        defaults = parameter_defaults(node)
        concrete = {
            param.arg: (param, concrete_annotation(param.annotation))
            for param in parameters(node)
            if not is_none(defaults[param.arg])
        }
        concrete = {
            name: value for name, value in concrete.items() if value[1] is not None
        }
        if not concrete:
            return
        nodes = [child for statement in node.body for child in local_walk(statement)]
        unbound_annotations = {
            child.target
            for child in nodes
            if isinstance(child, ast.AnnAssign) and child.value is None
        }
        deferred_bindings = {}
        for child in nodes:
            if (
                isinstance(
                    child, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)
                )
                and child.value is not None
            ):
                targets = (
                    child.targets if isinstance(child, ast.Assign) else [child.target]
                )
                for target in targets:
                    for bound in bound_targets(target):
                        # RHS checks still observe the parameter before the store.
                        deferred_bindings[bound] = (
                            child.end_lineno,
                            child.end_col_offset,
                        )
        first_bind = {}
        for child in nodes:
            if child in unbound_annotations:
                continue
            for name in binding_names(child) & concrete.keys():
                position = deferred_bindings.get(
                    child, (child.lineno, child.col_offset)
                )
                first_bind[name] = min(first_bind.get(name, position), position)
        for child in nodes:
            name = none_check(child)
            checked_types = None
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "isinstance"
                and len(child.args) == 2
                and not child.keywords
                and isinstance(child.args[0], ast.Name)
            ):
                name = child.args[0].id
                checked_types = terminal_names(child.args[1])
            if name not in concrete:
                continue
            param, annotation_type = concrete[name]
            if checked_types is not None and annotation_type not in checked_types:
                continue
            if (
                name in first_bind
                and (child.lineno, child.col_offset) >= first_bind[name]
            ):
                continue
            self.add(
                "PY06_revalidated_typed_param",
                child,
                f"Parameter '{name}' annotated {ast.unparse(param.annotation)} at line "
                f"{param.lineno} is checked at line {child.lineno} before rebinding",
                f"Does the immediate caller or type definition establish '{name}' as "
                f"{annotation_type} already, or is this an untrusted/public entry point "
                "(Counterweight)?",
            )

    def dead_parameters(self, node):
        params = {
            param.arg
            for param in [*parameters(node), node.args.vararg, node.args.kwarg]
            if param is not None and param.arg not in {"self", "cls"}
        }
        reads = {name: [] for name in params}
        for statement in node.body:
            for child in load_names(statement):
                if child.id in reads:
                    reads[child.id].append(child)
        for statement in node.body:
            for child in local_walk(statement):
                assignment = simple_assignment(child)
                if assignment is None:
                    continue
                name, value = assignment
                if (
                    name == "_"
                    and isinstance(value, ast.Name)
                    and value.id in params
                    and reads[value.id] == [value]
                ):
                    self.dead_param_candidate(
                        value.id,
                        child,
                        "explicit discard with no other Load references",
                    )
        unseen = set(params)
        defaults = parameter_defaults(node)
        for statement in node.body:
            # The first control-flow statement may itself be the fallback guard.
            if isinstance(statement, ast.If):
                new = none_check(statement.test)
                if (
                    new in params
                    and new in defaults
                    and isinstance(statement.test.ops[0], ast.Is)
                    and is_none(defaults[new])
                ):
                    for child in statement.body:
                        assignment = simple_assignment(child)
                        if assignment is None or assignment[0] != new:
                            continue
                        rhs_reads = load_names(assignment[1])
                        for old in sorted(params - {new}):
                            if reads[old] and all(
                                read in rhs_reads for read in reads[old]
                            ):
                                self.dead_param_candidate(
                                    old,
                                    statement.test,
                                    f"dual-parameter fallback to '{new}' (default None at line "
                                    f"{defaults[new].lineno}); sole reads in overwrite at line {child.lineno}",
                                    related=(child,),
                                )
            if prefix_boundary(statement):
                break
            mentioned = {
                child.id for child in ast.walk(statement) if isinstance(child, ast.Name)
            }
            for child in local_walk(statement):
                mentioned.update(binding_names(child))
            if (
                isinstance(statement, (ast.Assign, ast.AnnAssign))
                and statement.value is not None
            ):
                bound = {
                    target.id
                    for assignment in assignments(statement)
                    for target in bound_targets(assignment)
                    if isinstance(target, ast.Name)
                }
                rhs = {
                    child.id
                    for child in ast.walk(statement.value)
                    if isinstance(child, ast.Name)
                }
                for name in sorted((unseen & bound) - rhs):
                    self.dead_param_candidate(
                        name, statement, "entry overwrite before any RHS read"
                    )
            unseen -= mentioned

    def dead_param_candidate(self, name, node, fact, related=()):
        self.add(
            "PY05_dead_param_evasion",
            node,
            f"Parameter '{name}': {fact} at line {node.lineno}",
            f"Does an immediate caller still need parameter '{name}', or can its signature "
            "plumbing be removed; is a live public/framework protocol a Counterweight?",
            related=related,
        )

    def visit_Call(self, node):
        if (
            isinstance(node.func, ast.Name)
            and (
                (node.func.id == "getattr" and len(node.args) >= 3)
                or (node.func.id == "hasattr" and len(node.args) == 2)
            )
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and not (
                isinstance(node.args[0], ast.Name) and node.args[0].id in self.imported
            )
        ):
            attr = node.args[1].value
            evidence = (
                "; ".join(
                    f"Same-file class {cls} declares attribute '{attr}' at line {line}"
                    for cls, line in self.attributes.get(attr, [])
                )
                or f"No same-file class declaration found for attribute '{attr}'"
            )
            self.add(
                "PY01_reflect_default",
                node,
                evidence,
                f"Does the immediate producer or type definition prove the receiver "
                f"is owned here and guaranteed to define '{attr}', or is it a "
                "dynamic proxy/plugin/external object (Counterweight)?",
            )
        self.generic_visit(node)

    def visit_Try(self, node):
        internal = {
            "KeyError",
            "TypeError",
            "AttributeError",
            "IndexError",
            "ValueError",
            "LookupError",
        }
        hooks = [
            child
            for statement in node.body
            for child in local_walk(statement)
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "loads"
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id == "json"
            and any(keyword.arg == "object_pairs_hook" for keyword in child.keywords)
        ]
        for handler in node.handlers:
            caught = handler.type
            names = terminal_names(caught)
            broad = caught is None or bool(names & {"Exception", "BaseException"})
            outcomes = exits(handler.body)
            facts = []
            if (broad or len(names & internal) >= 2) and not outcomes <= {
                "bare",
                "raise",
            }:
                facts.append("broad/multi-exception handler has a non-raising exit")
            washes_hook = (
                hooks and (broad or "ValueError" in names) and outcomes != {"bare"}
            )
            if washes_hook:
                facts.append(
                    "json.loads(object_pairs_hook=...) at "
                    + ", ".join(f"line {hook.lineno}" for hook in hooks)
                    + " is not bare re-raised on every path"
                )
            if facts:
                label = ast.unparse(caught) if caught is not None else "bare except"
                verify = (
                    "Does the immediate hook's rejection contract require preserving its "
                    "diagnostic, or is replacement documented boundary translation (Counterweight)?"
                    if washes_hook
                    else "Does the immediate producer's error contract require these failures "
                    "to propagate, or is this documented boundary recovery (Counterweight)?"
                )
                self.add(
                    "PY02_error_washing",
                    handler,
                    f"Handler catches {label} at line {handler.lineno}; "
                    + "; ".join(facts),
                    verify,
                )
        self.generic_visit(node)

    visit_TryStar = visit_Try


def has_conflict(source):
    state = 0
    for line in source.splitlines():
        if state == 0 and line.startswith("<<<<<<< "):
            state = 1
        elif state == 1 and line == "=======":
            state = 2
        elif state == 2 and line.startswith(">>>>>>> "):
            return True
    return False


def render(result, output_format):
    if output_format == "json":
        print(json.dumps(result))
        return
    summary = result["summary"]
    print(
        f"Scanned {summary['files_scanned']} files "
        f"({summary['cross_checked_files']} cross-checked); "
        f"{summary['candidate_count']} candidates, "
        f"{summary['parse_error_count']} parse errors"
    )
    for error in result["parse_errors"]:
        print(f"[PARSE_ERROR] {error['path']}: {error['error']}")
    for candidate in result["candidates"]:
        start, end = candidate["span"]
        print(
            f"[{candidate['id']}] {candidate['path']}:{start}-{end} "
            f"({candidate['symbol']}) — {candidate['rule']} [{candidate['change_tag']}]"
        )
        print(f"  Code: {candidate['code']}")
        print(f"  Evidence: {candidate['evidence']}")
        print(f"  Verify: {candidate['verify']}")


def scan(root, files, tip, args):
    candidates = []
    parse_errors = []
    files_scanned = 0
    for change in sorted(files, key=lambda item: item.path):
        path = root / change.path
        if tip is None and path.is_symlink():
            continue
        raw = git(root, "show", f"{tip}:{change.path}") if tip else path.read_bytes()
        if b"\x00" in raw:
            continue
        files_scanned += 1
        relative = change.path
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
            source = raw.decode(encoding)
            if has_conflict(source):
                raise SyntaxError("Unresolved merge conflict markers")
            tree = ast.parse(source, filename=relative)
        except (SyntaxError, UnicodeError, LookupError) as error:
            parse_errors.append(
                {"path": relative, "error": " ".join(str(error).split())}
            )
            continue
        if role(Path(relative), args.test_glob) == "test":
            continue
        probes = CandidateProbes(source, tree)
        probes.visit(tree)
        for candidate in probes.candidates:
            spans = candidate.pop("_spans")
            if change.added_lines is not None and not any(
                line in change.added_lines
                for start, end in spans
                for line in range(start, end + 1)
            ):
                continue
            candidate.update(
                path=relative, change_tag="changed" if args.base else "path-audit"
            )
            candidates.append(candidate)
    candidates.sort(key=lambda item: (item["path"], item["span"][0], item["rule"]))
    for index, candidate in enumerate(candidates, 1):
        candidate["id"] = f"C{index:02d}"
    return {
        "summary": {
            "mode": "diff" if args.base else "paths",
            "files_scanned": files_scanned,
            "cross_checked_files": 0,
            "candidate_count": len(candidates),
            "parse_error_count": len(parse_errors),
        },
        "parse_errors": sorted(
            parse_errors, key=lambda item: (item["path"], item["error"])
        ),
        "candidates": candidates,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--paths", nargs="+", help="Audit files or directories on disk")
    scope.add_argument("--base", help="Base commit for a diff")
    parser.add_argument("--tip", help="Tip commit; omit for working tree")
    parser.add_argument(
        "--merge-base",
        action="store_true",
        help="Compare from divergence; tip defaults to HEAD",
    )
    parser.add_argument("--repo", help="Repository or explicit paths scan root")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="Add an excluded path/basename glob",
    )
    parser.add_argument(
        "--test-glob",
        action="append",
        default=[],
        metavar="GLOB",
        help="Add a test-role path/basename glob",
    )
    parser.add_argument("--format", choices=("compact", "json"), default="compact")
    args = parser.parse_args()
    if args.base == "" or args.tip == "":
        parser.error("Git references must not be empty")
    if not args.base and (args.tip or args.merge_base):
        parser.error("--tip and --merge-base require --base")
    try:
        if args.base:
            root, files, tip = diff_scope(args)
        else:
            root, files = paths_scope(args)
            tip = None
        render(scan(root, files, tip, args), args.format)
    except (ScanError, OSError) as error:
        print(f"scan_python.py: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
