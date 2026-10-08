"""Bounded, mechanical Herdr provider transport.

Herdr observations are operational telemetry only.  This module never edits Razr
state, selects work, interprets agent status as success, or retries an attempt.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import time
from typing import Callable, Mapping, Sequence

from .protocol import ProtocolError, canonical_json, raw_file_digest

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MODEL = "openai-codex/gpt-5.6-luna"
_CAPABILITIES = (
    "fresh_context", "working_directory", "environment_policy", "bounded_time",
    "bounded_output", "stream_capture", "interrupt", "runtime_identity",
)
_ALLOWED_ENV = {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "HERDR_CONFIG_PATH"}


def _error(code: str, message: str, **details: object) -> ProtocolError:
    return ProtocolError(code, message, details=details)


def _id(value: object, name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise _error("INVALID_REQUEST", f"invalid {name}")
    return value


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise _error("INVALID_REQUEST", f"invalid {name}")
    return value


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _path(value: object, name: str, *, directory: bool = True) -> Path:
    if not isinstance(value, str) or not os.path.isabs(value):
        raise _error("INVALID_REQUEST", f"{name} must be absolute")
    p = Path(value)
    try:
        resolved = p.resolve(strict=True)
    except OSError as exc:
        raise _error("INVALID_REQUEST", f"{name} is unavailable") from exc
    if p != resolved or p.is_symlink() or (not p.is_dir() if directory else not p.is_file()):
        raise _error("INVALID_REQUEST", f"invalid {name}")
    return p


def _host() -> dict[str, str]:
    if os.environ.get("HERDR_ENV") != "1":
        raise _error("FANOUT_UNAVAILABLE", "Herdr environment is not active")
    values = {key: os.environ.get(key, "") for key in ("HERDR_WORKSPACE_ID", "HERDR_TAB_ID", "HERDR_PANE_ID")}
    if any(not value for value in values.values()):
        raise _error("HERDR_IDENTITY_INCOMPLETE", "Herdr workspace, tab, and pane identity are required")
    return {"mode": "herdr", "workspace_id": _id(values["HERDR_WORKSPACE_ID"], "workspace_id"),
            "tab_id": _id(values["HERDR_TAB_ID"], "tab_id"), "pane_id": _id(values["HERDR_PANE_ID"], "pane_id")}


@dataclass(frozen=True)
class ProviderRequest:
    """The closed request projection used by :class:`HerdrProvider`."""
    request: Mapping[str, object]

    def __post_init__(self) -> None:
        required = {"schema_version", "request_id", "attempt_id", "role", "work", "worker_id", "branch",
                    "candidate", "worktree", "cwd", "output_root", "output_root_id", "output_root_digest",
                    "session_root", "session_root_id", "session_root_digest", "fresh_context",
                    "neutral_input_digest", "environment_policy_digest", "tool_policy_digest",
                    "required_capabilities", "runtime", "limits", "allowance_id"}
        if set(self.request) != required | {"host"}:
            raise _error("INVALID_REQUEST", "provider request has unknown or missing fields")
        if self.request.get("schema_version") != 1 or self.request.get("fresh_context") is not True:
            raise _error("INVALID_REQUEST", "invalid provider request version or context")
        _id(self.request["request_id"], "request_id"); _id(self.request["attempt_id"], "attempt_id")
        _id(self.request["worker_id"], "worker_id"); _id(self.request["allowance_id"], "allowance_id")
        if self.request["role"] not in {"technical", "contract", "worker"}:
            raise _error("INVALID_REQUEST", "invalid role")
        _path(self.request["worktree"], "worktree"); cwd = _path(self.request["cwd"], "cwd")
        if cwd != Path(self.request["worktree"]):
            raise _error("INVALID_REQUEST", "cwd and worktree differ")
        for key in ("output_root", "session_root"):
            _path(self.request[key], key)
        if self.request["output_root"] == self.request["session_root"]:
            raise _error("RESOURCE_REUSED", "output and session roots must differ")
        for key in ("output_root_digest", "session_root_digest", "neutral_input_digest",
                    "environment_policy_digest", "tool_policy_digest"):
            _digest(self.request[key], key)
        caps = self.request["required_capabilities"]
        if not isinstance(caps, list) or any(cap not in _CAPABILITIES for cap in caps):
            raise _error("UNSUPPORTED_CAPABILITY", "unsupported capability requested")
        runtime = self.request["runtime"]
        if not isinstance(runtime, dict) or set(runtime) != {"provider", "model", "thinking"}:
            raise _error("INVALID_REQUEST", "invalid runtime")
        if runtime["model"] != _MODEL or runtime["thinking"] != "medium":
            raise _error("UNSUPPORTED_CAPABILITY", "exact installed Pi runtime is required")
        limits = self.request["limits"]
        if not isinstance(limits, dict) or set(limits) != {"deadline_ms", "max_output_bytes"}:
            raise _error("INVALID_REQUEST", "invalid limits")
        if not isinstance(limits["deadline_ms"], int) or limits["deadline_ms"] <= 0 or not isinstance(limits["max_output_bytes"], int) or limits["max_output_bytes"] <= 0:
            raise _error("INVALID_REQUEST", "invalid limits")
        host = self.request["host"]
        if host != _host():
            raise _error("IDENTITY_MISMATCH", "request Herdr identity differs from host")

    @property
    def value(self) -> Mapping[str, object]:
        return self.request

    @property
    def digest(self) -> str:
        return raw_file_digest(canonical_json(dict(self.request)))


class AttemptLedger:
    """Small append-only STARTED/SETTLED evidence chain owned by the adapter."""
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: Mapping[str, object]) -> dict[str, object]:
        import fcntl
        if set(event) - {"schema_version", "event", "attempt_id", "request_digest", "prev_digest", "record_digest"}:
            # The complete mission event is intentionally carried opaquely; only
            # these transport invariants are owned here and checked mechanically.
            pass
        if event.get("schema_version") != 1 or event.get("event") not in {"STARTED", "SETTLED"}:
            raise _error("INVALID_REQUEST", "invalid attempt event")
        attempt = _id(event.get("attempt_id"), "attempt_id")
        _digest(event.get("request_digest"), "request_digest")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            stream.seek(0); prior: list[dict[str, object]] = []
            for line in stream:
                if not line.endswith("\n"):
                    raise _error("LEDGER_CORRUPT", "partial attempt ledger line")
                try: row = json.loads(line)
                except json.JSONDecodeError as exc: raise _error("LEDGER_CORRUPT", "malformed attempt ledger") from exc
                if not isinstance(row, dict): raise _error("LEDGER_CORRUPT", "attempt event is not an object")
                prior.append(row)
            previous = prior[-1]["record_digest"] if prior else "sha256:" + "0" * 64
            same = [row for row in prior if row.get("attempt_id") == attempt]
            if event["event"] == "STARTED" and same:
                raise _error("DUPLICATE_ATTEMPT", "attempt already started")
            if event.get("prev_digest") != previous:
                raise _error("LEDGER_CORRUPT", "attempt chain predecessor differs")
            if event["event"] == "SETTLED" and (len(same) != 1 or same[0].get("event") != "STARTED"):
                raise _error("INVALID_ORDER", "attempt settlement is out of order")
            body = dict(event); body.pop("record_digest", None)
            digest = raw_file_digest(canonical_json(body))
            if event.get("record_digest") != digest:
                raise _error("DIGEST_MISMATCH", "attempt record digest differs")
            stream.seek(0, 2); stream.write(json.dumps(dict(event), sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush(); os.fsync(stream.fileno()); fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        return dict(event)


class HerdrProvider:
    """Run only documented Herdr CLI argv, with injectable subprocess execution."""
    provider_id = "herdr"

    def __init__(self, *, runner: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
                 herdr: str = "herdr", pi: str = "pi") -> None:
        self.herdr, self.pi, self._runner = herdr, pi, runner or subprocess.run
        self._used_roots: set[str] = set()

    def _exec(self, argv: Sequence[str], *, timeout_ms: int, max_output: int = 1_048_576) -> bytes:
        if not argv or any(not isinstance(arg, str) or not arg for arg in argv):
            raise _error("INVALID_REQUEST", "argv must be non-empty strings")
        env = {key: value for key, value in os.environ.items() if key in _ALLOWED_ENV}
        env.update({key: os.environ[key] for key in ("HERDR_ENV", "HERDR_WORKSPACE_ID", "HERDR_TAB_ID", "HERDR_PANE_ID") if key in os.environ})
        try:
            result = self._runner(list(argv), cwd=None, env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                                  timeout=timeout_ms / 1000)
        except subprocess.TimeoutExpired as exc:
            raise _error("TIMEOUT", "Herdr command timed out", argv=list(argv)) from exc
        except OSError as exc:
            raise _error("PROVIDER_UNAVAILABLE", "Herdr command unavailable") from exc
        output = result.stdout or b""
        if len(output) > max_output or len(result.stderr or b"") > 262_144:
            raise _error("OUTPUT_LIMIT", "Herdr command output exceeded bound")
        if result.returncode:
            raise _error("LAUNCH_FAILED", "Herdr command failed", returncode=result.returncode,
                         stderr=(result.stderr or b"").decode("utf-8", "replace")[:2000])
        return output

    @staticmethod
    def _json(output: bytes) -> object:
        try:
            value = json.loads(output.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _error("ARTIFACT_UNAVAILABLE", "Herdr returned malformed JSON") from exc
        if not isinstance(value, dict):
            raise _error("ARTIFACT_UNAVAILABLE", "Herdr returned a non-object response")
        return value

    def probe(self, required_capabilities: Sequence[str] = ()) -> dict[str, object]:
        host = _host()
        for cap in required_capabilities:
            if cap not in _CAPABILITIES:
                raise _error("UNSUPPORTED_CAPABILITY", f"unsupported capability: {cap}")
        self._exec([self.herdr, "--help"], timeout_ms=10_000, max_output=262_144)
        self._exec([self.herdr, "agent", "--help"], timeout_ms=10_000, max_output=262_144)
        self._exec([self.pi, "--list-models", "gpt-5.6-luna"], timeout_ms=10_000, max_output=262_144)
        result = {"provider_id": self.provider_id, "runtime_identity": _MODEL + ":medium",
                  "capabilities": list(_CAPABILITIES), "report_digest": ""}
        result["report_digest"] = raw_file_digest(canonical_json(result))
        return {"schema_version": 1, "ok": True, "request_id": "probe", "result": result}

    def worktree_create(self, *, cwd: str, branch: str, base: str, label: str) -> dict[str, object]:
        _path(cwd, "cwd"); _id(branch, "branch")
        output = self._exec([self.herdr, "worktree", "create", "--cwd", cwd, "--branch", branch,
                             "--base", base, "--label", label, "--no-focus"], timeout_ms=120_000)
        return self._json(output)  # IDs come from Herdr; never predict them.

    def launch(self, request: Mapping[str, object]) -> dict[str, object]:
        provider_request = ProviderRequest(request)
        value = provider_request.value
        for root_key, id_key in (("output_root", "output_root_id"), ("session_root", "session_root_id")):
            root = Path(value[root_key])
            if root_key + ":" + str(value[id_key]) in self._used_roots or any(root.iterdir()):
                raise _error("RESOURCE_REUSED", f"{root_key} was reused or is non-empty")
            self._used_roots.add(root_key + ":" + str(value[id_key]))
        self.probe(value["required_capabilities"])
        runtime = value["runtime"]
        deadline = value["limits"]["deadline_ms"]
        # Herdr's documented agent-start shape starts Pi in the existing pane;
        # worktree creation/opening establishes that pane's cwd.  Do not invent a
        # Pi --cwd flag (the installed Pi does not expose one).
        self._exec([self.herdr, "agent", "start", str(value["worker_id"]), "--kind", "pi",
                    "--pane", value["host"]["pane_id"], "--timeout", str(min(deadline, 300_000)), "--",
                    "--model", runtime["model"], "--thinking", runtime["thinking"],
                    "--session-dir", value["session_root"], "--no-extensions", "--no-skills", "--no-prompt-templates",
                    "--no-themes", "--no-context-files"], timeout_ms=min(deadline, 300_000))
        return {"schema_version": 1, "ok": True, "request_id": value["request_id"], "result": {
            "handle": {"provider_id": "herdr", "handle_id": value["worker_id"], "request_digest": provider_request.digest,
                        "attempt_id": value["attempt_id"], "host": value["host"], "output_root_id": value["output_root_id"],
                        "output_root_digest": value["output_root_digest"], "session_root_id": value["session_root_id"],
                        "session_root_digest": value["session_root_digest"]}, "capability_report_digest": ""}}

    def prompt(self, target: str, text: str, *, timeout_ms: int) -> object:
        _id(target, "target")
        if not isinstance(text, str) or not text:
            raise _error("INVALID_REQUEST", "prompt must be non-empty")
        return self._json(self._exec([self.herdr, "agent", "prompt", target, text, "--wait", "--timeout", str(timeout_ms)], timeout_ms=timeout_ms))

    def inspect(self, handle: Mapping[str, object]) -> object:
        _host(); target = _id(handle.get("handle_id"), "handle_id")
        return self._json(self._exec([self.herdr, "agent", "get", target], timeout_ms=10_000))

    def read(self, target: str, *, lines: int = 160) -> bytes:
        _id(target, "target")
        if not isinstance(lines, int) or lines <= 0 or lines > 10_000:
            raise _error("INVALID_REQUEST", "invalid line limit")
        return self._exec([self.herdr, "agent", "read", target, "--source", "recent-unwrapped", "--lines", str(lines)], timeout_ms=10_000)

    def wait(self, target: str, *, timeout_ms: int) -> object:
        _id(target, "target")
        return self._json(self._exec([self.herdr, "agent", "wait", target, "--timeout", str(timeout_ms)], timeout_ms=timeout_ms))

    def interrupt(self, handle: Mapping[str, object], reason: str = "cancel") -> dict[str, object]:
        _host(); target = _id(handle.get("handle_id"), "handle_id")
        if reason not in {"cancel", "timeout", "policy_violation"}:
            raise _error("INVALID_REQUEST", "invalid interrupt reason")
        self._exec([self.herdr, "agent", "send-keys", target, "ctrl-c"], timeout_ms=10_000)
        return {"schema_version": 1, "ok": True, "request_id": handle.get("request_id", "interrupt"), "result": {
            "attempt_id": handle["attempt_id"], "host": _host(), "requested": True, "observed": "UNKNOWN", "cleanup": "uncertain"}}

    def collect(self, handle: Mapping[str, object]) -> dict[str, object]:
        _host(); target = _id(handle.get("handle_id"), "handle_id")
        data = self.read(target)
        return {"schema_version": 1, "ok": True, "request_id": handle.get("request_id", "collect"), "result": {
            "attempt_id": handle["attempt_id"], "termination": "UNKNOWN", "runtime_identity": _MODEL + ":medium",
            "host": _host(), "output_root_id": handle["output_root_id"], "output_root_digest": handle["output_root_digest"],
            "session_root_id": handle["session_root_id"], "session_root_digest": handle["session_root_digest"],
            "artifact_refs": [{"kind": "event", "digest_domain": "raw_file", "digest": raw_file_digest(data), "bytes": len(data)}],
            "exit_code": 0, "cleanup": "uncertain"}}

    def cleanup(self, handle: Mapping[str, object]) -> None:
        _host(); _id(handle.get("handle_id"), "handle_id")
        # Cleanup is deliberately explicit and never inferred from an agent state.
        self._exec([self.herdr, "pane", "close", _host()["pane_id"]], timeout_ms=10_000)


__all__ = ["HerdrProvider", "ProviderRequest", "ProtocolError"]
