"""真实 PostgreSQL 多会话并发验收。

为什么单独一套
--------------
`approve_asset` / `create_license` 的注释里写着「两个专家同时点通过不会双写」
「独占授权由部分唯一索引兜底」。但这句话成立与否，取决于**两个真实会话同时
到达同一个临界区**——单连接顺序调用永远测不出来：第二种情况根本不会发生。

同理，`asset_genealogy` 的 DAG 环检测也不能靠静态审查证明：
A→B、B→C 之后再插 C→A 会不会被拦、两个会话同时插 A→B 与 B→A 会不会双双通过，
只有真的从两个连接写进去才知道。

做法
----
1. 从现有库克隆一个**一次性沙箱库**（`CREATE DATABASE ... TEMPLATE`），
   所有写入都落在沙箱里，跑完 DROP —— 不污染演示数据，也不会因为
   audit_record / license_record 的追加式不可删除特性越跑越脏。
2. 每个并发场景用 `threading.Barrier` 让 N 个线程**同时**发起操作，
   每个线程有自己的连接（真正的多会话，不是同一事务里的两次调用）。
3. 断言「不变量」而不是「谁赢」：并发终审必须恰好一个成功；
   并发独占授权必须恰好一条生效记录；并发反向建边必须恰好一条落库。

运行
----
    python -m tests.test_pg_concurrency
    PG_DSN="host=127.0.0.1 port=55433 user=postgres dbname=ich_heritage" \
        python -m tests.test_pg_concurrency

前置：目标库已按 sql/01→03 建好，且**当前没有别的会话连着它**
（PostgreSQL 克隆模板库时不允许源库有活动连接；本套件不会去踢人）。
"""

from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
import traceback
import uuid
from datetime import date

import psycopg2
import psycopg2.extras
from psycopg2 import errors, pool as pgpool

PASS = 0
FAIL = 0
FAILED: list[str] = []

# 沙箱连接参数，main() 里赋值；场景函数开新会话时用它保证连到同一个沙箱
BASE: dict = {}
SANDBOX: str = ""


# =============================================================================
#  输出（与 check_pg 同一套记号，run_all 靠「N 项通过」抓数）
# =============================================================================
def section(title: str) -> None:
    print(f"\n[{title}]")


