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
- Persistent records (Memory, context packet contents/refs, and attempt ledger) contain only UTF-8 repo-relative POSIX refs beneath the declared repository root; absolute paths, symlinks, `..`, and path escapes are rejected. Ephemeral operational requests may contain canonical absolute paths for `cwd`, `worktree`, package/runtime files, and private output/session roots; these paths are never persisted in Memory/ledger records, request packets, public responses, or evidence refs. Machine paths and secret values are never serialized into public records.
- Errors use one envelope: `{ "schema_version":1, "ok":false, "error": {"code": <enum>, "message": <string>, "retryable": <boolean>, "details": <object> } }`. Messages are diagnostic, not authority. A caller must not retry unless the operation explicitly says so.

Successful responses use `{ "schema_version":1, "ok":true, "request_id": <id>, "result": <object> }`; `request_id` is echoed and must be unique per call. A response with a mismatched request ID, schema, digest, or identity is invalid and is retained as an observation rather than converted to success.

### Separate digest domains

These domains are not interchangeable, even when they use SHA-256:

| Domain | Exact input and encoding | Representation and use |
|---|---|---|
| Raw-file digest | Exact bytes read from one file; no newline/BOM normalization | `sha256:<64 hex>`; used for evidence/file identity |
| Canonical-JSON digest | Canonical JSON UTF-8: recursively sorted object keys, preserved array order, no insignificant whitespace, duplicate-key/non-finite-number rejection | `sha256:<64 hex>`; used for protocol packets, Memory/attempt records, and `record_digest` |
| Core `source_digest` | Existing `razr.py::source_tree_digest`: bytes concatenating each `git ls-tree -r -z --full-tree <commit>` entry as `metadata + b"\\t" + raw_path + b"\\0"`, excluding `razr.yaml`, `AGENTS.md`, and the configured work-root prefix | `sha256:<64 hex>`; identifies the reviewed Git tree, not a file or JSON document |
| Executable digest | Existing `razr.py::executable_digest`: `json.dumps(executable, sort_keys=True, separators=(",", ":")).encode("utf-8")` then SHA-256 | `sha256:<64 hex>`; identifies `executable.yaml`'s parsed JSON object under the core's existing serialization |
| Git object ID | Existing Git commit object identity, resolved and verified by `git cat-file -e <id>^{commit}` | exactly 40 lowercase hexadecimal characters matching `FULL_SHA_RE`; never prefixed with `sha256:` |

`candidate.commit` is therefore an existing full Git commit object ID such as `0123456789abcdef0123456789abcdef01234567`; `candidate.source_digest` is a separate `sha256:` tree digest. In provider requests, `candidate.repository`, `candidate.branch`, `candidate.commit`, and `candidate.source_digest` are all required. A candidate request is invalid if either domain is missing, malformed, or used in the other field.

## 3. Memory interface (M02)

Storage is exactly `.razr/memory.jsonl`, local-first and inspectable, with an optional disposable `.razr/memory-index.json` cache. M02 owns this file and its locking. A record is:

```json
{
  "schema_version":1, "id":"mem-...", "kind":"observation|rationale|learning|workaround|failure_pattern|environment_quirk|navigation_hint|preference|unresolved_question",
  "summary":"...", "scope":{"repository":"sha256:...", "paths":["src/x.py"]},
  "confidence":"low|medium|high", "status":"active|stale|retired|superseded",
  "created_at":"...Z", "updated_at":"...Z", "evidence":[{"type":"path|test|commit|config|work_item|external","ref":"...","digest_domain":"raw_file","digest":"sha256:..."}],
  "tags":["..."], "prev_digest":"sha256:...", "record_digest":"sha256:..."
}
```

