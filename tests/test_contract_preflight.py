"""Real unchanged-core preflight on newly authored public synthetic pairs."""
import json
from pathlib import Path

from test_adoption import FixtureTest, surface
from run_public import ROOT, sha

REQUIRED_SECTIONS = ('Intent', 'Requirements', 'Constraints', 'Non-goals',
                     'Assumptions', 'Proof obligations', 'Approval')


class ContractPreflightTests(FixtureTest):
    def fixture_surface(self):
        # Only the fixture harness's original command records may be added.
        return {name: value for name, value in surface(self.lab).items()
                if name != 'commands' and not name.startswith('commands/')}

    def inspect_pair(self, pair, expected=0):
        before = self.fixture_surface()
        result = self.helper('contract-preflight.py', '--pair', str(pair), expected=expected)
        self.assertEqual(self.fixture_surface(), before)
        self.assertFalse((pair / 'state.yaml').exists())
        self.assertFalse((pair / 'evidence.md').exists())
        if not result.stdout:
            return result.stderr
        self.assertEqual(result.stderr, '')
        report = json.loads(result.stdout)
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual(report['structural_preflight'], 'PASS' if expected == 0 else 'FAIL')
        self.assertIn('Not approval', report['authority'])
        executable = json.loads((pair / 'executable.yaml').read_bytes())
        # Independently reproduce the unchanged core's canonical JSON identity.
        canonical = json.dumps(executable, sort_keys=True, separators=(',', ':')).encode('utf-8')
        self.assertEqual(report['identities'], {
            'spec_file_sha256': 'sha256:' + sha((pair / 'spec.md').read_bytes()),
            'executable_file_sha256': 'sha256:' + sha((pair / 'executable.yaml').read_bytes()),
            'executable_core_digest': 'sha256:' + sha(canonical),
        })
        return report

    def rewrite_headings(self, pair, replacements):
        spec = pair / 'spec.md'
        text = spec.read_text()
        for section, heading in replacements.items():
            original = '## ' + section + '\n'
            self.assertEqual(text.count(original), 1)
            text = text.replace(original, heading)
        spec.write_text(text)
        path = pair / 'executable.yaml'
        executable = json.loads(path.read_bytes())
        executable['source_spec']['digest'] = 'sha256:' + sha(spec.read_bytes())
        path.write_text(json.dumps(executable, indent=2) + '\n')

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

    def test_missing_original_three_sections_refuse(self):
        pair = self.pair()
        self.rewrite_headings(pair, {section: '' for section in ('Intent', 'Assumptions', 'Approval')})
        result = self.inspect_pair(pair, expected=1)
        self.assertEqual(result['structural_preflight'], 'FAIL')
        self.assertEqual(result['errors'], ['missing required spec sections: Intent, Assumptions, Approval'])

    def test_each_required_section_and_all_sections_refuse_in_core_order(self):
        cases = [(section,) for section in REQUIRED_SECTIONS] + [REQUIRED_SECTIONS]
        for index, sections in enumerate(cases):
            with self.subTest(missing=sections):
                pair = self.pair('missing-' + str(index))
                self.rewrite_headings(pair, {section: '' for section in sections})
                result = self.inspect_pair(pair, expected=1)
                self.assertEqual(result['errors'], ['missing required spec sections: ' + ', '.join(sections)])

    def test_wrong_heading_case_or_level_is_not_a_required_section(self):
        for name, heading in (('case', '## intent\n'), ('level-one', '# Intent\n'),
                              ('level-three', '### Intent\n')):
            with self.subTest(format=name):
                pair = self.pair(name)
                self.rewrite_headings(pair, {'Intent': heading})
                self.assertEqual(self.inspect_pair(pair, expected=1)['errors'],
                                 ['missing required spec sections: Intent'])

    def test_core_heading_whitespace_and_optional_context_remain_valid(self):
        cases = {'whitespace': {section: '##\t' + section + ' \t\n' for section in REQUIRED_SECTIONS},
                 'optional-context': {'Context and Evidence': ''}}
        for name, replacements in cases.items():
            with self.subTest(format=name):
                pair = self.pair(name)
                self.rewrite_headings(pair, replacements)
                self.assertEqual(self.inspect_pair(pair)['errors'], [])
                self.assertIn('condition: draft\n', (pair / 'spec.md').read_text())

    def test_section_diagnostic_preserves_executable_and_authority_errors(self):
        pair = self.pair()
        self.rewrite_headings(pair, {'Intent': ''})
        self.rewrite_constraint(pair, 'Never hand-edit state and evidence.')
        path = pair / 'executable.yaml'
        executable = json.loads(path.read_bytes())
        executable['critique']['contract'] = 'optional'
        path.write_text(json.dumps(executable, indent=2) + '\n')
        errors = self.inspect_pair(pair, expected=1)['errors']
        self.assertIn('missing required spec sections: Intent', errors)
        self.assertTrue(any('separate technical and contract' in error for error in errors))
        self.assertTrue(any('Evidence authority conflict' in error for error in errors))
        self.assertEqual(len(errors), 3)

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
