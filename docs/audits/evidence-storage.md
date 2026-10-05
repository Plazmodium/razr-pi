# Evidence storage and retention audit

Status: policy recommendation based on a read-only inventory; no evidence was moved, deleted, or imported.

## Scope and method

Measured 2026-10-05 UTC. The measurement source was the local workshop wrapper, inspected read-only and reported here only in aggregate. It is not part of this public candidate. The candidate itself was pinned at start commit `b030d675fc5ced54a3b16b3098ab356b3138039c` and occupied about 364 KiB before this document.

Sizes below are allocated sizes from `du -sk`, so totals include filesystem block overhead and nested Git administration. File-type and duplicate figures are logical-byte measurements and therefore are not directly additive. The duplicate scan used SHA-256 only for same-size regular files and excluded nested `.git` directories. No raw evidence, session identifiers, personal absolute paths, credentials, or evidence payloads were copied into this audit.

The wrapper's Git repository is on an unborn `main` branch: it has no `HEAD` commit and `git ls-files` reports no tracked files. Thus none of the wrapper evidence is source-controlled there. The current ignore file also does not ignore `.labs`, `.bootstrap`, `ephemeral`, or the root crash dump. “Present in a working directory” must not be confused with durable versioned retention.

## Size and classification

| Area | Allocated size | Observed contents | Classification | Current action |
|---|---:|---|---|---|
| `.labs` | 1,528,196 KiB (about 1.46 GiB) | Accepted and historical canonical records, critic runs, reports, sessions, read-backs, test fixtures, copied repositories, and generated checks | Mixed, predominantly private historical proof | Preserve; do not ignore or clean wholesale |
| `.bootstrap` | 320,776 KiB (about 313 MiB) | Import worktrees, source copies, canonical fixture records, and test output | Mixed bootstrap proof and reproducible output | Preserve until references and provenance are indexed |
| `ephemeral` | 247,148 KiB (about 241 MiB) | Scratch implementations and proof runs, including canonical `spec.md`, `executable.yaml`, `state.yaml`, and `evidence.md` files | Mixed; its name does not establish disposability | Quarantine from public source, but preserve pending classification |
| Root crash dump | 3,512 KiB (about 3.4 MiB) | VM/runtime crash output | Disposable runtime output in principle; potentially sensitive | Do not publish; delete only with separate authorization after confirming no incident-retention need |
| **Measured evidence total** | **2,099,632 KiB (about 2.00 GiB)** | Excludes ordinary source/docs outside these areas | Local/private workspace evidence | Keep outside the public package |
| Public candidate before this audit | about 364 KiB | Allowlisted source, public synthetic fixtures, and documentation | Public development source | Keep historical/local evidence out |

The inventory also found these cross-cutting contributors:

| Contributor | Measurement | Interpretation |
|---|---:|---|
| Nested Git administration | 1,719 `.git` directories; 129,708 KiB allocated | Many copied/temporary repositories preserve useful identity, but also amplify storage |
| `.test-output` trees | 9 top-level trees; 417,800 KiB allocated | Generated, but some contain manifests, preservation inventories, or proof cited by local records; not blanket-disposable |
| Python cache | 14 cache directories; 104 KiB allocated | Reproducible cache, not proof |
| Logs and JSONL | 8,602 files; about 80 MiB logical | Mixture of raw proof, diagnostics, and retry/development output |
| Session-named artifacts | 3,044 files; about 34 MiB logical | Potential review-provenance evidence and private conversation/tool history |
| Report/evidence-named artifacts | 4,499 files; about 3.5 MiB logical | Small relative to the workspace and often chain-critical |
| Exact duplicate lower bound | 4,356 groups / 68,641 files; about 527 MiB logical duplicate overhead | Strong case for content-addressed storage; not permission to deduplicate path-sensitive evidence in place |

Canonical-looking files are widespread: `.labs` contains 1,087 specs, 1,065 executables, 838 states, and 975 evidence files; `.bootstrap` contains 1,354/1,292/786/786 respectively; `ephemeral` contains 497/487/341/341. Generated trees contain recursive copies, so these counts are not counts of accepted work items. They do show why directory-name cleanup is unsafe.

## What an accepted proof chain needs

A retained chain must preserve enough material to establish, without inference:

1. the exact approved `spec.md` and bound `executable.yaml`;
2. unchanged-core-produced `state.yaml` and `evidence.md`, including generation and evidence-anchor relationships;
3. candidate commit and source-digest identity, plus the reviewed excluded-input set;
4. actual commands, outcomes, manifests, raw-log digests, and the minimum raw output needed to audit the claim;
5. original Technical and Contract reports, their distinct fresh-context/session provenance, and candidate binding;
6. human checkpoint/acceptance records where the workflow requires them; and
7. read-backs that connect all of the above, including relevant refusal/failure evidence rather than only a final PASS.

A hash alone proves neither execution nor human approval. Conversely, preserving every copied checkout is not automatically necessary if immutable blobs, manifests, identities, and a tested reconstruction procedure provide the same chain. That equivalence has not been established for this workspace, so current accepted and historical evidence remains on hold.

The local readiness and verification documents directly reference `.labs` evidence. A metadata check found 168 of 170 concrete local `.labs` references present; the two unresolved references are pre-existing documentation/path issues, not files changed by this audit. A broad canonical-anchor scan also found unresolved references in historical simulated fixtures. Those findings prevent a blanket “all historical evidence is internally complete” claim and reinforce the preserve-first policy. The specifically documented accepted slug, offline-simulation, and second-item anchor sets were present before and after this audit, and their sampled content digest was unchanged.

## Retention categories

