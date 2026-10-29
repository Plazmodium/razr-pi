<!-- RAZR:START -->
## Razr in Pi

This project explicitly adopts Razr. Load the `razr` Pi skill before workflow actions. If unavailable, stop and ask the human to load the reviewed local package; do not install anything automatically or guess a CLI from PATH.

The primary Pi agent owns conversation and workflow mutations. The existing Razr Python CLI remains the validator; never hand-edit lifecycle state. Before acting on an existing project, read its rules and run actual `status --json` and `validate --all` through the skill's bundled core.

Authority: `spec.md` owns intent; `executable.yaml` owns the approved immutable DAG; `state.yaml` owns generation-fenced progress; `evidence.md` records reproduced proof. Git owns source history; the live PR is the human review surface. Before approval, discovery contains only `spec.md`.

This adapter currently supports adoption and discovery only. Stop before approval, compilation, build, candidate recording, or PR actions. Do not mistake this support boundary for removal of the eventual gates: exact human specification approval, first verified-task human checkpoint, source-digest identity, fresh independent Technical and Contract critics, and final human PR review remain mandatory.

CLI attestations do not authenticate approval, execute tests, or prove critic independence. A model switch or fork with builder history is not fresh critique. The source digest excludes the entire root AGENTS.md, razr.yaml, and configured work root; these paths still require explicit scrutiny. Stop on partial or inconsistent state, preserve artifacts, and never invent recovery evidence. No automatic install, global configuration, merge, or deployment.
<!-- RAZR:END -->
