# Execution provider boundary

## Decision

M04 implements a bounded Herdr transport proof within this execution-provider boundary. It does not provide a general delivery adapter: its subprocesses and telemetry remain transport evidence, never workflow authority or inferred approval. The public proof covers capability probing, owned worktree/session identity, durable attempts, bounded collection, interruption, cleanup, and truthful UNKNOWN outcomes; live campaign gates and human approval remain external.

This note is therefore a design target for a later, separately approved vertical slice. That slice should begin with one observable tracer—launch one bounded fresh Technical Critic, collect candidate-bound evidence, and stop for the actual human checkpoint—not with a framework-only refactor. No code, tests, allowlist, lifecycle state, or external file is changed by this mission.

A relevant Pi distinction is that a loaded skill supplies instructions; it does not itself start a model, authorize workflow changes, or prove that work ran. A headless Pi invocation is an actual execution only when a launcher starts it, and its result remains unverified until the required evidence is collected and independently checked.

## Authority and boundary

The intended separation is:

```text
Razr   workflow contracts, gates, evidence requirements, and authority
Pi     reasoning and tool execution
Herdr  optional process/session host and operational telemetry
```

An execution provider transports an already authorized request to an execution mechanism and returns observations. It must not:

- approve a specification, select work, or decide that a node is dispatchable;
- mutate Razr lifecycle state or translate process state into a Razr transition;
- decide that a report is a valid critic PASS;
- choose a candidate, relax source/excluded-input scrutiny, or alter neutral review inputs;
- infer human checkpoints, critic independence, allowance, retry authority, PR approval, merge, or deployment authority; or
- treat package loading, capability probing, process exit zero, or CLI attestations as proof of those decisions.

The caller remains responsible for exact approval, generation-fenced transitions, candidate/source identity, excluded inputs, critic sequencing and independence, semantic evidence review, and final human review.

**Provider lifecycle is not Razr state. Herdr state is not Razr state.** Per the Mission 03 audit, a Razr node marked `running` only records a generation-fenced logical transition; it is not process liveness. Likewise, Herdr `working`, `blocked`, `idle`, `done`, or `unknown` is transient host telemetry. None implies Razr `running`, `blocked`, `complete`, success, failure, or allowance availability, and pane disappearance must not select a Razr outcome.

## Current execution paths

### Public source

The supported public path is adoption/discovery and read-only inspection, not live task or critic execution:

1. Pi's installed package resolver loads one skill without constructing an agent, client, auth store, or model runtime.
2. Explicit adoption invokes the unchanged Razr core.
3. Discovery and read-only helpers invoke core/Git subprocesses.
4. `tests/run_public.py` launches bounded verification subprocesses in isolated temporary roots. Its Pi check is `tests/clean_package_preflight.mjs`, a no-model resource-loader test.

`tests/run_public.py` controls child argv, cwd, an allowlisted environment, timeout, captured stdout/stderr, and retained command records. That is test-runner supervision, not a live Pi execution provider. `packages/razr-pi/scripts/primary.py` similarly invokes the core for status and validation; it does not launch reasoning sessions. The public tree contains no session assignment, critic dispatcher, model capability probe, interruption API, worker fleet, or recovery loop.

### Excluded local headless launchers

Read-only inspection of the external workshop source found several related but distinct launch paths:

- `technical-critic.py` prepares a detached candidate worktree, isolated Pi resources and neutral role packet; performs a no-model parser/resource probe; launches one fresh saved headless Pi process; supervises its process group, timeout, output bound, and cancellation; then checks the actual session, event stream, model identity, tools, report, candidate identity, and source integrity.
- `real-technical-review.py` has a separate generic capture routine with process-group termination, output clipping, attempt consumption before dispatch, a live output policy watcher, and a later audit of saved session/events. Capture success is explicitly unassessed, not a review verdict.
- `real-technical-review-primary.py` adds an outer worker supervisor and nested-cleanup gates around the live review and primary reproduction phases. Its host-process completion is separately checked from nested execution records.
- `pi-resource-probe.mjs` checks concrete parser, fresh-session, resource-isolation, retry, compaction, transport, and tool-selection behavior without making a model call. Pi version is diagnostic rather than an acceptance pin.

These local helpers duplicate subprocess supervision concerns, but they are historical/lab-bound, absent from the public allowlist, and governed by different experimental contracts. They are evidence for the shape of a future boundary, not portable public implementation or proof that the public package supports critics.

### External Herdr host

Herdr currently controls terminal panes and interactive agents. Its installed Pi extension reports a TUI session reference and operational `working`, `blocked`, or `idle` observations to Herdr. It deliberately does not report headless JSON/print sessions. Herdr can start, prompt, inspect, interrupt, and observe agents through pane/agent operations, but submission, a settled state, or visible terminal output does not establish a candidate-bound Razr result.

A future Herdr provider would therefore be a host adapter, not a workflow adapter. It would need an explicit artifact/result protocol rather than deriving acceptance from screen text or agent state.

## Coupling findings

The excluded headless implementations currently combine several concerns:

