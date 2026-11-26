"""
SQL 脚本静态校验（无数据库环境下的兜底检查）。

为什么需要这个？
  本机安全策略禁止执行 initdb，无法建真实 PG 实例。
  但 SQL 一旦有语法错/引用错，用户第一次跑就会失败——所以用 Python 做结构级校验：
    1. 语句切分正确性：能否按 `;` 正确切分（正确处理 $$ 函数体、字符串、注释）
    2. 对象定义完整性：期望的表/索引/触发器/视图/角色是否都定义了
    3. 引用一致性：CREATE TABLE 的外键目标表是否在同一脚本集中定义；
       视图里引用的表/列是否存在；触发器函数是否已定义
    4. 括号/引号配平

  这不是 PostgreSQL 语法分析器的替代品，但能拦住 90% 的手写笔误。
  真实执行验证请在装有 PostgreSQL 15+ 的环境按 README 操作。

运行：python -m tests.test_sql_structure
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"


# =============================================================================
#  语句切分：正确处理 $$ ... $$ 函数体、单引号字符串、-- 行注释、/* */ 块注释
# =============================================================================
def split_statements(sql: str) -> list[str]:
    stmts: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    in_single = False
    in_dollar = False
    dollar_tag = ""
    in_line_comment = False
    in_block_comment = False

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
                buf.append(ch)
            i += 1
            continue

        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if in_single:
            buf.append(ch)
            if ch == "'":
                if nxt == "'":          # 转义的单引号 ''
                    buf.append(nxt)
                    i += 2
                    continue
                in_single = False
            i += 1
            continue

        if in_dollar:
            if sql.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                in_dollar = False
                continue
            buf.append(ch)
            i += 1
            continue

        # 正常状态
        if ch == "-" and nxt == "-":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue
        if ch == "$":
            m = re.match(r"\$[A-Za-z_]*\$", sql[i:])
            if m:
                dollar_tag = m.group(0)
                in_dollar = True
                buf.append(dollar_tag)
                i += len(dollar_tag)
                continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


# =============================================================================
#  提取对象定义
# =============================================================================
def extract_objects(stmts: list[str]) -> dict[str, set[str]]:
    objs = {
        "table": set(), "view": set(), "index": set(), "function": set(),
        "trigger": set(), "type": set(), "role": set(),
    }
    for s in stmts:
        low = s.lower()
        m = re.search(r"create\s+(?:or\s+replace\s+)?table\s+(?:if\s+not\s+exists\s+)?([a-z_][\w.]*)", low)
        if m:
            objs["table"].add(m.group(1).split(".")[-1])
        m = re.search(r"create\s+(?:or\s+replace\s+)?view\s+([a-z_][\w.]*)", low)
        if m:
            objs["view"].add(m.group(1).split(".")[-1])
        m = re.search(r"create\s+(?:unique\s+)?index\s+(?:if\s+not\s+exists\s+)?([a-z_][\w.]*)", low)
        if m:
            objs["index"].add(m.group(1).split(".")[-1])
        m = re.search(r"create\s+(?:or\s+replace\s+)?function\s+([a-z_][\w.]*)", low)
        if m:
            objs["function"].add(m.group(1).split(".")[-1])
        m = re.search(r"create\s+trigger\s+([a-z_][\w.]*)", low)
        if m:
            objs["trigger"].add(m.group(1).split(".")[-1])
        m = re.search(r"create\s+type\s+([a-z_][\w.]*)", low)
        if m:
            objs["type"].add(m.group(1).split(".")[-1])
        for rm in re.finditer(r"create\s+role\s+([a-z_][\w.]*)", low):
            objs["role"].add(rm.group(1))
    return objs


def check_brackets(sql: str) -> list[str]:
    """括号配平检查（扣掉注释与字符串内容）。"""
    errs = []
    depth_paren = 0
    depth_block = 0
    # 去掉行注释与单引号字符串内容（保留换行以维持行号）
    cleaned = re.sub(r"--[^\n]*", "", sql)
    cleaned = re.sub(r"'(?:''|[^'])*'", "''", cleaned)
    for lineno, line in enumerate(cleaned.splitlines(), 1):
        for ch in line:
            if ch == "(":
                depth_paren += 1
            elif ch == ")":
                depth_paren -= 1
            elif ch == "{":
                depth_block += 1
            elif ch == "}":
                depth_block -= 1
            if depth_paren < 0:
                errs.append(f"第 {lineno} 行：右括号 ')' 多于左括号")
                depth_paren = 0
            if depth_block < 0:
                errs.append(f"第 {lineno} 行：右花括号 '}}' 多于左花括号")
                depth_block = 0
    if depth_paren:
        errs.append(f"文件结束时 '(' 未闭合，剩余 {depth_paren} 个")
    if depth_block:
        errs.append(f"文件结束时 '{{' 未闭合，剩余 {depth_block} 个")
    return errs


# =============================================================================
#  主检查
# =============================================================================
def main() -> int:
    files = sorted(SQL_DIR.glob("*.sql"))
    if not files:
        print(f"{RED}未找到 sql/*.sql{RESET}")
        return 1

    all_objs = {k: set() for k in ("table", "view", "index", "function", "trigger", "type", "role")}
    failures = 0

    print("=" * 74)
    print(" SQL 脚本结构校验")
    print("=" * 74)

    for f in files:
        sql = f.read_text(encoding="utf-8")
        stmts = split_statements(sql)
        objs = extract_objects(stmts)
        br = check_brackets(sql)
        for k, v in objs.items():
            all_objs[k] |= v

        status = f"{GREEN}OK{RESET}" if not br else f"{RED}FAIL{RESET}"
        print(f"\n[{status}] {f.name}  语句数={len(stmts)}")
        counts = ", ".join(f"{k}={len(v)}" for k, v in objs.items() if v)
        print(f"     对象: {counts or '（仅数据/授权语句）'}")
        if br:
            failures += 1
            for e in br:
                print(f"     {RED}✗ {e}{RESET}")

        # 语句级粗检：每条语句应以已知关键字开头
        # psql 元命令（\echo / \if / \else / \endif / \set / \i / \gset）是合法开头，
        # 它们是脚本的条件分支与变量机制，不是 SQL 语句。
        bad = []
        for idx, s in enumerate(stmts, 1):
            head = re.sub(r"^/\*.*?\*/", "", s, flags=re.S).strip().lower()
            if not re.match(
                r"^(create|drop|alter|insert|update|delete|select|revoke|grant|comment|do|set"
                r"|\\echo|\\if|\\else|\\endif|\\set|\\i |\\gset)",
                head,
            ):
                bad.append((idx, head[:60].replace("\n", " ")))
        if bad:
            failures += 1
            for idx, head in bad:
                print(f"     {YELLOW}? 第 {idx} 条语句开头无法识别: {head}{RESET}")

    # ---------- 关键对象完整性 ----------
    print("\n" + "-" * 74)
    print("关键对象完整性")
    expected_tables = {"app_user", "public_motif", "asset", "feature_vector",
                       "audit_record", "license_record", "asset_genealogy", "system_audit_log"}
    expected_views = {"v_asset_pending_audit", "v_asset_trace_chain", "v_asset_ledger",
                      "v_asset_lineage"}
    expected_roles = {"ich_heritor", "ich_expert", "ich_admin"}

    def chk(label: str, expect: set[str], actual: set[str]) -> None:
        nonlocal failures
        missing = expect - actual
        if missing:
            failures += 1
            print(f"  {RED}✗ {label} 缺失: {sorted(missing)}{RESET}")
        else:
            print(f"  {GREEN}✓{RESET} {label} 齐全 ({len(expect)})")

    chk("8 张业务表", expected_tables, all_objs["table"])
    chk("3 个视图", expected_views, all_objs["view"])
    chk("3 个权限角色", expected_roles, all_objs["role"])

    # ---------- 外键目标表存在 ----------
    print("\n" + "-" * 74)
    print("外键引用一致性")
    fk_targets: set[str] = set()
    for f in files:
        sql = f.read_text(encoding="utf-8")
        for m in re.finditer(r"references\s+([a-z_][\w.]*)", sql, re.I):
            fk_targets.add(m.group(1).split(".")[-1].lower())
    missing_fk = fk_targets - all_objs["table"]
    if missing_fk:
        failures += 1
        print(f"  {RED}✗ 外键引用了未定义的表: {sorted(missing_fk)}{RESET}")
    else:
        print(f"  {GREEN}✓{RESET} 所有 {len(fk_targets)} 个外键目标表均已定义: {sorted(fk_targets)}")

    # ---------- 触发器函数存在 ----------
    print("\n" + "-" * 74)
    print("触发器函数引用")
    fn_refs: set[str] = set()
    for f in files:
        sql = f.read_text(encoding="utf-8")
        for m in re.finditer(r"execute\s+(?:procedure|function)\s+([a-z_][\w.]*)", sql, re.I):
            fn_refs.add(m.group(1).split(".")[-1].lower())
    missing_fn = fn_refs - all_objs["function"]
    if missing_fn:
        failures += 1
        print(f"  {RED}✗ 触发器引用的函数未定义: {sorted(missing_fn)}{RESET}")
    else:
        print(f"  {GREEN}✓{RESET} 所有 {len(fn_refs)} 个触发器函数均已定义")

    # ---------- 视图引用的表存在 ----------
    print("\n" + "-" * 74)
    print("视图依赖表")
    view_sql = "\n".join(
        f.read_text(encoding="utf-8") for f in files
    )
    # 收集所有 CREATE VIEW 段落，检查 FROM/JOIN 后的表名
    # 边界必须同时考虑"下一个 CREATE VIEW"和"下一个 CREATE FUNCTION/DO/GRANT"，
    # 否则会把后续语句误算进视图体（校验器自身的坑，已修正）
    view_boundary = r"(?=create\s+(?:view|function|or\s+replace\s+function|table|index)|^\s*do\s+\$\$|\Z)"
    for m in re.finditer(
        r"create\s+view\s+([a-z_]\w*)\s+as(.*?)" + view_boundary,
        view_sql, re.I | re.S | re.M,
    ):
        vname, body = m.group(1), m.group(2)
        body_wo_comments = re.sub(r"--[^\n]*", "", body)
        # 递归视图里的 CTE 名（WITH RECURSIVE up AS ...）不是表，
        # 但会被 FROM/JOIN 正则抓出来。不排除掉就会把 v_asset_lineage 误报为
        # "引用了未定义对象 up" —— 校验器自身的坑，和上面 view_boundary 同类。
        ctes = {
            c.lower()
            for c in re.findall(r"(?:with\s+(?:recursive\s+)?|,\s*)([a-z_]\w*)\s+as\s*\(", body, re.I)
        }
        refs = set(re.findall(r"(?:from|join)\s+([a-z_][\w.]*)", body_wo_comments, re.I))
        refs = {r.split(".")[-1].lower() for r in refs} - {"lateral", "select"} - ctes
        unknown = refs - all_objs["table"] - all_objs["view"]
        if unknown:
            failures += 1
            print(f"  {RED}✗ 视图 {vname} 引用了未定义对象: {sorted(unknown)}{RESET}")
        else:
            print(f"  {GREEN}✓{RESET} {vname} ← {sorted(refs)}")

    # ---------- 关键约束存在性 ----------
    print("\n" + "-" * 74)
    print("关键约束 / 索引")
    checks = [
        ("HNSW 向量索引 (public_motif)", r"using\s+hnsw"),
        ("HNSW 向量索引 (feature_vector)", r"hnsw\s*\(embedding"),
        ("部分索引 (仅待审行)", r"where\s+audit_status\s*=\s*'pending'"),
        ("独占授权部分唯一索引", r"unique\s+index[\s\S]{0,120}license_type\s*=\s*'exclusive'"),
        ("asset 权属变更触发器", r"after\s+insert\s+or\s+update\s+or\s+delete\s+on\s+asset"),
        ("审计日志防篡改触发器", r"before\s+update\s+or\s+delete\s+on\s+system_audit_log"),
        ("CHECK 约束 (哈希格式)", r"file_hash\s*~\s*'\^\[0-9a-f\]\{64\}\$'"),
        ("CHECK 约束 (枚举取值)", r"check\s*\(state\s+in\s*\("),
        ("传承人列级授权 (不含 right_level)", r"grant\s+update\s*\([^)]*\)\s*\n?\s*on\s+asset\s+to\s+ich_heritor"),
        ("专家可改 right_level", r"grant\s+update\s*\([^)]*right_level[^)]*\)\s*on\s+asset\s+to\s+ich_expert"),
        # 这两条是一对：传承人确实需要 audit_status 的写权限（草稿、提交待审），
        # 所以这一列堵不住 —— 堵它的必须是触发器。任何"把 audit_status 从 GRANT 里
        # 删掉"的改法都会让上传链路 42501，而只删权限不加闸门则会留下自我定级的口子。
        ("传承人对 audit_status 有写权限（故需闸门）",
         r"grant\s+update\s*\([^)]*audit_status[^)]*\)\s*\n?\s*on\s+asset\s+to\s+ich_heritor"),
        ("审核状态角色闸门", r"before\s+insert\s+or\s+update\s+on\s+asset\s+for\s+each\s+row\s+execute\s+function\s+fn_asset_status_guard"),
        ("谱系 DAG 环检测触发器", r"before\s+insert\s+or\s+update\s+on\s+asset_genealogy\s+for\s+each\s+row\s+execute\s+function\s+fn_genealogy_no_cycle"),
    ]
    for label, pat in checks:
        if re.search(pat, view_sql, re.I | re.S):
            print(f"  {GREEN}✓{RESET} {label}")
        else:
            failures += 1
            print(f"  {RED}✗{RESET} {label} 未找到")

    print("\n" + "=" * 74)
    if failures:
        print(f"{RED}校验失败：{failures} 项问题{RESET}")
        return 1
    print(f"{GREEN}全部校验通过{RESET}")
    print("提醒：这是静态结构检查，不能替代真实 PG 执行。")
    print("      真库侧已由 python -m tests.check_pg 覆盖（50 项，需库已启动）；")
    print("      重建便携库：python -m scripts.pg_run --reset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
