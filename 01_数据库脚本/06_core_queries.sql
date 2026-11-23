/* =====================================================================================
   地方非遗文化数字化保护数据库管理系统  ——  核心功能 SQL 脚本
   -------------------------------------------------------------------------------------
   文件     : 06_core_queries.sql
   内容     : 多表联查、分类查询、统计查询、子查询、窗口函数、递归 CTE、
              数据归档、触发器与约束验证、索引效率验证
   说明     : 本文件为"SQL 能力演示集"，可直接逐段执行并对照输出结果；
              第 H 节为预期失败的反例语句，建议逐条单独执行（或使用 mysql --force）。
   前置条件 : 已完成 01~05 全部脚本
   ===================================================================================== */

USE heritage_db;
SET NAMES utf8mb4;

/* =====================================================================================
   A 部分  多表联查（业务核心查询）
   ===================================================================================== */

/* A-1 【五表联查】非遗项目全景台账：项目 + 门类 + 传承人 + 作品 ==================== */
SELECT p.project_code                              AS 项目编号,
       p.project_name                              AS 项目名称,
       CONCAT(IFNULL(pc.category_name, '—'), ' / ', c.category_name) AS 门类路径,
       p.project_level                             AS 级别,
       p.declared_area                             AS 申报地区,
       p.protect_unit                              AS 保护单位,
       COUNT(DISTINCT pi.inheritor_id)             AS 传承人数,
       COUNT(DISTINCT w.work_id)                   AS 作品数,
       p.project_status                            AS 项目状态
  FROM heritage_project  p
  JOIN heritage_category c  ON c.category_id  = p.category_id
  LEFT JOIN heritage_category pc ON pc.category_id = c.parent_id
  LEFT JOIN project_inheritor pi ON pi.project_id = p.project_id
  LEFT JOIN heritage_work     w  ON w.project_id  = p.project_id
 GROUP BY p.project_id, p.project_code, p.project_name, pc.category_name,
          c.category_name, p.project_level, p.declared_area, p.protect_unit, p.project_status
 ORDER BY p.project_level, p.project_code;

/* A-2 【七表联查】学生研学全链路档案：学生 → 报名 → 活动 → 课程 → 项目 → 门类 → 传承人 */
SELECT s.student_no        AS 学号,
       s.student_name      AS 学生姓名,
       s.school_name       AS 学校,
       a.activity_name     AS 活动名称,
       DATE(a.start_time)  AS 活动日期,
       cs.course_name      AS 研学课程,
       p.project_name      AS 关联非遗项目,
       c.category_name     AS 非遗门类,
       th.inheritor_name   AS 主讲传承人,
       r.register_status   AS 报名状态,
       r.attendance_status AS 出勤状态,
       IFNULL(r.score, 0)  AS 实践成绩
  FROM activity_registration r
  JOIN student           s  ON s.student_id   = r.student_id
  JOIN research_activity a  ON a.activity_id  = r.activity_id
  JOIN research_course   cs ON cs.course_id   = a.course_id
  JOIN heritage_project  p  ON p.project_id   = cs.project_id
  JOIN heritage_category c  ON c.category_id  = p.category_id
  JOIN inheritor         th ON th.inheritor_id = cs.teacher_id
 WHERE s.student_id = 1
 ORDER BY a.start_time;

/* A-3 【左连接反查】尚未开设任何研学课程的非遗项目（数字化传播短板） */
SELECT p.project_code  AS 项目编号,
       p.project_name  AS 项目名称,
       p.project_level AS 级别,
       p.project_status AS 项目状态
  FROM heritage_project p
  LEFT JOIN research_course rc ON rc.project_id = p.project_id
 WHERE rc.course_id IS NULL
 ORDER BY p.project_level, p.project_code;

/* A-4 【多对多展开】跨项目传承的传承人及其全部传承项目（一人多项目） */
SELECT i.inheritor_code AS 传承人编号,
       i.inheritor_name AS 姓名,
       i.inheritor_level AS 级别,
       COUNT(DISTINCT pi.project_id) AS 关联项目数,
       GROUP_CONCAT(DISTINCT p.project_name ORDER BY p.project_code SEPARATOR '、') AS 传承项目,
       GROUP_CONCAT(DISTINCT pi.relation_role ORDER BY pi.relation_role SEPARATOR '/') AS 项目角色
  FROM inheritor i
  JOIN project_inheritor pi ON pi.inheritor_id = i.inheritor_id
  JOIN heritage_project  p  ON p.project_id    = pi.project_id
 GROUP BY i.inheritor_id, i.inheritor_code, i.inheritor_name, i.inheritor_level
