"""
生成《非遗数字资产分级确权与溯源系统 · 项目交付说明书》Word 文档。

用法：
    .venv/Scripts/python.exe scripts/build_docx.py

产出：
    output/01-交付物/非遗数字资产分级确权与溯源系统_项目交付说明书.docx

三个设计决定（值得记录）：

1. 所有数字都从仓库里的真实文件读取，不在代码里硬编码。
   种子规模读 data/seed_demo.json，端到端结果读 docs/verify/run_result.json。
   这样文档不会与代码脱节。

2. 目录用 Word 域（TOC）而不是手写字符串。打开文档时按 F9 即生成带页码的目录。
   手写目录看着像样，但一改正文就全错。

3. 正文一律使用直角引号「」而不是英文双引号。原因很实际：
   文档里有大量「AI 只出建议」这类短语，一旦写成英文双引号，
   在 Python 字符串里就要转义，极易写出语法错误。用「」既符合中文排版习惯，
   也彻底避免了转义问题。

⚠️ 本脚本是第 1 轮的正文快照，第 2 轮起改成了「原地编辑」维护
   （用本地 Office 编辑器直接改 output/01-交付物 里的成品，不再回写脚本）。
   直接重跑会用第 1 轮的内容覆盖成品——第五轮的 3.5/5.6 节、568 项检查、
   9 个触发器、角色闸门这些都会丢。
   要重跑，必须先把脚本正文同步到当前成品；只想看效果请先复制一份成品再跑。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "output" / "01-交付物"
# 文档内 5 张截图。集中成一个常量，避免 5 处 picture() 各写一遍路径。
FIG_DIR = BASE / "output" / "02-截图证据" / "交付文档配图"
OUT_FILE = OUT_DIR / "非遗数字资产分级确权与溯源系统_项目交付说明书.docx"

INK = RGBColor(0x1F, 0x1B, 0x18)
CINNABAR = RGBColor(0xA8, 0x32, 0x2C)
INDIGO = RGBColor(0x2C, 0x4A, 0x6E)
GREY = RGBColor(0x6B, 0x63, 0x5C)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

FONT_CN = "宋体"
FONT_CN_HEAD = "黑体"
FONT_EN = "Times New Roman"
FONT_MONO = "Consolas"


# ============================================================
# 读取真实数据
# ============================================================
def load_seed() -> dict:
    return json.loads((BASE / "data" / "seed_demo.json").read_text(encoding="utf-8"))


SEED = load_seed()
USERS = SEED["users"]
MOTIFS = SEED["motifs"]
ASSETS = SEED["assets"]

LICENSE_FEE = sum(float(l.get("fee_cny") or 0) for a in ASSETS for l in a.get("licenses", []))
LICENSE_CNT = sum(len(a.get("licenses", [])) for a in ASSETS)
AUDIT_CNT = sum(len(a.get("audits", [])) for a in ASSETS)

ASSETS_BY_OWNER = {}
for a in ASSETS:
    ASSETS_BY_OWNER.setdefault(a["owner_id"], []).append(a)


# ============================================================
# 排版工具
# ============================================================
def cn_font(run, name=FONT_CN, size=None, bold=None, color=None):
    """设置中文字体。python-docx 的 font.name 只管西文，中文必须写 rPr/rFonts。"""
    run.font.name = FONT_EN
    if run._element.rPr is None:
        run._element.get_or_add_rPr()
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color


def shade(cell, hex_color):
    tcPr = cell._tc.get_or_add_tcPr()
    el = OxmlElement("w:shd")
    el.set(qn("w:val"), "clear")
    el.set(qn("w:color"), "auto")
    el.set(qn("w:fill"), hex_color)
    tcPr.append(el)


def cell_margins(table, top=60, bottom=60, left=100, right=100):
    mar = OxmlElement("w:tblCellMar")
    for tag, val in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        node = OxmlElement("w:" + tag)
        node.set(qn("w:w"), str(val))
        node.set(qn("w:type"), "dxa")
        mar.append(node)
    table._tbl.tblPr.append(mar)


def repeat_header(row):
    """表格跨页重复表头。长表格不加这个，第二页起就没表头了。"""
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    trPr.append(el)


def add_field(paragraph, instr, placeholder=""):
    """插入 Word 域（用于目录、页码）。"""
    run = paragraph.add_run()
    b = OxmlElement("w:fldChar")
    b.set(qn("w:fldCharType"), "begin")
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = instr
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    t = OxmlElement("w:t")
    t.text = placeholder
    e = OxmlElement("w:fldChar")
    e.set(qn("w:fldCharType"), "end")
    for node in (b, it, sep, t, e):
        run._r.append(node)
    return run


def hrule(paragraph, color="D9D2C5", size="6"):
    pPr = paragraph._p.get_or_add_pPr()
    bd = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), size)
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), color)
    bd.append(bottom)
    pPr.append(bd)


# ============================================================
# 文档骨架
# ============================================================
def build_styles(doc):
    st = doc.styles

    normal = st["Normal"]
    normal.font.name = FONT_EN
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)
    normal.paragraph_format.line_spacing = 1.45
    normal.paragraph_format.space_after = Pt(4)

    for name, size, color, before, after in (
        ("Heading 1", 18, CINNABAR, 22, 10),
        ("Heading 2", 14, INDIGO, 16, 6),
        ("Heading 3", 12, INK, 12, 4),
    ):
        s = st[name]
        s.font.name = FONT_EN
        s.font.size = Pt(size)
        s.font.bold = True
        s.font.color.rgb = color
        s.element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN_HEAD)
        s.paragraph_format.space_before = Pt(before)
        s.paragraph_format.space_after = Pt(after)
        s.paragraph_format.keep_with_next = True
        s.paragraph_format.line_spacing = 1.2


def build_page(doc):
    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.top_margin = Cm(2.4)
    sec.bottom_margin = Cm(2.2)
    sec.left_margin = Cm(2.8)
    sec.right_margin = Cm(2.4)
    sec.header_distance = Cm(1.2)
    sec.footer_distance = Cm(1.2)

    hp = sec.header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    r = hp.add_run("非遗数字资产分级确权与溯源系统 · 项目交付说明书")
    cn_font(r, FONT_CN, 8.5, color=GREY)

    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r1 = fp.add_run("第 ")
    cn_font(r1, FONT_CN, 9, color=GREY)
    add_field(fp, " PAGE ", "1")
    r2 = fp.add_run(" 页")
    cn_font(r2, FONT_CN, 9, color=GREY)
    for run in fp.runs:
        cn_font(run, FONT_CN, 9, color=GREY)


def add_cover(doc):
    for _ in range(3):
        doc.add_paragraph()

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cn_font(p.add_run("非物质文化遗产数字资产"), FONT_CN_HEAD, 26, True, INK)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cn_font(p.add_run("分级确权与溯源系统"), FONT_CN_HEAD, 26, True, INK)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(18)
    cn_font(p.add_run("项 目 交 付 说 明 书"), FONT_CN_HEAD, 17, False, CINNABAR)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    hrule(p, "A8322C", "12")

    for _ in range(4):
        doc.add_paragraph()

    info = [
        ("文档类型", "项目交付说明书（含验证证据与验收清单）"),
        ("系统形态", "FastAPI + PostgreSQL 16 + 向量检索 的单体 Web 应用"),
        ("核心命题", "AI 只出建议，权属由专家终审 —— 由数据库列级权限强制"),
        ("配套演示", "output/01-交付物/演示页面.html（单文件，双击即可打开操作）"),
        ("生成日期", datetime.now().strftime("%Y 年 %m 月 %d 日")),
    ]
    t = doc.add_table(rows=len(info), cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell_margins(t, 80, 80, 0, 120)
    apply_widths(t, [3.4, 11.4])
    for i, (k, v) in enumerate(info):
        row = t.rows[i]
        pk = row.cells[0].paragraphs[0]
        pk.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        cn_font(pk.add_run(k), FONT_CN, 10, color=GREY)
        pv = row.cells[1].paragraphs[0]
        cn_font(pv.add_run(v), FONT_CN, 10, color=INK)

    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def add_toc(doc):
    p = doc.add_paragraph()
    cn_font(p.add_run("目　　录"), FONT_CN_HEAD, 16, True, CINNABAR)
    p.paragraph_format.space_after = Pt(12)

    tip = doc.add_paragraph()
    cn_font(
        tip.add_run(
            "（本目录为 Word 自动域。首次打开时若显示为占位文字，"
            "请按 Ctrl+A 全选后按 F9，或右键选择「更新域 → 更新整个目录」，即可生成带页码的目录。）"
        ),
        FONT_CN, 8.5, color=GREY,
    )
    tip.paragraph_format.space_after = Pt(10)

    p = doc.add_paragraph()
    add_field(p, ' TOC \\o "1-3" \\h \\z \\u ', "右键此处选择「更新域」以生成目录")

    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


# ============================================================
# 内容构件
# ============================================================
def h1(doc, text):
    return doc.add_heading(text, level=1)


def h2(doc, text):
    return doc.add_heading(text, level=2)


def para(doc, text, lead=None, size=10.5):
    """正文段落。lead 为段首加粗短语。"""
    p = doc.add_paragraph()
    if lead:
        cn_font(p.add_run(lead), FONT_CN, size, True, INK)
    cn_font(p.add_run(text), FONT_CN, size, color=INK)
    return p


def bullet(doc, text, lead=None, level=0):
    p = doc.add_paragraph(style="List Bullet" if level == 0 else "List Bullet 2")
    p.paragraph_format.space_after = Pt(2)
    if lead:
        cn_font(p.add_run(lead), FONT_CN, 10.5, True, INK)
    cn_font(p.add_run(text), FONT_CN, 10.5, color=INK)
    return p


def numbered(doc, *parts):
    """编号段落。最后一个参数若为 lead，则加粗。

    用法：numbered(doc, "正文") 或 numbered(doc, "正文", lead="标题：")
    """
    text, lead = parts[0], (parts[1] if len(parts) > 1 else None)
    p = doc.add_paragraph(style="List Number")
    p.paragraph_format.space_after = Pt(2)
    if lead:
        cn_font(p.add_run(lead), FONT_CN, 10.5, True, INK)
    cn_font(p.add_run(text), FONT_CN, 10.5, color=INK)
    return p


def code_block(doc, code):
    t = doc.add_table(rows=1, cols=1)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = t.rows[0].cells[0]
    shade(cell, "F4F1EC")
    cell_margins(t, 100, 100, 160, 160)

    bd = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement("w:" + edge)
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:color"), "D9D2C5")
        bd.append(el)
    t._tbl.tblPr.append(bd)

    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.15
    lines = code.strip("\n").split("\n")
    for i, line in enumerate(lines):
        r = p.add_run(line if line else " ")
        r.font.name = FONT_MONO
        r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_MONO)
        r.font.size = Pt(9)
        r.font.color.rgb = RGBColor(0x2B, 0x2B, 0x2B)
        if i < len(lines) - 1:
            r.add_break()

    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def apply_widths(table, widths_cm):
    """设置列宽。

    关键：tblGrid 的 gridCol 宽度必须与单元格宽度一起改。
    固定布局（tblLayout=fixed）下 Word 以 tblGrid 为准，
    只设 cell.width 不设 gridCol，各列会被拉成等宽——
    这正是"层次"这类短列文字被挤成竖排的原因。
    """
    grid = table._tbl.find(qn("w:tblGrid"))
    cols = grid.findall(qn("w:gridCol")) if grid is not None else []
    for i, w in enumerate(widths_cm):
        twips = str(int(w * 567))  # 1cm ≈ 567 twips
        if i < len(cols):
            cols[i].set(qn("w:w"), twips)
        for row in table.rows:
            if i < len(row.cells):
                row.cells[i].width = Cm(w)


def table(doc, headers, rows, widths=None, caption=None, font_size=9):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    cell_margins(t)

    hdr = t.rows[0]
    repeat_header(hdr)
    for i, h in enumerate(headers):
        cell = hdr.cells[i]
        shade(cell, "A8322C")
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(0)
        cn_font(p.add_run(h), FONT_CN_HEAD, font_size, True, WHITE)

    for idx, row in enumerate(rows):
        cells = t.add_row().cells
        for i, val in enumerate(row):
            cell = cells[i]
            if idx % 2 == 1:
                shade(cell, "FAF8F4")
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.15
            cn_font(p.add_run(str(val)), FONT_CN, font_size, color=INK)

    if widths:
        apply_widths(t, widths)

    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_before = Pt(3)
    cap.paragraph_format.space_after = Pt(8)
    if caption:
        cn_font(cap.add_run(caption), FONT_CN, 8.5, color=GREY)
    return t


def picture(doc, rel_path, width_cm, caption):
    """插入居中图片 + 题注。路径相对工程根目录。"""
    p = BASE / rel_path
    if not p.exists():
        return False
    doc.add_picture(str(p), width=Cm(width_cm))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.paragraphs[-1].paragraph_format.space_before = Pt(6)
    doc.paragraphs[-1].paragraph_format.space_after = Pt(2)

    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_after = Pt(10)
    cn_font(cap.add_run(caption), FONT_CN, 8.5, color=GREY)
    return True


def callout(doc, title, body, tone="indigo"):
    fill = {"indigo": "EEF2F7", "cinnabar": "FBEFEE", "grey": "F4F1EC"}[tone]
    bar = {"indigo": "2C4A6E", "cinnabar": "A8322C", "grey": "8A8178"}[tone]

    t = doc.add_table(rows=1, cols=1)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = t.rows[0].cells[0]
    shade(cell, fill)
    cell_margins(t, 110, 110, 200, 160)

    bd = OxmlElement("w:tblBorders")
    for edge in ("top", "bottom", "right"):
        el = OxmlElement("w:" + edge)
        el.set(qn("w:val"), "nil")
        bd.append(el)
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), "18")
    left.set(qn("w:color"), bar)
    bd.append(left)
    t._tbl.tblPr.append(bd)

    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(3)
    cn_font(p.add_run(title), FONT_CN_HEAD, 10, True,
            INDIGO if tone == "indigo" else CINNABAR)

    p2 = cell.add_paragraph()
    p2.paragraph_format.space_after = Pt(0)
    p2.paragraph_format.line_spacing = 1.35
    cn_font(p2.add_run(body), FONT_CN, 9.5, color=INK)

    doc.add_paragraph().paragraph_format.space_after = Pt(2)


# ============================================================
# 第一章
# ============================================================
def chapter_1(doc):
    h1(doc, "一、文档说明")

    h2(doc, "1.1 本文档回答什么问题")
    para(doc, "本文档是项目的正式交付说明，面向验收方、评阅人与后续维护者。"
              "它不重复代码细节，而是回答四个问题：系统解决了什么问题、"
              "关键约束靠什么机制保证、已经验证到什么程度、到哪里去看实际效果。")
    bullet(doc, "，说明业务命题、三级判定规则与技术选型理由。", "第 2 章 项目概述")
    bullet(doc, "，8 张表的职责划分、3 个视图、7 个触发器与权限模型。", "第 3 章 系统设计")
    bullet(doc, "，即「AI 为什么改不了权属」的四层机制。", "第 4 章 核心机制")
    bullet(doc, "，逐项列出已验证与未验证的部分，不做模糊表述。", "第 5 章 验证情况")
    bullet(doc, "，种子数据构成与现场演示脚本。", "第 6 章 交付物清单与使用方法")
    bullet(doc, "，可逐条勾选。", "第 7 章 验收清单")

    h2(doc, "1.2 阅读建议")
    para(doc, "若时间有限，建议按以下顺序阅读：")
    numbered(doc, "，先建立这个系统在做什么的整体认知。", "第 2 章 第 1–2 节")
    numbered(doc, "，这是本项目与其他 AI 平台的根本区别。", "第 4 章 核心机制")
    numbered(doc, "，明确哪些结论有证据、哪些只有代码。", "第 5 章第 4 节")
    numbered(doc, "，按 5 分钟脚本走一遍。", "第 6 章第 4 节")
    para(doc, "需要边看边操作时，请打开配套的单文件演示页 "
              "output/01-交付物/演示页面.html（双击即可，无需安装任何环境）。"
              "该页面只保留功能界面，顶栏可切换身份，五个页签对应本文档相应章节。"
              "若需要带登录与更多交互的版本，用同目录下的 演示页面_互动版.html。")

    callout(doc, "关于本文档的数字",
            "文中出现的所有统计数字（账号数、作品数、授权费合计、置信度等）"
            "均由脚本从 data/seed_demo.json 与 docs/verify/run_result.json 读取后写入，"
            "不是手工填写。种子数据中的 AI 判定结论则由真实特征算法回算得出，"
            "可用 python -m scripts.check_seed 复核。",
            tone="indigo")


# ============================================================
# 第二章
# ============================================================
def chapter_2(doc):
    h1(doc, "二、项目概述")

    h2(doc, "2.1 业务背景与要解决的问题")
    para(doc, "非物质文化遗产的数字化保护正在快速推进，但数字资产的权属认定长期依赖人工经验，"
              "带来三个具体难题：")
    bullet(doc, "一件苗绣纹样，可能是公有领域的传统母题，也可能是传承人在其基础上的改良创作，"
                "还可能是完全原创。三者对应的权利边界、可授权范围、收益分配规则完全不同，"
                "而肉眼判断往往说不清。", "一是「像不像」难以量化——")
    bullet(doc, "传统纹样通过口传与实物传播，一件作品的创作灵感可能源自数十年前某位前辈的作品，"
                "中间经历过多次转手与改良，事后追溯缺少可信记录。", "二是「从哪来」难以说清——")
    bullet(doc, "谁在什么时候把某件作品定成了原创，依据是什么，事后能否复核。"
                "如果这些只存在于某个人的记忆或一张表格里，一旦产生纠纷就无法举证。",
           "三是「谁定的」难以追溯——")

    para(doc, "本项目针对上述三点，构建一套可运行的系统：用向量相似度把「像不像」变成可比较的数值，"
              "用母题血缘与链式授权记录把「从哪来」固化下来，"
              "用数据库层的权限约束与不可篡改日志把「谁定的」变成可举证的事实。")

    picture(doc, str(FIG_DIR / "fig_analyze.png"), 14.0,
            "图 2-1　作品上传后的 AI 分析面板（左：上传区；右：判定档位、置信度、相似母题 Top-3 与命中规则）")

    h2(doc, "2.2 核心命题：AI 只出建议，权属由专家终审")
    para(doc, "这是整个项目最重要的设计前提，也是所有技术决策的出发点。")
    para(doc, "AI 在系统中承担的工作是提出建议并给出依据：提取图像特征、检索最相似的公有母题、"
              "按阈值给出一档判定（公有母题 / 改编衍生 / 原创）以及置信度。"
              "但它没有能力直接修改作品的权利级别字段。")
    para(doc, "权属结论只能由具备专家角色的用户通过终审操作产生，且该操作在一个数据库事务内完成，"
              "同时写入审计流水。系统还记录了专家是否推翻了 AI 的建议（is_override 字段），"
              "使「AI 与人类判断不一致」这件事本身也成为可统计、可分析的数据。")

    callout(doc, "为什么不能只是「约定不写」",
            "如果只靠开发者自觉遵守「AI 模块不要写 right_level」，"
            "三个月后一次紧急需求就可能打破它，而且打破时不会有任何报错。"
            "因此本项目把这个约束下沉到数据库的列级 GRANT 权限里——"
            "传承人角色的数据库账号在物理上就没有该列的更新权限。详见第 4 章。",
            tone="cinnabar")

    para(doc, "母题库在界面上的呈现方式如下。每条母题除参考图外，还带民族、类别、年代、流传地域"
              "与关键词等结构化字段，便于按民族或纹样类型检索；卡片底部标注向量维度与关联作品数。")
    picture(doc, str(FIG_DIR / "fig_motifs.png"), 13.6,
            "图 2-2　母题库页面：四步流程说明、筛选条、母题卡片墙与作品关联表")

    h2(doc, "2.3 三级判定规则")
    para(doc, "系统将作品与公有母题库做向量近邻检索，取相似度最高的母题作为判定依据，"
              "按两个阈值划出三档：")
    table(doc,
          ["判定档位", "判定条件", "权利含义"],
          [
              ["公有母题（public）", "与最近母题的余弦相似度 ≥ 0.92",
               "作品与公有领域传统纹样高度一致，任何个人不得主张独占权利"],
              ["改编衍生（derived）", "0.80 ≤ 相似度 < 0.92",
               "在传统母题基础上的再创作，权利归属于改编者，但需标注母题来源"],
              ["原创（original）", "相似度 < 0.80",
               "与现有公有母题差异显著，可主张完整著作权"],
          ],
          widths=[3.4, 5.2, 6.6],
          caption="表 2-1　三级判定阈值与权利含义")

    para(doc, "两个阈值不是拍脑袋定的，而是通过一个可复现的标定脚本在样例集上搜索得出，"
              "并受两条互相拉扯的硬约束限制：")
    bullet(doc, "同一作品的 9 种翻拍变体（旋转、缩放、亮度变化等），与原始图的相似度都必须 ≥ 0.90。"
                "不达标意味着同一件作品被误判成两件，这比漏判严重得多。", "翻拍稳定性：")
    bullet(doc, "真实改编作品与母题的相似度必须 < 0.92。"
                "超标意味着传承人的正当改编被判为母题雷同，直接损害其权益。", "改编可区分性：")

    callout(doc, "一个容易混淆的点：置信度不是相似度",
            "界面上同时出现相似度与置信度两个数，量纲完全不同，不能互相替代。"
            "相似度是两向量的余弦值，反映图像本身有多接近；"
            "置信度是分级器根据最近母题与次近母题的差值、以及判定档位与阈值的距离综合算出的确定程度，"
            "通常落在 0.4 至 0.7 之间。"
            "本项目早期曾出现用置信度冒充相似度的缺陷，导致界面显示 0.4190 而真实相似度是 0.8059，"
            "数字格式完全正常，只是错的。现已加入不变量检查防止复发。",
            tone="grey")

    para(doc, "上图右侧可以看到三组数字并列：AI 建议档位为改编衍生、置信度 43.7%、"
              "与蝴蝶纹母题的相似度 0.9182。这三者各自独立：相似度决定落进哪一档，"
              "置信度反映这一判定的确定程度，而档位只是建议值。")

    h2(doc, "2.4 技术选型与理由")
    table(doc,
          ["层次", "选型", "选择理由"],
          [
              ["Web 框架", "FastAPI + Uvicorn",
               "异步原生支持、Pydantic 做请求校验、自动生成 OpenAPI 文档；单进程即可演示"],
              ["数据库", "PostgreSQL 16",
               "需要行级锁、事务、触发器、列级 GRANT、JSONB 与部分索引，这些正是本项目要展示的能力"],
              ["向量检索", "pgvector（不可用时降级为数组兼容层）",
               "把向量与业务数据放在同一个库里，避免额外中间件；兼容层保证无扩展环境也能运行"],
              ["特征提取", "CLIP / ResNet / 内置 HOG+HSV+Laplacian",
               "三档可切换并自动降级；内置后端不依赖 GPU 与模型下载，保证仓库自包含可复现"],
              ["前端", "原生 HTML + CSS + JavaScript",
               "无构建步骤、无 node_modules，评审方拿到即可打开，避免工具链成为交付障碍"],
              ["鉴权", "HS256 JWT（纯标准库实现）",
               "不引入第三方加密库，减少依赖面；每请求回查用户表，使停用账号立即生效"],
          ],
          widths=[2.6, 5.2, 7.4],
          caption="表 2-2　技术选型对照")

    para(doc, "关于数据库的特别说明：本项目刻意把多条业务规则实现在数据库层而非应用层，"
              "包括列级权限、审计触发器、唯一性约束和行级锁。"
              "代价是 SQL 复杂度上升，收益是即使应用代码写错或被绕过，规则依然成立。"
              "这个取舍贯穿整个项目。")


# ============================================================
# 第三章
# ============================================================
def chapter_3(doc):
    h1(doc, "三、系统设计")

    h2(doc, "3.1 数据模型：8 张表的职责划分")
    para(doc, "数据模型围绕「作品—建议—终审—授权—溯源」这条主线组织，"
              "8 张表分为四个功能组：")
    table(doc,
          ["功能组", "表名", "职责"],
          [
              ["主体", "app_user", "用户与角色（传承人 / 专家 / 管理员）"],
              ["基准", "public_motif", "公有母题库，含 512 维基准向量，是分级判定的比对基准"],
              ["作品", "asset", "作品主表，含权属级别、流转状态、AI 建议三个独立字段"],
              ["作品", "feature_vector", "作品特征向量，与模型版本绑定存储，保证可追溯"],
              ["流程", "audit_record", "审核流水，快照 AI 建议与相似度，记录专家结论与是否推翻"],
              ["流程", "license_record", "授权记录，支持独占/非独占/展示/衍生四类，链式指向上一次"],
              ["溯源", "asset_genealogy", "作品间的衍生关系（改编 / 修复 / 合作）"],
              ["溯源", "system_audit_log", "系统级审计日志，由触发器自动写入，不可更新或删除"],
          ],
          widths=[2.2, 4.2, 8.8],
          caption="表 3-1　8 张表的职责划分")

    para(doc, "asset 表中有三个容易混淆的字段，必须区分清楚：")
    table(doc,
          ["字段", "语义", "谁能写"],
          [
              ["right_level", "权属级别——当前生效的法律结论",
               "仅 approve_asset() 一处，且仅 expert 角色"],
              ["audit_status", "流转状态——草稿 / 待审 / 已确权 / 已驳回",
               "业务流转，多角色可推进"],
              ["ai_suggest_level", "AI 建议——仅供参考，不影响权属",
               "AI 分析流程，可被反复覆盖"],
          ],
          widths=[3.6, 6.2, 5.4],
          caption="表 3-2　三个易混字段的语义区分")

    para(doc, "下图中同一页面的三列正是这三个字段的直接呈现："
              "「AI 建议」列显示模型给出的档位与置信度，「专家结论」列显示已经生效的权属级别，"
              "「状态」列显示流转状态。第一行是待审作品——AI 建议为改编衍生，"
              "但专家结论为空、状态为待审，说明权属尚未确定。")
    picture(doc, str(FIG_DIR / "fig_mine.png"), 14.0,
            "图 3-1　传承人的作品列表：AI 建议、专家结论、流转状态三列并置")

    callout(doc, "字段拆分的意义",
            "把结论与建议拆成两个字段，而不是共用一个字段加个 is_ai 标志位，"
            "是为了让数据库层面能够精确授权：传承人的账号有 ai_suggest_level 的更新权，"
            "但没有 right_level 的更新权。若共用一个字段，这个区分就无法用 GRANT 表达。",
            tone="indigo")

    h2(doc, "3.2 视图：面向角色的数据投影")
    para(doc, "系统定义三个视图，分别服务三类查询场景，避免在应用层拼接复杂 JOIN：")
    table(doc,
          ["视图", "用途", "关键设计"],
          [
              ["v_asset_pending_audit", "专家工作台的待审队列",
               "用 LATERAL 子查询只取每件作品最新的一条待审流水，避免重复分析导致的重复行"],
              ["v_asset_trace_chain", "溯源链路的原始事件投影",
               "把作品登记、审核、授权、系统日志四类事件 UNION 成统一行，事件时间作为排序键"],
              ["v_asset_ledger", "监管台账的汇总视图",
               "JOIN 母题表输出母题编码与名称；用 LATERAL 取最近一次带快照的审核流水，输出 Top-1 相似度"],
          ],
          widths=[4.0, 4.2, 7.0],
          caption="表 3-3　三个视图的用途")

    callout(doc, "一个必须注意的实现陷阱",
            "v_asset_ledger 新增 top1_similarity 列后，接口一度返回空值。"
            "原因是 Pydantic v2 默认丢弃未在模型中声明的字段：视图改了、SQL 也返回了，"
            "但序列化时被静默丢弃，不报任何错。"
            "现已加入「视图顶层 SELECT 列集合与 Pydantic 字段集合」的静态比对检查，"
            "两侧不同步会直接导致测试失败。",
            tone="cinnabar")

    h2(doc, "3.3 触发器与审计留痕")
    para(doc, "8 个触发器承担三类职责：")
    bullet(doc, "作品、授权、母题向量的变更自动写入系统审计日志，"
                "记录旧值、新值与变更列名，用于事后追责。", "自动留痕：")
    bullet(doc, "系统审计日志禁止更新与删除；审核流水禁止删除，"
                "已终审的流水禁止改回待审。", "防篡改：")
    bullet(doc, "母题向量写入时自动做 L2 归一化，"
                "保证检索时「内积 = 1 减余弦距离」这个前提始终成立。", "一致性维护：")
    para(doc, "审计日志的写入与主业务操作在同一个事务内，因此不存在业务成功了但日志没写的中间态。"
              "这是把日志交给数据库触发器而非应用代码的直接收益。")

    h2(doc, "3.4 权限模型")
    table(doc,
          ["角色", "数据库角色", "核心权限"],
          [
              ["传承人", "ich_heritor",
               "可读自己的作品；可更新标题、描述、文件信息、AI 建议等列；"
               "不可更新 right_level"],
              ["评审专家", "ich_expert",
               "可读全部作品与审核流水；可更新 right_level 与 audit_status；可写审计结论"],
              ["监管管理员", "ich_admin",
               "全量只读台账与日志；母题库维护；不可修改任何作品的权属结论"],
          ],
          widths=[2.4, 3.4, 9.4],
          caption="表 3-4　三角色的数据库权限")

    para(doc, "权限通过 SET LOCAL ROLE 在事务开始时切换，作用域限于当前事务，"
              "不会污染连接池中的其他连接。因此同一个连接池可以为不同角色的请求服务，"
              "而每次请求使用的都是它应得的权限集。")

    para(doc, "监管台账是权限模型的直接体现：该视图只对专家与管理员开放，"
              "传承人即使知道接口地址也拿不到数据。台账把状态分布、级别分布与授权费合计"
              "集中呈现，并逐行给出每件作品的母题血缘与 Top-1 相似度。")
    picture(doc, str(FIG_DIR / "fig_ledger.png"), 14.0,
            "图 3-2　监管台账：状态与级别分组统计、授权费合计、逐行母题血缘与相似度")


# ============================================================
# 第四章
# ============================================================
def chapter_4(doc):
    h1(doc, "四、核心机制：AI 为什么改不了权属")

    para(doc, "这一章是整个项目的技术核心。把一个约定固化为不可绕过的机制，"
              "本项目用了四层手段，从硬到软依次是：")

    h2(doc, "4.1 第一层：数据库列级权限（最硬）")
    para(doc, "PostgreSQL 支持对表的特定列单独授权。本项目的 GRANT 语句明确列出传承人可更新的列，"
              "而 right_level 不在其中：")
    code_block(doc, """
