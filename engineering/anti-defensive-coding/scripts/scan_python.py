#!/usr/bin/env python3
"""Read-only Python AST Candidate Extractor (Python 3.11+)."""

import argparse
import ast
import builtins
from dataclasses import dataclass, field
import fnmatch
import io
from itertools import pairwise
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tokenize


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
    old_path: str | None = None
    deleted_lines: set[int] | None = None
    deleted: bool = False


def git(root, *args, allow_non_repo=False, input=None):
    result = subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(root), *args],
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"},
        capture_output=True,
        input=input,
        check=False,
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
        pre_python = (
            status in {"M", "R", "D"}
            and Path(old_path).suffix == ".py"
            and not excluded(Path(old_path), args.exclude)
        )
        post_python = (
            status != "D"
            and Path(path).suffix == ".py"
            and not excluded(Path(path), args.exclude)
        )
        if not pre_python and not post_python:
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
        deleted = set()
        for match in re.finditer(
            rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", patch, re.MULTILINE
        ):
            for lines, raw_start, raw_count in (
                (deleted, match[1], match[2]),
                (added, match[3], match[4]),
            ):
                start = int(raw_start)
                count = int(raw_count) if raw_count is not None else 1
                lines.update(range(start, start + count))
        changes.append(
            FileChange(
                path, added, old_path if pre_python else None, deleted, not post_python
            )
        )
    if tip is None:
        for name in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(
            b"\0"
        )[:-1]:
            path = os.fsdecode(name)
            if Path(path).suffix == ".py" and not excluded(Path(path), args.exclude):
                changes.append(FileChange(path))
    return root, changes, base, tip


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


def absent_self_attribute(test):
    if not isinstance(test, ast.UnaryOp) or not isinstance(test.op, ast.Not):
        return None
    call = test.operand
    if (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "hasattr"
        and len(call.args) == 2
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "self"
        and isinstance(call.args[1], ast.Constant)
        and isinstance(call.args[1].value, str)
    ):
        return call.args[1].value
    return None


def guarded_self_writes(node):
    if not isinstance(node, ast.If):
        return
    attr = absent_self_attribute(node.test)
    if attr is None:
        return
    for statement in node.body:
        for write in local_walk(statement):
            if (
                not isinstance(write, (ast.Assign, ast.AnnAssign))
                or write.value is None
            ):
                continue
            for target in write_targets(write):
                for attribute in bound_targets(target):
                    if self_attribute(attribute) and attribute.attr == attr:
                        yield attribute, write.value


def class_declarations(classes):
    attributes = {}
    for cls in classes:
        for statement in lexical_nodes(cls.body):
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                targets = (
                    [statement.target]
                    if isinstance(statement, ast.AnnAssign)
                    else statement.targets
                )
                for target in targets:
                    for name in bound_targets(target):
                        if isinstance(name, ast.Name):
                            attributes.setdefault((cls, name.id), set()).add(
                                statement.lineno
                            )
        methods = [
            method
            for method in lexical_nodes(cls.body)
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        helpers = {
            node.func.attr
            for initializer in methods
            if initializer.name == "__init__"
            for node in lexical_nodes(initializer.body)
            if isinstance(node, ast.Call) and self_attribute(node.func)
        }
        for method in methods:
            if method.name not in {"__init__", *helpers}:
                continue
            # Lazy presence-guarded writes do not establish a construction invariant.
            lazy_targets = {
                attribute
                for node in lexical_nodes(method.body)
                for attribute, _ in guarded_self_writes(node)
            }
            for statement in method.body:
                for target in assignments(statement):
                    for attribute in bound_targets(target):
                        if self_attribute(attribute) and attribute not in lazy_targets:
                            attributes.setdefault((cls, attribute.attr), set()).add(
                                attribute.lineno
                            )
    return attributes


def named_class(node, classes):
    name = node.id if isinstance(node, ast.Name) else None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        name = node.value
    matches = classes.get(name, [])
    return matches[0] if len(matches) == 1 else None


def undeclared_attribute(cls, attr, classes, attributes):
    pending = [cls]
    seen = set()
    while pending:
        owner = pending.pop()
        if owner in seen:
            continue
        seen.add(owner)
        if (owner, attr) in attributes:
            return False
        for base in owner.bases:
            if isinstance(base, ast.Name) and base.id == "object":
                continue
            parent = named_class(base, classes)
            if parent is None:
                return False
            pending.append(parent)
    return True


def zero_arg_constructor(node, classes):
    if not isinstance(node, ast.Call) or node.args or node.keywords:
        return False
    name = terminal_name(node.func)
    return name is not None and (
        name[:1].isupper()
        or (
            isinstance(node.func, ast.Name)
            and isinstance(vars(builtins).get(name), type)
        )
        or name in {"cls", "__new__", "__class__"}
        or named_class(node.func, classes) is not None
    )


def local_walk(node):
    yield node
    if not isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    ):
        for child in ast.iter_child_nodes(node):
            yield from local_walk(child)


def lexical_nodes(body):
    """Located statement and expression nodes under body, in source order."""
    return sorted(
        (
            child
            for statement in body
            for child in local_walk(statement)
            if isinstance(child, (ast.stmt, ast.expr))
        ),
        key=lambda child: (child.lineno, child.col_offset),
    )


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
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and not any(
        isinstance(item, ast.Starred) for item in node.elts
    ):
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


def statement_neighbors(node, parent):
    body = next(
        value
        for _, value in ast.iter_fields(parent)
        if isinstance(value, list) and node in value
    )
    index = body.index(node)
    return body[:index], body[index + 1 :]


def saved_exception(handler):
    assignment = simple_assignment(handler.body[-1])
    if (
        assignment is not None
        and isinstance(assignment[1], ast.Name)
        and assignment[1].id == handler.name
    ):
        return assignment[0]
    return None


def exception_save_targets(handlers, slot):
    return {
        target
        for handler in handlers
        if saved_exception(handler) == slot
        for target in write_targets(handler.body[-1])
    }


def raises_name(node, name):
    return (
        isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Name)
        and node.exc.id == name
    )


