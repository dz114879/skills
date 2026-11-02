# ADC Graveyard (Dead Ends) & Deferred Probe Ledger

This document records AST guards, rule arms, and candidate probes that were empirically evaluated against the 6-repository benchmark corpus (`/home/roocode/adc_scanning_test`, audit reports under `/home/roocode/adc_scanning_test/reports/audits/`) during `v0`–`v0.3` and either **rejected**, **retired as Lint-Owned**, or **deferred**.

Check this ledger before proposing any new heuristic suppression or probe in `scan_python.py`.

---

## 1. Rejected AST Guards & Heuristics (Do Not Re-Introduce)

Every proposal below sounded plausible in theory but was rejected after measurement on the benchmark corpus (`/home/roocode/adc_scanning_test/reports/audits/proposal-ledger-v0.md` §8–§9, `cut2-summary-v0-baseline.md`) because it either **destroyed confirmed `[Hard]` or `[Simplify]` True Positives**, **flooded the output with false-positive noise / coincidence matches** (`K4-F2`, `M1`), or **duplicated a `Lint-Owned` check** (`D7` / `D8`).

| Proposal ID | Rule | Proposed Suppression / Heuristic | Why It Failed (Concrete Benchmark Counterexample) |
| --- | --- | --- | --- |
| **`K4-W` / `K4-P` / `K4-J`** | `PY04_lossy_falsy_coalesce` | Suppress `str(x.get(k) or "")`, `int(x.get(k) or 0)`, or `x.get(k, "") or ""` wrapper idioms on the assumption that absent and empty/zero are equivalent. | **Loses 9 sampled TPs** (e.g., `K4-J` proved `None` vs `""`/`0` carries distinct domain meaning in `Clonoth` and `Zoaholic` config/payload normalizers, and `int(row[...] or 0)` on `NOT NULL` integer columns is dead fallback plumbing). Handled via `K4-B` per-function bundling instead of suppression. |
| **`K4-F2`** | `PY04_lossy_falsy_coalesce` | Emit a new candidate for every `.get(k, default)` call with a non-`None` literal default. | **+230 identity no-ops on typed/boundary dicts**. Pure `.get(k, d)` without truthiness coalescing (`or`) is overwhelmingly legitimate boundary defaulting; kept as a manual check in `python.md §6` (`K4-F1`) rather than an AST probe. |
| **`K2-X4` / `T8`** | `PY02_error_washing` | Skip `except Exception:` handlers when every call inside the `try` body targets an external/stdlib function rather than an internal repo helper. | **Loses `S31 [Hard]` (`Zoaholic/core/oauth/state.py:31-34`)**: `try: shutil.copy2(src, bak) except Exception: pass` wraps only stdlib `shutil.copy2`, then unconditionally logs `"backed up state to ..."` on the next line, reporting false backup success when disk copy fails. |
| **`K2-X1` / `K2-X1d` / `K2-X2` / `K2-X3`** | `PY02_error_washing` | Skip broad exception handlers that call `logger.error` / `logger.warning` / `logger.exception` before falling through or returning a default. | **Loses multiple sampled `[Hard]` TPs** (and leaves 51 unadjudicated removals in `K2-X1d`). Logging an exception does not prevent error washing when the handler subsequently returns a valid-looking empty collection (`[]`/`{}`) or continues mutating corrupt state. |
| **Remove Multi-Exception Arm (`K2-M1`)** | `PY02_error_washing` | Remove the `len(names & internal) >= 2` arm (`except (KeyError, ValueError, TypeError):`) and only flag `Exception`/`BaseException`/bare `except:`. | **Loses 4 sampled `[Hard]` TPs (`S19, S20, S49, S50`)** where AI code listed `(KeyError, TypeError, ValueError)` specifically to evade `BLE001` while still washing internal bugs into silent fallbacks. |
| **`K1-A1` / `K1-M1` / `K1-B1`** | `PY01_reflect_default` | Skip `getattr`/`hasattr` when the attribute name starts with `_`, when the receiver is a method call, or when default is `None`. | **Loses `dc_dojo C349/C350`, `fox C01`, and `S46/S53` TPs**, where `getattr(self, "_private_state", None)` guarded attributes unconditionally initialized in `__init__`. |
| **`F1`** | `PY10c_cross_test_reachability` | Skip module-level `setup` / `teardown` functions in any file that imports `discord`. | **Removes 4 dead deprecated-cog `setup` rows in `fox-dayi-bot/cogs/deprecated/{gc,reload,reminder,debug_role}.py` (3 sampled and confirmed as Dead Plumbing TPs, 1 unsampled)**: those files import `discord`, but `bot.py:113` sets `SKIPPED_COG_DIRECTORIES = {"deprecated"}` and never loads them. Handled by `K10-G2` (1-hop check of the cog loader) instead. |
| **`Clonoth D1` (`Zoaholic D1`)** | `PY10a_moved_defense` | Require origin and destination of a moved defense to be in the same file, or require the surviving origin function to call the destination function directly. | **Loses genuine cross-file move TP `Zoaholic C72`** (`core/streaming.py` → `core/stream_errors.py`). `U1` (`len(destinations) <= len(lost_origins)`) achieved the same noise elimination with zero TP loss. |
| **`M1`** | `PY10a_moved_defense` | Allow moved-defense matching when `len(destinations) > len(lost_origins)` or across non-surviving functions. | Floods diff output with coincidence matches (e.g., `getattr(x, "id", None)` added in 5 unrelated handlers) and bloats `evidence` with 6+ unrelated base origins. |
| **`D7` / `D8` (`Clonoth D7`)** | `PY06_revalidated_typed_param` | Narrow `PY06` instead of retiring it, or add AST probe for `int(x or 0)` on `x: int`. | `pyright` strict (`reportUnnecessaryIsInstance`, `reportUnnecessaryComparison`) already owns typed-parameter revalidation; typed-field revalidation is covered in `python.md §1` and same-type scalar reparsing in `python.md §10` (`G6`). |

