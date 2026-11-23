#!/usr/bin/env python3
"""
真库一键初始化：docker compose 起库 → 等 healthy → 建表 → 回填向量 → 自检。

为什么单独做一个脚本：
  README §1.0 那 5 条命令是对的，但实际操作时有三个坑：
    1. `docker compose up -d` 返回时容器**还没准备好**，立刻 psql 会 Connection refused
    2. 建表脚本必须严格按 01 → 02 → 03 顺序，02 依赖 01 的对象，顺序错了要重来
    3. 建完表还得回填母题向量，否则 AI 全判 original —— 这一步最容易忘

  这个脚本把「等就绪」和「按顺序执行」固化下来，避免每次重装环境都要重新踩一遍。

用法：
    python scripts/init_db.py                 # 完整：起库 + 建表 + 回填
    python scripts/init_db.py --skip-up       # 库已在跑，只建表 + 回填
    python scripts/init_db.py --reset         # 删库重来（down -v 后重建）
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK = "  \033[32m✓\033[0m"
BAD = "  \033[31m✗\033[0m"
WARN = "  \033[33m!\033[0m"
DIM = "\033[2m"
RESET = "\033[0m"

SQL_ORDER = ["01_init.sql", "02_index_trigger_view.sql", "03_seed.sql"]


def hr(t: str) -> None:
    print(f"\n\033[1m{t}\033[0m")
    print("─" * 66)


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd or ROOT), check=check,
                          capture_output=True, text=True)


def docker_available() -> bool:
    return shutil.which("docker") is not None


def compose_up(reset: bool) -> bool:
    hr("1 / 5 启动数据库容器")
    if not docker_available():
        print(f"{BAD} 找不到 docker 命令")
        print("  请先安装 Docker Desktop，或改用手工方式（README §1.1）")
        return False

    if reset:
        print(f"{WARN} --reset：正在删除旧数据卷 ...")
        run(["docker", "compose", "down", "-v"], check=False)

    r = run(["docker", "compose", "up", "-d"], check=False)
    if r.returncode != 0:
        print(f"{BAD} docker compose up 失败：")
        print((r.stderr or r.stdout).strip()[:800])
        return False
    print(f"{OK} 容器已创建")
    return True


def wait_healthy(timeout: int = 90) -> bool:
    """轮询 pg_isready。容器起来 ≠ 数据库可接受连接，必须等。"""
    hr("2 / 5 等待数据库就绪")
    from app.config import settings

    t0 = time.time()
    while time.time() - t0 < timeout:
        r = run(
            ["docker", "exec", "ich-pg", "pg_isready", "-U", "postgres", "-d", settings.DB_NAME],
            check=False,
        )
        if r.returncode == 0:
            print(f"{OK} 就绪（耗时 {time.time() - t0:.1f}s）")
            return True
        print(f"{DIM}  ...等待中 {time.time() - t0:.0f}s{RESET}", end="\r", flush=True)
        time.sleep(2)

    print(f"{BAD} 等待超时（{timeout}s）。查看日志：docker compose logs db")
    return False


def find_psql() -> str | None:
    """
    优先用容器内的 psql（版本一定匹配），本机有也可以用。
    这样做的好处：用户不用额外装 PostgreSQL 客户端。
    """
    if docker_available():
        r = run(["docker", "exec", "ich-pg", "which", "psql"], check=False)
        if r.returncode == 0:
            return "docker"
    if shutil.which("psql"):
        return "local"
    return None


def exec_sql(sql_file: Path, mode: str) -> bool:
    """执行单个 SQL 文件。容器模式要把文件挂进容器或用 stdin 喂。"""
    from app.config import settings

    if mode == "docker":
        # 用 stdin 传入：避免容器内没有工程目录的路径问题
        with sql_file.open("rb") as fh:
            r = subprocess.run(
                ["docker", "exec", "-i", "ich-pg", "psql",
                 "-U", "postgres", "-d", settings.DB_NAME, "-v", "ON_ERROR_STOP=1"],
                stdin=fh, cwd=str(ROOT), capture_output=True, text=True,
            )
    else:
        env = {"PGPASSWORD": settings.DB_PASSWORD}
        import os
        r = subprocess.run(
            ["psql", "-h", settings.DB_HOST, "-p", str(settings.DB_PORT),
             "-U", settings.DB_USER, "-d", settings.DB_NAME,
             "-v", "ON_ERROR_STOP=1", "-f", str(sql_file)],
            cwd=str(ROOT), capture_output=True, text=True, env={**os.environ, **env},
        )
    if r.returncode != 0:
        print(f"{BAD} {sql_file.name} 执行失败：")
        err = (r.stderr or "").strip()
        # 只打印有意义的错误行，psql 的 NOTICE 太多
        for line in err.splitlines():
            if line.startswith("psql:") or "ERROR" in line or "错误" in line:
                print(f"    {line}")
        return False
    return True


def init_schema() -> bool:
    hr("3 / 5 建表 / 索引 / 触发器 / 视图 / 权限 / 种子")

    mode = find_psql()
    if mode is None:
        print(f"{BAD} 既没有容器内 psql，也没有本机 psql")
        return False
    print(f"{DIM}  psql 来源：{'容器 ich-pg' if mode == 'docker' else '本机'}{RESET}")

    for name in SQL_ORDER:
        p = ROOT / "sql" / name
        if not p.exists():
            print(f"{BAD} 缺少 {p}")
            return False
        print(f"  → {name} ...", end=" ", flush=True)
        if not exec_sql(p, mode):
            return False
        print("OK")
    return True


def check_vector_mode() -> None:
    """打印到底走了原生 pgvector 还是降级模式 —— 这是评审最关心的信息之一。"""
    hr("4 / 5 向量层自检")
    from app.config import settings

    try:
        import psycopg2

        conn = psycopg2.connect(
            host=settings.DB_HOST, port=settings.DB_PORT, dbname=settings.DB_NAME,
            user=settings.DB_USER, password=settings.DB_PASSWORD, connect_timeout=5,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT ich_vector_is_native()")
            native = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM pg_indexes WHERE indexname LIKE '%hnsw%'"
            )
            hnsw = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM public_motif WHERE base_vector IS NOT NULL")
            motifs = cur.fetchone()[0]
        conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"{BAD} 查询失败：{e}")
        return

    if native:
        print(f"{OK} pgvector 原生模式")
        print(f"{OK} HNSW 索引数量：{hnsw}")
    else:
        print(f"{WARN} 降级模式（无 pgvector）：检索走精确扫描，无 HNSW")
        print(f"{DIM}  功能等价，但不要用此模式的 EXPLAIN 证明「HNSW 生效」{RESET}")
    print(f"{OK} 已有向量的母题数：{motifs}")


def backfill() -> bool:
    hr("5 / 5 回填母题特征向量")
    print(f"{DIM}  种子里的向量是占位值，不回填的话 AI 会把一切都判成 original{RESET}")
    r = subprocess.run(
        [sys.executable, "-m", "app.cli", "backfill-motif-vectors"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"{BAD} 回填失败：")
        print((r.stderr or r.stdout).strip()[-800:])
        return False
    for line in r.stdout.strip().splitlines():
        if line.strip():
            print(f"  {line.strip()}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="真库一键初始化")
    ap.add_argument("--skip-up", action="store_true", help="库已在跑，跳过 docker compose up")
    ap.add_argument("--reset", action="store_true", help="删除数据卷后重建（会丢数据）")
    ap.add_argument("--skip-backfill", action="store_true", help="跳过母题向量回填")
    args = ap.parse_args()

    print("\033[1m非遗确权系统 —— 真库初始化\033[0m")

    if not args.skip_up:
        if not compose_up(args.reset):
            return 1
    else:
        hr("1 / 5 启动数据库容器")
        print(f"{DIM}  --skip-up：跳过{RESET}")

    if not wait_healthy():
        return 1
    if not init_schema():
        return 1

    check_vector_mode()

    if not args.skip_backfill:
        if not backfill():
            return 1

    hr("完成")
    from app.config import settings
    print(f"  数据库  {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")
    print(f"  下一步  \033[36mpython bootstrap.py\033[0m  （会自动识别为真库模式）")
    print(f"  {DIM}账号 heritor01 / expert01 / admin01，口令 Passw0rd!{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
