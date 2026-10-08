"""Repository-local append-only Memory adapter for M02.

This module stores experience, never workflow authority.  Records are immutable;
retirement and supersession are represented by new records linked to the old id.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Iterator

from .protocol import ProtocolError, canonical_json, raw_file_digest, validate_source_digest

GENESIS = "sha256:" + "0" * 64
KINDS = frozenset(("observation", "rationale", "learning", "workaround", "failure_pattern",
                   "environment_quirk", "navigation_hint", "preference", "unresolved_question"))
CONFIDENCES = frozenset(("low", "medium", "high"))
STATUSES = frozenset(("active", "stale", "retired", "superseded"))
DIGEST_DOMAINS = frozenset(("raw_file", "canonical_json", "core_source", "executable", "git_object"))
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SECRET_RE = re.compile(
    r"(?i)(?:authorization:\s*bearer\s+\S+|(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password)\s*[:=]\s*\S+|-----BEGIN [A-Z ]*PRIVATE KEY-----|cookie\s*[:=])"
)
MAX_LIMIT = 100
MAX_SUMMARY = 2000
MAX_TAGS = 32
MAX_TAG = 80
MAX_EVIDENCE = 32


def _fail(code: str, message: str, **details: object) -> ProtocolError:
    return ProtocolError(code, message, details=details)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _repo_path(value: str, *, allow_dot: bool = False) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise _fail("INVALID_REQUEST", "repository-relative POSIX path required")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value or (not allow_dot and value == "."):
        raise _fail("PATH_ESCAPE", "path is outside the repository")
    return value


def _safe_text(value: str, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise _fail("INVALID_REQUEST", f"invalid {label}")
    if SECRET_RE.search(value):
        raise _fail("INVALID_REQUEST", f"restricted or secret-like {label}")
    return value


def _digest(value: str, domain: str) -> str:
    if domain == "git_object":
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
            raise _fail("INVALID_REQUEST", "invalid Git evidence digest")
        return value
    if domain not in DIGEST_DOMAINS or not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise _fail("INVALID_REQUEST", "invalid evidence digest")
    return value


def _record_digest(record: dict[str, object]) -> str:
    body = {key: value for key, value in record.items() if key != "record_digest"}
    return raw_file_digest(canonical_json(body))


def _record_id(seed: dict[str, object]) -> str:
    return "mem-" + hashlib.sha256(canonical_json(seed)).hexdigest()[:32]


class MemoryStore:
    """An inspectable JSONL store rooted at one canonical repository directory."""
    def __init__(self, repository_root: Path | str, *, now=_now, limit: int = 20):
        root = Path(repository_root)
        if not root.is_absolute() or root.resolve() != root or not root.is_dir() or root.is_symlink():
            raise _fail("INVALID_REQUEST", "repository root must be canonical")
        self.root = root
        self.path = root / ".razr/memory.jsonl"
        self.lock_path = root / ".razr/memory.jsonl.lock"
        self._now = now
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
            raise _fail("INVALID_REQUEST", "invalid memory limit")
        self.default_limit = limit

    @contextmanager
    def _locked(self) -> Iterator[object]:
        self.path.parent.mkdir(mode=0o755, exist_ok=True)
        try:
            with self.lock_path.open("a+") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    yield lock
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except (OSError, BlockingIOError) as exc:
            raise _fail("LOCK_UNAVAILABLE", "memory lock unavailable", retryable=True) from exc

    def _read_locked(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        try:
            raw = self.path.read_bytes()
            if raw and not raw.endswith(b"\n"):
                raise _fail("LEDGER_CORRUPT", "partial final memory record")
            records: list[dict[str, object]] = []
            previous = GENESIS
            ids: set[str] = set()
            for line in raw.splitlines():
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise _fail("LEDGER_CORRUPT", "malformed memory record") from exc
                self._validate_record(value)
                if value["id"] in ids or value["prev_digest"] != previous:
                    raise _fail("LEDGER_CORRUPT", "broken memory chain or duplicate id")
                if value["record_digest"] != _record_digest(value):
                    raise _fail("LEDGER_CORRUPT", "invalid memory record digest")
                records.append(value)
                ids.add(value["id"])
                previous = value["record_digest"]
            return records
        except OSError as exc:
            raise _fail("LEDGER_CORRUPT", "memory ledger unavailable") from exc

    def _validate_record(self, value: object) -> None:
        if not isinstance(value, dict) or any(v is None for v in value.values()):
            raise _fail("LEDGER_CORRUPT", "invalid memory record shape")
        required = {"schema_version", "id", "kind", "summary", "scope", "confidence", "status",
                    "created_at", "updated_at", "evidence", "prev_digest", "record_digest"}
        optional = {"tags", "supersedes", "related_ids"}
        if set(value) - required - optional or not required <= set(value) or value["schema_version"] != 1:
            raise _fail("LEDGER_CORRUPT", "memory record fields are not closed")
        if not isinstance(value["id"], str) or not ID_RE.fullmatch(value["id"]):
            raise _fail("LEDGER_CORRUPT", "invalid memory id")
        if value["kind"] not in KINDS or value["confidence"] not in CONFIDENCES or value["status"] not in STATUSES:
            raise _fail("LEDGER_CORRUPT", "invalid memory enum")
        if not isinstance(value["summary"], str) or not value["summary"] or len(value["summary"]) > MAX_SUMMARY:
            raise _fail("LEDGER_CORRUPT", "invalid memory summary")
        scope = value["scope"]
        if not isinstance(scope, dict) or set(scope) != {"repository", "paths"}:
            raise _fail("LEDGER_CORRUPT", "invalid memory scope")
        validate_source_digest(scope["repository"])
        if not isinstance(scope["paths"], list):
            raise _fail("LEDGER_CORRUPT", "invalid memory scope paths")
        for path in scope["paths"]:
            _repo_path(path)
        for field in ("created_at", "updated_at"):
            if not isinstance(value[field], str) or not value[field].endswith("Z"):
                raise _fail("LEDGER_CORRUPT", "invalid memory timestamp")
        evidence = value["evidence"]
        if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE:
            raise _fail("LEDGER_CORRUPT", "invalid memory evidence")
        for item in evidence:
            if not isinstance(item, dict) or set(item) != {"type", "ref", "digest_domain", "digest"}:
                raise _fail("LEDGER_CORRUPT", "invalid evidence reference")
            if item["type"] not in {"path", "test", "commit", "config", "work_item", "external"}:
                raise _fail("LEDGER_CORRUPT", "invalid evidence type")
            _safe_text(item["ref"], "evidence reference", 512)
            _digest(item["digest"], item["digest_domain"])
            if item["type"] == "path" and item["digest_domain"] != "raw_file":
                raise _fail("LEDGER_CORRUPT", "path evidence must use raw-file digest")
        if value["prev_digest"] != GENESIS and not re.fullmatch(r"sha256:[0-9a-f]{64}", value["prev_digest"]):
            raise _fail("LEDGER_CORRUPT", "invalid chain digest")
        if "tags" in value and (not isinstance(value["tags"], list) or len(value["tags"]) > MAX_TAGS):
            raise _fail("LEDGER_CORRUPT", "invalid memory tags")
        for field in ("supersedes",):
            if field in value and (not isinstance(value[field], str) or not ID_RE.fullmatch(value[field])):
                raise _fail("LEDGER_CORRUPT", "invalid memory relation")
        if "related_ids" in value and (not isinstance(value["related_ids"], list)
                                        or any(not isinstance(item, str) or not ID_RE.fullmatch(item)
                                               for item in value["related_ids"])):
            raise _fail("LEDGER_CORRUPT", "invalid memory relations")

    def _append(self, record: dict[str, object]) -> dict[str, object]:
        with self._locked():
            records = self._read_locked()
            if any(row["id"] == record["id"] for row in records):
                raise _fail("LEDGER_CORRUPT", "duplicate memory id")
            record["prev_digest"] = records[-1]["record_digest"] if records else GENESIS
            record["record_digest"] = _record_digest(record)
            self._validate_record(record)
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
                try:
                    payload = canonical_json(record) + b"\n"
                    written = os.write(fd, payload)
                    if written != len(payload):
                        raise _fail("LEDGER_CORRUPT", "interrupted memory append")
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except ProtocolError:
                raise
            except OSError as exc:
                raise _fail("LOCK_UNAVAILABLE", "memory append unavailable", retryable=True) from exc
        return record

    def remember(self, *, kind: str, summary: str, repository: str, paths: list[str] | None = None,
                 confidence: str = "medium", evidence: list[dict[str, str]] | None = None,
                 tags: list[str] | None = None, related_ids: list[str] | None = None,
                 record_id: str | None = None) -> dict[str, object]:
        _safe_text(summary, "summary", MAX_SUMMARY)
        if kind not in KINDS or confidence not in CONFIDENCES:
            raise _fail("INVALID_REQUEST", "invalid memory kind or confidence")
        validate_source_digest(repository)
        paths = [] if paths is None else [_repo_path(path) for path in paths]
        evidence = [] if evidence is None else evidence
        checked_evidence = []
        for item in evidence:
            if not isinstance(item, dict) or set(item) != {"type", "ref", "digest_domain", "digest"}:
                raise _fail("INVALID_REQUEST", "evidence must be closed")
            _safe_text(item["ref"], "evidence reference", 512)
            if item["type"] == "path":
                relative = _repo_path(item["ref"])
                target = (self.root / relative).resolve()
                if not target.is_relative_to(self.root) or target.is_symlink() or not target.is_file():
                    raise _fail("PATH_ESCAPE", "evidence path is unavailable or restricted")
                content = target.read_bytes()
                if SECRET_RE.search(content.decode("utf-8", errors="replace")):
                    raise _fail("INVALID_REQUEST", "secret-like evidence refused")
                if item["digest_domain"] != "raw_file" or item["digest"] != raw_file_digest(content):
                    raise _fail("INVALID_REQUEST", "evidence digest does not match")
            _digest(item["digest"], item["digest_domain"])
            checked_evidence.append(dict(item))
        if tags is not None and (not isinstance(tags, list) or len(tags) > MAX_TAGS):
            raise _fail("INVALID_REQUEST", "invalid tags")
        tags = [] if tags is None else [_safe_text(tag, "tag", MAX_TAG) for tag in tags]
        seed = {"kind": kind, "summary": summary, "scope": {"repository": repository, "paths": paths},
                "evidence": checked_evidence, "tags": tags, "related_ids": related_ids or []}
        record = {"schema_version": 1, "id": record_id or _record_id(seed), "kind": kind, "summary": summary,
                  "scope": seed["scope"], "confidence": confidence, "status": "active", "created_at": self._now(),
                  "updated_at": self._now(), "evidence": checked_evidence}
        if tags:
            record["tags"] = tags
        if related_ids:
            record["related_ids"] = related_ids
        return self._append(record)

    def records(self) -> list[dict[str, object]]:
        with self._locked():
            return self._read_locked()

    def inspect(self) -> list[dict[str, object]]:
        return self.records()

    def recall(self, query: str = "", *, paths: list[str] | None = None, limit: int | None = None) -> dict[str, object]:
        if not isinstance(query, str) or len(query) > MAX_SUMMARY:
            raise _fail("INVALID_REQUEST", "invalid memory query")
        limit = self.default_limit if limit is None else limit
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
            raise _fail("INVALID_REQUEST", "invalid memory limit")
        wanted = set(_repo_path(path) for path in (paths or []))
        all_records = self.records()
        superseded = {row["supersedes"] for row in all_records if "supersedes" in row}
        candidates = []
        needle = query.casefold()
        stale_ids = []
        for row in all_records:
            if row["id"] in superseded or row["status"] in {"retired", "superseded"}:
                continue
            stale = self._is_stale(row)
            view = dict(row)
            if stale:
                view["status"] = "stale"
                stale_ids.append(row["id"])
            if wanted and not wanted.intersection(row["scope"]["paths"]):
                continue
            haystack = (row["summary"] + " " + " ".join(row.get("tags", []))).casefold()
            if needle and needle not in haystack:
                continue
            candidates.append(view)
        candidates.sort(key=lambda row: (row["status"] != "active", row["id"]))
        return {"records": candidates[:limit], "omitted_count": max(0, len(candidates) - limit),
                "stale_ids": sorted(stale_ids),
                "query_digest": raw_file_digest(canonical_json({"query": query, "paths": sorted(wanted), "limit": limit}))}

    def _is_stale(self, row: dict[str, object]) -> bool:
        for item in row["evidence"]:
            if item["type"] == "path" and item["digest_domain"] == "raw_file":
                target = self.root / item["ref"]
                try:
                    if target.is_symlink() or not target.is_file() or raw_file_digest(target.read_bytes()) != item["digest"]:
                        return True
                except OSError:
                    return True
        return False

    def retire(self, record_id: str) -> dict[str, object]:
        return self._transition(record_id, "retired")

    def supersede(self, record_id: str, *, summary: str | None = None, **changes: object) -> dict[str, object]:
        return self._transition(record_id, "superseded", summary=summary, **changes)

    def _transition(self, record_id: str, status: str, *, summary: str | None = None, **changes: object) -> dict[str, object]:
        rows = self.records()
        old = next((row for row in rows if row["id"] == record_id), None)
        if old is None:
            raise _fail("INVALID_REQUEST", "memory record not found")
        record = dict(old)
        record["id"] = _record_id({"transition": status, "source": record_id, "time": self._now()})
        record["status"] = "active" if status == "superseded" else status
        record["summary"] = summary or old["summary"]
        record["created_at"] = self._now()
        record["updated_at"] = record["created_at"]
        record["supersedes"] = record_id
        record.update(changes)
        record.pop("prev_digest", None)
        record.pop("record_digest", None)
        return self._append(record)

    def validate(self) -> dict[str, object]:
        return {"records": len(self.records()), "valid": True}

    revalidate = validate
