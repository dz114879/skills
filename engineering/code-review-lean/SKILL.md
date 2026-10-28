---
name: code-review-lean
description: "Three-axis code review (Standards, Spec, and Lean anti-over-defense) of changes from a baseline. Use when reviewing a branch/PR, commit range, or working-tree diff."
---

Three-axis, read-only review of changes since a baseline:

- **Standards**: does the code conform to this repo's documented coding standards and design baseline?
- **Spec**: does the code faithfully implement the originating issue or spec?
- **Lean**: does new complexity, defensive code, indirection, or test scaffolding earn its place against real trust boundaries and callers?

Run the applicable axes as **parallel sub-agents** so they don't pollute each other's context, then aggregate their findings.

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

Load the repository's documented rules that apply to the changed paths (such as `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `CODING_STANDARDS.md`, or area-specific guides the diff crosses).

Alongside documented rules, the Standards axis carries the **smell baseline** below—Fowler code smells (_Refactoring_, ch.3) that apply even when a repo documents nothing:

- **The repo overrides.** A documented repo standard always wins; where it endorses something the baseline would flag, suppress the smell.
- **Always a judgement call.** Each smell is a labelled heuristic ("possible Feature Envy"), never a hard violation. Skip issues already established by tooling, without assuming unrun checks passed.

Each smell reads *what it is* → *how to fix*; match it against the diff:

- **Mysterious Name**: a function, variable, or type whose name doesn't reveal what it does or holds. → rename it; if no honest name comes, the design's murky.
- **Duplicated Code**: the same logic shape appears in more than one hunk or file in the change. → extract the shared shape, call it from both.
- **Feature Envy**: a method that reaches into another object's data more than its own. → move the method onto the data it envies.
- **Data Clumps**: the same few fields or params keep travelling together (a type wanting to be born). → bundle them into one type, pass that.
- **Primitive Obsession**: a primitive or string standing in for a domain concept that deserves its own type. → give the concept its own small type.
- **Repeated Switches**: the same `switch`/`if`-cascade on the same type recurs across the change. → replace with polymorphism, or one map both sites share.
- **Shotgun Surgery**: one logical change forces scattered edits across many files in the diff. → gather what changes together into one module.
- **Divergent Change**: one file or module is edited for several unrelated reasons. → split so each module changes for one reason.
- **Speculative Generality**: abstraction, parameters, or hooks added for needs the spec doesn't have. → delete it; inline back until a real need shows.
- **Message Chains**: long `a.b().c().d()` navigation the caller shouldn't depend on. → hide the walk behind one method on the first object.
- **Middle Man**: a class or function that mostly just delegates onward. → cut it, call the real target direct.
- **Refused Bequest**: a subclass or implementer that ignores or overrides most of what it inherits. → drop the inheritance, use composition.

### 4. Prepare the Lean baseline

The **Lean** axis checks whether new guards, fallbacks, indirection, state, and tests earn their complexity:

1. **Boundary**: Parse untrusted input once at its owning external boundary (user input, network, disk, wire, raw storage) into a representation that preserves invariants (*Parse, don't validate*). Propagate expected failures; fail fast with context on internal invariant breaches.
2. **Ownership**: One owner per rule and state. Converge shared semantics at their source instead of adding downstream observers (`compensation`) or mirror state (`second-truth`). Reuse existing repo helpers and stdlib before introducing new ones.
3. **Consumer & effect**: Every guard, branch, fallback, parameter, wrapper, and test should serve a reachable caller or contract and change the outcome when triggered.
4. **Counterweight**: Preserve validation at external trust boundaries, recovery that prevents data loss, security/authorization checks, resource/concurrency guarantees, and compatibility paths backed by a live consumer or public contract.

**Grading**: Distinguish **`[Hard]`** defects (masked errors, false success, broken contracts, or correctness/safety failures) from **`[Simplify]`** judgement calls (redundant checks, avoidable indirection, or dead scaffolding).

Each pattern reads *what it is* → *how to fix*; match it against the diff:

- **Revalidation**: re-checking guarantees already established by types, construction, or an upstream boundary. → trust the established invariant and remove the duplicate check.
- **Error Washing**: broad catches, lossy fallbacks, or premature defaults that turn bugs or invalid input into valid-looking state. → fail fast on internal errors and handle only expected failures at external boundaries.
- **Dead Plumbing**: unused parameters, constant mode flags, unreachable fallbacks, or write-only state kept alive via linter suppression. → remove the dead parameter or branch across its callers.
- **Split Paths & Compensation**: boolean mode flags, parallel pipelines, or downstream sync/observer glue compensating for upstream writer disagreement. → converge shared semantics and state at the source.
- **Hidden Flow**: smuggling services, dependencies, or transient state through data maps, callable attributes, or pure calculation layers. → pass explicit typed inputs and keep effects at their boundary.
- **Half-Moves & Zombie Scaffolding**: legacy entry points, single-line shims, or helper chains left behind after a migration or kept alive solely by tests. → complete the caller cutover and delete the old path.
- **Implementation-Coupled Tests**: tests that manufacture impossible internal states, pin source text, duplicate assertions across layers, or exist only to cover dead/defensive branches. → test observable behavior through public seams and delete tautological tests.
- **Redundant Churn**: unnecessary copies, round-trip conversions, repeated in-scope queries or scans, or custom helpers duplicating stdlib/repo utilities. → reuse established values and existing helpers within their validity scope.

### 5. Spawn the reviewers in parallel

Spawn the applicable sub-agents in parallel with the shared scope packet (diff command, commit or untracked-file list, and any excluded pre-existing edits). Point each sub-agent to the relevant section of this `SKILL.md` file (or inline the section only if the sub-agent cannot read files).

**Standards sub-agent prompt**:

- Scope packet, applicable standards-source paths, and a pointer to Step 3 of this skill.
- Brief: "Read-only review: do not modify files or spawn sub-agents. Review the diff against the supplied standards files and Step 3's smell baseline. Report (a) documented-standard violations, citing the file and rule; and (b) baseline smells with `file:line`. Keep hard violations separate from judgement-call smells; documented repo standards override baseline smells."

**Spec sub-agent prompt** (skip if no spec is available):

- Scope packet and the spec/ticket path or contents.
- Brief: "Read-only review: do not modify files or spawn sub-agents. Compare the diff against the spec and report: (a) missing or partial requirements; (b) unrequested behavior or scope creep; (c) requirements implemented incorrectly. Cite the spec requirement and `file:line` for each finding."

**Lean sub-agent prompt**:

- Scope packet, any identified spec/standards sources (paths or contents, so Lean respects documented contracts), and a pointer to Step 4 of this skill.
- Brief: "Read-only review: do not modify files or spawn sub-agents. Review the diff against Step 4's Lean baseline, focusing on issues introduced, worsened, or left orphaned by this change. When a hunk looks suspicious, inspect its immediate caller or producer contract to confirm before reporting; do not run exhaustive repo-wide call-graph sweeps. For each confirmed finding, report `file:line`, `[Hard]` or `[Simplify]`, the pattern name, the evidence, and the suggested cleanup."

### 6. Aggregate

Present the results under `## Standards`, `## Spec`, and `## Lean` headings (marking any unassessed axis explicitly).

Preserve each axis's perspective, deduplicating findings where multiple axes flag the same underlying issue and calling out any direct conflict where two axes recommend opposite actions on the same code. End with a brief summary distinguishing substantive issues (`[Hard]` / spec gaps / standard breaches) from optional simplifications.
