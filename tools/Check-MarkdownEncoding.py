"""Reject invalid UTF-8 and lossy replacement characters in tracked Markdown."""
import argparse
import json
from pathlib import Path
import re
import subprocess


def inspect(root):
    names = subprocess.check_output(
        ['git', 'ls-files', '-z'], cwd=root).decode('utf-8').split('\0')
    findings = []
    count = 0
    for name in names:
        if not name.lower().endswith('.md'):
            continue
        path = root / name
        if not path.is_file():
            continue
        count += 1
        try:
            content = path.read_text(encoding='utf-8-sig')
        except UnicodeDecodeError:
            findings.append(dict(path=name, issue='invalid UTF-8'))
            continue
        for number, line in enumerate(content.splitlines(), 1):
            if '\ufffd' in line or re.search(r'\?{3,}', line):
                findings.append(dict(path=name, line=number,
                                     issue='replacement character or question-mark run'))
    return dict(root=str(root), markdownFiles=count, findings=findings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repositories', nargs='*', type=Path,
                        default=[Path(__file__).resolve().parents[1]])
    args = parser.parse_args()
    results = [inspect(path.resolve()) for path in args.repositories]
    print(json.dumps(results, ensure_ascii=True, indent=2))
    if any(result['findings'] for result in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
