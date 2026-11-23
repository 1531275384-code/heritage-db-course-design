"""
命令行运维工具（不依赖 Web 服务，直接操作数据库）。

三个子命令对应 README「初始化」章节的三步：

    python -m app.cli check                  # 自检：数据库连接、扩展、表、视图、角色、AI 后端
    python -m app.cli backfill-motif-vectors # 为公有母题回填真实特征向量（覆盖种子里的占位向量）
    python -m app.cli demo-check             # 用内置样例图做一次"分析→分级建议"，不进库

为什么需要独立 CLI？
    1. 种子脚本 03_seed.sql 只能写占位向量 —— 真实特征必须由 Python 侧 CV 模型算出来。
       初始化顺序是「建表 → 灌种子 → 回填向量」，中间这一步必须能用非 HTTP 方式触发。
    2. 排障时希望不启 Web 服务也能确认"库通不通、pgvector 装没装、AI 后端选的哪个"。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import services                                    # noqa: E402
from app.ai.classifier import SimilarityHit, level_label, suggest_level  # noqa: E402
from app.ai.features import current_backend_info, extract_from_path  # noqa: E402
from app.config import BASE_DIR                              # noqa: E402
from app.db import close_pool, init_pool, open_tx            # noqa: E402

# 母题参考图放在 data/motifs 下，种子数据里 reference_img 字段存的是相对 data/ 的路径
MOTIF_IMAGE_DIR = BASE_DIR / "data"

OK = "  ✓"
BAD = "  ✗"


# =============================================================================
#  check：环境自检
# =============================================================================
EXPECTED_TABLES = [
    "app_user", "public_motif", "asset", "feature_vector",
    "audit_record", "license_record", "asset_genealogy", "system_audit_log",
]
EXPECTED_VIEWS = ["v_asset_pending_audit", "v_asset_trace_chain", "v_asset_ledger"]
EXPECTED_ROLES = ["ich_heritor", "ich_expert", "ich_admin"]


def cmd_check(_args: argparse.Namespace) -> int:
    """逐项检查初始化是否完整。任何一项失败返回非零码，便于 CI/脚本判断。"""
    failures = 0

    print("[1] 数据库连接")
    try:
        init_pool()
        with open_tx() as cur:
            cur.execute("SELECT version() AS v, current_database() AS db, current_user AS u")
            row = cur.fetchone()
        print(f"{OK} 已连接 {row['db']}（用户 {row['u']}）")
        print(f"    {str(row['v']).split(' on ')[0]}")
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD} 连接失败：{type(exc).__name__}: {exc}")
        print("\n请检查：数据库是否已启动、app/config.py 或环境变量 ICH_DB_* 是否正确。")
        return 1

    print("\n[2] 扩展")
    with open_tx() as cur:
        cur.execute("SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','pgcrypto')")
        exts = {r["extname"]: r["extversion"] for r in cur.fetchall()}
    for name in ("vector", "pgcrypto"):
        if name in exts:
            print(f"{OK} {name} {exts[name]}")
        else:
            print(f"{BAD} 缺少扩展 {name}（执行 sql/01_init.sql 前需先安装该扩展的服务端文件）")
            failures += 1

    print("\n[3] 表")
    with open_tx() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() ORDER BY tablename"
        )
        tables = {r["tablename"] for r in cur.fetchall()}
    for t in EXPECTED_TABLES:
        if t in tables:
            with open_tx() as cur:
                cur.execute(f"SELECT count(*) AS c FROM {t}")   # 表名来自硬编码白名单
                cnt = cur.fetchone()["c"]
            print(f"{OK} {t:<20} {cnt:>6} 行")
        else:
            print(f"{BAD} 缺少表 {t}")
            failures += 1

    print("\n[4] 视图")
    for v in EXPECTED_VIEWS:
        try:
            with open_tx() as cur:
                cur.execute(f"SELECT count(*) AS c FROM {v}")
                cnt = cur.fetchone()["c"]
            print(f"{OK} {v:<24} {cnt:>6} 行")
        except Exception as exc:  # noqa: BLE001
            print(f"{BAD} 视图 {v} 不可用：{type(exc).__name__}")
            failures += 1

    print("\n[5] 数据库角色")
    with open_tx() as cur:
        cur.execute("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", (EXPECTED_ROLES,))
        roles = {r["rolname"] for r in cur.fetchall()}
    for r in EXPECTED_ROLES:
        print(f"{OK} {r}" if r in roles else f"{BAD} 缺少角色 {r}")
        failures += 0 if r in roles else 1

    print("\n[6] 触发器（验证审计留痕机制已部署）")
    with open_tx() as cur:
        cur.execute(
            """
            SELECT c.relname AS tbl, t.tgname
            FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
            WHERE NOT t.tgisinternal
            ORDER BY c.relname, t.tgname
            """
        )
        trigs = cur.fetchall()
    for r in trigs:
        print(f"{OK} {r['tbl']}.{r['tgname']}")
    if not trigs:
        print(f"{BAD} 未发现自定义触发器")
        failures += 1

    print("\n[7] HNSW 向量索引")
    with open_tx() as cur:
        cur.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE schemaname = current_schema() AND indexdef ILIKE '%hnsw%'
            """
        )
        hnsw = [r["indexname"] for r in cur.fetchall()]
    for name in hnsw:
        print(f"{OK} {name}")
    if not hnsw:
        print(f"{BAD} 未发现 HNSW 索引（pgvector >= 0.5.0 才支持）")
        failures += 1

    print("\n[8] AI 特征后端")
    info = current_backend_info()
    tag = "深度模型" if info["is_deep_model"] else "轻量手工特征（无 torch 依赖）"
    print(f"{OK} {info['backend']} v{info['version']}  dim={info['dim']}  [{tag}]")
    if not info["is_deep_model"]:
        print("    提示：演示够用；如需更强语义区分力，pip install -r requirements-deep.txt")

    print("\n[9] 公有母题向量")
    with open_tx() as cur:
        cur.execute(
            """
            SELECT motif_code, name, vector_model,
                   (base_vector IS NOT NULL) AS has_vec
            FROM public_motif ORDER BY motif_id
            """
        )
        motifs = cur.fetchall()
    if not motifs:
        print(f"{BAD} 公有母题库为空（执行 sql/03_seed.sql）")
        failures += 1
    for m in motifs:
        placeholder = (m["vector_model"] or "").startswith("placeholder")
        note = "  ⚠ 仍为占位向量，请执行 backfill-motif-vectors" if placeholder else ""
        print(f"{OK} {m['motif_code']} {m['name']:<8} model={m['vector_model']}{note}")

    close_pool()
    print()
    if failures:
        print(f"自检未通过：{failures} 项失败")
        return 1
    print("自检全部通过，可以启动服务：uvicorn app.main:app --reload")
    return 0


