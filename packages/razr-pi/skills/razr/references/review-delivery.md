# Independent review through human PR handoff

Use this only after [primary execution](primary-execution.md) has bound the fully inspected candidate and entered Verify. The core is lifecycle authority; the helper prepares and audits evidence but does not authenticate approval, independence or semantic support.

Resolve absolute paths from the skill directory:

```text
PACKAGE=<skill-dir>/../..
CORE=$PACKAGE/runtime/scripts/razr.py
REVIEW=$PACKAGE/scripts/review-flow.py
GITHUB=$PACKAGE/scripts/github-delivery.py
TARGET=<exact Git root>
WORK_ID=<exact work id>
```

Review output must be a new retained directory outside both the target and package. Do not use `/tmp`, overwrite old runs or delete failures/claims. The human chooses and reviews that path.

## 1. Re-read the candidate boundary

Run the read-only primary inspector and core validation. Confirm Verify/active, all tasks complete, candidate bound, both critics `not_due`, PR absent, clean exact candidate HEAD/branch, configured Git author, approved contract identities, and inspected digest-excluded `AGENTS.md`, `razr.yaml` and work-root inputs.

Stop on dirty/untracked source other than core lock tokens, linked/non-regular control paths, candidate/branch/source drift, unexpected prior outcomes, credentials, spending, destructive action or scope uncertainty.

## 2. Freeze neutral common inputs

```sh
python3 -B "$REVIEW" freeze \
  --target "$TARGET" \
  --work-id "$WORK_ID" \
  --output <new-reviewed-retained-directory>
```

Read `common.json`, its printed digest, source inventory, excluded inputs, declared validations, limitations and remotes. This is identity preparation, not launch authorization or human approval.

## 3. Launch fresh independent critics

The human must explicitly select and authorize provider/model/thinking, timeout and output limit. There is no billable default. Each role consumes its one allowance before process launch; failure does not authorize a retry.

```sh
python3 -B "$REVIEW" launch \
  --common <common.json> \
  --common-digest <sha256:...> \
  --role technical \
  --run <new-technical-run-directory> \
  --provider <provider> \
  --model <exact-model> \
  --thinking <level> \
  --timeout-seconds <bound> \
  --max-output-bytes <bound> \
  --authorize-live
```

Run Contract with a separate new run directory and `--role contract`. The roles may run sequentially or in parallel, but neither receives builder history or the peer report. Do not use `--continue`, resume, fork or a model switch inside a builder session. Optional Herdr hosting is transport only; Herdr state is not a Razr transition.

The launcher uses a fresh Pi session, read/bash tools only, no discovered project/package/MCP resources, explicit run-local retry/compaction settings, bounded output/time and retained JSON events. It is not an OS sandbox; inspect actual tool accesses and candidate changes.

## 4. Primary audit and outcome recording

For each original report:

```sh
python3 -B "$REVIEW" audit \
  --common <common.json> \
  --common-digest <sha256:...> \
  --role <technical|contract> \
  --run <retained-role-run-directory>
```

The audit binds the consumed role claim, original event stream, exact fresh saved session, original report bytes, provider/model, isolated candidate clone and exact observed validation tool calls. It returns `structurally_valid`, never `recordable`.

The primary must additionally inspect actual tool arguments/results, findings, limitations, candidate identity and protected before/after state, then reproduce every declared check independently in a fresh detached candidate clone with committed history but no configured remote URL. Retain bounded actual stdout/stderr and execution results with:

```sh
python3 -B "$REVIEW" reproduce \
  --common <common.json> \
  --common-digest <sha256:...> \
  --output <new-primary-reproduction-directory> \
  --primary-context <fresh-primary-session-UUID> \
  --timeout-seconds <explicit-positive-bound> \
  --max-output-bytes <explicit-positive-bound>
```

This executes repository-declared commands with an allowlisted environment and the primary's current `PATH`; the retained reproduction records a digest of that path. It is not a sandbox. Stop if the approved runner is unavailable rather than substituting another command. Structural PASS is not semantic support.

