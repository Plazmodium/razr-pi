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
    CoreRequest, CoreResponse, ProtocolError, ProvenancePin, ReadbackResult, ReadbackSpec, WorkflowRead,
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
    expected_runtime = package / "runtime/scripts/razr.py"
    if not expected_runtime.is_file() or expected_runtime.is_symlink():
        raise _fail("PROVENANCE_MISMATCH", "pinned core entrypoint is unavailable")
    if runtime != expected_runtime:
        raise _fail("INVALID_REQUEST", "runtime script is not the pinned core entrypoint")
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
        pin_paths = [pin.path for pin in request.expected_provenance]
        if len(pin_paths) != len(set(pin_paths)):
            raise _fail("PROVENANCE_MISMATCH", "duplicate provenance pin path")
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
            if rows.get(relative) != pin.sha256:
                raise _fail("PROVENANCE_MISMATCH", "provenance declaration differs", path=relative)
            path = package / "runtime" / relative
            if not path.is_file() or path.is_symlink() or raw_file_digest(path.read_bytes()) != "sha256:" + pin.sha256:
                raise _fail("PROVENANCE_MISMATCH", "pinned runtime file differs", path=relative)
        if raw_file_digest(runtime.read_bytes()) != "sha256:" + pins["scripts/razr.py"].sha256:
            raise _fail("PROVENANCE_MISMATCH", "runtime entrypoint bytes differ")
    except ProtocolError:
        raise
    except (OSError, UnicodeDecodeError, UnicodeError, json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
        raise _fail("PROVENANCE_MISMATCH", "provenance declaration unavailable") from exc
    validate_git_object_id(request.expected_commit)
    _verify_git_identity(root)
    if not _git_commit_exists(root, request.expected_commit):
        raise _fail("IDENTITY_MISMATCH", "expected commit is not present")
    committed_work_root = _core_work_root(root, request.expected_commit)
    if _git_head(root) != request.expected_commit:
        raise _fail("SOURCE_CHANGED", "repository HEAD differs from expected commit")
    _reject_dirty_checkout(root, committed_work_root)
    return root, package, runtime


def _verify_git_identity(root: Path) -> None:
    git_entry = root / ".git"
    if not git_entry.exists() or git_entry.is_symlink() or not (git_entry.is_dir() or git_entry.is_file()):
        raise _fail("IDENTITY_MISMATCH", "ambiguous or missing Git administration")
    administrative = git_entry
    if git_entry.is_file():
        try:
            raw = git_entry.read_bytes()
            text = raw.decode("utf-8")
            if not text.startswith("gitdir: ") or text.count("\n") != 1 or not text.endswith("\n"):
                raise ValueError("malformed linked worktree pointer")
            pointer = Path(text[8:-1])
            if not pointer.is_absolute():
                pointer = (root / pointer).resolve()
            if ".." in Path(text[8:-1]).parts or pointer.is_symlink() or not pointer.is_dir():
                raise ValueError("unsafe linked worktree pointer")
            common_guess = pointer.parent.parent
            if pointer.parent.name != "worktrees" or common_guess.name != ".git":
                raise ValueError("external linked worktree")
            administrative = pointer
            if (pointer / "commondir").read_text(encoding="utf-8") != "../..\n":
                raise ValueError("invalid linked commondir")
            backlink = (pointer / "gitdir").read_text(encoding="utf-8")
            if backlink != str(git_entry) + "\n":
                raise ValueError("invalid linked backlink")
        except (OSError, UnicodeError, ValueError) as exc:
            raise _fail("IDENTITY_MISMATCH", "ambiguous linked Git administration") from exc
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
    if (not git_dir.is_absolute() or not common_dir.is_absolute() or git_dir.resolve() != administrative.resolve()
            or git_dir.is_symlink() or common_dir.is_symlink() or not git_dir.is_dir() or not common_dir.is_dir()):
        raise _fail("IDENTITY_MISMATCH", "ambiguous Git administration")
    try:
        listed = subprocess.run(["git", "-C", str(root), "worktree", "list", "--porcelain", "-z"],
                                env=env, cwd=root, capture_output=True, check=False, timeout=10)
        if listed.returncode or listed.stderr:
            raise ValueError("worktree query failed")
        locations = []
        for block in listed.stdout.split(b"\0\0"):
            if not block:
                continue
            fields = block.split(b"\0")
            if any(field.startswith((b"bare", b"prunable")) for field in fields):
                raise ValueError("ambiguous/prunable worktree")
            if not fields or not fields[0].startswith(b"worktree "):
                raise ValueError("malformed worktree registration")
            locations.append(fields[0][9:].decode("utf-8"))
        if len(locations) != len(set(locations)) or locations.count(str(root)) != 1 or locations.count(str(common_dir.parent)) != 1:
            raise ValueError("ambiguous worktree registration")
    except (OSError, UnicodeError, subprocess.TimeoutExpired, ValueError) as exc:
        raise _fail("IDENTITY_MISMATCH", "ambiguous Git worktree registration") from exc


def _git_head(root: Path) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
                                env=_environment(), cwd=root, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _fail("IDENTITY_MISMATCH", "Git HEAD query unavailable") from exc
    head = result.stdout.decode("ascii", "ignore").strip()
    if result.returncode or result.stderr or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise _fail("IDENTITY_MISMATCH", "Git HEAD is unavailable or ambiguous")
    return head


def _reject_dirty_checkout(root: Path, work_root: str) -> None:
    env = _environment()
    try:
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                                env=env, cwd=root, capture_output=True, check=False, timeout=10)
        untracked = subprocess.run(["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
                                   env=env, cwd=root, capture_output=True, check=False, timeout=10)
        ignored = subprocess.run(["git", "-C", str(root), "ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
                                 env=env, cwd=root, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _fail("IDENTITY_MISMATCH", "Git dirtiness query unavailable") from exc
    if any(result.returncode or result.stderr for result in (status, untracked, ignored)):
        raise _fail("IDENTITY_MISMATCH", "Git dirtiness query failed")
    # The only owned private output/cache policy is the configured work_root.
    # No other ignored, untracked, source, or configuration path is owned.
    prefix = work_root.rstrip("/") + "/"
    paths: list[bytes] = []
    for entry in status.stdout.split(b"\0"):
        if entry and entry[:2] != b"??":
            paths.append(entry[3:])
    paths.extend(path for path in untracked.stdout.split(b"\0") if path)
    paths.extend(path for path in ignored.stdout.split(b"\0") if path)
    for raw_path in paths:
        try:
            path = raw_path.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _fail("IDENTITY_MISMATCH", "Git returned an invalid dirty path") from exc
        if not path.startswith(prefix):
            raise _fail("SOURCE_CHANGED", "checkout has dirty or untracked path", path=path)


def verify_candidate_identity(root: Path, *, branch: str, commit: str, source_digest: str) -> None:
    """Verify checkout identity; callers remain responsible for candidate read-back/state authority."""
    root = _canonical(root, directory=True)
    if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", branch) or ".." in PurePosixPath(branch).parts:
        raise _fail("INVALID_REQUEST", "invalid candidate branch")
    validate_git_object_id(commit)
    _verify_git_identity(root)
    if not _git_commit_exists(root, commit):
        raise _fail("IDENTITY_MISMATCH", "expected commit is not present")
    if _git_head(root) != commit:
        raise _fail("SOURCE_CHANGED", "repository HEAD differs from expected commit")
    _reject_dirty_checkout(root, _core_work_root(root, commit))
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", f"refs/heads/{branch}"], env=_environment(), cwd=root, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _fail("IDENTITY_MISMATCH", "Git branch query unavailable") from exc
    actual_commit = result.stdout.decode("ascii", "ignore").strip()
    if result.returncode or result.stderr or actual_commit != commit:
        raise _fail("CANDIDATE_CHANGED", "candidate branch does not point to commit")
    if _source_digest(root, commit) != source_digest:
        raise _fail("CANDIDATE_CHANGED", "worktree source digest differs")


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
    # Match core load_json/validate_config/safe_work_root from the requested tree.
    work_root = _core_work_root(root, commit)
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


def _extract_work_root(value: object) -> str:
    """Transport-only config parsing; core remains the semantic config authority."""
    if not isinstance(value, dict) or "work_root" not in value or not isinstance(value["work_root"], str) or not value["work_root"]:
        raise ValueError("work_root is unavailable")
    work = value["work_root"]
    path = PurePosixPath(work)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("unsafe work root")
    return work


def _core_work_root(root: Path, commit: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), "show", f"{commit}:razr.yaml"],
                                env=_environment(), cwd=root, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _fail("SOURCE_CHANGED", "committed razr.yaml unavailable") from exc
    if result.returncode != 0 or not result.stdout or result.stderr:
        raise _fail("SOURCE_CHANGED", "committed razr.yaml is missing")
    committed = result.stdout
    live = root / "razr.yaml"
    if not live.exists() or live.is_symlink() or not live.is_file():
        raise _fail("SOURCE_CHANGED", "live razr.yaml is missing or linked")
    try:
        live_bytes = live.read_bytes()
        if live_bytes != committed:
            raise _fail("SOURCE_CHANGED", "live razr.yaml differs from commit")
        committed_value = json.loads(committed.decode("utf-8"), object_pairs_hook=_json_object)
        live_value = json.loads(live_bytes.decode("utf-8"), object_pairs_hook=_json_object)
        committed_work = _extract_work_root(committed_value)
        live_work = _extract_work_root(live_value)
        if committed_value != live_value or committed_work != live_work:
            raise _fail("SOURCE_CHANGED", "live razr.yaml semantics differ from commit")
        return committed_work
    except ProtocolError:
        raise
    except (OSError, UnicodeDecodeError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise _fail("SOURCE_CHANGED", "malformed committed or live razr.yaml") from exc


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
            results.append(ReadbackResult(spec.path, domain, digest, len(data), parsed, data))
        except ProtocolError:
            raise
        except OSError as exc:
            raise _fail("READBACK_FAILED", "read-back failed", path=spec.path) from exc
    return tuple(results)


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


def _child_request_id(parent: str, suffix: str) -> str:
    if not isinstance(parent, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", parent):
        raise _fail("INVALID_REQUEST", "invalid parent request ID")
    value = parent[:128 - len(suffix) - 1] + "-" + suffix
    return value


def _require_response_id(response: CoreResponse, expected: str) -> None:
    if response.request_id != expected:
        raise _fail("IDENTITY_MISMATCH", "core response correlation differs")


def _nullable_string(value: object) -> str | None:
    if value is not None and not isinstance(value, str):
        raise _fail("READBACK_FAILED", "nullable string field is malformed")
    return value


def _workflow_projection(status_row: object, state: object, work_id: str, node_id: str,
                         raw: ReadbackResult, response: CoreResponse, root: Path, correlation_id: str) -> WorkflowRead:
    row_fields = {"id", "stage", "status", "depth", "priority", "approved_at", "candidate", "pull_request"}
    if not isinstance(status_row, dict) or set(status_row) != row_fields:
        raise _fail("READBACK_FAILED", "status row fields differ")
    if status_row["id"] != work_id or status_row["stage"] not in {"spec", "build", "verify", "review"} or status_row["status"] not in {"queued", "active", "blocked", "complete", "cancelled"}:
        raise _fail("READBACK_FAILED", "status row identity/domain differs")
    if status_row["depth"] not in {"tiny", "standard", "complex"} or not isinstance(status_row["priority"], int) or isinstance(status_row["priority"], bool) or not 0 <= status_row["priority"] <= 1000:
        raise _fail("READBACK_FAILED", "status row domain differs")
    _nullable_string(status_row["approved_at"])
    _nullable_string(status_row["candidate"])
    if status_row["candidate"] is not None:
        try:
            validate_git_object_id(status_row["candidate"])
        except ProtocolError as exc:
            raise _fail("READBACK_FAILED", "core status candidate identity is malformed") from exc
    _nullable_string(status_row["pull_request"])
    if not isinstance(state, dict):
        raise _fail("READBACK_FAILED", "state is not an object")
    required = {"id", "stage", "status", "generation", "depth", "priority", "approved_at", "nodes", "candidate", "delivery"}
    if not required <= set(state) or state["id"] != work_id:
        raise _fail("READBACK_FAILED", "state projection fields differ")
    if state["stage"] != status_row["stage"] or state["status"] != status_row["status"] or state["depth"] != status_row["depth"] or state["priority"] != status_row["priority"]:
        raise _fail("READBACK_FAILED", "state and status row differ")
    if state["approved_at"] != status_row["approved_at"]:
        raise _fail("READBACK_FAILED", "approval fields differ")
    if not isinstance(state["generation"], int) or isinstance(state["generation"], bool) or state["generation"] < 1:
        raise _fail("READBACK_FAILED", "state generation domain differs")
    _nullable_string(state["approved_at"])
    candidate = state["candidate"]
    if not isinstance(candidate, dict) or set(candidate) != {"repository", "branch", "commit", "source_digest"}:
        raise _fail("READBACK_FAILED", "state candidate fields differ")
    values = {key: candidate[key] for key in candidate}
    for key in ("repository", "branch", "commit", "source_digest"):
        _nullable_string(values[key])
    try:
        if values["commit"] is not None:
            validate_git_object_id(values["commit"])
        if values["source_digest"] is not None:
            validate_source_digest(values["source_digest"])
    except ProtocolError as exc:
        raise _fail("READBACK_FAILED", "core candidate identity is malformed") from exc
    if status_row["candidate"] != values["commit"]:
        raise _fail("READBACK_FAILED", "status candidate differs")
    delivery = state["delivery"]
    if not isinstance(delivery, dict) or "pull_request" not in delivery or delivery["pull_request"] != status_row["pull_request"]:
        raise _fail("READBACK_FAILED", "status pull request differs")
    nodes = state["nodes"]
    node = nodes.get(node_id) if isinstance(nodes, dict) else None
    if not isinstance(node, dict) or set(node) != {"status", "attempt", "result_ref"}:
        raise _fail("READBACK_FAILED", "state node fields differ")
    if node["status"] not in {"pending", "waiting", "running", "blocked", "complete", "failed"} or not isinstance(node["attempt"], int) or isinstance(node["attempt"], bool) or node["attempt"] < 0:
        raise _fail("READBACK_FAILED", "state node domain differs")
    result_ref = _nullable_string(node["result_ref"])
    return WorkflowRead(correlation_id, work_id, state["stage"], state["status"], state["generation"], node_id,
                        node["status"], node["attempt"], result_ref, values["repository"], values["branch"],
                        values["commit"], values["source_digest"], status_row, raw.path, raw.digest,
                        raw.bytes, root, response.core_identity, response.source_digest, response.provenance)


def read_workflow(request: CoreRequest, work_id: str, node_id: str) -> WorkflowRead:
    """Perform the frozen status → validate → raw state read-back sequence."""
    import dataclasses
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", work_id) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", node_id):
        raise _fail("INVALID_REQUEST", "invalid core work or node ID")
    status_id = _child_request_id(request.request_id, "status")
    validate_id = _child_request_id(request.request_id, "validate")
    state_id = _child_request_id(request.request_id, "state")
    status_request = dataclasses.replace(request, request_id=status_id, argv=("status", work_id, "--json"), expected_readback=())
    status_response = invoke_core(status_request)
    _require_response_id(status_response, status_id)
    try:
        rows = json.loads(status_response.stdout.decode("utf-8"), object_pairs_hook=_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _fail("MALFORMED_RESPONSE", "status JSON is malformed") from exc
    if not isinstance(rows, list) or len(rows) != 1:
        raise _fail("READBACK_FAILED", "status row count differs")
    validate_request = dataclasses.replace(request, request_id=validate_id, argv=("validate", work_id), expected_readback=())
    validation = invoke_core(validate_request)
    _require_response_id(validation, validate_id)
    try:
        lines = validation.stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise _fail("MALFORMED_RESPONSE", "validation output is not UTF-8") from exc
    if validation.returncode != 0 or lines != [f"PASS {work_id}"]:
        raise _fail("READBACK_FAILED", "core validation did not pass")
    root = _canonical(request.repository_root, directory=True)
    work_root = _core_work_root(root, request.expected_commit)
    state_path = f"{work_root}/{work_id}/state.yaml"
    state_specs = (ReadbackSpec(state_path, True, 1_048_576, "bytes"),)
    state_request = dataclasses.replace(request, request_id=state_id, argv=("status", work_id, "--json"), expected_readback=state_specs)
    state_response = invoke_core(state_request)
    _require_response_id(state_response, state_id)
    if len(state_response.readback) != 1 or state_response.readback[0].digest_domain != "raw_file":
        raise _fail("READBACK_FAILED", "state raw read-back missing")
    raw_state = state_response.readback[0]
    try:
        state = json.loads(raw_state.raw_bytes.decode("utf-8"), object_pairs_hook=_json_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _fail("MALFORMED_RESPONSE", "state JSON is malformed") from exc
    return _workflow_projection(rows[0], state, work_id, node_id, raw_state, state_response, request.repository_root, request.request_id)


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