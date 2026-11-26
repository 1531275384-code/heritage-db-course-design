"""
本文件由 scripts/gen_rules.py 从 SQL 自动生成，**请勿手改**。

生成：python -m scripts.gen_rules
校验：python -m scripts.gen_rules --check      （SQL 改了没重新生成 → 非零退出）
对账：python -m scripts.gen_rules --verify-db  （与运行中的库逐条比对）

来源指纹（前 16 位）
  sql/01_init.sql                e259c8d80a515091
  sql/02_index_trigger_view.sql  ce5dfb97b28b2637

规则计数：CHECK 24 / UNIQUE 4 / 外键 12 / 触发器 10

为什么这里只有数据没有逻辑：逻辑在 sim/ruleast.py（手写）与 sim/verdict.py。
生成文件被覆盖时不应丢失任何判定语义，否则说明有人把逻辑写进了生成器。
"""

from __future__ import annotations

from sim.ruleast import (
    And,
    Btrim,
    CheckRule,
    Col,
    Cmp,
    EnumRule,
    ForeignKeyRule,
    In,
    IsNull,
    Length,
    Lit,
    MaxLengthRule,
    Not,
    NotNullRule,
    Or,
    ParentGate,
    Regex,
    TriggerRule,
    UniqueRule,
    VectorDims,
)

SOURCE_FILES = ('sql/01_init.sql', 'sql/02_index_trigger_view.sql')
SOURCE_SHA256 = {'sql/01_init.sql': 'e259c8d80a515091', 'sql/02_index_trigger_view.sql': 'ce5dfb97b28b2637'}

# ==========================================================================
#  枚举取值域（由 CREATE TYPE ... AS ENUM 决定）
# ==========================================================================
ENUMS: dict[str, tuple[str, ...]] = {
    "audit_status_enum": ('draft', 'pending', 'approved', 'rejected',),
    "right_level_enum": ('public', 'derived', 'original',),
    "user_role_enum": ('heritor', 'expert', 'admin',),
}

# ==========================================================================
#  列定义（顺序与 DDL 一致，供样本构造使用）
# ==========================================================================
COLUMNS: dict[str, tuple[str, ...]] = {
    "app_user": ("user_id", "username", "display_name", "role", "password_hash", "org_name", "is_active", "created_at",),
    "public_motif": ("motif_id", "motif_code", "name", "region", "description", "reference_img", "base_vector", "vector_model", "created_at",),
    "asset": ("asset_id", "title", "owner_id", "motif_id", "file_path", "file_hash", "file_size", "mime_type", "right_level", "audit_status", "ai_suggest_level", "ai_confidence", "ai_model_version", "version_no", "description", "created_at", "updated_at",),
    "feature_vector": ("fv_id", "asset_id", "model_name", "model_version", "embed_dim", "embedding", "created_at",),
    "audit_record": ("audit_id", "asset_id", "expert_id", "state", "ai_suggest_level", "ai_confidence", "expert_level", "similarity_json", "opinion", "is_override", "created_at", "decided_at",),
    "license_record": ("license_id", "asset_id", "licensor_id", "licensee_user_id", "licensee_name", "license_type", "scope", "territory", "start_date", "end_date", "fee_cny", "contract_hash", "status", "prev_license_id", "created_at",),
    "asset_genealogy": ("genealogy_id", "parent_asset_id", "child_asset_id", "relation_type", "similarity", "note", "created_by", "created_at",),
    "system_audit_log": ("log_id", "table_name", "record_id", "operation", "actor", "old_snapshot", "new_snapshot", "changed_fields", "logged_at",),
}