HAVING COUNT(DISTINCT pi.project_id) >= 2
 ORDER BY 关联项目数 DESC, i.inheritor_code;

/* =====================================================================================
   B 部分  分类查询（按结构层次 / 条件 / 关键字）
   ===================================================================================== */

/* B-1 【分类下钻】查询"传统技艺"门类及其全部二级门类下的非遗项目 */
SELECT p.project_code AS 项目编号, p.project_name AS 项目名称,
       c.category_name AS 所属门类, p.project_level AS 级别
  FROM heritage_project p
  JOIN heritage_category c ON c.category_id = p.category_id
 WHERE p.category_id = 8
    OR p.category_id IN (SELECT category_id FROM heritage_category WHERE parent_id = 8)
 ORDER BY p.project_level, p.project_code;

/* B-2 【关键字模糊查询】项目名称或简介包含"绍兴"的项目 */
SELECT project_code AS 项目编号, project_name AS 项目名称,
       project_level AS 级别, declared_area AS 申报地区
  FROM heritage_project
 WHERE project_name LIKE '%绍兴%'
    OR project_intro  LIKE '%绍兴%'
 ORDER BY project_code;

/* B-3 【多条件组合筛选】国家级/省级且尚未建立数字化档案的项目 */
SELECT project_code AS 项目编号, project_name AS 项目名称,
       project_level AS 级别, project_status AS 状态,
       IF(is_digitized = 1, '已建档', '待建档') AS 数字化档案
  FROM heritage_project
 WHERE project_level IN ('国家级', '省级')
   AND is_digitized = 0
   AND project_status <> '已归档'
 ORDER BY FIELD(project_level, '国家级', '省级', '市级', '县级'), project_code;

/* B-4 【区间与日期条件】2026 年 10 月至 12 月可报名的研学活动 */
SELECT activity_code AS 活动编号, activity_name AS 活动名称,
       activity_place AS 活动地点, start_time AS 开始时间,
       deadline_time AS 报名截止, plan_capacity AS 计划人数,
       activity_status AS 状态
  FROM research_activity
 WHERE start_time BETWEEN '2026-10-01 00:00:00' AND '2026-12-31 23:59:59'
   AND activity_status = '报名中'
 ORDER BY start_time;

/* B-5 【分页查询】作品清单按归档时间倒序，取第 6~10 条（LIMIT / OFFSET） */
SELECT work_code AS 作品编号, work_name AS 作品名称,
       create_year AS 创作年份, digitize_status AS 数字化进度
  FROM heritage_work
 ORDER BY created_at DESC, work_id DESC
 LIMIT 5 OFFSET 5;

/* =====================================================================================
   C 部分  统计查询（聚合 / 分组 / 交叉汇总）
   ===================================================================================== */

/* C-1 【分组统计 + HAVING】各一级非遗门类的项目数量、传承人数量与作品数量
       注意：项目挂接在二级门类上（如"食品酿造技艺"），因此需把二级门类向上归并到一级门类，
             否则"传统技艺"等门类会被统计为 0。 */
SELECT c.category_name    AS 非遗门类,
       COUNT(DISTINCT p.project_id)    AS 项目数,
       COUNT(DISTINCT pi.inheritor_id) AS 传承人数,
       COUNT(DISTINCT w.work_id)       AS 作品数
  FROM heritage_category c
  LEFT JOIN heritage_project p
         ON p.category_id = c.category_id
         OR p.category_id IN (SELECT category_id
                                FROM heritage_category
                               WHERE parent_id = c.category_id)
  LEFT JOIN project_inheritor pi ON pi.project_id = p.project_id
  LEFT JOIN heritage_work     w  ON w.project_id  = p.project_id
 WHERE c.category_level = 1
 GROUP BY c.category_id, c.category_name
HAVING COUNT(DISTINCT p.project_id) > 0
 ORDER BY 项目数 DESC;

