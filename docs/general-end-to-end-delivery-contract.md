# General Pi–Razr end-to-end delivery, revision 1

Status: exact approval pending

## Objective

Provide one supported Pi–Razr path for an explicitly adopted, supported GitHub-backed project from discovery and exact contract approval through first-task execution, candidate binding, fresh independent Technical and Contract review, human-approved Review entry, pull-request creation/read-back, and final human PR handoff.

This path is general with respect to project identity, work ID, language, approved validation commands, branch, GitHub repository, base branch, and explicitly selected Pi model. It must contain no slug-check, workshop, `.labs`, fixed repository, fixed branch, fixed model, or fixed test-runner assumptions.

## Required behavior

### REQ-001 — one authoritative lifecycle

The unchanged provenance-pinned Razr core remains the sole lifecycle authority. The adapter may inspect, prepare inputs, launch fresh Pi processes, audit retained results, and invoke generation-fenced core commands. It must not create a second state machine, hand-edit canonical state, infer approval, or treat a CLI attestation as proof that checks or human review occurred.

### REQ-002 — existing discovery and execution gates remain

The supported path retains exact spec/executable approval, approved validation inspection, actual execution evidence, fresh generation read-back, the first verified-task checkpoint, configured Git-author preflight, explicit staging, clean committed candidate identity, source-digest identity, and scrutiny of digest-excluded `AGENTS.md`, `razr.yaml`, and configured work-root inputs.

No candidate may be prepared before the newly observed first-task result is shown to and accepted by the human.

### REQ-003 — generic frozen review inputs

After candidate binding and Verify entry, a no-model helper creates a new immutable candidate-bound common input directory. It records the exact target/work ID, core and approved contract identities, generation, candidate repository/branch/full commit/source digest, reviewed file inventory, explicitly excluded inputs, declared validation instructions, and bounded reviewer instructions.

The helper must refuse drift, partial candidate identity, dirty tracked or untracked source, linked control paths, conflicting output, unsupported repository shapes, stale generation, and mismatch between Git/core/contract identities. It must not forbid an otherwise valid configured GitHub remote merely because a remote exists.

### REQ-004 — fresh independent Technical and Contract reviews

A bounded review launcher supports exactly two roles: `technical` and `contract`. Each launch requires an explicit target, frozen input digest, role, model, thinking level, timeout, output-byte limit, and actual human authorization for model use. Defaults must not silently select a billable model.

Each role runs in a new Pi process/session with no builder transcript, previous critic report, peer report, reused session, or fork carrying builder history. Both roles receive the same immutable common candidate/contract inputs plus role-specific instructions. They may run sequentially or in parallel; neither receives the other role’s findings.

The launcher retains the exact prompt, settings, process/session identity, raw output, saved session reference when available, exit/timeout/limit outcome, and report bytes. There are no automatic retries, fallback models, repairs, or outcome recording.

### REQ-005 — report and audit boundary

Reports use one public schema with role, exact candidate/common-input identities, status `complete` or `incomplete`, substantive verdict `PASS` or `FAIL` only when complete, reproduced checks, findings, limitations, and accessed evidence summary.

Transport failure, timeout, malformed output, missing required checks/evidence, contradictory status/verdict, identity drift, or incomplete work is not a recordable PASS or FAIL.

A primary audit validates distinct fresh sessions/contexts, exact common inputs, prohibited peer/builder material absence, actual tool/access records where available, original report bytes, candidate identity, declared check reproduction, and unchanged protected inputs. Structural validation alone does not establish semantic support. Only the primary may decide that an original outcome is supported and invoke the core `critics` command with a freshly read generation.

### REQ-006 — dual-critic and Review gate

Verify-to-Review transition is permitted only after the core stores candidate-bound supported Technical PASS and Contract PASS from distinct fresh contexts and actual status/validation read-back succeeds.

The primary must then show the exact candidate, source digest, both stored outcomes/evidence references, remaining limitations, and proposed Review transition to the human. It may invoke the generation-fenced Review transition only after an explicit human decision tied to that read-back. A hash, flag, file, receipt, or prior broad approval does not authenticate this decision.

A critic FAIL or incomplete review stops delivery. Repair requires a legal return to Build, a new candidate, new frozen inputs, and new fresh reviews; old reports are never rebound.

### REQ-007 — explicit GitHub delivery

Delivery requires an explicitly selected GitHub repository, base branch, head branch, full candidate commit, title, and body. There is no default repository or base branch. Authentication, credentials, branch publication, network use, and PR creation remain separate explicit human-authorized actions.

Immediately before remote mutation, the primary revalidates Review state/generation, dual PASS records, clean exact candidate, repository/remote/base/head identity, excluded inputs, and that the remote head will equal the reviewed candidate. Push must be non-force and must not publish unrelated commits.

