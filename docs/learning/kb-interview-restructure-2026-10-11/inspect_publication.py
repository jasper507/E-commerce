"""Check protected resources, plan sibling edits, and verify read-back content."""
import argparse
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

BASE = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def document(path):
    result = read(path)
    assert result['ok'], result
    return result['data']['document']


def root(content):
    return ET.fromstring('<doc>' + content + '</doc>')


def boards(tree):
    return [(e.get('id'), e.get('token'), ET.tostring(e, encoding='unicode'))
            for e in tree.iter('whiteboard')]


def identifiers(element):
    if element.get('id'):
        return [element.get('id')]
    # List wrappers are serialization-only; their items are sibling blocks.
    assert element.tag in ('ul', 'ol'), element.tag
    result = []
    for child in element:
        assert child.tag == 'li' and child.get('id'), ET.tostring(child)
        result.append(child.get('id'))
    return result


def segments(tree):
    result, current = [], []
    for element in tree:
        if element.tag == 'title':
            continue
        if element.tag == 'whiteboard':
            result.append(current)
            current = []
        else:
            current.extend(identifiers(element))
    result.append(current)
    return result


def checked_tree(page, latest_path):
    before = document(BASE / 'before' / (page['key'] + '.json'))
    latest = document(latest_path)
    old, new = root(before['content']), root(latest['content'])
    assert boards(old) == boards(new), 'Protected board changed'
    assert ET.tostring(old.find('title')) == ET.tostring(new.find('title')), 'Title changed'
    forbidden = {'img', 'source', 'sheet', 'bitable', 'synced_reference',
                 'syncedReference', 'synced_source', 'cite', 'chat_card', 'task'}
    assert not {e.tag for e in new.iter()} & forbidden, 'Unexpected protected resource'
    assert len(segments(new)) == page['segments'], 'Segment count changed'
    return latest, new


def signature(tree):
    # Ignore generated IDs, layout widths, list grouping and whitespace rendering.
    for parent in list(tree.iter()):
        for child in list(parent):
            if child.tag in ('title', 'whiteboard'):
                parent.remove(child)
    text = re.sub(r'\s+', '', ''.join(tree.itertext()))
    headings = [(e.tag, re.sub(r'\s+', '', ''.join(e.itertext())))
                for e in tree.iter() if re.fullmatch(r'h[1-9]', e.tag)]
    links = [(e.get('href'), ''.join(e.itertext())) for e in tree.iter('a')]
    code = [re.sub(r'\s+', '', ''.join(e.itertext())) for e in tree.iter('pre')]
    return {'text': text, 'headings': headings, 'links': links, 'code': code}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['plan', 'plan-new', 'verify', 'verify-new'])
    parser.add_argument('key')
    parser.add_argument('latest')
    parser.add_argument('--segment', type=int)
    args = parser.parse_args()
    plan = read(BASE / 'publication-plan.json')
    pages = [dict(p, key=p['name']) for p in plan['existing_pages']]
    pages.extend({'key': 'redis-' + str(i).zfill(2),
                  'body': 'candidates/redis-' + str(i).zfill(2) + '-body.xml'}
                 for i in range(1, 6))
    page = next(p for p in pages if p['key'] == args.key)
    latest = document(args.latest)
    tree = root(latest['content'])
    if args.command in ('verify-new', 'plan-new'):
        candidate = root((BASE / page['body']).read_text(encoding='utf-8'))
    else:
        latest, tree = checked_tree(page, args.latest)
        candidate = root(''.join((BASE / 'candidates' / (args.key + '-segment-' + str(i) + '.xml')).read_text(encoding='utf-8')
                                 for i in range(page['segments'])))
    if args.command in ('plan', 'plan-new'):
        index = args.segment if args.command == 'plan' else 0
        ids = segments(tree)[index]
        content_path = ('candidates/' + args.key + '-segment-' + str(index) + '.xml'
                        if args.command == 'plan' else page['body'])
        content = (BASE / content_path).read_text(encoding='utf-8').strip()
        operation = {'revision': latest['revision_id'], 'content': content_path}
        if ids:
            operation.update(command='block_replace' if content else 'block_delete',
                             block=','.join(ids), count=len(ids))
        elif content:
            anchor = tree.find('title').get('id') if index == 0 else boards(tree)[index - 1][0]
            operation.update(command='block_insert_after', block=anchor)
        else:
            operation.update(command='skip')
        print(json.dumps(operation, ensure_ascii=False))
    else:
        expected, actual = signature(candidate), signature(tree)
        for field in expected:
            if expected[field] != actual[field]:
                debug = {'expected': expected[field], 'actual': actual[field]}
                (BASE / ('mismatch-' + args.key + '-' + field + '.json')).write_text(
                    json.dumps(debug, ensure_ascii=False, indent=2), encoding='utf-8')
                raise AssertionError('Read-back mismatch: ' + args.key + ' ' + field)
        assert all(e.get('seq') is None for e in tree.iter()
                   if re.fullmatch(r'h[1-9]', e.tag)), 'Unexpected automatic heading numbering'
        print(json.dumps({'key': args.key, 'revision': latest['revision_id'],
                          'text_characters': len(actual['text']),
                          'links': len(actual['links']), 'code_blocks': len(actual['code']),
                          'protected_boards': len(boards(root(latest['content']))),
                          'status': 'passed'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
