"""Compare exact planned changes and protect titles, code and legacy resources."""
from pathlib import Path
import copy
import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from new_article import BASE, read

spec=importlib.util.spec_from_file_location('previous',BASE.parent/'kb-interview-restructure-2026-10-11/inspect_publication.py')
previous=importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)


def tree(content):return ET.fromstring('<doc>'+content+'</doc>')


def resources(root):
    tags={'whiteboard','img','source','sheet','bitable','synced_reference','synced_source','cite'}
    return [(n.tag,ET.tostring(n,encoding='unicode')) for n in root.iter() if n.tag in tags]


def main():
    key=sys.argv[2]
    before=read(BASE/'before'/(key+'.json'))['data']['document']
    latest=read(BASE/'latest'/(key+'.json'))['data']['document']
    original,actual=tree(before['content']),tree(latest['content'])
    assert resources(original)==resources(actual),'Existing resources changed'
    assert ET.tostring(original.find('title'))==ET.tostring(actual.find('title')),'Existing title changed'
    assert previous.signature(copy.deepcopy(original))['code']==previous.signature(copy.deepcopy(actual))['code'],'Existing code excerpt changed'
    if sys.argv[1]=='before':
        assert previous.signature(copy.deepcopy(original))==previous.signature(copy.deepcopy(actual)),'Concurrent page content change'
    else:
        expected=ET.fromstring((BASE/'candidates'/(key+'-expected.xml')).read_text(encoding='utf-8'))
        for field,value in previous.signature(copy.deepcopy(expected)).items():
            assert previous.signature(copy.deepcopy(actual))[field]==value,(key,field,'Planned read-back mismatch')
        result={'key':key,'status':'passed','revision':latest['revision_id'],'boards_preserved':len(original.findall('.//whiteboard')),'all_changes_match_plan':True,'old_core_code_preserved':True}
        (BASE/'verified'/(key+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'key':key,'check':sys.argv[1],'status':'passed'}))


if __name__=='__main__':main()
