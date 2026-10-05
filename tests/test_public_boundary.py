"""Owned synthetic mutations test denial policy; no real private content is used."""
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

from test_adoption import FixtureTest, surface
from check_public_tree import check, git_administration, OUTPUT_MARKER
from run_public import ROOT


class BoundaryTests(FixtureTest):
    def linked_tree(self):
        primary = self.repo('primary repository')
        tree = self.lab / 'linked source tree'
        # Genuine Git metadata without staging, commits or borrowing real history.
        self.invoke([self.tools['git'], '-C', primary, 'worktree', 'add', '-q',
                     '--orphan', '-b', 'synthetic-public', tree])
        for relative in check(ROOT)['source_hashes']:
            target = tree / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
            target.chmod(0o644)
        administrative = Path((tree / '.git').read_text()[8:].rstrip('\n'))
        self.assertFalse((primary / '.git/index').exists())
        self.assertEqual(self.invoke([self.tools['git'], '-C', tree, 'ls-files', '--stage']).stdout, '')
        self.assertEqual(self.invoke([self.tools['git'], '-C', tree, 'rev-parse', '--verify', '--quiet', 'HEAD'],
                                     expected=1).stdout, '')
        return primary, tree, administrative

    def unchanged_refusal(self, tree):
        before = surface(self.lab)
        with self.assertRaises((ValueError, OSError)):
            check(tree)
        self.assertEqual(surface(self.lab), before)

    def test_normal_and_registered_linked_git_sources(self):
        normal = self.public_tree('normal source')
        self.invoke([self.tools['git'], 'init', '-q', '-b', 'main', normal])
        primary, linked, administrative = self.linked_tree()
        expected = check(ROOT)['source_manifest_sha256']
        for tree in (normal, linked):
            with self.subTest(kind=tree.name):
                before = surface(self.lab)
                self.assertEqual(check(tree)['source_manifest_sha256'], expected)
                # Ambient Git overrides must neither redirect identity nor write.
                with patch.dict(os.environ, {'GIT_DIR': str(primary / '.git'),
                                              'GIT_WORK_TREE': str(primary),
                                              'GIT_CONFIG_COUNT': '1',
                                              'GIT_CONFIG_KEY_0': 'core.bare',
                                              'GIT_CONFIG_VALUE_0': 'true'}):
                    self.assertEqual(check(tree)['source_manifest_sha256'], expected)
                self.assertEqual(surface(self.lab), before)
        pointer = linked / '.git'
        original = pointer.read_bytes()
        # An absolute, non-traversing Git-created pointer is the supported form.
        self.assertTrue(original.startswith(b'gitdir: /'))
        self.assertEqual(self.invoke([self.tools['git'], '-C', linked, 'ls-files', '--stage']).stdout, '')
        self.assertFalse((normal / '.git/index').exists())

    def test_malformed_traversing_and_outside_git_pointers_refuse(self):
        primary, tree, administrative = self.linked_tree()
        pointer = tree / '.git'; original = pointer.read_bytes()
        other = self.repo('unrelated repository')
        cases = {
            'empty': b'', 'wrong-prefix': b'gitdir wrong\n',
            'missing-newline': original.rstrip(b'\n'), 'multiple-lines': original + b'extra\n',
            'nul': b'gitdir: bad\x00path\n', 'carriage-return': b'gitdir: bad\r\n',
            'traversal': b'gitdir: ../primary repository/.git/worktrees/linked-source-tree\n',
            'outside-linked-layout': ('gitdir: ' + str(other / '.git') + '\n').encode(),
            'missing-target': ('gitdir: ' + str(self.lab / 'missing') + '\n').encode(),
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                pointer.write_bytes(raw)
                self.unchanged_refusal(tree)
        pointer.write_bytes(original)
        alias = self.lab / 'linked-admin'; alias.symlink_to(administrative, target_is_directory=True)
        pointer.write_text('gitdir: ' + str(alias) + '\n')
        self.unchanged_refusal(tree)
        pointer.write_bytes(original)
        alias.unlink()
        pointer.unlink(); pointer.symlink_to(administrative / 'gitdir')
        self.unchanged_refusal(tree)
        pointer.unlink(); pointer.write_bytes(original)
        self.assertEqual(check(tree)['files'], 45)

    def test_missing_linked_metadata_and_ambiguous_identity_refuse(self):
        primary, tree, administrative = self.linked_tree()
        for name in ('HEAD', 'commondir', 'gitdir'):
            path = administrative / name; original = path.read_bytes()
            with self.subTest(missing=name):
                path.unlink()
                self.unchanged_refusal(tree)
                path.write_bytes(original)
            with self.subTest(linked=name):
                path.unlink(); path.symlink_to(primary / '.git/HEAD')
                self.unchanged_refusal(tree)
                path.unlink(); path.write_bytes(original)
        path = administrative / 'commondir'; original = path.read_bytes()
        for raw in (b'../outside\n', b'../../..\n', b'../..\nextra\n'):
            with self.subTest(commondir=raw):
                path.write_bytes(raw); self.unchanged_refusal(tree)
        path.write_bytes(original)
        path = administrative / 'gitdir'; original = path.read_bytes()
        path.write_text(str(primary / '.git') + '\n')
        self.unchanged_refusal(tree); path.write_bytes(original)
        # Duplicate registration is ambiguous even with intact reciprocal metadata.
        duplicate = administrative.parent / 'duplicate-registration'; duplicate.mkdir()
        for name in ('HEAD', 'commondir', 'gitdir'):
            (duplicate / name).write_bytes((administrative / name).read_bytes())
        self.unchanged_refusal(tree)
        for name in ('HEAD', 'commondir', 'gitdir'):
            (duplicate / name).unlink()
        duplicate.rmdir()
        # Shared core.worktree is ignored for linked worktrees by Git. Exercise
        # an actual conflicting top-level identity in a normal repository instead.
        normal = self.public_tree('ambiguous normal')
        self.invoke([self.tools['git'], 'init', '-q', '-b', 'main', normal])
        config = normal / '.git/config'; original = config.read_bytes()
        config.write_bytes(original + ('\n[core]\n\tworktree = ' + str(primary) + '\n').encode())
        self.unchanged_refusal(normal); config.write_bytes(original)
        self.assertEqual(check(tree)['files'], 45)

    def test_non_git_administration_and_query_failures_refuse(self):
        tree = self.public_tree()
        # A source-only export remains supported, but is never a Git repository.
        self.assertEqual(check(tree)['files'], 45)
        with self.assertRaisesRegex(ValueError, 'not a Git repository'):
            git_administration(tree)
        (tree / '.git').mkdir()
        self.unchanged_refusal(tree)
        (tree / '.git').rmdir()
        self.invoke([self.tools['git'], 'init', '-q', '-b', 'main', tree])
        with patch('check_public_tree.shutil.which', return_value=None):
            self.unchanged_refusal(tree)
        from subprocess import CompletedProcess, TimeoutExpired
        results = (CompletedProcess([], 1, b'', b'not a repository'),
                   CompletedProcess([], 0, b'wrong identity\n', b''))
        for result in results:
            with self.subTest(query=result.returncode):
                with patch('check_public_tree.subprocess.run', return_value=result):
                    self.unchanged_refusal(tree)
        with patch('check_public_tree.subprocess.run', side_effect=TimeoutExpired('git', 10)):
            self.unchanged_refusal(tree)

    def test_exact_tree_and_ignored_private_extra_are_not_interchangeable(self):
        tree = self.public_tree()
        self.assertEqual(check(tree)['files'], 45)
        for name in ('.' + 'labs', 'sessions', '.env'):
            with self.subTest(name=name):
                path = tree / name
                path.write_text('synthetic private extra\n')
                with self.assertRaisesRegex(ValueError, 'unlisted source'):
                    check(tree)
                path.unlink()
        folder = tree / ('.' + 'labs'); folder.mkdir()
        with self.assertRaisesRegex(ValueError, 'unlisted directory'):
            check(tree)

    def test_missing_fixtures_fail_before_any_subprocess_or_output(self):
        tree = self.public_tree()
        for name in ('spec.md', 'executable.yaml', 'README.md'):
            with self.subTest(name=name):
                path = tree / 'tests/fixtures/public-contract' / name
                raw = path.read_bytes(); path.unlink()
                with self.assertRaisesRegex(ValueError, 'missing public files'):
                    check(tree)
                result = self.invoke([sys.executable, '-I', '-B', tree / 'tests/run_public.py',
                                      '--phase', 'full', '--require-pi'], expected=1)
                self.assertIn('missing public files', result.stderr)
                self.assertFalse((tree / '.test-output').exists())
                path.write_bytes(raw)

    def test_synthetic_privacy_payloads_in_allowed_docs_refuse(self):
        tree = self.public_tree(); doc = tree / 'docs/capabilities.md'; original = doc.read_bytes()
        payloads = {
            'personal-path': '/' + 'Users' + '/synthetic-user/project',
            'session-shaped': '-'.join(('a'*8, 'b'*4, 'c'*4, 'd'*4, 'e'*12)),
            'historical-spec': 'docs' + '/specs/' + 'synthetic.md',
            'retained-path': '.' + 'labs' + '/synthetic/run',
            'private-evidence': 'primary' + '-support' + '.json',
            'private-file-link': 'file' + '://' + '/synthetic/evidence',
            'human-quotation': 'human ' + 'replied: ' + chr(34) + 'SYNTHETIC not a real decision' + chr(34),
            'private-key-shaped': '-----BEGIN ' + 'PRIVATE KEY-----',
        }
        for name, text in payloads.items():
            with self.subTest(case=name):
                doc.write_bytes(original + ('\n' + text + '\n').encode())
                with self.assertRaises(ValueError):
                    check(tree)
        doc.write_bytes(original)
        self.assertEqual(check(tree)['files'], 45)

    def test_linked_source_ancestor_and_git_surface_refuse(self):
        tree = self.public_tree()
        linked_root = self.lab / 'linked-tree'; linked_root.symlink_to(tree, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'linked source ancestor'):
            check(linked_root)
        path = tree / 'README.md'; raw = path.read_bytes(); path.unlink()
        outside = self.lab / 'outside.md'; outside.write_bytes(raw)
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'linked surface'):
            check(tree)
        self.assertEqual(outside.read_bytes(), raw)
        path.unlink(); path.write_bytes(raw)
        (tree / '.git').symlink_to(self.lab / 'missing-git')
        with self.assertRaisesRegex(ValueError, 'linked surface'):
            check(tree)

    def test_unowned_output_traversal_and_allowlist_drift_refuse(self):
        tree = self.public_tree()
        output = tree / '.test-output'; output.mkdir()
        with self.assertRaisesRegex(ValueError, 'unowned private output'):
            check(tree)
        (output / '.owner').write_text(OUTPUT_MARKER)
        self.assertEqual(check(tree)['files'], 45)
        with self.assertRaisesRegex(ValueError, 'traversal'):
            check(tree / 'not-needed' / '..')
        path = tree / 'tests/public-tree.txt'; path.write_text(path.read_text() + '../outside\n')
        with self.assertRaisesRegex(ValueError, 'allowlist drift'):
            check(tree)

    def test_dangling_doc_link_and_runtime_import_drift_refuse(self):
        tree = self.public_tree()
        (tree / 'docs/capabilities.md').write_text('[Synthetic missing resource](missing.md)\n')
        with self.assertRaisesRegex(ValueError, 'unresolved/outside local link'):
            check(tree)
        (tree / 'docs/capabilities.md').write_bytes((ROOT / 'docs/capabilities.md').read_bytes())
        helper = tree / 'packages/razr-pi/scripts/adopt.py'
        helper.write_text(helper.read_text() + '\nimport synthetic_external_runtime\n')
        with self.assertRaisesRegex(ValueError, 'non-standard static package import'):
            check(tree)

    def test_resource_broadening_and_fixture_drift_refuse(self):
        tree = self.public_tree(); path = tree / 'packages/razr-pi/package.json'; original = path.read_bytes()
        manifest = json.loads(original); manifest['pi']['skills'] = ['../outside']
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'broadened package'):
            check(tree)
        path.write_bytes(original)
        spec = tree / 'tests/fixtures/public-contract/spec.md'
        spec.write_text(spec.read_text() + '\nSynthetic drift.\n')
        with self.assertRaisesRegex(ValueError, 'synthetic fixture source digest drift'):
            check(tree)
