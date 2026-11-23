"""HTTP 路由层：只做参数编解码 + 鉴权 + 调用服务层，不写业务 SQL。"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from psycopg2 import errors as pg_errors

from app import demo, services
from app.ai.features import current_backend_info
from app.auth import CurrentUser, create_token, get_current_user, require_roles
from app.config import UPLOAD_DIR, settings
from app.db import healthy, open_tx
from app.schemas import (
    AnalyzeResponse,
    ApproveRequest,
    ApproveResponse,
    AssetOut,
    HealthResponse,
    LedgerResponse,
    LicenseOut,
    LicenseRequest,
    LoginRequest,
    LoginResponse,
    PendingItem,
    RejectRequest,
    TraceResponse,
    UploadResponse,
    UserInfo,
)

log = logging.getLogger("ich.api")

router = APIRouter(prefix="/api")

# 允许上传的扩展名 → MIME 兜底映射（浏览器有时给 application/octet-stream）
EXT_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff",
}


# =============================================================================
#  健康检查 & 元信息
# =============================================================================
@router.get("/health", response_model=HealthResponse, tags=["系统"])
def health() -> HealthResponse:
    if demo.is_enabled():
        # 演示模式：数据库状态如实报告为"未连接"，但把内存表行数也报出来
        return HealthResponse(
            ok=True,
            database="演示模式（内存存储，非 PostgreSQL）",
            ai_backend=current_backend_info(),
            tables=demo.health_tables(),
        )
    ok, info = healthy()
    tables: dict[str, int] = {}
    if ok:
        with open_tx() as cur:
            for tbl in ("app_user", "public_motif", "asset", "feature_vector",
                        "audit_record", "license_record", "asset_genealogy", "system_audit_log"):
                cur.execute(f"SELECT count(*) AS c FROM {tbl}")
                tables[tbl] = cur.fetchone()["c"]
    return HealthResponse(ok=ok, database=info, ai_backend=current_backend_info(), tables=tables)


@router.get("/meta", tags=["系统"])
def meta() -> dict:
    """给前端展示的常量（级别标签、阈值、可用角色），避免前后端各写一份硬编码。"""
    return {
        "levels": [
            {"value": "public", "label": "公有母题", "hint": "公有领域传统纹样，不可个人独占"},
            {"value": "derived", "label": "改编衍生", "hint": "对公有母题有实质改编，享演绎著作权"},
            {"value": "original", "label": "原创作品", "hint": "与现存母题无实质重合"},
        ],
        "statuses": ["draft", "pending", "approved", "rejected"],
        "thresholds": {
            "public": settings.SIM_PUBLIC_THRESHOLD,
            "derived": settings.SIM_DERIVED_THRESHOLD,
        },
        "ai_backend": current_backend_info(),
        "max_upload_mb": settings.MAX_UPLOAD_MB,
        # 前端据此显示"演示数据"红色横幅，避免把模拟结果误当成真实台账
        "demo_mode": demo.is_enabled(),
        "demo_note": (
            "当前为演示模式：数据存于进程内存，退出即丢失。"
            "列级 GRANT、触发器、CHECK/UNIQUE/外键、HNSW 检索、MVCC 并发控制均不生效。"
            "设 ICH_DEMO_MODE=0 并启动 PostgreSQL 即可切换到真实数据库。"
        ) if demo.is_enabled() else None,
    }


# =============================================================================
#  认证
# =============================================================================
@router.post("/auth/login", response_model=LoginResponse, tags=["认证"])
def login(body: LoginRequest) -> LoginResponse:
    """
    口令在数据库里存 bcrypt 散列（pgcrypto 的 crypt + bf salt），
    校验也交给数据库做 —— 明文口令永远不进入应用内存中的日志。
    """
    if demo.is_enabled():
        row = demo.login(body.username, body.password)
    else:
        with open_tx() as cur:
            cur.execute(
                """
                SELECT user_id, username, display_name, role, org_name, is_active
                FROM app_user
                WHERE username = %s AND password_hash = crypt(%s, password_hash)
                """,
                (body.username, body.password),
            )
            row = cur.fetchone()

    if not row or not row["is_active"]:
        # 不区分"用户不存在"与"口令错误"，避免用户名枚举
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或口令错误")

    token = create_token(row["user_id"], row["username"], row["role"])
    return LoginResponse(
        access_token=token,
        expires_in=settings.JWT_EXPIRE_MINUTES * 60,
        user=UserInfo(**{k: row[k] for k in ("user_id", "username", "display_name", "role", "org_name")}),
    )


@router.get("/auth/me", response_model=UserInfo, tags=["认证"])
def me(user: CurrentUser = Depends(get_current_user)) -> UserInfo:
    return UserInfo(
        user_id=user.user_id, username=user.username,
        display_name=user.display_name, role=user.role, org_name=user.org_name,
    )


# =============================================================================
#  资产上传 / 查询
# =============================================================================
@router.post("/asset/upload", response_model=UploadResponse, tags=["资产"],
             status_code=status.HTTP_201_CREATED)
async def upload_asset(
    request: Request,
    file: UploadFile = File(..., description="非遗纹样图像"),
    title: str = Form(..., min_length=1, max_length=200),
    description: str | None = Form(default=None, max_length=2000),
    auto_analyze: bool = Form(default=False, description="上传后立即触发 AI 分析"),
    user: CurrentUser = Depends(require_roles("heritor", "admin")),
) -> UploadResponse:
    """
    上传流程：
      1. 校验 MIME 与大小（防超大文件打满磁盘 / 防伪装文件）
      2. 用 UUID 重命名落盘（**不使用用户原始文件名**，避免路径穿越 ../..）
      3. 写 asset（draft）
      4. 可选：立即调 AI 分析
    """
    suffix = Path(file.filename or "").suffix.lower()
    mime = file.content_type or EXT_MIME.get(suffix, "application/octet-stream")
    if mime not in settings.ALLOWED_MIME:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"不支持的图片类型 {mime}，允许：{sorted(settings.ALLOWED_MIME)}",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "上传文件为空")
    if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"文件超过 {settings.MAX_UPLOAD_MB}MB 上限",
        )

    # 用 Pillow 实际解码一次，确保是合法图像（防止改扩展名的恶意文件）
    try:
        from PIL import Image
        import io as _io
        with Image.open(_io.BytesIO(data)) as probe:
            probe.verify()
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件不是可解析的图片")

    object_key = f"{uuid.uuid4().hex}{suffix or '.jpg'}"
    target = UPLOAD_DIR / object_key
    target.write_bytes(data)

    _create = demo.create_asset if demo.is_enabled() else services.create_asset
    _get = demo.get_asset if demo.is_enabled() else services.get_asset
    _analyze = demo.analyze_asset if demo.is_enabled() else services.analyze_asset

    try:
        result = _create(
            owner_id=user.user_id,
            title=title,
            description=description,
            file_bytes=data,
            mime_type=mime,
            object_key=object_key,
            app_role=user.role,
        )
    except Exception:
        # 入库失败要清理已落盘的文件，避免产生孤儿文件
        target.unlink(missing_ok=True)
        raise

    if result["duplicate_of"]:
        target.unlink(missing_ok=True)     # 重复文件不必占磁盘
        asset = _get(result["duplicate_of"], app_role=user.role,
                     viewer_id=user.user_id, is_privileged=user.is_admin)
        return UploadResponse(asset=AssetOut(**_asset_out(asset)), duplicate_of=result["duplicate_of"])

    if auto_analyze:
        _analyze(
            asset_id=result["asset_id"], app_role=user.role, world_app_role=user.role,
            viewer_id=user.user_id, is_privileged=user.is_admin, include_plan=False,
        )

    asset = _get(result["asset_id"], app_role=user.role,
                 viewer_id=user.user_id, is_privileged=user.is_admin)
    return UploadResponse(asset=AssetOut(**_asset_out(asset)))


@router.get("/asset/mine", tags=["资产"])
def my_assets(user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    if demo.is_enabled():
        return demo.list_my_assets(user.user_id, app_role=user.role)
    return services.list_my_assets(user.user_id, app_role=user.role)


@router.get("/asset/{asset_id}", response_model=AssetOut, tags=["资产"])
def get_asset(asset_id: int, user: CurrentUser = Depends(get_current_user)) -> AssetOut:
    fn = demo.get_asset if demo.is_enabled() else services.get_asset
    row = fn(
        asset_id, app_role=user.role, viewer_id=user.user_id, is_privileged=user.is_expert or user.is_admin
    )
    return AssetOut(**_asset_out(row))


@router.get("/asset/{asset_id}/image", tags=["资产"])
def get_asset_image(asset_id: int, user: CurrentUser = Depends(get_current_user)):
    """返回原图（权限与资产查询一致）。生产环境建议改为对象存储的预签名 URL。"""
    from fastapi.responses import FileResponse

    fn = demo.get_asset if demo.is_enabled() else services.get_asset
    row = fn(
        asset_id, app_role=user.role, viewer_id=user.user_id, is_privileged=user.is_expert or user.is_admin
    )
    path = (UPLOAD_DIR / row["file_path"]).resolve()
    # 路径穿越防御：确保解析后的路径仍在 UPLOAD_DIR 之内
    if not str(path).startswith(str(UPLOAD_DIR.resolve())) or not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "原图不存在")
    return FileResponse(path, media_type=row["mime_type"])


# =============================================================================
#  AI 分析
# =============================================================================
@router.post("/asset/{asset_id}/analyze", response_model=AnalyzeResponse, tags=["AI"])
def analyze(asset_id: int, include_plan: bool = Query(True), user: CurrentUser = Depends(get_current_user)) -> AnalyzeResponse:
    """
    触发 AI 特征提取与相似检索，产出**建议**。
    权限：资产所有者本人 / 专家 / 管理员可触发。
    """
    row = (demo.get_asset if demo.is_enabled() else services.get_asset)(
        asset_id, app_role=user.role, viewer_id=user.user_id, is_privileged=user.is_expert or user.is_admin
    )
    if user.role == "heritor" and row["owner_id"] != user.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只能分析自己的作品")

    # heritor 角色在数据库层只有 UPDATE 部分列的权限；analyze 需要更新 audit_status
    # 我们用它自己的角色执行——这恰好验证了 GRANT 的正确性。
    fn = demo.analyze_asset if demo.is_enabled() else services.analyze_asset
    return AnalyzeResponse(**fn(
        asset_id=asset_id,
        app_role=user.role,
        world_app_role=user.role,
        viewer_id=user.user_id,
        is_privileged=user.is_expert or user.is_admin,
        include_plan=include_plan,
    ))


# =============================================================================
#  专家工作台
# =============================================================================
@router.get("/audit/pending", response_model=list[PendingItem], tags=["审核"])
def pending(limit: int = Query(100, ge=1, le=500),
            user: CurrentUser = Depends(require_roles("expert", "admin"))) -> list[PendingItem]:
    """待审列表（数据源：视图 v_asset_pending_audit）。"""
    raw = demo.list_pending(limit=limit) if demo.is_enabled() else services.list_pending(limit=limit)
    out = []
    for r in raw:
        d = dict(r)
        sim = d.get("similarity_json")
        if isinstance(sim, str):
            import json
            d["similarity_json"] = json.loads(sim)
        out.append(PendingItem(**d))
    return out


@router.post("/audit/{asset_id}/approve", response_model=ApproveResponse, tags=["审核"])
def approve(asset_id: int, body: ApproveRequest,
            user: CurrentUser = Depends(require_roles("expert", "admin"))) -> ApproveResponse:
    """
    专家终审（单事务：改 asset + 写 audit_record + 触发器写审计日志）。
    """
    expert_id = user.user_id
    expert_name = user.display_name
    if user.is_admin and body.expert_id:
        # admin 代指定专家：校验目标用户确实是专家
        if demo.is_enabled():
            tgt = next((u for u in demo.users("expert") if u["user_id"] == body.expert_id), None)
        else:
            with open_tx() as cur:
                cur.execute("SELECT user_id, display_name, role FROM app_user WHERE user_id = %s",
                            (body.expert_id,))
                tgt = cur.fetchone()
        if not tgt or tgt["role"] != "expert":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "指定的 expert_id 不是专家")
        expert_id, expert_name = tgt["user_id"], tgt["display_name"]

    fn = demo.approve_asset if demo.is_enabled() else services.approve_asset
    return ApproveResponse(**fn(
        asset_id=asset_id,
        final_level=body.final_level,
        opinion=body.opinion,
        expert_id=expert_id,
        expert_name=expert_name,
    ))


@router.post("/audit/{asset_id}/reject", tags=["审核"])
def reject(asset_id: int, body: RejectRequest,
           user: CurrentUser = Depends(require_roles("expert", "admin"))) -> dict:
    fn = demo.reject_asset if demo.is_enabled() else services.reject_asset
    return fn(asset_id=asset_id, opinion=body.opinion, expert_id=user.user_id)


# =============================================================================
#  授权登记
# =============================================================================
@router.post("/asset/{asset_id}/license", response_model=LicenseOut, tags=["授权"],
             status_code=status.HTTP_201_CREATED)
def create_license(asset_id: int, body: LicenseRequest,
                   user: CurrentUser = Depends(require_roles("heritor", "admin"))) -> LicenseOut:
    """登记授权流转记录（追加式，不可修改/删除）。"""
    _get = demo.get_asset if demo.is_enabled() else services.get_asset

    if demo.is_enabled():
        res = demo.create_license(
            asset_id=asset_id, payload=body.model_dump(), actor_id=user.user_id,
            app_role=user.role, is_privileged=user.is_admin,
        )
        row = next(l for l in demo.list_licenses(asset_id) if l["license_id"] == res["license_id"])
        return LicenseOut(**{k: row[k] for k in LicenseOut.model_fields if k in row})

    res = services.create_license(
        asset_id=asset_id,
        payload=body.model_dump(),
        actor_id=user.user_id,
        app_role=user.role,
        is_privileged=user.is_admin,
    )
    with open_tx(app_role=user.role) as cur:
        cur.execute(
            """
            SELECT l.*, lo.display_name AS licensor_name
            FROM license_record l JOIN app_user lo ON lo.user_id = l.licensor_id
            WHERE l.license_id = %s
            """,
            (res["license_id"],),
        )
        row = cur.fetchone()
    return LicenseOut(**{k: row[k] for k in LicenseOut.model_fields if k in row})


@router.get("/asset/{asset_id}/licenses", response_model=list[LicenseOut], tags=["授权"])
def list_licenses(asset_id: int, user: CurrentUser = Depends(get_current_user)) -> list[LicenseOut]:
    if demo.is_enabled():
        demo.get_asset(asset_id, app_role=user.role, viewer_id=user.user_id,
                       is_privileged=user.is_expert or user.is_admin)
        rows = demo.list_licenses(asset_id)
        return [LicenseOut(**{k: r[k] for k in LicenseOut.model_fields if k in r}) for r in rows]

    services.get_asset(asset_id, app_role=user.role, viewer_id=user.user_id,
                       is_privileged=user.is_expert or user.is_admin)
    with open_tx(app_role=user.role) as cur:
        cur.execute(
            """
            SELECT l.*, lo.display_name AS licensor_name
            FROM license_record l JOIN app_user lo ON lo.user_id = l.licensor_id
            WHERE l.asset_id = %s ORDER BY l.created_at
            """,
            (asset_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return [LicenseOut(**{k: r[k] for k in LicenseOut.model_fields if k in r}) for r in rows]


# =============================================================================
#  溯源 & 台账
# =============================================================================
@router.get("/asset/{asset_id}/trace", response_model=TraceResponse, tags=["溯源"])
def trace(asset_id: int, user: CurrentUser = Depends(get_current_user)) -> TraceResponse:
    """溯源链路（视图 v_asset_trace_chain + 应用层链式校验）。"""
    fn = demo.trace_asset if demo.is_enabled() else services.trace_asset
    return TraceResponse(**fn(
        asset_id, app_role=user.role, viewer_id=user.user_id,
        is_privileged=user.is_expert or user.is_admin,
    ))


@router.get("/ledger", response_model=LedgerResponse, tags=["监管"])
def ledger(user: CurrentUser = Depends(require_roles("expert", "admin"))) -> LedgerResponse:
    """监管台账（视图 v_asset_ledger）。"""
    fn = demo.get_ledger if demo.is_enabled() else services.get_ledger
    return LedgerResponse(**fn(app_role=user.role))


@router.get("/motifs", tags=["母题"])
def motifs(user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    if demo.is_enabled():
        return demo.list_motifs(app_role=user.role)
    return services.list_motifs(app_role=user.role)


@router.get("/users", tags=["系统"])
def users(role: str | None = Query(None), user: CurrentUser = Depends(require_roles("expert", "admin"))) -> list[dict]:
    """
    列出用户（供授权界面选择被授权方）。不含口令散列。
    仅专家/管理员可查 —— 传承人不需要看到全平台用户名录（最小必要原则）。
    """
    if demo.is_enabled():
        return demo.users(role)

    sql = "SELECT user_id, username, display_name, role, org_name FROM app_user"
    params: tuple = ()
    if role:
        sql += " WHERE role = %s"
        params = (role,)
    sql += " ORDER BY user_id"
    with open_tx(app_role=user.role) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


# =============================================================================
#  内部工具
# =============================================================================
def _asset_out(row: dict) -> dict:
    """DB 行 → AssetOut 需要的字段（过滤多余列，显式列清单防止内部字段外泄）。"""
    return {
        "asset_id": row["asset_id"],
        "title": row["title"],
        "owner_id": row["owner_id"],
        "owner_name": row.get("owner_name"),
        "motif_id": row.get("motif_id"),
        "file_hash": row["file_hash"],
        "file_size": row["file_size"],
        "right_level": row["right_level"],
        "audit_status": row["audit_status"],
        "ai_suggest_level": row.get("ai_suggest_level"),
        "ai_confidence": float(row["ai_confidence"]) if row.get("ai_confidence") is not None else None,
        "ai_model_version": row.get("ai_model_version"),
        "version_no": row.get("version_no", 0),
        "description": row.get("description"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
