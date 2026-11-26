#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从 SQL 提取约束规则 → 生成 sim/rules.py

为什么必须自动生成
------------------
规则写两遍（一遍 SQL、一遍 Python）就一定会偏离，而偏离的方向通常是
「SQL 更严、Python 更松」——于是仿真内核跑得好好的，真实系统却拒绝写入。
本工程的定位是「用仿真验证真实系统的判定」，两边口径必须来自同一份源头。

三类来源，三种处理方式
----------------------
1. CHECK / UNIQUE / 外键 / NOT NULL / 长度上限 / 枚举取值域
   → 结构性解析。SQL 表达式走一个小型递归下降解析器转成 AST。
   **解析不了就抛异常退出**，绝不跳过。跳过等于静默漏规则，比报错危险得多。

2. 触发器的约束语义（追加式表禁止改删、已终审不可改、未终审不得授权）
   → 签名匹配。plpgsql 是图灵完备的，不存在通用解析办法；这里对每个函数声明
     若干「必须匹配的正则」，函数体改了就生成失败，逼人重新确认语义。
     这不是手抄：表名、事件、列名、允许状态都是从 SQL 里抓出来的，
     声明的只是「这段代码属于哪一类规则」。

3. 派生列维护、审计日志写入（不改判定结果）
   → 只做分类登记，不参与接受/拒绝比对。但凡出现未分类的触发器一律失败。

两种额外的自我校验
------------------
--check     重新生成并与磁盘文件逐字节比对，发现 SQL 改了但没重新生成就失败。
--verify-db 连真实库，用 pg_constraint / pg_trigger / information_schema 反查
            实际存在的约束，与生成结果逐条对账。**这一步才是真的防漏**：
            解析器漏掉一条约束时，--check 依然通过，只有对账能发现。

用法
----
    python -m scripts.gen_rules                # 生成并写入 sim/rules.py
    python -m scripts.gen_rules --check        # 只校验是否同步（CI 用）
    python -m scripts.gen_rules --verify-db    # 与运行中的库对账
    python -m scripts.gen_rules --verify-db --dsn "host=127.0.0.1 port=55432 user=postgres dbname=heritage"
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQL_FILES = ("sql/01_init.sql", "sql/02_index_trigger_view.sql")
OUT_FILE = ROOT / "sim" / "rules.py"


class ExtractError(Exception):
    """SQL 里有无法结构化解析的内容 —— 必须人工处理，不许跳过。"""


# =============================================================================
#  注释剥离
# =============================================================================


_DOLLAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")


def strip_comments(text: str) -> str:
    """去掉 -- 行注释与 /* */ 块注释，保留字符串与美元引用内容。

    注释里会出现 `IF OLD.state IN (...)` 这类伪代码（本项目的历史注释就有），
    不剥掉会干扰触发器签名匹配。保留换行以便报错时定位行号。
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "'":  # 单引号字符串，'' 为转义
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(text[i:j])
            i = j
            continue
        if ch == "$":
            m = _DOLLAR_RE.match(text, i)
            if m:
                tag = m.group(0)
                end = text.find(tag, m.end())
                if end == -1:
                    raise ExtractError(f"美元引用 {tag} 没有闭合（位置 {i}）")
                out.append(text[i : end + len(tag)])
                i = end + len(tag)
                continue
        if text.startswith("--", i):
            j = text.find("\n", i)
            if j == -1:
                break
            out.append("\n")
            i = j + 1
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j == -1:
                raise ExtractError(f"块注释没有闭合（位置 {i}）")
            out.append("\n" * text.count("\n", i, j))
            i = j + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# =============================================================================
#  CHECK 表达式解析器
# =============================================================================

TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<str>'(?:[^']|'')*')
    | (?P<num>\d+(?:\.\d+)?)
    | (?P<op><=|>=|<>|!=|!~|=|<|>|~)
    | (?P<punct>[(),])
    | (?P<ident>[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)
    """,
    re.X,
)

KEYWORDS = {"AND", "OR", "NOT", "IS", "NULL", "IN", "TRUE", "FALSE"}

FUNCS = {
    "btrim": "Btrim",
    "length": "Length",
    "vector_dims": "VectorDims",
}


@dataclass
class Tok:
    kind: str
    value: str
    pos: int


def tokenize(expr: str) -> list[Tok]:
    toks: list[Tok] = []
    i = 0
    while i < len(expr):
        m = TOKEN_RE.match(expr, i)
        if not m:
            raise ExtractError(f"无法识别的字符 {expr[i]!r}（在 {expr!r} 位置 {i}）")
        kind = m.lastgroup or ""
        if kind != "ws":
            toks.append(Tok(kind, m.group(0), i))
        i = m.end()
    return toks


