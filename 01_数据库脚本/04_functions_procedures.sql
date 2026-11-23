/* =====================================================================================
   地方非遗文化数字化保护数据库管理系统  ——  用户自定义函数与存储过程
   -------------------------------------------------------------------------------------
   文件     : 04_functions_procedures.sql
   内容     : 4 个自定义函数 + 6 个存储过程 + 1 个定时事件
   设计说明 : 把"业务规则"下沉到数据库层，保证无论前端还是脚本调用，业务逻辑始终一致；
              报名过程使用显式事务 + 行级锁，防止高并发下的名额超卖。
   前置条件 : 已完成 01_schema.sql、02_init_data.sql、03_views.sql
   提示     : 若目标 MySQL 开启了二进制日志且当前账号无 SUPER 权限，请先执行
              SET GLOBAL log_bin_trust_function_creators = 1;
              或改用 SET SESSION 方式授予信任后创建函数。
   ===================================================================================== */

USE heritage_db;
SET NAMES utf8mb4;

/* =====================================================================================
   第一部分  自定义函数
   ===================================================================================== */

DROP FUNCTION IF EXISTS fn_inheritor_age;
DROP FUNCTION IF EXISTS fn_level_weight;
DROP FUNCTION IF EXISTS fn_project_inheritor_count;
DROP FUNCTION IF EXISTS fn_project_heat_score;

DELIMITER $$

/* 函数 1：根据出生日期计算传承人当前年龄 */
CREATE FUNCTION fn_inheritor_age(p_birth_date DATE)
RETURNS INT
NO SQL
NOT DETERMINISTIC
COMMENT '计算传承人当前年龄（周岁）'
BEGIN
    IF p_birth_date IS NULL THEN
        RETURN NULL;
    END IF;
    RETURN TIMESTAMPDIFF(YEAR, p_birth_date, CURDATE());
END$$

/* 函数 2：项目级别到影响力权重的映射 */
CREATE FUNCTION fn_level_weight(p_level VARCHAR(10))
RETURNS DECIMAL(4,2)
DETERMINISTIC
COMMENT '非遗级别影响力权重：国家级1.00/省级0.75/市级0.50/县级0.25'
BEGIN
    RETURN CASE p_level
               WHEN '国家级' THEN 1.00
               WHEN '省级'   THEN 0.75
               WHEN '市级'   THEN 0.50
               ELSE 0.25
           END;
END$$

/* 函数 3：统计某非遗项目的关联传承人数量 */
CREATE FUNCTION fn_project_inheritor_count(p_project_id INT UNSIGNED)
RETURNS INT
READS SQL DATA
COMMENT '统计指定非遗项目已登记的传承人数量'
BEGIN
    DECLARE v_cnt INT DEFAULT 0;
    SELECT COUNT(*) INTO v_cnt
      FROM project_inheritor
     WHERE project_id = p_project_id;
    RETURN IFNULL(v_cnt, 0);
END$$

/* 函数 4：读取非遗项目传播热度得分（复用视图 5 的算法结果） */
CREATE FUNCTION fn_project_heat_score(p_project_id INT UNSIGNED)
RETURNS DECIMAL(10,2)
READS SQL DATA
COMMENT '返回指定非遗项目的传播热度指数'
BEGIN
    DECLARE v_score DECIMAL(10,2) DEFAULT 0.00;
    SELECT heat_score INTO v_score
      FROM v_project_heat_index
     WHERE project_id = p_project_id;
    RETURN IFNULL(v_score, 0.00);
END$$

DELIMITER ;

/* =====================================================================================
   第二部分  存储过程
   ===================================================================================== */

DROP PROCEDURE IF EXISTS sp_project_full_profile;
DROP PROCEDURE IF EXISTS sp_student_register_activity;
DROP PROCEDURE IF EXISTS sp_category_statistics;
DROP PROCEDURE IF EXISTS sp_archive_project;
DROP PROCEDURE IF EXISTS sp_recommend_activity;
DROP PROCEDURE IF EXISTS sp_activity_report;

DELIMITER $$

/* ---------------------------------------------------------------------------
   存储过程 1：sp_project_full_profile
   功能：一键导出某个非遗项目的"四维完整档案"（基础信息 / 传承人 / 作品 / 研学活动）
         返回 4 个结果集，供前端档案详情页一次性渲染。
   --------------------------------------------------------------------------- */
