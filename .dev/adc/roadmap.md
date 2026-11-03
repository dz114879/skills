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

Current baseline (`v0.4` — Roadmap P0 completed):
- **Skill / Scanner Version**: `v0.4` (`/home/roocode/skills` HEAD `42890ed6a124`)
  - `scripts/scan_python.py` + `scripts/adc_scanner/{__init__,rules.json,registry,scope,ast_helpers,probes_prod,probes_test,reachability,bundling}`: Combined scanner SHA256 `93ca64dd1c32` (`scan_python.py` `2634d0c7f532`, `rules.json` `62c39365659d`, `registry.py` `b194268f7593`, `scope.py` `04ba6ad39c39`, `ast_helpers.py` `a0e011e326ac`, `probes_prod.py` `aa1235c8b52e`, `probes_test.py` `b51b6b41abc6`, `reachability.py` `751e7a4661d5`, `bundling.py` `d2b65eaa141c`)
  - `tests/{helpers,test_scope,test_py01_py04,test_py02,test_py05_py09,test_py10_diff_reach}.py`: `103` test methods across 5 modules (`~8.83s` full discovery; `0.83s–3.32s` per module)
  - `SKILL.md`: SHA256 `1e4635823d61`
  - `references/{python,sql,tests}.md`: Combined SHA256 `86a88d2784fe` (overall skill SHA256 `3a7093bb6b69`)
- **Benchmark Corpus (`/home/roocode/adc_scanning_test`, `reports/VERSION_LEDGER.md`)**:
  - `v0.4` (`paths` mode, 6 targets, `meta.json` SHA256 `f5d7040b3857abe354c1bd43e8813289c3a011caa6697b0f9ffa01adff436772`): **3,025** candidates (`-0 / +0 / ~0` across all 5 frozen external targets vs `v0.3`; `1` relocated `scripts/adc_scanner/scope.py:diff_scope` `PY04` bundle on `anti-defensive-coding`), `0` parse errors, `8.404s` wall-clock (`7.15s–8.40s` range across `v0.4.1`/`v0.4` runs, down from `24.457s` in `v0.3`).
  - `v0.4-diff-head5` (`diff` mode `HEAD~5..HEAD`, 6 targets, `meta.json` SHA256 `7ac088c76acbfb1859f57b0dadb2e3f8253629f42b4870f5115bd512868de9ed`): **174** candidates (`173` across the 5 frozen external targets at `-0 / +0 / ~0` vs `v0.3-diff-head5` + `1` relocated `scripts/adc_scanner/scope.py:diff_scope` `PY04` bundle on `[INPUT_CHANGED]` `anti-defensive-coding`), `0` parse errors, `9.265s` wall-clock (down from `19.500s` in `v0.3-diff-head5`).
---

## Priority Overview

Progression principle: **Stabilize modular foundation at zero behavioral delta (P0) → Reduce high-volume reviewer burden & verify v0.3 bundling (P1) → Expand benchmark corpus & evaluate low-frequency / deferred probes (P2) → Extend to new languages from real-world issue corpora (P3).**

