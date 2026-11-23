"""API 数据模型（请求/响应 Schema）。用 Pydantic 做入参校验，把非法数据挡在业务层之外。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

RightLevel = Literal["public", "derived", "original"]
AuditStatus = Literal["draft", "pending", "approved", "rejected"]


# ---------------------------------------------------------------- 认证
class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserInfo(BaseModel):
    user_id: int
    username: str
    display_name: str
    role: str
    org_name: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserInfo


# ---------------------------------------------------------------- 资产
class AssetOut(BaseModel):
    asset_id: int
    title: str
    owner_id: int
    owner_name: str | None = None
    motif_id: int | None = None
    file_hash: str
    file_size: int
    right_level: RightLevel
    audit_status: AuditStatus
    ai_suggest_level: RightLevel | None = None
    ai_confidence: float | None = None
    ai_model_version: str | None = None
    version_no: int = 0
    description: str | None = None
    created_at: datetime
    updated_at: datetime


class UploadResponse(BaseModel):
    asset: AssetOut
    duplicate_of: int | None = Field(
        default=None, description="若文件哈希已存在，返回已有资产 ID，本次为幂等复用"
    )


class SimilarityItem(BaseModel):
    kind: str
    id: int
    code: str
    name: str
    similarity: float
    distance: float
    extra: dict[str, Any] = {}


class AnalyzeResponse(BaseModel):
    asset_id: int
    audit_id: int
    suggested_level: RightLevel
    confidence: float
    reason: str
    rule: str
    flagged_as: str | None = None
    margin: float = 0
    top_k: list[SimilarityItem]
    similar_assets: list[SimilarityItem]
    model: dict[str, Any]
    plan: str | None = Field(default=None, description="HNSW 执行计划，证明走了向量索引")


# ---------------------------------------------------------------- 审核
class PendingItem(BaseModel):
    asset_id: int
    title: str
    owner_id: int
    owner_name: str
    owner_org: str | None = None
    current_right_level: RightLevel
    ai_suggest_level: RightLevel | None = None
    ai_confidence: float | None = None
    ai_model_version: str | None = None
    uploaded_at: datetime
    audit_id: int | None = None
    submitted_at: datetime | None = None
    top1_motif_name: str | None = None
    top1_similarity: float | None = None
    similarity_json: list[dict[str, Any]] | None = None
    license_count: int = 0


class ApproveRequest(BaseModel):
    final_level: RightLevel
    opinion: str = Field(default="", max_length=2000)
    expert_id: int | None = Field(
        default=None,
        description="仅 admin 可代指定专家；专家本人调用时忽略此字段",
    )

    @field_validator("opinion")
    @classmethod
    def strip_opinion(cls, v: str) -> str:
        return (v or "").strip()


class ApproveResponse(BaseModel):
    asset_id: int
    audit_id: int
    right_level: RightLevel
    audit_status: AuditStatus
    is_override: bool
    expert_name: str
    db_audit_log_ids: list[int] = Field(
        default_factory=list, description="触发器写入的审计日志主键，可用于验证触发器确实生效"
    )
    message: str


class RejectRequest(BaseModel):
    opinion: str = Field(min_length=1, max_length=2000)


# ---------------------------------------------------------------- 授权
class LicenseRequest(BaseModel):
    licensee_user_id: int | None = None
    licensee_name: str | None = Field(default=None, max_length=200)
    license_type: Literal["exclusive", "non_exclusive", "display", "derivative"] = "non_exclusive"
    scope: str | None = Field(default=None, max_length=1000)
    territory: str | None = Field(default=None, max_length=128)
    start_date: date
    end_date: date | None = None
    fee_cny: Decimal | None = Field(default=None, ge=0)
    contract_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    prev_license_id: int | None = None

    @field_validator("end_date")
    @classmethod
    def check_period(cls, v: date | None, info):
        start = info.data.get("start_date")
        if v is not None and start is not None and v < start:
            raise ValueError("end_date 不得早于 start_date")
        return v

    @field_validator("licensee_name")
    @classmethod
    def need_licensee(cls, v, info):
        # 两者都为空的情况在接口层再判一次（因为字段顺序不确定）
        return v.strip() if v else v


class LicenseOut(BaseModel):
    license_id: int
    asset_id: int
    licensor_id: int
    licensor_name: str | None = None
    licensee_user_id: int | None = None
    licensee_name: str | None = None
    license_type: str
    scope: str | None = None
    territory: str | None = None
    start_date: date
    end_date: date | None = None
    fee_cny: Decimal | None = None
    status: str
    contract_hash: str | None = None
    prev_license_id: int | None = None
    created_at: datetime


# ---------------------------------------------------------------- 溯源 / 台账
class TraceNode(BaseModel):
    seq_no: int
    event_at: datetime
    event_type: str
    summary: str
    actor_name: str | None = None
    actor_role: str | None = None
    detail: dict[str, Any] = {}


class TraceResponse(BaseModel):
    asset_id: int
    title: str
    file_hash: str
    right_level: RightLevel
    audit_status: AuditStatus
    is_chain_valid: bool = Field(
        description="链式校验：授权记录的 prev_license_id 是否首尾相接、文件哈希是否与登记值一致"
    )
    chain_issues: list[str] = Field(default_factory=list)
    nodes: list[TraceNode]


class LedgerRow(BaseModel):
    asset_id: int
    title: str
    owner_name: str
    owner_org: str | None = None
    right_level: RightLevel
    audit_status: AuditStatus
    ai_suggest_level: RightLevel | None = None
    ai_confidence: float | None = None
    # Top-1 相似度：作品与"血缘母题"的相似度，母题关联页展示用。
    # 注意：Pydantic 模型默认**丢弃未声明的字段**，视图新增列后必须在这里同步加，
    # 否则接口返回里根本没有这个键，前端只能拿到 undefined 而显示成"—"。
    top1_similarity: float | None = None
    ai_expert_agree: bool | None = None
    motif_code: str | None = None
    motif_name: str | None = None
    file_hash_short: str | None = None
    version_no: int
    created_at: datetime
    updated_at: datetime
    license_cnt: int = 0
    active_license_cnt: int | None = None
    total_fee_cny: Decimal | None = None
    audit_times: int = 0
    db_log_cnt: int = 0
    last_log_at: datetime | None = None
    derivative_cnt: int = 0


class LedgerSummary(BaseModel):
    total_assets: int
    by_level: dict[str, int]
    by_status: dict[str, int]
    ai_agree_rate: float | None = None
    total_license_fee_cny: Decimal = Decimal("0")
    pending_count: int = 0
    db_audit_log_count: int = 0


class LedgerResponse(BaseModel):
    summary: LedgerSummary
    rows: list[LedgerRow]


class HealthResponse(BaseModel):
    ok: bool
    database: str
    ai_backend: dict[str, Any]
    tables: dict[str, int] = {}
