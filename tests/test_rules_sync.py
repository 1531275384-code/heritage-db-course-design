"""规则单一真相的离线验证（不需要数据库）

要防的事
--------
「SQL 里写了 24 条 CHECK，Python 规则表里只有 22 条」——这种偏差不会报错，
只会让仿真内核悄悄放行真实系统会拒绝的写入。而且它一定发生在某次
「顺手改了下 SQL」之后，改的人不会有意识地同步另一个文件。

三道防线，本文件覆盖前两道
--------------------------
1. 生成器只解析不手抄：解析不了就抛异常，不允许跳过。
2. --check 漂移检测：SQL 改了没重新生成 → 本测试失败。
   （第三道是与真实库对账，见 scripts/gen_rules.py --verify-db 与
     tests/test_rules_parity.py，需要库才能跑。）

此外还验证求值语义本身：三值逻辑、边界值、触发器三类判定。
语义错了，同步做得再好也没意义 —— 一致地错。

用法：
    python -m tests.test_rules_sync
"""
from __future__ import annotations

import hashlib
import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim import rules as R  # noqa: E402
from sim.ruleast import RuleError, is_violated  # noqa: E402
from sim.verdict import check_operation, check_row  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}" + (f"  {extra}" if extra else ""))
    else:
        FAIL += 1
        print(f"  ✗ {label}  ← 失败  {extra}")


def section(t: str) -> None:
    print(f"\n{t}")


# =============================================================================
section("[1] 生成文件与 SQL 是否同步")

from scripts import gen_rules  # noqa: E402

inv = gen_rules.build_inventory()
rendered = gen_rules.render(inv)
on_disk = (ROOT / "sim" / "rules.py").read_text(encoding="utf-8")

check(
    "sim/rules.py 与当前 SQL 完全一致（漂移检测）",
    rendered == on_disk,
    "不一致时执行 python -m scripts.gen_rules",
)

import hashlib  # noqa: E402

for rel in R.SOURCE_FILES:
    actual = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()[:16]
    check(f"来源指纹 {rel}", R.SOURCE_SHA256.get(rel) == actual, actual)


# =============================================================================
section("[2] 覆盖完整性：SQL 里有的规则，规则表里必须有")

n_check = len(list(inv.checks()))
n_unique = len(list(inv.uniques())) + len(inv.partial_uniques)
n_fk = len(list(inv.fks()))
n_trg = len(inv.triggers)

check("CHECK 数量与 SQL 解析结果一致", len(R.CHECKS) == n_check, f"{len(R.CHECKS)} 条")
check("UNIQUE 数量一致（含部分唯一索引）", len(R.UNIQUE) == n_unique, f"{len(R.UNIQUE)} 条")
check("外键数量一致", len(R.FOREIGN_KEYS) == n_fk, f"{len(R.FOREIGN_KEYS)} 条")
check("触发器数量一致", len(R.TRIGGERS) == n_trg, f"{len(R.TRIGGERS)} 个")
check("CHECK 数量不为零（防空跑通过）", n_check >= 20, f"{n_check} 条")

# 每个约束名都必须能从 SQL 文本里找到，而不是凭空出现在规则表
sql_text = "\n".join(
    gen_rules.strip_comments((ROOT / rel).read_text(encoding="utf-8")) for rel in R.SOURCE_FILES
)
missing_in_sql = sorted(n for n in R.CONSTRAINT_NAMES if n not in sql_text)
check("规则表里的约束名都能在 SQL 中原样找到", not missing_in_sql, f"缺失 {missing_in_sql}")

# 反向：SQL 里的 CONSTRAINT 名都应该进规则表
ddl_names = set(re.findall(r"CONSTRAINT\s+(\w+)", sql_text))
undeclared = sorted(ddl_names - set(R.CONSTRAINT_NAMES))
check("SQL 里每个 CONSTRAINT 都进了规则表", not undeclared, f"漏掉 {undeclared}")

# 规则引用的表与列必须真实存在
bad_refs: list[str] = []
for c in R.CHECKS:
    if c.table not in R.COLUMNS:
        bad_refs.append(f"{c.name} 的表 {c.table}")
for nn in R.NOT_NULL:
    if nn.column not in R.COLUMN_TYPES.get(nn.table, {}):
        bad_refs.append(f"{nn.table}.{nn.column}")
