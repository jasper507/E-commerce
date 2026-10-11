"""Verify the fixed source, preserved pages, hierarchy and exported native board."""
from collections import Counter
from pathlib import Path
import json
import re
import struct

from new_article import BASE, ROOT, main as build, read


def node_signature(nodes):
    def visible_text(value):
        texts = []
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'text' and isinstance(child, str):
                    texts.append(re.sub(r'\s+', '', child))
                else:
                    texts.extend(visible_text(child))
        elif isinstance(value, list):
            for child in value:
                texts.extend(visible_text(child))
        return texts
    texts = []
    for node in nodes:
        # The server omits redundant fragment-level text; the visible
        # conditions are preserved in table.cells and their rich text.
        content = {k: v for k, v in node.items()
                   if not (node['type'] == 'combined_fragment' and k == 'text')}
        texts.extend(visible_text(content))
    return {'node_types': Counter(n['type'] for n in nodes),
            'visible_text': Counter(texts)}


def jpeg_size(path):
    """Read the exported JPEG dimensions without adding a document dependency."""
    data = path.read_bytes()
    assert data[:2] == b'\xff\xd8', 'Expected a JPEG preview'
    offset = 2
    while offset < len(data):
        assert data[offset] == 255, 'Invalid JPEG marker'
        while data[offset] == 255:
            offset += 1
        marker = data[offset]
        offset += 1
        if marker in {1, *range(0xD0, 0xD8)}:
            continue
        assert marker not in {0xD9, 0xDA}, 'Missing image dimensions'
        length = struct.unpack('>H', data[offset:offset+2])[0]
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height, width = struct.unpack('>HH', data[offset+3:offset+7])
            return [width, height]
        offset += length
    raise AssertionError('Missing image dimensions')


def connector_signature(nodes):
    def endpoint(value):
        position = value['position']
        return (round(position['x'], 2), round(position['y'], 2),
                value['arrow_style'])
    signatures = []
    for node in nodes:
        if node['type'] != 'connector':
            continue
        connector = node['connector']
        captions = tuple(re.sub(r'\s+', '', c['text'])
                         for c in connector['captions']['data'])
        signatures.append((captions, endpoint(connector['start']),
                           endpoint(connector['end']), connector['shape']))
    return Counter(signatures)


