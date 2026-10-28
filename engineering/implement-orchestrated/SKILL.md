---
name: implement-orchestrated
description: "Implement a ticket or spec through a dedicated implementer and three-axis review."
disable-model-invocation: true
---

## 1. Settle the work

Act as the orchestrator; the implementer owns code changes. Modification scope, branch creation, tracker transitions, and commits follow the user's actual authorization.

Read the ticket or spec (via `docs/agents/issue-tracker.md` when applicable, or the supplied source). Record the starting `HEAD` SHA and any pre-existing worktree changes as the baseline so review covers only this task's changes.

Resolve choices that materially change requirements or scope with the user before dispatch.

## 2. Dispatch the implementer

Spawn an implementer subagent with the ticket/spec, baseline, and scope boundaries, instructing it to:

- Choose the method that fits the work: follow `tdd` for test-first work at agreed seams (reusing any seams in the ticket/spec and asking only when a new seam choice materially alters interfaces or scope), `diagnosing-bugs` for unexplained failures, or direct implementation for straightforward changes.
- Run the checks that provide evidence for the touched scope and required outcomes.
- Leave changes in the worktree unless the user authorized a commit. Return the changed files, check commands with results, and the commit SHA if committed.

## 3. Run the three-axis review

Confirm the implementer's changes are present against the baseline and the reported checks passed.

Run `code-review-lean` against the baseline (covering the current worktree and in-scope untracked files, excluding unrelated pre-existing edits) and the spec/ticket source to obtain the **Standards**, **Spec**, and **Lean** reports.

## 4. Triage findings

Evaluate findings against the code, spec, and repo rules; read the flagged source and surrounding context directly when a finding is contested, ambiguous, high-impact, or conflicts across axes.

Prioritize substantive issues: missing or broken spec requirements, documented-standard violations, and `[Hard]` Lean defects. Treat baseline smells and `[Simplify]` items as judgement calls—accept them only when the cleanup clearly pays off within scope, and decline low-value or churning suggestions without blocking closeout. Ask the user before any fix that changes the spec's meaning or expands scope.

## 5. Fix and verify

If no findings warrant changes, close out.

Otherwise send accepted fixes to the implementer (reusing the session when practical), preserving the commit policy from step 1, and rerun affected checks.

Verify straightforward, localized fixes directly. Re-run only the affected reviewer axis (or axes) when a fix reshapes logic, contracts, or cross-cutting structure; if re-review surfaces a new substantive issue or regression, return to step 4. Stop rather than looping on stylistic or oscillating (`[Simplify]`) feedback.

## 6. Close out

Report the changes made (and commit SHA if committed), **Standards**, **Spec**, and **Lean** outcomes, key accepted fixes and notable declined trade-offs, checks run with results, and any remaining limitations.
