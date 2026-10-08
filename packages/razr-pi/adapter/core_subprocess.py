"""The sole subprocess boundary to the frozen Razr Python core."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import selectors
import signal
import subprocess
import time
from typing import Any

from .protocol import (
    CoreRequest, CoreResponse, ProtocolError, ProvenancePin, ReadbackResult,
    canonical_json, raw_file_digest, validate_source_digest,
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
    if not isinstance(request.request_id, str) or not __import__("re").fullmatch(
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
    if request.expected_core_identity != raw_file_digest((package / "runtime/provenance.json").read_bytes()):
        raise _fail("PROVENANCE_MISMATCH", "provenance identity differs")
    pins = {pin.path: pin for pin in request.expected_provenance}
    if set(pins) != set(("scripts/razr.py", "templates/evidence.md", "templates/executable.yaml",
                         "templates/razr.yaml", "templates/spec.md", "templates/state.yaml")):
        raise _fail("PROVENANCE_MISMATCH", "incomplete provenance pins")
    declaration = json.loads((package / "runtime/provenance.json").read_bytes())
    rows = {row["path"]: row["sha256"] for row in declaration.get("files", [])}
    for relative, pin in pins.items():
        path = package / "runtime" / relative
        if not path.is_file() or raw_file_digest(path.read_bytes()) != "sha256:" + pin.sha256:
            raise _fail("PROVENANCE_MISMATCH", "pinned runtime file differs", path=relative)
        if rows.get(relative) != pin.sha256:
            raise _fail("PROVENANCE_MISMATCH", "provenance declaration differs", path=relative)
    return root, package, runtime


def _source_digest(root: Path) -> str:
    try:
        result = subprocess.run(["git", "ls-tree", "-r", "-z", "--full-tree", "HEAD"],
                                cwd=root, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _fail("SOURCE_CHANGED", "cannot inspect source tree") from exc
    if result.returncode != 0:
        raise _fail("SOURCE_CHANGED", "source tree inspection failed")
    # The core's configured work root is excluded from source identity.
    work_root = ".razr/work"
    config = root / "razr.yaml"
    if config.is_file():
        try:
            value = json.loads(config.read_bytes()).get("work_root")
            if isinstance(value, str) and value:
                work_root = value
        except (ValueError, UnicodeDecodeError):
            pass
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


def invoke_core(request: CoreRequest) -> CoreResponse:
    root, package, runtime = _validate_request(request)
    source = _source_digest(root)
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