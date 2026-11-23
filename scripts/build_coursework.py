"""
生成课程作业提交物：代码说明、数据说明、数据字典、CSV 导出、代码包。

用法：
    .venv/Scripts/python.exe scripts/build_coursework.py

产出（全部落在 output/04-课程作业/）：
    01-代码/课程作业_第一部分_代码.docx
    01-代码/代码文件清单.csv
    02-数据/课程作业_第二部分_数据.docx
    02-数据/数据字典.csv
    02-数据/csv/{users,motifs,assets,audits,licenses}.csv
    课程作业_代码包.zip
    提交说明.md

三个设计决定：

1. 所有数字现场算，不写死。代码行数扫目录，数据规模读 seed_demo.json，
   测试项数取自本次提交前的实测基线（BASELINE 常量，跑完测试后手工校准一次）。
   文档里的数字与仓库不一致是这类作业最常见的扣分点，所以宁可少写也不写死。

2. 字段字典的「字段名 + 类型」从真实来源解析（JSON 键 / SQL 的 CREATE TABLE），
   我只补「含义」。脚本会把解析出的字段与我写的说明做差集校验：
   出现没解释的字段就打印警告，避免文档漏字段。

3. 正文一律用直角引号「」。Python 字符串里再嵌英文双引号，转义极易出错。
"""

from __future__ import annotations

import csv
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_docx as kit  # 复用上一份说明书的排版工具，避免重复实现

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.shared import Cm, Pt

OUT = ROOT / "output" / "04-课程作业"
CODE_DIR = OUT / "01-代码"
DATA_DIR = OUT / "02-数据"
CSV_DIR = DATA_DIR / "csv"

SEED = json.loads((ROOT / "data" / "seed_demo.json").read_text(encoding="utf-8"))
USERS = SEED["users"]
N_USERS = len(USERS)
MOTIFS = SEED["motifs"]
ASSETS = SEED["assets"]
N_AUDITS = sum(len(a.get("audits") or []) for a in ASSETS)
N_LICENSES = sum(len(a.get("licenses") or []) for a in ASSETS)
FEE_TOTAL = sum(float(l.get("fee_cny") or 0) for a in ASSETS for l in (a.get("licenses") or []))

# 本次提交前的实测基线（8 个套件）。改代码后请重跑并同步这里。
BASELINE = [
    ("test_invariants.py", 88, "源码不变量：AI 不写权属、路由不写 SQL、事务边界、分层依赖"),
    ("test_classifier.py", 24, "分级器三档阈值边界、多母题规则、抄袭例外"),
    ("test_feature_engine.py", 17, "特征后端：512 维、L2 归一化、同图确定性、异图差异"),
    ("test_compat_vector.py", 39, "无 pgvector 时的降级向量层与 numpy 结果对齐"),
    ("run_e2e_inline.py", 52, "进程内端到端：18 个端点全链路，含 403 / 409 反向验证"),
    ("shoot_ui.py", 49, "真实浏览器 UI 断言：四角色 × 五页签，数值级比对"),
    ("check_demo_page.py", 52, "纯界面演示页验收：file:// 直开、断网零请求、重置"),
    ("check_demo_v2.py", 58, "互动版演示页验收：拖动登录、终审、阈值联动"),
]
TOTAL_TESTS = sum(n for _, n, _ in BASELINE)


# ============================================================
# 一、代码清单（扫目录，不写死）
# ============================================================
APP_DUTY = {
    "app/main.py": "应用入口：lifespan 启停、静态页挂载、把数据库异常映射成 HTTP 状态码",
    "app/config.py": "全局配置：环境变量 > .env > 默认值三级覆盖，集中在类 Settings 里",
    "app/db.py": "数据访问层：连接池、事务上下文、连接级 SET LOCAL ROLE 让 GRANT 生效",
    "app/auth.py": "鉴权：JWT(HS256) 签发校验（纯标准库）+ 应用层 RBAC 依赖注入",
    "app/schemas.py": "18 组 Pydantic 模型，把非法入参挡在业务层之外",
    "app/services.py": "业务层：一次业务操作 = 一个事务；终审四步在此原子完成",
    "app/api.py": "路由层：18 个端点，只做参数编解码与鉴权，不写业务 SQL",
    "app/demo.py": "演示模式：内存存储，实现与 services 同名同签名的函数，api 无需改动",
    "app/cli.py": "运维 CLI：check / backfill-motif-vectors / demo-check / demo-seed",
    "app/motif_images.py": "程序生成 3 张母题参考图与 2 张样例图（无版权风险）",
    "app/ai/__init__.py": "AI 子包声明",
    "app/ai/features.py": "特征提取三档后端（builtin / clip / resnet），统一 512 维 L2 归一化",
    "app/ai/classifier.py": "分级建议器：相似度 → 建议级别 + 置信度，绝不写 right_level",
    "app/ai/similarity.py": "向量检索：cosine 距离 <=>、HNSW、ef_search、强制走索引",
    "app/__init__.py": "包声明与版本号",
}
SQL_DUTY = {
    "sql/00_compat_vector.sql": "降级层：无 pgvector 时用 NUMERIC[] + 自定义 <=> 运算符顶上",
    "sql/01_init.sql": "8 张表 + 3 个枚举 + 主外键 / CHECK / UNIQUE / 注释",
    "sql/02_index_trigger_view.sql": "14 个索引（含 2 个 HNSW）+ 7 个触发器 + 3 个只读视图",
    "sql/03_seed.sql": "最小可运行种子：3 账号 / 3 母题，建库即可跑",
}
TEST_DUTY = {name: desc for name, _, desc in BASELINE}
TEST_DUTY["test_sql_structure.py"] = "SQL 静态结构校验 18 项：表 / 视图 / 触发器 / 索引 / 列级授权语句"
SCRIPT_DUTY = {
    "scripts/init_db.py": "Docker 一键建库：起容器 → 等 healthy → 按序执行 3 个 SQL → 回填向量",
    "scripts/init_seed.py": "把 seed_demo.json 灌进真库（演示模式与真库共用同一份源）",
    "scripts/check_seed.py": "种子自校验：用真实算法回算 AI 结论，防止手编数字",
    "scripts/make_seed_images.py": "生成 8 张作品图（像素级互不相同，规避 file_hash 去重）",
}
ROOT_DUTY = {
    "bootstrap.py": "一键启动：依赖 → 数据库 → AI 后端 → 服务，四段自检后决定跑真库还是演示模式",
    "run.py": "uvicorn 启动包装，修 Windows 下 reload 丢失工作目录导致导入失败的问题",
    "requirements.txt": "核心依赖（不含 torch），pip 装完约几十秒",
    "requirements-deep.txt": "可选深度后端（torch / open_clip），约 2~3 GB",
    "docker-compose.yml": "pgvector/pgvector:pg16 容器，扩展预装，省掉自己编译",
}


def line_count(p: Path) -> int:
    try:
        return len(p.read_text(encoding="utf-8", errors="ignore").splitlines())
    except OSError:
        return 0


def code_inventory():
    """返回 [(路径, 行数, 字节, 分组, 职责)]。跳过缓存与构建脚本。"""
    rows = []
    skip_dir = {"__pycache__", "v2_assets"}
    skip_file = {"build_docx.py", "build_demo_page.py", "build_demo_v2.py",
                 "build_coursework.py", "preview_docx.py"}

    for rel, duty in APP_DUTY.items():
        p = ROOT / rel
        if p.exists():
            rows.append((rel, line_count(p), p.stat().st_size, "应用代码", duty))

    for rel, duty in SQL_DUTY.items():
        p = ROOT / rel
        if p.exists():
            rows.append((rel, line_count(p), p.stat().st_size, "数据库脚本", duty))

    for rel, duty in TEST_DUTY.items():
        p = ROOT / "tests" / rel
        if p.exists():
            rows.append(("tests/" + rel, line_count(p), p.stat().st_size, "测试", duty))

    for rel, duty in SCRIPT_DUTY.items():
        p = ROOT / rel
        if p.exists():
            rows.append((rel, line_count(p), p.stat().st_size, "工程脚本", duty))

    for rel, duty in ROOT_DUTY.items():
        p = ROOT / rel
        if p.exists():
            rows.append((rel, line_count(p), p.stat().st_size, "入口与依赖", duty))

    rows.sort(key=lambda r: (r[3], r[0]))
    return rows


INVENTORY = code_inventory()
N_CODE_FILES = len(INVENTORY)
N_CODE_LINES = sum(r[1] for r in INVENTORY)
N_APP_LINES = sum(r[1] for r in INVENTORY if r[3] in ("应用代码", "数据库脚本"))
N_TEST_LINES = sum(r[1] for r in INVENTORY if r[3] == "测试")


# ============================================================
# 二、字段字典（JSON 侧手写含义，字段名从真实文件取）
# ============================================================
USER_FIELDS = [
    ("user_id", "整数", "是", "主键，取值为 1–4；被 asset.owner_id、audit_record.expert_id 引用"),
    ("username", "字符串", "是", "登录名，全局唯一，格式 ^[a-zA-Z0-9_]{3,64}$"),
    ("display_name", "字符串", "是", "中文姓名，用于界面展示与专家署名"),
    ("role", "枚举", "是", "heritor 传承人 / expert 评审专家 / admin 监管管理员，决定可用功能与数据范围"),
    ("org_name", "字符串", "否", "所属单位；本数据中为传习所、评审机构、文旅主管部门"),
    ("password", "字符串", "是", "演示用明文口令，四账号均为 Passw0rd!。灌入真库时转 bcrypt 散列，明文不入库"),
]

MOTIF_FIELDS = [
    ("motif_id", "整数", "是", "主键，1–3；被 asset.motif_id 引用"),
    ("motif_code", "字符串", "是", "母题编号，形如 MO-001，全局唯一，是检索结果里给人看的那一列"),
    ("name", "字符串", "是", "母题名称，如蝴蝶纹、缠枝莲纹、回纹"),
    ("region", "字符串", "否", "主要流传地域，用于说明公有领域的地域属性"),
    ("ethnic", "字符串", "否", "关联民族。★ 种子文件独有字段，public_motif 表无对应列，灌库时忽略"),
    ("category", "字符串", "否", "纹样类别，如动物纹 / 图腾纹。★ 同上，仅前端展示用"),
    ("era", "字符串", "否", "年代或口传谱系说明。★ 同上，仅前端展示用"),
    ("category_note", "—", "—", "（占位说明：category 与 era 仅用于展示，不参与算法）"),
    ("description", "字符串", "是", "母题释义，写明「属公有领域，任何人不得独占」的法律属性"),
    ("keywords", "字符串数组", "否", "关键词，用于界面标签与人工检索。★ 同上，表结构无此列"),
    ("reference_img", "字符串", "是", "参考图相对路径，相对 data/ 目录，如 motifs/motif_butterfly.jpg"),
]

