"""Read-only inspection of core-materialized, explicitly synthetic work."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/razr-pi"))

from test_adoption import FixtureTest, surface
from run_public import ROOT, sha


class DeliveryIdentityTests(unittest.TestCase):
    def test_delivery_requires_exact_candidate_and_both_critic_commits_even_for_equal_digest(self):
        from runtime.scripts.razr import RazrError, require_exact_reviewed_head
        candidate = {"commit": "a" * 40, "source_digest": "sha256:" + "c" * 64}
        critics = {role: {"outcome": "PASS", "candidate": candidate["source_digest"], "reviewed_commit": "a" * 40}
                   for role in ("technical", "contract")}
        require_exact_reviewed_head(candidate, critics, "a" * 40)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                   "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                   "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
            subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
            (root / "razr.yaml").write_text(json.dumps({"schema_version": 1, "work_root": ".razr/work",
                "retention": "commit", "adapter": {"mode": "process-local", "max_active_work_items": 5,
                "queue_order": ["priority", "approved_at", "id"]}}) + "\n")
            (root / "source.txt").write_text("same product source\n"); (root / "AGENTS.md").write_text("first instructions\n")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True, env=env)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "candidate-a"], check=True, env=env)
            commit_a = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True, env=env).strip()
            (root / "AGENTS.md").write_text("changed excluded instructions\n")
            subprocess.run(["git", "-C", str(root), "add", "AGENTS.md"], check=True, env=env)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "candidate-b"], check=True, env=env)
            commit_b = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True, env=env).strip()
            from runtime.scripts.razr import source_tree_digest
            self.assertEqual(source_tree_digest(root, commit_a, ".razr/work"), source_tree_digest(root, commit_b, ".razr/work"))
            candidate["commit"] = commit_a
            critics["technical"]["reviewed_commit"] = commit_a; critics["contract"]["reviewed_commit"] = commit_a
            with self.assertRaises(RazrError):
                require_exact_reviewed_head(candidate, critics, commit_b)
        for role in ("technical", "contract"):
            changed = {name: dict(record) for name, record in critics.items()}
            changed[role]["reviewed_commit"] = "b" * 40
            with self.subTest(role=role), self.assertRaises(RazrError):
                require_exact_reviewed_head(candidate, changed, "a" * 40)


class PrimaryInspectionTests(FixtureTest):
    def inspect(self, root, expected=0):
        return self.helper('primary.py', '--target', str(root), '--work-id', 'public-probe', expected=expected)

    def test_synthetic_materialization_and_readonly_null_candidate(self):
        fixture = ROOT / 'tests/fixtures/public-contract'
        original = surface(fixture)
        root, item = self.approved_item()
        before = surface(root)
        report = json.loads(self.inspect(root).stdout)
        self.assertEqual(surface(root), before)
        self.assertEqual(surface(fixture), original)
        self.assertEqual((report['stage'], report['status'], report['generation']), ('build', 'active', 1))
        self.assertTrue(report['candidate_empty'])
        self.assertTrue(all(value is None for value in report['candidate'].values()))
        self.assertEqual(report['state']['nodes']['assert-arithmetic']['status'], 'pending')
        self.assertTrue(all(row['exit'] == 0 for row in report['core_checks']))
        self.assertIn('not approval', report['authority'])
        self.assertFalse((root / '.git/index').exists())
        self.assertIn('SYNTHETIC-test-only', (item / 'spec.md').read_text())

    def test_discovery_only_and_unknown_work_refuse(self):
        root = self.repo(); self.adopt(root)
        before = surface(root)
        self.assertIn('STOP', self.inspect(root, 1).stderr)
        self.assertEqual(surface(root), before)
        self.core(root, 'idea', 'public-probe', '--title', 'Synthetic discovery', '--depth', 'tiny')
        before = surface(root)
        self.assertIn('approved materialized work required', self.inspect(root, 1).stderr)
        self.assertEqual(surface(root), before)

    def test_linked_configuration_and_canonical_spec_refuse(self):
        root, item = self.approved_item()
        for path in (root / 'razr.yaml', item / 'spec.md'):
            with self.subTest(file=path.name):
                raw = path.read_bytes()
                outside = self.lab / ('protected-' + path.name); outside.write_bytes(raw)
                path.unlink(); path.symlink_to(outside)
                before = surface(root)
                self.assertIn('symlink', self.inspect(root, 1).stderr)
                self.assertEqual(surface(root), before)
                self.assertEqual(outside.read_bytes(), raw)
                path.unlink(); path.write_bytes(raw)

    def test_drift_and_malformed_config_refuse_without_state_edits(self):
        root, item = self.approved_item()
        raw_state = (item / 'state.yaml').read_bytes()
        spec = item / 'spec.md'; raw_spec = spec.read_bytes()
        spec.write_bytes(raw_spec + b'\nSynthetic contract drift.\n')
        before = surface(root)
        self.assertIn('STOP', self.inspect(root, 1).stderr)
        self.assertEqual(surface(root), before)
        spec.write_bytes(raw_spec)
        (root / 'razr.yaml').write_text('not-json\n')
        before = surface(root)
        self.assertIn('STOP', self.inspect(root, 1).stderr)
        self.assertEqual(surface(root), before)
        self.assertEqual((item / 'state.yaml').read_bytes(), raw_state)

    def test_exact_root_and_stale_generation_refuse(self):
        root, item = self.approved_item()
        child = root / 'child'; child.mkdir()
        self.assertIn('exact Git root', self.inspect(child, 1).stderr)
        # No hand-edited state or successful task transition. Locks are coordination data.
        canonical = {p.name: p.read_bytes() for p in item.iterdir() if p.name != '.state.lock'}
        refused = self.core(root, 'transition', 'public-probe', '--node', 'assert-arithmetic',
                            '--node-status', 'running', '--expect-generation', '0', expected=2)
        self.assertIn('generation', refused.stderr)
        self.assertEqual({p.name: p.read_bytes() for p in item.iterdir() if p.name != '.state.lock'}, canonical)
        report = json.loads(self.inspect(root).stdout)
        self.assertEqual(report['generation'], 1)
        self.assertEqual(report['state']['nodes']['assert-arithmetic']['status'], 'pending')
