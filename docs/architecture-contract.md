# ADR: Razr-Pi architecture contract

- **Status:** accepted for M01 implementation planning
- **Scope:** M02–M05 only; this document does not implement those missions

## 1. Authority and boundaries

The checked-out repository is the primary project brain. Current user/campaign direction and the current checkout outrank tests, configuration, Git history, approved Razr artifacts, docs, and finally Razr Memory. Memory stores experience and decision context, never a rewritten codebase and never a competing source of truth. Obsidian, Project Brain MCP, a vault, vector DB, background daemon, and synchronized component/session trees are not dependencies.

The existing Python core at `packages/razr-pi/runtime/scripts/razr.py` is the sole lifecycle authority. It owns `spec.md`, `executable.yaml`, `state.yaml`, `evidence.md`, legal transitions, generations, and validation. No adapter, Pi extension, provider, Memory record, or attempt ledger may hand-edit lifecycle state or infer approval/completion. Git owns project history and candidate identity. GitHub through `gh` is the delivery/read-back boundary.

## 2. Shared wire and identity rules

All M02–M05 interfaces use UTF-8 JSON objects with no unknown top-level fields, explicit `schema_version: 1`, and these common scalar rules:

- `id` values are non-empty ASCII strings matching `[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`.
- Times are UTC RFC 3339 strings with `Z` and second or millisecond precision.
- Digests are lowercase `sha256:<64 hex characters>` over canonical JSON: UTF-8, recursively sorted object keys, array order preserved, no insignificant whitespace, and no NaN/Infinity. The canonicalizer rejects duplicate keys, non-finite numbers, and ambiguous Unicode normalization; strings are retained as supplied.
- Paths are UTF-8 relative POSIX paths beneath the declared repository/worktree root after resolution; absolute paths, symlinks, `..`, and path escapes are rejected. Machine paths and secret values are never serialized into public records.
- Errors use one envelope: `{ "schema_version":1, "ok":false, "error": {"code": <enum>, "message": <string>, "retryable": <boolean>, "details": <object> } }`. Messages are diagnostic, not authority. A caller must not retry unless the operation explicitly says so.

Successful responses use `{ "schema_version":1, "ok":true, "request_id": <id>, "result": <object> }`; `request_id` is echoed and must be unique per call. A response with a mismatched request ID, schema, digest, or identity is invalid and is retained as an observation rather than converted to success.

## 3. Memory interface (M02)

Storage is exactly `.razr/memory.jsonl`, local-first and inspectable, with an optional disposable `.razr/memory-index.json` cache. M02 owns this file and its locking. A record is:

```json
{
  "schema_version":1, "id":"mem-...", "kind":"observation|rationale|learning|workaround|failure_pattern|environment_quirk|navigation_hint|preference|unresolved_question",
  "summary":"...", "scope":{"repository":"sha256:...", "paths":["src/x.py"]},
  "confidence":"low|medium|high", "status":"active|stale|retired|superseded",
  "created_at":"...Z", "updated_at":"...Z", "evidence":[{"type":"path|test|commit|config|work_item|external","ref":"...","digest":"sha256:..."}],
  "tags":["..."], "supersedes":null, "related_ids":[]
}
```

`supersedes` and `related_ids` are optional; omitted optional fields are not written as null. `summary`, tags, and evidence are bounded (M02 must reject oversized values). Secret-like content is rejected/redacted before append. A record becomes `stale` when its evidence digest no longer matches the current repository; stale Memory is shown as stale and cannot outrank source evidence. Retrieval returns `{records: [...], omitted_count, stale_ids, query_digest}` and is bounded by an explicit `limit` (default and maximum owned by M02).

Append integrity: M02 opens the file with create/exclusive mode as needed, takes an advisory exclusive `flock` on a sibling `.lock`, seeks to EOF, validates every prior line, writes exactly one complete JSON line with `O_APPEND`, calls `fsync`, and releases the lock. Each record has `prev_digest` and `record_digest`; `record_digest` covers the canonical record without `record_digest`, chaining from the fixed genesis digest `sha256:` plus 64 zeroes. Partial final lines, duplicate IDs, broken chains, invalid digests, and lock failures return `LEDGER_CORRUPT` or `LOCK_UNAVAILABLE` and never append. No compaction or in-place rewrite occurs in M02.

M02 owns `.razr/memory.jsonl`, its lock, record validation, secret policy, and recall. M03 consumes only the read/recall interface. M05 exposes it; M04 does not touch it.

