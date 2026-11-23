"""
互动演示版页面验收（Playwright 真实浏览器）。

用法：
    .venv/Scripts/python.exe -m tests.check_demo_v2

与 check_demo_page.py 的区别：那份验的是「文档+演示」双栏版，
这份验的是「拖动登录 + 纯应用界面」的互动版。两者互不替代。

断言原则：不只看页面有没有渲染，要看「值对不对」。
例如授权类型必须显示中文而不是 exclusive 这类枚举原值，
终审提交后待审数必须真的减一，而不是只弹个提示。
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

from playwright.async_api import async_playwright

BASE = pathlib.Path(__file__).resolve().parent.parent
PAGE = BASE / "output" / "01-交付物" / "演示页面_互动版.html"
SHOTS = BASE / "output" / "02-截图证据" / "互动版验收"

PASS = 0
FAIL = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}　{detail}")


async def drag_slider(page, ratio: float = 1.0) -> None:
    """把登录滑块从左侧拖到 ratio 位置。ratio<1 时用来验证"没拖到底不算通过"。"""
    knob = await page.query_selector("#slKnob")
    bb = await knob.bounding_box()
    track = await (await page.query_selector("#slider")).bounding_box()
    x0 = bb["x"] + bb["width"] / 2
    y = bb["y"] + bb["height"] / 2
    travel = track["width"] - bb["width"] - 6
    await page.mouse.move(x0, y)
    await page.mouse.down()
    steps = 14
    for i in range(1, steps + 1):
        await page.mouse.move(x0 + travel * ratio * i / steps, y)
        await page.wait_for_timeout(8)
    await page.mouse.up()
    await page.wait_for_timeout(200)


async def login(page, acc_index: int = 2) -> None:
    await page.click(f"#accGrid .acc:nth-child({acc_index})")
    await drag_slider(page, 1.0)
    await page.click("#lgSubmit")
    await page.wait_for_timeout(650)


async def main() -> int:
    if not PAGE.exists():
        print(f"找不到页面：{PAGE}")
        print("请先运行：python scripts/build_demo_v2.py")
        return 1

    SHOTS.mkdir(parents=True, exist_ok=True)
    url = PAGE.as_uri()
    console_errs: list[str] = []
    page_errs: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errs.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errs.append(str(e)))

        await page.goto(url)
        await page.wait_for_timeout(700)

        # ---------- 1. 登录页 ----------
        print("\n[1] 登录页与拖动验证")
        check("非遗" in (await page.title()), "页面标题正确", await page.title())
        check(await page.is_visible("#login"), "登录页显示")
        check(await page.eval_on_selector_all("#accGrid .acc", "e => e.length") == 4,
              "4 个候选账号")

        # 只选账号不拖滑块 → 不能登录
        await page.click("#accGrid .acc:nth-child(1)")
        await page.wait_for_timeout(150)
        check(await page.get_attribute("#lgSubmit", "disabled") is not None,
              "未拖滑块时登录按钮禁用")

        # 拖不到底也不通过
        await drag_slider(page, 0.5)
        check(await page.get_attribute("#lgSubmit", "disabled") is not None,
              "滑块未拖到底不通过")
        check("按住滑块" in (await page.text_content("#slTxt")),
              "未拖到底时滑块回弹提示", await page.text_content("#slTxt"))

        # 拖到底 → 通过
        await drag_slider(page, 1.0)
        check((await page.text_content("#slTxt")).strip() == "验证通过",
              "拖到底后验证通过", await page.text_content("#slTxt"))
        check(await page.get_attribute("#lgSubmit", "disabled") is None,
              "验证后登录按钮可用")

        # 错误密码
        await page.fill("#pw", "wrong-pass")
        await page.click("#lgSubmit")
        await page.wait_for_timeout(250)
        check(await page.is_visible("#lgErr"), "错误密码给出提示")
        check(await page.is_visible("#login"), "错误密码不放行")

        await page.screenshot(path=str(SHOTS / "01-login.png"))

        # ---------- 2. 登录并进入应用 ----------
        print("\n[2] 登录进入应用")
        await page.fill("#pw", "Passw0rd!")
        await page.click("#lgSubmit")
        await page.wait_for_timeout(700)
        check(await page.is_visible("#app"), "应用主界面出现")
        check(not await page.is_visible("#login"), "登录页已隐藏")
        name = (await page.text_content("#meNm")).strip()
        check(name == "杨秀英", "顶部显示当前账号", name)
        check((await page.text_content("#crumb")).find("作品管理") >= 0,
              "传承人默认落地到作品管理", await page.text_content("#crumb"))
        await page.screenshot(path=str(SHOTS / "02-app-assets.png"))

        # ---------- 3. 角色可见性 ----------
        print("\n[3] 角色可见性与数据隔离")
        visible: int = await page.eval_on_selector_all(
            "#nav button[data-pg]", "els => els.filter(e => e.style.display !== 'none').length")
        check(visible == 6, "传承人可见 6 个入口（无台账/工作台/审计）", f"实际 {visible}")

        rows = await page.eval_on_selector_all("#aList tbody tr", "e => e.length")
        check(rows == 3, "杨秀英名下 3 件作品", f"实际 {rows}")

        # ---------- 4. 筛选与多选 ----------
        print("\n[4] 作品筛选与批量选择")
        await page.click('#aLv button[data-v="original"]')
        await page.wait_for_timeout(280)
        orig_rows = await page.eval_on_selector_all("#aList tbody tr", "e => e.length")
        check(orig_rows == 1, "筛「原创」后剩 1 件", f"实际 {orig_rows}")

        await page.click('#aLv button[data-v="all"]')
        await page.wait_for_timeout(250)

        await page.fill("#aQ", "蝴蝶")
        await page.wait_for_timeout(280)
        q_rows = await page.eval_on_selector_all("#aList tbody tr", "e => e.length")
        check(q_rows == 1, "搜索「蝴蝶」命中 1 件", f"实际 {q_rows}")
        await page.fill("#aQ", "")
        await page.wait_for_timeout(250)

        # 多选
        await page.click("#aList tbody tr:nth-child(1) [data-ck]")
        await page.click("#aList tbody tr:nth-child(2) [data-ck]")
        await page.wait_for_timeout(280)
        bulk_text = (await page.text_content("#aBulkCnt")).strip()
        check(bulk_text == "已选 2 项", "多选后显示已选数量", bulk_text)
        check(await page.is_visible("#aBulk"), "批量操作条出现")

        await page.click('#aBulk [data-bulk="clear"]')
        await page.wait_for_timeout(220)
        check(not await page.is_visible("#aBulk"), "取消选择后操作条隐藏")

        # ---------- 5. 作品详情抽屉 ----------
        print("\n[5] 作品详情抽屉")
        await page.click("#aList tbody tr:nth-child(1)")
        await page.wait_for_timeout(350)
        check(await page.is_visible("#drawer"), "抽屉打开")
        dtext = await page.text_content("#dBody")
        check("AI 建议" in dtext and "专家结论" in dtext, "抽屉内含 AI 与专家并列对比")
        check("溯源链" in dtext, "抽屉内含溯源链")
        check(await page.is_visible("#dFoot"), "抽屉底部操作区存在")
        await page.click("#dClose")
        await page.wait_for_timeout(320)
        # 抽屉是靠 transform 移出屏幕关闭的，元素本身仍占位，
        # 所以不能用 is_visible 判断，要看 .show 类是否已移除。
        drawer_closed = await page.evaluate(
            "() => !document.getElementById('drawer').classList.contains('show')")
        check(drawer_closed, "抽屉可关闭")

        # ---------- 6. 母题库 ----------
        print("\n[6] 母题库筛选")
        await page.click('#nav button[data-pg="motifs"]')
        await page.wait_for_timeout(350)
        cards = await page.eval_on_selector_all("#mList .mcard", "e => e.length")
        check(cards == 3, "母题库 3 张卡片", f"实际 {cards}")
        await page.click('#mEthnic [data-eth="苗族"]')
        await page.wait_for_timeout(280)
        eth_cards = await page.eval_on_selector_all("#mList .mcard", "e => e.length")
        check(eth_cards == 1, "按民族「苗族」筛选后剩 1 张", f"实际 {eth_cards}")
        await page.click('#mEthnic [data-eth="苗族"]')
        await page.wait_for_timeout(250)
        await page.screenshot(path=str(SHOTS / "03-motifs.png"))

        # ---------- 7. 切换为专家 ----------
        print("\n[7] 换身份：专家视角")
        await page.click("#meBox")
        await page.wait_for_timeout(200)
        await page.click('#meMenu [data-act="logout"]')
        await page.wait_for_timeout(350)
        check(await page.is_visible("#login"), "退出后回到登录页")
        check(await page.get_attribute("#lgSubmit", "disabled") is not None,
              "退出后滑块状态已重置")

        await login(page, 2)   # 周文渊 专家
        visible2: int = await page.eval_on_selector_all(
            "#nav button[data-pg]", "els => els.filter(e => e.style.display !== 'none').length")
        check(visible2 == 9, "专家可见全部 9 个入口", f"实际 {visible2}")

        await page.click('#nav button[data-pg="assets"]')
        await page.wait_for_timeout(320)
        all_rows = await page.eval_on_selector_all("#aList tbody tr", "e => e.length")
        check(all_rows == 8, "专家可见全部 8 件作品", f"实际 {all_rows}")

        # 概览页的分级分布必须真的画出宽度。
        # 这里专门查一次：.fill 曾是 span 默认 inline，宽高算作 0，整条填色消失，
        # 而页面上数字全对，光看文字发现不了。
        await page.click('#nav button[data-pg="dashboard"]')
        await page.wait_for_timeout(400)
        bar_w = await page.evaluate("""() => {
          const f = document.querySelector('#dashDist .bar .fill');
          return f ? f.getBoundingClientRect().width : -1;
        }""")
        check(bar_w > 4, "分级分布条形有实际宽度", f"fill 宽 {bar_w}px")

        # ---------- 8. 台账数值 ----------
        print("\n[8] 监管台账")
        await page.click('#nav button[data-pg="ledger"]')
        await page.wait_for_timeout(400)
        stats = await page.eval_on_selector_all("#ledgerStats .stat .v", "e => e.map(x => x.textContent)")
        check(any("¥131,600" in s for s in stats), "授权金额合计 ¥131,600", str(stats))

        types = await page.eval_on_selector_all(
            "#lList tbody tr td:nth-child(5)", "e => e.map(x => x.textContent.trim())")
        check(all(t in ("独占授权", "非独占授权", "展示授权", "衍生开发授权") for t in types),
              "授权类型全部显示为中文（无枚举原值）", str(types))

        tfoot = await page.text_content("#lList tfoot")
        check("¥131,600" in tfoot, "合计行金额正确", tfoot.strip()[:40])
        await page.screenshot(path=str(SHOTS / "04-ledger.png"))

        # ---------- 9. 终审与推翻 ----------
        print("\n[9] 终审与推翻 AI")
        await page.click('#nav button[data-pg="review"]')
        await page.wait_for_timeout(400)
        before = (await page.text_content("#rvCount")).strip()
        check(before == "2 件待审", "待审队列 2 件", before)

        await page.click("#rvList .rv-item:nth-child(1)")
        await page.wait_for_timeout(350)
        opts = await page.eval_on_selector_all("#rvBody [data-lv]", "e => e.length")
        check(opts == 4, "终审提供 4 个档位选项", f"实际 {opts}")
        check(await page.get_attribute("#btnApprove", "disabled") is not None,
              "未选档位时不能提交")

        await page.click('#rvBody [data-lv="original"]')
        await page.wait_for_timeout(300)
        check(await page.is_visible("#rvBody .note-box.acc"), "选不同档位时提示将推翻 AI")

        # 不填意见直接提交 → 拦下
        await page.click("#btnApprove")
        await page.wait_for_timeout(300)
        check((await page.text_content("#rvCount")).strip() == "2 件待审",
              "未填意见时提交被拦下")

        await page.fill("#rvOpinion", "相似度 0.8500 处于边界，复核后判定为原创")
        await page.click("#btnApprove")
        await page.wait_for_timeout(600)
        after = (await page.text_content("#rvCount")).strip()
        check(after == "1 件待审", "提交后待审减为 1 件", after)

        # 台账里应能查到「推翻」标记
        await page.click('#nav button[data-pg="audit"]')
        await page.wait_for_timeout(380)
        audit_text = await page.text_content("#auList")
        check("推翻" in audit_text, "审计日志记录了推翻事件")
        await page.screenshot(path=str(SHOTS / "05-audit.png"))

        # ---------- 10. 上传分析 ----------
        print("\n[10] 上传与 AI 分析")
        await page.click('#nav button[data-pg="upload"]')
        await page.wait_for_timeout(350)
        check(await page.get_attribute("#btnAnalyze", "disabled") is not None,
              "未选样例时分析按钮禁用")

        await page.click("#upSamples .sample:nth-child(1)")
        await page.wait_for_timeout(280)
        await page.click("#btnAnalyze")
        await page.wait_for_timeout(2700)

        res = await page.text_content("#upResult")
        check("AI 建议档位" in res, "结果含 AI 建议档位")
        check("按当前阈值推算" in res, "结果含阈值推算（可对比）")
        check("AI 没有写入权属" in res, "结果明确声明 AI 未写权属")
        check("余弦相似度" in res, "结果含真实相似度")
        await page.screenshot(path=str(SHOTS / "06-upload.png"))

        # ---------- 11. 溯源图谱 ----------
        print("\n[11] 溯源图谱")
        await page.click('#nav button[data-pg="trace"]')
        await page.wait_for_timeout(420)
        rects = await page.eval_on_selector_all("#tGraph svg rect", "e => e.length")
        check(rects >= 4, "图谱至少 4 个节点", f"实际 {rects}")
        nodes = await page.eval_on_selector_all("#tNodes tbody tr", "e => e.length")
        check(nodes >= 3, "节点明细至少 3 行", f"实际 {nodes}")
        await page.screenshot(path=str(SHOTS / "07-trace.png"))

        # ---------- 12. 设置与阈值联动 ----------
        print("\n[12] 演示设置")
        await page.click('#nav button[data-pg="settings"]')
        await page.wait_for_timeout(350)
        dp = await page.input_value("#defPage")
        check(dp != "", "默认打开页下拉有值（不为空）", f"当前值 {dp!r}")
        opt_count = await page.eval_on_selector_all("#defPage option", "e => e.length")
        check(opt_count >= 6, "默认页下拉选项充分", f"实际 {opt_count}")

        # 关掉置信度 → 作品表格不再显示百分比
        await page.click('#setRow [data-cfg="showConf"]')
        await page.wait_for_timeout(250)
        await page.click('#nav button[data-pg="assets"]')
        await page.wait_for_timeout(350)
        conf_shown = await page.eval_on_selector_all(
            "#aList tbody tr td:nth-child(6)", "e => e.map(x => x.textContent)")
        has_pct = any("%" in c for c in conf_shown)
        check(not has_pct, "关闭置信度后表格不再显示百分比")

        # 打开回来
        await page.click('#nav button[data-pg="settings"]')
        await page.wait_for_timeout(300)
        await page.click('#setRow [data-cfg="showConf"]')
        await page.wait_for_timeout(220)

        # 拖阈值 → 提示文字随之变化
        await page.eval_on_selector("#thrD", "el => { el.value = 60; el.dispatchEvent(new Event('input')) }")
        await page.wait_for_timeout(280)
        note = await page.text_content("#thrNote")
        check("0.60" in note, "阈值拖动后说明文字实时更新", note.strip()[:50])
        await page.screenshot(path=str(SHOTS / "08-settings.png"))

        # ---------- 13. 重置 ----------
        print("\n[13] 恢复演示数据")
        await page.click("#btnResetAll")
        await page.wait_for_timeout(500)
        await page.click('#nav button[data-pg="review"]')
        await page.wait_for_timeout(400)
        reset_cnt = (await page.text_content("#rvCount")).strip()
        check(reset_cnt == "2 件待审", "重置后待审队列恢复为 2 件", reset_cnt)

        # ---------- 14. 无异常文本 ----------
        print("\n[14] 全页面异常文本扫描")
        bad = await page.evaluate("""() => {
          const out = [];
          const skip = new Set(['SCRIPT', 'STYLE']);
          const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
          let n;
          while (n = walk.nextNode()) {
            if (skip.has(n.parentElement.tagName)) continue;
            const t = n.textContent;
            if (/undefined|NaN|\\[object Object\\]/.test(t)) out.push(t.trim().slice(0, 60));
          }
          return out;
        }""")
        check(len(bad) == 0, "页面无 undefined / NaN / [object Object]", str(bad[:3]))

        # ---------- 15. 控制台 ----------
        print("\n[15] 控制台与运行时")
        check(len(page_errs) == 0, "无 JS 运行时异常", str(page_errs[:2]))
        check(len(console_errs) == 0, "控制台零报错", str(console_errs[:2]))

        await browser.close()

    print("\n" + "=" * 66)
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    print(f"\n截图目录：{SHOTS}")
    print("=" * 66)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