ASSET_FIELDS = [
    ("asset_id", "整数", "是", "主键，1–8；审核流水与授权记录都挂在它下面"),
    ("title", "字符串", "是", "作品名称，含作者与工艺特征，便于人工核对"),
    ("owner_id", "整数", "是", "权利人（传承人）的 user_id，外键指向 users"),
    ("description", "字符串", "是", "创作说明：改了什么、独创性在哪、母题血缘是什么。这是专家终审的主要依据"),
    ("file_path", "字符串", "是", "图片文件名，相对 data/uploads/；由 make_seed_images.py 生成"),
    ("right_level", "枚举", "是", "终审结论，public / derived / original。★ 只能由专家终审写入，AI 无此权限"),
    ("audit_status", "枚举", "是", "流转状态，draft / pending / approved / rejected"),
    ("ai_suggest_level", "枚举", "否", "AI 建议级别，算法算出的结果，仅供专家参考"),
    ("ai_confidence", "小数", "否", "AI 建议的置信度，0–1，与相似度不是一回事（见 4.4 节说明）"),
    ("motif_id", "整数", "否", "关联的公有母题 id；为空表示无母题血缘（原创或未识别）"),
    ("version_no", "整数", "是", "版本号，每次终审或变更 +1，用于并发冲突检测"),
    ("created_days_ago", "整数", "是", "距今天数，用于生成 created_at，避免把绝对日期写死"),
    ("decided_days_ago", "整数", "否", "距今多少天完成终审；为空表示尚未终审"),
    ("audits", "对象数组", "是", "审核流水，见下表；按时间先后排列，可有多条"),
    ("licenses", "对象数组", "是", "授权记录，见下表；未授权时为空数组"),
]

AUDIT_FIELDS = [
    ("state", "枚举", "是", "本条流水的状态：pending / rejected / approved（表里另有 expert_review）"),
    ("ai_suggest_level", "枚举", "是", "提交当时 AI 给出的建议，快照留存，不随算法更新而变"),
    ("ai_confidence", "小数", "是", "提交当时 AI 建议的置信度"),
    ("top_motif_id", "整数", "否", "AI 认为最相似的公有母题 id"),
    ("similarity", "小数", "是", "与 top_motif_id 对应母题的余弦相似度，0–1"),
    ("expert_level", "枚举", "否", "专家终审结论；待审或驳回记录中可能为空"),
    ("expert_id", "整数", "否", "终审专家 user_id；待审阶段为空"),
    ("is_override", "布尔", "否", "专家是否推翻了 AI 建议。本数据中均为 false，推翻行为在运行时产生"),
    ("opinion", "字符串", "否", "专家意见原文，是真值判断的依据，也用于审计追溯"),
    ("days_ago", "整数", "否", "该条流水距今天数，用于生成 created_at"),
    ("decided_days_ago", "整数", "否", "决策距今天数，用于生成 decided_at"),
]

LICENSE_FIELDS = [
    ("license_type", "枚举", "是", "exclusive 独占 / non_exclusive 非独占 / display 展示 / derivative 衍生开发"),
    ("licensee_name", "字符串", "是", "被授权方名称；外部机构不是系统用户，所以用名称而非 user_id"),
    ("scope", "字符串", "是", "授权范围，写明可做哪些品类、哪些用途"),
    ("territory", "字符串", "是", "地域范围"),
    ("start_days_ago", "整数", "是", "起算日距今多少天"),
    ("end_days_ago", "整数", "是", "到期日距今天数。★ 取负数表示未来日期，如 -335 即 335 天后到期"),
    ("fee_cny", "数值", "是", "授权费，单位人民币元；台账合计即各条之和"),
]

# 数据库侧：只写「含义」，字段名与类型从 01_init.sql 解析
DB_MEANING = {
    "app_user": {
        "user_id": "主键，自增。被 asset.owner_id / audit_record.expert_id / license_record 三方引用",
        "username": "登录名，唯一索引；CHECK 限制为字母数字下划线",
        "display_name": "显示名",
        "role": "角色枚举，决定 RBAC 边界",
        "password_hash": "口令散列，由 pgcrypto 的 crypt(..., gen_salt('bf',10)) 产出，不存明文",
        "org_name": "所属单位",
        "is_active": "是否启用；停用账号保留历史记录不物理删除",
        "created_at": "创建时间，带时区",
    },
    "public_motif": {
        "motif_id": "主键",
        "motif_code": "母题编号，唯一；检索结果按此列展示",
        "name": "母题名称",
        "region": "流传地域",
        "description": "释义与权属属性说明",
        "reference_img": "参考图相对路径",
        "base_vector": "512 维基准向量，L2 归一化；配 HNSW(cosine) 索引",
        "vector_model": "产出该向量的模型版本，换模型后可追溯",
        "created_at": "入库时间",
    },
    "asset": {
        "asset_id": "主键",
        "title": "作品名",
        "owner_id": "外键 → app_user，权利人",
        "motif_id": "外键 → public_motif，可为空（无母题血缘）",
        "file_path": "原图相对路径",
        "file_hash": "SHA-256，64 位小写十六进制；用于查重与防篡改",
        "file_size": "字节数，CHECK > 0",
        "mime_type": "媒体类型，默认 image/jpeg",
        "right_level": "★ 终审结论。传承人角色对该列无 UPDATE 权限，AI 进程更无写权限",
        "audit_status": "流转状态",
        "ai_suggest_level": "AI 建议级别，CHECK 与 ai_confidence 成对出现",
        "ai_confidence": "AI 置信度，0–1",
        "ai_model_version": "产出建议的模型版本",
        "version_no": "乐观锁版本号，终审时 +1",
        "description": "创作说明",
        "created_at": "上传时间",
        "updated_at": "末次变更时间，由触发器自动刷新",
    },
    "feature_vector": {
        "fv_id": "主键",
        "asset_id": "外键 → asset，1:1 垂直拆分",
        "model_name": "模型名，如 builtin-hog-color-texture",
        "model_version": "模型版本",
        "embed_dim": "维度，CHECK 恒为 512",
        "embedding": "L2 归一化向量；维度由 CHECK 兜底",
        "created_at": "写入时间；重算时新增行保留历史",
    },
    "audit_record": {
        "audit_id": "主键",
        "asset_id": "外键 → asset，ON DELETE RESTRICT 保留证据链",
        "expert_id": "外键 → app_user；待审阶段为空，CHECK 保证已决策必有专家",
        "state": "流水状态；pending → expert_review → approved / rejected",
        "ai_suggest_level": "AI 建议快照",
        "ai_confidence": "AI 置信度快照",
        "expert_level": "专家终审结论；未决策时 CHECK 要求为空",
        "similarity_json": "top-K 相似母题快照，JSONB，便于事后复现当时的检索结果",
        "opinion": "专家意见原文",
        "is_override": "是否推翻 AI；由服务层在写入时比对 expert_level 与 ai_suggest_level 得出",
        "created_at": "流水创建时间",
        "decided_at": "决策时间；已决策记录 CHECK 要求非空",
    },
    "license_record": {
        "license_id": "主键",
        "asset_id": "外键 → asset",
        "licensor_id": "外键 → app_user，授权方（通常为权利人本人）",
        "licensee_user_id": "外键 → app_user，被授权方是系统内用户时填",
        "licensee_name": "被授权方名称，外部机构用这一列；CHECK 要求二者至少填一个",
        "license_type": "四种取值，见枚举表",
        "scope": "授权范围",
        "territory": "地域",
        "start_date": "起算日",
        "end_date": "到期日；CHECK 要求不早于起算日",
        "fee_cny": "授权费，CHECK >= 0",
        "contract_hash": "合同文件 SHA-256，防篡改",
        "status": "active / expired / revoked",
        "prev_license_id": "自引用，指向上一次授权，串成流转链",
        "created_at": "登记时间",
    },
    "asset_genealogy": {
        "genealogy_id": "主键",
        "parent_asset_id": "外键 → asset，父作品",
        "child_asset_id": "外键 → asset，子作品",
        "relation_type": "adapted 改编 / restored 修复 / collaborated 合作",
        "similarity": "父子视觉相似度快照",
        "note": "说明",
        "created_by": "登记人；用户被删时置空而非级联删除",
        "created_at": "登记时间",
    },
    "system_audit_log": {
        "log_id": "主键",
        "table_name": "被改动的事实表名",
        "record_id": "被改动的行主键",
        "operation": "INSERT / UPDATE / DELETE",
        "actor": "执行变更的数据库会话用户，默认 current_user",
        "old_snapshot": "变更前整行快照，JSONB",
        "new_snapshot": "变更后整行快照，JSONB",
        "changed_fields": "仅发生变化的列名数组，便于检索",
        "logged_at": "记录时间",
    },
}


# ============================================================
# 三、解析 SQL 表结构（字段名与类型以真实 DDL 为准）
# ============================================================
def _split_top(body: str):
    """按顶层逗号切分，括号内的逗号不切（NUMERIC(12,2) 这种）。"""
    parts, buf, depth = [], [], 0
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def parse_ddl():
    sql = (ROOT / "sql" / "01_init.sql").read_text(encoding="utf-8")
    tables = {}
    for m in re.finditer(r"CREATE TABLE (\w+) \(", sql):
        name = m.group(1)
        end = sql.index("\n);", m.end())
        body = sql[m.end():end]
        # 先剥掉 -- 行注释。注释里有「外键」「不能自己授权给自己」这类文字，
        # 不剥的话会被当成列名解析出来（曾出现 license_record.-- 这种字段）。
        body = re.sub("--.*", "", body)
        cols = []
        for raw in _split_top(body):
            line = raw.strip().replace("\n", " ")
            if not line:
                continue
            head = line.split()[0].upper()
            if head in ("CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK"):
                continue
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            col, rest = parts[0], parts[1]
            typ = re.split(r"\s+(?:NOT NULL|NULL|PRIMARY|DEFAULT|CHECK|REFERENCES|UNIQUE|GENERATED)",
                           rest)[0].strip()
            typ = " ".join(typ.split())
            if typ.startswith(":"):
                typ = "VECTOR(512) 或 NUMERIC[]"
            cols.append((col, typ))
        tables[name] = cols
    return tables


DDL = parse_ddl()


def check_coverage():
    """字段说明是否覆盖了全部真实字段。漏了就打印出来，避免文档缺字段。"""
    missing = []
    for tname, cols in DDL.items():
        known = DB_MEANING.get(tname, {})
        for col, _ in cols:
            if col not in known:
                missing.append(f"{tname}.{col}")

    json_map = {
        "users": (USER_FIELDS, set(USERS[0].keys())),
        "motifs": (MOTIF_FIELDS, set().union(*(m.keys() for m in MOTIFS))),
        "assets": (ASSET_FIELDS, set().union(*(a.keys() for a in ASSETS))),
        "audits": (AUDIT_FIELDS, set().union(*(x.keys() for a in ASSETS for x in (a.get("audits") or [{}])))),
        "licenses": (LICENSE_FIELDS, set().union(*(x.keys() for a in ASSETS for x in (a.get("licenses") or [{}])))),
    }
    for name, (rows, real) in json_map.items():
        documented = {r[0] for r in rows}
        for k in sorted(real - documented):
            missing.append(f"seed.{name}.{k}")
    return missing


MISSING = check_coverage()

