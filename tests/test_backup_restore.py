"""备份恢复演练：把"有备份策略"变成"真的恢复过一次"。

为什么单独一套
--------------
`pg_dump` 能跑通，不等于备份可用。真正会翻车的是这些地方：

  - 触发器、视图、列级 GRANT 在恢复后丢失（dump 不带就真没了）；
  - 序列值没跟上，恢复后第一条 INSERT 就撞主键；
  - 数据行数对得上，但内容被截断/编码坏掉；
  - 库能连上，但 HNSW 索引坏了、终审事务写不进去。

所以本套件不是"跑一次 pg_dump 就算过"，而是：
**dump → 恢复到临时库 → 逐对象比对 → 在恢复库上真跑一遍业务事务**。

做法
----
1. `pg_dump -Fc` 导出**目标库**（默认 ich_heritage）。
2. `CREATE DATABASE ... TEMPLATE template0` 建一个空库，`pg_restore` 灌进去。
3. 结构/权限/数据三类逐项比对，全部以**系统目录**为准，不解析 dump 文本。
4. 在恢复库上做功能烟测：
     - HNSW 近邻检索能返回正确结果；
     - 递归视图 v_asset_lineage 可执行；
     - `approve_asset()` 正常提交：asset / audit_record / system_audit_log 三表同变；
     - 注入应用层异常：三表**全部**回滚，一条都不许留；
     - 绕过应用直连改状态仍被闸门拒绝（42501）。
5. DROP 临时库。

关于事务回滚的故障注入
----------------------
真实事务的回滚能力，只有"在事务中间抛异常"才能证明。本套件在驱动层拦截
终审流程最后一条回读语句并抛 RuntimeError —— 此时两条 UPDATE 已经发到服务端、
触发器也已写过审计日志，若 ROLLBACK 不彻底，三张表一定对不上。
这比"手动 BEGIN; ROLLBACK;"更接近真实事故（应用层异常导致的事务中断）。

运行
----
    python -m tests.test_backup_restore
    PG_DSN="host=127.0.0.1 port=55433 user=postgres dbname=ich_heritage" \
        python -m tests.test_backup_restore

    --keep      保留临时库与 dump 文件（排查用）
    --out DIR   证据输出目录（默认 docs/verify）

前置：pg_dump / pg_restore 可用（PG_BIN 或 PATH），且目标库当前无其他连接。
"""

from __future__ import annotations

import contextlib
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extensions

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "verify"

PASS = 0
FAIL = 0
FAILED: list[str] = []
RECORDS: list[dict] = []       # 证据条目，最后写 json


# =============================================================================
#  输出
# =============================================================================
def section(title: str) -> None:
    print(f"\n[{title}]")


def check(cond: bool, name: str, detail: str = "", evidence=None) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ [PASS] {name}" + (f"  —— {detail}" if detail else ""))
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  ✗ [FAIL] {name}" + (f"  —— {detail}" if detail else ""))
    RECORDS.append({"ok": bool(cond), "name": name, "detail": detail,
                    "evidence": evidence})


def note(text: str) -> None:
    print(f"      {text}")


# =============================================================================
#  连接与工具定位
# =============================================================================
def conninfo() -> dict:
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


def find_tool(name: str) -> str | None:
    """按 PG_BIN → PATH → 项目自带 → 常见安装目录 的顺序找一个客户端工具。"""
    exe = f"{name}.exe" if os.name == "nt" else name
    cands: list[Path] = []
    if os.environ.get("PG_BIN"):
        cands.append(Path(os.environ["PG_BIN"]) / exe)
    w = shutil.which(name)
    if w:
        cands.append(Path(w))
    cands.append(ROOT / "_env" / "pgsql" / "bin" / exe)
    for pat in ("C:/Users/*/pgvec-runtime/env/Library/bin",
                "C:/Program Files/PostgreSQL/*/bin"):
        for hit in sorted(glob.glob(pat)):
            cands.append(Path(hit) / exe)
    for c in cands:
        try:
            if c and c.exists():
                return str(c)
        except OSError:
            continue
    return None


def subprocess_env(base: dict) -> dict:
    env = dict(os.environ)
    env["PGPASSWORD"] = base.get("password") or ""
    env["PGCLIENTENCODING"] = "UTF8"
    env["LC_MESSAGES"] = "C"          # 保证 stderr 里的 "ERROR" 是英文，便于判定
    return env


