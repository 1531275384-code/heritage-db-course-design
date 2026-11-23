#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""统计数据库对象数量，输出 JSON 供报告生成脚本引用。"""
import io
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(BASE), '03_后端服务'))

import db  # noqa: E402

S = 'heritage_db'


def c(sql):
    return db.scalar(sql)


def rows(sql):
    return db.query(sql)


def main():
    q = ("SELECT COUNT(*) FROM information_schema.%s WHERE %s")
    info = {}
    info['tables'] = c(q % ('TABLES', "TABLE_SCHEMA='" + S + "' AND TABLE_TYPE='BASE TABLE'"))
    info['views'] = c(q % ('VIEWS', "TABLE_SCHEMA='" + S + "'"))
    info['triggers'] = c(q % ('TRIGGERS', "TRIGGER_SCHEMA='" + S + "'"))
    info['procedures'] = c(q % ('ROUTINES', "ROUTINE_SCHEMA='" + S + "' AND ROUTINE_TYPE='PROCEDURE'"))
    info['functions'] = c(q % ('ROUTINES', "ROUTINE_SCHEMA='" + S + "' AND ROUTINE_TYPE='FUNCTION'"))
    info['events'] = c(q % ('EVENTS', "EVENT_SCHEMA='" + S + "'"))
    info['indexes'] = c("SELECT COUNT(DISTINCT CONCAT(TABLE_NAME,'.',INDEX_NAME)) "
                        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA='" + S + "'")
    info['uk'] = c("SELECT COUNT(DISTINCT CONCAT(TABLE_NAME,'.',INDEX_NAME)) "
                   "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA='" + S + "' "
                   "AND NON_UNIQUE=0 AND INDEX_NAME<>'PRIMARY'")
    info['checks'] = c(q % ('TABLE_CONSTRAINTS', "CONSTRAINT_SCHEMA='" + S + "' AND CONSTRAINT_TYPE='CHECK'"))
    info['fks'] = c(q % ('TABLE_CONSTRAINTS', "CONSTRAINT_SCHEMA='" + S + "' AND CONSTRAINT_TYPE='FOREIGN KEY'"))
    info['pks'] = c(q % ('TABLE_CONSTRAINTS', "CONSTRAINT_SCHEMA='" + S + "' AND CONSTRAINT_TYPE='PRIMARY KEY'"))
    info['columns'] = c(q % ('COLUMNS', "TABLE_SCHEMA='" + S + "'"))
    info['notnull'] = c(q % ('COLUMNS', "TABLE_SCHEMA='" + S + "' AND IS_NULLABLE='NO'"))
    info['pk_cols'] = c(q % ('COLUMNS', "TABLE_SCHEMA='" + S + "' AND COLUMN_KEY='PRI'"))
    info['comments'] = c(q % ('TABLES', "TABLE_SCHEMA='" + S + "' AND TABLE_COMMENT<>''"))
    info['col_comments'] = c(q % ('COLUMNS', "TABLE_SCHEMA='" + S + "' AND COLUMN_COMMENT<>''"))

    info['table_rows'] = rows("""
        SELECT TABLE_NAME AS name, TABLE_COMMENT AS comment, TABLE_ROWS AS est_rows
          FROM information_schema.TABLES
         WHERE TABLE_SCHEMA='%s' AND TABLE_TYPE='BASE TABLE'
         ORDER BY TABLE_NAME""" % S)

    info['table_columns'] = rows("""
        SELECT TABLE_NAME AS tbl, COLUMN_NAME AS col, COLUMN_TYPE AS type,
               IS_NULLABLE AS nullable, COLUMN_KEY AS ckey, COLUMN_DEFAULT AS dft,
               EXTRA AS extra, COLUMN_COMMENT AS comment
          FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA='%s'
         ORDER BY TABLE_NAME, ORDINAL_POSITION""" % S)

    info['index_detail'] = rows("""
        SELECT TABLE_NAME AS tbl, INDEX_NAME AS idx, NON_UNIQUE AS nonuniq,
               GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS cols
          FROM information_schema.STATISTICS
         WHERE TABLE_SCHEMA='%s' AND INDEX_NAME<>'PRIMARY'
         GROUP BY TABLE_NAME, INDEX_NAME, NON_UNIQUE
         ORDER BY TABLE_NAME, INDEX_NAME""" % S)

    info['constraint_detail'] = rows("""
        SELECT TABLE_NAME AS tbl, CONSTRAINT_NAME AS name, CONSTRAINT_TYPE AS type
          FROM information_schema.TABLE_CONSTRAINTS
         WHERE CONSTRAINT_SCHEMA='%s' AND CONSTRAINT_TYPE<>'PRIMARY KEY'
         ORDER BY TABLE_NAME, CONSTRAINT_TYPE, CONSTRAINT_NAME""" % S)

    info['checks_detail'] = rows("""
        SELECT tc.TABLE_NAME AS tbl, tc.CONSTRAINT_NAME AS name,
               cc.CHECK_CLAUSE AS clause
          FROM information_schema.TABLE_CONSTRAINTS tc
          JOIN information_schema.CHECK_CONSTRAINTS cc
            ON cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA
           AND cc.CONSTRAINT_NAME   = tc.CONSTRAINT_NAME
         WHERE tc.CONSTRAINT_SCHEMA='%s' AND tc.CONSTRAINT_TYPE='CHECK'
         ORDER BY tc.TABLE_NAME, tc.CONSTRAINT_NAME""" % S)

    info['views_detail'] = rows("""
        SELECT TABLE_NAME AS name, VIEW_DEFINITION AS defn
          FROM information_schema.VIEWS WHERE TABLE_SCHEMA='%s'
         ORDER BY TABLE_NAME""" % S)

    info['dnf'] = {k: v for k, v in info.items()}

    out = os.path.join(BASE, '_dbstats.json')
    with io.open(out, 'w', encoding='utf-8') as fh:
        fh.write(json.dumps(info, ensure_ascii=False, indent=1, default=str))

    brief = {k: info[k] for k in ('tables', 'views', 'triggers', 'procedures', 'functions',
                                  'events', 'indexes', 'uk', 'checks', 'fks', 'pks',
                                  'columns', 'notnull', 'pk_cols', 'col_comments')}
    print(json.dumps(brief, ensure_ascii=False, indent=1))
    print('table_rows:')
    for r in info['table_rows']:
        print('   %-28s %s' % (r['name'], r['comment']))


if __name__ == '__main__':
    main()
