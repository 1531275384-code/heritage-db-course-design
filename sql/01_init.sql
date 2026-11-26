-- =============================================================================
--  非遗数字资产分级确权与溯源系统 —— 数据库初始化脚本 (Part 1 / 结构)
--  RDBMS : PostgreSQL 15+ (本工程在 PostgreSQL 16.4 上验证通过)
--  扩展  : pgvector (向量检索)、pgcrypto (计算文件摘要/口令校验)
--  执行  : psql -U postgres -d ich_heritage -v ON_ERROR_STOP=1 -f sql/01_init.sql
--
--  【数据库原理对照点总览】
--    范式      : 3NF 设计，特征向量单独成表(1:1 垂直拆分)避免大字段拖累主表扫描
--    完整性    : 主键 / 外键(ON DELETE RESTRICT 保留证据链) / CHECK / UNIQUE / NOT NULL
--    触发器    : 行级 BEFORE/AFTER 触发器实现"变更留痕"与"追加式不可变"
--    视图      : 3 个只读视图封装复杂多表连接，对外提供最小必要列(逻辑数据独立性)
--    索引      : B-Tree 组合索引 + 部分索引 + HNSW 向量索引
--    事务隔离  : 终审业务用 READ COMMITTED + SELECT ... FOR UPDATE 悲观锁
-- =============================================================================

\echo '>>> [1/9] 创建扩展'
-- pgvector 可选：装了就用原生向量类型，没装就降级为 NUMERIC[] + 自定义 <=> 运算符。
-- 见 sql/00_compat_vector.sql 的说明与代价。
\i 00_compat_vector.sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;    -- crypt()/gen_salt()：口令散列；digest()：摘要

-- 本脚本用 psql 的 \if 做条件分支（psql 10+ 支持），按 pgvector 是否存在切换向量列类型。
-- 全文件共两处分支：① 母题表/特征表的向量列；② HNSW 索引与种子数据的向量字面量。
SELECT ich_vector_is_native() AS use_native \gset
\if :use_native
    \set ICH_VEC_TYPE 'VECTOR(512)'
    \echo '    向量列类型：VECTOR(512)（pgvector 原生模式）'
\else
    \set ICH_VEC_TYPE 'NUMERIC[]'
    \echo '    向量列类型：NUMERIC[]（降级模式，无 HNSW 索引）'
\endif

-- -----------------------------------------------------------------------------
-- 自定义枚举：分级标签 / 审核状态 / 用户角色
-- 【原理】枚举类型在数据库层收敛取值域，比 VARCHAR 更省空间且强约束
-- -----------------------------------------------------------------------------
\echo '>>> [2/9] 创建枚举类型'
DO $$ BEGIN
    CREATE TYPE right_level_enum AS ENUM ('public', 'derived', 'original');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE audit_status_enum AS ENUM ('draft', 'pending', 'approved', 'rejected');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE user_role_enum AS ENUM ('heritor', 'expert', 'admin');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- =============================================================================
--  [1] app_user 用户表
--  业务角色：heritor 传承人 / expert 评审专家 / admin 监管管理员
-- =============================================================================
\echo '>>> [3/9] 创建表 app_user'
DROP TABLE IF EXISTS app_user CASCADE;
CREATE TABLE app_user (
    user_id       BIGSERIAL        PRIMARY KEY,
    username      VARCHAR(64)      NOT NULL,
    display_name  VARCHAR(128)     NOT NULL,
    role          user_role_enum   NOT NULL,
    password_hash TEXT             NOT NULL,
    org_name      VARCHAR(200),                                    -- 所属单位
    is_active     BOOLEAN          NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ      NOT NULL DEFAULT now(),

    CONSTRAINT uq_app_user_username UNIQUE (username),
    -- 【CHECK】用户名只允许字母数字下划线，避免注入型脏数据
    CONSTRAINT ck_app_user_username_fmt CHECK (username ~ '^[a-zA-Z0-9_]{3,64}$')
);
COMMENT ON TABLE  app_user IS '系统用户；role 决定 RBAC 权限边界';
COMMENT ON COLUMN app_user.role IS 'heritor=传承人(上传者) / expert=评审专家(终审) / admin=监管机构(全量只读+管理)';


