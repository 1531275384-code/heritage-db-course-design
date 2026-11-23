"""
代码审查式静态检查：验证关键实现约束没有被写错。

为什么要有这个文件？
  评审方最关心的不是"功能跑没跑"，而是"关键约束真的生效了吗"：
    · AI 是否真的不会写 right_level？
    · 终审是否真的在单事务内完成？
    · 溯源日志是否真的追加式、不可改？
    · 权限是否真的落到数据库层而不是只有前端隐藏按钮？
  这些问题可以在一部分上通过源码约束检查验证，比"我说有就有"更可信。

  ⚠️ 这是**静态**检查，只能证明"代码里是这样写的"，
     不能证明"运行起来一定这样"。运行时验证请按 README 连真库执行。

运行：python -m tests.test_invariants
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PASS, FAIL = 0, 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  \033[92m✓\033[0m {name}")
    else:
        FAIL += 1
        print(f"  \033[91m✗\033[0m {name}" + (f"\n      → {detail}" if detail else ""))


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _strip_sql_comments(sql: str) -> str:
    """去掉 -- 行注释，否则列名比对会把注释文字当成列。"""
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def _view_columns(top_select: str) -> set[str]:
    """
    从一个视图的顶层 SELECT 列表里抽出**输出列名**。

    支持三种写法：
        a.asset_id                          → asset_id
        m.name AS motif_name                → motif_name
        (a.x IS NOT NULL AND ...) AS agree  → agree
    括号内的子查询/表达式里的别名不会被误抓（按顶层逗号切分）。
    """
    sql = _strip_sql_comments(top_select)
    cols: set[str] = set()
    depth, buf, items = 0, [], []
    for ch in sql:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    items.append("".join(buf))

    for item in items:
        item = " ".join(item.split())
        if not item:
            continue
        m = re.search(r"\bAS\s+([a-z_][a-z0-9_]*)\s*$", item, re.I)
        if m:
            cols.add(m.group(1))
            continue
        # 无别名的简单列引用：a.asset_id / title
        m = re.search(r"(?:^|[.\s])([a-z_][a-z0-9_]*)$", item, re.I)
        if m and "(" not in item:
            cols.add(m.group(1))
    return cols


def main() -> int:
    services = read("app/services.py")
    api = read("app/api.py")
    sql01 = read("sql/01_init.sql")
    sql02 = read("sql/02_index_trigger_view.sql")
    features = read("app/ai/features.py")
    classifier = read("app/ai/classifier.py")

    print("=" * 72)
    print(" 关键不变量 · 源码级审查")
    print("=" * 72)

    # ---------------------------------------------------------------- 1
    print("\n【1】AI 只写建议，绝不改 right_level")
    # 找出所有 UPDATE asset 语句，检查 SET 子句是否包含 right_level
    update_asset = re.findall(
        r"UPDATE\s+asset\s+SET\s+(.*?)\s+WHERE", services, re.I | re.S
    )
    check("services.py 中存在 UPDATE asset 语句", len(update_asset) > 0, f"找到 {len(update_asset)} 条")
    ai_update = [u for u in update_asset if "ai_suggest_level" in u]
    check("AI 分析路径的 UPDATE 不含 right_level",
          all("right_level" not in u for u in ai_update),
          f"违规语句: {ai_update}")
    # 终审路径必须含 right_level（且只有它含）
    with_level = [u for u in update_asset if "right_level" in u]
    check("只有终审路径会写 right_level", len(with_level) == 1, f"实际 {len(with_level)} 条含 right_level")

    check("classifier.py 不 import 任何数据库模块",
          "from app.db" not in classifier and "import psycopg2" not in classifier,
          "分级器应为纯函数，不得直接写库")

    # ---------------------------------------------------------------- 2
    print("\n【2】终审事务原子性")
    approve_body = re.search(
        r"def approve_asset\(.*?(?=\ndef |\Z)", services, re.S
    ).group(0)
    check("终审使用单事务上下文", approve_body.count("with open_tx(") == 1,
          f"发现 {approve_body.count('with open_tx(')} 个事务上下文（应为 1，否则跨事务无法原子）")
    check("使用 FOR UPDATE 悲观锁", "FOR UPDATE" in approve_body)
    check("锁粒度含 asset 表", re.search(r"FOR UPDATE OF a", approve_body) is not None)
    check("审核流水也加锁", re.search(r"audit_record[\s\S]{0,400}FOR UPDATE", approve_body) is not None)
    check("有幂等拦截（已终审则报错）", "已终审通过" in approve_body)
    check("校验 rowcount", "rowcount != 1" in approve_body)
    check("回读触发器日志作为证据", "db_audit_log_ids" in approve_body or "log_id" in approve_body)

    # ---------------------------------------------------------------- 3
    print("\n【3】数据库层权限（GRANT）")
    m = re.search(r"GRANT\s+UPDATE\s*\(([^)]*)\)\s*\n?\s*ON\s+asset\s+TO\s+ich_heritor", sql02, re.I)
    check("传承人有列级 UPDATE 授权", m is not None)
    if m:
        cols = [c.strip() for c in m.group(1).split(",")]
        check("传承人授权列不含 right_level", "right_level" not in cols, f"列清单: {cols}")
        check("传承人授权列含 ai_suggest_level", "ai_suggest_level" in cols)
    m2 = re.search(r"GRANT\s+UPDATE\s*\(([^)]*)\)\s*\n?\s*ON\s+asset\s+TO\s+ich_expert", sql02, re.I)
    check("专家有 right_level 列权限", m2 is not None and "right_level" in m2.group(1))
    check("撤销了审计日志的 UPDATE/DELETE",
          re.search(r"REVOKE\s+UPDATE,\s*DELETE[^;]*system_audit_log", sql02, re.I) is not None)

    # ---------------------------------------------------------------- 4
    print("\n【4】追加式日志 / 不可篡改")
    check("audit_record 禁止 DELETE 的触发器", "trg_audit_record_no_delete" in sql02)
    check("已终审记录禁止 UPDATE 的触发器", "trg_audit_record_guard" in sql02
          and "已终审" in sql02)
    check("system_audit_log 禁止 UPDATE/DELETE", "trg_syslog_immutable" in sql02)
    check("license_record 禁止 DELETE", "trg_license_no_delete" in sql02)
    check("触发器函数会抛异常阻止操作", "RAISE EXCEPTION" in sql02)

    # 回归防护：曾有 trg_audit_record_immutable（BEFORE UPDATE OR DELETE → fn_forbid_modify），
    # 它对**任何** UPDATE 无条件抛异常，会堵死专家终审（UPDATE audit_record SET state='approved'）。
    # 现在 audit_record 的不可篡改由 no_delete(禁删) + guard(禁改已终审) 分工实现。
    check("未在 audit_record 上挂无条件的 UPDATE 禁止触发器",
          re.search(r"CREATE\s+TRIGGER\s+trg_audit_record_immutable\b[^;]*?BEFORE\s+UPDATE", sql02, re.I) is None,
          "trg_audit_record_immutable 若存在且拦 UPDATE，终审链路会被堵死")
    # 反向确认 guard 只拦"已终审"，否则 pending → approved 的迁移会被拒
    guard = re.search(r"FUNCTION fn_audit_record_guard.*?LANGUAGE plpgsql;", sql02, re.S)
    check("audit_record_guard 仅拦截已终审记录（放行 pending → approved）",
          guard is not None and "OLD.state IN ('approved', 'rejected')" in guard.group(0))

    # 回归防护：曾被写过的"把旧 pending 伪造成 rejected + 硬塞 expert_id"的作废逻辑，
    # 会凭空产生一次不存在的专家驳回，污染 is_override 与专家工作量统计。
    # 判据：所有改 audit_record.state 的 UPDATE 都必须按 audit_id（唯一行）定位，
    #       不允许 WHERE asset_id = ... AND state = 'pending' 这种批量更新。
    #
    # ⚠️ 注意：本工程的 SQL 都写在 """...""" 里，所以**不能**整体删除三引号字符串块
    #    （会连 SQL 一起删掉，导致后续检查全部匹配不到）。只删 `#` 注释行即可，
    #    并额外跳过那些整行都是注释的伪代码（如说明文字里引用的反例）。
    services_code = re.sub(r"^\s*#.*$", "", services, flags=re.M)

    # 只匹配真正执行 SQL 的三引号块：UPDATE audit_record ... SET ... WHERE ...
    # 这样注释里引用的反例（没有 SET）不会被误判。
    #
    # 合法形态只有两种：
    #   (a) WHERE audit_id = %(aid)s                    ← approve
    #   (b) WHERE audit_id = (SELECT ... LIMIT 1 FOR UPDATE)  ← reject
    # 不合法的形态是顶层直接 WHERE asset_id = %s AND state = 'pending'。
    # 因此判据：取 SET 之后到 WHERE 之间的第一段顶层条件，检查它是否以 audit_id 开头。
    void_blocks = re.findall(
        r"UPDATE\s+audit_record\s+SET\b(.*?)(?=\"\"\"|$)", services_code, re.I | re.S
    )
    bulk_void = []
    for b in void_blocks:
        m = re.search(r"\bWHERE\b(.*)", b, re.I | re.S)
        top = m.group(1).lstrip() if m else ""
        if not re.match(r"audit_id\s*=", top, re.I):
            bulk_void.append(top[:80])
    check("audit_record 状态更新均按 audit_id 定位（不批量作废 pending）",
          len(void_blocks) >= 2 and not bulk_void,
          f"找到 {len(void_blocks)} 处 UPDATE audit_record SET...；"
          f"顶层条件不是 audit_id 的有 {len(bulk_void)} 处：{bulk_void}")

    # approve 与 reject 都必须走"最新一条 pending"的语义
    _latest_pending = re.compile(
        r"state\s*=\s*'pending'\s+ORDER\s+BY\s+created_at\s+DESC\s+LIMIT\s+1", re.I
    )
    check("approve 取最新一条 pending（ORDER BY created_at DESC LIMIT 1）",
          len(_latest_pending.findall(services_code)) >= 2,
          f"匹配到 {len(_latest_pending.findall(services_code))} 处，期望 >= 2（approve + reject）")

    # ---------------------------------------------------------------- 5
    print("\n【5】审计触发器覆盖权属/状态变更")
    trig = re.search(r"FUNCTION fn_asset_audit_trigger.*?LANGUAGE plpgsql;", sql02, re.S)
    check("触发器函数存在", trig is not None)
    if trig:
        body = trig.group(0)
        check("监控 right_level 变化", "NEW.right_level" in body and "OLD.right_level" in body)
        check("监控 audit_status 变化", "NEW.audit_status" in body and "OLD.audit_status" in body)
        check("写入新旧快照", "old_snapshot" in body and "new_snapshot" in body)
        check("只记录真实变更（IS DISTINCT FROM）", "IS DISTINCT FROM" in body)
    check("触发器挂载在 asset 表",
          re.search(r"AFTER\s+INSERT\s+OR\s+UPDATE\s+OR\s+DELETE\s+ON\s+asset", sql02, re.I) is not None)

    # ---------------------------------------------------------------- 6
    print("\n【6】数据库结构完整性")
    for tbl in ("app_user", "public_motif", "asset", "feature_vector",
                "audit_record", "license_record", "asset_genealogy", "system_audit_log"):
        check(f"表 {tbl} 已定义", re.search(rf"CREATE TABLE {tbl}\b", sql01) is not None)
    for view in ("v_asset_pending_audit", "v_asset_trace_chain", "v_asset_ledger"):
        check(f"视图 {view} 已定义", f"CREATE VIEW {view}" in sql02)
    check("向量列声明为 VECTOR(512)",
          len(re.findall(r"VECTOR\(512\)", sql01)) >= 2,
          f"出现 {len(re.findall(r'VECTOR\\(512\\)', sql01))} 次")
    check("HNSW 索引使用 cosine 距离",
          "hnsw (base_vector vector_cosine_ops)" in sql02)
    check("右值是 3 种角色的枚举", all(r in sql01 for r in ("'heritor'", "'expert'", "'admin'")))
    check("权属级别枚举取值正确",
          all(r in sql01 for r in ("'public'", "'derived'", "'original'")))

    # ---------------------------------------------------------------- 7
    print("\n【7】API 层鉴权")
    check("终审端点要求 expert/admin",
          re.search(r"def approve\([\s\S]{0,200}require_roles\(\"expert\",\s*\"admin\"\)", api) is not None)
    check("待审列表要求 expert/admin",
          re.search(r"def pending\([\s\S]{0,300}require_roles\(\"expert\",\s*\"admin\"\)", api) is not None)
    check("台账要求 expert/admin",
          re.search(r"def ledger\([\s\S]{0,200}require_roles\(\"expert\",\s*\"admin\"\)", api) is not None)
    check("上传要求 heritor/admin",
          re.search(r"def upload_asset\([\s\S]{0,600}require_roles\(\"heritor\",\s*\"admin\"\)", api) is not None)
    check("JWT 使用 compare_digest 防时序攻击",
          "compare_digest" in read("app/auth.py"))
    check("令牌过期校验", "已过期" in read("app/auth.py"))

    # ---------------------------------------------------------------- 8
    print("\n【8】向量一致性与可追溯")
    check("特征向量做 L2 归一化", "_l2_normalize" in features)
    check("记录模型版本（可追溯）", "model_version" in features and "model_version" in sql01)
    check("检索时设置 hnsw.ef_search", "hnsw.ef_search" in read("app/ai/similarity.py"))
    check("相似度 = 1 - 距离 且 clamp 到 [0,1]",
          "LEAST(1.0" in read("app/ai/similarity.py"))
    check("向量以参数绑定传入（非字符串拼接）",
          "%(q)s::vector" in read("app/ai/similarity.py"))

    # ---- 母题血缘（asset.motif_id）必须被分析流程回写 ----
    # 字段设计意图：判为公有母题/改编衍生时记录依据的母题，供 v_asset_ledger 的
    # motif_code/motif_name 与衍生作品统计使用。曾经两侧实现都没写，
    # 导致台账"母题血缘"列恒为空 —— 是跑通全流程才暴露的。
    check("01 定义了 motif_id（母题血缘）字段",
          "motif_id" in sql01 and "fk_asset_motif" in sql01)

    # 真实库：UPDATE asset 的 SET 子句里必须有 motif_id
    ai_upd = [u for u in re.findall(r"UPDATE\s+asset\s+SET\s+(.*?)\s+WHERE", services, re.I | re.S)
              if "ai_suggest_level" in u]
    check("services.py 分析流程回写 motif_id",
          any("motif_id" in u for u in ai_upd),
          "UPDATE asset 的 SET 子句里没有 motif_id，台账母题血缘会恒为空")

    # 演示模式：不走 SQL，直接改内存字典，检查给 a["motif_id"] 赋值
    check("demo.py 分析流程回写 motif_id",
          re.search(r'a\[\s*["\']motif_id["\']\s*\]\s*=', read("app/demo.py")) is not None,
          "demo 的 analyze_asset 没有给 asset 的 motif_id 赋值")

    # ---- 溯源节点序号必须连续 ----
    # 视图的 seq_no 是"节点类别"（root=1/audit=2/license=3/system_log=4），
    # 一条链上会有多个 system_log，直接输出就是 1→4→4→2→4→3。
    # 应用层必须按发生时间重编 1..N。
    for fname, src in (("services.py", services), ("demo.py", read("app/demo.py"))):
        check(f"{fname} 溯源节点序号按时间重排",
              re.search(r"enumerate\(\s*nodes\s*,\s*1\s*\)", src) is not None,
              "未找到 for i, n in enumerate(nodes, 1) 之类的时间序重编")

    # ---------------------------------------------------------------- 8.5
    print("\n【8.5】演示种子数据（data/seed_demo.json）")

    seed_path = ROOT / "data" / "seed_demo.json"
    check("种子文件存在", seed_path.exists(), str(seed_path))

    if seed_path.exists():
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
        demo_src = read("app/demo.py")

        # 账号：三个原始角色必须齐全，否则登录页的快捷入口点不进去
        usernames = {u["username"] for u in seed.get("users", [])}
        check("种子含三种角色的账号",
              {"heritor01", "expert01", "admin01"} <= usernames, f"实际={sorted(usernames)}")
        check("种子账号都带口令", all(u.get("password") for u in seed.get("users", [])))

        # 母题：展示页要的字段必须齐
        need_motif = {"motif_id", "motif_code", "name", "region", "description", "reference_img"}
        bad_motif = [m.get("motif_code") for m in seed.get("motifs", [])
                     if not need_motif <= set(m)]
        check("母题字段齐全", not bad_motif, f"缺字段={bad_motif}")

        # 作品：每件都必须能追溯到真实存在的账号与图片
        uid = {u["user_id"] for u in seed.get("users", [])}
        mid = {m["motif_id"] for m in seed.get("motifs", [])}
        bad_owner = [a["asset_id"] for a in seed.get("assets", []) if a["owner_id"] not in uid]
        bad_motif_fk = [a["asset_id"] for a in seed.get("assets", [])
                        if a.get("motif_id") is not None and a["motif_id"] not in mid]
        check("作品 owner_id 都指向存在的账号", not bad_owner, f"孤立={bad_owner}")
        check("作品 motif_id 都指向存在的母题", not bad_motif_fk, f"孤立={bad_motif_fk}")

        upload_dir = ROOT / "data" / "uploads"
        missing_img = [a["file_path"] for a in seed.get("assets", [])
                       if not (upload_dir / a["file_path"]).exists()]
        check("作品图片文件都存在", not missing_img, f"缺失={missing_img}")

        # ★ 语义不变量：AI 不写 right_level
        # 种子里每件作品的 right_level 都必须有对应的终审记录；
        # 只有 pending / draft 态允许 right_level 是初始值。
        bad_level = []
        for a in seed.get("assets", []):
            if a["audit_status"] in ("approved",) and a.get("right_level"):
                decided = [x for x in (a.get("audits") or [])
                           if x["state"] == "approved" and x.get("expert_id")]
                if not decided:
                    bad_level.append(f"#{a['asset_id']} 已确权但无专家终审记录")
        check("已确权作品的 right_level 均由专家终审产生", not bad_level,
              "\n      → ".join(bad_level))

        # ★ 相似度与置信度不能混用
        # similarity 是余弦相似度，ai_confidence 是判定把握，量纲完全不同。
        # 曾把 confidence 当 similarity 写进 similarity_json，导致母题关联页数值全错。
        mixed = []
        for a in seed.get("assets", []):
            for x in a.get("audits") or []:
                if x.get("similarity") is not None and x.get("ai_confidence") is not None:
                    if abs(float(x["similarity"]) - float(x["ai_confidence"])) < 1e-9:
                        mixed.append(f"#{a['asset_id']} similarity == ai_confidence ({x['similarity']})")
        check("审核流水里 similarity 不是 ai_confidence 的复制", not mixed,
              "\n      → ".join(mixed))

        # 相似度必须与判定一致（>=0.92 才可能是 public，<0.80 才可能是 original）
        thr_pub = float(re.search(r"SIM_PUBLIC_THRESHOLD[^=]*=\s*float\([^)]*?(\d\.\d+)",
                                  read("app/config.py")).group(1))
        thr_dev = float(re.search(r"SIM_DERIVED_THRESHOLD[^=]*=\s*float\([^)]*?(\d\.\d+)",
                                  read("app/config.py")).group(1))
        inconsistent = []
        for a in seed.get("assets", []):
            lvl, sim = a.get("ai_suggest_level"), None
            for x in a.get("audits") or []:
                if x.get("similarity") is not None:
                    sim = float(x["similarity"])
                    break
            if lvl is None or sim is None:
                continue
            if lvl == "public" and sim < thr_pub:
                inconsistent.append(f"#{a['asset_id']} public 但 sim={sim}")
            if lvl == "derived" and not (thr_dev <= sim < thr_pub):
                inconsistent.append(f"#{a['asset_id']} derived 但 sim={sim}")
            if lvl == "original" and sim >= thr_dev:
                inconsistent.append(f"#{a['asset_id']} original 但 sim={sim}")
        check("AI 建议与相似度阈值自洽", not inconsistent, "\n      → ".join(inconsistent))

        # 演示模式必须从 JSON 读，而不是继续写死
        check("demo.py 从 seed_demo.json 载入种子",
              "seed_demo.json" in demo_src and "def load_seed" in demo_src,
              "demo.reset() 仍在用写死的 SEED_USERS / SEED_MOTIFS")
        check("demo.py 启动时预热作品向量",
              "def warm_asset_vectors" in demo_src,
              "种子作品没有特征向量，相似资产比对与查重会漏掉它们")
        check("演示种子可用 ICH_DEMO_SEED=off 关闭",
              "ICH_DEMO_SEED" in demo_src)
        check("种子初始化脚本存在（灌真库）",
              (ROOT / "scripts" / "init_seed.py").exists())
        check("种子一致性校验脚本存在",
              (ROOT / "scripts" / "check_seed.py").exists())

    # ---------------------------------------------------------------- 8.6
    print("\n【8.6】台账视图新增列与 Pydantic 模型同步")
    # Pydantic 模型默认丢弃未声明的字段。视图加了列但 schema 没加，
    # 接口返回里就没有这个键，前端只能拿到 undefined —— 而这是**静默失败**，
    # 类型检查、单元测试都发现不了，只有渲染出来才看得见。
    sql02 = read("sql/02_index_trigger_view.sql")
    ledger_view = sql02[sql02.index("CREATE VIEW v_asset_ledger"):]
    ledger_view = ledger_view[: ledger_view.index("COMMENT ON VIEW")]
    # 只取顶层 SELECT 列表（到首个 FROM 为止），否则会把 LATERAL 里的别名
    # （sim / active_cnt / total_fee）也当成视图的列
    top_select = ledger_view[ledger_view.index("SELECT") + 6: ledger_view.index("\nFROM ")]
    view_cols = _view_columns(top_select)

    schemas_src = read("app/schemas.py")
    ledger_row = schemas_src[schemas_src.index("class LedgerRow"):]
    ledger_row = ledger_row[: ledger_row.index("class ", 10)]
    # 去掉注释行，否则紧跟在注释后面的第一个字段会被正则漏掉
    ledger_row = "\n".join(
        ln for ln in ledger_row.splitlines() if not ln.strip().startswith("#")
    )
    # 字段名可以含数字（如 top1_similarity），正则必须用 [a-z0-9_]+
    model_fields = set(re.findall(r"^\s{4}([a-z_][a-z0-9_]*)\s*:", ledger_row, re.M))
    lack = sorted(c for c in view_cols if c not in model_fields)
    check("台账视图所有列都在 LedgerRow 里声明", not lack, f"视图有而模型缺={lack}")

    # demo 的 get_ledger 也必须返回同一列（否则切库时字段消失）
    demo_ledger = demo_src[demo_src.index("def get_ledger"):]
    demo_ledger = demo_ledger[: demo_ledger.index("\ndef ")]
    miss_demo = [c for c in view_cols if f'"{c}"' not in demo_ledger]
    check("演示模式台账覆盖视图全部列", not miss_demo, f"演示缺={miss_demo}")

    # ---------------------------------------------------------------- 9
    print("\n【9】前端安全性")
    js = read("app/static/app.js")
    check("定义了 HTML 转义函数", "function esc(" in js)

    # 精确检查：所有**用户可输入自由文本**的字段，在插入 HTML 时必须经过 esc()
    # 这些字段可能包含 < > & " ' ，不转义就是存储型 XSS
    user_text_fields = [
        "a.title", "r.title", "item.title", "t.title", "h.name", "n.summary",
        "n.actor_name", "r.owner_name", "item.owner_name", "u.display_name",
        "e.message", "an.reason", "r.top1_motif_name", "n.detail",
        "r.owner_org", "u.org_name", "n.actor_role",
    ]
    unescaped = []
    for field in user_text_fields:
        # 匹配 ${field} 或 ${expr(field)}，但排除被 esc( 包裹的
        for m in re.finditer(r"\$\{([^{}]*?" + re.escape(field) + r"[^{}]*?)\}", js):
            expr = m.group(1).strip()
            if not expr.startswith("esc("):
                unescaped.append(f"{field} 出现在 ${{{expr[:50]}}}")
    # 允许 json.dumps 结果再 esc 的形式（n.detail 已经 esc(JSON.stringify(...)) ）
    unescaped = [u for u in unescaped if "esc(JSON.stringify(n.detail" not in u]
    check("用户可控文本均经 esc() 转义", len(unescaped) == 0,
          "\n      → ".join(unescaped[:6]))

    check("401 自动登出", "logout(true)" in js)
    check("前端不自存口令", "localStorage.setItem('ich_pass" not in js)
    check("令牌只存 localStorage 不写 cookie（避免 CSRF）",
          "document.cookie" not in js)

    # 汇总统计字段必须兜底：后端 GROUP BY 只返回存在的分组，
    # 缺失的键取出来是 undefined，界面上会显示成 "undefined 件"。
    # 这在"刚部署、还没有已确权资产"时必然出现 —— 是首屏就撞见的问题。
    bad_stats = []
    for i, line in enumerate(js.splitlines(), 1):
        s = line.strip()
        if s.startswith("//") or s.startswith("*"):
            continue
        # 形如 ${sum.by_level.public} / ${sum.by_status.approved} 的直接取值
        if re.search(r"\$\{sum\.(by_level|by_status)\.[a-z]+\}", s):
            bad_stats.append(f"app.js:{i}  {s[:60]}")
    check("汇总统计字段有 undefined 兜底", not bad_stats,
          "\n      → ".join(bad_stats[:4]))
    # nz() 已提升为模块级工具函数（原先在 loadPending/renderLedgerStats 里各写一份），
    # 两处局部定义会遮蔽模块级版本，导致 nz(x, fallback) 的 fallback 失效。
    check("nz() 为模块级唯一实现", js.count("function nz(") == 1 and js.count("const nz = ") == 0,
          f"function nz 定义 {js.count('function nz(')} 处，"
          f"局部 const nz 残留 {js.count('const nz = ')} 处")

    # ---- 演示模式与真实库的返回字段必须逐列一致 ----
    # 曾经 demo.list_my_assets 漏返回 version_no，前端「我的作品」渲染出 "vundefined"。
    # 这类漂移只在切库时暴露，靠静态比对守住。
    demo_src = read("app/demo.py")

    svc_mine = services[services.index("def list_my_assets"):]
    svc_mine = svc_mine[: svc_mine.index("def ", 10) if svc_mine.find("def ", 10) > 0 else len(svc_mine)]
    mine_sql = svc_mine[svc_mine.index("SELECT") + 6: svc_mine.index("\n            FROM")]
    sql_cols = sorted(_view_columns(mine_sql))

    demo_mine = demo_src[demo_src.index("def list_my_assets"):]
    nxt = demo_mine.find("\ndef ", 10)
    demo_mine = demo_mine[: nxt if nxt > 0 else len(demo_mine)]
    demo_cols = re.findall(r'"([a-z_]+)":', demo_mine)

    missing = [c for c in sql_cols if c not in demo_cols]
    check("我的作品：演示模式覆盖真实库的全部列",
          len(sql_cols) > 0 and not missing,
          f"真实库列={sql_cols}；演示模式缺={missing}")

    # 前端引用的字段也必须在真实库 SELECT 里存在
    mine_render = js[js.index("async function loadMine"):]
    mine_render = mine_render[: mine_render.index("async function", 10)]
    used = set(re.findall(r"a\.([a-z_]+)", mine_render))
    orphan = sorted(used - set(sql_cols))
    check("我的作品：前端引用的字段后端都有返回", not orphan, f"缺字段={orphan}")

    print("\n" + "=" * 72)
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    if FAIL:
        print("\033[91m存在不满足的约束，请修复后再交付\033[0m")
        return 1
    print("\033[92m所有关键不变量均已满足（静态层面）\033[0m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