def saved_retry(node, handler, parents):
    slot = saved_exception(handler)
    if slot is None:
        return False
    loop = parents[-1]
    if (
        not isinstance(loop, (ast.For, ast.While))
        or loop.orelse
        or (isinstance(loop, ast.While) and literal_truth(loop.test))
        or exits(handler.body) & {"return", "break", "continue"}
        or any(isinstance(child, ast.Break) for child in local_walk(loop))
    ):
        return False
    before, after = statement_neighbors(node, loop)
    if any("return" in statement_exits(statement) for statement in before + after):
        return False
    saves = exception_save_targets(node.handlers, slot)
    if any(
        slot in binding_names(child) and child not in saves
        for child in local_walk(loop)
    ):
        return False
    _, following = statement_neighbors(loop, parents[-2])
    if following and isinstance(following[0], ast.Assert):
        if any(slot in binding_names(child) for child in local_walk(following[0])):
            return False
        following = following[1:]
    return bool(following) and raises_name(following[0], slot)


def final_attempt(test, name, count):
    if not (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == name
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
    ):
        return False
    value = test.comparators[0]
    return (isinstance(value, ast.Constant) and value.value == count - 1) or (
        isinstance(value, ast.BinOp)
        and isinstance(value.op, ast.Sub)
        and isinstance(value.left, ast.Constant)
        and value.left.value == count
        and isinstance(value.right, ast.Constant)
        and value.right.value == 1
    )


def bounded_retry(node, handler, parent):
    if not (
        isinstance(parent, ast.For)
        and parent.body == [node]
        and isinstance(parent.target, ast.Name)
        and isinstance(parent.iter, ast.Call)
        and isinstance(parent.iter.func, ast.Name)
        and parent.iter.func.id == "range"
        and len(parent.iter.args) == 1
        and not parent.iter.keywords
    ):
        return False
    count = parent.iter.args[0]
    if not (
        isinstance(count, ast.Constant) and type(count.value) is int and count.value > 0
    ):
        return False
    success = exits(node.body)
    if "return" not in success or not success <= {"return", "bare", "raise"}:
        return False
    if any(parent.target.id in binding_names(child) for child in local_walk(node)):
        return False
    guard = handler.body[-1]
    return (
        isinstance(guard, ast.If)
        and final_attempt(guard.test, parent.target.id, count.value)
        and exits(guard.body) == {"bare"}
        and not guard.orelse
        and not exits(handler.body) & {"return", "break", "continue"}
    )


def stored_primary_cleanup(node, handler, parents):
    guard = handler.body[-1]
    if not (
        isinstance(guard, ast.If)
        and none_check(guard.test) is not None
        and isinstance(guard.test.ops[0], ast.Is)
        and exits(guard.body) == {"bare"}
        and not guard.orelse
        and not exits(handler.body) & {"return", "break", "continue"}
    ):
        return False
    slot = none_check(guard.test)
    primary = parents[-1]
    if not (
        isinstance(primary, ast.Try)
        and node in primary.finalbody
        and not statement_exits(primary) & {"return", "break", "continue"}
    ):
        return False
    before, after = statement_neighbors(primary, parents[-2])
    if not before or not after:
        return False
    initial = simple_assignment(before[-1])
    propagate = after[0]
    if not (
        initial is not None
        and initial[0] == slot
        and is_none(initial[1])
        and isinstance(propagate, ast.If)
        and none_check(propagate.test) == slot
        and isinstance(propagate.test.ops[0], ast.IsNot)
        and len(propagate.body) == 1
        and raises_name(propagate.body[0], slot)
        and not propagate.orelse
    ):
        return False
    saves = exception_save_targets(primary.handlers, slot)
    return bool(saves) and all(
        child in saves for child in local_walk(primary) if slot in binding_names(child)
    )


def last_noreturn_call(body, helpers):
    last = body[-1]
    if not isinstance(last, ast.Expr) or "next" not in exits(body[:-1]):
        return None
    call = last.value.value if isinstance(last.value, ast.Await) else last.value
    if not isinstance(call, ast.Call) or terminal_name(call.func) not in helpers:
        return None
    if isinstance(call.func, ast.Name) or (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id in {"self", "cls"}
    ):
        return ast.unparse(call.func)
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
        for param, value in zip(
            parameters(node), values + node.args.kw_defaults, strict=True
        )
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


def import_bindings(nodes):
    return {
        name
        for node in nodes
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        for name in binding_names(alias)
    }


def identifier_tokens(text):
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", text)
    return [token.lower() for token in re.split(r"[\W_]+", separated) if token]


def name_path(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{name_path(node.value)}.{node.attr}"
    return ""


def write_targets(node):
    if isinstance(node, ast.Assign):
        return node.targets
    if (
        isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr))
        and node.value is not None
    ):
        return [node.target]
    return []


def self_state_writes(body, *, reset):
    writes = {}
    for statement in body:
        for child in local_walk(statement):
            if not isinstance(child, (ast.Assign, ast.AnnAssign)):
                continue
            if child.value is None or is_none(child.value) != reset:
                continue
            for target in write_targets(child):
                if self_attribute(target):
                    writes.setdefault(target.attr, []).append(child)
    return writes


def self_attribute(node):
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def adjacent_statements(nodes):
    """Adjacent statement pairs of every statement list owned by same-scope nodes."""
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for _, body in ast.iter_fields(node):
            if (
                isinstance(body, list)
                and body
                and all(isinstance(item, ast.stmt) for item in body)
            ):
                yield from pairwise(body)


def scope_bindings(function, nodes):
    """Names local to function: parameters and stores among its same-scope nodes."""
    # Comprehension targets bind in the comprehension's own scope.
    comprehension_names = {
        name
        for node in nodes
        if isinstance(node, ast.comprehension)
        for name in ast.walk(node.target)
    }
    external = {
        name
        for node in nodes
        if isinstance(node, (ast.Global, ast.Nonlocal))
        for name in node.names
    }
    return {
        param.arg
        for param in [*parameters(function), function.args.vararg, function.args.kwarg]
        if param is not None
    }.union(
        *(binding_names(node) for node in nodes if node not in comprehension_names)
    ) - external


