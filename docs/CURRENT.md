# Current public state

This page is an index of authority, not a second status ledger. It describes the public `dev` source baseline; it does not import or supersede workshop history.

## Authoritative sources

Read these in order:

1. [Source baseline](../SOURCE_BASELINE.md) — current acceptance and source-identity status. The public candidate remains pending acceptance and publication.
2. [Capability boundaries](capabilities.md) — the supported behavior and the capabilities that remain absent or unverified.
3. [Testing](testing.md) — current verification commands, prerequisites, isolation rules, and proof limitations.
4. [Architecture and authority](architecture.md) — ownership of instructions, contracts, lifecycle state, evidence, and Git history.
5. [Repository overview](../README.md) and [package instructions](../packages/razr-pi/README.md) — entry points and the package-level surface.

If these sources disagree, stop rather than turning this index into a competing status record.

## Active work and latest result

There is no canonical active Razr work item, approval bundle, or live result record in this public tree. The included [public contract fixture](../tests/fixtures/public-contract/README.md) is synthetic, unapproved test data and is not current workflow state.

The local workshop has roadmap, chronology, readiness, verification, approval-bundle, and retained-result material that is deliberately absent from public `dev`. In particular, `WORKSHOP.md`, `docs/milestones-2026-10-03.md`, `docs/readiness.md`, and `docs/verification.md` are not distributed here. Local workshop observations therefore must not be presented as public-dev execution evidence, and this public index cannot identify a workshop task as a public active work item or claim its latest result.

## Supported boundary

The public candidate currently supports explicit safe adoption and discovery, no-model package/resource loading, read-only inspection, bounded context, local-first Memory, contract preflight, and selected synthetic regressions. Its user-facing workflow is idea/conversation → spec convergence → executable → build → verify → delivery, using the repository as the project brain without requiring Project Brain, Obsidian, an MCP vault, or a parallel documentation tree. Loading Pi instructions is not execution, adoption, approval, or authorization to write. Verification results are evidence only for the exact tested source and stated scope.

Live task execution, general independent-critic launching, retained-run recovery, local-history admission, automated PR/factory operation, live GitHub delivery, merge, and deployment are absent or unverified here. Exact human specification approval, generation-fenced transitions, the first verified-task human checkpoint, source-digest identity and excluded-input scrutiny, fresh independent Technical and Contract review, final human PR review, and an actual post-import clean-clone check remain required where applicable.

## Historical-record rule

Workshop chronology and approval bundles are historical records outside this public tree. Preserve their original approval or pending wording intentionally; a later decision belongs in a later authority record and must not be inferred from prepared metadata or retroactively written into historical bytes. Historical outcomes do not transfer to this baseline.

## Resume here

A future agent working on the public source should:

1. read this index and the authoritative sources above;
2. confirm the exact Git/source identity and inspect excluded inputs;
3. treat the absence of public work-item state and workshop evidence as a boundary, not as approval or completion;
4. follow the [vertical-slice policy](05-vertical-slices.md), beginning with one end-to-end tracer and stopping for the actual human checkpoint; and
5. run only separately authorized checks from [testing](testing.md), reporting actual output and limitations without importing historical evidence.