def check(cond: bool, name: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ [PASS] {name}" + (f"  —— {detail}" if detail else ""))
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  ✗ [FAIL] {name}" + (f"  —— {detail}" if detail else ""))


def note(text: str) -> None:
    print(f"      {text}")


# =============================================================================
#  连接与沙箱
# =============================================================================
def conninfo() -> dict:
    """PG_DSN 优先；否则回落到 ICH_DB_* 环境变量（与 app/config.py 同名）。"""
    raw = os.environ.get("PG_DSN", "").strip()
    if raw:
        d = dict(psycopg2.extensions.parse_dsn(raw))
    else:
        d = dict(
            host=os.environ.get("ICH_DB_HOST", "127.0.0.1"),
            port=os.environ.get("ICH_DB_PORT", "5432"),
            dbname=os.environ.get("ICH_DB_NAME", "ich_heritage"),
            user=os.environ.get("ICH_DB_USER", "postgres"),
            password=os.environ.get("ICH_DB_PASSWORD", ""),
        )
    d["connect_timeout"] = 10
    return d


def create_sandbox(base: dict) -> tuple[str, psycopg2.extensions.connection]:
    """
    克隆一个一次性数据库。

    为什么用 TEMPLATE 而不是 `CREATE DATABASE` + 重跑 SQL：
      sql/01 里有 `\\i` / `:ICH_VEC_TYPE` 这类 psql 元命令，psycopg2 执行不了；
      模板克隆是文件级复制，几毫秒完成，且保证沙箱与"刚建好的库"完全一致。
    """
    name = f"ich_conc_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    maint = dict(base)
    maint["dbname"] = "postgres"
    maint["application_name"] = "ich_concurrency_maintenance"
    conn = psycopg2.connect(**maint)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        try:
            cur.execute(f'CREATE DATABASE "{name}" TEMPLATE "{base["dbname"]}"')
        except psycopg2.Error as exc:
            # 不踢别人的会话：共享环境里那是危险动作，宁可让本套件明确失败
            raise RuntimeError(
                f"无法克隆模板库 {base['dbname']}：{exc}\n"
                "  原因通常是还有别的会话连着它（如正在运行的 API 服务）。\n"
                "  处理：先停掉那些连接，或用 PG_DSN / ICH_DB_* 指向一个空闲库。"
            ) from exc
    return name, conn


def connect(app_name: str = "ich_concurrency_test") -> psycopg2.extensions.connection:
    d = dict(BASE)
    d["dbname"] = SANDBOX
    d["application_name"] = app_name
    return psycopg2.connect(**d)


# =============================================================================
#  夹具：直接写库造状态
#  （建库账号不在业务角色白名单里，fn_asset_status_guard 对其放行，这是设计内的）
# =============================================================================
def fixture_user(conn, username: str, role: str, display: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_user (username, display_name, role, password_hash)
            VALUES (%s, %s, %s, 'not-a-real-hash')
            ON CONFLICT (username) DO UPDATE SET display_name = EXCLUDED.display_name
            RETURNING user_id
            """,
            (username, display, role),
        )
        return cur.fetchone()[0]


def fixture_asset(
    conn,
    title: str,
    owner_id: int,
    *,
    status: str = "draft",
    level: str = "public",
    ai_level: str | None = None,
    ai_conf: float | None = None,
) -> int:
    h = hashlib.sha256(f"{title}-{uuid.uuid4()}".encode()).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO asset (title, owner_id, file_path, file_hash, file_size,
                               audit_status, right_level, ai_suggest_level, ai_confidence)
            VALUES (%s, %s, %s, %s, 1024, %s, %s, %s, %s)
            RETURNING asset_id
            """,
            (title, owner_id, f"test/{title}.jpg", h, status, level, ai_level, ai_conf),
        )
        return cur.fetchone()[0]


def fixture_pending(conn, title: str, owner_id: int) -> int:
    """造一条「AI 已出建议、等专家终审」的资产：asset=pending + 一条 pending 流水。"""
    aid = fixture_asset(conn, title, owner_id, status="pending",
                        ai_level="derived", ai_conf=0.9)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_record (asset_id, state, ai_suggest_level, ai_confidence, similarity_json)
            VALUES (%s, 'pending', 'derived', 0.9, '[]'::jsonb)
            """,
            (aid,),
        )
    return aid


def insert_edge(conn, parent: int, child: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO asset_genealogy (parent_asset_id, child_asset_id) VALUES (%s, %s)",
            (parent, child),
        )


def insert_edge_new_session(parent: int, child: int) -> None:
    """并发场景专用：每次写入都开一条独立会话，避免共享连接（psycopg2 连接非线程安全）。"""
    c = connect("ich_concurrency_edge")
    try:
        c.autocommit = True
        insert_edge(c, parent, child)
    finally:
        c.close()


# =============================================================================
#  并发执行器
# =============================================================================
def race(fns: list, timeout: float = 30.0) -> list[tuple[str, object]]:
    """
    让 len(fns) 个调用在同一瞬间起跑，返回 [("ok", 返回值) | ("err", 异常)]。

    Barrier 的意义：不用它，线程启动有先后，先跑完的那个会让后来者直接看到
    终态，测出来的"恰好一个成功"其实来自调度顺序，而不是锁。
    """
    barrier = threading.Barrier(len(fns))
    out: list[tuple[str, object]] = [("err", RuntimeError("线程未执行"))] * len(fns)

    def worker(i: int) -> None:
        try:
            barrier.wait(timeout=timeout)
        except threading.BrokenBarrierError as exc:
            out[i] = ("err", exc)
            return
        try:
            out[i] = ("ok", fns[i]())
        except BaseException as exc:            # HTTPException 也要接住，统一记录
            out[i] = ("err", exc)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(len(fns))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout)
    return out


def status_code(exc: object) -> int | None:
    """HTTPException 的 status_code；其它异常返回 None。"""
    return getattr(exc, "status_code", None)


def wait_for_lock_wait(observer, timeout: float = 5.0) -> bool:
    """
    观察 pg_stat_activity 里是否有会话正在等锁。

    这是"串行化真的发生了"的直接证据：只看最终结果（一个成功一个 409），
    分不清是锁挡住了，还是第二个请求本来就晚到。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        with observer.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM pg_stat_activity
                 WHERE datname = current_database()
                   AND application_name = 'ich_heritage_api'
                   AND wait_event_type = 'Lock'
                """
            )
            if cur.fetchone()[0] > 0:
                return True
        time.sleep(0.05)
    return False


# =============================================================================
#  场景
# =============================================================================
def scenario_dual_expert(conn, experts: list[int], owner: int) -> None:
    section("1 同作品双专家并发终审（真实两会话）")
    from app import services

    aid = fixture_pending(conn, "并发终审样张", owner)
    results = race([
        lambda: services.approve_asset(
            asset_id=aid, final_level="derived", opinion="并发验收 A",
            expert_id=experts[0], expert_name="专家A"),
        lambda: services.approve_asset(
            asset_id=aid, final_level="derived", opinion="并发验收 B",
            expert_id=experts[1], expert_name="专家B"),
    ])

    ok = [r for r in results if r[0] == "ok"]
    err = [r for r in results if r[0] == "err"]
    check(len(ok) == 1, "两个专家同时终审：恰好一个成功", f"成功 {len(ok)} / 失败 {len(err)}")
    check(err and all(status_code(e) == 409 for _, e in err),
          "另一个被状态机拒绝（409，而不是双写）",
          "; ".join(f"{type(e).__name__}: {e}" for _, e in err)[:160])

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_record WHERE asset_id=%s AND state='approved'", (aid,))
        n_appr = cur.fetchone()[0]
        cur.execute("SELECT audit_status FROM asset WHERE asset_id=%s", (aid,))
        st = cur.fetchone()[0]
        cur.execute(
            """SELECT count(*) FROM system_audit_log
                WHERE table_name='asset' AND record_id=%s AND operation='UPDATE'""",
            (aid,),
        )
        n_log = cur.fetchone()[0]
    check(n_appr == 1, "终审流水只有一条 approved", f"{n_appr} 条")
    check(st == "approved", "资产最终状态为 approved", st)
    check(n_log == 1, "DB 审计日志恰有一条 UPDATE（触发器只记一次）", f"{n_log} 条")


def scenario_lock_observed(conn, experts: list[int], owner: int) -> None:
    section("2 行锁串行化可观测（外部写者持锁时终审确实在等）")
    from app import services

    aid = fixture_pending(conn, "锁等待样张", owner)

    holder = connect("ich_lock_holder")
    holder.autocommit = False
    with holder.cursor() as cur:
        cur.execute("SELECT asset_id FROM asset WHERE asset_id=%s FOR UPDATE", (aid,))

    result: list[tuple[str, object]] = [("err", RuntimeError("未执行"))]

    def call() -> None:
        try:
            services.approve_asset(asset_id=aid, final_level="derived", opinion="锁等待验收",
                                   expert_id=experts[0], expert_name="专家A")
            result[0] = ("ok", None)
        except BaseException as exc:            # noqa: BLE001
            result[0] = ("err", exc)

    t = threading.Thread(target=call, daemon=True)
    t.start()
    blocked = wait_for_lock_wait(conn, timeout=5.0)
    check(blocked, "终审会话被行锁挂起（pg_stat_activity.wait_event_type=Lock）",
          "说明 FOR UPDATE 真的把两个会话串起来了")

    holder.commit()
    holder.close()
    t.join(20)
    check(result[0][0] == "ok", "持锁方提交后，终审继续并成功",
          str(result[0][1])[:120] if result[0][0] == "err" else "")
    with conn.cursor() as cur:
        cur.execute("SELECT audit_status FROM asset WHERE asset_id=%s", (aid,))
        check(cur.fetchone()[0] == "approved", "资产状态已落为 approved")


def scenario_exclusive_license(conn, owner: int) -> None:
    section("3 并发独占授权（部分唯一索引兜底）")
    from app import services

    aid = fixture_asset(conn, "独占授权样张", owner, status="approved", level="derived")

    def payload(name: str) -> dict:
        return {
            "license_type": "exclusive",
            "licensee_name": name,
            "start_date": date.today(),
            "scope": "并发验收",
            "territory": "CN",
        }

    results = race([
        lambda: services.create_license(asset_id=aid, payload=payload("机构甲"),
                                        actor_id=owner, app_role="heritor", is_privileged=False),
        lambda: services.create_license(asset_id=aid, payload=payload("机构乙"),
                                        actor_id=owner, app_role="heritor", is_privileged=False),
    ])
    ok = [r for r in results if r[0] == "ok"]
    err = [r for r in results if r[0] == "err"]
    check(len(ok) == 1, "两份独占授权同时登记：恰好一份成功", f"成功 {len(ok)} / 失败 {len(err)}")
    check(err and all(status_code(e) == 409 for _, e in err),
          "另一份被拒（409：已存在生效独占授权 / 唯一索引冲突）",
          "; ".join(f"{type(e).__name__}: {e}" for _, e in err)[:160])

    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM license_record
                WHERE asset_id=%s AND license_type='exclusive' AND status='active'""",
            (aid,),
        )
        n = cur.fetchone()[0]
    check(n == 1, "生效中的独占授权只有一条（uq_lic_exclusive_active 生效）", f"{n} 条")


