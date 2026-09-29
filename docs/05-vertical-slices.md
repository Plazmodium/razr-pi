# 5. Vertical slices and tracer bullets

Status: adopted planning policy for razr-pi. This is skill/review guidance, not a new validator implementation or a claim of completed feature tests.

## Delivery unit

Plan features as thin, complete, end-to-end behaviors. A slice crosses every layer needed for its outcome and can be demonstrated and verified on its own once its declared prerequisites are available. Do not split delivery into database, backend, frontend, and a final integration issue.

Cross relevant layers, not invented ones: a CLI may traverse command input → domain logic → persistence → output; a web feature may traverse browser → API → domain logic → storage → visible result. A library can traverse its public API to an observable result without acquiring a UI or database.

A feature may contain multiple slices. Each issue delivers one bounded, verifiable increment. The initial tracer bullet establishes the thinnest useful real integration path; later slices extend behavior along that path. Thin does not mean bypassing required authorization, correctness, or data integrity.

## Planning procedure

1. Read the feature intent, repository evidence, domain vocabulary and relevant architecture decisions. Keep exploration conversational before formalizing.
2. Identify the smallest observable behavior that exercises the important integration boundary or risk. Prefer that as the foundational first task, not scaffolding with no observable outcome.
3. Draft slices with an outcome, scope/non-goals, relevant layers, acceptance scenarios, explicit blockers and end-to-end proof.
4. Keep each slice understandable and executable from a fresh task packet within a practical single-context budget. Split by narrower behavior if too large, not by technical layer.
5. Check requirement coverage across the breakdown: no dropped requirements, hidden integration issue, or duplicated ownership. Include meaningful error, permission and boundary scenarios alongside the happy path as required by the approved contract.
6. Present title, delivered behavior, blockers and proof for each slice. Refine granularity and dependencies with the human before treating the breakdown as agreed. Agreeing to decomposition is not approval of an unreviewed exact spec revision.
7. Publish issues only when authorized, in dependency order with real identifiers. Do not adopt another skill's tracker, scratch-directory queue or auto-ready labels implicitly.
8. Build the first tracer bullet through its full path, run proof, and hold the existing human first-task checkpoint. Then execute eligible later slices, parallelizing only when safe.

## Razr authority mapping

- The approved spec owns the slice's observable outcome, requirement IDs, boundaries and proof obligations.
- The existing executable owns implementation DAG dependencies, requirement mapping and validation commands; it is compiled from the approved contract.
- Existing state and evidence record progress and actual proof through the core CLI.
- GitHub issues, if used, are delivery/review references, not a competing lifecycle engine. Before remote publication, agree which tracker owns cross-issue blocking relations; do not maintain an autonomous second queue in local Markdown.

Recommended default: one independently deliverable slice → one Razr work item → one issue/PR when a tracker is used. Larger feature intent can be linked as a parent. A DAG node is an execution step, not automatically a GitHub issue. Internal tasks may be layer-specific, but the enclosing slice is not complete until its integrated outcome works.

Keep the current executable schema and maximum of 12 nodes. Express slice requirements through existing contract IDs/statements, `depends_on`, `contract_ids`, `validation` and `trajectory.first_task`; do not add unrecognized fields. Cross-work-item issue dependencies are not automatically enforced by the existing node DAG. Until an adapter explicitly supports them, the primary must verify them before dispatch. Do not claim core validation certifies semantic verticality or actual test execution.

## End-to-end proof

Each slice must name:

- Entry point and actor or calling system.
- Controlled prerequisites, fixtures and environment.
- Action/scenario and observable expected result.
- Real integration boundaries crossed, including persistence/read-back if relevant.
- Relevant failure/authorization behavior and regression checks.
- Reproducible command or repeatable demonstration procedure.
- Actual output/report, tested candidate identity and limitations.

Prefer automated end-to-end checks when feasible, backed by focused unit/integration tests for diagnosis. A manual demonstration may supplement proof; it must not silently replace an agreed automated acceptance test. Mocked unit tests alone do not prove the internal integration path. For an unavailable external service, name the substituted boundary and residual risk; keep real-provider verification outstanding when the contract requires it. A passing slice test is not proof of deployment.