COLUMN_TYPES: dict[str, dict[str, str]] = {
    "app_user": {
        "user_id": "BIGSERIAL",
        "username": "VARCHAR(64)",
        "display_name": "VARCHAR(128)",
        "role": "user_role_enum",
        "password_hash": "TEXT",
        "org_name": "VARCHAR(200)",
        "is_active": "BOOLEAN",
        "created_at": "TIMESTAMPTZ",
    },
    "public_motif": {
        "motif_id": "BIGSERIAL",
        "motif_code": "VARCHAR(32)",
        "name": "VARCHAR(128)",
        "region": "VARCHAR(128)",
        "description": "TEXT",
        "reference_img": "VARCHAR(512)",
        "base_vector": ":ICH_VEC_TYPE",
        "vector_model": "VARCHAR(64)",
        "created_at": "TIMESTAMPTZ",
    },
    "asset": {
        "asset_id": "BIGSERIAL",
        "title": "VARCHAR(200)",
        "owner_id": "BIGINT",
        "motif_id": "BIGINT",
        "file_path": "VARCHAR(512)",
        "file_hash": "CHAR(64)",
        "file_size": "BIGINT",
        "mime_type": "VARCHAR(64)",
        "right_level": "right_level_enum",
        "audit_status": "audit_status_enum",
        "ai_suggest_level": "right_level_enum",
        "ai_confidence": "NUMERIC(5,4)",
        "ai_model_version": "VARCHAR(64)",
        "version_no": "INTEGER",
        "description": "TEXT",
        "created_at": "TIMESTAMPTZ",
        "updated_at": "TIMESTAMPTZ",
    },
    "feature_vector": {
        "fv_id": "BIGSERIAL",
        "asset_id": "BIGINT",
        "model_name": "VARCHAR(64)",
        "model_version": "VARCHAR(32)",
        "embed_dim": "SMALLINT",
        "embedding": ":ICH_VEC_TYPE",
        "created_at": "TIMESTAMPTZ",
    },
    "audit_record": {
        "audit_id": "BIGSERIAL",
        "asset_id": "BIGINT",
        "expert_id": "BIGINT",
        "state": "VARCHAR(24)",
        "ai_suggest_level": "right_level_enum",
        "ai_confidence": "NUMERIC(5,4)",
        "expert_level": "right_level_enum",
        "similarity_json": "JSONB",
        "opinion": "TEXT",
        "is_override": "BOOLEAN",
        "created_at": "TIMESTAMPTZ",
        "decided_at": "TIMESTAMPTZ",
    },
    "license_record": {
        "license_id": "BIGSERIAL",
        "asset_id": "BIGINT",
        "licensor_id": "BIGINT",
        "licensee_user_id": "BIGINT",
        "licensee_name": "VARCHAR(200)",
        "license_type": "VARCHAR(32)",
        "scope": "TEXT",
        "territory": "VARCHAR(128)",
        "start_date": "DATE",
        "end_date": "DATE",
        "fee_cny": "NUMERIC(12,2)",
        "contract_hash": "CHAR(64)",
        "status": "VARCHAR(16)",
        "prev_license_id": "BIGINT",
        "created_at": "TIMESTAMPTZ",
    },
    "asset_genealogy": {
        "genealogy_id": "BIGSERIAL",
        "parent_asset_id": "BIGINT",
        "child_asset_id": "BIGINT",
        "relation_type": "VARCHAR(32)",
        "similarity": "NUMERIC(5,4)",
        "note": "TEXT",
        "created_by": "BIGINT",
        "created_at": "TIMESTAMPTZ",
    },
    "system_audit_log": {
        "log_id": "BIGSERIAL",
        "table_name": "VARCHAR(64)",
        "record_id": "BIGINT",
        "operation": "VARCHAR(16)",
        "actor": "VARCHAR(64)",
        "old_snapshot": "JSONB",
        "new_snapshot": "JSONB",
        "changed_fields": "TEXT[]",
        "logged_at": "TIMESTAMPTZ",
    },
}

