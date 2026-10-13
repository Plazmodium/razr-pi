<img width="2172" height="724" alt="Razr-Pi Cozy Coding Workshop" src="https://github.com/user-attachments/assets/72fc4eaa-8c65-4598-aa89-4fe334feb187" />

Razr-Pi is a local [Pi](https://github.com/badlogic/pi-mono) package for repository-centered engineering work. It combines a Razr workflow core, selective context assembly, durable Razr Memory, Pi commands, optional Herdr-hosted execution, and bounded GitHub delivery operations.

## Why Razr-Pi?

AI coding sessions are useful but temporary. Context can disappear between sessions, implementation can drift from intent, and parallel workers need clear limits. Razr-Pi addresses those problems by:

- treating the checked-out repository as the authoritative representation of the project;
- keeping specification, execution, validation, review, and delivery gates explicit;
- selecting only the context relevant to the current objective;
- retaining useful experience across sessions without copying the codebase into a second documentation system; and
- separating workflow authority from agent and process hosting.

## How it works

```text
Repository
    |
    | current source of project truth
    v
Razr-Pi
    |
    +-- workflow and delivery authority
    |
    +-- context selection
    |
    +-- Razr Memory
    |     durable experiential context
    |
    +-- execution
          standalone Pi
          or
          Herdr-hosted Pi workers
```

### Repository as project truth

Razr-Pi reads project truth from the repository: source, tests, types and schemas, configuration, build and deployment definitions, Git history and current changes, active Razr specifications and executable workflow artifacts, and intentional repository documentation.

### Workflow

The Razr workflow is:

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

The provenance-pinned Python core is the sole lifecycle authority. It owns legal transitions, generations, candidate identity, review records, and delivery state. Adapters and hosts may read or transport this state; they do not replace it.

### Context engine

The context engine assembles a deterministic, bounded packet for the current objective. It selects relevant repository files, tests, configuration, Git history, active workflow state, and Razr Memory instead of loading the whole project. Candidate-bound file content comes from one immutable Git tree and requires a clean checkout.

### Razr Memory

Razr Memory stores experience that the repository does not adequately express, such as:

- rationale for a non-obvious decision;
- recurring failure patterns and debugging discoveries;
- environmental quirks and workarounds;
- navigation hints and project-specific preferences; and
- context that helps future agents interpret the code correctly.

**Repository evidence is authoritative. Memory helps interpret and navigate it.** Memory is not a second copy of project documentation or source.

### Pi and Herdr

Pi is the agent runtime. The package supplies one instruction skill and one extension that registers five commands. Loading the package exposes those resources; it does not adopt a repository, execute work, or authorize writes.

Herdr is an optional execution host for workspaces, terminals, worktrees, and Pi worker processes:

- Razr owns workflow state and delivery authority.
- Pi performs reasoning and engineering work.
- Herdr hosts and observes execution.

Herdr states such as `working`, `idle`, `blocked`, or `done` are operational telemetry, not Razr workflow transitions.

## Installation and quick start

Prerequisites are Python 3.11+, Git, Node, and an installed Pi. Razr-Pi does not install or configure them.

Review the local package, then declare it through Pi:

```sh
pi install --local /reviewed/path/to/razr-pi/packages/razr-pi
pi list
```

The package is referenced from its resolved local path. Loading it registers:

- `/razr-status`
- `/razr-context`
- `/razr-remember`
- `/razr-recall`
- `/razr-memory-inspect`

Loading is read-only. To adopt the current repository explicitly:

```sh
python3 -B /reviewed/path/to/razr-pi/packages/razr-pi/scripts/adopt.py \
  --target "$PWD" \
  --work-root .razr/work
```

Inspect the target and command before running it. Adoption creates managed routing and delegates initialization to the Razr core.

## Typical workflow

1. Explore an idea and converge on a specification.
2. Approve the exact specification.
3. Materialize executable work with explicit dependencies and checks.
4. Verify the first task before expanding execution.
5. Build and validate the remaining work.
6. Bind a candidate to an exact repository, branch, commit, and source digest.
7. Obtain fresh independent Technical and Contract reviews of that candidate.
8. Read back delivery state and stop for final human review.

Use `/razr-status <work_id>` to inspect workflow state, `/razr-context` to assemble bounded context, and the Memory commands to retain or retrieve experiential context. Authorized orchestrators may use the Herdr provider directly for workers and the GitHub helper for bounded delivery operations.

## Architecture and reference

- [Package use and command reference](packages/razr-pi/README.md)
- [Architecture and authority](docs/architecture.md)
- [Detailed architecture contract](docs/architecture-contract.md)
- [Current capabilities and limitations](docs/capabilities.md)
- [Testing](docs/testing.md)
- [Source identity policy](SOURCE_BASELINE.md)
- [Execution provider boundary](docs/design/execution-provider.md)
- [Vertical-slice planning](docs/05-vertical-slices.md)

## Current capabilities

Razr-Pi currently provides:

- explicit safe adoption and discovery;
- core-backed status and validation read-back;
- bounded immutable-Git context selection;
- repository-scoped append-only Memory;
- five Pi command surfaces;
- an optional bounded Herdr execution provider with durable attempt evidence; and
- a separate source-bound GitHub snapshot/read/create helper.

## Current limitations

- The Pi command surface deliberately has no worker command; authorized orchestrators use the Herdr provider directly.
- Package loading is not execution, adoption, approval, or a sandbox.
- Memory secret filtering is heuristic and is not a confidentiality boundary.
- Context requires a clean exact candidate and rejects dirty, untracked, linked, or mutable source inputs.
- Herdr telemetry never proves workflow completion or review acceptance.
- GitHub reads are bounded and non-atomic; empty check or review collections do not prove execution or approval.
- No automatic merge, deployment, package publication, credential management, general critic dispatcher, or PR factory is provided.
- Exact source scrutiny, independent review, and final human PR review remain mandatory delivery gates.

## Verification

```sh
python3 -B tests/check_public_tree.py --root .
python3 -B tests/run_public.py --phase tracer --require-pi
python3 -B tests/run_public.py --phase full --require-pi
```

These commands verify the documented public source and selected integration behavior. They do not authenticate human approval or authorize publication, merge, or deployment.