class ExprParser:
    """递归下降：or → and → not → primary → predicate → operand。

    只覆盖本工程 CHECK 里实际出现的语法。遇到不支持的写法直接抛错，
    因为「猜一个近似语义」比「明确报错」危险得多。
    """

    def __init__(self, expr: str):
        self.expr = expr
        self.toks = tokenize(expr)
        self.i = 0

    # -- 工具 --------------------------------------------------------------
    def peek(self, k: int = 0) -> Tok | None:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def at_kw(self, *names: str) -> bool:
        t = self.peek()
        return t is not None and t.kind == "ident" and t.value.upper() in names

    def next(self) -> Tok:
        t = self.peek()
        if t is None:
            raise ExtractError(f"表达式意外结束：{self.expr!r}")
        self.i += 1
        return t

    def expect(self, kind: str, value: str | None = None) -> Tok:
        t = self.next()
        if t.kind != kind or (value is not None and t.value != value):
            raise ExtractError(
                f"期望 {value or kind}，实际 {t.value!r}（表达式 {self.expr!r}）"
            )
        return t

    # -- 语法 --------------------------------------------------------------
    def parse(self):
        node = self.parse_or()
        if self.peek() is not None:
            t = self.peek()
            raise ExtractError(f"解析未消费完：{t.value!r}（表达式 {self.expr!r}）")
        return node

    def parse_or(self):
        items = [self.parse_and()]
        while self.at_kw("OR"):
            self.next()
            items.append(self.parse_and())
        return items[0] if len(items) == 1 else ("Or", tuple(items))

    def parse_and(self):
        items = [self.parse_not()]
        while self.at_kw("AND"):
            self.next()
            items.append(self.parse_not())
        return items[0] if len(items) == 1 else ("And", tuple(items))

    def parse_not(self):
        if self.at_kw("NOT"):
            self.next()
            return ("Not", self.parse_not())
        return self.parse_primary()

    def parse_primary(self):
        t = self.peek()
        if t is not None and t.kind == "punct" and t.value == "(":
            self.next()
            node = self.parse_or()
            self.expect("punct", ")")
            return node
        return self.parse_predicate()

    def parse_predicate(self):
        left = self.parse_operand()

        if self.at_kw("IS"):
            self.next()
            negate = False
            if self.at_kw("NOT"):
                self.next()
                negate = True
            if not self.at_kw("NULL"):
                raise ExtractError(f"只支持 IS [NOT] NULL：{self.expr!r}")
            self.next()
            return ("IsNull", left, negate)

        negate_in = False
        if self.at_kw("NOT") and self.peek(1) and self.peek(1).value.upper() == "IN":
            self.next()
            negate_in = True

        if self.at_kw("IN"):
            self.next()
            self.expect("punct", "(")
            values = []
            while True:
                values.append(self.parse_literal())
                t = self.peek()
                if t is not None and t.kind == "punct" and t.value == ",":
                    self.next()
                    continue
                break
            self.expect("punct", ")")
            node = ("In", left, tuple(values))
            return ("Not", node) if negate_in else node

        t = self.peek()
        if t is not None and t.kind == "op":
            op = self.next().value
            right = self.parse_operand()
            if op in ("~", "!~"):
                if not (isinstance(right, tuple) and right[0] == "Lit"
                        and isinstance(right[1], str)):
                    raise ExtractError(f"正则右侧必须是字符串字面量：{self.expr!r}")
                node = ("Regex", left, right[1])
                return ("Not", node) if op == "!~" else node
            return ("Cmp", left, op, right)

        raise ExtractError(f"无法解析的谓词结尾：{self.expr!r}")

    def parse_literal(self):
        t = self.next()
        if t.kind == "str":
            return unquote(t.value)
        if t.kind == "num":
            return float(t.value) if "." in t.value else int(t.value)
        if t.kind == "ident" and t.value.upper() in ("TRUE", "FALSE"):
            return t.value.upper() == "TRUE"
        if t.kind == "ident" and t.value.upper() == "NULL":
            return None
        raise ExtractError(f"IN 列表里出现非字面量 {t.value!r}：{self.expr!r}")

    def parse_operand(self):
        t = self.next()
        if t.kind == "str":
            return ("Lit", unquote(t.value))
        if t.kind == "num":
            return ("Lit", float(t.value) if "." in t.value else int(t.value))
        if t.kind == "ident":
            upper = t.value.upper()
            if upper in ("TRUE", "FALSE"):
                return ("Lit", upper == "TRUE")
            if upper == "NULL":
                return ("Lit", None)
            nxt = self.peek()
            if nxt is not None and nxt.kind == "punct" and nxt.value == "(":
                name = t.value.lower()
                if name not in FUNCS:
                    raise ExtractError(
                        f"CHECK 里出现未登记的函数 {name}()。"
                        " 新增函数需要先在 scripts/gen_rules.py 的 FUNCS 与 sim/ruleast.py 里实现同义求值。"
                    )
                self.next()  # (
                arg = self.parse_operand()
                if isinstance(arg, tuple) and arg[0] == "Lit":
                    raise ExtractError(f"{name}() 的参数不应是字面量：{self.expr!r}")
                self.expect("punct", ")")
                return (FUNCS[name], arg)
            return ("Col", t.value.split(".")[-1].lower())
        raise ExtractError(f"无法解析的操作数 {t.value!r}（表达式 {self.expr!r}）")


def unquote(s: str) -> str:
    return s[1:-1].replace("''", "'")


def parse_expr(expr: str):
    return ExprParser(expr).parse()


# =============================================================================
#  AST → Python 源码
# =============================================================================