def same_class_stashes(tree):
    """Map `self._a = n` directly followed by `return n` to sibling-method readers."""
    stashes = {}
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        methods = [
            node
            for node in cls.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        scopes = {
            method: [
                child for statement in method.body for child in local_walk(statement)
            ]
            for method in methods
        }
        loads = {}
        for method, nodes in scopes.items():
            for node in nodes:
                if self_attribute(node) and isinstance(node.ctx, ast.Load):
                    loads.setdefault(node.attr, []).append((method, node))
        for method, nodes in scopes.items():
            local = scope_bindings(method, nodes)
            for statement, following in [
                *pairwise(method.body),
                *adjacent_statements(nodes),
            ]:
                if not (
                    isinstance(statement, (ast.Assign, ast.AnnAssign))
                    and isinstance(statement.value, ast.Name)
                    and statement.value.id in local
                    and isinstance(following, ast.Return)
                    and isinstance(following.value, ast.Name)
                    and following.value.id == statement.value.id
                ):
                    continue
                for target in write_targets(statement):
                    if not (self_attribute(target) and target.attr.startswith("_")):
                        continue
                    readers = sorted(
                        (
                            (other.name, load)
                            for other, load in loads.get(target.attr, [])
                            if other is not method
                        ),
                        key=lambda reader: (reader[1].lineno, reader[1].col_offset),
                    )
                    if readers:
                        stashes[statement] = (target.attr, following, readers)
    return stashes


def context_tokens(node):
    while isinstance(node, ast.Subscript):
        node = node.value
    return set(
        identifier_tokens(name_path(node.func if isinstance(node, ast.Call) else node))
    )


def lock_context_parts(node):
    if not isinstance(node, (ast.With, ast.AsyncWith)):
        return
    for index, item in enumerate(node.items):
        if context_tokens(item.context_expr) & {
            "transaction",
            "immediate",
            "savepoint",
            "lock",
            "locks",
        }:
            yield (
                item.context_expr,
                [
                    *node.body,
                    *(later.context_expr for later in node.items[index + 1 :]),
                ],
            )


def io_or_hash_call(node):
    path = name_path(node.func)
    return (
        terminal_name(node.func)
        in {"open", "read_text", "write_text", "read_bytes", "write_bytes"}
        or path.startswith(("shutil.", "hashlib."))
        or path.endswith(".followup.send")
        or path in {"os.remove", "os.unlink", "os.replace", "sha256"}
    )


def iteration_parts(node):
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return [node.target, *node.body], [node.target, node.iter]
    if isinstance(node, ast.While):
        return [node.test, *node.body], [node.test]
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        repeated = (
            [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        )
        headers = []
        for index, generator in enumerate(node.generators):
            repeated.extend([generator.target, *generator.ifs])
            if index:
                repeated.append(generator.iter)
            headers.extend([generator.target, generator.iter])
        return repeated, headers
    return [], []


def predicate_tests(node):
    if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
        return [node.test]
    if isinstance(node, ast.comprehension):
        return node.ifs
    if isinstance(node, ast.match_case):
        return [node.guard] if node.guard is not None else []
    if (
        isinstance(node, (ast.Compare, ast.BoolOp))
        or (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not))
        or (isinstance(node, ast.Call) and name_path(node.func) == "bool")
    ):
        return [node]
    return []


def lookup_signature(values):
    keys = []
    for value in values:
        if isinstance(value, ast.Attribute):
            keys.append(repr(value.attr))
        elif isinstance(value, ast.Subscript) and isinstance(value.slice, ast.Constant):
            keys.append(repr(value.slice.value))
        elif get_call(value) and value.args and isinstance(value.args[0], ast.Constant):
            keys.append(repr(value.args[0].value))
    return tuple(keys) or None


class CandidateRecords(ast.NodeVisitor):
    def __init__(self, source):
        self.source = source
        self.symbols = []
        self.candidates = []
        self.function_depth = 0
        self.defenses = []

    def add(self, rule, node, evidence, verify, related=(), signature=None):
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
        if signature is not None and self.function_depth:
            self.defenses.append(((rule, signature), self.candidates[-1]))


class CandidateProbes(CandidateRecords):
    def __init__(self, source, tree):
        super().__init__(source)
        classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        self.classes = {}
        for cls in local_walk(tree):
            if isinstance(cls, ast.ClassDef):
                self.classes.setdefault(cls.name, []).append(cls)
        self.attributes = class_declarations(classes)
        self.parents = []
        self.imported = import_bindings(ast.walk(tree))
        self.stashes = same_class_stashes(tree)
        self.iterations = {}
        self.lock_contexts = {}
        self.transaction_contexts = set()
        returns = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                returns.setdefault(node.name, []).append(terminal_name(node.returns))
            if isinstance(node, (ast.With, ast.AsyncWith)):
                self.transaction_contexts.update(
                    item.context_expr
                    for item in node.items
                    if context_tokens(item.context_expr) & {"transaction", "savepoint"}
                )
            repeated, headers = iteration_parts(node)
            for part in repeated:
                for child in local_walk(part):
                    if isinstance(child, ast.Call):
                        self.iterations.setdefault(child, []).append((node, headers))
            for context, parts in lock_context_parts(node):
                for part in parts:
                    for child in local_walk(part):
                        if isinstance(child, ast.expr):
                            self.lock_contexts.setdefault(child, []).append(context)
        self.noreturn_helpers = {
            name
            for name, annotations in returns.items()
            if all(annotation in {"NoReturn", "Never"} for annotation in annotations)
        }
        self.functions = set()

    def generic_visit(self, node):
        self.parents.append(node)
        for _, value in ast.iter_fields(node):
            if (
                isinstance(value, list)
                and value
                and all(isinstance(item, ast.stmt) for item in value)
            ):
                self.default_shadows(value)
        super().generic_visit(node)
        self.parents.pop()

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

    def visit_Compare(self, node):
        if (
            len(node.ops) == 1
            and isinstance(node.ops[0], (ast.IsNot, ast.NotEq))
            and any(
                isinstance(operand, (ast.Name, ast.Attribute))
                and terminal_name(operand).startswith("_DEFAULT_")
                for operand in (node.left, node.comparators[0])
            )
        ):
            self.add(
                "PY10b_test_smell",
                node,
                "Production callable identity is compared with a _DEFAULT_* hook",
                "Does the immediate caller require a public replacement hook, or "
                "does this only accommodate test patching; is a live extension "
                "contract a Counterweight?",
            )
        self.generic_visit(node)

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
            lossy = (
                'a legitimate 0/""/False, the fallback overwrites it'
                if truth
                else 'a distinct legitimate one (None vs 0, 0 vs ""), the fallback rewrites it'
            )
            verify = (
                "Can the immediate producer or governing type deliver a falsy value "
                f"here? If not, the fallback is dead; if it delivers {lossy}. "
                "Counterweight only if the boundary contract equates missing with "
                "empty or the producer already rejects empty. See python.md §6."
            )
            self.add(
                "PY04_lossy_falsy_coalesce",
                node,
                f"Lookup coalesces to {ast.unparse(fallback)} at line {node.lineno}",
                verify,
                signature=lookup_signature(node.values[:-1]),
            )
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self.symbols.append(node.name)
        self.generic_visit(node)
        self.symbols.pop()

    def visit_FunctionDef(self, node):
        self.symbols.append(node.name)
        self.functions.add(".".join(self.symbols))
        self.function_depth += 1
        self.dead_parameters(node)
        self.state_smuggling(node)
        self.transaction_checks(node)
        self.generic_visit(node)
        self.function_depth -= 1
        self.symbols.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def transaction_checks(self, node):
        nodes = [child for statement in node.body for child in local_walk(statement)]
        contexts = [child for child in nodes if child in self.transaction_contexts]
        if not contexts:
            return
        checks = []
        covered = set()
        for child in nodes:
            for test in predicate_tests(child):
                if test in covered:
                    continue
                parts = list(local_walk(test))
                covered.update(parts)
                if any(
                    isinstance(part, ast.Attribute)
                    and part.attr == "in_transaction"
                    and isinstance(part.ctx, ast.Load)
                    for part in parts
                ):
                    checks.append(test)
        if checks:
            self.add(
                "PY09_io_or_tx_in_lock",
                checks[0],
                "in_transaction checked at "
                + ", ".join(f"line {check.lineno}" for check in checks)
                + "; same function enters "
                + ", ".join(
                    f"with {ast.unparse(context)} at line {context.lineno}"
                    for context in contexts
                ),
                "Does the immediate caller already own the transaction, making another "
                "savepoint redundant; is independent rollback or required isolation "
                "a Counterweight?",
                related=(*checks[1:], *contexts),
            )

    def state_smuggling(self, node):
        nodes = [child for statement in node.body for child in local_walk(statement)]
        for child in nodes:
            if child in self.stashes:
                attr, returned, readers = self.stashes[child]
                name = returned.value.id
                self.add(
                    "PY07_state_smuggling",
                    child,
                    f"self.{attr} = {name} at line {child.lineno} precedes return "
                    f"{name}; read in "
                    + ", ".join(
                        f"{method} at line {load.lineno}" for method, load in readers
                    ),
                    "Could each reader receive this value as an explicit argument from "
                    f"a caller that already holds the return, or does self.{attr} "
                    "memoize it across calls (Counterweight)? See python.md §8.",
                    related=(returned, *(load for _, load in readers)),
                )
            if isinstance(child, ast.Dict):
                for key, value in zip(child.keys, child.values, strict=True):
                    self.dict_dependency(key, value)
            if isinstance(child, (ast.Try, ast.TryStar)) and child.finalbody:
                active = self_state_writes(child.body, reset=False)
                resets = self_state_writes(child.finalbody, reset=True)
                for attr in sorted(active.keys() & resets.keys()):
                    first, *related = [*active[attr], *resets[attr]]
                    self.add(
                        "PY07_state_smuggling",
                        first,
                        f"self.{attr} set at line {first.lineno} in try at line "
                        f"{child.lineno}, reset to None in finally at line "
                        f"{resets[attr][0].lineno}",
                        f"Does the immediate consumer require transient 'self.{attr}', "
                        "or can it receive an explicit argument; is a documented "
                        "lifecycle/reentrancy guarantee a Counterweight?",
                        related=related,
                    )
            for assignment in write_targets(child):
                for target in bound_targets(assignment):
                    if isinstance(child, (ast.Assign, ast.AnnAssign)) and isinstance(
                        target, ast.Subscript
                    ):
                        self.dict_dependency(target.slice, child.value, child)

    def dict_dependency(self, key, value, assignment=None):
        if not (
            isinstance(key, ast.Constant)
            and isinstance(key.value, str)
            and isinstance(value, (ast.Name, ast.Attribute))
        ):
            return
        tokens = identifier_tokens(key.value)
        if not tokens or tokens[-1] not in {
            "service",
            "repo",
            "repository",
            "reader",
            "writer",
            "conn",
            "connection",
            "session",
        }:
            return
        self.add(
            "PY07_state_smuggling",
            assignment if assignment is not None else key,
            f"Dependency-shaped key {key.value!r} stores {ast.unparse(value)} "
            f"at line {key.lineno}",
            "Does the immediate dictionary consumer expect data only, so this dependency "
            "should be explicit; is a documented dependency registry or boundary adapter "
            "a Counterweight?",
            related=() if assignment is not None else (value,),
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

    def visit_ListComp(self, node):
        element = node.elt
        if (
            isinstance(element, ast.Call)
            and name_path(element.func) == "dict"
            and len(element.args) == 1
            and not element.keywords
            and isinstance(element.args[0], ast.Name)
        ):
            for generator in node.generators:
                if (
                    isinstance(generator.target, ast.Name)
                    and generator.target.id == element.args[0].id
                ):
                    self.add(
                        "PY08_roundtrip_and_copy",
                        element,
                        f"Comprehension at line {node.lineno} rewraps its target "
                        f"'{generator.target.id}' in dict at line {element.lineno}",
                        "Does the immediate row producer already return suitable mappings, "
                        "or is independent mutation/ownership of each copy a Counterweight?",
                        related=(generator.target,),
                    )
                    break
        self.generic_visit(node)

    def roundtrip(self, node):
        if len(node.args) != 1 or not isinstance(node.args[0], ast.Call):
            return
        inner = node.args[0]
        pair = name_path(node.func), name_path(inner.func)
        matches = pair in {("json.loads", "json.dumps"), ("int", "float")}
        matches |= (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "fromisoformat"
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "isoformat"
        )
        matches |= (
            pair == ("set", "list")
            and len(inner.args) == 1
            and isinstance(inner.args[0], ast.Call)
            and name_path(inner.args[0].func) == "set"
        )
        if matches:
            self.add(
                "PY08_roundtrip_and_copy",
                node,
                f"{ast.unparse(node.func)} wraps {ast.unparse(inner.func)} "
                f"at line {node.lineno}",
                "Does the immediate consumer need this representation round-trip, "
                "or can the original value preserve Semantic Fidelity; is documented "
                "wire normalization or isolation a Counterweight?",
            )

    def lock_candidate(self, node, fact):
        contexts = self.lock_contexts[node]
        catalogue = (
            "sql.md §7"
            if any(
                context_tokens(context) & {"transaction", "immediate", "savepoint"}
                for context in contexts
            )
            else "python.md §11"
        )
        self.add(
            "PY09_io_or_tx_in_lock",
            node,
            f"{fact} at line {node.lineno} inside "
            + ", ".join(
                f"with {ast.unparse(context)} at line {context.lineno}"
                for context in contexts
            ),
            "Does the immediate lock/transaction owner require this work in the protected "
            "region, or can it move outside; is atomicity, isolation, or data-loss "
            f"prevention a Counterweight? See {catalogue}.",
            related=tuple(contexts),
        )

    def visit_With(self, node):
        for item in node.items:
            context = item.context_expr
            if context in self.transaction_contexts and context in self.lock_contexts:
                self.lock_candidate(context, "Nested transaction/savepoint")
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    def receiver_binding(self, receiver):
        root = receiver
        while isinstance(root, (ast.Attribute, ast.Subscript)):
            root = root.value
        if not isinstance(root, ast.Name):
            return [], None
        for scope in reversed(self.parents):
            if isinstance(
                scope, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
            ) and any(
                isinstance(name, ast.Name) and name.id == root.id
                for generator in scope.generators
                for name in bound_targets(generator.target)
            ):
                break
            if not isinstance(
                scope, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            ):
                continue
            body = [] if isinstance(scope, ast.Lambda) else scope.body
            nodes = lexical_nodes(body)
            for statement in reversed(nodes):
                targets = (
                    [statement.target]
                    if isinstance(statement, ast.AnnAssign)
                    else write_targets(statement)
                )
                if (
                    targets
                    and (statement.end_lineno, statement.end_col_offset)
                    <= (receiver.lineno, receiver.col_offset)
                    and any(
                        isinstance(name, ast.Name) and name.id == root.id
                        for target in targets
                        for name in bound_targets(target)
                    )
                ):
                    annotation = (
                        statement.annotation
                        if isinstance(statement, ast.AnnAssign)
                        else None
                    )
                    return [
                        f"local binding {ast.unparse(statement)} at line {statement.lineno} "
                        "(control-flow unresolved)"
                    ], annotation if receiver is root else None
            if not isinstance(scope, ast.Module):
                for param in (*parameters(scope), scope.args.vararg, scope.args.kwarg):
                    if param is not None and param.arg == root.id:
                        annotation = (
                            ast.unparse(param.annotation)
                            if param.annotation is not None
                            else "<unannotated>"
                        )
                        return [
                            f"parameter {param.arg}: {annotation} at line {param.lineno}"
                        ], param.annotation if receiver is root else None
            if any(
                root.id in binding_names(child)
                for statement in body
                for child in local_walk(statement)
            ):
                break
        return [], None

    def reflection_evidence(self, receiver, cls, attr):
        facts = [f"Receiver {ast.unparse(receiver)}"]
        if isinstance(receiver, ast.Name) and receiver.id == "self" and cls is not None:
            facts.append(f"enclosing class {cls.name} at line {cls.lineno}")
        else:
            binding, annotation = self.receiver_binding(receiver)
            facts.extend(binding)
            cls = named_class(annotation, self.classes)
        if cls is None:
            facts.append("receiver class unknown")
        else:
            bases = (
                ", ".join(ast.unparse(base) for base in cls.bases)
                or "object (implicit)"
            )
            facts.append(f"receiver class {cls.name}; bases: {bases}")
            facts.extend(
                f"Same-file class {cls.name} declares attribute '{attr}' at line {line}"
                for line in sorted(self.attributes.get((cls, attr), []))
            )
        return "; ".join(facts)

    def memo_guard(self, cls, attr):
        if cls is None:
            return False
        test, guard = self.parents[-1], self.parents[-2]
        if not (
            isinstance(guard, ast.If)
            and absent_self_attribute(test) == attr
            and undeclared_attribute(cls, attr, self.classes, self.attributes)
        ):
            return False
        return any(
            not is_none(value)
            and literal_truth(value) is None
            and not zero_arg_constructor(value, self.classes)
            for _, value in guarded_self_writes(guard)
        )

    def visit_Call(self, node):
        self.roundtrip(node)
        if (
            node in self.lock_contexts
            and node not in self.transaction_contexts
            and io_or_hash_call(node)
        ):
            self.lock_candidate(node, f"I/O or hashing call {ast.unparse(node.func)}")
        contexts = self.iterations.get(node, [])
        if contexts and name_path(node.func) in {
            "deepcopy",
            "copy.deepcopy",
            "asdict",
            "dataclasses.asdict",
        }:
            self.add(
                "PY08_roundtrip_and_copy",
                node,
                f"{ast.unparse(node.func)} at line {node.lineno} runs per iteration of "
                + ", ".join(
                    f"{type(loop).__name__} at line {loop.lineno}"
                    for loop, _ in contexts
                ),
                "Does the immediate consumer mutate or retain an independent snapshot "
                "each iteration, or can copying be removed/hoisted; is isolation or "
                "required wire serialization a Counterweight?",
                related=tuple(header for _, headers in contexts for header in headers),
            )
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
            cls = next(
                (
                    parent
                    for parent in reversed(self.parents)
                    if isinstance(parent, ast.ClassDef)
                ),
                None,
            )
            if self.memo_guard(cls, attr):
                self.generic_visit(node)
                return
            self.add(
                "PY01_reflect_default",
                node,
                self.reflection_evidence(node.args[0], cls, attr),
                "Does the immediate producer or governing type (including third-party classes) "
                "guarantee the attribute, even if nullable, or is it an optional capability? "
                "For owned state, do construction/lifecycle writes or constructor-bypassing "
                "callers/tests explain absence? See python.md §1.",
                signature=attr,
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
            if (
                (broad or len(names & internal) >= 2)
                and not outcomes <= {"bare", "raise"}
                and not (
                    not exits(node.finalbody) & {"return", "break", "continue"}
                    and (
                        saved_retry(node, handler, self.parents)
                        or bounded_retry(node, handler, self.parents[-1])
                        or stored_primary_cleanup(node, handler, self.parents)
                    )
                )
            ):
                helper = last_noreturn_call(handler.body, self.noreturn_helpers)
                effective = (outcomes - {"next"}) | {"raise"} if helper else outcomes
                if not effective <= {"bare", "raise"}:
                    facts.append("broad/multi-exception handler has a non-raising exit")
                if helper:
                    facts.append(f"re-raises via `{helper}` (NoReturn)")
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
                    else "Which failure outcome reaches the immediate caller: false success, "
                    "omitted work, or fail-open, versus an established recovery contract "
                    "(Counterweight)? Is this handler Lint-Owned? See python.md §4."
                )
                self.add(
                    "PY02_error_washing",
                    handler,
                    f"Handler catches {label} at line {handler.lineno}; "
                    + "; ".join(facts),
                    verify,
                    signature=tuple(sorted(names - {None}))
                    if caught is not None
                    else ("bare",),
                )
        self.generic_visit(node)

    visit_TryStar = visit_Try


def raw_text_origin(node, bindings):
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return ("path", (node,)) if node.value.endswith((".py", ".md")) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return raw_text_origin(node.right, bindings) or raw_text_origin(
            node.left, bindings
        )
    if not isinstance(node, ast.Call):
        return None
    name = terminal_name(node.func)
    if name in {"Path", "open"}:
        for arg in node.args:
            origin = raw_text_origin(arg, bindings)
            if origin and origin[0] == "path":
                return "path", (*origin[1], node)
        if name == "Path":
            return "unknown-path", (node,)
    if isinstance(node.func, ast.Attribute):
        origin = raw_text_origin(node.func.value, bindings)
        if origin and origin[0] in {"path", "unknown-path"}:
            if name in {"read", "read_text"}:
                return ("text", (*origin[1], node)) if origin[0] == "path" else None
            for arg in node.args:
                suffix = raw_text_origin(arg, bindings)
                if suffix and suffix[0] == "path":
                    return "path", (*origin[1], *suffix[1], node)
            return origin
    return None


def patch_symbol(node):
    path = name_path(node.func)
    index = None
    if path in {"patch", "mock.patch", "unittest.mock.patch"}:
        index = 0
    elif path in {"patch.object", "mock.patch.object", "unittest.mock.patch.object"}:
        index = 1
    elif path == "monkeypatch.setattr":
        index = 0 if len(node.args) == 2 else 1
    if index is not None and len(node.args) > index:
        target = node.args[index]
        if isinstance(target, ast.Constant) and isinstance(target.value, str):
            return target.value.rsplit(".", 1)[-1]
    return None


def is_patch_constructor(node):
    return (
        isinstance(node, ast.Call)
        and bool(patch_symbol(node))
        and name_path(node.func) != "monkeypatch.setattr"
    )


@dataclass
class PatchWindow:
    patch: ast.Call
    start: tuple[int, int]
    end: tuple[int, int]
    activation: ast.Call | None = None


def patch_windows(node, nodes):
    """Lexical patch regions; resets affect only statement-installed patches."""
    end = (node.end_lineno, node.end_col_offset)
    windows = [
        PatchWindow(decorator, (node.lineno, node.col_offset), end)
        for decorator in node.decorator_list
        if is_patch_constructor(decorator)
    ]
    active = []
    patchers = {}
    for child in nodes:
        if isinstance(child, (ast.With, ast.AsyncWith)):
            for item in child.items:
                patch = item.context_expr
                if is_patch_constructor(patch):
                    windows.append(
                        PatchWindow(
                            patch,
                            (child.body[0].lineno, child.body[0].col_offset),
                            (child.end_lineno, child.end_col_offset),
                        )
                    )
        if isinstance(child, ast.Call):
            call = child
        elif isinstance(child, (ast.Expr, ast.Assign, ast.AnnAssign)):
            call = child.value
        else:
            call = None
        if isinstance(call, ast.Call):
            path = name_path(call.func)
            method = terminal_name(call.func)
            patcher = None
            if isinstance(call.func, ast.Attribute):
                receiver = call.func.value
                patcher = (
                    receiver
                    if isinstance(receiver, ast.Call)
                    else patchers.get(name_path(receiver))
                )
            stopall = path in {
                "patch.stopall",
                "mock.patch.stopall",
                "unittest.mock.patch.stopall",
            }
            for window in active[:]:
                monkeypatch = name_path(window.patch.func) == "monkeypatch.setattr"
                if (
                    (stopall and not monkeypatch)
                    or (path == "monkeypatch.undo" and monkeypatch)
                    or (method == "stop" and window.patch is patcher)
                ) and window.start <= (call.lineno, call.col_offset):
                    window.end = (call.lineno, call.col_offset)
                    active.remove(window)
            patch = (
                call
                if path == "monkeypatch.setattr"
                else (patcher if method == "start" else None)
            )
            if (
                not isinstance(child, ast.Call)
                and isinstance(patch, ast.Call)
                and patch_symbol(patch)
            ):
                window = PatchWindow(
                    patch, (call.end_lineno, call.end_col_offset), end, call
                )
                windows.append(window)
                active.append(window)
        for target in write_targets(child):
            for bound in bound_targets(target):
                if isinstance(bound, ast.Name):
                    patchers.pop(bound.id, None)
        assignment = simple_assignment(child)
        if assignment:
            name, value = assignment
            if is_patch_constructor(value):
                patchers[name] = value
    return windows


@dataclass
class GetterReturn:
    method: str
    binding: ast.stmt
    initial: ast.Call
    mutations: list[ast.AST] = field(default_factory=list)


class TestProbes(CandidateRecords):
    def __init__(self, source, tree):
        super().__init__(source)
        self.module_bindings = {}
        for statement in lexical_nodes(tree.body):
            self.bind_raw_text(statement, self.module_bindings)

    def smell(self, node, evidence, related=()):
        self.add(
            "PY10b_test_smell",
            node,
            evidence,
            "Does the immediate production contract require this test mechanism, "
            "or can the test exercise observable behavior; is a documented aliasing "
            "or compatibility contract a Counterweight?",
            related=related,
        )

    def bind_raw_text(self, node, bindings):
        assignment = simple_assignment(node)
        if assignment:
            name, value = assignment
            origin = raw_text_origin(value, bindings)
            if origin:
                bindings[name] = origin[0], (*origin[1], node)
            else:
                bindings.pop(name, None)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.optional_vars, ast.Name):
                    origin = raw_text_origin(item.context_expr, bindings)
                    if origin:
                        bindings[item.optional_vars.id] = origin
                    else:
                        bindings.pop(item.optional_vars.id, None)

    def visit_ClassDef(self, node):
        self.symbols.append(node.name)
        self.generic_visit(node)
        self.symbols.pop()

    def visit_Attribute(self, node):
        if node.attr.startswith("_legacy"):
            self.smell(
                node, f"Test accesses private compatibility attribute '{node.attr}'"
            )
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self.symbols.append(node.name)
        nodes = lexical_nodes(node.body)
        bindings = self.module_bindings.copy()
        for param in parameters(node):
            bindings.pop(param.arg, None)
        for child in nodes:
            self.bind_raw_text(child, bindings)
            expressions = []
            if isinstance(child, ast.Assert):
                expressions = [child.test]
            elif isinstance(child, ast.Call) and name_path(child.func).startswith(
                "self.assert"
            ):
                expressions = child.args
                if (
                    terminal_name(child.func) in {"assertIn", "assertNotIn"}
                    and len(child.args) >= 2
                ):
                    self.raw_assertion(child, child.args[1], bindings)
            for expression in expressions:
                for check in local_walk(expression):
                    if isinstance(check, ast.Compare):
                        for op, right in zip(check.ops, check.comparators, strict=True):
                            if isinstance(op, (ast.In, ast.NotIn)):
                                self.raw_assertion(check, right, bindings)
                    elif (
                        isinstance(check, ast.Call)
                        and isinstance(check.func, ast.Attribute)
                        and check.func.attr in {"find", "startswith"}
                    ):
                        self.raw_assertion(check, check.func.value, bindings)
        self.getter_mutations(nodes)
        calls = [child for child in nodes if isinstance(child, ast.Call)]
        windows = patch_windows(node, nodes)
        for window in windows:
            symbol = patch_symbol(window.patch)
            invoked = next(
                (
                    call
                    for call in calls
                    if terminal_name(call.func) == symbol
                    and window.start <= (call.lineno, call.col_offset) < window.end
                ),
                None,
            )
            if invoked:
                site = window.activation or window.patch
                related = (invoked,)
                if window.patch not in ast.walk(site):
                    related = (window.patch, invoked)
                self.smell(
                    site,
                    f"Patch targets directly invoked SUT symbol '{symbol}'",
                    related,
                )
        self.generic_visit(node)
        self.symbols.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def raw_assertion(self, node, operand, bindings):
        origin = raw_text_origin(operand, bindings)
        if origin and origin[0] == "text":
            self.smell(
                node,
                "Assertion checks raw source/documentation text from a .py/.md path",
                tuple(dict.fromkeys(origin[1])),
            )

    def getter_mutations(self, nodes):
        getters = {}
        for node in nodes:
            assignment = simple_assignment(node)
            if assignment:
                name, value = assignment
                for child in local_walk(value):
                    if isinstance(child, ast.Call):
                        self.getter_recall(child, getters)
                getters.pop(name, None)
                if isinstance(value, ast.Call) and isinstance(
                    value.func, ast.Attribute
                ):
                    getters[name] = GetterReturn(name_path(value.func), node, value)
            for target in write_targets(node):
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in getters
                ):
                    getters[target.value.id].mutations.append(node)
            if not isinstance(node, ast.Call):
                continue
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in getters
                and node.func.attr in {"append", "extend", "pop", "clear"}
            ):
                getters[node.func.value.id].mutations.append(node)
            self.getter_recall(node, getters)

    def getter_recall(self, node, getters):
        for name, getter in getters.items():
            if (
                node is not getter.initial
                and getter.mutations
                and name_path(node.func) == getter.method
            ):
                self.smell(
                    getter.binding,
                    f"Getter '{getter.method}' return '{name}' is mutated before re-call",
                    (*getter.mutations, node),
                )
                getter.mutations.clear()


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