/* C-2 【行转列 / 交叉汇总】各一级门类下不同级别项目数量分布（CASE WHEN 透视） */
SELECT c.category_name AS 非遗门类,
       SUM(CASE WHEN p.project_level = '国家级' THEN 1 ELSE 0 END) AS 国家级,
       SUM(CASE WHEN p.project_level = '省级'   THEN 1 ELSE 0 END) AS 省级,
       SUM(CASE WHEN p.project_level = '市级'   THEN 1 ELSE 0 END) AS 市级,
       SUM(CASE WHEN p.project_level = '县级'   THEN 1 ELSE 0 END) AS 县级,
       COUNT(p.project_id) AS 合计
  FROM heritage_category c
  LEFT JOIN heritage_project p
         ON p.category_id = c.category_id
         OR p.category_id IN (SELECT category_id
                                FROM heritage_category
                               WHERE parent_id = c.category_id)
 WHERE c.category_level = 1
 GROUP BY c.category_id, c.category_name
 ORDER BY 合计 DESC;

/* C-3 【时间维度统计】2025—2026 年研学活动场次与报名人次月度趋势 */
SELECT DATE_FORMAT(a.start_time, '%Y-%m') AS 活动月份,
       COUNT(DISTINCT a.activity_id)      AS 活动场次,
       COUNT(r.registration_id)           AS 报名人次,
       ROUND(COUNT(r.registration_id) / COUNT(DISTINCT a.activity_id), 2) AS 场均人次
  FROM research_activity a
  LEFT JOIN activity_registration r
         ON r.activity_id = a.activity_id
        AND r.register_status IN ('已报名', '已录取')
 GROUP BY DATE_FORMAT(a.start_time, '%Y-%m')
 ORDER BY 活动月份;

/* C-4 【条件聚合】非遗数字化保护进度总览
       注意：项目与作品是一对多关系，统计项目维度指标时必须使用
             COUNT(DISTINCT CASE WHEN ... THEN p.project_id END)，否则会被作品行放大。 */
SELECT COUNT(DISTINCT p.project_id) AS 项目总数,
       COUNT(DISTINCT CASE WHEN p.is_digitized = 1 THEN p.project_id END) AS 已建数字化档案项目,
       ROUND(COUNT(DISTINCT CASE WHEN p.is_digitized = 1 THEN p.project_id END)
             / COUNT(DISTINCT p.project_id) * 100, 1) AS 建档率,
       COUNT(w.work_id) AS 作品总数,
       SUM(CASE WHEN w.digitize_status = '未数字化' THEN 1 ELSE 0 END) AS 未数字化作品,
       SUM(CASE WHEN w.digitize_status = '已采集'   THEN 1 ELSE 0 END) AS 已采集作品,
       SUM(CASE WHEN w.digitize_status = '已建模'   THEN 1 ELSE 0 END) AS 已建模作品,
       SUM(CASE WHEN w.digitize_status = '已入库'   THEN 1 ELSE 0 END) AS 已入库作品
  FROM heritage_project p
  LEFT JOIN heritage_work w ON w.project_id = p.project_id;

/* C-5 【学生参与度排行】研学参与次数、总学分表现与平均成绩 */
SELECT s.student_no                     AS 学号,
       s.student_name                   AS 学生姓名,
       s.school_name                    AS 学校,
       COUNT(r.registration_id)         AS 报名总次数,
       SUM(CASE WHEN r.attendance_status = '已签到' THEN 1 ELSE 0 END) AS 实际参与次数,
       ROUND(AVG(r.score), 2)           AS 平均成绩,
       MAX(r.score)                     AS 最高成绩
  FROM student s
  LEFT JOIN activity_registration r ON r.student_id = s.student_id
 WHERE s.student_id IN (SELECT student_id FROM activity_registration)
 GROUP BY s.student_id, s.student_no, s.student_name, s.school_name
 ORDER BY 实际参与次数 DESC, 平均成绩 DESC;

/* C-6 【传承人结构分析】按级别统计人数与平均年龄（活态传承梯队评估） */
SELECT inheritor_level                  AS 传承人级别,
       COUNT(*)                         AS 人数,
       AVG(fn_inheritor_age(birth_date)) AS 平均年龄,
       MIN(fn_inheritor_age(birth_date)) AS 最小年龄,
       MAX(fn_inheritor_age(birth_date)) AS 最大年龄,
       SUM(CASE WHEN health_status = '已故' THEN 1 ELSE 0 END) AS 已故人数
  FROM inheritor
 GROUP BY inheritor_level
 ORDER BY FIELD(inheritor_level, '国家级', '省级', '市级', '县级');