# ==========================================================================
#  NOT NULL（含主键隐含的 NOT NULL）
# ==========================================================================
NOT_NULL: tuple[NotNullRule, ...] = (
    NotNullRule("app_user", "user_id"),
    NotNullRule("app_user", "username"),
    NotNullRule("app_user", "display_name"),
    NotNullRule("app_user", "role"),
    NotNullRule("app_user", "password_hash"),
    NotNullRule("app_user", "is_active"),
    NotNullRule("app_user", "created_at"),
    NotNullRule("public_motif", "motif_id"),
    NotNullRule("public_motif", "motif_code"),
    NotNullRule("public_motif", "name"),
    NotNullRule("public_motif", "created_at"),
    NotNullRule("asset", "asset_id"),
    NotNullRule("asset", "title"),
    NotNullRule("asset", "owner_id"),
    NotNullRule("asset", "file_path"),
    NotNullRule("asset", "file_hash"),
    NotNullRule("asset", "file_size"),
    NotNullRule("asset", "mime_type"),
    NotNullRule("asset", "right_level"),
    NotNullRule("asset", "audit_status"),
    NotNullRule("asset", "version_no"),
    NotNullRule("asset", "created_at"),
    NotNullRule("asset", "updated_at"),
    NotNullRule("feature_vector", "fv_id"),
    NotNullRule("feature_vector", "asset_id"),
    NotNullRule("feature_vector", "model_name"),
    NotNullRule("feature_vector", "model_version"),
    NotNullRule("feature_vector", "embed_dim"),
    NotNullRule("feature_vector", "embedding"),
    NotNullRule("feature_vector", "created_at"),
    NotNullRule("audit_record", "audit_id"),
    NotNullRule("audit_record", "asset_id"),
    NotNullRule("audit_record", "state"),
    NotNullRule("audit_record", "is_override"),
    NotNullRule("audit_record", "created_at"),
    NotNullRule("license_record", "license_id"),
    NotNullRule("license_record", "asset_id"),
    NotNullRule("license_record", "licensor_id"),
    NotNullRule("license_record", "license_type"),
    NotNullRule("license_record", "start_date"),
    NotNullRule("license_record", "status"),
    NotNullRule("license_record", "created_at"),
    NotNullRule("asset_genealogy", "genealogy_id"),
    NotNullRule("asset_genealogy", "parent_asset_id"),
    NotNullRule("asset_genealogy", "child_asset_id"),
    NotNullRule("asset_genealogy", "relation_type"),
    NotNullRule("asset_genealogy", "created_at"),
    NotNullRule("system_audit_log", "log_id"),
    NotNullRule("system_audit_log", "table_name"),
    NotNullRule("system_audit_log", "record_id"),
    NotNullRule("system_audit_log", "operation"),
    NotNullRule("system_audit_log", "actor"),
    NotNullRule("system_audit_log", "logged_at"),
)

# ==========================================================================
#  长度上限（写在类型里，不是 CHECK —— 最容易在盘点时漏掉的一类）
# ==========================================================================
MAX_LENGTH: tuple[MaxLengthRule, ...] = (
    MaxLengthRule("app_user", "username", 64),
    MaxLengthRule("app_user", "display_name", 128),
    MaxLengthRule("app_user", "org_name", 200),
    MaxLengthRule("public_motif", "motif_code", 32),
    MaxLengthRule("public_motif", "name", 128),
    MaxLengthRule("public_motif", "region", 128),
    MaxLengthRule("public_motif", "reference_img", 512),
    MaxLengthRule("public_motif", "vector_model", 64),
    MaxLengthRule("asset", "title", 200),
    MaxLengthRule("asset", "file_path", 512),
    MaxLengthRule("asset", "file_hash", 64, fixed=True),
    MaxLengthRule("asset", "mime_type", 64),
    MaxLengthRule("asset", "ai_model_version", 64),
    MaxLengthRule("feature_vector", "model_name", 64),
    MaxLengthRule("feature_vector", "model_version", 32),
    MaxLengthRule("audit_record", "state", 24),
    MaxLengthRule("license_record", "licensee_name", 200),
    MaxLengthRule("license_record", "license_type", 32),
    MaxLengthRule("license_record", "territory", 128),
    MaxLengthRule("license_record", "contract_hash", 64, fixed=True),
    MaxLengthRule("license_record", "status", 16),
    MaxLengthRule("asset_genealogy", "relation_type", 32),
    MaxLengthRule("system_audit_log", "table_name", 64),
    MaxLengthRule("system_audit_log", "operation", 16),
    MaxLengthRule("system_audit_log", "actor", 64),
)