CREATE PROCEDURE sp_project_full_profile(IN p_project_id INT UNSIGNED)
COMMENT '导出非遗项目完整档案（多结果集）'
BEGIN
    /* 结果集 1：项目基础档案与规模指标 */
    SELECT project_code          AS 项目编号,
           project_name          AS 项目名称,
           category_name         AS 所属门类,
           parent_category_name  AS 上级门类,
           project_level         AS 项目级别,
           batch_no              AS 公布批次,
           declared_area         AS 申报地区,
           protect_unit          AS 保护单位,
           approval_year         AS 入选年份,
           project_status        AS 项目状态,
           IF(is_digitized = 1, '已建数字化档案', '未建数字化档案') AS 数字化状态,
           inheritor_count       AS 传承人数,
           work_count            AS 作品数,
           digitized_work_count  AS 已入库数字化作品数,
           course_count          AS 研学课程数,
           activity_count        AS 活动场次,
           registration_count    AS 累计报名人次
      FROM v_project_profile
     WHERE project_id = p_project_id;

    /* 结果集 2：关联传承人名单 */
    SELECT inheritor_code AS 传承人编号,
           inheritor_name AS 姓名,
           gender         AS 性别,
           age            AS 年龄,
           inheritor_level AS 级别,
           region         AS 所属地区,
           health_status  AS 健康状况,
           relation_role  AS 项目内角色,
           start_year     AS 传承起始年,
           IF(is_master = 1, '是', '否') AS 是否主传承人
      FROM v_inheritor_heritage
     WHERE project_id = p_project_id
     ORDER BY is_master DESC, inheritor_level, inheritor_code;

    /* 结果集 3：作品数字化清单 */
    SELECT w.work_code       AS 作品编号,
           w.work_name       AS 作品名称,
           i.inheritor_name  AS 创作传承人,
           w.create_year     AS 创作年份,
           w.material        AS 材质,
           w.spec_size       AS 规格,
           w.digitize_status AS 数字化进度,
           IF(w.in_collection = 1, '已入藏', '未入藏') AS 入藏状态,
           COALESCE(w.image_url, '—') AS 数字资源路径
      FROM heritage_work w
      JOIN inheritor i ON i.inheritor_id = w.creator_id
     WHERE w.project_id = p_project_id
     ORDER BY w.digitize_status, w.work_code;

    /* 结果集 4：研学课程与开班情况 */
    SELECT activity_code   AS 活动编号,
           course_name     AS 课程名称,
           activity_name   AS 活动名称,
           teacher_name    AS 主讲传承人,
           activity_place  AS 活动地点,
           start_time      AS 开始时间,
           plan_capacity   AS 计划人数,
           confirmed_total AS 报名人数,
           remaining_quota AS 剩余名额,
           CONCAT(enroll_rate, '%') AS 报名率,
           activity_status AS 活动状态
      FROM v_activity_enrollment
     WHERE project_id = p_project_id
     ORDER BY start_time;
END$$

/* ---------------------------------------------------------------------------
   存储过程 2：sp_student_register_activity
   功能：学生报名研学活动的完整业务事务。
         校验链：学生存在 -> 活动存在 -> 活动可报 -> 未过截止 -> 未重复报名 -> 名额判定
         使用 SELECT ... FOR UPDATE 对活动行加锁，避免并发超卖。
         返回参数 p_code：1 成功；负数表示各类失败原因。
   --------------------------------------------------------------------------- */
