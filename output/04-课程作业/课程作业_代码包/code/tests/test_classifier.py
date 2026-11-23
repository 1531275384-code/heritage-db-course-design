"""
分级建议器单元测试（纯逻辑，不依赖数据库）。

覆盖：三档级别的判定边界、多母题重合、抄袭嫌疑、空母题库、置信度范围。
运行：python -m tests.test_classifier
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.classifier import SimilarityHit, level_label, suggest_level  # noqa: E402
from app.config import settings  # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  \033[92m✓\033[0m {name}")
    else:
        FAIL += 1
        print(f"  \033[91m✗\033[0m {name}  {detail}")


def hit(sim: float, name: str = "蝴蝶纹", code: str = "MO-001") -> SimilarityHit:
    return SimilarityHit(
        kind="public_motif", id=1, code=code, name=name, similarity=sim, distance=round(1 - sim, 6)
    )


def main() -> int:
    pub_th = settings.SIM_PUBLIC_THRESHOLD
    der_th = settings.SIM_DERIVED_THRESHOLD
    print(f"阈值配置: public>={pub_th}, derived>={der_th}\n")

    print("【1】三档分级判定")
    s = suggest_level([hit(0.97), hit(0.90)])
    check("0.97 → public", s.level == "public", s.level)
    check("理由包含母题名", "蝴蝶纹" in s.reason)

    s = suggest_level([hit(0.86), hit(0.82)])
    check("0.86 + 双母题 → derived", s.level == "derived", s.level)
    check("命中多母题规则", s.rule == "MULTI_MOTIF_OVERLAP", s.rule)

    s = suggest_level([hit(0.86), hit(0.50)])
    check("0.86 + 单母题 → derived", s.level == "derived", s.level)
    check("命中区间规则", s.rule == "SIM_IN_DERIVED_BAND", s.rule)

    s = suggest_level([hit(0.65), hit(0.40)])
    check("0.65 → original", s.level == "original", s.level)

    print("\n【2】阈值边界（含等号的行为）")
    s = suggest_level([hit(pub_th), hit(0.0)])
    check(f"恰好 {pub_th} → public（>= 语义）", s.level == "public", s.level)
    s = suggest_level([hit(der_th), hit(0.0)])
    check(f"恰好 {der_th} → derived（>= 语义）", s.level == "derived", s.level)
    s = suggest_level([hit(der_th - 1e-6), hit(0.0)])
    check(f"略低于 {der_th} → original", s.level == "original", s.level)

    print("\n【3】抄袭嫌疑（与私有资产高度相似）")
    dup = SimilarityHit(kind="private_asset", id=42, code="A00042", name="某传承人作品",
                        similarity=0.97, distance=0.03)
    s = suggest_level([hit(0.60)], [dup])
    check("0.97 私有相似 → flagged duplicate", s.flagged_as == "duplicate", str(s.flagged_as))
    check("规则标识正确", s.rule == "DUPLICATE_SUSPECT", s.rule)
    check("理由提到资产 ID", "#42" in s.reason, s.reason)

    dup_low = SimilarityHit(kind="private_asset", id=43, code="A00043", name="另一件",
                            similarity=0.90, distance=0.10)
    s = suggest_level([hit(0.60)], [dup_low])
    check("0.90 私有相似 → 不做抄袭标记", s.flagged_as is None, str(s.flagged_as))

    print("\n【4】退化输入")
    s = suggest_level([])
    check("空母题库 → original + 低置信", s.level == "original" and s.confidence <= 0.6,
          f"{s.level}/{s.confidence}")
    check("空库规则标识", s.rule == "NO_MOTIF_BASELINE", s.rule)
    s = suggest_level([hit(0.0)])
    check("全零相似 → original", s.level == "original", s.level)

    print("\n【5】置信度合理性")
    near = suggest_level([hit(pub_th + 0.001), hit(0.0)])      # 紧贴阈值
    far = suggest_level([hit(0.999), hit(0.0)])                # 远离阈值
    check("远离阈值比紧贴阈值更自信", far.confidence > near.confidence,
          f"far={far.confidence} near={near.confidence}")
    check("置信度在 [0.05,0.99]", all(0.05 <= x.confidence <= 0.99 for x in (near, far)),
          f"{near.confidence}/{far.confidence}")
    sep_close = suggest_level([hit(0.90), hit(0.899)])         # top1/top2 几乎并列
    sep_wide = suggest_level([hit(0.90), hit(0.10)])
    check("候选分离度大者更自信", sep_wide.confidence > sep_close.confidence,
          f"wide={sep_wide.confidence} close={sep_close.confidence}")

    print("\n【6】中文标签")
    check("public → 公有母题", level_label("public") == "公有母题")
    check("derived → 改编衍生", level_label("derived") == "改编衍生")
    check("original → 原创作品", level_label("original") == "原创作品")
    check("未知值原样返回", level_label("xxx") == "xxx")

    print("\n" + "=" * 60)
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
