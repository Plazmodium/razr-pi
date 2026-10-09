# Current public state

This page is an authority index, not a second lifecycle ledger. It describes the Razr-Pi Next M10 release candidate. Historical workshop state is deliberately absent from this public tree.

## Authoritative sources

Read these in order:

1. [Source baseline](../SOURCE_BASELINE.md) — current source-identity and acceptance status.
2. [Capability boundaries](capabilities.md) — implemented behavior, proof scope, and limitations.
3. [Testing](testing.md) — verification commands, prerequisites, isolation, and proof limits.
4. [Architecture and authority](architecture.md) — ownership of instructions, lifecycle state, evidence, host transport, and Git history.
5. [Repository overview](../README.md) and [package instructions](../packages/razr-pi/README.md) — user entry points and local package use.
6. [Campaign log](campaigns/razr-pi-next.md) — append-only campaign chronology and exact closing observations.

If these sources disagree, stop rather than turning this index into a competing status record.

## Current release-candidate status

M01–M09 are integrated on `campaign/razr-pi-next`. M10 finalizes normal product documentation, exercises the exact pushed mission head from fresh clones, and opens a mission PR back to that campaign branch. The campaign is not complete merely because mechanical checks pass.

There is no canonical active Razr work item, approval bundle, or live result record in this public tree. The included [public contract fixture](../tests/fixtures/public-contract/README.md) is synthetic, unapproved test data and is not current workflow state. No lifecycle state may be created or edited merely to mark M10 complete.

## Implemented boundary

The release candidate provides:

- explicit safe adoption and discovery;
- one Pi skill and one executable extension with five workflow/context/Memory commands;
- deterministic clean-candidate context selection from Git objects;
- local-first, bounded, evidence-linked Razr Memory;
- contract preflight and read-only core-backed workflow inspection;
- a direct optional Herdr provider with durable attempt/root evidence and strict generation/candidate/resource fences;
- a separately invoked source-bound GitHub snapshot/read/create helper; and
- no-model package loading, integration tracing, and selected public regressions.

M08 exercised the Herdr provider in owned live fixtures with separate caller/target panes, two non-overlapping worker slices, interruption/replacement, bounded fresh reviews, Memory stale/revalidation behavior, and delivery linkage. M09 then hardened exact delivery identity, immutable context, Memory identity/locking, and provider crash/filesystem/process boundaries. The [campaign log](campaigns/razr-pi-next.md) preserves exact historical observations and limitations; they do not turn Herdr telemetry into Razr authority or supply M10's final critics.

The extension deliberately has no worker command. General review dispatch, local-history admission, automatic recovery, PR factory operation, merge, deployment, and npm publication remain absent. Outside Herdr, ordinary status/context/Memory behavior remains available and fanout refuses cleanly.

## Remaining acceptance gates

Before a final campaign merge or completion decision:

1. the M10 exact pushed head must retain successful clean-room evidence;
2. fresh independent Technical and Contract critics must review that same exact head and excluded-input boundary;
3. the final campaign PR to the actual development branch must be created and remotely read back;
4. required checks and repository rules must be satisfied; and
5. a human must perform the final PR review and decide whether to merge.

No current document claims those future decisions. CLI attestations do not prove tests ran, no-check output does not prove success, and package loading does not authenticate approval.

## Historical-record rule

Workshop chronology and approval bundles are historical records outside this public tree. Preserve their original approval, failure, or pending wording. A later observation belongs in a later campaign-log entry; it must not be retroactively written into historical bytes. Historical outcomes do not transfer to this source candidate.
