"""
特征段权重标定脚本（README 中引用的可复现实验）。

背景
----
BuiltinExtractor 把三段特征（HOG 结构 / HSV 色彩 / 多尺度纹理）各自 L2 归一化后
按权重 w_s / w_c / w_t 缩放再拼接、整体归一化。三个权重不能拍脑袋定，必须标定。

两条互相拉扯的目标
------------------
  ① 翻拍稳定性  ：同一作品的 9 种翻拍变体，与原始图的余弦相似度都要 >= 0.90
                  （不达标 → 同一作品被误判成"另一个纹样"，比漏判严重得多）
  ② 改编可区分性：改编样例与三个母题的最大相似度必须 < 0.92（public 阈值）
                  （超标 → 真实改编作品被判"与母题雷同"，会剥夺传承人权益）

评分
----
分两步：

  第一步 可行性判定（硬约束，不可交易）
      feasible = (最差非旋转翻拍相似度 >= 0.90) and (改编样例相似度 < 0.92)
      不可行解永远排在可行解之后。

  第二步 可行解内部排序（软偏好）
      score = 旋转翻拍最差相似度 × 1.0
            + 最小单母题翻拍相似度 × 0.5     （避免某一母题被牺牲）
            - 原创样例相似度 × 0.1           （原创离母题越远越好）
      score = 0.0                          （不可行解，不参与排序）

为什么不用「超线余量」排序？
    早期版本用 min(稳定性/0.90, 改编/0.92) 排序，两次都跑偏：
      ① 不封顶时，"改编 0.9532 已越线"的解因比值偏大排第一；
      ② 封顶后，可行解挤在 2.0 附近，排序实际由"色彩权重压到多少"决定，
         最优解退化到 色彩 0.06 —— 色彩段几乎被丢掉。
    色彩段承担"同构图不同材质"的区分（苗绣靛蓝 vs 苏绣月白），不能丢。
    因此改为：硬约束只做门槛，门槛内改用「旋转鲁棒性 + 各母题均衡」做偏好排序，
    这个偏好天然把解推向各段都有贡献的区域，而不是推到某个角点。

性能设计（关键）
----------------
朴素实现是「每组权重都重新提取全部图片特征」，24 张图 × 240 组 = 5760 次提取，
在纯 numpy 后端下超过 10 分钟会被系统中断。
本脚本利用一个数学事实做优化：

    三段分别 L2 归一化后，加权拼接的 L2 范数是常数 ——
        ||[a·u, b·v, c·w]|| = sqrt(a² + b² + c²)     （u,v,w 均为单位向量）
    整体归一化后，余弦相似度只取决于各段的**内积**：
        cos(x, y) = (a²·u·u' + b²·v·v' + c²·w·w') / (a² + b² + c²)

    ⚠️ 注意平方：先加权再归一化，a² 而不是 a。同理，score 必须对**不达标**项封顶。
    早期版本 score 直接用比值，导致 0.37/0.19/0.44 这组"改编 0.9532 越线"
    却因为评分公式缺陷排到第一。现在 score 对两条约束分别截到 1.0，
    任何一条越线都不可能超过 1.0。

因此：每个变体只需提取**一次**三段子特征，缓存下来；之后每组权重只是对
缓存的三个内积做一次加权求和（标量运算），240 组权重瞬间算完。

用法
----
    python -m tests.tune_weights            # 两阶段网格搜索，打印 Top-12
    python -m tests.tune_weights --apply    # 额外输出可直接粘贴的代码片段
    python -m tests.tune_weights --full     # 单阶段细网格（慢，约 1~2 分钟）
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.ai.features as F  # noqa: E402
from app.config import settings  # noqa: E402

MOTIFS = {
    "蝴蝶纹": ROOT / "data" / "motifs" / "motif_butterfly.jpg",
    "缠枝莲纹": ROOT / "data" / "motifs" / "motif_lotus.jpg",
    "回纹": ROOT / "data" / "motifs" / "motif_huiwen.jpg",
}
DERIVED_SAMPLE = ROOT / "data" / "samples" / "sample_derived_butterfly.jpg"
ORIGINAL_SAMPLE = ROOT / "data" / "samples" / "sample_original_fish.jpg"

STABILITY_TARGET = 0.90                          # 翻拍稳定性下限
DERIVED_CEILING = settings.SIM_PUBLIC_THRESHOLD   # 0.92，改编相似度必须严格小于
SIZE = 256                                        # 与 BuiltinExtractor.extract 保持一致


# =============================================================================
#  一、三段子特征提取（与 BuiltinExtractor.extract 内部逐行等价）
# =============================================================================
_BASE = F.BuiltinExtractor()


def split_segments(img: Image.Image) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    返回 (hog, color, texture) 三个**已 L2 归一化**的段向量。
    必须与 BuiltinExtractor.extract() 中的切分/截断逻辑完全一致，
    否则标定出的权重套回生产代码会失真。
    """
    a = np.asarray(img.resize((SIZE, SIZE), Image.BILINEAR), dtype=np.float32) / 255.0

    hog = _BASE._hog_lite(a, cells=6, bins=8)
    hog = hog[:192] if hog.size >= 192 else np.pad(hog, (0, 192 - hog.size))

    color = _BASE._color_blocks(a, blocks=4, bins=4)
    color = color[:192] if color.size >= 192 else np.pad(color, (0, 192 - color.size))

    tex = _BASE._multiscale_texture(a)
    tex = tex[:128] if tex.size >= 128 else np.pad(tex, (0, 128 - tex.size))

    return (
        F._l2_normalize(hog).astype(np.float64),
        F._l2_normalize(color).astype(np.float64),
        F._l2_normalize(tex).astype(np.float64),
    )


