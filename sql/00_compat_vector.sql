-- =============================================================================
--  兼容层：在没有 pgvector 的 PostgreSQL 上也能跑
--  执行：psql -d ich_heritage -v ON_ERROR_STOP=1 -f sql/00_compat_vector.sql
--  由 sql/01_init.sql 自动 \i 引入，通常不需要单独执行。
--
--  【为什么需要这一层】
--  pgvector 在 Windows 上没有官方预编译包，需要 MSVC 自行编译。
--  很多评审/演示机器装不上它，于是整个系统卡在第一步无法启动。
--
--  【策略】
--    - 装了 pgvector → 什么都不做，走原生 VECTOR(512) + HNSW 索引（推荐，生产路径）
--    - 没装 pgvector → 用 NUMERIC[] 存 512 维向量，并定义语义**完全等价**的
--                      `<=>` 运算符（cosine 距离）与 l2_normalize / vector_dims 函数，
--                      使上层 SQL 一行都不用改。
--    - 两种情况都由同一个开关控制：ich_vector_is_native()
--
--  【必须讲清楚的代价】
--    降级模式下**没有 HNSW 索引**，检索退化为逐行计算距离的精确扫描（seq scan）。
--    对 3~几万条母题无感，但十万级以上会明显变慢。
--    所以降级模式适合「把功能跑通、把界面看到」，不适合做性能验证。
--    真正的性能结论必须在 pgvector + HNSW 下得出 —— 这一点不要含糊。
-- =============================================================================

\echo '>>> [0] 检测 pgvector 可用性'

-- 探测：能成功 CREATE EXTENSION vector 就是原生可用
DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS vector;
        RAISE NOTICE '  ✓ 检测到 pgvector —— 使用原生 VECTOR(512) + HNSW 索引';
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE '  ⚠ 未检测到 pgvector —— 降级为 NUMERIC[] + 自定义 <=> 运算符';
        RAISE NOTICE '    检索将走精确扫描（无 HNSW），功能等价但大数据量下较慢';
    END;
END $$;


-- -----------------------------------------------------------------------------
--  开关函数：供后续 SQL / 应用层判断当前走哪条路
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ich_vector_is_native() RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM pg_extension WHERE extname = 'vector'
    ) AND EXISTS (
        SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'vector' AND n.nspname = 'public'
    )
$$;
COMMENT ON FUNCTION ich_vector_is_native() IS
    'TRUE=已装 pgvector（原生向量+HNSW）；FALSE=降级模式（NUMERIC[]+精确扫描）';


-- -----------------------------------------------------------------------------
--  降级模式才需要的对象：cosine 距离运算符 <=> 、L2 归一化、维度校验
--  注意：全部包在 IF NOT ich_vector_is_native() 里，装了 pgvector 就不会执行，
--        避免与 pgvector 自带的同名运算符冲突。
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF ich_vector_is_native() THEN
        RETURN;          -- 原生模式：直接用 pgvector 的运算符，这里什么都不建
    END IF;

    -- cosine 距离 = 1 - (A·B)/(|A||B|)
    -- 与 pgvector 的 <=> 语义一致：0 表示完全同向，2 表示完全反向
    CREATE OR REPLACE FUNCTION ich_cosine_distance(a NUMERIC[], b NUMERIC[])
    RETURNS DOUBLE PRECISION
    LANGUAGE plpgsql IMMUTABLE STRICT AS $fn$
    DECLARE
        dot   DOUBLE PRECISION := 0;
        na    DOUBLE PRECISION := 0;
        nb    DOUBLE PRECISION := 0;
        i     INT;
    BEGIN
        IF array_length(a, 1) IS DISTINCT FROM array_length(b, 1) THEN
            RAISE EXCEPTION '向量维度不一致：% vs %', array_length(a,1), array_length(b,1);
        END IF;
        FOR i IN 1 .. array_length(a, 1) LOOP
            dot := dot + a[i] * b[i];
            na  := na  + a[i] * a[i];
            nb  := nb  + b[i] * b[i];
        END LOOP;
        IF na = 0 OR nb = 0 THEN
            RETURN 1;                     -- 零向量：定义为最大距离，避免除零
        END IF;
        RETURN 1.0 - (dot / (sqrt(na) * sqrt(nb)));
    END $fn$;

    -- L2 归一化（pgvector 0.7+ 的 l2_normalize 等价物）
    CREATE OR REPLACE FUNCTION l2_normalize(v NUMERIC[])
    RETURNS NUMERIC[]
    LANGUAGE plpgsql IMMUTABLE STRICT AS $fn$
    DECLARE
        n DOUBLE PRECISION := 0;
        i INT;
        out_arr NUMERIC[] := '{}';
    BEGIN
        FOR i IN 1 .. array_length(v, 1) LOOP
            n := n + v[i] * v[i];
        END LOOP;
        n := sqrt(n);
        IF n = 0 THEN
            RETURN v;
        END IF;
        FOR i IN 1 .. array_length(v, 1) LOOP
            -- 必须先转 NUMERIC 再 ROUND：v[i] 是 NUMERIC、n 是 DOUBLE PRECISION，
            -- 相除得到 double precision，而 PostgreSQL 没有 ROUND(double, int) 这个重载
            -- （只有 ROUND(numeric, int) 与 ROUND(double)），不转就报
            -- function round(double precision, integer) does not exist
            out_arr := out_arr || ROUND((v[i] / n)::NUMERIC, 8);
        END LOOP;
        RETURN out_arr;
    END $fn$;

    -- 维度校验（pgvector 的 vector_dims 等价物）
    -- 注意：这里的美元引用标签必须用 fn，不能用默认的空标签。
    --       本函数嵌在外层 DO 块里，而美元引用不能嵌套——内层用空标签
    --       会被当成外层块的结束符，导致整个文件解析失败（已实际踩到）。
    CREATE OR REPLACE FUNCTION vector_dims(v NUMERIC[])
    RETURNS INT LANGUAGE sql IMMUTABLE STRICT AS $fn$
        SELECT COALESCE(array_length(v, 1), 0)
    $fn$;

    -- 文本 → 向量：接受 '[0.1,0.2,...]' 或 '{0.1,0.2,...}' 两种写法，
    -- 这样应用层传参格式与 pgvector 模式完全一致
    CREATE OR REPLACE FUNCTION ich_to_vector(txt TEXT)
    RETURNS NUMERIC[]
    LANGUAGE plpgsql IMMUTABLE STRICT AS $fn$
    DECLARE
        cleaned TEXT;
    BEGIN
        cleaned := btrim(txt);
        cleaned := ltrim(cleaned, '[');
        cleaned := rtrim(cleaned, ']');
        RETURN string_to_array(cleaned, ',')::NUMERIC[];
    END $fn$;

    -- 核心：<=> 运算符（cosine 距离），让现有 SQL 一字不改
    CREATE OPERATOR <=> (
        LEFTARG   = NUMERIC[],
        RIGHTARG  = NUMERIC[],
        FUNCTION  = ich_cosine_distance,
        COMMUTATOR = <=>
    );

    RAISE NOTICE '  ✓ 降级向量层已就绪：<=> / l2_normalize / vector_dims / ich_to_vector';
END $$;
