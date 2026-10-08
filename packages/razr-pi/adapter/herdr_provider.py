"""Strict, mechanical Herdr transport for the M04 provider boundary.

This module owns transport observations and append-only attempt evidence only.
It never mutates Razr state, chooses work, infers approval, or classifies a
process observation as a workflow transition.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import shutil
import stat
import time
from typing import Callable, Mapping, Sequence

from .protocol import ERROR_CODES, ProtocolError, canonical_json, raw_file_digest
from .core_subprocess import verify_candidate_identity

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_MODEL = "openai-codex/gpt-5.6-luna"
_LAUNCH_EVIDENCE_CODES = frozenset({"LAUNCH_FAILED", "TIMEOUT", "OUTPUT_LIMIT", "PROVIDER_UNAVAILABLE"})
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


def _closed(value: object, fields: set[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise _fail("ARTIFACT_UNAVAILABLE", f"{name} has unknown or missing fields")
    return value


def _envelope(raw: bytes, name: str) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("ARTIFACT_UNAVAILABLE", f"malformed {name} response") from exc
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


def _marker(root: Path, root_id: str, digest: str, *, create: bool) -> None:
    marker = root / ".razr-herdr-root.json"
    if marker.exists():
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _fail("RESOURCE_REUSED", "root ownership marker is malformed") from exc
        if value != {"schema_version": 1, "root_id": root_id, "manifest_digest": digest}:
            raise _fail("RESOURCE_REUSED", "root ownership identity differs")
        if not create: return
        raise _fail("RESOURCE_REUSED", "root identity was already used")
    if not create:
        raise _fail("RESOURCE_REUSED", "root ownership marker is missing")
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"schema_version": 1, "root_id": root_id, "manifest_digest": digest}, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise _fail("RESOURCE_REUSED", "root ownership raced with another attempt") from exc


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
    OPTIONAL_STARTED = {"herdr"}
    OPTIONAL_SETTLED = {"herdr", "exit_code", "cleanup"}

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
        _digest(value["digest"], f"{name} digest")

    @classmethod
    def _validate_event(cls, event: object) -> dict[str, object]:
        if not isinstance(event, dict) or event.get("schema_version") != 1 or event.get("event") not in {"STARTED", "SETTLED"}:
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
            for ref in event["evidence_refs"]: cls._ref(ref, "evidence_ref", "raw_file")
            if "exit_code" in event and (not isinstance(event["exit_code"], int) or isinstance(event["exit_code"], bool)):
                raise _fail("LEDGER_CORRUPT", "invalid exit_code")
            if "cleanup" in event and event["cleanup"] not in {"confirmed", "uncertain", "not_applicable"}:
                raise _fail("LEDGER_CORRUPT", "invalid cleanup")
        if "herdr" in event:
            host = _closed(event["herdr"], {"workspace_id", "tab_id", "pane_id"}, "herdr")
            _id(host["workspace_id"], "workspace_id"); _id(host["tab_id"], "tab_id"); _id(host["pane_id"], "pane_id")
        expected = raw_file_digest(canonical_json({key: value for key, value in event.items() if key != "record_digest"}))
        if event["record_digest"] != expected: raise _fail("LEDGER_CORRUPT", "record digest differs")
        return event

    @classmethod
    def _replay_validate(cls, event: object) -> dict[str, object]:
        try:
            return cls._validate_event(event)
        except ProtocolError as exc:
            raise _fail("LEDGER_CORRUPT", "malformed attempt record", cause=exc.code) from exc
        except (KeyError, TypeError, AttributeError, ValueError, UnicodeError) as exc:
            raise _fail("LEDGER_CORRUPT", "malformed attempt record") from exc

    @classmethod
    def _replay_chain(cls, records: list[dict[str, object]]) -> list[dict[str, object]]:
        previous = "sha256:" + "0" * 64
        for row in records:
            if row["prev_digest"] != previous: raise _fail("LEDGER_CORRUPT", "attempt ledger hash chain is out of order")
            previous = row["record_digest"]
        return records

    def append(self, event: Mapping[str, object]) -> dict[str, object]:
        import copy
        event = copy.deepcopy(dict(event))
        if not isinstance(event, dict) or event.get("schema_version") != 1 or event.get("event") not in {"STARTED", "SETTLED"}:
            raise _fail("LEDGER_CORRUPT", "invalid attempt event")
        event.pop("prev_digest", None); event.pop("record_digest", None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            stream.seek(0); records: list[dict[str, object]] = []
            for line in stream:
                if not line.endswith("\n"): raise _fail("LEDGER_CORRUPT", "partial ledger line")
                try: row = json.loads(line)
                except json.JSONDecodeError as exc: raise _fail("LEDGER_CORRUPT", "malformed ledger JSON") from exc
                records.append(self._replay_validate(row))
            self._replay_chain(records)
            previous = records[-1]["record_digest"] if records else "sha256:" + "0" * 64
            event["prev_digest"] = previous
            event["record_digest"] = raw_file_digest(canonical_json(event))
            event = self._replay_validate(event)
            same = [row for row in records if row["attempt_id"] == event["attempt_id"]]
            if event["event"] == "STARTED" and any(row["event"] == "STARTED" and row["allowance_id"] == event["allowance_id"] and row["attempt_id"] != event["attempt_id"] for row in records):
                raise _fail("ALLOWANCE_REUSED", "allowance is already bound to another attempt")
            if event["event"] == "STARTED" and same: raise _fail("DUPLICATE_ATTEMPT", "duplicate STARTED")
            if event["event"] == "SETTLED" and (len(same) != 1 or same[0]["event"] != "STARTED"):
                raise _fail("INVALID_ORDER", "SETTLED requires one prior STARTED")
            if event["event"] == "SETTLED":
                started = same[0]
                for key in ("work_id", "node_id", "observed_generation", "observed_ordinal", "provider_id", "branch", "worktree_ref", "candidate", "candidate_evidence", "request_packet", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest", "request_digest"):
                    if event.get(key) != started.get(key): raise _fail("IDENTITY_MISMATCH", f"settlement {key} differs")
                if event.get("herdr") != started.get("herdr"): raise _fail("IDENTITY_MISMATCH", "settlement Herdr identity differs")
            stream.seek(0, 2); stream.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"); stream.flush(); os.fsync(stream.fileno())
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
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
        if self.value["schema_version"] != 1 or self.value["fresh_context"] is not True: raise _fail("INVALID_REQUEST", "invalid request version/context")
        _id(self.value["request_id"], "request_id"); _id(self.value["attempt_id"], "attempt_id"); _id(self.value["worker_id"], "worker_id"); _branch(self.value["branch"], "branch"); _id(self.value["allowance_id"], "allowance_id")
        if self.value["role"] not in {"technical", "contract", "worker"}: raise _fail("INVALID_REQUEST", "invalid role")
        if self.value["host"] != _host(): raise _fail("IDENTITY_MISMATCH", "Herdr host identity differs")
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
        if runtime["model"] != _MODEL or runtime["thinking"] != "medium": raise _fail("UNSUPPORTED_CAPABILITY", "exact Pi runtime required")
        limits = _closed(self.value["limits"], {"deadline_ms", "max_output_bytes"}, "limits")
        if not isinstance(limits["deadline_ms"], int) or isinstance(limits["deadline_ms"], bool) or limits["deadline_ms"] <= 0 or not isinstance(limits["max_output_bytes"], int) or isinstance(limits["max_output_bytes"], bool) or limits["max_output_bytes"] <= 0: raise _fail("INVALID_REQUEST", "limits must be positive integers")

    @property
    def digest(self) -> str:
        return raw_file_digest(canonical_json(dict(self.value)))


class HerdrProvider:
    provider_id = "herdr"

    def __init__(self, *, runner: Callable[..., subprocess.CompletedProcess[bytes]] | None = None, herdr: str = "herdr", pi: str = "pi", ledger: AttemptLedger | None = None, private_root: Path | None = None) -> None:
        self._runner = runner or subprocess.run
        self.herdr, self.pi = herdr, pi
        self._resolved = runner is not None
        self.ledger = ledger
        self.private_root = private_root
        self._settled: set[str] = set()
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

    def _exec(self, argv: Sequence[str], *, timeout_ms: int, cwd: Path | None = None, max_output: int = 1_048_576) -> bytes:
        if not argv or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv): raise _fail("INVALID_REQUEST", "invalid argv")
        if timeout_ms <= 0 or max_output <= 0: raise _fail("INVALID_REQUEST", "bounds must be positive")
        if not self._resolved:
            try:
                herdr_path = Path(shutil.which(self.herdr) or "").resolve(strict=True)
                pi_path = Path(shutil.which(self.pi) or "").resolve(strict=True)
            except OSError as exc:
                raise _fail("PROVIDER_UNAVAILABLE", "Herdr/Pi executable unavailable") from exc
            if herdr_path.is_symlink() or not herdr_path.is_file() or pi_path.is_symlink() or not pi_path.is_file(): raise _fail("PROVIDER_UNAVAILABLE", "Herdr/Pi executable is not canonical")
            self.herdr, self.pi, self._resolved = str(herdr_path), str(pi_path), True
        try:
            result = self._runner(list(argv), cwd=str(cwd) if cwd else None, env=self._env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, timeout=timeout_ms / 1000)
        except subprocess.TimeoutExpired as exc: raise _fail("TIMEOUT", "bounded Herdr command timed out") from exc
        except OSError as exc: raise _fail("PROVIDER_UNAVAILABLE", "Herdr executable unavailable") from exc
        stdout, stderr = result.stdout or b"", result.stderr or b""
        if len(stdout) > max_output or len(stderr) > 262_144: raise _fail("OUTPUT_LIMIT", "Herdr output exceeded bound")
        if result.returncode: raise _fail("LAUNCH_FAILED", "Herdr command failed", returncode=result.returncode, stderr=stderr.decode("utf-8", "replace")[:2000])
        return stdout

    def _adapter_bounds_active(self, kind: str) -> bool:
        implementation = getattr(self._exec, "__func__", None) is HerdrProvider._exec
        if kind == "time": return implementation and _CLI_TIMEOUT_MS > 0
        if kind == "output": return implementation and _CLI_MAX_OUTPUT > 0 and 1_048_576 > 0
        return False

    def _post_launch_identity_verification_active(self) -> bool:
        return getattr(self._handle, "__func__", None) is HerdrProvider._handle and getattr(self._inspect_handle, "__func__", None) is HerdrProvider._inspect_handle

    def _model(self) -> str:
        try: output = self._exec([self.pi, "--list-models", "gpt-5.6-luna"], timeout_ms=10_000, max_output=262_144).decode("utf-8", "strict")
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

    def probe(self, request: Mapping[str, object]) -> dict[str, object]:
        request = _closed(request, {"schema_version", "request_id", "required_capabilities", "runtime"}, "probe request")
        if request["schema_version"] != 1: raise _fail("INVALID_REQUEST", "invalid probe schema")
        request_id = _id(request["request_id"], "request_id")
        caps = request["required_capabilities"]
        if not isinstance(caps, list): raise _fail("INVALID_REQUEST", "required_capabilities must be an array")
        runtime = _closed(request["runtime"], {"provider", "model", "thinking"}, "probe runtime")
        if runtime["model"] != _MODEL or runtime["thinking"] != "medium": raise _fail("UNSUPPORTED_CAPABILITY", "exact Pi runtime required")
        _host()
        if any(cap not in _CAPABILITIES for cap in caps): raise _fail("UNSUPPORTED_CAPABILITY", "unknown capability")
        herdr_help = self._exec([self.herdr, "--help"], timeout_ms=10_000, max_output=262_144).decode("utf-8", "replace")
        agent_help = self._exec([self.herdr, "agent", "--help"], timeout_ms=10_000, max_output=262_144).decode("utf-8", "replace")
        read_help = self._exec([self.herdr, "agent", "read", "--help"], timeout_ms=10_000, max_output=262_144).decode("utf-8", "replace")
        worktree_help = self._exec([self.herdr, "worktree", "--help"], timeout_ms=_CLI_TIMEOUT_MS, max_output=_CLI_MAX_OUTPUT).decode("utf-8", "replace")
        worktree_open_help = self._exec([self.herdr, "worktree", "open", "--help"], timeout_ms=_CLI_TIMEOUT_MS, max_output=_CLI_MAX_OUTPUT).decode("utf-8", "replace")
        pane_help = self._exec([self.herdr, "pane", "--help"], timeout_ms=_CLI_TIMEOUT_MS, max_output=_CLI_MAX_OUTPUT).decode("utf-8", "replace")
        pane_get_help = self._exec([self.herdr, "pane", "get", "--help"], timeout_ms=_CLI_TIMEOUT_MS, max_output=_CLI_MAX_OUTPUT).decode("utf-8", "replace")
        pi_help = self._exec([self.pi, "--help"], timeout_ms=10_000, max_output=262_144).decode("utf-8", "replace")
        self._model()
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
        if expected_path is not None and path != expected_path: raise _fail("IDENTITY_MISMATCH", "worktree path differs")
        return {"workspace_id": workspace["workspace_id"], "tab_id": root_pane["tab_id"], "pane_id": root_pane["pane_id"], "path": str(path)}

    def worktree_create(self, *, cwd: str, branch: str, base: str, label: str) -> dict[str, object]:
        root = _canonical_dir(cwd, "cwd"); _branch(branch, "branch"); _branch(base, "base"); _id(label, "label")
        response = _envelope(self._exec([self.herdr, "worktree", "create", "--cwd", str(root), "--branch", branch, "--base", base, "--label", label, "--no-focus"], timeout_ms=120_000), "worktree")
        normalized = self._parse_worktree_response(response, None)
        return {**response, "result": normalized}

    def worktree_open(self, *, path: str) -> dict[str, object]:
        root = _canonical_dir(path, "worktree")
        response = _envelope(self._exec([self.herdr, "worktree", "open", "--path", str(root), "--no-focus"], timeout_ms=120_000), "worktree")
        return {**response, "result": self._parse_worktree_response(response, root)}

    def _resolve_existing_worktree(self, path: Path, expected_host: Mapping[str, object] | None = None) -> dict[str, str] | None:
        response = _envelope(self._exec([self.herdr, "worktree", "list", "--cwd", str(path)], timeout_ms=_CLI_TIMEOUT_MS), "worktree list")
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
        panes_response = _envelope(self._exec([self.herdr, "pane", "list", "--workspace", workspace_id], timeout_ms=_CLI_TIMEOUT_MS), "pane list")
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

    def _verify_candidate(self, request: ProviderRequest) -> None:
        value = request.value
        root = _canonical_dir(value["worktree"], "worktree")
        existing = self._resolve_existing_worktree(root, value["host"])
        opened = {**existing, "path": str(root)} if existing is not None else self.worktree_open(path=str(root))["result"]
        if any(opened[key] != value["host"][key] for key in ("workspace_id", "tab_id", "pane_id")):
            raise _fail("IDENTITY_MISMATCH", "dedicated worktree workspace differs from request host")
        root = _canonical_dir(value["worktree"], "worktree")
        # Provider-side re-verification confirms the checkout; candidate read-back remains foundation caller authority.
        verify_candidate_identity(root, branch=value["candidate"]["branch"], commit=value["candidate"]["commit"], source_digest=value["candidate"]["source_digest"])

    def _claim_root_identity(self, root: Path, root_id: str, root_digest: str, attempt_id: str, request_digest: str) -> None:
        if self.private_root is None: raise _fail("INVALID_REQUEST", "private root unavailable")
        private = _canonical_dir(str(self.private_root), "private_root")
        dirfd = os.open(private, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd = os.open("root-claims.jsonl", os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
            try:
                file_stat = os.fstat(fd)
                if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1: raise _fail("LEDGER_CORRUPT", "root claims is not a private regular file")
                with os.fdopen(fd, "a+", encoding="utf-8") as stream:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX); stream.seek(0); rows = []; previous = "sha256:" + "0" * 64
                    for line in stream:
                        if not line.endswith("\n"): raise _fail("LEDGER_CORRUPT", "partial root claims record")
                        try: row = json.loads(line)
                        except json.JSONDecodeError as exc: raise _fail("LEDGER_CORRUPT", "malformed root claims record") from exc
                        expected = {"schema_version", "root_id", "root_digest", "path", "attempt_id", "request_digest", "claimed_at", "prev_digest", "record_digest"}
                        if not isinstance(row, dict) or set(row) != expected or row["prev_digest"] != previous or row["record_digest"] != raw_file_digest(canonical_json({key: row[key] for key in row if key != "record_digest"})):
                            raise _fail("LEDGER_CORRUPT", "corrupt root claims record")
                        _id(row["root_id"], "root_id"); _digest(row["root_digest"], "root_digest"); _digest(row["request_digest"], "request_digest"); _digest(row["prev_digest"], "prev_digest"); _digest(row["record_digest"], "record_digest")
                        if not isinstance(row["path"], str) or not os.path.isabs(row["path"]): raise _fail("LEDGER_CORRUPT", "invalid root claim path")
                        rows.append(row); previous = row["record_digest"]
                    if any(row["root_id"] == root_id or row["path"] == str(root) for row in rows): raise _fail("RESOURCE_REUSED", "root identity or canonical path was previously claimed")
                    row = {"schema_version": 1, "root_id": root_id, "root_digest": root_digest, "path": str(root), "attempt_id": attempt_id, "request_digest": request_digest, "claimed_at": _now(), "prev_digest": previous}
                    row["record_digest"] = raw_file_digest(canonical_json(row)); stream.seek(0, 2); stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"); stream.flush(); os.fsync(stream.fileno()); fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except (OSError, UnicodeDecodeError) as exc: raise _fail("LEDGER_CORRUPT", "root claims cannot be locked or written") from exc
        finally: os.close(dirfd)

    def _claim_roots(self, request: ProviderRequest, capability_report_digest: str) -> None:
        values = request.value
        markers = []
        for root_key in ("output_root", "session_root"):
            root = _canonical_dir(values[root_key], root_key); markers.append((root, root / ".razr-herdr-root.json", (root / ".razr-herdr-root.json").exists()))
        evidence_dir = _canonical_dir(values["worktree"], "worktree") / ".razr" / "evidence"
        before = set(evidence_dir.iterdir()) if evidence_dir.is_dir() else set()
        try:
            return self._claim_roots_unprotected(request, capability_report_digest)
        except BaseException:
            for root, marker, existed in markers:
                if not existed and marker.is_file():
                    try: marker.unlink()
                    except OSError: pass
            if evidence_dir.is_dir():
                for child in set(evidence_dir.iterdir()) - before:
                    try:
                        if child.is_file() and not child.is_symlink(): child.unlink()
                    except OSError: pass
            raise

    def _claim_roots_unprotected(self, request: ProviderRequest, capability_report_digest: str) -> None:
        values = request.value
        prior: list[dict[str, object]] = []
        if self.ledger is not None and self.ledger.path.exists():
            for line in self.ledger.path.read_text(encoding="utf-8").splitlines():
                try: prior.append(AttemptLedger._replay_validate(json.loads(line)))
                except (json.JSONDecodeError, ProtocolError) as exc: raise _fail("LEDGER_CORRUPT", "cannot claim roots from corrupt ledger") from exc
        for root_key, id_key in (("output_root", "output_root_id"), ("session_root", "session_root_id")):
            root = _canonical_dir(values[root_key], root_key)
            if any(row.get(root_key + "_id") == values[id_key] for row in prior):
                raise _fail("RESOURCE_REUSED", f"{root_key} identity was previously used")
            digest, entries = _manifest(root)
            if digest != values[root_key + "_digest"] or entries: raise _fail("RESOURCE_REUSED", f"{root_key} manifest differs or is non-empty")
            root_id = _id(values[id_key], id_key); self._claim_root_identity(root, root_id, digest, values["attempt_id"], raw_file_digest(canonical_json(values)))
            _marker(root, root_id, digest, create=True)
        packet = {key: values[key] for key in ("schema_version", "request_id", "attempt_id", "role", "work", "worker_id", "branch", "candidate", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest", "fresh_context", "neutral_input_digest", "environment_policy_digest", "tool_policy_digest", "required_capabilities", "runtime", "limits", "allowance_id")}
        _digest(capability_report_digest, "capability_report_digest")
        packet["capability_report_digest"] = capability_report_digest
        packet["request_digest"] = raw_file_digest(canonical_json(packet))
        evidence = _canonical_dir(values["worktree"], "worktree") / ".razr" / "evidence"
        try:
            evidence.mkdir(parents=True, exist_ok=True)
            candidate_path = evidence / ("candidate-" + values["attempt_id"] + ".json")
            packet_path = evidence / ("attempt-" + values["attempt_id"] + ".json")
            candidate_bytes = canonical_json(values["candidate"])
            packet_bytes = canonical_json(packet)
            for path, data in ((candidate_path, candidate_bytes), (packet_path, packet_bytes)):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
        except FileExistsError as exc: raise _fail("RESOURCE_REUSED", "attempt evidence already exists") from exc
        return {"candidate": {"ref": str(candidate_path.relative_to(_canonical_dir(values["worktree"], "worktree"))), "digest_domain": "canonical_json", "digest": raw_file_digest(candidate_bytes)},
                "packet": {"ref": str(packet_path.relative_to(_canonical_dir(values["worktree"], "worktree"))), "digest_domain": "canonical_json", "digest": raw_file_digest(packet_bytes)}, "request_digest": packet["request_digest"], "capability_report_digest": capability_report_digest}

    def _release_unlaunched_claims(self, value: Mapping[str, object]) -> None:
        for root_key, id_key in (("output_root", "output_root_id"), ("session_root", "session_root_id")):
            try:
                root = _canonical_dir(value[root_key], root_key); marker = root / ".razr-herdr-root.json"
                if marker.is_file() and json.loads(marker.read_text(encoding="utf-8")).get("root_id") == value[id_key]: marker.unlink()
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ProtocolError):
                continue

    def launch(self, request: Mapping[str, object]) -> dict[str, object]:
        parsed = ProviderRequest(request)
        probe_report = self.probe({"schema_version": 1, "request_id": request["request_id"], "required_capabilities": request["required_capabilities"], "runtime": request["runtime"]})
        if self.ledger is None: raise _fail("INVALID_REQUEST", "append-only attempt ledger is required")
        if self.private_root is None: raise _fail("INVALID_REQUEST", "provider-approved private root is required")
        private_root = _canonical_dir(str(self.private_root), "private_root")
        for key in ("output_root", "session_root"):
            root = _canonical_dir(parsed.value[key], key)
            if root == private_root or not root.is_relative_to(private_root): raise _fail("PATH_ESCAPE", f"{key} is outside provider private root")
        self._verify_candidate(parsed)
        evidence = self._claim_roots(parsed, probe_report["result"]["report_digest"])
        value = parsed.value; deadline = value["limits"]["deadline_ms"]
        started = {"schema_version": 1, "event": "STARTED", "attempt_id": value["attempt_id"], "work_id": value["work"]["work_id"], "node_id": value["work"]["node_id"], "observed_generation": value["work"]["generation"], "observed_ordinal": value["work"]["ordinal"], "worker_id": value["worker_id"], "branch": value["branch"], "worktree_ref": ".", "allowance_id": value["allowance_id"], "provider_id": self.provider_id, "started_at": _now(), "started_at_source": "adapter_observed", "candidate": {"commit": value["candidate"]["commit"], "source_digest": value["candidate"]["source_digest"]}, "candidate_evidence": evidence["candidate"], "request_packet": evidence["packet"], "output_root_id": value["output_root_id"], "output_root_digest": value["output_root_digest"], "session_root_id": value["session_root_id"], "session_root_digest": value["session_root_digest"], "request_digest": evidence["request_digest"], "herdr": {key: value["host"][key] for key in ("workspace_id", "tab_id", "pane_id")}, "prev_digest": "sha256:" + "0" * 64}
        # The ledger computes no authority: this is a durable observation only.
        # Its predecessor is supplied by the ledger caller/chain; STARTED must
        # be appended before the provider launch below.
        try:
            started = self.ledger.append(started)
        except ProtocolError:
            self._release_unlaunched_claims(value)
            raise
        self._attempt_paths[value["attempt_id"]] = _canonical_dir(value["worktree"], "worktree")
        self._attempt_sessions[value["attempt_id"]] = _canonical_dir(value["session_root"], "session_root")
        self._attempt_roots[value["attempt_id"]] = (_canonical_dir(value["output_root"], "output_root"), _canonical_dir(value["session_root"], "session_root"))
        try:
            response = self._exec([self.herdr, "agent", "start", value["worker_id"], "--kind", "pi", "--pane", value["host"]["pane_id"], "--timeout", str(min(deadline, 300_000)), "--", "--model", _MODEL, "--thinking", "medium", "--session-dir", value["session_root"], "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-context-files", "--no-approve"], timeout_ms=min(deadline, 300_000), cwd=_canonical_dir(value["cwd"], "cwd"), max_output=value["limits"]["max_output_bytes"])
        except ProtocolError as exc:
            failure = _canonical_dir(value["worktree"], "worktree") / ".razr" / "evidence" / ("launch-failure-" + value["attempt_id"] + ".json")
            data = canonical_json({"error_code": exc.code, "message": str(exc)[:500]})
            fd = os.open(failure, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream: stream.write(data); stream.write(b"\n"); stream.flush(); os.fsync(stream.fileno())
            settled = {key: started[key] for key in ("schema_version", "attempt_id", "work_id", "node_id", "observed_generation", "observed_ordinal", "provider_id", "worktree_ref", "branch", "candidate", "candidate_evidence", "request_packet", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest", "request_digest", "herdr")}
            settled.update({"event": "SETTLED", "settled_at": _now(), "outcome": "UNKNOWN" if exc.code == "TIMEOUT" else "FAILED", "evidence_refs": [{"ref": str(failure.relative_to(_canonical_dir(value["worktree"], "worktree"))), "digest_domain": "raw_file", "digest": raw_file_digest(data + b"\n"), "kind": "launch_failure", "error_code": exc.code}], "prev_digest": started["record_digest"], "cleanup": "not_applicable"})
            settled["record_digest"] = raw_file_digest(canonical_json(settled)); self.ledger.append(settled)
            raise
        try:
            envelope = _envelope(response, "agent start"); result = envelope["result"]
            if not isinstance(result, dict) or not isinstance(result.get("agent"), dict): raise _fail("HERDR_IDENTITY_INCOMPLETE", "agent response lacks nested agent identity")
            agent = result["agent"]
            if "name" in agent:
                if not isinstance(agent["name"], str) or agent["name"] != value["worker_id"]: raise _fail("IDENTITY_MISMATCH", "agent name differs")
            elif "agent" not in agent or not isinstance(agent["agent"], str) or agent["agent"] != value["worker_id"]:
                raise _fail("IDENTITY_MISMATCH", "agent name is missing")
            for key in ("workspace_id", "tab_id", "pane_id"):
                if not isinstance(agent.get(key), str) or agent[key] != value["host"][key]: raise _fail("IDENTITY_MISMATCH", "agent host identity differs")
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
                    observed = _envelope(self._exec([self.herdr, "agent", "get", value["worker_id"]], timeout_ms=_CLI_TIMEOUT_MS), "agent get")["result"]
                    observed_agent = observed.get("agent") if isinstance(observed, dict) and isinstance(observed.get("agent"), dict) else observed
                    canonical_session_path = path_session(observed_agent) or path_session(observed)
                    if canonical_session_path is not None: break
            opaque_session = "pending"
            if canonical_session_path is not None: opaque_session = "pi-session-" + hashlib.sha256(str(canonical_session_path).encode("utf-8")).hexdigest()
            result = {"agent_id": agent_id, "workspace_id": agent["workspace_id"], "tab_id": agent["tab_id"], "pane_id": agent["pane_id"], "pi_session_id": opaque_session}
            if any(result[key] != value["host"][key] for key in ("workspace_id", "tab_id", "pane_id")): raise _fail("IDENTITY_MISMATCH", "agent host identity differs")
            pane = _envelope(self._exec([self.herdr, "pane", "get", value["host"]["pane_id"]], timeout_ms=10_000), "pane")
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
        root = self._attempt_paths[attempt_id]; directory = root / ".razr" / "evidence"; directory.mkdir(parents=True, exist_ok=True)
        data = canonical_json({"schema_version": 1, "attempt_id": attempt_id, "summary": dict(summary)})
        if len(data) > 65_536: raise _fail("OUTPUT_LIMIT", "sanitized evidence exceeds bound")
        path = directory / (name + "-" + attempt_id + ".json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
        return {"ref": str(path.relative_to(root)), "kind": str(summary["kind"]), "digest_domain": "raw_file", "digest": raw_file_digest(data)}

    def _settle(self, handle: Mapping[str, object], outcome: str, *, cleanup: str = "not_applicable", reason: str = "", evidence_refs: Sequence[Mapping[str, object]] = (), synthetic: bool = True) -> None:
        if handle["attempt_id"] in self._settled: return
        if self.ledger is None: raise _fail("LEDGER_CORRUPT", "settlement ledger is unavailable")
        root = self._attempt_paths.get(handle["attempt_id"])
        if root is None: raise _fail("ARTIFACT_UNAVAILABLE", "attempt worktree is not retained")
        evidence_dir = root / ".razr" / "evidence"; evidence_dir.mkdir(parents=True, exist_ok=True)
        artifact = evidence_dir / ("settled-" + handle["attempt_id"] + ".json")
        data = canonical_json({"attempt_id": handle["attempt_id"], "outcome": outcome, "reason": reason[:500]})
        collected = [dict(ref) for ref in evidence_refs]
        for ref in collected: AttemptLedger._ref(ref, "evidence_ref")
        if synthetic and not artifact.exists():
            fd = os.open(artifact, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
        try: records = AttemptLedger._replay_chain([AttemptLedger._replay_validate(json.loads(line)) for line in self.ledger.path.read_text(encoding="utf-8").splitlines()])
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ProtocolError) as exc: raise _fail("LEDGER_CORRUPT", "settlement ledger cannot be replayed") from exc
        started = next((row for row in records if row["attempt_id"] == handle["attempt_id"] and row["event"] == "STARTED"), None)
        if started is None: raise _fail("INVALID_ORDER", "settlement has no STARTED event")
        settled = {key: started[key] for key in ("schema_version", "attempt_id", "work_id", "node_id", "observed_generation", "observed_ordinal", "provider_id", "worktree_ref", "branch", "candidate", "candidate_evidence", "request_packet", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest", "request_digest", "herdr")}
        supplemental = [{"ref": str(artifact.relative_to(root)), "digest_domain": "raw_file", "digest": raw_file_digest(data), "kind": "result"}] if synthetic else []
        settled.update({"event": "SETTLED", "settled_at": _now(), "outcome": outcome, "evidence_refs": collected + supplemental, "prev_digest": started["record_digest"], "cleanup": cleanup})
        settled["record_digest"] = raw_file_digest(canonical_json(settled)); self.ledger.append(settled); self._settled.add(handle["attempt_id"])

    def _bind_session_if_available(self, value: Mapping[str, object]) -> bool:
        private = self._private_handles.get(value["handle_id"])
        if private is None or private["session_status"] == "bound": return True
        private_base = _canonical_dir(str(self.private_root), "private_root")
        existing_record = private_base / private["session_record_ref"]
        if existing_record.exists():
            self._handle(value)
            return True
        session_root = _canonical_dir(private["paths"]["session_root"], "session_root")
        candidate_path = None
        for attempt in range(3):
            observed = _envelope(self._exec([self.herdr, "agent", "get", value["handle_id"]], timeout_ms=_CLI_TIMEOUT_MS), "agent get")["result"]
            agent = observed.get("agent") if isinstance(observed, dict) and isinstance(observed.get("agent"), dict) else observed
            session = agent.get("agent_session") if isinstance(agent, dict) else None
            if isinstance(session, dict) and session.get("kind") == "path" and session.get("agent") == "pi" and isinstance(session.get("value"), str) and Path(session["value"]).is_absolute():
                path = Path(session["value"])
                try: canonical = path.resolve(strict=False); stat_result = canonical.stat()
                except OSError: canonical = None
                if canonical is not None and path == canonical and canonical.is_relative_to(session_root) and canonical.is_file() and not canonical.is_symlink() and stat_result.st_nlink == 1: candidate_path = canonical; break
            if attempt < 2: time.sleep(0.05)
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
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream: stream.write(canonical_json(record)); stream.flush(); os.fsync(stream.fileno())
        except FileExistsError as exc: raise _fail("RESOURCE_REUSED", "session binding already exists") from exc
        binding = {"schema_version": 1, "kind": "pi_session_binding", "handle_record_digest": private["record_digest"], "session_record_digest": record["record_digest"], "attempt_id": value["attempt_id"], "request_digest": value["request_digest"], "host": value["host"], "bound_at": _now()}
        binding["record_digest"] = raw_file_digest(canonical_json(binding))
        binding_path = private_base / private["session_binding_ref"]
        try:
            fd = os.open(binding_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream: stream.write(canonical_json(binding)); stream.flush(); os.fsync(stream.fileno())
        except FileExistsError as exc: raise _fail("RESOURCE_REUSED", "session binding event already exists") from exc
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
                    try: row = json.loads(line)
                    except json.JSONDecodeError as exc: raise _fail("IDENTITY_MISMATCH", "root claim is malformed") from exc
                    expected = {"schema_version", "root_id", "root_digest", "path", "attempt_id", "request_digest", "claimed_at", "prev_digest", "record_digest"}
                    if not isinstance(row, dict) or set(row) != expected or row["prev_digest"] != previous or row["record_digest"] != raw_file_digest(canonical_json({key: row[key] for key in row if key != "record_digest"})):
                        raise _fail("IDENTITY_MISMATCH", "root claim chain is corrupt")
                    if row["root_id"] == root_id and row["path"] == str(root) and row["root_digest"] == root_digest: found = True
                    previous = row["record_digest"]
                if not found: raise _fail("IDENTITY_MISMATCH", "root claim identity is missing")
            except (OSError, UnicodeDecodeError) as exc: raise _fail("IDENTITY_MISMATCH", "root claim cannot be read") from exc
        finally: os.close(dirfd)
        marker = root / ".razr-herdr-root.json"
        if not marker.is_file() or marker.is_symlink(): raise _fail("IDENTITY_MISMATCH", "root ownership marker is unavailable")
        _marker(root, root_id, root_digest, create=False)

    def _handle(self, handle: Mapping[str, object]) -> dict[str, object]:
        fields = {"provider_id", "handle_id", "request_digest", "attempt_id", "host", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest"}
        value = _closed(handle, fields, "handle")
        if value["provider_id"] != "herdr": raise _fail("IDENTITY_MISMATCH", "foreign provider handle")
        _id(value["handle_id"], "handle_id"); _id(value["attempt_id"], "attempt_id"); _digest(value["request_digest"], "request_digest"); _digest(value["output_root_digest"], "output_root_digest"); _digest(value["session_root_digest"], "session_root_digest")
        if value["host"] != _host(): raise _fail("IDENTITY_MISMATCH", "foreign Herdr host")
        if self.private_root is None: raise _fail("IDENTITY_MISMATCH", "provider private root is unavailable")
        private_path = _canonical_dir(str(self.private_root), "private_root") / "handles" / ("handle-" + value["handle_id"] + ".json")
        try: private = json.loads(_read_private_bytes(private_path, _canonical_dir(str(self.private_root), "private_root")).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc: raise _fail("IDENTITY_MISMATCH", "durable private handle evidence is unavailable") from exc
        private = _closed(private, {"schema_version", "handle", "agent_id", "pi_session_id", "session_status", "capability_report_digest", "session_record_ref", "session_record_digest", "session_binding_ref", "session_binding_digest", "handle_digest", "paths", "attempt_path", "attempt_ref", "record_digest"}, "private handle")
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
                try: pending_record = json.loads(_read_private_bytes(session_record_path, private_base).decode("utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc: raise _fail("IDENTITY_MISMATCH", "pending session binding is malformed") from exc
                pending_record = _closed(pending_record, {"schema_version", "kind", "handle_id", "attempt_id", "request_digest", "host", "pi_session_path", "pi_session_id", "record_digest"}, "private session record")
                session_root = _canonical_dir(paths["session_root"], "session_root"); pending_path = Path(pending_record["pi_session_path"])
                try: pending_stat = pending_path.stat()
                except OSError as exc: raise _fail("IDENTITY_MISMATCH", "pending session path is unavailable") from exc
                if pending_record["kind"] != "pi_session" or pending_record["handle_id"] != private["agent_id"] or pending_record["attempt_id"] != value["attempt_id"] or pending_record["request_digest"] != value["request_digest"] or pending_record["host"] != value["host"] or pending_record["record_digest"] != raw_file_digest(canonical_json({key: pending_record[key] for key in pending_record if key != "record_digest"})) or not pending_path.is_absolute() or pending_path != pending_path.resolve(strict=False) or not pending_path.is_relative_to(session_root) or not pending_path.is_file() or pending_path.is_symlink() or pending_stat.st_nlink != 1 or pending_record["pi_session_id"] != "pi-session-" + hashlib.sha256(str(pending_path).encode("utf-8")).hexdigest():
                    raise _fail("IDENTITY_MISMATCH", "pending session binding differs")
        else:
            _digest(private["session_record_digest"], "session_record_digest")
            try: session_record = json.loads(_read_private_bytes(session_record_path, private_base).decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc: raise _fail("IDENTITY_MISMATCH", "private session record is unavailable") from exc
            session_record = _closed(session_record, {"schema_version", "kind", "handle_id", "attempt_id", "request_digest", "host", "pi_session_path", "pi_session_id", "record_digest"}, "private session record")
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
        if session_exists != binding_exists: raise _fail("IDENTITY_MISMATCH", "incomplete session binding chain")
        if session_exists:
            try: binding = json.loads(_read_private_bytes(binding_path, private_base).decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc: raise _fail("IDENTITY_MISMATCH", "session binding event is unavailable") from exc
            binding = _closed(binding, {"schema_version", "kind", "handle_record_digest", "session_record_digest", "attempt_id", "request_digest", "host", "bound_at", "record_digest"}, "session binding")
            if binding["kind"] != "pi_session_binding" or binding["handle_record_digest"] != private["record_digest"] or binding["session_record_digest"] != (private["session_record_digest"] or (json.loads(_read_private_bytes(session_record_path, private_base).decode("utf-8"))["record_digest"])) or binding["attempt_id"] != value["attempt_id"] or binding["request_digest"] != value["request_digest"] or binding["host"] != value["host"] or binding["record_digest"] != raw_file_digest(canonical_json({key: binding[key] for key in binding if key != "record_digest"})):
                raise _fail("IDENTITY_MISMATCH", "session binding event differs")
        record_body = {key: private[key] for key in private if key != "record_digest"}
        if private["schema_version"] != 1 or private["handle"] != value or private["handle_digest"] != raw_file_digest(canonical_json(value)) or private["record_digest"] != raw_file_digest(canonical_json(record_body)) or private["attempt_path"] != paths["worktree"] or private["attempt_ref"] != ".": raise _fail("IDENTITY_MISMATCH", "handle evidence differs")
        cached = self._private_handles.get(value["handle_id"])
        if cached is not None and cached != private: raise _fail("IDENTITY_MISMATCH", "in-memory handle evidence differs")
        self._private_handles[value["handle_id"]] = private
        self._attempt_paths.setdefault(value["attempt_id"], Path(paths["worktree"]))
        self._attempt_sessions.setdefault(value["attempt_id"], Path(paths["session_root"]))
        self._attempt_roots.setdefault(value["attempt_id"], (Path(paths["output_root"]), Path(paths["session_root"])))
        if self.ledger is None or not self.ledger.path.exists(): raise _fail("IDENTITY_MISMATCH", "handle ledger is unavailable")
        try: records = AttemptLedger._replay_chain([AttemptLedger._replay_validate(json.loads(line)) for line in self.ledger.path.read_text(encoding="utf-8").splitlines()])
        except (OSError, json.JSONDecodeError, ProtocolError) as exc: raise _fail("LEDGER_CORRUPT", "handle ledger cannot be replayed") from exc
        started = next((row for row in records if row["event"] == "STARTED" and row["attempt_id"] == value["attempt_id"]), None)
        settled = next((row for row in records if row["event"] == "SETTLED" and row["attempt_id"] == value["attempt_id"]), None)
        if settled is not None: self._settled.add(value["attempt_id"])
        packet_path = Path(paths["worktree"]) / started["request_packet"]["ref"] if started is not None else Path("/")
        try:
            packet_bytes = packet_path.read_bytes()
            packet = json.loads(packet_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _fail("IDENTITY_MISMATCH", "request packet is unavailable") from exc
        if started is not None and raw_file_digest(packet_bytes) != started["request_packet"]["digest"]:
            raise _fail("DIGEST_MISMATCH", "request packet digest differs")
        if not isinstance(packet, dict) or packet.get("capability_report_digest") != private["capability_report_digest"] or packet.get("request_digest") != started.get("request_digest"):
            raise _fail("IDENTITY_MISMATCH", "capability report/request packet identity differs")
        if started is None or any(started.get(key) != value.get(key) for key in ("attempt_id", "provider_id", "request_digest", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest")) or started.get("herdr") != {key: value["host"][key] for key in ("workspace_id", "tab_id", "pane_id")}:
            raise _fail("IDENTITY_MISMATCH", "handle does not match STARTED identity")
        return value

    def recover_unmatched(self, request: Mapping[str, object]) -> dict[str, object]:
        request = _closed(request, {"schema_version", "request_id", "attempt_id", "worktree", "recovery_authority", "retained_evidence"}, "recovery request")
        if request["schema_version"] != 1: raise _fail("INVALID_REQUEST", "invalid recovery schema")
        request_id = _id(request["request_id"], "request_id"); _id(request["attempt_id"], "attempt_id")
        worktree = _canonical_dir(request["worktree"], "recovery worktree")
        authority = _closed(request["recovery_authority"], {"kind", "authority_id", "asserted", "reason"}, "recovery authority")
        if authority["kind"] != "explicit" or authority["asserted"] is not True: raise _fail("INVALID_REQUEST", "explicit recovery authority is required")
        _id(authority["authority_id"], "authority_id")
        if not isinstance(authority["reason"], str) or not authority["reason"]: raise _fail("INVALID_REQUEST", "recovery reason is required")
        evidence = request["retained_evidence"]
        if not isinstance(evidence, list) or not evidence: raise _fail("ARTIFACT_UNAVAILABLE", "retained recovery evidence is required")
        checked_evidence = []
        evidence_root = worktree / ".razr" / "evidence"
        for ref in evidence:
            try: AttemptLedger._ref(ref, "evidence_ref")
            except ProtocolError as exc:
                if exc.code == "PATH_ESCAPE": raise
                raise _fail("ARTIFACT_UNAVAILABLE", "invalid retained evidence reference") from exc
            relative = PurePosixPath(ref["ref"])
            if len(relative.parts) < 3 or relative.parts[:2] != (".razr", "evidence"):
                raise _fail("PATH_ESCAPE", "retained evidence is outside the evidence boundary")
            path = worktree.joinpath(*relative.parts)
            cursor = worktree
            for part in relative.parts:
                cursor = cursor / part
                if cursor.is_symlink(): raise _fail("PATH_ESCAPE", "retained evidence contains a symlink")
            try: resolved = path.resolve(strict=True)
            except OSError as exc: raise _fail("ARTIFACT_UNAVAILABLE", "retained evidence is missing") from exc
            if not resolved.is_relative_to(evidence_root) or resolved.is_symlink() or not resolved.is_file(): raise _fail("PATH_ESCAPE", "retained evidence escapes boundary")
            try:
                data = resolved.read_bytes()
            except OSError as exc: raise _fail("ARTIFACT_UNAVAILABLE", "retained evidence cannot be read") from exc
            if len(data) > 1_048_576: raise _fail("OUTPUT_LIMIT", "retained evidence exceeds bound")
            actual = raw_file_digest(data) if ref["digest_domain"] == "raw_file" else None
            if ref["digest_domain"] == "canonical_json":
                try: actual = raw_file_digest(canonical_json(json.loads(data.decode("utf-8"))))
                except (UnicodeDecodeError, json.JSONDecodeError, ProtocolError) as exc: raise _fail("DIGEST_MISMATCH", "retained canonical evidence is malformed") from exc
            if actual != ref["digest"]: raise _fail("DIGEST_MISMATCH", "retained evidence digest differs")
            checked_evidence.append(dict(ref))
        evidence = checked_evidence
        if self.ledger is None or not self.ledger.path.exists(): raise _fail("LEDGER_CORRUPT", "recovery ledger is unavailable")
        try: records = AttemptLedger._replay_chain([AttemptLedger._replay_validate(json.loads(line)) for line in self.ledger.path.read_text(encoding="utf-8").splitlines()])
        except (OSError, json.JSONDecodeError, ProtocolError) as exc: raise _fail("LEDGER_CORRUPT", "recovery ledger cannot be replayed") from exc
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
        try: record = json.loads(_read_private_bytes(record_path, private_root).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc: raise _fail("IDENTITY_MISMATCH", "durable prompt handle is unavailable") from exc
        if not isinstance(record, dict) or not isinstance(record.get("handle"), dict) or record["handle"].get("handle_id") != target: raise _fail("IDENTITY_MISMATCH", "prompt target is not a durable handle")
        value = self._handle(record["handle"])
        if value["handle_id"] != target: raise _fail("IDENTITY_MISMATCH", "prompt handle identity differs")
        response = _envelope(self._exec([self.herdr, "agent", "prompt", target, text, "--wait", "--timeout", str(timeout_ms)], timeout_ms=timeout_ms), "prompt")
        self._bind_session_if_available(value)
        return response

    def _inspect_handle(self, handle: Mapping[str, object]) -> object:
        value = self._handle(handle); raw = _envelope(self._exec([self.herdr, "agent", "get", value["handle_id"]], timeout_ms=10_000), "inspect"); result = raw["result"]
        if not isinstance(result, dict) or not isinstance(result.get("agent"), dict): raise _fail("ARTIFACT_UNAVAILABLE", "agent status lacks nested agent")
        agent = result["agent"]
        if not isinstance(agent.get("name"), str) or agent["name"] != value["handle_id"]: raise _fail("IDENTITY_MISMATCH", "agent status name differs")
        for key in ("workspace_id", "tab_id", "pane_id"):
            if not isinstance(agent.get(key), str) or agent[key] != value["host"][key]: raise _fail("IDENTITY_MISMATCH", "agent status host differs")
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
        return _envelope(self._exec([self.herdr, "agent", "wait", value["handle_id"], "--timeout", str(timeout_ms)], timeout_ms=timeout_ms), "wait")

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
            try: stored = json.loads(interrupt_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc: raise _fail("IDENTITY_MISMATCH", "interrupt record is malformed") from exc
            try: stored = _closed(stored, {"schema_version", "kind", "request_id", "handle_id", "request_digest", "reason", "reason_digest", "outcome"}, "interrupt record")
            except ProtocolError as exc: raise _fail("IDENTITY_MISMATCH", "legacy or malformed interrupt record") from exc
            expected_reason_digest = raw_file_digest(canonical_json({"reason": reason}))
            if stored["schema_version"] != 1 or stored["kind"] != "interrupt_result" or stored["request_id"] != request_id or stored["handle_id"] != value["handle_id"] or stored["request_digest"] != value["request_digest"] or stored["reason"] != reason or stored["reason_digest"] != expected_reason_digest:
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
                raw_get = _envelope(self._exec([self.herdr, "agent", "get", value["handle_id"]], timeout_ms=_CLI_TIMEOUT_MS), "collect status")["result"]
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
        if request["schema_version"] != 1: raise _fail("INVALID_REQUEST", "invalid inspect schema")
        request_id = _id(request["request_id"], "request_id")
        internal = self._inspect_handle(request["handle"])
        return {"schema_version": 1, "ok": True, "request_id": request_id, "result": internal["result"]}

    def collect(self, request: Mapping[str, object]) -> dict[str, object]:
        request = _closed(request, {"schema_version", "request_id", "handle"}, "collect request")
        if request["schema_version"] != 1: raise _fail("INVALID_REQUEST", "invalid collect schema")
        request_id = _id(request["request_id"], "request_id")
        internal = self._collect_handle(request["handle"])
        return {"schema_version": 1, "ok": True, "request_id": request_id, "result": internal["result"]}

    def interrupt(self, request: Mapping[str, object]) -> dict[str, object]:
        request = _closed(request, {"schema_version", "request_id", "handle", "reason"}, "interrupt request")
        if request["schema_version"] != 1: raise _fail("INVALID_REQUEST", "invalid interrupt schema")
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
            self._exec([self.herdr, "worktree", "remove", "--workspace", value["host"]["workspace_id"], "--force"], timeout_ms=120_000)
            workspace_response = _envelope(self._exec([self.herdr, "workspace", "list"], timeout_ms=10_000), "workspace list")
            workspace_result = workspace_response["result"]
            if not isinstance(workspace_result, dict) or workspace_result.get("type") != "workspace_list" or not isinstance(workspace_result.get("workspaces"), list): raise _fail("CLEANUP_UNKNOWN", "workspace list is malformed")
            for workspace in workspace_result["workspaces"]:
                if not isinstance(workspace, dict) or not isinstance(workspace.get("workspace_id"), str): raise _fail("CLEANUP_UNKNOWN", "workspace identity is malformed")
                if workspace["workspace_id"] == value["host"]["workspace_id"]: raise _fail("CLEANUP_UNKNOWN", "workspace remains after cleanup")
            worktree_response = _envelope(self._exec([self.herdr, "worktree", "list"], timeout_ms=10_000), "worktree list")
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