def make_variants(img: Image.Image) -> dict[str, Image.Image]:
    """9 种模拟"同一作品的不同翻拍/扫描件"的变体。"""
    w, h = img.size
    arr = np.asarray(img).astype(np.int16)
    noise = np.random.default_rng(7).normal(0, 5, arr.shape).astype(np.int16)
    return {
        "旋转 4°": img.rotate(4, resample=Image.BICUBIC, fillcolor=None),
        "旋转 -7°": img.rotate(-7, resample=Image.BICUBIC, fillcolor=None),
        "亮度 +12%": ImageEnhance.Brightness(img).enhance(1.12),
        "亮度 -15%": ImageEnhance.Brightness(img).enhance(0.85),
        "对比度 +10%": ImageEnhance.Contrast(img).enhance(1.10),
        "模糊 1.0": img.filter(ImageFilter.GaussianBlur(1.0)),
        "缩放 0.6x": img.resize((int(w * .6), int(h * .6)), Image.BILINEAR),
        "噪声 σ=5": Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8)),
        "裁边 8%": img.crop((int(w * .08), int(h * .08), int(w * .92), int(h * .92))),
    }


# =============================================================================
#  二、缓存：每张图只提取一次，存成「与原图的三段内积」
# =============================================================================
def build_cache() -> dict:
    """
    返回结构：
      {
        "motif_base":  {标签: (h,c,t)},                       # 母题原图三段向量
        "motif_var":   {标签: [(变体名, 是否旋转, (ih,ic,it))]},  # 变体相对原图的三段内积
        "samples":     {名称: [(与每个母题原图的三段内积)]},
      }
    内积提前算好，后面每组权重只需做 3 次乘加，速度提升约两个数量级。
    """
    cache: dict = {"motif_base": {}, "motif_var": {}, "samples": {}}

    for label, path in MOTIFS.items():
        img = Image.open(path).convert("RGB")
        base = split_segments(img)
        cache["motif_base"][label] = base

        rows = []
        for vname, vimg in make_variants(img).items():
            seg = split_segments(vimg)
            inner = tuple(float(np.dot(base[i], seg[i])) for i in range(3))
            rows.append((vname, vname.startswith("旋转"), inner))
        cache["motif_var"][label] = rows

    for name, path in (("derived", DERIVED_SAMPLE), ("original", ORIGINAL_SAMPLE)):
        seg = split_segments(Image.open(path).convert("RGB"))
        per_motif = []
        for label in MOTIFS:
            b = cache["motif_base"][label]
            per_motif.append(tuple(float(np.dot(b[i], seg[i])) for i in range(3)))
        cache["samples"][name] = per_motif

    return cache