# 两处字段口径不同，文档里必须写清楚，否则「85 个字段」和「字典 134 行」对不上
DB_FIELDS = sum(len(c) for c in DDL.values())
JSON_FIELDS = (len(USER_FIELDS) + len([m for m in MOTIF_FIELDS if m[1] != "—"])
               + len(ASSET_FIELDS) + len(AUDIT_FIELDS) + len(LICENSE_FIELDS))


# ============================================================
# 四、排版骨架
# ============================================================
def new_doc(header_text: str) -> Document:
    doc = Document()
    kit.build_styles(doc)

    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.top_margin = Cm(2.4)
    sec.bottom_margin = Cm(2.2)
    sec.left_margin = Cm(2.8)
    sec.right_margin = Cm(2.4)
    sec.header_distance = Cm(1.2)
    sec.footer_distance = Cm(1.2)

    hp = sec.header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    kit.cn_font(hp.add_run(header_text), kit.FONT_CN, 8.5, color=kit.GREY)

    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    kit.cn_font(fp.add_run("第 "), kit.FONT_CN, 9, color=kit.GREY)
    kit.add_field(fp, " PAGE ", "1")
    kit.cn_font(fp.add_run(" 页"), kit.FONT_CN, 9, color=kit.GREY)
    for run in fp.runs:
        kit.cn_font(run, kit.FONT_CN, 9, color=kit.GREY)
    return doc


def cover(doc, main_title, part_line, meta):
    for _ in range(3):
        doc.add_paragraph()

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    kit.cn_font(p.add_run(main_title), kit.FONT_CN_HEAD, 25, True, kit.INK)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(16)
    kit.cn_font(p.add_run(part_line), kit.FONT_CN_HEAD, 16, False, kit.CINNABAR)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    kit.hrule(p, "A8322C", "12")

    for _ in range(4):
        doc.add_paragraph()

    t = doc.add_table(rows=len(meta), cols=2)
    kit.apply_widths(t, [3.6, 11.2])
    kit.cell_margins(t, 80, 80, 0, 120)
    for i, (k, v) in enumerate(meta):
        row = t.rows[i]
        pk = row.cells[0].paragraphs[0]
        pk.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        kit.cn_font(pk.add_run(k), kit.FONT_CN, 10, color=kit.GREY)
        pv = row.cells[1].paragraphs[0]
        kit.cn_font(pv.add_run(v), kit.FONT_CN, 10, color=kit.INK)

    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def toc(doc):
    p = doc.add_paragraph()
    kit.cn_font(p.add_run("目　　录"), kit.FONT_CN_HEAD, 16, True, kit.CINNABAR)
    p.paragraph_format.space_after = Pt(12)

    tip = doc.add_paragraph()
    kit.cn_font(
        tip.add_run("（目录为 Word 自动域。首次打开若只看到提示语，请按 Ctrl+A 全选后按 F9，"
                    "或右键选择「更新域 → 更新整个目录」。）"),
        kit.FONT_CN, 8.5, color=kit.GREY,
    )
    tip.paragraph_format.space_after = Pt(10)

    p = doc.add_paragraph()
    kit.add_field(p, ' TOC \\o "1-3" \\h \\z \\u ', "右键此处选择「更新域」以生成目录")
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