- request authorization and allowance consumption;
- provider/model selection and Pi argv construction;
- candidate worktree and resource preparation;
- capability probing;
- process creation, timeout, signals, capture, and cleanup;
- session discovery and freshness checks;
- protocol/event/report validation;
- source-integrity checks; and
- role-specific critic sequencing and acceptance rules.

Only process/session hosting belongs behind an execution-provider boundary. Candidate admission, neutral-input construction, allowance policy, critic independence, report semantics, and Razr transitions must stay above it. Moving all existing helper behavior behind `launch()` would hide workflow authority inside a transport abstraction and make a Herdr adapter falsely responsible for Razr correctness.

There is no current public coupling problem to remove. The future seam becomes justified only when the public adapter has one approved live execution path and a second provider must satisfy the same explicit transport contract.

## Capability model

Capabilities are behavior claims, not provider names or release-number comparisons. Probe them before consuming an execution allowance, with no model/client construction and no workflow mutation. A probe result should be bound to the provider implementation/runtime identity and should expire when that identity or relevant configuration changes.

Minimum capability vocabulary:

| Capability | Meaning |
|---|---|
| `fresh_context` | Creates a new context with no continue/resume/fork or builder history. |
| `saved_session` | Produces one inspectable session artifact and stable session identity. |
| `neutral_instructions` | Accepts explicit system/rules/user inputs while disabling ambient resources. |
| `working_directory` | Executes in the exact requested candidate root. |
| `environment_policy` | Applies an explicit environment policy without exporting or logging secrets. |
| `bounded_time` | Enforces the requested work/grace deadlines or reports that it cannot. |
| `bounded_output` | Bounds retained output and reports clipping/overflow as incomplete. |
| `stream_capture` | Retains original machine-readable stdout/stderr or equivalent events. |
| `interrupt` | Requests termination and reports whether direct cleanup was observed. |
| `tool_policy_observation` | Exposes enough events to verify selected tools and arguments. |
| `runtime_identity` | Reports the concrete Pi/provider/model/runtime used, including fallback evidence. |

A required capability that cannot be concretely demonstrated is an unsupported request, not a warning. A version string alone is not demonstration. Capability success proves mechanism compatibility only; it does not prove provider availability at dispatch, critic independence, semantic review quality, or Razr acceptance.

## Proposed provider contract

Conceptually:

```text
probe(requirements) -> CapabilityReport
launch(request)      -> ExecutionHandle
inspect(handle)      -> ExecutionStatus
collect(handle)      -> ExecutionResult
interrupt(handle)    -> InterruptionResult
```

### Request

An immutable, digestible request should contain:

```text
request_id
attempt_id
role
working_directory
candidate: repository, commit, source_digest
work_item: work_id, node_id, observed_generation, observed_attempt_ordinal
fresh_context_required
neutral_input references and digests
environment policy (names/sources/redactions, not secret values)
time and output bounds
required capabilities
runtime selection constraints
approved tool/resource policy
allowance_id and already-consumed assertion
```

The provider must reject unknown fields, unsafe paths, unsupported capabilities, reused handles/output roots, or requests that cannot be represented without weakening constraints. It must not silently choose a fallback model, resume a session, broaden tools/resources, inherit ambient instructions, or retry.

The allowance should be consumed by an authority-owned append-only attempt record at the exact approved dispatch boundary, before provider launch. The provider may verify that assertion, but it does not create authority by itself.

### Handle and status

`ExecutionHandle` is an opaque provider-scoped locator with `provider_id`, `request_digest`, `attempt_id`, and provider handle identity. It is not a Razr node ID, session proof, or success token.

`inspect()` returns timestamped operational observations in a provider namespace, for example:

```text
STARTING | ACTIVE | SETTLED | STOPPING | TERMINATED | UNKNOWN
```

It may also include a host process/pane/session observation and source. These values must never be automatically mapped to Razr statuses or attempt outcomes. Repeated inspection is read-only and may return stale or `UNKNOWN` information.

### Result

`collect()` returns original references and mechanical observations, not a critic verdict:

```text
request and attempt identity
provider/runtime identity
start/end timestamps
termination observation and direct-cleanup confidence
exit/status observations
stdout/stderr/event/session artifact references, byte counts, and digests
actual session identity and lineage observations
actual cwd and applied environment-policy identity
limit, clipping, fallback, retry, and interruption observations
provider diagnostics and declared limitations
```

Collection must preserve partial artifacts and return an incomplete/unknown mechanical disposition when proof is missing. The caller separately validates freshness, candidate binding, neutral inputs, tool use, source integrity, report structure, substantive claims, and critic independence before any generation-fenced Razr command.

`interrupt()` is idempotent as a request but cannot claim complete termination merely because a signal or key was sent. It returns what was requested and observed, including `cleanup_unknown` where appropriate.

## Headless Pi implementation

A headless provider would:

1. validate capabilities with the no-model parser/resource-loader path;
2. require a fresh owned output/session directory and exact candidate cwd;
3. construct an argv array for JSON/print mode with explicit model constraints, tools, session directory, system prompt/rules, and all ambient resource types disabled;
4. apply a reviewed environment policy, removing inherited session metadata and recording names/policy identity without credential values;
5. start Pi in an owned process group with stdin closed;
6. capture bounded raw event and error streams while accounting for saved-session bytes;
7. on deadline, output limit, cancellation, or policy violation, request graceful termination, escalate within the approved grace, reap the direct child when possible, and report cleanup uncertainty honestly;
8. discover exactly one saved fresh session and return its identity/artifact references; and
9. never interpret the final report as PASS or mutate Razr state.

Role-specific packet construction and semantic audit remain caller responsibilities. The M04 Herdr implementation is limited to this approved transport proof; it does not absorb critic, primary, or workflow authority.

## M04 implemented Herdr proof scope

The M04 Herdr provider:

1. verify the installed Herdr API capabilities and select an explicitly owned pane/session topology;
2. start a fresh Pi session with the same reviewed cwd, runtime constraints, resource policy, and neutral input contract;
3. retain Herdr pane/agent identity only as operational provenance;
4. obtain a stable Pi session identity and machine-readable result artifacts through an explicit protocol, not terminal scraping alone;
5. translate Herdr observations only into provider `ExecutionStatus` values with source and timestamp;
6. use Herdr interruption controls, then inspect and report whether the process/session actually settled or disappeared; and
7. collect the same provider-neutral result fields as headless Pi.

Herdr `blocked` may indicate an interactive UI requiring human attention, but it is not a Razr blocked transition. `idle` or `done` means ready for input, not successful completion. A prompt submission acknowledgment does not prove a turn started, and a wait timeout does not prove it was not delivered; therefore automatic resubmission is prohibited.

If Herdr cannot provide fresh-context, exact-runtime, bounded-output, artifact, and cleanup guarantees equivalent to the approved request, that provider must refuse the request. The workflow must not weaken its contract to fit host telemetry.

## Provenance, privacy, and evidence

Every dispatch/result pair must bind:

- exact request bytes/digest and provider implementation identity;
- candidate repository, commit, source digest, approved contract identities, and separately scrutinized excluded inputs;
- work item/node plus the generation and attempt ordinal observed at dispatch;
- immutable adapter-owned `attempt_id` and allowance identity;
- provider/runtime selection, capability-report digest, and evidence of no fallback/retry;
- cwd, argv or equivalent structured launch request, environment-policy digest, and resource-policy digest;
- start/end/observation timestamps and provider handle/session/process references;
- original output/session/event artifact byte counts and digests; and
- interruption, limit, clipping, exit, and cleanup observations, including uncertainty.

Raw sessions, prompts, model output, credentials, machine paths, and host diagnostics are private evidence by default and stay outside public source. Public records should contain only reviewed minimum references/digests and limitations. Hashes identify bytes; they do not certify confidentiality, truth, human approval, execution, or independent review. Environment evidence should record approved names and policy, never enumerate ambient values or copy credentials. Provider handles and Herdr IDs are local operational identifiers, not globally authoritative identities.

## Retries, interruption, and recovery

There is no implicit retry. Timeout, output overflow, launch error, interruption, pane loss, process disappearance, malformed output, fallback, or cleanup uncertainty consumes the dispatched allowance under its approved rule and produces a terminal attempt observation of `FAILED`, `INTERRUPTED`, `TIMED_OUT`, or `UNKNOWN` only after the authority layer classifies retained evidence. It does not automatically change the Razr node.

A retry requires new explicit authority, a new `attempt_id`, a fresh session/output root, and a newly generation-checked request. Never reuse an allowance or session because a Razr node still says `running`, and never assume a timed-out Herdr command failed to submit. Preserve the original attempt and partial evidence. Recovery must reconcile source, generation, process/session state, allowance consumption, and evidence before deciding whether another dispatch is legal.

Consistent with Mission 03, a later adapter-owned append-only attempt ledger should record attempt identity, work/node identity, observed generation and ordinal, worker/session identity, allowance and terms, timestamps, classified outcome, and evidence references. It references Razr state; it does not impersonate it.

## Implementation gate and proof plan

M04 is implemented as an approved public execution slice, but it remains bounded proof rather than a general delivery adapter. It proves the transport boundary and stops at the actual human checkpoint; live/two-worker acceptance, fresh human review, exact specification approval, independent critics, and campaign gates remain pending and mandatory.

Focused proof for any implementation must cover at least:

- capability refusal before dispatch and no-model probing;
- fresh session/no inherited history and exact cwd/runtime/resource policy;
- candidate, contract, neutral-input, request, and session provenance;
- timeout, cancellation, output overflow, malformed/truncated events, nonzero exit, and cleanup uncertainty;
- pane/process disappearance and `UNKNOWN` handling for Herdr;
- no retry/fallback, duplicate allowance use, or reused output/session root;
- no provider-to-Razr state mirroring and stale-generation refusal;
- preserved partial evidence and source integrity; and
- identical acceptance/refusal behavior across provider implementations where the capability contract is shared.

Mocked supervisor tests diagnose mechanics but do not prove a real Pi or Herdr path. The tracer must cross the actual provider boundary, persist artifacts, read them back, and report the exact tested candidate and limitations. Process completion alone does not prove tests ran, a critic is independent, a report is correct, or a human approved it.
