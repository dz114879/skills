# ADC Testing & Benchmark Verification Map

Verification for `engineering/anti-defensive-coding` operates in two tiers:
1. **Tier 1 (In-Repo)**: Deterministic unit tests (native `pytest` since `v0.4.3`, `111` tests across 5 modules including one module-level registry test) and linter/formatter gates (`ruff`, including `PT` `flake8-pytest-style`) in `/home/roocode/skills`.
2. **Tier 2 (Cross-Repo Benchmark, `~8s`)**: Snapshot capture, multiset version diffing, and lossless site verification in `/home/roocode/adc_scanning_test`.

---

## 1. Tier 1: Fast Local Verification (`/home/roocode/skills`)

### 1.1 Minimal Commands

| Task | Command (run from `/home/roocode/skills`) |
| --- | --- |
| **Run a single test (fastest, ~0.1s)** | `uv run python -B -m pytest engineering/anti-defensive-coding/tests/<test_module>.py -k <test_name_or_pattern>` |
| **Run a single test module (~1.3s–4.9s)** | `uv run python -B -m pytest engineering/anti-defensive-coding/tests/<test_module>.py` |
| **Run the full unit test suite (~13s)** | `uv run python -B -m pytest -W error` |
| **Run linter on scanner & tests** | `uv run ruff check engineering/anti-defensive-coding/scripts engineering/anti-defensive-coding/tests` |
| **Run formatter check on scanner & tests** | `uv run ruff format --check engineering/anti-defensive-coding/scripts engineering/anti-defensive-coding/tests` |
| **Run self-scan hygiene gate** | `uv run python engineering/anti-defensive-coding/scripts/scan_python.py --paths engineering/anti-defensive-coding --format json` |

Always invoke `pytest` through `python -B`: `helpers.py` sets `sys.dont_write_bytecode = True` too late to stop `pytest` assertion rewriting from writing `tests/__pycache__`, and `pyproject.toml` disables `.pytest_cache` via `addopts = ["-p", "no:cacheprovider"]`. Both are required by `test_all_modes_leave_full_tree_and_git_status_unchanged`, which asserts `git status --porcelain --ignored` on `/home/roocode/skills` is unchanged. Timings above were measured with CPU turbo boost off; the `v0.4.2` `~8.83s` `unittest` figure was measured with turbo boost on. With turbo off, the `v0.4.2` `unittest` suite takes `11.7s–11.9s` versus `12.45s` under `pytest` (`~5%` runner overhead).

### 1.2 Unit Test Map (`engineering/anti-defensive-coding/tests/`)

Shared test infrastructure lives in `tests/helpers.py` (plain `ExtractorTestCase` base class collected via `python_classes = ["*Tests", "Test*"]`; sets `sys.dont_write_bytecode = True`; a class-scoped `autouse` `@classmethod` fixture builds the read-only Git template under `tmp_path_factory` and a function-scoped `autouse` fixture binds `self.root` to `tmp_path`; runs `scan_python.main(argv)` in-process via `cli` / `scan`, provides `cli_subprocess` for real subprocess CLI checks, and manages class-level read-only Git fixtures via `git_template` / `git_fixture` / `use_git_fixture`). Tests use plain `assert`, `pytest.raises`, and `@pytest.mark.skipif`. The original `103` tests remain, with `8` output-contract tests added in `v0.4.4`; there are no runtime dependencies beyond the standard library.