# ==========================================================================
#  使用枚举类型的列
# ==========================================================================
ENUM_COLUMNS: tuple[EnumRule, ...] = (
    EnumRule("app_user", "role", "user_role_enum", ENUMS["user_role_enum"]),
    EnumRule("asset", "right_level", "right_level_enum", ENUMS["right_level_enum"]),
    EnumRule("asset", "audit_status", "audit_status_enum", ENUMS["audit_status_enum"]),
    EnumRule("asset", "ai_suggest_level", "right_level_enum", ENUMS["right_level_enum"]),
    EnumRule("audit_record", "ai_suggest_level", "right_level_enum", ENUMS["right_level_enum"]),
    EnumRule("audit_record", "expert_level", "right_level_enum", ENUMS["right_level_enum"]),
)

# ==========================================================================
#  CHECK 约束
# ==========================================================================
CHECKS: tuple[CheckRule, ...] = (
    CheckRule("app_user", "ck_app_user_username_fmt", "username ~ '^[a-zA-Z0-9_]{3,64}$'",
        Regex(Col("username"), '^[a-zA-Z0-9_]{3,64}$'),
    ),
    CheckRule("public_motif", "ck_public_motif_name", 'length(btrim(name)) > 0',
        Cmp(Length(Btrim(Col("name"))), '>', Lit(0)),
    ),
    CheckRule("public_motif", "ck_public_motif_vdim", 'base_vector IS NULL OR vector_dims(base_vector) = 512',
        Or(IsNull(Col("base_vector")), Cmp(VectorDims(Col("base_vector")), '=', Lit(512))),
    ),
    CheckRule("asset", "ck_asset_file_hash", "file_hash ~ '^[0-9a-f]{64}$'",
        Regex(Col("file_hash"), '^[0-9a-f]{64}$'),
    ),
    CheckRule("asset", "ck_asset_file_size", 'file_size > 0',
        Cmp(Col("file_size"), '>', Lit(0)),
    ),
    CheckRule("asset", "ck_asset_confidence", 'ai_confidence IS NULL OR (ai_confidence >= 0 AND ai_confidence <= 1)',
        Or(
            IsNull(Col("ai_confidence")),
            And(
                Cmp(Col("ai_confidence"), '>=', Lit(0)),
                Cmp(Col("ai_confidence"), '<=', Lit(1)),
            ),
        ),
    ),
    CheckRule("asset", "ck_asset_version", 'version_no >= 0',
        Cmp(Col("version_no"), '>=', Lit(0)),
    ),
    CheckRule("asset", "ck_asset_ai_pair", '(ai_suggest_level IS NULL AND ai_confidence IS NULL)\n     OR (ai_suggest_level IS NOT NULL AND ai_confidence IS NOT NULL)',
        Or(
            And(IsNull(Col("ai_suggest_level")), IsNull(Col("ai_confidence"))),
            And(
                IsNull(Col("ai_suggest_level"), negate=True),
                IsNull(Col("ai_confidence"), negate=True),
            ),
        ),
    ),
    CheckRule("feature_vector", "ck_fv_dim", 'embed_dim = 512',
        Cmp(Col("embed_dim"), '=', Lit(512)),
    ),
    CheckRule("feature_vector", "ck_fv_vdim", 'vector_dims(embedding) = 512',
        Cmp(VectorDims(Col("embedding")), '=', Lit(512)),
    ),
    CheckRule("feature_vector", "ck_fv_model", 'length(btrim(model_name)) > 0 AND length(btrim(model_version)) > 0',
        And(
            Cmp(Length(Btrim(Col("model_name"))), '>', Lit(0)),
            Cmp(Length(Btrim(Col("model_version"))), '>', Lit(0)),
        ),
    ),
    CheckRule("audit_record", "ck_audit_state", "state IN ('pending', 'expert_review', 'approved', 'rejected')",
        In(Col("state"), ('pending', 'expert_review', 'approved', 'rejected')),
    ),
    CheckRule("audit_record", "ck_audit_decided", "(state IN ('approved', 'rejected') AND expert_id IS NOT NULL\n            AND decided_at IS NOT NULL AND expert_level IS NOT NULL)\n     OR (state IN ('pending', 'expert_review') AND decided_at IS NULL AND expert_level IS NULL)",
        Or(
            And(
                In(Col("state"), ('approved', 'rejected')),
                IsNull(Col("expert_id"), negate=True),
                IsNull(Col("decided_at"), negate=True),
                IsNull(Col("expert_level"), negate=True),
            ),
            And(
                In(Col("state"), ('pending', 'expert_review')),
                IsNull(Col("decided_at")),
                IsNull(Col("expert_level")),
            ),
        ),
    ),
    CheckRule("license_record", "ck_lic_type", "license_type IN ('exclusive','non_exclusive','display','derivative')",
        In(Col("license_type"), ('exclusive', 'non_exclusive', 'display', 'derivative')),
    ),
    CheckRule("license_record", "ck_lic_status", "status IN ('active','expired','revoked')",
        In(Col("status"), ('active', 'expired', 'revoked')),
    ),
    CheckRule("license_record", "ck_lic_period", 'end_date IS NULL OR end_date >= start_date',
        Or(IsNull(Col("end_date")), Cmp(Col("end_date"), '>=', Col("start_date"))),
    ),
    CheckRule("license_record", "ck_lic_fee", 'fee_cny IS NULL OR fee_cny >= 0',
        Or(IsNull(Col("fee_cny")), Cmp(Col("fee_cny"), '>=', Lit(0))),
    ),
    CheckRule("license_record", "ck_lic_licensee", 'licensee_user_id IS NOT NULL OR licensee_name IS NOT NULL',
        Or(
            IsNull(Col("licensee_user_id"), negate=True),
            IsNull(Col("licensee_name"), negate=True),
        ),
    ),
    CheckRule("license_record", "ck_lic_hash", "contract_hash IS NULL OR contract_hash ~ '^[0-9a-f]{64}$'",
        Or(IsNull(Col("contract_hash")), Regex(Col("contract_hash"), '^[0-9a-f]{64}$')),
    ),
    CheckRule("license_record", "ck_lic_self", 'licensee_user_id IS NULL OR licensee_user_id <> licensor_id',
        Or(
            IsNull(Col("licensee_user_id")),
            Cmp(Col("licensee_user_id"), '<>', Col("licensor_id")),
        ),
    ),
    CheckRule("asset_genealogy", "ck_gen_not_self", 'parent_asset_id <> child_asset_id',
        Cmp(Col("parent_asset_id"), '<>', Col("child_asset_id")),
    ),
    CheckRule("asset_genealogy", "ck_gen_relation", "relation_type IN ('adapted','restored','collaborated')",
        In(Col("relation_type"), ('adapted', 'restored', 'collaborated')),
    ),
    CheckRule("asset_genealogy", "ck_gen_sim", 'similarity IS NULL OR (similarity >= 0 AND similarity <= 1)',
        Or(
            IsNull(Col("similarity")),
            And(Cmp(Col("similarity"), '>=', Lit(0)), Cmp(Col("similarity"), '<=', Lit(1))),
        ),
    ),
    CheckRule("system_audit_log", "ck_log_op", "operation IN ('INSERT','UPDATE','DELETE')",
        In(Col("operation"), ('INSERT', 'UPDATE', 'DELETE')),
    ),
)