## 4. Context interface (M03)

The canonical request is:

```json
{
  "schema_version":1, "request_id":"ctx-...", "objective":"...",
  "repository_root":".", "work_id":"optional-id", "paths":["src/x.py"],
  "include":{"workflow":true,"source":true,"tests":true,"config":true,"git":true,"memory":true},
  "limits":{"max_files":40,"max_bytes":200000,"max_memory_records":20},
  "observed":{"commit":"sha256:...","source_digest":"sha256:..."}
}
```

`work_id`, `paths`, and `observed` are optional; `include` must contain only the six named booleans and `limits` must be positive bounded integers. The response is:

```json
{
  "schema_version":1, "ok":true, "request_id":"ctx-...",
  "result":{"packet_digest":"sha256:...","authoritative_sources":[{"kind":"source|test|config|git|workflow|memory","ref":"...","digest":"sha256:...","reason":"..."}],
  "items":[{"kind":"file|git_diff|git_history|workflow|memory","ref":"...","digest":"sha256:...","bytes":123,"reason":"...","authority":"repository|workflow|memory"}],
  "omitted":[{"ref":"...","reason":"limit|irrelevant|stale|unsafe"}],"stale_memory_ids":[]}
}
```

Items are ordered deterministically by `kind`, `ref`, then digest. The packet contains bounded excerpts or references, never the entire repository/store. Current repository evidence outranks Memory; stale or uncertain Memory is labeled and cannot override it. `INVALID_REQUEST`, `PATH_ESCAPE`, `SOURCE_CHANGED`, `WORKFLOW_UNAVAILABLE`, `MEMORY_UNAVAILABLE`, `LIMIT_EXCEEDED`, and `INTERNAL` are failure codes. `PATH_ESCAPE`, `SOURCE_CHANGED`, and digest mismatch are non-retryable; `MEMORY_UNAVAILABLE` is retryable only when Memory was explicitly included, and the response must identify the omission.

M03 owns assembly, selection, ordering, packet digest, and omission semantics. It reads M02 through its stable recall response and invokes the core read-only boundary below; it does not write workflow state, Memory, or attempts. M02 has no context dependency.

## 5. Python core invocation boundary

M02–M05 invoke the core through a subprocess, never by importing `razr.py` into the extension or embedding a second copy. The adapter resolves the package-local runtime script and executes:

```text
<python3> -B packages/razr-pi/runtime/scripts/razr.py <command> ...
```

with the exact repository root as `cwd`, an allowlisted environment (`PATH`, locale, and explicitly required variables only), closed stdin unless the command contract requires otherwise, bounded stdout/stderr, and no shell. The adapter uses `status --json` for machine-readable status. For other commands, exit code `0` is only transport success: the adapter reads the resulting files and calls `validate <work_id>` (or `validate --all`) through the same core, then checks the expected generation/digest. Human stdout is diagnostic only. Nonzero exit, timeout, signal, malformed JSON, unexpected files, or failed read-back is failure; no automatic retry.

Before any invocation, the adapter verifies the runtime provenance declaration and the six pinned core/template SHA-256 values in `runtime/provenance.json`, verifies the package helper pins and exact repository root, and records `core_identity` and `source_digest` in the operation result. A changed or missing provenance file returns `PROVENANCE_MISMATCH` before process launch. These checks establish byte identity, not execution or approval. The core remains the only validator: adapter checks are transport, path, provenance, and response-integrity checks, never contract or lifecycle reimplementation.

## 6. Pi package and extension (M05)

Decision: implement one TypeScript extension entrypoint, not a custom provider, MCP server, or second CLI. The exact future package shape is:

```text
packages/razr-pi/extensions/razr.ts
packages/razr-pi/package.json   # add pi.extensions and peerDependencies
```

The manifest changes from the current skill-only declaration to:

```json
{
  "pi": {"extensions":["./extensions/razr.ts"], "skills":["./skills"]},
  "peerDependencies": {"@earendil-works/pi-coding-agent":"*", "@earendil-works/pi-ai":"*", "typebox":"*"}
}
```