def conn_args(base: dict, dbname: str | None = None) -> list[str]:
    return [
        "-h", str(base["host"]),
        "-p", str(base["port"]),
        "-U", str(base["user"]),
        "-d", dbname or str(base["dbname"]),
    ]


def connect(base: dict, dbname: str | None = None, autocommit: bool = True):
    d = dict(base)
    if dbname:
        d["dbname"] = dbname
    c = psycopg2.connect(**d)
    c.autocommit = autocommit
    return c


# =============================================================================
#  指纹：把系统目录里的对象定义抓成可比对的集合
# =============================================================================
def _norm_check(defn: str) -> str:
    """把 CHECK 定义归一化到可比较的形式。

    只做三件事，都是"语法噪声"而非语义：
      1. 去掉显式类型转换（pg_dump 会给 CHECK 里的枚举比较补 ::text / ::xxx_enum）；
      2. 去掉括号与空白；
      3. 统一小写。
    不裁剪比较对象本身，所以真的改了判据仍然会被发现。
    """
    s = defn.lower()
    s = re.sub(r"::(?:text|character varying|\w+_enum|bpchar|varchar)(?:\[\])?", "", s)
    s = re.sub(r"[\s()]", "", s)
    return s


def catalog_snapshot(conn) -> dict:
    """抓一份"这个库长什么样"的快照，用于源库与恢复库逐项比对。"""
    snap: dict = {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname, c.relkind
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r','v','m','p','S')
            ORDER BY 1
            """
        )
        snap["relations"] = [{"name": r[0], "kind": r[1]} for r in cur.fetchall()]

        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' ORDER BY 1"
        )
        snap["tables"] = [r[0] for r in cur.fetchall()]

        # 行数：逐表精确 count（真实库规模小，不用估算值）
        counts = {}
        for t in snap["tables"]:
            cur.execute(f'SELECT count(*) FROM public."{t}"')
            counts[t] = cur.fetchone()[0]
        snap["row_counts"] = counts

        # 索引 / 约束 / 触发器 / 视图 / 函数：用系统目录生成的规范定义
        cur.execute(
            """
            SELECT c.relname, pg_get_indexdef(i.indexrelid)
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname='public' ORDER BY 1
            """
        )
        snap["indexes"] = {r[0]: r[1] for r in cur.fetchall()}

        # 约束：先存原始 def（证据），再存归一化 def（判定用）。
        # pg_dump 会给 CHECK 里的枚举比较补上显式 ::text 转换，
        # 导致 deparse 文本变化但语义不变 —— 不能直接拿文本判失败。
        cur.execute(
            """
            SELECT c.conname, c.contype, c.conrelid::regclass::text, c.convalidated,
                   CASE WHEN c.contype='c' THEN pg_get_constraintdef(c.oid) ELSE '' END
            FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
            WHERE n.nspname='public' ORDER BY 1
            """
        )
        cons, cons_raw = {}, {}
        for name, ctype, tbl, validated, defn in cur.fetchall():
            cons[name] = {
                "type": ctype, "table": tbl, "validated": bool(validated),
                "check": _norm_check(defn) if defn else "",
            }
            if defn:
                cons_raw[name] = " ".join(defn.split())
        snap["constraints"] = cons
        snap["constraints_raw"] = cons_raw

        cur.execute(
            """
            SELECT t.tgname, pg_get_triggerdef(t.oid)
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY 1
            """
        )
        snap["triggers"] = {r[0]: r[1] for r in cur.fetchall()}

        cur.execute(
            """
            SELECT c.relname, md5(pg_get_viewdef(c.oid, true))
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname='public' AND c.relkind='v' ORDER BY 1
            """
        )
        snap["views"] = {r[0]: r[1] for r in cur.fetchall()}

        cur.execute(
            """
            SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
                   md5(pg_get_functiondef(p.oid))
            FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname='public' AND p.prokind='f'
              -- 排除扩展成员：pgvector / pgcrypto 的函数装进了 public，
              -- 它们由 CREATE EXTENSION 重建，不该按项目函数逐个比对（聚合也没有 def）
              AND NOT EXISTS (
                  SELECT 1 FROM pg_depend d
                  WHERE d.objid = p.oid AND d.deptype = 'e'
              )
            ORDER BY 1
            """
        )
        snap["functions"] = {r[0]: r[1] for r in cur.fetchall()}

        cur.execute(
            """
            SELECT t.typname, string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder)
            FROM pg_type t
            JOIN pg_enum e ON e.enumtypid = t.oid
            JOIN pg_namespace n ON n.oid = t.typnamespace
            WHERE n.nspname='public' GROUP BY 1 ORDER BY 1
            """
        )
        snap["enums"] = {r[0]: r[1] for r in cur.fetchall()}

        # 序列当前值：恢复后不同步，第一条 INSERT 就会撞主键。
        # 直接读序列关系（pg_sequences 视图没有 is_called）。
        cur.execute(
            """
            SELECT c.relname FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname='public' AND c.relkind='S' ORDER BY 1
            """
        )
        seq_names = [r[0] for r in cur.fetchall()]
        seqs = {}
        for sn in seq_names:
            cur.execute(f'SELECT last_value, is_called FROM public."{sn}"')
            lv, called = cur.fetchone()
            seqs[sn] = [int(lv), bool(called)]
        snap["sequences"] = seqs

        # 表级授权
        cur.execute(
            """
            SELECT c.relname, coalesce(array_to_string(c.relacl, '|'), '')
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','v') ORDER BY 1
            """
        )
        snap["table_acl"] = {r[0]: r[1] for r in cur.fetchall()}

        # 列级授权：只取本项目三个业务角色，聚焦真正被设计过的那部分
        cur.execute(
            """
            SELECT table_name, column_name, grantee, privilege_type
            FROM information_schema.column_privileges
            WHERE table_schema='public'
              AND grantee IN ('ich_heritor','ich_expert','ich_admin')
            ORDER BY 1,2,3,4
            """
        )
        snap["column_grants"] = [
            f"{t}.{c}:{g}:{p}" for t, c, g, p in cur.fetchall()
        ]

        # 角色属性：确认三个业务角色在目标库里存在且不是超级用户
        cur.execute(
            """
            SELECT rolname, rolsuper, rolcanlogin
            FROM pg_roles WHERE rolname IN ('ich_heritor','ich_expert','ich_admin')
            ORDER BY 1
            """
        )
        snap["roles"] = {r[0]: [bool(r[1]), bool(r[2])] for r in cur.fetchall()}

        # 内容指纹：逐表 md5(所有行按文本排序拼接)，能发现"行数对但内容错"
        prints = {}
        for t in snap["tables"]:
            cur.execute(
                f'SELECT md5(coalesce(string_agg(x::text, \'|\' ORDER BY x::text), \'\')) '
                f'FROM public."{t}" x'
            )
            prints[t] = cur.fetchone()[0]
        snap["content_md5"] = prints
    return snap


# =============================================================================
#  事务回滚实证
# =============================================================================
def tx_state(conn, asset_id: int) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT right_level::text, audit_status::text, version_no, updated_at "
            "FROM asset WHERE asset_id=%s", (asset_id,))
        a = cur.fetchone()
        cur.execute(
            "SELECT audit_id, state::text, expert_id, decided_at, opinion "
            "FROM audit_record WHERE asset_id=%s ORDER BY audit_id", (asset_id,))
        audits = cur.fetchall()
        cur.execute(
            "SELECT count(*) FROM system_audit_log "
            "WHERE table_name='asset' AND record_id=%s", (asset_id,))
        logs = cur.fetchone()[0]
    return {
        "asset": [a[0], a[1], a[2], a[3].isoformat() if a and a[3] else None],
        "audits": [[r[0], r[1], r[2], r[3].isoformat() if r[3] else None, r[4]] for r in audits],
        "log_count": logs,
    }


def find_pending_asset(conn) -> tuple[int | None, int | None]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.asset_id FROM asset a
            JOIN audit_record r ON r.asset_id = a.asset_id AND r.state='pending'
            WHERE a.audit_status='pending'
            ORDER BY a.asset_id
            """
        )
        ids = [r[0] for r in cur.fetchall()]
    return (ids[0] if ids else None, ids[1] if len(ids) > 1 else None)


