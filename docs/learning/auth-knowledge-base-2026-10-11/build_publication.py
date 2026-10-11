"""Render five initialized authentication guides and verify exact source excerpts."""
from pathlib import Path
import html
import importlib.util
import json
import re
import subprocess
import xml.etree.ElementTree as ET

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
SOURCE = 'a5d4b04995552a340a471d31e68973e89f67e54c'
spec = importlib.util.spec_from_file_location('renderer', BASE.parent/'kb-interview-restructure-2026-10-11/build_publication.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
renderer.SOURCE_COMMIT = SOURCE


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def main():
    pages = read(BASE/'draft-workspaces.json')
    created = read(BASE/'created.json') if (BASE/'created.json').exists() else []
    wiki_map = {(ROOT/'docs/learning'/guide).resolve(): node
                for _, guide, node, _ in renderer.PAGE_GUIDES}
    wiki_map.update({(ROOT/'docs/learning'/guide).resolve(): node for guide, node in [
        ('flash-sale-current-overview-study-guide.md','B49NwtiePiCqqFkz61kc4ac9n79'),
        ('search-current-overview-study-guide.md','KxWQwuGFsiqO3Wk4m7TcZxXBnih'),
        ('mysql-current-overview-study-guide.md','CNaVwwMJsidse6k5d7zc2VLSnEf')]})
    for page in created:
        guide = next(p['guide'] for p in pages if p['key'] == page['key'])
        wiki_map[(ROOT/'docs/learning'/guide).resolve()] = page['node_token']
    source_files = subprocess.check_output(['git','ls-tree','-r','--name-only',SOURCE,
        'backend/apps/accounts','backend/apps/ordering','backend/apps/payments',
        'backend/apps/promotions','frontend/src/api/auth.ts'],cwd=ROOT).decode().splitlines()
    source_files = [p for p in source_files if p.endswith(('.py','.ts')) and '/tests/' not in p and '/migrations/' not in p]
    snapshots = {p:subprocess.check_output(['git','show',f'{SOURCE}:{p}'],cwd=ROOT).decode('utf-8') for p in source_files}
    links, snippets = [], []
    norm = lambda text: re.sub(r'\s+','',text)
    (BASE/'candidates').mkdir(exist_ok=True)
    for page in pages:
        source = ROOT/'docs/learning'/page['guide']
        text = source.read_text(encoding='utf-8')
        assert '如果重新做' in text and 'auth-interview-knowledge-base-publication.md' in text
        for target in re.findall(r'\]\(([^)]+)\)',text):
            if target.startswith(('https://','http://')):
                fixed = re.search(r'/blob/([0-9a-f]{40})/([^#]+)#L(\d+)',target)
                if fixed:
                    assert fixed[1] == SOURCE, ('Unexpected source snapshot',source,target)
                    original = subprocess.check_output(['git','show',f'{SOURCE}:{fixed[2]}'],cwd=ROOT).decode('utf-8')
                    assert 0 < int(fixed[3]) <= len(original.splitlines()), ('Invalid source line',target)
                continue
            path,_,anchor = target.partition('#')
            resolved = (source.parent/path).resolve()
            assert resolved.is_relative_to(ROOT) and resolved.exists(), (source,target)
            links.append({'guide':page['guide'],'target':target})
        for language, code in re.findall(r'```(python|typescript|ts)\n(.*?)\n```',text,re.S):
            matches = [p for p,full in snapshots.items() if norm(code) in norm(full)]
            assert len(matches) == 1, (page['key'],'Core excerpt mismatch',matches)
            snippets.append({'guide':page['guide'],'language':language,'source':matches[0],'commit':SOURCE})
        _,segments,boards = renderer.render(source,wiki_map)
        assert len(boards) == bool(page['board']) and len(segments) == len(boards)+1
        body = '\n<whiteboard type="blank"></whiteboard>\n'.join(segments)+'\n'
        profile = '<title>'+html.escape(page['title'])+'</title>\n'+body
        ET.fromstring('<doc>'+profile+'</doc>')
        draft = BASE/page['draft_path'].replace('\\','/')
        assert draft.parent.exists(), 'Initialize with CLI first'
        draft.write_text(profile,encoding='utf-8')
        (BASE/'candidates'/(page['key']+'-body.xml')).write_text(body,encoding='utf-8')
        (BASE/'candidates'/(page['key']+'-profile.xml')).write_text(profile,encoding='utf-8')
    result = {'status':'passed','source_commit':SOURCE,'guides':len(pages),
              'local_links':len(links),'core_snippets':len(snippets),'links':links,
              'source_excerpts':snippets,'fixed_snapshot_paths':sorted(renderer.snapshot_links)}
    (BASE/'local-checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('status','guides','local_links','core_snippets')}))


if __name__ == '__main__':
    main()