| Category | Examples | Policy |
|---|---|---|
| Public source/product | Package/runtime source, public docs, allowlisted synthetic fixtures | Version normally after privacy and source-identity review; never pull local evidence into the public tree by convenience |
| Durable small verification fixture | Minimal synthetic inputs and expected outputs that are portable, non-secret, deterministic, and exercised by public tests | Keep in source only when each file has an explicit test owner and no private/historical derivation |
| Chain-critical private evidence | Canonical records, human decisions, original critic reports, required session/tool provenance, manifests, read-backs, source/exclusion identities | Preserve immutably outside public source; retain by work item and candidate until an approved retention period expires |
| Reproducible bulky execution output | Copied worktrees, nested Git fixtures, repeated source exports, intermediate test output | Eligible for later compaction only after a reachability manifest proves it is not the sole support for an accepted claim and regeneration is tested |
| Disposable runtime output | `__pycache__`, bytecode, editor metadata, a crash dump after incident triage | Safe to exclude from source; deletion remains a separate authorized operation |
| Unknown or conflicting | Anything under a disposable-sounding path that is referenced, canonical, unique, or not classified | Hold. Unknown means preserve, not delete |

Retention should be claim-oriented rather than directory-oriented. In particular, neither `ephemeral` nor `.test-output` is a sufficient disposal label.

## Safe ignore candidates

No ignore file was changed in this mission. For the local wrapper, the following are reasonable future candidates after a separate review confirms they are not intentionally retained incident evidence:

- `**/__pycache__/`, `*.pyc`, and `*.pyo`;
- the root `erl_crash.dump` (or a dedicated private crash-output directory);
- OS/editor metadata such as `.DS_Store` (already ignored locally).

Do **not** blanket-ignore `.labs/`, `.bootstrap/`, `ephemeral/`, `*.log`, `*.jsonl`, `sessions/`, or `.test-output/` while canonical records and documentation point into them. Ignoring does not create retention, privacy, or deletion safety; it can instead hide unprotected evidence in an unborn repository.

A safer future layout is to make generated runs land in an explicitly private evidence root by default, then export only reviewed minimal fixtures to source. Ignore rules should follow that boundary, not attempt to create it.

## Risks of evidence beside source

- Accidental publication of sessions, machine paths, credentials, private decisions, raw prompts, or proprietary inputs.
- Accidental deletion because a directory is named “ephemeral,” “output,” or “cache” even though canonical records reference it.
- Source identity ambiguity: copied worktrees, nested repositories, generated code, and excluded files can contaminate digest or critic scope.
- Weak durability: this wrapper has no committed baseline, so local presence is not versioned preservation.
- Review noise and performance costs from tens of thousands of duplicate files and nested Git repositories.
- Mutable proof: in-place reruns can overwrite or append beside accepted evidence unless run directories are exclusive and immutable.
- Broken portability: canonical documents that link to local relative paths cannot be reconstructed from the public package.
- False assurance from hashes: content identity does not authenticate who approved a checkpoint or whether a command actually ran.

## Recommended evidence-store boundary

The volume, duplication, mixed privacy levels, and direct path references justify designing an evidence-reference abstraction now, but not implementing a new subsystem in this mission. The public candidate has no natural minimal extension point that can replace canonical `evidence.md` references without changing lifecycle contracts and provenance. Such a change requires an approved vertical slice and unchanged-core compatibility plan.

A future private store should separate immutable blobs from a small append-only index. An `EvidenceRef` should include at least:

```text
id
sha256 digest and byte size
media/evidence type and schema version
producer plus session/run identity
created timestamp
work item, generation, candidate commit, and source-digest binding
storage backend/location (opaque outside the private store)
privacy class and retention category
relationships to contract IDs, canonical evidence anchors, and parent manifests
```

Required behavior for a first thin slice should be: record one newly produced, non-sensitive synthetic artifact into an owned private store, read it back by digest, and verify its work-item/candidate binding without changing canonical lifecycle state by hand. It must fail closed on digest mismatch, path escape, stale generation, missing privacy classification, or an unavailable blob. Stop at the existing first-task human checkpoint before broad migration.

Later slices may add reachability reports, immutable retention/legal holds, encryption/access control, garbage-collection proposals, and tested reconstruction. Garbage collection must consume a reviewed reachability manifest and require explicit human authorization. It must never infer disposal from directory names, Git ignore status, duplication, or age alone.

## Immediate no-regret actions

1. **Delete nothing now.** No safe material storage reduction is large enough to outweigh proof-chain and privacy risk without an index.
2. Keep the local wrapper and its evidence private; do not copy raw inventory, logs, sessions, crash data, or machine paths into this repository.
3. Freeze accepted run directories against in-place reuse and direct new runs to exclusive directories.
4. Build a read-only inventory that assigns retention/privacy categories and records inbound references, starting with the accepted anchor sets cited by readiness and verification docs.
5. Resolve the pre-existing missing-path and historical-anchor ambiguities before any cleanup claim.
6. Only then propose a separately reviewed cleanup manifest. The first candidates should be unreferenced Python caches and triaged crash output; copied repositories and test-output trees need reachability and reconstruction proof first.

## Verification and limitations

This audit performed no lifecycle command, model invocation, installation, remote operation, evidence mutation, or cleanup. It did not authenticate historical approvals or certify confidentiality. Read-only inventory and hash checks show that the sampled accepted-chain content and the wrapper-wide file metadata inventory were unchanged across the audit; no accepted proof chain was broken by this mission.

The public change is documentation only. It does not make local historical evidence part of the public baseline, approve a retention period, or authorize deletion, deduplication, publication, migration, merge, or deployment.
