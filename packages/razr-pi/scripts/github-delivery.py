#!/usr/bin/env python3
"""Closed JSON CLI for the adapter-owned GitHub delivery boundary.

This path performs no Razr workflow mutation. It is intentionally separate
from Pi's frozen five-command extension surface.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
from adapter import github_delivery as delivery  # noqa: E402


def fail(code: str, message: str) -> int:
    print(json.dumps({"schema_version": 1, "ok": False,
                      "error": {"code": code, "message": message, "retryable": False,
                                "details": {}}}, sort_keys=True))
    return 1


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict) or set(request) - {"schema_version", "operation", "root", "branch", "base", "url", "title", "body", "repository", "base_sha", "head_sha", "source_digest"}:
            return fail("INVALID_REQUEST", "closed request fields required")
        if request.get("schema_version") != 1 or request.get("operation") not in {"snapshot", "read", "create"}:
            return fail("INVALID_REQUEST", "invalid schema or operation")
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
    except (KeyError, TypeError, ValueError, delivery.DeliveryError) as exc:
        return fail("INVALID_REQUEST" if isinstance(exc, (KeyError, TypeError, ValueError)) else "PROVIDER_UNAVAILABLE", str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
