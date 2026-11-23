"""
AI 特征引擎自测（不依赖数据库）。

目标：证明「相似度真的能区分不同纹样」，而不是随便返回一个数字。
     这是分级阈值能生效的前提，也是评审最该质疑的地方，所以用数据说话。

测试用的图是**工程里实际交付的母题图与样例作品**（data/motifs、data/samples），
不是另画的替身 —— 保证测的就是线上跑的那份资产。

三组指标：
  A. 母题互分离度      三个公有母题两两之间应显著不同（否则无法区分来源）
  B. 类内稳定性        同一作品的多次翻拍（旋转/亮度/模糊/噪声/缩放/裁剪）应高度一致
  C. 端到端分级正确性  改编样例应落 derived 区间，原创样例应落 original 区间

运行：python -m tests.test_feature_engine
"""
from __future__ import annotations

import io
import math
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ai.features import extract_from_bytes, get_extractor  # noqa: E402
from app.config import settings  # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  \033[92m✓\033[0m {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  \033[91m✗\033[0m {name}" + (f"\n      → {detail}" if detail else ""))


def to_bytes(img: Image.Image, fmt: str = "JPEG", quality: int = 92) -> bytes:
    buf = io.BytesIO()
    if fmt == "JPEG" and img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    img.save(buf, format=fmt, quality=quality) if fmt == "JPEG" else img.save(buf, format=fmt)
    return buf.getvalue()


def cos(a, b) -> float:
    va, vb = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    return float(va @ vb)      # 已 L2 归一化，内积即 cosine 相似度


def variants(img: Image.Image) -> dict[str, Image.Image]:
    """
    模拟真实的"同一作品的不同数字副本"：
      博物馆扫描件、手机翻拍、压缩上传、缩略图……都归到这一类。
    """
    rng = random.Random(20260101)
    out = {}
    out["旋转 4°"] = img.rotate(4, resample=Image.BICUBIC, fillcolor=None)
    out["旋转 -7°"] = img.rotate(-7, resample=Image.BICUBIC, fillcolor=None)
    out["亮度 +12%"] = ImageEnhance.Brightness(img).enhance(1.12)
    out["亮度 -15%"] = ImageEnhance.Brightness(img).enhance(0.85)
    out["对比度 +10%"] = ImageEnhance.Contrast(img).enhance(1.10)
    out["高斯模糊 1.0"] = img.filter(ImageFilter.GaussianBlur(radius=1.0))
    out["缩放 0.6x"] = img.resize((int(img.width * 0.6), int(img.height * 0.6)), Image.BILINEAR)

    # 加噪
    arr = np.asarray(img).astype(np.int16)
    noise = np.random.default_rng(7).normal(0, 5, arr.shape).astype(np.int16)
    out["高斯噪声 σ=5"] = Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8))

    # 轻度裁剪（模拟不同取景）
    w, h = img.size
    out["裁边 8%"] = img.crop((int(w * .08), int(h * .08), int(w * .92), int(h * .92)))

    # JPEG 低质量重压缩
    out["JPEG q=55"] = Image.open(io.BytesIO(to_bytes(img, quality=55)))
    return out


