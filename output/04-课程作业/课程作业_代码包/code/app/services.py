"""
业务服务层：把「一次业务操作 = 一个事务」的规则集中在这里。
路由层只负责 HTTP 编解码与鉴权，不写业务 SQL —— 保证事务边界清晰、可被单独测试。

本文件重点：**终审事务**（approve_asset）
  它必须原子地完成 4 件事：
    1. SELECT ... FOR UPDATE 锁住资产行，防止两个专家同时终审同一资产
    2. 校验状态机（只有 pending 才能 approved/rejected）
    3. UPDATE audit_record   → 填专家结论
    4. UPDATE asset          → 改 audit_status + right_level
    4 步在同一事务，任一失败全部回滚；而 asset 的 UPDATE 会**自动触发**触发器
    写 system_audit_log —— 由于触发器与主业务同事务，主业务回滚时审计日志也不留痕
    （这符合"要么同时成功要么同时失败"的要求：不会出现"日志说有变更但数据没变"）。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException, status
from psycopg2 import errors as pg_errors

from app.ai.classifier import level_label, suggest_level
from app.ai.features import current_backend_info, extract_from_bytes, file_hash
from app.ai.similarity import cast_vec, explain_plan, search_motifs, search_similar_assets
from app.config import settings
from app.db import open_tx, vector_literal

log = logging.getLogger("ich.service")


# =============================================================================
#  资产上传
# =============================================================================
def create_asset(
    *,
    owner_id: int,
    title: str,
    description: str | None,
    file_bytes: bytes,
    mime_type: str,
    object_key: str,
    app_role: str,
) -> dict[str, Any]:
    """
    登记资产（状态 draft），并按 file_hash 做**幂等查重**：
    同一张图重复上传不会产生两条资产记录，而是返回已有记录。
    【数据库原理】这是「候选键」思想的落地：file_hash 是业务上的唯一标识，
    即便它没被设为 UNIQUE 约束（因为同一图像可以合法出现在不同账户），
    应用层仍需在插入前检查，避免重复确权引发权属纠纷。
    """
    fhash = file_hash(file_bytes)

    with open_tx(app_role=app_role) as cur:
        cur.execute(
            "SELECT asset_id, owner_id FROM asset WHERE file_hash = %s ORDER BY asset_id LIMIT 1",
            (fhash,),
        )
        dup = cur.fetchone()
        if dup:
            log.info("命中重复文件 hash=%s asset=%s", fhash[:12], dup["asset_id"])
            return {"duplicate_of": dup["asset_id"], "asset_id": dup["asset_id"]}

        cur.execute(
            """
            INSERT INTO asset (title, owner_id, file_path, file_hash, file_size, mime_type,
                               right_level, audit_status, description)
            VALUES (%(title)s, %(owner)s, %(path)s, %(hash)s, %(size)s, %(mime)s,
                    'public', 'draft', %(desc)s)
            RETURNING asset_id
            """,
            {
                "title": title.strip(),
                "owner": owner_id,
                "path": object_key,
                "hash": fhash,
                "size": len(file_bytes),
                "mime": mime_type,
                "desc": description,
            },
        )
        asset_id = cur.fetchone()["asset_id"]

    return {"duplicate_of": None, "asset_id": asset_id}


def get_asset(asset_id: int, app_role: str, viewer_id: int | None = None, is_privileged: bool = False) -> dict:
    """
    读取资产。传承人只能看自己的（这条规则在 SQL 里用 owner_id 过滤实现，
    而不是取回来再判断 —— 减少不必要的数据传输，也避免"先查后判"被绕过）。
    """
    sql = """
        SELECT a.*, u.display_name AS owner_name, m.name AS motif_name, m.motif_code
        FROM asset a
        JOIN app_user u ON u.user_id = a.owner_id
        LEFT JOIN public_motif m ON m.motif_id = a.motif_id
        WHERE a.asset_id = %(aid)s
          AND (%(priv)s OR a.owner_id = %(viewer)s)
    """
    with open_tx(app_role=app_role) as cur:
        cur.execute(sql, {"aid": asset_id, "priv": is_privileged, "viewer": viewer_id})
        row = cur.fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"资产 {asset_id} 不存在或无权访问")
    return dict(row)


def list_my_assets(owner_id: int, app_role: str) -> list[dict]:
    with open_tx(app_role=app_role) as cur:
        cur.execute(
            """
            SELECT a.asset_id, a.title, a.right_level, a.audit_status, a.ai_suggest_level,
                   a.ai_confidence, a.version_no, a.created_at, a.updated_at,
                   m.motif_code, m.name AS motif_name,
                   -- 生效中的授权数（列表页"授权"按钮的数标）
                   (SELECT count(*) FROM license_record l
                     WHERE l.asset_id = a.asset_id AND l.status = 'active') AS license_count
            FROM asset a
            LEFT JOIN public_motif m ON m.motif_id = a.motif_id
            WHERE a.owner_id = %s
            ORDER BY a.created_at DESC
            """,
            (owner_id,),
        )
        return [dict(r) for r in cur.fetchall()]


# =============================================================================
#  AI 分析  ★核心：只写建议，不碰 right_level★
# =============================================================================
def analyze_asset(
    *,
    asset_id: int,
    app_role: str,
    world_app_role: str,
    viewer_id: int,
    is_privileged: bool,
    include_plan: bool = True,
) -> dict[str, Any]:
    """
    分析流程（单事务）：
      1. 取资产原图 → 提取特征向量
      2. feature_vector 落库（同一模型版本重复分析则覆盖，避免无限膨胀）
      3. HNSW 检索 top-K 公有母题 + 相似私有资产
      4. 计算分级建议
      5. 写 audit_record(state='pending') 与 asset.ai_suggest_level
      6. asset.audit_status → 'pending'，进入专家工作台

    ⚠️ 全程不写 asset.right_level。数据库层把 ich_heritor 的 UPDATE 权限
       限定在除 right_level 之外的列，即便有人改代码也无法越权。
    """
    # ---- 1. 读资产（含权限校验） ----
    asset = get_asset(asset_id, app_role=app_role, viewer_id=viewer_id, is_privileged=is_privileged)
    if asset["audit_status"] == "approved":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"资产 {asset_id} 已终审通过，如需重新分析请先走变更流程（当前实现禁止覆盖已确权结论）",
        )

    # ---- 2. 提特征 ----
    try:
        with open(_abs_upload_path(asset["file_path"]), "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        raise HTTPException(status.HTTP_410_GONE, f"资产原图文件缺失：{asset['file_path']}")

    feat = extract_from_bytes(raw)

    # ---- 3. 单事务完成：落向量 + 检索 + 写建议 ----
    with open_tx(app_role=app_role) as cur:
        # 3.1 特征向量：同资产同模型仅保留一份（重算覆盖）
        cur.execute(
            """
            DELETE FROM feature_vector
            WHERE asset_id = %(aid)s AND model_name = %(m)s AND model_version = %(v)s
            """,
            {"aid": asset_id, "m": feat.model_name, "v": feat.model_version},
        )
        cur.execute(
            f"""
            INSERT INTO feature_vector (asset_id, model_name, model_version, embed_dim, embedding)
            VALUES (%(aid)s, %(m)s, %(v)s, %(d)s, {cast_vec('%(e)s')})
            RETURNING fv_id
            """,
            {
                "aid": asset_id,
                "m": feat.model_name,
                "v": feat.model_version,
                "d": feat.embed_dim,
                "e": vector_literal(feat.vector),
            },
        )
        fv_id = cur.fetchone()["fv_id"]

        # 3.2 向量检索（复用当前事务游标，避免另开连接看到未提交数据）
        motif_hits = search_motifs(feat.vector, top_k=settings.TOP_K, cur=cur)
        asset_hits = search_similar_assets(
            feat.vector, exclude_asset_id=asset_id, top_k=settings.TOP_K, cur=cur
        )

        # 3.3 生成建议（纯计算，不碰数据库）
        suggestion = suggest_level(motif_hits, asset_hits)

        # 3.4 相似度快照 → JSONB（保存当时的证据，母题库后续变更不影响历史判定）
        similarity_snapshot = [
            {
                "kind": h.kind,
                "id": h.id,
                "code": h.code,
                "name": h.name,
                "similarity": h.similarity,
                "distance": h.distance,
            }
            for h in motif_hits
        ]

        # 3.5 写审核流水（pending）
        #
        #     若该资产已有未决流水（重复分析），**不删也不改**：
        #       - 不能 DELETE：audit_record 有 trg_audit_record_no_delete（追加式表）
        #       - 不能 UPDATE 成 rejected：那会伪造一次专家驳回，
        #         凭空产生 expert_id / decided_at，污染 is_override 与专家工作量统计
        #     正确做法：让多条 pending 并存，查询侧只取最新一条
        #     （v_asset_pending_audit 用 LATERAL + ORDER BY created_at DESC LIMIT 1 保证不重复）。
        #     历史流水完整保留，这也是"追加式"应有的语义。
        cur.execute(
            """
            INSERT INTO audit_record (asset_id, state, ai_suggest_level, ai_confidence, similarity_json)
            VALUES (%(aid)s, 'pending', %(lvl)s, %(conf)s, %(sim)s::jsonb)
            RETURNING audit_id
            """,
            {
                "aid": asset_id,
                "lvl": suggestion.level,
                "conf": suggestion.confidence,
                "sim": json.dumps(similarity_snapshot, ensure_ascii=False),
            },
        )
        audit_id = cur.fetchone()["audit_id"]

        # 3.6 回写 AI 建议到 asset —— 注意 SET 列表里**没有** right_level
        #
        # motif_id 是"母题血缘"：判定为公有母题或改编衍生时，记录它依据的是哪一条母题，
        # 监管台账（v_asset_ledger 的 motif_code/motif_name）与衍生作品统计都靠它。
        # 只在判定确实挂靠到某条母题时才写；判为原创则保持 NULL（表示无血缘）。
        # 注意这不是改权属，只是记录"AI 认为它源自哪条母题"，专家终审时可推翻。
        lineage_mid = motif_hits[0].id if (motif_hits and suggestion.level in ("public", "derived")) else None
        cur.execute(
            """
            UPDATE asset
               SET ai_suggest_level = %(lvl)s,
                   ai_confidence    = %(conf)s,
                   ai_model_version = %(mv)s,
                   motif_id         = %(mid)s,
                   audit_status     = 'pending'
             WHERE asset_id = %(aid)s
            """,
            {
                "lvl": suggestion.level,
                "conf": suggestion.confidence,
                "mv": f"{feat.model_name}@{feat.model_version}",
                "mid": lineage_mid,
                "aid": asset_id,
            },
        )

        plan = explain_plan(cur, feat.vector) if include_plan else None

    log.info(
        "AI 分析完成 asset=%s audit=%s fv=%s level=%s conf=%.4f rule=%s",
        asset_id, audit_id, fv_id, suggestion.level, suggestion.confidence, suggestion.rule,
    )

    return {
        "asset_id": asset_id,
        "audit_id": audit_id,
        "suggested_level": suggestion.level,
        "confidence": suggestion.confidence,
        "reason": suggestion.reason,
        "rule": suggestion.rule,
        "flagged_as": suggestion.flagged_as,
        "margin": suggestion.margin,
        "top_k": [_hit_dict(h) for h in motif_hits],
        "similar_assets": [_hit_dict(h) for h in asset_hits],
        "model": {
            "name": feat.model_name,
            "version": feat.model_version,
            "dim": feat.embed_dim,
            **current_backend_info(),
        },
        "plan": plan,
    }


def _hit_dict(h) -> dict:
    return {
        "kind": h.kind,
        "id": h.id,
        "code": h.code,
        "name": h.name,
        "similarity": h.similarity,
        "distance": h.distance,
        "extra": h.extra,
    }


# =============================================================================
#  待审列表（走视图 v_asset_pending_audit）
# =============================================================================
def list_pending(limit: int = 100) -> list[dict]:
    with open_tx(app_role="expert") as cur:
        cur.execute(
            """
            SELECT * FROM v_asset_pending_audit
            ORDER BY submitted_at ASC NULLS LAST
            LIMIT %s
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]


