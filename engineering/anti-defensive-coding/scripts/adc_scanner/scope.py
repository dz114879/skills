"""Git and explicit-path scan scope resolution."""

from dataclasses import dataclass
import fnmatch
import os
from pathlib import Path
import re
import subprocess

from .ast_helpers import parallel_map
from .registry import DIFF_FLAGS, SKIP_SEGMENTS, TEST_GLOBS


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
    entries = []
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
        entries.append((status, old_path, path, pre_python, post_python))

    def extract_hunks(entry):
        _status, old_path, path, pre_python, post_python = entry
        patch = git(
            root,
            "diff",
            *DIFF_FLAGS,
            "--unified=0",
            "--inter-hunk-context=0",
            # Unlike `-w`, keeps indentation changes, which are semantic in Python.
            "--ignore-space-at-eol",
            *endpoints,
            "--",
            *dict.fromkeys((old_path, path)),
        )
        if any(line.startswith(b"Binary files ") for line in patch.splitlines()):
            return None
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
        return FileChange(
            path, added, old_path if pre_python else None, deleted, not post_python
        )

    changes = [
        change
        for change in parallel_map(extract_hunks, entries, args.jobs)
        if change is not None
    ]
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


def touches(spans, lines):
    """`lines is None` marks a file whose every line counts as added."""
    return lines is None or any(
        line in lines for start, end in spans for line in range(start, end + 1)
    )


def intersects(node, lines):
    return touches([(node.lineno, node.end_lineno)], lines)