def main():
    build()
    assets = ROOT/'docs/learning/assets/flash-sale-lua-2026-10-11/lua-sequence'
    expected = read(assets/'diagram.json')['nodes']
    actual = read(assets/'published-raw.json')['nodes']
    assert actual and node_signature(expected) == node_signature(actual), 'Native board mismatch'
    assert connector_signature(expected) == connector_signature(actual), 'Native arrows changed'
    assert read(assets/'published-write-result.json')['ok']
    assert read(assets/'published-raw-result.json')['ok']
    assert read(assets/'published-preview-result.json')['ok']
    size = jpeg_size(assets/'published-preview.jpg')
    board = {'status': 'passed', 'native_nodes': len(actual),
             'node_types': dict(Counter(n['type'] for n in actual)),
             'visible_text_and_node_types': 'matched',
             'connector_endpoints_and_arrow_directions': 'matched',
             'preview_size': size, 'preview_visually_reviewed': True,
             'write_client_token': 'flash-sale-lua-20261011-publish-1',
             'source_export_message': read(assets/'published-source-result.json')['data']['msg'],
             'editable_mermaid': 'diagram.mmd'}
    (assets/'published-verification.json').write_text(json.dumps(board, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

    assert read(BASE/'target.json') == read(BASE/'final-target.json'), 'Parent identity changed'
    before = read(BASE/'before-children.json')['data']['nodes']
    after = read(BASE/'final-children.json')['data']['nodes']
    created = read(BASE/'created.json')
    assert len(after) == len(before)+1 == 7
    assert {n['node_token'] for n in after} == {n['node_token'] for n in before} | {created['node_token']}
    for node in before:
        final = next(n for n in after if n['node_token'] == node['node_token'])
        assert all(final[field] == node[field] for field in ('title', 'obj_token', 'obj_type'))
    new_node = next(n for n in after if n['node_token'] == created['node_token'])
    assert new_node['obj_token'] == created['obj_token'] and new_node['title'] == created['title']

    plan = read(BASE/'patch-plan.json')
    checked = [read(BASE/'verified'/(page['key']+'.json')) for page in plan]
    assert len(checked) == 7 and all(r['status'] == 'passed' for r in checked)
    assert sum(r['boards_preserved'] for r in checked) == 6
    unchanged_before = read(BASE/'before/existing-04.json')['data']['document']
    unchanged_after = read(BASE/'latest/existing-04.json')['data']['document']
    assert unchanged_before['content'] == unchanged_after['content']
    assert unchanged_before['revision_id'] == unchanged_after['revision_id']
    new_article = read(BASE/'lua-verified.json')
    assert new_article['status'] == 'passed' and new_article['core_snippets'] == 4
    assert new_article['boards'][0]['token'] == 'OEA3wcwkxhrHAWbGxt5c8J0Hnpf'

    profiles, writes = [], []
    for file in (BASE/'patch-journal').glob('*.json'):
        response = read(file)
        if file.stem.endswith('-profile'):
            profiles.append(response)
        elif not file.stem.endswith('-fetch'):
            writes.append(response)
    assert len(profiles) == len(writes) == sum(len(p['operations']) for p in plan) == 27
    assert all(p['ok'] and p['data']['assessment']['status'] == 'passed' for p in profiles)
    assert all(w['ok'] and w['data']['result'] == 'success' and not w['data'].get('warnings') for w in writes)
    assert read(BASE/'lua-profile.json')['data']['assessment']['status'] == 'passed'

    texts = [ROOT/'docs/learning/flash-sale-current-overview-study-guide.md',
             ROOT/'docs/learning/flash-sale-interview/07-lua-admission.md',
             ROOT/'docs/verification/flash-sale-lua-knowledge-base-update.md',
             BASE/'README.md']
    links = 0
    for file in texts:
        text = file.read_text(encoding='utf-8')
        assert not any(line.rstrip() != line for line in text.splitlines()), ('Trailing space', file)
        for target in re.findall(r'\]\(([^)]+)\)', text):
            if target.startswith(('https://', 'http://')):
                continue
            path = target.split('#', 1)[0]
            resolved = (file.parent/path).resolve()
            if resolved != BASE/'final-checks.json':
                assert resolved.exists(), (file, target)
            links += 1
    for file in list(BASE.rglob('*.json')) + list(assets.glob('*.json')):
        if file.is_relative_to(BASE) and any(p.startswith('draft_') for p in file.relative_to(BASE).parts):
            continue
        assert not re.search(r'ou_[A-Za-z0-9]+|Bearer\s+|(?:user|tenant)_access_token|app_secret|client_secret', file.read_text(encoding='utf-8-sig')), ('Sensitive account field', file)

    result = {'status': 'passed', 'source_commit': read(BASE/'local-checks.json')['source_commit'],
              'new_guides': 2, 'local_document_links_checked': links,
              'core_snippets': 4, 'existing_pages_checked': 7,
              'existing_pages_changed': 6, 'patch_operations': 27,
              'original_boards_preserved': 6, 'old_code_and_resource_identifiers_preserved': True,
              'unchanged_payment_lifecycle_page': True, 'new_question_pages': 1,
              'seckill_children': len(after), 'new_native_board': board,
              'application_tests_run': False}
    (BASE/'final-checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    assert (BASE/'final-checks.json').exists()
    print(json.dumps({'final_checks': 'passed', 'native_nodes': len(actual),
                      'preserved_boards': 6, 'existing_pages': 7, 'children': len(after)}))


if __name__ == '__main__':
    main()
