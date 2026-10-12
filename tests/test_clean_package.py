"""Actual installed no-model package resolver plus synthetic loading refusals."""
import json
import os
from pathlib import Path
import shutil
from unittest.mock import patch

from test_adoption import FixtureTest, surface
from run_public import ROOT, PACKAGE, installed_tools


class CleanPackageTests(FixtureTest):
    def loader_fixture(self):
        self.package = self.lab / 'relocated package'
        shutil.copytree(PACKAGE, self.package)
        self.project = self.repo('clean project')
        return Path(os.environ['PUBLIC_PI_ROOT'])

    def load(self, pi_root, expected=0):
        return self.invoke([self.tools['node'], ROOT / 'tests/clean_package_preflight.mjs', pi_root, self.lab],
                           self.project, expected)

    def test_actual_resolver_is_readonly_and_does_not_adopt(self):
        pi_root = self.loader_fixture()
        before = {name: surface(self.lab / name) for name in ('home', 'agent', 'xdg', 'clean project', 'relocated package')}
        result = json.loads(self.load(pi_root).stdout)
        self.assertEqual(result['loaded'], dict(skills=1, extensions=1, prompts=0, themes=0, contextFiles=0))
        self.assertEqual(result['networkAttempts'], 0)
        self.assertEqual(result['modelInvocations'], 0)
        self.assertFalse(result['adopted'])
        self.assertEqual(result['before'], result['after'])
        self.assertEqual({name: surface(self.lab / name) for name in before}, before)
        self.assertFalse((self.project / 'razr.yaml').exists())

    def test_broadened_manifest_and_install_scripts_refuse_before_loading(self):
        pi_root = self.loader_fixture()
        path = self.package / 'package.json'; original = path.read_bytes()
        for case in ('extensions', 'scripts', 'external-files'):
            with self.subTest(case=case):
                manifest = json.loads(original)
                if case == 'extensions': manifest['pi']['extensions'] = ['./scripts']
                elif case == 'scripts': manifest['scripts'] = {'postinstall': 'must-not-run'}
                else: manifest['files'].append('../outside')
                path.write_text(json.dumps(manifest))
                before = surface(self.project)
                self.load(pi_root, 1)
                self.assertEqual(surface(self.project), before)
                self.assertEqual(surface(self.lab / 'agent'), {})
                self.assertEqual(surface(self.lab / 'home'), {})
        path.write_bytes(original)

    def test_missing_skill_and_linked_skill_refuse(self):
        pi_root = self.loader_fixture()
        path = self.package / 'skills/razr/SKILL.md'; raw = path.read_bytes()
        path.unlink()
        self.load(pi_root, 1)
        external = self.lab / 'synthetic-skill.md'; external.write_bytes(raw)
        path.symlink_to(external)
        self.load(pi_root, 1)
        self.assertEqual(external.read_bytes(), raw)
        self.assertFalse((self.project / 'razr.yaml').exists())

    def test_unsupported_loader_layout_is_not_a_skip_or_fallback(self):
        self.loader_fixture()
        unavailable = self.lab / 'unsupported-pi'; unavailable.mkdir()
        (unavailable / 'package.json').write_text(json.dumps({'name': 'synthetic/pi-coding-agent', 'version': 'diagnostic'}))
        self.load(unavailable, 1)
        self.assertFalse((self.project / 'razr.yaml').exists())
        real_which = shutil.which
        with patch('run_public.shutil.which', side_effect=lambda name: None if name == 'pi' else real_which(name)):
            with self.assertRaisesRegex(ValueError, 'missing prerequisite: pi'):
                installed_tools()

    def test_unsupported_review_and_factory_scripts_have_no_fallback(self):
        for name in ('technical-critic.py', 'portable-review.py', 'factory.py'):
            with self.subTest(name=name):
                self.assertFalse((PACKAGE / 'scripts' / name).exists())
                self.helper(name, expected=2)
        self.assertFalse((self.lab / 'project').exists())
