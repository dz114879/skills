# ADC Candidate Extractor Architecture, Code Map & Data Contracts

Target skill: `engineering/anti-defensive-coding/`
Extractor entrypoint: `engineering/anti-defensive-coding/scripts/scan_python.py` (Python 3.11+ standard library only)
Internal package: `engineering/anti-defensive-coding/scripts/adc_scanner/` (`rules.json`, `registry.py`, `scope.py`, `ast_helpers.py`, `probes_prod.py`, `probes_test.py`, `reachability.py`, `bundling.py`)

---

## 1. Pipeline Architecture

`scan_python.py` delegates to the acyclic `scripts/adc_scanner/` package across a stateless, 4-stage AST pipeline (`main` → `scan` → `render`):

1. **Stage 1 — Scope & Inventory Resolution (`adc_scanner/scope.py`: `diff_scope` / `paths_scope`)**:
   - Resolves the repository or common path root and produces a sorted list of `FileChange` records.
   - **Diff mode (`--base` [`--tip`] [`--merge-base`])**: Runs `git diff --name-status -z` and per-file `git diff --unified=0 --inter-hunk-context=0 --ignore-space-at-eol` to populate `added_lines` and `deleted_lines`. When `--tip` is omitted (worktree mode), appends untracked non-ignored `.py` files (`added_lines = None`, meaning every line counts as added).
   - **Paths mode (`--paths`)**: Walks explicit files/directories (pruning `SKIP_SEGMENTS`, `--exclude` globs, and symlinks) and emits `FileChange(path, added_lines=None)`.
2. **Stage 2 — Single-Pass Indexing & Per-File AST Extraction (`adc_scanner/{ast_helpers,probes_prod,probes_test}.py`)**:
   - Classifies each file via `role(path, test_globs)` into `"prod"` or `"test"`.
   - In diff mode, parses the `base` blob (`git show <base>:<old_path>`) for modified/renamed/deleted files to collect deleted `CallSite`s and pre-change `CandidateProbes` (used by `PY10a`).
   - Parses the active file (`git show <tip>:<path>` or disk bytes) via `parse_source` (honoring PEP 263 encoding cookies and recording syntax/codec/merge-conflict errors into `parse_errors` without aborting) and builds a cached single-pass `TreeIndex` (`tree_index(tree)`) and lazy per-scope `ScopeIndex` (`scope_index(node)`).
   - Runs `CandidateProbes` on `"prod"` files (`PY01`–`PY09`, plus `_DEFAULT_*` hook comparisons for `PY10b`) or `TestProbes` on `"test"` files (`PY10b`), filtering emitted candidates by `touches(candidate["_scope"], change.added_lines)`.
3. **Stage 3 — Cross-Version & Cross-File Correlation (`adc_scanner/reachability.py`: `moved_defenses` / `cross_reachability`)**:
   - **`moved_defenses` (`PY10a`)**: Compares `(path, symbol, signature)` defense presence between `before` and `after` production ASTs. When a surviving function loses a signature (`PY01`, `PY02`, or `PY04`) and added lines introduce the same signature in at most as many destinations as lost origins, promotes the destination candidate in place to `PY10a_moved_defense` (`change_tag = "moved-defense"`).
   - **`cross_reachability` (`PY10c`)**: Streams the repository Python snapshot (`snapshot_sources`, using `git ls-tree` + `git cat-file --batch` when `tip` is pinned, or `git ls-files` / `os.walk` on worktree), pre-filters raw source with `TargetWords` (`__all__` plus target function names), and projects `TreeIndex` through `CrossReferences` + `module_scope` + `public_contracts` to emit `constant_defaults`, `cross-test`, and `orphaned` candidates.
4. **Stage 4 — Post-Processing, Lossless Bundling & Rendering (`adc_scanner/bundling.py`: `bundle_coalesces` / `scan` / `render`)**:
   - `bundle_coalesces` groups non-module `PY04_lossy_falsy_coalesce` records by `(path, symbol, change_tag)` after `moved_defenses` promotion has completed.
   - Sorts all candidates by `(path, span[0], rule)`, strips internal transient keys (`_scope`, `_coalesce`), truncates `code` to 200 characters, assigns sequential IDs (`C01`..`Cnn`), and renders `"compact"` text or `"json"`.

---

## 2. Architectural Invariants & Boundaries

1. **Zero Target Mutation (Read-Only Guarantee)**:
   - Every `git` invocation passes `GIT_OPTIONAL_LOCKS=0` and `LC_ALL=C`, and both `scripts/scan_python.py` and `tests/helpers.py` set `sys.dont_write_bytecode = True` before importing scanner modules. The extractor writes exclusively to `stdout`/`stderr` and never creates caches, temporary files, or `.pyc` artifacts in scanned repositories or `/home/roocode/skills`.
2. **Symlink & Root Containment**:
   - Explicit `--paths` outside `--repo` raise `ScanError` (exit `2`). Symlinked files and directories are skipped during tree walks and worktree snapshot enumeration (`mode != b"120000"` in `git ls-tree`, `is_symlink()` on disk).
