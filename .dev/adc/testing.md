# ADC Testing & Benchmark Verification Map

Verification for `engineering/anti-defensive-coding` operates in two tiers:
1. **Tier 1 (In-Repo)**: Deterministic unit tests (native `pytest` since `v0.4.3`, `482` collected cases across 5 modules from `150` test functions/methods in `v0.6`: `480 passed / 2 skipped` on Python 3.12 and `482 passed / 0 skipped` on Python 3.15t) and linter/formatter gates (`ruff`, including `PT` `flake8-pytest-style`) in `/home/roocode/skills`.
2. **Tier 2 (Cross-Repo Benchmark, `~5s` paths / `~14s` diff across 11 targets with `--jobs auto`)**: Snapshot capture, multiset version diffing, and lossless site verification in `/home/roocode/adc_scanning_test`.

---

## 1. Tier 1: Fast Local Verification (`/home/roocode/skills`)

### 1.1 Minimal Commands

| Task | Command (run from `/home/roocode/skills`) |
| --- | --- |
| **Run a single test (fastest, ~0.1s)** | `uv run python -B -m pytest engineering/anti-defensive-coding/tests/<test_module>.py -k <test_name_or_pattern>` |
| **Run a single test module (~1.3s–4.9s)** | `uv run python -B -m pytest engineering/anti-defensive-coding/tests/<test_module>.py` |
| **Run the full unit test suite (~15s)** | `uv run python -B -m pytest -W error` |
| **Run linter on scanner & tests** | `uv run ruff check engineering/anti-defensive-coding/scripts engineering/anti-defensive-coding/tests` |
| **Run formatter check on scanner & tests** | `uv run ruff format --check engineering/anti-defensive-coding/scripts engineering/anti-defensive-coding/tests` |
| **Run self-scan hygiene gate** | `uv run python engineering/anti-defensive-coding/scripts/scan_python.py --paths engineering/anti-defensive-coding --format json` |
| **Inspect read-only/cache hygiene** | `git status --porcelain --ignored` (must show `0` `.pytest_cache` / `__pycache__` artifacts) |

Always invoke `pytest` through `python -B`: `helpers.py` sets `sys.dont_write_bytecode = True` too late to stop `pytest` assertion rewriting from writing `tests/__pycache__`, and `pyproject.toml` disables `.pytest_cache` via `addopts = ["-p", "no:cacheprovider"]`. Both are required by `test_all_modes_leave_full_tree_and_git_status_unchanged`, which asserts `git status --porcelain --ignored` on `/home/roocode/skills` is unchanged. Timings above were measured with CPU turbo boost off; the `v0.4.2` `~8.83s` `unittest` figure was measured with turbo boost on. With turbo off, the `v0.4.2` `unittest` suite takes `11.7s–11.9s` versus `12.45s` under `pytest` (`~5%` runner overhead).

### 1.2 Unit Test Map (`engineering/anti-defensive-coding/tests/`)

Shared test infrastructure lives in `tests/helpers.py` (plain `ExtractorTestCase` base class collected via `python_classes = ["*Tests", "Test*"]`; sets `sys.dont_write_bytecode = True`; a class-scoped `autouse` `@classmethod` fixture builds the read-only Git template under `tmp_path_factory` and a function-scoped `autouse` fixture binds `self.root` to `tmp_path`; runs `scan_python.main(argv)` in-process via `cli` / `scan`, provides `cli_subprocess` for real subprocess CLI checks, and manages class-level read-only Git fixtures via `git_template` / `git_fixture` / `use_git_fixture`). Tests use plain `assert`, `pytest.raises`, `@pytest.mark.skipif`, and parametrized independent cases. The agreed test seams are the CLI/scanner output and registry contracts; tests do not call `handler_stratum` or bundlers directly. There are no runtime dependencies beyond the standard library.

Current `v0.6` counts: `482` collected cases from `150` test functions/methods (`64` `test_scope` from `28` functions/methods including `4` module-level registry/CLI tests, `20` `test_py01_py04` from `20` methods, `59` `test_py02` from `15` methods, `67` `test_py05_py09` from `23` methods, and `272` `test_py10_diff_reach` from `64` methods). On Python 3.12, `480` pass and `2` Python 3.15+ PEP 810 `lazy import` syntax tests skip; on Python 3.15t (`uv run --python 3.15t python -B -m pytest -W error`), all `482` pass (`0` skipped). Ruff lint/format and the `1` `scope.py:diff_scope` paths candidate / `0` diff candidate / `0` parse-error self-scan gates pass with `0` cache/bytecode artifacts.

