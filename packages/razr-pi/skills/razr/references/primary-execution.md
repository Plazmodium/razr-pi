# Primary execution — exact approval, first task, candidate

This is the supported primary procedure. A fresh Pi primary reads this file plus the selected project's instructions, exact approved spec/executable, evidence and actual human decisions. No builder transcript, lab script, profile constant or prior report is needed for this segment. Pi supplies tools; the unchanged core owns contracts/state. This procedure grants no approval or review budget and does not expand narrower project rules.

## Set the paths

Resolve `../../..` from this reference's directory to the **package** directory. Set absolute paths (quote every expansion; paths may contain spaces):

```sh
PACKAGE='/absolute/path/to/packages/razr-pi'
TARGET='/absolute/path/to/approved/project'
WORK_ID='the-approved-work-id'
CORE="$PACKAGE/runtime/scripts/razr.py"
```

## 0. Apply exact approval only when it is due

If the item is already materialized and approved, skip this section. Never approve again to repair an inspection failure. For an absent project or unprepared pair, follow [greenfield preparation](greenfield.md); do not initialize, adopt or switch to direct implementation implicitly.

Before approval, read the project rules, actual core status/validation, full spec and executable, and the separately recorded human decision. The human must have approved the exact pair, not just the task description. Check positive revision, approval fields and timezone-bearing metadata, the executable's raw spec digest, verbatim normative statements, node mappings and declared checks. Tiny depth has exactly one node, named by `trajectory.first_task`. Prepared approval metadata is not the actual decision; preserve approved bytes and retain the real decision separately as described in greenfield preparation. Compare raw-file and core executable hashes by their distinct labels.

The contract must permit the primary to append actual proof to core-created evidence while reserving state changes to the core. If it forbids both, stop for an exact revised pair. Do not reinterpret or edit approved terms. Existing work that needs reapproval requires a separately authorized legal return to Spec and a newer exact revision; this is not a reset procedure.

For an authorized initial approval, apply the byte-identical approved pair to the existing discovery item. Read actual status/validation immediately before invoking the core once from `TARGET`:

```sh
(cd "$TARGET" && python3 "$CORE" status --json)
(cd "$TARGET" && python3 "$CORE" validate --all)
# Inspect both results before proceeding. Stop on failure.
(cd "$TARGET" && python3 "$CORE" approve "$WORK_ID")
```

`approve` has no generation flag. It is the only Spec → Build operation and materializes `state.yaml` and `evidence.md`; never create those files manually. Inspect the actual result and materialized files, then use section 1. A successful approval command validates artifacts, not the human decision or test execution. Stop on failure or partial state; do not retry automatically.

## 1. Orient without writing

```sh
python3 "$PACKAGE/scripts/primary.py" --target "$TARGET" --work-id "$WORK_ID"
```

The read-only command resolves paths, uses actual core status/validation and prints the materialized state, declared DAG/validation commands, generation, canonical paths and file hashes. It never runs declared commands or writes artifacts. Both relative and absolute target arguments resolve to the same absolute Git root; a subdirectory is refused. Inspect stdout and exit status; stop on failure. Do not chain a mutation after a failed check.

Read the listed files with Pi `read`, including the full spec and root instructions. Use the paths returned by the inspector; do not guess extra artifact filenames. If any read or command unexpectedly fails, stop immediately, preserve the result and report it. Do not continue with other work, try another path, repair or retry without human direction. Confirm the exact human-approved spec revision/digest and executable digest against the decision, not just the word `approved`. If not yet materialized, use section 0 first; never switch a Razr task silently to direct implementation or initialize/approve again to repair an inspection failure. A queued, blocked, failed, already-running or completed item is not a fresh pending-task invitation. Stop to establish the authorized action; never reset/retry automatically.

**Empty candidate:** core state uses `{repository: null, branch: null, commit: null, source_digest: null}`. Inspect `candidate_empty` and the original object; never assert `state.candidate is null`, use object truthiness, or coerce partial/malformed values into absence. Core validation refuses malformed/partial candidates. A bound candidate means stop this fresh-task procedure, not start another candidate.