The Technical Critic checks the real path, integration and regressions. The Contract Critic checks requirement coverage and whether the evidence actually proves the promised increment. Preserve both fresh contexts and the same reviewed candidate identity. Rerun affected slice and integration checks on the final integrated candidate rather than adding together branch-level PASS reports.

## Parallelism: slices, not departments

Assign a Pi worker an outcome such as “save one project and display it after reload,” not merely “do the backend.” The worker may load frontend, backend, testing and security expertise as needed.

A slice is dispatchable only when its prerequisites are satisfied, its contract is approved, and the work does not require unresolved shared-interface decisions. No blockers is necessary but not sufficient for safe parallelism.

Before parallel dispatch:

- Make schema/API ownership and compatibility explicit.
- Check overlapping migrations, shared configuration, fixtures and generated files.
- Give workers isolated worktrees and bounded write ownership.
- Serialize shared changes or establish a compatible prerequisite first.
- Pin each worker's base/candidate and define the integration order.
- Recheck dependencies and candidate identity after upstream changes.

If slice B needs slice A, B is independently verifiable on A's integrated baseline, not independent of A. Never label it unblocked just because the anticipated API has a mock. New Pi processes and Herdr panes do not resolve integration conflicts or dependency ordering.

## Example: project management

Avoid separate deliverables called “create schema,” “build CRUD API,” “build all UI,” and “integrate/tests.”

| Slice | Observable increment | Blocked by | Proof |
|---|---|---|---|
| Create one named project | Submit a valid name and see the persisted project after reload | Existing identity/access prerequisites, if required | Submit through actual entry point; read back from real test storage; reload and assert result |
| Rename a project | Change its name and see the persisted replacement | Create-project slice | Rename through entry point; reload; assert new name and applicable validation/access behavior |
| Archive a project | Archive it and observe the contracted visibility behavior | Create-project slice | Archive through entry point; reload list/detail; assert visibility and required access rules |

Rename and archive are candidates for parallel work only after the create slice's checkpoint/dependencies and shared-contract review. They are not automatically conflict-free. Required invalid-name and unauthorized scenarios belong in the relevant slice's contract, not an unspecified future “hardening” issue.

## Apply this to building razr-pi itself

A first extension slice could be: “From Pi, inspect the current Razr work item and see accurate status or a useful failure.” Its path is Pi command → existing CLI → parsed actual result/error → rendered output, with adapter tests plus a real Pi smoke test before claiming end-to-end acceptance.

Do not split it into “write all CLI wrappers,” “build all widgets,” and “connect later.” A later slice might launch one Technical Critic and return a candidate-bound report; another adds Contract critique and dual-result handling. Neither intermediate launcher slice may advertise full Razr review readiness before both critics are present and verified.

## Refactoring exceptions

Prefer small behavior-preserving prefactoring with explicit regression proof where it reduces risk; avoid a large generic foundation phase. Label enabling work honestly rather than pretending it delivers new user behavior.

For genuinely wide changes, use expand → migrate compatible call sites in bounded batches → contract. Each issue should remain verifiable and CI-green. The source describes an integration-branch fallback when batches cannot stand alone; that is not this project's default. Such a fallback requires explicit human approval, labels interim tasks as non-independent, and does not claim they satisfy the independently verifiable increment policy.

## Source and adaptation

Based on the supplied [DeepWiki explanation](https://deepwiki.com/mattpocock/skills/3.2-breaking-prds-into-vertical-slices) and the directly retrieved [to-tickets skill at 6acc160e](https://github.com/mattpocock/skills/blob/6acc160e/skills/engineering/to-tickets/SKILL.md). The upstream principles are complete paths, demoable increments, context-sized tickets, explicit blocking edges, human refinement, and expand–contract for wide refactors. The Razr authority mapping, candidate evidence requirements, conditional parallelism, and stricter handling of non-green intermediate work are our adaptation, not claims about upstream enforcement.
