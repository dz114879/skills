# Anti-Defensive-Coding (ADC) Scanner Roadmap (Post-v0.3)

Baseline at roadmap creation:
- **Skill / Scanner Version**: `v0.3` (`/home/roocode/skills` HEAD `67fc36e40443`)
  - `scripts/scan_python.py`: SHA256 `ac81f540fe91` (3,050 lines)
  - `tests/test_scan_python.py`: 4,349 lines
  - `SKILL.md`: SHA256 `1e4635823d61`
  - `references/{python,sql,tests}.md`: Combined SHA256 `86a88d2784fe`
- **Benchmark Corpus (`/home/roocode/adc_scanning_test`, audit reports under `reports/audits/`)**:
  - `v0.3` (`paths` mode, 6 targets: 5 frozen external targets + 1 `anti-defensive-coding` self-scan target): **3,025** candidates (`PY02` 1,384; `PY01` 767; `PY04` 673; `PY10c` 155; `PY08` 22; `PY09` 16; `PY05` 7; `PY07` 1; `PY03` 0; `PY10b` 0), `0` parse errors, `24.5s` wall-clock.
  - `v0.3-diff-head5` (`diff` mode `HEAD~5..HEAD`, 6 targets): **173** candidates, `0` parse errors, `19.5s` wall-clock.

Latest official benchmark baseline (`v0.4.4` — Roadmap P0, P0.5 & P0.5b completed; official snapshot tags remain `v0.4` / `v0.4-diff-head5`):
- **Skill / Scanner Version**: `v0.4.4` (`/home/roocode/skills` clean implementation HEAD `9df023d00a27`)
  - `scripts/scan_python.py` + `scripts/adc_scanner/{__init__,rules.json,registry,scope,ast_helpers,probes_prod,probes_test,reachability,bundling}`: Combined scanner SHA256 `755a50f3d7fa` (full: `755a50f3d7fa5f2cd6b63dd1f4b25a1330c2791a9d34cae48133d120dfad1b6e`; `scan_python.py` `2634d0c7f532`, `rules.json` `d40a10b3a414`, `registry.py` `3e91a79ff446`, `scope.py` `04ba6ad39c39`, `ast_helpers.py` `2f865f566ecc`, `probes_prod.py` `80fd9d87282a`, `probes_test.py` `72998d5b9667`, `reachability.py` `b50a634f5961`, `bundling.py` `b8690e3d69e9`)
  - `tests/{helpers,test_scope,test_py01_py04,test_py02,test_py05_py09,test_py10_diff_reach}.py`: `111` tests across 5 modules, including one module-level registry test (`103` migrated tests + `8` output-contract tests); Ticket 06 passed `uv run python -B -m pytest -W error`, Ruff lint/format and self-scan gates.
  - Historical `v0.4.3` harness measurement (Roadmap P0.5): `103` tests as native `pytest` classes (`pytest` `9.2.0` dev dependency, `ruff` `PT` enabled), passing with `0` `.pytest_cache` / `__pycache__` artifacts (`12.45s` full suite, `1.28s–4.87s` per module, measured with CPU turbo boost off; the `~8.83s` `v0.4.2` figure was measured with turbo boost on and takes `11.7s–11.9s` with it off).
  - `SKILL.md`: SHA256 `1e4635823d61`
  - `references/{python,sql,tests}.md`: Combined SHA256 `86a88d2784fe` (overall skill SHA256 `d18bb2fc3aef`)
- **Benchmark Corpus (`/home/roocode/adc_scanning_test`, `reports/VERSION_LEDGER.md`)**:
  - `v0.4` (`paths` mode, 6 targets, `meta.json` SHA256 `6f8191ff1aafb1815a46a6589814a44337fe4a2ae6e6a3b54347dcdd501c863c`): **3,025** candidates (`3,024` frozen external + `1` `scripts/adc_scanner/scope.py:diff_scope` self-scan `PY04` bundle), `0` parse errors, `7.651s` wall-clock. Format-independent coordinates and member sites are unchanged from pre-`v0.4.4` (`0` lost / `0` added).
  - `v0.4-diff-head5` (`diff` mode `HEAD~5..HEAD`, 6 targets, `meta.json` SHA256 `f8a27a32a095dc6ace6e93ee3a26b86d10bf2869c97cc105512920ea0326db92`): **173** candidates (all frozen external, with `0` lost / `0` added coordinates/member sites), `0` parse errors, `8.735s` wall-clock. The old self-scan record is absent because `scope.py` has aged out of `HEAD~5` (`42890ed6a124..9df023d00a27`); the former six-target diff total was `174`.