Memory record closure is normative: required fields are `schema_version`, `id`, `kind`, `summary`, `scope`, `confidence`, `status`, `created_at`, `updated_at`, `evidence`, `prev_digest`, and `record_digest`; optional fields are `tags`, `supersedes`, and `related_ids`; no other fields are allowed. `scope.repository` and `scope.paths` are required. `evidence[].type`, `evidence[].ref`, `evidence[].digest_domain`, and `evidence[].digest` are required; `digest_domain` must match the digest-domain table and `git_object` values are 40-hex IDs rather than `sha256:` strings. Optional fields are omitted when absent, never emitted as `null`; `null` is forbidden in records. `summary`, tags, and evidence are bounded (M02 must reject oversized values). Secret-like content is rejected/redacted before append. A record becomes `stale` when its evidence digest no longer matches the current repository; stale Memory is shown as stale and cannot outrank source evidence. Retrieval returns `{records: [...], omitted_count, stale_ids, query_digest}` and is bounded by an explicit `limit` (default and maximum owned by M02).

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
  "observed":{"commit":"0123456789abcdef0123456789abcdef01234567","source_digest":"sha256:..."}
}
```

`work_id`, `paths`, and `observed` are optional; `include` must contain only the six named booleans and `limits` must be positive bounded integers. The closed response is:

```json
{
  "schema_version":1, "ok":true, "request_id":"ctx-...",
  "result":{"packet_digest":"sha256:...","authoritative_sources":[{"kind":"source|test|config|git|workflow|memory","ref":"...","digest_domain":"raw_file|canonical_json|core_source|executable|git_object","digest":"sha256:...","reason":"..."}],
  "items":[{"kind":"file|git_diff|git_history|workflow|memory","ref":"...","digest_domain":"raw_file|canonical_json|core_source|executable|git_object","digest":"sha256:...","bytes":123,"reason":"...","authority":"repository|workflow|memory","content_utf8":"bounded text"}],
  "omitted":[{"ref":"...","reason":"limit|irrelevant|stale|unsafe"}],"stale_memory_ids":[]}
}
```

Context request required fields are `schema_version`, `request_id`, `objective`, `repository_root`, `include`, and `limits`; optional fields are `work_id`, `paths`, and `observed`; no other fields are allowed. `observed.commit` is required if `observed` is present and is a 40-hex Git object ID; `observed.source_digest` is required if `observed` is present and is a core `sha256:` source digest. `null` is forbidden in all context requests and responses. Context response required fields are `schema_version`, `ok`, `request_id`, and `result`; result required fields are `packet_digest`, `authoritative_sources`, `items`, `omitted`, and `stale_memory_ids`; no other fields are allowed. Source entries require `kind`, `ref`, `digest_domain`, `digest`, and `reason`. Item entries require `kind`, `ref`, `digest_domain`, `digest`, `bytes`, `reason`, and `authority`, plus exactly one of `content_utf8` or `metadata`; `metadata` is an object with required `type` and `ref` and optional `note`, and means no content was included. Omission entries require `ref` and `reason`. Optional request objects/fields are omitted, never null; `null` is forbidden throughout the context protocol. Bounded content is actual UTF-8 text, and `bytes` is its UTF-8 byte length; metadata-only items use `bytes:0`.

`packet_digest` is the canonical-JSON digest of exactly `{ "schema_version":1, "request_id":..., "authoritative_sources":..., "items":..., "omitted":..., "stale_memory_ids":... }` before adding `packet_digest`; it therefore covers every included content byte, every metadata reference, every source digest, and every omission/stale marker. Items are sorted deterministically by `kind`, `ref`, then digest. The packet contains bounded excerpts or explicit metadata-only references, never the entire repository/store. Current repository evidence outranks Memory; stale or uncertain Memory is labeled and cannot override it. `INVALID_REQUEST`, `PATH_ESCAPE`, `SOURCE_CHANGED`, `WORKFLOW_UNAVAILABLE`, `MEMORY_UNAVAILABLE`, `LIMIT_EXCEEDED`, and `INTERNAL` are failure codes. `PATH_ESCAPE`, `SOURCE_CHANGED`, and digest mismatch are non-retryable; `MEMORY_UNAVAILABLE` is retryable only when Memory was explicitly included, and the response must identify the omission.

M03 owns assembly, selection, ordering, packet digest, and omission semantics. It reads M02 through its stable recall response and invokes the core read-only boundary below; it does not write workflow state, Memory, or attempts. M02 has no context dependency.

## 5. Python core invocation boundary

M02–M05 invoke the core through a subprocess, never by importing `razr.py` into the extension or embedding a second copy. The adapter resolves the package-local runtime script and executes:

```text
<python3> -B packages/razr-pi/runtime/scripts/razr.py <command> ...
```

with the exact repository root as `cwd`, an allowlisted environment (`PATH`, locale, and explicitly required variables only), closed stdin unless the command contract requires otherwise, bounded stdout/stderr, and no shell. The adapter uses `status --json` for machine-readable status. For other commands, exit code `0` is only transport success: the adapter reads the resulting files and calls `validate <work_id>` (or `validate --all`) through the same core, then checks the expected generation/digest. Human stdout is diagnostic only. Nonzero exit, timeout, signal, malformed JSON, unexpected files, or failed read-back is failure; no automatic retry.

Before any invocation, the adapter verifies the runtime provenance declaration and the six pinned core/template SHA-256 values in `runtime/provenance.json`, verifies the package helper pins and exact repository root, and records `core_identity` and `source_digest` in the operation result. A changed or missing provenance file returns `PROVENANCE_MISMATCH` before process launch. These checks establish byte identity, not execution or approval. The core remains the only validator: adapter checks are transport, path, provenance, and response-integrity checks, never contract or lifecycle reimplementation.

The frozen work read-back sequence is: (1) invoke `status <work_id> --json`; accept only a JSON array of row objects with exactly `id`, `stage`, `status`, `depth`, `priority`, `approved_at`, `candidate`, and `pull_request`; (2) invoke `validate <work_id>`; accept only exit code 0 and a `PASS <work_id>` line, with no inferred state transition; (3) through `core_subprocess.py`'s declared read-back, read `work_root/<work_id>/state.yaml` only as core-produced raw bytes, recording its raw-file digest and provenance identity. The typed projection is `WorkflowRead { work_id, stage, status, generation, node_id, node_status, node_attempt, node_result_ref, candidate_repository, candidate_branch, candidate_commit, candidate_source_digest, status_row }`. Exact status JSON mapping is `row.id -> work_id`, `row.stage -> stage`, `row.status -> status`, `row.depth -> status_row.depth`, `row.priority -> status_row.priority`, `row.approved_at -> status_row.approved_at`, `row.candidate -> status_row.candidate_commit`, and `row.pull_request -> status_row.pull_request`; generation, node fields, and the four candidate fields are projected from the full core state schema below. `state.yaml` is not restricted to the projection and unknown future fields are preserved; it is never opened by a consumer directly, and its raw digest/root/provenance cross-check must match the core-backed response.

The actual current core state top-level schema is closed for known fields but permits additive future fields: required `schema_version:int=1`, `id:string`, `stage:spec|build|verify|review`, `status:queued|active|blocked|complete|cancelled`, `generation:positive int`, `created_at:timestamp`, `depth:tiny|standard|complex`, `priority:int`, `approved_at:timestamp|null`, `spec:{approved_revision:string,digest:sha256}`, `executable:{source_spec_revision:string,digest:sha256}`, `nodes:{node_id:{status:pending|waiting|running|blocked|complete|failed,attempt:nonnegative int,result_ref:string|null}}`, `trajectory:{first_verified_task:{node:string|null,evidence_ref:string|null,verified_at:timestamp|null}}`, `candidate:{repository:string|null,branch:string|null,commit:full 40-hex|null,source_digest:sha256|null}`, `critics:{technical:CriticRecord,contract:CriticRecord}`, `delivery:{pull_request:string|null,head_commit:full 40-hex|null,source_digest:sha256|null,outcome:not_due|pending|ready|merged|closed,review_iteration:nonnegative int}`, and `last_transition:{from:string,to:string,decision:string,reason:string|null,at:timestamp}`. Each `CriticRecord` has exactly `outcome:not_due|PASS|FAIL`, `candidate:sha256|null`, `reviewed_commit:full 40-hex|null`, `evidence_ref:string|null`, `reviewer:string|null`, `context_id:string|null`, and `recorded_at:timestamp|null`. Core-produced nulls above are legitimate; this core-state exception does not change the omit-vs-null rules for Memory, context, or attempt records.

The adapter extracts `nodes[<node_id>].status`, `.attempt`, top-level `generation`, and candidate fields only after core validation and exact type/domain checks. Missing required current fields, malformed known fields, a non-object, a work-ID mismatch, invalid full SHA/source digest, generation or attempt mismatch, status-row mismatch, validation failure, or read-back digest/root mismatch is `READBACK_FAILED` or `IDENTITY_MISMATCH`; additive unknown core fields are not a refusal and no second validator is introduced.

## 6. Shared adapter foundation and parallel gate

The campaign integrator owns the shared adapter foundation before any M02–M05 consumer starts in parallel. Its exact planned files are `packages/razr-pi/adapter/protocol.py` (closed request/response/error dataclasses, canonical serialization, digest-domain validators, and enum constants) and `packages/razr-pi/adapter/core_subprocess.py` (allowlisted subprocess invocation, provenance preflight, bounded capture, status/read-back, and typed errors). It is one library with one public import surface; M02–M05 may not each define private protocol or subprocess helpers.

The foundation is a hard prerequisite, not a parallel deliverable. Its frozen typed Python API is:

```python
class ProtocolError(Exception):
    code: ErrorCode
    retryable: bool
    details: dict[str, object]

