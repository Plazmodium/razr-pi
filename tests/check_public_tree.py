"""Read-only public source boundary check; no project module imports.

Git administration, when present, is validated with bounded read-only Git queries.

A lexical check is not confidentiality certification. Review every source before
publication. Synthetic refusal tests supplement, not replace, that review.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys

ALLOWLIST_SHA256 = '52ebf52bdb8739f3fadbeca39bc1d9277b85073c9761b267f3fc7c1ae17258f4'
LICENSE_SHA256 = '9d2fdecab2aa2dca006dbd67b07d3cc63a9d9f64a4f954aff2fe782251f9f8f4'
OUTPUT_MARKER = 'clean-source-v1 owned private test output\n'
PROVENANCE = {
    'scripts/razr.py': '4a366402b130b946d6609ddee392167b80c8f69723e292202b23aeb0a507bd9a',
    'templates/evidence.md': '7a4a2eee74df44e152f079f31f032c34c2de1a0dba04530d97df3f15ac8c7447',
    'templates/executable.yaml': '662461e91527d3ed810edd6a04f7fd223f3097f9066e1e9fa158f8adc196a0f2',
    'templates/razr.yaml': 'a68278cdf4588c8debad214f22dedbc6ef31011287160bdafd981b57a2ac335a',
    'templates/spec.md': '908f799cada27c14ebf8e121c8150dbbd2a6dbf4115f0f6033a032f060e334bb',
    'templates/state.yaml': 'e1f908381f38097798320e2ebc1e12663d93a6aa90e66c89f6bc5873758a11af',
}

SOURCE_PINS = {
    'scripts/adopt.py': '0beb922788fbf0e62182437a97fa7ce2382ad3af6184f912110a3e370dab4689',
    'scripts/primary.py': '4ea38b5ef08dfb4944f50376d2475653d81671fb51239cac61549c49dfe895ed',
    'scripts/contract-preflight.py': '54d237feabd02cb250c51d677cc8df08de9fe9feaecc557583ef76579899a850',
    'scripts/named-regressions.py': '8885fa72a2ae3446e69577f26347c3ffacd8f499a322725d807bb8e947d01392',
    'runtime/provenance.json': 'd4a712e663ac75ff0f447a0a2a62e917f43222aa652cb1ba8cb7735b6e4bf1ee',
}


class Refusal(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise Refusal(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def regular(path):
    require(not path.is_symlink(), 'linked file: ' + path.name)
    require(stat.S_ISREG(path.stat().st_mode), 'non-regular file: ' + path.name)
    require(stat.S_IMODE(path.stat().st_mode) == 0o644, 'file mode must be 100644: ' + path.name)
    return path.read_bytes()


def git_administration(root):
    """Validate only normal repositories or registered linked worktrees.

    This is an identity cross-check, not a concurrent-path-swap sandbox or an
    authentication of repository ownership. Source-only exports use no .git and
    are handled by check(), not by this repository-only validator.
    """
    root = Path(root).absolute()

    def safe(path, directory=False):
        require(all(not p.is_symlink() for p in (path, *path.parents)),
                'linked Git metadata')
        mode = path.stat().st_mode
        require(stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode),
                'nonregular Git metadata')
        return path

    def text(path):
        safe(path)
        require(path.stat().st_size <= 4096, 'oversized Git metadata')
        raw = path.read_bytes()
        require(b'\x00' not in raw and b'\r' not in raw, 'malformed Git metadata')
        return raw.decode('utf-8')

    def pointer(value, parent):
        require(value and '\n' not in value and '\x00' not in value,
                'malformed Git pointer')
        p = Path(value)
        require('..' not in p.parts and str(p) == value, 'Git pointer traversal or ambiguity')
        p = p if p.is_absolute() else parent / p
        require(all(not q.is_symlink() for q in (p, *p.parents)), 'linked Git metadata')
        return p

    safe(root, directory=True)
    entry = root / '.git'
    require(entry.exists() or entry.is_symlink(), 'not a Git repository')
    require(not entry.is_symlink(), 'linked Git metadata')
    if entry.is_dir():
        administrative = common = safe(entry, directory=True)
        require(not (entry / 'commondir').exists() and not (entry / 'commondir').is_symlink(),
                'ambiguous normal repository')
    else:
        raw = text(entry)
        require(raw.startswith('gitdir: ') and raw.endswith('\n') and raw.count('\n') == 1,
                'malformed .git file')
        administrative = safe(pointer(raw[8:-1], root), directory=True)
        # A standard linked entry lives directly in COMMON/worktrees/NAME.
        require(administrative.parent.name == 'worktrees' and administrative.name not in ('', '.', '..'),
                'outside-repository gitdir')
        common = safe(administrative.parent.parent, directory=True)
        require(common.name == '.git', 'outside-repository common directory')
        safe(administrative.parent, directory=True)
        require(text(administrative / 'commondir') == '../..\n', 'invalid worktree commondir')
        backlink = text(administrative / 'gitdir')
        require(backlink.endswith('\n') and backlink.count('\n') == 1,
                'malformed worktree backlink')
        require(pointer(backlink[:-1], administrative) == entry, 'wrong worktree backlink')
    safe(administrative / 'HEAD')
    safe(common / 'HEAD')
    safe(common / 'objects', directory=True)
    safe(common / 'refs', directory=True)
    if (common / 'config').exists() or (common / 'config').is_symlink():
        safe(common / 'config')
    if (administrative / 'config.worktree').exists() or (administrative / 'config.worktree').is_symlink():
        safe(administrative / 'config.worktree')
    executable = shutil.which('git')
    require(executable is not None, 'Git required to validate repository administration')
    # No inherited GIT_DIR/WORK_TREE/CONFIG, provider, loader or shell environment.
    env = {'PATH': os.defpath, 'LANG': 'C', 'LC_ALL': 'C', 'HOME': os.devnull,
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_SYSTEM': os.devnull,
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_OPTIONAL_LOCKS': '0',
           'GIT_TERMINAL_PROMPT': '0', 'GIT_CEILING_DIRECTORIES': str(root.parent)}

    def query(*args):
        try:
            result = subprocess.run([executable, '--no-optional-locks', '-c', 'core.fsmonitor=false',
                                     '-c', 'core.hooksPath=' + os.devnull, '-C', str(root), *args],
                                    env=env, capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Refusal('Git identity query unavailable or timed out') from exc
        require(result.returncode == 0 and not result.stderr and len(result.stdout) <= 1024 * 1024,
                'Git identity query refused or ambiguous')
        return result.stdout.decode('utf-8')

    values = query('rev-parse', '--path-format=absolute', '--show-toplevel', '--absolute-git-dir',
                   '--git-common-dir', '--is-inside-work-tree', '--is-bare-repository').splitlines()
    require(values == [str(root), str(administrative), str(common), 'true', 'false'],
            'ambiguous repository identity')
    entries = query('worktree', 'list', '--porcelain', '-z').split('\x00\x00')
    locations = []
    for block in entries:
        if not block:
            continue
        fields = block.split('\x00')
        require(fields[0].startswith('worktree ') and not any(f.startswith(('bare', 'prunable')) for f in fields),
                'ambiguous worktree registration')
        locations.append(fields[0][9:])
    require(len(locations) == len(set(locations)) and locations.count(str(root)) == 1
            and locations.count(str(common.parent)) == 1, 'ambiguous worktree registration')


def check(root):
    root = Path(root).absolute()
    require('..' not in root.parts, 'source root traversal refused')
    require(all(not p.is_symlink() for p in (root, *root.parents)), 'linked source ancestor')
    root = root.resolve(strict=True)
    raw = regular(root / 'tests/public-tree.txt')
    require(sha(raw) == ALLOWLIST_SHA256, 'public allowlist drift')
    names = raw.decode('utf-8').splitlines()
    require(len(names) == len(set(names)) == 53, 'invalid allowlist cardinality')
    for name in names:
        p = PurePosixPath(name)
        require(not p.is_absolute() and '..' not in p.parts and str(p) == name, 'unsafe allowlist entry')
    allowed = set(names)
    parent_dirs = {str(parent) for name in names for parent in PurePosixPath(name).parents if str(parent) != '.'}
    found = set()

    def walk(folder):
        for p in sorted(folder.iterdir()):
            name = p.relative_to(root).as_posix()
            require(not p.is_symlink(), 'linked surface: ' + name)
            if folder == root and p.name == '.git':
                require(p.is_dir() or p.is_file(), 'nonregular Git administrative surface')
                continue  # Validate identity after all source/fixture checks; never export it.
            if folder == root and p.name == '.test-output':
                require(p.is_dir(), 'invalid private output root')
                marker = p / '.owner'
                require(marker.is_file() and not marker.is_symlink() and marker.read_text() == OUTPUT_MARKER,
                        'unowned private output root')
                continue  # Private runtime output is never exported as source.
            if p.is_dir():
                require(name in parent_dirs, 'unlisted directory: ' + name)
                walk(p)
            else:
                require(name in allowed, 'unlisted source: ' + name)
                regular(p)
                found.add(name)

    walk(root)
    require(found == allowed, 'missing public files: ' + ', '.join(sorted(allowed - found)))
    hashes = {}
    for name in sorted(allowed):
        data = regular(root / name)
        hashes[name] = sha(data)
        text = data.decode('utf-8')
        # Denial policy itself is not a runtime dependency. No other source exception.
        if name != 'tests/check_public_tree.py':
            require(not re.search(r'/(?:Users|home)/[^\s/]+|[A-Za-z]:\\(?:Users|Documents)\\', text),
                    'personal absolute path: ' + name)
            require(not re.search(r'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', text, re.I),
                    'session-shaped identifier: ' + name)
            require('docs/specs/' not in text, 'historical specification dependency: ' + name)
            require(not re.search(r'\b(?:primary-support|sequence-receipt|technical-checkpoint|review-inputs)\.(?:json|jsonl)\b', text),
                    'private retained evidence reference: ' + name)
            require('file://' not in text, 'private file evidence link: ' + name)
            require(not re.search(r'''\bhuman\s+(?:said|replied|responded)\s*:?\s*["'\u201c\u2018]''', text, re.I),
                    'historical human quotation: ' + name)
            require(not re.search(r'(?:ROOT|root)\.parent\s*/\s*[\x27\x22]razr[\x27\x22]', text),
                    'sibling distribution dependency: ' + name)
            require(not re.search(r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----', text),
                    'private key material: ' + name)
            if name != '.gitignore':
                require('.labs/' not in text and 'ephemeral/' not in text,
                        'retained workspace dependency: ' + name)
        if name.endswith('.py'):
            tree = ast.parse(text, filename=name)
            if name.startswith('packages/'):
                for node in ast.walk(tree):
                    imports = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                               else [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
                    require(all(module.startswith('.') or module == 'protocol' or module.split('.')[0] in sys.stdlib_module_names for module in imports),
                            'non-standard static package import: ' + name)
        elif name.endswith('.json'):
            json.loads(text)
        if name.endswith('.md'):
            for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', text):
                if '://' in target or target.startswith('#'):
                    continue
                linked = (root / name).parent / target.split('#', 1)[0]
                resolved = linked.resolve()
                require(resolved.is_relative_to(root) and resolved.is_file(), 'unresolved/outside local link: ' + name)

    require(hashes['LICENSE'] == LICENSE_SHA256, 'remote license drift')
    package = root / 'packages/razr-pi'
    for name, expected in SOURCE_PINS.items():
        require(sha(regular(package / name)) == expected, 'retained helper/provenance drift: ' + name)
    manifest = json.loads(regular(package / 'package.json'))
    require(set(manifest) == {'name', 'version', 'private', 'description', 'keywords', 'pi', 'files'},
            'unexpected package manifest fields')
    require(manifest['name'] == 'razr-pi' and manifest['private'] is True, 'unsafe package identity/publication policy')
    require(manifest['pi'] == {'skills': ['./skills']}, 'broadened package resource loading')
    require(manifest['files'] == ['skills', 'runtime', 'scripts', 'adapter', 'README.md'], 'package file declaration drift')
    require(sorted(p.relative_to(package).as_posix() for p in (package / 'skills').rglob('SKILL.md'))
            == ['skills/razr/SKILL.md'], 'unexpected package skill inventory')
    provenance = json.loads(regular(package / 'runtime/provenance.json'))
    rows = provenance['files']
    require(len(rows) == 6 and {row['path']: row['sha256'] for row in rows} == PROVENANCE,
            'immutable provenance declaration drift')
    for name, expected in PROVENANCE.items():
        require(sha(regular(package / 'runtime' / name)) == expected, 'core/template provenance mismatch: ' + name)
    fixture = root / 'tests/fixtures/public-contract'
    spec_raw = regular(fixture / 'spec.md')
    executable = json.loads(regular(fixture / 'executable.yaml'))
    require(executable['source_spec']['digest'] == 'sha256:' + sha(spec_raw), 'synthetic fixture source digest drift')
    require(b'condition: draft\n' in spec_raw and b'approved_by: null\n' in spec_raw
            and b'approval_ref: null\n' in spec_raw, 'public fixture must remain unapproved')
    body = spec_raw.decode('utf-8').split('---', 2)[2]
    ids = re.findall(r'^((?:REQ|CON|NG|VER)-\d+): (.+)$', body, re.M)
    require({item['id']: item['statement'] for item in executable['contract']} == dict(ids),
            'synthetic normative mapping drift')
    if (root / '.git').exists():
        git_administration(root)
    records = ''.join(name + '\t' + hashes[name] + '\t100644\n' for name in sorted(hashes))
    return {'status': 'PASS', 'kind': 'static-public-tree-preflight', 'files': len(hashes),
            'core_templates_matched': 6, 'source_manifest_sha256': sha(records.encode()), 'source_hashes': hashes,
            'limitations': 'Static shape/lexical/link/provenance checks, not execution or complete privacy certification.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = check(args.root)
        print(json.dumps({k: v for k, v in report.items() if k != 'source_hashes'}, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, SyntaxError) as exc:
        print('STOP: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
