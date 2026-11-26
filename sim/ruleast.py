"""
规则表达式 AST 与求值器（手写，不参与代码生成）

为什么需要这一层
----------------
`sim/rules.py` 是从 SQL 自动生成的**数据**，它的节点类型必须由手写代码提供，
否则每次重新生成都会把节点定义一起覆盖掉，生成器就变成了「自己校验自己」。

语义对齐的关键：SQL 的 CHECK 约束是**三值逻辑**
---------------------------------------------
    CHECK 只在表达式求值为 FALSE 时抛 23514。
    求值为 UNKNOWN（即 NULL）时视为通过。

所以 `ai_confidence IS NULL OR (ai_confidence >= 0 AND ai_confidence <= 1)`
在 ai_confidence 为 NULL 时通过；而如果把它误实现成 Python 的
`None is None or None >= 0`，会直接抛 TypeError —— 这是最容易写错的地方。
本模块统一用 None 表示 UNKNOWN，并在 And/Or/Not/Cmp 里按 SQL 真值表传播。

    AND : 有 FALSE → FALSE；否则有 UNKNOWN → UNKNOWN；否则 TRUE
    OR  : 有 TRUE  → TRUE ；否则有 UNKNOWN → UNKNOWN；否则 FALSE
    NOT : UNKNOWN → UNKNOWN

对外只有两个函数是稳定接口：
    evaluate(node, row) -> True / False / None
    is_violated(node, row) -> bool        # 仅当结果为 False 才算违反
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

# UNKNOWN 用 None 表示，与 SQL NULL 一致
UNKNOWN = None


class RuleError(Exception):
    """规则本身有问题（不是数据违反规则）。"""


# =============================================================================
#  表达式节点
# =============================================================================


@dataclass(frozen=True)
class Node:
    """所有节点的基类，仅用于类型标注。"""


@dataclass(frozen=True)
class Col(Node):
    """列引用。name 为小写列名，不带表别名。"""

    name: str


@dataclass(frozen=True)
class Lit(Node):
    """字面量：数字、字符串、布尔。"""

    value: Any


@dataclass(frozen=True)
class Btrim(Node):
    """btrim(x)：去首尾空白。"""

    arg: Node


@dataclass(frozen=True)
class Length(Node):
    """length(x)：字符长度。"""

    arg: Node


@dataclass(frozen=True)
class VectorDims(Node):
    """vector_dims(x)：数组/向量维度。降级模式下即 len()。"""

    arg: Node


@dataclass(frozen=True)
class Regex(Node):
    """x ~ 'pattern'：PostgreSQL 的**搜索**语义，不是全串匹配。"""

    arg: Node
    pattern: str


@dataclass(frozen=True)
class Cmp(Node):
    """二元比较。op 取值 = <> != < <= > >=。"""

    left: Node
    op: str
    right: Node


@dataclass(frozen=True)
class In(Node):
    """x IN (...)。"""

    arg: Node
    values: tuple[Any, ...]


@dataclass(frozen=True)
class IsNull(Node):
    """x IS NULL / x IS NOT NULL。

    注意 is_null / is not null **永远不会**返回 UNKNOWN：
    即使 x 是 NULL，`x IS NULL` 也是 TRUE。
    """

    arg: Node
    negate: bool = False


@dataclass(frozen=True, init=False)
class And(Node):
    """逻辑与。构造时自动展平嵌套的 And（语义等价，减少生成代码的括号噪音）。"""

    children: tuple[Node, ...]

    def __init__(self, *children: Node):
        flat: list[Node] = []
        for ch in children:
            if isinstance(ch, And):
                flat.extend(ch.children)
            else:
                flat.append(ch)
        if not flat:
            raise RuleError("And 至少需要一个子表达式")
        object.__setattr__(self, "children", tuple(flat))


@dataclass(frozen=True, init=False)
class Or(Node):
    children: tuple[Node, ...]

    def __init__(self, *children: Node):
        flat: list[Node] = []
        for ch in children:
            if isinstance(ch, Or):
                flat.extend(ch.children)
            else:
                flat.append(ch)
        if not flat:
            raise RuleError("Or 至少需要一个子表达式")
        object.__setattr__(self, "children", tuple(flat))


@dataclass(frozen=True)
class Not(Node):
    child: Node


# =============================================================================
#  求值
# =============================================================================


def evaluate(node: Node, row: Mapping[str, Any]) -> Any:
    """按 SQL 三值逻辑求值。返回 True / False / None(UNKNOWN) 或具体值。"""
    if isinstance(node, Lit):
        return node.value

    if isinstance(node, Col):
        if node.name not in row:
            raise RuleError(
                f"行数据缺少列 {node.name!r}（现有列：{sorted(row)}）。"
                " 规则求值要求提供完整行，缺列说明样本构造有遗漏，不能当成 NULL。"
            )
        return row[node.name]

    if isinstance(node, Btrim):
        v = evaluate(node.arg, row)
        return UNKNOWN if v is None else str(v).strip()

    if isinstance(node, Length):
        v = evaluate(node.arg, row)
        return UNKNOWN if v is None else len(str(v))

    if isinstance(node, VectorDims):
        v = evaluate(node.arg, row)
        return UNKNOWN if v is None else len(v)

    if isinstance(node, Regex):
        v = evaluate(node.arg, row)
        if v is None:
            return UNKNOWN
        # PostgreSQL `~` 是「能否在串中搜索到」，等价于 re.search，不是 re.fullmatch
        return re.search(node.pattern, str(v)) is not None

    if isinstance(node, Cmp):
        a = evaluate(node.left, row)
        b = evaluate(node.right, row)
        if a is None or b is None:
            return UNKNOWN
        op = node.op
        try:
            if op == "=":
                return a == b
            if op in ("<>", "!="):
                return a != b
            if op == "<":
                return a < b
            if op == "<=":
                return a <= b
            if op == ">":
                return a > b
            if op == ">=":
                return a >= b
        except TypeError as exc:  # 类型不可比
            raise RuleError(f"无法比较 {a!r} 与 {b!r}：{exc}") from exc
        raise RuleError(f"未知比较运算符 {op!r}")

    if isinstance(node, In):
        v = evaluate(node.arg, row)
        if v is None:
            return UNKNOWN
        return v in node.values

    if isinstance(node, IsNull):
        v = evaluate(node.arg, row)
        return (v is not None) if node.negate else (v is None)

    if isinstance(node, And):
        saw_unknown = False
        for ch in node.children:
            v = evaluate(ch, row)
            if v is False:
                return False
            if v is None:
                saw_unknown = True
        return UNKNOWN if saw_unknown else True

    if isinstance(node, Or):
        saw_unknown = False
        for ch in node.children:
            v = evaluate(ch, row)
            if v is True:
                return True
            if v is None:
                saw_unknown = True
        return UNKNOWN if saw_unknown else False

    if isinstance(node, Not):
        v = evaluate(node.child, row)
        return UNKNOWN if v is None else (not v)

    raise RuleError(f"未知节点类型 {type(node).__name__}")


def is_violated(node: Node, row: Mapping[str, Any]) -> bool:
    """CHECK 约束是否被违反：**只有 FALSE 算违反**，UNKNOWN 视为通过。"""
    return evaluate(node, row) is False


# =============================================================================
#  规则载体
# =============================================================================


@dataclass(frozen=True)
class CheckRule:
    table: str
    name: str
    expr: str  # SQL 原文，差异报告时直接给出，便于人工核对
    pred: Node


@dataclass(frozen=True)
class NotNullRule:
    table: str
    column: str


@dataclass(frozen=True)
class EnumRule:
    """列使用自定义枚举类型：取值域由 CREATE TYPE 决定。"""

    table: str
    column: str
    type_name: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class MaxLengthRule:
    """VARCHAR(n) / CHAR(n) 的长度上限。

    这不是 CHECK 约束，违反时报 22001（value too long），很容易在规则盘点时漏掉：
    它只写在列类型里，不在任何 CONSTRAINT 里。CHAR(n) 还会把短值补空格。
    """

    table: str
    column: str
    max_length: int
    fixed: bool = False


@dataclass(frozen=True)
class UniqueRule:
    table: str
    name: str
    columns: tuple[str, ...]
    where: Node | None = None  # 部分唯一索引的 WHERE 子句
    index: bool = False  # True 表示来自 CREATE UNIQUE INDEX 而非表约束


@dataclass(frozen=True)
class ForeignKeyRule:
    table: str
    name: str
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]
    on_delete: str = "NO ACTION"
    on_update: str = "NO ACTION"


@dataclass(frozen=True)
class ParentGate:
    """写入前置条件：本表某列引用的父行，其某列必须落在允许集合内。

    对应 fn_license_require_approved()：license_record.asset_id → asset.audit_status
    必须为 'approved'。
    """

    fk_column: str
    ref_table: str
    ref_column: str
    allowed: tuple[str, ...]


@dataclass(frozen=True)
class TriggerRule:
    """触发器的**约束语义**。

    kind:
        reject      无条件拒绝某些事件（追加式表防篡改）
        gate        写入前检查父行状态（ParentGate）
        guard       依据 OLD 行状态拒绝本次修改
        actor_gate  依据数据库角色拒绝本次修改（同一行数据，换个角色结论就不同）
        derived     只改写 NEW 的派生列，不拒绝
        audit       只写审计日志，不拒绝
        dag         依据整张图的结构拒绝（如衍生谱系成环），需要遍历多行才能判定

    只有 reject / gate / guard 三类会产生「数据可判定的拒绝」，参与接受与否的比对。
    actor_gate 的判定输入里有一个身份（会话角色），样本只描述数据不描述身份，
    硬塞进同一套对拍会得到「同样的数据有时接受有时拒绝」的假冲突 —— 它由
    tests/check_pg 用真实 SET LOCAL ROLE 逐条验证，不在此处求值。
    derived / audit 不参与比对，但必须被显式分类，否则说明新触发器没人审过。
    dag 同理：判定需要一张图，而样本是单行，硬套只会得到假结论；
    它由 tests/test_pg_concurrency 用真实边集验证。
    """

    table: str
    name: str
    timing: str  # BEFORE / AFTER
    events: tuple[str, ...]
    function: str
    kind: str
    gate: ParentGate | None = None
    forbid_when_old_state_in: tuple[str, ...] = ()
    # actor_gate 专用：受管角色、被认作终态的取值、允许写终态的事务标记及取值
    actor_roles: tuple[str, ...] = ()
    final_states: tuple[str, ...] = ()
    gate_marker: str | None = None
    gate_values: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Violation:
    rule: str  # 规则名，如 ck_asset_file_size
    table: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - 仅用于报错可读性
        return f"[{self.rule}] {self.table}: {self.detail}"