---

## 2. Retired Probe Arms (Lint-Owned by Standard Tooling)

Per `ADR-0001` and `SKILL.md` Gate 3 (`Lint-Owned`), the extractor never duplicates single-node rules owned by `ruff` or `pyright`:

1. **`PY06_revalidated_typed_param` (Retired in `v0.2` via `X06`)**:
   - **What it did**: Flagged `isinstance(param, ...)` inside a function where `param` had a non-`Any` type annotation.
   - **Why retired**: `pyright` strict reports `reportUnnecessaryIsInstance` with full cross-file type resolution, whereas single-file AST inspection produced 49 rows of noise/overlap.
2. **Standalone Foreign Private Attribute Write in `PY07_state_smuggling` (Retired in `v0.2` via `P7R`)**:
   - **What it did**: Flagged `other._private = val` when `other` was not `self`/`cls`.
   - **Why retired**: Audited all 25 hits across `Clonoth` (20) and `Zoaholic` (5); **25/25 (100%)** were exact duplicates of `ruff` rule `SLF001` (`private-member-access`). Only the multi-site `S7` (`self._x = v; return v` + sibling read), `try`/`finally` transient reset, and `dict_dependency` arms remain in `PY07`.

---

## 3. Deferred Probe Ideas (Revisit Only in P2 Corpus Expansion)

These 11 probe ideas from `proposal-ledger-v0.md` §7 had `<=2` confirmed hits on the initial 6-repository corpus or high Counterweight overlap. Do not implement them until P2 adds new benchmark repositories and measures their paths-mode precision:

| ID | Rule / Family | Candidate Probe Idea | Current Corpus Evidence & Blocker |
| --- | --- | --- | --- |
| **`H1`** | `PY10a` | Flag "copied defense" at call sites of a genuine `PY10a` destination function. | 2 hits (`Zoaholic FN-5`); needs multi-window diff corpus to verify signal vs noise. |
| **`Clonoth D5`** | `PY10c` | Flag production stub functions whose body only returns a constant (`return None` / `return True` / `return {}`) while having live production callers. | 1 confirmed hit in `Clonoth`; risk of matching legitimate base-class default hooks without inheritance filtering. |
| **`Clonoth D6`** | Diff / `PY07` | In diff mode, flag a dict string-key reader whose last matching writer in the repo was deleted by the diff. | 1 hit in `Clonoth`; high risk of false positives on external JSON/API keys. |
| **`fox D4`** | `PY01` | Cross-file mixin `__init__` and `object.__new__` test-fixture attribute indexing for `PY01` `evidence`. | 1 hit (`fox-dayi-bot` mixin); adds cross-file class graph complexity for 1 evidence string. |
| **`fox D5`** | Diff / `PY02` | In diff mode, surface a pre-existing `PY02` handler inside an unchanged function when the diff adds a new call site to that function. | 1 hit; blurs diff-scope boundary (`SKILL.md:49`). |
| **`fox D6`** | `PY10c` | Flag production functions whose return value is discarded (`Expr(Call)`) at every resolved production call site. | 1 hit; needs filtering for side-effect methods that return optional status. |
| **`dc_dojo C1`** | `PY10c` | Extend `PY10c` reachability from functions/methods to classes with zero production references. | Diff mode `+2` valid hits, but `paths` mode emits `+41` rows (crosses into `drift-analyzer` territory). |
| **`dc_dojo FN-2`** | `PY02` | Flag `await asyncio.gather(..., return_exceptions=True)` whose returned list is discarded without inspecting element exceptions. | 7 sites across corpus, but several are shutdown/cancellation drains (`Counterweight`); covered in catalogue (`python.md §4`) for now. |
| **`A08-FRESH` / `A08-SERIAL`** | `PY08` | Flag `copy.deepcopy(x)` on a freshly constructed return value or fed directly into `json.dumps(...)`. | 1 hit each; covered in `python.md §10` catalogue. |
| **`A05-ID`** | `PY05` / `PY10c` | Flag private helper functions whose entire body is `return <param>` (identity wrapper). | 1 hit; trivial frequency on current corpus. |
| **`httpx FN-2` / `FN-5`** | `PY08` / `PY01` | Flag single-use `Cls(self.<prop>)` re-wrapping or `.lower()` on an already-canonicalized slot. | 1 hit each in `httpx`; requires multi-hop property/constructor invariant tracking, kept in `python.md §1, §10`. |