@dataclass(frozen=True)
class ProvenancePin:
    path: Literal["scripts/razr.py", "templates/evidence.md", "templates/executable.yaml", "templates/razr.yaml", "templates/spec.md", "templates/state.yaml"]
    sha256: str  # lowercase 64 hex, raw-file domain

@dataclass(frozen=True)
class ReadbackSpec:
    path: str  # repo-relative, never absolute
    required: bool
    max_bytes: int
    parse_as: Literal["bytes", "json"]

@dataclass(frozen=True)
class CoreRequest:
    request_id: str  # unique per invocation, echoed exactly
    repository_root: Path  # canonical absolute, ephemeral only
    argv: tuple[str, ...]
    cwd: Path  # canonical absolute, ephemeral only
    package_root: Path  # canonical absolute, ephemeral only
    python_executable: Path  # canonical absolute, ephemeral only
    runtime_script: Path  # canonical absolute, ephemeral only
    expected_source_digest: str  # core source-tree sha256
    expected_core_identity: str  # sha256 of exact provenance.json bytes
    expected_provenance: tuple[ProvenancePin, ...]
    expected_readback: tuple[ReadbackSpec, ...]
    timeout_ms: int
    termination_grace_ms: int
    max_stdout_bytes: int
    max_stderr_bytes: int

@dataclass(frozen=True)
class ReadbackResult:
    path: str  # repo-relative
    digest_domain: Literal["raw_file", "canonical_json"]
    digest: str
    bytes: int
    parsed: object | None