def fmt_inline(node) -> str:
    tag = node[0]
    if tag == "Col":
        return f'Col("{node[1]}")'
    if tag == "Lit":
        return f"Lit({node[1]!r})"
    if tag in ("Btrim", "Length", "VectorDims"):
        return f"{tag}({fmt_inline(node[1])})"
    if tag == "Regex":
        return f"Regex({fmt_inline(node[1])}, {node[2]!r})"
    if tag == "Cmp":
        return f"Cmp({fmt_inline(node[1])}, {node[2]!r}, {fmt_inline(node[3])})"
    if tag == "In":
        vals = ", ".join(repr(v) for v in node[2])
        return f"In({fmt_inline(node[1])}, ({vals}{',' if len(node[2]) == 1 else ''}))"
    if tag == "IsNull":
        return f"IsNull({fmt_inline(node[1])}, negate=True)" if node[2] else f"IsNull({fmt_inline(node[1])})"
    if tag in ("And", "Or"):
        return f"{tag}({', '.join(fmt_inline(c) for c in node[1])})"
    if tag == "Not":
        return f"Not({fmt_inline(node[1])})"
    raise ExtractError(f"未知 AST 节点 {tag}")


def fmt(node, indent: int = 0, width: int = 96) -> str:
    inline = fmt_inline(node)
    if indent + len(inline) <= width or node[0] not in ("And", "Or"):
        return inline
    pad = " " * (indent + 4)
    parts = ",\n".join(pad + fmt(c, indent + 4, width) for c in node[1])
    return f"{node[0]}(\n{parts},\n{' ' * indent})"


# =============================================================================
#  DDL 解析
# =============================================================================


@dataclass
class ColDef:
    name: str
    type_sql: str
    not_null: bool
    is_primary: bool


@dataclass
class TableDef:
    name: str
    columns: list[ColDef] = field(default_factory=list)
    checks: list[tuple[str, str]] = field(default_factory=list)  # (name, expr)
    uniques: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    fks: list[dict] = field(default_factory=list)


TYPE_RE = re.compile(r"^(?P<name>:?[A-Za-z_][A-Za-z0-9_]*)(?P<mod>\s*\([^)]*\))?(?P<arr>\[\])?")
CONSTRAINT_RE = re.compile(r"^CONSTRAINT\s+(\w+)\s+(.+)$", re.S | re.I)


def split_top_level(body: str) -> list[str]:
    items, buf, depth, i = [], [], 0, 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if body[j] == "'":
                    if j + 1 < n and body[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            buf.append(body[i:j])
            i = j
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            items.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if "".join(buf).strip():
        items.append("".join(buf))
    return items


def find_block(text: str, start: int) -> tuple[str, int]:
    """从 start（指向 '(' 之后）找到配对的 ')'，返回块内容与结束位置。"""
    depth, i, n = 1, start, len(text)
    while i < n:
        ch = text[i]
        if ch == "'":
            i += 1
            while i < n:
                if text[i] == "'":
                    if i + 1 < n and text[i + 1] == "'":
                        i += 2
                        continue
                    break
                i += 1
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start:i], i
        i += 1
    raise ExtractError("括号没有配对")


def parse_tables(text: str) -> tuple[dict[str, TableDef], dict[str, tuple[str, ...]]]:
    enums: dict[str, tuple[str, ...]] = {}
    for m in re.finditer(
        r"CREATE\s+TYPE\s+(\w+)\s+AS\s+ENUM\s*\(([^)]*)\)", text, re.I | re.S
    ):
        enums[m.group(1).lower()] = tuple(re.findall(r"'((?:[^']|'')*)'", m.group(2)))

    tables: dict[str, TableDef] = {}
    for m in re.finditer(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(", text, re.I
    ):
        name = m.group(1).lower()
        body, _ = find_block(text, m.end())
        tdef = TableDef(name)
        for item in split_top_level(body):
            item = item.strip()
            if not item:
                continue
            cm = CONSTRAINT_RE.match(item)
            if cm:
                cname, cbody = cm.group(1), cm.group(2).strip()
                _parse_table_constraint(tdef, cname, cbody)
                continue
            if re.match(r"^(PRIMARY|UNIQUE|FOREIGN)\s+KEY|^UNIQUE\s*\(|^CHECK\s*\(", item, re.I):
                raise ExtractError(f"表 {name} 有未命名约束，无法追踪来源：{item[:60]!r}")
            _parse_column(tdef, item)
        tables[name] = tdef
    return tables, enums


def _parse_column(tdef: TableDef, item: str) -> None:
    parts = item.split(None, 1)
    if len(parts) < 2:
        raise ExtractError(f"表 {tdef.name} 列定义无法解析：{item!r}")
    col, rest = parts[0].lower(), parts[1]
    tm = TYPE_RE.match(rest)
    if not tm:
        raise ExtractError(f"表 {tdef.name}.{col} 类型无法解析：{rest[:60]!r}")
    type_sql = tm.group(0).strip()
    tail = rest[tm.end():]
    not_null = bool(re.search(r"\bNOT\s+NULL\b", tail, re.I))
    is_pk = bool(re.search(r"\bPRIMARY\s+KEY\b", tail, re.I))
    if is_pk:
        not_null = True
    tdef.columns.append(ColDef(col, type_sql, not_null, is_pk))


def _parse_table_constraint(tdef: TableDef, cname: str, cbody: str) -> None:
    if re.match(r"^CHECK\s*\(", cbody, re.I):
        expr = cbody[cbody.index("(") + 1 : cbody.rindex(")")].strip()
        tdef.checks.append((cname, expr))
        return
    m = re.match(r"^UNIQUE\s*\(([^)]*)\)", cbody, re.I)
    if m:
        cols = tuple(c.strip().lower() for c in m.group(1).split(","))
        tdef.uniques.append((cname, cols))
        return
    m = re.match(
        r"^FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+(\w+)\s*\(([^)]*)\)(.*)$",
        cbody,
        re.I | re.S,
    )
    if m:
        cols = tuple(c.strip().lower() for c in m.group(1).split(","))
        ref_cols = tuple(c.strip().lower() for c in m.group(3).split(","))
        rest = m.group(4)
        om = re.search(r"ON\s+DELETE\s+(CASCADE|RESTRICT|SET\s+NULL|SET\s+DEFAULT|NO\s+ACTION)", rest, re.I)
        um = re.search(r"ON\s+UPDATE\s+(CASCADE|RESTRICT|SET\s+NULL|SET\s+DEFAULT|NO\s+ACTION)", rest, re.I)
        tdef.fks.append(
            dict(
                name=cname,
                columns=cols,
                ref_table=m.group(2).lower(),
                ref_columns=ref_cols,
                on_delete=re.sub(r"\s+", " ", om.group(1).upper()) if om else "NO ACTION",
                on_update=re.sub(r"\s+", " ", um.group(1).upper()) if um else "NO ACTION",
            )
        )
        return
    raise ExtractError(f"表 {tdef.name} 约束 {cname} 类型未知：{cbody[:60]!r}")


def parse_partial_unique(text: str) -> list[dict]:
    out = []
    for m in re.finditer(
        r"CREATE\s+UNIQUE\s+INDEX\s+(\w+)\s+ON\s+(\w+)\s*\(([^)]*)\)\s*WHERE\s+(.+?);",
        text,
        re.I | re.S,
    ):
        out.append(
            dict(
                name=m.group(1),
                table=m.group(2).lower(),
                columns=tuple(c.strip().lower() for c in m.group(3).split(",")),
                where=" ".join(m.group(4).split()),
            )
        )
    return out


# =============================================================================
#  触发器解析
# =============================================================================

FUNC_RE = re.compile(
    r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+(\w+)\s*\([^)]*\)\s*RETURNS\s+TRIGGER\b.*?"
    r"AS\s+\$(\w*)\$(.*?)\$\2\$",
    re.I | re.S,
)