| Test Module & Class | Subsystem / Rule | Test Method Prefix / Pattern | What Is Tested |
| --- | --- | --- | --- |
| `tests/test_scope.py` (`ScopeTests` + module-level) | **CLI, Git Scope, Concurrency & Read-Only Contract** | `test_relative_non_git_paths_*`, `test_candidates_share_schema_*`, `test_mixed_parse_failures_*`, `test_non_text_codec_*`, `test_unreadable_subtree_*`, `test_path_inventory_*`, `test_invalid_cli_*`, `test_worktree_diff_*`, `test_git_paths_*`, `test_commit_and_merge_base_*`, `test_renames_binary_*`, `test_diff_ignores_trailing_whitespace_*`, `test_checkout_ancestors_*`, `test_non_git_root_*`, `test_code_snippets_*`, `test_jobs_*`, `test_parallel_map_*`, `test_allocation_batch_*`, `test_lazy_import_*`, `test_all_modes_leave_full_tree_and_git_status_unchanged` | Exit codes (`0` vs `2`), real `subprocess.run` (`cli_subprocess`) and `runpy.run_path` CLI entrypoints, `--jobs` / `-j` (`auto` default, `1/2/4/8` byte-for-byte parity, invalid value rejection), `parallel_map` order preservation, `_FREE_THREADED_GC` runtime-aware GC behavior, Python 3.15 PEP 810 `lazy import` AST indexing, PEP 263 codecs, merge conflicts, `W1` `--ignore-space-at-eol`, symlink skipping, and strict working-tree/git-status immutability. |
| `tests/test_scope.py` (module-level and `ScopeTests`) | **Output & Registry Contracts** | `test_verify_templates_*`, `test_py02_evidence_templates_*`, `test_compact_*`, `test_code_snippets_*` | Exact 30-key verification bijection (including `PY10c:stub`) and four `PY02` evidence templates; first-seen legend deduplication and empty-scan omission; file grouping; `:L` / `:a-b` spans; paths/diff default-tag filtering; preserved non-default tags; redundant `Code:` omission on `PY01`/`PY04` bundles and `PY10c:default`/`PY10c:bundle` while singleton union annotations and `PY10c:stub` keep `Code:`; explicit `...` clipping above 200 characters. |
| `tests/test_py01_py04.py` (`Py01Py04Tests`) | **`PY01` Reflection, Memo Guard & Bundling** | `test_memo_guard_*`, `test_reflection_*`, `test_constructor_helpers_*`, `test_conditional_initializer_*`, `test_receiver_evidence_*`, `test_declaration_clues_*` | `T4` memo guard conditions, same-file class/base attribute resolution, `K1-E1` receiver binding and closure annotation evidence; per-function multi-site bundles with covering spans, first-member code, receiver provenance deduplication and declaration merging; quote-insensitive nested folding before span widening; unchanged singleton/module sites and compact `Code:` suppression/preservation. |
| `tests/test_py01_py04.py` (`Py01Py04Tests`) | **`PY04` Lossy Coalesce & Bundling** | `test_lossy_coalesce_*` | Truthy vs falsy fallback prompts, `K4-B` per-function bundling, singleton preservation, full expression retention before 200-char truncation, same-line nested folding onto top-level sites (count includes all members), multiline/peer non-folding, interaction with `PY10a` promotion. |
| `tests/test_py02.py` (`Py02Tests`) | **`PY02` Error Washing, Retries & Handler Strata** | `test_error_washing_*` | Broad/multi-exception exit analysis, `ExceptionGroup` (`try*`), `saved_retry` (`dc-L1`/`K2-L1b`), `bounded_retry`, `stored_primary_cleanup` (`K2-L2`), `NoReturn` helpers (`dc_dojo E1`), hook diagnostics, `Z-T1` diff scope, and long-handler code summarization. Parametrized cases cover silent statements/documentation (`8`), bare/single/multiple/deduplicated/source-ordered returns and 80-character truncation (`10`), traceback calls and explicit `exc_info` disabling plus nested-scope exclusion (`23`), and raising-only/mixed abort + hook fact ordering (`7`). |
| `tests/test_py05_py09.py` (`Py05Py09Tests`) | **`PY03` Default Shadows Guard** | `test_default_shadow*`, `test_get_default_shadows_*`, `test_shadow_diff_*` | 5-statement sibling lookahead, rebinding stop condition, `.get()` vs `or` guard matching. |
| `tests/test_py05_py09.py` (`Py05Py09Tests`) | **`PY05` Dead Parameter Evasion** | `test_dead_parameter_*`, `test_diff_eligibility_excludes_noncontributing_else_branches` | `_ = param` discard, linear entry-prefix overwrite, dual-parameter `if new is None: new = ... old ...` fallback, non-contributing `else` branch diff filtering. |
| `tests/test_py05_py09.py` (`Py05Py09Tests`) | **`PY07` State Smuggling** | `test_transient_self_state_*`, `test_same_class_stash_*`, `test_dict_dependency_smuggling_*` | `try`/`finally` transient attribute reset, `S7` `self._last = x; return x` + sibling reader, dependency-keyed dict storage with `G3` terminal value-token suffix check (`PY07_DEPENDENCY_SUFFIXES`). |
| `tests/test_py05_py09.py` (`Py05Py09Tests`) | **`PY08` Copy & Roundtrip Churn** | `test_roundtrip_conversions_*`, `test_dict_comprehension_rewrap_*`, `test_dict_rewrap_evidence_*`, `test_copy_churn_*` | Nested conversion chains, `[dict(row) for row in rows]` with `E8` `row_factory` evidence, loop-body `deepcopy`/`asdict` with `G4` 1-arg `asdict` arity check. |
| `tests/test_py05_py09.py` (`Py05Py09Tests`) | **`PY09` Lock & Transaction Bleed** | `test_lock_io_and_hashing_*`, `test_nested_transaction_*`, `test_transaction_entry_*`, `test_transaction_checks_*`, `test_lock_header_added_*`, `test_state_and_lock_diff_*` | `A09-CTX` subscripted lock unwrapping, `A09-NET` network/file/hash calls in locks, nested transactions, `.in_transaction` predicate checks. |
| `tests/test_py10_diff_reach.py` (`Py10DiffReachTests`) | **`PY10a` Moved Defense (Diff)** | `test_moved_defense*` | Full-tree `before`/`after` signature pairing, git rename mapping, `U1` (`destinations <= origins`), `A1` added-line intersection, `D2` in-place promotion & evidence prepending; remaining reflection sites bundle only after promotions (single remaining site stays unbundled). |
| `tests/test_py10_diff_reach.py` (`Py10DiffReachTests`) | **`PY10b` Test Smells** | `test_test_smell*`, `test_getter_recall_*`, `test_self_patch_*`, `test_starting_a_bound_patcher_*`, `test_default_hook_identity_*`, `test_module_raw_text_*`, `test_raw_text_compound_*`, `test_self_patch_binding_*` | `_legacy*` access, `.py`/`.md` raw text assertions with `G1` compound-assertion folding (`PY10b_raw_text_bundle`), getter mutation before re-call, `D3` lexical `patch_windows`, `G2` qualified patch-target vs. imported constructor / `monkeypatch.setattr` receiver disambiguation (`same_patched_slot` + `import_env`), `D10` prod `_DEFAULT_*` hook comparison. |
| `tests/test_py10_diff_reach.py` (`Py10DiffReachTests`) | **`PY10c` Cross-Reachability & Stubs** | `test_cross_*`, `test_rebound_import_aliases_*`, `test_module_all_mutations_*`, `test_base_decode_failure_*`, `test_fastmcp_tool_*`, `test_protocol_method_*`, `test_constant_return_stub_*` | `T1`/`T2` `__all__` export closure, `T3` foreign-receiver & `super()` delegation, `S3-1` `@*.tool(...)` FastMCP and `@override` exclusions, `S3-2` `typing.Protocol` / `typing_extensions.Protocol` exclusions, `P1` `PY10c:stub` live constant-result stub detection and compact `Code:` retention, `R2` recursive bindings & forwarded params, `D4b` per-function uniform parameter/default evidence, sync/async signature-only code, `fox D1`/`R1` diff deleted-call net loss, `fox D3` external base classes, base decode failure resilience. |