Current implementation (`v0.5.1`, P1.1–P1.3 completed; official `v0.5` snapshot promotion remains Ticket 03):
- `166` unit test cases across 5 modules (`121` test functions/methods, including `2` module-level registry contracts): `117` cases after Ticket 01 plus `49` Ticket 02 cases (`48` parametrized handler cases and `1` registry test). `uv run python -B -m pytest -W error` passed in `14.82s`; Ruff lint/format and the `1`-candidate, `0`-parse-error self-scan gates passed with `0` cache/bytecode artifacts.
- `v0.5-wip-01` / `v0.5-wip-01-diff` establish the lossless `PY01` bundling baseline (`2,625` paths / `158` diff candidates; `767 → 367` paths `PY01` records and `37 → 22` diff records).
- `v0.5-wip-02` / `v0.5-wip-02-diff` preserve all candidate coordinates and change only `PY02` evidence: paths `-0 / +0 / ~1,383`, diff `-0 / +0 / ~83`. Paths strata are `353` `silent-pass`, `330` `returns-fallback`, `151` `fallthrough-logged-exc-info`, and `549` `fallthrough-no-traceback`, plus `1` unchanged `NoReturn`-only record. Paired validation matches `59/59` coarse strata (`4 / 9 / 46`, with fallthrough split `22 / 24`).

---

## Priority Overview

Progression principle: **Stabilize modular foundation at zero behavioral delta (P0) → Reduce high-volume reviewer burden & verify v0.3 bundling (P1) → Expand benchmark corpus & evaluate low-frequency / deferred probes (P2) → Extend to new languages from real-world issue corpora (P3).**

