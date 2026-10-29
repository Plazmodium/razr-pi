#!/usr/bin/env python3
"""Small Pi-facing dispatcher; authority remains the core and MemoryStore."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import sys
import uuid

PACKAGE = Path(__file__).resolve().parents[1]
CORE = PACKAGE / "runtime/scripts/razr.py"


def _request(root: Path, argv: tuple[str, ...]):
    sys.path.insert(0, str(PACKAGE))
    # These package-internal identity primitives are the same ones M03 uses to
    # populate the frozen CoreRequest; do not duplicate or broaden their policy.
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


def _fanout_status() -> dict[str, object]:
    sys.path.insert(0, str(PACKAGE))
    from adapter.herdr_provider import HerdrProvider
    from adapter.protocol import ProtocolError
    request = {"schema_version": 1, "request_id": "probe-" + uuid.uuid4().hex,
               "required_capabilities": ["fresh_context", "working_directory", "bounded_time",
                                          "bounded_output", "runtime_identity"],
               "runtime": {"provider": "openai-codex", "model": "openai-codex/gpt-5.6-luna", "thinking": "medium"}}
    try:
        result = HerdrProvider().probe(request)
        report = result["result"]
        return {"availability": "available", "provider_id": report["provider_id"],
                "capabilities": report["capabilities"], "report_digest": report["report_digest"]}
    except ProtocolError as exc:
        return {"availability": "unavailable", "code": exc.code}


def _routing_status(root: Path) -> dict[str, str]:
    start, end = b"<!-- RAZR:START -->", b"<!-- RAZR:END -->"
    path = root / "AGENTS.md"
    if not path.is_file() or path.is_symlink():
        return {"routing": "missing", "blocked_by": "project_routing",
                "next_action": "Inspect or explicitly adopt this exact Git repository."}
    raw = path.read_bytes()
    if raw.count(start) != 1 or raw.count(end) != 1 or raw.find(end) < raw.find(start):
        return {"routing": "ambiguous", "blocked_by": "project_routing",
                "next_action": "Stop and inspect the project instructions; no in-place migration is supported."}
    block = raw[raw.find(start):raw.find(end) + len(end)]
    current = (PACKAGE / "runtime/AGENTS.project-razr.md").read_bytes().strip()
    if block == current:
        return {"routing": "current", "blocked_by": "none",
                "next_action": "Follow the loaded skill and exact project contract; human approval is never inferred."}
    routing_dir = PACKAGE / "runtime/routing"
    known = {} if not routing_dir.is_dir() else {
        p.read_bytes().strip(): p.stem for p in routing_dir.glob("*.md")
    }
    if block in known:
        return {"routing": "incompatible_previous_release", "routing_profile": known[block],
                "blocked_by": "breaking_release",
                "next_action": "Use scripts/de-adopt.py to prepare exact files, manually review and apply the proposal, then restart in a clean repository. Retained razr.yaml and .razr data require a separate explicit archive or removal decision."}
    return {"routing": "modified_or_unknown", "blocked_by": "project_routing",
            "next_action": "Review the local routing; the adapter will not replace an unknown block."}


def _adapter_status(root: Path, stage: str, status: str) -> dict[str, object]:
    routing = _routing_status(root)
    boundary = {
        "supported_through": "candidate_binding",
        "experimental_through": "open_pr_handoff",
        "delivery_status": "acceptance_pending",
        "review_and_delivery": "fresh dual review, human-gated Review entry and exact PR handoff are experimental and acceptance-pending; merge/deploy remain unavailable",
        **routing,
    }
    if stage == "spec" and status == "exploring" and routing["routing"] == "incompatible_previous_release":
        boundary["reason"] = "This project was adopted by an incompatible previous release. In-place upgrade is not supported; start again from the beginning."
    elif stage == "spec" and status == "exploring":
        boundary["reason"] = "This item is still exploring. Progress requires an exact approved spec/executable pair and the current project routing."
    else:
        boundary["reason"] = "Core workflow state and adapter authorization are separate; a valid state does not authorize the next action."
    return boundary


def core_status(root: Path, work_id: str | None = None) -> object:
    if not work_id:
        raise ValueError("status requires a work_id")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", work_id):
        raise ValueError("invalid core work ID")
    sys.path.insert(0, str(PACKAGE))
    from adapter.core_subprocess import invoke_core, read_workflow_context
    response = invoke_core(_request(root, ("status", work_id, "--json")))
    try:
        rows = json.loads(response.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("core status response is malformed") from exc
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("status row count differs")
    row = rows[0]
    stage, status = row.get("stage"), row.get("status")
    if not isinstance(stage, str) or not isinstance(status, str):
        raise ValueError("core status row is malformed")
    adapter = _adapter_status(root, stage, status)
    if stage == "spec" and status == "exploring":
        return {"schema_version": 2, "work_id": work_id, "stage": stage, "status": status,
                "generation": None, "node_id": None, "node_status": None, "node_attempt": None,
                "node_result_ref": None, "candidate": {"repository": None, "branch": None,
                "commit": None, "source_digest": None}, "status_row": row, "raw_state": None,
                "adapter": adapter, "fanout": _fanout_status()}
    read = read_workflow_context(_request(root, ("status", work_id, "--json")), work_id)
    return {"schema_version": 2, "work_id": read.work_id, "stage": read.stage, "status": read.status,
            "generation": read.generation, "node_id": read.node_id, "node_status": read.node_status,
            "node_attempt": read.node_attempt, "node_result_ref": read.node_result_ref,
            "candidate": {"repository": read.candidate_repository, "branch": read.candidate_branch,
                          "commit": read.candidate_commit, "source_digest": read.candidate_source_digest},
            "status_row": read.status_row,
            "raw_state": {"path": read.raw_state_path, "digest": read.raw_state_digest,
                          "bytes": read.raw_state_bytes, "core_identity": read.raw_state_core_identity,
                          "source_digest": read.raw_state_source_digest},
            "adapter": adapter, "fanout": _fanout_status()}


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
        from adapter.core_subprocess import _git_head, _source_digest
        from adapter.protocol import canonical_json, raw_file_digest
        value = _object(raw, "recall") if raw else {}
        query, paths, limit = value.get("query", ""), value.get("paths", []), value.get("limit", 20)
        repository = _source_digest(root, _git_head(root))
        return {"records": [], "omitted_count": 0, "stale_ids": [],
                "query_digest": raw_file_digest(canonical_json({"query": query, "paths": sorted(paths), "limit": limit,
                                                                  "repository": repository}))}
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
    core_request = None
    if isinstance(value.get("include"), dict) and value["include"].get("workflow") is True:
        work_id = value.get("work_id")
        if not isinstance(work_id, str) or not work_id:
            raise ValueError("workflow context requires work_id")
        core_request = _request(root, ("status", work_id, "--json"))
    with MemoryStore(root) as store:
        return ContextEngine(root, memory=store, core_request=core_request).context(value)


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
