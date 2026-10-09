# Razr for Pi: public bootstrap

Pi concept: a skill supplies instructions, while an extension registers executable commands. Loading either resource does not adopt a project or authorize writes. The extension is a thin dispatcher to the existing core and Memory adapter; it does not reimplement lifecycle authority, workers, authentication, or automatic adoption.

The package registers `/razr-status`, `/razr-context`, `/razr-remember`, `/razr-recall`, and `/razr-memory-inspect`. `/razr-status <work_id>` requires an adopted, materialized work item and returns the frozen core status/validate/raw-state projection; it also reports M04 fanout capability availability without launching workers. Status/context are read-only; context delegates bounded selection to the M03 `ContextEngine`, while remember is the one explicit append operation and requires the M02 Memory adapter's closed JSON request. There is deliberately no worker command: live fanout remains deferred.

Review [the skill](skills/razr/SKILL.md) and [managed routing](runtime/AGENTS.project-razr.md) before loading. This private package provides the primary skill plus a thin command extension for explicit inspection/adoption workflows. Loading does not authorize writes or adopt a project. Do not install automatically.

The package resolves helpers and runtime internally. The core and five canonical templates are unchanged snapshots identified by [provenance](runtime/provenance.json). Provenance is not execution proof or licensing clearance.

Stable adapter surfaces:

- `adapter/memory.py`: deterministic repository-local append-only Memory for M03/M05; it stores experience only and never owns workflow state.
- `adapter/context.py`: read-only M03 context selector. `ContextEngine.context(request)` returns the closed bounded packet response; repository files, tests, config, Git and core readback are selected deterministically, while Memory is labelled lower-priority experience.

Context packets contain bounded UTF-8 excerpts or metadata-only references, repository-relative provenance, deterministic ordering and a canonical packet digest. The selector never writes workflow state or Memory and refuses unsafe paths, invalid limits and unavailable explicitly requested Memory/workflow boundaries.

The closed Memory surface is `MemoryStore(root).remember(kind, summary, repository, paths=[], confidence="medium", evidence=[], tags=[], related_ids=[], record_id=None)`, returning one closed record with `schema_version`, stable `id`, `kind`, `summary`, `scope`, `confidence`, `status`, UTC `created_at`/`updated_at`, `evidence`, `prev_digest`, `record_digest`, and optional `tags`/`related_ids`. Reads are `recall(query="", paths=[], limit=20)` → `{records, omitted_count, stale_ids, query_digest}` and `inspect()` → records. `retire(id)` and `supersede(id, summary, evidence=None, tags=None, confidence=None)` append immutable transitions; no transition can change schema, kind, scope, or relation authority. Inputs and stored bytes use strict canonical JSON, bounded values, and repository-relative POSIX evidence refs.

Supported helper surfaces:

- `scripts/adopt.py`: explicit exact-root preflight, then unchanged core initialization.
- `scripts/primary.py`: read-only inspection of already materialized work; refuses discovery-only items. No execution or approval authority.
- `scripts/contract-preflight.py`: read-only pair identities and a narrow evidence-authority conflict diagnostic. Not semantic completeness or human approval.
- `scripts/named-regressions.py`: actual unittest observation collection. Its raw output is private, not public source or independent critique.

Do not infer supported live task, candidate, critique, recovery or delivery automation from commands present in the unchanged core. No live reviewer or PR factory ships here. The Herdr provider is a bounded, capability-probed host adapter: outside `HERDR_ENV=1` it is cleanly unavailable, and inside Herdr it uses documented argv operations for worktree/agent/pane telemetry only. Herdr state and attempt evidence never authorize or mutate Razr workflow state. The narrower project contract/routing always applies. Exact approval, first-task human inspection, source identity, fresh independent dual critique and final human PR review remain required for later work.

[Public testing](../../docs/testing.md) describes no-model loader and adoption proof, prerequisites and remaining gates. No model, auth or client runtime is required to inspect resources.