def scenario_cycle(conn, owner: int) -> dict[str, int]:
    section("4 谱系 DAG：多跳成环拒绝 + 并发反向建边")
    g = {k: fixture_asset(conn, f"谱系{k}", owner) for k in ("g1", "g2", "g3", "g4")}
    for a, b in (("g1", "g2"), ("g2", "g3"), ("g3", "g4")):
        insert_edge(conn, g[a], g[b])
    check(True, "基础链 g1→g2→g3→g4 建立成功")

    # 三跳闭环
    try:
        insert_edge(conn, g["g4"], g["g1"])
        check(False, "三跳闭环 g4→g1 被拒绝", "居然写进去了")
    except errors.CheckViolation as exc:
        msg = str(exc)
        check("成环" in msg, "三跳闭环被触发器拒绝（SQLSTATE 23514）", msg.splitlines()[0][:90])
        check("闭环路径" in msg, "报错给出闭环路径，便于定位建反的边",
              msg.splitlines()[1][:90] if len(msg.splitlines()) > 1 else "")

    # 菱形应当允许（DAG 允许多条路径到达同一祖先）
    try:
        insert_edge(conn, g["g1"], g["g3"])
        check(True, "菱形 DAG（g1→g3）允许写入", "同一祖先可经多条路径到达")
    except Exception as exc:                   # noqa: BLE001
        check(False, "菱形 DAG（g1→g3）允许写入", f"{type(exc).__name__}: {exc}")

    # 自环由 CHECK 拦，不是环检测触发器
    try:
        insert_edge(conn, g["g1"], g["g1"])
        check(False, "自环 g1→g1 被 ck_gen_not_self 拒绝", "居然写进去了")
    except errors.CheckViolation as exc:
        check("ck_gen_not_self" in str(exc), "自环 g1→g1 被 ck_gen_not_self 拒绝",
              str(exc).splitlines()[0][:80])

    # 重复边由 UNIQUE 拦
    try:
        insert_edge(conn, g["g1"], g["g2"])
        check(False, "重复边被 uq_gen_edge 拒绝", "居然写进去了")
    except errors.UniqueViolation:
        check(True, "重复边被 uq_gen_edge 拒绝", "SQLSTATE 23505")

    # UPDATE 也要过环检测，否则改一条边就能绕过
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE asset_genealogy SET parent_asset_id=%s, child_asset_id=%s "
                "WHERE parent_asset_id=%s AND child_asset_id=%s",
                (g["g3"], g["g1"], g["g1"], g["g3"]),
            )
        check(False, "把 g1→g3 改写成 g3→g1 被拒绝", "UPDATE 绕过了环检测")
    except errors.CheckViolation as exc:
        check("成环" in str(exc), "把 g1→g3 改写成 g3→g1 被拒绝",
              str(exc).splitlines()[0][:80])

    # 并发反向建边：没有咨询锁时两个会话都看不到对方未提交的边，会双双通过（写偏斜）
    s1 = fixture_asset(conn, "偏斜甲", owner)
    s2 = fixture_asset(conn, "偏斜乙", owner)
    results = race([
        lambda: insert_edge_new_session(s1, s2),
        lambda: insert_edge_new_session(s2, s1),
    ], timeout=20)
    ok = [r for r in results if r[0] == "ok"]
    err = [r for r in results if r[0] == "err"]
    check(len(ok) == 1, "并发插入 s1→s2 与 s2→s1：恰好一条落库",
          f"成功 {len(ok)} / 失败 {len(err)}")
    check(err and all(isinstance(e, errors.CheckViolation) for _, e in err),
          "另一条被判成环拒绝", "; ".join(f"{type(e).__name__}" for _, e in err))
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM asset_genealogy
                WHERE (parent_asset_id=%s AND child_asset_id=%s)
                   OR (parent_asset_id=%s AND child_asset_id=%s)""",
            (s1, s2, s2, s1),
        )
        n = cur.fetchone()[0]
    check(n == 1, "库里只剩一条边（咨询锁消除了写偏斜）", f"{n} 条")
    return g


def scenario_lineage(conn, g: dict[str, int]) -> None:
    section("5 递归祖先链（任意深度 + 多路径）")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT l.depth, l.ancestor_id, l.path, l.is_direct_parent
              FROM v_asset_lineage l
             WHERE l.asset_id = %s
             ORDER BY l.depth, l.ancestor_id
            """,
            (g["g4"],),
        )
        rows = cur.fetchall()

    # 边：g1→g2、g2→g3、g3→g4、g1→g3
    # g4 的祖先：g3(1)、g2(2)、g1(2 经 g3)、g1(3 经 g3→g2)
    depths = sorted(r[0] for r in rows)
    check(len(rows) == 4, "g4 的祖先关系共 4 条（多路径不折叠）", f"depth={depths}")
    check(depths == [1, 2, 2, 3], "深度分布 = 1/2/2/3", f"{depths}")
    n_g1 = sum(1 for r in rows if r[1] == g["g1"])
    check(n_g1 == 2, "g1 经两条路径出现在祖先链中（菱形血缘）", f"{n_g1} 次")
    deepest = [r for r in rows if r[0] == 3]
    check(len(deepest) == 1 and list(deepest[0][2]) == [g["g4"], g["g3"], g["g2"], g["g1"]],
          "最深路径 = g4→g3→g2→g1", str(list(deepest[0][2])) if deepest else "无")
    check(all((r[3] is True) == (r[0] == 1) for r in rows),
          "is_direct_parent 仅 depth=1 为真")

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM v_asset_lineage WHERE asset_id=%s", (g["g1"],))
        check(cur.fetchone()[0] == 0, "根节点 g1 没有祖先行（递归正常终止）")

    # 三档角色都要能读：视图权限是单独 GRANT 的，不在 ALL TABLES 里。
    # 必须在显式事务里 SET LOCAL ROLE，否则 autocommit 下 SET LOCAL 无效，
    # 查询会以建库账号身份跑通，测出来的是假通过。
    for role in ("ich_heritor", "ich_expert", "ich_admin"):
        try:
            with conn.cursor() as cur:
                cur.execute("BEGIN")
                cur.execute(f"SET LOCAL ROLE {role}")
                cur.execute("SELECT count(*) FROM v_asset_lineage")
                n = cur.fetchone()[0]
                cur.execute("ROLLBACK")
            check(n >= 4, f"{role} 可读 v_asset_lineage", f"{n} 行")
        except Exception as exc:               # noqa: BLE001
            check(False, f"{role} 可读 v_asset_lineage", f"{type(exc).__name__}: {exc}")


