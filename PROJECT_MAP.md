# PROJECT_MAP　全仓入口地图

> 一句话用法：**先找你要的那一行，照着「入口」一列敲命令。**
> 本文件只回答「怎么进、产物在哪、怎么验证」；深度说明在 `docs/README.md` 指向的各文档里。
>
> 更新日期：2026-11-27　适用状态：641 项测试（15 套）全绿，PostgreSQL 16.15 + pgvector 0.8.6 原生实例实测通过，规则表与库逐条对账，规则对拍 100% 一致
>
> 对象规模：**8 张业务表 / 4 视图 / 10 触发器 / 14 个显式索引**；按系统目录口径为
> **12 张表（8 业务 + 4 系统目录）/ 26 个索引（14 显式 + 8 主键 + 3 唯一约束 + 1 部分唯一索引）/ 11 个函数**。
> 三方差集见 [`docs/verify/SQL对象清点-20261127.md`](docs/verify/SQL对象清点-20261127.md)。

---

## 0. 三十秒认路

```
本仓库里有 9 条线，每条线一个入口。代码类目录名是英文（要被工具链解析），
文档与成品目录名是中文（要给人看）。这不是随意，是两条不同消费者的要求。

进系统      →  python bootstrap.py
要交的成品  →  output/01-交付物/
按课上交    →  output/04-课程作业/
想读文档    →  docs/README.md
想跑测试    →  python tests/run_all.py
要改数据库  →  sql/   +  python -m scripts.pg_run --reset
下一步做什么 →  docs/02-研究规划/研究方向精进路线图.md
```

---

## 1. 九条线总览

| # | 线 | 入口（一条命令 / 一个动作） | 主产物 | 验证方式 |
|---|---|---|---|---|
| 1 | 业务系统（Web 服务） | `python bootstrap.py` | 运行中的服务 + `app/` | `python -m app.cli demo-check` |
| 2 | 纯界面演示页 | 双击 `output/01-交付物/演示页面.html` | 单文件 HTML 2.3 MB | `python -m tests.check_demo_page` |
| 3 | 互动版演示页 | 双击 `output/01-交付物/演示页面_互动版.html` | 单文件 HTML 0.7 MB | `python -m tests.check_demo_v2` |
| 4 | 测试基线 | `python tests/run_all.py` | 641 项断言（15 套，含真库） | 自身就是验证手段 |
| 5 | 数据库 | `python -m scripts.pg_run --reset` | 8 表 4 视图 10 触发器 14 索引 | `python -m tests.check_pg` |
| 6 | 交付说明书 | `python scripts/build_docx.py` | `output/01-交付物/*.docx` | `python scripts/preview_docx.py` |
| 7 | 课程作业两件套 | `python scripts/build_coursework.py` | `output/04-课程作业/` | 产物自检 + 提交说明 |
| 8 | 研究化改造（进行中） | 读路线图 | `sim/`（规则层已落地） | `python -m tests.test_rules_sync` |
| 9 | 规则单一真相 | `python -m scripts.gen_rules` | `sim/rules.py`（自动生成） | `--check` + `--verify-db` + `tests.test_rules_parity` |

**依赖顺序**：1 依赖 5；6、7 依赖 1 的代码与 `data/`；2、3 依赖 `data/seed_demo.json`；8 依赖 5 已跑通。
2、3、4、6、7 彼此独立，可单独跑。

---

## 2. 逐条线：入口在哪

### 线 1　业务系统（FastAPI 服务）

| 项 | 内容 |
|---|---|
| **推荐入口** | `python bootstrap.py`　先自检依赖/数据库/AI 后端，再决定用真库还是演示模式 |
| 严格模式 | `python bootstrap.py --strict`　连不上库直接失败，不降级（答辩演示用这个） |
| 只自检 | `python bootstrap.py --check`　不启动服务，只报环境 |
| **用便携库跑** | `ICH_DB_PORT=55432 ICH_DB_NAME=heritage python bootstrap.py --strict`　见下方说明 |
| 轻量入口 | `python run.py`　不做降级判定，只做连通性自检后启动 |
| 裸启动 | `python -m uvicorn app.main:app --reload --port 8000` |
| 打开 | 界面 http://127.0.0.1:8000/　接口文档 http://127.0.0.1:8000/docs |
| 登录 | `heritor01` / `expert01` / `admin01`，密码统一 `Passw0rd!` |

