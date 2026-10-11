"""Compare the entire fetched page with its planned text and resource count."""
import copy
import importlib.util
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('inspector', BASE.parent/'kb-interview-restructure-2026-10-11/inspect_publication.py')
inspector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspector)
read = inspector.read


def main():
    key = sys.argv[1]
    page = next(p for p in read(BASE/'draft-workspaces.json') if p['key']==key)
    document = read(BASE/(key+'-latest.json'))['data']['document']
    actual = inspector.root(document['content'])
    expected = inspector.root((BASE/'candidates'/(key+'-profile.xml')).read_text(encoding='utf-8'))
    assert ''.join(actual.find('title').itertext()) == page['title']
    assert inspector.signature(copy.deepcopy(actual)) == inspector.signature(copy.deepcopy(expected)), (key,'Full read-back mismatch')
    boards = [{'block_id':b.get('id'),'token':b.get('token'),'diagram':page['board']} for b in actual.iter('whiteboard')]
    assert len(boards)==bool(page['board']) and all(b['block_id'] and b['token'] for b in boards)
    signature = inspector.signature(actual)
    result = {'key':key,'status':'passed','revision':document['revision_id'],
              'headings':len(signature['headings']),'links':len(signature['links']),
              'core_snippets':len(signature['code']),'boards':boards}
    (BASE/(key+'-verified.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