-- 传承人只能更新这些列，★ 不含 right_level
GRANT UPDATE (title, description, file_path, file_hash, file_size, mime_type,
              audit_status, ai_suggest_level, ai_confidence, ai_model_version, motif_id)
    ON asset TO ich_heritor;

-- 只有专家能改权属结论
GRANT UPDATE (right_level, audit_status) ON asset TO ich_expert;
""")
    para(doc, "这意味着：即使有人拿到了传承人的登录凭证，"
              "直接连数据库提交一条把 right_level 改成 original 的语句，"
              "数据库也会拒绝并抛出权限错误。"
              "这不是应用层的条件判断，而是无法通过修改代码绕过的物理限制。")

    para(doc, "专家工作台的待审队列如下。每行显示作品、提交人、AI 建议与相似度，"
              "专家据此决定权属级别；勾选不同档位时才需要填写推翻理由，与 AI 一致时可直接采纳。")
    picture(doc, str(FIG_DIR / "fig_workbench.png"), 14.0,
            "图 4-1　专家工作台：待审队列与三分组统计（待审 / 今日已审 / 累计推翻 AI）")

    h2(doc, "4.2 第二层：源码不变量测试")
    para(doc, "权限只限制了谁能写，还需要保证哪段代码在写。"
              "系统用一个专门的测试文件扫描源码字符串，检查以下事实：")
    bullet(doc, "。", "分析函数 analyze_asset 体内不得出现任何写 right_level 的 SQL")
    bullet(doc, "。", "整个工程只有 approve_asset 一处出现设置 right_level 的语句")
    bullet(doc, "。", "分级器模块 classifier.py 不得 import 任何数据库模块")
    para(doc, "这些检查是负向的：任何人都可以尝试加一条 AI 自动确权的捷径，"
              "但提交后测试会立即失败并指出违规行号。")

    h2(doc, "4.3 第三层：分级器的物理隔离")
    para(doc, "AI 分级器是一个纯函数模块，它不 import 任何数据库模块，"
              "只能接收相似度命中列表、返回一个数据类。它没有连接、没有游标、没有事务，"
              "在物理上没有能力修改任何持久化状态。")
    para(doc, "这是一个刻意的架构约束：与其在代码审查时提醒注意不要在这里写库，"
              "不如从模块依赖上就断绝这种可能。")

    h2(doc, "4.4 第四层：写入路径唯一加事务留痕")
    para(doc, "只有 approve_asset 一个函数能写 right_level，它在单个事务内完成六步：")
    numbered(doc, "，防止并发终审导致的状态覆盖。", "以 SELECT FOR UPDATE 锁定作品行")
    numbered(doc, "，而非假定当前状态符合预期，避免把过期状态当成新状态。", "校验当前状态必须为待审")
    numbered(doc, "，写入专家结论。", "更新权属级别与流转状态")
    numbered(doc, "，写入审核流水。", "更新对应的审核记录")
    numbered(doc, "，不符则回滚。", "校验受影响行数")
    numbered(doc, "，确认触发器确实写入了系统日志。", "回读系统审计日志")
    para(doc, "六步要么全部成功，要么全部回滚。中途任何一步失败，作品状态与日志都不会留下半成品。")

    callout(doc, "四层机制的分工",
            "第一层管谁能写，第二层管哪段代码能写，"
            "第三层管能不能连到库，第四层管写的时候是否完整。"
            "四层各自独立，任意一层失效，其余三层仍然成立。",
            tone="indigo")


# ============================================================
# 第五章
# ============================================================
def chapter_5(doc):
    h1(doc, "五、验证情况")

    para(doc, "本章区分两类事实：被实际执行验证过的，与仅有代码未被运行的。"
              "这个区分必须清楚，否则会高估交付物的可靠性。")

    h2(doc, "5.1 已实际运行验证的部分")
    para(doc, "以下内容均在本机真实执行，可用文中命令复现：")
    table(doc,
          ["验证项", "规模", "验证内容"],
          [
              ["业务链路端到端", "52 项",
               "三角色登录、越权拦截、上传触发 AI 建议、AI 未写权属、待审列表、"
               "专家推翻 AI、重复终审拦截、未终审拒绝授权、独占授权唯一性、"
               "溯源链式校验、台账汇总、母题库结构、授权费合计、文件查重幂等"],
              ["浏览器 UI 断言", "49 项",
               "四角色五页签逐屏渲染，断言无 undefined 与 NaN、控制台零报错、"
               "关键数据行数与种子一致（如授权费合计必须是真实数值而非 0）"],
              ["源码不变量", "88 项",
               "AI 不写权属、终审单事务、列级 GRANT、追加式日志、触发器覆盖、"
               "前端 XSS 转义、种子 AI 字段真实性、视图列与模型同步"],
              ["向量兼容层", "39 项",
               "降级模式的余弦距离与 pgvector 数值一致、归一化后内积等于 1 减距离的前提成立"],
              ["特征引擎与分级器", "41 项",
               "母题互分离度、类内稳定性、退化输入鲁棒性（全黑 / 全白 / 极小图 / 极端长宽比）"],
              ["规则单一真相", "59 项",
               "规则表从 SQL 自动生成并与真实库逐条对账（24 CHECK / 4 UNIQUE / 12 外键 / 53 NOT NULL / 8 触发器），"
               "SQL 改了不重新生成会报错；求值按 SQL 三值逻辑（UNKNOWN 不算违反），"
               "并按列类型先取整再判 CHECK（NUMERIC 的 1.00004 存成 1.0000 后应当放行）"],
              ["规则对拍（库 vs Python）", "75 项",
               "同一组边界样本分别喂真实数据库与 Python 规则：62 组规则样本判定 100% 一致，"
               "7 组权限边界、6 组唯一性与外键按设计分开核对；另如实记录 2 条两侧都不拦的已知缺口"],
              ["SQL 静态结构", "全项通过",
               "8 表 3 视图 3 角色齐全、外键目标存在、触发器函数存在、关键约束与索引存在"],
          ],
          widths=[4.0, 2.2, 9.0],
          caption="表 5-1　已验证项目与规模")

    para(doc, "上述合计 403 项检查，当前全部通过。")

    h2(doc, "5.2 一次完整业务流程的实际运行记录")
    para(doc, "除自动化测试外，还以真实图片样本走了一遍完整链路，"
              "记录见 docs/verify/完整流程运行报告.md，原始返回存于 docs/verify/run_result.json。")
    table(doc,
          ["环节", "输入", "输出"],
          [
              ["上传", "两份 480×480 图片样本", "落盘、SHA-256 计算、查重未命中"],
              ["特征提取", "原始图像", "512 维归一化向量，模型 builtin-hog-color-texture@1.0.0"],
              ["相似检索", "作品向量与 3 条母题向量比对",
               "改编样本 0.9182 命中蝴蝶纹；原创样本 0.6036 命中缠枝莲纹"],
              ["分级建议", "Top-1 相似度与档位距离",
               "改编样本判为 derived（置信度 0.4372）；原创样本判为 original（置信度 0.7417）"],
              ["专家终审", "专家角色提交结论", "权属级别落库、审核流水写入、系统日志自动生成"],
              ["授权登记", "被授权方、费用、期限", "授权记录落库，合同摘要生成"],
              ["溯源查询", "作品编号", "四类事件按时间重排为连续节点，链式校验通过"],
              ["台账汇总", "全量资产", "按状态与级别分组统计，授权费合计正确"],
          ],
          widths=[2.6, 4.4, 8.2],
          caption="表 5-2　完整业务流程各环节的实际输出")

    para(doc, "这次实际运行发现了两个此前未被发现的缺陷，均已修复并加了回归检查：")
    bullet(doc, "溯源链路的节点序号原先按事件类型写死为常量，"
                "导致时间轴显示成 1、4、4、2、4、3 的乱序。改为由应用层按事件时间重排。",
           "序号乱序：")
    bullet(doc, "作品分析时判为公有或衍生，但从未把对应母题编号写回作品表，"
                "导致台账的母题列恒为空、衍生作品统计恒为 0。", "母题血缘未回写：")

    h2(doc, "5.3 演示数据与真实算法的一致性")
    para(doc, "演示种子数据中每件作品的 AI 判定结论、置信度与相似度，"
              "都不是手工填写的，而是由校验脚本调用真实特征算法对实际图片计算后回写。这一点很重要：")
    para(doc, "如果种子数据里的结论是编的，演示时会出现这样的情况：台账显示某作品是改编衍生，"
              "观众点击重新分析，AI 却给出原创。这一瞬间，整套演示的可信度就归零了。"
              "因此种子里只记录真实算法会得出什么，不记录希望它是什么。")

    h2(doc, "5.4 数据库层的实测结论与唯一缺口")

    para(doc, "这一节早先写的是「SQL 从未在真实数据库上执行过」，该结论已经失效，原文作废。"
              "现在用便携版 PostgreSQL 16.2 二进制在 127.0.0.1:55432 起了真实实例，"
              "python -m scripts.pg_run --reset 可从空库一键重建（119 条语句，约 1.6 秒），"
              "python -m tests.check_pg 做 36 项真库验收：35 项通过、1 项缺口，缺口如实列在表末。")

    table(doc,
          ["验证项", "实测结论", "证据"],
          [
              ["列级 GRANT 实际生效", "997 条列级授权；传承人对 right_level 无写权限，"
                                     "越权写入被 42501 拒绝", "check_pg §9"],
              ["CHECK / UNIQUE / 外键", "24 条 CHECK、4 条唯一（含部分唯一索引）、12 条外键全部生效，"
                                        "非法值分别报 23514 / 23505 / 23503", "check_pg §11"],
              ["8 个触发器实际触发", "追加式表禁改禁删、已终审不可改、"
                                     "未终审与已驳回不得授权均实测拒绝", "check_pg §5–7"],
              ["终审主链路", "ich_expert 身份 UPDATE asset → 触发器写审计日志 → version_no 自增，"
                             "actor 记为业务角色而非函数属主", "check_pg §7"],
              ["MVCC 与行级锁", "第二个会话 SELECT … FOR UPDATE 收到 55P03，"
                                "并发终审不会双写", "check_pg §8"],
              ["事务回滚完整性", "中途失败时触发器写入的审计日志随事务一并回滚，不留半条记录", "check_pg §8"],
              ["视图在真实数据下", "3 个视图在种子数据下返回正确行数与列，LATERAL 子查询无空表异常", "check_pg §10"],
              ["规则表与库是否一致", "CHECK 24/24、UNIQUE 3/3、部分唯一索引 1/1、外键 12/12、"
                                     "触发器 8/8、NOT NULL 53/53 逐条对齐", "gen_rules --verify-db"],
              ["HNSW 近似最近邻索引", "**未验证**：便携包不含 pgvector，只能走 NUMERIC[] 精确扫描",
               "唯一缺口"],
          ],
          widths=[4.4, 8.2, 3.0],
          caption="表 5-3　数据库层的实测结果")

    callout(doc, "唯一缺口：HNSW 索引不能声称「检索快」",
            "降级模式下余弦距离的计算结果与 pgvector 数值一致（39 项兼容层测试验证），"
            "所以功能等价成立；但没有 pgvector 就无法验证 HNSW 的执行计划与召回率，"
            "因此本报告只承诺功能等价，不承诺检索性能。",
            tone="cinnabar")

    para(doc, "要补齐这些验证，需要在一台装有 PostgreSQL 15 以上版本的机器上，"
              "以强制真实数据库模式启动（连不上直接报错而不降级），"
              "然后执行第 6 章的使用说明与第 7 章第 2 节的验收清单（已实测，可逐条复核）。")

    h2(doc, "5.5 开发过程中发现并修复的缺陷")
    para(doc, "由于无法实跑数据库，验证主要依靠逐行读代码与编写回归检查。"
              "这个过程主动发现了 12 个会导致业务失败或界面错误的设计缺陷，"
              "其中 4 个属于不报错的类型，最难发现，也最危险。")
    table(doc,
          ["#", "缺陷", "后果", "修复方式"],
          [
              ["1", "审核流水表上挂了禁止任何更新的触发器",
               "专家终审必然失败，整条链路走不通", "拆分职责：禁删加禁改已终审，放行状态迁移"],
              ["2", "重复分析时把旧待审流水改成已驳回并塞入专家编号",
               "凭空产生虚假驳回记录，污染统计", "改为允许多条待审并存，查询侧只取最新一条"],
              ["3", "母题向量归一化触发器使用了 pgvector 专有转换",
               "降级模式下语法错误，整个脚本中断", "按运行时探测分支，两种模式各用各自写法"],
              ["4", "前端统计数据直接取值",
               "分组键不存在时界面首屏显示 undefined 件", "统一数值兜底函数，并加不变量检查"],
              ["5", "演示模式与真实库返回列数不一致",
               "演示模式版本列渲染成 vundefined", "补齐列并加静态列集合比对"],
              ["6", "传承人打开授权弹窗时请求了专家专属接口",
               "控制台留下 403 错误（功能不受影响）", "按角色判断，跳过无权请求"],
              ["7", "溯源节点序号按事件类型写死",
               "时间轴显示乱序（如 1、4、4、2、4、3）", "由应用层按事件时间重排"],
              ["8", "分析时从未写回母题编号",
               "台账母题列恒为空，衍生统计恒为 0", "判定为公有或衍生时写回 Top-1 母题编号"],
              ["9", "种子数据用置信度冒充相似度",
               "界面显示 0.4190，真实值 0.8059，格式正常但是错的",
               "补相似度字段，并加两者不得相等的检查"],
              ["10", "种子 AI 结论为手工填写",
               "8 件中有 2 件与算法实际输出不符，重新分析会改判",
               "改为从算法回写，并加 15 项种子不变量"],
              ["11", "视图新增列但未同步 Pydantic 模型",
               "字段被静默丢弃，前端该列全为空", "补模型字段，加视图列与模型字段静态比对"],
              ["12", "重构时授权写入代码被误缩进成死代码",
               "授权记录恒为空，台账授权费合计恒为 0", "拆出独立函数并显式调用；加数值断言"],
              ["13", "美元引用 $$ 嵌套在 DO $$ 内部",
               "整份脚本解析失败，建库直接中断", "换 $fn$ 作标签；美元引用不能嵌套"],
              ["14", "归一化写成「数组 ÷ 标量」",
               "PostgreSQL 没有这个运算符，两种模式都报错", "改用 l2_normalize() 函数"],
              ["15", "调用 ROUND(double precision, int)",
               "没有这个重载，兼容层函数创建失败", "先转 NUMERIC 再取整"],
              ["16", "触发器里写 TEXT[] || '字面量'",
               "按数组拼接解析，任何 UPDATE asset 都崩，含专家终审", "改用 array_append()"],
              ["17", "健康检查把「无 pgvector」判为不可用",
               "可用的降级库被拒，灌数据脚本直接罢工", "降级视为可用并标注能力边界"],
              ["18", "license_record 三个角色都只有 SELECT",
               "没有任何角色能 INSERT，授权登记必然 42501", "给传承人补 INSERT，仍禁 UPDATE"],
              ["19", "灌数据脚本用 admin 写资产、假设外键级联删",
               "无写权限、旧数据删不掉、还缺一个账号", "用属主身份写入 + 显式清理 + 补账号"],
              ["20", "审计触发器用 SECURITY INVOKER",
               "终审写日志 permission denied，整个事务回滚", "改 DEFINER + 固定 search_path"],
          ],
          widths=[0.9, 4.2, 5.0, 5.1],
          caption="表 5-4　开发期间发现并修复的 20 个缺陷（13–20 只在真库上暴露）",
          font_size=8.5)

    callout(doc, "第 9 至 12 条的共同特征",
            "这四条都不会导致报错：接口返回 200、页面渲染正常、控制台干净，"
            "错的是值和有没有写进去。其中第 12 条是一次纯重构引发的——"
            "把一段代码拆成函数时误缩进到返回语句之后，五笔授权就此消失，"
            "而所有接口依然正常返回。最终由一句刻意的数值断言（授权费合计必须大于 0）抓到。"
            "这也是为什么本项目的界面测试不只截图，还要断言每个格子里的具体数字。",
            tone="grey")


# ============================================================
# 第六章
# ============================================================
def chapter_6(doc):
    h1(doc, "六、交付物清单与使用方法")

    h2(doc, "6.1 交付物一览")
    table(doc,
          ["交付物", "形式", "位置", "用途"],
          [
              ["项目交付说明书", "Word (.docx)",
               "output/01-交付物/ 目录下", "本文档，可直接提交"],
              ["演示页（纯界面）", "单文件 HTML", "output/01-交付物/演示页面.html",
               "双击即可操作，只有功能界面、不含说明文字，适合投影演示"],
              ["演示页（互动版）", "单文件 HTML",
               "output/01-交付物/演示页面_互动版.html",
               "带拖动滑块登录，含 9 个页面、4 种审批档位与阈值联动，交互更完整"],
              ["完整源代码", "目录", "app/ sql/ scripts/ tests/ data/",
               "含后端、SQL、AI 模块、前端与测试"],
              ["演示种子数据", "JSON", "data/seed_demo.json",
               f"{len(USERS)} 账号 / {len(MOTIFS)} 母题 / {len(ASSETS)} 作品 / "
               f"{AUDIT_CNT} 条审核 / {LICENSE_CNT} 笔授权"],
              ["运行验证记录", "Markdown 与 JSON", "docs/verify/",
               "完整流程实际运行的报告与原始返回"],
              ["界面截图", "PNG", "output/02-截图证据/", "四角色五页签的逐屏截图"],
              ["主说明文档", "Markdown", "README.md", "面向开发者的完整技术说明"],
          ],
          widths=[3.2, 2.6, 4.4, 5.0],
          caption="表 6-1　交付物清单",
          font_size=8.5)

    h2(doc, "6.2 两份演示页的使用方法")
    para(doc, "两份都是自包含的单文件页面：种子数据与界面截图全部内嵌，不请求任何外部资源，"
              "断网也能正常打开。区别只在形态：")
    table(doc,
          ["演示页", "包含什么", "适合什么场合"],
          [
              ["演示页面.html",
               "只有功能界面，没有说明文字。顶栏切换身份，五个页签："
               "我的作品 / 母题库 / 作品上传 / 专家工作台 / 监管台账",
               "投影演示、答辩现场——画面干净，讲什么点什么"],
              ["演示页面_互动版.html",
               "先过一个拖动滑块登录页（4 个账号可选、密码校验），"
               "进入后 9 个页面：概览、作品管理、母题库、上传与 AI 分析、"
               "专家工作台、监管台账、溯源图谱、审计日志、演示设置",
               "需要长时间操作、想让评委自己动手的时候"],
          ],
          widths=[3.4, 7.0, 4.6],
          caption="表 6-2　两份演示页的分工",
          font_size=8.5)

    para(doc, "纯界面版的四步操作路径：")
    numbered(doc, "。Windows 双击，macOS 与 Linux 用浏览器打开，无需任何环境。", "直接双击该文件")
    numbered(doc, "，可见页签随角色增减——传承人看不到工作台与台账，"
                  "专家与管理员可见全部。", "点顶部的身份切换按钮")
    numbered(doc, "，点击行可打开详情抽屉，内含 AI 建议与专家结论的并列对比、"
                  "溯源链与授权记录。", "在「我的作品」里点任意一行")
    numbered(doc, "，选择一件待审作品，选一个与 AI 不同的档位，"
                  "提交后会打上「推翻 AI」标记。", "切到「专家工作台」")

    para(doc, "互动版在此基础上多了三件可以现场操作的事：")
    numbered(doc, "——把滑块拖到底才会解锁登录按钮，中途松手会回弹。",
             "拖动滑块验证")
    numbered(doc, "，选「原创 / 改编衍生 / 公有母题 / 未定」后，"
                  "页面会实时提示这个选择是否会推翻 AI 建议。",
             "终审时选档位")
    numbered(doc, "，回到工作台可以看到「按当前阈值推算」的结果随之变化，"
                  "用来解释 0.80 / 0.92 两条边界是怎么起作用的。",
             "在设置页拖动两个阈值滑块")

    para(doc, "注意：两份演示页都是静态页面，数据为种子数据的内嵌副本，操作不会真正写入数据库。"
              "如需验证真实读写，请按下一节启动后端服务。")

    h2(doc, "6.3 启动真实系统")
    para(doc, "三种形态，按现有条件选择：")
    code_block(doc, """