> **为什么便携库要单独覆盖参数**：`.env` 默认写的是标准库路径（`5432` / `ich_heritage`），
> 而 `.pgpkg/` 里的便携库跑在 `55432`、库名是 `heritage`（`pg_run.py` 与 `check_pg.py` 的默认 DSN）。
> 两套是并行的，不覆盖就会去连 5432 然后报「数据库不可用」。
> 实测：覆盖后自检输出「✓ 认证通过 / ✓ 8 张业务表齐全」，可直接启动。
> PowerShell 写法：`$env:ICH_DB_PORT=55432; $env:ICH_DB_NAME=heritage; python bootstrap.py --strict`

代码分层（`app/`，14 个模块）：

| 层 | 文件 | 职责 |
|---|---|---|
| 入口 | `main.py` | FastAPI 实例、静态挂载、`/data` 图片路由 |
| 接口 | `api.py` | 7 个业务 API + 11 个辅助接口 |
| 业务 | `services.py` | 判定、终审事务、授权、溯源。**唯一允许写 `right_level` 的地方** |
| 数据 | `db.py` / `schemas.py` | 连接池、事务上下文；Pydantic 出入参 |
| 鉴权 | `auth.py` | 三角色会话、越权拦截 |
| 演示 | `demo.py` | 无库时的内存实现，接口形状与真库一致 |
| 配置 | `config.py` | `.env` 读取，唯一配置源 |
| AI | `ai/features.py` `ai/similarity.py` `ai/classifier.py` | 512 维特征 → 余弦检索 → 三档建议 |
| 前端 | `static/index.html` `static/app.js` | 单页应用，四页签 |
| 运维 | `cli.py` / `motif_images.py` | 命令行工具；母题图程序生成 |

### 线 2　纯界面演示页（投影用）

| 项 | 内容 |
|---|---|
| **入口** | 双击 `output/01-交付物/演示页面.html` |
| 零依赖 | 单文件 2.3 MB，CSS/JS/图片/数据全部内联，断网可开 |
| 内容 | 5 个页签，无登录，顶栏直接切身份；无左侧说明栏 |
| 生成 | `python scripts/build_demo_page.py`　加 `--with-doc` 出带左栏的双栏版 |
| 源 | `scripts/build_demo_page.py` 单文件模板（约 1500 行，数据从 `docs/verify/run_result.json` 读取） |
| 验收 | `python -m tests.check_demo_page`　52 项，含断网与重置 |

### 线 3　互动版演示页（让人自己玩）

| 项 | 内容 |
|---|---|
| **入口** | 双击 `output/01-交付物/演示页面_互动版.html` |
| 内容 | 先拖动滑块登录 → 9 个页面；4 个账号可选 |
| 生成 | `python scripts/build_demo_v2.py` |
| 源 | `scripts/v2_assets/` 6 个文件：`style.css` / `login.html` / `app.html` / `app.js` / `pages1.js` / `pages2.js` |
| 验收 | `python -m tests.check_demo_v2`　58 项 |

两份演示页的区别见 `docs/01-工程说明书/交付物说明.md`。

### 线 4　测试基线（641 项 / 15 套）

| 项 | 内容 |
|---|---|
| **统一入口** | `python tests/run_all.py`　跑纯代码 + 浏览器两组合计 444 项 |
| 只跑不用浏览器的 | `python tests/run_all.py --group pure`　285 项，约 13 秒 |
| 带数据库的 | `python tests/run_all.py --group db`　额外 124 项（45 真库验收 + 79 规则对拍，另有 1 项缺口） |
| 单套跑 | `python -m tests.test_xxx`，套件清单见 README §12 |

分组与项数：