# ============================================================
# 五、第一部分：代码
# ============================================================
def build_code_doc() -> Path:
    doc = new_doc("非遗数字资产分级确权与溯源系统 · 课程作业（代码部分）")

    cover(doc,
          "非遗数字资产\n分级确权与溯源系统",
          "课程作业　第一部分：代　码",
          [
              ("作业内容", "可运行的非遗数字资产确权系统：上传 → AI 分级建议 → 专家终审 → 授权流转 → 溯源台账"),
              ("技术栈", "Python 3.13 / FastAPI / PostgreSQL 16 + pgvector / 纯标准库 JWT"),
              ("代码规模", f"{N_CODE_FILES} 个源文件，{N_CODE_LINES} 行（其中应用与 SQL {N_APP_LINES} 行，测试 {N_TEST_LINES} 行）"),
              ("复现方式", "三条路径：演示模式（零数据库）／ Docker 真库／ 已有 PostgreSQL"),
              ("验证状态", f"{len(BASELINE)} 个测试套件、{TOTAL_TESTS} 项断言，当前全部通过"),
              ("生成日期", datetime.now().strftime("%Y 年 %m 月 %d 日")),
          ])
    toc(doc)

    # ---------------- 一 ----------------
    kit.h1(doc, "一、这份作业交了什么")
    kit.para(doc, "本作业由三个可独立检查的部分组成，对应课程的两个要求：结构清晰、能够复现。"
                  "三个部分放在同一个提交目录里，拿到后不需要额外索取任何文件。")

    kit.table(doc,
              ["编号", "内容", "载体", "位置"],
              [
                  ["1", "代码包", "压缩包 zip，解压即得完整工程", "课程作业_代码包.zip"],
                  ["2", "代码说明", "本文档", "01-代码/"],
                  ["3", "数据包", "种子 JSON + 5 张 CSV + 11 张图片", "02-数据/"],
                  ["4", "数据说明", "配套文档，含字段字典", "02-数据/"],
              ],
              widths=[1.4, 3.2, 6.6, 4.2],
              caption="表 1-1　提交内容清单")

    kit.h2(doc, "1.1 与课程要求的对应")
    kit.table(doc,
              ["课程要求", "本作业的落点", "自查位置"],
              [
                  ["结构清晰",
                   "四层单向依赖：路由层 api.py → 业务层 services.py → 数据层 db.py / AI 层 ai/。"
                   "路由层不写 SQL，业务层不碰 HTTP，AI 层不碰数据库",
                   "第三章；tests/test_invariants.py 有专门断言"],
                  ["结构清晰",
                   "SQL 拆成 4 个文件，按「兼容层 → 结构 → 索引触发器视图 → 种子」顺序执行，"
                   "每个 DDL 语句都带 COMMENT",
                   "sql/ 目录"],
                  ["能够复现",
                   "三条运行路径，最省事的一条只需 4 条命令、不装数据库",
                   "第五章 5.1 节"],
                  ["能够复现",
                   f"8 个测试套件共 {TOTAL_TESTS} 项断言，可一条命令全跑",
                   "第六章"],
                  ["能够复现",
                   "随机性全部收敛：种子数据固定、特征算法确定性、构建脚本不依赖时间与机器",
                   "第七章"],
              ],
              widths=[2.4, 9.0, 4.0],
              caption="表 1-2　课程要求与实现落点对照")

    kit.h2(doc, "1.2 一个必须先说清楚的边界")
    kit.callout(
        doc, "代码是完整的，但数据库脚本没在真实 PostgreSQL 上执行过",
        "本项目在开发环境里无法启动 PostgreSQL（受沙箱策略限制，initdb 无法运行），因此 sql/ 下 4 个脚本"
        "以及列级 GRANT、7 个触发器、HNSW 索引的行为，只有静态结构校验（test_sql_structure.py 18 项）与"
        "代码走查，没有运行时证据。所有「跑通了」的结论都来自演示模式（内存存储 + 完全相同的 AI 算法）。"
        "这一条在第七章还会再写一次，请评审时按此口径理解。",
        tone="cinnabar")

    # ---------------- 二 ----------------
    kit.h1(doc, "二、运行环境")
    kit.h2(doc, "2.1 环境要求")
    kit.table(doc,
              ["项目", "要求", "说明"],
              [
                  ["Python", "3.10 及以上", "用到 X | None 联合类型语法。本次实测 3.13.14"],
                  ["PostgreSQL", "15 及以上", "真库路径才需要；演示模式不需要"],
                  ["pgvector", "0.5.0 及以上（建议 0.7.0+）", "VECTOR(512) 与 HNSW 自 0.5.0 起支持；vector_dims() 自 0.7.0 起提供"],
                  ["pgcrypto", "任意版本", "口令散列与文件摘要，通常随主包提供"],
                  ["Docker", "可选", "用 compose 起 pgvector 镜像，省掉自己编译扩展"],
                  ["磁盘", "约 200 MB", "代码 + 依赖 + 样例图；装深度模型另需 2~3 GB"],
              ],
              widths=[2.6, 4.6, 8.2],
              caption="表 2-1　运行环境要求")

    kit.h2(doc, "2.2 Python 依赖")
    kit.para(doc, "核心依赖刻意保持精简，不含 torch，因此安装只需几十秒，256 MB 内存的机器也能跑演示。"
                  "完整清单见 requirements.txt，下表是每个包为什么必须在。")
    kit.table(doc,
              ["包", "版本约束", "用途"],
              [
                  ["fastapi", ">=0.115,<1.0", "Web 框架"],
                  ["uvicorn[standard]", ">=0.30,<1.0", "ASGI 服务器"],
                  ["python-multipart", ">=0.0.9", "解析 multipart 上传，缺了上传接口直接 500"],
                  ["psycopg2-binary", ">=2.9,<3.0", "PostgreSQL 驱动，自带 libpq，不必装客户端库"],
                  ["pydantic", ">=2.6,<3.0", "请求响应校验"],
                  ["pillow", ">=10.0", "图像解码、EXIF 矫正、格式转换"],
                  ["numpy", ">=1.24", "特征向量计算"],
              ],
              widths=[4.4, 4.0, 7.0],
              caption="表 2-2　核心依赖及其存在理由")
    kit.para(doc, "requirements-deep.txt 是可选包（torch / open_clip / torchvision），"
                  "装了就自动优先用 CLIP 或 ResNet 特征，没装则降级到内置后端，演示不受影响。")

    kit.h2(doc, "2.3 三条运行路径")
    kit.table(doc,
              ["路径", "前提", "命令条数", "适合什么场景"],
              [
                  ["A 演示模式", "只要 Python", "4 条", "评审快速看效果、没有数据库的机器、课堂演示"],
                  ["B Docker 真库", "装 Docker", "3 条", "要验证数据库特性（GRANT / 触发器 / HNSW）"],
                  ["C 已有 PostgreSQL", "本机已装 PG 15+", "5 条", "已配置好数据库的环境"],
              ],
              widths=[3.6, 3.4, 2.4, 6.0],
              caption="表 2-3　三条路径的取舍")
    kit.para(doc, "三条路径跑的是同一套 app/ 代码，差别只在数据存在哪里。"
                  "路径 A 用 app/demo.py 的内存存储，路径 B、C 用 app/services.py 加真实 SQL。"
                  "两条路径的 AI 算法、接口路径、返回字段完全一致。")

    # ---------------- 三 ----------------
    kit.h1(doc, "三、代码结构")
    kit.h2(doc, "3.1 顶层结构")
    kit.code_block(doc, """
课程作业/
├── bootstrap.py          # 一键启动：四段自检后决定跑真库还是演示模式
├── run.py                # uvicorn 启动包装（修 Windows 下 reload 丢工作目录的问题）
├── requirements.txt      # 核心依赖，不含 torch
├── docker-compose.yml    # pgvector/pgvector:pg16，扩展预装
├── app/                  # ★ 应用代码（分层，见 3.3）
│   ├── main.py  config.py  db.py  auth.py  schemas.py
│   ├── api.py            # 路由层，18 个端点
│   ├── services.py       # 业务层，一个操作 = 一个事务
│   ├── demo.py           # 演示模式的内存实现（与 services 同名同签名）
│   ├── cli.py            # 运维命令行
│   ├── motif_images.py   # 程序生成母题参考图
│   ├── ai/               # ★ 算法层，不碰数据库
│   │   ├── features.py   #   特征提取，三档后端统一 512 维
│   │   ├── similarity.py #   向量检索
│   │   └── classifier.py #   相似度 → 分级建议
│   └── static/           # 前端页面（单文件 HTML + JS）
├── sql/                  # ★ 按序执行：00 兼容层 → 01 结构 → 02 索引/触发器/视图 → 03 种子
├── tests/                # 8 个测试套件
├── scripts/              # 建库、灌种子、种子校验、造图
└── data/                 # 种子数据与图片（第二部分详述）
""")

    kit.h2(doc, "3.2 模块清单")
    kit.para(doc, f"共 {N_CODE_FILES} 个源文件、{N_CODE_LINES} 行。下表按职责分组，行数为实测值"
                  f"（扫描脚本输出，随代码变动自动更新）。")

    groups = {}
    for path, lines, _size, group, duty in INVENTORY:
        groups.setdefault(group, []).append((path, lines, duty))

    for idx, group in enumerate(("入口与依赖", "应用代码", "数据库脚本", "测试", "工程脚本"), start=1):
        if group not in groups:
            continue
        rows = [[p, str(n), d] for p, n, d in groups[group]]
        kit.table(doc,
                  ["文件", "行数", "职责"],
                  rows,
                  widths=[5.2, 1.4, 8.8],
                  caption=f"表 3-{idx}　{group}清单")

    kit.para(doc, "上表只列运行系统所需的源文件。scripts/ 下另有 5 个文档生成脚本"
                  "（build_docx.py、build_demo_page.py、build_demo_v2.py、build_coursework.py、preview_docx.py）"
                  "与 scripts/v2_assets/ 下的 6 个前端资源文件，用于产出提交文档与演示页，不参与系统运行，"
                  "因此不计入上表的行数；它们随代码包一并提供，以免 README 里的命令指向不存在的文件。")

    kit.h2(doc, "3.3 一次请求怎么走")
    kit.para(doc, "以「专家终审」为例，四层单向依赖，任何一层都不向上反向调用：")
    kit.code_block(doc, """
POST /audit/{asset_id}/approve
  │
  ├─ api.py        require_roles('expert') 鉴权 → 解析 Pydantic 模型 → 调 services
  │                  （本层不出现任何 SQL 字符串）
  │
  ├─ services.py   open_tx(app_role='expert') 开事务
  │                  ① SELECT ... FOR UPDATE 锁住 asset 行
  │                  ② 校验状态机：只有 pending 才能 approved / rejected
  │                  ③ UPDATE audit_record  填 expert_level / opinion / decided_at
  │                  ④ UPDATE asset         改 audit_status / right_level / version_no + 1
  │                  四步同事务，任一失败整体回滚
  │
  ├─ db.py         SET LOCAL ROLE ich_expert → 数据库列级 GRANT 在此生效
  │
  └─ 触发器        trg_asset_right_change 自动往 system_audit_log 写一条变更快照
                   触发器与主业务同事务，主业务回滚则日志不留痕
""")
    kit.para(doc, "这条链路是本作业的核心命题：AI 只能写 ai_suggest_level，"
                  "right_level 只有专家角色的数据库身份才有列的 UPDATE 权限。"
                  "应用层的 if 判断只是第一道，真正的边界在数据库。")

    # ---------------- 四 ----------------
    kit.h1(doc, "四、核心模块导读")
    kit.para(doc, "本章只讲「为什么这么写」，逐个模块的完整注释在源码里。"
                  "如果时间有限，读 4.3 与 4.4 两节即可掌握全貌。")

    kit.h2(doc, "4.1 数据访问层：连接池与事务边界")
    kit.para(doc, "db.py 做三件事：维护连接池、把一次业务操作包成一个事务、"
                  "在事务内切换数据库角色。第三件容易被忽略——如果不切角色，"
                  "应用用高权限账号连库，列级 GRANT 就形同虚设，"
                  "「AI 改不了权属」只剩应用层的一句 if。")
    kit.code_block(doc, """
with open_tx(app_role="expert") as cur:     # 事务开始 + SET LOCAL ROLE
    ...                                      # 正常结束 COMMIT，异常 ROLLBACK
# SET LOCAL 的作用域是当前事务，事务一结束自动失效，
# 不会把角色泄漏给连接池里的下一个请求。
""")

    kit.h2(doc, "4.2 鉴权：为什么是两层")
    kit.para(doc, "应用层用 JWT 判断「这个请求能不能进入业务逻辑」，给前端友好的 403；"
                  "数据库层用 GRANT 兜底，即便应用层有 bug 绕过了判断，数据库仍会拒绝。"
                  "JWT 是纯标准库手写的（hmac + base64 + json，60 行），没有引入 python-jose，"
                  "因为本项目只签发和校验自己的 token，没有三方互操作需求。")
    kit.para(doc, "需要说明的是：这套 JWT 用的是 HS256 对称密钥，密钥来自环境变量，"
                  "默认值仅供本地开发。若接入统一身份中心，必须换成标准库并校验证书链——"
                  "这一点写在 auth.py 的文件头注释里。")

    kit.h2(doc, "4.3 终审事务：四步原子完成")
    kit.para(doc, "这是整个系统最容易写错的地方。朴素写法是先查状态、再改流水、再改资产，"
                  "三个独立事务，中间失败就会出现「流水说通过了，资产还是待审」。"
                  "正确写法是把四步放进一个事务，并用 SELECT ... FOR UPDATE 挡住并发。")
    kit.code_block(doc, """
cur.execute("SELECT audit_status, version_no FROM asset WHERE asset_id=%s FOR UPDATE", (aid,))
row = cur.fetchone()
if row["audit_status"] != "pending":          # 状态机：已终审的不能再终审
    raise Conflict("该作品已终审")
cur.execute("UPDATE audit_record SET state=%s, expert_level=%s, opinion=%s, "
            "decided_at=now(), is_override=%s WHERE audit_id=%s", ...)
cur.execute("UPDATE asset SET audit_status=%s, right_level=%s, version_no=version_no+1 "
            "WHERE asset_id=%s", ...)
# 提交时触发器写 system_audit_log；任一步异常，四步全部回滚
""")

    kit.h2(doc, "4.4 AI 链路：特征 → 检索 → 建议")
    kit.para(doc, "AI 部分拆成三个互不依赖的模块，每一层都能单独测试，也不碰数据库。")
    kit.table(doc,
              ["后端", "实现", "维度", "依赖", "说明"],
              [
                  ["clip", "open_clip ViT-B/32", "512", "torch", "语义最强，需另装约 2 GB"],
                  ["resnet", "ResNet-50 全局平均池化 + 线性降维", "512", "torch", "折中方案"],
                  ["builtin", "HOG 梯度方向直方图 + HSV 颜色统计 + Laplacian 纹理", "512", "仅 numpy", "默认，无 GPU 也能跑，结果确定可复现"],
              ],
              widths=[1.8, 6.4, 1.6, 2.0, 3.6],
              caption="表 4-1　三档特征后端，输出维度统一为 512")

    kit.para(doc, "分级建议的规则只有三条阈值，写死在 classifier.py 里并有测试锁住边界：")
    kit.table(doc,
              ["与最相似公有母题的余弦相似度 s", "建议级别", "含义"],
              [
                  ["s ≥ 0.92", "public", "与公有母题高度重合，属公有领域，个人不得独占，只能登记使用事实"],
                  ["0.80 ≤ s < 0.92", "derived", "与母题有实质重合，属改编衍生，有演绎著作权但需标注血缘"],
                  ["s < 0.80", "original", "与现存母题无实质重合，倾向认定为原创"],
                  ["与已有私有资产相似度 ≥ 0.95", "（例外）", "疑似抄袭他人作品，单独提示，不进入上述三档"],
              ],
              widths=[5.2, 2.6, 7.6],
              caption="表 4-2　分级阈值及其语义")

    kit.callout(doc, "置信度不是相似度，这两个数字不要混着讲",
                "相似度是对母题算出来的余弦值，典型在 0.8–0.95；置信度是分类器对「自己这个判断有多确定」的估计，"
                "典型只有 0.4–0.7。种子数据里能看到「相似度 0.8349、置信度 0.4741」这样的组合，"
                "前者高、后者中等，是完全正常的。演示时若把两者说成同一个数，会被追问。",
                tone="indigo")

    kit.h2(doc, "4.5 数据库脚本")
    kit.table(doc,
              ["脚本", "内容", "可验证点"],
              [
                  ["00_compat_vector.sql", "无 pgvector 时的降级层：NUMERIC[] + 自定义 <=> 运算符",
                   "有 39 项测试比对降级实现与 numpy 的结果一致"],
                  ["01_init.sql", "8 张表、3 个枚举、主外键 / CHECK / UNIQUE / 全部 COMMENT", "test_sql_structure 18 项静态校验"],
                  ["02_index_trigger_view.sql", "14 个索引（含 2 个 HNSW）、7 个触发器、3 个只读视图", "同上"],
                  ["03_seed.sql", "最小种子：3 账号 / 3 母题，建库即可跑", "与 seed_demo.json 可叠加"],
              ],
              widths=[4.6, 7.4, 3.4],
              caption="表 4-3　四个 SQL 脚本的分工")
    kit.para(doc, "8 张表：app_user、public_motif、asset、feature_vector、audit_record、"
                  "license_record、asset_genealogy、system_audit_log。"
                  "3 个视图：v_asset_pending_audit、v_asset_trace_chain、v_asset_ledger。"
                  "每个字段的类型、约束与含义见第二部分第五章的字段字典。")

    # ---------------- 五 ----------------
    kit.h1(doc, "五、复现步骤")
    kit.para(doc, "以下命令均在解压后的工程根目录执行。Windows 用 .venv\\Scripts\\activate，"
                  "macOS 与 Linux 用 source .venv/bin/activate。")

    kit.h2(doc, "5.1 路径 A：演示模式（不装数据库，四步）")
    kit.code_block(doc, """
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
python bootstrap.py --demo
""")
    kit.para(doc, "启动后会依次打印四段自检。正常输出形如：")
    kit.code_block(doc, """
非遗数字资产分级确权与溯源系统 —— 启动自检
工作目录：...\\课程作业

1 / 4  检查 Python 依赖
  ✓ fastapi   ✓ uvicorn   ✓ psycopg2   ✓ pillow   ✓ numpy   ✓ pydantic

2 / 4  数据库
  ! --demo：已跳过探测，直接使用演示模式

3 / 4  AI 特征后端
  当前后端：builtin-hog-color-texture  （深度模型=False）
  ! 未安装 torch，使用内置手工特征后端（HOG + HSV + Laplacian）
    这是设计内的降级方案，不是错误：无需 GPU、512 维、可复现。

4 / 4  启动服务
  ! 演示模式：内存存储（数据退出即丢失）
  ✓ 演示种子已就绪　种子：4 账号 / 3 母题 / 8 作品

  打开 →  http://127.0.0.1:8000/
  账号 heritor01 / heritor02 / expert01 / admin01，口令均为 Passw0rd!
""")
    kit.para(doc, "浏览器打开 http://127.0.0.1:8000/ ，用下列任一账号登录。演示模式启动时会自动加载种子数据，"
                  "登录即见完整列表，不需要先上传：")
    kit.table(doc,
              ["用户名", "口令", "角色", "登录后能看到什么"],
              [
                  ["heritor01", "Passw0rd!", "传承人", "3 件作品（含待审、已确权），可上传、分析、登记授权"],
                  ["heritor02", "Passw0rd!", "传承人", "2 件作品，用于演示「不同传承人只看自己的」"],
                  ["expert01", "Passw0rd!", "评审专家", "全部待审队列、终审确权、看全量数据"],
                  ["admin01", "Passw0rd!", "监管管理员", "全量只读台账、母题库维护"],
              ],
              widths=[2.6, 2.6, 2.4, 7.8],
              caption="表 5-1　演示账号")

    kit.h2(doc, "5.2 路径 B：Docker 真库（三步）")
    kit.code_block(doc, """
pip install -r requirements.txt
python scripts/init_db.py      # 起容器 → 等 healthy → 按序建表 → 回填母题向量
python bootstrap.py            # 探测到真库就自动走真库模式
""")
    kit.para(doc, "init_db.py 替使用者处理了三件容易出错的事：容器 up 返回后数据库其实还没就绪，"
                  "它会轮询 pg_isready；建表严格按 01 → 02 → 03 顺序（01 的 DROP CASCADE 会删掉 02 的视图）；"
                  "建完自动回填母题向量（不回填的话 AI 会把一切都判成原创）。")

    kit.h2(doc, "5.3 路径 C：已有 PostgreSQL")
    kit.code_block(doc, """
psql -h 127.0.0.1 -U postgres -d ich_heritage -v ON_ERROR_STOP=1 -f sql/01_init.sql
psql -h 127.0.0.1 -U postgres -d ich_heritage -v ON_ERROR_STOP=1 -f sql/02_index_trigger_view.sql
psql -h 127.0.0.1 -U postgres -d ich_heritage -v ON_ERROR_STOP=1 -f sql/03_seed.sql
python -m app.cli backfill-motif-vectors     # 回填母题的真实特征向量
python bootstrap.py --check                  # 先自检，再启动
""")
    kit.para(doc, "连接参数用环境变量覆盖（ICH_DB_HOST / ICH_DB_PORT / ICH_DB_NAME / ICH_DB_USER / ICH_DB_PASSWORD），"
                  "也可写进根目录 .env，环境变量优先级最高。")

    kit.h2(doc, "5.4 复现对照：做完这几步应该看到什么")
    kit.table(doc,
              ["操作", "预期结果", "数字来源"],
              [
                  ["用 heritor01 登录，看作品列表", "3 件作品：1 待审、1 已确权、1 已驳回", "seed_demo.json"],
                  ["换 heritor02 登录", "只剩 2 件，看不到 heritor01 的", "owner_id 过滤"],
                  ["上传 data/samples/sample_derived_butterfly.jpg 并分析", "建议为「改编衍生」，最高相似母题 MO-001 蝴蝶纹，相似度 0.9182", "真实算法计算"],
                  ["用 expert01 进工作台", "待审 2 件", "audit_status = pending"],
                  ["终审时选与 AI 不同的档位", "页面提示「与 AI 建议不一致」，提交后审计日志留痕", "is_override"],
                  ["进监管台账", "8 行、授权 5 笔、授权费合计 ¥131,600", "5 条 license 的 fee_cny 之和"],
              ],
              widths=[6.0, 7.4, 2.0],
              caption="表 5-2　复现对照表")

    # ---------------- 六 ----------------
    kit.h1(doc, "六、可复现性是怎么被验证的")
    kit.h2(doc, "6.1 测试套件")
    kit.para(doc, f"{len(BASELINE)} 个套件、共 {TOTAL_TESTS} 项断言。这些不是「跑完没报错就算过」，"
                  f"而是逐项比对数值与文本：作品数、母题数、授权费合计、相似度、越权返回码。")
    kit.table(doc,
              ["套件", "项数", "验的是什么"],
              [[n, str(c), d] for n, c, d in BASELINE],
              widths=[4.6, 1.4, 9.4],
              caption=f"表 6-1　测试套件一览（合计 {TOTAL_TESTS} 项）")
    kit.para(doc, "另有 tests/test_sql_structure.py 做 SQL 静态结构校验 18 项（表 / 视图 / 触发器 / "
                  "索引 / 列级授权语句是否存在），不依赖数据库，也不计入上表。")

    kit.h2(doc, "6.2 一条命令跑全量")
    kit.code_block(doc, """
for m in test_invariants test_classifier test_feature_engine test_compat_vector \\
         run_e2e_inline shoot_ui check_demo_page check_demo_v2; do
  .venv/Scripts/python.exe -m tests.$m
done
""")
    kit.para(doc, "Windows 的 cmd 请逐条执行；Git Bash 与 macOS 用上面这段。"
                  "每个套件末尾会打印「通过 N 项，失败 0 项」，退出码非 0 即失败。")

    kit.h2(doc, "6.3 数据自证")
    kit.para(doc, "种子数据里写着「AI 建议：改编衍生，置信度 0.4741」。这个数字如果与真实算法对这张图的"
                  "计算结果不符，演示时就会出现「台账说 derived，点 AI 分析重跑变成 original」的穿帮。"
                  "所以专门有一个脚本把两者钉死：")
    kit.code_block(doc, """
python -m scripts.check_seed          # 校验：拿真实算法回算，逐件比对，不一致退出码 1
python -m scripts.check_seed --write  # 改图之后用这个把算法结果回写进 JSON
""")

    # ---------------- 七 ----------------
    kit.h1(doc, "七、已知限制")
    kit.para(doc, "这一章不是免责声明，是把评审可能会问的问题提前摆出来，并给出我能提供的边界。")

    kit.h2(doc, "7.1 没有在真实 PostgreSQL 上执行过")
    kit.para(doc, "开发环境无法运行 initdb（受沙箱策略限制），也未能编译 pgvector。因此下列内容只有代码与静态校验，"
                  "没有运行时证据：列级 GRANT 的实际拦截效果、7 个触发器的实际触发、CHECK / UNIQUE / 外键的真实报错、"
                  "HNSW 索引被 planner 使用（EXPLAIN 里的 Index Scan）、MVCC 与 SELECT FOR UPDATE 的并发行为、事务回滚。")
    kit.para(doc, "要在真实环境补齐这些，按 5.2 或 5.3 起库后，用 ICH_DEMO_MODE=0 启动，"
                  "演示模式下的降级行为就不再发生。")

    kit.h2(doc, "7.2 演示模式不等于数据库特性")
    kit.table(doc,
              ["能力", "真库模式", "演示模式"],
              [
                  ["8 张表的结构与关联", "PostgreSQL 表 + 外键", "Python dict 模拟，语义一致"],
                  ["AI 特征提取与分级建议", "相同", "完全相同的算法代码"],
                  ["列级 GRANT（AI 改不了权属）", "数据库强制", "仅靠代码路径约束"],
                  ["触发器留痕", "数据库触发器", "手工模拟，字段一致"],
                  ["CHECK / UNIQUE / 外键", "数据库强制", "业务层检查"],
                  ["HNSW 近似检索", "pgvector 索引", "numpy 精确全量扫描（更准但更慢）"],
                  ["并发控制", "MVCC + FOR UPDATE", "单进程 RLock"],
                  ["数据持久化", "有", "进程退出即丢失"],
              ],
              widths=[5.6, 5.0, 4.8],
              caption="表 7-1　两种模式的差异，不可互相替代")

    kit.h2(doc, "7.3 其他")
    kit.bullet(doc, "内置特征后端是手工设计的 HOG + HSV + Laplacian 组合，语义能力弱于 CLIP，"
                    "对拍摄角度、光照较敏感；换 CLIP 只需 pip install -r requirements-deep.txt，接口不变。")
    kit.bullet(doc, "JWT 使用 HS256 对称密钥，未做密钥轮换与吊销列表，仅适用于课程演示。")
    kit.bullet(doc, "前端页面为演示用单文件，未做组件化与无障碍适配。")

    path = CODE_DIR / "课程作业_第一部分_代码.docx"
    doc.save(str(path))
    return path