The extension exports the installed Pi API factory `export default function(pi: ExtensionAPI)`. It registers read/command surfaces with `pi.registerCommand(name, {description, handler: async (args, ctx) => ...})`; model-callable read operations use `pi.registerTool({name,label,description,parameters: Type.Object(...), outputSchema, annotations:{readOnlyHint:true}, execute(...)})` imported from `@earendil-works/pi-coding-agent` and `@earendil-works/pi-ai`/`typebox` as shown by the installed `hello.ts` example. Commands/tools call the M02/M03/M04 adapters and core subprocess boundary; they do not mutate lifecycle state directly. Use `ctx.cwd`, `ctx.mode`, `ctx.ui.notify`, and `ctx.signal`; guard TUI-only behavior with `ctx.mode === "tui"`. Persistent session display metadata, if needed, uses `pi.appendEntry`, never `.razr` workflow files.

Registered commands are exactly `/razr-status`, `/razr-context`, `/razr-remember`, `/razr-recall`, and `/razr-memory-inspect` in M05. Worker operations are not registered until M04's provider interface and M05's command review are complete. The extension is loaded by normal Pi package discovery; loading does not adopt, initialize, or write a project. M05 owns only the manifest, entrypoint, registration, argument parsing, and Pi smoke test. It does not own Memory/context/provider implementation.

## 7. Herdr provider (M04)

Runtime detection uses `HERDR_ENV=1`; when true, capture `HERDR_WORKSPACE_ID`, `HERDR_TAB_ID`, and `HERDR_PANE_ID` only when present. Outside Herdr, operations return standalone mode and `FANOUT_UNAVAILABLE`; inside Herdr, the provider owns host transport and telemetry, not Razr authority.

The exact provider request is:

```json
{
  "schema_version":1,"request_id":"exec-...","attempt_id":"att-...","role":"technical|contract|worker",
  "work":{"work_id":"...","node_id":"...","generation":12,"ordinal":2},
  "candidate":{"repository":"...","commit":"sha256:...","source_digest":"sha256:..."},
  "cwd":".","fresh_context":true,"neutral_input_digest":"sha256:...",
  "environment_policy_digest":"sha256:...","tool_policy_digest":"sha256:...",
  "required_capabilities":["fresh_context","working_directory","bounded_time","bounded_output","runtime_identity"],
  "runtime":{"provider":"...","model":"...","thinking":"medium"},
  "limits":{"deadline_ms":600000,"max_output_bytes":1048576},"allowance_id":"allow-..."
}
```

The provider response schemas are:

```json
{"schema_version":1,"ok":true,"request_id":"exec-...","result":{"handle":{"provider_id":"herdr","handle_id":"...","request_digest":"sha256:...","attempt_id":"att-..."},"capability_report_digest":"sha256:..."}}
{"schema_version":1,"ok":true,"request_id":"exec-...","result":{"status":"STARTING|ACTIVE|SETTLED|STOPPING|TERMINATED|UNKNOWN","observed_at":"...Z","source":"herdr|pi|process","workspace_id":"...","pane_id":"..."}}
{"schema_version":1,"ok":true,"request_id":"exec-...","result":{"attempt_id":"att-...","termination":"SUCCEEDED|FAILED|INTERRUPTED|TIMED_OUT|UNKNOWN","runtime_identity":"...","artifact_refs":[{"kind":"stdout|stderr|session|event","digest":"sha256:...","bytes":123}],"exit_code":0,"cleanup":"confirmed|uncertain|not_applicable"}}
```

The provider operation schemas are also fixed. `probe` accepts `{schema_version, request_id, required_capabilities, runtime}` and returns `{schema_version, ok:true, request_id, result:{provider_id, runtime_identity, capabilities:[<capability enum>], report_digest}}`; `capabilities` may contain only `fresh_context`, `saved_session`, `neutral_instructions`, `working_directory`, `environment_policy`, `bounded_time`, `bounded_output`, `stream_capture`, `interrupt`, `tool_policy_observation`, and `runtime_identity`. `launch` accepts the request above and returns the handle response above. `inspect` accepts `{schema_version, request_id, handle}` and returns the status response above. `collect` accepts `{schema_version, request_id, handle}` and returns the result response above. `interrupt` accepts `{schema_version, request_id, handle, reason:"cancel|timeout|policy_violation"}` and returns `{schema_version:1,ok:true,request_id,result:{attempt_id,requested:true,observed:"SETTLED|STOPPING|TERMINATED|UNKNOWN",cleanup:"confirmed|uncertain|not_applicable"}}`.

