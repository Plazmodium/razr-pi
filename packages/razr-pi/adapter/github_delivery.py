"""Bounded, adapter-owned GitHub delivery observations and one safe PR create."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
from typing import Any

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
REPO_RE = re.compile(r"^(?:git@github\.com:|https://github\.com/)([^/]+)/([^/]+)\Z")
PR_RE = re.compile(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?\Z")
MAX_STDOUT = 1024 * 1024
MAX_STDERR = 262144
MAX_THREADS_PAGES = 10


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


def _capture(program: str, args: list[str], cwd: Path, *, timeout: float = 30.0,
             accepted_codes: set[int] | None = None) -> tuple[bytes, bytes]:
    if len(args) > 32 or sum(len(x) for x in args) > 32768:
        raise DeliveryError("bounded request refused")
    try:
        process = subprocess.Popen([program, *args], cwd=cwd, env=_env(), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    except OSError as exc:
        raise DeliveryError(f"{program} unavailable") from exc
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "out")
    selector.register(process.stderr, selectors.EVENT_READ, "err")
    buffers = {"out": bytearray(), "err": bytearray()}
    limits = {"out": MAX_STDOUT, "err": MAX_STDERR}
    deadline = __import__("time").monotonic() + timeout
    overflow = False
    while selector.get_map():
        remaining = deadline - __import__("time").monotonic()
        if remaining <= 0:
            process.kill()
            process.wait()
            raise DeliveryError(f"{program} timed out")
        for key, _ in selector.select(remaining):
            chunk = key.fileobj.read(4096)
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            name = key.data
            if len(buffers[name]) + len(chunk) > limits[name]:
                overflow = True
                process.kill()
                break
            buffers[name].extend(chunk)
        if overflow:
            break
    if overflow:
        process.wait()
        raise DeliveryError("bounded response refused")
    code = process.wait()
    if code not in (accepted_codes or {0}):
        # stderr is deliberately never included: credential helpers can write there.
        raise DeliveryError(f"{program} request failed")
    return bytes(buffers["out"]), bytes(buffers["err"])


def _run(program: str, args: list[str], cwd: Path, *, timeout: float = 30.0,
         accepted_codes: set[int] | None = None) -> str:
    stdout, _ = _capture(program, args, cwd, timeout=timeout, accepted_codes=accepted_codes)
    try:
        return stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise DeliveryError("non-UTF-8 response") from exc


def _json(program: str, args: list[str], cwd: Path, **kwargs: Any) -> Any:
    try:
        value = json.loads(_run(program, args, cwd, **kwargs))
    except json.JSONDecodeError as exc:
        raise DeliveryError("malformed GitHub response") from exc
    return value


def auth_preflight(root: Path) -> None:
    _run("gh", ["auth", "status"], root)


def remote_identity(root: Path, remote: str = "origin") -> str:
    value = _run("git", ["remote", "get-url", remote], root).removesuffix(".git")
    match = REPO_RE.fullmatch(value)
    if not match:
        raise DeliveryError("unsupported GitHub remote")
    return f"{match.group(1)}/{match.group(2)}"


def pushed_head(root: Path, branch: str, remote: str = "origin") -> str:
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,255}", branch) or ".." in branch:
        raise DeliveryError("invalid branch")
    rows = _run("git", ["ls-remote", remote, f"refs/heads/{branch}"], root).splitlines()
    if len(rows) != 1 or len(rows[0].split()) != 2 or not SHA_RE.fullmatch(rows[0].split()[0]):
        raise DeliveryError("remote branch head is missing or ambiguous")
    return rows[0].split()[0]


def source_identity(root: Path, commit: str, work_root: str = ".razr") -> str:
    if not SHA_RE.fullmatch(commit):
        raise DeliveryError("candidate commit must be a full Git object id")
    out, _ = _capture("git", ["ls-tree", "-r", "-z", "--full-tree", commit], root,
                      timeout=30)
    if len(out) > MAX_STDOUT * 8:
        raise DeliveryError("candidate tree unavailable")
    prefix = work_root.rstrip("/") + "/"
    entries = []
    for row in out.split(b"\0"):
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


def _string(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise DeliveryError(f"invalid PR {field}")
    return value


def _validate_collection(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise DeliveryError(f"invalid PR {field}")
    return value


def _validate_pr(value: Any, url: str, owner: str, repo: str, number: int) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("url") != url or value.get("number") != number:
        raise DeliveryError("PR identity read-back mismatch")
    for field in ("state", "baseRefName", "headRefName", "mergeStateStatus", "mergeable"):
        _string(value.get(field), field)
    for field in ("baseRefOid", "headRefOid"):
        if not SHA_RE.fullmatch(_string(value.get(field), field)):
            raise DeliveryError("PR SHA read-back is invalid")
    for field in ("reviews", "comments", "reviewRequests", "statusCheckRollup"):
        rows = _validate_collection(value.get(field), field)
        for row in rows:
            if field == "reviewRequests" and not isinstance(row.get("login") or row.get("name"), str):
                raise DeliveryError("malformed review request")
            if field == "reviews" and not isinstance(row.get("state"), str):
                raise DeliveryError("malformed review")
            if field == "comments" and not isinstance(row.get("body"), str):
                raise DeliveryError("malformed comment")
    if not isinstance(value.get("isDraft"), bool):
        raise DeliveryError("malformed draft state")
    value["repository"] = f"{owner}/{repo}"
    return value


def _required_checks(root: Path, url: str) -> tuple[list[dict[str, str]], str]:
    try:
        raw = _run("gh", ["pr", "checks", url, "--required", "--json", "name,state,bucket"], root,
                   accepted_codes={0, 1, 8})
        rows = [] if not raw else json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeliveryError("check read-back is malformed") from exc
    if not isinstance(rows, list):
        raise DeliveryError("check read-back is invalid")
    result = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"name", "state", "bucket"}:
            raise DeliveryError("check row schema is invalid")
        if not all(isinstance(row[x], str) and row[x] for x in row):
            raise DeliveryError("check row is invalid")
        if row["bucket"] not in {"PASS", "FAIL", "PENDING", "SKIPPING"}:
            raise DeliveryError("unknown check bucket")
        result.append({x: row[x] for x in ("name", "state", "bucket")})
    if not result:
        return result, "none"
    if any(row["bucket"] == "FAIL" for row in result):
        return result, "failing"
    if any(row["bucket"] == "PENDING" for row in result):
        return result, "pending"
    return result, "passing"


def _threads(root: Path, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
    query = ("query($o:String!,$r:String!,$n:Int!,$a:String){repository(owner:$o,name:$r){"
             "pullRequest(number:$n){reviewThreads(first:100,after:$a){nodes{id isResolved "
             "comments(last:1){nodes{id body author{login} updatedAt path}}} "
             "pageInfo{hasNextPage endCursor}}}}}")
    result: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(MAX_THREADS_PAGES):
        args = ["api", "graphql", "-f", f"query={query}", "-F", f"o={owner}",
                "-F", f"r={repo}", "-F", f"n={number}"]
        if cursor is not None:
            args += ["-F", f"a={cursor}"]
        value = _json("gh", args, root)
        try:
            page = value["data"]["repository"]["pullRequest"]["reviewThreads"]
            nodes = page["nodes"]
            info = page["pageInfo"]
            if not isinstance(nodes, list) or not isinstance(info, dict):
                raise KeyError("page")
            for node in nodes:
                if set(node) != {"id", "isResolved", "comments"} or not isinstance(node["id"], str) \
                        or not isinstance(node["isResolved"], bool):
                    raise KeyError("thread")
                comments = node["comments"].get("nodes") if isinstance(node["comments"], dict) else None
                if not isinstance(comments, list):
                    raise KeyError("comments")
                for comment in comments:
                    if not isinstance(comment, dict) or not isinstance(comment.get("id"), str) \
                            or not isinstance(comment.get("body"), str):
                        raise KeyError("comment")
            result.extend(nodes)
            has_next = info.get("hasNextPage")
            next_cursor = info.get("endCursor")
            if not isinstance(has_next, bool) or (has_next and not isinstance(next_cursor, str)):
                raise KeyError("pageInfo")
            if not has_next:
                return result
            if next_cursor == cursor:
                raise DeliveryError("review-thread pagination did not advance")
            cursor = next_cursor
        except (KeyError, TypeError) as exc:
            raise DeliveryError("review-thread schema is invalid") from exc
    raise DeliveryError("review-thread pagination exceeds bound")


def read_pr(root: Path, url: str, *, expected_repository: str | None = None,
            expected_base: str | None = None, expected_base_sha: str | None = None,
            expected_head: str | None = None, expected_head_sha: str | None = None,
            expected_source_digest: str | None = None) -> dict[str, Any]:
    owner, repo, number = _pr_url(url)
    repository = f"{owner}/{repo}"
    if expected_repository and repository.lower() != expected_repository.lower():
        raise DeliveryError("PR repository does not match expectation")
    fields = ("number,url,state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid,"
              "mergeStateStatus,mergeable,reviewDecision,reviewRequests,reviews,comments,"
              "statusCheckRollup,mergedAt,closedAt,updatedAt")
    value = _validate_pr(_json("gh", ["pr", "view", url, "--json", fields], root), url, owner, repo, number)
    if expected_base is not None and value["baseRefName"] != expected_base:
        raise DeliveryError("PR base branch mismatch")
    if expected_base_sha is not None and value["baseRefOid"] != expected_base_sha:
        raise DeliveryError("PR base commit mismatch")
    if expected_head is not None and value["headRefName"] != expected_head:
        raise DeliveryError("PR head branch mismatch")
    if expected_head_sha is not None and value["headRefOid"] != expected_head_sha:
        raise DeliveryError("PR head commit mismatch")
    checks, status = _required_checks(root, url)
    value["requiredChecks"] = checks
    value["requiredChecksStatus"] = status
    value["reviewThreads"] = _threads(root, owner, repo, number)
    if expected_source_digest is not None:
        if not expected_head_sha or source_identity(root, expected_head_sha) != expected_source_digest:
            raise DeliveryError("PR source identity mismatch")
        value["sourceDigest"] = expected_source_digest
    return value


def _existing_open(root: Path, repository: str, base: str, head: str, base_sha: str,
                   head_sha: str, source_digest: str) -> dict[str, Any] | None:
    rows = _json("gh", ["pr", "list", "--state", "open", "--base", base, "--head", head,
                         "--json", "url"], root)
    if not isinstance(rows, list) or any(not isinstance(row, dict) or set(row) != {"url"} for row in rows):
        raise DeliveryError("existing PR query schema is invalid")
    if len(rows) > 1:
        raise DeliveryError("ambiguous existing open PRs")
    if not rows:
        return None
    url = rows[0]["url"]
    return read_pr(root, url, expected_repository=repository, expected_base=base,
                   expected_base_sha=base_sha, expected_head=head, expected_head_sha=head_sha,
                   expected_source_digest=source_digest)


def create_pr(root: Path, *, base: str, head: str, title: str, body: str,
              expected_repository: str | None = None, expected_base_sha: str | None = None,
              expected_head_sha: str | None = None, expected_source_digest: str | None = None) -> dict[str, Any]:
    if not base or not head or not title or len(title) > 256 or len(body) > 100000:
        raise DeliveryError("invalid PR request")
    repository = expected_repository or remote_identity(root)
    base_sha = expected_base_sha or pushed_head(root, base)
    head_sha = expected_head_sha or pushed_head(root, head)
    if not SHA_RE.fullmatch(base_sha) or not SHA_RE.fullmatch(head_sha):
        raise DeliveryError("PR commit expectation is not a full Git object id")
    digest = expected_source_digest or source_identity(root, head_sha)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise DeliveryError("PR source identity is invalid")
    existing = _existing_open(root, repository, base, head, base_sha, head_sha, digest)
    if existing is not None:
        return existing
    url = _run("gh", ["pr", "create", "--base", base, "--head", head, "--title", title,
                       "--body", body], root)
    return read_pr(root, url, expected_repository=repository, expected_base=base,
                   expected_base_sha=base_sha, expected_head=head, expected_head_sha=head_sha,
                   expected_source_digest=digest)


def snapshot(root: Path, *, branch: str, base: str = "campaign/razr-pi-next") -> dict[str, Any]:
    repository = remote_identity(root)
    commit = pushed_head(root, branch)
    return {"repository": repository, "branch": branch, "commit": commit,
            "source_digest": source_identity(root, commit), "base": base,
            "base_commit": pushed_head(root, base)}
