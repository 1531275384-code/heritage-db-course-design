"""
把 data/seed_demo.json 灌进真实 PostgreSQL。

═══════════════════════════════════════════════════════════════════════════════
 它和演示模式的关系
═══════════════════════════════════════════════════════════════════════════════
demo.py 在进程内存里"重放"这份 JSON，让没有数据库的机器也能看完整界面。
这个脚本做的是同一件事的**持久化版本**：把同样的账号、母题、作品、审核流水、
授权记录写进 8 张真实表，让真库模式（ICH_DEMO_MODE=0）一开就有数据。

两者共用同一个 JSON 源，所以切库时界面长得一模一样 —— 只有横幅消失、
数据库约束开始真正生效。这正是演示要传达的差别。

═══════════════════════════════════════════════════════════════════════════════
 与 sql/03_seed.sql 的分工
═══════════════════════════════════════════════════════════════════════════════
  03_seed.sql  ：最小可运行种子（3 账号 / 3 母题），给"建库就能跑"用
  seed_demo.json + 本脚本：**演示级**种子（4 账号 / 8 作品 / 全链路），给"要展示"用
两者可叠加：先跑 03_seed.sql，再跑本脚本，本脚本用 ON CONFLICT 幂等覆盖。

═══════════════════════════════════════════════════════════════════════════════
 用法
═══════════════════════════════════════════════════════════════════════════════
  python -m scripts.init_seed              # 灌数据（先清空演示作品，再重建）
  python -m scripts.init_seed --dry-run    # 只打印将要执行的 SQL，不写库
  python -m scripts.init_seed --keep       # 不清空已有作品，只补缺的

⚠️ 只应在演示 / 测试库执行。生产库请勿运行（会插入虚构的授权记录）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED_FILE = ROOT / "data" / "seed_demo.json"
UPLOAD_DIR = ROOT / "data" / "uploads"

# 演示作品 id 起点：避开 03_seed.sql 可能已用的低号
DEMO_ASSET_BASE = 1000

OK = "\033[32m✓\033[0m"
BAD = "\033[31m✗\033[0m"
WARN = "\033[33m!\033[0m"


def load_seed() -> dict:
    if not SEED_FILE.exists():
        raise SystemExit(f"种子文件不存在：{SEED_FILE}")
    return json.loads(SEED_FILE.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="把 seed_demo.json 灌入 PostgreSQL")
    ap.add_argument("--dry-run", action="store_true", help="只打印 SQL，不写库")
    ap.add_argument("--keep", action="store_true", help="不清空已有演示作品")
    args = ap.parse_args(argv)

    from app.db import healthy, open_tx

    ok, info = healthy()
    print("=" * 74)
    print(" 演示种子数据 · 灌入 PostgreSQL")
    print("=" * 74)
    print(f" 数据库：{info}")
    if not ok:
        print(f"\n{BAD} 数据库不可用。请先按 README 完成建库与 01/02/03 SQL 初始化。")
        print("   （没有数据库时直接用 python bootstrap.py 走演示模式，数据在内存里。）")
        return 1

    seed = load_seed()
    t0 = datetime.now(timezone.utc)

    if args.dry_run:
        print("\n[DRY-RUN] 以下内容将写入数据库：")
        print(f"  账号 {len(seed['users'])} 个、母题 {len(seed['motifs'])} 条、"
              f"作品 {len(seed['assets'])} 件")
        for a in seed["assets"]:
            na = len(a.get("audits") or [])
            nl = len(a.get("licenses") or [])
            print(f"    #{a['asset_id']} {a['title']}  "
                  f"[{a['audit_status']}/{a['right_level']}]  审核{na} 授权{nl}")
        return 0

    # ------------------------------------------------------------------ 母题
    print("\n[1/4] 母题")
    with open_tx(app_role="admin") as cur:
        for m in seed["motifs"]:
            cur.execute(
                """
                INSERT INTO public_motif
                       (motif_id, motif_code, name, region, description, reference_img)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (motif_id) DO UPDATE
                   SET motif_code = EXCLUDED.motif_code,
                       name       = EXCLUDED.name,
                       region     = EXCLUDED.region,
                       description= EXCLUDED.description,
                       reference_img = EXCLUDED.reference_img
                """,
                (m["motif_id"], m["motif_code"], m["name"],
                 m.get("region"), m.get("description"), m.get("reference_img")),
            )
            print(f"  {OK} {m['motif_code']} {m['name']}")

    # ------------------------------------------------------------------ 作品
    print("\n[2/4] 作品")
    with open_tx(app_role="admin") as cur:
        if not args.keep:
            # 只清演示作品（id >= base），不动 03_seed.sql 的样例
            cur.execute("DELETE FROM asset WHERE asset_id >= %s", (DEMO_ASSET_BASE,))
            deleted = cur.rowcount
            if deleted:
                print(f"  {WARN} 已清空 {deleted} 条旧演示作品（含级联的审核/授权记录）")

        for spec in seed["assets"]:
            asset_id = DEMO_ASSET_BASE + spec["asset_id"]
            f = UPLOAD_DIR / spec["file_path"]
            data = f.read_bytes() if f.exists() else b""
            fh = hashlib.sha256(data).hexdigest() if data else ("0" * 64)
            created = t0 - timedelta(days=float(spec.get("created_days_ago", 0)))
            updated = (t0 - timedelta(days=float(spec["decided_days_ago"]))
                       if spec.get("decided_days_ago") is not None else created)

            cur.execute(
                """
                INSERT INTO asset
                    (asset_id, title, description, owner_id, motif_id,
                     file_path, file_hash, file_size, mime_type,
                     right_level, audit_status, ai_suggest_level, ai_confidence,
                     version_no, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (asset_id) DO UPDATE
                   SET title = EXCLUDED.title,
                       description = EXCLUDED.description,
                       right_level = EXCLUDED.right_level,
                       audit_status = EXCLUDED.audit_status,
                       ai_suggest_level = EXCLUDED.ai_suggest_level,
                       ai_confidence = EXCLUDED.ai_confidence,
                       motif_id = EXCLUDED.motif_id,
                       version_no = EXCLUDED.version_no,
                       updated_at = EXCLUDED.updated_at
                """,
                (asset_id, spec["title"], spec.get("description"),
                 spec["owner_id"], spec.get("motif_id"),
                 spec["file_path"], fh, len(data), "image/jpeg",
                 spec.get("right_level"), spec.get("audit_status"),
                 spec.get("ai_suggest_level"), spec.get("ai_confidence"),
                 spec.get("version_no", 0), created, updated),
            )
            print(f"  {OK} #{asset_id} {spec['title']}")

    # ------------------------------------------------------------------ 审核流水
    print("\n[3/4] 审核流水")
    with open_tx(app_role="expert") as cur:
        for spec in seed["assets"]:
            asset_id = DEMO_ASSET_BASE + spec["asset_id"]
            cur.execute("DELETE FROM audit_record WHERE asset_id = %s", (asset_id,))
            for a in spec.get("audits") or []:
                # similarity_json 必须写成 JSON 数组，形状与 services.analyze_asset 一致
                sim = []
                if a.get("top_motif_id") and a.get("similarity") is not None:
                    mm = next((x for x in seed["motifs"]
                               if x["motif_id"] == a["top_motif_id"]), None)
                    if mm:
                        sim = [{
                            "id": mm["motif_id"], "code": mm["motif_code"],
                            "name": mm["name"], "similarity": a["similarity"],
                            "distance": round(1 - float(a["similarity"]), 4),
                        }]
                created = t0 - timedelta(days=float(a.get("days_ago", 0)))
                decided = (t0 - timedelta(days=float(a["decided_days_ago"]))
                           if a.get("decided_days_ago") is not None else None)
                if a["state"] != "pending" and decided is None:
                    decided = created
                cur.execute(
                    """
                    INSERT INTO audit_record
                        (asset_id, expert_id, state, ai_suggest_level, ai_confidence,
                         expert_level, similarity_json, opinion, is_override,
                         created_at, decided_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
                    """,
                    (asset_id, a.get("expert_id"), a["state"],
                     a.get("ai_suggest_level"), a.get("ai_confidence"),
                     a.get("expert_level"), json.dumps(sim, ensure_ascii=False),
                     a.get("opinion"), bool(a.get("is_override")),
                     created, decided),
                )
            n = len(spec.get("audits") or [])
            if n:
                print(f"  {OK} 资产 #{asset_id}：{n} 条")

    # ------------------------------------------------------------------ 授权
    print("\n[4/4] 授权记录")
    with open_tx(app_role="heritor") as cur:
        for spec in seed["assets"]:
            asset_id = DEMO_ASSET_BASE + spec["asset_id"]
            cur.execute("DELETE FROM license_record WHERE asset_id = %s", (asset_id,))
            for lic in spec.get("licenses") or []:
                start = (t0 - timedelta(days=float(lic.get("start_days_ago", 0)))).date()
                end_raw = lic.get("end_days_ago")
                end = (t0 - timedelta(days=float(end_raw))).date() if end_raw is not None else None
                ch = hashlib.sha256(
                    f"{asset_id}|{lic.get('licensee_name')}|{lic.get('fee_cny')}".encode()
                ).hexdigest()
                cur.execute(
                    """
                    INSERT INTO license_record
                        (asset_id, licensor_id, license_type, licensee_name, scope,
                         territory, start_date, end_date, fee_cny, status, contract_hash)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (asset_id, spec["owner_id"], lic.get("license_type"),
                     lic.get("licensee_name"), lic.get("scope"), lic.get("territory"),
                     start, end, lic.get("fee_cny"), lic.get("status", "active"), ch),
                )
            n = len(spec.get("licenses") or [])
            if n:
                print(f"  {OK} 资产 #{asset_id}：{n} 笔")

    print("\n" + "=" * 74)
    print(" 完成。接下来：")
    print("   1) 回填母题向量（真库模式下检索要用）：")
    print("        python -m app.cli backfill-motif-vectors")
    print("   2) 启动服务（真库模式）：")
    print("        set ICH_DEMO_MODE=0 && python bootstrap.py --strict")
    print("   3) 登录：heritor01 / heritor02 / expert01 / admin01，口令均为 Passw0rd!")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