The bounded GitHub helper performs auth preflight, exact snapshot/read/create operations, duplicate/ambiguous-create handling, and post-create read-back. It never merges, deploys, force-pushes, changes credentials, launches critics, or infers human approval. Any compensating remote close after uncertain verification requires separate explicit authorization; it is not automatic.

After successful create/read-back, the primary records the PR through the core with a freshly read generation and exact URL/head commit, then reads status and validation back. Candidate commit, both reviewed commits, remote PR head, stored PR head, and source identity must agree exactly.

### REQ-008 — final human PR handoff

The supported automated endpoint is an open, non-merged PR presented for final human review with exact candidate/head identity, checks/review observations, unresolved limitations, and core read-back. Empty checks or reviews are observations, not proof of success or approval.

No automatic merge, deployment, release, package publication, or `ready`/`merged` claim is made. Final human PR review and any later merge remain human-owned.

### REQ-009 — Pi user experience

The shipped Razr skill and references provide one coherent procedure from discovery through PR handoff. Status output reports accurate adapter capability and next action without granting authority. Package loading remains read-only and does not adopt, execute, launch models, push, or create a PR.

The initial public implementation may be primary/runbook driven with narrow JSON CLIs; it need not add a new Pi slash command or TUI. Optional Herdr hosting may transport fresh Pi reviewer processes but is not workflow authority and is not required for standalone support.

### REQ-010 — compatibility and refusal

The implementation supports normal exact Git roots and linked Git worktrees when their Git identity is valid, while retaining existing refusal of linked/non-regular Razr control paths. Any additional repository limitation—such as submodules, source symlinks, ignored inputs, or provider capability requirements—must be explicit, tested, and reported as a refusal, not hidden behind “arbitrary projects.”

Previous-release workflow/routing state remains incompatible and requires the separately documented clean restart. No migration is introduced by this slice.

## Vertical slices

### Slice A — general candidate-to-dual-review tracer

Outcome: in a disposable non-workshop project with a different work root and validation runner, freeze one exact candidate, launch fresh simulated/no-network Technical and Contract transports, audit complete candidate-bound reports, record supported outcomes through the real core, and stop in Verify for human read-back.

Proof includes identity/drift/refusal cases, distinct contexts, peer contamination refusal, incomplete/FAIL behavior, stale generation, and unchanged core/provenance gates. No live model call is required for this tracer.

### Slice B — real fresh Pi reviews

Outcome: with explicit model/spending authorization, launch two genuinely fresh Pi sessions against the same candidate/common inputs, retain and audit original evidence, reproduce relevant checks, record supported outcomes, and stop for the human Review-transition decision.

Proof must use a non-slug project and must distinguish actual fresh review from simulated transport tests. No retry or fallback is permitted without a new human decision.

### Slice C — Review-to-PR handoff

Outcome: after explicit human Review approval and separate remote authorization, enter Review, publish only the exact candidate, create/read back the PR, record it through the real core, and present the open PR for final human review without merging.

Repeatable tests use a fixture-local fake GitHub boundary for success/refusal/recovery cases. Final acceptance also reads back the existing live PR for this exact Razr-Pi candidate; if a new live PR creation test is desired, it requires a separately selected disposable repository and explicit remote authorization.

## Required verification

1. Public-tree/provenance checks and the complete no-model suite pass from a source-only export.
2. Two unrelated project fixtures prove no fixed work ID, repository, language, validation runner, work root, branch, base, or model assumptions.
3. Full simulated lifecycle proves exact approval → first task/checkpoint → candidate → fresh distinct simulated reviews → supported core recording → human-gated Review transition → fake PR create/read-back → core PR recording/handoff.
4. Negative tests cover stale generations; contract/candidate/excluded-input drift; dirty/untracked source; linked control paths; reused/forked contexts; peer contamination; malformed/incomplete/unsupported reports; critic FAIL; missing human gates; direct delivery before Review; wrong repository/base/head; branch movement; duplicate/ambiguous PR; failed/pending checks; unresolved threads; requested changes; and uncertain compensation.
5. Separately authorized real Pi Technical and Contract sessions review the final integrated candidate and return independently audited outcomes.
6. The exact pushed PR head is read back and equals the candidate reviewed by both critics. Human final PR review remains pending and explicit.

## Non-goals

- Automatic merge, deployment, release, registry publication, credential management, provider installation, or global Pi/Herdr configuration.
- Treating Herdr telemetry, hashes, CLI output, structurally valid reports, empty GitHub observations, or model identity as proof of approval or independence.
- In-place migration of previous-release routing or workflow state.
- A sandbox for arbitrary repository commands or untrusted code.

## Approval request

Approve these exact revision-1 bytes to authorize implementation on `fix/razr-pi-breaking-recovery`, extension of PR #20, no-model tests, and the later use of fresh `openai-codex/gpt-6.1-terra` Technical and Contract reviewers for the final integrated candidate.

Approval does not authorize merge/deploy, credential changes, force-push, automatic compensation, additional live PR creation, or any model retry/fallback beyond one fresh session per final critic role.
