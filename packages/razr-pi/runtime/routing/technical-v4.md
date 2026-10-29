<!-- RAZR:START -->
## Razr in Pi

This project explicitly adopts Razr. Load the reviewed `razr` Pi skill before workflow actions. If unavailable, stop and ask the human to load the reviewed local package; do not install anything automatically or guess a CLI from PATH.

The primary Pi agent owns conversation and workflow mutations. The existing bundled Razr Python CLI remains the validator; never hand-edit lifecycle state. Before acting, read project rules and run actual `status --json` and `validate --all` through the bundled core.

Authority: `spec.md` owns approved intent; `executable.yaml` owns the immutable approved DAG; `state.yaml` owns generation-fenced progress; `evidence.md` records actual proof. Git owns source history; a live PR is the human review surface. Before approval, discovery contains only `spec.md`.

For an exact human-approved canonical spec and digest-matched executable, the primary may use the supported CLI to approve, run the declared first task, record actual validation evidence, and complete that task with current generation fencing. Then pause for a human checkpoint. This is not permission to infer approval, fabricate PASS evidence, or continue beyond the checkpoint.

Candidate recording is permitted only when that work item's exact human-approved canonical spec explicitly requires it, every declared node and first-task checkpoint is complete, required artifacts are committed, and the core's source-identity gates pass. Entering Verify stops unless that exact approved canonical contract explicitly permits one bounded Technical Critic launch and report collection. Only after the human first-task checkpoint, verified helper/resource-loading prerequisites, and explicit live-run limits may the primary launch that fresh saved headless session. The critic returns evidence only; the primary inspects actual candidate-bound session/tool results and reproduces relevant claims, then pauses for human report inspection. No Contract Critic launch, critic-outcome recording, Verify-to-Review transition, PR action, merge, or deployment is permitted in this increment. Future gates remain mandatory: fresh independent Technical and Contract critics against one candidate, live PR read-back, and final human review. A model switch or fork with builder history is not independent critique.

CLI attestations do not authenticate approval, execute tests, or prove critic independence. The source digest excludes root `AGENTS.md`, `razr.yaml`, and the configured work root; these paths still require explicit scrutiny. Stop on partial/inconsistent state and preserve artifacts. No automatic install, global configuration, merge, deployment, or cleanup.
<!-- RAZR:END -->
