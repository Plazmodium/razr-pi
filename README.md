Razr-Pi is a local [Pi](https://github.com/badlogic/pi-mono) package for repository-centered engineering work. It combines a Razr workflow core, selective context assembly, durable Razr Memory, Pi commands, optional Herdr-hosted execution, and bounded GitHub delivery operations.

Already using Pi? Point your agent to [For Pi agents](#for-pi-agents) for guided setup.

> **Breaking release:** Razr-Pi does not support in-place migration from previously generated project routing or workflow state. Existing users must start again from the beginning in a clean repository. The helper prepares original/proposed `AGENTS.md` files for human-applied de-adoption without deleting project data; archival or deletion of retained `razr.yaml` and `.razr/` data is a separate explicit decision.
>
> Razr-Pi includes an experimental primary-led end-to-end path for bounded GitHub-backed projects: exact approval, execution, first-task checkpoint, candidate binding, fresh independent Technical and Contract review, human-gated Review entry, exact PR creation/read-back, and final human PR handoff. Release acceptance is still pending; it never merges or deploys automatically.

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

Clone Razr-Pi into a reviewed tools directory, then install the local package from the repository you want to use it with:

```sh
git clone https://github.com/Plazmodium/razr-pi.git /path/to/razr-pi
cd /path/to/your-project
pi install --local /path/to/razr-pi/packages/razr-pi
pi list
```

The package stays in the cloned directory; Pi records a local reference to it. Loading is read-only and registers:

- `/razr-status`
- `/razr-context`
- `/razr-remember`
- `/razr-recall`
- `/razr-memory-inspect`

Reload Pi, invoke `/skill:razr`, and begin with read-only orientation. When you are ready, adopt the current repository as a separate action:

```sh
python3 -B /path/to/razr-pi/packages/razr-pi/scripts/adopt.py \
  --target "$PWD" \
  --work-root .razr/work
```

Inspect the target and command before running it. Adoption creates managed routing and delegates initialization to the Razr core.

## End-to-end developer path

The complete operator walkthrough is in the [package developer guide](packages/razr-pi/README.md). It provides the concrete path and command shapes for:

1. local Pi package installation and verification;
2. explicit repository adoption;
3. discovery and approval of an exact spec/executable pair;
4. declared execution and the first-task human checkpoint;
5. explicit candidate staging, commit, binding and Verify entry;
6. fresh independent Technical and Contract reviews;
7. primary audit, independent reproduction and supported core recording;
8. human-approved Review entry;
9. separately authorized non-force push and exact PR create/read-back; and
10. core PR recording and final human handoff.

The guide deliberately stops before merge or deployment. It also identifies every credential, spending, approval and remote-mutation boundary rather than treating package loading or a CLI flag as authorization.

## For Pi agents

If a developer points you here, guide the setup instead of silently changing their environment:

1. Read this README, [`AGENTS.md`](AGENTS.md), and the [package guide](packages/razr-pi/README.md).
2. Check `python3`, `git`, `node`, and `pi`. Report missing prerequisites; ask before any global installation, authentication, or credential work.
3. Confirm the intended project with `git rev-parse --show-toplevel`. Do not initialize or adopt a different repository implicitly.
4. If Razr-Pi is not present, propose the exact clone location and command. Wait for approval before network or filesystem changes.
5. Show the exact `pi install --local` command and package path before running it. Verify the result with `pi list`.
6. Start with read-only orientation. Explain that package loading is not repository adoption or permission to write.
7. Ask separately before running `packages/razr-pi/scripts/adopt.py`. Reconfirm the target Git root and work root, then read back the created routing and Razr state.
8. If the developer requests delivery, follow the [package developer guide](packages/razr-pi/README.md) and the progressively loaded Razr references. Preserve the first-task, Review-entry, remote-mutation and final PR-review human gates.

Stop on failed prerequisites, unexpected existing Razr files, symlinks in control paths, or ambiguous repository identity. Never infer approval, alter credentials, or merge, deploy, or publish automatically.

## Workflow model and current support

The experimental delivery workflow is intended to run from exploration through an open PR presented for final human review; treat it as acceptance-pending until the release evidence is complete:

1. Explicitly adopt a repository and create/refine an exact specification and executable.
2. Execute declared work with core-backed state, validation read-back and the first-task human checkpoint.
3. Bind one clean committed candidate and freeze neutral candidate-bound review inputs.
4. Launch fresh independent Technical and Contract Pi sessions with explicit model/budget authorization.
5. Primary-audit and record supported outcomes, then obtain explicit human approval before entering Review.
6. After separate remote authorization, push the exact reviewed head, create/read back the PR and record it through the core.
7. Stop at final human PR review. Merge and deployment remain human-owned.

The path is project/language/test-runner/model/branch independent within its explicit repository and safety constraints. Core commands, helper flags and hashes never bypass actual human gates.

Use `/razr-context` for bounded context and the Memory commands for experiential context. Updating the package does not rewrite project routing, and this breaking release provides no in-place upgrade. Previous users should use `packages/razr-pi/scripts/de-adopt.py`, manually review and apply its proposal, retain or separately archive the old data, and begin again in a clean repository.

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
- an optional bounded Herdr execution provider with durable attempt evidence;
- candidate-bound fresh Technical/Contract Pi review preparation, launch, audit and core recording; and
- source-bound GitHub snapshot/read/create plus core PR handoff.

## Current limitations

- The Pi command surface deliberately has no worker command; authorized orchestrators use the Herdr provider directly.
- Package loading is not execution, adoption, approval, or a sandbox.
- Memory secret filtering is heuristic and is not a confidentiality boundary.
- Context requires a clean exact candidate and rejects dirty, untracked, linked, or mutable source inputs.
- Herdr telemetry never proves workflow completion or review acceptance.
- GitHub reads are bounded and non-atomic; empty check or review collections do not prove execution or approval.
- Review launch and PR creation are explicit bounded operations, not autonomous factories; there are no automatic retries, repairs, compensation, merge, deployment, package publication or credential changes.
- Exact source scrutiny, independent review, and final human PR review remain mandatory delivery gates.

## Verification

```sh
python3 -B tests/check_public_tree.py --root .
python3 -B tests/run_public.py --phase tracer --require-pi
python3 -B tests/run_public.py --phase full --require-pi
```

These commands verify the documented public source and selected integration behavior. They do not authenticate human approval or authorize publication, merge, or deployment.
