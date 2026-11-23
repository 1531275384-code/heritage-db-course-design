/* =====================================================================================
   地方非遗文化数字化保护数据库管理系统  ——  触发器定义脚本
   -------------------------------------------------------------------------------------
   文件     : 05_triggers.sql
   内容     : 6 个触发器，覆盖"报名准入校验、名额自动流转、审计留痕、活态传承预警、
              数字化档案自动标记、容量变更保护"六类典型数据库自治场景
   设计思想 : 把"数据完整性 + 业务规则"下沉到数据库层，使任何入口（前端、脚本、
              第三方程序）都无法绕过规则，从根本上避免脏数据。
   前置条件 : 已完成 01_schema.sql、02_init_data.sql、03_views.sql、04_functions_procedures.sql
   注意     : 触发器必须在基础数据装载完成之后再创建，否则历史活动报名数据
              （截止时间已过）会被准入校验拦截。
   ===================================================================================== */

USE heritage_db;
SET NAMES utf8mb4;

DROP TRIGGER IF EXISTS trg_reg_before_insert;
DROP TRIGGER IF EXISTS trg_reg_after_insert;
DROP TRIGGER IF EXISTS trg_reg_after_update;
DROP TRIGGER IF EXISTS trg_inheritor_after_update;
DROP TRIGGER IF EXISTS trg_work_after_insert;
DROP TRIGGER IF EXISTS trg_activity_before_update;

DELIMITER $$

/* ---------------------------------------------------------------------------
   触发器 1：trg_reg_before_insert
   时机：BEFORE INSERT ON activity_registration
   作用：报名准入校验（数据库层最后一道防线）
         ① 活动必须存在；
         ② 活动不得为"已取消/已结束"；
         ③ 不得在报名截止时间之后报名；
         ④ "已报名/已录取"状态的人数不得超过计划容量（满员须登记为"候补"）。
   --------------------------------------------------------------------------- */
CREATE TRIGGER trg_reg_before_insert
BEFORE INSERT ON activity_registration
FOR EACH ROW
BEGIN
    DECLARE v_status    VARCHAR(20);
    DECLARE v_deadline  DATETIME;
    DECLARE v_capacity  INT DEFAULT 0;
    DECLARE v_confirmed INT DEFAULT 0;

    SELECT activity_status, deadline_time, plan_capacity
      INTO v_status, v_deadline, v_capacity
      FROM research_activity
     WHERE activity_id = NEW.activity_id;

    IF v_status IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '报名失败：指定的研学活动不存在。';
    ELSEIF v_status IN ('已取消', '已结束') THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '报名失败：该活动已取消或已结束，不接受报名。';
    ELSEIF NEW.register_status IN ('已报名', '已录取') AND NOW() > v_deadline THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '报名失败：已超过该活动的报名截止时间。';
    ELSEIF NEW.register_status IN ('已报名', '已录取') THEN
        SELECT COUNT(*) INTO v_confirmed
          FROM activity_registration
         WHERE activity_id = NEW.activity_id
           AND register_status IN ('已报名', '已录取');
        IF v_confirmed >= v_capacity THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = '报名失败：活动名额已满，请以"候补"状态登记。';
        END IF;
    END IF;
END$$

/* ---------------------------------------------------------------------------
   触发器 2：trg_reg_after_insert
   时机：AFTER INSERT ON activity_registration
   作用：① 自动写入操作审计日志；
         ② 报名人数达到计划容量时，自动把活动状态由"报名中"流转为"已满员"。
         —— 体现"派生状态由数据库自动维护"，避免人工维护造成不一致。
   --------------------------------------------------------------------------- */
CREATE TRIGGER trg_reg_after_insert
AFTER INSERT ON activity_registration
FOR EACH ROW
BEGIN
    DECLARE v_status    VARCHAR(20);
    DECLARE v_capacity  INT DEFAULT 0;
    DECLARE v_aname     VARCHAR(100);
    DECLARE v_sname     VARCHAR(50);
    DECLARE v_confirmed INT DEFAULT 0;

    SELECT activity_status, plan_capacity, activity_name
      INTO v_status, v_capacity, v_aname
      FROM research_activity
     WHERE activity_id = NEW.activity_id;

    SELECT student_name INTO v_sname
      FROM student
     WHERE student_id = NEW.student_id;

    INSERT INTO operation_log (table_name, action_type, record_id, action_desc, operator)
    VALUES ('activity_registration', 'INSERT', NEW.registration_id,
            CONCAT('学生「', IFNULL(v_sname, '未知'), '」报名研学活动「', IFNULL(v_aname, '未知'),
                   '」，报名状态：', NEW.register_status),
            'system');

    IF NEW.register_status IN ('已报名', '已录取') AND v_status = '报名中' THEN
        SELECT COUNT(*) INTO v_confirmed
          FROM activity_registration
         WHERE activity_id = NEW.activity_id
           AND register_status IN ('已报名', '已录取');
        IF v_confirmed >= v_capacity THEN
            UPDATE research_activity
               SET activity_status = '已满员'
             WHERE activity_id = NEW.activity_id;
        END IF;
    END IF;