@dataclass(frozen=True)
class CoreResponse:
    request_id: str  # exact echo of CoreRequest.request_id
    returncode: int
    stdout: bytes
    stderr: bytes
    elapsed_ms: int
    core_identity: str  # exact provenance.json raw-file sha256
    provenance: tuple[ProvenancePin, ...]
    source_digest: str  # core source-tree sha256
    readback: tuple[ReadbackResult, ...]

ErrorCode = Literal[
    "INVALID_REQUEST", "OUTPUT_LIMIT", "TIMEOUT", "INTERRUPTED", "CORE_EXIT",
    "MALFORMED_RESPONSE", "READBACK_FAILED", "PROVENANCE_MISMATCH",
    "LOCK_UNAVAILABLE", "LEDGER_CORRUPT", "DUPLICATE_ATTEMPT", "INVALID_ORDER",
    "ALLOWANCE_REUSED", "PATH_ESCAPE", "SOURCE_CHANGED", "INTERNAL",
    "UNSUPPORTED_CAPABILITY", "IDENTITY_MISMATCH", "DIGEST_MISMATCH", "STALE_GENERATION",
    "CANDIDATE_CHANGED", "RESOURCE_REUSED", "FRESH_CONTEXT_UNAVAILABLE", "LAUNCH_FAILED",
    "INTERRUPT_FAILED", "ARTIFACT_UNAVAILABLE", "CLEANUP_UNKNOWN", "PROVIDER_UNAVAILABLE",
    "FANOUT_UNAVAILABLE", "HERDR_IDENTITY_INCOMPLETE", "WORKFLOW_UNAVAILABLE",
    "MEMORY_UNAVAILABLE", "LIMIT_EXCEEDED"
]

