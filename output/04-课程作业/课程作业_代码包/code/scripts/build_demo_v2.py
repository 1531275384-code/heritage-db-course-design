"""
生成「互动演示版」单文件页面：带拖动滑块验证的登录页 + 可操作的应用界面。

用法：
    .venv/Scripts/python.exe scripts/build_demo_v2.py

产出：
    output/01-交付物/演示页面_互动版.html

设计决定：

1. 样式与脚本放在 scripts/v2_assets/ 下的独立文件里，不塞进 Python 字符串。
   原因很实际：JS 里大量使用模板字符串和 ${}，一旦写成 Python 三引号，
   每一处反引号、花括号都要盯着转义，改一次错一次。
   独立文件还能被编辑器和 linter 正常处理。

2. 数据仍然从 data/seed_demo.json 读取并内嵌，图片转 base64。
   页面在 file:// 协议下双击可开，断网零请求。

3. AI 相关数字一律取自种子中算法真实写入的记录，页面里不编造相似度。
   演示页只做"呈现"，不做"计算"——计算发生在后端，这一点必须诚实。
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ASSETS_DIR = Path(__file__).resolve().parent / "v2_assets"

SEED_FILE = BASE / "data" / "seed_demo.json"
OUT_DIR = BASE / "output" / "01-交付物"
OUT_FILE = OUT_DIR / "演示页面_互动版.html"

# 页面的四个组成部件，顺序即拼接顺序
PARTS = ["login.html", "app.html"]
SCRIPTS = ["app.js", "pages1.js", "pages2.js"]


def b64(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def build_payload() -> dict:
    seed = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    images: dict[str, str] = {}

    # 母题参考图：seed 里的 reference_img 是相对 data/ 的路径
    for m in seed["motifs"]:
        p = BASE / "data" / m["reference_img"]
        if p.exists():
            images[m["motif_code"]] = b64(p)

    # 作品图
    for a in seed["assets"]:
        p = BASE / "data" / "uploads" / a["file_path"]
        if p.exists():
            images[f"asset_{a['asset_id']}"] = b64(p)

    # days_ago 转成可读日期，前端不再做日期算术
    today = datetime.now().date()
    for a in seed["assets"]:
        for k, out in (("created_days_ago", "created_at"),
                       ("decided_days_ago", "decided_at")):
            if a.get(k) is not None:
                a[out] = str(today - timedelta(days=a[k]))
        for au in a.get("audits", []):
            for k, out in (("days_ago", "created_at"),
                           ("decided_days_ago", "decided_at")):
                if au.get(k) is not None:
                    au[out] = str(today - timedelta(days=au[k]))
        for l in a.get("licenses", []):
            for k, out in (("start_days_ago", "start_date"),
                           ("end_days_ago", "end_date")):
                if l.get(k) is not None:
                    l[out] = str(today - timedelta(days=l[k]))

    return {"seed": seed, "images": images,
            "built_at": datetime.now().strftime("%Y-%m-%d %H:%M")}


def read(name: str) -> str:
    p = ASSETS_DIR / name
    if not p.exists():
        raise FileNotFoundError(f"缺少资源文件：{p}")
    return p.read_text(encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = build_payload()

    seed_json = json.dumps(payload["seed"], ensure_ascii=False)
    img_json = json.dumps(payload["images"], ensure_ascii=False)

    # </script> 出现在 JSON 字符串里会提前闭合脚本块，必须转义
    def safe(s: str) -> str:
        return s.replace("</", "<\\/")

    css = read("style.css")
    body = "\n".join(read(n) for n in PARTS)
    js = "\n\n".join(read(n) for n in SCRIPTS)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>非遗数字资产分级确权与溯源系统 · 互动演示</title>
<meta name="generator" content="build_demo_v2.py @ {payload['built_at']}">
<style>
{css}
</style>
</head>
<body>

{body}

<script>
/* 种子数据与图片，由构建脚本内嵌。窗口对象上的两个变量是 JS 层唯一的外部依赖。 */
window.__SEED__ = {safe(seed_json)};
window.__IMG__ = {safe(img_json)};
window.__BUILT__ = "{payload['built_at']}";
</script>

<script>
{js}
</script>

</body>
</html>
"""

    OUT_FILE.write_text(html, encoding="utf-8")

    size_mb = OUT_FILE.stat().st_size / 1024 / 1024
    seed = payload["seed"]
    print(f"已生成：{OUT_FILE}")
    print(f"体积：{size_mb:.2f} MB　内嵌图片：{len(payload['images'])} 张")
    print(f"账号 {len(seed['users'])} / 母题 {len(seed['motifs'])} / 作品 {len(seed['assets'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
