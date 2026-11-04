import re
import textwrap

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
            assert [c["symbol"] for c in candidates] == ["Hedge.read"]

    def test_reflection_class_names_resolve_only_at_module_scope(self):
        for wrapper, suffix, visible in (
            ("def factory():", "", False),
            ("class Factory:", "", False),
            ("if supported:", "", True),
            ("try:", "except ImportError:\n    pass\n", True),
        ):
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
            assert ("Model.read" in candidates) == (not visible)
            evidence = candidates["inspect"]["evidence"]
            assert "item: Record" in evidence
            if visible:
                assert "'cache' at :6" in evidence
            else:
                assert "class " not in evidence
                assert "'cache' at :6" not in evidence

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
            assert [c["symbol"] for c in candidates] == expected

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
            assert [c["symbol"] for c in candidates] == ["Model.read"]

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
            assert len(candidates) == int(expected)

    def test_memo_guard_distinguishes_builtin_constructor_from_same_named_method(self):
        for value, expected in (("self.bytes()", []), ("bytes()", ["Model.read"])):
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
            assert [c["symbol"] for c in candidates] == expected

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
        assert len(result["candidates"]) == 3
        first, second, third = result["candidates"]
        assert first["symbol"] == "Second.read.nested"
        assert "self" in first["evidence"]
        assert "class Second" in first["evidence"]
        assert "First" not in first["evidence"]
        assert "'value' at :8" in first["evidence"]
        assert "'value' at :10" in first["evidence"]
        assert "'other' at :11" in second["evidence"]
        assert third["symbol"] == "<module>"
        assert "local" in third["evidence"]
        assert "class " not in third["evidence"]
        assert "Second" not in third["evidence"]

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
            assert len(candidates) == 1
            evidence = candidates[0]["evidence"]
            if known:
                assert re.search(r"item: Record.*:3", evidence)
                assert "'value' at :2" in evidence
            else:
                assert "class " not in evidence
                assert "Record" not in evidence

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
        assert "self" in evidence["Record.read"]
        assert "class Record" in evidence["Record.read"]
        assert re.search(r"bases:.*Base.*external.Mixin", evidence["Record.read"])
        assert "'cache' at :8" in evidence["Record.read"]
        assert re.search(r"item: Record.*:13", evidence["typed"])
        assert "'value' at :4" in evidence["typed"]
        assert "'value' at :4" in evidence["forward"]
        assert re.search(r"item: Any.*:15", evidence["unknown"])
        assert "class " not in evidence["unknown"]
        assert "item.child" in evidence["member"]
        assert re.search(r"item: Record.*:17", evidence["member"])
        assert "class " not in evidence["member"]
        assert "'value' at :4" not in evidence["member"]
        assert re.search(
            r"local binding cls = load_plugin\(\).*:20", evidence["plugin"]
        )
        assert "control-flow unresolved" in evidence["plugin"]
        assert "class " not in evidence["plugin"]
        assert re.search(r"item = Record\(\).*:23", evidence["assigned"])
        assert "control-flow unresolved" in evidence["assigned"]
        assert "class " not in evidence["assigned"]
        for facts in evidence.values():
            assert "Unrelated" not in facts

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
        [candidate] = self.scan("--paths", ".")["candidates"]
        assert candidate["span"] == [12, 18]
        assert candidate["evidence"] == (
            '7 sites: :12 hasattr(item, "foreign"); :13 hasattr(item, "child"); '
            ':14 hasattr(item, "nested"); :15 hasattr(item, "real"); '
            ':16 hasattr(item, "one"); :17 hasattr(item, "two"); '
            ':18 hasattr(item, "uninitialized")'
            " | item; parameter item: Model at :11; "
            "class Model (bases: object (implicit)); "
            "declares 'real' at :9; declares 'one' at :3; declares 'two' at :3"
        )

    def test_reflection_preserves_singletons_union_annotations_and_module_sites(self):
        from adc_scanner.registry import verify_text

        self.put(
            "reflect.py",
            """
            getattr(x, "first", None)
            hasattr(x, "second")

            def single(x):
                return getattr(x, "value", None)
            def union(x: Foo | None):
                return hasattr(x, "value")
            """,
        )
        candidates = self.scan("--paths", "reflect.py")["candidates"]
        assert [
            (c["symbol"], c["span"], c["code"], c["evidence"]) for c in candidates
        ] == [
            ("<module>", [1, 1], 'getattr(x, "first", None)', "x"),
            ("<module>", [2, 2], 'hasattr(x, "second")', "x"),
            (
                "single",
                [5, 5],
                'getattr(x, "value", None)',
                "x; parameter x: <unannotated> at :4",
            ),
            (
                "union",
                [7, 7],
                'hasattr(x, "value")',
                "x; parameter x: Foo | None at :6",
            ),
        ]
        for candidate in candidates:
            assert candidate["rule"] == "PY01_reflect_default"
            assert candidate["change_tag"] == "path-audit"
            assert candidate["verify"] == verify_text("PY01")

    def test_reflection_bundles_merge_receiver_bindings_and_declarations(self):
        self.put(
            "reflect.py",
            """
            class Record:
                first: int
                second = 0
                def __init__(self):
                    self.first = 1
                def read(self):
                    getattr(self, "first", None)
                    return hasattr(self, "second")
            def inspect(item: Record, other):
                getattr(item, "first", None)
                hasattr(other, "flag")
                hasattr(item, "second")
                return getattr(item, "first", 0)
            def plugin():
                item = load_plugin()
                getattr(item, "first", None)
                return hasattr(item, "second")
            """,
        )
        candidates = self.scan("--paths", "reflect.py")["candidates"]
        assert [(c["symbol"], c["span"], c["code"]) for c in candidates] == [
            ("Record.read", [7, 8], 'getattr(self, "first", None)'),
            ("inspect", [10, 13], 'getattr(item, "first", None)'),
            ("plugin", [16, 17], 'getattr(item, "first", None)'),
        ]
        assert candidates[0]["evidence"] == (
            '2 sites: :7 getattr(self, "first", None); :8 hasattr(self, "second")'
            " | self; class Record at :1; class Record (bases: object (implicit)); "
            "declares 'first' at :2; declares 'first' at :5; declares 'second' at :3"
        )
        assert candidates[1]["evidence"] == (
            '4 sites: :10 getattr(item, "first", None); :11 hasattr(other, "flag"); '
            ':12 hasattr(item, "second"); :13 getattr(item, "first", 0)'
            " | item; parameter item: Record at :9; "
            "class Record (bases: object (implicit)); "
            "declares 'first' at :2; declares 'first' at :5; declares 'second' at :3; "
            "other; parameter other: <unannotated> at :9"
        )
        assert candidates[2]["evidence"] == (
            '2 sites: :16 getattr(item, "first", None); :17 hasattr(item, "second")'
            " | item; local binding item = load_plugin() at :15 "
            "(control-flow unresolved)"
        )
        assert len({candidate["verify"] for candidate in candidates}) == 1
        for candidate in candidates:
            assert candidate["rule"] == "PY01_reflect_default"
            assert not any(key.startswith("_") for key in candidate)

    def test_reflection_folds_same_line_nested_calls_before_covering_the_span(self):
        self.put(
            "reflect.py",
            """
            def load(x):
                value = getattr(getattr(x, "a", None), "b", None)
                return hasattr(x, "c")
            """,
        )
        [candidate] = self.scan("--paths", "reflect.py")["candidates"]
        assert candidate["span"] == [2, 3]
        assert candidate["code"] == 'getattr(getattr(x, "a", None), "b", None)'
        assert candidate["evidence"] == (
            '3 sites: :2 getattr(getattr(x, "a", None), "b", None)'
            ' [inner: getattr(x, "a", None)]; :3 hasattr(x, "c")'
            " | x; parameter x: <unannotated> at :1"
        )

    def test_reflection_bundles_keep_full_member_expressions_before_code_clipping(self):
        attribute = "x" * 220
        first = f'getattr(item, "{attribute}", None)'
        self.put(
            "reflect.py",
            f"def load(item):\n    {first}\n    return hasattr(item, 'last')\n",
        )
        [candidate] = self.scan("--paths", "reflect.py")["candidates"]
        assert candidate["span"] == [2, 3]
        assert candidate["code"] == first[:197] + "..."
        assert candidate["evidence"] == (
            f"2 sites: :2 {first}; :3 hasattr(item, 'last')"
            " | item; parameter item: <unannotated> at :1"
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
        assert result["summary"]["candidate_count"] == 4
        falsy_verify = (
            "Can the immediate producer or governing type deliver a falsy value "
            "here? If not, the fallback is dead; if it delivers a distinct "
            'legitimate one (None vs 0, 0 vs ""), the fallback rewrites it. '
            "Counterweight only if the boundary contract equates missing with "
            "empty or the producer already rejects empty. See python.md §6."
        )
        assert [
            (c["symbol"], c["span"], c["code"], c["evidence"], c["verify"])
            for c in candidates
        ] == [
            (
                "<module>",
                [1, 1],
                'data.get("zero") or 0',
                "fallback 0",
                falsy_verify,
            ),
            (
                "<module>",
                [2, 2],
                'data["empty"] or ""',
                "fallback ''",
                falsy_verify,
            ),
            (
                "single",
                [5, 5],
                "data.value or False",
                "fallback False",
                falsy_verify,
            ),
            (
                "load",
                [8, 11],
                'data.get("count") or 0',
                '2 sites: :8 data.get("count") or 0 -> 0; :10 data.items or [] -> []',
                falsy_verify,
            ),
        ]
        for index, candidate in enumerate(candidates, 1):
            assert set(candidate) == {
                "id",
                "path",
                "span",
                "symbol",
                "rule",
                "change_tag",
                "code",
                "evidence",
                "verify",
            }
            assert candidate["id"] == f"C{index:02d}"
            assert candidate["path"] == "coalesce.py"
            assert candidate["rule"] == "PY04_lossy_falsy_coalesce"
            assert candidate["change_tag"] == "path-audit"

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
            assert len(candidates) == 1
            candidate = candidates[0]
            assert candidate["rule"] == "PY04_lossy_falsy_coalesce"
            assert candidate["span"] == [2, 5]
            assert candidate["code"] == 'data.get("' + "x" * 187 + "..."
            assert (
                candidate["evidence"] == "2 sites: "
                f':2 data.get("{long_key}") or 30 -> 30; '
                f':4 data["name"] or {fallback} -> {fallback}'
            )
            assert candidate["verify"] == verify

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
        assert [(c["id"], c["path"], c["symbol"], c["span"]) for c in candidates] == [
            ("C01", "a.py", "Record", [3, 6]),
            ("C02", "a.py", "Record.label", [11, 15]),
            ("C03", "b.py", "Record.label", [3, 3]),
        ]
        assert candidates[0]["code"] == 'data.get("key") or "default"'
        assert (
            candidates[0]["evidence"] == "4 sites: "
            ':3 data.get("key") or "default" -> \'default\'; '
            ':4 data.get("value") or "" -> \'\'; '
            ':5 data.get("next") or 0 -> 0; '
            ':6 data.get("last") or [] -> []'
        )
        assert (
            candidates[1]["evidence"] == "2 sites: "
            ':11 self._row.get("label") or "" -> \'\'; '
            ':15 self._row["setter"] or "" -> \'\''
        )
        assert candidates[2]["evidence"] == "fallback ''"

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
            extra = '    last = data["last"] or ""\n' if remaining == 2 else ""
            self.put(
                "coalesce.py",
                "def original(data):\n"
                "    return data\n"
                "\n"
                "def destination(data):\n"
                '    ignored = data.get("unchanged") or ""\n'
                '    first = data.get("first") or []\n'
                '    moved = data.get("moved") or 30\n' + extra + "    return data\n",
            )
            result = self.scan("--base", base)
            candidates = result["candidates"]
            assert result["summary"]["candidate_count"] == 2
            assert [(c["rule"], c["change_tag"], c["span"]) for c in candidates] == [
                (
                    "PY04_lossy_falsy_coalesce",
                    "changed",
                    [6, 6] if remaining == 1 else [6, 8],
                ),
                ("PY10a_moved_defense", "moved-defense", [7, 7]),
            ]
            coalesced, moved = candidates
            if remaining == 1:
                assert coalesced["code"] == 'data.get("first") or []'
                assert coalesced["evidence"] == "fallback []"
            else:
                assert coalesced["code"] == 'data.get("first") or []'
                assert (
                    coalesced["evidence"] == "2 sites: "
                    ':6 data.get("first") or [] -> []; '
                    ':8 data["last"] or "" -> \'\''
                )
            assert "distinct legitimate one" in coalesced["verify"]
            assert moved["code"] == 'data.get("moved") or 30'
            assert (
                moved["evidence"] == "fallback 30; "
                "same PY04_lossy_falsy_coalesce signature 'moved' "
                "lost from base coalesce.py:2 (original) (signature match only)"
            )
            assert "under python.md §6." in moved["verify"]
            for index, candidate in enumerate(candidates, 1):
                assert candidate["id"] == f"C{index:02d}"
                assert candidate["symbol"] == "destination"
                assert not any(key.startswith("_") for key in candidate)
            compact = self.cli("--base", base)
            assert "(destination) PY04:falsy" in compact
            assert "(destination) PY10a:PY04 [moved-defense]" in compact
            assert "[changed]" not in compact

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
                self.put(
                    "coalesce.py",
                    f"def load():\n    return {lookup} or {fallback}\n",
                )
                candidates = self.scan("--paths", "coalesce.py")["candidates"]
                assert len(candidates) == 1
                candidate = candidates[0]
                assert candidate["rule"] == "PY04_lossy_falsy_coalesce"
                assert candidate["span"] == [2, 2]
                assert candidate["evidence"] == f"fallback {fallback}"
                assert "Counterweight" in candidate["verify"]
                truthy = fallback in {"30", "'default'", "True", "-1"}
                prompts[truthy].add(candidate["verify"])
        assert prompts[True].isdisjoint(prompts[False])
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
            self.put("coalesce.py", f"def load():\n    return {expression}\n")
            assert self.scan("--paths", "coalesce.py")["candidates"] == []

    def test_lossy_coalesce_folds_only_same_line_inner_sites_into_top_level_sites(self):
        self.put(
            "coalesce.py",
            "def load(data):\n"
            '    value = data.get(data.get(data["key"] or "id") or "key", '
            'data["other"] or "x") or ""\n'
            '    left = data["a"] or 0; right = data["b"] or 1\n'
            "    return (\n"
            '        data.get(data["nested"] or "n")\n'
            '        or ""\n'
            "    )\n",
        )
        candidates = self.scan("--paths", ".")["candidates"]
        assert len(candidates) == 1
        candidate = candidates[0]
        outer = (
            'data.get(data.get(data["key"] or "id") or "key", '
            'data["other"] or "x") or ""'
        )
        assert candidate["span"] == [2, 6]
        assert candidate["code"] == outer
        assert candidate["evidence"] == (
            f"8 sites: :2 {outer} -> ''"
            ' [inner: data.get(data["key"] or "id") or "key" -> \'key\']'
            ' [inner: data["key"] or "id" -> \'id\']'
            ' [inner: data["other"] or "x" -> \'x\']; '
            ':3 data["a"] or 0 -> 0; :3 data["b"] or 1 -> 1; '
            ':5 data.get(data["nested"] or "n") or "" -> \'\'; '
            ':5 data["nested"] or "n" -> \'n\''
        )
        compact = self.cli("--paths", ".")
        assert "  Code:" not in compact
        assert f"  Evidence: {candidate['evidence']}" in compact
