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
MAX_THREAD_COMMENTS = 100
PR_STATES = {"OPEN", "CLOSED", "MERGED"}
MERGEABLE = {"CONFLICTING", "MERGEABLE", "UNKNOWN"}
MERGE_STATES = {"BEHIND", "BLOCKED", "CLEAN", "DIRTY", "DRAFT", "HAS_HOOK_ERRORS", "UNKNOWN", "UNSTABLE"}
CHECK_BUCKETS = {"PASS", "FAIL", "PENDING", "SKIPPING"}
CHECK_STATES = {"QUEUED", "IN_PROGRESS", "COMPLETED", "EXPECTED", "REQUESTED", "WAITING", "PENDING"}


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
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                                   start_new_session=True)
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
            _terminate_group(process)
            raise DeliveryError(f"{program} timed out")
        for key, _ in selector.select(remaining):
            chunk = key.fileobj.read(4096)
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            name = key.data
            if len(buffers[name]) + len(chunk) > limits[name]:
                overflow = True
                _terminate_group(process)
                break
            buffers[name].extend(chunk)
        if overflow:
            break
    if overflow:
        _terminate_group(process)
        raise DeliveryError("bounded response refused")
    code = process.wait()
    if code not in (accepted_codes or {0}):
        # stderr is deliberately never included: credential helpers can write there.
        raise DeliveryError(f"{program} request failed")
    return bytes(buffers["out"]), bytes(buffers["err"])


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            process.wait()
        except subprocess.TimeoutExpired:
            pass
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        return
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait()
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


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


def _nullable_string(value: Any, field: str) -> None:
    if value is not None and not isinstance(value, str):
        raise DeliveryError(f"invalid PR {field}")


def _author(value: Any) -> None:
    if value is not None and (not isinstance(value, dict) or set(value) != {"login"}
                              or not isinstance(value.get("login"), str) or not value["login"]):
        raise DeliveryError("invalid PR author")


def _validate_pr(value: Any, url: str, owner: str, repo: str, number: int) -> dict[str, Any]:
    allowed = {"number", "url", "state", "isDraft", "baseRefName", "baseRefOid", "headRefName", "headRefOid",
               "mergeStateStatus", "mergeable", "reviewDecision", "reviewRequests", "reviews", "comments",
               "statusCheckRollup", "mergedAt", "closedAt", "updatedAt"}
    if not isinstance(value, dict) or set(value) != allowed or value.get("url") != url or value.get("number") != number:
        raise DeliveryError("PR identity/schema read-back mismatch")
    if value["state"] not in PR_STATES or value["mergeStateStatus"] not in MERGE_STATES \
            or value["mergeable"] not in MERGEABLE:
        raise DeliveryError("unknown PR state enum")
    for field in ("baseRefName", "headRefName"):
        _string(value.get(field), field)
    for field in ("baseRefOid", "headRefOid"):
        if not SHA_RE.fullmatch(_string(value.get(field), field)):
            raise DeliveryError("PR SHA read-back is invalid")
    for field in ("reviews", "comments", "reviewRequests", "statusCheckRollup"):
        rows = _validate_collection(value.get(field), field)
        for row in rows:
            if field == "reviewRequests":
                if set(row) != {"login", "name"} or not isinstance(row.get("login"), str) \
                        or not isinstance(row.get("name"), str):
                    raise DeliveryError("malformed review request")
            elif field == "reviews":
                allowed = {"id", "author", "body", "commit", "createdAt", "includesCreatedEdit",
                           "reactionGroups", "state", "submittedAt", "updatedAt"}
                if set(row) - allowed or not isinstance(row.get("id"), str) or not row["id"] \
                        or not isinstance(row.get("state"), str) or row["state"] not in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "PENDING"}:
                    raise DeliveryError("malformed review")
                _author(row.get("author"))
                for key in ("body", "commit", "createdAt", "submittedAt", "updatedAt"):
                    _nullable_string(row.get(key), "review " + key)
                if not isinstance(row.get("includesCreatedEdit"), bool):
                    raise DeliveryError("malformed review metadata")
            elif field == "comments":
                allowed = {"id", "author", "body", "createdAt", "isMinimized", "reactions", "updatedAt", "url"}
                if set(row) - allowed or not isinstance(row.get("id"), str) or not row["id"] \
                        or not isinstance(row.get("body"), str) or not isinstance(row.get("isMinimized"), bool):
                    raise DeliveryError("malformed comment")
                _author(row.get("author"))
                for key in ("createdAt", "updatedAt", "url"):
                    _nullable_string(row.get(key), "comment " + key)
            else:
                allowed = {"context", "conclusion", "detailsUrl", "event", "group", "name", "startedAt", "state", "status", "workflowName"}
                if set(row) - allowed or not isinstance(row.get("name") or row.get("context"), str):
                    raise DeliveryError("malformed status check")
                for key in ("conclusion", "detailsUrl", "event", "group", "startedAt", "workflowName"):
                    _nullable_string(row.get(key), "check " + key)
    if not isinstance(value.get("isDraft"), bool) or not isinstance(value.get("reviewDecision"), str):
        raise DeliveryError("malformed draft/review state")
    value["repository"] = f"{owner}/{repo}"
    return value


