"""用现有作品样本（data/samples）+ 种子母题（data/motifs）实际跑一次完整业务流程。

覆盖：三角色登录 → 上传 → AI 特征提取与向量检索 → 分级建议 → 待审队列
      → 专家终审（含推翻 AI）→ 授权登记 → 溯源链路 → 监管台账 → 越权/幂等反向验证

运行：.venv/Scripts/python.exe docs/verify/run_full_flow.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["ICH_DEMO_MODE"] = "1"
os.environ.setdefault("ICH_LOG_LEVEL", "WARNING")

HOST, PORT = "127.0.0.1", 18800
BASE = f"http://{HOST}:{PORT}"
SAMPLES = ROOT / "data" / "samples"

_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 记录所有请求，最后做一次异常清单
CALLS: list[dict] = []
NOTES: list[dict] = []


def hr(t: str) -> None:
    print(f"\n{'=' * 78}\n  {t}\n{'=' * 78}")


def req(method, path, token=None, body=None, raw=None, ctype="application/json"):
    url = BASE + path
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", ctype)
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    t0 = time.time()
    try:
        with _op.open(r, timeout=60) as resp:
            st, payload = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        st, payload = e.code, e.read()
    dt = (time.time() - t0) * 1000
    CALLS.append({"m": method, "p": path, "st": st, "ms": dt})
    return st, payload, dt


def jload(b):
    try:
        return json.loads(b)
    except Exception:
        return None


def note(level: str, what: str, detail: str) -> None:
    """level: 设计内 / 缺陷 / 环境限制"""
    NOTES.append({"level": level, "what": what, "detail": detail})


def upload(token, fname: str, title: str, desc: str):
    path = SAMPLES / fname
    content = path.read_bytes()
    bnd = "----ichb" + str(int(time.time() * 1000))
    parts = []
    for k, v in (("title", title), ("description", desc)):
        parts.append(
            f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    suffix = path.suffix.lower()
    parts.append(
        f"--{bnd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{fname}\"\r\n"
        f"Content-Type: image/jpeg\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{bnd}--\r\n".encode())
    return req("POST", "/api/asset/upload", token, raw=b"".join(parts),
               ctype=f"multipart/form-data; boundary={bnd}")


def main() -> int:
    import uvicorn
    from app.main import app
    from app.ai.features import current_backend_info
    from app.config import settings

    cfg = uvicorn.Config(app, host=HOST, port=PORT, log_level="error", access_log=False)
    srv = uvicorn.Server(cfg)
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(150):
        if srv.started:
            break
        time.sleep(0.1)

    # ==================================================================== 0
    hr("0  运行环境与使用的数据")
    print(f"服务地址        {BASE}（进程内启动，演示模式）")
    print(f"数据库          ？见下方 /api/health")
    st, b, _ = req("GET", "/api/health")
    h = jload(b) or {}
    print(f"存储模式        {h.get('database')}")
    print(f"8 张表行数      {h.get('tables')}")
    ai = current_backend_info()
    print(f"AI 特征后端      {ai['backend']}（深度模型={ai['is_deep_model']}，维度={ai.get('embed_dim')}）")
    print(f"分级阈值        public≥{settings.SIM_PUBLIC_THRESHOLD}  derived≥{settings.SIM_DERIVED_THRESHOLD}")
    print(f"检索 top-K      {settings.TOP_K}")
    if not ai["is_deep_model"]:
        note("设计内", "AI 后端降级到 builtin",
             "未安装 torch，自动降级为 HOG+HSV+Laplacian 手工特征。维度仍为 512，无需 GPU，属设计内方案。")

    st, b, _ = req("GET", "/api/motifs", login_token := "")
    # 母题需要登录，先登录再查
    print("\n【输入数据清单】")
    print(f"{'类别':<8}{'文件':<38}{'用途'}")
    print("-" * 78)
    for f in ("sample_derived_butterfly.jpg", "sample_original_fish.jpg"):
        print(f"{'作品样本':<8}{f:<38}待上传，参与分级判定")
    for f in ("motif_butterfly.jpg", "motif_lotus.jpg", "motif_huiwen.jpg"):
        print(f"{'母题参考':<8}{f:<38}已作为公有母题预载入向量库")

    # ==================================================================== 1
    hr("1  三角色登录（sql/03_seed.sql 同名种子账号）")
    toks = {}
    for un in ("heritor01", "expert01", "admin01"):
        st, b, ms = req("POST", "/api/auth/login",
                        body={"username": un, "password": "Passw0rd!"})
        d = jload(b) or {}
        if st == 200:
            toks[un] = d["access_token"]
            u = d["user"]
            print(f"  ✓ {un:<11} {st}  {ms:5.0f}ms  {u['display_name']} / {u['role']:<8} {u.get('org_name')}")
        else:
            print(f"  ✗ {un:<11} {st}  {b[:120]}")
    if len(toks) != 3:
        print("登录失败，终止")
        return 1

    st, b, _ = req("GET", "/api/motifs", toks["heritor01"])
    motifs = jload(b) or []
    print(f"\n  母题库 {len(motifs)} 条（512 维向量已预载）：")
    for m in motifs:
        print(f"    - {m.get('motif_code')} {m.get('name'):<6} / {m.get('region'):<8} "
              f"向量模型={m.get('vector_model')} 已回填={m.get('has_vector')}")

    # ==================================================================== 2
    hr("2  上传作品样本并触发 AI 分析")
    assets = {}
    plan = [
        ("sample_derived_butterfly.jpg", "蝴蝶纹袖片（杨氏改良版）",
         "在苗绣蝴蝶纹母题基础上重构：翅内改为同心螺旋，腹部加珠串，翅下增流苏，色彩沿用传统靛蓝与朱红。"),
        ("sample_original_fish.jpg", "鱼纹挑花（原创）",
         "以苗绣鱼纹为题材自创构图，未参照任何现存公有母题。"),
    ]
    for fname, title, desc in plan:
        print(f"\n▸ 上传 {fname}")
        st, b, ms = upload(toks["heritor01"], fname, title, desc)
        if st != 201:
            print(f"  ✗ HTTP {st}  {b[:300]}")
            note("缺陷", f"上传失败 {fname}", f"HTTP {st} {b[:200]}")
            continue
        d = jload(b) or {}
        a = d.get("asset") or {}
        aid = a.get("asset_id")
        assets[aid] = {"title": title, "file": fname, "asset": a}
        print(f"  ✓ HTTP 201  {ms:5.0f}ms  asset_id={aid}")
        print(f"    file_hash={a.get('file_hash')}  size={a.get('file_size'):,}B  "
              f"sha 前 12 位={str(a.get('file_hash'))[:12]}")
        print(f"    初始状态 audit_status={a.get('audit_status')}  right_level={a.get('right_level')}")
        if d.get("duplicate_of"):
            note("设计内", f"{fname} 命中文件查重", f"duplicate_of={d['duplicate_of']}（同内容已存在，直接返回原资产）")

        st, b, ms = req("POST", f"/api/asset/{aid}/analyze", toks["heritor01"])
        an = jload(b) or {}
        if st != 200:
            print(f"  ✗ 分析失败 HTTP {st} {b[:300]}")
            note("缺陷", f"AI 分析失败 asset {aid}", f"HTTP {st} {b[:200]}")
            continue
        print(f"  ✓ AI 分析 {ms:5.0f}ms")
        print(f"    建议级别   {an.get('suggested_level')}    置信度 {an.get('confidence')}")
        print(f"    判定规则   {an.get('rule')}    margin={an.get('margin')}")
        print(f"    审核流水   audit_id={an.get('audit_id')}")
        md = an.get("model") or {}
        print(f"    模型       {md.get('name')} v{md.get('version')} dim={md.get('dim')} "
              f"深度模型={md.get('is_deep_model')}")
        print(f"    理由       {an.get('reason')}")
        print(f"    母题 Top-K（余弦相似度）:")
        for s in (an.get("top_k") or []):
            print(f"      · {s.get('code'):<8} {str(s.get('name')):<7} "
                  f"sim={s.get('similarity'):.4f}  距离={s.get('distance'):.4f}  {s.get('extra', {}).get('region', '')}")
        if an.get("similar_assets"):
            print(f"    相似私有资产 {len(an['similar_assets'])} 条")
        print(f"    HNSW 执行计划 {an.get('plan')}")
        assets[aid].update({"suggested": an.get("suggested_level"),
                            "confidence": an.get("confidence"), "top_k": an.get("top_k")})

        # 关键约束：AI 绝不能改 right_level
        st2, b2, _ = req("GET", f"/api/asset/{aid}", toks["heritor01"])
        cur = (jload(b2) or {}).get("right_level")
        print(f"    ★ 复核：分析后 right_level={cur}（AI 未改动）")
        if cur != a.get("right_level"):
            note("缺陷", "AI 分析改动了 right_level", f"{a.get('right_level')} → {cur}")
        st3, b3, _ = req("GET", f"/api/asset/{aid}", toks["heritor01"])
        print(f"    ★ 复核：audit_status={(jload(b3) or {}).get('audit_status')}（应进入 pending）")

    # ==================================================================== 3
    hr("3  专家工作台：待审队列")
    st, b, _ = req("GET", "/api/audit/pending", toks["expert01"])
    pend = jload(b) or []
    print(f"待审 {len(pend)} 件：")
    for p in pend:
        print(f"  · #{p.get('asset_id')} {p.get('title')}")
        print(f"   权利人={p.get('owner_name')} / {p.get('owner_org')}")
        print(f"   AI 建议={p.get('ai_suggest_level')}  置信度={p.get('ai_confidence')}  当前权属={p.get('current_right_level')}")

    # 越权反向验证
    st, b, _ = req("GET", "/api/audit/pending", toks["heritor01"])
    print(f"\n反向验证 · 传承人访问待审列表 → HTTP {st}（应为 403）")
    if st != 403:
        note("缺陷", "传承人能访问专家待审列表", f"HTTP {st}")

    # ==================================================================== 4
    hr("4  专家终审确权（单事务）")
    approved = []
    for p in pend:
        aid = p["asset_id"]
        ai_lv = p.get("ai_suggest_level")
        # 样本 1：专家推翻 AI（AI 说 derived，专家也判 derived 其实不一致，按实际样本定）
        # 这里按业务意图：蝴蝶改良版→derived；原创鱼纹→original
        target = "derived" if "蝴蝶" in (p.get("title") or "") else "original"
        opinion = ("与公有「蝴蝶纹」母题相比，翅形骨架保留，但翅内螺旋、腹部珠串与翅下流苏为新增设计，"
                   "构成实质性改编，同意按改编衍生确权。") if target == "derived" else \
                  ("题材与构图均未参照现存公有母题，鱼身卷草刻线为自创，同意认定为原创作品。")
        st, b, ms = req("POST", f"/api/audit/{aid}/approve", toks["expert01"],
                        body={"final_level": target, "opinion": opinion})
        r = jload(b) or {}
        if st == 200:
            approved.append(aid)
            print(f"  ✓ asset #{aid} 终审通过 {ms:5.0f}ms")
            print(f"    AI 建议 {ai_lv}  →  专家终审 {target}  "
                  f"{'【专家推翻 AI】' if ai_lv != target else '【与 AI 一致】'}")
            print(f"    返回 right_level={r.get('right_level')} audit_status={r.get('audit_status')} "
                  f"is_override={r.get('is_override')}")
            assets[aid]["final"] = target
        else:
            print(f"  ✗ HTTP {st} {b[:250]}")
            note("缺陷", f"终审失败 asset {aid}", f"HTTP {st} {b[:200]}")

    # 重复终审反向验证
    if approved:
        st, b, _ = req("POST", f"/api/audit/{approved[0]}/approve", toks["expert01"],
                       body={"final_level": "original", "opinion": "重复提交"})
        print(f"\n反向验证 · 重复终审 asset #{approved[0]} → HTTP {st}（应为 409）")
        print(f"  {b[:140].decode('utf-8', 'replace')}")
        if st != 409:
            note("缺陷", "重复终审未被拦截", f"HTTP {st}")

    # ==================================================================== 5
    hr("5  授权前反向验证 + 授权登记")
    # 找一个未确权的资产试授权
    st, b, _ = req("GET", "/api/asset/mine", toks["heritor01"])
    mine = jload(b) or []
    draft = [m for m in mine if m.get("audit_status") != "approved"]
    if draft:
        st, b, _ = req("POST", f"/api/asset/{draft[0]['asset_id']}/license", toks["heritor01"],
                       body={"licensee_name": "测试机构", "license_type": "non_exclusive",
                             "start_date": "2026-11-01"})
        print(f"反向验证 · 对未确权资产 #{draft[0]['asset_id']} 授权 → HTTP {st}（应拒绝）")
        print(f"  {b[:140].decode('utf-8', 'replace')}")
        if st == 201:
            note("缺陷", "未终审资产可以登记授权", f"asset {draft[0]['asset_id']}")

    for aid in approved:
        st, b, ms = req("POST", f"/api/asset/{aid}/license", toks["heritor01"],
                        body={"licensee_name": "省博物馆文创开发部",
                              "license_type": "exclusive",
                              "scope": "文创衍生品开发与销售",
                              "territory": "中国大陆",
                              "start_date": "2026-11-01",
                              "end_date": "2029-10-31",
                              "fee_cny": 18000})
        d = jload(b) or {}
        print(f"  ✓ asset #{aid} 授权登记 HTTP {st}  license_id={d.get('license_id')} "
              f"类型={d.get('license_type')} 费用=¥{d.get('fee_cny')} 状态={d.get('status')}")
        assets[aid]["license"] = d

    # 独占唯一性反向验证
    if approved:
        st, b, _ = req("POST", f"/api/asset/{approved[0]}/license", toks["heritor01"],
                       body={"licensee_name": "另一家公司", "license_type": "exclusive",
                             "start_date": "2027-01-01"})
        print(f"\n反向验证 · 同一资产再登记独占授权 → HTTP {st}（应拒绝）")
        print(f"  {b[:160].decode('utf-8', 'replace')}")
        if st == 201:
            note("缺陷", "独占授权唯一性未被约束", f"asset {approved[0]}")

    # ==================================================================== 6
    hr("6  溯源链路")
    for aid in approved:
        st, b, ms = req("GET", f"/api/asset/{aid}/trace", toks["heritor01"])
        t = jload(b) or {}
        nodes = t.get("nodes") or []
        print(f"\n▸ asset #{aid}「{t.get('title')}」")
        print(f"  HTTP {st}  链路完整={t.get('is_chain_valid')}  问题={t.get('chain_issues')}  "
              f"file_hash={str(t.get('file_hash'))[:16]}")
        print(f"  权属={t.get('right_level')}  状态={t.get('audit_status')}  共 {len(nodes)} 个节点：")
        seqs = [n.get("seq_no") for n in nodes]
        if seqs != list(range(1, len(nodes) + 1)):
            note("缺陷", f"asset #{aid} 溯源节点序号不连续", f"实际 seq={seqs}")
        else:
            print(f"    序号连续性  {seqs} ✓")
        for n in nodes:
            print(f"    {n.get('seq_no')}. [{n.get('event_type'):<11}] {n.get('summary')}")
            print(f"        {n.get('event_at')}  操作者={n.get('actor_name')}({n.get('actor_role')})")
            det = n.get("detail") or {}
            if det:
                ks = [k for k in det if k in ("state", "opinion", "changed_fields", "old_value", "new_value")]
                if ks:
                    print(f"        详情键: {ks}")

    # ==================================================================== 7
    hr("7  监管台账")
    st, b, ms = req("GET", "/api/ledger", toks["admin01"])
    led = jload(b) or {}
    rows = led.get("rows") if isinstance(led, dict) else led
    summary = led.get("summary") if isinstance(led, dict) else None
    print(f"HTTP {st}  {ms:.0f}ms")
    if summary:
        print(f"汇总：{json.dumps(summary, ensure_ascii=False, indent=2)[:600]}")
    print(f"\n台账 {len(rows or [])} 行：")
    for r in (rows or []):
        print(f"  · #{r.get('asset_id')} {r.get('title')}")
        print(f"    权属={r.get('right_level')} 状态={r.get('audit_status')} "
              f"AI={r.get('ai_suggest_level')} 一致={r.get('ai_expert_agree')}")
        print(f"    授权 {r.get('license_cnt')} 份（生效 {r.get('active_license_cnt')}）  "
              f"授权费合计 ¥{r.get('total_fee_cny')}  审核 {r.get('audit_times')} 次  "
              f"DB审计日志 {r.get('db_log_cnt')} 条  版本 v{r.get('version_no')}  "
              f"母题血缘={r.get('motif_code') or '无'}  衍生 {r.get('derivative_cnt')} 件")

    # ==================================================================== 8
    hr("8  异常与注意事项清单")
    if not NOTES:
        print("  无异常")
    else:
        for i, n in enumerate(NOTES, 1):
            tag = {"设计内": "○", "缺陷": "✗", "环境限制": "!"}[n["level"]]
            print(f"  {tag} [{n['level']}] {n['what']}")
            print(f"      {n['detail']}")

    # 请求统计
    by_st: dict[int, int] = {}
    for c in CALLS:
        by_st[c["st"]] = by_st.get(c["st"], 0) + 1
    print(f"\n  请求总数 {len(CALLS)}，状态分布 {dict(sorted(by_st.items()))}")

    jout = ROOT / "docs" / "verify" / "run_result.json"
    jout.write_text(json.dumps(
        {"backend": ai, "motifs": len(motifs), "assets": {str(k): {kk: vv for kk, vv in v.items() if kk != "asset"}
                                                          for k, v in assets.items()},
         "notes": NOTES, "calls": CALLS}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"  原始结果已写入 {jout.relative_to(ROOT)}")

    srv.should_exit = True
    return 0


if __name__ == "__main__":
    sys.exit(main())