def intersects(node, lines):
    return lines is None or any(
        line in lines for line in range(node.lineno, node.end_lineno + 1)
    )


def parse_source(raw, path, errors, *, base=False, pattern=None):
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        source = raw.decode(encoding)
        if pattern is not None and not pattern.search(source):
            return None
        if has_conflict(source):
            raise SyntaxError("Unresolved merge conflict markers")
        tree = ast.parse(source, filename=f"base:{path}" if base else path)
        return source, tree
    except (SyntaxError, UnicodeError, LookupError) as error:
        errors.add((path, ("base: " if base else "") + " ".join(str(error).split())))
        return None


def moved_defenses(before, after):
    """Compare signature presence per function, rather than defense multiplicity."""

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
            lost.setdefault(signature, []).append((path, symbol, candidate["span"][0]))
    for identity, candidate in post.items():
        path, symbol, signature = identity
        if identity in pre or signature not in lost:
            continue
        origins = [
            f"{old_path}:{line} ({old_symbol}, pre-change line {line})"
            for old_path, old_symbol, line in lost[signature]
            if (old_path, old_symbol) != (path, symbol)
        ]
        if origins:
            yield {
                **candidate,
                "rule": "PY10a_moved_defense",
                "path": path,
                "change_tag": "moved-defense",
                "evidence": "Equivalent defense removed from " + "; ".join(origins),
                "verify": "Did the immediate callee become the owner of this validation, "
                "or is this a relocated hedge; is the new location a genuine boundary "
                "(Counterweight)?",
            }


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