check("所有规则引用的表/列都在 COLUMNS 中", not bad_refs, "; ".join(bad_refs))


# =============================================================================
section("[3] 触发器必须被显式分类（新增触发器不许静默溜进来）")

KINDS = {"reject", "gate", "guard", "actor_gate", "derived", "audit", "dag"}
bad_kind = [t.name for t in R.TRIGGERS if t.kind not in KINDS]
check("触发器类别都在已知集合内", not bad_kind, f"未知 {bad_kind}")

rejecting = [t for t in R.TRIGGERS if t.kind in ("reject", "gate", "guard")]
check("产生拒绝判定的触发器 ≥ 5 个", len(rejecting) >= 5, f"{len(rejecting)} 个")
check(
    "「未终审不得授权」的 gate 规则存在",
    any(t.kind == "gate" and t.table == "license_record" for t in R.TRIGGERS),
)
check(
    "「已终审不可改」的 guard 规则存在",
    any(t.kind == "guard" and t.table == "audit_record" for t in R.TRIGGERS),
)
check(
    "审计日志表禁止改删",
    any(t.table == "system_audit_log" and t.kind == "reject"
        and {"UPDATE", "DELETE"} <= set(t.events) for t in R.TRIGGERS),
)
check(
    "衍生谱系有 DAG 环检测触发器",
    any(t.table == "asset_genealogy" and t.kind == "dag" for t in R.TRIGGERS),
    "; ".join(t.name for t in R.TRIGGERS if t.table == "asset_genealogy"),
)

# 角色闸门：它的判定输入里有一个「会话角色」，不是纯数据规则，所以登记为
# actor_gate 而不进 REJECTING_TRIGGERS（否则对拍会出现"同样的数据时而收时而拒"）。
# 这里验的是"规则被登记且被抽全了"——角色、终态、标记名与取值，一个都不能少。
gate_rules = [t for t in R.TRIGGERS if t.kind == "actor_gate"]
check("「传承人不能自己定级」的 actor_gate 规则存在",
      any(t.table == "asset" for t in gate_rules), f"{[t.name for t in gate_rules]}")
if gate_rules:
    g = gate_rules[0]
    check("闸门抽到了受管角色（传承人/专家/监管）",
          {"ich_heritor", "ich_expert", "ich_admin"} <= set(g.actor_roles), f"{g.actor_roles}")
    check("闸门抽到了终态取值", set(g.final_states) >= {"approved", "rejected"}, f"{g.final_states}")
    check("闸门抽到了终审事务标记名", g.gate_marker == "ich.finalize_gate", f"{g.gate_marker}")
    check("标记允许的取值覆盖通过/驳回两条路径",
          {"approve_asset", "reject_asset"} <= set(g.gate_values), f"{g.gate_values}")
    check("actor_gate 不参与接受/拒绝对拍", g not in R.REJECTING_TRIGGERS)

# 解析器遇到不认识的写法必须抛错，不能跳过
try:
    gen_rules.parse_expr("now() > 0")
    loud = False
except Exception:
    loud = True
check("未登记的 SQL 函数会让提取器抛错（不静默跳过）", loud)

try:
    gen_rules.parse_expr("a IS DISTINCT FROM b")
    loud2 = False
except Exception:
    loud2 = True
check("不支持的操作符会让提取器抛错", loud2)


# =============================================================================
section("[4] CHECK 求值语义：三值逻辑与边界值")


def rule(name: str) -> R.CheckRule:
    for c in R.CHECKS:
        if c.name == name:
            return c
    raise AssertionError(f"规则 {name} 不存在")


# UNKNOWN 不算违反（SQL CHECK 只在 FALSE 时抛 23514）
p = rule("ck_lic_period").pred
check("end_date=NULL 时约束通过（UNKNOWN≠FALSE）",
      not is_violated(p, {"end_date": None, "start_date": "2026-01-01"}))
check("end_date < start_date 被拒", is_violated(p, {"end_date": "2025-01-01", "start_date": "2026-01-01"}))
check("end_date == start_date 通过", not is_violated(p, {"end_date": "2026-01-01", "start_date": "2026-01-01"}))

