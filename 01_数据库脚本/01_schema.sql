/* =====================================================================================
   地方非遗文化数字化保护数据库管理系统  ——  数据库结构定义脚本
   -------------------------------------------------------------------------------------
   文件     : 01_schema.sql
   数据库   : MySQL 8.0.16+  (InnoDB / utf8mb4)
   内容     : 建库、建表、主外键、非空 / 唯一 / 检查约束、索引、表注释
   说明     : 严格执行第三范式（3NF）；所有业务实体关系通过主外键显式建模；
              派生数据（如活动报名人数）不落库存储，统一由视图实时计算，杜绝数据冗余。
   执行顺序 : 01_schema.sql -> 02_init_data.sql -> 03_views.sql
              -> 04_functions_procedures.sql -> 05_triggers.sql -> 06_core_queries.sql
   ===================================================================================== */

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

DROP DATABASE IF EXISTS heritage_db;
CREATE DATABASE heritage_db
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_general_ci;
USE heritage_db;

SET FOREIGN_KEY_CHECKS = 1;

/* -------------------------------------------------------------------------------------
   表 1 : heritage_category  非遗分类表（自关联层次表）
   设计要点: 国家非遗"十大类"为一级分类，其下可扩展二级门类；
             通过 parent_id 自关联表达层次结构，避免"一级名称+二级名称"在同一行冗余。
   ------------------------------------------------------------------------------------- */
