"""Scan orchestration, lossless bundling, and output rendering."""

import ast
import json
from pathlib import Path

from .ast_helpers import parse_source
from .probes_prod import CandidateProbes
from .probes_test import TestProbes
from .reachability import (
    call_sites,
    cross_reachability,
    framework_hook,
    lost_calls,
    moved_defenses,
    target_name,
)
from .registry import VERIFY_KEYS, evidence_text, verify_text
from .scope import git, intersects, role, touches


def render(result, output_format):
    if output_format == "json":
        print(json.dumps(result))
        return
    summary = result["summary"]
    print(
        f"Scanned {summary['files_scanned']} files "
        f"({summary['cross_checked_files']} cross-checked) in {summary['mode']} mode; "
        f"{summary['candidate_count']} candidates, "
        f"{summary['parse_error_count']} parse errors"
    )
    for error in result["parse_errors"]:
        print(f"[PARSE_ERROR] {error['path']}: {error['error']}")
    candidates = result["candidates"]
    if candidates:
        print("Verification Legend:")
        for verify in dict.fromkeys(candidate["verify"] for candidate in candidates):
            print(f"  [{VERIFY_KEYS[verify]}] {verify}")
    default_tag = "path-audit" if summary["mode"] == "paths" else "changed"
    path = None
    for candidate in candidates:
        if candidate["path"] != path:
            path = candidate["path"]
            print(f"=== {path} ===")
        short_key = VERIFY_KEYS[candidate["verify"]]
        start, end = candidate["span"]
        span = str(start) if start == end else f"{start}-{end}"
        tag = candidate["change_tag"]
        tag_suffix = "" if tag == default_tag else f" [{tag}]"
        print(
            f"[{candidate['id']}] :{span} "
            f"({candidate['symbol']}) {short_key}{tag_suffix}"
        )
        if short_key not in {"PY10c:default", "PY10c:bundle"} and not (
            candidate["rule"] == "PY04_lossy_falsy_coalesce"
            and not candidate["evidence"].startswith("fallback ")
        ):
            print(f"  Code: {candidate['code']}")
        print(f"  Evidence: {candidate['evidence']}")


def bundle_coalesces(candidates):
    groups = {}
    bundled = []
    for candidate in candidates:
        if (
            candidate["rule"] == "PY04_lossy_falsy_coalesce"
            and candidate["symbol"] != "<module>"
        ):
            key = candidate["path"], candidate["symbol"], candidate["change_tag"]
            groups.setdefault(key, []).append(candidate)
        else:
            bundled.append(candidate)
    for members in groups.values():
        members.sort(key=lambda item: (item["span"][0], item["_coalesce"][0]))
        candidate = members[0]
        if len(members) >= 2:
            top_level, sites = [], []
            for item in members:
                col, end_col, fallback = item["_coalesce"]
                code = item["code"]
                line, end_line = item["span"]
                for index, outer in enumerate(top_level):
                    outer_col, outer_end, _ = outer["_coalesce"]
                    if (
                        line == end_line == outer["span"][0] == outer["span"][1]
                        and outer_col <= col
                        and end_col <= outer_end
                        and (col, end_col) != (outer_col, outer_end)
                    ):
                        sites[index] += evidence_text(
                            "PY04_bundle_inner", code=code, fallback=fallback
                        )
                        break
                else:
                    top_level.append(item)
                    sites.append(
                        evidence_text(
                            "PY04_bundle_site", line=line, code=code, fallback=fallback
                        )
                    )
            candidate["span"] = [
                min(item["span"][0] for item in members),
                max(item["span"][1] for item in members),
            ]
            candidate["evidence"] = evidence_text(
                "PY04_bundle",
                count=len(members),
                sites="; ".join(sites),
            )
            if len({item["verify"] for item in members}) > 1:
                candidate["verify"] = verify_text("PY04:mixed")
        bundled.append(candidate)
    return bundled


def scan(root, files, base, tip, args):
    candidates = []
    parse_errors = set()
    scanned = set()
    before, after = {}, {}
    active_trees = {}
    selected, deleted_calls, added_calls = {}, [], []
    for change in sorted(files, key=lambda item: item.path):
        relative = change.path
        if change.old_path is not None:
            raw = git(root, "show", f"{base}:{change.old_path}")
            if b"\x00" not in raw:
                scanned.add(relative)
                parsed = parse_source(raw, change.old_path, parse_errors, base=True)
                if parsed:
                    base_role = role(Path(change.old_path), args.test_glob)
                    deleted_calls.extend(
                        call_sites(
                            parsed[1], change.deleted_lines, change.old_path, base_role
                        )
                    )
                    if base_role == "prod":
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
        file_role = role(Path(relative), args.test_glob)
        if args.base:
            added_calls.extend(
                call_sites(parsed[1], change.added_lines, relative, file_role)
            )
        probes = (TestProbes if file_role == "test" else CandidateProbes)(*parsed)
        probes.visit(parsed[1])
        if file_role == "prod":
            after[relative] = probes
            selected.update(
                ((relative, node.lineno), node.name)
                for node in ast.walk(parsed[1])
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and target_name(node.name)
                and not framework_hook(node)
                and intersects(node, change.added_lines)
            )
        for candidate in probes.candidates:
            if not touches(candidate["_scope"], change.added_lines):
                continue
            candidate["path"] = relative
            candidate["change_tag"] = "changed" if args.base else "path-audit"
            candidates.append(candidate)
    moved_defenses(before, after, {change.path: change for change in files})
    cross, checked = cross_reachability(
        root,
        tip,
        args,
        selected,
        lost_calls(deleted_calls, added_calls),
        active_trees,
        parse_errors,
    )
    candidates.extend(cross)
    candidates = bundle_coalesces(candidates)
    candidates.sort(key=lambda item: (item["path"], item["span"][0], item["rule"]))
    for index, candidate in enumerate(candidates, 1):
        candidate.pop("_scope")
        candidate.pop("_coalesce", None)
        code = candidate["code"]
        if len(code) > 200:
            candidate["code"] = code[:197] + "..."
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