3. **1-Hop Sweet-Spot Boundary (`ADR-0001`)**:
   - Extract only patterns that require a 1-hop producer/caller/schema verification. Single-node deterministic issues already reported and fixed by `ruff` or `pyright` strict are `Lint-Owned` and excluded from extraction; multi-hop architectural drift belongs to `drift-analyzer`.
4. **Late Code Truncation Invariant**:
   - `CandidateRecords.add` stores full whitespace-normalized `code` without length truncation. `candidate["code"][:200]` runs **only** in the final `scan()` loop after `moved_defenses` and `bundle_coalesces` so bundled `PY04` `evidence` retains complete member expressions.
5. **Diff Scope Eligibility (`_scope` vs `span`)**:
   - A candidate's display `span` marks the primary AST node(s), while its internal `_scope` lists all line ranges whose modification makes the candidate diff-eligible (e.g., `PY02` sets `span` to the `except` handler but adds the guarded `try` body range to `_scope` so widening a `try` body surfaces the unchanged handler).

---

## 3. Code Map (By Module & Symbol)

Never hardcode line numbers when referencing the scanner; locate subsystems by module and symbol name. Modules follow a strict bottom-up acyclic dependency DAG (`registry` → `scope` / `ast_helpers` → `probes_prod` / `probes_test` → `reachability` → `bundling` → `scan_python.py`):

| Module | Subsystem | Key Classes, Functions & Constants | Responsibility |
| --- | --- | --- | --- |
| `scripts/scan_python.py` | **CLI Entrypoint** | `main`, `SCRIPT_DIR` | Sets `sys.dont_write_bytecode = True`, injects `SCRIPT_DIR` into `sys.path`, validates CLI flags, dispatches `diff_scope`/`paths_scope` → `scan` → `render`. |
| `scripts/adc_scanner/rules.json` + `registry.py` | **Declarative Rule Registry** | `RULES`, `VERIFY_TEMPLATES`, `EVIDENCE_TEMPLATES`, `SKIP_SEGMENTS`, `TEST_GLOBS`, `DIFF_FLAGS`, `PY07_DEPENDENCY_SUFFIXES`, `PY08_ROUNDTRIP_PAIRS`, `PY08_COPY_CALLS`, `PY09_LOCK_TOKENS`, `PY09_TX_TOKENS`, `PY09_IO_CALLS`, `PY10A_BASE_SECTIONS`, `PY10B_PATCH_METHODS`, `PY10C_FRAMEWORK_DECORATORS`, `verify_text`, `evidence_text` | Loads `rules.json` once at import time, exposes immutable vocabulary constants and `Verify`/`Evidence` string formatters. |
| `scripts/adc_scanner/scope.py` | **Git & Path Scope** | `ScanError`, `FileChange`, `git`, `worktree_root`, `resolve_ref`, `diff_scope`, `paths_scope`, `matches`, `excluded`, `role`, `raise_walk_error`, `touches`, `intersects` | Git diff hunk parsing (`--ignore-space-at-eol`), worktree/path inventory, exclusion filtering, `"prod"` vs `"test"` role assignment, span intersection checks. |
| `scripts/adc_scanner/ast_helpers.py` | **Single-Pass AST Indexing & Helpers** | `TreeIndex`, `tree_index`, `ScopeIndex`, `scope_index`, `IndexedVisitor`, `ReferenceCounts`, `CandidateRecords`, `allocation_batch`, `unparse`, `walk`, `local_walk`, `lexical_nodes`, `exits`, `statement_exits`, `assignments`, `bound_targets`, `write_targets`, `binding_names`, `import_bindings`, `name_path`, `terminal_name`, `lookup_signature`, `parse_source`, `has_conflict`, `class_declarations`, `named_class`, `undeclared_attribute`, `absent_self_attribute`, `guarded_self_writes`, `zero_arg_constructor`, `saved_retry`, `bounded_retry`, `stored_primary_cleanup`, `last_noreturn_call`, `saved_exception`, `final_attempt`, `same_class_stashes`, `self_state_writes`, `row_factory_facts`, `lock_context_parts`, `io_or_hash_call`, `iteration_parts`, `predicate_tests` | Single-pass `TreeIndex` and lazy `ScopeIndex` construction, per-node `unparse` caching, control-flow exit analysis, receiver/class attribute indexing (`PY01`), deferred propagation proofs (`PY02`), and state/lock helpers (`PY07`–`PY09`). |
| `scripts/adc_scanner/probes_prod.py` | **Production Visitor (`PY01`–`PY09`, `PY10b` hook)** | `CandidateProbes` (`default_shadows`, `visit_Compare`, `visit_BoolOp`, `transaction_checks`, `state_smuggling`, `dict_dependency`, `dead_parameters`, `visit_ListComp`, `roundtrip`, `visit_With`, `receiver_binding`, `reflection_evidence`, `memo_guard`, `visit_Call`, `visit_Try`) | Single-file AST extraction on production files consuming `tree_index(tree)`, recording both `candidates` and `defenses` (`(rule, signature)` pairs for `PY10a`). |
| `scripts/adc_scanner/probes_test.py` | **Test Visitor (`PY10b`)** | `TestProbes`, `patch_windows`, `PatchWindow`, `patch_symbol`, `is_patch_constructor`, `raw_text_origin`, `GetterReturn` | Lexical patch-window tracking (`@patch`, `with patch`, `.start()`/`.stop()`, `monkeypatch.setattr`), mutated getter return recall, raw `.py`/`.md` text assertions, `_legacy*` attribute access. |
| `scripts/adc_scanner/reachability.py` | **Moved Defense (`PY10a`) & Cross-Reachability (`PY10c`)** | `moved_defenses`, `cross_reachability`, `snapshot_sources`, `TargetWords`, `CrossReferences`, `ImportedAlias`, `effective_arguments`, `constant_defaults`, `module_scope`, `ModuleScope`, `import_origin`, `public_contracts`, `lost_calls`, `lost_call_clause`, `call_sites`, `CallSite`, `framework_hook`, `external_base_clause`, `target_name` | Diff-only signature pairing (`PY10a`), whole-repo reference counting over `TreeIndex`, `__all__` static export graph closure, uniform constant parameter bundling (`D4b`), `cross-test` and `orphaned` detection. |
| `scripts/adc_scanner/bundling.py` | **Bundling & Output** | `bundle_coalesces`, `scan`, `render` | Per-function `PY04` bundling (`K4-B`), candidate sorting, 200-char code truncation, `C01` ID assignment, JSON/compact rendering. |