-- =============================================================================
--  [2] public_motif 非遗公有母题表
--  公有领域传统纹样（蝴蝶纹、缠枝莲纹、回纹……），不可被个人独占
--  base_vector: 512 维特征向量，供 HNSW 近邻检索
-- =============================================================================
\echo '>>> [4/9] 创建表 public_motif'
DROP TABLE IF EXISTS public_motif CASCADE;
CREATE TABLE public_motif (
    motif_id      BIGSERIAL        PRIMARY KEY,
    motif_code    VARCHAR(32)      NOT NULL,                       -- 母题编号，如 MO-001
    name          VARCHAR(128)     NOT NULL,
    region        VARCHAR(128),                                    -- 主要流传地域
    description   TEXT,
    reference_img VARCHAR(512),                                    -- 母题参考图相对路径
    base_vector   :ICH_VEC_TYPE,                                   -- 512 维基准特征向量
    vector_model  VARCHAR(64),                                     -- 生成该向量的模型版本
    created_at    TIMESTAMPTZ      NOT NULL DEFAULT now(),

    CONSTRAINT uq_public_motif_code UNIQUE (motif_code),
    CONSTRAINT ck_public_motif_name CHECK (length(btrim(name)) > 0),
    -- 降级模式下没有类型级维度约束，用 CHECK 补上（原生模式下 vector_dims 由 pgvector 提供）
    CONSTRAINT ck_public_motif_vdim CHECK (
        base_vector IS NULL OR vector_dims(base_vector) = 512
    )
);
COMMENT ON TABLE  public_motif IS '非遗公有母题库：公有领域传统纹样，任何人可自由使用，但不可被确权为私有';
COMMENT ON COLUMN public_motif.base_vector IS '512 维 L2 归一化特征向量，配合 HNSW(cosine) 索引做相似检索';


