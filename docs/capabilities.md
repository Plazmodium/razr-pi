# Capabilities and limitations

## Product boundary

Razr-Pi includes an experimental, acceptance-pending primary-led end-to-end delivery path for bounded GitHub-backed projects. It covers explicit adoption, discovery, exact-approved execution, the first-task checkpoint, candidate binding, fresh independent Technical and Contract review, human-approved Review entry, exact PR creation/read-back and final human PR handoff. It does not merge or deploy automatically.

## Supported capabilities

### Repository adoption and workflow inspection

Razr-Pi safely adopts an explicitly selected repository, creates one current managed routing block, and delegates workflow initialization and validation to the Python core. Status reads are core-backed and do not mutate lifecycle state. The Pi status surface reports core state separately from the adapter boundary and refuses missing, ambiguous, unknown or locally modified routing without inferring approval.

### Pi package surface

The package supplies one instruction skill and one command extension. It registers:

- `/razr-status`
- `/razr-context`
- `/razr-remember`
- `/razr-recall`
- `/razr-memory-inspect`

Loading is read-only and does not adopt a project or authorize writes.

### Context selection

The context engine creates deterministic bounded packets from:

- tracked source, tests, and configuration;
- bounded Git history;
- active core-backed workflow state; and
- repository-scoped Razr Memory.

Candidate-bound content comes from one immutable Git commit tree. Dirty, untracked, linked, private, or mutable input is refused.

### Razr Memory

Memory is local, append-only, bounded, evidence-linked, and scoped to the live repository source identity. It supports remember, recall, inspect, retire, and supersede operations. Changed or foreign evidence is labelled stale or omitted and cannot override repository evidence.

Memory is intended for rationale, failure patterns, debugging discoveries, environmental quirks, workarounds, navigation hints, and preferences—not a prose mirror of the codebase.

### Herdr-hosted execution

The optional Herdr provider supports capability probing, launch, inspect, collect, interrupt, and explicit unmatched-attempt recovery. It verifies workflow generation and candidate identity before dispatch and again immediately before launch.

Attempts use fresh context, separate caller/target identity, permanent resource identities, append-only STARTED/SETTLED evidence, bounded time/output, process-group cleanup, and truthful `UNKNOWN` outcomes. Herdr telemetry is never Razr state.

### Independent review

`scripts/review-flow.py` creates immutable candidate-bound neutral inputs, launches separately authorized fresh Technical and Contract Pi sessions with explicit model/time/output bounds, retains structured events and reports, validates report identity/completeness, and supports primary-owned generation-fenced outcome recording through the core.

The launcher has no billable model default, retry, fallback or repair path. Structural validation and flags do not prove semantic support, independence or human approval. The primary must inspect actual accesses/results, reproduce relevant checks, preserve consumed claims and stop on incomplete or failed review.

### GitHub delivery

The separate GitHub helper supports:

- exact local `snapshot`;
- bounded PR `read`; and
- separately authorized PR `create`.

Operations require explicit repository/base/head/full commits/source digest; there is no default base. Read results disclose bounded/non-atomic and collection-completeness limits. Candidate movement or malformed provider data is refused. Post-create compensation is disabled unless separately authorized for the exact safely rebound PR.

### Clean adoption and safe routing removal

Adoption supports one current managed routing block and refuses unknown or modified blocks rather than replacing them. `scripts/de-adopt.py` never changes `AGENTS.md`; for the exact current managed block it creates original/proposed files for human review and manual application. It retains `razr.yaml`, `.razr/`, specifications, evidence and Memory by default. Applying the proposal, package uninstallation, archival or deletion of retained data requires a separate explicit decision.

## Deliberately unsupported

Razr-Pi does not provide:

- automatic project adoption;
- a Pi worker command;
- automatic model selection, critic retry, fallback or repair;
- implicit retry after uncertain execution;
- an autonomous PR factory or automatic compensation;
- automatic merge, deployment, or publication;
- credential installation or global configuration; or
- inference of approval from a hash, model report, process state, or CLI message.

## Security and consistency limits

- Memory secret filtering is heuristic and is not a confidentiality boundary.
- Memory locking coordinates cooperative processes and is not a hostile same-UID sandbox.
- Context requires a clean exact Git candidate.
- Descriptor and identity checks are cooperative filesystem hardening, not hostile-OS authentication.
- Herdr cleanup cannot control deliberately detached processes outside the owned process group; uncertainty is reported.
- GitHub reads are bounded and non-atomic. No-check and empty-review results do not prove execution or approval.
- Test coverage establishes only the selected source, fixtures, commands, and environments.

## Mandatory human and review gates

For delivery, Razr-Pi requires exact candidate identity, excluded-input scrutiny, fresh independent Technical and Contract review of that exact commit, explicit human approval before Review entry, separate authorization for remote mutation, and final human review of the actual PR. These gates remain outside package loading, helper flags and automated test results.
