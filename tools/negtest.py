# -*- coding: utf-8 -*-
"""验证完整性约束与触发器的"反例"是否被数据库正确拦截。"""
import pymysql

NEG = [
 ("H-1 唯一约束：重复项目编号",
  "INSERT INTO heritage_project (project_code, project_name, category_id, project_level, declared_area) VALUES ('SX-001','重复编号测试',4,'省级','浙江省绍兴市')"),
 ("H-2 检查约束：手机号格式非法",
  "INSERT INTO inheritor (inheritor_code,inheritor_name,gender,birth_date,id_card,phone,region,inheritor_level) VALUES ('SXR-999','测试人员','男','1980-01-01','330602198001019999','12345','浙江省绍兴市','县级')"),
 ("H-3 外键约束：引用不存在的分类",
  "INSERT INTO heritage_project (project_code, project_name, category_id, project_level, declared_area) VALUES ('SX-999','外键测试项目',999,'省级','浙江省绍兴市')"),
 ("H-4 检查约束：结束时间早于开始时间",
  "INSERT INTO research_activity (activity_code,activity_name,course_id,activity_place,start_time,end_time,deadline_time,plan_capacity,organizer) VALUES ('A-TEST-01','时间校验测试',1,'测试地点','2026-10-01 16:00:00','2026-10-01 09:00:00','2026-09-25 17:00:00',10,'测试单位')"),
 ("H-5 触发器：向已结束活动报名",
  "INSERT INTO activity_registration (activity_id, student_id, register_status, attendance_status) VALUES (1, 7, '已报名','未开始')"),
 ("H-6 触发器：超过报名截止时间",
  "INSERT INTO activity_registration (activity_id, student_id, register_status, attendance_status) VALUES (13, 3, '已报名','未开始')"),
 ("H-7 触发器：名额已满仍以已报名提交",
  "INSERT INTO activity_registration (activity_id, student_id, register_status, attendance_status) VALUES (16, 5, '已报名','未开始')"),
 ("H-8 触发器：计划人数低于已报名人数",
  "UPDATE research_activity SET plan_capacity = 5 WHERE activity_id = 13"),
 ("H-9 唯一约束（多对多）：重复报名同一活动",
  "INSERT INTO activity_registration (activity_id, student_id, register_status) VALUES (14, 7, '候补')"),
 ("H-10 检查约束：分类层级与父级不一致",
  "INSERT INTO heritage_category (category_code, category_name, category_level, parent_id) VALUES ('TST-01','层级测试分类',1,8)"),
 ("H-11 检查约束：学生成绩超出 0-100",
  "INSERT INTO activity_registration (activity_id, student_id, register_status, score) VALUES (15, 15, '已报名', 150)"),
 ("H-12 检查约束：报名截止晚于活动开始",
  "INSERT INTO research_activity (activity_code,activity_name,course_id,activity_place,start_time,end_time,deadline_time,plan_capacity,organizer) VALUES ('A-TEST-02','截止校验',1,'地点','2026-10-01 09:00:00','2026-10-01 17:00:00','2026-10-05 17:00:00',10,'单位')"),
]

POS = [
 ("P-1 满员活动以候补登记应成功",
  "INSERT INTO activity_registration (activity_id, student_id, register_status, attendance_status) VALUES (13, 5, '候补','未开始')"),
 ("P-2 报名中活动正常报名应成功",
  "INSERT INTO activity_registration (activity_id, student_id, register_status, attendance_status) VALUES (15, 15, '已报名','未开始')"),
]

c = pymysql.connect(host='127.0.0.1', port=3399, user='root', password='', charset='utf8mb4', autocommit=True)
cur = c.cursor()
cur.execute("USE heritage_db")

out = []
out.append('===== 反例（应全部被拦截）=====')
blocked = 0
for name, sql in NEG:
    try:
        cur.execute(sql)
        out.append('  [未拦截!] %s' % name)
    except Exception as e:
        blocked += 1
        out.append('  [已拦截] %s\n            => %s' % (name, str(e)[:180]))
out.append('反例拦截率: %d / %d' % (blocked, len(NEG)))

out.append('')
out.append('===== 正例（应全部成功）=====')
passed = 0
for name, sql in POS:
    try:
        cur.execute(sql)
        passed += 1
        out.append('  [成功] %s' % name)
    except Exception as e:
        out.append('  [失败!] %s => %s' % (name, str(e)[:180]))
out.append('正例通过率: %d / %d' % (passed, len(POS)))

# 清理正例产生的数据
cur.execute("DELETE FROM activity_registration WHERE (activity_id=13 AND student_id=5) OR (activity_id=15 AND student_id=15)")
out.append('')
out.append('已清理正例数据，基础数据集恢复（报名总数应为 79）：')
cur.execute("SELECT COUNT(*) FROM activity_registration")
out.append('  报名记录数 = %s' % cur.fetchone()[0])

c.close()
open('_negtest.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('done')