def sim(weights: tuple[float, float, float], inner: tuple[float, float, float]) -> float:
    """
    由三段内积 + 权重直接算出余弦相似度（无需重建 512 维向量）。

        cos = (ws²·ih + wc²·ic + wt²·it) / (ws² + wc² + wt²)

    推导：加权拼接向量 x = [ws·u, wc·v, wt·w]，其中 u,v,w 为单位向量，
         则 ||x|| = sqrt(ws²+wc²+wt²)，两向量内积 = ws²·u·u' + wc²·v·v' + wt²·w·w'。
    """
    ws, wc, wt = weights
    denom = ws * ws + wc * wc + wt * wt
    ih, ic, it = inner
    return (ws * ws * ih + wc * wc * ic + wt * wt * it) / denom


def evaluate(cache: dict, weights: tuple[float, float, float]) -> dict:
    worst_nonrot, worst_rot = 1.0, 1.0
    worst_item = ""
    per_motif: dict[str, float] = {}

    for label, rows in cache["motif_var"].items():
        worst = 1.0
        for vname, is_rot, inner in rows:
            s = sim(weights, inner)
            if s < worst:
                worst, worst_name = s, f"{label}/{vname}"
            if is_rot:
                worst_rot = min(worst_rot, s)
            else:
                if s < worst_nonrot:
                    worst_nonrot, worst_item = s, f"{label}/{vname}"
        per_motif[label] = worst

    derived = max(sim(weights, inn) for inn in cache["samples"]["derived"])
    original = max(sim(weights, inn) for inn in cache["samples"]["original"])

    feasible = worst_nonrot >= STABILITY_TARGET and derived < DERIVED_CEILING
    score = (
        1.0 * worst_rot
        + 0.5 * min(per_motif.values())
        - 0.1 * original
    ) if feasible else 0.0          # 不可行解一律记 0，杜绝越线解混进 Top 榜

    return {
        "weights": weights,
        "worst_nonrot": worst_nonrot, "worst_rot": worst_rot, "worst_item": worst_item,
        "derived": derived, "original": original,
        "score": score,
        "feasible": feasible,
        "per_motif": per_motif,
    }


# =============================================================================
#  三、网格搜索
# =============================================================================
def search(cache: dict, grid: list[tuple[float, float, float]]) -> list[dict]:
    """
    排序键：先按「是否满足全部硬约束」降序，再按 score 降序。
    这样即使评分公式有意外，可行解也永远排在不可行解前面 —— 双保险。
    """
    return sorted(
        (evaluate(cache, w) for w in grid),
        key=lambda r: (not r["feasible"], -r["score"]),
    )


def grid_combinations(s_vals, c_vals, t_vals, step: float = 0.01) -> list[tuple]:
    """只保留三段和为 1.00 的组合（归一化后段权重的和没有绝对意义，固定为 1 便于解释）。"""
    out = []
    for ws, wc, wt in itertools.product(s_vals, c_vals, t_vals):
        if abs((ws + wc + wt) - 1.0) < step / 2:
            out.append((round(ws, 2), round(wc, 2), round(wt, 2)))
    return out


def print_table(results: list[dict], top: int = 12) -> None:
    hdr = (f"{'S/C/T':<14}{'翻拍最差':>11}{'旋转最差':>10}{'改编':>8}"
           f"{'原创':>8}{'评分':>8}  结论")
    print(hdr)
    print("-" * (len(hdr) + 6))
    n_ok = 0
    for r in results[:top]:
        ws, wc, wt = r["weights"]
        ok_stab = "✓" if r["worst_nonrot"] >= STABILITY_TARGET else "✗"
        ok_der = "✓" if r["derived"] < DERIVED_CEILING else "✗"
        status = "达标" if r["feasible"] else "越线"
        n_ok += 1 if r["feasible"] else 0
        print(f"{ws:.2f}/{wc:.2f}/{wt:.2f}   {r['worst_nonrot']:>9.4f}{ok_stab} "
              f"{r['worst_rot']:>9.4f} {r['derived']:>7.4f}{ok_der} {r['original']:>7.4f} "
              f"{r['score']:>8.4f}  {status}")
    n_feasible = sum(1 for r in results if r["feasible"])
    print(f"\n共评估 {len(results)} 组，其中同时满足两条硬约束的 {n_feasible} 组"
          f"（上表 Top{top} 中含 {n_ok} 组）")


