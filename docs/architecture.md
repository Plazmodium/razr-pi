# Architecture and authority

Razr-Pi connects repository evidence, the Razr workflow, Pi, optional Herdr execution, and GitHub delivery without collapsing their authority boundaries.

## System model

```text
Repository
    |
    | authoritative project evidence
    v
Razr-Pi core
    |
    +-- workflow and delivery state
    +-- context selection
    +-- Razr Memory
    +-- Pi command surface
    +-- optional Herdr execution provider
    +-- bounded GitHub delivery helper
```

## Repository authority

The checked-out repository is the authoritative representation of the project. Source, tests, types and schemas, configuration, build/deployment definitions, Git history and changes, intentional documentation, and active Razr artifacts provide project truth.

Current user direction and the current checkout take precedence. Razr Memory is advisory and cannot override repository evidence.

## Workflow authority

The provenance-pinned Python core is the sole lifecycle authority. It owns:

- `spec.md` — approved intent;
- `executable.yaml` — the executable dependency graph;
- `state.yaml` — generation-fenced materialized progress; and
- `evidence.md` — reproduced observations.

The product flow is:

```text
Idea / exploration
        ↓
Specification
        ↓
Executable work
        ↓
Build
        ↓
Validation
        ↓
Candidate
        ↓
Independent review
        ↓
Delivery
```

No adapter, extension, model, provider, Memory record, process status, or CLI message may hand-edit lifecycle state or infer approval.

## Context and Memory

The context engine selects a bounded objective-specific packet from one clean immutable Git candidate. It may include relevant source, tests, configuration, Git history, workflow read-back, and lower-authority Memory. It records omissions rather than loading the entire repository.

Razr Memory retains experiential context that source alone may not convey: rationale, failure patterns, debugging discoveries, environmental quirks, workarounds, navigation hints, and preferences. It is repository-scoped, evidence-linked, bounded, and append-only.

**Repository evidence is authoritative. Memory helps interpret and navigate it.**

## Pi boundary

Pi is the reasoning and tool-execution runtime. Razr-Pi supplies one instruction skill and one extension with five commands. Loading those resources is not execution, adoption, approval, or write authority.

The extension delegates to the core and adapters. It does not implement another workflow state machine.

## Herdr boundary

Herdr optionally hosts workspaces, terminals, worktrees, panes, and Pi workers. The separation is:

```text
Razr   workflow contracts, state, gates, and delivery authority
Pi     reasoning and engineering work
Herdr  execution hosting and operational telemetry
```

The provider transports an already authorized request, records attempt evidence, and returns observations. Herdr `working`, `idle`, `blocked`, `done`, process exit, or pane disappearance never implies a Razr transition or critic verdict.

The provider applies generation/candidate fences before dispatch, a final read-only fence before launch, fresh-context requirements, bounded output/time, durable resource identities, append-only attempt events, process-group cleanup, and explicit recovery. Uncertain outcomes remain `UNKNOWN`.

## Delivery boundary

Git owns candidate commit identity. The GitHub helper observes or creates one explicitly authorized PR bound to exact repository, base/head branches, full commits, and source digest.

Delivery readiness requires exact equality between the PR head, candidate commit, and both independently reviewed commits. Source-digest equality alone is insufficient. GitHub observations are bounded and non-atomic; absent checks or reviews are observations, not evidence of success or approval.

The helper never merges, deploys, force-pushes, launches critics, or authenticates human approval.

## Trust and review gates

The architecture keeps these gates separate:

1. exact specification approval;
2. generation-fenced execution;
3. first-task verification before expansion;
4. exact candidate and excluded-input scrutiny;
5. fresh independent Technical and Contract review; and
6. final human PR review.

Hashes establish identity, not execution or approval. A model switch or a context carrying builder history is not independent review.

## Further detail

- [Architecture contract](architecture-contract.md)
- [Capability boundaries](capabilities.md)
- [Execution provider](design/execution-provider.md)
- [Testing](testing.md)
