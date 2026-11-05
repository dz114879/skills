#!/usr/bin/env python3
"""Read-only Python AST Candidate Extractor (Python 3.11+)."""

import argparse
from pathlib import Path
import sys


sys.dont_write_bytecode = True
SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from adc_scanner.bundling import render, scan  # noqa: E402
from adc_scanner.scope import ScanError, diff_scope, paths_scope  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--paths", nargs="+", help="Audit files or directories on disk")
    scope.add_argument("--base", help="Base commit for a diff")
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=1,
        help="Number of worker threads for per-file git diff and AST stages",
    )
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
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be >= 1")
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
    sys.exit(main(sys.argv[1:]))