-- =============================================================================
--  [3] asset 非遗数字资产主表
--  核心约束：AI 只能写 ai_suggest_level，不能写 right_level —— 由列级 GRANT + 触发器双重保证
-- =============================================================================
\echo '>>> [5/9] 创建表 asset'
DROP TABLE IF EXISTS asset CASCADE;
CREATE TABLE asset (
    asset_id          BIGSERIAL        PRIMARY KEY,
    title             VARCHAR(200)     NOT NULL,
    owner_id          BIGINT           NOT NULL,                   -- 传承人(权利人)
    motif_id          BIGINT,                                      -- 关联公有母题(可为空=无母题血缘)
    file_path         VARCHAR(512)     NOT NULL,                   -- 原图存储相对路径
    file_hash         CHAR(64)         NOT NULL,                   -- SHA-256，用于查重与完整性校验
    file_size         BIGINT           NOT NULL,
    mime_type         VARCHAR(64)      NOT NULL DEFAULT 'image/jpeg',
    right_level       right_level_enum NOT NULL DEFAULT 'public',   -- 【终审结论】当前权属级别
    audit_status      audit_status_enum NOT NULL DEFAULT 'draft',  -- 【流转状态】
    ai_suggest_level  right_level_enum,                            -- 【AI 建议】只读参考
    ai_confidence     NUMERIC(5,4),                                -- AI 建议置信度 0~1
    ai_model_version  VARCHAR(64),                                 -- 产出建议的模型版本(可追溯)
    version_no        INTEGER          NOT NULL DEFAULT 0,         -- 乐观锁版本号
    description       TEXT,
    created_at        TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ      NOT NULL DEFAULT now(),

    -- 【外键】所有者必须存在；禁止物理删除有资产的用户，保护溯源链完整
    CONSTRAINT fk_asset_owner FOREIGN KEY (owner_id)
        REFERENCES app_user (user_id) ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT fk_asset_motif FOREIGN KEY (motif_id)
        REFERENCES public_motif (motif_id) ON DELETE SET NULL ON UPDATE CASCADE,

    -- 【CHECK】业务不变量
    CONSTRAINT ck_asset_file_hash   CHECK (file_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_asset_file_size   CHECK (file_size > 0),
    CONSTRAINT ck_asset_confidence  CHECK (ai_confidence IS NULL OR (ai_confidence >= 0 AND ai_confidence <= 1)),
    CONSTRAINT ck_asset_version     CHECK (version_no >= 0),
    -- 已终审通过，必须已绑定公有母题链路或明确为原创 —— 这里只约束状态与结论的一致性：
    -- 状态为 approved 时 right_level 必须已被显式设置（默认值 public 不允许直接 approved）
    CONSTRAINT ck_asset_ai_pair     CHECK (
        (ai_suggest_level IS NULL AND ai_confidence IS NULL)
     OR (ai_suggest_level IS NOT NULL AND ai_confidence IS NOT NULL)
    )
);
COMMENT ON TABLE  asset IS '非遗数字资产主表；right_level 为终审结论，ai_suggest_level 仅为机器建议';
COMMENT ON COLUMN asset.version_no IS '乐观锁版本号：终审/变更时 +1，用于并发冲突检测';


-- =============================================================================
--  [4] feature_vector 资产特征向量表（asset 1:1 垂直拆分）
--  为什么要拆表？向量列体积大(512*4=2KB)，与主表放一起会拖慢 asset 的全表扫描，
--  拆分后主表行更窄、缓存命中率更高 —— 这是典型的「垂直拆分」物理设计
-- =============================================================================
\echo '>>> [6/9] 创建表 feature_vector'
DROP TABLE IF EXISTS feature_vector CASCADE;
CREATE TABLE feature_vector (
    fv_id         BIGSERIAL      PRIMARY KEY,
    asset_id      BIGINT         NOT NULL,
    model_name    VARCHAR(64)    NOT NULL,                         -- 如 clip-vit-base-patch32
    model_version VARCHAR(32)    NOT NULL,                         -- 如 1.0.0
    embed_dim     SMALLINT       NOT NULL,                         -- 向量维度(本工程恒为 512)
    embedding     :ICH_VEC_TYPE   NOT NULL,
    created_at    TIMESTAMPTZ    NOT NULL DEFAULT now(),

    CONSTRAINT fk_fv_asset FOREIGN KEY (asset_id)
        REFERENCES asset (asset_id) ON DELETE CASCADE,
    -- 【CHECK】维度自洽，防止误写入错误维度导致检索静默失败
    CONSTRAINT ck_fv_dim   CHECK (embed_dim = 512),
    CONSTRAINT ck_fv_vdim  CHECK (vector_dims(embedding) = 512),
    CONSTRAINT ck_fv_model CHECK (length(btrim(model_name)) > 0 AND length(btrim(model_version)) > 0)
);
COMMENT ON TABLE feature_vector IS '资产特征向量，与 asset 1:1；重算特征时保留历史版本(model_version)';
COMMENT ON COLUMN feature_vector.embedding IS 'L2 归一化 512 维向量，cosine 相似度等价于内积';


-- =============================================================================
--  [5] audit_record 权属审核流水（追加式，永不可改）
--  state: pending(AI 已出建议待审) → expert_review(专家领取) → approved/rejected(终审)
-- =============================================================================
\echo '>>> [7/9] 创建表 audit_record'
DROP TABLE IF EXISTS audit_record CASCADE;
CREATE TABLE audit_record (
    audit_id        BIGSERIAL         PRIMARY KEY,
    asset_id        BIGINT            NOT NULL,
    expert_id       BIGINT,                                        -- 终审专家(待审阶段为空)
    state           VARCHAR(24)       NOT NULL DEFAULT 'pending',
    ai_suggest_level right_level_enum,                             -- 快照：AI 当时的建议
    ai_confidence   NUMERIC(5,4),
    expert_level    right_level_enum,                              -- 专家终审结论
    similarity_json JSONB,                                         -- top-K 相似母题快照
    opinion         TEXT,                                          -- 专家意见
    is_override     BOOLEAN           NOT NULL DEFAULT FALSE,       -- 专家是否推翻了 AI 建议
    created_at      TIMESTAMPTZ       NOT NULL DEFAULT now(),
    decided_at      TIMESTAMPTZ,

    CONSTRAINT fk_audit_asset  FOREIGN KEY (asset_id)
        REFERENCES asset (asset_id) ON DELETE RESTRICT,
    CONSTRAINT fk_audit_expert FOREIGN KEY (expert_id)
        REFERENCES app_user (user_id) ON DELETE RESTRICT,
    CONSTRAINT ck_audit_state CHECK (state IN ('pending', 'expert_review', 'approved', 'rejected')),
    -- 【CHECK】已决策的记录必须有专家与决策时间；未决策的记录不得有终审结论
    CONSTRAINT ck_audit_decided CHECK (
        (state IN ('approved', 'rejected') AND expert_id IS NOT NULL
            AND decided_at IS NOT NULL AND expert_level IS NOT NULL)
     OR (state IN ('pending', 'expert_review') AND decided_at IS NULL AND expert_level IS NULL)
    )
);
COMMENT ON TABLE audit_record IS '权属审核流水：追加式，每个资产可有多次审核(驳回后可再提交)';


-- =============================================================================
--  [6] license_record 授权流转记录（溯源的链式核心）
--  licensee_user_id 与被授权方名称冗余存储：外部机构可能不是系统用户
-- =============================================================================
\echo '>>> [8/9] 创建表 license_record'
DROP TABLE IF EXISTS license_record CASCADE;
CREATE TABLE license_record (
    license_id        BIGSERIAL       PRIMARY KEY,
    asset_id          BIGINT          NOT NULL,
    licensor_id       BIGINT          NOT NULL,                     -- 授权方(通常为 owner)
    licensee_user_id  BIGINT,                                       -- 被授权方(系统内用户)
    licensee_name     VARCHAR(200),                                 -- 被授权方(外部机构名称)
    license_type      VARCHAR(32)     NOT NULL,                     -- exclusive/non_exclusive/display/derivative
    scope             TEXT,
    territory         VARCHAR(128),
    start_date        DATE            NOT NULL,
    end_date          DATE,
    fee_cny           NUMERIC(12,2),
    contract_hash     CHAR(64),                                     -- 合同文件摘要，防篡改
    status            VARCHAR(16)     NOT NULL DEFAULT 'active',    -- active/expired/revoked
    prev_license_id   BIGINT,                                       -- 链式指向上一次授权，形成流转链
    created_at        TIMESTAMPTZ     NOT NULL DEFAULT now(),

    CONSTRAINT fk_lic_asset    FOREIGN KEY (asset_id)
        REFERENCES asset (asset_id) ON DELETE RESTRICT,
    CONSTRAINT fk_lic_licensor FOREIGN KEY (licensor_id)
        REFERENCES app_user (user_id) ON DELETE RESTRICT,
    CONSTRAINT fk_lic_licensee FOREIGN KEY (licensee_user_id)
        REFERENCES app_user (user_id) ON DELETE RESTRICT,
    CONSTRAINT fk_lic_prev     FOREIGN KEY (prev_license_id)
        REFERENCES license_record (license_id) ON DELETE RESTRICT,

    CONSTRAINT ck_lic_type   CHECK (license_type IN ('exclusive','non_exclusive','display','derivative')),
    CONSTRAINT ck_lic_status CHECK (status IN ('active','expired','revoked')),
    CONSTRAINT ck_lic_period CHECK (end_date IS NULL OR end_date >= start_date),
    CONSTRAINT ck_lic_fee    CHECK (fee_cny IS NULL OR fee_cny >= 0),
    -- 【CHECK】被授权方必须至少有一种标识方式
    CONSTRAINT ck_lic_licensee CHECK (licensee_user_id IS NOT NULL OR licensee_name IS NOT NULL),
    CONSTRAINT ck_lic_hash   CHECK (contract_hash IS NULL OR contract_hash ~ '^[0-9a-f]{64}$'),
    -- 不能自己授权给自己
    CONSTRAINT ck_lic_self   CHECK (licensee_user_id IS NULL OR licensee_user_id <> licensor_id)
);
COMMENT ON TABLE license_record IS '授权流转记录：prev_license_id 自引用构成授权链，支撑溯源视图';

-- 【部分唯一索引】同一资产、同一时间点只允许存在一份"现行有效"的独占授权
-- 【原理】用部分索引(WHERE)把唯一性约束限制在业务关心的子集上，避免影响历史记录
DROP INDEX IF EXISTS uq_lic_exclusive_active;
CREATE UNIQUE INDEX uq_lic_exclusive_active
    ON license_record (asset_id)
    WHERE license_type = 'exclusive' AND status = 'active';


-- =============================================================================
--  [7] asset_genealogy 衍生谱系（有向无环图）
--  parent → child 表示"改编自"。自引用 + CHECK 阻止自环
-- =============================================================================
\echo '>>> [9/9] 创建表 asset_genealogy'
DROP TABLE IF EXISTS asset_genealogy CASCADE;
CREATE TABLE asset_genealogy (
    genealogy_id   BIGSERIAL     PRIMARY KEY,
    parent_asset_id BIGINT       NOT NULL,
    child_asset_id  BIGINT       NOT NULL,
    relation_type   VARCHAR(32)  NOT NULL DEFAULT 'adapted',       -- adapted/restored/collaborated
    similarity      NUMERIC(5,4),                                  -- 父子的视觉相似度快照
    note            TEXT,
    created_by      BIGINT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT fk_gen_parent FOREIGN KEY (parent_asset_id)
        REFERENCES asset (asset_id) ON DELETE RESTRICT,
    CONSTRAINT fk_gen_child  FOREIGN KEY (child_asset_id)
        REFERENCES asset (asset_id) ON DELETE RESTRICT,
    CONSTRAINT fk_gen_user   FOREIGN KEY (created_by)
        REFERENCES app_user (user_id) ON DELETE SET NULL,
    CONSTRAINT ck_gen_not_self CHECK (parent_asset_id <> child_asset_id),
    CONSTRAINT ck_gen_relation CHECK (relation_type IN ('adapted','restored','collaborated')),
    CONSTRAINT ck_gen_sim CHECK (similarity IS NULL OR (similarity >= 0 AND similarity <= 1)),
    CONSTRAINT uq_gen_edge UNIQUE (parent_asset_id, child_asset_id)
);
COMMENT ON TABLE asset_genealogy IS '衍生谱系边表：parent 改编为 child，构成 DAG（多跳成环由 trg_genealogy_no_cycle 拒绝）';


-- =============================================================================
--  [8] system_audit_log 全局审计日志（触发器自动写入，只插入不修改）
-- =============================================================================
\echo '>>> [10/13] 创建表 system_audit_log'
DROP TABLE IF EXISTS system_audit_log CASCADE;
CREATE TABLE system_audit_log (
    log_id      BIGSERIAL     PRIMARY KEY,
    table_name  VARCHAR(64)   NOT NULL,
    record_id   BIGINT        NOT NULL,
    operation   VARCHAR(16)   NOT NULL,                            -- INSERT/UPDATE/DELETE
    actor       VARCHAR(64)   NOT NULL DEFAULT current_user,       -- 数据库会话用户
    old_snapshot JSONB,
    new_snapshot JSONB,
    changed_fields TEXT[],                                         -- 仅变更的列名，便于检索
    logged_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT ck_log_op CHECK (operation IN ('INSERT','UPDATE','DELETE'))
);
COMMENT ON TABLE system_audit_log IS '全局审计日志：由 asset_audit_trigger 自动写入，应用层无 UPDATE/DELETE 权限';