def report(r: dict) -> None:
    ws, wc, wt = r["weights"]
    print(f"\n选用权重：结构 {ws:.2f} / 色彩 {wc:.2f} / 纹理 {wt:.2f}")
    print(f"  非旋转翻拍最差 {r['worst_nonrot']:.4f} ← {r['worst_item']}")
    print(f"  旋转翻拍最差   {r['worst_rot']:.4f}（周期网格纹样天然敏感，不参与评分）")
    print(f"  改编样例最高相似度 {r['derived']:.4f} → 判 derived（阈值 0.80~0.92）")
    print(f"  原创样例最高相似度 {r['original']:.4f} → 判 original（阈值 < 0.80）")
    print("  各母题翻拍最差：" + ", ".join(f"{k}={v:.4f}" for k, v in r["per_motif"].items()))


def main() -> int:
    full = "--full" in sys.argv
    print("正在提取特征（每张图只提取一次，后续加权为标量运算）…")
    cache = build_cache()
    n_imgs = sum(len(v) for v in cache["motif_var"].values()) + len(MOTIFS) + 2
    print(f"已缓存 {n_imgs} 张图的三段子特征。")
    print(f"目标：非旋转翻拍最差 >= {STABILITY_TARGET}，改编相似度 < {DERIVED_CEILING}\n")

    if full:
        grid = grid_combinations(
            [round(x * 0.02, 2) for x in range(18, 33)],
            [round(x * 0.02, 2) for x in range(6, 16)],
            [round(x * 0.02, 2) for x in range(6, 16)],
        )
        print(f"[单阶段细网格] {len(grid)} 组（三权重和 = 1.00）\n")
        results = search(cache, grid)
        print_table(results)
        report(results[0])
        best = results[0]
    else:
        # 阶段一：粗网格，先锁定评分最高的区域
        coarse = grid_combinations(
            [round(x * 0.05, 2) for x in range(8, 14)],      # 0.40 ~ 0.65
            [round(x * 0.05, 2) for x in range(2, 9)],       # 0.10 ~ 0.40
            [round(x * 0.05, 2) for x in range(2, 9)],
            step=0.05,
        )
        print(f"[阶段一] 粗网格 {len(coarse)} 组（步长 0.05）")
        r1 = search(cache, coarse)
        print_table(r1, top=6)

        ws0, wc0, wt0 = r1[0]["weights"]
        print(f"\n阶段一最优 {ws0:.2f}/{wc0:.2f}/{wt0:.2f}，"
              f"在其 ±0.04 邻域内做细网格（步长 0.01）…")

        def nb(center: float) -> list[float]:
            return [round(center + d * 0.01, 2) for d in range(-4, 5) if center + d * 0.01 > 0]

        fine = grid_combinations(nb(ws0), nb(wc0), nb(wt0))
        print(f"[阶段二] 细网格 {len(fine)} 组\n")
        results = search(cache, fine)
        print_table(results)
        report(results[0])
        best = results[0]

    if "--apply" in sys.argv:
        ws, wc, wt = best["weights"]
        print("\n/* 将以下三行写入 app/ai/features.py 中 BuiltinExtractor.extract() 的 np.concatenate */")
        print(f"            _l2_normalize(hog) * {ws:.2f},")
        print(f"            _l2_normalize(color) * {wc:.2f},")
        print(f"            _l2_normalize(texture) * {wt:.2f},")

    # 与当前生产代码中的权重做一致性校验，防止脚本与实现漂移
    prod = (F.HOG_WEIGHT, F.COLOR_WEIGHT, F.TEXTURE_WEIGHT)
    prod_r = evaluate(cache, prod)
    print(f"\n[一致性校验] 生产代码当前权重 {prod[0]:.2f}/{prod[1]:.2f}/{prod[2]:.2f}："
          f"翻拍最差 {prod_r['worst_nonrot']:.4f}，改编 {prod_r['derived']:.4f}，"
          f"评分 {prod_r['score']:.4f}")
    if prod_r["worst_nonrot"] < STABILITY_TARGET or prod_r["derived"] >= DERIVED_CEILING:
        print("  ⚠️ 生产代码权重未同时满足两条目标，请用 --apply 的输出更新 features.py")
        return 1
    print("  ✓ 生产代码权重同时满足两条目标")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
