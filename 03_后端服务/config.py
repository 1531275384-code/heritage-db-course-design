# -*- coding: utf-8 -*-
"""
配置模块
--------------------------------------------------------------------------
集中管理数据库连接参数、服务端口与算法模型的可调权重。
修改本文件即可切换运行环境，无需改动任何业务代码。
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
FRONTEND_DIR = os.path.join(PROJECT_ROOT, '05_前端页面')


class DBConfig(object):
    """数据库连接配置（默认对接本地 MariaDB / MySQL 实例）"""
    HOST = os.environ.get('HERITAGE_DB_HOST', '127.0.0.1')
    PORT = int(os.environ.get('HERITAGE_DB_PORT', '3399'))
    USER = os.environ.get('HERITAGE_DB_USER', 'root')
    PASSWORD = os.environ.get('HERITAGE_DB_PASSWORD', '')
    DATABASE = os.environ.get('HERITAGE_DB_NAME', 'heritage_db')
    CHARSET = 'utf8mb4'

    @classmethod
    def as_dict(cls):
        return dict(host=cls.HOST, port=cls.PORT, user=cls.USER,
                    password=cls.PASSWORD, database=cls.DATABASE, charset=cls.CHARSET)


class ServerConfig(object):
    """Web 服务配置"""
    HOST = '127.0.0.1'
    PORT = int(os.environ.get('HERITAGE_API_PORT', '5000'))
    DEBUG = False


class HeatWeight(object):
    """
    非遗活态传承热度评估算法权重
    ------------------------------------------------------------------
    热度指数 = 作品数×W_WORK + 已入库数字化作品数×W_DIGI + 活动场次×W_ACT
             + 报名人次×W_ENROLL + 级别权重 + 濒危保护加权
    权重系数可在不修改数据库、不重启服务的前提下按需调整，
    体现算法模型"可配置"的工程特性。
    """
    W_WORK = 2.0            # 作品数量权重
    W_DIGI = 3.0            # 已入库数字化作品权重（数字化程度越高，保护价值越大）
    W_ACT = 4.0             # 研学活动场次权重（活态传承的直接体现）
    W_ENROLL = 1.5          # 报名人次权重（社会参与度）

    LEVEL_WEIGHT = {        # 项目级别基础分（影响力）
        '国家级': 20.0,
        '省级': 14.0,
        '市级': 9.0,
        '县级': 5.0,
    }
    ENDANGERED_BONUS = 12.0  # 濒危项目抢救性保护加权

    # 热度分级阈值（用于前端标签着色）
    GRADE_THRESHOLDS = ((60.0, 'S', '极高热度'), (45.0, 'A', '高热度'),
                        (30.0, 'B', '中等热度'), (0.0, 'C', '待激活'))


class RecommendWeight(object):
    """
    研学活动混合推荐算法权重
    ------------------------------------------------------------------
    推荐得分 = 内容匹配分（兴趣门类 ↔ 项目门类）+ 群体热度分 + 项目级别分
    内容匹配采用"两级门类双向包含"判定，避免仅精确匹配导致冷启动。
    """
    MATCH_EXACT = 5.0        # 活动项目门类 == 学生兴趣门类（精准命中）
    MATCH_INTEREST_IS_PARENT = 4.0   # 学生兴趣为该项目的上级门类
    MATCH_CATEGORY_IS_PARENT = 4.0   # 项目门类为学生兴趣的上级门类
    MATCH_SAME_ROOT = 3.0    # 与兴趣同属一个一级门类（同大门类拓展）

    HOT_MAX = 3.0            # 群体热度分上限（报名率 / 10，封顶 3 分）
    HOT_DIVISOR = 10.0

    LEVEL_WEIGHT = {
        '国家级': 2.0,
        '省级': 1.5,
        '市级': 1.0,
        '县级': 0.5,
    }
