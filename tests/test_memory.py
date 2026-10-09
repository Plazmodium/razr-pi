from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import resource
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/razr-pi"))
from adapter.core_subprocess import _git_head, _source_digest  # noqa: E402
from adapter.memory import GENESIS, MAX_LEDGER_BYTES, MemoryStore  # noqa: E402
from adapter.protocol import ProtocolError, raw_file_digest  # noqa: E402


def _append_worker(root: str, index: int, repository: str) -> None:
    MemoryStore(root, repository=repository).remember(kind="learning", summary=f"worker {index}", repository=repository)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        env = {"PATH": __import__("os").environ.get("PATH", ""), "HOME": str(self.root),
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": __import__("os").devnull,
               "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
               "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, env=env)
        (self.root / "tracked.txt").write_text("initial\n")
        (self.root / "razr.yaml").write_text('{"schema_version":1,"work_root":".razr/work"}\n')
        subprocess.run(["git", "-C", str(self.root), "add", "tracked.txt", "razr.yaml"], check=True, env=env)
        subprocess.run(["git", "-C", str(self.root), "commit", "-q", "-m", "initial"], check=True, env=env)
        self.git_env = env
        self.repo = _source_digest(self.root, _git_head(self.root))
        self.store = MemoryStore(self.root, repository=self.repo, now=lambda: "2026-01-01T00:00:00Z")

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
        (self.root / ".razr/memory.jsonl.lock").unlink()
        (self.root / ".razr/memory.jsonl.lock").symlink_to(self.root / "target.jsonl")
        with self.assertRaises(ProtocolError) as caught:
            self.store.validate()
        self.assertEqual(caught.exception.code, "LOCK_UNAVAILABLE")
        (self.root / ".razr/memory.jsonl.lock").unlink()
        evidence = self.root / "identity.txt"
        evidence.write_text("one")
        digest = raw_file_digest(evidence.read_bytes())
        original_stat = __import__("os").stat
        calls = {"leaf": 0}
        def pathname_swap(path, *args, **kwargs):
            info = original_stat(path, *args, **kwargs)
            if path == "identity.txt": calls["leaf"] += 1
            if path == "identity.txt" and calls["leaf"] == 1:
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
        store = MemoryStore(self.root, repository=self.repo)
        self.assertIs(store.close(), None)
        store.close()
        with self.assertRaises(ProtocolError):
            store.inspect()
        with MemoryStore(self.root, repository=self.repo) as managed:
            managed.remember(kind="learning", summary="managed", repository=self.repo)
        with self.assertRaises(ProtocolError):
            managed.inspect()

        evidence = self.root / "hard.txt"
        evidence.write_text("hard")
        hardlink = self.root / "hard-copy.txt"
        hardlink.hardlink_to(evidence)
        with MemoryStore(self.root, repository=self.repo) as managed:
            with self.assertRaises(ProtocolError):
                managed.remember(kind="learning", summary="hard evidence", repository=self.repo,
                    evidence=[{"type":"path", "ref":"hard.txt", "digest_domain":"raw_file",
                               "digest":raw_file_digest(b"hard")}])

        with MemoryStore(self.root, repository=self.repo) as managed:
            managed.remember(kind="learning", summary="storage", repository=self.repo)
        external_ledger = self.root / "ledger-copy"
        external_ledger.hardlink_to(self.root / ".razr/memory.jsonl")
        with MemoryStore(self.root, repository=self.repo) as linked_store:
            with self.assertRaises(ProtocolError):
                linked_store.inspect()
        external_ledger.unlink()
        former_lock = self.root / ".razr/memory.jsonl.lock"
        former_lock.unlink()
        former_lock.symlink_to(self.root / "ledger-copy")
        with MemoryStore(self.root, repository=self.repo) as linked_store:
            with self.assertRaises(ProtocolError) as caught:
                linked_store.validate()
        self.assertEqual(caught.exception.code, "LOCK_UNAVAILABLE")
        former_lock.unlink()

        first = MemoryStore(self.root, repository=self.repo)
        second = MemoryStore(self.root, repository=self.repo)
        first.remember(kind="learning", summary="first instance", repository=self.repo)
        second.remember(kind="learning", summary="second instance", repository=self.repo)
        first.close(); second.close()
        with MemoryStore(self.root, repository=self.repo) as flock_store:
            with patch("adapter.memory.fcntl.flock", side_effect=OSError("unsupported")):
                with self.assertRaises(ProtocolError) as caught:
                    flock_store.validate()
            self.assertEqual(caught.exception.code, "LOCK_UNAVAILABLE")

        pinned = MemoryStore(self.root, repository=self.repo)
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
                    with MemoryStore(self.root, repository=self.repo) as managed:
                        managed.validate()
            finally:
                resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))

    def test_explicit_repository_is_expected_not_authority_after_commit(self):
        record = self.store.remember(kind="learning", summary="old source", repository=self.repo)
        (self.root / "tracked.txt").write_text("changed\n")
        subprocess.run(["git", "-C", str(self.root), "add", "tracked.txt"], check=True, env=self.git_env)
        subprocess.run(["git", "-C", str(self.root), "commit", "-q", "-m", "changed"], check=True, env=self.git_env)
        current = _source_digest(self.root, _git_head(self.root))
        self.assertNotEqual(current, self.repo)
        operations = (
            self.store.inspect,
            self.store.recall,
            lambda: self.store.remember(kind="learning", summary="wrong source", repository=self.repo),
            lambda: self.store.retire(record["id"]),
            lambda: self.store.supersede(record["id"], summary="wrong source"),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(ProtocolError) as caught:
                operation()
            self.assertEqual(caught.exception.code, "SOURCE_CHANGED")
        with self.assertRaises(ProtocolError) as caught:
            MemoryStore(self.root, repository=self.repo)
        self.assertEqual(caught.exception.code, "SOURCE_CHANGED")
        with MemoryStore(self.root, repository=current) as current_store:
            recalled = current_store.recall(repository=current)
        self.assertEqual(recalled["records"], [])
        self.assertEqual(recalled["stale_ids"], [record["id"]])

    def test_repository_scope_bounds_permissions_and_secret_heuristics(self):
        record = self.store.remember(kind="learning", summary="repository scoped", repository=self.repo)
        recalled = self.store.recall(repository=self.repo)
        self.assertEqual([row["id"] for row in recalled["records"]], [record["id"]])
        self.assertEqual(recalled["stale_ids"], [])

        for secret in ("AKIAABCDEFGHIJKLMNOP", "https://user:password@example.invalid/path",
                       "eyJabcdefgh.abcdefgh.abcdefgh", "secret_b64=YWJjZGVmZ2hpamtsbW5vcA==",
                       "-----BEGIN " + "OPENSSH PRIVATE" + " KEY-----\nencoded"):
            with self.subTest(secret=secret), self.assertRaises(ProtocolError):
                MemoryStore(self.root, repository=self.repo).remember(kind="learning", summary=secret, repository=self.repo)
        with MemoryStore(self.root, repository=self.repo) as store:
            store.remember(kind="learning", summary="private modes", repository=self.repo)
        self.assertEqual(self.store.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.store.path.parent / "memory.jsonl.lock").stat().st_mode & 0o777, 0o600)
        ignores = (ROOT / ".gitignore").read_text().splitlines()
        self.assertIn("/.razr/memory.jsonl", ignores)
        self.assertIn("/.razr/memory.jsonl.lock", ignores)
        implementation = (ROOT / "packages/razr-pi/adapter/memory.py").read_text()
        for forbidden in ("keyring", "keychain", "boto3", "requests", "urllib.request", "socket.", "subprocess.", "os.environ"):
            self.assertNotIn(forbidden, implementation)

        oversized = self.root / ".razr/memory.jsonl"
        oversized.write_bytes(b"x" * (MAX_LEDGER_BYTES + 1))
        with MemoryStore(self.root, repository=self.repo) as store:
            with self.assertRaises(ProtocolError) as caught:
                store.inspect()
        self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")

    def test_ledger_record_line_identity_and_directory_durability_bounds(self):
        self.store.remember(kind="learning", summary="bounded ledger", repository=self.repo)
        with patch("adapter.memory.MAX_LEDGER_RECORDS", 0):
            with self.assertRaises(ProtocolError) as caught: self.store.inspect()
        self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")
        with patch("adapter.memory.MAX_LEDGER_LINE_BYTES", 8):
            with self.assertRaises(ProtocolError) as caught: self.store.inspect()
        self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")

        current = [self.repo]
        evidence = self.root / "race.txt"; evidence.write_text("stable")
        digest = raw_file_digest(evidence.read_bytes()); original_read = __import__("os").read
        changed = {"done": False}
        def change_repository(fd, count):
            data = original_read(fd, count)
            if data and not changed["done"]:
                changed["done"] = True; current[0] = "sha256:" + "b" * 64
            return data
        with MemoryStore(self.root, repository=self.repo, repository_identity=lambda: current[0]) as store, \
             patch("adapter.memory.os.read", side_effect=change_repository):
            with self.assertRaises(ProtocolError) as caught:
                store.remember(kind="learning", summary="identity race", repository=self.repo,
                    evidence=[{"type": "path", "ref": "race.txt", "digest_domain": "raw_file", "digest": digest}])
        self.assertEqual(caught.exception.code, "SOURCE_CHANGED")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); actual_fsync = __import__("os").fsync
            def reject_directory_fsync(fd):
                if stat.S_ISDIR(__import__("os").fstat(fd).st_mode): raise OSError("directory fsync")
                return actual_fsync(fd)
            with patch("adapter.memory._live_repository_identity", return_value=self.repo), \
                 MemoryStore(root, repository=self.repo) as store, \
                 patch("adapter.memory.os.fsync", side_effect=reject_directory_fsync):
                with self.assertRaises(ProtocolError) as caught:
                    store.remember(kind="learning", summary="durability", repository=self.repo)
            self.assertEqual(caught.exception.code, "LEDGER_CORRUPT")
            self.assertFalse((root / ".razr/memory.jsonl").exists())

    def test_post_flock_lock_path_replacement_fails_closed_without_race_proof(self):
        actual_flock = __import__("fcntl").flock
        replaced = {"done": False}
        def replace_after_flock(fd, operation):
            result = actual_flock(fd, operation)
            if operation == __import__("fcntl").LOCK_EX and not replaced["done"]:
                replaced["done"] = True
                old = self.store.lock_path.with_name("memory.lock.old")
                self.store.lock_path.rename(old)
                self.store.lock_path.write_bytes(b"")
            return result
        with patch("adapter.memory.fcntl.flock", side_effect=replace_after_flock):
            with self.assertRaises(ProtocolError) as caught:
                self.store.remember(kind="learning", summary="pre-yield replacement", repository=self.repo)
        self.assertEqual(caught.exception.code, "PATH_ESCAPE")
        self.assertFalse(self.store.path.exists())
        self.store.lock_path.unlink(); self.store.lock_path.with_name("memory.lock.old").unlink()

        original_append = self.store._append_locked
        def append_then_replace(storage_fd, record, rows):
            result = original_append(storage_fd, record, rows)
            old = self.store.lock_path.with_name("memory.lock.old")
            self.store.lock_path.rename(old); self.store.lock_path.write_bytes(b"")
            return result
        with patch.object(self.store, "_append_locked", side_effect=append_then_replace):
            with self.assertRaises(ProtocolError) as caught:
                self.store.remember(kind="learning", summary="post-write replacement", repository=self.repo)
        self.assertEqual(caught.exception.code, "PATH_ESCAPE")
        self.store.lock_path.with_name("memory.lock.old").unlink()
        with MemoryStore(self.root, repository=self.repo) as reopened:
            records = reopened.inspect()
        self.assertEqual([row["summary"] for row in records], ["post-write replacement"])

    def test_portable_paths_and_validate(self):
        record = self.store.remember(kind="preference", summary="portable", repository=self.repo,
                                     paths=["a/b.txt"])
        self.assertNotIn(str(self.root), json.dumps(record))
        self.assertEqual(self.store.validate(), {"records": 1, "valid": True})
        with self.assertRaises(ProtocolError):
            self.store.recall(limit=101)


if __name__ == "__main__":
    unittest.main()
