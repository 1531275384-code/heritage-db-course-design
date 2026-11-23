/* =====================================================================================
   地方非遗文化数字化保护数据库管理系统  ——  视图定义脚本
   -------------------------------------------------------------------------------------
   文件     : 03_views.sql
   内容     : 7 个业务视图，用于封装复杂多表连接、消除派生数据冗余、支撑前端只读查询
   设计说明 : 本系统所有"统计类派生字段"（如报名人数、报名率、热度指数）一律不入表存储，
              统一由视图实时计算，从根本上杜绝数据冗余与不一致。
   前置条件 : 已完成 01_schema.sql、02_init_data.sql
   ===================================================================================== */

USE heritage_db;

/* ---------------------------------------------------------------------------
   视图 1 : v_project_profile  非遗项目全景档案视图
   作用   : 一站式输出"项目 + 分类 + 传承人规模 + 作品规模 + 课程规模 + 活动规模 + 报名人次"
            解决前端多次分页查询的性能与一致性难题。
   --------------------------------------------------------------------------- */
CREATE OR REPLACE VIEW v_project_profile AS
SELECT p.project_id,
       p.project_code,
       p.project_name,
       c.category_id,
       c.category_name                                     AS category_name,
       pc.category_name                                    AS parent_category_name,
       p.project_level,
       p.batch_no,
       p.declared_area,
       p.protect_unit,
       p.approval_year,
       p.project_status,
       p.is_digitized,
       COALESCE(ih.inheritor_count, 0)                     AS inheritor_count,
       COALESCE(wk.work_count, 0)                          AS work_count,
       COALESCE(wk.digitized_work_count, 0)                AS digitized_work_count,
       COALESCE(cs.course_count, 0)                        AS course_count,
       COALESCE(ac.activity_count, 0)                      AS activity_count,
       COALESCE(rg.registration_count, 0)                  AS registration_count
FROM heritage_project p
JOIN heritage_category c  ON c.category_id = p.category_id
LEFT JOIN heritage_category pc ON pc.category_id = c.parent_id
LEFT JOIN (SELECT project_id, COUNT(*) AS inheritor_count
           FROM project_inheritor GROUP BY project_id) ih
       ON ih.project_id = p.project_id
LEFT JOIN (SELECT project_id,
                  COUNT(*) AS work_count,
                  SUM(CASE WHEN digitize_status = '已入库' THEN 1 ELSE 0 END) AS digitized_work_count
           FROM heritage_work GROUP BY project_id) wk
       ON wk.project_id = p.project_id
LEFT JOIN (SELECT project_id, COUNT(*) AS course_count
           FROM research_course GROUP BY project_id) cs
       ON cs.project_id = p.project_id
LEFT JOIN (SELECT rc.project_id, COUNT(*) AS activity_count
           FROM research_activity ra
           JOIN research_course rc ON rc.course_id = ra.course_id
           GROUP BY rc.project_id) ac
       ON ac.project_id = p.project_id
LEFT JOIN (SELECT rc.project_id, COUNT(*) AS registration_count
           FROM activity_registration ar
           JOIN research_activity ra ON ra.activity_id = ar.activity_id
           JOIN research_course rc    ON rc.course_id  = ra.course_id
           WHERE ar.register_status IN ('已报名', '已录取')
           GROUP BY rc.project_id) rg
       ON rg.project_id = p.project_id;

/* ---------------------------------------------------------------------------
   视图 2 : v_inheritor_heritage  传承人—传承项目展开视图
   作用   : 将 project_inheritor 多对多关系展开为明细，并计算传承人实时年龄，
            支撑"一人多项目""活态传承预警"等业务分析。
   --------------------------------------------------------------------------- */
CREATE OR REPLACE VIEW v_inheritor_heritage AS
SELECT i.inheritor_id,
       i.inheritor_code,
       i.inheritor_name,
       i.gender,
       i.birth_date,
       TIMESTAMPDIFF(YEAR, i.birth_date, CURDATE()) AS age,
       i.ethnicity,
       i.region,
       i.inheritor_level,
       i.health_status,
       i.skill_years,
       pi.relation_role,
       pi.start_year,
       pi.is_master,
       p.project_id,
       p.project_code,
       p.project_name,
       p.project_level                             AS project_level,
       p.project_status,
       c.category_name
