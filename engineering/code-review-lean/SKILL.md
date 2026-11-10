---
name: code-review-lean
description: "Three-axis code review (Standards, Spec, and Lean anti-over-defense) of changes from a baseline. Use when reviewing a branch/PR, commit range, or working-tree diff."
---

Three-axis, read-only review of changes since a baseline:

- **Standards**: does the code conform to this repo's documented coding standards and design smell baseline (`STANDARDS.md`)?
- **Spec**: does the code faithfully implement the originating issue or spec?
- **Lean**: does new complexity, defensive code, indirection, or test scaffolding earn its place against real trust boundaries and callers (`anti-defensive-coding`)?

Run the applicable **Review Axes** in parallel as fresh-context reviewers so they don't pollute each other's context, then aggregate their findings.

## Process

### 1. Pin the scope

Use the baseline supplied by the user or caller (a commit SHA, branch, tag, or recorded starting `HEAD`). Ask only when the baseline is unknown or ambiguous.

Match the diff command to the requested endpoint:

- **Branch/PR since divergence**: `git diff <base>...<tip>` and `git log <base>..<tip> --oneline` (`<tip>` defaults to `HEAD`).
- **Exact commit range**: `git diff <base>..<tip>` and `git log <base>..<tip> --oneline` (`<tip>` defaults to `HEAD`).
- **Worktree / uncommitted changes**: `git diff <base>` plus in-scope untracked files from `git status --short`, excluding unrelated pre-existing edits when known.

Confirm the baseline resolves (`git rev-parse --verify <base>^{commit}`) and the selected scope contains changes before spawning sub-agents.

### 2. Identify the spec source

Find the originating requirements, in order:

1. A ticket, spec path, or explicit requirements passed by the user or caller.
2. Issue references in commit messages (`#123`, `Closes #45`, etc.), resolved via the repo's tracker doc (e.g. `docs/agents/issue-tracker.md`) when present.
3. A matching spec file under `docs/`, `specs/`, or `.scratch/`.
4. If none is found and the user's request does not state the requirements, ask the user or skip the **Spec** sub-agent and report **Spec not assessed — no spec available**.

### 3. Identify the standards sources

Collect the paths of the repository's documented rules that apply to the changed paths (such as root `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `CODING_STANDARDS.md`, and any area-specific guides or ADRs the diff crosses).

`STANDARDS.md` in this skill's directory holds the Fowler smell baseline and output format for the **Standards** reviewer; `../anti-defensive-coding/SKILL.md` (the `anti-defensive-coding` skill) holds the workflow, gates, candidate extractor, and output format for the **Lean** reviewer. They are written for the sub-agents; do not preload them in the orchestrator.

Done when the baseline commit resolves, the diff scope is non-empty, the spec source is resolved (or explicitly marked unavailable), and the applicable standards file paths are collected into a shared **scope packet** (repo root, diff command, commit or untracked-file list, and any excluded pre-existing edits).

### 4. Spawn the reviewers in parallel

Spawn one fresh-context, read-only sub-agent per applicable axis in parallel. Pass the shared scope packet and axis-specific pointers below (`<skill-dir>` is the absolute path of this skill's directory):

**Standards sub-agent brief**:

```text
You are the Standards reviewer. Read-only: do not modify files or spawn sub-agents.
Read <skill-dir>/STANDARDS.md and follow its workflow and exact single-line output format.
Scope packet: <repo root, diff command, commit/untracked list, excluded edits>
Standards files: <applicable standards file paths, or "none found">
Verified passed checks: <linter/typechecker checks already confirmed passed, if any>
```

**Spec sub-agent brief** (skip if no spec is available):

```text
You are the Spec reviewer. Read-only: do not modify files or spawn sub-agents.
Scope packet: <repo root, diff command, commit/untracked list, excluded edits>
Spec source: <spec/ticket path or inline requirements>
Compare the diff against the spec and report:
(a) [Missing] missing or partial requirements;
(b) [Scope Creep] unrequested behavior;
(c) [Wrong] requirements implemented incorrectly;
(d) [Undecided] edge cases the diff settles that the spec leaves open (missing/empty fields, fallbacks, ties, boundaries), including ones a test pins down.
Emit one line per confirmed finding (no preamble or summary):
file:line [Missing|Scope Creep|Wrong] <spec requirement> — <how the diff diverges>.
file:line [Undecided] <open question> — <behavior the diff/test currently picks> (needs a human decision).
When no findings are confirmed, emit: No Spec findings.
```

**Lean sub-agent brief**:

```text
You are the Lean reviewer. Read-only: do not modify files or spawn sub-agents.
Read <skill-dir>/../anti-defensive-coding/SKILL.md and follow its 4-step workflow and exact single-line output format.
Scope packet: <repo root, diff command / base & tip endpoints, commit/untracked list, excluded edits>
Contract context: <spec source and applicable standards file paths, so documented contracts and Counterweights are respected>
```

Done when every spawned reviewer has returned its report. A reviewer that fails or returns no report is marked **not assessed**, never clean.

### 5. Aggregate

Present the results under `## Standards`, `## Spec`, and `## Lean` headings (marking any unassessed axis explicitly).

Preserve each axis's perspective while deduplicating findings where multiple axes flag the same underlying code:

- **Hard contracts override heuristics**: Explicit spec requirements, documented repo standards (`[Violation]`), and `[Hard]` Lean defects take precedence over judgement-call heuristics (`[Smell]` and `[Simplify]`). Where a heuristic suggestion would violate an explicit spec requirement, documented repo standard, or `[Hard]` Counterweight, suppress the heuristic rather than listing a conflict.
- **Surface genuine conflicts**: Where two explicit contracts conflict, or two heuristics pull the same code in opposite directions (such as a `[Smell]` recommending extraction vs. a `[Simplify]` flagging a single-use wrapper), call out the conflict explicitly and carry it into the decision list below.

End with a brief summary grouped into:

1. **Substantive issues**: `[Hard]` Lean defects, `[Missing]` / `[Wrong]` / `[Scope Creep]` spec gaps, and `[Violation]` documented-standard breaches.
2. **Needs a human decision**: `[Undecided]` spec edge cases (stating the behavior the diff currently picks) and unresolved cross-axis conflicts.
3. **Optional simplifications**: `[Smell]` and `[Simplify]` judgement calls.
