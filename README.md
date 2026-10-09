# Razr-Pi public source candidate

Razr-Pi is a local Pi package for repository-first delivery work. It combines one instruction skill, one command extension, a provenance-pinned Razr core, bounded repository context, local-first Razr Memory, an optional Herdr execution provider, and a separately invoked GitHub delivery helper. Campaign acceptance and publication remain pending final exact-head critique and human PR review.

Pi concept: a skill supplies instructions, while an extension registers executable commands. Loading either resource makes the surface available; it does not adopt a project, execute work, approve a specification, or create a sandbox.

Razr-Pi treats the checked-out repository as the project brain. Source, tests, configuration, Git history, and approved workflow artifacts outrank advisory Memory. Project Brain, Obsidian, an MCP vault, and a parallel architecture/component/session documentation tree are not required. The user-facing path is idea or conversation → converged spec → executable → build → verify → delivery.

## What is implemented

- Safe explicit adoption and discovery through the provenance-pinned Python core.
- Read-only `/razr-status` and bounded `/razr-context` command surfaces.
- Repository-scoped append-only Razr Memory with explicit remember, recall, inspect, retire, and supersede behavior.
- Deterministic context selection from one clean immutable Git commit, including bounded source, tests, config, history, workflow, and lower-authority Memory.
- A bounded optional Herdr provider with separate caller/target identities, generation and candidate fences, append-only attempt evidence, permanent root claims, streamed output bounds, interruption, and truthful `UNKNOWN` handling.
- A closed GitHub helper for source-bound `snapshot`, read-only PR `read`, and separately authorized PR `create`.
- No-model Pi package/resource probing and selected public regression coverage.

The provider and delivery helper transport and observe work; neither owns Razr lifecycle state, approves reviews, merges, deploys, or forms a PR factory.

## Read first

- [Package use and commands](packages/razr-pi/README.md)
- [Current public state](docs/CURRENT.md)
- [Architecture and authority](docs/architecture.md)
- [Capability boundaries](docs/capabilities.md)
- [Testing and prerequisites](docs/testing.md)
- [Source identity policy](SOURCE_BASELINE.md)
- [Vertical slices](docs/05-vertical-slices.md)

## Local Pi package use

Review the package and project settings before granting Pi project trust. With human-provided Pi, a project may declare the local package using normal Pi package behavior:

```sh
pi install --local /reviewed/path/to/razr-pi/packages/razr-pi
pi list
```

A local package is loaded from its resolved path and is not copied. This repository does not run that installation command automatically. For a one-process resource check, explicit `--extension` and `--skill` paths may be used instead; see the package README and testing procedure.

Loading registers `/razr-status`, `/razr-context`, `/razr-remember`, `/razr-recall`, and `/razr-memory-inspect`. It does not create `razr.yaml`, `.razr`, or `AGENTS.md`. Adoption remains an explicit, separately authorized action through `packages/razr-pi/scripts/adopt.py`.

## Verification

Use human-provided Python 3.11+, Git, Node, and Pi. Do not install or configure prerequisites implicitly. There is no npm build or third-party Python dependency; validation and executable integration tests are the source build.

```sh
python3 -B tests/check_public_tree.py --root .
python3 -B tests/run_public.py --phase tracer --require-pi
python3 -B tests/run_public.py --phase full --require-pi
```

The full phase reruns the integration tracer, exports only the public allowlist, and executes the selected regression modules with isolated configuration. Missing prerequisites, fixtures, failures, or unexpected skips fail. Focused clean-room commands and proof limits are in [testing](docs/testing.md).

## Authority and remaining gates

The core alone owns legal generation-fenced lifecycle transitions. `spec.md` owns intent, `executable.yaml` the approved DAG, `state.yaml` materialized progress, and `evidence.md` reproduced observations. Do not hand-edit lifecycle state or infer approval from metadata, CLI output, Pi loading, Herdr telemetry, a model report, or a hash.

Exact human specification approval, the first verified-task checkpoint, source identity and excluded-input scrutiny, fresh independent Technical and Contract critics on the exact candidate, and final human PR review remain mandatory. A different model or a fork carrying builder history is not independent critique. No automatic merge, deployment, npm publication, installation, credential handling, or general review/factory automation is provided.

Razr Memory secret filtering is heuristic and is not a confidentiality boundary. The cooperative lock does not make hostile same-UID pathname replacement race-proof. GitHub observations are bounded and non-atomic; `requiredChecksStatus: "none"` does not prove tests ran. Review all source before use or publication.
