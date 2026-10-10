"""Build the two scoped DocxXML additions from reviewed local study guides."""
from pathlib import Path
import html
import json
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
FOLDER = Path(__file__).resolve().parent
COMMIT = "4673b9f8ea95664ce4ab886e1a9e791e6ffbd42e"
WIKI = "https://ocnbkcx1ug58.feishu.cn/wiki/"
NEW_GUIDES = {
    "project-current-overview-study-guide.md": WIKI + "WN1jwXDj0iPXm7kwLyHcmneenlb",
    "payment-current-overview-study-guide.md": WIKI + "PCQ7wP9kMikz0eklmNYcH1J1nwh",
}
snapshot_paths = set()


def link_url(base, target):
    if target.startswith("https://"):
        return target
    path, _, anchor = target.partition("#")
    if Path(path).name in NEW_GUIDES:
        return NEW_GUIDES[Path(path).name]
    resolved = (base.parent / path).resolve()
    assert resolved.is_relative_to(ROOT) and resolved.exists(), target
    relative = resolved.relative_to(ROOT).as_posix()
    subprocess.run(["git", "cat-file", "-e", f"{COMMIT}:{relative}"], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    snapshot_paths.add(relative)
    return f"https://github.com/jasper507/E-commerce/blob/{COMMIT}/{relative}" + ("#" + anchor if anchor else "")


def inline(text, base):
    escaped = html.escape(text)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                     lambda m: f'<a href="{html.escape(link_url(base, html.unescape(m[2])), quote=True)}">{m[1]}</a>', escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    return re.sub(r"`([^`]+)`", r"<b>\1</b>", escaped)


def render(base, payment=False):
    text = base.read_text(encoding="utf-8")
    if payment:
        text = text[text.index("## 从点击付款到库存核销"):]
    lines = text.splitlines()
    output = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.startswith("# ") or line.startswith("[归档核对]"):
            i += 1
            continue
        if line.startswith("```"):
            language = line[3:]
            code = []
            i += 1
            while lines[i] != "```":
                code.append(lines[i])
                i += 1
            output.append(f'<pre lang="{language}"><code>{html.escape(chr(10).join(code))}</code></pre>')
        elif line.startswith("## "):
            output.append("<h1>" + inline(line[3:], base) + "</h1>")
        elif line.startswith("!["):
            match = re.fullmatch(r"!\[([^\]]+)\]\(([^)]+)\)", line)
            source = (base.parent / match[2]).with_suffix(".mmd")
            assert source.exists()
            relative = source.relative_to(ROOT).as_posix()
            output.append("<p>" + html.escape(match[1]) + "</p>")
            output.append(f'<whiteboard type="mermaid" path="@{relative}"></whiteboard>')
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [cell.strip() for cell in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r"[-: ]+", cell) for cell in cells):
                    rows.append(cells)
                i += 1
            def row(cells, tag):
                return "<tr>" + "".join(f"<{tag}><p>{inline(cell, base)}</p></{tag}>" for cell in cells) + "</tr>"
            output.append("<table><thead>" + row(rows[0], "th") + "</thead><tbody>" +
                          "".join(row(cells, "td") for cells in rows[1:]) + "</tbody></table>")
            continue
        elif re.match(r"(?:\d+\. |\- )", line):
            ordered = bool(re.match(r"\d+\. ", line))
            tag = "ol" if ordered else "ul"
            items = []
            pattern = r"\d+\. " if ordered else r"\- "
            while i < len(lines) and re.match(pattern, lines[i]):
                items.append("<li>" + inline(re.sub("^" + pattern, "", lines[i]), base) + "</li>")
                i += 1
            output.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue
        else:
            output.append("<p>" + inline(line, base) + "</p>")
        i += 1
    result = "\n".join(output)
    ET.fromstring("<doc>" + result + "</doc>")
    return result


for name, guide, title, payment in [
    ("project", "project-current-overview-study-guide.md", "电商项目", False),
    ("payment", "payment-current-overview-study-guide.md", "支付", True),
]:
    body = render(ROOT / "docs/learning" / guide, payment)
    (FOLDER / f"{name}-body.xml").write_text(body, encoding="utf-8")
    (FOLDER / f"{name}-profile.xml").write_text(f"<title>{title}</title>\n" + body, encoding="utf-8")
(FOLDER / "snapshot-link-checks.json").write_text(json.dumps({"source_commit": COMMIT,
    "verified_paths": sorted(snapshot_paths)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