TRG_RE = re.compile(
    r"CREATE\s+TRIGGER\s+(\w+)\s+(BEFORE|AFTER|INSTEAD\s+OF)\s+(.*?)\s+ON\s+(\w+)\s+"
    r"FOR\s+EACH\s+(ROW|STATEMENT).*?EXECUTE\s+(?:FUNCTION|PROCEDURE)\s+(\w+)",
    re.I | re.S,
)

# 触发器语义签名：不是「规则内容」，而是「这段 plpgsql 属于哪一类判定」。
# must 里的正则有捕获组时会被用来抽取表名/列名/状态值，避免手抄。
SIGNATURES: dict[str, dict] = {
    "fn_forbid_delete": dict(kind="reject", must=[r"RAISE\s+EXCEPTION"]),
    "fn_forbid_modify": dict(kind="reject", must=[r"RAISE\s+EXCEPTION"]),
    "fn_audit_record_guard": dict(
        kind="guard",
        must=[r"IF\s+OLD\.state\s+IN\s*\(([^)]*)\)\s+THEN", r"RAISE\s+EXCEPTION"],
    ),
    "fn_license_require_approved": dict(
        kind="gate",
        must=[
            r"SELECT\s+a\.(\w+)\s+INTO\s+v_status\s+FROM\s+(\w+)\s+a\s+"
            r"WHERE\s+a\.(\w+)\s*=\s*NEW\.(\w+)",
            r"IF\s+v_status\s*<>\s*'(\w+)'\s+THEN",
            r"RAISE\s+EXCEPTION",
        ],
    ),
    "fn_asset_touch": dict(
        kind="derived", must=[r"NEW\.version_no\s*:=\s*OLD\.version_no\s*\+\s*1"]
    ),
    # 角色闸门：同一行数据，换个角色判定就不同 —— 所以它不能进「接受/拒绝」对拍
    # （对拍样本只描述数据，不描述身份）。这里抽取的是三件事：受管的角色集合、
    # 被认作「终态」的取值、以及允许写终态的事务标记名与标记值。
    # 三者都从 SQL 里抓，不手抄；函数体改了导致抓不到，生成直接失败。
    "fn_asset_status_guard": dict(
        kind="actor_gate",
        must=[
            r"v_db_role\s+NOT\s+IN\s*\(([^)]*)\)",
            r"v_final\s+CONSTANT\s+TEXT\[\]\s*:=\s*ARRAY\[([^\]]*)\]",
            r"current_setting\('(ich\.finalize_gate)',\s*true\)",
            r"v_gate_ops\s+CONSTANT\s+TEXT\[\]\s*:=\s*ARRAY\[([^\]]*)\]",
            r"RAISE\s+EXCEPTION",
        ],
    ),
    "fn_motif_vector_normalize": dict(kind="derived", must=[r"NEW\.base_vector\s*:="]),
    "fn_asset_audit_trigger": dict(kind="audit", must=[r"INSERT\s+INTO\s+system_audit_log"]),
    # 谱系 DAG 守卫：判据是「图结构」而不是单行取值 —— child 是否已是 parent 的祖先，
    # 要靠递归遍历整张边表才能回答。对拍样本的模型是"一行数据 + 一次操作"，
    # 表达不了"若干条边构成一条路径"，因此登记为 dag 类别，不参与接受/拒绝对拍；
    # 它的行为由 tests/test_pg_concurrency 在真实库上按图场景逐条验证。
    "fn_genealogy_no_cycle": dict(
        kind="dag",
        must=[
            r"pg_advisory_xact_lock",
            r"WITH\s+RECURSIVE\s+up",
            r"NEW\.parent_asset_id",
            r"RAISE\s+EXCEPTION",
        ],
    ),
}


