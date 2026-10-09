# Razr-Pi Next campaign log

## M00 baseline — 2026-10-08

- Public development base: `dev` at `10acf5b5b1131a76b7659d2a429a9de1d3bef07e`.
- Pre-reconciliation remote: `origin/dev` at `b030d675fc5ced54a3b16b3098ab356b3138039c`; it was an ancestor of local `dev` by eight commits (`0 8`).
- Reconciliation: normal fast-forward push, no force, followed by Git and GitHub API read-back of `origin/dev` at `10acf5b5b1131a76b7659d2a429a9de1d3bef07e`.
- GitHub auth: authenticated; no open PRs at baseline. Existing PR #1 was already merged.
- Historical workshop/lab material in the unborn primary `main` worktree was preserved and not imported into this public campaign branch.
- Campaign branch: `campaign/razr-pi-next`, created from the verified `dev` endpoint in Herdr workspace `wD`.
- Herdr 0.9.3 initially refused creation from the linked `dev` checkout (`linked_worktree_source`); creation from the primary repository workspace succeeded. This changed no source.

### Clean-clone baseline

Fresh local clone: `/tmp/razr-pi-next-m00.6vs9Vw/repo` (ephemeral machine-local path, not product evidence).

```text
python3 -B tests/check_public_tree.py --root .
PASS: 46 files; 6 core/templates matched
manifest sha256:64c6ab642bec2453a8534c6377ee4ff8b6e21caefc441811317deb6ed5d91326

python3 -B tests/run_public.py --phase tracer --require-pi
PASS: 8 commands observed

python3 -B tests/run_public.py --phase full --require-pi
PASS: 41 tests / 81 subcases; 9 commands observed
```

The fresh clone remained clean. The installed model catalog resolves campaign workers to `openai-codex/gpt-5.6-luna` with medium thinking. Herdr process state remains telemetry only; Razr workflow evidence remains authoritative.

The first direct check in the linked campaign checkout refused the already-known stale/prunable shared-worktree registration with `ambiguous worktree registration`. The registration was not removed. The exact 47-file campaign source allowlist was then materialized without Git administration into an owned temporary source tree and passed the same static, tracer and full checks: 47 files and 41 tests / 81 subcases. The command output records the generated source manifest.

## M08 integrated dogfood — 2026-10-09

- Used the shipped local Pi package in an owned repository with no Project Brain, Obsidian, MCP vault or remote. Exact user campaign authorization was recorded as the specification/checkpoint decision source; no claim was made that the user personally observed the tracer.
- The first dispatcher tracer passed before expansion. Two non-overlapping command slices then ran in separate Herdr worktrees with fresh named `openai-codex/gpt-5.6-luna:medium` workers. Their commits were integrated by normal cherry-picks and the final fixture suite passed 10 tests.
- Product attempt `att-normalize-interrupted` was deliberately interrupted and settled `UNKNOWN` with uncertain cleanup. The Razr node remained incomplete, was explicitly classified through a core transition, and replacement `att-normalize-replacement` used a new allowance/session/worktree identity. Herdr status was not used as completion proof.
- Memory `mem-6e0e96313b2d84f7649dd8fd3ba2768b` was recalled from a fresh Pi session, became stale after a material NFKC source change, and was superseded by evidence-bound `mem-d850e4bd20ffee2c0f0e71955af1f317`. Repository source remained authoritative.
- Bound fixture candidate `94874b7a72a5d2107dcb8aa3540587a2b9001b8a` at source digest `sha256:66e14d2b41d487984c5d5cf19246ead3c66557e50475e8cf294b8d3e2eb1c41f`. Fresh review attempt `att-fresh-review-2` reproduced all 10 tests and accepted the bounded contract with one retained LOW finding: invalid CLI arguments expose tracebacks. Provider termination remained truthfully `UNKNOWN`; report content, source and reproduced checks supported the recommendation.
- The fixture intentionally had no remote. Delivery links to same-campaign live PR [#11](https://github.com/Plazmodium/razr-pi/pull/11), read back as merged from head `c1ce50d42931c0a2f2bf8c28ca213650e01a53ca` to base `d68ce653fbfee7563fbdf637adf9b1f2a45892f6`, merge `e1d588bdd3d51a4ce36528a3f6aeae914f25188c`, with no reported checks or reviews. Empty collections are observations, not test or approval proof.
- Dogfood exposed and fixed two package integration defects: orchestrators could not target a distinct verified worker pane, and `/razr-context` did not construct the typed core readback when workflow context was requested. The post-fix full public runner passed 137 tests / 139 subcases.

M08 did not publish the fixture, transition it past Verify, record final Technical/Contract critic outcomes, claim final human review, merge, or deploy. Private sessions, raw output, AttemptLedger bytes and machine paths remain outside public source.
