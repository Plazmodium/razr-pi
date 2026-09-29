"""Collector assertions only; nested failures/skips are deliberate synthetic probes."""
import importlib.util
import io
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'packages/razr-pi/scripts/named-regressions.py'
spec = importlib.util.spec_from_file_location('named_regressions', PATH)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def execute(case):
    return unittest.TextTestRunner(stream=io.StringIO(), resultclass=collector.NamedResult).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(case))


class CollectorTests(unittest.TestCase):
    def test_subcases_record_actual_outcomes(self):
        class Probe(unittest.TestCase):
            def test_cases(self):
                for case in ('pass', 'failure', 'error', 'skip'):
                    with self.subTest(case=case):
                        if case == 'failure':
                            self.fail('synthetic failure')
                        elif case == 'error':
                            raise ValueError('synthetic error')
                        elif case == 'skip':
                            self.skipTest('synthetic skip')
        result = execute(Probe)
        self.assertEqual(result.start_count, 1)
        self.assertEqual([r['outcome'] for r in result.observations], ['pass', 'failure', 'error', 'skip'])
        self.assertTrue(all(r['kind'] == 'subcase' and r['parent_id'] for r in result.observations))
        self.assertEqual(len({r['id'] for r in result.observations}), 4)
        self.assertTrue(all(r['started_at'] and r['source'] in result.sources for r in result.observations))

    def test_all_test_outcome_hooks(self):
        class Probe(unittest.TestCase):
            def test_pass(self):
                pass
            def test_failure(self):
                self.fail('synthetic')
            def test_error(self):
                raise ValueError('synthetic')
            @unittest.skip('synthetic')
            def test_skip(self):
                pass
            @unittest.expectedFailure
            def test_expected(self):
                self.fail('synthetic')
            @unittest.expectedFailure
            def test_unexpected(self):
                pass
        result = execute(Probe)
        self.assertEqual(result.start_count, 6)
        self.assertEqual({r['outcome'] for r in result.observations},
                         {'pass', 'failure', 'error', 'skip', 'expected-failure', 'unexpected-success'})

    def test_fixture_error_not_lost(self):
        class Probe(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise ValueError('synthetic fixture failure')
            def test_unexecuted(self):
                pass
        result = execute(Probe)
        self.assertEqual(result.start_count, 0)
        self.assertEqual([r['outcome'] for r in result.observations], ['error'])

    def test_required_coverage_uses_observed_ids_and_assertions(self):
        class Probe(unittest.TestCase):
            def test_cases(self):
                with self.subTest(case='observed'):
                    self.assertEqual(2 + 2, 4)
        result = execute(Probe)
        observed = result.observations[0]['id']
        mapping = {'arithmetic': [{'id': observed, 'assertion': '2 + 2 equals 4'}]}
        self.assertTrue(collector.coverage_results(mapping, result.observations, result.sources)['arithmetic']['satisfied'])
        for outcome in ('failure', 'error', 'skip', 'expected-failure', 'unexpected-success'):
            with self.subTest(outcome=outcome):
                altered = [{**r, 'outcome': outcome} for r in result.observations]
                self.assertFalse(collector.coverage_results(mapping, altered, result.sources)['arithmetic']['satisfied'])
        for rows, sources in (([], result.sources), (result.observations, {})):
            with self.subTest(rows=bool(rows), sources=bool(sources)):
                self.assertFalse(collector.coverage_results(mapping, rows, sources)['arithmetic']['satisfied'])
        for invalid in ({}, {'arithmetic': []}, {'arithmetic': [{'id': observed}]}, {'arithmetic': [{'id': observed, 'assertion': ''}]}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    collector.coverage_results(invalid, result.observations, result.sources)

    def test_nonpassing_child_cannot_hide_behind_parent_pass(self):
        # Synthetic observations test defense in depth; unittest itself omits parent
        # success after a failed subcase.
        rows = [dict(id='parent', parent_id=None, outcome='pass', source='source'),
                dict(id='child', parent_id='parent', outcome='skip', source='source')]
        mapping = {'obligation': [{'id': 'parent', 'assertion': 'all subcases pass'}]}
        self.assertFalse(collector.coverage_results(mapping, rows, {'source': {}})['obligation']['satisfied'])


if __name__ == '__main__':
    unittest.main()
