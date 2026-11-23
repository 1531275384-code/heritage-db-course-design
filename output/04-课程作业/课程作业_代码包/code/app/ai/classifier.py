"""
分级建议器：把「相似度数字」翻译成「权属级别建议」。

⚠️  这是本项目最关键的合规设计点：
    本模块 **只产出建议**，绝不写 asset.right_level。
    写入路径是：audit_record(state='pending') + asset.ai_suggest_level。
    终审结论只能由专家通过 /api/audit/{id}/approve 写 business 事务落地。

分级逻辑（阈值语义）：
    设 s = 与最相似公有母题的 cosine 相似度

    s >= 0.92  →  public     高度重合于公有母题，属公有领域传统母题，
                             传承人不能主张独占，只能登记"使用"事实
    0.80 <= s < 0.92 且 相似母题 >= 2
               →  derived    与多个公有母题有实质重合，判定为改编衍生：
                             传承人有演绎著作权，但需标注母题血缘并受公有领域限制
    s < 0.80   →  original   与现存公有母题无实质重合，倾向认定为全新原创

    例外：若图像与**已有私有资产**高度相似（>=0.95），说明可能是抄袭他人作品，
         这种情况不参与分级建议，直接标记 duplicate 交由专家人工判定。

置信度置信度 conf 的构造（不是拍脑袋，而是给专家一个"该不该相信 AI"的提示）：
    - 与阈值的距离越远，置信度越高（margin 越大越自信）
    - top1 与 top2 的差距越大，说明结论越不被"并列候选"干扰
    - 两个因子相乘后再压缩到 [0.05, 0.99]

⚠️  阈值是经验值，必须在真实数据上标定（ROC/PR 曲线找最佳切点）。
    这里给出默认值让系统可用，README 中说明了标定方法。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings


@dataclass
class SimilarityHit:
    """一条候选（公有母题或私有资产）。"""
    kind: str                      # public_motif / private_asset
    id: int
    code: str
    name: str
    similarity: float              # cosine 相似度，[-1, 1]，实际在 [0,1]
    distance: float = 0.0          # pgvector 返回的 cosine 距离 = 1 - similarity
    extra: dict = field(default_factory=dict)


@dataclass
class Suggestion:
    level: str                     # public / derived / original
    confidence: float              # 0~1
    reason: str                    # 人可读的判定依据（给专家看）
    rule: str                      # 命中的规则标识，便于后期统计规则命中率
    flagged_as: str | None = None  # 'duplicate' 时表示疑似抄袭他人私有作品
    margin: float = 0.0            # 距离最近阈值的余量，专家可据此判断边界情况


def suggest_level(
    motif_hits: list[SimilarityHit],
    asset_hits: list[SimilarityHit] | None = None,
) -> Suggestion:
    """
    根据 top-K 相似结果给出分级建议。

    motif_hits: 与公有母题的相似结果（按相似度降序）
    asset_hits: 与已有私有资产的相似结果（按相似度降序）
    """
    asset_hits = asset_hits or []

    # ---------- 前置检查：疑似抄袭他人私有资产 ----------
    if asset_hits:
        top_dup = asset_hits[0]
        if top_dup.similarity >= 0.95:
            return Suggestion(
                level="derived",
                confidence=min(0.99, top_dup.similarity),
                reason=(
                    f"与已有资产 #{top_dup.id}「{top_dup.name}」相似度 {top_dup.similarity:.4f}，"
                    f"超过抄袭判定线 0.95。AI 无法确认权属，强烈建议人工核查原始创作过程。"
                ),
                rule="DUPLICATE_SUSPECT",
                flagged_as="duplicate",
                margin=top_dup.similarity - 0.95,
            )

    # ---------- 无母题可比对：只能判原创 ----------
    if not motif_hits:
        return Suggestion(
            level="original",
            confidence=0.55,
            reason="母题库中无可用特征向量，无法比对。默认倾向原创，请专家结合创作过程材料人工判定。",
            rule="NO_MOTIF_BASELINE",
            margin=0.0,
        )

    s1 = motif_hits[0].similarity
    s2 = motif_hits[1].similarity if len(motif_hits) > 1 else 0.0
    public_th = settings.SIM_PUBLIC_THRESHOLD
    derived_th = settings.SIM_DERIVED_THRESHOLD

    # 与最近阈值的余量：越大越"安全"，越小越"边界情况"
    margin = min(abs(s1 - public_th), abs(s1 - derived_th))
    # 候选并列度：top1 与 top2 越接近，结论越可能被另一个母题改写
    separation = max(0.0, s1 - s2)
    # 置信度 = 阈值距离 × 候选分离度（两者都归一化后相乘，再压缩）
    conf = _squash(margin * 8.0) * _squash(separation * 6.0 + 0.35)
    conf = round(max(0.05, min(0.99, conf)), 4)

    top = motif_hits[0]
    if s1 >= public_th:
        return Suggestion(
            level="public",
            confidence=conf,
            reason=(
                f"与公有母题「{top.name}」({top.code}) 相似度 {s1:.4f} ≥ {public_th}，"
                f"视觉特征与该传统纹样高度重合。该类母题属公有领域，"
                f"建议认定为公有母题使用，禁止个人独占确权。"
            ),
            rule="SIM>=PUBLIC_THRESHOLD",
            margin=round(s1 - public_th, 4),
        )

    if s1 >= derived_th:
        multi = len([h for h in motif_hits if h.similarity >= derived_th])
        if multi >= 2:
            names = "、".join(f"「{h.name}」" for h in motif_hits[:multi][:3])
            return Suggestion(
                level="derived",
                confidence=conf,
                reason=(
                    f"与 {multi} 个公有母题同时存在实质重合（{names}），"
                    f"最高相似度 {s1:.4f} 落在 [{derived_th}, {public_th}) 区间。"
                    f"建议认定为改编衍生作品：传承人对改编部分享有演绎著作权，"
                    f"但须标注母题血缘，且不得限制他人对原始母题的使用。"
                ),
                rule="MULTI_MOTIF_OVERLAP",
                margin=round(s1 - derived_th, 4),
            )
        return Suggestion(
            level="derived",
            confidence=round(conf * 0.9, 4),
            reason=(
                f"与公有母题「{top.name}」相似度 {s1:.4f} 落在 [{derived_th}, {public_th}) 区间，"
                f"存在明显借鉴但不构成整体复制。建议认定为改编衍生，请专家核验改编幅度。"
            ),
            rule="SIM_IN_DERIVED_BAND",
            margin=round(s1 - derived_th, 4),
        )

    return Suggestion(
        level="original",
        confidence=conf,
        reason=(
            f"与最相似的公有母题「{top.name}」相似度仅 {s1:.4f} < {derived_th}，"
            f"未发现与现存公有母题的实质重合。建议倾向认定为原创作品，"
            f"请专家结合创作手稿、时间戳等材料终审。"
        ),
        rule="SIM<DERIVED_THRESHOLD",
        margin=round(derived_th - s1, 4),
    )


def _squash(x: float) -> float:
    """把任意实数平滑压缩到 (0,1)：x=0→0.5, x=1→0.73, x=-1→0.27。"""
    import math
    return 1.0 / (1.0 + math.exp(-x))


def level_label(level: str) -> str:
    """级别 → 中文标签，前后端共用同一套措辞。"""
    return {
        "public": "公有母题",
        "derived": "改编衍生",
        "original": "原创作品",
    }.get(level, level)