# =============================================================================
#  终审  ★核心事务★
# =============================================================================
def approve_asset(
    *,
    asset_id: int,
    final_level: str,
    opinion: str,
    expert_id: int,
    expert_name: str,
) -> dict[str, Any]:
    """
    专家终审：必须在**单个事务**内完成，任何一步失败整体回滚。

    ── 并发控制说明 ────────────────────────────────────────────────
    两个专家同时点"通过"会发生什么？
      T1: BEGIN → SELECT ... FOR UPDATE (拿到行锁)
      T2: BEGIN → SELECT ... FOR UPDATE (阻塞，等待 T1)
      T1: UPDATE audit_record / UPDATE asset → COMMIT（释放锁）
      T2: 拿到锁，读到的 audit_status 已是 'approved'
          → 命中下面的状态机校验 → 抛 409 → ROLLBACK
    结论：不会出现"同一资产被终审两次"或"两条终审流水指向同一结论"。

    这里用**悲观锁**而不是乐观锁（version_no 比对），原因：
      - 终审是低频、高价值、人工触发的操作，锁等待成本可忽略
      - 乐观锁失败后要用户重试，体验差；悲观锁阻塞 100ms 后自然成功
    version_no 仍保留自增，用于台账展示"该记录被改过几次"。

    ── 触发器链路 ──────────────────────────────────────────────────
    UPDATE asset 会自动触发 trg_asset_touch(BEFORE) 与
    trg_asset_right_change(AFTER) → 写 system_audit_log。
    因为触发器就在本事务里，所以：
      事务 COMMIT → 业务数据 + 审计日志 一起生效
      事务 ROLLBACK → 业务数据 + 审计日志 一起消失
    这正是"数据一致性：要么同时成功要么回滚"的实现机制。
    """
    with open_tx(app_role="expert") as cur:
        # ---------- 步骤 1：加行锁，一次性拿到需要的字段 ----------
        cur.execute(
            """
            SELECT a.asset_id, a.title, a.right_level, a.audit_status,
                   a.ai_suggest_level, a.version_no, a.owner_id
            FROM asset a
            WHERE a.asset_id = %s
            FOR UPDATE OF a
            """,
            (asset_id,),
        )
        asset = cur.fetchone()
        if not asset:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"资产 {asset_id} 不存在")

        # ---------- 步骤 2：状态机校验 ----------
        if asset["audit_status"] == "approved":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"资产 {asset_id} 已终审通过，禁止重复终审（并发保护生效）",
            )
        if asset["audit_status"] != "pending":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"资产 {asset_id} 当前状态为 {asset['audit_status']}，只有 pending 状态可终审。"
                f"请先执行 AI 分析。",
            )

        # ---------- 步骤 3：取本次要终结的待审流水（同样加锁，避免双写） ----------
        cur.execute(
            """
            SELECT audit_id, ai_suggest_level
            FROM audit_record
            WHERE asset_id = %s AND state = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            FOR UPDATE
            """,
            (asset_id,),
        )
        audit = cur.fetchone()
        if not audit:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"资产 {asset_id} 没有待审流水（数据不一致），请重新执行 AI 分析",
            )

        is_override = bool(
            audit["ai_suggest_level"] and audit["ai_suggest_level"] != final_level
        )

        # ---------- 步骤 4：写审核结论 ----------
        cur.execute(
            """
            UPDATE audit_record
               SET state         = 'approved',
                   expert_id     = %(expert)s,
                   expert_level  = %(lvl)s,
                   opinion       = %(opinion)s,
                   is_override   = %(override)s,
                   decided_at    = now()
             WHERE audit_id = %(aid)s
            """,
            {
                "expert": expert_id,
                "lvl": final_level,
                "opinion": opinion,
                "override": is_override,
                "aid": audit["audit_id"],
            },
        )
        if cur.rowcount != 1:
            # 理论上不可达；一旦出现说明有人绕过应用改数据，必须整单回滚
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, "审核流水更新影响行数异常，已回滚"
            )

        # ---------- 步骤 5：改资产权属（触发器在此写入 system_audit_log） ----------
        cur.execute(
            """
            UPDATE asset
               SET right_level  = %(lvl)s,
                   audit_status = 'approved'
             WHERE asset_id = %(aid)s
            """,
            {"lvl": final_level, "aid": asset_id},
        )
        if cur.rowcount != 1:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, "资产更新影响行数异常，已回滚"
            )

        # ---------- 步骤 6：回读触发器产生的日志，作为"触发器确实生效"的证据 ----------
        cur.execute(
            """
            SELECT log_id, changed_fields FROM system_audit_log
            WHERE table_name = 'asset' AND record_id = %s AND operation = 'UPDATE'
              AND logged_at >= now() - interval '5 seconds'
            ORDER BY log_id DESC
            """,
            (asset_id,),
        )
        logs = cur.fetchall()

        cur.execute("SELECT right_level, audit_status, version_no FROM asset WHERE asset_id = %s", (asset_id,))
        final_row = cur.fetchone()

    return {
        "asset_id": asset_id,
        "audit_id": audit["audit_id"],
        "right_level": final_row["right_level"],
        "audit_status": final_row["audit_status"],
        "is_override": is_override,
        "expert_name": expert_name,
        "db_audit_log_ids": [r["log_id"] for r in logs],
        "message": (
            f"终审完成：{level_label(final_row['right_level'])}"
            + ("（专家推翻了 AI 建议）" if is_override else "（与 AI 建议一致）")
        ),
    }


