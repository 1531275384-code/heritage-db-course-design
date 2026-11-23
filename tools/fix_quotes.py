#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把报告源文件中的半角双引号统一转换为中文弯引号（“ ”）。
--------------------------------------------------------------------------
转换策略：
    1. 跳过三引号文档字符串行；
    2. 对其余文本按出现顺序交替替换为 “ 与 ”，
       同时用上下文规则修正（后接中文且前非中文 -> 开引号；反之 -> 闭引号）；
    3. 报告任何仍处于未闭合状态的引号所在行，供人工复核。
"""

import io
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(BASE, 'make_report.py')

CJK = r'[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]'


def convert(src):
    lines = src.split('\n')
    out = []
    open_state = True          # True 表示下一个引号应为开引号
    problems = []

    for idx, ln in enumerate(lines, 1):
        if '"""' in ln:
            out.append(ln)
            continue

        chars = list(ln)
        buf = []
        i = 0
        while i < len(chars):
            ch = chars[i]
            if ch != '"':
                buf.append(ch)
                i += 1
                continue

            prev = chars[i - 1] if i > 0 else ''
            nxt = chars[i + 1] if i + 1 < len(chars) else ''

            prev_cjk = bool(re.match(CJK, prev)) if prev else False
            next_cjk = bool(re.match(CJK, nxt)) if nxt else False

            if next_cjk and not prev_cjk:
                is_open = True
            elif prev_cjk and not next_cjk:
                is_open = False
            else:
                is_open = open_state

            buf.append('\u201c' if is_open else '\u201d')
            open_state = not is_open
            i += 1

        out.append(''.join(buf))

    if not open_state:
        problems.append('全文引号配对检查：数量为奇数，存在未闭合引号')

    return '\n'.join(out), problems


def main():
    with io.open(TARGET, 'r', encoding='utf-8') as fh:
        src = fh.read()

    before = src.count('"')
    dst, problems = convert(src)
    after = dst.count('"')

    with io.open(TARGET, 'w', encoding='utf-8') as fh:
        fh.write(dst)

    print('半角双引号：%d -> %d（保留的三引号 %d 个）' % (before, after, after))
    print('中文弯引号：开 “ %d 个，闭 ” %d 个'
          % (dst.count('\u201c'), dst.count('\u201d')))
    for p in problems:
        print('[!] ' + p)
    if dst.count('\u201c') != dst.count('\u201d'):
        print('[!] 弯引号不配对，请人工复核')


if __name__ == '__main__':
    main()
