"""Compare all read-back text, headings, hyperlinks, snippets, and board counts."""
from pathlib import Path
import copy
import importlib.util
import json
import sys
import xml.etree.ElementTree as ET

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('prior_inspector', BASE.parent / 'kb-interview-restructure-2026-10-11/inspect_publication.py')
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
read, root = prior.read, prior.root


def main():
    key = sys.argv[1]
    page = next(p for p in read(BASE / 'draft-workspaces.json') if p['key'] == key)
    latest = read(BASE / (key + '-latest.json'))['data']['document']
    actual = root(latest['content'])
    expected = root((BASE / 'candidates' / (key + '-profile.xml')).read_text(encoding='utf-8'))
    assert ''.join(actual.find('title').itertext()) == page['title'], 'Title mismatch'
    assert len(actual.findall('.//whiteboard')) == bool(page['board']), 'Board count mismatch'
    expected_sig, actual_sig = prior.signature(copy.deepcopy(expected)), prior.signature(copy.deepcopy(actual))
    for field in expected_sig:
        assert expected_sig[field] == actual_sig[field], ('Read-back mismatch', key, field)
    boards = [{'block_id': e.get('id'), 'token': e.get('token'), 'diagram': page['board']} for e in actual.iter('whiteboard')]
    assert all(p['block_id'] and p['token'] for p in boards), 'Missing board identity'
    result = {'key': key, 'status': 'passed', 'revision': latest['revision_id'], 'headings': len(actual_sig['headings']), 'links': len(actual_sig['links']), 'core_snippets': len(actual_sig['code']), 'boards': boards}
    (BASE / (key + '-verified.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
