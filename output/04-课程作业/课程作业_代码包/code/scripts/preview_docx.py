"""
把生成的 docx 转成 HTML 预览，用于人工核对排版效果。

这不是给用户看的交付物，纯粹是自查工具 —— 本机没有 LibreOffice / Word，
无法直接把 docx 渲染成图，所以用 docx 的 XML 结构重建一个近似视度的 HTML，
再用浏览器截图检查标题层级、表格换行、图片尺寸是否合理。

用法：
    .venv/Scripts/python.exe scripts/preview_docx.py
    .venv/Scripts/python.exe scripts/preview_docx.py 某个文件.docx [输出.html]
"""

import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

BASE = Path(__file__).resolve().parent.parent

# 默认预览主说明书；命令行传参可预览其它 docx
DOCX = (Path(sys.argv[1]) if len(sys.argv) > 1
        else BASE / "output" / "01-交付物" / "非遗数字资产分级确权与溯源系统_项目交付说明书.docx")
OUT = (Path(sys.argv[2]) if len(sys.argv) > 2
       else DOCX.with_suffix(".preview.html"))


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def cell_style(cell):
    """读取底纹色，用于还原表头与提示框。"""
    tcPr = cell._tc.find(qn("w:tcPr"))
    if tcPr is None:
        return ""
    shd = tcPr.find(qn("w:shd"))
    if shd is None:
        return ""
    fill = shd.get(qn("w:fill"))
    return f' style="background:#{fill}"' if fill and fill != "auto" else ""


def is_picture_cell(cell):
    return bool(cell._tc.findall(".//" + qn("a:blip")))


def render_table(t):
    # 读取 tblGrid 的真实列宽，按比例转成百分比，否则预览会把各列拉成等宽
    grid = t._tbl.find(qn("w:tblGrid"))
    gcols = [int(x.get(qn("w:w"))) for x in grid.findall(qn("w:gridCol"))] if grid is not None else []
    total = sum(gcols) or 1
    colgroup = ""
    if gcols:
        colgroup = "<colgroup>" + "".join(
            f'<col style="width:{w * 100.0 / total:.1f}%">' for w in gcols
        ) + "</colgroup>"

    # 判断是否为提示框（单列且有两段文字）
    if len(t.columns) == 1 and len(t.rows) == 1:
        cell = t.rows[0].cells[0]
        parts = [p.text.strip() for p in cell.paragraphs if p.text.strip()]
        if len(parts) == 2:
            style = cell_style(cell) or ' style="background:#EEF2F7"'
            return (f'<div class="callout"{style}>'
                    f'<div class="ct">{esc(parts[0])}</div>'
                    f'<div class="cb">{esc(parts[1])}</div></div>')
        if len(parts) >= 2 and parts[0].startswith("--") or (parts and parts[0].startswith("#")):
            pass
        # 代码块
        body = "<br>".join(esc(p.text) for p in cell.paragraphs)
        return f'<pre class="code">{body}</pre>'

    rows = []
    for i, row in enumerate(t.rows):
        tds = []
        for cell in row.cells:
            tag = "th" if i == 0 else "td"
            st = cell_style(cell) if i == 0 else ""
            tds.append(f'<{tag}{st}>{esc(cell.text)}</{tag}>')
        rows.append("<tr>" + "".join(tds) + "</tr>")
    return '<table class="dt">' + colgroup + "".join(rows) + "</table>"


def main():
    doc = Document(str(DOCX))

    # 建立 body 元素到对象的映射，以保持段落与表格的原始顺序
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = doc.element.body
    blocks = []
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            blocks.append(Paragraph(child, doc))
        elif child.tag == qn("w:tbl"):
            blocks.append(Table(child, doc))

    html = ['<html><head><meta charset="utf-8"><style>',
            'body{background:#7a7a7a;margin:0;padding:24px;font-family:"Microsoft YaHei",sans-serif}',
            '.page{background:#fff;max-width:780px;margin:0 auto 20px;padding:60px 70px;',
            'box-shadow:0 4px 20px rgba(0,0,0,.4);font-size:13px;line-height:1.75;color:#1f1b18}',
            'h1{font-size:22px;color:#A8322C;margin:26px 0 10px;font-weight:700}',
            'h2{font-size:16px;color:#2C4A6E;margin:20px 0 8px;font-weight:700}',
            'p{margin:6px 0}',
            '.dt{width:100%;border-collapse:collapse;margin:8px 0;font-size:11.5px}',
            '.dt th{background:#A8322C;color:#fff;padding:5px 7px;text-align:left;font-weight:700}',
            '.dt td{border:1px solid #ddd;padding:5px 7px;vertical-align:top}',
            '.dt tr:nth-child(even) td{background:#FAF8F4}',
            '.callout{padding:12px 14px;border-left:5px solid #2C4A6E;margin:10px 0;font-size:12px}',
            '.ct{font-weight:700;color:#2C4A6E;margin-bottom:4px}',
            '.cb{color:#3a3532}',
            '.code{background:#F4F1EC;border:1px solid #D9D2C5;padding:10px;font-size:11px;',
            'font-family:Consolas,monospace;white-space:pre-wrap;margin:8px 0}',
            'img{max-width:100%;display:block;margin:10px auto}',
            '.cap{text-align:center;font-size:10.5px;color:#6B635C;margin:2px 0 14px}',
            'ul{margin:6px 0 6px 20px;padding:0}li{margin:2px 0}',
            '.pb{page-break-after:always;border-top:2px dashed #ccc;margin:20px 0}',
            '</style></head><body><div class="page">',
            ]

    list_open = False
    for b in blocks:
        if isinstance(b, Paragraph):
            style = b.style.name
            text = b.text.strip()

            # 图片
            if b._p.findall(".//" + qn("a:blip")):
                # 取图片关系
                blips = b._p.findall(".//" + qn("a:blip"))
                for blip in blips:
                    rid = blip.get(qn("r:embed"))
                    if rid and rid in doc.part.related_parts:
                        img_part = doc.part.related_parts[rid]
                        import base64
                        b64 = base64.b64encode(img_part.blob).decode()
                        ext = img_part.partname.split(".")[-1]
                        html.append(f'<img src="data:image/{ext};base64,{b64}">')
                continue

            if not text:
                continue

            # 题注（表 x-x / 图 x-x）
            if text.startswith("表 ") or text.startswith("图 ") or text.startswith("（本目录为"):
                html.append(f'<div class="cap">{esc(text)}</div>')
                if text.startswith("表 "):
                    # 题注出现在表后，但 HTML 里表格已输出，视觉上等价
                    pass
                continue

            if style == "Heading 1":
                html.append(f'<h1>{esc(text)}</h1>')
            elif style == "Heading 2":
                html.append(f'<h2>{esc(text)}</h2>')
            elif style in ("List Bullet", "List Bullet 2"):
                if not list_open:
                    html.append("<ul>")
                    list_open = True
                html.append(f'<li>{esc(text)}</li>')
                continue
            else:
                html.append(f'<p>{esc(text)}</p>')

            if list_open:
                html.append("</ul>")
                list_open = False

        else:
            if list_open:
                html.append("</ul>")
                list_open = False
            html.append(render_table(b))

    if list_open:
        html.append("</ul>")

    html.append("</div></body></html>")
    OUT.write_text("".join(html), encoding="utf-8")
    print("预览已生成：" + str(OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