Provider failure codes are exactly `INVALID_REQUEST`, `UNSUPPORTED_CAPABILITY`, `IDENTITY_MISMATCH`, `DIGEST_MISMATCH`, `STALE_GENERATION`, `CANDIDATE_CHANGED`, `RESOURCE_REUSED`, `ALLOWANCE_REUSED`, `FRESH_CONTEXT_UNAVAILABLE`, `LAUNCH_FAILED`, `TIMEOUT`, `OUTPUT_LIMIT`, `INTERRUPT_FAILED`, `ARTIFACT_UNAVAILABLE`, `CLEANUP_UNKNOWN`, `PROVIDER_UNAVAILABLE`, and `INTERNAL`. `INVALID_REQUEST`, identity/digest mismatch, stale generation, candidate change, resource/allowance reuse, unsupported capability, and fresh-context failure are non-retryable for that request. `PROVIDER_UNAVAILABLE`, launch failure, and timeout may be retried only by a new explicitly authorized attempt with a new `attempt_id`, allowance assertion, request digest, and output/session root. Output clipping, interruption failure, missing artifacts, and cleanup uncertainty produce an incomplete result and are never silently retried or classified as success.

`inspect` is read-only. `collect` preserves partial artifacts. Missing identity, candidate/request digest mismatch, unsupported capability, fallback, retry, reused handle/output root, timeout, output clipping, pane loss, or cleanup uncertainty returns a non-success classification; `UNKNOWN` is never converted to failure or success automatically. `interrupt` is idempotent as a request and reports requested versus observed cleanup. `Herdr` status is never a Razr transition.

## 8. Attempt ledger (M04)

M04 owns `.razr/attempts.jsonl` and `.razr/attempts.lock`, separate from `state.yaml`. Each line has exactly:

```json
{
  "schema_version":1,"attempt_id":"att-...","work_id":"...","node_id":"...",
  "observed_generation":12,"observed_ordinal":2,"worker_id":"...",
  "herdr":{"workspace_id":"...","tab_id":"...","pane_id":"..."},
  "branch":"...","worktree":"...","allowance_id":"...","provider_id":"herdr",
  "started_at":"...Z","finished_at":"...Z","outcome":"SUCCEEDED|FAILED|INTERRUPTED|TIMED_OUT|UNKNOWN",
  "request_digest":"sha256:...","evidence_refs":[{"digest":"sha256:...","kind":"..."}],
  "prev_digest":"sha256:...","record_digest":"sha256:..."
}
```

`herdr`, `finished_at`, and `evidence_refs` are omitted until known; an attempt is immutable thereafter. Append uses the same sibling-lock `flock`, `O_APPEND`, one complete line, `fsync`, and hash-chain verification as Memory. A writer validates the full chain while holding the lock; duplicate `attempt_id`, reused allowance/session, broken `prev_digest`, partial line, or failed fsync returns `LEDGER_CORRUPT`, `DUPLICATE_ATTEMPT`, `ALLOWANCE_REUSED`, or `LOCK_UNAVAILABLE` and does not append. There is no in-place repair; recovery records `UNKNOWN` with a new attempt only under explicit authority. The ledger references core generation/state but cannot change it.

## 9. Legacy classification and migration ownership

- **Permanent:** `runtime/scripts/razr.py` and its canonical templates; `scripts/adopt.py` safe adoption; `scripts/primary.py` read-only inspection; Git/`gh` as history and delivery boundaries; the single `skills/razr/SKILL.md` instruction surface. These remain supported and are owned by the core/adoption compatibility owner.
- **Temporary compatibility shims:** `scripts/contract-preflight.py` and `scripts/named-regressions.py`. Owner: M05/package maintainers. Removal gate: M02–M05 replacement callers, equivalent refusal/identity tests, documented migration notes, and one clean-source full-suite run on the replacement path; no removal before a separately reviewed campaign commit.
- **Excluded/retired:** historical workshop/lab helpers, critic launchers, recovery dispatchers, scheduler/fleet code, PR factory, Project Brain/Obsidian integrations, and any direct lifecycle-state writer. They are not imported, packaged, or compatibility targets.

M02 owns Memory only; M03 owns context only; M04 owns Herdr/provider and attempts only; M05 owns Pi packaging/registration only. Shared JSON rules above are frozen for these missions. Interface changes require an ADR revision and serialized review before parallel implementation.

## 10. Non-goals

This M01 contract does not implement Memory, context, Herdr fanout, the extension, a scheduler, deployment, npm publication, credential management, vector search, background summarization, automatic retries, independent critique, human approval, clean-clone acceptance, or GitHub delivery automation. It does not replace the Razr workflow state machine.