def parse_triggers(text: str) -> list[dict]:
    bodies: dict[str, str] = {}
    for m in FUNC_RE.finditer(text):
        bodies[m.group(1).lower()] = m.group(3)

    triggers = []
    for m in TRG_RE.finditer(text):
        tname, timing, events_sql, table, level, func = m.groups()
        func = func.lower()
        events = []
        for part in re.split(r"\s+OR\s+", " ".join(events_sql.split()), flags=re.I):
            head = part.split()[0].upper()
            if head not in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                raise ExtractError(f"触发器 {tname} 的事件无法解析：{events_sql!r}")
            events.append(head)

        if func not in bodies:
            raise ExtractError(f"触发器 {tname} 引用的函数 {func}() 未在文件中定义")
        if func not in SIGNATURES:
            raise ExtractError(
                f"触发器函数 {func}() 没有语义签名。新增触发器必须先在 "
                "scripts/gen_rules.py 的 SIGNATURES 里声明它属于哪类规则，否则规则表会漏掉它。"
            )
        sig = SIGNATURES[func]
        body = bodies[func]
        groups: list[re.Match] = []
        for pat in sig["must"]:
            mm = re.search(pat, body, re.I | re.S)
            if not mm:
                raise ExtractError(
                    f"{func}() 的函数体与声明的语义不再匹配（模式 {pat!r} 未命中）。"
                    " 触发器逻辑被改动过，必须人工确认后更新签名，不能放任规则表与实现脱节。"
                )
            groups.append(mm)

        rule = dict(
            table=table.lower(),
            name=tname,
            timing=re.sub(r"\s+", " ", timing.upper()),
            events=tuple(events),
            function=func,
            kind=sig["kind"],
            level=level.upper(),
            gate=None,
            forbid_when_old_state_in=(),
            actor_roles=(),
            final_states=(),
            gate_marker=None,
            gate_values=(),
        )

        if sig["kind"] == "guard":
            states = tuple(re.findall(r"'([^']*)'", groups[0].group(1)))
            if not states:
                raise ExtractError(f"{func}() 的 OLD.state IN 列表为空，无法抽取")
            rule["forbid_when_old_state_in"] = states

        if sig["kind"] == "actor_gate":
            roles = tuple(re.findall(r"'([^']*)'", groups[0].group(1)))
            finals = tuple(re.findall(r"'([^']*)'", groups[1].group(1)))
            marker = groups[2].group(1)
            values = tuple(re.findall(r"'([^']*)'", groups[3].group(1)))
            if not roles or not finals or not values:
                raise ExtractError(
                    f"{func}() 的角色/终态/标记列表有空项，无法抽取"
                    f"（roles={roles}, finals={finals}, values={values}）"
                )
            rule["actor_roles"] = roles
            rule["final_states"] = finals
            rule["gate_marker"] = marker
            rule["gate_values"] = values

        if sig["kind"] == "gate":
            gm = groups[0]
            ref_column, ref_table, ref_key, fk_column = gm.groups()
            allowed = (groups[1].group(1),)
            rule["gate"] = dict(
                fk_column=fk_column.lower(),
                ref_table=ref_table.lower(),
                ref_column=ref_column.lower(),
                allowed=allowed,
            )

        triggers.append(rule)

    if not triggers:
        raise ExtractError("没有解析到任何触发器 —— 文件结构可能变了")
    return triggers


# =============================================================================
#  汇总 + 生成
# =============================================================================


@dataclass
class Inventory:
    enums: dict[str, tuple[str, ...]]
    tables: dict[str, TableDef]
    partial_uniques: list[dict]
    triggers: list[dict]

    # -- 展平视图 ----------------------------------------------------------
    def checks(self):
        for t in self.tables.values():
            for name, expr in t.checks:
                yield t.name, name, expr

    def uniques(self):
        for t in self.tables.values():
            for name, cols in t.uniques:
                yield t.name, name, cols

    def fks(self):
        for t in self.tables.values():
            for fk in t.fks:
                yield t.name, fk

    def not_nulls(self):
        for t in self.tables.values():
            for c in t.columns:
                if c.not_null:
                    yield t.name, c.name

    def max_lengths(self):
        for t in self.tables.values():
            for c in t.columns:
                m = re.match(r"^(VARCHAR|CHARACTER\s+VARYING|CHAR|CHARACTER)\s*\((\d+)\)", c.type_sql, re.I)
                if m:
                    fixed = m.group(1).upper() in ("CHAR", "CHARACTER")
                    yield t.name, c.name, int(m.group(2)), fixed

    def enum_columns(self):
        for t in self.tables.values():
            for c in t.columns:
                key = c.type_sql.split("(")[0].strip().lower()
                if key in self.enums:
                    yield t.name, c.name, key

    def constraint_names(self) -> set[str]:
        s = {n for _, n, _ in self.checks()}
        s |= {n for _, n, _ in self.uniques()}
        s |= {fk["name"] for _, fk in self.fks()}
        s |= {u["name"] for u in self.partial_uniques}
        s |= {t["name"] for t in self.triggers}
        return s


