# Razr-Pi local package

Razr-Pi integrates the Razr workflow with Pi. The package contains one instruction skill, one command extension, a provenance-pinned Python core, bounded context and Memory adapters, an optional Herdr execution provider, and a separate GitHub delivery helper.

A Pi skill supplies instructions. A Pi extension registers executable commands. Loading either makes resources available but does not adopt a repository, execute work, approve a specification, or authorize writes.

## Repository model

The checked-out repository is authoritative. Relevant project truth includes source, tests, types and schemas, configuration, build and deployment definitions, Git history and current changes, intentional documentation, and active Razr workflow artifacts.

Razr Memory stores bounded experiential context—rationale, recurring failures, debugging discoveries, environmental quirks, workarounds, navigation hints, and preferences. Repository evidence is authoritative; Memory helps interpret and navigate it.

## Install and load

Prerequisites are human-provided Python 3.11+, Git, Node, and Pi. Review this directory before granting Pi project trust.

```sh
pi install --local /reviewed/path/to/razr-pi/packages/razr-pi
pi list
```

Pi references the resolved local package path; it does not copy the package. Razr-Pi never runs this command automatically, installs dependencies, or changes global configuration.

Loading registers one `razr` skill and one extension. It must not create `razr.yaml`, `.razr`, or `AGENTS.md`.

## Adopt a repository

Adoption is explicit and separate from package loading:

```sh
python3 -B /reviewed/path/to/razr-pi/packages/razr-pi/scripts/adopt.py \
  --target "$PWD" \
  --work-root .razr/work
```

Inspect the command and target before running it. Adoption validates the repository root and control paths, preserves unrelated instructions, and delegates initialization to the pinned Razr core.

## Pi commands

The extension registers:

| Command | Purpose |
|---|---|
| `/razr-status <work_id>` | Read core-backed workflow status and validation state. |
| `/razr-context ...` | Build a bounded context packet for an objective. |
| `/razr-remember ...` | Append an evidence-linked Memory record. |
| `/razr-recall ...` | Retrieve bounded active Memory and identify stale records. |
| `/razr-memory-inspect` | Inspect the append-only Memory ledger. |

Status and context are read-only. Remember is an explicit append operation. There is no Pi worker command: authorized orchestration uses the Herdr provider directly, outside the command surface.

## Context engine

`adapter/context.py` selects relevant context from one clean candidate:

- tracked source, tests, and configuration from an immutable Git commit tree;
- bounded Git history;
- core-backed active workflow state; and
- repository-scoped Razr Memory at lower authority.

The result is deterministic, bounded, provenance-labelled, and digest-bound. Dirty, untracked, linked, private, or mutable source inputs are refused rather than silently included.

## Razr Memory

`adapter/memory.py` stores Memory in `.razr/memory.jsonl` as canonical append-only JSON records. Every operation derives and rechecks the live repository source identity. A supplied repository digest is only an expected identity.

Memory supports `remember`, `recall`, `inspect`, `retire`, and `supersede`. Records are bounded and evidence-linked. Foreign or changed evidence is omitted or labelled stale and cannot outrank repository evidence.

The ledger and sibling lock use restrictive local modes. The lock coordinates cooperative processes; it is not a hostile same-UID sandbox. Secret-like input filtering is heuristic and is not a confidentiality guarantee. Do not store credentials or sensitive secrets in Memory.

## Workflow authority

The package invokes `runtime/scripts/razr.py` through a bounded subprocess boundary rather than reimplementing lifecycle rules. The core alone owns:

- specifications and executable graphs;
- legal generation-fenced transitions;
- node progress and first-task verification;
- candidate identity;
- critic records; and
- delivery state.

Adapter output, hashes, model reports, process exit, and CLI messages are observations—not approval or lifecycle authority.

## Standalone Pi and Herdr

Ordinary status, context, and Memory operations work outside Herdr. Fanout reports `FANOUT_UNAVAILABLE` when `HERDR_ENV=1` and complete Herdr identity are unavailable.

Inside Herdr, the provider preserves separate caller and target workspace/tab/pane identities, verifies current workflow generation and candidate through the core, binds the requested worktree, and repeats a read-only fence immediately before launch.

The provider owns bounded transport and attempt evidence. Herdr owns workspaces, terminals, worktrees, and worker processes. Herdr observations such as `working`, `blocked`, `idle`, `done`, process exit, or pane disappearance never become Razr transitions or review verdicts.

Provider attempts use append-only STARTED/SETTLED evidence, permanent resource identities, bounded output, end-to-end deadlines, process-group cleanup, explicit interruption, and truthful `UNKNOWN` outcomes. Recovery is explicit; there is no automatic retry.

## GitHub delivery helper

`scripts/github-delivery.py` is a separately invoked closed-JSON helper, not a Pi command. It provides:

- `snapshot` for exact local repository/source identity;
- `read` for bounded PR observations; and
- separately authorized `create` for one exact candidate.

Requests bind repository, base/head branches, full base/head commits, and source digest. Reads are bounded and non-atomic. `requiredChecksStatus: "none"` means no required checks were reported; it does not prove tests ran. Provider collections may be explicitly incomplete.

The helper does not merge, deploy, force-push, delete branches, launch critics, mutate Razr workflow state, or infer approval. Creation can produce an uncertain remote side effect; recovery and compensation report uncertainty rather than claiming success.

## Helper surfaces

- `scripts/adopt.py` — explicit repository adoption.
- `scripts/primary.py` — read-only inspection of materialized work.
- `scripts/contract-preflight.py` — structural pair and evidence-authority checks; not semantic approval.
- `scripts/named-regressions.py` — bounded unittest observation collection.
- `scripts/pi-surface.py` — command-facing adapter boundary.
- `scripts/github-delivery.py` — source-bound GitHub operations.

## Verification

From the repository root:

```sh
python3 -B tests/check_public_tree.py --root .
python3 -B tests/run_public.py --phase tracer --require-pi
python3 -B tests/run_public.py --phase full --require-pi
```

See [testing](../../docs/testing.md) for focused commands, isolation rules, and proof limits.

## Limits

- Loading is not adoption, execution, approval, or sandboxing.
- Context requires a clean Git candidate.
- Memory is local evidence, not source truth or secret storage.
- The provider does not choose work, approve results, or control Razr lifecycle state.
- The GitHub helper is not a general PR factory.
- Fresh independent Technical and Contract review must target the exact candidate.
- Final human PR review remains mandatory before merge.
