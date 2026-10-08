"""The sole subprocess boundary to the frozen Razr Python core."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath
import selectors
import signal
import subprocess
import time
from typing import Any

from .protocol import (
    CoreRequest, CoreResponse, ProtocolError, ProvenancePin, ReadbackResult, WorkflowRead,
    canonical_json, raw_file_digest, validate_git_object_id, validate_source_digest,
)

MAX_STDOUT_BYTES = 1_048_576
MAX_STDERR_BYTES = 262_144
DEFAULT_TIMEOUT_MS = 120_000
DEFAULT_TERMINATION_GRACE_MS = 5_000
_ENV_NAMES = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
              "PYTHONIOENCODING", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL",
              "GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT")


def _fail(code: str, message: str, **details: object) -> ProtocolError:
    return ProtocolError(code, message, details=details)


def _canonical(path: Path, *, directory: bool | None = None) -> Path:
    if not path.is_absolute():
        raise _fail("INVALID_REQUEST", "operational paths must be absolute")
    try:
        value = path.resolve(strict=True)
    except OSError as exc:
        raise _fail("INVALID_REQUEST", "operational path is unavailable") from exc
    if path != value:
        raise _fail("INVALID_REQUEST", "operational path is not canonical")
    if directory is True and not value.is_dir():
        raise _fail("INVALID_REQUEST", "directory required")
    if directory is False and not value.is_file():
        raise _fail("INVALID_REQUEST", "regular file required")
    if value.is_symlink():
        raise _fail("INVALID_REQUEST", "symlink is not allowed")
    return value


def _validate_request(request: CoreRequest) -> tuple[Path, Path, Path]:
    forbidden_git = sorted(name for name in os.environ if name.startswith("GIT_") and name not in {
        "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT"})
    if forbidden_git:
        raise _fail("INVALID_REQUEST", "ambient Git override refused", names=forbidden_git)
    if not isinstance(request.request_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request.request_id):
        raise _fail("INVALID_REQUEST", "invalid request ID")
    root = _canonical(request.repository_root, directory=True)
    cwd = _canonical(request.cwd, directory=True)
    if root != cwd:
        raise _fail("INVALID_REQUEST", "repository root and cwd differ")
    package = _canonical(request.package_root, directory=True)
    python = _canonical(request.python_executable, directory=False)
    runtime = _canonical(request.runtime_script, directory=False)
    if not runtime.is_relative_to(package):
        raise _fail("INVALID_REQUEST", "runtime script is outside package")
    if not isinstance(request.argv, tuple) or not all(isinstance(v, str) and v for v in request.argv):
        raise _fail("INVALID_REQUEST", "invalid core arguments")
    for value, maximum, name in ((request.timeout_ms, DEFAULT_TIMEOUT_MS, "timeout_ms"),
                                 (request.termination_grace_ms, DEFAULT_TERMINATION_GRACE_MS, "termination_grace_ms"),
                                 (request.max_stdout_bytes, MAX_STDOUT_BYTES, "max_stdout_bytes"),
                                 (request.max_stderr_bytes, MAX_STDERR_BYTES, "max_stderr_bytes")):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0 or value > maximum:
            raise _fail("INVALID_REQUEST", f"invalid {name}")
    try:
        provenance_path = package / "runtime/provenance.json"
        provenance_bytes = provenance_path.read_bytes()
        if request.expected_core_identity != raw_file_digest(provenance_bytes):
            raise _fail("PROVENANCE_MISMATCH", "provenance identity differs")
        pins = {pin.path: pin for pin in request.expected_provenance}
        required = {"scripts/razr.py", "templates/evidence.md", "templates/executable.yaml",
                    "templates/razr.yaml", "templates/spec.md", "templates/state.yaml"}
        if set(pins) != required:
            raise _fail("PROVENANCE_MISMATCH", "incomplete provenance pins")
        declaration = json.loads(provenance_bytes.decode("utf-8"), object_pairs_hook=_json_object)
        if (not isinstance(declaration, dict) or set(declaration) != {"source", "policy", "files"}
                or not isinstance(declaration["files"], list) or len(declaration["files"]) != 6):
            raise _fail("PROVENANCE_MISMATCH", "invalid provenance schema")
        rows = {}
        for row in declaration["files"]:
            if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
                raise _fail("PROVENANCE_MISMATCH", "invalid provenance row")
            rows[row["path"]] = row["sha256"]
        if set(rows) != required:
            raise _fail("PROVENANCE_MISMATCH", "invalid provenance paths")
        for relative, pin in pins.items():
            path = package / "runtime" / relative
            if not path.is_file() or path.is_symlink() or raw_file_digest(path.read_bytes()) != "sha256:" + pin.sha256:
                raise _fail("PROVENANCE_MISMATCH", "pinned runtime file differs", path=relative)
            if rows.get(relative) != pin.sha256:
                raise _fail("PROVENANCE_MISMATCH", "provenance declaration differs", path=relative)
    except ProtocolError:
        raise
    except (OSError, UnicodeDecodeError, UnicodeError, json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
        raise _fail("PROVENANCE_MISMATCH", "provenance declaration unavailable") from exc
    validate_git_object_id(request.expected_commit)
    _verify_git_identity(root)
    if not _git_commit_exists(root, request.expected_commit):
        raise _fail("IDENTITY_MISMATCH", "expected commit is not present")
    return root, package, runtime


def _verify_git_identity(root: Path) -> None:
    git_entry = root / ".git"
    if not git_entry.exists() or git_entry.is_symlink() or not (git_entry.is_dir() or git_entry.is_file()):
        raise _fail("IDENTITY_MISMATCH", "ambiguous or missing Git administration")
    env = _environment()
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "--path-format=absolute",
                                 "--show-toplevel", "--git-dir", "--git-common-dir",
                                 "--is-inside-work-tree", "--is-bare-repository"], env=env, cwd=root,
                                capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _fail("IDENTITY_MISMATCH", "Git identity query unavailable") from exc
    values = result.stdout.decode("utf-8", "replace").splitlines()
    if result.returncode or result.stderr or len(values) != 5 or values[0] != str(root) or values[3:] != ["true", "false"]:
        raise _fail("IDENTITY_MISMATCH", "ambiguous Git administration")
    git_dir, common_dir = (Path(values[1]), Path(values[2]))
    if (not git_dir.is_absolute() or not common_dir.is_absolute() or git_dir.is_symlink()
            or common_dir.is_symlink() or not git_dir.is_dir() or not common_dir.is_dir()):
        raise _fail("IDENTITY_MISMATCH", "ambiguous Git administration")


def _git_commit_exists(root: Path, commit: str) -> bool:
    try:
        result = subprocess.run(["git", "-C", str(root), "cat-file", "-e", commit + "^{commit}"],
                                env=_environment(), cwd=root, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _fail("IDENTITY_MISMATCH", "Git commit identity query unavailable") from exc
    return result.returncode == 0 and not result.stdout and not result.stderr


def _source_digest(root: Path, commit: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "-z", "--full-tree", commit],
                                env=_environment(), cwd=root, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _fail("SOURCE_CHANGED", "cannot inspect source tree") from exc
    if result.returncode != 0:
        raise _fail("SOURCE_CHANGED", "source tree inspection failed")
    # Match core load_json/validate_config/safe_work_root: malformed or linked config refuses.
    work_root = _core_work_root(root)
    prefix = work_root.rstrip("/") + "/"
    entries = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, raw_path = raw.split(b"\t", 1)
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise _fail("SOURCE_CHANGED", "invalid Git tree response") from exc
        if path in {"razr.yaml", "AGENTS.md"} or path.startswith(prefix):
            continue
        entries.append(metadata + b"\t" + raw_path + b"\0")
    import hashlib
    return "sha256:" + hashlib.sha256(b"".join(entries)).hexdigest()


def _core_work_root(root: Path) -> str:
    config = root / "razr.yaml"
    if not config.exists():
        return ".razr/work"
    if config.is_symlink() or not config.is_file():
        raise _fail("SOURCE_CHANGED", "linked or invalid razr.yaml")
    try:
        value = json.loads(config.read_text(encoding="utf-8"), object_pairs_hook=_json_object)
        if not isinstance(value, dict) or set(value) - {"schema_version", "work_root", "retention", "adapter"}:
            raise ValueError("unsupported config")
        work = value.get("work_root")
        if value.get("schema_version") != 1 or not isinstance(work, str) or not work:
            raise ValueError("invalid config")
        path = PurePosixPath(work)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("unsafe work root")
        current = root
        for part in path.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("linked work root")
        return work
    except (OSError, UnicodeDecodeError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise _fail("SOURCE_CHANGED", "malformed or linked razr.yaml") from exc


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _readback(root: Path, specs: tuple[Any, ...]) -> tuple[ReadbackResult, ...]:
    results = []
    for spec in specs:
        relative = PurePosixPath(spec.path)
        target = root.joinpath(*relative.parts)
        try:
            if target.is_symlink() or not target.resolve().is_relative_to(root) or any(p.is_symlink() for p in (target, *target.parents)):
                raise _fail("READBACK_FAILED", "read-back path is linked", path=spec.path)
            if not target.exists():
                if spec.required:
                    raise _fail("READBACK_FAILED", "required read-back is missing", path=spec.path)
                continue
            if not target.is_file():
                raise _fail("READBACK_FAILED", "read-back is not a file", path=spec.path)
            data = target.read_bytes()
            if len(data) > spec.max_bytes:
                raise _fail("READBACK_FAILED", "read-back exceeds declared limit", path=spec.path)
            parsed = None
            domain = "raw_file"
            digest = raw_file_digest(data)
            if spec.parse_as == "json":
                try:
                    parsed = json.loads(data.decode("utf-8"), object_pairs_hook=lambda pairs: _json_object(pairs))
                    canonical = canonical_json(parsed)
                except (UnicodeDecodeError, json.JSONDecodeError, ProtocolError, ValueError) as exc:
                    raise _fail("MALFORMED_RESPONSE", "read-back JSON is malformed", path=spec.path) from exc
                domain, digest = "canonical_json", raw_file_digest(canonical)
            results.append(ReadbackResult(spec.path, domain, digest, len(data), parsed))
        except ProtocolError:
            raise
        except OSError as exc:
            raise _fail("READBACK_FAILED", "read-back failed", path=spec.path) from exc
    return tuple(results)


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _environment() -> dict[str, str]:
    env = {name: os.environ[name] for name in _ENV_NAMES if name in os.environ}
    env.setdefault("PATH", os.defpath)
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("LC_ALL", "C.UTF-8")
    env.setdefault("LC_CTYPE", "C.UTF-8")
    env.setdefault("PYTHONIOENCODING", "UTF-8")
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
    return env


def _workflow_projection(status_row: object, state: object, work_id: str, node_id: str) -> WorkflowRead:
    if not isinstance(status_row, dict) or set(status_row) != {"id", "stage", "status", "depth", "priority", "approved_at", "candidate", "pull_request"}:
        raise _fail("READBACK_FAILED", "status row fields differ")
    if status_row.get("id") != work_id or not isinstance(status_row.get("stage"), str) or not isinstance(status_row.get("status"), str):
        raise _fail("READBACK_FAILED", "status row identity differs")
    if status_row.get("depth") not in {"tiny", "standard", "complex"} or not isinstance(status_row.get("priority"), int) or isinstance(status_row.get("priority"), bool):
        raise _fail("READBACK_FAILED", "status row domain differs")
    if not isinstance(state, dict) or state.get("id") != work_id or state.get("stage") != status_row["stage"] or state.get("status") != status_row["status"]:
        raise _fail("READBACK_FAILED", "state identity differs")
    generation = state.get("generation")
    nodes = state.get("nodes")
    node = nodes.get(node_id) if isinstance(nodes, dict) else None
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1 or not isinstance(node, dict):
        raise _fail("READBACK_FAILED", "state generation or node is malformed")
    attempt = node.get("attempt")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 0 or node.get("status") not in {"pending", "waiting", "running", "blocked", "complete", "failed"}:
        raise _fail("READBACK_FAILED", "state node is malformed")
    result_ref = node.get("result_ref")
    if result_ref is not None and not isinstance(result_ref, str):
        raise _fail("READBACK_FAILED", "state node result is malformed")
    candidate = state.get("candidate")
    if not isinstance(candidate, dict):
        raise _fail("READBACK_FAILED", "state candidate is malformed")
    values = {key: candidate.get(key) for key in ("repository", "branch", "commit", "source_digest")}
    if any(value is not None and not isinstance(value, str) for value in values.values()):
        raise _fail("READBACK_FAILED", "state candidate is malformed")
    if values["commit"] is not None:
        validate_git_object_id(values["commit"])
    if values["source_digest"] is not None:
        validate_source_digest(values["source_digest"])
    if status_row["depth"] != state.get("depth") or status_row["priority"] != state.get("priority"):
        raise _fail("READBACK_FAILED", "status row and state differ")
    if status_row["candidate"] != values["commit"]:
        raise _fail("READBACK_FAILED", "status candidate differs")
    return WorkflowRead(work_id, state["stage"], state["status"], generation, node_id,
                        node["status"], attempt, result_ref, values["repository"], values["branch"],
                        values["commit"], values["source_digest"], status_row)


def read_workflow(request: CoreRequest, work_id: str, node_id: str) -> WorkflowRead:
    """Perform the frozen status → validate → raw state read-back sequence."""
    import dataclasses
    status_request = dataclasses.replace(request, argv=("status", work_id, "--json"), expected_readback=())
    status_response = invoke_core(status_request)
    try:
        rows = json.loads(status_response.stdout.decode("utf-8"), object_pairs_hook=_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _fail("MALFORMED_RESPONSE", "status JSON is malformed") from exc
    if not isinstance(rows, list) or len(rows) != 1:
        raise _fail("READBACK_FAILED", "status row count differs")
    validate_request = dataclasses.replace(request, argv=("validate", work_id), expected_readback=())
    validation = invoke_core(validate_request)
    try:
        lines = validation.stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise _fail("MALFORMED_RESPONSE", "validation output is not UTF-8") from exc
    if validation.returncode != 0 or lines != [f"PASS {work_id}"]:
        raise _fail("READBACK_FAILED", "core validation did not pass")
    state_specs = tuple(spec for spec in request.expected_readback if spec.path.endswith("/state.yaml") or spec.path == "state.yaml")
    if len(state_specs) != 1 or state_specs[0].parse_as != "json":
        raise _fail("INVALID_REQUEST", "workflow read requires one JSON state read-back")
    state_request = dataclasses.replace(request, argv=("status", work_id, "--json"), expected_readback=state_specs)
    state_response = invoke_core(state_request)
    if len(state_response.readback) != 1:
        raise _fail("READBACK_FAILED", "state read-back missing")
    return _workflow_projection(rows[0], state_response.readback[0].parsed, work_id, node_id)


def invoke_core(request: CoreRequest) -> CoreResponse:
    root, package, runtime = _validate_request(request)
    source = _source_digest(root, request.expected_commit)
    validate_source_digest(request.expected_source_digest)
    if source != request.expected_source_digest:
        raise _fail("SOURCE_CHANGED", "source digest differs")
    command = [str(request.python_executable), "-B", str(runtime), *request.argv]
    started = time.monotonic()
    try:
        process = subprocess.Popen(command, cwd=root, env=_environment(), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                                   start_new_session=True)
    except OSError as exc:
        raise _fail("INTERNAL", "core launch failed") from exc
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": request.max_stdout_bytes, "stderr": request.max_stderr_bytes}
    timed_out = False
    exceeded: str | None = None
    while selector.get_map() or process.poll() is None:
        remaining = request.timeout_ms / 1000 - (time.monotonic() - started)
        if remaining <= 0:
            timed_out = True
            break
        for key, _ in selector.select(min(remaining, 0.05)):
            chunk = key.fileobj.read1(65536)
            if not chunk:
                selector.unregister(key.fileobj)
                key.fileobj.close()
                continue
            name = key.data
            buffers[name].extend(chunk)
            if len(buffers[name]) > limits[name]:
                exceeded = name
                break
        if exceeded:
            break
    if timed_out or exceeded or process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=request.termination_grace_ms / 1000)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except ProcessLookupError:
            pass
    else:
        process.wait()
    for stream in (process.stdout, process.stderr):
        try:
            stream.close()
        except OSError:
            pass
    if exceeded:
        raise _fail("OUTPUT_LIMIT", f"{exceeded} exceeds output limit")
    if timed_out:
        raise _fail("TIMEOUT", "core invocation timed out")
    if process.returncode < 0:
        raise _fail("INTERRUPTED", "core was terminated", signal=-process.returncode)
    if process.returncode != 0:
        raise _fail("CORE_EXIT", "core exited unsuccessfully", returncode=process.returncode)
    readback = _readback(root, request.expected_readback)
    return CoreResponse(request.request_id, process.returncode, bytes(buffers["stdout"]),
                        bytes(buffers["stderr"]), int((time.monotonic() - started) * 1000),
                        request.expected_core_identity, request.expected_provenance, source, readback)