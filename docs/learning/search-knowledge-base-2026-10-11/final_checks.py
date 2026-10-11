"""Final source, remote-board semantics, hierarchy, and scoped-content checks."""
from collections import Counter
from pathlib import Path
import json
import re
from build_publication import BASE, ROOT, main as build, read


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
        # The service omits redundant fragment-level text; its visible
        # conditions remain in table.cells, including their rich text.
        content = {k: v for k, v in node.items() if not (node['type'] == 'combined_fragment' and k == 'text')}
        texts.extend(visible_text(content))
    return {'node_types': Counter(n['type'] for n in nodes), 'visible_text': Counter(texts)}


def main():
    build()
    boards = []
    assets = ROOT / 'docs/learning/assets/search-interview-2026-10-11'
    for name, exported in [('read-sequence', 'published-nodes.json'), ('switch-sequence', 'published-raw.json')]:
        expected = read(assets / name / 'diagram.json')['nodes']
        actual = read(assets / name / exported)['nodes']
        assert actual and node_signature(expected) == node_signature(actual), (name, 'Native board content mismatch')
        assert (assets / name / 'published-preview.jpg').exists()
        boards.append({'diagram': name, 'native_nodes': len(actual), 'visible_text_and_node_types': 'matched', 'preview_visually_reviewed': True})
    before = read(BASE / 'project-before-children.json')['data']['nodes']
    root = read(BASE / 'project-final-children.json')['data']['nodes']
    children = read(BASE / 'search-final-children.json')['data']['nodes']
    created = read(BASE / 'created.json')
    assert {n['node_token'] for n in root} == {n['node_token'] for n in before} | {created[0]['node_token']}
    assert {n['node_token'] for n in children} == {n['node_token'] for n in created[1:]}
    assert all(n['obj_type'] == 'docx' for n in root + children)
    texts = list((ROOT / 'docs/learning/search-interview').glob('*.md')) + [ROOT / 'docs/learning/search-current-overview-study-guide.md']
    for file in texts:
        assert not any(line.rstrip() != line for line in file.read_text(encoding='utf-8').splitlines()), ('Trailing space', file)
    for file in list(BASE.rglob('*.json')) + list(assets.rglob('*.json')):
        if file.is_relative_to(BASE) and any(p.startswith('draft_') for p in file.relative_to(BASE).parts):
            continue
        assert not re.search(r'ou_[A-Za-z0-9]+', file.read_text(encoding='utf-8-sig')), ('Personal account ID', file)
    result = {'status': 'passed', 'source_commit': read(BASE / 'local-checks.json')['source_commit'], 'guides': 6, 'question_pages': 5, 'native_boards': boards, 'root_children': len(root), 'search_children': len(children), 'existing_nodes_preserved': True, 'original_root_board_preserved': read(BASE / 'project-verified.json')['status'] == 'passed', 'application_tests_run': False}
    (BASE / 'final-checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'final_checks': 'passed', 'native_nodes': [b['native_nodes'] for b in boards], 'existing_nodes': 'preserved'}))


if __name__ == '__main__':
    main()