Incomplete, timed-out, malformed, drifted or unsupported work is not recordable PASS or FAIL. A substantive FAIL stops delivery. Repair requires legal return to Build, a new candidate and two new fresh reviews; never rebind old reports.

Only after one role’s original outcome is actually supported, bind the primary decision and reproduction to that retained run:

```sh
python3 -B "$REVIEW" support \
  --common <common.json> \
  --common-digest <sha256:...> \
  --role <technical|contract> \
  --run <retained-role-run-directory> \
  --primary-context <fresh-primary-session-UUID> \
  --decision-ref <faithful-decision-reference> \
  --reproduction <primary-reproduction.json>
```

A support record binds the primary decision to original review bytes and retained reproduction outputs; it is not independently authenticated approval. After both roles have structurally valid runs and support records:

```sh
python3 -B "$REVIEW" record \
  --common <common.json> \
  --common-digest <sha256:...> \
  --technical-run <technical-run-directory> \
  --contract-run <contract-run-directory>
```

The helper revalidates claims, sessions, events, original reports, isolated candidates, reproductions and support bindings; then it appends faithful candidate-bound evidence sections, invokes the generation-fenced core `critics` command and reads back stored outcomes. Preserve a refused append/recording result; do not rewrite history automatically.

## 5. Human Review-transition gate

Show the human the exact candidate commit/source digest, both stored critic records/evidence, reproduced checks, findings and limitations, current generation, and proposed Review transition. Wait for an explicit decision tied to this read-back.

After approval, freshly read the generation and invoke the core directly:

```sh
python3 -B "$CORE" transition "$WORK_ID" \
  --to review \
  --status active \
  --expect-stage verify \
  --decision <faithful-human-decision-reference> \
  --expect-generation <fresh-generation>
```

Read status and validation back. A file, hash, flag or old broad approval is not this decision.

## 6. Explicit push and PR creation

Remote/network/credential actions require separate human authorization. Confirm exact repository, remote, base, head, full reviewed candidate and commit range. Use only a normal non-force push of the reviewed head; do not publish unrelated commits or change credentials/configuration.

Before the first push, inspect locally: Review state/generation, stored dual PASS, candidate and excluded inputs, configured remote identity, base remote ref, local head, full `base..head` commit list and absence of unrelated commits. A new head branch is expected to be absent remotely; do not call `snapshot` until it exists.

After the separately authorized non-force push, obtain a bounded snapshot with an explicit base—there is no default:

```sh
printf '%s\n' '<snapshot JSON>' | python3 -B "$GITHUB"
```

```json
{"schema_version":1,"operation":"snapshot","root":"<target>","branch":"<head>","base":"<base>"}
```

Require repository/head/source identity to equal the reviewed candidate, freshly re-read Review state and both stored PASS records, then submit an explicit create request containing `repository`, `base`, `branch`, `base_sha`, `head_sha`, `source_digest`, `title` and `body`.

Create never compensates automatically. If post-create verification is uncertain, preserve `CREATED_BUT_UNVERIFIED`; its structured details retain the known exact PR URL, repository, branches, OIDs and source digest without claiming verification. Ask the human. A separate `close` operation may be used only after a new explicit decision and exact repository/URL/base/head/OID rebinding.

Read the returned PR again with the helper's `read` operation. Candidate commit, both reviewed commits, remote head, stored head and source identity must match exactly.

## 7. Record and hand off the PR

Freshly read the core generation, then record the open PR as pending:

```sh
python3 -B "$CORE" pr "$WORK_ID" \
  --url <exact-url> \
  --head-commit <exact-reviewed-head> \
  --outcome pending \
  --expect-generation <fresh-generation>
```

Read status/validation and live PR observations back. Empty checks/reviews are observations, not approval. Present the open PR, exact head, checks, review/thread state and limitations for final human PR review.

**STOP.** Do not merge, deploy, publish, force-push, mark ready/merged, or infer final approval. Those remain human-owned and separately authorized.
