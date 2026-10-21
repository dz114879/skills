---
name: implement-orchestrated
description: "Implement one ticket through a dedicated implementer and two-axis review."
disable-model-invocation: true
---

## 1. Settle the work

Act as the orchestrator for one ticket; the implementer owns all code changes.

Read the ticket and its parent spec via `docs/agents/issue-tracker.md` or the supplied source path. Record the starting `HEAD` SHA (`git rev-parse HEAD`) as the review fixed point.

Reuse the seams in the spec/ticket's `## Testing Decisions` as pre-agreed seams for `tdd`. Ask the user to confirm seams only when none are defined or they ask to revisit them.

Resolve genuine requirement ambiguities with the user before dispatch.

## 2. Dispatch the implementer

Spawn one implementer subagent and retain its session id for follow-ups. Pass the ticket/spec and pre-agreed seams, with instructions to:

- Call the Skill tool with "tdd", treating the supplied seams as already confirmed.
- Run typechecking, linting, and single-file tests while iterating; run the full test suite once at the end.
- Commit the work to the current branch and return the commit SHA and check commands with results.

## 3. Run the two-axis review

Confirm the commit landed with `git log <fixed-point>..HEAD --oneline` and `git diff <fixed-point>...HEAD --stat`, and confirm the returned checks passed.

Call the Skill tool with "code-review", passing the pinned fixed-point SHA and spec/ticket source. Capture the **Standards** and **Spec** reviewer session ids and separate reports. Keep findings and priorities within their own axis throughout triage and reporting.

## 4. Triage by reading code

For every finding, personally read the flagged source file and surrounding code alongside the cited standard or spec passage before deciding whether a fix is warranted. Include whether a flagged baseline smell is worth fixing here.

Record an accept/decline decision with a one-line reason for each finding. Ask the user before fixes that change the spec's meaning or expand scope.

## 5. Fix and focused re-review

If no findings are accepted, close out. Otherwise record the pre-fix `HEAD` SHA and send accepted findings to the **same implementer session** to create a new commit (not an amend) and run the affected checks.

Resume the **same Standards and Spec reviewer sessions**, each with the incremental fix delta (`git diff <pre-fix-SHA>..HEAD`) and its own prior findings, marking accepted items. Omit the implementer's rationale; ask each reviewer to verify fixes and check for regressions.

Return to step 4 with both reports.

## 6. Close out

Report commit SHA(s), separate **Standards** and **Spec** results, accepted and declined findings with one-line reasons, and the checks run with results.
