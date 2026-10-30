# Razr-Pi: developer guide

Razr-Pi is a local Pi package for taking one explicitly adopted Git repository from discovery to an open GitHub pull request for final human review.

The provenance-pinned Razr core owns lifecycle state. Pi is the primary engineering session. Package helpers provide safe adoption, read-only inspection, fresh bounded Technical and Contract review, and exact GitHub PR create/read-back. They do not authenticate approval, merge, deploy, retry failed reviews, or change credentials.

> **Breaking release:** projects adopted by an earlier Razr-Pi release are not upgraded in place. Follow [Safe de-adoption](skills/razr/references/de-adoption.md), retain or separately archive existing `razr.yaml` and `.razr/` data, and restart from the beginning in a clean repository.

## Supported endpoint

The supported operator path is:

```text
install local package
  → explicitly adopt one Git repository
  → discover and approve an exact spec/executable pair
  → execute declared work through the first-task human checkpoint
  → commit and bind one exact candidate
  → run fresh Technical and Contract critics
  → primary-audit and record supported outcomes
  → obtain human approval to enter Review
  → separately authorize and perform a non-force push
  → create and read back one exact GitHub PR
  → record the open PR and hand it to the human
  → STOP before merge or deployment
```

This path is experimental and release acceptance remains pending. Each delivered candidate still requires genuine fresh reviews and exact live PR read-back; simulated tests do not substitute for those gates.

## 1. Prerequisites

The developer owns installation, authentication, credentials, model selection, spending, repository selection, and GitHub permissions.

Required:

- Python 3.11 or newer
- Git
- Node
- an installed Pi
- GitHub CLI (`gh`) for delivery
- a normal Git repository with a configured author before candidate commits
- an explicitly selected GitHub repository, base branch, and head branch before remote delivery

Check without changing configuration:

```sh
python3 --version
git --version
node --version
pi --version
gh --version
gh auth status
```

Stop on a missing prerequisite. Do not install packages, log in, change Git identity, or modify credentials implicitly.

## 2. Install the local Pi package

Clone Razr-Pi into a reviewed tools location. From the project you intend to use:

```sh
cd /absolute/path/to/your-project
pi install --local /absolute/path/to/razr-pi/packages/razr-pi
pi list
```

The package remains at the cloned path; Pi records a local reference. Reload Pi, invoke `/skill:razr`, and request read-only orientation first.

For temporary inspection without package installation, Pi can load the skill explicitly:

```sh
pi --skill /absolute/path/to/razr-pi/packages/razr-pi/skills/razr/SKILL.md
```

Package or skill loading is read-only. It does not adopt a repository or authorize writes.

## 3. Set operator paths

Use absolute paths throughout:

```sh
PACKAGE='/absolute/path/to/razr-pi/packages/razr-pi'
TARGET='/absolute/path/to/your-project'
WORK_ID='your-approved-work-id'
CORE="$PACKAGE/runtime/scripts/razr.py"
REVIEW="$PACKAGE/scripts/review-flow.py"
GITHUB="$PACKAGE/scripts/github-delivery.py"
```

Confirm the exact Git root:

```sh
git -C "$TARGET" rev-parse --show-toplevel
git -C "$TARGET" status --short
```

A subdirectory of another repository is not an independent target. Stop on ambiguous identity, linked control paths, unexpected existing Razr files, or unrelated changes.

## 4. Explicitly adopt the repository

Read [Safe adoption](skills/razr/references/adoption.md), inspect `AGENTS.md`, `razr.yaml`, and the intended work root, then run:

```sh
python3 -B "$PACKAGE/scripts/adopt.py" \
  --target "$TARGET" \
  --work-root .razr/work
```

Read back the result:

```sh
(cd "$TARGET" && python3 -B "$CORE" status --json)
(cd "$TARGET" && python3 -B "$CORE" validate --all)
```

Adoption creates managed routing and delegates initialization to the unchanged core. It does not create an approved work item.

## 5. Discover and approve exact work

In the primary Pi session, follow [Greenfield preparation](skills/razr/references/greenfield.md):

1. Create/refine the discovery item through the core:

```sh
(cd "$TARGET" && python3 -B "$CORE" idea "$WORK_ID" \
  --title '<human-readable title>' --depth tiny)
(cd "$TARGET" && python3 -B "$CORE" status "$WORK_ID" --json)
(cd "$TARGET" && python3 -B "$CORE" validate --all)
```

2. Draft `spec.md` and `executable.yaml` as a separate approval pair.
3. Map every normative statement to the executable DAG and declared validation.
4. Run the read-only pair preflight:

