"""Read-back check for all text, links, headings, snippets and the new board."""
import copy
import importlib.util
import json
from build_publication import BASE,TITLE,read

spec=importlib.util.spec_from_file_location('inspector',BASE.parent/'kb-interview-restructure-2026-10-11/inspect_publication.py')
inspector=importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspector)


def main():
    latest=read(BASE/'deployment-latest.json')['data']['document']
    actual=inspector.root(latest['content'])
    expected=inspector.root((BASE/'candidates/deployment-profile.xml').read_text(encoding='utf-8'))
    assert ''.join(actual.find('title').itertext())==TITLE
    signature=inspector.signature(copy.deepcopy(actual))
    assert signature==inspector.signature(copy.deepcopy(expected)), 'Page read-back mismatch'
    boards=[{'token':n.get('token'),'block_id':n.get('id')} for n in actual.iter('whiteboard')]
    assert len(boards)==1 and boards[0]['token'] and boards[0]['block_id']
    result={'status':'passed','revision':latest['revision_id'],'headings':len(signature['headings']),
            'links':len(signature['links']),'source_fragments':len(signature['code']),'boards':boards,
            'native_board_verification':'required separately'}
    (BASE/'deployment-verified.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':
    main()
