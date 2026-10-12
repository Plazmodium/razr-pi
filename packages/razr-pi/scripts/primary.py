#!/usr/bin/env python3
"""Read-only primary orientation. No task runner, approval or transition authority."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
CORE = Path(__file__).resolve().parents[1] / "runtime/scripts/razr.py"
spec = importlib.util.spec_from_file_location("razr_primary_core", CORE)
core = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = core
spec.loader.exec_module(core)


def inspect(target, work_id):
    target = Path(target).expanduser().resolve(strict=True)
    repo = core.git_root(target)
    if target != repo.resolve():
        raise core.RazrError("--target must be the exact Git root, not a subdirectory")
    config_path = repo / "razr.yaml"
    if config_path.is_symlink() or (repo / "AGENTS.md").is_symlink():
        raise core.RazrError("project config/instructions must not be symlinks")
    config_bytes = config_path.read_bytes()
    config = core.load_json(config_path)
    work_root = core.validate_config(repo, config)
    folder = core.work_item_dir(work_root, work_id)
    core.reject_canonical_symlinks(folder, include_materialized=True)
    paths = [repo / "AGENTS.md", config_path,
             *[folder / name for name in ("spec.md", "executable.yaml", "state.yaml", "evidence.md")]]
    if not (folder / "state.yaml").is_file():
        raise core.RazrError("approved materialized work required; inspect exact approval before core approve")
    before = {path: path.read_bytes() for path in paths}
    if before[config_path] != config_bytes:
        raise core.RazrError("configuration changed during inspection; STOP")
    # The unchanged core owns validation, including approval digests and node evidence.
    checks = []
    for args in (("status", work_id, "--json"), ("validate", work_id)):
        argv = [sys.executable, str(CORE), *args]
        result = subprocess.run(argv, cwd=repo, capture_output=True, text=True, timeout=30)
        checks.append({"argv": argv, "cwd": str(repo), "exit": result.returncode,
                       "stdout": result.stdout, "stderr": result.stderr})
        if result.returncode:
            raise core.RazrError(result.stdout + result.stderr)
    if any(path.is_symlink() or path.read_bytes() != data for path, data in before.items()):
        raise core.RazrError("artifacts changed during inspection; STOP")
    state = json.loads(before[folder / "state.yaml"])
    executable = json.loads(before[folder / "executable.yaml"])
    candidate = state["candidate"]  # core requires an object, including when empty
    return {
        "schema_version": 1,
        "authority": "Read-only observation, not approval, execution proof or a next-action authorization",
        "target": str(repo), "core": str(CORE), "work_id": work_id,
        "work_root": str(work_root), "item": str(folder),
        "generation": state["generation"], "stage": state["stage"], "status": state["status"],
        "candidate_empty": all(candidate[key] is None for key in ("repository", "branch", "commit", "source_digest")),
        "candidate": candidate, "state": state, "executable": executable,
        "files": {str(path): "sha256:" + hashlib.sha256(data).hexdigest() for path, data in before.items()},
        "core_checks": checks,
        "limits": ["Read the spec, project instructions and actual human decisions separately.",
                   "Reinspect before each mutation; pass the observed generation, never an inferred increment.",
                   "No checkpoint acceptance or review allowance is inferred from state.",
                   "Lock files are mutable coordination data, not source/evidence identities.",
                   "This is not an atomic snapshot or adversarial path-swap sandbox."]
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="Exact adopted Git root; resolved absolutely")
    parser.add_argument("--work-id", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.target, args.work_id), indent=2))
        return 0
    except (core.RazrError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
