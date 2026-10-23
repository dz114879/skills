---
name: code-review-lean
description: "Three-axis code review (Standards, Spec, and Lean anti-over-defense) of changes since a fixed point. Use when reviewing a branch, PR, commit range, or work-in-progress diff for spec compliance, coding standards, and AI defensive slop."
---

Three-axis review of the diff between `HEAD` and a fixed point the user supplies:

- **Standards**: does the code conform to this repo's documented coding standards and design baseline?
- **Spec**: does the code faithfully implement the originating issue / spec?
- **Lean**: does every guard, fallback, parameter, abstraction, and test earn its place against real trust boundaries and production callers?

All three axes run as **parallel sub-agents** so they don't pollute each other's context, then this skill aggregates their findings.

## Process

### 1. Pin the fixed point

Whatever the user said is the fixed point (a commit SHA, branch name, tag, `main`, `HEAD~5`, etc.). If they didn't specify one, ask for it.

Capture the diff command once: `git diff <fixed-point>...HEAD` (three-dot, against merge-base), or `git diff <fixed-point>` (plus any untracked files in `git status --short`) when the user explicitly asks to review uncommitted work-in-progress changes. Also note the commit list via `git log <fixed-point>..HEAD`.

Before going further, confirm the fixed point resolves (`git rev-parse --verify <fixed-point>^{commit}`) and the diff is non-empty. A bad ref or empty diff should fail here, not inside three parallel sub-agents.

### 2. Identify the spec source

Look for the originating spec, in this order:

1. A path or ticket/spec reference the user passed as an argument.
2. Issue references in the commit messages (`#123`, `Closes #45`, GitLab `!67`, etc.), fetched via the workflow in the repo's tracker doc (e.g. `docs/agents/issue-tracker.md`; if needed and missing, tell the user to run `/setup-matt-pocock-skills` or pass the spec path).
3. A spec file under `docs/`, `specs/`, or `.scratch/` matching the branch name or feature.
4. If nothing is found, ask the user where the spec is. If they say there isn't one, the **Spec** sub-agent will skip and report "no spec available".

### 3. Identify the standards sources

Search the repo for every file that documents how code should be written. When `CODING_STANDARDS.md` or `CONTRIBUTING.md` exists, it must be on the list.

On top of whatever the repo documents, the Standards axis always carries the **smell baseline** below: a fixed set of Fowler code smells (_Refactoring_, ch.3) that applies even when a repo documents nothing. Two rules bind it:

- **The repo overrides.** A documented repo standard always wins; where it endorses something the baseline would flag, suppress the smell.
- **Always a judgement call.** Each smell is a labelled heuristic ("possible Feature Envy"), never a hard violation. Like any standard here, skip anything tooling already enforces.

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

The **Lean** axis hunts AI over-defensive coding, silent error washing, contract smuggling, redundant churn, and dead-code preservation. Apply these gates to every candidate, including novel variants absent from the clusters below:

