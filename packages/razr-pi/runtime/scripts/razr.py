#!/usr/bin/env python3
"""Razr deterministic workflow harness."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, BinaryIO

if os.name == "nt":
    import msvcrt
else:
    import fcntl

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CONFIG_NAME = "razr.yaml"
START_MARKER = "<!-- RAZR:START -->"
END_MARKER = "<!-- RAZR:END -->"
DEFAULT_CONFIG: dict[str, Any] = json.loads((PACKAGE_ROOT / "templates" / "razr.yaml").read_text(encoding="utf-8"))
WORK_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NORMATIVE_ID_RE = re.compile(r"^\s*(?:[-*]\s*)?((?:REQ|CON|NG|IMP|VER)-\d{3,})\b", re.MULTILINE)
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SOURCE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_RE = re.compile(r"^(?!/)(?!.*(?:\.\.|//|@\{|\\\\))[A-Za-z0-9._/-]+(?<![/.])$")
PROVENANCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{2,127}$")
STAGES = {"spec", "build", "verify", "review"}
STATUSES = {"queued", "active", "blocked", "complete", "cancelled"}
CONTRACT_TYPES = {"requirement", "constraint", "non_goal", "verification", "implementation"}
EXECUTABLE_FIELDS = {
    "schema_version", "id", "status", "source_spec", "objective", "contract",
    "nodes", "trajectory", "critique", "human_gates",
}
NODE_FIELDS = {"id", "role", "depends_on", "contract_ids", "validation"}
NODE_ROLES = {"guide", "worker", "integrator", "specialist", "presenter"}
REQUIRED_HUMAN_GATES = {"exact specification approval", "final PR review and merge"}
LEGAL_TRANSITIONS = {
    "spec": {"build"},
    "build": {"verify", "spec"},
    "verify": {"build", "spec", "review"},
    "review": {"build", "spec"},
}


class RazrError(RuntimeError):
    pass


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def git_root(cwd: Path | None = None) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd or Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RazrError("Razr requires a Git repository.")
    return Path(result.stdout.strip()).resolve()


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def transactional_write(contents: dict[Path, str], guard: StateLock | None = None) -> None:
    """Roll back caught exceptions; no crash-atomic multi-file commit or journal."""
    staged: dict[Path, Path] = {}
    prior: dict[Path, bytes | None] = {}
    replaced: list[Path] = []
    try:
        for path, content in contents.items():
            if path.is_symlink():
                raise RazrError(f"refusing transactional write through symlink: {path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            prior[path] = path.read_bytes() if path.exists() else None
            fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            staged[path] = Path(temporary)
        for path in sorted(contents, key=lambda item: item.name):
            if guard is not None:
                guard.assert_owned()
            os.replace(staged[path], path)
            replaced.append(path)
    except Exception:
        for path in reversed(replaced):
            previous = prior[path]
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, previous.decode("utf-8"))
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


class StateLock:
    def __init__(self, path: Path, token: str, handle: BinaryIO):
        self.path = path
        self.token = token
        self.handle = handle

    def assert_owned(self) -> None:
        if self.handle.closed or self.path.is_symlink():
            raise RazrError(f"state lock ownership lost: {self.path}")
        try:
            held_stat = os.fstat(self.handle.fileno())
            path_stat = os.stat(self.path, follow_symlinks=False)
        except OSError as exc:
            raise RazrError(f"state lock path is unavailable: {self.path}") from exc
        if not os.path.samestat(held_stat, path_stat):
            raise RazrError(f"state lock path no longer names the held lock inode: {self.path}")
        self.handle.seek(0)
        current = self.handle.read().decode("utf-8").strip("\x00\n ")
        if current != self.token:
            raise RazrError(f"state lock ownership lost: {self.path}")

    def release(self) -> None:
        if self.handle.closed:
            return
        try:
            if os.name == "nt":
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()


def try_advisory_lock(handle: BinaryIO) -> bool:
    try:
        if os.name == "nt":
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\x00")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (BlockingIOError, PermissionError, OSError):
        return False


@contextlib.contextmanager
def state_lock(folder: Path, timeout: float = 10.0):
    if folder.is_symlink():
        raise RazrError(f"state lock folder must not be a symlink: {folder}")
    folder.mkdir(parents=True, exist_ok=True)
    lock_path = folder / ".state.lock"
    if lock_path.is_symlink():
        raise RazrError(f"state lock must not be a symlink: {lock_path}")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise RazrError(f"unable to open state lock safely: {lock_path}: {exc}") from exc
    handle = os.fdopen(descriptor, "r+b", buffering=0)
    deadline = time.monotonic() + timeout
    while not try_advisory_lock(handle):
        if time.monotonic() >= deadline:
            handle.close()
            raise RazrError(f"timed out waiting for state mutation lock: {folder.name}")
        time.sleep(0.05)
    token = f"{os.getpid()}:{now_iso()}"
    handle.seek(0)
    handle.truncate()
    handle.write((token + "\n").encode("utf-8"))
    handle.flush()
    os.fsync(handle.fileno())
    guard = StateLock(lock_path, token, handle)
    try:
        yield guard
    finally:
        guard.release()


def safe_work_root(repo: Path, value: str) -> Path:
    raw = Path(value)
    if raw.is_absolute():
        raise RazrError("work_root must be repository-relative.")
    if not value or any(part in {"", ".", ".."} for part in raw.parts):
        raise RazrError("work_root must be a clean repository-relative path.")
    candidate = repo / raw
    current = repo
    for part in raw.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise RazrError(f"work_root path must not contain symlinks: {current}")
    resolved_parent = candidate.parent.resolve()
    if not resolved_parent.is_relative_to(repo):
        raise RazrError("work_root escapes the repository.")
    return candidate


def load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise RazrError(f"Refusing symlinked JSON/YAML file: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RazrError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RazrError(f"{path} must use JSON-compatible YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RazrError(f"{path} must contain an object.")
    return data


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"", "null", "~"}:
        return None
    if value in {"true", "false"}:
        return value == "true"
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_spec(path: Path) -> tuple[dict[str, Any], str]:
    if path.is_symlink():
        raise RazrError(f"Refusing symlinked specification: {path}")
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---\n"):
        raise RazrError(f"{path} must start with flat YAML frontmatter.")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise RazrError(f"{path} has no closing frontmatter delimiter.")
    metadata: dict[str, Any] = {}
    for number, line in enumerate(text[4:end].splitlines(), start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) or ":" not in line:
            raise RazrError(f"{path}:{number}: spec frontmatter supports flat fields only.")
        key, value = line.split(":", 1)
        normalized_key = key.strip()
        if normalized_key in metadata:
            raise RazrError(f"{path}:{number}: duplicate spec frontmatter field: {normalized_key}")
        metadata[normalized_key] = parse_scalar(value)
    return metadata, text[end + 5 :]


SPEC_REQUIRED_SECTIONS = (
    "Intent",
    "Requirements",
    "Constraints",
    "Non-goals",
    "Assumptions",
    "Proof obligations",
    "Approval",
)
SPEC_FRONTMATTER_FIELDS = {
    "id", "condition", "revision", "depth", "priority", "created_at",
    "approved_by", "approved_at", "approval_ref",
}


def missing_spec_sections(body: str) -> list[str]:
    headings = {
        match.group(1).strip()
        for match in re.finditer(r"^##\s+(.+?)\s*$", body, flags=re.MULTILINE)
    }
    return [section for section in SPEC_REQUIRED_SECTIONS if section not in headings]


def spec_digest(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def validate_config(repo: Path, config: dict[str, Any]) -> Path:
    supported_top = {"schema_version", "work_root", "retention", "adapter"}
    unsupported_top = sorted(set(config) - supported_top)
    if unsupported_top:
        raise RazrError("razr.yaml contains unsupported fields: " + ", ".join(unsupported_top))
    if config.get("schema_version") != 1:
        raise RazrError("razr.yaml schema_version must be 1.")
    if config.get("retention") != "commit":
        raise RazrError("Razr currently requires retention 'commit'.")
    work_root = config.get("work_root")
    if not isinstance(work_root, str):
        raise RazrError("razr.yaml work_root must be a string.")
    adapter = config.get("adapter")
    if not isinstance(adapter, dict):
        raise RazrError("razr.yaml requires an adapter object.")
    supported_adapter = {"mode", "max_active_work_items", "queue_order"}
    unsupported_adapter = sorted(set(adapter) - supported_adapter)
    if unsupported_adapter:
        raise RazrError("adapter contains unsupported fields: " + ", ".join(unsupported_adapter))
    if adapter.get("mode") != "process-local":
        raise RazrError("adapter.mode must be process-local in Razr version one.")
    if adapter.get("max_active_work_items") != 5:
        raise RazrError("adapter.max_active_work_items must be exactly 5 in Razr version one.")
    if adapter.get("queue_order") != ["priority", "approved_at", "id"]:
        raise RazrError("adapter.queue_order must be ['priority', 'approved_at', 'id'].")
    return safe_work_root(repo, work_root)


def project_context() -> tuple[Path, dict[str, Any], Path]:
    repo = git_root()
    config = load_json(repo / CONFIG_NAME)
    return repo, config, validate_config(repo, config)


def work_item_dir(work_root: Path, work_id: str) -> Path:
    if not WORK_ID_RE.fullmatch(work_id):
        raise RazrError("work item id must be lowercase kebab-case.")
    if work_root.is_symlink():
        raise RazrError(f"work root must not be a symlink: {work_root}")
    base = work_root.resolve()
    candidate = base / work_id
    if candidate.exists() and (candidate.is_symlink() or candidate.resolve().parent != base):
        raise RazrError(f"unsafe work item path: {candidate}")
    return candidate


def iter_work_item_dirs(work_root: Path) -> list[Path]:
    if work_root.is_symlink():
        raise RazrError(f"work root must not be a symlink: {work_root}")
    if not work_root.exists():
        return []
    folders: list[Path] = []
    base = work_root.resolve()
    for path in work_root.iterdir():
        if path.is_symlink():
            raise RazrError(f"work item entry must not be a symlink: {path}")
        if not path.is_dir():
            continue
        if not WORK_ID_RE.fullmatch(path.name):
            raise RazrError(f"invalid work item directory name: {path.name}")
        if path.resolve().parent != base:
            raise RazrError(f"work item escapes work root: {path}")
        folders.append(path)
    return sorted(folders, key=lambda item: item.name)


def reject_canonical_symlinks(folder: Path, *, include_materialized: bool) -> None:
    names = {"spec.md", "executable.yaml"}
    if include_materialized:
        names.update({"state.yaml", "evidence.md"})
    for name in names:
        path = folder / name
        if path.is_symlink():
            raise RazrError(f"canonical file must not be a symlink: {path}")


def agents_block() -> str:
    source = PACKAGE_ROOT / "AGENTS.project-razr.md"
    text = source.read_text(encoding="utf-8").strip()
    if not text.startswith(START_MARKER) or not text.endswith(END_MARKER):
        raise RazrError("Packaged AGENTS Razr block is malformed.")
    return text


def merge_agents(path: Path, block: str) -> str:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    start = existing.find(START_MARKER)
    end = existing.find(END_MARKER)
    if (start >= 0) != (end >= 0):
        raise RazrError("AGENTS.md contains an incomplete Razr marker block.")
    if start >= 0:
        end += len(END_MARKER)
        return existing[:start] + block + existing[end:]
    if existing and not existing.endswith("\n"):
        existing += "\n"
    separator = "\n" if existing else ""
    return existing + separator + block + "\n"


def command_init(args: argparse.Namespace) -> None:
    repo = git_root()
    config_path = repo / CONFIG_NAME
    config_existed = config_path.exists()
    if config_existed:
        config = load_json(config_path)
        existing_root = config.get("work_root")
        if args.work_root and args.work_root != existing_root:
            raise RazrError(
                f"razr.yaml already uses work_root {existing_root!r}; refusing silent change."
            )
    else:
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        if args.work_root:
            config["work_root"] = args.work_root
    work_root = validate_config(repo, config)
    agents_path = repo / "AGENTS.md"
    agents_existed = agents_path.exists()
    prior_agents = agents_path.read_bytes() if agents_existed else None
    merged = merge_agents(agents_path, agents_block())
    current = prior_agents.decode("utf-8") if prior_agents is not None else None
    work_root_existed = work_root.exists()
    try:
        if not config_existed:
            atomic_write_json(config_path, config)
        work_root.mkdir(parents=True, exist_ok=True)
        if current != merged:
            atomic_write(agents_path, merged)
    except Exception:
        if not config_existed:
            config_path.unlink(missing_ok=True)
        if not work_root_existed and work_root.exists():
            shutil.rmtree(work_root)
            parent = work_root.parent
            if parent != repo and parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        if agents_existed and prior_agents is not None:
            atomic_write(agents_path, prior_agents.decode("utf-8"))
        elif not agents_existed:
            agents_path.unlink(missing_ok=True)
        raise
    print(f"Razr initialized: {repo}")
    print(f"Work root: {config['work_root']}")


def validate_executable(
    folder: Path,
    work_id: str,
    spec: dict[str, Any],
    spec_body: str,
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    path = folder / "executable.yaml"
    try:
        executable = load_json(path)
    except RazrError as exc:
        return {}, [str(exc)]
    unsupported = sorted(set(executable) - EXECUTABLE_FIELDS)
    if unsupported:
        errors.append("executable contains unsupported fields: " + ", ".join(unsupported))
    if executable.get("schema_version") != 1:
        errors.append("executable.yaml schema_version must be 1.")
    if executable.get("id") != work_id:
        errors.append("executable id does not match work item.")
    if executable.get("status") != "compiled":
        errors.append("executable status must be compiled.")
    if not isinstance(executable.get("objective"), str) or not executable["objective"].strip():
        errors.append("executable objective must be a non-empty string.")
    source = executable.get("source_spec") if isinstance(executable.get("source_spec"), dict) else {}
    if set(source) != {"revision", "digest"}:
        errors.append("executable source_spec must define only revision and digest.")
    if source.get("revision") != spec.get("revision"):
        errors.append("executable source revision does not match approved spec.")
    if source.get("digest") != spec_digest(folder / "spec.md"):
        errors.append("executable source digest does not match spec.md.")
    contract = executable.get("contract")
    if not isinstance(contract, list) or not contract:
        errors.append("executable contract must be a non-empty list.")
        contract = []
    contract_ids: list[str] = []
    non_verification: set[str] = set()
    proof_coverage: set[str] = set()
    for index, item in enumerate(contract):
        if not isinstance(item, dict):
            errors.append(f"contract item {index} must be an object.")
            continue
        item_id = item.get("id")
        item_type = item.get("type")
        allowed_item_fields = {"id", "type", "statement", "satisfies"} if item_type == "verification" else {"id", "type", "statement"}
        unsupported_item = sorted(set(item) - allowed_item_fields)
        if unsupported_item:
            errors.append(f"contract item {item_id or index} contains unsupported fields: {', '.join(unsupported_item)}")
        if not isinstance(item_id, str):
            errors.append(f"contract item {index} requires a string id.")
            continue
        contract_ids.append(item_id)
        if not isinstance(item_type, str) or item_type not in CONTRACT_TYPES:
            errors.append(f"contract item {item_id} has invalid type {item_type!r}.")
        if not isinstance(item.get("statement"), str) or not item["statement"].strip():
            errors.append(f"contract item {item_id} requires a statement.")
        if item_type == "verification":
            satisfies = item.get("satisfies")
            if not isinstance(satisfies, list) or not satisfies or any(not isinstance(link, str) for link in satisfies):
                errors.append(f"verification item {item_id} requires non-empty string satisfies links.")
            else:
                proof_coverage.update(satisfies)
        else:
            non_verification.add(item_id)
    duplicates = sorted({item_id for item_id in contract_ids if contract_ids.count(item_id) > 1})
    if duplicates:
        errors.append("duplicate contract ids: " + ", ".join(duplicates))
    spec_ids = set(NORMATIVE_ID_RE.findall(spec_body))
    compiled_ids = set(contract_ids)
    if spec_ids != compiled_ids:
        omitted = sorted(spec_ids - compiled_ids)
        added = sorted(compiled_ids - spec_ids)
        if omitted:
            errors.append("normative ids omitted from executable: " + ", ".join(omitted))
        if added:
            errors.append("executable ids absent from spec: " + ", ".join(added))
    for item in contract:
        if isinstance(item, dict) and item.get("type") == "verification":
            satisfies = item.get("satisfies")
            if isinstance(satisfies, list) and all(isinstance(link, str) for link in satisfies):
                unknown = sorted(set(satisfies) - non_verification)
                if unknown:
                    errors.append(f"verification item {item.get('id')} links unknown ids: {', '.join(unknown)}")
    uncovered = sorted(non_verification - proof_coverage)
    if uncovered:
        errors.append("contract ids missing proof coverage: " + ", ".join(uncovered))

    nodes = executable.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        errors.append("executable nodes must be a non-empty list.")
        nodes = []
    node_ids: list[str] = []
    dependencies: dict[str, list[str]] = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"node {index} must be an object.")
            continue
        node_id = node.get("id")
        unsupported_node = sorted(set(node) - NODE_FIELDS)
        if unsupported_node:
            errors.append(f"node {node_id or index} contains unsupported fields: {', '.join(unsupported_node)}")
        if not isinstance(node_id, str) or not WORK_ID_RE.fullmatch(node_id):
            errors.append(f"node {index} requires a lowercase kebab-case id.")
            continue
        node_ids.append(node_id)
        deps = node.get("depends_on")
        if not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps):
            errors.append(f"node {node_id} depends_on must be a list of ids.")
            deps = []
        dependencies[node_id] = deps
        role = node.get("role")
        if not isinstance(role, str) or role not in NODE_ROLES:
            errors.append(f"node {node_id} role must be one of: {', '.join(sorted(NODE_ROLES))}.")
        for field in ("contract_ids", "validation"):
            value = node.get(field)
            if not isinstance(value, list) or any(not isinstance(entry, str) for entry in value):
                errors.append(f"node {node_id} requires list field {field}.")
        for field in ("contract_ids", "validation"):
            value = node.get(field)
            if not isinstance(value, list) or not value or any(not isinstance(entry, str) or not entry.strip() for entry in value):
                errors.append(f"node {node_id} {field} must be a non-empty list of strings.")

        if isinstance(node.get("contract_ids"), list):
            unknown_contract = sorted(set(item for item in node["contract_ids"] if isinstance(item, str)) - compiled_ids)
            if unknown_contract:
                errors.append(f"node {node_id} references unknown contract ids: {', '.join(unknown_contract)}")
        if "model" in node or "provider" in node:
            errors.append(f"node {node_id} must not hard-code model/provider policy.")
    duplicate_nodes = sorted({node_id for node_id in node_ids if node_ids.count(node_id) > 1})
    if duplicate_nodes:
        errors.append("duplicate node ids: " + ", ".join(duplicate_nodes))
    known_nodes = set(node_ids)
    mapped_contract_ids: set[str] = set()
    for node in nodes:
        if isinstance(node, dict) and isinstance(node.get("contract_ids"), list):
            mapped_contract_ids.update(item for item in node["contract_ids"] if isinstance(item, str))
    unmapped = sorted(compiled_ids - mapped_contract_ids)
    if unmapped:
        errors.append("contract ids not mapped to executable nodes: " + ", ".join(unmapped))
    for node_id, deps in dependencies.items():
        unknown = sorted(set(deps) - known_nodes)
        if unknown:
            errors.append(f"node {node_id} depends on unknown nodes: {', '.join(unknown)}")
        if node_id in deps:
            errors.append(f"node {node_id} cannot depend on itself.")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return False
        if node_id in visited:
            return True
        visiting.add(node_id)
        for dependency in dependencies.get(node_id, []):
            if dependency in known_nodes and not visit(dependency):
                return False
        visiting.remove(node_id)
        visited.add(node_id)
        return True

    if any(not visit(node_id) for node_id in node_ids):
        errors.append("executable DAG contains a cycle.")
    trajectory = executable.get("trajectory") if isinstance(executable.get("trajectory"), dict) else {}
    if set(trajectory) != {"first_task", "todo_limit"}:
        errors.append("trajectory must define only first_task and todo_limit.")
    todo_limit = trajectory.get("todo_limit")
    if not isinstance(todo_limit, int) or isinstance(todo_limit, bool) or not 1 <= todo_limit <= 12:
        errors.append("trajectory.todo_limit must be from 1 through 12.")
    elif len(node_ids) > todo_limit:
        errors.append("executable node count must not exceed trajectory.todo_limit.")

    first_task = trajectory.get("first_task")
    if not isinstance(first_task, str) or first_task not in known_nodes:
        errors.append("trajectory.first_task must name an executable node.")
    depth = spec.get("depth")
    if depth == "tiny" and len(node_ids) != 1:
        errors.append("tiny work requires exactly one executable node.")
    if depth == "standard" and len(node_ids) > 12:
        errors.append("standard work supports at most 12 executable nodes.")
    critique = executable.get("critique") if isinstance(executable.get("critique"), dict) else {}
    if set(critique) != {"technical", "contract"}:
        errors.append("critique must define only technical and contract.")
    if critique.get("technical") != "required" or critique.get("contract") != "required":
        errors.append("executable requires separate technical and contract critique.")
    human_gates = executable.get("human_gates")
    if not isinstance(human_gates, list) or not human_gates or any(not isinstance(gate, str) or not gate.strip() for gate in human_gates):
        errors.append("executable human_gates must be a non-empty list of strings.")
    else:
        missing_gates = sorted(REQUIRED_HUMAN_GATES - set(human_gates))
        if missing_gates:
            errors.append("executable human_gates missing required gates: " + ", ".join(missing_gates))
    return executable, errors


def active_work_count(work_root: Path, excluding: str | None = None) -> int:
    count = 0
    for folder in iter_work_item_dirs(work_root):
        if folder.name == excluding or not (folder / "state.yaml").exists():
            continue
        reject_canonical_symlinks(folder, include_materialized=True)
        state = load_json(folder / "state.yaml")
        state_errors = state_data_errors(folder, state)
        if state_errors:
            raise RazrError(f"cannot allocate queue slots while {folder.name} has invalid state: " + "; ".join(state_errors))
        if state.get("status") in {"active", "blocked"}:
            count += 1
    return count


def initial_state(
    work_id: str,
    spec: dict[str, Any],
    executable: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    nodes = executable["nodes"]
    node_states = {
        node["id"]: {
            "status": "pending" if not node.get("depends_on") else "waiting",
            "attempt": 0,
            "result_ref": None,
        }
        for node in nodes
    }
    max_active = config["adapter"]["max_active_work_items"]
    status = "active" if active_work_count((git_root() / config["work_root"]).resolve(), excluding=work_id) < max_active else "queued"
    return {
        "schema_version": 1,
        "id": work_id,
        "stage": "build",
        "status": status,
        "generation": 1,
        "created_at": now_iso(),
        "depth": spec.get("depth"),
        "priority": spec.get("priority"),
        "approved_at": spec.get("approved_at"),
        "spec": {"approved_revision": spec.get("revision"), "digest": executable["source_spec"]["digest"]},
        "executable": {
            "source_spec_revision": executable["source_spec"]["revision"],
            "digest": executable_digest(executable),
        },
        "nodes": node_states,
        "trajectory": {
            "first_verified_task": {"node": None, "evidence_ref": None, "verified_at": None},
        },
        "candidate": {"repository": None, "branch": None, "commit": None, "source_digest": None},
        "critics": {
            "technical": {
                "outcome": "not_due", "candidate": None, "reviewed_commit": None,
                "evidence_ref": None, "reviewer": None, "context_id": None, "recorded_at": None,
            },
            "contract": {
                "outcome": "not_due", "candidate": None, "reviewed_commit": None,
                "evidence_ref": None, "reviewer": None, "context_id": None, "recorded_at": None,
            },
        },
        "delivery": {
            "pull_request": None,
            "head_commit": None,
            "source_digest": None,
            "outcome": "not_due",
            "review_iteration": 0,
        },
        "last_transition": {"from": "spec", "to": "build", "decision": "approved_contract", "reason": None, "at": now_iso()},
    }


def executable_digest(executable: dict[str, Any]) -> str:
    payload = json.dumps(executable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def validate_work(work_root: Path, work_id: str) -> list[str]:
    errors: list[str] = []
    folder = work_item_dir(work_root, work_id)
    if not folder.is_dir():
        return [f"missing work item: {work_id}"]
    if folder.is_symlink():
        return [f"work item must not be a symlink: {folder}"]
    try:
        reject_canonical_symlinks(folder, include_materialized=(folder / "state.yaml").exists())
    except RazrError as exc:
        return [str(exc)]
    try:
        spec, body = parse_spec(folder / "spec.md")
    except (RazrError, OSError, UnicodeError) as exc:
        return [str(exc)]
    if spec.get("id") != work_id:
        errors.append("spec id does not match work item.")
    unsupported_spec = sorted(set(spec) - SPEC_FRONTMATTER_FIELDS)
    if unsupported_spec:
        errors.append("unsupported spec frontmatter fields: " + ", ".join(unsupported_spec))
    if not is_timestamp(spec.get("created_at")):
        errors.append("spec created_at must be an ISO-8601 timestamp with timezone.")
    missing_sections = missing_spec_sections(body)
    if missing_sections:
        errors.append("missing required spec sections: " + ", ".join(missing_sections))
    condition = spec.get("condition")
    if condition not in {"exploring", "draft", "ready", "approved"}:
        errors.append(f"invalid spec condition: {condition!r}.")
    priority = spec.get("priority")
    if not isinstance(priority, int) or isinstance(priority, bool) or not 0 <= priority <= 1000:
        errors.append("spec priority must be an integer from 0 through 1000.")
    state_path = folder / "state.yaml"
    if not state_path.exists():
        allowed = {"spec.md"} if condition != "approved" else {"spec.md", "executable.yaml"}
        extras = {path.name for path in folder.iterdir() if path.is_file() and not path.name.startswith(".")} - allowed
        if extras:
            errors.append("pre-approval work item contains unexpected files: " + ", ".join(sorted(extras)))
        if condition == "approved" and not (folder / "executable.yaml").is_file():
            errors.append("approved pre-materialization work requires executable.yaml.")
        if condition != "approved" and (folder / "executable.yaml").exists():
            errors.append("executable.yaml is not allowed before exact spec approval.")
        return errors

    names = {path.name for path in folder.iterdir() if path.is_file() and not path.name.startswith(".")}
    required = {"spec.md", "executable.yaml", "state.yaml", "evidence.md"}
    if names != required:
        missing = sorted(required - names)
        extras = sorted(names - required)
        if missing:
            errors.append("missing canonical files: " + ", ".join(missing))
        if extras:
            errors.append("unexpected files in work item: " + ", ".join(extras))
    for name in required:
        if (folder / name).is_symlink():
            errors.append(f"canonical file must not be a symlink: {name}")
    executable, executable_errors = validate_executable(folder, work_id, spec, body)
    errors.extend(executable_errors)
    try:
        state = load_json(state_path)
    except RazrError as exc:
        errors.append(str(exc))
        return errors
    errors.extend(state_data_errors(folder, state))
    if state.get("schema_version") != 1 or state.get("id") != work_id:
        errors.append("state schema/id is invalid.")
    if condition != "approved":
        errors.append("materialized work requires an approved spec condition.")
    stage_value = state.get("stage")
    status_value = state.get("status")
    stage = stage_value if isinstance(stage_value, str) else None
    status = status_value if isinstance(status_value, str) else None
    if stage is None or stage not in STAGES:
        errors.append(f"invalid state stage: {stage!r}.")
    if status is None or status not in STATUSES:
        errors.append(f"invalid state status: {status!r}.")
    generation = state.get("generation")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        errors.append("state generation must be a positive integer.")
    state_spec = state.get("spec") if isinstance(state.get("spec"), dict) else {}
    if not isinstance(state.get("spec"), dict):
        errors.append("state spec must be an object.")
    state_executable = state.get("executable") if isinstance(state.get("executable"), dict) else {}
    if not isinstance(state.get("executable"), dict):
        errors.append("state executable must be an object.")
    if state_spec.get("approved_revision") != spec.get("revision"):
        errors.append("state approved revision does not match spec.")
    if state_spec.get("digest") != spec_digest(folder / "spec.md"):
        errors.append("state spec digest does not match spec.md.")
    if executable and state_executable.get("digest") != executable_digest(executable):
        errors.append("state executable digest does not match executable.yaml.")
    state_nodes = state.get("nodes") if isinstance(state.get("nodes"), dict) else {}
    if not isinstance(state.get("nodes"), dict):
        errors.append("state nodes must be an object.")
    executable_nodes_by_id = {
        node.get("id"): node
        for node in executable.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("id"), str)
    }
    executable_nodes = set(executable_nodes_by_id)
    if set(state_nodes) != executable_nodes:
        errors.append("state node set does not match executable DAG.")
    for node_id, node_state in state_nodes.items():
        node_status = node_state.get("status") if isinstance(node_state, dict) else None
        if not isinstance(node_status, str) or node_status not in {"waiting", "pending", "running", "blocked", "complete", "failed"}:
            errors.append(f"invalid node state: {node_id}")
        if node_status == "complete":
            node_spec = executable_nodes_by_id.get(node_id, {})
            validations = node_spec.get("validation") if isinstance(node_spec, dict) else None
            result_ref = node_state.get("result_ref") if isinstance(node_state, dict) else None
            if not isinstance(validations, list) or not evidence_ref_proves_node(folder, result_ref, validations):
                errors.append(f"completed node evidence no longer proves declared validations: {node_id}")
    candidate = state.get("candidate") if isinstance(state.get("candidate"), dict) else {}
    if not isinstance(state.get("candidate"), dict):
        errors.append("state candidate must be an object.")
    if stage in {"verify", "review"}:
        commit = candidate.get("commit")
        if not isinstance(commit, str) or not FULL_SHA_RE.fullmatch(commit):
            errors.append(f"stage {stage} requires a full candidate SHA.")
        if not isinstance(candidate.get("source_digest"), str) or not SOURCE_DIGEST_RE.fullmatch(candidate["source_digest"]):
            errors.append(f"stage {stage} requires a source-tree digest.")
        if not isinstance(candidate.get("repository"), str) or not REPOSITORY_RE.fullmatch(candidate["repository"]):
            errors.append(f"stage {stage} requires owner/repository identity.")
    critics = state.get("critics") if isinstance(state.get("critics"), dict) else {}
    if not isinstance(state.get("critics"), dict):
        errors.append("state critics must be an object.")
    if stage == "review":
        technical = critics.get("technical") if isinstance(critics.get("technical"), dict) else {}
        contract = critics.get("contract") if isinstance(critics.get("contract"), dict) else {}
        if technical.get("outcome") != "PASS":
            errors.append("review requires Technical Critic PASS.")
        if contract.get("outcome") != "PASS":
            errors.append("review requires Contract Critic PASS.")
        for role in ("technical", "contract"):
            record = critics.get(role) if isinstance(critics.get(role), dict) else {}
            if record.get("candidate") != candidate.get("source_digest"):
                errors.append(f"review requires {role} Critic source-digest identity.")
            evidence_ref = record.get("evidence_ref")
            if not isinstance(evidence_ref, str) or not evidence_ref.startswith("evidence.md#"):
                errors.append(f"review requires {role} Critic evidence.md anchor.")
            elif not critic_evidence_matches(
                folder,
                evidence_ref,
                source_digest=str(candidate.get("source_digest") or ""),
                commit=str(record.get("reviewed_commit") or ""),
                reviewer=str(record.get("reviewer") or ""),
                context_id=str(record.get("context_id") or ""),
                outcome=str(record.get("outcome") or ""),
            ):
                errors.append(f"review requires intact {role} Critic evidence and provenance.")
    delivery = state.get("delivery") if isinstance(state.get("delivery"), dict) else {}
    if not isinstance(state.get("delivery"), dict):
        errors.append("state delivery must be an object.")
    if status == "complete":
        delivery_outcome = delivery.get("outcome")
        if stage != "review" or not isinstance(delivery_outcome, str) or delivery_outcome not in {"ready", "merged"}:
            errors.append("complete status requires review stage and ready/merged PR outcome.")
        if delivery.get("source_digest") != candidate.get("source_digest"):
            errors.append("complete PR source digest does not match candidate.")
    if not (folder / "evidence.md").read_text(encoding="utf-8").strip():
        errors.append("evidence.md must not be empty.")
    return errors


def command_status(args: argparse.Namespace) -> None:
    _, _, work_root = project_context()
    rows: list[dict[str, Any]] = []
    for folder in iter_work_item_dirs(work_root):
        if args.work_id and folder.name != args.work_id:
            continue
        if (folder / "state.yaml").is_file():
            state = load_json(folder / "state.yaml")
            candidate = state.get("candidate") if isinstance(state.get("candidate"), dict) else {}
            delivery = state.get("delivery") if isinstance(state.get("delivery"), dict) else {}
            rows.append(
                {
                    "id": folder.name,
                    "stage": state.get("stage"),
                    "status": state.get("status"),
                    "depth": state.get("depth"),
                    "priority": state.get("priority") if isinstance(state.get("priority"), int) else 1000,
                    "approved_at": state.get("approved_at"),
                    "candidate": candidate.get("commit"),
                    "pull_request": delivery.get("pull_request"),
                }
            )
        elif (folder / "spec.md").is_file():
            spec, _ = parse_spec(folder / "spec.md")
            rows.append(
                {
                    "id": folder.name,
                    "stage": "spec",
                    "status": spec.get("condition"),
                    "depth": spec.get("depth"),
                    "priority": spec.get("priority") if isinstance(spec.get("priority"), int) else 1000,
                    "approved_at": spec.get("approved_at"),
                    "candidate": None,
                    "pull_request": None,
                }
            )
    rank = {"active": 0, "blocked": 1, "queued": 2, "exploring": 2, "draft": 2, "ready": 2, "complete": 3, "approved": 3, "cancelled": 4}
    rows.sort(key=lambda row: (
        rank.get(str(row["status"]), 2), row.get("priority", 100),
        dt.datetime.fromisoformat(row["approved_at"].replace("Z", "+00:00"))
        if is_timestamp(row.get("approved_at")) else dt.datetime.min.replace(tzinfo=dt.timezone.utc),
        row["id"],
    ))
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        for row in rows:
            print(
                f"{row['id']}\t{row['stage']}\t{row['status']}\tdepth={row['depth']}\t"
                f"priority={row['priority']}\tpr={row['pull_request'] or '-'}"
            )
        print(f"Work items: {len(rows)}")


def command_validate(args: argparse.Namespace) -> None:
    _, config, work_root = project_context()
    work_ids: list[str]
    if args.all:
        work_ids = [path.name for path in iter_work_item_dirs(work_root)]
    elif args.work_id:
        work_ids = [args.work_id]
    else:
        raise RazrError("validate requires a work item id or --all.")
    failed = False
    for work_id in work_ids:
        errors = validate_work(work_root, work_id)
        if errors:
            failed = True
            print(f"FAIL {work_id}")
            for error in errors:
                print(f"  ERROR: {error}")
        else:
            print(f"PASS {work_id}")
    if args.all:
        active = active_work_count(work_root)
        maximum = config["adapter"]["max_active_work_items"]
        if active > maximum:
            failed = True
            print(f"FAIL queue\n  ERROR: active work count {active} exceeds adapter.max_active_work_items {maximum}.")
    if failed:
        raise RazrError("validation failed.")


def read_state(folder: Path) -> dict[str, Any]:
    return load_json(folder / "state.yaml")


def check_expectations(state: dict[str, Any], args: argparse.Namespace) -> None:
    if getattr(args, "expect_stage", None) and state.get("stage") != args.expect_stage:
        raise RazrError(f"expected stage {args.expect_stage}, found {state.get('stage')}.")
    expected_generation = getattr(args, "expect_generation", None)
    if expected_generation is not None and state.get("generation") != expected_generation:
        raise RazrError(
            f"expected generation {expected_generation}, found {state.get('generation')}."
        )


def evidence_ref_exists(folder: Path, value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith("evidence.md#"):
        return False
    anchor = value.split("#", 1)[1].strip().lower()
    if not anchor or not WORK_ID_RE.fullmatch(anchor):
        return False
    text = (folder / "evidence.md").read_text(encoding="utf-8")
    headings = re.findall(r"^#{1,6}\s+(.+?)\s*$", text, re.MULTILINE)
    normalized = {
        re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
        for heading in headings
    }
    return anchor in normalized


def evidence_section(folder: Path, value: Any) -> str | None:
    if not evidence_ref_exists(folder, value):
        return None
    anchor = str(value).split("#", 1)[1].strip().lower()
    text = (folder / "evidence.md").read_text(encoding="utf-8")
    headings = list(re.finditer(r"^(#{1,6})\s+(.+?)\s*$", text, re.MULTILINE))
    for index, heading in enumerate(headings):
        normalized = re.sub(r"[^a-z0-9]+", "-", heading.group(2).lower()).strip("-")
        if normalized == anchor:
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            return text[heading.end():end].strip()
    return None


def evidence_ref_proves_node(folder: Path, value: Any, validations: Any) -> bool:
    section = evidence_section(folder, value)
    if not section or section.strip().lower() == "pending.":
        return False
    if not isinstance(validations, list) or not validations:
        return False
    # Parse an explicit record, not prose mentioning a successful outcome.
    # This validates an attestation; it does not execute the command.
    lines = {line.strip() for line in section.splitlines()}
    return all(
        isinstance(command, str)
        and bool(command.strip())
        and "\n" not in command and "\r" not in command
        and f"- {command} => PASS" in lines
        for command in validations
    )


def evidence_ref_targets(folder: Path, value: Any, source_digest: str) -> bool:
    section = evidence_section(folder, value)
    return bool(section and source_digest in section)


def critic_evidence_matches(
    folder: Path,
    value: Any,
    *,
    source_digest: str,
    commit: str,
    reviewer: str,
    context_id: str,
    outcome: str,
) -> bool:
    section = evidence_section(folder, value)
    if not section or section.strip().lower() == "pending.":
        return False
    required_lines = {
        f"Candidate: {source_digest}",
        f"Reviewed commit: {commit}",
        f"Reviewer: {reviewer}",
        f"Context: {context_id}",
        f"Verdict: {outcome}",
    }
    lines = {line.strip() for line in section.splitlines() if line.strip()}
    return required_lines <= lines and len(section.split()) >= 12


def reset_candidate_state(state: dict[str, Any], *, preserve_pr: bool) -> None:
    state["candidate"] = {"repository": None, "branch": None, "commit": None, "source_digest": None}
    state["critics"] = {
        "technical": {
            "outcome": "not_due", "candidate": None, "reviewed_commit": None,
            "evidence_ref": None, "reviewer": None, "context_id": None, "recorded_at": None,
        },
        "contract": {
            "outcome": "not_due", "candidate": None, "reviewed_commit": None,
            "evidence_ref": None, "reviewer": None, "context_id": None, "recorded_at": None,
        },
    }
    prior_delivery = state.get("delivery") if isinstance(state.get("delivery"), dict) else {}
    state["delivery"] = {
        "pull_request": prior_delivery.get("pull_request") if preserve_pr else None,
        "head_commit": None,
        "source_digest": None,
        "outcome": "pending" if preserve_pr and prior_delivery.get("pull_request") else "not_due",
        "review_iteration": prior_delivery.get("review_iteration", 0) + (1 if preserve_pr else 0),
    }


def promote_ready_nodes(state: dict[str, Any], executable: dict[str, Any]) -> None:
    nodes = state.get("nodes", {})
    for node in executable.get("nodes", []):
        if not isinstance(node, dict) or node.get("id") not in nodes:
            continue
        node_state = nodes[node["id"]]
        if node_state.get("status") != "waiting":
            continue
        dependencies = node.get("depends_on", [])
        if all(nodes.get(dependency, {}).get("status") == "complete" for dependency in dependencies):
            node_state["status"] = "pending"


def state_data_errors(folder: Path, state: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if state.get("schema_version") != 1 or state.get("id") != folder.name:
        errors.append("state schema/id is invalid")
    stage_value = state.get("stage")
    if not isinstance(stage_value, str) or stage_value not in STAGES:
        errors.append("state stage is invalid")
    status_value = state.get("status")
    if not isinstance(status_value, str) or status_value not in STATUSES:
        errors.append("state status is invalid")
    generation = state.get("generation")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        errors.append("state generation is invalid")
    priority = state.get("priority")
    if not isinstance(priority, int) or isinstance(priority, bool) or not 0 <= priority <= 1000:
        errors.append("state priority is invalid")
    if not is_timestamp(state.get("approved_at")):
        errors.append("state approved_at is invalid")

    state_spec = state.get("spec") if isinstance(state.get("spec"), dict) else {}
    if not isinstance(state.get("spec"), dict):
        errors.append("state spec must be an object")
    elif (
        not isinstance(state_spec.get("approved_revision"), int)
        or isinstance(state_spec.get("approved_revision"), bool)
        or state_spec["approved_revision"] < 1
        or not isinstance(state_spec.get("digest"), str)
        or not SOURCE_DIGEST_RE.fullmatch(state_spec["digest"])
    ):
        errors.append("state spec approval identity is invalid")

    state_executable = state.get("executable") if isinstance(state.get("executable"), dict) else {}
    if not isinstance(state.get("executable"), dict):
        errors.append("state executable must be an object")
    elif (
        not isinstance(state_executable.get("source_spec_revision"), int)
        or isinstance(state_executable.get("source_spec_revision"), bool)
        or not isinstance(state_executable.get("digest"), str)
        or not SOURCE_DIGEST_RE.fullmatch(state_executable["digest"])
    ):
        errors.append("state executable identity is invalid")

    nodes = state.get("nodes")
    if not isinstance(nodes, dict):
        errors.append("state nodes must be an object")
    else:
        for node_id, node in nodes.items():
            if not isinstance(node_id, str) or not isinstance(node, dict):
                errors.append("state node entry is invalid")
                continue
            node_status = node.get("status")
            if not isinstance(node_status, str) or node_status not in {"waiting", "pending", "running", "blocked", "complete", "failed"}:
                errors.append(f"state node status is invalid: {node_id}")
            attempt = node.get("attempt")
            if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 0:
                errors.append(f"state node attempt is invalid: {node_id}")
            if node.get("result_ref") is not None and not isinstance(node.get("result_ref"), str):
                errors.append(f"state node result_ref is invalid: {node_id}")

    candidate = state.get("candidate") if isinstance(state.get("candidate"), dict) else {}
    if not isinstance(state.get("candidate"), dict):
        errors.append("state candidate must be an object")
    candidate_values = [candidate.get(key) for key in ("repository", "branch", "commit", "source_digest")]
    if any(value is not None for value in candidate_values):
        if not isinstance(candidate.get("repository"), str) or not REPOSITORY_RE.fullmatch(candidate["repository"]):
            errors.append("candidate repository is invalid")
        if not isinstance(candidate.get("branch"), str) or not BRANCH_RE.fullmatch(candidate["branch"]):
            errors.append("candidate branch is invalid")
        if not isinstance(candidate.get("commit"), str) or not FULL_SHA_RE.fullmatch(candidate["commit"]):
            errors.append("candidate commit is invalid")
        if not isinstance(candidate.get("source_digest"), str) or not SOURCE_DIGEST_RE.fullmatch(candidate["source_digest"]):
            errors.append("candidate source digest is invalid")
    if isinstance(state.get("stage"), str) and state.get("stage") in {"verify", "review"} and all(value is None for value in candidate_values):
        errors.append("verify/review state requires candidate identity")

    trajectory = state.get("trajectory")
    if not isinstance(trajectory, dict):
        errors.append("state trajectory must be an object")
    else:
        checkpoint = trajectory.get("first_verified_task")
        if not isinstance(checkpoint, dict):
            errors.append("state trajectory checkpoint is invalid")
        elif checkpoint.get("node") is not None:
            if (
                not isinstance(checkpoint.get("node"), str)
                or not isinstance(checkpoint.get("evidence_ref"), str)
                or not is_timestamp(checkpoint.get("verified_at"))
            ):
                errors.append("state first verified task is invalid")

    critics = state.get("critics") if isinstance(state.get("critics"), dict) else {}
    if not isinstance(state.get("critics"), dict):
        errors.append("state critics must be an object")
    else:
        for role in ("technical", "contract"):
            record = critics.get(role)
            if not isinstance(record, dict):
                errors.append(f"state {role} Critic record must be an object")
                continue
            expected_fields = {
                "outcome", "candidate", "reviewed_commit", "evidence_ref",
                "reviewer", "context_id", "recorded_at",
            }
            if set(record) != expected_fields:
                errors.append(f"state {role} Critic fields are invalid")
            critic_outcome = record.get("outcome")
            if not isinstance(critic_outcome, str) or critic_outcome not in {"not_due", "PASS", "FAIL"}:
                errors.append(f"state {role} Critic outcome is invalid")
                continue
            provenance_values = [
                record.get("candidate"), record.get("reviewed_commit"), record.get("evidence_ref"),
                record.get("reviewer"), record.get("context_id"), record.get("recorded_at"),
            ]
            if critic_outcome == "not_due":
                if any(value is not None for value in provenance_values):
                    errors.append(f"state {role} Critic not_due record must not contain provenance")
            else:
                if not isinstance(record.get("candidate"), str) or not SOURCE_DIGEST_RE.fullmatch(record["candidate"]):
                    errors.append(f"state {role} Critic candidate is invalid")
                if not isinstance(record.get("reviewed_commit"), str) or not FULL_SHA_RE.fullmatch(record["reviewed_commit"]):
                    errors.append(f"state {role} Critic reviewed commit is invalid")
                if not isinstance(record.get("evidence_ref"), str):
                    errors.append(f"state {role} Critic evidence ref is invalid")
                if not isinstance(record.get("reviewer"), str) or not PROVENANCE_RE.fullmatch(record["reviewer"]):
                    errors.append(f"state {role} Critic reviewer is invalid")
                if not isinstance(record.get("context_id"), str) or not PROVENANCE_RE.fullmatch(record["context_id"]):
                    errors.append(f"state {role} Critic context is invalid")
                if not is_timestamp(record.get("recorded_at")):
                    errors.append(f"state {role} Critic recorded_at is invalid")
        technical = critics.get("technical") if isinstance(critics.get("technical"), dict) else {}
        contract = critics.get("contract") if isinstance(critics.get("contract"), dict) else {}
        technical_outcome = technical.get("outcome") if isinstance(technical.get("outcome"), str) else None
        contract_outcome = contract.get("outcome") if isinstance(contract.get("outcome"), str) else None
        if technical_outcome in {"PASS", "FAIL"} and contract_outcome in {"PASS", "FAIL"}:
            if technical.get("reviewer") == contract.get("reviewer"):
                errors.append("Technical and Contract Critics must have distinct reviewers")
            if technical.get("context_id") == contract.get("context_id"):
                errors.append("Technical and Contract Critics must have distinct contexts")
            if technical.get("evidence_ref") == contract.get("evidence_ref"):
                errors.append("Technical and Contract Critics must have distinct evidence")

    delivery = state.get("delivery") if isinstance(state.get("delivery"), dict) else {}
    if not isinstance(state.get("delivery"), dict):
        errors.append("state delivery must be an object")
    else:
        delivery_outcome = delivery.get("outcome")
        if not isinstance(delivery_outcome, str) or delivery_outcome not in {"not_due", "pending", "ready", "merged", "closed"}:
            errors.append("state delivery outcome is invalid")
    if state.get("status") == "complete":
        delivery_outcome = delivery.get("outcome")
        if state.get("stage") != "review" or not isinstance(delivery_outcome, str) or delivery_outcome not in {"ready", "merged"}:
            errors.append("complete state requires review with ready/merged PR")
        if delivery.get("source_digest") != candidate.get("source_digest"):
            errors.append("complete PR source digest differs from candidate")
    return errors


def save_state(folder: Path, state: dict[str, Any], guard: StateLock) -> None:
    guard.assert_owned()
    errors = state_data_errors(folder, state)
    if errors:
        raise RazrError("refusing invalid state mutation:\n- " + "\n- ".join(errors))
    atomic_write_json(folder / "state.yaml", state)


def promote_queued(work_root: Path, config: dict[str, Any]) -> None:
    with state_lock(work_root) as queue_guard:
        _promote_queued_locked(work_root, config, queue_guard)


def _promote_queued_locked(
    work_root: Path,
    config: dict[str, Any],
    queue_guard: StateLock,
) -> None:
    max_active = config["adapter"]["max_active_work_items"]
    active = active_work_count(work_root)
    if active >= max_active:
        return
    queued: list[tuple[int, str, Path, dict[str, Any]]] = []
    for folder in iter_work_item_dirs(work_root):
        if not (folder / "state.yaml").exists():
            continue
        reject_canonical_symlinks(folder, include_materialized=True)
        state = load_json(folder / "state.yaml")
        if state.get("status") == "queued":
            priority = state.get("priority")
            approved_at = state.get("approved_at")
            if not isinstance(priority, int) or not isinstance(approved_at, str):
                raise RazrError(f"queued work item has invalid ordering metadata: {folder.name}")
            queued.append((priority, approved_at, folder, state))
    queued.sort(key=lambda item: (
        item[0], dt.datetime.fromisoformat(item[1].replace("Z", "+00:00")), item[2].name
    ))
    for _, _, folder, state in queued[: max_active - active]:
        with state_lock(folder) as guard:
            current = read_state(folder)
            if current.get("status") != "queued":
                continue
            current["status"] = "active"
            current["generation"] += 1
            current["last_transition"] = {
                "from": current.get("stage"),
                "to": current.get("stage"),
                "decision": "concurrency_slot_available",
                "reason": None,
                "at": now_iso(),
            }
            queue_guard.assert_owned()
            save_state(folder, current, guard)


def command_transition(args: argparse.Namespace) -> None:
    _, config, work_root = project_context()
    with state_lock(work_root) as queue_guard:
        _command_transition_locked(args, config, work_root, queue_guard)


def _command_transition_locked(
    args: argparse.Namespace,
    config: dict[str, Any],
    work_root: Path,
    queue_guard: StateLock,
) -> None:
    folder = work_item_dir(work_root, args.work_id)
    with state_lock(folder) as guard:
        state = read_state(folder)
        check_expectations(state, args)
        if state.get("status") == "cancelled":
            raise RazrError("cancelled work is terminal.")
        executable = load_json(folder / "executable.yaml")
        current_stage = state.get("stage")
        decision = args.decision or "state_update"
        if args.node or (args.to and args.to != "spec"):
            contract_errors = validate_work(work_root, args.work_id)
            if contract_errors:
                raise RazrError("current contract/state validation failed:\n- " + "\n- ".join(contract_errors))

        if args.node:
            if state.get("stage") != "build" or state.get("status") != "active":
                raise RazrError("node progress may be updated only for active Build work.")
            if args.to:
                raise RazrError("node update and stage transition must be separate commands.")
            node_state = state.get("nodes", {}).get(args.node)
            if not isinstance(node_state, dict):
                raise RazrError(f"unknown executable node: {args.node}")
            if args.node_status not in {"running", "blocked", "complete", "failed"}:
                raise RazrError("node update requires --node-status.")
            current_node_status = node_state.get("status")
            legal_node_transitions = {
                "pending": {"running", "blocked"},
                "running": {"complete", "blocked", "failed"},
                "blocked": {"running", "failed"},
                "failed": {"running"},
                "waiting": set(),
                "complete": set(),
            }
            if args.node_status not in legal_node_transitions.get(str(current_node_status), set()):
                raise RazrError(f"illegal node transition: {args.node} {current_node_status} -> {args.node_status}.")
            node_definition = next(
                (node for node in executable.get("nodes", []) if isinstance(node, dict) and node.get("id") == args.node),
                {},
            )
            dependencies = node_definition.get("depends_on", []) if isinstance(node_definition, dict) else []
            if args.node_status == "running" and any(
                state.get("nodes", {}).get(dependency, {}).get("status") != "complete"
                for dependency in dependencies
            ):
                raise RazrError(f"node dependencies are incomplete: {args.node}")
            if args.node_status == "complete" and not evidence_ref_proves_node(
                folder, args.result_ref, node_definition.get("validation")
            ):
                raise RazrError("completed node requires validation evidence with an exact '- COMMAND => PASS' line for every declared command; prose mentioning PASS is not a result record.")
            node_state["status"] = args.node_status
            node_state["attempt"] = int(node_state.get("attempt", 0)) + (1 if args.node_status == "running" else 0)
            if args.result_ref:
                node_state["result_ref"] = args.result_ref
            first_task = (
                executable.get("trajectory", {}).get("first_task")
                if isinstance(executable.get("trajectory"), dict)
                else None
            )
            if args.node_status == "complete" and args.node == first_task:
                state["trajectory"]["first_verified_task"] = {
                    "node": args.node,
                    "evidence_ref": args.result_ref,
                    "verified_at": now_iso(),
                }
            promote_ready_nodes(state, executable)
            decision = f"node_{args.node}_{args.node_status}"
        elif args.to:
            target = args.to
            if current_stage == "spec" and target == "build":
                raise RazrError("Spec -> Build is allowed only through `razr approve` after exact reapproval.")
            if state.get("status") in {"queued", "blocked"} and target != current_stage:
                raise RazrError(f"{state.get('status')} work cannot change stage until activated.")
            if target == current_stage:
                if args.status not in {"active", "blocked"}:
                    raise RazrError("same-stage update supports active or blocked status only.")
                if state.get("status") == "queued":
                    raise RazrError("queued work may be activated only by the deterministic queue promoter.")
                state["status"] = args.status
            elif target not in LEGAL_TRANSITIONS.get(current_stage, set()):
                raise RazrError(f"illegal transition: {current_stage} -> {target}.")
            else:
                if target == "verify":
                    incomplete = [node_id for node_id, node in state.get("nodes", {}).items() if node.get("status") != "complete"]
                    if incomplete:
                        raise RazrError("Build has incomplete nodes: " + ", ".join(sorted(incomplete)))
                    if not FULL_SHA_RE.fullmatch(str(state.get("candidate", {}).get("commit", ""))):
                        raise RazrError("Build -> Verify requires a full candidate SHA.")
                if target == "review":
                    source_digest = state.get("candidate", {}).get("source_digest")
                    critics = state.get("critics", {})
                    if critics.get("technical", {}).get("outcome") != "PASS":
                        raise RazrError("Verify -> Review requires Technical Critic PASS.")
                    if critics.get("contract", {}).get("outcome") != "PASS":
                        raise RazrError("Verify -> Review requires Contract Critic PASS.")
                    if critics.get("technical", {}).get("candidate") != source_digest or critics.get("contract", {}).get("candidate") != source_digest:
                        raise RazrError("Critic source digest does not match current candidate.")
                    for role in ("technical", "contract"):
                        record = critics.get(role) if isinstance(critics.get(role), dict) else {}
                        if not critic_evidence_matches(
                            folder,
                            record.get("evidence_ref"),
                            source_digest=str(source_digest or ""),
                            commit=str(record.get("reviewed_commit") or ""),
                            reviewer=str(record.get("reviewer") or ""),
                            context_id=str(record.get("context_id") or ""),
                            outcome=str(record.get("outcome") or ""),
                        ):
                            raise RazrError(f"Verify -> Review requires intact {role} Critic evidence and provenance.")
                should_reset = (
                    target == "spec" and current_stage in {"build", "verify", "review"}
                ) or (
                    target == "build" and current_stage in {"verify", "review"}
                )
                if should_reset:
                    reset_candidate_state(state, preserve_pr=current_stage == "review")
                    if target == "build":
                        state["trajectory"]["first_verified_task"] = {
                            "node": None,
                            "evidence_ref": None,
                            "verified_at": None,
                        }
                        node_definitions = {
                            node.get("id"): node
                            for node in executable.get("nodes", [])
                            if isinstance(node, dict) and isinstance(node.get("id"), str)
                        }
                        for node_id, node in state.get("nodes", {}).items():
                            dependencies = node_definitions.get(node_id, {}).get("depends_on", [])
                            node["status"] = "pending" if not dependencies else "waiting"
                            node["attempt"] = 0
                            node["result_ref"] = None
                        promote_ready_nodes(state, executable)
                state["stage"] = target
                if target == "review":
                    state["status"] = "active"
                elif target in {"build", "spec"}:
                    max_active = config["adapter"]["max_active_work_items"]
                    state["status"] = "active" if active_work_count(work_root, excluding=args.work_id) < max_active else "queued"
                decision = args.decision or f"enter_{target}"
        elif args.status == "cancelled":
            if not args.reason:
                raise RazrError("cancellation requires --reason.")
            state["status"] = "cancelled"
            decision = args.decision or "cancel_work"
        else:
            raise RazrError("transition requires --to, --node, or --status cancelled.")

        state["generation"] += 1
        state["last_transition"] = {
            "from": current_stage,
            "to": state.get("stage"),
            "decision": decision,
            "reason": args.reason,
            "at": now_iso(),
        }
        queue_guard.assert_owned()
        save_state(folder, state, guard)
    if state.get("status") in {"complete", "cancelled"}:
        _promote_queued_locked(work_root, config, queue_guard)
    print(f"Updated {args.work_id}: {state['stage']} / {state['status']} generation={state['generation']}")


def git_commit_exists(repo: Path, commit: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=repo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def git_commit_blob(repo: Path, commit: str, path: str) -> bytes | None:
    listing = subprocess.run(
        ["git", "ls-tree", "-z", commit, "--", path],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if listing.returncode != 0:
        return None
    entries = [entry for entry in listing.stdout.split(b"\0") if entry]
    if len(entries) != 1 or b"\t" not in entries[0]:
        return None
    metadata, raw_path = entries[0].split(b"\t", 1)
    fields = metadata.split()
    if len(fields) != 3 or fields[0] not in {b"100644", b"100755"} or fields[1] != b"blob":
        return None
    try:
        listed_path = raw_path.decode("utf-8")
        object_id = fields[2].decode("ascii")
    except UnicodeDecodeError:
        return None
    if listed_path != path:
        return None
    blob = subprocess.run(
        ["git", "cat-file", "blob", object_id],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    return blob.stdout if blob.returncode == 0 else None


def git_commit_has_path(repo: Path, commit: str, path: str) -> bool:
    return git_commit_blob(repo, commit, path) is not None


def source_tree_digest(repo: Path, commit: str, work_root_relative: str) -> str:
    result = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--full-tree", commit],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RazrError(f"cannot inspect candidate tree: {commit}")
    excluded_exact = {CONFIG_NAME, "AGENTS.md"}
    excluded_prefix = work_root_relative.rstrip("/") + "/"
    entries: list[bytes] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, raw_path = raw.split(b"\t", 1)
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise RazrError("git returned an invalid tree entry.") from exc
        if path in excluded_exact or path.startswith(excluded_prefix):
            continue
        entries.append(metadata + b"\t" + raw_path + b"\0")
    return "sha256:" + hashlib.sha256(b"".join(entries)).hexdigest()


def branch_commit(repo: Path, branch: str) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and FULL_SHA_RE.fullmatch(value) else None


def git_common_dir(repo: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RazrError(f"cannot identify Git common directory: {repo}")
    value = Path(result.stdout.strip())
    return (value if value.is_absolute() else repo / value).resolve()


def command_candidate(args: argparse.Namespace) -> None:
    repo, config, work_root = project_context()
    folder = work_item_dir(work_root, args.work_id)
    reject_canonical_symlinks(folder, include_materialized=True)
    if not REPOSITORY_RE.fullmatch(args.repository):
        raise RazrError("candidate repository must use owner/repository form.")
    if not BRANCH_RE.fullmatch(args.branch):
        raise RazrError("candidate branch is not a safe Git branch name.")
    if not FULL_SHA_RE.fullmatch(args.commit):
        raise RazrError("candidate commit must be a full lowercase 40-character SHA.")
    candidate_repo = Path(args.repo).expanduser().resolve() if args.repo else repo
    candidate_repo = git_root(candidate_repo)
    if git_common_dir(candidate_repo) != git_common_dir(repo):
        raise RazrError("candidate --repo must belong to the same Git repository (linked worktrees are allowed).")

    with state_lock(work_root) as project_guard:
        # Recheck branch identity and all cross-item bindings while holding the
        # project-wide lock. The same lock protects queue and PR uniqueness.
        if not git_commit_exists(candidate_repo, args.commit):
            raise RazrError(f"candidate commit does not exist: {args.commit}")
        pointed = branch_commit(candidate_repo, args.branch)
        if pointed != args.commit:
            raise RazrError(f"candidate branch {args.branch} does not point at {args.commit}.")
        work_relative = str(config["work_root"]).rstrip("/")
        required_paths = [
            CONFIG_NAME,
            "AGENTS.md",
            f"{work_relative}/{args.work_id}/spec.md",
            f"{work_relative}/{args.work_id}/executable.yaml",
            f"{work_relative}/{args.work_id}/state.yaml",
            f"{work_relative}/{args.work_id}/evidence.md",
        ]
        missing = [path for path in required_paths if not git_commit_has_path(candidate_repo, args.commit, path)]
        if missing:
            raise RazrError("candidate is missing committed Razr artifact paths: " + ", ".join(missing))
        digest = source_tree_digest(candidate_repo, args.commit, work_relative)
        for other in iter_work_item_dirs(work_root):
            if other.name == args.work_id or not (other / "state.yaml").exists():
                continue
            other_state = load_json(other / "state.yaml")
            other_errors = state_data_errors(other, other_state)
            if other_errors:
                raise RazrError(f"cannot bind a candidate while {other.name} has invalid state: " + "; ".join(other_errors))
            other_candidate = other_state.get("candidate") if isinstance(other_state.get("candidate"), dict) else {}
            if other_candidate.get("repository") == args.repository and other_candidate.get("branch") == args.branch:
                raise RazrError(f"candidate branch is already bound to work item {other.name}.")

        with state_lock(folder) as guard:
            project_guard.assert_owned()
            state = read_state(folder)
            check_expectations(state, args)
            if state.get("stage") != "build" or state.get("status") != "active":
                raise RazrError("candidate may be recorded only for active Build work.")
            validation_errors = validate_work(work_root, args.work_id)
            if validation_errors:
                raise RazrError("candidate requires valid current artifacts:\n- " + "\n- ".join(validation_errors))
            live_paths = {
                CONFIG_NAME: repo / CONFIG_NAME,
                "AGENTS.md": repo / "AGENTS.md",
                f"{work_relative}/{args.work_id}/spec.md": folder / "spec.md",
                f"{work_relative}/{args.work_id}/executable.yaml": folder / "executable.yaml",
                f"{work_relative}/{args.work_id}/state.yaml": folder / "state.yaml",
                f"{work_relative}/{args.work_id}/evidence.md": folder / "evidence.md",
            }
            for git_path, live_path in live_paths.items():
                if live_path.is_symlink() or not live_path.is_file():
                    raise RazrError(f"candidate canonical path must be a regular local file: {git_path}")
                committed_blob = git_commit_blob(candidate_repo, args.commit, git_path)
                if committed_blob is None or committed_blob != live_path.read_bytes():
                    raise RazrError(f"current canonical artifact does not match committed blob: {git_path}")
            incomplete = [node_id for node_id, node in state.get("nodes", {}).items() if node.get("status") != "complete"]
            if incomplete:
                raise RazrError("candidate requires complete nodes: " + ", ".join(sorted(incomplete)))
            executable = load_json(folder / "executable.yaml")
            trajectory = executable.get("trajectory") if isinstance(executable.get("trajectory"), dict) else {}
            first_task = trajectory.get("first_task")
            state_trajectory = state.get("trajectory") if isinstance(state.get("trajectory"), dict) else {}
            checkpoint = state_trajectory.get("first_verified_task", {})
            if not isinstance(checkpoint, dict) or checkpoint.get("node") != first_task or not evidence_ref_exists(folder, checkpoint.get("evidence_ref")):
                raise RazrError("candidate requires a recorded first verified task matching executable trajectory.first_task.")
            state["candidate"] = {
                "repository": args.repository,
                "branch": args.branch,
                "commit": args.commit,
                "source_digest": digest,
            }
            state["generation"] += 1
            state["last_transition"] = {
                "from": "build",
                "to": "build",
                "decision": "record_candidate",
                "reason": None,
                "at": now_iso(),
            }
            save_state(folder, state, guard)
    print(f"Candidate {args.work_id}: {args.commit}")
    print(f"Source digest: {digest}")


def command_critics(args: argparse.Namespace) -> None:
    _, _, work_root = project_context()
    folder = work_item_dir(work_root, args.work_id)
    with state_lock(folder) as guard:
        state = read_state(folder)
        check_expectations(state, args)
        state_errors = state_data_errors(folder, state)
        if state_errors:
            raise RazrError("cannot record Critic outcomes for invalid state: " + "; ".join(state_errors))
        if state.get("stage") != "verify":
            raise RazrError("Critic outcomes may be recorded only in Verify.")
        candidate = state.get("candidate", {}) if isinstance(state.get("candidate"), dict) else {}
        source_digest = candidate.get("source_digest")
        if not isinstance(source_digest, str) or not SOURCE_DIGEST_RE.fullmatch(source_digest):
            raise RazrError("Critic outcomes require a current candidate source digest.")
        critics = state.get("critics") if isinstance(state.get("critics"), dict) else {}
        technical = critics.get("technical") if isinstance(critics.get("technical"), dict) else {}
        contract = critics.get("contract") if isinstance(critics.get("contract"), dict) else {}
        commit = candidate.get("commit")
        if not isinstance(commit, str) or not FULL_SHA_RE.fullmatch(commit):
            raise RazrError("Critic outcomes require the reviewed implementation commit.")
        for role, outcome, evidence_ref, reviewer, context_id in (
            ("Technical", args.technical, args.technical_evidence, args.technical_reviewer, args.technical_context),
            ("Contract", args.contract, args.contract_evidence, args.contract_reviewer, args.contract_context),
        ):
            if outcome and (
                not isinstance(reviewer, str) or not PROVENANCE_RE.fullmatch(reviewer)
                or not isinstance(context_id, str) or not PROVENANCE_RE.fullmatch(context_id)
            ):
                raise RazrError(f"{role} Critic requires valid reviewer and context provenance.")
            if not outcome and any(value is not None for value in (evidence_ref, reviewer, context_id)):
                raise RazrError(f"{role} Critic provenance requires a matching outcome.")
        technical_ref = args.technical_evidence if args.technical else technical.get("evidence_ref")
        contract_ref = args.contract_evidence if args.contract else contract.get("evidence_ref")
        technical_reviewer = args.technical_reviewer if args.technical else technical.get("reviewer")
        contract_reviewer = args.contract_reviewer if args.contract else contract.get("reviewer")
        technical_context = args.technical_context if args.technical else technical.get("context_id")
        contract_context = args.contract_context if args.contract else contract.get("context_id")
        if technical_ref and contract_ref and technical_ref == contract_ref:
            raise RazrError("Technical and Contract Critics must reference distinct evidence sections.")
        if technical_reviewer and contract_reviewer and technical_reviewer == contract_reviewer:
            raise RazrError("Technical and Contract Critics must have distinct reviewers.")
        if technical_context and contract_context and technical_context == contract_context:
            raise RazrError("Technical and Contract Critics must have distinct contexts.")
        recorded_at = now_iso()
        if args.technical:
            if not critic_evidence_matches(
                folder,
                args.technical_evidence,
                source_digest=source_digest,
                commit=commit,
                reviewer=args.technical_reviewer,
                context_id=args.technical_context,
                outcome=args.technical,
            ):
                raise RazrError("Technical Critic evidence must be a substantive matching provenance report.")
            state["critics"]["technical"] = {
                "outcome": args.technical,
                "candidate": source_digest,
                "reviewed_commit": commit,
                "evidence_ref": args.technical_evidence,
                "reviewer": args.technical_reviewer,
                "context_id": args.technical_context,
                "recorded_at": recorded_at,
            }
        if args.contract:
            if not critic_evidence_matches(
                folder,
                args.contract_evidence,
                source_digest=source_digest,
                commit=commit,
                reviewer=args.contract_reviewer,
                context_id=args.contract_context,
                outcome=args.contract,
            ):
                raise RazrError("Contract Critic evidence must be a substantive matching provenance report.")
            state["critics"]["contract"] = {
                "outcome": args.contract,
                "candidate": source_digest,
                "reviewed_commit": commit,
                "evidence_ref": args.contract_evidence,
                "reviewer": args.contract_reviewer,
                "context_id": args.contract_context,
                "recorded_at": recorded_at,
            }
        if not args.technical and not args.contract:
            raise RazrError("critics requires at least one Critic outcome.")
        state["generation"] += 1
        state["last_transition"] = {
            "from": "verify",
            "to": "verify",
            "decision": "record_critic_outcomes",
            "reason": None,
            "at": now_iso(),
        }
        save_state(folder, state, guard)
    print(f"Recorded Critic outcomes: {args.work_id}")


def ensure_gh_ready() -> None:
    if not shutil.which("gh"):
        raise RazrError("gh CLI is required for PR state recording and monitoring.")
    auth = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, check=False)
    if auth.returncode != 0:
        raise RazrError("gh auth is not ready; run `gh auth status` and authenticate first.")


def command_pr(args: argparse.Namespace) -> None:
    repo, config, work_root = project_context()
    ensure_gh_ready()
    with state_lock(work_root) as queue_guard:
        _command_pr_locked(args, repo, config, work_root, queue_guard)


def _command_pr_locked(
    args: argparse.Namespace,
    repo: Path,
    config: dict[str, Any],
    work_root: Path,
    queue_guard: StateLock,
) -> None:
    folder = work_item_dir(work_root, args.work_id)
    owner, name, _ = parse_pr_url(args.url)
    snapshot = snapshot_pr(args.work_id, args.url)
    live_url = snapshot.get("url")
    if live_url != args.url:
        raise RazrError("GitHub PR read-back URL does not match the requested PR.")
    live_head = snapshot.get("head_commit")
    if live_head != args.head_commit or not isinstance(live_head, str) or not FULL_SHA_RE.fullmatch(live_head):
        raise RazrError("GitHub PR read-back head does not match --head-commit.")
    if not git_commit_exists(repo, live_head):
        raise RazrError("PR head commit is not present locally; fetch it before recording PR state.")
    live_digest = source_tree_digest(repo, live_head, str(config["work_root"]))

    with state_lock(folder) as guard:
        state = read_state(folder)
        check_expectations(state, args)
        validation_errors = validate_work(work_root, args.work_id)
        if validation_errors:
            raise RazrError("PR recording requires valid current artifacts:\n- " + "\n- ".join(validation_errors))
        if state.get("stage") != "review":
            raise RazrError("PR state may be recorded only in Review.")
        candidate = state.get("candidate") if isinstance(state.get("candidate"), dict) else {}
        repository = candidate.get("repository")
        if not isinstance(repository, str) or repository.lower() != f"{owner}/{name}".lower():
            raise RazrError("PR repository does not match the candidate repository.")
        if live_digest != candidate.get("source_digest"):
            raise RazrError("PR head changes reviewed product source; rerun Build and both Critics.")
        for other in iter_work_item_dirs(work_root):
            if other.name == args.work_id or not (other / "state.yaml").exists():
                continue
            other_state = load_json(other / "state.yaml")
            other_delivery = other_state.get("delivery") if isinstance(other_state.get("delivery"), dict) else {}
            if other_delivery.get("pull_request") == args.url:
                raise RazrError(f"PR is already bound to work item {other.name}.")
        previous = state.get("delivery") if isinstance(state.get("delivery"), dict) else {}
        previous_outcome = previous.get("outcome")
        if previous_outcome == "merged" and args.outcome != "merged":
            raise RazrError("merged PR state is terminal.")
        if previous_outcome == "ready" and args.outcome == "pending":
            raise RazrError("stale pending state cannot regress a ready PR; reopen through transition instead.")
        if args.outcome in {"ready", "merged"}:
            critics = state.get("critics") if isinstance(state.get("critics"), dict) else {}
            if critics.get("technical", {}).get("outcome") != "PASS" or critics.get("contract", {}).get("outcome") != "PASS":
                raise RazrError("PR readiness requires both Critic passes.")
            required_checks = snapshot.get("required_checks") if isinstance(snapshot.get("required_checks"), list) else []
            incomplete_checks = [
                check for check in required_checks
                if not isinstance(check, dict)
                or str(check.get("bucket") or "").lower() not in {"pass", "skipping"}
            ]
            if incomplete_checks:
                raise RazrError("PR has pending or failing required checks.")
            unresolved = [thread for thread in snapshot.get("review_threads", []) if isinstance(thread, dict) and not thread.get("resolved")]
            if unresolved:
                raise RazrError("PR has unresolved review threads.")
            if snapshot.get("review_decision") == "CHANGES_REQUESTED":
                raise RazrError("PR still has requested changes.")
        if args.outcome == "ready":
            if snapshot.get("state") != "OPEN" or snapshot.get("draft"):
                raise RazrError("ready PR must be open and not draft.")
        elif args.outcome == "merged":
            if snapshot.get("state") != "MERGED" and not snapshot.get("merged_at"):
                raise RazrError("merged outcome requires live GitHub merge evidence.")
        elif args.outcome == "closed" and snapshot.get("state") not in {"CLOSED", "MERGED"}:
            raise RazrError("closed outcome requires live GitHub closed state.")

        state["delivery"] = {
            "pull_request": args.url,
            "head_commit": live_head,
            "source_digest": live_digest,
            "outcome": args.outcome,
            "review_iteration": previous.get("review_iteration", 0),
        }
        if args.outcome in {"ready", "merged"}:
            state["status"] = "complete"
        elif args.outcome == "closed":
            state["status"] = "blocked"
        else:
            state["status"] = "active"
        state["generation"] += 1
        state["last_transition"] = {
            "from": "review",
            "to": "review",
            "decision": f"pr_{args.outcome}",
            "reason": None,
            "at": now_iso(),
        }
        queue_guard.assert_owned()
        save_state(folder, state, guard)
    if args.outcome in {"ready", "merged", "closed"}:
        _promote_queued_locked(work_root, config, queue_guard)
    print(f"PR {args.work_id}: {args.outcome} {args.url}")
    print(f"Source digest: {live_digest}")


def run_gh(arguments: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        ["gh", *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RazrError(f"gh command failed: {' '.join(arguments)}: {result.stderr.strip()}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RazrError("gh returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise RazrError("gh JSON response must be an object.")
    return data


def run_gh_list(arguments: list[str], accepted_exit_codes: set[int] | None = None) -> list[Any]:
    result = subprocess.run(
        ["gh", *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    allowed = accepted_exit_codes or {0}
    if result.returncode not in allowed:
        raise RazrError(f"gh command failed: {' '.join(arguments)}: {result.stderr.strip()}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RazrError("gh returned invalid JSON.") from exc
    if not isinstance(data, list):
        raise RazrError("gh JSON response must be a list.")
    return data


def body_hash(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_actor(value: Any) -> str | None:
    return value.get("login") if isinstance(value, dict) and isinstance(value.get("login"), str) else None


def parse_pr_url(url: str) -> tuple[str, str, int]:
    match = re.fullmatch(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)(?:/)?", url)
    if not match:
        raise RazrError(f"unsupported GitHub PR URL: {url}")
    return match.group(1), match.group(2), int(match.group(3))


REVIEW_THREADS_QUERY = """
query($owner:String!,$name:String!,$number:Int!,$after:String){
  repository(owner:$owner,name:$name){
    pullRequest(number:$number){
      reviewThreads(first:100,after:$after){
        nodes{
          id
          isResolved
          comments(last:1){nodes{id author{login} updatedAt path body}}
        }
        pageInfo{hasNextPage endCursor}
      }
    }
  }
}
""".strip()


def snapshot_pr(work_id: str, url: str) -> dict[str, Any]:
    owner, name, number = parse_pr_url(url)
    fields = "number,url,state,isDraft,headRefOid,reviewDecision,statusCheckRollup,comments,reviews,mergedAt,closedAt,updatedAt"
    pr = run_gh(["pr", "view", url, "--json", fields])
    list_fields = ("comments", "reviews", "statusCheckRollup")
    for field in list_fields:
        if not isinstance(pr.get(field), list):
            raise RazrError(f"gh PR response {field} must be a list.")
    if not isinstance(pr.get("number"), int) or isinstance(pr.get("number"), bool):
        raise RazrError("gh PR response number must be an integer.")
    if not isinstance(pr.get("url"), str) or not isinstance(pr.get("state"), str):
        raise RazrError("gh PR response URL/state is invalid.")
    if not isinstance(pr.get("isDraft"), bool):
        raise RazrError("gh PR response isDraft must be boolean.")
    if not isinstance(pr.get("headRefOid"), str) or not FULL_SHA_RE.fullmatch(pr["headRefOid"]):
        raise RazrError("gh PR response headRefOid must be a full commit SHA.")
    required_raw = run_gh_list(
        ["pr", "checks", url, "--required", "--json", "name,state,bucket"],
        accepted_exit_codes={0, 1, 8},
    )
    required_checks = []
    for check in required_raw:
        if not isinstance(check, dict):
            raise RazrError("gh required-check entry must be an object.")
        if not all(isinstance(check.get(field), str) for field in ("name", "state", "bucket")):
            raise RazrError("gh required-check fields must be strings.")
        required_checks.append(
            {
                "name_sha256": body_hash(check.get("name")),
                "state": check.get("state"),
                "bucket": check.get("bucket"),
            }
        )
    required_checks.sort(
        key=lambda item: (
            str(item.get("name_sha256")),
            str(item.get("state")),
            str(item.get("bucket")),
        )
    )
    thread_nodes: list[Any] = []
    cursor: str | None = None
    while True:
        arguments = [
            "api",
            "graphql",
            "-f",
            f"query={REVIEW_THREADS_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={number}",
        ]
        if cursor is not None:
            arguments.extend(["-F", f"after={cursor}"])
        graphql = run_gh(arguments)
        data = graphql.get("data")
        repository = data.get("repository") if isinstance(data, dict) else None
        pull_request = repository.get("pullRequest") if isinstance(repository, dict) else None
        thread_page = pull_request.get("reviewThreads") if isinstance(pull_request, dict) else None
        if not isinstance(thread_page, dict):
            raise RazrError("gh GraphQL response is missing reviewThreads.")
        nodes = thread_page.get("nodes", [])
        if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
            raise RazrError("gh GraphQL reviewThreads.nodes must be a list of objects.")
        thread_nodes.extend(nodes)
        page_info = thread_page.get("pageInfo")
        if not isinstance(page_info, dict):
            raise RazrError("gh GraphQL reviewThreads.pageInfo must be an object.")
        has_next = page_info.get("hasNextPage")
        if not isinstance(has_next, bool):
            raise RazrError("gh GraphQL reviewThreads.pageInfo.hasNextPage must be boolean.")
        next_cursor = page_info.get("endCursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise RazrError("gh GraphQL reviewThreads.pageInfo.endCursor must be a string or null.")
        if not has_next:
            break
        if not next_cursor or next_cursor == cursor:
            raise RazrError("gh GraphQL review-thread pagination did not advance.")
        cursor = next_cursor
    comments = []
    for comment in pr["comments"]:
        if not isinstance(comment, dict):
            raise RazrError("gh PR comment entry must be an object.")
        comments.append(
            {
                "id": comment.get("id"),
                "author": normalize_actor(comment.get("author")),
                "updated_at": comment.get("updatedAt"),
                "body_sha256": body_hash(comment.get("body")),
            }
        )
    comments.sort(key=lambda item: str(item.get("id")))
    reviews = []
    for review in pr["reviews"]:
        if not isinstance(review, dict):
            raise RazrError("gh PR review entry must be an object.")
        reviews.append(
            {
                "id": review.get("id"),
                "author": normalize_actor(review.get("author")),
                "state": review.get("state"),
                "submitted_at": review.get("submittedAt"),
            }
        )
    reviews.sort(key=lambda item: str(item.get("id")))
    checks = []
    for check in pr["statusCheckRollup"]:
        if not isinstance(check, dict):
            raise RazrError("gh PR check entry must be an object.")
        checks.append(
            {
                "name_sha256": body_hash(check.get("name") or check.get("context")),
                "status": check.get("status"),
                "conclusion": check.get("conclusion") or check.get("state"),
            }
        )
    checks.sort(
        key=lambda item: (
            str(item.get("name_sha256")),
            str(item.get("status")),
            str(item.get("conclusion")),
        )
    )
    threads = []
    for thread in thread_nodes:
        comments_record = thread.get("comments")
        nodes = comments_record.get("nodes") if isinstance(comments_record, dict) else None
        if not isinstance(thread.get("id"), str) or not isinstance(thread.get("isResolved"), bool):
            raise RazrError("gh review-thread identity/resolution fields are invalid.")
        if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
            raise RazrError("gh review-thread comments must be a list of objects.")
        last = nodes[-1] if nodes else {}
        threads.append(
            {
                "id": thread.get("id"),
                "resolved": thread["isResolved"],
                "last_comment": {
                    "id": last.get("id"),
                    "author": normalize_actor(last.get("author")),
                    "updated_at": last.get("updatedAt"),
                    "path_sha256": body_hash(last.get("path")),
                    "body_sha256": body_hash(last.get("body")),
                },
            }
        )
    threads.sort(key=lambda item: str(item.get("id")))
    return {
        "id": work_id,
        "number": pr.get("number"),
        "url": pr.get("url") or url,
        "state": pr.get("state"),
        "draft": bool(pr.get("isDraft")),
        "head_commit": pr.get("headRefOid"),
        "review_decision": pr.get("reviewDecision"),
        "required_checks": required_checks,
        "checks": checks,
        "comments": comments,
        "reviews": reviews,
        "review_threads": threads,
        "merged_at": pr.get("mergedAt"),
        "closed_at": pr.get("closedAt"),
        "updated_at": pr.get("updatedAt"),
    }


def command_monitor(args: argparse.Namespace) -> None:
    if not args.snapshot:
        raise RazrError("monitor currently requires --snapshot.")
    ensure_gh_ready()
    _, _, work_root = project_context()
    targets: list[tuple[str, str]] = []
    for folder in iter_work_item_dirs(work_root):
        if not (folder / "state.yaml").exists():
            continue
        reject_canonical_symlinks(folder, include_materialized=True)
        state = load_json(folder / "state.yaml")
        delivery = state.get("delivery") if isinstance(state.get("delivery"), dict) else {}
        url = delivery.get("pull_request")
        if isinstance(url, str) and delivery.get("outcome") not in {"merged", "closed"}:
            targets.append((folder.name, url))
    snapshots = [snapshot_pr(work_id, url) for work_id, url in sorted(targets)]
    print(json.dumps(snapshots, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def render_initial_evidence(work_id: str, executable: dict[str, Any]) -> str:
    node_sections = []
    for node in executable.get("nodes", []):
        if isinstance(node, dict) and isinstance(node.get("id"), str):
            node_sections.append(f"## {node['id']}\n\nPending.\n")
    template = (PACKAGE_ROOT / "templates" / "evidence.md").read_text(encoding="utf-8")
    return template.replace("{{WORK_ID}}", work_id).replace("{{NODE_SECTIONS}}", "\n".join(node_sections) + "\n")


def command_approve(args: argparse.Namespace) -> None:
    _, config, work_root = project_context()
    with state_lock(work_root) as queue_guard:
        _command_approve_locked(args, config, work_root, queue_guard)


def _command_approve_locked(
    args: argparse.Namespace,
    config: dict[str, Any],
    work_root: Path,
    queue_guard: StateLock,
) -> None:
    folder = work_item_dir(work_root, args.work_id)
    if not folder.is_dir():
        raise RazrError(f"unknown work item: {args.work_id}")
    reject_canonical_symlinks(folder, include_materialized=(folder / "state.yaml").exists())
    spec, body = parse_spec(folder / "spec.md")
    errors: list[str] = []
    unsupported_spec = sorted(set(spec) - SPEC_FRONTMATTER_FIELDS)
    if unsupported_spec:
        errors.append("unsupported spec frontmatter fields: " + ", ".join(unsupported_spec))
    if not is_timestamp(spec.get("created_at")):
        errors.append("spec created_at must be an ISO-8601 timestamp with timezone.")
    missing_sections = missing_spec_sections(body)
    if missing_sections:
        errors.append("missing required spec sections: " + ", ".join(missing_sections))
    if spec.get("id") != args.work_id:
        errors.append("spec id does not match work item.")
    if spec.get("condition") != "approved":
        errors.append("spec condition must be approved.")
    if not isinstance(spec.get("revision"), int) or isinstance(spec.get("revision"), bool) or spec["revision"] < 1:
        errors.append("approved spec requires a positive revision.")
    priority = spec.get("priority")
    if not isinstance(priority, int) or isinstance(priority, bool) or not 0 <= priority <= 1000:
        errors.append("approved spec priority must be an integer from 0 through 1000.")
    if not spec.get("approved_by") or not is_timestamp(spec.get("approved_at")) or not spec.get("approval_ref"):
        errors.append("approved spec requires approved_by, approved_at, and approval_ref.")
    if spec.get("depth") not in {"tiny", "standard", "complex"}:
        errors.append("spec depth must be tiny, standard, or complex.")
    executable, executable_errors = validate_executable(folder, args.work_id, spec, body)
    errors.extend(executable_errors)
    state_path = folder / "state.yaml"
    evidence_path = folder / "evidence.md"
    old: dict[str, Any] | None = None
    if state_path.exists():
        old = read_state(folder)
        if old.get("stage") != "spec":
            errors.append("reapproval requires state stage spec.")
        old_revision = old.get("spec", {}).get("approved_revision") if isinstance(old.get("spec"), dict) else None
        if not isinstance(old_revision, int) or not isinstance(spec.get("revision"), int) or spec["revision"] <= old_revision:
            errors.append("reapproval requires a strictly newer revision.")
    if errors:
        raise RazrError("Approval gate failed:\n- " + "\n- ".join(errors))

    state = initial_state(args.work_id, spec, executable, config)
    if old is None:
        evidence = render_initial_evidence(args.work_id, executable)
    else:
        state["created_at"] = old.get("created_at")
        state["generation"] = int(old.get("generation", 0)) + 1
        prior_delivery = old.get("delivery") if isinstance(old.get("delivery"), dict) else {}
        state["delivery"] = {
            "pull_request": prior_delivery.get("pull_request"),
            "head_commit": None,
            "source_digest": None,
            "outcome": "pending" if prior_delivery.get("pull_request") else "not_due",
            "review_iteration": prior_delivery.get("review_iteration", 0),
        }
        state["last_transition"] = {
            "from": "spec",
            "to": "build",
            "decision": "reapproved_contract",
            "reason": f"spec revision {spec.get('revision')}",
            "at": now_iso(),
        }
        evidence = evidence_path.read_text(encoding="utf-8")
        heading = f"## Reapproval Revision {spec.get('revision')}"
        if heading not in evidence:
            evidence = evidence.rstrip() + f"\n\n{heading}\n\nCandidate and Critic targets reset for the reapproved contract.\n"
    queue_guard.assert_owned()
    state_errors = state_data_errors(folder, state)
    if state_errors:
        raise RazrError("refusing invalid approval state:\n- " + "\n- ".join(state_errors))
    with state_lock(folder) as guard:
        transactional_write(
            {
                evidence_path: evidence,
                state_path: json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            },
            guard,
        )
    print(f"Approved Razr work item: {args.work_id}")
    print(f"Status: {state['stage']} / {state['status']}")


def command_idea(args: argparse.Namespace) -> None:
    _, _, work_root = project_context()
    if not 0 <= args.priority <= 1000:
        raise RazrError("priority must be from 0 through 1000; lower runs first.")
    folder = work_item_dir(work_root, args.work_id)
    if folder.exists():
        raise RazrError(f"work item already exists: {args.work_id}")
    folder.mkdir(parents=True)
    created = now_iso()
    spec = (PACKAGE_ROOT / "templates" / "spec.md").read_text(encoding="utf-8")
    replacements = {
        "{{WORK_ID}}": args.work_id,
        "{{DEPTH}}": args.depth,
        "{{PRIORITY}}": str(args.priority),
        "{{CREATED_AT}}": created,
        "{{TITLE}}": args.title,
    }
    for marker, value in replacements.items():
        spec = spec.replace(marker, value)
    atomic_write(folder / "spec.md", spec)
    print(f"Created Razr idea: {args.work_id}")
    print(f"Depth: {args.depth}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Initialize project config and AGENTS routing")
    init.add_argument("--work-root", default=None)
    idea = sub.add_parser("idea", help="Create a spec-only work item")
    idea.add_argument("work_id")
    idea.add_argument("--title", required=True)
    idea.add_argument("--depth", choices=["tiny", "standard", "complex"], required=True)
    idea.add_argument("--priority", type=int, default=100)
    approve = sub.add_parser("approve", help="Validate approval and materialize state/evidence")
    approve.add_argument("work_id")
    status = sub.add_parser("status", help="List current work-item state")
    status.add_argument("work_id", nargs="?")
    status.add_argument("--json", action="store_true")
    validate = sub.add_parser("validate", help="Validate one or all work items")
    validate.add_argument("work_id", nargs="?")
    validate.add_argument("--all", action="store_true")
    transition = sub.add_parser("transition", help="Update node progress or apply a legal stage/status transition")
    transition.add_argument("work_id")
    transition.add_argument("--to", choices=sorted(STAGES))
    transition.add_argument("--status", choices=sorted(STATUSES))
    transition.add_argument("--expect-stage", choices=sorted(STAGES))
    transition.add_argument("--expect-generation", type=int, required=True)
    transition.add_argument("--node")
    transition.add_argument("--node-status")
    transition.add_argument("--result-ref")
    transition.add_argument("--decision")
    transition.add_argument("--reason")
    candidate = sub.add_parser("candidate", help="Record the exact integrated candidate")
    candidate.add_argument("work_id")
    candidate.add_argument("--repository", required=True)
    candidate.add_argument("--branch", required=True)
    candidate.add_argument("--commit", required=True)
    candidate.add_argument("--repo")
    candidate.add_argument("--expect-generation", type=int, required=True)
    critics = sub.add_parser("critics", help="Record independent Critic outcomes")
    critics.add_argument("work_id")
    critics.add_argument("--technical", choices=["PASS", "FAIL"])
    critics.add_argument("--technical-evidence")
    critics.add_argument("--technical-reviewer")
    critics.add_argument("--technical-context")
    critics.add_argument("--contract", choices=["PASS", "FAIL"])
    critics.add_argument("--contract-evidence")
    critics.add_argument("--contract-reviewer")
    critics.add_argument("--contract-context")
    critics.add_argument("--expect-generation", type=int, required=True)
    pr = sub.add_parser("pr", help="Record pull-request state")
    pr.add_argument("work_id")
    pr.add_argument("--url", required=True)
    pr.add_argument("--head-commit", required=True)
    pr.add_argument("--outcome", required=True, choices=["pending", "ready", "merged", "closed"])
    pr.add_argument("--expect-generation", type=int, required=True)
    monitor = sub.add_parser("monitor", help="Emit deterministic GitHub PR state")
    monitor.add_argument("--snapshot", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            command_init(args)
            return 0
        if args.command == "idea":
            command_idea(args)
            return 0
        if args.command == "approve":
            command_approve(args)
            return 0
        if args.command == "status":
            command_status(args)
            return 0
        if args.command == "validate":
            command_validate(args)
            return 0
        if args.command == "transition":
            command_transition(args)
            return 0
        if args.command == "candidate":
            command_candidate(args)
            return 0
        if args.command == "critics":
            command_critics(args)
            return 0
        if args.command == "pr":
            command_pr(args)
            return 0
        if args.command == "monitor":
            command_monitor(args)
            return 0
    except (RazrError, OSError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
