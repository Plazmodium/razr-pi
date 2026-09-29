#!/usr/bin/env python3
"""Read-only approval-pair diagnostics. Never approves, compiles or applies a pair."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
CORE = Path(__file__).resolve().parents[1] / 'runtime/scripts/razr.py'
spec = importlib.util.spec_from_file_location('razr_contract_core', CORE)
core = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = core
spec.loader.exec_module(core)


def inspect_pair(pair):
    folder = Path(pair).expanduser().absolute()
    if any(p.is_symlink() for p in (folder, *folder.parents)):
        raise core.RazrError('approval-pair directory must not contain symlink components')
    folder = folder.resolve(strict=True)
    core.reject_canonical_symlinks(folder, include_materialized=False)
    paths = [folder / name for name in ('spec.md', 'executable.yaml')]
    before = {p: p.read_bytes() for p in paths}
    metadata, body = core.parse_spec(paths[0])
    executable, errors = core.validate_executable(folder, metadata.get('id'), metadata, body)
    # Narrow regression diagnostic, not a semantic contract validator or state authority.
    # Review every contract manually; passing this pattern check is not contradiction proof.
    flat = re.sub(r'\s+', ' ', body.replace('`', '')).lower()
    if re.search(r'(?:do not|never) hand[- ]edit (?:materialized )?state (?:or|and) evidence', flat):
        errors.append('Evidence authority conflict: forbid hand-editing state, not observed evidence appends. '
                      'Revise the draft before exact approval; do not rewrite an approved contract.')
    if any(p.is_symlink() or p.read_bytes() != data for p, data in before.items()):
        raise core.RazrError('approval pair changed during inspection; STOP')
    return {
        'schema_version': 1, 'pair': str(folder), 'work_id': metadata.get('id'),
        'structural_preflight': 'FAIL' if errors else 'PASS', 'errors': errors,
        'identities': {
            'spec_file_sha256': 'sha256:' + hashlib.sha256(before[paths[0]]).hexdigest(),
            'executable_file_sha256': 'sha256:' + hashlib.sha256(before[paths[1]]).hexdigest(),
            'executable_core_digest': core.executable_digest(executable),
        },
        'authority': 'Not approval, proof of execution, or permission to apply this pair.',
        'manual_checks': ['Review semantic consistency, including state versus evidence ownership.',
                          'Resolve ambiguous product inputs and match every normative statement verbatim.',
                          'Record actual human decisions separately; never reuse prepared timestamps as observations.',
                          'Core approve and later transitions remain authoritative.'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pair', required=True)
    args = parser.parse_args()
    try:
        report = inspect_pair(args.pair)
        print(json.dumps(report, indent=2))
        return 0 if not report['errors'] else 1
    except (core.RazrError, OSError, ValueError, TypeError) as exc:
        print(f'STOP: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