| Test Module & Class | Subsystem / Rule | Test Method Prefix / Pattern | What Is Tested |
| --- | --- | --- | --- |
| `tests/test_scope.py` (`ScopeTests`) | **CLI, Git Scope & Read-Only Contract** | `test_relative_non_git_paths_*`, `test_candidates_share_schema_*`, `test_mixed_parse_failures_*`, `test_non_text_codec_*`, `test_unreadable_subtree_*`, `test_path_inventory_*`, `test_invalid_cli_*`, `test_worktree_diff_*`, `test_git_paths_*`, `test_commit_and_merge_base_*`, `test_renames_binary_*`, `test_diff_ignores_trailing_whitespace_*`, `test_checkout_ancestors_*`, `test_non_git_root_*`, `test_code_snippets_*`, `test_all_modes_leave_full_tree_and_git_status_unchanged` | Exit codes (`0` vs `2`), real `subprocess.run` (`cli_subprocess`) and `runpy.run_path` CLI entrypoints, PEP 263 codecs, merge conflicts, `W1` `--ignore-space-at-eol`, symlink skipping, and strict working-tree/git-status immutability on both the target temp repo and `/home/roocode/skills`. |
| `tests/test_scope.py` (module-level and `ScopeTests`) | **Output & Registry Contracts** | `test_verify_templates_*`, `test_compact_*`, `test_code_snippets_*` | Exact 29-key verification bijection; first-seen legend deduplication and empty-scan omission; file grouping; `:L` / `:a-b` spans; paths/diff default-tag filtering; preserved non-default tags; redundant `Code:` omission; explicit `...` clipping above 200 characters. |
| `tests/test_py01_py04.py` (`Py01Py04Tests`) | **`PY01` Reflection & Memo Guard** | `test_memo_guard_*`, `test_reflection_*`, `test_constructor_helpers_*`, `test_conditional_initializer_*`, `test_receiver_evidence_*`, `test_declaration_clues_*` | `T4` memo guard conditions, same-file class/base attribute resolution, `K1-E1` receiver binding and closure annotation evidence. |
| `tests/test_py01_py04.py` (`Py01Py04Tests`) | **`PY04` Lossy Coalesce & Bundling** | `test_lossy_coalesce_*` | Truthy vs falsy fallback prompts, `K4-B` per-function bundling, singleton preservation, full expression retention before 200-char truncation, same-line nested folding onto top-level sites (count includes all members), multiline/peer non-folding, interaction with `PY10a` promotion. |
| `tests/test_py02.py` (`Py02Tests`) | **`PY02` Error Washing & Retries** | `test_error_washing_*` | Broad/multi-exception exit analysis, `ExceptionGroup` (`try*`), `saved_retry` (`dc-L1`/`K2-L1b`), `bounded_retry`, `stored_primary_cleanup` (`K2-L2`), `NoReturn` helpers (`dc_dojo E1`), `json.loads(object_pairs_hook=...)`, `Z-T1` `try`-body diff scope, and long-handler header/first/last-statement summarization. |
| `tests/test_py05_py09.py` (`Py05Py09Tests`) | **`PY03` Default Shadows Guard** | `test_default_shadow*`, `test_get_default_shadows_*`, `test_shadow_diff_*` | 5-statement sibling lookahead, rebinding stop condition, `.get()` vs `or` guard matching. |
| `tests/test_py05_py09.py` (`Py05Py09Tests`) | **`PY05` Dead Parameter Evasion** | `test_dead_parameter_*`, `test_diff_eligibility_excludes_noncontributing_else_branches` | `_ = param` discard, linear entry-prefix overwrite, dual-parameter `if new is None: new = ... old ...` fallback, non-contributing `else` branch diff filtering. |
| `tests/test_py05_py09.py` (`Py05Py09Tests`) | **`PY07` State Smuggling** | `test_transient_self_state_*`, `test_same_class_stash_*`, `test_dict_dependency_smuggling_*` | `try`/`finally` transient attribute reset, `S7` `self._last = x; return x` + sibling reader, dependency-keyed dict storage. |
| `tests/test_py05_py09.py` (`Py05Py09Tests`) | **`PY08` Copy & Roundtrip Churn** | `test_roundtrip_conversions_*`, `test_dict_comprehension_rewrap_*`, `test_dict_rewrap_evidence_*`, `test_copy_churn_*` | Nested conversion chains, `[dict(row) for row in rows]` with `E8` `row_factory` evidence, loop-body `deepcopy`/`asdict`. |
| `tests/test_py05_py09.py` (`Py05Py09Tests`) | **`PY09` Lock & Transaction Bleed** | `test_lock_io_and_hashing_*`, `test_nested_transaction_*`, `test_transaction_entry_*`, `test_transaction_checks_*`, `test_lock_header_added_*`, `test_state_and_lock_diff_*` | `A09-CTX` subscripted lock unwrapping, `A09-NET` network/file/hash calls in locks, nested transactions, `.in_transaction` predicate checks. |
| `tests/test_py10_diff_reach.py` (`Py10DiffReachTests`) | **`PY10a` Moved Defense (Diff)** | `test_moved_defense*` | Full-tree `before`/`after` signature pairing, git rename mapping, `U1` (`destinations <= origins`), `A1` added-line intersection, `D2` in-place promotion & evidence prepending. |
| `tests/test_py10_diff_reach.py` (`Py10DiffReachTests`) | **`PY10b` Test Smells** | `test_test_smell*`, `test_getter_recall_*`, `test_self_patch_*`, `test_starting_a_bound_patcher_*`, `test_default_hook_identity_*`, `test_module_raw_text_*` | `_legacy*` access, `.py`/`.md` raw text assertions, getter mutation before re-call, `D3` lexical `patch_windows` (`.start()`/`.stop()`/`stopall`/`monkeypatch.undo`), `D10` prod `_DEFAULT_*` hook comparison. |
| `tests/test_py10_diff_reach.py` (`Py10DiffReachTests`) | **`PY10c` Cross-Reachability** | `test_cross_*`, `test_rebound_import_aliases_*`, `test_module_all_mutations_*`, `test_base_decode_failure_*` | `T1`/`T2` `__all__` export closure, `T3` foreign-receiver & `super()` delegation, `R2` recursive bindings & forwarded params, `D4b` per-function uniform parameter/default evidence, sync/async signature-only code for cross-test/orphaned records, `fox D1`/`R1` diff deleted-call net loss, `fox D3` external base classes, base decode failure resilience. |

