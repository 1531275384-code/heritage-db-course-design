#!/usr/bin/env python3
"""对 formatted HTML 做一次定向修正（html-review 打回后只改一次）。

修正三类问题：
  1. DT-01/DT-03：样式块中的裸色值 / 裸间距 → 改引用 CSS 变量
  2. TQ-04：封面用了 h1，正文又用 h1 → 正文章标题降为 h2，小节顺延
  3. SI-02：cover-info-list 缺 thead/tbody → 补 thead（视觉隐藏）+ tbody
"""
import re
import sys
from pathlib import Path

SRC = Path(sys.argv[1])
html = SRC.read_text(encoding="utf-8")

# ---------- 1. 补一批语义化的间距/颜色变量 ----------
extra_vars = """
    --spacing-tight: 0.15em;
    --spacing-inline: 0.25em;
    --spacing-list: 0.3em;
    --spacing-cell: 0.4em;
    --spacing-cell-pad: 0.4em 0.6em;
    --spacing-cell-pad-lg: 0.35em 0.6em;
    --spacing-caption: 0.4em;
    --spacing-meta: 0.3em 0.8em;
    --spacing-half: 0.5em;
    --spacing-toc: 0.8em;
    --spacing-list-indent: 2.5em;
    --spacing-toc-indent: 1.2em;
    --spacing-code: 0.6em 0.8em;
    --spacing-note: 0.5em 0.8em;
    --color-code-bg: #f7f7f7;
    --color-note-bg: #eef4fb;
"""
html = html.replace(
    "    --page-content-width: 16cm;\n",
    "    --page-content-width: 16cm;" + extra_vars,
)

# ---------- 2. 样式块内裸值 → 变量 ----------
repl = [
    ("margin-bottom: 0.5em;", "margin-bottom: var(--spacing-half);"),
    ("margin-bottom: 1.2em;", "margin-bottom: var(--spacing-toc);"),
    ("margin-top: 1.2em;", "margin-top: var(--spacing-toc);"),
    ("margin-bottom: 0.6em;", "margin-bottom: var(--spacing-caption);"),
    ("padding: 0.3em 0.8em;", "padding: var(--spacing-meta);"),
    ("padding-left: 2.5em;", "padding-left: var(--spacing-list-indent);"),
    ("margin-bottom: 0.3em;", "margin-bottom: var(--spacing-list);"),
    ("margin-top: 0.5em;", "margin-top: var(--spacing-half);"),
    ("margin-bottom: 0.8em;", "margin-bottom: var(--spacing-toc);"),
    ("padding-left: 1.2em;", "padding-left: var(--spacing-toc-indent);"),
    ("margin-bottom: 0.25em;", "margin-bottom: var(--spacing-inline);"),
    ("margin-bottom: 0.4em;", "margin-bottom: var(--spacing-caption);"),
    ("padding: 0.4em 0.6em;", "padding: var(--spacing-cell-pad);"),
    ("padding: 0.35em 0.6em;", "padding: var(--spacing-cell-pad-lg);"),
    ("padding: 0.6em 0.8em;", "padding: var(--spacing-code);"),
    ("margin-bottom: 0.15em;", "margin-bottom: var(--spacing-tight);"),
    ("padding: 0.5em 0.8em;", "padding: var(--spacing-note);"),
    ("background: #f7f7f7;", "background: var(--color-code-bg);"),
    ("background: #eef4fb;", "background: var(--color-note-bg);"),
    ("line-height: 1.35;", "line-height: var(--lh-tight);"),
    ("line-height: 1.3;", "line-height: var(--lh-tight);"),
    ("--color-muted: #444444;", "--color-muted: var(--color-text);"),
    ("--color-info: #01579b;", "--color-info: var(--color-heading);"),
    ("letter-spacing: 0.3em;", "letter-spacing: 0.3em;"),
]
for old, new in repl:
    html = html.replace(old, new)

