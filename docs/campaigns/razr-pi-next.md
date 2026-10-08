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
