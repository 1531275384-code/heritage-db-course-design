# 后端服务说明（Flask REST API + 算法模型）

## 1. 目录结构

```
03_后端服务/
├── app.py             Flask 应用入口：全部 REST 路由
├── db.py              数据访问层：连接管理、查询封装、存储过程调用
├── algorithms.py      算法模型层：热度评估模型 + 混合推荐模型（纯函数）
├── config.py          配置：数据库连接、服务端口、算法权重
├── requirements.txt   依赖清单
└── README.md          本文件
```

## 2. 环境准备与启动

```bash
# 1) 先按 01_数据库脚本 中的顺序建库、导入数据
#    01_schema.sql -> 02_init_data.sql -> 03_views.sql
#    -> 04_functions_procedures.sql -> 05_triggers.sql

# 2) 安装依赖
pip install -r requirements.txt

# 3) 按实际环境调整数据库连接（也可用环境变量覆盖）
#    HERITAGE_DB_HOST / HERITAGE_DB_PORT / HERITAGE_DB_USER
#    HERITAGE_DB_PASSWORD / HERITAGE_DB_NAME

# 4) 启动服务（同时托管 05_前端页面）
python app.py
# 浏览器访问 http://127.0.0.1:5000/
```

## 3. 接口清单

### 3.1 基础与元数据

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 服务与数据库健康检查（版本、表数量） |
| GET | `/api/overview` | 全局 KPI + 级别分布 + 门类分布 + 活动月度趋势 |
| GET | `/api/meta/enums` | 枚举字典（与数据库 ENUM 定义一致，供下拉筛选） |
| GET | `/api/categories` | 非遗分类层次树（一级门类 + 二级门类） |

### 3.2 非遗档案

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/projects` | 项目列表，支持 `keyword / level / status / category_id / digitized / sort / page / size` |
| GET | `/api/projects/<id>` | 项目完整档案，调用 `sp_project_full_profile` 返回四段式结果集 |
| GET | `/api/projects/heat` | 热度指数排行（算法模型一），同时返回公式与权重 |
| GET | `/api/inheritors` | 传承人列表（含所属项目聚合） |
| GET | `/api/inheritors/<id>` | 传承人详情 + 传承项目 + 作品 |
| GET | `/api/works` | 非遗作品/数字藏品列表 |

### 3.3 研学业务

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/courses` | 研学课程列表 |
| GET | `/api/activities` | 活动列表（报名率、剩余名额由视图实时计算） |
| GET | `/api/activities/<id>` | 活动详情 + 报名名单 + 统计 + 审计日志 |
| POST | `/api/registrations` | 学生报名，调用 `sp_student_register_activity`（事务 + 行锁 + 触发器联动） |
| GET | `/api/students` | 学生列表 |
| GET | `/api/students/<id>/record` | 学生学习档案全链路 |

### 3.4 算法模型

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/algorithms/heat` | 热度评估模型：公式 + 权重 + 全量计算明细 |
| GET | `/api/algorithms/recommend` | 混合推荐模型，参数 `student_id`、`top` |

### 3.5 报表与审计（输出中文列名，便于直接导出）

| 方法 | 路径 | 对应数据库对象 |
| --- | --- | --- |
| GET | `/api/reports/activity` | `sp_activity_report`（周期活动报表） |
| GET | `/api/reports/category` | `sp_category_statistics`（游标汇总） |
| GET | `/api/reports/digitization` | 视图 `v_digitization_progress` |
| GET | `/api/logs` | 表 `operation_log` 审计日志 |

## 4. 报名接口返回码约定

存储过程 `sp_student_register_activity` 的 `p_code`：

| 返回码 | 含义 |
| --- | --- |
| `1` | 报名成功（已录取 / 候补） |
| `-1` | 学生记录不存在 |
| `-2` | 研学活动不存在 |
| `-3` | 活动已取消或已结束 |
| `-4` | 已超过报名截止时间 |
| `-5` | 重复报名 |
| `-99` | 数据库执行异常，事务已回滚 |

请求示例：

```bash
curl -X POST http://127.0.0.1:5000/api/registrations \
     -H "Content-Type: application/json" \
     -d '{"student_id": 1, "activity_id": 12}'
```

## 5. 关于 PyMySQL 调用带 OUT 参数存储过程的说明

PyMySQL 的 `cursor.callproc()` 不会像 MySQLdb 那样把 OUT 参数回填到参数列表，
因此 `db.callproc_out()` 采用「会话变量中转」的等价写法：

```sql
SET @p_out_0 = NULL;
CALL sp_student_register_activity(?, ?, @p_out_0, @p_out_1);
SELECT @p_out_0, @p_out_1;
```

三条语句在同一连接（同一会话）内顺序执行，可正确取回 OUT 值。
