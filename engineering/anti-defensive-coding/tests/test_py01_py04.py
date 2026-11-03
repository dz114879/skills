import textwrap
import unittest

from helpers import ExtractorTestCase


class Py01Py04Tests(ExtractorTestCase):
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


if __name__ == "__main__":
    unittest.main()
