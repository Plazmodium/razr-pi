# Safe de-adoption preparation

Use this only when the human explicitly asks to stop Pi from treating one exact Git repository as Razr-managed.

The helper never changes `AGENTS.md`. It reads one exact recognized `<!-- RAZR:START -->` … `<!-- RAZR:END -->` block and creates two new exclusive files beside it:

- `.AGENTS.md.razr-deadopt-original-*` — the exact observed original bytes;
- `.AGENTS.md.razr-deadopt-proposed-*` — the proposed bytes with only that managed block removed.

This avoids silently overwriting editor or concurrent-process changes. The human must compare the current file, original snapshot and proposal, then apply the proposal manually. Existing conflicting preparation files are refused rather than overwritten.

This release does not migrate previous routing or workflow state. After manually applying the proposal, start again from the beginning in a clean repository. Reusing the same repository requires a separate explicit decision to archive or remove retained `razr.yaml` and `.razr/` data; this helper never does that automatically.

Inspect the exact Git root and command first:

```text
python3 <absolute-de-adopt.py> --target <exact-git-root>
```

The command refuses symlinked or non-regular `AGENTS.md`, ambiguous markers, and unknown or locally modified managed blocks. A project with no managed block is a safe no-op. It does not uninstall Pi, apply the proposal, or delete/archive workflow data.