---

## 4. Data & State Contracts

### 4.1 Internal Scope Contract (`FileChange`)
```python
@dataclass
class FileChange:
    path: str                          # Posix path relative to scan root
    added_lines: set[int] | None = None    # None = all lines eligible (paths mode / untracked file)
    old_path: str | None = None        # Pre-change path for M/R/D in diff mode; None for A/untracked/paths
    deleted_lines: set[int] | None = None  # Deleted line numbers in base blob
    deleted: bool = False              # True when file was deleted in diff
```

### 4.2 Internal Transient Candidate Keys (Pre-Render)
Before final normalization in `scan()`, each candidate dictionary in `CandidateRecords.candidates` carries:
- `_scope: list[tuple[int, int]]` — Line intervals checked against `FileChange.added_lines` via `touches()`. Stripped before output.
- `_coalesce: tuple[int, str]` — Present only on `PY04_lossy_falsy_coalesce` (`(col_offset, unparsed_fallback)`). Used by `bundle_coalesces` to sort member sites by `(span[0], col_offset)` and format `line <L>: <code> -> <fallback>`. Stripped before output.
- `CandidateRecords.defenses: list[tuple[tuple[str, tuple[str, ...]], dict]]` — Maps `((rule, signature_tuple), candidate_dict)` for `PY01`, `PY02`, and `PY04` inside function bodies (`function_depth > 0`) so `moved_defenses` can mutate promoted destination candidates in place before `bundle_coalesces` runs.

### 4.3 External Output Schema (`--format json`)
```json
{
  "summary": {
    "mode": "diff | paths",
    "files_scanned": 15,
    "cross_checked_files": 164,
    "candidate_count": 16,
    "parse_error_count": 0
  },
  "parse_errors": [
    {"path": "relative/path.py", "error": "base: SyntaxError: ..."}
  ],
  "candidates": [
    {
      "id": "C01",
      "path": "relative/path.py",
      "span": [57, 122],
      "symbol": "ClassName.method_name | function_name | <module>",
      "rule": "PY04_lossy_falsy_coalesce",
      "change_tag": "changed | path-audit | moved-defense | cross-test | orphaned",
      "code": "whitespace-normalized source snippet (<= 200 chars)",
      "evidence": "local structural facts and 1-hop clues",
      "verify": "1-hop contract question + Counterweight prompt + catalogue section pointer"
    }
  ]
}
```

### 4.4 `change_tag` Domain Values
- `"path-audit"`: Emitted in `--paths` mode for all rules (including `PY10c` `constant_defaults`), except `PY10c`'s `cross-test` reachability arm.
- `"changed"`: Emitted in `--base` diff mode for `PY01`–`PY09`, `PY10b`, and `PY10c` `constant_defaults`.
- `"moved-defense"`: Emitted in `--base` diff mode when `moved_defenses` promotes a `PY01`/`PY02`/`PY04` destination candidate to `PY10a_moved_defense`.
- `"cross-test"`: Emitted by `PY10c` (both modes) when terminal-name aggregate reference counts across the repository show `0` production references and `>=1` test references for that function name (not attributed per individual definition when multiple definitions share a name).
- `"orphaned"`: Emitted by `PY10c` (diff mode only) when a net loss of call sites leaves a function's terminal name with `0` production references and `0` test references across the repository.

### 4.5 Exit Code Contract
- **`0`**: Scan completed (including when `candidate_count > 0` or `parse_error_count > 0`).
- **`2`**: Invalid CLI arguments, missing `--repo`/`--paths` target, path outside `--repo`, unreadable directory (`OSError`), or unresolvable Git reference (`ScanError`).
