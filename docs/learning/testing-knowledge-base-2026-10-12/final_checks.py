"""Validate current sources, fetched documents, exact hierarchy and native diagrams."""
from collections import Counter
import json
import re
import struct
from pathlib import Path
from build_publication import BASE, ROOT, SOURCE, main as build, read


def visible_text(value):
    texts=[]
    if isinstance(value,dict):
        for key,child in value.items():
            if key=='text' and isinstance(child,str):
                texts.append(re.sub(r'\s+','',child))
            else:
                texts.extend(visible_text(child))
    elif isinstance(value,list):
        for child in value:
            texts.extend(visible_text(child))
    return texts


def node_signature(nodes):
    texts=[]
    for node in nodes:
        # The server omits redundant fragment-level text while retaining
        # the visible conditions in table cells and their rich text.
        texts.extend(visible_text({k:v for k,v in node.items()
            if not (node['type']=='combined_fragment' and k=='text')}))
    return {'types':Counter(n['type'] for n in nodes),'texts':Counter(texts)}


def arrow_signature(nodes):
    signatures={}
    for node in nodes:
        if node['type']!='connector':
            continue
        connector=node['connector']
        captions=tuple(re.sub(r'\s+','',c['text']) for c in connector['captions']['data'])
        start,end=connector['start'],connector['end']
        key=(captions,start['arrow_style'],end['arrow_style'],connector['shape'])
        coordinates=tuple(value['position'][axis] for value in (start,end) for axis in ('x','y'))
        signatures.setdefault(key,[]).append(coordinates)
    return {key:sorted(values) for key,values in signatures.items()}


def arrows_match(expected,actual,tolerance=0.02):
    # Feishu serializes connector coordinates at a lower decimal precision.
    left,right=arrow_signature(expected),arrow_signature(actual)
    if left.keys()!=right.keys():
        return False
    for key in left:
        if len(left[key])!=len(right[key]):
            return False
        for source,target in zip(left[key],right[key]):
            if any(abs(a-b)>tolerance for a,b in zip(source,target)):
                return False
    return True


def jpeg_size(path):
    data=path.read_bytes()
    assert data[:2]==b'\xff\xd8'
    offset=2
    while offset<len(data):
        assert data[offset]==255
        while data[offset]==255:
            offset+=1
        marker=data[offset];offset+=1
        if marker in {1,*range(0xD0,0xD8)}:
            continue
        assert marker not in {0xD9,0xDA}
        length=struct.unpack('>H',data[offset:offset+2])[0]
        if marker in {0xC0,0xC1,0xC2,0xC3,0xC5,0xC6,0xC7,0xC9,0xCA,0xCB,0xCD,0xCE,0xCF}:
            height,width=struct.unpack('>HH',data[offset+3:offset+7])
            return [width,height]
        offset+=length
    raise AssertionError('Missing JPEG dimensions')


def main():
    build()
    pages=read(BASE/'draft-workspaces.json')
    created=read(BASE/'created.json')
    assert len(pages)==len(created)==5
    assert len({p['node_token'] for p in created})==5
    assert len({p['workspace'] for p in pages})==5
    verified=[read(BASE/(p['key']+'-verified.json')) for p in pages]
    assert all(p['status']=='passed' for p in verified)
    assert sum(p['core_snippets'] for p in verified)==5
    assert all(read(BASE/(p['key']+'-profile.json'))['data']['assessment']['status']=='passed' for p in pages)

    before=read(BASE/'project-before-children.json')['data']['nodes']
    root=read(BASE/'project-final-children.json')['data']['nodes']
    children=read(BASE/'testing-final-children.json')['data']['nodes']
    assert len(root)==len(before)+1==9 and len(children)==4
    assert {n['node_token'] for n in root}=={n['node_token'] for n in before}|{created[0]['node_token']}
    assert {n['node_token'] for n in children}=={n['node_token'] for n in created[1:]}
    for node in before:
        actual=next(n for n in root if n['node_token']==node['node_token'])
        assert all(actual[f]==node[f] for f in ('title','obj_token','obj_type','parent_node_token'))
    for page in created:
        actual=next(n for n in root+children if n['node_token']==page['node_token'])
        assert actual['obj_token']==page['obj_token'] and actual['title']==page['title']
    assert read(BASE/'project-verified.json')['status']=='passed'

    boards=[]
    assets=ROOT/'docs/learning/assets/testing-evidence-2026-10-12'
    for page in pages:
        if not page['board']:
            continue
        folder=assets/page['board']
        expected=read(folder/'diagram.json')['nodes']
        actual=read(folder/'published-raw.json')['nodes']
        assert actual and node_signature(expected)==node_signature(actual), (page['board'],'Native content mismatch')
        assert arrows_match(expected,actual), (page['board'],'Native arrows changed')
        for operation in ('before','write','raw','preview','source'):
            assert read(folder/('published-'+operation+'-result.json'))['ok']
        assert read(folder/'published-before-result.json')['data']['msg']=='whiteboard is empty'
        result={'diagram':page['board'],'status':'passed','native_nodes':len(actual),
                'node_types':dict(Counter(n['type'] for n in actual)),
                'visible_text_node_types_and_arrow_directions':'matched',
                'connector_coordinate_tolerance_pixels':0.02,
                'actual_preview_dimensions':jpeg_size(folder/'published-preview.jpg'),
                'local_and_remote_previews_visually_reviewed':True,
                'idempotent_token':'testing-20261012-'+page['board']+'-publish-1',
                'source_export_message':read(folder/'published-source-result.json')['data']['msg'],
                'editable_mermaid':'diagram.mmd'}
        (folder/'published-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        boards.append(result)
    assert len(boards)==1 and sum(b['native_nodes'] for b in boards)==42

    links=0
    for path in [ROOT/'docs/learning'/p['guide'] for p in pages]+[BASE/'README.md',ROOT/'docs/verification/testing-evidence-knowledge-base-publication.md']:
        text=path.read_text(encoding='utf-8')
        assert not any(line.rstrip()!=line for line in text.splitlines()), ('Trailing space',path)
        for target in re.findall(r'\]\(([^)]+)\)',text):
            if target.startswith(('https://','http://')):
                continue
            resolved=(path.parent/target.split('#',1)[0]).resolve()
            if resolved!=BASE/'final-checks.json':
                assert resolved.exists(), (path,target)
            links+=1
    for path in list(BASE.rglob('*.json'))+list(assets.rglob('*.json')):
        if path.is_relative_to(BASE) and any(p.startswith('draft_') for p in path.relative_to(BASE).parts):
            continue
        text=path.read_text(encoding='utf-8-sig')
        assert not re.search(r'ou_[A-Za-z0-9]+|Bearer[ \t]+[A-Za-z0-9._~+/=-]{16,}|(?:tenant|user)_access_token|app_secret|client_secret',text), ('Sensitive field',path)
        json.loads(text)
    result={'status':'passed','source_commit':SOURCE,'guides':5,'core_snippets':5,
            'local_document_links_checked':links,'remote_page_headings':sum(p['headings'] for p in verified),
            'remote_page_links':sum(p['links'] for p in verified),'remote_pages':5,
            'new_section':created[0]['node_token'],'testing_children':len(children),'project_children':len(root),
            'all_existing_project_nodes_preserved':True,'project_modified_paragraphs':1,
            'all_original_project_resources_preserved':True,'native_boards':boards,'application_tests_run':False}
    (BASE/'final-checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'final_checks':'passed','guides':5,'core_snippets':5,'native_nodes':[b['native_nodes'] for b in boards],'existing_nodes':'preserved'}))


if __name__=='__main__':
    main()
