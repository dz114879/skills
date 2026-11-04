import ast
from collections import Counter
from contextlib import chdir, redirect_stdout
import io
import json
import runpy
import sys
import textwrap
from unittest.mock import patch

from helpers import SCRIPT, ExtractorTestCase
import pytest


class Py10DiffReachTests(ExtractorTestCase):
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
        assert {c["path"] for c in moved} == {f"{name}.py" for name in defenses}
        expected_moved = {
            "reflect.py": (
                "item; parameter item: <unannotated> at :4; "
                "same PY01_reflect_default signature "
                "'value' lost from base reflect.py:2 (original) "
                "(signature match only)",
                "python.md §1",
            ),
            "handler.py": (
                "Handler catches (TypeError, KeyError) at :8; "
                "broad/multi-exception handler has a non-raising exit; "
                "same PY02_error_washing signature KeyError, TypeError "
                "lost from base handler.py:4 (original) (signature match only)",
                "python.md §4",
            ),
            "coalesce.py": (
                "fallback 30; "
                "same PY04_lossy_falsy_coalesce signature 'value' "
                "lost from base coalesce.py:2 (original) (signature match only)",
                "python.md §6",
            ),
        }
        for candidate in moved:
            expected_evidence, base_section = expected_moved[candidate["path"]]
            assert candidate["symbol"] == "destination"
            assert candidate["change_tag"] == "moved-defense"
            assert candidate["evidence"] == expected_evidence
            assert (
                candidate["verify"]
                == "Open the origin first: did it guard the same producer, "
                "receiver or exception source? If so, did this callee become "
                "the owner, or is it a relocated hedge (a genuine boundary is "
                "a Counterweight)? If not, verify this defense on its own "
                f"under {base_section}. See python.md §4."
            )
        assert result["candidates"] == moved

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
        assert "R" in self.git("diff", "-M", "--name-status", base)
        candidates = self.scan("--base", base)["candidates"]
        assert len(candidates) == 1
        assert candidates[0]["rule"] == "PY10a_moved_defense"
        assert candidates[0]["symbol"] == "Owner.destination"
        assert candidates[0]["path"] == "new.py"
        assert (
            "item; parameter item: <unannotated> at :45; "
            "same PY01_reflect_default signature "
            "'value' lost from base old.py:43 (Owner.original) "
            "(signature match only)" in candidates[0]["evidence"]
        )
        self.put(
            "new.py",
            padding
            + 'class Owner:\n    def original(self, item):\n        return getattr(item, "value", None)\n\n    def destination(self, item):\n        return hasattr(item, "value")\n',
        )
        assert not [
            c
            for c in self.scan("--base", base)["candidates"]
            if c["rule"] == "PY10a_moved_defense"
        ]

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
        assert [(c["path"], c["rule"]) for c in candidates] == [
            ("new0.py", "PY10a_moved_defense"),
            ("new1.py", "PY10a_moved_defense"),
            ("new2.py", "PY10a_moved_defense"),
        ]
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
        assert not [
            c
            for c in self.scan("--base", base)["candidates"]
            if c["rule"] == "PY10a_moved_defense"
        ]

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
            tip = history(lost, gained)
            candidates = self.scan("--base", base, "--tip", tip)["candidates"]
            moved = {
                c["symbol"]: c["evidence"]
                for c in candidates
                if c["rule"] == "PY10a_moved_defense"
            }
            assert len(candidates) == len(gained)
            assert set(moved) == set(expected)
            for symbol, names in expected.items():
                assert "Handler catches Exception at :" in moved[symbol]
                assert (
                    "; same PY02_error_washing signature Exception lost from "
                    in moved[symbol]
                )
                assert "signature match only" in moved[symbol]
                for name in names:
                    assert origins[name] in moved[symbol]
            assert {
                c["symbol"]
                for c in candidates
                if c["rule"] == "PY02_error_washing" and c["change_tag"] == "changed"
            } == set(gained) - set(expected)

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
            self.put(
                "app.py",
                "def original(item):\n    return item.value\n\n\n"
                "def new_name(item, other=None):\n" + body,
            )
            tip = self.commit()
            candidates = self.scan("--base", base, "--tip", tip)["candidates"]
            assert [
                (c["rule"], c["change_tag"], c["span"]) for c in candidates
            ] == expected
            for candidate in candidates:
                if candidate["rule"] == "PY10a_moved_defense":
                    assert (
                        "; same PY01_reflect_default signature 'value' "
                        "lost from base app.py:2 (original)" in candidate["evidence"]
                    )

    def test_moved_defense_handles_non_named_exception_types_without_crashing(self):
        base = self.use_git_fixture(
            "moved_non_named_exc",
            {
                "app.py": (
                    "def original():\n    try:\n        action()\n"
                    "    except (Exception, extra()):\n        return None\n\n"
                    "def destination():\n    action()\n"
                )
            },
            working_files={
                "app.py": (
                    "def original():\n    action()\n\n"
                    "def destination():\n    try:\n        action()\n"
                    "    except (extra(), Exception):\n        return None\n"
                )
            },
        )
        moved = [
            c
            for c in self.scan("--base", base)["candidates"]
            if c["rule"] == "PY10a_moved_defense"
        ]
        assert len(moved) == 1
        assert moved[0]["symbol"] == "destination"

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
            self.put(
                "tests/test_case.py",
                "def test_case(self, obj, other, monkeypatch):\n"
                + textwrap.indent(body, "    ")
                + "\n",
            )
            candidates = self.scan("--paths", "tests/test_case.py")["candidates"]
            assert len(candidates) == int(expected), (label, candidates)
            if expected:
                assert candidates[0]["rule"] == "PY10b_test_smell"
                assert candidates[0]["symbol"] == "test_case"
                assert "Counterweight" in candidates[0]["verify"]

    def test_test_smell_verify_points_to_its_subprobe_catalogue(self):
        for body, pointer in [
            ("obj._legacy_read()", "tests.md §4"),
            ('assert "class" in Path("app.py").read_text()', "tests.md §6"),
            ("r = obj.items()\nr.append(1)\nobj.items()", "tests.md §2"),
            ('with patch("app.execute"):\n    execute()', "tests.md §5"),
        ]:
            self.put(
                "tests/test_case.py",
                "def test_case():\n" + textwrap.indent(body, "    ") + "\n",
            )
            candidates = self.scan("--paths", "tests/test_case.py")["candidates"]
            assert len(candidates) == 1
            assert candidates[0]["rule"] == "PY10b_test_smell"
            assert pointer in candidates[0]["verify"]

    def test_getter_recall_is_checked_before_rebinding_its_result(self):
        for replacement, expected in [
            ("result = obj.items()", True),
            ("result: list = obj.items()", True),
            ("result = list(obj.items())", True),
            ("result = other.items()", False),
            ("result = []", False),
        ]:
            self.put(
                "tests/test_items.py",
                "def test_items():\n"
                "    result = obj.items()\n"
                "    result.append(1)\n"
                f"    {replacement}\n",
            )
            candidates = self.scan("--paths", "tests/test_items.py")["candidates"]
            assert len(candidates) == int(expected), candidates
            if expected:
                assert candidates[0]["rule"] == "PY10b_test_smell"
                assert candidates[0]["symbol"] == "test_items"
                assert candidates[0]["span"] == [2, 4]

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
            self.put("tests/test_patch.py", source)
            candidates = self.scan("--paths", "tests/test_patch.py")["candidates"]
            assert [(c["rule"], c["symbol"], c["span"]) for c in candidates] == [
                ("PY10b_test_smell", "test_patch", span)
            ]

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
            self.put(
                "tests/test_patch.py",
                "def test_patch(monkeypatch):\n" + textwrap.indent(body, "    ") + "\n",
            )
            candidates = self.scan("--paths", "tests/test_patch.py")["candidates"]
            assert [(c["rule"], c["symbol"], c["span"]) for c in candidates] == (
                [("PY10b_test_smell", "test_patch", span)] if span else []
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
            self.put(
                "tests/test_patch.py",
                "def test_patch(monkeypatch):\n" + textwrap.indent(body, "    ") + "\n",
            )
            candidates = self.scan("--paths", "tests/test_patch.py")["candidates"]
            assert [(c["rule"], c["symbol"], c["span"]) for c in candidates] == (
                [("PY10b_test_smell", "test_patch", span)] if span else []
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
            self.put("tests/test_patch.py", source)
            candidates = self.scan("--paths", "tests/test_patch.py")["candidates"]
            assert [(c["rule"], c["symbol"], c["span"]) for c in candidates] == (
                [("PY10b_test_smell", "test_patch", span)] if span else []
            )

    def test_starting_a_bound_patcher_makes_the_sut_call_diff_eligible(self):
        self.init_repo()
        for previous, spans in [
            ("", [[2, 4]]),
            ("    p.start()\n    execute()\n    p.stop()\n", [[2, 4], [2, 7]]),
        ]:
            prefix = 'def test_patch():\n    p = patch("app.execute")\n' + previous
            self.put("tests/test_patch.py", prefix + "    execute()\n")
            base = self.commit()
            self.put("tests/test_patch.py", prefix + "    p.start()\n    execute()\n")
            candidates = self.scan("--base", base)["candidates"]
            assert [
                (c["rule"], c["symbol"], c["span"], c["change_tag"]) for c in candidates
            ] == [("PY10b_test_smell", "test_patch", spans[-1], "changed")]
            candidates = self.scan("--paths", "tests/test_patch.py")["candidates"]
            assert [(c["rule"], c["symbol"], c["span"]) for c in candidates] == [
                ("PY10b_test_smell", "test_patch", span) for span in spans
            ]

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
            self.put("app.py", f"if {expression}:\n    execute()\n")
            self.put("tests/test_app.py", f"assert {expression}\n")
            candidates = self.scan("--paths", ".")["candidates"]
            assert len(candidates) == int(expected)
            if expected:
                assert candidates[0]["rule"] == "PY10b_test_smell"
                assert candidates[0]["path"] == "app.py"
                assert candidates[0]["symbol"] == "<module>"
                assert candidates[0]["span"] == [1, 1]

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
        assert {c["symbol"] for c in audit} == {"test_patched", "test_read"}
        self.put("tests/test_app.py", original + "    obj.items()\n")
        changed = self.scan("--base", base)["candidates"]
        assert len(changed) == 1
        assert changed[0]["symbol"] == "test_getter"
        assert changed[0]["change_tag"] == "changed"

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
            self.put(
                "tests/test_read.py",
                setup + '\ndef test_read():\n    assert "class" in TEXT\n',
            )
            candidates = self.scan("--paths", "tests/test_read.py")["candidates"]
            assert len(candidates) == int(expected), candidates
            if expected:
                assert candidates[0]["rule"] == "PY10b_test_smell"
                assert candidates[0]["symbol"] == "test_read"
                assert "raw source/documentation text" in candidates[0]["evidence"]

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
        assert len(candidates) == 3
        assert {c["change_tag"] for c in candidates} == {"cross-test"}
        assert result["summary"]["cross_checked_files"] == 3
        for candidate in candidates:
            assert "prod_calls=0" in candidate["evidence"]
            assert "test_calls=1" in candidate["evidence"]
            if candidate["symbol"] == "shared":
                assert "ambiguous_defs: 2" in candidate["evidence"]
        for reference in (
            "lonely()",
            "callbacks = [lonely]",
            "from app import lonely as local\nlocal()",
        ):
            self.put("caller.py", reference + "\nshared()\n")
            assert not [
                c
                for c in self.scan("--paths", "app.py")["candidates"]
                if c["rule"] == "PY10c_cross_test_reachability"
            ]

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
            self.put("app.py", f"class Runner(Base):\n    {method}\n")
            candidates = self.scan("--paths", "app.py")["candidates"]
            assert [(c["symbol"], c["change_tag"]) for c in candidates] == (
                [("Runner.execute", "cross-test")] if flagged else []
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
            self.put("caller.py", caller + "\n")
            candidates = self.scan("--paths", "app.py")["candidates"]
            assert [c["code"] for c in candidates] == (["3"] if flagged else [])

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
            self.put("app.py", definition.format(recursion))
            self.put("caller.py", caller + "\n")
            candidates = self.scan("--paths", "app.py")["candidates"]
            assert [c["code"] for c in candidates] == (["0"] if flagged else [])

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
            self.put("app.py", fixture.format(body))
            self.put("caller.py", caller + "\n")
            candidates = self.scan("--paths", "app.py")["candidates"]
            assert [c["code"] for c in candidates] == (["2"] if flagged else [])
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
            self.put(
                "app.py",
                f"class Tree:\n    {header}\n        for child in node.children:\n            {call}\n        return indent + depth\n",
            )
            candidates = self.scan("--paths", "app.py")["candidates"]
            assert [c["code"] for c in candidates] == ["2"]

    def test_rebound_import_aliases_are_escapes_instead_of_resolved_calls(self):
        self.init_repo()
        self.put("app.py", "def configure(flag=False):\n    return flag\n")
        self.put("tests/test_app.py", "configure()\n")
        for binding in ("from other import unrelated as local", "local = unrelated"):
            for nested in (False, True):
                caller = f"from app import configure as local\n{binding}\nlocal(True)\n"
                if nested:
                    caller = "def caller():\n" + textwrap.indent(caller, "    ")
                self.put("caller.py", caller)
                assert self.scan("--paths", "app.py")["candidates"] == []
                as_test = self.scan("--paths", "app.py", "--test-glob", "caller.py")[
                    "candidates"
                ]
                assert len(as_test) == 1
                assert as_test[0]["change_tag"] == "cross-test"
                assert "test_calls=1, test_refs=1" in as_test[0]["evidence"]
        self.put("caller.py", "from app import configure as local\nlocal(True)\n")
        resolved = self.scan("--paths", "app.py")["candidates"]
        assert len(resolved) == 1
        assert resolved[0]["change_tag"] == "path-audit"
        assert "uniform: True" in resolved[0]["evidence"]

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
            self.put("app.py", source)
            self.put("caller.py", calls + "\n")
            candidates = [
                c
                for c in self.scan("--paths", "app.py")["candidates"]
                if c["rule"] == "PY10c_cross_test_reachability"
            ]
            assert len(candidates) == int(uniform is not None), (label, candidates)
            if uniform is not None:
                assert candidates[0]["change_tag"] == "path-audit"
                assert f"uniform: {uniform}" in candidates[0]["evidence"]
                assert "prod calls" in candidates[0]["evidence"]
                assert candidates[0]["code"] == "False"
                assert "Counterweight" in candidates[0]["verify"]

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
            self.put("app.py", f"def configure({signature}):\n    return {body}\n")
            self.put("caller.py", calls)
            assert self.scan("--paths", "app.py")["candidates"] == [
                {
                    "id": "C01",
                    "path": "app.py",
                    "symbol": "configure",
                    "rule": "PY10c_cross_test_reachability",
                    "change_tag": "path-audit",
                    "span": [1, 1],
                    "code": "False",
                    "evidence": "Param 'flag'=False: 2 prod calls; uniform: True",
                    "verify": "Does every immediate production binding, recursion included, "
                    "fix this value, so the override is dead while the default's policy stays? "
                    "Is it an export or extension contract (Counterweight)? See tests.md §4.",
                }
            ]

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
        assert candidates == [
            {
                "id": "C01",
                "path": "app.py",
                "symbol": "configure",
                "rule": "PY10c_cross_test_reachability",
                "change_tag": "path-audit",
                "span": [2, 6],
                "code": 'False; "policy"',
                "evidence": "Params 'zulu'=False (uniform: True), "
                "'alpha'='policy' (uniform: <DEFAULT>): 2 prod calls",
                "verify": "Does every immediate production binding, recursion included, "
                "fix each value, so the override is dead while the default's policy stays? "
                "Is it an export or extension contract (Counterweight)? See tests.md §4.",
            }
        ]

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
            if changed:
                self.put(
                    "app.py",
                    source.replace(
                        "return zulu, varying, alpha", "return alpha, varying, zulu"
                    ),
                )
            candidates = self.scan("--base", base)["candidates"]
            assert len(candidates) == 1
            candidate = candidates[0]
            assert candidate["rule"] == "PY10c_cross_test_reachability"
            assert candidate["symbol"] == "Panel.configure"
            assert candidate["path"] == "app.py"
            assert candidate["change_tag"] == "changed"
            assert candidate["span"] == [5, 8]
            assert candidate["code"] == 'False; "policy"'
            unchanged = (
                "" if changed else "; definition unchanged; triggered by deleted call"
            )
            assert (
                candidate["evidence"] == "Params 'zulu'=False (uniform: True), "
                "'alpha'='policy' (uniform: <DEFAULT>): 2 prod calls"
                "; lost call(s): base caller.py:3 (prod)"
                f"{unchanged}; enclosing class bases: ui.View (not defined in repo)"
            )
            assert "fix each value" in candidate["verify"]

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
            own = module == "app.py"
            self.put("app.py", definitions + (exports + "\n" if own else ""))
            self.put("exports.py", "" if own else exports + "\n")
            candidates = self.scan("--paths", "app.py")["candidates"]
            assert [(c["symbol"], c["code"]) for c in candidates] == (
                [("sibling", "False")]
                if exported
                else [("configure", "False"), ("sibling", "False")]
            )

    def test_cross_exported_function_skips_reach_but_not_unexported_namesake(self):
        self.init_repo()
        self.put("other.py", "def helper():\n    return 2\n")
        self.put("tests/test_app.py", "helper()\n")
        for exports, flagged in [
            ('__all__ = ["helper"]\n', ["other.py"]),
            ("", ["app.py", "other.py"]),
        ]:
            self.put("app.py", exports + "def helper():\n    return 1\n")
            candidates = self.scan("--paths", "app.py", "other.py")["candidates"]
            assert [(c["path"], c["symbol"], c["change_tag"]) for c in candidates] == [
                (path, "helper", "cross-test") for path in flagged
            ]

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
            self.put("pkg/__init__.py", package + "\n")
            candidates = self.scan("--paths", "pkg/core.py")["candidates"]
            assert [(c["symbol"], c["change_tag"]) for c in candidates] == (
                [] if exported else [("helper", "cross-test")]
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
            ("Client._fetch_raw", "cross-test", "def _fetch_raw(self):"),
        ]
        public = [
            ("Client.send", "path-audit", "3"),
            ("Client.fetch", "cross-test", "def fetch(self):"),
        ]
        # The package module mentions no method name; `__all__` admits it.
        for package, expected in [
            ('from ._client import Client\n__all__ = ["Client"]', private),
            ("from ._client import Client", sorted(public + private)),
        ]:
            self.put("pkg/__init__.py", package + "\n")
            candidates = self.scan("--paths", "pkg/_client.py")["candidates"]
            assert sorted(
                (c["symbol"], c["change_tag"], c["code"]) for c in candidates
            ) == sorted(expected)

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
        assert [(c["symbol"], c["change_tag"]) for c in candidates] == [
            ("Panel.refresh", "cross-test"),
            ("Panel.resize", "path-audit"),
            ("refresh", "cross-test"),
            ("lonely", "cross-test"),
        ]
        method, default, function, lonely = (c["evidence"] for c in candidates)
        external = "enclosing class bases: ui.View (not defined in repo)"
        for evidence in (method, function):
            assert evidence.startswith("defs=2, prod_calls=0, prod_refs=0, ")
            assert "test_calls=1, test_refs=0" in evidence
            assert "ambiguous_defs: 2" in evidence
        assert lonely == "defs=1, prod_calls=0, prod_refs=0, test_calls=1, test_refs=0"
        assert "ambiguous_defs" not in lonely
        assert external in method
        assert external in default
        assert "uniform: <DEFAULT>" in default
        assert "enclosing class bases" not in function
        assert "enclosing class bases" not in lonely
        for candidate in (candidates[0], candidates[2], candidates[3]):
            assert "Counts are terminal-name aggregates" in candidate["verify"]
            assert (
                "test references are not attributed to a specific definition"
                in candidate["verify"]
            )

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
            self.put("caller.py", remaining)
            result = self.scan("--base", base)
            candidates = [
                c
                for c in result["candidates"]
                if c["rule"] == "PY10c_cross_test_reachability"
            ]
            assert [c["symbol"] for c in candidates] == expected
            if expected:
                assert candidates[0]["change_tag"] == "orphaned"
                assert candidates[0]["path"] == "app.py"
        (self.root / "caller.py").unlink()
        deleted = self.scan("--base", base)
        assert deleted["summary"]["files_scanned"] == 1
        assert deleted["summary"]["cross_checked_files"] == 1
        assert deleted["candidates"][0]["change_tag"] == "orphaned"
        self.put("tests/test_app.py", "orphan()\n")
        assert self.scan("--base", base)["candidates"][0]["change_tag"] == "cross-test"

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
            self.put("other.py", other)
            candidates = self.scan("--base", base)["candidates"]
            assert [(c["path"], c["symbol"], c["change_tag"]) for c in candidates] == [
                (path, "shared", "cross-test") for path in flagged
            ]
            for candidate in candidates:
                assert "test_calls=1" in candidate["evidence"]
                assert "ambiguous_defs: 3" in candidate["evidence"]

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
        for path, source in base_files.items():
            self.put(path, source)
        base = self.commit()
        for label, changes, lost in cases:
            for path in tip_only:
                (self.root / path).unlink(missing_ok=True)
            for path, source in base_files.items():
                self.put(path, source)
            for path, source in changes.items():
                if source is None:
                    (self.root / path).unlink()
                else:
                    self.put(path, source)
            candidates = self.scan("--base", base)["candidates"]
            assert [(c["path"], c["symbol"], c["code"]) for c in candidates] == (
                [("app.py", "helper", "3")] if lost else []
            ), label
            if lost:
                evidence = candidates[0]["evidence"]
                assert "uniform: <DEFAULT>" in evidence
                assert f"lost call(s): {lost}" in evidence
                assert "definition unchanged" in evidence

    def test_cross_snapshot_isolation_exact_tip_and_default_merge_base(self):
        self.init_repo()
        self.put("app.py", "def reachable():\n    return 1\n")
        self.put("caller.py", "value = 0\n")
        self.put("tests/test_app.py", "reachable()\n")
        base = self.commit()
        self.put("app.py", "def reachable():\n    return 2\n")
        tip = self.commit()
        for flags in [("--tip", tip), ("--merge-base",)]:
            self.put("caller.py", "value = 0\n")
            committed = self.scan("--base", base, *flags)
            self.put("caller.py", "reachable()\n")
            isolated = self.scan("--base", base, *flags)
            assert isolated == committed
            candidates = isolated["candidates"]
            assert len(candidates) == 1
            assert candidates[0]["change_tag"] == "cross-test"
            assert "prod_calls=0" in candidates[0]["evidence"]
        assert self.scan("--base", base)["candidates"] == []

    def test_cross_cli_parses_each_active_file_at_most_once(self):
        base, tip = self.use_git_fixture(
            "cross_cli_once",
            {
                "app.py": "def reachable():\n    return 1\n",
                "caller.py": "from app import reachable as local\nlocal()\n",
                "unrelated.py": "value = 1\n",
                "broken.py": "reachable(\n",
            },
            {
                "app.py": "def reachable():\n    return 2\n",
                "broken.py": "reachable((\n",
            },
        )
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
            pytest.raises(SystemExit) as exit_,
        ):
            runpy.run_path(str(SCRIPT), run_name="__main__")
        assert exit_.value.code == 0
        counts = Counter(call.kwargs.get("filename") for call in parse.call_args_list)
        assert counts["base:app.py"] == 1
        assert counts["app.py"] == 1
        assert counts["caller.py"] == 1
        assert counts["broken.py"] == 1
        assert counts["unrelated.py"] == 0
        assert all(
            count <= 1
            for filename, count in counts.items()
            if filename and not filename.startswith("base:")
        )
        result = json.loads(stdout.getvalue())
        assert result["summary"]["cross_checked_files"] == 4
        assert result["summary"]["parse_error_count"] == 2
        assert sum(e["error"].startswith("base: ") for e in result["parse_errors"]) == 1

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
        assert [c["symbol"] for c in result["candidates"]] == ["ordinary"]
        base = self.commit()
        self.put("app.py", source + "value = 1\n")
        assert self.scan("--base", base)["summary"]["cross_checked_files"] == 0
        self.put("app.py", source.replace("return 1", "return 2", 1))
        assert self.scan("--base", base)["candidates"][0]["symbol"] == "ordinary"
        self.put("filtered.py", "@app.getaway\ndef viable():\n    return 1\n")
        self.put("tests/test_filtered.py", "viable()\n")
        assert (
            self.scan("--paths", "filtered.py")["candidates"][0]["symbol"] == "viable"
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
        assert result["summary"]["cross_checked_files"] == 2
        assert result["candidates"][0]["change_tag"] == "cross-test"
        assert self.scan("--paths", "app.py", "--exclude", "omit/*")["candidates"] == []

    def test_base_decode_failure_is_reported_without_disabling_post_analysis(self):
        base = self.use_git_fixture(
            "base_decode_failure",
            {
                "app.py": b"# coding: utf-8\nvalue = '\xff'\n",
                "deleted.py": "def broken(:\n",
            },
            working_files={
                "app.py": 'hasattr(item, "value")\n',
                "deleted.py": None,
            },
        )
        result = self.scan("--base", base)
        assert result["summary"]["files_scanned"] == 2
        assert result["summary"]["parse_error_count"] == 2
        assert all(
            error["error"].startswith("base: ") for error in result["parse_errors"]
        )
        assert result["candidates"][0]["rule"] == "PY01_reflect_default"

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
        assert [(c["symbol"], c["change_tag"]) for c in orphaned] == [
            ("idle", "orphaned")
        ]
        lost = "lost call(s): base tests/test_app.py:2 (test)"
        assert lost in orphaned[0]["evidence"]
        assert "definition unchanged" in orphaned[0]["evidence"]
        self.put(
            "app.py",
            "async def callback():\n    return 2\n"
            "def configure(flag=False, *, mode=None):\n    return flag, mode, 2\n"
            "def idle():\n    return 2\n",
        )
        candidates = self.scan("--base", base)["candidates"]
        callback = next(c for c in candidates if c["symbol"] == "callback")
        assert callback["change_tag"] == "cross-test"
        assert "test_calls=0, test_refs=1" in callback["evidence"]
        assert "lost call(s)" not in callback["evidence"]
        idle = next(c for c in candidates if c["symbol"] == "idle")
        assert idle["change_tag"] == "orphaned"
        assert lost in idle["evidence"]
        assert "definition unchanged" not in idle["evidence"]
        defaults = [c for c in candidates if c["symbol"] == "configure"]
        assert len(defaults) == 1
        assert defaults[0]["change_tag"] == "changed"
        assert defaults[0]["code"] == "False; None"
        self.put("exports.py", 'from app import configure\n__all__ = ["configure"]\n')
        assert not [
            c
            for c in self.scan("--base", base)["candidates"]
            if c["symbol"] == "configure"
        ]

    def test_cross_reach_code_keeps_only_sync_and_async_signatures(self):
        self.init_repo()
        source = (
            "def lost(value: int):\n"
            '    """This body is never code context."""\n'
            "    return value + 1\n"
            "async def callback(\n"
            "    café: str,\n"
            "):\n"
            '    """Async body also stays out of code context."""\n'
            "    return café\n"
            'def short(): return "same-line body"\n'
        )
        self.put("app.py", source)
        self.put("caller.py", "lost(1)\n")
        self.put("tests/test_app.py", 'callback("x")\nshort()\n')
        base = self.commit()
        candidates = self.scan("--paths", "app.py")["candidates"]
        assert [(c["symbol"], c["change_tag"], c["code"]) for c in candidates] == [
            ("callback", "cross-test", "async def callback( café: str, ):"),
            ("short", "cross-test", "def short():"),
        ]
        self.put("caller.py", "")
        self.put("app.py", source.replace("return café", 'return café + "!"'))
        candidates = self.scan("--base", base)["candidates"]
        assert [(c["symbol"], c["change_tag"], c["code"]) for c in candidates] == [
            ("lost", "orphaned", "def lost(value: int):"),
            ("callback", "cross-test", "async def callback( café: str, ):"),
        ]