def main() -> int:
    print("=" * 74)
    print(" AI 特征引擎自测")
    print("=" * 74)

    ext = get_extractor()
    print(f"后端: {getattr(ext, 'NAME', type(ext).__name__)}   维度: {ext.dim}")
    print(f"分级阈值: public >= {settings.SIM_PUBLIC_THRESHOLD}, "
          f"derived >= {settings.SIM_DERIVED_THRESHOLD}\n")

    motif_dir = ROOT / "data" / "motifs"
    sample_dir = ROOT / "data" / "samples"
    motifs = {
        "蝴蝶纹": motif_dir / "motif_butterfly.jpg",
        "缠枝莲纹": motif_dir / "motif_lotus.jpg",
        "回纹": motif_dir / "motif_huiwen.jpg",
    }
    missing = [str(p) for p in list(motifs.values()) if not p.exists()]
    if missing:
        print(f"\033[91m缺少数数据文件：{missing}\033[0m")
        print("请先运行：python -m app.motif_images")
        return 1

    motif_vec = {k: extract_from_bytes(p.read_bytes()).vector for k, p in motifs.items()}

    # ---------------------------------------------------------------- A
    print("【A】母题互分离度（三个公有母题两两之间）")
    pair_sims = []
    names = list(motifs)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            s = cos(motif_vec[names[i]], motif_vec[names[j]])
            pair_sims.append(s)
            print(f"    {names[i]} ↔ {names[j]}: {s:.4f}")
    max_pair = max(pair_sims)
    check("母题之间最大相似度 < 0.60（可区分来源）", max_pair < 0.60, f"实测最大 {max_pair:.4f}")
    check("自相似为 1.0", abs(cos(motif_vec["蝴蝶纹"], motif_vec["蝴蝶纹"]) - 1.0) < 1e-6)

    # ---------------------------------------------------------------- B
    print("\n【B】类内稳定性（同一作品的多次翻拍）")
    print("    判定标准：非旋转类扰动 >= 0.90；旋转类 >= 0.70（周期性几何纹样天然敏感）")
    all_intra = []
    worst_rot = 1.0
    for label, img in [("蝴蝶纹", Image.open(motifs["蝴蝶纹"])),
                       ("缠枝莲纹", Image.open(motifs["缠枝莲纹"])),
                       ("回纹", Image.open(motifs["回纹"]))]:
        base = motif_vec[label]
        sims = {}
        for vname, vimg in variants(img).items():
            sims[vname] = cos(base, extract_from_bytes(to_bytes(vimg)).vector)
        all_intra.extend(sims.values())
        worst = min(sims, key=sims.get)
        print(f"    {label}: 最低 {sims[worst]:.4f}（{worst}）, "
              f"均值 {np.mean(list(sims.values())):.4f}")

        rot_sims = {k: v for k, v in sims.items() if k.startswith("旋转")}
        non_rot = {k: v for k, v in sims.items() if not k.startswith("旋转")}
        worst_rot = min(worst_rot, min(rot_sims.values()))
        check(f"  {label} 非旋转翻拍全部 >= 0.90",
              min(non_rot.values()) >= 0.90,
              "; ".join(f"{k}={v:.3f}" for k, v in non_rot.items() if v < 0.90))
        check(f"  {label} 旋转翻拍 >= 0.70",
              min(rot_sims.values()) >= 0.70,
              "; ".join(f"{k}={v:.3f}" for k, v in rot_sims.items() if v < 0.70))

    check("整体类内均值 >= 0.95", np.mean(all_intra) >= 0.95, f"{np.mean(all_intra):.4f}")
    # 关键安全边界：类内最差情况仍应显著高于类间最大相似度，
    # 否则"同一作品换角度翻拍"会被误判为"另一个纹样"，这是比漏判更严重的错误
    check("类内最低 > 类间最高（不会把同一作品误判为不同母题）",
          min(all_intra) > max_pair,
          f"类内最低 {min(all_intra):.4f} vs 类间最高 {max_pair:.4f}")

    # ---------------------------------------------------------------- C
    print("\n【C】端到端分级正确性（用交付的样例作品）")
    cases = [
        ("sample_derived_butterfly.jpg", "derived", "蝴蝶纹改编版"),
        ("sample_original_fish.jpg", "original", "鱼纹原创"),
    ]
    for fname, expect, label in cases:
        p = sample_dir / fname
        if not p.exists():
            check(f"{label} 样例存在", False, f"缺文件 {p}")
            continue
        v = extract_from_bytes(p.read_bytes()).vector
        sims = sorted(((cos(v, motif_vec[m]), m) for m in motifs), reverse=True)
        top_sim, top_name = sims[0]
        if top_sim >= settings.SIM_PUBLIC_THRESHOLD:
            got = "public"
        elif top_sim >= settings.SIM_DERIVED_THRESHOLD:
            got = "derived"
        else:
            got = "original"
        print(f"    {label}: 最相似「{top_name}」{top_sim:.4f} → 判定 {got}")
        check(f"  {label} 判定为 {expect}", got == expect, f"实际 {got}（相似度 {top_sim:.4f}）")

    # ---------------------------------------------------------------- D
    print("\n【D】鲁棒性（退化输入不得产生 NaN / 错误维度）")
    for label, img in [
        ("全黑图", Image.new("RGB", (64, 64), (0, 0, 0))),
        ("全白图", Image.new("RGB", (64, 64), (255, 255, 255))),
        ("8×8 极小图", Image.new("RGB", (8, 8), (128, 128, 128))),
        ("极端长宽比", Image.new("RGB", (600, 20), (90, 120, 60))),
    ]:
        f = extract_from_bytes(to_bytes(img))
        norm = float(np.linalg.norm(f.vector))
        has_nan = any(math.isnan(x) for x in f.vector)
        check(f"  {label}：512 维、范数≈1、无 NaN",
              len(f.vector) == 512 and abs(norm - 1.0) < 1e-4 and not has_nan,
              f"dim={len(f.vector)} norm={norm:.4f} nan={has_nan}")

    # PNG 透明通道图
    rgba = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
    f = extract_from_bytes(to_bytes(rgba, fmt="PNG"))
    check("  透明 PNG 可处理", len(f.vector) == 512)

    print("\n" + "=" * 74)
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    if FAIL:
        print("\033[91m存在未通过项，请检查特征实现或阈值配置\033[0m")
        return 1
    print("\033[92m全部通过\033[0m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