| Priority | Phase | Core Scope | Acceptance / Exit Gate |
| --- | --- | --- | --- |
| **P0** | Modular Python Architecture, Declarative Rule Metadata & Test Split (`v0.4`) | Split `scan_python.py` (3,050 lines) into `scripts/adc_scanner/` behind the unchanged `scripts/scan_python.py` CLI entrypoint; extract rule metadata, `Verify`/`Evidence` templates, and probe vocabularies into `scripts/adc_scanner/rules.json`; replace repeated AST subtree walks with single-pass `TreeIndex`/`ScopeIndex` and `unparse` caching; split `test_scan_python.py` (4,349 lines, `103` methods) into `tests/{helpers,test_scope,test_py01_py04,test_py02,test_py05_py09,test_py10_diff_reach}.py`. | `audit_helper.py compare v0.3 v0.4` and `v0.3-diff-head5 v0.4-diff-head5` produce **`-0 / +0 / ~0` (100% multiset equivalence) across all 5 frozen external targets**, and the 6th self-scan target (`anti-defensive-coding`, flagged `[INPUT_CHANGED]` when splitting `scan_python.py`) retains only its `scripts/adc_scanner/scope.py:diff_scope` lookup candidate (`1` in `paths` mode and `1` relocated in `diff` `HEAD~5..HEAD`); full 6-target `paths` runtime drops from `24.5s` to `7.15s–8.40s` (`8.404s` in official `v0.4`) and unit test suite runtime drops from `~56.7s` to `~8.83s` (`0.83s–3.32s` per test module). |
| **P0.5 (Pre-P1)** | `unittest` → `pytest` Test Harness Migration (`v0.4.3`) | Migrate `engineering/anti-defensive-coding/tests/` (`103` tests across 5 modules + `helpers.py`) from `unittest.TestCase` to native `pytest` (`ExtractorTestCase` autouse fixtures, plain `assert` statements, unwrapped in-method `subTest` loops preserving `103` collected items, and `-p no:cacheprovider` + `python -B -m pytest` so neither `.pytest_cache` nor `__pycache__` violates the read-only worktree invariant) before P1 changes `PY01`/`PY02` output shapes. | Zero production scanner changes (`scripts/scan_python.py` and `scripts/adc_scanner/*` untouched); all 103 tests pass under `uv run python -B -m pytest` with `0` cache/bytecode artifacts; `ruff check` (with `PT` enabled) / `ruff format --check` clean; `.dev/adc/testing.md` updated with `uv run python -B -m pytest` commands. |
| **P0.5b (Pre-P1)** | Agent-Facing Output Format, Verification Legend & Context-Density Optimization (`v0.4.4`) | Convert all 29 `verify_templates` in `rules.json` to static strings keyed by short variant tags (`PY01`, `PY02`, `PY02:hook`, `PY04:falsy`, etc.); emit a per-scan `Verification Legend` header in `--format compact` and drop per-candidate `Verify:` rows (`-45.7%` compact volume); group compact output by file (`=== path ===`) with `:L` single-line spans and non-default `change_tag` filtering; eliminate `Code`/`Evidence` duplication and multi-line body dumps across `PY01`, `PY02`, `PY04` (including `[inner: ...]` same-line nested folding), and `PY10c`; enforce explicit `...` on `>200`-char `code` truncation. | 100% coordinate and member-site parity (`3,025` paths / `173` diff, as `scope.py` ages past `HEAD~5`; `0` lost or added sites) across all 6 targets; `--format compact` character count reduced by ~60%+; all unit tests (`uv run python -B -m pytest -W error`) and `ruff` checks pass; official `v0.4` / `v0.4-diff-head5` snapshots and `v0.3-vs-v0.4*` comparisons refreshed. |
| **P1** | `v0.3` Bundling Reviewer Validation + `PY01` / `PY02` High-Volume Ergonomics | Completed Goldfish Reviewer validation on `16` `v0.4.4` compact `PY04` / `PY10c` bundles; introduced lossless per-function `PY01` bundling (`767 → 367` paths records); enriched `1,383` non-raising `PY02` records with `silent-pass`, `returns-fallback: <expr>`, `fallthrough-logged-exc-info`, and `fallthrough-no-traceback`. | `11/11` `PY04` TP recall and severity agreement, `13/13` `PY10c` parameter agreement; lossless `PY01` site equality with `52.2%` paths record compaction; `PY02` coarse stratum parity `59/59`. Official `v0.5` capture/promotion remains Ticket 03. |
| **P2** | Benchmark Corpus Expansion, Multi-Window Diff Audits, Deferred Probe Triage & Multi-Threaded Pipeline (Python 3.15) | Add 3–5 new Python targets covering underrepresented archetypes (Pydantic/SQLAlchemy CRUD, CLI/data pipeline, mock-heavy test suites) and multiple PR diff windows; measure low-count rules (`PY03`, `PY05`, `PY07`–`PY09`, `PY10b`) and the 11 deferred probes in `/home/roocode/adc_scanning_test/reports/audits/proposal-ledger-v0.md` §7; evolve the single-threaded pipeline into a deterministic multi-threaded map-reduce (`ThreadPoolExecutor`, targeting Python 3.15 free-threading). | Every active probe has measured precision on >=10 hits or synthetic+corpus proof; each §7 deferred probe is either promoted with `0` extractor noise or permanently rejected into `.dev/adc/dead-ends.md`; multi-threaded scan produces 100% byte-identical output (`-0 / +0 / ~0`) to single-threaded execution while scaling across cores on Python 3.15+. |
| **P3** | Cross-Language Expansion (Corpus-First) | Build a 100+ issue real-world over-defense corpus in the next target language (e.g., TypeScript or Rust) using `SKILL.md`'s Four Gates & Eight Families; author `references/<lang>.md` and map linter overlap (`tsc`/`biome`/`clippy`) before building a language extractor. | `references/<lang>.md` backed by real project audits and Counterweight proofs before any `scan_<lang>` script or `ast-grep` / `tree-sitter` extractor is written. |

---

## Detailed Phase Specifications

### P0: Modular Python Architecture, Declarative Rule Metadata & Performance

