"""Verify authored links and Python excerpts against the recorded source commit."""
import json
from pathlib import Path
import re
import subprocess

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
SOURCE = '29b7944860080ccddedb2c0f0c060c0a27658376'
GUIDES = [ROOT / 'docs/learning' / name for name in (
    'project-current-overview-study-guide.md', 'payment-current-overview-study-guide.md',
    'redis-current-overview-study-guide.md')]
GUIDES += sorted((ROOT / 'docs/learning/payment-interview').glob('*.md'))
GUIDES += sorted((ROOT / 'docs/learning/redis-interview').glob('*.md'))
OTHER = [ROOT / 'docs/learning/README.md', ROOT / 'docs/learning/knowledge-base-authoring-sop.md',
         ROOT / 'docs/verification/payment-redis-interview-knowledge-base-restructure.md']


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode('utf-8')


def normalized(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


sources = {path: git('show', SOURCE + ':' + path)
           for path in git('ls-tree', '-r', '--name-only', SOURCE, 'backend/apps').splitlines()
           if path.endswith('.py')}
links, excerpts = [], []
for path in GUIDES + OTHER:
    text = path.read_text(encoding='utf-8')
    for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', text):
        if re.match(r'(https?://|#)', target):
            continue
        relative, _, anchor = target.partition('#')
        resolved = (path.parent / relative).resolve()
        assert resolved.is_relative_to(ROOT) and resolved.exists(), (path, target)
        if re.fullmatch(r'L\d+', anchor):
            assert int(anchor[1:]) <= len(resolved.read_text(encoding='utf-8').splitlines()), (path, target)
        links.append({'guide': path.relative_to(ROOT).as_posix(), 'target': target})
    for index, excerpt in enumerate(re.findall(r'```python\n(.*?)\n```', text, re.S), 1):
        selected = normalized(excerpt)
        matches = []
        for source_path, source in sources.items():
            lines = normalized(source)
            for offset in range(len(lines)-len(selected)+1):
                if lines[offset:offset+len(selected)] == selected:
                    matches.append(source_path)
                    break
        assert matches, ('Source excerpt not matched', path, index)
        excerpts.append({'guide': path.relative_to(ROOT).as_posix(), 'excerpt': index,
                         'nonblank_lines': len(selected), 'matching_source_paths': matches})
assert len(GUIDES) == 15 and len(excerpts) == 23, (len(GUIDES), len(excerpts))
result = {'status': 'passed', 'source_commit': SOURCE, 'guides': len(GUIDES),
          'local_links': len(links), 'python_excerpts': len(excerpts),
          'excerpt_checks': excerpts, 'link_checks': links}
(BASE / 'local-static-checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps({key: result[key] for key in ('status', 'guides', 'local_links', 'python_excerpts')}))
