"""Check exact Wiki hierarchy, prose, source fragments and generated native board."""
from collections import Counter
import importlib.util
import json
import re
from build_publication import BASE,ROOT,SOURCE,main as build,read
from inspect_publication import main as inspect

spec=importlib.util.spec_from_file_location('board_checks',BASE.parent/'auth-knowledge-base-2026-10-11/final_checks.py')
checks=importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


def main():
    build()
    inspect()
    created=read(BASE/'created.json')
    page=read(BASE/'deployment-verified.json')
    assert read(BASE/'deployment-profile.json')['data']['assessment']['status']=='passed'
    assert page['source_fragments']==3 and len(page['boards'])==1
    before=read(BASE/'project-before-children.json')['data']['nodes']
    current=read(BASE/'project-final-children.json')['data']['nodes']
    children=read(BASE/'deployment-final-children.json')['data']['nodes']
    assert len(current)==len(before)+1==8 and not children
    assert {n['node_token'] for n in current}=={n['node_token'] for n in before}|{created['node_token']}
    for node in before:
        actual=next(n for n in current if n['node_token']==node['node_token'])
        assert all(actual[f]==node[f] for f in ('title','obj_token','obj_type','parent_node_token'))
    new=next(n for n in current if n['node_token']==created['node_token'])
    assert new['title']==created['title'] and new['obj_token']==created['obj_token']
    assert new['parent_node_token']==created['parent_node_token']
    assert read(BASE/'project-verified.json')['status']=='passed'

    folder=ROOT/'docs/learning/assets/deployment-request-chain-2026-10-11/request-sequence'
    geometry=read(folder/'geometry-check.json')['data']
    assert geometry['metadata']['nodeCount']==60
    assert geometry['check']['errors']==geometry['check']['warnings']==0
    expected=read(folder/'diagram.json')['nodes']
    actual=read(folder/'published-raw.json')['nodes']
    assert len(actual)==len(expected)==60
    assert checks.node_signature(expected)==checks.node_signature(actual),'Native content mismatch'
    assert checks.arrow_signature(expected)==checks.arrow_signature(actual),'Native arrows changed'
    for operation in ('before','write','raw','preview','source'):
        assert read(folder/('published-'+operation+'-result.json'))['ok']
    assert read(folder/'published-before-result.json')['data']['msg']=='whiteboard is empty'
    board={'status':'passed','token':page['boards'][0]['token'],'native_nodes':len(actual),
           'node_types':dict(Counter(n['type'] for n in actual)),
           'visible_text_node_types_and_arrow_directions':'matched',
           'actual_preview_dimensions':checks.jpeg_size(folder/'published-preview.jpg'),
           'local_and_remote_previews_visually_reviewed':True,
           'idempotent_token':'deployment-request-chain-20261011-publish-1',
           'source_export_message':read(folder/'published-source-result.json')['data']['msg'],
           'editable_mermaid':'diagram.mmd'}
    (folder/'published-verification.json').write_text(json.dumps(board,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

    links=0
    paths=[ROOT/'docs/learning/deployment-request-chain-study-guide.md',BASE/'README.md',
           ROOT/'docs/verification/deployment-request-chain-knowledge-base-publication.md',
           ROOT/'docs/learning/README.md',ROOT/'docs/learning/project-current-overview-study-guide.md']
    for path in paths:
        text=path.read_text(encoding='utf-8')
        assert not any(line.rstrip()!=line for line in text.splitlines()),('Trailing space',path)
        for target in re.findall(r'\]\(([^)]+)\)',text):
            if target.startswith(('https://','http://')):
                continue
            resolved=(path.parent/target.split('#',1)[0]).resolve()
            if resolved!=BASE/'final-checks.json':
                assert resolved.exists(),(path,target)
            links+=1
    for path in list(BASE.rglob('*.json'))+list(folder.rglob('*.json')):
        if path.is_relative_to(BASE) and any(p.startswith('draft_') for p in path.relative_to(BASE).parts):
            continue
        text=path.read_text(encoding='utf-8-sig')
        assert not re.search(r'ou_[A-Za-z0-9]+|Bearer[ \t]+[A-Za-z0-9._~+/=-]{16,}|(?:tenant|user)_access_token|app_secret|client_secret',text),('Sensitive field',path)
        json.loads(text)
    result={'status':'passed','source_commit':SOURCE,'guides':1,'source_fragments':3,
            'local_document_links_checked':links,'remote_page_headings':page['headings'],
            'remote_page_links':page['links'],'remote_pages':1,'new_page':created['node_token'],
            'deployment_children':0,'project_children':8,'all_existing_project_nodes_preserved':True,
            'project_modified_paragraphs':1,'all_original_project_resources_preserved':True,
            'native_board':board,'local_geometry_check':geometry['check'],
            'application_tests_run':False,'services_or_database_accessed':False}
    (BASE/'final-checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'final_checks':'passed','guides':1,'source_fragments':3,'native_nodes':60,'existing_nodes':'preserved'}))


if __name__=='__main__':
    main()
