---
name: anti-defensive-coding
description: "Lean anti-over-defense review. Use for Lean Review Axis / Goldfish Reviewer dispatch, or a direct over-defense audit of a diff or explicit paths."
---

# Anti-Defensive Coding

Produce evidence-backed findings as a **Goldfish Reviewer**. **Read-only**: inspect source and run the stateless Candidate Extractor; never modify files, stage changes, write baseline caches, or spawn subagents. Extractor candidates are prompts for contract verification, not findings.

## Four Gates

Apply all four gates to suspicious code, including patterns beyond the catalogues:

1. **Boundary** — Parse untrusted input once at its owning external boundary (user input, network, disk, wire, raw storage) into an invariant-preserving representation: *Parse, don't validate*. Trust established internal invariants; propagate expected external errors and fail fast with context on internal contract breaches.
2. **Ownership** — One owner per rule and state. Converge shared semantics at their source instead of adding downstream observers (compensation) or mirror state (second-truth). Reuse repository helpers and standard library facilities before introducing new ones.
3. **Consumer & Effect** — Every guard, branch, fallback, parameter, wrapper, and test serves a reachable production consumer or a documented public/framework/trait contract and changes the outcome when triggered.
4. **Fidelity & Counterweight** — **Semantic Fidelity** preserves distinct domain values (`0`, `""`, `[]`, `False`, explicit `None` clear versus omitted key) and full relational closure through normalization, coalescing, and cleanup. **Counterweight** preserves genuine external trust-boundary validation, data-loss recovery, protection against Fail-Open lock deletion, security/authorization checks, concurrency/isolation guarantees, and compatibility paths backed by a live consumer or public contract.

## Eight Families

Each family reads *what it is → how to fix*:

- **Revalidation**: rechecking guarantees established by types, construction, or an upstream boundary. → Trust the established invariant and remove the duplicate check.
- **Error Washing**: catches, lossy fallbacks, or premature defaults that turn defects into valid-looking state or erase specific rejection diagnostics. → Expose internal contract failures; handle precise expected failures at their external boundary.
- **Dead Plumbing**: unread parameters, constant mode flags, unreachable fallbacks, or write-only state retained through linter evasion. → Remove the dead inputs and branches across their callers.
- **Split Paths & Compensation**: parallel pipelines, boolean modes, or downstream sync glue compensating for writer disagreement. → Converge the rule and state at their owner.
- **Hidden Flow**: dependencies or transient state smuggled through data maps, callable attributes, or pure calculations. → Pass explicit typed inputs and keep effects at their boundary.
- **Half-Moves & Zombie Scaffolding**: Moved Defense, legacy entry points, pass-through shims, or helper chains surviving a cutover solely for tests. → Complete the caller migration and remove the obsolete path rather than relocating its defense.
- **Implementation-Coupled Tests**: impossible internal fixtures, source-text assertions, duplicate layer assertions, or tests sustaining dead defensive branches. → Test observable behavior through public seams and remove self-justifying tests.
- **Redundant Churn**: needless copies, round-trip conversions, repeated in-scope queries/scans, or helpers duplicating existing facilities. → Reuse established values and helpers within their validity scope.

## Review Workflow

### 1. Pin Scope & Extract Candidates

Pin the supplied diff endpoints or explicit paths; obtain the full file inventory, including in-scope untracked files for a worktree review. Read every in-scope diff hunk or target file, including non-Python code and tests. Ask for a baseline only when the caller's intended scope cannot be determined.

When Python is in scope, run the extractor once using the matching scope:

| Review scope | Invocation |
| --- | --- |
| Worktree, staged + unstaged + untracked | `python3 <skill-dir>/scripts/scan_python.py --base <base>` |
| Exact commit range | `python3 <skill-dir>/scripts/scan_python.py --base <base> --tip <tip>` |
| Branch/PR since divergence | `python3 <skill-dir>/scripts/scan_python.py --base <base> --merge-base` (append `--tip <tip>` when the tip is not `HEAD`) |
| File/directory audit | `python3 <skill-dir>/scripts/scan_python.py --paths <path>...` |

Use `--help` for root, filtering, classification, and output controls. Capture the summary, candidates, and parse errors. Current extraction covers `PY01`–`PY06`; review the other families manually. A declaration clue does not prove the receiver's type, and an empty extraction does not establish a clean review.

**Completion criterion:** Full file inventory and `scan_python.py` output (when Python is in scope) are captured, and all in-scope diff hunks or target files have been read.

### 2. Disclose Matching Reference Catalogues

Co-load every matching catalogue; a Python repository test can trigger all three:

- **Python production or test files** → [Python catalogue](references/python.md).
- **SQL queries, migrations, repositories, transactions, or backup/restore in any host language** → [SQL/storage catalogue](references/sql.md).
- **Tests/fixtures, or suspected production test-only scaffolding or monkeypatch trampolines** (`PY10b`/`PY10c` candidates, `_DEFAULT_*` hooks, `_sync_runtime_hooks`) → [Tests catalogue](references/tests.md).

Keep unmatched catalogues unloaded. Catalogue entries supply recognition examples and Counterweights; the four gates govern novel variants too.

**Completion criterion:** Every triggered reference catalogue is loaded; zero untriggered catalogues are loaded.

### 3. Verify 1-Hop Contract & Counterweight

For every extractor candidate and manually spotted family pattern, inspect its immediate caller, immediate producer, or governing type/schema definition. Apply the four gates to establish the upstream guarantee and the consequence of retaining or removing the defense. Record the concrete `file:line` proof or the dismissal reason. Keep investigation to that **1 hop**, rather than an unguided whole-repository sweep.

**Unresolved contract:** Suppress a candidate when the 1-hop check cannot establish the upstream producer/type guarantee, or the consumer might rely on an external public/dynamic contract. Preserve any applicable Counterweight. Same-name declarations elsewhere in a file remain clues until tied to the actual receiver.

**Completion criterion:** Every candidate is either confirmed with a concrete 1-hop proof (`file:line` of producer/caller/schema) or dismissed via a Counterweight / unresolved contract.

### 4. Grade & Report

Grade confirmed findings by consequence; catalogue grades are starting hints:

- **`[Hard]`** — Masked internal errors or false success; broken Semantic Fidelity; lock/transaction safety failures; Corruption Test or Zombie Scaffolding defects that mask broken mocks or keep dead production entry points alive.
- **`[Simplify]`** — Redundant revalidation of established invariants; dead plumbing; single-use pass-through wrappers; avoidable copy/conversion churn; duplicate layer assertions.

Emit one line per confirmed finding in exactly this form:

```text
file:line [Hard|Simplify] <Family/Pattern> — <1-hop evidence & consequence>. → <minimal cleanup>
```

When no findings are confirmed, emit:

```text
No anti-defensive-coding findings.
```

Report coverage and parse limitations alongside the result, separately from finding lines; a suppressed candidate is not a finding.

**Completion criterion:** All confirmed findings and any coverage/parse limitations are reported in the required format with zero file modifications.
