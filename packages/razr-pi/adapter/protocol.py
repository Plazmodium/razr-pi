"""Closed, dependency-free wire types for the Razr adapter boundary."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
import math
import re
from typing import Literal, TypeAlias

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PROVENANCE_PATHS = (
    "scripts/razr.py", "templates/evidence.md", "templates/executable.yaml",
    "templates/razr.yaml", "templates/spec.md", "templates/state.yaml",
)
ErrorCode: TypeAlias = Literal[
    "INVALID_REQUEST", "OUTPUT_LIMIT", "TIMEOUT", "INTERRUPTED", "CORE_EXIT",
    "MALFORMED_RESPONSE", "READBACK_FAILED", "PROVENANCE_MISMATCH",
    "LOCK_UNAVAILABLE", "LEDGER_CORRUPT", "DUPLICATE_ATTEMPT", "INVALID_ORDER",
    "ALLOWANCE_REUSED", "PATH_ESCAPE", "SOURCE_CHANGED", "INTERNAL",
    "UNSUPPORTED_CAPABILITY", "IDENTITY_MISMATCH", "DIGEST_MISMATCH", "STALE_GENERATION",
    "CANDIDATE_CHANGED", "RESOURCE_REUSED", "FRESH_CONTEXT_UNAVAILABLE", "LAUNCH_FAILED",
    "INTERRUPT_FAILED", "ARTIFACT_UNAVAILABLE", "CLEANUP_UNKNOWN", "PROVIDER_UNAVAILABLE",
    "FANOUT_UNAVAILABLE", "HERDR_IDENTITY_INCOMPLETE", "WORKFLOW_UNAVAILABLE",
    "MEMORY_UNAVAILABLE", "LIMIT_EXCEEDED"
]
ERROR_CODES = frozenset(ErrorCode.__args__)


class ProtocolError(Exception):
    def __init__(self, code: ErrorCode, message: str, *, retryable: bool = False,
                 details: dict[str, object] | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = {} if details is None else details


def _digest(value: str, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ProtocolError("INVALID_REQUEST", f"invalid {label}")
    return value


def validate_git_object_id(value: str) -> str:
    return _digest(value, FULL_SHA_RE, "Git object ID")


def validate_source_digest(value: str) -> str:
    return _digest(value, DIGEST_RE, "source digest")


def validate_executable_digest(value: str) -> str:
    return _digest(value, DIGEST_RE, "executable digest")


def raw_file_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _reject_constant(value: str) -> object:
    raise ValueError("non-finite JSON number")


def _reject_duplicate(keys: list[str]) -> dict[str, object]:
    raise ValueError("duplicate JSON object key")


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProtocolError("INVALID_REQUEST", "non-finite JSON number")
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ProtocolError("INVALID_REQUEST", "JSON object keys must be strings")
        return {key: _json_value(item) for key, item in value.items()}
    raise ProtocolError("INVALID_REQUEST", "value is not a JSON type")


def canonical_json(value: JsonValue) -> bytes:
    """Serialize canonical JSON after recursively enforcing the JSON value model."""
    try:
        checked = _json_value(value)
        return json.dumps(checked, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ProtocolError("INVALID_REQUEST", "value is not canonical JSON") from exc


@dataclass(frozen=True, slots=True)
class ProvenancePin:
    path: Literal["scripts/razr.py", "templates/evidence.md", "templates/executable.yaml", "templates/razr.yaml", "templates/spec.md", "templates/state.yaml"]
    sha256: str

    def __post_init__(self) -> None:
        if self.path not in PROVENANCE_PATHS or not isinstance(self.sha256, str) or not HEX64_RE.fullmatch(self.sha256):
            raise ProtocolError("INVALID_REQUEST", "invalid provenance pin")


@dataclass(frozen=True, slots=True)
class ReadbackSpec:
    path: str
    required: bool
    max_bytes: int
    parse_as: Literal["bytes", "json"]

    def __post_init__(self) -> None:
        p = PurePosixPath(self.path)
        if (not self.path or p.is_absolute() or ".." in p.parts or str(p) != self.path
                or not isinstance(self.required, bool) or self.max_bytes <= 0
                or self.max_bytes > 1_048_576 or self.parse_as not in ("bytes", "json")):
            raise ProtocolError("INVALID_REQUEST", "invalid read-back specification")


@dataclass(frozen=True, slots=True)
class CoreRequest:
    request_id: str
    repository_root: Path
    argv: tuple[str, ...]
    cwd: Path
    package_root: Path
    python_executable: Path
    runtime_script: Path
    expected_commit: str
    expected_source_digest: str
    expected_core_identity: str
    expected_provenance: tuple[ProvenancePin, ...]
    expected_readback: tuple[ReadbackSpec, ...]
    timeout_ms: int
    termination_grace_ms: int
    max_stdout_bytes: int
    max_stderr_bytes: int


@dataclass(frozen=True, slots=True)
class ReadbackResult:
    path: str
    digest_domain: Literal["raw_file", "canonical_json"]
    digest: str
    bytes: int
    parsed: object | None
    raw_bytes: bytes = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class CoreResponse:
    request_id: str
    returncode: int
    stdout: bytes
    stderr: bytes
    elapsed_ms: int
    core_identity: str
    provenance: tuple[ProvenancePin, ...]
    source_digest: str
    readback: tuple[ReadbackResult, ...]


@dataclass(frozen=True, slots=True)
class WorkflowRead:
    request_id: str
    work_id: str
    stage: str
    status: str
    generation: int
    node_id: str
    node_status: str
    node_attempt: int
    node_result_ref: str | None
    candidate_repository: str | None
    candidate_branch: str | None
    candidate_commit: str | None
    candidate_source_digest: str | None
    status_row: dict[str, object]
    raw_state_path: str
    raw_state_digest: str
    raw_state_bytes: int
    raw_state_root: Path
    raw_state_core_identity: str
    raw_state_source_digest: str
    raw_state_provenance: tuple[ProvenancePin, ...]
