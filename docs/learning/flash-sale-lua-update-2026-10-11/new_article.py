"""Render one initialized Lua article and verify its current source excerpts."""
from pathlib import Path
import html
import importlib.util
import json
import re
import subprocess
import xml.etree.ElementTree as ET

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
SOURCE = '96904bcc47562c51a1e4b9cc731f8ed5d40fdb75'
spec = importlib.util.spec_from_file_location('renderer', BASE.parent / 'kb-interview-restructure-2026-10-11/build_publication.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
renderer.SOURCE_COMMIT = SOURCE


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def main():
    source = ROOT / 'docs/learning/flash-sale-interview/07-lua-admission.md'
    overview = ROOT / 'docs/learning/flash-sale-current-overview-study-guide.md'
    wiki_map = {overview.resolve(): 'B49NwtiePiCqqFkz61kc4ac9n79'}
    if (BASE / 'created.json').exists():
        wiki_map[source.resolve()] = read(BASE / 'created.json')['node_token']
    title, segments, boards = renderer.render(source, wiki_map)
    assert boards == ['LUA'] and len(segments) == 2
    body = segments[0] + '\n<whiteboard type="blank"></whiteboard>\n' + segments[1] + '\n'
    title = '07 Lua原子准入：为什么先保护进入事务的尝试'
    profile = '<title>' + html.escape(title) + '</title>\n' + body
    workspace = read(BASE / 'lua-workspace.json')['data']
    draft = BASE / workspace['draft_path'].replace('\\', '/')
    assert draft.parent.exists(), 'Initialize draft with CLI first'
    draft.write_text(profile, encoding='utf-8')
    (BASE / 'candidates').mkdir(exist_ok=True)
    (BASE / 'candidates/lua-body.xml').write_text(body, encoding='utf-8')
    (BASE / 'candidates/lua-profile.xml').write_text(profile, encoding='utf-8')
    files = ['backend/apps/promotions/admission.lua', 'backend/apps/promotions/admission.py', 'backend/apps/promotions/views.py', 'backend/apps/promotions/services.py']
    snapshots = {p: subprocess.check_output(['git','show',f'{SOURCE}:{p}'],cwd=ROOT).decode('utf-8') for p in files}
    snippets = []
    norm = lambda s: re.sub(r'\s+', '', s)
    for language, code in re.findall(r'```(python|lua)\n(.*?)\n```',source.read_text(encoding='utf-8'),re.S):
        matches = [p for p,s in snapshots.items() if norm(code) in norm(s)]
        assert len(matches) == 1, (language, matches)
        snippets.append({'language':language,'source':matches[0],'commit':SOURCE})
    links = []
    for page in (source, overview):
        text = page.read_text(encoding='utf-8')
        assert '如果重新做' in text and 'flash-sale-lua-knowledge-base-update.md' in text
        for target in re.findall(r'\]\(([^)]+)\)',text):
            if target.startswith(('https://','http://')):
                continue
            path,_,anchor = target.partition('#')
            full = (page.parent/path).resolve()
            assert full.exists() and full.is_relative_to(ROOT), (page,target)
            if re.fullmatch(r'L\d+',anchor):
                assert 0 < int(anchor[1:]) <= len(full.read_text(encoding='utf-8').splitlines())
            links.append({'guide':str(page.relative_to(ROOT)),'target':target})
    result = {'status':'passed','source_commit':SOURCE,'guides':2,'local_links':len(links),'core_snippets':snippets,'links':links,'fixed_snapshot_paths':sorted(renderer.snapshot_links)}
    (BASE / 'local-checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'guides':2,'local_links':len(links),'core_snippets':len(snippets)}))


if __name__ == '__main__':
    main()
