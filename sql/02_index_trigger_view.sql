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
RETURNS TRIGGER
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_changed TEXT[] := ARRAY[]::TEXT[];
    v_actor   TEXT;
BEGIN
    -- ── 为什么必须是 SECURITY DEFINER ────────────────────────────────────
    -- 这个函数要往 system_audit_log 写日志，而三个业务角色对它只有 SELECT
    -- （UPDATE/DELETE 被 REVOKE，连 INSERT 也没给 —— 审计日志不允许应用直接写）。
    -- plpgsql 触发器函数默认 SECURITY INVOKER，以**调用者**身份执行，
    -- 于是专家终审走到 UPDATE asset 时，触发器内部 INSERT 直接 permission denied，
    -- 整个终审事务回滚。修好 UPDATE 的数组拼接之后才发现这一层：
    -- 「权限收紧」和「触发器代写」在 INVOKER 下是互相打架的。
    -- 改成 DEFINER 后，写日志这件事以定义者（建库账号）身份完成，
    -- 业务角色依然没有任何直接写审计日志的途径 —— 权限模型反而更干净。
    -- SET search_path 是 DEFINER 函数的必要防护，防止调用方篡改解析路径。

    -- ── 为什么 actor 不能直接用 current_user ────────────────────────────
    -- DEFINER 函数内 current_user 会变成函数属主（建库账号），
    -- 「谁改的」就丢了。实测发现 SET LOCAL ROLE 设置的 role GUC 不受影响，
    -- 仍然保留业务角色名，所以优先取它，取不到再退回 current_user。
    v_actor := COALESCE(NULLIF(current_setting('role', true), 'none'), current_user);

    IF TG_OP = 'INSERT' THEN
        INSERT INTO system_audit_log (table_name, record_id, operation, actor, new_snapshot, changed_fields)
        VALUES ('asset', NEW.asset_id, 'INSERT', v_actor, to_jsonb(NEW), NULL);
        RETURN NEW;
    END IF;

    IF TG_OP = 'DELETE' THEN
        INSERT INTO system_audit_log (table_name, record_id, operation, actor, old_snapshot, changed_fields)
        VALUES ('asset', OLD.asset_id, 'DELETE', v_actor, to_jsonb(OLD), NULL);
        RETURN OLD;
    END IF;

    -- UPDATE：逐列比对，收集真实变更列
    --
    -- ⚠️ 这里必须用 array_append，不能写 v_changed || 'right_level'。
    --    v_changed 是 TEXT[]，而 'right_level' 是未知类型字面量；
    --    PostgreSQL 的运算符解析会把它当成 text[] 去匹配「数组 || 数组」重载，
    --    于是在运行时抛 malformed array literal: "right_level"。
    --    后果不是这一行报错，而是**任何 UPDATE asset 都失败** —— 包括专家终审。
    --    这个缺陷只在真实库上暴露，静态解析与代码审查都看不出来。
    IF NEW.right_level  IS DISTINCT FROM OLD.right_level  THEN v_changed := array_append(v_changed, 'right_level');  END IF;
    IF NEW.audit_status IS DISTINCT FROM OLD.audit_status THEN v_changed := array_append(v_changed, 'audit_status'); END IF;
    IF NEW.motif_id     IS DISTINCT FROM OLD.motif_id     THEN v_changed := array_append(v_changed, 'motif_id');     END IF;
    IF NEW.owner_id     IS DISTINCT FROM OLD.owner_id     THEN v_changed := array_append(v_changed, 'owner_id');     END IF;

    -- 只对"权属/状态"类变更留痕；普通元数据更新(标题、描述)不写业务审计日志
    IF array_length(v_changed, 1) IS NULL THEN
        RETURN NEW;
    END IF;

    INSERT INTO system_audit_log (
        table_name, record_id, operation, actor, old_snapshot, new_snapshot, changed_fields
    ) VALUES (
        'asset', NEW.asset_id, 'UPDATE', v_actor,
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
--  B3b. 未终审不得授权（BEFORE INSERT ON license_record）
--
--  这条规则原先只写在 app/services.py 的 create_license() 里（读 asset.audit_status）。
--  应用层校验只能拦住走 API 的请求；任何直连数据库的写入 —— 运维脚本、数据修复、
--  第三方客户端、甚至是一次手抖的 INSERT —— 都能造出「作品还没确权就对外授权」的记录。
--  而 license_record 是追加式的（trg_license_no_delete 禁止删除），写错之后删不掉，
--  只能靠再补一条 revoked 记录来收尾，留下了永久的脏数据窗口。
--
--  所以这条规则必须下沉到数据库层。触发器的判断口径与 services.py 完全一致：
--  只认 asset.audit_status = 'approved'。rejected 同样不放行 —— 被驳回的作品
--  没有取得确权，「终审了」不等于「可以授权」。
--
--  为什么只挂 INSERT、不挂 UPDATE：
--    license_record.status 允许 active -> expired / revoked 的迁移，那是授权的正常
--    收尾动作，不改变「当初这次授权是否合法」。若在 UPDATE 上也检查一遍当前
--    audit_status，一旦资产状态日后发生变化，历史记录的正常收尾会被误伤。
--    要守的是「第一次把授权写进库」这一刻，之后的状态迁移不改变已发生事实的合法性。
--
--  为什么不在这里 SELECT ... FOR UPDATE 锁住 asset：
--    asset 的状态机是 draft -> pending -> approved，且 approved 之后 services.py 与
--    ck_asset_* 约束都不允许回退，所以「读到 approved 之后又被改回 draft」这条竞态
--    在实际数据流里不存在。加锁只会给授权登记引入一次无意义的行锁等待。
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_license_require_approved()
RETURNS TRIGGER AS $$
DECLARE
    v_status audit_status_enum;
BEGIN
    SELECT a.audit_status INTO v_status FROM asset a WHERE a.asset_id = NEW.asset_id;

    -- 资产不存在：外键检查发生在 AFTER 阶段，这里先给出比 23503 更好懂的提示
    IF v_status IS NULL THEN
        RAISE EXCEPTION '资产 % 不存在，无法登记授权', NEW.asset_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF v_status <> 'approved' THEN
        RAISE EXCEPTION '资产 % 尚未终审通过（当前状态 %），不得登记授权',
            NEW.asset_id, v_status
            USING ERRCODE = 'check_violation',
                  HINT = '授权前必须由专家完成终审；此约束在数据库层，绕过 API 同样生效';
    END IF;

    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_license_require_approved ON license_record;
CREATE TRIGGER trg_license_require_approved
    BEFORE INSERT ON license_record
    FOR EACH ROW EXECUTE FUNCTION fn_license_require_approved();


-- -----------------------------------------------------------------------------
--  B3c. 审核状态只能由该改的角色改（BEFORE INSERT OR UPDATE ON asset）  ★核心★
--
--  这里补的是一个只靠列级 GRANT 堵不住的洞。
--  right_level 不给传承人 UPDATE 权限，这一点是对的；但 audit_status **必须**给 ——
--  产线草稿、提交待审都得用它。于是出现了这条路径：
--
--      UPDATE asset SET audit_status = 'approved' WHERE asset_id = ...;
--
--  它不含被禁列，列级权限拦不住，语句成功；资产显示"已确权"，而权级还停在
--  上一次的旧值（新建时是 public）。应用层 services.approve_asset 的状态机、
--  audit_record 的终审流水、system_audit_log 里的专家署名，一个都没经过。
--
--  换言之：只要拿到 ich_heritor 这一个身份，就能绕过整个审核链路给自己定级。
--  列级权限守的是"数据列"，拦不住"业务动作"——业务动作的守卫只能落在触发器上。
--
--  三条规则（与 app/services.py 的状态机口径一致，但不再依赖应用层）：
--
--    ich_heritor  新建只能 draft + public；只能 draft / rejected → pending；
--                 任何情况下不得改 right_level。
--    ich_expert   不得把已终审（approved/rejected）改回未终审；
--                 写终审结论（改成 approved/rejected、或改 right_level）必须在
--                 同一事务里先声明 ich.finalize_gate ∈ {approve_asset, reject_asset}，
--                 也就是必须走 approve_asset() / reject_asset() 那条单事务路径。
--    ich_admin    只读：不得改 audit_status / right_level。
--
--  ── 为什么用 role GUC 判角色，而不是 current_user ──────────────────────
--  后端以建库账号连接、再用 SET LOCAL ROLE 切到业务角色，两者此时一致；但与
--  fn_asset_audit_trigger 保持同一口径（优先 role GUC）比"各自一套"更不容易出错，
--  尤其是将来若把本函数改成 SECURITY DEFINER，也不会因为 current_user 变成属主而失效。
--
--  ── 为什么"不是三个业务角色"就放行 ────────────────────────────────────
--  建库账号 / postgres / 运维脚本 / 种子导入 / 便携库重建走的就是这条路。
--  这不是后门：这些身份本来就持有 SUPERUSER 或表属主权，触发器挡不住也不该挡。
--  闸门守的是**业务角色**这一层，也就是 Web 应用真正握着的那三个身份。
--
--  ── finalize_gate 的边界（如实说明，别把它说成密码）──────────────────
--  它是事务级标记，不是口令：知道这个名字的人仍然可以自己 SET LOCAL。
--  它解决的不是"防不住专家"（专家本来就有改结论的权限），而是"终审只有一条路"：
--    · 任何一次误写的 UPDATE（运维脚本、数据修复、手抖）立刻失败，报错直接指明该走哪条路；
--    · 传承人冒充专家改状态，由角色分支本身拦死，与标记无关。
--  真正不可绕过的是角色判断，标记只是把"专家"这一支收窄到单一路径。
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_asset_status_guard()
RETURNS TRIGGER AS $$
DECLARE
    v_db_role   TEXT := COALESCE(NULLIF(current_setting('role', true), 'none'), current_user);
    v_gate      TEXT := current_setting('ich.finalize_gate', true);
    v_final     CONSTANT TEXT[] := ARRAY['approved', 'rejected'];
    v_gate_ops  CONSTANT TEXT[] := ARRAY['approve_asset', 'reject_asset'];
BEGIN
    -- 非业务角色（建库账号、运维、种子）：不受本闸门约束
    IF v_db_role NOT IN ('ich_heritor', 'ich_expert', 'ich_admin') THEN
        RETURN NEW;
    END IF;

    -- ── INSERT：只可能是传承人上传（专家/监管没有 asset 的 INSERT 权限）──
    -- 但权限是另一回事，这里守住"上传不能自带结论"：
    -- 新建行必须是 draft + public，定级只能由专家终审产生。
    IF TG_OP = 'INSERT' THEN
        IF NEW.audit_status <> 'draft' OR NEW.right_level <> 'public' THEN
            RAISE EXCEPTION '新建资产只能是 draft + public（当前 % + %）',
                    NEW.audit_status, NEW.right_level
                USING ERRCODE = 'insufficient_privilege',
                      HINT = '权属结论只能由专家终审产生，上传时不得自带';
        END IF;
        RETURN NEW;
    END IF;

    -- ── 以下均为 UPDATE ────────────────────────────────────────────────
    -- 这段顺序有讲究：不写 OLD 判断而是按角色分派，最终兜底 RETURN NEW 必须是可达的，
    -- 否则把 above 的所有 RETURN NEW 当成"提前放行"会把陌生角色漏过去。

    IF v_db_role = 'ich_heritor' THEN
        IF NEW.right_level IS DISTINCT FROM OLD.right_level THEN
            RAISE EXCEPTION '传承人不得修改权属级别（% → %）', OLD.right_level, NEW.right_level
                USING ERRCODE = 'insufficient_privilege',
                      HINT = '列级 GRANT 已禁止该列；这里是第二道，防的是权限被误放宽';
        END IF;

        -- 允许的迁移只有两种：draft → pending、rejected → pending（驳回后重新提交）。
        -- 状态不变（改标题、写 AI 建议）直接放行。
        IF NEW.audit_status IS DISTINCT FROM OLD.audit_status
           AND NOT (OLD.audit_status IN ('draft', 'rejected') AND NEW.audit_status = 'pending') THEN
            RAISE EXCEPTION '传承人只能把状态从 draft/rejected 推到 pending（% → %）',
                    OLD.audit_status, NEW.audit_status
                USING ERRCODE = 'insufficient_privilege',
                      HINT = 'approved/rejected 是专家终审结论，传承人无权写入';
        END IF;

        RETURN NEW;
    END IF;

    IF v_db_role = 'ich_expert' THEN
        -- 终审是终局：已终审的资产不得回退、不得改判
        IF OLD.audit_status IN ('approved', 'rejected')
           AND NEW.audit_status IS DISTINCT FROM OLD.audit_status THEN
            RAISE EXCEPTION '资产已终审（%），不得改回 %', OLD.audit_status, NEW.audit_status
                USING ERRCODE = 'insufficient_privilege',
                      HINT = '改判只能由权利人重新提交，再走一次完整审核';
        END IF;

        -- 写终审结论必须由 approve_asset()/reject_asset() 在单事务内完成
        --
        -- ⚠️ 必须写成 NEW.audit_status::text = ANY (v_final)，不能省 ::text。
        --    v_final 是 TEXT[]，而 audit_status 是枚举类型，PostgreSQL 不会自动
        --    在「枚举 = ANY(text[])」之间找隐式转换，直接报 42883
        --    operator does not exist: audit_status_enum = text。
        --    后果不是这一条报错，而是**任何**改状态/改权级的 UPDATE 全部失败 ——
        --    包括专家终审。这与审计触发器里 TEXT[] || 字面量那个坑同一类：
        --    数组与枚举相遇处的隐式转换不存在，而静态审查看不出来。
        IF (NEW.audit_status IS DISTINCT FROM OLD.audit_status AND NEW.audit_status::text = ANY (v_final))
           OR NEW.right_level IS DISTINCT FROM OLD.right_level THEN
            IF v_gate IS NULL OR NOT (v_gate = ANY (v_gate_ops)) THEN
                RAISE EXCEPTION '终审结论必须由 approve_asset()/reject_asset() 写入（% → %）',
                        OLD.audit_status, NEW.audit_status
                    USING ERRCODE = 'insufficient_privilege',
                          HINT = '单事务路径会声明 ich.finalize_gate；直连改状态/改权级一律拒绝';
            END IF;
        END IF;

        RETURN NEW;
    END IF;

    -- ── ich_admin：只读 ───────────────────────────────────────────────
    -- 实际上这一支在今天的权限配置下走不到：ich_admin 对 asset 只有 SELECT，
    -- 连 UPDATE 语句都发不出去（42501 permission denied for table asset）。
    -- 保留它是因为"只读"这条规则不该只靠 GRANT 表达 —— GRANT 一旦被人为了
    -- 「让监管能改个备注」放宽，这里还有一道。规则写在该在的地方，比写在
    -- 恰好能生效的地方更重要。
    IF NEW.audit_status IS DISTINCT FROM OLD.audit_status
       OR NEW.right_level IS DISTINCT FROM OLD.right_level THEN
        RAISE EXCEPTION '监管角色对资产权属/状态只读（% / % → % / %）',
                OLD.audit_status, OLD.right_level, NEW.audit_status, NEW.right_level
            USING ERRCODE = 'insufficient_privilege',
                  HINT = '监管方看全量、签终审之外的账；不参与定级';
    END IF;

    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_asset_status_guard ON asset;
CREATE TRIGGER trg_asset_status_guard
    BEFORE INSERT OR UPDATE ON asset
    FOR EACH ROW EXECUTE FUNCTION fn_asset_status_guard();

COMMENT ON TRIGGER trg_asset_status_guard ON asset IS
'审核状态流转的角色闸门：传承人只能提交待审，专家写终审须走 approve_asset()，监管只读';

-- 同是 BEFORE UPDATE 的两个触发器，PostgreSQL 按名称字母序执行：
--   trg_asset_status_guard（判权限）先于 trg_asset_touch（维护 updated_at/version_no）。
-- 顺序不影响结果 —— 前者只读 NEW/OLD，不修改行。


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
            -- 两种模式下都有同名函数 l2_normalize，按参数类型自动选重载：
            --   pgvector 模式 → l2_normalize(vector)   （pgvector 0.7+ 自带）
            --   降级模式     → l2_normalize(NUMERIC[])（sql/00_compat_vector.sql 定义）
            --
            -- 【为什么不用除法】原写法是 base_vector / v_norm，但 PostgreSQL
            -- 没有「数组 ÷ 标量」这个运算符：vector、real[]、NUMERIC[] 都没有。
            -- 两种模式都会报 operator does not exist，只能在真实库上才暴露。
            NEW.base_vector := l2_normalize(NEW.base_vector);
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
-- 重算覆盖需要先删掉同资产同模型的旧向量（见 app/services.py analyze_asset 3.1）。
-- 只给 SELECT/INSERT 的话，第二次分析必然 42501 —— 首次分析能过、重算才炸，
-- 属于最容易被漏测的一类：单次流程永远碰不到 DELETE 那一行。
-- 特征向量是**派生物、可重建**，越权删除他人向量只会降低检索质量，
-- 不会改变权属或审核结论；要按人隔离需 RLS + 每用户会话变量，超出本设计范围。
GRANT DELETE ON feature_vector TO ich_heritor;
GRANT SELECT, INSERT ON audit_record TO ich_heritor;                -- 可提交审核申请
GRANT SELECT ON v_asset_pending_audit, v_asset_trace_chain, v_asset_ledger TO ich_heritor;
-- 权利人本人可登记授权。只给 INSERT 不给 UPDATE/DELETE：
-- 授权是追加式的，撤销 = 新增一条 revoked 记录，历史不可改不可删。
-- ⚠️ 这一行曾经漏掉（只有 SELECT），后果是「登记授权」在真实库上必然 42501 ——
--    而演示模式走内存、e2e 走进程内模拟，全都测不出来。
GRANT INSERT ON license_record TO ich_heritor;
GRANT SELECT ON license_record TO ich_heritor;                      -- 可看自己资产的授权
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ich_heritor;

-- ---------- 专家：可读全部，可写审核结论 ----------
GRANT SELECT ON app_user, public_motif, asset, feature_vector, license_record, asset_genealogy TO ich_expert;
GRANT SELECT, INSERT, UPDATE ON audit_record TO ich_expert;
GRANT UPDATE (audit_status, right_level, motif_id) ON asset TO ich_expert;  -- ★ 终审改权属
GRANT SELECT ON v_asset_pending_audit, v_asset_trace_chain, v_asset_ledger, system_audit_log TO ich_expert;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ich_expert;

-- ---------- 监管管理员：全量只读 + 母题库维护 + 授权代登记 ----------
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ich_admin;
GRANT INSERT, UPDATE, DELETE ON public_motif TO ich_admin;
-- 监管代登记：传承人不便操作时由监管方代为录入授权（仍受 trg_license_require_approved
-- 与 ck_lic_self 约束，代登记不能绕过"已终审"和"不能授权给自己"）
GRANT INSERT ON license_record TO ich_admin;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ich_admin;

-- ---------- 通用限制：审计日志与审核流水任何人都不能改 ----------
-- （触发器已从数据库层强制；这里再去掉表级权限，形成双保险）
REVOKE UPDATE, DELETE, TRUNCATE ON system_audit_log FROM ich_heritor, ich_expert, ich_admin;
REVOKE UPDATE, DELETE, TRUNCATE ON audit_record    FROM ich_heritor, ich_admin;
REVOKE DELETE, TRUNCATE ON license_record          FROM ich_heritor, ich_expert, ich_admin;

\echo '✅ 数据库结构、索引、触发器、视图、角色权限创建完成'