def _required_checks(root: Path, url: str, repository: str) -> tuple[list[dict[str, str]], str]:
    try:
        raw = _run("gh", ["pr", "checks", url, "--repo", repository, "--required", "--json", "name,state,bucket"], root,
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
        if row["state"] not in CHECK_STATES or row["bucket"] not in CHECK_BUCKETS:
            raise DeliveryError("unknown check state/bucket")
        result.append({x: row[x] for x in ("name", "state", "bucket")})
    if not result:
        return result, "none"
    if any(row["bucket"] == "FAIL" for row in result):
        return result, "failing"
    if any(row["bucket"] == "PENDING" for row in result):
        return result, "pending"
    if any(row["bucket"] == "SKIPPING" for row in result):
        return result, "skipping"
    return result, "passing"


def _validate_thread_comment(comment: Any) -> None:
    if not isinstance(comment, dict) or set(comment) != {"id", "body", "author", "updatedAt", "path"} \
            or not isinstance(comment.get("id"), str) or not comment["id"] \
            or not isinstance(comment.get("body"), str):
        raise DeliveryError("review-thread comment schema is invalid")
    _author(comment.get("author"))
    _nullable_string(comment.get("updatedAt"), "thread comment updatedAt")
    _nullable_string(comment.get("path"), "thread comment path")


def _thread_comments(root: Path, thread_id: str, initial: list[dict[str, Any]],
                     cursor: str | None) -> list[dict[str, Any]]:
    query = ("query($id:ID!,$a:String){node(id:$id){... on PullRequestReviewThread{comments(first:100,after:$a){"
             "nodes{id body author{login} updatedAt path} pageInfo{hasNextPage endCursor}}}}}")
    result = list(initial)
    pages = 0
    while cursor is not None and pages < MAX_THREADS_PAGES:
        value = _json("gh", ["api", "graphql", "-f", f"query={query}", "-F", f"id={thread_id}",
                              "-F", f"a={cursor}"], root)
        pages += 1
        try:
            page = value["data"]["node"]["comments"]
            nodes, info = page["nodes"], page["pageInfo"]
            if not isinstance(nodes, list) or len(nodes) > MAX_THREAD_COMMENTS \
                    or not isinstance(info, dict) or set(info) != {"hasNextPage", "endCursor"}:
                raise KeyError("comments")
            for comment in nodes:
                _validate_thread_comment(comment)
            result.extend(nodes)
            if len(result) > MAX_THREAD_COMMENTS:
                raise DeliveryError("review-thread comment bound exceeded")
            has_next, next_cursor = info["hasNextPage"], info["endCursor"]
            if not isinstance(has_next, bool) or (has_next and not isinstance(next_cursor, str)) \
                    or (not has_next and next_cursor is not None) or next_cursor == cursor:
                raise KeyError("pageInfo")
            cursor = next_cursor if has_next else None
        except (KeyError, TypeError) as exc:
            raise DeliveryError("review-thread comment pagination schema is invalid") from exc
    if cursor is not None:
        raise DeliveryError("review-thread comment pagination exceeds bound")
    return result


def _threads(root: Path, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
    query = ("query($o:String!,$r:String!,$n:Int!,$a:String){repository(owner:$o,name:$r){"
             "pullRequest(number:$n){reviewThreads(first:100,after:$a){nodes{id isResolved "
             "comments(first:100){nodes{id body author{login} updatedAt path} pageInfo{hasNextPage endCursor}}} "
             "pageInfo{hasNextPage endCursor}}}}}")
    result: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(MAX_THREADS_PAGES):
        # gh api has no --repo flag; the explicit owner/name variables are the
        # API equivalent and are bound to the verified repository above.
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
                comments_obj = node["comments"] if isinstance(node["comments"], dict) else None
                comments = comments_obj.get("nodes") if comments_obj is not None else None
                comment_info = comments_obj.get("pageInfo") if comments_obj is not None else None
                if comments_obj is None or not isinstance(comment_info, dict) \
                        or set(comment_info) != {"hasNextPage", "endCursor"} or not isinstance(comments, list) \
                        or len(comments) > MAX_THREAD_COMMENTS:
                    raise KeyError("comments")
                for comment in comments:
                    _validate_thread_comment(comment)
                comment_next = comment_info["endCursor"] if comment_info["hasNextPage"] else None
                if not isinstance(comment_info["hasNextPage"], bool) \
                        or (comment_info["hasNextPage"] and not isinstance(comment_next, str)) \
                        or (not comment_info["hasNextPage"] and comment_info["endCursor"] is not None):
                    raise KeyError("comment pageInfo")
                node["comments"] = {"nodes": _thread_comments(root, node["id"], comments, comment_next),
                                    "pageInfo": {"hasNextPage": False, "endCursor": None}}
            result.extend(nodes)
            has_next = info.get("hasNextPage")
            next_cursor = info.get("endCursor")
            if set(info) != {"hasNextPage", "endCursor"} or not isinstance(has_next, bool) \
                    or (has_next and not isinstance(next_cursor, str)) \
                    or (not has_next and next_cursor is not None):
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
    actual_repository = remote_identity(root)
    if repository.lower() != actual_repository.lower():
        raise DeliveryError("PR repository differs from configured remote")
    fields = ("number,url,state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid,"
              "mergeStateStatus,mergeable,reviewDecision,reviewRequests,reviews,comments,"
              "statusCheckRollup,mergedAt,closedAt,updatedAt")
    value = _validate_pr(_json("gh", ["pr", "view", url, "--repo", actual_repository, "--json", fields], root), url, owner, repo, number)
    actual_base_sha = pushed_head(root, value["baseRefName"])
    actual_head_sha = pushed_head(root, value["headRefName"])
    if value["baseRefOid"] != actual_base_sha or value["headRefOid"] != actual_head_sha:
        raise DeliveryError("PR SHA differs from current remote branch")
    if expected_repository and actual_repository.lower() != expected_repository.lower():
        raise DeliveryError("PR repository expectation mismatch")
    if expected_base is not None and value["baseRefName"] != expected_base:
        raise DeliveryError("PR base branch mismatch")
    if expected_base_sha is not None and actual_base_sha != expected_base_sha:
        raise DeliveryError("PR base commit mismatch")
    if expected_head is not None and value["headRefName"] != expected_head:
        raise DeliveryError("PR head branch mismatch")
    if expected_head_sha is not None and actual_head_sha != expected_head_sha:
        raise DeliveryError("PR head commit mismatch")
    checks, status = _required_checks(root, url, actual_repository)
    value["requiredChecks"] = checks
    value["requiredChecksStatus"] = status
    value["reviewThreads"] = _threads(root, owner, repo, number)
    actual_source_digest = source_identity(root, actual_head_sha)
    if expected_source_digest is not None and actual_source_digest != expected_source_digest:
        raise DeliveryError("PR source identity mismatch")
    value["sourceDigest"] = actual_source_digest
    return value


def _existing_open(root: Path, repository: str, base: str, head: str, base_sha: str,
                   head_sha: str, source_digest: str) -> dict[str, Any] | None:
    rows = _json("gh", ["pr", "list", "--repo", repository, "--state", "open", "--base", base,
                         "--head", head, "--json", "url"], root)
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
    repository = remote_identity(root)
    if expected_repository and expected_repository.lower() != repository.lower():
        raise DeliveryError("repository expectation mismatch")
    base_sha = pushed_head(root, base)
    head_sha = pushed_head(root, head)
    if expected_base_sha and expected_base_sha != base_sha:
        raise DeliveryError("base commit expectation mismatch")
    if expected_head_sha and expected_head_sha != head_sha:
        raise DeliveryError("head commit expectation mismatch")
    if not SHA_RE.fullmatch(base_sha) or not SHA_RE.fullmatch(head_sha):
        raise DeliveryError("PR commit expectation is not a full Git object id")
    digest = expected_source_digest or source_identity(root, head_sha)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise DeliveryError("PR source identity is invalid")
    existing = _existing_open(root, repository, base, head, base_sha, head_sha, digest)
    if existing is not None:
        return existing
    try:
        url = _run("gh", ["pr", "create", "--repo", repository, "--base", base, "--head", head,
                           "--title", title, "--body", body], root)
    except DeliveryError:
        # A concurrent creator may have won. One read-only exact lookup is the only recovery.
        recovered = _existing_open(root, repository, base, head, base_sha, head_sha, digest)
        if recovered is not None:
            return recovered
        raise
    return read_pr(root, url, expected_repository=repository, expected_base=base,
                   expected_base_sha=base_sha, expected_head=head, expected_head_sha=head_sha,
                   expected_source_digest=digest)


def snapshot(root: Path, *, branch: str, base: str = "campaign/razr-pi-next") -> dict[str, Any]:
    repository = remote_identity(root)
    commit = pushed_head(root, branch)
    return {"repository": repository, "branch": branch, "commit": commit,
            "source_digest": source_identity(root, commit), "base": base,
            "base_commit": pushed_head(root, base)}