# =============================================================================
#  backfill-motif-vectors：回填真实特征向量
# =============================================================================
def cmd_backfill(_args: argparse.Namespace) -> int:
    """
    遍历 public_motif，用 reference_img 指向的参考图重新提取特征并写回。

    ⚠️ 这里必须以 admin 身份写库 —— ich_heritor / ich_expert 都没有
       public_motif 的写权限（母题库由监管机构维护），这正是 GRANT 设计的一部分。
    """
    init_pool()
    with open_tx() as cur:
        cur.execute("SELECT motif_id, motif_code, name, reference_img FROM public_motif ORDER BY motif_id")
        motifs = cur.fetchall()

    if not motifs:
        print("公有母题库为空，先执行 sql/03_seed.sql")
        close_pool()
        return 1

    print(f"共 {len(motifs)} 条母题待回填（使用后端：{current_backend_info()['backend']}）")
    failed = 0
    for m in motifs:
        rel = m["reference_img"]
        if not rel:
            print(f"{BAD} {m['motif_code']} {m['name']}：reference_img 为空，跳过")
            failed += 1
            continue

        img_path = (MOTIF_IMAGE_DIR / rel).resolve()
        if not img_path.exists():
            print(f"{BAD} {m['motif_code']} {m['name']}：参考图不存在 {img_path}")
            failed += 1
            continue

        try:
            res = services.backfill_motif_vector(m["motif_id"], str(img_path))
            print(f"{OK} {m['motif_code']} {m['name']:<8} ← {res['model_name']}@{res['model_version']}")
        except Exception as exc:  # noqa: BLE001
            print(f"{BAD} {m['motif_code']} {m['name']}：{type(exc).__name__}: {exc}")
            failed += 1

    close_pool()
    print()
    if failed:
        print(f"回填完成，{failed} 条失败")
        return 1
    print("回填完成。现在上传一张纹样图即可跑通「AI 分析 → 专家审核 → 溯源链路」。")
    return 0


