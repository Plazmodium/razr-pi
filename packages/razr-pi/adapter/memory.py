"""Deterministic repository-local append-only Memory adapter for M02."""
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
EVIDENCE_TYPES = frozenset(("path", "test", "commit", "config", "work_item", "external"))
DIGEST_DOMAINS = frozenset(("raw_file", "canonical_json", "core_source", "executable", "git_object"))
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$")
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


def _repo_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise _fail("INVALID_REQUEST", "repository-relative POSIX path required")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value or value == ".":
        raise _fail("PATH_ESCAPE", "path is outside the repository")
    return value


def _safe_text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise _fail("INVALID_REQUEST", f"invalid {label}")
    if SECRET_RE.search(value):
        raise _fail("INVALID_REQUEST", f"restricted or secret-like {label}")
    return value


def _digest(value: object, domain: object) -> str:
    if domain == "git_object":
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
            raise _fail("INVALID_REQUEST", "invalid Git evidence digest")
        return value
    if domain not in DIGEST_DOMAINS or not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise _fail("INVALID_REQUEST", "invalid evidence digest")
    return value


def _record_digest(record: dict[str, object]) -> str:
    return raw_file_digest(canonical_json({k: v for k, v in record.items() if k != "record_digest"}))


def _record_id(logical: dict[str, object]) -> str:
    return "mem-" + hashlib.sha256(canonical_json(logical)).hexdigest()[:32]


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant: {value}")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _parse_record_line(line: bytes) -> dict[str, object]:
    try:
        value = json.loads(line[:-1].decode("utf-8"), object_pairs_hook=_pairs,
                            parse_constant=_reject_constant)
        if canonical_json(value) + b"\n" != line:
            raise ValueError("noncanonical JSON bytes")
    except (ProtocolError, UnicodeDecodeError, ValueError, TypeError) as exc:
        raise _fail("LEDGER_CORRUPT", "malformed or noncanonical memory record") from exc
    if not isinstance(value, dict):
        raise _fail("LEDGER_CORRUPT", "memory record must be an object")
    return value


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not UTC_RE.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        return parsed.tzinfo == timezone.utc
    except ValueError:
        return False


