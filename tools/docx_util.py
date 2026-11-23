#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Word 排版工具模块
--------------------------------------------------------------------------
按国内高校课程设计/毕业论文排版惯例封装 python-docx：
    正文：宋体 小四(12pt)，行距 1.5 倍，首行缩进 2 字符
    一级标题：黑体 三号(16pt) 居中
    二级标题：黑体 四号(14pt) 左对齐
    三级标题：黑体 小四(12pt) 左对齐
    图题/表题：宋体 五号(10.5pt) 居中
    代码：Consolas + 等线 五号，浅灰底纹
"""

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

SONG = '宋体'
HEI = '黑体'
KAI = '楷体'
MONO_CN = '等线'
MONO_EN = 'Consolas'


# ----------------------------------------------------------------------
# 基础设置
# ----------------------------------------------------------------------
def new_document(margin_cm=(2.6, 2.6, 3.0, 2.5)):
    """创建文档并设定 A4 页面与页边距（上、下、左、右，单位 cm）"""
    doc = Document()
    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.top_margin = Cm(margin_cm[0])
    sec.bottom_margin = Cm(margin_cm[1])
    sec.left_margin = Cm(margin_cm[2])
    sec.right_margin = Cm(margin_cm[3])

    # 正文默认样式
    normal = doc.styles['Normal']
    normal.font.name = 'Times New Roman'
    normal.font.size = Pt(12)
    normal.element.rPr.rFonts.set(qn('w:eastAsia'), SONG)
    pf = normal.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    pf.line_spacing = 1.5
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    return doc


def set_run_font(run, cn=SONG, en='Times New Roman', size=12, bold=False, color=None):
    run.font.name = en
    run.font.size = Pt(size)
    run.font.bold = bold
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn('w:rFonts'))
    if rFonts is None:
        rFonts = OxmlElement('w:rFonts')
        rPr.append(rFonts)
    rFonts.set(qn('w:ascii'), en)
    rFonts.set(qn('w:hAnsi'), en)
    rFonts.set(qn('w:eastAsia'), cn)
    if color:
        run.font.color.rgb = RGBColor(*color)
    return run


def shade(el, fill):
    """给段落或单元格加底纹"""
    pr = el._element.get_or_add_pPr() if hasattr(el, 'paragraph_format') else el._element.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), fill)
    pr.append(shd)


def para(doc, text='', size=12, cn=SONG, en='Times New Roman', bold=False,
         align='left', indent_chars=2.0, space_before=0, space_after=0,
         line_spacing=1.5, color=None):
    """通用段落"""
    p = doc.add_paragraph()
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    p.paragraph_format.line_spacing = line_spacing
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.space_after = Pt(space_after)
    if indent_chars:
        p.paragraph_format.first_line_indent = Pt(size * indent_chars)
    p.alignment = {'left': WD_ALIGN_PARAGRAPH.LEFT, 'center': WD_ALIGN_PARAGRAPH.CENTER,
                   'right': WD_ALIGN_PARAGRAPH.RIGHT,
                   'justify': WD_ALIGN_PARAGRAPH.JUSTIFY}[align]
    if text:
        set_run_font(p.add_run(text), cn=cn, en=en, size=size, bold=bold, color=color)
    return p


def rich(doc, segments, size=12, align='left', indent_chars=2.0, line_spacing=1.5,
         space_before=0, space_after=0):
    """富文本段落：segments = [(文本, {粗体/字体/颜色/字号}), ...]"""
    p = doc.add_paragraph()
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    p.paragraph_format.line_spacing = line_spacing
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.space_after = Pt(space_after)
    if indent_chars:
        p.paragraph_format.first_line_indent = Pt(size * indent_chars)
    p.alignment = {'left': WD_ALIGN_PARAGRAPH.LEFT, 'center': WD_ALIGN_PARAGRAPH.CENTER,
                   'justify': WD_ALIGN_PARAGRAPH.JUSTIFY}[align]
    for text, opt in segments:
        set_run_font(p.add_run(text), cn=opt.get('cn', SONG), en=opt.get('en', 'Times New Roman'),
                     size=opt.get('size', size), bold=opt.get('bold', False),
                     color=opt.get('color'))
    return p


# ----------------------------------------------------------------------
# 标题
# ----------------------------------------------------------------------
def heading(doc, text, level=1, page_break=False):
    if page_break:
        doc.add_page_break()
    p = doc.add_paragraph()
    p.style = doc.styles['Heading %d' % min(level, 4)]
    pf = p.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    pf.line_spacing = 1.5
    size = {1: 16, 2: 14, 3: 12, 4: 12}[level]
    pf.space_before = Pt({1: 18, 2: 12, 3: 8, 4: 6}[level])
    pf.space_after = Pt({1: 14, 2: 8, 3: 6, 4: 4}[level])
    pf.first_line_indent = Pt(0)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if level == 1 else WD_ALIGN_PARAGRAPH.LEFT
    set_run_font(p.add_run(text), cn=HEI, en='Times New Roman', size=size, bold=(level <= 2))
    # 让标题出现在目录中
    p.style.font.color.rgb = RGBColor(0x1A, 0x1A, 0x1A)
    return p


def add_toc(doc, levels='1-3'):
    """插入可自动更新的目录域"""
    p = doc.add_paragraph()
    p.paragraph_format.first_line_indent = Pt(0)
    run = p.add_run()
    fld_begin = OxmlElement('w:fldChar')
    fld_begin.set(qn('w:fldCharType'), 'begin')
    instr = OxmlElement('w:instrText')
    instr.set(qn('xml:space'), 'preserve')
    instr.text = 'TOC \\o "%s" \\h \\z \\u' % levels
    fld_sep = OxmlElement('w:fldChar')
    fld_sep.set(qn('w:fldCharType'), 'separate')
    fld_text = OxmlElement('w:t')
    fld_text.text = '（在 Word 中按 F9 或右键“更新域”生成目录）'
    fld_end = OxmlElement('w:fldChar')
    fld_end.set(qn('w:fldCharType'), 'end')
    for e in (fld_begin, instr, fld_sep, fld_text, fld_end):
        run._element.append(e)
    return p


def add_page_number_footer(section, prefix=''):
    """页脚居中页码"""
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.first_line_indent = Pt(0)
    if prefix:
        set_run_font(p.add_run(prefix), size=10.5)
    run = p.add_run()
    set_run_font(run, size=10.5)
    fld_begin = OxmlElement('w:fldChar'); fld_begin.set(qn('w:fldCharType'), 'begin')
    instr = OxmlElement('w:instrText'); instr.set(qn('xml:space'), 'preserve'); instr.text = 'PAGE'
    fld_end = OxmlElement('w:fldChar'); fld_end.set(qn('w:fldCharType'), 'end')
    for e in (fld_begin, instr, fld_end):
        run._element.append(e)


# ----------------------------------------------------------------------
# 图 / 表
# ----------------------------------------------------------------------
def figure(doc, path, caption, width_cm=15.0):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.first_line_indent = Pt(0)
    p.paragraph_format.space_before = Pt(8)
    p.add_run().add_picture(path, width=Cm(width_cm))
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.first_line_indent = Pt(0)
    cap.paragraph_format.space_after = Pt(12)
    set_run_font(cap.add_run(caption), cn=SONG, size=10.5, bold=True)
    return cap


def table_caption(doc, caption):
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.first_line_indent = Pt(0)
    cap.paragraph_format.space_before = Pt(8)
    cap.paragraph_format.space_after = Pt(4)
    set_run_font(cap.add_run(caption), cn=SONG, size=10.5, bold=True)


def table(doc, headers, rows, widths=None, font_size=10.5, header_fill='E8E4DC',
          align_right=None, note=None):
    """三线表风格表格"""
    align_right = align_right or []
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = True

    hdr = t.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = ''
        p = hdr[i].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.first_line_indent = Pt(0)
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.line_spacing = 1.0
        set_run_font(p.add_run(str(h)), cn=HEI, size=font_size, bold=True)
        shade(hdr[i], header_fill)

    for r in rows:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = ''
            p = cells[i].paragraphs[0]
            p.alignment = (WD_ALIGN_PARAGRAPH.RIGHT if i in align_right
                           else WD_ALIGN_PARAGRAPH.LEFT)
            p.paragraph_format.first_line_indent = Pt(0)
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(1)
            p.paragraph_format.line_spacing = 1.0
            set_run_font(p.add_run('' if v is None else str(v)), cn=SONG, size=font_size)

    if widths:
        for i, w in enumerate(widths):
            for row in t.rows:
                row.cells[i].width = Cm(w)

    if note:
        n = doc.add_paragraph()
        n.paragraph_format.first_line_indent = Pt(0)
        n.paragraph_format.space_before = Pt(3)
        n.paragraph_format.space_after = Pt(10)
        set_run_font(n.add_run(note), cn=SONG, size=9, color=(0x60, 0x60, 0x60))
    else:
        doc.add_paragraph().paragraph_format.space_after = Pt(6)
    return t


def code_block(doc, code, size=9, fill='F4F2EE'):
    """等宽代码块（逐行段落，保证不丢格式）"""
    lines = code.strip('\n').split('\n')
    t = doc.add_table(rows=1, cols=1)
    t.style = 'Table Grid'
    cell = t.rows[0].cells[0]
    cell.text = ''
    shade(cell, fill)
    first = True
    for ln in lines:
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        first = False
        p.paragraph_format.first_line_indent = Pt(0)
        p.paragraph_format.line_spacing = 1.15
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        set_run_font(p.add_run(ln if ln else ' '), cn=MONO_CN, en=MONO_EN, size=size)
    doc.add_paragraph().paragraph_format.space_after = Pt(6)
    return t


def bullets(doc, items, size=12, ordered=False):
    for i, it in enumerate(items):
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
        pf.line_spacing = 1.5
        pf.left_indent = Pt(size * 2)
        pf.first_line_indent = Pt(0)
        pf.space_after = Pt(0)
        prefix = ('%d) ' % (i + 1)) if ordered else '· '
        if isinstance(it, tuple):
            set_run_font(p.add_run(prefix), size=size)
            set_run_font(p.add_run(it[0]), cn=HEI, size=size, bold=True)
            set_run_font(p.add_run(it[1]), size=size)
        else:
            set_run_font(p.add_run(prefix + it), size=size)


def page_break(doc):
    doc.add_page_break()
