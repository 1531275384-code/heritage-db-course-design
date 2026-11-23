# -*- coding: utf-8 -*-
import pymysql
c = pymysql.connect(host='127.0.0.1', port=3399, user='root', password='', charset='utf8mb4', autocommit=True)
cur = c.cursor()
for sql, t in [
    ("SELECT project_id, project_code, is_digitized FROM heritage_project ORDER BY project_id", "digitized"),
    ("SELECT COUNT(*) FROM heritage_work WHERE work_code='W-TEST-001'", "test work残留"),
    ("SELECT COUNT(*) FROM activity_registration", "报名总数"),
    ("SELECT activity_id, student_id FROM activity_registration WHERE (activity_id=14 AND student_id=5) OR (activity_id=13 AND student_id=3) OR (activity_id=12 AND student_id=6)", "验证残留"),
    ("SELECT COUNT(*) FROM project_inheritor WHERE relation_id IS NOT NULL", "关系总数"),
]:
    cur.execute(sql)
    print(t, ':', cur.fetchall())
c.close()
