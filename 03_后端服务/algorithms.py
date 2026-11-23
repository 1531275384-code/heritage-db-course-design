# -*- coding: utf-8 -*-
"""
算法模型层
==========================================================================
本课程设计在数据库层之外，额外抽象出两个轻量算法模型，用于演示
"数据 -> 指标 -> 决策"的完整链路。二者均为纯函数实现（无副作用、无 IO），
便于单元测试与论文中的公式对照。

模型一：非遗活态传承热度评估模型（Heritage Vitality Index, HVI）
----------------------------------------------------------------------
设计动机：
    传统非遗管理只关心"名录登记"，无法回答"哪些项目正在失去活力、
    应当优先抢救"这一核心问题。本模型把项目的静态属性（级别、存续状态）
    与动态行为数据（作品归档、数字化进度、研学开展、社会报名）融合为
    单一可比标量，用于排序与预警。

模型形式：
    HVI(p) = w1·W(p) + w2·D(p) + w3·A(p) + w4·E(p) + L(p) + B(p)

    W(p)  项目 p 的已归档作品数              w1 = 2.0
    D(p)  项目 p 已数字化入库（已入库）作品数  w2 = 3.0
    A(p)  项目 p 开展的研学活动场次            w3 = 4.0
    E(p)  项目 p 累计报名人次                  w4 = 1.5
    L(p)  项目级别基础分（国家级 20 / 省级 14 / 市级 9 / 县级 5）
    B(p)  濒危保护加权（濒危 +12，体现"抢救性保护优先级"）

参数解释：
    权重满足 w2 > w1，表示"数字化入库"比"仅仅归档"更能提升可存续性；
    w3 > w4 > w1，表示"活态传承（有人参与）"优于"静态收藏"。
    该权重体系与《关于进一步加强非物质文化遗产保护工作的意见》中
    "见人见物见生活"的保护理念一致。

模型二：研学活动混合推荐模型（Hybrid Research-Activity Recommendation）
----------------------------------------------------------------------
设计动机：
    面向学生的研学活动推荐面临"冷启动"（新学生无历史行为）与
    "信息茧房"（只推兴趣内项目）双重问题。本模型采用
    "内容匹配为主 + 群体热度为辅"的混合策略，并允许同大门类拓展。

模型形式：
    Score(s, a) = M(s, a) + H(a) + L(a)

    M(s,a) 内容匹配分，按兴趣门类与项目门类的关系分档：
              精准命中（二级门类相同）        5.0
              学生兴趣为该项目的上级门类      4.0
              项目门类为学生兴趣的上级门类    4.0
              同属一个一级门类（大门类拓展）  3.0
              跨门类                          0.0
    H(a)   群体热度分 = min(报名率 / 10, 3.0)   —— 弱协同过滤，缓解冷启动
    L(a)   项目级别分（国家级 2.0 / 省级 1.5 / 市级 1.0 / 县级 0.5）

推荐理由由模型同步生成（可解释性），前端直接展示，避免"黑箱推荐"。
==========================================================================
"""

from config import HeatWeight, RecommendWeight


# ----------------------------------------------------------------------
# 模型一：非遗活态传承热度评估
# ----------------------------------------------------------------------
def level_weight(level):
    """项目级别基础分"""
    return HeatWeight.LEVEL_WEIGHT.get(level, 5.0)


def heat_score(project):
    """
    计算单个项目的热度指数。

    :param project: dict，至少包含 work_count / digitized_work_count /
                    activity_count / registration_count / project_level / project_status
    :return: (热度得分, 各分项明细 dict)
    """
    w = int(project.get('work_count') or 0)
    d = int(project.get('digitized_work_count') or 0)
    a = int(project.get('activity_count') or 0)
    e = int(project.get('registration_count') or 0)
    lv = level_weight(project.get('project_level'))
    bonus = HeatWeight.ENDANGERED_BONUS if project.get('project_status') == '濒危' else 0.0

    total = (w * HeatWeight.W_WORK
             + d * HeatWeight.W_DIGI
             + a * HeatWeight.W_ACT
             + e * HeatWeight.W_ENROLL
             + lv + bonus)

    detail = {
        'work_part': round(w * HeatWeight.W_WORK, 2),
        'digitized_part': round(d * HeatWeight.W_DIGI, 2),
        'activity_part': round(a * HeatWeight.W_ACT, 2),
        'enroll_part': round(e * HeatWeight.W_ENROLL, 2),
        'level_part': round(lv, 2),
        'endangered_part': round(bonus, 2),
    }
    return round(total, 2), detail


