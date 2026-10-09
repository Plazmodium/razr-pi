"""Small, bounded GitHub delivery boundary owned by the public adapter.

This module observes Git/GitHub and, explicitly, creates one PR.  It never
writes Razr state, merges, force-pushes, deploys, or retries a lifecycle.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
REPO_RE = re.compile(r"^(?:git@github\.com:|https://github\.com/)([^/]+)/([^/]+?)(?:\.git)?\Z")
PR_RE = re.compile(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?\Z")
MAX_OUTPUT = 1024 * 1024

class DeliveryError(RuntimeError):
    pass


def _env() -> dict[str, str]:
    names = ("PATH", "HOME", "TMPDIR", "GH_HOST", "LANG", "LC_ALL", "LC_CTYPE",
             "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_OPTIONAL_LOCKS",
             "GIT_TERMINAL_PROMPT")
    result = {key: os.environ[key] for key in names if os.environ.get(key) is not None}
    result.setdefault("PATH", os.defpath)
    result.update({"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_CONFIG_GLOBAL": os.devnull, "GIT_OPTIONAL_LOCKS": "0",
                   "GIT_TERMINAL_PROMPT": "0"})
    return result


def _run(program: str, args: list[str], cwd: Path, *, timeout: float = 30.0,
         accepted_codes: set[int] | None = None) -> str:
    if len(args) > 32 or sum(len(x) for x in args) > 32768:
        raise DeliveryError("bounded request refused")
    try:
        completed = subprocess.run([program, *args], cwd=cwd, env=_env(), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeliveryError(f"{program} unavailable or timed out") from exc
    if len(completed.stdout.encode()) > MAX_OUTPUT or len(completed.stderr.encode()) > 262144:
        raise DeliveryError("bounded response refused")
    if completed.returncode not in (accepted_codes or {0}):
        # Never include gh stderr: it can contain credential-helper diagnostics.
        raise DeliveryError(f"{program} request failed")
    return completed.stdout.strip()


def _json(program: str, args: list[str], cwd: Path) -> Any:
    try:
        value = json.loads(_run(program, args, cwd))
    except json.JSONDecodeError as exc:
        raise DeliveryError("malformed GitHub response") from exc
    return value


def auth_preflight(root: Path) -> None:
    """Verify authentication without emitting gh's status or credential data."""
    _run("gh", ["auth", "status"], root)


def remote_identity(root: Path, remote: str = "origin") -> str:
    value = _run("git", ["remote", "get-url", remote], root)
    match = REPO_RE.fullmatch(value.removesuffix(".git"))
    if not match:
        raise DeliveryError("unsupported GitHub remote")
    return f"{match.group(1)}/{match.group(2)}"


def pushed_head(root: Path, branch: str, remote: str = "origin") -> str:
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,255}", branch) or ".." in branch:
        raise DeliveryError("invalid branch")
    value = _run("git", ["ls-remote", remote, f"refs/heads/{branch}"], root)
    rows = value.splitlines()
    if len(rows) != 1 or len(rows[0].split()) != 2 or not SHA_RE.fullmatch(rows[0].split()[0]):
        raise DeliveryError("remote branch head is missing or ambiguous")
    return rows[0].split()[0]


