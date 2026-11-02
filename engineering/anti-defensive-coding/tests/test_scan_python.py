import ast
from collections import Counter
from contextlib import chdir, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch


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
            check=False,
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

    def test_moved_defenses_use_full_trees_and_surviving_function_identities(self):
        self.init_repo()
        defenses = {
            "reflect": 'return getattr(item, "value", None)',
            "handler": "try:\n    return item.value\nexcept (TypeError, KeyError):\n    return None",
            "coalesce": 'return item.get("value") or 30',
        }
        for name, defense in defenses.items():
            self.put(
                f"{name}.py",
                "def original(item):\n"
                + textwrap.indent(defense, "    ")
                + "\n\ndef destination(item):\n    return item.value\n",
            )
        self.put(
            "rename.py", 'def original(item):\n    return getattr(item, "name", None)\n'
        )
        self.put(
            "move.py", 'def relocate(item):\n    return getattr(item, "other", None)\n'
        )
        base = self.commit()
        for name, defense in defenses.items():
            # Nesting re-indents the moved lines, so git cannot align them as context.
            self.put(
                f"{name}.py",
                "def original(item):\n    return item.value\n\ndef destination(item):\n"
                "    if item:\n" + textwrap.indent(defense, "        ") + "\n",
            )
        self.put(
            "rename.py", 'def renamed(item):\n    return getattr(item, "name", None)\n'
        )
        self.git("mv", "move.py", "relocated.py")
        result = self.scan("--base", base)
        moved = [c for c in result["candidates"] if c["rule"] == "PY10a_moved_defense"]
        self.assertEqual(
            {c["path"] for c in moved}, {f"{name}.py" for name in defenses}
        )
        expected_moved = {
            "reflect.py": (
                "Receiver item; parameter item: <unannotated> at line 4; "
                "receiver class unknown; same PY01_reflect_default signature "
                "'value' lost from base reflect.py:2 (original) "
                "(signature match only)",
                "python.md §1",
            ),
            "handler.py": (
                "Handler catches (TypeError, KeyError) at line 8; "
                "broad/multi-exception handler has a non-raising exit; "
                "same PY02_error_washing signature KeyError, TypeError "
                "lost from base handler.py:4 (original) (signature match only)",
                "python.md §4",
            ),
            "coalesce.py": (
                "Lookup coalesces to 30 at line 6; "
                "same PY04_lossy_falsy_coalesce signature 'value' "
                "lost from base coalesce.py:2 (original) (signature match only)",
                "python.md §6",
            ),
        }
        for candidate in moved:
            expected_evidence, base_section = expected_moved[candidate["path"]]
            self.assertEqual(candidate["symbol"], "destination")
            self.assertEqual(candidate["change_tag"], "moved-defense")
            self.assertEqual(candidate["evidence"], expected_evidence)
            self.assertEqual(
                candidate["verify"],
                "Open the origin first: did it guard the same producer, "
                "receiver or exception source? If so, did this callee become "
                "the owner, or is it a relocated hedge (a genuine boundary is "
                "a Counterweight)? If not, verify this defense on its own "
                f"under {base_section}. See python.md §4.",
            )
        self.assertEqual(result["candidates"], moved)

    def test_moved_defense_maps_git_rename_and_requires_signature_loss(self):
        self.init_repo()
        padding = "\n".join(f"# unchanged context {i}" for i in range(40)) + "\n"
        self.put(
            "old.py",
            padding
            + 'class Owner:\n    def original(self, item):\n        return getattr(item, "value", None)\n\n    def destination(self, item):\n        return item.value\n',
        )
        base = self.commit()
        self.git("mv", "old.py", "new.py")
        self.put(
            "new.py",
            padding
            + 'class Owner:\n    def original(self, item):\n        return item.value\n\n    def destination(self, item):\n        return hasattr(item, "value")\n',
        )
        self.assertIn("R", self.git("diff", "-M", "--name-status", base))
        candidates = self.scan("--base", base)["candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["rule"], "PY10a_moved_defense")
        self.assertEqual(candidates[0]["symbol"], "Owner.destination")
        self.assertEqual(candidates[0]["path"], "new.py")
        self.assertIn(
            "Receiver item; parameter item: <unannotated> at line 45; "
            "receiver class unknown; same PY01_reflect_default signature "
            "'value' lost from base old.py:43 (Owner.original) "
            "(signature match only)",
            candidates[0]["evidence"],
        )
        self.put(
            "new.py",
            padding
            + 'class Owner:\n    def original(self, item):\n        return getattr(item, "value", None)\n\n    def destination(self, item):\n        return hasattr(item, "value")\n',
        )
        self.assertFalse(
            [
                c
                for c in self.scan("--base", base)["candidates"]
                if c["rule"] == "PY10a_moved_defense"
            ]
        )

    def test_moved_defense_normalizes_signatures_across_files(self):
        self.init_repo()
        pairs = [
            ('return hasattr(item, "field")', 'return getattr(item, "field", 0)'),
            (
                "try:\n    return item.field\nexcept (KeyError, TypeError):\n    return None",
                "try:\n    return item.field\nexcept (errors.TypeError, errors.KeyError):\n    return {}",
            ),
            ("return item.field or 30", 'return item.get("field") or 40'),
        ]
        for index, (before, _) in enumerate(pairs):
            self.put(
                f"old{index}.py",
                "def original(item):\n" + textwrap.indent(before, "    ") + "\n",
            )
            self.put(
                f"new{index}.py", "def destination(item):\n    return item.field\n"
            )
        base = self.commit()
        for index, (_, after) in enumerate(pairs):
            self.put(f"old{index}.py", "def original(item):\n    return item.field\n")
            self.put(
                f"new{index}.py",
                "def destination(item):\n" + textwrap.indent(after, "    ") + "\n",
            )
        candidates = self.scan("--base", base)["candidates"]
        self.assertEqual(
            [(c["path"], c["rule"]) for c in candidates],
            [
                ("new0.py", "PY10a_moved_defense"),
                ("new1.py", "PY10a_moved_defense"),
                ("new2.py", "PY10a_moved_defense"),
            ],
        )
        for index, (_, after) in enumerate(pairs):
            self.put(
                f"new{index}.py",
                "def destination(item):\n"
                + textwrap.indent(
                    after.replace("field", "other").replace("KeyError", "ValueError"),
                    "    ",
                )
                + "\n",
            )
        self.assertFalse(
            [
                c
                for c in self.scan("--base", base)["candidates"]
                if c["rule"] == "PY10a_moved_defense"
            ]
        )

    def test_moved_defense_skips_signatures_with_more_destinations_than_origins(self):
        self.init_repo()
        guarded = "    try:\n        return item.{0}()\n    except Exception:\n        return None\n"
        plain = "    return item.{0}()\n"

        def module(*functions):
            return "\n\n".join(
                f"def {name}(item):\n" + (guarded if defended else plain).format(call)
                for name, call, defended in functions
            )

        def history(lost, gained):
            self.put(
                "origins.py",
                module(
                    ("first", "load", "first" not in lost),
                    ("second", "store", "second" not in lost),
                ),
            )
            self.put(
                "helpers.py",
                module(
                    ("alpha", "parse", "alpha" in gained),
                    ("beta", "render", "beta" in gained),
                ),
            )
            return self.commit()

        base = history(lost=(), gained=())
        origins = {
            "first": "base origins.py:4 (first)",
            "second": "base origins.py:11 (second)",
        }
        for lost, gained, expected in [
            (("first",), ("alpha",), {"alpha": ["first"]}),
            (("first",), ("alpha", "beta"), {}),
            (
                ("first", "second"),
                ("alpha", "beta"),
                {"alpha": ["first", "second"], "beta": ["first", "second"]},
            ),
        ]:
            with self.subTest(lost=lost, gained=gained):
                tip = history(lost, gained)
                candidates = self.scan("--base", base, "--tip", tip)["candidates"]
                moved = {
                    c["symbol"]: c["evidence"]
                    for c in candidates
                    if c["rule"] == "PY10a_moved_defense"
                }
                self.assertEqual(len(candidates), len(gained))
                self.assertEqual(set(moved), set(expected))
                for symbol, names in expected.items():
                    self.assertIn("Handler catches Exception at line", moved[symbol])
                    self.assertIn(
                        "; same PY02_error_washing signature Exception lost from ",
                        moved[symbol],
                    )
                    self.assertIn("signature match only", moved[symbol])
                    for name in names:
                        self.assertIn(origins[name], moved[symbol])
                self.assertEqual(
                    {
                        c["symbol"]
                        for c in candidates
                        if c["rule"] == "PY02_error_washing"
                        and c["change_tag"] == "changed"
                    },
                    set(gained) - set(expected),
                )

    def test_moved_defense_destination_must_intersect_added_lines(self):
        self.init_repo()
        self.put(
            "app.py",
            'def original(item):\n    return getattr(item, "value", None)\n\n\n'
            'def old_name(item):\n    return getattr(item, "value", None)\n',
        )
        base = self.commit()
        for body, expected in [
            ('    return getattr(item, "value", None)\n', []),
            (
                '    return getattr(item, "value", 0)\n',
                [("PY10a_moved_defense", "moved-defense", [6, 6])],
            ),
            (
                '    first = getattr(item, "value", 0)\n'
                '    return getattr(other, "value", 1)\n',
                [
                    ("PY01_reflect_default", "changed", [6, 6]),
                    ("PY10a_moved_defense", "moved-defense", [7, 7]),
                ],
            ),
        ]:
            with self.subTest(body=body):
                self.put(
                    "app.py",
                    "def original(item):\n    return item.value\n\n\n"
                    "def new_name(item, other=None):\n" + body,
                )
                tip = self.commit()
                candidates = self.scan("--base", base, "--tip", tip)["candidates"]
                self.assertEqual(
                    [(c["rule"], c["change_tag"], c["span"]) for c in candidates],
                    expected,
                )
                for candidate in candidates:
                    if candidate["rule"] == "PY10a_moved_defense":
                        self.assertIn(
                            "; same PY01_reflect_default signature 'value' "
                            "lost from base app.py:2 (original)",
                            candidate["evidence"],
                        )

    def test_test_smells_legacy_raw_text_getter_mutation_and_self_patch(self):
        cases = [
            ("legacy", "obj._legacy_read()", True),
            ("public", "obj.read()", False),
            (
                "source",
                'text = Path("app.py").read_text()\nassert "class" in text',
                True,
            ),
            (
                "document",
                'path = Path("README.md")\ntext = path.read_text()\nself.assertTrue(text.startswith("Title"))',
                True,
            ),
            (
                "open",
                'text = open("app.py").read()\nassert text.find("class") >= 0',
                True,
            ),
            ("assert_in", 'self.assertIn("class", open("app.py").read())', True),
            (
                "path_chain",
                'assert "class" in Path(__file__).with_name("app.py").read_text()',
                True,
            ),
            (
                "open_handle",
                'with open("app.py") as handle:\n    assert "class" in handle.read()',
                True,
            ),
            (
                "rebound",
                'text = Path("app.py").read_text()\ntext = render()\nassert "class" in text',
                False,
            ),
            (
                "find_data",
                'text = Path("data.txt").read_text()\nassert text.find("class") >= 0',
                False,
            ),
            (
                "prefix_data",
                'text = Path("data.txt").read_text()\nself.assertTrue(text.startswith("Title"))',
                False,
            ),
            ("raw_read_without_path", 'assert "class" in response.read_text()', False),
            (
                "nested_scope",
                'def helper():\n    text = Path("app.py").read_text()\nassert "class" in text',
                False,
            ),
            (
                "data",
                'text = Path("data.txt").read_text()\nassert "class" in text',
                False,
            ),
            ("behavior", 'text = render()\nassert "class" in text', False),
            (
                "not_assert",
                'text = Path("app.py").read_text()\nfound = "class" in text',
                False,
            ),
            ("getter", "r = obj.items()\nr.append(1)\nobj.items()", True),
            ("item", "r = obj.items()\nr[0] = 1\nobj.items()", True),
            ("extend", "r = obj.items()\nr.extend([1])\nobj.items()", True),
            ("pop", "r = obj.items()\nr.pop()\nobj.items()", True),
            ("clear", "r = obj.items()\nr.clear()\nobj.items()", True),
            (
                "rebound_getter",
                "r = obj.items()\nr = []\nr.append(1)\nobj.items()",
                False,
            ),
            ("no_mutation", "r = obj.items()\nobj.items()", False),
            ("other_getter", "r = obj.items()\nr.clear()\nother.items()", False),
            ("wrong_order", "r = obj.items()\nobj.items()\nr.pop()", False),
            ("patch", 'with mock.patch("app.execute"):\n    execute()', True),
            (
                "patch_object",
                'with patch.object(app, "execute"):\n    app.execute()',
                True,
            ),
            (
                "monkeypatch",
                'monkeypatch.setattr(app, "execute", replacement)\napp.execute()',
                True,
            ),
            (
                "monkeypatch_string",
                'monkeypatch.setattr("app.execute", replacement)\nexecute()',
                True,
            ),
            ("other_patch", 'with patch("app.dependency"):\n    execute()', False),
            ("inside_patch", 'patch("app.execute", execute())', False),
            (
                "patch_object_other",
                'with patch.object(app, "other"):\n    app.execute()',
                False,
            ),
            (
                "monkeypatch_other",
                'monkeypatch.setattr("app.other", replacement)\nexecute()',
                False,
            ),
        ]
        for label, body, expected in cases:
            with self.subTest(label=label):
                self.put(
                    "tests/test_case.py",
                    "def test_case(self, obj, other, monkeypatch):\n"
                    + textwrap.indent(body, "    ")
                    + "\n",
                )
                candidates = self.scan("--paths", "tests/test_case.py")["candidates"]
                self.assertEqual(len(candidates), int(expected), candidates)
                if expected:
                    self.assertEqual(candidates[0]["rule"], "PY10b_test_smell")
                    self.assertEqual(candidates[0]["symbol"], "test_case")
                    self.assertIn("Counterweight", candidates[0]["verify"])

    def test_test_smell_verify_points_to_its_subprobe_catalogue(self):
        for body, pointer in [
            ("obj._legacy_read()", "tests.md §4"),
            ('assert "class" in Path("app.py").read_text()', "tests.md §6"),
            ("r = obj.items()\nr.append(1)\nobj.items()", "tests.md §2"),
            ('with patch("app.execute"):\n    execute()', "tests.md §5"),
        ]:
            with self.subTest(pointer=pointer):
                self.put(
                    "tests/test_case.py",
                    "def test_case():\n" + textwrap.indent(body, "    ") + "\n",
                )
                candidates = self.scan("--paths", "tests/test_case.py")["candidates"]
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0]["rule"], "PY10b_test_smell")
                self.assertIn(pointer, candidates[0]["verify"])

    def test_getter_recall_is_checked_before_rebinding_its_result(self):
        for replacement, expected in [
            ("result = obj.items()", True),
            ("result: list = obj.items()", True),
            ("result = list(obj.items())", True),
            ("result = other.items()", False),
            ("result = []", False),
        ]:
            with self.subTest(replacement=replacement):
                self.put(
                    "tests/test_items.py",
                    "def test_items():\n"
                    "    result = obj.items()\n"
                    "    result.append(1)\n"
                    f"    {replacement}\n",
                )
                candidates = self.scan("--paths", "tests/test_items.py")["candidates"]
                self.assertEqual(len(candidates), int(expected), candidates)
                if expected:
                    self.assertEqual(candidates[0]["rule"], "PY10b_test_smell")
                    self.assertEqual(candidates[0]["symbol"], "test_items")
                    self.assertEqual(candidates[0]["span"], [2, 4])

    def test_self_patch_counts_calls_in_other_patch_arguments(self):
        for source, span in [
            (
                "def test_patch(monkeypatch):\n"
                '    with patch("app.execute"):\n'
                '        monkeypatch.setattr("app.other", execute())\n',
                [2, 3],
            ),
            (
                '@patch("app.execute")\n'
                "def test_patch():\n"
                '    patch("app.other", execute()).start()\n',
                [1, 3],
            ),
        ]:
            with self.subTest(source=source):
                self.put("tests/test_patch.py", source)
                candidates = self.scan("--paths", "tests/test_patch.py")["candidates"]
                self.assertEqual(
                    [(c["rule"], c["symbol"], c["span"]) for c in candidates],
                    [("PY10b_test_smell", "test_patch", span)],
                )

    def test_self_patch_calls_require_an_active_window(self):
        for body, span in [
            ('execute()\nmonkeypatch.setattr("app.execute", replacement)', None),
            ('patch("app.execute").start()\nexecute()', [2, 3]),
            ('execute()\npatch("app.execute").start()', None),
            ('patch("app.execute").start()\npatch.stopall()\nexecute()', None),
            ('patch("app.execute").start()\ncheck(patch.stopall())\nexecute()', None),
            ('patch("app.execute").start(); execute(); patch.stopall()', [2, 2]),
            ('patch("app.execute").start(); patch.stopall(); execute()', None),
            (
                'monkeypatch.setattr("app.execute", replacement)\nmonkeypatch.undo()\nexecute()',
                None,
            ),
            (
                'monkeypatch.setattr("app.execute", replacement)\ncheck(monkeypatch.undo())\nexecute()',
                None,
            ),
            (
                'monkeypatch.setattr("app.execute", reset(monkeypatch.undo()))\nexecute()',
                [2, 3],
            ),
            ('execute()\nwith patch("app.execute"):\n    pass', None),
            ('patch("app.execute")\nexecute()', None),
            ('monkeypatch.setattr("app.execute", execute())', None),
            ('patch("app.execute", execute()).start()', None),
            (
                'if enabled:\n    monkeypatch.setattr("app.execute", replacement)\nexecute()',
                [3, 4],
            ),
        ]:
            with self.subTest(body=body):
                self.put(
                    "tests/test_patch.py",
                    "def test_patch(monkeypatch):\n"
                    + textwrap.indent(body, "    ")
                    + "\n",
                )
                candidates = self.scan("--paths", "tests/test_patch.py")["candidates"]
                self.assertEqual(
                    [(c["rule"], c["symbol"], c["span"]) for c in candidates],
                    [("PY10b_test_smell", "test_patch", span)] if span else [],
                )

    def test_self_patch_tracks_bound_patchers_until_their_stop(self):
        for body, span in [
            ('p = patch("app.execute")\np.start()\nexecute()\np.stop()', [2, 4]),
            ('p: Patcher = patch.object(app, "execute")\np.start()\nexecute()', [2, 4]),
            ('p = patch("app.execute")\nexecute()\np.start()', None),
            ('p = patch("app.execute")\np.start()\np.stop()\nexecute()', None),
            (
                'p = patch("app.execute")\np.start()\nassert p.stop() is None\nexecute()',
                None,
            ),
            ('p = patch("app.execute")\np.start()\npatch.stopall()\nexecute()', None),
            (
                'p = patch("app.execute")\np.start()\nmonkeypatch.undo()\nexecute()',
                [2, 5],
            ),
            (
                'monkeypatch.setattr("app.execute", replacement)\npatch.stopall()\nexecute()',
                [2, 4],
            ),
            (
                'p = patch("app.execute")\nother = patch("app.other")\np.start()\nother.stop()\nexecute()',
                [2, 6],
            ),
            (
                'p = patch("app.execute")\np.start()\np = patch("app.other")\np.stop()\nexecute()',
                [2, 6],
            ),
            ('p = patch("app.execute")\np = replacement\np.start()\nexecute()', None),
            (
                'p = patch("app.execute")\n(p := replacement)\np.start()\nexecute()',
                None,
            ),
            (
                'p = patch("app.execute")\np, other = replacements\np.start()\nexecute()',
                None,
            ),
            ('mocked = patch("app.execute").start()\nmocked.stop()\nexecute()', [2, 4]),
            (
                'p = patch("app.execute")\np.start()\np.stop()\np.start()\nexecute()',
                [2, 6],
            ),
        ]:
            with self.subTest(body=body):
                self.put(
                    "tests/test_patch.py",
                    "def test_patch(monkeypatch):\n"
                    + textwrap.indent(body, "    ")
                    + "\n",
                )
                candidates = self.scan("--paths", "tests/test_patch.py")["candidates"]
                self.assertEqual(
                    [(c["rule"], c["symbol"], c["span"]) for c in candidates],
                    [("PY10b_test_smell", "test_patch", span)] if span else [],
                )

    def test_self_patch_scoped_windows_recognize_the_patch_family(self):
        cases = [
            (f"@{patch_call}\ndef test_patch():\n    execute()\n", [1, 3])
            for patch_call in [
                'patch("app.execute")',
                'mock.patch("app.execute")',
                'unittest.mock.patch("app.execute")',
                'patch.object(app, "execute")',
                'mock.patch.object(app, "execute")',
                'unittest.mock.patch.object(app, "execute")',
            ]
        ]
        cases += [
            (
                '@decorate(patch("app.execute"))\ndef test_patch():\n    execute()\n',
                None,
            ),
            (
                '@patch("app.execute")\ndef test_patch():\n    def helper():\n        execute()\n',
                None,
            ),
            (
                'def test_patch():\n    with patch("app.execute"):\n        execute()\n',
                [2, 3],
            ),
            (
                'def test_patch():\n    with patch("app.execute"):\n        pass\n    execute()\n',
                None,
            ),
            (
                'def test_patch():\n    with patch("app.execute"), other(execute()):\n        pass\n',
                None,
            ),
        ]
        for source, span in cases:
            with self.subTest(source=source):
                self.put("tests/test_patch.py", source)
                candidates = self.scan("--paths", "tests/test_patch.py")["candidates"]
                self.assertEqual(
                    [(c["rule"], c["symbol"], c["span"]) for c in candidates],
                    [("PY10b_test_smell", "test_patch", span)] if span else [],
                )

    def test_starting_a_bound_patcher_makes_the_sut_call_diff_eligible(self):
        self.init_repo()
        for previous, spans in [
            ("", [[2, 4]]),
            ("    p.start()\n    execute()\n    p.stop()\n", [[2, 4], [2, 7]]),
        ]:
            with self.subTest(previous=previous):
                prefix = 'def test_patch():\n    p = patch("app.execute")\n' + previous
                self.put("tests/test_patch.py", prefix + "    execute()\n")
                base = self.commit()
                self.put(
                    "tests/test_patch.py", prefix + "    p.start()\n    execute()\n"
                )
                candidates = self.scan("--base", base)["candidates"]
                self.assertEqual(
                    [
                        (c["rule"], c["symbol"], c["span"], c["change_tag"])
                        for c in candidates
                    ],
                    [("PY10b_test_smell", "test_patch", spans[-1], "changed")],
                )
                candidates = self.scan("--paths", "tests/test_patch.py")["candidates"]
                self.assertEqual(
                    [(c["rule"], c["symbol"], c["span"]) for c in candidates],
                    [("PY10b_test_smell", "test_patch", span) for span in spans],
                )

    def test_default_hook_identity_checks_are_production_only(self):
        for expression, expected in [
            ("fn is not _DEFAULT_FN", True),
            ("module._DEFAULT_FN != fn", True),
            ('fn is not _DEFAULT_FN["hook"]', False),
            ('module._DEFAULT_FN["hook"] != fn', False),
            ("fn is _DEFAULT_FN", False),
            ("fn == _DEFAULT_FN", False),
            ("fn != DEFAULT_FN", False),
            ("fn != _DEFAULT_FN != other", False),
        ]:
            with self.subTest(expression=expression):
                self.put("app.py", f"if {expression}:\n    execute()\n")
                self.put("tests/test_app.py", f"assert {expression}\n")
                candidates = self.scan("--paths", ".")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    self.assertEqual(candidates[0]["rule"], "PY10b_test_smell")
                    self.assertEqual(candidates[0]["path"], "app.py")
                    self.assertEqual(candidates[0]["symbol"], "<module>")
                    self.assertEqual(candidates[0]["span"], [1, 1])

    def test_test_smells_scope_decorators_module_reads_and_diff_spans(self):
        self.init_repo()
        original = (
            'TEXT = Path("app.py").read_text()\n'
            '@patch("app.execute")\ndef test_patched():\n    execute()\n'
            'def test_read():\n    assert "class" not in TEXT\n'
            'def test_unrelated():\n    with patch("app.other"):\n        pass\n'
            "def test_other():\n    other()\n"
            "def test_getter():\n    r = obj.items()\n    r.extend([1])\n"
        )
        self.put("tests/test_app.py", original)
        base = self.commit()
        audit = self.scan("--paths", ".")["candidates"]
        self.assertEqual({c["symbol"] for c in audit}, {"test_patched", "test_read"})
        self.put("tests/test_app.py", original + "    obj.items()\n")
        changed = self.scan("--base", base)["candidates"]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["symbol"], "test_getter")
        self.assertEqual(changed[0]["change_tag"], "changed")

    def test_module_raw_text_bindings_follow_nested_statements_but_not_scopes(self):
        for setup, expected in [
            ('with open("app.py") as handle:\n    TEXT = handle.read()', True),
            ('if enabled:\n    TEXT = Path("app.py").read_text()', True),
            (
                'try:\n    TEXT = Path("app.py").read_text()\nfinally:\n    close()',
                True,
            ),
            ('def helper():\n    TEXT = Path("app.py").read_text()', False),
            ('class Fixture:\n    TEXT = Path("app.py").read_text()', False),
            ('with open("data.txt") as handle:\n    TEXT = handle.read()', False),
            (
                'if enabled:\n    TEXT = Path("app.py").read_text()\nTEXT = render()',
                False,
            ),
        ]:
            with self.subTest(setup=setup):
                self.put(
                    "tests/test_read.py",
                    setup + '\ndef test_read():\n    assert "class" in TEXT\n',
                )
                candidates = self.scan("--paths", "tests/test_read.py")["candidates"]
                self.assertEqual(len(candidates), int(expected), candidates)
                if expected:
                    self.assertEqual(candidates[0]["rule"], "PY10b_test_smell")
                    self.assertEqual(candidates[0]["symbol"], "test_read")
                    self.assertIn(
                        "raw source/documentation text", candidates[0]["evidence"]
                    )

    def test_cross_test_references_aliases_ambiguity_and_production_escape(self):
        self.init_repo()
        self.put(
            "app.py",
            "def lonely():\n    return lonely()\ndef shared():\n    return 1\n",
        )
        self.put("other.py", "def shared():\n    return 2\n")
        self.put(
            "tests/test_app.py", "from app import lonely as local\nlocal()\nshared()\n"
        )
        result = self.scan("--paths", "app.py")
        candidates = [
            c
            for c in result["candidates"]
            if c["rule"] == "PY10c_cross_test_reachability"
        ]
        self.assertEqual(len(candidates), 3)
        self.assertEqual({c["change_tag"] for c in candidates}, {"cross-test"})
        self.assertEqual(result["summary"]["cross_checked_files"], 3)
        for candidate in candidates:
            self.assertIn("prod calls: 0", candidate["evidence"])
            self.assertIn("test calls: 1", candidate["evidence"])
            if candidate["symbol"] == "shared":
                self.assertIn("ambiguous_defs: 2", candidate["evidence"])
        for reference in (
            "lonely()",
            "callbacks = [lonely]",
            "from app import lonely as local\nlocal()",
        ):
            with self.subTest(reference=reference):
                self.put("caller.py", reference + "\nshared()\n")
                self.assertFalse(
                    [
                        c
                        for c in self.scan("--paths", "app.py")["candidates"]
                        if c["rule"] == "PY10c_cross_test_reachability"
                    ]
                )

    def test_cross_own_body_suppresses_only_self_recursion(self):
        self.init_repo()
        self.put("tests/test_app.py", "Runner().execute(None)\n")
        for method, flagged in [
            ("def execute(self, other):\n        return other.execute(other)", False),
            ("def execute(self, other):\n        return super().execute(other)", False),
            ("def execute(self, other):\n        return self.execute(other)", True),
            (
                "@classmethod\n    def execute(cls, other):\n        return cls.execute(other)",
                True,
            ),
        ]:
            with self.subTest(method=method):
                self.put("app.py", f"class Runner(Base):\n    {method}\n")
                candidates = self.scan("--paths", "app.py")["candidates"]
                self.assertEqual(
                    [(c["symbol"], c["change_tag"]) for c in candidates],
                    [("Runner.execute", "cross-test")] if flagged else [],
                )

    def test_cross_super_delegation_reaches_without_binding_parameters(self):
        self.init_repo()
        self.put(
            "app.py",
            "class Runner(Base):\n"
            "    def execute(self, other, retries=3):\n"
            "        return super().execute(other, retries=5)\n",
        )
        for caller, flagged in [("", False), ("Runner().execute(1)", True)]:
            with self.subTest(caller=caller):
                self.put("caller.py", caller + "\n")
                candidates = self.scan("--paths", "app.py")["candidates"]
                self.assertEqual(
                    [c["code"] for c in candidates], ["3"] if flagged else []
                )

    def test_cross_default_counts_recursive_bindings(self):
        self.init_repo()
        function = (
            "def walk(node, depth=0):\n"
            "    for child in node.children:\n"
            "        {}\n"
            "    return depth\n"
        )
        method = (
            "class Tree:\n"
            "    def walk(self, node, depth=0):\n"
            "        for child in node.children:\n"
            "            {}\n"
            "        return depth\n"
        )
        for definition, caller, recursion, flagged in [
            (function, "walk(tree)", "walk(child, depth=depth + 1)", False),
            (function, "walk(tree)", "walk(child)", True),
            (method, "Tree().walk(tree)", "self.walk(child, depth=depth + 1)", False),
            (method, "Tree().walk(tree)", "self.walk(child)", True),
            # A bare name inside a method resolves outside the class, never to it.
            (method, "Tree().walk(tree)", "walk(child, depth=depth + 1)", True),
        ]:
            with self.subTest(caller=caller, recursion=recursion):
                self.put("app.py", definition.format(recursion))
                self.put("caller.py", caller + "\n")
                candidates = self.scan("--paths", "app.py")["candidates"]
                self.assertEqual(
                    [c["code"] for c in candidates], ["0"] if flagged else []
                )

    def test_cross_default_forwarded_recursive_parameters(self):
        self.init_repo()
        fixture = (
            "def render(node, width, indent=2):\n"
            "    step = 4\n"
            "    for child in node.children:\n"
            "        {}\n"
            "    return width + step + indent\n"
        )
        for caller, body, flagged in [
            ("render(tree, 8)", "render(child, width, indent)", True),
            ("render(tree, 8)", "render(child, width, indent=indent)", True),
            ("render(tree, 8)", "render(child, width, indent + 1)", False),
            ("render(tree, 8)", "render(child, width, step)", False),
            ("render(tree, 8)", "render(child, width, indent=width)", False),
            (
                "render(tree, 8)",
                "indent = indent * 2\n        render(child, width, indent)",
                False,
            ),
            (
                "render(tree, 8)",
                "indent += 2\n        render(child, width, indent)",
                False,
            ),
            (
                "render(tree, 8)",
                "for indent in node.indents:\n            render(child, width, indent)",
                False,
            ),
            (
                "render(tree, 8)",
                "with node.lock as indent:\n            render(child, width, indent)",
                False,
            ),
            (
                "render(tree, 8)",
                "if (indent := step):\n            render(child, width, indent)",
                False,
            ),
            (
                "def other(tree):\n    indent = 4\n    return render(tree, 8, indent)",
                "render(child, width, indent)",
                False,
            ),
        ]:
            with self.subTest(caller=caller, body=body):
                self.put("app.py", fixture.format(body))
                self.put("caller.py", caller + "\n")
                candidates = self.scan("--paths", "app.py")["candidates"]
                self.assertEqual(
                    [c["code"] for c in candidates], ["2"] if flagged else []
                )
        self.put("caller.py", "Tree().render(tree)\n")
        for header, call in [
            (
                "def render(self, node, indent=2, depth=0):",
                "self.render(child, indent, depth + 1)",
            ),
            (
                "@classmethod\n    def render(cls, node, indent=2, depth=0):",
                "cls.render(child, indent=indent, depth=depth + 1)",
            ),
        ]:
            with self.subTest(call=call):
                self.put(
                    "app.py",
                    f"class Tree:\n    {header}\n        for child in node.children:\n            {call}\n        return indent + depth\n",
                )
                candidates = self.scan("--paths", "app.py")["candidates"]
                self.assertEqual([c["code"] for c in candidates], ["2"])

    def test_rebound_import_aliases_are_escapes_instead_of_resolved_calls(self):
        self.init_repo()
        self.put("app.py", "def configure(flag=False):\n    return flag\n")
        self.put("tests/test_app.py", "configure()\n")
        for binding in ("from other import unrelated as local", "local = unrelated"):
            for nested in (False, True):
                with self.subTest(binding=binding, nested=nested):
                    caller = (
                        f"from app import configure as local\n{binding}\nlocal(True)\n"
                    )
                    if nested:
                        caller = "def caller():\n" + textwrap.indent(caller, "    ")
                    self.put("caller.py", caller)
                    self.assertEqual(self.scan("--paths", "app.py")["candidates"], [])
                    as_test = self.scan(
                        "--paths", "app.py", "--test-glob", "caller.py"
                    )["candidates"]
                    self.assertEqual(len(as_test), 1)
                    self.assertEqual(as_test[0]["change_tag"], "cross-test")
                    self.assertIn(
                        "test calls: 1, test non-call refs: 1", as_test[0]["evidence"]
                    )
        self.put("caller.py", "from app import configure as local\nlocal(True)\n")
        resolved = self.scan("--paths", "app.py")["candidates"]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["change_tag"], "path-audit")
        self.assertIn("uniform value: True", resolved[0]["evidence"])

    def test_cross_default_effective_arguments_and_escape_suppression(self):
        self.init_repo()
        cases = [
            (
                "omitted",
                "def configure(flag=False):\n    return flag\n",
                "configure()\nconfigure()",
                "<DEFAULT>",
            ),
            (
                "constant",
                "def configure(flag=False):\n    return flag\n",
                "configure(True)\nconfigure(flag=True)",
                "True",
            ),
            (
                "mixed",
                "def configure(flag=False):\n    return flag\n",
                "configure()\nconfigure(False)",
                None,
            ),
            (
                "different",
                "def configure(flag=False):\n    return flag\n",
                "configure(True)\nconfigure(False)",
                None,
            ),
            (
                "dynamic",
                "def configure(flag=False):\n    return flag\n",
                "configure(value)",
                None,
            ),
            (
                "callback",
                "def configure(flag=False):\n    return flag\n",
                "configure()\ncallbacks = [configure]",
                None,
            ),
            (
                "star",
                "def configure(flag=False):\n    return flag\n",
                "configure()\nconfigure(*args)",
                None,
            ),
            (
                "kwargs",
                "def configure(flag=False):\n    return flag\n",
                "configure()\nconfigure(**options)",
                None,
            ),
            (
                "export",
                '__all__ = ["configure"]\ndef configure(flag=False):\n    return flag\n',
                "configure()",
                None,
            ),
            (
                "ambiguous",
                "def configure(flag=False):\n    return flag\nclass Other:\n    def configure(self, flag=False):\n        return flag\n",
                "configure()",
                None,
            ),
            (
                "method",
                "class Runner:\n    def configure(self, value, flag=False):\n        return value, flag\n",
                "obj.configure(data)\nother.configure(data)",
                "<DEFAULT>",
            ),
            (
                "method_constant",
                "class Runner:\n    def configure(self, value, flag=False):\n        return value, flag\n",
                "obj.configure(data, True)\nother.configure(data, flag=True)",
                "True",
            ),
            (
                "method_mixed",
                "class Runner:\n    def configure(self, value, flag=False):\n        return value, flag\n",
                "obj.configure(data)\nobj.configure(data, False)",
                None,
            ),
            (
                "class_method",
                "class Runner:\n    @classmethod\n    def configure(cls, flag=False):\n        return flag\n",
                "Runner.configure(True)",
                "True",
            ),
            (
                "static_method",
                "class Runner:\n    @staticmethod\n    def configure(self=False):\n        return self\n",
                "Runner.configure(True)",
                "True",
            ),
            (
                "bare_method",
                "class Runner:\n    def configure(self, flag=False):\n        return flag\n",
                "configure(receiver, True)",
                "True",
            ),
            (
                "keyword_only",
                "def configure(value, /, *, flag=False):\n    return value, flag\n",
                "configure(data, flag=True)",
                "True",
            ),
            (
                "alias",
                "def configure(flag=False):\n    return flag\n",
                "from app import configure as local\nlocal(True)",
                "True",
            ),
            (
                "local_alias",
                "def configure(flag=False):\n    return flag\n",
                "def caller():\n    from app import configure as local\n    local(True)",
                "True",
            ),
            (
                "local_alias_callback",
                "def configure(flag=False):\n    return flag\n",
                "def caller():\n    from app import configure as local\n    local(True)\n    callbacks = [local]",
                None,
            ),
            (
                "annotated_export",
                "__all__: list[str]\ndef configure(flag=False):\n    return flag\n",
                "configure()",
                "<DEFAULT>",
            ),
            (
                "unresolved",
                "def configure(required, flag=False):\n    return required, flag\n",
                "configure()",
                None,
            ),
            (
                "import_only",
                "def configure(flag=False):\n    return flag\n",
                "from app import configure",
                None,
            ),
        ]
        for label, source, calls, uniform in cases:
            with self.subTest(label=label):
                self.put("app.py", source)
                self.put("caller.py", calls + "\n")
                candidates = [
                    c
                    for c in self.scan("--paths", "app.py")["candidates"]
                    if c["rule"] == "PY10c_cross_test_reachability"
                ]
                self.assertEqual(len(candidates), int(uniform is not None), candidates)
                if uniform is not None:
                    self.assertEqual(candidates[0]["change_tag"], "path-audit")
                    self.assertIn(
                        f"uniform value: {uniform}", candidates[0]["evidence"]
                    )
                    self.assertIn("prod call sites", candidates[0]["evidence"])
                    self.assertEqual(candidates[0]["code"], "False")
                    self.assertIn("Counterweight", candidates[0]["verify"])

    def test_cross_default_keeps_single_uniform_parameter_format(self):
        self.init_repo()
        for signature, body, calls in [
            (
                "flag=False",
                "flag",
                "configure(True)\nconfigure(flag=True)\n",
            ),
            (
                "flag=False, varying=None",
                "flag, varying",
                "configure(True, 1)\nconfigure(flag=True, varying=2)\n",
            ),
        ]:
            with self.subTest(signature=signature):
                self.put("app.py", f"def configure({signature}):\n    return {body}\n")
                self.put("caller.py", calls)
                self.assertEqual(
                    self.scan("--paths", "app.py")["candidates"],
                    [
                        {
                            "id": "C01",
                            "path": "app.py",
                            "symbol": "configure",
                            "rule": "PY10c_cross_test_reachability",
                            "change_tag": "path-audit",
                            "span": [1, 1],
                            "code": "False",
                            "evidence": "Parameter 'flag': 2 resolved prod call sites; "
                            "uniform value: True",
                            "verify": "Does every immediate production binding, recursion included, "
                            "fix this value, so the override is dead while the default's policy stays? "
                            "Is it an export or extension contract (Counterweight)? See tests.md §4.",
                        }
                    ],
                )

    def test_cross_default_bundles_only_uniform_parameters_in_declaration_order(self):
        self.init_repo()
        self.put(
            "app.py",
            """\
            def configure(
                zulu=False,
                /,
                varying=None,
                *,
                alpha="policy",
            ):
                return zulu, varying, alpha
            """,
        )
        self.put("caller.py", "configure(True, 1)\nconfigure(True, 2)\n")
        candidates = self.scan("--paths", "app.py")["candidates"]
        self.assertEqual(
            candidates,
            [
                {
                    "id": "C01",
                    "path": "app.py",
                    "symbol": "configure",
                    "rule": "PY10c_cross_test_reachability",
                    "change_tag": "path-audit",
                    "span": [2, 6],
                    "code": 'False; "policy"',
                    "evidence": "Parameters 'zulu' (uniform value: True), "
                    "'alpha' (uniform value: <DEFAULT>): 2 resolved prod call sites",
                    "verify": "Does every immediate production binding, recursion included, "
                    "fix each value, so the override is dead while the default's policy stays? "
                    "Is it an export or extension contract (Counterweight)? See tests.md §4.",
                }
            ],
        )

    def test_cross_default_bundle_keeps_diff_lost_calls_and_external_base_clauses(self):
        self.init_repo()
        source = """\
            from discord import ui
            class Panel(ui.View):
                def configure(
                    self,
                    zulu=False,
                    varying=None,
                    *,
                    alpha="policy",
                ):
                    return zulu, varying, alpha
            """
        self.put("app.py", source)
        calls = "Panel().configure(True, 1)\nPanel().configure(True, 2)\n"
        self.put("caller.py", calls + "Panel().configure(False, 3, alpha='other')\n")
        base = self.commit()
        self.put("caller.py", calls)
        for changed in (False, True):
            with self.subTest(changed=changed):
                if changed:
                    self.put(
                        "app.py",
                        source.replace(
                            "return zulu, varying, alpha", "return alpha, varying, zulu"
                        ),
                    )
                candidates = self.scan("--base", base)["candidates"]
                self.assertEqual(len(candidates), 1)
                candidate = candidates[0]
                self.assertEqual(candidate["rule"], "PY10c_cross_test_reachability")
                self.assertEqual(candidate["symbol"], "Panel.configure")
                self.assertEqual(candidate["path"], "app.py")
                self.assertEqual(candidate["change_tag"], "changed")
                self.assertEqual(candidate["span"], [5, 8])
                self.assertEqual(candidate["code"], 'False; "policy"')
                unchanged = (
                    ""
                    if changed
                    else "; definition unchanged; triggered by deleted call"
                )
                self.assertEqual(
                    candidate["evidence"],
                    "Parameters 'zulu' (uniform value: True), "
                    "'alpha' (uniform value: <DEFAULT>): 2 resolved prod call sites"
                    "; lost call(s): base caller.py:3 (prod)"
                    f"{unchanged}; enclosing class bases: ui.View (not defined in repo)",
                )
                self.assertIn("fix each value", candidate["verify"])

    def test_module_all_mutations_suppress_constant_default_candidates(self):
        self.init_repo()
        definitions = (
            "def configure(flag=False):\n    return flag\n"
            "def sibling(flag=False):\n    return flag\n"
        )
        self.put("caller.py", "configure(True)\nsibling(True)\n")
        for module, exports, exported in [
            ("app.py", '__all__ = []\n__all__ += ["configure"]', True),
            ("app.py", '__all__.append("configure")', True),
            ("app.py", '__all__.extend(["configure"])', True),
            ("app.py", '__all__[0] = "configure"', True),
            ("app.py", 'if exporting:\n    __all__.append("configure")', True),
            ("app.py", 'declare(__all__, "configure")', True),
            ("app.py", '__all__.append("other")', False),
            ("app.py", '__all__ = []\nPUBLIC = ["configure"]', False),
            ("app.py", 'PUBLIC = ["configure"]\n__all__ = PUBLIC', False),
            ("app.py", 'def local_exports():\n    __all__.append("configure")', False),
            ("app.py", 'class Fixture:\n    __all__.append("configure")', False),
            # `exports.py` never imports `configure`, so its `__all__` exports nothing.
            ("exports.py", '__all__.append("configure")', False),
        ]:
            with self.subTest(module=module, exports=exports):
                own = module == "app.py"
                self.put("app.py", definitions + (exports + "\n" if own else ""))
                self.put("exports.py", "" if own else exports + "\n")
                candidates = self.scan("--paths", "app.py")["candidates"]
                self.assertEqual(
                    [(c["symbol"], c["code"]) for c in candidates],
                    [("sibling", "False")]
                    if exported
                    else [("configure", "False"), ("sibling", "False")],
                )

    def test_cross_exported_function_skips_reach_but_not_unexported_namesake(self):
        self.init_repo()
        self.put("other.py", "def helper():\n    return 2\n")
        self.put("tests/test_app.py", "helper()\n")
        for exports, flagged in [
            ('__all__ = ["helper"]\n', ["other.py"]),
            ("", ["app.py", "other.py"]),
        ]:
            with self.subTest(exports=exports):
                self.put("app.py", exports + "def helper():\n    return 1\n")
                candidates = self.scan("--paths", "app.py", "other.py")["candidates"]
                self.assertEqual(
                    [(c["path"], c["symbol"], c["change_tag"]) for c in candidates],
                    [(path, "helper", "cross-test") for path in flagged],
                )

    def test_cross_static_reexport_makes_definition_public(self):
        self.init_repo()
        self.put("pkg/core.py", "def helper():\n    return 1\n")
        self.put("tests/test_core.py", "from pkg import helper\nhelper()\n")
        for package, exported in [
            ('from .core import helper\n__all__ = ["helper"]', True),
            ('from pkg.core import helper\n__all__ = ["helper"]', True),
            ('from .core import helper as public\n__all__ = ["public"]', True),
            ('from .core import *\n__all__ = ["helper"]', False),
            ('__all__ = ["helper"]', False),
            ("from .core import helper", False),
        ]:
            with self.subTest(package=package):
                self.put("pkg/__init__.py", package + "\n")
                candidates = self.scan("--paths", "pkg/core.py")["candidates"]
                self.assertEqual(
                    [(c["symbol"], c["change_tag"]) for c in candidates],
                    [] if exported else [("helper", "cross-test")],
                )

    def test_cross_exported_class_public_methods_skip_both_arms(self):
        self.init_repo()
        self.put(
            "pkg/_client.py",
            "class Client:\n"
            "    def send(self, request, retries=3):\n"
            "        return request, retries\n"
            "    def _prepare(self, request, retries=3):\n"
            "        return request, retries\n"
            "    def fetch(self):\n"
            "        return 1\n"
            "    def _fetch_raw(self):\n"
            "        return 1\n",
        )
        self.put("caller.py", "Client().send(1)\nClient()._prepare(1)\n")
        self.put("tests/test_client.py", "Client().fetch()\nClient()._fetch_raw()\n")
        private = [
            ("Client._prepare", "path-audit", "3"),
            ("Client._fetch_raw", "cross-test", "def _fetch_raw(self): return 1"),
        ]
        public = [
            ("Client.send", "path-audit", "3"),
            ("Client.fetch", "cross-test", "def fetch(self): return 1"),
        ]
        # The package module mentions no method name; `__all__` admits it.
        for package, expected in [
            ('from ._client import Client\n__all__ = ["Client"]', private),
            ("from ._client import Client", sorted(public + private)),
        ]:
            with self.subTest(package=package):
                self.put("pkg/__init__.py", package + "\n")
                candidates = self.scan("--paths", "pkg/_client.py")["candidates"]
                self.assertEqual(
                    sorted(
                        (c["symbol"], c["change_tag"], c["code"]) for c in candidates
                    ),
                    sorted(expected),
                )

    def test_cross_evidence_labels_aggregates_and_external_bases(self):
        self.init_repo()
        self.put("models.py", "class Local:\n    pass\n")
        self.put(
            "app.py",
            "from discord import ui\n"
            "from models import Local\n"
            "class Panel(ui.View, Local, object):\n"
            "    def refresh(self):\n"
            "        return 1\n"
            "    def resize(self, width=10):\n"
            "        return width\n"
            "def refresh():\n"
            "    return 2\n"
            "def lonely():\n"
            "    return 3\n",
        )
        self.put("caller.py", "Panel().resize()\n")
        self.put("tests/test_app.py", "refresh()\nlonely()\n")
        candidates = self.scan("--paths", "app.py")["candidates"]
        self.assertEqual(
            [(c["symbol"], c["change_tag"]) for c in candidates],
            [
                ("Panel.refresh", "cross-test"),
                ("Panel.resize", "path-audit"),
                ("refresh", "cross-test"),
                ("lonely", "cross-test"),
            ],
        )
        method, default, function, lonely = (c["evidence"] for c in candidates)
        external = "enclosing class bases: ui.View (not defined in repo)"
        for evidence in (method, function):
            self.assertIn("aggregates across 2 prod definitions", evidence)
            self.assertIn("not attributed to this definition", evidence)
            self.assertIn("test calls: 1, test non-call refs: 0", evidence)
            self.assertIn("ambiguous_defs: 2", evidence)
        self.assertIn("aggregates across 1 prod definition;", lonely)
        self.assertNotIn("ambiguous_defs", lonely)
        self.assertIn(external, method)
        self.assertIn(external, default)
        self.assertIn("uniform value: <DEFAULT>", default)
        self.assertNotIn("enclosing class bases", function)
        self.assertNotIn("enclosing class bases", lonely)

    def test_cross_orphaned_deleted_calls_and_deleted_file_inventory(self):
        self.init_repo()
        self.put(
            "app.py", "def orphan():\n    return 1\ndef untouched():\n    return 2\n"
        )
        self.put("caller.py", "orphan()\n")
        base = self.commit()
        for remaining, expected in [
            ("orphan()\n", []),
            ("callbacks = [orphan]\n", []),
            ("", ["orphan"]),
        ]:
            with self.subTest(remaining=remaining):
                self.put("caller.py", remaining)
                result = self.scan("--base", base)
                candidates = [
                    c
                    for c in result["candidates"]
                    if c["rule"] == "PY10c_cross_test_reachability"
                ]
                self.assertEqual([c["symbol"] for c in candidates], expected)
                if expected:
                    self.assertEqual(candidates[0]["change_tag"], "orphaned")
                    self.assertEqual(candidates[0]["path"], "app.py")
        (self.root / "caller.py").unlink()
        deleted = self.scan("--base", base)
        self.assertEqual(deleted["summary"]["files_scanned"], 1)
        self.assertEqual(deleted["summary"]["cross_checked_files"], 1)
        self.assertEqual(deleted["candidates"][0]["change_tag"], "orphaned")
        self.put("tests/test_app.py", "orphan()\n")
        self.assertEqual(
            self.scan("--base", base)["candidates"][0]["change_tag"], "cross-test"
        )

    def test_cross_diff_emits_changed_definitions_not_unchanged_namesakes(self):
        self.init_repo()
        panel = "class Panel:\n    def shared(self):\n        return 2\n"
        self.put("app.py", "def shared():\n    return 1\n" + panel)
        self.put("other.py", "def shared():\n    return 3\n")
        self.put("tests/test_app.py", "shared()\n")
        base = self.commit()
        self.put("app.py", "def shared():\n    return 4\n" + panel)
        for other, flagged in [
            ("def shared():\n    return 3\n", ["app.py"]),
            ("def shared():\n    return 5\n", ["app.py", "other.py"]),
        ]:
            with self.subTest(other=other):
                self.put("other.py", other)
                candidates = self.scan("--base", base)["candidates"]
                self.assertEqual(
                    [(c["path"], c["symbol"], c["change_tag"]) for c in candidates],
                    [(path, "shared", "cross-test") for path in flagged],
                )
                for candidate in candidates:
                    self.assertIn("test calls: 1", candidate["evidence"])
                    self.assertIn("ambiguous_defs: 3", candidate["evidence"])

    def test_cross_diff_deleted_calls_trigger_on_net_loss_naming_lost_sites(self):
        self.init_repo()
        self.put("app.py", "def helper(value, retries=3):\n    return value, retries\n")
        base_files = {
            "caller.py": "helper(1)\nresult = helper(2)\n",
            "extra.py": "helper(5)\n",
        }
        cases = [
            (
                "re-added on the next line",
                {"caller.py": "helper(1)\nresult = None\nhelper(2)\n"},
                None,
            ),
            (
                "re-added reformatted",
                {"caller.py": "helper(1)\nresult = helper(\n    2,\n)\n"},
                None,
            ),
            (
                "re-added in another prod file",
                {"caller.py": "helper(1)\n", "other.py": "helper(2)\n"},
                None,
            ),
            (
                "lost",
                {"caller.py": "helper(1)\n", "extra.py": None},
                "base caller.py:2 (prod), base extra.py:1 (prod)",
            ),
            (
                "replaced by a test call",
                {"caller.py": "helper(1)\n", "tests/test_app.py": "helper(2)\n"},
                "base caller.py:2 (prod)",
            ),
            (
                "arguments changed",
                {"caller.py": "helper(1)\nresult = helper(3)\n"},
                "base caller.py:2 (prod)",
            ),
        ]
        tip_only = {path for _, changes, _ in cases for path in changes} - set(
            base_files
        )
        for label, changes, lost in cases:
            with self.subTest(label):
                for path in tip_only:
                    (self.root / path).unlink(missing_ok=True)
                for path, source in base_files.items():
                    self.put(path, source)
                base = self.commit()
                for path, source in changes.items():
                    if source is None:
                        (self.root / path).unlink()
                    else:
                        self.put(path, source)
                tip = self.commit()
                candidates = self.scan("--base", base, "--tip", tip)["candidates"]
                self.assertEqual(
                    [(c["path"], c["symbol"], c["code"]) for c in candidates],
                    [("app.py", "helper", "3")] if lost else [],
                )
                if lost:
                    evidence = candidates[0]["evidence"]
                    self.assertIn("uniform value: <DEFAULT>", evidence)
                    self.assertIn(f"lost call(s): {lost}", evidence)
                    self.assertIn("definition unchanged", evidence)

    def test_cross_snapshot_isolation_exact_tip_and_default_merge_base(self):
        self.init_repo()
        self.put("app.py", "def reachable():\n    return 1\n")
        self.put("caller.py", "value = 0\n")
        self.put("tests/test_app.py", "reachable()\n")
        base = self.commit()
        self.put("app.py", "def reachable():\n    return 2\n")
        tip = self.commit()
        for flags in [("--tip", tip), ("--merge-base",)]:
            with self.subTest(flags=flags):
                self.put("caller.py", "value = 0\n")
                committed = self.scan("--base", base, *flags)
                self.put("caller.py", "reachable()\n")
                isolated = self.scan("--base", base, *flags)
                self.assertEqual(isolated, committed)
                candidates = isolated["candidates"]
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0]["change_tag"], "cross-test")
                self.assertIn("prod calls: 0", candidates[0]["evidence"])
        self.assertEqual(self.scan("--base", base)["candidates"], [])

    def test_cross_cli_parses_each_active_file_at_most_once(self):
        self.init_repo()
        self.put("app.py", "def reachable():\n    return 1\n")
        self.put("caller.py", "from app import reachable as local\nlocal()\n")
        self.put("unrelated.py", "value = 1\n")
        self.put("broken.py", "reachable(\n")
        base = self.commit()
        self.put("app.py", "def reachable():\n    return 2\n")
        self.put("broken.py", "reachable((\n")
        tip = self.commit()
        stdout = io.StringIO()
        with (
            chdir(self.root),
            redirect_stdout(stdout),
            patch.object(
                sys,
                "argv",
                [str(SCRIPT), "--base", base, "--tip", tip, "--format", "json"],
            ),
            patch("ast.parse", wraps=ast.parse) as parse,
            self.assertRaises(SystemExit) as exit_,
        ):
            runpy.run_path(str(SCRIPT), run_name="__main__")
        self.assertEqual(exit_.exception.code, 0)
        counts = Counter(call.kwargs.get("filename") for call in parse.call_args_list)
        self.assertEqual(counts["base:app.py"], 1)
        self.assertEqual(counts["app.py"], 1)
        self.assertEqual(counts["caller.py"], 1)
        self.assertEqual(counts["broken.py"], 1)
        self.assertEqual(counts["unrelated.py"], 0)
        self.assertTrue(
            all(
                count <= 1
                for filename, count in counts.items()
                if filename and not filename.startswith("base:")
            )
        )
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["summary"]["cross_checked_files"], 4)
        self.assertEqual(result["summary"]["parse_error_count"], 2)
        self.assertEqual(
            sum(e["error"].startswith("base: ") for e in result["parse_errors"]), 1
        )

    def test_cross_target_filters_decorators_diff_scope_and_no_targets(self):
        self.init_repo()
        source = "def ordinary():\n    return 1\n"
        for decorator in (
            "app.get",
            "app.route('/x')",
            "pytest.fixture",
            "functools.cached_property",
            "abc.abstractmethod",
            "registry.register",
            "onHook",
            "contextmanager",
        ):
            source += f"@{decorator}\ndef routed_{len(source)}():\n    return 1\n"
        source += "def run():\n    return 1\ndef __magic__():\n    return 1\n"
        self.put("app.py", source)
        names = ["ordinary"] + [
            line.split("(")[0].removeprefix("def ")
            for line in source.splitlines()
            if line.startswith("def routed")
        ]
        self.put(
            "tests/test_app.py",
            "\n".join(f"{name}()" for name in [*names, "run", "__magic__"]) + "\n",
        )
        result = self.scan("--paths", "app.py")
        self.assertEqual([c["symbol"] for c in result["candidates"]], ["ordinary"])
        base = self.commit()
        self.put("app.py", source + "value = 1\n")
        self.assertEqual(self.scan("--base", base)["summary"]["cross_checked_files"], 0)
        self.put("app.py", source.replace("return 1", "return 2", 1))
        self.assertEqual(
            self.scan("--base", base)["candidates"][0]["symbol"], "ordinary"
        )
        self.put("filtered.py", "@app.getaway\ndef viable():\n    return 1\n")
        self.put("tests/test_filtered.py", "viable()\n")
        self.assertEqual(
            self.scan("--paths", "filtered.py")["candidates"][0]["symbol"], "viable"
        )

    def test_cross_universe_exclusions_and_test_roles(self):
        self.init_repo()
        self.put("app.py", "def reachable():\n    return 1\n")
        self.put(".gitignore", "ignored/\n")
        for name in ("ignored/caller.py", "build/caller.py", "omit/caller.py"):
            self.put(name, "reachable()\n")
        self.put("checks/case.py", "reachable()\n")
        (self.root / "binary.py").write_bytes(b"reachable()\x00")
        (self.root / "link.py").symlink_to(self.root / "ignored/caller.py")
        self.put("absent.py", "reachable()\n")
        self.put("redirect/caller.py", "reachable()\n")
        self.commit()
        (self.root / "absent.py").unlink()
        (self.root / "redirect/caller.py").unlink()
        (self.root / "redirect").rmdir()
        (self.root / "redirect").symlink_to(
            self.root / "ignored", target_is_directory=True
        )
        result = self.scan(
            "--paths", "app.py", "--exclude", "omit/*", "--test-glob", "checks/*"
        )
        self.assertEqual(result["summary"]["cross_checked_files"], 2)
        self.assertEqual(result["candidates"][0]["change_tag"], "cross-test")
        self.assertEqual(
            self.scan("--paths", "app.py", "--exclude", "omit/*")["candidates"], []
        )

    def test_base_decode_failure_is_reported_without_disabling_post_analysis(self):
        self.init_repo()
        (self.root / "app.py").write_bytes(b"# coding: utf-8\nvalue = '\xff'\n")
        self.put("deleted.py", "def broken(:\n")
        base = self.commit()
        self.put("app.py", 'hasattr(item, "value")\n')
        (self.root / "deleted.py").unlink()
        result = self.scan("--base", base)
        self.assertEqual(result["summary"]["files_scanned"], 2)
        self.assertEqual(result["summary"]["parse_error_count"], 2)
        self.assertTrue(
            all(error["error"].startswith("base: ") for error in result["parse_errors"])
        )
        self.assertEqual(result["candidates"][0]["rule"], "PY01_reflect_default")

    def test_cross_non_call_counts_deleted_test_calls_and_default_diff_tags(self):
        self.init_repo()
        self.put(
            "app.py",
            "async def callback():\n    return 1\n"
            "def configure(flag=False, *, mode=None):\n    return flag, mode\n"
            "def idle():\n    return 1\n",
        )
        self.put("caller.py", "configure(mode=None)\n")
        self.put("tests/test_app.py", "callbacks = [callback]\nidle()\n")
        base = self.commit()
        self.put("tests/test_app.py", "callbacks = [callback]\n")
        orphaned = self.scan("--base", base)["candidates"]
        self.assertEqual(
            [(c["symbol"], c["change_tag"]) for c in orphaned], [("idle", "orphaned")]
        )
        lost = "lost call(s): base tests/test_app.py:2 (test)"
        self.assertIn(lost, orphaned[0]["evidence"])
        self.assertIn("definition unchanged", orphaned[0]["evidence"])
        self.put(
            "app.py",
            "async def callback():\n    return 2\n"
            "def configure(flag=False, *, mode=None):\n    return flag, mode, 2\n"
            "def idle():\n    return 2\n",
        )
        candidates = self.scan("--base", base)["candidates"]
        callback = next(c for c in candidates if c["symbol"] == "callback")
        self.assertEqual(callback["change_tag"], "cross-test")
        self.assertIn("test calls: 0, test non-call refs: 1", callback["evidence"])
        self.assertNotIn("lost call(s)", callback["evidence"])
        idle = next(c for c in candidates if c["symbol"] == "idle")
        self.assertEqual(idle["change_tag"], "orphaned")
        self.assertIn(lost, idle["evidence"])
        self.assertNotIn("definition unchanged", idle["evidence"])
        defaults = [c for c in candidates if c["symbol"] == "configure"]
        self.assertEqual(len(defaults), 1)
        self.assertEqual(defaults[0]["change_tag"], "changed")
        self.assertEqual(defaults[0]["code"], "False; None")
        self.put("exports.py", 'from app import configure\n__all__ = ["configure"]\n')
        self.assertFalse(
            [
                c
                for c in self.scan("--base", base)["candidates"]
                if c["symbol"] == "configure"
            ]
        )

    def test_moved_defense_handles_non_named_exception_types_without_crashing(self):
        self.init_repo()
        self.put(
            "app.py",
            "def original():\n    try:\n        action()\n    except (Exception, extra()):\n        return None\n\ndef destination():\n    action()\n",
        )
        base = self.commit()
        self.put(
            "app.py",
            "def original():\n    action()\n\ndef destination():\n    try:\n        action()\n    except (extra(), Exception):\n        return None\n",
        )
        moved = [
            c
            for c in self.scan("--base", base)["candidates"]
            if c["rule"] == "PY10a_moved_defense"
        ]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]["symbol"], "destination")

    def test_transient_self_state_requires_matching_try_and_finally_writes(self):
        for body, cleanup, handler, expected in [
            ("self.context = active", "self.context = None", "", True),
            ("self.context: Context = active", "self.context = None", "", True),
            (
                "self.context = active",
                "self.context = None",
                "except* RuntimeError:\n    raise\n",
                True,
            ),
            ("self.context = None", "self.context = None", "", False),
            ("self.context = active", "self.context = previous", "", False),
            ("self.context = active", "self.other = None", "", False),
            ("other.context = active", "self.context = None", "", False),
            ("self.context = active", "other.context = None", "", False),
            (
                "def callback():\n    self.context = active",
                "self.context = None",
                "",
                False,
            ),
            (
                "self.context = active",
                "class Cleanup:\n    self.context = None",
                "",
                False,
            ),
        ]:
            with self.subTest(body=body, cleanup=cleanup, handler=handler):
                self.put(
                    "state.py",
                    "class Runner:\n    def run(self):\n        try:\n"
                    + textwrap.indent(body, "            ")
                    + "\n"
                    + textwrap.indent(handler, "        ")
                    + "        finally:\n"
                    + textwrap.indent(cleanup, "            ")
                    + "\n",
                )
                candidates = self.scan("--paths", "state.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    candidate = candidates[0]
                    self.assertEqual(candidate["rule"], "PY07_state_smuggling")
                    self.assertEqual(candidate["symbol"], "Runner.run")
                    self.assertIn("self.context", candidate["evidence"])
                    self.assertIn("finally", candidate["evidence"])
                    self.assertIn("Counterweight", candidate["verify"])

    def test_same_class_stash_needs_an_immediate_return_and_a_sibling_reader(self):
        made = "payload = compose()\n"
        stash = "self._last = payload\nreturn payload"
        for writer, reader, span in [
            (made + stash, "return self._last", [4, 7]),
            (
                made + "if ready:\n    self._last = payload\n    return payload\n"
                "return None",
                "return self._last",
                [5, 9],
            ),
            (made + stash, "self._last = None", None),
            (made + "print(self._last)\n" + stash, "return None", None),
            (
                made + "self._last = payload\nlog(payload)\nreturn payload",
                "return self._last",
                None,
            ),
            (
                made + "self._last = payload\nreturn compose()",
                "return self._last",
                None,
            ),
            ("global payload\n" + made + stash, "return self._last", None),
            (
                "print([payload for payload in compose()])\n" + stash,
                "return self._last",
                None,
            ),
            (
                made + stash,
                "class View:\n    def show(self):\n        return self._last\n"
                "return View",
                None,
            ),
        ]:
            with self.subTest(writer=writer, reader=reader):
                self.put(
                    "state.py",
                    "class Provider:\n    def build(self):\n"
                    + textwrap.indent(writer, "        ")
                    + "\n    def dump(self):\n"
                    + textwrap.indent(reader, "        ")
                    + "\n",
                )
                candidates = self.scan("--paths", "state.py")["candidates"]
                self.assertEqual(len(candidates), int(span is not None))
                if span is not None:
                    candidate = candidates[0]
                    self.assertEqual(candidate["rule"], "PY07_state_smuggling")
                    self.assertEqual(candidate["symbol"], "Provider.build")
                    self.assertEqual(candidate["span"], span)
                    self.assertIn("self._last", candidate["evidence"])
                    self.assertIn(f"dump at line {span[1]}", candidate["evidence"])

    def test_dict_dependency_smuggling_requires_final_key_token_and_object_value(self):
        for key, expected in [
            ("service", True),
            ("user_service", True),
            ("orderRepo", True),
            ("db.repository", True),
            ("Reader", True),
            ("cache-writer", True),
            ("db_conn", True),
            ("dbConnection", True),
            ("SESSION", True),
            ("session_id", False),
            ("repo_url", False),
            ("reader_name", False),
            ("connection_string", False),
            ("microservice", False),
            ("", False),
        ]:
            for expression in [
                f"data[{key!r}] = dep",
                f"return {{{key!r}: owner.dep}}",
            ]:
                with self.subTest(key=key, expression=expression):
                    self.put(
                        "state.py", "def pack(data, dep, owner):\n    " + expression
                    )
                    candidates = self.scan("--paths", "state.py")["candidates"]
                    self.assertEqual(len(candidates), int(expected))
                    if expected:
                        candidate = candidates[0]
                        self.assertEqual(candidate["rule"], "PY07_state_smuggling")
                        self.assertIn(key, candidate["evidence"])
                        self.assertIn("line 2", candidate["evidence"])
                        self.assertIn("Counterweight", candidate["verify"])
        for expression in [
            'data["session"] = None',
            'data["service"] = make_service()',
            'return {"repo": "url"}',
            'return {"reader": deps[0]}',
            'return {"conn": lambda: dep}',
            "return {key: dep}",
        ]:
            with self.subTest(expression=expression):
                self.put("state.py", "def pack(data, dep):\n    " + expression)
                self.assertEqual(self.scan("--paths", "state.py")["candidates"], [])
        self.put("state.py", 'data = {"session": dep}\n')
        self.assertEqual(self.scan("--paths", "state.py")["candidates"], [])

    def test_roundtrip_conversions_require_the_complete_nested_chain(self):
        for expression, expected in [
            ("json.loads(json.dumps(value))", True),
            ("json.loads(json.dumps(value, sort_keys=True))", True),
            ("int(float(value))", True),
            ("datetime.fromisoformat(value.isoformat())", True),
            ("date.fromisoformat(value.isoformat(timespec='seconds'))", True),
            ("set(list(set(values)))", True),
            ("int(value)", False),
            ("float(value)", False),
            ("json.dumps(value)", False),
            ("json.loads(value)", False),
            ("json.loads(other.dumps(value))", False),
            ("date.fromisoformat(value)", False),
            ("value.isoformat()", False),
            ("date.fromisoformat(value.format())", False),
            ("set(list(values))", False),
            ("list(set(values))", False),
        ]:
            with self.subTest(expression=expression):
                self.put("churn.py", "result = " + expression + "\n")
                candidates = self.scan("--paths", "churn.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    self.assertEqual(candidates[0]["rule"], "PY08_roundtrip_and_copy")
                    self.assertEqual(candidates[0]["code"], expression)
                    self.assertIn("line 1", candidates[0]["evidence"])
                    self.assertIn("Counterweight", candidates[0]["verify"])

    def test_dict_comprehension_rewrap_requires_exact_target_argument(self):
        for expression, expected in [
            ("[dict(row) for row in rows]", True),
            ("[dict(row) for group in groups for row in group]", True),
            ("[dict(other) for row in rows]", False),
            ("[dict(row, extra=1) for row in rows]", False),
            ("[dict(**row) for row in rows]", False),
            ("[dict(row.items()) for row in rows]", False),
            ("[dict(row) for key, row in pairs]", False),
            ("[dict(row) for obj.row in rows]", False),
            ("(dict(row) for row in rows)", False),
        ]:
            with self.subTest(expression=expression):
                self.put("churn.py", "result = " + expression + "\n")
                candidates = self.scan("--paths", "churn.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    self.assertEqual(candidates[0]["rule"], "PY08_roundtrip_and_copy")
                    self.assertIn("row", candidates[0]["evidence"])
                    self.assertIn("line 1", candidates[0]["evidence"])

    def test_dict_rewrap_evidence_names_the_enclosing_function_row_factory(self):
        self.put(
            "rows.py",
            """
            import sqlite3


            def connect(path):
                conn = sqlite3.connect(path)
                conn.row_factory = sqlite3.Row
                return conn


            class Store:
                def tagged(self, conn):
                    conn.row_factory = self.db.dict_row
                    rows = conn.execute("SELECT 1").fetchall()
                    self.conn.row_factory = None
                    return [dict(row) for row in rows]

                def untagged(self, conn):
                    rows = conn.execute("SELECT 1").fetchall()
                    return [dict(row) for row in rows]

                def nested(self, conn):
                    def configure():
                        conn.row_factory = sqlite3.Row

                    configure()
                    return [dict(row) for row in conn.execute("SELECT 1")]
            """,
        )
        evidence = {
            candidate["symbol"]: candidate["evidence"]
            for candidate in self.scan("--paths", "rows.py")["candidates"]
            if candidate["rule"] == "PY08_roundtrip_and_copy"
        }
        self.assertEqual(
            set(evidence), {"Store.tagged", "Store.untagged", "Store.nested"}
        )
        self.assertIn(
            "conn.row_factory = self.db.dict_row at line 12", evidence["Store.tagged"]
        )
        self.assertIn(
            "self.conn.row_factory = None at line 14", evidence["Store.tagged"]
        )
        for symbol in ["Store.untagged", "Store.nested"]:
            with self.subTest(symbol=symbol):
                self.assertNotIn("row_factory", evidence[symbol])

    def test_copy_churn_requires_per_iteration_execution_without_nested_scopes(self):
        for body, expected in [
            ("for item in items:\n    deepcopy(item)", 1),
            ("async for item in items:\n    copy.deepcopy(item)", 1),
            ("while ready():\n    asdict(item)", 1),
            ("while dataclasses.asdict(item):\n    consume(item)", 1),
            ("[deepcopy(item) for item in items]", 1),
            ("{key: dataclasses.asdict(item) for key, item in items}", 1),
            ("{asdict(item) for item in items}", 1),
            ("(copy.deepcopy(item) for item in items)", 1),
            ("[item for item in items if asdict(item)]", 1),
            ("[part for item in items for part in deepcopy(item)]", 1),
            ("for group in groups:\n    for item in group:\n        deepcopy(item)", 1),
            ("asdict(item)", 0),
            ("deepcopy(item)", 0),
            ("for item in deepcopy(items):\n    consume(item)", 0),
            ("async for item in asdict(items):\n    consume(item)", 0),
            ("for item in items:\n    consume(item)\nelse:\n    deepcopy(item)", 0),
            ("while ready():\n    consume(item)\nelse:\n    asdict(item)", 0),
            ("[item for item in deepcopy(items)]", 0),
            ("(item for item in asdict(items))", 0),
            ("for item in items:\n    def callback():\n        deepcopy(item)", 0),
            ("for item in items:\n    async def callback():\n        asdict(item)", 0),
            (
                "for item in items:\n    class Callback:\n        value = asdict(item)",
                0,
            ),
            ("for item in items:\n    callback = lambda: deepcopy(item)", 0),
            ("for item in items:\n    other.deepcopy(item)", 0),
            ("for item in items:\n    other.asdict(item)", 0),
            (
                "for item in items:\n    def callback():\n"
                "        for part in item:\n            deepcopy(part)",
                1,
            ),
        ]:
            with self.subTest(body=body):
                self.put(
                    "churn.py", "async def run():\n" + textwrap.indent(body, "    ")
                )
                candidates = self.scan("--paths", "churn.py")["candidates"]
                self.assertEqual(len(candidates), expected)
                if expected:
                    candidate = candidates[0]
                    self.assertEqual(candidate["rule"], "PY08_roundtrip_and_copy")
                    self.assertIn("iteration", candidate["evidence"])
                    self.assertIn("line", candidate["evidence"])
                    self.assertIn("Counterweight", candidate["verify"])

    def test_lock_io_and_hashing_use_context_tokens_and_stop_at_nested_scopes(self):
        prompts = {True: set(), False: set()}
        for context, expected in [
            ("lock", True),
            ("locks", True),
            ("self._lock", True),
            ("user_locks[user_id]", True),
            ("self.user_challenge_locks[user_id]", True),
            ("user_locks[guild_id][user_id]", True),
            ("conn.transaction()", True),
            ("db.begin_immediate()", True),
            ("db.savepoint()", True),
            ("write_lock", True),
            ("threading.RLock()", True),
            ("FileLock(path)", True),
            ("clock()", False),
            ("self.block_writer()", False),
            ("open(lock_path)", False),
            ("manager(transaction=lock)", False),
            ("sessions[user_id]", False),
            ("sessions[lock_id]", False),
        ]:
            for keyword in ["with", "async with"]:
                with self.subTest(context=context, keyword=keyword):
                    self.put(
                        "locking.py",
                        f"async def run():\n    {keyword} {context}:\n"
                        "        path.read_text()\n",
                    )
                    candidates = self.scan("--paths", "locking.py")["candidates"]
                    self.assertEqual(len(candidates), int(expected))
                    if expected:
                        candidate = candidates[0]
                        self.assertEqual(candidate["rule"], "PY09_io_or_tx_in_lock")
                        self.assertEqual(candidate["span"], [2, 3])
                        self.assertIn("line 2", candidate["evidence"])
                        self.assertIn(context, candidate["evidence"])
                        self.assertIn("Counterweight", candidate["verify"])
                        prompts[context.startswith(("conn.", "db."))].add(
                            candidate["verify"]
                        )
        self.assertTrue(prompts[True].isdisjoint(prompts[False]))
        for expression, expected in [
            ("open(path)", True),
            ("path.open()", True),
            ("path.read_text()", True),
            ("path.write_text(text)", True),
            ("path.read_bytes()", True),
            ("path.write_bytes(data)", True),
            ("read_text(path)", True),
            ("write_text(path, text)", True),
            ("read_bytes(path)", True),
            ("write_bytes(path, data)", True),
            ("shutil.copy(source, target)", True),
            ("os.remove(path)", True),
            ("os.unlink(path)", True),
            ("os.replace(source, target)", True),
            ("hashlib.sha256(data).hexdigest()", True),
            ("sha256(data)", True),
            ("hashlib.blake2b(data)", True),
            ("interaction.followup.send(message)", True),
            ("self.interaction.followup.send(message)", True),
            ("os.fspath(path)", False),
            ("path.exists()", False),
            ("consume(data)", False),
            ("followup.send(message)", False),
            ("interaction.response.send_message(message)", False),
        ]:
            with self.subTest(expression=expression):
                self.put(
                    "locking.py",
                    "with outer_lock:\n    with inner_lock:\n        " + expression,
                )
                candidates = self.scan("--paths", "locking.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    self.assertEqual(candidates[0]["rule"], "PY09_io_or_tx_in_lock")
                    self.assertIn("outer_lock", candidates[0]["evidence"])
                    self.assertIn("inner_lock", candidates[0]["evidence"])
        for body in [
            "def callback():\n    open(path)",
            "async def callback():\n    path.write_text(text)",
            "class Callback:\n    data = path.read_bytes()",
            "callback = lambda: sha256(data)",
        ]:
            with self.subTest(body=body):
                self.put("locking.py", "with lock:\n" + textwrap.indent(body, "    "))
                self.assertEqual(self.scan("--paths", "locking.py")["candidates"], [])
        self.put("locking.py", "open(path)\nhashlib.sha256(data)\n")
        self.assertEqual(self.scan("--paths", "locking.py")["candidates"], [])
        self.put(
            "locking.py",
            "async def run(user_locks, user_id, interaction):\n"
            "    async with user_locks[user_id]:\n"
            "        await interaction.followup.send('inside')\n",
        )
        candidates = self.scan("--paths", "locking.py")["candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["rule"], "PY09_io_or_tx_in_lock")
        self.assertEqual(candidates[0]["span"], [2, 3])
        self.assertIn("interaction.followup.send", candidates[0]["evidence"])
        self.assertIn("user_locks[user_id]", candidates[0]["evidence"])
        self.put(
            "locking.py",
            "async def run(user_locks, user_id, interaction):\n"
            "    async with user_locks[user_id]:\n"
            "        reply = 'outside'\n"
            "    await interaction.followup.send(reply)\n",
        )
        self.assertEqual(self.scan("--paths", "locking.py")["candidates"], [])

    def test_nested_transaction_contexts_are_reported_once_in_acquisition_order(self):
        for body, expected in [
            ("with lock:\n    with conn.transaction():\n        work()", 1),
            ("with lock:\n    with transaction.open(path):\n        work()", 1),
            (
                "with db.begin_immediate():\n    with conn.savepoint():\n        work()",
                1,
            ),
            ("async with lock:\n    async with conn.savepoint():\n        work()", 1),
            (
                "with outer_lock:\n    with inner_lock:\n        with transaction:\n            work()",
                1,
            ),
            ("with lock, conn.transaction():\n    work()", 1),
            ("with conn.transaction(), lock:\n    work()", 0),
            ("with lock, open(path):\n    work()", 1),
            ("with open(path), lock:\n    work()", 0),
            ("with lock:\n    with conn.begin_immediate():\n        work()", 0),
            ("with lock:\n    with transactionless():\n        work()", 0),
            ("with lock:\n    with manager(savepoint):\n        work()", 0),
            ("with clock():\n    with conn.transaction():\n        work()", 0),
            (
                "with lock:\n    def callback():\n        with conn.transaction():\n            work()",
                0,
            ),
            ("with conn.savepoint():\n    work()", 0),
        ]:
            with self.subTest(body=body):
                self.put(
                    "locking.py", "async def run():\n" + textwrap.indent(body, "    ")
                )
                candidates = self.scan("--paths", "locking.py")["candidates"]
                self.assertEqual(len(candidates), expected)
                if expected:
                    self.assertEqual(candidates[0]["rule"], "PY09_io_or_tx_in_lock")
                    self.assertIn("with", candidates[0]["evidence"])
                    self.assertIn("Counterweight", candidates[0]["verify"])

    def test_transaction_entry_requires_a_check_in_the_same_function_scope(self):
        for check, context, expected in [
            ("assert conn.in_transaction", "conn.transaction()", True),
            ("if conn.in_transaction:\n    work()", "conn.savepoint()", True),
            ("if not conn.in_transaction:\n    return", "db.savepoint", True),
            ("while conn.in_transaction:\n    work()", "db.transaction()", True),
            ("work() if conn.in_transaction else wait()", "db.transaction()", True),
            ("assert conn.in_transaction is True", "db.transaction()", True),
            ("assert conn.in_transaction", "write_lock", False),
            ("assert conn.in_transaction", "db.begin_immediate()", False),
            ("assert conn.in_transaction", "transactionless()", False),
            ("assert conn.in_transaction", "manager(savepoint)", False),
            ("assert conn.ready", "conn.transaction()", False),
            ("log(conn.in_transaction)", "conn.transaction()", False),
            ("snapshot = conn.in_transaction", "conn.transaction()", False),
            (
                "def callback():\n    assert conn.in_transaction",
                "conn.transaction()",
                False,
            ),
            (
                "class Callback:\n    assert conn.in_transaction",
                "conn.transaction()",
                False,
            ),
            (
                "callback = lambda: bool(conn.in_transaction)",
                "conn.transaction()",
                False,
            ),
        ]:
            with self.subTest(check=check, context=context):
                self.put(
                    "locking.py",
                    "async def run():\n"
                    + textwrap.indent(check, "    ")
                    + f"\n    async with {context}:\n        work()\n",
                )
                candidates = self.scan("--paths", "locking.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    candidate = candidates[0]
                    self.assertEqual(candidate["rule"], "PY09_io_or_tx_in_lock")
                    self.assertEqual(candidate["symbol"], "run")
                    self.assertIn("in_transaction", candidate["evidence"])
                    self.assertIn("line 2", candidate["evidence"])
                    self.assertIn(context, candidate["evidence"])
                    self.assertIn("Counterweight", candidate["verify"])
        self.put(
            "locking.py",
            """
            def outer():
                assert conn.in_transaction
                def callback():
                    with conn.transaction():
                        work()
            def other():
                with conn.savepoint():
                    work()
            """,
        )
        self.assertEqual(self.scan("--paths", "locking.py")["candidates"], [])

    def test_transaction_checks_include_boolean_expressions_and_filter_guards(self):
        for check, line in [
            ("ready = conn.in_transaction is True", 2),
            ("ready = not conn.in_transaction", 2),
            ("ready = conn.in_transaction and enabled", 2),
            ("ready = bool(conn.in_transaction)", 2),
            ("selected = [row for row in rows if conn.in_transaction]", 2),
            ("match value:\n    case _ if conn.in_transaction:\n        work()", 3),
        ]:
            with self.subTest(check=check):
                self.put(
                    "locking.py",
                    "def run():\n"
                    + textwrap.indent(check, "    ")
                    + "\n    with conn.transaction():\n        work()\n",
                )
                candidates = self.scan("--paths", "locking.py")["candidates"]
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0]["rule"], "PY09_io_or_tx_in_lock")
                self.assertIn(f"line {line}", candidates[0]["evidence"])

    def test_lock_header_added_around_unchanged_io_is_diff_eligible(self):
        self.init_repo()
        self.put("locking.py", "def run():\n        path.read_text()\n")
        base = self.commit()
        self.put("locking.py", "def run():\n    with lock:\n        path.read_text()\n")
        candidates = self.scan("--base", base)["candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["rule"], "PY09_io_or_tx_in_lock")
        self.assertEqual(candidates[0]["change_tag"], "changed")
        self.assertEqual(candidates[0]["span"], [2, 3])
        self.assertIn("with lock at line 2", candidates[0]["evidence"])

    def test_state_and_lock_diff_eligibility_uses_only_contributing_children(self):
        self.init_repo()
        original = textwrap.dedent("""
            def transient(self):
                try:
                    self.context = active
                    unrelated_try()
                finally:
                    self.context = None
                    unrelated_finally()
            def locked():
                with lock:
                    path.read_text()
                    unrelated_lock()
                    with db.savepoint():
                        unrelated_nested()
            def contract():
                assert conn.in_transaction
                unrelated_between()
                with conn.transaction():
                    unrelated_transaction()
        """)
        self.put("scope.py", original)
        base = self.commit()
        for before, after, expected in [
            (
                "self.context = active",
                "self.context = replacement",
                "PY07_state_smuggling",
            ),
            (
                "self.context = None",
                "self.context: Context = None",
                "PY07_state_smuggling",
            ),
            ("unrelated_try()", "new_try_work()", None),
            ("unrelated_finally()", "new_finally_work()", None),
            ("path.read_text()", "path.read_bytes()", "PY09_io_or_tx_in_lock"),
            ("unrelated_lock()", "new_lock_work()", None),
            ("db.savepoint()", "db.transaction()", "PY09_io_or_tx_in_lock"),
            ("unrelated_nested()", "new_nested_work()", None),
            (
                "assert conn.in_transaction",
                "assert not conn.in_transaction",
                "PY09_io_or_tx_in_lock",
            ),
            ("unrelated_between()", "new_between_work()", None),
            ("conn.transaction()", "conn.savepoint()", "PY09_io_or_tx_in_lock"),
            ("unrelated_transaction()", "new_transaction_work()", None),
        ]:
            with self.subTest(before=before):
                self.put("scope.py", original.replace(before, after))
                candidates = self.scan("--base", base)["candidates"]
                self.assertEqual(len(candidates), int(expected is not None))
                if expected:
                    self.assertEqual(candidates[0]["rule"], expected)
                    self.assertEqual(candidates[0]["change_tag"], "changed")

    def test_default_shadow_rebinding_respects_current_scope(self):
        for intervening, expected in [
            ("from external import value", False),
            ("def value():\n    pass", False),
            ("def helper():\n    value = None", True),
        ]:
            with self.subTest(intervening=intervening):
                self.put(
                    "defaults.py",
                    "def load(raw):\n    value = raw or 1\n"
                    + textwrap.indent(intervening, "    ")
                    + "\n    if value is None:\n        return None\n",
                )
                candidates = self.scan("--paths", "defaults.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    self.assertEqual(
                        candidates[0]["rule"], "PY03_default_shadows_check"
                    )

    def test_error_washing_in_exception_group_handler(self):
        self.put(
            "groups.py",
            """
            def load():
                try:
                    work()
                except* Exception:
                    pass
            """,
        )
        candidates = self.scan("--paths", "groups.py")["candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["rule"], "PY02_error_washing")
        self.assertEqual(candidates[0]["span"], [4, 5])
        self.assertIn("Exception", candidates[0]["evidence"])

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
                "cross_checked_files": 1,
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
        self.assertIn("Receiver self", candidate["evidence"])
        self.assertIn("class Record", candidate["evidence"])
        self.assertIn("'value' at line 3", candidate["evidence"])

    def test_memo_guard_requires_an_undeclared_attribute_and_a_derived_write(self):
        memo = 'if not hasattr(self, "cache"):\n    self.cache = build(self.source)'
        for declaration, bases, guard in (
            (
                "",
                "object",
                'if hasattr(self, "cache"):\n    self.cache = build(self.source)',
            ),
            (
                "",
                "object",
                'if not hasattr(self, "cache"):\n    self.other = build(self.source)',
            ),
            ("cache: bytes", "object", memo),
            ("cache = None", "object", memo),
            ("def __init__(self):\n    self.cache = seed", "object", memo),
            (
                "def __init__(self):\n    self.prepare()\n"
                "def prepare(self):\n    self.cache = seed",
                "object",
                memo,
            ),
            ("", "Middle", memo),
            ("", "External", memo),
            ("", "ExternalMiddle", memo),
            ("", "object", 'if not hasattr(self, "cache"):\n    self.cache = {}'),
            ("", "object", 'if not hasattr(self, "cache"):\n    self.cache = Cache()'),
        ):
            with self.subTest(declaration=declaration, bases=bases, guard=guard):
                self.put(
                    "memo.py",
                    "from foreign import External\n"
                    "class Base:\n    cache: bytes\n"
                    "class Middle(Base):\n    pass\n"
                    "class ExternalMiddle(External):\n    pass\n"
                    "class Memo(object):\n    def read(self):\n"
                    + textwrap.indent(memo, "        ")
                    + f"\nclass Hedge({bases}):\n"
                    + textwrap.indent(declaration, "    ")
                    + "\n    def read(self):\n"
                    + textwrap.indent(guard, "        ")
                    + "\n",
                )
                candidates = [
                    candidate
                    for candidate in self.scan("--paths", ".")["candidates"]
                    if candidate["rule"] == "PY01_reflect_default"
                ]
                self.assertEqual([c["symbol"] for c in candidates], ["Hedge.read"])

    def test_reflection_class_names_resolve_only_at_module_scope(self):
        for wrapper, suffix, visible in (
            ("def factory():", "", False),
            ("class Factory:", "", False),
            ("if supported:", "", True),
            ("try:", "except ImportError:\n    pass\n", True),
        ):
            with self.subTest(wrapper=wrapper):
                self.put(
                    "classes.py",
                    "from external import Base, Record\n"
                    + wrapper
                    + "\n    class Base:\n        pass\n"
                    "    class Record:\n        cache: bytes\n"
                    + suffix
                    + "class Model(Base):\n    def read(self):\n"
                    '        if not hasattr(self, "cache"):\n'
                    "            self.cache = derive(self.source)\n"
                    "def inspect(item: Record):\n"
                    '    return getattr(item, "cache", None)\n',
                )
                candidates = {
                    c["symbol"]: c
                    for c in self.scan("--paths", ".")["candidates"]
                    if c["rule"] == "PY01_reflect_default"
                }
                self.assertEqual("Model.read" in candidates, not visible)
                evidence = candidates["inspect"]["evidence"]
                self.assertIn("item: Record", evidence)
                if visible:
                    self.assertIn("'cache' at line 6", evidence)
                else:
                    self.assertIn("class unknown", evidence)
                    self.assertNotIn("'cache' at line 6", evidence)

    def test_constructor_helpers_distinguish_lazy_writes_from_declarations(self):
        for body, expected in (
            ('if not hasattr(self, "cache"):\n    self.cache = build(self.source)', []),
            (
                'if not hasattr(self, "cache"):\n    self.cache, other = derive(self.source)',
                [],
            ),
            ("self.cache = build(self.source)", ["Model.read"]),
            (
                'if not hasattr(self, "other"):\n    self.cache = build(self.source)',
                ["Model.prepare", "Model.read"],
            ),
            (
                'if not hasattr(self, "cache"):\n    pass\nelse:\n    self.cache = build(self.source)',
                ["Model.prepare", "Model.read"],
            ),
        ):
            with self.subTest(body=body):
                self.put(
                    "helpers.py",
                    "class Model:\n"
                    "    def __init__(self):\n"
                    "        if ready:\n            self.prepare()\n"
                    "    def prepare(self):\n"
                    + textwrap.indent(body, "        ")
                    + "\n    def read(self):\n"
                    '        if not hasattr(self, "cache"):\n'
                    "            self.cache = build(self.source)\n",
                )
                candidates = [
                    c
                    for c in self.scan("--paths", ".")["candidates"]
                    if c["rule"] == "PY01_reflect_default"
                ]
                self.assertEqual([c["symbol"] for c in candidates], expected)

    def test_conditional_initializer_and_helper_definitions_all_contribute_declarations(
        self,
    ):
        for methods in (
            "def __init__(self):\n    self.prepare()\n"
            "if enabled:\n    def prepare(self):\n        self.cache = seed\n"
            "else:\n    def prepare(self):\n        pass\n",
            "if enabled:\n    def __init__(self):\n        self.prepare()\n"
            "else:\n    def __init__(self):\n        pass\n"
            "def prepare(self):\n    self.cache = seed\n",
            "def __init__(self):\n    self.prepare()\n"
            "try:\n    def prepare(self):\n        self.cache = seed\n"
            "except ImportError:\n    def prepare(self):\n        pass\n",
        ):
            with self.subTest(methods=methods):
                self.put(
                    "conditional.py",
                    "class Model:\n"
                    + textwrap.indent(methods, "    ")
                    + "    def read(self):\n"
                    '        if not hasattr(self, "cache"):\n'
                    "            self.cache = derive(self.source)\n",
                )
                candidates = [
                    c
                    for c in self.scan("--paths", ".")["candidates"]
                    if c["rule"] == "PY01_reflect_default"
                ]
                self.assertEqual([c["symbol"] for c in candidates], ["Model.read"])

    def test_memo_guard_uses_any_derived_write_but_only_in_the_guarded_body(self):
        for body, expected in (
            ("self.cache = None", True),
            ("self.cache, other = derive(self.source)", False),
            ("self.cache, other = (None, False)", True),
            ("self.cache = -1", True),
            ("self.cache = list()", True),
            ("self.cache = bytearray()", True),
            ("self.cache = complex()", True),
            ("self.cache = self.compute()", False),
            ("self.cache = Cache(source=self.source)", False),
            (
                "if empty:\n    self.cache = ''\nelse:\n    self.cache = derive(self.source)",
                False,
            ),
            (
                "if empty:\n    self.cache = Cache()\nelse:\n    self.cache = derive(self.source)",
                False,
            ),
            ("def deferred():\n    self.cache = derive(self.source)", True),
        ):
            with self.subTest(body=body):
                self.put(
                    "memo.py",
                    "class Model:\n    def read(self):\n"
                    '        if not hasattr(self, "cache"):\n'
                    + textwrap.indent(body, "            ")
                    + "\n",
                )
                candidates = [
                    c
                    for c in self.scan("--paths", ".")["candidates"]
                    if c["rule"] == "PY01_reflect_default"
                ]
                self.assertEqual(len(candidates), int(expected))

    def test_memo_guard_distinguishes_builtin_constructor_from_same_named_method(self):
        for value, expected in (("self.bytes()", []), ("bytes()", ["Model.read"])):
            with self.subTest(value=value):
                self.put(
                    "memo.py",
                    "class Model:\n"
                    "    def bytes(self):\n        return self.source\n"
                    "    def read(self):\n"
                    '        if not hasattr(self, "cache"):\n'
                    f"            self.cache = {value}\n",
                )
                candidates = [
                    c
                    for c in self.scan("--paths", ".")["candidates"]
                    if c["rule"] == "PY01_reflect_default"
                ]
                self.assertEqual([c["symbol"] for c in candidates], expected)

    def test_reflection_pairs_only_cite_the_receivers_class(self):
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
        self.assertIn("Receiver self", first["evidence"])
        self.assertIn("class Second", first["evidence"])
        self.assertNotIn("First", first["evidence"])
        self.assertIn("'value' at line 8", first["evidence"])
        self.assertIn("'value' at line 10", first["evidence"])
        self.assertIn("'other' at line 11", second["evidence"])
        self.assertEqual(third["symbol"], "<module>")
        self.assertIn("Receiver local", third["evidence"])
        self.assertIn("class unknown", third["evidence"])
        self.assertNotIn("Second", third["evidence"])

    def test_receiver_evidence_stops_at_inner_bindings_but_preserves_closure_annotations(
        self,
    ):
        for body, known in (
            ('return [getattr(item, "value", None) for item in values]', False),
            (
                "def nested():\n    try:\n        run()\n"
                "    except Exception as item:\n"
                '        return getattr(item, "value", None)',
                False,
            ),
            ('def nested():\n    return getattr(item, "value", None)', True),
        ):
            with self.subTest(body=body):
                self.put(
                    "shadowing.py",
                    "class Record:\n    value: int\n"
                    "def inspect(item: Record, values):\n"
                    + textwrap.indent(body, "    ")
                    + "\n",
                )
                candidates = [
                    c
                    for c in self.scan("--paths", ".")["candidates"]
                    if c["rule"] == "PY01_reflect_default"
                ]
                self.assertEqual(len(candidates), 1)
                evidence = candidates[0]["evidence"]
                if known:
                    self.assertRegex(evidence, r"item: Record.*line 3")
                    self.assertIn("'value' at line 2", evidence)
                else:
                    self.assertIn("class unknown", evidence)
                    self.assertNotIn("Record", evidence)

    def test_receiver_evidence_reports_annotations_bindings_and_owning_class(self):
        self.put(
            "receivers.py",
            "class Base:\n    pass\n"
            "class Record(Base, external.Mixin):\n"
            "    value: int\n"
            "    def __init__(self):\n        self.prepare()\n"
            "    def prepare(self):\n        self.cache = seed\n"
            "    def read(self):\n        return getattr(self, 'cache', None)\n"
            "class Unrelated:\n    value = 0\n"
            "def typed(item: Record):\n    return getattr(item, 'value', None)\n"
            "def unknown(item: Any):\n    return getattr(item, 'value', None)\n"
            "def member(item: Record):\n    return getattr(item.child, 'value', None)\n"
            "def plugin():\n    cls = load_plugin()\n"
            "    return getattr(cls, 'value', None)\n"
            "def assigned():\n    item = Record()\n"
            "    return getattr(item, 'value', None)\n"
            "def forward(item: 'Record'):\n    return hasattr(item, 'value')\n",
        )
        evidence = {
            candidate["symbol"]: candidate["evidence"]
            for candidate in self.scan("--paths", ".")["candidates"]
            if candidate["rule"] == "PY01_reflect_default"
        }
        self.assertIn("Receiver self", evidence["Record.read"])
        self.assertIn("class Record", evidence["Record.read"])
        self.assertRegex(evidence["Record.read"], r"bases:.*Base.*external.Mixin")
        self.assertIn("'cache' at line 8", evidence["Record.read"])
        self.assertRegex(evidence["typed"], r"item: Record.*line 13")
        self.assertIn("'value' at line 4", evidence["typed"])
        self.assertIn("'value' at line 4", evidence["forward"])
        self.assertRegex(evidence["unknown"], r"item: Any.*line 15")
        self.assertIn("class unknown", evidence["unknown"])
        self.assertIn("Receiver item.child", evidence["member"])
        self.assertRegex(evidence["member"], r"item: Record.*line 17")
        self.assertIn("class unknown", evidence["member"])
        self.assertNotIn("'value' at line 4", evidence["member"])
        self.assertRegex(
            evidence["plugin"], r"local binding cls = load_plugin\(\).*line 20"
        )
        self.assertIn("control-flow unresolved", evidence["plugin"])
        self.assertIn("class unknown", evidence["plugin"])
        self.assertRegex(evidence["assigned"], r"item = Record\(\).*line 23")
        self.assertIn("control-flow unresolved", evidence["assigned"])
        self.assertIn("class unknown", evidence["assigned"])
        for facts in evidence.values():
            self.assertNotIn("Unrelated", facts)

    def test_error_washing_broad_handlers_follow_control_flow(self):
        cases = [
            ("Exception", "logger.error('failed')\nreturn {}", True),
            ("BaseException", "fallback = {}", True),
            ("", "pass", True),
            ("(KeyError, errors.TypeError)", "return None", True),
            ("(KeyError, Exception)", "pass", True),
            ("ValueError", "return None", False),
            ("(KeyError, KeyError)", "pass", False),
            (
                "Exception",
                "if retry:\n    raise\nelse:\n    raise DomainError()",
                False,
            ),
            ("Exception", "if retry:\n    return {}\nraise", True),
            ("Exception", "def nested():\n    return {}\nraise", False),
            (
                "Exception",
                "class Nested:\n    def run(self):\n        return {}\nraise",
                False,
            ),
            ("Exception", "for item in items:\n    raise", True),
            ("Exception", "for item in items:\n    return {}\nraise", True),
            ("Exception", "try:\n    recover()\nfinally:\n    raise", False),
            ("Exception", "try:\n    raise\nfinally:\n    return {}", True),
            (
                "Exception",
                "try:\n    recover()\nexcept ValueError:\n    return {}\nelse:\n    raise",
                True,
            ),
            (
                "Exception",
                "try:\n    recover()\nexcept ValueError:\n    raise\nelse:\n    raise",
                False,
            ),
            (
                "Exception",
                "try:\n    recover()\nexcept* ValueError:\n    log()\nelse:\n    raise",
                True,
            ),
            (
                "Exception",
                "try:\n    recover()\nexcept* ValueError:\n    raise\nelse:\n    raise",
                False,
            ),
            ("Exception", "with context():\n    return {}\nraise", True),
            ("Exception", "with context():\n    raise", True),
            ("Exception", "with context():\n    log()\nraise", False),
            (
                "Exception",
                "match error:\n    case 1:\n        raise\n    case _:\n        raise",
                False,
            ),
            ("Exception", "match error:\n    case 1:\n        raise", True),
            ("Exception", "match error:\n    case _ if ready:\n        raise", True),
        ]
        for caught, body, expected in cases:
            with self.subTest(caught=caught, body=body):
                header = f"except {caught}:" if caught else "except:"
                self.put(
                    "errors.py",
                    "def load():\n    try:\n        work()\n    "
                    + header
                    + "\n"
                    + textwrap.indent(body, "        ")
                    + "\n",
                )
                candidates = self.scan("--paths", "errors.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    candidate = candidates[0]
                    self.assertEqual(candidate["rule"], "PY02_error_washing")
                    self.assertEqual(candidate["symbol"], "load")
                    self.assertEqual(candidate["span"], [4, 4 + len(body.splitlines())])
                    self.assertIn(caught or "bare except", candidate["evidence"])
                    self.assertIn("line 4", candidate["evidence"])

    def test_error_washing_saved_retry_requires_post_loop_propagation(self):
        for following, escape, finalizer, expected in [
            ("assert last is not None\nraise last", "", "", False),
            ("raise last", "", "", False),
            ("return None", "", "", True),
            ("raise last", "if stop:\n    break", "", True),
            ("raise last", "if stop:\n    return None", "", True),
            ("raise last", "last = None", "", True),
            ("raise last", "", "finally:\n    return None\n", True),
            ("else:\n    pass\nraise last", "", "", True),
        ]:
            with self.subTest(following=following, escape=escape, finalizer=finalizer):
                self.put(
                    "retry.py",
                    "def send():\n    last = None\n    for attempt in range(3):\n"
                    "        try:\n            return deliver()\n"
                    "        except Exception as error:\n"
                    "            log(error)\n            last = error\n"
                    + textwrap.indent(finalizer, "        ")
                    + textwrap.indent(escape, "        ")
                    + "\n        sleep(attempt)\n"
                    + textwrap.indent(following, "    ")
                    + "\n",
                )
                candidates = self.scan("--paths", "retry.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    self.assertEqual(candidates[0]["rule"], "PY02_error_washing")
                    self.assertEqual(candidates[0]["symbol"], "send")
                    self.assertIn("non-raising exit", candidates[0]["evidence"])
        for condition, expected in [("True", True), ("1", True), ("pending", False)]:
            with self.subTest(condition=condition):
                self.put(
                    "retry.py",
                    f"def send():\n    last = None\n    while {condition}:\n"
                    "        try:\n            return deliver()\n"
                    "        except Exception as error:\n            last = error\n"
                    "    raise last\n",
                )
                candidates = self.scan("--paths", "retry.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    self.assertEqual(candidates[0]["rule"], "PY02_error_washing")
                    self.assertEqual(candidates[0]["symbol"], "send")
                    self.assertIn("non-raising exit", candidates[0]["evidence"])

    def test_error_washing_bounded_retry_requires_final_attempt_reraise(self):
        for condition, prefix, finalizer, expected in [
            ("attempt == 2", "log()", "", False),
            ("attempt == 3 - 1", "log()", "", False),
            ("attempt == 1", "log()", "", True),
            ("attempt == 2", "attempt = 0", "", True),
            ("attempt == 2", "if stop:\n    return None", "", True),
            ("attempt == 2", "if stop:\n    continue", "", True),
            ("attempt == 2", "if stop:\n    break", "", True),
            ("attempt == 2", "log()", "finally:\n    return None\n", True),
        ]:
            with self.subTest(condition=condition, prefix=prefix, finalizer=finalizer):
                self.put(
                    "retry.py",
                    "def send():\n    for attempt in range(3):\n"
                    "        try:\n            return deliver()\n"
                    "        except Exception:\n"
                    + textwrap.indent(prefix, "            ")
                    + f"\n            if {condition}:\n                raise\n"
                    + textwrap.indent(finalizer, "        "),
                )
                candidates = self.scan("--paths", "retry.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    self.assertEqual(candidates[0]["rule"], "PY02_error_washing")
                    self.assertIn("non-raising exit", candidates[0]["evidence"])

    def test_error_washing_cleanup_preserves_primary_or_cleanup_failure(self):
        for mutation, finalizer, expected_cleanup in [
            ("", "", False),
            ("saved = None", "", True),
            ("del saved", "", True),
            ("if stop:\n    return None", "", True),
            ("", "finally:\n    return None\n", True),
        ]:
            with self.subTest(mutation=mutation, finalizer=finalizer):
                self.put(
                    "cleanup.py",
                    "def update():\n    saved = None\n    try:\n        change()\n"
                    "    except Exception as error:\n        saved = error\n"
                    "    finally:\n        try:\n            restore()\n"
                    "        except Exception as cleanup_error:\n"
                    "            log(cleanup_error)\n"
                    + textwrap.indent(mutation, "            ")
                    + "\n            if saved is None:\n                raise\n"
                    + textwrap.indent(finalizer, "        ")
                    + "    if saved is not None:\n        raise saved\n",
                )
                candidates = self.scan("--paths", "cleanup.py")["candidates"]
                self.assertEqual(len(candidates), 1 + int(expected_cleanup))
                self.assertTrue(
                    all(c["rule"] == "PY02_error_washing" for c in candidates)
                )
                self.assertIn("except Exception as error", candidates[0]["code"])
                if expected_cleanup:
                    self.assertIn("cleanup_error", candidates[1]["code"])

    def test_error_washing_noreturn_helper_stays_with_truthful_evidence(self):
        for annotation, call, prefix, duplicate, helper, nonraising in [
            ("NoReturn", "self._reject()", "", "", "self._reject", False),
            ("typing.NoReturn", "self._reject()", "", "", "self._reject", False),
            (
                "typing_extensions.Never",
                "await cls._reject()",
                "",
                "",
                "cls._reject",
                False,
            ),
            ("Never", "await _reject()", "", "", "_reject", False),
            (
                "Never",
                "_reject()",
                "",
                "def _reject() -> NoReturn:\n    raise Rejected()\n",
                "_reject",
                False,
            ),
            (
                "NoReturn",
                "self._reject()",
                "if stop:\n    return None",
                "",
                "self._reject",
                True,
            ),
            (
                "NoReturn",
                "self._reject()",
                "",
                "def _reject() -> None:\n    pass\n",
                None,
                True,
            ),
            ("NoReturn", "other._reject()", "", "", None, True),
            ("None", "self._reject()", "", "", None, True),
            ("NoReturn", "self._reject()", "return None", "", None, True),
            ("NoReturn", "return self._reject()", "", "", None, True),
        ]:
            with self.subTest(
                annotation=annotation, call=call, prefix=prefix, duplicate=duplicate
            ):
                self.put(
                    "helpers.py",
                    duplicate
                    + f"class Client:\n    @staticmethod\n    def _reject() -> {annotation}:\n"
                    "        raise Rejected()\n"
                    "    async def check(self):\n        try:\n            request()\n"
                    "        except Exception:\n"
                    + textwrap.indent(prefix, "            ")
                    + "\n            "
                    + call
                    + "\n",
                )
                candidates = self.scan("--paths", "helpers.py")["candidates"]
                self.assertEqual(len(candidates), 1)
                candidate = candidates[0]
                self.assertEqual(candidate["rule"], "PY02_error_washing")
                self.assertEqual(candidate["symbol"], "Client.check")
                self.assertEqual(
                    "non-raising exit" in candidate["evidence"], nonraising
                )
                if helper:
                    self.assertIn(
                        f"re-raises via `{helper}` (NoReturn)", candidate["evidence"]
                    )
                else:
                    self.assertNotIn("re-raises via", candidate["evidence"])

    def test_error_washing_noreturn_helper_keeps_earlier_loop_exits(self):
        for escape in ("break", "continue"):
            with self.subTest(escape=escape):
                self.put(
                    "helpers.py",
                    "def reject() -> Never:\n    raise Rejected()\n"
                    "def run():\n    for item in items:\n"
                    "        try:\n            work(item)\n"
                    "        except Exception:\n            if stop:\n"
                    f"                {escape}\n            reject()\n",
                )
                candidates = self.scan("--paths", "helpers.py")["candidates"]
                self.assertEqual(len(candidates), 1)
                self.assertIn("non-raising exit", candidates[0]["evidence"])
                self.assertIn(
                    "re-raises via `reject` (NoReturn)", candidates[0]["evidence"]
                )

    def test_error_washing_deferred_propagation_keeps_hook_fact(self):
        self.put(
            "hooks.py",
            """
            def saved_retry(raw):
                last = None
                for attempt in range(3):
                    try:
                        return json.loads(raw, object_pairs_hook=unique)
                    except Exception as error:
                        last = error
                assert last is not None
                raise last

            def bounded_retry(raw):
                for attempt in range(2):
                    try:
                        return json.loads(raw, object_pairs_hook=unique)
                    except Exception:
                        if attempt == 1:
                            raise

            def cleanup(raw):
                saved = None
                try:
                    change()
                except OSError as error:
                    saved = error
                finally:
                    try:
                        json.loads(raw, object_pairs_hook=unique)
                    except Exception:
                        if saved is None:
                            raise
                if saved is not None:
                    raise saved
            """,
        )
        candidates = self.scan("--paths", "hooks.py")["candidates"]
        self.assertEqual(len(candidates), 3)
        for candidate, symbol, line in zip(
            candidates,
            ("saved_retry", "bounded_retry", "cleanup"),
            (5, 14, 27),
            strict=True,
        ):
            self.assertEqual(candidate["rule"], "PY02_error_washing")
            self.assertEqual(candidate["symbol"], symbol)
            self.assertEqual(
                candidate["evidence"].split("; ")[1:],
                [
                    f"json.loads(object_pairs_hook=...) at line {line} is not bare re-raised on every path"
                ],
            )

    def test_error_washing_json_hook_preserves_only_all_path_bare_reraises(self):
        cases = [
            ("ValueError", "raise DomainError('invalid') from error", True),
            ("(KeyError, ValueError)", "return {}", True),
            ("BaseException", "raise DomainError()", True),
            ("Exception", "return {}", True),
            ("", "raise DomainError()", True),
            ("ValueError", "if retry:\n    raise\nelse:\n    raise", False),
            ("Exception", "def nested():\n    return {}\nraise", False),
            ("json.JSONDecodeError", "raise DomainError()", False),
            ("JSONDecodeError", "return {}", False),
            ("TypeError", "return {}", False),
        ]
        for caught, body, expected in cases:
            with self.subTest(caught=caught, body=body):
                header = f"except {caught} as error:" if caught else "except:"
                self.put(
                    "hook.py",
                    "def load(raw):\n    try:\n"
                    "        return json.loads(raw, object_pairs_hook=unique)\n    "
                    + header
                    + "\n"
                    + textwrap.indent(body, "        ")
                    + "\n",
                )
                candidates = self.scan("--paths", "hook.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    candidate = candidates[0]
                    self.assertEqual(candidate["rule"], "PY02_error_washing")
                    self.assertIn("object_pairs_hook", candidate["evidence"])
                    self.assertIn("line 3", candidate["evidence"])
                    if caught == "Exception":
                        self.assertIn("non-raising", candidate["evidence"])
        for body in [
            "return json.loads(raw)",
            "def nested():\n    return json.loads(raw, object_pairs_hook=unique)",
            "reader = lambda: json.loads(raw, object_pairs_hook=unique)",
        ]:
            with self.subTest(try_body=body):
                self.put(
                    "hook.py",
                    "def load(raw):\n    try:\n"
                    + textwrap.indent(body, "        ")
                    + "\n    except ValueError:\n        raise DomainError()\n",
                )
                self.assertEqual(self.scan("--paths", "hook.py")["candidates"], [])

    def test_error_washing_diff_scope_covers_handler_and_guarded_try_body(self):
        self.init_repo()
        path = self.put(
            "errors.py",
            """
            def load():
                try:
                    work()
                except Exception:
                    log()
                else:
                    done()
                finally:
                    close()
            """,
        )
        source = path.read_text()
        base = self.commit()
        for old, new, expected in [
            (
                "log()",
                "log()\n        return {}",
                [([4, 6], "except Exception: log() return {}")],
            ),
            ("work()", "rewritten_work()", [([4, 5], "except Exception: log()")]),
            ("done()", "rewritten_done()", []),
            ("close()", "rewritten_close()", []),
        ]:
            with self.subTest(edited=old):
                path.write_text(source.replace(old, new))
                tip = self.commit()
                candidates = self.scan("--base", base, "--tip", tip)["candidates"]
                self.assertEqual(
                    [
                        (c["rule"], c["span"], c["code"], c["change_tag"])
                        for c in candidates
                    ],
                    [
                        ("PY02_error_washing", span, code, "changed")
                        for span, code in expected
                    ],
                )

    def test_default_shadows_required_guard_within_five_siblings(self):
        cases = [
            ("raw or 0", "value is None", "", True),
            ("raw or 'default'", "not value", "", True),
            ("raw or [1]", "not value", "", True),
            ("raw or {}", "value is None", "", True),
            ("raw or 0", "not value", "", False),
            ("raw or []", "not value", "", False),
            ("raw or None", "value is None", "", False),
            ("raw or fallback", "value is None", "", False),
            ("raw or 1", "value is None", "value = raw\n", False),
            ("raw or 1", "value is None", "if ready:\n    value = raw\n", False),
            ("raw or 1", "value is None", "value, other = values\n", False),
            ("raw or 1", "value is None", "one()\ntwo()\nthree()\nfour()\n", True),
            (
                "raw or 1",
                "value is None",
                "one()\ntwo()\nthree()\nfour()\nfive()\n",
                False,
            ),
        ]
        for default, guard, intervening, expected in cases:
            with self.subTest(default=default, guard=guard, intervening=intervening):
                self.put(
                    "defaults.py",
                    "def load(raw):\n    value = "
                    + default
                    + "\n"
                    + textwrap.indent(intervening, "    ")
                    + f"    if {guard}:\n        raise ValueError('required')\n"
                    + "    return value\n",
                )
                candidates = self.scan("--paths", "defaults.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    candidate = candidates[0]
                    self.assertEqual(candidate["rule"], "PY03_default_shadows_check")
                    self.assertEqual(
                        candidate["span"], [2, 4 + len(intervening.splitlines())]
                    )
                    self.assertIn("line 2", candidate["evidence"])
                    self.assertIn(
                        f"line {3 + len(intervening.splitlines())}",
                        candidate["evidence"],
                    )
                    self.assertIn("Counterweight", candidate["verify"])
        self.put(
            "defaults.py",
            """
            def load(raw):
                value = raw or 1
                if value is None:
                    log()
                if ready:
                    other = raw or 1
                if other is None:
                    return None
            """,
        )
        self.assertEqual(self.scan("--paths", "defaults.py")["candidates"], [])

    def test_get_default_shadows_only_none_guard(self):
        for expression, guard, expected in [
            ("data.get('key', 0)", "value is None", True),
            ("data.get('key', '')", "value is None", True),
            ("data.get('key', [])", "value is None", True),
            ("data.get('key', 1)", "not value", False),
            ("data.get('key')", "value is None", False),
            ("data.get('key', None)", "value is None", False),
            ("data.get('key', default)", "value is None", False),
        ]:
            with self.subTest(expression=expression, guard=guard):
                self.put(
                    "defaults.py",
                    f"def load(data):\n    value: int = {expression}\n"
                    f"    if {guard}:\n        return None\n    return value\n",
                )
                candidates = self.scan("--paths", "defaults.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    self.assertEqual(
                        candidates[0]["rule"], "PY03_default_shadows_check"
                    )
                    self.assertIn("missing keys", candidates[0]["verify"])
                    self.assertIn("None", candidates[0]["verify"])
                    self.assertIn("explicitly stored", candidates[0]["verify"])

    def test_shadow_diff_ignores_unrelated_intervening_sibling(self):
        self.init_repo()
        self.put(
            "defaults.py",
            """
            def load(raw):
                value = raw or 1
                log('before')
                if value is None:
                    return None
            """,
        )
        base = self.commit()
        self.put(
            "defaults.py",
            """
            def load(raw):
                value = raw or 1
                log('after')
                if value is None:
                    return None
            """,
        )
        self.assertEqual(self.scan("--base", base)["candidates"], [])
        self.put(
            "defaults.py",
            """
            def load(raw):
                value = raw or 1
                log('before')
                if value is None:
                    raise ValueError('required')
            """,
        )
        candidates = self.scan("--base", base)["candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["span"], [2, 5])
        self.assertEqual(candidates[0]["change_tag"], "changed")
        self.assertEqual(
            set(candidates[0]),
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

    def test_lossy_coalesce_bundles_falsy_sites_but_keeps_singletons(self):
        self.put(
            "coalesce.py",
            """
            zero = data.get("zero") or 0
            empty = data["empty"] or ""

            def single(data):
                return data.value or False

            async def load(data):
                first = data.get("count") or 0
                return (
                    data.items
                    or []
                )
            """,
        )
        result = self.scan("--paths", "coalesce.py")
        candidates = result["candidates"]
        self.assertEqual(result["summary"]["candidate_count"], 4)
        falsy_verify = (
            "Can the immediate producer or governing type deliver a falsy value "
            "here? If not, the fallback is dead; if it delivers a distinct "
            'legitimate one (None vs 0, 0 vs ""), the fallback rewrites it. '
            "Counterweight only if the boundary contract equates missing with "
            "empty or the producer already rejects empty. See python.md §6."
        )
        self.assertEqual(
            [
                (c["symbol"], c["span"], c["code"], c["evidence"], c["verify"])
                for c in candidates
            ],
            [
                (
                    "<module>",
                    [1, 1],
                    'data.get("zero") or 0',
                    "Lookup coalesces to 0 at line 1",
                    falsy_verify,
                ),
                (
                    "<module>",
                    [2, 2],
                    'data["empty"] or ""',
                    "Lookup coalesces to '' at line 2",
                    falsy_verify,
                ),
                (
                    "single",
                    [5, 5],
                    "data.value or False",
                    "Lookup coalesces to False at line 5",
                    falsy_verify,
                ),
                (
                    "load",
                    [8, 11],
                    'data.get("count") or 0; data.items or []',
                    "2 coalescing lookups in load: "
                    'line 8: data.get("count") or 0 -> 0; '
                    "line 10: data.items or [] -> []",
                    falsy_verify,
                ),
            ],
        )
        for index, candidate in enumerate(candidates, 1):
            self.assertEqual(
                set(candidate),
                {
                    "id",
                    "path",
                    "span",
                    "symbol",
                    "rule",
                    "change_tag",
                    "code",
                    "evidence",
                    "verify",
                },
            )
            self.assertEqual(candidate["id"], f"C{index:02d}")
            self.assertEqual(candidate["path"], "coalesce.py")
            self.assertEqual(candidate["rule"], "PY04_lossy_falsy_coalesce")
            self.assertEqual(candidate["change_tag"], "path-audit")

    def test_lossy_coalesce_bundles_keep_full_expressions_and_select_verify(self):
        truthy_verify = (
            "Can the immediate producer or governing type deliver a falsy value "
            "here? If not, the fallback is dead; if it delivers "
            'a legitimate 0/""/False, the fallback overwrites it. '
            "Counterweight only if the boundary contract equates missing with "
            "empty or the producer already rejects empty. See python.md §6."
        )
        mixed_verify = (
            "Can the immediate producer or governing type deliver a falsy value "
            "at each site? If not, the fallback is dead; if it delivers "
            'a legitimate 0/""/False or a distinct falsy value '
            '(None vs 0, 0 vs ""), the fallback rewrites it. '
            "Counterweight only if the boundary contract equates missing with "
            "empty or the producer already rejects empty. See python.md §6."
        )
        long_key = "x" * 220
        for fallback, verify in [("'default'", truthy_verify), ("0", mixed_verify)]:
            with self.subTest(fallback=fallback):
                self.put(
                    "coalesce.py",
                    f"""
                    def load(data):
                        first = data.get("{long_key}") or 30
                        return (
                            data["name"]
                            or {fallback}
                        )
                    """,
                )
                candidates = self.scan("--paths", "coalesce.py")["candidates"]
                self.assertEqual(len(candidates), 1)
                candidate = candidates[0]
                self.assertEqual(candidate["rule"], "PY04_lossy_falsy_coalesce")
                self.assertEqual(candidate["span"], [2, 5])
                self.assertEqual(candidate["code"], 'data.get("' + "x" * 190)
                self.assertEqual(
                    candidate["evidence"],
                    "2 coalescing lookups in load: "
                    f'line 2: data.get("{long_key}") or 30 -> 30; '
                    f'line 4: data["name"] or {fallback} -> {fallback}',
                )
                self.assertEqual(candidate["verify"], verify)

    def test_lossy_coalesce_groups_emitted_symbols_and_paths_in_source_order(self):
        self.put(
            "a.py",
            """
            class Record:
                entries = {
                    data.get("key") or "default":
                        data.get("value") or "",
                    data.get("next") or 0:
                        data.get("last") or [],
                }

                @property
                def label(self):
                    return self._row.get("label") or ""

                @label.setter
                def label(self, value):
                    return self._row["setter"] or ""
            """,
        )
        self.put(
            "b.py",
            """
            class Record:
                def label(self):
                    return self._row.get("label") or ""
            """,
        )
        candidates = self.scan("--paths", "b.py", "a.py")["candidates"]
        self.assertEqual(
            [(c["id"], c["path"], c["symbol"], c["span"]) for c in candidates],
            [
                ("C01", "a.py", "Record", [3, 6]),
                ("C02", "a.py", "Record.label", [11, 15]),
                ("C03", "b.py", "Record.label", [3, 3]),
            ],
        )
        self.assertEqual(
            candidates[0]["code"],
            'data.get("key") or "default"; data.get("value") or ""; '
            'data.get("next") or 0; data.get("last") or []',
        )
        self.assertEqual(
            candidates[0]["evidence"],
            "4 coalescing lookups in Record: "
            'line 3: data.get("key") or "default" -> \'default\'; '
            'line 4: data.get("value") or "" -> \'\'; '
            'line 5: data.get("next") or 0 -> 0; '
            'line 6: data.get("last") or [] -> []',
        )
        self.assertEqual(
            candidates[1]["evidence"],
            "2 coalescing lookups in Record.label: "
            'line 11: self._row.get("label") or "" -> \'\'; '
            'line 15: self._row["setter"] or "" -> \'\'',
        )
        self.assertEqual(candidates[2]["evidence"], "Lookup coalesces to '' at line 3")

    def test_lossy_coalesce_bundles_only_eligible_sites_after_moved_promotion(self):
        self.init_repo()
        self.put(
            "coalesce.py",
            """
            def original(data):
                return data.get("moved") or 0

            def destination(data):
                ignored = data.get("unchanged") or ""
                return data
            """,
        )
        base = self.commit()
        for remaining in [1, 2]:
            with self.subTest(remaining=remaining):
                extra = '    last = data["last"] or ""\n' if remaining == 2 else ""
                self.put(
                    "coalesce.py",
                    "def original(data):\n"
                    "    return data\n"
                    "\n"
                    "def destination(data):\n"
                    '    ignored = data.get("unchanged") or ""\n'
                    '    first = data.get("first") or []\n'
                    '    moved = data.get("moved") or 30\n'
                    + extra
                    + "    return data\n",
                )
                result = self.scan("--base", base)
                candidates = result["candidates"]
                self.assertEqual(result["summary"]["candidate_count"], 2)
                self.assertEqual(
                    [(c["rule"], c["change_tag"], c["span"]) for c in candidates],
                    [
                        (
                            "PY04_lossy_falsy_coalesce",
                            "changed",
                            [6, 6] if remaining == 1 else [6, 8],
                        ),
                        ("PY10a_moved_defense", "moved-defense", [7, 7]),
                    ],
                )
                coalesced, moved = candidates
                if remaining == 1:
                    self.assertEqual(coalesced["code"], 'data.get("first") or []')
                    self.assertEqual(
                        coalesced["evidence"], "Lookup coalesces to [] at line 6"
                    )
                else:
                    self.assertEqual(
                        coalesced["code"],
                        'data.get("first") or []; data["last"] or ""',
                    )
                    self.assertEqual(
                        coalesced["evidence"],
                        "2 coalescing lookups in destination: "
                        'line 6: data.get("first") or [] -> []; '
                        'line 8: data["last"] or "" -> \'\'',
                    )
                self.assertIn("distinct legitimate one", coalesced["verify"])
                self.assertEqual(moved["code"], 'data.get("moved") or 30')
                self.assertEqual(
                    moved["evidence"],
                    "Lookup coalesces to 30 at line 7; "
                    "same PY04_lossy_falsy_coalesce signature 'moved' "
                    "lost from base coalesce.py:2 (original) (signature match only)",
                )
                self.assertIn("under python.md §6.", moved["verify"])
                for index, candidate in enumerate(candidates, 1):
                    self.assertEqual(candidate["id"], f"C{index:02d}")
                    self.assertEqual(candidate["symbol"], "destination")
                    self.assertFalse(any(key.startswith("_") for key in candidate))
                compact = self.cli("--base", base)
                self.assertIn("PY04_lossy_falsy_coalesce [changed]", compact)
                self.assertIn("PY10a_moved_defense [moved-defense]", compact)

    def test_lossy_coalesce_separates_truthy_and_falsy_fallback_prompts(self):
        prompts = {True: set(), False: set()}
        for lookup in [
            "data['limit']",
            "config.limit",
            "data.get('limit')",
            "raw or data.get('limit')",
        ]:
            for fallback in [
                "30",
                "'default'",
                "True",
                "-1",
                "0",
                "0.0",
                "''",
                "False",
                "[]",
                "{}",
                "()",
                "dict()",
                "list()",
                "set()",
            ]:
                with self.subTest(lookup=lookup, fallback=fallback):
                    self.put(
                        "coalesce.py",
                        f"def load():\n    return {lookup} or {fallback}\n",
                    )
                    candidates = self.scan("--paths", "coalesce.py")["candidates"]
                    self.assertEqual(len(candidates), 1)
                    candidate = candidates[0]
                    self.assertEqual(candidate["rule"], "PY04_lossy_falsy_coalesce")
                    self.assertEqual(candidate["span"], [2, 2])
                    self.assertIn(fallback, candidate["evidence"])
                    self.assertIn("line 2", candidate["evidence"])
                    self.assertIn("Counterweight", candidate["verify"])
                    truthy = fallback in {"30", "'default'", "True", "-1"}
                    prompts[truthy].add(candidate["verify"])
        self.assertTrue(prompts[True].isdisjoint(prompts[False]))
        for expression in [
            "value or 30",
            "value or []",
            "data['limit'] or default",
            "data['limit'] or make_default()",
            "data['limit'] or [1]",
            "data['limit'] or {'x': 1}",
            "data['limit'] or None",
            "data['limit'] and 30",
        ]:
            with self.subTest(expression=expression):
                self.put("coalesce.py", f"def load():\n    return {expression}\n")
                self.assertEqual(self.scan("--paths", "coalesce.py")["candidates"], [])

    def test_dead_parameter_discard_requires_no_other_loads_anywhere(self):
        for body, expected in [
            ("_ = old\nreturn 1", True),
            ("log(old)\n_ = old\nreturn 1", False),
            ("_ = old\nreturn old", False),
            ("_ = old\ndef closure():\n    return old\nreturn closure", False),
            ("_ = old\nreader = lambda: old\nreturn reader", False),
            ("_ = old\nvalues = [old for item in items]\nreturn values", False),
            ("_ = old\n_ = old\nreturn 1", False),
            ("return 1", False),
        ]:
            with self.subTest(body=body):
                self.put(
                    "params.py",
                    'def load(old):\n    """Load."""\n'
                    + textwrap.indent(body, "    ")
                    + "\n",
                )
                candidates = self.scan("--paths", "params.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    self.assertEqual(candidates[0]["rule"], "PY05_dead_param_evasion")
                    self.assertEqual(candidates[0]["span"], [3, 3])
                    self.assertIn("discard", candidates[0]["evidence"])
                    self.assertIn("'old'", candidates[0]["evidence"])
                    self.assertIn("line 3", candidates[0]["evidence"])
                    self.assertIn("caller", candidates[0]["verify"])
                    self.assertIn("Counterweight", candidates[0]["verify"])
        self.put(
            "params.py",
            """
            class Model:
                def load(self, cls):
                    _ = self
                    _ = cls
                    return 1
            """,
        )
        self.assertEqual(self.scan("--paths", "params.py")["candidates"], [])

    def test_dead_parameter_entry_overwrite_stops_at_reads_and_control_flow(self):
        for body, expected in [
            ("old = 1\nreturn old", True),
            ("log()\nold: int = 1\nreturn old", True),
            ("old, other = (1, 2)\nreturn old", True),
            ("old = normalize(old)\nreturn old", False),
            ("log(old)\nold = 1\nreturn old", False),
            ("reader = lambda: old\nold = 1\nreturn reader", False),
            ("import module as old\nold = 1\nreturn old", False),
            ("def old():\n    pass\nold = 1\nreturn old", False),
            ("if ready:\n    log()\nold = 1\nreturn old", False),
            ("for item in items:\n    log()\nold = 1\nreturn old", False),
            ("with context():\n    log()\nold = 1\nreturn old", False),
            (
                "try:\n    work()\nexcept* ValueError:\n    raise\nold = 1\nreturn old",
                False,
            ),
            ("yield 1\nold = 1\nreturn old", False),
            ("old = yield 1\nreturn old", False),
            ("return 1\nold = 1", False),
            ("pass", False),
            ("...", False),
            ('"""Protocol."""\npass\n...\nraise NotImplementedError', False),
            ("raise NotImplementedError('abstract')", False),
        ]:
            with self.subTest(body=body):
                self.put(
                    "params.py",
                    "def load(old):\n" + textwrap.indent(body, "    ") + "\n",
                )
                candidates = self.scan("--paths", "params.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    self.assertEqual(candidates[0]["rule"], "PY05_dead_param_evasion")
                    self.assertIn("entry overwrite", candidates[0]["evidence"])
                    self.assertIn("'old'", candidates[0]["evidence"])
                    self.assertEqual(
                        candidates[0]["span"][0], 3 if body.startswith("log()") else 2
                    )
        self.put(
            "params.py",
            """
            class Model:
                async def load(self, old, /, *, option):
                    old = 1
                    option = 2
                    return old + option
                def protocol(self, arg):
                    ...
            """,
        )
        candidates = self.scan("--paths", "params.py")["candidates"]
        self.assertEqual(
            [c["symbol"] for c in candidates], ["Model.load", "Model.load"]
        )

    def test_dead_parameter_dual_fallback_in_entry_prefix(self):
        for signature, before, rhs, after, expected in [
            ("old, new=None", "", "old", "", True),
            ("old, *, new=None", "log()\n", "normalize(old)", "", True),
            ("old, /, new=None", "", "combine(old, old)", "", True),
            ("old, new=1", "", "old", "", False),
            ("old, new=None", "log(old)\n", "old", "", False),
            ("old, new=None", "", "old", "log(old)\n", False),
            ("old, new=None", "", "old", "reader = lambda: old\n", False),
            ("old, new=None", "if ready:\n    log()\n", "old", "", False),
            ("old, new=None", "return 1\n", "old", "", False),
        ]:
            with self.subTest(signature=signature, before=before, after=after):
                self.put(
                    "params.py",
                    f"def load({signature}):\n"
                    + textwrap.indent(before, "    ")
                    + f"    if new is None:\n        new = {rhs}\n"
                    + textwrap.indent(after, "    ")
                    + "    return new\n",
                )
                candidates = self.scan("--paths", "params.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    candidate = candidates[0]
                    self.assertEqual(candidate["rule"], "PY05_dead_param_evasion")
                    self.assertIn("dual-parameter", candidate["evidence"])
                    self.assertIn("'old'", candidate["evidence"])
                    self.assertIn("'new'", candidate["evidence"])
                    self.assertIn("default None at line 1", candidate["evidence"])
                    self.assertEqual(
                        candidate["span"],
                        [2 + len(before.splitlines()), 3 + len(before.splitlines())],
                    )

    def test_diff_eligibility_excludes_noncontributing_else_branches(self):
        self.init_repo()
        original = textwrap.dedent("""
            def required(raw):
                value = raw or 1
                if value is None:
                    return None
                else:
                    log('before')
            def migrated(old, new=None):
                if new is None:
                    new = old
                else:
                    log('before')
                return new
        """).lstrip("\n")
        self.put("branches.py", original)
        base = self.commit()
        self.put("branches.py", original.replace("'before'", "'after'"))
        self.assertEqual(self.scan("--base", base)["candidates"], [])

    def test_candidates_share_schema_compact_output_and_production_role_gate(self):
        source = """
            def washed():
                try:
                    work()
                except Exception:
                    return {}
            def shadow(raw):
                value = raw or 1
                if value is None:
                    return 0
            def coalesced(data):
                return data.get('count') or 0
            def discarded(old):
                _ = old
                return 1
            def lint_owned_typed(item: Widget):
                return item is None
            def lint_owned_private_write(item):
                item._context = state
            def packed(data, dep):
                data['service'] = dep
            def converted(item):
                return int(float(item))
            def locked():
                with lock:
                    open(path)
        """
        self.put("app.py", source)
        self.put("tests/helpers.py", source)
        result = self.scan("--paths", ".")
        self.assertEqual(result["summary"]["files_scanned"], 2)
        self.assertEqual(result["summary"]["candidate_count"], 7)
        self.assertEqual(
            [c["rule"] for c in result["candidates"]],
            [
                "PY02_error_washing",
                "PY03_default_shadows_check",
                "PY04_lossy_falsy_coalesce",
                "PY05_dead_param_evasion",
                "PY07_state_smuggling",
                "PY08_roundtrip_and_copy",
                "PY09_io_or_tx_in_lock",
            ],
        )
        for candidate in result["candidates"]:
            self.assertEqual(candidate["path"], "app.py")
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
            self.assertEqual(candidate["change_tag"], "path-audit")
            self.assertLessEqual(len(candidate["code"]), 200)
        compact = self.cli("--paths", ".", "--format", "compact")
        self.assertIn(
            "Scanned 2 files (2 cross-checked); 7 candidates, 0 parse errors", compact
        )
        for candidate in result["candidates"]:
            self.assertIn(f"— {candidate['rule']} [path-audit]", compact)
            self.assertIn(f"  Evidence: {candidate['evidence']}", compact)
            self.assertIn(f"  Verify: {candidate['verify']}", compact)

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
                "files_scanned": 3,
                "cross_checked_files": 2,
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
        self.assertEqual(result["summary"]["files_scanned"], 2)
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

    def test_diff_ignores_trailing_whitespace_but_counts_indentation(self):
        self.init_repo()
        path = self.put(
            "edits.py",
            """
            def check(item):
                return hasattr(item, "trailing")


            def load():
                try:
                    work()
                except Exception:
                    return {}


            def realign(item):
                return hasattr(
                    item, "indented")
            """,
        )
        base = self.commit()
        path.write_text(
            path.read_text()
            .replace('"trailing")\n', '"trailing")  \t\n')
            .replace("work()\n", "work()   \n")
            .replace('        item, "indented")', '            item, "indented")')
        )
        tip = self.commit()
        candidates = self.scan("--base", base, "--tip", tip)["candidates"]
        self.assertEqual(
            [(c["rule"], c["span"], c["symbol"]) for c in candidates],
            [("PY01_reflect_default", [13, 14], "realign")],
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
            def inspect(item: Model):
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
            self.assertNotIn("declares attribute", candidate["evidence"])
        self.assertIn("'real' at line 9", candidates[3]["evidence"])
        self.assertIn("'one' at line 3", candidates[4]["evidence"])
        self.assertIn("'two' at line 3", candidates[5]["evidence"])
        self.assertNotIn("declares attribute", candidates[6]["evidence"])

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
