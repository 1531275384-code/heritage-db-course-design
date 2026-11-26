"""可复现的查询性能采集：EXPLAIN (ANALYZE, BUFFERS) 对照。

为什么单独一个脚本
------------------
`tests/check_pg.py` 回答的是「规则有没有落实」，不是「查询快不快」。
性能结论必须能复现，所以这里把三件事固定下来：

    1. 采集哪些查询（向量近邻 / 待审队列 / 台账视图）
    2. 在什么设置下采（默认 vs 关闭索引扫描）
    3. 输出落在哪（docs/verify/bench_<时间戳>.md + .json）

关于「关闭索引扫描」这一组
--------------------------
默认设置下，小数据量的优化器常常选择顺序扫描——这是正确的选择，不是缺陷。
但只贴默认计划，无法区分「索引建了但没被选中」和「索引根本没建」。
因此额外在 `enable_indexscan=off, enable_bitmapscan=off` 下跑一遍：
如果此时计划里仍没有索引节点，说明索引本身不可用，而不是优化器偏好问题。
两组结果**分别标注**，不拿强制设置冒充默认行为。

关于向量场景
------------
降级模式（NUMERIC[]）下没有 HNSW 索引，`<=>` 运算符也不存在，
该场景记为 SKIPPED 并写明原因，而不是拿精确扫描的耗时冒充「向量检索性能」。

用法
----
    PG_DSN="host=127.0.0.1 port=55432 user=postgres dbname=heritage" \
        python -m scripts.bench_pg

    # 只跑某个场景 / 指定输出目录
    python -m scripts.bench_pg --only ledger --out docs/verify
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import psycopg2

BASE = Path(__file__).resolve().parent.parent
DSN = os.environ.get(
    "PG_DSN", "host=127.0.0.1 port=55432 user=postgres dbname=heritage"
)

# 关闭索引扫描时同时关掉 bitmap：否则 bitmap index scan 会顶上来，
# 「索引不可用」的结论就下不了
NO_INDEX = "SET LOCAL enable_indexscan = off; SET LOCAL enable_bitmapscan = off"

# EXPLAIN 会把查询里的向量字面量整条打出来（512 维 ≈ 5KB 一行），
# 报告里既读不了也没必要。保留维度信息，正文省略。
_VEC_LITERAL = re.compile(r"'\[[-0-9eE.,\s]{60,}\]'")


def _trim_vector_literal(plan: str) -> str:
    return _VEC_LITERAL.sub("'[<512 维向量字面量已省略>]'", plan)


def explain(cur, sql: str, params=None) -> tuple[str, float | None]:
    """执行 EXPLAIN (ANALYZE, BUFFERS)，返回 (计划全文, 执行毫秒)。"""
    cur.execute("EXPLAIN (ANALYZE, BUFFERS, COSTS ON, TIMING ON) " + sql, params)
    lines = [r[0] for r in cur.fetchall()]
    plan = _trim_vector_literal("\n".join(lines))
    ms = None
    for line in lines:
        if line.strip().startswith("Execution Time:"):
            ms = float(line.split(":", 1)[1].strip().split()[0])
    return plan, ms


def run_case(cur, name: str, desc: str, sql: str, params=None, available=True,
             skip_reason: str = "") -> dict:
    """在默认设置与关闭索引扫描两种设置下各跑一次，返回结构化结果。"""
    if not available:
        print(f"  - {name}: SKIPPED（{skip_reason}）")
        return {"name": name, "desc": desc, "status": "SKIPPED",
                "reason": skip_reason}

    cur.execute("SAVEPOINT bench")
    # 预热：先执行一次把数据页读进 shared_buffers。
    # 不预热的话，先跑的那一组承担全部冷读，第二组白占便宜，两组耗时不可比。
    cur.execute("EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF) " + sql, params)
    cur.fetchall()
    plan_default, ms_default = explain(cur, sql, params)
    cur.execute(NO_INDEX)
    plan_noindex, ms_noindex = explain(cur, sql, params)
    cur.execute("ROLLBACK TO SAVEPOINT bench")

    cur.execute(f"SELECT count(*) FROM ({sql}) t", params)
    n_rows = cur.fetchone()[0]

    print(f"  - {name}: {n_rows} 行, 默认 {ms_default} ms / 关闭索引 {ms_noindex} ms")
    return {
        "name": name,
        "desc": desc,
        "status": "OK",
        "rows": n_rows,
        "sql": sql.strip(),
        "default": {"plan": plan_default, "exec_ms": ms_default},
        "no_index": {"plan": plan_noindex, "exec_ms": ms_noindex},
    }


def seed_synthetic(cur, n: int) -> None:
    """在**当前事务内**合成 n 条纹样向量，供规模测试使用。

    为什么要合成
    ------------
    种子库只有 3 条母题，优化器必然选顺序扫描——这是正确选择，但无法回答
    「HNSW 在数据量上来后到底省多少」。规模结论不能靠 3 行数据外推。

    为什么不落库
    ------------
    调用方在事务内执行本函数，脚本结束时整笔 ROLLBACK，索引条目与数据页一起撤销，
    不会污染演示库，也不需要事后清理。（HNSW 索引是 WAL 记录的，回滚同样生效。）
    """
    import io

    import numpy as np

    rng = np.random.default_rng(20261126)  # 固定种子：换机器也能复现同一批向量
    print(f"  合成 {n} 条 512 维纹样向量（事务内，结束即回滚）...", flush=True)
    buf = io.StringIO()
    for i in range(1, n + 1):
        v = rng.standard_normal(512).astype("float32")
        v /= np.linalg.norm(v) or 1.0
        buf.write(f"SYN-{i}\t合成纹样 {i}\t合成\t[{','.join(f'{x:.6f}' for x in v)}]\tsynthetic\n")
    buf.seek(0)
    cur.copy_expert(
        "COPY public_motif (motif_code, name, region, base_vector, vector_model) "
        "FROM STDIN", buf
    )
    cur.execute("ANALYZE public_motif")  # 不 ANALYZE 的话统计信息还停在 3 行
    print("  合成数据已就绪（统计信息已更新）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EXPLAIN (ANALYZE, BUFFERS) 性能采集")
    ap.add_argument("--out", default=str(BASE / "docs" / "verify"), help="输出目录")
    ap.add_argument("--only", default="", help="只跑指定场景：vector/queue/ledger")
    ap.add_argument("--synthetic", type=int, default=0, metavar="N",
                    help="额外合成 N 条纹样向量做规模测试（事务内，结束回滚）；"
                         "仅在 pgvector 原生模式下有效")
    args = ap.parse_args(argv)

    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("SELECT version()")
    pg_version = cur.fetchone()[0]
    cur.execute("SELECT ich_vector_is_native()")
    native = cur.fetchone()[0]
    cur.execute("SELECT current_database(), current_setting('server_version')")
    dbname, srv_ver = cur.fetchone()

    print(f"数据库：{dbname}（{srv_ver}）")
    print(f"向量模式：{'pgvector 原生' if native else '降级（NUMERIC[] + 精确扫描）'}")

    results: list[dict] = []
    want = {args.only} if args.only else {"vector", "queue", "ledger"}

    # ---------------------------------------------------------------- 向量近邻
    if "vector" in want:
        if native:
            cur.execute("SELECT base_vector::text FROM public_motif LIMIT 1")
            probe = cur.fetchone()
            if probe:
                results.append(run_case(
                    cur, "vector_knn",
                    "母题库向量近邻检索（HNSW / cosine，LIMIT 5）",
                    "SELECT motif_id, name FROM public_motif "
                    "ORDER BY base_vector <=> %s::vector LIMIT 5",
                    (probe[0],),
                ))
            else:
                results.append(run_case(
                    cur, "vector_knn", "母题库向量近邻检索", "",
                    available=False, skip_reason="public_motif 无数据，缺少查询探针"))
        else:
            results.append(run_case(
                cur, "vector_knn", "母题库向量近邻检索（HNSW / cosine）", "",
                available=False,
                skip_reason="降级模式无 pgvector：无 <=> 运算符与 HNSW 索引，"
                            "精确扫描耗时不能代表向量检索性能"))

    # ------------------------------------------------ 向量近邻（合成规模对照）
    # 种子库只有 3 条母题，无论索引建得多好，优化器都会选顺序扫描。
    # 所以规模结论必须另造数据：合成 → ANALYZE → 采集 → 随事务回滚。
    if "vector" in want and args.synthetic:
        if not native:
            print(f"  - vector_knn_syn{args.synthetic}: SKIPPED（降级模式无 HNSW，"
                  f"合成数据无法体现索引收益）")
        else:
            seed_synthetic(cur, args.synthetic)
            cur.execute("""SELECT base_vector::text FROM public_motif
                           WHERE vector_model='synthetic' LIMIT 1""")
            probe_syn = cur.fetchone()
            results.append(run_case(
                cur, f"vector_knn_syn{args.synthetic}",
                f"母题库向量近邻检索（HNSW / cosine，合成 {args.synthetic} 行对照）",
                "SELECT motif_id, name FROM public_motif "
                "ORDER BY base_vector <=> %s::vector LIMIT 5",
                (probe_syn[0],),
            ))

    # ---------------------------------------------------------------- 待审队列
    if "queue" in want:
        results.append(run_case(
            cur, "pending_audit",
            "专家工作台：待终审资产队列（含 LATERAL 取最新审核）",
            "SELECT asset_id, title, owner_name, top1_similarity, license_count "
            "FROM v_asset_pending_audit ORDER BY submitted_at DESC",
        ))

    # ---------------------------------------------------------------- 台账视图
    if "ledger" in want:
        results.append(run_case(
            cur, "asset_ledger",
            "资产台账：授权数 / 费用汇总 / 审计次数（多路聚合）",
            "SELECT asset_id, title, license_cnt, active_license_cnt, "
            "total_fee_cny, audit_times, db_log_cnt "
            "FROM v_asset_ledger ORDER BY updated_at DESC LIMIT 50",
        ))

    conn.rollback()
    cur.close()
    conn.close()

    # ---------------------------------------------------------------- 落盘
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    md_path = out_dir / f"bench_{stamp}.md"
    json_path = out_dir / f"bench_{stamp}.json"

    json_path.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dsn_db": dbname, "server_version": srv_ver, "pg_version": pg_version,
        "vector_native": native, "cases": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# 性能采集 {stamp}",
        "",
        f"- 数据库：`{dbname}`（PostgreSQL {srv_ver}）",
        f"- 向量模式：{'pgvector 原生（HNSW）' if native else '降级（NUMERIC[] + 精确扫描）'}",
        f"- 环境：`{pg_version}`",
        "",
        "> 默认设置为优化器自由选择；「关闭索引」组为 "
        "`enable_indexscan=off, enable_bitmapscan=off`，用于判断索引是否可用，"
        "不代表线上实际计划。",
        "",
    ]
    if any("_syn" in c["name"] for c in results):
        lines.insert(5, "- 规模对照：`vector_knn_syn*` 的合成数据在事务内生成，"
                        "脚本结束时整笔回滚，**未落库**；固定随机种子可复现")
    for c in results:
        lines.append(f"## {c['name']} —— {c['desc']}")
        lines.append("")
        if c["status"] == "SKIPPED":
            lines.append(f"**SKIPPED**：{c['reason']}")
            lines.append("")
            continue
        lines.append(f"- 返回行数：{c['rows']}")
        lines.append(f"- 默认设置：{c['default']['exec_ms']} ms")
        lines.append(f"- 关闭索引：{c['no_index']['exec_ms']} ms")
        lines.append("")
        lines.append("```sql")
        lines.append(c["sql"])
        lines.append("```")
        lines.append("")
        lines.append("**默认设置计划**")
        lines.append("```")
        lines.append(c["default"]["plan"])
        lines.append("```")
        lines.append("")
        lines.append("**关闭索引扫描后的计划**")
        lines.append("```")
        lines.append(c["no_index"]["plan"])
        lines.append("```")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n已写入：\n  {md_path}\n  {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
