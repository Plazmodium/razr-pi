# Safe adoption procedure

For a new empty project, follow [greenfield preparation](greenfield.md) before this procedure. Recheck effective Git author configuration inside the new independent root after initialization; an enclosing repository's local identity is not inherited.

## Read-only preflight

Use `git rev-parse --show-toplevel` in the proposed target; do not silently initialize Git for a normal project. Test fixtures may be initialized only within the human-approved lab scope. Verify the returned root is the intended repository, not a parent repository.

Inspect the target's AGENTS.md, razr.yaml, and configured work root before mutations. Refuse symlinks in those paths (including dangling links), non-regular control files, and unexpected work-root ancestry. Routing must have either no Razr markers or exactly one ordered `<!-- RAZR:START -->` / `<!-- RAZR:END -->` pair. Refuse duplicate, reversed, or incomplete markers. These preflight instructions are operator checks, not claims of added CLI enforcement.

If existing managed routing differs from the bundled template, stop for review rather than replacing it. If routing is absent but `razr.yaml`, the requested work root or `.razr/` already exists, adoption refuses because the target is not cleanly unadopted; resolve that retained data explicitly rather than treating it as a fresh start. A symlinked, dangling, or non-regular control path is refused before core invocation; do not follow, replace, edit, or delete either end. Preserve unrelated instructions. On corrupt config, partial state, or a core safety defect, stop and retain evidence; do not patch the validator or clean up partial state automatically.

## Invocation

After resolving package paths from the skill directory, use the preflight's absolute path with the explicitly approved exact Git root. Quote paths and values; never concatenate untrusted title text into executable shell syntax. The preflight uses argument-array delegation to the unchanged core; it is not another lifecycle validator.

```text
python3 <absolute-adopt.py> --target <exact-git-root> --work-root .razr/work
python3 <absolute-core-path> status --json
python3 <absolute-core-path> validate --all
python3 <absolute-core-path> idea <id> --title <title> --depth tiny
python3 <absolute-core-path> status --json
python3 <absolute-core-path> validate --all
```

Read back configuration, routing, and the discovery spec. Capture exit codes, stdout/stderr, and resulting files. Do not report success if a subprocess fails. Do not treat a PASS from `validate` as a test of the proposed product behavior.

## State boundary

Only the target contains razr.yaml and .razr/work. The reusable package contains no adopted-project list or mutable workflow state. The bootstrap lab's records are development evidence, not canonical candidate/critic evidence. Preserve the four-file authority model when a later approved increment supports execution.