CREATE PROCEDURE sp_student_register_activity(
    IN  p_student_id  INT UNSIGNED,
    IN  p_activity_id INT UNSIGNED,
    OUT p_code        INT,
    OUT p_msg         VARCHAR(200)
)
COMMENT '学生报名研学活动（含校验与事务控制）'
BEGIN
    DECLARE v_stu_cnt   INT DEFAULT 0;
    DECLARE v_act_cnt   INT DEFAULT 0;
    DECLARE v_status    VARCHAR(20);
    DECLARE v_deadline  DATETIME;
    DECLARE v_capacity  INT DEFAULT 0;
    DECLARE v_confirmed INT DEFAULT 0;
    DECLARE v_dup       INT DEFAULT 0;
    DECLARE v_new_state VARCHAR(20);

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        SET p_code = -99;
        SET p_msg  = '报名失败：数据库执行异常，事务已回滚。';
    END;

    SET p_code = 0;

    SELECT COUNT(*) INTO v_stu_cnt FROM student WHERE student_id = p_student_id;
    SELECT COUNT(*) INTO v_act_cnt FROM research_activity WHERE activity_id = p_activity_id;

    IF v_stu_cnt = 0 THEN
        SET p_code = -1; SET p_msg = '报名失败：学生记录不存在。';
    ELSEIF v_act_cnt = 0 THEN
        SET p_code = -2; SET p_msg = '报名失败：研学活动不存在。';
    ELSE
        START TRANSACTION;

        SELECT activity_status, deadline_time, plan_capacity
          INTO v_status, v_deadline, v_capacity
          FROM research_activity
         WHERE activity_id = p_activity_id
           FOR UPDATE;

        IF v_status IN ('已取消', '已结束') THEN
            SET p_code = -3; SET p_msg = '报名失败：该活动已取消或已结束。';
            ROLLBACK;
        ELSEIF NOW() > v_deadline THEN
            SET p_code = -4; SET p_msg = CONCAT('报名失败：报名已于 ', DATE_FORMAT(v_deadline, '%Y-%m-%d %H:%i'), ' 截止。');
            ROLLBACK;
        ELSE
            SELECT COUNT(*) INTO v_dup
              FROM activity_registration
             WHERE activity_id = p_activity_id
               AND student_id  = p_student_id
               AND register_status <> '已取消';

            IF v_dup > 0 THEN
                SET p_code = -5; SET p_msg = '报名失败：该学生已报名本场活动，请勿重复报名。';
                ROLLBACK;
            ELSE
                SELECT COUNT(*) INTO v_confirmed
                  FROM activity_registration
                 WHERE activity_id = p_activity_id
                   AND register_status IN ('已报名', '已录取');

                IF v_confirmed >= v_capacity THEN
                    SET v_new_state = '候补';
                ELSE
                    SET v_new_state = '已录取';
                END IF;

                INSERT INTO activity_registration
                       (activity_id, student_id, register_status, attendance_status, register_time)
                VALUES (p_activity_id, p_student_id, v_new_state, '未开始', NOW());

                SET p_code = 1;
                SET p_msg  = CONCAT('报名成功，当前报名状态：', v_new_state,
                                    '（已报名 ', v_confirmed + 1, '/', v_capacity, ' 人）。');
                COMMIT;
            END IF;
        END IF;
    END IF;
END$$

/* ---------------------------------------------------------------------------
   存储过程 3：sp_category_statistics
   功能：按"十大类"逐层汇总统计（一级门类 + 其下二级门类），
         使用游标（CURSOR）逐行扫描分类表并写入临时表后统一输出，
         展示游标 + 循环 + 临时表的标准写法。
   --------------------------------------------------------------------------- */
CREATE PROCEDURE sp_category_statistics()
COMMENT '按一级非遗门类汇总统计项目/传承人/作品/活动/报名情况'
BEGIN
    DECLARE v_done  INT DEFAULT 0;
    DECLARE v_cid   INT UNSIGNED;
    DECLARE v_cname VARCHAR(50);

    DECLARE cur_cat CURSOR FOR
        SELECT category_id, category_name
          FROM heritage_category
         WHERE category_level = 1
         ORDER BY category_id;

    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_done = 1;

    DROP TEMPORARY TABLE IF EXISTS tmp_category_stat;
    CREATE TEMPORARY TABLE tmp_category_stat (
        category_name      VARCHAR(50),
        project_count      INT,
        national_count     INT,
        inheritor_count    INT,
        work_count         INT,
        activity_count     INT,
        registration_count INT
    );

    OPEN cur_cat;

    read_loop: LOOP
        FETCH cur_cat INTO v_cid, v_cname;
        IF v_done = 1 THEN
            LEAVE read_loop;
        END IF;

        INSERT INTO tmp_category_stat
        SELECT v_cname,
               COUNT(DISTINCT p.project_id),
               COUNT(DISTINCT CASE WHEN p.project_level = '国家级' THEN p.project_id END),
               COUNT(DISTINCT pi.inheritor_id),
               COUNT(DISTINCT w.work_id),
               COUNT(DISTINCT ra.activity_id),
               COUNT(DISTINCT ar.registration_id)
          FROM heritage_project p
          LEFT JOIN project_inheritor   pi ON pi.project_id = p.project_id
          LEFT JOIN heritage_work       w  ON w.project_id  = p.project_id
          LEFT JOIN research_course     rc ON rc.project_id = p.project_id
          LEFT JOIN research_activity   ra ON ra.course_id  = rc.course_id
          LEFT JOIN activity_registration ar ON ar.activity_id = ra.activity_id
                                           AND ar.register_status IN ('已报名', '已录取')
         WHERE p.category_id = v_cid
            OR p.category_id IN (SELECT category_id
                                   FROM heritage_category
                                  WHERE parent_id = v_cid);
    END LOOP;

    CLOSE cur_cat;

    SELECT category_name      AS 一级门类,
           project_count      AS 项目数,
           national_count     AS 国家级项目数,
           inheritor_count    AS 关联传承人数,
           work_count         AS 作品数,
           activity_count     AS 研学活动场次,
           registration_count AS 累计报名人次
      FROM tmp_category_stat
     ORDER BY project_count DESC, category_name;

    DROP TEMPORARY TABLE IF EXISTS tmp_category_stat;