/* =====================================================================================
   D 部分  子查询（标量 / 相关 / 存在性）
   ===================================================================================== */

/* D-1 【相关子查询】报名人次高于全体活动平均报名人次的研学活动 */
SELECT e.activity_code  AS 活动编号,
       e.activity_name  AS 活动名称,
       e.confirmed_total AS 报名人数,
       (SELECT ROUND(AVG(confirmed_total), 2) FROM v_activity_enrollment) AS 全体场均人数
  FROM v_activity_enrollment e
 WHERE e.confirmed_total > (SELECT AVG(confirmed_total) FROM v_activity_enrollment)
 ORDER BY e.confirmed_total DESC;

/* D-2 【NOT EXISTS】从未参加过任何研学活动的学生（研学推广重点对象） */
SELECT s.student_no   AS 学号,
       s.student_name AS 学生姓名,
       s.school_name  AS 学校,
       ic.category_name AS 兴趣门类
  FROM student s
  LEFT JOIN heritage_category ic ON ic.category_id = s.interest_category_id
 WHERE NOT EXISTS (SELECT 1 FROM activity_registration r WHERE r.student_id = s.student_id);

/* D-3 【标量子查询取极值】关联传承人数量最多的非遗项目 */
SELECT p.project_code AS 项目编号,
       p.project_name AS 项目名称,
       fn_project_inheritor_count(p.project_id) AS 传承人数
  FROM heritage_project p
 WHERE fn_project_inheritor_count(p.project_id) =
       (SELECT MAX(cnt) FROM (SELECT COUNT(*) AS cnt FROM project_inheritor GROUP BY project_id) t);

/* D-4 【EXISTS + GROUP BY / HAVING】参加过 3 场及以上研学活动的活跃学生 */
SELECT s.student_no   AS 学号,
       s.student_name AS 学生姓名,
       COUNT(*)       AS 参与场次
  FROM student s
  JOIN activity_registration r ON r.student_id = s.student_id
 WHERE EXISTS (SELECT 1 FROM activity_registration x
                WHERE x.student_id = s.student_id AND x.score IS NOT NULL)
 GROUP BY s.student_id, s.student_no, s.student_name
HAVING COUNT(*) >= 3
 ORDER BY 参与场次 DESC;

/* =====================================================================================
   E 部分  窗口函数（排名 / 分区 / 累计 / 环比）
   ===================================================================================== */

/* E-1 【三种排名函数对比】非遗项目传播热度排行 */
SELECT DENSE_RANK() OVER (ORDER BY heat_score DESC) AS 密集排名,
       RANK()       OVER (ORDER BY heat_score DESC) AS 跳跃排名,
       ROW_NUMBER() OVER (ORDER BY heat_score DESC, project_id) AS 行号,
       project_code   AS 项目编号,
       project_name   AS 项目名称,
       project_level  AS 级别,
       heat_score     AS 热度得分,
       registration_count AS 累计报名人次
  FROM v_project_heat_index
 ORDER BY heat_score DESC
 LIMIT 10;

/* E-2 【PARTITION BY】各一级非遗门类内部的项目热度排名（门类内榜）
       门类统一按"一级门类"归并，避免二级门类把同一大门类拆散。 */
SELECT root_category_name AS 非遗门类,
       project_name  AS 项目名称,
       project_level AS 级别,
       heat_score    AS 热度得分,
       DENSE_RANK() OVER (PARTITION BY root_category_name ORDER BY heat_score DESC) AS 门类内排名,
       ROUND(heat_score - AVG(heat_score) OVER (PARTITION BY root_category_name), 2) AS 高于门类均值
  FROM v_project_heat_index
 ORDER BY 非遗门类, 门类内排名;

/* E-3 【分区排名】单个活动内学生的成绩排名与及格情况 */
SELECT a.activity_name AS 活动名称,
       s.student_name  AS 学生姓名,
       r.score         AS 成绩,
       RANK() OVER (PARTITION BY a.activity_id ORDER BY r.score DESC) AS 活动内排名,
       ROUND(AVG(r.score) OVER (PARTITION BY a.activity_id), 2)       AS 活动平均分,
       CASE WHEN r.score >= 90 THEN '优秀'
            WHEN r.score >= 80 THEN '良好'
            WHEN r.score >= 60 THEN '合格'
            ELSE '待提升' END AS 等级
  FROM activity_registration r
  JOIN student           s ON s.student_id  = r.student_id
  JOIN research_activity a ON a.activity_id = r.activity_id
 WHERE r.activity_id = 9
 ORDER BY r.score DESC;