FROM inheritor i
JOIN project_inheritor pi ON pi.inheritor_id = i.inheritor_id
JOIN heritage_project  p  ON p.project_id    = pi.project_id
JOIN heritage_category c  ON c.category_id    = p.category_id;

/* ---------------------------------------------------------------------------
   视图 3 : v_activity_enrollment  研学活动报名情况视图
   作用   : 实时计算每个活动的报名人数、确认人数、剩余名额与报名率。
            注意：这些均为派生数据，不落库，避免与 activity_registration 产生冗余不一致。
   --------------------------------------------------------------------------- */
CREATE OR REPLACE VIEW v_activity_enrollment AS
SELECT a.activity_id,
       a.activity_code,
       a.activity_name,
       a.course_id,
       cs.course_name,
       cs.course_type,
       cs.class_hours,
       p.project_id,
       p.project_name,
       p.project_level,
       p.project_status,
       c.category_id,
       c.category_name,
       pc.category_id      AS parent_category_id,
       pc.category_name    AS parent_category_name,
       th.inheritor_id AS teacher_id,
       th.inheritor_name AS teacher_name,
       a.activity_place,
       a.start_time,
       a.end_time,
       a.deadline_time,
       a.plan_capacity,
       a.activity_status,
       a.organizer,
       COALESCE(r.registered_total, 0)                              AS registered_total,
       COALESCE(r.confirmed_total, 0)                               AS confirmed_total,
       COALESCE(r.checkin_total, 0)                                 AS checkin_total,
       GREATEST(a.plan_capacity - COALESCE(r.confirmed_total, 0), 0) AS remaining_quota,
       ROUND(COALESCE(r.confirmed_total, 0) / a.plan_capacity * 100, 1) AS enroll_rate
FROM research_activity a
JOIN research_course  cs ON cs.course_id  = a.course_id
JOIN heritage_project p  ON p.project_id  = cs.project_id
JOIN heritage_category c ON c.category_id = p.category_id
LEFT JOIN heritage_category pc ON pc.category_id = c.parent_id
JOIN inheritor        th ON th.inheritor_id = cs.teacher_id
LEFT JOIN (SELECT activity_id,
                  COUNT(*) AS registered_total,
                  SUM(CASE WHEN register_status IN ('已报名', '已录取') THEN 1 ELSE 0 END) AS confirmed_total,
                  SUM(CASE WHEN attendance_status = '已签到' THEN 1 ELSE 0 END)            AS checkin_total
           FROM activity_registration
           GROUP BY activity_id) r
       ON r.activity_id = a.activity_id;

/* ---------------------------------------------------------------------------
   视图 4 : v_student_learning_record  学生研学学习档案视图
   作用   : 输出"学生—活动—课程—非遗项目—授课传承人—成绩评价"全链路档案，
            是学生查询端与研学档案导出功能的数据基础。
   --------------------------------------------------------------------------- */
CREATE OR REPLACE VIEW v_student_learning_record AS
SELECT s.student_id,
       s.student_no,
       s.student_name,
       s.gender,
       s.grade,
       s.class_name,
       s.school_name,
       ic.category_id      AS interest_category_id,
       ic.category_name    AS interest_category,
       a.activity_id,
       a.activity_code,
       a.activity_name,
       a.start_time,
       a.activity_status,
       cs.course_id,
       cs.course_name,
       cs.course_type,
       p.project_id,
       p.project_name,
       p.project_level,
       c.category_name     AS project_category,
       th.inheritor_name   AS teacher_name,
       r.register_status,
       r.attendance_status,
       r.register_time,
       r.checkin_time,
       r.score,
       r.comment
FROM activity_registration r
JOIN student          s  ON s.student_id  = r.student_id
LEFT JOIN heritage_category ic ON ic.category_id = s.interest_category_id
JOIN research_activity a ON a.activity_id = r.activity_id
JOIN research_course   cs ON cs.course_id = a.course_id
JOIN heritage_project  p  ON p.project_id = cs.project_id
JOIN heritage_category c  ON c.category_id = p.category_id
JOIN inheritor         th ON th.inheritor_id = cs.teacher_id;

/* ---------------------------------------------------------------------------
   视图 5 : v_project_heat_index  非遗项目传播热度指数视图
   作用   : 落地"非遗活态传承热度评估算法"的数据层实现。
            热度指数 = 作品数×2 + 已入库数字化作品数×3 + 活动场次×4 + 报名人次×1.5
                     + 级别权重(国家级20/省级14/市级9/县级5)
                     + 濒危保护加权(濒危额外+12)
            说明：级别权重体现项目影响力，濒危加权体现"抢救性保护优先级"。
   --------------------------------------------------------------------------- */
