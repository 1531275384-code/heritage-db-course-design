#!/usr/bin/env python
"""
测试总入口：一条命令跑完全部套件。

为什么需要这个文件：
    套件有 10 个，分散在 README §12 里，每次要手敲 10 条命令，漏跑一个也不会有人发现。
    这里有三个固定分组，跑完给一张汇总表，一眼能看出哪套红了。

用法：
    python tests/run_all.py                  # 纯代码 + 浏览器（444 项，默认）
    python tests/run_all.py --group pure     # 只跑不用浏览器的（285 项，约 10 秒）
    python tests/run_all.py --group browser  # 只跑三个需要 Playwright 的
    python tests/run_all.py --group db       # 只跑真实数据库验收（需库已起）
    python tests/run_all.py --all            # 三组全跑
    python tests/run_all.py -v               # 同时打印每套的完整输出

设计约定：
    1. 每个套件都以退出码表示成败（0 通过 / 非 0 失败），本脚本只认退出码，不解析文案。
    2. 项数是「尽力抓取」——各套件打印格式不统一（「通过 N 项」/「N 项通过」），
       抓不到就记 —，不猜、不伪造。
    3. 默认不把完整输出灌到终端（444 项断言太长），失败时才把该套件尾部打出来。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 套件清单：模块名 -> (人读的名字, 期望项数或 None)
GROUPS: dict[str, list[tuple[str, str, int | None]]] = {
    "pure": [
        ("tests.test_sql_structure",  "SQL 静态结构",        None),
        ("tests.test_rules_sync",     "规则单一真相",        66),
        ("tests.test_compat_vector",  "向量兼容层",          39),
        ("tests.test_invariants",     "源码级不变量",        88),
        ("tests.test_classifier",     "三档分级器",          24),
        ("tests.test_feature_engine", "特征引擎",            17),
        ("tests.tune_weights",        "权重标定",            None),
        ("tests.run_e2e_inline",      "端到端业务链路",      52),
    ],
    "browser": [
        ("tests.shoot_ui",            "真实浏览器 UI 断言",  49),
        ("tests.check_demo_page",     "纯界面演示页验收",    52),
        ("tests.check_demo_v2",       "互动版演示页验收",    58),
    ],
    "db": [
        ("tests.check_pg",            "真实 PostgreSQL 验收", 50),
        ("tests.test_pg_concurrency", "多会话并发验收",       30),
        ("tests.test_rules_parity",   "规则对拍（库 vs Python）", 79),
    ],
}

# 各套件的项数打印格式不统一，按优先级逐个尝试
NUM_PATTERNS = [
    re.compile(r"通过\s*(\d+)\s*项"),          # 「通过 88 项，失败 0 项」
    re.compile(r"(\d+)\s*项通过"),              # 「26 项通过 / 0 项失败」
    re.compile(r"通过\s*(\d+)\s*/\s*(\d+)"),    # 「通过 52/52」
]


def extract_count(text: str) -> int | None:
    """从输出里尽力抓一个「通过项数」。抓不到返回 None，绝不瞎填。"""
    for pat in NUM_PATTERNS:
        m = pat.search(text)
        if m:
            return int(m.group(1))
    return None


def extract_gap(text: str) -> int:
    """
    抓「缺口」数。目前只有 check_pg 会打印 GAP（如实的未落实项，不算通过也不算失败）。
    不抓这个的话，汇总里「26 项通过」会把 2 个缺口盖掉，正是本项目最忌讳的那种误导。
    """
    m = re.search(r"(\d+)\s*项缺口", text)
    return int(m.group(1)) if m else 0


def run_one(module: str, verbose: bool) -> tuple[bool, int | None, int, float, str]:
    """跑一个套件，返回 (是否通过, 通过项数, 缺口数, 耗时秒, 输出全文)。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"     # Windows 下不设会因中文输出炸在管道上
    env["PYTHONUNBUFFERED"] = "1"

    t0 = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", module],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.perf_counter() - t0
    out = (proc.stdout or "") + (proc.stderr or "")

    if verbose:
        print(out)

    ok = proc.returncode == 0
    return ok, extract_count(out), extract_gap(out), elapsed, out


def tail(text: str, n: int = 14) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def main() -> int:
    ap = argparse.ArgumentParser(description="跑全部测试套件并汇总")
    ap.add_argument("--group", choices=list(GROUPS), action="append",
                    help="只跑指定分组，可重复；不给则跑 pure + browser")
    ap.add_argument("--all", action="store_true", help="三组全跑（含数据库）")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印每套件的完整输出")
    args = ap.parse_args()

    if args.all:
        selected = list(GROUPS)
    elif args.group:
        selected = args.group
    else:
        selected = ["pure", "browser"]

    plan: list[tuple[str, str, int | None]] = []
    for g in selected:
        plan.extend(GROUPS[g])

    print("=" * 74)
    print(f"  测试总入口   分组 {' + '.join(selected)}   共 {len(plan)} 套")
    print(f"  解释器 {sys.executable}")
    print("=" * 74)

    rows: list[tuple[str, str, bool, int | None, int, float]] = []
    failures: list[tuple[str, str]] = []

    for idx, (module, label, expect) in enumerate(plan, 1):
        print(f"[{idx}/{len(plan)}] {label:<20}", end="", flush=True)
        ok, count, gap, elapsed, out = run_one(module, args.verbose)
        rows.append((label, module, ok, count, gap, elapsed))

        if ok:
            if count is None:
                verdict = "通过"
            elif gap:
                verdict = f"{count} 项 + {gap} 缺口"   # 缺口必须露出来，不能藏进「通过」
            else:
                verdict = f"{count} 项"
            print(f" \033[92mOK\033[0m   {verdict:<14} {elapsed:5.1f}s")
        else:
            print(f" \033[91mFAIL\033[0m             {elapsed:5.1f}s")
            failures.append((label, module))
            print("-" * 74)
            print(tail(out))
            print("-" * 74)

    # ---------- 汇总 ----------
    total_count = sum(c for _, _, _, c, _, _ in rows if c is not None)
    got_count = sum(1 for _, _, _, c, _, _ in rows if c is not None)
    total_gap = sum(g for _, _, _, _, g, _ in rows)
    n_fail = len(failures)
    n_ok = len(rows) - n_fail

    print()
    print("=" * 74)
    print(f"  汇总：{n_ok} 套通过 / {n_fail} 套失败（共 {len(rows)} 套，"
          f"{sum(r[5] for r in rows):.1f}s）")
    if got_count == len(rows):
        assert_str = f"{total_count} 项通过"
    elif got_count:
        assert_str = (f"已抓取 {got_count}/{len(rows)} 套，合计 {total_count} 项"
                      f"（其余套件不打印项数）")
    else:
        assert_str = "未抓到可统计的项数"
    if total_gap:
        assert_str += f"，另有 {total_gap} 项缺口（如实记录的未落实项，详见单套输出）"
    print(f"  断言：{assert_str}")
    print("=" * 74)

    if failures:
        print("\n失败套件：")
        for label, module in failures:
            print(f"  ✗ {label}  →  python -m {module}")
        print("\n单独跑上面任意一条可看完整失败明细。")
        return 1

    print("\n全部通过。")
    if total_gap:
        print("注意：存在缺口项，它们不算通过。跑 python -m tests.check_pg 看明细。")
    if "db" not in selected:
        print("提示：真库验收未包含在内，需要时加 --group db（要求 PostgreSQL 已启动）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
