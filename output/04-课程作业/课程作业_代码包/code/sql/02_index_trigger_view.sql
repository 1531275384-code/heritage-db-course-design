-- =============================================================================
--  非遗数字资产分级确权与溯源系统 —— 数据库脚本 (Part 2 / 索引·触发器·视图·权限)
--  执行前提：已执行 sql/01_init.sql
-- =============================================================================

-- 本脚本可能被单独执行，所以自己判断一次向量模式（不依赖 01 里设的 psql 变量）
SELECT ich_vector_is_native() AS use_native \gset

-- =============================================================================
--  A. 索引设计
--  【数据库原理】索引不是越多越好：每个索引都会拖慢写入。这里只建支撑
--  真实查询路径的索引，并说明每个索引对应哪条查询。
-- =============================================================================
\echo '>>> [11/14] 创建索引'

-- ① 资产查重：上传时按 SHA-256 命中即复用已有资产（避免重复确权）
CREATE INDEX IF NOT EXISTS idx_asset_file_hash ON asset (file_hash);

-- ② 专家工作台：v_asset_pending_audit 的驱动条件 audit_status='pending'
--    使用【部分索引】：只索引待审的少数行，索引体积小、命中快
CREATE INDEX IF NOT EXISTS idx_asset_pending
    ON asset (created_at DESC)
    WHERE audit_status = 'pending';

-- ③ 传承人"我的作品"：owner_id 等值 + created_at 排序 → 组合索引覆盖排序
CREATE INDEX IF NOT EXISTS idx_asset_owner_time ON asset (owner_id, created_at DESC);

-- ④ 监管台账按权属级别统计/筛选
CREATE INDEX IF NOT EXISTS idx_asset_right_level ON asset (right_level);

-- ⑤ 母题血缘：按 motif_id 汇总衍生作品
CREATE INDEX IF NOT EXISTS idx_asset_motif ON asset (motif_id) WHERE motif_id IS NOT NULL;

-- ⑥ 审核流水按资产查询（溯源链路视图用）
CREATE INDEX IF NOT EXISTS idx_audit_record_asset ON audit_record (asset_id, created_at DESC);

-- ⑦ 待审队列按状态过滤
CREATE INDEX IF NOT EXISTS idx_audit_record_state ON audit_record (state);

-- ⑧ 授权记录按资产查询（溯源链路视图用）
CREATE INDEX IF NOT EXISTS idx_license_asset ON license_record (asset_id, created_at);

-- ⑨ 索引审计日志：按表名+记录定位历史
CREATE INDEX IF NOT EXISTS idx_audit_log_target ON system_audit_log (table_name, record_id, logged_at DESC);

-- ⑩ 审计日志时间轴查询（监管复盘）
CREATE INDEX IF NOT EXISTS idx_audit_log_time ON system_audit_log (logged_at DESC);

-- ⑪ 谱系双向查询
CREATE INDEX IF NOT EXISTS idx_genealogy_child ON asset_genealogy (child_asset_id);

-- ⑫【向量索引】HNSW：图结构近似最近邻，适合"边写边查"的在线场景
--    vector_cosine_ops 与 cosine 距离运算符 <=> 匹配
--    m=16 每层最大连接数；ef_construction=64 建图时的候选队列大小
--
--    ⚠ 仅 pgvector 原生模式可用。降级模式（NUMERIC[]）建不了 HNSW，
--      这里跳过并把事实打印出来，而不是让整个脚本报错中断。
--      跳过意味着：功能正常，检索走精确扫描；性能结论不可在此模式得出。
\if :use_native
    \echo '    HNSW：创建 public_motif / feature_vector 两个向量索引'
