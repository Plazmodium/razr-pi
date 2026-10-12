# Razr-Pi source baseline

The public source consists of the exact files listed in `tests/public-tree.txt`. The package is private and intended for reviewed local loading.

## Identity

The deterministic Python core and five workflow templates are pinned by `packages/razr-pi/runtime/provenance.json`. Provenance hashes establish byte identity; they do not prove execution, review, or human approval.

Candidate identity has two separate domains:

- the full Git commit identifies the exact candidate object; and
- the core source digest identifies the covered tree content.

Delivery requires exact equality between PR head, candidate commit, and both critic-reviewed commits. Source-digest equality cannot substitute for commit equality.

## Included product surface

- Razr core and templates
- one Pi skill and one extension
- adoption and read-only workflow helpers
- context and Memory adapters
- optional Herdr execution provider
- GitHub delivery helper
- public documentation and selected synthetic test fixtures

Runtime and adapters do not depend on sibling distributions, personal paths, credentials, private sessions, or external knowledge stores.

## Verification boundary

Tests use declared synthetic fixtures and owned temporary output. Installed Pi is a human-provided prerequisite for the real no-model package-loader probe. Missing prerequisites are failures, not passing skips.

The public runner exports the allowlisted tree into an isolated source directory and runs selected regressions. This is source isolation, not a substitute for exact-head Git clone verification when delivery policy requires one.

## Publication boundary

Do not publish raw test output, machine paths, private Memory, attempt ledgers, session records, credentials, or private review evidence. Review all source and licensing before distribution.

Exact specification approval, generation fencing, first-task verification, excluded-input scrutiny, independent exact-candidate review, applicable repository checks, and final human PR review remain separate delivery gates.
