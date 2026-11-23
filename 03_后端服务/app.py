# -*- coding: utf-8 -*-
"""
地方非遗文化数字化保护数据库管理系统 —— 后端服务入口
==========================================================================
技术栈 : Python 3 + Flask + PyMySQL + MariaDB/MySQL 8.0
职责   : 以 RESTful API 形式对外暴露数据库能力，并在服务层叠加
         "非遗活态传承热度评估"与"研学活动混合推荐"两个算法模型。

启动方式：
    cd 03_后端服务
    python app.py
默认监听 http://127.0.0.1:5000 ，同时托管 05_前端页面 下的静态页面。

接口清单（详见 README.md）：
    GET  /api/health                     服务与数据库健康检查
    GET  /api/overview                   全局 KPI 概览
    GET  /api/meta/enums                 枚举字典
    GET  /api/categories                 分类层次树
    GET  /api/projects                   非遗项目列表（筛选 + 分页）
    GET  /api/projects/<id>              项目完整档案（存储过程多结果集）
    GET  /api/projects/heat              项目热度指数排行（算法模型一）
    GET  /api/inheritors                 传承人列表
    GET  /api/inheritors/<id>            传承人详情
    GET  /api/works                      非遗作品列表
    GET  /api/courses                    研学课程列表
    GET  /api/activities                 研学活动列表
    GET  /api/activities/<id>            活动详情 + 报名名单
    POST /api/registrations              学生报名（事务 + 行锁 + 触发器）
    GET  /api/students                   学生列表
    GET  /api/students/<id>/record       学生学习档案
    GET  /api/algorithms/recommend       研学活动推荐（算法模型二）
    GET  /api/algorithms/heat            热度指数计算明细
    GET  /api/reports/activity           研学活动周期报表（中文报表）
    GET  /api/reports/category           门类汇总统计报表（游标实现）
    GET  /api/reports/digitization       数字化保护进度报表
    GET  /api/logs                       操作审计日志
==========================================================================
"""

import os

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

import algorithms
import db
from config import FRONTEND_DIR, ServerConfig

app = Flask(__name__, static_folder=None)
CORS(app)   # 允许前端页面跨域调试


# ======================================================================
# 通用工具
# ======================================================================
def ok(data, **extra):
    body = {'code': 0, 'success': True, 'data': data}
    body.update(extra)
    return jsonify(body)


def fail(msg, code=1):
    return jsonify({'code': code, 'success': False, 'message': msg}), 200


def rename(rows, mapping):
    """按映射表把中文列名改写为英文键名"""
    out = []
    for row in rows:
        out.append(dict((mapping.get(k, k), v) for k, v in row.items()))
    return out


def paging():
    """解析分页参数"""
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1
    try:
        size = min(200, max(1, int(request.args.get('size', 20))))
    except ValueError:
        size = 20
    return page, size


# ======================================================================
# 静态页面托管（同一端口即可访问前端，便于答辩演示）
# ======================================================================
@app.route('/')
def index_page():
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/<path:filename>')
def static_page(filename):
    return send_from_directory(FRONTEND_DIR, filename)