---

## 2. Tier 2: Benchmark Corpus & Version Diffing (`/home/roocode/adc_scanning_test`)

Whenever a change modifies `scripts/scan_python.py`, `scripts/adc_scanner/*`, `SKILL.md`, or `references/*.md`, benchmark it against the 6 frozen target repositories in `/home/roocode/adc_scanning_test`.

### 2.1 Benchmark Targets

| Target | Profile | Role in Benchmark |
| --- | --- | --- |
| `anti-defensive-coding` | Symlink to `/home/roocode/skills/engineering/anti-defensive-coding` | Self-dogfooding. **Caveat**: editing `scripts/scan_python.py` or `scripts/adc_scanner/*` changes this target's own input source, so `audit_helper.py compare` flags it `[INPUT_CHANGED]` (emitting only the `1` `scripts/adc_scanner/scope.py:diff_scope` `PY04` candidate). |
| `Clonoth` | AI Agent framework (`refactor/lite` branch, 164 files) | High-density async flows, protocols, and `PY01`/`PY02`/`PY04` idioms. |
| `fox-dayi-bot` | AI-assisted Discord bot + SQLite (`main`, 67 files) | Cogs, extension loaders (`PY10c`), SQLite queries (`PY08`/`PY09`). |
| `Zoaholic` | HTTP LLM proxy / Uni-API fork (`main`, 212 files) | Streaming interceptors, config parsing, `PY10a` moved defenses in diff mode. |
| `dc_dojo_bot_moli` | Discord UI/Views + SQLite (`fix/fixed-failure-cooldown`, 46 files) | Slash-command boundaries (`PY02`), `[dict(row)]` (`PY08`), lock/tx patterns (`PY09`). |
| `httpx` | Mature human-engineered HTTP client (`master`, 60 files) | Low-noise Counterweight & false-positive baseline (`32` candidates in `v0.3` paths, `0` in diff). |

### 2.2 Standard Benchmark Workflow

Run from `/home/roocode/adc_scanning_test`:

```sh
# 1. Capture paths-mode snapshot across all 6 targets
python3 scripts/audit_helper.py snapshot --tag <new-tag> --note "<summary>"

# 2. Capture diff-mode (HEAD~5..HEAD) snapshot across all 6 targets
python3 scripts/audit_helper.py snapshot --tag <new-tag>-diff-head5 --mode diff --base-ref HEAD~5 --tip-ref HEAD --note "<summary>"

# 3. Compare against the previous official baseline (e.g. v0.3 and v0.3-diff-head5)
python3 scripts/audit_helper.py compare v0.3 <new-tag>
python3 scripts/audit_helper.py compare v0.3-diff-head5 <new-tag>-diff-head5

# 4. Regenerate reports/VERSION_LEDGER.md
python3 scripts/audit_helper.py ledger
```

### 2.3 Mechanical Lossless Verification (For Refactoring / Bundling Rounds)

- **Pure refactoring rounds (such as Roadmap P0)**:
  - Must achieve **`-0` Removed, `+0` Added, `~0` Modified** across all 5 frozen external targets in both `compare v0.3 <tag>` and `compare v0.3-diff-head5 <tag>-diff-head5`, and preserve only the single `scripts/adc_scanner/scope.py:diff_scope` `PY04` candidate (`1` in `paths` mode, and `1` relocated in `diff` `HEAD~5..HEAD` when `scope.py` is inside the diff window or `0` otherwise) on the `[INPUT_CHANGED]` self-scan target (`anti-defensive-coding`).
- **Output-format rounds (`v0.4.4`)**:
  - `audit_helper.py compare` includes `code` in candidate keys and compares evidence/verification text, so presentation changes intentionally appear as modified or removed/added records. Compare format-independent coordinates `(target, path, tuple(span), symbol, rule, change_tag)` as multisets instead.
  - Read snapshots from `reports/snapshots/<tag>/<target>.json`, verify target HEADs against snapshot `meta.json`, and assert both multiset differences are empty across the 5 frozen external targets in paths and diff modes. Separately preserve the self-scan `diff_scope`-only invariant above.
  - Unpack the following semantic items as multisets, retaining values without normalization:
    1. **`PY04` inspection sites (`(path, symbol, line, code, fallback)`)**:
       - Singleton `PY04_lossy_falsy_coalesce` evidence is `fallback {fallback}`. Promoted `PY10a` evidence starts `fallback {fallback}; same PY04_lossy_falsy_coalesce signature ...`. Take the line from `span[0]` and the expression from `code`.
       - Bundles are `{count} sites: :{line} {code} -> {fallback}; ...`. Unpack each top-level site and each ` [inner: {code} -> {fallback}]`, inheriting the outer line for inner sites. Count every folded site and assert the unpacked count equals `{count}`.
       - For pre-`v0.4.4` snapshots, singletons/promotions start `Lookup coalesces to {fallback} at line {line}`; bundles start `{count} coalescing lookups in {symbol}: ` and separate `line {line}: {code} -> {fallback}` sites with `; `.
       - For comparisons, use full member code where available or the first 200 characters consistently. If a new singleton's `code` is explicitly clipped (`first 197 + ...`), recover the full expression from its unchanged source AST, validate the emitted prefix, and compare its first 200 characters to the legacy silent clip.
    2. **`PY10c` uniform-default parameters (`(path, symbol, name, value)`)**:
       - Singletons use `Param '<name>'=<default>: <K> prod calls; uniform: <val>...`; bundles use `Params '<name>'=<default> (uniform: <val>), ...: <K> prod calls...`. Extract every name/uniform-value pair, without confusing declared defaults with uniform bindings.
       - Legacy snapshots use `Parameter '<name>': <K> resolved prod call sites; uniform value: <val>...` or `Parameters '<name>' (uniform value: <val>), ...`. Keep `<DEFAULT>` and literal spellings unchanged.
    3. **`PY10a` moved-defense pairings (`(target, path, tuple(span), symbol, base_rule, signature, origins)`)**:
       - Extract `base_rule`, `signature`, and all `origins` from the unchanged `; same <base_rule> signature <signature> lost from <origins> (signature match only)` suffix. Compare these tuples independently of the concise base evidence prefix.