def canonical_json(value: JsonValue) -> bytes: ...
def raw_file_digest(data: bytes) -> str: ...
def validate_git_object_id(value: str) -> str: ...
def validate_source_digest(value: str) -> str: ...
def validate_executable_digest(value: str) -> str: ...
def invoke_core(request: CoreRequest) -> CoreResponse: ...
```

`CoreRequest` and `CoreResponse` are closed to the fields shown above. `request_id` must match the shared ID grammar, be unique for the invocation, and be echoed byte-for-byte; missing, duplicate, or mismatched IDs map to `INVALID_REQUEST` or `IDENTITY_MISMATCH`. `repository_root` and `cwd` must both be existing canonical absolute directories and must resolve to the same path; the subprocess must run with that exact cwd. `package_root`, `python_executable`, and `runtime_script` must be existing canonical regular files/dirs in the reviewed package selection; the runtime script must be beneath `package_root`. Every `ReadbackSpec.path` must be repo-relative, non-symlinked, beneath `repository_root`, and within its declared byte limit. A response whose root, request ID, runtime identity, source digest, or read-back set does not match the request is refused with `IDENTITY_MISMATCH`, `SOURCE_CHANGED`, or `READBACK_FAILED`. `expected_core_identity` is the raw-file `sha256:<64 hex>` of the exact `runtime/provenance.json` bytes; each `ProvenancePin` is a raw-file SHA-256 for one of the six pinned paths. `source_digest` in the response is the core tree digest defined in the digest-domain table, never the provenance digest. `expected_readback` is closed to repo-relative paths with positive byte limits; `readback` returns every required result and any optional result that was present, with raw-file or canonical-JSON digest as declared. `invoke_core` uses `MAX_STDOUT_BYTES = 1_048_576`, `MAX_STDERR_BYTES = 262_144`, a default `timeout_ms = 120_000`, and a default `termination_grace_ms = 5_000`; request values may lower limits but may not raise these maxima. Exceeding either output limit maps to `OUTPUT_LIMIT`; timeout maps to `TIMEOUT`; signal termination maps to `INTERRUPTED`; nonzero exit maps to `CORE_EXIT`; malformed expected JSON maps to `MALFORMED_RESPONSE`; failed expected-file read-back maps to `READBACK_FAILED`; provenance mismatch maps to `PROVENANCE_MISMATCH`; path/environment rejection maps to `INVALID_REQUEST`; and lock failures map to `LOCK_UNAVAILABLE`. These mappings are closed and do not infer workflow outcomes.

Every operation uses the same complete `ErrorCode` enum above; an operation may emit only its documented subset, but no operation invents a code outside it. Context may emit `INVALID_REQUEST`, `PATH_ESCAPE`, `SOURCE_CHANGED`, `WORKFLOW_UNAVAILABLE`, `MEMORY_UNAVAILABLE`, `LIMIT_EXCEEDED`, or `INTERNAL`. Provider may emit `INVALID_REQUEST`, `UNSUPPORTED_CAPABILITY`, `IDENTITY_MISMATCH`, `DIGEST_MISMATCH`, `STALE_GENERATION`, `CANDIDATE_CHANGED`, `RESOURCE_REUSED`, `ALLOWANCE_REUSED`, `FRESH_CONTEXT_UNAVAILABLE`, `LAUNCH_FAILED`, `TIMEOUT`, `OUTPUT_LIMIT`, `INTERRUPT_FAILED`, `ARTIFACT_UNAVAILABLE`, `CLEANUP_UNKNOWN`, `PROVIDER_UNAVAILABLE`, `FANOUT_UNAVAILABLE`, `HERDR_IDENTITY_INCOMPLETE`, or `INTERNAL`. Memory/ledger may emit `INVALID_REQUEST`, `LOCK_UNAVAILABLE`, `LEDGER_CORRUPT`, `DUPLICATE_ATTEMPT`, `INVALID_ORDER`, `ALLOWANCE_REUSED`, or `INTERNAL`. Core subprocess may emit the mappings specified below. This is the complete shared enum and includes every later code list.

The subprocess environment is exactly the generated allowlist `PATH`, `HOME`, `TMPDIR`, `LANG`, `LC_ALL`, `LC_CTYPE`, `PYTHONIOENCODING`, `GIT_CONFIG_NOSYSTEM`, `GIT_CONFIG_GLOBAL`, `GIT_OPTIONAL_LOCKS`, and `GIT_TERMINAL_PROMPT`; values are supplied by the caller or fixed to `UTF-8`, `1`, `/dev/null`, `0`, and `0` as applicable. `PI_*`, provider credentials, ambient Git overrides, `PYTHONPATH`, `NODE_OPTIONS`, and all other inherited names are removed. stdin is closed, shell execution is disabled, and stdout/stderr are captured as bytes before UTF-8 decoding for diagnostics.

Its owner must land a focused test module covering closed-field rejection, omit-vs-null, canonical digest vectors, full-40-hex Git IDs, source/executable digest separation, the concrete limits/defaults/error mappings, timeout/nonzero/malformed-output/read-back failures, environment stripping, and provenance refusal. The campaign integrator freezes this API, constants, and test vectors in the prerequisite commit, then M02 owns only Memory implementation, M03 only context assembly, M04 only provider/ledger, and M05 only Pi registration. Changes after freeze require a new ADR revision and serialized migration; consumers cannot silently fork or broaden the protocol.

## 7. Pi package and extension (M05)

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

Registered command names (the strings passed to `pi.registerCommand`) are exactly `razr-status`, `razr-context`, `razr-remember`, `razr-recall`, and `razr-memory-inspect`; Pi user invocation displays them as `/razr-status`, `/razr-context`, `/razr-remember`, `/razr-recall`, and `/razr-memory-inspect`. Worker operations are not registered until M04's provider interface and M05's command review are complete. The extension is loaded by normal Pi package discovery; loading does not adopt, initialize, or write a project. M05 owns only the manifest, entrypoint, registration, argument parsing, and Pi smoke test. It does not own Memory/context/provider implementation.

## 8. Herdr provider (M04)

Runtime detection uses `HERDR_ENV=1`. Outside Herdr, normal Razr status/context/Memory/core operations continue to work; only the M04 fanout execution provider is unavailable. Its `probe` returns the authoritative `FANOUT_UNAVAILABLE`, and its `launch`, `inspect`, `collect`, and `interrupt` operations refuse with `FANOUT_UNAVAILABLE`. There is no standalone provider launch, handle, or result variant. When `HERDR_ENV=1`, all of `HERDR_WORKSPACE_ID`, `HERDR_TAB_ID`, and `HERDR_PANE_ID` are required and must be non-empty IDs. If any is missing, the provider refuses with `HERDR_IDENTITY_INCOMPLETE`; it does not guess or fall back. Inside Herdr with complete identity, the provider owns host transport and telemetry, not Razr authority.

The exact provider request is:

```json
{
  "schema_version":1,"request_id":"exec-...","attempt_id":"att-...","role":"technical|contract|worker",
  "host":{"mode":"herdr","workspace_id":"...","tab_id":"...","pane_id":"..."},
  "work":{"work_id":"...","node_id":"...","generation":12,"ordinal":2},
  "worker_id":"worker-...","branch":"feature/example",
  "candidate":{"repository":"owner/repository","branch":"feature/example","commit":"0123456789abcdef0123456789abcdef01234567","source_digest":"sha256:..."},
  "worktree":"/absolute/canonical/worktree","cwd":"/absolute/canonical/worktree",
  "output_root":"/absolute/canonical/output","output_root_id":"out-...","output_root_digest":"sha256:...",
  "session_root":"/absolute/canonical/session","session_root_id":"sess-...","session_root_digest":"sha256:...",
  "fresh_context":true,"neutral_input_digest":"sha256:...",
  "environment_policy_digest":"sha256:...","tool_policy_digest":"sha256:...",
  "required_capabilities":["fresh_context","working_directory","bounded_time","bounded_output","runtime_identity"],
  "runtime":{"provider":"...","model":"...","thinking":"medium"},
  "limits":{"deadline_ms":600000,"max_output_bytes":1048576},"allowance_id":"allow-..."
}
```

The fanout request is a closed Herdr-only variant: `host` is exactly `{"mode":"herdr","workspace_id":"...","tab_id":"...","pane_id":"..."}` and requires all three IDs. There is no standalone request variant for fanout. `worker_id`, `branch`, `worktree`, `cwd`, both root IDs, both root paths, and all four root digests are required request fields. `worktree` and `cwd` are canonical absolute paths and must be byte-for-byte equal after canonicalization; this makes the STARTED ledger fields derivable and validation deterministic. `output_root` and `session_root` must be distinct, owned, empty directories under the provider-approved private root. Their IDs are unique opaque identities; their digests are canonical-JSON digests of a sorted directory manifest (relative path, type, size, and raw-file digest for each entry). These absolute request paths are ephemeral and are not written to the ledger. A provider refuses `RESOURCE_REUSED` when either ID or digest was used by any prior attempt, the directory is non-empty/reused, or a returned handle/result does not preserve the exact request values. No root is reused after timeout, interruption, cleanup uncertainty, or settlement.

The provider response schemas are closed. Launch and collect are:

```json
{"schema_version":1,"ok":true,"request_id":"exec-...","result":{"handle":{"provider_id":"herdr","handle_id":"...","request_digest":"sha256:...","attempt_id":"att-...","host":{"mode":"herdr","workspace_id":"...","tab_id":"...","pane_id":"..."},"output_root_id":"out-...","output_root_digest":"sha256:...","session_root_id":"sess-...","session_root_digest":"sha256:..."},"capability_report_digest":"sha256:..."}}
{"schema_version":1,"ok":true,"request_id":"exec-...","result":{"attempt_id":"att-...","termination":"SUCCEEDED|FAILED|INTERRUPTED|TIMED_OUT|UNKNOWN","runtime_identity":"...","host":{"mode":"herdr","workspace_id":"...","tab_id":"...","pane_id":"..."},"output_root_id":"out-...","output_root_digest":"sha256:...","session_root_id":"sess-...","session_root_digest":"sha256:...","artifact_refs":[{"kind":"stdout|stderr|session|event","digest_domain":"raw_file","digest":"sha256:...","bytes":123}],"exit_code":0,"cleanup":"confirmed|uncertain|not_applicable"}}
```

A fanout status response is Herdr-only and closed, with no null fields:

```json
{"schema_version":1,"ok":true,"request_id":"exec-...","result":{"status":"STARTING|ACTIVE|SETTLED|STOPPING|TERMINATED|UNKNOWN","observed_at":"...Z","source":"herdr","workspace_id":"...","tab_id":"...","pane_id":"..."}}
```

`workspace_id`, `tab_id`, and `pane_id` are required and never null. Outside Herdr there is no status handle/result to inspect; `inspect` refuses with `FANOUT_UNAVAILABLE`. Launch handles and collect results require the Herdr host variant and preserve all three exact IDs from the launch request. A host variant mismatch is `IDENTITY_MISMATCH`.

The provider operation schemas are also fixed. `probe` accepts `{schema_version, request_id, required_capabilities, runtime}` and returns `{schema_version, ok:true, request_id, result:{provider_id, runtime_identity, capabilities:[<capability enum>], report_digest}}`; `capabilities` may contain only `fresh_context`, `saved_session`, `neutral_instructions`, `working_directory`, `environment_policy`, `bounded_time`, `bounded_output`, `stream_capture`, `interrupt`, `tool_policy_observation`, and `runtime_identity`. `launch` accepts the request above and returns the handle response above. `inspect` accepts `{schema_version, request_id, handle}` and returns the status response above. `collect` accepts `{schema_version, request_id, handle}` and returns the result response above. `interrupt` accepts `{schema_version, request_id, handle, reason:"cancel|timeout|policy_violation"}` and returns `{schema_version:1,ok:true,request_id,result:{attempt_id,host:{mode:"herdr",workspace_id:"...",tab_id:"...",pane_id:"..."},requested:true,observed:"SETTLED|STOPPING|TERMINATED|UNKNOWN",cleanup:"confirmed|uncertain|not_applicable"}}`; it preserves the complete Herdr identity.

Provider emits only the provider subset of the complete shared `ErrorCode` enum specified above. `INVALID_REQUEST`, identity/digest mismatch, stale generation, candidate change, resource/allowance reuse, unsupported capability, Herdr identity incompleteness, and fresh-context failure are non-retryable for that request. `PROVIDER_UNAVAILABLE`, launch failure, and timeout may be retried only by a new explicitly authorized attempt with a new `attempt_id`, allowance assertion, request digest, and fresh output/session roots. Output clipping, interruption failure, missing artifacts, and cleanup uncertainty produce an incomplete result and are never silently retried or classified as success.

`inspect` is read-only. `collect` preserves partial artifacts. Missing identity, candidate/request digest mismatch, unsupported capability, fallback, retry, reused handle/output root, timeout, output clipping, pane loss, or cleanup uncertainty returns a non-success classification; `UNKNOWN` is never converted to failure or success automatically. `interrupt` is idempotent as a request and reports requested versus observed cleanup. `Herdr` status is never a Razr transition.

## 9. Attempt ledger (M04)

M04 chooses an append-only event ledger, not a mutable record and not a terminal-only record. `.razr/attempts.jsonl` contains exactly two closed event variants: one `STARTED` event and one `SETTLED` event per `attempt_id`.

Dispatch ordering is fixed: the authority validates the generation and claims/records the allowance; the foundation writes and fsyncs the redacted request packet; the adapter appends and fsyncs STARTED; only then may it call provider `launch`. STARTED is therefore durable after allowance claim and before launch. A launch that returns an error or is refused appends exactly one SETTLED event with `outcome:FAILED` when the launch failure is observed or `outcome:UNKNOWN` when launch/cleanup cannot be classified. Its `evidence_refs` must include a repo-relative `launch_failure` record with `error_code:LAUNCH_FAILED` (or the more specific shared code when applicable), digest domain, and digest. A timeout or cleanup loss after a handle exists uses its specific timeout/cleanup evidence and settles `UNKNOWN` when necessary. No failed launch is left with only STARTED and no automatic second STARTED is permitted.

If the process dies after STARTED fsync and before launch, recovery finds the unmatched STARTED, confirms no provider handle was issued when possible, and appends SETTLED `UNKNOWN` with crash-boundary evidence. If death occurs after launch but before SETTLED, recovery inspects the preserved request/handle/root identities; it appends SETTLED `UNKNOWN` unless a provider result proves a terminal outcome. Recovery never assumes success, never reuses roots/allowance, and requires explicit recovery authority. Thus a crash between the two durable events leaves one recoverable unmatched STARTED, not a second state machine; recovery closes it with the second and final event.

A STARTED event requires `schema_version`, `event`, `attempt_id`, `work_id`, `node_id`, `observed_generation`, `observed_ordinal`, `worker_id`, `branch`, repo-relative `worktree_ref`, `allowance_id`, `provider_id`, `started_at`, `started_at_source`, `candidate`, `candidate_evidence`, `request_packet`, `output_root_id`, `output_root_digest`, `session_root_id`, `session_root_digest`, `request_digest`, `prev_digest`, and `record_digest`. `candidate` requires the 40-hex `commit` and `sha256:` `source_digest`; `candidate_evidence` and `request_packet` require repo-relative `ref`, `digest_domain`, and `digest`, with `request_packet.digest_domain` fixed to `canonical_json`. It optionally has `herdr`; when present, `herdr` requires `workspace_id`, `tab_id`, and `pane_id`. No other fields are allowed, and optional `herdr` is omitted rather than null.

A SETTLED event requires `schema_version`, `event`, `attempt_id`, `work_id`, `node_id`, `observed_generation`, `observed_ordinal`, `provider_id`, `worktree_ref`, `branch`, `settled_at`, `candidate`, `candidate_evidence`, `request_packet`, `output_root_id`, `output_root_digest`, `session_root_id`, `session_root_digest`, `outcome`, `request_digest`, `evidence_refs`, `prev_digest`, and `record_digest`. It optionally has `herdr`, `exit_code`, and `cleanup`; when present, `herdr` requires all three IDs, `exit_code` is an integer, and `cleanup` is `confirmed|uncertain|not_applicable`. `evidence_refs` is an array whose entries require repo-relative `ref`, `digest_domain`, `digest`, and `kind`; `error_code` is additionally required for `launch_failure` entries and must be `LAUNCH_FAILED` or the specific shared launch error. Provider artifacts use `raw_file` and request/report identities use `canonical_json`. No other fields are allowed, and absent optional fields are omitted rather than null. `event` is exactly `STARTED|SETTLED`; STARTED `started_at_source` is exactly `adapter_observed`; `outcome` is exactly `SUCCEEDED|FAILED|INTERRUPTED|TIMED_OUT|UNKNOWN`.

Each event has exactly this shape, with the event-specific required fields above. The foundation reads the candidate commit/source digest and generation from the core read-back and writes `candidate_evidence`; it creates the redacted, repo-relative request packet and writes `request_packet` before launch. The provider request/adapter observes `provider_id` from the selected provider and validates `worker_id`, branch, canonical absolute worktree/cwd, root IDs/digests, and Herdr IDs before dispatch. The adapter records `started_at` immediately before the durable STARTED append with `started_at_source:"adapter_observed"`, copies the validated Herdr identity when present, then launches the provider and later records the returned handle in private evidence. The persistent request packet contains no absolute operational path: it stores repo-relative `worktree_ref`, branch, worker ID, root IDs/digests, candidate identity, limits, and the canonical request digest. The later collector appends SETTLED and links collected artifacts through `evidence_refs`; it cannot alter STARTED fields.

```json
{"schema_version":1,"event":"STARTED","attempt_id":"att-...","work_id":"...","node_id":"...","observed_generation":12,"observed_ordinal":2,"worker_id":"...","branch":"...","worktree_ref":"worktrees/worker","allowance_id":"...","provider_id":"herdr","started_at":"...Z","started_at_source":"adapter_observed","candidate":{"commit":"0123456789abcdef0123456789abcdef01234567","source_digest":"sha256:..."},"candidate_evidence":{"ref":".razr/evidence/candidate.json","digest_domain":"canonical_json","digest":"sha256:..."},"request_packet":{"ref":".razr/evidence/attempt-att.json","digest_domain":"canonical_json","digest":"sha256:..."},"output_root_id":"out-...","output_root_digest":"sha256:...","session_root_id":"sess-...","session_root_digest":"sha256:...","request_digest":"sha256:...","herdr":{"workspace_id":"...","tab_id":"...","pane_id":"..."},"prev_digest":"sha256:...","record_digest":"sha256:..."}
{"schema_version":1,"event":"SETTLED","attempt_id":"att-...","work_id":"...","node_id":"...","observed_generation":12,"observed_ordinal":2,"provider_id":"herdr","worktree_ref":"worktrees/worker","branch":"...","settled_at":"...Z","candidate":{"commit":"0123456789abcdef0123456789abcdef01234567","source_digest":"sha256:..."},"candidate_evidence":{"ref":".razr/evidence/candidate.json","digest_domain":"canonical_json","digest":"sha256:..."},"request_packet":{"ref":".razr/evidence/attempt-att.json","digest_domain":"canonical_json","digest":"sha256:..."},"output_root_id":"out-...","output_root_digest":"sha256:...","session_root_id":"sess-...","session_root_digest":"sha256:...","outcome":"SUCCEEDED|FAILED|INTERRUPTED|TIMED_OUT|UNKNOWN","request_digest":"sha256:...","evidence_refs":[{"ref":".razr/evidence/result.json","digest_domain":"raw_file","digest":"sha256:...","kind":"result"}],"cleanup":"confirmed","prev_digest":"sha256:...","record_digest":"sha256:..."}
```

Events are ordered by append position and form one global hash chain. A STARTED event must be the first event for its attempt; exactly one SETTLED event must follow it; no event may follow SETTLED; `SETTLED.request_digest`, work/node identity, observed generation/ordinal, provider, branch, worktree_ref, candidate, candidate_evidence, request_packet, output-root identity/digest, session-root identity/digest, and Herdr identity must equal STARTED; and a SETTLED event is not written until the provider result is collected. `record_digest` is the canonical-JSON digest of the complete event with `record_digest` omitted, including `prev_digest`; it does not use raw-file, source-tree, executable, or Git-object serialization. Append takes the sibling exclusive `flock`, validates the full chain and per-attempt ordering while holding the lock, writes one complete line with `O_APPEND`, calls `fsync`, then releases the lock. Broken chains, partial lines, duplicate STARTED/SETTLED events, settlement without STARTED, reused allowance/session, or failed fsync return `LEDGER_CORRUPT`, `DUPLICATE_ATTEMPT`, `INVALID_ORDER`, `ALLOWANCE_REUSED`, or `LOCK_UNAVAILABLE` and do not append. There is no in-place repair; an interrupted dispatch is settled as `UNKNOWN` only from retained evidence under explicit authority, and a retry uses a new attempt ID and STARTED event.

## 10. Legacy classification and migration ownership

- **Permanent:** `runtime/scripts/razr.py` and its canonical templates; `scripts/adopt.py` safe adoption; `scripts/primary.py` read-only inspection; Git/`gh` as history and delivery boundaries; the single `skills/razr/SKILL.md` instruction surface. These remain supported and are owned by the core/adoption compatibility owner.
- **Temporary compatibility shims:** `scripts/contract-preflight.py` and `scripts/named-regressions.py`. Owner: M05/package maintainers. Removal gate: M02–M05 replacement callers, equivalent refusal/identity tests, documented migration notes, and one clean-source full-suite run on the replacement path; no removal before a separately reviewed campaign commit.
- **Excluded/retired:** historical workshop/lab helpers, critic launchers, recovery dispatchers, scheduler/fleet code, PR factory, Project Brain/Obsidian integrations, and any direct lifecycle-state writer. They are not imported, packaged, or compatibility targets.

The dependency graph is `foundation (campaign integrator) -> M02 Memory`, `foundation -> M04 Herdr/provider+attempts`, `foundation -> M05 Pi registration`, and `M02 -> M03 context`; M05 also consumes the frozen M02/M03/M04 public interfaces. No M02–M05 implementation may start in parallel before the foundation prerequisite commit and tests are reviewed; M03 cannot start before M02, and M05 cannot register consumers before their interface owners freeze them. M02 owns Memory only; M03 owns context only; M04 owns Herdr/provider and attempts only; M05 owns Pi packaging/registration only. Shared JSON rules above are frozen for these missions. Interface changes require an ADR revision and serialized review before parallel implementation.

## 11. Non-goals

This M01 contract does not implement Memory, context, Herdr fanout, the extension, a scheduler, deployment, npm publication, credential management, vector search, background summarization, automatic retries, independent critique, human approval, clean-clone acceptance, or GitHub delivery automation. It does not replace the Razr workflow state machine.
