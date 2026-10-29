#!/usr/bin/env python3
"""Prepare a safe Razr de-adoption proposal without changing AGENTS.md or project data."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys


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


PACKAGE = Path(__file__).resolve().parents[1]
START = b"<!-- RAZR:START -->"
END = b"<!-- RAZR:END -->"


def recognized_blocks() -> set[bytes]:
    blocks = {(PACKAGE / "runtime/AGENTS.project-razr.md").read_bytes().strip()}
    routing = PACKAGE / "runtime/routing"
    if routing.is_dir():
        blocks.update(path.read_bytes().strip() for path in routing.glob("*.md"))
    return blocks


def managed_block(path: Path, raw: bytes) -> tuple[bytes, int, int] | None:
    if not path.exists():
        return None
    starts, ends = raw.count(START), raw.count(END)
    if starts == ends == 0:
        return None
    if starts != 1 or ends != 1:
        raise PreflightError(f"refusing ambiguous Razr routing markers in {path}")
    start, end = raw.find(START), raw.find(END)
    if end < start:
        raise PreflightError(f"refusing reversed Razr routing markers in {path}")
    end += len(END)
    return raw[start:end], start, end


def prepare(target: Path) -> tuple[Path, Path, bytes | None, int | None, int | None, bytes]:
    root = git_root(target)
    agents = root / "AGENTS.md"
    regular_or_absent(agents, "AGENTS.md")
    original = agents.read_bytes() if agents.exists() else b""
    found = managed_block(agents, original)
    if found is None:
        return root, agents, None, None, None, original
    block, start, end = found
    if block not in recognized_blocks():
        raise PreflightError(f"refusing unknown or locally modified Razr routing in {agents}")
    return root, agents, block, start, end, original


def write_exact(path: Path, raw: bytes, mode: int) -> bool:
    if path.exists() or path.is_symlink():
        regular_or_absent(path, path.name)
        if path.read_bytes() != raw or stat.S_IMODE(path.stat().st_mode) != mode:
            raise PreflightError(f"refusing conflicting existing de-adoption file: {path}")
        return False
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, mode)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare exact original/proposed AGENTS.md files for manual Razr de-adoption."
    )
    parser.add_argument("--target", required=True, help="exact Git root to prepare for de-adoption")
    args = parser.parse_args()
    try:
        root, agents, block, start, end, original = prepare(Path(args.target))
        if block is None:
            print(f"No managed Razr routing present: {agents}")
        else:
            assert start is not None and end is not None
            mode = stat.S_IMODE(agents.stat().st_mode)
            digest = hashlib.sha256(original).hexdigest()[:12]
            snapshot = agents.with_name(f".AGENTS.md.razr-deadopt-original-{digest}")
            proposal = agents.with_name(f".AGENTS.md.razr-deadopt-proposed-{digest}")
            write_exact(snapshot, original, mode)
            write_exact(proposal, original[:start] + original[end:], mode)
            print(f"De-adoption proposal prepared; AGENTS.md was not changed: {agents}")
            print(f"Original snapshot: {snapshot}")
            print(f"Proposed AGENTS.md: {proposal}")
            print("Review the current file, snapshot and proposal, then apply the proposal manually.")
        retained = [path for path in (root / "razr.yaml", root / ".razr") if path.exists() or path.is_symlink()]
        if retained:
            print("Retained project data: " + ", ".join(str(path) for path in retained))
        else:
            print("No Razr project data paths were present.")
        print("Pi package installation was not changed.")
        return 0
    except (OSError, PreflightError) as error:
        print(f"DE-ADOPT ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
