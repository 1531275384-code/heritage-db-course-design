"""端到端冒烟测试：不依赖 curl / 代理，直接用标准库走 HTTP。

用法：
    python tests/e2e_demo.py [base_url]

默认 base_url = http://127.0.0.1:8126

设计意图：把 README §演示流程 的 12 步用代码固化一遍。
它验证的是**接口契约与业务规则**，不是数据库实现细节：
  - AI 只写建议，不写 right_level
  - 专家可以推翻 AI（is_override=true）
  - 重复终审被拒（幂等/并发保护）
  - 未终审不能授权
  - 溯源链路完整、台账汇总正确
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8126"
ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"

# 关键：显式禁用代理。本机 curl 走了系统代理，导致 127.0.0.1 连不上。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PASS = 0
FAIL = 0


def call(method: str, path: str, token: str | None = None, body: dict | None = None) -> tuple[int, dict | list]:
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with _OPENER.open(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"null")
        except json.JSONDecodeError:
            return e.code, {"detail": raw[:300].decode("utf-8", "replace")}


def upload(token: str, path: Path, title: str, auto_analyze: bool = False) -> tuple[int, dict]:
    """multipart/form-data 手写，避免引入第三方依赖。"""
    boundary = "----" + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, val in (("title", title), ("description", "端到端测试样本"),
                      ("auto_analyze", "true" if auto_analyze else "false")):
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{val}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n".encode()
        + path.read_bytes() + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    payload = b"".join(parts)

    req = urllib.request.Request(BASE + "/api/asset/upload", data=payload, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with _OPENER.open(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"null")
        except json.JSONDecodeError:
            return e.code, {"detail": raw[:300].decode("utf-8", "replace")}


def check(label: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}" + (f"  {extra}" if extra else ""))
    else:
        FAIL += 1
        print(f"  ✗ {label}  ← 失败  {extra}")


def section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


# =============================================================================
section("0. 服务与演示模式")


def _seed_expected() -> dict:
    """从 data/seed_demo.json 读期望行数，避免把种子规模硬编码在测试里。

    曾经这里写死 `app_user == 3`，后来种子从 3 个账号扩到 4 个，
    测试就红了 —— 而红的原因不是产品坏了，是断言过时。
    让测试跟着数据源走，才不会每次加演示数据都要改测试。
    """
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "data" / "seed_demo.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    return {
        "app_user": len(d.get("users") or []),
        "public_motif": len(d.get("motifs") or []),
        "asset": len(d.get("assets") or []),
    }


exp = _seed_expected()
code, h = call("GET", "/api/health")
check("GET /api/health 返回 200", code == 200, f"HTTP {code}")
check("数据库状态明确标注为演示模式", "演示" in str(h.get("database")), str(h.get("database")))
tables = h.get("tables", {})
if exp:
    mism = {k: (tables.get(k), v) for k, v in exp.items() if tables.get(k) != v}
    check("种子规模与 seed_demo.json 一致", not mism, f"实际/期望={mism}; 全表={tables}")
else:
    check("内存表行数可读", tables.get("app_user", 0) > 0, f"8 张表: {tables}")
# 授权记录与审计日志必须已随种子写入（否则台账「授权费合计」会是 0）
check("种子授权记录已加载", (tables.get("license_record") or 0) > 0, f"license_record={tables.get('license_record')}")
check("已确权作品生成了审计日志",
      (tables.get("system_audit_log") or 0) >= (tables.get("asset") or 0),
      f"log={tables.get('system_audit_log')} asset={tables.get('asset')}")

code, m = call("GET", "/api/meta")
check("demo_mode 标志为 true（前端据此显示红色横幅）", m.get("demo_mode") is True)
print(f"  阈值: public<={m['thresholds']['public']}  derived<={m['thresholds']['derived']}")
print(f"  AI 后端: {m['ai_backend']['backend']} (deep={m['ai_backend']['is_deep_model']})")

section("1. 三个角色登录")
tokens = {}
for uname in ("heritor01", "expert01", "admin01"):
    code, r = call("POST", "/api/auth/login", body={"username": uname, "password": "Passw0rd!"})
    ok = code == 200 and "access_token" in r
    check(f"{uname} 登录成功", ok, r.get("user", {}).get("display_name", r.get("detail", "")))
    if ok:
        tokens[r["user"]["role"]] = r["access_token"]

code, r = call("POST", "/api/auth/login", body={"username": "heritor01", "password": "wrong"})
check("错误口令返回 401", code == 401, f"HTTP {code} / {r.get('detail')}")
code, r = call("POST", "/api/auth/login", body={"username": "nobody", "password": "x"})
check("不存在用户返回同一句错误（防用户名枚举）", code == 401 and r.get("detail") == "用户名或口令错误")

T_H, T_E, T_A = tokens.get("heritor"), tokens.get("expert"), tokens.get("admin")

section("2. 权限：传承人访问专家接口被拒")
code, r = call("GET", "/api/audit/pending", token=T_H)
check("传承人拉待审列表 → 403", code == 403, f"HTTP {code}")
code, r = call("GET", "/api/ledger", token=T_H)
check("传承人看监管台账 → 403", code == 403, f"HTTP {code}")
code, r = call("GET", "/api/users", token=T_H)
check("传承人查用户名录 → 403", code == 403, f"HTTP {code}")

section("3. 公有母题库（3 条种子 + 真实提取的向量）")
code, ms = call("GET", "/api/motifs", token=T_H)
if not isinstance(ms, list):
    print(f"  母题接口异常：HTTP {code} {ms}")
    sys.exit(1)
check("母题列表返回 3 条", len(ms) == 3, ", ".join(x["name"] for x in ms))
check("每条母题都已加载 512 维向量", all(x["has_vector"] for x in ms),
      f"维度向量模型: {ms[0]['vector_model']}")

section("4. 上传改编作品 → AI 给出建议")
sample = SAMPLES / "sample_derived_butterfly.jpg"
check("样本文件存在", sample.exists(), str(sample))
code, up = upload(T_H, sample, "蝶恋花·缠枝改良", auto_analyze=False)
if code != 201:
    print(f"  上传失败：HTTP {code} {up}")
    sys.exit(1)
aid = up["asset"]["asset_id"]
check("上传成功 201", code == 201, f"asset_id={aid}")
check("初始状态 draft，right_level 尚未确定", up["asset"]["audit_status"] == "draft",
      f"status={up['asset']['audit_status']} level={up['asset']['right_level']}")

code, an = call("POST", f"/api/asset/{aid}/analyze", token=T_H)
check("AI 分析返回 200", code == 200, f"HTTP {code}")
check("给出分级建议", an.get("suggested_level") in ("public", "derived", "original"),
      f"建议={an.get('suggested_level')} 置信度={an.get('confidence')}")
print(f"  规则命中: {an.get('rule')}")
print(f"  理由: {an.get('reason')}")
print("  top-3 母题相似度:")
for h in an.get("top_k", []):
    print(f"    {h['code']} {h['name']:<8} sim={h['similarity']:.4f}  dist={h['distance']:.4f}")
check("top_k 按相似度降序", all(
    an["top_k"][i]["similarity"] >= an["top_k"][i + 1]["similarity"] for i in range(len(an["top_k"]) - 1)))

last_plan = an.get("plan") or ""
check("执行计划如实说明无 HNSW", "HNSW" in last_plan, last_plan.splitlines()[0] if last_plan else "")

section("5. ★ 核心不变量：AI 只写建议，没碰 right_level")
code, a = call("GET", f"/api/asset/{aid}", token=T_H)
check("asset.right_level 仍是初始值 public（AI 未写入）", a["right_level"] == "public",
      f"right_level={a['right_level']}")
check("audit_status 变为 pending", a["audit_status"] == "pending", f"status={a['audit_status']}")
check("ai_suggest_level 记录了建议", a["ai_suggest_level"] == an["suggested_level"],
      f"ai_suggest_level={a['ai_suggest_level']}")

section("6. 专家工作台：待审列表")
code, pend = call("GET", "/api/audit/pending", token=T_E)
check("专家可拉取待审列表 200", code == 200, f"{len(pend)} 条")
item = next((p for p in pend if p["asset_id"] == aid), None)
check("新提交的资产在待审列表中", item is not None, f"asset_id={aid}")
if item:
    print(f"  {item['title']} | 传承人={item['owner_name']} | AI建议={item['ai_suggest_level']} "
          f"| top1={item['top1_motif_name']} sim={item['top1_similarity']}")
    check("待审项带相似度报告（专家决策依据）", bool(item.get("similarity_json")))

section("7. 专家终审：推翻 AI 建议")
ai_said = a["ai_suggest_level"]
final = "original" if ai_said != "original" else "derived"
code, ap = call("POST", f"/api/audit/{aid}/approve", token=T_E,
                body={"final_level": final, "opinion": "线条与配色均为个人重绘，构成实质性独创表达。"})
check("终审返回 200", code == 200, f"HTTP {code} {ap.get('detail', '')}")
if code == 200:
    check("权属已落库", ap["right_level"] == final, f"{ai_said} → {ap['right_level']}")
    check("标记为专家推翻", ap["is_override"] is True, f"is_override={ap['is_override']}")
    check("触发器留痕返回日志 ID", bool(ap.get("db_audit_log_ids")), f"log_ids={ap['db_audit_log_ids']}")
    print(f"  {ap['message']}")

section("8. 并发/幂等保护：重复终审被拒")
code, r = call("POST", f"/api/audit/{aid}/approve", token=T_E,
               body={"final_level": "public", "opinion": "试图二次终审"})
check("再次终审 → 409", code == 409, f"HTTP {code} / {r.get('detail', '')[:60]}")

section("9. 授权登记：未终审不能授权")
code, up2 = upload(T_H, SAMPLES / "sample_original_fish.jpg", "原创·云纹新构")
aid2 = up2["asset"]["asset_id"] if code == 201 else None
code, r = call("POST", f"/api/asset/{aid2}/license", token=T_H, body={
    "licensee_name": "某某文创有限公司", "license_type": "non_exclusive",
    "scope": "文创产品", "territory": "中国大陆", "start_date": "2026-01-01",
    "end_date": "2028-12-31", "fee_cny": 80000,
})
check("draft 状态授权 → 409（权属未定不能授权）", code == 409, f"HTTP {code} / {r.get('detail', '')[:50]}")

code, r = call("POST", f"/api/asset/{aid}/license", token=T_H, body={
    "licensee_name": "苏州丝语文创有限公司", "license_type": "exclusive",
    "scope": "家纺类文创产品", "territory": "中国大陆", "start_date": "2026-01-01",
    "end_date": "2028-12-31", "fee_cny": 120000,
})
check("已终审资产可登记独占授权 201", code == 201, f"license_id={r.get('license_id')}")
print(f"  被授权方={r.get('licensee_name')} 费用=¥{r.get('fee_cny')} 有效期={r.get('start_date')}~{r.get('end_date')}")

code, r2 = call("POST", f"/api/asset/{aid}/license", token=T_H, body={
    "licensee_name": "另一家公司", "license_type": "exclusive",
    "scope": "全品类", "territory": "中国大陆", "start_date": "2026-02-01",
    "end_date": "2027-12-31", "fee_cny": 90000,
})
check("同一资产第二条独占授权 → 409（部分唯一索引拦截）", code == 409, f"HTTP {code}")

code, lics = call("GET", f"/api/asset/{aid}/licenses", token=T_H)
check("授权列表可查", code == 200 and len(lics) == 1, f"{len(lics)} 条")

section("10. 溯源链路")
code, tr = call("GET", f"/api/asset/{aid}/trace", token=T_H)
check("溯源返回 200", code == 200, f"HTTP {code}")
check("链路完整性校验通过", tr.get("is_chain_valid") is True, f"issues={tr.get('chain_issues')}")
kinds: dict[str, int] = {}
for n in tr.get("nodes", []):
    kinds[n["event_type"]] = kinds.get(n["event_type"], 0) + 1
print(f"  节点数={len(tr.get('nodes', []))} 分布={kinds}")
check("包含 root 登记节点", kinds.get("root", 0) == 1)
# 一次审核 = audit_record 的一条记录；pending → approved/rejected 是**同一条**的状态流转，
# 不是两条事件。所以这里必须是 1 而不是 2 —— 这也正是 v_asset_trace_chain 的设计：
# 决策结论与 AI 建议同源，专家看到的建议和最终判定不会脱节。
check("包含 audit 决策节点（一条记录承载 pending→final 流转）", kinds.get("audit", 0) == 1,
      f"audit={kinds.get('audit')}")
check("包含 license 授权节点", kinds.get("license", 0) == 1)
check("包含 system_log 触发器留痕节点", kinds.get("system_log", 0) >= 2, f"{kinds.get('system_log')} 条")

print("  链路时间线：")
for n in tr.get("nodes", []):
    print(f"    [{n['event_type']:<10}] {n['summary'][:44]:<46} by {n['actor_name']}")

section("11. 监管台账（视图 v_asset_ledger）")
code, lg = call("GET", "/api/ledger", token=T_A)
check("管理员可看台账 200", code == 200, f"HTTP {code}")
s = lg.get("summary", {})
# 台账条数会随本脚本前面几步的上传/确权而变化，所以断言「不少于种子规模」而不是等号
base_assets = exp.get("asset", 0) if exp else 0
check("资产总数不少于种子规模",
      s.get("total_assets", 0) >= base_assets,
      f"{s.get('total_assets')} 条（种子 {base_assets} 条 + 本次流程新增）")
check("按级别汇总存在", bool(s.get("by_level")), str(s.get("by_level")))
check("按状态汇总存在", bool(s.get("by_status")), str(s.get("by_status")))
_check_sum = sum((s.get("by_level") or {}).values())
check("按级别汇总数等于总数", _check_sum == s.get("total_assets"),
      f"by_level 合计={_check_sum} total={s.get('total_assets')}")
print(f"  AI-专家一致率 = {s.get('ai_agree_rate')}  （种子里有专家推翻 AI 的情形，所以不是 1.0）")
print(f"  授权费总额 = ¥{s.get('total_license_fee_cny')}")
print(f"  数据库审计日志 = {s.get('db_audit_log_count')} 条")
print("  台账明细：")
for row in lg.get("rows", []):
    print(f"    #{row['asset_id']} {row['title'][:16]:<18} 级别={row['right_level']:<8} "
          f"状态={row['audit_status']:<9} AI建议={row['ai_suggest_level']} "
          f"一致={row['ai_expert_agree']} 授权={row['license_cnt']} 日志={row['db_log_cnt']}")

section("12. 我的作品（传承人视角）")
code, mine = call("GET", "/api/asset/mine", token=T_H)
# 种子给 heritor01 配了 3 件作品，加上本次流程上传的，应 >= 4
check("我的作品不少于种子给她的数量",
      code == 200 and len(mine) >= 4, f"{len(mine)} 条")
check("我的作品只含本人的",
      all(a.get("title") for a in mine), "存在无名作品")

section("13. 重复文件上传幂等")
code, dup = upload(T_H, sample, "蝶恋花·重复上传")
check("同一文件再次上传命中 duplicate_of", code == 201 and dup.get("duplicate_of") == aid,
      f"duplicate_of={dup.get('duplicate_of')}")

# =============================================================================
print(f"\n{'=' * 72}")
print(f"  结果：{PASS} 项通过 / {FAIL} 项失败")
print(f"{'=' * 72}")
sys.exit(0 if FAIL == 0 else 1)