def run_tx_drill(base: dict, dbname: str) -> dict:
    """在恢复库上跑：正常提交 / 注入异常回滚 / 闸门拦截。"""
    out: dict = {}

    # 让 app.* 连到恢复库：config 在 import 时读环境变量，必须先设再导入
    for k, v in {
        "ICH_DB_HOST": str(base["host"]), "ICH_DB_PORT": str(base["port"]),
        "ICH_DB_NAME": dbname, "ICH_DB_USER": str(base["user"]),
        "ICH_DB_PASSWORD": base.get("password") or "",
        "ICH_DEMO_MODE": "0", "ICH_LOG_LEVEL": "ERROR",
    }.items():
        os.environ[k] = v
    sys.path.insert(0, str(ROOT))
    from app import db as appdb            # noqa: E402
    from app import services               # noqa: E402

    appdb.init_pool()
    conn = connect(base, dbname, autocommit=False)
    first, second = find_pending_asset(conn)

    with conn.cursor() as cur:
        cur.execute("SELECT user_id, display_name FROM app_user WHERE role='expert' ORDER BY user_id LIMIT 1")
        row = cur.fetchone()
    conn.rollback()
    expert_id, expert_name = (row[0], row[1]) if row else (1, "演练专家")

    try:
        # ---------- 正常提交 ----------
        if first:
            before = tx_state(conn, first)
            res = services.approve_asset(
                asset_id=first, final_level="derived", opinion="备份恢复演练：正常提交",
                expert_id=expert_id, expert_name=expert_name,
            )
            after = tx_state(conn, first)
            out["commit"] = {"asset_id": first, "before": before, "after": after, "result": res}
        else:
            out["commit"] = None

        # ---------- 注入异常 → 全回滚 ----------
        if second:
            before = tx_state(conn, second)
            orig_open_tx = services.open_tx
            marker = "SELECT right_level, audit_status, version_no FROM asset WHERE asset_id = %s"

            @contextlib.contextmanager
            def patched_open_tx(app_role=None):
                """包住真实事务，只在最后那条回读语句上抛异常。

                此时 audit_record 与 asset 的 UPDATE 都已发到服务端，
                trg_asset_right_change 也已写过 system_audit_log —— 正是
                "事务进行到一半应用层崩了"的真实场景。
                """
                with orig_open_tx(app_role=app_role) as real:
                    class BoomCursor:
                        def __getattr__(self, name):
                            return getattr(real, name)

                        def execute(self, sql, params=None):
                            if isinstance(sql, str) and marker in " ".join(sql.split()):
                                raise RuntimeError(
                                    "fault injection: 终审写库后应用层异常（模拟进程崩溃前抛错）"
                                )
                            return real.execute(sql, params)

                    yield BoomCursor()

            services.open_tx = patched_open_tx
            raised = None
            try:
                services.approve_asset(
                    asset_id=second, final_level="original",
                    opinion="备份恢复演练：这条不该落库",
                    expert_id=expert_id, expert_name=expert_name,
                )
            except Exception as exc:                       # noqa: BLE001
                raised = f"{type(exc).__name__}: {exc}"
            finally:
                services.open_tx = orig_open_tx

            after = tx_state(conn, second)
            out["rollback"] = {"asset_id": second, "before": before,
                               "after": after, "raised": raised}
        else:
            out["rollback"] = None

        # ---------- 绕过应用直连改状态 ----------
        # 必须挑一条**还在 pending** 的资产：对已 approved 的行再写一次 approved
        # 是幂等更新，状态机不会拦，测不到闸门。second 在回滚场景后仍是 pending。
        target = second or first
        if target:
            with conn.cursor() as cur:
                cur.execute("SELECT right_level::text, audit_status::text FROM asset WHERE asset_id=%s", (target,))
                before = cur.fetchone()
                err = None
                try:
                    cur.execute("SET LOCAL ROLE ich_expert")
                    cur.execute("UPDATE asset SET audit_status='approved' WHERE asset_id=%s", (target,))
                except psycopg2.Error as exc:
                    err = {"pgcode": exc.pgcode, "msg": (exc.pgerror or "").strip().splitlines()[0]}
                finally:
                    conn.rollback()
            out["gate"] = {"asset_id": target, "before": before, "error": err}
        else:
            out["gate"] = None
    finally:
        conn.close()
        appdb.close_pool()
    return out


