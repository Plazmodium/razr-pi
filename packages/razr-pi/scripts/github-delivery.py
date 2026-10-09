#!/usr/bin/env python3
"""Closed JSON CLI for the adapter-owned GitHub delivery boundary.

This path performs no Razr workflow mutation. It is intentionally separate
from Pi's frozen five-command extension surface.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
from adapter import github_delivery as delivery  # noqa: E402


def fail(code: str, message: str) -> int:
    print(json.dumps({"schema_version": 1, "ok": False,
                      "error": {"code": code, "message": message, "retryable": False,
                                "details": {}}}, sort_keys=True))
    return 1


def _string(request: dict, key: str, required: bool = False) -> None:
    if key not in request:
        if required:
            raise ValueError(f"missing {key}")
        return
    if not isinstance(request[key], str) or not request[key]:
        raise ValueError(f"invalid {key}")


def _validate_request(request: object) -> dict:
    if not isinstance(request, dict) or not isinstance(request.get("schema_version"), int) \
            or isinstance(request.get("schema_version"), bool) or request["schema_version"] != 1:
        raise ValueError("invalid schema")
    operation = request.get("operation")
    if operation not in {"snapshot", "read", "create"} or not isinstance(operation, str):
        raise ValueError("invalid operation")
    common = {"schema_version", "operation", "root"}
    if "root" in request:
        _string(request, "root")
    if operation == "snapshot":
        allowed, required = common | {"branch", "base"}, ("branch",)
    elif operation == "read":
        allowed, required = common | {"url", "repository", "base", "branch", "base_sha", "head_sha", "source_digest"}, ("url",)
    else:
        allowed, required = common | {"base", "branch", "title", "body", "repository", "base_sha", "head_sha", "source_digest"}, ("base", "branch", "title", "body", "repository", "base_sha", "head_sha", "source_digest")
    if set(request) - allowed:
        raise ValueError("unknown operation field")
    for key in required:
        _string(request, key, True)
    for key in allowed - set(required) - {"schema_version", "operation"}:
        _string(request, key)
    for key in ("branch", "base"):
        if key in request and (not re.fullmatch(r"[A-Za-z0-9._/-]{1,255}", request[key]) or ".." in request[key]):
            raise ValueError(f"invalid {key}")
    if operation == "read":
        delivery._pr_url(request["url"])
    for key in ("base_sha", "head_sha"):
        if key in request and not delivery.SHA_RE.fullmatch(request[key]):
            raise ValueError(f"invalid {key}")
    if "source_digest" in request and not re.fullmatch(r"sha256:[0-9a-f]{64}", request["source_digest"]):
        raise ValueError("invalid source_digest")
    if "repository" in request and not re.fullmatch(r"[^/]+/[^/]+", request["repository"]):
        raise ValueError("invalid repository")
    return request


def main() -> int:
    try:
        request = _validate_request(json.load(sys.stdin))
        root = Path(request.get("root", "."))
        delivery.auth_preflight(root)
        operation = request["operation"]
        if operation == "snapshot":
            result = delivery.snapshot(root, branch=request["branch"], base=request.get("base", "campaign/razr-pi-next"))
        elif operation == "read":
            result = delivery.read_pr(root, request["url"], expected_repository=request.get("repository"),
                                      expected_base=request.get("base"), expected_base_sha=request.get("base_sha"),
                                      expected_head=request.get("branch"), expected_head_sha=request.get("head_sha"),
                                      expected_source_digest=request.get("source_digest"))
        else:
            result = delivery.create_pr(root, base=request["base"], head=request["branch"],
                                        title=request["title"], body=request["body"],
                                        expected_repository=request.get("repository"),
                                        expected_base_sha=request.get("base_sha"),
                                        expected_head_sha=request.get("head_sha"),
                                        expected_source_digest=request.get("source_digest"))
        print(json.dumps({"schema_version": 1, "ok": True, "result": result}, sort_keys=True))
        return 0
    except delivery.CandidateChanged as exc:
        return fail("CANDIDATE_CHANGED", str(exc))
    except delivery.IdentityMismatch as exc:
        return fail("IDENTITY_MISMATCH", str(exc))
    except (KeyError, TypeError, ValueError, delivery.InvalidRequest) as exc:
        return fail("INVALID_REQUEST", str(exc))
    except delivery.DeliveryError as exc:
        return fail("PROVIDER_UNAVAILABLE", str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
