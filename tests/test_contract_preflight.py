"""Real unchanged-core preflight on newly authored public synthetic pairs."""
import json
from pathlib import Path

from test_adoption import FixtureTest, surface
from run_public import ROOT, sha


class ContractPreflightTests(FixtureTest):
    def inspect_pair(self, pair, expected=0):
        before = surface(pair)
        result = self.helper('contract-preflight.py', '--pair', str(pair), expected=expected)
        self.assertEqual(surface(pair), before)
        return json.loads(result.stdout) if result.stdout else result.stderr

    def rewrite_constraint(self, pair, statement):
        exe = json.loads((pair / 'executable.yaml').read_bytes())
        row = next(r for r in exe['contract'] if r['id'] == 'CON-001')
        spec = pair / 'spec.md'
        original = spec.read_text()
        self.assertEqual(original.count(row['statement']), 1)
        spec.write_text(original.replace(row['statement'], statement))
        row['statement'] = statement
        exe['source_spec']['digest'] = 'sha256:' + sha(spec.read_bytes())
        (pair / 'executable.yaml').write_text(json.dumps(exe, indent=2) + '\n')

    def test_public_draft_pair_is_consistent_readonly_and_not_approval(self):
        pair = self.pair()
        result = self.inspect_pair(pair)
        self.assertEqual(result['structural_preflight'], 'PASS')
        self.assertEqual(result['errors'], [])
        self.assertIn('Not approval', result['authority'])
        self.assertIn('condition: draft', (pair / 'spec.md').read_text())
        self.assertFalse((pair / 'state.yaml').exists())

    def test_raw_and_canonical_identities_are_distinct(self):
        pair = self.pair()
        before = self.inspect_pair(pair)['identities']
        self.assertEqual(before['spec_file_sha256'], 'sha256:' + sha((pair / 'spec.md').read_bytes()))
        self.assertEqual(before['executable_file_sha256'], 'sha256:' + sha((pair / 'executable.yaml').read_bytes()))
        self.assertNotEqual(before['executable_file_sha256'], before['executable_core_digest'])
        path = pair / 'executable.yaml'
        path.write_text(json.dumps(json.loads(path.read_bytes()), indent=4) + '\n')
        after = self.inspect_pair(pair)['identities']
        self.assertNotEqual(before['executable_file_sha256'], after['executable_file_sha256'])
        self.assertEqual(before['executable_core_digest'], after['executable_core_digest'])

    def test_evidence_authority_conflict_refuses_without_weakening_core(self):
        pair = self.pair()
        self.rewrite_constraint(pair, 'Never hand-edit state and evidence.')
        result = self.inspect_pair(pair, expected=1)
        self.assertTrue(any('Evidence authority conflict' in e for e in result['errors']))
        self.rewrite_constraint(pair, 'Never hand-edit state.yaml. The primary appends observed proof to core-created evidence.md.')
        self.assertEqual(self.inspect_pair(pair)['structural_preflight'], 'PASS')

    def test_source_drift_and_missing_or_linked_pair_refuse(self):
        for case in ('drift', 'missing', 'linked'):
            with self.subTest(case=case):
                pair = self.pair(case)
                path = pair / 'spec.md'
                if case == 'drift': path.write_text(path.read_text() + '\nSynthetic drift.\n')
                elif case == 'missing': path.unlink()
                else:
                    target = self.lab / 'synthetic-spec.md'; target.write_bytes(path.read_bytes())
                    path.unlink(); path.symlink_to(target)
                result = self.inspect_pair(pair, expected=1)
                if case == 'drift': self.assertTrue(any('digest' in e for e in result['errors']))
                elif case == 'linked': self.assertIn('symlink', result)
                else: self.assertIn('STOP', result)

    def test_missing_required_gate_refuses(self):
        pair = self.pair()
        path = pair / 'executable.yaml'
        exe = json.loads(path.read_bytes()); exe['critique']['contract'] = 'optional'
        path.write_text(json.dumps(exe) + '\n')
        self.assertTrue(any('separate technical and contract' in e for e in self.inspect_pair(pair, 1)['errors']))
