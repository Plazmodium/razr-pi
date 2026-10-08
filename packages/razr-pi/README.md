# Razr for Pi: public bootstrap

Review [the skill](skills/razr/SKILL.md) and [managed routing](runtime/AGENTS.project-razr.md) before loading. This skill-only, private package supports explicit adoption/discovery. Loading does not authorize writes or adopt a project. Do not install automatically.

The package resolves helpers and runtime internally. The core and five canonical templates are unchanged snapshots identified by [provenance](runtime/provenance.json). Provenance is not execution proof or licensing clearance.

Stable adapter surfaces:

- `adapter/memory.py`: deterministic repository-local append-only Memory for M03/M05; it stores experience only and never owns workflow state.

Supported helper surfaces:

- `scripts/adopt.py`: explicit exact-root preflight, then unchanged core initialization.
- `scripts/primary.py`: read-only inspection of already materialized work; refuses discovery-only items. No execution or approval authority.
- `scripts/contract-preflight.py`: read-only pair identities and a narrow evidence-authority conflict diagnostic. Not semantic completeness or human approval.
- `scripts/named-regressions.py`: actual unittest observation collection. Its raw output is private, not public source or independent critique.

Do not infer supported live task, candidate, critique, recovery or delivery automation from commands present in the unchanged core. No live reviewer or PR factory ships here. The narrower project contract/routing always applies. Exact approval, first-task human inspection, source identity, fresh independent dual critique and final human PR review remain required for later work.

[Public testing](../../docs/testing.md) describes no-model loader and adoption proof, prerequisites and remaining gates. No model, auth or client runtime is required to inspect resources.
