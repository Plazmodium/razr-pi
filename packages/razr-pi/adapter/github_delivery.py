"""Bounded, adapter-owned GitHub delivery observations and one safe PR create."""
from __future__ import annotations

import hashlib
import json
import os
import runpy
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time
from typing import Any

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,38})/[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})\Z")
REPO_RE = re.compile(r"^(?:git@github\.com:|https://github\.com/)([^/]+)/([^/]+)\Z")
PR_RE = re.compile(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?\Z")
MAX_STDOUT = 1024 * 1024
MAX_STDERR = 262144
MAX_THREADS_PAGES = 10
MAX_THREAD_COMMENTS = 100
PR_STATES = {"OPEN", "CLOSED", "MERGED"}
MERGEABLE = {"CONFLICTING", "MERGEABLE", "UNKNOWN"}
MERGE_STATES = {"BEHIND", "BLOCKED", "CLEAN", "DIRTY", "HAS_HOOKS", "UNKNOWN", "UNSTABLE"}
CHECK_BUCKETS = {"PASS", "FAIL", "PENDING", "SKIPPING", "CANCEL"}
CHECK_STATES = {"QUEUED", "IN_PROGRESS", "COMPLETED", "EXPECTED", "REQUESTED", "WAITING", "PENDING",
                "SUCCESS", "FAILURE", "ERROR", "SKIPPED", "NEUTRAL", "CANCELLED", "TIMED_OUT",
                "ACTION_REQUIRED", "STALE", "STARTUP_FAILURE"}


class DeliveryError(RuntimeError):
    pass


class InvalidRequest(DeliveryError):
    pass


class CandidateChanged(DeliveryError):
    pass


class IdentityMismatch(DeliveryError):
    pass


def _env() -> dict[str, str]:
    names = ("PATH", "HOME", "TMPDIR", "GH_HOST", "LANG", "LC_ALL", "LC_CTYPE",
             "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_OPTIONAL_LOCKS",
             "GIT_TERMINAL_PROMPT")
    result = {key: os.environ[key] for key in names if os.environ.get(key) is not None}
    result.setdefault("PATH", os.defpath)
    # Do not permit an ambient host to redirect verified github.com identity.
    result["GH_HOST"] = "github.com"
    result.update({"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_CONFIG_GLOBAL": os.devnull, "GIT_OPTIONAL_LOCKS": "0",
                   "GIT_TERMINAL_PROMPT": "0"})
    return result


def _capture_detailed(program: str, args: list[str], cwd: Path, *, timeout: float = 30.0,
                     accepted_codes: set[int] | None = None, allow_nonzero: bool = False) -> tuple[bytes, bytes, int]:
    if len(args) > 32 or sum(len(x) for x in args) > 32768:
        raise DeliveryError("bounded request refused")
    try:
        process = subprocess.Popen([program, *args], cwd=cwd, env=_env(), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                                   start_new_session=True)
    except OSError as exc:
        raise DeliveryError(f"{program} unavailable") from exc
    selector = None
    try:
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "out")
        selector.register(process.stderr, selectors.EVENT_READ, "err")
    except BaseException as exc:
        try:
            _terminate_group(process)
        except DeliveryError:
            pass
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        raise DeliveryError("subprocess capture setup failed") from exc
    buffers = {"out": bytearray(), "err": bytearray()}
    limits = {"out": MAX_STDOUT, "err": MAX_STDERR}
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_group(process)
                raise DeliveryError(f"{program} timed out")
            for key, _ in selector.select(remaining):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                name = key.data
                if len(buffers[name]) + len(chunk) > limits[name]:
                    _terminate_group(process)
                    raise DeliveryError("bounded response refused")
                buffers[name].extend(chunk)
        if process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_group(process)
                raise DeliveryError(f"{program} timed out")
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                _terminate_group(process)
                raise DeliveryError(f"{program} timed out")
        code = process.returncode
        _verify_group_clear(process)
        if code not in (accepted_codes or {0}):
            raise DeliveryError(f"{program} request failed")
        return bytes(buffers["out"]), bytes(buffers["err"]), int(code)
    except DeliveryError:
        try:
            _terminate_group(process)
        except DeliveryError:
            pass
        raise
    except BaseException as exc:
        try:
            _terminate_group(process)
        except DeliveryError:
            pass
        raise DeliveryError("subprocess capture failed") from exc
    finally:
        if selector is not None and hasattr(selector, "close"):
            selector.close()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _capture(program: str, args: list[str], cwd: Path, *, timeout: float = 30.0,
             accepted_codes: set[int] | None = None) -> tuple[bytes, bytes]:
    stdout, stderr, _ = _capture_detailed(program, args, cwd, timeout=timeout,
                                          accepted_codes=accepted_codes)
    return stdout, stderr