p = rule("ck_lic_self").pred
check("被授权方为空时通过", not is_violated(p, {"licensee_user_id": None, "licensor_id": 1}))
check("自己授权给自己被拒", is_violated(p, {"licensee_user_id": 1, "licensor_id": 1}))

p = rule("ck_asset_ai_pair").pred
check("建议与置信度同时为空 → 通过", not is_violated(p, {"ai_suggest_level": None, "ai_confidence": None}))
check("建议与置信度同时非空 → 通过", not is_violated(p, {"ai_suggest_level": "derived", "ai_confidence": 0.5}))
check("只有建议没有置信度 → 拒", is_violated(p, {"ai_suggest_level": "derived", "ai_confidence": None}))
check("只有置信度没有建议 → 拒", is_violated(p, {"ai_suggest_level": None, "ai_confidence": 0.5}))

p = rule("ck_asset_confidence").pred
check("置信度 0 通过", not is_violated(p, {"ai_confidence": 0}))
check("置信度 1 通过", not is_violated(p, {"ai_confidence": 1}))
check("置信度 1.0001 被拒", is_violated(p, {"ai_confidence": 1.0001}))
check("置信度 -0.1 被拒", is_violated(p, {"ai_confidence": -0.1}))

p = rule("ck_app_user_username_fmt").pred
check("用户名 2 字符被拒", is_violated(p, {"username": "ab"}))
check("用户名 3 字符通过", not is_violated(p, {"username": "abc"}))
check("用户名 64 字符通过", not is_violated(p, {"username": "a" * 64}))
check("用户名 65 字符被拒", is_violated(p, {"username": "a" * 65}))
check("用户名含连字符被拒", is_violated(p, {"username": "ab-c"}))
check("正则按搜索而非全串匹配（含 $ 时等价）", not is_violated(p, {"username": "a_1"}))

p = rule("ck_fv_model").pred
check("模型名全空白被拒", is_violated(p, {"model_name": "   ", "model_version": "1.0"}))
check("模型名正常通过", not is_violated(p, {"model_name": "clip", "model_version": "1.0"}))


# =============================================================================
section("[5] 触发器判定：拒绝 / 门禁 / 守卫")

lic_row = {
    "license_id": 1,
    "asset_id": 1001,
    "licensor_id": 2,
    "licensee_user_id": None,
    "licensee_name": "某公司",
    "license_type": "display",
    "scope": None,
    "territory": None,
    "start_date": "2026-01-01",
    "end_date": None,
    "fee_cny": 500,
    "contract_hash": None,
    "status": "active",
    "prev_license_id": None,
    "created_at": "2026-01-01",
}

v = check_operation("license_record", "INSERT", new=lic_row,
                    parent={"asset_id": 1001, "audit_status": "draft"})
check("未终审（draft）登记授权被拒", any(x.rule == "trg_license_require_approved" for x in v))
v = check_operation("license_record", "INSERT", new=lic_row,
                    parent={"asset_id": 1001, "audit_status": "rejected"})
check("已驳回（rejected）登记授权同样被拒", bool(v))
v = check_operation("license_record", "INSERT", new=lic_row,
                    parent={"asset_id": 1001, "audit_status": "approved"})
check("已终审（approved）登记授权放行", not v, str(v))
v = check_operation("license_record", "INSERT", new=lic_row, parent=None)
check("父资产不存在时被拒", bool(v))

v = check_operation("license_record", "DELETE",
                    old={"license_id": 1, "license_type": "display", "status": "active"})
check("授权记录禁止删除", any(x.rule == "trg_license_no_delete" for x in v))

audit_base = {
    "audit_id": 1,
    "asset_id": 1001,
    "expert_id": 2,
    "state": "approved",
    "ai_suggest_level": "derived",
    "ai_confidence": 0.82,
    "expert_level": "derived",
    "similarity_json": None,
    "opinion": "回纹与母题一致",
    "is_override": False,
    "created_at": "2026-01-01",
    "decided_at": "2026-01-02",
}
v = check_operation("audit_record", "UPDATE", old=audit_base,
                    new={**audit_base, "opinion": "改一下"})
check("已终审的审核记录不可再改", any(x.rule == "trg_audit_record_guard" for x in v))