| 组 | 套件 | 项数 | 需要什么 |
|---|---|---|---|
| pure | `test_sql_structure` | 全部通过 | 无 |
| pure | `test_rules_sync` | 65 | 无 |
| pure | `test_compat_vector` | 39 | 无 |
| pure | `test_invariants` | 88 | 无 |
| pure | `test_classifier` | 24 | 无 |
| pure | `test_feature_engine` | 17 | 无 |
| pure | `tune_weights` | 权重标定 | 无 |
| pure | `run_e2e_inline` | 52 | 无（进程内起服务） |
| browser | `shoot_ui` | 49 | Playwright |
| browser | `check_demo_page` | 52 | Playwright |
| browser | `check_demo_v2` | 58 | Playwright |
| db | `check_pg` | 46（45 通过 / 1 缺口） | 真实 PostgreSQL |
| db | `test_rules_parity` | 79 | 真实 PostgreSQL |

### 线 5　数据库

| 项 | 内容 |
|---|---|
| **SQL 源** | `sql/00_compat_vector.sql`（降级向量层）→ `01_init.sql`（8 表 / 约束 / GRANT）→ `02_index_trigger_view.sql`（索引 / 10 触发器 / 4 视图）→ `03_seed.sql`（种子） |
| 便携库重建 | `python -m scripts.pg_run --reset`　自动起 PG 16.2（端口 55432）并顺序执行，120 条语句 / 约 1.6 秒 |
| 只看不跑 | `python -m scripts.pg_run --dry-run` |
| 标准库（Docker） | `docker compose up -d` 后 `python scripts/init_db.py` |
| 灌种子 | `python scripts/init_seed.py`（`--dry-run` 先看） |
| 种子护栏 | `python scripts/check_seed.py`　校验 AI 字段是真实算法输出而非手编 |
| 验收 | `python -m tests.check_pg`　46 项（45 通过 / 1 项 HNSW 缺口） |

`scripts/pg_run.py` 是**不需要 psql 的 SQL 执行器**，自己实现了 `\i` / `\if` / `\gset` / `\set` 与变量替换。
便携库在 `.pgpkg/`（22.8 MB 二进制），只监听 127.0.0.1，端口 **55432**、库名 **heritage**。

两条路径别搞混：

| | 标准库（Docker） | 便携库（.pgpkg） |
|---|---|---|
| 怎么起 | `docker compose up -d` | `python -m scripts.pg_run --reset`（自带 `pg_ctl` 起停） |
| 端口 / 库名 | `5432` / `ich_heritage` | `55432` / `heritage` |
| 配置来源 | `.env` 默认值 | 脚本内默认 DSN，**不读 `.env`** |
| 谁用它 | `bootstrap.py` / `run.py` / Web 服务 | `tests/check_pg.py` |
| 向量能力 | pgvector + HNSW | 降级 `NUMERIC[]` 精确扫描（功能等价，性能不承诺） |

### 线 6　交付说明书（Word）

| 项 | 内容 |
|---|---|
| **成品** | `output/01-交付物/非遗数字资产分级确权与溯源系统_项目交付说明书.docx`（1.0 MB，7 章 28 节 32 表 5 图） |
| 生成 | `python scripts/build_docx.py`　数字全部现场从 `data/seed_demo.json` 与 `docs/verify/run_result.json` 读，不硬编码 |
| 排版自查 | `python scripts/preview_docx.py output/01-交付物/xxx.docx`　转 HTML 看效果 |
| Markdown 源 | `docs/01-工程说明书/` 三份 |
| 选题报告 Word | `output/01-交付物/非遗数字资产分级确权与溯源AI工具-选题报告.docx`，源在 `docs/03-选题材料/选题报告.md` |

### 线 7　课程作业两件套

