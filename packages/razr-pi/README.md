# Razr for Pi: local package

Pi concept: a skill supplies instructions, while an extension registers executable commands. Loading either resource does not adopt a project, execute work, or authorize writes. The extension is a thin dispatcher to the existing core, context engine, and Memory adapter; it does not reimplement lifecycle authority, workers, authentication, or automatic adoption.

Razr-Pi is designed for an ordinary repository: the checkout is the project brain. Project Brain, Obsidian, MCP vaults, and separate architecture/component/session documentation trees are not dependencies. The supported guidance is idea or conversation → spec convergence → executable → build → verify → delivery. Source, tests, configuration, and Git remain durable project truth; Memory stores bounded experience and workflow artifacts retain intent/provenance as needed.

## Review, declare, and load locally

Review this directory before granting Pi project trust. With human-provided Pi, declare the package in a project using normal local-package behavior:

```sh
pi install --local /reviewed/path/to/razr-pi/packages/razr-pi
pi list
```

A local package is loaded from its resolved path and is not copied. Razr-Pi never runs this command, installs dependencies, or changes global configuration. Loading registers exactly one `razr` skill and one extension; it must not create `razr.yaml`, `.razr`, or `AGENTS.md`. Explicit adoption remains separate:

```sh
python3 -B /reviewed/path/to/razr-pi/packages/razr-pi/scripts/adopt.py --target "$PWD" --work-root .razr/work
```

Inspect the command and target before running it. Adoption checks the exact root and control paths, preserves unrelated instructions, and delegates initialization to the pinned core.

## Commands

The package registers `/razr-status`, `/razr-context`, `/razr-remember`, `/razr-recall`, and `/razr-memory-inspect`. `/razr-status <work_id>` requires an adopted, materialized work item and returns the frozen core status/validate/raw-state projection; it also reports M04 fanout capability availability without launching workers. Status/context are read-only; context delegates bounded selection to the M03 `ContextEngine`, while remember is the one explicit append operation and requires the M02 Memory adapter's closed JSON request. Recall is bounded and labels stale or foreign evidence rather than allowing it to override repository truth. There is deliberately no worker command: authorized callers use the bounded Herdr provider directly, while dispatch authority remains outside the Pi command surface.

Review [the skill](skills/razr/SKILL.md) and [managed routing](runtime/AGENTS.project-razr.md) before loading. This local package provides the primary skill plus a thin command extension for explicit inspection/adoption workflows. Loading does not authorize writes or adopt a project. Do not install automatically. Existing legacy Project Brain/Obsidian/MCP-vault or workshop hooks may be migrated or ignored explicitly; they are never silently required or treated as workflow authority.

The package resolves helpers and runtime internally. Status constructs the frozen `CoreRequest` using the package-internal identity primitives already shared with M03 (`_git_head` and `_source_digest`); it does not duplicate digest logic or broaden the frozen protocol. The Python core and five canonical templates are pinned by [provenance](runtime/provenance.json); M09 narrowly hardens the core's exact PR/candidate/critic commit gate while preserving it as sole lifecycle authority. Provenance is not execution proof or licensing clearance.

Stable adapter surfaces:

- `adapter/memory.py`: deterministic repository-local append-only Memory for M03/M05; it stores experience only and never owns workflow state. Every operation derives live core source identity; a supplied repository digest is an expectation, never authority. Recall is scoped to that identity, total processing is capped, storage/lock modes are private, and secret-like rejection is explicitly heuristic rather than a confidentiality guarantee. The sibling lock serializes cooperative processes; post-flock identity checks detect feasible replacements but do not make same-UID hostile writers race-proof or retroactively undo a completed append.
- `adapter/context.py`: read-only M03 context selector. `ContextEngine.context(request)` returns the closed bounded packet response; tracked file bytes and metadata come from one content-addressed Git commit tree rather than mutable worktree paths, while clean-checkout fences reject dirty/untracked input and foreign/stale Memory is omitted or labelled lower-priority experience.

Context packets contain bounded UTF-8 excerpts or metadata-only references, repository-relative provenance, deterministic ordering and a canonical packet digest. The selector never writes workflow state or Memory and refuses unsafe paths, invalid limits and unavailable explicitly requested Memory/workflow boundaries.

The closed Memory surface is `MemoryStore(root, repository=<expected current source digest>).remember(kind, summary, repository, paths=[], confidence="medium", evidence=[], tags=[], related_ids=[], record_id=None)`; the store independently derives and rechecks live core source identity for inspect, recall, remember, retire and supersede, returning one closed record with `schema_version`, stable `id`, `kind`, `summary`, `scope`, `confidence`, `status`, UTC `created_at`/`updated_at`, `evidence`, `prev_digest`, `record_digest`, and optional `tags`/`related_ids`. Reads are `recall(query="", paths=[], limit=20)` → `{records, omitted_count, stale_ids, query_digest}` and `inspect()` → records. `retire(id)` and `supersede(id, summary, evidence=None, tags=None, confidence=None)` append immutable transitions; no transition can change schema, kind, scope, or relation authority. Inputs and stored bytes use strict canonical JSON, bounded values, and repository-relative POSIX evidence refs.