def reject_asset(*, asset_id: int, opinion: str, expert_id: int) -> dict[str, Any]:
    """驳回：资产退回 draft，权属级别保持不变（不因驳回而改写权属）。"""
    with open_tx(app_role="expert") as cur:
        cur.execute("SELECT audit_status FROM asset WHERE asset_id = %s FOR UPDATE", (asset_id,))
        asset = cur.fetchone()
        if not asset:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"资产 {asset_id} 不存在")
        if asset["audit_status"] != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, f"只有 pending 状态可驳回，当前 {asset['audit_status']}")

        # 只驳回归属本次决策的最新一条待审流水（与 approve_asset 保持一致）。
        #
        # WHY 不能按 `WHERE asset_id = %s AND state = 'pending'` 批量更新：
        #   若该资产被重复分析过，会存在多条 pending，
        #   批量更新会把**所有**历史 pending 一次性判成 rejected，
        #   让"一次驳回"看起来像"驳回了很多次"，污染审核流水与专家工作量统计。
        #   这里用子查询按 created_at DESC 取最新一条，并加 FOR UPDATE 锁住它。
        cur.execute(
            """
            UPDATE audit_record
               SET state = 'rejected', expert_id = %s, opinion = %s,
                   expert_level = ai_suggest_level, decided_at = now()
             WHERE audit_id = (
                     SELECT audit_id FROM audit_record
                      WHERE asset_id = %s AND state = 'pending'
                      ORDER BY created_at DESC
                      LIMIT 1
                      FOR UPDATE
                   )
             RETURNING audit_id
            """,
            (expert_id, opinion, asset_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status.HTTP_409_CONFLICT, "没有待审流水可驳回")

        cur.execute("UPDATE asset SET audit_status = 'rejected' WHERE asset_id = %s", (asset_id,))
        audit_id = row["audit_id"]

    return {"asset_id": asset_id, "audit_id": audit_id, "message": "已驳回，资产退回待修改"}


# =============================================================================
#  授权登记
# =============================================================================
def create_license(
    *,
    asset_id: int,
    payload: dict[str, Any],
    actor_id: int,
    app_role: str,
    is_privileged: bool,
) -> dict[str, Any]:
    """
    登记授权。业务前置条件：
      - 资产必须已终审通过（未确权的作品不应流出）
      - 授权方必须是权利人本人（或 admin 监管代登记）
      - 独占授权同一时刻只能有一份（由部分唯一索引 uq_lic_exclusive_active 兜底）
    记录本身是追加式的：不允许 UPDATE/DELETE，撤销授权也是"新增一条 revoked 记录"。
    """
    with open_tx(app_role=app_role) as cur:
        cur.execute(
            "SELECT asset_id, owner_id, right_level, audit_status FROM asset WHERE asset_id = %s FOR UPDATE",
            (asset_id,),
        )
        asset = cur.fetchone()
        if not asset:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"资产 {asset_id} 不存在")
        if not is_privileged and asset["owner_id"] != actor_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "只有权利人本人可为该资产登记授权")
        if asset["audit_status"] != "approved":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"资产尚未终审通过（当前 {asset['audit_status']}），不得对外授权",
            )
        if not payload.get("licensee_user_id") and not payload.get("licensee_name"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "被授权方必须提供 user_id 或 name")

        # 独占授权冲突的友好报错（数据库唯一索引仍会拦，但先给出可读信息）
        if payload.get("license_type") == "exclusive":
            cur.execute(
                """
                SELECT license_id FROM license_record
                WHERE asset_id = %s AND license_type = 'exclusive' AND status = 'active'
                """,
                (asset_id,),
            )
            if cur.fetchone():
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "该资产已存在生效中的独占授权，需先登记撤销（新增 revoked 记录）后才能再次独占授权",
                )

        try:
            cur.execute(
                """
                INSERT INTO license_record (
                    asset_id, licensor_id, licensee_user_id, licensee_name,
                    license_type, scope, territory, start_date, end_date,
                    fee_cny, contract_hash, prev_license_id
                ) VALUES (
                    %(asset)s, %(licensor)s, %(luid)s, %(lname)s,
                    %(ltype)s, %(scope)s, %(territory)s, %(start)s, %(end)s,
                    %(fee)s, %(chash)s, %(prev)s
                ) RETURNING license_id, created_at
                """,
                {
                    "asset": asset_id,
                    "licensor": actor_id,
                    "luid": payload.get("licensee_user_id"),
                    "lname": payload.get("licensee_name"),
                    "ltype": payload.get("license_type", "non_exclusive"),
                    "scope": payload.get("scope"),
                    "territory": payload.get("territory"),
                    "start": payload["start_date"],
                    "end": payload.get("end_date"),
                    "fee": payload.get("fee_cny"),
                    "chash": payload.get("contract_hash"),
                    "prev": payload.get("prev_license_id"),
                },
            )
            lic = cur.fetchone()
        except pg_errors.UniqueViolation:
            raise HTTPException(status.HTTP_409_CONFLICT, "独占授权唯一性约束冲突（并发写入）")

    return {"license_id": lic["license_id"], "asset_id": asset_id, "created_at": lic["created_at"]}