def _verify_group_clear(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        raise DeliveryError("process-group cleanup could not be verified") from exc
    _terminate_group(process)
    raise DeliveryError("process-group descendant survived leader exit")


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    deadline = __import__("time").monotonic() + 0.5
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        remaining = max(0.0, deadline - __import__("time").monotonic())
        process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            process.wait(timeout=max(0.0, deadline - __import__("time").monotonic()))
        except subprocess.TimeoutExpired:
            raise DeliveryError("process-group cleanup timed out")
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        group_alive = False
    except PermissionError as exc:
        raise DeliveryError("process-group cleanup could not be verified") from exc
    else:
        group_alive = True
    if group_alive:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            group_alive = False
        except PermissionError as exc:
            raise DeliveryError("process-group cleanup could not be verified") from exc
        if group_alive:
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                group_alive = False
            except PermissionError as exc:
                raise DeliveryError("process-group cleanup could not be verified") from exc
        if group_alive:
            raise DeliveryError("process-group cleanup timed out")
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def _run_result(program: str, args: list[str], cwd: Path, *, timeout: float = 30.0,
                accepted_codes: set[int]) -> tuple[str, str, int]:
    stdout, stderr, code = _capture_detailed(program, args, cwd, timeout=timeout,
                                               accepted_codes=accepted_codes, allow_nonzero=True)
    try:
        return stdout.decode("utf-8"), stderr.decode("utf-8"), code
    except UnicodeDecodeError as exc:
        raise DeliveryError("non-UTF-8 response") from exc


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
    repository = f"{match.group(1)}/{match.group(2)}"
    if not REPOSITORY_RE.fullmatch(repository):
        raise InvalidRequest("invalid GitHub repository identity")
    return repository


def pushed_head(root: Path, branch: str, remote: str = "origin") -> str:
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,255}", branch) or ".." in branch:
        raise InvalidRequest("invalid branch")
    # Branch identity comes from the authenticated, host-pinned GitHub API;
    # local core.sshCommand/url rewrites cannot spoof this ref observation.
    repository = remote_identity(root, remote)
    owner, name = repository.split("/", 1)
    value = _run("gh", ["api", "--hostname", "github.com", f"repos/{owner}/{name}/git/ref/heads/{branch}",
                         "--jq", ".object.sha"], root)
    if not SHA_RE.fullmatch(value):
        raise DeliveryError("remote branch head is missing or ambiguous")
    return value


def _configured_work_root(root: Path, commit: str) -> str:
    # Read configuration from the candidate object, never from the live
    # checkout. Core validate_config remains the semantic authority.
    core_namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "runtime/scripts/razr.py"))
    try:
        out, diagnostic, code = _run_result("git", ["show", f"{commit}:razr.yaml"], root,
                                            accepted_codes={0, 128})
        if code == 0:
            config = json.loads(out)
            try:
                validated = core_namespace["validate_config"](root.resolve(), config)
            except Exception as exc:
                raise DeliveryError("candidate razr.yaml is invalid") from exc
            return str(validated.relative_to(root.resolve()))
        if "does not exist in" not in diagnostic:
            raise DeliveryError("candidate razr.yaml is unavailable")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError) as exc:
        raise DeliveryError("candidate razr.yaml is invalid") from exc
    work_root = core_namespace.get("DEFAULT_CONFIG", {}).get("work_root")
    if not isinstance(work_root, str) or not work_root:
        raise DeliveryError("core default work root is unavailable")
    return work_root


def source_identity(root: Path, commit: str, work_root: str | None = None) -> str:
    if not SHA_RE.fullmatch(commit):
        raise InvalidRequest("candidate commit must be a full Git object id")
    out, _ = _capture("git", ["ls-tree", "-r", "-z", "--full-tree", commit], root,
                      timeout=30)
    if len(out) > MAX_STDOUT * 8:
        raise DeliveryError("candidate tree unavailable")
    prefix = (work_root if work_root is not None else _configured_work_root(root, commit)).rstrip("/") + "/"
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
        raise InvalidRequest("GitHub returned an invalid PR URL")
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