Supported helper surfaces:

- `scripts/adopt.py`: explicit exact-root preflight, then unchanged core initialization.
- `scripts/primary.py`: read-only inspection of already materialized work; refuses discovery-only items. No execution or approval authority.
- `scripts/contract-preflight.py`: read-only pair identities and a narrow evidence-authority conflict diagnostic. Not semantic completeness or human approval.
- `scripts/named-regressions.py`: actual unittest observation collection. Its raw output is private, not public source or independent critique.

## Herdr and standalone behavior

Outside `HERDR_ENV=1`, all ordinary package commands continue to work and fanout reports `FANOUT_UNAVAILABLE`. Inside Herdr, complete caller identity is mandatory; the provider never guesses workspace, tab, or pane IDs. Herdr `working`, `blocked`, `idle`, `done`, pane loss, or process exit is telemetry only and never completes or reviews a Razr node.

Do not infer general task, candidate, critique, recovery or delivery automation from commands present in the provenance-pinned core. No general reviewer dispatcher or PR factory ships here. The Herdr provider is a bounded, capability-probed host adapter: outside `HERDR_ENV=1` it is cleanly unavailable, and inside Herdr it preserves separate closed `caller` and `target` identities, verifies the caller against its environment, re-reads current generation/node ordinal/candidate through the core before opening/binding the target worktree or making any dispatch mutation, then applies a read-only final candidate fence immediately before launch. Pre-START evidence staging is transactional; STARTED is the durable attempt/resource-ID claim, and append-only private path claims materialize only afterward with crash reconciliation and no chronology deletion. Durable starts require complete integrity-bound root-claim coverage: missing state or missing, truncated, or replaced claim history refuses before a new STARTED, except that an exact surviving prepared record whose complete canonical-byte digest was committed in STARTED may finish its own unmatched-STARTED crash window. Root storage is checked before reconciliation; stale prepared state for a SETTLED attempt cannot recreate missing history. Ownership marker absence/read/create uses pinned root descriptors and `O_NOFOLLOW`; linked/replaced/non-private/non-single-link markers refuse before claim mutation. The attempt ledger is independently bound to an atomic inode/count/chain-head state; an exact fsynced append intent is the only authority to recover first creation or one state-lagging append, so missing/replaced/truncated/reset history never becomes an empty ledger. Its subprocess boundary streams bounded output, owns process groups, uses one end-to-end deadline, rejects linked executables, and passes explicit fresh-context flags. Herdr state and attempt evidence never authorize or mutate Razr workflow state. Comments explain non-obvious local intent or invariants only; tests carry behavioral memory, and Git plus Razr Memory carry historical context. The narrower project contract/routing always applies. Exact approval, first-task human inspection, source identity, fresh independent dual critique and final human PR review remain required for later work.

## Shipped M07 GitHub delivery helper

`scripts/github-delivery.py` is a separately invoked closed-JSON helper, not a Pi command and not a PR factory. It exposes `snapshot` and read-only `read`, plus one explicitly authorized `create` operation. Create/read requests require exact repository identity, base/head branch names, full base/head SHAs, and the candidate source digest; the helper performs an authentication preflight without returning credentials or auth stderr. It is intended for authenticated GitHub/private-repository observations through host-pinned `gh` operations.

The read result is a bounded, non-atomic observation. `requiredChecksStatus: "none"` means no required checks were reported; it does not prove tests ran or passed. Review/comment/request/status-rollup collections from the bounded provider response are explicitly marked incomplete because `gh pr view` does not provide collection page information; review threads are bounded and paginated. Candidate movement, collection changes, malformed provider data, and unsupported enums refuse.

Create has explicit authority requirements and may report `created-but-unverified`, compensation unavailable, or compensation uncertain after a transport/output/recovery failure. Compensation closes only a candidate independently bound to the verified repository, PR URL/number, attempted base/head branch names, and base/head OIDs, then confirms `CLOSED`. No helper operation merges, deploys, force-pushes, deletes branches, mutates Razr lifecycle state, launches critics, authenticates approval, or claims final human PR review. The final human PR review gate remains mandatory.

## Limits

Memory files and attempt ledgers are private local evidence, not source truth or a confidentiality sandbox. Secret-like filtering is heuristic. Context requires a clean candidate and rejects dirty, untracked, linked, or mutable source inputs. Provider recovery is narrow and explicit; no implicit retry is available. GitHub reads are bounded and non-atomic, and a `none` check status does not prove execution. Exact approval, first-task checkpoint, candidate/excluded-input scrutiny, fresh independent Technical and Contract critique, and final human PR review remain outside package loading.

[Public testing](../../docs/testing.md) describes no-model loader/adoption proof, focused context/Memory and Herdr mechanics, the `test_github_delivery` scope, prerequisites, and limits. No model, auth client, or credential access is required to inspect package resources.
