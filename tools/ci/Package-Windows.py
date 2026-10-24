"""Preserve CI binaries, their source and runtime notices for local validation."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

root = Path(__file__).resolve().parents[2]
out = root / 'build/ci-bundle'
out.mkdir(parents=True, exist_ok=False)

def copy(source, target):
    destination = out / target
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)

tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
for name in filter(None, tracked):
    if (root / name).is_file():
        copy(root / name, name)

for source in (root / 'build').rglob('*'):
    if out in source.parents or not source.is_file():
        continue
    relative = source.relative_to(root)
    # Keep final programs, libraries and fixtures; omit compiler intermediates.
    if source.suffix.lower() not in ('.exe', '.dll'):
        continue
    if any(part in ('deps', 'incremental', 'build', '.fingerprint') for part in relative.parts[2:]):
        continue
    copy(source, relative)
copy(root / 'work/OemEndpointProbe.exe', 'work/OemEndpointProbe.exe')

for name in ('minhook', 'roar'):
    source_root = root / 'work' / name
    for source in source_root.rglob('*'):
        if source.is_file() and '.git' not in source.relative_to(source_root).parts:
            copy(source, source.relative_to(root))

cargo_sources = Path(os.environ['CARGO_HOME']) / 'registry/src'
for source in cargo_sources.rglob('*'):
    if source.is_file():
        copy(source, Path('dependency-sources/cargo') / source.relative_to(cargo_sources))
llvm_root = Path(os.environ['NSP_LLVM_BIN']).parent
for source in llvm_root.rglob('*'):
    if source.is_file() and (source.name == 'LICENSE.TXT' or source.name.startswith('COPYING')):
        copy(source, Path('dependency-sources/llvm-runtime-notices') / source.relative_to(llvm_root))

(out / 'CI-BUNDLE.txt').write_text(
    'Windows CI development bundle; not an installer or a release.\n'
    'Programs retain repository-relative paths. No audio driver is bundled.\n'
    'Source, dependency sources and runtime notices accompany the binaries.\n'
    'ASIO combined binaries are GPL-3.0-only; original source notices remain valid.\n', encoding='utf-8')
files = {p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in sorted(out.rglob('*')) if p.is_file()}
manifest = {'commit': os.environ['GITHUB_SHA'], 'runId': os.environ['GITHUB_RUN_ID'],
            'files': files}
(out / 'CI-MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
print(f'Packaged {len(files)} files from {manifest["commit"]}')
