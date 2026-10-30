#!/usr/bin/env python3
"""Candidate-bound Technical/Contract review preparation, launch, audit and recording.

The Razr core remains lifecycle authority. This helper does not authenticate
human approval or semantic support, and it never retries a model invocation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("review_primary", PACKAGE / "scripts/primary.py")
primary = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = primary
_spec.loader.exec_module(primary)
core = primary.core
COMMON_PROTOCOL = "razr-general-review-common-v1"
REPORT_PROTOCOL = "razr-general-review-report-v1"
ROLES = ("technical", "contract")
THINKING = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
RESOURCE_PATHS = (
    "scripts/review-flow.py",
    "scripts/primary.py",
    "runtime/scripts/razr.py",
)


class Refusal(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Refusal(message)


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = {}
    for key, item in pairs:
        require(key not in value, "duplicate JSON object field: " + key)
        value[key] = item
    return value


def invalid_json_constant(value: str) -> object:
    raise Refusal("non-finite JSON number refused: " + value)


def finite_float(value: str) -> float:
    parsed = float(value)
    require(math.isfinite(parsed), "non-finite JSON number refused: " + value)
    return parsed


def strict_json(raw: str) -> object:
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_json_constant,
                      parse_float=finite_float)


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def encoded(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def unlinked(path: str | Path, *, must_exist: bool = True) -> Path:
    value = Path(path).expanduser().absolute()
    parents = (value, *value.parents) if must_exist or value.exists() else tuple(value.parents)
    require(all(not item.is_symlink() for item in parents), f"linked path refused: {value}")
    if must_exist:
        return value.resolve(strict=True)
    return value.parent.resolve(strict=True) / value.name


def regular(path: str | Path) -> bytes:
    value = unlinked(path)
    require(value.is_file() and stat.S_ISREG(value.stat().st_mode), f"regular file required: {value}")
    return value.read_bytes()


def load(path: str | Path) -> object:
    return strict_json(regular(path).decode())


def save_new(path: Path, value: object) -> None:
    with path.open("xb") as handle:
        handle.write(encoded(value))
        handle.flush()
        os.fsync(handle.fileno())


def git(root: Path, *args: str, timeout: int = 30) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=timeout)
    require(result.returncode == 0, result.stderr.decode(errors="replace").strip() or "Git command failed")
    return result.stdout


def git_text(root: Path, *args: str) -> str:
    return git(root, *args).decode("utf-8").strip()


def safe_git_environment() -> None:
    for name in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        require(name not in os.environ, "Git environment override refused: " + name)


def ref_inventory(root: Path) -> list[dict[str, str]]:
    raw = git_text(root, "for-each-ref", "--format=%(refname)%09%(objectname)%09%(symref)", "refs")
    rows = []
    for line in raw.splitlines():
        parts = line.split("\t", 2); name, object_id = parts[:2]
        symbolic_target = parts[2] if len(parts) == 3 else ""
        require(name.startswith("refs/") and re.fullmatch(r"[0-9a-f]{40}", object_id),
                "invalid committed-history reference")
        rows.append({"name": name, "object": object_id, "symbolic_target": symbolic_target})
    return rows


def history_identity(root: Path) -> dict[str, object]:
    fsck = subprocess.run(["git", "fsck", "--full", "--no-dangling"], cwd=root,
                          capture_output=True, text=True, timeout=120)
    require(fsck.returncode == 0, "unsupported or corrupt committed-history refs")
    refs = ref_inventory(root)
    require(refs, "candidate committed history has no refs")
    commits = []
    for row in refs:
        peeled = subprocess.run(["git", "rev-parse", row["object"] + "^{commit}"], cwd=root,
                                capture_output=True, text=True, timeout=30)
        if peeled.returncode == 0:
            commits.append(peeled.stdout.strip())
    require(commits, "candidate committed history has no commits")
    graph = git(root, "rev-list", "--parents", "--topo-order", *sorted(set(commits)))
    return {"refs": refs, "graph_digest": digest(b"\n".join(sorted(graph.splitlines())) + b"\n")}


def materialize_history_refs(root: Path, expected: dict[str, object]) -> None:
    for row in reversed(ref_inventory(root)):
        if row["symbolic_target"]:
            git(root, "symbolic-ref", "--delete", row["name"])
        else:
            git(root, "update-ref", "-d", row["name"])
    for row in expected["refs"]:
        if not row["symbolic_target"]:
            git(root, "update-ref", row["name"], row["object"])
    for row in expected["refs"]:
        if row["symbolic_target"]:
            git(root, "symbolic-ref", row["name"], row["symbolic_target"])


def verify_history(root: Path, expected: dict[str, object]) -> None:
    require(git_text(root, "rev-parse", "--is-shallow-repository") == "false",
            "shallow candidate history refused")
    require(ref_inventory(root) == expected["refs"], "candidate committed-history refs mismatch")
    commits = []
    for row in expected["refs"]:
        present = subprocess.run(["git", "cat-file", "-e", row["object"]], cwd=root,
                                 capture_output=True, timeout=30)
        require(present.returncode == 0, "candidate history object missing")
        peeled = subprocess.run(["git", "rev-parse", row["object"] + "^{commit}"], cwd=root,
                                capture_output=True, text=True, timeout=30)
        if peeled.returncode == 0:
            commits.append(peeled.stdout.strip())
    graph = git(root, "rev-list", "--parents", "--topo-order", *sorted(set(commits)))
    require(digest(b"\n".join(sorted(graph.splitlines())) + b"\n") == expected["graph_digest"],
            "candidate committed history mismatch")
    fsck = subprocess.run(["git", "fsck", "--full", "--no-dangling"], cwd=root,
                          capture_output=True, text=True, timeout=120)
    require(fsck.returncode == 0, "candidate committed history integrity failure")


def source_inventory(root: Path, commit: str, work_relative: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in git(root, "ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
        if not record:
            continue
        metadata, raw_name = record.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split()
        name = raw_name.decode("utf-8")
        path = PurePosixPath(name)
        require(not path.is_absolute() and ".." not in path.parts and str(path) == name, "unsafe Git path")
        if name in ("AGENTS.md", "razr.yaml") or name == work_relative or name.startswith(work_relative + "/"):
            continue
        require(kind == "blob" and mode in ("100644", "100755"), "unsupported source entry: " + name)
        content = git(root, "cat-file", "blob", object_id)
        live = regular(root / name)
        require(content == live, "working source differs from candidate: " + name)
        require(bool((root / name).stat().st_mode & 0o111) == (mode == "100755"), "source mode drift: " + name)
        rows.append({"path": name, "mode": mode, "object": object_id,
                     "sha256": digest(content), "size_bytes": len(content)})
    require(rows, "candidate source inventory is empty")
    return rows


def freeze_data(target: str | Path, work_id: str) -> dict[str, object]:
    safe_git_environment()
    root = unlinked(target)
    require(root == Path(git_text(root, "rev-parse", "--show-toplevel")).resolve(), "target must be exact Git root")
    observation = primary.inspect(root, work_id)
    state = observation["state"]
    require(state["stage"] == "verify" and state["status"] == "active", "Verify/active state required")
    require(not observation["candidate_empty"], "bound candidate required")
    require(all(node["status"] == "complete" for node in state["nodes"].values()), "all executable nodes must be complete")
    require(all(row["outcome"] == "not_due" for row in state["critics"].values()), "fresh unrecorded critics required")
    require(state["delivery"]["pull_request"] is None, "PR already recorded")
    candidate = state["candidate"]
    commit = candidate["commit"]
    require(git_text(root, "rev-parse", "HEAD") == commit, "HEAD is not the recorded candidate")
    require(core.branch_commit(root, candidate["branch"]) == commit, "candidate branch moved")
    require(core.git_commit_exists(root, commit), "candidate commit is missing")
    config_raw = regular(root / "razr.yaml")
    config = core.load_json(root / "razr.yaml")
    work_root = core.validate_config(root, config)
    work_relative = work_root.relative_to(root).as_posix()
    item = core.work_item_dir(work_root, work_id)
    spec_raw = regular(item / "spec.md")
    executable_raw = regular(item / "executable.yaml")
    evidence_raw = regular(item / "evidence.md")
    agents_raw = regular(root / "AGENTS.md")
    executable = observation["executable"]
    require(state["spec"]["digest"] == digest(spec_raw), "approved spec identity drift")
    require(state["executable"]["digest"] == core.executable_digest(strict_json(executable_raw.decode())),
            "approved executable identity drift")
    require(core.source_tree_digest(root, commit, work_relative) == candidate["source_digest"], "source digest drift")
    for name, raw in (
        ("AGENTS.md", agents_raw), ("razr.yaml", config_raw),
        (f"{work_relative}/{work_id}/spec.md", spec_raw),
        (f"{work_relative}/{work_id}/executable.yaml", executable_raw),
    ):
        require(core.git_commit_blob(root, commit, name) == raw, "candidate excluded-input drift: " + name)
    require(not git(root, "diff", "--cached", "--name-only", "-z").strip(b"\0"), "nonempty Git index")
    changed = set(git(root, "diff", "HEAD", "--name-only", "-z").decode().split("\0")) - {""}
    allowed = {f"{work_relative}/{work_id}/state.yaml", f"{work_relative}/{work_id}/evidence.md"}
    require(changed <= allowed, "tracked changes outside canonical state/evidence")
    untracked = set(git(root, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")) - {""}
    allowed_locks = {f"{work_relative}/.state.lock", f"{work_relative}/{work_id}/.state.lock"}
    require(untracked <= allowed_locks, "untracked files require resolution")
    ignored = set(git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z").decode().split("\0")) - {""}
    require(ignored <= allowed_locks, "ignored files require resolution before review")
    validations = []
    for node in executable["nodes"]:
        for index, command in enumerate(node["validation"]):
            validations.append({"id": f"{node['id']}:{index + 1}", "node": node["id"], "command": command})
    require(validations, "declared validations required")
    inventory = source_inventory(root, commit, work_relative)
    history = history_identity(root)
    remotes = sorted(git_text(root, "remote").splitlines()) if git_text(root, "remote") else []
    data: dict[str, object] = {
        "schema_version": 1,
        "protocol": COMMON_PROTOCOL,
        "purpose": "candidate-bound neutral inputs; not human approval or semantic review support",
        "target": str(root),
        "work_id": work_id,
        "work_root": work_relative,
        "generation": state["generation"],
        "candidate": candidate,
        "contract": {
            "spec": {"path": f"{work_relative}/{work_id}/spec.md", "sha256": digest(spec_raw), "content": spec_raw.decode()},
            "executable": {"path": f"{work_relative}/{work_id}/executable.yaml", "sha256": digest(executable_raw), "content": executable_raw.decode()},
        },
        "excluded_inputs": {
            "AGENTS.md": {"sha256": digest(agents_raw), "content": agents_raw.decode()},
            "razr.yaml": {"sha256": digest(config_raw), "content": config_raw.decode()},
        },
        "canonical": {"state": state, "evidence_sha256": digest(evidence_raw)},
        "declared_validations": validations,
        "source_inventory": inventory,
        "committed_history": history,
        "remotes": remotes,
        "package_resources": {name: digest(regular(PACKAGE / name)) for name in RESOURCE_PATHS},
        "review_requirements": {
            "roles": list(ROLES),
            "fresh_contexts": True,
            "same_candidate": True,
            "peer_reports_forbidden": True,
            "builder_history_forbidden": True,
            "complete_report_required_for_outcome": True,
        },
        "limitations": [
            "Hashes establish identity, not execution, independence, semantic support or human approval.",
            "Reviewer tools execute with the current operating-system identity and are not a sandbox.",
            "Reviewers receive a detached candidate clone with committed Git history and no live-target path, but same-UID indirect discovery is not prevented.",
            "Tracked source symlinks, Git submodules, and ignored live files other than core lock tokens are unsupported and refused; valid linked Git worktrees are supported.",
            "GitHub observations and model/provider transport are external and non-atomic.",
        ],
    }
    require(primary.inspect(root, work_id) == observation, "canonical state changed during freeze")
    require(source_inventory(root, commit, work_relative) == inventory, "candidate source changed during freeze")
    require(history_identity(root) == history, "candidate committed history changed during freeze")
    return data


def freeze(target: str, work_id: str, output: str) -> tuple[Path, str]:
    root = unlinked(target)
    destination = unlinked(output, must_exist=False)
    require(not destination.exists(), "output already exists")
    require(not destination.is_relative_to(root) and not destination.is_relative_to(PACKAGE),
            "review output must be outside candidate and package")
    destination.mkdir(parents=True, exist_ok=False)
    common = destination / "common.json"
    save_new(common, freeze_data(root, work_id))
    value = digest(regular(common))
    save_new(destination / "identity.json", {"common": str(common), "common_digest": value})
    return common, value


def validate_common(path: str | Path, expected_digest: str) -> dict[str, object]:
    raw = regular(path)
    require(digest(raw) == expected_digest, "common input digest mismatch")
    data = strict_json(raw.decode())
    require(isinstance(data, dict) and isinstance(data.get("schema_version"), int)
            and not isinstance(data.get("schema_version"), bool) and data.get("schema_version") == 1
            and data.get("protocol") == COMMON_PROTOCOL, "unknown common input protocol")
    require(data == freeze_data(data["target"], data["work_id"]), "candidate/contract/common input drift")
    return data


def report_identity(common: dict[str, object], common_digest: str) -> dict[str, str]:
    candidate = common["candidate"]
    return {
        "work_id": common["work_id"], "repository": candidate["repository"],
        "branch": candidate["branch"], "commit": candidate["commit"],
        "source_digest": candidate["source_digest"], "common_digest": common_digest,
        "spec_digest": common["contract"]["spec"]["sha256"],
        "executable_digest": common["canonical"]["state"]["executable"]["digest"],
    }


def validate_report(value: object, common: dict[str, object], common_digest: str, role: str,
                    expected_context: str | None = None) -> dict[str, object]:
    require(role in ROLES, "invalid review role")
    require(isinstance(value, dict), "report must be a JSON object")
    fields = {"schema_version", "protocol", "role", "context", "identity", "review_status", "verdict",
              "reproduced_checks", "findings", "limitations", "evidence_access"}
    require(set(value) == fields, "report has missing or extra fields")
    require(isinstance(value["schema_version"], int) and not isinstance(value["schema_version"], bool)
            and value["schema_version"] == 1 and value["protocol"] == REPORT_PROTOCOL,
            "unknown report protocol")
    require(value["role"] == role, "report role mismatch")
    try:
        context = str(uuid.UUID(value["context"]))
    except (ValueError, TypeError) as exc:
        raise Refusal("report context must be a canonical UUID") from exc
    require(context == value["context"] and (expected_context is None or context == expected_context), "report context mismatch")
    require(value["identity"] == report_identity(common, common_digest), "report identity mismatch")
    require(value["review_status"] in ("complete", "incomplete"), "invalid review status")
    if value["review_status"] == "complete":
        require(value["verdict"] in ("PASS", "FAIL"), "complete report requires PASS or FAIL")
    else:
        require(value["verdict"] is None, "incomplete report cannot claim a verdict")
    for key in ("findings", "limitations", "evidence_access"):
        require(isinstance(value[key], list) and all(isinstance(row, str) and row.strip() for row in value[key]),
                "invalid report list: " + key)
    checks = value["reproduced_checks"]
    require(isinstance(checks, list), "reproduced_checks must be a list")
    expected = {(row["id"], row["command"]) for row in common["declared_validations"]}
    observed = set()
    for row in checks:
        require(isinstance(row, dict) and set(row) == {"id", "command", "exit_code", "summary"}, "invalid reproduced check")
        require(isinstance(row["exit_code"], int) and not isinstance(row["exit_code"], bool)
                and isinstance(row["summary"], str) and row["summary"].strip(), "invalid check result")
        key = (row["id"], row["command"])
        require(key not in observed, "duplicate reproduced check")
        observed.add(key)
    if value["review_status"] == "complete":
        require(value["evidence_access"], "complete report requires accessed-evidence summary")
        require(observed == expected, "complete report must reproduce every declared validation")
        if value["verdict"] == "PASS":
            require(all(row["exit_code"] == 0 for row in checks), "PASS report contains a failed validation")
    return value


def parse_jsonl(raw: bytes) -> list[dict[str, object]]:
    rows = []
    for line in raw.splitlines():
        require(line.strip(), "blank JSONL record")
        value = strict_json(line.decode())
        require(isinstance(value, dict), "JSONL record must be an object")
        rows.append(value)
    return rows


def assistant_text(message: object) -> str:
    require(isinstance(message, dict) and message.get("role") == "assistant", "final assistant message missing")
    blocks = message.get("content")
    require(isinstance(blocks, list), "assistant content must be a list")
    texts = [row["text"] for row in blocks if isinstance(row, dict) and row.get("type") == "text" and isinstance(row.get("text"), str)]
    require(len(texts) == 1, "final report must be one text block")
    return texts[0].strip()


def check_exit_marker(context: str, check_id: str) -> str:
    value = hashlib.sha256((context + "\0" + check_id).encode()).hexdigest()[:24]
    return "__RAZR_REVIEW_EXIT_" + value + "__"


def audit_events(events_raw: bytes, common: dict[str, object], common_digest: str, role: str,
                 expected_context: str | None = None, required_commands: list[str] | None = None,
                 required_read_paths: list[str] | None = None, forbidden_root: str | None = None, provider: str | None = None,
                 model: str | None = None) -> tuple[dict[str, object], dict[str, object]]:
    rows = parse_jsonl(events_raw)
    headers = [row for row in rows if row.get("type") == "session"]
    require(len(headers) == 1 and rows and rows[0] is headers[0], "exactly one leading Pi session header required")
    header = headers[0]
    context = header.get("id")
    require(isinstance(context, str) and (expected_context is None or context == expected_context),
            "session header context mismatch")
    require("parentSession" not in header, "forked/reused session refused")
    require(not any("retry" in str(row.get("type", "")) or "compaction" in str(row.get("type", "")) for row in rows),
            "retry or compaction event refused")
    settled = [row for row in rows if row.get("type") == "agent_settled"]
    require(len(settled) == 1 and settled[0].get("aborted") is False, "review run did not settle successfully")
    final_messages = [row["message"] for row in rows if row.get("type") == "message_end"
                      and isinstance(row.get("message"), dict) and row["message"].get("role") == "assistant"]
    require(final_messages, "assistant report message missing")
    final = final_messages[-1]
    if provider is not None:
        require(final.get("provider") == provider, "review provider mismatch")
    if model is not None:
        require(final.get("model") == model, "review model mismatch")
    original_report = assistant_text(final)
    report = validate_report(strict_json(original_report), common, common_digest, role, context)
    start_rows = [row for row in rows if row.get("type") == "tool_execution_start"]
    end_rows = [row for row in rows if row.get("type") == "tool_execution_end"]
    require(all(isinstance(row.get("toolCallId"), str) and row.get("toolCallId") for row in start_rows + end_rows),
            "tool execution id missing")
    require(len({row["toolCallId"] for row in start_rows}) == len(start_rows)
            and len({row["toolCallId"] for row in end_rows}) == len(end_rows),
            "duplicate tool execution record")
    starts = {row["toolCallId"]: row for row in start_rows}
    ends = {row["toolCallId"]: row for row in end_rows}
    require(starts.keys() == ends.keys(), "unpaired tool execution records")
    accesses = []
    observed_commands = []
    for key, start in starts.items():
        require(start.get("toolName") in ("read", "bash"), "unexpected reviewer tool")
        arguments = start.get("args")
        serialized = json.dumps(arguments, sort_keys=True)
        if forbidden_root is not None:
            require(forbidden_root not in serialized, "reviewer accessed live builder target")
        if start.get("toolName") == "bash" and isinstance(arguments, dict) and isinstance(arguments.get("command"), str):
            observed_commands.append(arguments["command"])
        require(isinstance(ends[key].get("isError"), bool), "tool execution error status must be boolean")
        result = ends[key].get("result")
        accesses.append({"tool_call_id": key, "tool": start.get("toolName"), "args": arguments,
                         "is_error": ends[key].get("isError"),
                         "result_content": result.get("content") if isinstance(result, dict) else None})
    if required_commands is not None:
        for required_path in required_read_paths or []:
            require(any(row["tool"] == "read" and row["is_error"] is False
                        and isinstance(row["args"], dict) and row["args"].get("path") == required_path
                        and any(isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"]
                                for item in (row["result_content"] or []))
                        for row in accesses), "reviewer did not successfully read required evidence: " + required_path)
        require(all(observed_commands.count(command) == 1 for command in required_commands),
                "declared validations were not each observed exactly once")
        reported = {(row["id"], row["command"]): row["exit_code"] for row in report["reproduced_checks"]}
        for declared, command in zip(common["declared_validations"], required_commands):
            call_id = next(key for key, start in starts.items()
                           if start.get("toolName") == "bash" and start.get("args", {}).get("command") == command)
            result = ends[call_id]
            details = result.get("result", {}).get("details", {}) if isinstance(result.get("result"), dict) else {}
            require(not (isinstance(details, dict) and details.get("truncation", {}).get("truncated")),
                    "truncated validation result refused")
            exit_code = reported[(declared["id"], declared["command"])]
            require(isinstance(result.get("isError"), bool), "validation tool result lacks error status")
            content = result.get("result", {}).get("content", []) if isinstance(result.get("result"), dict) else []
            text = "\n".join(row.get("text", "") for row in content
                              if isinstance(row, dict) and row.get("type") == "text")
            matches = re.findall(r"(?m)^" + re.escape(check_exit_marker(context, declared["id"])) + r"=(\d+)$", text)
            require(len(matches) == 1, "validation tool result lacks exact exit marker")
            observed_exit = int(matches[0])
            require(0 <= observed_exit <= 255 and observed_exit == exit_code,
                    "reported validation exit contradicts exact tool result")
            require(result["isError"] == (exit_code != 0), "reported validation exit contradicts tool status")
    return report, {"context": context, "accesses": accesses, "event_count": len(rows),
                    "original_report": original_report}


def supervise(argv: list[str], cwd: Path, environment: dict[str, str], timeout_seconds: int,
              max_output_bytes: int, watch_dir: Path | None = None) -> tuple[bytes, bytes, dict[str, object]]:
    started = datetime.now(timezone.utc)
    process = subprocess.Popen(argv, cwd=cwd, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    reason = None
    deadline = time.monotonic() + timeout_seconds
    while selector.get_map():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            reason = "timeout"
            break
        for key, _ in selector.select(min(remaining, 0.25)):
            chunk = os.read(key.fileobj.fileno(), 65536)
            if chunk:
                buffers[key.data].extend(chunk)
                if sum(len(value) for value in buffers.values()) > max_output_bytes:
                    reason = "output_limit"
                    break
            else:
                selector.unregister(key.fileobj)
        watched = 0 if watch_dir is None or not watch_dir.exists() else sum(
            path.stat().st_size for path in watch_dir.rglob("*") if path.is_file() and not path.is_symlink())
        if sum(len(value) for value in buffers.values()) + watched > max_output_bytes:
            reason = "output_limit"
        if reason:
            break
        if process.poll() is not None and not selector.get_map():
            break
    if reason:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    else:
        process.wait(timeout=5)
    selector.close()
    process.stdout.close()
    process.stderr.close()
    finished = datetime.now(timezone.utc)
    return bytes(buffers["stdout"]), bytes(buffers["stderr"]), {
        "started_at": started.isoformat(), "finished_at": finished.isoformat(),
        "elapsed_seconds": (finished - started).total_seconds(), "exit_code": process.returncode,
        "reason": reason, "captured_output_bytes": sum(len(value) for value in buffers.values()),
    }


def role_prompt(common_path: Path, common_digest: str, role: str, context: str,
                candidate_view: Path, check_invocations: list[dict[str, str]]) -> str:
    focus = ("implementation correctness, integration, safety and regressions" if role == "technical"
             else "approved requirements, evidence sufficiency, boundaries and delivery contract")
    return f"""You are the fresh independent Razr {role.title()} Critic.
