# Razr for one Pi primary

Provides explicit safe adoption and an experimental, acceptance-pending primary-led workflow from discovery through an open PR presented for final human review. The provenance-pinned core owns lifecycle state; package helpers prepare candidate-bound review inputs, launch fresh bounded Pi critics, audit/record supported outcomes, and perform exact GitHub create/read operations without merging.

The command extension remains read-oriented and grants no lifecycle authority. Human approval is required at exact contract approval, the first verified task, Review entry, remote mutation and final PR review. There is no install script, global setup, autonomous worker fleet, automatic retry, merge or deployment.

**Breaking change:** projects adopted by an earlier release are not upgraded in place. `/razr-status <work_id>` reports incompatible previous routing. Use `scripts/de-adopt.py` to prepare exact original/proposed `AGENTS.md` files, manually review and apply the proposal, retain or separately archive the old `razr.yaml` and `.razr/` data, then start again from the beginning in a clean repository.

Review `skills/razr/SKILL.md` and `runtime/AGENTS.project-razr.md` before loading. From an intended disposable project, the installed Pi CLI supports explicit skill loading without installation:

```sh
pi --skill /absolute/path/to/razr-pi/skills/razr/SKILL.md
```

Invoke `/skill:razr` and ask for read-only orientation first. Authorize adoption separately with the target path and work root. Skill loading does not authorize writes. Project trust is not a sandbox; do not change persistent trust settings for this lab.

Explicit skill loading and isolated installed-Pi critic-resource loading have retained evidence. A no-model clean-project test now also loads the relocated local package through the actual installed package resolver, without adoption or global changes. This is not an interactive fresh-primary workflow or full-workflow portability proof. No global installation is authorized. `private: true` prevents accidental npm publication; distribution is deferred.

`runtime/scripts/razr.py` and canonical templates are unchanged snapshots identified by `runtime/provenance.json`. The routing template is Pi-specific adapter code. Runtime paths resolve within this package, not the sibling distribution. Update snapshots deliberately with hash comparison and regression proof, never as a second independently maintained validator.

## Primary execution

For an empty project, start with [greenfield preparation](skills/razr/references/greenfield.md), not direct implementation. `scripts/contract-preflight.py --pair <absolute-directory>` reports labelled raw/core identities and catches the known evidence-ownership contradiction before approval; it never approves or applies a contract.

From a fresh primary, read [the primary runbook](skills/razr/references/primary-execution.md). It covers approved task → actual first-task human pause → candidate preparation/binding → Verify. Then follow [independent review through human PR handoff](skills/razr/references/review-delivery.md). No one-off task execution wrapper is required:

```sh
python3 /absolute/path/to/razr-pi/scripts/primary.py \
  --target '/absolute/project root' --work-id approved-item
```

Inspection is read-only. The primary runs reviewed declared validations directly with Pi tools and invokes the unchanged core for each mutation with its freshly observed generation. It never interprets arbitrary command strings or infers human approval. See the runbook for exact evidence, configured-author/staging and source-identity rules. A new session must inspect actual artifacts and decisions, not reuse a previous session's generation.

## Capabilities and limits

| Resource | Implemented behavior | Current limit |
|---|---|---|
| `scripts/adopt.py` | Safe explicit adoption before unchanged core initialization | Not automatic adoption or a concurrent-path-swap sandbox |
| `scripts/contract-preflight.py` | Read-only core executable preflight, labelled identities and narrow known-conflict diagnostic | Not complete semantic validation or exact human approval |
| `scripts/primary.py` | Read-only core-backed approved-item inspection, absolute paths, empty-candidate handling and fresh generation read-back | No command execution, checkpoint authentication, automatic resume or workflow mutations |
| `scripts/de-adopt.py` | Creates exact original/proposed files for recognized managed routing and reports retained project data | Never changes `AGENTS.md`, uninstalls Pi or deletes/migrates Razr artifacts |
| `scripts/pi-surface.py` | Read-only core status plus a separately labelled adapter boundary and next action | Status never authenticates approval or grants permission |
| `scripts/review-flow.py` | Generic candidate freeze, bounded fresh Technical/Contract Pi launch, structural audit and supported core recording | Human model authorization and primary semantic audit remain mandatory; not a sandbox |
| `scripts/github-delivery.py` | Explicit-base snapshot, exact read/create and optional separately authorized compensation | Push and core lifecycle mutations remain primary-owned; observations are non-atomic |
| `skills/razr/` | Discovery through candidate, fresh dual review, human Review gate and PR handoff | No automatic approval, retry, merge, deploy or publication |
| `scripts/named-regressions.py` | Source-bound named test/subcase proof collection | Test observations are not independent review or authenticated approval |
| `runtime/` | Unchanged deterministic core plus adapter-owned routing | CLI attestations do not prove checks ran or humans approved |

Pi release versions are diagnostic metadata, not runtime gates. Missing required behavior—not an unfamiliar version—is a refusal. The experimental delivery path requires actual fresh independent reviews and exact live PR read-back for each delivered candidate; tests and simulated transports do not substitute for those per-candidate gates or release acceptance.
