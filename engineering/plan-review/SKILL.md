---
name: plan-review
description: "Review a plan, or tickets with their spec, through four fresh-context reviewers before implementation."
disable-model-invocation: true
---

# Plan Review

Run this in the session that wrote the Plan and holds the conversation behind it. Four fresh-context reviewers (subagents that know only what their brief points them to) each review the Plan along one axis; you triage their findings with the user and edit the Plan.

Without a subagent tool, stop and tell the user this review needs one.

## Axes

- **Reader**: self-containment, via a newcomer's paraphrase.
- **Implementer**: readiness: Open Decisions, False Premises, coverage, blocking edges.
- **Critic**: a premortem weighed against YAGNI.
- **Reuse**: build-vs-buy, including new dependencies.

Each axis has its own file in this skill's directory (`READER.md`, `IMPLEMENTER.md`, `CRITIC.md`, `REUSE.md`); `REVIEWER.md` holds the rules every axis follows. They are written for the reviewers; open one only to understand a finding.

## 1. Pin the Plan

The **Plan** is what implementers will work from: an ad-hoc plan document, or tickets together with the spec or plan they were cut from (tickets cut straight from conversation have no parent).

- Write any part of the Plan that lives only in conversation (a chat-only plan, or decisions made since the file was written) into its files first: reviewers see only what is written.
- A spec without tickets: stop and tell the user to run `/to-tickets` first. Reviewing the spec now means reviewing it again once its tickets exist.

Create a fresh report directory with `mktemp -d`. Fetch tickets that live on a remote tracker into files there, per `docs/agents/issue-tracker.md`.

Run all four axes on the whole Plan unless the user narrows the review to some axes or tickets.

Done when every Plan file has a local path, the parent (if any) is identified, and the axes to run are fixed.

## 2. Brief and fan out

Spawn one reviewer per axis, in parallel, each with a fresh context. Fill this brief and add nothing else: the conversation, your account of the intent, and your own suspicions each anchor the reviewer to your reading of the Plan, which is the bias this review exists to remove.

```
You are the <Axis> reviewer reviewing a Plan.
Read <skill dir>/REVIEWER.md, then <skill dir>/<AXIS>.md, and follow them.
Repo root: <path>
Plan files: <paths, parent document first>
Report: write <report directory>/<axis>.md
```

`<skill dir>` is the absolute path of this skill's directory. Two optional lines follow the template:

- `Review only: <tickets>` when the user narrowed to some tickets. `Plan files` still lists the whole Plan so coverage and blocking edges stay checkable.
- `User concern, verbatim: "<text>"` when the user voiced a concern in their own words.

Done when every reviewer has returned its report path and Blocker/Major/Nit counts. A reviewer that returns no report did not review: mark its axis as not run, never as clean.

## 3. Triage

Compare the Reader's paraphrase, claim by claim, with the intent you hold. Each place it reads the Plan differently from what you meant becomes a finding in the Reader section, worded "Reader read X as Y": a misread of what the Plan proposes is a Blocker, a misread of how the current system works is a Major. When another axis returns 0/0/0 (and no `User concern` was passed), scan only its "Looked for, found nothing" receipts to confirm no in-scope ticket or check was skipped.

Then give every finding one suggestion:

- **Accept**: the finding holds; you will edit the Plan.
- **Reject**: cite evidence the reviewer could have read, either a file and symbol or a decision already written in the Plan; for a Blocker or Major, read the cited source yourself first. "This was intentional" is a wish, not evidence: you wrote the Plan, so your pull toward Reject is motivated reasoning. Reject is the only way to dispute a severity.
- **Ask**: the evidence lives outside every file (a verbal decision, a user preference), or the finding proposes a new dependency.

Genuine Reader comprehension findings take Accept or Ask: implementers start as fresh as the Reader did, so what the Reader could not get from the page, they cannot either. If Reader drifts out of lane to raise an unchosen implementation branch or code claim on an otherwise clear passage, reclassify and triage it under the owning axis (Implementer or Critic) rather than locking out Reject.

Done when every claim of the Reader's paraphrase has been checked against your intent, each divergence is a Reader finding, and every finding carries a suggestion (every Reject its evidence).

## 4. Report

The evidence lives in the report files; the chat carries the gist.

- One heading per axis, in the order above, findings ranked by severity within each.
- Each finding in a line or two: severity, the gist, your suggestion (with the evidence for a Reject). Nits take one line each.
- A finding several axes raised appears once, under the first of them, naming the others.
- Conflicts, where two axes pull the same part of the Plan in opposite directions, go in a closing **Decisions** list for the user.
- End with the report directory path.

Then wait for the user's decisions.

## 5. Apply

Edit the Plan as decided. Write each decision the user made on an Ask into the Plan itself (Out of Scope, a note on rejected alternatives, or an ADR when it qualifies), so the next reviewer reads it instead of raising it again.

Done when every Accept and every user decision is in the Plan's files.

When accepted Blocker or Major fixes changed the Plan, offer one re-run round of the axes behind them, each with a new reviewer: one that has seen the old Plan is no longer fresh. When a re-run finding could only be resolved by reverting an earlier fix, the review is churning: stop and put both findings to the user.
