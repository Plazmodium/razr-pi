"""Pinned core/template/helper identity; synthetic missing/drift refusals."""
import json

from test_adoption import FixtureTest
from check_public_tree import check, PROVENANCE, SOURCE_PINS
from run_public import ROOT


class ProvenanceTests(FixtureTest):
    def test_all_declared_bytes_match_and_survive_relocation(self):
        result = check(ROOT)
        self.assertEqual(result['core_templates_matched'], 6)
        self.assertEqual(check(self.public_tree())['source_manifest_sha256'], result['source_manifest_sha256'])

    def test_each_core_template_missing_or_changed_refuses(self):
        tree = self.public_tree()
        for name in PROVENANCE:
            with self.subTest(path=name):
                path = tree / 'packages/razr-pi/runtime' / name
                raw = path.read_bytes(); path.unlink()
                with self.assertRaisesRegex(ValueError, 'missing public files'):
                    check(tree)
                path.write_bytes(raw + b'\n')
                with self.assertRaisesRegex(ValueError, 'provenance mismatch'):
                    check(tree)
                path.write_bytes(raw)

    def test_helper_and_provenance_declaration_drift_refuses(self):
        tree = self.public_tree()
        for name in SOURCE_PINS:
            with self.subTest(path=name):
                path = tree / 'packages/razr-pi' / name
                raw = path.read_bytes(); path.write_bytes(raw + b'\n')
                with self.assertRaisesRegex(ValueError, 'retained helper/provenance drift'):
                    check(tree)
                path.write_bytes(raw)

    def test_remote_license_and_modes_are_preserved(self):
        tree = self.public_tree()
        license_path = tree / 'LICENSE'; original = license_path.read_bytes()
        license_path.write_bytes(original + b'\n')
        with self.assertRaisesRegex(ValueError, 'remote license drift'):
            check(tree)
        license_path.write_bytes(original)
        path = tree / 'packages/razr-pi/scripts/adopt.py'; path.chmod(0o755)
        with self.assertRaisesRegex(ValueError, '100644'):
            check(tree)
