"""Change only the project reading paragraph and preserve every other resource."""
import copy
import json
import sys
import xml.etree.ElementTree as ET
from build_publication import BASE, ROOT, renderer, read
from inspect_publication import inspector


def tree(path):
    return inspector.root(read(path)['data']['document']['content'])


def target(root):
    matches = [e for e in root if e.tag == 'p' and ''.join(e.itertext()).startswith('阅读顺序是：')]
    assert len(matches) == 1, 'Ambiguous reading paragraph'
    return matches[0]


def protected(root):
    tags = {'whiteboard','img','source','cite','sheet','bitable','synced_source','synced_reference'}
    return [(n.tag,ET.tostring(n,encoding='unicode')) for n in root.iter() if n.tag in tags]


def main():
    before,latest = tree(BASE/'project-before.json'),tree(BASE/'project-latest.json')
    created = read(BASE/'created.json')
    source = ROOT/'docs/learning/project-current-overview-study-guide.md'
    line = next(l for l in source.read_text(encoding='utf-8').splitlines() if l.startswith('阅读顺序是：'))
    assert 'auth-current-overview-study-guide.md' in line and '认证与部署Wiki栏目仍待' not in line
    wiki_map = {(ROOT/'docs/learning'/guide).resolve(): node for guide,node in [
        ('auth-current-overview-study-guide.md',created[0]['node_token']),
        ('search-current-overview-study-guide.md','KxWQwuGFsiqO3Wk4m7TcZxXBnih'),
        ('mysql-current-overview-study-guide.md','CNaVwwMJsidse6k5d7zc2VLSnEf')]}
    replacement_node = ET.fromstring('<p>'+renderer.inline(line,source,wiki_map)+'</p>')
    # Only the new authentication link is new. Preserve the original
    # targets for every existing navigation label, including fixed evidence.
    old_links = {''.join(n.itertext()):n.get('href') for n in target(before).iter('a')}
    for link in replacement_node.iter('a'):
        label = ''.join(link.itertext())
        if label in old_links:
            link.set('href',old_links[label])
    replacement = ET.tostring(replacement_node,encoding='unicode')
    expected = copy.deepcopy(before)
    old = target(expected)
    index = list(expected).index(old)
    expected.remove(old)
    expected.insert(index,ET.fromstring(replacement))
    assert protected(before) == protected(latest), 'Existing project resources changed'
    assert ET.tostring(before.find('title')) == ET.tostring(latest.find('title')), 'Project title changed'
    if sys.argv[1] == 'plan':
        assert inspector.signature(copy.deepcopy(before)) == inspector.signature(copy.deepcopy(latest)), 'Concurrent project edit'
        (BASE/'candidates/root-navigation.xml').write_text(replacement+'\n',encoding='utf-8')
        print(json.dumps({'block':target(latest).get('id'),'revision':read(BASE/'project-latest.json')['data']['document']['revision_id']}))
    else:
        assert inspector.signature(copy.deepcopy(expected)) == inspector.signature(copy.deepcopy(latest)), 'Unexpected project content changes'
        result = {'status':'passed','revision':read(BASE/'project-latest.json')['data']['document']['revision_id'],
                  'modified_paragraphs':1,'all_other_content_preserved':True,
                  'original_boards_preserved':len(before.findall('.//whiteboard')),
                  'all_original_resource_identifiers_preserved':True}
        (BASE/'project-verified.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(result))


if __name__=='__main__':
    main()