1. **Boundary**: Parse untrusted input once at its owning external boundary (user input, network, disk, wire, raw storage) into a representation that preserves checked invariants (*Parse, don't validate*); narrow sum types/discriminants before accessing variant-specific data. Trust only guarantees the producer or type actually establishes. Propagate expected failures; fail fast with context on invariant breaches.
2. **Ownership**: One owner per rule/state, one computation per validity scope. Reuse established results while mutation, time, concurrency, or authority has not invalidated them. Converge equivalent paths at their source instead of adding downstream observers (`compensation`) or mirror state (`second-truth`). Reuse existing repo helpers and stdlib before introducing new helpers.
3. **Consumer & effect**: Judge every guard by what it detects and how its rejection, recovery, or diagnosis changes the outcome. Every branch, default, parameter, wrapper, re-export shim, state field, and test must serve a reachable **production** consumer (or a public export, framework/config registration, or required trait/interface/callback signature). Tests or quality ledgers alone never justify production scaffolding.
4. **Counterweight (never cut)**: Keep validation at genuine external trust boundaries, precise recovery/error handling that prevents data loss, security/authorization checks, accessibility, hardware/resource constraints, ownership/lifetime/synchronization guarantees, and compatibility paths backed by a live consumer or documented public contract.

**Grading**: `[Hard]` requires a demonstrated contract/correctness/safety failure, masked error/false success, failure at supported load, or proven dead production scaffolding without an external obligation. `[Simplify]` requires evidenced redundancy or avoidable design cost without such a failure. Cluster tags below indicate typical defaults; verified evidence decides.

Each cluster reads *what it is* → *how to fix*:

- **Revalidation** `[Simplify]`: re-checking guarantees already established by types, construction, or upstream validation, including defensive field probes on owned types, unpacked-arg self-reconciliation, and runtime self-checks on static literals. → preserve invariants in the representation, initialize required fields at the source, and remove repeated checks.
- **Error washing** `[Hard]`: broad catches, `.ok()`, `.unwrap_or_default()`, or fallback values that turn bugs, failed reads, unknown variants, or duplicates into valid-looking results; defaults applied on line 1 that make line 2's required check unreachable; restore/import paths silently falling back to empty initialization; deleted upstream error suppression migrated into downstream callees. → reject before lossy normalization, propagate or fail fast on internal errors, separate validation/recovery/initialization, and handle only exact expected failures at external boundaries.
- **Dead plumbing & linter evasion** `[Hard]`: unreachable fallbacks, constant boolean flags, ignored pass-through parameters, unread collectors, and write-only state kept alive via `_ = param`, `#[allow(dead_code)]`, immediate entry overwrite (`param = prepared.x`), or passing both a parent container and an extracted slice with an unreachable fallback. → remove the dead parameter or state across the full call chain; retain only positions required by external trait/callback signatures.
- **Split paths & dual-mode flags** `[Hard]/[Simplify]`: boolean mode flags (`prevalidated`, `strict`) that split one operation into two rules or discard sibling arguments; caller-side chunking to bypass a callee's hardcoded limit; downstream observers/sync glue (`compensation`) or mirror flags (`second-truth`) compensating for upstream writer disagreement. → converge shared semantics, batching/limits, and state under one owning mechanism.
- **Hidden flow & contract smuggling** `[Hard]`: smuggling services or state through data maps, callable attributes, or transient scope mutation instead of explicit signatures; pure calculation layers secretly performing I/O; mixing distinct timing responsibilities into one tuple helper and working around it at call sites. → pass explicit typed inputs and outputs; separate distinct phases and keep effects at their owning boundary.
- **Half-moves & zombie scaffolding** `[Hard]`: zero-production-caller functions/methods, test-only monolithic entry points, pure re-export shells, single-line alias wrappers, unoverridden hook stubs, runtime test-monkeypatch hooks, or transitive helper islands kept alive solely by tests or ledgers; extracting shared helpers for dead methods. → trace transitive production reachability, complete the caller cutover, and delete the abandoned chain with its dead helpers.
- **Corruption & zombie tests** `[Hard]/[Simplify]`: tests that manufacture impossible internal states to justify a defensive branch (`[Hard]`), call dead or private legacy paths directly to keep them alive (`[Hard]`), duplicate assertions across layers (`[Simplify]`), or pin raw source/doc prose literals (`[Hard]`). → repair fixtures, test observable behavior and real boundaries through public seams, and delete tests that only justify dead or defensive scaffolding.
- **Copy, conversion & loop churn** `[Simplify]`: copying/cloning fresh or unaliased values unnecessarily, taking serialize-deserialize or collection round-trips across layers, allocating temporary wrapper containers/closures in hot loops to feed an obsolete signature, recomputing loop invariants, or eagerly building heavy structures before a fast-return branch. → pass typed values directly, borrow/move/copy according to actual ownership needs, hoist invariants, and short-circuit before expensive work.
- **Repeated work, bleed & reinvention** `[Simplify]/[Hard]`: duplicate in-scope scans, queries, or layered caches/write-backs; unguarded I/O before precondition checks or unconsumed I/O inside critical locks (`[Hard]`); stacked duplicate confirmation/transaction guards across caller and callee (`[Hard]`); custom helpers duplicating existing repo utilities or stdlib. → reuse within the validity scope, keep critical sections tight, assign each guard to one owner, and reuse existing repo/stdlib capabilities.

**Stack disclosure — diff first**: Read only catalogues whose branches match the changed code or its affected callers/tests. Repository language lists and unused dependencies do not trigger a read; if neither branch matches, the inline baseline above is complete.

- **Python** — Changed Python code or Python-facing contracts: read [`references/lean-python.md`](references/lean-python.md).
- **SQL/databases** — Changed queries, database access, transactions, schema/migrations, or backup/restore in any host language: read [`references/lean-sql.md`](references/lean-sql.md).

### 5. Spawn all three sub-agents in parallel

Issue all three sub-agent calls together, in the foreground, and aggregate the reports they return.

**Standards sub-agent prompt** should include:

- The full diff command and commit list.
- The list of standards-source files you found in step 3, **plus the smell baseline from step 3** pasted in full (the sub-agent has no other access to it).
- The brief: "Read-only review: do not modify any files. Report, per file/hunk where relevant, (a) every place the diff violates a documented standard: cite the standard (file + the rule); and (b) any baseline smell you spot: name it and quote the hunk. Distinguish hard violations from judgement calls: documented-standard breaches can be hard, but baseline smells are always judgement calls, and a documented repo standard overrides the baseline. Skip anything tooling enforces. Do not invoke `code-review-lean` or `code-review`, and do not spawn sub-agents; perform this review directly. Under 400 words."

**Spec sub-agent prompt** should include:

- The diff command and commit list.
- The path or fetched contents of the spec.
- The brief: "Read-only review: do not modify any files. Report: (a) requirements the spec asked for that are missing or partial; (b) behaviour in the diff that wasn't asked for (scope creep); (c) requirements that look implemented but where the implementation looks wrong. Quote the spec line for each finding. Do not invoke `code-review-lean` or `code-review`, and do not spawn sub-agents; perform this review directly. Under 400 words."

If the spec is missing, skip the Spec sub-agent and note this in the final report.

**Lean sub-agent prompt** should include:

- The diff command, commit list, and any identified spec/standards paths (so Lean respects documented contracts).
- Step 4 pasted in full (gates, counterweight, grading, clusters, and stack-disclosure pointers resolved to paths in this skill directory).
- The brief: "Read-only review: do not modify any files. Read the diff first, then read only the stack-disclosure references from Step 4 that match the changed code. Report only issues introduced, worsened, moved, or newly orphaned by this change, reassessing moved defenses under the same gates. Before reporting, verify contracts and reachability outside the diff hunk: check upstream producers/types/schemas for existing guarantees, trace production callers vs test-only callers (respecting public exports, registrations, and required trait/interface signatures), establish what a defense detects and how its rejection/recovery/diagnosis changes the outcome, and audit `tests/` diffs for corruption or zombie-resurrection tests. Every finding requires a concrete proof case ('upstream X already guarantees Y' or '0 production reachability outside tests/Z'). For each finding, give `file:line`, `[Hard]` or `[Simplify]`, the pattern name, verified evidence, and the exact deletion, cutover, or repair in 2–3 sentences. If none qualify, report 'No Lean findings.' Do not invoke `code-review-lean` or `code-review`, and do not spawn sub-agents; perform this review directly."

### 6. Aggregate

Present the three reports under `## Standards`, `## Spec`, and `## Lean` headings, verbatim or lightly cleaned. Do **not** merge or rerank findings across axes.

End with a one-line summary: total findings per axis, the worst issue _within each axis_ (if any), and any direct conflict where two axes recommend opposite actions on the same hunk. Don't pick a single winner across axes.