### Retain inspection safely

Print inspection results directly unless you need to save them. Save any command logs only in a **new directory inside the approved target**; never use `/tmp`, overwrite an existing log, or delete logs afterward. Directory checks and creation must succeed before any log redirection or task command runs. If a check shows that preparation cannot succeed, stop immediately. Don’t attempt the command just to confirm the failure. Use explicit fail-fast chaining or checked exits; `set -u` alone and unchecked semicolons are insufficient. If the contract restricts log locations further, follow it. To retain inspection, use the existing snippet below after setting absolute `PACKAGE`, `TARGET` and `WORK_ID`. `.razr/private-runs` must be an inspected regular directory, not a link. Logs are not canonical evidence or automatic candidate staging inputs.

<!-- retained-inspection-start -->
```sh
set -euo pipefail
test ! -L "$TARGET/.razr"
test ! -L "$TARGET/.razr/private-runs"
mkdir -p "$TARGET/.razr/private-runs"
PROOF="$(mktemp -d "$TARGET/.razr/private-runs/primary-inspection-XXXXXX")"
(set -o noclobber
 python3 "$PACKAGE/scripts/primary.py" --target "$TARGET" --work-id "$WORK_ID" \
   > "$PROOF/inspection.json" 2> "$PROOF/inspection.stderr")
printf '%s\n' "$PROOF"
```
<!-- retained-inspection-end -->

After success, read the saved stdout and stderr before proceeding; redirecting output does not mean you inspected it. Expected negative product-test exits remain distinct from failed preparation or infrastructure commands. On failure, leave partial logs and stderr untouched, report the failure and stop; no further task work, mutation, repair or retry follows without human direction. An infrastructure failure is not permission to bypass a contract's stop rule. This is explicit local file retention, not a sandbox or new workflow runner.

## 2. Execute the approved first task

Confirm Build/active, empty candidate, declared `trajectory.first_task`, node pending and satisfied dependencies. Verify scope, stop rules and validation commands with the human-approved contract. Treat command strings as instructions to inspect, **not executable input to eval or an arbitrary-command launcher**. Stop for credentials, spending, destructive operations, production or scope decisions.

Re-run the inspector immediately before each core mutation, inspect that run's output and use the generation you actually read there. If you saved its output, read the saved file before issuing the mutation; an earlier visible result is not a substitute. Substitute the observed node/generation below; do not calculate `N+1`, reuse an earlier read, or retry after a stale-generation refusal.

```sh
(cd "$TARGET" && python3 "$CORE" transition "$WORK_ID" \
  --node '<declared-node>' --node-status running --expect-generation '<observed-generation>')
```

Inspect the command result and state. Implement only the approved behavior with Pi tools. Run every exact declared validation command from `TARGET` while the node is running; run additional contract-required checks too. Retain actual command, absolute cwd, exit status, stdout/stderr and meaningful assertions/output in an explicitly selected proof location. Expected negative-case exits must match their contract; an unexplained nonzero exit is a stop, not PASS. Do not borrow workshop logs or infer successful execution from CLI attestations.

