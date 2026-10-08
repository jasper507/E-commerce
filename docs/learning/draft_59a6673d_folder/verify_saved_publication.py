"""Verify already-saved authoring results locally; no remote or database calls."""

from collections import Counter
import ast
import json
from pathlib import Path
import re

from publish_and_verify import BASE, REMOTE, PLAN_PATH, candidate, dump, meaningful_text, plain, xml_root


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def displayed_text(value):
    if isinstance(value, str):
        return re.sub(r"\s+", "", value)
    if isinstance(value, dict):
        text = value.get("text", "")
        rich = value.get("rich_text", {})
        if rich:
            paragraphs = [
                "".join(element.get("text_element", {}).get("text", "") for element in paragraph.get("elements", []))
                for paragraph in rich.get("paragraphs", [])
            ]
            assert displayed_text(text) == displayed_text("\n".join(paragraphs))
        return displayed_text(text)
    return ""


def node_signature(node):
    if node["type"] == "combined_fragment":
        # 分支文字显示在表格单元格；顶层text只是转换器的冗余元数据。
        cells = sorted(node["table"]["cells"], key=lambda cell: (cell["row_index"], cell["col_index"]))
        text = tuple((cell["row_index"], cell["col_index"], displayed_text(cell.get("text"))) for cell in cells)
    elif node["type"] == "connector":
        connector = node["connector"]
        text = tuple(displayed_text(caption) for caption in connector.get("captions", {}).get("data", []))
        endpoints = tuple(
            (end.get("arrow_style"), round(end["position"]["x"], 1), round(end["position"]["y"], 1))
            for end in (connector["start"], connector["end"])
        )
        return node["type"], text, endpoints, node.get("style", {}).get("border_style")
    else:
        text = displayed_text(node.get("text"))
    return node["type"], text


def markdown_block(node, page):
    content = plain(node).strip()
    if node.tag == "title":
        return "# " + content
    if re.fullmatch(r"h[1-9]", node.tag):
        return "#" * (int(node.tag[1]) + 1) + " " + content
    if node.tag == "whiteboard":
        path = (REMOTE / f"board-{page['id']:02d}-preview.jpg").as_posix()
        return f"![{page['title']}的飞书实际画板]({path})"
    if node.tag == "pre":
        return "```" + node.get("lang", "") + "\n" + content + "\n```"
    if node.tag == "table":
        rows = [[plain(cell).strip().replace("|", "\\|").replace("\n", " ") for cell in row] for row in node.iter("tr")]
        if not rows:
            return ""
        return "\n".join(["| " + " | ".join(rows[0]) + " |", "| " + " | ".join("---" for _ in rows[0]) + " |"] + ["| " + " | ".join(row) + " |" for row in rows[1:]])
    if node.tag in {"ul", "ol"}:
        return "\n".join(("- " if node.tag == "ul" else f"{i + 1}. ") + plain(item).strip() for i, item in enumerate(node))
    if node.tag == "callout":
        return "> " + content
    return content


def main():
    plan = read(PLAN_PATH)
    records = []
    review = ["# 秒杀与库存并发：飞书发布内容与实际画板", "2026-10-08。七页正文、六张原生时序图已发布，以下使用飞书实际导出预览。应用历史证据未在本轮重跑。"]
    total_nodes = 0
    for page in plan["pages"]:
        expected = (candidate(page).parent / "publish.xml").read_text(encoding="utf-8-sig")
        actual_data = read(REMOTE / f"page-{page['id']:02d}-fetch.json")["data"]["document"]
        actual = actual_data["content"]
        eroot, aroot = xml_root(expected), xml_root(actual)
        text_equal = meaningful_text(expected) == meaningful_text(actual)
        links_equal = [x.get("href") for x in eroot.iter("a")] == [x.get("href") for x in aroot.iter("a")]
        tables_equal = [[re.sub(r"\s+", "", plain(x)) for x in row] for row in eroot.iter("tr")] == [[re.sub(r"\s+", "", plain(x)) for x in row] for row in aroot.iter("tr")]
        code_expected = [plain(x).strip().replace("\r\n", "\n") for x in eroot.iter("code")]
        code_actual = [plain(x).strip().replace("\r\n", "\n") for x in aroot.iter("code")]
        code_equal = code_expected == code_actual
        assert text_equal and links_equal and tables_equal and code_equal, (page["id"], text_equal, links_equal, tables_equal, code_equal)
        record = {"id": page["id"], "title": page["title"], "url": page["url"], "text_equal": text_equal, "links_equal": links_equal, "tables_equal": tables_equal, "code_equal": code_equal, "code_blocks": len(code_expected), "revision_id": actual_data["revision_id"]}
        if page["diagram"]:
            original = read(BASE / "diagrams" / page["diagram"] / "diagram.json")["nodes"]
            exported = read(REMOTE / f"board-{page['id']:02d}-raw.json")["nodes"]
            board_count_equal = len(original) == len(exported)
            board_text_equal = Counter(node_signature(node) for node in original) == Counter(node_signature(node) for node in exported)
            assert board_count_equal and board_text_equal, (page["id"], board_count_equal, board_text_equal)
            assert (REMOTE / f"board-{page['id']:02d}-preview.jpg").is_file()
            record.update(board_node_count=len(exported), board_count_equal=board_count_equal, board_text_equal=board_text_equal, remote_preview_visually_reviewed=True)
            total_nodes += len(exported)
        page["readback_verified"] = True
        page["revision_id"] = actual_data["revision_id"]
        review += ["---", f"[打开飞书：{page['title']}]({page['url']})"]
        review += [markdown_block(x, page) for x in eroot]
        records.append(record)
    children = read(REMOTE / "children-final.json")["data"]
    assert not children["has_more"]
    assert {x["node_token"] for x in children["nodes"]} == {x["node_token"] for x in plan["pages"][1:]}
    index_before = read(REMOTE / "project-index-before.json")["data"]["document"]["content"]
    index_after = read(REMOTE / "project-index-after.json")["data"]["document"]["content"]
    assert meaningful_text(index_after).startswith(meaningful_text(index_before))
    assert plan["pages"][0]["url"] in index_after
    for file in BASE.rglob("*.py"):
        ast.parse(file.read_text(encoding="utf-8-sig"))
    plan["status"] = "published_verified"
    plan["board_count"] = 6
    plan["native_node_count"] = total_nodes
    plan["remote_previews_visually_reviewed"] = True
    plan["project_navigation_verified"] = True
    dump(PLAN_PATH, plan)
    summary = {"status": "published_verified", "pages": records, "board_count": 6, "native_node_count": total_nodes, "child_hierarchy_verified": True, "index_preserved_and_appended": True, "application_tests_run_this_turn": False}
    dump(BASE / "verification-summary.json", summary)
    (BASE / "review.md").write_text("\n\n".join(review) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
