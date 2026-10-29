<!-- RAZR:START -->
## Razr in Pi

This project explicitly adopts Razr. Load the reviewed `razr` Pi skill before workflow actions. If unavailable, stop and ask the human to load the reviewed local package; do not install automatically or guess a CLI from PATH.

One primary Pi session owns the conversation and supported workflow mutations. The provenance-pinned Razr Python core is the sole lifecycle authority; never hand-edit canonical state. Read project rules and actual human decisions, then inspect core status and validation before acting.

Authority: `spec.md` owns exact approved intent; `executable.yaml` owns the immutable approved DAG; `state.yaml` owns generation-fenced progress; `evidence.md` records observed proof. Git owns candidate history. The live PR is the final human review surface.

Follow the shipped skill procedures in order:

1. discovery and exact spec/executable approval;
2. declared task execution with actual evidence and fresh generations;
3. mandatory human inspection after the first verified task;
4. explicit candidate staging/commit, source identity and digest-excluded input scrutiny;
5. fresh independent Technical and Contract Pi sessions against the same frozen candidate inputs;
6. primary audit and supported original-outcome recording through the core;
7. explicit human approval before Verify-to-Review transition;
8. separately authorized non-force push and exact GitHub PR create/read-back;
9. core PR recording and final human PR handoff.

Package loading, hashes, flags, CLI attestations, model identity and Herdr telemetry do not prove execution, independence or human approval. A model switch, resumed/forked builder session or context carrying builder history is not fresh critique. Incomplete, unsupported, contaminated, drifted or failed review stops; there is no automatic retry, repair or fallback. A repair requires legal return to Build, a new candidate and two new reviews.

The source digest excludes root `AGENTS.md`, `razr.yaml` and the configured work root; inspect them explicitly. Use freshly read generations for every core mutation. Preserve failures and consumed review claims. Stop for credentials, spending, destructive operations, production actions and material scope decisions.

No automatic install, global configuration, force-push, compensation, merge, deployment, release or publication is permitted. Review entry, remote mutation and final PR acceptance remain distinct human-owned decisions.
<!-- RAZR:END -->
