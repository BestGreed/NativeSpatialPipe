"""Check the exported tree, required files, documentation links and public boundary."""
from pathlib import Path
import hashlib
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def verify(root):
    manifest = json.loads((root/'docs/core-source-manifest.json').read_text(encoding='utf-8'))
    problems = []
    names = {record['path'] for record in manifest['files']}
    for required in ('tools/Build-Product.ps1', 'modules/zig-scheduler/host.zig',
                     'modules/native-capture/profiles/capture-profiles.json',
                     'docs/user-guide.md', 'docs/development.md', 'tools/ci/Test-Candidate.ps1'):
        if required not in names:
            problems.append(f'Missing manifest entry: {required}')
    for record in manifest['files']:
        name = record['path']
        path = (root/name).resolve()
        if not path.is_relative_to(root.resolve()) or '.git' in Path(name).parts:
            problems.append(f'Invalid manifest path: {name}')
            continue
        if not path.is_file():
            problems.append(f'Missing file: {name}')
            continue
        raw = path.read_bytes()
        if hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest() != record['sha256']:
            problems.append(f'Hash mismatch: {name}')
        if name.startswith(('build/', 'work/', 'experiments/', '.zig-cache/')) or path.suffix.lower() in {'.dll', '.exe', '.pdb', '.wav', '.etl', '.f32', '.obj', '.o', '.rlib', '.pyd'}:
            problems.append(f'Non-source payload: {name}')
        if path.name in {'Enum-Hook.c', 'Enum-Hook.h', 'native_enum.py', 'audiosrv-profiles.json', 'Build-EnumHook.ps1'}:
            problems.append(f'Private enumeration implementation: {name}')
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            continue
        if re.search(r'(?i)[CD]:[/\\](?:Users[/\\]root|Codex|Documents[/\\]ChatGPT)', text):
            problems.append(f'Developer path: {name}')
        if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}', text):
            problems.append(f'Credential-shaped content: {name}')
        if path.suffix == '.md':
            if '\ufffd' in text:
                problems.append(f'Invalid Markdown text: {name}')
            for link in re.findall(r'\]\(([^)]+)\)', text):
                target = link.split('#')[0].strip('<>')
                if target and '://' not in target and not target.startswith(('mailto:', 'data:')) and not (path.parent/target).exists():
                    problems.append(f'Broken Markdown link: {name}: {link}')
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    for name in filter(None, tracked):
        if (root/name).is_file() and name != 'docs/core-source-manifest.json' and name not in names:
            problems.append(f'Tracked source omitted from manifest: {name}')
    return dict(sourceRevision=manifest['sourceRevision'], files=len(names), problems=problems)


if __name__ == '__main__':
    result = verify(ROOT)
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result['problems']))
