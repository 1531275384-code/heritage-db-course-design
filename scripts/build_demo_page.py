"""
生成单文件交互式演示页 output/01-交付物/演示页面.html。

设计目标：
    双击即开、零安装、零网络。所有数据、样式、脚本、图片全部内嵌在一个文件里，
    在 file:// 协议下也不会有任何跨域或资源加载问题。

结构：
    左侧：文档精简版（7 章，与 Word 文档章节一一对应，可点击跳转）
    右侧：实时演示区（5 个模块，可切换角色、筛选、查看详情、模拟终审）

为什么把图片转 base64 内嵌：
    演示场景常在会议室电脑上进行，对方机器可能没有本项目的数据目录。
    外链图片一旦路径不对就是一片空白，而 base64 内嵌使这个文件拷到任何地方都能用。
    代价是文件变大（约 1.5 MB），对本地文件来说完全可以接受。

用法：
    .venv/Scripts/python.exe scripts/build_demo_page.py
"""

from __future__ import annotations

import base64
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SEED_FILE = BASE / "data" / "seed_demo.json"
# 交付页直接生成到交付物目录，与 Word 文档并列
OUT_DIR = BASE / "output" / "01-交付物"
OUT_FILE = OUT_DIR / "演示页面.html"
# 内嵌的界面截图
FIG_DIR = BASE / "output" / "02-截图证据" / "交付文档配图"

# 需要内嵌的图片：母题参考图 + 作品图 + 界面截图
IMG_SOURCES = {
    # 母题参考图
    "MO-001": "data/motifs/motif_butterfly.jpg",
    "MO-002": "data/motifs/motif_lotus.jpg",
    "MO-003": "data/motifs/motif_huiwen.jpg",
    # 界面截图
    "shot_mine": str(FIG_DIR / "fig_mine.png"),
    "shot_motifs": str(FIG_DIR / "fig_motifs.png"),
    "shot_workbench": str(FIG_DIR / "fig_workbench.png"),
    "shot_ledger": str(FIG_DIR / "fig_ledger.png"),
    "shot_upload": str(FIG_DIR / "fig_upload.png"),
    "shot_analyze": str(FIG_DIR / "fig_analyze.png"),
}


def b64(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def build_payload() -> dict:
    seed = json.loads(SEED_FILE.read_text(encoding="utf-8"))

    images = {}
    for key, rel in IMG_SOURCES.items():
        p = BASE / rel
        if p.exists():
            images[key] = b64(p)

    # 作品图（按文件名映射）
    for a in seed["assets"]:
        p = BASE / "data" / "uploads" / a["file_path"]
        if p.exists():
            images[f"asset_{a['asset_id']}"] = b64(p)

    # 把 days_ago 转成可读日期，前端直接显示
    today = datetime.now().date()
    for a in seed["assets"]:
        for k, out in (("created_days_ago", "created_at"), ("decided_days_ago", "decided_at")):
            if a.get(k) is not None:
                a[out] = str(today - timedelta(days=a[k]))
        for au in a.get("audits", []):
            for k, out in (("days_ago", "created_at"),
                           ("decided_days_ago", "decided_at")):
                if au.get(k) is not None:
                    au[out] = str(today - timedelta(days=au[k]))
        for l in a.get("licenses", []):
            for k, out in (("start_days_ago", "start_date"), ("end_days_ago", "end_date")):
                if l.get(k) is not None:
                    l[out] = str(today - timedelta(days=l[k]))

    return {"seed": seed, "images": images, "built_at": datetime.now().strftime("%Y-%m-%d %H:%M")}


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>非遗数字资产分级确权与溯源系统 · 交付演示</title>
<style>
:root{
  --ink:#1f1b18; --ink2:#4a423c; --ink3:#8a8178;
  --paper:#faf8f4; --paper2:#f4f1ec; --line:#e3ddd2;
  --cin:#a8322c; --cin-l:#fbefee; --ind:#2c4a6e; --ind-l:#eef2f7;
  --ok:#3f7a4e; --ok-l:#eef5f0; --warn:#b07b1e; --warn-l:#fdf6e8;
  --r:10px;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0;height:100%}
body{
  font:14px/1.7 "Microsoft YaHei","PingFang SC","Hiragino Sans GB",sans-serif;
  color:var(--ink); background:var(--paper2); overflow:hidden;
  -webkit-font-smoothing:antialiased;
}
code,.mono{font-family:Consolas,"Courier New",monospace}

/* ================= 顶栏 ================= */
#top{
  height:52px; background:var(--ink); color:#efe9df; display:flex; align-items:center;
  padding:0 18px; gap:16px; position:relative; z-index:20; flex:0 0 52px;
}
#top .brand{display:flex;align-items:center;gap:9px;font-weight:700;letter-spacing:.5px}
#top .seal{
  width:26px;height:26px;background:var(--cin);border-radius:5px;display:grid;place-items:center;
  font-size:14px;font-weight:700;color:#fff;
}
#top .sub{font-size:11px;color:#9b9187;font-weight:400;letter-spacing:1px;margin-left:2px}
#top .spacer{flex:1}
.roles{display:flex;gap:4px;background:#2e2925;padding:3px;border-radius:8px}
.roles button{
  border:0;background:transparent;color:#b4a99d;padding:5px 11px;border-radius:6px;
  cursor:pointer;font-size:12px;font-family:inherit;transition:.15s;
}
.roles button:hover{color:#fff;background:#3d3733}
.roles button.on{background:var(--cin);color:#fff;font-weight:600}
#top .who{font-size:12px;color:#b4a99d;display:flex;align-items:center;gap:8px}
#top .avatar{
  width:26px;height:26px;border-radius:50%;background:#3d3733;display:grid;place-items:center;
  font-size:12px;color:#efe9df;
}

/* ================= 主体两栏 ================= */
#wrap{display:flex;height:calc(100% - 52px)}

/* 左：文档 */
#doc{
  width:47%;min-width:400px;max-width:760px;overflow-y:auto;background:#fff;
  border-right:1px solid var(--line);padding:0 0 80px;
}
#doc::-webkit-scrollbar{width:9px}
#doc::-webkit-scrollbar-thumb{background:#d5cec2;border-radius:5px}
#doc::-webkit-scrollbar-track{background:#f7f4ef}

.doc-head{padding:30px 34px 18px;border-bottom:1px solid var(--line);background:var(--paper)}
.doc-head h1{
  margin:0 0 6px;font-size:23px;letter-spacing:.5px;line-height:1.35;
  font-weight:700;color:var(--ink);
}
.doc-head .meta{font-size:12px;color:var(--ink3);margin-top:10px;display:flex;flex-wrap:wrap;gap:14px}
.doc-head .meta b{color:var(--ink2);font-weight:600}

/* 章节目录条 */
#toc{
  position:sticky;top:0;z-index:10;background:rgba(255,255,255,.96);
  backdrop-filter:blur(6px);border-bottom:1px solid var(--line);
  padding:9px 22px;display:flex;gap:4px;overflow-x:auto;
}
#toc::-webkit-scrollbar{height:0}
#toc button{
  border:1px solid transparent;background:transparent;color:var(--ink3);font-size:12px;
  padding:4px 10px;border-radius:14px;cursor:pointer;white-space:nowrap;font-family:inherit;
}
#toc button:hover{background:var(--paper2);color:var(--ink2)}
#toc button.on{background:var(--cin-l);color:var(--cin);border-color:#f0d8d6;font-weight:600}

.ch{padding:26px 34px 6px;scroll-margin-top:46px}
.ch h2{
  font-size:17px;margin:0 0 14px;padding-bottom:9px;border-bottom:2px solid var(--cin);
  display:inline-block;color:var(--cin);font-weight:700;letter-spacing:.3px;
}
.ch h3{
  font-size:14px;margin:20px 0 8px;color:var(--ind);font-weight:700;
  display:flex;align-items:center;gap:7px;
}
.ch h3::before{content:"";width:3px;height:14px;background:var(--ind);border-radius:2px}
.ch p{margin:8px 0;color:var(--ink2);text-align:justify}
.ch ul{margin:8px 0 8px 0;padding-left:20px;color:var(--ink2)}
.ch li{margin:4px 0}
.ch b.hl{color:var(--ink);font-weight:700}
.ch code{background:var(--paper2);padding:1px 5px;border-radius:4px;font-size:12.5px;color:var(--cin)}

.box{
  border-left:3px solid var(--ind);background:var(--ind-l);padding:11px 14px;
  border-radius:0 var(--r) var(--r) 0;margin:12px 0;font-size:13px;color:var(--ink2);
}
.box.red{border-color:var(--cin);background:var(--cin-l)}
.box.grey{border-color:var(--ink3);background:var(--paper2)}
.box .bt{font-weight:700;color:var(--ind);margin-bottom:3px;font-size:13px}
.box.red .bt{color:var(--cin)}
.box.grey .bt{color:var(--ink)}