```sh
python3 -B "$PACKAGE/scripts/contract-preflight.py" \
  --pair "$TARGET/approval/$WORK_ID"
```

5. Present the exact raw spec hash, raw executable hash, and core executable digest.
6. Wait for human approval of those exact bytes.
7. Apply the byte-identical pair and invoke the core `approve` command once.

Prepared approval metadata, a hash, or a successful preflight is not human approval. Preserve the actual decision separately.

## 6. Execute work and stop at the first-task checkpoint

Follow [Primary execution](skills/razr/references/primary-execution.md). Start with the read-only inspector:

```sh
python3 -B "$PACKAGE/scripts/primary.py" \
  --target "$TARGET" \
  --work-id "$WORK_ID"
```

For every lifecycle mutation:

1. inspect current state and validation;
2. read the current generation;
3. invoke one core mutation with that exact generation where supported;
4. inspect the command result and state again;
5. stop on any unexpected failure—do not retry automatically.

Run the exact declared validation commands while the node is running. Append only observed evidence to the existing `evidence.md`; never hand-edit `state.yaml` or invent PASS output.

After completing the foundational task, show the actual checks, output, evidence anchor, state, and limitations to the human. **Stop before staging or committing.** Candidate work begins only after the human accepts that newly observed checkpoint and the approved contract permits it.

## 7. Commit and bind the candidate

Before each commit, privately verify that the target repository has nonempty effective `user.name` and `user.email`. Do not invent an identity or change Git configuration.

Inspect and stage explicit approved paths only:

```sh
git -C "$TARGET" status --short
git -C "$TARGET" diff --no-ext-diff --no-textconv
git -C "$TARGET" diff --cached --name-status
```

Do not use `git add .`. Inspect the complete staged diff and blobs before the authorized commit. Do not stage lock files, private run logs, credentials, or unrelated files.

After committing, rerun required checks and follow the candidate commands in [Primary execution](skills/razr/references/primary-execution.md):

```text
core candidate → inspect → core transition to Verify → inspect
```

The stored repository, branch, full commit, and source digest must match the inspected candidate. Stop in `Verify/active` with all tasks complete, critics `not_due`, and no PR.

## 8. Freeze and run fresh independent reviews

Read [Independent review through human PR handoff](skills/razr/references/review-delivery.md) completely before launching a model.

Choose a new retained directory outside both the target and package:

```sh
REVIEW_ROOT='/absolute/path/to/new-retained-review'
python3 -B "$REVIEW" freeze \
  --target "$TARGET" \
  --work-id "$WORK_ID" \
  --output "$REVIEW_ROOT"
```

Inspect `common.json` and retain the printed `common_digest`:

```sh
COMMON="$REVIEW_ROOT/common.json"
COMMON_DIGEST='sha256:replace-with-printed-digest'
```

The human must explicitly authorize provider/model/thinking, time, and output bounds. There is no billable default. Each role allowance is consumed before launch and is not automatically restored after failure.

```sh
python3 -B "$REVIEW" launch \
  --common "$COMMON" \
  --common-digest "$COMMON_DIGEST" \
  --role technical \
  --run '/absolute/path/to/new-technical-run' \
  --provider '<provider>' \
  --model '<exact-model>' \
  --thinking '<level>' \
  --timeout-seconds '<positive-bound>' \
  --max-output-bytes '<positive-bound>' \
  --authorize-live

python3 -B "$REVIEW" launch \
  --common "$COMMON" \
  --common-digest "$COMMON_DIGEST" \
  --role contract \
  --run '/absolute/path/to/new-contract-run' \
  --provider '<provider>' \
  --model '<exact-model>' \
  --thinking '<level>' \
  --timeout-seconds '<positive-bound>' \
  --max-output-bytes '<positive-bound>' \
  --authorize-live
```

The roles must use distinct fresh Pi sessions and the same immutable common inputs. Do not use a continued, resumed, forked, cloned, or builder-history session. Do not provide either role with the peer report.

## 9. Primary-audit, reproduce, support, and record

Structural audit is necessary but not sufficient:

```sh
python3 -B "$REVIEW" audit \
  --common "$COMMON" --common-digest "$COMMON_DIGEST" \
  --role technical --run '/absolute/path/to/new-technical-run'

python3 -B "$REVIEW" audit \
  --common "$COMMON" --common-digest "$COMMON_DIGEST" \
  --role contract --run '/absolute/path/to/new-contract-run'
```

The primary must inspect actual tool calls/results, reports, findings, limitations, session identities, candidate state, and protected before/after state. Then reproduce every declared check in a new detached clone. Use the current primary Pi session UUID from `/session`:

```sh
PRIMARY_CONTEXT='<canonical-primary-session-UUID>'
REPRODUCTION='/absolute/path/to/new-primary-reproduction'

python3 -B "$REVIEW" reproduce \
  --common "$COMMON" \
  --common-digest "$COMMON_DIGEST" \
  --output "$REPRODUCTION" \
  --primary-context "$PRIMARY_CONTEXT" \
  --timeout-seconds '<positive-bound>' \
  --max-output-bytes '<positive-bound>'
```

Only complete, semantically supported original outcomes may be bound:

```sh
python3 -B "$REVIEW" support \
  --common "$COMMON" --common-digest "$COMMON_DIGEST" \
  --role technical --run '/absolute/path/to/new-technical-run' \
  --primary-context "$PRIMARY_CONTEXT" \
  --decision-ref '<faithful-primary-decision-reference>' \
  --reproduction "$REPRODUCTION/reproduction.json"

python3 -B "$REVIEW" support \
  --common "$COMMON" --common-digest "$COMMON_DIGEST" \
  --role contract --run '/absolute/path/to/new-contract-run' \
  --primary-context "$PRIMARY_CONTEXT" \
  --decision-ref '<faithful-primary-decision-reference>' \
  --reproduction "$REPRODUCTION/reproduction.json"

python3 -B "$REVIEW" record \
  --common "$COMMON" \
  --common-digest "$COMMON_DIGEST" \
  --technical-run '/absolute/path/to/new-technical-run' \
  --contract-run '/absolute/path/to/new-contract-run'
```

An incomplete review is not PASS or FAIL. A substantive FAIL stops delivery. Repair requires a legal return to Build, a new candidate, new frozen inputs, and two new reviews.

## 10. Human approval to enter Review

Show the human the exact candidate commit/source digest, both stored outcomes and evidence anchors, reproduced checks, findings, limitations, current generation, and proposed transition.

Only after explicit approval tied to that read-back, freshly inspect the generation and transition:

```sh
(cd "$TARGET" && python3 -B "$CORE" transition "$WORK_ID" \
  --to review \
  --status active \
  --expect-stage verify \
  --decision '<faithful-human-decision-reference>' \
  --expect-generation '<freshly-observed-generation>')
python3 -B "$PACKAGE/scripts/primary.py" \
  --target "$TARGET" --work-id "$WORK_ID"
(cd "$TARGET" && python3 -B "$CORE" validate --all)
```

A file, hash, flag, or previous broad approval does not authenticate this gate.

## 11. Separately authorize push and PR creation

Remote mutation is a separate human decision. Before pushing, verify:

- Review state and current generation;
- both stored candidate-bound PASS records;
- clean exact candidate HEAD;
- configured GitHub remote and repository;
- explicit base and head branches;
- remote base identity;
- the complete `base..head` commit list; and
- that no unrelated commit will be published.

Perform only the authorized normal non-force push. Never force-push or change credentials/configuration:

```sh
HEAD_BRANCH='<head-branch>'
git -C "$TARGET" push --set-upstream origin "$HEAD_BRANCH"
```

Read the remote branch back and stop if it is not the reviewed full candidate commit.

After the branch exists remotely, request a snapshot. `github-delivery.py` is a closed JSON-on-stdin CLI, not a flag-based command:

```sh
cat <<JSON | python3 -B "$GITHUB"
{
  "schema_version": 1,
  "operation": "snapshot",
  "root": "$TARGET",
  "branch": "<head-branch>",
  "base": "<base-branch>"
}
JSON
```

Inspect the returned repository, base commit, head commit, and source digest. They must match the reviewed candidate and the freshly re-read core state.

Create the PR with every identity explicit:

```sh
cat <<JSON | python3 -B "$GITHUB"
{
  "schema_version": 1,
  "operation": "create",
  "root": "$TARGET",
  "repository": "<owner/repository>",
  "base": "<base-branch>",
  "branch": "<head-branch>",
  "base_sha": "<40-character-base-commit>",
  "head_sha": "<40-character-reviewed-candidate>",
  "source_digest": "sha256:<64-hex-source-digest>",
  "title": "<pull-request-title>",
  "body": "<pull-request-body>"
}
JSON
```

If creation returns `CREATED_BUT_UNVERIFIED`, preserve the structured details and stop. The helper never closes the PR automatically. A compensating `close` requires a new explicit human decision and exact identity rebinding.

Read the PR back independently with the same identity expectations:

