# 非遗数字资产分级确权与溯源 AI 工具

> **本仓库默认分支：`iteration/ai-traceability`（迭代版 v2，即当前首页）。**
> 课程设计原版（v1，MySQL + Flask）保留在 [`main`](https://github.com/1531275384-code/heritage-db-course-design/tree/main) 分支。
> 两个版本的演进关系、技术栈差异与新增能力见 [迭代说明.md](迭代说明.md)。

面向文旅部门、非遗传承人、知识产权监管机构的一套可运行系统。

它要解决的核心问题是：**一件非遗纹样作品，到底属于"公有传统母题""传承人改编衍生作品"，还是"全新原创作品"？** 以及，这件作品后续每一次授权流转，能否被可信追溯？

系统由两部分组成：

- **AI 特征引擎** —— 提取图像特征向量、与公有母题库做向量近邻检索、给出分级**建议**
- **数据库系统** —— 确权事务、溯源日志、权限管控

> **贯穿全系统的第一原则：AI 只出建议，绝不直接改 `asset.right_level`。**
> 终审结论只能由专家通过一次数据库事务写下，且该事务被触发器自动留痕。
> 这条原则不是写在注释里，而是用**列级 GRANT + 源码不变量测试 + 事务设计**三重手段保证的（见 [§7](#7-关键设计ai-为什么改不了权属)）。

---

## 不知道从哪进？

本仓库有四层导航，按「你离代码多远」排：

| 你想 | 去哪里 | 一句话 |
|---|---|---|
| 知道整个仓库怎么走 | **`PROJECT_MAP.md`** | 8 条产品线，每条给一个入口命令、产物位置、验证方式 |
| 只想拿成品去交 | `output/产物清单.md` | 四类产物，哪些要交、哪些是证据、哪些不交 |
| 想读说明不想读代码 | `docs/README.md` | 6 份文档各看什么、给谁看、哪份已作废 |
| 要动代码 | 本文 | 技术全文档，14 章 |

最短路径：`python bootstrap.py` → 打开 http://127.0.0.1:8000/ → `heritor01` / `Passw0rd!`。

---

## 目录

1. [快速开始](#1-快速开始) — **三条路径：Docker 真库 / 已有库 / 演示模式**
2. [环境要求](#2-环境要求)
3. [初始化步骤](#3-初始化步骤)
4. [启动服务](#4-启动服务)
5. [演示流程（走通全链路）](#5-演示流程走通全链路)
6. [文件清单与用途](#6-文件清单与用途)
7. [关键设计：AI 为什么改不了权属](#7-关键设计ai-为什么改不了权属)
8. [数据库原理对照表](#8-数据库原理对照表)
9. [API 一览](#9-api-一览)
10. [配置项](#10-配置项)
11. [阈值与权重是怎么定的](#11-阈值与权重是怎么定的)
12. [测试与验证](#12-测试与验证)
13. [已知限制](#13-已知限制)
14. [常见问题](#14-常见问题)

### 三种运行形态，先选一个

| 形态 | 命令 | 有什么 | 缺什么 |
|---|---|---|---|
| **真库 + pgvector** | `docker compose up -d` → `python bootstrap.py` | 全部 8 张表、触发器、视图、列级 GRANT、HNSW 索引、MVCC 并发控制 | — |
| **真库无 pgvector** | 同上，但跳过 pgvector | 表/触发器/视图/GRANT/约束全部生效；检索为精确扫描 | HNSW 索引（结果仍正确，只是慢） |
| **演示模式** | `python bootstrap.py --demo` | 完整界面与业务逻辑、真实 AI 算法 | 数据库层的一切强制手段（见 §1.3 对照表） |

三种形态跑的是**同一份** `api.py` / `services.py` 业务代码，差异只在存储与检索实现。

---

## 1. 快速开始

### 1.0 最快路径：用 Docker 起一个带 pgvector 的真库（推荐）

`pgvector` 在 Windows 上没有官方预编译包，要自己用 MSVC 编译，这是本项目最容易卡住的一步。
`docker-compose.yml` 已经配好：镜像是 **`pgvector/pgvector:pg16`**，扩展预装，不用编译。

```bash
# ① 装 Python 依赖
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# ② 一键建库：起容器 → 等 healthy → 按顺序建表 → 回填母题向量
python scripts/init_db.py

# ③ 启动
python bootstrap.py
```

`init_db.py` 替你处理了三件容易出错的事：
- 容器 `up -d` 返回后数据库**还没准备好**，它会轮询 `pg_isready` 再继续
- 建表严格按 `01 → 02 → 03` 顺序（`02` 依赖 `01` 的对象，`01` 的 `DROP ... CASCADE` 会删掉 `02` 的视图）
- 建完表自动回填母题向量（不回填的话 AI 会把一切都判成 `original`）

也可以手工执行，效果一样：

```bash
docker compose up -d
docker compose ps                                    # 等 STATUS 显示 healthy

psql -h 127.0.0.1 -U postgres -d ich_heritage -v ON_ERROR_STOP=1 -f sql/01_init.sql
psql -h 127.0.0.1 -U postgres -d ich_heritage -v ON_ERROR_STOP=1 -f sql/02_index_trigger_view.sql
psql -h 127.0.0.1 -U postgres -d ich_heritage -v ON_ERROR_STOP=1 -f sql/03_seed.sql

python -m app.cli backfill-motif-vectors
python bootstrap.py
```

`postgres` 超级用户的密码在 compose 里是 `postgres`，本地开发用。
生产务必改掉，并且**不要**用超级用户跑应用 —— 见 §8.6 的三角色 GRANT 设计。

### 1.1 已有 PostgreSQL 15+

把上面 ③ 之后的步骤照做即可；连接参数用环境变量覆盖：

```bash
export ICH_DB_HOST=127.0.0.1 ICH_DB_PORT=5432 ICH_DB_NAME=ich_heritage
export ICH_DB_USER=postgres ICH_DB_PASSWORD=你的密码
python bootstrap.py --check     # 先看能不能连、表建没建、pgvector 有没有
```

### 1.2 没有 pgvector 也能跑（降级模式，功能等价）

如果装不上 pgvector，`sql/00_compat_vector.sql` 会自动把向量层换成
`NUMERIC[]` + 自定义 `<=>` 运算符，**上层 SQL 一行都不用改**。

| | 有 pgvector | 无 pgvector（降级） |
|---|---|---|
| 向量列类型 | `VECTOR(512)` | `NUMERIC[]` + CHECK 维度约束 |
| cosine 距离 | pgvector 内置 `<=>` | 函数 `ich_cosine_distance()` 实现的 `<=>` |
| 检索 | HNSW 近似最近邻 | 精确全量扫描 |
| 结果准确性 | 近似（有召回率损失） | **更准**（精确计算） |
| 复杂度 | O(log n) | O(n) |
| 适用 | 生产、性能验证 | 功能演示、中小数据量 |

脚本会自动探测并打印走了哪条路，不会静默切换：

```
>>> [0] 检测 pgvector 可用性
  ⚠ 未检测到 pgvector —— 降级为 NUMERIC[] + 自定义 <=> 运算符
    检索将走精确扫描（无 HNSW），功能等价但大数据量下较慢
```

> 注意：降级模式下 `EXPLAIN` 看到的是 `Seq Scan`，**不是** `Index Scan using ...hnsw`。
> 这是如实反映，不是 bug。要证明 HNSW 生效，必须在有 pgvector 的环境跑。

### 1.3 什么都没有？先看演示模式

如果你现在**没有可用的 PostgreSQL**，可以跳过所有数据库步骤，直接：

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # 不含 torch，装得快

python bootstrap.py --demo         # 明确指定演示模式
# 或 python run.py                 # 数据库连不上时自动降级，效果相同
```

打开 <http://127.0.0.1:8000/> 用下面任一账号登录（三处路径通用）：

| 用户名 | 口令 | 角色 | 用途 |
|---|---|---|---|
| `heritor01` | `Passw0rd!` | 传承人 heritor | 3 件作品（含待审 / 已确权 / 已驳回），可上传、分析、登记授权 |
| `heritor02` | `Passw0rd!` | 传承人 heritor | 2 件作品（苗族银饰方向），用于演示"不同传承人只看自己的" |
| `expert01` | `Passw0rd!` | 评审专家 expert | 专家工作台、终审确权、看全量待审队列 |
| `admin01` | `Passw0rd!` | 监管管理员 admin | 全量只读台账、母题库维护 |

> 真库模式下口令以 bcrypt 散列保存在 `app_user.password_hash`（pgcrypto 的
> `crypt()` + `gen_salt('bf', 10)`），校验也交给数据库做，明文口令不进入应用日志。
> 演示模式下用的是 SHA-256 + `compare_digest`，仅为演示方便，**不具备** bcrypt 的抗暴力破解能力。

页面顶部会有一条**红色横幅**标明「演示模式」。

**进来就有东西看**：演示模式启动时会自动加载 `data/seed_demo.json` ——
**4 个账号 / 3 个母题 / 8 件作品 / 14 条审核流水 / 5 笔授权**，状态覆盖
`draft`、`pending`、`approved`、已驳回，登录即见完整列表，不需要先上传。

想看"上传 → AI 建议"这条链路，用 `heritor01` 上传 `data/samples/sample_derived_butterfly.jpg`，
点上传按钮即会自动完成「提特征 → 检索母题 → 出分级建议」。
**不要**试图用 CLI 预置演示数据 —— 演示库在进程内存里，`python -m app.cli demo-seed` 写的是
**另一个进程**的内存，服务端看不到。种子由服务端 `lifespan` 自己加载。

> 想清空重来：`ICH_DEMO_SEED=off python bootstrap.py --demo`，就是一个空库（0 件作品）。

演示模式做了什么、没做什么，必须说清楚（这也是横幅上写的）：

| | 真实数据库模式 | 演示模式（内存存储） |
|---|---|---|
| 8 张表的结构与关联 | PostgreSQL 表 + 外键 | Python dict 模拟，语义一致 |
| AI 特征提取 / 相似检索 / 分级建议 | ✅ | ✅ **完全相同的算法代码** |
| 列级 GRANT（AI 改不了 `right_level`） | ✅ 数据库强制 | ❌ 靠代码路径约束 |
| 触发器自动留痕 `system_audit_log` | ✅ 数据库触发器 | ⚠️ 手工模拟，字段一致 |
| CHECK / UNIQUE / 外键约束 | ✅ 数据库强制 | ❌ 业务层检查 |
| HNSW 近似最近邻检索 | ✅ pgvector 索引 | ❌ numpy 精确全量扫描（结果更准但更慢） |
| MVCC / `SELECT FOR UPDATE` 并发控制 | ✅ | ❌ 单进程 `RLock` |
| 视图 `v_asset_pending_audit` 等 | ✅ | ⚠️ 等价查询，逻辑一致 |
| 数据持久化 | ✅ | ❌ **进程退出即丢失** |

控制方式（环境变量 `ICH_DEMO_MODE`）：

| 值 | 行为 |
|---|---|
| `auto`（默认） | 先试数据库；连不上则自动降级为演示模式并打红字警告 |
| `1` | 强制演示模式，完全不碰数据库（CI / 演示 / 评审看界面时用） |
| `0` | 强制真实数据库；连不上就返回 503 + 排查步骤，**绝不静默降级**（验收时用这个） |

> 什么时候必须用 `0`：要验证「数据库层真的拦住了 AI 改权属」「触发器真的写了日志」「HNSW 索引真的被用上了」的时候。
> 这些是数据库原理的核心证据，演示模式**不能**作为它们的证明。

> 演示模式的数据生命周期 = 服务进程的生命周期。重启服务即回到种子初始状态
> （4 个账号 + 3 条母题 + 8 件作品 + 14 条审核 + 5 笔授权，来自 `data/seed_demo.json`）。
> 这是刻意的：演示库不该有任何持久化，否则容易被误当成真库。
> 也正因为如此，**同一份种子文件同时供演示模式和 `scripts/init_seed.py`（真库）使用**，
> 两条路径的数字完全一致 —— 演示时看到的 ¥131,600，灌进真库后还是 ¥131,600。

---

## 2. 环境要求

| 项目 | 要求 | 说明 |
|---|---|---|
| Python | 3.10+ | 用到 `X \| None` 联合类型语法 |
| PostgreSQL | 15+ | 本项目在 16.4 上开发。低于 15 部分 `jsonb` 与 `LATERAL` 写法需调整 |
| pgvector | 0.5.0+（建议 0.7.0+） | **必须**。`VECTOR(512)` 类型与 HNSW 索引自 **0.5.0** 起支持；`vector_dims()` / `l2_normalize()` 自 **0.7.0** 起提供 |
| pgcrypto | 任意 | PostgreSQL 官方 contrib，通常随主包提供 |
| 磁盘 | ~200 MB | 代码 + 依赖 + 样例图；装深度模型另需 2~3 GB |

### 安装 pgvector

**Ubuntu / Debian**
```bash
sudo apt install postgresql-16-pgvector
```

**macOS (Homebrew)**
```bash
brew install pgvector
```

**Windows**
从 <https://github.com/pgvector/pgvector/releases> 下载对应 PG 版本的预编译包，
把 `vector.dll` 放进 `C:\Program Files\PostgreSQL\16\lib\`，
把 `vector.control` 与 `vector--*.sql` 放进 `...\share\extension\`。

**验证**
```bash
psql -d ich_heritage -c "CREATE EXTENSION IF NOT EXISTS vector; SELECT extversion FROM pg_extension WHERE extname='vector';"
```

### Python 依赖

核心依赖（`requirements.txt`）刻意保持精简：**不含 torch**，因此 `pip install` 几十秒就能装完，256 MB 内存的机器也能跑演示。

| 包 | 用途 |
|---|---|
| fastapi + uvicorn | Web 框架与 ASGI 服务器 |
| psycopg2-binary | PostgreSQL 驱动（自带 libpq，无需本机装客户端） |
| python-multipart | 解析文件上传 |
| pydantic | 请求/响应校验 |
| pillow | 图像解码、EXIF 矫正、格式转换 |
| numpy | 特征向量计算 |

### 可选：深度特征后端

想用 CLIP / ResNet 而不是内置的轻量后端：

```bash
pip install -r requirements-deep.txt     # 约 2~3 GB
```

装好后 `ICH_FEATURE_BACKEND=auto`（默认）会**自动优先选 clip**，其次 resnet，都没有才降级到 builtin。
首次运行会下载预训练权重（CLIP ViT-B/32 ≈ 350 MB）。

### 关于内置特征后端（builtin）

为了让系统"克隆下来就能跑"，我们实现了一个**纯 numpy 的 512 维纹样特征提取器**：

| 段 | 维度 | 内容 | 捕捉什么 |
|---|---|---|---|
| 结构 | 192 | 6×6 网格 × 8 方向梯度直方图（HOG-lite） | 线条走向（回纹的折线、缠枝的曲线） |
| 色彩 | 192 | 4×4 空间块 × HSV 的 H+S 二维直方图 | 色相与饱和度（苗绣靛蓝 / 青花钴蓝） |
| 纹理 | 128 | 8 尺度 Laplacian 方差 + 边缘密度 | 疏密程度（细密苗绣 vs 简洁回纹） |

实测数据（`python -m tests.test_feature_engine`）：

- 同一作品的 9 种翻拍变体，非旋转类最低相似度 **0.9076**，均值 **0.9642**
- 类内最低 **0.7610** 远高于类间最高 **0.4086**（不会把同一作品误判成不同母题）
- 蝴蝶纹改编样例 → 0.9182 → 判 `derived` ✓
- 鱼纹原创样例 → 0.6036 → 判 `original` ✓

它不是"玩具"：在纹样这个特定场景下区分度足够撑起真实的分级演示。但它**没有语义理解能力**、对大幅旋转/透视变形不鲁棒。生产环境请换 CLIP。

---

## 3. 初始化步骤

### 3.1 建库

```bash
createdb -U postgres ich_heritage
```

或用 psql：
```sql
CREATE DATABASE ich_heritage;
```

### 3.2 按顺序执行三个 SQL 脚本

**顺序不能乱**，因为脚本间有依赖：`01` 建表（`02` 的视图依赖表）、`02` 建触发器函数（`03` 的插入会触发它们）。

```bash
psql -U postgres -d ich_heritage -v ON_ERROR_STOP=1 -f sql/01_init.sql
psql -U postgres -d ich_heritage -v ON_ERROR_STOP=1 -f sql/02_index_trigger_view.sql
psql -U postgres -d ich_heritage -v ON_ERROR_STOP=1 -f sql/03_seed.sql
```

`-v ON_ERROR_STOP=1` 很关键：不加的话 psql 会"带错继续跑"，最后看起来全绿，实际缺一半对象。

| 脚本 | 内容 | 对象数 |
|---|---|---|
| `sql/01_init.sql` | 扩展、3 个枚举类型、8 张表（含全部 CHECK/UNIQUE/外键/注释） | 35 条语句 |
| `sql/02_index_trigger_view.sql` | 14 个索引、8 个触发器函数、9 个触发器、3 个视图、3 个角色与 GRANT | 73 条语句 |
| `sql/03_seed.sql` | 3 个用户、3 条公有母题（占位向量） | 4 条语句 |

> **脚本可重复执行吗？** `01` 用 `DROP TABLE ... CASCADE` + `DO $$ ... EXCEPTION WHEN duplicate_object`，可以重复跑（会清空数据）。`02` 用 `CREATE OR REPLACE` / `IF NOT EXISTS` / `DROP TRIGGER IF EXISTS`，也可以重复跑。`03` 用 `ON CONFLICT DO NOTHING`，重复跑不会产生重复数据。
> **但**：`01` 的 `DROP TABLE` 会级联删掉 `02` 建的视图和触发器，所以**重跑 `01` 之后必须重跑 `02`**。

### 3.3 回填母题向量

种子脚本只能用 SQL 生成"占位向量"（用 `sin()` 做确定性伪随机再归一化），因为真实特征必须由 CV 模型算。所以初始化后必须跑一次：

```bash
python -m app.cli backfill-motif-vectors
```

它会读 `public_motif.reference_img` 指向的参考图，用当前 AI 后端提取 512 维特征并写回，同时把 `vector_model` 从 `placeholder-v0` 更新成真实模型名。

输出示例：
```
共 3 条母题待回填（使用后端：builtin-hog-color-texture）
  ✓ MO-001 蝴蝶纹   ← builtin-hog-color-texture@1.0.0
  ✓ MO-002 缠枝莲纹 ← builtin-hog-color-texture@1.0.0
  ✓ MO-003 回纹     ← builtin-hog-color-texture@1.0.0
回填完成。现在上传一张纹样图即可跑通「AI 分析 → 专家审核 → 溯源链路」。
```

> **不回填会怎样？** 系统不会崩，但所有母题向量都是随机方向，检索结果毫无意义，AI 会给出错误的 `original` 建议。
> `python -m app.cli check` 的第 9 项会明确标出"⚠ 仍为占位向量"。

### 3.4 自检

```bash
python -m app.cli check
```

逐项检查 9 个方面，任何一项失败返回非零退出码（可接 CI）：

```
[1] 数据库连接 → [2] 扩展 → [3] 表（含行数）→ [4] 视图 → [5] 数据库角色
[6] 触发器 → [7] HNSW 向量索引 → [8] AI 特征后端 → [9] 公有母题向量
```

全部通过时最后一行是：`自检全部通过，可以启动服务：uvicorn app.main:app --reload`

---

## 4. 启动服务

```bash
python run.py
```

`run.py` 会先做一次启动前自检（数据库能否连通、AI 后端选了哪个），再拉起 uvicorn。数据库不通时**不会崩溃**，而是打印醒目的排查提示——这样你能在浏览器里打开页面、从 `/api/health` 看到具体原因，而不是对着一个 connection refused 的堆栈发呆。

```
======================================================================
  非遗数字资产分级确权与溯源系统
======================================================================
  ✓ 数据库   127.0.0.1:5432/ich_heritage
             PostgreSQL 16.4 | pgvector=0.8.0            ← 版本号以你实际安装的为准
  ✓ AI 后端  builtin-hog-color-texture v1.0.0  dim=512  [轻量手工特征]
======================================================================

  演示页面  http://127.0.0.1:8000/
  接口文档  http://127.0.0.1:8000/docs
```

常用参数：

```bash
python run.py --port 9000          # 换端口
python run.py --host 0.0.0.0       # 允许局域网访问（演示给同事看）
python run.py --no-reload          # 关闭热重载（演示时更稳，不会因改文件重启）
```

也可以直接用 uvicorn：
```bash
python -m uvicorn app.main:app --reload --port 8000
```

| 地址 | 内容 |
|---|---|
| `/` | 演示控制台（单页应用） |
| `/docs` | Swagger UI，可直接调所有接口 |
| `/redoc` | ReDoc 文档 |
| `/api/health` | 健康检查：数据库版本、pgvector 版本、8 张表行数、AI 后端 |

---

## 5. 演示流程（走通全链路）

这条链路对应验收标准：「上传一张非遗纹样图 → AI 分析 → 专家审核 → 生成审计日志 → 查询溯源链路」。
下面给出 **UI 操作**和**等价的 curl**两个版本。

### 5.0 实际界面截图

以下截图由 `python tests/shoot_ui.py` 用真实浏览器自动走完整流程生成（`output/02-截图证据/自动验收/`），
可复现 —— 不是手工拼的示意图。

| 步骤 | 截图 | 说明 |
|---|---|---|
| 登录 | `01-login.png` | 三个演示账号可点击直填 |
| 上传 | `02-upload.png` | 顶部红色横幅标明演示模式 |
| AI 预审 | `03-analyze.png` | 分级建议 + 判定理由 + top-3 相似度 + 命中规则 |
| 专家工作台 | `04-workbench.png` | 待审队列，数据源标注为视图 `v_asset_pending_audit` |
| 终审 | `05-approve.png` | AI 建议带标记，三档级别各有说明，底部写明单事务语义 |
| 终审后 | `06-after-approve.png` | 待审归零、已确权 +1，队列清空 |
| 监管台账 | `07-ledger.png` | AI 与专家分歧用「分歧」标出 |
| 溯源链路 | `08-trace.png` | 5 个事件节点，含触发器自动留痕的字段名 |

关键校验点在截图中直接可见：

- **`03-analyze.png`**：AI 给出「改编衍生」，但资产状态仍是「草稿」、权属级别仍是「公有母题」——
  AI 没有写 `right_level`，这是设计上就要能一眼看出来的事
- **`05-approve.png`**：专家选择「原创作品」，与 AI 建议不同 —— 推翻是允许且被记录的
- **`07-ledger.png`**：「AI 建议 / AI·专家」两列并排放，`分歧` 标记让复核有据可查
- **`08-trace.png`**：`DB 自动审计：right_level,audit_status` 这条是触发器写的，不是应用代码写的

重新生成截图：

```bash
pip install playwright && python -m playwright install chromium
python tests/shoot_ui.py
```

### 步骤 0：准备一张图

仓库已内置样例图，可以直接用：

| 文件 | 内容 | 预期 AI 判定 |
|---|---|---|
| `data/samples/sample_derived_butterfly.jpg` | 蝴蝶纹的改编作品（加了双弧翼、流苏珠串、同心圆、个人符号） | `derived` 改编衍生 |
| `data/samples/sample_original_fish.jpg` | 原创鱼纹 | `original` 原创作品 |
| `data/motifs/motif_butterfly.jpg` | 蝴蝶纹母题本身 | 上传它会判 `public` |

想重新生成样例图：`python -m app.motif_images`

### 步骤 1：传承人上传 + AI 分析

**UI**：用 `heritor01` 登录 → 「上传作品」页 → 选图、填标题 → 勾选"上传后立即触发 AI 分析" → 提交。

页面会显示 AI 分析报告：三个候选母题的相似度条形图、AI 建议的级别与置信度、判定依据，以及**折叠起来的 HNSW 执行计划**（用来证明向量检索真的走了索引，而不是全表扫描）。

**curl**：
```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"heritor01","password":"Passw0rd!"}' | jq -r .access_token)

curl -s -X POST http://127.0.0.1:8000/api/asset/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@data/samples/sample_derived_butterfly.jpg" \
  -F "title=蝴蝶纹改良绣片（演示）" \
  -F "description=在公有蝴蝶纹母题基础上重绘翼形并加入珠串流苏" \
  -F "auto_analyze=true" | jq
```

**这一步数据库发生了什么**（单事务）：

1. 算文件 SHA-256，查 `idx_asset_file_hash` 看是否重复（幂等）
2. 图片用 UUID 重命名落盘（**不使用用户原始文件名**，杜绝 `../../` 路径穿越）
3. `INSERT INTO asset`，`audit_status='draft'`，`right_level='public'`（默认值，无实际意义）
4. 提取 512 维特征 → `DELETE` 旧向量（若有）→ `INSERT INTO feature_vector`
5. `SELECT ... FROM public_motif ORDER BY base_vector <=> %s::vector LIMIT 5` 走 HNSW 索引
6. 分级器产出建议 → `INSERT INTO audit_record (state='pending')` + 作废该资产此前的 pending 记录
7. `UPDATE asset SET ai_suggest_level=..., ai_confidence=..., ai_model_version=..., audit_status='pending'`
   —— **注意这条 UPDATE 里没有 `right_level`**

此时 `trg_asset_touch`（BEFORE UPDATE）把 `version_no` +1、`updated_at` 刷新；因为 `audit_status` 从 `draft` 变成 `pending`，`trg_asset_right_change`（AFTER UPDATE）向 `system_audit_log` 写入了一条 `changed_fields = {audit_status}` 的快照。

### 步骤 2：查看待审列表

**UI**：用 `expert01` 登录 → 「专家工作台」。列表数据来自视图 `v_asset_pending_audit`。

**curl**：
```bash
ETOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"expert01","password":"Passw0rd!"}' | jq -r .access_token)

curl -s http://127.0.0.1:8000/api/audit/pending -H "Authorization: Bearer $ETOKEN" | jq
```

每条待审项包含：资产标题、传承人及所属单位、**AI 建议级别与置信度**、**top1 相似母题名称与相似度**、完整 top-K 相似度快照（JSONB）。

### 步骤 3：专家终审确权

**UI**：在工作台点某条记录的「终审」→ 弹窗顶部**并排显示 AI 建议与置信度**（明确标注"仅供参考"）→ 专家在三个级别卡片中选择 → 填意见 → 提交。

如果专家选择与 AI 建议不同，系统会置 `is_override = true`，这个字段正是长期评估 AI 准确率的原料。

**curl**：
```bash
curl -s -X POST http://127.0.0.1:8000/api/audit/1/approve \
  -H "Authorization: Bearer $ETOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"final_level":"derived","opinion":"翼形重绘、新增珠串与同心圆装饰，改编幅度显著，同意认定为改编衍生"}' | jq
```

**这是全系统唯一能写 `right_level` 的路径**，且它在**一个事务**里完成 6 步：

| 步 | 操作 | 作用 |
|---|---|---|
| 1 | `SELECT ... FROM asset WHERE asset_id=%s FOR UPDATE OF a` | **悲观锁**。同一资产的并发终审在此串行化 |
| 2 | 状态机校验 | 已 `approved` → 409；非 `pending` → 409 |
| 3 | `SELECT ... FROM audit_record WHERE asset_id=%s AND state='pending' FOR UPDATE` | 锁住待审记录，防止并发领取 |
| 4 | `UPDATE audit_record SET state='approved', expert_id, expert_level, is_override, decided_at` | 写终审结论 |
| 5 | 校验 `cur.rowcount == 1` | 影响行数异常立即抛错 → **整个事务回滚** |
| 6 | `UPDATE asset SET right_level=%s, audit_status='approved'` | 写权属结论（触发器自动留痕） |

任一步失败，前 5 步全部回滚 —— 不会出现"审核流水说通过了但资产状态还是待审"这种撕裂状态。

响应里的 `db_audit_log_ids` 字段是**回读 `system_audit_log` 得到的日志主键**，这是触发器确实生效的硬证据，不是靠代码"假装"写日志。

### 步骤 4：查看溯源链路

**UI**：任意页面点作品的「溯源」，弹窗以时间轴展示 4 类事件，用不同颜色区分。

**curl**：
```bash
curl -s http://127.0.0.1:8000/api/asset/1/trace -H "Authorization: Bearer $ETOKEN" | jq
```

数据源是视图 `v_asset_trace_chain`，一行一个事件节点，`event_type` 分四类：

| `event_type` | 含义 | 数据来源 |
|---|---|---|
| `root` | 资产登记（含 `file_hash`，证据固定起点） | `asset` + `app_user` |
| `audit` | 权属审核决策（AI 预审 / 专家终审通过 / 驳回） | `audit_record` |
| `license` | 授权流转 | `license_record` |
| `system_log` | **数据库触发器自动写的快照** | `system_audit_log` |

视图返回节点后，应用层还会做 4 项**链式完整性校验**，把结果附在响应里：

1. 每个节点的 `file_hash` 符合 `^[0-9a-f]{64}$`
2. 授权链 `prev_license_id` 首尾相接，无断点
3. 链中必须存在至少一个终审决策节点
4. 链中必须存在触发器写入的 `system_log` 节点

任何一项不过，响应会明确标出来 —— 这比"返回一堆数据让调用方自己判断"负责得多。

### 步骤 5：登记授权流转

**UI**：传承人登录 → 我的作品 → 「授权」→ 填被授权方、类型、地域、期限、费用 → 提交。

```bash
curl -s -X POST http://127.0.0.1:8000/api/asset/1/license \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"license_type":"exclusive","licensee_user_id":2,"scope":"文创产品开发与销售","territory":"中国大陆","start_date":"2026-01-01","end_date":"2028-12-31","fee_cny":80000}' | jq
```

两个设计点：

- **独占授权唯一性**由部分唯一索引保证：
  ```sql
  CREATE UNIQUE INDEX uq_lic_exclusive_active
      ON license_record (asset_id)
      WHERE license_type = 'exclusive' AND status = 'active';
  ```
  同一资产只能存在一份"现行有效"的独占授权，历史上失效的不受影响。重复登记时 `UniqueViolation` 被全局处理器转成 409 和可读提示。
- **只有已终审通过（`approved`）的资产才能登记授权**。业务上这很关键：权属未定就授权，等于给出无法兑现的承诺。

### 步骤 6：监管台账

**UI**：`expert01` / `admin01` → 「监管台账」。数据源视图 `v_asset_ledger`，一资产一行。

**curl**：
```bash
curl -s http://127.0.0.1:8000/api/ledger -H "Authorization: Bearer $ETOKEN" | jq
```

每行聚合了：权属结论、AI 建议与专家是否一致（`ai_expert_agree`）、母题血缘、授权数量/有效授权数/授权费合计、审核次数、**DB 自动审计日志条数**、最近审计时间、衍生作品数。

`ai_expert_agree` 和 `audit_times` 这两列让监管方可以直接看出：AI 在哪些母题上判得准、哪些资产被反复审核过。

### 步骤 7：验证"AI 改不了权属"

这条建议亲手跑一遍，它是整个系统的合规基石。

用传承人令牌，尝试分析一件**已终审**的资产：

```bash
curl -s -X POST http://127.0.0.1:8000/api/asset/1/analyze -H "Authorization: Bearer $TOKEN"
```

会看到流程仍然把 `ai_suggest_level` 更新了，但 `right_level` **纹丝不动**。
再试直接用 SQL（以传承人角色）：

```sql
SET ROLE ich_heritor;
UPDATE asset SET right_level = 'original' WHERE asset_id = 1;
-- ERROR:  permission denied for table asset
```

数据库层直接拒绝 —— 因为 `ich_heritor` 只有 `UPDATE (title, description, ..., ai_suggest_level, ...)` 的**列级权限，不含 `right_level`**。

---

## 6. 文件清单与用途

> **只想拿交付物的话，看 `output/产物清单.md` 就够了** —— `output/` 按「要交的 / 证据 / 过程材料」
> 分了三类，那份额外清单逐目录说明了每个文件是什么、要不要交。本节是全仓技术清单。

```
.
├── bootstrap.py                    # ★ 推荐入口：一键自检 + 启动（真库/演示自动判定）
├── run.py                          # 直接启动（含启动前自检，不做降级判定）
├── docker-compose.yml              # ★ 一条命令起带 pgvector 的真库
├── .env.example                    # 配置模板（cp 成 .env 生效）
├── requirements.txt                # 核心依赖（不含 torch）
├── requirements-deep.txt           # 可选：CLIP / ResNet 后端
├── README.md                       # 本文档
│
├── scripts/
│   ├── init_db.py                  # ★ 真库一键初始化：起容器→等就绪→建表→回填向量
│   ├── make_seed_images.py         # 生成 8 张演示作品图（以母题图为底做可控扰动）
│   ├── check_seed.py               # ★ 种子护栏：校验 AI 字段是否为真实算法输出，--write 回写
│   ├── init_seed.py                # 把 data/seed_demo.json 灌进真实 PostgreSQL（--dry-run）
│   ├── build_docx.py               # ★ 生成交付说明书 Word（数据实时读取，不硬编码）
│   ├── build_demo_page.py          # ★ 生成纯界面演示页（默认无左栏，--with-doc 出双栏版）
│   ├── build_demo_v2.py            # ★ 生成互动版演示页（拖动登录 + 9 个页面）
│   ├── build_coursework.py         # ★ 生成课程作业两件套（代码说明 + 数据说明 + 数据字典 + 代码包）
│   ├── v2_assets/                  # 互动版的样式与脚本源文件（css/html/js，各司其职）
│   │   ├── style.css               # 全部样式
│   │   ├── login.html              # 登录页结构
│   │   ├── app.html                # 应用骨架与 9 个页面
│   │   ├── app.js                  # 数据层 / 登录 / 框架 / 通用组件
│   │   ├── pages1.js               # 概览 / 作品管理 / 母题库
│   │   └── pages2.js               # 上传 / 终审 / 台账 / 溯源 / 审计 / 设置
│   ├── gen_rules.py                # ★ 从 SQL 提取 CHECK/UNIQUE/外键/触发器 → 生成 sim/rules.py
│   ├── probe_drift.py              # 故意制造不同步，验证漂移检测真的会失败（跑完按字节还原）
│   └── preview_docx.py             # 自查工具：把 docx 转 HTML 预览，用于核对排版
│
├── sim/
│   ├── ruleast.py                  # 规则表达式 AST + 三值逻辑求值器（手写）
│   ├── rules.py                    # ★ 从 SQL 自动生成的规则表（请勿手改）
│   └── verdict.py                  # 行级/操作级判定：NOT NULL、长度、枚举、CHECK、触发器
│
├── docs/
│   ├── README.md                   # ★ 文档索引：6 份文档各看什么、给谁看
│   ├── 01-工程说明书/               # 给评审与使用者
│   │   ├── 项目说明与进展证明清单.md   # 大白话版项目说明 + 阶段证明材料清单
│   │   ├── 交付物说明.md              # 四份交付物的打开方式、结构与使用动线
│   │   └── 演示前端与示例数据说明.md   # 怎么启动 / 看到什么 / 数据从哪来
│   ├── 02-研究规划/
│   │   └── 研究方向精进路线图.md       # ★ 待办真相源：15 项研发排期与完成标准
│   ├── 03-选题材料/
│   │   └── 选题报告.md                # 选题论证 Markdown 源（Word 版在 output/）
│   ├── 04-归档/
│   │   └── 后续工作计划与前端说明.md   # 已作废（2026-10-26 的判断），仅留档
│   └── verify/                     # 证据而非说明：实跑报告 + 原始 JSON + PG 重建日志
│                                   # 路径被 build_docx.py 写进 Word 正文，勿改名
│
├── output/
│   ├── 产物清单.md                  # ★ 导航：哪三类、每类是什么、哪些要交
│   ├── 01-交付物/                   # 要交出去的东西（4 份）
│   │   ├── 非遗数字资产分级确权与溯源系统_项目交付说明书.docx   # ★ 正式提交件
│   │   ├── 演示页面.html            # ★ 纯界面版，双击即开，零依赖零网络
│   │   ├── 演示页面_互动版.html      # ★ 拖动登录版，9 个页面
│   │   └── 非遗数字资产分级确权与溯源AI工具-选题报告.docx
│   ├── 02-截图证据/                 # 证明"跑过、跑对了"的截图（5 个来源）
│   │   ├── 交付文档配图/            # Word 里的 5 张 + 演示页内嵌的 6 张，自截图裁剪而来
│   │   ├── 前端功能/                # 手动走查时的 12 张
│   │   ├── 实跑验证/                # 少量样本实跑完整流程的 7 张
│   │   ├── 自动验收/                # shoot_ui.py 产出（四角色五页签）
│   │   ├── 演示页验收/              # check_demo_page.py 产出
│   │   └── 互动版验收/              # check_demo_v2.py 产出
│   ├── 03-过程材料/                 # 不交，留档备查
│   │   └── 选题报告流水线/          # 选题报告生成过程中的 stage2/3、修补脚本
│   │
│   └── 04-课程作业/                 # ★ 按课程要求组织的「代码 + 数据」两件套
│       ├── 提交说明.md              # 一页纸：30 秒上手、目录对照、两个边界
│       ├── 课程作业_代码包.zip      # 70 个文件的可运行工程，解压即用
│       ├── 01-代码/                 # 代码说明 docx + 文件清单 csv
│       └── 02-数据/                 # 数据说明 docx + 数据字典 + 5 张 CSV + 11 张图
│
├── sql/
│   ├── 00_compat_vector.sql        # ★ pgvector 兼容层：无 pgvector 时用 NUMERIC[] + 自定义 <=>
│   ├── 01_init.sql                 # 扩展 + 枚举 + 8 张表 + 约束 + 注释
│   ├── 02_index_trigger_view.sql   # 14 索引 + 7 函数 + 8 触发器 + 3 视图 + 3 角色/GRANT
│   └── 03_seed.sql                 # 3 用户 + 3 公有母题
│
├── app/
│   ├── main.py                     # FastAPI 入口：lifespan 建池/演示模式判定、CORS、5 个异常处理器
│   ├── api.py                      # 18 个 HTTP 端点（只做编解码 + 鉴权 + 调服务层）
│   ├── services.py                 # 业务核心：全部 SQL 与事务边界都在这里
│   ├── demo.py                     # ★ 演示模式：内存版 8 张表 + 同名同签名业务函数
│   ├── db.py                       # 连接池 + open_tx 统一事务上下文 + SET LOCAL ROLE
│   ├── auth.py                     # HS256 JWT（纯标准库）、get_current_user、require_roles
│   ├── schemas.py                  # Pydantic 请求/响应模型
│   ├── config.py                   # 配置（环境变量 > .env > 默认值）
│   ├── cli.py                      # 运维 CLI：check / backfill-motif-vectors / demo-check / demo-seed
│   ├── motif_images.py             # 程序化生成 3 张母题参考图 + 2 张样例作品
│   │
│   ├── ai/
│   │   ├── features.py             # 特征提取：clip / resnet / builtin 三档 + auto 降级
│   │   ├── classifier.py           # 分级建议器（纯函数，不 import 任何数据库模块）
│   │   └── similarity.py           # 向量检索封装（原生 pgvector / 降级精确扫描双模式）
│   │
│   └── static/
│       ├── index.html              # 单页前端（宣纸底色 + 朱砂红 + 演示模式横幅）
│       └── app.js                  # 原生 JS，无构建
│
├── data/
│   ├── seed_demo.json              # ★ 种子数据唯一真源：4 账号 / 3 母题 / 8 作品 / 14 条审核 / 5 笔授权
│   ├── motifs/                     # 3 张公开母题参考图
│   ├── samples/                    # 2 张演示样例作品
│   ├── works/                      # 8 张种子作品图（由 make_seed_images.py 生成）
│   └── uploads/                    # 运行时上传目录（UUID 命名）
│
└── tests/
    ├── run_all.py                  # ★ 测试总入口：三个分组，一条命令跑完并汇总
    ├── test_sql_structure.py       # SQL 静态结构校验（无库也能跑）
    ├── test_rules_sync.py          # ★ 59 项规则单一真相：漂移检测 / 覆盖完整性 / 求值语义
    ├── test_rules_parity.py        # ★ 75 项规则对拍：同一组边界样本喂真实库与 Python，比判定
    ├── test_compat_vector.py       # ★ 39 项兼容层验证（cosine 公式、归一化、脚本结构）
    ├── test_invariants.py          # ★ 88 项源码级不变量审查
    ├── test_classifier.py          # 24 项分级器测试
    ├── test_feature_engine.py      # 17 项特征引擎测试
    ├── e2e_demo.py                 # ★ 52 项端到端接口测试（15 个场景）
    ├── run_e2e_inline.py           # 进程内起服务 + 跑 e2e（一条命令）
    ├── shoot_ui.py                 # ★ 49 项三角色逐页 UI 断言 + 截图
    ├── check_demo_page.py          # ★ 52 项纯界面演示页验收（file:// 直开，含断网测试）
    ├── check_demo_v2.py            # ★ 58 项互动版验收（拖动登录、终审、阈值联动）
    ├── check_pg.py                 # ★ 46 项真实 PostgreSQL 验收（45 通过 / 1 缺口）
    └── tune_weights.py             # 特征段权重标定脚本（可复现）
```

### 各文件详解

| 文件 | 关键点 |
|---|---|
| `bootstrap.py` | 四步自检（依赖 → 数据库 → AI 后端 → 启动）。数据库探测分四层：TCP 可连 / 认证通过 / 8 张表齐全 / pgvector 有无。**每一层失败都给出可直接复制执行的修复命令**，而不是一句"连接失败" |
| `sql/00_compat_vector.sql` | 无 pgvector 时的兼容层：`NUMERIC[]` + 自定义 `<=>` 运算符 + `l2_normalize` / `vector_dims` / `ich_to_vector`。全部包在 `IF NOT ich_vector_is_native()` 里，有 pgvector 时一行都不执行，不会与原生运算符冲突 |
| `app/demo.py` | 演示模式的内存存储。**业务函数签名与 `services.py` 完全一致**，所以 `api.py` 里只需切换被调函数，路由与校验逻辑零改动。文件头逐条列明「哪些约束在此模式下不生效」 |
| `app/ai/similarity.py` | `cast_vec()` 统一处理两种模式的向量参数写法（`::vector` 与 `ich_to_vector()`），避免维护两份 SQL；`_apply_vector_settings()` 在降级模式跳过 `hnsw.ef_search`（该参数不存在会报错） |
| `app/services.py` | **业务唯一入口**。`approve_asset()` 是全系统唯一写 `right_level` 的函数，6 步单事务 + `FOR UPDATE` + `rowcount` 校验 + 回读触发器日志；`analyze_asset()` 明确不写 `right_level`；`trace_asset()` 在视图结果上再做 4 项链式校验 |
| `app/db.py` | `open_tx(app_role)` 是统一事务上下文：`SET LOCAL ROLE` 切角色（作用域限本事务，不污染连接池）、出块 COMMIT、异常 ROLLBACK、归还前复位 `autocommit` |
| `app/auth.py` | JWT 只用 `hmac` + `base64` + `json` 实现，不引第三方库；用 `compare_digest` 防时序攻击；`get_current_user` 每请求回查用户表，使"停用账号"立即生效 |
| `app/ai/classifier.py` | **不 import 任何数据库模块**。这是刻意的：从类型系统上就断绝了"分级器顺手改一下权属"的可能 |
| `app/ai/similarity.py` | 每次检索前 `SET LOCAL enable_seqscan=off` + `hnsw.ef_search=100`。小数据量下 planner 会选 seqscan，虽然结果相同但就不是"向量索引检索"了，演示时需要强制走索引 |
| `app/ai/features.py` | 三档后端 + auto 降级链；所有向量 L2 归一化；模型版本号必须与向量一起落库 |
| `app/motif_images.py` | 用 Pillow 程序化画纹样，不依赖外部图片素材，保证仓库自包含 |
| `sql/02_...sql` | 见 [§8 数据库原理对照表](#8-数据库原理对照表) |

---

## 7. 关键设计：AI 为什么改不了权属

用户原话要求："**明确 AI 只写建议，绝不直接改 `asset.right_level`**"。
这条如果只靠"我记得别写"，三个月后必然被某次紧急需求打破。所以我们用**四层机制**把它焊死，
外加一道补在列权限缺口上的**角色闸门**（见「第一层补」）。

### 第一层：数据库列级权限（最硬）

```sql
-- 传承人只能更新这些列，★ 不含 right_level
GRANT UPDATE (title, description, file_path, file_hash, file_size, mime_type,
              audit_status, ai_suggest_level, ai_confidence, ai_model_version, motif_id)
    ON asset TO ich_heritor;

-- 只有专家能改权属结论
GRANT UPDATE (audit_status, right_level, motif_id) ON asset TO ich_expert;
```

后端以 `ich_app` 连接，每个请求用 `SET LOCAL ROLE` 切到业务角色。
所以即便应用代码写出 `UPDATE asset SET right_level=...`，数据库也会抛 `InsufficientPrivilege`，
被 `main.py` 的异常处理器转成 **403 + "数据库层权限拒绝"**。

这不是应用层的 `if role == 'expert'`，而是**数据库自己拒绝**。

### 第一层补：状态流转的角色闸门

上面那条 GRANT 有一个表达不了的东西：**同一个 `audit_status` 列，不同角色能写进去的值不一样。**

传承人必须有这一列的写权限 —— 上传草稿、提交待审都得用它；可一旦有了写权限，
`UPDATE asset SET audit_status='approved'` 就不含任何被禁列，列级权限拦不住，
语句成功、资产显示"已确权"，而整个 `approve_asset` 的状态机根本没经过。
**列权限守的是"哪一列"，守不住"改成什么"。**

所以这一半只能落在触发器上（`trg_asset_status_guard`，BEFORE INSERT OR UPDATE ON asset）：

| 角色 | 允许 | 拒绝 |
|---|---|---|
| `ich_heritor` | 新建 `draft + public`；`draft/rejected → pending`；改标题等元数据 | 写 `approved/rejected`；任何改 `right_level`；自带上来的结论 |
| `ich_expert` | 改结论，但必须在本事务声明 `ich.finalize_gate` | 无标记直改终态/权级；把已终审改回未终审 |
| `ich_admin` | 只读 | 改 `audit_status` / `right_level` |

```sql
-- 终审事务的第一句话（app/db.py: declare_finalize_gate）
SELECT set_config('ich.finalize_gate', 'approve_asset', true);
```

这是"终审只有一条路"的兑现方式：**不是注释里约定，而是没有这句话就写不进去。**
反过来也要说清楚它的边界 —— 标记名不是密码，知道名字的专家仍然可以自己 `SET LOCAL`；
真正不可绕过的是角色判断（传承人那一支，与标记无关）。标记收窄的是"专家"这一支，
让任何一次误写的 UPDATE 立刻失败并被告知该走哪条路。

验证它的是 `tests/check_pg.py` 第 13 节，9 条断言，全部以真实 `SET LOCAL ROLE` 执行：
传承人改 `approved` 必须返回 42501 且错误来自数据库（不是应用层的 403），
专家不带标记改终态必须失败、带上标记必须成功，已终审不可回退，监管改状态被拒。

### 第二层：源码不变量测试

`tests/test_invariants.py` 直接扫描源码字符串：

- `services.analyze_asset` 的函数体里**不得出现**写 `right_level` 的 SQL
- 全工程只有 `approve_asset` 一处出现 `SET right_level`
- `classifier.py` 不得 import `app.db` / `app.services`

88 项检查全部通过才算数。任何人日后想加一条"AI 自动确权"的捷径，CI 会立刻红。

### 第三层：分级器物理隔离

`app/ai/classifier.py` 是**纯函数模块**，不 import 任何数据库模块。它只能接收 `SimilarityHit` 列表、返回 `Suggestion` 数据类。
它没有能力、也没有接口去改任何东西。

### 第四层：写入路径唯一 + 事务留痕

就算前三层都被绕过，`asset.right_level` 的每次变更都会被 `trg_asset_right_change`（AFTER UPDATE 行级触发器）自动写入 `system_audit_log`，记录新旧快照与 `changed_fields`。
而 `system_audit_log` 本身有防篡改触发器：

```sql
CREATE TRIGGER trg_syslog_immutable
    BEFORE UPDATE OR DELETE ON system_audit_log
    FOR EACH ROW EXECUTE FUNCTION fn_forbid_modify();
```

谁改了权属、什么时候改的、从什么改成什么、用什么数据库角色改的 —— 全部留痕，且**改不掉**。

---

## 8. 数据库原理对照表

这张表把需求里要求的「注释标注数据库原理对应点」集中呈现。代码里每处也有对应注释。

### 8.1 范式与结构设计

| 原理点 | 设计 | 在哪 |
|---|---|---|
| **3NF** | 每个非主属性完全依赖于主键，消除传递依赖。例如 `asset` 只存 `owner_id`，不冗余存 `owner_name`、`owner_org` | `01_init.sql` |
| **1:1 垂直拆分** | `feature_vector` 从 `asset` 拆出。512 维向量占 2 KB/行，与主表放一起会严重拖慢 `asset` 全表扫描与缓存命中 | `01_init.sql` `[4]` |
| **枚举类型收敛值域** | `right_level_enum` / `audit_status_enum` / `user_role_enum`，比 `VARCHAR` 省空间且强约束 | `01_init.sql` |
| **自引用外键构成 DAG** | `asset_genealogy` 的 `parent_asset_id` / `child_asset_id` 各自指向 `asset`，配合 `CHECK (parent <> child)` 阻止自环；`UNIQUE(parent, child)` 阻止重复边 | `01_init.sql` `[7]` |
| **链式自引用** | `license_record.prev_license_id → license_record.license_id`，把授权串成流转链 | `01_init.sql` `[6]` |
| **ON DELETE RESTRICT 保护证据链** | 几乎所有外键都用 `RESTRICT` 而非 `CASCADE`：有资产关联的用户不能物理删除，否则溯源链断掉。**唯一例外**是 `feature_vector → asset` 用 `CASCADE`（向量是派生数据，随资产走） | `01_init.sql` |
| **CHECK 表达业务不变量** | `ck_audit_decided`：已决策必须同时有专家、决策时间、终审结论；未决策则三者皆空。`ck_asset_ai_pair`：AI 建议级别与置信度必须同生同灭 | `01_init.sql` |
| **部分唯一索引** | `uq_lic_exclusive_active ON license_record(asset_id) WHERE license_type='exclusive' AND status='active'` —— 把唯一性限制在"现行独占授权"这个子集，历史失效记录不受影响 | `01_init.sql` |

### 8.2 索引

| 索引 | 类型 | 服务的查询 |
|---|---|---|
| `idx_asset_file_hash` | B-Tree | 上传查重（SHA-256 命中即复用） |
| `idx_asset_pending` | **部分索引** `WHERE audit_status='pending'` | 专家工作台待审列表。只索引少数行，体积小命中快 |
| `idx_asset_owner_time` | 组合 B-Tree `(owner_id, created_at DESC)` | 传承人"我的作品"，同时覆盖等值过滤与排序 |
| `idx_asset_right_level` | B-Tree | 台账按权属级别统计 |
| `idx_asset_motif` | 部分索引 `WHERE motif_id IS NOT NULL` | 按母题汇总衍生作品 |
| `idx_audit_record_asset` | 组合 `(asset_id, created_at DESC)` | 溯源链路的审核节点 |
| `idx_license_asset` | 组合 `(asset_id, created_at)` | 溯源链路的授权节点 |
| `idx_audit_log_target` | 组合 `(table_name, record_id, logged_at DESC)` | 按表+记录定位审计历史 |
| `idx_audit_log_time` | B-Tree `(logged_at DESC)` | 监管复盘时间轴 |
| `idx_genealogy_child` | B-Tree | 谱系反向查询（谁改编了我） |
| `idx_public_motif_hnsw` | **HNSW** `vector_cosine_ops (m=16, ef_construction=64)` | 母题相似检索（核心） |
| `idx_feature_vector_hnsw` | **HNSW** 同上 | 以图搜图找相似资产（发现衍生关系/抄袭） |
| `idx_audit_record_sim_gin` | **GIN** `jsonb_path_ops` | 按 JSONB 内容检索相似度快照 |

**为什么用 HNSW 而不是 IVFFlat？** HNSW 是图结构近似最近邻，"边写边查"场景下表现稳定，不需要预先训练聚类中心；IVFFlat 需要足够数据量调 `lists` 且对增量写入不友好。本项目是持续上传的场景，选 HNSW。

**余弦距离怎么算的？** 向量已 L2 归一化，`<=>` 返回 `1 - cos`，所以 `相似度 = 1 - 距离`，clamp 到 `[0,1]`。

### 8.3 触发器（7 个）

| 触发器 | 时机 | 作用 |
|---|---|---|
| `trg_asset_right_change` ★ | `AFTER INSERT/UPDATE/DELETE ON asset` | **核心审计**。逐列 `IS DISTINCT FROM` 比对 `right_level`/`audit_status`/`motif_id`/`owner_id`，有变更才写 `system_audit_log`（含新旧快照 + `changed_fields` 数组）。普通元数据更新（改标题）不产生噪音 |
| `trg_asset_touch` | `BEFORE UPDATE ON asset` | `updated_at = now()`、`version_no = OLD.version_no + 1`（乐观锁版本号） |
| `trg_asset_status_guard` ★ | `BEFORE INSERT OR UPDATE ON asset` | **角色闸门**。同是 `audit_status` 这一列，传承人只能 `draft/rejected → pending`、专家写终态必须带 `ich.finalize_gate` 事务标记、监管只读。补的是列级 GRANT 表达不了的那一半（"改成什么"，而不只是"哪一列"） |
| `trg_audit_record_no_delete` | `BEFORE DELETE ON audit_record` | 一律禁止删除审核流水（流水只能追加） |
| `trg_audit_record_guard` | `BEFORE UPDATE ON audit_record` | 已 `approved`/`rejected` 的记录不可再改；**放行** `pending → expert_review → approved/rejected` 的状态迁移 |
| `trg_syslog_immutable` | `BEFORE UPDATE OR DELETE ON system_audit_log` | 审计日志不可改不可删 |
| `trg_license_no_delete` | `BEFORE DELETE ON license_record` | 授权记录只能追加/改状态，不能删除 |
| `trg_motif_vector_norm` | `BEFORE INSERT OR UPDATE OF base_vector` | 母题向量自动 L2 归一化，保证 cosine 距离计算稳定 |

> **一个已被删掉的触发器，值得单独说：**
> 早期版本在 `audit_record` 上还挂过 `trg_audit_record_immutable`（`BEFORE UPDATE OR DELETE` → `fn_forbid_modify`），
> 意图是"追加式不可改"。但它对**任何** UPDATE 都无条件抛异常，而专家终审的核心动作恰恰是
> `UPDATE audit_record SET state='approved'` —— 挂上它会把整条终审链路彻底堵死。
> 现在 `audit_record` 的"不可篡改"由 `no_delete`（禁删）+ `guard`（禁改已终审）**分工**实现，粒度更准。
> `tests/test_invariants.py` 里有一条专门的负向回归检查，防止这个触发器被再次加回去。

**关键性质：触发器与主事务同生共死。** `AFTER UPDATE` 触发器在同一事务内执行，主业务回滚时审计日志一并回滚 —— 不会留下"操作没成功但日志说成功"的脏记录。

**执行顺序**：PostgreSQL 同事件同级别按触发器名**字母序**执行。`trg_asset_right_change`(AFTER) 天然晚于 `trg_asset_touch`(BEFORE)，符合预期。

### 8.4 视图（3 个）

| 视图 | 用途 | 技术点 |
|---|---|---|
| `v_asset_pending_audit` | 专家工作台数据源 | `LEFT JOIN LATERAL` 为每行取最新一条待审记录；从 `similarity_json` JSONB 里抽 top1 母题 |
| `v_asset_trace_chain` | 溯源链路 | 4 段 `UNION ALL` 把子查询结果（表）拼起来，统一成 `(asset_id, seq_no, event_at, event_type, summary, actor, detail)` 的事件流模型 |
| `v_asset_ledger` | 监管台账 | 一资产一行；`LEFT JOIN LATERAL` 做授权聚合（`count` / `count FILTER (WHERE status='active')` / `sum FILTER`），**避免多表 JOIN 产生笛卡尔积放大** |

**为什么用普通视图而非物化视图？** 溯源链路必须查到实时一致数据 —— 刚完成的终审，打开溯源页就得看到。物化视图需要 `REFRESH`，在审计场景下是不可接受的语义偏差。代价是每次执行查询计划，但对监管方这种低频查询完全可接受。

**视图的另一个作用**：数据独立性。应用只依赖视图列名，底层表结构调整不影响上层；同时视图可以限定可见列（比如不暴露 `password_hash`）。

### 8.5 事务与并发

| 原理点 | 实现 |
|---|---|
| **事务原子性** | `open_tx()` 统一上下文：块内 SQL 同生共死，异常 `ROLLBACK` |
| **悲观锁** | 终审时 `SELECT ... FROM asset WHERE asset_id=%s FOR UPDATE OF a`，并发终审串行化 |
| **防并发领取** | `SELECT ... FROM audit_record WHERE asset_id=%s AND state='pending' FOR UPDATE` |
| **丢失更新检测** | `rowcount != 1` 立即抛错回滚 + `version_no` 乐观锁版本号 |
| **隔离级别** | 默认 `READ COMMITTED`（PostgreSQL 默认）。**不加 `FOR UPDATE` 会出问题**：两个专家同时终审同一资产，都能读到 `pending`，后者会覆盖前者结论 |
| **`SET LOCAL` 的作用域** | 角色切换限定在当前事务，事务结束自动失效。**这比 `SET ROLE` 安全得多** —— 后者会污染连接池里被复用的连接，导致下一个请求带着上个用户的权限 |

### 8.6 权限管控（数据库层 GRANT）

| 角色 | SELECT | INSERT | UPDATE | 特殊限制 |
|---|---|---|---|---|
| `ich_heritor` 传承人 | `public_motif`、`app_user`（部分列）、`asset`、`feature_vector`、`audit_record`、`license_record`、3 个视图 | `asset`、`feature_vector`、`audit_record` | **`asset` 仅 11 个非权属列** ★ 不含 `right_level` | 看不到别人的资产（应用层 `owner_id` 过滤 + 视图过滤） |
| `ich_expert` 专家 | 全部业务表 + 视图 + `system_audit_log` | `audit_record` | `audit_record`；`asset` 仅 `(audit_status, right_level, motif_id)` | 无 `public_motif` 写权限（母题库归监管方） |
| `ich_admin` 监管 | 全表全视图 | `public_motif` | `public_motif` | `asset.right_level` **无写权限**（监管方只读，防止裁判兼运动员） |
| 全体 | — | — | — | `system_audit_log` 的 `UPDATE`/`DELETE`/`TRUNCATE` 被 `REVOKE`；`audit_record` 的 `UPDATE`/`DELETE` 对 heritor/admin 被 `REVOKE` |

另外 `REVOKE ALL ON SCHEMA public FROM PUBLIC` —— 强制显式授权，不依赖 PostgreSQL 的历史默认行为。

---

## 9. API 一览

全部前缀 `/api`。除 `/health`、`/meta`、`/auth/login` 外均需 `Authorization: Bearer <token>`。

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/health` | 公开 | 数据库/pgvector 版本、8 张表行数、AI 后端 |
| GET | `/meta` | 公开 | 级别标签、阈值、可用角色（前后端共用一份定义） |
| POST | `/auth/login` | 公开 | 登录，返回 JWT |
| GET | `/auth/me` | 登录 | 当前用户 |
| POST | `/asset/upload` | heritor/admin | 上传（可选 `auto_analyze=true` 立即分析） |
| GET | `/asset/mine` | 登录 | 我的作品 |
| GET | `/asset/{id}` | 登录 | 资产详情（heritor 只能看自己的） |
| GET | `/asset/{id}/image` | 登录 | 原图（含路径穿越防御） |
| POST | `/asset/{id}/analyze` | 登录（heritor 限本人） | 触发 AI 分析，产出**建议** |
| GET | `/audit/pending` | expert/admin | 待审列表（视图 `v_asset_pending_audit`） |
| POST | `/audit/{id}/approve` | expert/admin | **终审确权**（单事务） |
| POST | `/audit/{id}/reject` | expert/admin | 驳回 |
| POST | `/asset/{id}/license` | heritor/admin | 登记授权 |
| GET | `/asset/{id}/licenses` | 登录 | 该资产的授权列表 |
| GET | `/asset/{id}/trace` | 登录 | 溯源链路（视图 + 4 项链式校验） |
| GET | `/ledger` | expert/admin | 监管台账（视图 `v_asset_ledger`） |
| GET | `/motifs` | 登录 | 公有母题列表 |
| GET | `/users` | expert/admin | 用户列表（供选被授权方，**不含口令散列**） |

数据库异常统一映射：

| 数据库错误 | HTTP | 说明 |
|---|---|---|
| `InsufficientPrivilege` | 403 | 数据库层 GRANT 拦截（这条能跑起来说明权限真的在生效） |
| `UniqueViolation` | 409 | 重复确权 / 已有独占授权 |
| `ForeignKeyViolation` | 422 | 引用不存在，或记录被引用不能删 |
| `CheckViolation` | 422 | 违反业务规则 |

---

## 10. 配置项

全部可用环境变量覆盖，默认值在 `app/config.py`。

### 数据库

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `ICH_DB_HOST` | `127.0.0.1` | |
| `ICH_DB_PORT` | `5432` | |
| `ICH_DB_NAME` | `ich_heritage` | |
| `ICH_DB_USER` | `postgres` | 需有权限 `SET ROLE` 到三个 `ich_*` 角色，故用高权限账号 |
| `ICH_DB_PASSWORD` | 空 | |
| `ICH_DB_POOL_MIN` | `2` | |
| `ICH_DB_POOL_MAX` | `10` | 并发上限，过长会压垮数据库 |

### 鉴权

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `ICH_JWT_SECRET` | `dev-only-secret-change-me-in-production` | **生产必须换** |
| `ICH_JWT_EXPIRE_MINUTES` | `720` | |

### AI

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `ICH_FEATURE_BACKEND` | `auto` | `auto` / `clip` / `resnet` / `builtin` |
| `ICH_TOPK` | `5` | 返回 top-K 相似母题 |
| `ICH_SIM_PUBLIC` | `0.92` | ≥ 此值判 `public` |
| `ICH_SIM_DERIVED` | `0.80` | ≥ 此值且 < `public` 判 `derived` |

### 上传

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `ICH_MAX_UPLOAD_MB` | `20` | |
| `ICH_UPLOAD_DIR` | `data/uploads` | |

示例（Windows PowerShell）：
```powershell
$env:ICH_DB_PASSWORD="yourpass"
$env:ICH_FEATURE_BACKEND="clip"
python run.py
```

---

## 11. 阈值与权重是怎么定的

这部分不是"参数拍脑袋"，而是有脚本、可复现。

### 11.1 分级阈值（0.92 / 0.80）

设 `s` = 与最相似公有母题的余弦相似度：

| 条件 | 建议级别 | 业务含义 |
|---|---|---|
| `s ≥ 0.92` | `public` 公有母题 | 视觉特征与传统纹样高度重合，属公有领域，传承人不能主张独占，只能登记"使用"事实 |
| `0.80 ≤ s < 0.92` | `derived` 改编衍生 | 存在明显借鉴但不构成整体复制。传承人对改编部分享演绎著作权，但须标注母题血缘，且不得限制他人使用原始母题 |
| `s < 0.80` | `original` 原创作品 | 与现存公有母题无实质重合 |
| 与私有资产 `s ≥ 0.95` | `derived` + `flagged_as='duplicate'` | 疑似抄袭他人作品，不参与常规分级，直接交由专家人工核查 |

**置信度** = 阈值余量 × 候选分离度，压缩到 `[0.05, 0.99]`：
- 离阈值越远越自信（`margin`）
- top1 与 top2 差距越大越自信（`separation`）
- 两者相乘，避免"接近阈值但 top2 很近"这种双重不确定仍然给出高置信度

### 11.2 特征段权重（0.51 / 0.23 / 0.26）

`builtin` 后端把三段特征加权拼接。权重用 `tests/tune_weights.py` 网格搜索标定：

```bash
python -m tests.tune_weights        # 两阶段搜索，约 3 秒
python -m tests.tune_weights --apply  # 额外输出可直接粘贴的代码
```

**两条硬约束（互相拉扯）**：

1. **翻拍稳定性**：同一作品的 9 种翻拍变体，与原始图相似度都要 ≥ 0.90
   —— 不达标则同一作品被误判成"另一个纹样"，比漏判严重得多
2. **改编可区分性**：改编样例与母题相似度必须 < 0.92
   —— 超标则真实改编作品被判"母题雷同"，剥夺传承人权益

先判定可行性，再在可行解内部按"旋转鲁棒性 + 各母题均衡"排序。评分公式的演进过程也写在脚本注释里（早期版本不封顶导致越线解排第一，白纸黑字记录了修正）。

**关键调参点：为什么纹理权重不能太低？**
回纹是"高对比度几何网格"，辨识度主要靠纹理能量。纹理权重压到 0.15 时，回纹在"对比度 +10%"这一项上相似度仅 0.878，跌破 0.90 线。这是三个母题里最脆弱的一个，它决定了纹理权重的下限。

**性能优化细节**：脚本利用一个数学事实做加速 —— 三段分别归一化后加权拼接，L2 范数恒为 `sqrt(ws²+wc²+wt²)`，所以余弦相似度只取决于三段各自的内积。于是每张图只需提取**一次**，240 组权重退化为标量乘加，整体从"超过 10 分钟被中断"降到 3 秒。

### 11.3 想换阈值怎么办

阈值是经验值，**必须在真实数据上标定**（用人工标注集画 ROC/PR 曲线找最佳切点）。换阈值只改环境变量，不用改代码：

```bash
export ICH_SIM_PUBLIC=0.94
export ICH_SIM_DERIVED=0.82
```

换完重跑 `python -m tests.test_feature_engine` 确认样例仍判对。

---

## 12. 测试与验证

十二个套件，**十一个不需要数据库和网络**，`pip install -r requirements.txt` 后即可跑；
`check_pg` 需要真实 PostgreSQL（便携库用 `python -m scripts.pg_run --reset` 起）。

### 一条命令跑完（推荐）

```bash
python tests/run_all.py                # 纯代码 + 浏览器，433 项，约 73 秒
python tests/run_all.py --group pure   # 只跑不用浏览器的，274 项，约 10 秒
python tests/run_all.py --group db     # 真库验收 46 项（45 通过 / 1 缺口，需库已启动）
python tests/run_all.py --all          # 三组全跑：13 套 / 568 项 / 1 缺口
python tests/run_all.py -v             # 附加每套的完整输出
```

汇总表会逐套报「通过 / 失败 / 缺口」，失败时自动打出该套件尾部。
**缺口不会被算成通过**——`check_pg` 那 1 项 GAP（HNSW 未实测）会单独标出来。

### 逐套命令

```bash
python -m tests.test_sql_structure     # SQL 静态结构校验
python -m tests.test_compat_vector     # 39 项兼容层验证（cosine 公式 / 归一化 / 脚本结构）
python -m tests.test_invariants        # 88 项源码级不变量（含种子数据 15 项、视图列同步 2 项）
python -m tests.test_classifier        # 24 项分级器
python -m tests.test_feature_engine    # 17 项特征引擎
python -m tests.tune_weights           # 权重标定 + 与生产代码一致性校验
python -m tests.run_e2e_inline         # 52 项端到端（进程内起服务，跑完整业务链路）
python -m tests.shoot_ui               # 49 项 UI 断言（三角色逐页渲染 + 截图，需 Playwright）
python -m tests.check_demo_page        # 52 项纯界面演示页验收（file:// 直开，含断网与重置）
python -m tests.check_demo_v2          # 58 项互动版验收（拖动登录、终审、阈值联动）
python -m tests.check_pg               # 46 项真实 PostgreSQL 验收（45 通过 / 1 缺口）
```

| 套件 | 项数 | 干什么 |
|---|---|---|
| `test_sql_structure.py` | 全部通过 | 正确切分 `$$` 函数体 / 字符串 / 注释 / psql 元命令，校验 8 表 3 视图 3 角色齐全、外键目标存在、触发器函数存在、视图依赖表存在、12 项关键约束/索引存在、传承人列级授权不含 `right_level`、传承人对 `audit_status` 有写权限（故必须有角色闸门） |
| `test_rules_sync.py` | **65** | 规则单一真相：`sim/rules.py` 与 SQL 漂移检测（含来源指纹）、24 条 CHECK/4 唯一/12 外键/9 触发器全覆盖且无凭空出现的约束、触发器必须被显式分类（含 `actor_gate`：角色闸门的受管角色/终态/事务标记必须被抽全）、三值逻辑（UNKNOWN≠失败）、成对约束与边界值、三类触发器判定（拒绝/门禁/守卫） |
| `test_compat_vector.py` | **39** | 验证降级模式的 `<=>` 与 pgvector 的 cosine 距离**数值一致**（含相同/相反/正交/零向量边界）、归一化后「内积 = 1-距离」这一检索正确性前提、`\if`/`\endif` 配对、无未受保护的 `::vector` 强转 |
| `test_invariants.py` | **88** | AI 不写 `right_level`、终审单事务、GRANT 列级权限、追加式日志、触发器覆盖、结构完整性、API 鉴权、向量一致性、前端用户可控文本全部经 `esc()` 转义、演示模式与真实库返回列集合一致、**母题血缘被回写**、**溯源节点序号连续**、**种子 AI 字段必须是真实算法输出且相似度 ≠ 置信度**、**视图列与 Pydantic 模型同步** |
| `test_classifier.py` | **24** | 三档判定、阈值等号语义、抄袭嫌疑、退化输入、置信度单调性、中文标签 |
| `test_feature_engine.py` | **17** | 母题互分离度、类内稳定性、端到端分级、鲁棒性（全黑/全白/8×8/极端长宽比/透明 PNG 均 512 维无 NaN） |
| `e2e_demo.py` | **52** | 15 个场景：三角色登录、越权拦截、上传→AI 建议、**AI 未写 `right_level`**、待审列表、专家推翻 AI（`is_override`）、重复终审 409、未终审拒绝授权、独占授权唯一性、溯源四类节点与链式校验、台账汇总、母题库结构与关联、账号作品分布、**授权费合计 > 0**、文件查重幂等 |
| `shoot_ui.py` | **49** | 进程内起 uvicorn + Playwright 驱动真实浏览器，三种角色逐个页签断言：无 `undefined`/`NaN`/`[object Object]`、console 零报错、关键数据行数与种子一致，并逐屏截图 |
| `check_demo_page.py` | **52** | 单文件演示页验收：`file://` 协议直开，7 章节渲染、身份切换后页签随角色变化、越权页签不可见、母题筛选、作品详情含溯源与专家意见、AI 分析显示真实相似度、终审推翻 AI 的标记、台账授权费 ¥131,600、重置恢复初始状态、五页签逐页无异常文本、console 零报错 |
| `check_demo_v2.py` | **58** | 互动版验收：拖动滑块验证（拖到一半不通过、拖到底才通过）、错误密码拦截、登录后角色可见性（传承人 6 个入口 / 专家 9 个）、筛选与多选批量、详情抽屉、终审 4 档位与未填意见拦截、推翻 AI 后审计日志留痕、上传分析含阈值推算、溯源图谱节点数、默认页下拉非空、关闭置信度后表格随之变化、阈值拖动联动、重置恢复 |
| `check_pg.py` | **46**（45 通过 / 1 缺口） | 真实 PostgreSQL 验收：8 表 3 视图 3 角色齐全、9 个触发器、枚举取值域、CHECK/UNIQUE/外键、事务回滚、`FOR UPDATE` 行锁（第二会话 55P03）、降级向量层 cosine 语义、**未终审与已驳回不得授权**、**审计日志 actor 必须是业务角色而非函数属主**、**`license_record` 的 INSERT 权限配置正确**、**传承人直连库改 `approved` 被触发器拒绝（42501，且错误来自数据库而非应用层）**、**专家无终审标记改终态被拒 / 带标记放行 / 已终审不可回退**、**监管改状态被拒**。唯一缺口是 HNSW（便携包无 pgvector） |

| `test_rules_parity.py` | **79** | 规则对拍：同一组边界样本分别喂真实数据库与 `sim/verdict`，比「接受/拒绝」是否一致。62 组规则样本（三档取值、四种授权类型、状态机非法跃迁、未终审即授权、自己授权给自己、NUMERIC 取整边界）判定 **100% 一致**；另 11 组权限边界（含传承人自封已确权、专家直改终态、专家带终审标记放行）、6 组唯一性与外键按设计分开核对；上一轮那 2 条「两侧都不拦」的已知缺口已被数据库闸门关闭，探针升级为断言，缺口位留空，`--self-check` 仍会故意让步以证明对拍真的会红 |

合计 **568 项**（285 项纯代码与业务检查 + 49 项真实浏览器断言 + 52 项纯界面版验收 + 58 项互动版验收 + 45 项真库验收 + 79 项规则对拍），另有 1 项如实的缺口（HNSW 未实测），当前全部通过。规则对拍里的「已知缺口」一栏本轮清零：原先那 2 条自我定级路径已被 `trg_asset_status_guard` 关闭，探针升级成了断言。

交付物都是脚本生成的，改了数据或代码后按需重跑：

```bash
python scripts/build_docx.py           # 项目交付说明书（output/01-交付物/）
python scripts/build_demo_page.py      # 纯界面演示页（加 --with-doc 出双栏版）
python scripts/build_demo_v2.py        # 互动版演示页
python scripts/build_coursework.py     # 课程作业两件套（output/04-课程作业/）
python scripts/preview_docx.py 某.docx  # 把 docx 转 HTML 预览，自查排版用
```

### 12.1 不启数据库也能验证的 AI 链路

```bash
python -m app.cli demo-check
```

直接用 `data/samples` 的样例图跑「提取特征 → 相似比对 → 分级建议」，打印判定依据并校验是否符合预期。

**实测结果**（`builtin` 后端）：

| 指标 | 数值 | 判定线 |
|---|---|---|
| 母题互分离度（最大） | 0.3474 | < 0.60 ✓ |
| 类内相似度均值 | 0.9642 | ≥ 0.95 ✓ |
| 类内最低 vs 类间最高 | 0.7610 vs 0.4086 | 类内 > 类间 ✓ |
| 改编样例 | 0.9182 → `derived` | 应判 derived ✓ |
| 原创样例 | 0.6036 → `original` | 应判 original ✓ |

### 12.2 端到端实测输出（演示模式，节选）

```
5. ★ 核心不变量：AI 只写建议，没碰 right_level
  ✓ asset.right_level 仍是初始值 public（AI 未写入）  right_level=public
  ✓ audit_status 变为 pending  status=pending
  ✓ ai_suggest_level 记录了建议  ai_suggest_level=derived

7. 专家终审：推翻 AI 建议
  ✓ 权属已落库  derived → original
  ✓ 标记为专家推翻  is_override=True
  ✓ 触发器留痕返回日志 ID  log_ids=[3]

10. 溯源链路
  ✓ 链路完整性校验通过  issues=[]
  节点数=6 分布={'root': 1, 'system_log': 3, 'audit': 1, 'license': 1}
```

---

## 13. 已知限制

我们把这些写清楚，而不是藏在角落。

### 13.1 本交付包未在真实 PostgreSQL 上执行验证

**这是最重要的一条，请认真看。**

开发环境的系统安全策略禁止执行 `initdb`，也无法安装 C 编译器（因此装不了 pgvector），
所以**本机无法创建 PostgreSQL 实例**，三个建库脚本没有在真实数据库上跑过一遍。

替代验证措施（全部已执行并通过）：

| 措施 | 覆盖范围 |
|---|---|
| SQL 静态结构校验 | 解析 `$$` 块 / 字符串 / 注释 / psql 元命令，校验 8 表 3 视图 3 角色、外键目标、触发器函数、关键约束/索引 |
| 88 项源码级不变量 | AI 不写 `right_level`、终审单事务、列级 GRANT、追加式日志、前端 XSS 转义、种子 AI 字段真实性、视图列与 Pydantic 同步 |
| 39 项兼容层验证 | `<=>` 与 pgvector cosine 距离**数值一致**（含 4 类边界）、归一化后内积=1-距离、`\if` 配对、无裸 `::vector` |
| 52 项端到端 | 进程内起服务，跑完整 15 个业务场景（**接口与业务逻辑是真跑过的**） |
| 49 项浏览器 UI 断言 | 四角色 × 5 页签逐屏渲染 + 数值级断言（作品数、母题数、授权费合计、无 `undefined`） |
| 52 项纯界面版验收 | `file://` 直开、左栏已移除且演示区占满整宽、身份切换、越权页签、AI 分析、终审推翻、台账金额、重置、断网零请求 |
| 58 项互动版验收 | 拖动滑块（半程不通过 / 全程通过）、错误密码、角色可见性、多选批量、终审 4 档位与意见校验、推翻留痕、阈值联动、设置开关、重置 |
| AI 模块全量实测 | 特征引擎 17 项 + 分级器 24 项，不依赖数据库 |

正因为无法实跑，我们改用"逐行读代码 + 写回归检查"的方式过关键路径，并因此**主动发现并修掉了十二个会导致业务失败或界面错误的设计缺陷**：

| # | 缺陷 | 后果 | 修复 |
|---|---|---|---|
| 1 | `audit_record` 上挂了 `trg_audit_record_immutable`（`BEFORE UPDATE OR DELETE` → `fn_forbid_modify`） | 该触发器对**任何** UPDATE 都抛异常，专家终审执行 `UPDATE audit_record SET state='approved'` 必然失败 —— **整条终审链路走不通** | 删掉该触发器；由 `trg_audit_record_no_delete`（禁删）+ `trg_audit_record_guard`（禁改已终审，放行状态迁移）分工实现 |
| 2 | `analyze_asset` 重复分析时，把旧 pending 流水 **UPDATE 成 `rejected` 并硬塞一个 `expert_id`** | 凭空产生一次"专家驳回"，污染 `is_override` 与专家工作量统计 | 改为不删不改：允许多条 pending 并存，查询侧（`v_asset_pending_audit` 的 `LATERAL ... LIMIT 1`）只取最新一条 |
| 3 | 触发器 `fn_motif_vector_normalize` 里 `unnest(NEW.base_vector::real[])` 与 `(... / v_norm)::vector` | `vector` 与 `real[]` 的转换依赖 pgvector 版本；在降级模式下（`NUMERIC[]`）**直接语法错误**，整个 `02` 脚本会中断 | 改用 `ich_vector_is_native()` 运行时分支：原生走 `::real[]`，降级直接 `unnest` 数组。**这条是被 `test_compat_vector.py` 抓出来的** |
| 4 | 前端统计卡片 `${sum.by_status.approved}` / `${sum.by_level.public}` 直接取值 | 后端 `GROUP BY` **只返回实际存在的分组**，没有已确权资产时 `approved` 键根本不存在，取出来是 `undefined` —— 界面首屏就显示「undefined 件」，真库模式同样会犯 | 两处统计渲染统一加 `nz()` 兜底成 0；新增 2 项不变量检查（正则扫描直接取值 + 校验 `nz()` 存在） |
| 5 | `demo.list_my_assets()` 只返回 8 列，而 `services.list_my_assets()` 的 SQL 查 9 列（少了 `version_no` / `updated_at`） | 演示模式「我的作品」的**版本列渲染成 `vundefined`**。这类"两套实现字段漂移"在演示模式看不出来，切到真库才暴露，属于典型的两头都测过、拼接处没测 | 演示实现补齐 9 列并注释"必须与真实实现逐列一致"；前端版本列加 `nz()` 兜底；新增 3 项不变量检查做**静态列集合比对**（`services.py` 的 SELECT 子句 vs `demo.py` 的字典键 vs `app.js` 的 `a.xxx` 引用） |
| 6 | `openLicense()` 无条件请求 `GET /api/users` | 该接口限 `expert`/`admin`，传承人打开自己的授权弹窗时浏览器 console 留一条 403 红字（功能不受影响，但显得不干净） | 按 `S.user.role` 判断，传承人跳过该请求（外部机构名称手填即可） |
| 7 | `v_asset_trace_chain` 的 `seq_no` 是按**事件类型写死的常量**（root=1/audit=2/license=3/system_log=4） | 一条链上必然有多个 `system_log`（登记、分析、终审各触发一次），前端时间轴显示成 **`1→4→4→2→4→3`** 的乱序。真实库与演示模式写法一致，**真库同样会犯** | 视图保持「事实投影」职责不变，由应用层（`services.trace_asset` / `demo.trace_asset`）按 `event_at` 重编 1..N；同毫秒节点保留视图原序以保证「业务事件排在其触发的审计快照之前」 |
| 8 | 分析流程的 `UPDATE asset` 里**没有 `motif_id`** | `01_init.sql:114` 建了 `motif_id`（母题血缘）+ 外键 `fk_asset_motif`，`v_asset_ledger` 也 JOIN 它输出 `motif_code`/`motif_name`，但生产代码从未写入 —— 台账「母题」列恒为 `—`，衍生作品统计恒为 0 | 判为 `public`/`derived` 时写入 Top-1 母题的 id，判为 `original` 时保持 `NULL`；两侧实现同步修复 |
| 9 | 种子数据里的 `similarity_json` 用了 `ai_confidence` 冒充相似度 | 置信度 ≈ 0.42、真实余弦相似度 ≈ 0.81，母题关联页显示 0.4190 而非 0.8059 —— **数字是错的，但看起来像对的**。这比报错更危险：演示时没人会怀疑一个格式正常的数字 | 种子补 `similarity` 字段（余弦），`_snapshot_for()` 只认 `similarity`；新增不变量「`similarity` 必须 ≠ `ai_confidence`」，防止再次混用 |
| 10 | 种子 AI 结论是**手填**的（8 件里有 2 件与真实算法输出不符，如作品 5 手填 `public`，实际算得 0.8783 → `derived`） | 演示时台账显示 `derived`，观众点「重新分析」AI 改判 `original` —— **整套演示的可信度归零** | 原则修正：种子不虚构 AI 结论，只描述真实算法会得出什么。写 `scripts/check_seed.py` 从算法回写全部 `ai_suggest_level` / `ai_confidence` / `similarity`，并加 15 项种子不变量守住 |
| 11 | 视图加了 `top1_similarity`，但只改了 SQL、**没改 `LedgerRow`** | Pydantic v2 默认丢弃未声明字段，接口静默返回 `null`，前端相似度列全是 `—`。**改动"生效"了，字段却没了**，且无任何报错 | `schemas.LedgerRow` 补 `top1_similarity: float \| None`，并加 2 项不变量做「视图顶层 SELECT 列集合 vs Pydantic 字段集合」静态比对（只解析首个顶层 SELECT，按括号深度切分，避免把 LATERAL 内部别名当视图列） |
| 12 | 重构 `demo.py` 时把授权与审批日志的写入代码**误缩进到 `_snapshot_for()` 的 `return` 之后**，成了死代码 | `license_record` 表恒为空 → 台账「授权费合计」恒为 ¥0，且 5 笔授权在界面上一笔都看不到。**这是本轮唯一的真实业务缺陷**，由 e2e 的一句 `license_record > 0` 断言抓出 | 拆出 `_seed_licenses()` / `_seed_approve_log()` 两个独立函数，在 `_load_assets()` 中显式调用；修复后授权费合计 ¥131,600 / 13 条日志 |

第 1、2、4、5、6、7、8 条在 `tests/test_invariants.py` 里加了**负向回归检查**（把坏写法放回去，检查必须失败）；
第三条在 `tests/test_compat_vector.py` 里检查"是否存在未受模式保护的 `::vector` 强转"。
我们实际做了这个验证：注入坏代码后检查报 `✗` 并指出具体行号，恢复后全部通过。

> **第 9–12 条是"用种子数据把五个页面逐个点一遍"时才暴露的** —— 它们有一个共同特征：> **都不报错**。数字格式正常、字段返回正常、页面渲染正常，错的是"值"和"有没有写进去"。
> 第 12 条是最典型的一个：一次纯重构（拆函数）把 5 笔授权变成了死代码，接口 200、页面正常、
> console 干净，只有一句刻意写下的数值断言（授权费合计必须 > 0）才抓到它。
> 所以本轮把"UI 截图"升级成了 `tests/shoot_ui.py` 的 **49 项数值断言** —— 不只是拍张图好看，
> 而是断言"这个格子里必须是 ¥131,600 这个数"，并在三种角色下各跑一遍。

> **第 7、8 条是"用现有样本实跑一遍完整流程"时才暴露的** —— 静态审查和单元测试都没抓到：
> 前者要看到界面才觉得"序号怪"，后者要看到台账那一列空着才起疑。
> 这正说明「能跑通」和「跑对了」是两件事。运行记录见 `docs/verify/完整流程运行报告.md`。


> 第 5 条顺带暴露了一个设计问题：`nz()` 原先在 `loadPending()` 和 `renderLedgerStats()` 里各写一份局部定义，
> 这种复制粘贴在加 fallback 参数时会漏改其中一处。已提升为**模块级唯一实现**，并由测试守住"不得再出现局部定义"。

### 13.1.1 前端的"能跑"降到什么程度

`docs/01-工程说明书/演示前端与示例数据说明.md` 是种子数据版本的实测记录，这里是结论：

- 启动日志 **4 条 WARNING / 0 条 ERROR**（3 条是 AI 后端降级，1 条是演示模式提示，均属设计内）
- 浏览器端**四种角色**（`heritor01` / `heritor02` / `expert01` / `admin01`）各开独立上下文、轮转各自可见的 **5 个页签**
  （我的作品 / 待审工作台 / 权属台账 / 母题库 / 溯源链）：
  **console 错误 0、page 错误 0、请求失败 0、页面无 `undefined`/`NaN`/`[object Object]`**
- `tests/shoot_ui.py` 的 49 项断言里，**数值类断言占多数**，不是只截图：
  作品 8 件、母题 3 个、待审条数、台账授权费合计 ¥131,600、相似度列无 `—` 等，逐项比对种子真值
- `tests/check_demo_page.py` 的 **52 项**针对交付用单文件演示页（`file://` 协议直开，无后端、无网络）：
  7 章节渲染、身份切换后页签随角色增减、越权页签不出现在 DOM 里、母题筛选、作品详情含溯源链与专家意见、
  AI 分析面板显示真实余弦相似度而非置信度、终审推翻 AI 建议后打上「已推翻」标记、
  台账授权费合计 ¥131,600、重置按钮恢复初始状态、**断网后零新增请求**、五页签逐页无 `undefined`/`NaN`
- API 全链路 52 项全部 PASS，其中"传承人越权访问待审列表 → 403"、"重复终审 → 409"、
  "未终审即授权 → 409"三条是**反向验证**，证明约束确实在生效


> 这类缺陷正是"不实跑"最容易漏掉的东西。所以**请务必在真实数据库上跑一遍 §5 的演示流程**，
> 尤其是终审那一步 —— 它是唯一同时触碰触发器、事务、列级权限的地方。

**请在首次部署时按 §1.0 逐条执行，并关注控制台输出。** 仍可能出问题的位置（已逐一定位）：

| 位置 | 风险 | 应对 |
|---|---|---|
| `02` HNSW 索引 `WITH (m = 16, ef_construction = 64)` | HNSW 需 pgvector ≥ 0.5.0 | 老版本降级到 `ivfflat`（`WITH (lists = 100)`），或升级扩展；脚本已判断 pgvector 是否存在，无则跳过 |
| `03` `vector_dims(base_vector)` | 0.7.0 起才有（降级模式下由兼容层提供等价函数） | 老版本原生模式改用 `array_length(base_vector::real[], 1)` |
| `01` 重跑顺序 | `DROP TABLE ... CASCADE` 会删掉 `02` 建的视图与触发器 | 重跑 `01` 后**必须**重跑 `02` |
| `01` 里 `\i 00_compat_vector.sql` | 相对路径依赖"从工程根目录执行 psql" | 若在别处执行，改为绝对路径或先 `cd` 到工程根目录 |

另外，若遇到 `ERROR: could not open extension control file ".../vector.control"`，说明 pgvector 服务端文件没装 ——
不过**这不影响使用**：`00_compat_vector.sql` 会捕获该错误并切到降级模式，功能照常。
想用 HNSW 再按 §1.0 用 Docker 起库，或按 §2 装 pgvector。

### 13.1.2 降级模式的边界（务必清楚）

降级模式（无 pgvector）**不是**"打折扣跑通"，而是"换一种等价实现"：

- 检索结果是**精确** cosine 排序，比 HNSW 的近似结果更准
- 但复杂度 O(n)，且 `EXPLAIN` 看到的是 `Seq Scan` 而非 `Index Scan using ..._hnsw`
- 因此：**不能用降级模式的执行计划去证明"HNSW 索引生效了"**。这条证据只能在有 pgvector 的环境取得
- 同理，演示模式的红色横幅也写明了：列级 GRANT / 触发器 / CHECK / 外键 / MVCC 在内存存储下不生效

### 13.2 builtin 特征后端的局限

- **无语义理解**：不能做跨模态（文字↔图像）检索
- **对大幅旋转敏感**：回纹这类周期网格纹样旋转 4° 后相似度约 0.75。这是刻意接受的取舍 —— 见 `features.py` 注释：多角度取最大会让"同一作品换个角度拍"被误判成另一个纹样，是更严重的错误方向。需要时用 `extract_rotation_robust()`（5× 耗时）或换 CLIP
- **对透视/形变不鲁棒**：HOG 类特征的固有问题
- **阈值是经验值**：需要在真实非遗图像数据集上重新标定

### 13.3 工程层面的简化

| 项 | 现状 | 生产建议 |
|---|---|---|
| 图片存储 | 本地 `data/uploads/`，UUID 命名 | 换对象存储 + 预签名 URL |
| JWT 密钥 | 有开发默认值 | 必须用环境变量注入随机值 |
| CORS | `allow_origins=["*"]` | 限定 Origin 白名单 |
| 令牌存储 | `localStorage` | 有 XSS 风险；正式产品建议 `HttpOnly` cookie + CSRF token |
| 谱系环检测 | 应用层未做 | 加递归 CTE 环检测 |
| 审计日志 | 只增不归档 | 按时间分区 + 冷数据归档 |
| 限流 | 无 | 上传与登录接口需限流 |

---

## 14. 常见问题

### 14.0 关于「怎么真实运行」

**Q：我只想让它跑起来，最短路径是什么？**

```bash
docker compose up -d                          # 起带 pgvector 的库
pip install -r requirements.txt
python bootstrap.py --check                   # 它会告诉你还差什么
```

`bootstrap.py` 会逐层探测并给出**可直接复制的修复命令**，而不是一句"连接失败"：

```
2 / 4  探测 PostgreSQL
  目标：postgres@127.0.0.1:5432/ich_heritage
  ✗ 连不上 127.0.0.1:5432（ConnectionRefusedError）

    ! 这台机器上没有可用的 PostgreSQL。有两种走法：
     A. 装一个（推荐 Docker，最省事，自带 pgvector）：
        docker run -d --name ich-pg -p 5432:5432 \
          -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=ich_heritage \
          pgvector/pgvector:pg16
     B. 先看演示模式（内存存储，界面功能一样，但没有数据库约束验证）：
        python bootstrap.py --demo
```

**Q：装不上 pgvector，还能真实运行吗？**

能。`sql/00_compat_vector.sql` 会自动把向量层换成 `NUMERIC[]` + 自定义 `<=>` 运算符，
表、触发器、视图、列级 GRANT、CHECK/UNIQUE/外键**全部照常生效** —— 只有 HNSW 索引退化为精确扫描。
脚本会明确打印走了哪条路，不会静默切换。

**Q：为什么 `docker compose up` 之后还提示"还没初始化"？**

容器只提供"一个空的库"。建表要在容器 healthy 后手动执行 `sql/01~03`（顺序不能换）。
`bootstrap.py --check` 会检测到 `已建业务表 0/8` 并把三条 psql 命令原样打出来。

**Q：`initdb` 报"命令被安全策略拦截"**

说明你在一个禁止创建数据库实例的受限环境里（如某些沙箱/CI）。这不是项目问题。
在你的本机或服务器上正常执行即可；没有数据库时用 `python bootstrap.py --demo` 先看界面。

**Q：怎么确认自己现在跑的是真库还是演示模式？**

三种方式，任选：
1. 页面顶部有没有**红色横幅**（有横幅 = 演示模式）
2. `curl http://127.0.0.1:8000/api/meta` 看 `demo_mode` 字段是 `true` 还是 `false`
3. `curl http://127.0.0.1:8000/api/health` 看 `database` 字段是数据库地址还是 `演示模式（内存存储，非 PostgreSQL）`

### 14.1 其他常见问题

**Q：`python -m app.cli check` 报 "Connection refused"**
数据库没启动，或 `ICH_DB_*` 配置不对。先确认 `psql -d ich_heritage -c "select 1"` 能通。

**Q：`CREATE EXTENSION vector` 失败，提示 "could not open extension control file"**
pgvector 的服务端文件没装到 PostgreSQL 的 `share/extension/` 目录。
**这不影响使用**：兼容层会捕获该错误并切到降级模式。想用 HNSW 见 §1.0（Docker）或 §2（安装 pgvector）。

**Q：建库后 `v_asset_trace_chain` 报 "relation does not exist"**
`02` 脚本没执行，或者执行了 `01` 之后没重跑 `02`（`DROP TABLE ... CASCADE` 会连带删掉视图）。

**Q：AI 总是给出 `original` 建议**
多半是母题向量还是占位值。跑 `python -m app.cli backfill-motif-vectors`。`check` 的第 9 项会提示。

**Q：上传后待审列表里没有**
`audit_status` 必须是 `pending`，`v_asset_pending_audit` 还要求存在 `state='pending'` 的 `audit_record`。检查是否上传时没勾 `auto_analyze`、也没手动点分析。

**Q：专家终审报 403 "数据库层权限拒绝"**
说明数据库 GRANT 生效了，但当前账号没有 `ich_expert` 权限。确认登录用户 `role` 是 `expert`，且后端连接账号有 `SET ROLE` 权限。

**Q：日志里一直有 "特征后端 clip 不可用，继续降级"**
正常，这是 `auto` 模式的预期行为。想消除就装 `requirements-deep.txt`，或显式设 `ICH_FEATURE_BACKEND=builtin`。

**Q：Windows 下 `python run.py` 卡住不动**
首次启动需要初始化连接池，稍等几秒。若持续卡住，检查防火墙是否拦截了到 5432 的连接。

**Q：想换个端口 / 局域网演示**
`python run.py --port 9000 --host 0.0.0.0`，然后同事用 `http://你的内网IP:9000/` 访问。

---

## 交付说明

| 交付项 | 位置 |
|---|---|
| 完整代码 | `app/`（后端 8 模块 + AI 3 模块 + 前端 2 文件）、`run.py`、`tests/` 5 个脚本 |
| SQL 脚本 | `sql/01_init.sql`、`sql/02_index_trigger_view.sql`、`sql/03_seed.sql` |
| 初始化说明 | 本文 §3 |
| 启动命令 | 本文 §4 |
| 演示流程 | 本文 §5（含 UI 操作与等价 curl 两版） |
| 文件用途 | 本文 §6 |
| 数据库原理对照 | 本文 §8（范式/索引/触发器/视图/事务/权限 六张表） |

**核心业务规则的落地位置**：

| 规则 | 实现在哪 |
|---|---|
| AI 只出建议，绝不改 `right_level` | §7 四层机制：列级 GRANT + 88 项不变量测试 + 分级器无 DB 依赖 + 触发器留痕 |
| 终审由专家决定，事务原子完成 | `services.approve_asset()` 6 步单事务 + `FOR UPDATE` + `rowcount` 校验 |
| 溯源日志追加式，不更新不删除 | `trg_syslog_immutable` + `REVOKE UPDATE/DELETE/TRUNCATE` + 应用层不留 UPDATE 路径 |
| 分级标签取值 `public`/`derived`/`original` | `right_level_enum` 枚举类型 + CHECK 约束 |
| 数据一致性（要么同时成功要么回滚） | `open_tx()` 统一事务上下文；触发器与主事务同生共死 |