| Priority | Phase | Core Scope | Acceptance / Exit Gate |
| --- | --- | --- | --- |
| **P0** | Modular Python Architecture, Declarative Rule Metadata & Test Split (`v0.4`) | Split `scan_python.py` (3,050 lines) into `scripts/adc_scanner/` behind the unchanged `scripts/scan_python.py` CLI entrypoint; extract rule metadata, `Verify`/`Evidence` templates, and probe vocabularies into `scripts/adc_scanner/rules.json`; replace repeated AST subtree walks with single-pass `TreeIndex`/`ScopeIndex` and `unparse` caching; split `test_scan_python.py` (4,349 lines, `103` methods) into `tests/{helpers,test_scope,test_py01_py04,test_py02,test_py05_py09,test_py10_diff_reach}.py`. | `audit_helper.py compare v0.3 v0.4` and `v0.3-diff-head5 v0.4-diff-head5` produce **`-0 / +0 / ~0` (100% multiset equivalence) across all 5 frozen external targets**, and the 6th self-scan target (`anti-defensive-coding`, flagged `[INPUT_CHANGED]` when splitting `scan_python.py`) retains only its `scripts/adc_scanner/scope.py:diff_scope` lookup candidate (`1` in `paths` mode and `1` relocated in `diff` `HEAD~5..HEAD`); full 6-target `paths` runtime drops from `24.5s` to `7.15s–8.40s` (`8.404s` in official `v0.4`) and unit test suite runtime drops from `~56.7s` to `~8.83s` (`0.83s–3.32s` per test module). |
| **P1** | `v0.3` Bundling Reviewer Validation + `PY01` / `PY02` High-Volume Ergonomics | Run Goldfish Reviewer validation on `v0.3`'s bundled `PY04` (`K4-B`) and `PY10c` (`D4b`) records; introduce lossless per-function/per-receiver bundling for `PY01` (767 rows); enrich `PY02` (1,384 rows) `evidence` with handler exit strata (`silent-pass`, `returns-fallback`, `logged-exc-info`, `logged-no-traceback`). | Zero missed TP sites in bundled `PY04`/`PY10c` reviewer test; `PY01` lossless site equality (`old == new`) with ~50%+ record compaction; `PY02` stratum tags verified against `/home/roocode/adc_scanning_test/reports/audits/v0.2-PY02-paired-validation.md`. |
| **P2** | Benchmark Corpus Expansion, Multi-Window Diff Audits & Deferred Probe Triage | Add 3–5 new Python targets covering underrepresented archetypes (Pydantic/SQLAlchemy CRUD, CLI/data pipeline, mock-heavy test suites) and multiple PR diff windows; measure low-count rules (`PY03`, `PY05`, `PY07`–`PY09`, `PY10b`) and the 11 deferred probes in `/home/roocode/adc_scanning_test/reports/audits/proposal-ledger-v0.md` §7. | Every active probe has measured precision on >=10 hits or synthetic+corpus proof; each §7 deferred probe is either promoted with `0` extractor noise (AST matches mechanically distinguishable as safe without losing TPs) or permanently rejected into `.dev/adc/dead-ends.md`. |
| **P3** | Cross-Language Expansion (Corpus-First) | Build a 100+ issue real-world over-defense corpus in the next target language (e.g., TypeScript or Rust) using `SKILL.md`'s Four Gates & Eight Families; author `references/<lang>.md` and map linter overlap (`tsc`/`biome`/`clippy`) before building a language extractor. | `references/<lang>.md` backed by real project audits and Counterweight proofs before any `scan_<lang>` script or `ast-grep` / `tree-sitter` extractor is written. |

---

## Detailed Phase Specifications

### P0: Modular Python Architecture, Declarative Rule Metadata & Performance

