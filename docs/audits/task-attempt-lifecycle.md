# Task and worker-attempt lifecycle audit

## Scope and conclusion

This audit distinguishes three authorities:

1. **Public Razr behavior** — the tracked core snapshot and public adapter/tests in this repository.
2. **Local historical evidence** — external workshop records for the Markdown-link checker. These records are observations, not part of the public package or portable proof.
3. **Herdr operational state** — live pane/agent telemetry from an external process host. It is neither Razr state nor acceptance evidence.

**Conclusion:** Razr's node state is a logical workflow record, not a process monitor. The core has an `attempt` counter, but no attempt entity, worker/session binding, allowance identity, or per-attempt outcome. The Markdown-link incident was therefore representable only as “node still `running`” in Razr plus a separate historical record that one bounded worker had stopped and its allowance was consumed. That separation was operationally correct, but it is not self-explanatory from public status output and is vulnerable to misinterpretation.

No core or adapter implementation should be added now. The public adapter deliberately has no task runner, scheduler, recovery dispatcher, or Herdr integration. This audit is the minimal clarification. A structured attempt ledger belongs in a later, explicitly approved execution-provider/Herdr slice, outside the unchanged core unless a separately approved core contract requires otherwise.

## Public Razr behavior

### What `task = running` means

The relevant value is a **node** status under `state.nodes`, distinct from the work item's top-level `status` (`active`, `blocked`, and so on).

The core:

- initializes each node as `pending` or `waiting`, with `attempt: 0` and `result_ref: null` (`packages/razr-pi/runtime/scripts/razr.py`, `initial_state`);
- permits `pending -> running`, `running -> complete|blocked|failed`, `blocked -> running|failed`, and `failed -> running`;
- checks dependency completion before entering `running`;
- increments the node's integer `attempt` whenever a legal transition enters `running`;
- increments the generation for every transition; and
- requires declared validation attestations in referenced evidence before entering `complete`.

Accordingly, `running` means only: **a generation-fenced core command recorded that this unresolved logical node entered a run**. It does not establish that an OS process, Pi session, model request, or Herdr pane is currently active. The core has no heartbeat or reconciliation with any of those systems. If a worker exits without a later authorized core transition, `running` remains stale but valid.

A node in `failed` is also logically unresolved: the core permits it to return to `running`. `blocked` likewise describes node progress, not a Herdr approval UI. Only `complete` has the core's node-evidence requirement.

### State exposed and state omitted

The canonical node shape is only:

```text
status
attempt
result_ref
```

The public core does **not** store:

- an attempt ID or append-only attempt list;
- worker, process, pane, or session identity;
- start/end timestamps per attempt;
- allowance identity, budget, or consumption;
- exit code, timeout, interruption, or unknown outcome per attempt; or
- a binding from one attempt to its diagnostics.

The integer `attempt` is therefore an ordinal count, not an identifiable attempt. It is reset to zero when a return to Build resets nodes, so it is not durable history across lifecycle revisions. `result_ref` belongs to the node and is not a per-attempt record.

The ordinary `status --json` output reports work-item stage/status and candidate/PR summary, but omits node status and attempt count. The read-only `primary.py` helper includes the full state, yet explicitly provides observation only and infers no execution, checkpoint, or allowance authority.

### Public adapter and test boundary

The tracked adapter is adoption/discovery and read-only inspection only. `docs/architecture.md` explicitly says there is no background fleet, scheduler, recovery dispatcher, or factory. `docs/capabilities.md` warns that unchanged runtime commands do not grant execution authority.

The selected public tests verify synthetic materialization, read-only inspection, and stale-generation refusal. They do not claim a worker-attempt lifecycle or process liveness. This is consistent with the advertised boundary; it also means attempt semantics are not currently a tested public capability.

## Local historical evidence: Markdown-link checker

The external revision-3 records demonstrate the ambiguity without changing the public contract:

- The approved packet authorized one bounded worker session with separate startup and task limits and no retry.
- The core legally recorded `check-local-links` as `running`, incrementing its ordinal to 1.
- The worker's declared suite failed during isolated unittest discovery and the worker stopped without a completion/failure transition.
- The coordinator's retained read-back recorded Build/active generation 10, node `running`, `attempt: 1`, null result/checkpoint, the worker pane left open, and allowance `CONSUMED`.
- The retained result correctly stated that the worker was stopped although the node remained `running`; it did not hand-edit state or fabricate completion.

Those facts are spread across the external packet, exact approval, agent-start/prompt result, saved session summary, failed-suite record, `RESULT.md`, and primary read-back. Together they identify that historical worker/session and its failure sufficiently for a local forensic narrative. Razr itself does not bind those records to attempt ordinal 1, and “consumed” is a coordinator assertion governed by the approved Markdown packet rather than a core-enforced field.

The live target has since advanced through a separately authorized continuation: the currently inspected canonical state is generation 11 with the node complete and a revision-3 evidence reference. That later result must not overwrite the historical observation that generation 10 was running after the earlier bounded worker stopped. Historical records and current state answer different questions.

This local material is not imported into the public tree and is not evidence that the public package implements portable attempt tracking.

## Herdr operational state

The external Pi integration reports only operational agent state. Its wrapper sends:

