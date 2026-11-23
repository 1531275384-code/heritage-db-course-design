#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校验生成的 Word 报告结构完整性。"""
import io
import os
import sys
from docx import Document
from docx.shared import Pt

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
OUT = os.path.join(ROOT, '02_课程设计报告',
                   '地方非遗文化数字化保护数据库管理系统_课程设计报告.docx')

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

doc = Document(OUT)
paras = doc.paragraphs
tables = doc.tables
imgs = [r for r in doc.part.rels.values() if 'image' in r.reltype]

heads = [(p.style.name, p.text) for p in paras if p.style.name.startswith('Heading')]
h1 = [h for h in heads if h[0] == 'Heading 1']
h2 = [h for h in heads if h[0] == 'Heading 2']
h3 = [h for h in heads if h[0] == 'Heading 3']

chars = sum(len(p.text) for p in paras)
for t in tables:
    for row in t.rows:
        for c in row.cells:
            chars += len(c.text)

print('段落数        :', len(paras))
print('表格数        :', len(tables))
print('内嵌图片数    :', len(imgs))
print('一级标题      :', len(h1))
print('二级标题      :', len(h2))
print('三级标题      :', len(h3))
print('正文与表格字符:', chars)
print('文件大小      : %.1f KB' % (os.path.getsize(OUT) / 1024.0))
print()
print('--- 一级标题清单 ---')
for st, tx in h1:
    print('  ' + tx)

print()
print('--- 表格规模抽样 ---')
for i, t in enumerate(tables[:6], 1):
    print('  表 %d: %d 行 x %d 列  首行: %s'
          % (i, len(t.rows), len(t.columns),
             ' | '.join(c.text[:10] for c in t.rows[0].cells)[:70]))

# 检查字体设置是否生效
fonts = set()
for p in paras:
    for r in p.runs:
        if r.font.name:
            fonts.add(r.font.name)
print()
print('显式设置的英文字体:', sorted(fonts))
