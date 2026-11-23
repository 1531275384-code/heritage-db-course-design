"""真实 PostgreSQL 验收：把「只有代码」的 12 项变成「已执行」。

背景
----
项目早期因为本机起不了 PostgreSQL（initdb 被拦、pgvector 编译不了），
所有关于列级 GRANT、触发器、HNSW、FOR UPDATE、事务回滚的说法都只有代码，
没有运行证据。本脚本在真实库上逐条验证，并把结论分成三类：

    PASS  行为符合设计
    FAIL  行为不符合设计（需要修）
    GAP   数据库层根本没实现这条规则（只靠应用层兜），如实记录

最后一项 GAP 是刻意保留的：验收报告如果只报 PASS，就失去了验收的意义。

运行
----
    python -m tests.check_pg                       # 默认连 127.0.0.1:55432/heritage
    PG_DSN="host=... dbname=..." python -m tests.check_pg

前置：库已用 `python -m scripts.pg_run --reset` 建好。
"""

from __future__ import annotations

import hashlib
import os
import sys

import psycopg2
from psycopg2 import errors

DSN = os.environ.get(
    "PG_DSN", "host=127.0.0.1 port=55432 user=postgres dbname=heritage"
)

RESULTS: list[tuple[str, str, str]] = []   # (状态, 名称, 说明)


def record(status: str, name: str, detail: str = "") -> None:
    RESULTS.append((status, name, detail))
    mark = {"PASS": "✓", "FAIL": "✗", "GAP": "!"}[status]
    print(f"  {mark} [{status}] {name}" + (f"  —— {detail}" if detail else ""))


def check(cond: bool, name: str, detail: str = "") -> None:
    record("PASS" if cond else "FAIL", name, detail)


def gap(name: str, detail: str = "") -> None:
    record("GAP", name, detail)


class Case:
    """把一条预期失败的语句包在 SAVEPOINT 里，失败后回滚到保存点，不污染后续用例。"""

    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()

    def expect_error(self, sql: str, params=None, code: str | None = None):
        """执行并期望报错。返回 (是否报错, 错误码, 错误摘要)。"""
        self.cur.execute("SAVEPOINT c")
        try:
            self.cur.execute(sql, params)
            self.cur.execute("RELEASE SAVEPOINT c")
            return False, None, "未报错（规则未生效）"
        except Exception as exc:  # noqa: BLE001
            self.cur.execute("ROLLBACK TO SAVEPOINT c")
            sqlstate = getattr(exc, "pgcode", None)
            msg = str(exc).strip().splitlines()[0][:110]
            return True, sqlstate, msg


def section(title: str) -> None:
    print(f"\n[{title}]")


def cleanup(cur) -> None:
    """删除本脚本产生的测试数据。

    【为什么需要临时停用触发器】
    audit_record 与 system_audit_log 上有「禁止 DELETE」的触发器，且**不区分角色**，
    连超级用户也会被拦（这正是它该有的样子）。但测试收尾属于维护动作，
    只能用 session_replication_role = replica 在本会话内临时停用触发器。
    这也从侧面证明了那条规则是无条件生效的，而不是靠权限绕过的。
    """
    cur.execute("SET LOCAL session_replication_role = replica")
    cur.execute("""DELETE FROM license_record
                   WHERE asset_id IN (SELECT asset_id FROM asset WHERE title LIKE 'TEST-%')""")
    cur.execute("""DELETE FROM audit_record
                   WHERE asset_id IN (SELECT asset_id FROM asset WHERE title LIKE 'TEST-%')""")
    cur.execute("DELETE FROM asset WHERE title LIKE 'TEST-%'")
    cur.execute("RESET session_replication_role")