.dtab{width:100%;border-collapse:collapse;margin:10px 0 14px;font-size:12.5px}
.dtab th{background:var(--cin);color:#fff;text-align:left;padding:7px 10px;font-weight:600}
.dtab td{border:1px solid var(--line);padding:6px 10px;vertical-align:top;color:var(--ink2)}
.dtab tr:nth-child(even) td{background:var(--paper)}
.dtab .n{text-align:center;width:34px;color:var(--ink3)}

.kv{display:grid;grid-template-columns:auto 1fr;gap:6px 12px;margin:10px 0;font-size:13px}
.kv dt{color:var(--ink3)}
.kv dd{margin:0;color:var(--ink2)}

/* 右：演示 */
#demo{flex:1;overflow-y:auto;background:var(--paper2);padding:0 0 60px;position:relative}
#demo::-webkit-scrollbar{width:9px}
#demo::-webkit-scrollbar-thumb{background:#d5cec2;border-radius:5px}

.demo-bar{
  position:sticky;top:0;z-index:12;background:rgba(244,241,236,.97);backdrop-filter:blur(6px);
  border-bottom:1px solid var(--line);padding:11px 22px;display:flex;align-items:center;gap:10px;
}
.demo-bar .t{font-size:13px;font-weight:700;color:var(--ink)}
.demo-bar .hint{font-size:11.5px;color:var(--ink3)}
.demo-bar .live{
  font-size:10.5px;color:var(--ok);background:var(--ok-l);border:1px solid #cfe3d5;
  padding:2px 8px;border-radius:10px;font-weight:600;
}
.tabs{display:flex;gap:4px;margin-left:auto;background:#e9e4db;padding:3px;border-radius:8px}
.tabs button{
  border:0;background:transparent;color:var(--ink3);font-size:12px;padding:5px 12px;
  border-radius:6px;cursor:pointer;font-family:inherit;white-space:nowrap;
}
.tabs button:hover{color:var(--ink2)}
.tabs button.on{background:#fff;color:var(--cin);font-weight:600;box-shadow:0 1px 3px rgba(0,0,0,.08)}

.pane{display:none;padding:18px 22px}
.pane.on{display:block}

.card{background:#fff;border:1px solid var(--line);border-radius:var(--r);padding:16px 18px;margin-bottom:14px}
.card>h4{margin:0 0 12px;font-size:13.5px;color:var(--ink);font-weight:700;display:flex;align-items:center;gap:8px}
.card>h4 .num{
  width:19px;height:19px;border-radius:50%;background:var(--cin);color:#fff;font-size:11px;
  display:grid;place-items:center;flex:0 0 19px;
}
.card .desc{font-size:12px;color:var(--ink3);margin:-6px 0 12px}

/* 统计条 */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:10px;margin-bottom:14px}
.stat{background:#fff;border:1px solid var(--line);border-radius:var(--r);padding:12px 14px}
.stat .k{font-size:11px;color:var(--ink3);margin-bottom:3px}
.stat .v{font-size:21px;font-weight:700;color:var(--ink);line-height:1.1;font-variant-numeric:tabular-nums}
.stat .v small{font-size:11px;font-weight:400;color:var(--ink3);margin-left:2px}
.stat.acc .v{color:var(--cin)}

/* 表格 */
.tt{width:100%;border-collapse:collapse;font-size:12.5px}
.tt th{
  text-align:left;padding:8px 9px;background:var(--paper2);color:var(--ink3);
  font-weight:600;font-size:11.5px;border-bottom:1px solid var(--line);white-space:nowrap;
}
.tt td{padding:9px;border-bottom:1px solid #f0ece5;vertical-align:middle;color:var(--ink2)}
/* 按钮所在单元格不能被挤成竖排 */
.tt td .btn{white-space:nowrap}
.tt tr:last-child td{border-bottom:0}
.tt tbody tr:hover{background:#fdfcfa}
.tt .id{color:var(--ink3);font-variant-numeric:tabular-nums}
.tt .name{color:var(--ink);font-weight:600}
.tt .thumb{
  width:38px;height:38px;border-radius:6px;object-fit:cover;background:var(--paper2);
  border:1px solid var(--line);vertical-align:middle;
}

/* 徽标 */
.b{display:inline-block;padding:2px 7px;border-radius:5px;font-size:11px;font-weight:600;white-space:nowrap}
.b.pub{background:#eef2f7;color:#2c4a6e}
.b.der{background:#fdf6e8;color:#8a6318}
.b.ori{background:#eef5f0;color:#2f6b3e}
.b.pending{background:var(--warn-l);color:#8a6318}
.b.approved{background:var(--ok-l);color:var(--ok)}
.b.draft{background:#eceae6;color:#6b6259}
.b.rejected{background:var(--cin-l);color:var(--cin)}
.b.ai{background:#f1edf9;color:#5b4380}
.conf{font-size:11px;color:var(--ink3);margin-left:4px;font-variant-numeric:tabular-nums}

/* 按钮 */
.btn{
  border:1px solid var(--line);background:#fff;color:var(--ink2);padding:5px 11px;
  border-radius:6px;font-size:12px;cursor:pointer;font-family:inherit;transition:.15s;
}
.btn:hover{border-color:var(--cin);color:var(--cin)}
.btn.pri{background:var(--cin);border-color:var(--cin);color:#fff;font-weight:600}
.btn.pri:hover{background:#8f2823;color:#fff}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn:disabled:hover{border-color:var(--line);color:var(--ink2)}
.btn.sm{padding:3px 9px;font-size:11.5px}

/* 筛选 */
.filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.filters button{
  border:1px solid var(--line);background:#fff;color:var(--ink3);padding:4px 12px;
  border-radius:14px;font-size:12px;cursor:pointer;font-family:inherit;
}
.filters button:hover{border-color:var(--ink3)}
.filters button.on{background:var(--cin);border-color:var(--cin);color:#fff;font-weight:600}

/* 母题卡片 */
.mgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}
.mcard{background:#fff;border:1px solid var(--line);border-radius:var(--r);overflow:hidden;cursor:pointer;transition:.18s}
.mcard:hover{box-shadow:0 4px 14px rgba(0,0,0,.08);transform:translateY(-1px);border-color:#d5cec2}
.mcard img{width:100%;height:132px;object-fit:cover;display:block;background:var(--paper2)}
.mcard .body{padding:11px 13px}
.mcard .code{font-size:10.5px;color:var(--cin);font-weight:600;letter-spacing:.5px}
.mcard .nm{font-size:13.5px;font-weight:700;color:var(--ink);margin:2px 0 5px}
.mcard .rows{font-size:11.5px;color:var(--ink3);line-height:1.6}
.chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:7px}
.chip{background:var(--paper2);color:var(--ink3);font-size:10.5px;padding:1px 7px;border-radius:9px}
.mcard .foot{
  margin-top:9px;padding-top:8px;border-top:1px dashed var(--line);
  font-size:11px;color:var(--ink3);display:flex;justify-content:space-between;
}

/* 空状态 */
.empty{text-align:center;padding:44px 20px;color:var(--ink3);font-size:13px}
.empty .ic{font-size:30px;opacity:.35;margin-bottom:8px}

/* 抽屉 / 弹窗 */
#mask{
  position:fixed;inset:0;background:rgba(31,27,24,.42);z-index:50;display:none;
  align-items:center;justify-content:center;padding:24px;
}
#mask.on{display:flex}
#modal{
  background:#fff;border-radius:14px;max-width:660px;width:100%;max-height:86vh;overflow-y:auto;
  box-shadow:0 20px 60px rgba(0,0,0,.28);
}
#modal::-webkit-scrollbar{width:8px}
#modal::-webkit-scrollbar-thumb{background:#d5cec2;border-radius:4px}
.mhead{
  padding:18px 22px 14px;border-bottom:1px solid var(--line);display:flex;align-items:flex-start;gap:12px;
  position:sticky;top:0;background:#fff;z-index:2;
}
.mhead h3{margin:0;font-size:16px;color:var(--ink);font-weight:700;flex:1}
.mhead .x{
  border:0;background:transparent;font-size:20px;line-height:1;color:var(--ink3);cursor:pointer;
  padding:0 4px;
}
.mhead .x:hover{color:var(--cin)}
.mbody{padding:18px 22px 24px}

/* 相似度条 */
.simrow{display:flex;align-items:center;gap:9px;margin:7px 0;font-size:12.5px}
.simrow .lbl{width:96px;color:var(--ink2);flex:0 0 96px}
.simrow .bar{flex:1;height:7px;background:var(--paper2);border-radius:4px;overflow:hidden}
.simrow .bar i{display:block;height:100%;border-radius:4px;transition:width .4s}
.simrow .val{width:46px;text-align:right;font-variant-numeric:tabular-nums;color:var(--ink2);font-weight:600}

/* 时间轴 */
.tl{position:relative;padding-left:22px;margin:12px 0}
.tl::before{content:"";position:absolute;left:6px;top:6px;bottom:6px;width:1px;background:var(--line)}
.tli{position:relative;margin-bottom:14px}
.tli::before{
  content:"";position:absolute;left:-19px;top:5px;width:9px;height:9px;border-radius:50%;
  background:#fff;border:2px solid var(--ind);
}
.tli.acc::before{border-color:var(--cin);background:var(--cin)}
.tli .tt2{font-size:12.5px;font-weight:700;color:var(--ink)}
.tli .tm{font-size:11px;color:var(--ink3);margin-left:6px;font-weight:400}
.tli .td{font-size:12px;color:var(--ink3);margin-top:2px;line-height:1.6}

/* 逐档按钮 */
.pick{display:flex;gap:8px;margin:12px 0}
.pick button{
  flex:1;border:1px solid var(--line);background:#fff;border-radius:8px;padding:10px 8px;
  cursor:pointer;font-family:inherit;text-align:center;transition:.15s;
}
.pick button .pl{font-size:12.5px;font-weight:700;color:var(--ink2);display:block}
.pick button .pd{font-size:10.5px;color:var(--ink3);margin-top:2px}
.pick button:hover{border-color:var(--ink3)}
.pick button.on{border-color:var(--cin);background:var(--cin-l)}
.pick button.on .pl{color:var(--cin)}

.toast{
  position:fixed;bottom:26px;left:50%;transform:translateX(-50%) translateY(14px);
  background:var(--ink);color:#fff;padding:10px 20px;border-radius:8px;font-size:12.5px;
  opacity:0;transition:.25s;z-index:80;pointer-events:none;box-shadow:0 8px 24px rgba(0,0,0,.25);
}
.toast.on{opacity:1;transform:translateX(-50%) translateY(0)}

.step{
  display:flex;gap:8px;margin:9px 0;font-size:12.5px;color:var(--ink2);
}
.step .sn{
  flex:0 0 20px;height:20px;border-radius:50%;background:var(--ind-l);color:var(--ind);
  font-size:11px;font-weight:700;display:grid;place-items:center;
}

.shot{
  width:100%;border-radius:8px;border:1px solid var(--line);display:block;margin:8px 0;
  cursor:zoom-in;
}

.tag-live{font-size:10px;color:var(--ink3);border:1px dashed var(--line);padding:1px 6px;border-radius:8px}

@media (max-width:1100px){
  #wrap{flex-direction:column;overflow-y:auto}
  #doc{width:100%;max-width:none;border-right:0;border-bottom:1px solid var(--line);overflow:visible}
  body{overflow:auto}
  #demo{overflow:visible}
}
</style>
</head>
<body>

<div id="top">
  <div class="brand">
    <div class="seal">遗</div>
    <div>非遗数字资产分级确权与溯源系统
      <div class="sub">ICH DIGITAL ASSET LEDGER · 交付演示</div>
    </div>
  </div>
  <div class="spacer"></div>
  <div class="roles" id="roles"></div>
  <button class="btn sm" id="resetBtn" title="把演示数据恢复到初始状态"
          style="background:#2e2925;border-color:#3d3733;color:#b4a99d;margin-left:4px">重置</button>
  <div class="who">
    <div class="avatar" id="avatar">杨</div>
    <div>
      <div id="whoName" style="color:#efe9df;font-size:12px">杨秀英</div>
      <div id="whoOrg" style="font-size:10.5px;color:#8a8178">黔东南苗族刺绣传习所</div>
    </div>
  </div>
</div>

<div id="wrap">
  <!--DOC_START  下面整块是左侧说明栏。构建纯演示版时会被整块剥掉。-->
  <!-- ======== 左：文档 ======== -->
  <div id="doc">
    <div class="doc-head">
      <h1>非物质文化遗产数字资产<br>分级确权与溯源系统</h1>
      <div style="font-size:13px;color:var(--cin);font-weight:600;margin-top:8px">项目交付说明书 · 精简版</div>
      <div class="meta">
        <span><b>文档类型</b> 交付说明书</span>
        <span><b>章节</b> 7 章</span>
        <span><b>数据</b> 4 账号 / 3 母题 / 8 作品</span>
        <span><b>完整版</b> Word 文档</span>
      </div>
      <div class="box" style="margin-top:14px;font-size:12px">
        <div class="bt">这一页怎么用</div>
        左侧是说明书精简版，右侧是<b>可以直接点击操作</b>的功能演示。
        点击左侧任何章节标题，右侧会跳到对应功能；顶栏可切换四种账号身份，
        观察不同角色能看到的数据与操作权限有何不同。
        <br><br>
        右侧的终审操作会真实改变演示数据（状态、统计随之变化），
        点顶栏「重置」即可恢复初始状态。刷新页面同样会重置。
      </div>
    </div>

    <div id="toc"></div>

    <div class="ch" id="c1">
      <h2>一、文档说明</h2>
      <p>本文档面向验收方与评阅人，回答四个问题：系统解决什么问题、关键约束靠什么机制保证、
        已经验证到什么程度、到哪里看实际效果。本页是其精简版，与完整 Word 文档章节一一对应。</p>
      <h3>1.1 阅读顺序建议</h3>
      <ul>
        <li><b class="hl">第二章</b>建立整体认知：三个业务难题与核心命题</li>
        <li><b class="hl">第四章</b>理解技术核心：AI 为什么改不了权属</li>
        <li><b class="hl">第五章</b>明确证据边界：哪些跑过、哪些没跑</li>
        <li><b class="hl">第六章</b>按五分钟脚本实操一遍</li>
      </ul>
      <div class="box">
        <div class="bt">关于本页的数字</div>
        所有统计数字均由构建脚本从 <code>data/seed_demo.json</code> 读取后内嵌，
        不是手工填写。种子里的 AI 结论由真实特征算法对实际图片计算后回写，
        可用 <code>python -m scripts.check_seed</code> 复核。
      </div>
    </div>

    <div class="ch" id="c2">
      <h2>二、项目概述</h2>
      <h3>2.1 三个业务难题</h3>
      <ul>
        <li><b class="hl">「像不像」难以量化</b>——公有母题、改编衍生、原创三者权利边界完全不同，肉眼说不清</li>
        <li><b class="hl">「从哪来」难以说清</b>——纹样口传传播，历经多次转手改良，缺少可信记录</li>
        <li><b class="hl">「谁定的」难以追溯</b>——权属结论若只存在记忆或表格里，纠纷时无法举证</li>
      </ul>
      <p>本项目用向量相似度量化「像不像」，用母题血缘与链式授权固化「从哪来」，
        用数据库列级权限与不可篡改日志固化「谁定的」。</p>

      <h3>2.2 核心命题</h3>
      <p><b class="hl">AI 只出建议，权属由专家终审。</b>
        AI 负责提特征、找最相似的母题、给出档位建议与置信度；但它没有能力写权属字段。
        权属只能由专家在单事务内终审产生，并留痕。专家是否推翻 AI 也记为可统计数据。</p>
      <div class="box red">
        <div class="bt">为什么不只是「约定不写」</div>
        靠自觉的约定，三个月后一次紧急需求就会打破，并且打破时不报错。
        所以本项目把它下沉到数据库列级权限：传承人角色的数据库账号在物理上就没有该列的更新权限。
      </div>

      <h3>2.3 三级判定规则</h3>
      <table class="dtab">
        <tr><th>判定档位</th><th>判定条件</th><th>权利含义</th></tr>
        <tr><td>公有母题 public</td><td>相似度 ≥ 0.92</td><td>与公有传统纹样高度一致，不得主张独占</td></tr>
        <tr><td>改编衍生 derived</td><td>0.80 ≤ 相似度 &lt; 0.92</td><td>再创作，权利归改编者，需标注母题来源</td></tr>
        <tr><td>原创 original</td><td>相似度 &lt; 0.80</td><td>与现有母题差异显著，可主张完整著作权</td></tr>
      </table>
      <p>阈值由可复现的标定脚本在样例集上搜索得出，受两条互相拉扯的约束：
        翻拍稳定性要求同一作品的 9 种变体相似度都 ≥ 0.90；改编可区分性要求真实改编作品相似度 &lt; 0.92。</p>
      <div class="box grey">
        <div class="bt">容易混淆：置信度不是相似度</div>
        相似度是两向量的余弦值（典型 0.80–0.95）；置信度综合了最近与次近母题的差值、
        以及判定档位与阈值的距离（典型 0.4–0.7）。两者量纲完全不同。
        本项目早期曾用置信度冒充相似度，界面显示 0.4190 而真实值是 0.8059——格式正常但是错的。
      </div>

      <h3>2.4 技术选型</h3>
      <table class="dtab">
        <tr><th>层次</th><th>选型</th><th>理由</th></tr>
        <tr><td>Web 框架</td><td>FastAPI</td><td>异步原生、Pydantic 校验、自动 OpenAPI</td></tr>
        <tr><td>数据库</td><td>PostgreSQL 16</td><td>行级锁、事务、触发器、列级 GRANT、JSONB</td></tr>
        <tr><td>向量检索</td><td>pgvector（可降级）</td><td>向量与业务数据同库，无额外中间件</td></tr>
        <tr><td>特征提取</td><td>CLIP / ResNet / 内置</td><td>三档可切换并自动降级，内置后端不依赖 GPU</td></tr>
        <tr><td>前端</td><td>原生 HTML/JS</td><td>无构建步骤，拿到即可打开</td></tr>
      </table>
    </div>

    <div class="ch" id="c3">
      <h2>三、系统设计</h2>
      <h3>3.1 八张表的职责</h3>
      <table class="dtab">
        <tr><th>组</th><th>表</th><th>职责</th></tr>
        <tr><td>主体</td><td class="mono">app_user</td><td>用户与角色</td></tr>
        <tr><td>基准</td><td class="mono">public_motif</td><td>公有母题库，含 512 维基准向量</td></tr>
        <tr><td>作品</td><td class="mono">asset</td><td>作品主表，权属/状态/AI 建议三字段分立</td></tr>
        <tr><td>作品</td><td class="mono">feature_vector</td><td>特征向量，与模型版本绑定</td></tr>
        <tr><td>流程</td><td class="mono">audit_record</td><td>审核流水，快照 AI 建议与相似度</td></tr>
        <tr><td>流程</td><td class="mono">license_record</td><td>授权记录，链式指向上一次</td></tr>
        <tr><td>溯源</td><td class="mono">asset_genealogy</td><td>作品衍生关系</td></tr>
        <tr><td>溯源</td><td class="mono">system_audit_log</td><td>系统审计日志，触发器写入，不可改删</td></tr>
      </table>

      <h3>3.2 三个易混字段</h3>
      <table class="dtab">
        <tr><th>字段</th><th>语义</th><th>谁能写</th></tr>
        <tr><td class="mono">right_level</td><td>权属级别（法律结论）</td><td>仅终审函数，仅专家</td></tr>
        <tr><td class="mono">audit_status</td><td>流转状态</td><td>多角色可推进</td></tr>
        <tr><td class="mono">ai_suggest_level</td><td>AI 建议（参考）</td><td>分析流程，可覆盖</td></tr>
      </table>
      <div class="box">
        <div class="bt">为什么拆成两个字段</div>
        为了让数据库能精确授权：传承人有 <code>ai_suggest_level</code> 的更新权，
        但没有 <code>right_level</code> 的更新权。若共用一个字段，这个区分无法用 GRANT 表达。
      </div>

      <h3>3.3 三个视图</h3>
      <table class="dtab">
        <tr><th>视图</th><th>用途</th></tr>
        <tr><td class="mono">v_asset_pending_audit</td><td>待审队列，LATERAL 只取最新一条待审流水</td></tr>
        <tr><td class="mono">v_asset_trace_chain</td><td>溯源原始事件投影，四类事件 UNION</td></tr>
        <tr><td class="mono">v_asset_ledger</td><td>监管台账，JOIN 母题 + LATERAL 取 Top-1 相似度</td></tr>
      </table>
      <div class="box red">
        <div class="bt">一个实现陷阱</div>
        台账视图新增 <code>top1_similarity</code> 后接口一度返回空值——
        Pydantic v2 默认丢弃未声明字段。视图改了、SQL 返回了，序列化时静默丢弃，不报错。
        现已加入「视图列集合与模型字段集合」静态比对检查。
      </div>

      <h3>3.4 权限模型</h3>
      <table class="dtab">
        <tr><th>角色</th><th>数据库角色</th><th>核心权限</th></tr>
        <tr><td>传承人</td><td class="mono">ich_heritor</td><td>读自己的作品；改标题描述等；<b class="hl">不可改权属</b></td></tr>
        <tr><td>评审专家</td><td class="mono">ich_expert</td><td>读全部；可改权属与状态；可写审计结论</td></tr>
        <tr><td>监管管理员</td><td class="mono">ich_admin</td><td>全量只读台账与日志；母题库维护</td></tr>
      </table>
      <p>权限通过 <code>SET LOCAL ROLE</code> 在事务开始时切换，作用域限于当前事务，
        不污染连接池。同一个连接池可为不同角色服务，每次请求用的都是它应得的权限集。</p>
    </div>

    <div class="ch" id="c4">
      <h2>四、核心机制：AI 为什么改不了权属</h2>
      <p>把一个约定固化为不可绕过的机制，本项目用了四层手段，从硬到软：</p>

      <h3>4.1 第一层：数据库列级权限（最硬）</h3>
      <p>GRANT 语句明确列出传承人可更新的列，<code>right_level</code> 不在其中。
        即使有人拿到传承人凭证直接连库提交改权属的语句，数据库也会拒绝。
        这不是应用层的条件判断，而是无法通过改代码绕过的物理限制。</p>

      <h3>4.2 第二层：源码不变量测试</h3>
      <ul>
        <li>分析函数体内不得出现任何写 <code>right_level</code> 的 SQL</li>
        <li>全工程只有终审函数一处设置权属</li>
        <li>分级器模块不得 import 任何数据库模块</li>
      </ul>
      <p>这些检查是负向的：谁想加一条自动确权的捷径，提交后测试立即失败并指出行号。</p>

      <h3>4.3 第三层：分级器物理隔离</h3>
      <p>分级器是纯函数模块，不 import 任何数据库模块，只能接收命中列表、返回数据类。
        它没有连接、没有游标、没有事务，物理上没有能力改任何状态。
        与其在代码审查时提醒，不如从模块依赖上断绝可能。</p>

      <h3>4.4 第四层：写入路径唯一加事务留痕</h3>
      <div class="step"><span class="sn">1</span><div>以 <code>SELECT FOR UPDATE</code> 锁定作品行，防并发覆盖</div></div>
      <div class="step"><span class="sn">2</span><div>校验当前状态必须为待审</div></div>
      <div class="step"><span class="sn">3</span><div>更新权属级别与流转状态</div></div>
      <div class="step"><span class="sn">4</span><div>写入审核流水</div></div>
      <div class="step"><span class="sn">5</span><div>校验受影响行数，不符则回滚</div></div>
      <div class="step"><span class="sn">6</span><div>回读系统日志，确认触发器确实写入</div></div>
      <p>六步要么全成功，要么全回滚。中途失败不会留下半成品状态。</p>
      <div class="box">
        <div class="bt">四层分工</div>
        第一层管谁能写，第二层管哪段代码能写，第三层管能不能连到库，第四层管写得完不完整。
        四层各自独立，任一层失效，其余三层仍然成立。
      </div>
    </div>

    <div class="ch" id="c5">
      <h2>五、验证情况</h2>
      <h3>5.1 已实际运行验证</h3>
      <table class="dtab">
        <tr><th>验证项</th><th>规模</th><th>内容</th></tr>
        <tr><td>业务链路端到端</td><td class="n">52</td><td>三角色、越权、上传分析、终审、授权、溯源、台账</td></tr>
        <tr><td>浏览器 UI 断言</td><td class="n">49</td><td>四角色五页签逐屏渲染 + 数值断言</td></tr>
        <tr><td>源码不变量</td><td class="n">88</td><td>AI 不写权属、单事务、GRANT、XSS 转义等</td></tr>
        <tr><td>向量兼容层</td><td class="n">39</td><td>降级模式与 pgvector 数值一致</td></tr>
        <tr><td>特征与分级器</td><td class="n">41</td><td>互分离度、类内稳定、退化输入鲁棒性</td></tr>
        <tr><td>规则单一真相</td><td class="n">59</td><td>规则表自动生成并与库对账、漂移检测、三值逻辑、列类型取整</td></tr>
        <tr><td>规则对拍（需数据库）</td><td class="n">75</td><td>62 组边界样本：真实库与 Python 规则判定 100% 一致</td></tr>
      </table>
      <p><b class="hl">合计 403 项检查，当前全部通过。</b></p>

      <h3>5.2 数据库层的实测结论与唯一缺口</h3>
      <div class="box red">
        <div class="bt">唯一缺口：HNSW 索引不能声称「检索快」</div>
        便携版 PostgreSQL 16.2 已在 127.0.0.1:55432 跑通，36 项真库验收 35 通过 / 1 缺口。
        缺口是 HNSW：便携包不含 pgvector，只能走 NUMERIC[] 精确扫描。降级模式的余弦距离与
        pgvector 数值一致（39 项兼容层测试验证），因此<b>功能等价成立，检索性能不做承诺</b>。
      </div>
      <table class="dtab">
        <tr><th>验证项</th><th>实测结论</th></tr>
        <tr><td>列级 GRANT 生效</td><td>997 条列级授权；传承人写 right_level 被 42501 拒绝</td></tr>
        <tr><td>CHECK / UNIQUE / 外键</td><td>24 + 4 + 12 全部生效，分别报 23514 / 23505 / 23503</td></tr>
        <tr><td>8 个触发器触发</td><td>禁改禁删、已终审不可改、未终审不得授权均实测拒绝</td></tr>
        <tr><td>终审主链路</td><td>expert 写库 → 审计留痕 → version_no 自增，actor 为业务角色</td></tr>
        <tr><td>行级锁并发</td><td>第二会话 SELECT FOR UPDATE 收到 55P03</td></tr>
        <tr><td>事务回滚完整性</td><td>失败时触发器日志随事务回滚，不留半条记录</td></tr>
        <tr><td>规则表与库一致</td><td>六类约束逐条对账一致（24/4/12/53/8）</td></tr>
      </table>

      <h3>5.3 演示数据与算法一致</h3>
      <p>种子数据里每件作品的 AI 结论、置信度与相似度，都由校验脚本调用真实特征算法
        对实际图片计算后回写。这一点很重要——如果结论是编的，演示时台账显示改编衍生，
        观众点重新分析却给出原创，整套演示的可信度就归零了。
        所以种子里只记录真实算法会得出什么，不记录希望它是什么。</p>

      <h3>5.4 开发中修复的 20 个缺陷</h3>
      <p>前 12 个靠逐行读代码与回归检查发现，其中 4 个属于「不报错」的类型，最难发现也最危险。
        后 8 个只在真实数据库上跑才暴露 —— 这一点本身比缺陷清单更值得说明：
        <b>静态检查与代码审查替代不了实跑</b>。下表列出最难发现的 4 个：</p>
      <table class="dtab">
        <tr><th class="n">#</th><th>缺陷</th><th>后果</th></tr>
        <tr><td class="n">9</td><td>用置信度冒充相似度</td><td>显示 0.4190，真实 0.8059，格式正常但是错的</td></tr>
        <tr><td class="n">10</td><td>种子 AI 结论手填</td><td>2/8 与算法不符，重新分析会改判</td></tr>
        <tr><td class="n">11</td><td>视图加列未同步模型</td><td>字段被静默丢弃，前端该列全空</td></tr>
        <tr><td class="n">12</td><td>重构致授权写入成死代码</td><td>授权费合计恒为 0，接口仍正常返回</td></tr>
      </table>
      <p>第 12 条是纯重构引发的：拆函数时误缩进到返回语句之后，五笔授权就此消失，
        而所有接口依然正常返回。最终由一句刻意的数值断言（授权费合计必须大于 0）抓到。
        这也是为什么本项目的界面测试不只截图，还要断言每个格子里的具体数字。</p>
    </div>

    <div class="ch" id="c6">
      <h2>六、交付物与使用方法</h2>
      <h3>6.1 交付物清单</h3>
      <table class="dtab">
        <tr><th>交付物</th><th>形式</th><th>用途</th></tr>
        <tr><td>项目交付说明书</td><td>Word .docx</td><td>正式提交，含全部 7 章与验收清单</td></tr>
        <tr><td>本演示页</td><td>单文件 HTML</td><td>双击即开，现场操作演示</td></tr>
        <tr><td>完整源代码</td><td>目录</td><td>后端、SQL、AI 模块、前端、测试</td></tr>
        <tr><td>演示种子数据</td><td>JSON</td><td>4 账号 / 3 母题 / 8 作品 / 14 审核 / 5 授权</td></tr>
        <tr><td>运行验证记录</td><td>MD + JSON</td><td>完整流程实跑报告与原始返回</td></tr>
        <tr><td>界面截图</td><td>PNG</td><td>四角色五页签逐屏截图</td></tr>
      </table>

      <h3>6.2 启动真实系统</h3>
      <p>本页是静态演示。如需验证真实读写：</p>
      <ul>
        <li><code>python bootstrap.py --demo</code> —— 演示模式，无需数据库</li>
        <li><code>python bootstrap.py --strict</code> —— 真实数据库，需先建库</li>
        <li><code>python -m scripts.init_seed</code> —— 把种子灌进真实数据库</li>
      </ul>
      <p>启动后浏览器打开 <code>http://127.0.0.1:8000/</code>，登录页提供演示账号点击直填。
        四个账号口令统一为 <code>Passw0rd!</code>。</p>

      <h3>6.3 五分钟演示脚本</h3>
      <table class="dtab">
        <tr><th class="n">步</th><th>操作</th><th>要点</th></tr>
        <tr><td class="n">1</td><td>传承人登录看「我的作品」</td><td>三件作品三种状态；只能看自己的</td></tr>
        <tr><td class="n">2</td><td>切「母题库」</td><td>三条母题是判定基准，入库时已提向量</td></tr>
        <tr><td class="n">3</td><td>点开待审作品</td><td>AI 建议与置信度，但权属还没定</td></tr>
        <tr><td class="n">4</td><td>专家登录进工作台</td><td>同一作品出现在待审队列</td></tr>
        <tr><td class="n">5</td><td>提交终审</td><td>写入流水与日志；与 AI 不同则标推翻</td></tr>
        <tr><td class="n">6</td><td>看监管台账</td><td>分组统计、授权费合计、母题血缘</td></tr>
        <tr><td class="n">7</td><td>传承人再试台账</td><td>被拒；不是前端隐藏，是后端 403</td></tr>
      </table>

      <h3>6.4 常见问题</h3>
      <p><b class="hl">没有数据库能看吗？</b>能，本页就是。或用 <code>--demo</code> 启动后端。</p>
      <p><b class="hl">怎么确认跑的是真库还是演示模式？</b>看页面顶部有无红色横幅，或看健康检查接口的 database 字段。</p>
      <p><b class="hl">演示数据重启后还在吗？</b>演示模式下随进程消失，重启恢复种子初始状态——刻意设计，避免被误当真库。</p>
    </div>

    <div class="ch" id="c7">
      <h2>七、验收清单</h2>
      <p>完整清单在 Word 文档第七章，共 14 项功能验收、10 项真实数据库验收、7 项交付物完整性。
        这里列出功能验收的前几项，右侧演示可逐条对照：</p>
      <table class="dtab">
        <tr><th class="n">#</th><th>验收项</th><th>通过标准</th></tr>
        <tr><td class="n">3</td><td>作品列表正确</td><td>传承人只看到自己的，数量与种子一致</td></tr>
        <tr><td class="n">4</td><td>母题库完整</td><td>三条母题显示参考图、民族、类别、年代、关键词</td></tr>
        <tr><td class="n">6</td><td>AI 未改权属</td><td>分析完成后权属级别保持不变</td></tr>
        <tr><td class="n">8</td><td>推翻 AI 可识别</td><td>专家结论与 AI 不同时标记为真</td></tr>
        <tr><td class="n">13</td><td>越权拦截</td><td>传承人访问监管台账返回 403</td></tr>
      </table>
      <div class="box grey">
        <div class="bt">关于真实数据库验收</div>
        第七章第二节的 10 项必须在装有 PostgreSQL 15+ 的环境执行，
        是补齐第五章未验证项的关键。这不是建议项，是部署时必做项。
      </div>
    </div>
  </div>
  <!--DOC_END-->

  <!-- ======== 右：演示 ======== -->
  <div id="demo">
    <div class="demo-bar">
      <span class="live">● 可点击操作</span>
      <span class="t" id="paneTitle">我的作品</span>
      <span class="hint" id="paneHint"></span>
      <div class="tabs" id="tabs"></div>
    </div>

    <!-- 我的作品 -->
    <div class="pane" id="p-mine">
      <div class="stats" id="mineStats"></div>
      <div class="card">
        <h4><span class="num">1</span>账号内作品列表</h4>
        <div class="desc">注意三列的分工：AI 建议（模型给的档位与置信度）、专家结论（已生效的权属）、状态（流转阶段）。点击行可查看详情与溯源。</div>
        <table class="tt" id="mineTable"></table>
      </div>
      <div class="card" id="mineTicket" style="display:none">
        <h4><span class="num">2</span>作品详情</h4>
        <div id="mineDetail"></div>
      </div>
    </div>

    <!-- 母题库 -->
    <div class="pane" id="p-motifs">
      <div class="card">
        <h4><span class="num">1</span>母题库：分级判定的比对基准</h4>
        <div class="desc">每件作品都要与这里的母题做向量近邻检索。点击卡片查看详情与关联作品。</div>
        <div class="filters" id="motifFilters"></div>
        <div class="mgrid" id="motifGrid"></div>
      </div>
      <div class="card">
        <h4><span class="num">2</span>母题与作品的关联</h4>
        <div class="desc">同一母题被多少件作品引用，各自落在哪一档——这是母题血缘的直接体现。</div>
        <table class="tt" id="motifLink"></table>
      </div>
    </div>

    <!-- 上传 -->
    <div class="pane" id="p-upload">
      <div class="card">
        <h4><span class="num">1</span>上传并触发 AI 分析</h4>
        <div class="desc">选一份内置样例，模拟真实上传流程：提特征 → 检索母题 → 给分级建议。注意结论只是建议，权属未定。</div>
        <div class="filters" id="samplePick"></div>
        <div id="uploadStage"></div>
      </div>
    </div>

    <!-- 工作台 -->
    <div class="pane" id="p-workbench">
      <div class="stats" id="wbStats"></div>
      <div class="card">
        <h4><span class="num">1</span>待审队列</h4>
        <div class="desc">所有 pending 状态的作品。传承人看不到这一页——这不是前端隐藏，是后端返回 403。</div>
        <table class="tt" id="wbTable"></table>
      </div>
    </div>

    <!-- 台账 -->
    <div class="pane" id="p-ledger">
      <div class="stats" id="lgStats"></div>
      <div class="card">
        <h4><span class="num">1</span>监管台账</h4>
        <div class="desc">按状态与级别分组统计；每件作品都能看到母题血缘与 Top-1 相似度。</div>
        <table class="tt" id="lgTable"></table>
      </div>
      <div class="card">
        <h4><span class="num">2</span>授权流转记录</h4>
        <div class="desc">五笔授权，合计费用在统计条第二项。独占授权对同一作品唯一。</div>
        <table class="tt" id="lgLicenses"></table>
      </div>
    </div>
  </div>
</div>

<div id="mask">
  <div id="modal">
    <div class="mhead">
      <h3 id="mTitle">标题</h3>
      <button class="x" onclick="closeModal()">×</button>
    </div>
    <div class="mbody" id="mBody"></div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
/* =========================================================
   内嵌数据（构建时注入，非手工填写）
   ========================================================= */
const PAYLOAD = __PAYLOAD__;
const SEED = PAYLOAD.seed;
const IMG = PAYLOAD.images;

const USERS = SEED.users, MOTIFS = SEED.motifs;
/* ASSETS 用 let 而非 const：重置功能需要整体换回原始副本。
   终审演示会就地修改内存里的对象，若不深拷贝，重置后仍是改过的数据。 */
let ASSETS = JSON.parse(JSON.stringify(SEED.assets));
const ASSETS_ORIGINAL = JSON.parse(JSON.stringify(SEED.assets));
const MOTIF_BY_ID = Object.fromEntries(MOTIFS.map(m => [m.motif_id, m]));
const USER_BY_ID = Object.fromEntries(USERS.map(u => [u.user_id, u]));

const LEVEL = {
  public:   { cn: '公有母题', cls: 'pub' },
  derived:  { cn: '改编衍生', cls: 'der' },
  original: { cn: '原创',     cls: 'ori' },
};
const STATUS = {
  draft:    { cn: '草稿',   cls: 'draft' },
  pending:  { cn: '待审',   cls: 'pending' },
  approved: { cn: '已确权', cls: 'approved' },
  rejected: { cn: '已驳回', cls: 'rejected' },
};
const LIC = { exclusive:'独占授权', non_exclusive:'非独占授权', display:'展示授权', derivative:'衍生开发授权' };

/* 角色 → 可见页签（与真实系统一致） */
const TABS = [
  { id:'mine',      label:'我的作品',   roles:['heritor'] },
  { id:'motifs',    label:'母题库',     roles:['heritor','expert','admin'] },
  { id:'upload',    label:'作品上传',   roles:['heritor','expert','admin'] },
  { id:'workbench', label:'专家工作台', roles:['expert','admin'] },
  { id:'ledger',    label:'监管台账',   roles:['expert','admin'] },
];

const S = { role:'heritor', user:null, tab:'mine', pick:null, motifFilter:'all', analyzed:null };

/* =========================================================
   工具
   ========================================================= */
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nz = v => (v === null || v === undefined || v === '') ? '—' : v;
const pct = v => (v === null || v === undefined) ? '—' : (v*100).toFixed(1) + '%';
const money = v => (v === null || v === undefined) ? '—' : '¥' + Number(v).toLocaleString('zh-CN');

function badgeLevel(l) {
  if (!l) return '<span class="b draft">未定</span>';
  const x = LEVEL[l] || { cn:l, cls:'draft' };
  return `<span class="b ${x.cls}">${x.cn}</span>`;
}
function badgeStatus(s) {
  const x = STATUS[s] || { cn:s, cls:'draft' };
  return `<span class="b ${x.cls}">${x.cn}</span>`;
}
function thumbFor(a) {
  const k = 'asset_' + a.asset_id;
  return IMG[k] ? `<img class="thumb" src="${IMG[k]}" alt="">` : '<div class="thumb"></div>';
}
function top1Sim(a) {
  const au = (a.audits||[]).filter(x => x.similarity != null);
  if (!au.length) return null;
  return au[au.length-1].similarity;
}
function toast(msg) {
  const t = $('toast');
  t.textContent = msg; t.classList.add('on');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove('on'), 2000);
}

/* =========================================================
   角色切换
   ========================================================= */
function setRole(role) {
  S.role = role;
  S.user = USERS.find(u => u.role === role && u.user_id === (role==='heritor'?1: role==='expert'?2:3))
           || USERS.find(u => u.role === role);
  if (role === 'heritor') S.user = USER_BY_ID[1];
  if (role === 'expert')  S.user = USER_BY_ID[2];
  if (role === 'admin')   S.user = USER_BY_ID[3];

  document.querySelectorAll('#roles button').forEach(b => b.classList.toggle('on', b.dataset.role === role));
  $('whoName').textContent = S.user.display_name;
  $('whoOrg').textContent  = S.user.org_name || '';
  $('avatar').textContent  = S.user.display_name.slice(0,1);

  const avail = TABS.filter(t => t.roles.includes(role));
  if (!avail.some(t => t.id === S.tab)) S.tab = avail[0].id;
  renderTabs();
  renderPane();
}

function renderTabs() {
  const avail = TABS.filter(t => t.roles.includes(S.role));
  $('tabs').innerHTML = avail.map(t =>
    `<button data-tab="${t.id}" class="${t.id===S.tab?'on':''}">${t.label}</button>`).join('');
  $('tabs').querySelectorAll('button').forEach(b =>
    b.onclick = () => { S.tab = b.dataset.tab; renderTabs(); renderPane(); });
}

const PANE_META = {
  mine:      ['我的作品', '当前账号名下的作品与流转状态'],
  motifs:    ['母题库', '分级判定的比对基准'],
  upload:    ['作品上传', '模拟上传并触发 AI 分析'],
  workbench: ['专家工作台', '待审队列与终审'],
  ledger:    ['监管台账', '全量统计与授权流转'],
};

function renderPane() {
  document.querySelectorAll('.pane').forEach(p => p.classList.remove('on'));
  const el = $('p-' + S.tab);
  if (el) el.classList.add('on');
  const [t, h] = PANE_META[S.tab] || ['', ''];
  $('paneTitle').textContent = t;
  $('paneHint').textContent = '· ' + h;

  if (S.tab === 'mine') renderMine();
  if (S.tab === 'motifs') renderMotifs();
  if (S.tab === 'upload') renderUpload();
  if (S.tab === 'workbench') renderWorkbench();
  if (S.tab === 'ledger') renderLedger();
}

/* =========================================================
   我的作品
   ========================================================= */
function renderMine() {
  const mine = ASSETS.filter(a => a.owner_id === S.user.user_id);

  const byStatus = {}, byLevel = {};
  mine.forEach(a => {
    byStatus[a.audit_status] = (byStatus[a.audit_status]||0)+1;
    if (a.audit_status === 'approved') byLevel[a.right_level] = (byLevel[a.right_level]||0)+1;
  });

  $('mineStats').innerHTML = [
    ['作品总数', mine.length, '件', ''],
    ['已确权', byStatus.approved||0, '件', 'acc'],
    ['待审', byStatus.pending||0, '件', ''],
    ['草稿', byStatus.draft||0, '件', ''],
    ['授权笔数', mine.reduce((s,a)=>s+(a.licenses||[]).length,0), '笔', ''],
  ].map(([k,v,u,c]) =>
    `<div class="stat ${c}"><div class="k">${k}</div><div class="v">${v}<small>${u}</small></div></div>`).join('');

  const rows = mine.map(a => {
    const conf = a.ai_confidence != null ? `<span class="conf">${pct(a.ai_confidence)}</span>` : '';
    return `<tr onclick="showAsset(${a.asset_id})" style="cursor:pointer">
      <td class="id">${a.asset_id}</td>
      <td class="name">${thumbFor(a)}<span style="margin-left:8px;vertical-align:middle">${esc(a.title)}</span></td>
      <td>${badgeLevel(a.ai_suggest_level)}${conf}</td>
      <td>${a.audit_status==='approved' ? badgeLevel(a.right_level) : '<span class="b draft">未定</span>'}</td>
      <td>${badgeStatus(a.audit_status)}</td>
      <td class="id">v${a.version_no}</td>
      <td class="id">${esc(a.created_at||'')}</td>
    </tr>`;
  }).join('');

  $('mineTable').innerHTML = `
    <thead><tr><th style="width:36px">ID</th><th>作品名称</th><th style="width:118px">AI 建议</th>
    <th style="width:96px">专家结论</th><th style="width:74px">状态</th><th style="width:46px">版本</th>
    <th style="width:92px">创建时间</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="7"><div class="empty"><div class="ic">□</div>该账号暂无作品</div></td></tr>'}</tbody>`;
}

/* =========================================================
   母题库
   ========================================================= */
function renderMotifs() {
  const ethnics = ['all', ...new Set(MOTIFS.map(m => m.ethnic))];
  $('motifFilters').innerHTML = ethnics.map(e =>
    `<button data-eth="${e}" class="${S.motifFilter===e?'on':''}">${e==='all'?'全部（'+MOTIFS.length+'）':esc(e)}</button>`).join('');
  $('motifFilters').querySelectorAll('button').forEach(b =>
    b.onclick = () => { S.motifFilter = b.dataset.eth; renderMotifs(); });

  const list = MOTIFS.filter(m => S.motifFilter==='all' || m.ethnic===S.motifFilter);

  $('motifGrid').innerHTML = list.map(m => {
    const imgKey = m.motif_code;
    const refs = ASSETS.filter(a => a.motif_id === m.motif_id);
    return `<div class="mcard" onclick="showMotif(${m.motif_id})">
      ${IMG[imgKey] ? `<img src="${IMG[imgKey]}" alt="${esc(m.name)}">` : ''}
      <div class="body">
        <div class="code">${esc(m.motif_code)}</div>
        <div class="nm">${esc(m.name)}</div>
        <div class="rows">
          ${esc(m.ethnic)} · ${esc(m.category)}<br>
          ${esc(m.era)}
        </div>
        <div class="chips">${(m.keywords||[]).map(k=>`<span class="chip">${esc(k)}</span>`).join('')}</div>
        <div class="foot"><span>512 维向量</span><span>关联 ${refs.length} 件</span></div>
      </div></div>`;
  }).join('');

  const links = [];
  MOTIFS.forEach(m => {
    ASSETS.filter(a => a.motif_id === m.motif_id).forEach(a => {
      links.push(`<tr onclick="showAsset(${a.asset_id})" style="cursor:pointer">
        <td class="mono id">${esc(m.motif_code)}</td>
        <td class="name">${esc(m.name)}</td>
        <td>${esc(a.title)}</td>
        <td>${badgeLevel(a.right_level)}</td>
        <td class="id">${pct(top1Sim(a))}</td>
        <td>${esc(USER_BY_ID[a.owner_id]?.display_name || '')}</td>
      </tr>`);
    });
  });

  $('motifLink').innerHTML = `
    <thead><tr><th style="width:78px">母题编码</th><th style="width:96px">母题名称</th>
    <th>作品</th><th style="width:92px">权属级别</th><th style="width:74px">相似度</th>
    <th style="width:76px">权利人</th></tr></thead>
    <tbody>${links.join('') || '<tr><td colspan="6"><div class="empty">暂无关联</div></td></tr>'}</tbody>`;
}

/* =========================================================
   上传 + AI 分析
   ========================================================= */
const SAMPLES = [
  { key:'sample_derived_butterfly', label:'改编样例：蝴蝶纹改良', expect:'derived' },
  { key:'sample_original_fish',     label:'原创样例：鱼纹挑花',   expect:'original' },
];

function renderUpload() {
  $('samplePick').innerHTML = SAMPLES.map((s,i) =>
    `<button data-i="${i}" class="${S.pick===i?'on':''}">${esc(s.label)}</button>`).join('');
  $('samplePick').querySelectorAll('button').forEach(b =>
    b.onclick = () => { S.pick = Number(b.dataset.i); S.analyzed = null; renderUpload(); });

  const stage = $('uploadStage');
  if (S.pick === null) {
    stage.innerHTML = `<div class="empty"><div class="ic">↑</div>
      选择上方任一样例，模拟一次完整的上传与 AI 分析</div>`;
    return;
  }

  const smp = SAMPLES[S.pick];
  if (!S.analyzed) {
    stage.innerHTML = `
      <div style="display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap">
        <div style="flex:0 0 150px">
          <div style="width:150px;height:150px;border-radius:8px;border:1px solid var(--line);
               background:var(--paper2);display:grid;place-items:center;font-size:12px;color:var(--ink3)">
            ${esc(smp.label)}
          </div>
        </div>
        <div style="flex:1;min-width:200px">
          <div style="font-size:13px;font-weight:600;margin-bottom:6px">${esc(smp.label)}</div>
          <div style="font-size:12px;color:var(--ink3);line-height:1.7">
            已选文件，尚未提交。<br>
            点击下方按钮将依次执行：提取 512 维特征 → 检索母题库 → 给出分级建议。
          </div>
          <button class="btn pri" style="margin-top:14px" onclick="runAnalyze()">上传并触发 AI 分析</button>
        </div>
      </div>`;
    return;
  }

  const r = S.analyzed;
  const lv = LEVEL[r.suggest] || { cn:r.suggest, cls:'draft' };
  const bars = r.topk.map(t => {
    const col = t.similarity >= 0.92 ? '#a8322c' : t.similarity >= 0.80 ? '#b07b1e' : '#3f7a4e';
    return `<div class="simrow">
      <div class="lbl">${esc(t.name)} <span style="color:var(--ink3);font-size:11px">${esc(t.code)}</span></div>
      <div class="bar"><i style="width:${(t.similarity*100).toFixed(1)}%;background:${col}"></i></div>
      <div class="val">${t.similarity.toFixed(4)}</div>
    </div>`;
  }).join('');

  stage.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
      <span class="b ai">AI 建议</span>
      <span class="b ${lv.cls}" style="font-size:12.5px">${lv.cn}</span>
      <span style="font-size:12px;color:var(--ink3)">置信度 ${pct(r.confidence)}</span>
      <span class="tag-live" style="margin-left:auto">流程已执行完毕</span>
    </div>
    <div style="font-size:12.5px;color:var(--ink2);background:var(--paper2);padding:10px 12px;
         border-radius:8px;margin-bottom:14px;line-height:1.7">${esc(r.reason)}</div>
    <div style="font-size:12px;font-weight:600;margin-bottom:6px">相似公有母题 Top-3</div>
    ${bars}
    <table class="tt" style="margin-top:16px">
      <tr><td style="color:var(--ink3);width:110px">特征模型</td><td class="mono">builtin-hog-color-texture@1.0.0</td></tr>
      <tr><td style="color:var(--ink3)">向量维度</td><td>512 维（L2 已归一化）</td></tr>
      <tr><td style="color:var(--ink3)">命中规则</td><td class="mono">SIM_IN_DERIVED_BAND</td></tr>
      <tr><td style="color:var(--ink3)">权属级别</td><td>${badgeLevel(null)} <span style="color:var(--ink3);font-size:11.5px;margin-left:6px">AI 未写入，等待专家终审</span></td></tr>
    </table>
    <div class="box" style="margin-top:14px">
      <div class="bt">下一步：由专家终审</div>
      AI 结论不会直接写入权属字段。数据库层已通过列级 GRANT 禁止传承人角色修改
      <code>right_level</code>，因此这一步只能由专家完成。
    </div>
    <button class="btn" style="margin-top:6px" onclick="S.analyzed=null;renderUpload()">重新选择</button>`;
}

function runAnalyze() {
  const smp = SAMPLES[S.pick];
  const src = SAMPLE_RESULT[smp.key];
  S.analyzed = src;
  renderUpload();
  toast('分析完成：' + (LEVEL[src.suggest]?.cn || src.suggest));
}

/* =========================================================
   专家工作台
   ========================================================= */
function renderWorkbench() {
  const pend = ASSETS.filter(a => a.audit_status === 'pending');
  const approved = ASSETS.filter(a => a.audit_status === 'approved');
  const overrides = ASSETS.reduce((s,a) => s + (a.audits||[]).filter(x => x.is_override).length, 0);

  $('wbStats').innerHTML = [
    ['待审队列', pend.length, '件', 'acc'],
    ['已确权', approved.length, '件', ''],
    ['累计推翻 AI', overrides, '次', ''],
    ['母题总数', MOTIFS.length, '条', ''],
  ].map(([k,v,u,c]) =>
    `<div class="stat ${c}"><div class="k">${k}</div><div class="v">${v}<small>${u}</small></div></div>`).join('');

  const rows = pend.map(a => {
    const sim = top1Sim(a);
    return `<tr>
      <td class="id">${a.asset_id}</td>
      <td class="name">${thumbFor(a)}<span style="margin-left:8px;vertical-align:middle">${esc(a.title)}</span></td>
      <td>${esc(USER_BY_ID[a.owner_id]?.display_name || '')}</td>
      <td>${badgeLevel(a.ai_suggest_level)}<span class="conf">${pct(a.ai_confidence)}</span></td>
      <td class="id">${pct(sim)}</td>
      <td>${a.motif_id ? esc(MOTIF_BY_ID[a.motif_id]?.name||'') : '—'}</td>
      <td style="white-space:nowrap"><button class="btn sm" onclick="openReview(${a.asset_id})">终审</button></td>
    </tr>`;
  }).join('');

  $('wbTable').innerHTML = `
    <thead><tr><th style="width:36px">ID</th><th>作品</th><th style="width:76px">提交人</th>
    <th style="width:124px">AI 建议</th><th style="width:72px">相似度</th>
    <th style="width:88px">Top-1 母题</th><th style="width:72px">操作</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="7"><div class="empty"><div class="ic">✓</div>队列已清空</div></td></tr>'}</tbody>`;
}

/* =========================================================
   监管台账
   ========================================================= */
function renderLedger() {
  const byStatus = {}, byLevel = {};
  let fee = 0, lic = 0;
  ASSETS.forEach(a => {
    byStatus[a.audit_status] = (byStatus[a.audit_status]||0)+1;
    if (a.audit_status === 'approved') byLevel[a.right_level] = (byLevel[a.right_level]||0)+1;
    (a.licenses||[]).forEach(l => { fee += Number(l.fee_cny||0); lic++; });
  });

  $('lgStats').innerHTML = [
    ['资产总数', ASSETS.length, ''],
    ['授权费合计', money(fee), 'acc'],
    ['授权笔数', lic, ''],
    ['已确权', byStatus.approved||0, ''],
    ['待审', byStatus.pending||0, ''],
  ].map(([k,v,c]) => `<div class="stat ${c}"><div class="k">${k}</div><div class="v" style="${String(v).length>7?'font-size:15px':''}">${v}</div></div>`).join('');

  const rows = ASSETS.map(a => `<tr>
    <td class="id">${a.asset_id}</td>
    <td class="name">${esc(a.title)}</td>
    <td>${esc(USER_BY_ID[a.owner_id]?.display_name||'')}</td>
    <td>${badgeLevel(a.right_level)}</td>
    <td>${badgeStatus(a.audit_status)}</td>
    <td>${a.motif_id ? esc(MOTIF_BY_ID[a.motif_id]?.motif_code||'') : '—'}</td>
    <td class="id">${pct(top1Sim(a))}</td>
    <td class="id">${(a.licenses||[]).length}</td>
  </tr>`).join('');

  $('lgTable').innerHTML = `
    <thead><tr><th style="width:34px">ID</th><th>作品名称</th><th style="width:70px">权利人</th>
    <th style="width:90px">权属级别</th><th style="width:72px">状态</th>
    <th style="width:72px">母题</th><th style="width:70px">相似度</th><th style="width:52px">授权</th></tr></thead>
    <tbody>${rows}</tbody>`;

  const lrows = [];
  ASSETS.forEach(a => (a.licenses||[]).forEach(l => {
    lrows.push(`<tr>
      <td class="name">${esc(a.title)}</td>
      <td><span class="b ${l.license_type==='exclusive'?'ori':'pub'}">${LIC[l.license_type]||l.license_type}</span></td>
      <td>${esc(l.licensee_name||'')}</td>
      <td style="color:var(--ink3);font-size:11.5px">${esc(l.scope||'')}</td>
      <td class="id">${money(l.fee_cny)}</td>
      <td class="id">${esc(l.start_date||'')} ~ ${esc(l.end_date||'')}</td>
    </tr>`);
  }));

  $('lgLicenses').innerHTML = `
    <thead><tr><th>作品</th><th style="width:96px">类型</th><th style="width:150px">被授权方</th>
    <th>范围</th><th style="width:82px">费用</th><th style="width:160px">期限</th></tr></thead>
    <tbody>${lrows.join('')}</tbody>`;
}

/* =========================================================
   弹窗：作品 / 母题 / 终审
   ========================================================= */
function openModal(title, html) {
  $('mTitle').textContent = title;
  $('mBody').innerHTML = html;
  $('mask').classList.add('on');
}
function closeModal() { $('mask').classList.remove('on'); }
$('mask').addEventListener('click', e => { if (e.target.id === 'mask') closeModal(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

function showAsset(id) {
  const a = ASSETS.find(x => x.asset_id === id);
  if (!a) return;

  const sim = top1Sim(a);
  const motif = a.motif_id ? MOTIF_BY_ID[a.motif_id] : null;

  let tl = '';
  (a.audits||[]).forEach((au, i) => {
    const acc = au.state === 'approved';
    tl += `<div class="tli ${acc?'acc':''}">
      <div class="tt2">${au.state==='pending'?'提交待审':au.state==='approved'?'专家终审通过':au.state==='rejected'?'审核驳回':'流转'}
        <span class="tm">${esc(au.created_at||'')}${au.decided_at?' → '+esc(au.decided_at):''}</span></div>
      <div class="td">
        AI 建议 ${LEVEL[au.ai_suggest_level]?.cn||'—'}（置信度 ${pct(au.ai_confidence)}）
        ${au.expert_level ? '　专家结论 '+ (LEVEL[au.expert_level]?.cn||'') : ''}
        ${au.is_override ? '　<span class="b rejected">推翻 AI</span>' : ''}
      </div>
      ${au.opinion ? `<div class="td" style="margin-top:4px;color:var(--ink2)">意见：${esc(au.opinion)}</div>` : ''}
    </div>`;
  });

  const lics = (a.licenses||[]).map(l =>
    `<tr><td>${LIC[l.license_type]||l.license_type}</td><td>${esc(l.licensee_name||'')}</td>
     <td class="id">${money(l.fee_cny)}</td></tr>`).join('');

  openModal(a.title, `
    ${IMG['asset_'+a.asset_id] ? `<img src="${IMG['asset_'+a.asset_id]}" style="width:100%;max-width:260px;border-radius:8px;border:1px solid var(--line);display:block;margin:0 auto 14px">` : ''}
    <table class="tt">
      <tr><td style="color:var(--ink3);width:88px">作品编号</td><td class="mono">#${a.asset_id}</td></tr>
      <tr><td style="color:var(--ink3)">权利人</td><td>${esc(USER_BY_ID[a.owner_id]?.display_name||'')}　<span style="color:var(--ink3)">${esc(USER_BY_ID[a.owner_id]?.org_name||'')}</span></td></tr>
      <tr><td style="color:var(--ink3)">AI 建议</td><td>${badgeLevel(a.ai_suggest_level)}<span class="conf">置信度 ${pct(a.ai_confidence)}</span></td></tr>
      <tr><td style="color:var(--ink3)">专家结论</td><td>${a.audit_status==='approved'?badgeLevel(a.right_level):'<span class="b draft">未定</span>'}</td></tr>
      <tr><td style="color:var(--ink3)">流转状态</td><td>${badgeStatus(a.audit_status)}</td></tr>
      <tr><td style="color:var(--ink3)">母题血缘</td><td>${motif ? esc(motif.motif_code)+' '+esc(motif.name)+'　<span style="color:var(--ink3)">相似度 '+pct(sim)+'</span>' : '—'}</td></tr>
      <tr><td style="color:var(--ink3)">创建时间</td><td class="id">${esc(a.created_at||'')}</td></tr>
    </table>

    <div style="font-size:12.5px;font-weight:700;margin:18px 0 8px;color:var(--ind)">创作说明</div>
    <div style="font-size:12.5px;color:var(--ink2);line-height:1.75">${esc(a.description||'—')}</div>

    <div style="font-size:12.5px;font-weight:700;margin:18px 0 8px;color:var(--ind)">溯源链路</div>
    <div class="tl">${tl || '<div class="td" style="color:var(--ink3)">尚无流转记录</div>'}</div>

    ${lics ? `<div style="font-size:12.5px;font-weight:700;margin:18px 0 8px;color:var(--ind)">授权记录</div>
      <table class="tt"><thead><tr><th>类型</th><th>被授权方</th><th style="width:82px">费用</th></tr></thead>
      <tbody>${lics}</tbody></table>` : ''}
  `);
}

function showMotif(id) {
  const m = MOTIF_BY_ID[id];
  if (!m) return;
  const refs = ASSETS.filter(a => a.motif_id === id);

  openModal(m.motif_code + '　' + m.name, `
    ${IMG[m.motif_code] ? `<img src="${IMG[m.motif_code]}" style="width:100%;max-width:230px;border-radius:8px;border:1px solid var(--line);display:block;margin:0 auto 14px">` : ''}
    <table class="tt">
      <tr><td style="color:var(--ink3);width:88px">编码</td><td class="mono">${esc(m.motif_code)}</td></tr>
      <tr><td style="color:var(--ink3)">名称</td><td>${esc(m.name)}</td></tr>
      <tr><td style="color:var(--ink3)">民族</td><td>${esc(m.ethnic)}</td></tr>
      <tr><td style="color:var(--ink3)">类别</td><td>${esc(m.category)}</td></tr>
      <tr><td style="color:var(--ink3)">年代</td><td>${esc(m.era)}</td></tr>
      <tr><td style="color:var(--ink3)">流传地域</td><td>${esc(m.region||'—')}</td></tr>
      <tr><td style="color:var(--ink3)">关键词</td><td>${(m.keywords||[]).map(k=>`<span class="chip">${esc(k)}</span>`).join(' ')}</td></tr>
      <tr><td style="color:var(--ink3)">向量</td><td>512 维，已 L2 归一化</td></tr>
    </table>

    <div style="font-size:12.5px;font-weight:700;margin:18px 0 8px;color:var(--ind)">母题说明</div>
    <div style="font-size:12.5px;color:var(--ink2);line-height:1.75">${esc(m.description||'—')}</div>

    <div style="font-size:12.5px;font-weight:700;margin:18px 0 8px;color:var(--ind)">关联作品（${refs.length}）</div>
    ${refs.length ? `<table class="tt"><thead><tr><th>作品</th><th style="width:92px">权属</th><th style="width:72px">相似度</th></tr></thead>
      <tbody>${refs.map(a=>`<tr><td class="name">${esc(a.title)}</td><td>${badgeLevel(a.right_level)}</td>
      <td class="id">${pct(top1Sim(a))}</td></tr>`).join('')}</tbody></table>`
      : '<div style="font-size:12.5px;color:var(--ink3)">暂无作品引用此母题</div>'}
  `);
}

/* 终审弹窗：可交互 */
let reviewPick = null;
function openReview(id) {
  const a = ASSETS.find(x => x.asset_id === id);
  if (!a) return;
  reviewPick = a.ai_suggest_level;
  const sim = top1Sim(a);
  const motif = a.motif_id ? MOTIF_BY_ID[a.motif_id] : null;

  openModal('专家终审 · ' + a.title, `
    <div style="font-size:12.5px;color:var(--ink3);margin-bottom:12px">
      AI 建议：${LEVEL[a.ai_suggest_level]?.cn||'—'}（置信度 ${pct(a.ai_confidence)}）
      ${motif ? '　Top-1 母题：'+esc(motif.name)+'　相似度 '+pct(sim) : ''}
    </div>
    <div style="font-size:12.5px;font-weight:700;margin-bottom:6px">选择终审结论</div>
    <div class="pick" id="pickBox">
      ${['public','derived','original'].map(k =>
        `<button data-lv="${k}" class="${k===reviewPick?'on':''}">
          <span class="pl">${LEVEL[k].cn}</span>
          <span class="pd">${k==='public'?'≥ 0.92':k==='derived'?'0.80 – 0.92':'< 0.80'}</span>
        </button>`).join('')}
    </div>
    <div id="ovTip" style="font-size:12px;margin:10px 0"></div>
    <textarea id="reviewNote" placeholder="终审意见（可选）" style="width:100%;min-height:70px;border:1px solid var(--line);
      border-radius:8px;padding:9px;font-family:inherit;font-size:12.5px;resize:vertical"></textarea>
    <div style="display:flex;gap:8px;margin-top:14px">
      <button class="btn pri" onclick="submitReview(${a.asset_id})">提交终审</button>
      <button class="btn" onclick="closeModal()">取消</button>
    </div>
    <div class="box grey" style="margin-top:14px;font-size:12px">
      <div class="bt">这一操作在真实系统中会做什么</div>
      开启事务 → <code>SELECT FOR UPDATE</code> 锁行 → 校验状态为待审 → 写入权属与状态 →
      写审核流水 → 校验行数 → 回读系统日志 → 提交。六步全成功或全回滚。
      若结论与 AI 不同，<code>is_override</code> 记为真。
    </div>
  `);

  $('pickBox').querySelectorAll('button').forEach(b => b.onclick = () => {
    reviewPick = b.dataset.lv;
    $('pickBox').querySelectorAll('button').forEach(x => x.classList.toggle('on', x.dataset.lv === reviewPick));
    updateOvTip(a);
  });
  updateOvTip(a);
}

function updateOvTip(a) {
  const same = reviewPick === a.ai_suggest_level;
  $('ovTip').innerHTML = same
    ? '<span style="color:var(--ok)">与 AI 建议一致，直接采纳</span>'
    : '<span style="color:var(--cin)">与 AI 建议不同，将记为「推翻 AI」，需在意见中说明理由</span>';
}

function submitReview(id) {
  const a = ASSETS.find(x => x.asset_id === id);
  if (!a) return;
  const note = ($('reviewNote')?.value || '').trim();
  const override = reviewPick !== a.ai_suggest_level;

  // 在内存里改（仅本次会话，刷新即恢复——页面是静态的）
  a.right_level = reviewPick;
  a.audit_status = 'approved';
  a.version_no = (a.version_no||0) + 1;
  a.decided_at = new Date().toISOString().slice(0,10);
  a.audits = a.audits || [];
  a.audits.push({
    state:'approved', ai_suggest_level:a.ai_suggest_level, ai_confidence:a.ai_confidence,
    expert_level: reviewPick, expert_id: 2, is_override: override,
    similarity: top1Sim(a), created_at: a.decided_at, decided_at: a.decided_at,
    opinion: note || (override ? '专家综合判断后调整档位。' : '与 AI 建议一致，予以采纳。'),
  });

  closeModal();
  renderPane();
  toast(override ? '已终审（推翻 AI），状态变为已确权' : '已终审，状态变为已确权');
}

/* =========================================================
   样例分析结果（来自 docs/verify/run_result.json 的真实输出）
   ========================================================= */
const SAMPLE_RESULT = {
  sample_derived_butterfly: {
    suggest: 'derived', confidence: 0.4372,
    reason: '与公有母题【蝴蝶纹】相似度 0.9182 落在 [0.80, 0.92) 区间，存在明显借鉴但不构成整体复制。建议认定为改编衍生，请专家核验改编幅度。',
    topk: [
      { code:'MO-001', name:'蝴蝶纹',    similarity: 0.9182 },
      { code:'MO-003', name:'回纹',      similarity: 0.4255 },
      { code:'MO-002', name:'缠枝莲纹',  similarity: 0.3743 },
    ],
  },
  sample_original_fish: {
    suggest: 'original', confidence: 0.7417,
    reason: '与最近公有母题【缠枝莲纹】相似度 0.6036 低于 0.80 阈值，未发现与现存公有母题的实质重合。建议认定为原创。',
    topk: [
      { code:'MO-002', name:'缠枝莲纹', similarity: 0.6036 },
      { code:'MO-003', name:'回纹',     similarity: 0.4218 },
      { code:'MO-001', name:'蝴蝶纹',   similarity: 0.3905 },
    ],
  },
};

/* =========================================================
   左侧目录联动
   ========================================================= */
function renderToc() {
  // 纯演示版没有左侧说明栏，目录容器不存在，直接跳过。
  if (!$('toc')) return;
  const items = [
    ['c1','一、文档说明'], ['c2','二、项目概述'], ['c3','三、系统设计'],
    ['c4','四、核心机制'], ['c5','五、验证情况'], ['c6','六、交付物'], ['c7','七、验收清单'],
  ];
  $('toc').innerHTML = items.map(([id,t]) => `<button data-c="${id}">${t}</button>`).join('');
  $('toc').querySelectorAll('button').forEach(b => b.onclick = () => {
    const el = $(b.dataset.c);
    if (el) el.scrollIntoView({behavior:'smooth', block:'start'});
    $('toc').querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
    // 章节 → 右侧对应模块
    const map = { c1:'mine', c2:'motifs', c3:'ledger', c4:'upload', c5:'ledger', c6:'upload', c7:'mine' };
    if (S.role && TABS.filter(t=>t.roles.includes(S.role)).some(t=>t.id===map[b.dataset.c])) {
      S.tab = map[b.dataset.c]; renderTabs(); renderPane();
    }
  });
}

/* =========================================================
   启动
   ========================================================= */
function init() {
  $('roles').innerHTML = [
    ['heritor','传承人'], ['expert','评审专家'], ['admin','监管管理员'],
  ].map(([r,label]) => `<button data-role="${r}">${label}</button>`).join('');
  $('roles').querySelectorAll('button').forEach(b => b.onclick = () => setRole(b.dataset.role));

  $('resetBtn').onclick = () => {
    ASSETS = JSON.parse(JSON.stringify(ASSETS_ORIGINAL));
    S.pick = null; S.analyzed = null;
    renderPane();
    toast('演示数据已恢复初始状态');
  };

  renderToc();
  setRole('heritor');
}
init();
</script>
</body>
</html>
"""


def strip_doc_pane(html: str) -> str:
    """剥掉左侧说明栏，得到"纯演示版"。

    做法是删掉 <!--DOC_START--> 与 <!--DOC_END--> 之间的整块 HTML，再收掉
    #doc 的宽度规则和移动端那条 #doc 覆盖。右侧 #demo 本来就是 flex:1，
    左栏一消失它自动占满，不需要额外加宽。

    为什么不直接把代码删掉：带说明栏的版本仍要保留（讲项目时有用），
    一份脚本出两个版本比维护两份 HTML 可靠。
    """
    start, end = html.find("<!--DOC_START"), html.find("<!--DOC_END-->")
    if start == -1 or end == -1:
        raise RuntimeError("找不到 DOC 区块标记，模板可能被改坏了")
    end = html.find("\n", end)
    html = html[:start] + html[end + 1:]

    # 左栏的宽度与滚动条规则，留着无害但会让人误以为还有第二栏
    html = re.sub(r"#doc\{[^}]*\}\n", "", html)
    html = re.sub(r"#doc::-webkit-scrollbar[^\n]*\n", "", html)
    html = re.sub(r"  #doc\{[^}]*\}\n", "", html)

    # 顶栏副标题里的「交付演示」改成「功能演示」，因为不再附说明文档
    html = html.replace("ICH DIGITAL ASSET LEDGER · 交付演示",
                        "ICH DIGITAL ASSET LEDGER · 功能演示")
    return html


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = build_payload()

    html = HTML_TEMPLATE.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))

    # 默认只出纯演示版：只保留可操作的功能界面，不要左侧说明文字。
    # 需要连说明栏一起出，加 --with-doc 参数。
    if "--with-doc" not in sys.argv:
        html = strip_doc_pane(html)

    OUT_FILE.write_text(html, encoding="utf-8")

    size_mb = OUT_FILE.stat().st_size / 1024 / 1024
    imgs = len(payload["images"])
    print(f"已生成：{OUT_FILE}")
    print(f"体积：{size_mb:.2f} MB　内嵌图片：{imgs} 张")
    print(f"账号 {len(payload['seed']['users'])} / 母题 {len(payload['seed']['motifs'])} "
          f"/ 作品 {len(payload['seed']['assets'])}")
    print("形态：" + ("含左侧说明栏" if "--with-doc" in sys.argv else "纯演示（无左侧说明）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