Review {focus} for the exact candidate in {common_path} (redacted packet digest {digest(regular(common_path))}; authoritative common digest {common_digest}).
Read the redacted common JSON first. Inspect ONLY the detached candidate view at {candidate_view}; do not search for or access live builder paths.
Run each validation using exactly the corresponding bash command below and report the original declared command/id and exit code:
{json.dumps(check_invocations, indent=2)}
Do not edit files, repair findings, launch another model, access builder or peer reports, or claim human approval.
Return ONLY one JSON object with exactly these fields:
schema_version=1; protocol={REPORT_PROTOCOL}; role={role}; context={context};
identity exactly as specified by the common candidate; review_status complete|incomplete;
verdict PASS|FAIL only when complete, otherwise null; reproduced_checks as objects with id, command, exit_code, summary;
findings and limitations as nonempty-string arrays (arrays may be empty); evidence_access as a nonempty array faithfully summarizing accessed evidence.
PASS requires all declared validations to exit 0 and no blocking finding. Missing evidence or interrupted work is incomplete, not FAIL.
"""


def launch(common_path: str, common_digest: str, role: str, run_path: str, provider: str, model: str,
           thinking: str, timeout_seconds: int, max_output_bytes: int, pi_path: str | None) -> dict[str, object]:
    require(role in ROLES and thinking in THINKING, "invalid launch selection")
    require(timeout_seconds > 0 and max_output_bytes >= 1024, "invalid launch bounds")
    common = validate_common(common_path, common_digest)
    run = unlinked(run_path, must_exist=False)
    root = Path(common["target"])
    require(not run.exists() and not run.is_relative_to(root) and not run.is_relative_to(PACKAGE),
            "new run directory outside candidate/package required")
    claim_dir = Path(common_path).parent / "claims"
    claim_dir.mkdir(exist_ok=True)
    claim = claim_dir / f"{role}.json"
    require(not claim.exists(), role + " review allowance already consumed")
    context = str(uuid.uuid4())
    save_new(claim, {"role": role, "context": context, "common_digest": common_digest,
                     "status": "consumed-before-launch"})
    run.mkdir(parents=True, exist_ok=False)
    candidate_view = run / "candidate"
    clone = subprocess.run(["git", "clone", "--quiet", "--no-local", "--no-hardlinks", "--no-checkout",
                            str(root), str(candidate_view)], capture_output=True, text=True, timeout=120)
    require(clone.returncode == 0, "cannot create isolated candidate clone: " + clone.stderr.strip())
    checkout = subprocess.run(["git", "-C", str(candidate_view), "checkout", "--quiet", "--detach",
                               common["candidate"]["commit"]], capture_output=True, text=True, timeout=60)
    require(checkout.returncode == 0, "cannot checkout isolated candidate: " + checkout.stderr.strip())
    fetch = subprocess.run(["git", "-C", str(candidate_view), "fetch", "--quiet", "--force", "--no-tags", str(root),
                            "+refs/*:refs/*"], capture_output=True, text=True, timeout=120)
    require(fetch.returncode == 0, "cannot fetch complete committed history: " + fetch.stderr.strip())
    subprocess.run(["git", "-C", str(candidate_view), "remote", "remove", "origin"], capture_output=True, timeout=30)
    materialize_history_refs(candidate_view, common["committed_history"])
    require(git_text(candidate_view, "rev-parse", "HEAD") == common["candidate"]["commit"]
            and not git(candidate_view, "status", "--porcelain", "-z"), "isolated candidate mismatch")
    verify_history(candidate_view, common["committed_history"])
    check_invocations = []
    for row in common["declared_validations"]:
        marker = check_exit_marker(context, row["id"])
        command = ("cd -- " + shlex.quote(str(candidate_view)) + " && "
                   + "RAZR_REVIEW_CHECK_ID=" + shlex.quote(row["id"]) + " /bin/sh -c "
                   + shlex.quote(row["command"]) + "; razr_review_code=$?; printf '\\n"
                   + marker + "=%s\\n' \"$razr_review_code\"; exit \"$razr_review_code\"")
        check_invocations.append({"id": row["id"], "command": row["command"],
                                  "exit_marker": marker, "bash": command})
    driver = run / "driver"
    (driver / ".pi").mkdir(parents=True)
    settings = {"transport": "sse", "retry": {"enabled": False, "maxRetries": 0,
                "provider": {"maxRetries": 0}}, "compaction": {"enabled": False},
                "packages": [], "extensions": [], "skills": [], "prompts": [], "themes": []}
    save_new(driver / ".pi/settings.json", settings)
    reviewer_common = json.loads(json.dumps(common))
    reviewer_common["target"] = "<redacted-live-target>"
    reviewer_common["remotes"] = []
    reviewer_common["authoritative_common_digest"] = common_digest
    reviewer_common_path = run / "reviewer-common.json"
    save_new(reviewer_common_path, reviewer_common)
    system = run / "system.md"
    system.write_text("You are a read-only independent engineering critic. Follow the user review protocol exactly.\n")
    prompt = role_prompt(reviewer_common_path, common_digest, role, context, candidate_view, check_invocations)
    (run / "prompt.md").write_text(prompt)
    executable = pi_path or shutil.which("pi")
    require(bool(executable), "pi executable not found")
    argv = [str(executable), "--mode", "json", "--session-dir", str(run / "sessions"),
            "--session-id", context, "--provider", provider, "--model", model, "--thinking", thinking,
            "--tools", "read,bash", "--no-mcp", "--no-extensions", "--no-skills",
            "--no-prompt-templates", "--no-themes", "--no-context-files", "--approve",
            "--system-prompt", str(system), prompt]
    save_new(run / "launch.json", {"argv": argv, "cwd": str(driver), "role": role, "context": context,
                                    "provider": provider, "model": model, "thinking": thinking,
                                    "timeout_seconds": timeout_seconds, "max_output_bytes": max_output_bytes,
                                    "common_digest": common_digest,
                                    "reviewer_common_digest": digest(regular(reviewer_common_path)),
                                    "candidate_view": str(candidate_view),
                                    "check_invocations": check_invocations})
    allowed_environment = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TMPDIR",
                           "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS"}
    environment = {name: value for name, value in os.environ.items() if name in allowed_environment}
    stdout, stderr, execution = supervise(argv, driver, environment, timeout_seconds, max_output_bytes,
                                          run / "sessions")
    (run / "events.jsonl").write_bytes(stdout)
    (run / "stderr.log").write_bytes(stderr)
    save_new(run / "execution.json", execution)
    require(execution["reason"] is None and isinstance(execution["exit_code"], int)
            and not isinstance(execution["exit_code"], bool) and execution["exit_code"] == 0,
            "review process did not complete")
    session_files = list((run / "sessions").rglob("*.jsonl")) if (run / "sessions").is_dir() else []
    require(len(session_files) == 1, "exactly one saved fresh session required")
    session_rows = parse_jsonl(regular(session_files[0]))
    session_headers = [row for row in session_rows if row.get("type") == "session"]
    require(len(session_headers) == 1 and session_rows and session_rows[0] is session_headers[0]
            and session_headers[0].get("id") == context and "parentSession" not in session_headers[0],
            "saved session freshness mismatch")
    total = execution["captured_output_bytes"] + session_files[0].stat().st_size
    require(total <= max_output_bytes, "retained review output exceeds bound")
    required_reads = [str(reviewer_common_path),
                      str(candidate_view / common["source_inventory"][0]["path"])]
    report, audit = audit_events(stdout, common, common_digest, role, context,
                                 [row["bash"] for row in check_invocations], required_reads,
                                 str(root), provider, model)
    (run / "report-original.txt").write_text(audit.pop("original_report"))
    save_new(run / "report.json", report)
    save_new(run / "structural-audit.json", {**audit, "events_digest": digest(stdout),
             "session": str(session_files[0]), "session_digest": digest(regular(session_files[0])),
             "report_original_digest": digest(regular(run / "report-original.txt")),
             "candidate_commit": common["candidate"]["commit"], "common_digest": common_digest})
    validate_common(common_path, common_digest)
    return {"run": str(run), "role": role, "context": context, "review_status": report["review_status"],
            "verdict": report["verdict"], "report_digest": digest(regular(run / "report-original.txt"))}


def audit_run(common_path: str, common_digest: str, run_path: str, role: str) -> tuple[dict[str, object], dict[str, object]]:
    common = validate_common(common_path, common_digest)
    run = unlinked(run_path)
    require(run.is_dir(), "retained review run directory required")
    launch_data = load(run / "launch.json")
    require(isinstance(launch_data, dict) and launch_data.get("role") == role
            and launch_data.get("common_digest") == common_digest
            and launch_data.get("reviewer_common_digest") == digest(regular(run / "reviewer-common.json")),
            "launch identity mismatch")
    context = launch_data.get("context")
    claim = Path(common_path).parent / "claims" / f"{role}.json"
    require(load(claim) == {"role": role, "context": context, "common_digest": common_digest,
                            "status": "consumed-before-launch"}, "missing or changed role claim")
    execution = load(run / "execution.json")
    require(execution.get("reason") is None and isinstance(execution.get("exit_code"), int)
            and not isinstance(execution.get("exit_code"), bool) and execution.get("exit_code") == 0,
            "review execution incomplete")
    required_reads = [str(run / "reviewer-common.json"),
                      str(Path(launch_data["candidate_view"]) / common["source_inventory"][0]["path"])]
    report, audit = audit_events(regular(run / "events.jsonl"), common, common_digest, role, context,
                                 [row["bash"] for row in launch_data["check_invocations"]], required_reads,
                                 common["target"], launch_data["provider"], launch_data["model"])
    require(audit.pop("original_report") == regular(run / "report-original.txt").decode(), "original report drift")
    require(report == load(run / "report.json"), "parsed report drift")
    sessions = list((run / "sessions").rglob("*.jsonl"))
    require(len(sessions) == 1, "saved session count changed")
    session_rows = parse_jsonl(regular(sessions[0]))
    session_headers = [row for row in session_rows if row.get("type") == "session"]
    require(len(session_headers) == 1 and session_rows and session_rows[0] is session_headers[0]
            and session_headers[0].get("id") == context and "parentSession" not in session_headers[0],
            "saved session header changed")
    messages = [row.get("message") for row in session_rows if row.get("type") == "message"
                and isinstance(row.get("message"), dict)]
    user_texts = []
    session_calls = {}
    session_results = {}
    assistant_texts = []
    for message in messages:
        content = message.get("content")
        if message.get("role") == "user":
            if isinstance(content, str):
                user_texts.append(content)
            elif isinstance(content, list):
                user_texts.extend(row.get("text") for row in content if isinstance(row, dict) and row.get("type") == "text")
        if message.get("role") == "assistant" and isinstance(content, list):
            text_blocks = [row.get("text") for row in content if isinstance(row, dict) and row.get("type") == "text"]
            assistant_texts.extend(text for text in text_blocks if isinstance(text, str))
            for row in content:
                if isinstance(row, dict) and row.get("type") == "toolCall":
                    require(isinstance(row.get("id"), str) and row["id"] not in session_calls,
                            "duplicate saved-session tool call")
                    session_calls[row["id"]] = {"tool": row.get("name"), "args": row.get("arguments")}
        if message.get("role") == "toolResult":
            call_id = message.get("toolCallId")
            require(isinstance(call_id, str) and call_id not in session_results,
                    "duplicate saved-session tool result")
            require(isinstance(message.get("isError"), bool), "saved-session tool status must be boolean")
            session_results[call_id] = {"is_error": message["isError"],
                                        "content": message.get("content")}
    require(regular(run / "prompt.md").decode() in user_texts, "saved session prompt mismatch")
    require(regular(run / "report-original.txt").decode() in assistant_texts, "saved session report mismatch")
    event_accesses = {row["tool_call_id"]: row for row in audit["accesses"]}
    require(set(session_calls) == set(event_accesses) == set(session_results), "saved session tool-call set mismatch")
    for call_id, access in event_accesses.items():
        require(session_calls[call_id] == {"tool": access["tool"], "args": access["args"]},
                "saved session tool invocation mismatch")
        require(session_results[call_id] == {"is_error": bool(access["is_error"]),
                                             "content": access["result_content"]},
                "saved session tool result mismatch")
    structural = load(run / "structural-audit.json")
    require(structural["events_digest"] == digest(regular(run / "events.jsonl"))
            and structural["session_digest"] == digest(regular(sessions[0]))
            and structural["report_original_digest"] == digest(regular(run / "report-original.txt")),
            "retained review evidence drift")
    candidate_view = Path(launch_data["candidate_view"])
    require(git_text(candidate_view, "rev-parse", "HEAD") == common["candidate"]["commit"]
            and not git(candidate_view, "remote")
            and not git(candidate_view, "status", "--porcelain", "-z"), "isolated candidate changed")
    verify_history(candidate_view, common["committed_history"])
    return report, {"role": role, "context": context, "review_status": report["review_status"],
                    "verdict": report["verdict"], "structurally_valid": True,
                    "events_digest": structural["events_digest"], "session_digest": structural["session_digest"],
                    "report_digest": structural["report_original_digest"]}


def reproduce(common_path: str, common_digest: str, output_path: str, primary_context: str,
              timeout_seconds: int, max_output_bytes: int) -> dict[str, object]:
    common = validate_common(common_path, common_digest)
    try:
        require(str(uuid.UUID(primary_context)) == primary_context, "primary context must be canonical UUID")
    except ValueError as exc:
        raise Refusal("primary context must be canonical UUID") from exc
    require(0 < timeout_seconds <= 3600 and 1024 <= max_output_bytes <= 104857600,
            "invalid primary reproduction bounds")
    output = Path(output_path)
    require(not output.exists(), "primary reproduction output already exists")
    output.mkdir(parents=True)
    candidate = output / "candidate"
    root = Path(common["target"])
    clone = subprocess.run(["git", "clone", "--quiet", "--no-local", "--no-hardlinks", "--no-checkout",
                            str(root), str(candidate)], capture_output=True, text=True, timeout=120)
    require(clone.returncode == 0, "cannot create primary candidate clone: " + clone.stderr.strip())
    checkout = subprocess.run(["git", "checkout", "--quiet", "--detach", common["candidate"]["commit"]],
                              cwd=candidate, capture_output=True, text=True, timeout=120)
    require(checkout.returncode == 0, "cannot checkout primary candidate: " + checkout.stderr.strip())
    fetch = subprocess.run(["git", "fetch", "--quiet", "--force", "--no-tags", str(root),
                            "+refs/*:refs/*"], cwd=candidate,
                           capture_output=True, text=True, timeout=120)
    require(fetch.returncode == 0, "cannot fetch complete primary history: " + fetch.stderr.strip())
    git(candidate, "remote", "remove", "origin")
    materialize_history_refs(candidate, common["committed_history"])
    verify_history(candidate, common["committed_history"])
    home = output / "home"; temporary = output / "tmp"; home.mkdir(); temporary.mkdir()
    environment = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(home), "TMPDIR": str(temporary), "LANG": "C.UTF-8",
                   "LC_ALL": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                   "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    checks = []
    for index, declared in enumerate(common["declared_validations"]):
        stdout, stderr, execution = supervise(["/bin/sh", "-c", declared["command"]], candidate, environment,
                                               timeout_seconds, max_output_bytes)
        stdout_path = output / f"check-{index}-stdout.bin"; stderr_path = output / f"check-{index}-stderr.bin"
        stdout_path.write_bytes(stdout); stderr_path.write_bytes(stderr)
        checks.append({"id": declared["id"], "command": declared["command"],
                       "exit_code": execution["exit_code"], "stdout": str(stdout_path),
                       "stdout_digest": digest(stdout), "stderr": str(stderr_path),
                       "stderr_digest": digest(stderr), "execution": execution})
    value = {"schema_version": 1, "primary_context": primary_context, "common_digest": common_digest,
             "candidate": common["candidate"], "environment": {"path_digest": digest(environment["PATH"].encode())},
             "checks": checks,
             "limitations": ["Primary reproduction executes declared repository commands; it is not a sandbox."]}
    save_new(output / "reproduction.json", value)
    return value


def validate_primary_reproduction(reproduction: dict[str, object], common: dict[str, object],
                                  report: dict[str, object]) -> None:
    expected = {(row["id"], row["command"]) for row in common["declared_validations"]}
    observed = {}
    for row in reproduction["checks"]:
        require(isinstance(row, dict) and set(row) == {"id", "command", "exit_code", "stdout", "stdout_digest",
                                                       "stderr", "stderr_digest", "execution"},
                "invalid primary reproduction check")
        require(digest(regular(row["stdout"])) == row["stdout_digest"]
                and digest(regular(row["stderr"])) == row["stderr_digest"], "primary reproduction output drift")
        require(isinstance(row["exit_code"], int) and not isinstance(row["exit_code"], bool)
                and isinstance(row["execution"].get("exit_code"), int)
                and not isinstance(row["execution"].get("exit_code"), bool)
                and row["execution"].get("reason") is None
                and row["execution"].get("exit_code") == row["exit_code"],
                "primary reproduction result mismatch")
        key = (row["id"], row["command"])
        require(key not in observed, "duplicate primary reproduction check")
        observed[key] = row["exit_code"]
    require(set(observed) == expected, "primary reproduction does not prove every declared validation")
    reported = {(row["id"], row["command"]): row["exit_code"] for row in report["reproduced_checks"]}
    if report["verdict"] == "PASS":
        require(all(exit_code == 0 for exit_code in observed.values()), "primary reproduction contradicts PASS")
    else:
        require(observed == reported, "primary reproduction does not reproduce critic FAIL checks")


def support_run(common_path: str, common_digest: str, run_path: str, role: str,
                primary_context: str, decision_ref: str, reproduction_path: str) -> dict[str, object]:
    report, audit = audit_run(common_path, common_digest, run_path, role)
    require(report["review_status"] == "complete", "incomplete report cannot be supported")
    try:
        require(str(uuid.UUID(primary_context)) == primary_context and primary_context != report["context"],
                "primary context must be a distinct canonical UUID")
    except ValueError as exc:
        raise Refusal("primary context must be canonical UUID") from exc
    require(isinstance(decision_ref, str) and decision_ref.strip(), "primary decision reference required")
    reproduction = load(reproduction_path)
    require(isinstance(reproduction, dict) and set(reproduction) == {"schema_version", "primary_context", "common_digest", "candidate", "environment", "checks", "limitations"}
            and isinstance(reproduction["schema_version"], int) and not isinstance(reproduction["schema_version"], bool)
            and reproduction["schema_version"] == 1 and reproduction["primary_context"] == primary_context,
            "invalid primary reproduction record")
    common = validate_common(common_path, common_digest)
    require(reproduction.get("common_digest") == common_digest and reproduction.get("candidate") == common["candidate"]
            and isinstance(reproduction.get("environment"), dict)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", reproduction["environment"].get("path_digest", "")),
            "primary reproduction identity mismatch")
    validate_primary_reproduction(reproduction, common, report)
    support_path = Path(run_path) / "outcome-support.json"
    value = {"schema_version": 1, "role": role, "context": report["context"],
             "primary_context": primary_context, "decision_ref": decision_ref,
             "common_digest": common_digest, "verdict": report["verdict"],
             "events_digest": audit["events_digest"], "session_digest": audit["session_digest"],
             "report_digest": audit["report_digest"], "reproduction": str(unlinked(reproduction_path)),
             "reproduction_digest": digest(regular(reproduction_path)), "supported": True}
    save_new(support_path, value)
    return value


def evidence_section(report: dict[str, object], heading: str) -> str:
    identity = report["identity"]
    checks = "\n".join(f"- {row['command']} => {'PASS' if row['exit_code'] == 0 else 'FAIL'} ({row['summary']})"
                       for row in report["reproduced_checks"])
    findings = "\n".join("- " + row for row in report["findings"]) or "- None."
    limitations = "\n".join("- " + row for row in report["limitations"]) or "- None reported."
    return (f"\n## {heading}\n\nCandidate: {identity['source_digest']}\nReviewed commit: {identity['commit']}\n"
            f"Reviewer: {report['role']}\nContext: {report['context']}\nVerdict: {report['verdict']}\n"
            f"Review status: {report['review_status']}\nCommon inputs: {identity['common_digest']}\n\n"
            f"### Reproduced checks\n{checks}\n\n### Findings\n{findings}\n\n### Limitations\n{limitations}\n")


def supported_run(common_path: str, common_digest: str, run_path: str, role: str) -> dict[str, object]:
    report, audit = audit_run(common_path, common_digest, run_path, role)
    support = load(Path(run_path) / "outcome-support.json")
    require(isinstance(support, dict) and support.get("schema_version") == 1 and support.get("supported") is True
            and support.get("role") == role and support.get("context") == report["context"]
            and support.get("common_digest") == common_digest and support.get("verdict") == report["verdict"]
            and support.get("events_digest") == audit["events_digest"]
            and support.get("session_digest") == audit["session_digest"]
            and support.get("report_digest") == audit["report_digest"], "primary support record mismatch")
    require(digest(regular(support["reproduction"])) == support["reproduction_digest"], "primary reproduction drift")
    reproduction = load(support["reproduction"])
    for row in reproduction.get("checks", []):
        require(isinstance(row, dict) and row.get("execution", {}).get("reason") is None
                and row.get("execution", {}).get("exit_code") == row.get("exit_code")
                and digest(regular(row["stdout"])) == row.get("stdout_digest")
                and digest(regular(row["stderr"])) == row.get("stderr_digest"),
                "primary reproduction output drift")
    return report


def record(common_path: str, common_digest: str, technical_path: str, contract_path: str) -> dict[str, object]:
    common = validate_common(common_path, common_digest)
    technical = supported_run(common_path, common_digest, technical_path, "technical")
    contract = supported_run(common_path, common_digest, contract_path, "contract")
    for report in (technical, contract):
        require(report["review_status"] == "complete", "incomplete report is not recordable")
    require(technical["context"] != contract["context"], "critic contexts must be distinct")
    technical_support = load(Path(technical_path) / "outcome-support.json")
    contract_support = load(Path(contract_path) / "outcome-support.json")
    require(technical_support["primary_context"] == contract_support["primary_context"]
            and technical_support["primary_context"] not in {technical["context"], contract["context"]},
            "one distinct primary context must support both critic outcomes")
    root = Path(common["target"])
    work_id = common["work_id"]
    observation = primary.inspect(root, work_id)
    generation = observation["state"]["generation"]
    work_root = core.validate_config(root, core.load_json(root / "razr.yaml"))
    item = core.work_item_dir(work_root, work_id)
    evidence = item / "evidence.md"
    old = regular(evidence)
    headings = {role: f"{role}-general-review-{report['context']}" for role, report in
                (("technical", technical), ("contract", contract))}
    addition = evidence_section(technical, headings["technical"]) + evidence_section(contract, headings["contract"])
    with evidence.open("ab") as handle:
        handle.write(addition.encode())
        handle.flush()
        os.fsync(handle.fileno())
    require(regular(evidence) == old + addition.encode(), "unexpected evidence append")
    argv = [sys.executable, str(PACKAGE / "runtime/scripts/razr.py"), "critics", work_id,
            "--technical", technical["verdict"], "--technical-evidence", f"evidence.md#{headings['technical']}",
            "--technical-reviewer", "technical", "--technical-context", technical["context"],
            "--contract", contract["verdict"], "--contract-evidence", f"evidence.md#{headings['contract']}",
            "--contract-reviewer", "contract", "--contract-context", contract["context"],
            "--expect-generation", str(generation)]
    result = subprocess.run(argv, cwd=root, capture_output=True, text=True)
    require(result.returncode == 0, result.stderr.strip() or "core critic recording failed; evidence retained")
    readback = primary.inspect(root, work_id)
    require(readback["state"]["generation"] == generation + 1, "critic generation read-back mismatch")
    return {"command": argv, "stdout": result.stdout, "generation": readback["state"]["generation"],
            "critics": readback["state"]["critics"], "human_review_transition": "pending"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    freeze_parser = sub.add_parser("freeze")
    freeze_parser.add_argument("--target", required=True); freeze_parser.add_argument("--work-id", required=True)
    freeze_parser.add_argument("--output", required=True)
    for name in ("validate", "audit"):
        item = sub.add_parser(name); item.add_argument("--common", required=True); item.add_argument("--common-digest", required=True)
        if name == "audit":
            item.add_argument("--run", required=True); item.add_argument("--role", choices=ROLES, required=True)
    launch_parser = sub.add_parser("launch")
    launch_parser.add_argument("--common", required=True); launch_parser.add_argument("--common-digest", required=True)
    launch_parser.add_argument("--role", choices=ROLES, required=True); launch_parser.add_argument("--run", required=True)
    launch_parser.add_argument("--provider", required=True); launch_parser.add_argument("--model", required=True)
    launch_parser.add_argument("--thinking", choices=THINKING, required=True)
    launch_parser.add_argument("--timeout-seconds", type=int, required=True)
    launch_parser.add_argument("--max-output-bytes", type=int, required=True)
    launch_parser.add_argument("--pi")
    launch_parser.add_argument("--authorize-live", action="store_true",
                               help="mechanical acknowledgement only; does not authenticate human authorization")
    reproduce_parser = sub.add_parser("reproduce")
    reproduce_parser.add_argument("--common", required=True); reproduce_parser.add_argument("--common-digest", required=True)
    reproduce_parser.add_argument("--output", required=True); reproduce_parser.add_argument("--primary-context", required=True)
    reproduce_parser.add_argument("--timeout-seconds", type=int, required=True)
    reproduce_parser.add_argument("--max-output-bytes", type=int, required=True)
    support_parser = sub.add_parser("support")
    support_parser.add_argument("--common", required=True); support_parser.add_argument("--common-digest", required=True)
    support_parser.add_argument("--run", required=True); support_parser.add_argument("--role", choices=ROLES, required=True)
    support_parser.add_argument("--primary-context", required=True); support_parser.add_argument("--decision-ref", required=True)
    support_parser.add_argument("--reproduction", required=True)
    record_parser = sub.add_parser("record")
    record_parser.add_argument("--common", required=True); record_parser.add_argument("--common-digest", required=True)
    record_parser.add_argument("--technical-run", required=True); record_parser.add_argument("--contract-run", required=True)
    args = parser.parse_args()
    try:
        if args.action == "freeze":
            path, value = freeze(args.target, args.work_id, args.output)
            output = {"common": str(path), "common_digest": value, "live_model_used": False}
        elif args.action == "validate":
            common = validate_common(args.common, args.common_digest)
            output = {"status": "PASS", "candidate": common["candidate"], "generation": common["generation"]}
        elif args.action == "audit":
            _, output = audit_run(args.common, args.common_digest, args.run, args.role)
        elif args.action == "launch":
            require(args.authorize_live, "launch requires actual human model/spending authorization plus --authorize-live")
            output = launch(args.common, args.common_digest, args.role, args.run, args.provider, args.model,
                            args.thinking, args.timeout_seconds, args.max_output_bytes, args.pi)
        elif args.action == "reproduce":
            output = reproduce(args.common, args.common_digest, args.output, args.primary_context,
                               args.timeout_seconds, args.max_output_bytes)
        elif args.action == "support":
            output = support_run(args.common, args.common_digest, args.run, args.role, args.primary_context,
                                 args.decision_ref, args.reproduction)
        else:
            output = record(args.common, args.common_digest, args.technical_run, args.contract_run)
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    except (Refusal, core.RazrError, OSError, ValueError, KeyError, TypeError,
            subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print("STOP: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
