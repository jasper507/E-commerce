"""Local draft checks and preparation. Remote publication uses publish_native.ps1."""

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

BASE = Path(__file__).resolve().parent
REPO = BASE.parents[2]
REMOTE = BASE / "remote"
MAP_PATH = BASE / "page-map.json"
PLAN_PATH = BASE / "publication-plan.json"
PROJECT_NODE = "WN1jwXDj0iPXm7kwLyHcmneenlb"
PROJECT_URL = "https://ocnbkcx1ug58.feishu.cn/wiki/" + PROJECT_NODE
ORIGINAL_NAV = "<p>导航将在发布时补齐。</p>"


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cli(args, record, cwd=BASE):
    assert args[:2] == ["docs", "+script"] and args[2:4] == ["--command", "parse"], "Local Profile Check only"
    env = os.environ.copy()
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    powershell = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not powershell:
        raise RuntimeError("PowerShell runtime was not found")
    argument_path = REMOTE / f"{record}-args.json"
    dump(argument_path, args)
    result = subprocess.run([powershell, "-NoProfile", "-File", str(BASE / "run_cli.ps1"), str(argument_path)], cwd=cwd, env=env, encoding="utf-8", capture_output=True)
    try:
        data = json.loads(result.stdout or result.stderr)
    except json.JSONDecodeError:
        raise RuntimeError(f"CLI {args[:2]} returned exit={result.returncode}; response was not JSON")
    dump(REMOTE / f"{record}.json", data)
    if result.returncode or data.get("ok") is not True:
        error = data.get("error", {})
        raise RuntimeError(f"CLI {args[:2]} failed exit={result.returncode}: {error.get('type')} / {error.get('message')}; {error.get('hint', '')}")
    if data.get("identity") not in (None, "user"):
        raise RuntimeError("Expected user identity")
    return data


def xml_root(text):
    return ET.fromstring("<doc>" + text + "</doc>")


def plain(node):
    if node.tag == "br":
        return "\n" + (node.tail or "")
    text = node.text or ""
    for child in node:
        text += plain(child)
    return text + (node.tail or "")


def meaningful_text(text):
    node = xml_root(text)
    values = []
    for child in node:
        if child.tag == "whiteboard":
            continue
        values.append(re.sub(r"\s+", "", plain(child)))
    return "".join(values)


def page_location(page):
    if page["id"] == 0:
        return BASE.parent, f"{BASE.name}/draft.xml"
    return BASE, f"{page['workspace']}/draft.xml"


def candidate(page):
    return BASE / page["workspace"] / "draft.xml"


def load_plan():
    if PLAN_PATH.exists():
        return json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    return {
        "status": "local_draft", "date": "2026-10-08", "source_commit": "18ffbe2f2bf674d31f3d2dc055976aedd41af010",
        "approved_parent": PROJECT_URL, "space_id": SPACE, "pages": json.loads(MAP_PATH.read_text(encoding="utf-8")),
        "renderer_identity": "GPT", "board_format": "Mermaid converted to editable OpenAPI nodes",
        "historical_application_evidence_only": True, "application_tests_run_this_turn": False,
    }


def preflight(plan):
    records = []
    for page in plan["pages"]:
        text = candidate(page).read_text(encoding="utf-8-sig")
        root = xml_root(text)
        titles = root.findall("title")
        assert len(titles) == 1
        page["title"] = titles[0].text
        assert text.count(ORIGINAL_NAV) == 1
        assert len(root.findall("whiteboard")) == (1 if page["diagram"] else 0)
        assert all(x.get("seq") == "auto" for x in root if re.fullmatch(r"h[1-9]", x.tag))
        source_links = []
        for a in root.iter("a"):
            href = a.get("href", "")
            if "/blob/18ffbe2" in href:
                relative = href.split("18ffbe2f2bf674d31f3d2dc055976aedd41af010/", 1)[1].split("#", 1)[0]
                local = REPO / relative
                assert local.is_file(), relative
                line = re.search(r"#L(\d+)(?:-L(\d+))?", href)
                if line:
                    last = int(line.group(2) or line.group(1))
                    assert 1 <= int(line.group(1)) <= last <= len(local.read_text(encoding="utf-8-sig").splitlines())
                source_links.append(href)
        cwd, relative = page_location(page)
        result = cli(["docs", "+script", "--command", "parse", "--content", "@./" + relative, "--as", "user", "--format", "json"], f"page-{page['id']:02d}-parse", cwd)
        assert result["data"]["assessment"]["status"] == "passed", result["data"].get("diagnostics")
        page["draft_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        records.append({"id": page["id"], "title": page["title"], "profile": "passed", "source_links": len(source_links), "draft_sha256": page["draft_sha256"]})
    dump(BASE / "local-check.json", records)
    plan["status"] = "local_checked"
    dump(PLAN_PATH, plan)
    print(json.dumps(records, ensure_ascii=False))


def navigation(plan, page):
    items = [f'<a href="{PROJECT_URL}">电商项目</a>']
    if page["id"]:
        items.append(f'<a href="{plan["pages"][0]["url"]}">秒杀总览</a>')
    if page["id"] > 1:
        prev = plan["pages"][page["id"] - 1]
        items.append(f'<a href="{prev["url"]}">上一篇：{html.escape(prev["title"])}</a>')
    if page["id"] < 6:
        nxt = plan["pages"][page["id"] + 1]
        items.append(f'<a href="{nxt["url"]}">下一篇：{html.escape(nxt["title"])}</a>')
    nav = "<p>" + "；".join(items) + "。</p>"
    if page["id"] == 0:
        nav += "<ul>" + "".join(f'<li><a href="{x["url"]}">{html.escape(x["title"])}</a></li>' for x in plan["pages"][1:]) + "</ul>"
    return nav


def prepare(plan):
    for page in plan["pages"]:
        nav = navigation(plan, page)
        folder = candidate(page).parent
        (folder / "navigation.xml").write_text(nav, encoding="utf-8")
        text = candidate(page).read_text(encoding="utf-8").replace(ORIGINAL_NAV, nav)
        (folder / "publish.xml").write_text(text, encoding="utf-8")
        page["publish_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        cwd, relative = page_location(page)
        relative = relative.replace("draft.xml", "publish.xml")
        result = cli(["docs", "+script", "--command", "parse", "--content", "@./" + relative, "--as", "user", "--format", "json"], f"page-{page['id']:02d}-publish-parse", cwd)
        assert result["data"]["assessment"]["status"] == "passed"
    plan["status"] = "prepared"
    dump(PLAN_PATH, plan)
    print("Prepared seven checked candidates with real navigation.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["preflight", "prepare"])
    args = parser.parse_args()
    plan = load_plan()
    globals()[args.command](plan)