def _reactions(value: Any) -> None:
    if not isinstance(value, list):
        raise DeliveryError("invalid reaction groups")
    for group in value:
        if not isinstance(group, dict) or set(group) != {"content", "users"} \
                or group.get("content") not in {"THUMBS_UP", "THUMBS_DOWN", "LAUGH", "HOORAY", "CONFUSED", "HEART", "ROCKET", "EYES"} \
                or not isinstance(group["users"], dict):
            raise DeliveryError("invalid reaction group")
        users = group["users"]
        if set(users) - {"totalCount", "nodes"} or not isinstance(users.get("totalCount"), int) \
                or isinstance(users.get("totalCount"), bool):
            raise DeliveryError("invalid reaction users")
        if "nodes" in users and (not isinstance(users["nodes"], list)
                                  or any(not isinstance(node, dict) or set(node) != {"login"}
                                         or not isinstance(node["login"], str) for node in users["nodes"])):
            raise DeliveryError("invalid reaction user nodes")


def _commit(value: Any) -> None:
    if value is not None and (not isinstance(value, dict) or set(value) != {"oid"}
                              or not isinstance(value["oid"], str) or not SHA_RE.fullmatch(value["oid"])):
        raise DeliveryError("invalid review commit")


def _reaction_summary(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"totalCount"} \
            or not isinstance(value["totalCount"], int) or isinstance(value["totalCount"], bool):
        raise DeliveryError("invalid reaction summary")


