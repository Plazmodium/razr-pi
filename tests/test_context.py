from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/razr-pi"))
from adapter.context import ContextEngine, ContextRequest  # noqa: E402
from adapter.memory import MemoryStore  # noqa: E402
from adapter.protocol import ProtocolError, raw_file_digest  # noqa: E402
from adapter.core_subprocess import _git_head, _source_digest  # noqa: E402


class ContextEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
               "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
               "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, env=env)
        (self.root / "src").mkdir(); (self.root / "tests").mkdir()
        (self.root / "src/app.py").write_text("def context(): return True\n")
        (self.root / "tests/test_app.py").write_text("def test_context(): assert True\n")
        (self.root / "razr.yaml").write_text(json.dumps({"schema_version": 1, "work_root": ".razr/work"}) + "\n")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True, env=env)
        subprocess.run(["git", "-C", str(self.root), "commit", "-q", "-m", "fixture"], check=True, env=env)
        self.request = {"schema_version": 1, "request_id": "ctx-test", "objective": "context app tests",
                        "repository_root": ".", "include": {k: k not in {"workflow", "memory"} for k in ("workflow", "source", "tests", "config", "git", "memory")},
                        "limits": {"max_files": 4, "max_bytes": 1000, "max_memory_records": 2}}

    def tearDown(self): self.tmp.cleanup()

    def test_tracer_reads_real_files_git_and_returns_digest(self):
        result = ContextEngine(self.root).context(self.request)
        self.assertTrue(result["ok"])
        packet = result["result"]
        self.assertTrue(packet["packet_digest"].startswith("sha256:"))
        self.assertEqual({item["kind"] for item in packet["items"]}, {"file", "git_history"})
        self.assertTrue(any(item["ref"] == "src/app.py" for item in packet["items"]))
        self.assertTrue(any(item["ref"] == "tests/test_app.py" for item in packet["items"]))

    def test_bounds_omit_instead_of_dump(self):
        request = dict(self.request, limits={"max_files": 1, "max_bytes": 5, "max_memory_records": 1})
        packet = ContextEngine(self.root).build(request)
        self.assertLessEqual(len(packet["items"]), 2)  # one bounded file plus git metadata
        self.assertTrue(packet["omitted"])
        self.assertTrue(all("content_utf8" not in item or item["bytes"] <= 5 for item in packet["items"]))

    def test_memory_is_labelled_and_stale_is_exposed(self):
        store = MemoryStore(self.root)
        path = self.root / "src/app.py"
        record = store.remember(kind="learning", summary="old app note", repository="sha256:" + "a" * 64,
                                paths=["src/app.py"], evidence=[{"type":"path", "ref":"src/app.py", "digest_domain":"raw_file", "digest":raw_file_digest(path.read_bytes())}])
        path.write_text("changed\n")
        request = dict(self.request); request["include"] = dict(self.request["include"], memory=True)
        packet = ContextEngine(self.root, memory=store).build(request)
        self.assertIn(record["id"], packet["stale_memory_ids"])
        self.assertTrue(all(item["authority"] in {"repository", "memory"} for item in packet["items"]))

    def test_closed_request_and_safe_paths(self):
        with self.assertRaises(ProtocolError): ContextRequest.from_dict(dict(self.request, extra=True))
        with self.assertRaises(ProtocolError): ContextRequest.from_dict(dict(self.request, schema_version=True))
        with self.assertRaises(ProtocolError): ContextRequest.from_dict(dict(self.request, paths=["../secret"]))
        with self.assertRaises(ProtocolError): ContextRequest.from_dict(dict(self.request, include=dict(self.request["include"], source=1)))
        with self.assertRaises(ProtocolError): ContextRequest.from_dict(dict(self.request, limits={"max_files": 201, "max_bytes": 1, "max_memory_records": 1}))

    def test_observed_identity_is_verified_not_advisory(self):
        commit = _git_head(self.root)
        source = _source_digest(self.root, commit)
        request = dict(self.request, observed={"commit": commit, "source_digest": source})
        self.assertTrue(ContextEngine(self.root).context(request)["ok"])
        stale = dict(self.request, observed={"commit": "0" * 40, "source_digest": source})
        with self.assertRaises(ProtocolError) as caught:
            ContextEngine(self.root).context(stale)
        self.assertEqual(caught.exception.code, "SOURCE_CHANGED")

    def test_workflow_seam_cannot_return_untyped_authority(self):
        request = dict(self.request, include=dict(self.request["include"], workflow=True), work_id="work")
        with self.assertRaises(ProtocolError) as caught:
            ContextEngine(self.root, workflow=lambda _: {"status": "complete"}).context(request)
        self.assertEqual(caught.exception.code, "WORKFLOW_UNAVAILABLE")


if __name__ == "__main__": unittest.main()
