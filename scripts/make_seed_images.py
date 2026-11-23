"""
生成演示种子数据的示例图片（一次性工具，产物已提交在 data/ 下）。

为什么造图而不是直接复用现有样本：
  演示需要 4 个账号、8 件作品、3 张母题参考图，而仓库里只有 3 张母题图与 2 张样本。
  直接用同一张图会触发 file_hash 去重（create_asset 会判为重复），
  所以必须让每件作品的图**像素级不同**。

同时要保证 AI 判定结果与种子数据里写的结论一致：
  · 母题参考图 3 张（蝴蝶 / 缠枝莲 / 回纹）—— 已有，本脚本不覆盖
  · 「改良蝴蝶」「直接复制蝴蝶」→ 必须与 motif_butterfly 高相似（>0.92 / 0.80~0.92）
  · 「缠枝莲屏风」→ 与 motif_lotus 高相似
  · 「回纹坐垫 / 银镯」→ 与 motif_huiwen 中等相似
  · 「原创鱼纹 / 山间月」→ 与所有母题都低相似

实现方式：以母题图为底，叠加受控的几何变换与色相扰动，
        再对"原创"类作品改用完全不同的构图（鱼形 / 月牙）。
每张图在右下角打上极小的标识，避免与原始素材混淆。

运行：python -m scripts.make_seed_images
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
MOTIF_DIR = ROOT / "data" / "motifs"
OUT = ROOT / "data" / "uploads"
SIZE = 480


def _load(name: str) -> Image.Image:
    p = MOTIF_DIR / name
    if not p.exists():
        raise SystemExit(f"母题参考图缺失：{p}（请先准备 MOTIF_DIR 下的三张图）")
    return Image.open(p).convert("RGB").resize((SIZE, SIZE), Image.LANCZOS)


def _hue_shift(im: Image.Image, deg: float) -> Image.Image:
    """在 HSV 空间旋转色相，用于制造"配色改良"的合理差异。"""
    hsv = im.convert("HSV")
    h, s, v = hsv.split()
    shift = int(deg / 360.0 * 255) % 256
    h = h.point(lambda x: (x + shift) % 256)
    return Image.merge("HSV", (h, s, v)).convert("RGB")


def _contrast(im: Image.Image, k: float) -> Image.Image:
    from PIL import ImageEnhance
    return ImageEnhance.Contrast(im).enhance(k)


def _stamp(im: Image.Image, text: str) -> Image.Image:
    """右下角小字标识，避免演示图与真实藏品照片混淆。"""
    d = ImageDraw.Draw(im)
    w = 8 * len(text) + 16
    x0, y0 = SIZE - w - 10, SIZE - 30
    d.rectangle([x0, y0, SIZE - 10, SIZE - 10], fill=(255, 255, 255))
    d.text((x0 + 8, y0 + 6), text, fill=(90, 84, 76))
    return im


def _warp(im: Image.Image, seed: int, amp: float = 6.0) -> Image.Image:
    """
    轻微坐标扰动：模拟手绘/翻拍的必然差异。
    amp 越大越"不像"，用于控制与母题的相似度落点。
    """
    rnd = random.Random(seed)
    src = im.load()
    out = Image.new("RGB", (SIZE, SIZE))
    dst = out.load()
    # 预生成正弦扰动表，避免逐像素算 sin
    dx = [int(amp * math.sin(2 * math.pi * x / 97.0) + amp * 0.6 * math.sin(2 * math.pi * x / 31.0))
          for x in range(SIZE)]
    dy = [int(amp * math.cos(2 * math.pi * y / 89.0) + amp * 0.5 * math.cos(2 * math.pi * y / 43.0))
          for y in range(SIZE)]
    jitter = rnd.uniform(-amp, amp)
    for y in range(SIZE):
        yy = min(SIZE - 1, max(0, y + dy[y]))
        for x in range(SIZE):
            xx = min(SIZE - 1, max(0, x + dx[x] + int(jitter)))
            dst[x, y] = src[xx, yy]
    return out


# ---------------------------------------------------------------- 各类作品
def make_derived_butterfly() -> Image.Image:
    """改良蝴蝶：母题本体保留，配色改为五色渐变 + 边缘双线框。→ 与 MO-001 高度相似"""
    base = _load("motif_butterfly.jpg")
    base = _hue_shift(base, 46)           # 明显换色，但纹样结构不动
    base = _contrast(base, 1.12)
    base = _warp(base, seed=11, amp=4.0)  # 轻微手绘差异
    d = ImageDraw.Draw(base)
    # 双线边框 = 申请书上写的"独创性表达"
    for off, col in ((10, (150, 60, 60)), (16, (44, 62, 95))):
        d.rectangle([off, off, SIZE - off, SIZE - off], outline=col, width=2)
    return _stamp(base, "SEED 改良蝴蝶")


def make_butterfly_copy() -> Image.Image:
    """直接复制件：只换底色。→ 与 MO-001 相似度 > 0.92"""
    base = _load("motif_butterfly.jpg")
    bg = Image.new("RGB", (SIZE, SIZE), (250, 240, 222))
    # 用原图亮度做蒙版贴到新底色上，纹样关系完全保留
    mask = base.convert("L").point(lambda x: 255 if x < 200 else 0)
    bg.paste(base, (0, 0), mask)
    return _stamp(bg, "SEED 直接复制")


def make_lotus_screen() -> Image.Image:
    """缠枝莲屏风：扭曲骨架比例 + 双面异色感。→ 与 MO-002 高相似"""
    base = _load("motif_lotus.jpg")
    base = _hue_shift(base, -28)
    base = _contrast(base, 1.18)
    base = _warp(base, seed=23, amp=7.5)
    d = ImageDraw.Draw(base)
    d.rectangle([24, 24, SIZE - 24, SIZE - 24], outline=(166, 124, 46), width=3)
    return _stamp(base, "SEED 缠枝莲屏风")


def make_huiwen_cushion() -> Image.Image:
    """回纹坐垫：回纹仅作地纹，中央叠自绘折枝花。→ 与 MO-003 中等相似"""
    base = _load("motif_huiwen.jpg")
    base = _hue_shift(base, 120)
    base = _contrast(base, 1.05)
    base = _warp(base, seed=37, amp=6.0)
    d = ImageDraw.Draw(base)
    # 中央八瓣折枝花
    cx = cy = SIZE // 2
    for i in range(8):
        a = 2 * math.pi * i / 8
        x = cx + 78 * math.cos(a)
        y = cy + 78 * math.sin(a)
        d.ellipse([x - 34, y - 34, x + 34, y + 34], outline=(140, 47, 47), width=3)
    d.ellipse([cx - 40, cy - 40, cx + 40, cy + 40], outline=(44, 62, 95), width=3)
    return _stamp(base, "SEED 回纹坐垫")


def make_silver_bracelet() -> Image.Image:
    """银饰镯：回纹带 + 自绘蝴蝶。→ 与 MO-003 中等相似"""
    base = Image.new("RGB", (SIZE, SIZE), (238, 236, 230))
    d = ImageDraw.Draw(base)
    # 镯身圆柱感：上下两条回纹带
    for y0 in (46, SIZE - 96):
        d.rectangle([0, y0, SIZE, y0 + 50], fill=(206, 208, 212), outline=(150, 152, 158))
        for x in range(-20, SIZE, 34):
            d.line([(x, y0 + 4), (x + 20, y0 + 4), (x + 20, y0 + 46), (x + 8, y0 + 46),
                    (x + 8, y0 + 16), (x + 34, y0 + 16)], fill=(104, 106, 112), width=2)
    # 中央蝴蝶
    cx, cy = SIZE // 2, SIZE // 2
    d.ellipse([cx - 9, cy - 58, cx + 9, cy + 58], fill=(170, 172, 178), outline=(104, 106, 112))
    for sx in (-1, 1):
        # 左右各一只翅膀：先算中心，再用半宽展开成 x0/x1，避免 x1 < x0
        wx, wy = cx + sx * 42, cy - 36
        d.ellipse([wx - 46, wy - 30, wx + 46, wy + 30], outline=(140, 47, 47), width=3)
        lx, ly = cx + sx * 34, cy + 30
        d.ellipse([lx - 34, ly - 26, lx + 34, ly + 26], outline=(44, 62, 95), width=3)
    base = base.filter(ImageFilter.SMOOTH)
    return _stamp(base, "SEED 银饰镯")


def make_butterfly_screen() -> Image.Image:
    """苗绣屏风（草稿态）：母题放大 + 重排。"""
    base = _load("motif_butterfly.jpg")
    base = _hue_shift(base, 188)
    base = _warp(base, seed=53, amp=9.0)
    base = _contrast(base, 0.94)
    d = ImageDraw.Draw(base)
    for i in range(3):
        y = 60 + i * 140
        d.line([(30, y), (SIZE - 30, y)], fill=(120, 110, 96), width=1)
    return _stamp(base, "SEED 苗绣屏风")


def make_original_fish() -> Image.Image:
    """原创鱼纹蜡染：完全不同的构图。→ 与全部母题低相似"""
    base = Image.new("RGB", (SIZE, SIZE), (246, 248, 250))
    d = ImageDraw.Draw(base)
    # 蜡染冰裂纹
    rnd = random.Random(7)
    for _ in range(90):
        x, y = rnd.randrange(SIZE), rnd.randrange(SIZE)
        d.line([(x, y), (x + rnd.randint(-40, 40), y + rnd.randint(-40, 40))],
               fill=(214, 220, 228), width=1)
    # 鱼身
    d.ellipse([96, 168, 336, 320], fill=(52, 88, 142), outline=(28, 52, 88), width=4)
    d.polygon([(336, 244), (420, 186), (420, 302)], fill=(52, 88, 142), outline=(28, 52, 88))
    d.ellipse([136, 210, 176, 250], fill=(246, 248, 250), outline=(28, 52, 88), width=3)
    d.ellipse([150, 224, 164, 238], fill=(28, 52, 88))
    for i in range(5):
        x = 200 + i * 26
        d.arc([x, 200, x + 40, 292], start=280, end=80, fill=(180, 200, 224), width=3)
    # 水波
    for i in range(4):
        y = 60 + i * 26
        d.arc([40, y, 440, y + 60], start=200, end=340, fill=(160, 186, 214), width=2)
    return _stamp(base, "SEED 原创鱼纹")


def make_original_moon() -> Image.Image:
    """原创「山间月」：月相 + 山脊折线。→ 与全部母题低相似"""
    base = Image.new("RGB", (SIZE, SIZE), (30, 34, 46))
    d = ImageDraw.Draw(base)
    # 月相（三个叠圆）
    d.ellipse([300, 60, 420, 180], fill=(238, 230, 200))
    d.ellipse([270, 60, 390, 180], fill=(30, 34, 46))
    # 山脊
    pts = [(0, 380)]
    rnd = random.Random(19)
    x = 0
    while x < SIZE:
        x += rnd.randint(30, 70)
        pts.append((x, 300 + rnd.randint(-46, 46)))
    pts += [(SIZE, 380), (SIZE, SIZE), (0, SIZE)]
    d.polygon(pts, fill=(72, 82, 104))
    # 山脊线
    d.line(pts[:-3], fill=(150, 164, 190), width=2)
    # 星点
    for _ in range(46):
        x, y = rnd.randrange(SIZE), rnd.randrange(200)
        d.point((x, y), fill=(220, 226, 240))
    return _stamp(base, "SEED 山间月")


JOBS = {
    "seed_butterfly_sleeve.jpg": make_derived_butterfly,
    "seed_butterfly_copy.jpg": make_butterfly_copy,
    "seed_lotus_screen.jpg": make_lotus_screen,
    "seed_huiwen_cushion.jpg": make_huiwen_cushion,
    "seed_silver_bracelet.jpg": make_silver_bracelet,
    "seed_butterfly_screen.jpg": make_butterfly_screen,
    "seed_fish_scarf.jpg": make_original_fish,
    "seed_moon_valley.jpg": make_original_moon,
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"输出目录：{OUT}")
    for name, fn in JOBS.items():
        p = OUT / name
        im = fn()
        im.save(p, "JPEG", quality=88, optimize=True)
        print(f"  ✓ {name:<28} {p.stat().st_size/1024:6.1f} KB  {im.size[0]}×{im.size[1]}")
    print(f"\n共生成 {len(JOBS)} 张演示作品图。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