/* E-4 【累计与环比】各月活动场次、报名人次、累计报名人次及环比变化 */
SELECT DATE_FORMAT(a.start_time, '%Y-%m') AS 活动月份,
       COUNT(DISTINCT a.activity_id)      AS 活动场次,
       COUNT(r.registration_id)           AS 报名人次,
       SUM(COUNT(r.registration_id)) OVER (ORDER BY DATE_FORMAT(a.start_time, '%Y-%m')) AS 累计报名人次,
       LAG(COUNT(r.registration_id)) OVER (ORDER BY DATE_FORMAT(a.start_time, '%Y-%m')) AS 上月报名人次,
       COUNT(r.registration_id)
       - IFNULL(LAG(COUNT(r.registration_id)) OVER (ORDER BY DATE_FORMAT(a.start_time, '%Y-%m')), 0) AS 环比增减
  FROM research_activity a
  LEFT JOIN activity_registration r
         ON r.activity_id = a.activity_id
        AND r.register_status IN ('已报名', '已录取')
 GROUP BY DATE_FORMAT(a.start_time, '%Y-%m')
 ORDER BY 活动月份;

/* E-5 【窗口聚合】各一级门类项目的平均热度与全体均值对比（识别薄弱门类） */
SELECT root_category_name AS 非遗门类,
       COUNT(*)      AS 项目数,
       ROUND(AVG(heat_score), 2) AS 门类平均热度,
       ROUND(AVG(AVG(heat_score)) OVER (), 2) AS 全体平均热度,
       ROUND(AVG(heat_score) - AVG(AVG(heat_score)) OVER (), 2) AS 差值
  FROM v_project_heat_index
 GROUP BY root_category_name
 ORDER BY 门类平均热度 DESC;

/* =====================================================================================
   F 部分  公用表表达式 CTE（含递归 CTE）
   ===================================================================================== */

/* F-1 【基础 CTE】用 WITH 简化"先聚合再连接"的查询 */
WITH project_stat AS (
    SELECT p.project_id,
           COUNT(DISTINCT w.work_id)             AS work_cnt,
           SUM(CASE WHEN w.digitize_status = '已入库' THEN 1 ELSE 0 END) AS stored_cnt
      FROM heritage_project p
      LEFT JOIN heritage_work w ON w.project_id = p.project_id
     GROUP BY p.project_id
)
SELECT p.project_code AS 项目编号,
       p.project_name AS 项目名称,
       s.work_cnt     AS 作品数,
       s.stored_cnt   AS 已入库数字化作品,
       CASE WHEN s.work_cnt = 0 THEN '无作品档案'
            WHEN s.stored_cnt = s.work_cnt THEN '数字化完成'
            WHEN s.stored_cnt > 0 THEN '数字化进行中'
            ELSE '数字化未启动' END AS 数字化状态判定
  FROM heritage_project p
  JOIN project_stat s ON s.project_id = p.project_id
 ORDER BY s.work_cnt DESC, p.project_code;

/* F-2 【递归 CTE】非遗分类树自顶向下展开，并输出层级路径 */
WITH RECURSIVE category_tree AS (
    SELECT category_id, category_code, category_name, category_level, parent_id,
           CAST(category_name AS CHAR(200)) AS full_path,
           1 AS depth
      FROM heritage_category
     WHERE parent_id IS NULL
    UNION ALL
    SELECT c.category_id, c.category_code, c.category_name, c.category_level, c.parent_id,
           CONCAT(ct.full_path, ' > ', c.category_name),
           ct.depth + 1
      FROM heritage_category c
      JOIN category_tree ct ON ct.category_id = c.parent_id
)
SELECT depth               AS 层级,
       full_path           AS 分类路径,
       category_code       AS 分类编码,
       (SELECT COUNT(*) FROM heritage_project p WHERE p.category_id = t.category_id) AS 直属项目数
  FROM category_tree t
 ORDER BY full_path;