- `working` while Pi has an active turn;
- `blocked` while a recognized blocking UI is active; and
- `idle` after the agent settles;

along with pane identity and, when available, a Pi session path or session ID. Herdr's wider API may render `done` and `unknown`; `idle` and `done` mean ready for input, while `unknown` does not prove completion.

A read-only live inspection during this audit showed that Herdr can report some agents as working, idle, or done independently of any Razr item. This confirms separation, not correlation. Herdr state is transient process/session telemetry:

- `working` does not imply a Razr node is `running` or authorized;
- `idle`/`done` does not imply node success, completion, or allowance availability;
- `blocked` does not imply the Razr node status `blocked`; and
- disappearance of a pane or process does not select `FAILED`, `INTERRUPTED`, or `TIMED_OUT` for Razr.

The overlapping everyday meanings of `running`/`working`, `blocked`, and `complete`/`done` create a real future integration risk. An adapter must label each namespace and must never mirror Herdr state into Razr automatically.

## Answers to the mission questions

| Question | Finding |
|---|---|
| What does `task = running` mean? | A core transition recorded that the unresolved logical node entered a run. It is not a liveness assertion. |
| Is worker execution state stored separately? | Not in public Razr. Herdr stores operational telemetry separately; local historical records retained worker observations separately. |
| Are individual attempts identifiable? | Not in core. `attempt` is only a resettable ordinal. External session/pane records can identify a local worker run, but there is no canonical binding. |
| Is allowance consumption durable and attributable to a specific attempt? | Not in core. The historical packet/read-back make a durable local narrative, but consumption is neither schema-enforced nor portably bound to a Razr attempt. |
| Can a human distinguish an active OS process? | Not from Razr. Query the process host/Herdr, understanding it is a point-in-time observation. |
| Can a human distinguish an unresolved logical task? | Partly. Any non-`complete` node is unresolved, but ordinary status output omits nodes and stale `running` is easy to overread. |
| Can a human distinguish a failed attempt? | Only from external evidence, unless someone explicitly moves the node to `failed`; that node status is not a per-attempt outcome record. |
| Can a human distinguish an interrupted attempt? | No canonical public representation. |
| Can a human distinguish consumed allowance? | Only from separately governed external records, not public Razr state. |
| Could Herdr state be confused with Razr state? | Yes. Similar labels have different authorities and meanings. |

## Recommended minimal change

### Now

Keep this as a documentation-only finding. Do not alter the provenance-pinned core, invent new core states, or advertise worker execution in the public adapter. When presenting state, use explicit labels such as:

```text
Razr node: running (logical record; liveness unknown)
Herdr agent: idle (operational snapshot only)
Attempt allowance: consumed (external approved record)
```

Do not infer one line from another.

### Later execution-provider/Herdr slice

Before dispatching real work, define an adapter-owned, append-only attempt record with at least:

```text
attempt_id
work_id and node_id
core generation and node attempt ordinal observed at dispatch
worker/session identity (pane identity is optional operational metadata)
allowance_id and bounded terms
started_at and finished_at
outcome: SUCCEEDED | FAILED | INTERRUPTED | TIMED_OUT | UNKNOWN
evidence references
```

Required semantics:

1. Create a new immutable attempt identity for each authorized dispatch; never reuse an allowance or session because a node still says `running`.
2. Record allowance consumption at dispatch (or another exactly specified boundary) and preserve it despite timeout, crash, pane loss, or later retry.
3. Treat process/Herdr state as observations attached to the attempt, never as Razr transitions.
4. Record `UNKNOWN` when termination cannot be classified; do not convert absence into failure or success.
5. Let an authorized primary inspect evidence and perform any generation-fenced Razr transition separately. A successful process exit alone does not complete a node, and an interrupted process need not invent a new core state.
6. Display current logical node, latest attempt outcome, allowance, and live operational state in separate fields with timestamps/source authority.
7. Add focused tests for timeout, interruption, pane disappearance, stale generation, duplicate dispatch/allowance reuse, and successful evidence-bound completion before claiming execution support.

Whether this ledger eventually becomes core state is an architecture decision requiring an approved schema/migration contract. The minimal safe design for the planned Herdr boundary is adapter-owned metadata referencing, but not mutating or impersonating, the current core lifecycle.

## Verification and limits

Inspection was read-only for tracked core/adapter/tests, external workshop records, the installed Herdr wrapper, and live Herdr status. No model, worker, lifecycle mutation, product/regression suite, remote operation, or external-file edit was performed.

Verified against start SHA `b030d675fc5ced54a3b16b3098ab356b3138039c`:

- core initialization, validation, transition graph, attempt increment/reset, generation fence, evidence gate, status output, and CLI arguments;
- public architecture/capability/testing claims and focused stale-generation test;
- external revision-3 packet/result/read-back and the later current canonical state;
- installed Herdr wrapper state reporting and a read-only live status snapshot.

The static public-tree checker was run once and refused as expected with `unlisted directory: docs/audits`: the exact public allowlist does not yet include this new audit, and the user-authorized write boundary forbids updating `tests/public-tree.txt`. Integration must either authorize the audit as a public source entry and update the allowlist in a separate compatible change, or keep the audit out of an allowlist-enforced release candidate. This audit does not authenticate historical human decisions, certify live process history, or implement the recommended ledger.