# =============================================================================
#  溯源链路（走视图 v_asset_trace_chain + 应用层链式校验）
# =============================================================================
def trace_asset(asset_id: int, app_role: str, viewer_id: int, is_privileged: bool) -> dict[str, Any]:
    """
    溯源 = 视图取节点 + 应用层做链式完整性校验。

    为什么校验放在应用层而不是视图里？
      视图适合"投影+连接"，而"prev_license_id 是否首尾相接"是**过程性判断**，
      用 SQL 写会变成难以维护的递归 CTE，且每加一条规则就要改视图。
      放在应用层，规则可读、可测试、可独立演进 —— 视图保持"事实"，应用层负责"判断"。
    """
    asset = get_asset(asset_id, app_role=app_role, viewer_id=viewer_id, is_privileged=is_privileged)

    with open_tx(app_role=app_role) as cur:
        cur.execute(
            "SELECT * FROM v_asset_trace_chain WHERE asset_id = %s ORDER BY event_at ASC, seq_no ASC",
            (asset_id,),
        )
        nodes = [dict(r) for r in cur.fetchall()]

    # 视图里的 seq_no 是**按事件类型写死的**（root=1 / audit=2 / license=3 / system_log=4），
    # 它表达的是"节点类别"，不是"第几步"。同一条链上会有多个 system_log 节点
    # （登记、分析、终审各触发一次），照原样输出就会得到 1→4→4→2→4→3 这种乱序，
    # 时间轴看上去像坏了。所以这里按实际发生时间重新编 1..N。
    #   · 同毫秒的节点（触发器与业务写操作几乎同时）保留视图给的 seq_no 作次序，
    #     保证"业务事件在其触发的审计快照之前"
    #   · 原始 seq_no 仍保留在 detail 的父级，便于排查是哪一类节点
    for i, n in enumerate(nodes, 1):
        n["seq_no"] = i

    issues: list[str] = []

    # 校验 1：文件哈希长度与格式
    if len(asset["file_hash"]) != 64:
        issues.append(f"文件哈希格式异常：{asset['file_hash'][:16]}...")

    # 校验 2：授权链首尾相接
    lic_nodes = [n for n in nodes if n["event_type"] == "license"]
    ids = {n["detail"].get("license_id") for n in lic_nodes}
    for n in lic_nodes:
        prev = n["detail"].get("prev_license_id")
        if prev is not None and prev not in ids:
            issues.append(f"授权 #{n['detail'].get('license_id')} 指向的前序授权 #{prev} 不在本资产链路中")

    # 校验 3：必须有审核节点且有明确的终审结论
    if asset["audit_status"] == "approved":
        approved = [n for n in nodes if n["event_type"] == "audit" and n["detail"].get("state") == "approved"]
        if not approved:
            issues.append("资产状态为已确权，但链路中缺失终审通过节点")

    # 校验 4：DB 层审计日志必须存在（证明触发器在工作）
    if not any(n["event_type"] == "system_log" for n in nodes):
        issues.append("未找到数据库自动审计日志，触发器可能未生效")

    return {
        "asset_id": asset_id,
        "title": asset["title"],
        "file_hash": asset["file_hash"],
        "right_level": asset["right_level"],
        "audit_status": asset["audit_status"],
        "is_chain_valid": not issues,
        "chain_issues": issues,
        "nodes": [
            {
                "seq_no": n["seq_no"],
                "event_at": n["event_at"],
                "event_type": n["event_type"],
                "summary": n["summary"],
                "actor_name": n["actor_name"],
                "actor_role": n["actor_role"],
                "detail": n["detail"] or {},
            }
            for n in nodes
        ],
    }