def framework_hook(node):
    return any(
        context_tokens(decorator)
        & {
            "route",
            "get",
            "post",
            "put",
            "delete",
            "patch",
            "fixture",
            "property",
            "abstractmethod",
            "overload",
            "contextmanager",
            "register",
            "hook",
        }
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
        self.own_bodies = []
        self.aliases = {}
        self.definitions = []
        self.references = {name: ReferenceCounts() for name in targets}
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
        self.own_bodies.append(node.name)
        self.visit_scope(node)
        self.own_bodies.pop()
        self.symbols.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        for expr in (*node.decorator_list, *node.bases, *node.keywords):
            self.visit(expr)
        self.symbols.append(node.name)
        self.visit_scope(node)
        self.symbols.pop()

    def reference(self, node, name, *, ambiguous=False):
        if name in self.targets and name not in self.own_bodies:
            refs = self.references[name]
            if node in self.call_funcs and not ambiguous:
                refs.calls.append(self.call_funcs[node])
            else:
                refs.non_calls += 1

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            if node.id in self.aliases:
                alias = self.aliases[node.id]
                for name in alias.targets:
                    self.reference(node, name, ambiguous=alias.ambiguous)
            else:
                self.reference(node, node.id)

    def visit_Attribute(self, node):
        self.reference(node, node.attr)
        self.generic_visit(node)


def effective_arguments(node, call):
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
    }


