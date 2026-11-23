# -*- coding: utf-8 -*-
"""生成课程设计的 E-R 图与数据库物理结构关系图（PNG）。
坐标系统一以"英寸"为单位，通过等比数据坐标保证图形比例正确。"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Polygon, FancyArrowPatch
import os

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'SimSun']
plt.rcParams['axes.unicode_minus'] = False

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, '04_设计图表')
if not os.path.isdir(OUT):
    os.makedirs(OUT)

C_ENT_F, C_ENT_E = '#DCE9F7', '#2F6FB0'
C_REL_F, C_REL_E = '#FDE9C9', '#C98A14'
C_LINE, C_TITLE, C_CARD = '#46536B', '#1F3352', '#B22222'


class Canvas:
    def __init__(self, w_in, h_in):
        self.w, self.h = w_in, h_in
        self.fig = plt.figure(figsize=(w_in, h_in), dpi=170)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, w_in)
        self.ax.set_ylim(0, h_in)
        self.ax.axis('off')
        self.fig.patch.set_facecolor('white')

    def text(self, x, y, s, fs=10, color='#12263F', weight='normal', ha='center', va='center', **kw):
        return self.ax.text(x, y, s, fontsize=fs, color=color, fontweight=weight,
                            ha=ha, va=va, zorder=6, **kw)

    def title(self, t, sub=''):
        self.text(self.w / 2, self.h - 0.30, t, fs=15, weight='bold', color=C_TITLE)
        if sub:
            self.text(self.w / 2, self.h - 0.63, sub, fs=9, color='#5A6C86')

    def save(self, name):
        self.fig.savefig(os.path.join(OUT, name), facecolor='white', bbox_inches=None)
        plt.close(self.fig)


def box(cv, x, y, w, h, label, fs=11, fc=C_ENT_F, ec=C_ENT_E, lw=1.6, ls='-'):
    cv.ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                                   boxstyle='round,pad=0.02,rounding_size=0.10',
                                   linewidth=lw, edgecolor=ec, facecolor=fc,
                                   linestyle=ls, zorder=3))
    cv.text(x, y, label, fs=fs, weight='bold', linespacing=1.5)
    return dict(x=x, y=y, w=w, h=h)


def diamond(cv, x, y, w, h, label, fs=9.5):
    pts = [(x, y + h / 2), (x + w / 2, y), (x, y - h / 2), (x - w / 2, y)]
    cv.ax.add_patch(Polygon(pts, closed=True, linewidth=1.4, edgecolor=C_REL_E,
                            facecolor=C_REL_F, zorder=4))
    cv.text(x, y, label, fs=fs, weight='bold', color='#6B4708', linespacing=1.35)
    return dict(x=x, y=y, w=w, h=h)


def edge_point(b, tx, ty):
    dx, dy = tx - b['x'], ty - b['y']
    if dx == 0 and dy == 0:
        return b['x'], b['y']
    sx = (b['w'] / 2) / abs(dx) if dx else float('inf')
    sy = (b['h'] / 2) / abs(dy) if dy else float('inf')
    s = min(sx, sy)
    return b['x'] + dx * s, b['y'] + dy * s


def link(cv, A, B, name='', cardA='', cardB='', t=0.5, dw=1.5, dh=0.85,
         rad=0.0, dot_offset=0.34, lw=1.4, ls='-', color=C_LINE, fs_rel=9.5):
    """在实体 A、B 之间绘制联系：直线 + 中间菱形 + 两端基数标注。"""
    pa = edge_point(A, B['x'], B['y'])
    pb = edge_point(B, A['x'], A['y'])
    cv.ax.add_patch(FancyArrowPatch(pa, pb, arrowstyle='-', linestyle=ls,
                                    linewidth=lw, color=color, zorder=2,
                                    connectionstyle='arc3,rad=%s' % rad,
                                    shrinkA=0, shrinkB=0))
    if name:
        cx = pa[0] + (pb[0] - pa[0]) * t
        cy = pa[1] + (pb[1] - pa[1]) * t
        diamond(cv, cx, cy, dw, dh, name, fs=fs_rel)
    L = ((pb[0] - pa[0]) ** 2 + (pb[1] - pa[1]) ** 2) ** 0.5
    ux, uy = (pb[0] - pa[0]) / L, (pb[1] - pa[1]) / L
    for p, t2, txt in ((pa, dot_offset, cardA), (pb, -dot_offset, cardB)):
        if not txt:
            continue
        cv.ax.text(p[0] + ux * t2, p[1] + uy * t2 - 0.03, txt, fontsize=10,
                   color=C_CARD, fontweight='bold', ha='center', va='center', zorder=7,
                   bbox=dict(boxstyle='circle,pad=0.16', fc='white', ec=C_CARD, lw=0.9))


def legend(cv, y=0.42):
    items = [(C_ENT_F, C_ENT_E, '实体（Entity）'),
             (C_REL_F, C_REL_E, '联系（Relationship）'),
             ('white', C_CARD, '1 / N / M  基数标注'),
             ('white', C_LINE, '实体—联系连线')]
    x = 0.7
    for fc, ec, t in items:
        cv.ax.add_patch(FancyBboxPatch((x, y - 0.13), 0.28, 0.26,
                                       boxstyle='round,pad=0.01,rounding_size=0.05',
                                       linewidth=1.1, edgecolor=ec, facecolor=fc, zorder=5))
        cv.text(x + 0.38, y, t, fs=8.2, color='#3D4C63', ha='left')
        x += 2.35


# =====================================================================================
# 图 3-1  非遗档案域 E-R 图
# =====================================================================================
def fig_er_archive():
    cv = Canvas(10.6, 7.4)
    cv.title('图 3-1  非遗档案域 E-R 图',
             '实体：非遗分类 / 非遗项目 / 非遗传承人 / 非遗作品')

    w, h = 2.5, 0.92
    cat = box(cv, 2.6, 6.10, w, h, '非遗分类\nheritage_category', fs=10)
    prj = box(cv, 8.2, 6.10, w, h, '非遗项目\nheritage_project', fs=10)
    inh = box(cv, 2.6, 1.90, w, h, '非遗传承人\ninheritor', fs=10)
    wrk = box(cv, 8.2, 1.90, w, h, '非遗作品\nheritage_work', fs=10)

    link(cv, cat, prj, '归属', '1', 'N', t=0.5, dw=1.55, dh=0.88)
    link(cv, inh, wrk, '创作', '1', 'N', t=0.5, dw=1.55, dh=0.88)
    link(cv, prj, wrk, '收录', '1', 'N', t=0.5, dw=1.55, dh=0.88)
    link(cv, inh, prj, '传承', 'M', 'N', t=0.5, dw=1.55, dh=0.88)
    cv.text(5.4, 3.24, '传承联系属性：传承角色 / 起始年份 / 是否主传承人',
            fs=8.6, color='#6B4708')

    # 分类自关联（父子）
    x0, y0 = 2.6, 6.56
    cv.ax.add_patch(FancyArrowPatch((x0 - 0.32, y0), (x0 - 0.32, y0 + 0.50),
                                    arrowstyle='-', lw=1.4, color=C_LINE, zorder=2))
    cv.ax.add_patch(FancyArrowPatch((x0 - 0.32, y0 + 0.50), (x0 + 0.32, y0 + 0.50),
                                    arrowstyle='-', lw=1.4, color=C_LINE, zorder=2))
    cv.ax.add_patch(FancyArrowPatch((x0 + 0.32, y0 + 0.50), (x0 + 0.32, y0),
                                    arrowstyle='-', lw=1.4, color=C_LINE, zorder=2))
    cv.text(x0, y0 + 0.66, '父子自关联（1 : N）', fs=8.6, color=C_CARD)

    legend(cv)
    cv.save('图3-1_非遗档案域ER图.png')


# =====================================================================================
# 图 3-2  研学活动域 E-R 图
# =====================================================================================
def fig_er_research():
    cv = Canvas(14.4, 8.6)
    cv.title('图 3-2  研学活动域 E-R 图',
             '实体：非遗项目 / 非遗传承人 / 研学课程 / 研学活动 / 学生 / 非遗分类')

    w, h = 2.80, 0.92
    prj = box(cv, 2.70, 7.15, w, h, '非遗项目\nheritage_project', fs=10)
    crs = box(cv, 7.20, 7.15, w, h, '研学课程\nresearch_course', fs=10)
    stu = box(cv, 11.70, 7.15, w, h, '学生\nstudent', fs=10.5)
    inh = box(cv, 2.70, 2.30, w, h, '非遗传承人\ninheritor', fs=10.5)
    act = box(cv, 7.20, 2.30, w, h, '研学活动\nresearch_activity', fs=10)
    cat = box(cv, 11.70, 2.30, w, h, '非遗分类\nheritage_category', fs=9.6)

    link(cv, prj, crs, '开设', '1', 'N', t=0.50, dw=1.35, dh=0.84, dot_offset=0.30)
    link(cv, crs, act, '开办', '1', 'N', t=0.50, dw=1.35, dh=0.84, dot_offset=0.30)
    link(cv, stu, cat, '关注', 'N', '1', t=0.50, dw=1.35, dh=0.84, dot_offset=0.30)
    link(cv, inh, crs, '主讲', '1', 'N', t=0.50, dw=1.35, dh=0.84, dot_offset=0.30)
    link(cv, act, stu, '报名', 'M', 'N', t=0.47, dw=1.35, dh=0.84, dot_offset=0.30)

    cv.text(0.55, 4.90, '主讲联系：\n一名传承人可主讲多门课程；\n一门课程由一名传承人主讲。',
            fs=8.0, color='#6B4708', ha='left', linespacing=1.6)
    cv.text(0.55, 3.60, '开设 / 开办联系：\n一个项目可开设多门课程；\n一门课程可开办多场活动。',
            fs=8.0, color='#6B4708', ha='left', linespacing=1.6)
    cv.text(7.20, 1.30, '报名联系属性：报名时间 / 报名状态 / 出勤状态 / 签到时间 / 实践成绩 / 研学评语   （学生 M : 活动 N）',
            fs=8.4, color='#6B4708')

    legend(cv, y=0.45)
    cv.save('图3-2_研学活动域ER图.png')


# =====================================================================================
# 图 3-3  数据库物理结构关系图
# =====================================================================================
def table(cv, x, y, w, name, fields, fs_t=8.6, fs_f=7.4, lh=0.195, pad_top=0.30):
    h = pad_top + len(fields) * lh + 0.10
    top = y + h / 2
    cv.ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                                   boxstyle='round,pad=0.02,rounding_size=0.09',
                                   linewidth=1.4, edgecolor=C_ENT_E, facecolor='white', zorder=3))
    cv.ax.add_patch(FancyBboxPatch((x - w / 2, top - pad_top), w, pad_top,
                                   boxstyle='round,pad=0.02,rounding_size=0.09',
                                   linewidth=1.4, edgecolor=C_ENT_E, facecolor=C_ENT_F, zorder=4))
    cv.text(x, top - pad_top / 2, name, fs=fs_t, weight='bold')
    for i, f in enumerate(fields):
        yy = top - pad_top - 0.055 - i * lh - lh / 2
        col, tag = '#26374F', ''
        if f.startswith('*'):
            tag, f, col = 'PK', f[1:], '#8B1E1E'
        elif f.startswith('+'):
            tag, f, col = 'FK', f[1:], '#1B5E20'
        cv.text(x - w / 2 + 0.09, yy, f, fs=fs_f, color=col, ha='left')
        if tag:
            cv.text(x + w / 2 - 0.09, yy, tag, fs=fs_f - 0.6, color=col, ha='right')
    return dict(x=x, y=y, w=w, h=h)


def fig_schema():
    cv = Canvas(15.6, 10.4)
    cv.title('图 3-3  地方非遗文化数字化保护数据库  物理结构关系图',
             '11 张数据表  ·  12 个外键约束  ·  18 个 CHECK 约束  ·  13 个唯一约束  ·  43 个索引')

    W = 2.95
    cat = table(cv, 2.35, 9.05, W, 'heritage_category 非遗分类',
                ['*category_id', 'category_code (U)', 'category_name (U)',
                 'category_level', '+parent_id', 'description'])
    prj = table(cv, 6.75, 8.85, W, 'heritage_project 非遗项目',
                ['*project_id', 'project_code (U)', 'project_name (U)',
                 '+category_id', 'project_level', 'project_status',
                 'is_digitized', 'approval_year', 'project_intro'])
    wrk = table(cv, 11.30, 9.05, W, 'heritage_work 非遗作品',
                ['*work_id', 'work_code (U)', 'work_name', '+project_id',
                 '+creator_id', 'create_year', 'digitize_status', 'in_collection'])
    arc = table(cv, 2.35, 6.10, W, 'heritage_project_archive 归档快照',
                ['*archive_id', 'project_id', 'project_code', 'project_name',
                 'category_name', 'inheritor_count', 'work_count',
                 'activity_count', 'archive_time'])
    pim = table(cv, 6.75, 6.15, W, 'project_inheritor 项目-传承人',
                ['*relation_id', '+project_id', '+inheritor_id',
                 'relation_role', 'start_year', 'is_master',
                 '(project_id,inheritor_id) 唯一'])
    crs = table(cv, 11.30, 6.05, W, 'research_course 研学课程',
                ['*course_id', 'course_code (U)', 'course_name',
                 '+project_id', '+teacher_id', 'suit_grade',
                 'class_hours', 'course_status'])
    inh = table(cv, 2.35, 3.10, W, 'inheritor 非遗传承人',
                ['*inheritor_id', 'inheritor_code (U)', 'inheritor_name',
                 'gender / birth_date', 'id_card (U)', 'phone (U)',
                 'inheritor_level', 'health_status'])
    reg = table(cv, 6.75, 3.05, W, 'activity_registration 报名记录',
                ['*registration_id', '+activity_id', '+student_id',
                 'register_time', 'register_status', 'attendance_status',
                 'score', 'comment'])
    act = table(cv, 11.30, 3.10, W, 'research_activity 研学活动',
                ['*activity_id', 'activity_code (U)', '+course_id',
                 'start_time / end_time', 'deadline_time',
                 'plan_capacity', 'activity_status'])
    stu = table(cv, 4.55, 0.72, W, 'student 学生',
                ['*student_id', 'student_no (U)', 'student_name',
                 'grade / class_name', '+interest_category_id'])
    log = table(cv, 9.05, 0.72, W, 'operation_log 审计日志',
                ['*log_id', 'table_name', 'action_type',
                 'record_id / operator', 'log_time'])

    link(cv, cat, prj, '', '1', 'N', t=0.5, dot_offset=0.30)
    link(cv, prj, wrk, '', '1', 'N', t=0.5, dot_offset=0.30)
    link(cv, prj, pim, '', '1', 'N', t=0.5, dot_offset=0.30)
    link(cv, inh, pim, '', '1', 'N', t=0.5, dot_offset=0.30, rad=-0.10)
    link(cv, prj, arc, '', '1', 'N', t=0.5, dot_offset=0.30, rad=0.10)
    link(cv, prj, crs, '', '1', 'N', t=0.5, dot_offset=0.30, rad=0.06)
    link(cv, inh, wrk, '', '1', 'N', t=0.5, dot_offset=0.30, rad=-0.22)
    link(cv, inh, crs, '', '1', 'N', t=0.5, dot_offset=0.30, rad=-0.16)
    link(cv, crs, act, '', '1', 'N', t=0.5, dot_offset=0.30)
    link(cv, act, reg, '', '1', 'N', t=0.5, dot_offset=0.30)
    link(cv, stu, reg, '', '1', 'N', t=0.58, dot_offset=0.30, rad=-0.10)
    link(cv, act, stu, '', 'N', 'N', t=0.5, dot_offset=0.30, rad=0.16, ls='-')

    # 分类自关联
    x0 = 2.35 - W / 2
    y0, y1 = 9.05 + 0.63, 9.05 + 1.05
    cv.ax.add_patch(FancyArrowPatch((x0, y0), (x0 - 0.42, y0), arrowstyle='-', lw=1.3, color=C_LINE, zorder=2))
    cv.ax.add_patch(FancyArrowPatch((x0 - 0.42, y0), (x0 - 0.42, y1), arrowstyle='-', lw=1.3, color=C_LINE, zorder=2))
    cv.ax.add_patch(FancyArrowPatch((x0 - 0.42, y1), (x0, y1), arrowstyle='-', lw=1.3, color=C_LINE, zorder=2))
    cv.text(x0 - 0.62, (y0 + y1) / 2 + 0.02, '1 : N', fs=8.0, color=C_CARD)

    cv.text(7.0, 2.05, '逻辑关联（无外键，由触发器自动写入）：operation_log ← 报名 / 传承人 / 作品 三类业务表的增删改审计留痕',
            fs=7.8, color='#5A6C86')
    cv.text(7.0, 1.78, '归档快照表为项目维度的历史冻结数据，不设外键，保证非遗项目数据永久可追溯、不做物理删除',
            fs=7.8, color='#5A6C86')
    cv.text(0.45, 10.03, 'PK = 主键    FK = 外键    (U) = 唯一约束',
            fs=8.4, color='#3D4C63', ha='left',
            bbox=dict(boxstyle='round,pad=0.30', fc='#F2F6FC', ec='#C3D4E8', lw=1.0))
    cv.save('图3-3_数据库物理结构关系图.png')


if __name__ == '__main__':
    fig_er_archive()
    fig_er_research()
    fig_schema()
    print('ER 图生成完成 ->', OUT)