def _validate_pr(value: Any, url: str, owner: str, repo: str, number: int) -> dict[str, Any]:
    allowed = {"number", "url", "state", "isDraft", "baseRefName", "baseRefOid", "headRefName", "headRefOid",
               "mergeStateStatus", "mergeable", "reviewDecision", "reviewRequests", "reviews", "comments",
               "statusCheckRollup", "mergedAt", "closedAt", "updatedAt"}
    if not isinstance(value, dict) or set(value) != allowed or value.get("url") != url \
            or not isinstance(value.get("number"), int) or isinstance(value.get("number"), bool) \
            or value.get("number") != number:
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
                typename = row.get("__typename")
                if typename == "User":
                    if set(row) != {"__typename", "login"} or not isinstance(row.get("login"), str) or not row["login"]:
                        raise DeliveryError("malformed user review request")
                elif typename == "Team":
                    if set(row) != {"__typename", "name"} or not isinstance(row.get("name"), str) or not row["name"]:
                        raise DeliveryError("malformed team review request")
                else:
                    raise DeliveryError("unknown review request type")
            elif field == "reviews":
                allowed = {"id", "author", "authorAssociation", "body", "commit", "createdAt", "includesCreatedEdit",
                           "reactionGroups", "state", "submittedAt", "updatedAt"}
                if set(row) - allowed or not isinstance(row.get("id"), str) or not row["id"] \
                        or not isinstance(row.get("state"), str) or row["state"] not in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "PENDING"}:
                    raise DeliveryError("malformed review")
                _author(row.get("author"))
                if row.get("authorAssociation") not in {"COLLABORATOR", "CONTRIBUTOR", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR", "MANNEQUIN", "MEMBER", "NONE", "OWNER"}:
                    raise DeliveryError("unknown review author association")
                _commit(row.get("commit"))
                _reactions(row.get("reactionGroups"))
                for key in ("body", "createdAt", "submittedAt", "updatedAt"):
                    _nullable_string(row.get(key), "review " + key)
                if not isinstance(row.get("includesCreatedEdit"), bool):
                    raise DeliveryError("malformed review metadata")
            elif field == "comments":
                allowed = {"id", "author", "authorAssociation", "body", "createdAt", "includesCreatedEdit", "isMinimized", "minimizedReason", "reactionGroups", "updatedAt", "url", "viewerDidAuthor"}
                if set(row) - allowed or not isinstance(row.get("id"), str) or not row["id"] \
                        or not isinstance(row.get("body"), str) or not isinstance(row.get("isMinimized"), bool) \
                        or not isinstance(row.get("includesCreatedEdit"), bool) or not isinstance(row.get("viewerDidAuthor"), bool):
                    raise DeliveryError("malformed comment")
                _author(row.get("author"))
                if row.get("authorAssociation") not in {"COLLABORATOR", "CONTRIBUTOR", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR", "MANNEQUIN", "MEMBER", "NONE", "OWNER"}:
                    raise DeliveryError("unknown comment author association")
                _reactions(row.get("reactionGroups"))
                for key in ("createdAt", "minimizedReason", "updatedAt", "url"):
                    _nullable_string(row.get(key), "comment " + key)
            else:
                allowed = {"__typename", "completedAt", "conclusion", "context", "createdAt", "detailsUrl", "event", "group", "headSha", "name", "startedAt", "state", "status", "targetUrl", "updatedAt", "workflowName"}
                if set(row) - allowed or not isinstance(row.get("__typename"), str):
                    raise DeliveryError("malformed status check")
                if row["__typename"] == "CheckRun":
                    if not isinstance(row.get("name"), str) or row.get("status") not in {"QUEUED", "IN_PROGRESS", "PENDING", "REQUESTED", "WAITING", "COMPLETED"}:
                        raise DeliveryError("malformed check run")
                    conclusions = {"", "SUCCESS", "SKIPPED", "FAILURE", "CANCELLED", "NEUTRAL", "TIMED_OUT", "ACTION_REQUIRED", "STALE", "STARTUP_FAILURE"}
                    if row.get("conclusion") not in conclusions:
                        raise DeliveryError("unknown check conclusion")
                    if row["status"] == "COMPLETED" and row.get("conclusion") in {None, ""}:
                        raise DeliveryError("completed check has no terminal conclusion")
                    if row["status"] != "COMPLETED" and row.get("conclusion") not in {None, ""}:
                        raise DeliveryError("pending check has terminal conclusion")
                elif row["__typename"] == "StatusContext":
                    if not isinstance(row.get("context"), str) or row.get("state") not in {"EXPECTED", "ERROR", "FAILURE", "PENDING", "SUCCESS"}:
                        raise DeliveryError("malformed status context")
                else:
                    raise DeliveryError("unknown status check type")
                for key in ("completedAt", "createdAt", "detailsUrl", "event", "group", "headSha", "startedAt", "targetUrl", "updatedAt", "workflowName"):
                    _nullable_string(row.get(key), "check " + key)
    if not isinstance(value.get("isDraft"), bool):
        raise DeliveryError("malformed draft state")
    if value.get("reviewDecision") not in {"", "APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        raise DeliveryError("unknown review decision")
    for key in ("mergedAt", "closedAt", "updatedAt"):
        _nullable_string(value.get(key), key)
    value["repository"] = f"{owner}/{repo}"
    return value


def validate_repository(repository: str) -> str:
    if not isinstance(repository, str) or not REPOSITORY_RE.fullmatch(repository):
        raise InvalidRequest("invalid GitHub repository identity")
    return repository


def _repo_arg(repository: str) -> str:
    return "github.com/" + validate_repository(repository)


def _required_checks(root: Path, url: str, repository: str) -> tuple[list[dict[str, str]], str]:
    raw, diagnostic, code = _run_result("gh", ["pr", "checks", url, "--repo", _repo_arg(repository), "--required", "--json", "name,state,bucket"], root,
                                         accepted_codes={0, 1, 8})
    no_checks = r"no (?:required )?checks reported on the '[^'\n]{1,255}' branch\n?"
    if code == 1 and not raw:
        if not re.fullmatch(no_checks, diagnostic):
            raise DeliveryError("required-check provider request failed")
        rows = []
    else:
        if diagnostic.strip():
            raise DeliveryError("required-check output contradicts provider diagnostic")
        if not raw and code != 0:
            raise DeliveryError("required-check provider request failed")
        try:
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
        state, bucket = row["state"].upper(), row["bucket"].upper()
        if state not in CHECK_STATES or bucket not in CHECK_BUCKETS:
            raise DeliveryError("unknown check state/bucket")
        result.append({"name": row["name"], "state": state, "bucket": bucket})
    failing = any(row["bucket"] == "FAIL" or row["state"] in {
        "FAILURE", "ERROR", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"} for row in result)
    pending = any(row["bucket"] == "PENDING" or row["state"] in {
        "QUEUED", "IN_PROGRESS", "EXPECTED", "REQUESTED", "WAITING", "PENDING", "STALE"} for row in result)
    cancelled = any(row["bucket"] == "CANCEL" or row["state"] == "CANCELLED" for row in result)
    if not result:
        return result, "none"
    # gh's transport contract is exact: failure=1, pending-only=8, and all
    # other complete/skipped/cancelled output=0. Contradictions fail closed.
    expected = 1 if failing else 8 if pending else 0
    if code != expected:
        raise DeliveryError("required-check exit contradicts populated checks")
    # gh uses failure precedence (exit 1) while retaining pending rows.
    # The aggregate is failing, but every provider row remains returned.
    if failing:
        return result, "failing"
    if pending:
        return result, "pending"
    if cancelled:
        return result, "failing"
    if any(row["bucket"] == "SKIPPING" or row["state"] == "SKIPPED" for row in result):
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
        value = _json("gh", ["api", "--hostname", "github.com", "graphql", "-f", f"query={query}", "-F", f"id={thread_id}",
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
        args = ["api", "--hostname", "github.com", "graphql", "-f", f"query={query}", "-F", f"o={owner}",
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


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _stable_collection(label: str, first: Any, second: Any) -> Any:
    if _canonical(first) != _canonical(second):
        raise CandidateChanged(f"{label} changed during bounded observation")
    return second


def read_pr(root: Path, url: str, *, expected_repository: str | None = None,
            expected_base: str | None = None, expected_base_sha: str | None = None,
            expected_head: str | None = None, expected_head_sha: str | None = None,
            expected_source_digest: str | None = None) -> dict[str, Any]:
    owner, repo, number = _pr_url(url)
    repository = f"{owner}/{repo}"
    actual_repository = remote_identity(root)
    if repository.lower() != actual_repository.lower():
        raise IdentityMismatch("PR repository differs from configured remote")
    fields = ("number,url,state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid,"
              "mergeStateStatus,mergeable,reviewDecision,reviewRequests,reviews,comments,"
              "statusCheckRollup,mergedAt,closedAt,updatedAt")
    value = _validate_pr(_json("gh", ["pr", "view", url, "--repo", _repo_arg(actual_repository), "--json", fields], root), url, owner, repo, number)
    actual_base_sha = pushed_head(root, value["baseRefName"])
    actual_head_sha = pushed_head(root, value["headRefName"])
    if value["baseRefOid"] != actual_base_sha or value["headRefOid"] != actual_head_sha:
        raise CandidateChanged("PR SHA differs from current remote branch")
    if expected_repository and actual_repository.lower() != expected_repository.lower():
        raise IdentityMismatch("PR repository expectation mismatch")
    if expected_base is not None and value["baseRefName"] != expected_base:
        raise IdentityMismatch("PR base branch mismatch")
    if expected_base_sha is not None and actual_base_sha != expected_base_sha:
        raise IdentityMismatch("PR base commit mismatch")
    if expected_head is not None and value["headRefName"] != expected_head:
        raise IdentityMismatch("PR head branch mismatch")
    if expected_head_sha is not None and actual_head_sha != expected_head_sha:
        raise IdentityMismatch("PR head commit mismatch")
    checks, status = _required_checks(root, url, actual_repository)
    threads = _threads(root, owner, repo, number)
    value["requiredChecks"] = checks
    value["requiredChecksStatus"] = status
    value["reviewThreads"] = threads
    actual_source_digest = source_identity(root, actual_head_sha)
    if expected_source_digest is not None and actual_source_digest != expected_source_digest:
        raise IdentityMismatch("PR source identity mismatch")
    # Final fence after all network/page reads: branch and PR identity must
    # remain stable from the initial observation through the returned result.
    final_value = _validate_pr(_json("gh", ["pr", "view", url, "--repo", _repo_arg(actual_repository), "--json", fields], root), url, owner, repo, number)
    final_base_sha = pushed_head(root, final_value["baseRefName"])
    final_head_sha = pushed_head(root, final_value["headRefName"])
    final_digest = source_identity(root, final_head_sha)
    final_checks, final_status = _required_checks(root, url, actual_repository)
    final_threads = _threads(root, owner, repo, number)
    for field in ("reviews", "comments", "reviewRequests", "statusCheckRollup"):
        _stable_collection("PR " + field, value[field], final_value[field])
    _stable_collection("required checks", value["requiredChecks"], final_checks)
    _stable_collection("required-check status", value["requiredChecksStatus"], final_status)
    _stable_collection("review threads", value["reviewThreads"], final_threads)
    if (final_value["baseRefName"] != value["baseRefName"] or final_value["headRefName"] != value["headRefName"]
            or final_value["baseRefOid"] != value["baseRefOid"] or final_value["headRefOid"] != value["headRefOid"]
            or final_value["url"] != value["url"] or final_value["number"] != value["number"]
            or final_value["state"] != value["state"] or final_value["isDraft"] != value["isDraft"]
            or final_value["updatedAt"] != value["updatedAt"] or final_value["mergedAt"] != value["mergedAt"]
            or final_value["closedAt"] != value["closedAt"] or final_value["mergeStateStatus"] != value["mergeStateStatus"]
            or final_value["mergeable"] != value["mergeable"] or final_value["reviewDecision"] != value["reviewDecision"]
            or final_base_sha != actual_base_sha or final_head_sha != actual_head_sha
            or final_digest != actual_source_digest):
        raise CandidateChanged("PR or remote branch identity changed during readback")
    if expected_base_sha is not None and final_base_sha != expected_base_sha:
        raise CandidateChanged("final base identity mismatch")
    if expected_head_sha is not None and final_head_sha != expected_head_sha:
        raise CandidateChanged("final head identity mismatch")
    if expected_source_digest is not None and final_digest != expected_source_digest:
        raise CandidateChanged("final source identity mismatch")
    final_value["requiredChecks"] = final_checks
    final_value["requiredChecksStatus"] = final_status
    final_value["reviewThreads"] = final_threads
    final_value["sourceDigest"] = final_digest
    final_value["observation"] = {
        "mode": "bounded-two-pass-stability",
        "passes": 2,
        "atomic": False,
        "dynamicCollections": ["reviews", "comments", "reviewRequests", "statusCheckRollup",
                                "requiredChecks", "reviewThreads"],
    }
    return final_value


def _existing_open(root: Path, repository: str, base: str, head: str, base_sha: str,
                   head_sha: str, source_digest: str) -> dict[str, Any] | None:
    rows = _json("gh", ["pr", "list", "--repo", _repo_arg(repository), "--state", "open", "--base", base,
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


def _close_created(root: Path, repository: str, url: str, *, base: str, head: str,
                   base_sha: str, head_sha: str) -> str:
    # A syntactic URL is not a compensation binding. Independently read the
    # candidate and require the attempted branch names and OIDs before close.
    owner, name, number = _pr_url(url)
    if f"{owner}/{name}".lower() != repository.lower():
        raise IdentityMismatch("created PR repository differs from verified repository")
    fields = _json("gh", ["pr", "view", url, "--repo", _repo_arg(repository), "--json",
                           "number,url,state,baseRefName,headRefName,baseRefOid,headRefOid"], root)
    required = {"number", "url", "state", "baseRefName", "headRefName", "baseRefOid", "headRefOid"}
    if not isinstance(fields, dict) or set(fields) != required \
            or fields["number"] != number or fields["url"] != url \
            or fields["baseRefName"] != base or fields["headRefName"] != head \
            or fields["baseRefOid"] != base_sha or fields["headRefOid"] != head_sha \
            or fields["state"] != "OPEN":
        raise DeliveryError("created PR compensation target is not safely bound")
    _run("gh", ["pr", "close", url, "--repo", _repo_arg(repository)], root)
    closed = _json("gh", ["pr", "view", url, "--repo", _repo_arg(repository), "--json", "number,url,state"], root)
    if not isinstance(closed, dict) or set(closed) != {"number", "url", "state"} \
            or closed["number"] != number or closed["url"] != url or closed["state"] != "CLOSED":
        raise DeliveryError("created PR compensation is unconfirmed")
    return "confirmed"


def _recover_created(root: Path, repository: str, base: str, head: str, base_sha: str,
                     head_sha: str, digest: str, cause: DeliveryError) -> dict[str, Any]:
    try:
        recovered = _existing_open(root, repository, base, head, base_sha, head_sha, digest)
    except DeliveryError as recovery_error:
        raise DeliveryError(f"created-but-unverified; compensation uncertain; exact recovery failed: {cause}; {recovery_error}") from cause
    if recovered is not None:
        return recovered
    raise DeliveryError(f"created-but-unverified; compensation unavailable; exact candidate not found: {cause}") from cause


def create_pr(root: Path, *, base: str, head: str, title: str, body: str,
              expected_repository: str | None = None, expected_base_sha: str | None = None,
              expected_head_sha: str | None = None, expected_source_digest: str | None = None) -> dict[str, Any]:
    if not base or not head or not title or len(title) > 256 or len(body) > 100000 \
            or not isinstance(expected_repository, str) or not REPOSITORY_RE.fullmatch(expected_repository) \
            or not isinstance(expected_base_sha, str) or not SHA_RE.fullmatch(expected_base_sha) \
            or not isinstance(expected_head_sha, str) or not SHA_RE.fullmatch(expected_head_sha) \
            or not isinstance(expected_source_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_source_digest):
        raise InvalidRequest("create requires explicit repository, base_sha, head_sha, and source_digest")
    repository = remote_identity(root)
    if expected_repository and expected_repository.lower() != repository.lower():
        raise IdentityMismatch("repository expectation mismatch")
    base_sha = pushed_head(root, base)
    head_sha = pushed_head(root, head)
    if expected_base_sha and expected_base_sha != base_sha:
        raise IdentityMismatch("base commit expectation mismatch")
    if expected_head_sha and expected_head_sha != head_sha:
        raise IdentityMismatch("head commit expectation mismatch")
    if not SHA_RE.fullmatch(base_sha) or not SHA_RE.fullmatch(head_sha):
        raise InvalidRequest("PR commit expectation is not a full Git object id")
    digest = source_identity(root, head_sha)
    if expected_source_digest is not None and expected_source_digest != digest:
        raise IdentityMismatch("PR source identity expectation mismatch")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise InvalidRequest("PR source identity is invalid")
    existing = _existing_open(root, repository, base, head, base_sha, head_sha, digest)
    if existing is not None:
        return existing
    # There is no GitHub/gh atomic expected-head constraint for branch-name
    # creation. Repeat the exact identity preflight immediately before mutation.
    fresh_repository = remote_identity(root)
    fresh_base_sha = pushed_head(root, base)
    fresh_head_sha = pushed_head(root, head)
    fresh_digest = source_identity(root, fresh_head_sha)
    if (fresh_repository != repository or fresh_base_sha != base_sha or fresh_head_sha != head_sha
            or fresh_digest != digest):
        raise CandidateChanged("candidate branch moved before PR creation")
    try:
        url = _run("gh", ["pr", "create", "--repo", _repo_arg(repository), "--base", base, "--head", head,
                           "--title", title, "--body", body], root)
    except DeliveryError as exc:
        # Transport failure is potentially mutating; never imply that create
        # definitely did not happen.
        return _recover_created(root, repository, base, head, base_sha, head_sha, digest, exc)
    try:
        created_owner, created_name, _ = _pr_url(url)
        if f"{created_owner}/{created_name}".lower() != repository.lower():
            raise DeliveryError("returned PR repository differs from verified repository")
    except DeliveryError as exc:
        # Every unverified output variant uses the same exact recovery path;
        # malformed or wrong-repository text never reaches gh close.
        return _recover_created(root, repository, base, head, base_sha, head_sha, digest, exc)

    try:
        return read_pr(root, url, expected_repository=repository, expected_base=base,
                       expected_base_sha=base_sha, expected_head=head, expected_head_sha=head_sha,
                       expected_source_digest=digest)
    except DeliveryError as exc:
        try:
            outcome = _close_created(root, repository, url, base=base, head=head,
                                     base_sha=base_sha, head_sha=head_sha)
        except DeliveryError as close_error:
            message = f"post-create verification failed: {exc}; compensation uncertain: {close_error}"
            raise type(exc)(message) from exc
        message = f"post-create verification failed: {exc}; compensation {outcome}"
        raise type(exc)(message) from exc


def snapshot(root: Path, *, branch: str, base: str = "campaign/razr-pi-next") -> dict[str, Any]:
    repository = remote_identity(root)
    commit = pushed_head(root, branch)
    return {"repository": repository, "branch": branch, "commit": commit,
            "source_digest": source_identity(root, commit), "base": base,
            "base_commit": pushed_head(root, base)}
