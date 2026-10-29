#!/usr/bin/env python3
"""Read-only Python AST Candidate Extractor (Python 3.10+)."""

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
    if isinstance(node, ast.AnnAssign):
        if node.value is not None:
            yield node.target
    elif isinstance(node, ast.Assign):
        yield from node.targets
    elif not isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    ):
        for child in ast.iter_child_nodes(node):
            yield from assignments(child)


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


class ReflectionProbes(ast.NodeVisitor):
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

    def visit_ClassDef(self, node):
        self.symbols.append(node.name)
        self.generic_visit(node)
        self.symbols.pop()

    visit_FunctionDef = visit_ClassDef
    visit_AsyncFunctionDef = visit_ClassDef

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
            self.candidates.append(
                {
                    "rule": "PY01_reflect_default",
                    "span": [node.lineno, node.end_lineno],
                    "symbol": ".".join(self.symbols) or "<module>",
                    "code": " ".join(ast.get_source_segment(self.source, node).split())[
                        :200
                    ],
                    "evidence": evidence,
                    "verify": (
                        f"Does the immediate producer or type definition prove the receiver "
                        f"is owned here and guaranteed to define '{attr}', or is it a "
                        "dynamic proxy/plugin/external object (Counterweight)?"
                    ),
                }
            )
        self.generic_visit(node)


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
        probes = ReflectionProbes(source, tree)
        probes.visit(tree)
        for candidate in probes.candidates:
            start, end = candidate["span"]
            if change.added_lines is not None and not any(
                line in change.added_lines for line in range(start, end + 1)
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
