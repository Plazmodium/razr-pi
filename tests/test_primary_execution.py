"""Read-only inspection of core-materialized, explicitly synthetic work."""
import json

from test_adoption import FixtureTest, surface
from run_public import ROOT, sha


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