def heat_grade(score):
    """热度分级：返回 (等级, 中文描述)"""
    for threshold, grade, label in HeatWeight.GRADE_THRESHOLDS:
        if score >= threshold:
            return grade, label
    return 'C', '待激活'


def rank_projects_by_heat(projects, top_n=None):
    """对项目列表按热度指数降序排序，并补充等级、排名与分项明细"""
    scored = []
    for p in projects:
        s, detail = heat_score(p)
        grade, label = heat_grade(s)
        item = dict(p)
        item['heat_score'] = s
        item['heat_detail'] = detail
        item['heat_grade'] = grade
        item['heat_label'] = label
        scored.append(item)

    scored.sort(key=lambda x: x['heat_score'], reverse=True)
    for idx, item in enumerate(scored, 1):
        item['heat_rank'] = idx

    return scored[:top_n] if top_n else scored


# ----------------------------------------------------------------------
# 模型二：研学活动混合推荐
# ----------------------------------------------------------------------
def match_score(interest_id, interest_parent_id, category_id, category_parent_id):
    """
    计算兴趣门类与活动项目门类之间的内容匹配分。

    :param interest_id:         学生兴趣分类ID
    :param interest_parent_id:  学生兴趣分类的父分类ID（可能为 None）
    :param category_id:         活动所属项目的分类ID
    :param category_parent_id:  活动所属项目分类的父分类ID（可能为 None）
    :return: (匹配得分, 匹配说明)
    """
    W = RecommendWeight
    if interest_id is None:
        return 0.0, '未填写兴趣门类'

    if category_id == interest_id:
        return W.MATCH_EXACT, '兴趣门类精准匹配'
    if interest_parent_id is not None and category_parent_id == interest_id:
        return W.MATCH_INTEREST_IS_PARENT, '学生兴趣为该非遗项目的上级门类'
    if interest_parent_id is not None and category_id == interest_parent_id:
        return W.MATCH_CATEGORY_IS_PARENT, '非遗项目门类为学生兴趣的上级门类'
    if interest_parent_id is not None and category_parent_id == interest_parent_id:
        return W.MATCH_SAME_ROOT, '与兴趣同属一个一级门类（大门类拓展）'
    return 0.0, '跨门类拓展'


def recommend_score(interest_id, interest_parent_id, activity):
    """
    计算单个活动的推荐得分。

    :param activity: dict，需含 category_id / parent_category_id /
                     enroll_rate / project_level
    :return: (得分, 推荐理由)
    """
    W = RecommendWeight
    m, reason = match_score(interest_id, interest_parent_id,
                            activity.get('category_id'),
                            activity.get('parent_category_id'))

    enroll_rate = float(activity.get('enroll_rate') or 0.0)
    hot = min(enroll_rate / W.HOT_DIVISOR, W.HOT_MAX)
    lv = W.LEVEL_WEIGHT.get(activity.get('project_level'), 0.5)

    total = round(m + hot + lv, 2)
    full_reason = '%s；群体热度 %s%%；%s项目' % (
        reason, round(enroll_rate, 1), activity.get('project_level'))
    return total, full_reason


def recommend_activities(student, activities, top_n=5):
    """
    为学生生成研学活动推荐列表。

    :param student:    dict，含 interest_category_id / interest_parent_id
    :param activities: 候选活动列表（应为"报名中且未开始且未报名"集合）
    :return: 按得分降序的推荐列表（含 score 与 reason）
    """
    interest_id = student.get('interest_category_id')
    interest_parent = student.get('interest_parent_id')

    result = []
    for act in activities:
        score, reason = recommend_score(interest_id, interest_parent, act)
        item = dict(act)
        item['recommend_score'] = score
        item['recommend_reason'] = reason
        result.append(item)

    result.sort(key=lambda x: (x['recommend_score'], float(x.get('enroll_rate') or 0)), reverse=True)
    return result[:top_n] if top_n else result
