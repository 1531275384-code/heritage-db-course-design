"""
数据库连接层。

【数据库原理】
1. 连接池：避免每次请求都走 TCP + 认证握手，把连接复用起来。
   池大小 = 并发请求数上限，过长会压垮数据库（连接本身占内存），
   过短会让请求排队。演示环境 10 条足够。
2. 事务边界：一个业务操作 = 一个事务。成功 COMMIT、异常 ROLLBACK。
   终审这类多表写入必须落在同一事务里，才能保证「要么同时成功、要么同时回滚」。
3. 角色切换：后端以高权限账号连接，再用 SET LOCAL ROLE 切到业务角色，
   让数据库层 GRANT 真正生效（而不只是应用层 if 判断）。
   SET LOCAL 的作用域是当前事务，事务结束自动失效，不会污染连接池里的连接。
"""
from __future__ import annotations

import contextlib
import logging
from typing import Any, Iterator

import psycopg2
import psycopg2.extras
from psycopg2 import pool
from psycopg2.extensions import connection as PGConnection
from psycopg2.extensions import cursor as PGCursor

from app.config import settings

log = logging.getLogger("ich.db")

_pool: pool.ThreadedConnectionPool | None = None

# 业务角色 → 数据库角色（对应 sql/02_index_trigger_view.sql 中 GRANT 的三个角色）
DB_ROLE_BY_APP_ROLE = {
    "heritor": "ich_heritor",
    "expert": "ich_expert",
    "admin": "ich_admin",
}


def init_pool() -> None:
    """应用启动时调用；数据库不可用时抛异常，由 main 决定是否继续启动。"""
    global _pool
    if _pool is not None:
        return
    _pool = pool.ThreadedConnectionPool(
        minconn=settings.DB_POOL_MIN,
        maxconn=settings.DB_POOL_MAX,
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        cursor_factory=psycopg2.extras.RealDictCursor,   # 查询结果直接是 dict
        application_name="ich_heritage_api",
        connect_timeout=5,
    )
    log.info("数据库连接池已建立 %s:%s/%s", settings.DB_HOST, settings.DB_PORT, settings.DB_NAME)


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        log.info("数据库连接池已关闭")


@contextlib.contextmanager
def open_tx(app_role: str | None = None) -> Iterator[PGCursor]:
    """
    统一事务上下文：块内所有 SQL 同生共死。

        with open_tx("expert") as cur:
            cur.execute(...)
            cur.execute(...)
        # 出块即 COMMIT；抛异常则 ROLLBACK

    app_role 为 None 时使用连接账号本身的权限（用于登录查询等系统操作）。
    """
    if _pool is None:
        raise RuntimeError("连接池未初始化，请先调用 init_pool()")

    conn: PGConnection = _pool.getconn()
    cur: PGCursor | None = None
    try:
        conn.autocommit = False
        cur = conn.cursor()
        if app_role:
            db_role = DB_ROLE_BY_APP_ROLE.get(app_role)
            if not db_role:
                raise ValueError(f"未知业务角色: {app_role}")
            # 角色名来自固定白名单字典，不存在注入风险
            cur.execute(f"SET LOCAL ROLE {db_role}")
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        conn.autocommit = True      # 归还池前复位，避免脏状态传递
        _pool.putconn(conn)


def read_all(sql: str, params: Any = None, app_role: str | None = None) -> list[dict]:
    """只读查询便捷函数。"""
    with open_tx(app_role=app_role) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def read_one(sql: str, params: Any = None, app_role: str | None = None) -> dict | None:
    """查询单行，无结果返回 None。"""
    with open_tx(app_role=app_role) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def healthy() -> tuple[bool, str]:
    """
    健康检查：返回 (是否可用, 说明)。

    池未建时**先尝试建一次**，这样返回的是
    "connection refused / 数据库不存在 / 密码错误"这类真实原因，
    而不是把内部状态"连接池未初始化"当成故障描述抛给用户 —— 那句话对排障毫无帮助。
    """
    if _pool is None:
        try:
            init_pool()
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)
            return False, msg

    try:
        with open_tx() as cur:
            cur.execute(
                "SELECT version() AS v,"
                " (SELECT extversion FROM pg_extension WHERE extname = 'vector') AS vec"
            )
            row = cur.fetchone()
        pg_ver = str(row["v"]).split(" on ")[0]
        if row["vec"] is None:
            return False, f"{pg_ver} | ❌ 未安装 pgvector 扩展（CREATE EXTENSION vector）"
        return True, f"{pg_ver} | pgvector={row['vec']}"
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)
        return False, msg


def vector_literal(vec: list[float]) -> str:
    """
    把 Python list 转成 pgvector 的字面量字符串 '[0.1,0.2,...]'。
    psycopg2 未适配 vector 类型，用文本字面量传入最省事，也不会触发 SQL 注入
    （因为它是参数绑定，不是字符串拼接）。
    """
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"
