"""Render the six reviewed MySQL guides and verify local/source references."""
from pathlib import Path
import html
import importlib.util
import json
import re
import subprocess
import xml.etree.ElementTree as ET

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
SOURCE_COMMIT = '886600018badf0d157a6cd6d7164a308c4bd1c2b'
spec = importlib.util.spec_from_file_location('prior_renderer', BASE.parent / 'kb-interview-restructure-2026-10-11/build_publication.py')
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
prior.SOURCE_COMMIT = SOURCE_COMMIT


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def render(source, wiki_map):
    lines = source.read_text(encoding='utf-8').splitlines()
    blocks, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.startswith('# '):
            i += 1
            continue
        marker = re.fullmatch(r'<!-- DIAGRAM:([a-z-]+) -->', line)
        if marker:
            diagram = ROOT / 'docs/learning/assets/mysql-interview-2026-10-11' / marker[1] / 'diagram.mmd'
            # The prior search publication found direct Docx Mermaid import
            # unsupported here. Publish locally rendered native nodes into
            # this board, then verify its nonempty content before delivery.
            assert diagram.exists()
            blocks.append('<whiteboard type="blank"></whiteboard>')
        elif line.startswith('<!--') or line.startswith('!['):
            pass
        elif line.startswith('```'):
            language, code = line[3:].strip() or 'text', []
            i += 1
            while i < len(lines) and not lines[i].startswith('```'):
                code.append(lines[i])
                i += 1
            assert i < len(lines), ('Unclosed code', source)
            blocks.append(f'<pre lang="{html.escape(language, quote=True)}"><code>{html.escape(chr(10).join(code))}</code></pre>')
        elif re.match(r'^#{2,4} ', line):
            level = len(line) - len(line.lstrip('#')) - 1
            blocks.append(f'<h{level}>' + prior.inline(line[level+2:], source, wiki_map) + f'</h{level}>')
        elif line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].startswith('|'):
                cells = [x.strip() for x in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r'[-: ]+', c) for c in cells):
                    rows.append(cells)
                i += 1
            assert all(len(r) == len(rows[0]) for r in rows), ('Ragged table', source)
            def row_xml(row, tag):
                return '<tr>' + ''.join(f'<{tag}><p>{prior.inline(c, source, wiki_map)}</p></{tag}>' for c in row) + '</tr>'
            blocks.append('<table><thead>' + row_xml(rows[0], 'th') + '</thead><tbody>' + ''.join(row_xml(r, 'td') for r in rows[1:]) + '</tbody></table>')
            continue
        elif re.match(r'(?:\d+\. |[-*] )', line):
            ordered = bool(re.match(r'\d+\. ', line))
            pattern, tag = (r'\d+\. ', 'ol') if ordered else (r'[-*] ', 'ul')
            items = []
            while i < len(lines) and re.match(pattern, lines[i]):
                items.append('<li>' + prior.inline(re.sub('^' + pattern, '', lines[i]), source, wiki_map) + '</li>')
                i += 1
            blocks.append(f'<{tag}>' + ''.join(items) + f'</{tag}>')
            continue
        elif line.startswith('> '):
            blocks.append('<blockquote>' + prior.inline(line[2:], source, wiki_map) + '</blockquote>')
        else:
            blocks.append('<p>' + prior.inline(line, source, wiki_map) + '</p>')
        i += 1
    body = '\n'.join(blocks) + '\n'
    ET.fromstring('<doc>' + body + '</doc>')
    return body


def norm(value):
    return re.sub(r'\s+', '', value)


def local_check(pages):
    links, code_checks = [], []
    source_files = ['backend/apps/ordering/' + f for f in ('views.py','models.py','services.py','serializers.py','migrations/0005_order_list_index.py')] + ['backend/apps/inventory/models.py','backend/apps/promotions/services.py']
    snapshots = {p: subprocess.check_output(['git', 'show', f'{SOURCE_COMMIT}:{p}'], cwd=ROOT).decode('utf-8') for p in source_files}
    for page in pages:
        source = ROOT / 'docs/learning' / page['guide']
        text = source.read_text(encoding='utf-8')
        assert '如果重新做' in text and 'knowledge-base-publication.md' in text, page['key']
        for target in re.findall(r'\]\(([^)]+)\)', text):
            if re.match(r'https?://', target):
                continue
            path, _, anchor = target.partition('#')
            resolved = (source.parent / path).resolve()
            assert resolved.is_relative_to(ROOT) and resolved.exists(), (source, target)
            if re.fullmatch(r'L\d+', anchor):
                assert 0 < int(anchor[1:]) <= len(resolved.read_text(encoding='utf-8').splitlines()), target
            links.append({'guide': page['guide'], 'target': target})
        for code in re.findall(r'```python\n(.*?)\n```', text, re.S):
            matches = [p for p, full in snapshots.items() if norm(code) in norm(full)]
            assert len(matches) == 1, (page['key'], 'Snippet not unique/exact', matches)
            code_checks.append({'guide': page['guide'], 'source': matches[0], 'source_commit': SOURCE_COMMIT})
    return links, code_checks


def main():
    pages = read(BASE / 'draft-workspaces.json')
    created_path = BASE / 'created.json'
    created = read(created_path) if created_path.exists() else []
    wiki_map = {(ROOT / 'docs/learning' / guide).resolve(): node for _, guide, node, _ in prior.PAGE_GUIDES}
    for page in created:
        workspace = next(p for p in pages if p['key'] == page['key'])
        wiki_map[(ROOT / 'docs/learning' / workspace['guide']).resolve()] = page['node_token']
    for folder, name in [('redis-interview', 'redis-created.json')]:
        previous = read(BASE.parent / 'kb-interview-restructure-2026-10-11' / name)
        for index, page in enumerate(previous):
            wiki_map[(ROOT / 'docs/learning' / folder / prior.REDIS_GUIDES[index]).resolve()] = page['node_token']
    wiki_map[(ROOT / 'docs/learning/search-current-overview-study-guide.md').resolve()] = 'KxWQwuGFsiqO3Wk4m7TcZxXBnih'
    links, codes = local_check(pages)
    candidates = BASE / 'candidates'
    candidates.mkdir(exist_ok=True)
    for page in pages:
        source = ROOT / 'docs/learning' / page['guide']
        body = render(source, wiki_map)
        profile = '<title>' + html.escape(page['title']) + '</title>\n' + body
        draft = BASE / page['draft_path'].replace('\\', '/')
        assert draft.parent.exists(), 'Must initialize draft through CLI first'
        draft.write_text(profile, encoding='utf-8')
        (candidates / (page['key'] + '-body.xml')).write_text(body, encoding='utf-8')
        (candidates / (page['key'] + '-profile.xml')).write_text(profile, encoding='utf-8')
    result = {'source_commit': SOURCE_COMMIT, 'guides': len(pages), 'local_links': len(links), 'core_snippets': len(codes), 'links': links, 'code_checks': codes, 'fixed_snapshot_paths': sorted(prior.snapshot_links)}
    (BASE / 'local-checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('guides', 'local_links', 'core_snippets')}, ensure_ascii=True))


if __name__ == '__main__':
    main()