END$$

/* ---------------------------------------------------------------------------
   存储过程 4：sp_archive_project
   功能：非遗项目"数据归档"（逻辑归档，不做物理删除）。
         冻结多维统计快照写入归档表，并把项目状态置为"已归档"，
         全过程在单一事务内完成，保证快照与状态一致。
   --------------------------------------------------------------------------- */
CREATE PROCEDURE sp_archive_project(
    IN  p_project_id INT UNSIGNED,
    IN  p_reason     VARCHAR(200),
    IN  p_operator   VARCHAR(50),
    OUT p_msg        VARCHAR(200)
)
COMMENT '非遗项目数据归档（生成统计快照快照并置为已归档状态）'
BEGIN
    DECLARE v_cnt    INT DEFAULT 0;
    DECLARE v_status VARCHAR(10);
    DECLARE v_code   VARCHAR(30);
    DECLARE v_name   VARCHAR(100);
    DECLARE v_level  VARCHAR(10);
    DECLARE v_area   VARCHAR(100);
    DECLARE v_cat    VARCHAR(50);
    DECLARE v_inh    INT DEFAULT 0;
    DECLARE v_work   INT DEFAULT 0;
    DECLARE v_act    INT DEFAULT 0;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        SET p_msg = '归档失败：数据库执行异常，事务已回滚。';
    END;

    SELECT COUNT(*) INTO v_cnt FROM heritage_project WHERE project_id = p_project_id;

    IF v_cnt = 0 THEN
        SET p_msg = '归档失败：指定项目不存在。';
    ELSE
        SELECT project_status, project_code, project_name, project_level, declared_area
          INTO v_status, v_code, v_name, v_level, v_area
          FROM heritage_project
         WHERE project_id = p_project_id;

        IF v_status = '已归档' THEN
            SET p_msg = CONCAT('归档失败：项目「', v_name, '」已处于归档状态，无需重复归档。');
        ELSE
            SELECT c.category_name INTO v_cat
              FROM heritage_project p
              JOIN heritage_category c ON c.category_id = p.category_id
             WHERE p.project_id = p_project_id;

            SELECT COUNT(*) INTO v_inh  FROM project_inheritor WHERE project_id = p_project_id;
            SELECT COUNT(*) INTO v_work FROM heritage_work      WHERE project_id = p_project_id;
            SELECT COUNT(*) INTO v_act
              FROM research_activity ra
              JOIN research_course rc ON rc.course_id = ra.course_id
             WHERE rc.project_id = p_project_id;

            START TRANSACTION;

            INSERT INTO heritage_project_archive
                   (project_id, project_code, project_name, category_name, project_level,
                    declared_area, inheritor_count, work_count, activity_count,
                    archive_reason, archived_by)
            VALUES (p_project_id, v_code, v_name, v_cat, v_level, v_area,
                    v_inh, v_work, v_act,
                    IFNULL(p_reason, '按非遗档案管理规定归档'), IFNULL(p_operator, 'system'));

            UPDATE heritage_project
               SET project_status = '已归档'
             WHERE project_id = p_project_id;

            INSERT INTO operation_log (table_name, action_type, record_id, action_desc, operator)
            VALUES ('heritage_project', 'UPDATE', p_project_id,
                    CONCAT('项目归档：', v_name, '（传承人 ', v_inh,
                           ' 人 / 作品 ', v_work, ' 件 / 活动 ', v_act, ' 场）'),
                    IFNULL(p_operator, 'system'));

            COMMIT;

            SET p_msg = CONCAT('归档成功：项目「', v_name, '」已生成归档快照，状态置为"已归档"。');
        END IF;
    END IF;
END$$

