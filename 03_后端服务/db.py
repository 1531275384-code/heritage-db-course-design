# -*- coding: utf-8 -*-
"""
数据访问层
--------------------------------------------------------------------------
封装数据库连接与通用查询/存储过程调用，向上层屏蔽 pymysql 细节。
提供：
    - query()      查询返回列表（字典形式）
    - query_one()  查询返回单条
    - execute()    执行写操作，返回受影响行数
    - callproc()   调用存储过程，支持 OUT 参数与多结果集
    - scalars()    执行"多结果集"的过程并按结果集顺序返回

说明：教学演示环境采用"每次请求新建连接"的简单策略，
      生产环境可替换为 DBUtils.PooledDB 连接池，接口保持不变。
"""

import pymysql
from pymysql.cursors import DictCursor

from config import DBConfig


class DBError(Exception):
    """数据库访问异常"""
    pass


def get_conn():
    """创建一条新连接"""
    try:
        return pymysql.connect(cursorclass=DictCursor, autocommit=True, **DBConfig.as_dict())
    except Exception as exc:
        raise DBError('数据库连接失败：%s' % exc)


def _clean(rows):
    """把 Decimal / date / datetime 等类型转换为可 JSON 序列化的原生类型"""
    import datetime
    from decimal import Decimal

    def conv(v):
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, datetime.datetime):
            return v.strftime('%Y-%m-%d %H:%M')
        if isinstance(v, datetime.date):
            return v.strftime('%Y-%m-%d')
        if isinstance(v, datetime.timedelta):
            return str(v)
        if isinstance(v, bytes):
            return v.decode('utf-8', 'replace')
        return v

    out = []
    for row in rows:
        out.append(dict((k, conv(v)) for k, v in row.items()))
    return out


def _args(args):
    """
    统一参数预处理。
    说明：当 SQL 中不含占位符时返回 None，令 PyMySQL 跳过 % 格式化，
          这样 SQL 里可以正常书写 DATE_FORMAT(x, '%Y-%m') 这类字面量百分号；
          当存在占位符时返回元组，此时 SQL 中的 %s 才会被正确替换。
    """
    return tuple(args) if args else None


def query(sql, args=None):
    """执行查询，返回字典列表"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, _args(args))
            rows = cur.fetchall()
            return _clean(rows)
    except pymysql.MySQLError as exc:
        raise DBError(str(exc))
    finally:
        conn.close()


def query_one(sql, args=None):
    """执行查询，返回首行或 None"""
    rows = query(sql, args)
    return rows[0] if rows else None


def scalar(sql, args=None):
    """执行查询，返回首行首列"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, _args(args))
            row = cur.fetchone()
            if not row:
                return None
            return list(row.values())[0]
    except pymysql.MySQLError as exc:
        raise DBError(str(exc))
    finally:
        conn.close()


def execute(sql, args=None):
    """执行写操作，返回受影响行数"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            n = cur.execute(sql, _args(args))
            return n
    except pymysql.MySQLError as exc:
        raise DBError(str(exc))
    finally:
        conn.close()


def callproc(name, in_args, out_count=0, multi=False):
    """
    调用存储过程（仅 IN 参数）。

    :param name:      过程名
    :param in_args:   IN 参数列表
    :param out_count: OUT 参数个数（本函数不负责回填，仅为保持签名一致）
    :param multi:     是否收集多个结果集（True 返回 list[list[dict]]）
    :return: multi=True  -> (结果集列表, [])
             multi=False -> (单个结果集, [])
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            args = tuple(in_args) + tuple([None] * out_count)
            cur.callproc(name, args)

            if multi:
                sets = []
                while True:
                    try:
                        sets.append(_clean(cur.fetchall()))
                    except Exception:
                        pass
                    if not cur.nextset():
                        break
                return sets, []

            try:
                rows = cur.fetchall()
            except Exception:
                rows = []
            return _clean(rows), []
    except pymysql.MySQLError as exc:
        raise DBError(str(exc))
    finally:
        conn.close()


def callproc_out(name, in_args, out_count=1):
    """
    调用"带 OUT 参数"的存储过程，返回 OUT 参数值列表。

    背景：PyMySQL 的 cursor.callproc() 只负责发送调用，不会像 MySQLdb 那样
          把 OUT 参数回填到参数列表中。因此这里改用"会话变量中转"的写法：
              SET @p_out_0 = NULL;  CALL p(?, ?, @p_out_0);  SELECT @p_out_0;
          三条语句在同一个连接（同一会话）中顺序执行，即可拿到 OUT 值。
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for i in range(out_count):
                cur.execute('SET @p_out_%d = NULL' % i)

            out_vars = ', '.join('@p_out_%d' % i for i in range(out_count))
            head = ', '.join(['%s'] * len(in_args))
            args_sql = head + (', ' if head and out_vars else '') + out_vars

            cur.execute('CALL %s(%s)' % (name, args_sql), _args(in_args))

            # 清空过程体可能产生的额外结果集，避免后续语句 "Commands out of sync"
            try:
                while cur.nextset():
                    pass
            except Exception:
                pass

            cur.execute('SELECT ' + ', '.join('@p_out_%d AS o%d' % (i, i)
                                              for i in range(out_count)))
            row = cur.fetchone() or {}
            return [row.get('o%d' % i) for i in range(out_count)]
    except pymysql.MySQLError as exc:
        raise DBError(str(exc))
    finally:
        conn.close()


def server_info():
    """返回数据库版本等环境信息（用于健康检查）"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT VERSION() AS v, DATABASE() AS d, NOW() AS t')
            row = cur.fetchone()
            return _clean([row])[0]
    finally:
        conn.close()
