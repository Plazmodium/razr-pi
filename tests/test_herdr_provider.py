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
                HerdrProvider().probe({"schema_version": 1, "request_id": "probe-1", "required_capabilities": [], "runtime": {"provider": "openai-codex", "model": "openai-codex/gpt-5.6-luna", "thinking": "medium"}})
        self.assertEqual(caught.exception.code, "FANOUT_UNAVAILABLE")

    def test_probe_uses_capabilities_not_version_and_argv_only(self):
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            help_text = b"--no-session --session --continue --resume --cwd --path --pane --timeout --lines pane"
            if argv[:2] == ["pi", "--list-models"]: return subprocess.CompletedProcess(argv, 0, b"openai-codex/gpt-5.6-luna\n", b"")
            return subprocess.CompletedProcess(argv, 0, help_text, b"")
        result = HerdrProvider(runner=run).probe({"schema_version": 1, "request_id": "probe-1", "required_capabilities": list(("fresh_context", "working_directory", "bounded_time", "bounded_output", "runtime_identity")), "runtime": {"provider": "openai-codex", "model": "openai-codex/gpt-5.6-luna", "thinking": "medium"}})
        self.assertEqual(result["request_id"], "probe-1")
        self.assertEqual(result["result"]["runtime_identity"], "openai-codex/gpt-5.6-luna:medium")
        self.assertEqual([call[0][:2] for call in calls], [["herdr", "--help"], ["herdr", "agent"], ["herdr", "worktree"], ["herdr", "pane"], ["pi", "--help"], ["pi", "--list-models"]])
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
            HerdrProvider(runner=timeout).probe({"schema_version": 1, "request_id": "probe-1", "required_capabilities": [], "runtime": {"provider": "openai-codex", "model": "openai-codex/gpt-5.6-luna", "thinking": "medium"}})
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
                       "herdr": {"workspace_id": "w", "tab_id": "t", "pane_id": "p"}, "prev_digest": "sha256:" + "0" * 64}
            started["record_digest"] = raw_file_digest(canonical_json(started)); ledger.append(started)
            settled = {key: started[key] for key in ("schema_version", "attempt_id", "work_id", "node_id", "observed_generation", "observed_ordinal", "provider_id", "worktree_ref", "branch", "candidate", "candidate_evidence", "request_packet", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest", "request_digest", "herdr")}
            settled.update({"event": "SETTLED", "settled_at": "2026-01-01T00:01:00Z", "outcome": "UNKNOWN", "evidence_refs": [{**ref, "kind": "result"}], "prev_digest": started["record_digest"], "cleanup": "uncertain"})
            settled["record_digest"] = raw_file_digest(canonical_json(settled)); ledger.append(settled)
            with self.assertRaises(ProtocolError) as caught: ledger.append(started)
            self.assertEqual(caught.exception.code, "DUPLICATE_ATTEMPT")
            self.assertEqual(len(path.read_text().splitlines()), 2)

    def test_launch_collect_and_restart_recover_private_handle(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); worktree = (base / "worktree").resolve(); worktree.mkdir()
            private = (base / "private").resolve(); private.mkdir(); output = (private / "output").resolve(); output.mkdir(); session = (private / "session").resolve(); session.mkdir()
            tree = b"100644 blob " + b"0" * 40 + b"\tfile.txt\0"
            source_digest = "sha256:" + hashlib.sha256(tree).hexdigest()
            empty_digest = raw_file_digest(canonical_json([]))
            def run(argv, **kwargs):
                if argv[:3] == ["herdr", "agent", "start"]:
                    result = {"agent_id": "agent-1", "workspace_id": "w", "tab_id": "t", "pane_id": "p", "pi_session_id": "pi-1"}
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "start", "result": result}).encode(), b"")
                if argv[:3] == ["herdr", "agent", "get"]:
                    result = {"status": "SETTLED", "workspace_id": "w", "tab_id": "t", "pane_id": "p", "observed_at": "2026-01-01T00:00:00Z", "source": "herdr"}
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "get", "result": result}).encode(), b"")
                if argv[:3] == ["herdr", "worktree", "open"]:
                    result = {"workspace_id": "w", "tab_id": "t", "pane_id": "p", "path": str(worktree)}
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "open", "result": result}).encode(), b"")
                if argv[:3] == ["herdr", "pane", "get"]:
                    result = {"cwd": str(worktree), "agent_id": "agent-1"}
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "pane", "result": result}).encode(), b"")
                if argv[:2] == ["herdr", "--help"] or argv[:2] == ["herdr", "agent"] or argv[:2] == ["herdr", "worktree"] or argv[:2] == ["herdr", "pane"]:
                    return subprocess.CompletedProcess(argv, 0, b"help", b"")
                if argv[:2] == ["pi", "--list-models"]:
                    return subprocess.CompletedProcess(argv, 0, b"openai-codex/gpt-5.6-luna\n", b"")
                if argv[:2] == ["pi", "--help"]:
                    return subprocess.CompletedProcess(argv, 0, b"help", b"")
                if argv[:3] == ["herdr", "agent", "read"]:
                    return subprocess.CompletedProcess(argv, 0, b"observed output", b"")
                if argv[:3] == ["herdr", "pane", "close"] or argv[:3] == ["herdr", "worktree", "remove"]:
                    return subprocess.CompletedProcess(argv, 0, b"", b"")
                if argv[:3] == ["herdr", "worktree", "list"]:
                    return subprocess.CompletedProcess(argv, 0, b"[]", b"")
                if argv[:3] == ["git", "-C", str(worktree)] and argv[3] == "symbolic-ref":
                    return subprocess.CompletedProcess(argv, 0, b"feature/x\n", b"")
                if argv[:3] == ["git", "-C", str(worktree)] and argv[3] == "rev-parse":
                    return subprocess.CompletedProcess(argv, 0, b"0" * 40 + b"\n", b"")
                if argv[:3] == ["git", "-C", str(worktree)] and argv[3] == "ls-tree":
                    return subprocess.CompletedProcess(argv, 0, tree, b"")
                raise AssertionError(argv)
            request = {"schema_version": 1, "request_id": "exec-1", "attempt_id": "att-1", "role": "worker", "host": {"mode": "herdr", "workspace_id": "w", "tab_id": "t", "pane_id": "p"}, "work": {"work_id": "work", "node_id": "node", "generation": 1, "ordinal": 1}, "worker_id": "worker-1", "branch": "feature/x", "candidate": {"repository": "owner/repo", "branch": "feature/x", "commit": "0" * 40, "source_digest": source_digest}, "worktree": str(worktree), "cwd": str(worktree), "output_root": str(output), "output_root_id": "out-1", "output_root_digest": empty_digest, "session_root": str(session), "session_root_id": "sess-1", "session_root_digest": empty_digest, "fresh_context": True, "neutral_input_digest": "sha256:" + "a" * 64, "environment_policy_digest": "sha256:" + "b" * 64, "tool_policy_digest": "sha256:" + "c" * 64, "required_capabilities": ["runtime_identity"], "runtime": {"provider": "openai-codex", "model": "openai-codex/gpt-5.6-luna", "thinking": "medium"}, "limits": {"deadline_ms": 1000, "max_output_bytes": 1000}, "allowance_id": "allow-1"}
            ledger = AttemptLedger(base / "attempts.jsonl")
            provider = HerdrProvider(runner=run, ledger=ledger, private_root=private)
            response = provider.launch(request); handle = response["result"]["handle"]
            self.assertEqual(set(handle), {"provider_id", "handle_id", "request_digest", "attempt_id", "host", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest"})
            recovery = {"schema_version": 1, "request_id": "recover-1", "attempt_id": "att-1", "recovery_authority": {"kind": "explicit", "authority_id": "human-1", "asserted": True, "reason": "retained crash evidence"}, "retained_evidence": [{"ref": ".razr/evidence/crash.json", "digest_domain": "canonical_json", "digest": "sha256:" + "e" * 64, "kind": "recovery"}]}
            self.assertEqual(provider.recover_unmatched(recovery)["result"]["outcome"], "UNKNOWN")
            collected = HerdrProvider(runner=run, ledger=ledger, private_root=private)
            result = collected.collect({"schema_version": 1, "request_id": "collect-1", "handle": handle})
            self.assertEqual(result["request_id"], "collect-1"); self.assertEqual(result["result"]["termination"], "UNKNOWN")
            self.assertEqual(collected.cleanup(handle)["cleanup"], "confirmed")
            with self.assertRaises(ProtocolError): collected._handle({**handle, "request_digest": "sha256:" + "d" * 64})
            self.assertEqual(len(ledger.path.read_text().splitlines()), 2)

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
