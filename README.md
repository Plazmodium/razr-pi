# Razr-Pi public source bootstrap

An isolated source candidate for a small, public Pi package. Full acceptance and publication are pending. See [capability boundaries](docs/capabilities.md) and [source identity policy](SOURCE_BASELINE.md).

Razr-Pi treats the checked-out repository as the project brain; it does not require Project Brain, Obsidian, an MCP vault, or a parallel architecture/component/session documentation tree. The user-facing path is idea or conversation → converged spec → executable → build → verify → delivery. Package loading supplies instructions and commands; it is not adoption, execution, approval, or a sandbox.

## Read first

- [Package instructions](packages/razr-pi/README.md)
- [Architecture and authority](docs/architecture.md)
- [Testing and prerequisites](docs/testing.md)
- [Vertical slices](docs/05-vertical-slices.md)
- [Slice template](templates/vertical-slice.md)

Use human-provided Python, Git, Node and Pi. Never install or configure them implicitly. There is no npm build or third-party Python dependency. Validation and executable integration tests constitute the source build.

Repository-native source, tests, configuration, and Git are durable project truth. Razr Memory stores repository-scoped, ledger-bounded experience and rationale, while workflow artifacts preserve intent and provenance as needed. Memory secret filtering is heuristic and does not make it a confidentiality boundary. Comments explain non-obvious local intent or invariants; they do not narrate bug history. Regression tests carry behavioral memory, and Git plus Razr Memory carry historical context.

## Prospective checks

```sh
python3 -B tests/check_public_tree.py --root .
python3 -B tests/run_public.py --phase tracer --require-pi
python3 -B tests/run_public.py --phase full --require-pi
```

The full phase reruns the integration tracer, exports only the public allowlist to a fresh owned source tree, and runs the selected regression modules there with isolated configuration. Missing prerequisites, fixtures, failures or unexpected skips fail acceptance. The first verified-task human checkpoint remains a workflow requirement, not an assertion authenticated by this test runner. Original workshop tests and historical results are not this baseline's proof.

Existing Project Brain, Obsidian, MCP-vault, or legacy workshop settings are optional inputs only; they are not required dependencies. Keep historical evidence intact, and migrate or ignore legacy hooks explicitly rather than silently treating them as current authority.