# ==========================================================================
#  UNIQUE（含部分唯一索引：唯一性只作用于 WHERE 命中的子集）
# ==========================================================================
UNIQUE: tuple[UniqueRule, ...] = (
    UniqueRule("app_user", "uq_app_user_username", ("username",)),
    UniqueRule("public_motif", "uq_public_motif_code", ("motif_code",)),
    UniqueRule("asset_genealogy", "uq_gen_edge", ("parent_asset_id", "child_asset_id",)),
    UniqueRule("license_record", "uq_lic_exclusive_active", ("asset_id",),
        where=And(
            Cmp(Col("license_type"), '=', Lit('exclusive')),
            Cmp(Col("status"), '=', Lit('active')),
        ),
        index=True,
    ),
)

# ==========================================================================
#  外键与引用动作（ON DELETE RESTRICT 是溯源链的完整性基础）
# ==========================================================================
FOREIGN_KEYS: tuple[ForeignKeyRule, ...] = (
    ForeignKeyRule("asset", "fk_asset_owner", ("owner_id",),
        "app_user", ("user_id",),
        on_delete="RESTRICT", on_update="CASCADE"),
    ForeignKeyRule("asset", "fk_asset_motif", ("motif_id",),
        "public_motif", ("motif_id",),
        on_delete="SET NULL", on_update="CASCADE"),
    ForeignKeyRule("feature_vector", "fk_fv_asset", ("asset_id",),
        "asset", ("asset_id",),
        on_delete="CASCADE", on_update="NO ACTION"),
    ForeignKeyRule("audit_record", "fk_audit_asset", ("asset_id",),
        "asset", ("asset_id",),
        on_delete="RESTRICT", on_update="NO ACTION"),
    ForeignKeyRule("audit_record", "fk_audit_expert", ("expert_id",),
        "app_user", ("user_id",),
        on_delete="RESTRICT", on_update="NO ACTION"),
    ForeignKeyRule("license_record", "fk_lic_asset", ("asset_id",),
        "asset", ("asset_id",),
        on_delete="RESTRICT", on_update="NO ACTION"),
    ForeignKeyRule("license_record", "fk_lic_licensor", ("licensor_id",),
        "app_user", ("user_id",),
        on_delete="RESTRICT", on_update="NO ACTION"),
    ForeignKeyRule("license_record", "fk_lic_licensee", ("licensee_user_id",),
        "app_user", ("user_id",),
        on_delete="RESTRICT", on_update="NO ACTION"),
    ForeignKeyRule("license_record", "fk_lic_prev", ("prev_license_id",),
        "license_record", ("license_id",),
        on_delete="RESTRICT", on_update="NO ACTION"),
    ForeignKeyRule("asset_genealogy", "fk_gen_parent", ("parent_asset_id",),
        "asset", ("asset_id",),
        on_delete="RESTRICT", on_update="NO ACTION"),
    ForeignKeyRule("asset_genealogy", "fk_gen_child", ("child_asset_id",),
        "asset", ("asset_id",),
        on_delete="RESTRICT", on_update="NO ACTION"),
    ForeignKeyRule("asset_genealogy", "fk_gen_user", ("created_by",),
        "app_user", ("user_id",),
        on_delete="SET NULL", on_update="NO ACTION"),
)