def source_identity(root: Path, commit: str, work_root: str = ".razr") -> str:
    if not SHA_RE.fullmatch(commit):
        raise DeliveryError("candidate commit must be a full Git object id")
    # Keep the NUL-delimited tree as bytes; text-mode stripping would alter identity.
    try:
        proc = subprocess.run(["git", "ls-tree", "-r", "-z", "--full-tree", commit], cwd=root,
                              env=_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeliveryError("candidate tree unavailable") from exc
    if proc.returncode or len(proc.stdout) > MAX_OUTPUT * 8:
        raise DeliveryError("candidate tree unavailable")
    prefix = work_root.rstrip("/") + "/"
    entries = []
    for row in proc.stdout.split(b"\0"):
        if not row:
            continue
        try:
            metadata, path = row.split(b"\t", 1)
            text_path = path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise DeliveryError("malformed Git tree") from exc
        if text_path in {"razr.yaml", "AGENTS.md"} or text_path.startswith(prefix):
            continue
        entries.append(metadata + b"\t" + path + b"\0")
    return "sha256:" + hashlib.sha256(b"".join(entries)).hexdigest()


def _pr_url(value: str) -> tuple[str, str, int]:
    match = PR_RE.fullmatch(value)
    if not match:
        raise DeliveryError("GitHub returned an invalid PR URL")
    return match.group(1), match.group(2), int(match.group(3))


def read_pr(root: Path, url: str) -> dict[str, Any]:
    owner, repo, number = _pr_url(url)
    fields = ("number,url,state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid,"
              "mergeStateStatus,mergeable,reviewDecision,reviewRequests,reviews,comments,"
              "statusCheckRollup,mergedAt,closedAt,updatedAt")
    value = _json("gh", ["pr", "view", url, "--json", fields], root)
    if not isinstance(value, dict) or value.get("url") != url or value.get("number") != number:
        raise DeliveryError("PR identity read-back mismatch")
    for key in ("baseRefOid", "headRefOid"):
        if not isinstance(value.get(key), str) or not SHA_RE.fullmatch(value[key]):
            raise DeliveryError("PR SHA read-back is invalid")
    for key in ("reviews", "comments", "reviewRequests", "statusCheckRollup"):
        if not isinstance(value.get(key), list):
            raise DeliveryError("PR collection read-back is invalid")
    try:
        checks = json.loads(_run("gh", ["pr", "checks", url, "--json", "name,state,bucket"], root,
                                 accepted_codes={0, 1, 8}))
    except json.JSONDecodeError as exc:
        raise DeliveryError("check read-back is malformed") from exc
    if not isinstance(checks, list) or any(not isinstance(row, dict) for row in checks):
        raise DeliveryError("check read-back is invalid")
    value["requiredChecks"] = checks
    value["repository"] = f"{owner}/{repo}"
    value["reviewThreads"] = _threads(root, owner, repo, number)
    return value


def _threads(root: Path, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
    query = ("query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){"
             "pullRequest(number:$n){reviewThreads(first:100){nodes{id isResolved} "
             "pageInfo{hasNextPage}}}}}")
    value = _json("gh", ["api", "graphql", "-f", f"query={query}", "-F", f"o={owner}",
                         "-F", f"r={repo}", "-F", f"n={number}"], root)
    try:
        page = value["data"]["repository"]["pullRequest"]["reviewThreads"]
        nodes = page["nodes"]
        if page["pageInfo"]["hasNextPage"]:
            raise DeliveryError("review-thread pagination is not bounded")
        if not isinstance(nodes, list) or any(not isinstance(x, dict) for x in nodes):
            raise KeyError("nodes")
        return nodes
    except (KeyError, TypeError) as exc:
        raise DeliveryError("review-thread read-back is invalid") from exc


def create_pr(root: Path, *, base: str, head: str, title: str, body: str) -> dict[str, Any]:
    if not base or not head or not title or len(title) > 256 or len(body) > 100000:
        raise DeliveryError("invalid PR request")
    # Creation is the only mutating boundary and has no --force/merge/deploy mode.
    url = _run("gh", ["pr", "create", "--base", base, "--head", head, "--title", title,
                       "--body", body], root)
    return read_pr(root, url)


def snapshot(root: Path, *, branch: str, base: str = "campaign/razr-pi-next") -> dict[str, Any]:
    """Read-only candidate snapshot used before any PR creation."""
    repository = remote_identity(root)
    commit = pushed_head(root, branch)
    return {"repository": repository, "branch": branch, "commit": commit,
            "source_digest": source_identity(root, commit), "base": base}
