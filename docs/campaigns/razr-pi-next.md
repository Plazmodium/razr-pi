# Release review checklist

This file is a release-review aid, not product guidance or Razr workflow state. The user-facing documentation begins at the repository [README](../../README.md).

For any release candidate, record exact evidence outside the product source and verify all of the following against one immutable commit.

## Source identity

- Exact repository, branch, and full commit are known.
- The candidate source digest is recomputed.
- Tracked files match the public allowlist.
- Excluded inputs are inspected explicitly.
- The checkout is clean before and after verification.

## Clean-room verification

Run from a new remote clone of the exact pushed commit:

```sh
python3 -B tests/check_public_tree.py --root .
python3 -B tests/run_public.py --phase tracer --require-pi
python3 -B tests/run_public.py --phase full --require-pi
(cd tests && python3 -B -m unittest -v test_context test_memory)
(cd tests && python3 -B -m unittest -v test_herdr_provider)
```

Use the physical-path fixture procedure in [testing](../testing.md) for the abbreviated Pi/core/GitHub integration check.

Retain actual commands, results, source identities, and limitations. A public-source export is not a substitute for the remote clone.

## Independent review

- Technical and Contract critics use fresh independent contexts.
- Both review the same exact commit.
- Reviewer evidence is candidate-bound and inspectable.
- Findings are repaired additively and re-reviewed.
- A model switch or a fork carrying builder history does not count as fresh independent review.

## Pull-request read-back

Verify and record:

- base and head branches;
- exact base and head commits;
- mergeability and merge state;
- reported status checks;
- review decision and visible reviews;
- review requests, comments, and threads where available; and
- provider completeness and non-atomic observation limits.

No-check or empty collection output is an observation, not proof of test execution, collection completeness, or approval. If repository-rule read-back is unavailable, disclose that limitation to the human reviewer.

## Final authority

The final human reviewer decides whether to merge. Critic recommendations, hashes, local tests, package loading, Herdr telemetry, and GitHub metadata do not authorize merge, publication, or deployment.
