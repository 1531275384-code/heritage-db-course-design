"""
种子数据一致性校验：确认 data/seed_demo.json 里写的 AI 结论，
是**真实特征后端算出来的结果**，而不是人手编的数字。

为什么必须校验：
  种子数据里写着「AI 建议：改编衍生，置信度 0.4741」。如果这个数字与
  真实算法对这张图跑出来的结果不符，演示时会出现诡异现象 ——
  台账显示 derived，但点「AI 分析」重跑一遍变成 original。
  评审方一眼就能看出数据是编的，整套演示的可信度归零。

  所以：AI 字段必须由算法生成，本脚本负责在提交前把它们钉死。
  用 --write 可以把当前算法结果回写进 JSON（改图后重跑一次即可）。

运行：
  python -m scripts.check_seed         # 只校验，不一致则退出码 1
  python -m scripts.check_seed --write # 用当前算法结果覆盖 JSON 中的 AI 字段
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from app import demo
from app.ai.classifier import suggest_level
from app.config import UPLOAD_DIR, settings

ROOT = Path(__file__).resolve().parent.parent
SEED_FILE = ROOT / "data" / "seed_demo.json"

# 允许的误差：算法改动（如权重微调）会让末位跳动，不该因此判为不一致
TOL_YEAR = 0.02


def _level_of(vec: np.ndarray, asset_id: int) -> tuple[str, float, float, int | None]:
    """跑一次与 analyze_asset 完全相同的判定，返回 (级别, 置信度, 最高相似度, 母题 id)。"""
    hits = demo._search_motifs(vec, settings.TOP_K)
    others = demo._search_assets(vec, asset_id, settings.TOP_K)
    sug = suggest_level(hits, others)
    top_sim = hits[0].similarity if hits else 0.0
    top_id = hits[0].id if hits else None
    return sug.level, sug.confidence, top_sim, top_id


def _all_sims(vec: np.ndarray) -> dict[int, float]:
    """与检索逻辑一致的全量母题相似度（降序无关，返回 dict 便于按 id 取）。"""
    out: dict[int, float] = {}
    for mid in demo.STORE.motifs:
        mv = demo.STORE.motif_vector(mid)
        if mv is None:
            continue
        out[mid] = round(float(np.clip(vec @ mv, 0.0, 1.0)), 4)
    return out


def main(argv: list[str]) -> int:
    write = "--write" in argv

    data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    demo.activate()
    store = demo.STORE
    # reset() 已按 JSON 建好 8 张表；这里补齐向量
    store.warm_all()

    print("=" * 96)
    print(" 种子数据一致性校验 · AI 字段是否为真实算法输出")
    print("=" * 96)
    print(f" 特征后端：{demo.current_backend_info()}")
    print(f" 阈值：public ≥ {settings.SIM_PUBLIC_THRESHOLD}　derived ≥ {settings.SIM_DERIVED_THRESHOLD}")
    print()

    bad: list[str] = []
    hdr = f"{'id':>3}  {'作品':<26} {'算法判定':<9} {'种子判定':<9} {'相似度':>7} {'置信度':>7}  结论"
    print(hdr)
    print("-" * 96)

    for spec in data["assets"]:
        aid = spec["asset_id"]
        key = spec["file_path"]
        path = (UPLOAD_DIR / key).resolve()
        if not path.exists():
            bad.append(f"#{aid} 图片缺失：{path}")
            print(f"{aid:>3}  {spec['title'][:24]:<26} — 图片缺失 {key}")
            continue

        vec = np.asarray(store.feature_vectors[aid]["embedding"], dtype=np.float64)
        lvl, conf, sim, top_id = _level_of(vec, aid)

        # 草稿态作品本来就没有 AI 结论，只核对"确实算不出一致结论"这条不变量
        if spec.get("ai_suggest_level") is None:
            print(f"{aid:>3}  {spec['title'][:24]:<26} {'(草稿态，跳过 AI 字段核对)':<40}")
            continue

        seed_lvl = spec["ai_suggest_level"]
        seed_conf = float(spec["ai_confidence"])
        lvl_ok = lvl == seed_lvl
        conf_ok = abs(conf - seed_conf) <= TOL_YEAR
        mark = "✓" if (lvl_ok and conf_ok) else "✗"

        print(f"{aid:>3}  {spec['title'][:24]:<26} {lvl:<9} {seed_lvl:<9} "
              f"{sim:>7.4f} {conf:>7.4f}  {mark}")

        if not lvl_ok:
            bad.append(f"#{aid} {spec['title']}：级别不一致 算法={lvl} 种子={seed_lvl}")
        if not conf_ok:
            bad.append(f"#{aid} {spec['title']}：置信度不一致 算法={conf:.4f} 种子={seed_conf:.4f}")

        sims = _all_sims(vec)
        for a in spec.get("audits") or []:
            want = a.get("similarity")
            got = sims.get(a.get("top_motif_id"))
            if want is None:
                continue
            if got is None or abs(float(want) - got) > 1e-3:
                bad.append(f"#{aid} 审核流水 similarity={want} 与算法 {got} 不符"
                           f"（注意：这里必须写相似度，不能填置信度）")

        if write:
            spec["ai_suggest_level"] = lvl
            spec["ai_confidence"] = round(conf, 4)
            # 母题血缘：公有/衍生才有依据母题，原创留空
            spec["motif_id"] = top_id if lvl in ("public", "derived") else None
            # 审核流水里的 AI 快照同步更新（只改最后一条 pending 及与之同值的记录）
            sims = _all_sims(vec)
            for a in spec.get("audits") or []:
                if a.get("state") in ("pending", "approved") or a.get("ai_suggest_level"):
                    a["ai_suggest_level"] = lvl
                    a["ai_confidence"] = round(conf, 4)
                    a["top_motif_id"] = top_id
                    # ★ 相似度必须写「该母题的余弦相似度」，不是置信度。
                    #   两者量纲完全不同，混用会让母题关联页显示错误的数值。
                    a["similarity"] = sims.get(top_id)

    print()
    if write:
        SEED_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"已回写 {SEED_FILE}")
        print("注意：回写后请重新跑一次不带 --write 的校验确认稳定（避免浮点往返差异）")
        return 0

    if bad:
        print(f"发现 {len(bad)} 处不一致：")
        for b in bad:
            print("  ✗ " + b)
        print("\n修法：python -m scripts.check_seed --write 用当前算法结果覆盖 JSON。")
        print("      若你刚换了图片，这是正常操作；若没换图，说明算法改过，需人工确认阈值是否仍合理。")
        return 1

    print("全部一致：种子里的 AI 结论确实是真实算法算出来的。")
    # 顺便把母题血缘也核一遍
    print("\n母题血缘核对：")
    for spec in data["assets"]:
        if spec.get("ai_suggest_level") is None:
            continue
        aid = spec["asset_id"]
        vec = np.asarray(store.feature_vectors[aid]["embedding"], dtype=np.float64)
        _, _, _, top_id = _level_of(vec, aid)
        expect = top_id if spec["ai_suggest_level"] in ("public", "derived") else None
        ok = spec.get("motif_id") == expect
        if not ok:
            print(f"  ✗ #{aid} {spec['title']}：motif_id={spec.get('motif_id')} 期望={expect}")
        else:
            print(f"  ✓ #{aid} {spec['title']}：motif_id={expect}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
