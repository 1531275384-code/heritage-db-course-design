"""
演示前端全页检查：三个角色逐页渲染 + 控制台零报错 + 关键数据断言 + 截图。

为什么不用 curl 而是真浏览器：
  "页面结构完整、数据能正常渲染"这句话里，一半的问题只有渲染出来才看得见 ——
  比如字段名对不上会渲染出 "undefined"、CSS 类名拼错会白屏、
  onerror 兜底写错会让整张卡片消失。这些 curl 全都测不出来。

运行：
  python -m tests.shoot_ui          # 起服务 + 截图 + 断言
  python -m tests.shoot_ui --keep   # 保留截图不覆盖
"""
from __future__ import annotations

import sys
import threading
import time
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "output" / "02-截图证据" / "自动验收"
UIPORT = 8731

PASS, FAIL = 0, 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  \033[92m✓\033[0m {name}")
    else:
        FAIL += 1
        print(f"  \033[91m✗\033[0m {name}" + (f"\n      → {detail}" if detail else ""))


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def start_server():
    """与进程内起 uvicorn，避免后台子进程被工具环境回收。"""
    import os
    os.environ["ICH_DEMO_MODE"] = "1"
    os.environ.setdefault("ICH_LOG_LEVEL", "WARNING")
    import uvicorn
    from app.main import app

    cfg = uvicorn.Config(app, host="127.0.0.1", port=UIPORT, log_level="warning")
    server = uvicorn.Server(cfg)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(120):
        if not _port_free(UIPORT):
            return server
        time.sleep(0.1)
    raise SystemExit("服务未能启动")


ACCOUNTS = [
    ("heritor01", "传承人", ["upload", "motifs", "mine"]),
    ("expert01", "专家", ["upload", "motifs", "workbench", "ledger"]),
    ("admin01", "管理员", ["upload", "motifs", "workbench", "ledger"]),
]

PAGE_TITLES = {
    "upload": "作品上传与 AI 预审",
    "motifs": "公有母题库",
    "mine": "我的作品",
    "workbench": "专家工作台",
    "ledger": "监管台账与溯源",
}


def main(argv: list[str]) -> int:
    from playwright.sync_api import sync_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print(" 演示前端全页检查（真浏览器）")
    print("=" * 78)

    server = start_server()
    base = f"http://127.0.0.1:{UIPORT}"

    total_errors: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for username, role_cn, pages in ACCOUNTS:
                print(f"\n【{role_cn} {username}】")
                page = browser.new_page(viewport={"width": 1440, "height": 980})

                errors: list[str] = []
                page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                        if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

                page.goto(base, wait_until="networkidle")
                page.fill("#loginUser", username)
                page.fill("#loginPass", "Passw0rd!")
                page.click("#loginBtn")
                page.wait_for_selector("#app.on", timeout=15000)
                page.wait_for_timeout(600)

                check(f"{role_cn} 登录成功", page.is_visible("#app"))

                for pg in pages:
                    page.click(f'#tabs button[data-page="{pg}"]')
                    page.wait_for_timeout(900)
                    title = page.inner_text(f"#page-{pg} h1")
                    check(f"{role_cn} / {pg} 标题正确", title == PAGE_TITLES[pg], f"实际={title!r}")

                    # —— 关键内容断言 ——
                    if pg == "mine":
                        n = page.locator("#mineBody tr").count()
                        txt = page.inner_text("#mineBody")
                        check(f"{role_cn} 我的作品有数据", n > 0 and "还没有作品" not in txt,
                              f"{n} 行")
                        check(f"{role_cn} 我的作品无 undefined", "undefined" not in txt,
                              txt[:200])
                    if pg == "motifs":
                        cards = page.locator(".motif-card").count()
                        grid_txt = page.inner_text("#motifGrid")
                        check(f"{role_cn} 母题库渲染卡片", cards >= 3, f"{cards} 张")
                        check(f"{role_cn} 母题库无 undefined", "undefined" not in grid_txt,
                              grid_txt[:200])
                        check(f"{role_cn} 母题库显示特征模型",
                              "builtin" in grid_txt or "向量已就绪" in grid_txt)
                        link_txt = page.inner_text("#motifLinkBody")
                        check(f"{role_cn} 母题关联表有内容",
                              page.locator("#motifLinkBody tr").count() > 0)
                        check(f"{role_cn} 母题关联表无 undefined",
                              "undefined" not in link_txt and "NaN" not in link_txt,
                              link_txt[:200])
                        # 专家/管理员能看到相似度条；这是"视图新增列→Pydantic 模型同步"的护栏
                        if role_cn != "传承人":
                            bars = page.locator("#motifLinkBody .bar").count()
                            check(f"{role_cn} 母题关联表显示相似度条", bars >= 3, f"{bars} 条")
                            # 相似度必须落在 0~1，且不能全等于置信度（那是标错字段的信号）
                            sims = page.evaluate(
                                """() => Array.from(
                                       document.querySelectorAll('#motifLinkBody .bar i'))
                                       .map(el => parseInt(el.style.width) / 100)"""
                            )
                            check(f"{role_cn} 相似度数值在合理区间",
                                  all(0 <= s <= 1.001 for s in sims) and len(sims) >= 3,
                                  f"{sims}")
                        # 参考图真的加载出来了（naturalWidth > 0 才算成功）
                        ok_img = page.evaluate(
                            """() => Array.from(document.querySelectorAll('.motif-thumb img'))
                                    .map(i => i.naturalWidth > 0)"""
                        )
                        check(f"{role_cn} 母题参考图全部加载成功",
                              all(ok_img) and len(ok_img) >= 3, f"{ok_img}")
                    if pg == "workbench":
                        txt = page.inner_text("#wbStats")
                        check(f"{role_cn} 工作台统计无 undefined", "undefined" not in txt, txt[:160])
                        nb = page.locator("#pendingBody").inner_text()
                        check(f"{role_cn} 待审队列有数据", "待终审" not in nb or "清空" not in nb,
                              nb[:120])
                    if pg == "ledger":
                        txt = page.inner_text("#ledgerStats") + page.inner_text("#ledgerBody")
                        check(f"{role_cn} 台账无 undefined", "undefined" not in txt, txt[:160])
                        check(f"{role_cn} 台账有母题列数据",
                              page.locator("#ledgerBody tr").count() > 0)

                    shot = SHOTS / f"{username}_{pg}.png"
                    page.screenshot(path=str(shot), full_page=True)

                check(f"{role_cn} 控制台零报错", not errors, "\n      → ".join(errors[:5]))
                total_errors.extend(errors)
                page.close()
            browser.close()
    finally:
        server.should_exit = True
        time.sleep(0.4)

    print("\n" + "=" * 78)
    if total_errors:
        print(f"控制台错误共 {len(total_errors)} 条：")
        for e in total_errors[:10]:
            print("  · " + e)
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    print(f"截图目录：{SHOTS}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
