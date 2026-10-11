"""Replace only the existing reading-order paragraph; protect all other blocks."""
from pathlib import Path
import copy
import json
import sys
import xml.etree.ElementTree as ET
from build_publication import BASE, ROOT, prior, read
from inspect_publication import prior as inspector


def tree(path):
    return ET.fromstring('<doc>' + read(path)['data']['document']['content'] + '</doc>')


def target(root):
    matches = [e for e in root if e.tag == 'p' and ''.join(e.itertext()).startswith('阅读顺序是：')]
    assert len(matches) == 1, 'Ambiguous navigation paragraph'
    return matches[0]


def main():
    before, latest = tree(BASE / 'project-before.json'), tree(BASE / 'project-latest.json')
    created = read(BASE / 'created.json')
    wiki_map = {(ROOT / 'docs/learning/search-current-overview-study-guide.md').resolve(): created[0]['node_token']}
    source = ROOT / 'docs/learning/project-current-overview-study-guide.md'
    line = next(l for l in source.read_text(encoding='utf-8').splitlines() if l.startswith('阅读顺序是：'))
    expected_xml = '<p>' + prior.inline(line, source, wiki_map) + '</p>'
    expected = copy.deepcopy(before)
    old = target(expected)
    position = list(expected).index(old)
    expected.remove(old)
    expected.insert(position, ET.fromstring(expected_xml))
    assert inspector.boards(before) == inspector.boards(latest), 'Original board changed'
    assert ET.tostring(before.find('title')) == ET.tostring(latest.find('title')), 'Original title changed'
    if sys.argv[1] == 'plan':
        assert inspector.signature(copy.deepcopy(before)) == inspector.signature(copy.deepcopy(latest)), 'Root concurrent change'
        (BASE / 'candidates/root-navigation.xml').write_text(expected_xml+'\n', encoding='utf-8')
        print(json.dumps({'block': target(latest).get('id'), 'revision': read(BASE / 'project-latest.json')['data']['document']['revision_id']}))
    else:
        assert inspector.signature(copy.deepcopy(expected)) == inspector.signature(copy.deepcopy(latest)), 'Unexpected root content changes'
        result = {'status': 'passed', 'revision': read(BASE / 'project-latest.json')['data']['document']['revision_id'], 'protected_boards': inspector.boards(latest), 'modified_paragraphs': 1, 'all_other_content_preserved': True}
        (BASE / 'project-verified.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
        print(json.dumps({'root_navigation': 'passed', 'original_board': 'preserved'}))


if __name__ == '__main__':
    main()