def hnsw_smoke(conn) -> dict:
    """在恢复库插入 3 条临时向量，验证 HNSW 近邻检索返回预期结果，然后回滚。"""
    import math
    out: dict = {}
    with conn.cursor() as cur:
        cur.execute("SELECT asset_id FROM asset ORDER BY asset_id LIMIT 3")
        ids = [r[0] for r in cur.fetchall()]
        if len(ids) < 3:
            return {"skipped": "资产不足 3 条"}
        base_vecs = []
        for k in range(3):
            v = [0.0] * 512
            v[k * 7] = 1.0
            v[(k * 7 + 1) % 512] = 0.2
            n = math.sqrt(sum(x * x for x in v))
            base_vecs.append([x / n for x in v])
        for aid, v in zip(ids, base_vecs):
            cur.execute(
                "INSERT INTO feature_vector (asset_id, model_name, model_version, embed_dim, embedding) "
                "VALUES (%s,'restore-smoke','v1',512,%s::vector)",
                (aid, "[" + ",".join(f"{x:.6f}" for x in v) + "]"),
            )
        cur.execute("ANALYZE feature_vector")
        probe = "[" + ",".join(f"{x:.6f}" for x in base_vecs[1]) + "]"
        cur.execute(
            "SELECT asset_id FROM feature_vector ORDER BY embedding <=> %s::vector LIMIT 1",
            (probe,),
        )
        got = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM feature_vector WHERE model_name='restore-smoke'")
        inserted = cur.fetchone()[0]
    conn.rollback()
    out.update({"expected": ids[1], "got": got, "inserted": inserted,
                "clean_after_rollback": None})
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM feature_vector WHERE model_name='restore-smoke'")
        out["clean_after_rollback"] = cur.fetchone()[0]
    conn.rollback()
    return out


