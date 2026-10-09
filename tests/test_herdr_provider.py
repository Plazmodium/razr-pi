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
from adapter.herdr_provider import AttemptLedger, HerdrProvider, ProviderRequest, ProtocolError
from adapter.protocol import canonical_json, raw_file_digest


def execution_host(*, workspace="w", tab="t", pane="p"):
    return {"mode": "herdr", "caller": {"workspace_id": "w", "tab_id": "t", "pane_id": "p"},
            "target": {"workspace_id": workspace, "tab_id": tab, "pane_id": pane}}


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
            with patch.object(rejecting, "probe", return_value=probe), \
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
            with patch("adapter.herdr_provider.fcntl.flock", side_effect=OSError("lock")):
                with self.assertRaises(ProtocolError) as lock_error: ledger.append({**started, "attempt_id": "lock-fail", "allowance_id": "lock-allow"})
            self.assertEqual(lock_error.exception.code, "LOCK_UNAVAILABLE")
            original_bytes = path.read_bytes()
            with patch("adapter.herdr_provider.os.fsync", side_effect=[OSError("disk"), None]):
                with self.assertRaises(ProtocolError) as write_error: ledger.append({**started, "attempt_id": "write-fail", "allowance_id": "write-allow"})
            self.assertEqual(write_error.exception.code, "LOCK_UNAVAILABLE"); self.assertEqual(path.read_bytes(), original_bytes)
            with patch("adapter.herdr_provider.os.fsync", side_effect=[OSError("disk"), OSError("rollback"), OSError("close")]):
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
            open_calls = []; list_calls = [0]
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
            with patch("adapter.herdr_provider.verify_candidate_identity"):
                response = provider.launch(request)
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
            cleanup_result = collected.cleanup(handle)
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
            root = Path(tmp); session = root / "session"; session.mkdir()
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
            root = Path(tmp); session = root / "session"; session.mkdir(); evidence = root / ".razr" / "evidence"; evidence.mkdir(parents=True)
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