```sh
cat <<JSON | python3 -B "$GITHUB"
{
  "schema_version": 1,
  "operation": "read",
  "root": "$TARGET",
  "url": "<exact-created-PR-url>",
  "repository": "<owner/repository>",
  "base": "<base-branch>",
  "branch": "<head-branch>",
  "base_sha": "<40-character-base-commit>",
  "head_sha": "<40-character-reviewed-candidate>",
  "source_digest": "sha256:<64-hex-source-digest>"
}
JSON
```

Empty checks, reviews, or threads are observations, not proof of success or approval.

## 12. Record and hand off the open PR

After exact create/read-back succeeds, freshly read the core generation and record the PR as `pending`:

```sh
(cd "$TARGET" && python3 -B "$CORE" pr "$WORK_ID" \
  --url '<exact-created-PR-url>' \
  --head-commit '<40-character-reviewed-candidate>' \
  --outcome pending \
  --expect-generation '<freshly-observed-generation>')
python3 -B "$PACKAGE/scripts/primary.py" \
  --target "$TARGET" --work-id "$WORK_ID"
(cd "$TARGET" && python3 -B "$CORE" validate --all)
```

Read status and validation back before presenting the handoff.

Present to the human:

- PR URL and open state;
- exact reviewed candidate and remote head;
- source digest;
- stored Technical and Contract outcomes;
- checks, reviews, requests, comments, and unresolved threads observed by the bounded read;
- collection-completeness limitations; and
- any remaining findings or operational limitations.

**Stop at final human PR review.** Do not merge, deploy, publish, mark ready/merged, delete the branch, or infer acceptance.

## Recovery and refusal behavior

- `scripts/de-adopt.py` prepares exact original/proposed `AGENTS.md` files but never changes the project or deletes retained data.
- `scripts/primary.py` is read-only; it does not execute tasks or mutate workflow state.
- `scripts/review-flow.py` has no automatic retry, fallback, repair, or outcome recording after launch.
- `scripts/github-delivery.py` never pushes, mutates Razr state, merges, deploys, changes credentials, or compensates automatically.
- Tracked source symlinks, Git submodules, and ignored live files other than recognized Razr lock tokens are refused by the review path. Registered linked Git worktrees are supported when their identity is valid.
- GitHub observations are bounded and non-atomic. Dynamic collections are checked for short-term stability but are not claimed complete.

## Pi commands

Loading the package registers five read-oriented commands:

- `/razr-status`
- `/razr-context`
- `/razr-remember`
- `/razr-recall`
- `/razr-memory-inspect`

They expose status, bounded context, and Razr Memory. They do not grant workflow mutation, reviewer, push, or PR authority.

## Capability reference

| Resource | Implemented behavior | Current limit |
|---|---|---|
| `scripts/adopt.py` | Safe explicit adoption before unchanged core initialization | Not automatic adoption or a concurrent-path-swap sandbox |
| `scripts/contract-preflight.py` | Read-only pair diagnostics with labelled identities | Not semantic certification or approval |
| `scripts/primary.py` | Read-only core-backed work-item inspection | No task execution, decision authentication, or mutation |
| `scripts/de-adopt.py` | Prepares exact original/proposed routing files | Never applies changes or deletes/migrates Razr data |
| `scripts/review-flow.py` | Candidate freeze, fresh bounded critic launch, structural audit, reproduction support, core recording | Human model authorization and primary semantic audit remain mandatory; not a sandbox |
| `scripts/github-delivery.py` | Exact snapshot/read/create and separately authorized close | Push and core mutations remain primary-owned; observations are non-atomic |
| `scripts/pi-surface.py` | Read-only core status plus adapter boundary | Status never authenticates approval or permission |
| `scripts/named-regressions.py` | Source-bound named test/subcase proof collection | Test observations are not independent review or authenticated approval |
| `skills/razr/` | The complete operator procedure and gates | No automatic approval, retry, merge, deployment, or publication |
| `runtime/` | Unchanged deterministic core plus adapter-owned routing | CLI attestations do not prove checks or human decisions |

Pi release numbers are diagnostic metadata, not runtime gates. Missing required behavior—not an unfamiliar release number—is a refusal.

## Package verification

From the Razr-Pi repository root:

```sh
python3 -B tests/check_public_tree.py --root .
python3 -B tests/run_public.py --phase tracer --require-pi
python3 -B tests/run_public.py --phase full --require-pi
```

Passing tests prove only the selected source, fixtures, commands, and observed environment. They do not authenticate human decisions, substitute for fresh review of a delivery candidate, or authorize remote mutation, merge, or deployment.
