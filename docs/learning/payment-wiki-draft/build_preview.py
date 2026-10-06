"""Build a local review copy and publication manifest; never calls a remote API."""

import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
PAGES = BASE / "pages"
BOARD_MAP = {
    0: ("knowledge-map", "支付学习地图", "mermaid"),
    1: ("data-model", "189元订单的数据关系", "svg"),
    2: ("normal-payment", "正常付款与事务提交", "mermaid"),
    3: ("timeout-query", "超时与查询原付款", "mermaid"),
    5: ("mq-recovery", "MQ故障与人工恢复", "mermaid"),
    6: ("payment-expiration", "付款与订单到期竞争", "mermaid"),
}


def inline(node):
    result = node.text or ""
    for child in node:
        if child.tag == "a":
            result += f"[{inline(child)}]({child.attrib['href']})"
        elif child.tag == "br":
            result += "<br>"
        else:
            result += inline(child)
        result += child.tail or ""
    return result


def markdown(node, page_index):
    if node.tag == "title":
        return "## " + inline(node)
    if node.tag.startswith("h") and node.tag[1:].isdigit():
        return "#" * (int(node.tag[1:]) + 2) + " " + inline(node)
    if node.tag == "p":
        return inline(node)
    if node.tag == "callout":
        return "\n".join("> " + inline(child) for child in node)
    if node.tag in {"ul", "ol"}:
        return "\n".join(
            f"{i + 1}. {inline(child)}" if node.tag == "ol" else "- " + inline(child)
            for i, child in enumerate(node)
        )
    if node.tag == "pre":
        return f"```{node.attrib.get('lang', '')}\n{inline(node.find('code'))}\n```"
    if node.tag == "table":
        rows = []
        for row in node.iter("tr"):
            rows.append("| " + " | ".join(inline(cell).replace("|", "\\|") for cell in row) + " |")
        columns = len(next(node.iter("tr")))
        rows.insert(1, "| " + " | ".join(["---"] * columns) + " |")
        return "\n".join(rows)
    if node.tag == "whiteboard":
        folder, title, _ = BOARD_MAP[page_index]
        path = (BASE / "diagrams" / folder / "diagram.png").as_posix()
        return f"![{title}](/" + path + ")"
    raise ValueError(f"Unsupported preview tag: {node.tag}")


def main():
    plan_path = BASE / "publication-plan.json"
    if plan_path.exists():
        previous = json.loads(plan_path.read_text(encoding="utf-8-sig"))
        if previous.get("space_id") or any(page.get("remote_node_token") for page in previous.get("pages", [])):
            raise RuntimeError("Remote publication records exist; preserve their tokens and update the review separately.")
    page_map = json.loads((PAGES / "page-map.json").read_text(encoding="utf-8-sig"))
    preview = [
        "# Project One：支付与 MQ 知识库预览",
        "保存目标已确认：新建“Project One 电商项目”飞书 Wiki。下面是八页正文与六张图的本地审阅版，远端创建与画板写入仍待 Wiki 授权。",
        "图的 PNG 用于预览；发布时使用原生可编辑画板。已有 Redis 文档保留原位置。历史实验只引用已有证据，本次未重跑应用测试。",
        "阅读顺序：总览 → 数据模型 → 正常付款 → UNKNOWN → 幂等 → MQ恢复 → 到期与退款 → 实验与面试。",
    ]
    manifest = {
        "date": "2026-10-06",
        "space_name": "Project One 电商项目",
        "status": "local_ready_remote_authorization_pending",
        "existing_redis_document": "https://ocnbkcx1ug58.feishu.cn/docx/KKBXdHFQqoZRCNxJv1YcFb29nkf",
        "pages": [],
        "verification": {},
    }
    board_count = 0
    for item in page_map:
        index = item["page_index"]
        path = PAGES / item["draft_path"]
        xml = path.read_text(encoding="utf-8")
        document = ET.fromstring("<document>" + xml + "</document>")
        assert document.findtext("title") == item["title"]
        boards = document.findall("whiteboard")
        assert len(boards) == int(index in BOARD_MAP)
        board_count += len(boards)
        preview.extend(["---"] + [markdown(node, index) for node in document])
        record = {
            "index": index,
            "title": item["title"],
            "parent_page_index": None if index == 0 else 0,
            "xml": path.relative_to(BASE).as_posix(),
            "xml_sha256": hashlib.sha256(xml.encode()).hexdigest(),
            "remote_node_token": None,
            "remote_obj_token": None,
        }
        if index in BOARD_MAP:
            folder, title, input_format = BOARD_MAP[index]
            diagram = BASE / "diagrams" / folder
            suffix = "svg" if input_format == "svg" else "mmd"
            source = diagram / ("diagram." + suffix)
            check = json.loads((diagram / "check-result.json").read_text(encoding="utf-8-sig"))
            assert check["code"] == 0 and check["data"]["check"]["errors"] == 0
            native = json.loads((diagram / "diagram.json").read_text(encoding="utf-8"))
            nodes = native.get("nodes") or native.get("data", {}).get("result", {}).get("nodes")
            assert nodes, folder
            record["whiteboard"] = {
                "title": title,
                "source": source.relative_to(BASE).as_posix(),
                "input_format": input_format,
                "preview": (diagram / "diagram.png").relative_to(BASE).as_posix(),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "idempotent_token": "project-one-payment-20261006-" + folder,
                "remote_token": None,
                "node_count": len(nodes),
                "check_warnings": check["data"]["check"]["warnings"],
            }
        manifest["pages"].append(record)
    assert len(page_map) == 8 and board_count == 6
    excerpts = json.loads((PAGES / "source-excerpts.json").read_text(encoding="utf-8"))
    for excerpt in excerpts:
        lines = (ROOT / excerpt["path"]).read_text(encoding="utf-8").splitlines()
        text = "\n".join(lines[excerpt["first"] - 1 : excerpt["last"]])
        assert hashlib.sha256(text.encode()).hexdigest() == excerpt["sha256"]
    manifest["verification"] = {
        "xml_pages": 8,
        "whiteboards": 6,
        "exact_source_excerpts": len(excerpts),
        "app_tests_rerun": False,
        "remote_writes": False,
        "mindmap_check_note": "Native mind_map nodes contain automatic-layout coordinates; static checker reports 528 overlap warnings. Rendered PNG visually checked and readable; native remote preview remains to be verified.",
    }
    (BASE / "review.md").write_text("\n\n".join(preview) + "\n", encoding="utf-8")
    (BASE / "publication-plan.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["verification"], ensure_ascii=False))


if __name__ == "__main__":
    main()
