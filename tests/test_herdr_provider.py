from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/razr-pi"))
from adapter.herdr_provider import AttemptLedger, HerdrProvider, ProtocolError
from adapter.protocol import canonical_json, raw_file_digest


class HerdrProviderTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"HERDR_ENV": "1", "HERDR_WORKSPACE_ID": "w", "HERDR_TAB_ID": "t", "HERDR_PANE_ID": "p"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_standalone_is_clean_and_never_launches(self):
        with patch.dict(os.environ, {"HERDR_ENV": "0"}, clear=False):
            with self.assertRaises(ProtocolError) as caught:
                HerdrProvider().probe()
        self.assertEqual(caught.exception.code, "FANOUT_UNAVAILABLE")

    def test_probe_uses_capabilities_not_version_and_argv_only(self):
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, b"openai-codex/gpt-5.6-luna\n", b"")
        result = HerdrProvider(runner=run).probe(["runtime_identity"])
        self.assertEqual(result["result"]["runtime_identity"], "openai-codex/gpt-5.6-luna:medium")
        self.assertEqual([call[0][:2] for call in calls], [["herdr", "--help"], ["herdr", "agent"], ["pi", "--list-models"]])
        self.assertTrue(all(call[1]["shell"] is False and call[1]["stdin"] is subprocess.DEVNULL for call in calls))

    def test_malformed_and_timeout_are_mechanical_errors(self):
        def malformed(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, b"not-json", b"")
        provider = HerdrProvider(runner=malformed)
        with self.assertRaises(ProtocolError) as caught:
            provider.worktree_create(cwd=str(ROOT), branch="b", base="main", label="x")
        self.assertEqual(caught.exception.code, "ARTIFACT_UNAVAILABLE")

        def timeout(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 1)
        with self.assertRaises(ProtocolError) as caught:
            HerdrProvider(runner=timeout).probe()
        self.assertEqual(caught.exception.code, "TIMEOUT")

    def test_ledger_replays_closed_identity_bound_events_and_rejects_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".razr" / "attempts.jsonl"
            ledger = AttemptLedger(path)
            digest = "sha256:" + "a" * 64
            candidate = {"commit": "0" * 40, "source_digest": digest}
            ref = {"ref": ".razr/evidence/candidate.json", "digest_domain": "canonical_json", "digest": digest}
            packet = {"ref": ".razr/evidence/attempt.json", "digest_domain": "canonical_json", "digest": digest}
            started = {"schema_version": 1, "event": "STARTED", "attempt_id": "att-1", "work_id": "work", "node_id": "node",
                       "observed_generation": 2, "observed_ordinal": 1, "worker_id": "worker-1", "branch": "feature/x",
                       "worktree_ref": "worktrees/worker", "allowance_id": "allow-1", "provider_id": "herdr",
                       "started_at": "2026-01-01T00:00:00Z", "started_at_source": "adapter_observed", "candidate": candidate,
                       "candidate_evidence": ref, "request_packet": packet, "output_root_id": "out-1", "output_root_digest": digest,
                       "session_root_id": "sess-1", "session_root_digest": digest, "request_digest": digest,
                       "herdr": {"workspace_id": "w", "tab_id": "t", "pane_id": "p"}, "agent_id": "agent-1", "pi_session_id": "pi-1",
                       "core_identity": digest, "core_state_digest": digest, "prev_digest": "sha256:" + "0" * 64}
            started["record_digest"] = raw_file_digest(canonical_json(started)); ledger.append(started)
            settled = {key: started[key] for key in ("schema_version", "attempt_id", "work_id", "node_id", "observed_generation", "observed_ordinal", "provider_id", "worktree_ref", "branch", "candidate", "candidate_evidence", "request_packet", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest", "request_digest", "herdr", "agent_id", "pi_session_id", "core_identity", "core_state_digest")}
            settled.update({"event": "SETTLED", "settled_at": "2026-01-01T00:01:00Z", "outcome": "UNKNOWN", "evidence_refs": [{**ref, "kind": "result"}], "prev_digest": started["record_digest"], "cleanup": "uncertain"})
            settled["record_digest"] = raw_file_digest(canonical_json(settled)); ledger.append(settled)
            with self.assertRaises(ProtocolError) as caught: ledger.append(started)
            self.assertEqual(caught.exception.code, "DUPLICATE_ATTEMPT")
            self.assertEqual(len(path.read_text().splitlines()), 2)

    def test_request_rejects_runtime_fallback_and_root_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = (Path(tmp) / "work").resolve(); root.mkdir()
            out = (Path(tmp) / "out").resolve(); out.mkdir()
            session = (Path(tmp) / "session").resolve(); session.mkdir()
            base = {"schema_version": 1, "request_id": "exec-1", "attempt_id": "att-1", "role": "technical",
                    "host": {"mode": "herdr", "workspace_id": "w", "tab_id": "t", "pane_id": "p"},
                    "work": {"work_id": "work", "node_id": "node", "generation": 1, "ordinal": 1},
                    "worker_id": "worker-1", "branch": "feature/x", "candidate": {"repository": "owner/repo", "branch": "feature/x", "commit": "0" * 40, "source_digest": "sha256:" + "f" * 64}, "worktree": str(root), "cwd": str(root),
                    "output_root": str(out), "output_root_id": "out-1", "output_root_digest": "sha256:" + "a" * 64,
                    "session_root": str(session), "session_root_id": "sess-1", "session_root_digest": "sha256:" + "b" * 64,
                    "fresh_context": True, "neutral_input_digest": "sha256:" + "c" * 64,
                    "environment_policy_digest": "sha256:" + "d" * 64, "tool_policy_digest": "sha256:" + "e" * 64,
                    "required_capabilities": ["fresh_context"], "runtime": {"provider": "openai-codex", "model": "other", "thinking": "medium"},
                    "limits": {"deadline_ms": 1000, "max_output_bytes": 100}, "allowance_id": "allow-1"}
            with self.assertRaises(ProtocolError) as caught:
                HerdrProvider().launch(base)
            self.assertEqual(caught.exception.code, "UNSUPPORTED_CAPABILITY")


if __name__ == "__main__":
    unittest.main()
