"""Public adoption integration and shared owned synthetic fixture mechanics."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from check_public_tree import check, require
from run_public import ROOT, PACKAGE, isolate, save, sha


def surface(root):
    """Snapshot even intentionally invalid links, without following them."""
    rows = {}
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            p = Path(base) / name
            s = p.lstat()
            rows[p.relative_to(root).as_posix()] = (
                stat.S_IMODE(s.st_mode), os.readlink(p) if p.is_symlink()
                else sha(p.read_bytes()) if p.is_file() else 'directory')
    return rows


class FixtureTest(unittest.TestCase):
    """No test methods: imports do not duplicate test cases in other modules."""
    def setUp(self):
        base = Path(os.environ['PUBLIC_TEST_ROOT'])
        require(base.is_relative_to(ROOT / '.test-output') and base.is_dir()
                and all(not p.is_symlink() for p in (base, *base.parents)), 'owned worker root required')
        self.lab = Path(tempfile.mkdtemp(prefix='case-', dir=base))
        self.tools = {'python3': Path(sys.executable).resolve(),
                      'git': Path(shutil.which('git')).resolve(), 'node': Path(shutil.which('node')).resolve()}
        self.env = isolate(self.lab, self.tools)
        self.commands = self.lab / 'commands'
        self.commands.mkdir()
        self.sequence = 0

    def invoke(self, argv, cwd=None, expected=0):
        self.sequence += 1
        proc = subprocess.run(argv, cwd=cwd or self.lab, env=self.env, capture_output=True, text=True, timeout=60)
        save(self.commands / f'{self.sequence:03}.json', {
            'argv': [str(x) for x in argv], 'cwd': str(cwd or self.lab),
            'exit': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr})
        self.assertEqual(proc.returncode, expected, proc.stdout + proc.stderr)
        if expected == 0:
            self.assertEqual(proc.stderr, '')
        return proc

    def repo(self, name='project'):
        root = self.lab / name
        root.mkdir()
        self.invoke([self.tools['git'], 'init', '-q', '-b', 'main', str(root)])
        return root

    def helper(self, name, *args, package=PACKAGE, cwd=None, expected=0):
        return self.invoke([sys.executable, '-I', '-B', package / 'scripts' / name, *args], cwd, expected)

    def core(self, root, *args, package=PACKAGE, expected=0):
        return self.invoke([sys.executable, '-I', '-B', package / 'runtime/scripts/razr.py', *args], root, expected)

    def adopt(self, root, work_root='.razr/work', package=PACKAGE, expected=0):
        return self.helper('adopt.py', '--target', str(root), '--work-root', work_root,
                           package=package, expected=expected)

    def public_tree(self, name='public tree'):
        tree = self.lab / name
        tree.mkdir()
        for relative in check(ROOT)['source_hashes']:
            target = tree / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as f:
                f.write((ROOT / relative).read_bytes())
            target.chmod(0o644)
        return tree

    def pair(self, name='pair'):
        pair = self.lab / name
        pair.mkdir()
        for filename in ('spec.md', 'executable.yaml'):
            (pair / filename).write_bytes((ROOT / 'tests/fixtures/public-contract' / filename).read_bytes())
        return pair

    def approved_item(self):
        """Explicitly synthetic metadata; materialized exclusively by real core."""
        root = self.repo()
        self.adopt(root)
        self.core(root, 'idea', 'public-probe', '--title', 'Synthetic public probe', '--depth', 'tiny')
        item = root / '.razr/work/public-probe'
        fixture = ROOT / 'tests/fixtures/public-contract'
        text = (fixture / 'spec.md').read_text()
        text = text.replace('condition: draft\n', 'condition: approved\n')
        text = text.replace('approved_by: null\n', 'approved_by: SYNTHETIC-test-only\n')
        text = text.replace('approved_at: null\n', 'approved_at: 2000-01-01T00:00:00+00:00\n')
        text = text.replace('approval_ref: null\n', 'approval_ref: synthetic-fixture-not-human-approval\n')
        (item / 'spec.md').write_text(text)
        exe = json.loads((fixture / 'executable.yaml').read_bytes())
        exe['source_spec']['digest'] = 'sha256:' + sha((item / 'spec.md').read_bytes())
        (item / 'executable.yaml').write_text(json.dumps(exe, indent=2) + '\n')
        self.core(root, 'approve', 'public-probe')
        self.core(root, 'validate', 'public-probe')
        return root, item


class AdoptionTests(FixtureTest):
    def test_adoption_idempotence_preserves_instructions_and_conflicts(self):
        root = self.repo()
        original = '# Public synthetic project instructions\n\nKeep this text.\n'
        (root / 'AGENTS.md').write_text(original)
        self.adopt(root)
        self.assertTrue((root / 'AGENTS.md').read_text().startswith(original))
        before = surface(root)
        self.adopt(root)
        self.assertEqual(surface(root), before)
        refused = self.adopt(root, '.other/work', expected=2)
        self.assertIn('refusing silent change', refused.stderr)
        self.assertEqual(surface(root), before)
        self.assertFalse((root / '.git/index').exists())

    def test_discovery_duplicate_protection_and_project_separation(self):
        a, b = self.repo('a'), self.repo('b')
        for root in (a, b):
            self.adopt(root)
        self.core(a, 'idea', 'public-one', '--title', 'Public one', '--depth', 'tiny')
        item = a / '.razr/work/public-one'
        before = (item / 'spec.md').read_bytes()
        refused = self.core(a, 'idea', 'public-one', '--title', 'Different', '--depth', 'tiny', expected=2)
        self.assertIn('already exists', refused.stderr)
        self.assertEqual(before, (item / 'spec.md').read_bytes())
        self.assertEqual(sorted(p.name for p in item.iterdir()), ['spec.md'])
        self.assertFalse((b / '.razr/work/public-one').exists())
        self.core(a, 'validate', '--all')
        self.assertEqual(json.loads(self.core(b, 'status', '--json').stdout), [])

    def test_relocated_package_and_custom_work_root(self):
        package = self.public_tree() / 'packages/razr-pi'
        before = surface(package)
        root = self.repo('path with spaces')
        self.adopt(root, 'work items', package)
        self.core(root, 'idea', 'relocated', '--title', 'Relocated', '--depth', 'tiny', package=package)
        self.core(root, 'validate', '--all', package=package)
        self.assertTrue((root / 'work items/relocated/spec.md').is_file())
        self.assertEqual(surface(package), before)
        self.assertFalse((package / 'razr.yaml').exists())

    def test_linked_and_nonregular_control_paths_refuse_without_changes(self):
        for kind in ('agents', 'dangling', 'config', 'work-root', 'directory-agents', 'directory-config'):
            with self.subTest(kind=kind):
                root = self.repo(kind)
                outside = self.lab / (kind + '-outside')
                outside.write_text('synthetic protected bytes\n')
                if kind == 'agents': (root / 'AGENTS.md').symlink_to(outside)
                elif kind == 'dangling': (root / 'AGENTS.md').symlink_to(self.lab / 'missing')
                elif kind == 'config': (root / 'razr.yaml').symlink_to(outside)
                elif kind == 'work-root':
                    folder = self.lab / 'outside-work'; folder.mkdir()
                    (root / '.razr').symlink_to(folder, target_is_directory=True)
                elif kind == 'directory-agents': (root / 'AGENTS.md').mkdir()
                else: (root / 'razr.yaml').mkdir()
                before, external = surface(root), outside.read_bytes()
                self.assertIn('refusing', self.adopt(root, expected=2).stderr)
                self.assertEqual(surface(root), before)
                self.assertEqual(outside.read_bytes(), external)

    def test_ambiguous_and_modified_routing_refuses(self):
        start, end = '<!-- RAZR:START -->', '<!-- RAZR:END -->'
        for index, text in enumerate((start, end+'\n'+start, (start+'\nx\n'+end+'\n')*2, start+'\nmodified\n'+end)):
            with self.subTest(case=index):
                root = self.repo('routing-' + str(index))
                (root / 'AGENTS.md').write_text(text)
                before = surface(root)
                self.assertIn('refusing', self.adopt(root, expected=2).stderr)
                self.assertEqual(surface(root), before)

    def test_non_git_subdirectory_and_unsafe_work_root_refuse(self):
        root = self.repo()
        child = root / 'child'; child.mkdir()
        before = surface(root)
        self.assertIn('exact Git root', self.adopt(child, expected=2).stderr)
        self.assertEqual(surface(root), before)
        independent = self.lab / 'not-git'; independent.mkdir()
        self.assertIn('not a Git repository', self.adopt(independent, expected=2).stderr)
        self.assertEqual(list(independent.iterdir()), [])
        for work_root in ('../escape', str(self.lab / 'absolute-work')):
            with self.subTest(work_root=work_root):
                self.assertIn('relative path', self.adopt(root, work_root, expected=2).stderr)
                self.assertEqual(surface(root), before)