# =============================================================================
#  demo-check：不写库，只用内置样例验证 AI 分级链路
# =============================================================================
def cmd_demo_check(_args: argparse.Namespace) -> int:
    """
    对 data/samples 下的样例图直接提取特征，与 data/motifs 下的母题比对，
    打印分级建议。**完全不连数据库**，用于单独验证 AI 模块是否正常。
    """
    samples = {
        "改编样例（蝴蝶纹改编）": BASE_DIR / "data" / "samples" / "sample_derived_butterfly.jpg",
        "原创样例（鱼纹）":       BASE_DIR / "data" / "samples" / "sample_original_fish.jpg",
    }
    motifs = {
        "蝴蝶纹":   BASE_DIR / "data" / "motifs" / "motif_butterfly.jpg",
        "缠枝莲纹": BASE_DIR / "data" / "motifs" / "motif_lotus.jpg",
        "回纹":     BASE_DIR / "data" / "motifs" / "motif_huiwen.jpg",
    }

    missing = [p for p in list(samples.values()) + list(motifs.values()) if not p.exists()]
    if missing:
        print("缺少样例图片：")
        for p in missing:
            print(f"  {p}")
        print("\n可先运行：python -m app.motif_images  重新生成")
        return 1

    print(f"特征后端：{current_backend_info()['backend']}")
    print("正在提取母题特征 …")
    motif_vecs = {name: extract_from_path(p).vector for name, p in motifs.items()}

    import numpy as np

    rc = 0
    for label, path in samples.items():
        print(f"\n{label}   ({path.name})")
        q = np.asarray(extract_from_path(path).vector, dtype=np.float64)
        hits: list[SimilarityHit] = []
        for idx, (name, mv) in enumerate(motif_vecs.items(), start=1):
            v = np.asarray(mv, dtype=np.float64)
            # 向量已 L2 归一化，余弦相似度 = 内积
            s = float(q @ v)
            hits.append(SimilarityHit(
                kind="public_motif", id=idx, code=f"MO-DEMO-{idx:02d}",
                name=name, similarity=s, distance=1.0 - s,
            ))
        hits.sort(key=lambda h: -h.similarity)

        for h in hits:
            bar = "█" * int(round(h.similarity * 40))
            print(f"    {h.name:<8} {h.similarity:.4f}  {bar}")

        res = suggest_level(hits, [])
        print(f"    → AI 建议：{level_label(res.level)}（{res.level}），"
              f"置信度 {res.confidence:.4f}，规则 {res.rule}")
        print(f"    判定依据：{res.reason}")

        expect = "derived" if "改编" in label else "original"
        if res.level == expect:
            print(f"{OK} 符合预期（{expect}）")
        else:
            print(f"{BAD} 与预期不符（期望 {expect}）")
            rc = 1

    print("\n说明：AI 结果只是**建议**，最终权属由专家在数据库事务中终审确定。")
    return rc