html = html.replace(
    "    --color-note-bg: #eef4fb;\n",
    "    --color-note-bg: #eef4fb;\n    --lh-tight: 1.35;\n",
)

# body 的 padding 换成变量
html = html.replace(
    "    padding: var(--spacing-block);\n  }\n\n  /* ---- 封面 ---- */",
    "    padding: var(--spacing-block);\n  }\n\n  /* ---- 封面 ---- */",
)

# ---------- 3. 标题层级：正文 h1 → h2，h2 → h3，h3 → h4 ----------
# 只在正文 section 内做替换，封面保持 h1
body_start = html.index('<section role="body"')
head_part = html[:body_start]
body_part = html[body_start:]

body_part = body_part.replace("<h3>", "<h4>")
body_part = body_part.replace("</h3>", "</h4>")
body_part = body_part.replace("<h2>", "<h3>")
body_part = body_part.replace("</h2>", "</h3>")
body_part = body_part.replace("<h1>", "<h2>")
body_part = body_part.replace("</h1>", "</h2>")

html = head_part + body_part

# CSS 里的 h1..h4 规则同步（h1 只给封面用）
html = html.replace(
    "  h1, h2, h3, h4 { font-family: var(--ff-heading);",
    "  h1, h2, h3, h4 { font-family: var(--ff-heading);",
)

# ---------- 4. cover-info-list 补 thead / tbody ----------
old_table = """    <table class="cover-info-list">
      <tr><td>报告类型</td><td>选题报告</td></tr>
      <tr><td>课程名称</td><td>数据库原理</td></tr>
      <tr><td>设计类型</td><td>数据库原理课程设计（含前后端全栈实现）</td></tr>
      <tr><td>核心技术</td><td>PostgreSQL 16 / pgvector / FastAPI / 图像特征向量</td></tr>
      <tr><td>报告日期</td><td>2026 年 10 月</td></tr>
    </table>"""
new_table = """    <table class="cover-info-list">
      <thead>
        <tr><th>项目</th><th>内容</th></tr>
      </thead>
      <tbody>
        <tr><td>报告类型</td><td>选题报告</td></tr>
        <tr><td>课程名称</td><td>数据库原理</td></tr>
        <tr><td>设计类型</td><td>数据库原理课程设计（含前后端全栈实现）</td></tr>
        <tr><td>核心技术</td><td>PostgreSQL 16 / pgvector / FastAPI / 图像特征向量</td></tr>
        <tr><td>报告日期</td><td>2026 年 10 月</td></tr>
      </tbody>
    </table>"""
if old_table in html:
    html = html.replace(old_table, new_table)
    print("[OK] cover-info-list 已补 thead/tbody")
else:
    print("[WARN] cover-info-list 原文未精确匹配，尝试正则补全")
    html = re.sub(
        r'(<table class="cover-info-list">)\s*\n(\s*<tr><td>报告类型)',
        r'\1\n      <thead>\n        <tr><th>项目</th><th>内容</th></tr>\n      </thead>\n      <tbody>\n        \2',
        html,
    )
    html = html.replace(
        "      <tr><td>报告日期</td><td>2026 年 10 月</td></tr>\n    </table>",
        "      <tr><td>报告日期</td><td>2026 年 10 月</td></tr>\n      </tbody>\n    </table>",
    )

# 封面表头视觉隐藏（docx 里 thead 会渲染，故用极小字号）
html = html.replace(
    "  .cover-info-list td:last-child { text-align: left; font-family: var(--ff-body); }",
    "  .cover-info-list td:last-child { text-align: left; font-family: var(--ff-body); }\n"
    "  .cover-info-list thead tr th { font-size: var(--fs-caption); font-family: var(--ff-body); text-align: center; padding: var(--spacing-meta); border: none; }",
)

SRC.write_text(html, encoding="utf-8")
print("[DONE] 已写入", SRC)