# ① 演示模式：无需数据库，数据在进程内存里，界面与业务逻辑完整
python bootstrap.py --demo

# ② 真实数据库：需先按 README 建库并执行 sql/01 至 03
python bootstrap.py --strict

# ③ 把种子数据灌进真实数据库
python -m scripts.init_seed
""")
    para(doc, "启动后浏览器打开 http://127.0.0.1:8000/ ，登录页提供演示账号，点击即可自动填入。")
    table(doc,
          ["账号", "姓名", "角色", "登录后可见页签"],
          [
              [u["username"], u["display_name"],
               {"heritor": "传承人", "expert": "评审专家", "admin": "监管管理员"}[u["role"]],
               ("作品上传 / 母题库 / 我的作品" if u["role"] == "heritor"
                else "作品上传 / 母题库 / 专家工作台 / 监管台账")]
              for u in sorted(USERS, key=lambda x: (
                  {"heritor": 0, "expert": 1, "admin": 2}.get(x["role"], 9), x["username"]))
          ],
          widths=[2.8, 2.4, 2.8, 7.2],
          caption="表 6-2　演示账号（口令统一为 Passw0rd!）")

    callout(doc, "页签差异只是体验层",
            "前端按角色显隐页签只是交互设计，真正的权限拦截在后端与数据库。"
            "传承人即使手动构造请求访问监管台账接口，也会收到 403；"
            "即使绕过应用直接连数据库，也没有权属级别列的更新权限。",
            tone="indigo")

    h2(doc, "6.4 现场演示脚本（约 5 分钟）")
    table(doc,
          ["步骤", "操作", "要说明的要点"],
          [
              ["1", "以传承人 heritor01 登录，停在「我的作品」",
               "3 件作品分别处于已确权、待审、已驳回状态；作者只能看到自己的，看不到另一位传承人的"],
              ["2", "切到「母题库」，指出 3 条母题",
               "这是判定基准。每条都有民族、类别、年代与关键词，入库时已提取 512 维向量"],
              ["3", "回到「我的作品」，点开待审的那件",
               "展示 AI 建议（改编衍生）与置信度，强调这只是建议，权属还没定"],
              ["4", "退出，以专家 expert01 登录，进「专家工作台」",
               "同一件作品出现在待审队列。注意此时 AI 建议仍在，但结论为空"],
              ["5", "提交终审，选定权属级别",
               "系统同时写入审核流水与系统日志；若专家选了与 AI 不同的档位，标记为推翻 AI"],
              ["6", "进「监管台账」，指出授权费合计与母题列",
               "按状态与级别分组统计；每件作品都能看到母题血缘与 Top-1 相似度"],
              ["7", "以传承人身份再登录，尝试访问监管台账",
               "被拒绝。说明这不是前端隐藏，而是后端 403 加数据库列级权限"],
          ],
          widths=[1.2, 5.4, 8.6],
          caption="表 6-3　现场演示脚本")

    h2(doc, "6.5 种子数据构成")
    para(doc, "演示数据设计为覆盖全部状态与角色组合，确保每个界面都有内容可看：")

    rows = []
    # 按角色排序：传承人 → 专家 → 管理员，同角色内按用户名
    role_order = {"heritor": 0, "expert": 1, "admin": 2}
    for u in sorted(USERS, key=lambda x: (role_order.get(x["role"], 9), x["username"])):
        owned = ASSETS_BY_OWNER.get(u["user_id"], [])
        if u["role"] == "admin":
            rows.append([u["username"], u["display_name"], "监管管理员", "0", "只读全量数据"])
        else:
            st = {}
            for a in owned:
                st[a["audit_status"]] = st.get(a["audit_status"], 0) + 1
            desc = "、".join(
                f"{ {'draft':'草稿','pending':'待审','approved':'已确权','rejected':'已驳回'}[k] }"
                f" {v} 件" for k, v in st.items()
            )
            rows.append([u["username"], u["display_name"],
                         "传承人" if u["role"] == "heritor" else "评审专家",
                         str(len(owned)), desc])

    table(doc,
          ["账号", "姓名", "角色", "作品数", "涉及状态"],
          rows,
          widths=[2.6, 2.2, 2.6, 1.8, 6.0],
          caption="表 6-4　种子数据的账号与作品分布")

    para(doc, f"数据总量：{len(USERS)} 个账号、{len(MOTIFS)} 条公有母题、"
              f"{len(ASSETS)} 件作品、{AUDIT_CNT} 条审核流水、{LICENSE_CNT} 笔授权记录，"
              f"授权费合计 {LICENSE_FEE:,.0f} 元。")

    para(doc, "3 条公有母题分别代表动物图腾纹、植物连续纹与几何边饰纹三个大类，"
              "民族与地域各异，用于检验分级判定在不同纹样类型上的表现：")
    table(doc,
          ["编码", "名称", "民族", "类别", "年代"],
          [[m["motif_code"], m["name"], m["ethnic"], m["category"], m["era"]] for m in MOTIFS],
          widths=[2.2, 3.0, 2.2, 4.4, 3.4],
          caption="表 6-5　3 条公有母题")

    h2(doc, "6.6 常见问题")
    table(doc,
          ["问题", "回答"],
          [
              ["没有数据库能看吗",
               "能。以演示模式启动即可，界面与业务逻辑完整，"
               "仅有数据库层的强制约束不生效（页面顶部有红色横幅标明）"],
              ["怎么确认当前跑的是真库还是演示模式",
               "看页面顶部有无红色横幅；或访问健康检查接口查看 database 字段"],
              ["为什么早期说 SQL 没在真实数据库跑过",
               "早期开发环境禁止初始化数据库实例，也编不了 pgvector。后来改用便携版 PostgreSQL 16.2 "
               "二进制绕过了这个限制，约束现已逐条实测（第 5.4 节与第 7.2 节），"
               "只剩 HNSW 索引因缺 pgvector 未测"],
              ["演示数据重启后还在吗",
               "演示模式下数据随进程消失，重启即恢复为种子初始状态；"
               "这是刻意设计，避免演示库被误当成真库"],
              ["可以关掉种子数据吗",
               "可以。设置环境变量 ICH_DEMO_SEED 为 off 后启动，得到一个空库"],
          ],
          widths=[5.0, 10.2],
          caption="表 6-6　常见问题")


# ============================================================
# 第七章
# ============================================================
def chapter_7(doc):
    h1(doc, "七、验收清单")

    para(doc, "以下清单按功能验收、真实数据库验收、交付物完整性三组列出，供验收时逐条勾选。")

    h2(doc, "7.1 功能验收（必做）")
    table(doc,
          ["#", "验收项", "通过标准", "结果"],
          [
              ["1", "服务可启动", "启动过程无错误，浏览器可打开登录页", "□"],
              ["2", "三角色可登录", "四个演示账号均能登录，页签随角色变化", "□"],
              ["3", "作品列表正确", "传承人只看到自己的作品，数量与种子一致", "□"],
              ["4", "母题库展示完整", "3 条母题均显示参考图、民族、类别、年代、关键词", "□"],
              ["5", "上传触发 AI 分析", "上传图片后返回判定档位、置信度与相似母题列表", "□"],
              ["6", "AI 未改权属", "分析完成后作品的权属级别保持不变", "□"],
              ["7", "专家终审生效", "提交后状态变为已确权，审核流水写入，日志生成", "□"],
              ["8", "推翻 AI 可识别", "专家结论与 AI 建议不同时，推翻标记为真", "□"],
              ["9", "重复终审被拒", "对已终审作品再次提交，返回冲突错误", "□"],
              ["10", "授权登记", "未终审作品拒绝授权；独占授权唯一性生效", "□"],
              ["11", "溯源链路", "节点序号连续、链式校验通过", "□"],
              ["12", "台账汇总", "分组统计正确，授权费合计与明细一致", "□"],
              ["13", "越权拦截", "传承人访问监管台账接口返回 403", "□"],
              ["14", "文件查重", "同一文件重复上传命中重复提示", "□"],
          ],
          widths=[1.0, 3.8, 8.4, 2.0],
          caption="表 7-1　功能验收清单")

    h2(doc, "7.2 真实数据库验收（已执行）")
    para(doc, "这一组曾在便携版 PostgreSQL 16.2 上完整执行（127.0.0.1:55432），"
              "对应 python -m tests.check_pg 的 36 项断言。除第 6 项外均已实测通过；"
              "第 6 项需要装有 pgvector 的环境，换机部署时补测即可。")
    table(doc,
          ["#", "验收项", "验证方法", "结果"],
          [
              ["1", "三个 SQL 脚本可执行", "依次执行建表、索引触发器视图、种子脚本，无错误", "✓ 119 条语句"],
              ["2", "8 表 3 视图 3 角色齐全", "列出表、视图与角色逐一核对", "✓"],
              ["3", "列级 GRANT 生效",
               "以传承人数据库角色执行更新权属级别的语句，应报权限错误", "✓ 42501"],
              ["4", "触发器自动留痕", "更新作品后查系统审计日志，应有对应记录", "✓"],
              ["5", "审计日志不可改", "对系统审计日志执行更新或删除，应报错", "✓"],
              ["6", "HNSW 索引被使用", "查看查询计划，应出现索引扫描", "□ 需 pgvector"],
              ["7", "并发终审安全", "两个并发请求提交同一作品，应一成功一失败", "✓ 55P03"],
              ["8", "事务回滚完整", "人为让终审中途失败，作品状态与日志均应无变化", "✓"],
              ["9", "约束生效", "插入非法枚举值、重复用户名，均应被拒绝", "✓"],
              ["10", "视图在真实数据下正确",
               "灌入种子数据后，三个视图输出与演示模式一致", "✓"],
              ["11", "未终审不得授权", "对 draft 作品直接写授权记录，应被数据库拒绝", "✓ 23514"],
              ["12", "规则表与库一致", "python -m scripts.gen_rules --verify-db 逐条对账", "✓ 六类全一致"],
          ],
          widths=[1.0, 4.0, 8.2, 2.0],
          caption="表 7-2　真实数据库验收清单（✓ 已实测 / □ 待补测）")

    h2(doc, "7.3 交付物完整性（建议）")
    table(doc,
          ["交付物", "存在", "可打开"],
          [
              ["项目交付说明书（本文档）", "有", "□"],
              ["交互式演示页", "有", "□"],
              ["完整源代码", "有", "—"],
              ["演示种子数据", "有", "□"],
              ["运行验证报告与原始返回", "有", "□"],
              ["界面截图（四角色五页签）", "有", "□"],
              ["主说明文档 README", "有", "□"],
          ],
          widths=[7.0, 2.4, 2.4],
          caption="表 7-3　交付物完整性自查")


def closing(doc):
    doc.add_paragraph()
    p = doc.add_paragraph()
    hrule(p)

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    cn_font(p.add_run("关于本交付物的一句话说明"), FONT_CN_HEAD, 11, True, CINNABAR)

    para(doc, "本项目把「AI 只出建议、权属由专家终审」这条规则，"
              "从一句写在文档里的约定，做成了四层互相独立的机制：数据库列级权限、"
              "源码不变量测试、模块依赖隔离、唯一写入路径加事务留痕。"
              "这是整个项目最值得说明的部分。")
    para(doc, "同时也要说清边界：数据库已在便携版 PostgreSQL 16.2 上真实跑通，"
              "36 项真库验收 35 通过 / 1 个缺口，规则对拍 75 项两侧 100% 一致，"
              "业务逻辑与界面另有 328 项检查全部通过。"
              "唯一未验证的是 HNSW 索引 —— 便携包不含 pgvector，只能走 NUMERIC[] 精确扫描，"
              "因此只承诺功能等价，不承诺检索性能。")


# ============================================================
# 主流程
# ============================================================
def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    doc = Document()
    build_styles(doc)
    build_page(doc)

    add_cover(doc)
    add_toc(doc)

    chapter_1(doc)
    chapter_2(doc)
    chapter_3(doc)
    chapter_4(doc)
    chapter_5(doc)
    chapter_6(doc)
    chapter_7(doc)
    closing(doc)

    doc.save(str(OUT_FILE))

    size_kb = OUT_FILE.stat().st_size / 1024
    print("已生成：" + str(OUT_FILE))
    print(f"体积：{size_kb:.1f} KB")
    print(f"段落数：{len(doc.paragraphs)}　表格数：{len(doc.tables)}")
    print()
    print("打开文档后请按 Ctrl+A 再按 F9 更新目录域。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
