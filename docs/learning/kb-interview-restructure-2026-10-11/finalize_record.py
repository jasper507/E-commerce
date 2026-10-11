"""Check the final hierarchy and consolidate this run's publication journal."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def nodes(path):
    value = read(path)
    assert value['ok'] and not value['data']['has_more']
    return value['data']['nodes']


fields = ('node_token', 'obj_token', 'title', 'parent_node_token', 'obj_type', 'node_type')
def identities(items):
    return sorted(tuple(item.get(f, '') for f in fields) for item in items)


for name in ('project', 'payment'):
    assert identities(nodes(BASE / 'before' / (name + '-children.json'))) == identities(nodes(BASE / (name + '-final-children.json'))), name
redis = nodes(BASE / 'redis-final-children.json')
created = read(BASE / 'redis-created.json')
assert len(redis) == len(created) == 5
assert {(n['node_token'], n['obj_token'], n['title']) for n in redis} == {(n['node_token'], n['obj_token'], n['title']) for n in created}
assert all(n['parent_node_token'] == 'O4iUwSvl1i4Je7keOL0ci1iAnLf' for n in redis)
checks = [read(p) for p in sorted(BASE.glob('*-verified.json'))]
assert len(checks) == 15 and all(p['status'] == 'passed' for p in checks)
assert sum(p['protected_boards'] for p in checks) == 13
journal = [{'entry': p.name, 'result': read(p)} for p in sorted((BASE / 'journal').glob('*.json'))]
destination = BASE / 'publication-journal.jsonl'
destination.write_text(''.join(json.dumps(p, ensure_ascii=False)+'\n' for p in journal), encoding='utf-8')
assert len(destination.read_text(encoding='utf-8').splitlines()) == len(journal)
manifest = {'status': 'complete', 'date': '2026-10-11',
            'source_commit': '29b7944860080ccddedb2c0f0c060c0a27658376',
            'existing_pages_updated': 10, 'new_redis_pages': 5,
            'protected_boards_unchanged': 13, 'root_children': 4,
            'payment_children': 7, 'redis_children': 5,
            'node_moves': 0, 'permission_changes': 0,
            'temporary_draft_cleanup': 'blocked_by_automatic_review',
            'checks': checks, 'journal_entries': len(journal),
            'application_tests_run': False}
(BASE / 'publication-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps({key: manifest[key] for key in ('status', 'existing_pages_updated', 'new_redis_pages', 'protected_boards_unchanged', 'journal_entries')}))