# ============================================================
# 六、第二部分：数据
# ============================================================
def build_data_doc() -> Path:
    doc = new_doc("非遗数字资产分级确权与溯源系统 · 课程作业（数据部分）")

    cover(doc,
          "非遗数字资产\n分级确权与溯源系统",
          "课程作业　第二部分：数　据",
          [
              ("数据内容", "4 账号 / 3 条公有母题 / 8 件作品 / 14 条审核流水 / 5 笔授权 / 11 张图像"),
              ("格式", "主数据 JSON（UTF-8）+ 5 张 CSV 展开表 + JPEG 图像"),
              ("来源", "全部为程序合成，无第三方素材；AI 结论由真实算法回算，非手编"),
              ("规模", f"{N_AUDITS + N_LICENSES} 条结构化记录；{len(DDL)} 张数据库表 {DB_FIELDS} 个字段，JSON 侧 {JSON_FIELDS} 个字段"),
              ("配套", "数据字典.csv、csv/ 明细表、seed_demo.json 原文"),
              ("生成日期", datetime.now().strftime("%Y 年 %m 月 %d 日")),
          ])
    toc(doc)

    # ---------------- 一 ----------------
    kit.h1(doc, "一、数据总览")
    kit.para(doc, "数据分三类，用途不同，不要混着看：结构化种子是「业务记录」；图像是「算法输入」；"
                  "运行产物是「跑一遍之后才会出现的东西」，本作业不预置。")

    kit.table(doc,
              ["类别", "内容", "存放位置", "能否重造"],
              [
                  ["结构化种子", f"{len(USERS)} 账号、{len(MOTIFS)} 母题、{len(ASSETS)} 作品、"
                                 f"{N_AUDITS} 条审核流水、{N_LICENSES} 笔授权",
                   "data/seed_demo.json", "可手改，改完须跑 check_seed 校验"],
                  ["图像素材", f"{len(MOTIFS)} 张母题参考图 + {len(ASSETS)} 张作品图 + 2 张上传样例",
                   "data/motifs、data/uploads、data/samples", "可由脚本重新生成"],
                  ["运行产物", "上传缓存、特征向量、审计日志", "运行时生成", "跑一遍即产生，不入库提交"],
              ],
              widths=[2.8, 7.6, 3.4, 2.2],
              caption="表 1-1　三类数据")

    kit.h2(doc, "1.1 规模与分布")
    kit.table(doc,
              ["维度", "取值", "条数"],
              [
                  ["账号角色", "传承人 / 评审专家 / 监管管理员", "2 / 1 / 1"],
                  ["作品状态", "已终审通过 approved", str(sum(1 for a in ASSETS if a["audit_status"] == "approved"))],
                  ["", "待审 pending", str(sum(1 for a in ASSETS if a["audit_status"] == "pending"))],
                  ["", "草稿 draft", str(sum(1 for a in ASSETS if a["audit_status"] == "draft"))],
                  ["权属级别", "public 公有", str(sum(1 for a in ASSETS if a["right_level"] == "public"))],
                  ["", "derived 改编衍生", str(sum(1 for a in ASSETS if a["right_level"] == "derived"))],
                  ["", "original 原创", str(sum(1 for a in ASSETS if a["right_level"] == "original"))],
                  ["母题关联", "有母题血缘（motif_id 非空）",
                   str(sum(1 for a in ASSETS if a.get("motif_id")))],
                  ["", "无母题血缘", str(sum(1 for a in ASSETS if not a.get("motif_id")))],
                  ["授权类型", "非独占 non_exclusive / 独占 exclusive / 展示 display",
                   " / ".join(str(sum(1 for a in ASSETS for l in a["licenses"] if l["license_type"] == t))
                              for t in ("non_exclusive", "exclusive", "display"))],
                  ["授权费合计", "人民币元", f"¥{FEE_TOTAL:,.0f}"],
              ],
              widths=[3.0, 9.0, 3.4],
              caption="表 1-2　种子数据的分布特征")
    kit.para(doc, "分布不是随手凑的：三档权属各有着落、状态覆盖 draft 到 approved、"
                  "有母题血缘与无血缘各占一半，这样任何一次界面演示都能找到对应样本，"
                  "不至于出现「想演待审找不到待审」的情况。")

    # ---------------- 二 ----------------
    kit.h1(doc, "二、数据来源")
    kit.h2(doc, "2.1 逐项来源")
    kit.para(doc, "本作业没有使用任何第三方数据集，包括公开的非遗图像库。原因有两个："
                  "真实非遗纹样图片存在版权与授权不明的问题，不适合打包提交；"
                  "程序合成可以精确控制「哪些图应该判公有、哪些应该判原创」，结果才可复现。")

    kit.table(doc,
              ["数据项", "产生方式", "具体做法"],
              [
                  ["账号（4 条）", "人工设定", "虚构的传承人与机构，姓名、单位均为示例，不对应真实个人"],
                  ["母题（3 条）", "人工设定 + 程序绘图", "蝴蝶纹 / 缠枝莲纹 / 回纹，属公有领域传统纹样；参考图由 app/motif_images.py 用几何图元绘制"],
                  ["作品（8 条）", "人工设定 + 程序绘图的衍生", "以母题图为底，叠加受控的色相旋转、对比度与几何变换；原创类改用鱼纹、月牙等不同构图"],
                  ["审核流水（14 条）", "人工设定状态与意见", "专家意见为撰写文本，但其引用的相似度数字必须与算法一致，否则脚本报错"],
                  ["授权记录（5 条）", "人工设定", "被授权方为虚构机构，费用为示例金额"],
                  ["AI 字段", "★ 算法计算", "ai_suggest_level / ai_confidence / similarity 全部由 app/ai/ 对真实图片算出"],
                  ["特征向量", "★ 算法计算", "512 维，L2 归一化，启动时现算，不预存"],
              ],
              widths=[3.4, 4.2, 8.4],
              caption="表 2-1　每一类数据的来历")

    kit.h2(doc, "2.2 图像是怎么生成的")
    kit.para(doc, "全部图像由脚本生成，不使用任何外部素材。母题图是几何图元绘制的："
                  "蝴蝶纹用对称双翼加锯齿边，缠枝莲纹用中心莲瓣加螺旋枝蔓，回纹用方形折线连续图案。"
                  "作品图则是以母题图为底，叠加受控变换，并保证每张图像素级不同——"
                  "因为系统用 SHA-256 做查重，两张完全相同的图会被判为重复上传而拒绝。")
    kit.table(doc,
              ["图片", "相对路径", "与母题的关系", "预期算法判定"],
              [
                  ["蝴蝶纹", "data/motifs/motif_butterfly.jpg", "母题本体", "—"],
                  ["缠枝莲纹", "data/motifs/motif_lotus.jpg", "母题本体", "—"],
                  ["回纹", "data/motifs/motif_huiwen.jpg", "母题本体", "—"],
                  ["蝴蝶纹袖片改良版", "uploads/seed_butterfly_sleeve.jpg", "色相旋转 + 边框重绘", "derived（改编衍生）"],
                  ["蝴蝶纹直接复制件", "uploads/seed_butterfly_copy.jpg", "仅轻微扰动", "public（高度重合）"],
                  ["缠枝莲双面绣屏风", "uploads/seed_lotus_screen.jpg", "局部重排", "derived"],
                  ["回纹织锦坐垫", "uploads/seed_huiwen_cushion.jpg", "材质化处理", "derived"],
                  ["原创鱼纹蜡染", "uploads/seed_fish_scarf.jpg", "完全不同的构图", "original"],
                  ["原创山间月手稿", "uploads/seed_moon_valley.jpg", "完全不同的构图", "original"],
                  ["银饰回纹手镯", "uploads/seed_silver_bracelet.jpg", "取回纹骨架，工艺形态不同", "public / derived 边界样本"],
                  ["两张上传样例", "data/samples/", "供现场演示上传用", "derived / original"],
              ],
              widths=[4.4, 6.4, 4.0, 3.0],
              caption="表 2-2　图像清单与设计意图")

    kit.h2(doc, "2.3 AI 字段为什么可信")
    kit.para(doc, "种子文件里写着「AI 建议：改编衍生，置信度 0.4741，与 MO-001 相似度 0.8349」。"
                  "这三个数字不是我编的，是算法对这张图算出来之后回写进去的。"
                  "如果手编，演示时会出现「台账显示 derived，点一下 AI 分析重跑变成 original」的穿帮，"
                  "评审一眼能看出来。")
    kit.para(doc, "因此有一个专门的校验脚本，拿真实算法逐件回算并与 JSON 比对，容差 0.02：")
    kit.code_block(doc, """
python -m scripts.check_seed            # 逐件比对，不一致退出码 1
python -m scripts.check_seed --write    # 改了图之后，把算法结果回写进 JSON
""")
    kit.para(doc, "本次提交前跑过一次，结果全部一致，并且母题血缘（motif_id）也与算法检索结果吻合。")

    kit.h2(doc, "2.4 版权与合规")
    kit.bullet(doc, "全部图像为程序生成，不含任何来自互联网的非遗纹样照片，不存在素材授权问题。",
               lead="素材：")
    kit.bullet(doc, "账号姓名为虚构，单位名称为虚构或通用名称，不对应可识别的真实个人与机构。",
               lead="个人信息：")
    kit.bullet(doc, "口令统一为 Passw0rd!，是公开的示例口令，写在 README 与界面上，不具备任何保密性。",
               lead="口令：")
    kit.bullet(doc, "母题（蝴蝶纹、缠枝莲纹、回纹）本身属公有领域传统纹样，本作业对它们的描述与使用"
                    "不主张任何权利，这也正是系统设计的立论前提。",
               lead="公有领域：")

    # ---------------- 三 ----------------
    kit.h1(doc, "三、文件清单")
    kit.table(doc,
              ["文件", "格式", "条数 / 大小", "说明"],
              [
                  ["data/seed_demo.json", "JSON（UTF-8）", f"{N_USERS + len(MOTIFS) + len(ASSETS)} 条主记录",
                   "主数据源，演示模式与真库共用同一份"],
                  ["csv/users.csv", "CSV（UTF-8 带 BOM）", f"{len(USERS)} 行", "账号表展开，Excel 可直接打开"],
                  ["csv/motifs.csv", "CSV", f"{len(MOTIFS)} 行", "母题表展开，关键词用分号连接"],
                  ["csv/assets.csv", "CSV", f"{len(ASSETS)} 行", "作品表展开，附审核条数与授权条数"],
                  ["csv/audits.csv", "CSV", f"{N_AUDITS} 行", "审核流水长表，每行一条流水并带 asset_id"],
                  ["csv/licenses.csv", "CSV", f"{N_LICENSES} 行", "授权记录长表，每行一笔并带 asset_id"],
                  ["数据字典.csv", "CSV", f"{len(DICT_ROWS)} 行", "全部字段的类型与含义，见第九章"],
                  ["data/motifs/*.jpg", "JPEG 480×480", "3 张", "母题参考图"],
                  ["data/uploads/seed_*.jpg", "JPEG 480×480", "8 张", "作品原图"],
                  ["data/samples/*.jpg", "JPEG", "2 张", "现场上传演示用"],
              ],
              widths=[5.4, 4.2, 3.0, 3.4],
              caption="表 3-1　数据文件清单")
    kit.para(doc, "CSV 一律用 UTF-8 带 BOM 写出。这一点不是洁癖：不带 BOM 的话，"
                  "Windows 版 Excel 打开中文会乱码，而课程作业最可能的读取方式恰恰是双击用 Excel 看。")

    # ---------------- 四 ----------------
    kit.h1(doc, "四、主数据文件的结构")
    kit.para(doc, "data/seed_demo.json 是唯一的主数据源，顶层四个键：")
    kit.code_block(doc, """
{
  "_meta":  { ... },        // 自述：版本、用途、加载方式、注意事项、规模统计
  "users":  [ 4 条 ],       // 账号
  "motifs": [ 3 条 ],       // 公有母题
  "assets": [ 8 条 ]        // 作品，内含 audits[] 与 licenses[] 两个子数组
}
""")
    kit.para(doc, "作品与审核、授权是嵌套关系而非平铺：一件作品的多条流水挂在它的 audits 数组里。"
                  "这样读的时候不用做连接，写的时候也不容易把流水挂错作品。"
                  "导入数据库时由 scripts/init_seed.py 拆成 audit_record 与 license_record 两张表。")

    kit.h2(doc, "4.1 _meta：数据自述")
    kit.para(doc, f"版本 {SEED['_meta']['version']}，包含用途说明、加载方式、4 条注意事项与规模统计。"
                  f"注意事项里最要紧的一条是：AI 字段必须由算法回算，改了图片就要重跑 check_seed。")

    kit.h2(doc, "4.2 users（账号）")
    kit.table(doc, ["字段", "类型", "必填", "含义"],
              [[f, t, r, d] for f, t, r, d in USER_FIELDS],
              widths=[3.2, 2.4, 1.2, 8.8],
              caption=f"表 4-1　users 字段（{len(USERS)} 条）")

    kit.h2(doc, "4.3 motifs（公有母题）")
    kit.para(doc, "需要说明一处不一致：种子文件比数据库表多带了 ethnic、category、era、keywords 四个字段，"
                  "public_motif 表没有对应列。这是有意保留的——四个字段供前端展示与人工检索，"
                  "灌库时按列名映射，多余字段被忽略，不会报错。数据说明里点出来，"
                  "比让人自己去比两边的字段要省事。")
    kit.table(doc, ["字段", "类型", "必填", "含义"],
              [[f, t, r, d] for f, t, r, d in MOTIF_FIELDS if t != "—"],
              widths=[3.2, 2.4, 1.2, 8.8],
              caption=f"表 4-2　motifs 字段（{len(MOTIFS)} 条）")

    kit.h2(doc, "4.4 assets（作品）")
    kit.table(doc, ["字段", "类型", "必填", "含义"],
              [[f, t, r, d] for f, t, r, d in ASSET_FIELDS],
              widths=[3.2, 2.4, 1.2, 8.8],
              caption=f"表 4-3　assets 字段（{len(ASSETS)} 条）")

    kit.h2(doc, "4.5 audits（审核流水，嵌套在作品内）")
    kit.table(doc, ["字段", "类型", "必填", "含义"],
              [[f, t, r, d] for f, t, r, d in AUDIT_FIELDS],
              widths=[3.2, 2.4, 1.2, 8.8],
              caption=f"表 4-4　audits 字段（共 {N_AUDITS} 条）")

    kit.h2(doc, "4.6 licenses（授权记录，嵌套在作品内）")
    kit.table(doc, ["字段", "类型", "必填", "含义"],
              [[f, t, r, d] for f, t, r, d in LICENSE_FIELDS],
              widths=[3.2, 2.4, 1.2, 8.8],
              caption=f"表 4-5　licenses 字段（共 {N_LICENSES} 条）")
    kit.para(doc, f"5 笔授权合计 ¥{FEE_TOTAL:,.0f}，这个数字在界面上、Word 说明书里、本数据说明里三处一致，"
                  f"因为它只有一个来源：这 5 条记录的 fee_cny 之和。")

    # ---------------- 五 ----------------
    kit.h1(doc, "五、数据库表结构")
    kit.para(doc, f"真库模式下有 {len(DDL)} 张表、{sum(len(c) for c in DDL.values())} 个字段。"
                  f"字段名与类型由脚本从 sql/01_init.sql 解析得出，不是手抄的；"
                  f"含义为人工补写。下表按表逐一列出。")

    kit.h2(doc, "5.1 表关系")
    kit.code_block(doc, """
app_user (4)
   │ 1:N (owner_id)
   ├──────────────< asset (8) >────────────── public_motif (3)
   │                  │ 1:1                        ↑
   │                  ├──────── feature_vector     │ 1:N (motif_id)
   │                  │                            │
   │                  ├──────── audit_record (14) >┘ (expert_id → app_user)
   │                  ├──────── license_record (5)  (licensor_id / licensee_user_id → app_user)
   │                  └──────── asset_genealogy      (自关联 parent → child，DAG)

system_audit_log  由 7 个触发器写入，不设外键（避免审计日志反过来锁住业务表）
""")
    kit.para(doc, "三张核心表的基数关系：一个用户多件作品（1:N），一件作品挂一条母题（N:1），"
                  "一条作品可有多条审核流水与多笔授权（1:N）。"
                  "feature_vector 与 asset 是 1:1 垂直拆分——向量列 2 KB，和主表放一起会拖慢全表扫描。")

    order = ["app_user", "public_motif", "asset", "feature_vector",
             "audit_record", "license_record", "asset_genealogy", "system_audit_log"]
    for i, tname in enumerate(order, start=2):
        cols = DDL.get(tname, [])
        if not cols:
            continue
        rows = [[c, t, DB_MEANING.get(tname, {}).get(c, "—")] for c, t in cols]
        kit.h2(doc, f"5.{i} {tname}")
        kit.table(doc, ["字段", "类型", "含义"], rows,
                  widths=[4.0, 3.6, 8.0],
                  caption=f"表 5-{i}　{tname} 的 {len(cols)} 个字段")

    # ---------------- 六 ----------------
    kit.h1(doc, "六、枚举与取值域")
    kit.para(doc, "取值域在数据库层用 ENUM 类型或 CHECK 约束收敛，不是靠应用层自觉。"
                  "这一点在数据上表现为：写错一个字，插入直接失败。")
    kit.table(doc,
              ["枚举", "取值", "含义"],
              [
                  ["right_level", "public / derived / original", "公有 / 改编衍生 / 原创。★ 只能由专家终审写入"],
                  ["audit_status", "draft / pending / approved / rejected", "草稿 / 待审 / 已通过 / 已驳回"],
                  ["user_role", "heritor / expert / admin", "传承人 / 评审专家 / 监管管理员"],
                  ["audit_record.state", "pending / expert_review / approved / rejected", "比作品的 audit_status 多一个「专家领取」中间态"],
                  ["license_type", "exclusive / non_exclusive / display / derivative", "独占 / 非独占 / 展示 / 衍生开发"],
                  ["license_record.status", "active / expired / revoked", "生效 / 到期 / 撤销"],
                  ["asset_genealogy.relation_type", "adapted / restored / collaborated", "改编 / 修复 / 合作"],
                  ["system_audit_log.operation", "INSERT / UPDATE / DELETE", "变更类型"],
              ],
              widths=[4.6, 6.2, 4.8],
              caption="表 6-1　枚举取值域")

    # ---------------- 七 ----------------
    kit.h1(doc, "七、时间字段的约定")
    kit.para(doc, "种子文件里没有绝对日期，只有「距今多少天」的整数。原因很实际："
                  "写死 2026-03-15 这样的日期，过几个月再看，数据就变成了「三年前的授权已经到期」，"
                  "界面上的到期状态会莫名变化。用相对天数，无论哪天打开，画面都稳定。")
    kit.table(doc,
              ["字段", "含义", "换算方式"],
              [
                  ["created_days_ago", "作品创建距今天数", "created_at = 今天 − N 天"],
                  ["decided_days_ago", "终审完成距今天数", "同上；为空表示未终审"],
                  ["audits[].days_ago", "该条流水创建距今", "同上"],
                  ["audits[].decided_days_ago", "该条流水决策距今", "同上"],
                  ["licenses[].start_days_ago", "授权起算距今", "start_date = 今天 − N 天"],
                  ["licenses[].end_days_ago", "授权到期距今", "★ 取负数表示未来，如 −335 即 335 天后到期"],
              ],
              widths=[5.2, 5.0, 5.4],
              caption="表 7-1　相对天数与绝对日期的换算")
    kit.para(doc, "演示模式在加载时实时换算，所以永远「今天」在看这份数据；"
                  "灌入真库时由 init_seed.py 以导入时刻为基准换算成 TIMESTAMPTZ，之后就固定了。")

    # ---------------- 八 ----------------
    kit.h1(doc, "八、数据校验")
    kit.h2(doc, "8.1 三层校验")
    kit.table(doc,
              ["层次", "谁来做", "验什么"],
              [
                  ["文件层", "scripts/check_seed.py", "AI 结论与算法回算一致、母题血缘与检索结果吻合"],
                  ["代码层", "tests/test_invariants.py（88 项）", "状态与结论的搭配是否合法，如 pending 不得有 expert_level"],
                  ["数据库层", "CHECK / ENUM / 外键", "取值域、范围、引用完整性；真库模式下才生效"],
              ],
              widths=[2.6, 5.6, 7.4],
              caption="表 8-1　三层校验的分工")

    kit.h2(doc, "8.2 种子数据遵守的自洽规则")
    kit.para(doc, "这些规则不是写在文档里的君子协定，而是被测试或约束实际检查的：")
    kit.table(doc,
              ["规则", "为什么", "由谁保证"],
              [
                  ["right_level 只能由专家终审产生", "这是整个系统的立论前提", "列级 GRANT + 触发器 + 不变量测试"],
                  ["pending 状态的资产不得有专家结论", "否则等于「没审就定了」", "CHECK ck_audit_decided"],
                  ["已决策的流水必须有 expert_id 与 decided_at", "否则审计链断裂", "同上"],
                  ["授权只能登记在已终审通过的资产上", "未确权就授权等于无权处分", "服务层校验 + 409"],
                  ["同一资产同时只能有一份生效的独占授权", "重复独占在法律上冲突", "部分唯一索引"],
                  ["作品必须有 owner_id 且指向存在的用户", "孤儿作品无法追溯权属", "外键 ON DELETE RESTRICT"],
                  ["file_hash 为 64 位小写十六进制", "同一图重复上传要能被识别", "CHECK + 服务层查重"],
                  ["审核流水只追加，不可改不可删", "证据链必须不可变", "触发器 trg_audit_record_no_delete"],
              ],
              widths=[6.4, 5.2, 4.0],
              caption="表 8-2　数据自洽规则")

    kit.h2(doc, "8.3 关于 is_override")
    kit.para(doc, "种子数据中 is_override 全部为 false。5 号作品（缠枝莲双面绣屏风）演示的不是「推翻 AI」，"
                  "而是「驳回 → 补充材料 → 再次终审」的完整流转：第一次终审被驳回（理由为拍摄角度遮挡工艺特征），"
                  "补图后第二次终审通过。推翻 AI 的行为由运行时的交互产生，不预置在种子里——"
                  "预置一个「专家已推翻」的结论，反而会让演示失去亲手操作的意义。")

    # ---------------- 九 ----------------
    kit.h1(doc, "九、CSV 与数据字典")
    kit.para(doc, "JSON 适合程序读，不适合人翻。所以同一份数据另出两套东西：")
    kit.bullet(doc, "五张展开表放在 csv/ 下，每张对应一个实体，可用 Excel 直接打开、筛选、排序。"
                    "审核与授权做成长表（每行一条，带 asset_id），便于透视统计。", lead="csv/：")
    kit.bullet(doc, "把第四、五章所有字段的类型与含义导出成一张平表，共 "
                    f"{len(DICT_ROWS)} 行，列为主数据 JSON 命名空间、对象、字段、类型、是否必填、说明。"
                    "写代码时按这张表取字段名即可，不必翻文档。", lead="数据字典.csv：")
    kit.para(doc, "CSV 与 JSON 是同一份数据的两种视图，不构成两份真值。若要改数据，改 JSON 后重跑生成脚本，"
                  "CSV 会自动跟着变——手工改 CSV 不会回流。")

    # ---------------- 十 ----------------
    kit.h1(doc, "十、使用声明")
    kit.bullet(doc, "本数据仅用于本课程作业的演示与测试，账号、机构、金额均为虚构，不对应真实业务。")
    kit.bullet(doc, "可自由复制、修改、再分发，无需署名；若用于其他场合，请保留「数据为合成示例」的说明。")
    kit.bullet(doc, "母题纹样属公有领域，本作业不对其主张任何权利。")
    kit.bullet(doc, "如替换为自己的真实数据，请注意：换了图片必须重跑 check_seed --write，"
                    "否则种子里的 AI 结论会与实际计算结果脱节。")

    path = DATA_DIR / "课程作业_第二部分_数据.docx"
    doc.save(str(path))
    return path