CREATE TABLE heritage_category (
    category_id     INT UNSIGNED    NOT NULL AUTO_INCREMENT COMMENT '分类ID，主键',
    category_code   VARCHAR(20)     NOT NULL                COMMENT '分类编码（业务唯一）',
    category_name   VARCHAR(50)     NOT NULL                COMMENT '分类名称',
    category_level  TINYINT         NOT NULL DEFAULT 1      COMMENT '分类层级：1=一级门类，2=二级门类',
    parent_id       INT UNSIGNED    NULL                    COMMENT '父分类ID（一级分类为NULL）',
    description     VARCHAR(500)    NULL                    COMMENT '分类说明',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (category_id),
    CONSTRAINT uk_category_code  UNIQUE (category_code),
    CONSTRAINT uk_category_name  UNIQUE (category_name),
    /* 采用 RESTRICT：存在二级子分类时禁止删除一级分类，
       避免父键置空后与 ck_category_parent_consistency 约束冲突。
       同时不使用 ON UPDATE CASCADE —— 对自增代理键无实际意义，
       且 MariaDB（XAMPP/WAMP 自带）不允许在 ON UPDATE CASCADE 的外键列上定义 CHECK 约束。 */
    CONSTRAINT fk_category_parent FOREIGN KEY (parent_id)
        REFERENCES heritage_category (category_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_category_level CHECK (category_level IN (1, 2)),
    CONSTRAINT ck_category_parent_consistency CHECK (
        (category_level = 1 AND parent_id IS NULL) OR
        (category_level = 2 AND parent_id IS NOT NULL)
    )
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci
  COMMENT = '非遗分类表（十大类及二级门类）';

/* -------------------------------------------------------------------------------------
   表 2 : heritage_project  非遗项目表
   设计要点: 只存 category_id 外键，不冗余存储分类名称（满足2NF/3NF）；
             项目级别、状态用 ENUM 收敛取值，并辅以 CHECK 约束校验年份合理性。
   ------------------------------------------------------------------------------------- */
CREATE TABLE heritage_project (
    project_id      INT UNSIGNED    NOT NULL AUTO_INCREMENT COMMENT '项目ID，主键',
    project_code    VARCHAR(30)     NOT NULL                COMMENT '项目档案编号（业务唯一）',
    project_name    VARCHAR(100)    NOT NULL                COMMENT '项目名称',
    category_id     INT UNSIGNED    NOT NULL                COMMENT '所属分类ID，外键',
    project_level   ENUM('国家级','省级','市级','县级') NOT NULL COMMENT '项目级别',
    batch_no        VARCHAR(30)     NULL                    COMMENT '公布批次',
    declared_area   VARCHAR(100)    NOT NULL                COMMENT '申报地区',
    protect_unit    VARCHAR(100)    NULL                    COMMENT '保护单位',
    approval_year   SMALLINT        NULL                    COMMENT '入选年份',
    project_status  ENUM('正常','濒危','已归档') NOT NULL DEFAULT '正常' COMMENT '项目状态',
    is_digitized    TINYINT         NOT NULL DEFAULT 0      COMMENT '是否已建立数字化档案：0=否，1=是',
    project_intro   TEXT            NULL                    COMMENT '项目简介',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '建档时间',
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                                            ON UPDATE CURRENT_TIMESTAMP  COMMENT '最后更新时间',
    PRIMARY KEY (project_id),
    CONSTRAINT uk_project_code      UNIQUE (project_code),
    CONSTRAINT uk_project_name_area UNIQUE (project_name, declared_area),
    CONSTRAINT fk_project_category  FOREIGN KEY (category_id)
        REFERENCES heritage_category (category_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_project_year      CHECK (approval_year IS NULL OR approval_year BETWEEN 1949 AND 2100),
    CONSTRAINT ck_project_digitized CHECK (is_digitized IN (0, 1))
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci
  COMMENT = '非遗项目（名录）表';

CREATE INDEX idx_project_category     ON heritage_project (category_id);
CREATE INDEX idx_project_level_status ON heritage_project (project_level, project_status);

/* -------------------------------------------------------------------------------------
   表 3 : inheritor  非遗传承人表（活态传承的核心主体）
   设计要点: 身份证号、手机号作为自然键设置唯一约束；
             以 CHECK + REGEXP 校验证件与手机号格式，保证数据质量。
   ------------------------------------------------------------------------------------- */
CREATE TABLE inheritor (
    inheritor_id    INT UNSIGNED    NOT NULL AUTO_INCREMENT COMMENT '传承人ID，主键',
    inheritor_code  VARCHAR(30)     NOT NULL                COMMENT '传承人档案编号（业务唯一）',
    inheritor_name  VARCHAR(50)     NOT NULL                COMMENT '姓名',
    gender          ENUM('男','女') NOT NULL                COMMENT '性别',
    birth_date      DATE            NOT NULL                COMMENT '出生日期',
    ethnicity       VARCHAR(30)     NOT NULL DEFAULT '汉族'  COMMENT '民族',
    id_card         VARCHAR(18)     NOT NULL                COMMENT '身份证号',
    phone           VARCHAR(20)     NOT NULL                COMMENT '联系电话',
    region          VARCHAR(100)    NOT NULL                COMMENT '所属地区',
    inheritor_level ENUM('国家级','省级','市级','县级') NOT NULL COMMENT '传承人级别',
    recognized_year SMALLINT        NULL                    COMMENT '认定年份',
    skill_years     TINYINT UNSIGNED NULL                   COMMENT '从艺年限',
    health_status   ENUM('健康','一般','较差','已故') NOT NULL DEFAULT '健康' COMMENT '健康状况',
    introduction    TEXT            NULL                    COMMENT '人物简介 / 技艺特征',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '建档时间',
    PRIMARY KEY (inheritor_id),
    CONSTRAINT uk_inheritor_code   UNIQUE (inheritor_code),
    CONSTRAINT uk_inheritor_idcard UNIQUE (id_card),
    CONSTRAINT uk_inheritor_phone  UNIQUE (phone),
    CONSTRAINT ck_inheritor_idcard CHECK (CHAR_LENGTH(id_card) IN (15, 18)),
    CONSTRAINT ck_inheritor_phone  CHECK (phone REGEXP '^1[3-9][0-9]{9}$'),
    CONSTRAINT ck_inheritor_birth  CHECK (birth_date >= '1900-01-01'),
    CONSTRAINT ck_inheritor_years  CHECK (skill_years IS NULL OR skill_years <= 100)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci
  COMMENT = '非遗代表性传承人表';

CREATE INDEX idx_inheritor_level  ON inheritor (inheritor_level, health_status);
CREATE INDEX idx_inheritor_region ON inheritor (region);

/* -------------------------------------------------------------------------------------
   表 4 : project_inheritor  项目—传承人 关联表（多对多中间表）
   设计要点: 复合唯一键 (project_id, inheritor_id) 保证同一"项目-传承人"关系不重复；
             关系本身的属性（传承角色、起始年份、是否主传承人）落在关联表上，符合3NF。
   ------------------------------------------------------------------------------------- */
CREATE TABLE project_inheritor (
    relation_id     INT UNSIGNED    NOT NULL AUTO_INCREMENT COMMENT '关系ID，主键',
    project_id      INT UNSIGNED    NOT NULL                COMMENT '项目ID，外键',
    inheritor_id    INT UNSIGNED    NOT NULL                COMMENT '传承人ID，外键',
    relation_role   ENUM('代表性传承人','一般传承人','学徒') NOT NULL DEFAULT '代表性传承人'
                                                            COMMENT '在项目中的角色',
    start_year      SMALLINT        NULL                    COMMENT '传承起始年份',
    is_master       TINYINT         NOT NULL DEFAULT 0      COMMENT '是否师承主传承人：0=否，1=是',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '登记时间',
    PRIMARY KEY (relation_id),
    CONSTRAINT uk_project_inheritor UNIQUE (project_id, inheritor_id),
    CONSTRAINT fk_pi_project   FOREIGN KEY (project_id)
        REFERENCES heritage_project (project_id)
        ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT fk_pi_inheritor FOREIGN KEY (inheritor_id)
        REFERENCES inheritor (inheritor_id)
        ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT ck_pi_master CHECK (is_master IN (0, 1)),
    CONSTRAINT ck_pi_start  CHECK (start_year IS NULL OR start_year BETWEEN 1900 AND 2100)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci
  COMMENT = '项目—传承人关联表（多对多）';

CREATE INDEX idx_pi_inheritor ON project_inheritor (inheritor_id);

/* -------------------------------------------------------------------------------------
   表 5 : student  学生表（研学服务对象）
   设计要点: 学号唯一；interest_category_id 记录兴趣门类，为"研学活动推荐算法"提供内容特征。
   ------------------------------------------------------------------------------------- */
CREATE TABLE student (
    student_id           INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '学生ID，主键',
    student_no           VARCHAR(20)  NOT NULL               COMMENT '学号（唯一）',
    student_name         VARCHAR(50)  NOT NULL               COMMENT '姓名',
    gender               ENUM('男','女') NOT NULL            COMMENT '性别',
    grade                VARCHAR(20)  NOT NULL               COMMENT '年级',
    class_name           VARCHAR(30)  NULL                   COMMENT '班级',
    school_name          VARCHAR(100) NOT NULL               COMMENT '学校名称',
    phone                VARCHAR(20)  NULL                   COMMENT '联系电话',
    interest_category_id INT UNSIGNED NULL                   COMMENT '感兴趣的非遗门类，外键',
    created_at           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '注册时间',
    PRIMARY KEY (student_id),
    CONSTRAINT uk_student_no UNIQUE (student_no),
    CONSTRAINT fk_student_interest FOREIGN KEY (interest_category_id)
        REFERENCES heritage_category (category_id)
        ON DELETE SET NULL ON UPDATE RESTRICT,
    CONSTRAINT ck_student_phone CHECK (phone IS NULL OR phone REGEXP '^1[3-9][0-9]{9}$')
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci
  COMMENT = '学生（研学参与人）表';

CREATE INDEX idx_student_school ON student (school_name);

/* -------------------------------------------------------------------------------------
   表 6 : heritage_work  非遗作品（实物/数字藏品）表
   设计要点: 同时关联"所属项目"与"创作传承人"，构成两条一对多关系；
             digitize_status 刻画数字化保护流程进度，是"文化数字化"主题的核心字段。
   ------------------------------------------------------------------------------------- */
CREATE TABLE heritage_work (
    work_id         INT UNSIGNED    NOT NULL AUTO_INCREMENT COMMENT '作品ID，主键',
    work_code       VARCHAR(30)     NOT NULL                COMMENT '作品编号（唯一）',
    work_name       VARCHAR(100)    NOT NULL                COMMENT '作品名称',
    project_id      INT UNSIGNED    NOT NULL                COMMENT '所属非遗项目ID，外键',
    creator_id      INT UNSIGNED    NOT NULL                COMMENT '创作（代表性）传承人ID，外键',
    create_year     SMALLINT        NULL                    COMMENT '创作年份',
    material        VARCHAR(50)     NULL                    COMMENT '材质',
    spec_size       VARCHAR(50)     NULL                    COMMENT '规格尺寸',
    storage_place   VARCHAR(100)    NULL                    COMMENT '实物存放地点',
    image_url       VARCHAR(255)    NULL                    COMMENT '数字化影像文件路径',
    digitize_status ENUM('未数字化','已采集','已建模','已入库') NOT NULL DEFAULT '未数字化'
                                                            COMMENT '数字化保护进度',
    in_collection   TINYINT         NOT NULL DEFAULT 0      COMMENT '是否已入藏：0=否，1=是',
    work_desc       TEXT            NULL                    COMMENT '作品描述',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '归档时间',
    PRIMARY KEY (work_id),
    CONSTRAINT uk_work_code UNIQUE (work_code),
    CONSTRAINT fk_work_project FOREIGN KEY (project_id)
        REFERENCES heritage_project (project_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_work_creator FOREIGN KEY (creator_id)
        REFERENCES inheritor (inheritor_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_work_year CHECK (create_year IS NULL OR create_year BETWEEN 1900 AND 2100),
    CONSTRAINT ck_work_collection CHECK (in_collection IN (0, 1))
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci
  COMMENT = '非遗作品（数字化藏品）表';

CREATE INDEX idx_work_project ON heritage_work (project_id, digitize_status);
CREATE INDEX idx_work_creator ON heritage_work (creator_id);

/* -------------------------------------------------------------------------------------
   表 7 : research_course  非遗研学课程表
   设计要点: 课程绑定"非遗项目"与"主讲传承人"，是"非遗进校园"的载体；
             同时把课程作为活动的模板（一个课程可开办多场活动），避免活动表冗余课程信息。
   ------------------------------------------------------------------------------------- */
CREATE TABLE research_course (
    course_id     INT UNSIGNED    NOT NULL AUTO_INCREMENT COMMENT '课程ID，主键',
    course_code   VARCHAR(30)     NOT NULL                COMMENT '课程编号（唯一）',
    course_name   VARCHAR(100)    NOT NULL                COMMENT '课程名称',
    project_id    INT UNSIGNED    NOT NULL                COMMENT '关联非遗项目ID，外键',
    teacher_id    INT UNSIGNED    NOT NULL                COMMENT '主讲传承人ID，外键',
    suit_grade    VARCHAR(50)     NOT NULL                COMMENT '适用学段',
    class_hours   TINYINT UNSIGNED NOT NULL               COMMENT '课时数',
    course_type   ENUM('体验实践','理论讲授','参观研学','综合实践') NOT NULL COMMENT '课程类型',
    course_status ENUM('筹备中','已上线','已停开') NOT NULL DEFAULT '筹备中' COMMENT '课程状态',
    course_intro  TEXT            NULL                    COMMENT '课程简介 / 教学目标',
    created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (course_id),
    CONSTRAINT uk_course_code UNIQUE (course_code),
    CONSTRAINT fk_course_project FOREIGN KEY (project_id)
        REFERENCES heritage_project (project_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_course_teacher FOREIGN KEY (teacher_id)
        REFERENCES inheritor (inheritor_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_course_hours CHECK (class_hours BETWEEN 1 AND 40)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci
  COMMENT = '非遗研学课程表（非遗进校园）';

CREATE INDEX idx_course_project ON research_course (project_id, course_status);
CREATE INDEX idx_course_teacher ON research_course (teacher_id);

/* -------------------------------------------------------------------------------------
   表 8 : research_activity  研学活动表
   设计要点: 活动隶属于课程（多对一）；时间关系由 CHECK 约束在数据库层强制保证；
             "已报名人数"属于派生数据，不存储，由视图 v_activity_enrollment 实时计算。
   ------------------------------------------------------------------------------------- */
CREATE TABLE research_activity (
    activity_id     INT UNSIGNED    NOT NULL AUTO_INCREMENT COMMENT '活动ID，主键',
    activity_code   VARCHAR(30)     NOT NULL                COMMENT '活动编号（唯一）',
    activity_name   VARCHAR(100)    NOT NULL                COMMENT '活动名称',
    course_id       INT UNSIGNED    NOT NULL                COMMENT '所属课程ID，外键',
    activity_place  VARCHAR(150)    NOT NULL                COMMENT '活动地点',
    start_time      DATETIME        NOT NULL                COMMENT '活动开始时间',
    end_time        DATETIME        NOT NULL                COMMENT '活动结束时间',
    deadline_time   DATETIME        NOT NULL                COMMENT '报名截止时间',
    plan_capacity   SMALLINT UNSIGNED NOT NULL              COMMENT '计划招募人数',
    organizer       VARCHAR(100)    NOT NULL                COMMENT '组织单位',
    activity_status ENUM('报名中','已满员','已结束','已取消') NOT NULL DEFAULT '报名中' COMMENT '活动状态',
    remark          VARCHAR(500)    NULL                    COMMENT '备注',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (activity_id),
    CONSTRAINT uk_activity_code UNIQUE (activity_code),
    CONSTRAINT fk_activity_course FOREIGN KEY (course_id)
        REFERENCES research_course (course_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT ck_activity_capacity CHECK (plan_capacity > 0),
    CONSTRAINT ck_activity_time     CHECK (end_time > start_time AND deadline_time <= start_time)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci
  COMMENT = '研学活动表';

CREATE INDEX idx_activity_course       ON research_activity (course_id, activity_status);
CREATE INDEX idx_activity_status_start ON research_activity (activity_status, start_time);

/* -------------------------------------------------------------------------------------
   表 9 : activity_registration  学生—活动 报名关联表（多对多中间表）
   设计要点: 复合唯一键阻止重复报名；报名状态与出勤状态、成绩等"报名记录自身属性"落在关联表；
             成绩由 CHECK 限定 0~100 区间。
   ------------------------------------------------------------------------------------- */
CREATE TABLE activity_registration (
    registration_id   INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '报名ID，主键',
    activity_id       INT UNSIGNED NOT NULL                COMMENT '活动ID，外键',
    student_id        INT UNSIGNED NOT NULL                COMMENT '学生ID，外键',
    register_time     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '报名时间',
    register_status   ENUM('已报名','已录取','候补','已取消') NOT NULL DEFAULT '已报名' COMMENT '报名状态',
    attendance_status ENUM('未开始','已签到','请假','缺勤') NOT NULL DEFAULT '未开始' COMMENT '出勤状态',
    checkin_time      DATETIME     NULL                    COMMENT '签到时间',
    score             DECIMAL(5,2) NULL                    COMMENT '实践成绩（0-100）',
    comment           VARCHAR(255) NULL                    COMMENT '研学评价 / 教师评语',
    PRIMARY KEY (registration_id),
    CONSTRAINT uk_activity_student UNIQUE (activity_id, student_id),
    CONSTRAINT fk_reg_activity FOREIGN KEY (activity_id)
        REFERENCES research_activity (activity_id)
        ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT fk_reg_student FOREIGN KEY (student_id)
        REFERENCES student (student_id)
        ON DELETE CASCADE ON UPDATE RESTRICT,
    CONSTRAINT ck_reg_score CHECK (score IS NULL OR (score >= 0 AND score <= 100))
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci
  COMMENT = '研学活动报名记录表（多对多）';

CREATE INDEX idx_reg_student  ON activity_registration (student_id, register_status);
CREATE INDEX idx_reg_activity ON activity_registration (activity_id, register_status);

/* -------------------------------------------------------------------------------------
   表 10 : heritage_project_archive  非遗项目归档快照表
   设计要点: 数据"归档"而非"删除"。归档时冻结项目多维统计快照，满足文化遗产数据
             长期留存与可追溯要求；与 heritage_project 构成归档业务支撑表。
   ------------------------------------------------------------------------------------- */
CREATE TABLE heritage_project_archive (
    archive_id      INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '归档ID，主键',
    project_id      INT UNSIGNED NOT NULL                COMMENT '被归档项目ID',
    project_code    VARCHAR(30)  NOT NULL                COMMENT '项目编号快照',
    project_name    VARCHAR(100) NOT NULL                COMMENT '项目名称快照',
    category_name   VARCHAR(50)  NOT NULL                COMMENT '分类名称快照',
    project_level   VARCHAR(10)  NOT NULL                COMMENT '项目级别快照',
    declared_area   VARCHAR(100) NOT NULL                COMMENT '申报地区快照',
    inheritor_count INT UNSIGNED NOT NULL DEFAULT 0      COMMENT '归档时传承人数量',
    work_count      INT UNSIGNED NOT NULL DEFAULT 0      COMMENT '归档时作品数量',
    activity_count  INT UNSIGNED NOT NULL DEFAULT 0      COMMENT '归档时研学活动场次',
    archive_reason  VARCHAR(255) NULL                    COMMENT '归档原因',
    archived_by     VARCHAR(50)  NOT NULL DEFAULT 'system' COMMENT '归档操作人',
    archive_time    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '归档时间',
    PRIMARY KEY (archive_id),
    CONSTRAINT ck_archive_count CHECK (inheritor_count >= 0 AND work_count >= 0 AND activity_count >= 0)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci
  COMMENT = '非遗项目归档快照表';

CREATE INDEX idx_archive_project ON heritage_project_archive (project_id);

/* -------------------------------------------------------------------------------------
   表 11 : operation_log  数据库操作审计日志表
   设计要点: 由触发器自动写入，记录关键业务表的增删改轨迹，满足数据治理与溯源要求。
   ------------------------------------------------------------------------------------- */
CREATE TABLE operation_log (
    log_id      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '日志ID，主键',
    table_name  VARCHAR(50)  NOT NULL                COMMENT '目标表名',
    action_type VARCHAR(20)  NOT NULL                COMMENT '操作类型：INSERT/UPDATE/DELETE',
    record_id   BIGINT UNSIGNED NULL                 COMMENT '业务记录主键值',
    action_desc VARCHAR(500) NULL                    COMMENT '操作描述',
    operator    VARCHAR(50)  NOT NULL DEFAULT 'system' COMMENT '操作人',
    log_time    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录时间',
    PRIMARY KEY (log_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci
  COMMENT = '业务操作审计日志表';

CREATE INDEX idx_log_table_time ON operation_log (table_name, log_time);

/* =============================== 结构统计（执行后核对） =============================== */
SELECT TABLE_NAME          AS '表名',
       TABLE_COMMENT       AS '表注释',
       TABLE_ROWS          AS '估算行数'
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'heritage_db'
ORDER BY TABLE_NAME;
