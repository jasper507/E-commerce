"""Verify the new Lua article without accepting an empty board as final."""
import copy
import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET

BASE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('previous',BASE.parent/'kb-interview-restructure-2026-10-11/inspect_publication.py')
previous=importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)


def main():
    latest=json.loads((BASE/'lua-latest.json').read_text(encoding='utf-8-sig'))['data']['document']
    actual=ET.fromstring('<doc>'+latest['content']+'</doc>')
    expected=ET.fromstring('<doc>'+(BASE/'candidates/lua-profile.xml').read_text(encoding='utf-8')+'</doc>')
    assert ''.join(actual.find('title').itertext())==''.join(expected.find('title').itertext())
    assert previous.signature(copy.deepcopy(actual))==previous.signature(copy.deepcopy(expected))
    boards=[{'token':n.get('token'),'block_id':n.get('id')} for n in actual.iter('whiteboard')]
    assert len(boards)==1 and boards[0]['token'] and boards[0]['block_id']
    result={'status':'passed','revision':latest['revision_id'],'boards':boards,'headings':len(actual.findall('.//h1'))+len(actual.findall('.//h2')),'links':len(actual.findall('.//a')),'core_snippets':len(actual.findall('.//pre')),'native_board_content_check':'required separately'}
    (BASE/'lua-verified.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':
    main()