CREATE OR REPLACE VIEW v_project_heat_index AS
SELECT pp.project_id,
       pp.project_code,
       pp.project_name,
       pp.category_name,
       pp.parent_category_name,
       COALESCE(pp.parent_category_name, pp.category_name) AS root_category_name,
       pp.project_level,
       pp.project_status,
       pp.inheritor_count,
       pp.work_count,
       pp.digitized_work_count,
       pp.activity_count,
       pp.registration_count,
       ROUND(pp.work_count * 2.0
           + pp.digitized_work_count * 3.0
           + pp.activity_count * 4.0
           + pp.registration_count * 1.5
           + CASE pp.project_level
                 WHEN '国家级' THEN 20
                 WHEN '省级'   THEN 14
                 WHEN '市级'   THEN 9
                 ELSE 5
             END
           + CASE pp.project_status WHEN '濒危' THEN 12 ELSE 0 END, 2) AS heat_score
FROM v_project_profile pp;

/* ---------------------------------------------------------------------------
   视图 6 : v_category_hierarchy  分类层次结构视图
   作用   : 把自关联的分类表展平为"一级门类—二级门类"两列，便于前端树形展示与分组统计。
   --------------------------------------------------------------------------- */
CREATE OR REPLACE VIEW v_category_hierarchy AS
SELECT c1.category_id   AS level1_id,
       c1.category_code AS level1_code,
       c1.category_name AS level1_name,
       c2.category_id   AS level2_id,
       c2.category_code AS level2_code,
       c2.category_name AS level2_name
FROM heritage_category c1
LEFT JOIN heritage_category c2 ON c2.parent_id = c1.category_id
WHERE c1.category_level = 1;

/* ---------------------------------------------------------------------------
   视图 7 : v_digitization_progress  非遗数字化保护进度视图
   作用   : 按项目统计作品总量、已采集量、已入库量与采集覆盖率，
            直接呈现"文化数字化战略"落地成效，服务于数字化专项汇报。
   --------------------------------------------------------------------------- */
CREATE OR REPLACE VIEW v_digitization_progress AS
SELECT p.project_id,
       p.project_code,
       p.project_name,
       p.project_level,
       p.project_status,
       c.category_name,
       p.is_digitized                                        AS project_digitized,
       COUNT(w.work_id)                                      AS total_works,
       SUM(CASE WHEN w.digitize_status <> '未数字化' THEN 1 ELSE 0 END) AS collected_works,
       SUM(CASE WHEN w.digitize_status = '已建模'   THEN 1 ELSE 0 END) AS modeled_works,
       SUM(CASE WHEN w.digitize_status = '已入库'   THEN 1 ELSE 0 END) AS stored_works,
       ROUND(SUM(CASE WHEN w.digitize_status <> '未数字化' THEN 1 ELSE 0 END)
             / NULLIF(COUNT(w.work_id), 0) * 100, 1)          AS collect_rate
FROM heritage_project p
JOIN heritage_category c ON c.category_id = p.category_id
LEFT JOIN heritage_work w ON w.project_id = p.project_id
GROUP BY p.project_id, p.project_code, p.project_name, p.project_level,
         p.project_status, c.category_name, p.is_digitized;

/* ---------------------------------------------------------------------------
   视图校验
   --------------------------------------------------------------------------- */
SELECT 'v_project_profile（项目全景档案）' AS 视图名称, COUNT(*) AS 输出行数 FROM v_project_profile
UNION ALL SELECT 'v_inheritor_heritage（传承人-项目展开）', COUNT(*) FROM v_inheritor_heritage
UNION ALL SELECT 'v_activity_enrollment（活动报名情况）',   COUNT(*) FROM v_activity_enrollment
UNION ALL SELECT 'v_student_learning_record（学习档案）',    COUNT(*) FROM v_student_learning_record
UNION ALL SELECT 'v_project_heat_index（项目热度指数）',     COUNT(*) FROM v_project_heat_index
UNION ALL SELECT 'v_category_hierarchy（分类层次）',         COUNT(*) FROM v_category_hierarchy
UNION ALL SELECT 'v_digitization_progress（数字化进度）',    COUNT(*) FROM v_digitization_progress;