/* F-3 【递归 CTE + 上卷统计】把二级门类项目数上卷汇总到一级门类
       注意：项目与传承人/作品/报名均为一对多关系，若直接多表连接会产生笛卡尔放大，
             因此先用 project_stats 把每个项目的指标聚合为一行，再按门类上卷求和。 */
WITH RECURSIVE category_tree AS (
    SELECT category_id, category_id AS root_id
      FROM heritage_category
     WHERE parent_id IS NULL
    UNION ALL
    SELECT c.category_id, ct.root_id
      FROM heritage_category c
      JOIN category_tree ct ON ct.category_id = c.parent_id
),
project_stats AS (
    SELECT p.project_id,
           p.category_id,
           (SELECT COUNT(*) FROM project_inheritor pi WHERE pi.project_id = p.project_id) AS inheritor_cnt,
           (SELECT COUNT(*) FROM heritage_work w    WHERE w.project_id  = p.project_id) AS work_cnt,
           (SELECT COUNT(*) FROM activity_registration ar
              JOIN research_activity ra ON ra.activity_id = ar.activity_id
              JOIN research_course   rc ON rc.course_id  = ra.course_id
             WHERE rc.project_id = p.project_id
               AND ar.register_status IN ('已报名', '已录取'))                           AS registration_cnt
      FROM heritage_project p
)
SELECT root.category_name              AS 一级门类,
       COUNT(DISTINCT ct.category_id)  AS 覆盖门类数,
       COUNT(DISTINCT ps.project_id)   AS 项目总数,
       COALESCE(SUM(ps.inheritor_cnt), 0)    AS 传承人关联数,
       COALESCE(SUM(ps.work_cnt), 0)         AS 作品数,
       COALESCE(SUM(ps.registration_cnt), 0) AS 累计报名人次
  FROM category_tree ct
  JOIN heritage_category root ON root.category_id = ct.root_id
  LEFT JOIN project_stats ps  ON ps.category_id  = ct.category_id
 GROUP BY root.category_id, root.category_name
 ORDER BY 项目总数 DESC, 一级门类;

/* F-4 【多 CTE 组合】项目传播热度与数字化进度的联合健康度评估 */
WITH heat AS (
    SELECT project_id, project_name, heat_score FROM v_project_heat_index
),
digit AS (
    SELECT project_id, total_works, collected_works, collect_rate FROM v_digitization_progress
)
SELECT h.project_name AS 项目名称,
       h.heat_score   AS 热度得分,
       IFNULL(d.total_works, 0)     AS 作品总数,
       IFNULL(d.collect_rate, 0)    AS 采集覆盖率,
       CASE WHEN h.heat_score >= 60 AND IFNULL(d.collect_rate, 0) >= 50 THEN '双优项目'
            WHEN h.heat_score >= 60 THEN '传播强 / 数字化待补'
            WHEN IFNULL(d.collect_rate, 0) >= 50 THEN '数字化好 / 传播待提升'
            ELSE '双弱项目（需重点扶持）' END AS 健康度评估
  FROM heat h
  LEFT JOIN digit d ON d.project_id = h.project_id
 ORDER BY h.heat_score DESC;

/* =====================================================================================
   G 部分  数据归档（档案留存 / 归档候选识别 / 归档执行）
   ===================================================================================== */

/* G-1 查询已归档项目快照（历史档案留存，不做物理删除） */
SELECT project_code   AS 项目编号,
       project_name   AS 项目名称,
       category_name  AS 门类,
       project_level  AS 级别,
       inheritor_count AS 归档时传承人数,
       work_count      AS 归档时作品数,
       activity_count  AS 归档时活动场次,
       archive_reason  AS 归档原因,
       archived_by     AS 归档人,
       archive_time    AS 归档时间
  FROM heritage_project_archive
 ORDER BY archive_time DESC;

/* G-2 归档候选识别：超过 365 天未开展研学活动、且未归档的非遗项目 */
SELECT p.project_code AS 项目编号,
       p.project_name AS 项目名称,
       p.project_status AS 当前状态,
       IFNULL(DATE_FORMAT(MAX(a.start_time), '%Y-%m-%d'), '从未开展') AS 最近研学活动,
       IFNULL(DATEDIFF(CURDATE(), MAX(a.start_time)), 9999) AS 距今天数,
       fn_project_inheritor_count(p.project_id) AS 传承人数
  FROM heritage_project p
  LEFT JOIN research_course   cs ON cs.project_id = p.project_id
  LEFT JOIN research_activity a  ON a.course_id   = cs.course_id
 WHERE p.project_status <> '已归档'
 GROUP BY p.project_id, p.project_code, p.project_name, p.project_status