def main() -> int:
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    cur = conn.cursor()
    case = Case(conn)

    # ------------------------------------------------------------------ 准备
    cur.execute("SELECT user_id, username, role FROM app_user ORDER BY user_id")
    users = cur.fetchall()
    heritor = next((u for u in users if u[2] == "heritor"), None)
    expert = next((u for u in users if u[2] == "expert"), None)
    if not heritor or not expert:
        print("链路失败：app_user 缺少 heritor/expert 账号，无法构造用例", file=sys.stderr)
        return 2
    hid, hname, _ = heritor
    eid, ename, _ = expert

    # 清理上一次可能残留的测试数据
    cleanup(cur)
    conn.commit()

    # ------------------------------------------------------------------ 1 结构
    section("1 结构落地")
    cur.execute("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname='public' AND c.relkind='r'""")
    n_tab = cur.fetchone()[0]
    check(n_tab == 8, "8 张业务表存在", f"实际 {n_tab}")

    cur.execute("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname='public' AND c.relkind='v'""")
    n_view = cur.fetchone()[0]
    check(n_view == 3, "3 个视图存在", f"实际 {n_view}")

    cur.execute("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal")
    n_trg = cur.fetchone()[0]
    check(n_trg == 9, "9 个触发器存在", f"实际 {n_trg}")

    cur.execute("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'ich\\_%'")
    n_role = cur.fetchone()[0]
    check(n_role == 3, "3 个业务角色存在", f"实际 {n_role}")

    # ------------------------------------------------------------------ 2 枚举
    section("2 枚举取值域")
    cur.execute("SELECT 1 FROM public_motif LIMIT 1")
    mid = cur.fetchone()
    mid = mid and 1 or None
    cur.execute("SELECT motif_id FROM public_motif ORDER BY 1 LIMIT 1")
    row = cur.fetchone()
    motif_id = row[0] if row else None

    def new_asset(right_level="derived", status="draft", title="TEST-A"):
        h = hashlib.sha256(title.encode()).hexdigest()
        cur.execute(
            """INSERT INTO asset (title, owner_id, motif_id, file_path, file_hash,
                                  file_size, mime_type, right_level, audit_status, version_no)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING asset_id""",
            (title, hid, motif_id, f"data/test/{title}.jpg", h, 1024,
             "image/jpeg", right_level, status, 1),
        )
        return cur.fetchone()[0]

    ok, code, msg = case.expect_error(
        """INSERT INTO asset (title, owner_id, file_path, file_hash, file_size, mime_type,
                              right_level, audit_status, version_no)
           VALUES ('TEST-bad-enum', %s, 'x', %s, 1, 'image/jpeg', '不是枚举值', 'draft', 1)""",
        (hid, "a" * 64),
    )
    check(ok and code == "22P02", "非法 right_level 被枚举拒绝", msg)

    # ------------------------------------------------------------------ 3 CHECK
    section("3 CHECK 约束")
    ok, code, msg = case.expect_error(
        """INSERT INTO asset (title, owner_id, file_path, file_hash, file_size, mime_type,
                              right_level, audit_status, version_no)
           VALUES ('TEST-bad-hash', %s, 'x', 'abc', 1, 'image/jpeg', 'derived', 'draft', 1)""",
        (hid,),
    )
    check(ok and code == "23514", "file_hash 非 64 位十六进制被拒", msg)

    ok, code, msg = case.expect_error(
        """INSERT INTO asset (title, owner_id, file_path, file_hash, file_size, mime_type,
                              right_level, audit_status, version_no, ai_confidence)
           VALUES ('TEST-bad-conf', %s, 'x', %s, 1, 'image/jpeg', 'derived', 'draft', 1, 1.5)""",
        (hid, "b" * 64),
    )
    check(ok and code == "23514", "置信度 > 1 被拒", msg)

    # ------------------------------------------------------------------ 4 事务
    section("4 事务与持久性")
    cur.execute("SAVEPOINT rb")
    cur.execute(
        """INSERT INTO asset (title, owner_id, file_path, file_hash, file_size, mime_type,
                              right_level, audit_status, version_no)
           VALUES ('TEST-rollback', %s, 'x', %s, 1, 'image/jpeg', 'derived', 'draft', 1)""",
        (hid, hashlib.sha256(b"rb").hexdigest()),
    )
    cur.execute("ROLLBACK TO SAVEPOINT rb")
    cur.execute("SELECT count(*) FROM asset WHERE title='TEST-rollback'")
    left = cur.fetchone()[0]
    check(left == 0, "ROLLBACK 后无残留", f"残留 {left} 行")

    # ------------------------------------------------------------------ 5 触发器
    section("5 触发器与不可篡改")
    asset_id = new_asset(title="TEST-guard")
    cur.execute(
        """INSERT INTO audit_record (asset_id, expert_id, state, expert_level,
                                     opinion, is_override, decided_at)
           VALUES (%s, %s, 'approved', 'derived', '测试终审', false, now())
           RETURNING audit_id""",
        (asset_id, eid),
    )
    audit_id = cur.fetchone()[0]
    cur.execute("SAVEPOINT g1")

    ok, code, msg = case.expect_error(
        "UPDATE audit_record SET opinion='改一下' WHERE audit_id=%s", (audit_id,))
    check(ok, "已终审记录不可再修改", msg)

    ok, code, msg = case.expect_error(
        "UPDATE system_audit_log SET actor='伪造' WHERE log_id=(SELECT min(log_id) FROM system_audit_log)")
    check(ok, "系统审计日志禁止 UPDATE", msg)

    ok, code, msg = case.expect_error(
        "DELETE FROM system_audit_log WHERE log_id=(SELECT min(log_id) FROM system_audit_log)")
    check(ok, "系统审计日志禁止 DELETE", msg)

    ok, code, msg = case.expect_error(
        "DELETE FROM audit_record WHERE audit_id=%s", (audit_id,))
    check(ok, "审核记录禁止 DELETE（历史不可删）", msg)

    # UPDATE asset 的审计留痕路径。
    # 这条断言是补的：该路径曾因 fn_asset_audit_trigger 里用 TEXT[] || '字面量'
    # 拼变更列名（被 PG 当成数组拼接）而整体崩溃，任何 UPDATE asset 都失败 ——
    # 包括专家终审。而当时的 26 项验收全绿，因为只测了"不能改"，没测"能改"。
    # 所以这里同时断言两件事：更新能执行、变更列被正确记录。
    # 注意 right_level 用 original 而非 derived —— new_asset 的默认值就是 derived，
    # 改成一个不同的值才能验证"变更列被识别"，否则是空更新、只记 audit_status。
    cur.execute("SAVEPOINT g2")
    cur.execute(
        "UPDATE asset SET audit_status='approved', right_level='original' WHERE asset_id=%s",
        (asset_id,))
    cur.execute(
        """SELECT changed_fields FROM system_audit_log
           WHERE table_name='asset' AND record_id=%s AND operation='UPDATE'
           ORDER BY log_id DESC LIMIT 1""", (asset_id,))
    row = cur.fetchone()
    fields = sorted(row[0]) if row else []
    check(fields == ["audit_status", "right_level"],
          "UPDATE asset 可执行且审计日志记录变更列", f"changed_fields={fields}")
    cur.execute("ROLLBACK TO SAVEPOINT g2")

    # 审计日志的 actor 必须是业务角色，不是建库账号。
    # 触发器现在声明了 SECURITY DEFINER（否则业务角色无权写 system_audit_log，
    # 终审会直接失败），而 DEFINER 会让函数内的 current_user 变成函数属主 ——
    # 已用 role GUC 兜住。这条断言防止以后有人把它"简化"回 current_user，
    # 把"谁改的"悄悄丢掉：那种退化不会报错，只会让审计记录全是 postgres。
    #
    # 这里改的是 motif_id 而不是 right_level：自从 trg_asset_status_guard 上线，
    # 专家"不声明终审标记就改 right_level"会被拒绝（那正是我们要的），
    # 而这条用例要验的是"actor 记的是谁"，不该顺手验另一条规则。
    # motif_id 变更同样进 changed_fields，是审计触发器会留痕的合法字段。
    cur.execute("SAVEPOINT a2")
    cur.execute("SET LOCAL ROLE ich_expert")
    cur.execute("UPDATE asset SET motif_id = NULL WHERE asset_id=%s", (asset_id,))
    cur.execute("RESET ROLE")
    cur.execute(
        """SELECT actor FROM system_audit_log
           WHERE table_name='asset' AND record_id=%s AND operation='UPDATE'
           ORDER BY log_id DESC LIMIT 1""", (asset_id,))
    row = cur.fetchone()
    actor = row[0] if row else None
    check(actor == "ich_expert", "审计日志记录业务角色而非函数属主", f"actor={actor}")
    cur.execute("ROLLBACK TO SAVEPOINT a2")

    # ------------------------------------------------------------------ 6 授权约束
    section("6 授权约束")
    ok, code, msg = case.expect_error(
        """INSERT INTO license_record (asset_id, licensor_id, licensee_user_id, license_type,
                                       start_date, fee_cny, status)
           VALUES (%s, %s, %s, 'exclusive', CURRENT_DATE, 100, 'active')""",
        (asset_id, hid, hid),
    )
    check(ok and code == "23514", "不能自己授权给自己（ck_lic_self）", msg)

    ok, code, msg = case.expect_error(
        """INSERT INTO license_record (asset_id, licensor_id, licensee_name, license_type,
                                       start_date, fee_cny, status)
           VALUES (%s, %s, '某公司', '不是授权类型', CURRENT_DATE, 100, 'active')""",
        (asset_id, hid),
    )
    check(ok and code == "23514", "非法 license_type 被拒", msg)

    ok, code, msg = case.expect_error(
        """INSERT INTO license_record (asset_id, licensor_id, licensee_name, license_type,
                                       start_date, end_date, fee_cny, status)
           VALUES (%s, %s, '某公司', 'display', CURRENT_DATE, CURRENT_DATE - 10, 100, 'active')""",
        (asset_id, hid),
    )
    check(ok and code == "23514", "结束日期早于开始日期被拒", msg)

    # ------------------------------------------------------------------ 7 列级权限
    section("7 权限隔离")
    cur.execute("SELECT count(*) FROM information_schema.column_privileges "
                "WHERE table_schema='public' AND grantee LIKE 'ich\\_%'")
    n_colpriv = cur.fetchone()[0]
    check(n_colpriv > 0, "存在列级授权记录", f"{n_colpriv} 条")

    # 授权登记链路的权限必须是"够用且仅够用"：
    # 曾经 license_record 三个角色都只有 SELECT，没有任何角色能 INSERT ——
    # 「登记授权」这个核心功能在真库上必然 42501，而演示模式与 e2e 全在内存里跑，
    # 一路绿灯。下面这组断言直接查 information_schema，比跑一次业务更能防回归。
    cur.execute("SELECT has_table_privilege('ich_heritor','license_record','INSERT'),"
                "       has_table_privilege('ich_admin','license_record','INSERT'),"
                "       has_table_privilege('ich_heritor','license_record','UPDATE')")
    can_heritor_ins, can_admin_ins, can_update = cur.fetchone()
    check(can_heritor_ins, "传承人可登记授权（license_record INSERT）", "无 INSERT 权限")
    check(can_admin_ins, "监管可代登记授权（license_record INSERT）", "无 INSERT 权限")
    check(not can_update, "授权记录不可 UPDATE（撤销靠新增 revoked 记录）",
          "竟然有 UPDATE 权限，追加式设计被破坏")

    # 审计触发器必须是 SECURITY DEFINER，否则业务角色写不了 system_audit_log
    cur.execute("""SELECT p.prosecdef FROM pg_proc p
                   JOIN pg_namespace n ON n.oid = p.pronamespace
                   WHERE n.nspname='public' AND p.proname='fn_asset_audit_trigger'""")
    row = cur.fetchone()
    check(bool(row and row[0]), "审计触发器为 SECURITY DEFINER（业务角色无权直写审计表）",
          "仍为 INVOKER，终审会在触发器内部 permission denied")

    # 切换到传承人角色，尝试改自己的权属。
    # 注意顺序：语句一失败整个事务就进入 aborted 状态，此时连 RESET ROLE 都会被拒，
    # 必须先 ROLLBACK TO SAVEPOINT 把事务救回来。SET LOCAL 是事务级的，
    # 回滚到保存点会自动恢复原来的角色，不需要额外 RESET。
    case.cur.execute("SAVEPOINT r1")
    blocked, detail = False, ""
    try:
        case.cur.execute("SET LOCAL ROLE ich_heritor")
        try:
            case.cur.execute(
                "UPDATE asset SET right_level='original' WHERE asset_id=%s", (asset_id,))
            detail = "UPDATE 竟然成功，权限未生效"
        except errors.InsufficientPrivilege as exc:
            blocked = True
            detail = str(exc).strip().splitlines()[0][:100]
    finally:
        case.cur.execute("ROLLBACK TO SAVEPOINT r1")
    check(blocked, "传承人无法 UPDATE asset（更无法改权属）", detail)

    # ------------------------------------------------------------------ 8 并发
    section("8 并发控制")
    conn.commit()
    cc = conn.cursor()
    cc.execute("SELECT count(*) FROM asset WHERE asset_id=%s", (asset_id,))

    conn2 = psycopg2.connect(DSN)
    conn2.autocommit = False
    cur2 = conn2.cursor()
    try:
        cc.execute("SELECT title FROM asset WHERE asset_id=%s FOR UPDATE", (asset_id,))
        locked = True
        detail = ""
        try:
            cur2.execute("SELECT title FROM asset WHERE asset_id=%s FOR UPDATE NOWAIT", (asset_id,))
            locked = False
        except errors.LockNotAvailable:
            detail = "第二个会话拿到 55P03（锁不可用），说明行锁真的生效"
        conn2.rollback()
        check(locked, "FOR UPDATE 行锁互斥（第二个会话被阻塞）", detail)
    finally:
        conn.rollback()
        conn2.close()

    # ------------------------------------------------------------------ 9 向量层
    section("9 降级向量层语义")
    cur.execute("SELECT ich_vector_is_native()")
    native = cur.fetchone()[0]
    print(f"    当前模式：{'pgvector 原生' if native else '降级（NUMERIC[] + 精确扫描）'}")

    cur.execute("SELECT ich_cosine_distance(ARRAY[1,0]::numeric[], ARRAY[1,0]::numeric[])")
    d_same = float(cur.fetchone()[0])
    cur.execute("SELECT ich_cosine_distance(ARRAY[1,0]::numeric[], ARRAY[-1,0]::numeric[])")
    d_opp = float(cur.fetchone()[0])
    cur.execute("SELECT ich_cosine_distance(ARRAY[1,0]::numeric[], ARRAY[0,1]::numeric[])")
    d_orth = float(cur.fetchone()[0])
    check(abs(d_same - 0) < 1e-9, "同向 cosine 距离 = 0", f"{d_same}")
    check(abs(d_opp - 2) < 1e-9, "反向 cosine 距离 = 2", f"{d_opp}")
    check(abs(d_orth - 1) < 1e-9, "正交 cosine 距离 = 1", f"{d_orth}")

    cur.execute("SELECT l2_normalize(ARRAY[3,4]::numeric[])")
    vec = cur.fetchone()[0]
    check(abs(float(vec[0]) - 0.6) < 1e-6 and abs(float(vec[1]) - 0.8) < 1e-6,
          "l2_normalize([3,4]) = [0.6, 0.8]", f"{vec}")

    ok, code, msg = case.expect_error(
        "SELECT ich_cosine_distance(ARRAY[1,0]::numeric[], ARRAY[1,0,0]::numeric[])")
    check(ok, "维度不一致时拒绝计算", msg)

    # ------------------------------------------------------------------ 10 HNSW
    section("10 向量索引")
    cur.execute("""SELECT indexname, indexdef FROM pg_indexes
                   WHERE schemaname='public' AND tablename='public_motif'""")
    idx = cur.fetchall()
    if native:
        has_hnsw = any("hnsw" in d.lower() for _, d in idx)
        check(has_hnsw, "HNSW 索引已建立", "; ".join(n for n, _ in idx))
    else:
        gap("HNSW 索引未实测",
            "当前为降级模式，无 pgvector；检索走精确扫描，性能结论不可得")

    # ------------------------------------------------------------------ 11 业务规则
    section("11 业务规则在数据库层的落实情况")

    # 11.1 负向：asset 此刻是 draft 且没有任何 approved 审核 —— 数据库层必须拒绝
    ok, code, msg = case.expect_error(
        """INSERT INTO license_record (asset_id, licensor_id, licensee_name, license_type,
                                       start_date, fee_cny, status)
           VALUES (%s, %s, '越权公司', 'display', CURRENT_DATE, 500, 'active')""",
        (asset_id, hid),
    )
    check(ok and code == "23514",
          "未终审即授权被数据库拒绝（trg_license_require_approved）",
          f"err={code} {msg}")

    # 11.2 rejected 同样不放行 —— 「终审了」不等于「可以授权」
    case.cur.execute("SAVEPOINT r")
    case.cur.execute("UPDATE asset SET audit_status = 'rejected' WHERE asset_id = %s", (asset_id,))
    ok2, code2, msg2 = case.expect_error(
        """INSERT INTO license_record (asset_id, licensor_id, licensee_name, license_type,
                                       start_date, fee_cny, status)
           VALUES (%s, %s, '越权公司', 'display', CURRENT_DATE, 500, 'active')""",
        (asset_id, hid),
    )
    case.cur.execute("ROLLBACK TO SAVEPOINT r")
    check(ok2 and code2 == "23514", "已驳回的资产同样不得授权", f"err={code2} {msg2}")

    # 11.3 正向对照：改成 approved 后必须放行。
    #      没有这一条，前面的「拒绝」可能只是触发器一律拒绝造成的假阳性。
    case.cur.execute("SAVEPOINT p")
    case.cur.execute(
        "UPDATE asset SET audit_status = 'approved', right_level = 'original' WHERE asset_id = %s",
        (asset_id,),
    )
    try:
        case.cur.execute(
            """INSERT INTO license_record (asset_id, licensor_id, licensee_name, license_type,
                                           start_date, fee_cny, status)
               VALUES (%s, %s, '合规公司', 'display', CURRENT_DATE, 500, 'active')
               RETURNING license_id""",
            (asset_id, hid),
        )
        new_lid = case.cur.fetchone()[0]
        check(True, "已终审资产可以正常登记授权（未误伤合法路径）", f"license_id={new_lid}")
    except Exception as exc:  # noqa: BLE001
        check(False, "已终审资产可以正常登记授权（未误伤合法路径）",
              f"{type(exc).__name__}: {str(exc).splitlines()[0][:80]}")
    case.cur.execute("ROLLBACK TO SAVEPOINT p")

    # ------------------------------------------------------------------ 12 视图
    section("12 视图可用性")
    cur.execute("SELECT count(*) FROM v_asset_ledger")
    n_ledger = cur.fetchone()[0]
    check(n_ledger >= 1, "v_asset_ledger 可查询且有数据", f"{n_ledger} 行")
    cur.execute("SELECT count(*) FROM v_asset_trace_chain")
    check(True, "v_asset_trace_chain 可查询", f"{cur.fetchone()[0]} 行")
    cur.execute("SELECT count(*) FROM v_asset_pending_audit")
    check(True, "v_asset_pending_audit 可查询", f"{cur.fetchone()[0]} 行")

    # ------------------------------------------------------------------ 13 角色闸门
    section("13 审核状态流转的角色闸门（trg_asset_status_guard）")

    # 先看结构：闸门必须同时挂在 INSERT 与 UPDATE 上。
    # pg_trigger.tgtype 是位掩码：4=INSERT 16=UPDATE 8=DELETE 32=TRUNCATE。
    cur.execute(
        """SELECT (tgtype & 4) <> 0, (tgtype & 16) <> 0, p.proname, p.prosecdef
           FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
           WHERE t.tgname = 'trg_asset_status_guard' AND t.tgrelid = 'asset'::regclass"""
    )
    row = cur.fetchone()
    check(bool(row and row[0] and row[1]),
          "闸门触发器同时覆盖 INSERT 与 UPDATE", f"{row}")

    def run_as(role: str, sql: str, params=None, gate: str | None = None):
        """以业务角色执行一条语句，返回 (是否成功, SQLSTATE, 首行消息)。

        角色与终审标记都用 SET LOCAL / set_config(..., true) 施加，作用域是本事务；
        无论成败都回滚到保存点，状态与角色一并还原 —— 否则一条失败语句会把整个
        事务打成 aborted，后面所有用例连带失败。
        """
        case.cur.execute("SAVEPOINT gate")
        try:
            case.cur.execute(f"SET LOCAL ROLE {role}")
            if gate:
                case.cur.execute(
                    "SELECT set_config('ich.finalize_gate', %s, true)", (gate,))
            case.cur.execute(sql, params)
            case.cur.execute("ROLLBACK TO SAVEPOINT gate")
            return True, None, ""
        except Exception as exc:  # noqa: BLE001
            case.cur.execute("ROLLBACK TO SAVEPOINT gate")
            return (False, getattr(exc, "pgcode", None),
                    str(exc).strip().splitlines()[0][:110])

    # 13.1 传承人直接把状态推到 approved —— 这就是那条"绕过应用自己定级"的路。
    #      注意这条语句**不含** right_level：含了会先被列级 GRANT 挡住，
    #      测出来的就是权限而不是规则。去掉 right_level 才是真实攻击面。
    ok, code, msg = run_as(
        "ich_heritor",
        "UPDATE asset SET audit_status='approved' WHERE asset_id=%s", (asset_id,))
    check(ok is False and code == "42501" and "传承人" in msg,
          "传承人直连库改 approved 被触发器拒绝", f"err={code} {msg}")

    # 13.2 连 INSERT 都不能自带结论：一条 approved + original 的新行不许落库
    ok, code, msg = run_as(
        "ich_heritor",
        """INSERT INTO asset (title, owner_id, file_path, file_hash, file_size, mime_type,
                              right_level, audit_status, version_no)
           VALUES ('TEST-自封原创', %s, 'x', %s, 1, 'image/jpeg', 'original', 'approved', 1)""",
        (hid, hashlib.sha256(b"self-approve").hexdigest()))
    check(ok is False and code == "42501" and "draft" in msg,
          "传承人上传即自封 approved 被拒绝", f"err={code} {msg}")

    # 13.3 正向对照：传承人提交待审必须放行。
    #      没有这一条，上面的"拒绝"可能只是闸门一律拒绝造成的假阳性。
    ok, code, msg = run_as(
        "ich_heritor",
        "UPDATE asset SET audit_status='pending' WHERE asset_id=%s", (asset_id,))
    check(ok, "传承人 draft → pending 正常放行（未误伤合法路径）", msg)

    # 13.4 专家直改终态：没声明终审标记就必须失败。
    #      这条守的是「终审只有一条路」。它和 13.1 的区别：13.1 靠角色，
    #      这里角色是对的（专家），失败的只是"没走那条路"。
    ok, code, msg = run_as(
        "ich_expert",
        "UPDATE asset SET audit_status='approved', right_level='original' WHERE asset_id=%s",
        (asset_id,))
    check(ok is False and code == "42501" and "approve_asset" in msg,
          "专家直改终态但未声明终审标记被拒绝", f"err={code} {msg}")

    ok, code, msg = run_as(
        "ich_expert",
        "UPDATE asset SET right_level='original' WHERE asset_id=%s", (asset_id,))
    check(ok is False and code == "42501",
          "专家脱离终审事务单独改权级被拒绝", f"err={code} {msg}")

    # 13.5 正向对照：带着标记走同一条 UPDATE 必须成功 —— 这就是 approve_asset 的写法
    ok, code, msg = run_as(
        "ich_expert",
        "UPDATE asset SET audit_status='approved', right_level='original' WHERE asset_id=%s",
        (asset_id,), gate="approve_asset")
    check(ok, "声明终审标记后专家可写终态（approve_asset 路径可用）", msg)

    # 13.6 已终审不可回退：专家带着标记也改不回去
    cur.execute("UPDATE asset SET audit_status='approved', right_level='original' WHERE asset_id=%s",
                (asset_id,))
    ok, code, msg = run_as(
        "ich_expert",
        "UPDATE asset SET audit_status='pending' WHERE asset_id=%s",
        (asset_id,), gate="approve_asset")
    check(ok is False and code == "42501" and "已终审" in msg,
          "专家不得把已终审改回待审（即便带标记）", f"err={code} {msg}")

    # 13.7 监管只读。今天这一层先被表级 GRANT 挡下（ich_admin 对 asset 无 UPDATE），
    #      触发器里的 admin 分支是第二道 —— 所以这里断言"被拒"，并说明拒在哪一层，
    #      不假装是触发器在起作用。
    ok, code, msg = run_as(
        "ich_admin",
        "UPDATE asset SET audit_status='draft' WHERE asset_id=%s", (asset_id,))
    check(ok is False and code == "42501",
          "监管角色不能改审核状态（表级 GRANT 先挡，闸门是第二道）", f"err={code} {msg}")

    # 13.8 审计触发器与闸门必须能共存：一次合法的终审 UPDATE 仍要留下审计日志。
    #      这条防的是"新触发器把老链路顶坏"这类回归 —— 顺序、异常码都可能出问题。
    #
    #      必须新造一件资产：13.6 已经把它改成 approved + original，
    #      再写一次同样的值是"空更新"，审计触发器按设计不留痕（它只记真实变更），
    #      于是读回来的会是上一条 postgres 写的日志，断言就测错了对象。
    aid2 = new_asset(title="TEST-gate-log", status="pending")
    cur.execute("SAVEPOINT co")
    case.cur.execute("SET LOCAL ROLE ich_expert")
    case.cur.execute("SELECT set_config('ich.finalize_gate', 'approve_asset', true)")
    case.cur.execute(
        "UPDATE asset SET audit_status='approved', right_level='original' WHERE asset_id=%s",
        (aid2,))
    case.cur.execute(
        """SELECT changed_fields, actor FROM system_audit_log
           WHERE table_name='asset' AND record_id=%s AND operation='UPDATE'
           ORDER BY log_id DESC LIMIT 1""", (aid2,))
    row = case.cur.fetchone()
    fields = sorted(row[0]) if row else []
    check(fields == ["audit_status", "right_level"] and row and row[1] == "ich_expert",
          "闸门放行时审计触发器照常留痕（两条触发器共存）",
          f"changed_fields={fields} actor={row[1] if row else None}")
    cur.execute("ROLLBACK TO SAVEPOINT co")

    # ------------------------------------------------------------------ 收尾
    conn.rollback()
    cur = conn.cursor()
    cleanup(cur)
    conn.commit()
    conn.close()

    n_pass = sum(1 for s, _, _ in RESULTS if s == "PASS")
    n_fail = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    n_gap = sum(1 for s, _, _ in RESULTS if s == "GAP")
    print(f"\n结果：{n_pass} 项通过 / {n_fail} 项失败 / {n_gap} 项缺口（共 {len(RESULTS)} 项）")
    if n_gap:
        print("\n缺口明细（这些不是通过，是如实记录的未落实项）：")
        for s, name, detail in RESULTS:
            if s == "GAP":
                print(f"  ! {name}：{detail}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
