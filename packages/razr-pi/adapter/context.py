"""Deterministic, bounded, read-only repository context assembly (M03)."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
from typing import Any, Callable, Mapping

from .core_subprocess import _git_head, _source_digest, read_workflow_context
from .protocol import (CoreRequest, ProtocolError, WorkflowRead, canonical_json,
                       raw_file_digest, validate_git_object_id, validate_source_digest)

KINDS = ("source", "tests", "config", "git", "workflow", "memory")
_LIMITS = {"max_files": 200, "max_bytes": 2_000_000, "max_memory_records": 100}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".yaml", ".yml", ".json", ".toml", ".ini"}
_CONFIG_NAMES = {"razr.yaml", "package.json", "pyproject.toml", "tsconfig.json"}
_PRIVATE_DIRS = {".git", ".razr", ".hg", ".svn", ".venv", "venv", "node_modules", "dist", "build", "target", "vendor", "__pycache__", ".cache", "cache", "tmp", "temp", ".pi", ".herdr"}
_PRIVATE_NAMES = {".env", ".env.local", ".envrc", "credentials", "secrets"}
_MAX_DISCOVERED = 4096
_MAX_VISITED_DIRS = 512
_MAX_DIR_ENTRIES = 512
_MAX_VISITED_ENTRIES = 8192
_MAX_QUEUE = 512
_MAX_DEPTH = 16
_MAX_HISTORY_BYTES = 64 * 1024


def _error(code: str, message: str, **details: object) -> ProtocolError:
    return ProtocolError(code, message, details=details)


def _without_nulls(value: object) -> object:
    if isinstance(value, dict):
        return {key: _without_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        if any(item is None for item in value):
            raise _error("INTERNAL", "null is not permitted in context arrays")
        return [_without_nulls(item) for item in value]
    return value


def _assert_no_nulls(value: object, path: str = "response") -> None:
    if value is None:
        raise _error("INTERNAL", "null is not permitted in context response", path=path)
    if isinstance(value, dict):
        for key, item in value.items(): _assert_no_nulls(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value): _assert_no_nulls(item, f"{path}[{index}]")


def _ref(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise _error("PATH_ESCAPE", "repository-relative POSIX path required")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value or value == ".":
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
        fields = {"schema_version", "request_id", "objective", "repository_root", "work_id", "paths", "include", "limits", "observed"}
        if not isinstance(value, Mapping) or set(value) - fields:
            raise _error("INVALID_REQUEST", "unknown context request field")
        required = ("schema_version", "request_id", "objective", "repository_root", "include", "limits")
        if any(key not in value or value[key] is None for key in required):
            raise _error("INVALID_REQUEST", "missing context request field")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise _error("INVALID_REQUEST", "schema_version must be integer 1")
        if any(key in value and value[key] is None for key in ("work_id", "paths", "observed")):
            raise _error("INVALID_REQUEST", "null is not permitted; omit optional fields")
        include = value["include"]
        if not isinstance(include, dict) or set(include) != set(KINDS) or any(type(v) is not bool for v in include.values()):
            raise _error("INVALID_REQUEST", "include is not a closed boolean object")
        limits = value["limits"]
        if not isinstance(limits, dict) or set(limits) != set(_LIMITS):
            raise _error("INVALID_REQUEST", "limits is not a closed object")
        checked_limits: dict[str, int] = {}
        for key, maximum in _LIMITS.items():
            number = limits[key]
            if type(number) is not int or not 0 < number <= maximum:
                raise _error("LIMIT_EXCEEDED", "context limit is outside the bounded range", limit=key)
            checked_limits[key] = number
        request_id, objective, repository = value["request_id"], value["objective"], value["repository_root"]
        if not isinstance(request_id, str) or not _ID.fullmatch(request_id) or not isinstance(objective, str) or not objective or len(objective) > 4000 or repository != ".":
            raise _error("INVALID_REQUEST", "repository_root must be the exact engine checkout token '.'")
        paths = value.get("paths", [])
        if not isinstance(paths, list) or len(paths) > 256 or any(not isinstance(p, str) for p in paths):
            raise _error("INVALID_REQUEST", "paths is not a bounded list")
        for path in paths:
            _ref(path)
        work_id = value.get("work_id")
        if work_id is not None and (not isinstance(work_id, str) or not _ID.fullmatch(work_id)):
            raise _error("INVALID_REQUEST", "invalid work ID")
        observed = value.get("observed")
        if observed is not None:
            if not isinstance(observed, dict) or set(observed) != {"commit", "source_digest"}:
                raise _error("INVALID_REQUEST", "observed identity is not closed")
            if type(observed["commit"]) is not str or type(observed["source_digest"]) is not str:
                raise _error("INVALID_REQUEST", "observed identity values must be strings")
            validate_git_object_id(observed["commit"])
            validate_source_digest(observed["source_digest"])
        return cls(1, request_id, objective, repository, dict(include), checked_limits, work_id, tuple(paths), observed)

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"schema_version": 1, "request_id": self.request_id, "objective": self.objective,
                                    "repository_root": self.repository_root, "include": dict(self.include), "limits": dict(self.limits)}
        if self.work_id is not None: result["work_id"] = self.work_id
        if self.paths: result["paths"] = list(self.paths)
        if self.observed is not None: result["observed"] = dict(self.observed)
        return result


def _deny_ref(ref: str) -> None:
    parts = PurePosixPath(ref).parts
    if any(part in _PRIVATE_DIRS or part in _PRIVATE_NAMES or part.startswith(".env") for part in parts):
        raise _error("PATH_ESCAPE", "private or control path refused", ref=ref)


def _open_nofollow(root: Path, ref: str) -> int:
    """Open every component without following links, then open the leaf safely."""
    parts = PurePosixPath(_ref(ref)).parts
    _deny_ref(ref)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    try:
        current = os.open(root, os.O_RDONLY | directory | nofollow)
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | directory | nofollow, dir_fd=current)
            os.close(current); current = child
        leaf = os.open(parts[-1], os.O_RDONLY | nofollow, dir_fd=current)
        os.close(current)
        return leaf
    except OSError as exc:
        try: os.close(current)
        except (UnboundLocalError, OSError): pass
        raise _error("PATH_ESCAPE", "repository path cannot be opened safely", ref=ref) from exc


def _read_bounded(root: Path, ref: str, max_content: int) -> tuple[bytes, str, int] | None:
    """Read max_content+1 from one no-follow FD; digest only a complete bounded file."""
    if max_content <= 0: return None
    fd = _open_nofollow(root, ref)
    try:
        info = os.fstat(fd)
        if (info.st_mode & 0o170000) != 0o100000 or info.st_nlink != 1:
            return None
        data = bytearray()
        while len(data) <= max_content:
            chunk = os.read(fd, min(65536, max_content + 1 - len(data)))
            if not chunk: break
            data.extend(chunk)
        if len(data) > max_content:
            return None
        raw = bytes(data)
        return raw, raw_file_digest(raw), len(raw)
    except OSError as exc:
        raise _error("INTERNAL", "bounded descriptor read failed", ref=ref) from exc
    finally:
        os.close(fd)


def _git_env(git: Path) -> dict[str, str]:
    return {"PATH": str(git.parent), "HOME": os.devnull, "LANG": "C", "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0", "GIT_PAGER": "cat"}


def _git_history(root: Path) -> list[dict[str, str]]:
    found = shutil.which("git")
    if not found:
        raise _error("INTERNAL", "Git executable unavailable")
    git = Path(found).resolve(strict=True)
    if not git.is_file() or git.is_symlink():
        raise _error("INTERNAL", "Git executable is not canonical")
    argv = [str(git), "-C", str(root), "--no-pager", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
            "log", "-8", "--format=%H%x09%.500s"]
    try:
        result = subprocess.run(argv, cwd=root, env=_git_env(git), stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise _error("INTERNAL", "bounded Git history read failed") from exc
    if len(result.stdout) > _MAX_HISTORY_BYTES or result.stderr:
        raise _error("LIMIT_EXCEEDED", "Git history output exceeded bound")
    try:
        rows = []
        for line in result.stdout.decode("utf-8").splitlines():
            commit, separator, subject = line.partition("\t")
            if not separator: raise ValueError("missing history separator")
            validate_git_object_id(commit); rows.append({"commit": commit, "subject": subject})
        return rows
    except (UnicodeDecodeError, ValueError, ProtocolError) as exc:
        raise _error("INTERNAL", "Git history response malformed") from exc


class ContextEngine:
    """Select bounded context; repository and typed core readback outrank Memory."""
    def __init__(self, repository_root: Path | str, *, memory: Any = None,
                 core_request: CoreRequest | None = None,
                 workflow: Callable[[ContextRequest], WorkflowRead] | None = None):
        self.root = _root(repository_root)
        self.memory = memory
        self.core_request = core_request
        self.workflow = workflow

    def _candidate_files(self, request: ContextRequest) -> tuple[list[str], list[dict[str, str]]]:
        objective = set(re.findall(r"[a-z0-9][a-z0-9_.-]{2,}", request.objective.lower()))
        refs: list[str] = []
        omitted: list[dict[str, str]] = []
        seen: set[str] = set()
        # Explicit paths use exactly the same policy as discovery; they cannot bypass controls.
        for ref in request.paths:
            _deny_ref(_ref(ref))
            fd = _open_nofollow(self.root, ref)
            try:
                info = os.fstat(fd)
                if (info.st_mode & 0o170000) != 0o100000 or info.st_nlink != 1:
                    raise _error("PATH_ESCAPE", "explicit path is not a private regular file", ref=ref)
            finally:
                os.close(fd)
            refs.append(ref); seen.add(ref)
        queue: list[tuple[Path, int]] = [(self.root, 0)]
        visited_dirs = visited_entries = discovered = 0
        while queue and visited_dirs < _MAX_VISITED_DIRS and visited_entries < _MAX_VISITED_ENTRIES and discovered < _MAX_DISCOVERED:
            directory, depth = queue.pop(0); visited_dirs += 1
            try:
                # Read only a bounded prefix plus one sentinel; sorting makes selection deterministic.
                with os.scandir(directory) as scan:
                    entries = []
                    for _ in range(_MAX_DIR_ENTRIES + 1):
                        try: entry = next(scan)
                        except StopIteration: break
                        entries.append(entry)
                entries.sort(key=lambda e: e.name)
            except OSError:
                continue
            if len(entries) > _MAX_DIR_ENTRIES:
                omitted.append({"ref": directory.relative_to(self.root).as_posix() or ".", "reason": "limit"})
                entries = entries[:_MAX_DIR_ENTRIES]
            for entry in entries:
                visited_entries += 1
                ref = Path(entry.path).relative_to(self.root).as_posix()
                if entry.name in _PRIVATE_DIRS or entry.name in _PRIVATE_NAMES or entry.name.startswith(".env"):
                    omitted.append({"ref": ref, "reason": "unsafe"}); continue
                try: info = entry.stat(follow_symlinks=False)
                except OSError:
                    omitted.append({"ref": ref, "reason": "unsafe"}); continue
                mode = info.st_mode & 0o170000
                if mode == 0o040000:
                    if depth >= _MAX_DEPTH or len(queue) >= _MAX_QUEUE:
                        omitted.append({"ref": ref, "reason": "limit"})
                    elif len(queue) < _MAX_QUEUE:
                        queue.append((Path(entry.path), depth + 1))
                    continue
                if mode != 0o100000 or info.st_nlink != 1 or Path(entry.name).suffix.lower() not in _TEXT_SUFFIXES: continue
                discovered += 1
                if ref not in seen: refs.append(ref); seen.add(ref)
        if queue or visited_dirs >= _MAX_VISITED_DIRS or visited_entries >= _MAX_VISITED_ENTRIES or discovered >= _MAX_DISCOVERED:
            omitted.append({"ref": ".", "reason": "limit"})
        def score(ref: str) -> tuple[int, str]:
            low, base = ref.lower(), Path(ref).name.lower(); rank = 0
            if any(token in low for token in objective): rank -= 30
            if base in {"readme.md", "razr.yaml", "pyproject.toml", "package.json"}: rank -= 20
            if "/test" in low or base.startswith("test_"): rank -= 10
            return rank, ref
        return sorted(refs, key=score), omitted

    def _item_file(self, ref: str, remaining: int) -> dict[str, object] | None:
        _deny_ref(_ref(ref))
        value = _read_bounded(self.root, ref, remaining)
        if value is None: return None
        content, digest, total = value
        try: text = content.decode("utf-8")
        except UnicodeDecodeError:
            return {"kind": "file", "ref": ref, "digest_domain": "raw_file", "digest": digest, "bytes": 0,
                    "reason": "binary metadata", "authority": "repository", "metadata": {"type": "binary", "ref": ref}}
        return {"kind": "file", "ref": ref, "digest_domain": "raw_file", "digest": digest, "bytes": len(content),
                "reason": "selected bounded repository evidence", "authority": "repository", "content_utf8": text}

    @staticmethod
    def _workflow_value(read: WorkflowRead) -> dict[str, object]:
        candidate = {"repository": read.candidate_repository, "branch": read.candidate_branch,
                     "commit": read.candidate_commit, "source_digest": read.candidate_source_digest}
        return _without_nulls({"work_id": read.work_id, "stage": read.stage, "status": read.status, "generation": read.generation,
                "node_id": read.node_id, "node_status": read.node_status, "node_attempt": read.node_attempt,
                "node_result_ref": read.node_result_ref, "candidate": candidate, "status_row": read.status_row,
                "raw_state_digest": read.raw_state_digest, "raw_state_bytes": read.raw_state_bytes,
                "core_identity": read.raw_state_core_identity, "source_digest": read.raw_state_source_digest})

    def _read_workflow(self, request: ContextRequest) -> WorkflowRead:
        if not request.work_id: raise _error("INVALID_REQUEST", "work_id is required for workflow context")
        if self.core_request is not None:
            # The shared boundary owns status -> validate -> raw state and provenance checks.
            try: return read_workflow_context(self.core_request, request.work_id)
            except ProtocolError as exc:
                if exc.code in {"SOURCE_CHANGED", "IDENTITY_MISMATCH", "DIGEST_MISMATCH"}:
                    raise
                raise _error("WORKFLOW_UNAVAILABLE", "typed core workflow readback failed", cause=exc.code) from exc
        if self.workflow is None:
            raise _error("WORKFLOW_UNAVAILABLE", "typed core workflow readback boundary unavailable")
        try: read = self.workflow(request)
        except ProtocolError: raise
        except Exception as exc: raise _error("WORKFLOW_UNAVAILABLE", "workflow readback failed") from exc
        if not isinstance(read, WorkflowRead):
            raise _error("WORKFLOW_UNAVAILABLE", "workflow seam did not return WorkflowRead")
        return read

    def _verify_workflow_candidate(self, read: WorkflowRead) -> None:
        try:
            actual_commit = _git_head(self.root)
            actual_source = _source_digest(self.root, actual_commit)
        except ProtocolError as exc:
            raise _error("SOURCE_CHANGED", "workflow candidate identity could not be verified", cause=exc.code) from exc
        if ((read.candidate_commit is not None and read.candidate_commit != actual_commit)
                or (read.candidate_source_digest is not None and read.candidate_source_digest != actual_source)):
            raise _error("IDENTITY_MISMATCH", "core workflow candidate differs from verified checkout")

    def _verify_observed(self, observed: dict[str, str], read: WorkflowRead | None = None) -> None:
        try:
            actual_commit = _git_head(self.root)
            actual_source = _source_digest(self.root, actual_commit)
        except ProtocolError as exc:
            raise _error("SOURCE_CHANGED", "candidate identity could not be verified", cause=exc.code) from exc
        if observed["commit"] != actual_commit or observed["source_digest"] != actual_source:
            raise _error("SOURCE_CHANGED", "observed candidate identity is stale", expected=observed,
                         actual={"commit": actual_commit, "source_digest": actual_source})
        if read is not None and (read.candidate_commit not in (None, actual_commit) or read.candidate_source_digest not in (None, actual_source)):
            raise _error("IDENTITY_MISMATCH", "core candidate differs from verified checkout")

    def build(self, request: ContextRequest | Mapping[str, object]) -> dict[str, object]:
        parsed = request if isinstance(request, ContextRequest) else ContextRequest.from_dict(request)
        if parsed.repository_root != "." and Path(parsed.repository_root).is_absolute() and Path(parsed.repository_root).resolve() != self.root:
            raise _error("INVALID_REQUEST", "request root differs from engine root")
        items: list[dict[str, object]] = []; omitted: list[dict[str, str]] = []; used = 0; read: WorkflowRead | None = None
        if parsed.observed is not None:
            self._verify_observed(parsed.observed)
        if parsed.include["workflow"]:
            read = self._read_workflow(parsed)
            self._verify_workflow_candidate(read)
            if parsed.observed is not None: self._verify_observed(parsed.observed, read)
            value = self._workflow_value(read)
            items.append({"kind": "workflow", "ref": read.work_id, "digest_domain": "canonical_json",
                          "digest": raw_file_digest(canonical_json(value)), "bytes": 0,
                          "reason": "shared core status/validate/readback", "authority": "repository",
                          "metadata": {"type": "core_readback", "ref": read.work_id,
                                       "note": "relevant state only; raw state is not dumped"}})
        refs, discovery_omitted = self._candidate_files(parsed); omitted.extend(discovery_omitted)
        for ref in refs:
            is_test = "/test" in ref.lower() or Path(ref).name.startswith("test_")
            is_config = Path(ref).name in _CONFIG_NAMES or Path(ref).suffix.lower() in {".toml", ".ini"}
            kind = "tests" if is_test and parsed.include["tests"] else "config" if is_config and parsed.include["config"] else "source" if parsed.include["source"] else None
            if kind is None: continue
            if len(items) >= parsed.limits["max_files"] or used >= parsed.limits["max_bytes"]:
                omitted.append({"ref": ref, "reason": "limit"}); continue
            item = self._item_file(ref, parsed.limits["max_bytes"] - used)
            if item is None: omitted.append({"ref": ref, "reason": "unsafe"}); continue
            item["reason"] = f"selected bounded {kind} evidence"; items.append(item); used += int(item["bytes"])
        if parsed.include["git"]:
            entries = _git_history(self.root)
            items.append({"kind": "git_history", "ref": "HEAD", "digest_domain": "canonical_json",
                          "digest": raw_file_digest(canonical_json(entries)), "bytes": 0,
                          "reason": "bounded recent history", "authority": "repository",
                          "metadata": {"type": "git_history", "ref": "HEAD", "note": "8 commits maximum"}})
        stale: list[str] = []
        if parsed.include["memory"]:
            if self.memory is None: raise _error("MEMORY_UNAVAILABLE", "Memory recall boundary unavailable", retryable=True)
            try: recalled = self.memory.recall(parsed.objective, paths=list(parsed.paths), limit=parsed.limits["max_memory_records"])
            except ProtocolError as exc: raise _error("MEMORY_UNAVAILABLE", "Memory recall failed", retryable=exc.retryable) from exc
            stale = list(recalled["stale_ids"])
            for record in recalled["records"]:
                status = record["status"]
                label = "stale conflict: repository evidence outranks Memory" if status == "stale" else "lower authority than repository evidence"
                items.append({"kind": "memory", "ref": record["id"], "digest_domain": "canonical_json",
                              "digest": record["record_digest"], "bytes": 0,
                              "reason": f"{label}; status={status}", "authority": "memory",
                              "metadata": {"type": "memory_record", "ref": record["id"], "note": f"status={status}; authority=lower-than-repository"}})
        items.sort(key=lambda item: (item["kind"], item["ref"], item["digest"]))
        sources = []
        for item in items:
            if item["kind"] == "memory": continue
            if item["kind"] == "file":
                ref = str(item["ref"]); base = Path(ref).name
                source_kind = "test" if "/test" in ref.lower() or base.startswith("test_") else "config" if base in _CONFIG_NAMES or Path(ref).suffix.lower() in {".toml", ".ini"} else "source"
            elif item["kind"] == "git_history": source_kind = "git"
            else: source_kind = "workflow"
            sources.append({"kind": source_kind, "ref": item["ref"], "digest_domain": item["digest_domain"], "digest": item["digest"], "reason": item["reason"]})
        packet = {"schema_version": 1, "request_id": parsed.request_id, "authoritative_sources": sources,
                  "items": items, "omitted": omitted, "stale_memory_ids": sorted(stale)}
        _assert_no_nulls(packet)
        packet["packet_digest"] = raw_file_digest(canonical_json(packet))
        _assert_no_nulls(packet)
        return packet

    def context(self, request: ContextRequest | Mapping[str, object]) -> dict[str, object]:
        parsed = request if isinstance(request, ContextRequest) else ContextRequest.from_dict(request)
        result = self.build(parsed)
        digest = result.pop("packet_digest")
        return {"schema_version": 1, "ok": True, "request_id": parsed.request_id,
                "result": {"packet_digest": digest, **result}}


def build_context(request: ContextRequest | Mapping[str, object], repository_root: Path | str, **kwargs: object) -> dict[str, object]:
    return ContextEngine(repository_root, **kwargs).context(request)