# =============================================================================
#  监管台账
# =============================================================================
def get_ledger(app_role: str) -> dict[str, Any]:
    with open_tx(app_role=app_role) as cur:
        cur.execute("SELECT * FROM v_asset_ledger ORDER BY asset_id DESC")
        rows = [dict(r) for r in cur.fetchall()]

        cur.execute(
            """
            SELECT
                count(*)                                                        AS total,
                count(*) FILTER (WHERE right_level = 'public')                  AS n_public,
                count(*) FILTER (WHERE right_level = 'derived')                 AS n_derived,
                count(*) FILTER (WHERE right_level = 'original')                AS n_original,
                count(*) FILTER (WHERE audit_status = 'pending')                AS n_pending,
                count(*) FILTER (WHERE audit_status = 'approved')               AS n_approved,
                count(*) FILTER (WHERE audit_status = 'draft')                  AS n_draft,
                count(*) FILTER (WHERE audit_status = 'rejected')               AS n_rejected,
                count(*) FILTER (WHERE ai_suggest_level IS NOT NULL
                                   AND ai_suggest_level = right_level)          AS n_agree,
                count(*) FILTER (WHERE ai_suggest_level IS NOT NULL
                                   AND audit_status = 'approved')               AS n_compared
            FROM asset
            """,
        )
        agg = cur.fetchone()

        cur.execute("SELECT COALESCE(sum(fee_cny), 0) AS fee FROM license_record WHERE status = 'active'")
        fee = cur.fetchone()["fee"]

        cur.execute("SELECT count(*) AS c FROM system_audit_log")
        log_cnt = cur.fetchone()["c"]

    compared = agg["n_compared"] or 0
    return {
        "summary": {
            "total_assets": agg["total"],
            "by_level": {
                "public": agg["n_public"],
                "derived": agg["n_derived"],
                "original": agg["n_original"],
            },
            "by_status": {
                "draft": agg["n_draft"],
                "pending": agg["n_pending"],
                "approved": agg["n_approved"],
                "rejected": agg["n_rejected"],
            },
            "ai_agree_rate": round(agg["n_agree"] / compared, 4) if compared else None,
            "total_license_fee_cny": fee,
            "pending_count": agg["n_pending"],
            "db_audit_log_count": log_cnt,
        },
        "rows": rows,
    }


