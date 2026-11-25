-- =============================================================================
--  非遗数字资产分级确权与溯源系统 —— 种子数据 (Part 3)
--  执行：psql -U postgres -d ich_heritage -v ON_ERROR_STOP=1 -f sql/03_seed.sql
--
--  说明：
--   - 本系统样例纹样与传承人数据均为课程设计模拟数据，不代表真实人物或法律意见
--   - 三个示例用户口令统一为 Passw0rd!（演示用，生产环境务必改为随机强口令）
--   - 三个公有母题预置了「占位特征向量」，启动服务后执行
--       python -m app.cli backfill-motif-vectors
--     会用真实的 CV 模型重新提取参考图特征并回填（覆盖占位向量）
-- =============================================================================

\echo '>>> 写入用户'
INSERT INTO app_user (username, display_name, role, password_hash, org_name) VALUES
    ('heritor01', '杨秀英',   'heritor', crypt('Passw0rd!', gen_salt('bf', 10)), '黔东南苗族刺绣传习所'),
    ('expert01',  '周文渊',   'expert',  crypt('Passw0rd!', gen_salt('bf', 10)), '省非物质文化遗产保护中心'),
    ('admin01',   '陈立',     'admin',   crypt('Passw0rd!', gen_salt('bf', 10)), '省文旅厅非遗处')
ON CONFLICT (username) DO NOTHING;

\echo '>>> 写入公有母题（占位向量：各自构造的 512 维方向向量）'
-- 【说明】占位向量用确定性算法生成，保证可重复；真实演示前用 CLI 回填
-- 生成方式：以 motif_code 的种子构造伪随机方向向量，L2 归一化
--
-- 向量字面量写法随模式切换：
--   原生模式：array_agg(...)::vector
--   降级模式：array_agg(...) 直接就是 NUMERIC[]，无需转换
SELECT ich_vector_is_native() AS use_native \gset
\if :use_native
    \set ICH_CAST '::vector'
\else
    \set ICH_CAST ''
\endif

INSERT INTO public_motif (motif_code, name, region, description, reference_img, base_vector, vector_model)
SELECT
    v.code,
    v.name,
    v.region,
    v.descr,
    v.img,
    -- 512 维占位向量：用 generate_series 做确定性伪随机后归一化
    (SELECT array_agg(s ORDER BY i):ICH_CAST
       FROM (
         SELECT i,
                ((sin(i * v.seed) * 43758.5453)::numeric
                 - trunc((sin(i * v.seed) * 43758.5453)::numeric))::float8::numeric AS s
         FROM generate_series(1, 512) AS i
       ) t),
    'placeholder-v0'
FROM (VALUES
    ('MO-001', '蝴蝶纹',   '贵州黔东南', '苗族刺绣核心母题，象征繁衍与族群认同，属公有传统纹样', 'motifs/motif_butterfly.jpg', 1.0),
    ('MO-002', '缠枝莲纹', '江苏苏州',   '汉族传统连续纹样，明清织物与瓷器常见，公有领域',     'motifs/motif_lotus.jpg',     2.0),
    ('MO-003', '回纹',     '河南洛阳',   '几何折线连续纹，源自青铜器，最古老的公有装饰母题之一', 'motifs/motif_huiwen.jpg',    3.0)
) AS v(code, name, region, descr, img, seed)
ON CONFLICT (motif_code) DO NOTHING;

\echo '>>> 占位向量做 L2 归一化（检索前必须归一化，否则 <=> 不是纯 cosine）'
-- 原生模式用 pgvector 的 l2_normalize；降级模式用兼容层提供的同名函数
UPDATE public_motif
   SET base_vector = l2_normalize(base_vector)
 WHERE base_vector IS NOT NULL;

\echo '>>> 校验'
SELECT user_id, username, display_name, role, org_name FROM app_user ORDER BY user_id;
SELECT motif_id, motif_code, name, region, vector_dims(base_vector) AS dim
FROM public_motif ORDER BY motif_id;
