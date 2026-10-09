# Public bootstrap tests

## Prerequisites and commands

Human-provided POSIX environment, Python 3.11 or newer, Git, Node compatible with installed Pi, and the actual Pi package resource-loader API. No automatic installation, network, model or credential access. Pi version is diagnostic, not a release-number gate. There is no npm build script.

From the public candidate root:

```sh
python3 -B tests/check_public_tree.py --root .
python3 -B tests/run_public.py --phase tracer --require-pi
python3 -B tests/run_public.py --phase full --require-pi
```

The workflow must obtain the actual first-task human checkpoint before expanding beyond the initial tracer. This runner does not authenticate human approval or require importing private checkpoint history. The full command performs a fresh tracer followed by public regression execution, not publication or a delivery lifecycle.

## Source and test isolation

Before package/core subprocesses, the runner checks the exact public allowlist, modes, links, required fixtures, package declarations and immutable core/template/helper bytes. Ignored/untracked source extras are still rejected. Private owned output and Git administration are not source export entries. Source-only exports without .git remain valid source trees, not evidence of repository identity.

When .git is present, bounded read-only Git queries must confirm a non-bare exact root, administrative directory, common directory and unique worktree registration. A normal .git directory must be a real repository. A linked-worktree file must name a regular nonlinked entry directly under the primary repository's .git/worktrees directory, with intact HEAD, the standard commondir and reciprocal gitdir backlink. Pointer traversal, symlinks, malformed/missing metadata, external/separate gitdirs, ambiguous identities and failed/unavailable Git queries refuse. Traversing relative worktree pointers, bare common repositories, submodule gitfiles and separate-git-dir layouts are deliberately unsupported, not silently normalized.

Git subprocesses receive no ambient Git overrides, global/system configuration or provider environment; optional locks, fsmonitor and hooks are disabled. Git interprets repository-local configuration to derive identity; the checker does not export its contents or inspect credentials. These checks are not a concurrent path-swap sandbox or repository-ownership authentication.

Tests use exclusively created run directories under .test-output, allowlisted child environments, isolated HOME/XDG/Git/agent locations, disabled global/system Git configuration and Python -I/-B child execution. Only resolved Python, Git and Node aliases enter child PATH; there is no model CLI alias, inherited provider environment, NODE_OPTIONS or PYTHONPATH. Test repositories are initialized without author lookup, staging or commits. Focused worktree fixtures use Git's --orphan worktree support to produce genuine metadata without commits; an installed Git lacking that capability fails rather than skips. Logs and any dynamically resolved machine paths stay private and untracked.

The full runner exports exactly tests/public-tree.txt to an owned source directory without Git history or private output, confirms its source identity, and executes the named modules in a new isolated child from that tree. Fixtures and shared test support resolve only there. Existing run destinations are never reused. Linked or unowned output roots refuse. Isolation is an inspected procedure, not an adversarial OS sandbox.

## Actual integration and regression scope

The tracer uses the actual installed Pi local-package resolver, not direct skill injection. Exactly one razr skill and one Razr extension, zero prompts/themes/context files, unchanged package/project/configuration and no implicit adoption are required. Network attempts in the loader child throw. No agent, auth store, client or model runtime is constructed. Explicit adoption then creates routing/configuration; discovery must contain only spec.md and validate through the actual core.

The selected public modules are:

| Module | Reviewed proof scope |
|---|---|
| test_adoption | Wrapper safety, preservation, idempotence, duplicates, exact roots, custom work roots, separation and relocation |
| test_clean_package | Actual no-model resolver, no adoption, missing/linked skill, resource/install broadening, unavailable Pi/layout and absent review/factory helpers |
| test_contract_preflight | Synthetic pair, raw/core identities, evidence-authority conflict, drift, missing/linked files and required critique gate |
| test_primary_execution | Core-materialized synthetic item, read-only null-candidate inspection, discovery/unknown/linked/malformed/drift refusal, stale-generation fence, and exact PR-head/candidate/dual-critic commit equality across equal-source-digest commits |
| test_provenance | Six immutable core/template hashes, retained helper/provenance pins, relocation, missing/drifted files, unchanged license and file modes |
| test_public_boundary | Exact tree, ignored private extras, missing fixtures before execution, synthetic privacy patterns, links, traversal, output ownership, imports, resources and fixture drift; genuine normal/linked repository identity, ambient-Git override isolation, malformed/outside/missing/linked administration and query-error refusals |
| test_context | M03 tracer and deterministic bounded selector: clean candidate binding, immutable commit-tree source/tests/config/Git selection, dirty/untracked refusal, mutate-and-restore isolation, omission limits, foreign/stale Memory markers and closed safe-path requests |
| test_pi_surface | Pi-facing typed workflow context/status readback and standalone Herdr refusal |
| test_named_regressions | Actual callback outcomes, subcases, fixture errors, skips/failures and explicit coverage mappings |
| test_github_delivery | Bounded GitHub snapshot/read/create boundary: exact repository/base/head/source identity, host-pinned refs, required-check vocabulary/exits, provider schemas, two-pass stability, explicit incomplete collection metadata, candidate-bound recovery/compensation, and subprocess cleanup |