#### 1. Why Modular Python Over a Rust Rewrite Now
- **Runtime bottleneck is algorithmic, not CPython `ast.parse`**: Parsing all 551 Python files across the 6 targets takes <0.4s in CPython 3.13. The 24.5s `paths` runtime in `v0.3` comes from repeated subtree walks inside `scan_python.py` (e.g., `PY01`'s `receiver_binding` sorting `lexical_nodes` and running `ast.unparse` per site, and `CrossReferences.visit_scope` walking every function body repeatedly). Single-pass scope indexing fixes this in pure Python.
- **Zero distribution friction**: Keeping the extractor in Python 3.11+ standard library preserves zero-build execution (`python3 <skill-dir>/scripts/scan_python.py`) across any environment where the skill is installed.
- **Fast iteration**: P1 and P2 still adjust probe evidence, bundling, and deferred AST guards; Python keeps the edit-to-snapshot cycle under 5 seconds.

#### 2. Package & Test Layout (Current `v0.5.1`)
Keep `scripts/scan_python.py` as the stable CLI entrypoint (`sys.dont_write_bytecode = True`; `SKILL.md` requires zero CLI changes), delegating to the acyclic internal package `scripts/adc_scanner/`:
- `scripts/scan_python.py` — CLI argument parsing, `sys.dont_write_bytecode = True`, `sys.path` setup, entrypoint (`main`), and top-level error handling.
- `scripts/adc_scanner/`:
  - `__init__.py` — Minimal package docstring marker (zero barrel re-exports).
  - `rules.json` + `registry.py` — Declarative rule metadata, `Verify`/`Evidence` variant templates, and immutable probe vocabularies (`verify_text`, `evidence_text`).
  - `scope.py` — Git and explicit-path scope resolution (`ScanError`, `FileChange`, `git`, `worktree_root`, `resolve_ref`, `diff_scope`, `paths_scope`, `excluded`, `role`, `touches`, `intersects`).
  - `ast_helpers.py` — Single-pass `TreeIndex` (`tree_index`), lazy `ScopeIndex` (`scope_index`), cached `unparse`, `CandidateRecords`, control-flow `exits`, receiver/class declaration helpers (`PY01`), retry/cleanup proofs and mechanical `handler_stratum` (`PY02`), and state/lock helpers (`PY07`–`PY09`).
  - `probes_prod.py` — Production AST probes (`CandidateProbes`: `PY01`–`PY09` plus `_DEFAULT_*` production hook comparisons for `PY10b`).
  - `probes_test.py` — Test AST probes (`TestProbes`, `PatchWindow`, `patch_windows`, `patch_symbol`, `is_patch_constructor`, `raw_text_origin`, `GetterReturn` for `PY10b`).
  - `reachability.py` — Diff moved-defense pairing (`moved_defenses` for `PY10a`) and whole-repo cross-reachability (`cross_reachability`, `snapshot_sources`, `CrossReferences`, `effective_arguments`, `constant_defaults`, `module_scope`, `public_contracts` for `PY10c`).
  - `bundling.py` — Lossless post-extraction record bundling (`bundle_reflections`, `bundle_coalesces`) with shared `same_line_inner`, orchestration (`scan`), and output rendering (`render`).
- `tests/`:
  - `helpers.py` — Sets `sys.dont_write_bytecode = True`, defines `SCRIPT`, `SKILLS_ROOT`, and `ExtractorTestCase` (`put`, in-process `cli`, `cli_subprocess`, `scan`, `git`, `init_repo`, `commit`, and class-level read-only Git fixture helpers `git_fixture` / `use_git_fixture`).
  - `test_scope.py` (`ScopeTests`, `20` methods + `2` module-level registry tests; `22` cases) — CLI/JSON/compact contracts, 29-key verification bijection, 4 `PY02` stratum templates, codecs/conflicts, diff flags, symlink skipping, and working-tree/git-status immutability.
  - `test_py01_py04.py` (`Py01Py04Tests`, `20` methods/cases) — `PY01` reflection/memo-guard/receiver evidence, lossless per-function reflection bundling with nested folding and provenance deduplication, singleton/module and compact `Code:` preservation, and `PY04` lossy coalesce + bundling (`K4-B`).
  - `test_py02.py` (`Py02Tests`, `15` methods / `59` collected cases) — Error washing, deferred propagation, all 4 handler strata, return ordering/deduplication/truncation, nested-scope exclusion, traceback enable/disable evidence, abort/hook coexistence, and `Z-T1` `try`-body diff scope.
  - `test_py05_py09.py` (`Py05Py09Tests`, `21` methods/cases) — `PY03` default shadows guard, `PY05` dead parameter evasion, `PY07` state smuggling, `PY08` copy/roundtrip churn, and `PY09` lock/transaction bleed.
  - `test_py10_diff_reach.py` (`Py10DiffReachTests`, `43` methods / `44` cases) — `PY10a` moved defenses (`D2`), reflection-bundling promotion interactions, `PY10b` test smells and lexical patch windows (`D3`, `D10`), and `PY10c` cross-reachability and parameter bundling (`D4b`).

#### 3. Hybrid JSON Rule Extraction (Avoiding the Inner-Platform Trap)
- **Move to JSON (`rules.json`)**:
  - Rule catalog metadata (`rule_id`, `family`, `catalogue_section`, `bundling_policy`, `diff_scope_mode`).
  - `Verify` prompt templates and `Evidence` format strings (decoupling prompt wording updates like `v0.2.1` from AST code).
  - Probe signature tables and vocabularies: `SKIP_SEGMENTS`, `TEST_GLOBS`, `PY07` dependency key suffixes (`service`, `repo`, `session`, etc.), `PY08` roundtrip call pairs and loop-copy functions, `PY09` lock/transaction context tokens and I/O call names, `PY10c` framework hook decorator names.
- **Keep in Python**:
  - Multi-statement control-flow and cross-scope mechanics (`saved_retry`, `bounded_retry`, `stored_primary_cleanup`, `memo_guard`, `same_class_stashes`, `patch_windows`, `CrossReferences`, `effective_arguments`).


#### 4. P0.5 (Pre-P1): `unittest` → `pytest` Migration (`v0.4.3`)
- **Why before P1 instead of after P1**:
  - Right after `v0.4`, the scanner's output contract is frozen (`-0 / +0 / ~0`), giving a 100% deterministic baseline to verify the test-runner migration without touching `scripts/`.
  - P1.2 (`PY01` bundling) and P1.3 (`PY02` handler stratum `evidence`) will update expected candidate payloads across `test_py01_py04.py`, `test_py02.py`, `test_scope.py`, and `test_py10_diff_reach.py`, and add new multi-case tests for the 4 `PY02` exit strata. Migrating to `pytest` first avoids rewriting those assertions twice and unlocks `@pytest.mark.parametrize` and `pytest`'s rich dict/list assertion diffs during P1 development.
- **Migration scope**:
  - Add `pytest` to `[dependency-groups] dev` in `pyproject.toml`, enable `"PT"` (`flake8-pytest-style`) in `[tool.ruff.lint] select`, and configure `[tool.pytest.ini_options]` with `testpaths = ["engineering/anti-defensive-coding/tests"]`, `python_classes = ["*Tests", "Test*"]`, and `addopts = ["-p", "no:cacheprovider"]` alongside interpreter-level `uv run python -B -m pytest` invocation (so `sys.dont_write_bytecode = True` is active before `pytest` assertion-rewriting or `helpers.py` import runs, preserving `0` `.pytest_cache` and `0` `__pycache__` artifacts).
  - Replace `unittest.TestCase` (`self.assertEqual`, `self.assertIn`, `self.assertRaises`, `@unittest.skipIf`, `setUpClass`/`setUp`) with native `pytest` assertions, `pytest.raises`, `@pytest.mark.skipif`, and `ExtractorTestCase` `autouse` fixtures (`tmp_path_factory` / `tmp_path`), unwrapping `with self.subTest(...):` into plain in-method `for` loops with `assert` to preserve `103`-item collection parity and the `~8.8s` shared-workspace runtime (reserving `@pytest.mark.parametrize` for new independent P1 tests).
- **Status (`v0.4.3`, completed)**: `helpers.py` `ExtractorTestCase` is a plain base class with a class-scoped `autouse` `@classmethod` fixture (`tmp_path_factory` Git template, as required by `pytest` 9's deprecation of instance-method class fixtures) and a function-scoped `autouse` fixture (`self.root = tmp_path`); `79` `subTest` blocks were unwrapped into in-method loops (the `3` case `label` loop variables that only fed `subTest` now appear in assertion messages), and `507` `self.assert*` / `assertRaises` calls were converted to `assert` / `pytest.raises`. With turbo boost off, the suite takes `12.45s` under `pytest` versus `11.7s–11.9s` under `unittest` (`~5%` runner overhead, no regression from the migration).

#### 5. P0.5b (Pre-P1): Agent-Facing Output Format, Verification Legend & Context-Density Optimization (`v0.4.4`)
- **Why before P1**:
  - Quantitative audit of `v0.4` across the 6 targets (`3,025` candidates, `1,687,523` compact chars / `1,974,660` JSON chars) showed that **45.74%** (`771,798` chars) of `--format compact` is repeated `Verify:` boilerplate, **18.81%** (`317,390` chars) is candidate headers repeating file paths (`63,563` chars), long rule suffixes (`50,340` chars), default `[path-audit]` tags (`38,285` chars), and single-line `L-L` spans (`1,188` rows), and `Code:`/`Evidence:` either duplicate each other (`PY04` bundles/singletons, `PY10c` defaults) or dump multi-line function/`except` bodies into a single line before silent clipping at char 200 (`174` truncated records).
  - Cleaning up the output contract and template vocabulary before P1.1 (Goldfish Reviewer validation) and P1.2/P1.3 (`PY01` bundling and `PY02` stratum tags) directly benefits the P1 reviewer experiments and ensures `PY01`/`PY02` build on the compact template conventions.
- **Scope (`v0.4.4`, Tickets 06 & 07 in `.scratch/anti-defensive-coding-v0.4/`)**:
  - Make all 29 `verify_templates` in `rules.json` static strings keyed by short variant tags (`PY01`, `PY02`, `PY02:hook`, `PY03`, `PY03:get`, `PY04:falsy`, `PY04:truthy`, `PY04:mixed`, `PY05`, `PY07:stash`, `PY07:try`, `PY07:dict`, `PY08:comp`, `PY08:roundtrip`, `PY08:loop`, `PY09:tx-check`, `PY09:lock`, `PY09:tx`, `PY10a:PY01`, `PY10a:PY02`, `PY10a:PY04`, `PY10b:hook`, `PY10b:legacy`, `PY10b:raw-text`, `PY10b:mutation`, `PY10b:patch`, `PY10c:default`, `PY10c:bundle`, `PY10c:reach`), forming a 1-to-1 bijection (`VERIFY_KEYS`).
  - In `--format compact`, print `Scanned ... in <mode> mode; ...`, a per-scan `Verification Legend:` listing only triggered variant keys, file group headers (`=== <path> ===`), compact candidate headers (`[C01] :75 (symbol) PY01` with `:L` single-line spans and non-default `[change_tag]` only), omit redundant `Code:` lines on `PY04` bundles and `PY10c` `constant_defaults`, and omit per-candidate `Verify:` lines.
  - Slice `FunctionDef`/`AsyncFunctionDef` `code` to the `def ...:` signature header only (`PY10c` `cross-test`/`orphaned`), summarize `>200`-char `ExceptHandler` `code` (`PY02`), keep `members[0]["code"]` on bundled `PY04` while folding same-line nested sites as `[inner: <code> -> <fallback>]` in `evidence`, merge declared defaults into `PY10c` `constant_defaults` `evidence`, drop `"receiver class unknown"` in `PY01` when `cls is None`, shorten `"at line L"` / `"line L"` to `"at :L"` / `":L"`, and append `"..."` whenever `code` exceeds 200 chars (`code[:197] + "..."`).
  - Refresh official `v0.4` (`3,025` paths) and `v0.4-diff-head5` (`173` diff, with `scope.py` aged past `HEAD~5`) snapshots and `v0.3-vs-v0.4*` comparison reports at 100% coordinate and member-site parity.
- **Status (`v0.4.4`, completed 2026-11-04)**: Reused and verified the clean-HEAD `v0.4-wip-03` / `v0.4-wip-03-diff` captures, then promoted them without changing payload/archive bytes. Refreshed both official comparisons and the 10-snapshot ledger; removed staging directories and the temporary verifier. Detailed evidence: `/home/roocode/adc_scanning_test/reports/audits/v0.4.4-benchmark-verification.{md,json}`; pre-refresh snapshots remain in `reports/audits/v0.4.4-pre-refresh.tar.gz`.
  - **Lossless gate**: exact `Counter` equality for `3,025` paths / `173` external diff coordinates; `1,763` paths `PY04` sites (`1,761` external + `2` self) / `48` external diff sites; `94` / `17` `PY10c` uniform parameters; `4` diff `PY10a` moved-defense pairings. All have `0` lost / `0` added. Folded inner sites remain represented (`41` paths / `2` diff); zero parse errors. The old self diff's `1` record / `2` sites is the explicitly verified input-window exclusion.
  - **Exact CLI density** (stdout including final newlines, each archived renderer applied to its own snapshot; JSON uses default `json.dumps`, not the indented snapshot files):

    | Scope | Format | Characters old → new | Saved characters (%) | UTF-8 bytes old → new | Saved bytes (%) |
    | --- | --- | ---: | ---: | ---: | ---: |
    | 6 targets, paths | compact | 1,687,523 → 645,206 | 1,042,317 (61.766091%) | 1,707,406 → 654,279 | 1,053,127 (61.679940%) |
    | 6 targets, paths | json | 1,974,660 → 1,862,959 | 111,701 (5.656721%) | 1,974,660 → 1,862,959 | 111,701 (5.656721%) |
    | 6 targets, diff | compact | 96,791 → 48,496 | 48,295 (49.896168%) | 98,327 → 49,475 | 48,852 (49.683200%) |
    | 6 targets, diff | json | 114,503 → 108,967 | 5,536 (4.834808%) | 114,503 → 108,967 | 5,536 (4.834808%) |
    | Clonoth `supervisor/task_router.py`, paths | compact | 33,532 → 14,362 | 19,170 (57.169271%) | 33,661 → 14,366 | 19,295 (57.321529%) |
    | Clonoth `supervisor/task_router.py`, paths | json | 37,895 → 33,063 | 4,832 (12.751023%) | 37,895 → 33,063 | 4,832 (12.751023%) |

  - The `~60%+` gate passes for the full six-target paths workload. Diff's smaller scans have proportionally larger legends and include the expected self-window removal; frozen-external-only compact reduction is `49.631209%` characters / `49.419402%` bytes. The task-router figures are direct single-file CLI scans (`43 → 43` candidates).
  - **Clipping gate**: all `code` strings are at most `200` characters; `33` paths / `4` diff records end in explicit `...` at the limit. Each mode also contains one naturally exact-200-character handler (`dc_dojo_bot_moli/cogs/forum_post_monitor.py:638-641`), verified against the full source AST. Zero untruncated `>200`-character snippets.
  - **Comparison interpretation**: refreshed `v0.3-vs-v0.4` reports `-484 / +484 / ~2,541`; diff reports `-26 / +26 / ~147`. These text-sensitive deltas include `code`/`evidence`/`verify` presentation changes (and the historical self-input relocation), while the format-independent gate above proves semantic parity. Only the self target is flagged `[INPUT_CHANGED]`.

---

### P1: `v0.3` Bundling Validation & High-Volume Rule Ergonomics (`PY01` / `PY02`)

1. **Downstream Goldfish Reviewer Validation (P1.1, completed)**:
   - The stateless Goldfish Reviewer evaluated `16` `v0.4.4` compact bundles: `11` `PY04` bundles (`66` member sites, sizes `2..18`) and `5` `PY10c` bundles (`13` parameters). Results: `11/11` `PY04` TP recall, `11/11` severity agreement, `13/13` `PY10c` parameter agreement, `0` missed TPs. Report: `/home/roocode/adc_scanning_test/reports/audits/v0.4-bundling-reviewer-validation.md`.
   - Whole-function `(path, symbol, change_tag)` bundling is confirmed safe; `PY01` adopts it without receiver splitting.
2. **`PY01_reflect_default` Lossless Bundling (P1.2, `v0.5.0`, completed)**:
   - `bundle_reflections` runs after diff-eligibility filtering and `PY10a` promotion. It preserves singletons and `<module>` calls; bundles two or more sites per `(path, symbol, change_tag)` with covering spans, the first member's `code`, and deduplicated receiver binding/declaration provenance after ` | `.
   - `_reflect = (col_offset, end_col_offset, unparse(call))` supports quote-insensitive redundant-intermediate-receiver removal. Shared `same_line_inner` folds nested same-line calls before span widening, retaining all member counts. `_reflect` is stripped before output.
   - Registry templates: `PY01_bundle_site = ":{line} {code}"`, `PY01_bundle_inner = " [inner: {code}]"`, and `PY01_bundle = "{count} sites: {sites} | {receivers}"`. Compact `Code:` suppression for `PY01` / `PY04` uses `evidence.split(" sites: :", 1)[0].isdigit()`; singleton union annotations retain `Code:`.
   - Paths `767 → 367` records (`-400`, `-52.2%`); diff `37 → 22` (`-15`, `-40.5%`). Unpacked member sites remain equal; see `.dev/adc/testing.md` for the verification recipe.
3. **`PY02_error_washing` Handler Stratum Evidence (P1.3, `v0.5.1`, completed)**:
   - `/home/roocode/adc_scanning_test/reports/audits/v0.2-PY02-paired-validation.md` §4.2 shows sharply different TP rates by exit shape. `handler_stratum` now replaces generic non-raising evidence with mechanical facts, without changing candidate eligibility or verification prompts.
   - Templates: `PY02_silent_pass` → `silent-pass`; `PY02_returns_fallback` → `returns-fallback: {value}`; `PY02_fallthrough_exc_info` → `fallthrough-logged-exc-info`; `PY02_fallthrough_no_tb` → `fallthrough-no-traceback`. Classifier precedence is silent statements (ignoring documentation), local returns (unique source-order values, bare return `None`, 80-character cap), local traceback/`exc_info` calls (excluding constant `False` / `None` / `0`), then remaining fallthrough.
   - Raising-only `NoReturn` and hook-diagnostic handlers keep their existing evidence; mixed exits append those facts after `"; "` behind the stratum. The `59` paired handlers match coarse ground truth at `100%`: `4` silent-pass → pass, `9` returns-fallback → return, `22` logged + `24` no-traceback → `46` fallthrough.

---

### P2: Benchmark Corpus Expansion, Diff-Mode Depth & Deferred Probes

1. **Expand `~/adc_scanning_test/targets/`**:
   - Add 3–5 frozen repositories covering domains underrepresented in the current 6 targets:
     - Heavy Pydantic / SQLAlchemy / FastAPI service (tests boundary revalidation vs internal model trust).
     - Synchronous CLI / data-processing pipeline (tests `PY03`, `PY05`, `PY08`).
     - AI-authored repository with a large `unittest`/`pytest` `mock.patch` suite (tests `PY10b` and `PY10c`).
2. **Multi-Window Diff Evaluation**:
   - Benchmark `diff` mode across multiple real PR / commit ranges per repo (beyond `HEAD~5..HEAD`), auditing the 12 `Z-T1` diff additions and diff-specific edge cases.
3. **Triage `/home/roocode/adc_scanning_test/reports/audits/proposal-ledger-v0.md` §7 Deferred Probes**:
   - Measure the 11 deferred probes (`H1`, `Clonoth D5/D6`, `fox D4/D5/D6`, `dc_dojo C1/FN-2`, `A08-FRESH/SERIAL`, `A05-ID`, `httpx FN-2/FN-5`) across the expanded corpus.
   - Promote only probes with confirmed TPs and zero mechanically preventable extractor noise; move rejected probes with their counterexamples into `.dev/adc/dead-ends.md`.

4. **Deterministic Multi-Threaded Pipeline (Python 3.15 Free-Threading)**:
   - **Why Python 3.15 / Multi-Threading**: With Python 3.15's free-threaded runtime (PEP 703) maturing alongside multi-target / multi-window corpus expansion, both I/O-bound Git operations (`git diff`, `git show`, disk reads) and CPU-bound per-file AST stages (`ast.parse` → `TreeIndex` → `CandidateProbes` / `TestProbes` → `CrossReferences`) can execute in parallel across worker threads without multiprocessing serialization overhead.
   - **Thread-Safety Prerequisites in `adc_scanner/ast_helpers.py`**:
     - Replace or scope module-level mutable caches (`_TREE_INDEX_CACHE`, `_UNPARSE_CACHE`) to per-file / per-worker state so concurrent AST walks never contend on shared dictionaries.
     - Remove or guard process-global `gc.disable()` / `gc.set_threshold()` mutations inside `allocation_batch()` when running across multiple threads.
   - **Deterministic Map-Reduce Design**:
     - Parallelize Stage 1 per-file `git diff` hunk extraction, Stage 2 per-file AST probe extraction (`before`/`after` blobs), and Stage 3 per-file `CrossReferences` / `ModuleScope` extraction over `concurrent.futures.ThreadPoolExecutor`.
     - Keep Stage 3 cross-file reduction (`moved_defenses`, `cross_reachability` aggregation) and Stage 4 post-processing (`bundle_*` and final `(path, span[0], rule)` sorting + `C01`..`Cnn` numbering) deterministic, guaranteeing **100% byte-identical JSON and compact output (`-0 / +0 / ~0`)** regardless of thread count.
---

### P3: Cross-Language Expansion (TypeScript / Rust)

1. **Phase 3a — Real-World Issue Extraction (No Scanner Code)**:
   - Audit 3–5 real repositories in the target language using `SKILL.md`'s Four Gates and Eight Families.
   - Collect 100+ verified over-defensive findings and Counterweights (mirroring the initial 140-issue Python corpus).
   - Author `references/<lang>.md` and explicitly demarcate which single-node patterns are already `Lint-Owned` by `tsc` / `biome` / `oxlint` (TypeScript) or `rustc` / `clippy` (Rust).
2. **Phase 3b — Language Candidate Extractor**:
   - Build `scripts/scan_<lang>` only for 1-hop structural patterns that survive the language's typechecker and standard linter.
   - Evaluate whether `ast-grep` / `tree-sitter` or a native language parser best fits the target language's AST needs.
