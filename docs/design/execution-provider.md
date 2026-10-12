# Execution provider boundary

The execution provider connects an already authorized Razr work request to an execution host. It transports work and returns observations; it does not own workflow authority.

## Authority separation

```text
Razr   workflow contracts, state, gates, and delivery authority
Pi     reasoning and tool execution
Herdr  optional workspace, terminal, worktree, and process hosting
```

The caller remains responsible for specification approval, generation-fenced transitions, candidate identity, excluded-input scrutiny, critic independence and sequencing, semantic evidence review, and final human review.

**Provider lifecycle is not Razr state. Herdr state is not Razr state.** Host observations such as `working`, `blocked`, `idle`, `done`, process exit, or pane disappearance do not imply Razr success, failure, completion, review acceptance, or allowance availability.

## Provider operations

Conceptually, the provider exposes:

```text
probe(requirements) -> CapabilityReport
launch(request)      -> ExecutionHandle
inspect(handle)      -> ExecutionStatus
collect(handle)      -> ExecutionResult
interrupt(handle)    -> InterruptionResult
```

- `probe` verifies concrete behavior without constructing a model client.
- `launch` accepts one closed, exact, already authorized request.
- `inspect` returns timestamped operational observations.
- `collect` retains original artifacts and mechanical outcomes, not a review verdict.
- `interrupt` requests termination and reports observed cleanup, including uncertainty.

## Capability model

Capabilities are behavior claims, not product-name or version checks.

| Capability | Meaning |
|---|---|
| `fresh_context` | Starts without resume, fork, or builder history. |
| `saved_session` | Produces one inspectable session identity and artifact. |
| `neutral_instructions` | Accepts explicit inputs while ambient resources are disabled. |
| `working_directory` | Uses the exact verified candidate root. |
| `environment_policy` | Applies a reviewed environment allowlist without exposing secrets. |
| `bounded_time` | Enforces the operation deadline or refuses. |
| `bounded_output` | Limits retained output and reports overflow. |
| `stream_capture` | Retains machine-readable output/events. |
| `interrupt` | Supports termination requests and cleanup observations. |
| `tool_policy_observation` | Exposes enough evidence to inspect tool selection and arguments. |
| `runtime_identity` | Reports the concrete provider/model/runtime and fallback status. |

A missing required capability is an unsupported request, not a warning.

## Request identity

A launch request binds:

- request, attempt, worker, role, and allowance IDs;
- repository, branch, commit, and source digest;
- work item, node, observed generation, and attempt ordinal;
- exact worktree and working directory;
- fresh output and session roots with opaque IDs;
- neutral input and policy digests;
- required capabilities and runtime constraints; and
- time and output limits.

Unknown fields, unsafe paths, identity mismatches, reused resources, unsupported capabilities, fallback, resume, or implicit retry are refused.

## Dispatch ordering

Under the provider's dispatch lock:

1. Validate the closed request and capability report.
2. Reject duplicate attempt, allowance, and resource identities.
3. Read current work through the Razr core and verify generation, node ordinal, and candidate before any worktree or dispatch mutation.
4. Verify or bind the requested Herdr target worktree.
5. Stage exact evidence transactionally.
6. Append and fsync STARTED, making it the durable attempt/resource-ID claim.
7. Materialize integrity-bound permanent path claims and ownership markers.
8. Repeat core and candidate verification with a read-only fence.
9. Start the worker immediately after the final fence.

Known failures before STARTED clean exact staging and remain retryable only under the original request rules. Failures after STARTED settle explicitly or leave one recoverable unmatched STARTED. Append-only chronology is not rewritten, and no second worker is launched for the same attempt.

## Herdr implementation

Inside Herdr, the request preserves separate caller and target workspace/tab/pane identities. The caller must match the invoking environment. The target is independently bound to the requested worktree before launch.

The provider:

- launches fresh Pi context with explicit runtime and resource constraints;
- streams independently bounded stdout and stderr;
- runs commands in owned process groups under one end-to-end deadline;
- rejects linked executable and evidence paths;
- records durable STARTED/SETTLED events and integrity-bound resource claims;
- retains session/output artifacts with digests and bounds;
- supports idempotent interruption; and
- reports `UNKNOWN` when termination or cleanup cannot be established.

A deliberately detached process may escape the owned process group; the provider reports cleanup uncertainty rather than claiming control.

## Result semantics

Collection returns mechanical evidence such as:

- exact request/attempt/provider/runtime identity;
- timestamps and termination observations;
- stdout, stderr, event, and session references with byte counts and digests;
- actual working directory and applied policy identity;
- clipping, fallback, retry, interruption, and cleanup observations; and
- declared limitations.

The caller separately verifies freshness, candidate binding, neutral inputs, tools, source integrity, report structure, substantive claims, and reviewer independence before any Razr transition.

## Privacy and evidence

Public records use repository-relative references and digests. Canonical machine paths, environment values, raw output, sessions, and provider handles remain in private owned storage. Secrets are never included intentionally; filtering and path controls are not confidentiality certification or hostile-OS authentication.

The provider does not approve specifications, choose work, validate critic semantics, mutate Razr lifecycle state, create merge authority, or deploy software.