class MemoryStore:
    """Stable M03/M05 surface: remember, recall, inspect, retire, supersede, validate."""
    def __init__(self, repository_root: Path | str, *, now=_now, limit: int = 20):
        root = Path(repository_root)
        if not root.is_absolute() or root.resolve() != root or not root.is_dir() or root.is_symlink():
            raise _fail("INVALID_REQUEST", "repository root must be canonical")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
            raise _fail("INVALID_REQUEST", "invalid memory limit")
        self.root = root
        self.path = root / ".razr/memory.jsonl"
        self.lock_path = root / ".razr/memory.jsonl.lock"
        self._now = now
        self.default_limit = limit

    @contextmanager
    def _locked(self) -> Iterator[None]:
        try:
            self.path.parent.mkdir(mode=0o755, exist_ok=True)
            with self.lock_path.open("a+") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except (OSError, BlockingIOError) as exc:
            raise _fail("LOCK_UNAVAILABLE", "memory lock unavailable", retryable=True) from exc

    def _read_locked(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            raise _fail("LEDGER_CORRUPT", "memory ledger unavailable") from exc
        if raw and not raw.endswith(b"\n"):
            raise _fail("LEDGER_CORRUPT", "partial final memory record")
        records: list[dict[str, object]] = []
        previous = GENESIS
        ids: set[str] = set()
        for line in raw.splitlines(keepends=True):
            value = _parse_record_line(line)
            self._validate_record(value, corruption=True)
            if value["id"] in ids or value["prev_digest"] != previous:
                raise _fail("LEDGER_CORRUPT", "broken memory chain or duplicate id")
            if value["record_digest"] != _record_digest(value):
                raise _fail("LEDGER_CORRUPT", "invalid memory record digest")
            records.append(value)
            ids.add(value["id"])
            previous = value["record_digest"]
        return records

    def _validate_record(self, value: object, *, corruption: bool = False) -> None:
        fail = (lambda message: _fail("LEDGER_CORRUPT", message)) if corruption else (lambda message: _fail("INVALID_REQUEST", message))
        if not isinstance(value, dict) or any(v is None for v in value.values()):
            raise fail("invalid memory record shape")
        required = {"schema_version", "id", "kind", "summary", "scope", "confidence", "status",
                    "created_at", "updated_at", "evidence", "prev_digest", "record_digest"}
        optional = {"tags", "supersedes", "related_ids"}
        if set(value) - required - optional or not required <= set(value) or value["schema_version"] != 1:
            raise fail("memory record fields are not closed")
        if not isinstance(value["id"], str) or not ID_RE.fullmatch(value["id"]):
            raise fail("invalid memory id")
        if (not isinstance(value["kind"], str) or value["kind"] not in KINDS
                or not isinstance(value["confidence"], str) or value["confidence"] not in CONFIDENCES
                or not isinstance(value["status"], str) or value["status"] not in STATUSES):
            raise fail("invalid memory enum")
        try:
            _safe_text(value["summary"], "summary", MAX_SUMMARY)
        except ProtocolError as exc:
            raise fail("invalid or restricted memory summary") from exc
        scope = value["scope"]
        if not isinstance(scope, dict) or set(scope) != {"repository", "paths"}:
            raise fail("invalid memory scope")
        try:
            validate_source_digest(scope["repository"])
            if not isinstance(scope["paths"], list):
                raise ValueError
            for path in scope["paths"]:
                _repo_path(path)
        except (ProtocolError, ValueError) as exc:
            raise fail("invalid memory scope") from exc
        if not _valid_timestamp(value["created_at"]) or not _valid_timestamp(value["updated_at"]):
            raise fail("invalid UTC RFC3339 timestamp")
        evidence = value["evidence"]
        if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE:
            raise fail("invalid memory evidence")
        for item in evidence:
            if not isinstance(item, dict) or set(item) != {"type", "ref", "digest_domain", "digest"}:
                raise fail("invalid evidence reference")
            if not isinstance(item["type"], str) or item["type"] not in EVIDENCE_TYPES:
                raise fail("invalid evidence type")
            try:
                _safe_text(item["ref"], "evidence reference", 512)
                _digest(item["digest"], item["digest_domain"])
            except ProtocolError as exc:
                raise fail("invalid or restricted evidence reference") from exc
            if item["type"] == "path" and item["digest_domain"] != "raw_file":
                raise fail("path evidence must use raw-file digest")
        if (not isinstance(value["prev_digest"], str)
                or (value["prev_digest"] != GENESIS and not re.fullmatch(r"sha256:[0-9a-f]{64}", value["prev_digest"]))):
            raise fail("invalid chain digest")
        if not isinstance(value["record_digest"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["record_digest"]):
            raise fail("invalid record digest")
        if "tags" in value:
            if not isinstance(value["tags"], list) or len(value["tags"]) > MAX_TAGS:
                raise fail("invalid memory tags")
            for tag in value["tags"]:
                try:
                    _safe_text(tag, "tag", MAX_TAG)
                except ProtocolError as exc:
                    raise fail("invalid or restricted memory tag") from exc
        if "supersedes" in value and (not isinstance(value["supersedes"], str) or not ID_RE.fullmatch(value["supersedes"])):
            raise fail("invalid supersedes relation")
        if "related_ids" in value and (not isinstance(value["related_ids"], list)
                                        or any(not isinstance(item, str) or not ID_RE.fullmatch(item)
                                               for item in value["related_ids"])):
            raise fail("invalid related ids")

    def _write_all(self, fd: int, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            try:
                count = os.write(fd, payload[offset:])
            except InterruptedError:
                continue
            except OSError as exc:
                raise _fail("LOCK_UNAVAILABLE", "memory append unavailable", retryable=True) from exc
            if not isinstance(count, int) or count <= 0:
                raise _fail("LEDGER_CORRUPT", "memory append made no progress")
            offset += count
        try:
            os.fsync(fd)
        except OSError as exc:
            raise _fail("LOCK_UNAVAILABLE", "memory fsync unavailable", retryable=True) from exc

    def _append_locked(self, record: dict[str, object], records: list[dict[str, object]]) -> dict[str, object]:
        if any(row["id"] == record["id"] for row in records):
            raise _fail("LEDGER_CORRUPT", "duplicate memory id")
        record["prev_digest"] = records[-1]["record_digest"] if records else GENESIS
        record["record_digest"] = _record_digest(record)
        self._validate_record(record)
        payload = canonical_json(record) + b"\n"
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                self._write_all(fd, payload)
            finally:
                os.close(fd)
        except ProtocolError:
            raise
        except OSError as exc:
            raise _fail("LOCK_UNAVAILABLE", "memory append unavailable", retryable=True) from exc
        return record

    def _logical(self, record: dict[str, object]) -> dict[str, object]:
        return {key: record[key] for key in ("kind", "summary", "scope", "confidence", "evidence", "tags", "related_ids") if key in record}

    def _prepare(self, *, kind: str, summary: str, repository: str, paths: list[str] | None,
                 confidence: str, evidence: list[dict[str, str]] | None, tags: list[str] | None,
                 related_ids: list[str] | None) -> dict[str, object]:
        _safe_text(summary, "summary", MAX_SUMMARY)
        if kind not in KINDS or confidence not in CONFIDENCES:
            raise _fail("INVALID_REQUEST", "invalid memory kind or confidence")
        validate_source_digest(repository)
        scope = {"repository": repository, "paths": [] if paths is None else [_repo_path(path) for path in paths]}
        checked = self._prepare_evidence([] if evidence is None else evidence)
        checked_tags = [] if tags is None else tags
        if not isinstance(checked_tags, list) or len(checked_tags) > MAX_TAGS:
            raise _fail("INVALID_REQUEST", "invalid tags")
        checked_tags = [_safe_text(tag, "tag", MAX_TAG) for tag in checked_tags]
        checked_relations = [] if related_ids is None else related_ids
        if not isinstance(checked_relations, list) or any(not isinstance(item, str) or not ID_RE.fullmatch(item) for item in checked_relations):
            raise _fail("INVALID_REQUEST", "invalid related ids")
        result: dict[str, object] = {"kind": kind, "summary": summary, "scope": scope, "confidence": confidence,
                                     "evidence": checked, "tags": checked_tags, "related_ids": checked_relations}
        return result

    def _safe_evidence_path(self, ref: str) -> Path:
        relative = _repo_path(ref)
        candidate = self.root
        for component in PurePosixPath(relative).parts:
            candidate = candidate / component
            try:
                if os.path.islink(candidate):
                    raise _fail("PATH_ESCAPE", "symlink evidence path refused")
            except OSError as exc:
                raise _fail("PATH_ESCAPE", "evidence path unavailable") from exc
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise _fail("PATH_ESCAPE", "evidence path unavailable") from exc
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            raise _fail("PATH_ESCAPE", "evidence path escapes repository")
        checked = self.root
        for component in resolved.relative_to(self.root).parts:
            checked = checked / component
            try:
                if os.path.islink(checked):
                    raise _fail("PATH_ESCAPE", "symlink evidence path refused")
            except OSError as exc:
                raise _fail("PATH_ESCAPE", "evidence path unavailable") from exc
        return resolved

    def _prepare_evidence(self, evidence: list[dict[str, str]]) -> list[dict[str, str]]:
        if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE:
            raise _fail("INVALID_REQUEST", "invalid memory evidence")
        checked: list[dict[str, str]] = []
        for item in evidence:
            if not isinstance(item, dict) or set(item) != {"type", "ref", "digest_domain", "digest"}:
                raise _fail("INVALID_REQUEST", "evidence must be closed")
            if item["type"] not in EVIDENCE_TYPES:
                raise _fail("INVALID_REQUEST", "invalid evidence type")
            _safe_text(item["ref"], "evidence reference", 512)
            _digest(item["digest"], item["digest_domain"])
            if item["type"] == "path":
                if item["digest_domain"] != "raw_file":
                    raise _fail("INVALID_REQUEST", "path evidence must use raw-file digest")
                target = self._safe_evidence_path(item["ref"])
                content = target.read_bytes()
                if SECRET_RE.search(content.decode("utf-8", errors="replace")):
                    raise _fail("INVALID_REQUEST", "secret-like evidence refused")
                if item["digest"] != raw_file_digest(content):
                    raise _fail("INVALID_REQUEST", "evidence digest does not match")
            checked.append(dict(item))
        return checked

    def remember(self, *, kind: str, summary: str, repository: str, paths: list[str] | None = None,
                 confidence: str = "medium", evidence: list[dict[str, str]] | None = None,
                 tags: list[str] | None = None, related_ids: list[str] | None = None,
                 record_id: str | None = None) -> dict[str, object]:
        logical = self._prepare(kind=kind, summary=summary, repository=repository, paths=paths,
                                confidence=confidence, evidence=evidence, tags=tags, related_ids=related_ids)
        requested_id = record_id or _record_id(logical)
        if not ID_RE.fullmatch(requested_id):
            raise _fail("INVALID_REQUEST", "invalid memory id")
        record = {"schema_version": 1, "id": requested_id, **logical, "status": "active",
                  "created_at": self._now(), "updated_at": self._now()}
        record.pop("tags", None) if not logical["tags"] else None
        record.pop("related_ids", None) if not logical["related_ids"] else None
        with self._locked():
            rows = self._read_locked()
            existing = next((row for row in rows if row["id"] == requested_id), None)
            if existing is not None:
                if self._logical(existing) == self._logical(record) and existing["status"] == "active":
                    return existing
                raise _fail("INVALID_REQUEST", "memory id conflicts with different logical payload", id=requested_id)
            return self._append_locked(record, rows)

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
        candidates: list[dict[str, object]] = []
        stale_ids: list[str] = []
        needle = query.casefold()
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
            if needle and needle not in (row["summary"] + " " + " ".join(row.get("tags", []))).casefold():
                continue
            candidates.append(view)
        candidates.sort(key=lambda row: (row["status"] != "active", row["id"]))
        return {"records": candidates[:limit], "omitted_count": max(0, len(candidates) - limit),
                "stale_ids": sorted(stale_ids),
                "query_digest": raw_file_digest(canonical_json({"query": query, "paths": sorted(wanted), "limit": limit}))}

    def _is_stale(self, row: dict[str, object]) -> bool:
        for item in row["evidence"]:
            if item["type"] == "path" and item["digest_domain"] == "raw_file":
                try:
                    target = self._safe_evidence_path(item["ref"])
                    if raw_file_digest(target.read_bytes()) != item["digest"]:
                        return True
                except (OSError, ProtocolError):
                    return True
        return False

    def retire(self, record_id: str) -> dict[str, object]:
        return self._transition(record_id, retired=True)

    def supersede(self, record_id: str, *, summary: str, evidence: list[dict[str, str]] | None = None,
                  tags: list[str] | None = None, confidence: str | None = None) -> dict[str, object]:
        """Append an active replacement; kind/scope/schema/status/relations are protected."""
        if not isinstance(summary, str):
            raise _fail("INVALID_REQUEST", "supersede summary is required")
        return self._transition(record_id, retired=False, summary=summary, evidence=evidence,
                                tags=tags, confidence=confidence)

    def _transition(self, record_id: str, *, retired: bool, summary: str | None = None,
                    evidence: list[dict[str, str]] | None = None, tags: list[str] | None = None,
                    confidence: str | None = None) -> dict[str, object]:
        if not isinstance(record_id, str) or not ID_RE.fullmatch(record_id):
            raise _fail("INVALID_REQUEST", "invalid memory id")
        with self._locked():
            rows = self._read_locked()
            old = next((row for row in rows if row["id"] == record_id), None)
            if old is None or old["status"] in {"retired", "superseded"}:
                raise _fail("INVALID_REQUEST", "memory record is unavailable")
            new_summary = old["summary"] if summary is None else summary
            _safe_text(new_summary, "summary", MAX_SUMMARY)
            new_evidence = old["evidence"] if evidence is None else self._prepare_evidence(evidence)
            new_tags = old.get("tags", []) if tags is None else tags
            if not isinstance(new_tags, list) or len(new_tags) > MAX_TAGS:
                raise _fail("INVALID_REQUEST", "invalid tags")
            new_tags = [_safe_text(tag, "tag", MAX_TAG) for tag in new_tags]
            new_confidence = old["confidence"] if confidence is None else confidence
            if new_confidence not in CONFIDENCES:
                raise _fail("INVALID_REQUEST", "invalid confidence")
            logical = {"kind": old["kind"], "summary": new_summary, "scope": old["scope"],
                       "confidence": new_confidence, "evidence": new_evidence, "tags": new_tags,
                       "related_ids": old.get("related_ids", [])}
            new_id = _record_id({"transition": "retire" if retired else "supersede", "source": record_id,
                                 "logical": logical})
            record: dict[str, object] = {"schema_version": 1, "id": new_id, **logical,
                                         "status": "retired" if retired else "active",
                                         "created_at": self._now(), "updated_at": self._now(),
                                         "supersedes": record_id}
            if not new_tags:
                record.pop("tags")
            if not record["related_ids"]:
                record.pop("related_ids")
            return self._append_locked(record, rows)

    def validate(self) -> dict[str, object]:
        return {"records": len(self.records()), "valid": True}

    revalidate = validate