def build_inventory() -> Inventory:
    full = []
    for rel in SQL_FILES:
        full.append(strip_comments((ROOT / rel).read_text(encoding="utf-8")))
    text = "\n".join(full)

    tables, enums = parse_tables(text)
    for t in tables.values():
        for _, expr in t.checks:
            try:
                parse_expr(expr)
            except ExtractError as exc:
                raise ExtractError(
                    f"表 {t.name} 的 CHECK {_} 无法解析：{exc}\n"
                    "  处理方式：在 ExprParser 里补语法，或改写该约束。不要跳过。"
                ) from exc

    return Inventory(
        enums=enums,
        tables=tables,
        partial_uniques=parse_partial_unique(text),
        triggers=parse_triggers(text),
    )


def render(inv: Inventory) -> str:
    src_hash = {
        rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()[:16] for rel in SQL_FILES
    }
    n_checks = len(list(inv.checks()))
    n_unique = len(list(inv.uniques())) + len(inv.partial_uniques)
    n_fk = len(list(inv.fks()))
    n_trg = len(inv.triggers)

    L: list[str] = []
    A = L.append
    A('"""')
    A("本文件由 scripts/gen_rules.py 从 SQL 自动生成，**请勿手改**。")
    A("")
    A("生成：python -m scripts.gen_rules")
    A("校验：python -m scripts.gen_rules --check      （SQL 改了没重新生成 → 非零退出）")
    A("对账：python -m scripts.gen_rules --verify-db  （与运行中的库逐条比对）")
    A("")
    A("来源指纹（前 16 位）")
    for rel, h in src_hash.items():
        A(f"  {rel:<30} {h}")
    A("")
    A(f"规则计数：CHECK {n_checks} / UNIQUE {n_unique} / 外键 {n_fk} / 触发器 {n_trg}")
    A("")
    A("为什么这里只有数据没有逻辑：逻辑在 sim/ruleast.py（手写）与 sim/verdict.py。")
    A("生成文件被覆盖时不应丢失任何判定语义，否则说明有人把逻辑写进了生成器。")
    A('"""')
    A("")
    A("from __future__ import annotations")
    A("")
    A("from sim.ruleast import (")
    A("    And,")
    A("    Btrim,")
    A("    CheckRule,")
    A("    Col,")
    A("    Cmp,")
    A("    EnumRule,")
    A("    ForeignKeyRule,")
    A("    In,")
    A("    IsNull,")
    A("    Length,")
    A("    Lit,")
    A("    MaxLengthRule,")
    A("    Not,")
    A("    NotNullRule,")
    A("    Or,")
    A("    ParentGate,")
    A("    Regex,")
    A("    TriggerRule,")
    A("    UniqueRule,")
    A("    VectorDims,")
    A(")")
    A("")
    A(f"SOURCE_FILES = {tuple(SQL_FILES)!r}")
    A(f"SOURCE_SHA256 = {src_hash!r}")
    A("")

    # ---- 枚举
    A("# " + "=" * 74)
    A("#  枚举取值域（由 CREATE TYPE ... AS ENUM 决定）")
    A("# " + "=" * 74)
    A("ENUMS: dict[str, tuple[str, ...]] = {")
    for name, vals in sorted(inv.enums.items()):
        A(f'    "{name}": ({", ".join(repr(v) for v in vals)},),')
    A("}")
    A("")

    # ---- 列
    A("# " + "=" * 74)
    A("#  列定义（顺序与 DDL 一致，供样本构造使用）")
    A("# " + "=" * 74)
    A("COLUMNS: dict[str, tuple[str, ...]] = {")
    for t in inv.tables.values():
        cols = ", ".join(f'"{c.name}"' for c in t.columns)
        A(f'    "{t.name}": ({cols},),')
    A("}")
    A("")
    A("COLUMN_TYPES: dict[str, dict[str, str]] = {")
    for t in inv.tables.values():
        A(f'    "{t.name}": {{')
        for c in t.columns:
            A(f'        "{c.name}": "{c.type_sql}",')
        A("    },")
    A("}")
    A("")

    # ---- NOT NULL
    A("# " + "=" * 74)
    A("#  NOT NULL（含主键隐含的 NOT NULL）")
    A("# " + "=" * 74)
    A("NOT_NULL: tuple[NotNullRule, ...] = (")
    for tbl, col in inv.not_nulls():
        A(f'    NotNullRule("{tbl}", "{col}"),')
    A(")")
    A("")

    # ---- 长度上限
    A("# " + "=" * 74)
    A("#  长度上限（写在类型里，不是 CHECK —— 最容易在盘点时漏掉的一类）")
    A("# " + "=" * 74)
    A("MAX_LENGTH: tuple[MaxLengthRule, ...] = (")
    for tbl, col, n, fixed in inv.max_lengths():
        extra = ", fixed=True" if fixed else ""
        A(f'    MaxLengthRule("{tbl}", "{col}", {n}{extra}),')
    A(")")
    A("")

    # ---- 枚举列
    A("# " + "=" * 74)
    A("#  使用枚举类型的列")
    A("# " + "=" * 74)
    A("ENUM_COLUMNS: tuple[EnumRule, ...] = (")
    for tbl, col, key in inv.enum_columns():
        A(f'    EnumRule("{tbl}", "{col}", "{key}", ENUMS["{key}"]),')
    A(")")
    A("")

    # ---- CHECK
    A("# " + "=" * 74)
    A("#  CHECK 约束")
    A("# " + "=" * 74)
    A("CHECKS: tuple[CheckRule, ...] = (")
    for tbl, name, expr in inv.checks():
        A(f'    CheckRule("{tbl}", "{name}", {expr!r},')
        node = parse_expr(expr)
        A(f"        {fmt(node, 8)},")
        A("    ),")
    A(")")
    A("")

    # ---- UNIQUE
    A("# " + "=" * 74)
    A("#  UNIQUE（含部分唯一索引：唯一性只作用于 WHERE 命中的子集）")
    A("# " + "=" * 74)
    A("UNIQUE: tuple[UniqueRule, ...] = (")
    for tbl, name, cols in inv.uniques():
        cs = ", ".join(f'"{c}"' for c in cols)
        A(f'    UniqueRule("{tbl}", "{name}", ({cs},)),')
    for u in inv.partial_uniques:
        cs = ", ".join(f'"{c}"' for c in u["columns"])
        A(f'    UniqueRule("{u["table"]}", "{u["name"]}", ({cs},),')
        A(f"        where={fmt(parse_expr(u['where']), 8, 96)},")
        A("        index=True,")
        A("    ),")
    A(")")
    A("")

    # ---- 外键
    A("# " + "=" * 74)
    A("#  外键与引用动作（ON DELETE RESTRICT 是溯源链的完整性基础）")
    A("# " + "=" * 74)
    A("FOREIGN_KEYS: tuple[ForeignKeyRule, ...] = (")
    for tbl, fk in inv.fks():
        cs = ", ".join(f'"{c}"' for c in fk["columns"])
        rs = ", ".join(f'"{c}"' for c in fk["ref_columns"])
        A(f'    ForeignKeyRule("{tbl}", "{fk["name"]}", ({cs},),')
        A(f'        "{fk["ref_table"]}", ({rs},),')
        A(f'        on_delete="{fk["on_delete"]}", on_update="{fk["on_update"]}"),')
    A(")")
    A("")

    # ---- 触发器
    A("# " + "=" * 74)
    A("#  触发器语义")
    A("#    reject  无条件拒绝某类事件（追加式表防篡改）")
    A("#    gate    写入前要求父行处于允许状态（未终审不得授权）")
    A("#    guard   依据 OLD 行状态拒绝本次修改（已终审不可改）")
    A("#    actor_gate 依据数据库角色决定能不能改（传承人不能写终审结论）")
    A("#    derived 只改写 NEW 的派生列，不拒绝")
    A("#    audit   只写审计日志，不拒绝")
    A("#  前三类参与「接受/拒绝」比对；actor_gate 的判定依赖身份而非数据，")
    A("#  对拍样本描述不了它，改由 tests/check_pg 以真实角色切换验证；")
    A("#  后两类登记在案以防新触发器无人审阅。")
    A("# " + "=" * 74)
    A("TRIGGERS: tuple[TriggerRule, ...] = (")
    for t in inv.triggers:
        ev = ", ".join(f'"{e}"' for e in t["events"])
        A(f'    TriggerRule("{t["table"]}", "{t["name"]}", "{t["timing"]}",')
        A(f'        ({ev},), "{t["function"]}", "{t["kind"]}",')
        if t["gate"]:
            g = t["gate"]
            al = ", ".join(f'"{a}"' for a in g["allowed"])
            A("        gate=ParentGate(")
            A(f'            "{g["fk_column"]}", "{g["ref_table"]}",')
            A(f'            "{g["ref_column"]}", ({al},),')
            A("        ),")
        if t["forbid_when_old_state_in"]:
            st = ", ".join(f'"{s}"' for s in t["forbid_when_old_state_in"])
            A(f"        forbid_when_old_state_in=({st},),")
        if t["actor_roles"]:
            ro = ", ".join(f'"{r}"' for r in t["actor_roles"])
            fi = ", ".join(f'"{f}"' for f in t["final_states"])
            va = ", ".join(f'"{v}"' for v in t["gate_values"])
            A(f"        actor_roles=({ro},),")
            A(f"        final_states=({fi},),")
            A(f'        gate_marker="{t["gate_marker"]}",')
            A(f"        gate_values=({va},),")
        A("    ),")
    A(")")
    A("")

    # ---- 索引
    A("CONSTRAINT_NAMES: frozenset[str] = frozenset({")
    for n in sorted(inv.constraint_names()):
        A(f'    "{n}",')
    A("})")
    A("")
    A("REJECTING_TRIGGERS: tuple[TriggerRule, ...] = tuple(")
    A('    t for t in TRIGGERS if t.kind in ("reject", "gate", "guard")')
    A(")")
    A("")
    return "\n".join(L) + "\n"