Missing prerequisites/fixtures, import errors, failed tests, unexpected top-level skips/expected failures and unobserved module categories fail the runner. It retains named observations, source identities and raw-log hashes. Nested collector probes deliberately exercise failures/skips in isolated in-memory results; they are not outer-suite acceptance failures. Module observations identify selected coverage, not exhaustive security or semantic certification.

The public contract is newly authored, synthetic and unapproved. Test-owned copies may carry clearly synthetic approval metadata; actual unchanged-core commands alone materialize state/evidence. No positive task completion, candidate commit, genuine critique or PR operation is inferred from those fixtures.

### M04 Herdr proof scope

M04/M09 are implemented and covered by focused provider regressions plus the full public runner. The proof includes structured capability/model/worktree/agent parsing, a closed caller/target identity schema, caller-environment verification, target-worktree binding with genuine success/mismatch cases, both identities in handles/private records/new ledger events, legacy target-only ledger replay without new-operation authority, core-read current generation/ordinal/candidate refusal before worktree-open or dispatch mutation, a final read-only pre-start candidate verification that cannot reopen a missing worktree, duplicate preflight before claims, transactional descriptor-rooted/no-follow pre-START staging and launch-failure evidence, linked `.razr`/`evidence` refusal without outside writes, STARTED-owned permanent root IDs and canonical paths across cleanup/recreation, fail-closed missing/truncated/replaced root-claim journal state before new STARTED plus authenticated prepared+STARTED crash completion, injected failure after every evidence/root-claim/marker step, exact retry/no-double-launch checks, restart reconciliation of post-START crash windows, durable binding-authoritative late sessions across restart, explicitly propagated per-launch deadlines with concurrent distinct-deadline coverage, explicit fresh context, streamed output limits, process-group cleanup with atomic PID-fixture stress coverage, linked-executable refusal, cross-pane orchestration, pending/bound private session evidence, append-only single settlement across restart/race tests, bounded artifact collection, idempotent interruption, delegated cleanup, equal empty-root manifest digests with distinct root IDs/paths, and truthful UNKNOWN handling. The automated suite does not itself certify a live model turn, human approval, independent critique, or campaign release gates. The separate M08 campaign observation in `docs/campaigns/razr-pi-next.md` records the bounded live two-slice dogfood and its limitations.

### M09 Memory hardening scope

`tests/test_memory.py` covers the revision-6 closed Memory error subset and retry semantics, cooperative-lock/same-UID threat boundary with feasible post-flock replacement detection (including the non-rollback post-write case), live core repository/source derivation for every operation, expected-only constructor identities, old-explicit-digest refusal without a callback, foreign-record omission, total byte/record/line bounds, malformed/truncated chains, exact sibling-lock symlink/contention behavior, restrictive modes, append/fsync failures, concurrent writes, evidence identity races, and common secret-like patterns. The filter is deliberately heuristic: passing it does not certify confidentiality or authorize storing credentials. Directory fsync is exercised by the creation path; no automated test can prove physical crash durability on every filesystem.

## M07 GitHub delivery proof scope

`tests/test_github_delivery.py` is the focused regression suite for the shipped `packages/razr-pi/scripts/github-delivery.py` helper and its adapter. It covers the read-only `snapshot` and `read` operations plus the explicitly requested `create` boundary in mocked/owned fixtures. The helper requires explicit repository, base branch, head branch, full base/head SHAs, and source digest for create/read binding; it performs `gh auth status` but does not expose credentials.

The delivery observation is bounded and non-atomic. PR scalar identity, GitHub API base/head refs, source digest, required checks, review threads, and visible PR collections are observed in two passes and movement refuses. Review/comment/request/status-rollup collections returned through `gh pr view` carry explicit `complete: false` metadata because that provider response supplies no page information; identical bounded prefixes are not treated as complete collections. Review-thread pagination is separately bounded and validates page advancement and total limits.

Required checks are classified from actual `gh` exit semantics. No checks is an observed `none` status, not proof that tests ran or passed. Create requires separate explicit authority and may report `created-but-unverified` with compensation unavailable/uncertain after transport/output/recovery failures. Confirmed compensation closes only a target independently bound to the attempted repository, PR number/URL, base/head branch names, and base/head OIDs. The helper is not a PR factory and does not merge, deploy, mutate Razr lifecycle state, infer approval, launch critics, or replace final human PR review.

The focused suite and authenticated product read prove only the exact source, provider observations, fixtures, and commands actually executed. They do not prove atomic GitHub snapshots, complete provider collections, human approval, tests having run remotely, merge authorization, deployment authorization, or final campaign acceptance.

## Proof limitations

Do not publish .test-output, raw logs, machine paths, preservation inventories or private decisions. A matching ownership marker is not authentication. Preserve failure output; do not retry silently or relabel an incomplete run. Source changes require a fresh integrated run.

This proof does not run the excluded historical suites, establish general critique/recovery/factory portability, validate other platforms or replace independent review. An allowlist-only materialized tree is not an actual post-import clone. The latter remains a separately authorized acceptance gate. Exact human specification approval, deterministic generation fencing, the first-task checkpoint, source identity and excluded-input scrutiny, fresh independent Technical/Contract critique and final human PR review remain mandatory.
