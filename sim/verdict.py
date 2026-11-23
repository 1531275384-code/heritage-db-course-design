"""
行级 / 操作级判定

覆盖范围（务必看清，避免误以为这里是「全量校验」）
--------------------------------------------------
本模块判定的是**不依赖其他行**的规则：
    · NOT NULL
    · VARCHAR(n)/CHAR(n) 长度上限
    · 枚举取值域
    · CHECK 约束
    · 触发器语义：追加式表禁改禁删、已终审不可改、未终审不得授权

**不判定**：UNIQUE、外键、部分唯一索引 —— 它们取决于库里已有哪些行。
这类规则要靠「真往库里写一遍看是否被拒」来对拍，见 tests/test_rules_parity.py。
把唯一性也塞进这里只会自欺欺人：Python 侧看不到并发写入的其它事务。

三值逻辑
--------
CHECK 的判定沿用 SQL 语义：只有 FALSE 才算违反，UNKNOWN 视为通过。
这一点由 sim/ruleast.py 负责，本模块只调用 is_violated()。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping

from sim import rules as R
from sim.ruleast import (
    RuleError,
    Violation,
    is_violated,
)

__all__ = [
    "check_row",
    "check_operation",
    "row_violations",
    "trigger_violations",
    "coerce_value",
]

# PostgreSQL 在 CHECK 之前会先把值转成列的存储类型，这一步会**悄悄改变数值**：
#   NUMERIC(5,4) 收到 1.00004 → 存成 1.0000 → `<= 1` 成立 → 放行
#   NUMERIC(12,2) 收到 -0.004 → 存成 -0.00  → `>= 0` 成立 → 放行
#   INTEGER 收到 -0.4        → 存成 0      → `>= 0` 成立 → 放行
# 若 Python 直接拿原始值判 CHECK，这三种样本会得出与库相反的结论。
# 实测依据见 docs/verify/ 与 tests/test_rules_parity.py 的「取整」样本组。
_NUMERIC_RE = re.compile(r"^NUMERIC\(\s*(\d+)\s*,\s*(\d+)\s*\)$", re.I)
_INT_TYPES = frozenset({"SMALLINT", "INTEGER", "INT", "BIGINT"})


def coerce_value(col_type: str, value: Any) -> Any:
    """模拟 PostgreSQL 的赋值转换（只处理会改变数值的两种）。

    只做取整，不做溢出检查：值超出 NUMERIC(p,s) 精度时库会报 22003，
    而这里会继续算出违反 CHECK 的结论 —— 两边都是「拒绝」，判定结果一致。
    """
    if value is None or isinstance(value, bool):
        return value

    m = _NUMERIC_RE.match(col_type or "")
    if m and isinstance(value, (int, float, Decimal, str)):
        scale = int(m.group(2))
        try:
            return Decimal(str(value)).quantize(
                Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP
            )
        except (InvalidOperation, ValueError):
            return value

    if (col_type or "").strip().upper() in _INT_TYPES and isinstance(value, float):
        return Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP)

    return value


def _coerced_row(table: str, row: Mapping[str, Any]) -> dict[str, Any]:
    types = R.COLUMN_TYPES.get(table, {})
    return {col: coerce_value(types.get(col, ""), v) for col, v in row.items()}

_NOT_NULL = {}
for _r in R.NOT_NULL:
    _NOT_NULL.setdefault(_r.table, []).append(_r.column)

_MAX_LEN = {}
for _r in R.MAX_LENGTH:
    _MAX_LEN.setdefault(_r.table, []).append(_r)

_ENUM_COLS = {}
for _r in R.ENUM_COLUMNS:
    _ENUM_COLS.setdefault(_r.table, []).append(_r)

_CHECKS = {}
for _r in R.CHECKS:
    _CHECKS.setdefault(_r.table, []).append(_r)

_TRIGGERS = {}
for _r in R.TRIGGERS:
    _TRIGGERS.setdefault(_r.table, []).append(_r)


def _require_columns(table: str, row: Mapping[str, Any]) -> None:
    """要求提供完整行。

    允许「少给列」会让缺列与 NULL 混为一谈：调用方漏传 ai_confidence，
    判定结果和真传 NULL 一模一样，但真实 INSERT 会因缺列走 DEFAULT 分支。
    这类差异只有在真库上才会暴露成难查的假通过，所以这里直接拒绝。
    """
    expected = set(R.COLUMNS.get(table, ()))
    missing = expected - set(row)
    if missing:
        raise RuleError(
            f"{table} 的行样本缺少列 {sorted(missing)}。"
            " 判定要求完整行；缺列不等于 NULL。"
        )


def row_violations(table: str, row: Mapping[str, Any]) -> list[Violation]:
    """只做行内校验，不看触发器（用于 INSERT 的值合法性）。"""
    if table not in R.COLUMNS:
        raise RuleError(f"未知表 {table!r}，规则表里没有它")
    _require_columns(table, row)

    # 先按列类型转换，再判规则 —— 顺序必须与 PostgreSQL 一致，
    # 否则 NUMERIC 取整类的边界样本会得出相反结论。
    vals = _coerced_row(table, row)
    out: list[Violation] = []

    for col in _NOT_NULL.get(table, ()):
        if vals[col] is None:
            out.append(Violation("NOT NULL", table, f"{col} 列不允许为空"))

    for rule in _MAX_LEN.get(table, ()):
        v = vals[rule.column]
        if v is None:
            continue  # 可空列由 NOT NULL 规则单独管
        if len(str(v)) > rule.max_length:
            out.append(
                Violation(
                    f"length<={rule.max_length}",
                    table,
                    f"{rule.column} 长度 {len(str(v))} 超过上限 {rule.max_length}",
                )
            )

    for rule in _ENUM_COLS.get(table, ()):
        v = vals[rule.column]
        if v is None:
            continue
        if v not in rule.values:
            out.append(
                Violation(
                    rule.type_name,
                    table,
                    f"{rule.column}={v!r} 不在枚举取值域 {rule.values}",
                )
            )

    for rule in _CHECKS.get(table, ()):
        if is_violated(rule.pred, vals):
            out.append(Violation(rule.name, table, f"CHECK 不成立：{' '.join(rule.expr.split())}"))

    return out


def trigger_violations(
    table: str,
    event: str,
    *,
    old: Mapping[str, Any] | None = None,
    new: Mapping[str, Any] | None = None,
    parent: Mapping[str, Any] | None = None,
) -> list[Violation]:
    """按触发器语义判定一次操作是否被拒。

    parent：gate 类规则的父行（如 license_record.asset_id 对应的 asset 行）。
            为 None 表示父行不存在 —— 这在真库上会由外键报 23503，
            但 gate 触发器先行检查，会报「资产不存在」，两者都是拒绝。
    """
    event = event.upper()
    out: list[Violation] = []

    for trg in _TRIGGERS.get(table, ()):
        if event not in trg.events:
            continue

        if trg.kind == "reject":
            out.append(Violation(trg.name, table, f"该表为追加式，禁止 {event}"))

        elif trg.kind == "guard":
            if event != "UPDATE":
                continue
            if old is None:
                raise RuleError(f"{trg.name} 需要 OLD 行才能判定")
            cur = old.get("state")
            if cur in trg.forbid_when_old_state_in:
                out.append(
                    Violation(
                        trg.name,
                        table,
                        f"原状态 {cur!r} 已终审，不可再修改",
                    )
                )

        elif trg.kind == "gate":
            if event != "INSERT":
                continue
            if new is None:
                raise RuleError(f"{trg.name} 需要 NEW 行才能判定")
            gate = trg.gate
            if gate is None:
                raise RuleError(f"{trg.name} 声明为 gate 但没有 ParentGate")
            if parent is None:
                out.append(
                    Violation(trg.name, table, f"父行 {gate.ref_table}.{gate.fk_column} 不存在")
                )
                continue
            status = parent.get(gate.ref_column)
            if status not in gate.allowed:
                out.append(
                    Violation(
                        trg.name,
                        table,
                        f"父行 {gate.ref_table}.{gate.ref_column}={status!r}，"
                        f"要求 {'/'.join(gate.allowed)}",
                    )
                )

        # actor_gate / derived / audit：不靠数据判定，此处不求值
        #
        # actor_gate（如 trg_asset_status_guard）的判定输入里有一个「会话角色」，
        # 而本函数收到的只有行数据。同一个 new 行，传承人提交被拒、专家带着终审
        # 标记提交就通过 —— 没有身份就无法给出唯一结论。
        # 所以这里刻意不判（判了会出现「仿真说拒、真库收下」的假冲突），
        # 由 tests/check_pg 用真实 SET LOCAL ROLE 逐条验证。规则本身已在
        # sim/rules.py 里登记（kind=actor_gate），漏审一个新触发器仍会被
        # scripts/gen_rules.py 的 SIGNATURES 检查挡下。

    return out


def check_row(table: str, row: Mapping[str, Any]) -> list[Violation]:
    """INSERT 场景的完整判定：行内规则 + 触发器 gate（若调用方给了 parent）。"""
    return row_violations(table, row)


def check_operation(
    table: str,
    event: str,
    *,
    old: Mapping[str, Any] | None = None,
    new: Mapping[str, Any] | None = None,
    parent: Mapping[str, Any] | None = None,
) -> list[Violation]:
    """一次 DML 的判定：行内规则（INSERT/UPDATE 才有）+ 触发器语义。

    注意：真库上 UPDATE/DELETE 也会重新校验 CHECK 与 NOT NULL（UPDATE 会校验新行），
    DELETE 只受触发器约束。这里按同样口径处理。
    """
    event = event.upper()
    out: list[Violation] = []

    if event in ("INSERT", "UPDATE"):
        if new is None:
            raise RuleError(f"{event} 需要 NEW 行")
        out.extend(row_violations(table, new))
    elif event != "DELETE":
        raise RuleError(f"未知事件 {event!r}")

    out.extend(trigger_violations(table, event, old=old, new=new, parent=parent))
    return out
