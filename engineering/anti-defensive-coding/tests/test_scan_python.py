import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "scan_python.py"


class ScanPythonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, path, source):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(source).lstrip("\n"), encoding="utf-8")
        return target

    def cli(self, *args, code=0, cwd=None):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, args)],
            cwd=cwd or self.root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, code, result.stderr)
        if code == 0:
            self.assertEqual(result.stderr, "")
        else:
            self.assertTrue(result.stderr)
        return result.stdout

    def scan(self, *args, **kwargs):
        return json.loads(self.cli(*args, "--format", "json", **kwargs))

    def git(self, *args):
        return subprocess.run(
            [
                "git",
                "-c",
                "user.name=Extractor Test",
                "-c",
                "user.email=test@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=/dev/null",
                *args,
            ],
            cwd=self.root,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def init_repo(self):
        self.git("init", "-q", "-b", "main")

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def test_relative_non_git_paths_schema_and_non_execution(self):
        self.put(
            "src/model.py",
            """
            raise SystemExit("executed")
            class Record:
                value: int
                def read(self):
                    return getattr(self, "value", 0)
            """,
        )
        result = self.scan("--paths", "src/model.py")
        self.assertEqual(set(result), {"summary", "parse_errors", "candidates"})
        self.assertEqual(
            result["summary"],
            {
                "mode": "paths",
                "files_scanned": 1,
                "cross_checked_files": 0,
                "candidate_count": 1,
                "parse_error_count": 0,
            },
        )
        self.assertEqual(result["parse_errors"], [])
        candidate = result["candidates"][0]
        self.assertEqual(
            set(candidate),
            {
                "id",
                "rule",
                "path",
                "span",
                "symbol",
                "change_tag",
                "code",
                "evidence",
                "verify",
            },
        )
        self.assertEqual(candidate["id"], "C01")
        self.assertEqual(candidate["rule"], "PY01_reflect_default")
        self.assertEqual(candidate["path"], "model.py")
        self.assertEqual(candidate["span"], [5, 5])
        self.assertEqual(candidate["symbol"], "Record.read")
        self.assertEqual(candidate["change_tag"], "path-audit")
        self.assertEqual(candidate["code"], 'getattr(self, "value", 0)')
        self.assertEqual(
            candidate["evidence"],
            "Same-file class Record declares attribute 'value' at line 3",
        )
        self.assertIn("owned", candidate["verify"])
        self.assertIn("'value'", candidate["verify"])
        self.assertIn("Counterweight", candidate["verify"])

    def test_reflection_pairs_and_all_same_file_declarations(self):
        self.put(
            "model.py",
            """
            import external.module
            import other as alias
            from package import service
            from package import another as imported
            class First:
                value: int
            class Second:
                value = 1
                def __init__(self):
                    self.value = 2
                    self.other: str = ""
                async def read(self):
                    def nested():
                        return hasattr(self, "value")
                    return getattr(self, "other", "")
            getattr(local, "missing", None, extra)
            getattr(local, "value")
            getattr(external, "value", None)
            hasattr(alias, "value")
            getattr(service, "value", None)
            hasattr(imported, "value")
            getattr(local, variable, None)
            hasattr(local, "value", extra)
            tools.getattr(local, "value", None)
        """,
        )
        result = self.scan("--paths", ".")
        self.assertEqual(len(result["candidates"]), 3)
        first, second, third = result["candidates"]
        self.assertEqual(first["symbol"], "Second.read.nested")
        self.assertEqual(
            first["evidence"],
            (
                "Same-file class First declares attribute 'value' at line 6; "
                "Same-file class Second declares attribute 'value' at line 8; "
                "Same-file class Second declares attribute 'value' at line 10"
            ),
        )
        self.assertIn("'other' at line 11", second["evidence"])
        self.assertEqual(third["symbol"], "<module>")
        self.assertIn("No same-file", third["evidence"])

    def test_mixed_parse_failures_keep_valid_candidates_in_both_formats(self):
        self.put("good.py", 'hasattr(item, "value")\n')
        self.put("syntax.py", "def broken(:\n")
        self.put("conflict.py", "<<<<<<< ours\nx = 1\n=======\nx = 2\n>>>>>>> theirs\n")
        self.put("heading.py", '"""Heading\n=======\n"""\n')
        (self.root / "decode.py").write_bytes(b"# coding: utf-8\nx = '\xff'\n")
        (self.root / "binary.py").write_bytes(b"hasattr(x, 'value')\x00")
        (self.root / "cookie.py").write_bytes(
            b"# coding: latin-1\nhasattr(item, 'caf\xe9')\n"
        )
        (self.root / "bom.py").write_bytes(b"\xef\xbb\xbfhasattr(item, 'bom')\n")
        result = self.scan("--paths", ".")
        self.assertEqual(
            result["summary"],
            {
                "mode": "paths",
                "files_scanned": 7,
                "cross_checked_files": 0,
                "candidate_count": 3,
                "parse_error_count": 3,
            },
        )
        errors = result["parse_errors"]
        self.assertEqual(
            [error["path"] for error in errors],
            ["conflict.py", "decode.py", "syntax.py"],
        )
        for error in errors:
            self.assertEqual(set(error), {"path", "error"})
            self.assertTrue(error["error"])
        self.assertIn("conflict", errors[0]["error"].lower())
        self.assertEqual([c["id"] for c in result["candidates"]], ["C01", "C02", "C03"])
        self.assertEqual(result["candidates"][1]["code"], "hasattr(item, 'café')")
        compact = self.cli("--paths", ".").splitlines()
        self.assertEqual(
            compact[0],
            "Scanned 7 files (0 cross-checked); 3 candidates, 3 parse errors",
        )
        self.assertEqual(
            compact[1:4],
            [f"[PARSE_ERROR] {error['path']}: {error['error']}" for error in errors],
        )
        self.assertEqual(len(compact), 16)
        for index, candidate in enumerate(result["candidates"]):
            start = 4 + index * 4
            self.assertEqual(
                compact[start],
                (
                    f"[{candidate['id']}] {candidate['path']}:"
                    f"{candidate['span'][0]}-{candidate['span'][1]} "
                    f"({candidate['symbol']}) — {candidate['rule']} [path-audit]"
                ),
            )
            self.assertEqual(compact[start + 1], f"  Code: {candidate['code']}")
            self.assertEqual(compact[start + 2], f"  Evidence: {candidate['evidence']}")
            self.assertEqual(compact[start + 3], f"  Verify: {candidate['verify']}")

    def test_non_text_codec_cookie_is_a_parse_error_without_aborting(self):
        self.put("good.py", 'hasattr(item, "value")\n')
        self.put("bad_codec.py", '# coding: base64_codec\nhasattr(item, "broken")\n')
        result = self.scan("--paths", ".")
        self.assertEqual(result["summary"]["files_scanned"], 2)
        self.assertEqual(result["summary"]["parse_error_count"], 1)
        self.assertEqual([c["path"] for c in result["candidates"]], ["good.py"])
        self.assertEqual(result["parse_errors"][0]["path"], "bad_codec.py")
        self.assertIn("base64_codec", result["parse_errors"][0]["error"])

    @unittest.skipIf(os.geteuid() == 0, "Root bypasses directory read permissions")
    def test_unreadable_subtree_fails_instead_of_reporting_a_partial_scan(self):
        self.put("good.py", 'hasattr(item, "value")\n')
        self.put("blocked/hidden.py", 'hasattr(item, "hidden")\n')
        blocked = self.root / "blocked"
        blocked.chmod(0)
        self.addCleanup(blocked.chmod, 0o700)
        self.assertEqual(self.cli("--paths", ".", code=2), "")

    def test_path_inventory_exclusions_and_additive_test_roles(self):
        skipped = [
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
        ]
        for directory in skipped:
            self.put(f"src/{directory}/ignored.py", 'hasattr(x, "skip")\n')
        tests = [
            "tests/helper.py",
            "test/helper.py",
            "test_model.py",
            "model_test.py",
            "conftest.py",
            "model_test_support.py",
            "checks/case.py",
            "custom.py",
        ]
        for path in tests:
            self.put(path, 'hasattr(x, "test")\n')
        self.put("tests/broken.py", "def bad(:\n")
        self.put("omit/first.py", 'hasattr(x, "excluded")\n')
        self.put("other/generated.py", 'hasattr(x, "excluded")\n')
        self.put("production.py", 'hasattr(x, "value")\n')
        self.put("notes.txt", "not Python")
        result = self.scan(
            "--paths",
            ".",
            "production.py",
            "notes.txt",
            "--exclude",
            "omit/*",
            "--exclude",
            "generated.py",
            "--test-glob",
            "checks/*",
            "--test-glob",
            "custom.py",
        )
        self.assertEqual(result["summary"]["files_scanned"], 10)
        self.assertEqual([c["path"] for c in result["candidates"]], ["production.py"])
        self.assertEqual(
            [e["path"] for e in result["parse_errors"]], ["tests/broken.py"]
        )
        explicit = self.scan("--paths", "production.py", "--exclude", "*.py")
        self.assertEqual(explicit["summary"]["files_scanned"], 0)

    def test_invalid_cli_and_explicit_root_boundaries(self):
        self.put("scope/a.py", 'hasattr(x, "a")\n')
        self.put("other/b.py", 'hasattr(x, "b")\n')
        for args in [
            [],
            ["--tip", "HEAD"],
            ["--merge-base"],
            ["--base", "HEAD", "--paths", "scope"],
            ["--paths", "scope", "--tip", "HEAD"],
            ["--paths", "scope", "--merge-base"],
            ["--paths", "missing"],
            ["--paths", "scope", "--repo", "missing"],
            ["--paths", "other", "--repo", "scope"],
            ["--paths", "scope", "--repo", "scope/a.py"],
            ["--paths", "scope", "--format", "xml"],
        ]:
            with self.subTest(args=args):
                self.cli(*args, code=2)
        result = self.scan("--paths", "scope/a.py", "other/b.py")
        self.assertEqual(
            [c["path"] for c in result["candidates"]], ["other/b.py", "scope/a.py"]
        )
        result = self.scan("--paths", "scope/a.py", "--repo", ".")
        self.assertEqual(result["candidates"][0]["path"], "scope/a.py")

    def test_worktree_diff_includes_index_disk_and_untracked_with_span_filter(self):
        self.init_repo()
        self.put(
            "src/changed.py",
            """
            unchanged = getattr(item, "unchanged", None)
            value = getattr(
                item,
                "value",
                None,
            )
        """,
        )
        self.put("removed.py", 'hasattr(item, "removed")\n')
        self.put(".gitignore", "ignored/\n")
        base = self.commit()
        self.put(
            "src/changed.py",
            """
            unchanged = getattr(item, "unchanged", None)
            value = getattr(
                item,
                "value",
                0,
            )
        """,
        )
        self.git("add", "src/changed.py")
        with (self.root / "src/changed.py").open("a") as source:
            source.write('hasattr(item, "unstaged")\n')
        (self.root / "removed.py").unlink()
        self.put("untracked.py", 'hasattr(item, "untracked")\n')
        self.put("ignored/hidden.py", 'hasattr(item, "ignored")\n')
        self.put("build/generated.py", 'hasattr(item, "skip")\n')
        result = self.scan("--base", base, "--repo", ".", cwd=self.root)
        self.assertEqual(
            result["summary"],
            {
                "mode": "diff",
                "files_scanned": 2,
                "cross_checked_files": 0,
                "candidate_count": 3,
                "parse_error_count": 0,
            },
        )
        self.assertEqual(
            [c["span"] for c in result["candidates"]], [[2, 6], [7, 7], [1, 1]]
        )
        self.assertTrue(all(c["change_tag"] == "changed" for c in result["candidates"]))
        self.assertEqual(
            result["candidates"][0]["code"], 'getattr( item, "value", 0, )'
        )
        result = self.scan("--base", base, cwd=self.root / "src")
        self.assertEqual(result["candidates"][0]["path"], "src/changed.py")
        result = self.scan(
            "--base", base, "--exclude", "untracked.py", "--test-glob", "changed.py"
        )
        self.assertEqual(result["summary"]["files_scanned"], 1)
        self.assertEqual(result["candidates"], [])

    def test_git_paths_use_worktree_root_and_bad_refs_exit_two(self):
        self.init_repo()
        self.put("src/a.py", 'hasattr(item, "value")\n')
        self.commit()
        result = self.scan("--paths", "src/a.py")
        self.assertEqual(result["candidates"][0]["path"], "src/a.py")
        for args in [
            ["--base", "missing"],
            ["--base", ""],
            ["--base", "HEAD", "--tip", ""],
            ["--base", "HEAD", "--tip", "missing"],
            ["--base", "HEAD", "--merge-base", "--tip", "missing"],
            ["--base", "HEAD", "--repo", "missing"],
            ["--base", "HEAD:src/a.py"],
        ]:
            with self.subTest(args=args):
                self.cli(*args, code=2)

    def test_commit_and_merge_base_snapshots_ignore_disk_edits(self):
        self.init_repo()
        self.put("model.py", "value = 0\n")
        ancestor = self.commit()
        self.git("checkout", "-qb", "base")
        self.put("base_only.py", 'hasattr(item, "base_only")\n')
        base = self.commit()
        self.git("checkout", "-qb", "feature", ancestor)
        self.put("model.py", 'value = getattr(item, "committed", 0)\n')
        tip = self.commit()
        self.put("model.py", "def uncommitted(:\n")
        self.put("untracked.py", 'hasattr(item, "untracked")\n')
        for args in [
            ["--base", ancestor, "--tip", tip],
            ["--base", base, "--merge-base"],
            ["--base", base, "--merge-base", "--tip", tip],
        ]:
            with self.subTest(args=args):
                result = self.scan(*args)
                self.assertEqual(
                    result["summary"],
                    {
                        "mode": "diff",
                        "files_scanned": 1,
                        "cross_checked_files": 0,
                        "candidate_count": 1,
                        "parse_error_count": 0,
                    },
                )
                self.assertEqual(
                    result["candidates"][0]["code"], 'getattr(item, "committed", 0)'
                )
                self.assertEqual(result["candidates"][0]["change_tag"], "changed")

    def test_renames_binary_and_git_configuration_preserve_file_local_hunks(self):
        self.init_repo()
        original = 'hasattr(item, "unchanged")\n' + "value = 1\n" * 30
        self.put("old.py", original)
        self.put("binary.py", 'hasattr(item, "before")\n')
        self.put(".gitattributes", "*.py diff=custom\nbinary.py -diff\n")
        self.put("textconv.py", "value = 1\n")
        base = self.commit()
        renamed = 'renamed\t"é\n[one].py'
        self.git("mv", "old.py", renamed)
        self.put(renamed, original + 'hasattr(item, "renamed")\n')
        self.put("added.py", 'hasattr(item, "added")\n')
        self.put("binary.py", 'hasattr(item, "binary")\n')
        self.put("textconv.py", 'hasattr(item, "real_source")\n')
        self.put("build/generated.py", 'hasattr(item, "skipped")\n')
        tip = self.commit()
        self.git("config", "diff.external", "false")
        self.git("config", "diff.custom.textconv", "false")
        self.git("config", "diff.custom.command", "false")
        self.git("config", "diff.noprefix", "true")
        self.git("config", "diff.mnemonicPrefix", "true")
        self.git("config", "diff.relative", "true")
        self.git("config", "diff.renames", "false")
        self.git("config", "color.ui", "always")
        self.git("config", "core.quotePath", "true")
        for args in [
            ["--base", base],
            ["--base", base, "--tip", tip],
            ["--base", base, "--merge-base"],
        ]:
            with self.subTest(args=args):
                result = self.scan(*args)
                self.assertEqual(result["summary"]["files_scanned"], 3)
                self.assertEqual(
                    [(c["path"], c["span"]) for c in result["candidates"]],
                    [
                        ("added.py", [1, 1]),
                        (renamed, [32, 32]),
                        ("textconv.py", [1, 1]),
                    ],
                )

    def test_declaration_clues_respect_assignment_targets_and_lexical_scope(self):
        self.put(
            "evidence.py",
            """
            class Model:
                foreign.field = 1
                one, two = (1, 2)
                def __init__(self):
                    self.child.value = 1
                    def later():
                        self.nested = 1
                    if ready:
                        self.real = 1
                    self.uninitialized: int
            hasattr(item, "foreign")
            hasattr(item, "child")
            hasattr(item, "nested")
            hasattr(item, "real")
            hasattr(item, "one")
            hasattr(item, "two")
            hasattr(item, "uninitialized")
        """,
        )
        candidates = self.scan("--paths", ".")["candidates"]
        for candidate in candidates[:3]:
            self.assertIn("No same-file", candidate["evidence"])
        self.assertIn("'real' at line 9", candidates[3]["evidence"])
        self.assertIn("'one' at line 3", candidates[4]["evidence"])
        self.assertIn("'two' at line 3", candidates[5]["evidence"])
        self.assertIn("No same-file", candidates[6]["evidence"])

    def test_checkout_ancestors_do_not_affect_relative_path_rules(self):
        for ancestor in ("build", "tests"):
            with self.subTest(ancestor=ancestor):
                self.root = Path(self.temp.name) / ancestor / "repo"
                self.root.mkdir(parents=True)
                self.init_repo()
                self.put("model.py", "value = 0\n")
                self.commit()
                self.put("model.py", 'hasattr(item, "value")\n')
                for args in (("--base", "HEAD"), ("--paths", ".")):
                    with self.subTest(args=args):
                        result = self.scan(*args)
                        self.assertEqual(result["summary"]["files_scanned"], 1)
                        self.assertEqual(
                            [c["path"] for c in result["candidates"]], ["model.py"]
                        )

    def test_non_git_root_roles_can_be_selected_by_glob(self):
        self.put("tests/helper.py", 'hasattr(item, "test")\n')
        result = self.scan("--paths", "tests")
        self.assertEqual(result["summary"]["files_scanned"], 1)
        self.assertEqual([c["path"] for c in result["candidates"]], ["helper.py"])
        tests = self.scan("--paths", "tests", "--test-glob", "*.py")
        self.assertEqual(tests["summary"]["files_scanned"], 1)
        self.assertEqual(tests["candidates"], [])

    def test_code_snippets_are_bounded_and_ids_follow_path_then_line(self):
        self.put("z.py", 'getattr(item, "value", "' + "x" * 300 + '")\n')
        self.put("a.py", 'hasattr(item, "a")\nhasattr(item, "b")\n')
        candidates = self.scan("--paths", "z.py", "a.py")["candidates"]
        self.assertEqual(
            [(c["id"], c["path"], c["span"]) for c in candidates],
            [
                ("C01", "a.py", [1, 1]),
                ("C02", "a.py", [2, 2]),
                ("C03", "z.py", [1, 1]),
            ],
        )
        self.assertEqual(len(candidates[2]["code"]), 200)
        self.assertNotIn("\n", candidates[2]["code"])

    def test_all_modes_leave_full_tree_and_git_status_unchanged(self):
        self.init_repo()
        self.put(".gitignore", "ignored/\n")
        self.put("model.py", "value = 0\n")
        base = self.commit()
        self.put("model.py", 'getattr(item, "committed", None)\n')
        tip = self.commit()
        self.put("model.py", 'getattr(item, "disk", None)\n')
        self.put("untracked.py", 'raise SystemExit("executed")\nhasattr(item, "new")\n')
        self.put("ignored/cache.dat", "preserve ignored bytes")
        self.put("ignored/broken.py", "def broken(:\n")
        self.put("syntax.py", "def broken(:\n")
        self.put("conflict.py", "<<<<<<< ours\nx = 1\n=======\nx = 2\n>>>>>>> theirs\n")

        def snapshot():
            return {
                path.relative_to(self.root).as_posix(): (
                    "directory"
                    if path.is_dir()
                    else hashlib.sha256(path.read_bytes()).hexdigest()
                )
                for path in self.root.rglob("*")
            }

        before_status = self.git("status", "--porcelain")
        before_tree = snapshot()
        for args in [
            ["--base", base],
            ["--base", base, "--tip", tip],
            ["--base", base, "--merge-base"],
            ["--paths", "."],
        ]:
            for output_format in ["json", "compact"]:
                with self.subTest(args=args, output_format=output_format):
                    self.cli(*args, "--format", output_format)
                    self.assertEqual(self.git("status", "--porcelain"), before_status)
                    self.assertEqual(snapshot(), before_tree)


if __name__ == "__main__":
    unittest.main()