/* ---------------------------------------------------------------------------
   存储过程 5：sp_recommend_activity
   功能：面向学生的研学活动推荐（混合推荐模型在数据库层的实现）。
         打分 = 内容匹配分（兴趣门类一致 +5 / 上级门类一致 +3）
              + 协同过滤分（活动报名率归一化，最多 +3，体现群体热度）
              + 项目级别分（国家级 2.0 / 省级 1.5 / 市级 1.0 / 县级 0.5）
         过滤条件：仅"报名中"且尚未开始、且该学生未报名的活动。
   --------------------------------------------------------------------------- */
CREATE PROCEDURE sp_recommend_activity(
    IN p_student_id INT UNSIGNED,
    IN p_top_n      INT
)
COMMENT '面向学生的研学活动混合推荐（内容匹配 + 群体热度）'
BEGIN
    DECLARE v_interest INT UNSIGNED;
    DECLARE v_parent   INT UNSIGNED;

    SET v_interest = (SELECT interest_category_id FROM student WHERE student_id = p_student_id);
    SET v_parent   = (SELECT parent_id FROM heritage_category WHERE category_id = v_interest);
    SET p_top_n    = IFNULL(p_top_n, 5);

    SELECT t.activity_code   AS 活动编号,
           t.activity_name   AS 活动名称,
           t.course_name     AS 课程名称,
           t.project_name    AS 非遗项目,
           t.category_name   AS 所属门类,
           t.teacher_name    AS 主讲传承人,
           t.start_time      AS 开始时间,
           t.activity_place  AS 活动地点,
           t.remaining_quota AS 剩余名额,
           CONCAT(t.enroll_rate, '%') AS 报名率,
           t.recommend_score AS 推荐得分,
           t.recommend_reason AS 推荐理由
      FROM (
            SELECT e.activity_id,
                   e.activity_code,
                   e.activity_name,
                   e.course_name,
                   e.project_name,
                   e.category_name,
                   e.teacher_name,
                   e.start_time,
                   e.activity_place,
                   e.remaining_quota,
                   e.enroll_rate,
                   ROUND(
                       CASE WHEN e.category_id = v_interest THEN 5.0
                            WHEN v_interest IS NOT NULL AND e.parent_category_id = v_interest THEN 4.0
                            WHEN v_parent   IS NOT NULL AND e.category_id = v_parent THEN 4.0
                            WHEN v_parent   IS NOT NULL AND e.parent_category_id = v_parent THEN 3.0
                            ELSE 0.0
                        END
                     + LEAST(e.enroll_rate / 10.0, 3.0)
                     + CASE e.project_level
                           WHEN '国家级' THEN 2.0
                           WHEN '省级'   THEN 1.5
                           WHEN '市级'   THEN 1.0
                           ELSE 0.5
                       END
                   , 2) AS recommend_score,
                   CONCAT(
                       CASE WHEN e.category_id = v_interest THEN '兴趣门类精准匹配；'
                            WHEN v_interest IS NOT NULL AND e.parent_category_id = v_interest THEN '学生兴趣为该项目的上级门类；'
                            WHEN v_parent   IS NOT NULL AND e.category_id = v_parent THEN '项目门类为学生兴趣的上级门类；'
                            WHEN v_parent   IS NOT NULL AND e.parent_category_id = v_parent THEN '与兴趣同属一个大门类；'
                            ELSE '跨门类拓展；' END,
                       '群体报名热度 ', e.enroll_rate, '%；',
                       e.project_level, '项目'
                   ) AS recommend_reason
              FROM v_activity_enrollment e
             WHERE e.activity_status = '报名中'
               AND e.start_time > NOW()
               AND e.activity_id NOT IN (
                       SELECT r.activity_id
                         FROM activity_registration r
                        WHERE r.student_id = p_student_id
                          AND r.register_status <> '已取消')
           ) t
     ORDER BY t.recommend_score DESC, t.enroll_rate DESC
     LIMIT p_top_n;
END$$

/* ---------------------------------------------------------------------------
   存储过程 6：sp_activity_report
   功能：研学活动周期报表。按时间段输出活动执行情况（报名、签到、出勤率、均分），
         用于研学活动档案统计与上级汇报。
   --------------------------------------------------------------------------- */
