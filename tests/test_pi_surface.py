"""Pi surface status readback and capability refusal checks."""
import json
import os
import subprocess
import sys

from test_adoption import FixtureTest, surface
from run_public import PACKAGE


class PiSurfaceTests(FixtureTest):
    def commit_adopted_root(self, root):
        self.invoke([self.tools["git"], "-C", root, "add", "AGENTS.md", "razr.yaml"], root)
        self.invoke([self.tools["git"], "-C", root, "-c", "user.name=fixture", "-c",
                     "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], root)

    def surface(self, root, *args, expected=0, env=None):
        command = [sys.executable, "-I", "-B", PACKAGE / "scripts/pi-surface.py", *args]
        merged = dict(self.env if env is None else {**self.env, **env})
        allowed_git = {"GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT"}
        for key in list(merged):
            if key.startswith("GIT_") and key not in allowed_git:
                merged.pop(key)
        result = subprocess.run(command, cwd=root, env=merged, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        if expected == 0:
            self.assertEqual(result.stderr, "")
        return result

    def test_status_uses_core_readback_projection_and_fanout_refusal(self):
        root, item = self.approved_item()
        self.commit_adopted_root(root)
        before = surface(root)
        result = json.loads(self.surface(root, "status", "public-probe").stdout)
        self.assertEqual(surface(root), before)
        self.assertEqual(set(result), {"schema_version", "work_id", "stage", "status", "generation",
                                       "node_id", "node_status", "node_attempt", "node_result_ref",
                                       "candidate", "status_row", "raw_state", "adapter", "fanout"})
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["adapter"]["routing"], "current")
        self.assertEqual(result["adapter"]["supported_through"], "candidate_binding")
        self.assertEqual(result["adapter"]["experimental_through"], "open_pr_handoff")
        self.assertEqual(result["adapter"]["delivery_status"], "acceptance_pending")
        self.assertEqual((result["work_id"], result["stage"], result["status"], result["generation"]),
                         ("public-probe", "build", "active", 1))
        self.assertEqual(result["node_id"], "assert-arithmetic")
        self.assertEqual(result["raw_state"]["path"], ".razr/work/public-probe/state.yaml")
        self.assertEqual(result["fanout"], {"availability": "unavailable", "code": "FANOUT_UNAVAILABLE"})
        self.assertTrue(all(value is None for value in result["candidate"].values()))

    def test_context_includes_typed_workflow_readback_when_requested(self):
        root, item = self.approved_item()
        self.commit_adopted_root(root)
        request = {"schema_version": 1, "request_id": "caller-replaced", "objective": "inspect active workflow",
                   "repository_root": ".", "work_id": "public-probe", "paths": [],
                   "include": {"workflow": True, "source": False, "tests": False, "config": False, "git": False, "memory": False},
                   "limits": {"max_files": 4, "max_bytes": 1000, "max_memory_records": 1}}
        before = surface(root)
        response = json.loads(self.surface(root, "context", json.dumps(request)).stdout)
        self.assertEqual(surface(root), before)
        workflow = [entry for entry in response["result"]["items"] if entry["kind"] == "workflow"]
        self.assertEqual(len(workflow), 1)
        self.assertEqual(workflow[0]["ref"], "public-probe")
        self.assertEqual(workflow[0]["authority"], "repository")
        self.assertEqual(workflow[0]["metadata"]["type"], "core_readback")

    def test_status_requires_materialized_work_and_refuses_unknown_or_malformed(self):
        root = self.repo("unadopted")
        before = surface(root)
        self.assertIn("status requires a work_id", self.surface(root, "status", expected=2).stderr)
        self.adopt(root)
        self.commit_adopted_root(root)
        before = surface(root)
        self.assertIn("status row count differs", self.surface(root, "status", "unknown-work", expected=2).stderr)
        self.assertIn("invalid core work", self.surface(root, "status", "../escape", expected=2).stderr)
        self.assertEqual(surface(root), before)

    def test_herdr_identity_refusal_comes_from_m04_provider(self):
        root = self.repo("herdr-identity")
        result = self.surface(root, "status", expected=2, env={"HERDR_ENV": "1"})
        self.assertIn("status requires a work_id", result.stderr)
        # The provider's public probe remains the direct identity assertion; no CLI is launched.
        sys.path.insert(0, str(PACKAGE))
        from adapter.herdr_provider import HerdrProvider
        from adapter.protocol import ProtocolError
        request = {"schema_version": 1, "request_id": "probe-test", "required_capabilities": [],
                   "runtime": {"provider": "openai-codex", "model": "openai-codex/gpt-5.6-luna", "thinking": "medium"}}
        old = os.environ.get("HERDR_ENV")
        os.environ["HERDR_ENV"] = "1"
        for key in ("HERDR_WORKSPACE_ID", "HERDR_TAB_ID", "HERDR_PANE_ID"):
            os.environ.pop(key, None)
        try:
            with self.assertRaises(ProtocolError) as caught:
                HerdrProvider().probe(request)
            self.assertEqual(caught.exception.code, "HERDR_IDENTITY_INCOMPLETE")
        finally:
            if old is None: os.environ.pop("HERDR_ENV", None)
            else: os.environ["HERDR_ENV"] = old