END$$

/* ---------------------------------------------------------------------------
   触发器 3：trg_reg_after_update
   时机：AFTER UPDATE ON activity_registration
   作用：① 记录报名状态 / 出勤状态的变更轨迹（审计留痕）；
         ② 报名人数发生变化后，自动在"报名中"与"已满员"之间双向流转活动状态。
   --------------------------------------------------------------------------- */
CREATE TRIGGER trg_reg_after_update
AFTER UPDATE ON activity_registration
FOR EACH ROW
BEGIN
    DECLARE v_status    VARCHAR(20);
    DECLARE v_capacity  INT DEFAULT 0;
    DECLARE v_confirmed INT DEFAULT 0;

    IF OLD.register_status <> NEW.register_status
       OR OLD.attendance_status <> NEW.attendance_status
       OR NOT (OLD.score <=> NEW.score) THEN
        INSERT INTO operation_log (table_name, action_type, record_id, action_desc, operator)
        VALUES ('activity_registration', 'UPDATE', NEW.registration_id,
                CONCAT('报名记录变更：报名状态 ', OLD.register_status, ' -> ', NEW.register_status,
                       '；出勤状态 ', OLD.attendance_status, ' -> ', NEW.attendance_status,
                       IF(NEW.score IS NULL, '', CONCAT('；成绩 ', NEW.score))),
                'system');
    END IF;

    SELECT activity_status, plan_capacity
      INTO v_status, v_capacity
      FROM research_activity
     WHERE activity_id = NEW.activity_id;

    SELECT COUNT(*) INTO v_confirmed
      FROM activity_registration
     WHERE activity_id = NEW.activity_id
       AND register_status IN ('已报名', '已录取');

    IF v_status = '已满员' AND v_confirmed < v_capacity THEN
        UPDATE research_activity
           SET activity_status = '报名中'
         WHERE activity_id = NEW.activity_id;
    ELSEIF v_status = '报名中' AND v_confirmed >= v_capacity THEN
        UPDATE research_activity
           SET activity_status = '已满员'
         WHERE activity_id = NEW.activity_id;
    END IF;
END$$

/* ---------------------------------------------------------------------------
   触发器 4：trg_inheritor_after_update
   时机：AFTER UPDATE ON inheritor
   作用：活态传承预警。当传承人健康状况变为"已故"时，自动检查其关联的非遗项目：
         若某项目名下全部传承人均已离世，则把项目状态由"正常"自动置为"濒危"，
         触发抢救性保护流程。这是本系统"活态传承"主题最具业务价值的自动化规则。
   --------------------------------------------------------------------------- */
CREATE TRIGGER trg_inheritor_after_update
AFTER UPDATE ON inheritor
FOR EACH ROW
BEGIN
    IF NEW.health_status <> OLD.health_status THEN
        INSERT INTO operation_log (table_name, action_type, record_id, action_desc, operator)
        VALUES ('inheritor', 'UPDATE', NEW.inheritor_id,
                CONCAT('传承人「', NEW.inheritor_name, '」健康状况变更：',
                       OLD.health_status, ' -> ', NEW.health_status),
                'system');

        IF NEW.health_status = '已故' THEN
            UPDATE heritage_project p
               SET p.project_status = '濒危'
             WHERE p.project_status = '正常'
               AND p.project_id IN (SELECT pi.project_id
                                      FROM project_inheritor pi
                                     WHERE pi.inheritor_id = NEW.inheritor_id)
               AND NOT EXISTS (SELECT 1
                                 FROM project_inheritor pi2
                                 JOIN inheritor i2 ON i2.inheritor_id = pi2.inheritor_id
                                WHERE pi2.project_id = p.project_id
                                  AND i2.health_status <> '已故');
        END IF;
    END IF;
END$$

