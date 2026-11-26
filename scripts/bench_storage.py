#!/usr/bin/env python
"""
物理存储实测：asset 与 feature_vector 为什么必须分表。

要回答的问题
------------
1. 一行 asset 在磁盘上到底占多少？一行 512 维向量占多少？
2. description / embedding 有没有进 TOAST（行外存储）？TOAST 表和它的索引多大？
3. 拆表省下的到底是"空间"还是"读放大"？关掉拆表（读列表时连带向量）要多读多少页？
4. HNSW 索引有多少是纯空间代价，换来的查询收益是多少？

做法
----
在**一次性沙箱库**（CREATE DATABASE ... TEMPLATE）里造真实规模数据再测，
不污染演示库，跑完 DROP。测量分三层：

    存储层   pg_column_size / pg_relation_size / reltoastrelid / 索引粒度
    TOAST    pg_toast.pg_toast_<oid> 的真实行数（不是"估算"）
    读取层   EXPLAIN (ANALYZE, BUFFERS) 的 shared buffers 与耗时

关于"冷缓存"的诚实说明
----------------------
Windows 上没有 drop_caches，重启实例也清不掉 OS 文件缓存。所以本脚本
**不声称**测了冷读，而以 shared buffers 作为 I/O 成本的主指标——它由页数
决定，与缓存状态无关，可复现；耗时只作为同机同缓存状态下的次要参考。

运行
----
    python -m scripts.bench_storage                 # 默认 2 万行
    python -m scripts.bench_storage --rows 50000
    PG_DSN="host=127.0.0.1 port=55433 user=postgres dbname=ich_heritage" \
        python -m scripts.bench_storage

前置：目标库已按 sql/01→03 建好，且没有别的会话连着它（克隆模板库的要求）。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import psycopg2
import psycopg2.extensions

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "verify"
DIM = 512

# 三段描述画像，用来观察「压缩 + TOAST 阈值」怎么起作用
PROFILES = [
    ("P1", "短描述（约 50 字，真实种子数据水平）"),
    ("P2", "长描述（约 800 字中文，可压缩）"),
    ("P3", "超长描述（约 3840 字节高熵文本，压不动）"),
]


# =============================================================================
#  连接与沙箱
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


def create_sandbox(base: dict) -> tuple[str, psycopg2.extensions.connection]:
    name = f"ich_stor_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    maint = dict(base)
    maint["dbname"] = "postgres"
    maint["application_name"] = "ich_storage_maintenance"
    conn = psycopg2.connect(**maint)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        try:
            cur.execute(f'CREATE DATABASE "{name}" TEMPLATE "{base["dbname"]}"')
        except psycopg2.Error as exc:
            raise RuntimeError(
                f"无法克隆模板库 {base['dbname']}：{exc}\n"
                "  原因通常是还有别的会话连着它（比如正在跑的服务）。\n"
                "  处理：先停掉那些连接，或把 PG_DSN 指向一个空闲库。"
            ) from exc
    return name, conn


def drop_sandbox(maint: psycopg2.extensions.connection, name: str) -> None:
    with maint.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


# =============================================================================
#  造数
# =============================================================================
def seed(conn, rows: int) -> dict:
    """写入 rows 条 asset + 一一对应的 rows 条 feature_vector。"""
    rng = np.random.default_rng(20261126)
    t0 = time.perf_counter()

    with conn.cursor() as cur:
        # 种子数据是带显式 asset_id 插进去的，序列还停在旧位置；
        # 不对齐就会撞主键。这本身也是真实运维要处理的问题，顺手做掉。
        cur.execute(
            "SELECT setval('asset_asset_id_seq', "
            "(SELECT greatest(coalesce(max(asset_id), 0), 1) FROM asset))"
        )
        cur.execute(
            "SELECT setval('feature_vector_fv_id_seq', "
            "(SELECT greatest(coalesce(max(fv_id), 0), 1) FROM feature_vector))"
        )
        cur.execute("SELECT user_id FROM app_user ORDER BY user_id LIMIT 1")
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO app_user (username, display_name, role, password_hash) "
                "VALUES ('storage_probe', '存储探针', 'heritor', 'not-a-real-hash') "
                "RETURNING user_id"
            )
            row = cur.fetchone()
        owner_id = row[0]

        # ---- asset：三条画像按行号轮流，便于分组统计 ----
        cur.execute(
            """
            INSERT INTO asset
                (title, owner_id, file_path, file_hash, file_size,
                 mime_type, right_level, audit_status, description)
            SELECT
                '合成纹样资产 #' || g,
                %(owner)s,
                '/data/uploads/probe/' || g || '.jpg',
                md5(g::text) || md5((g * 7919)::text),
                120000 + g,
                'image/jpeg',
                (ARRAY['public','derived','original'])[1 + (g %% 3)]::right_level_enum,
                'approved',
                CASE g %% 3
                  WHEN 0 THEN '[P1]' ||
                       '该纹样取自传统服饰袖口，主体为回纹与缠枝莲组合，线条圆润，配色以靛蓝为主。'
                  WHEN 1 THEN '[P2]' || repeat(
                       '纹样以缠枝莲为主体，辅以回纹边饰，采用靛蓝与米白两色，常见于当地女性上衣的袖口与衣襟。', 20)
                  ELSE '[P3]' || (
                       SELECT string_agg(md5((g::bigint * 1000003 + i)::text), '')
                       FROM generate_series(1, 120) AS i
                  )
                END
            FROM generate_series(1, %(n)s) AS g
            RETURNING asset_id
            """,
            {"owner": owner_id, "n": rows},
        )
        asset_ids = [r[0] for r in cur.fetchall()]

        # ---- feature_vector：numpy 造 L2 归一化向量，COPY 灌入 ----
        cur.execute("SET LOCAL synchronous_commit = off")
        cp_sql = (
            "COPY feature_vector "
            "(asset_id, model_name, model_version, embed_dim, embedding) "
            "FROM STDIN WITH (FORMAT text)"
        )
        batch = 2000
        for i in range(0, rows, batch):
            chunk = asset_ids[i : i + batch]
            vecs = rng.standard_normal((len(chunk), DIM), dtype=np.float32)
            vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
            buf = io.StringIO()
            for aid, v in zip(chunk, vecs):
                buf.write(
                    f"{aid}\tprobe-builtin-512\tv1\t{DIM}\t"
                    "[" + ",".join(f"{x:.6f}" for x in v.tolist()) + "]\n"
                )
            buf.seek(0)
            cur.copy_expert(cp_sql, buf)
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("ANALYZE asset")
        cur.execute("ANALYZE feature_vector")
    conn.commit()
    return {"rows": rows, "owner_id": owner_id,
            "seed_seconds": round(time.perf_counter() - t0, 2)}


# =============================================================================
#  测量
# =============================================================================
def q1(cur, sql: str):
    cur.execute(sql)
    return cur.fetchone()[0]


def table_storage(cur, table: str) -> dict:
    cur.execute(
        """
        SELECT c.oid, c.relpages, c.reltuples::bigint,
               pg_relation_size(c.oid)                          AS heap_bytes,
               pg_relation_size(c.reltoastrelid)                AS toast_heap_bytes,
               pg_indexes_size(c.reltoastrelid)                 AS toast_index_bytes,
               pg_total_relation_size(c.reltoastrelid)          AS toast_total_bytes,
               pg_indexes_size(c.oid)                           AS index_bytes,
               pg_total_relation_size(c.oid)                    AS total_bytes
        FROM pg_class c WHERE c.oid = %s::regclass
        """,
        (table,),
    )
    oid, pages, tuples, heap, toast_h, toast_i, toast_t, idx, total = cur.fetchone()

    toast_rows = None
    # 物理表名约定：pg_toast.pg_toast_<主表 OID>；不是 TOAST 关系自己的 OID
    if toast_t:
        cur.execute(f"SELECT count(*) FROM pg_toast.pg_toast_{oid}")
        toast_rows = cur.fetchone()[0]

    cur.execute(
        """
        SELECT i.indexrelid::regclass::text,
               pg_relation_size(i.indexrelid),
               pg_get_indexdef(i.indexrelid)
        FROM pg_index i WHERE i.indrelid = %s::regclass
        ORDER BY pg_relation_size(i.indexrelid) DESC
        """,
        (table,),
    )
    indexes = [
        {"name": n, "bytes": int(b), "def": d} for n, b, d in cur.fetchall()
    ]

    return {
        "table": table,
        "heap_bytes": int(heap),
        "heap_pages": int(pages),
        "live_tuples": int(tuples),
        "toast_heap_bytes": int(toast_h),
        "toast_index_bytes": int(toast_i),
        "toast_total_bytes": int(toast_t),
        "toast_rows": toast_rows,
        "index_bytes": int(idx),
        "total_bytes": int(total),
        "indexes": indexes,
    }


def column_profile(cur) -> list[dict]:
    """按描述画像分组，看每段描述的真实存储字节数与是否行外。"""
    out = []
    for tag, label in PROFILES:
        cur.execute(
            """
            SELECT count(*),
                   sum(octet_length(description))                     AS raw_bytes,
                   sum(pg_column_size(description))                   AS stored_bytes,
                   max(pg_column_size(description))                    AS max_stored
            FROM asset WHERE description LIKE %s
            """,
            (f"[{tag}]%",),
        )
        n, raw, stored, mx = cur.fetchone()
        if not n:
            continue
        out.append({
            "profile": tag,
            "label": label,
            "rows": int(n),
            "avg_raw_bytes": round((raw or 0) / n, 1),
            "avg_stored_bytes": round((stored or 0) / n, 1),
            "max_stored_bytes": int(mx or 0),
        })
    return out


def explain(cur, sql: str, label: str) -> dict:
    """跑 EXPLAIN (ANALYZE, BUFFERS)，返回耗时与缓冲区页数。

    BUFFERS 是本脚本的主指标：页数与缓存无关，换机器也成立。
    """
    cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql)
    plan = "\n".join(r[0] for r in cur.fetchall())
    m = re.search(r"Execution Time: ([\d.]+) ms", plan)
    ms = float(m.group(1)) if m else None
    hit = sum(int(x) for x in re.findall(r"shared hit=(\d+)", plan))
    read = sum(int(x) for x in re.findall(r"shared read=(\d+)", plan))
    # 嵌套循环里会有多行 Buffers，取最大值的那行近似顶层，但逐行求和会重复计数；
    # 顶层节点永远是计划第一行，直接取它。
    top = re.search(r"Buffers: shared hit=(\d+)(?: read=(\d+))?", plan)
    top_hit = int(top.group(1)) if top else hit
    top_read = int(top.group(2) or 0) if top else read
    return {
        "label": label,
        "sql": sql.strip(),
        "ms": ms,
        "top_hit_pages": top_hit,
        "top_read_pages": top_read,
        "sum_hit_pages": hit,
        "sum_read_pages": read,
        "plan_head": plan.splitlines()[:6],
    }


def measure(conn, rows: int, owner_id: int) -> dict:
    result: dict = {"generated_at": datetime.now().isoformat(timespec="seconds")}
    with conn.cursor() as cur:
        result["asset"] = table_storage(cur, "public.asset")
        result["feature_vector"] = table_storage(cur, "public.feature_vector")
        result["descriptions"] = column_profile(cur)

        # 单行/单列尺寸
        cur.execute(
            """
            SELECT pg_column_size(t.*) AS row_bytes,
                   pg_column_size(title), pg_column_size(file_path),
                   pg_column_size(description), pg_column_size(file_hash)
            FROM asset t ORDER BY asset_id DESC LIMIT 1
            """
        )
        rb, ti, fp, de, fh = cur.fetchone()
        result["asset_row_sample"] = {
            "row_bytes": int(rb), "title": int(ti), "file_path": int(fp),
            "description": int(de), "file_hash": int(fh),
        }
        cur.execute(
            """
            SELECT pg_column_size(f.*) AS row_bytes,
                   pg_column_size(embedding),
                   octet_length(embedding::text)
            FROM feature_vector f ORDER BY fv_id DESC LIMIT 1
            """
        )
        frb, emb, emb_text = cur.fetchone()
        result["fv_row_sample"] = {
            "row_bytes": int(frb), "embedding_stored": int(emb),
            "embedding_text_len": int(emb_text),
            "raw_float4_bytes": DIM * 4,
        }

        a = result["asset"]
        f = result["feature_vector"]
        n = rows
        result["per_row"] = {
            "asset_heap": round(a["heap_bytes"] / n, 1),
            "asset_toast": round(a["toast_total_bytes"] / n, 1),
            "asset_index": round(a["index_bytes"] / n, 1),
            "fv_heap": round(f["heap_bytes"] / n, 1),
            "fv_toast": round(f["toast_total_bytes"] / n, 1),
            "fv_index": round(f["index_bytes"] / n, 1),
        }
        pr = result["per_row"]
        result["toast_chunks_per_fv_row"] = (
            round(f["toast_rows"] / rows, 2) if f["toast_rows"] else 0
        )
        result["per_row"]["split_total"] = round(
            pr["asset_heap"] + pr["asset_toast"] + pr["asset_index"]
            + pr["fv_heap"] + pr["fv_toast"] + pr["fv_index"], 1
        )
        result["per_row"]["asset_side_share_pct"] = round(
            100 * (pr["asset_heap"] + pr["asset_toast"] + pr["asset_index"])
            / result["per_row"]["split_total"], 1
        )

        # ---------- 读取成本对照 ----------
        # 列表页故意走 idx_asset_owner_time（owner_id + created_at），
        # 这样才是"真的只读 20 行"；若不带 owner 条件，规划器会全表排序，
        # 量到的是排序成本而不是投影宽度，对照就失真了。
        result["reads"] = []
        cases = [
            ("列表页：走 owner 索引取 20 行（只展示列）",
             "SELECT asset_id, title, right_level, audit_status, created_at "
             f"FROM asset WHERE owner_id = {owner_id} "
             "ORDER BY created_at DESC LIMIT 20"),
            ("列表页：同样 20 行，多带 description",
             "SELECT asset_id, title, description, right_level, audit_status, created_at "
             f"FROM asset WHERE owner_id = {owner_id} "
             "ORDER BY created_at DESC LIMIT 20"),
            ("不拆表假设：同样 20 行，连带 512 维向量",
             "SELECT x.asset_id, x.title, x.right_level, x.audit_status, x.created_at, "
             "f.embedding FROM (SELECT asset_id, title, right_level, audit_status, created_at "
             f"FROM asset WHERE owner_id = {owner_id} ORDER BY created_at DESC LIMIT 20) x "
             "JOIN feature_vector f ON f.asset_id = x.asset_id"),
            ("（反例）直接写 join 不限行：规划器退化为全表排序",
             "SELECT a.asset_id, a.title, a.right_level, a.audit_status, a.created_at, "
             "f.embedding FROM asset a JOIN feature_vector f ON f.asset_id = a.asset_id "
             f"WHERE a.owner_id = {owner_id} "
             "ORDER BY a.created_at DESC LIMIT 20"),
            ("全表扫描 asset（不含向量）",
             "SELECT count(*), sum(file_size), max(created_at) FROM asset"),
            ("全表扫描 + 逐行取向量（不拆表时的等价读法）",
             "SELECT count(*), sum(a.file_size), max(a.created_at), sum(f.embed_dim) "
             "FROM asset a JOIN feature_vector f ON f.asset_id = a.asset_id"),
            ("向量近邻检索：HNSW top-5",
             "SELECT asset_id FROM feature_vector "
             "ORDER BY embedding <=> (SELECT embedding FROM feature_vector LIMIT 1) LIMIT 5"),
        ]
        for label, sql in cases:
            result["reads"].append(explain(cur, sql, label))

        # 顺序扫描基线：强制关掉索引，量一次"暴力全表比一遍"的代价
        cur.execute("SET LOCAL enable_indexscan = off")
        cur.execute("SET LOCAL enable_bitmapscan = off")
        cur.execute("SET LOCAL enable_seqscan = on")
        result["reads"].append(
            explain(
                cur,
                "SELECT asset_id FROM feature_vector "
                "ORDER BY embedding <=> (SELECT embedding FROM feature_vector LIMIT 1) LIMIT 5",
                "向量近邻检索：顺序扫描基线（关索引）",
            )
        )
        cur.execute("RESET enable_indexscan")
        cur.execute("RESET enable_bitmapscan")
    return result


# =============================================================================
#  输出
# =============================================================================
def human(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(n) < 1024 or unit == "GiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n} B"


def render(res: dict) -> str:
    a, f = res["asset"], res["feature_vector"]
    L: list[str] = []
    p = L.append
    p(f"# 物理存储实测（{res['generated_at']}）\n")
    p(f"数据规模：asset {a['live_tuples']} 行，feature_vector {f['live_tuples']} 行\n")

    p("## 1. 表与 TOAST 占用\n")
    p("| 对象 | 堆 | TOAST 堆 | TOAST 索引 | 索引 | 合计 | 每行合计 |")
    p("|---|---|---|---|---|---|---|")
    for t in (a, f):
        n = t["live_tuples"] or 1
        p(f"| {t['table']} | {human(t['heap_bytes'])} | {human(t['toast_heap_bytes'])} "
          f"| {human(t['toast_index_bytes'])} | {human(t['index_bytes'])} "
          f"| {human(t['total_bytes'])} | {human(t['total_bytes'] // n)} |")
    p("")
    p(f"- asset 的 TOAST 表实际行数：**{a['toast_rows']}**（{a['live_tuples']} 行中）")
    p(f"- feature_vector 的 TOAST 表实际行数：**{f['toast_rows']}**（{f['live_tuples']} 行中，"
      f"平均每行 {res.get('toast_chunks_per_fv_row')} 个 chunk —— 2052 B 的向量超过单 chunk 上限，被切开存）")
    p("")

    p("## 2. 索引明细（按大小降序）\n")
    p("| 表 | 索引 | 大小 | 定义 |")
    p("|---|---|---|---|")
    for t in (a, f):
        for ix in t["indexes"]:
            p(f"| {t['table']} | {ix['name']} | {human(ix['bytes'])} | `{ix['def'][:110]}` |")
    p("")

    p("## 3. 描述字段的压缩与 TOAST 行为\n")
    p("| 画像 | 行数 | 平均原始字节 | 平均存储字节 | 压缩率 | 最大存储字节 |")
    p("|---|---|---|---|---|---|")
    for d in res["descriptions"]:
        ratio = (d["avg_stored_bytes"] / d["avg_raw_bytes"]) if d["avg_raw_bytes"] else 0
        p(f"| {d['profile']}（{d['label']}） | {d['rows']} | {d['avg_raw_bytes']} "
          f"| {d['avg_stored_bytes']} | {ratio:.1%} | {d['max_stored_bytes']} |")
    p("")

    s = res["asset_row_sample"]
    v = res["fv_row_sample"]
    p("## 4. 单行尺寸抽样\n")
    p(f"- asset 整行 `pg_column_size(t.*)` = **{s['row_bytes']} B**"
      f"（title {s['title']} / file_path {s['file_path']} / description {s['description']} / file_hash {s['file_hash']}）")
    p(f"- feature_vector 整行 = **{v['row_bytes']} B**，其中 embedding 存储 **{v['embedding_stored']} B**")
    p(f"  （512 维 float4 裸数据 {v['raw_float4_bytes']} B；文本表示 {v['embedding_text_len']} B，说明 pgvector 按二进制存，不是存文本）")
    p("")

    pr = res["per_row"]
    p("## 5. 每行摊销\n")
    p("| 部分 | 字节/行 |")
    p("|---|---|")
    for k in ("asset_heap", "asset_toast", "asset_index", "fv_heap", "fv_toast", "fv_index"):
        p(f"| {k} | {pr[k]} |")
    p(f"| **合计** | **{pr['split_total']}** |")
    p(f"\nasset 侧只占 {pr['asset_side_share_pct']}%，其余 {(100 - pr['asset_side_share_pct']):.1f}% 全是向量与它的索引。\n")

    p("## 6. 读取成本对照（shared buffers = 8KiB 页）\n")
    p("| 场景 | 顶层命中页 | 顶层读盘页 | 耗时 ms |")
    p("|---|---|---|---|")
    for r in res["reads"]:
        p(f"| {r['label']} | {r['top_hit_pages']} | {r['top_read_pages']} | {r['ms']} |")
    p("")
    by = {r["label"]: r for r in res["reads"]}
    k1 = "列表页：走 owner 索引取 20 行（只展示列）"
    k3 = "不拆表假设：同样 20 行，连带 512 维向量"
    if k1 in by and k3 in by:
        p1, p3 = by[k1]["top_hit_pages"], by[k3]["top_hit_pages"]
        if p1:
            p(f"同一列表查询（20 行）：只取展示列 **{p1} 页**，连带向量 **{p3} 页**，"
              f"放大 **{p3 / max(p1,1):.1f}×**。这就是分表要解决的问题——"
              "不是省磁盘，是让不关心向量的查询不必把向量页读进来。\n")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="asset / feature_vector 物理存储实测")
    ap.add_argument("--rows", type=int, default=20000, help="造多少行（默认 20000）")
    ap.add_argument("--keep", action="store_true", help="保留沙箱库便于手工排查")
    args = ap.parse_args()

    base = conninfo()
    print(f"目标库：{base['user']}@{base['host']}:{base['port']}/{base['dbname']}")
    sandbox, maint = create_sandbox(base)
    print(f"沙箱库：{sandbox}")

    conn = psycopg2.connect(**{**base, "dbname": sandbox})
    conn.autocommit = False
    try:
        info = seed(conn, args.rows)
        print(f"造数完成：{info['rows']} 行，用时 {info['seed_seconds']}s")
        res = measure(conn, args.rows, info["owner_id"])
        res.update({"sandbox": sandbox, "seed": info})
    finally:
        conn.close()
        if not args.keep:
            drop_sandbox(maint, sandbox)
            print("沙箱库已删除")
        maint.close()

    md = render(res)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (OUT_DIR / f"storage_{stamp}.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / f"storage_{stamp}.md").write_text(md, encoding="utf-8")
    print("\n" + md)
    print(f"\n已写入 docs/verify/storage_{stamp}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
