# -*- coding: utf-8 -*-
import pymysql, io, json
c = pymysql.connect(host='127.0.0.1', port=3399, user='root', password='', charset='utf8mb4', autocommit=True)
cur = c.cursor()
cur.execute("USE heritage_db")
out = []

def q(label, sql):
    cur.execute(sql)
    rows = cur.fetchall()
    out.append('### ' + label)
    for r in rows:
        out.append('   ' + ' | '.join('' if v is None else str(v) for v in r))

q('表行数', """SELECT 'heritage_category' t, COUNT(*) n FROM heritage_category
UNION ALL SELECT 'heritage_project', COUNT(*) FROM heritage_project
UNION ALL SELECT 'inheritor', COUNT(*) FROM inheritor
UNION ALL SELECT 'project_inheritor', COUNT(*) FROM project_inheritor
UNION ALL SELECT 'heritage_work', COUNT(*) FROM heritage_work
UNION ALL SELECT 'student', COUNT(*) FROM student
UNION ALL SELECT 'research_course', COUNT(*) FROM research_course
UNION ALL SELECT 'research_activity', COUNT(*) FROM research_activity
UNION ALL SELECT 'activity_registration', COUNT(*) FROM activity_registration
UNION ALL SELECT 'heritage_project_archive', COUNT(*) FROM heritage_project_archive
UNION ALL SELECT 'operation_log', COUNT(*) FROM operation_log""")

q('对象数量', """SELECT
 (SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='heritage_db' AND TABLE_TYPE='BASE TABLE') 基础表,
 (SELECT COUNT(*) FROM information_schema.VIEWS WHERE TABLE_SCHEMA='heritage_db') 视图,
 (SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA='heritage_db') 触发器,
 (SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='heritage_db') 函数与存储过程,
 (SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA='heritage_db' AND INDEX_NAME<>'PRIMARY') 二级索引,
 (SELECT COUNT(DISTINCT CONSTRAINT_NAME) FROM information_schema.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA='heritage_db') 约束总数,
 (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA='heritage_db' AND CONSTRAINT_TYPE='CHECK') CHECK约束,
 (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA='heritage_db' AND CONSTRAINT_TYPE='FOREIGN KEY') 外键约束,
 (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA='heritage_db' AND CONSTRAINT_TYPE='UNIQUE') 唯一约束""")

q('各表 CHECK 约束', """SELECT TABLE_NAME, CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS
WHERE TABLE_SCHEMA='heritage_db' AND CONSTRAINT_TYPE='CHECK' ORDER BY TABLE_NAME""")
q('外键清单', """SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA='heritage_db' AND REFERENCED_TABLE_NAME IS NOT NULL ORDER BY TABLE_NAME""")
q('索引清单', """SELECT TABLE_NAME, INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) cols, NON_UNIQUE
FROM information_schema.STATISTICS WHERE TABLE_SCHEMA='heritage_db' AND INDEX_NAME<>'PRIMARY'
GROUP BY TABLE_NAME, INDEX_NAME, NON_UNIQUE ORDER BY TABLE_NAME""")
q('例程清单', """SELECT ROUTINE_TYPE, ROUTINE_NAME FROM information_schema.ROUTINES
WHERE ROUTINE_SCHEMA='heritage_db' ORDER BY ROUTINE_TYPE, ROUTINE_NAME""")
q('触发器清单', """SELECT TRIGGER_NAME, EVENT_MANIPULATION, EVENT_OBJECT_TABLE, ACTION_TIMING
FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA='heritage_db' ORDER BY EVENT_OBJECT_TABLE""")

q('项目热度排行', """SELECT project_code, project_name, project_level, project_status, heat_score,
 inheritor_count, work_count, digitized_work_count, activity_count, registration_count
 FROM v_project_heat_index ORDER BY heat_score DESC""")
q('分类上卷', """WITH RECURSIVE ct AS (SELECT category_id, category_id root_id FROM heritage_category WHERE parent_id IS NULL
 UNION ALL SELECT c.category_id, ct.root_id FROM heritage_category c JOIN ct ON ct.category_id=c.parent_id),
 ps AS (SELECT p.project_id, p.category_id,
  (SELECT COUNT(*) FROM project_inheritor pi WHERE pi.project_id=p.project_id) ic,
  (SELECT COUNT(*) FROM heritage_work w WHERE w.project_id=p.project_id) wc,
  (SELECT COUNT(*) FROM activity_registration ar JOIN research_activity ra ON ra.activity_id=ar.activity_id
     JOIN research_course rc ON rc.course_id=ra.course_id WHERE rc.project_id=p.project_id
     AND ar.register_status IN ('已报名','已录取')) rcc FROM heritage_project p)
 SELECT root.category_name, COUNT(DISTINCT ct.category_id), COUNT(DISTINCT ps.project_id),
 COALESCE(SUM(ps.ic),0), COALESCE(SUM(ps.wc),0), COALESCE(SUM(ps.rcc),0)
 FROM ct JOIN heritage_category root ON root.category_id=ct.root_id
 LEFT JOIN ps ON ps.category_id=ct.category_id GROUP BY root.category_id, root.category_name
 ORDER BY 3 DESC""")
q('活动报名情况', """SELECT activity_code, activity_name, activity_status, plan_capacity, confirmed_total,
 remaining_quota, enroll_rate FROM v_activity_enrollment ORDER BY start_time""")
q('数字化进度', """SELECT project_code, project_name, total_works, collected_works, modeled_works, stored_works, collect_rate
 FROM v_digitization_progress ORDER BY collect_rate DESC, project_code""")
out.append('### 推荐结果-学生1(张思远,兴趣=传统戏剧)')
cur.callproc('sp_recommend_activity', (1, 5))
for r in cur.fetchall():
    out.append('   ' + ' | '.join('' if v is None else str(v) for v in r))

io.open('_stats.txt','w',encoding='utf-8').write('\n'.join(out))
c.close()
print('ok')
