#!/usr/bin/env python3
"""Retain actual unittest observations; never infer required coverage from totals.

No model calls or workflow mutations. Tests retain their own fixture authority.
Coverage is an explicit reviewed map, not automatically inferred from test names.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import sys
import time
import unittest


def digest(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


class NamedResult(unittest.TextTestResult):
    """Hooks record callbacks, including nonpassing subcases and fixture errors."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.observations = []
        self.sources = {}
        self.starts = {}
        self.start_count = 0

    def startTest(self, test):
        self.start_count += 1
        self.starts[id(test)] = (now(), time.monotonic())
        self.pin_source(test)
        super().startTest(test)

    def pin_source(self, owner):
        try:
            path = Path(inspect.getfile(type(owner))).resolve()
            content = path.read_bytes()
            source = str(path)
            self.sources.setdefault(source, {'digest': digest(content), 'content': content.decode()})
            return source
        except (TypeError, OSError, UnicodeError):
            return None  # Fixture/import errors remain nonpassing observations.

    def observe(self, test, outcome, parent=None):
        owner = parent or getattr(test, 'test_case', test)
        started, tick = self.starts.get(id(owner), (None, None))
        source = self.pin_source(owner)
        self.observations.append({
            'id': test.id(), 'parent_id': parent.id() if parent else None,
            'kind': 'subcase' if isinstance(test, unittest.case._SubTest) else 'test',
            'outcome': outcome, 'started_at': started, 'finished_at': now(),
            'elapsed_seconds': time.monotonic() - tick if tick is not None else None,
            'source': source,
        })

    def addSuccess(self, test):
        self.observe(test, 'pass')
        super().addSuccess(test)

    def addFailure(self, test, err):
        self.observe(test, 'failure')
        super().addFailure(test, err)

    def addError(self, test, err):
        self.observe(test, 'error')
        super().addError(test, err)

    def addSkip(self, test, reason):
        self.observe(test, 'skip', getattr(test, 'test_case', None))
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self.observe(test, 'expected-failure')
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self.observe(test, 'unexpected-success')
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):
        outcome = 'pass' if err is None else ('failure' if issubclass(err[0], test.failureException) else 'error')
        self.observe(subtest, outcome, test)
        super().addSubTest(test, subtest, err)


def coverage_results(mapping, observations, sources):
    """Every mapped ID must have actual, exclusively passing, sourced observations.

    The caller must separately inspect whether assertions satisfy the obligation.
    A test with any nonpassing subcase cannot satisfy coverage via its parent ID.
    """
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError('nonempty reviewed coverage mapping required')
    results = {}
    for obligation, cases in mapping.items():
        if not isinstance(obligation, str) or not isinstance(cases, list) or not cases:
            raise ValueError('each obligation needs concrete cases')
        checked = []
        for case in cases:
            if (not isinstance(case, dict) or set(case) != {'id', 'assertion'}
                    or not all(isinstance(v, str) and v.strip() for v in case.values())):
                raise ValueError('case requires exact ID and readable assertion')
            rows = [r for r in observations if r['id'] == case['id']]
            children = [r for r in observations if r['parent_id'] == case['id']]
            passed = bool(rows) and all(r['outcome'] == 'pass' and r['source'] in sources for r in rows + children)
            checked.append({**case, 'observations': len(rows), 'satisfied': passed})
        results[obligation] = {'cases': checked, 'satisfied': all(c['satisfied'] for c in checked)}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tests', type=Path, required=True)
    parser.add_argument('--pattern', default='test_*.py')
    parser.add_argument('--output', type=Path, required=True, help='new retained directory')
    parser.add_argument('--coverage', type=Path, help='reviewed obligation to ID/assertion mapping')
    parser.add_argument('--source', type=Path, action='append', default=[], help='additional source/resource to pin')
    args = parser.parse_args()
    out = args.output.absolute()
    if any(p.is_symlink() for p in (out, *out.parents)):
        raise ValueError('linked output path')
    out.mkdir(parents=True, exist_ok=False)
    start, tick = now(), time.monotonic()
    sources = {}
    for path in [Path(__file__), *args.source, *([args.coverage] if args.coverage else [])]:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'expected regular source: {path}')
        content = path.read_bytes()
        sources[str(path.resolve())] = {'digest': digest(content), 'content': content.decode()}
    with (out / 'stdout.log').open('x') as stdout, (out / 'stderr.log').open('x') as stderr:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            suite = unittest.defaultTestLoader.discover(str(args.tests.resolve()), pattern=args.pattern)
            result = unittest.TextTestRunner(stream=stderr, verbosity=2, resultclass=NamedResult).run(suite)
    # Retain source bytes, not just hashes; compare against the filesystem before freezing.
    sources.update(result.sources)
    source_drift = [p for p, row in sources.items() if digest(Path(p).read_bytes()) != row['digest']]
    coverage = coverage_results(json.loads(args.coverage.read_bytes()), result.observations, sources) if args.coverage else {}
    coverage_ok = bool(coverage) and all(row['satisfied'] for row in coverage.values())
    exit_code = 0 if result.wasSuccessful() and not source_drift and (not args.coverage or coverage_ok) else 1
    manifest = {
        'schema_version': 1, 'kind': 'observed-unittest-regressions',
        'command': [sys.executable, *sys.argv], 'cwd': str(Path.cwd()),
        'started_at': start, 'finished_at': now(), 'elapsed_seconds': time.monotonic() - tick,
        'exit_code': exit_code, 'tests_started': result.start_count,
        'subcase_observations': sum(r['kind'] == 'subcase' for r in result.observations),
        'observations': result.observations, 'sources': sources, 'source_drift': source_drift,
        'raw_outputs': {name: digest((out / name).read_bytes()) for name in ('stdout.log', 'stderr.log')},
        'coverage': coverage, 'required_coverage_satisfied': coverage_ok,
        'limitations': 'Callbacks prove observed execution, not semantic coverage or live critic independence. No coverage map means coverage remains unverified.',
    }
    with (out / 'manifest.json').open('x') as f:
        json.dump(manifest, f, indent=2)
        f.write('\n')
    print(f'Retained {result.start_count} tests and {manifest["subcase_observations"]} subcases: {out}')
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
