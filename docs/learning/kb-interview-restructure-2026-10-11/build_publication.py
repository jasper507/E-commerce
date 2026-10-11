"""Render reviewed guides and plan text-only edits around protected Lark boards."""
from pathlib import Path
from urllib.parse import quote
import copy
import html
import json
import re
import subprocess
import xml.etree.ElementTree as ET

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
SOURCE_COMMIT = '29b7944860080ccddedb2c0f0c060c0a27658376'
WIKI = 'https://ocnbkcx1ug58.feishu.cn/wiki/'
PAGE_GUIDES = [
    ('project', 'project-current-overview-study-guide.md', 'WN1jwXDj0iPXm7kwLyHcmneenlb', 'P0LPd9AkmoAhq8xCgOdcqEL2nag'),
    ('payment', 'payment-current-overview-study-guide.md', 'PCQ7wP9kMikz0eklmNYcH1J1nwh', 'T6O0deWUnowaBsxM6LgcJfuynjb'),
    ('redis', 'redis-current-overview-study-guide.md', 'O4iUwSvl1i4Je7keOL0ci1iAnLf', 'KKBXdHFQqoZRCNxJv1YcFb29nkf'),
    ('payment-01', 'payment-interview/01-models.md', 'N37xwtJ27iZ4XmkYa1uchxswnbc', 'Onm2dT0Two29z9xQwi5cdrrAncT'),
    ('payment-02', 'payment-interview/02-normal-payment.md', 'Dn70wUIlKixYzMk0X4kcCvGsnR3', 'Gpgdd1UeAo4fPIx9biBc9gn4nxd'),
    ('payment-03', 'payment-interview/03-unknown.md', 'JyJGwzL2CiiFKhkzrLUcx3pgnUb', 'Qrgid0JhEo91HcxEjktcVk0fnbe'),
    ('payment-04', 'payment-interview/04-idempotency.md', 'I0a5wc7LUibRArkMkUHct0swnvc', 'DTCsdol7JoPH8hxKznfcHTAInUc'),
    ('payment-05', 'payment-interview/05-mq-recovery.md', 'CDXkw84eWiDj5ZkzitPcs1PhnIh', 'PHvqdJ10bob22mxCHpccJHKgn4b'),
    ('payment-06', 'payment-interview/06-expiration.md', 'Kc9EwXMzni8ttTkYgk6cxSFknSd', 'AU7EdZEQ7oZRHexUSYicaQU5nHe'),
    ('payment-07', 'payment-interview/07-evidence.md', 'KsqfwyvRZiYtsnkB09HcBsQWnpe', 'RMlTdRE77oLPyBxAE9GcuUuTnwh'),
]
REDIS_GUIDES = ['01-read-path.md', '02-consistency.md', '03-concurrency.md', '04-failure-recovery.md', '05-negative-cache.md']
snapshot_links = set()
link_checks = []


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def resolve_link(base, target, wiki_map):
    if re.match(r'https?://', target):
        return target
    path, _, anchor = target.partition('#')
    if not path:
        return '#' + anchor
    resolved = (base.parent / path).resolve()
    assert resolved.is_relative_to(ROOT) and resolved.exists(), (base, target)
    link_checks.append({'guide': str(base.relative_to(ROOT)), 'target': target})
    if resolved in wiki_map:
        return WIKI + wiki_map[resolved] + ('#' + anchor if anchor else '')
    relative = resolved.relative_to(ROOT).as_posix()
    # Newly authored study guides / the verification record are linked at their
    # final published commit. Existing application/evidence links stay fixed.
    result = subprocess.run(['git', 'cat-file', '-e', f'{SOURCE_COMMIT}:{relative}'], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode == 0:
        revision = SOURCE_COMMIT
        snapshot_links.add(relative)
    else:
        revision = 'main'
    return f'https://github.com/jasper507/E-commerce/blob/{revision}/{quote(relative, safe="/")}' + ('#' + anchor if anchor else '')


def inline(text, base, wiki_map):
    # Protect links and inline code before applying emphasis.
    pieces = []
    pattern = re.compile(r'\[([^\]]+)\]\(([^)]+)\)|`([^`]+)`')
    pos = 0
    for m in pattern.finditer(text):
        pieces.append(html.escape(text[pos:m.start()]))
        if m[3] is not None:
            pieces.append('<b>' + html.escape(m[3]) + '</b>')
        else:
            url = resolve_link(base, m[2], wiki_map)
            pieces.append('<a href="' + html.escape(url, quote=True) + '">' + html.escape(m[1]) + '</a>')
        pos = m.end()
    pieces.append(html.escape(text[pos:]))
    return re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', ''.join(pieces))


def render(base, wiki_map):
    lines = base.read_text(encoding='utf-8').splitlines()
    title = next(line[2:].strip() for line in lines if line.startswith('# '))
    segments, boards, blocks = [], [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.startswith('# '):
            i += 1
            continue
        marker = re.fullmatch(r'<!-- BOARD:([A-Za-z0-9]+) -->', line)
        if marker:
            segments.append('\n'.join(blocks))
            blocks = []
            boards.append(marker[1])
        elif line.startswith('<!--'):
            pass
        elif line.startswith('!['):
            # Local preview is paired with the protected board marker.
            pass
        elif line.startswith('```'):
            language = line[3:].strip() or 'text'
            code = []
            i += 1
            while i < len(lines) and not lines[i].startswith('```'):
                code.append(lines[i])
                i += 1
            assert i < len(lines), ('Unclosed code block', base)
            blocks.append(f'<pre lang="{html.escape(language, quote=True)}"><code>{html.escape(chr(10).join(code))}</code></pre>')
        elif re.match(r'^#{2,4} ', line):
            level = len(line) - len(line.lstrip('#')) - 1
            blocks.append(f'<h{level}>' + inline(line[level+2:], base, wiki_map) + f'</h{level}>')
        elif line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].startswith('|'):
                cells = [x.strip() for x in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r'[-: ]+', c) for c in cells):
                    rows.append(cells)
                i += 1
            width = len(rows[0])
            assert all(len(row) == width for row in rows), ('Ragged table', base, rows)
            def row_html(row, tag):
                return '<tr>' + ''.join(f'<{tag}><p>{inline(c, base, wiki_map)}</p></{tag}>' for c in row) + '</tr>'
            blocks.append('<table><thead>' + row_html(rows[0], 'th') + '</thead><tbody>' + ''.join(row_html(r, 'td') for r in rows[1:]) + '</tbody></table>')
            continue
        elif re.match(r'(?:\d+\. |[-*] )', line):
            ordered = bool(re.match(r'\d+\. ', line))
            pattern = r'\d+\. ' if ordered else r'[-*] '
            tag = 'ol' if ordered else 'ul'
            items = []
            while i < len(lines) and re.match(pattern, lines[i]):
                items.append('<li>' + inline(re.sub('^' + pattern, '', lines[i]), base, wiki_map) + '</li>')
                i += 1
            blocks.append(f'<{tag}>' + ''.join(items) + f'</{tag}>')
            continue
        elif line.startswith('> '):
            blocks.append('<blockquote>' + inline(line[2:], base, wiki_map) + '</blockquote>')
        elif line.strip() == '---':
            blocks.append('<hr/>')
        else:
            blocks.append('<p>' + inline(line, base, wiki_map) + '</p>')
        i += 1
    segments.append('\n'.join(blocks))
    for segment in segments:
        ET.fromstring('<doc>' + segment + '</doc>')
    return title, segments, boards