Using Pi `edit`, append observed proof to the existing canonical `evidence.md` under a unique heading. Never overwrite existing proof or edit `state.yaml`. Each declared successful validation needs this exact standalone line (preserve the executable's quoting):

```text
## unique-task-run

- exact declared validation command => PASS

Observed command/cwd/exit/output and evidence of the required behavior.
Additional required checks and their actual expected exit/channel results.
```

The core checks evidence syntax, **not whether tests actually ran**. The primary must inspect the actual results before writing PASS. Reinspect, then:

```sh
(cd "$TARGET" && python3 "$CORE" transition "$WORK_ID" \
  --node '<declared-node>' --node-status complete \
  --result-ref 'evidence.md#unique-task-run' --expect-generation '<fresh-observed-generation>')
python3 "$PACKAGE/scripts/primary.py" --target "$TARGET" --work-id "$WORK_ID"
```

Read back completed node, first verified-task node/anchor, current generation, empty candidate, critics and PR. Report actual checks/results and limitations. **STOP at the first verified-task human checkpoint, before staging, committing or preparing a candidate.** A general advance approval of future checkpoints is not inspection of this newly observed result. Retain the actual subsequent human decision and its scope; do not invent an acceptance field in state. Tests may simulate this pause only when explicitly labelled.

## 3. After actual checkpoint acceptance, prepare the candidate

Candidate work must also be allowed by the exact contract and project routing. Otherwise stop for legal Spec revision/reapproval. Reinspect, read the recorded human decision, complete any remaining authorized nodes using the same run/evidence/fresh-generation protocol, and verify all nodes complete. The first-task checkpoint alone does not authorize remaining work or a commit outside the approved scope.

Read effective Git `user.name` and `user.email` privately from the **target repository** before **each** commit; both must be present. In a newly initialized nested repository, the enclosing repository's local identity is not inherited. Check each command's exit and nonempty value explicitly; a missing value inside a shell `&&` list must not be treated as successful merely because subsequent commands ran. Use configured identity automatically. If missing, stop and ask; never invent it, copy credentials or change Git configuration. A separately authorized transient fixture identity is not a general fallback. Inspect branch, HEAD (an unborn branch has no HEAD), full status, index and diff:

```sh
git -C "$TARGET" status --short
git -C "$TARGET" branch --show-current
git -C "$TARGET" diff --cached --name-status
git -C "$TARGET" diff --no-ext-diff --no-textconv
```

Do not require a preexisting HEAD merely to execute the first task. Conversely, do not silently create a baseline commit: some approved critic protocols require a parent/base; establish that separately before candidate construction. Stop on unexpected changes/staged files. Stage only individually reviewed approved paths with `git add -- <paths>`; never `git add .`. Inspect full staged diff/blobs, not only filenames/statistics, before the authorized commit. Required canonical files, root AGENTS/config and product must be committed byte-identically to the inspected working files. Do not stage `.state.lock`, run logs, credentials or unrelated files. Lock tokens may change on successful **or refused** core mutations; do not hash them as immutable evidence or delete/restore them after an assertion.

After the authorized commit read its full SHA and branch, inspect explicit excluded paths (`AGENTS.md`, `razr.yaml`, configured work root) and re-run affected checks on the committed candidate as required. Stop if checks change tracked source or required canonical bytes. Source digest excludes these metadata paths; digest equality alone does not prove their review.

## 4. Bind only the inspected candidate, then enter Verify

Use the approved repository identity, full 40-character implementation commit and inspected branch pointing to it. Immediately reinspect before **each** command, supply the generation actually returned, and inspect each result before proceeding:

```sh
(cd "$TARGET" && python3 "$CORE" candidate "$WORK_ID" \
  --repository '<approved-owner/repository>' --branch '<inspected-branch>' \
  --commit '<inspected-full-SHA>' --repo "$TARGET" --expect-generation '<fresh-generation>')
# Inspect again. Do not infer the next generation or continue after an error.
python3 "$PACKAGE/scripts/primary.py" --target "$TARGET" --work-id "$WORK_ID"
(cd "$TARGET" && python3 "$CORE" transition "$WORK_ID" \
  --to verify --expect-generation '<newly-observed-generation>')
python3 "$PACKAGE/scripts/primary.py" --target "$TARGET" --work-id "$WORK_ID"
```

Compare stored repository/branch/commit/source digest with the inspected commit and actual core output; retain actual status/validation and expected state/evidence deltas. Canonical state now postdates the implementation commit; that is expected. Do not automatically recommit metadata to make times appear identical. Never require locks to be unchanged. A synthetic identity is local metadata, not a remote: no push/PR/network action follows.

**STOP this procedure.** It neither launches critics nor grants model or remote allowances. If the human requests formal review, continue only with [independent review through human PR handoff](review-delivery.md). Fresh Technical and Contract sessions, same-source identity, primary semantic audit, explicit Review approval, exact PR read-back and final human PR review remain mandatory. Prior claims stay consumed; this inspector is not an automatic resume engine or an authenticated decision ledger.
