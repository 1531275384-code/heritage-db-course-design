"""在没有 psql 客户端的环境里执行 sql/*.sql。

为什么需要它
------------
项目里的 SQL 用了 psql 的元命令（\\i 引入、\\if 条件分支、\\gset 取值、\\set 变量），
目的是让「装了 pgvector」与「没装」两种情况共用同一份脚本。但很多机器（含本机）
只有 PostgreSQL 服务端二进制、没有 psql，直接喂给驱动会报语法错。

本脚本实现一个最小的 psql 预处理器，覆盖项目实际用到的 5 个元命令：

    \\echo  \\set  \\i  \\if/\\else/\\endif  \\gset

用法
----
    # 从空库重建（先 DROP SCHEMA public CASCADE）
    python -m scripts.pg_run --reset

    # 指定连接串与文件
    python -m scripts.pg_run --dsn "host=127.0.0.1 port=55432 user=postgres dbname=heritage" \
        sql/01_init.sql sql/02_index_trigger_view.sql sql/03_seed.sql

让 Web 服务也用上这个库
----------------------
便携库的端口和库名与 `.env` 里的默认值（5432 / ich_heritage）不同，
直接 `python bootstrap.py` 会去连 5432 然后报"数据库不可用"。要覆盖两个变量：

    # Windows (Git Bash) / macOS / Linux
    ICH_DB_PORT=55432 ICH_DB_NAME=heritage python bootstrap.py --strict

    # Windows (PowerShell)
    $env:ICH_DB_PORT=55432; $env:ICH_DB_NAME=heritage; python bootstrap.py --strict

`tests/check_pg.py` 不读 `.env`，它自己默认连 55432/heritage，所以不需要覆盖。

设计取舍
--------
1. **全部文件放在一个事务里**，最后统一 COMMIT。
   好处是「要么全建成、要么什么都没有」，失败后不需要手工收拾残局；
   代价是不能执行 CREATE INDEX CONCURRENTLY / CREATE DATABASE 这类语句——
   本项目没有用到，若将来用到需改为按文件提交。
2. **语句切分必须理解引号与注释**。不能简单按分号 split：函数体是
   $$ ... ; ... $$ 包起来的，里面全是分号；注释里的分号同样不能切。
3. **不追求完整实现 psql**。只支持本项目实际用到的语法，遇到未知元命令
   直接报错而不是静默跳过——静默跳过会让「没执行」看起来像「执行成功」。
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import psycopg2

BASE = Path(__file__).resolve().parent.parent
SQL_DIR = BASE / "sql"

DEFAULT_DSN = "host=127.0.0.1 port=55432 user=postgres dbname=heritage"
DEFAULT_FILES = ["01_init.sql", "02_index_trigger_view.sql", "03_seed.sql"]

# psql 对布尔变量的真假判定
TRUE_WORDS = {"t", "true", "1", "on", "yes", "y"}
FALSE_WORDS = {"f", "false", "0", "off", "no", "n"}

# 变量引用：:name 或 :'name'；用 (?<!:) 避开 x::int 这类类型转换
VAR_RE = re.compile(r"(?<!:):('?)([A-Za-z_]\w*)\1")


class SqlError(RuntimeError):
    """SQL 执行失败，携带足够的上下文便于定位。"""


class PsqlError(RuntimeError):
    """元命令无法处理。"""


def strip_comments(text: str) -> str:
    """去掉 -- 行注释与 /* */ 块注释，但保留字符串与美元引用里的内容。

    只用于判断「这条语句是不是只有注释」。驱动对纯注释语句会报
    「can't execute an empty query」，而脚本里的分节标题恰好都是注释，
    按分号切分时会被切成一条独立语句。
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]

        if ch == "-" and text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j == -1 else j + 1
            out.append("\n")
            continue

        if ch == "/" and text.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/*", j):
                    depth += 1
                    j += 2
                elif text.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            i = j
            continue

        if ch == "$":
            m = re.match(r"\$([A-Za-z_]\w*)?\$", text[i:])
            if m:
                tag = m.group(0)
                j = text.find(tag, i + len(tag))
                j = n if j == -1 else j + len(tag)
                out.append(text[i:j])
                i = j
                continue

        if ch in "'\"":
            quote = ch
            j = i + 1
            while j < n:
                if text[j] == quote:
                    if j + 1 < n and text[j + 1] == quote:
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(text[i:j])
            i = j
            continue

        out.append(ch)
        i += 1
    return "".join(out)


def truthy(value: str) -> bool:
    v = (value or "").strip().strip("'\"").lower()
    if v in TRUE_WORDS:
        return True
    if v in FALSE_WORDS:
        return False
    raise PsqlError(f"\\if 的条件无法判定真假：{value!r}")


def substitute(sql: str, variables: dict[str, str]) -> str:
    """把 :name / :'name' 替换成变量值。未定义的变量原样保留，便于报错时看出来。"""

    def repl(m: re.Match) -> str:
        quoted, name = m.group(1), m.group(2)
        if name not in variables:
            return m.group(0)
        val = variables[name]
        return f"'{val}'" if quoted else val

    return VAR_RE.sub(repl, sql)


class Runner:
    def __init__(self, conn, verbose: bool = True, dry_run: bool = False):
        self.conn = conn
        self.cur = conn.cursor() if conn is not None else None
        self.variables: dict[str, str] = {}
        self.verbose = verbose
        self.dry_run = dry_run
        self.count = 0
        self.skipped = 0

    # ------------------------------------------------------------------ 执行
    def execute(self, sql: str, want_gset: bool = False) -> None:
        sql = substitute(sql, self.variables)
        if not sql.strip():
            return
        if self.dry_run:
            self.count += 1
            head = " ".join(strip_comments(sql).split())[:110]
            print(f"  [{self.count:03d}] {head}")
            return
        try:
            self.cur.execute(sql)
        except Exception as exc:  # noqa: BLE001 - 需要原始错误信息
            head = " ".join(sql.split())[:220]
            raise SqlError(f"{exc}\n  —— 出错的语句：{head}") from exc

        self.count += 1
        if want_gset:
            row = self.cur.fetchone()
            cols = [d[0] for d in self.cur.description] if self.cur.description else []
            for name, val in zip(cols, row or ()):
                if isinstance(val, bool):
                    self.variables[name] = "t" if val else "f"
                elif val is None:
                    self.variables[name] = ""
                else:
                    self.variables[name] = str(val)
            if self.verbose:
                print(f"    \\gset {dict(zip(cols, row or ()))}")

    # ------------------------------------------------------------------ 主循环
    def run_file(self, path: Path, depth: int = 0) -> None:
        if depth > 8:
            raise PsqlError(f"\\i 嵌套过深，疑似循环引用：{path}")
        text = path.read_text(encoding="utf-8")
        if self.verbose:
            print(f"\n>>> {path.name}（{depth=}）")

        buf: list[str] = []
        cond: list[bool] = []          # True=当前分支生效
        i, n = 0, len(text)
        at_line_start = True

        def skipping() -> bool:
            return any(not c for c in cond)

        def flush(terminator: str = ";") -> None:
            raw = "".join(buf).strip()
            buf.clear()
            if not raw:
                return
            if skipping():
                self.skipped += 1
                return
            # 只有注释的片段（分节标题）不送进驱动，否则报空查询
            if not strip_comments(raw).strip():
                return
            self.execute(raw, want_gset=(terminator == "gset"))

        while i < n:
            ch = text[i]

            # ---- 注释 ----
            if ch == "-" and text.startswith("--", i):
                j = text.find("\n", i)
                j = n if j == -1 else j
                buf.append(text[i:j])
                i = j
                continue

            if ch == "/" and text.startswith("/*", i):
                depth_c, j = 1, i + 2
                while j < n and depth_c:
                    if text.startswith("/*", j):
                        depth_c += 1
                        j += 2
                    elif text.startswith("*/", j):
                        depth_c -= 1
                        j += 2
                    else:
                        j += 1
                buf.append(text[i:j])
                i = j
                continue

            # ---- 美元引用：$$ ... $$ 或 $tag$ ... $tag$ ----
            if ch == "$":
                m = re.match(r"\$([A-Za-z_]\w*)?\$", text[i:])
                if m:
                    tag = m.group(0)
                    j = text.find(tag, i + len(tag))
                    j = n if j == -1 else j + len(tag)
                    buf.append(text[i:j])
                    i = j
                    at_line_start = False
                    continue

            # ---- 单引号字符串（'' 转义）----
            if ch == "'":
                j = i + 1
                while j < n:
                    if text[j] == "'":
                        if j + 1 < n and text[j + 1] == "'":
                            j += 2
                            continue
                        j += 1
                        break
                    j += 1
                buf.append(text[i:j])
                i = j
                at_line_start = False
                continue

            # ---- 双引号标识符（"" 转义）----
            if ch == '"':
                j = i + 1
                while j < n:
                    if text[j] == '"':
                        if j + 1 < n and text[j + 1] == '"':
                            j += 2
                            continue
                        j += 1
                        break
                    j += 1
                buf.append(text[i:j])
                i = j
                at_line_start = False
                continue

            # ---- \gset 是语句终止符，可能出现在行中间
            #      （写法是 SELECT ... \gset，不能只按行首识别，否则变量取不到、
            #        后面的 \if 会拿到未替换的原字面量而报错）
            if ch == "\\" and text.startswith("\\gset", i):
                flush("gset")
                i += 5
                at_line_start = False
                continue

            # ---- 元命令（只认行首反斜杠，且不在任何引用上下文里）----
            if ch == "\\" and at_line_start:
                j = text.find("\n", i)
                j = n if j == -1 else j
                line = text[i:j].strip()
                i = j
                self.handle_meta(line, path, cond, depth, flush)
                continue

            # ---- 语句结束 ----
            if ch == ";":
                flush(";")
                i += 1
                at_line_start = False
                continue

            if ch == "\n":
                at_line_start = True
            elif ch not in " \t\r":
                at_line_start = False

            buf.append(ch)
            i += 1

        flush(";")

    # ------------------------------------------------------------------ 元命令
    def handle_meta(self, line: str, path: Path, cond: list[bool],
                    depth: int, flush) -> None:
        parts = line.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "\\if":
            cond.append(truthy(substitute(arg, self.variables)))
            return
        if cmd == "\\else":
            if not cond:
                raise PsqlError("\\else 没有对应的 \\if")
            cond[-1] = not cond[-1]
            return
        if cmd == "\\endif":
            if not cond:
                raise PsqlError("\\endif 没有对应的 \\if")
            cond.pop()
            return

        if any(not c for c in cond):
            # 处于被跳过的分支里，其余元命令一律忽略
            return

        if cmd == "\\echo":
            print("   ", arg.strip("'"))
            return

        if cmd == "\\set":
            bits = arg.split(None, 1)
            if not bits:
                raise PsqlError("\\set 缺少变量名")
            name = bits[0]
            val = bits[1].strip() if len(bits) > 1 else ""
            if len(val) >= 2 and val[0] == val[-1] == "'":
                val = val[1:-1].replace("''", "'")
            self.variables[name] = val
            return

        if cmd == "\\i":
            include = Path(arg.strip("'"))
            if not include.is_absolute():
                include = path.parent / include
            flush(";")
            self.run_file(include, depth + 1)
            return

        if cmd == "\\gset":
            flush("gset")
            return

        raise PsqlError(f"不支持的元命令：{line[:60]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="在没有 psql 的环境里执行 sql/*.sql")
    ap.add_argument("files", nargs="*", help="要执行的 sql 文件；默认 01/02/03")
    ap.add_argument("--dsn", default=DEFAULT_DSN, help="libpq 连接串")
    ap.add_argument("--reset", action="store_true", help="先 DROP SCHEMA public CASCADE 再重建")
    ap.add_argument("--quiet", action="store_true", help="只输出结果，不输出过程")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印切分出的语句，不连库。用于排查切分错误")
    args = ap.parse_args(argv)

    files = [Path(f) for f in args.files] if args.files else [SQL_DIR / f for f in DEFAULT_FILES]
    for f in files:
        if not f.exists():
            print(f"文件不存在：{f}", file=sys.stderr)
            return 2

    verbose = not args.quiet

    if args.dry_run:
        runner = Runner(None, verbose=verbose, dry_run=True)
        for f in files:
            try:
                runner.run_file(f)
            except PsqlError as exc:
                print(f"\n元命令错误：{exc}", file=sys.stderr)
                return 1
        print(f"\n共 {runner.count} 条语句")
        return 0

    t0 = time.perf_counter()
    conn = psycopg2.connect(args.dsn)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            if args.reset:
                cur.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
                if verbose:
                    print("已重置 schema public")

        runner = Runner(conn, verbose=verbose)
        for f in files:
            runner.run_file(f)

        conn.commit()
        elapsed = time.perf_counter() - t0
        print(f"\n完成：执行 {runner.count} 条语句，跳过 {runner.skipped} 条（未生效分支），"
              f"耗时 {elapsed:.2f}s")
        return 0
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"\n已回滚。失败原因：\n{exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