---

## 2. Tier 2: Benchmark Corpus & Version Diffing (`/home/roocode/adc_scanning_test`)

Whenever a change modifies `scripts/scan_python.py`, `scripts/adc_scanner/*`, `SKILL.md`, or `references/*.md`, benchmark it against the 11 frozen target repositories in `/home/roocode/adc_scanning_test`.

### 2.1 Benchmark Targets (`11` Targets: `1` Self + `10` Frozen External)

| Target | Profile | Role in Benchmark (`v0.6` Paths / `HEAD~5..HEAD` Diff) |
| --- | --- | --- |
| `anti-defensive-coding` | Symlink to `/home/roocode/skills/engineering/anti-defensive-coding` (`15` files) | Self-dogfooding. **Caveat**: editing `scripts/scan_python.py` or `scripts/adc_scanner/*` changes this target's own input source, so `audit_helper.py compare` flags it `[INPUT_CHANGED]` (`1` paths candidate on `scope.py:diff_scope`, `0` diff candidates when `scope.py` is unchanged in `HEAD~5..HEAD`). |
| `Clonoth` | AI Agent framework (`refactor/lite` `d93f15c09006`, `164` files) | High-density async flows, protocols, `PY01`/`PY02`/`PY04` idioms, and `PY10c:stub` (`_run_idle_cleanup`) (`1,091` paths / `17` diff). |
| `fox-dayi-bot` | AI-assisted Discord bot + SQLite (`main` `102440d92cc5`, `67` files) | Cogs, extension loaders (`PY10c`), SQLite queries (`PY08`/`PY09`) (`350` paths / `9` diff). |
| `Zoaholic` | HTTP LLM proxy / Uni-API fork (`main` `bf3458dc88f4`, `212` files) | Streaming interceptors, config parsing, `PY10a` moved defenses in diff mode (`791` paths / `57` diff). |
| `dc_dojo_bot_moli` | Discord UI/Views + SQLite (`fix/fixed-failure-cooldown` `c4f56b0a7a61`, `46` files) | Slash-command boundaries (`PY02`), `[dict(row)]` (`PY08`), lock/tx patterns (`PY09`), `PY10c:stub` (`get_privilege_roles`) (`363` paths / `76` diff). |
| `httpx` | Mature human-engineered HTTP client (`master` `b5addb64f016`, `60` files) | Low-noise Counterweight & false-positive baseline (`31` paths / `0` diff). |
| `wardrowbe` | FastAPI + SQLAlchemy 2.0 `Mapped[...]` async ORM + Pydantic v2 (`main` `1ba57a87e8ea`, `195` files) | P2 CRUD/ORM/validator boundary archetype, `PY10a` moved defenses, and `G2` `PY10b` clock fixture disambiguation (`140` paths / `48` diff). |
| `beets` | Synchronous CLI media library + plugin system + SQLite `dbcore` (`master` `5682b275b5ad`, `306` files) | P2 CLI/plugin/thread-local SQLite `row_factory` archetype and `G2` `monkeypatch.setattr` receiver disambiguation (`116` paths / `1` diff). |
| `ruamel-yaml` | YAML parser/emitter state machine (`master` `b6b5921256e7`, `91` files) | P2 polymorphic AST/token/event unions, `PY03` default-shadows-check (`1` hit), and `__all__` base/mixin inheritance (`136` paths / `0` diff). |
| `dbt-mcp` | FastMCP server + `typing.Protocol` + `pydantic-settings` (`main` `9ad0770ba730`, `278` files) | P2 MCP tool server, `S3-2` `typing.Protocol` exclusion, and `G1` compound raw-text assertion folding (`119` paths / `0` diff). |
| `mcp-atlassian` | FastMCP Jira/Confluence server + mixin client (`main` `0a5d2427144d`, `290` files) | P2 `@*.tool()` registration (`S3-1`), `PY07` `dict_dependency` value-token guard (`G3`), and `G1`/`G2` `PY10b` test-suite guards (`374` paths / `3` diff). |

