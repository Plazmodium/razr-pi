from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/razr-pi"))
from adapter.herdr_provider import AttemptLedger, HerdrProvider, ProviderRequest, ProtocolError
from adapter.protocol import canonical_json, raw_file_digest


def execution_host(*, workspace="w", tab="t", pane="p"):
    return {"mode": "herdr", "caller": {"workspace_id": "w", "tab_id": "t", "pane_id": "p"},
            "target": {"workspace_id": workspace, "tab_id": tab, "pane_id": pane}}


def request_fixture(base: Path, attempt: str = "att-hardening"):
    worktree = (base / "worktree").resolve(); worktree.mkdir()
    private = (base / "private").resolve(); private.mkdir()
    output = (private / "output").resolve(); output.mkdir()
    session = (private / "session").resolve(); session.mkdir()
    empty = raw_file_digest(canonical_json([])); source = "sha256:" + "f" * 64
    request = {"schema_version": 1, "request_id": "exec-hardening", "attempt_id": attempt, "role": "worker",
               "host": execution_host(), "work": {"work_id": "work", "node_id": "node", "generation": 3, "ordinal": 2},
               "worker_id": "worker-hardening", "branch": "feature/x",
               "candidate": {"repository": "owner/repo", "branch": "feature/x", "commit": "0" * 40, "source_digest": source},
               "worktree": str(worktree), "cwd": str(worktree), "output_root": str(output), "output_root_id": "out-" + attempt,
               "output_root_digest": empty, "session_root": str(session), "session_root_id": "sess-" + attempt,
               "session_root_digest": empty, "fresh_context": True, "neutral_input_digest": "sha256:" + "a" * 64,
               "environment_policy_digest": "sha256:" + "b" * 64, "tool_policy_digest": "sha256:" + "c" * 64,
               "required_capabilities": ["runtime_identity"],
               "runtime": {"provider": "openai-codex", "model": "openai-codex/gpt-5.6-luna", "thinking": "medium"},
               "limits": {"deadline_ms": 2000, "max_output_bytes": 1000}, "allowance_id": "allow-" + attempt}
    current = SimpleNamespace(work_id="work", node_id="node", generation=3, node_attempt=2,
                              candidate_repository="owner/repo", candidate_branch="feature/x", candidate_commit="0" * 40,
                              candidate_source_digest=source, raw_state_digest="sha256:" + "9" * 64,
                              raw_state_core_identity="sha256:" + "8" * 64)
    probe = {"schema_version": 1, "ok": True, "request_id": request["request_id"],
             "result": {"provider_id": "herdr", "runtime_identity": "openai-codex/gpt-5.6-luna:medium",
                        "capabilities": ["runtime_identity"], "report_digest": "sha256:" + "d" * 64}}
    return request, current, probe, private, worktree, output, session


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

    def test_cross_pane_request_preserves_caller_and_binds_target_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); work = root / "work"; work.mkdir()
            output = root / "output"; output.mkdir(); session = root / "session"; session.mkdir()
            digest = "sha256:" + "a" * 64
            host = execution_host(workspace="target-workspace", tab="target-tab", pane="target-pane")
            request = {"schema_version": 1, "request_id": "exec-cross-pane", "attempt_id": "att-cross-pane", "role": "worker",
                       "host": host, "work": {"work_id": "work", "node_id": "node", "generation": 1, "ordinal": 1},
                       "worker_id": "worker-cross-pane", "branch": "feature/x",
                       "candidate": {"repository": "fixture", "branch": "feature/x", "commit": "0" * 40, "source_digest": digest},
                       "worktree": str(work), "cwd": str(work), "output_root": str(output), "output_root_id": "out-cross-pane", "output_root_digest": digest,
                       "session_root": str(session), "session_root_id": "sess-cross-pane", "session_root_digest": digest, "fresh_context": True,
                       "neutral_input_digest": digest, "environment_policy_digest": digest, "tool_policy_digest": digest,
                       "required_capabilities": ["runtime_identity"],
                       "runtime": {"provider": "openai-codex", "model": "openai-codex/gpt-5.6-luna", "thinking": "medium"},
                       "limits": {"deadline_ms": 1000, "max_output_bytes": 1000}, "allowance_id": "allow-cross-pane"}
            parsed = ProviderRequest(request)
            self.assertEqual(parsed.value["host"], host)
            provider = HerdrProvider()
            target = {"workspace_id": "target-workspace", "tab_id": "target-tab", "pane_id": "target-pane"}
            with patch.object(provider, "_resolve_existing_worktree", return_value=target), patch("adapter.herdr_provider.verify_candidate_identity"):
                provider._verify_candidate(parsed)
            calls = []
            rejecting = HerdrProvider(runner=lambda argv, **kwargs: calls.append(argv),
                                      ledger=AttemptLedger(root / "attempts.jsonl"), private_root=root)
            probe = {"schema_version": 1, "ok": True, "request_id": "probe-cross-pane",
                     "result": {"provider_id": "herdr", "runtime_identity": "openai-codex/gpt-5.6-luna:medium",
                                "capabilities": ["runtime_identity"], "report_digest": digest}}
            current = SimpleNamespace(work_id="work", node_id="node", generation=1, node_attempt=1,
                                      candidate_repository="fixture", candidate_branch="feature/x",
                                      candidate_commit="0" * 40, candidate_source_digest=digest,
                                      raw_state_digest=digest, raw_state_core_identity=digest)
            with patch.object(rejecting, "probe", return_value=probe), \
                 patch.object(rejecting, "_read_current_work", return_value=current), \
                 patch.object(rejecting, "_resolve_existing_worktree", return_value={**target, "pane_id": "wrong-pane"}), \
                 patch("adapter.herdr_provider.verify_candidate_identity"):
                with self.assertRaises(ProtocolError) as caught:
                    rejecting.launch(request)
            self.assertEqual(caught.exception.code, "IDENTITY_MISMATCH")
            self.assertEqual(calls, [])
            with patch.dict(os.environ, {"HERDR_PANE_ID": "different-caller"}):
                with self.assertRaises(ProtocolError) as caught:
                    ProviderRequest(request)
            self.assertEqual(caught.exception.code, "IDENTITY_MISMATCH")

    def test_scoped_worktree_resolution_ignores_closed_historical_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); target = root / "target"; target.mkdir(); old = root / "old"; old.mkdir()
            listed = {"id": "cli:worktree:list", "result": {"source": {"repo_key": str(root / ".git"), "repo_name": "repo", "repo_root": str(root), "source_checkout_path": str(root), "source_workspace_id": "w"}, "type": "worktree_list", "worktrees": [{"is_bare": False, "is_detached": True, "is_linked_worktree": True, "is_prunable": True, "label": "old", "path": str(old), "historical_extra": "ignored"}, {"branch": "main", "is_bare": False, "is_detached": False, "is_linked_worktree": True, "is_prunable": False, "label": "target", "open_workspace_id": "w", "path": str(target)}]}}
            pane = {"agent_status": "unknown", "cwd": str(target), "focused": False, "foreground_cwd": str(target), "pane_id": "p", "revision": 0, "scroll": {"max_offset_from_bottom": 0, "offset_from_bottom": 0, "viewport_rows": 29}, "tab_id": "t", "terminal_id": "term", "workspace_id": "w"}
            def run(argv, **kwargs):
                if argv[:3] == ["herdr", "worktree", "list"]:
                    self.assertEqual(argv[3:], ["--cwd", str(target)])
                    return subprocess.CompletedProcess(argv, 0, json.dumps(listed).encode(), b"")
                if argv[:3] == ["herdr", "pane", "list"]:
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "cli:pane:list", "result": {"panes": [pane], "type": "pane_list"}}).encode(), b"")
                raise AssertionError(argv)
            result = HerdrProvider(runner=run)._resolve_existing_worktree(target)
            self.assertEqual(result, {"workspace_id": "w", "tab_id": "t", "pane_id": "p"})

    def test_nested_worktree_create_open_shape_and_identity_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); checkout = root / "checkout"; checkout.mkdir()
            fixture = {"id": "cli:worktree:create", "result": {"root_pane": {"agent_status": "unknown", "cwd": str(checkout), "focused": False, "foreground_cwd": str(checkout), "pane_id": "wP:p1", "revision": 0, "scroll": {"max_offset_from_bottom": 0, "offset_from_bottom": 0, "viewport_rows": 29}, "tab_id": "wP:t1", "terminal_id": "term_1", "workspace_id": "wP"}, "tab": {"agent_status": "unknown", "focused": False, "label": "1", "number": 1, "pane_count": 1, "tab_id": "wP:t1", "workspace_id": "wP"}, "type": "worktree_created", "workspace": {"active_tab_id": "wP:t1", "agent_status": "unknown", "focused": False, "label": "shape-test", "number": 1, "pane_count": 1, "tab_count": 1, "workspace_id": "wP", "worktree": {"checkout_path": str(checkout), "is_linked_worktree": True, "repo_key": str(root / ".git"), "repo_name": "repo", "repo_root": str(root)}}, "worktree": {"branch": "shape-test", "is_bare": False, "is_detached": False, "is_linked_worktree": True, "is_prunable": False, "label": "repo", "open_workspace_id": "wP", "path": str(checkout)}}}
            provider = HerdrProvider()
            with patch.object(provider, "_exec", side_effect=[json.dumps(fixture).encode(), json.dumps({**fixture, "id": "cli:worktree:open"}).encode()]):
                normalized = provider.worktree_create(cwd=str(root), branch="shape-test", base="main", label="shape-test")["result"]
                opened = provider.worktree_open(path=str(checkout))["result"]
            self.assertEqual(normalized, {"workspace_id": "wP", "tab_id": "wP:t1", "pane_id": "wP:p1", "path": str(checkout)})
            self.assertEqual(opened, normalized)
            conflict = json.loads(json.dumps(fixture)); conflict["result"]["workspace"]["workspace_id"] = "other"
            with patch.object(provider, "_exec", return_value=json.dumps(conflict).encode()):
                with self.assertRaises(ProtocolError) as caught: provider.worktree_open(path=str(checkout))
            self.assertEqual(caught.exception.code, "IDENTITY_MISMATCH")

    def test_model_probe_requires_unambiguous_structured_table(self):
        cases = [
            b"openai-codex gpt-5.6-luna\n",
            b"Provider Model\nopenai-codex gpt-5.6-luna\nopenai-codex gpt-5.6-luna\n",
            b"Provider Model\nopenai-codex/gpt-5.6-luna\n",
            b"Provider Model\nother-provider gpt-5.6-luna\n",
        ]
        for output in cases:
            with self.subTest(output=output):
                provider = HerdrProvider()
                with patch.object(provider, "_exec", return_value=output):
                    with self.assertRaises(ProtocolError) as caught: provider._model()
                self.assertEqual(caught.exception.code, "UNSUPPORTED_CAPABILITY")
                self.assertNotIn("release", str(caught.exception).lower())
                self.assertNotIn("version", str(caught.exception).lower())
        provider = HerdrProvider()
        with patch.object(provider, "_exec", return_value=b"Provider    Model             Context\nopenai-codex gpt-5.6-luna      200000\n"):
            self.assertEqual(provider._model().splitlines()[1].split()[:2], ["openai-codex", "gpt-5.6-luna"])

    def test_probe_uses_capabilities_not_version_and_argv_only(self):
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            help_text = b"Provider Model --no-session --session --continue --resume --cwd --path --pane --timeout --lines --workspace cwd pane"
            if argv[:2] == ["pi", "--list-models"]: return subprocess.CompletedProcess(argv, 0, b"Provider    Model             Context\nopenai-codex gpt-5.6-luna      200000\n", b"")
            return subprocess.CompletedProcess(argv, 0, help_text, b"")
        result = HerdrProvider(runner=run).probe({"schema_version": 1, "request_id": "probe-1", "required_capabilities": list(("fresh_context", "working_directory", "bounded_time", "bounded_output", "runtime_identity")), "runtime": {"provider": "openai-codex", "model": "openai-codex/gpt-5.6-luna", "thinking": "medium"}})
        self.assertEqual(result["request_id"], "probe-1")
        self.assertEqual(result["result"]["runtime_identity"], "openai-codex/gpt-5.6-luna:medium")
        self.assertTrue({"working_directory", "bounded_time", "bounded_output"} <= set(result["result"]["capabilities"]))
        self.assertEqual([call[0][:2] for call in calls], [["herdr", "--help"], ["herdr", "agent"], ["herdr", "agent"], ["herdr", "worktree"], ["herdr", "worktree"], ["herdr", "pane"], ["herdr", "pane"], ["pi", "--help"], ["pi", "--list-models"]])
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
                       "herdr": {"caller": execution_host()["caller"], "target": execution_host()["target"]}, "prev_digest": "sha256:" + "0" * 64}
            legacy = {**started, "herdr": {"workspace_id": "w", "tab_id": "t", "pane_id": "p"}}
            legacy["record_digest"] = raw_file_digest(canonical_json(legacy))
            self.assertEqual(AttemptLedger._replay_validate(legacy)["herdr"]["pane_id"], "p")
            with self.assertRaises(ProtocolError) as caught:
                ledger.append(legacy)
            self.assertEqual(caught.exception.code, "LEDGER_CORRUPT")
            for bad_schema in (True, 1.0):
                malformed = dict(started); malformed["schema_version"] = bad_schema
                with self.assertRaises(ProtocolError): ledger.append(malformed)
            for index, bad_ref in enumerate(("/absolute", "../traversal", "a\\\\b")):
                malformed = dict(started); malformed["attempt_id"] = "bad-ref-" + str(index); malformed["allowance_id"] = "bad-allow-" + str(index); malformed["worktree_ref"] = bad_ref; malformed["record_digest"] = raw_file_digest(canonical_json({**malformed, "record_digest": None}))
                with self.assertRaises(ProtocolError) as caught:
                    ledger.append(malformed)
                self.assertEqual(caught.exception.code, "LEDGER_CORRUPT")
            started["record_digest"] = raw_file_digest(canonical_json(started)); ledger.append(started)
            settled = {key: started[key] for key in ("schema_version", "attempt_id", "work_id", "node_id", "observed_generation", "observed_ordinal", "provider_id", "worktree_ref", "branch", "candidate", "candidate_evidence", "request_packet", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest", "request_digest", "herdr")}
            settled.update({"event": "SETTLED", "settled_at": "2026-01-01T00:01:00Z", "outcome": "UNKNOWN", "evidence_refs": [{"ref": ".razr/evidence/result.json", "digest_domain": "raw_file", "digest": digest, "kind": "result"}], "prev_digest": started["record_digest"], "cleanup": "uncertain"})
            settled["record_digest"] = raw_file_digest(canonical_json(settled)); ledger.append(settled)
            with self.assertRaises(ProtocolError) as caught: ledger.append(started)
            self.assertEqual(caught.exception.code, "DUPLICATE_ATTEMPT")
            self.assertEqual(len(path.read_text().splitlines()), 2)
            append_crash = {**started, "attempt_id": "append-crash", "allowance_id": "append-crash-allow",
                            "output_root_id": "append-crash-out", "session_root_id": "append-crash-session"}
            original_state_write = AttemptLedger._write_attempt_sidecar; injected = {"done": False}
            def fail_existing_state(dirfd, name, body):
                if name == ledger.state_path.name and body.get("records") == 3 and not injected["done"]:
                    injected["done"] = True; raise ProtocolError("LEDGER_CORRUPT", "injected existing append crash")
                return original_state_write(dirfd, name, body)
            with patch.object(AttemptLedger, "_write_attempt_sidecar", side_effect=fail_existing_state):
                with self.assertRaises(ProtocolError) as append_error: ledger.append(append_crash)
            self.assertEqual(append_error.exception.code, "LEDGER_CORRUPT"); self.assertTrue(ledger.intent_path.is_file())
            self.assertEqual(len(AttemptLedger(path).records()), 3); self.assertFalse(ledger.intent_path.exists())
            with patch("adapter.herdr_provider.fcntl.flock", side_effect=OSError("lock")):
                with self.assertRaises(ProtocolError) as lock_error: ledger.append({**started, "attempt_id": "lock-fail", "allowance_id": "lock-allow"})
            self.assertEqual(lock_error.exception.code, "LOCK_UNAVAILABLE")
            original_bytes = path.read_bytes()
            with patch("adapter.herdr_provider.os.write", side_effect=OSError("disk")):
                with self.assertRaises(ProtocolError) as write_error: ledger.append({**started, "attempt_id": "write-fail", "allowance_id": "write-allow"})
            self.assertEqual(write_error.exception.code, "LOCK_UNAVAILABLE"); self.assertEqual(path.read_bytes(), original_bytes)
            with patch("adapter.herdr_provider.os.write", side_effect=OSError("disk")), patch("adapter.herdr_provider.os.ftruncate", side_effect=OSError("rollback")):
                with self.assertRaises(ProtocolError) as rollback_error: ledger.append({**started, "attempt_id": "rollback-fail", "allowance_id": "rollback-allow"})
            self.assertEqual(rollback_error.exception.code, "LEDGER_CORRUPT")

    def test_strict_persisted_ledger_json_rejects_duplicate_and_noncanonical_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "attempts.jsonl"; ledger = AttemptLedger(path)
            for raw in (b'{"schema_version":1,"schema_version":1}\n', b'{ "schema_version": 1 }\n'):
                path.write_bytes(raw)
                with self.assertRaises(ProtocolError) as caught:
                    ledger.append({"schema_version": 1, "event": "STARTED"})
                self.assertEqual(caught.exception.code, "LEDGER_CORRUPT")

    def test_evidence_error_code_is_closed_by_kind_and_launch_scope(self):
        digest = "sha256:" + "a" * 64
        for code in ("LAUNCH_FAILED", "TIMEOUT", "OUTPUT_LIMIT", "PROVIDER_UNAVAILABLE"):
            AttemptLedger._ref({"ref": ".razr/evidence/failure.json", "digest_domain": "raw_file", "digest": digest, "kind": "launch_failure", "error_code": code}, "evidence_ref", "raw_file")
        with self.assertRaises(ProtocolError):
            AttemptLedger._ref({"ref": ".razr/evidence/result.json", "digest_domain": "raw_file", "digest": digest, "kind": "result", "error_code": "LAUNCH_FAILED"}, "evidence_ref", "raw_file")
        with self.assertRaises(ProtocolError):
            AttemptLedger._ref({"ref": ".razr/evidence/failure.json", "digest_domain": "raw_file", "digest": digest, "kind": "launch_failure", "error_code": "CLEANUP_UNKNOWN"}, "evidence_ref", "raw_file")

    def test_launch_collect_and_restart_recover_private_handle(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); worktree = (base / "worktree").resolve(); worktree.mkdir()
            private = (base / "private").resolve(); private.mkdir(); output = (private / "output").resolve(); output.mkdir(); session = (private / "session").resolve(); session.mkdir()
            tree = b"100644 blob " + b"0" * 40 + b"\tfile.txt\0"
            source_digest = "sha256:" + hashlib.sha256(tree).hexdigest()
            empty_digest = raw_file_digest(canonical_json([]))
            open_calls = []; list_calls = [1]
            host = execution_host(workspace="target-w", tab="target-t", pane="target-p")
            target = host["target"]
            def run(argv, **kwargs):
                if argv[:3] == ["herdr", "agent", "start"]:
                    self.assertEqual(argv[argv.index("--pane") + 1], target["pane_id"])
                    result = {"agent": {"name": "worker-1", "agent": "pi", "agent_session": {"kind": "provisional", "agent": "pi", "value": "herdr:pi"}, **target, "telemetry": {"status": "ready"}}, "type": "agent_started"}
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "cli:agent:start", "result": result}).encode(), b"")
                if argv[:3] == ["herdr", "agent", "get"]:
                    (session / "session-live.jsonl").write_bytes(b'{"type":"session"}\n')
                    result = {"agent": {"name": "worker-1", "agent_status": "done", **target, "telemetry": {"live": True}}, "telemetry": {"observed": True}}
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "cli:agent:get", "result": result}).encode(), b"")
                if argv[:3] == ["herdr", "workspace", "list"]:
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "cli:workspace:list", "result": {"type": "workspace_list", "workspaces": []}}).encode(), b"")
                if argv[:3] == ["herdr", "worktree", "list"]:
                    list_calls[0] += 1
                    if list_calls[0] > 1: return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "cli:worktree:list", "result": {"source": {"repo_key": str(worktree / ".git"), "repo_name": "repo", "repo_root": str(worktree), "source_checkout_path": str(worktree), "source_workspace_id": "other"}, "type": "worktree_list", "worktrees": []}}).encode(), b"")
                    result = {"source": {"repo_key": str(worktree / ".git"), "repo_name": "repo", "repo_root": str(worktree), "source_checkout_path": str(worktree), "source_workspace_id": target["workspace_id"]}, "type": "worktree_list", "worktrees": [{"branch": "feature/x", "is_bare": False, "is_detached": False, "is_linked_worktree": False, "is_prunable": False, "label": "worktree", "open_workspace_id": target["workspace_id"], "path": str(worktree)}]}
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "cli:worktree:list", "result": result}).encode(), b"")
                if argv[:3] == ["herdr", "pane", "list"]:
                    pane = {"agent_status": "unknown", "cwd": str(worktree), "focused": False, "foreground_cwd": str(worktree), **target, "revision": 0, "scroll": {"max_offset_from_bottom": 0, "offset_from_bottom": 0, "viewport_rows": 29}, "terminal_id": "term", "workspace_telemetry": {"layout": "grid"}}
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "cli:pane:list", "result": {"panes": [pane], "type": "pane_list"}}).encode(), b"")
                if argv[:3] == ["herdr", "worktree", "open"]:
                    if "--help" not in argv: open_calls.append(argv)
                    result = {"root_pane": {"agent_status": "unknown", "cwd": str(worktree), "focused": False, "foreground_cwd": str(worktree), "pane_id": "p", "revision": 0, "scroll": {"max_offset_from_bottom": 0, "offset_from_bottom": 0, "viewport_rows": 29}, "tab_id": "t", "terminal_id": "term", "workspace_id": "w"}, "tab": {"agent_status": "unknown", "focused": False, "label": "1", "number": 1, "pane_count": 1, "tab_id": "t", "workspace_id": "w"}, "type": "worktree_opened", "workspace": {"active_tab_id": "t", "agent_status": "unknown", "focused": False, "label": "worktree", "number": 1, "pane_count": 1, "tab_count": 1, "workspace_id": "w", "worktree": {"checkout_path": str(worktree), "is_linked_worktree": False, "repo_key": str(worktree / ".git"), "repo_name": "repo", "repo_root": str(worktree)}}, "worktree": {"branch": "feature/x", "is_bare": False, "is_detached": False, "is_linked_worktree": False, "is_prunable": False, "label": "worktree", "open_workspace_id": "w", "path": str(worktree)}}
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "cli:worktree:open", "result": result}).encode(), b"")
                if argv[:3] == ["herdr", "pane", "get"]:
                    result = {"pane": {"foreground_cwd": str(worktree), "cwd": str(worktree), "agent_id": "worker-1", "telemetry": {"source": "herdr"}}, "telemetry": {"request": "captured"}}
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "cli:pane:get", "result": result}).encode(), b"")
                if argv[:2] == ["herdr", "--help"] or argv[:2] == ["herdr", "agent"] or argv[:2] == ["herdr", "worktree"] or argv[:2] == ["herdr", "pane"]:
                    return subprocess.CompletedProcess(argv, 0, b"help", b"")
                if argv[:2] == ["pi", "--list-models"]:
                    return subprocess.CompletedProcess(argv, 0, b"Provider    Model             Context\nopenai-codex gpt-5.6-luna      200000\n", b"")
                if argv[:2] == ["pi", "--help"]:
                    return subprocess.CompletedProcess(argv, 0, b"Pi options: --approve -a --no-approve -na --no-context-files --no-extensions --no-skills --no-prompt-templates --no-themes --session --session-dir --no-session --continue --resume\n", b"")
                if argv[:3] == ["herdr", "agent", "read"]:
                    return subprocess.CompletedProcess(argv, 0, b"observed output", b"")
                if argv[:3] == ["herdr", "pane", "close"] or argv[:3] == ["herdr", "worktree", "remove"]:
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "cli:worktree:remove", "result": {"type": "worktree_removed", "workspace_id": "w", "removed": True}}).encode(), b"")
                if argv[:3] == ["herdr", "worktree", "list"]:
                    return subprocess.CompletedProcess(argv, 0, b"[]", b"")
                if argv[:3] == ["git", "-C", str(worktree)] and argv[3] == "symbolic-ref":
                    return subprocess.CompletedProcess(argv, 0, b"feature/x\n", b"")
                if argv[:3] == ["git", "-C", str(worktree)] and argv[3] == "rev-parse":
                    return subprocess.CompletedProcess(argv, 0, b"0" * 40 + b"\n", b"")
                if argv[:3] == ["git", "-C", str(worktree)] and argv[3] == "ls-tree":
                    return subprocess.CompletedProcess(argv, 0, tree, b"")
                raise AssertionError(argv)
            request = {"schema_version": 1, "request_id": "exec-1", "attempt_id": "att-1", "role": "worker", "host": host, "work": {"work_id": "work", "node_id": "node", "generation": 1, "ordinal": 1}, "worker_id": "worker-1", "branch": "feature/x", "candidate": {"repository": "owner/repo", "branch": "feature/x", "commit": "0" * 40, "source_digest": source_digest}, "worktree": str(worktree), "cwd": str(worktree), "output_root": str(output), "output_root_id": "out-1", "output_root_digest": empty_digest, "session_root": str(session), "session_root_id": "sess-1", "session_root_digest": empty_digest, "fresh_context": True, "neutral_input_digest": "sha256:" + "a" * 64, "environment_policy_digest": "sha256:" + "b" * 64, "tool_policy_digest": "sha256:" + "c" * 64, "required_capabilities": ["runtime_identity"], "runtime": {"provider": "openai-codex", "model": "openai-codex/gpt-5.6-luna", "thinking": "medium"}, "limits": {"deadline_ms": 1000, "max_output_bytes": 1000}, "allowance_id": "allow-1"}
            ledger = AttemptLedger(base / "attempts.jsonl")
            provider = HerdrProvider(runner=run, ledger=ledger, private_root=private)
            for bad_schema in (True, 1.0):
                with self.assertRaises(ProtocolError): provider.launch({**request, "schema_version": bad_schema})
            current = SimpleNamespace(work_id="work", node_id="node", generation=1, node_attempt=1,
                                      candidate_repository="owner/repo", candidate_branch="feature/x",
                                      candidate_commit="0" * 40, candidate_source_digest=source_digest,
                                      raw_state_digest="sha256:" + "9" * 64,
                                      raw_state_core_identity="sha256:" + "8" * 64)
            with patch.object(provider, "_verify_candidate") as verify, patch.object(provider, "_read_current_work", return_value=current):
                response = provider.launch(request)
            self.assertEqual(verify.call_count, 2)
            self.assertNotIn("allow_worktree_open", verify.call_args_list[0].kwargs)
            self.assertIs(verify.call_args_list[1].kwargs["allow_worktree_open"], False)
            handle = response["result"]["handle"]
            self.assertEqual(open_calls, [])
            self.assertEqual(set(handle), {"provider_id", "handle_id", "request_digest", "attempt_id", "host", "output_root_id", "output_root_digest", "session_root_id", "session_root_digest"})
            self.assertEqual(handle["host"], host)
            started = json.loads(ledger.path.read_text().splitlines()[0])
            self.assertEqual(started["herdr"], {"caller": host["caller"], "target": target})
            private_handle = json.loads((private / "handles" / "handle-worker-1.json").read_text())
            self.assertEqual(private_handle["handle"]["host"], host)
            self.assertEqual(response["result"]["capability_report_digest"], provider.probe({"schema_version": 1, "request_id": "probe-after-launch", "required_capabilities": ["runtime_identity"], "runtime": request["runtime"]})["result"]["report_digest"])
            self.assertEqual(provider.inspect({"schema_version": 1, "request_id": "inspect-unique", "handle": handle})["request_id"], "inspect-unique")
            collected = HerdrProvider(runner=run, ledger=ledger, private_root=private)
            result = collected.collect({"schema_version": 1, "request_id": "collect-1", "handle": handle})
            self.assertEqual(result["request_id"], "collect-1"); self.assertEqual(result["result"]["termination"], "UNKNOWN")
            response_refs = result["result"]["artifact_refs"]
            self.assertTrue(all(set(ref) == {"kind", "digest_domain", "digest", "bytes"} for ref in response_refs))
            self.assertNotIn("exit_code", result["result"])
            self.assertTrue(all(not any("/" in str(value) for value in ref.values()) for ref in response_refs))
            settled = json.loads(ledger.path.read_text().splitlines()[1])
            self.assertEqual(set(settled["evidence_refs"][0]), {"ref", "kind", "digest_domain", "digest"})
            self.assertEqual(len(settled["evidence_refs"][:2]), 2)
            self.assertTrue(all(ref["ref"].startswith(".razr/evidence/") and not ref["ref"].startswith("private/") for ref in settled["evidence_refs"][:2]))
            self.assertTrue(all(set(ref) == {"ref", "kind", "digest_domain", "digest"} for ref in settled["evidence_refs"][:2]))
            self.assertEqual(json.loads((private / "handles/handle-worker-1.json").read_text())["session_status"], "pending")
            binding_path = private / "handles/pi-binding-worker-1.json"
            self.assertTrue(binding_path.is_file())
            binding_path.unlink()  # simulate interruption after the durable session record
            restarted = HerdrProvider(runner=run, ledger=ledger, private_root=private)
            self.assertEqual(restarted.inspect({"schema_version": 1, "request_id": "inspect-after-bind", "handle": handle})["request_id"], "inspect-after-bind")
            self.assertTrue(binding_path.is_file())
            self.assertEqual(sum(row["event"] == "SETTLED" for row in ledger.records()), 1)
            cleanup_result = restarted.cleanup(handle)
            self.assertEqual(cleanup_result["cleanup"], "confirmed"); self.assertEqual(cleanup_result["status"], "ACTIVE")
            with self.assertRaises(ProtocolError): collected._handle({**handle, "request_digest": "sha256:" + "d" * 64})
            self.assertEqual(len(ledger.path.read_text().splitlines()), 2)

    def test_interrupt_request_id_is_echoed_and_closed(self):
        provider = HerdrProvider(runner=lambda *args, **kwargs: subprocess.CompletedProcess([], 0, b"", b""))
        with patch.object(provider, "_interrupt_handle", return_value={"result": {"attempt_id": "att-1", "host": execution_host(), "requested": True, "observed": "TERMINATED", "cleanup": "confirmed"}}):
            result = provider.interrupt({"schema_version": 1, "request_id": "interrupt-unique", "handle": {}, "reason": "cancel"})
        self.assertEqual(result["request_id"], "interrupt-unique")
        with self.assertRaises(ProtocolError): provider.interrupt({"schema_version": 1, "request_id": "interrupt-unique", "handle": {}, "reason": "cancel", "extra": True})

    def test_interrupt_repeat_is_idempotent_and_conflict_refuses_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); session = root / "session"; session.mkdir()
            value = {"attempt_id": "att-repeat", "handle_id": "agent-repeat", "request_digest": "sha256:" + "a" * 64, "host": execution_host()}
            provider = HerdrProvider(runner=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, b"", b""))
            provider._attempt_sessions[value["attempt_id"]] = session; provider._attempt_paths[value["attempt_id"]] = root
            with patch.object(provider, "_handle", return_value=value), patch.object(provider, "_inspect_handle", return_value={"result": {"status": "TERMINATED"}}), patch.object(provider, "_settle") as settle:
                first = provider._interrupt_handle(value, "cancel", "interrupt-repeat")
                second = provider._interrupt_handle(value, "cancel", "interrupt-repeat")
                self.assertEqual(first, second); self.assertEqual(settle.call_count, 1)
                with self.assertRaises(ProtocolError) as caught: provider._interrupt_handle(value, "timeout", "interrupt-repeat")
                self.assertEqual(caught.exception.code, "IDENTITY_MISMATCH")

    def test_successful_interrupt_uses_real_settlement_without_response_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); session = root / "session"; session.mkdir(); evidence = root / ".razr" / "evidence"; evidence.mkdir(parents=True)
            packet = root / ".razr" / "request.json"; packet.write_bytes(b"{}")
            candidate_file = evidence / "candidate.json"; candidate_file.write_bytes(b"{}")
            digest = "sha256:" + "a" * 64; ref = {"ref": ".razr/evidence/candidate.json", "digest_domain": "canonical_json", "digest": raw_file_digest(b"{}")}; packet_ref = {"ref": ".razr/request.json", "digest_domain": "canonical_json", "digest": raw_file_digest(b"{}")}
            started = {"schema_version": 1, "event": "STARTED", "attempt_id": "att-real-int", "work_id": "work", "node_id": "node", "worker_id": "worker", "observed_generation": 1, "observed_ordinal": 0, "provider_id": "herdr", "worktree_ref": ".", "branch": "feature/x", "allowance_id": "allow", "started_at": "2026-01-01T00:00:00Z", "started_at_source": "adapter_observed", "candidate": {"commit": "0" * 40, "source_digest": digest}, "candidate_evidence": ref, "request_packet": packet_ref, "output_root_id": "out", "output_root_digest": digest, "session_root_id": "sess", "session_root_digest": digest, "request_digest": digest, "herdr": {"caller": execution_host()["caller"], "target": execution_host()["target"]}, "prev_digest": "sha256:" + "0" * 64}
            started["record_digest"] = raw_file_digest(canonical_json(started)); ledger = AttemptLedger(root / "attempts.jsonl"); ledger.append(started)
            value = {"attempt_id": "att-real-int", "handle_id": "agent-real", "request_digest": digest, "host": execution_host(), "output_root_id": "out", "output_root_digest": digest, "session_root_id": "sess", "session_root_digest": digest}
            provider = HerdrProvider(ledger=ledger, runner=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, b"", b"")); provider._attempt_paths[value["attempt_id"]] = root; provider._attempt_sessions[value["attempt_id"]] = session
            with patch.object(provider, "_handle", return_value=value), patch.object(provider, "_inspect_handle", return_value={"result": {"status": "TERMINATED"}}):
                result = provider._interrupt_handle(value, "cancel", "interrupt-real")
            self.assertTrue(result["ok"]); settled = json.loads(ledger.path.read_text().splitlines()[1]); self.assertEqual(set(settled["evidence_refs"][0]), {"ref", "kind", "digest_domain", "digest"})

    def test_failing_interrupt_replays_same_protocol_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); session = root / "session"; session.mkdir()
            value = {"attempt_id": "att-fail", "handle_id": "agent-fail", "request_digest": "sha256:" + "a" * 64, "host": execution_host()}
            calls = []
            def fail(argv, **kwargs): calls.append(argv); raise ProtocolError("INTERRUPT_FAILED", "signal failed", retryable=True, details={"source": "fake"})
            provider = HerdrProvider(runner=fail); provider._attempt_sessions[value["attempt_id"]] = session; provider._attempt_paths[value["attempt_id"]] = root
            with patch.object(provider, "_handle", return_value=value), patch.object(provider, "_settle"):
                with self.assertRaises(ProtocolError) as first: provider._interrupt_handle(value, "cancel", "interrupt-fail")
                with self.assertRaises(ProtocolError) as second: provider._interrupt_handle(value, "cancel", "interrupt-fail")
            self.assertEqual(first.exception.code, second.exception.code); self.assertEqual(str(first.exception), str(second.exception)); self.assertEqual(len(calls), 1); self.assertEqual(calls[0][-1], "ctrl+c")

    def test_collection_failure_with_malformed_ledger_maps_and_retains_private_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); output = root / "output"; session = root / "session"; output.mkdir(); session.mkdir(); ledger_path = root / "attempts.jsonl"; ledger_path.write_text("not-json\\n")
            ledger = AttemptLedger(ledger_path); provider = HerdrProvider(ledger=ledger); value = {"attempt_id": "att-corrupt", "handle_id": "agent-corrupt"}
            provider._attempt_roots[value["attempt_id"]] = (output, session); provider._attempt_sessions[value["attempt_id"]] = session; provider._attempt_paths[value["attempt_id"]] = root
            (output / "agent-read-att-corrupt.bin").write_bytes(b"already")
            with patch.object(provider, "_handle", return_value=value), patch.object(provider, "_inspect_handle", return_value={"result": {"status": "TERMINATED"}}), patch.object(provider, "read", return_value=b"new"):
                with self.assertRaises(ProtocolError) as caught: provider._collect_handle(value)
            self.assertEqual(caught.exception.code, "LEDGER_CORRUPT")
            self.assertTrue((session / "collect-failure-att-corrupt.json").exists())

    def test_agent_get_nested_status_projection_maps_all_telemetry(self):
        value = {"attempt_id": "att-status", "handle_id": "worker-status", "request_digest": "sha256:" + "a" * 64, "host": execution_host(), "output_root_id": "out", "output_root_digest": "sha256:" + "b" * 64, "session_root_id": "sess", "session_root_digest": "sha256:" + "c" * 64}
        provider = HerdrProvider(); provider._private_handles[value["handle_id"]] = {"session_status": "pending"}
        for telemetry, expected in (("starting", "STARTING"), ("working", "ACTIVE"), ("idle", "ACTIVE"), ("done", "ACTIVE"), ("blocked", "ACTIVE"), ("unknown", "UNKNOWN")):
            raw = {"id": "cli:agent:get", "result": {"agent": {"name": "worker-status", "agent_status": telemetry, "workspace_id": "w", "tab_id": "t", "pane_id": "p", "extra": True}}}
            with patch.object(provider, "_handle", return_value=value), patch.object(provider, "_bind_session_if_available", return_value=False), patch.object(provider, "_exec", return_value=json.dumps(raw).encode()):
                self.assertEqual(provider._inspect_handle(value)["result"]["status"], expected)

    def test_nested_malformed_records_fail_as_ledger_corrupt(self):
        malformed = [None, [], {}, {"commit": "0" * 40}, {"source_digest": "sha256:" + "a" * 64}]
        for candidate in malformed:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ProtocolError) as caught:
                    AttemptLedger._replay_validate({"schema_version": 1, "event": "STARTED", "candidate": candidate, "candidate_evidence": [], "request_packet": None, "herdr": []})
                self.assertEqual(caught.exception.code, "LEDGER_CORRUPT")

    def test_recovery_retained_evidence_requires_existing_matching_boundary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); evidence = root / ".razr" / "evidence"; evidence.mkdir(parents=True)
            base = {"schema_version": 1, "request_id": "recover-unique", "attempt_id": "att-missing", "worktree": str(root), "recovery_authority": {"kind": "explicit", "authority_id": "human-1", "asserted": True, "reason": "crash"}}
            missing = {**base, "retained_evidence": [{"ref": ".razr/evidence/missing.json", "digest_domain": "raw_file", "digest": "sha256:" + "a" * 64, "kind": "event"}]}
            with self.assertRaises(ProtocolError) as caught: HerdrProvider().recover_unmatched(missing)
            self.assertEqual(caught.exception.code, "ARTIFACT_UNAVAILABLE")
            escaping = {**base, "retained_evidence": [{"ref": ".razr/evidence/../../secret", "digest_domain": "raw_file", "digest": "sha256:" + "a" * 64, "kind": "event"}]}
            with self.assertRaises(ProtocolError) as caught: HerdrProvider().recover_unmatched(escaping)
            self.assertEqual(caught.exception.code, "PATH_ESCAPE")
            actual = evidence / "actual.json"; actual.write_bytes(b"actual")
            mismatch = {**base, "retained_evidence": [{"ref": ".razr/evidence/actual.json", "digest_domain": "raw_file", "digest": "sha256:" + "b" * 64, "kind": "event"}]}
            with self.assertRaises(ProtocolError) as caught: HerdrProvider().recover_unmatched(mismatch)
            self.assertEqual(caught.exception.code, "DIGEST_MISMATCH")

    def test_launch_fences_generation_candidate_duplicate_and_failure_ordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve(); request, current, probe, private, worktree, output, session = request_fixture(base)
            ledger = AttemptLedger(base / "attempts.jsonl")
            herdr_calls = []
            provider = HerdrProvider(runner=lambda argv, **kwargs: herdr_calls.append(list(argv)),
                                     ledger=ledger, private_root=private)
            for code in ("STALE_GENERATION", "CANDIDATE_CHANGED"):
                with self.subTest(code=code), patch.object(provider, "probe", return_value=probe), \
                     patch.object(provider, "_read_current_work", side_effect=ProtocolError(code, "moved")), \
                     patch.object(provider, "_resolve_existing_worktree") as resolve, \
                     patch.object(provider, "worktree_open") as opened, \
                     patch("adapter.herdr_provider.verify_candidate_identity") as git_verify, \
                     patch.object(provider, "_prepare_launch_material") as claims:
                    with self.assertRaises(ProtocolError) as caught: provider.launch(request)
                self.assertEqual(caught.exception.code, code)
                resolve.assert_not_called(); opened.assert_not_called(); git_verify.assert_not_called()
                claims.assert_not_called()
            self.assertEqual(herdr_calls, [])
            self.assertFalse(ledger.path.exists())
            self.assertEqual(list(output.iterdir()), []); self.assertEqual(list(session.iterdir()), [])

            started_ref = {"ref": ".razr/evidence/candidate.json", "digest_domain": "canonical_json", "digest": "sha256:" + "e" * 64}
            started = {"schema_version": 1, "event": "STARTED", "attempt_id": request["attempt_id"], "work_id": "work", "node_id": "node",
                       "observed_generation": 3, "observed_ordinal": 2, "worker_id": "worker-hardening", "branch": "feature/x", "worktree_ref": ".",
                       "allowance_id": request["allowance_id"], "provider_id": "herdr", "started_at": "2026-01-01T00:00:00Z",
                       "started_at_source": "adapter_observed", "candidate": {"commit": "0" * 40, "source_digest": request["candidate"]["source_digest"]},
                       "candidate_evidence": started_ref, "request_packet": started_ref, "output_root_id": "prior-out", "output_root_digest": request["output_root_digest"],
                       "session_root_id": "prior-session", "session_root_digest": request["session_root_digest"], "request_digest": "sha256:" + "d" * 64,
                       "herdr": {"caller": execution_host()["caller"], "target": execution_host()["target"]}, "prev_digest": "sha256:" + "0" * 64}
            started["record_digest"] = raw_file_digest(canonical_json(started)); ledger.append(started)
            before = ledger.path.read_bytes()
            with patch.object(provider, "probe", return_value=probe), patch.object(provider, "_verify_candidate") as verify, \
                 patch.object(provider, "_prepare_launch_material") as claims:
                with self.assertRaises(ProtocolError) as caught: provider.launch(request)
            self.assertEqual(caught.exception.code, "LEDGER_CORRUPT")
            verify.assert_not_called(); claims.assert_not_called(); self.assertEqual(ledger.path.read_bytes(), before)
            self.assertFalse((private / "root-claims.jsonl").exists()); self.assertFalse((worktree / ".razr/evidence").exists())

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve(); request, current, probe, private, worktree, _, _ = request_fixture(base, "att-race")
            ledger = AttemptLedger(base / "attempts.jsonl"); calls = []
            provider = HerdrProvider(runner=lambda argv, **kwargs: calls.append(argv), ledger=ledger, private_root=private)
            candidate_checks = [None, ProtocolError("CANDIDATE_CHANGED", "moved before launch")]
            with patch.object(provider, "probe", return_value=probe), \
                 patch.object(provider, "_verify_candidate", side_effect=candidate_checks), \
                 patch.object(provider, "_read_current_work", return_value=current):
                with self.assertRaises(ProtocolError) as caught: provider.launch(request)
            self.assertEqual(caught.exception.code, "CANDIDATE_CHANGED")
            self.assertEqual(calls, [])
            events = ledger.records(); self.assertEqual([row["event"] for row in events], ["STARTED", "SETTLED"])
            self.assertEqual(events[1]["outcome"], "FAILED")
            boundary = json.loads((worktree / ".razr/evidence/launch-boundary-att-race.json").read_text())
            self.assertEqual(boundary["candidate"]["commit"], request["candidate"]["commit"])
            self.assertEqual(boundary["work"], request["work"])

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve(); request, current, probe, private, _, _, _ = request_fixture(base, "att-failure")
            ledger = AttemptLedger(base / "attempts.jsonl"); calls = []
            def fail_start(argv, **kwargs):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 7, b"", b"failed")
            provider = HerdrProvider(runner=fail_start, ledger=ledger, private_root=private)
            actual_open = os.open
            def fail_failure_evidence(path, flags, *args, **kwargs):
                if Path(path).name.startswith("launch-failure-"): raise OSError("evidence write failed")
                return actual_open(path, flags, *args, **kwargs)
            with patch.object(provider, "probe", return_value=probe), patch.object(provider, "_verify_candidate"), \
                 patch.object(provider, "_read_current_work", return_value=current), \
                 patch("adapter.herdr_provider.os.open", side_effect=fail_failure_evidence):
                with self.assertRaises(ProtocolError) as caught: provider.launch(request)
            self.assertEqual(caught.exception.code, "LAUNCH_FAILED")
            self.assertIn("--no-session", calls[0])
            self.assertEqual([row["event"] for row in ledger.records()], ["STARTED", "SETTLED"])
            self.assertEqual(ledger.records()[1]["evidence_refs"][0]["error_code"], "LAUNCH_FAILED")
            self.assertIn("launch-boundary-", ledger.records()[1]["evidence_refs"][0]["ref"])

    def test_worker_disappearance_recovery_and_concurrent_settlement_use_one_durable_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); evidence = root / ".razr/evidence"; evidence.mkdir(parents=True)
            digest = "sha256:" + "a" * 64
            retained = evidence / "pane-disappeared.json"; retained.write_bytes(b"pane disappeared")
            ref = {"ref": ".razr/evidence/candidate.json", "digest_domain": "canonical_json", "digest": digest}
            started = {"schema_version": 1, "event": "STARTED", "attempt_id": "att-disappeared", "work_id": "work", "node_id": "node",
                       "observed_generation": 4, "observed_ordinal": 1, "worker_id": "worker", "branch": "feature/x", "worktree_ref": ".",
                       "allowance_id": "allow-disappeared", "provider_id": "herdr", "started_at": "2026-01-01T00:00:00Z",
                       "started_at_source": "adapter_observed", "candidate": {"commit": "0" * 40, "source_digest": digest},
                       "candidate_evidence": ref, "request_packet": ref, "output_root_id": "out-disappeared", "output_root_digest": digest,
                       "session_root_id": "sess-disappeared", "session_root_digest": digest, "request_digest": digest,
                       "herdr": {"caller": execution_host()["caller"], "target": execution_host()["target"]}, "prev_digest": "sha256:" + "0" * 64}
            started["record_digest"] = raw_file_digest(canonical_json(started)); ledger = AttemptLedger(root / ".razr/attempts.jsonl"); ledger.append(started)
            retained_ref = {"ref": ".razr/evidence/pane-disappeared.json", "digest_domain": "raw_file",
                            "digest": raw_file_digest(retained.read_bytes()), "kind": "event"}
            recovered = HerdrProvider(ledger=ledger).recover_unmatched({"schema_version": 1, "request_id": "recover-disappeared",
                "attempt_id": "att-disappeared", "worktree": str(root),
                "recovery_authority": {"kind": "explicit", "authority_id": "human-1", "asserted": True, "reason": "pane disappeared"},
                "retained_evidence": [retained_ref]})
            self.assertEqual(recovered["result"]["outcome"], "UNKNOWN")
            self.assertEqual([row["event"] for row in ledger.records()], ["STARTED", "SETTLED"])
            self.assertEqual(ledger.records()[1]["outcome"], "UNKNOWN")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); evidence = root / ".razr/evidence"; evidence.mkdir(parents=True)
            digest = "sha256:" + "b" * 64; ref = {"ref": ".razr/evidence/candidate.json", "digest_domain": "canonical_json", "digest": digest}
            started = {"schema_version": 1, "event": "STARTED", "attempt_id": "att-race-settle", "work_id": "work", "node_id": "node",
                       "observed_generation": 1, "observed_ordinal": 1, "worker_id": "worker", "branch": "feature/x", "worktree_ref": ".",
                       "allowance_id": "allow-race", "provider_id": "herdr", "started_at": "2026-01-01T00:00:00Z", "started_at_source": "adapter_observed",
                       "candidate": {"commit": "0" * 40, "source_digest": digest}, "candidate_evidence": ref, "request_packet": ref,
                       "output_root_id": "out-race", "output_root_digest": digest, "session_root_id": "sess-race", "session_root_digest": digest,
                       "request_digest": digest, "herdr": {"caller": execution_host()["caller"], "target": execution_host()["target"]},
                       "prev_digest": "sha256:" + "0" * 64}
            started["record_digest"] = raw_file_digest(canonical_json(started)); ledger = AttemptLedger(root / ".razr/attempts.jsonl"); ledger.append(started)
            provider_a = HerdrProvider(ledger=ledger); provider_b = HerdrProvider(ledger=ledger)
            provider_a._attempt_paths["att-race-settle"] = root; provider_b._attempt_paths["att-race-settle"] = root
            errors = []
            def settle(provider):
                try: provider._settle({"attempt_id": "att-race-settle"}, "UNKNOWN", cleanup="uncertain", reason="concurrent terminal observation")
                except ProtocolError as exc: errors.append(exc.code)
            threads = [threading.Thread(target=settle, args=(provider,)) for provider in (provider_a, provider_b)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(5)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertEqual(sum(row["event"] == "SETTLED" for row in ledger.records()), 1)

    def test_stale_authority_refuses_before_worktree_or_herdr_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            request, _, probe, private, worktree, output, session = request_fixture(base, "att-pre-open-fence")
            ledger = AttemptLedger(base / "attempts.jsonl")
            herdr_calls = []
            provider = HerdrProvider(runner=lambda argv, **kwargs: herdr_calls.append(list(argv)),
                                     ledger=ledger, private_root=private)
            for code in ("STALE_GENERATION", "CANDIDATE_CHANGED"):
                with self.subTest(code=code), patch.object(provider, "probe", return_value=probe), \
                     patch.object(provider, "_read_current_work", side_effect=ProtocolError(code, "moved")), \
                     patch.object(provider, "_resolve_existing_worktree") as resolve, \
                     patch.object(provider, "worktree_open") as opened, \
                     patch("adapter.herdr_provider.verify_candidate_identity") as git_verify, \
                     patch.object(provider, "_prepare_launch_material") as claims:
                    with self.assertRaises(ProtocolError) as caught:
                        provider.launch(request)
                self.assertEqual(caught.exception.code, code)
                resolve.assert_not_called(); opened.assert_not_called(); git_verify.assert_not_called()
                claims.assert_not_called()
            self.assertEqual(herdr_calls, [])
            self.assertFalse(ledger.path.exists())
            self.assertFalse((worktree / ".razr").exists())
            self.assertEqual(list(output.iterdir()), []); self.assertEqual(list(session.iterdir()), [])

    def test_evidence_parent_symlinks_refuse_without_outside_write_or_event(self):
        for linked_component in (".razr", "evidence"):
            with self.subTest(component=linked_component), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp).resolve(); request, current, probe, private, worktree, _, _ = request_fixture(base, "att-linked-" + linked_component.strip("."))
                outside = base / "outside"; outside.mkdir()
                if linked_component == ".razr":
                    (worktree / ".razr").symlink_to(outside, target_is_directory=True)
                else:
                    (worktree / ".razr").mkdir(); (worktree / ".razr/evidence").symlink_to(outside, target_is_directory=True)
                ledger = AttemptLedger(base / "attempts.jsonl")
                provider = HerdrProvider(runner=lambda argv, **kwargs: (_ for _ in ()).throw(AssertionError(argv)), ledger=ledger, private_root=private)
                with patch.object(provider, "probe", return_value=probe), patch.object(provider, "_verify_candidate"), \
                     patch.object(provider, "_read_current_work", return_value=current):
                    with self.assertRaises(ProtocolError) as caught: provider.launch(request)
                self.assertEqual(caught.exception.code, "PATH_ESCAPE")
                self.assertEqual(list(outside.iterdir()), []); self.assertFalse(ledger.path.exists())
                self.assertFalse((private / "root-claims.jsonl").exists())

    def test_launch_failure_evidence_refuses_swapped_parent_without_outside_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve(); request, current, probe, private, worktree, _, _ = request_fixture(base, "att-launch-link")
            outside = base / "outside"; outside.mkdir(); ledger = AttemptLedger(base / "attempts.jsonl")
            provider = HerdrProvider(runner=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 7, b"", b"failed"), ledger=ledger, private_root=private)
            checks = {"count": 0}
            def swap_before_start(*args, **kwargs):
                checks["count"] += 1
                if checks["count"] == 2:
                    evidence = worktree / ".razr/evidence"; evidence.rename(worktree / ".razr/evidence-retained")
                    evidence.symlink_to(outside, target_is_directory=True)
            with patch.object(provider, "probe", return_value=probe), patch.object(provider, "_verify_candidate", side_effect=swap_before_start), \
                 patch.object(provider, "_read_current_work", return_value=current):
                with self.assertRaises(ProtocolError): provider.launch(request)
            self.assertFalse(any(path.name.startswith("launch-failure-") for path in outside.iterdir()))
            self.assertEqual([row["event"] for row in ledger.records()], ["STARTED"])

    def test_dispatch_transaction_failures_retry_or_settle_without_double_launch(self):
        def run(provider, request, current, probe):
            with patch.object(provider, "probe", return_value=probe), \
                 patch.object(provider, "_verify_candidate"), \
                 patch.object(provider, "_read_current_work", return_value=current):
                return provider.launch(request)

        # Each pre-START evidence step is transactional: ordinary failure leaves
        # no claim/evidence, and the exact request can retry once.
        for failing_link in (1, 2, 3):
            with self.subTest(kind="evidence", step=failing_link), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp).resolve(); request, current, probe, private, worktree, _, _ = request_fixture(base, f"att-evidence-{failing_link}")
                ledger = AttemptLedger(base / "attempts.jsonl"); starts = []
                def runner(argv, **kwargs):
                    if argv[:3] == ["herdr", "agent", "start"]:
                        starts.append(argv); return subprocess.CompletedProcess(argv, 7, b"", b"failed")
                    raise AssertionError(argv)
                provider = HerdrProvider(runner=runner, ledger=ledger, private_root=private)
                actual_link = os.link; calls = {"count": 0}
                def fail_link(source, target, *args, **kwargs):
                    result = actual_link(source, target, *args, **kwargs); calls["count"] += 1
                    if calls["count"] == failing_link: raise OSError("injected post-evidence failure")
                    return result
                with patch("adapter.herdr_provider.os.link", side_effect=fail_link):
                    with self.assertRaises(ProtocolError) as caught: run(provider, request, current, probe)
                self.assertIn(caught.exception.code, {"ARTIFACT_UNAVAILABLE", "PATH_ESCAPE"})
                self.assertFalse(ledger.path.exists()); self.assertFalse((private / "root-claims.jsonl").exists())
                self.assertEqual(list(private.glob("dispatch-prepared-*.json")), [])
                evidence = worktree / ".razr/evidence"
                self.assertFalse(evidence.exists() and any(evidence.iterdir()))
                with self.assertRaises(ProtocolError) as caught: run(provider, request, current, probe)
                self.assertEqual(caught.exception.code, "LAUNCH_FAILED")
                self.assertEqual(len(starts), 1)
                self.assertEqual([row["event"] for row in ledger.records()], ["STARTED", "SETTLED"])
                with self.assertRaises(ProtocolError) as caught: run(provider, request, current, probe)
                self.assertEqual(caught.exception.code, "DUPLICATE_ATTEMPT"); self.assertEqual(len(starts), 1)

        # A failed STARTED append is known-not-committed, cleans staging, and is exactly retryable.
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve(); request, current, probe, private, worktree, _, _ = request_fixture(base, "att-start-append")
            ledger = AttemptLedger(base / "attempts.jsonl"); starts = []
            def runner(argv, **kwargs):
                if argv[:3] == ["herdr", "agent", "start"]:
                    starts.append(argv); return subprocess.CompletedProcess(argv, 7, b"", b"failed")
                raise AssertionError(argv)
            provider = HerdrProvider(runner=runner, ledger=ledger, private_root=private)
            with patch.object(ledger, "append", side_effect=ProtocolError("LOCK_UNAVAILABLE", "injected STARTED failure")):
                with self.assertRaises(ProtocolError) as caught: run(provider, request, current, probe)
            self.assertEqual(caught.exception.code, "LOCK_UNAVAILABLE")
            self.assertFalse(ledger.path.exists()); self.assertFalse((private / "root-claims.jsonl").exists())
            self.assertEqual(list(private.glob("dispatch-prepared-*.json")), [])
            self.assertFalse(any((worktree / ".razr/evidence").iterdir()))
            with self.assertRaises(ProtocolError): run(provider, request, current, probe)
            self.assertEqual(len(starts), 1)
            self.assertEqual([row["event"] for row in ledger.records()], ["STARTED", "SETTLED"])

        # Root-claim and marker failures happen only after STARTED. They settle
        # that exact attempt and can never produce a second agent start.
        for kind, failing_step in (("claim", 1), ("claim", 2), ("marker", 1), ("marker", 2)):
            with self.subTest(kind=kind, step=failing_step), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp).resolve(); request, current, probe, private, _, _, _ = request_fixture(base, f"att-{kind}-{failing_step}")
                ledger = AttemptLedger(base / "attempts.jsonl"); starts = []
                provider = HerdrProvider(runner=lambda argv, **kwargs: starts.append(argv), ledger=ledger, private_root=private)
                calls = {"count": 0}
                if kind == "claim":
                    original = provider._claim_root_identity
                    def fail_after(*args, **kwargs):
                        result = original(*args, **kwargs); calls["count"] += 1
                        if calls["count"] == failing_step: raise ProtocolError("ARTIFACT_UNAVAILABLE", "injected post-claim failure")
                        return result
                    boundary = patch.object(provider, "_claim_root_identity", side_effect=fail_after)
                else:
                    from adapter import herdr_provider as provider_module
                    original_marker = provider_module._marker
                    def fail_after(*args, **kwargs):
                        result = original_marker(*args, **kwargs); calls["count"] += 1
                        if calls["count"] == failing_step: raise ProtocolError("ARTIFACT_UNAVAILABLE", "injected post-marker failure")
                        return result
                    boundary = patch("adapter.herdr_provider._marker", side_effect=fail_after)
                with boundary:
                    with self.assertRaises(ProtocolError) as caught: run(provider, request, current, probe)
                self.assertEqual(caught.exception.code, "ARTIFACT_UNAVAILABLE")
                self.assertEqual([row["event"] for row in ledger.records()], ["STARTED", "SETTLED"])
                restarted = HerdrProvider(runner=lambda argv, **kwargs: starts.append(argv), ledger=ledger, private_root=private)
                with self.assertRaises(ProtocolError) as caught: run(restarted, request, current, probe)
                self.assertEqual(caught.exception.code, "DUPLICATE_ATTEMPT")
                self.assertEqual(starts, [])

    def test_committed_root_path_remains_consumed_after_settlement_and_recreation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve(); request, current, probe, private, _, output, _ = request_fixture(base, "att-root-original")
            ledger = AttemptLedger(base / "attempts.jsonl"); starts = []
            def runner(argv, **kwargs):
                if argv[:3] == ["herdr", "agent", "start"]:
                    starts.append(argv); return subprocess.CompletedProcess(argv, 7, b"", b"failed")
                raise AssertionError(argv)
            provider = HerdrProvider(runner=runner, ledger=ledger, private_root=private)
            def launch(instance, value):
                with patch.object(instance, "probe", return_value=probe), patch.object(instance, "_verify_candidate"), \
                     patch.object(instance, "_read_current_work", return_value=current):
                    return instance.launch(value)
            with self.assertRaises(ProtocolError): launch(provider, request)
            self.assertEqual(len(starts), 1); self.assertEqual([row["event"] for row in ledger.records()], ["STARTED", "SETTLED"])
            __import__("shutil").rmtree(output); output.mkdir()
            session2 = private / "session-recreated"; session2.mkdir()
            second = json.loads(json.dumps(request)); second.update({"request_id": "exec-root-recreated", "attempt_id": "att-root-recreated",
                "allowance_id": "allow-root-recreated", "worker_id": "worker-root-recreated",
                "output_root_id": "out-root-recreated", "session_root": str(session2), "session_root_id": "sess-root-recreated"})
            restarted = HerdrProvider(runner=runner, ledger=ledger, private_root=private)
            with self.assertRaises(ProtocolError) as caught: launch(restarted, second)
            self.assertEqual(caught.exception.code, "RESOURCE_REUSED")
            self.assertEqual(len(starts), 1)
            self.assertEqual([row["event"] for row in ledger.records()], ["STARTED", "SETTLED", "STARTED", "SETTLED"])
            claims = [json.loads(line) for line in (private / "root-claims.jsonl").read_text().splitlines()]
            self.assertEqual(sum(row["path"] == str(output) for row in claims), 1)

    def test_lost_or_replaced_root_claim_journal_refuses_before_new_started_or_launch(self):
        for damage in ("deleted", "replaced", "state-deleted"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp).resolve(); request, current, probe, private, _, output, _ = request_fixture(base, "att-journal-original-" + damage)
                ledger = AttemptLedger(base / "attempts.jsonl"); starts = []
                def runner(argv, **kwargs):
                    if argv[:3] == ["herdr", "agent", "start"]:
                        starts.append(argv); return subprocess.CompletedProcess(argv, 7, b"", b"failed")
                    raise AssertionError(argv)
                provider = HerdrProvider(runner=runner, ledger=ledger, private_root=private)
                with patch.object(provider, "probe", return_value=probe), patch.object(provider, "_verify_candidate"), \
                     patch.object(provider, "_read_current_work", return_value=current):
                    with self.assertRaises(ProtocolError): provider.launch(request)
                self.assertEqual(len(starts), 1); self.assertEqual([row["event"] for row in ledger.records()], ["STARTED", "SETTLED"])
                journal = private / "root-claims.jsonl"; original = journal.read_bytes()
                if damage == "deleted":
                    journal.unlink(); (private / "root-claims.state.json").unlink()
                elif damage == "replaced":
                    journal.rename(private / "root-claims.replaced"); journal.write_bytes(original)
                else:
                    (private / "root-claims.state.json").unlink()
                __import__("shutil").rmtree(output); output.mkdir()
                session2 = private / ("session-journal-" + damage); session2.mkdir()
                second = json.loads(json.dumps(request)); second.update({"request_id": "exec-journal-" + damage,
                    "attempt_id": "att-journal-new-" + damage, "allowance_id": "allow-journal-" + damage,
                    "worker_id": "worker-journal-" + damage, "output_root_id": "out-journal-" + damage,
                    "session_root": str(session2), "session_root_id": "sess-journal-" + damage})
                restarted = HerdrProvider(runner=runner, ledger=ledger, private_root=private)
                with patch.object(restarted, "probe", return_value=probe), patch.object(restarted, "_read_current_work") as authority, \
                     patch.object(restarted, "_verify_candidate") as candidate:
                    with self.assertRaises(ProtocolError) as caught: restarted.launch(second)
                self.assertEqual(caught.exception.code, "LEDGER_CORRUPT")
                authority.assert_not_called(); candidate.assert_not_called()
                self.assertEqual(len(starts), 1); self.assertEqual([row["event"] for row in ledger.records()], ["STARTED", "SETTLED"])

    def test_lost_replaced_truncated_or_reset_attempt_journal_refuses_before_dispatch(self):
        for damage in ("missing", "replaced", "truncated", "prefix-reset", "state-missing"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp).resolve(); request, current, probe, private, worktree, _, _ = request_fixture(base, "att-attempt-original-" + damage)
                ledger = AttemptLedger(base / "attempts.jsonl"); starts = []
                def runner(argv, **kwargs):
                    if argv[:3] == ["herdr", "agent", "start"]:
                        starts.append(argv); return subprocess.CompletedProcess(argv, 7, b"", b"failed")
                    raise AssertionError(argv)
                provider = HerdrProvider(runner=runner, ledger=ledger, private_root=private)
                with patch.object(provider, "probe", return_value=probe), patch.object(provider, "_verify_candidate"), patch.object(provider, "_read_current_work", return_value=current):
                    with self.assertRaises(ProtocolError): provider.launch(request)
                original = ledger.path.read_bytes(); self.assertEqual(len(starts), 1); self.assertTrue(ledger.state_path.is_file())
                if damage == "missing":
                    ledger.path.unlink()
                elif damage == "replaced":
                    ledger.path.rename(base / "attempts.replaced"); ledger.path.write_bytes(original)
                elif damage == "truncated":
                    ledger.path.write_bytes(original[:len(original) // 2])
                elif damage == "prefix-reset":
                    ledger.path.write_bytes(original.splitlines(keepends=True)[0])
                else:
                    ledger.state_path.unlink()
                output2 = private / ("output-attempt-" + damage); session2 = private / ("session-attempt-" + damage); output2.mkdir(); session2.mkdir()
                second = json.loads(json.dumps(request)); second.update({"request_id": "exec-attempt-" + damage,
                    "attempt_id": "att-attempt-new-" + damage, "allowance_id": "allow-attempt-" + damage,
                    "worker_id": "worker-attempt-" + damage, "output_root": str(output2), "output_root_id": "out-attempt-" + damage,
                    "session_root": str(session2), "session_root_id": "sess-attempt-" + damage})
                restarted = HerdrProvider(runner=runner, ledger=ledger, private_root=private)
                with patch.object(restarted, "probe", return_value=probe), patch.object(restarted, "_read_current_work") as authority, patch.object(restarted, "_verify_candidate") as candidate:
                    with self.assertRaises(ProtocolError) as caught: restarted.launch(second)
                self.assertEqual(caught.exception.code, "LEDGER_CORRUPT"); authority.assert_not_called(); candidate.assert_not_called()
                self.assertEqual(len(starts), 1); self.assertNotIn(second["attempt_id"].encode("utf-8"), ledger.path.read_bytes() if ledger.path.exists() else b"")
                self.assertFalse((worktree / ".razr/evidence" / ("attempt-" + second["attempt_id"] + ".json")).exists())

    def test_first_attempt_append_state_crash_recovers_only_authenticated_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve(); request, current, probe, private, _, _, _ = request_fixture(base, "att-attempt-intent")
            ledger = AttemptLedger(base / "attempts.jsonl"); starts = []; provider = HerdrProvider(runner=lambda argv, **kwargs: starts.append(argv), ledger=ledger, private_root=private)
            original_write = AttemptLedger._write_attempt_sidecar; failed = {"done": False}
            def fail_first_state(dirfd, name, body):
                if name == ledger.state_path.name and body.get("records") == 1 and not failed["done"]:
                    failed["done"] = True; raise ProtocolError("LEDGER_CORRUPT", "injected state crash")
                return original_write(dirfd, name, body)
            with patch.object(provider, "probe", return_value=probe), patch.object(provider, "_verify_candidate"), patch.object(provider, "_read_current_work", return_value=current), \
                 patch.object(AttemptLedger, "_write_attempt_sidecar", side_effect=fail_first_state):
                with self.assertRaises(ProtocolError) as caught: provider.launch(request)
            self.assertEqual(caught.exception.code, "LEDGER_CORRUPT"); self.assertEqual(starts, [])
            self.assertTrue(ledger.path.is_file()); self.assertFalse(ledger.state_path.exists()); self.assertTrue(ledger.intent_path.is_file())
            restarted = HerdrProvider(runner=lambda argv, **kwargs: starts.append(argv), ledger=ledger, private_root=private)
            with patch.object(restarted, "probe", return_value=probe), patch.object(restarted, "_read_current_work") as authority, patch.object(restarted, "_verify_candidate") as candidate:
                with self.assertRaises(ProtocolError) as caught: restarted.launch(request)
            self.assertEqual(caught.exception.code, "DUPLICATE_ATTEMPT"); authority.assert_not_called(); candidate.assert_not_called()
            self.assertEqual([row["event"] for row in ledger.records()], ["STARTED"]); self.assertTrue(ledger.state_path.is_file()); self.assertFalse(ledger.intent_path.exists())
            self.assertEqual(starts, []); self.assertEqual(list(private.glob("dispatch-prepared-*.json")), [])

    def test_pre_started_crash_staging_is_reconciled_and_exactly_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve(); request, current, probe, private, _, _, _ = request_fixture(base, "att-pre-start-crash")
            ledger = AttemptLedger(base / "attempts.jsonl"); starts = []
            def runner(argv, **kwargs):
                if argv[:3] == ["herdr", "agent", "start"]:
                    starts.append(argv); return subprocess.CompletedProcess(argv, 7, b"", b"failed")
                raise AssertionError(argv)
            provider = HerdrProvider(runner=runner, ledger=ledger, private_root=private)
            with patch.object(provider, "probe", return_value=probe), patch.object(provider, "_verify_candidate"), \
                 patch.object(provider, "_read_current_work", return_value=current), \
                 patch.object(ledger, "append", side_effect=KeyboardInterrupt("crash before STARTED commit")):
                with self.assertRaises(KeyboardInterrupt): provider.launch(request)
            self.assertFalse(ledger.path.exists()); self.assertEqual(len(list(private.glob("dispatch-prepared-*.json"))), 1)
            restarted = HerdrProvider(runner=runner, ledger=ledger, private_root=private)
            with patch.object(restarted, "probe", return_value=probe), patch.object(restarted, "_verify_candidate"), \
                 patch.object(restarted, "_read_current_work", return_value=current):
                with self.assertRaises(ProtocolError) as caught: restarted.launch(request)
            self.assertEqual(caught.exception.code, "LAUNCH_FAILED")
            self.assertEqual(len(starts), 1); self.assertEqual(list(private.glob("dispatch-prepared-*.json")), [])
            self.assertEqual([row["event"] for row in ledger.records()], ["STARTED", "SETTLED"])

    def test_post_started_crash_is_reconciled_and_explicitly_recoverable(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve(); request, current, probe, private, worktree, output, session = request_fixture(base, "att-crash-window")
            ledger = AttemptLedger(base / "attempts.jsonl"); starts = []
            provider = HerdrProvider(runner=lambda argv, **kwargs: starts.append(argv), ledger=ledger, private_root=private)
            with patch.object(provider, "probe", return_value=probe), patch.object(provider, "_verify_candidate"), \
                 patch.object(provider, "_read_current_work", return_value=current), \
                 patch.object(provider, "_materialize_root_claims", side_effect=KeyboardInterrupt("crash")):
                with self.assertRaises(KeyboardInterrupt): provider.launch(request)
            self.assertEqual([row["event"] for row in ledger.records()], ["STARTED"])
            self.assertEqual(len(list(private.glob("dispatch-prepared-*.json"))), 1)
            self.assertEqual(starts, [])

            restarted = HerdrProvider(runner=lambda argv, **kwargs: starts.append(argv), ledger=ledger, private_root=private)
            with patch.object(restarted, "probe", return_value=probe), patch.object(restarted, "_verify_candidate"), \
                 patch.object(restarted, "_read_current_work", return_value=current):
                with self.assertRaises(ProtocolError) as caught: restarted.launch(request)
            self.assertEqual(caught.exception.code, "DUPLICATE_ATTEMPT")
            self.assertEqual(starts, []); self.assertEqual(list(private.glob("dispatch-prepared-*.json")), [])
            self.assertTrue((output / ".razr-herdr-root.json").is_file())
            self.assertTrue((session / ".razr-herdr-root.json").is_file())
            claims = (private / "root-claims.jsonl").read_text().splitlines()
            self.assertEqual(len(claims), 2)
            self.assertTrue((private / "root-claims.state.json").is_file())
            boundary = worktree / ".razr/evidence/launch-boundary-att-crash-window.json"
            retained = {"ref": ".razr/evidence/" + boundary.name, "digest_domain": "raw_file",
                        "digest": raw_file_digest(boundary.read_bytes()), "kind": "event"}
            result = restarted.recover_unmatched({"schema_version": 1, "request_id": "recover-crash-window",
                "attempt_id": request["attempt_id"], "worktree": str(worktree),
                "recovery_authority": {"kind": "explicit", "authority_id": "human-crash-recovery", "asserted": True, "reason": "confirmed pre-launch crash"},
                "retained_evidence": [retained]})
            self.assertEqual(result["result"]["outcome"], "UNKNOWN")
            self.assertEqual([row["event"] for row in ledger.records()], ["STARTED", "SETTLED"])

    def test_end_to_end_deadline_includes_final_authority_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve(); request, current, probe, private, _, _, _ = request_fixture(base, "att-deadline")
            request["limits"]["deadline_ms"] = 50
            ledger = AttemptLedger(base / "attempts.jsonl"); calls = []
            provider = HerdrProvider(runner=lambda argv, **kwargs: calls.append(argv), ledger=ledger, private_root=private)
            reads = {"count": 0}
            def slow_final(_request, **_kwargs):
                reads["count"] += 1
                if reads["count"] == 2: time.sleep(.06)
                return current
            with patch.object(provider, "probe", return_value=probe), patch.object(provider, "_verify_candidate"), \
                 patch.object(provider, "_read_current_work", side_effect=slow_final):
                with self.assertRaises(ProtocolError) as caught: provider.launch(request)
            self.assertEqual(caught.exception.code, "TIMEOUT")
            self.assertEqual(calls, [])
            self.assertEqual([row["event"] for row in ledger.records()], ["STARTED", "SETTLED"])

    def test_concurrent_launches_keep_distinct_explicit_deadlines(self):
        observed = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)
        def runner(argv, **kwargs):
            with lock: observed.append((argv[-1], kwargs["timeout"]))
            return subprocess.CompletedProcess(argv, 0, b"ok", b"")
        provider = HerdrProvider(runner=runner)
        def bounded_launch(request, _started, deadline_at):
            barrier.wait(timeout=2)
            provider._exec(["herdr", "deadline", request["request_id"]], timeout_ms=10_000,
                           deadline_at=deadline_at)
            return {"request_id": request["request_id"]}
        provider._launch = bounded_launch  # type: ignore[method-assign]
        requests = [
            {"request_id": "short", "limits": {"deadline_ms": 800}},
            {"request_id": "long", "limits": {"deadline_ms": 5000}},
        ]
        results = []; failures = []
        def run(request):
            try: results.append(provider.launch(request))
            except Exception as exc: failures.append(exc)
        threads = [threading.Thread(target=run, args=(request,)) for request in requests]
        for thread in threads: thread.start()
        for thread in threads: thread.join(3)
        self.assertFalse(failures)
        self.assertEqual({row["request_id"] for row in results}, {"short", "long"})
        bounds = dict(observed)
        self.assertLessEqual(bounds["short"], .8)
        self.assertGreater(bounds["long"] - bounds["short"], 3.5)
        self.assertFalse(hasattr(provider, "_operation_deadline_at"))

    def test_native_command_boundary_streams_kills_groups_and_rejects_symlinks(self):
        python = str(Path(sys.executable).resolve())
        provider = HerdrProvider(herdr=python, pi=python)
        with self.assertRaises(ProtocolError) as caught:
            provider._exec([python, "-c", "import os; os.write(1,b'x'*200000)"], timeout_ms=2000, max_output=1024)
        self.assertEqual(caught.exception.code, "OUTPUT_LIMIT")
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / "child.pid"
            script = ("import os,subprocess,sys,time; subprocess.Popen([sys.executable,'-c',"
                      "'import os,sys,time; p=sys.argv[1]; t=p+\".tmp\"; f=open(t,\"w\"); f.write(str(os.getpid())); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(t,p); time.sleep(10)',sys.argv[1]]); time.sleep(10)")
            with self.assertRaises(ProtocolError) as caught:
                provider._exec([python, "-c", script, str(pidfile)], timeout_ms=100, max_output=1024)
            self.assertEqual(caught.exception.code, "TIMEOUT")
            pid = None
            for _ in range(50):
                try:
                    candidate = int(pidfile.read_text().strip())
                    if candidate > 1: pid = candidate; break
                except (FileNotFoundError, ValueError):
                    time.sleep(.01)
            if pid is not None:
                for _ in range(50):
                    try: os.kill(pid, 0)
                    except ProcessLookupError: break
                    __import__("time").sleep(.02)
                else: self.fail("surviving descendant after Herdr timeout")
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / "orphan.pid"
            script = ("import subprocess,sys; subprocess.Popen([sys.executable,'-c',"
                      "'import os,sys,time; os.close(1); os.close(2); p=sys.argv[1]; t=p+\".tmp\"; f=open(t,\"w\"); f.write(str(os.getpid())); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(t,p); time.sleep(10)',sys.argv[1]])")
            with self.assertRaises(ProtocolError) as caught:
                provider._exec([python, "-c", script, str(pidfile)], timeout_ms=1000, max_output=1024)
            self.assertEqual(caught.exception.code, "CLEANUP_UNKNOWN")
            pid = None
            for _ in range(50):
                try:
                    candidate = int(pidfile.read_text().strip())
                    if candidate > 1: pid = candidate; break
                except (FileNotFoundError, ValueError):
                    time.sleep(.01)
            if pid is not None:
                for _ in range(50):
                    try: os.kill(pid, 0)
                    except ProcessLookupError: break
                    time.sleep(.02)
                else: self.fail("surviving descendant after Herdr leader exit")
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "herdr"; link.symlink_to(Path(python))
            with self.assertRaises(ProtocolError) as caught:
                HerdrProvider(herdr=str(link), pi=sys.executable)._exec([str(link), "--help"], timeout_ms=1000)
            self.assertEqual(caught.exception.code, "PROVIDER_UNAVAILABLE")

    def test_cli_unavailable_has_no_attempt_or_root_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve(); request, _, _, private, worktree, output, session = request_fixture(base)
            ledger = AttemptLedger(base / "attempts.jsonl")
            provider = HerdrProvider(herdr="razr-definitely-missing-herdr", pi=sys.executable, ledger=ledger, private_root=private)
            with self.assertRaises(ProtocolError) as caught: provider.launch(request)
            self.assertEqual(caught.exception.code, "PROVIDER_UNAVAILABLE")
            self.assertFalse(ledger.path.exists()); self.assertFalse((private / "root-claims.jsonl").exists())
            self.assertFalse((worktree / ".razr").exists()); self.assertEqual(list(output.iterdir()), []); self.assertEqual(list(session.iterdir()), [])

    def test_request_rejects_runtime_fallback_and_root_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = (Path(tmp) / "work").resolve(); root.mkdir()
            out = (Path(tmp) / "out").resolve(); out.mkdir()
            session = (Path(tmp) / "session").resolve(); session.mkdir()
            base = {"schema_version": 1, "request_id": "exec-1", "attempt_id": "att-1", "role": "technical",
                    "host": execution_host(),
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
