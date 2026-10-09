#!/usr/bin/env python3
"""Small Pi-facing dispatcher; authority remains the core and MemoryStore."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import uuid

PACKAGE = Path(__file__).resolve().parents[1]
CORE = PACKAGE / "runtime/scripts/razr.py"


def _request(root: Path, argv: tuple[str, ...]):
    sys.path.insert(0, str(PACKAGE))
    from adapter.core_subprocess import _git_head, _source_digest
    from adapter.protocol import CoreRequest, ProvenancePin, raw_file_digest
    provenance_path = PACKAGE / "runtime/provenance.json"
    declaration = json.loads(provenance_path.read_text(encoding="utf-8"))
    pins = tuple(ProvenancePin(row["path"], row["sha256"]) for row in declaration["files"])
    commit = _git_head(root)
    return CoreRequest(
        request_id="pi-" + uuid.uuid4().hex,
        repository_root=root, argv=argv, cwd=root, package_root=PACKAGE,
        python_executable=Path(sys.executable).resolve(), runtime_script=CORE.resolve(),
        expected_commit=commit, expected_source_digest=_source_digest(root, commit),
        expected_core_identity=raw_file_digest(provenance_path.read_bytes()),
        expected_provenance=pins, expected_readback=(), timeout_ms=30000,
        termination_grace_ms=5000, max_stdout_bytes=1_048_576, max_stderr_bytes=262_144)


def core_status(root: Path, work_id: str | None = None) -> object:
    sys.path.insert(0, str(PACKAGE))
    from adapter.core_subprocess import invoke_core
    argv = ("status", *( (work_id,) if work_id else () ), "--json")
    response = invoke_core(_request(root, argv))
    try:
        return json.loads(response.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("core status response was not valid JSON") from exc


def _object(raw: str, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} requires one JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} requires one JSON object")
    return value


def memory(root: Path, operation: str, raw: str = "") -> object:
    sys.path.insert(0, str(PACKAGE))
    from adapter.memory import MemoryStore
    memory_path = root / ".razr/memory.jsonl"
    if operation == "inspect" and not memory_path.exists():
        return []
    if operation == "recall" and not memory_path.exists():
        from adapter.protocol import canonical_json, raw_file_digest
        value = _object(raw, "recall") if raw else {}
        query, paths, limit = value.get("query", ""), value.get("paths", []), value.get("limit", 20)
        return {"records": [], "omitted_count": 0, "stale_ids": [],
                "query_digest": raw_file_digest(canonical_json({"query": query, "paths": sorted(paths), "limit": limit}))}
    with MemoryStore(root) as store:
        if operation == "inspect":
            return store.inspect()
        if operation == "recall":
            value = _object(raw, "recall") if raw else {}
            return store.recall(value.get("query", ""), paths=value.get("paths", []), limit=value.get("limit"))
        if operation == "remember":
            value = _object(raw, "remember")
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
    value = _object(raw, "context") if raw else {
        "schema_version": 1, "request_id": "unused", "objective": "current repository context",
        "repository_root": ".", "include": {"source": True, "tests": True, "config": True,
        "git": True, "workflow": False, "memory": False},
        "limits": {"max_files": 40, "max_bytes": 200000, "max_memory_records": 20},
    }
    value["request_id"] = "ctx-" + uuid.uuid4().hex
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
    except Exception as exc:
        # Keep command failures bounded and diagnostic; never expose a traceback or
        # reinterpret an adapter/core refusal as a successful workflow result.
        print(f"PREFLIGHT ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