### 2.2 Standard Benchmark Workflow

Run from `/home/roocode/adc_scanning_test` (always with `python3 -B`):

```sh
# 1. Capture paths-mode snapshot across all 11 targets
python3 -B scripts/audit_helper.py snapshot --tag <new-tag> --note "<summary>"

# 2. Capture diff-mode (HEAD~5..HEAD) snapshot across all 11 targets
python3 -B scripts/audit_helper.py snapshot --tag <new-tag>-diff-head5 --mode diff --base-ref HEAD~5 --tip-ref HEAD --note "<summary>"

# 3. Compare against the previous official 11-target baseline (e.g. v0.5-11t / v0.6)
python3 -B scripts/audit_helper.py compare v0.6 <new-tag>
python3 -B scripts/audit_helper.py compare v0.6-diff-head5 <new-tag>-diff-head5

# 4. Regenerate reports/VERSION_LEDGER.md
python3 -B scripts/audit_helper.py ledger
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

### 2.4 P1 Validation & Official `v0.5` Benchmark Baseline (`v0.5.0` → `v0.5.1` → `v0.5`)

**P1.1 Goldfish Reviewer gate (completed):** `/home/roocode/adc_scanning_test/reports/audits/v0.4-bundling-reviewer-validation.md` records a stateless review of `16` `v0.4.4` compact bundles (`11` `PY04` bundles / `66` sites, `5` `PY10c` bundles / `13` parameters). Results are `11/11` `PY04` TP recall, `11/11` severity agreement, and `13/13` `PY10c` parameter agreement, with `0` missed TPs. This validates the whole-function `(path, symbol, change_tag)` policy adopted by `bundle_reflections`, without receiver splitting.

**`PY01` lossless unpacking recipe (Ticket 01 → `v0.5`):**

1. Compare `v0.4` → `v0.5` (paths) and `v0.4-diff-head5` → `v0.5-diff-head5` (diff) at fixed target HEADs/diff endpoints. Select `PY01_reflect_default` plus `PY10a_moved_defense` records whose unchanged signature suffix names `PY01_reflect_default`.
2. Detect bundles only when `evidence.split(" sites: :", 1)[0].isdigit()`; a singleton receiver annotation may contain ` | ` and must not be mistaken for a bundle. Singletons/promotions contribute `(target, path, symbol, change_tag, span[0], code)`. Recover any clipped full call from the unchanged source AST and validate the emitted `[:197] + "..."` prefix before comparing full whitespace-normalized calls.
3. For bundles, unpack each `:{line} {code}` member before the structural ` | {receivers}` separator and every ` [inner: {code}]` member, inheriting its containing site's line. Parse delimiters outside quoted expressions/nested brackets; do not split arbitrary expression strings on `; ` or ` | `. Assert the expanded count equals the numeric prefix, including every folded member. Shared `same_line_inner` folds only strict same-line containment; multiline and equal intervals remain separate.
4. Compare `Counter`s of those site tuples, including same-line multiplicity, with exact code (no quote normalization for member sites). Require `0` lost / `0` added across `767` paths sites (`158` bundles + `209` singletons) and `37` diff `PY01` sites (`9` bundles + `13` singletons) plus `2` `PY01`-origin `PY10a` sites.
5. Verify root receiver provenance separately: collect the old singleton receiver/binding/declaration clauses; omit only the intermediate receiver expressions whose unparsed call matches a folded inner call (quote-insensitive). Require the new receiver section to preserve every root receiver's clauses exactly once, merging distinct same-receiver declarations in source order. Check covering span, first member's JSON `code`, absence of `_reflect` in public output, and unchanged `PY01_bundle_site` / `PY01_bundle_inner` / `PY01_bundle` formatting. Compact `Code:` is suppressed on multi-site `PY01` / `PY04` bundles with the numeric-prefix predicate above and retained on singleton/module calls, including union annotations.
6. Require `0` non-`PY01`/`PY02` candidate changes (`1,763 == 1,763` paths / `48 == 48` diff `PY04` sites; `94 == 94` paths / `17 == 17` diff `PY10c` uniform parameters; `4 == 4` diff `PY10a` pairings; `874` paths / `52` diff non-`PY01`/`PY02` records `100%` unchanged). Record compaction is `767 → 367` paths (`-400`, `52.2%`) and `37 → 22` diff (`-15`, `40.5%`), without losing promoted sites.

**Official `v0.5` & `v0.5-diff-head5` snapshots and `PY02` stratum verification (Tickets 02 & 03):**

- **Scanner fingerprint (`skills` HEAD `3149d54ec19e`, `skills_dirty: false`)**: Combined scanner SHA256 `449a84d405a7` (full: `449a84d405a73efb2e8bf9c9a74984f370f5318dd7b88a3b8ad715786a5a24ea`), `SKILL.md` SHA256 `1e4635823d61`, `references/*.md` SHA256 `86a88d2784fe`, overall skill SHA256 `70b1950e9c51`.
- **`v0.5` (`paths` mode, `meta.json` SHA256 `baae5067974b2629ac112664cd0055d32c5b4b8867c37ea57599e81789435bda`)**: `2,625` candidates (`anti-defensive-coding` `1`, `Clonoth` `1,090`, `fox-dayi-bot` `350`, `Zoaholic` `791`, `dc_dojo_bot_moli` `362`, `httpx` `31`), `0` parse errors, `8.067s` wall-clock.
- **`v0.5-diff-head5` (`diff` mode `HEAD~5..HEAD`, `meta.json` SHA256 `c650123afb3f1ea83e7bc79d803656ba49201896f713955bfac14ca96296a2ae`)**: `158` candidates (`anti-defensive-coding` `0`, `Clonoth` `16`, `fox-dayi-bot` `9`, `Zoaholic` `57`, `dc_dojo_bot_moli` `76`, `httpx` `0`), `0` parse errors, `9.114s` wall-clock.

| Comparison Report | Total Candidates | Removed / Added / Modified | Delta Breakdown |
| --- | ---: | --- | --- |
| `reports/comparisons/v0.4-vs-v0.5.md` (`paths`) | `3,025 → 2,625` (`-400`) | `-543 / +143 / ~1,398` | `PY01` bundling (`-543 / +143 / ~15`, net `-400`) + `PY02` stratum evidence (`~1,383`: `Clonoth` `498`, `Zoaholic` `411`, `fox-dayi-bot` `193`, `dc_dojo_bot_moli` `278`, `httpx` `3`, self `0`) |
| `reports/comparisons/v0.4-diff-head5-vs-v0.5-diff-head5.md` (`diff`) | `173 → 158` (`-15`) | `-21 / +6 / ~86` | `PY01` bundling (`-21 / +6 / ~3`, net `-15`) + `PY02` stratum evidence (`~83`: `Clonoth` `4`, `Zoaholic` `24`, `fox-dayi-bot` `2`, `dc_dojo_bot_moli` `53`, `httpx` / self `0`) |

Temporary `v0.5-wip-*` snapshots and comparisons were removed after verification, and `reports/VERSION_LEDGER.md` was regenerated with all `12` official snapshots (`v0-baseline` through `v0.5-diff-head5`).

Compare multisets of `(target, path, span, symbol, rule, change_tag, code, verify)` and all non-evidence fields, requiring equality. Only the existing non-raising `PY02` evidence changes. Paths partition into `353` `silent-pass` (`PY02_silent_pass`), `330` `returns-fallback: {value}` (`PY02_returns_fallback`), `151` `fallthrough-logged-exc-info` (`PY02_fallthrough_exc_info`), and `549` `fallthrough-no-traceback` (`PY02_fallthrough_no_tb`). Diff partitions are `20 / 19 / 21 / 23`. Each mode retains the unchanged `NoReturn`-only `dc_dojo_bot_moli/core/quiz_eligibility.py:[182, 183]` record. Synthetic cases also verify raising-only hook facts and `"; "` ordering behind a stratum for mixed exits.

Match all `59` rows of `/home/roocode/adc_scanning_test/reports/audits/v0.2-PY02-paired-validation.md` §2 by unique `(target, path, span)` against the new paths snapshot: `4` silent-pass → pass, `9` returns-fallback → return, `22` logged + `24` no-traceback → `46` fallthrough, yielding `59/59 (100%)`. The historical matrix calls one symbol `Permission.permission`; its current symbol is `PermissionCog.permission` at the same handler coordinates. Both comparisons flag only the expected self-input change; it remains `1` `scope.py:diff_scope` paths candidate / `0` diff candidates. `SKILL.md` and `references/*.md` fingerprints are unchanged.

### 2.5 P2 Validation & Official `v0.6` 11-Target Benchmark Baseline (`v0.5-11t` → `v0.6`)

- **Scanner fingerprint (`skills` HEAD `b4da982bf7c1`, `skills_dirty: false`)**: Combined scanner SHA256 `0de7cba37d9f` (full: `0de7cba37d9fd361f022d53a6abca466c87a9278b13ce1e0f172289e2edfd80a`), `SKILL.md` SHA256 `1e4635823d61`, `references/*.md` SHA256 `c9bf4c244ec5`, overall skill SHA256 `2bd21c817562`.
- **`v0.6` (`paths` mode, 11 targets, `1,724` files scanned, `meta.json` SHA256 `7726a3f2e347350c8768013a464f39af7e61de1ac2801e9f64fea1635a8b185a`)**: `3,512` candidates (`anti-defensive-coding` `1`, `Clonoth` `1,091`, `fox-dayi-bot` `350`, `Zoaholic` `791`, `dc_dojo_bot_moli` `363`, `httpx` `31`, `wardrowbe` `140`, `beets` `116`, `ruamel-yaml` `136`, `dbt-mcp` `119`, `mcp-atlassian` `374`), `0` parse errors, `19.608s` wall-clock.
- **`v0.6-diff-head5` (`diff` mode `HEAD~5..HEAD`, 11 targets, `344` / `1,664` files scanned [`337` / `1,649` across the 10 external targets], `meta.json` SHA256 `e491be890d10abff689f4c104015d240a9d3ac69c10f50d2d13a3dd7507dbed9`)**: `211` candidates (`anti-defensive-coding` `0`, `Clonoth` `17`, `fox-dayi-bot` `9`, `Zoaholic` `57`, `dc_dojo_bot_moli` `76`, `httpx` `0`, `wardrowbe` `48`, `beets` `1`, `ruamel-yaml` `0`, `dbt-mcp` `0`, `mcp-atlassian` `3`), `0` parse errors, `19.234s` wall-clock.

| Comparison Report | Total Candidates | Removed / Added / Modified | Exact Attribution |
| --- | ---: | --- | --- |
| `reports/comparisons/v0.5-11t-vs-v0.6.md` (`paths`) | `3,560 → 3,512` (`-48`) | `-53 / +5 / ~0` (`-52 / +4 / ~0` across the 10 frozen external targets) | **`-52` External Removed**: `G1` (`-23` unbundled raw-text assertions in `dbt-mcp` `-9` and `mcp-atlassian` `-14`), `G2` (`-7` self-patch binding disambiguation FPs in `wardrowbe` `-4`, `mcp-atlassian` `-2`, `beets` `-1`), `G3` (`-3` `PY07` `dict_dependency` scalar value-token FPs in `mcp-atlassian`), `S3-1` (`-17` FastMCP `@*.tool()` FPs in `mcp-atlassian`), and `S3-2` (`-2` `typing.Protocol` method FPs in `dbt-mcp`).<br>**`+4` External Added**: `G1` (`+2` folded compound raw-text assertion records in `dbt-mcp`) and `P1` `PY10c:stub` (`+2` constant-return stub TPs: `Clonoth` `_run_idle_cleanup` and `dc_dojo_bot_moli` `get_privilege_roles`).<br>Self-scan `[INPUT_CHANGED]` `scope.py:diff_scope` shifted `:67-72` → `:68-73` (`-1 / +1`).<br>**`~0` Modified**: `0` extractor payload churn on unchanged candidates. |
| `reports/comparisons/v0.5-11t-diff-head5-vs-v0.6-diff-head5.md` (`diff`) | `214 → 211` (`-3`) | `-4 / +1 / ~0` | **`-4` Removed**: `G2` (`-4` `wardrowbe` `frozen_clock` `patch("app.utils.timezone.datetime")` vs. imported `datetime(...)` constructor FPs).<br>**`+1` Added**: `P1` `PY10c:stub` (`Clonoth` `_run_idle_cleanup` TP).<br>**`~0` Modified**: `0` extractor payload churn on unchanged candidates. |
Temporary `v0.6-wip-*` snapshots and comparison reports were removed after verification, and `reports/VERSION_LEDGER.md` was regenerated with all `16` official snapshots (`v0-baseline` through `v0.6-diff-head5`).
