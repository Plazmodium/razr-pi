# Current Razr-Pi release

Razr-Pi `0.2.0` is a breaking local Pi package release. It does not migrate routing or workflow state created by previous releases.

## Start here

1. [Repository overview](../README.md)
2. [Package installation and commands](../packages/razr-pi/README.md)
3. [Capabilities and limitations](capabilities.md)
4. [Architecture and authority](architecture.md)
5. [Testing](testing.md)
6. [Source identity policy](../SOURCE_BASELINE.md)

## Supported now

- Explicit repository adoption and validated discovery.
- Core-backed status that reports workflow state separately from adapter authority.
- Clear detection of incompatible previous-release routing.
- Safe de-adoption preparation that creates exact original/proposed files and retains project data.
- Exact-approved first-task execution and the required human checkpoint.
- Experimental candidate-bound fresh Technical and Contract review with explicit model bounds and primary audit.
- Acceptance-pending human-gated Verify-to-Review transition, exact GitHub PR create/read-back and final human PR handoff.
- Deterministic bounded context, repository-scoped Memory, and the documented read-oriented Pi commands.

## Breaking restart policy

Projects adopted by previous releases must start again from the beginning in a clean repository. There is no in-place routing or workflow-state upgrade.

Use `scripts/de-adopt.py` to create exact original/proposed `AGENTS.md` files, then compare the current file and apply the proposal manually. The helper never changes `AGENTS.md`; it retains `razr.yaml`, `.razr/`, specifications, state, evidence and Memory. Applying the proposal, or archiving/deleting retained data, is a separate explicit decision.

## Deliberately not automated

- Model/spending authorization, semantic critic support or human approval.
- Retry, repair, fallback, force-push or post-create compensation without a new explicit decision.
- Automatic merge, deployment or publication.
- Credential installation or global configuration.
- Treating Pi, Herdr, process output or CLI validation as proof of human approval or completed checks.

Final human review and all original Razr gates remain mandatory for every delivered candidate.