| 项 | 内容 |
|---|---|
| **交付目录** | `output/04-课程作业/`　整个目录可直接压缩上交 |
| 一页纸说明 | `output/04-课程作业/提交说明.md`　30 秒上手 + 目录对照 + 两个边界 |
| 第一部分（代码） | `01-代码/课程作业_第一部分_代码.docx` + `代码文件清单.csv` |
| 第二部分（数据） | `02-数据/课程作业_第二部分_数据.docx` + `数据字典.csv`（134 字段）+ `csv/`（5 张展开表）+ `images/`（11 张） |
| 可运行工程 | `课程作业_代码包.zip`（0.93 MB，80 文件）与 `课程作业_代码包/code/` 同源 |
| 生成 | `python scripts/build_coursework.py`　所有数字现场扫描，改数据重跑即同步 |

### 线 8　研究化改造（进行中）

| 项 | 内容 |
|---|---|
| **当前唯一真相源** | `docs/02-研究规划/研究方向精进路线图.md` |
| 已完成 | P0-2 真实 PostgreSQL 跑通；P0-3 步骤 2 规则表自动生成与对账 |
| 下一个 | P0-3 步骤 3：`tests/test_rules_parity.py`（≥40 组样本，两边判定 100% 一致） |
| 将要新增 | `sim/env`、`sim/runner`、`sim/metrics`、`policies/`、`scenarios/`（YAML） |
| 现状 | `sim/` 已有 ruleast / rules / verdict 三个文件，其余不要提前建空壳 |

### 线 9　规则单一真相（P0-3 步骤 2 已落地）

同一套业务规则只允许有一份源头：`sql/`。`sim/rules.py` 由生成器从 SQL 抽取，不手抄。

| 项 | 内容 |
|---|---|
| **生成** | `python -m scripts.gen_rules`　产出 `sim/rules.py` |
| 漂移检测 | `python -m scripts.gen_rules --check`　SQL 改了没重新生成 → 退出码 1 |
| 与库对账 | `python -m scripts.gen_rules --verify-db --dsn "host=127.0.0.1 port=55432 user=postgres dbname=heritage"` |
| 判定层 | `sim/verdict.py`　`check_row()` 行内规则、`check_operation()` 含触发器语义 |
| 验证 | `python -m tests.test_rules_sync`　66 项（离线）｜`python -m tests.test_rules_parity`　79 项（真库对拍） |
| 机制自证 | `python scripts/probe_drift.py`　故意改 SQL，确认检测真的报错（自动还原） |
| 覆盖 | 24 CHECK / 4 UNIQUE（含 1 个部分唯一索引）/ 12 外键 / 53 NOT NULL / 10 触发器 |

**为什么不手抄**：手抄的规则表与 SQL 迟早偏离，而偏离方向通常是「SQL 严、Python 松」，
仿真会跑得好好的而真实系统拒绝写入 —— 这类错误在论文里表现为「仿真结论不可复现」。

---

## 3. 目录结构与命名规则

### 3.1 现有结构（本次整理后）

```
.
├── PROJECT_MAP.md            ★ 本文件：入口地图
├── README.md                   技术全文档（14 章，含 API / 配置 / 限制）
├── bootstrap.py              ★ 推荐启动入口
├── run.py                      轻量启动入口
├── requirements.txt            核心依赖（不含 torch）
├── requirements-deep.txt       可选深度模型依赖
├── docker-compose.yml          带 pgvector 的标准库
├── .env / .env.example         配置（.env 不提交）
│
├── app/                      ← 线 1 业务系统（14 模块 + ai/ 子包 + static/）
├── sql/                      ← 线 5 数据库（4 个脚本，按执行顺序编号）
├── scripts/                  ← 构建与运维工具（12 个脚本 + v2_assets/）
├── tests/                    ← 线 4 测试（11 个套件 + run_all.py 统一入口）
├── data/                       运行时数据：seed_demo.json / motifs / samples / uploads
│
├── docs/                     ← 人读的说明与证据（中文目录，见 docs/README.md）
│   ├── README.md               文档索引
│   ├── 01-工程说明书/           给评审与使用者（3 份）
│   ├── 02-研究规划/             给未来的自己（路线图）
│   ├── 03-选题材料/             选题报告 Markdown 源
│   ├── 04-归档/                 已作废文档（标注作废原因，不删除）
│   └── verify/                 证据：实跑报告 / 原始 JSON / PG 重建日志
│
└── output/                   ← 可交付成品（中文编号，见 output/产物清单.md）
    ├── 产物清单.md              output 层导航
    ├── 01-交付物/               要交的 4 份
    ├── 02-截图证据/             6 个子目录，按产生方式分
    ├── 03-过程材料/             不交，留档
    └── 04-课程作业/             按课程要求重组的代码 + 数据
```