# ======================================================================
# 基础：健康检查 / 概览 / 元数据
# ======================================================================
@app.route('/api/health')
def api_health():
    try:
        info = db.server_info()
        tables = db.scalar(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'", (db.DBConfig.DATABASE,))
        return ok({
            'service': 'running',
            'database': 'connected',
            'db_version': info.get('v'),
            'db_name': info.get('d'),
            'db_time': info.get('t'),
            'base_table_count': tables,
        })
    except Exception as exc:
        return fail('服务异常：%s' % exc)


@app.route('/api/overview')
def api_overview():
    """全局 KPI：一屏概览非遗数字化保护成效"""
    try:
        row = db.query_one("""
            SELECT (SELECT COUNT(*) FROM heritage_project WHERE project_status <> '已归档') AS project_total,
                   (SELECT COUNT(*) FROM heritage_project WHERE project_status = '濒危')      AS endangered_total,
                   (SELECT COUNT(*) FROM heritage_project WHERE is_digitized = 1)             AS project_digitized,
                   (SELECT COUNT(*) FROM inheritor)                                          AS inheritor_total,
                   (SELECT COUNT(*) FROM inheritor WHERE health_status = '已故')              AS inheritor_deceased,
                   (SELECT COUNT(*) FROM heritage_work)                                       AS work_total,
                   (SELECT COUNT(*) FROM heritage_work WHERE digitize_status <> '未数字化')   AS work_collected,
                   (SELECT COUNT(*) FROM heritage_work WHERE digitize_status = '已入库')      AS work_stored,
                   (SELECT COUNT(*) FROM research_course)                                     AS course_total,
                   (SELECT COUNT(*) FROM research_activity)                                   AS activity_total,
                   (SELECT COUNT(*) FROM research_activity WHERE activity_status = '报名中')  AS activity_open,
                   (SELECT COUNT(*) FROM activity_registration
                     WHERE register_status IN ('已报名','已录取'))                            AS registration_total,
                   (SELECT COUNT(*) FROM student)                                             AS student_total
        """)
        level_rows = db.query("""
            SELECT project_level AS level, COUNT(*) AS cnt
              FROM heritage_project WHERE project_status <> '已归档'
             GROUP BY project_level ORDER BY FIELD(project_level,'国家级','省级','市级','县级')
        """)
        cat_rows = db.query("""
            SELECT COALESCE(pc.category_name, c.category_name) AS category,
                   COUNT(DISTINCT p.project_id) AS project_count
              FROM heritage_project p
              JOIN heritage_category c  ON c.category_id = p.category_id
              LEFT JOIN heritage_category pc ON pc.category_id = c.parent_id
             WHERE p.project_status <> '已归档'
             GROUP BY COALESCE(pc.category_name, c.category_name)
             ORDER BY project_count DESC
        """)
        trend_rows = db.query("""
            SELECT DATE_FORMAT(start_time, '%Y-%m') AS month,
                   COUNT(*) AS activity_count,
                   SUM(plan_capacity) AS plan_total
              FROM research_activity
             WHERE start_time IS NOT NULL
             GROUP BY DATE_FORMAT(start_time, '%Y-%m')
             ORDER BY month
        """)
        return ok({
            'kpi': row,
            'level_distribution': level_rows,
            'category_distribution': cat_rows,
            'activity_trend': trend_rows,
        })
    except Exception as exc:
        return fail(str(exc))


@app.route('/api/meta/enums')
def api_enums():
    """前端筛选下拉所需的枚举字典（与数据库 ENUM 定义保持一致）"""
    return ok({
        'project_level': ['国家级', '省级', '市级', '县级'],
        'project_status': ['正常', '濒危', '已归档'],
        'inheritor_level': ['国家级', '省级', '市级', '县级'],
        'health_status': ['健康', '一般', '较差', '已故'],
        'digitize_status': ['未数字化', '已采集', '已建模', '已入库'],
        'course_type': ['体验实践', '理论讲授', '参观研学', '综合实践'],
        'course_status': ['筹备中', '已上线', '已停开'],
        'activity_status': ['报名中', '已满员', '已结束', '已取消'],
        'register_status': ['已报名', '已录取', '候补', '已取消'],
        'attendance_status': ['未开始', '已签到', '请假', '缺勤'],
    })


@app.route('/api/categories')
def api_categories():
    """分类层次树（一级门类 + 二级门类）"""
    try:
        rows = db.query("""
            SELECT c1.category_id, c1.category_code, c1.category_name,
                   c2.category_id AS child_id, c2.category_code AS child_code,
                   c2.category_name AS child_name
              FROM heritage_category c1
              LEFT JOIN heritage_category c2 ON c2.parent_id = c1.category_id
             WHERE c1.category_level = 1
             ORDER BY c1.category_id, c2.category_id
        """)
        tree = {}
        for r in rows:
            node = tree.setdefault(r['category_id'], {
                'category_id': r['category_id'], 'category_code': r['category_code'],
                'category_name': r['category_name'], 'children': []})
            if r['child_id']:
                node['children'].append({'category_id': r['child_id'],
                                         'category_code': r['child_code'],
                                         'category_name': r['child_name']})
        return ok(list(tree.values()))
    except Exception as exc:
        return fail(str(exc))


# ======================================================================
# 非遗项目
# ======================================================================
@app.route('/api/projects')
def api_projects():
    """
    非遗项目列表。
    支持 keyword（名称/编号/地区模糊）、level、status、category_id、
    digitized、sort（heat/name/year）与分页。
    """
    try:
        page, size = paging()
        where, args = ["1 = 1"], []

        kw = (request.args.get('keyword') or '').strip()
        if kw:
            where.append('(v.project_name LIKE %s OR v.project_code LIKE %s '
                         'OR v.declared_area LIKE %s OR v.protect_unit LIKE %s)')
            args += ['%%%s%%' % kw] * 4

        level = request.args.get('level')
        if level:
            where.append('v.project_level = %s')
            args.append(level)

        status = request.args.get('status')
        if status:
            where.append('v.project_status = %s')
            args.append(status)

        cid = request.args.get('category_id')
        if cid:
            # 传入一级门类时自动包含其下所有二级门类（c.parent_id 即一级门类）
            where.append('(v.category_id = %s OR c.parent_id = %s)')
            args += [cid, cid]

        if request.args.get('digitized') in ('0', '1'):
            where.append('v.is_digitized = %s')
            args.append(int(request.args.get('digitized')))

        cond = ' AND '.join(where)
        from_sql = ('FROM v_project_profile v '
                    'JOIN heritage_category c ON c.category_id = v.category_id')

        total = db.scalar('SELECT COUNT(*) ' + from_sql + ' WHERE ' + cond, args)

        order = 'heat_score DESC'
        sort = request.args.get('sort')
        if sort == 'name':
            order = 'v.project_code'
        elif sort == 'year':
            order = 'v.approval_year DESC'

        sql = ('SELECT v.project_id, v.project_code, v.project_name, v.category_id, '
               'v.category_name, c.parent_id AS parent_category_id, '
               'v.parent_category_name, v.project_level, v.batch_no, v.declared_area, '
               'v.protect_unit, v.approval_year, v.project_status, v.is_digitized, '
               'v.inheritor_count, v.work_count, v.digitized_work_count, v.course_count, '
               'v.activity_count, v.registration_count, '
               '  ROUND(v.work_count*%s + v.digitized_work_count*%s + v.activity_count*%s '
               '      + v.registration_count*%s '
               '      + CASE v.project_level WHEN %s THEN %s WHEN %s THEN %s '
               '            WHEN %s THEN %s ELSE %s END '
               '      + CASE v.project_status WHEN %s THEN %s ELSE 0 END, 2) AS heat_score '
               + from_sql + ' WHERE ' + cond +
               ' ORDER BY ' + order + ' LIMIT %s OFFSET %s')

        w = algorithms.HeatWeight
        pre = [w.W_WORK, w.W_DIGI, w.W_ACT, w.W_ENROLL,
               '国家级', w.LEVEL_WEIGHT['国家级'], '省级', w.LEVEL_WEIGHT['省级'],
               '市级', w.LEVEL_WEIGHT['市级'], w.LEVEL_WEIGHT['县级'],
               '濒危', w.ENDANGERED_BONUS]
        rows = db.query(sql, pre + args + [size, (page - 1) * size])

        for r in rows:
            r['heat_grade'], r['heat_label'] = algorithms.heat_grade(r['heat_score'])

        return ok(rows, total=total, page=page, size=size)
    except Exception as exc:
        return fail(str(exc))


PROFILE_MAP = {
    '项目编号': 'project_code', '项目名称': 'project_name', '所属门类': 'category_name',
    '上级门类': 'parent_category_name', '项目级别': 'project_level', '公布批次': 'batch_no',
    '申报地区': 'declared_area', '保护单位': 'protect_unit', '入选年份': 'approval_year',
    '项目状态': 'project_status', '数字化状态': 'digitize_text', '传承人数': 'inheritor_count',
    '作品数': 'work_count', '已入库数字化作品数': 'digitized_work_count',
    '研学课程数': 'course_count', '活动场次': 'activity_count', '累计报名人次': 'registration_count',
    '传承人编号': 'inheritor_code', '姓名': 'inheritor_name', '性别': 'gender', '年龄': 'age',
    '级别': 'inheritor_level', '所属地区': 'region', '健康状况': 'health_status',
    '项目内角色': 'relation_role', '传承起始年': 'start_year', '是否主传承人': 'is_master',
    '作品编号': 'work_code', '作品名称': 'work_name', '创作传承人': 'creator_name',
    '创作年份': 'create_year', '材质': 'material', '规格': 'spec_size',
    '数字化进度': 'digitize_status', '入藏状态': 'collection_text', '数字资源路径': 'image_url',
    '活动编号': 'activity_code', '课程名称': 'course_name', '活动名称': 'activity_name',
    '主讲传承人': 'teacher_name', '活动地点': 'activity_place', '开始时间': 'start_time',
    '计划人数': 'plan_capacity', '报名人数': 'confirmed_total', '剩余名额': 'remaining_quota',
    '报名率': 'enroll_rate', '活动状态': 'activity_status',
}


@app.route('/api/projects/<int:project_id>')
def api_project_detail(project_id):
    """调用存储过程 sp_project_full_profile，一次返回四段式完整档案"""
    try:
        exists = db.scalar('SELECT COUNT(*) FROM heritage_project WHERE project_id = %s',
                           (project_id,))
        if not exists:
            return fail('项目不存在', 404)

        sets, _ = db.callproc('sp_project_full_profile', [project_id], multi=True)
        names = ['basic', 'inheritors', 'works', 'activities']
        payload = {}
        for name, rows in zip(names, sets):
            payload[name] = rename(rows, PROFILE_MAP)

        score, detail = algorithms.heat_score({
            'work_count': payload['basic'][0]['work_count'] if payload['basic'] else 0,
            'digitized_work_count': payload['basic'][0]['digitized_work_count'] if payload['basic'] else 0,
            'activity_count': payload['basic'][0]['activity_count'] if payload['basic'] else 0,
            'registration_count': payload['basic'][0]['registration_count'] if payload['basic'] else 0,
            'project_level': payload['basic'][0]['project_level'] if payload['basic'] else None,
            'project_status': payload['basic'][0]['project_status'] if payload['basic'] else None,
        })
        grade, label = algorithms.heat_grade(score)
        payload['heat'] = {'score': score, 'grade': grade, 'label': label, 'detail': detail}
        return ok(payload)
    except Exception as exc:
        return fail(str(exc))


@app.route('/api/projects/heat')
def api_project_heat():
    """算法模型一：非遗活态传承热度指数排行"""
    try:
        top = int(request.args.get('top', 20))
        rows = db.query('SELECT * FROM v_project_profile')
        ranked = algorithms.rank_projects_by_heat(rows, top)
        return ok(ranked, formula={
            'expression': 'HVI = 作品数×2 + 已入库数字化作品数×3 + 活动场次×4 + 报名人次×1.5 + 级别权重 + 濒危加权',
            'weights': {
                'work': algorithms.HeatWeight.W_WORK,
                'digitized': algorithms.HeatWeight.W_DIGI,
                'activity': algorithms.HeatWeight.W_ACT,
                'enroll': algorithms.HeatWeight.W_ENROLL,
                'level': algorithms.HeatWeight.LEVEL_WEIGHT,
                'endangered_bonus': algorithms.HeatWeight.ENDANGERED_BONUS,
            }})
    except Exception as exc:
        return fail(str(exc))


# ======================================================================
# 传承人
# ======================================================================
@app.route('/api/inheritors')
def api_inheritors():
    try:
        page, size = paging()
        where, args = ['1 = 1'], []

        kw = (request.args.get('keyword') or '').strip()
        if kw:
            where.append('(inheritor_name LIKE %s OR inheritor_code LIKE %s OR region LIKE %s)')
            args += ['%%%s%%' % kw] * 3

        for field in ('inheritor_level', 'health_status', 'gender'):
            val = request.args.get(field)
            if val:
                where.append('%s = %%s' % field)
                args.append(val)

        cond = ' AND '.join(where)
        total = db.scalar('SELECT COUNT(*) FROM inheritor WHERE ' + cond, args)

        rows = db.query("""
            SELECT i.inheritor_id, i.inheritor_code, i.inheritor_name, i.gender, i.birth_date,
                   TIMESTAMPDIFF(YEAR, i.birth_date, CURDATE()) AS age,
                   i.ethnicity, i.phone, i.region, i.inheritor_level, i.recognized_year,
                   i.skill_years, i.health_status,
                   (SELECT COUNT(*) FROM project_inheritor pi WHERE pi.inheritor_id = i.inheritor_id)
                       AS project_count,
                   (SELECT GROUP_CONCAT(p.project_name ORDER BY p.project_id SEPARATOR '、')
                      FROM project_inheritor pi
                      JOIN heritage_project p ON p.project_id = pi.project_id
                     WHERE pi.inheritor_id = i.inheritor_id) AS project_names
              FROM inheritor i
             WHERE """ + cond + """
             ORDER BY FIELD(i.inheritor_level,'国家级','省级','市级','县级'),
                      i.inheritor_id
             LIMIT %s OFFSET %s""", args + [size, (page - 1) * size])

        return ok(rows, total=total, page=page, size=size)
    except Exception as exc:
        return fail(str(exc))


@app.route('/api/inheritors/<int:inheritor_id>')
def api_inheritor_detail(inheritor_id):
    try:
        person = db.query_one("""
            SELECT inheritor_id, inheritor_code, inheritor_name, gender, birth_date,
                   TIMESTAMPDIFF(YEAR, birth_date, CURDATE()) AS age, ethnicity,
                   phone, region, inheritor_level, recognized_year, skill_years,
                   health_status, introduction
              FROM inheritor WHERE inheritor_id = %s""", (inheritor_id,))
        if not person:
            return fail('传承人不存在', 404)

        projects = db.query("""
            SELECT p.project_id, p.project_code, p.project_name, p.project_level,
                   p.project_status, c.category_name, pi.relation_role,
                   pi.start_year, pi.is_master
              FROM project_inheritor pi
              JOIN heritage_project  p ON p.project_id  = pi.project_id
              JOIN heritage_category c ON c.category_id = p.category_id
             WHERE pi.inheritor_id = %s
             ORDER BY pi.is_master DESC, p.project_id""", (inheritor_id,))

        works = db.query("""
            SELECT w.work_id, w.work_code, w.work_name, w.create_year, w.material,
                   w.digitize_status, w.in_collection, p.project_name
              FROM heritage_work w
              JOIN heritage_project p ON p.project_id = w.project_id
             WHERE w.creator_id = %s ORDER BY w.work_id""", (inheritor_id,))

        return ok({'profile': person, 'projects': projects, 'works': works})
    except Exception as exc:
        return fail(str(exc))


# ======================================================================
# 作品
# ======================================================================
@app.route('/api/works')
def api_works():
    try:
        page, size = paging()
        where, args = ['1 = 1'], []

        pid = request.args.get('project_id')
        if pid:
            where.append('w.project_id = %s')
            args.append(pid)

        ds = request.args.get('digitize_status')
        if ds:
            where.append('w.digitize_status = %s')
            args.append(ds)

        kw = (request.args.get('keyword') or '').strip()
        if kw:
            where.append('(w.work_name LIKE %s OR w.work_code LIKE %s)')
            args += ['%%%s%%' % kw] * 2

        cond = ' AND '.join(where)
        total = db.scalar('SELECT COUNT(*) FROM heritage_work w WHERE ' + cond, args)

        rows = db.query("""
            SELECT w.work_id, w.work_code, w.work_name, w.create_year, w.material,
                   w.spec_size, w.storage_place, w.image_url, w.digitize_status,
                   w.in_collection, p.project_name, i.inheritor_name AS creator_name
              FROM heritage_work w
              JOIN heritage_project p ON p.project_id = w.project_id
              JOIN inheritor i        ON i.inheritor_id = w.creator_id
             WHERE """ + cond + """
             ORDER BY w.project_id, w.work_id LIMIT %s OFFSET %s""",
            args + [size, (page - 1) * size])
        return ok(rows, total=total, page=page, size=size)
    except Exception as exc:
        return fail(str(exc))


# ======================================================================
# 研学课程与活动
# ======================================================================
@app.route('/api/courses')
def api_courses():
    try:
        rows = db.query("""
            SELECT cs.course_id, cs.course_code, cs.course_name, cs.suit_grade,
                   cs.class_hours, cs.course_type, cs.course_status, cs.course_intro,
                   p.project_name, p.project_level, c.category_name,
                   i.inheritor_name AS teacher_name,
                   (SELECT COUNT(*) FROM research_activity a WHERE a.course_id = cs.course_id)
                       AS activity_count
              FROM research_course cs
              JOIN heritage_project  p ON p.project_id  = cs.project_id
              JOIN heritage_category c ON c.category_id = p.category_id
              JOIN inheritor         i ON i.inheritor_id = cs.teacher_id
             ORDER BY cs.course_id""")
        return ok(rows)
    except Exception as exc:
        return fail(str(exc))


@app.route('/api/activities')
def api_activities():
    try:
        page, size = paging()
        where, args = ['1 = 1'], []

        status = request.args.get('status')
        if status:
            where.append('activity_status = %s')
            args.append(status)

        kw = (request.args.get('keyword') or '').strip()
        if kw:
            where.append('(activity_name LIKE %s OR activity_code LIKE %s OR project_name LIKE %s)')
            args += ['%%%s%%' % kw] * 3

        cid = request.args.get('category_id')
        if cid:
            where.append('(category_id = %s OR parent_category_id = %s)')
            args += [cid, cid]

        cond = ' AND '.join(where)
        total = db.scalar('SELECT COUNT(*) FROM v_activity_enrollment WHERE ' + cond, args)

        rows = db.query("""
            SELECT activity_id, activity_code, activity_name, course_name, course_type,
                   class_hours, project_name, project_level, category_id, category_name,
                   parent_category_id, parent_category_name, teacher_name, activity_place,
                   start_time, deadline_time, plan_capacity, activity_status, organizer,
                   registered_total, confirmed_total, checkin_total, remaining_quota, enroll_rate
              FROM v_activity_enrollment
             WHERE """ + cond + """
             ORDER BY FIELD(activity_status,'报名中','已满员','已结束','已取消'), start_time
             LIMIT %s OFFSET %s""", args + [size, (page - 1) * size])
        return ok(rows, total=total, page=page, size=size)
    except Exception as exc:
        return fail(str(exc))


@app.route('/api/activities/<int:activity_id>')
def api_activity_detail(activity_id):
    try:
        act = db.query_one('SELECT * FROM v_activity_enrollment WHERE activity_id = %s', (activity_id,))
        if not act:
            return fail('活动不存在', 404)

        roster = db.query("""
            SELECT r.registration_id, s.student_no, s.student_name, s.gender, s.grade,
                   s.class_name, s.school_name, r.register_time, r.register_status,
                   r.attendance_status, r.checkin_time, r.score, r.comment
              FROM activity_registration r
              JOIN student s ON s.student_id = r.student_id
             WHERE r.activity_id = %s
             ORDER BY FIELD(r.register_status,'已录取','已报名','候补','已取消'), r.register_time
        """, (activity_id,))

        stats = db.query_one("""
            SELECT COUNT(*) AS total,
                   SUM(register_status IN ('已报名','已录取')) AS confirmed,
                   SUM(register_status = '候补')               AS waiting,
                   SUM(register_status = '已取消')             AS canceled,
                   SUM(attendance_status = '已签到')           AS checkin,
                   ROUND(AVG(CASE WHEN attendance_status='已签到' THEN score END), 2) AS avg_score,
                   MAX(score) AS max_score
              FROM activity_registration WHERE activity_id = %s
        """, (activity_id,))

        logs = db.query("""
            SELECT log_id, table_name, action_type, record_id, action_desc, log_time
              FROM operation_log
             WHERE table_name = 'activity_registration'
               AND action_desc LIKE %s
             ORDER BY log_id DESC LIMIT 20
        """, ('%「' + act['activity_name'] + '」%',))

        return ok({'activity': act, 'roster': roster, 'stats': stats, 'logs': logs})
    except Exception as exc:
        return fail(str(exc))


@app.route('/api/registrations', methods=['POST'])
def api_register():
    """
    学生报名研学活动。
    业务校验与并发控制全部下沉到存储过程 sp_student_register_activity 中完成，
    并在写入时由触发器联动 activity_registration / research_activity 的状态机。
    """
    try:
        payload = request.get_json(silent=True) or {}
        sid = payload.get('student_id')
        aid = payload.get('activity_id')
        if not sid or not aid:
            return fail('缺少参数 student_id / activity_id', 400)

        outs = db.callproc_out('sp_student_register_activity',
                               [int(sid), int(aid)], out_count=2)
        code, msg = outs[0], outs[1]
        return ok({'result_code': code, 'message': msg,
                   'success_flag': 1 if code == 1 else 0})
    except Exception as exc:
        return fail(str(exc))


# ======================================================================
# 学生
# ======================================================================
@app.route('/api/students')
def api_students():
    try:
        kw = (request.args.get('keyword') or '').strip()
        where, args = ['1 = 1'], []
        if kw:
            where.append('(s.student_name LIKE %s OR s.student_no LIKE %s OR s.school_name LIKE %s)')
            args += ['%%%s%%' % kw] * 3

        rows = db.query("""
            SELECT s.student_id, s.student_no, s.student_name, s.gender, s.grade,
                   s.class_name, s.school_name, s.phone, s.interest_category_id,
                   ic.category_name AS interest_category,
                   (SELECT COUNT(*) FROM activity_registration r
                     WHERE r.student_id = s.student_id AND r.register_status <> '已取消')
                       AS registration_count,
                   (SELECT ROUND(AVG(r.score), 2) FROM activity_registration r
                     WHERE r.student_id = s.student_id AND r.score IS NOT NULL) AS avg_score
              FROM student s
              LEFT JOIN heritage_category ic ON ic.category_id = s.interest_category_id
             WHERE """ + ' AND '.join(where) + """
             ORDER BY s.student_id""", args)
        return ok(rows)
    except Exception as exc:
        return fail(str(exc))


@app.route('/api/students/<int:student_id>/record')
def api_student_record(student_id):
    """学生学习档案：学生—活动—课程—项目—传承人 全链路"""
    try:
        stu = db.query_one("""
            SELECT s.student_id, s.student_no, s.student_name, s.gender, s.grade, s.class_name,
                   s.school_name, s.phone, ic.category_id AS interest_category_id,
                   ic.category_name AS interest_category
              FROM student s
              LEFT JOIN heritage_category ic ON ic.category_id = s.interest_category_id
             WHERE s.student_id = %s""", (student_id,))
        if not stu:
            return fail('学生不存在', 404)

        records = db.query("""
            SELECT activity_code, activity_name, start_time, activity_status,
                   course_name, course_type, project_name, project_level,
                   project_category, teacher_name, register_status, attendance_status,
                   register_time, checkin_time, score, comment
              FROM v_student_learning_record
             WHERE student_id = %s
             ORDER BY start_time DESC""", (student_id,))

        summary = db.query_one("""
            SELECT COUNT(*) AS total_activity,
                   SUM(register_status IN ('已报名','已录取')) AS active_count,
                   SUM(attendance_status = '已签到')           AS checkin_count,
                   ROUND(AVG(CASE WHEN score IS NOT NULL THEN score END), 2) AS avg_score,
                   MAX(score) AS best_score,
                   COUNT(DISTINCT project_name) AS project_covered
              FROM v_student_learning_record WHERE student_id = %s""", (student_id,))

        return ok({'profile': stu, 'records': records, 'summary': summary})
    except Exception as exc:
        return fail(str(exc))


# ======================================================================
# 算法模型接口
# ======================================================================
@app.route('/api/algorithms/heat')
def api_algo_heat():
    """热度指数算法说明 + 计算明细"""
    try:
        rows = db.query('SELECT * FROM v_project_profile')
        ranked = algorithms.rank_projects_by_heat(rows)
        return ok({
            'model': '非遗活态传承热度评估模型（HVI）',
            'expression': 'HVI = W×2 + D×3 + A×4 + E×1.5 + L(级别) + B(濒危)',
            'explain': ('W=已归档作品数，D=已入库数字化作品数，A=研学活动场次，'
                        'E=累计报名人次，L=级别权重，B=濒危加权'),
            'items': ranked,
        })
    except Exception as exc:
        return fail(str(exc))


@app.route('/api/algorithms/recommend')
def api_algo_recommend():
    """
    算法模型二：研学活动混合推荐。
    候选集 = 状态为"报名中" 且 未开始 且 该学生未报名的活动。
    """
    try:
        sid = request.args.get('student_id', type=int)
        top = request.args.get('top', default=5, type=int)
        if not sid:
            return fail('缺少参数 student_id', 400)

        stu = db.query_one("""
            SELECT s.student_id, s.student_no, s.student_name, s.grade, s.school_name,
                   s.interest_category_id,
                   ic.category_name AS interest_category,
                   ic.parent_id     AS interest_parent_id
              FROM student s
              LEFT JOIN heritage_category ic ON ic.category_id = s.interest_category_id
             WHERE s.student_id = %s""", (sid,))
        if not stu:
            return fail('学生不存在', 404)

        candidates = db.query("""
            SELECT activity_id, activity_code, activity_name, course_name, project_name,
                   project_level, category_id, category_name, parent_category_id,
                   parent_category_name, teacher_name, activity_place, start_time,
                   deadline_time, plan_capacity, remaining_quota, enroll_rate,
                   activity_status
              FROM v_activity_enrollment
             WHERE activity_status = '报名中'
               AND start_time > NOW()
               AND activity_id NOT IN (
                       SELECT activity_id FROM activity_registration
                        WHERE student_id = %s AND register_status <> '已取消')
             ORDER BY start_time""", (sid,))

        recs = algorithms.recommend_activities(stu, candidates, top)
        return ok({
            'model': '研学活动混合推荐模型',
            'expression': 'Score = 内容匹配分(0/3/4/5) + 群体热度分(min(报名率/10,3)) + 项目级别分',
            'student': stu,
            'candidate_count': len(candidates),
            'recommendations': recs,
        })
    except Exception as exc:
        return fail(str(exc))


# ======================================================================
# 报表接口（直接输出数据库侧的中文列名，便于报表导出与答辩演示）
# ======================================================================
@app.route('/api/reports/activity')
def api_report_activity():
    """调用存储过程 sp_activity_report 输出活动周期报表"""
    try:
        start = request.args.get('start')
        end = request.args.get('end')
        rows, _ = db.callproc('sp_activity_report', [start, end])
        return ok(rows)
    except Exception as exc:
        return fail(str(exc))


@app.route('/api/reports/category')
def api_report_category():
    """调用存储过程 sp_category_statistics（游标实现）输出门类汇总报表"""
    try:
        rows, _ = db.callproc('sp_category_statistics', [])
        return ok(rows)
    except Exception as exc:
        return fail(str(exc))


@app.route('/api/reports/digitization')
def api_report_digitization():
    """数字化保护进度报表"""
    try:
        rows = db.query("""
            SELECT project_code, project_name, project_level, project_status, category_name,
                   project_digitized, total_works, collected_works, modeled_works,
                   stored_works, collect_rate
              FROM v_digitization_progress
             ORDER BY stored_works DESC, project_id""")
        return ok(rows)
    except Exception as exc:
        return fail(str(exc))


@app.route('/api/logs')
def api_logs():
    """操作审计日志"""
    try:
        limit = min(500, int(request.args.get('limit', 50)))
        rows = db.query("""
            SELECT log_id, table_name, action_type, record_id, action_desc,
                   operator, log_time
              FROM operation_log ORDER BY log_id DESC LIMIT %s""", (limit,))
        return ok(rows)
    except Exception as exc:
        return fail(str(exc))


# ======================================================================
# SQL 能力演示：把 06_core_queries.sql 中的核心语句搬进接口，
# 供前端"SQL 演示"页面现场执行并展示结果，答辩时可逐条讲解。
# ======================================================================
DEMO_QUERIES = [
    {
        'key': 'join',
        'title': '① 多表联查：非遗项目全景档案',
        'tech': '5 表内连接 + 子查询聚合',
        'desc': '一次查询输出"项目—分类—传承人规模—作品规模—报名人次"，'
                '是档案管理页面的主查询。',
        'sql': """SELECT p.project_code AS 项目编号, p.project_name AS 项目名称,
       c.category_name AS 所属门类, p.project_level AS 级别,
       p.declared_area AS 申报地区, p.project_status AS 状态,
       COALESCE(ih.cnt, 0) AS 传承人数,
       COALESCE(wk.cnt, 0) AS 作品数,
       COALESCE(rg.cnt, 0) AS 累计报名人次
  FROM heritage_project p
  JOIN heritage_category c ON c.category_id = p.category_id
  LEFT JOIN (SELECT project_id, COUNT(*) cnt FROM project_inheritor GROUP BY project_id) ih
         ON ih.project_id = p.project_id
  LEFT JOIN (SELECT project_id, COUNT(*) cnt FROM heritage_work GROUP BY project_id) wk
         ON wk.project_id = p.project_id
  LEFT JOIN (SELECT rc.project_id, COUNT(*) cnt
               FROM activity_registration ar
               JOIN research_activity ra ON ra.activity_id = ar.activity_id
               JOIN research_course rc   ON rc.course_id  = ra.course_id
              WHERE ar.register_status IN ('已报名','已录取')
              GROUP BY rc.project_id) rg
         ON rg.project_id = p.project_id
 ORDER BY 传承人数 DESC, 作品数 DESC""",
    },
    {
        'key': 'aggregate',
        'title': '② 分组统计：一级门类上卷汇总',
        'tech': 'CASE 门类上卷 + COUNT(DISTINCT) 防连接放大',
        'desc': '把二级门类的项目归并到其一级门类下统计，'
                '并用 COUNT(DISTINCT ...) 避免多表连接造成的计数放大。',
        'sql': """SELECT COALESCE(pc.category_name, c.category_name) AS 一级门类,
       COUNT(DISTINCT p.project_id) AS 项目数,
       COUNT(DISTINCT CASE WHEN p.project_level = '国家级' THEN p.project_id END) AS 国家级项目数,
       COUNT(DISTINCT pi.inheritor_id) AS 传承人数,
       COUNT(DISTINCT w.work_id)       AS 作品数
  FROM heritage_project p
  JOIN heritage_category c  ON c.category_id = p.category_id
  LEFT JOIN heritage_category pc ON pc.category_id = c.parent_id
  LEFT JOIN project_inheritor pi ON pi.project_id = p.project_id
  LEFT JOIN heritage_work     w  ON w.project_id  = p.project_id
 GROUP BY COALESCE(pc.category_name, c.category_name)
 ORDER BY 项目数 DESC, 传承人数 DESC""",
    },
    {
        'key': 'window',
        'title': '③ 窗口函数：项目热度排名与门类内位次',
        'tech': 'DENSE_RANK() + PARTITION BY + LAG()',
        'desc': '在数据库层直接算出"全局名次"与"同一大门类内部名次"，'
                '并计算与上一名的热度差距，避免应用层二次排序。',
        'sql': """SELECT project_name AS 项目名称, root_category_name AS 一级门类,
       heat_score AS 热度指数,
       DENSE_RANK() OVER (ORDER BY heat_score DESC) AS 全局排名,
       DENSE_RANK() OVER (PARTITION BY root_category_name
                          ORDER BY heat_score DESC) AS 门类内排名,
       ROUND(heat_score - LAG(heat_score) OVER (ORDER BY heat_score DESC), 2) AS 与上一名差距
  FROM v_project_heat_index
 ORDER BY heat_score DESC""",
    },
    {
        'key': 'cte',
        'title': '④ 公用表表达式（CTE）：研学参与度分层',
        'tech': 'WITH 子查询 + CASE 分档',
        'desc': '先聚合出每个学生的参与指标，再按参与度分档，'
                '用于"非遗进校园"成效评估。',
        'sql': """WITH stu_stat AS (
    SELECT s.student_id, s.student_name, s.school_name,
           ic.category_name AS 兴趣门类,
           COUNT(r.registration_id) AS 报名场次,
           ROUND(AVG(r.score), 2)   AS 平均成绩,
           SUM(r.attendance_status = '已签到') AS 签到次数
      FROM student s
      LEFT JOIN heritage_category ic ON ic.category_id = s.interest_category_id
      LEFT JOIN activity_registration r ON r.student_id = s.student_id
                                       AND r.register_status <> '已取消'
     GROUP BY s.student_id, s.student_name, s.school_name, ic.category_name
)
SELECT student_name AS 学生姓名, school_name AS 学校, 兴趣门类,
       报名场次, 平均成绩, 签到次数,
       CASE WHEN 报名场次 >= 8 THEN '非遗传播达人'
            WHEN 报名场次 >= 5 THEN '积极参与者'
            WHEN 报名场次 >= 2 THEN '初步体验者'
            ELSE '尚未参与' END AS 参与度分层
  FROM stu_stat
 ORDER BY 报名场次 DESC, 平均成绩 DESC""",
    },
    {
        'key': 'view',
        'title': '⑤ 视图查询：活动报名实时状态',
        'tech': '视图 v_activity_enrollment 实时计算派生数据',
        'desc': '"已报名人数 / 剩余名额 / 报名率"均为派生数据，'
                '不落库存储，由视图实时计算，从根本上杜绝冗余不一致。',
        'sql': """SELECT activity_code AS 活动编号, activity_name AS 活动名称,
       project_name AS 非遗项目, teacher_name AS 主讲传承人,
       plan_capacity AS 计划人数, confirmed_total AS 已报名,
       remaining_quota AS 剩余名额, CONCAT(enroll_rate, '%') AS 报名率,
       activity_status AS 状态
  FROM v_activity_enrollment
 ORDER BY FIELD(activity_status,'报名中','已满员','已结束','已取消'),
          enroll_rate DESC""",
    },
]


@app.route('/api/demo/queries')
def api_demo_queries():
    """返回演示 SQL 及其真实执行结果（只读）"""
    try:
        key = request.args.get('key')
        items = []
        for q in DEMO_QUERIES:
            if key and q['key'] != key:
                continue
            rows = db.query(q['sql'])
            items.append({'key': q['key'], 'title': q['title'], 'tech': q['tech'],
                          'desc': q['desc'], 'sql': q['sql'], 'row_count': len(rows),
                          'columns': list(rows[0].keys()) if rows else [],
                          'rows': rows[:30]})
        return ok(items)
    except Exception as exc:
        return fail(str(exc))


# ======================================================================
# 错误处理
# ======================================================================
@app.errorhandler(404)
def handle_404(_e):
    return jsonify({'code': 404, 'success': False, 'message': '接口或资源不存在'}), 404


@app.errorhandler(500)
def handle_500(e):
    return jsonify({'code': 500, 'success': False, 'message': str(e)}), 500


if __name__ == '__main__':
    print('=' * 68)
    print(' 地方非遗文化数字化保护数据库管理系统 —— 后端服务')
    print(' 数据库: %s@%s:%s/%s' % (db.DBConfig.USER, db.DBConfig.HOST,
                                    db.DBConfig.PORT, db.DBConfig.DATABASE))
    print(' 前端页面: %s' % FRONTEND_DIR)
    print(' 访问地址: http://%s:%s/' % (ServerConfig.HOST, ServerConfig.PORT))
    print('=' * 68)
    app.run(host=ServerConfig.HOST, port=ServerConfig.PORT, debug=ServerConfig.DEBUG,
            threaded=True)