def constant_defaults(definition, calls, change_tag):
    path, source, node, symbol = definition
    if framework_hook(node):
        return []
    bound = [effective_arguments(node, call) for call in calls]
    if any(arguments is None for arguments in bound):
        return []
    record = CandidateRecords(source)
    record.symbols = symbol.split(".")
    for name, default in parameter_defaults(node).items():
        if default is None:
            continue
        values = {arguments[name] for arguments in bound}
        if len(values) == 1 and None not in values:
            record.add(
                "PY10c_cross_test_reachability",
                default,
                f"Parameter '{name}': {len(calls)} resolved prod call sites; uniform value: {values.pop()}",
                "Can the immediate production callers remove or inline this parameter/branch, "
                "or is it a public/external knob (Counterweight)?",
            )
    return [
        {**candidate, "path": path, "change_tag": change_tag}
        for candidate in record.candidates
    ]


def cross_reachability(root, tip, args, targets, deleted_calls, active_trees, errors):
    if not targets:
        return [], 0
    pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(name) for name in sorted(targets)) + r")\b"
    )
    definitions = {name: [] for name in targets}
    counts = {
        name: {"prod": ReferenceCounts(), "test": ReferenceCounts()} for name in targets
    }
    checked = 0
    exported = set()
    for path, raw in snapshot_sources(root, tip, args.exclude):
        checked += 1
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
        if file_role == "prod":
            for statement in local_walk(tree):
                if not isinstance(statement, ast.stmt):
                    continue
                parts = list(local_walk(statement))
                if any(
                    isinstance(child, ast.Name) and child.id == "__all__"
                    for child in parts
                ):
                    exported.update(
                        child.value
                        for child in parts
                        if isinstance(child, ast.Constant)
                        and isinstance(child.value, str)
                    )
            for node, symbol in refs.definitions:
                definitions[node.name].append((path, source, node, symbol))
    candidates = []
    for name, defs in definitions.items():
        prod = counts[name]["prod"]
        test = counts[name]["test"]
        if (
            len(defs) == 1
            and name not in exported
            and not prod.non_calls
            and prod.calls
        ):
            candidates.extend(
                constant_defaults(
                    defs[0], prod.calls, "changed" if args.base else "path-audit"
                )
            )
        if prod.calls or prod.non_calls:
            continue
        tag = (
            "cross-test"
            if test.calls or test.non_calls
            else "orphaned"
            if name in deleted_calls
            else None
        )
        if tag is None:
            continue
        evidence = (
            f"Observed prod calls: {len(prod.calls)}, prod non-call refs: {prod.non_calls}; "
            f"test calls: {len(test.calls)}, test non-call refs: {test.non_calls}"
        )
        if len(defs) > 1:
            evidence += f"; ambiguous_defs: {len(defs)}"
        for path, source, node, symbol in defs:
            if framework_hook(node):
                continue
            record = CandidateRecords(source)
            record.symbols = symbol.split(".")
            record.add(
                "PY10c_cross_test_reachability",
                node,
                evidence,
                "Does the immediate caller inventory confirm this production entry point "
                "serves only tests or is orphaned; is a public/dynamic framework contract "
                "a Counterweight?",
            )
            candidates.append({**record.candidates[0], "path": path, "change_tag": tag})
    return candidates, checked


