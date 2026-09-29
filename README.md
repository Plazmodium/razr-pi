# Razr-Pi public source bootstrap

An isolated source candidate for a small, public Pi package. Full acceptance and publication are pending. See [capability boundaries](docs/capabilities.md) and [source identity policy](SOURCE_BASELINE.md).

The first integration path is no-model package resolution followed by separately explicit safe adoption and discovery. Package loading is not adoption, and project trust is not a sandbox. No installer, extension, worker fleet, critic launcher or PR factory is included.

## Read first

- [Package instructions](packages/razr-pi/README.md)
- [Architecture and authority](docs/architecture.md)
- [Testing and prerequisites](docs/testing.md)
- [Vertical slices](docs/05-vertical-slices.md)
- [Slice template](templates/vertical-slice.md)

Use human-provided Python, Git, Node and Pi. Never install or configure them implicitly. There is no npm build or third-party Python dependency. Validation and executable integration tests constitute the source build.

## Prospective checks

```sh
python3 -B tests/check_public_tree.py --root .
python3 -B tests/run_public.py --phase tracer --require-pi
python3 -B tests/run_public.py --phase full --require-pi
```

The full phase reruns the integration tracer, exports only the public allowlist to a fresh owned source tree, and runs the selected regression modules there with isolated configuration. Missing prerequisites, fixtures, failures or unexpected skips fail acceptance. The first verified-task human checkpoint remains a workflow requirement, not an assertion authenticated by this test runner. Original workshop tests and historical results are not this baseline's proof.