# =============================================================================
#  母题库
# =============================================================================
def list_motifs(app_role: str) -> list[dict]:
    with open_tx(app_role=app_role) as cur:
        cur.execute(
            """
            SELECT motif_id, motif_code, name, region, description, reference_img,
                   vector_model, (base_vector IS NOT NULL) AS has_vector, created_at
            FROM public_motif ORDER BY motif_id
            """
        )
        return [dict(r) for r in cur.fetchall()]


def backfill_motif_vector(motif_id: int, reference_img_abs: str) -> dict:
    """用真实模型为母题重新提取特征并回填（覆盖占位向量）。仅 admin 调用。"""
    with open(reference_img_abs, "rb") as fh:
        feat = extract_from_bytes(fh.read())
    with open_tx(app_role="admin") as cur:
        cur.execute(
            f"""
            UPDATE public_motif
               SET base_vector = {cast_vec('%(v)s')},
                   vector_model = %(m)s
             WHERE motif_id = %(id)s
            RETURNING motif_id, motif_code, name
            """,
            {"v": vector_literal(feat.vector), "m": f"{feat.model_name}@{feat.model_version}", "id": motif_id},
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"母题 {motif_id} 不存在")
    return {**dict(row), "model_name": feat.model_name, "model_version": feat.model_version}


# =============================================================================
#  工具函数
# =============================================================================
def _abs_upload_path(object_key: str) -> str:
    """对象键 → 本地绝对路径。生产环境这里应替换为对象存储客户端。"""
    from app.config import UPLOAD_DIR
    return str((UPLOAD_DIR / object_key).resolve())