def main():
    creates_path = BASE / 'redis-created.json'
    created = read_json(creates_path) if creates_path.exists() else []
    wiki_map = {(ROOT / 'docs/learning' / guide).resolve(): node for _, guide, node, _ in PAGE_GUIDES}
    for guide, info in zip(REDIS_GUIDES, created):
        wiki_map[(ROOT / 'docs/learning/redis-interview' / guide).resolve()] = info['node_token']
    candidates = BASE / 'candidates'
    candidates.mkdir(exist_ok=True)
    plans = []
    for name, guide, node, doc in PAGE_GUIDES:
        source = ROOT / 'docs/learning' / guide
        before = read_json(BASE / 'before' / (name + '.json'))['data']['document']
        original = ET.fromstring('<doc>' + before['content'] + '</doc>')
        original_boards = [e.get('token') for e in original if e.tag == 'whiteboard']
        title, segments, boards = render(source, wiki_map)
        assert boards == original_boards, (name, 'Board order or count changed', boards, original_boards)
        protected = [{'token': e.get('token'), 'block_id': e.get('id'), 'xml': ET.tostring(e, encoding='unicode')} for e in original if e.tag == 'whiteboard']
        content_parts = []
        for idx, segment in enumerate(segments):
            path = candidates / f'{name}-segment-{idx}.xml'
            path.write_text(segment + '\n', encoding='utf-8')
            content_parts.append(segment)
            if idx < len(boards):
                content_parts.append(f'<p>原生画板位置：{boards[idx]}（保留原块，不重新生成）。</p>')
        profile = '<title>' + html.escape(title) + '</title>\n' + '\n'.join(content_parts)
        (candidates / f'{name}-profile.xml').write_text(profile + '\n', encoding='utf-8')
        plans.append({'name': name, 'guide': guide, 'node': node, 'doc': doc, 'base_revision': before['revision_id'], 'segments': len(segments), 'protected_boards': protected})
    workspaces = read_json(BASE / 'draft-workspaces.json')
    for index, (guide, workspace) in enumerate(zip(REDIS_GUIDES, workspaces), 1):
        source = ROOT / 'docs/learning/redis-interview' / guide
        title, segments, boards = render(source, wiki_map)
        assert not boards
        profile = '<title>' + html.escape(workspace['title']) + '</title>\n' + segments[0]
        draft = BASE / workspace['draft_path']
        if draft.parent.exists():
            draft.write_text(profile + '\n', encoding='utf-8')
        (candidates / f'redis-{index:02}-body.xml').write_text(segments[0] + '\n', encoding='utf-8')
        (candidates / f'redis-{index:02}-profile.xml').write_text(profile + '\n', encoding='utf-8')
    (BASE / 'publication-plan.json').write_text(json.dumps({'source_commit': SOURCE_COMMIT, 'existing_pages': plans, 'new_redis_pages': workspaces, 'existing_node_moves': 0, 'existing_board_mutations': 0}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (BASE / 'local-link-checks.json').write_text(json.dumps({'local_links': link_checks, 'fixed_snapshot_paths': sorted(snapshot_links)}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'existing_pages': len(plans), 'new_pages': len(workspaces), 'protected_boards': sum(len(p['protected_boards']) for p in plans), 'local_links': len(link_checks)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
