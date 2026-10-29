#!/usr/bin/env python3
"""Safe preflight for explicit Pi Razr adoption; delegates lifecycle work to bundled core."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

PACKAGE = Path(__file__).resolve().parents[1]
CORE = PACKAGE / "runtime" / "scripts" / "razr.py"
ROUTING = (PACKAGE / "runtime" / "AGENTS.project-razr.md").read_text(encoding="utf-8").strip()
START = "<!-- RAZR:START -->"
END = "<!-- RAZR:END -->"


class PreflightError(Exception):
    pass


def git_root(target: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--show-toplevel"], text=True, capture_output=True
    )
    if result.returncode:
        raise PreflightError(f"target is not a Git repository: {target}")
    root = Path(result.stdout.strip()).resolve()
    if target.resolve() != root:
        raise PreflightError(f"target must be the exact Git root: {target.resolve()} (root is {root})")
    return root


def regular_or_absent(path: Path, label: str) -> None:
    if path.is_symlink():
        raise PreflightError(f"refusing symlinked {label}: {path}")
    if path.exists() and not path.is_file():
        raise PreflightError(f"refusing non-regular {label}: {path}")


def check_routing(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    starts, ends = text.count(START), text.count(END)
    if starts == ends == 0:
        return False
    if starts != 1 or ends != 1:
        raise PreflightError(f"refusing ambiguous Razr routing markers in {path}")
    start, end = text.find(START), text.find(END)
    if end < start:
        raise PreflightError(f"refusing reversed Razr routing markers in {path}")
    end += len(END)
    if text[start:end] != ROUTING:
        raise PreflightError(f"refusing locally modified Razr routing in {path}; reconcile it explicitly")
    return True


def work_root(root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise PreflightError(f"work root must be a non-empty relative path under target: {value!r}")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PreflightError(f"refusing symlinked work-root path: {current}")
        if current.exists() and not current.is_dir():
            raise PreflightError(f"refusing non-directory work-root path: {current}")
    return current


def preflight(target: Path, requested_root: str) -> Path:
    root = git_root(target)
    agents, config = root / "AGENTS.md", root / "razr.yaml"
    regular_or_absent(agents, "AGENTS.md")
    regular_or_absent(config, "razr.yaml")
    managed = check_routing(agents)
    requested = work_root(root, requested_root)
    if not managed and (config.exists() or requested.exists() or (root / ".razr").exists()):
        raise PreflightError(
            "refusing retained Razr data without current managed routing; this breaking release requires a clean repository"
        )
    return root


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight explicit Razr adoption, then run unchanged core init.")
    parser.add_argument("--target", required=True, help="exact Git root to adopt")
    parser.add_argument("--work-root", default=".razr/work")
    args = parser.parse_args()
    try:
        root = preflight(Path(args.target), args.work_root)
    except PreflightError as error:
        print(f"PREFLIGHT ERROR: {error}", file=sys.stderr)
        return 2
    result = subprocess.run([sys.executable, str(CORE), "init", "--work-root", args.work_root], cwd=root)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
