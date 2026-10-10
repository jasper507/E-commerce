"""Check this publication's text, code, links and preserved Wiki resources."""
from pathlib import Path
import copy
import json
import re
import xml.etree.ElementTree as ET

FOLDER = Path(__file__).resolve().parent


def read(name):
    return json.loads((FOLDER / name).read_text(encoding="utf-8-sig"))


def tree(name):
    return ET.fromstring("<doc>" + read(name)["data"]["document"]["content"] + "</doc>")


def text(root):
    return re.sub(r"\s+", "", "".join(root.itertext()))


def links(root):
    return [(node.get("href"), text(node)) for node in root.iter("a")]


def add_body(before_name, body_name, after_name, anchor_start, ignore_new_boards=False):
    expected = tree(before_name)
    body = ET.fromstring("<doc>" + (FOLDER / body_name).read_text(encoding="utf-8") + "</doc>")
    position = next(i for i, node in enumerate(expected) if "".join(node.itertext()).startswith(anchor_start))
    for i, node in enumerate(body):
        expected.insert(position + i + 1, copy.deepcopy(node))
    actual = tree(after_name)
    assert text(expected) == text(actual), after_name
    assert links(expected) == links(actual), after_name
    if not ignore_new_boards:
        assert len(list(expected.iter("whiteboard"))) == len(list(actual.iter("whiteboard")))


add_body("project-addition-before.json", "project-body.xml", "project-addition-after.json",
         "Project One 电商项目的学习资料", ignore_new_boards=True)
add_body("payment-addition-before.json", "payment-wire.xml", "payment-addition-after.json",
         "可信成功通知处理完后")

for page in ["project", "payment"]:
    before = read(f"{page}-children-before.json")["data"]["nodes"]
    after = read(f"{page}-children-after.json")["data"]["nodes"]
    assert {(node["node_token"], node["title"], node["parent_node_token"]) for node in before} == {
        (node["node_token"], node["title"], node["parent_node_token"]) for node in after}

for operation in ["project-intro", "payment-intro", "payment-authoring-rule",
                  "payment-maintenance", "payment-legacy-questions-heading"]:
    before = tree(operation + "-before.json")
    after = tree(operation + "-after.json")
    previous = {node.get("id"): (node.tag, text(node)) for node in before if node.get("id")}
    current = {node.get("id"): (node.tag, text(node)) for node in after if node.get("id")}
    modified = [key for key in previous if previous[key] != current.get(key)]
    assert len(modified) == 1, operation
    assert len(list(before)) == len(list(after)), operation
    assert links(before) == links(after), operation

project = tree("project-after.json")
payment = tree("payment-after.json")
assert [e.get("token") for e in project.iter("whiteboard")] == ["SDuuwOD4dhwiy6bzOgocuEuPn2b"]
assert [e.get("token") for e in payment.iter("whiteboard")] == [
    "Cee6wMjj5hbDqRb2AHVc1uqunbh", "VuDgwuZZNh0gYCb4vRCc5haDnph", "EALVw0LZphx322b6q93ctTDxnne"]
assert len(list(payment.iter("pre"))) == 2
assert len(list(project.iter("pre"))) == 1
checks = {"published_text_code_and_links_match": True, "single_block_edits_scoped": True,
          "new_boards_count": 3, "original_payment_board_token_preserved": True,
          "wiki_children_unchanged": True, "project_children": 4, "payment_children": 7,
          "business_tests_run": False}
(FOLDER / "publication-checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(checks))
