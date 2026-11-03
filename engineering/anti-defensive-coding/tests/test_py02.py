import textwrap

from helpers import ExtractorTestCase


class Py02Tests(ExtractorTestCase):
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
        assert len(candidates) == 1
        assert candidates[0]["rule"] == "PY02_error_washing"
        assert candidates[0]["span"] == [4, 5]
        assert "Exception" in candidates[0]["evidence"]

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
            assert len(candidates) == int(expected)
            if expected:
                candidate = candidates[0]
                assert candidate["rule"] == "PY02_error_washing"
                assert candidate["symbol"] == "load"
                assert candidate["span"] == [4, 4 + len(body.splitlines())]
                assert (caught or "bare except") in candidate["evidence"]
                assert "line 4" in candidate["evidence"]

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
            assert len(candidates) == int(expected)
            if expected:
                assert candidates[0]["rule"] == "PY02_error_washing"
                assert candidates[0]["symbol"] == "send"
                assert "non-raising exit" in candidates[0]["evidence"]
        for condition, expected in [("True", True), ("1", True), ("pending", False)]:
            self.put(
                "retry.py",
                f"def send():\n    last = None\n    while {condition}:\n"
                "        try:\n            return deliver()\n"
                "        except Exception as error:\n            last = error\n"
                "    raise last\n",
            )
            candidates = self.scan("--paths", "retry.py")["candidates"]
            assert len(candidates) == int(expected)
            if expected:
                assert candidates[0]["rule"] == "PY02_error_washing"
                assert candidates[0]["symbol"] == "send"
                assert "non-raising exit" in candidates[0]["evidence"]

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
            assert len(candidates) == int(expected)
            if expected:
                assert candidates[0]["rule"] == "PY02_error_washing"
                assert "non-raising exit" in candidates[0]["evidence"]

    def test_error_washing_cleanup_preserves_primary_or_cleanup_failure(self):
        for mutation, finalizer, expected_cleanup in [
            ("", "", False),
            ("saved = None", "", True),
            ("del saved", "", True),
            ("if stop:\n    return None", "", True),
            ("", "finally:\n    return None\n", True),
        ]:
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
            assert len(candidates) == 1 + int(expected_cleanup)
            assert all(c["rule"] == "PY02_error_washing" for c in candidates)
            assert "except Exception as error" in candidates[0]["code"]
            if expected_cleanup:
                assert "cleanup_error" in candidates[1]["code"]

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
            assert len(candidates) == 1
            candidate = candidates[0]
            assert candidate["rule"] == "PY02_error_washing"
            assert candidate["symbol"] == "Client.check"
            assert ("non-raising exit" in candidate["evidence"]) == nonraising
            if helper:
                assert f"re-raises via `{helper}` (NoReturn)" in candidate["evidence"]
            else:
                assert "re-raises via" not in candidate["evidence"]

    def test_error_washing_noreturn_helper_keeps_earlier_loop_exits(self):
        for escape in ("break", "continue"):
            self.put(
                "helpers.py",
                "def reject() -> Never:\n    raise Rejected()\n"
                "def run():\n    for item in items:\n"
                "        try:\n            work(item)\n"
                "        except Exception:\n            if stop:\n"
                f"                {escape}\n            reject()\n",
            )
            candidates = self.scan("--paths", "helpers.py")["candidates"]
            assert len(candidates) == 1
            assert "non-raising exit" in candidates[0]["evidence"]
            assert "re-raises via `reject` (NoReturn)" in candidates[0]["evidence"]

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
        assert len(candidates) == 3
        for candidate, symbol, line in zip(
            candidates,
            ("saved_retry", "bounded_retry", "cleanup"),
            (5, 14, 27),
            strict=True,
        ):
            assert candidate["rule"] == "PY02_error_washing"
            assert candidate["symbol"] == symbol
            assert candidate["evidence"].split("; ")[1:] == [
                f"json.loads(object_pairs_hook=...) at line {line} is not bare re-raised on every path"
            ]

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
            assert len(candidates) == int(expected)
            if expected:
                candidate = candidates[0]
                assert candidate["rule"] == "PY02_error_washing"
                assert "object_pairs_hook" in candidate["evidence"]
                assert "line 3" in candidate["evidence"]
                if caught == "Exception":
                    assert "non-raising" in candidate["evidence"]
        for body in [
            "return json.loads(raw)",
            "def nested():\n    return json.loads(raw, object_pairs_hook=unique)",
            "reader = lambda: json.loads(raw, object_pairs_hook=unique)",
        ]:
            self.put(
                "hook.py",
                "def load(raw):\n    try:\n"
                + textwrap.indent(body, "        ")
                + "\n    except ValueError:\n        raise DomainError()\n",
            )
            assert self.scan("--paths", "hook.py")["candidates"] == []

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
            path.write_text(source.replace(old, new))
            tip = self.commit()
            candidates = self.scan("--base", base, "--tip", tip)["candidates"]
            assert [
                (c["rule"], c["span"], c["code"], c["change_tag"]) for c in candidates
            ] == [
                ("PY02_error_washing", span, code, "changed") for span, code in expected
            ]
