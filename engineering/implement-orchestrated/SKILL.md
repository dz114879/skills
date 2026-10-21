---
name: implement-orchestrated
description: "Implement one ticket through a dedicated implementer and two-axis review."
disable-model-invocation: true
---

## 1. Role and preconditions

Act as the main-agent orchestrator. The main agent does not write code. Own scope, user decisions, dispatch, evidence verification, review triage, and reporting; the implementer owns code, tests, checks, and commits.

Handle one ticket per invocation. Require an existing durable ticket or spec file in the repository as the source of truth; conversation alone is insufficient. Require `writing-for-agents`, `tdd`, and `code-review` to be installed and callable before proceeding.

Call the Skill tool with "writing-for-agents" before composing any subagent brief, including follow-ups.

## 2. Settle the work

Read the ticket/spec, resolve its repository path, and state its title. Resolve ambiguities with the user before dispatch.

Record the current branch, starting HEAD (`git rev-parse HEAD`), and existing worktree changes. Inspect `git status --short`, `git diff`, and `git diff --cached`; account for untracked files. Resolve any ownership overlap with the ticket before dispatch.

Identify the public test seams. Give each proposed seam a one-line account of what it catches and misses, then confirm the proposed seams with the user. Treat this confirmation as the implementer's pre-agreement for `tdd`.

Pin the review fixed point to the starting HEAD SHA and keep it unchanged through fixes. Proceed only when the durable source, scope boundaries, worktree baseline, fixed point, and user-confirmed seams are recorded.

## 3. Dispatch the implementer

Dispatch one implementer subagent and retain its session id for every implementation follow-up.

**Implementer brief:**

- Point to the durable ticket/spec path, its acceptance criteria, the starting SHA, and the agreed scope boundaries.
- State the confirmed seams and what each covers. Require: Call the Skill tool with "tdd" and use these already-confirmed seams.
- Preserve existing worktree changes and exclude them from commits. Stop and report a new seam, ambiguity, or scope expansion before acting on it.
- Run focused checks while iterating, including applicable typechecking, linting, and single-file tests; run the full test suite once at the end, as the repository prescribes. Record exact commands and results, including failures; list unavailable checks as limitations.
- Commit the ticket work on the current branch. Return the commit SHA, changed files, checks with results, seams covered, and blockers.

Wait for the handback before reviewing.

## 4. Verify the handback

Verify the returned SHA resolves to a commit at current HEAD on the recorded branch and descends from the fixed point. Inspect `git log <fixed-point>..<SHA> --oneline` and `git diff <fixed-point>...<SHA>`: require a non-empty diff confined to the ticket. Confirm the recorded pre-existing changes were preserved and excluded from the commits.

After a fix, additionally verify that the new SHA differs from and descends from the pre-fix SHA. Require `git diff <pre-fix-SHA>..<new-SHA>` to contain only changes addressing accepted findings. Reject rewritten history, including an amended implementation commit.

Check every requested return field, including results for each required check and coverage of the confirmed seams. Send missing evidence or handback gaps back to the same implementer session. Advance only with a verified in-scope commit, complete evidence, and no unresolved implementer blocker.

## 5. Run the two-axis review

Call the Skill tool with "code-review", passing the pinned fixed-point SHA, durable spec path, and `<fixed-point>..<SHA>` commit range. Require the supplied path to be the Spec source. Treat a readable supplied spec path as sufficient; continue without stopping for tracker setup.

Capture the two reviewer agent ids as they are created, labelled **Standards** and **Spec**, and retain their reports. Assign stable axis-local finding ids, such as `Standards-1` and `Spec-1`, when collecting the initial reports. Keep these axes separate through triage, follow-ups, and reporting; never merge or rerank them. Proceed only with both reviewer ids and both axis reports.

## 6. Triage and fix

Verify each finding's cited standard, spec passage, or demonstrated failure against its source and relevant code before accepting it. Accept only documented Standards violations, clear Spec gaps or misimplementation, and demonstrated correctness failures. Decline judgement-call smells and style preferences with a one-line reason. Record a decision and reason for every finding.

If a fix would change the spec's meaning, stop and ask the user before proceeding. If no findings are accepted, close out. Otherwise send only accepted findings to the same implementer session.

**Fix brief:**

- Supply the spec path and pre-fix HEAD; retain the original scope, confirmed seams, and stop conditions.
- List accepted finding ids with their cited standard, spec requirement, or demonstrated failure. Require a disposition for each id.
- Create a new fixup commit on the current branch, not an amend. Run the affected checks.
- Return the new SHA, changed files, checks with results, seams covered, finding dispositions, and blockers.

Apply step 4 to the fixup handback before focused re-review.

## 7. Focused re-review

Resume the same two reviewer sessions, including an axis with no accepted findings. Do not make a second `code-review` invocation. Build each brief from the fix delta and that reviewer's prior findings; omit the implementer's fix rationale.

**Focused re-review brief:**

- State the reviewer's original axis and provide their own prior findings with the stable ids, identifying which were accepted.
- Supply the new HEAD SHA and `git diff <pre-fix-SHA>..<new-HEAD>` fix delta, plus the original standards sources for Standards or durable spec path for Spec.
- Check resolution of accepted findings and demonstrated regressions in the fix delta; inspect surrounding code as needed for those checks.
- Return resolved and unresolved finding ids, regression evidence, the axis result, and limitations.

Require both original reviewer sessions to return focused reports for the new HEAD before closeout. A missing or skipped focused report is an incomplete review, not a pass.

Allow one fix-and-focused-review round by default. Permit one extra round only for a demonstrated regression, using steps 6 and 7 with the recorded sessions. After the allowed rounds, report unresolved findings as limitations rather than extending the loop or claiming a pass.

## 8. Close out

Report:

- Implementation and fixup commit SHA(s).
- Standards and Spec results separately, including unresolved findings.
- Accepted and declined findings with reasons.
- Exact checks actually run and their results, plus seams covered.
- Blockers, unavailable checks, and remaining limitations.

Create no PR, close no ticket, and make no tracker update unless the user or an explicit tracker flow requests it. Finish when the report accounts for every finding and accurately states the verified outcome.

## 9. Failure handling

- **Missing durable source or required skill:** stop and identify the missing repository ticket/spec or dependency.
- **Implementer blocker:** stop and report it. Obtain the user's decision for a new seam, ambiguity, or scope expansion before resuming the recorded implementer session.
- **Missing evidence:** keep review paused during step 4's follow-up. If the evidence remains unavailable, stop and report the exact gap.
- **Unavailable implementer or reviewer session:** stop and report which recorded session cannot be resumed. Do not substitute a fresh agent.
- **Missing Spec source:** stop review and report the missing path or inaccessible content. A skipped Spec axis is incomplete, not a successful two-axis review.