# ==========================================================================
#  触发器语义
#    reject  无条件拒绝某类事件（追加式表防篡改）
#    gate    写入前要求父行处于允许状态（未终审不得授权）
#    guard   依据 OLD 行状态拒绝本次修改（已终审不可改）
#    actor_gate 依据数据库角色决定能不能改（传承人不能写终审结论）
#    derived 只改写 NEW 的派生列，不拒绝
#    audit   只写审计日志，不拒绝
#  前三类参与「接受/拒绝」比对；actor_gate 的判定依赖身份而非数据，
#  对拍样本描述不了它，改由 tests/check_pg 以真实角色切换验证；
#  后两类登记在案以防新触发器无人审阅。
# ==========================================================================
TRIGGERS: tuple[TriggerRule, ...] = (
    TriggerRule("asset", "trg_asset_right_change", "AFTER",
        ("INSERT", "UPDATE", "DELETE",), "fn_asset_audit_trigger", "audit",
    ),
    TriggerRule("asset", "trg_asset_touch", "BEFORE",
        ("UPDATE",), "fn_asset_touch", "derived",
    ),
    TriggerRule("audit_record", "trg_audit_record_no_delete", "BEFORE",
        ("DELETE",), "fn_forbid_delete", "reject",
    ),
    TriggerRule("audit_record", "trg_audit_record_guard", "BEFORE",
        ("UPDATE",), "fn_audit_record_guard", "guard",
        forbid_when_old_state_in=("approved", "rejected",),
    ),
    TriggerRule("system_audit_log", "trg_syslog_immutable", "BEFORE",
        ("UPDATE", "DELETE",), "fn_forbid_modify", "reject",
    ),
    TriggerRule("license_record", "trg_license_no_delete", "BEFORE",
        ("DELETE",), "fn_forbid_delete", "reject",
    ),
    TriggerRule("license_record", "trg_license_require_approved", "BEFORE",
        ("INSERT",), "fn_license_require_approved", "gate",
        gate=ParentGate(
            "asset_id", "asset",
            "audit_status", ("approved",),
        ),
    ),
    TriggerRule("asset", "trg_asset_status_guard", "BEFORE",
        ("INSERT", "UPDATE",), "fn_asset_status_guard", "actor_gate",
        actor_roles=("ich_heritor", "ich_expert", "ich_admin",),
        final_states=("approved", "rejected",),
        gate_marker="ich.finalize_gate",
        gate_values=("approve_asset", "reject_asset",),
    ),
    TriggerRule("public_motif", "trg_motif_vector_norm", "BEFORE",
        ("INSERT", "UPDATE",), "fn_motif_vector_normalize", "derived",
    ),
    TriggerRule("asset_genealogy", "trg_genealogy_no_cycle", "BEFORE",
        ("INSERT", "UPDATE",), "fn_genealogy_no_cycle", "dag",
    ),
)