HAVING MAX(a.start_time) IS NULL OR DATEDIFF(CURDATE(), MAX(a.start_time)) > 365
 ORDER BY 距今天数 DESC;

/* G-3 归档执行：调用存储过程完成"生成快照 + 状态流转 + 日志留痕"的原子操作
       （以项目 11 诸暨西路乱弹为例，如需真实执行请去掉注释）          */
-- CALL sp_archive_project(11, '长期未开展研学活动，转入历史档案留存', 'admin', @archive_msg);
-- SELECT @archive_msg AS 归档结果;
-- SELECT * FROM heritage_project_archive WHERE project_id = 11;
-- SELECT project_status AS 归档后项目状态 FROM heritage_project WHERE project_id = 11;

/* =====================================================================================
   H 部分  完整性约束与触发器验证
   -------------------------------------------------------------------------------------
   注意：以下语句为"预期失败"的反例，用于证明约束与触发器真实生效。
        请逐条单独执行（或用 mysql --force 运行本文件），脚本默认已注释。
   ===================================================================================== */

/* H-1 唯一约束：重复的项目编号 -> 应报 Duplicate entry 'SX-001' */
-- INSERT INTO heritage_project (project_code, project_name, category_id, project_level, declared_area)
-- VALUES ('SX-001', '重复编号测试', 4, '省级', '浙江省绍兴市');

/* H-2 检查约束：手机号格式非法 -> 应报 ck_inheritor_phone 违反 */
-- INSERT INTO inheritor (inheritor_code, inheritor_name, gender, birth_date, id_card, phone, region, inheritor_level)
-- VALUES ('SXR-999', '测试人员', '男', '1980-01-01', '330602198001019999', '12345', '浙江省绍兴市', '县级');

/* H-3 外键约束：引用不存在的分类 -> 应报外键约束失败 */
-- INSERT INTO heritage_project (project_code, project_name, category_id, project_level, declared_area)
-- VALUES ('SX-999', '外键测试项目', 999, '省级', '浙江省绍兴市');

/* H-4 检查约束：活动结束时间早于开始时间 -> 应报 ck_activity_time 违反 */
-- INSERT INTO research_activity (activity_code, activity_name, course_id, activity_place,
--        start_time, end_time, deadline_time, plan_capacity, organizer)
-- VALUES ('A-TEST-01', '时间校验测试', 1, '测试地点',
--        '2026-10-01 16:00:00', '2026-10-01 09:00:00', '2026-09-25 17:00:00', 10, '测试单位');

/* H-5 触发器 1：向已结束活动报名 -> 应报"该活动已取消或已结束" */
-- INSERT INTO activity_registration (activity_id, student_id, register_status, attendance_status)
-- VALUES (1, 7, '已报名', '未开始');

/* H-6 触发器 1：超过报名截止时间 -> 应报"已超过该活动的报名截止时间" */
-- INSERT INTO activity_registration (activity_id, student_id, register_status, attendance_status)
-- VALUES (13, 3, '已报名', '未开始');

/* H-7 触发器 1：名额已满仍以"已报名"提交 -> 应报"活动名额已满"
       （活动 16 处于"已满员"状态且报名截止时间未到，可独立验证容量校验分支） */
-- INSERT INTO activity_registration (activity_id, student_id, register_status, attendance_status)
-- VALUES (16, 5, '已报名', '未开始');

/* H-8 触发器 6：把计划人数调到低于已报名人数 -> 应报"计划人数不得小于当前已报名人数" */
-- UPDATE research_activity SET plan_capacity = 5 WHERE activity_id = 13;

/* H-9 唯一约束（多对多）：同一学生重复报名同一活动 -> 应报 uk_activity_student 冲突 */
-- INSERT INTO activity_registration (activity_id, student_id, register_status)
-- VALUES (14, 7, '候补');

/* ---------------------------------------------------------------------------
   H-10 正向验证：活态传承预警触发器
   场景：把"新昌调腔"唯一传承人（张大鸿，ID=7）健康状况更新为"已故"，
        其名下项目 5 将自动由"正常"流转为"濒危"。
        以下语句在事务中执行并回滚，不影响基础数据。
   --------------------------------------------------------------------------- */
