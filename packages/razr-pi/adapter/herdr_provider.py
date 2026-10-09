"""Strict, mechanical Herdr transport for the M04 provider boundary.

This module owns transport observations and append-only attempt evidence only.
It never mutates Razr state, chooses work, infers approval, or classifies a
process observation as a workflow transition.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import dataclasses
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import subprocess
import shutil
import stat
import time
from typing import Callable, Iterator, Mapping, Sequence

from .protocol import (ERROR_CODES, CoreRequest, ProtocolError, WorkflowRead,
                       canonical_json, raw_file_digest)
from .core_subprocess import read_workflow, verify_candidate_identity

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_MODEL = "openai-codex/gpt-5.6-luna"
_LAUNCH_EVIDENCE_CODES = frozenset({"LAUNCH_FAILED", "TIMEOUT", "OUTPUT_LIMIT", "PROVIDER_UNAVAILABLE", "STALE_GENERATION", "CANDIDATE_CHANGED"})
_EVIDENCE_KIND_DOMAINS = {"launch_failure": "raw_file", "stdout": "raw_file", "stderr": "raw_file", "session": "raw_file", "event": "raw_file", "result": "raw_file", "candidate": "canonical_json", "request_packet": "canonical_json", "capability_report": "canonical_json", "identity": "canonical_json", "report": "canonical_json"}
_CLI_TIMEOUT_MS = 10_000
_CLI_MAX_OUTPUT = 262_144
_CAPABILITIES = frozenset({
    "fresh_context", "saved_session", "neutral_instructions", "working_directory",
    "environment_policy", "bounded_time", "bounded_output", "stream_capture",
    "interrupt", "tool_policy_observation", "runtime_identity",
})
_TERMINATIONS = {"SUCCEEDED", "FAILED", "INTERRUPTED", "TIMED_OUT", "UNKNOWN"}
_STATUSES = {"STARTING", "ACTIVE", "SETTLED", "STOPPING", "TERMINATED", "UNKNOWN"}
_OUTCOMES = _TERMINATIONS
_REASONS = {"cancel", "timeout", "policy_violation"}
_BASELINE_CAPABILITIES = ("fresh_context", "working_directory", "bounded_time", "bounded_output", "runtime_identity")
_CAPABILITY_ORDER = ("fresh_context", "saved_session", "neutral_instructions", "working_directory", "environment_policy", "bounded_time", "bounded_output", "stream_capture", "interrupt", "tool_policy_observation", "runtime_identity")


def _fail(code: str, message: str, **details: object) -> ProtocolError:
    return ProtocolError(code, message, details=details)


def _id(value: object, name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise _fail("INVALID_REQUEST", f"invalid {name}")
    return value


def _branch(value: object, name: str) -> str:
    if not isinstance(value, str) or not _BRANCH.fullmatch(value) or ".." in value or value.endswith("/"):
        raise _fail("INVALID_REQUEST", f"invalid {name}")
    return value


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise _fail("INVALID_REQUEST", f"invalid {name}")
    return value


def _commit(value: object, name: str = "commit") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise _fail("INVALID_REQUEST", f"invalid {name}")
    return value


def _timestamp(value: object, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z", value):
        raise _fail("INVALID_REQUEST", f"invalid {name}")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_dir(value: object, name: str) -> Path:
    if not isinstance(value, str) or not os.path.isabs(value):
        raise _fail("INVALID_REQUEST", f"{name} must be absolute")
    path = Path(value)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise _fail("INVALID_REQUEST", f"{name} is unavailable") from exc
    if path != resolved or path.is_symlink() or not path.is_dir():
        raise _fail("INVALID_REQUEST", f"invalid {name}")
    return path


def _host() -> dict[str, str]:
    if os.environ.get("HERDR_ENV") != "1":
        raise _fail("FANOUT_UNAVAILABLE", "Herdr environment is not active")
    result = {key: os.environ.get(key, "") for key in ("HERDR_WORKSPACE_ID", "HERDR_TAB_ID", "HERDR_PANE_ID")}
    if any(not value for value in result.values()):
        raise _fail("HERDR_IDENTITY_INCOMPLETE", "workspace, tab, and pane identity are required")
    return {"mode": "herdr", "workspace_id": _id(result["HERDR_WORKSPACE_ID"], "workspace_id"),
            "tab_id": _id(result["HERDR_TAB_ID"], "tab_id"), "pane_id": _id(result["HERDR_PANE_ID"], "pane_id")}


def _identity(value: object, name: str) -> dict[str, str]:
    identity = _closed(value, {"workspace_id", "tab_id", "pane_id"}, name)
    return {key: _id(identity[key], key) for key in ("workspace_id", "tab_id", "pane_id")}


def _execution_host(value: object) -> dict[str, object]:
    host = _closed(value, {"mode", "caller", "target"}, "execution host")
    if host["mode"] != "herdr":
        raise _fail("IDENTITY_MISMATCH", "execution host mode differs")
    caller = _identity(host["caller"], "caller host")
    current = _host()
    expected_caller = {key: current[key] for key in ("workspace_id", "tab_id", "pane_id")}
    if caller != expected_caller:
        raise _fail("IDENTITY_MISMATCH", "caller Herdr identity differs")
    return {"mode": "herdr", "caller": caller, "target": _identity(host["target"], "target host")}


def _target(value: Mapping[str, object]) -> Mapping[str, object]:
    """Return target identity; legacy test/evidence values remain replayable only."""
    target = value.get("target")
    return target if isinstance(target, dict) else value


def _ledger_host(value: Mapping[str, object]) -> dict[str, object]:
    return {"caller": dict(value["caller"]), "target": dict(value["target"])}


def _closed(value: object, fields: set[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise _fail("ARTIFACT_UNAVAILABLE", f"{name} has unknown or missing fields")
    return value


def _strict_json(data: bytes) -> object:
    def reject_duplicate(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    value = json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicate, parse_constant=lambda token: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    if canonical_json(value) != data: raise ValueError("noncanonical JSON")
    return value


def _envelope(raw: bytes, name: str, expected_ids: set[str] | None = None) -> dict[str, object]:
    try: value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise _fail("ARTIFACT_UNAVAILABLE", f"malformed {name} response") from exc
    if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not value["id"].startswith("cli:") or not value["id"][4:] or not re.fullmatch(r"cli:[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", value["id"]):
        raise _fail("ARTIFACT_UNAVAILABLE", f"invalid {name} event identity")
    if expected_ids is not None and value["id"] not in expected_ids: raise _fail("IDENTITY_MISMATCH", f"unexpected {name} event identity")
    return _closed(value, {"id", "result"}, name)


def _manifest(path: Path) -> tuple[str, list[dict[str, object]]]:
    entries: list[dict[str, object]] = []
    total = 0
    for child in sorted(path.iterdir(), key=lambda p: p.name):
        if child.is_symlink() or child.name.startswith(".razr-herdr-"):
            raise _fail("RESOURCE_REUSED", "root contains an unsafe or foreign entry")
        if child.is_dir():
            raise _fail("RESOURCE_REUSED", "root must remain empty before dispatch")
        data = child.read_bytes()
        if len(data) > 1_048_576 or len(entries) >= 64 or total + len(data) > 8_388_608:
            raise _fail("LIMIT_EXCEEDED", "root manifest exceeds bounded limits")
        total += len(data)
        entries.append({"path": child.name, "type": "file", "size": len(data), "digest": raw_file_digest(data)})
    return raw_file_digest(canonical_json(entries)), entries


def _read_private_bytes(path: Path, private_root: Path, maximum: int = 1_048_576) -> bytes:
    try:
        canonical = path.resolve(strict=True)
        if canonical != path or not canonical.is_relative_to(private_root) or canonical.is_symlink(): raise _fail("PATH_ESCAPE", "private record path is not canonical")
        fd = os.open(canonical, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum: raise _fail("IDENTITY_MISMATCH", "private record file is unsafe")
            data = os.read(fd, maximum + 1)
            if len(data) != info.st_size or len(data) > maximum: raise _fail("OUTPUT_LIMIT", "private record exceeds bound")
            return data
        finally: os.close(fd)
    except ProtocolError: raise
    except OSError as exc: raise _fail("IDENTITY_MISMATCH", "private record is unavailable") from exc


@contextmanager
def _evidence_directory(root: Path, *, create: bool) -> Iterator[int]:
    """Open worktree/.razr/evidence without following any pathname component."""
    root = _canonical_dir(str(root), "evidence worktree")
    nofollow = getattr(os, "O_NOFOLLOW", 0); directory = getattr(os, "O_DIRECTORY", 0)
    rootfd = razrfd = evidencefd = -1
    try:
        rootfd = os.open(root, os.O_RDONLY | directory | nofollow)
        parent = rootfd
        for name, mode in ((".razr", 0o700), ("evidence", 0o700)):
            created = False
            if create:
                try: os.mkdir(name, mode, dir_fd=parent); created = True
                except FileExistsError: pass
            child = os.open(name, os.O_RDONLY | directory | nofollow, dir_fd=parent)
            info = os.fstat(child); path_info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISDIR(info.st_mode) or info.st_nlink < 1
                    or (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino)):
                os.close(child); raise _fail("PATH_ESCAPE", "evidence directory identity changed")
            if created:
                os.fchmod(child, mode); os.fsync(child); os.fsync(parent)
            if name == ".razr": razrfd = child
            else: evidencefd = child
            parent = child
        yield evidencefd
    except ProtocolError:
        raise
    except FileNotFoundError as exc:
        raise _fail("ARTIFACT_UNAVAILABLE", "evidence directory is unavailable") from exc
    except OSError as exc:
        raise _fail("PATH_ESCAPE", "evidence directory is linked or unavailable") from exc
    finally:
        for fd in (evidencefd, razrfd, rootfd):
            if fd >= 0:
                try: os.close(fd)
                except OSError: pass


def _evidence_name(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,240}", name):
        raise _fail("INVALID_REQUEST", "invalid evidence filename")
    return name


def _read_evidence_at(dirfd: int, name: str, maximum: int = 1_048_576) -> bytes:
    name = _evidence_name(name)
    try: fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dirfd)
    except FileNotFoundError: raise
    except OSError as exc: raise _fail("PATH_ESCAPE", "evidence file is linked or unavailable") from exc
    try:
        before = os.fstat(fd); path_info = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or (before.st_dev, before.st_ino) != (path_info.st_dev, path_info.st_ino)):
            raise _fail("PATH_ESCAPE", "evidence file identity is unsafe")
        data = os.read(fd, maximum + 1); after = os.fstat(fd)
        if len(data) > maximum: raise _fail("OUTPUT_LIMIT", "evidence file exceeds bound")
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise _fail("ARTIFACT_UNAVAILABLE", "evidence file changed during read")
        return data
    except FileNotFoundError: raise
    except ProtocolError: raise
    except OSError as exc: raise _fail("PATH_ESCAPE", "evidence file identity changed") from exc
    finally: os.close(fd)


def _write_evidence(root: Path, name: str, data: bytes, *, existing_ok: bool = False) -> str:
    name = _evidence_name(name)
    with _evidence_directory(root, create=True) as dirfd:
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
        except FileExistsError:
            if not existing_ok: raise
            if _read_evidence_at(dirfd, name, len(data)) != data:
                raise _fail("IDENTITY_MISMATCH", "existing evidence differs")
            return ".razr/evidence/" + name
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.fsync(dirfd)
    return ".razr/evidence/" + name


def _evidence_ref_bytes(root: Path, ref: Mapping[str, object], maximum: int = 1_048_576) -> bytes:
    relative = PurePosixPath(str(ref.get("ref", "")))
    if len(relative.parts) != 3 or relative.parts[:2] != (".razr", "evidence"):
        raise _fail("PATH_ESCAPE", "evidence reference escapes boundary")
    name = _evidence_name(relative.parts[2])
    with _evidence_directory(root, create=False) as dirfd:
        return _read_evidence_at(dirfd, name, maximum)


_MARKER_NAME = ".razr-herdr-root.json"


def _marker_bytes(root: Path) -> bytes | None:
    """Read a root marker relative to one pinned root descriptor, never by pathname."""
    root = _canonical_dir(str(root), "provider root"); rootfd = markerfd = -1
    try:
        rootfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        root_info = os.fstat(rootfd); path_info = os.stat(root, follow_symlinks=False)
        if (not stat.S_ISDIR(root_info.st_mode) or (root_info.st_dev, root_info.st_ino) != (path_info.st_dev, path_info.st_ino)):
            raise _fail("PATH_ESCAPE", "provider root identity changed")
        try: markerfd = os.open(_MARKER_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=rootfd)
        except FileNotFoundError: return None
        except OSError as exc: raise _fail("PATH_ESCAPE", "root ownership marker is linked or unavailable") from exc
        before = os.fstat(markerfd); marker_path = os.stat(_MARKER_NAME, dir_fd=rootfd, follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_mode & 0o077
                or (before.st_dev, before.st_ino) != (marker_path.st_dev, marker_path.st_ino)):
            raise _fail("PATH_ESCAPE", "root ownership marker identity is unsafe")
        data = os.read(markerfd, 65_537); after = os.fstat(markerfd)
        if len(data) > 65_536 or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise _fail("PATH_ESCAPE", "root ownership marker changed during read")
        return data
    except ProtocolError: raise
    except OSError as exc: raise _fail("PATH_ESCAPE", "root ownership marker cannot be inspected") from exc
    finally:
        if markerfd >= 0:
            try: os.close(markerfd)
            except OSError: pass
        if rootfd >= 0:
            try: os.close(rootfd)
            except OSError: pass


def _marker_present(root: Path, root_id: str | None = None, digest: str | None = None) -> bool:
    data = _marker_bytes(root)
    if data is None: return False
    if root_id is not None or digest is not None:
        if root_id is None or digest is None or data != canonical_json({"schema_version": 1, "root_id": root_id, "manifest_digest": digest}):
            raise _fail("RESOURCE_REUSED", "root ownership identity differs")
    return True


def _marker(root: Path, root_id: str, digest: str, *, create: bool) -> None:
    expected = canonical_json({"schema_version": 1, "root_id": root_id, "manifest_digest": digest})
    if _marker_present(root, root_id, digest):
        if not create: return
        raise _fail("RESOURCE_REUSED", "root identity was already used")
    if not create: raise _fail("RESOURCE_REUSED", "root ownership marker is missing")
    root = _canonical_dir(str(root), "provider root"); rootfd = fd = -1
    try:
        rootfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        root_info = os.fstat(rootfd); path_info = os.stat(root, follow_symlinks=False)
        if (not stat.S_ISDIR(root_info.st_mode) or (root_info.st_dev, root_info.st_ino) != (path_info.st_dev, path_info.st_ino)):
            raise _fail("PATH_ESCAPE", "provider root identity changed")
        try: fd = os.open(_MARKER_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=rootfd)
        except FileExistsError as exc: raise _fail("RESOURCE_REUSED", "root ownership raced with another attempt") from exc
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = -1; stream.write(expected); stream.flush(); os.fsync(stream.fileno())
            info = os.fstat(stream.fileno()); marker_path = os.stat(_MARKER_NAME, dir_fd=rootfd, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077
                    or (info.st_dev, info.st_ino) != (marker_path.st_dev, marker_path.st_ino)):
                raise _fail("PATH_ESCAPE", "created root ownership marker identity is unsafe")
        os.fsync(rootfd)
    except ProtocolError: raise
    except OSError as exc: raise _fail("PATH_ESCAPE", "root ownership marker cannot be created safely") from exc
    finally:
        if fd >= 0:
            try: os.close(fd)
            except OSError: pass
        if rootfd >= 0:
            try: os.close(rootfd)
            except OSError: pass


class AttemptLedger:
    """Strict global hash-chain replay and append for M04 events."""
    STARTED = {"schema_version", "event", "attempt_id", "work_id", "node_id", "observed_generation",
               "observed_ordinal", "worker_id", "branch", "worktree_ref", "allowance_id", "provider_id",
               "started_at", "started_at_source", "candidate", "candidate_evidence", "request_packet",
               "output_root_id", "output_root_digest", "session_root_id", "session_root_digest",
               "request_digest", "prev_digest", "record_digest"}
    SETTLED = {"schema_version", "event", "attempt_id", "work_id", "node_id", "observed_generation",
               "observed_ordinal", "provider_id", "worktree_ref", "branch", "settled_at", "candidate",
               "candidate_evidence", "request_packet", "output_root_id", "output_root_digest",
               "session_root_id", "session_root_digest", "outcome", "request_digest", "evidence_refs",
               "prev_digest", "record_digest"}
    OPTIONAL_STARTED = {"herdr", "prepared_record_digest"}
    OPTIONAL_SETTLED = {"herdr", "exit_code", "cleanup", "prepared_record_digest"}

    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def _ref(value: object, name: str, domain: str | None = None) -> None:
        allowed = ({"ref", "digest_domain", "digest", "kind", "error_code"}
                   if name == "evidence_ref" else {"ref", "digest_domain", "digest"})
        required = {"ref", "digest_domain", "digest", "kind"} if name == "evidence_ref" else {"ref", "digest_domain", "digest"}
        if not isinstance(value, dict) or not required <= set(value) or set(value) - required - ({"error_code"} if name == "evidence_ref" else set()):
            raise _fail("LEDGER_CORRUPT", f"invalid {name}")
        if name == "evidence_ref":
            if value["kind"] == "launch_failure":
                if "error_code" not in value or value["error_code"] not in _LAUNCH_EVIDENCE_CODES:
                    raise _fail("LEDGER_CORRUPT", "invalid launch failure evidence")
            elif "error_code" in value:
                raise _fail("LEDGER_CORRUPT", "error_code is only valid for launch_failure evidence")
        ref = value["ref"]
        if not isinstance(ref, str) or not ref or PurePosixPath(ref).is_absolute() or ".." in PurePosixPath(ref).parts or str(PurePosixPath(ref)) != ref:
            raise _fail("PATH_ESCAPE", f"invalid {name} reference")
        if domain and value["digest_domain"] != domain:
            raise _fail("LEDGER_CORRUPT", f"invalid {name} digest domain")
        if name == "evidence_ref":
            if value["kind"] not in _EVIDENCE_KIND_DOMAINS: raise _fail("LEDGER_CORRUPT", "unknown evidence kind")
            if value["digest_domain"] != _EVIDENCE_KIND_DOMAINS[value["kind"]]: raise _fail("LEDGER_CORRUPT", "evidence kind and digest domain differ")
        _digest(value["digest"], f"{name} digest")

    @classmethod
    def _validate_event(cls, event: object, *, allow_legacy_herdr: bool = True) -> dict[str, object]:
        if not isinstance(event, dict) or not isinstance(event.get("schema_version"), int) or isinstance(event.get("schema_version"), bool) or event.get("schema_version") != 1 or event.get("event") not in {"STARTED", "SETTLED"}:
            raise _fail("LEDGER_CORRUPT", "invalid attempt event")
        required = cls.STARTED if event["event"] == "STARTED" else cls.SETTLED
        optional = cls.OPTIONAL_STARTED if event["event"] == "STARTED" else cls.OPTIONAL_SETTLED
        if not (required <= set(event) and set(event) - required <= optional):
            raise _fail("LEDGER_CORRUPT", "attempt event is not closed")
        _id(event["attempt_id"], "attempt_id"); _id(event["work_id"], "work_id"); _id(event["node_id"], "node_id")
        _id(event["worker_id"], "worker_id") if "worker_id" in event else None
        _branch(event["branch"], "branch"); _id(event["allowance_id"], "allowance_id") if "allowance_id" in event else None
        worktree_ref = event["worktree_ref"]
        if not isinstance(worktree_ref, str) or not worktree_ref or "\\" in worktree_ref or worktree_ref.startswith("/") or str(PurePosixPath(worktree_ref)) != worktree_ref or (worktree_ref != "." and any(part in {".", ".."} for part in PurePosixPath(worktree_ref).parts)):
            raise _fail("LEDGER_CORRUPT", "worktree_ref must be a normalized repository-relative POSIX path")
        _id(event["provider_id"], "provider_id"); _id(event["output_root_id"], "output_root_id"); _id(event["session_root_id"], "session_root_id")
        if not isinstance(event["observed_generation"], int) or isinstance(event["observed_generation"], bool) or event["observed_generation"] < 1:
            raise _fail("LEDGER_CORRUPT", "invalid observed generation")
        if not isinstance(event["observed_ordinal"], int) or isinstance(event["observed_ordinal"], bool) or event["observed_ordinal"] < 0:
            raise _fail("LEDGER_CORRUPT", "invalid observed ordinal")
        _digest(event["output_root_digest"], "output_root_digest"); _digest(event["session_root_digest"], "session_root_digest")
        _digest(event["request_digest"], "request_digest"); _digest(event["prev_digest"], "prev_digest"); _digest(event["record_digest"], "record_digest")
        if "prepared_record_digest" in event: _digest(event["prepared_record_digest"], "prepared_record_digest")
        candidate = event.get("candidate")
        if not isinstance(candidate, dict) or set(candidate) != {"commit", "source_digest"}:
            raise _fail("LEDGER_CORRUPT", "invalid candidate record")
        _commit(candidate["commit"]); _digest(candidate["source_digest"], "candidate source digest")
        cls._ref(event["candidate_evidence"], "candidate_evidence", "canonical_json")
        cls._ref(event["request_packet"], "request_packet", "canonical_json")
        if event["event"] == "STARTED":
            _timestamp(event["started_at"], "started_at")
            if event["started_at_source"] != "adapter_observed": raise _fail("LEDGER_CORRUPT", "invalid started_at_source")
        else:
            _timestamp(event["settled_at"], "settled_at")
            if event["outcome"] not in _OUTCOMES: raise _fail("LEDGER_CORRUPT", "invalid outcome")
            if not isinstance(event["evidence_refs"], list) or not event["evidence_refs"]:
                raise _fail("LEDGER_CORRUPT", "settlement evidence is required")
            for ref in event["evidence_refs"]: cls._ref(ref, "evidence_ref")
            if "exit_code" in event and (not isinstance(event["exit_code"], int) or isinstance(event["exit_code"], bool)):
                raise _fail("LEDGER_CORRUPT", "invalid exit_code")
            if "cleanup" in event and event["cleanup"] not in {"confirmed", "uncertain", "not_applicable"}:
                raise _fail("LEDGER_CORRUPT", "invalid cleanup")
        if "herdr" in event:
            host = event["herdr"]
            if isinstance(host, dict) and set(host) == {"caller", "target"}:
                _identity(host["caller"], "ledger caller"); _identity(host["target"], "ledger target")
            else:
                # Retained revision-3 records used one overloaded target identity.
                if not allow_legacy_herdr:
                    raise _fail("LEDGER_CORRUPT", "new ledger event uses legacy Herdr identity")
                _identity(host, "legacy ledger target")
        expected = raw_file_digest(canonical_json({key: value for key, value in event.items() if key != "record_digest"}))
        if event["record_digest"] != expected: raise _fail("LEDGER_CORRUPT", "record digest differs")
        return event

    @classmethod
    def _replay_validate(cls, event: object, *, allow_legacy_herdr: bool = True) -> dict[str, object]:
        try:
            return cls._validate_event(event, allow_legacy_herdr=allow_legacy_herdr)
        except ProtocolError as exc:
            raise _fail("LEDGER_CORRUPT", "malformed attempt record", cause=exc.code) from exc
        except (KeyError, TypeError, AttributeError, ValueError, UnicodeError) as exc:
            raise _fail("LEDGER_CORRUPT", "malformed attempt record") from exc

    @classmethod
    def _replay_chain(cls, records: list[dict[str, object]]) -> list[dict[str, object]]:
        previous = "sha256:" + "0" * 64; attempts = {}; allowances = {}; roots = {}
        for row in records:
            if row["prev_digest"] != previous: raise _fail("LEDGER_CORRUPT", "attempt ledger hash chain is out of order")
            attempt = row["attempt_id"]
            if attempt not in attempts:
                if row["event"] != "STARTED": raise _fail("LEDGER_CORRUPT", "attempt does not start with STARTED")
                attempts[attempt] = "STARTED"
                allowance = row.get("allowance_id")
                if allowance in allowances and allowances[allowance] != attempt: raise _fail("ALLOWANCE_REUSED", "allowance is already bound to another attempt")
                allowances[allowance] = attempt
                for root_id, digest in ((row["output_root_id"], row["output_root_digest"]), (row["session_root_id"], row["session_root_digest"])):
                    if root_id in roots and roots[root_id] != attempt: raise _fail("RESOURCE_REUSED", "root identity was reused")
                    roots[root_id] = attempt
            elif attempts[attempt] == "STARTED" and row["event"] == "SETTLED":
                started = next(item for item in records if item["attempt_id"] == attempt and item["event"] == "STARTED")
                for key in ("work_id", "node_id", "observed_generation", "observed_ordinal", "provider_id", "branch", "worktree_ref", "candidate", "candidate_evidence", "request_packet", "request_digest", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest", "prepared_record_digest", "herdr"):
                    if row.get(key) != started.get(key): raise _fail("IDENTITY_MISMATCH", f"settlement {key} differs")
                attempts[attempt] = "SETTLED"
            else: raise _fail("LEDGER_CORRUPT", "invalid per-attempt event order")
            previous = row["record_digest"]
        return records

    def _verify_settlement_evidence(self, event: Mapping[str, object], root: Path | None = None) -> None:
        if root is None:
            try: root = self.path.parent.parent.resolve(strict=True)
            except OSError as exc: raise _fail("LEDGER_CORRUPT", "attempt worktree is unavailable") from exc
        for ref in event["evidence_refs"]:
            try: data = _evidence_ref_bytes(root, ref)
            except ProtocolError as exc:
                if exc.code == "OUTPUT_LIMIT": raise
                raise _fail("LEDGER_CORRUPT", "settlement evidence is unavailable") from exc
            actual = raw_file_digest(data)
            if ref["digest_domain"] == "canonical_json":
                try:
                    def reject_duplicate(pairs):
                        result = {}
                        for key, value in pairs:
                            if key in result: raise ValueError("duplicate JSON key")
                            result[key] = value
                        return result
                    parsed = json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicate, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
                    canonical = canonical_json(parsed)
                    if canonical != data: raise ValueError("noncanonical JSON")
                    actual = raw_file_digest(canonical)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, ProtocolError) as exc: raise _fail("LEDGER_CORRUPT", "settlement canonical evidence is malformed") from exc
            if actual != ref["digest"]: raise _fail("LEDGER_CORRUPT", "settlement evidence digest differs")

    @property
    def state_path(self) -> Path:
        return self.path.with_name(self.path.name + ".state.json")

    @property
    def intent_path(self) -> Path:
        return self.path.with_name(self.path.name + ".append-intent.json")

    @contextmanager
    def _authority_lock(self, *, create_parent: bool) -> Iterator[int]:
        if create_parent:
            try: self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc: raise _fail("LOCK_UNAVAILABLE", "attempt ledger cannot create its parent") from exc
        try: parent = self.path.parent.resolve(strict=True)
        except OSError as exc: raise _fail("LEDGER_CORRUPT", "attempt ledger parent is unavailable") from exc
        if not parent.is_dir(): raise _fail("LEDGER_CORRUPT", "attempt ledger parent is not a directory")
        basename = self.path.name
        if not basename or basename in {".", ".."} or "/" in basename or "\\" in basename: raise _fail("LEDGER_CORRUPT", "attempt ledger basename is unsafe")
        dirfd = lockfd = -1
        try:
            dirfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            lockfd = os.open(basename + ".authority.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
            info = os.fstat(lockfd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise _fail("LOCK_UNAVAILABLE", "attempt authority lock is unsafe")
            os.fchmod(lockfd, 0o600); fcntl.flock(lockfd, fcntl.LOCK_EX)
            yield dirfd
        except ProtocolError: raise
        except OSError as exc: raise _fail("LOCK_UNAVAILABLE", "attempt authority lock is unavailable") from exc
        finally:
            if lockfd >= 0:
                try: fcntl.flock(lockfd, fcntl.LOCK_UN)
                except OSError: pass
                try: os.close(lockfd)
                except OSError: pass
            if dirfd >= 0:
                try: os.close(dirfd)
                except OSError: pass

    @staticmethod
    def _attempt_sidecar(dirfd: int, name: str, kind: str) -> dict[str, object] | None:
        try: fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dirfd)
        except FileNotFoundError: return None
        except OSError as exc: raise _fail("LEDGER_CORRUPT", f"{kind} is unsafe") from exc
        try:
            info = os.fstat(fd); data = os.read(fd, 1_048_577)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or len(data) > 1_048_576: raise _fail("LEDGER_CORRUPT", f"{kind} is unsafe")
            value = _strict_json(data)
            if canonical_json(value) != data: raise ValueError("noncanonical sidecar")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc: raise _fail("LEDGER_CORRUPT", f"{kind} is malformed") from exc
        finally: os.close(fd)
        if not isinstance(value, dict) or value.get("kind") != kind or value.get("schema_version") != 1 or not isinstance(value.get("record_digest"), str):
            raise _fail("LEDGER_CORRUPT", f"{kind} identity differs")
        body = {key: value[key] for key in value if key != "record_digest"}
        if value["record_digest"] != raw_file_digest(canonical_json(body)): raise _fail("LEDGER_CORRUPT", f"{kind} digest differs")
        return value

    @staticmethod
    def _write_attempt_sidecar(dirfd: int, name: str, body: Mapping[str, object]) -> None:
        value = dict(body); value["record_digest"] = raw_file_digest(canonical_json(value)); data = canonical_json(value); temporary = "." + name + ".staging"
        try:
            try: os.unlink(temporary, dir_fd=dirfd)
            except FileNotFoundError: pass
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
            with os.fdopen(fd, "wb") as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=dirfd, dst_dir_fd=dirfd); os.fsync(dirfd)
        except OSError as exc: raise _fail("LEDGER_CORRUPT", "attempt authority sidecar cannot be persisted") from exc

    @staticmethod
    def _remove_attempt_sidecar(dirfd: int, name: str) -> None:
        try: os.unlink(name, dir_fd=dirfd); os.fsync(dirfd)
        except FileNotFoundError: return
        except OSError as exc: raise _fail("LEDGER_CORRUPT", "attempt append intent cannot be cleared") from exc

    def _read_attempt_journal(self, dirfd: int) -> tuple[os.stat_result, list[dict[str, object]]] | None:
        try: fd = os.open(self.path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dirfd)
        except FileNotFoundError: return None
        except OSError as exc: raise _fail("LEDGER_CORRUPT", "attempt ledger cannot be opened") from exc
        try:
            info = os.fstat(fd); raw = os.read(fd, 16 * 1024 * 1024 + 1)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise _fail("LEDGER_CORRUPT", "attempt ledger is not a private regular file")
        except OSError as exc: raise _fail("LEDGER_CORRUPT", "attempt ledger cannot be read") from exc
        finally: os.close(fd)
        if len(raw) > 16 * 1024 * 1024 or (raw and not raw.endswith(b"\n")): raise _fail("LEDGER_CORRUPT", "attempt ledger is oversized or partial")
        records = []
        for line in raw.splitlines():
            try: records.append(self._replay_validate(_strict_json(line)))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, ProtocolError) as exc: raise _fail("LEDGER_CORRUPT", "malformed ledger JSON") from exc
        return info, self._replay_chain(records)

    @staticmethod
    def _state_body(info: os.stat_result, records: Sequence[Mapping[str, object]]) -> dict[str, object]:
        return {"schema_version": 1, "kind": "attempt_ledger_state", "journal_device": info.st_dev, "journal_inode": info.st_ino,
                "records": len(records), "head_digest": records[-1]["record_digest"] if records else "sha256:" + "0" * 64}

    @classmethod
    def _validate_attempt_state(cls, value: Mapping[str, object]) -> None:
        expected = {"schema_version", "kind", "journal_device", "journal_inode", "records", "head_digest", "record_digest"}
        if set(value) != expected or not all(isinstance(value[key], int) and not isinstance(value[key], bool) and value[key] >= 0 for key in ("journal_device", "journal_inode", "records")):
            raise _fail("LEDGER_CORRUPT", "attempt ledger state is not closed")
        try: _digest(value["head_digest"], "attempt state head"); _digest(value["record_digest"], "attempt state digest")
        except ProtocolError as exc: raise _fail("LEDGER_CORRUPT", "attempt ledger state digest is invalid") from exc

    @classmethod
    def _validate_append_intent(cls, value: Mapping[str, object]) -> dict[str, object]:
        expected = {"schema_version", "kind", "journal_exists", "journal_device", "journal_inode", "prior_records", "prior_head_digest", "event", "record_digest"}
        if (set(value) != expected or not isinstance(value["journal_exists"], bool)
                or not all(isinstance(value[key], int) and not isinstance(value[key], bool) and value[key] >= 0 for key in ("journal_device", "journal_inode", "prior_records"))):
            raise _fail("LEDGER_CORRUPT", "attempt append intent is not closed")
        try: _digest(value["prior_head_digest"], "attempt intent head"); _digest(value["record_digest"], "attempt intent digest")
        except ProtocolError as exc: raise _fail("LEDGER_CORRUPT", "attempt append intent digest is invalid") from exc
        return cls._replay_validate(value["event"], allow_legacy_herdr=False)

    def _append_journal_event(self, dirfd: int, event: Mapping[str, object], *, create: bool) -> os.stat_result:
        flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | (os.O_CREAT | os.O_EXCL if create else 0)
        try: fd = os.open(self.path.name, flags, 0o600, dir_fd=dirfd)
        except OSError as exc: raise _fail("LOCK_UNAVAILABLE", "attempt ledger cannot be opened for append") from exc
        original = os.fstat(fd).st_size
        try:
            data = canonical_json(event) + b"\n"; written = 0
            while written < len(data):
                count = os.write(fd, data[written:])
                if count <= 0: raise OSError("short attempt ledger write")
                written += count
            os.fsync(fd); return os.fstat(fd)
        except OSError as exc:
            try: os.ftruncate(fd, original); os.fsync(fd)
            except OSError as rollback_exc: raise _fail("LEDGER_CORRUPT", "attempt ledger append rollback could not be established") from rollback_exc
            raise _fail("LOCK_UNAVAILABLE", "attempt ledger append could not be durably completed") from exc
        finally: os.close(fd)

    def _recover_attempt_authority(self, dirfd: int) -> list[dict[str, object]]:
        state_name = self.state_path.name; intent_name = self.intent_path.name
        state = self._attempt_sidecar(dirfd, state_name, "attempt_ledger_state")
        intent = self._attempt_sidecar(dirfd, intent_name, "attempt_append_intent")
        if state is not None: self._validate_attempt_state(state)
        intent_event = self._validate_append_intent(intent) if intent is not None else None
        journal = self._read_attempt_journal(dirfd)
        if journal is None:
            if state is not None: raise _fail("LEDGER_CORRUPT", "established attempt ledger is missing")
            if intent is None: return []
            if intent["journal_exists"] or intent["prior_records"] != 0 or intent["prior_head_digest"] != "sha256:" + "0" * 64 or intent_event["event"] != "STARTED":
                raise _fail("LEDGER_CORRUPT", "missing attempt ledger has invalid creation intent")
            info = self._append_journal_event(dirfd, intent_event, create=True); records = self._replay_chain([intent_event])
            self._write_attempt_sidecar(dirfd, state_name, self._state_body(info, records)); self._remove_attempt_sidecar(dirfd, intent_name)
            return records
        info, records = journal; head = records[-1]["record_digest"] if records else "sha256:" + "0" * 64
        if state is None:
            if (intent is None or intent["journal_exists"] or intent["prior_records"] != 0 or intent["prior_head_digest"] != "sha256:" + "0" * 64
                    or len(records) != 1 or records[0] != intent_event):
                raise _fail("LEDGER_CORRUPT", "attempt ledger state is missing")
            self._write_attempt_sidecar(dirfd, state_name, self._state_body(info, records)); self._remove_attempt_sidecar(dirfd, intent_name)
            return records
        if (state["journal_device"], state["journal_inode"]) != (info.st_dev, info.st_ino) or state["records"] > len(records):
            raise _fail("LEDGER_CORRUPT", "attempt ledger was replaced or truncated")
        state_head = records[state["records"] - 1]["record_digest"] if state["records"] else "sha256:" + "0" * 64
        if state["head_digest"] != state_head: raise _fail("LEDGER_CORRUPT", "attempt ledger reset differs from state")
        if len(records) == state["records"]:
            if intent is None: return records
            if intent["journal_exists"] and (intent["journal_device"], intent["journal_inode"], intent["prior_records"], intent["prior_head_digest"]) == (info.st_dev, info.st_ino, len(records), head):
                recovered = self._replay_chain(records + [intent_event]); info = self._append_journal_event(dirfd, intent_event, create=False)
                self._write_attempt_sidecar(dirfd, state_name, self._state_body(info, recovered)); self._remove_attempt_sidecar(dirfd, intent_name)
                return recovered
            if (intent["prior_records"] + 1 == len(records) and records[-1] == intent_event
                    and ((intent["journal_exists"] and (intent["journal_device"], intent["journal_inode"]) == (info.st_dev, info.st_ino))
                         or (not intent["journal_exists"] and len(records) == 1))
                    and intent["prior_head_digest"] == (records[-2]["record_digest"] if len(records) > 1 else "sha256:" + "0" * 64)):
                self._remove_attempt_sidecar(dirfd, intent_name); return records
            raise _fail("LEDGER_CORRUPT", "attempt append intent does not match established state")
        if (len(records) == state["records"] + 1 and intent is not None and intent["journal_exists"]
                and (intent["journal_device"], intent["journal_inode"], intent["prior_records"], intent["prior_head_digest"]) == (info.st_dev, info.st_ino, state["records"], state["head_digest"])
                and records[-1] == intent_event):
            self._write_attempt_sidecar(dirfd, state_name, self._state_body(info, records)); self._remove_attempt_sidecar(dirfd, intent_name); return records
        raise _fail("LEDGER_CORRUPT", "attempt ledger history exceeds its authenticated state")

    def records(self) -> list[dict[str, object]]:
        """Read or narrowly recover one independently anchored durable chronology."""
        if not self.path.parent.exists(): return []
        with self._authority_lock(create_parent=False) as dirfd:
            return self._recover_attempt_authority(dirfd)

    def assert_start_available(self, *, attempt_id: str, allowance_id: str,
                               output_root_id: str, session_root_id: str) -> None:
        records = self.records()
        if any(row["attempt_id"] == attempt_id for row in records): raise _fail("DUPLICATE_ATTEMPT", "duplicate attempt identity")
        for row in records:
            if row["event"] != "STARTED": continue
            if row.get("allowance_id") == allowance_id: raise _fail("ALLOWANCE_REUSED", "allowance is already bound to another attempt")
            if output_root_id in {row["output_root_id"], row["session_root_id"]} or session_root_id in {row["output_root_id"], row["session_root_id"]}: raise _fail("RESOURCE_REUSED", "root identity was previously used")

    def append(self, event: Mapping[str, object]) -> dict[str, object]:
        import copy
        event = copy.deepcopy(dict(event))
        if not isinstance(event, dict) or not isinstance(event.get("schema_version"), int) or isinstance(event.get("schema_version"), bool) or event.get("schema_version") != 1 or event.get("event") not in {"STARTED", "SETTLED"}: raise _fail("LEDGER_CORRUPT", "invalid attempt event")
        event.pop("prev_digest", None); event.pop("record_digest", None)
        with self._authority_lock(create_parent=True) as dirfd:
            records = self._recover_attempt_authority(dirfd); previous = records[-1]["record_digest"] if records else "sha256:" + "0" * 64
            event["prev_digest"] = previous; event["record_digest"] = raw_file_digest(canonical_json(event)); event = self._replay_validate(event, allow_legacy_herdr=False)
            same = [row for row in records if row["attempt_id"] == event["attempt_id"]]
            if event["event"] == "STARTED" and any(row["event"] == "STARTED" and row["allowance_id"] == event["allowance_id"] and row["attempt_id"] != event["attempt_id"] for row in records): raise _fail("ALLOWANCE_REUSED", "allowance is already bound to another attempt")
            if event["event"] == "STARTED" and same: raise _fail("DUPLICATE_ATTEMPT", "duplicate STARTED")
            if event["event"] == "SETTLED" and (len(same) != 1 or same[0]["event"] != "STARTED"): raise _fail("INVALID_ORDER", "SETTLED requires one prior STARTED")
            if event["event"] == "SETTLED":
                started = same[0]
                for key in ("work_id", "node_id", "observed_generation", "observed_ordinal", "provider_id", "branch", "candidate", "candidate_evidence", "request_packet", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest", "request_digest", "prepared_record_digest"):
                    if event.get(key) != started.get(key): raise _fail("IDENTITY_MISMATCH", f"settlement {key} differs")
                if event.get("herdr") != started.get("herdr"): raise _fail("IDENTITY_MISMATCH", "settlement Herdr identity differs")
            journal = self._read_attempt_journal(dirfd); info = journal[0] if journal is not None else None
            intent = {"schema_version": 1, "kind": "attempt_append_intent", "journal_exists": info is not None,
                      "journal_device": info.st_dev if info is not None else 0, "journal_inode": info.st_ino if info is not None else 0,
                      "prior_records": len(records), "prior_head_digest": previous, "event": event}
            self._write_attempt_sidecar(dirfd, self.intent_path.name, intent)
            try: info = self._append_journal_event(dirfd, event, create=journal is None)
            except ProtocolError as exc:
                if exc.code == "LOCK_UNAVAILABLE": self._remove_attempt_sidecar(dirfd, self.intent_path.name)
                raise
            try:
                self._write_attempt_sidecar(dirfd, self.state_path.name, self._state_body(info, records + [event]))
                self._remove_attempt_sidecar(dirfd, self.intent_path.name)
            except ProtocolError as exc:
                raise _fail("LEDGER_CORRUPT", "attempt append committed but authority state requires recovery") from exc
            return event


@dataclass(frozen=True)
class ProviderRequest:
    value: Mapping[str, object]

    def __post_init__(self) -> None:
        required = {"schema_version", "request_id", "attempt_id", "role", "host", "work", "worker_id", "branch", "candidate",
                    "worktree", "cwd", "output_root", "output_root_id", "output_root_digest", "session_root", "session_root_id",
                    "session_root_digest", "fresh_context", "neutral_input_digest", "environment_policy_digest", "tool_policy_digest",
                    "required_capabilities", "runtime", "limits", "allowance_id"}
        if set(self.value) != required: raise _fail("INVALID_REQUEST", "provider request is not closed")
        if not isinstance(self.value["schema_version"], int) or isinstance(self.value["schema_version"], bool) or self.value["schema_version"] != 1 or self.value["fresh_context"] is not True: raise _fail("INVALID_REQUEST", "invalid request version/context")
        _id(self.value["request_id"], "request_id"); _id(self.value["attempt_id"], "attempt_id"); _id(self.value["worker_id"], "worker_id"); _branch(self.value["branch"], "branch"); _id(self.value["allowance_id"], "allowance_id")
        if self.value["role"] not in {"technical", "contract", "worker"}: raise _fail("INVALID_REQUEST", "invalid role")
        if self.value["host"] != _execution_host(self.value["host"]): raise _fail("IDENTITY_MISMATCH", "Herdr execution identity differs")
        work = _closed(self.value["work"], {"work_id", "node_id", "generation", "ordinal"}, "work")
        _id(work["work_id"], "work_id"); _id(work["node_id"], "node_id")
        if not isinstance(work["generation"], int) or isinstance(work["generation"], bool) or work["generation"] < 1 or not isinstance(work["ordinal"], int) or isinstance(work["ordinal"], bool) or work["ordinal"] < 0: raise _fail("STALE_GENERATION", "invalid generation/ordinal")
        candidate = _closed(self.value["candidate"], {"repository", "branch", "commit", "source_digest"}, "candidate")
        if not isinstance(candidate["repository"], str) or not candidate["repository"]: raise _fail("INVALID_REQUEST", "invalid candidate repository")
        _branch(candidate["branch"], "candidate branch"); _commit(candidate["commit"]); _digest(candidate["source_digest"], "candidate source digest")
        cwd = _canonical_dir(self.value["cwd"], "cwd"); worktree = _canonical_dir(self.value["worktree"], "worktree")
        if cwd != worktree: raise _fail("INVALID_REQUEST", "cwd and worktree must be equal")
        output = _canonical_dir(self.value["output_root"], "output_root"); session = _canonical_dir(self.value["session_root"], "session_root")
        if output == session or output in session.parents or session in output.parents:
            raise _fail("RESOURCE_REUSED", "roots must be disjoint and not nested")
        for key in ("output_root_digest", "session_root_digest", "neutral_input_digest", "environment_policy_digest", "tool_policy_digest"): _digest(self.value[key], key)
        caps = self.value["required_capabilities"]
        if not isinstance(caps, list) or any(cap not in _CAPABILITIES for cap in caps): raise _fail("UNSUPPORTED_CAPABILITY", "unsupported capability")
        runtime = _closed(self.value["runtime"], {"provider", "model", "thinking"}, "runtime")
        if runtime["provider"] != "openai-codex" or runtime["model"] != _MODEL or runtime["thinking"] != "medium": raise _fail("UNSUPPORTED_CAPABILITY", "exact Pi runtime required")
        limits = _closed(self.value["limits"], {"deadline_ms", "max_output_bytes"}, "limits")
        if not isinstance(limits["deadline_ms"], int) or isinstance(limits["deadline_ms"], bool) or limits["deadline_ms"] <= 0 or limits["deadline_ms"] > 600_000 or not isinstance(limits["max_output_bytes"], int) or isinstance(limits["max_output_bytes"], bool) or limits["max_output_bytes"] <= 0 or limits["max_output_bytes"] > 1_048_576: raise _fail("INVALID_REQUEST", "limits exceed contract bounds")

    @property
    def digest(self) -> str:
        return raw_file_digest(canonical_json(dict(self.value)))


class HerdrProvider:
    provider_id = "herdr"

    def __init__(self, *, runner: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
                 herdr: str = "herdr", pi: str = "pi", ledger: AttemptLedger | None = None,
                 private_root: Path | None = None, core_request: CoreRequest | None = None) -> None:
        self._runner = runner
        self.herdr, self.pi = herdr, pi
        self._resolved = runner is not None
        self.ledger = ledger
        self.private_root = private_root
        self.core_request = core_request
        self._settled: set[str] = set()  # cache only; durable ledger remains authoritative
        self._attempt_paths: dict[str, Path] = {}
        self._attempt_sessions: dict[str, Path] = {}
        self._attempt_session_paths: dict[str, Path] = {}
        self._attempt_roots: dict[str, tuple[Path, Path]] = {}
        self._private_handles: dict[str, dict[str, object]] = {}

    def _env(self) -> dict[str, str]:
        allowed = {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "HERDR_CONFIG_PATH"}
        env = {key: value for key, value in os.environ.items() if key in allowed}
        if "HERDR_CONFIG_PATH" in env:
            path = Path(env["HERDR_CONFIG_PATH"])
            if not path.is_absolute() or not path.is_file() or path.is_symlink(): raise _fail("INVALID_REQUEST", "invalid HERDR_CONFIG_PATH")
        env.update({key: os.environ[key] for key in ("HERDR_ENV", "HERDR_WORKSPACE_ID", "HERDR_TAB_ID", "HERDR_PANE_ID") if key in os.environ})
        return env

    @staticmethod
    def _resolve_executable(command: str) -> str:
        found = shutil.which(command)
        if not found:
            raise _fail("PROVIDER_UNAVAILABLE", "required Herdr/Pi executable is unavailable", executable=command)
        candidate = Path(found)
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        try:
            cursor = Path(candidate.anchor)
            for part in candidate.parts[1:]:
                cursor = cursor / part
                if stat.S_ISLNK(os.lstat(cursor).st_mode):
                    raise _fail("PROVIDER_UNAVAILABLE", "symlinked Herdr/Pi executable path is unsupported", executable=command)
            resolved = candidate.resolve(strict=True)
        except ProtocolError:
            raise
        except OSError as exc:
            raise _fail("PROVIDER_UNAVAILABLE", "required Herdr/Pi executable is unavailable", executable=command) from exc
        if not resolved.is_file() or resolved.is_symlink():
            raise _fail("PROVIDER_UNAVAILABLE", "Herdr/Pi executable is not a canonical regular file", executable=command)
        return str(resolved)

    @staticmethod
    def _terminate_group(process: subprocess.Popen[bytes], grace: float = 0.5) -> None:
        try: os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        except PermissionError as exc: raise _fail("CLEANUP_UNKNOWN", "Herdr process group cannot be signalled") from exc
        try: process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            except PermissionError as exc: raise _fail("CLEANUP_UNKNOWN", "Herdr process group cannot be killed") from exc
            try: process.wait(timeout=grace)
            except subprocess.TimeoutExpired as exc:
                raise _fail("CLEANUP_UNKNOWN", "Herdr process group could not be reaped") from exc
        for _ in range(20):
            try: os.killpg(process.pid, 0)
            except ProcessLookupError: return
            except PermissionError as exc: raise _fail("CLEANUP_UNKNOWN", "Herdr process-group cleanup cannot be observed") from exc
            time.sleep(0.01)
        raise _fail("CLEANUP_UNKNOWN", "Herdr process-group cleanup could not be verified")

    def _exec(self, argv: Sequence[str], *, timeout_ms: int, cwd: Path | None = None,
              max_output: int = 1_048_576, deadline_at: float | None = None) -> bytes:
        if not argv or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv): raise _fail("INVALID_REQUEST", "invalid argv")
        if timeout_ms <= 0 or max_output <= 0: raise _fail("INVALID_REQUEST", "bounds must be positive")
        timeout_ms = self._remaining_ms(deadline_at, timeout_ms)
        command_argv = list(argv)
        if not self._resolved:
            old_herdr, old_pi = self.herdr, self.pi
            self.herdr = self._resolve_executable(self.herdr)
            self.pi = self._resolve_executable(self.pi)
            if command_argv[0] == old_herdr: command_argv[0] = self.herdr
            if command_argv[0] == old_pi: command_argv[0] = self.pi
            self._resolved = True
        if self._runner is not None:
            try:
                result = self._runner(command_argv, cwd=str(cwd) if cwd else None, env=self._env(), stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, timeout=timeout_ms / 1000)
            except subprocess.TimeoutExpired as exc: raise _fail("TIMEOUT", "bounded Herdr command timed out") from exc
            except OSError as exc: raise _fail("PROVIDER_UNAVAILABLE", "Herdr executable unavailable") from exc
            stdout, stderr = result.stdout or b"", result.stderr or b""
            if len(stdout) > max_output or len(stderr) > 262_144: raise _fail("OUTPUT_LIMIT", "Herdr output exceeded bound")
            if result.returncode: raise _fail("LAUNCH_FAILED", "Herdr command failed", returncode=result.returncode, stderr=stderr.decode("utf-8", "replace")[:2000])
            return stdout
        try:
            process = subprocess.Popen(command_argv, cwd=str(cwd) if cwd else None, env=self._env(), stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, start_new_session=True)
        except OSError as exc:
            raise _fail("PROVIDER_UNAVAILABLE", "Herdr executable unavailable") from exc
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        limits = {"stdout": max_output, "stderr": 262_144}
        reason: str | None = None
        started = time.monotonic()
        try:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map() or process.poll() is None:
                remaining = timeout_ms / 1000 - (time.monotonic() - started)
                if remaining <= 0:
                    reason = "TIMEOUT"; break
                for key, _ in selector.select(min(remaining, 0.05)):
                    chunk = os.read(key.fileobj.fileno(), 65_536)
                    if not chunk:
                        selector.unregister(key.fileobj); key.fileobj.close(); continue
                    name = key.data
                    room = limits[name] - len(buffers[name])
                    if len(chunk) > room:
                        if room > 0: buffers[name].extend(chunk[:room])
                        reason = "OUTPUT_LIMIT"; break
                    buffers[name].extend(chunk)
                if reason: break
            if reason is not None or process.poll() is None:
                self._terminate_group(process)
            else:
                process.wait()
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    pass
                except PermissionError as exc:
                    raise _fail("CLEANUP_UNKNOWN", "Herdr process-group state cannot be observed") from exc
                else:
                    self._terminate_group(process)
                    reason = "ORPHANED_DESCENDANT"
        except (KeyboardInterrupt, InterruptedError) as exc:
            self._terminate_group(process)
            raise _fail("INTERRUPTED", "Herdr command was interrupted") from exc
        finally:
            selector.close()
            for stream in (process.stdout, process.stderr):
                try: stream.close()
                except OSError: pass
        if reason == "TIMEOUT": raise _fail("TIMEOUT", "bounded Herdr command timed out")
        if reason == "OUTPUT_LIMIT": raise _fail("OUTPUT_LIMIT", "Herdr output exceeded bound")
        if reason == "ORPHANED_DESCENDANT": raise _fail("CLEANUP_UNKNOWN", "Herdr command left a descendant process group")
        if process.returncode is None:
            raise _fail("CLEANUP_UNKNOWN", "Herdr command termination is unknown")
        if process.returncode < 0:
            raise _fail("LAUNCH_FAILED", "Herdr command was interrupted", signal=-process.returncode)
        if process.returncode:
            raise _fail("LAUNCH_FAILED", "Herdr command failed", returncode=process.returncode,
                        stderr=bytes(buffers["stderr"]).decode("utf-8", "replace")[:2000])
        return bytes(buffers["stdout"])

    def _adapter_bounds_active(self, kind: str) -> bool:
        implementation = getattr(self._exec, "__func__", None) is HerdrProvider._exec
        if kind == "time": return implementation and _CLI_TIMEOUT_MS > 0
        if kind == "output": return implementation and _CLI_MAX_OUTPUT > 0 and 1_048_576 > 0
        return False

    def _post_launch_identity_verification_active(self) -> bool:
        return getattr(self._handle, "__func__", None) is HerdrProvider._handle and getattr(self._inspect_handle, "__func__", None) is HerdrProvider._inspect_handle

    def _model(self, deadline_at: float | None = None) -> str:
        try: output = self._exec([self.pi, "--list-models", "gpt-5.6-luna"], timeout_ms=10_000, max_output=262_144, deadline_at=deadline_at).decode("utf-8", "strict")
        except UnicodeDecodeError as exc: raise _fail("UNSUPPORTED_CAPABILITY", "Pi model table is not UTF-8") from exc
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if len(lines) < 2: raise _fail("UNSUPPORTED_CAPABILITY", "Pi model table is malformed")
        header = lines[0].split()
        if len(header) < 2 or [token.lower() for token in header[:2]] != ["provider", "model"]:
            raise _fail("UNSUPPORTED_CAPABILITY", "Pi model table header is malformed")
        rows = []
        for line in lines[1:]:
            tokens = line.split()
            if not tokens or all(set(token) <= {"-", "="} for token in tokens): continue
            if len(tokens) < 2 or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", token) for token in tokens[:2]):
                raise _fail("UNSUPPORTED_CAPABILITY", "Pi model table row is malformed")
            rows.append((tokens[0], tokens[1]))
        matches = [(provider, model) for provider, model in rows if provider == "openai-codex" and model == "gpt-5.6-luna"]
        if len(matches) != 1 or len(rows) != len(set(rows)):
            raise _fail("UNSUPPORTED_CAPABILITY", "Pi model table is ambiguous")
        return output

    @staticmethod
    def _remaining_ms(deadline_at: float | None, maximum: int) -> int:
        if deadline_at is None:
            return maximum
        remaining = int((deadline_at - time.monotonic()) * 1000)
        if remaining <= 0:
            raise _fail("TIMEOUT", "end-to-end launch deadline expired during preflight")
        return min(maximum, remaining)

    def probe(self, request: Mapping[str, object], *, deadline_at: float | None = None) -> dict[str, object]:
        request = _closed(request, {"schema_version", "request_id", "required_capabilities", "runtime"}, "probe request")
        if not isinstance(request["schema_version"], int) or isinstance(request["schema_version"], bool) or request["schema_version"] != 1: raise _fail("INVALID_REQUEST", "invalid probe schema")
        request_id = _id(request["request_id"], "request_id")
        caps = request["required_capabilities"]
        if not isinstance(caps, list): raise _fail("INVALID_REQUEST", "required_capabilities must be an array")
        runtime = _closed(request["runtime"], {"provider", "model", "thinking"}, "probe runtime")
        if runtime["provider"] != "openai-codex" or runtime["model"] != _MODEL or runtime["thinking"] != "medium": raise _fail("UNSUPPORTED_CAPABILITY", "exact Pi runtime required")
        _host()
        if any(cap not in _CAPABILITIES for cap in caps): raise _fail("UNSUPPORTED_CAPABILITY", "unknown capability")
        herdr_help = self._exec([self.herdr, "--help"], timeout_ms=10_000, max_output=262_144, deadline_at=deadline_at).decode("utf-8", "replace")
        agent_help = self._exec([self.herdr, "agent", "--help"], timeout_ms=10_000, max_output=262_144, deadline_at=deadline_at).decode("utf-8", "replace")
        read_help = self._exec([self.herdr, "agent", "read", "--help"], timeout_ms=10_000, max_output=262_144, deadline_at=deadline_at).decode("utf-8", "replace")
        worktree_help = self._exec([self.herdr, "worktree", "--help"], timeout_ms=_CLI_TIMEOUT_MS, max_output=_CLI_MAX_OUTPUT, deadline_at=deadline_at).decode("utf-8", "replace")
        worktree_open_help = self._exec([self.herdr, "worktree", "open", "--help"], timeout_ms=_CLI_TIMEOUT_MS, max_output=_CLI_MAX_OUTPUT, deadline_at=deadline_at).decode("utf-8", "replace")
        pane_help = self._exec([self.herdr, "pane", "--help"], timeout_ms=_CLI_TIMEOUT_MS, max_output=_CLI_MAX_OUTPUT, deadline_at=deadline_at).decode("utf-8", "replace")
        pane_get_help = self._exec([self.herdr, "pane", "get", "--help"], timeout_ms=_CLI_TIMEOUT_MS, max_output=_CLI_MAX_OUTPUT, deadline_at=deadline_at).decode("utf-8", "replace")
        pi_help = self._exec([self.pi, "--help"], timeout_ms=10_000, max_output=262_144, deadline_at=deadline_at).decode("utf-8", "replace")
        self._model(deadline_at)
        capabilities = set()
        if "--no-session" in pi_help and "--session" in pi_help and "--continue" in pi_help and "--resume" in pi_help:
            capabilities.add("fresh_context")
        if "--session-dir" in pi_help and "--session" in pi_help:
            capabilities.add("saved_session")
        if "--path" in worktree_open_help and pane_get_help.strip() and self._post_launch_identity_verification_active():
            capabilities.add("working_directory")
        if self._adapter_bounds_active("time"):
            capabilities.add("bounded_time")
        if "send-keys" in agent_help:
            capabilities.add("interrupt")
        if "--source" in read_help and "--lines" in read_help:
            capabilities.add("stream_capture")
        if all(flag in pi_help for flag in ("--no-context-files", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-approve")) and self._env():
            capabilities.add("environment_policy")
        if self._adapter_bounds_active("output"):
            capabilities.add("bounded_output")
        capabilities.add("runtime_identity")
        if any(cap not in capabilities for cap in caps): raise _fail("UNSUPPORTED_CAPABILITY", "required capability was not directly evidenced")
        report = {"provider_id": self.provider_id, "runtime_identity": _MODEL + ":medium", "capabilities": [cap for cap in _CAPABILITY_ORDER if cap in capabilities]}
        report["report_digest"] = raw_file_digest(canonical_json(report))
        return {"schema_version": 1, "ok": True, "request_id": request_id, "result": report}

    def _parse_worktree_response(self, response: Mapping[str, object], expected_path: Path | None) -> dict[str, object]:
        result = _closed(response["result"], {"root_pane", "tab", "type", "workspace", "worktree"}, "worktree result")
        if result["type"] not in {"worktree_created", "worktree_opened"}: raise _fail("ARTIFACT_UNAVAILABLE", "unexpected worktree result type")
        root_pane = _closed(result["root_pane"], {"agent_status", "cwd", "focused", "foreground_cwd", "pane_id", "revision", "scroll", "tab_id", "terminal_id", "workspace_id"}, "root pane")
        _closed(root_pane["scroll"], {"max_offset_from_bottom", "offset_from_bottom", "viewport_rows"}, "pane scroll")
        tab = _closed(result["tab"], {"agent_status", "focused", "label", "number", "pane_count", "tab_id", "workspace_id"}, "worktree tab")
        workspace = _closed(result["workspace"], {"active_tab_id", "agent_status", "focused", "label", "number", "pane_count", "tab_count", "workspace_id", "worktree"}, "workspace")
        workspace_worktree = _closed(workspace["worktree"], {"checkout_path", "is_linked_worktree", "repo_key", "repo_name", "repo_root"}, "workspace worktree")
        worktree = _closed(result["worktree"], {"branch", "is_bare", "is_detached", "is_linked_worktree", "is_prunable", "label", "open_workspace_id", "path"}, "worktree")
        for name, item in (("root workspace", root_pane["workspace_id"]), ("tab workspace", tab["workspace_id"]), ("workspace", workspace["workspace_id"]), ("worktree workspace", worktree["open_workspace_id"])): _id(item, name)
        for name, item in (("tab_id", root_pane["tab_id"]), ("pane_id", root_pane["pane_id"]), ("active_tab_id", workspace["active_tab_id"]), ("tab tab_id", tab["tab_id"])): _id(item, name)
        if not (root_pane["workspace_id"] == tab["workspace_id"] == workspace["workspace_id"] == worktree["open_workspace_id"]): raise _fail("IDENTITY_MISMATCH", "nested workspace identities conflict")
        if root_pane["tab_id"] != tab["tab_id"] or workspace["active_tab_id"] != tab["tab_id"]: raise _fail("IDENTITY_MISMATCH", "nested tab identities conflict")
        path = _canonical_dir(worktree["path"], "worktree path")
        if _canonical_dir(root_pane["cwd"], "root pane cwd") != path or _canonical_dir(root_pane["foreground_cwd"], "root pane foreground cwd") != path or _canonical_dir(workspace_worktree["checkout_path"], "workspace checkout path") != path:
            raise _fail("IDENTITY_MISMATCH", "nested checkout paths differ")
        if expected_path is not None and path != expected_path: raise _fail("IDENTITY_MISMATCH", "worktree path differs")
        return {"workspace_id": workspace["workspace_id"], "tab_id": root_pane["tab_id"], "pane_id": root_pane["pane_id"], "path": str(path)}

    def worktree_create(self, *, cwd: str, branch: str, base: str, label: str) -> dict[str, object]:
        root = _canonical_dir(cwd, "cwd"); _branch(branch, "branch"); _branch(base, "base"); _id(label, "label")
        response = _envelope(self._exec([self.herdr, "worktree", "create", "--cwd", str(root), "--branch", branch, "--base", base, "--label", label, "--no-focus"], timeout_ms=120_000), "worktree", {"cli:worktree:create"})
        normalized = self._parse_worktree_response(response, None)
        return {**response, "result": normalized}

    def worktree_open(self, *, path: str, deadline_at: float | None = None) -> dict[str, object]:
        root = _canonical_dir(path, "worktree")
        response = _envelope(self._exec([self.herdr, "worktree", "open", "--path", str(root), "--no-focus"], timeout_ms=120_000, deadline_at=deadline_at), "worktree", {"cli:worktree:open"})
        return {**response, "result": self._parse_worktree_response(response, root)}

    def _resolve_existing_worktree(self, path: Path, expected_host: Mapping[str, object] | None = None,
                                   *, deadline_at: float | None = None) -> dict[str, str] | None:
        response = _envelope(self._exec([self.herdr, "worktree", "list", "--cwd", str(path)], timeout_ms=_CLI_TIMEOUT_MS, deadline_at=deadline_at), "worktree list", {"cli:worktree:list"})
        result = _closed(response["result"], {"source", "type", "worktrees"}, "worktree list result")
        _closed(result["source"], {"repo_key", "repo_name", "repo_root", "source_checkout_path", "source_workspace_id"}, "worktree list source")
        if result["type"] != "worktree_list" or not isinstance(result["worktrees"], list): raise _fail("HERDR_IDENTITY_INCOMPLETE", "worktree list is malformed")
        matches = []
        for raw_item in result["worktrees"]:
            if not isinstance(raw_item, dict) or not isinstance(raw_item.get("path"), str): continue
            try: listed_path = _canonical_dir(raw_item["path"], "listed worktree path")
            except ProtocolError: continue
            if listed_path != path: continue
            try: item = _closed(raw_item, {"branch", "is_bare", "is_detached", "is_linked_worktree", "is_prunable", "label", "open_workspace_id", "path"}, "worktree list entry")
            except ProtocolError as exc: raise _fail("HERDR_IDENTITY_INCOMPLETE", "target worktree list entry is malformed") from exc
            if not {"is_bare", "is_detached", "is_linked_worktree", "is_prunable", "label", "path"} <= set(item): raise _fail("HERDR_IDENTITY_INCOMPLETE", "target worktree list entry is incomplete")
            matches.append(item)
        if len(matches) > 1: raise _fail("IDENTITY_MISMATCH", "duplicate worktree paths are listed")
        if not matches or "open_workspace_id" not in matches[0]: return None
        workspace_id = matches[0]["open_workspace_id"]; _id(workspace_id, "workspace_id")
        panes_response = _envelope(self._exec([self.herdr, "pane", "list", "--workspace", workspace_id], timeout_ms=_CLI_TIMEOUT_MS, deadline_at=deadline_at), "pane list", {"cli:pane:list"})
        panes_result = _closed(panes_response["result"], {"panes", "type"}, "pane list result")
        if panes_result["type"] != "pane_list" or not isinstance(panes_result["panes"], list): raise _fail("HERDR_IDENTITY_INCOMPLETE", "pane list is malformed")
        roots = []
        for raw_pane in panes_result["panes"]:
            if not isinstance(raw_pane, dict): continue
            if not all(field in raw_pane for field in ("pane_id", "workspace_id", "tab_id", "cwd")): continue
            pane = raw_pane
            for field in ("workspace_id", "tab_id", "pane_id"): _id(pane[field], field)
            if not isinstance(pane["cwd"], str): raise _fail("HERDR_IDENTITY_INCOMPLETE", "listed pane cwd is malformed")
            if pane["workspace_id"] != workspace_id: continue
            if expected_host is not None and pane["pane_id"] != expected_host["pane_id"]: continue
            if _canonical_dir(pane["cwd"], "listed pane cwd") == path: roots.append(pane)
        if expected_host is not None:
            if len(roots) != 1 or roots[0]["workspace_id"] != expected_host["workspace_id"] or roots[0]["tab_id"] != expected_host["tab_id"]:
                raise _fail("IDENTITY_MISMATCH", "expected root pane identity is not present")
        elif len(roots) != 1: raise _fail("IDENTITY_MISMATCH", "open worktree root pane is ambiguous")
        return {"workspace_id": roots[0]["workspace_id"], "tab_id": roots[0]["tab_id"], "pane_id": roots[0]["pane_id"]}

    def _verify_candidate(self, request: ProviderRequest, *, deadline_at: float | None = None,
                          allow_worktree_open: bool = True) -> None:
        value = request.value
        root = _canonical_dir(value["worktree"], "worktree")
        target = _target(value["host"])
        existing = self._resolve_existing_worktree(root, target, deadline_at=deadline_at)
        if existing is None and not allow_worktree_open:
            raise _fail("IDENTITY_MISMATCH", "verified worktree disappeared before launch")
        opened = ({**existing, "path": str(root)} if existing is not None
                  else self.worktree_open(path=str(root), deadline_at=deadline_at)["result"])
        if any(opened[key] != target[key] for key in ("workspace_id", "tab_id", "pane_id")):
            raise _fail("IDENTITY_MISMATCH", "dedicated worktree workspace differs from request host")
        root = _canonical_dir(value["worktree"], "worktree")
        # Provider-side re-verification confirms the checkout; candidate read-back remains foundation caller authority.
        verify_candidate_identity(root, branch=value["candidate"]["branch"], commit=value["candidate"]["commit"],
                                  source_digest=value["candidate"]["source_digest"], deadline_at=deadline_at)

    def _read_current_work(self, request: ProviderRequest, *, deadline_at: float | None = None) -> WorkflowRead:
        if self.core_request is None:
            raise _fail("STALE_GENERATION", "authority-owned core readback is required before dispatch")
        value = request.value
        root = _canonical_dir(value["worktree"], "worktree")
        if self.core_request.repository_root != root or self.core_request.cwd != root:
            raise _fail("IDENTITY_MISMATCH", "core readback root differs from dispatch worktree")
        remaining = self._remaining_ms(deadline_at, self.core_request.timeout_ms)
        bounded_core = dataclasses.replace(self.core_request, timeout_ms=remaining)
        try:
            read = read_workflow(bounded_core, value["work"]["work_id"], value["work"]["node_id"],
                                 deadline_at=deadline_at)
        except ProtocolError as exc:
            if exc.code == "TIMEOUT":
                raise
            if exc.code in {"SOURCE_CHANGED", "IDENTITY_MISMATCH", "DIGEST_MISMATCH"}:
                raise _fail("CANDIDATE_CHANGED", "core candidate readback changed", cause=exc.code) from exc
            raise _fail("STALE_GENERATION", "current core work readback failed", cause=exc.code) from exc
        if read.generation != value["work"]["generation"] or read.node_attempt != value["work"]["ordinal"]:
            raise _fail("STALE_GENERATION", "current generation or node ordinal differs",
                        actual_generation=read.generation, actual_ordinal=read.node_attempt)
        candidate = value["candidate"]
        if (read.candidate_repository != candidate["repository"] or read.candidate_branch != candidate["branch"]
                or read.candidate_commit != candidate["commit"] or read.candidate_source_digest != candidate["source_digest"]):
            raise _fail("CANDIDATE_CHANGED", "current core candidate differs from dispatch candidate")
        return read

    @staticmethod
    def _root_claim_rows(stream) -> list[dict[str, object]]:
        stream.seek(0); rows = []; previous = "sha256:" + "0" * 64
        for line in stream:
            if not line.endswith("\n"): raise _fail("LEDGER_CORRUPT", "partial root claims record")
            try: row = _strict_json(line.rstrip("\n").encode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc: raise _fail("LEDGER_CORRUPT", "malformed root claims record") from exc
            expected = {"schema_version", "root_id", "root_digest", "path", "attempt_id", "request_digest", "claimed_at", "prev_digest", "record_digest"}
            if not isinstance(row, dict) or set(row) != expected or row["schema_version"] != 1 or row["prev_digest"] != previous or row["record_digest"] != raw_file_digest(canonical_json({key: row[key] for key in row if key != "record_digest"})):
                raise _fail("LEDGER_CORRUPT", "corrupt root claims record")
            try:
                _id(row["root_id"], "root_id"); _id(row["attempt_id"], "attempt_id"); _digest(row["root_digest"], "root_digest"); _digest(row["request_digest"], "request_digest"); _digest(row["prev_digest"], "prev_digest"); _digest(row["record_digest"], "record_digest")
            except ProtocolError as exc: raise _fail("LEDGER_CORRUPT", "invalid root claim identity") from exc
            if not isinstance(row["path"], str) or not os.path.isabs(row["path"]) or os.path.normpath(row["path"]) != row["path"]:
                raise _fail("LEDGER_CORRUPT", "invalid root claim path")
            rows.append(row); previous = row["record_digest"]
        return rows

    def _started_claim_records(self) -> dict[str, dict[str, object]]:
        if self.ledger is None: raise _fail("INVALID_REQUEST", "attempt ledger is required for root claims")
        return {row["attempt_id"]: row for row in self.ledger.records() if row["event"] == "STARTED"}

    @staticmethod
    def _validate_root_claim_coverage(rows: Sequence[Mapping[str, object]], started: Mapping[str, Mapping[str, object]], *, incomplete_attempt: str | None = None) -> None:
        observed: dict[str, set[str]] = {attempt_id: set() for attempt_id in started}
        committed_paths: dict[str, str] = {}; committed_ids: dict[str, str] = {}
        for row in rows:
            event = started.get(row["attempt_id"])
            if event is None:  # Pre-START orphan chronology is retained but has no authority.
                continue
            expected = {event["output_root_id"]: event["output_root_digest"], event["session_root_id"]: event["session_root_digest"]}
            if row["root_id"] not in expected or row["root_digest"] != expected[row["root_id"]] or row["request_digest"] != event["request_digest"]:
                raise _fail("LEDGER_CORRUPT", "root claim does not match durable STARTED")
            if row["root_id"] in observed[row["attempt_id"]]: raise _fail("LEDGER_CORRUPT", "duplicate durable root claim")
            owner = committed_ids.get(row["root_id"])
            if owner is not None and owner != row["attempt_id"]: raise _fail("LEDGER_CORRUPT", "durable root ID is multiply claimed")
            path_owner = committed_paths.get(row["path"])
            if path_owner is not None: raise _fail("LEDGER_CORRUPT", "durable canonical root path is multiply claimed")
            observed[row["attempt_id"]].add(row["root_id"]); committed_ids[row["root_id"]] = row["attempt_id"]; committed_paths[row["path"]] = row["attempt_id"]
        for attempt_id, event in started.items():
            expected_ids = {event["output_root_id"], event["session_root_id"]}
            if attempt_id == incomplete_attempt:
                if not observed[attempt_id].issubset(expected_ids): raise _fail("LEDGER_CORRUPT", "invalid incomplete root claim set")
            elif observed[attempt_id] != expected_ids:
                raise _fail("LEDGER_CORRUPT", "durable STARTED root claim history is missing")

    def _prepared_authorizes_claims(self, attempt_id: str, request_digest: str, started: Mapping[str, object], *, prepared_bytes: bytes | None = None) -> bool:
        if self.ledger is None or not isinstance(started.get("prepared_record_digest"), str): return False
        records = self.ledger.records()
        matching_starts = [row for row in records if row["event"] == "STARTED" and row["attempt_id"] == attempt_id]
        if len(matching_starts) != 1 or matching_starts[0] != started or any(row["event"] == "SETTLED" and row["attempt_id"] == attempt_id for row in records):
            return False
        path = self._prepared_path(attempt_id)
        try:
            data = prepared_bytes if prepared_bytes is not None else _read_private_bytes(path, _canonical_dir(str(self.private_root), "private_root"))
            if raw_file_digest(data) != started["prepared_record_digest"]: return False
            value = _strict_json(data)
            if canonical_json(value) != data: return False
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError, ProtocolError): return False
        expected = {"schema_version", "kind", "attempt_id", "request_digest", "worktree", "roots", "evidence"}
        if not isinstance(value, dict) or set(value) != expected or value["schema_version"] != 1 or value["kind"] != "dispatch_prepared" or value["attempt_id"] != attempt_id or value["request_digest"] != request_digest:
            return False
        roots = value["roots"]
        if not isinstance(roots, list) or len(roots) != 2: return False
        expected_roots = {started["output_root_id"]: started["output_root_digest"], started["session_root_id"]: started["session_root_digest"]}
        actual: dict[str, str] = {}
        for item in roots:
            if not isinstance(item, dict) or set(item) != {"path", "root_id", "root_digest"} or not isinstance(item["path"], str) or not os.path.isabs(item["path"]) or os.path.normpath(item["path"]) != item["path"]:
                return False
            if item["root_id"] in actual: return False
            actual[item["root_id"]] = item["root_digest"]
        return actual == expected_roots and started["request_digest"] == request_digest

    @staticmethod
    def _root_claim_state(dirfd: int) -> dict[str, object] | None:
        try: fd = os.open("root-claims.state.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dirfd)
        except FileNotFoundError: return None
        except OSError as exc: raise _fail("LEDGER_CORRUPT", "root claim state is unsafe") from exc
        try:
            info = os.fstat(fd); data = os.read(fd, 65_537)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or len(data) > 65_536: raise _fail("LEDGER_CORRUPT", "root claim state is unsafe")
            value = _strict_json(data)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc: raise _fail("LEDGER_CORRUPT", "root claim state is malformed") from exc
        finally: os.close(fd)
        expected = {"schema_version", "kind", "journal_device", "journal_inode", "records", "head_digest", "record_digest"}
        body = {key: value[key] for key in value if key != "record_digest"} if isinstance(value, dict) else {}
        if (not isinstance(value, dict) or set(value) != expected or value["schema_version"] != 1 or value["kind"] != "root_claim_state"
                or not all(isinstance(value[key], int) and not isinstance(value[key], bool) and value[key] >= 0 for key in ("journal_device", "journal_inode", "records"))
                or value["record_digest"] != raw_file_digest(canonical_json(body))):
            raise _fail("LEDGER_CORRUPT", "root claim state identity differs")
        try: _digest(value["head_digest"], "root claim head"); _digest(value["record_digest"], "root claim state digest")
        except ProtocolError as exc: raise _fail("LEDGER_CORRUPT", "root claim state digest is invalid") from exc
        return value

    @staticmethod
    def _write_root_claim_state(dirfd: int, journal: os.stat_result, rows: Sequence[Mapping[str, object]]) -> None:
        body = {"schema_version": 1, "kind": "root_claim_state", "journal_device": journal.st_dev, "journal_inode": journal.st_ino,
                "records": len(rows), "head_digest": rows[-1]["record_digest"] if rows else "sha256:" + "0" * 64}
        body["record_digest"] = raw_file_digest(canonical_json(body)); data = canonical_json(body); temporary = ".root-claims.state.staging"
        try:
            try: os.unlink(temporary, dir_fd=dirfd)
            except FileNotFoundError: pass
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
            with os.fdopen(fd, "wb") as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, "root-claims.state.json", src_dir_fd=dirfd, dst_dir_fd=dirfd); os.fsync(dirfd)
        except OSError as exc: raise _fail("LEDGER_CORRUPT", "root claim state cannot be persisted") from exc

    def _verify_root_claim_history(self, *, allow_prepared_recovery: bool = False) -> None:
        if self.private_root is None: raise _fail("INVALID_REQUEST", "private root unavailable")
        started = self._started_claim_records(); private = _canonical_dir(str(self.private_root), "private_root")
        authorized = [attempt_id for attempt_id, event in started.items()
                      if allow_prepared_recovery and self._prepared_authorizes_claims(attempt_id, event["request_digest"], event)]
        incomplete = authorized[0] if len(authorized) == 1 else None
        dirfd = os.open(private, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            state = self._root_claim_state(dirfd)
            try: fd = os.open("root-claims.jsonl", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dirfd)
            except FileNotFoundError:
                if state is None and incomplete is not None and set(started) == {incomplete}: return
                if state is not None or started: raise _fail("LEDGER_CORRUPT", "durable root claim history is missing")
                return
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise _fail("LEDGER_CORRUPT", "root claims is not a private regular file")
                fcntl.flock(stream.fileno(), fcntl.LOCK_SH); rows = self._root_claim_rows(stream)
            self._validate_root_claim_coverage(rows, started, incomplete_attempt=incomplete)
            if state is None:
                if incomplete is not None and set(started) == {incomplete}: return
                raise _fail("LEDGER_CORRUPT", "root claim state is missing")
            if (state["journal_device"], state["journal_inode"]) != (info.st_dev, info.st_ino) or state["records"] > len(rows):
                raise _fail("LEDGER_CORRUPT", "root claim journal was replaced or truncated")
            state_head = rows[state["records"] - 1]["record_digest"] if state["records"] else "sha256:" + "0" * 64
            if state["head_digest"] != state_head: raise _fail("LEDGER_CORRUPT", "root claim journal reset differs from state")
            if state["records"] != len(rows) and (incomplete is None or any(row["attempt_id"] != incomplete for row in rows[state["records"]:])):
                raise _fail("LEDGER_CORRUPT", "root claim history exceeds authenticated state")
        except OSError as exc: raise _fail("LEDGER_CORRUPT", "root claims cannot be read") from exc
        finally: os.close(dirfd)

    def _claim_root_identity(self, root: Path, root_id: str, root_digest: str, attempt_id: str, request_digest: str) -> None:
        if self.private_root is None: raise _fail("INVALID_REQUEST", "private root unavailable")
        private = _canonical_dir(str(self.private_root), "private_root"); started = self._started_claim_records()
        if attempt_id not in started: raise _fail("INVALID_ORDER", "root claim requires durable STARTED")
        authorized = self._prepared_authorizes_claims(attempt_id, request_digest, started[attempt_id])
        dirfd = os.open(private, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            state = self._root_claim_state(dirfd); created = False
            try: fd = os.open("root-claims.jsonl", os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
            except FileNotFoundError:
                if state is not None or set(started) != {attempt_id} or not authorized:
                    raise _fail("LEDGER_CORRUPT", "durable root claim history is missing")
                fd = os.open("root-claims.jsonl", os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_APPEND | os.O_NOFOLLOW, 0o600, dir_fd=dirfd); created = True
            try:
                file_stat = os.fstat(fd)
                if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1: raise _fail("LEDGER_CORRUPT", "root claims is not a private regular file")
                with os.fdopen(fd, "a+", encoding="utf-8") as stream:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX); rows = self._root_claim_rows(stream)
                    self._validate_root_claim_coverage(rows, started, incomplete_attempt=attempt_id)
                    head = rows[-1]["record_digest"] if rows else "sha256:" + "0" * 64
                    if state is not None:
                        if (state["journal_device"], state["journal_inode"]) != (file_stat.st_dev, file_stat.st_ino) or state["records"] > len(rows):
                            raise _fail("LEDGER_CORRUPT", "root claim journal was replaced or truncated")
                        state_head = rows[state["records"] - 1]["record_digest"] if state["records"] else "sha256:" + "0" * 64
                        if state["head_digest"] != state_head: raise _fail("LEDGER_CORRUPT", "root claim state chain differs")
                        if state["records"] != len(rows) and (not authorized or any(row["attempt_id"] != attempt_id for row in rows[state["records"]:])):
                            raise _fail("LEDGER_CORRUPT", "unauthenticated root claim state lag")
                    elif (rows or started) and not authorized:
                        raise _fail("LEDGER_CORRUPT", "root claim state is missing")
                    if state is None or state["records"] != len(rows): self._write_root_claim_state(dirfd, file_stat, rows)
                    exact = next((row for row in rows if row["attempt_id"] == attempt_id and row["root_id"] == root_id and row["root_digest"] == root_digest and row["path"] == str(root) and row["request_digest"] == request_digest), None)
                    if exact is not None: return
                    if not authorized: raise _fail("LEDGER_CORRUPT", "incomplete root claims lack authenticated prepared state")
                    committed = [row for row in rows if row["attempt_id"] in started]
                    if any(row["root_id"] == root_id or row["path"] == str(root) for row in committed): raise _fail("RESOURCE_REUSED", "root identity or canonical path was previously claimed")
                    row = {"schema_version": 1, "root_id": root_id, "root_digest": root_digest, "path": str(root), "attempt_id": attempt_id, "request_digest": request_digest, "claimed_at": _now(), "prev_digest": head}
                    row["record_digest"] = raw_file_digest(canonical_json(row)); stream.seek(0, 2); stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"); stream.flush(); os.fsync(stream.fileno()); rows.append(row)
                    self._write_root_claim_state(dirfd, file_stat, rows); fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                if created: os.fsync(dirfd)
            except (OSError, UnicodeDecodeError) as exc: raise _fail("LEDGER_CORRUPT", "root claims cannot be locked or written") from exc
        finally: os.close(dirfd)

    def _prepare_launch_material(self, request: ProviderRequest, capability_report_digest: str,
                                 current: WorkflowRead) -> dict[str, object]:
        """Validate empty roots and construct deterministic evidence without durable claims."""
        values = request.value
        for root_key, id_key in (("output_root", "output_root_id"), ("session_root", "session_root_id")):
            root = _canonical_dir(values[root_key], root_key)
            if _marker_present(root): raise _fail("RESOURCE_REUSED", f"{root_key} was previously claimed")
            digest, entries = _manifest(root)
            if digest != values[root_key + "_digest"] or entries:
                raise _fail("RESOURCE_REUSED", f"{root_key} manifest differs or is non-empty")
            _id(values[id_key], id_key)
        packet = {key: values[key] for key in ("schema_version", "request_id", "attempt_id", "role", "work", "worker_id", "branch", "candidate", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest", "fresh_context", "neutral_input_digest", "environment_policy_digest", "tool_policy_digest", "required_capabilities", "runtime", "limits", "allowance_id")}
        _digest(capability_report_digest, "capability_report_digest")
        packet["capability_report_digest"] = capability_report_digest
        packet["request_digest"] = raw_file_digest(canonical_json(packet))
        worktree = _canonical_dir(values["worktree"], "worktree")
        evidence = worktree / ".razr" / "evidence"
        candidate_path = evidence / ("candidate-" + values["attempt_id"] + ".json")
        packet_path = evidence / ("attempt-" + values["attempt_id"] + ".json")
        boundary_path = evidence / ("launch-boundary-" + values["attempt_id"] + ".json")
        candidate_bytes = canonical_json(values["candidate"])
        packet_bytes = canonical_json(packet)
        boundary_bytes = canonical_json({"schema_version": 1, "kind": "launch_boundary", "attempt_id": values["attempt_id"],
                                         "request_digest": packet["request_digest"],
                                         "work": {"work_id": current.work_id, "node_id": current.node_id,
                                                  "generation": current.generation, "ordinal": current.node_attempt},
                                         "candidate": {"repository": current.candidate_repository,
                                                       "branch": current.candidate_branch,
                                                       "commit": current.candidate_commit,
                                                       "source_digest": current.candidate_source_digest},
                                         "core_readback": {"state_digest": current.raw_state_digest,
                                                           "core_identity": current.raw_state_core_identity}})
        return {"candidate": {"ref": str(candidate_path.relative_to(worktree)), "digest_domain": "canonical_json", "digest": raw_file_digest(candidate_bytes)},
                "packet": {"ref": str(packet_path.relative_to(worktree)), "digest_domain": "canonical_json", "digest": raw_file_digest(packet_bytes)},
                "launch_boundary": {"ref": str(boundary_path.relative_to(worktree)), "digest_domain": "raw_file", "digest": raw_file_digest(boundary_bytes)},
                "request_digest": packet["request_digest"], "capability_report_digest": capability_report_digest,
                "files": ((candidate_path, candidate_bytes), (packet_path, packet_bytes), (boundary_path, boundary_bytes))}

    def _cleanup_staged_evidence(self, material: Mapping[str, object]) -> None:
        files = material["files"]; root = files[0][0].parents[2]
        try:
            with _evidence_directory(root, create=False) as dirfd:
                for path, data in files:
                    name = _evidence_name(path.name); temporary = "." + name + ".staging"
                    try:
                        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dirfd)
                        try:
                            info = os.fstat(fd); existing = os.read(fd, len(data) + 1)
                            if stat.S_ISREG(info.st_mode) and info.st_nlink <= 2 and existing == data:
                                os.unlink(name, dir_fd=dirfd)
                        finally: os.close(fd)
                    except FileNotFoundError: pass
                    try:
                        info = os.stat(temporary, dir_fd=dirfd, follow_symlinks=False)
                        if stat.S_ISREG(info.st_mode) and info.st_nlink <= 2: os.unlink(temporary, dir_fd=dirfd)
                    except FileNotFoundError: pass
                os.fsync(dirfd)
        except ProtocolError as exc:
            if exc.code != "ARTIFACT_UNAVAILABLE": raise

    def _stage_attempt_evidence(self, material: Mapping[str, object]) -> None:
        """Atomically stage exact evidence through a no-follow worktree boundary."""
        files = material["files"]; root = files[0][0].parents[2]
        try:
            with _evidence_directory(root, create=True) as dirfd:
                for path, data in files:
                    name = _evidence_name(path.name); temporary = "." + name + ".staging"
                    try:
                        existing = _read_evidence_at(dirfd, name, len(data))
                    except FileNotFoundError:
                        existing = None
                    if existing is not None:
                        if existing != data: raise _fail("RESOURCE_REUSED", "attempt evidence identity conflicts")
                        try:
                            info = os.stat(temporary, dir_fd=dirfd, follow_symlinks=False)
                            if not stat.S_ISREG(info.st_mode) or info.st_nlink > 2: raise _fail("PATH_ESCAPE", "attempt evidence staging path is unsafe")
                            os.unlink(temporary, dir_fd=dirfd)
                        except FileNotFoundError: pass
                        continue
                    try:
                        info = os.stat(temporary, dir_fd=dirfd, follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 2: raise _fail("PATH_ESCAPE", "attempt evidence staging path is unsafe")
                        os.unlink(temporary, dir_fd=dirfd)
                    except FileNotFoundError: pass
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(data); stream.flush(); os.fsync(stream.fileno())
                    try: os.link(temporary, name, src_dir_fd=dirfd, dst_dir_fd=dirfd, follow_symlinks=False)
                    except FileExistsError as exc: raise _fail("RESOURCE_REUSED", "attempt evidence raced") from exc
                    os.unlink(temporary, dir_fd=dirfd); os.fsync(dirfd)
        except ProtocolError:
            self._cleanup_staged_evidence(material)
            raise
        except OSError as exc:
            self._cleanup_staged_evidence(material)
            raise _fail("ARTIFACT_UNAVAILABLE", "attempt evidence could not be staged") from exc

    def _prepared_path(self, attempt_id: str) -> Path:
        if self.private_root is None: raise _fail("INVALID_REQUEST", "private root unavailable")
        return _canonical_dir(str(self.private_root), "private_root") / ("dispatch-prepared-" + _id(attempt_id, "attempt_id") + ".json")

    def _stage_prepared(self, request: ProviderRequest, material: Mapping[str, object]) -> Path:
        values = request.value
        rows = []
        for path, data in material["files"]:
            rows.append({"ref": str(path.relative_to(_canonical_dir(values["worktree"], "worktree"))),
                         "digest": raw_file_digest(data)})
        record = {"schema_version": 1, "kind": "dispatch_prepared", "attempt_id": values["attempt_id"],
                  "request_digest": material["request_digest"], "worktree": values["worktree"],
                  "roots": [{"path": values["output_root"], "root_id": values["output_root_id"], "root_digest": values["output_root_digest"]},
                            {"path": values["session_root"], "root_id": values["session_root_id"], "root_digest": values["session_root_digest"]}],
                  "evidence": rows}
        data = canonical_json(record); path = self._prepared_path(values["attempt_id"])
        if path.exists():
            try:
                if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
                    raise _fail("RESOURCE_REUSED", "prepared dispatch identity conflicts")
            except OSError as exc: raise _fail("ARTIFACT_UNAVAILABLE", "prepared dispatch is unavailable") from exc
            return path
        created = False
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600); created = True
            with os.fdopen(fd, "wb") as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            dirfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(dirfd)
            finally: os.close(dirfd)
            return path
        except OSError as exc:
            if created:
                try:
                    if path.is_file() and not path.is_symlink(): path.unlink()
                except OSError: pass
            raise _fail("ARTIFACT_UNAVAILABLE", "prepared dispatch could not be persisted") from exc

    @staticmethod
    def _remove_prepared(path: Path) -> None:
        try:
            if path.is_file() and not path.is_symlink(): path.unlink()
            dirfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(dirfd)
            finally: os.close(dirfd)
        except OSError as exc: raise _fail("ARTIFACT_UNAVAILABLE", "prepared dispatch could not be cleared") from exc

    def _materialize_claim_values(self, *, attempt_id: str, request_digest: str,
                                  roots: Sequence[Mapping[str, object]]) -> None:
        for index, item in enumerate(roots):
            if not isinstance(item, Mapping) or set(item) != {"path", "root_id", "root_digest"}:
                raise _fail("LEDGER_CORRUPT", "prepared root claim is malformed")
            root = _canonical_dir(item["path"], f"prepared root {index}")
            root_id = _id(item["root_id"], "root_id"); digest = _digest(item["root_digest"], "root_digest")
            marker_present = _marker_present(root, root_id, digest)
            if not marker_present:
                actual, entries = _manifest(root)
                if actual != digest or entries:
                    raise _fail("RESOURCE_REUSED", "prepared root changed before claim materialization")
            self._claim_root_identity(root, root_id, digest, attempt_id, request_digest)
            if not marker_present: _marker(root, root_id, digest, create=True)

    def _materialize_root_claims(self, request: ProviderRequest, request_digest: str) -> None:
        """Materialize private path claims only after STARTED is durable; replay is idempotent."""
        values = request.value
        roots = [{"path": values["output_root"], "root_id": values["output_root_id"], "root_digest": values["output_root_digest"]},
                 {"path": values["session_root"], "root_id": values["session_root_id"], "root_digest": values["session_root_digest"]}]
        self._materialize_claim_values(attempt_id=values["attempt_id"], request_digest=request_digest, roots=roots)

    def _reconcile_prepared(self) -> None:
        """Recover crash windows under the dispatch lock without erasing chronology."""
        if self.private_root is None or self.ledger is None: return
        private = _canonical_dir(str(self.private_root), "private_root")
        paths = sorted(private.glob("dispatch-prepared-*.json"))
        if len(paths) > 10_000: raise _fail("LIMIT_EXCEEDED", "too many prepared dispatch records")
        records = self.ledger.records()
        for path in paths:
            if path.is_symlink() or not path.is_file(): raise _fail("LEDGER_CORRUPT", "prepared dispatch path is unsafe")
            try:
                prepared_bytes = _read_private_bytes(path, private); value = _strict_json(prepared_bytes)
                if canonical_json(value) != prepared_bytes: raise ValueError("noncanonical prepared record")
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError, ProtocolError) as exc:
                raise _fail("LEDGER_CORRUPT", "prepared dispatch record is malformed") from exc
            expected = {"schema_version", "kind", "attempt_id", "request_digest", "worktree", "roots", "evidence"}
            if not isinstance(value, dict) or set(value) != expected or value["schema_version"] != 1 or value["kind"] != "dispatch_prepared":
                raise _fail("LEDGER_CORRUPT", "prepared dispatch record is not closed")
            attempt_id = _id(value["attempt_id"], "attempt_id"); request_digest = _digest(value["request_digest"], "request_digest")
            started = next((row for row in records if row["attempt_id"] == attempt_id and row["event"] == "STARTED"), None)
            settled = any(row["attempt_id"] == attempt_id and row["event"] == "SETTLED" for row in records)
            if started is not None and not settled:
                # Only an unmatched STARTED may use its exact prepared crash record
                # to complete path claims; settled attempts cannot recreate history.
                if not self._prepared_authorizes_claims(attempt_id, request_digest, started, prepared_bytes=prepared_bytes):
                    raise _fail("LEDGER_CORRUPT", "prepared dispatch does not authorize unmatched STARTED recovery")
                self._materialize_claim_values(attempt_id=attempt_id, request_digest=request_digest, roots=value["roots"])
            elif started is None:
                try: worktree = _canonical_dir(value["worktree"], "prepared worktree")
                except ProtocolError:
                    # No STARTED means no durable claim; an unavailable staging
                    # checkout cannot block unrelated future dispatches.
                    self._remove_prepared(path)
                    continue
                if not isinstance(value["evidence"], list) or len(value["evidence"]) != 3:
                    raise _fail("LEDGER_CORRUPT", "prepared evidence list is malformed")
                try:
                    with _evidence_directory(worktree, create=False) as dirfd:
                        for item in value["evidence"]:
                            if not isinstance(item, dict) or set(item) != {"ref", "digest"}: raise _fail("LEDGER_CORRUPT", "prepared evidence is malformed")
                            ref = PurePosixPath(item["ref"]); _digest(item["digest"], "prepared evidence digest")
                            if ref.parts[:2] != (".razr", "evidence") or len(ref.parts) != 3: raise _fail("PATH_ESCAPE", "prepared evidence escapes worktree")
                            name = _evidence_name(ref.name); temporary = "." + name + ".staging"
                            try:
                                if raw_file_digest(_read_evidence_at(dirfd, name)) == item["digest"]: os.unlink(name, dir_fd=dirfd)
                            except FileNotFoundError: pass
                            try:
                                info = os.stat(temporary, dir_fd=dirfd, follow_symlinks=False)
                                if stat.S_ISREG(info.st_mode) and info.st_nlink <= 2: os.unlink(temporary, dir_fd=dirfd)
                            except FileNotFoundError: pass
                        os.fsync(dirfd)
                except ProtocolError as exc:
                    if exc.code != "ARTIFACT_UNAVAILABLE": raise
            self._remove_prepared(path)

    @contextmanager
    def _dispatch_lock(self):
        if self.private_root is None:
            raise _fail("INVALID_REQUEST", "provider-approved private root is required")
        private = _canonical_dir(str(self.private_root), "private_root")
        dirfd = fd = -1
        try:
            dirfd = os.open(private, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            fd = os.open("dispatch.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise _fail("LOCK_UNAVAILABLE", "dispatch lock is unsafe")
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        except ProtocolError:
            raise
        except OSError as exc:
            raise _fail("LOCK_UNAVAILABLE", "dispatch lock is unavailable") from exc
        finally:
            if fd >= 0:
                try: fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError: pass
                try: os.close(fd)
                except OSError: pass
            if dirfd >= 0:
                try: os.close(dirfd)
                except OSError: pass

    def launch(self, request: Mapping[str, object]) -> dict[str, object]:
        operation_started = time.monotonic()
        limits = request.get("limits") if isinstance(request, Mapping) else None
        raw_deadline = limits.get("deadline_ms") if isinstance(limits, Mapping) else None
        provisional = raw_deadline if isinstance(raw_deadline, int) and not isinstance(raw_deadline, bool) and raw_deadline > 0 else 600_000
        deadline_at = operation_started + min(provisional, 600_000) / 1000
        return self._launch(request, operation_started, deadline_at)

    def _launch(self, request: Mapping[str, object], operation_started: float, deadline_at: float) -> dict[str, object]:
        parsed = ProviderRequest(request)
        value = parsed.value
        deadline = value["limits"]["deadline_ms"]
        if abs(deadline_at - (operation_started + deadline / 1000)) > 0.001:
            deadline_at = operation_started + deadline / 1000
        probe_report = self.probe({"schema_version": 1, "request_id": request["request_id"], "required_capabilities": request["required_capabilities"], "runtime": request["runtime"]}, deadline_at=deadline_at)
        if self.ledger is None: raise _fail("INVALID_REQUEST", "append-only attempt ledger is required")
        if self.private_root is None: raise _fail("INVALID_REQUEST", "provider-approved private root is required")
        private_root = _canonical_dir(str(self.private_root), "private_root")
        for key in ("output_root", "session_root"):
            root = _canonical_dir(parsed.value[key], key)
            if root == private_root or not root.is_relative_to(private_root): raise _fail("PATH_ESCAPE", f"{key} is outside provider private root")
        with self._dispatch_lock():
            self._verify_root_claim_history(allow_prepared_recovery=True)
            self._reconcile_prepared()
            self._verify_root_claim_history()
            self.ledger.assert_start_available(attempt_id=value["attempt_id"], allowance_id=value["allowance_id"],
                                               output_root_id=value["output_root_id"], session_root_id=value["session_root_id"])
            # Razr authority is read before worktree_open or any other dispatch mutation.
            current = self._read_current_work(parsed, deadline_at=deadline_at)
            self._verify_candidate(parsed, deadline_at=deadline_at)
            evidence = self._prepare_launch_material(parsed, probe_report["result"]["report_digest"], current)
            prepared = self._stage_prepared(parsed, evidence)
            prepared_record_digest = raw_file_digest(_read_private_bytes(prepared, private_root))
            try:
                self._stage_attempt_evidence(evidence)
            except BaseException:
                self._remove_prepared(prepared)
                raise
            started = {"schema_version": 1, "event": "STARTED", "attempt_id": value["attempt_id"], "work_id": value["work"]["work_id"], "node_id": value["work"]["node_id"], "observed_generation": value["work"]["generation"], "observed_ordinal": value["work"]["ordinal"], "worker_id": value["worker_id"], "branch": value["branch"], "worktree_ref": ".", "allowance_id": value["allowance_id"], "provider_id": self.provider_id, "started_at": _now(), "started_at_source": "adapter_observed", "candidate": {"commit": value["candidate"]["commit"], "source_digest": value["candidate"]["source_digest"]}, "candidate_evidence": evidence["candidate"], "request_packet": evidence["packet"], "output_root_id": value["output_root_id"], "output_root_digest": value["output_root_digest"], "session_root_id": value["session_root_id"], "session_root_digest": value["session_root_digest"], "request_digest": evidence["request_digest"], "prepared_record_digest": prepared_record_digest, "herdr": _ledger_host(value["host"]), "prev_digest": "sha256:" + "0" * 64}
            try:
                started = self.ledger.append(started)
            except ProtocolError as exc:
                if exc.code != "LEDGER_CORRUPT":
                    self._cleanup_staged_evidence(evidence)
                    self._remove_prepared(prepared)
                raise
            self._attempt_paths[value["attempt_id"]] = _canonical_dir(value["worktree"], "worktree")
            self._attempt_sessions[value["attempt_id"]] = _canonical_dir(value["session_root"], "session_root")
            self._attempt_roots[value["attempt_id"]] = (_canonical_dir(value["output_root"], "output_root"), _canonical_dir(value["session_root"], "session_root"))
            try:
                # STARTED is the durable attempt/root-ID claim. Private path claims are a
                # replayable post-START materialization, never pre-START authority.
                self._materialize_root_claims(parsed, evidence["request_digest"])
                self._remove_prepared(prepared)
                final = self._read_current_work(parsed, deadline_at=deadline_at)
                if (final.raw_state_digest != current.raw_state_digest or final.raw_state_core_identity != current.raw_state_core_identity):
                    raise _fail("STALE_GENERATION", "core state changed at final launch fence")
                self._verify_candidate(parsed, deadline_at=deadline_at, allow_worktree_open=False)
                remaining = self._remaining_ms(deadline_at, deadline)
                response = self._exec([self.herdr, "agent", "start", value["worker_id"], "--kind", "pi", "--pane", _target(value["host"])["pane_id"], "--timeout", str(remaining), "--", "--model", _MODEL, "--thinking", "medium", "--session-dir", value["session_root"], "--no-session", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-context-files", "--no-approve"], timeout_ms=remaining, cwd=_canonical_dir(value["cwd"], "cwd"), max_output=value["limits"]["max_output_bytes"], deadline_at=deadline_at)
            except ProtocolError as exc:
                launch_code = exc.code if exc.code in _LAUNCH_EVIDENCE_CODES else "LAUNCH_FAILED"
                data = canonical_json({"error_code": launch_code, "cause": exc.code, "message": str(exc)[:500]}) + b"\n"
                try:
                    failure_ref = {"ref": _write_evidence(_canonical_dir(value["worktree"], "worktree"), "launch-failure-" + value["attempt_id"] + ".json", data),
                                   "digest_domain": "raw_file", "digest": raw_file_digest(data), "kind": "launch_failure", "error_code": launch_code}
                except (OSError, ProtocolError):
                    failure_ref = dict(evidence["launch_boundary"]); failure_ref.update({"kind": "launch_failure", "error_code": "LAUNCH_FAILED"})
                settled = {key: started[key] for key in ("schema_version", "attempt_id", "work_id", "node_id", "observed_generation", "observed_ordinal", "provider_id", "worktree_ref", "branch", "candidate", "candidate_evidence", "request_packet", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest", "request_digest", "prepared_record_digest", "herdr")}
                settled.update({"event": "SETTLED", "settled_at": _now(), "outcome": "UNKNOWN" if exc.code in {"TIMEOUT", "CLEANUP_UNKNOWN"} else "FAILED", "evidence_refs": [failure_ref], "prev_digest": started["record_digest"], "cleanup": "uncertain" if exc.code == "CLEANUP_UNKNOWN" else "not_applicable"})
                self.ledger._verify_settlement_evidence(settled, _canonical_dir(value["worktree"], "worktree"))
                settled["record_digest"] = raw_file_digest(canonical_json(settled)); self.ledger.append(settled)
                raise
        try:
            envelope = _envelope(response, "agent start", {"cli:agent:start"}); result = envelope["result"]
            if not isinstance(result, dict) or not isinstance(result.get("agent"), dict): raise _fail("HERDR_IDENTITY_INCOMPLETE", "agent response lacks nested agent identity")
            agent = result["agent"]
            if "name" in agent:
                if not isinstance(agent["name"], str) or agent["name"] != value["worker_id"]: raise _fail("IDENTITY_MISMATCH", "agent name differs")
            elif "agent" not in agent or not isinstance(agent["agent"], str) or agent["agent"] != value["worker_id"]:
                raise _fail("IDENTITY_MISMATCH", "agent name is missing")
            target = _target(value["host"])
            for key in ("workspace_id", "tab_id", "pane_id"):
                if not isinstance(agent.get(key), str) or agent[key] != target[key]: raise _fail("IDENTITY_MISMATCH", "agent target identity differs")
            agent_id = agent["name"] if "name" in agent else agent["agent"]
            _id(agent_id, "agent_id")
            session_root = _canonical_dir(value["session_root"], "session_root")
            def path_session(observed: object) -> Path | None:
                if not isinstance(observed, dict): return None
                candidate = observed.get("agent_session")
                if not isinstance(candidate, dict) or candidate.get("kind") != "path" or candidate.get("agent") != "pi" or not isinstance(candidate.get("value"), str) or not Path(candidate["value"]).is_absolute(): return None
                session_path = Path(candidate["value"])
                try: canonical_session_path = session_path.resolve(strict=False)
                except OSError: return None
                if session_path != canonical_session_path or not canonical_session_path.is_relative_to(session_root): return None
                try: stat_result = canonical_session_path.stat()
                except OSError: return None
                if not canonical_session_path.is_file() or canonical_session_path.is_symlink() or stat_result.st_nlink != 1: return None
                return canonical_session_path
            canonical_session_path = path_session(agent) or path_session(result)
            if canonical_session_path is None:
                for _ in range(3):
                    observed = _envelope(self._exec([self.herdr, "agent", "get", value["worker_id"]], timeout_ms=_CLI_TIMEOUT_MS, deadline_at=deadline_at), "agent get", {"cli:agent:get"})["result"]
                    observed_agent = observed.get("agent") if isinstance(observed, dict) and isinstance(observed.get("agent"), dict) else observed
                    canonical_session_path = path_session(observed_agent) or path_session(observed)
                    if canonical_session_path is not None: break
            opaque_session = "pending"
            if canonical_session_path is not None: opaque_session = "pi-session-" + hashlib.sha256(str(canonical_session_path).encode("utf-8")).hexdigest()
            result = {"agent_id": agent_id, "workspace_id": agent["workspace_id"], "tab_id": agent["tab_id"], "pane_id": agent["pane_id"], "pi_session_id": opaque_session}
            if any(result[key] != target[key] for key in ("workspace_id", "tab_id", "pane_id")): raise _fail("IDENTITY_MISMATCH", "agent target identity differs")
            pane = _envelope(self._exec([self.herdr, "pane", "get", target["pane_id"]], timeout_ms=10_000, deadline_at=deadline_at), "pane", {"cli:pane:get"})
            pane_result = pane["result"]
            if not isinstance(pane_result, dict) or not isinstance(pane_result.get("pane"), dict): raise _fail("IDENTITY_MISMATCH", "pane response lacks nested pane")
            pane_result = pane_result["pane"]
            if "foreground_cwd" in pane_result and "cwd" in pane_result and _canonical_dir(pane_result["foreground_cwd"], "foreground cwd") != _canonical_dir(pane_result["cwd"], "cwd"):
                raise _fail("IDENTITY_MISMATCH", "pane cwd telemetry conflicts")
            current_cwd = pane_result.get("foreground_cwd", pane_result.get("cwd"))
            if not isinstance(current_cwd, str) or _canonical_dir(current_cwd, "agent cwd") != _canonical_dir(value["worktree"], "worktree"):
                raise _fail("IDENTITY_MISMATCH", "agent cwd/worktree differs")
            if "agent_id" in pane_result and pane_result["agent_id"] != result["agent_id"]:
                raise _fail("IDENTITY_MISMATCH", "pane agent identity differs")
            handle = {"provider_id": "herdr", "handle_id": result["agent_id"], "request_digest": evidence["request_digest"], "attempt_id": value["attempt_id"], "host": value["host"], "output_root_id": value["output_root_id"], "output_root_digest": value["output_root_digest"], "session_root_id": value["session_root_id"], "session_root_digest": value["session_root_digest"]}
            if self.private_root is None: raise _fail("INVALID_REQUEST", "provider private root is unavailable")
            private_dir = _canonical_dir(str(self.private_root), "private_root") / "handles"
            private_dir.mkdir(mode=0o700, exist_ok=True)
            session_path_file = private_dir / ("pi-session-" + result["agent_id"] + ".json")
            session_record_ref = "handles/" + session_path_file.name
            session_record_digest = None; session_status = "pending"
            if canonical_session_path is not None:
                session_record = {"schema_version": 1, "kind": "pi_session", "handle_id": result["agent_id"], "attempt_id": value["attempt_id"], "request_digest": evidence["request_digest"], "host": value["host"], "pi_session_path": str(canonical_session_path), "pi_session_id": opaque_session}
                session_record["record_digest"] = raw_file_digest(canonical_json(session_record))
                fd = os.open(session_path_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream: stream.write(canonical_json(session_record)); stream.flush(); os.fsync(stream.fileno())
                session_record_digest = session_record["record_digest"]; session_status = "bound"
            private = {"schema_version": 1, "handle": handle, "agent_id": result["agent_id"], "pi_session_id": result["pi_session_id"], "session_status": session_status, "capability_report_digest": evidence["capability_report_digest"], "session_record_ref": session_record_ref, "session_record_digest": session_record_digest, "session_binding_ref": "handles/pi-binding-" + result["agent_id"] + ".json", "session_binding_digest": None, "handle_digest": raw_file_digest(canonical_json(handle)), "paths": {"worktree": value["worktree"], "output_root": value["output_root"], "session_root": value["session_root"]}, "attempt_path": value["worktree"], "attempt_ref": "."}
            private["record_digest"] = raw_file_digest(canonical_json(private))
            private_path = private_dir / ("handle-" + result["agent_id"] + ".json")
            private_bytes = canonical_json(private)
            fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream: stream.write(private_bytes); stream.flush(); os.fsync(stream.fileno())
            if session_status == "bound":
                binding = {"schema_version": 1, "kind": "pi_session_binding", "handle_record_digest": private["record_digest"], "session_record_digest": session_record_digest, "attempt_id": value["attempt_id"], "request_digest": evidence["request_digest"], "host": value["host"], "bound_at": _now()}
                binding["record_digest"] = raw_file_digest(canonical_json(binding)); binding_path = private_dir / ("pi-binding-" + result["agent_id"] + ".json")
                fd = os.open(binding_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream: stream.write(canonical_json(binding)); stream.flush(); os.fsync(stream.fileno())
            self._private_handles[result["agent_id"]] = private
            return {"schema_version": 1, "ok": True, "request_id": value["request_id"], "result": {"handle": handle, "capability_report_digest": probe_report["result"]["report_digest"]}}
        except OSError as exc:
            self._settle({"attempt_id": value["attempt_id"]}, "UNKNOWN", cleanup="uncertain", reason=str(exc))
            raise _fail("LAUNCH_FAILED", "private handle persistence failed") from exc
        except ProtocolError as exc:
            self._settle({"attempt_id": value["attempt_id"]}, "UNKNOWN", cleanup="uncertain", reason=str(exc))
            raise

    def _public_evidence(self, attempt_id: str, name: str, summary: Mapping[str, object]) -> dict[str, object]:
        root = self._attempt_paths[attempt_id]
        data = canonical_json({"schema_version": 1, "attempt_id": attempt_id, "summary": dict(summary)})
        if len(data) > 65_536: raise _fail("OUTPUT_LIMIT", "sanitized evidence exceeds bound")
        ref = _write_evidence(root, name + "-" + attempt_id + ".json", data)
        return {"ref": ref, "kind": str(summary["kind"]), "digest_domain": "raw_file", "digest": raw_file_digest(data)}

    def _settle(self, handle: Mapping[str, object], outcome: str, *, cleanup: str = "not_applicable", reason: str = "", evidence_refs: Sequence[Mapping[str, object]] = (), synthetic: bool = True) -> None:
        if self.ledger is None: raise _fail("LEDGER_CORRUPT", "settlement ledger is unavailable")
        durable = self.ledger.records()
        if any(row["attempt_id"] == handle["attempt_id"] and row["event"] == "SETTLED" for row in durable):
            self._settled.add(handle["attempt_id"])
            return
        root = self._attempt_paths.get(handle["attempt_id"])
        if root is None: raise _fail("ARTIFACT_UNAVAILABLE", "attempt worktree is not retained")
        artifact_name = "settled-" + handle["attempt_id"] + ".json"
        data = canonical_json({"attempt_id": handle["attempt_id"], "outcome": outcome, "reason": reason[:500]})
        collected = [dict(ref) for ref in evidence_refs]
        for ref in collected: AttemptLedger._ref(ref, "evidence_ref")
        if synthetic:
            _write_evidence(root, artifact_name, data, existing_ok=True)
        started = next((row for row in durable if row["attempt_id"] == handle["attempt_id"] and row["event"] == "STARTED"), None)
        if started is None: raise _fail("INVALID_ORDER", "settlement has no STARTED event")
        settled = {key: started[key] for key in ("schema_version", "attempt_id", "work_id", "node_id", "observed_generation", "observed_ordinal", "provider_id", "worktree_ref", "branch", "candidate", "candidate_evidence", "request_packet", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest", "request_digest", "herdr")}
        if "prepared_record_digest" in started: settled["prepared_record_digest"] = started["prepared_record_digest"]
        supplemental = [{"ref": ".razr/evidence/" + artifact_name, "digest_domain": "raw_file", "digest": raw_file_digest(data), "kind": "result"}] if synthetic else []
        settled.update({"event": "SETTLED", "settled_at": _now(), "outcome": outcome, "evidence_refs": collected + supplemental, "prev_digest": started["record_digest"], "cleanup": cleanup})
        self.ledger._verify_settlement_evidence(settled, root)
        settled["record_digest"] = raw_file_digest(canonical_json(settled))
        try:
            self.ledger.append(settled)
        except ProtocolError as exc:
            if exc.code == "INVALID_ORDER" and any(row["attempt_id"] == handle["attempt_id"] and row["event"] == "SETTLED" for row in self.ledger.records()):
                self._settled.add(handle["attempt_id"])
                return
            raise
        self._settled.add(handle["attempt_id"])

    def _bind_session_if_available(self, value: Mapping[str, object]) -> bool:
        private = self._private_handles.get(value["handle_id"])
        if private is None or private["session_status"] == "bound": return True
        private_base = _canonical_dir(str(self.private_root), "private_root")
        existing_record = private_base / private["session_record_ref"]
        binding_path = private_base / private["session_binding_ref"]
        if existing_record.exists() and binding_path.exists():
            self._handle(value)
            return True
        if binding_path.exists() and not existing_record.exists():
            raise _fail("IDENTITY_MISMATCH", "session binding exists without its session record")
        session_root = _canonical_dir(private["paths"]["session_root"], "session_root")
        candidate_path = None
        if existing_record.exists():
            try: staged = _strict_json(_read_private_bytes(existing_record, private_base))
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                raise _fail("IDENTITY_MISMATCH", "staged session record is malformed") from exc
            if isinstance(staged, dict) and isinstance(staged.get("pi_session_path"), str):
                candidate_path = Path(staged["pi_session_path"])
        for attempt in range(3 if candidate_path is None else 0):
            observed = _envelope(self._exec([self.herdr, "agent", "get", value["handle_id"]], timeout_ms=_CLI_TIMEOUT_MS), "agent get", {"cli:agent:get"})["result"]
            agent = observed.get("agent") if isinstance(observed, dict) and isinstance(observed.get("agent"), dict) else observed
            session = agent.get("agent_session") if isinstance(agent, dict) else None
            if isinstance(session, dict) and session.get("kind") == "path" and session.get("agent") == "pi" and isinstance(session.get("value"), str) and Path(session["value"]).is_absolute():
                path = Path(session["value"])
                try: canonical = path.resolve(strict=False); stat_result = canonical.stat()
                except OSError: canonical = None
                if canonical is not None and path == canonical and canonical.is_relative_to(session_root) and canonical.is_file() and not canonical.is_symlink() and stat_result.st_nlink == 1: candidate_path = canonical; break
            if attempt < 2: time.sleep(0.05)
        if candidate_path is not None:
            try: candidate_stat = candidate_path.stat()
            except OSError as exc: raise _fail("IDENTITY_MISMATCH", "staged session path is unavailable") from exc
            if (not candidate_path.is_absolute() or candidate_path != candidate_path.resolve(strict=False)
                    or not candidate_path.is_relative_to(session_root) or candidate_path.parent != session_root
                    or not candidate_path.is_file() or candidate_path.is_symlink() or candidate_stat.st_nlink != 1):
                raise _fail("IDENTITY_MISMATCH", "staged session path identity differs")
        if candidate_path is None:
            dirfd = os.open(session_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                names = os.listdir(dirfd); candidates = []
                known = {".razr-herdr-root.json", "agent-status-" + value["attempt_id"] + ".json", "interrupt-" + value["attempt_id"] + ".json", "collect-failure-" + value["attempt_id"] + ".json"}
                for name in names:
                    if name in known: continue
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dirfd)
                    try:
                        file_stat = os.fstat(fd)
                        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.jsonl", name): os.close(fd); return False
                        with os.fdopen(fd, "rb") as stream: content = stream.read(1_048_577)
                        if not content or len(content) > 1_048_576 or not content.endswith(b"\n"): return False
                        for line in content.splitlines():
                            if not line.strip() or not isinstance(json.loads(line), dict): return False
                        candidates.append(session_root / name)
                    except (OSError, UnicodeDecodeError, UnicodeError, json.JSONDecodeError, TypeError): return False
                if len(candidates) != 1: return False
                candidate_path = candidates[0]
            finally: os.close(dirfd)
        opaque = "pi-session-" + hashlib.sha256(str(candidate_path).encode("utf-8")).hexdigest()
        record = {"schema_version": 1, "kind": "pi_session", "handle_id": value["handle_id"], "attempt_id": value["attempt_id"], "request_digest": value["request_digest"], "host": value["host"], "pi_session_path": str(candidate_path), "pi_session_id": opaque}
        record["record_digest"] = raw_file_digest(canonical_json(record))
        path = private_base / private["session_record_ref"]
        with self._dispatch_lock():
            if path.exists():
                try: existing = _strict_json(_read_private_bytes(path, private_base))
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc: raise _fail("IDENTITY_MISMATCH", "session record is malformed") from exc
                if existing != record: raise _fail("IDENTITY_MISMATCH", "session record identity conflicts")
            else:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as stream: stream.write(canonical_json(record)); stream.flush(); os.fsync(stream.fileno())
            if not binding_path.exists():
                binding = {"schema_version": 1, "kind": "pi_session_binding", "handle_record_digest": private["record_digest"], "session_record_digest": record["record_digest"], "attempt_id": value["attempt_id"], "request_digest": value["request_digest"], "host": value["host"], "bound_at": _now()}
                binding["record_digest"] = raw_file_digest(canonical_json(binding))
                fd = os.open(binding_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as stream: stream.write(canonical_json(binding)); stream.flush(); os.fsync(stream.fileno())
            handles_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(handles_fd)
            finally: os.close(handles_fd)
        self._handle(value)
        return True

    def _verify_root_claim(self, root: Path, root_id: str, root_digest: str) -> None:
        if self.private_root is None: raise _fail("IDENTITY_MISMATCH", "private root is unavailable")
        private = _canonical_dir(str(self.private_root), "private_root")
        dirfd = os.open(private, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd = os.open("root-claims.jsonl", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dirfd)
            try:
                file_stat = os.fstat(fd)
                if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1: raise _fail("IDENTITY_MISMATCH", "root claim identity is unsafe")
                with os.fdopen(fd, "rb") as stream:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_SH); claim_bytes = stream.read(); fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                if not claim_bytes.endswith(b"\n"): raise _fail("IDENTITY_MISMATCH", "root claim is partial")
                try: lines = claim_bytes.decode("utf-8").splitlines()
                except UnicodeDecodeError as exc: raise _fail("IDENTITY_MISMATCH", "root claim is not UTF-8") from exc
                previous = "sha256:" + "0" * 64; found = False
                for line in lines:
                    try: row = _strict_json(line.rstrip("\n").encode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError) as exc: raise _fail("IDENTITY_MISMATCH", "root claim is malformed") from exc
                    expected = {"schema_version", "root_id", "root_digest", "path", "attempt_id", "request_digest", "claimed_at", "prev_digest", "record_digest"}
                    if not isinstance(row, dict) or set(row) != expected or row["prev_digest"] != previous or row["record_digest"] != raw_file_digest(canonical_json({key: row[key] for key in row if key != "record_digest"})):
                        raise _fail("IDENTITY_MISMATCH", "root claim chain is corrupt")
                    if row["root_id"] == root_id and row["path"] == str(root) and row["root_digest"] == root_digest: found = True
                    previous = row["record_digest"]
                if not found: raise _fail("IDENTITY_MISMATCH", "root claim identity is missing")
            except (OSError, UnicodeDecodeError) as exc: raise _fail("IDENTITY_MISMATCH", "root claim cannot be read") from exc
        finally: os.close(dirfd)
        try: _marker(root, root_id, root_digest, create=False)
        except ProtocolError as exc: raise _fail("IDENTITY_MISMATCH", "root ownership marker is unavailable", cause=exc.code) from exc

    def _handle(self, handle: Mapping[str, object]) -> dict[str, object]:
        fields = {"provider_id", "handle_id", "request_digest", "attempt_id", "host", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest"}
        value = _closed(handle, fields, "handle")
        if value["provider_id"] != "herdr": raise _fail("IDENTITY_MISMATCH", "foreign provider handle")
        _id(value["handle_id"], "handle_id"); _id(value["attempt_id"], "attempt_id"); _digest(value["request_digest"], "request_digest"); _digest(value["output_root_digest"], "output_root_digest"); _digest(value["session_root_digest"], "session_root_digest")
        if value["host"] != _execution_host(value["host"]): raise _fail("IDENTITY_MISMATCH", "foreign Herdr execution identity")
        if self.private_root is None: raise _fail("IDENTITY_MISMATCH", "provider private root is unavailable")
        private_path = _canonical_dir(str(self.private_root), "private_root") / "handles" / ("handle-" + value["handle_id"] + ".json")
        try: private = _strict_json(_read_private_bytes(private_path, _canonical_dir(str(self.private_root), "private_root")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc: raise _fail("IDENTITY_MISMATCH", "durable private handle evidence is unavailable") from exc
        private = _closed(private, {"schema_version", "handle", "agent_id", "pi_session_id", "session_status", "capability_report_digest", "session_record_ref", "session_record_digest", "session_binding_ref", "session_binding_digest", "handle_digest", "paths", "attempt_path", "attempt_ref", "record_digest"}, "private handle")
        if not isinstance(private["schema_version"], int) or isinstance(private["schema_version"], bool): raise _fail("IDENTITY_MISMATCH", "private handle schema is invalid")
        paths = _closed(private["paths"], {"worktree", "output_root", "session_root"}, "private handle paths")
        private_base = _canonical_dir(str(self.private_root), "private_root")
        for name in paths:
            path = _canonical_dir(paths[name], name)
            if name != "worktree" and not path.is_relative_to(private_base): raise _fail("PATH_ESCAPE", "private handle path escapes provider root")
        _digest(private["capability_report_digest"], "capability_report_digest")
        self._verify_root_claim(_canonical_dir(paths["output_root"], "output_root"), value["output_root_id"], value["output_root_digest"])
        self._verify_root_claim(_canonical_dir(paths["session_root"], "session_root"), value["session_root_id"], value["session_root_digest"])
        if private["session_status"] not in {"pending", "bound"}: raise _fail("IDENTITY_MISMATCH", "invalid private session status")
        if not isinstance(private["session_record_ref"], str) or PurePosixPath(private["session_record_ref"]).is_absolute() or ".." in PurePosixPath(private["session_record_ref"]).parts:
            raise _fail("PATH_ESCAPE", "private session record reference escapes root")
        session_record_path = private_base / private["session_record_ref"]
        if private["session_status"] == "pending":
            if private["session_record_digest"] is not None: raise _fail("IDENTITY_MISMATCH", "pending session digest is unexpected")
            if session_record_path.exists():
                try: pending_record = _strict_json(_read_private_bytes(session_record_path, private_base))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc: raise _fail("IDENTITY_MISMATCH", "pending session binding is malformed") from exc
                pending_record = _closed(pending_record, {"schema_version", "kind", "handle_id", "attempt_id", "request_digest", "host", "pi_session_path", "pi_session_id", "record_digest"}, "private session record")
                if not isinstance(pending_record["schema_version"], int) or isinstance(pending_record["schema_version"], bool): raise _fail("IDENTITY_MISMATCH", "private session schema is invalid")
                session_root = _canonical_dir(paths["session_root"], "session_root"); pending_path = Path(pending_record["pi_session_path"])
                try: pending_stat = pending_path.stat()
                except OSError as exc: raise _fail("IDENTITY_MISMATCH", "pending session path is unavailable") from exc
                if pending_record["kind"] != "pi_session" or pending_record["handle_id"] != private["agent_id"] or pending_record["attempt_id"] != value["attempt_id"] or pending_record["request_digest"] != value["request_digest"] or pending_record["host"] != value["host"] or pending_record["record_digest"] != raw_file_digest(canonical_json({key: pending_record[key] for key in pending_record if key != "record_digest"})) or not pending_path.is_absolute() or pending_path != pending_path.resolve(strict=False) or not pending_path.is_relative_to(session_root) or not pending_path.is_file() or pending_path.is_symlink() or pending_stat.st_nlink != 1 or pending_record["pi_session_id"] != "pi-session-" + hashlib.sha256(str(pending_path).encode("utf-8")).hexdigest():
                    raise _fail("IDENTITY_MISMATCH", "pending session binding differs")
        else:
            _digest(private["session_record_digest"], "session_record_digest")
            try: session_record = _strict_json(_read_private_bytes(session_record_path, private_base))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc: raise _fail("IDENTITY_MISMATCH", "private session record is unavailable") from exc
            session_record = _closed(session_record, {"schema_version", "kind", "handle_id", "attempt_id", "request_digest", "host", "pi_session_path", "pi_session_id", "record_digest"}, "private session record")
            if not isinstance(session_record["schema_version"], int) or isinstance(session_record["schema_version"], bool): raise _fail("IDENTITY_MISMATCH", "private session schema is invalid")
            session_body = {key: session_record[key] for key in session_record if key != "record_digest"}
            if session_record["record_digest"] != raw_file_digest(canonical_json(session_body)) or private["session_record_digest"] != session_record["record_digest"] or session_record["handle_id"] != private["agent_id"] or session_record["attempt_id"] != value["attempt_id"] or session_record["request_digest"] != value["request_digest"] or session_record["host"] != value["host"] or session_record["kind"] != "pi_session":
                raise _fail("IDENTITY_MISMATCH", "private session binding differs")
            session_root = _canonical_dir(paths["session_root"], "session_root"); session_path = Path(session_record["pi_session_path"])
            try: stat_result = session_path.stat()
            except OSError as exc: raise _fail("IDENTITY_MISMATCH", "bound session path is unavailable") from exc
            if not session_path.is_absolute() or session_path != session_path.resolve(strict=False) or not session_path.is_relative_to(session_root) or not session_path.is_file() or session_path.is_symlink() or stat_result.st_nlink != 1 or session_record["pi_session_id"] != "pi-session-" + hashlib.sha256(str(session_path).encode("utf-8")).hexdigest():
                raise _fail("IDENTITY_MISMATCH", "private session path identity differs")
        session_identity = session_record if private["session_status"] == "bound" else (pending_record if "pending_record" in locals() else None)
        if session_identity is not None:
            session_path_exact = Path(session_identity["pi_session_path"])
            if not session_path_exact.is_relative_to(_canonical_dir(paths["session_root"], "session_root")) or session_path_exact.parent != _canonical_dir(paths["session_root"], "session_root"):
                raise _fail("IDENTITY_MISMATCH", "session path is not an exact direct child of session root")
            self._attempt_session_paths[value["attempt_id"]] = session_path_exact
        binding_ref = private["session_binding_ref"]
        if not isinstance(binding_ref, str) or PurePosixPath(binding_ref).is_absolute() or ".." in PurePosixPath(binding_ref).parts: raise _fail("PATH_ESCAPE", "session binding reference escapes root")
        binding_path = private_base / binding_ref
        session_exists = session_record_path.exists(); binding_exists = binding_path.exists()
        if session_exists and not binding_exists and private["session_status"] == "pending" and "pending_record" in locals():
            # The session record is recoverable staging; publish the authoritative
            # binding under the provider lock before accepting durable bound state.
            recovered_binding = {"schema_version": 1, "kind": "pi_session_binding", "handle_record_digest": private["record_digest"],
                                 "session_record_digest": pending_record["record_digest"], "attempt_id": value["attempt_id"],
                                 "request_digest": value["request_digest"], "host": value["host"], "bound_at": _now()}
            recovered_binding["record_digest"] = raw_file_digest(canonical_json(recovered_binding))
            with self._dispatch_lock():
                if not binding_path.exists():
                    fd = os.open(binding_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(fd, "wb") as stream: stream.write(canonical_json(recovered_binding)); stream.flush(); os.fsync(stream.fileno())
                    handles_fd = os.open(binding_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try: os.fsync(handles_fd)
                    finally: os.close(handles_fd)
            binding_exists = binding_path.exists()
        if session_exists != binding_exists: raise _fail("IDENTITY_MISMATCH", "incomplete session binding chain")
        if session_exists:
            try: binding = _strict_json(_read_private_bytes(binding_path, private_base))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc: raise _fail("IDENTITY_MISMATCH", "session binding event is unavailable") from exc
            binding = _closed(binding, {"schema_version", "kind", "handle_record_digest", "session_record_digest", "attempt_id", "request_digest", "host", "bound_at", "record_digest"}, "session binding")
            if not isinstance(binding["schema_version"], int) or isinstance(binding["schema_version"], bool): raise _fail("IDENTITY_MISMATCH", "session binding schema is invalid")
            try: session_record_digest = private["session_record_digest"] or _strict_json(_read_private_bytes(session_record_path, private_base))["record_digest"]
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc: raise _fail("IDENTITY_MISMATCH", "session binding record is malformed") from exc
            if binding["kind"] != "pi_session_binding" or binding["handle_record_digest"] != private["record_digest"] or binding["session_record_digest"] != session_record_digest or binding["attempt_id"] != value["attempt_id"] or binding["request_digest"] != value["request_digest"] or binding["host"] != value["host"] or binding["record_digest"] != raw_file_digest(canonical_json({key: binding[key] for key in binding if key != "record_digest"})):
                raise _fail("IDENTITY_MISMATCH", "session binding event differs")
        record_body = {key: private[key] for key in private if key != "record_digest"}
        if not isinstance(private["schema_version"], int) or isinstance(private["schema_version"], bool) or private["schema_version"] != 1 or private["handle"] != value or private["handle_digest"] != raw_file_digest(canonical_json(value)) or private["record_digest"] != raw_file_digest(canonical_json(record_body)) or private["attempt_path"] != paths["worktree"] or private["attempt_ref"] != ".": raise _fail("IDENTITY_MISMATCH", "handle evidence differs")
        cached = self._private_handles.get(value["handle_id"])
        if cached is not None and cached != private: raise _fail("IDENTITY_MISMATCH", "in-memory handle evidence differs")
        self._private_handles[value["handle_id"]] = private
        self._attempt_paths.setdefault(value["attempt_id"], Path(paths["worktree"]))
        self._attempt_sessions.setdefault(value["attempt_id"], Path(paths["session_root"]))
        self._attempt_roots.setdefault(value["attempt_id"], (Path(paths["output_root"]), Path(paths["session_root"])))
        if self.ledger is None or not self.ledger.path.exists(): raise _fail("IDENTITY_MISMATCH", "handle ledger is unavailable")
        try: records = AttemptLedger._replay_chain([AttemptLedger._replay_validate(_strict_json(line.rstrip("\n").encode("utf-8"))) for line in self.ledger.path.read_text(encoding="utf-8").splitlines()])
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, ProtocolError) as exc: raise _fail("LEDGER_CORRUPT", "handle ledger cannot be replayed") from exc
        started = next((row for row in records if row["event"] == "STARTED" and row["attempt_id"] == value["attempt_id"]), None)
        settled = next((row for row in records if row["event"] == "SETTLED" and row["attempt_id"] == value["attempt_id"]), None)
        if settled is not None: self._settled.add(value["attempt_id"])
        try:
            packet_bytes = _evidence_ref_bytes(Path(paths["worktree"]), started["request_packet"]) if started is not None else b""
            packet = _strict_json(packet_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, ProtocolError) as exc:
            raise _fail("IDENTITY_MISMATCH", "request packet is unavailable") from exc
        if started is not None and raw_file_digest(packet_bytes) != started["request_packet"]["digest"]:
            raise _fail("DIGEST_MISMATCH", "request packet digest differs")
        if not isinstance(packet, dict) or packet.get("capability_report_digest") != private["capability_report_digest"] or packet.get("request_digest") != started.get("request_digest"):
            raise _fail("IDENTITY_MISMATCH", "capability report/request packet identity differs")
        if started is None or any(started.get(key) != value.get(key) for key in ("attempt_id", "provider_id", "request_digest", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest")) or started.get("herdr") != _ledger_host(value["host"]):
            raise _fail("IDENTITY_MISMATCH", "handle does not match STARTED identity")
        return value

    def recover_unmatched(self, request: Mapping[str, object]) -> dict[str, object]:
        request = _closed(request, {"schema_version", "request_id", "attempt_id", "worktree", "recovery_authority", "retained_evidence"}, "recovery request")
        if not isinstance(request["schema_version"], int) or isinstance(request["schema_version"], bool) or request["schema_version"] != 1: raise _fail("INVALID_REQUEST", "invalid recovery schema")
        request_id = _id(request["request_id"], "request_id"); _id(request["attempt_id"], "attempt_id")
        worktree = _canonical_dir(request["worktree"], "recovery worktree")
        authority = _closed(request["recovery_authority"], {"kind", "authority_id", "asserted", "reason"}, "recovery authority")
        if authority["kind"] != "explicit" or authority["asserted"] is not True: raise _fail("INVALID_REQUEST", "explicit recovery authority is required")
        _id(authority["authority_id"], "authority_id")
        if not isinstance(authority["reason"], str) or not authority["reason"]: raise _fail("INVALID_REQUEST", "recovery reason is required")
        evidence = request["retained_evidence"]
        if not isinstance(evidence, list) or not evidence: raise _fail("ARTIFACT_UNAVAILABLE", "retained recovery evidence is required")
        checked_evidence = []
        for ref in evidence:
            try: AttemptLedger._ref(ref, "evidence_ref")
            except ProtocolError as exc:
                if exc.code == "PATH_ESCAPE": raise
                raise _fail("ARTIFACT_UNAVAILABLE", "invalid retained evidence reference") from exc
            data = _evidence_ref_bytes(worktree, ref)
            if len(data) > 1_048_576: raise _fail("OUTPUT_LIMIT", "retained evidence exceeds bound")
            actual = raw_file_digest(data) if ref["digest_domain"] == "raw_file" else None
            if ref["digest_domain"] == "canonical_json":
                try:
                    parsed = _strict_json(data); actual = raw_file_digest(canonical_json(parsed))
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc: raise _fail("DIGEST_MISMATCH", "retained canonical evidence is malformed") from exc
            if actual != ref["digest"]: raise _fail("DIGEST_MISMATCH", "retained evidence digest differs")
            checked_evidence.append(dict(ref))
        evidence = checked_evidence
        if self.ledger is None or not self.ledger.path.exists(): raise _fail("LEDGER_CORRUPT", "recovery ledger is unavailable")
        try: records = AttemptLedger._replay_chain([AttemptLedger._replay_validate(_strict_json(line.rstrip("\n").encode("utf-8"))) for line in self.ledger.path.read_text(encoding="utf-8").splitlines()])
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, ProtocolError) as exc: raise _fail("LEDGER_CORRUPT", "recovery ledger cannot be replayed") from exc
        started = next((row for row in records if row["event"] == "STARTED" and row["attempt_id"] == request["attempt_id"]), None)
        if started is None: raise _fail("INVALID_ORDER", "no unmatched STARTED event")
        if any(row["attempt_id"] == request["attempt_id"] and row["event"] == "SETTLED" for row in records): raise _fail("INVALID_ORDER", "attempt is already settled")
        self._attempt_paths[request["attempt_id"]] = worktree
        self._settle({"attempt_id": request["attempt_id"]}, "UNKNOWN", cleanup="uncertain", reason="explicit recovery", evidence_refs=evidence)
        return {"schema_version": 1, "ok": True, "request_id": request_id, "result": {"attempt_id": request["attempt_id"], "outcome": "UNKNOWN", "recovered": True}}

    def prompt(self, target: str, text: str, *, timeout_ms: int) -> object:
        if not isinstance(target, str) or len(target) > 128: raise _fail("INVALID_REQUEST", "invalid prompt target")
        _id(target, "target")
        if not isinstance(text, str) or not text or len(text.encode("utf-8")) > 65_536 or timeout_ms <= 0: raise _fail("INVALID_REQUEST", "invalid prompt/bound")
        if self.private_root is None: raise _fail("IDENTITY_MISMATCH", "provider private root is unavailable")
        private_root = _canonical_dir(str(self.private_root), "private_root")
        record_path = private_root / "handles" / ("handle-" + target + ".json")
        try: record = _strict_json(_read_private_bytes(record_path, private_root))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc: raise _fail("IDENTITY_MISMATCH", "durable prompt handle is unavailable") from exc
        if not isinstance(record, dict) or not isinstance(record.get("handle"), dict) or record["handle"].get("handle_id") != target: raise _fail("IDENTITY_MISMATCH", "prompt target is not a durable handle")
        value = self._handle(record["handle"])
        if value["handle_id"] != target: raise _fail("IDENTITY_MISMATCH", "prompt handle identity differs")
        response = _envelope(self._exec([self.herdr, "agent", "prompt", target, text, "--wait", "--timeout", str(timeout_ms)], timeout_ms=timeout_ms), "prompt", {"cli:agent:prompt"})
        self._bind_session_if_available(value)
        return response

    def _inspect_handle(self, handle: Mapping[str, object]) -> object:
        value = self._handle(handle); raw = _envelope(self._exec([self.herdr, "agent", "get", value["handle_id"]], timeout_ms=10_000), "inspect", {"cli:agent:get"}); result = raw["result"]
        if not isinstance(result, dict) or not isinstance(result.get("agent"), dict): raise _fail("ARTIFACT_UNAVAILABLE", "agent status lacks nested agent")
        agent = result["agent"]
        if not isinstance(agent.get("name"), str) or agent["name"] != value["handle_id"]: raise _fail("IDENTITY_MISMATCH", "agent status name differs")
        target = _target(value["host"])
        for key in ("workspace_id", "tab_id", "pane_id"):
            if not isinstance(agent.get(key), str) or agent[key] != target[key]: raise _fail("IDENTITY_MISMATCH", "agent status target differs")
        telemetry = agent.get("agent_status")
        if not isinstance(telemetry, str) or telemetry not in {"starting", "working", "idle", "done", "blocked", "unknown"}: raise _fail("ARTIFACT_UNAVAILABLE", "agent status is absent or malformed")
        status = {"starting": "STARTING", "working": "ACTIVE", "idle": "ACTIVE", "done": "ACTIVE", "blocked": "ACTIVE", "unknown": "UNKNOWN"}[telemetry]
        observed = {"status": status, "workspace_id": agent["workspace_id"], "tab_id": agent["tab_id"], "pane_id": agent["pane_id"], "observed_at": _now(), "source": "herdr"}
        return {"schema_version": 1, "ok": True, "request_id": "inspect", "result": observed}

    def read(self, handle: Mapping[str, object], *, lines: int = 160) -> bytes:
        value = self._handle(handle)
        if not isinstance(lines, int) or lines <= 0: raise _fail("INVALID_REQUEST", "invalid line bound")
        data = self._exec([self.herdr, "agent", "read", value["handle_id"], "--source", "recent-unwrapped", "--lines", str(min(lines, 10_000))], timeout_ms=10_000, max_output=1_048_576)
        self._bind_session_if_available(value)
        return data

    def wait(self, handle: Mapping[str, object], *, timeout_ms: int) -> object:
        value = self._handle(handle)
        if timeout_ms <= 0: raise _fail("INVALID_REQUEST", "invalid wait bound")
        return _envelope(self._exec([self.herdr, "agent", "wait", value["handle_id"], "--timeout", str(timeout_ms)], timeout_ms=timeout_ms), "wait", {"cli:agent:wait"})

    def _private_observation(self, handle: Mapping[str, object], kind: str, value: Mapping[str, object]) -> bytes:
        root = self._attempt_roots[handle["attempt_id"]][1]
        data = canonical_json({"schema_version": 1, "kind": kind, "attempt_id": handle["attempt_id"], "handle_id": handle["handle_id"], "request_digest": handle["request_digest"], "host": handle["host"], "observation": dict(value)})
        if len(data) > 65_536: raise _fail("OUTPUT_LIMIT", "private observation exceeds bound")
        path = root / (kind + "-" + handle["attempt_id"] + ".json")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
        except FileExistsError as exc: raise _fail("RESOURCE_REUSED", "private observation already exists") from exc
        return data

    def _interrupt_handle(self, handle: Mapping[str, object], reason: str = "cancel", request_id: str = "interrupt") -> dict[str, object]:
        value = self._handle(handle)
        if reason not in _REASONS: raise _fail("INVALID_REQUEST", "invalid interrupt reason")
        if self._attempt_sessions.get(value["attempt_id"]) is None: raise _fail("ARTIFACT_UNAVAILABLE", "interrupt session root is unavailable")
        interrupt_path = self._attempt_sessions[value["attempt_id"]] / ("interrupt-" + value["attempt_id"] + ".json")
        if interrupt_path.exists():
            try: stored = _strict_json(interrupt_path.read_bytes())
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc: raise _fail("IDENTITY_MISMATCH", "interrupt record is malformed") from exc
            try: stored = _closed(stored, {"schema_version", "kind", "request_id", "handle_id", "request_digest", "reason", "reason_digest", "outcome"}, "interrupt record")
            except ProtocolError as exc: raise _fail("IDENTITY_MISMATCH", "legacy or malformed interrupt record") from exc
            if not isinstance(stored["schema_version"], int) or isinstance(stored["schema_version"], bool): raise _fail("IDENTITY_MISMATCH", "interrupt record schema is invalid")
            expected_reason_digest = raw_file_digest(canonical_json({"reason": reason}))
            if not isinstance(stored["schema_version"], int) or isinstance(stored["schema_version"], bool) or stored["schema_version"] != 1 or stored["kind"] != "interrupt_result" or stored["request_id"] != request_id or stored["handle_id"] != value["handle_id"] or stored["request_digest"] != value["request_digest"] or stored["reason"] != reason or stored["reason_digest"] != expected_reason_digest:
                raise _fail("IDENTITY_MISMATCH", "interrupt reuse identity differs")
            outcome = _closed(stored["outcome"], {"ok", "response", "error"}, "interrupt outcome")
            if outcome["ok"] is True:
                if not isinstance(outcome["response"], dict) or outcome["error"] is not None: raise _fail("IDENTITY_MISMATCH", "invalid stored interrupt success")
                return outcome["response"]
            error = _closed(outcome["error"], {"code", "retryable", "details", "message"}, "interrupt error")
            if outcome["response"] is not None or error["code"] not in ERROR_CODES or not isinstance(error["retryable"], bool) or not isinstance(error["details"], dict): raise _fail("IDENTITY_MISMATCH", "invalid stored interrupt error")
            raise ProtocolError(error["code"], error["message"], retryable=error["retryable"], details=error["details"])
        try:
            self._exec([self.herdr, "agent", "send-keys", value["handle_id"], "ctrl+c"], timeout_ms=10_000)
            observed_raw = self._inspect_handle(value)["result"]["status"]
            self._bind_session_if_available(value)
            observed = "STOPPING" if observed_raw in {"ACTIVE", "STARTING"} else observed_raw
            cleanup = "confirmed" if observed_raw in {"SETTLED", "TERMINATED"} else "uncertain"
            response = {"schema_version": 1, "ok": True, "request_id": request_id, "result": {"attempt_id": value["attempt_id"], "host": value["host"], "requested": True, "observed": observed, "cleanup": cleanup}}
            interrupt_record = {"schema_version": 1, "kind": "interrupt_result", "request_id": request_id, "handle_id": value["handle_id"], "request_digest": value["request_digest"], "reason": reason, "reason_digest": raw_file_digest(canonical_json({"reason": reason})), "outcome": {"ok": True, "response": response, "error": None}}
            interruption_bytes = canonical_json(interrupt_record)
            fd = os.open(interrupt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream: stream.write(interruption_bytes); stream.flush(); os.fsync(stream.fileno())
            interruption_ref = self._public_evidence(value["attempt_id"], "interrupt", {"kind": "event", "private_ref": "private/attempt-" + value["attempt_id"] + "/session/interrupt-" + value["attempt_id"] + ".json", "private_digest": raw_file_digest(interruption_bytes)})
            self._settle(value, "INTERRUPTED" if cleanup == "confirmed" else "UNKNOWN", cleanup=cleanup, reason=reason, evidence_refs=[interruption_ref])
            return response
        except ProtocolError as exc:
            failure = _fail("INTERRUPT_FAILED", "Herdr interrupt command failed", cause=exc.code) if exc.code in {"LAUNCH_FAILED", "TIMEOUT", "PROVIDER_UNAVAILABLE"} else exc
            error_record = {"schema_version": 1, "kind": "interrupt_result", "request_id": request_id, "handle_id": value["handle_id"], "request_digest": value["request_digest"], "reason": reason, "reason_digest": raw_file_digest(canonical_json({"reason": reason})), "outcome": {"ok": False, "response": None, "error": {"code": failure.code, "message": str(failure), "retryable": failure.retryable, "details": failure.details}}}
            try:
                error_bytes = canonical_json(error_record); fd = os.open(interrupt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream: stream.write(error_bytes); stream.flush(); os.fsync(stream.fileno())
                error_ref = self._public_evidence(value["attempt_id"], "interrupt-error", {"kind": "event", "private_ref": "private/attempt-" + value["attempt_id"] + "/session/" + interrupt_path.name, "private_digest": raw_file_digest(error_bytes)})
                self._settle(value, "UNKNOWN", cleanup="uncertain", reason="interrupt failed", evidence_refs=[error_ref])
            except ProtocolError as settle_exc:
                if settle_exc.code == "LEDGER_CORRUPT": raise _fail("LEDGER_CORRUPT", "interrupt failure cannot settle") from settle_exc
            except OSError:
                pass
            raise failure
        except OSError as exc:
            try: self._settle(value, "UNKNOWN", cleanup="uncertain", reason=str(exc), evidence_refs=[])
            except ProtocolError: pass
            raise _fail("INTERRUPT_FAILED", "interruption observation failed") from exc

    def _collect_handle(self, handle: Mapping[str, object]) -> dict[str, object]:
        value = self._handle(handle)
        try:
            status_response = self._inspect_handle(value); status = status_response["result"]["status"]
            raw_status = None
            if status not in {"SETTLED", "TERMINATED"} and value["attempt_id"] not in self._settled:
                raw_get = _envelope(self._exec([self.herdr, "agent", "get", value["handle_id"]], timeout_ms=_CLI_TIMEOUT_MS), "collect status", {"cli:agent:get"})["result"]
                raw_agent = raw_get.get("agent") if isinstance(raw_get, dict) else None
                raw_status = raw_agent.get("agent_status") if isinstance(raw_agent, dict) else None
            if status not in {"SETTLED", "TERMINATED"} and raw_status != "done" and value["attempt_id"] not in self._settled: raise _fail("ARTIFACT_UNAVAILABLE", "collect requires an observed completed turn or settled attempt")
            data = self.read(value)
            session_available = self._bind_session_if_available(value)
            output_root, session_root = self._attempt_roots[value["attempt_id"]]
            output_artifact = output_root / ("agent-read-" + value["attempt_id"] + ".bin")
            status_bytes = canonical_json(status_response["result"])
            status_artifact = session_root / ("agent-status-" + value["attempt_id"] + ".json")
            for artifact, content in ((output_artifact, data), (status_artifact, status_bytes)):
                if len(content) > 1_048_576: raise _fail("OUTPUT_LIMIT", "provider artifact exceeds bound")
                fd = os.open(artifact, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream: stream.write(content); stream.flush(); os.fsync(stream.fileno())
            # The frozen status response has no exit code and no semantic
            # termination. Preserve that uncertainty; never consume worker
            # metadata or invent an exit code.
            response_refs = [{"ref": "private/attempt-" + value["attempt_id"] + "/output/" + output_artifact.name, "kind": "stdout", "digest_domain": "raw_file", "digest": raw_file_digest(data), "bytes": len(data)}, {"ref": "private/attempt-" + value["attempt_id"] + "/session/" + status_artifact.name, "kind": "session", "digest_domain": "raw_file", "digest": raw_file_digest(status_bytes), "bytes": len(status_bytes)}]
            ledger_refs = [self._public_evidence(value["attempt_id"], "collect-" + ref["kind"], {"kind": ref["kind"], "private_ref": ref["ref"], "private_digest": ref["digest"], "bytes": ref["bytes"]}) for ref in response_refs]
            self._settle(value, "UNKNOWN", reason="Herdr status/read observed; termination UNKNOWN", evidence_refs=ledger_refs)
            public_refs = [{key: ref[key] for key in ("kind", "digest_domain", "digest", "bytes")} for ref in response_refs]
            return {"schema_version": 1, "ok": True, "request_id": "collect", "result": {"attempt_id": value["attempt_id"], "termination": "UNKNOWN", "runtime_identity": _MODEL + ":medium", "host": value["host"], "output_root_id": value["output_root_id"], "output_root_digest": value["output_root_digest"], "session_root_id": value["session_root_id"], "session_root_digest": value["session_root_digest"], "artifact_refs": public_refs, "cleanup": "not_applicable"}}
        except OSError as exc:
            failure_data = canonical_json({"schema_version": 1, "kind": "collect_failure", "attempt_id": value["attempt_id"], "error": str(exc)[:500]})
            failure_path = self._attempt_sessions[value["attempt_id"]] / ("collect-failure-" + value["attempt_id"] + ".json")
            try:
                fd = os.open(failure_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream: stream.write(failure_data); stream.flush(); os.fsync(stream.fileno())
                failure_ref = {"ref": "private/attempt-" + value["attempt_id"] + "/session/" + failure_path.name, "kind": "event", "digest_domain": "raw_file", "digest": raw_file_digest(failure_data)}
            except OSError:
                failure_ref = None
            try:
                self._settle(value, "UNKNOWN", cleanup="uncertain", reason="collect artifact persistence failed", evidence_refs=[failure_ref] if failure_ref else [], synthetic=not bool(failure_ref and failure_ref.get("ref", "").startswith("private/")))
            except ProtocolError as settle_exc:
                if settle_exc.code == "LEDGER_CORRUPT": raise _fail("LEDGER_CORRUPT", "collect failure cannot be settled") from settle_exc
                raise _fail("CLEANUP_UNKNOWN", "collect failure could not be durably settled") from settle_exc
            except OSError as settle_exc:
                raise _fail("CLEANUP_UNKNOWN", "collect failure could not be durably settled") from settle_exc
            raise _fail("ARTIFACT_UNAVAILABLE", "collect artifact persistence failed") from exc
        except ProtocolError as exc:
            self._settle(value, "UNKNOWN", cleanup="uncertain", reason=str(exc)); raise

    def inspect(self, request: Mapping[str, object]) -> dict[str, object]:
        request = _closed(request, {"schema_version", "request_id", "handle"}, "inspect request")
        if not isinstance(request["schema_version"], int) or isinstance(request["schema_version"], bool) or request["schema_version"] != 1: raise _fail("INVALID_REQUEST", "invalid inspect schema")
        request_id = _id(request["request_id"], "request_id")
        internal = self._inspect_handle(request["handle"])
        return {"schema_version": 1, "ok": True, "request_id": request_id, "result": internal["result"]}

    def collect(self, request: Mapping[str, object]) -> dict[str, object]:
        request = _closed(request, {"schema_version", "request_id", "handle"}, "collect request")
        if not isinstance(request["schema_version"], int) or isinstance(request["schema_version"], bool) or request["schema_version"] != 1: raise _fail("INVALID_REQUEST", "invalid collect schema")
        request_id = _id(request["request_id"], "request_id")
        internal = self._collect_handle(request["handle"])
        return {"schema_version": 1, "ok": True, "request_id": request_id, "result": internal["result"]}

    def interrupt(self, request: Mapping[str, object]) -> dict[str, object]:
        request = _closed(request, {"schema_version", "request_id", "handle", "reason"}, "interrupt request")
        if not isinstance(request["schema_version"], int) or isinstance(request["schema_version"], bool) or request["schema_version"] != 1: raise _fail("INVALID_REQUEST", "invalid interrupt schema")
        request_id = _id(request["request_id"], "request_id")
        internal = self._interrupt_handle(request["handle"], request["reason"], request_id)
        return {"schema_version": 1, "ok": True, "request_id": request_id, "result": internal["result"]}

    def _cleanup_record(self, handle: Mapping[str, object], outcome: str, **details: object) -> None:
        if self.private_root is None: return
        directory = _canonical_dir(str(self.private_root), "private_root") / "cleanup"
        directory.mkdir(mode=0o700, exist_ok=True)
        record = {"schema_version": 1, "attempt_id": handle["attempt_id"], "handle_id": handle["handle_id"], "request_digest": handle["request_digest"], "outcome": outcome, "observed_at": _now(), "details": details}
        path = directory / "observations.jsonl"
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "ab") as stream:
            stat_result = os.fstat(stream.fileno())
            if not stat.S_ISREG(stat_result.st_mode) or stat_result.st_nlink != 1: raise _fail("CLEANUP_UNKNOWN", "cleanup record is unsafe")
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX); stream.write(canonical_json(record) + b"\n"); stream.flush(); os.fsync(stream.fileno()); fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def cleanup(self, handle: Mapping[str, object]) -> dict[str, object]:
        value = self._handle(handle)
        try: self._cleanup_record(value, "requested")
        except OSError as exc: raise _fail("CLEANUP_UNKNOWN", "cleanup observation persistence failed") from exc
        if value["attempt_id"] not in self._settled:
            self._cleanup_record(value, "uncertain", reason="attempt is not settled")
            raise _fail("CLEANUP_UNKNOWN", "cleanup requires a settled attempt")
        status = self._inspect_handle(value)["result"]["status"]
        try:
            worktree_root = _canonical_dir(str(self._attempt_paths[value["attempt_id"]]), "worktree")
            target = _target(value["host"])
            self._exec([self.herdr, "worktree", "remove", "--workspace", target["workspace_id"], "--force"], timeout_ms=120_000)
            workspace_response = _envelope(self._exec([self.herdr, "workspace", "list"], timeout_ms=10_000), "workspace list", {"cli:workspace:list"})
            workspace_result = workspace_response["result"]
            if not isinstance(workspace_result, dict) or workspace_result.get("type") != "workspace_list" or not isinstance(workspace_result.get("workspaces"), list): raise _fail("CLEANUP_UNKNOWN", "workspace list is malformed")
            for workspace in workspace_result["workspaces"]:
                if not isinstance(workspace, dict) or not isinstance(workspace.get("workspace_id"), str): raise _fail("CLEANUP_UNKNOWN", "workspace identity is malformed")
                if workspace["workspace_id"] == target["workspace_id"]: raise _fail("CLEANUP_UNKNOWN", "workspace remains after cleanup")
            worktree_response = _envelope(self._exec([self.herdr, "worktree", "list"], timeout_ms=10_000), "worktree list", {"cli:worktree:list"})
            worktree_result = worktree_response["result"]
            if not isinstance(worktree_result, dict) or worktree_result.get("type") != "worktree_list" or not isinstance(worktree_result.get("worktrees"), list): raise _fail("CLEANUP_UNKNOWN", "worktree list is malformed")
            root_text = str(worktree_root)
            for worktree in worktree_result["worktrees"]:
                if not isinstance(worktree, dict) or not isinstance(worktree.get("path"), str): raise _fail("CLEANUP_UNKNOWN", "worktree identity is malformed")
                if Path(worktree["path"]).resolve(strict=False) == Path(root_text): raise _fail("CLEANUP_UNKNOWN", "worktree remains registered")
            for root, root_id, root_digest in ((self._attempt_roots[value["attempt_id"]][0], value["output_root_id"], value["output_root_digest"]), (self._attempt_roots[value["attempt_id"]][1], value["session_root_id"], value["session_root_digest"])):
                marker = root / ".razr-herdr-root.json"
                _marker(root, root_id, root_digest, create=False)
                for child in root.iterdir():
                    session_path = self._attempt_session_paths.get(value["attempt_id"])
                    allowed = {marker, session_path, root / ("agent-read-" + value["attempt_id"] + ".bin"), root / ("agent-status-" + value["attempt_id"] + ".json"), root / ("interrupt-" + value["attempt_id"] + ".json"), root / ("collect-failure-" + value["attempt_id"] + ".json")}
                    if child in allowed:
                        if child.is_file() and not child.is_symlink(): child.unlink()
                    else: raise _fail("CLEANUP_UNKNOWN", "foreign/nonempty provider root")
                root.rmdir()
            result = {"attempt_id": value["attempt_id"], "cleanup": "confirmed", "status": status}
            self._cleanup_record(value, "confirmed", status=status)
            return result
        except OSError as exc:
            try:
                if self.private_root is not None:
                    evidence = _canonical_dir(str(self.private_root), "private_root") / ("cleanup-failure-" + value["attempt_id"] + ".json")
                    evidence.write_bytes(canonical_json({"attempt_id": value["attempt_id"], "error": str(exc)[:500]}))
            except OSError:
                pass
            try: self._cleanup_record(value, "uncertain", reason=str(exc)[:500])
            except OSError: pass
            raise _fail("CLEANUP_UNKNOWN", "cleanup filesystem operation failed") from exc
        except ProtocolError as exc:
            try: self._cleanup_record(value, "uncertain", reason=str(exc)[:500])
            except OSError: pass
            raise _fail("CLEANUP_UNKNOWN", str(exc)) from exc


__all__ = ["AttemptLedger", "HerdrProvider", "ProviderRequest", "ProtocolError"]
