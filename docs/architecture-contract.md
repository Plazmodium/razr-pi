# ADR: Razr-Pi architecture contract

- **Status:** accepted for M01 implementation planning
- **Scope:** M02–M05 only; this document does not implement those missions

## Decision

The checked-out repository is the primary project brain. Current user/campaign direction and the current checkout outrank memory; tests, configuration, Git history, approved Razr artifacts, docs, and finally Razr Memory follow in that order. Memory records experience and decision context, not a second copy of source. Obsidian, Project Brain MCP, a vault, vector database, background daemon, and synchronized component/session trees are not dependencies.

### Repository and Razr authority

The existing Python core remains the sole lifecycle authority: `packages/razr-pi/runtime/scripts/razr.py` owns `spec.md`, `executable.yaml`, `state.yaml`, `evidence.md`, legal transitions, generations, and validation. The adapter must call the core rather than reproduce its state machine. No Herdr, Pi extension, memory, or attempt record may hand-edit lifecycle state or infer approval/completion.

Git remains project-history memory and candidate identity. GitHub, accessed through `gh`, is the delivery/read-back boundary; a local flag or host status is never a PR/check/review result.

## Memory (M02)

Use one local, inspectable append-oriented JSONL file at `.razr/memory.jsonl`, with an optional derived `.razr/memory-index.json` cache that is disposable and never authoritative. `.razr/` is project-local and should be ignored by default; a future explicit policy may commit reviewed records for portability. Records use the target fields `id`, `kind`, `summary`, `scope`, `confidence`, `status`, `created_at`, `updated_at`, `evidence[]`, and optional `supersedes`/`related_ids`/`tags`.

Memory accepts experience only: observations, rationale, learnings, workarounds, failure patterns, environment quirks, navigation hints, preferences, and unresolved questions. Evidence references are structured paths/symbol hints, tests, commits, config, work items, or explicitly approved external artifacts. Writes are append-only; retirement/supersession appends a new record. Retrieval is bounded, relevance-scored or filtered, and must report stale evidence when referenced repository content changes. Secret-like values are rejected or redacted before append. Memory never overrides current repository evidence.

## Context engine (M03)

The public API is a command/tool surface, not a new workflow authority:

```text
razr context --objective <text> [--path <path> ...] [--limit <n>]
```

It returns a bounded, inspectable context packet containing inclusion reasons and source authority for selected objective, active approved Razr spec/executable/workflow state when relevant, affected source/tests/configuration, useful Git diff/history, and relevant non-stale Memory. It reports omissions caused by limits and stale/uncertain records. It must not load the whole repository or memory file, and it must use the existing core for workflow reads. The command may be exposed through the Pi extension later, but its implementation remains callable without Pi.

## Pi boundary (M05)

`packages/razr-pi` remains a real local Pi package with one primary skill at `packages/razr-pi/skills/razr/SKILL.md`. The skill progressively references package-local guidance; loading it supplies instructions and does not adopt a project or authorize writes. A TypeScript Pi extension is the integration surface only when executable commands/tools are justified. Its minimum coherent commands/tools are `razr status`, `razr context`, `razr remember`, `razr recall`, `razr memory inspect`, and later worker operations. These call adapter functions/core commands; they do not contain lifecycle transitions.

The existing Python surfaces remain compatibility shims during migration: `scripts/adopt.py` for explicit safe adoption, `scripts/primary.py` for read-only inspection, `scripts/contract-preflight.py` for contract diagnostics, and `scripts/named-regressions.py` for test observation. Their names and behavior remain until replacement callers and regression proof exist. The unchanged runtime core and its canonical templates are not retired. Historical/lab helpers, critic launchers, recovery, and PR-factory surfaces remain excluded and are not imported.

## Herdr provider (M04)

Detect standalone mode with the explicit `HERDR_ENV=1` signal; when present, read `HERDR_WORKSPACE_ID`, `HERDR_TAB_ID`, and `HERDR_PANE_ID` as available identity metadata without guessing. Outside Herdr, Razr-Pi operates normally and reports fanout unavailable. Inside Herdr, the provider owns terminals, persistent processes, topology, worktrees, worker lifecycle telemetry, and prompt/observe/collect/interrupt transport.

The provider-neutral interface is:

```text
probe(requirements) -> CapabilityReport
launch(request)      -> ExecutionHandle
inspect(handle)      -> ExecutionStatus
collect(handle)      -> ExecutionResult
interrupt(handle)    -> InterruptionResult
```

Requests bind work/node, observed generation and attempt ordinal, candidate commit/source digest, fresh neutral inputs, cwd, runtime/tool/environment policies, limits, and allowance identity. Handles are provider-scoped, not Razr node IDs. Status values are operational observations only. Herdr `working`, `blocked`, `idle`, `done`, or pane disappearance never becomes a Razr transition or completion result. The provider refuses unsupported guarantees rather than weakening the request.

## Attempt ledger

M04 owns an adapter-local append-only `.razr/attempts.jsonl` ledger, separate from core `state.yaml`. Each record binds `attempt_id`, work/node, observed core generation and ordinal, worker/agent and Herdr workspace/tab/pane identity when available, branch/worktree, allowance, timestamps, provider/session/process identity, classified outcome, and evidence references. Partial/unknown outcomes are retained; retries require a new identity and generation check. The ledger is operational evidence, not a second state machine and not a replacement for core transitions.

## Git/GitHub delivery boundary

Use Git directly for status, diff, ancestry, source digest, worktree isolation, and commit identity. Use `gh` for PR creation and read-back of actual base/head SHA, checks, reviews, and threads. Delivery is not claimed from a local readiness marker, process exit, or Herdr status. M07 owns later delivery implementation; M01 changes no delivery automation.

## Migration and parallel write boundaries

- **M02:** owns `.razr/memory.jsonl` format, memory policy, and memory tests; no context or Pi manifest changes.
- **M03:** owns context assembly/API and context tests; reads M02 through its public memory interface; no provider or package lifecycle changes.
- **M04:** owns Herdr detection, provider contract, attempt ledger, and provider tests; no core state schema changes.
- **M05:** owns Pi package/extension manifests, skill references, and adapter command registration; calls M02/M03/M04 interfaces and existing core shims, without embedding their state.

Shared interface changes are serialized and reviewed before parallel implementation. No mission may modify provenance-pinned core/templates or import excluded historical artifacts without a new approved contract.

## Non-goals

This contract does not implement memory, context, Herdr fanout, a TypeScript extension, a scheduler, deployment, npm publication, credential management, vector search, background summarization, automatic retries, or a replacement Razr state machine. It does not claim independent critique, human approval, clean-clone acceptance, or GitHub delivery until those later missions demonstrate them.