def cmd_demo_seed(_args: argparse.Namespace) -> int:
    """
    往演示模式的内存库里预置两条示例资产，让界面一登录就有内容可看。

    ⚠️ 只对演示模式有效。真实数据库模式的种子数据由 sql/03_seed.sql 负责 ——
    那才是唯一的权威来源，这里不能也不应该往生产库里插演示数据。
    """
    from app import demo

    # CLI 不走 lifespan，所以要自己按配置激活。
    # 只有显式 ICH_DEMO_MODE=1 才允许 —— auto 在这里没有意义：
    # 一个一次性命令没必要"先试着连库再降级"，那只会让行为不可预测。
    if demo.mode_setting() == "1" and not demo.is_enabled():
        demo.activate()
        demo.STORE.warm_motif_vectors()

    if not demo.is_enabled():
        print(f"{BAD} 当前不是演示模式。本命令只用于内存存储的演示环境。")
        print("    用法：ICH_DEMO_MODE=1 python -m app.cli demo-seed")
        print("    真实数据库请执行：psql -d ich_heritage -f sql/03_seed.sql")
        return 1

    from app.config import BASE_DIR

    samples = [
        ("sample_derived_butterfly.jpg", "蝶恋花·缠枝改良袖片",
         "取苗族蝴蝶纹母题，重绘翅纹线条，配色改为靛蓝配赭石，结构比例调整为竖向三段式。"),
        ("sample_original_fish.jpg", "鱼跃云纹·新构",
         "以本地水乡的鱼形为原型，纯自创构成，未参照任何既有传统纹样。"),
    ]

    # 传承人上传
    heritor = next(u for u in demo.STORE.users.values() if u["role"] == "heritor")
    created: list[int] = []
    for fname, title, desc in samples:
        p = BASE_DIR / "data" / "samples" / fname
        if not p.exists():
            print(f"{BAD} 样例图不存在：{p}")
            continue
        key = f"demo_{fname}"
        res = demo.create_asset(
            owner_id=heritor["user_id"], title=title, description=desc,
            file_bytes=p.read_bytes(), mime_type="image/jpeg", object_key=fname, app_role="heritor",
        )
        # create_asset 只存 object_key，这里补一份真实文件副本，让原图接口能取到
        import shutil
        from app.config import UPLOAD_DIR
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dst = UPLOAD_DIR / fname
        if not dst.exists():
            shutil.copy2(p, dst)
        demo.STORE.assets[res["asset_id"]]["file_path"] = fname

        if res["duplicate_of"]:
            created.append(res["duplicate_of"])
            continue
        print(f"  + 资产 #{res['asset_id']}  {title}")

        an = demo.analyze_asset(
            asset_id=res["asset_id"], app_role="heritor", world_app_role="heritor",
            viewer_id=heritor["user_id"], is_privileged=False, include_plan=False,
        )
        print(f"    AI 建议 {level_label(an['suggested_level'])}  置信度 {an['confidence']:.4f}"
              f"  top1={an['top_k'][0]['name']} {an['top_k'][0]['similarity']:.4f}")
        created.append(res["asset_id"])

    # 第一条由专家终审通过，这样台账/溯源/授权都有真实内容
    if created:
        expert = next(u for u in demo.STORE.users.values() if u["role"] == "expert")
        aid = created[0]
        demo.approve_asset(
            asset_id=aid, final_level="derived",
            opinion="与蝴蝶纹母题存在明显借鉴关系，但线条、配色、构图均有实质改动，认定为改编衍生。",
            expert_id=expert["user_id"], expert_name=expert["display_name"],
        )
        demo.create_license(
            asset_id=aid,
            payload={
                "licensee_name": "苏州丝语文创有限公司", "license_type": "exclusive",
                "scope": "家纺类文创产品", "territory": "中国大陆",
                "start_date": "2026-01-01", "end_date": "2028-12-31", "fee_cny": 120000,
            },
            actor_id=heritor["user_id"], app_role="heritor", is_privileged=False,
        )
        print(f"  ✓ 资产 #{aid} 已由 {expert['display_name']} 终审为「改编衍生」，并登记一份独占授权")

    print(f"\n{OK} 演示数据就绪：{len(demo.STORE.assets)} 条资产，"
          f"{len(demo.STORE.audit_records)} 条审核流水，"
          f"{len(demo.STORE.audit_log)} 条 DB 审计日志，"
          f"{len(demo.STORE.license_records)} 条授权记录")
    print("    注意：这些数据只存在于本进程内存，重新启动即清空。")
    return 0


# =============================================================================
#  入口
# =============================================================================
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="非遗数字资产确权系统 —— 运维命令行工具",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="环境自检：连接/扩展/表/视图/角色/触发器/索引/AI 后端").set_defaults(func=cmd_check)
    sub.add_parser("backfill-motif-vectors", help="为公有母题回填真实特征向量").set_defaults(func=cmd_backfill)
    sub.add_parser("demo-check", help="用内置样例验证 AI 分级链路（不连数据库）").set_defaults(func=cmd_demo_check)
    sub.add_parser("demo-seed", help="往演示模式内存库预置示例资产（仅 ICH_DEMO_MODE=1 有效）").set_defaults(func=cmd_demo_seed)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