#### 1. Why Modular Python Over a Rust Rewrite Now
- **Runtime bottleneck is algorithmic, not CPython `ast.parse`**: Parsing all 551 Python files across the 6 targets takes <0.4s in CPython 3.13. The 24.5s `paths` runtime in `v0.3` comes from repeated subtree walks inside `scan_python.py` (e.g., `PY01`'s `receiver_binding` sorting `lexical_nodes` and running `ast.unparse` per site, and `CrossReferences.visit_scope` walking every function body repeatedly). Single-pass scope indexing fixes this in pure Python.
- **Zero distribution friction**: Keeping the extractor in Python 3.11+ standard library preserves zero-build execution (`python3 <skill-dir>/scripts/scan_python.py`) across any environment where the skill is installed.
- **Fast iteration**: P1 and P2 still adjust probe evidence, bundling, and deferred AST guards; Python keeps the edit-to-snapshot cycle under 5 seconds.

#### 2. Package & Test Layout (`v0.4`)
Keep `scripts/scan_python.py` as the stable CLI entrypoint (`sys.dont_write_bytecode = True`; `SKILL.md` requires zero CLI changes), delegating to the acyclic internal package `scripts/adc_scanner/`:
- `scripts/scan_python.py` — CLI argument parsing, `sys.dont_write_bytecode = True`, `sys.path` setup, entrypoint (`main`), and top-level error handling.
- `scripts/adc_scanner/`:
  - `__init__.py` — Minimal package docstring marker (zero barrel re-exports).
  - `rules.json` + `registry.py` — Declarative rule metadata, `Verify`/`Evidence` variant templates, and immutable probe vocabularies (`verify_text`, `evidence_text`).
  - `scope.py` — Git and explicit-path scope resolution (`ScanError`, `FileChange`, `git`, `worktree_root`, `resolve_ref`, `diff_scope`, `paths_scope`, `excluded`, `role`, `touches`, `intersects`).
  - `ast_helpers.py` — Single-pass `TreeIndex` (`tree_index`), lazy `ScopeIndex` (`scope_index`), cached `unparse`, `CandidateRecords`, control-flow `exits`, receiver/class declaration helpers (`PY01`), retry/cleanup proofs (`PY02`), and state/lock helpers (`PY07`–`PY09`).
  - `probes_prod.py` — Production AST probes (`CandidateProbes`: `PY01`–`PY09` plus `_DEFAULT_*` production hook comparisons for `PY10b`).
  - `probes_test.py` — Test AST probes (`TestProbes`, `PatchWindow`, `patch_windows`, `patch_symbol`, `is_patch_constructor`, `raw_text_origin`, `GetterReturn` for `PY10b`).
  - `reachability.py` — Diff moved-defense pairing (`moved_defenses` for `PY10a`) and whole-repo cross-reachability (`cross_reachability`, `snapshot_sources`, `CrossReferences`, `effective_arguments`, `constant_defaults`, `module_scope`, `public_contracts` for `PY10c`).
  - `bundling.py` — Lossless post-extraction record bundling (`bundle_coalesces`), orchestration (`scan`), and output rendering (`render`).
- `tests/`:
  - `helpers.py` — Sets `sys.dont_write_bytecode = True`, defines `SCRIPT`, `SKILLS_ROOT`, and `ExtractorTestCase` (`put`, in-process `cli`, `cli_subprocess`, `scan`, `git`, `init_repo`, `commit`, and class-level read-only Git fixture helpers `git_fixture` / `use_git_fixture`).
  - `test_scope.py` (`ScopeTests`, `16` methods) — CLI arguments, exit codes, `cli_subprocess` (`subprocess.run`) and `runpy.run_path` entrypoints, PEP 263 codecs, merge conflicts, `--ignore-space-at-eol`, symlink skipping, and working-tree/git-status immutability.
  - `test_py01_py04.py` (`Py01Py04Tests`, `15` methods) — `PY01` reflection/memo-guard/receiver-evidence and `PY04` lossy coalesce + per-function bundling (`K4-B`).
  - `test_py02.py` (`Py02Tests`, `10` methods) — `PY02` error washing, deferred propagation, and `Z-T1` `try`-body diff scope.
  - `test_py05_py09.py` (`Py05Py09Tests`, `21` methods) — `PY03` default shadows guard, `PY05` dead parameter evasion, `PY07` state smuggling, `PY08` copy/roundtrip churn, and `PY09` lock/transaction bleed.
  - `test_py10_diff_reach.py` (`Py10DiffReachTests`, `41` methods) — `PY10a` moved defenses (`D2`), `PY10b` test smells and lexical patch windows (`D3`, `D10`), and `PY10c` cross-reachability and parameter bundling (`D4b`).

#### 3. Hybrid JSON Rule Extraction (Avoiding the Inner-Platform Trap)
- **Move to JSON (`rules.json`)**:
  - Rule catalog metadata (`rule_id`, `family`, `catalogue_section`, `bundling_policy`, `diff_scope_mode`).
  - `Verify` prompt templates and `Evidence` format strings (decoupling prompt wording updates like `v0.2.1` from AST code).
  - Probe signature tables and vocabularies: `SKIP_SEGMENTS`, `TEST_GLOBS`, `PY07` dependency key suffixes (`service`, `repo`, `session`, etc.), `PY08` roundtrip call pairs and loop-copy functions, `PY09` lock/transaction context tokens and I/O call names, `PY10c` framework hook decorator names.
- **Keep in Python**:
  - Multi-statement control-flow and cross-scope mechanics (`saved_retry`, `bounded_retry`, `stored_primary_cleanup`, `memo_guard`, `same_class_stashes`, `patch_windows`, `CrossReferences`, `effective_arguments`).

---

### P1: `v0.3` Bundling Validation & High-Volume Rule Ergonomics (`PY01` / `PY02`)

1. **Downstream Goldfish Reviewer Validation of `v0.3` Bundling**:
   - Close the evaluation loop noted in `/home/roocode/adc_scanning_test/reports/audits/proposal-ledger-v0.2.md` §3: run a Goldfish Reviewer pass on functions with large `PY04` bundles (e.g., 8–14 sites in a single function) and bundled `PY10c` parameters.
   - Verify whether whole-function bundling preserves 100% per-site TP recall or whether grouping by `(path, symbol, receiver, change_tag)` is cleaner for large functions.
2. **`PY01_reflect_default` Lossless Bundling (767 rows in `v0.3`)**:
   - `Clonoth` (315) and `Zoaholic` (308) frequently probe multiple attributes on the same receiver inside one function (`getattr(cfg, "a", None)`, `getattr(cfg, "b", None)`).
   - Bundle `PY01` per `(path, symbol, receiver_expr, change_tag)` (or per function), preserving every line and attribute in `evidence` with `0` site loss.
3. **`PY02_error_washing` Handler Stratum Evidence (1,384 rows in `v0.3`)**:
   - `/home/roocode/adc_scanning_test/reports/audits/v0.2-PY02-paired-validation.md` §4.2 showed that `PY02` TP rates vary sharply by handler exit shape (in the 64-row stratified `cut2-PY02` sample: `returns_fallback` `68.2%` TP, `pass_body` `50.0%` TP, `fallthrough_log` `22.7%` TP; in the UI-heavy 59-row 20-file `v0.2` paired sample: `returns_fallback` `22.2%–33.3%` TP, `pass_body` `25.0%` TP, `fallthrough_log` `10.9%` TP).
   - Emit the mechanical handler exit stratum and traceback status (`silent-pass`, `returns-fallback: <expr>`, `fallthrough-logged-exc-info`, `fallthrough-no-traceback`) directly in `PY02` `evidence` so reviewers can separate high-risk silent/fallback catches from standard top-level command boundaries (`CW`) and `BLE001` lint-owned handlers (`Fine`).

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

---

### P3: Cross-Language Expansion (TypeScript / Rust)

1. **Phase 3a — Real-World Issue Extraction (No Scanner Code)**:
   - Audit 3–5 real repositories in the target language using `SKILL.md`'s Four Gates and Eight Families.
   - Collect 100+ verified over-defensive findings and Counterweights (mirroring the initial 140-issue Python corpus).
   - Author `references/<lang>.md` and explicitly demarcate which single-node patterns are already `Lint-Owned` by `tsc` / `biome` / `oxlint` (TypeScript) or `rustc` / `clippy` (Rust).
2. **Phase 3b — Language Candidate Extractor**:
   - Build `scripts/scan_<lang>` only for 1-hop structural patterns that survive the language's typechecker and standard linter.
   - Evaluate whether `ast-grep` / `tree-sitter` or a native language parser best fits the target language's AST needs.
