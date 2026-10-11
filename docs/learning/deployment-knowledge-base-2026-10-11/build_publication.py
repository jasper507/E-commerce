"""Render the one initialized deployment guide and verify fixed source fragments."""
from pathlib import Path
import html
import importlib.util
import json
import re
import subprocess
import xml.etree.ElementTree as ET

BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[2]
SOURCE='57329d80fd6afd13f78b2f6e2ea45f61a898ea6b'
TITLE='部署与完整请求链：从浏览器到MySQL再返回页面'
spec=importlib.util.spec_from_file_location('renderer',BASE.parent/'kb-interview-restructure-2026-10-11/build_publication.py')
renderer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
renderer.SOURCE_COMMIT=SOURCE


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def main():
    source=ROOT/'docs/learning/deployment-request-chain-study-guide.md'
    text=source.read_text(encoding='utf-8')
    assert 'deployment-request-chain-knowledge-base-publication.md' in text
    assert not re.search(r'^#{1,4}.*(?:如果重新做|重做选择|替代方案)',text,re.M), 'Owner excluded these sections'
    wiki_map={(ROOT/'docs/learning'/guide).resolve():node for _,guide,node,_ in renderer.PAGE_GUIDES}
    wiki_map[(ROOT/'docs/learning/auth-current-overview-study-guide.md').resolve()]='TS8SwlKxKi8gJ5kIjlScjyyFn3d'
    if (BASE/'created.json').exists():
        wiki_map[source.resolve()]=read(BASE/'created.json')['node_token']
    files=['compose.deployment-local.yaml','docker/deployment/nginx.conf',
           'docker/deployment/web.Dockerfile','docker/deployment/frontend.Dockerfile',
           'frontend/vite.config.ts','backend/config/urls.py','backend/config/wsgi.py',
           'backend/apps/catalog/views.py']
    snapshots={path:subprocess.check_output(['git','show',f'{SOURCE}:{path}'],cwd=ROOT).decode('utf-8') for path in files}
    links,fragments=[],[]
    for target in re.findall(r'\]\(([^)]+)\)',text):
        if target.startswith(('https://','http://')):
            fixed=re.search(r'/blob/([0-9a-f]{40})/([^#]+)#L(\d+)',target)
            if fixed:
                assert fixed[1]==SOURCE
                full=subprocess.check_output(['git','show',f'{SOURCE}:{fixed[2]}'],cwd=ROOT).decode('utf-8')
                assert 0<int(fixed[3])<=len(full.splitlines()),target
            continue
        path,_,anchor=target.partition('#')
        full=(source.parent/path).resolve()
        assert full.exists() and full.is_relative_to(ROOT),(source,target)
        links.append(target)
    for language,code in re.findall(r'```(yaml|nginx|dockerfile|python|typescript)\n(.*?)\n```',text,re.S):
        matches=[path for path,full in snapshots.items() if code.strip() in full]
        assert len(matches)==1,(language,'Source fragment mismatch',matches)
        fragments.append({'language':language,'source':matches[0],'commit':SOURCE})
    _,segments,boards=renderer.render(source,wiki_map)
    assert boards==['REQUESTCHAIN'] and len(segments)==2
    body='\n<whiteboard type="blank"></whiteboard>\n'.join(segments)+'\n'
    profile='<title>'+html.escape(TITLE)+'</title>\n'+body
    ET.fromstring('<doc>'+profile+'</doc>')
    workspace=read(BASE/'draft-workspace.json')['data']
    draft=BASE/workspace['draft_path'].replace('\\','/')
    assert draft.parent.exists(), 'Initialize through CLI before writing XML'
    draft.write_text(profile,encoding='utf-8')
    (BASE/'candidates').mkdir(exist_ok=True)
    (BASE/'candidates/deployment-body.xml').write_text(body,encoding='utf-8')
    (BASE/'candidates/deployment-profile.xml').write_text(profile,encoding='utf-8')
    result={'status':'passed','source_commit':SOURCE,'guides':1,'local_links':len(links),
            'source_fragments':fragments,'links':links,'owner_exclusions_respected':True,
            'fixed_snapshot_paths':sorted(renderer.snapshot_links)}
    (BASE/'local-checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'guide':1,'local_links':len(links),'source_fragments':len(fragments),'status':'passed'}))


if __name__=='__main__':
    main()
