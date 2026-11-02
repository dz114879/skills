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
---

## Priority Overview

Progression principle: **Stabilize modular foundation at zero behavioral delta (P0) → Reduce high-volume reviewer burden & verify v0.3 bundling (P1) → Expand benchmark corpus & evaluate low-frequency / deferred probes (P2) → Extend to new languages from real-world issue corpora (P3).**

| Priority | Phase | Core Scope | Acceptance / Exit Gate |
| --- | --- | --- | --- |
| **P0** | Modular Python Architecture, Declarative Rule Metadata & Test Split | Split `scan_python.py` (3,050 lines) into an internal Python package behind the unchanged `scripts/scan_python.py` CLI entrypoint; extract rule metadata, `Verify`/`Evidence` templates, and probe vocabularies into JSON; split `test_scan_python.py` (4,349 lines) by subsystem and eliminate redundant AST walks. | `audit_helper.py compare v0.3 <p0-tag>` and `v0.3-diff-head5 <p0-diff-tag>` produce **`-0 / +0 / ~0` (100% multiset equivalence) across all 5 frozen external targets**, and the 6th self-scan target (`anti-defensive-coding`, flagged `[INPUT_CHANGED]` when splitting `scan_python.py`) retains only its `diff_scope` lookup candidate in `paths` mode and `0` in `diff` mode; full 6-target `paths` scan drops from ~24.5s to <5s. |
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

#### 2. Package & Test Layout
Keep `scripts/scan_python.py` as the stable CLI entrypoint (`SKILL.md` and `~/adc_scanning_test/scripts/audit_helper.py` require zero CLI changes), delegating to an internal package:
- `scripts/scan_python.py` — CLI argument parsing, entrypoint, and top-level error handling.
- `scripts/adc_scanner/` (or `scripts/scanner/`):
  - `scope.py` — Git plumbing (`diff_scope`, `paths_scope`, `snapshot_sources`).
  - `ast_helpers.py` — Single-pass scope/parent indexing, control-flow `exits`, receiver/binding resolution.
  - `rules.json` + `registry.py` — Declarative rule metadata, `Verify`/`Evidence` templates, and pattern vocabularies.
  - `probes_prod.py` — Production AST probes (`PY01`–`PY09`).
  - `probes_test.py` — Test AST probes (`PY10b` patch windows, getter mutation, raw source assertions).
  - `reachability.py` — Diff moved-defense pairing (`PY10a`) and whole-repo cross-reachability (`PY10c`).
  - `bundling.py` — Lossless post-extraction record bundling (`PY04`, `PY10c`, and future `PY01`) and output rendering.
- Split `tests/test_scan_python.py` into focused test modules (`test_scope.py`, `test_py01_py04.py`, `test_py02.py`, `test_py05_py09.py`, `test_py10_diff_reach.py`) with shared read-only Git fixtures where history mutation is not under test.

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
