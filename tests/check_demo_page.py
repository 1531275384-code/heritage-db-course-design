"""
对单文件演示页做真实浏览器验收。

这份脚本不是"截图工具"，而是逐项断言：
    每个模块渲染出预期的行数、切换角色后页签随之变化、越权页签不可见、
    点击作品能打开详情、模拟终审能改状态、控制台零报错。

用法：
    .venv/Scripts/python.exe tests/check_demo_page.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_launch import launch_async  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
PAGE = BASE / "output" / "01-交付物" / "演示页面.html"
SHOTS = BASE / "output" / "02-截图证据" / "演示页验收"

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = ""):
    results.append((ok, name, detail))
    mark = "✓" if ok else "✗"
    print(f"  {mark} {name}" + (f"  {detail}" if detail and not ok else ""))


async def main() -> int:
    if not PAGE.exists():
        print("页面不存在：" + str(PAGE))
        return 1

    SHOTS.mkdir(parents=True, exist_ok=True)
    url = "file:///" + str(PAGE).replace("\\", "/")

    async with async_playwright() as p:
        browser = await launch_async(p)
        page = await browser.new_page(viewport={"width": 1500, "height": 940})

        console_errors: list[str] = []
        page_errors: list[str] = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        await page.goto(url)
        await page.wait_for_timeout(700)

        print("\n[1] 页面加载与演示区")
        title = await page.title()
        check("非遗" in title, "页面标题正确", title)

        # 纯演示版：左侧说明栏必须彻底不存在（连 DOM 都不能有）
        chapters = await page.eval_on_selector_all(".ch", "els => els.length")
        check(chapters == 0, "无左侧说明章节", f"实际 {chapters}")

        has_doc = await page.evaluate("() => !!document.getElementById('doc')")
        check(not has_doc, "左栏容器 #doc 已移除", f"存在={has_doc}")

        # 左栏没了，演示区应当占满整宽（留 5% 容差给滚动条）
        fill = await page.evaluate("""() => {
          const d = document.getElementById('demo');
          return [d.getBoundingClientRect().width, window.innerWidth];
        }""")
        check(fill[0] / fill[1] >= 0.95,
              "演示区占满整宽",
              f"demo={fill[0]:.0f}px / 视口={fill[1]}px")

        print("\n[2] 传承人身份：我的作品")
        # 默认是传承人 heritor01
        who = await page.text_content("#whoName")
        check(who.strip() == "杨秀英", "默认账号为杨秀英", who)

        rows = await page.eval_on_selector_all("#mineTable tbody tr", "els => els.length")
        check(rows == 3, "杨秀英名下 3 件作品", f"实际 {rows}")

        # 统计卡片
        stat_nums = await page.eval_on_selector_all("#mineStats .stat .v", "els => els.map(e=>e.textContent)")
        check(any("3" in s for s in stat_nums), "统计卡片显示作品数", str(stat_nums))

        # 页签：传承人不该看到工作台与台账
        tabs = await page.eval_on_selector_all("#tabs button", "els => els.map(e=>e.textContent)")
        check("专家工作台" not in tabs, "传承人看不到专家工作台", str(tabs))
        check("监管台账" not in tabs, "传承人看不到监管台账", str(tabs))
        check("母题库" in tabs, "传承人能看到母题库", str(tabs))

        await page.screenshot(path=str(SHOTS / "01-heritor-mine.png"))

        # 点击第一行（作品 1，已确权，带专家意见与授权记录）
        print("\n[3] 作品详情与溯源")
        await page.click("#mineTable tbody tr:nth-child(1)")
        await page.wait_for_timeout(350)
        modal_on = await page.eval_on_selector("#mask", "e => e.classList.contains('on')")
        check(modal_on, "点击作品行打开详情弹窗")
        mtitle = await page.text_content("#mTitle")
        check("苗绣" in (mtitle or ""), "弹窗标题为已确权作品名", mtitle)
        body = await page.text_content("#mBody")
        check("溯源链路" in body, "详情含溯源链路")
        check("创作说明" in body, "详情含创作说明")
        check("意见：" in body, "已确权作品详情含专家意见")
        check("授权记录" in body, "已确权作品详情含授权记录")
        await page.screenshot(path=str(SHOTS / "02-asset-detail.png"))
        await page.click("#modal .x")
        await page.wait_for_timeout(250)

        # 再点第三行（作品 3，待审），验证 AI 有建议但权属未定
        await page.click("#mineTable tbody tr:nth-child(3)")
        await page.wait_for_timeout(350)
        body3 = await page.text_content("#mBody")
        check("未定" in body3, "待审作品的权属级别显示为「未定」")
        check("意见：" not in body3, "待审作品无专家意见（尚未终审）")
        await page.click("#modal .x")
        await page.wait_for_timeout(250)

        print("\n[4] 母题库")
        await page.click("#tabs button:nth-child(2)")  # 母题库
        await page.wait_for_timeout(400)
        cards = await page.eval_on_selector_all(".mcard", "els => els.length")
        check(cards == 3, "母题库 3 张卡片", f"实际 {cards}")

        imgs_ok = await page.eval_on_selector_all(
            ".mcard img", "els => els.every(e => e.complete && e.naturalWidth > 50)")
        check(imgs_ok, "母题参考图全部加载成功")

        # 关联表只含有母题血缘的作品（种子里 4 件，其余 4 件为原创或草稿）
        links = await page.eval_on_selector_all("#motifLink tbody tr", "els => els.length")
        check(links == 4, "母题关联表 4 行（有母题血缘的作品）", f"实际 {links}")

        # 筛选：苗族
        await page.click("#motifFilters button:nth-child(2)")
        await page.wait_for_timeout(300)
        cards2 = await page.eval_on_selector_all(".mcard", "els => els.length")
        check(cards2 == 1, "按民族筛选后为 1 张", f"实际 {cards2}")
        await page.click("#motifFilters button:nth-child(1)")
        await page.wait_for_timeout(250)

        # 点开母题详情
        await page.click(".mcard")
        await page.wait_for_timeout(350)
        mbody = await page.text_content("#mBody")
        check("关键词" in mbody, "母题详情含关键词")
        check("关联作品" in mbody, "母题详情含关联作品")
        await page.screenshot(path=str(SHOTS / "03-motif-detail.png"))
        await page.click("#modal .x")
        await page.wait_for_timeout(250)
        await page.screenshot(path=str(SHOTS / "04-motifs.png"))

        print("\n[5] 作品上传与 AI 分析")
        await page.click("#tabs button:nth-child(3)")  # 作品上传
        await page.wait_for_timeout(350)
        empty = await page.text_content("#uploadStage")
        check("选择上方任一样例" in empty, "初始为空状态提示")

        await page.click("#samplePick button:nth-child(1)")
        await page.wait_for_timeout(300)
        btn = await page.query_selector("#uploadStage button.pri")
        check(btn is not None, "出现「上传并触发 AI 分析」按钮")

        await btn.click()
        await page.wait_for_timeout(600)
        stage = await page.text_content("#uploadStage")
        check("AI 建议" in stage, "分析结果含 AI 建议")
        check("0.9182" in stage, "显示真实相似度 0.9182")
        check("改编衍生" in stage, "判定为改编衍生")
        check("等待专家终审" in stage, "明确权属未定")
        bars = await page.eval_on_selector_all(".simrow", "els => els.length")
        check(bars == 3, "相似度条 3 条", f"实际 {bars}")
        await page.screenshot(path=str(SHOTS / "05-analyze.png"))

        print("\n[6] 切换为专家：工作台与终审")
        await page.click('#roles button[data-role="expert"]')
        await page.wait_for_timeout(500)
        who2 = await page.text_content("#whoName")
        check(who2.strip() == "周文渊", "切换为周文渊", who2)

        tabs2 = await page.eval_on_selector_all("#tabs button", "els => els.map(e=>e.textContent)")
        check("专家工作台" in tabs2, "专家能看到工作台", str(tabs2))
        check("监管台账" in tabs2, "专家能看到台账", str(tabs2))

        # 进工作台
        idx = tabs2.index("专家工作台") + 1
        await page.click(f"#tabs button:nth-child({idx})")
        await page.wait_for_timeout(400)
        pend = await page.eval_on_selector_all("#wbTable tbody tr", "els => els.length")
        check(pend == 2, "待审队列 2 件（与种子一致）", f"实际 {pend}")
        await page.screenshot(path=str(SHOTS / "06-workbench.png"))

        # 打开终审
        await page.click("#wbTable tbody tr:first-child button")
        await page.wait_for_timeout(400)
        picks = await page.eval_on_selector_all("#pickBox button", "els => els.length")
        check(picks == 3, "终审三个档位可选", f"实际 {picks}")

        # 故意选一个与 AI 不同的档位，验证推翻标记
        cur = await page.eval_on_selector("#pickBox button.on", "e => e.dataset.lv")
        other = "original" if cur != "original" else "public"
        await page.click(f'#pickBox button[data-lv="{other}"]')
        await page.wait_for_timeout(250)
        tip = await page.text_content("#ovTip")
        check("推翻" in tip, "选不同档位时提示推翻 AI", tip)

        await page.click("#mBody button.pri")
        await page.wait_for_timeout(600)
        toast = await page.eval_on_selector("#toast", "e => e.textContent")
        check("推翻" in toast, "提交后提示推翻 AI", toast)

        pend2 = await page.eval_on_selector_all("#wbTable tbody tr", "els => els.length")
        check(pend2 == 1, "待审队列减为 1 件", f"实际 {pend2}")

        print("\n[7] 监管台账")
        tabs3 = await page.eval_on_selector_all("#tabs button", "els => els.map(e=>e.textContent)")
        idx3 = tabs3.index("监管台账") + 1
        await page.click(f"#tabs button:nth-child({idx3})")
        await page.wait_for_timeout(500)

        ledger_rows = await page.eval_on_selector_all("#lgTable tbody tr", "els => els.length")
        check(ledger_rows == 8, "台账 8 行", f"实际 {ledger_rows}")

        stats = await page.eval_on_selector_all("#lgStats .stat", "els => els.map(e=>e.textContent)")
        fee_txt = " ".join(stats)
        check("131,600" in fee_txt, "授权费合计 131,600", fee_txt[:120])

        lic_rows = await page.eval_on_selector_all("#lgLicenses tbody tr", "els => els.length")
        check(lic_rows == 5, "授权记录 5 笔", f"实际 {lic_rows}")

        # 检查渲染后的可见文本。注意不能用 page.content()——
        # 那会把 <script> 里的 JS 源码也算进去，源码里本来就有 undefined 关键字。
        bad_text = await page.evaluate("""() => {
          const out = [];
          const skip = new Set(['SCRIPT','STYLE']);
          const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
          let n;
          while (n = walk.nextNode()) {
            if (skip.has(n.parentElement.tagName)) continue;
            const t = n.textContent;
            if (/undefined|NaN|\\[object Object\\]/.test(t)) {
              out.push(n.parentElement.tagName + '.' + n.parentElement.className + ': ' + t.trim().slice(0,60));
            }
          }
          return out;
        }""")
        check(len(bad_text) == 0, "渲染文本无 undefined / NaN / [object Object]",
              " | ".join(bad_text[:3]))

        await page.screenshot(path=str(SHOTS / "07-ledger.png"))

        print("\n[8] 管理员身份")
        await page.click('#roles button[data-role="admin"]')
        await page.wait_for_timeout(450)
        who3 = await page.text_content("#whoName")
        check(who3.strip() == "陈立", "切换为陈立", who3)
        tabs4 = await page.eval_on_selector_all("#tabs button", "els => els.map(e=>e.textContent)")
        check("我的作品" not in tabs4, "管理员看不到「我的作品」", str(tabs4))
        await page.screenshot(path=str(SHOTS / "08-admin.png"))

        # 逐页签扫一遍渲染文本，确保每个模块都干净
        bad_all = []
        for label in tabs4:
            idx = tabs4.index(label) + 1
            await page.click(f"#tabs button:nth-child({idx})")
            await page.wait_for_timeout(420)
            found = await page.evaluate("""() => {
              const out = [];
              const skip = new Set(['SCRIPT','STYLE']);
              const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
              let n;
              while (n = walk.nextNode()) {
                if (skip.has(n.parentElement.tagName)) continue;
                const t = n.textContent;
                if (/undefined|NaN|\\[object Object\\]/.test(t)) {
                  out.push(n.parentElement.className + ': ' + t.trim().slice(0,40));
                }
              }
              return out;
            }""")
            if found:
                bad_all.append(label + " → " + "; ".join(found[:2]))
        check(len(bad_all) == 0, "五个页签逐页扫描均无异常文本", " | ".join(bad_all[:3]))

        print("\n[9] 第二传承人：数据隔离")
        await page.evaluate("setRole('heritor')")
        await page.wait_for_timeout(300)
        rows1 = await page.eval_on_selector_all("#mineTable tbody tr", "els => els.length")
        # 手动把当前用户切到 heritor02 以验证隔离
        await page.evaluate("S.user = USER_BY_ID[4]; renderMine();")
        await page.wait_for_timeout(300)
        rows2 = await page.eval_on_selector_all("#mineTable tbody tr", "els => els.length")
        check(rows1 == 3 and rows2 == 3, "两位传承人各 3 件作品", f"{rows1} / {rows2}")

        names2 = await page.eval_on_selector_all(
            "#mineTable tbody tr td:nth-child(2)", "els => els.map(e=>e.textContent.trim())")
        check(not any("杨氏" in n for n in names2), "第二位传承人看不到他人作品", str(names2)[:100])

        print("\n[10] 重置功能")
        await page.click('#roles button[data-role="expert"]')
        await page.wait_for_timeout(350)
        tabs_r = await page.eval_on_selector_all("#tabs button", "els => els.map(e=>e.textContent)")
        await page.click(f"#tabs button:nth-child({tabs_r.index('专家工作台')+1})")
        await page.wait_for_timeout(350)
        pend_before = await page.eval_on_selector_all("#wbTable tbody tr", "els => els.length")
        await page.click("#resetBtn")
        await page.wait_for_timeout(450)
        pend_after = await page.eval_on_selector_all("#wbTable tbody tr", "els => els.length")
        check(pend_after == 2, "重置后待审队列恢复为 2 件", f"重置前 {pend_before} → 后 {pend_after}")
        toast_r = await page.eval_on_selector("#toast", "e => e.textContent")
        check("恢复初始状态" in toast_r, "重置有明确提示", toast_r)

        print("\n[11] 控制台与错误")
        check(len(page_errors) == 0, "无 JS 运行时异常",
              " | ".join(page_errors[:2]) if page_errors else "")
        check(len(console_errors) == 0, "控制台零报错",
              " | ".join(console_errors[:2]) if console_errors else "")

        await browser.close()

    ok = sum(1 for r in results if r[0])
    total = len(results)
    print("\n" + "=" * 66)
    print(f"通过 {ok} 项，失败 {total - ok} 项")
    if ok < total:
        print("\n失败明细：")
        for good, name, detail in results:
            if not good:
                print(f"  ✗ {name}  {detail}")
    print(f"\n截图目录：{SHOTS}")
    print("=" * 66)
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
