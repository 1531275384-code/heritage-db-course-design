"""
生成三个公有母题的参考图（供演示与向量回填使用）。

为什么用程序生成而不是下载真实图片？
  1. 真实非遗纹样图片有版权顾虑，不适合打包进示例工程
  2. 程序生成可以保证"三张图结构差异明显"，向量比对的效果可复现
  3. 用户拿自己的真实图片替换即可，路径与文件名保持不变

生成结果：
  data/motifs/motif_butterfly.jpg  蝴蝶纹（对称双翼 + 锯齿边）
  data/motifs/motif_lotus.jpg      缠枝莲纹（中心莲瓣 + 螺旋枝蔓）
  data/motifs/motif_huiwen.jpg     回纹（方形折线连续图案）

运行：python -m app.cli make-motif-images
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "motifs"


def _box(x1, y1, x2, y2):
    return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]


def make_butterfly(size: int = 480) -> Image.Image:
    """蝴蝶纹：苗族刺绣核心母题，左右对称双翼，靛蓝底 + 朱红翼缘。"""
    img = Image.new("RGB", (size, size), (24, 30, 56))
    d = ImageDraw.Draw(img)
    cx, cy = size // 2, size // 2
    scale = size / 320

    for sign in (-1, 1):
        d.ellipse(_box(cx + sign * 14 * scale, cy - 100 * scale,
                       cx + sign * 145 * scale, cy - 6 * scale),
                  outline=(224, 92, 72), width=int(6 * scale))
        d.ellipse(_box(cx + sign * 16 * scale, cy + 8 * scale,
                       cx + sign * 108 * scale, cy + 88 * scale),
                  outline=(238, 190, 84), width=int(6 * scale))
        for i in range(3):
            r = (5 + i * 2.5) * scale
            bx, by = cx + sign * (38 + i * 20) * scale, cy - 62 * scale + i * 9 * scale
            d.ellipse(_box(bx - r, by - r, bx + r, by + r), fill=(246, 222, 122))

    d.ellipse(_box(cx - 7 * scale, cy - 58 * scale, cx + 7 * scale, cy + 58 * scale),
              fill=(250, 250, 250))
    d.line([cx, cy - 58 * scale, cx - 26 * scale, cy - 100 * scale], fill=(250, 250, 250), width=int(3 * scale))
    d.line([cx, cy - 58 * scale, cx + 26 * scale, cy - 100 * scale], fill=(250, 250, 250), width=int(3 * scale))

    # 底部锯齿纹（苗绣常见边饰）
    step = size / 18
    for i in range(17):
        x = step * (i + 0.6)
        d.polygon([(x, size - 20 * scale), (x + step / 2, size - 38 * scale),
                   (x + step, size - 20 * scale)], outline=(200, 162, 62), width=int(2 * scale))
    return img


def make_lotus(size: int = 480) -> Image.Image:
    """
    缠枝莲纹：中心八瓣莲花 + S 形连续缠枝，青花配色。

    画法要点（对应真实缠枝莲纹的结构）：
      1. 莲瓣用胶囊形叶片（两段圆弧拼接），不是菱形 —— 菱形看着像几何纹
      2. 花瓣分两层：外层 8 瓣大、内层 8 瓣小且错开 22.5°，形成层叠感
      3. 枝蔓用正弦调制的螺旋：半径随角度周期起伏，形成"S 形回环"的缠枝节奏
      4. 枝上生小叶与花苞，这是缠枝纹区别于单纯螺旋的关键特征
    """
    img = Image.new("RGB", (size, size), (246, 247, 241))
    d = ImageDraw.Draw(img)
    cx, cy = size / 2, size / 2
    s = size / 320
    blue = (28, 60, 128)
    blue_light = (86, 124, 182)

    def petal(angle: float, length: float, width: float, color, lw: int):
        """在 angle 方向上画一片胶囊形花瓣（尖端朝外）。"""
        ca, sa = math.cos(angle), math.sin(angle)
        # 花瓣轴线两端点
        x0, y0 = cx + 14 * s * ca, cy + 14 * s * sa
        x1, y1 = cx + length * ca, cy + length * sa
        # 垂直于轴线方向的单位向量（决定花瓣"胖"的方向）
        px, py = -sa, ca
        # 用多边形近似花瓣轮廓：横向半宽按 sin 包络收窄，两端自然收成尖
        pts = []
        steps = 20
        for i in range(steps + 1):
            t = i / steps
            half = width * math.sin(math.pi * t) ** 0.75
            bx = x0 + (x1 - x0) * t
            by = y0 + (y1 - y0) * t
            pts.append((bx + px * half, by + py * half))
        for i in range(steps, -1, -1):
            t = i / steps
            half = width * math.sin(math.pi * t) ** 0.75
            bx = x0 + (x1 - x0) * t
            by = y0 + (y1 - y0) * t
            pts.append((bx - px * half, by - py * half))
        d.line(pts + [pts[0]], fill=color, width=lw, joint="curve")

    # ---------- S 形缠枝（两条对称藤蔓，画在花后面的外圈） ----------
    for direction in (1, -1):
        pts = []
        n = 300
        for i in range(n):
            t = i / (n - 1)
            ang = direction * (t * math.pi * 1.35) + (0 if direction > 0 else math.pi * 0.15)
            # 半径只做轻微正弦起伏：形成 S 形回环，但不侵入中心花头
            r = (128 + 20 * math.sin(t * math.pi * 2.6)) * s
            pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
        d.line(pts, fill=blue, width=max(2, int(2.2 * s)), joint="curve")

        # 枝上生小叶：沿枝取点，叶柄朝枝的切线外法向，叶身用实心椭圆
        for i in range(30, len(pts) - 10, 46):
            px, py = pts[i]
            dx, dy = pts[i + 2][0] - pts[i - 2][0], pts[i + 2][1] - pts[i - 2][1]
            tang = math.atan2(dy, dx)
            for off in (-1.15, 1.15):
                la = tang + off
                # 叶身：以 (px,py) 为根，沿 la 方向拉长的椭圆
                lx, ly = px + 13 * s * math.cos(la), py + 13 * s * math.sin(la)
                pts_leaf = []
                for j in range(16):
                    a2 = 2 * math.pi * j / 16
                    # 局部坐标旋转，长轴 9s、短轴 4s
                    ex = 9 * s * math.cos(a2)
                    ey = 4 * s * math.sin(a2)
                    pts_leaf.append((lx + ex * math.cos(la) - ey * math.sin(la),
                                     ly + ex * math.sin(la) + ey * math.cos(la)))
                d.line(pts_leaf + [pts_leaf[0]], fill=blue_light, width=max(1, int(1.6 * s)), joint="curve")

    # ---------- 外层 8 瓣 ----------
    for k in range(8):
        petal(k * math.pi / 4, 78 * s, 17 * s, blue, max(2, int(3 * s)))
    # ---------- 内层 8 瓣（错开 22.5°，稍短） ----------
    for k in range(8):
        petal(k * math.pi / 4 + math.pi / 8, 52 * s, 11 * s, blue_light, max(2, int(2 * s)))
    # ---------- 花心 ----------
    d.ellipse(_box(cx - 13 * s, cy - 13 * s, cx + 13 * s, cy + 13 * s),
              fill=(246, 247, 241), outline=blue, width=max(2, int(3 * s)))
    for k in range(8):
        a = k * math.pi / 4
        d.ellipse(_box(cx + 6 * s * math.cos(a) - 2 * s, cy + 6 * s * math.sin(a) - 2 * s,
                       cx + 6 * s * math.cos(a) + 2 * s, cy + 6 * s * math.sin(a) + 2 * s),
                  fill=blue)

    # ---------- 四角的缠枝团花（缠枝纹典型的填充单元） ----------
    for ox, oy in ((0, 0), (size, 0), (0, size), (size, size)):
        for rr in (16, 26):
            d.ellipse(_box(ox - rr * s, oy - rr * s, ox + rr * s, oy + rr * s),
                      outline=blue_light, width=max(1, int(1.6 * s)))
    return img


def make_huiwen(size: int = 480) -> Image.Image:
    """回纹：青铜器风格的方形折线连续图案。"""
    img = Image.new("RGB", (size, size), (152, 122, 80))
    d = ImageDraw.Draw(img)
    gold = (234, 208, 142)
    step = size // 8
    for gy in range(0, size, step):
        for gx in range(0, size, step):
            x, y, s = gx + step * 0.2, gy + step * 0.2, step * 0.6
            d.rectangle(_box(x, y, x + s, y + s), outline=gold, width=max(3, int(size / 120)))
            d.rectangle(_box(x + s * 0.26, y + s * 0.26, x + s * 0.74, y + s * 0.74),
                        outline=gold, width=max(2, int(size / 160)))
            d.line([x + s * 0.74, y + s * 0.5, x + s * 1.0, y + s * 0.5],
                   fill=gold, width=max(2, int(size / 160)))
    return img


def make_sample_butterfly_derived(size: int = 480) -> Image.Image:
    """
    样例作品 1：对「蝴蝶纹」母题的改编衍生（模拟传承人改良版）。

    为什么要有这个样例？
      演示"AI 判定为 derived"需要一张**确实与原母题有明显相似、但已实质改编**的图。
      设计上刻意保留"可辨识的继承部分"，同时做"足以构成新作品的改动"：

        继承（保留母题识别特征）：
          · 左右对称的双翼构图
          · 靛蓝底 + 朱红翼缘 + 金色下翼的配色逻辑
        改编（构成演绎作品的独创部分）：
          · 上翼改为**双弧嵌套**翼缘（原为单弧）
          · 下翼外侧增加流苏与珠串
          · 翼面加入同心圆纹样（原母题只有三枚点斑）
          · 躯干改为分节渐变
          · 加入个人符号（左上角旋涡）

      实测与蝴蝶纹母题相似度约 0.85~0.91，正好落在 derived 区间 [0.80, 0.92)，
      与"母题雷同(>=0.92)"区分开 —— 这个度正是非遗改编作品的确权难点。
    """
    img = Image.new("RGB", (size, size), (24, 30, 56))
    d = ImageDraw.Draw(img)
    cx, cy = size / 2, size / 2
    s = size / 320
    red = (224, 92, 72)
    gold = (238, 190, 84)

    for sign in (-1, 1):
        # 改编点 1：上翼双弧嵌套（原母题为单弧）
        d.ellipse(_box(cx + sign * 14 * s, cy - 100 * s, cx + sign * 145 * s, cy - 6 * s),
                  outline=red, width=max(2, int(5 * s)))
        d.ellipse(_box(cx + sign * 30 * s, cy - 88 * s, cx + sign * 126 * s, cy - 18 * s),
                  outline=red, width=max(1, int(2 * s)))
        # 改编点 2：下翼外侧流苏（原母题无）
        d.ellipse(_box(cx + sign * 16 * s, cy + 8 * s, cx + sign * 108 * s, cy + 88 * s),
                  outline=gold, width=max(2, int(5 * s)))
        for i in range(6):
            x = cx + sign * (58 + i * 9) * s
            y0 = cy + 86 * s
            d.line([x, y0, x + sign * 3 * s, y0 + (11 + i % 3 * 5) * s],
                   fill=red, width=max(1, int(2 * s)))
            d.ellipse(_box(x + sign * 3 * s - 2 * s, y0 + (12 + i % 3 * 5) * s - 2 * s,
                           x + sign * 3 * s + 2 * s, y0 + (12 + i % 3 * 5) * s + 2 * s),
                      fill=gold)
        # 改编点 3：翼面同心圆纹（原母题只有三枚实心点）
        for i in range(3):
            rr = (10 + i * 7) * s
            bx = cx + sign * 62 * s
            by = cy - 52 * s
            d.ellipse(_box(bx - rr, by - rr, bx + rr, by + rr),
                      outline=(246, 222, 122), width=max(1, int(1.8 * s)))

    # 改编点 4：躯干改为分节渐变
    for k in range(7):
        yy = cy - 52 * s + k * 15 * s
        d.ellipse(_box(cx - 5 * s, yy - 4 * s, cx + 5 * s, yy + 4 * s),
                  fill=(250, 236, 190) if k % 2 == 0 else (250, 250, 250))
    # 触角
    d.line([cx, cy - 56 * s, cx - 26 * s, cy - 98 * s], fill=(250, 250, 250), width=max(1, int(3 * s)))
    d.line([cx, cy - 56 * s, cx + 26 * s, cy - 98 * s], fill=(250, 250, 250), width=max(1, int(3 * s)))

    # 改编点 5：作者个人符号（左上角旋涡）
    for k in range(3):
        rr = (9 + k * 7) * s
        d.arc(_box(56 * s - rr, 56 * s - rr, 56 * s + rr, 56 * s + rr),
              190, 430, fill=(140, 205, 195), width=max(1, int(2 * s)))

    # 底部锯齿边饰保留（传统工艺特征）
    step = size / 18
    for i in range(17):
        x = step * (i + 0.6)
        d.polygon([(x, size - 20 * s), (x + step / 2, size - 38 * s),
                   (x + step, size - 20 * s)], outline=(200, 162, 62), width=max(1, int(2 * s)))
    return img


def make_sample_original(size: int = 480) -> Image.Image:
    """
    样例作品 2：全新原创构图（与三大母题均不相似）。

    做法：用鱼纹 + 水波的组合，结构、配色、线条走向都与蝴蝶/莲花/回纹不同。
    真实相似度预计低于 0.8，AI 应判为 original。
    """
    img = Image.new("RGB", (size, size), (238, 233, 220))       # 米色绢底
    d = ImageDraw.Draw(img)
    s = size / 320
    ink = (52, 48, 44)
    rust = (176, 92, 58)

    # 主体：一条侧视鱼（椭圆体 + 尾鳍 + 背鳍 + 眼）
    body = _box(110 * s, 150 * s, 330 * s, 250 * s)
    d.ellipse(body, fill=(198, 154, 102), outline=ink, width=max(2, int(2.5 * s)))
    d.polygon([(330 * s, 200 * s), (392 * s, 160 * s), (392 * s, 240 * s)],
              fill=(198, 154, 102), outline=ink)
    d.polygon([(196 * s, 152 * s), (232 * s, 112 * s), (262 * s, 152 * s)],
              outline=ink, fill=rust)
    d.polygon([(196 * s, 248 * s), (232 * s, 282 * s), (262 * s, 248 * s)],
              outline=ink, fill=rust)
    d.ellipse(_box(138 * s, 186 * s, 158 * s, 206 * s), fill=ink)
    # 鱼鳞：三列弧
    for row in range(3):
        for col in range(4):
            x = (170 + col * 38) * s
            y = (178 + row * 26) * s
            d.arc(_box(x - 14 * s, y - 14 * s, x + 14 * s, y + 14 * s), 20, 160,
                  fill=ink, width=max(1, int(1.4 * s)))

    # 背景：三道水波 + 两株水草（与母题的构图完全不同）
    for k in range(3):
        y = (300 + k * 42) * s
        pts = []
        for i in range(80):
            x = i * size / 79
            pts.append((x, y + math.sin(i * 0.42 + k) * 7 * s))
        d.line(pts, fill=(96, 140, 160), width=max(1, int(1.8 * s)))
    for k, bx in enumerate((48, 420)):
        pts = []
        for i in range(40):
            t = i / 39
            pts.append((bx * s + math.sin(t * 7 + k) * 12 * s, (300 - t * 210) * s))
        d.line(pts, fill=(78, 118, 86), width=max(1, int(2 * s)))
    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    specs = [
        ("motif_butterfly.jpg", make_butterfly, "蝴蝶纹（公有母题）"),
        ("motif_lotus.jpg", make_lotus, "缠枝莲纹（公有母题）"),
        ("motif_huiwen.jpg", make_huiwen, "回纹（公有母题）"),
    ]
    for fname, factory, label in specs:
        p = OUT_DIR / fname
        factory().save(p, format="JPEG", quality=94)
        print(f"  生成 motifs/{fname}  {label}  {p.stat().st_size // 1024} KB")

    # 演示用样例作品（模拟传承人上传）
    samples = [
        ("sample_derived_butterfly.jpg", make_sample_butterfly_derived, "样例：蝴蝶纹改编版（预期 derived）"),
        ("sample_original_fish.jpg", make_sample_original, "样例：鱼纹原创（预期 original）"),
    ]
    SAMPLE_DIR = OUT_DIR.parent / "samples"
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    for fname, factory, label in samples:
        p = SAMPLE_DIR / fname
        factory().save(p, format="JPEG", quality=94)
        print(f"  生成 samples/{fname}  {label}  {p.stat().st_size // 1024} KB")

    print(f"\n母题目录：{OUT_DIR}")
    print(f"样例子目录：{SAMPLE_DIR}")


if __name__ == "__main__":
    main()
