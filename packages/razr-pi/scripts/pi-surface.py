#!/usr/bin/env python3
"""Small Pi-facing dispatcher; authority remains the core and MemoryStore."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys

PACKAGE = Path(__file__).resolve().parents[1]
CORE = PACKAGE / "runtime/scripts/razr.py"


def core_status(root: Path, work_id: str | None = None) -> object:
    argv = [sys.executable, "-B", str(CORE), "status"]
    if work_id:
        argv.append(work_id)
    argv.append("--json")
    result = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError((result.stdout + result.stderr).strip() or "core status failed")
    return json.loads(result.stdout)


def memory(root: Path, operation: str, raw: str = "") -> object:
    sys.path.insert(0, str(PACKAGE))
    from adapter.memory import MemoryStore
    memory_path = root / ".razr/memory.jsonl"
    if operation == "inspect" and not memory_path.exists():
        return []
    if operation == "recall" and not memory_path.exists():
        from adapter.protocol import canonical_json, raw_file_digest
        value = json.loads(raw) if raw else {}
        query, paths, limit = value.get("query", ""), value.get("paths", []), value.get("limit", 20)
        return {"records": [], "omitted_count": 0, "stale_ids": [],
                "query_digest": raw_file_digest(canonical_json({"query": query, "paths": sorted(paths), "limit": limit}))}
    with MemoryStore(root) as store:
        if operation == "inspect":
            return store.inspect()
        if operation == "recall":
            value = json.loads(raw) if raw else {}
            return store.recall(value.get("query", ""), paths=value.get("paths", []), limit=value.get("limit"))
        if operation == "remember":
            value = json.loads(raw)
            required = {"kind", "summary", "repository"}
            if set(value) - required - {"paths", "confidence", "evidence", "tags", "related_ids"} or not required <= set(value):
                raise ValueError("remember requires kind, summary, and repository; no unknown fields")
            return store.remember(kind=value["kind"], summary=value["summary"], repository=value["repository"],
                                  paths=value.get("paths", []), confidence=value.get("confidence", "medium"),
                                  evidence=value.get("evidence", []), tags=value.get("tags", []),
                                  related_ids=value.get("related_ids", []))
    raise ValueError("unsupported surface operation")


def context(root: Path, raw: str = "") -> object:
    """Delegate bounded context assembly to the frozen M03 ContextEngine."""
    sys.path.insert(0, str(PACKAGE))
    from adapter.context import ContextEngine
    from adapter.memory import MemoryStore
    value = json.loads(raw) if raw else {
        "schema_version": 1, "request_id": "ctx-pi", "objective": "current repository context",
        "repository_root": ".", "include": {"source": True, "tests": True, "config": True,
        "git": True, "workflow": False, "memory": False},
        "limits": {"max_files": 40, "max_bytes": 200000, "max_memory_records": 20},
    }
    with MemoryStore(root) as store:
        return ContextEngine(root, memory=store).context(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("status", "context", "remember", "recall", "inspect"))
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    root = Path.cwd().resolve(strict=True)
    if args.operation == "status":
        result = core_status(root, args.value)
    elif args.operation == "context":
        result = context(root, args.value or "")
    else:
        result = memory(root, args.operation, args.value or "")
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"PREFLIGHT ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
