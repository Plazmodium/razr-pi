"""Run the no-model tracer and selected self-contained public regressions.

No credentials, global configuration, staging, commits or model invocation.
Owned results are private. This runner is not an adversarial OS sandbox.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import unittest
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

# Explicit inspected test directory, including when launched with Python -I.
# Never depend on the caller's cwd or PYTHONPATH for public test imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_public_tree import check, regular, require, OUTPUT_MARKER

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'packages/razr-pi'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def snapshot(root):
    result = {}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(), 'linked fixture surface')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {
                'sha256': sha(path.read_bytes()), 'mode': stat.S_IMODE(path.stat().st_mode)}
        else:
            require(path.is_dir(), 'non-regular fixture surface')
    return result


def installed_tools():
    require(os.name == 'posix' and sys.version_info >= (3, 11), 'POSIX and Python 3.11+ required')
    tools = {'python3': Path(sys.executable).resolve()}
    for name in ('git', 'node', 'pi'):
        found = shutil.which(name)
        require(found is not None, 'missing prerequisite: ' + name + '; do not install implicitly')
        tools[name] = Path(found).resolve(strict=True)
    # Inspect only the installed program's manifest, never user settings/auth.
    pi_root = None
    for parent in tools['pi'].parents:
        manifest = parent / 'package.json'
        if manifest.is_file():
            metadata = json.loads(manifest.read_bytes())
            if metadata.get('name', '').endswith('/pi-coding-agent'):
                pi_root = parent
                break
    require(pi_root is not None and (pi_root / 'dist/index.js').is_file(),
            'installed Pi package loader unavailable; no version fallback or install')
    return tools, pi_root


def isolate(run, tools):
    for name in ('home', 'xdg', 'agent', 'tmp', 'bin', 'empty-git-template'):
        (run / name).mkdir()
    # The only intentional links are reviewed executable aliases, not source/fixtures.
    for name in ('python3', 'git', 'node'):
        (run / 'bin' / name).symlink_to(tools[name])
    return {
        'PATH': str(run / 'bin'), 'HOME': str(run / 'home'),
        'XDG_CONFIG_HOME': str(run / 'xdg'), 'XDG_CACHE_HOME': str(run / 'xdg'),
        'XDG_DATA_HOME': str(run / 'xdg'), 'TMPDIR': str(run / 'tmp'), 'TMP': str(run / 'tmp'),
        'TEMP': str(run / 'tmp'), 'LANG': 'C', 'LC_ALL': 'C', 'TZ': 'UTC',
        'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
        'PI_OFFLINE': '1', 'PI_CODING_AGENT_DIR': str(run / 'agent'),
        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_SYSTEM': os.devnull,
        'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0',
        'GIT_OPTIONAL_LOCKS': '0', 'GIT_TEMPLATE_DIR': str(run / 'empty-git-template'),
        'GIT_CEILING_DIRECTORIES': str(run),
    }


TEST_MODULES = ('test_adoption', 'test_clean_package', 'test_contract_preflight',
                'test_primary_execution', 'test_provenance', 'test_public_boundary', 'test_named_regressions')


def regression_worker(source_before):
    """Only entered in a new isolated child; no dependency on private checkpoint records."""
    run = Path(os.environ['PUBLIC_WORKER_ROOT'])
    require(run.is_relative_to(ROOT / '.test-output') and run.is_dir()
            and all(not p.is_symlink() for p in (run, *run.parents)), 'owned isolated worker root required')
    require(os.environ['HOME'] == str(run / 'home') and os.environ['PI_OFFLINE'] == '1', 'worker isolation missing')
    require(Path(os.environ['PUBLIC_TEST_ROOT']) == run / 'fixtures', 'wrong fixture root')
    require(not any(name.endswith(('_API_KEY', '_TOKEN')) or name in ('NODE_OPTIONS', 'PYTHONPATH')
                    for name in os.environ), 'unexpected credential/injection environment')
    spec = importlib.util.spec_from_file_location('public_named_collector', PACKAGE / 'scripts/named-regressions.py')
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    with (run / 'stdout.log').open('x') as stdout, (run / 'stderr.log').open('x') as stderr:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            suite = unittest.defaultTestLoader.loadTestsFromNames(TEST_MODULES)
            planned = suite.countTestCases()
            result = unittest.TextTestRunner(stream=stderr, verbosity=2, failfast=True,
                                            resultclass=collector.NamedResult).run(suite)
    source_after = check(ROOT)
    coverage = {}
    for module in TEST_MODULES:
        observations = [r for r in result.observations if r['id'].startswith(module + '.')]
        coverage[module] = {'observed_ids': sorted({r['id'] for r in observations}),
                            'satisfied': bool(observations) and all(r['outcome'] == 'pass' for r in observations)}
    passed = (planned > 0 and result.start_count == planned and result.wasSuccessful()
              and not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses
              and all(v['satisfied'] for v in coverage.values()) and source_after == source_before)
    report = {'status': 'PASS' if passed else 'FAIL', 'kind': 'public-no-model-regressions',
              'source_manifest_sha256': source_before['source_manifest_sha256'],
              'planned_tests': planned, 'tests_started': result.start_count,
              'subcases': sum(r['kind'] == 'subcase' for r in result.observations),
              'observations': result.observations, 'sources': result.sources, 'coverage': coverage,
              'source_before': source_before, 'source_after': source_after,
              'raw_logs': {name: sha((run / name).read_bytes()) for name in ('stdout.log', 'stderr.log')},
              'limitations': 'Selected reviewed public test modules only; synthetic approvals, no clone/critic/factory proof.'}
    save(run / 'regressions.json', report)
    print(json.dumps({'status': report['status'], 'tests': result.start_count, 'subcases': report['subcases'],
                      'source_manifest_sha256': source_before['source_manifest_sha256']}))
    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('tracer', 'full'), required=True)
    parser.add_argument('--require-pi', action='store_true', required=True)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    # Validate the complete source/fixture boundary before any project/loader import or subprocess.
    source_before = check(ROOT)
    if args.worker:
        require(args.phase == 'full', 'worker is only for full regressions')
        return regression_worker(source_before)
    tools, pi_root = installed_tools()
    out = ROOT / '.test-output'
    if not out.exists():
        out.mkdir()
        with (out / '.owner').open('x') as stream:
            stream.write(OUTPUT_MARKER)
    require(not out.is_symlink() and not (out / '.owner').is_symlink()
            and (out / '.owner').read_text() == OUTPUT_MARKER, 'unowned output root')
    run = Path(tempfile.mkdtemp(prefix=args.phase + '-', dir=out))
    save(run / 'source-before.json', source_before)
    commands = []

    def command(label, argv, cwd, env, timeout=90):
        started = datetime.now(timezone.utc).isoformat()
        try:
            result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            save(run / f'{len(commands):02}-{label}-timeout.json', {'label': label, 'timeout_seconds': timeout})
            raise RuntimeError('subprocess timeout: ' + label) from exc
        record = {'label': label, 'argv': [str(a) for a in argv], 'cwd': str(cwd),
                  'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
                  'exit': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
        commands.append(record)
        save(run / f'{len(commands):02}-{label}.json', record)
        require(len(result.stdout.encode()) + len(result.stderr.encode()) <= 2 * 1024 * 1024, 'oversized result: ' + label)
        require(result.returncode == 0, 'subprocess failed: ' + label + '; retained original result, no retry')
        require(not result.stderr, 'unexpected stderr: ' + label + '; retained original result, no retry')
        return result.stdout

    try:
        env = isolate(run, tools)
        # Record names only, never inspect/export the ambient environment.
        save(run / 'environment-policy.json', {'child_keys': sorted(env), 'ambient_values_copied': False,
                                               'credential_inspection': False, 'model_invocations': 0})
        relocated = run / 'relocated package'
        relocated.mkdir()
        prefix = 'packages/razr-pi/'
        for name in source_before['source_hashes']:
            if name.startswith(prefix):
                target = relocated / name.removeprefix(prefix)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as stream:
                    stream.write(regular(ROOT / name))
                target.chmod(0o644)
        original_package = snapshot(PACKAGE)
        require(snapshot(relocated) == original_package, 'relocated package differs')
        project = run / 'clean project'
        project.mkdir()
        command('git-init', [tools['git'], 'init', '-q', '-b', 'main', str(project)], run, env)
        initial_project = snapshot(project)
        loader_before = {k: snapshot(run / k) for k in ('home', 'agent', 'xdg')}
        loader = json.loads(command('package-resolver', [tools['node'], ROOT / 'tests/clean_package_preflight.mjs',
                                                        pi_root, run], project, env))
        require(loader['status'] == 'PASS' and loader['adopted'] is False and loader['unchanged'] is True,
                'loader did not prove read-only package resolution')
        require(loader['loaded'] == {'skills': 1, 'extensions': 0, 'prompts': 0, 'themes': 0, 'contextFiles': 0},
                'unexpected loaded resources')
        require(loader['networkAttempts'] == loader['modelInvocations'] == 0, 'unexpected external/model activity')
        require(snapshot(project) == initial_project, 'package loading adopted or changed project')
        require(snapshot(relocated) == original_package, 'package loading changed package')
        require({k: snapshot(run / k) for k in loader_before} == loader_before, 'loader changed isolated configuration')
        require(not (project / 'razr.yaml').exists() and not (project / '.razr').exists()
                and not (project / 'AGENTS.md').exists(), 'implicit adoption')
        core = relocated / 'runtime/scripts/razr.py'
        cli = [tools['python3'], '-I', '-B', core]
        command('explicit-adoption', [tools['python3'], '-I', '-B', relocated / 'scripts/adopt.py',
                                      '--target', str(project), '--work-root', '.razr/work'], project, env)
        config = json.loads((project / 'razr.yaml').read_bytes())
        require(config['work_root'] == '.razr/work', 'unexpected adopted work root')
        agents = (project / 'AGENTS.md').read_text()
        routing = (relocated / 'runtime/AGENTS.project-razr.md').read_text().strip()
        require(agents.count('<!-- RAZR:START -->') == agents.count('<!-- RAZR:END -->') == 1
                and routing in agents, 'adoption routing mismatch')
        require(json.loads(command('empty-status', cli + ['status', '--json'], project, env)) == [],
                'unexpected prior work item')
        command('empty-validation', cli + ['validate', '--all'], project, env)
        command('discovery', cli + ['idea', 'public-discovery', '--title', 'Public discovery tracer', '--depth', 'tiny'], project, env)
        status = json.loads(command('discovery-status', cli + ['status', '--json'], project, env))
        require(len(status) == 1 and status[0]['id'] == 'public-discovery'
                and status[0]['stage'] == 'spec' and status[0]['status'] == 'exploring'
                and status[0]['candidate'] is None and status[0]['pull_request'] is None,
                'unexpected discovery read-back')
        command('discovery-validation', cli + ['validate', '--all'], project, env)
        item = project / '.razr/work/public-discovery'
        require(sorted(p.name for p in item.iterdir()) == ['spec.md'], 'discovery must contain only spec.md')
        require(b'id: public-discovery\n' in (item / 'spec.md').read_bytes(), 'wrong discovery spec')
        require(snapshot(relocated) == original_package == snapshot(PACKAGE), 'package source mutation')
        require({k: snapshot(run / k) for k in loader_before} == loader_before, 'isolated configuration changed')
        source_after = check(ROOT)
        require(source_after == source_before, 'public source drift during tracer')
        suite_summary = None
        if args.phase == 'full':
            # Export only the 46 declared files, never .git, private output or workshop data.
            # This is a stripped materialized tree, not a commit or actual clone.
            stripped = run / 'public source tree'
            stripped.mkdir()
            for name in source_before['source_hashes']:
                target = stripped / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as stream:
                    stream.write(regular(ROOT / name))
                target.chmod(0o644)
            require(check(stripped) == source_before, 'stripped tree differs from source')
            private = stripped / '.test-output'; private.mkdir()
            (private / '.owner').write_text(OUTPUT_MARKER)
            worker = private / 'regressions'; worker.mkdir()
            (worker / 'fixtures').mkdir()
            child_env = isolate(worker, tools)
            child_env.update(PUBLIC_WORKER_ROOT=str(worker), PUBLIC_TEST_ROOT=str(worker / 'fixtures'),
                             PUBLIC_PI_ROOT=str(pi_root))
            suite_summary = json.loads(command('public-regressions', [tools['python3'], '-I', '-B',
                        stripped / 'tests/run_public.py', '--phase', 'full', '--require-pi', '--worker'],
                        stripped, child_env, timeout=600))
            require(suite_summary['status'] == 'PASS', 'public regressions incomplete')
            require(check(stripped) == source_before and check(ROOT) == source_before, 'post-regression source drift')
        final = {'status': 'PASS', 'kind': 'no-model-loading-adoption-discovery-and-public-proof',
                 'commands_observed': len(commands), 'source_manifest_sha256': source_after['source_manifest_sha256'],
                 'package_loader': {k: v for k, v in loader.items() if k not in ('before', 'after')},
                 'project_before_loading': initial_project, 'project_after': snapshot(project),
                 'discovery_status': status, 'package_unchanged': True, 'core_templates_matched': 6,
                 'full_suite_executed': args.phase == 'full', 'public_regressions': suite_summary,
                 'human_checkpoint': 'results require human review; execution does not authenticate approval',
                 'limits': ['not a post-import clone', 'selected public regressions, not excluded workshop suites',
                            'no live task, candidate, critics, recovery, factory or PR proof']}
        save(run / 'source-after.json', source_after)
        save(run / 'result.json', final)
        print(json.dumps({'status': final['status'], 'run': run.relative_to(ROOT).as_posix(),
                          'source_manifest_sha256': final['source_manifest_sha256'],
                          'commands_observed': len(commands), 'public_regressions': suite_summary,
                          'human_review': 'pending', 'full_suite_executed': args.phase == 'full'}, indent=2))
        return 0
    except Exception as exc:
        save(run / 'STOP.json', {'status': 'STOP', 'error': str(exc), 'commands_observed': len(commands),
                                'retry': False, 'human_checkpoint': 'not reached'})
        print('STOP: retained ' + run.relative_to(ROOT).as_posix() + '/STOP.json', file=sys.stderr)
        return 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print('STOP: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