# =============================================================================
#  主流程
# =============================================================================
def main() -> int:
    global BASE, SANDBOX
    print("=" * 74)
    print(" 真实 PostgreSQL 多会话并发验收")
    print("=" * 74)

    BASE = conninfo()
    print(f"  模板库：{BASE.get('user')}@{BASE.get('host')}:{BASE.get('port')}/{BASE.get('dbname')}")

    maint = None
    pool = None
    try:
        SANDBOX, maint = create_sandbox(BASE)
        print(f"  沙箱库：{SANDBOX}（跑完即 DROP）")

        # app.config 在 import 时读环境变量，因此必须先覆盖再导入 app.db
        os.environ["ICH_DB_HOST"] = str(BASE.get("host") or "127.0.0.1")
        os.environ["ICH_DB_PORT"] = str(BASE.get("port") or 5432)
        os.environ["ICH_DB_NAME"] = SANDBOX
        os.environ["ICH_DB_USER"] = str(BASE.get("user") or "postgres")
        os.environ["ICH_DB_PASSWORD"] = str(BASE.get("password") or "")

        import app.db as db          # noqa: E402
        from app.config import settings  # noqa: E402

        pool = pgpool.ThreadedConnectionPool(
            2, 8,
            host=settings.DB_HOST, port=settings.DB_PORT, dbname=settings.DB_NAME,
            user=settings.DB_USER, password=settings.DB_PASSWORD,
            cursor_factory=psycopg2.extras.RealDictCursor,
            application_name="ich_heritage_api",
            connect_timeout=10,
        )
        db._pool = pool

        conn = connect("ich_concurrency_test")
        conn.autocommit = True

        owner = fixture_user(conn, "heritor01", "heritor", "杨秀英")
        e1 = fixture_user(conn, "expert01", "expert", "周文渊")
        e2 = fixture_user(conn, "expert02", "expert", "并发测试专家")

        with conn.cursor() as cur:
            cur.execute("SELECT ich_vector_is_native()")
            note(f"向量层：{'pgvector 原生' if cur.fetchone()[0] else '降级模式'}")

        scenario_dual_expert(conn, [e1, e2], owner)
        scenario_lock_observed(conn, [e1, e2], owner)
        scenario_exclusive_license(conn, owner)
        g = scenario_cycle(conn, owner)
        scenario_lineage(conn, g)

        conn.close()
    except Exception as exc:                   # noqa: BLE001
        FAIL_ADD("套件执行异常", f"{type(exc).__name__}: {exc}")
        traceback.print_exc(limit=3)
    finally:
        if pool is not None:
            try:
                pool.closeall()
            except Exception:                  # noqa: BLE001
                pass
        if SANDBOX and maint:
            try:
                with maint.cursor() as cur:
                    cur.execute(f'DROP DATABASE IF EXISTS "{SANDBOX}"')
                print(f"\n  已清理沙箱库 {SANDBOX}")
            except Exception as exc:           # noqa: BLE001
                print(f"\n  ⚠ 沙箱库清理失败：{exc}")
        if maint:
            maint.close()

    print("\n" + "=" * 74)
    print(f"结果：{PASS} 项通过 / {FAIL} 项失败")
    if FAILED:
        print("失败项：" + "；".join(FAILED))
    print("=" * 74)
    return 1 if FAIL else 0


def FAIL_ADD(name: str, detail: str) -> None:
    global FAIL
    FAIL += 1
    FAILED.append(name)
    print(f"  ✗ [FAIL] {name}  —— {detail}")


if __name__ == "__main__":
    sys.exit(main())