CREATE PROCEDURE sp_activity_report(
    IN p_start DATE,
    IN p_end   DATE
)
COMMENT '按时间区间输出研学活动执行情况统计报表'
BEGIN
    SELECT e.activity_code    AS 活动编号,
           e.activity_name    AS 活动名称,
           e.project_name     AS 非遗项目,
           DATE(e.start_time) AS 活动日期,
           e.organizer        AS 组织单位,
           e.plan_capacity    AS 计划人数,
           e.confirmed_total  AS 报名人数,
           e.checkin_total    AS 实际签到,
           CONCAT(e.enroll_rate, '%') AS 报名率,
           ROUND(s.avg_score, 2)      AS 平均成绩,
           COALESCE(s.max_score, 0)   AS 最高成绩,
           CASE WHEN e.confirmed_total = 0 THEN '—'
                ELSE CONCAT(ROUND(e.checkin_total / e.confirmed_total * 100, 1), '%')
           END                AS 出勤率,
           e.activity_status  AS 活动状态
      FROM v_activity_enrollment e
      LEFT JOIN (SELECT activity_id,
                        AVG(score) AS avg_score,
                        MAX(score) AS max_score
                   FROM activity_registration
                  WHERE attendance_status = '已签到'
                  GROUP BY activity_id) s
             ON s.activity_id = e.activity_id
     WHERE DATE(e.start_time) BETWEEN IFNULL(p_start, '1900-01-01')
                                  AND IFNULL(p_end,   '2999-12-31')
     ORDER BY e.start_time;
END$$

DELIMITER ;

/* =====================================================================================
   第三部分  定时事件（可选，用于活动状态自动流转）
   说明：MySQL 事件调度器需处于开启状态：SET GLOBAL event_scheduler = ON;
        该事件每天 01:00 执行一次：把已过结束时间的活动自动置为"已结束"。
   ===================================================================================== */

DROP EVENT IF EXISTS ev_activity_status_auto_sync;

DELIMITER $$
CREATE EVENT ev_activity_status_auto_sync
ON SCHEDULE EVERY 1 DAY
STARTS (TIMESTAMP(CURDATE()) + INTERVAL 1 DAY + INTERVAL 1 HOUR)
COMMENT '每日自动同步研学活动状态（报名中/已满员 -> 已结束）'
DO
BEGIN
    UPDATE research_activity
       SET activity_status = '已结束'
     WHERE activity_status IN ('报名中', '已满员')
       AND end_time < NOW();

    INSERT INTO operation_log (table_name, action_type, record_id, action_desc, operator)
    VALUES ('research_activity', 'UPDATE', NULL,
            '定时事件 ev_activity_status_auto_sync 完成活动状态自动同步',
            'event_scheduler');
END$$
DELIMITER ;

/* =====================================================================================
   第四部分  函数与存储过程功能验证
   ===================================================================================== */

/* 验证 1：自定义函数 */
SELECT fn_inheritor_age('1948-03-12')                       AS 函数1_年龄计算,
       fn_level_weight('国家级')                            AS 函数2_级别权重,
       fn_project_inheritor_count(1)                        AS 函数3_项目1传承人数,
       fn_project_heat_score(2)                             AS 函数4_项目2热度分;

/* 验证 2：项目完整档案（多结果集，取项目 1 越剧） */
CALL sp_project_full_profile(1);

/* 验证 3：分类统计报表（游标实现） */
CALL sp_category_statistics();

/* 验证 4：学生报名事务 —— 学生 6 报名活动 12（黄酒秋酿场，报名中） */
CALL sp_student_register_activity(6, 12, @code, @msg);
SELECT @code AS 返回码, @msg AS 返回信息;

/* 验证 5：重复报名校验（学生 6 再次报名活动 12，应返回 -5） */
CALL sp_student_register_activity(6, 12, @code2, @msg2);
SELECT @code2 AS 返回码, @msg2 AS 返回信息;

/* 清理：删除本次验证新产生的报名记录，使基础数据集与课程设计文档保持一致 */
DELETE FROM activity_registration WHERE activity_id = 12 AND student_id = 6;
SELECT '已清理验证数据，基础数据集恢复' AS 验证说明, ROW_COUNT() AS 删除行数;

/* 验证 6：推荐算法（学生 1 兴趣为"传统戏剧"，活动多为跨门类拓展；
            学生 15 兴趣为"食品酿造技艺"，应命中黄酒酿造秋酿专场并获最高推荐分） */
CALL sp_recommend_activity(1, 5);
CALL sp_recommend_activity(15, 5);

/* 验证 7：研学活动周期报表（2026 年全年） */
CALL sp_activity_report('2026-01-01', '2026-12-31');
