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

    def test_typed_parameter_rhs_check_precedes_assignment_binding(self):
        for assignment in [
            "item = item if item is not None else make_widget()",
            "item: Widget = item if item is not None else make_widget()",
            "item += item if item is not None else make_widget()",
            "(item := item if item is not None else make_widget())",
        ]:
            with self.subTest(assignment=assignment):
                self.put(
                    "typed.py",
                    f"def load(item: Widget):\n    {assignment}\n"
                    "    return item is None\n",
                )
                candidates = self.scan("--paths", "typed.py")["candidates"]
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0]["rule"], "PY06_revalidated_typed_param")
                self.assertEqual(candidates[0]["span"], [2, 2])
                self.assertEqual(candidates[0]["code"], "item is not None")

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
                    self.assertIn("Counterweight", candidate["verify"])

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
                    self.assertIn("diagnostic", candidate["verify"])
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

    def test_error_washing_diff_uses_handler_span_not_try_body(self):
        self.init_repo()
        self.put(
            "errors.py",
            """
            def load():
                try:
                    work()
                except Exception:
                    log()
            """,
        )
        base = self.commit()
        with (self.root / "errors.py").open("a") as source:
            source.write("        return {}\n")
        candidates = self.scan("--base", base)["candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["rule"], "PY02_error_washing")
        self.assertEqual(candidates[0]["span"], [4, 6])
        self.assertEqual(candidates[0]["change_tag"], "changed")
        self.put(
            "errors.py",
            """
            def load():
                try:
                    changed_work()
                except Exception:
                    log()
            """,
        )
        self.assertEqual(self.scan("--base", base)["candidates"], [])

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

    def test_lossy_coalesce_distinguishes_value_loss_from_empty_state_washing(self):
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
                    if fallback in {"30", "'default'", "True", "-1"}:
                        self.assertIn("Semantic Fidelity", candidate["verify"])
                        self.assertIn('0/""/False', candidate["verify"])
                    else:
                        self.assertIn("missing/None", candidate["verify"])
                        self.assertIn("zero/empty", candidate["verify"])
                        self.assertNotIn("Semantic Fidelity", candidate["verify"])
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

    def test_typed_parameter_revalidation_respects_annotations_and_rebinding(self):
        for annotation, default, before, check, expected in [
            ("Widget", "", "", "item is None", True),
            ("pkg.Widget", "", "", "item is not None", True),
            ("Widget", "", "", "isinstance(item, Widget)", True),
            ("pkg.Widget", "", "", "isinstance(item, (str, other.Widget))", True),
            ("list[int]", "", "", "isinstance(item, list)", True),
            ("ManyObjects", "", "", "item is None", True),
            ("OptionalThing", "", "", "item is None", True),
            ("UnionMember", "", "", "item is None", True),
            ("object_record", "", "", "item is None", True),
            ("Optional[Widget]", "", "", "item is None", False),
            ("typing.Optional[Widget]", "", "", "item is None", False),
            ("Widget | None", "", "", "item is None", False),
            ("Union[Widget, str]", "", "", "item is None", False),
            ("Widget", "=None", "", "item is None", False),
            ("Any", "", "", "item is None", False),
            ("typing.Any", "", "", "item is None", False),
            ("object", "", "", "item is None", False),
            ("list[Any]", "", "", "item is None", False),
            ("None", "", "", "item is None", False),
            ("Widget", "", "item = normalize(item)\n", "item is None", False),
            (
                "Widget",
                "",
                "if ready:\n    item = normalize(item)\n",
                "item is None",
                False,
            ),
            ("Widget", "", "", "isinstance(item, Other)", False),
            ("Widget", "", "", "isinstance(other, Widget)", False),
        ]:
            with self.subTest(
                annotation=annotation, default=default, before=before, check=check
            ):
                self.put(
                    "typed.py",
                    f"def load(item: {annotation}{default}):\n"
                    + textwrap.indent(before, "    ")
                    + f"    if {check}:\n        return 1\n    return item\n",
                )
                candidates = self.scan("--paths", "typed.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    candidate = candidates[0]
                    self.assertEqual(candidate["rule"], "PY06_revalidated_typed_param")
                    self.assertEqual(candidate["span"], [2, 2])
                    self.assertIn(annotation, candidate["evidence"])
                    self.assertIn("line 1", candidate["evidence"])
                    self.assertIn("line 2", candidate["evidence"])
                    self.assertIn("Counterweight", candidate["verify"])
        self.put(
            "typed.py",
            """
            class Model:
                async def load(self, item: Widget, /, *, option: list[int]):
                    if item is None:
                        return 1
                    if isinstance(option, list):
                        return 2
                    item = normalize(item)
                    if item is None:
                        return 3
                def outer(self, item: Widget, *args: Widget, **kwargs: Widget):
                    def nested():
                        return item is None
                    reader = lambda: item is None
                    class Inner:
                        value = item is None
                    return args is None or kwargs is None
            """,
        )
        candidates = self.scan("--paths", "typed.py")["candidates"]
        self.assertEqual(
            [c["symbol"] for c in candidates], ["Model.load", "Model.load"]
        )

    def test_string_annotations_use_tokens_and_skip_unparseable_types(self):
        for annotation, check, expected in [
            ("pkg.Widget", "isinstance(item, Widget)", True),
            ("list[int]", "isinstance(item, (tuple, list))", True),
            ("ManyObjects", "item is None", True),
            ("OptionalThing", "item is not None", True),
            ("Widget", "isinstance(item, Other)", False),
            ("typing.Optional[Widget]", "item is None", False),
            ("Widget | None", "item is None", False),
            ("list[Any]", "item is None", False),
            ("Union[Widget, str]", "item is None", False),
            ("object", "item is None", False),
            ("None", "item is None", False),
            ("Widget[", "item is None", False),
            ("", "item is None", False),
        ]:
            with self.subTest(annotation=annotation, check=check):
                self.put(
                    "typed.py",
                    f"def load(item: {annotation!r}):\n    return {check}\n",
                )
                candidates = self.scan("--paths", "typed.py")["candidates"]
                self.assertEqual(len(candidates), int(expected))
                if expected:
                    self.assertEqual(
                        candidates[0]["rule"], "PY06_revalidated_typed_param"
                    )
                    self.assertIn(annotation, candidates[0]["evidence"])
                    self.assertEqual(candidates[0]["span"], [2, 2])

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

    def test_new_probes_share_schema_compact_output_and_production_role_gate(self):
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
            def typed(item: Widget):
                return item is None
        """
        self.put("app.py", source)
        self.put("tests/helpers.py", source)
        result = self.scan("--paths", ".")
        self.assertEqual(result["summary"]["files_scanned"], 2)
        self.assertEqual(result["summary"]["candidate_count"], 5)
        self.assertEqual(
            [c["rule"] for c in result["candidates"]],
            [
                "PY02_error_washing",
                "PY03_default_shadows_check",
                "PY04_lossy_falsy_coalesce",
                "PY05_dead_param_evasion",
                "PY06_revalidated_typed_param",
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
        compact = self.cli("--paths", ".", "--format", "compact")
        self.assertIn(
            "Scanned 2 files (0 cross-checked); 5 candidates, 0 parse errors", compact
        )
        for candidate in result["candidates"]:
            self.assertIn(f"— {candidate['rule']} [path-audit]", compact)
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
