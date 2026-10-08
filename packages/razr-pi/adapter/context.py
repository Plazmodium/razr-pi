"""Deterministic, bounded repository context assembly for M03.

This module is deliberately read-only.  The repository is authoritative; Memory is
an explicitly labelled, lower-priority source of experience.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any, Callable, Mapping

from .protocol import ProtocolError, canonical_json, raw_file_digest, validate_git_object_id, validate_source_digest

KINDS = ("source", "tests", "config", "git", "workflow", "memory")
_DOMAINS = {"raw_file", "canonical_json", "core_source", "executable", "git_object"}
_LIMIT = {"max_files": 200, "max_bytes": 2_000_000, "max_memory_records": 100}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _error(code: str, message: str, **details: object) -> ProtocolError:
    return ProtocolError(code, message, details=details)


def _ref(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise _error("PATH_ESCAPE", "repository-relative POSIX path required")
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or str(p) != value or value == ".":
        raise _error("PATH_ESCAPE", "path escapes repository")
    return value


def _root(value: Path | str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path or not path.is_dir() or path.is_symlink():
        raise _error("INVALID_REQUEST", "repository root must be canonical")
    return path


@dataclass(frozen=True, slots=True)
class ContextRequest:
    schema_version: int
    request_id: str
    objective: str
    repository_root: str
    include: dict[str, bool]
    limits: dict[str, int]
    work_id: str | None = None
    paths: tuple[str, ...] = ()
    observed: dict[str, str] | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ContextRequest":
        if not isinstance(value, Mapping) or set(value) - {"schema_version", "request_id", "objective", "repository_root", "work_id", "paths", "include", "limits", "observed"}:
            raise _error("INVALID_REQUEST", "unknown context request field")
        required = ("schema_version", "request_id", "objective", "repository_root", "include", "limits")
        if any(key not in value or value[key] is None for key in required):
            raise _error("INVALID_REQUEST", "missing context request field")
        if any(key in value and value[key] is None for key in ("work_id", "paths", "observed")):
            raise _error("INVALID_REQUEST", "null is not permitted; omit optional fields")
        include = value["include"]
        limits = value["limits"]
        if not isinstance(include, dict) or set(include) != set(KINDS) or any(type(v) is not bool for v in include.values()):
            raise _error("INVALID_REQUEST", "include must contain the six context sources")
        if not isinstance(limits, dict) or set(limits) - set(_LIMIT) or set(limits) != set(_LIMIT):
            raise _error("INVALID_REQUEST", "limits must contain max_files, max_bytes and max_memory_records")
        checked_limits = {}
        for key, maximum in _LIMIT.items():
            number = limits[key]
            if type(number) is not int or not 0 < number <= maximum:
                raise _error("LIMIT_EXCEEDED", "context limit is outside the bounded range", limit=key)
            checked_limits[key] = number
        objective, repository = value["objective"], value["repository_root"]
        request_id = value["request_id"]
        if not isinstance(request_id, str) or not _ID.fullmatch(request_id) or not isinstance(objective, str) or not objective or len(objective) > 4000 or not isinstance(repository, str):
            raise _error("INVALID_REQUEST", "invalid context identity or objective")
        paths = value.get("paths", [])
        if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
            raise _error("INVALID_REQUEST", "paths must be a list")
        for path in paths:
            _ref(path)
        work_id = value.get("work_id")
        if work_id is not None and (not isinstance(work_id, str) or not _ID.fullmatch(work_id)):
            raise _error("INVALID_REQUEST", "invalid work ID")
        observed = value.get("observed")
        if observed is not None:
            if not isinstance(observed, dict) or set(observed) != {"commit", "source_digest"}:
                raise _error("INVALID_REQUEST", "observed identity is closed")
            validate_git_object_id(observed["commit"]); validate_source_digest(observed["source_digest"])
        return cls(1, request_id, objective, repository, dict(include), checked_limits, work_id, tuple(paths), observed)

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"schema_version": 1, "request_id": self.request_id, "objective": self.objective,
            "repository_root": self.repository_root, "include": dict(self.include), "limits": dict(self.limits)}
        if self.work_id is not None: result["work_id"] = self.work_id
        if self.paths: result["paths"] = list(self.paths)
        if self.observed is not None: result["observed"] = dict(self.observed)
        return result


class ContextEngine:
    """Select bounded context using repository files, core readback, Git, and M02 Memory."""
    def __init__(self, repository_root: Path | str, *, memory: Any = None,
                 workflow: Callable[[ContextRequest], object] | None = None):
        self.root = _root(repository_root)
        self.memory = memory
        self.workflow = workflow

    def _candidate_files(self, request: ContextRequest) -> list[str]:
        objective = set(re.findall(r"[a-z0-9][a-z0-9_.-]{2,}", request.objective.lower()))
        explicit = list(request.paths)
        names = []
        for path in self.root.rglob("*"):
            if not path.is_file() or path.is_symlink(): continue
            ref = path.relative_to(self.root).as_posix()
            if ref.startswith(".git/") or ref.startswith(".razr/"): continue
            if ref in explicit: continue
            if path.suffix.lower() in {".py", ".ts", ".js", ".md", ".yaml", ".yml", ".json", ".toml", ".ini"}:
                names.append(ref)
        def score(ref: str) -> tuple[int, str]:
            low = ref.lower(); base = Path(ref).name.lower()
            rank = 0
            if any(token in low for token in objective): rank -= 30
            if base in {"readme.md", "razr.yaml", "pyproject.toml", "package.json"}: rank -= 20
            if "/test" in low or base.startswith("test_"): rank -= 10
            return rank, ref
        return sorted(explicit + sorted(names, key=score), key=score)

    def _item_file(self, ref: str, remaining: int, authority: str = "repository") -> dict[str, object] | None:
        _ref(ref); target = self.root / ref
        if target.is_symlink() or not target.resolve().is_relative_to(self.root) or not target.is_file(): return None
        data = target.read_bytes()
        if not remaining: return None
        content = data[:remaining]
        try: text = content.decode("utf-8")
        except UnicodeDecodeError: return {"kind":"file", "ref":ref, "digest_domain":"raw_file", "digest":raw_file_digest(data), "bytes":0, "reason":"binary metadata", "authority":authority, "metadata":{"type":"binary","ref":ref}}
        return {"kind":"file", "ref":ref, "digest_domain":"raw_file", "digest":raw_file_digest(data), "bytes":len(content), "reason":"selected by objective and repository priority", "authority":authority, "content_utf8":text}

    def build(self, request: ContextRequest | Mapping[str, object]) -> dict[str, object]:
        if not isinstance(request, ContextRequest): request = ContextRequest.from_dict(request)
        if request.repository_root != "." and Path(request.repository_root).is_absolute() and Path(request.repository_root).resolve() != self.root:
            raise _error("INVALID_REQUEST", "request root differs from engine root")
        items: list[dict[str, object]] = []; omitted: list[dict[str, str]] = []; used = 0
        if request.include["workflow"] and self.workflow is not None:
            try:
                value = self.workflow(request)
                items.append({"kind":"workflow", "ref":request.work_id or "current", "digest_domain":"canonical_json", "digest":raw_file_digest(canonical_json(value)), "bytes":0, "reason":"core read-only readback", "authority":"workflow", "metadata":{"type":"core_readback","ref":request.work_id or "current"}})
            except ProtocolError: raise
            except Exception as exc: raise _error("WORKFLOW_UNAVAILABLE", "core workflow readback unavailable") from exc
        elif request.include["workflow"]:
            raise _error("WORKFLOW_UNAVAILABLE", "workflow readback boundary unavailable")
        kinds = ["source", "test", "config"]
        if any(request.include[k] for k in kinds):
            for ref in self._candidate_files(request):
                is_test = "/test" in ref.lower() or Path(ref).name.startswith("test_")
                is_config = Path(ref).name in {"razr.yaml", "package.json", "pyproject.toml", "tsconfig.json"} or Path(ref).suffix in {".toml", ".ini"}
                kind = "test" if is_test and request.include["tests"] else "config" if is_config and request.include["config"] else "source" if request.include["source"] else None
                if kind is None: continue
                if len(items) >= request.limits["max_files"] or used >= request.limits["max_bytes"]:
                    omitted.append({"ref":ref, "reason":"limit"}); continue
                item = self._item_file(ref, request.limits["max_bytes"] - used)
                if item is None: omitted.append({"ref":ref, "reason":"unsafe"}); continue
                item["kind"] = "file"; item["reason"] = f"selected {kind} evidence by objective and repository priority"
                items.append(item); used += int(item["bytes"])
        if request.include["git"]:
            try:
                result = subprocess.run(["git", "-C", str(self.root), "log", "-8", "--format=%H%x09%s"], cwd=self.root, env={"PATH":os.environ.get("PATH", os.defpath), "GIT_CONFIG_NOSYSTEM":"1", "GIT_CONFIG_GLOBAL":os.devnull, "GIT_OPTIONAL_LOCKS":"0"}, capture_output=True, check=True, timeout=10)
                text = result.stdout.decode("utf-8")
                entries = []
                for line in text.splitlines():
                    commit, _, subject = line.partition("\t"); validate_git_object_id(commit); entries.append({"commit":commit,"subject":subject[:500]})
                items.append({"kind":"git_history","ref":"HEAD","digest_domain":"canonical_json","digest":raw_file_digest(canonical_json(entries)),"bytes":0,"reason":"bounded recent history","authority":"repository","metadata":{"type":"git_history","ref":"HEAD","note":"8 commits maximum"}})
            except (OSError, subprocess.SubprocessError, UnicodeDecodeError, ProtocolError) as exc: raise _error("INTERNAL", "Git history read failed") from exc
        stale: list[str] = []
        if request.include["memory"]:
            if self.memory is None: raise _error("MEMORY_UNAVAILABLE", "Memory recall boundary unavailable", retryable=True)
            try: recalled = self.memory.recall(request.objective, paths=list(request.paths), limit=request.limits["max_memory_records"])
            except ProtocolError as exc: raise _error("MEMORY_UNAVAILABLE", "Memory recall failed", retryable=exc.retryable) from exc
            stale = list(recalled["stale_ids"])
            for record in recalled["records"]:
                items.append({"kind":"memory", "ref":record["id"], "digest_domain":"canonical_json", "digest":record["record_digest"], "bytes":0, "reason":"lower-priority experience; repository evidence outranks it", "authority":"memory", "metadata":{"type":"memory_record","ref":record["id"]}})
        items.sort(key=lambda item: (item["kind"], item["ref"], item["digest"]))
        sources = []
        for item in items:
            if item["kind"] == "memory": continue
            if item["kind"] == "file":
                ref = str(item["ref"]); base = Path(ref).name
                source_kind = "test" if "/test" in ref.lower() or base.startswith("test_") else "config" if base in {"razr.yaml", "package.json", "pyproject.toml", "tsconfig.json"} or Path(ref).suffix in {".toml", ".ini"} else "source"
            elif item["kind"] == "git_history": source_kind = "git"
            else: source_kind = "workflow"
            sources.append({"kind": source_kind, "ref":item["ref"], "digest_domain":item["digest_domain"], "digest":item["digest"], "reason":item["reason"]})
        packet = {"schema_version":1,"request_id":request.request_id,"authoritative_sources":sources,"items":items,"omitted":omitted,"stale_memory_ids":sorted(stale)}
        packet["packet_digest"] = raw_file_digest(canonical_json({k:packet[k] for k in packet if k != "packet_digest"}))
        return packet

    def context(self, request: ContextRequest | Mapping[str, object]) -> dict[str, object]:
        parsed = request if isinstance(request, ContextRequest) else ContextRequest.from_dict(request)
        result = self.build(parsed)
        digest = result.pop("packet_digest")
        result = {"packet_digest": digest, **result}
        return {"schema_version": 1, "ok": True, "request_id": parsed.request_id, "result": result}


def build_context(request: ContextRequest | Mapping[str, object], repository_root: Path | str, **kwargs: object) -> dict[str, object]:
    return ContextEngine(repository_root, **kwargs).context(request)