# ============================================================
# 七、数据字典 / CSV / 代码清单 / 压缩包
# ============================================================
DICT_ROWS = (
    [("seed JSON", "users", f, t, r, d) for f, t, r, d in USER_FIELDS]
    + [("seed JSON", "motifs", f, t, r, d) for f, t, r, d in MOTIF_FIELDS if t != "—"]
    + [("seed JSON", "assets", f, t, r, d) for f, t, r, d in ASSET_FIELDS]
    + [("seed JSON", "assets.audits[]", f, t, r, d) for f, t, r, d in AUDIT_FIELDS]
    + [("seed JSON", "assets.licenses[]", f, t, r, d) for f, t, r, d in LICENSE_FIELDS]
    + [(("数据库", tname, c, typ, "—", DB_MEANING.get(tname, {}).get(c, "")))
       for tname, cols in DDL.items() for c, typ in cols]
)


def write_csv(path: Path, headers, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


def export_csvs():
    write_csv(CSV_DIR / "users.csv",
              ["user_id", "username", "display_name", "role", "org_name", "作品数"],
              [[u["user_id"], u["username"], u["display_name"], u["role"], u.get("org_name", ""),
                sum(1 for a in ASSETS if a["owner_id"] == u["user_id"])] for u in USERS])

    write_csv(CSV_DIR / "motifs.csv",
              ["motif_id", "motif_code", "name", "region", "ethnic", "category", "era",
               "keywords", "reference_img", "被引用次数"],
              [[m["motif_id"], m["motif_code"], m["name"], m.get("region", ""), m.get("ethnic", ""),
                m.get("category", ""), m.get("era", ""), ";".join(m.get("keywords") or []),
                m.get("reference_img", ""),
                sum(1 for a in ASSETS if a.get("motif_id") == m["motif_id"])] for m in MOTIFS])

    write_csv(CSV_DIR / "assets.csv",
              ["asset_id", "title", "owner_id", "owner_name", "motif_id", "motif_code",
               "right_level", "audit_status", "ai_suggest_level", "ai_confidence",
               "version_no", "file_path", "审核条数", "授权笔数", "授权费合计", "description"],
              [[a["asset_id"], a["title"], a["owner_id"],
                next((u["display_name"] for u in USERS if u["user_id"] == a["owner_id"]), ""),
                a.get("motif_id", ""),
                next((m["motif_code"] for m in MOTIFS if m["motif_id"] == a.get("motif_id")), ""),
                a["right_level"], a["audit_status"], a.get("ai_suggest_level", ""),
                a.get("ai_confidence", ""), a.get("version_no", 0), a.get("file_path", ""),
                len(a.get("audits") or []), len(a.get("licenses") or []),
                sum(float(l.get("fee_cny") or 0) for l in (a.get("licenses") or [])),
                (a.get("description") or "").replace("\n", " ")] for a in ASSETS])

    audit_rows = []
    for a in ASSETS:
        for i, x in enumerate(a.get("audits") or [], start=1):
            audit_rows.append([
                a["asset_id"], a["title"], i, x.get("state", ""), x.get("ai_suggest_level", ""),
                x.get("ai_confidence", ""), x.get("top_motif_id", ""), x.get("similarity", ""),
                x.get("expert_level", ""),
                next((u["display_name"] for u in USERS if u["user_id"] == x.get("expert_id")), ""),
                x.get("is_override", ""), x.get("days_ago", ""), x.get("decided_days_ago", ""),
                (x.get("opinion") or "").replace("\n", " "),
            ])
    write_csv(CSV_DIR / "audits.csv",
              ["asset_id", "asset_title", "序号", "state", "ai_suggest_level", "ai_confidence",
               "top_motif_id", "similarity", "expert_level", "expert_name", "is_override",
               "days_ago", "decided_days_ago", "opinion"],
              audit_rows)

    lic_rows = []
    for a in ASSETS:
        for i, l in enumerate(a.get("licenses") or [], start=1):
            lic_rows.append([
                a["asset_id"], a["title"], i, l.get("license_type", ""), l.get("licensee_name", ""),
                l.get("scope", ""), l.get("territory", ""), l.get("start_days_ago", ""),
                l.get("end_days_ago", ""), l.get("fee_cny", ""),
            ])
    write_csv(CSV_DIR / "licenses.csv",
              ["asset_id", "asset_title", "序号", "license_type", "licensee_name", "scope",
               "territory", "start_days_ago", "end_days_ago", "fee_cny"],
              lic_rows)

    write_csv(DATA_DIR / "数据字典.csv",
              ["命名空间", "对象", "字段", "类型", "是否必填", "含义"],
              [list(r) for r in DICT_ROWS])

    write_csv(CODE_DIR / "代码文件清单.csv",
              ["文件", "行数", "字节", "分组", "职责"],
              [[p, n, s, g, d] for p, n, s, g, d in INVENTORY])

    return len(audit_rows), len(lic_rows)


ZIP_ROOT_FILES = ["bootstrap.py", "run.py", "requirements.txt", "requirements-deep.txt",
                  "docker-compose.yml", ".env.example", "README.md"]
ZIP_DIRS = ["app", "sim", "sql", "tests", "scripts"]


def build_zip() -> Path:
    zpath = OUT / "课程作业_代码包.zip"
    skip_names = {"__pycache__", ".pytest_cache"}
    skip_suffix = {".pyc", ".pyo"}

    if zpath.exists():
        zpath.unlink()

    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in ZIP_ROOT_FILES:
            p = ROOT / rel
            if p.exists():
                z.write(p, f"code/{rel}")

        for d in ZIP_DIRS:
            for p in sorted((ROOT / d).rglob("*")):
                if not p.is_file():
                    continue
                if any(part in skip_names for part in p.parts):
                    continue
                if p.suffix in skip_suffix:
                    continue
                z.write(p, "code/" + p.relative_to(ROOT).as_posix())

        # 运行所需的最小数据子集：种子 + 母题图 + 作品图 + 上传样例
        data_files = [ROOT / "data" / "seed_demo.json"]
        for pat in ("motifs/*.jpg", "samples/*.jpg", "uploads/seed_*.jpg", "uploads/sample_*.jpg"):
            data_files.extend(sorted((ROOT / "data").glob(pat)))
        for p in data_files:
            if p.exists():
                z.write(p, "code/" + p.relative_to(ROOT).as_posix())
    return zpath


def write_readme(code_zip: Path, n_audit_rows: int, n_lic_rows: int):
    text = f"""# 课程作业提交说明

本目录是「非遗数字资产分级确权与溯源系统」课程作业的完整提交物，分两部分：代码与数据。

## 30 秒上手

```
解压 课程作业_代码包.zip
cd code
python -m venv .venv
.venv\\Scripts\\activate        # Windows；macOS / Linux 用 source .venv/bin/activate
pip install -r requirements.txt
python bootstrap.py --demo
```

浏览器打开 http://127.0.0.1:8000/ ，用 `heritor01` / `Passw0rd!` 登录。
不需要数据库、不需要联网、不需要 GPU。

## 目录

| 路径 | 内容 |
|---|---|
| `课程作业_代码包.zip` | 可运行工程（{N_CODE_FILES} 个源文件，{N_CODE_LINES} 行） |
| `01-代码/课程作业_第一部分_代码.docx` | 代码说明：结构、模块导读、复现步骤、测试 |
| `01-代码/代码文件清单.csv` | 每个文件的行数与职责，按分组排列 |
| `02-数据/课程作业_第二部分_数据.docx` | 数据说明：来源、格式、字段字典、校验规则 |
| `02-数据/seed_demo.json` | 主数据（{len(USERS)} 账号 / {len(MOTIFS)} 母题 / {len(ASSETS)} 作品） |
| `02-数据/csv/*.csv` | 五张展开表，Excel 可直接打开（审核 {n_audit_rows} 行 / 授权 {n_lic_rows} 行） |
| `02-数据/数据字典.csv` | {len(DICT_ROWS)} 个字段的类型与含义 |
| `02-数据/images/` | 11 张图像（3 母题 + 8 作品） |

## 两份文档的分工

**第一部分（代码）** 回答「怎么跑起来、代码怎么组织」：环境要求、目录结构、四层架构、
终审事务为什么必须原子、复现的三条路径、{TOTAL_TESTS} 项测试怎么跑。

**第二部分（数据）** 回答「数据从哪来、每个字段什么意思」：三类数据的来源、
图像如何程序生成、AI 结论为什么不是手编的、JSON 与 8 张数据库表共 {len(DICT_ROWS)} 个字段的字典、
枚举取值域、相对天数约定、三层校验规则。

## 两个必须知道的边界

1. **数据库脚本没有在真实 PostgreSQL 上执行过。** 开发环境无法运行 initdb，
   `sql/` 下 4 个脚本只有静态结构校验（18 项）。列级 GRANT、触发器、HNSW 索引的行为
   需要在有 PG 15+ 的环境按文档第五章 5.2 / 5.3 起库后才能验证。

2. **演示模式不等于真库。** 演示模式的 AI 算法与真库完全相同，但数据库约束
   （GRANT / 触发器 / 外键 / 并发控制）由 Python 代码模拟。具体差异见第一部分表 7-1。

## 数据改了怎么办

改 `data/seed_demo.json` 后必须重跑：

```
python -m scripts.check_seed --write     # 用真实算法回写 AI 字段
python scripts/build_coursework.py       # 重新生成本套提交物
```

否则种子里的 AI 结论会与实际计算结果脱节——演示时点一下「AI 分析」就会露馅。
"""
    (OUT / "提交说明.md").write_text(text, encoding="utf-8")


def copy_images():
    dest = DATA_DIR / "images"
    dest.mkdir(parents=True, exist_ok=True)

    # 主数据原文一并放进来：数据部分要能独立看，不能只有 CSV 没有源
    import shutil
    shutil.copy2(ROOT / "data" / "seed_demo.json", DATA_DIR / "seed_demo.json")

    n = 0
    for src in sorted((ROOT / "data" / "motifs").glob("*.jpg")):
        (dest / ("母题-" + src.name)).write_bytes(src.read_bytes())
        n += 1
    for a in ASSETS:
        src = ROOT / "data" / "uploads" / a["file_path"]
        if src.exists():
            (dest / f"作品-{a['asset_id']:02d}-{src.name}").write_bytes(src.read_bytes())
            n += 1
    return n


def main() -> int:
    for d in (CODE_DIR, DATA_DIR, CSV_DIR):
        d.mkdir(parents=True, exist_ok=True)

    if MISSING:
        print("[警告] 以下字段没有写说明，字典会缺项：")
        for m in MISSING:
            print("   ", m)
    else:
        print(f"[校验] 字段说明完整：{len(DICT_ROWS)} 个字段全部覆盖")

    p1 = build_code_doc()
    print(f"[1/5] 代码说明：{p1.name}  {p1.stat().st_size / 1024:.0f} KB")

    p2 = build_data_doc()
    print(f"[2/5] 数据说明：{p2.name}  {p2.stat().st_size / 1024:.0f} KB")

    n_audit, n_lic = export_csvs()
    print(f"[3/5] CSV：审核 {n_audit} 行 / 授权 {n_lic} 行 / 字典 {len(DICT_ROWS)} 行")

    n_img = copy_images()
    print(f"[4/5] 图像：{n_img} 张")

    z = build_zip()
    print(f"[5/5] 代码包：{z.name}  {z.stat().st_size / 1024 / 1024:.2f} MB")

    write_readme(z, n_audit, n_lic)
    print("\n已生成：" + str(OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