def scan(root, files, base, tip, args):
    candidates = []
    parse_errors = set()
    scanned = set()
    before, after = {}, {}
    active_trees = {}
    targets, deleted_calls = set(), set()
    for change in sorted(files, key=lambda item: item.path):
        relative = change.path
        if change.old_path is not None:
            raw = git(root, "show", f"{base}:{change.old_path}")
            if b"\x00" not in raw:
                scanned.add(relative)
                parsed = parse_source(raw, change.old_path, parse_errors, base=True)
                if parsed:
                    deleted_calls.update(
                        terminal_name(node.func)
                        for node in ast.walk(parsed[1])
                        if isinstance(node, ast.Call)
                        and intersects(node, change.deleted_lines)
                        and target_name(terminal_name(node.func))
                    )
                if parsed and role(Path(change.old_path), args.test_glob) == "prod":
                    probes = CandidateProbes(*parsed)
                    probes.visit(parsed[1])
                    before[relative] = probes
        path = root / relative
        if change.deleted or (tip is None and path.is_symlink()):
            continue
        raw = git(root, "show", f"{tip}:{relative}") if tip else path.read_bytes()
        if b"\x00" in raw:
            continue
        scanned.add(relative)
        parsed = parse_source(raw, relative, parse_errors)
        active_trees[relative] = parsed
        if not parsed:
            continue
        is_test = role(Path(relative), args.test_glob) == "test"
        probes = (TestProbes if is_test else CandidateProbes)(*parsed)
        probes.visit(parsed[1])
        if not is_test:
            after[relative] = probes
            targets.update(
                node.name
                for node in ast.walk(parsed[1])
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and target_name(node.name)
                and not framework_hook(node)
                and intersects(node, change.added_lines)
            )
        for candidate in probes.candidates:
            if change.added_lines is not None and not any(
                line in change.added_lines
                for start, end in candidate["_spans"]
                for line in range(start, end + 1)
            ):
                continue
            candidates.append(
                {
                    **candidate,
                    "path": relative,
                    "change_tag": "changed" if args.base else "path-audit",
                }
            )
    candidates.extend(moved_defenses(before, after))
    cross, checked = cross_reachability(
        root,
        tip,
        args,
        targets | deleted_calls,
        deleted_calls,
        active_trees,
        parse_errors,
    )
    candidates.extend(cross)
    candidates.sort(key=lambda item: (item["path"], item["span"][0], item["rule"]))
    for index, candidate in enumerate(candidates, 1):
        candidate.pop("_spans")
        candidate["id"] = f"C{index:02d}"
    return {
        "summary": {
            "mode": "diff" if args.base else "paths",
            "files_scanned": len(scanned),
            "cross_checked_files": checked,
            "candidate_count": len(candidates),
            "parse_error_count": len(parse_errors),
        },
        "parse_errors": [
            {"path": path, "error": error} for path, error in sorted(parse_errors)
        ],
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
            root, files, base, tip = diff_scope(args)
        else:
            root, files = paths_scope(args)
            base = tip = None
        render(scan(root, files, base, tip, args), args.format)
    except (ScanError, OSError) as error:
        print(f"scan_python.py: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
