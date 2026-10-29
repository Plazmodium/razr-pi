---
name: razr
description: Guide explicitly adopted Razr projects through exact approval, task execution, human checkpoints and separately authorized review. Loading never authorizes actions. Stops before Review or PR work.
---

# Razr — start here

One primary Pi session handles the conversation and workflow. Pi supplies tools; the bundled Razr core validates contracts and owns workflow state. A skill is instructions, not approval, proof that checks ran, or a sandbox.

## Choose the next step

Read the project's instructions and actual decisions first. Follow only the row that applies; do not load every procedure or infer permission from a file's existence.

| Situation | Read next |
| --- | --- |
| New idea or project; no exact approved pair | [Greenfield preparation](references/greenfield.md). Keep discovery separate from implementation. |
| Existing project needs explicit adoption | [Safe adoption](references/adoption.md). Never adopt or initialize automatically. |
| Project was adopted by an earlier incompatible release | [Safe de-adoption](references/de-adoption.md), then restart from the beginning in a clean repository. No in-place upgrade. |
| Human wants to stop using Razr routing in this project | [Safe de-adoption](references/de-adoption.md). Retain workflow data by default. |
| Exact pair approved; task execution or candidate work authorized | [Primary execution](references/primary-execution.md). This is the single runbook from approval through the first human checkpoint and separately permitted candidate work. |
| Formal review requested | Stop. This release has no general review procedure for arbitrary projects. |

If the current state is incomplete, conflicting, failed, already running or completed, inspect it and establish the authorized next action. Do not reset, retry or treat it as a new pending task.

## Rules that always apply

- Preserve the exact human-approved spec and executable. Scope agreement or prepared approval metadata is not an observed human decision.
- Only the unchanged core materializes lifecycle artifacts and changes `state.yaml`. The primary appends inspected actual proof to core-created `evidence.md`, preserving earlier evidence. Never invent PASS results.
- Read the generation immediately before each mutation that supports `--expect-generation`. Do not calculate the next value or retry after a refusal.
- Stop after the first verified task for actual human inspection. Candidate work also needs permission in the exact contract and completion of all declared tasks.
- Keep source identity and inspect digest-excluded instructions/configuration too. Later formal review requires separate fresh Technical and Contract sessions on the same source; a model switch or builder-history fork is not independent.
- Stop immediately on any unexpected read or command error: preserve the result, report it and wait for human direction. Do not continue other task work, guess another path, repair or retry. Expected negative test cases are not unexpected errors. If a check shows that preparation cannot succeed, stop immediately. Don’t attempt the command just to confirm the failure.
- Save command logs only in a new directory inside the approved target, never `/tmp`; directory checks and creation must succeed before log redirection or task commands run. Use explicit fail-fast chaining or checked exits; `set -u` alone and unchecked semicolons are insufficient. Do not overwrite or delete logs. Read saved output before relying on it. Preserve failures and consumed allowances; no automatic cleanup, merge or deployment. Stop for credentials, spending, destructive operations, production and material scope decisions.
- Installation, global configuration and credentials remain human-owned. This package stops before Review/PR work; final human PR review remains required for later delivery.

## Review boundary

This breaking release does not provide general Technical/Contract critic dispatch or Review/PR delivery for arbitrary projects. Do not reuse retained lab procedures as product capability. Stop and establish a separately specified, approved and verified review procedure before any formal critique or later lifecycle transition.

## Resource paths

Resolve paths from this skill's directory, not the project cwd: package `../..`, core `../../runtime/scripts/razr.py`, read-only inspector `../../scripts/primary.py`, adoption helper `../../scripts/adopt.py`, de-adoption helper `../../scripts/de-adopt.py`, provenance `../../runtime/provenance.json`. Use resolved absolute paths; never guess a `razr` executable from PATH or use the sibling distribution.

No launcher or extra worker is required for the ordinary task procedure. File checks, process output and model reports are observations, not dispatch authority or workflow approval.
