# Current product state

Razr-Pi is a private local Pi package for repository-centered workflow, context, Memory, optional Herdr-hosted execution, and bounded GitHub delivery operations.

## Start here

1. [Repository overview](../README.md)
2. [Package installation and commands](../packages/razr-pi/README.md)
3. [Architecture and authority](architecture.md)
4. [Capabilities and limitations](capabilities.md)
5. [Testing](testing.md)
6. [Source identity policy](../SOURCE_BASELINE.md)

## Implemented

- Explicit repository adoption and discovery.
- One Pi skill and one extension with five commands.
- Core-backed status and validation read-back.
- Deterministic bounded context from immutable Git objects.
- Repository-scoped append-only Razr Memory.
- Optional Herdr execution with candidate fences, durable attempts, bounded subprocess supervision, interruption, and explicit recovery.
- Source-bound GitHub snapshot/read/create operations.
- No-model package loading checks and selected public regression coverage.

## Product boundaries

- The Razr core is the sole workflow authority.
- Repository evidence outranks Memory.
- Pi loading does not adopt or execute a project.
- Herdr telemetry does not become Razr workflow state.
- The Pi command surface has no worker command.
- GitHub observations are bounded and non-atomic.
- No automatic merge, deployment, publication, credential management, general critic dispatcher, or PR factory is provided.

## Delivery requirements

A deliverable candidate still requires exact source and excluded-input scrutiny, fresh independent Technical and Contract review of the same commit, applicable repository checks, and final human PR review. Package loading, hashes, process status, CLI output, and empty remote check collections do not satisfy those gates.
