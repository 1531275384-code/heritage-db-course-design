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
# 直接 `python scripts/init_seed.py` 时 __package__ 为空，`from app.db import ...` 会失败。
# 补上仓库根，让两种调用方式（-m 与直接跑）都成立。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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

    # ------------------------------------------------------------------ 账号
    # 03_seed.sql 只插了 3 个账号（heritor01 / expert01 / admin01），
    # 而 seed_demo.json 有 4 个 —— 缺的 heritor02 名下挂着第 6 件作品，
    # 不补就会在插作品时撞 fk_asset_owner。这里以 JSON 为准把账号补齐。
    print("\n[1/5] 账号")
    with open_tx() as cur:
        for u in seed["users"]:
            cur.execute(
                """
                INSERT INTO app_user (user_id, username, display_name, role, password_hash, org_name)
                VALUES (%s, %s, %s, %s, crypt(%s, gen_salt('bf', 10)), %s)
                ON CONFLICT (username) DO UPDATE
                   SET display_name = EXCLUDED.display_name,
                       role         = EXCLUDED.role,
                       org_name     = EXCLUDED.org_name
                """,
                (u["user_id"], u["username"], u["display_name"], u["role"],
                 u.get("password", "Passw0rd!"), u.get("org_name")),
            )
            print(f"  {OK} {u['username']:<10} {u['display_name']}  [{u['role']}]")

        # 显式写 user_id / asset_id 不会推进序列。不校准的话，
        # 应用里新建的第一个账号或作品会拿到 1，直接撞主键。
        cur.execute(
            "SELECT setval(pg_get_serial_sequence('app_user','user_id'),"
            " COALESCE((SELECT max(user_id) FROM app_user), 1))")
        cur.execute(
            "SELECT setval(pg_get_serial_sequence('asset','asset_id'),"
            " COALESCE((SELECT max(asset_id) FROM asset), 1))")

    # ------------------------------------------------------------------ 母题
    print("\n[2/5] 母题")
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
    print("\n[3/5] 作品")

    if not args.keep:
        # 清空旧演示作品。这里踩过三个坑，都写下来免得下次重犯：
        #
        # 1) 不能用 open_tx(app_role="admin")：ich_admin 故意没有 asset 的 DELETE 权限
        #    （资产不可随意删除，这是设计而非疏漏）。初始化是运维动作，走超级用户。
        # 2) 不能只 DELETE asset：audit_record / license_record 的外键是
        #    ON DELETE RESTRICT，不是 CASCADE。原注释写的"含级联"从来不成立，
        #    有审核或授权记录的作品根本删不掉。必须先删子表。
        # 3) 这两张子表还挂着「禁止 DELETE」的触发器，且不区分角色，
        #    连超级用户也拦。只能在本会话临时停用触发器，与 tests/check_pg.py
        #    清理测试数据用的是同一手法。SET LOCAL 随事务结束自动失效。
        with open_tx() as cur:
            cur.execute("SET LOCAL session_replication_role = replica")
            cur.execute("DELETE FROM license_record WHERE asset_id >= %s", (DEMO_ASSET_BASE,))
            n_lic = cur.rowcount
            cur.execute("DELETE FROM audit_record WHERE asset_id >= %s", (DEMO_ASSET_BASE,))
            n_aud = cur.rowcount
            cur.execute(
                "DELETE FROM asset_genealogy"
                " WHERE parent_asset_id >= %s OR child_asset_id >= %s",
                (DEMO_ASSET_BASE, DEMO_ASSET_BASE))
            cur.execute("DELETE FROM asset WHERE asset_id >= %s", (DEMO_ASSET_BASE,))
            n_asset = cur.rowcount
            # feature_vector 的外键是 ON DELETE CASCADE，随 asset 一起走，无需手删
        if n_asset:
            print(f"  {WARN} 已清空 {n_asset} 条旧演示作品"
                  f"（连同 {n_aud} 条审核、{n_lic} 条授权）")

    # 用超级用户而不是 app_role="admin"：这段要写 right_level（种子自带终审结论），
    # 而 ich_admin 对 asset 只有 SELECT；ich_heritor 虽能 INSERT，但没有 right_level 的
    # UPDATE 列权限 —— 那正是「AI 不能改权属」的列级保护，不该为了灌数据去放宽。
    # 初始化属于运维动作，不走应用运行路径，用连接账号本身的权限是对的。
    with open_tx() as cur:
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
    # 审核流水同样走超级用户：种子里的审核记录包含 expert_level 与 decides 时间，
    # 模拟的是「历史上已经发生过的终审」，不是当场由专家角色操作，用业务角色反而失真。
    print("\n[4/5] 审核流水")
    with open_tx() as cur:
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
    print("\n[5/5] 授权记录")
    with open_tx() as cur:
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
