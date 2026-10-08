from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import resource
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/razr-pi"))
from adapter.memory import GENESIS, MemoryStore  # noqa: E402
from adapter.protocol import ProtocolError, raw_file_digest  # noqa: E402


def _append_worker(root: str, index: int, repository: str) -> None:
    MemoryStore(root).remember(kind="learning", summary=f"worker {index}", repository=repository)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.repo = "sha256:" + "a" * 64
        self.store = MemoryStore(self.root, now=lambda: "2026-01-01T00:00:00Z")

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_recall_scope_bound_and_round_trip(self):
        (self.root / "src").mkdir()
        path = self.root / "src/a.py"
        path.write_text("print('ok')\n")
        record = self.store.remember(kind="learning", summary="use the tracer", repository=self.repo,
            paths=["src/a.py"], evidence=[{"type":"path", "ref":"src/a.py", "digest_domain":"raw_file",
            "digest":raw_file_digest(path.read_bytes())}], tags=["trace"])
        self.assertTrue(record["id"].startswith("mem-"))
        found = self.store.recall("tracer", paths=["src/a.py"], limit=1)
        self.assertEqual(found["records"][0]["id"], record["id"])
        self.assertEqual(found["records"][0]["evidence"], record["evidence"])
        self.assertEqual(len(self.store.recall(limit=1)["records"]), 1)
        self.assertEqual(self.store.recall(limit=1)["omitted_count"], 0)

    def test_stale_retire_and_supersede(self):
        path = self.root / "note.txt"
        path.write_text("one")
        record = self.store.remember(kind="observation", summary="old", repository=self.repo,
            evidence=[{"type":"path", "ref":"note.txt", "digest_domain":"raw_file",
                       "digest":raw_file_digest(b"one")}])
        path.write_text("two")
        result = self.store.recall()
        self.assertEqual(result["stale_ids"], [record["id"]])
        self.assertEqual(result["records"][0]["status"], "stale")
        replacement = self.store.supersede(record["id"], summary="new")
        self.assertEqual(self.store.recall()["records"][0]["id"], replacement["id"])
        retired = self.store.retire(replacement["id"])
        self.assertEqual(retired["status"], "retired")
        self.assertEqual(self.store.recall()["records"], [])
        with self.assertRaises(TypeError):
            self.store.supersede(record["id"], summary="bad", scope={})  # type: ignore[call-arg]

    def test_duplicate_remember_is_idempotent_or_conflict(self):
        first = self.store.remember(kind="learning", summary="same", repository=self.repo, tags=["x"])
        second = self.store.remember(kind="learning", summary="same", repository=self.repo, tags=["x"])
        self.assertEqual(first, second)
        with self.assertRaises(ProtocolError) as caught:
            self.store.remember(kind="learning", summary="different", repository=self.repo, record_id=first["id"])
        self.assertEqual(caught.exception.code, "INVALID_REQUEST")

    def test_secrets_paths_and_corruption_refused(self):
        with self.assertRaises(ProtocolError):
            self.store.remember(kind="learning", summary="token=abc", repository=self.repo)
        secret = self.root / "secret.txt"
        secret.write_text("Authorization: Bearer abc")
        with self.assertRaises(ProtocolError):
            self.store.remember(kind="learning", summary="safe", repository=self.repo,
                evidence=[{"type":"path", "ref":"secret.txt", "digest_domain":"raw_file",
                           "digest":raw_file_digest(secret.read_bytes())}])
        with self.assertRaises(ProtocolError):
            self.store.remember(kind="learning", summary="safe", repository=self.repo,
                                evidence=[{"type":"path", "ref":"../secret", "digest_domain":"raw_file",
                                           "digest":raw_file_digest(b"")}])
        self.store.path.parent.mkdir()
        self.store.path.write_text('{"broken":true}')
        with self.assertRaises(ProtocolError) as caught:
            self.store.inspect()
        self.assertEqual(caught.exception.code, "LEDGER_CORRUPT")

    def test_canonical_parser_and_symlink_components_refuse(self):
        self.store.path.parent.mkdir()
        with self.store.path.open("wb") as stream:
            stream.write(b'{"schema_version":1,"schema_version":1}\n')
        with self.assertRaises(ProtocolError):
            self.store.inspect()
        self.store.path.unlink()
        (self.root / "real").mkdir()
        (self.root / "link").symlink_to(self.root / "real", target_is_directory=True)
        evidence = {"type":"path", "ref":"link/file", "digest_domain":"raw_file", "digest":raw_file_digest(b"x")}
        with self.assertRaises(ProtocolError) as caught:
            self.store.remember(kind="learning", summary="safe", repository=self.repo, evidence=[evidence])
        self.assertEqual(caught.exception.code, "PATH_ESCAPE")
        (self.root / "real" / "file").write_text("ok")
        evidence = {"type":"path", "ref":"real/file", "digest_domain":"raw_file", "digest":raw_file_digest(b"ok")}
        remembered = self.store.remember(kind="learning", summary="symlink later", repository=self.repo, evidence=[evidence])
        (self.root / "real").rename(self.root / "moved")
        (self.root / "real").symlink_to(self.root / "moved", target_is_directory=True)
        self.assertEqual(self.store.recall()["stale_ids"], [remembered["id"]])

    def test_append_retries_eintr_and_rejects_zero_progress(self):
        original = __import__("os").write
        calls = {"count": 0}
        def interrupted_then_write(fd, payload):
            calls["count"] += 1
            if calls["count"] == 1:
                raise InterruptedError()
            return original(fd, payload[:4])
        with patch("adapter.memory.os.write", side_effect=interrupted_then_write):
            record = self.store.remember(kind="learning", summary="short writes", repository=self.repo)
        self.assertEqual(self.store.inspect()[0]["id"], record["id"])
        self.store.path.unlink()
        with patch("adapter.memory.os.write", return_value=0):
            with self.assertRaises(ProtocolError) as caught:
                self.store.remember(kind="learning", summary="zero", repository=self.repo)
        self.assertEqual(caught.exception.code, "LEDGER_CORRUPT")
        self.store.path.unlink()
        with patch("adapter.memory.os.fsync", side_effect=OSError("fsync")):
            with self.assertRaises(ProtocolError) as caught:
                self.store.remember(kind="learning", summary="fsync failure", repository=self.repo)
        self.assertEqual(caught.exception.code, "LOCK_UNAVAILABLE")
        self.assertEqual(len(self.store.inspect()), 1)

    def test_chain_interruption_and_concurrent_append(self):
        self.store.remember(kind="learning", summary="first", repository=self.repo)
        raw = self.store.path.read_bytes()
        self.assertIn(b'"prev_digest":"' + GENESIS.encode(), raw)
        with self.store.path.open("ab") as stream:
            stream.write(b'{"partial"')
        with self.assertRaises(ProtocolError):
            self.store.validate()

        self.store.path.unlink()
        root = str(self.root)
        workers = [multiprocessing.Process(target=_append_worker, args=(root, i, self.repo)) for i in range(4)]
        for worker in workers: worker.start()
        for worker in workers: worker.join(10)
        self.assertTrue(all(worker.exitcode == 0 for worker in workers))
        self.assertEqual(len(self.store.inspect()), 4)

    def test_storage_links_and_component_identity_fail_closed(self):
        outside = Path(tempfile.mkdtemp())
        self.root.joinpath(".razr").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ProtocolError):
            self.store.remember(kind="learning", summary="linked storage", repository=self.repo)
        self.root.joinpath(".razr").unlink()
        (self.root / ".razr").mkdir()
        (self.root / "target.jsonl").write_text("")
        (self.root / ".razr/memory.jsonl").symlink_to(self.root / "target.jsonl")
        with self.assertRaises(ProtocolError):
            self.store.inspect()
        (self.root / ".razr/memory.jsonl").unlink()
        (self.root / ".razr-memory.lock").unlink()
        (self.root / ".razr-memory.lock").symlink_to(self.root / "target.jsonl")
        with self.assertRaises(ProtocolError):
            self.store.inspect()
        (self.root / ".razr-memory.lock").unlink()
        evidence = self.root / "identity.txt"
        evidence.write_text("one")
        digest = raw_file_digest(evidence.read_bytes())
        original_stat = __import__("os").stat
        calls = {"count": 0}
        def pathname_swap(path, *args, **kwargs):
            calls["count"] += 1
            info = original_stat(path, *args, **kwargs)
            if calls["count"] == 2:
                values = list(info); values[1] += 1
                return type(info)(values)
            return info
        with patch("adapter.memory.os.stat", side_effect=pathname_swap):
            with self.assertRaises(ProtocolError) as caught:
                self.store.remember(kind="learning", summary="identity", repository=self.repo,
                    evidence=[{"type":"path", "ref":"identity.txt", "digest_domain":"raw_file", "digest":digest}])
        self.assertEqual(caught.exception.code, "PATH_ESCAPE")
        with self.assertRaises(ProtocolError):
            self.store.remember(kind="learning", summary="metadata", repository=self.repo,
                evidence=[{"type":"external", "ref":"https://outside", "digest_domain":"raw_file",
                           "digest":"sha256:" + "a" * 64}])

    def test_repeated_transitions_are_idempotent_or_conflicts(self):
        record = self.store.remember(kind="learning", summary="transition", repository=self.repo)
        retired = self.store.retire(record["id"])
        self.assertEqual(self.store.retire(record["id"]), retired)
        with self.assertRaises(ProtocolError):
            self.store.supersede(record["id"], summary="different")
        replacement_source = self.store.remember(kind="learning", summary="replace", repository=self.repo)
        replacement = self.store.supersede(replacement_source["id"], summary="new")
        self.assertEqual(self.store.supersede(replacement_source["id"], summary="new"), replacement)
        with self.assertRaises(ProtocolError):
            self.store.supersede(replacement_source["id"], summary="other")

    def test_oversized_collections_are_refused(self):
        with self.assertRaises(ProtocolError):
            self.store.remember(kind="learning", summary="evidence", repository=self.repo,
                evidence=[{"type":"external", "ref":"x" * 512, "digest_domain":"raw_file",
                           "digest":"sha256:" + "a" * 64}] * 32)
        with self.assertRaises(ProtocolError):
            self.store.remember(kind="learning", summary="tags", repository=self.repo,
                                tags=["t" * 80] * 32)
        with self.assertRaises(ProtocolError):
            self.store.remember(kind="learning", summary="relations", repository=self.repo,
                                related_ids=["r" * 128] * 32)

    def test_lifecycle_hardlinks_directory_replacement_and_fd_bound(self):
        store = MemoryStore(self.root)
        self.assertIs(store.close(), None)
        store.close()
        with self.assertRaises(ProtocolError):
            store.inspect()
        with MemoryStore(self.root) as managed:
            managed.remember(kind="learning", summary="managed", repository=self.repo)
        with self.assertRaises(ProtocolError):
            managed.inspect()

        evidence = self.root / "hard.txt"
        evidence.write_text("hard")
        hardlink = self.root / "hard-copy.txt"
        hardlink.hardlink_to(evidence)
        with MemoryStore(self.root) as managed:
            with self.assertRaises(ProtocolError):
                managed.remember(kind="learning", summary="hard evidence", repository=self.repo,
                    evidence=[{"type":"path", "ref":"hard.txt", "digest_domain":"raw_file",
                               "digest":raw_file_digest(b"hard")}])

        with MemoryStore(self.root) as managed:
            managed.remember(kind="learning", summary="storage", repository=self.repo)
        external_ledger = self.root / "ledger-copy"
        external_ledger.hardlink_to(self.root / ".razr/memory.jsonl")
        with MemoryStore(self.root) as linked_store:
            with self.assertRaises(ProtocolError):
                linked_store.inspect()
        external_ledger.unlink()
        external_lock = self.root / "lock-copy"
        external_lock.hardlink_to(self.root / ".razr-memory.lock")
        with MemoryStore(self.root) as linked_store:
            with self.assertRaises(ProtocolError):
                linked_store.inspect()
        external_lock.unlink()

        pinned = MemoryStore(self.root)
        pinned.validate()
        old_storage = self.root / "razr-old"
        (self.root / ".razr").rename(old_storage)
        (self.root / ".razr").mkdir()
        with self.assertRaises(ProtocolError):
            pinned.inspect()
        pinned.close()

        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        lowered = min(soft, 128)
        if lowered >= 32:
            try:
                resource.setrlimit(resource.RLIMIT_NOFILE, (lowered, hard))
                for _ in range(80):
                    with MemoryStore(self.root) as managed:
                        managed.validate()
            finally:
                resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))

    def test_portable_paths_and_validate(self):
        record = self.store.remember(kind="preference", summary="portable", repository=self.repo,
                                     paths=["a/b.txt"])
        self.assertNotIn(str(self.root), json.dumps(record))
        self.assertEqual(self.store.validate(), {"records": 1, "valid": True})
        with self.assertRaises(ProtocolError):
            self.store.recall(limit=101)


if __name__ == "__main__":
    unittest.main()