START TRANSACTION;

UPDATE inheritor SET health_status = '已故' WHERE inheritor_id = 7;

SELECT '【触发前】项目状态' AS 验证步骤, project_code AS 项目编号,
       project_name AS 项目名称, project_status AS 项目状态
  FROM heritage_project WHERE project_id = 5;

SELECT '【触发器日志】' AS 验证步骤, action_desc AS 日志内容, log_time AS 记录时间
  FROM operation_log WHERE table_name = 'inheritor'
 ORDER BY log_id DESC LIMIT 1;

SELECT '【触发后】项目状态（应变为"濒危"）' AS 验证步骤, project_code AS 项目编号,
       project_name AS 项目名称, project_status AS 项目状态
  FROM heritage_project WHERE project_id = 5;

ROLLBACK;

SELECT '【回滚后】项目状态（应恢复为"正常"）' AS 验证步骤, project_code AS 项目编号,
       project_name AS 项目名称, project_status AS 项目状态
  FROM heritage_project WHERE project_id = 5;

/* ---------------------------------------------------------------------------
   H-11 正向验证：作品入库自动标记项目数字化档案
   --------------------------------------------------------------------------- */
SELECT '【插入作品前】' AS 验证步骤, project_code AS 项目编号,
       project_name AS 项目名称, is_digitized AS 是否已建数字化档案
  FROM heritage_project WHERE project_id = 7;

START TRANSACTION;
INSERT INTO heritage_work (work_code, work_name, project_id, creator_id, create_year,
                           material, digitize_status, in_collection)
VALUES ('W-TEST-001', '调腔面具（数字化入库测试件）', 7, 9, 2020, '木胎漆', '已入库', 0);

SELECT '【插入作品后】触发器应自动把项目标记为已建数字化档案' AS 验证步骤,
       project_code AS 项目编号, project_name AS 项目名称, is_digitized AS 是否已建数字化档案
  FROM heritage_project WHERE project_id = 7;
ROLLBACK;

/* =====================================================================================
   I 部分  索引效率验证（EXPLAIN）
   ===================================================================================== */

/* I-1 走索引：按 category_id 查询，命中 idx_project_category */
EXPLAIN SELECT project_id, project_name
  FROM heritage_project
 WHERE category_id = 4;

/* I-2 走索引：按级别 + 状态组合查询，命中复合索引 idx_project_level_status */
EXPLAIN SELECT project_id, project_name
  FROM heritage_project
 WHERE project_level = '国家级'
   AND project_status = '正常';

/* I-3 走索引：多对多中间表按 inheritor_id 反查，命中 idx_pi_inheritor */
EXPLAIN SELECT project_id FROM project_inheritor WHERE inheritor_id = 15;

/* I-4 走索引：报名记录按学生 + 状态查询，命中复合索引 idx_reg_student */
EXPLAIN SELECT registration_id FROM activity_registration
 WHERE student_id = 1 AND register_status = '已录取';

/* I-5 覆盖索引验证：查询列全部包含在索引中，Extra 应出现 Using index */
EXPLAIN SELECT activity_id, student_id FROM activity_registration
 WHERE activity_id = 13;

/* =====================================================================================
   J 部分  项目数据总览（答辩演示首屏查询）
   ===================================================================================== */
SELECT '非遗分类'   AS 数据对象, COUNT(*) AS 记录数 FROM heritage_category
UNION ALL SELECT '非遗项目',     COUNT(*) FROM heritage_project
UNION ALL SELECT '传承人',       COUNT(*) FROM inheritor
UNION ALL SELECT '项目-传承人关系', COUNT(*) FROM project_inheritor
UNION ALL SELECT '非遗作品',     COUNT(*) FROM heritage_work
UNION ALL SELECT '学生',         COUNT(*) FROM student
UNION ALL SELECT '研学课程',     COUNT(*) FROM research_course
UNION ALL SELECT '研学活动',     COUNT(*) FROM research_activity
UNION ALL SELECT '报名记录',     COUNT(*) FROM activity_registration
UNION ALL SELECT '归档快照',     COUNT(*) FROM heritage_project_archive
UNION ALL SELECT '审计日志',     COUNT(*) FROM operation_log;