CONSTRAINT_NAMES: frozenset[str] = frozenset({
    "ck_app_user_username_fmt",
    "ck_asset_ai_pair",
    "ck_asset_confidence",
    "ck_asset_file_hash",
    "ck_asset_file_size",
    "ck_asset_version",
    "ck_audit_decided",
    "ck_audit_state",
    "ck_fv_dim",
    "ck_fv_model",
    "ck_fv_vdim",
    "ck_gen_not_self",
    "ck_gen_relation",
    "ck_gen_sim",
    "ck_lic_fee",
    "ck_lic_hash",
    "ck_lic_licensee",
    "ck_lic_period",
    "ck_lic_self",
    "ck_lic_status",
    "ck_lic_type",
    "ck_log_op",
    "ck_public_motif_name",
    "ck_public_motif_vdim",
    "fk_asset_motif",
    "fk_asset_owner",
    "fk_audit_asset",
    "fk_audit_expert",
    "fk_fv_asset",
    "fk_gen_child",
    "fk_gen_parent",
    "fk_gen_user",
    "fk_lic_asset",
    "fk_lic_licensee",
    "fk_lic_licensor",
    "fk_lic_prev",
    "trg_asset_right_change",
    "trg_asset_status_guard",
    "trg_asset_touch",
    "trg_audit_record_guard",
    "trg_audit_record_no_delete",
    "trg_genealogy_no_cycle",
    "trg_license_no_delete",
    "trg_license_require_approved",
    "trg_motif_vector_norm",
    "trg_syslog_immutable",
    "uq_app_user_username",
    "uq_gen_edge",
    "uq_lic_exclusive_active",
    "uq_public_motif_code",
})

REJECTING_TRIGGERS: tuple[TriggerRule, ...] = tuple(
    t for t in TRIGGERS if t.kind in ("reject", "gate", "guard")
)