# =============================================================================
#  与真实库对账
# =============================================================================


def verify_db(inv: Inventory, dsn: str) -> int:
    try:
        import psycopg2
    except ImportError:
        print("✗ 需要 psycopg2 才能对账（pip install psycopg2-binary）")
        return 2

    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT c.conname, t.relname, c.contype
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public' AND c.contype IN ('c', 'u', 'f')
        """
    )
    db = {"c": set(), "u": set(), "f": set()}
    for name, table, typ in cur.fetchall():
        db[typ].add((table, name))

    gen = {
        "c": {(t, n) for t, n, _ in inv.checks()},
        "u": {(t, n) for t, n, _ in inv.uniques()},
        "f": {(t, fk["name"]) for t, fk in inv.fks()},
    }

    # 部分唯一索引在 pg_indexes 里，不在 pg_constraint
    cur.execute(
        "SELECT tablename, indexname FROM pg_indexes"
        " WHERE schemaname='public' AND indexdef ILIKE '%UNIQUE INDEX%'"
    )
    db_partial = {
        (t, n)
        for t, n in cur.fetchall()
        if any(u["name"] == n for u in inv.partial_uniques)
    }
    gen_partial = {(u["table"], u["name"]) for u in inv.partial_uniques}

    cur.execute(
        """
        SELECT tg.tgname, c.relname
        FROM pg_trigger tg
        JOIN pg_class c ON c.oid = tg.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE NOT tg.tgisinternal AND n.nspname = 'public'
        """
    )
    db_trg = {(table, name) for name, table in cur.fetchall()}
    gen_trg = {(t["table"], t["name"]) for t in inv.triggers}

    cur.execute(
        """
        SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema='public' AND is_nullable='NO'
        """
    )
    db_nn = {(t.lower(), c.lower()) for t, c in cur.fetchall()}
    gen_nn = set(inv.not_nulls())

    rows = [
        ("CHECK", gen["c"], db["c"]),
        ("UNIQUE(表约束)", gen["u"], db["u"]),
        ("UNIQUE(部分索引)", gen_partial, db_partial),
        ("外键", gen["f"], db["f"]),
        ("触发器", gen_trg, db_trg),
        ("NOT NULL", gen_nn, db_nn),
    ]

    print("=" * 68)
    print(f"  与真实库对账：{dsn.split('dbname=')[-1] if 'dbname=' in dsn else dsn}")
    print("=" * 68)
    bad = 0
    for label, g, d in rows:
        only_gen = g - d
        only_db = d - g
        status = "✓" if not only_gen and not only_db else "✗"
        if only_gen or only_db:
            bad += 1
        print(f"  {status} {label:<16} 生成 {len(g):>3} / 库内 {len(d):>3}")
        for x in sorted(only_gen):
            print(f"      - 只在生成结果里（SQL 已删但规则表没重生成？）: {x}")
        for x in sorted(only_db):
            print(f"      - 只在库内（解析器漏抽）: {x}")

    conn.close()
    if bad:
        print(f"\n✗ {bad} 类不一致 —— 规则表与真实库已经偏离")
        return 1
    print("\n✓ 全部一致：生成结果与库内实际约束逐条对应")
    return 0


# =============================================================================
#  main
# =============================================================================


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从 SQL 生成 sim/rules.py")
    ap.add_argument("--check", action="store_true", help="只校验生成文件是否与 SQL 同步")
    ap.add_argument("--verify-db", action="store_true", help="与运行中的 PostgreSQL 对账")
    ap.add_argument("--dsn", default=None, help="连接串，默认取 ICH_DB_* 环境变量")
    args = ap.parse_args(argv)

    try:
        inv = build_inventory()
    except ExtractError as exc:
        print(f"✗ 提取失败：{exc}", file=sys.stderr)
        return 2

    text = render(inv)

    if args.check:
        if not OUT_FILE.exists():
            print(f"✗ {OUT_FILE.relative_to(ROOT)} 不存在，先运行 python -m scripts.gen_rules")
            return 1
        current = OUT_FILE.read_text(encoding="utf-8")
        if current == text:
            n = len(list(inv.checks()))
            print(f"✓ 规则表与 SQL 同步（CHECK {n} / UNIQUE {len(list(inv.uniques())) + len(inv.partial_uniques)}"
                  f" / 外键 {len(list(inv.fks()))} / 触发器 {len(inv.triggers)}）")
            return 0
        print("✗ sim/rules.py 与 SQL 不同步 —— SQL 改过但规则表没重新生成。")
        import difflib

        diff = list(
            difflib.unified_diff(
                current.splitlines(),
                text.splitlines(),
                fromfile="sim/rules.py（磁盘）",
                tofile="sim/rules.py（按当前 SQL 生成）",
                lineterm="",
                n=2,
            )
        )
        print("\n".join(diff[:60]))
        if len(diff) > 60:
            print(f"... 还有 {len(diff) - 60} 行差异")
        print("\n修复：python -m scripts.gen_rules")
        return 1

    OUT_FILE.write_text(text, encoding="utf-8")
    n_checks = len(list(inv.checks()))
    print(f"✓ 已写入 {OUT_FILE.relative_to(ROOT)}")
    print(f"  CHECK {n_checks} / UNIQUE {len(list(inv.uniques())) + len(inv.partial_uniques)}"
          f" / 外键 {len(list(inv.fks()))} / 触发器 {len(inv.triggers)}"
          f" / NOT NULL {len(list(inv.not_nulls()))}")

    if args.verify_db:
        dsn = args.dsn or (
            f"host={os.getenv('ICH_DB_HOST', '127.0.0.1')} "
            f"port={os.getenv('ICH_DB_PORT', '5432')} "
            f"user={os.getenv('ICH_DB_USER', 'postgres')} "
            f"password={os.getenv('ICH_DB_PASSWORD', '')} "
            f"dbname={os.getenv('ICH_DB_NAME', 'ich_heritage')}"
        )
        print()
        return verify_db(inv, dsn)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
