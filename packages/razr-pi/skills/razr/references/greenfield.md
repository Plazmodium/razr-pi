# Greenfield task → exact approval (before implementation)

A task description authorizes discovery, not an exact canonical approval. When asked to use Razr on an empty folder, keep the workflow selected. **Do not silently switch to direct implementation** when artifacts are missing or the human says the task itself is approved. Explain the distinction once and offer the preparation below. If the human explicitly chooses ordinary coding instead, say it will not count as a Razr workflow run; do not later retrofit its tests into a verified-task checkpoint.

## Establish the project

Inspect the absolute target, entries and `git rev-parse --show-toplevel`. A folder inside another repository is not an independent root. Obtain explicit authorization to initialize that exact directory and adopt it; a request already explicitly granting this scope needs no redundant confirmation. Initialize only that authorized local project, then use [safe adoption](adoption.md). Never adopt the enclosing workspace instead.

After `git init`, repeat the Git root check. Parent repository-local identity may no longer be effective in the new repository. Privately inspect `git config --get user.name` and `git config --get user.email` **inside the new root**. Both values must be nonempty; treat exit 1/empty output explicitly as missing, not as a successful preflight just because a shell `&&` list continued. Do not display identity values in logs. Missing identity does not prevent discovery/task proof, but report the future commit blocker early. Do not invent identity, copy parent configuration or write Git config; only the human may configure it or explicitly authorize a bounded identity operation. Recheck before each commit.

## Prepare the pair without executing

Use the bundled core `idea` to establish discovery. Read package-local `runtime/templates/spec.md`, `runtime/templates/executable.yaml` and core schema guidance as needed. Do not search workshop projects for ready-made evidence or depend on their constants. Templates are a starting point, not an executable approved task.

Draft the approval pair in an explicitly selected separate approval directory. Keep the canonical discovery item exploration-only until exact approval. Map every normative statement verbatim to the bounded executable DAG and proof obligations. Define validation commands and output/exit expectations explicitly. Review edge cases such as decimal syntax, whitespace, Unicode, underscores and input length rather than assuming a language parser equals the product contract.

Use this authority wording, or an equivalent with the same meaning:

> The unchanged Razr core exclusively materializes lifecycle artifacts and mutates `state.yaml` through approval, generation-fenced transitions, candidate recording and Verify entry. Never hand-edit `state.yaml`. After actual checks, the primary appends observed proof to core-created `evidence.md`, preserving prior evidence; the core validates references when recording completion. Neither instructions nor a successful CLI attestation proves execution.

Do **not** prohibit editing both “state or evidence”: that contradicts the supported primary evidence procedure. If an already-approved contract has this conflict, stop that work item and prepare a revised exact pair; no silent reinterpretation, history rewrite, or retroactive success claim.

Run the package's read-only preflight:

```sh
python3 "$PACKAGE/scripts/contract-preflight.py" --pair "$TARGET/approval/<item>"
```

Inspect its exit, errors and identities. It uses the core executable validator plus a narrow known-conflict diagnostic; it does not certify all semantic consistency or enforce approval. Still inspect normative mapping, scope, product semantics and all checkpoints manually.

## Identity and decision record

Present full, explicitly labelled identities:

- `spec_file_sha256`: raw spec bytes; this binds executable `source_spec.digest`.
- `executable_file_sha256`: raw executable bytes for byte-identical application.
- `executable_core_digest`: the core's parsed canonical-JSON identity, used in state.

The two executable hashes normally differ. Never interpret their difference as drift, substitute one for the other, or guess the core serialization. Re-run preflight and compare the same named fields immediately before application.

If preparing approval-ready frontmatter, label its metadata as **prepared, not observed approval**. Prepared timestamps are not the timestamp of a later human decision. Once the human approves the displayed exact pair, preserve its bytes. Retain the actual decision separately with an actually observed timestamp or an explicit “timestamp unavailable” limitation; never copy the prepared timestamp and call it observed. Approval of a displayed pair can be referenced by its unambiguous full identities; an unclear selection must be clarified, not guessed.

Only then apply that byte-identical pair, invoke the real core `approve`, inspect materialized state and follow [primary execution](primary-execution.md). Keep actual first-task human inspection, independent critique and final human PR review. Preparing, linting or approving this pair does not authorize a model budget, remote operation or PR.