/* ---------------------------------------------------------------------------
   触发器 5：trg_work_after_insert
   时机：AFTER INSERT ON heritage_work
   作用：① 记录非遗作品归档日志；
         ② 当新归档作品的数字化进度达到"已入库"时，自动把所属项目标记为
            "已建立数字化档案"（is_digitized = 1），实现数字化保护进度的自动汇总。
   --------------------------------------------------------------------------- */
CREATE TRIGGER trg_work_after_insert
AFTER INSERT ON heritage_work
FOR EACH ROW
BEGIN
    INSERT INTO operation_log (table_name, action_type, record_id, action_desc, operator)
    VALUES ('heritage_work', 'INSERT', NEW.work_id,
            CONCAT('新增非遗作品档案「', NEW.work_name, '」，数字化进度：', NEW.digitize_status),
            'system');

    IF NEW.digitize_status = '已入库' THEN
        UPDATE heritage_project
           SET is_digitized = 1
         WHERE project_id = NEW.project_id
           AND is_digitized = 0;
    END IF;
END$$

/* ---------------------------------------------------------------------------
   触发器 6：trg_activity_before_update
   时机：BEFORE UPDATE ON research_activity
   作用：容量变更保护。计划人数一旦被调小，不得低于当前已报名（含已录取）人数，
         防止人为缩小容量造成"超额报名"的脏数据。
   --------------------------------------------------------------------------- */
CREATE TRIGGER trg_activity_before_update
BEFORE UPDATE ON research_activity
FOR EACH ROW
BEGIN
    DECLARE v_confirmed INT DEFAULT 0;

    IF NEW.plan_capacity <> OLD.plan_capacity THEN
        SELECT COUNT(*) INTO v_confirmed
          FROM activity_registration
         WHERE activity_id = OLD.activity_id
           AND register_status IN ('已报名', '已录取');

        IF NEW.plan_capacity < v_confirmed THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = '更新失败：计划人数不得小于当前已报名（含已录取）人数。';
        END IF;
    END IF;
END$$

DELIMITER ;

/* =====================================================================================
   触发器创建结果校验
   ===================================================================================== */
SELECT TRIGGER_NAME        AS 触发器名称,
       EVENT_MANIPULATION  AS 触发时机,
       EVENT_OBJECT_TABLE  AS 目标表,
       ACTION_TIMING       AS 触发时序
FROM information_schema.TRIGGERS
WHERE TRIGGER_SCHEMA = 'heritage_db'
ORDER BY EVENT_OBJECT_TABLE, ACTION_TIMING;

/* ---------------------------------------------------------------------------
   正向验证一：模拟一次合法报名，观察日志自动写入
   场景：学生 5 报名活动 14（越窑青瓷拉坯体验营·秋季，报名中，容量 20，当前 5 人）
   --------------------------------------------------------------------------- */
INSERT INTO activity_registration (activity_id, student_id, register_status, attendance_status)
VALUES (14, 5, '已报名', '未开始');

SELECT '报名后活动状态与人数' AS 验证项,
       e.activity_status      AS 活动状态,
       e.confirmed_total      AS 报名人数,
       e.plan_capacity        AS 计划人数
  FROM v_activity_enrollment e
 WHERE e.activity_id = 14;

/* ---------------------------------------------------------------------------
   正向验证二：活动已满员时，触发器仍允许以"候补"状态登记
   场景：活动 13（越剧国庆专场）容量 10 且已满员，学生 3 以候补身份登记成功
   --------------------------------------------------------------------------- */
INSERT INTO activity_registration (activity_id, student_id, register_status, attendance_status)
VALUES (13, 3, '候补', '未开始');

SELECT '满员活动候补登记结果' AS 验证项,
       activity_status AS 活动状态,
       confirmed_total AS 已占名额,
       plan_capacity   AS 计划人数
  FROM v_activity_enrollment
 WHERE activity_id = 13;

SELECT '触发器自动写入的审计日志' AS 验证项,
       action_desc AS 日志内容,
       operator    AS 操作人,
       log_time    AS 记录时间
  FROM operation_log
 ORDER BY log_id DESC
 LIMIT 3;

/* 清理：撤销本次验证新增的报名记录，保持基础数据集与课程设计文档一致
   （审计日志属于历史留痕，按数据治理要求保留，不做删除） */
DELETE FROM activity_registration WHERE activity_id = 14 AND student_id = 5;
DELETE FROM activity_registration WHERE activity_id = 13 AND student_id = 3;
SELECT '已清理验证数据，基础数据集恢复' AS 验证说明, ROW_COUNT() AS 删除行数;