### 3.2 命名规则（四条，都有理由）

| 规则 | 理由 |
|---|---|
| **代码目录英文、文档目录中文** | 代码要被 import 与工具链解析，中文名在 Windows 某些场景会出编码问题；文档要给人看，中文比 `deliverables` 好懂 |
| **编号前缀 = 阅读/执行顺序** | `01-交付物` → `04-课程作业` 是越往后越专门；`00_compat` → `03_seed` 是必须按序执行，乱序会报错 |
| **不带日期 = 当前有效** | 带日期的是快照（如归档文档正文里的「生成时间」），不带日期的是活文档，永远代表现在 |
| **作废不删除，移到 `04-归档/` 并在开头写作废原因** | 判断轨迹比目录干净更值钱；但必须标注，否则下一个人会当成有效结论引用 |

---

## 4. 哪些目录**不能**随意移动（重要）

本次整理**只动了文档层与产物补齐**，代码与数据目录一律原位。原因不是懒，是这些路径已被固化成契约：

| 路径 | 被谁钉住 | 动了会怎样 |
|---|---|---|
| `app/` | `run.py` 的 `reload_dirs=[ROOT/"app"]`、uvicorn 的 `app.main:app`、README 全部命令 | 服务起不来 |
| `sql/` `tests/` `scripts/` `sim/` | `scripts/build_coursework.py` 的 `ZIP_DIRS` 列表 | 课程作业 zip 少目录 |
| `data/` | `app/config.py` `app/demo.py` `app/main.py` 的 `BASE_DIR/"data"`；`/data` 静态挂载 | 图片 404、种子读不到 |
| `output/01-交付物/` | 4 个构建脚本的 `OUT_DIR` 常量 + 3 个测试的 `PAGE` 常量 | 重新生成会造出第二份，验收脚本读不到 |
| `output/02-截图证据/` 六个子目录名 | `shoot_ui.py` / `check_demo_page.py` / `check_demo_v2.py` 的 `SHOTS` 常量 | 截图落到旧位置，文档配图失效 |
| `docs/verify/` | `build_docx.py` 把该路径作为**文字**写进 Word 正文 | 说明书里的引用指不到实际文件 |

**推论**：以后要改这些路径，正确顺序是「改常量 → 改测试 → 改文档文字 → 重跑全部产物」，
不能只 `mv` 目录。`output/产物清单.md` 第六节记着上一次这么改的完整对照表，可以照抄流程。

**唯一可以自由增删的**：`docs/` 下的文档、`output/03-过程材料/`、任何 `__pycache__`。

---

## 5. 常见「我该跑哪个」

| 场景 | 命令 |
|---|---|
| 第一次拿到这个仓库，想看看 | `python bootstrap.py` 然后开 http://127.0.0.1:8000/ |
| 要答辩演示，稳一点 | `python bootstrap.py --strict`，失败就用两份 HTML |
| 只有一台干净电脑，什么都没装 | 直接双击 `output/01-交付物/演示页面.html` |
| 改完代码想知道有没有改坏 | `python tests/run_all.py --group pure` |
| 改完 SQL 想知道约束还在不在 | `python -m scripts.pg_run --reset && python -m tests.check_pg` |
| 要用真库跑起来给人看 | `ICH_DB_PORT=55432 ICH_DB_NAME=heritage python bootstrap.py --strict` |
| 改了种子数据 | `python scripts/check_seed.py` → `python scripts/build_docx.py` → `python scripts/build_coursework.py` |
| 只想交作业 | 压缩 `output/04-课程作业/` 整个目录 |
| 想知道接下来做什么 | `docs/02-研究规划/研究方向精进路线图.md` 第 2 节优先级表 |