pending = {
    "audit_id": 2,
    "asset_id": 1001,
    "expert_id": None,
    "state": "pending",
    "ai_suggest_level": "derived",
    "ai_confidence": 0.55,
    "expert_level": None,
    "similarity_json": None,
    "opinion": None,
    "is_override": False,
    "created_at": "2026-01-03",
    "decided_at": None,
}
v = check_operation("audit_record", "UPDATE", old=pending, new={**pending, "state": "expert_review"})
check("待审记录可迁移到专家评审", not v, str(v))

v = check_operation("audit_record", "DELETE", old=pending)
check("审核流水禁止删除", any(x.rule == "trg_audit_record_no_delete" for x in v))

log_row = {
    "log_id": 1,
    "table_name": "asset",
    "record_id": 1001,
    "operation": "UPDATE",
    "actor": "ich_expert",
    "old_snapshot": None,
    "new_snapshot": None,
    "changed_fields": ["audit_status"],
    "logged_at": "2026-01-01",
}
v = check_operation("system_audit_log", "UPDATE", old=log_row, new=log_row)
check("审计日志禁止修改", any(x.rule == "trg_syslog_immutable" for x in v))
v = check_operation("system_audit_log", "DELETE", old=log_row)
check("审计日志禁止删除", any(x.rule == "trg_syslog_immutable" for x in v))


# =============================================================================
section("[6] 行内判定：NOT NULL / 长度 / 枚举")

asset_row = {
    "asset_id": 1,
    "title": "回纹锦",
    "owner_id": 4,
    "motif_id": None,
    "file_path": "a.jpg",
    "file_hash": "a" * 64,
    "file_size": 1024,
    "mime_type": "image/jpeg",
    "right_level": "public",
    "audit_status": "draft",
    "ai_suggest_level": None,
    "ai_confidence": None,
    "ai_model_version": None,
    "version_no": 0,
    "description": None,
    "created_at": "2026-01-01",
    "updated_at": "2026-01-01",
}
check("合法资产行 0 违反", not check_row("asset", asset_row))
check("title 超 200 字符被拒",
      any(x.rule == "length<=200" for x in check_row("asset", {**asset_row, "title": "汉" * 201})))
check("title 恰好 200 字符通过", not check_row("asset", {**asset_row, "title": "汉" * 200}))
check("audit_status 非法取值被拒",
      any(x.rule == "audit_status_enum" for x in check_row("asset", {**asset_row, "audit_status": "done"})))


# --------------------------------------------------------- 列类型转换（取整）语义
# PostgreSQL 在 CHECK 之前会先把值转成列的存储类型，NUMERIC(p,s) 会四舍五入：
#   1.00004 → 1.0000 通过；-0.00004 → 0.0000 通过；-0.0001 → -0.0001 被拒
# Python 若拿原始值判 `<= 1`，结论与库相反。这四条是与真实库对拍时补出来的。
def _conf(v):
    return check_row("asset", {**asset_row, "ai_suggest_level": "derived", "ai_confidence": v})


check("置信度 1.00004 取整后通过", not _conf(Decimal("1.00004")))
check("置信度 1.0001 超出 4 位小数被拒",
      any(x.rule == "ck_asset_confidence" for x in _conf(Decimal("1.0001"))))
check("置信度 -0.00004 取整后通过", not _conf(Decimal("-0.00004")))
check("置信度 -0.0001 取整后仍为负，被拒",
      any(x.rule == "ck_asset_confidence" for x in _conf(Decimal("-0.0001"))))
check("版本号 -0.4 取整为 0 通过",
      not check_row("asset", {**asset_row, "version_no": -0.4}))

try:
    check_row("asset", {"asset_id": 1})
    strict = False
except RuleError:
    strict = True
check("残缺行会被拒绝（缺列 ≠ NULL）", strict)


# =============================================================================
print(f"\n{'=' * 70}")
print(f"  结果：{PASS} 项通过 / {FAIL} 项失败")
print(f"{'=' * 70}")
print("\n说明：本文件验证「规则表是否忠实反映 SQL」与「求值语义是否正确」。")
print("      与真实数据库的逐条对拍在 tests/test_rules_parity.py（需要 PostgreSQL）。")
sys.exit(0 if FAIL == 0 else 1)
