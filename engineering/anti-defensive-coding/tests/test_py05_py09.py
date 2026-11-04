import textwrap

from helpers import ExtractorTestCase


class Py05Py09Tests(ExtractorTestCase):
    def test_default_shadow_rebinding_respects_current_scope(self):
        for intervening, expected in [
            ("from external import value", False),
            ("def value():\n    pass", False),
            ("def helper():\n    value = None", True),
        ]:
            self.put(
                "defaults.py",
                "def load(raw):\n    value = raw or 1\n"
                + textwrap.indent(intervening, "    ")
                + "\n    if value is None:\n        return None\n",
            )
            candidates = self.scan("--paths", "defaults.py")["candidates"]
            assert len(candidates) == int(expected)
            if expected:
                assert candidates[0]["rule"] == "PY03_default_shadows_check"

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
            assert len(candidates) == int(expected)
            if expected:
                candidate = candidates[0]
                assert candidate["rule"] == "PY03_default_shadows_check"
                assert candidate["span"] == [2, 4 + len(intervening.splitlines())]
                assert ":2" in candidate["evidence"]
                assert f":{3 + len(intervening.splitlines())}" in candidate["evidence"]
                assert "Counterweight" in candidate["verify"]
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
        assert self.scan("--paths", "defaults.py")["candidates"] == []

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
            self.put(
                "defaults.py",
                f"def load(data):\n    value: int = {expression}\n"
                f"    if {guard}:\n        return None\n    return value\n",
            )
            candidates = self.scan("--paths", "defaults.py")["candidates"]
            assert len(candidates) == int(expected)
            if expected:
                assert candidates[0]["rule"] == "PY03_default_shadows_check"
                assert "missing keys" in candidates[0]["verify"]
                assert "None" in candidates[0]["verify"]
                assert "explicitly stored" in candidates[0]["verify"]

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
        assert self.scan("--base", base)["candidates"] == []
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
        assert len(candidates) == 1
        assert candidates[0]["span"] == [2, 5]
        assert candidates[0]["change_tag"] == "changed"
        assert set(candidates[0]) == {
            "id",
            "rule",
            "path",
            "span",
            "symbol",
            "change_tag",
            "code",
            "evidence",
            "verify",
        }

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
            self.put(
                "params.py",
                'def load(old):\n    """Load."""\n'
                + textwrap.indent(body, "    ")
                + "\n",
            )
            candidates = self.scan("--paths", "params.py")["candidates"]
            assert len(candidates) == int(expected)
            if expected:
                assert candidates[0]["rule"] == "PY05_dead_param_evasion"
                assert candidates[0]["span"] == [3, 3]
                assert "discard" in candidates[0]["evidence"]
                assert "'old'" in candidates[0]["evidence"]
                assert ":3" in candidates[0]["evidence"]
                assert "caller" in candidates[0]["verify"]
                assert "Counterweight" in candidates[0]["verify"]
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
        assert self.scan("--paths", "params.py")["candidates"] == []

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
            self.put(
                "params.py",
                "def load(old):\n" + textwrap.indent(body, "    ") + "\n",
            )
            candidates = self.scan("--paths", "params.py")["candidates"]
            assert len(candidates) == int(expected)
            if expected:
                assert candidates[0]["rule"] == "PY05_dead_param_evasion"
                assert "entry overwrite" in candidates[0]["evidence"]
                assert "'old'" in candidates[0]["evidence"]
                assert candidates[0]["span"][0] == (
                    3 if body.startswith("log()") else 2
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
        assert [c["symbol"] for c in candidates] == ["Model.load", "Model.load"]

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
            self.put(
                "params.py",
                f"def load({signature}):\n"
                + textwrap.indent(before, "    ")
                + f"    if new is None:\n        new = {rhs}\n"
                + textwrap.indent(after, "    ")
                + "    return new\n",
            )
            candidates = self.scan("--paths", "params.py")["candidates"]
            assert len(candidates) == int(expected)
            if expected:
                candidate = candidates[0]
                assert candidate["rule"] == "PY05_dead_param_evasion"
                assert "dual-parameter" in candidate["evidence"]
                assert "'old'" in candidate["evidence"]
                assert "'new'" in candidate["evidence"]
                assert "default None at :1" in candidate["evidence"]
                assert candidate["span"] == [
                    2 + len(before.splitlines()),
                    3 + len(before.splitlines()),
                ]

    def test_diff_eligibility_excludes_noncontributing_else_branches(self):
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
        base = self.use_git_fixture(
            "else_branches",
            {"branches.py": original},
            working_files={"branches.py": original.replace("'before'", "'after'")},
        )
        assert self.scan("--base", base)["candidates"] == []

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
            assert len(candidates) == int(expected)
            if expected:
                candidate = candidates[0]
                assert candidate["rule"] == "PY07_state_smuggling"
                assert candidate["symbol"] == "Runner.run"
                assert "self.context" in candidate["evidence"]
                assert "finally" in candidate["evidence"]
                assert "Counterweight" in candidate["verify"]

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
            self.put(
                "state.py",
                "class Provider:\n    def build(self):\n"
                + textwrap.indent(writer, "        ")
                + "\n    def dump(self):\n"
                + textwrap.indent(reader, "        ")
                + "\n",
            )
            candidates = self.scan("--paths", "state.py")["candidates"]
            assert len(candidates) == int(span is not None)
            if span is not None:
                candidate = candidates[0]
                assert candidate["rule"] == "PY07_state_smuggling"
                assert candidate["symbol"] == "Provider.build"
                assert candidate["span"] == span
                assert "self._last" in candidate["evidence"]
                assert f"dump at :{span[1]}" in candidate["evidence"]

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
                self.put("state.py", "def pack(data, dep, owner):\n    " + expression)
                candidates = self.scan("--paths", "state.py")["candidates"]
                assert len(candidates) == int(expected)
                if expected:
                    candidate = candidates[0]
                    assert candidate["rule"] == "PY07_state_smuggling"
                    assert key in candidate["evidence"]
                    assert ":2" in candidate["evidence"]
                    assert "Counterweight" in candidate["verify"]
        for expression in [
            'data["session"] = None',
            'data["service"] = make_service()',
            'return {"repo": "url"}',
            'return {"reader": deps[0]}',
            'return {"conn": lambda: dep}',
            "return {key: dep}",
        ]:
            self.put("state.py", "def pack(data, dep):\n    " + expression)
            assert self.scan("--paths", "state.py")["candidates"] == []
        self.put("state.py", 'data = {"session": dep}\n')
        assert self.scan("--paths", "state.py")["candidates"] == []

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
            self.put("churn.py", "result = " + expression + "\n")
            candidates = self.scan("--paths", "churn.py")["candidates"]
            assert len(candidates) == int(expected)
            if expected:
                assert candidates[0]["rule"] == "PY08_roundtrip_and_copy"
                assert candidates[0]["code"] == expression
                assert ":1" in candidates[0]["evidence"]
                assert "Counterweight" in candidates[0]["verify"]

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
            self.put("churn.py", "result = " + expression + "\n")
            candidates = self.scan("--paths", "churn.py")["candidates"]
            assert len(candidates) == int(expected)
            if expected:
                assert candidates[0]["rule"] == "PY08_roundtrip_and_copy"
                assert "row" in candidates[0]["evidence"]
                assert ":1" in candidates[0]["evidence"]

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
        assert set(evidence) == {"Store.tagged", "Store.untagged", "Store.nested"}
        assert "conn.row_factory = self.db.dict_row at :12" in evidence["Store.tagged"]
        assert "self.conn.row_factory = None at :14" in evidence["Store.tagged"]
        for symbol in ["Store.untagged", "Store.nested"]:
            assert "row_factory" not in evidence[symbol]

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
            self.put("churn.py", "async def run():\n" + textwrap.indent(body, "    "))
            candidates = self.scan("--paths", "churn.py")["candidates"]
            assert len(candidates) == expected
            if expected:
                candidate = candidates[0]
                assert candidate["rule"] == "PY08_roundtrip_and_copy"
                assert "iteration" in candidate["evidence"]
                assert "Counterweight" in candidate["verify"]

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
                self.put(
                    "locking.py",
                    f"async def run():\n    {keyword} {context}:\n"
                    "        path.read_text()\n",
                )
                candidates = self.scan("--paths", "locking.py")["candidates"]
                assert len(candidates) == int(expected)
                if expected:
                    candidate = candidates[0]
                    assert candidate["rule"] == "PY09_io_or_tx_in_lock"
                    assert candidate["span"] == [2, 3]
                    assert ":2" in candidate["evidence"]
                    assert context in candidate["evidence"]
                    assert "Counterweight" in candidate["verify"]
                    prompts[context.startswith(("conn.", "db."))].add(
                        candidate["verify"]
                    )
        assert prompts[True].isdisjoint(prompts[False])
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
            self.put(
                "locking.py",
                "with outer_lock:\n    with inner_lock:\n        " + expression,
            )
            candidates = self.scan("--paths", "locking.py")["candidates"]
            assert len(candidates) == int(expected)
            if expected:
                assert candidates[0]["rule"] == "PY09_io_or_tx_in_lock"
                assert "outer_lock" in candidates[0]["evidence"]
                assert "inner_lock" in candidates[0]["evidence"]
        for body in [
            "def callback():\n    open(path)",
            "async def callback():\n    path.write_text(text)",
            "class Callback:\n    data = path.read_bytes()",
            "callback = lambda: sha256(data)",
        ]:
            self.put("locking.py", "with lock:\n" + textwrap.indent(body, "    "))
            assert self.scan("--paths", "locking.py")["candidates"] == []
        self.put("locking.py", "open(path)\nhashlib.sha256(data)\n")
        assert self.scan("--paths", "locking.py")["candidates"] == []
        self.put(
            "locking.py",
            "async def run(user_locks, user_id, interaction):\n"
            "    async with user_locks[user_id]:\n"
            "        await interaction.followup.send('inside')\n",
        )
        candidates = self.scan("--paths", "locking.py")["candidates"]
        assert len(candidates) == 1
        assert candidates[0]["rule"] == "PY09_io_or_tx_in_lock"
        assert candidates[0]["span"] == [2, 3]
        assert "interaction.followup.send" in candidates[0]["evidence"]
        assert "user_locks[user_id]" in candidates[0]["evidence"]
        self.put(
            "locking.py",
            "async def run(user_locks, user_id, interaction):\n"
            "    async with user_locks[user_id]:\n"
            "        reply = 'outside'\n"
            "    await interaction.followup.send(reply)\n",
        )
        assert self.scan("--paths", "locking.py")["candidates"] == []

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
            self.put("locking.py", "async def run():\n" + textwrap.indent(body, "    "))
            candidates = self.scan("--paths", "locking.py")["candidates"]
            assert len(candidates) == expected
            if expected:
                assert candidates[0]["rule"] == "PY09_io_or_tx_in_lock"
                assert "with" in candidates[0]["evidence"]
                assert "Counterweight" in candidates[0]["verify"]

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
            self.put(
                "locking.py",
                "async def run():\n"
                + textwrap.indent(check, "    ")
                + f"\n    async with {context}:\n        work()\n",
            )
            candidates = self.scan("--paths", "locking.py")["candidates"]
            assert len(candidates) == int(expected)
            if expected:
                candidate = candidates[0]
                assert candidate["rule"] == "PY09_io_or_tx_in_lock"
                assert candidate["symbol"] == "run"
                assert "in_transaction" in candidate["evidence"]
                assert ":2" in candidate["evidence"]
                assert context in candidate["evidence"]
                assert "Counterweight" in candidate["verify"]
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
        assert self.scan("--paths", "locking.py")["candidates"] == []

    def test_transaction_checks_include_boolean_expressions_and_filter_guards(self):
        for check, line in [
            ("ready = conn.in_transaction is True", 2),
            ("ready = not conn.in_transaction", 2),
            ("ready = conn.in_transaction and enabled", 2),
            ("ready = bool(conn.in_transaction)", 2),
            ("selected = [row for row in rows if conn.in_transaction]", 2),
            ("match value:\n    case _ if conn.in_transaction:\n        work()", 3),
        ]:
            self.put(
                "locking.py",
                "def run():\n"
                + textwrap.indent(check, "    ")
                + "\n    with conn.transaction():\n        work()\n",
            )
            candidates = self.scan("--paths", "locking.py")["candidates"]
            assert len(candidates) == 1
            assert candidates[0]["rule"] == "PY09_io_or_tx_in_lock"
            assert f":{line}" in candidates[0]["evidence"]

    def test_lock_header_added_around_unchanged_io_is_diff_eligible(self):
        base = self.use_git_fixture(
            "lock_header",
            {"locking.py": "def run():\n        path.read_text()\n"},
            working_files={
                "locking.py": "def run():\n    with lock:\n        path.read_text()\n"
            },
        )
        candidates = self.scan("--base", base)["candidates"]
        assert len(candidates) == 1
        assert candidates[0]["rule"] == "PY09_io_or_tx_in_lock"
        assert candidates[0]["change_tag"] == "changed"
        assert candidates[0]["span"] == [2, 3]
        assert "with lock at :2" in candidates[0]["evidence"]

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
            self.put("scope.py", original.replace(before, after))
            candidates = self.scan("--base", base)["candidates"]
            assert len(candidates) == int(expected is not None)
            if expected:
                assert candidates[0]["rule"] == expected
                assert candidates[0]["change_tag"] == "changed"
