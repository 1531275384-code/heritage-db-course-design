#!/usr/bin/env python3
"""一键把项目跑起来（真库 / 演示模式自动判定）。

这个脚本解决的是「从零到能打开浏览器」这一段最容易卡住的路：
很多人不是不会看代码，而是卡在
    「要先建库吗？先跑哪个 SQL？pgvector 没装会不会失败？端口被占了怎么办？」

用法：
    python bootstrap.py                # 自动：有库就用库，没库就演示模式
    python bootstrap.py --strict       # 严格要求真库：连不上直接失败，不降级
    python bootstrap.py --check        # 只做环境自检，不启动服务

它会依次做：
    1. 检查 Python 依赖（缺什么直接告诉你 pip 装什么）
    2. 探测数据库（TCP + 认证 + 表是否建好 + pgvector 是否有）
    3. 按探测结果写日志、给出**该执行哪条命令**的明确提示
    4. 启动 uvicorn（真库 / 演示模式自动切换）
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

OK = "  \033[32m✓\033[0m"
BAD = "  \033[31m✗\033[0m"
WARN = "  \033[33m!\033[0m"
DIM = "\033[2m"
RESET = "\033[0m"


def hr(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")
    print("─" * 66)


# =============================================================================
#  1. 依赖
# =============================================================================
def check_deps() -> bool:
    hr("1 / 4  检查 Python 依赖")
    # 与 requirements.txt 对齐：只列真正 import 的模块
    required = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn[standard]",
        "psycopg2": "psycopg2-binary",
        "multipart": "python-multipart",     # FastAPI 解析上传表单，缺了上传接口 500
        "PIL": "pillow",
        "numpy": "numpy",
        "pydantic": "pydantic>=2",
    }
    missing = []
    for mod, pkg in required.items():
        try:
            __import__(mod)
            print(f"{OK} {mod}")
        except ImportError:
            print(f"{BAD} {mod}  →  pip install {pkg}")
            missing.append(pkg)

    if missing:
        print(f"\n  {WARN} 缺少依赖，无法启动。请执行：")
        print(f"    pip install -r requirements.txt")
        return False
    return True


# =============================================================================
#  2. 数据库探测
# =============================================================================
def probe_db() -> dict:
    """
    返回：{
        tcp: bool        TCP 端口是否可连
        auth: bool       能否用配置的账号登进去
        tables: int      已建好的业务表数量（0~8）
        vector: bool     是否装了 pgvector
        error: str       失败原因
    }
    """
    from app.config import settings

    out = {"tcp": False, "auth": False, "tables": 0, "vector": False, "error": ""}

    # ① TCP
    try:
        with socket.create_connection((settings.DB_HOST, settings.DB_PORT), timeout=3):
            out["tcp"] = True
    except OSError as e:
        out["error"] = f"连不上 {settings.DB_HOST}:{settings.DB_PORT}（{e.__class__.__name__}）"
        return out

    # ② 认证 + ③ 表 + ④ pgvector
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=settings.DB_HOST, port=settings.DB_PORT, dbname=settings.DB_NAME,
            user=settings.DB_USER, password=settings.DB_PASSWORD,
            connect_timeout=5,
        )
        out["auth"] = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name IN "
                "('app_user','public_motif','asset','feature_vector',"
                " 'audit_record','license_record','asset_genealogy','system_audit_log')"
            )
            out["tables"] = cur.fetchone()[0]
            cur.execute("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector')")
            out["vector"] = cur.fetchone()[0]
        conn.close()
    except Exception as e:  # noqa: BLE001
        msg = str(e).strip().splitlines()[0] if str(e).strip() else e.__class__.__name__
        out["error"] = msg
    return out


def report_db(info: dict) -> str:
    """
    返回模式判定：'real'（可直连）、'need_init'（库在但没建表）、'none'（用不了）
    """
    from app.config import settings

    hr("2 / 4  探测 PostgreSQL")
    print(f"{DIM}  目标：{settings.DB_USER}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}{RESET}")

    if not info["tcp"]:
        print(f"{BAD} {info['error']}")
        print(f"\n  {WARN} 这台机器上没有可用的 PostgreSQL。有两种走法：")
        print("     A. 装一个（推荐 Docker，最省事，自带 pgvector）：")
        print("        docker run -d --name ich-pg -p 5432:5432 \\")
        print("          -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=ich_heritage \\")
        print("          pgvector/pgvector:pg16")
        print("     B. 先看演示模式（内存存储，界面功能一样，但没有数据库约束验证）：")
        print("        python bootstrap.py --demo")
        return "none"

    print(f"{OK} TCP 可连")
    if not info["auth"]:
        print(f"{BAD} 认证失败：{info['error']}")
        print(f"  {WARN} 检查 .env 里的 ICH_DB_USER / ICH_DB_PASSWORD，或数据库是否允许该用户从本机连接")
        return "none"
    print(f"{OK} 认证通过")

    if info["tables"] < 8:
        print(f"{WARN} 已建业务表 {info['tables']}/8 —— 还没初始化")
        print(f"\n  下一步请依次执行（注意顺序，02 依赖 01 的对象）：")
        print(f"    psql -d {settings.DB_NAME} -v ON_ERROR_STOP=1 -f sql/01_init.sql")
        print(f"    psql -d {settings.DB_NAME} -v ON_ERROR_STOP=1 -f sql/02_index_trigger_view.sql")
        print(f"    psql -d {settings.DB_NAME} -v ON_ERROR_STOP=1 -f sql/03_seed.sql")
        print(f"    python -m app.cli backfill-motif-vectors")
        return "need_init"

    print(f"{OK} 8 张业务表齐全")
    if info["vector"]:
        print(f"{OK} pgvector 已安装 —— 走原生 VECTOR(512) + HNSW 索引")
    else:
        print(f"{WARN} 未安装 pgvector —— 降级为 NUMERIC[] + 精确扫描")
        print(f"  {DIM}功能完全等价，但没有 HNSW 索引，大数据量下检索会慢。{RESET}")
        print(f"  {DIM}想要 HNSW：装 pgvector 后重跑 sql/00_compat_vector.sql 再重跑 01/02。{RESET}")
    return "real"


# =============================================================================
#  3. AI 后端
# =============================================================================
def check_ai() -> None:
    hr("3 / 4  AI 特征后端")
    from app.ai.features import current_backend_info

    info = current_backend_info()
    print(f"  当前后端：{info['backend']}  （深度模型={info['is_deep_model']}）")
    if not info["is_deep_model"]:
        print(f"  {WARN} 未安装 torch，使用内置手工特征后端（HOG + HSV + Laplacian）")
        print(f"  {DIM}这是设计内的降级方案，不是错误：无需 GPU、512 维、可复现。{RESET}")
        print(f"  {DIM}想要 CLIP：pip install -r requirements-deep.txt（约 2GB）。{RESET}")
    else:
        print(f"{OK} 深度模型可用")


# =============================================================================
#  4. 启动
# =============================================================================
def serve(mode: str, port: int, strict: bool) -> int:
    hr("4 / 4  启动服务")

    if mode == "real":
        os.environ["ICH_DEMO_MODE"] = "0"
        print(f"{OK} 数据库模式：真实 PostgreSQL")
    elif strict:
        print(f"{BAD} --strict 要求真库，但数据库不可用。已中止（不降级）。")
        return 1
    else:
        os.environ["ICH_DEMO_MODE"] = "1"
        print(f"{WARN} 演示模式：内存存储（数据退出即丢失）")

    # 演示数据规模：让人一眼知道"打开就有东西看"，而不是空表
    seed_info = ""
    seed_file = ROOT / "data" / "seed_demo.json"
    if mode in ("none", "demo") and seed_file.exists():
        try:
            import json as _json
            d = _json.loads(seed_file.read_text(encoding="utf-8"))
            seed_info = (f"　种子：{len(d.get('users', []))} 账号 / "
                         f"{len(d.get('motifs', []))} 母题 / "
                         f"{len(d.get('assets', []))} 作品")
            print(f"{OK} 演示种子已就绪{seed_info}")
        except Exception as exc:  # noqa: BLE001
            print(f"{WARN} 种子文件解析失败，将以空库启动：{exc}")

    print(f"\n  打开 →  \033[36mhttp://127.0.0.1:{port}/\033[0m")
    print(f"  {DIM}账号 heritor01 / heritor02 / expert01 / admin01，口令均为 Passw0rd!{RESET}")
    print(f"  {DIM}Ctrl+C 停止{RESET}\n")

    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, reload=False)
    return 0


# =============================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="一键启动非遗数字资产确权系统")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--demo", action="store_true", help="强制演示模式（不探测数据库）")
    ap.add_argument("--strict", action="store_true", help="要求真库，连不上就失败")
    ap.add_argument("--check", action="store_true", help="只自检，不启动")
    args = ap.parse_args()

    print("\033[1m非遗数字资产分级确权与溯源系统 —— 启动自检\033[0m")
    print(f"{DIM}工作目录：{ROOT}{RESET}")

    if not check_deps():
        return 1

    if args.demo:
        mode = "none"
        hr("2 / 4  数据库")
        print(f"{WARN} --demo：已跳过探测，直接使用演示模式")
    else:
        mode = report_db(probe_db())

    check_ai()

    if args.check:
        hr("自检结果")
        verdict = {"real": "可以连真实数据库启动", "need_init": "库在，但还没初始化",
                   "none": "数据库不可用"}[mode]
        print(f"  结论：{verdict}")
        if mode == "real":
            print(f"  启动：python bootstrap.py")
        return 0

    return serve(mode, args.port, args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
