---
name: razr
description: Guide explicitly adopted Razr projects through exact approval, execution, fresh dual review, human-gated Review entry and PR handoff. Loading never authorizes actions.
---

# Razr — start here

One primary Pi session handles the conversation and workflow. Pi supplies tools; the bundled Razr core validates contracts and owns workflow state. A skill is instructions, not approval, proof that checks ran, or a sandbox.

## Choose the next step

Read the project's instructions and actual decisions first. Follow only the row that applies; do not load every procedure or infer permission from a file's existence.

| Situation | Read next |
| --- | --- |
| New idea or project; no exact approved pair | [Greenfield preparation](references/greenfield.md). Keep discovery separate from implementation. |
| Existing project needs explicit adoption | [Safe adoption](references/adoption.md). Never adopt or initialize automatically. |
| Human wants to stop using Razr routing in this project | [Safe de-adoption](references/de-adoption.md). Retain workflow data by default. |
| Exact pair approved; task execution or candidate work authorized | [Primary execution](references/primary-execution.md). This is the single runbook from approval through the first human checkpoint and separately permitted candidate work. |
| Candidate is in Verify; formal review or delivery requested | [Independent review through human PR handoff](references/review-delivery.md). Preserve fresh sessions, separate human gates and exact remote identity. |

If the current state is incomplete, conflicting, failed, already running or completed, inspect it and establish the authorized next action. Do not reset, retry or treat it as a new pending task.

## Rules that always apply

- Preserve the exact human-approved spec and executable. Scope agreement or prepared approval metadata is not an observed human decision.
- Only the unchanged core materializes lifecycle artifacts and changes `state.yaml`. The primary appends inspected actual proof to core-created `evidence.md`, preserving earlier evidence. Never invent PASS results.
- Read the generation immediately before each mutation that supports `--expect-generation`. Do not calculate the next value or retry after a refusal.
- Stop after the first verified task for actual human inspection. Candidate work also needs permission in the exact contract and completion of all declared tasks.
- Keep source identity and inspect digest-excluded instructions/configuration too. Later formal review requires separate fresh Technical and Contract sessions on the same source; a model switch or builder-history fork is not independent.
- Stop immediately on any unexpected read or command error: preserve the result, report it and wait for human direction. Do not continue other task work, guess another path, repair or retry. Expected negative test cases are not unexpected errors. If a check shows that preparation cannot succeed, stop immediately. Don’t attempt the command just to confirm the failure.
- Save ordinary task logs only in a new directory inside the approved target, never `/tmp`; review runs are the explicit exception and must use a new retained directory outside both target and package. Directory checks and creation must succeed before redirection or commands run. Use explicit fail-fast chaining or checked exits; `set -u` alone and unchecked semicolons are insufficient. Do not overwrite or delete logs. Read saved output before relying on it. Preserve failures and consumed allowances; no automatic cleanup, retry, merge or deployment. Stop for credentials, spending, destructive operations, production and material scope decisions.
- Installation, global configuration and credentials remain human-owned. Model use, network actions, push and PR creation each require actual scoped authorization. Final human PR review remains mandatory.

## Review and delivery boundary

The experimental, acceptance-pending path is primary-led and candidate-bound. Helpers prepare/transport/audit evidence; they do not authenticate human approval, semantic support or independence. Fresh Technical and Contract sessions must review the same commit without builder/peer history. The human separately approves Review entry and remote mutation, then owns final review of the actual PR. No automatic merge, deploy or publication is provided.

## Resource paths

Resolve paths from this skill's directory, not the project cwd: package `../..`, core `../../runtime/scripts/razr.py`, read-only inspector `../../scripts/primary.py`, adoption helper `../../scripts/adopt.py`, de-adoption helper `../../scripts/de-adopt.py`, review helper `../../scripts/review-flow.py`, GitHub helper `../../scripts/github-delivery.py`, provenance `../../runtime/provenance.json`. Use resolved absolute paths; never guess a `razr` executable from PATH or use the sibling distribution.

No launcher is required for ordinary task execution. The bounded launcher is used only after candidate binding and explicit model authorization. File checks, process output, model reports, hashes and flags are observations—not dispatch authority, semantic support or workflow approval.