# =============================================================================
#  主流程
# =============================================================================
def main() -> int:
    global FAIL
    keep = "--keep" in sys.argv
    out_dir = OUT_DIR
    if "--out" in sys.argv:
        out_dir = Path(sys.argv[sys.argv.index("--out") + 1])

    base = conninfo()
    dbname = str(base["dbname"])
    print(f"源库：{base['user']}@{base['host']}:{base['port']}/{dbname}")

    pg_dump = find_tool("pg_dump")
    pg_restore = find_tool("pg_restore")
    section("0. 工具与前置")
    check(bool(pg_dump), "pg_dump 可用", pg_dump or "未找到")
    check(bool(pg_restore), "pg_restore 可用", pg_restore or "未找到")
    if not (pg_dump and pg_restore):
        print("\n缺少 pg_dump / pg_restore，无法演练。安装客户端或用 PG_BIN 指定目录。")
        return 2

    env = subprocess_env(base)
    sandbox = f"ich_restore_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    tmpdir = Path(tempfile.mkdtemp(prefix="ich_restore_"))
    dumpfile = tmpdir / f"{dbname}.dump"
    maint = connect(base, "postgres")
    t_start = time.perf_counter()
    report: dict = {"generated_at": datetime.now().isoformat(timespec="seconds"),
                    "source_db": dbname, "sandbox_db": sandbox,
                    "pg_dump": pg_dump, "pg_restore": pg_restore}

    try:
        # ---------------------------------------------------------------- dump
        section("1. 备份（pg_dump -Fc）")
        t0 = time.perf_counter()
        p = subprocess.run(
            [pg_dump, "-Fc", "-Z6", "--no-password", "-f", str(dumpfile)]
            + conn_args(base),
            capture_output=True, text=True, env=env, timeout=600,
        )
        dump_sec = time.perf_counter() - t0
        check(p.returncode == 0, "pg_dump 退出码 0",
              (p.stderr or "").strip().splitlines()[-1] if p.stderr.strip() else "")
        size = dumpfile.stat().st_size if dumpfile.exists() else 0
        check(size > 0, "dump 文件非空", f"{size} 字节，耗时 {dump_sec:.2f}s")
        magic = dumpfile.read_bytes()[:5] if size else b""
        check(magic == b"PGDMP", "dump 为 custom 格式（文件头 PGDMP）", repr(magic))
        report["dump"] = {
            "bytes": size, "seconds": round(dump_sec, 2),
            "sha256": hashlib.sha256(dumpfile.read_bytes()).hexdigest() if size else None,
            "path": str(dumpfile),
        }
        print(f"      dump: {size} 字节  sha256={report['dump']['sha256'][:16]}…")

        # ------------------------------------------------------------- restore
        section("2. 恢复到临时库")
        with maint.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{sandbox}"')
            cur.execute(f'CREATE DATABASE "{sandbox}" TEMPLATE template0')
        check(True, "临时库创建成功", sandbox)

        t0 = time.perf_counter()
        p = subprocess.run(
            [pg_restore, "--no-password"] + conn_args(base, sandbox) + [str(dumpfile)],
            capture_output=True, text=True, env=env, timeout=900,
        )
        restore_sec = time.perf_counter() - t0
        err_lines = [l for l in (p.stderr or "").splitlines() if "ERROR" in l or "错误" in l]
        check(p.returncode == 0 and not err_lines, "pg_restore 退出码 0 且无 ERROR",
              f"耗时 {restore_sec:.2f}s" + (f"，首条错误：{err_lines[0][:120]}" if err_lines else ""))
        report["restore"] = {"seconds": round(restore_sec, 2),
                             "stderr_head": (p.stderr or "").strip().splitlines()[:5]}

        # --------------------------------------------------------- 结构比对
        section("3. 结构一致性（以系统目录为准）")
        src = connect(base, dbname)
        dst = connect(base, sandbox)
        s1 = catalog_snapshot(src)
        s2 = catalog_snapshot(dst)

        for label, key in (("表清单", "tables"), ("视图清单", "views"),
                           ("索引定义", "indexes"), ("约束集合", "constraints"),
                           ("触发器定义", "triggers"), ("函数定义", "functions"),
                           ("枚举取值", "enums"), ("表级授权", "table_acl")):
            a, b = s1[key], s2[key]
            diff = []
            if isinstance(a, dict):
                for k in sorted(set(a) | set(b)):
                    if a.get(k) != b.get(k):
                        diff.append(f"{k}: 源={str(a.get(k))[:60]} 恢复={str(b.get(k))[:60]}")
            else:
                only_a = [x for x in a if x not in b]
                only_b = [x for x in b if x not in a]
                if only_a:
                    diff.append(f"恢复库缺少：{only_a}")
                if only_b:
                    diff.append(f"恢复库多出：{only_b}")
            check(not diff, f"{label}一致",
                  f"{len(a)} 项" if not diff else "；".join(diff[:3]))

        # CHECK 的 deparse 文本：pg_dump 会给枚举比较补 ::text，
        # 文本不同但语义相同。如实记录差了几条，并证明底层枚举仍然生效。
        raw_diff = [k for k in s1["constraints_raw"]
                    if s1["constraints_raw"][k] != s2["constraints_raw"].get(k)]
        if raw_diff:
            note(f"{len(raw_diff)} 条 CHECK 的 deparse 文本不同（pg_dump 补了显式类型转换）："
                 + "、".join(raw_diff[:4]))
            note("判定用归一化定义；下面是行为证据：两边都必须拒绝非法枚举值")

        enum_probe = {}
        for tag, c in (("源库", src), ("恢复库", dst)):
            with c.cursor() as cur:
                try:
                    cur.execute(
                        "INSERT INTO asset (title, owner_id, file_path, file_hash, "
                        "file_size, right_level) VALUES ('枚举探针', "
                        "(SELECT owner_id FROM asset LIMIT 1), '/x', "
                        "repeat('a',64), 1, 'not_a_level')"
                    )
                    enum_probe[tag] = "未拒绝"
                except psycopg2.Error as exc:
                    enum_probe[tag] = exc.pgcode or type(exc).__name__
                finally:
                    c.rollback()
        check(enum_probe.get("源库") == enum_probe.get("恢复库") == "22P02",
              "非法枚举值在两边同样被拒（22P02）",
              json.dumps(enum_probe, ensure_ascii=False))

        # 行数
        rc_diff = [f"{t}: {s1['row_counts'].get(t)}→{s2['row_counts'].get(t)}"
                   for t in s1["row_counts"] if s1["row_counts"].get(t) != s2["row_counts"].get(t)]
        check(not rc_diff, "各表行数一致",
              f"{sum(s1['row_counts'].values())} 行 / {len(s1['row_counts'])} 表"
              if not rc_diff else "；".join(rc_diff[:5]))

        # 内容指纹
        md_diff = [t for t in s1["content_md5"] if s1["content_md5"][t] != s2["content_md5"].get(t)]
        check(not md_diff, "全表内容指纹一致",
              f"{len(s1['content_md5'])} 表" if not md_diff else f"内容不一致：{md_diff[:5]}")

        # 序列
        seq_diff = [f"{k}: {s1['sequences'].get(k)}→{s2['sequences'].get(k)}"
                    for k in s1["sequences"] if s1["sequences"].get(k) != s2["sequences"].get(k)]
        check(not seq_diff, "序列当前值一致（不会撞主键）",
              f"{len(s1['sequences'])} 个序列" if not seq_diff else "；".join(seq_diff))

        # 列级授权（这是最容易在 dump/restore 中丢的东西）
        cg_a, cg_b = sorted(s1["column_grants"]), sorted(s2["column_grants"])
        check(cg_a == cg_b, "列级授权一致",
              f"{len(cg_a)} 条授权" if cg_a == cg_b
              else f"源 {len(cg_a)} 条 vs 恢复 {len(cg_b)} 条；缺：{sorted(set(cg_a)-set(cg_b))[:5]}")

        # 角色
        check(s1["roles"] == s2["roles"] and len(s2["roles"]) == 3,
              "三个业务角色存在且属性一致", json.dumps(s2["roles"], ensure_ascii=False))

        # 角色仍非超级用户（防止"恢复时图省事给了 superuser"）
        check(all(not v[0] for v in s2["roles"].values()),
              "业务角色均非超级用户", json.dumps(s2["roles"], ensure_ascii=False))

        # --------------------------------------------------------- 权限实测
        # 验证的不是"ACL 文本一样"，而是授权仍然**有选择性**：
        #   heritor 能改 title（列级允许），不能改 right_level（列级排除），
        #   也不能改 system_audit_log（追加式表被 REVOKE）。
        # 三条全过，才说明恢复库的权限模型没被"恢复成一把梭"。
        section("4. 恢复库上的权限实测（ACL 文本之外的行为证据）")
        probe = connect(base, sandbox, autocommit=False)
        probe_errors: dict[str, str] = {}
        with probe.cursor() as cur:
            # (1) 允许的列：改 title
            cur.execute("SELECT asset_id FROM asset ORDER BY asset_id LIMIT 1")
            aid = cur.fetchone()[0]
            probe.rollback()
            try:
                cur.execute("SET LOCAL ROLE ich_heritor")
                cur.execute("UPDATE asset SET title = title WHERE asset_id=%s", (aid,))
                probe_errors["改 title（应允许）"] = f"OK rowcount={cur.rowcount}"
            except psycopg2.Error as exc:
                probe_errors["改 title（应允许）"] = exc.pgcode or "ERR"
            probe.rollback()

            # (2) 被列级排除的列：改 right_level
            try:
                cur.execute("SET LOCAL ROLE ich_heritor")
                cur.execute("UPDATE asset SET right_level='public' WHERE asset_id=%s", (aid,))
                probe_errors["改 right_level（应拒绝）"] = "未被拦截"
            except psycopg2.Error as exc:
                probe_errors["改 right_level（应拒绝）"] = exc.pgcode or "ERR"
            probe.rollback()

            # (3) 追加式审计表：heritor 无 UPDATE
            try:
                cur.execute("SET LOCAL ROLE ich_heritor")
                cur.execute("UPDATE system_audit_log SET operation='INSERT'")
                probe_errors["改 system_audit_log（应拒绝）"] = "未被拦截"
            except psycopg2.Error as exc:
                probe_errors["改 system_audit_log（应拒绝）"] = exc.pgcode or "ERR"
            probe.rollback()

            cur.execute("SELECT count(*) FROM asset")
            owner_ok = cur.fetchone()[0]
        probe.close()

        check(str(probe_errors.get("改 title（应允许）", "")).startswith("OK"),
              "heritor 仍可改允许的列（授权未被一刀切）",
              probe_errors.get("改 title（应允许）", ""))
        check(probe_errors.get("改 right_level（应拒绝）") == "42501",
              "heritor 改 right_level 在恢复库中仍被列级授权拒绝",
              f"SQLSTATE={probe_errors.get('改 right_level（应拒绝）')}")
        check(probe_errors.get("改 system_audit_log（应拒绝）") == "42501",
              "heritor 改审计日志在恢复库中仍被拒绝",
              f"SQLSTATE={probe_errors.get('改 system_audit_log（应拒绝）')}")
        check(owner_ok > 0, "维护账号仍可读（拒绝来自角色授权，不是库损坏）",
              f"count={owner_ok}")
        report["privilege_probe"] = probe_errors

        # --------------------------------------------------------- 功能烟测
        section("5. 恢复库功能烟测")
        smoke = connect(base, sandbox, autocommit=False)
        hn = hnsw_smoke(smoke)
        check(hn.get("got") == hn.get("expected") and hn.get("inserted") == 3,
              "HNSW 近邻检索可用且返回正确最近邻",
              f"期望 {hn.get('expected')} 实得 {hn.get('got')}，写入 {hn.get('inserted')} 条")
        check(hn.get("clean_after_rollback") == 0, "烟测向量已随事务回滚清除",
              f"残留 {hn.get('clean_after_rollback')} 条")

        smoke.close()
        drill = run_tx_drill(base, sandbox)
        report["tx_drill"] = drill

        if drill.get("commit"):
            c = drill["commit"]
            b, a = c["before"], c["after"]
            asset_ok = (a["asset"][0] == "derived" and a["asset"][1] == "approved"
                        and a["asset"][2] == b["asset"][2] + 1)
            audit_ok = any(x[1] == "approved" for x in a["audits"]) and \
                       not any(x[1] == "approved" for x in b["audits"])
            log_ok = a["log_count"] > b["log_count"]
            check(asset_ok, "终审正常提交：asset 状态与版本号已更新",
                  f"{b['asset'][:3]} → {a['asset'][:3]}")
            check(audit_ok, "终审正常提交：audit_record 落定结论")
            check(log_ok, "终审正常提交：触发器写入 system_audit_log",
                  f"{b['log_count']} → {a['log_count']}")

        if drill.get("rollback"):
            r = drill["rollback"]
            b, a = r["before"], r["after"]
            check(bool(r["raised"]) and "fault injection" in (r["raised"] or ""),
                  "故障注入确实在事务中途抛出", (r["raised"] or "")[:80])
            check(a["asset"] == b["asset"], "回滚后 asset 未变（状态/版本号/时间戳全同）",
                  f"{b['asset'][:3]} vs {a['asset'][:3]}")
            check(a["audits"] == b["audits"], "回滚后 audit_record 未变（没有半条结论）")
            check(a["log_count"] == b["log_count"], "回滚后 system_audit_log 未变（触发器日志一并回滚）",
                  f"{b['log_count']} vs {a['log_count']}")

        if drill.get("gate"):
            g = drill["gate"]
            check(g["error"] is not None and g["error"].get("pgcode") == "42501",
                  "绕过应用直连改状态仍被数据库闸门拒绝",
                  (g["error"] or {}).get("msg", "")[:100])

        # --------------------------------------------------------- 清理
        section("6. 清理")
        dst.close()
        src.close()
        with maint.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{sandbox}" WITH (FORCE)')
        check(True, "临时库已删除", sandbox if not keep else f"{sandbox}（--keep 保留）")
    except Exception:                                   # noqa: BLE001
        traceback.print_exc()
        FAILED.append("演练过程异常中断")
        global FAIL
        FAIL += 1
        return 1
    finally:
        try:
            if not keep:
                with maint.cursor() as cur:
                    cur.execute(f'DROP DATABASE IF EXISTS "{sandbox}" WITH (FORCE)')
        except Exception:                               # noqa: BLE001
            pass
        maint.close()
        if not keep:
            shutil.rmtree(tmpdir, ignore_errors=True)
        total_sec = time.perf_counter() - t_start

    report["checks"] = RECORDS
    report["seconds"] = round(total_sec, 2)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / f"backup_restore_{stamp}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 74)
    print(f"结果：{PASS} 项通过 / {FAIL} 项失败（总耗时 {total_sec:.1f}s）")
    if FAILED:
        print("失败项：" + "；".join(FAILED))
    print(f"证据：docs/verify/backup_restore_{stamp}.json")
    print("=" * 74)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
