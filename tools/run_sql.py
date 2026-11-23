#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SQL 脚本执行器：按 MySQL 客户端语义拆分语句（支持 DELIMITER），逐条执行并报告结果。"""
import sys
import io
import pymysql

CONF = dict(host='127.0.0.1', port=3399, user='root', password='',
            charset='utf8mb4', autocommit=True, cursorclass=pymysql.cursors.Cursor)


def split_statements(text):
    """按字符状态机拆分 SQL 语句，正确处理字符串、行注释、块注释与自定义分隔符。"""
    stmts = []
    buf = []
    delimiter = ';'
    i = 0
    n = len(text)
    state = 'normal'

    while i < n:
        ch = text[i]

        if state == 'normal':
            # 行注释
            if ch == '-' and text[i:i + 2] == '--' and (i + 2 >= n or text[i + 2] in ' \t\r\n'):
                j = text.find('\n', i)
                i = n if j == -1 else j + 1
                continue
            if ch == '#':
                j = text.find('\n', i)
                i = n if j == -1 else j + 1
                continue
            if ch == '/' and text[i:i + 2] == '/*':
                j = text.find('*/', i + 2)
                i = n if j == -1 else j + 2
                continue
            if ch == "'":
                state = 'squote'
                buf.append(ch)
                i += 1
                continue
            if ch == '"':
                state = 'dquote'
                buf.append(ch)
                i += 1
                continue
            if ch == '\\':
                buf.append(ch)
                if i + 1 < n:
                    buf.append(text[i + 1])
                i += 2
                continue
            # DELIMITER 指令（仅在行首生效）
            if text[i:i + 9].upper() == 'DELIMITER' and not ''.join(buf).strip():
                k = text.find('\n', i)
                if k == -1:
                    k = n
                newdelim = text[i + 9:k].strip()
                if newdelim:
                    delimiter = newdelim
                buf = []
                i = k + 1
                continue
            if text.startswith(delimiter, i):
                stmt = ''.join(buf).strip()
                if stmt:
                    stmts.append(stmt)
                buf = []
                i += len(delimiter)
                continue
            buf.append(ch)
            i += 1

        elif state == 'squote':
            if ch == '\\':
                buf.append(ch)
                if i + 1 < n:
                    buf.append(text[i + 1])
                i += 2
                continue
            if ch == "'":
                if text[i:i + 2] == "''":
                    buf.append("''")
                    i += 2
                    continue
                state = 'normal'
            buf.append(ch)
            i += 1

        elif state == 'dquote':
            if ch == '\\':
                buf.append(ch)
                if i + 1 < n:
                    buf.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                state = 'normal'
            buf.append(ch)
            i += 1

    rest = ''.join(buf).strip()
    if rest:
        stmts.append(rest)
    return stmts


def main():
    files = sys.argv[1:]
    conn = pymysql.connect(**CONF)

    out = []
    total_ok = 0
    total_err = 0
    err_detail = []

    for path in files:
        with io.open(path, 'r', encoding='utf-8') as fh:
            text = fh.read()
        stmts = split_statements(text)
        out.append('=========== %s  (共 %d 条语句) ===========' % (path, len(stmts)))
        for idx, stmt in enumerate(stmts, 1):
            head = ' '.join(stmt.split())[:70]
            try:
                cur = conn.cursor()
                cur.execute(stmt)
                try:
                    rows = cur.fetchall()
                    if rows:
                        out.append('  [OK ] #%d (%d 行返回) %s' % (idx, len(rows), head))
                        for r in rows[:4]:
                            out.append('        -> ' + str(r)[:180])
                        if len(rows) > 4:
                            out.append('        -> ... 共 %d 行' % len(rows))
                    else:
                        out.append('  [OK ] #%d (影响 %d 行) %s' % (idx, cur.rowcount, head))
                except Exception:
                    out.append('  [OK ] #%d (无结果集) %s' % (idx, head))
                cur.close()
                total_ok += 1
            except Exception as e:
                total_err += 1
                msg = str(e).replace('\n', ' ')[:220]
                out.append('  [ERR] #%d %s\n        => %s' % (idx, head, msg))
                err_detail.append('%s #%d: %s' % (path, idx, msg))
        out.append('')

    out.append('================= 汇总 =================')
    out.append('成功语句: %d    失败语句: %d' % (total_ok, total_err))

    conn.close()
    with io.open(sys.argv[0] + '.result.txt', 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(out))
    print('\n'.join(out[-6:]))


if __name__ == '__main__':
    main()