CREATE INDEX IF NOT EXISTS idx_public_motif_hnsw
    ON public_motif USING hnsw (base_vector vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ⑬ 资产特征向量同样建 HNSW：支持"以图搜图"找相似资产（衍生关系发现）
CREATE INDEX IF NOT EXISTS idx_feature_vector_hnsw
    ON feature_vector USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
\else
    \echo '    HNSW：已跳过（当前为降级模式，无 pgvector；检索走精确扫描）'
\endif

-- ⑭ audit_record.similarity_json 的 GIN 索引：支持按 JSONB 内容检索
CREATE INDEX IF NOT EXISTS idx_audit_record_sim_gin
    ON audit_record USING gin (similarity_json jsonb_path_ops);


-- =============================================================================
--  B. 通用函数：行转 JSONB（触发器做快照用）
-- =============================================================================
\echo '>>> [12/14] 创建触发器函数'
CREATE OR REPLACE FUNCTION row_to_jsonb_safe(tbl regclass, rec ANYELEMENT)
RETURNS JSONB AS $$
DECLARE
    snap JSONB;
BEGIN
    EXECUTE format('SELECT to_jsonb($1)') INTO snap USING rec;
    RETURN snap;
END $$ LANGUAGE plpgsql IMMUTABLE;


-- -----------------------------------------------------------------------------
--  B1. 资产权属/状态变更自动审计触发器  ★核心★
--  【数据库原理】AFTER UPDATE 行级触发器，同一事务内执行：
--    - 若主业务事务回滚，审计日志一并回滚（原子性）
--    - 只记录 right_level / audit_status 实际发生变化的行（避免噪音）
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_asset_audit_trigger()
RETURNS TRIGGER AS $$
DECLARE
    v_changed TEXT[] := ARRAY[]::TEXT[];
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO system_audit_log (table_name, record_id, operation, actor, new_snapshot, changed_fields)
        VALUES ('asset', NEW.asset_id, 'INSERT', current_user, to_jsonb(NEW), NULL);
        RETURN NEW;
    END IF;

    IF TG_OP = 'DELETE' THEN
        INSERT INTO system_audit_log (table_name, record_id, operation, actor, old_snapshot, changed_fields)
        VALUES ('asset', OLD.asset_id, 'DELETE', current_user, to_jsonb(OLD), NULL);
        RETURN OLD;
    END IF;

    -- UPDATE：逐列比对，收集真实变更列
    IF NEW.right_level  IS DISTINCT FROM OLD.right_level  THEN v_changed := v_changed || 'right_level';  END IF;
    IF NEW.audit_status IS DISTINCT FROM OLD.audit_status THEN v_changed := v_changed || 'audit_status'; END IF;
    IF NEW.motif_id     IS DISTINCT FROM OLD.motif_id     THEN v_changed := v_changed || 'motif_id';     END IF;
    IF NEW.owner_id     IS DISTINCT FROM OLD.owner_id     THEN v_changed := v_changed || 'owner_id';     END IF;

    -- 只对"权属/状态"类变更留痕；普通元数据更新(标题、描述)不写业务审计日志
    IF array_length(v_changed, 1) IS NULL THEN
        RETURN NEW;
    END IF;

    INSERT INTO system_audit_log (
        table_name, record_id, operation, actor, old_snapshot, new_snapshot, changed_fields
    ) VALUES (
        'asset', NEW.asset_id, 'UPDATE', current_user,
        jsonb_build_object(
            'right_level',  OLD.right_level,
            'audit_status', OLD.audit_status,
            'motif_id',     OLD.motif_id,
            'owner_id',     OLD.owner_id,
            'version_no',   OLD.version_no,
            'updated_at',   OLD.updated_at
        ),
        jsonb_build_object(
            'right_level',  NEW.right_level,
            'audit_status', NEW.audit_status,
            'motif_id',     NEW.motif_id,
            'owner_id',     NEW.owner_id,
            'version_no',   NEW.version_no,
            'updated_at',   NEW.updated_at
        ),
        v_changed
    );
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_asset_right_change ON asset;
CREATE TRIGGER trg_asset_right_change
    AFTER INSERT OR UPDATE OR DELETE ON asset
    FOR EACH ROW EXECUTE FUNCTION fn_asset_audit_trigger();

COMMENT ON TRIGGER trg_asset_right_change ON asset IS
'权属/状态变更自动审计：right_level、audit_status、motif_id、owner_id 变化时写入 system_audit_log';


-- -----------------------------------------------------------------------------
--  B2. asset.updated_at 自动维护 + version_no 自增
--  【数据库原理】BEFORE 触发器可直接修改 NEW，实现"派生列"维护
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_asset_touch()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    NEW.version_no := OLD.version_no + 1;   -- 乐观锁版本号自增
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_asset_touch ON asset;
CREATE TRIGGER trg_asset_touch
    BEFORE UPDATE ON asset
    FOR EACH ROW EXECUTE FUNCTION fn_asset_touch();

-- 注意触发器执行顺序：PostgreSQL 同事件同级别按名称字母序执行
--   trg_asset_right_change(AFTER) 在 trg_asset_touch(BEFORE) 之后，符合预期


-- -----------------------------------------------------------------------------
--  B3. 追加式表防篡改触发器  ★核心★
--  【数据库原理】用触发器在数据库层强制实现"追加式日志"(append-only)：
--    即使应用有 UPDATE 权限，也无法改写历史，保证审计证据的法律效力
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_forbid_modify()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION '表 % 为追加式日志，禁止 % 操作（历史不可篡改）', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION fn_forbid_delete()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION '表 % 为追加式日志，禁止 DELETE 操作（历史不可篡改）', TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_record_immutable ON audit_record;

-- ⚠️ 这里曾挂过一个 trg_audit_record_immutable（BEFORE UPDATE OR DELETE → fn_forbid_modify），
--    后来删掉了。原因：它会对**任何** UPDATE 无条件抛异常，
--    而专家终审的核心动作正是 UPDATE audit_record SET state='approved'，
--    挂上它会把整条终审链路堵死（approve_asset 第 4 步必然失败）。
--
--    audit_record 的"不可篡改"由下面两个触发器**分工**实现，粒度更准：
--      trg_audit_record_no_delete  → 一律禁止 DELETE（流水只能追加）
--      trg_audit_record_guard      → 禁止修改**已终审**记录，但放行
--                                    pending → expert_review → approved/rejected 的状态迁移
--    这两个触发器是互补的：删除被彻底禁止，修改被限制在"决策字段首次写入"这一刻。
DROP TRIGGER IF EXISTS trg_audit_record_no_delete ON audit_record;
CREATE TRIGGER trg_audit_record_no_delete
    BEFORE DELETE ON audit_record
    FOR EACH ROW EXECUTE FUNCTION fn_forbid_delete();

CREATE OR REPLACE FUNCTION fn_audit_record_guard()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.state IN ('approved', 'rejected') THEN
        RAISE EXCEPTION '审核记录 % 已终审(状态 %)，不可再修改', OLD.audit_id, OLD.state
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_record_guard ON audit_record;
CREATE TRIGGER trg_audit_record_guard
    BEFORE UPDATE ON audit_record
    FOR EACH ROW EXECUTE FUNCTION fn_audit_record_guard();

DROP TRIGGER IF EXISTS trg_syslog_immutable ON system_audit_log;
CREATE TRIGGER trg_syslog_immutable
    BEFORE UPDATE OR DELETE ON system_audit_log
    FOR EACH ROW EXECUTE FUNCTION fn_forbid_modify();

DROP TRIGGER IF EXISTS trg_license_no_delete ON license_record;
CREATE TRIGGER trg_license_no_delete
    BEFORE DELETE ON license_record
    FOR EACH ROW EXECUTE FUNCTION fn_forbid_delete();


-- -----------------------------------------------------------------------------
--  B4. 母题向量写入时自动 L2 归一化（保证 cosine 距离计算稳定）
--
--  两种模式下的写法差异：
--    原生 pgvector：base_vector 是 vector 类型，需 ::real[] 才能喂给 unnest
--                   （pgvector 没有给 vector 定义数组下标/unnest）
--    降级模式     ：base_vector 已经是 NUMERIC[]，直接 unnest 即可
--  用 ich_vector_is_native() 在运行时分支，避免为两种模式复制整个函数
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_motif_vector_normalize()
RETURNS TRIGGER AS $$
DECLARE
    v_norm   DOUBLE PRECISION;
    v_native BOOLEAN := ich_vector_is_native();
BEGIN
    IF NEW.base_vector IS NOT NULL THEN
        IF v_native THEN
            -- pgvector：vector → real[] → 求模
            SELECT sqrt(sum(x * x)) INTO v_norm
            FROM unnest(NEW.base_vector::real[]) AS x;
        ELSE
            -- 降级模式：本身就是数组
            SELECT sqrt(sum(x * x)) INTO v_norm
            FROM unnest(NEW.base_vector) AS x;
        END IF;

        IF v_norm > 0 THEN
            IF v_native THEN
                NEW.base_vector := (NEW.base_vector::real[] / v_norm)::vector;
            ELSE
                NEW.base_vector := NEW.base_vector / v_norm;
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_motif_vector_norm ON public_motif;
CREATE TRIGGER trg_motif_vector_norm
    BEFORE INSERT OR UPDATE OF base_vector ON public_motif
    FOR EACH ROW EXECUTE FUNCTION fn_motif_vector_normalize();

-- 首次导入母题时未带向量，待 AI 回填；回填后由触发器归一化
-- （asset 侧向量由 Python 侧归一化后写入，避免每行向量运算开销）


-- =============================================================================
--  C. 视图（对应用层屏蔽多表连接细节，同时限定可见列 —— 逻辑数据独立性）
--  【数据库原理】视图是"存储的查询"，本身不存数据；这里用普通视图(非物化)，
--  保证查询到的是实时一致数据，代价是每次都要执行查询计划。
-- =============================================================================
\echo '>>> [13/14] 创建视图'

DROP VIEW IF EXISTS v_asset_pending_audit;
-- -----------------------------------------------------------------------------
--  C1. v_asset_pending_audit 专家待审列表
--  待审 = asset.audit_status='pending' 且存在 pending 审核记录
--  附带 top1 相似母题名称/相似度，专家一眼看到风险点
-- -----------------------------------------------------------------------------
CREATE VIEW v_asset_pending_audit AS
SELECT
    a.asset_id,
    a.title,
    a.owner_id,
    u.display_name                                        AS owner_name,
    u.org_name                                            AS owner_org,
    a.right_level                                         AS current_right_level,
    a.ai_suggest_level,
    a.ai_confidence,
    a.ai_model_version,
    a.created_at                                          AS uploaded_at,
    ar.audit_id,
    ar.created_at                                         AS submitted_at,
    ar.similarity_json,
    -- 从 AI 快照 JSONB 中取 top1 母题，便于专家一眼看到最相似的公有母题
    (ar.similarity_json -> 0 ->> 'name')                  AS top1_motif_name,
    ((ar.similarity_json -> 0 ->> 'similarity')::numeric) AS top1_similarity,
    (SELECT count(*) FROM license_record l WHERE l.asset_id = a.asset_id) AS license_count
FROM asset a
JOIN app_user u ON u.user_id = a.owner_id
LEFT JOIN LATERAL (
    -- 【LATERAL 子查询】为每一行 asset 执行一次"取最新待审记录"
    SELECT audit_id, created_at, similarity_json
    FROM audit_record
    WHERE asset_id = a.asset_id AND state = 'pending'
    ORDER BY created_at DESC
    LIMIT 1
) ar ON TRUE
WHERE a.audit_status = 'pending';

COMMENT ON VIEW v_asset_pending_audit IS '专家工作台数据源：待终审资产 + AI 建议 + top1 相似母题';


DROP VIEW IF EXISTS v_asset_trace_chain;
-- -----------------------------------------------------------------------------
--  C2. v_asset_trace_chain 溯源链路
--  一行 = 一个事件节点，event_type 区分三类：
--    root        资产登记（确权起点，file_hash 固定证据）
--    audit       权属审核决策
--    license     授权流转
--    system_log  数据库层自动审计快照（触发器产物）
--  按 asset_id + 时间排序即为完整链条
-- -----------------------------------------------------------------------------
CREATE VIEW v_asset_trace_chain AS
-- 节点 1：资产登记
SELECT
    a.asset_id,
    1::smallint                                        AS seq_no,
    a.created_at                                       AS event_at,
    'root'::varchar(16)                                AS event_type,
    ('资产登记：' || a.title)::text                     AS summary,
    u.display_name                                     AS actor_name,
    u.role::text                                       AS actor_role,
    jsonb_build_object(
        'asset_id',   a.asset_id,
        'title',      a.title,
        'file_hash',  a.file_hash,
        'file_path',  a.file_path,
        'file_size',  a.file_size,
        'owner',      u.display_name,
        'motif_id',   a.motif_id,
        'right_level',a.right_level
    )                                                  AS detail
FROM asset a
JOIN app_user u ON u.user_id = a.owner_id

UNION ALL

-- 节点 2：审核决策
SELECT
    ar.asset_id,
    2::smallint,
    COALESCE(ar.decided_at, ar.created_at),
    'audit'::varchar(16),
    (CASE ar.state
        WHEN 'pending'       THEN '提交 AI 预审（待专家终审）'
        WHEN 'expert_review' THEN '专家领取审核'
        WHEN 'approved'      THEN '专家终审通过'
        WHEN 'rejected'      THEN '专家终审驳回'
     END)::text,
    COALESCE(e.display_name, '系统(AI 特征引擎)'),
    COALESCE(e.role::text, 'system'),
    jsonb_build_object(
        'audit_id',        ar.audit_id,
        'state',           ar.state,
        'ai_suggest_level',ar.ai_suggest_level,
        'ai_confidence',   ar.ai_confidence,
        'expert_level',    ar.expert_level,
        'is_override',     ar.is_override,
        'opinion',         ar.opinion,
        'similarity_json', ar.similarity_json
    )
FROM audit_record ar
LEFT JOIN app_user e ON e.user_id = ar.expert_id

UNION ALL

-- 节点 3：授权流转
SELECT
    l.asset_id,
    3::smallint,
    l.created_at,
    'license'::varchar(16),
    (CASE WHEN l.license_type = 'exclusive' THEN '独占授权' ELSE '授权流转' END
        || ' → ' || COALESCE(lu.display_name, l.licensee_name, '未知被授权方'))::text,
    lo.display_name,
    lo.role::text,
    jsonb_build_object(
        'license_id',      l.license_id,
        'license_type',    l.license_type,
        'licensee',        COALESCE(lu.display_name, l.licensee_name),
        'scope',           l.scope,
        'territory',       l.territory,
        'start_date',      l.start_date,
        'end_date',        l.end_date,
        'fee_cny',         l.fee_cny,
        'status',          l.status,
        'contract_hash',   l.contract_hash,
        'prev_license_id', l.prev_license_id
    )
FROM license_record l
JOIN app_user lo ON lo.user_id = l.licensor_id
LEFT JOIN app_user lu ON lu.user_id = l.licensee_user_id

UNION ALL

-- 节点 4：数据库层自动审计（触发器写入的快照）
SELECT
    s.record_id,
    4::smallint,
    s.logged_at,
    'system_log'::varchar(16),
    ('DB 自动审计：' || array_to_string(COALESCE(s.changed_fields, ARRAY['-'::text]), ','))::text,
    s.actor,
    'db_trigger'::text,
    jsonb_build_object(
        'log_id',         s.log_id,
        'operation',      s.operation,
        'changed_fields', s.changed_fields,
        'old_snapshot',   s.old_snapshot,
        'new_snapshot',   s.new_snapshot
    )
FROM system_audit_log s
WHERE s.table_name = 'asset';

COMMENT ON VIEW v_asset_trace_chain IS '溯源链路：资产登记 / 审核决策 / 授权流转 / DB 自动审计 四类节点按时间聚合';


DROP VIEW IF EXISTS v_asset_ledger;
-- -----------------------------------------------------------------------------
--  C3. v_asset_ledger 监管台账
--  一资产一行，聚合权属、AI 建议吻合度、授权数量、最近审计时间
--  【数据库原理】多表聚合视图；LEFT JOIN + 子查询聚合避免多表笛卡尔积放大
-- -----------------------------------------------------------------------------
CREATE VIEW v_asset_ledger AS
SELECT
    a.asset_id,
    a.title,
    u.display_name                                          AS owner_name,
    u.org_name                                              AS owner_org,
    a.right_level,
    a.audit_status,
    a.ai_suggest_level,
    a.ai_confidence,
    -- Top-1 相似度：取该资产最近一条审核流水里 similarity_json[0].similarity
    -- WHY 要留在视图里：母题关联页要展示「这件作品是靠多高的相似度挂到这个母题上的」，
    --     而这个值只存在于审核流水里。让前端再去请求一次 /api/audit 会造成 N+1，
    --     索性在台账视图里一次性算出来（一资产一行的语义不变）。
    top1.sim                                                AS top1_similarity,
    (a.ai_suggest_level IS NOT NULL AND a.ai_suggest_level = a.right_level) AS ai_expert_agree,
    m.motif_code,
    m.name                                                  AS motif_name,
    left(a.file_hash, 16)                                   AS file_hash_short,
    a.version_no,
    a.created_at,
    a.updated_at,
    COALESCE(lic.license_cnt, 0)                            AS license_cnt,
    lic.active_cnt                                          AS active_license_cnt,
    COALESCE(lic.total_fee, 0)                              AS total_fee_cny,
    (SELECT count(*) FROM audit_record ar WHERE ar.asset_id = a.asset_id)          AS audit_times,
    (SELECT count(*) FROM system_audit_log s
      WHERE s.table_name = 'asset' AND s.record_id = a.asset_id)                   AS db_log_cnt,
    (SELECT max(s.logged_at) FROM system_audit_log s
      WHERE s.table_name = 'asset' AND s.record_id = a.asset_id)                   AS last_log_at,
    (SELECT count(*) FROM asset_genealogy g WHERE g.parent_asset_id = a.asset_id)  AS derivative_cnt
FROM asset a
JOIN app_user u          ON u.user_id = a.owner_id
LEFT JOIN public_motif m ON m.motif_id = a.motif_id
LEFT JOIN LATERAL (
    -- 只取最近一次有相似度快照的审核流水（AI 重跑会覆盖，取最新才反映现状）
    SELECT (ar.similarity_json -> 0 ->> 'similarity')::real AS sim
    FROM audit_record ar
    WHERE ar.asset_id = a.asset_id
      AND ar.similarity_json IS NOT NULL
      AND jsonb_array_length(ar.similarity_json) > 0
    ORDER BY ar.created_at DESC
    LIMIT 1
) top1 ON TRUE
LEFT JOIN LATERAL (
    SELECT
        count(*)                                                        AS license_cnt,
        count(*) FILTER (WHERE l.status = 'active')                     AS active_cnt,
        COALESCE(sum(l.fee_cny) FILTER (WHERE l.status = 'active'), 0)  AS total_fee
    FROM license_record l
    WHERE l.asset_id = a.asset_id
) lic ON TRUE;

COMMENT ON VIEW v_asset_ledger IS '监管台账：一资产一行，汇总权属结论、AI 吻合度、授权规模与审计痕迹';


-- =============================================================================
--  D. 角色与权限（数据库层 GRANT）  ★对应"权限管控"要求★
--  【数据库原理】最小权限原则：
--    ich_heritor  只能看自己的资产、只能插入待审资产、不能改终审结论
--    ich_expert   可读全部资产、可写审核流水，但不能改审计日志
--    ich_admin    监管机构：全量只读 + 管理母题库
--    ich_app      应用服务账号(表 owner)，后端以它连接
--  应用层用 SET LOCAL ROLE 切到对应角色，实现"数据库级 + 应用级"双重鉴权
-- =============================================================================
\echo '>>> [14/14] 创建数据库角色与授权'

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ich_heritor') THEN
        CREATE ROLE ich_heritor NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ich_expert') THEN
        CREATE ROLE ich_expert NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ich_admin') THEN
        CREATE ROLE ich_admin NOLOGIN;
    END IF;
END $$;

-- 收回默认的 public 权限，强制显式授权
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO ich_heritor, ich_expert, ich_admin;
GRANT USAGE ON TYPE user_role_enum, right_level_enum, audit_status_enum TO ich_heritor, ich_expert, ich_admin;

-- ---------- 传承人：只能干活在自己的资产上 ----------
GRANT SELECT (user_id, username, display_name, role, org_name, is_active) ON app_user TO ich_heritor;
GRANT SELECT ON public_motif TO ich_heritor;                        -- 母题库公开可查
GRANT SELECT, INSERT ON asset TO ich_heritor;                       -- 可上传
GRANT UPDATE (title, description, file_path, file_hash, file_size, mime_type,
              audit_status, ai_suggest_level, ai_confidence, ai_model_version, motif_id)
    ON asset TO ich_heritor;                                        -- ★ 注意：不含 right_level
GRANT SELECT, INSERT ON feature_vector TO ich_heritor;
GRANT SELECT, INSERT ON audit_record TO ich_heritor;                -- 可提交审核申请
GRANT SELECT ON v_asset_pending_audit, v_asset_trace_chain, v_asset_ledger TO ich_heritor;
GRANT SELECT ON license_record TO ich_heritor;                      -- 可看自己资产的授权
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ich_heritor;

-- ---------- 专家：可读全部，可写审核结论 ----------
GRANT SELECT ON app_user, public_motif, asset, feature_vector, license_record, asset_genealogy TO ich_expert;
GRANT SELECT, INSERT, UPDATE ON audit_record TO ich_expert;
GRANT UPDATE (audit_status, right_level, motif_id) ON asset TO ich_expert;  -- ★ 终审改权属
GRANT SELECT ON v_asset_pending_audit, v_asset_trace_chain, v_asset_ledger, system_audit_log TO ich_expert;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ich_expert;

-- ---------- 监管管理员：全量只读 + 母题库维护 ----------
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ich_admin;
GRANT INSERT, UPDATE, DELETE ON public_motif TO ich_admin;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ich_admin;

-- ---------- 通用限制：审计日志与审核流水任何人都不能改 ----------
-- （触发器已从数据库层强制；这里再去掉表级权限，形成双保险）
REVOKE UPDATE, DELETE, TRUNCATE ON system_audit_log FROM ich_heritor, ich_expert, ich_admin;
REVOKE UPDATE, DELETE, TRUNCATE ON audit_record    FROM ich_heritor, ich_admin;
REVOKE DELETE, TRUNCATE ON license_record          FROM ich_heritor, ich_expert, ich_admin;

\echo '✅ 数据库结构、索引、触发器、视图、角色权限创建完成'
