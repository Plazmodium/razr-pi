# Testing Razr-Pi

## Prerequisites

Provide a POSIX environment, Python 3.11+, Git, Node, and an installed Pi with its package resource-loader API. Razr-Pi does not install prerequisites, access credentials, or invoke a model during the public test commands. Pi version is diagnostic; capabilities are tested directly.

## Public verification

From the repository root:

```sh
python3 -B tests/check_public_tree.py --root .
python3 -B tests/run_public.py --phase tracer --require-pi
python3 -B tests/run_public.py --phase full --require-pi
```

- `check_public_tree.py` validates the exact public allowlist, file modes, links, package declarations, fixtures, and provenance pins.
- The tracer uses Pi's actual local-package resolver with network blocked and without constructing an agent, client, auth store, or model runtime. It then verifies explicit adoption, discovery, and core-backed status/validation read-back.
- The full phase reruns the tracer, creates an allowlist-only isolated source copy, and executes the selected regression suite.

Missing prerequisites, missing fixtures, import failures, unexpected skips, or failed assertions fail the run.

## Focused checks

```sh
(cd tests && python3 -B -m unittest -v test_context test_memory)
(cd tests && python3 -B -m unittest -v test_herdr_provider)
```

For the abbreviated Pi/core/GitHub integration check, use a physical owned fixture path:

```sh
physical_root="$(pwd -P)"
dogfood_root="$(mktemp -d "$physical_root/.test-output/dogfood.XXXXXX")"
mkdir "$dogfood_root/fixtures"
(cd "$physical_root/tests" && PUBLIC_TEST_ROOT="$dogfood_root/fixtures" \
  python3 -B -m unittest -v \
  test_pi_surface test_primary_execution test_github_delivery)
```

Run focused commands only in an owned checkout. The Herdr suite tests provider mechanics with real subprocess/Git fixtures and simulated host responses; it does not launch a live worker or model.

## Isolation

Tests use owned directories under `.test-output`, isolated HOME/XDG/Git/agent locations, disabled global/system Git configuration, bounded subprocesses, and allowlisted environments. The runner excludes model/provider environment, `PYTHONPATH`, `NODE_OPTIONS`, ambient Git overrides, hooks, fsmonitor, and interactive credential prompts.

The full runner exports exactly `tests/public-tree.txt` into a fresh source directory without Git history or private output. That verifies source isolation but is not itself a Git clone. Delivery verification should additionally use a fresh clone of the exact pushed commit.

When `.git` is present, read-only Git checks require a valid exact repository root and supported normal or linked-worktree metadata. Traversal, linked administration files, separate external Git directories, malformed metadata, ambiguous registrations, and failed Git identity queries are refused.

## Coverage map

| Module | Main scope |
|---|---|
| `test_adoption` | Exact-root adoption safety, preservation, idempotence, work-root handling, relocation |
| `test_clean_package` | Actual no-model Pi loading, resource closure, no implicit adoption |
| `test_contract_preflight` | Structural contract identities and evidence-authority conflicts |
| `test_primary_execution` | Core-backed inspection, generation fences, candidate and delivery identity |
| `test_provenance` | Core/template/helper pins, relocation, missing or changed files |
| `test_public_boundary` | Exact tree, links, paths, output ownership, fixtures, repository identity |
| `test_context` | Immutable-candidate context selection, bounds, dirty-source refusal, stale Memory |
| `test_memory` | Record protocol, repository scoping, bounds, locking, corruption and concurrency |
| `test_pi_surface` | Typed Pi command status/context behavior and standalone fanout refusal |
| `test_herdr_provider` | Capability, authority fences, attempts, recovery, subprocess and cleanup mechanics |
| `test_github_delivery` | Repository/PR identity, bounded reads, create recovery, compensation and cleanup |
| `test_named_regressions` | Named callback outcomes, subcases, fixture errors and category mappings |

The fixtures are synthetic. They do not represent real human approval, a genuine critic decision, or a live PR operation unless a separately identified integration explicitly performs one.

## What passing tests prove

Passing tests establish only the selected source, fixtures, commands, and observed environments. They do not prove:

- complete security or confidentiality;
- a live model or Herdr worker ran;
- remote checks ran;
- GitHub collections are complete or atomic;
- a specification or review was approved by a human;
- merge, publication, or deployment is authorized; or
- behavior on untested platforms.

Preserve failure output and do not silently retry or relabel incomplete runs. Any source change requires fresh exact-head verification and review.
