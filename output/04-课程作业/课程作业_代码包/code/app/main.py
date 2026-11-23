"""
FastAPI 应用入口。

启动：python -m uvicorn app.main:app --reload --port 8000
或：  python run.py
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg2 import errors as pg_errors

from app import demo
from app.api import router
from app.config import BASE_DIR, STATIC_DIR, settings
from app.db import close_pool, healthy, init_pool

logging.basicConfig(
    level=os.getenv("ICH_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
)
log = logging.getLogger("ich.main")

DB_READY = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    启动时建连接池。数据库不可用时**不让进程崩掉**：
    前端仍能打开登录页，/api/health 会返回明确原因，便于排障。

    演示模式（ICH_DEMO_MODE）：
      - "0"  : 只用真实数据库，连不上就报错（生产/验收用）
      - "1"  : 强制演示模式（内存存储），完全跳过数据库
      - "auto": 默认。先试数据库，连不上则自动切到演示模式并打警告横幅

    WHY 要有 auto：本项目的价值在于"AI 只建议、专家终审、原子落库"这套业务链路。
    没有数据库时，纯粹报错什么都演示不了；而 auto 能让人先看到完整交互，
    同时界面上一眼就能看出"现在是模拟数据，数据库规则没生效"。
    """
    global DB_READY
    mode = demo.mode_setting()

    if mode == "1":
        demo.activate()
        w = demo.STORE.warm_all()
        DB_READY = False
        log.warning(
            "🧪 演示模式已启用（ICH_DEMO_MODE=1）：内存存储，种子 %s，"
            "已预热母题向量 %d 条、作品向量 %d 条",
            demo.STORE.seed_stats, w["motif_vectors"], w["asset_vectors"],
        )
        yield
        return

    try:
        init_pool()
        ok, info = healthy()
        DB_READY = ok
        if ok:
            log.info("✅ 数据库就绪：%s", info)
        else:
            log.error("❌ 数据库不可用：%s", info)
    except Exception as exc:  # noqa: BLE001
        DB_READY = False
        info = str(exc)
        log.error("❌ 连接池初始化失败：%s", exc)

    if not DB_READY and mode == "auto":
        demo.activate()
        w = demo.STORE.warm_all()
        log.warning(
            "🧪 数据库不可用 → 自动降级为演示模式：内存存储，种子 %s，"
            "已预热母题向量 %d 条、作品向量 %d 条。"
            "真实数据库的约束/触发器/GRANT/HNSW 在此模式下不生效。设 ICH_DEMO_MODE=0 可关闭该降级。",
            demo.STORE.seed_stats, w["motif_vectors"], w["asset_vectors"],
        )

    yield
    close_pool()


app = FastAPI(
    title="非遗数字资产分级确权与溯源 AI 工具",
    description=(
        "AI 特征引擎（提特征 / 相似比对 / 分级建议）+ PostgreSQL + pgvector 数据库系统"
        "（确权事务 / 溯源日志 / 权限管控）。\n\n"
        "**核心原则**：AI 只出建议，终审由专家决定并原子落库。"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# 演示环境允许本地跨域；生产应限定 Origin 白名单
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


# ---------------------------------------------------------------- 统一异常处理
@app.exception_handler(pg_errors.InsufficientPrivilege)
async def handle_db_permission(request: Request, exc: pg_errors.InsufficientPrivilege):
    """
    数据库层 GRANT 拦截 → 转成 403。
    这条路径能真正跑起来，说明「数据库级权限」在起作用，
    例如传承人角色试图直接改 right_level，或有人试图 UPDATE 审计日志。
    """
    log.warning("数据库权限拒绝 %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=403,
        content={
            "detail": "数据库层权限拒绝：当前角色无权执行该操作",
            "db_error": str(exc).strip().splitlines()[0] if str(exc).strip() else "",
        },
    )


@app.exception_handler(pg_errors.UniqueViolation)
async def handle_unique(request: Request, exc: pg_errors.UniqueViolation):
    log.warning("唯一约束冲突 %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=409,
        content={"detail": "唯一约束冲突：数据已存在", "db_error": str(exc).strip().splitlines()[0]},
    )


@app.exception_handler(pg_errors.ForeignKeyViolation)
async def handle_fk(request: Request, exc: pg_errors.ForeignKeyViolation):
    log.warning("外键约束冲突 %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=422,
        content={"detail": "外键约束冲突：引用的记录不存在，或该记录已被其他数据引用而不能删除",
                 "db_error": str(exc).strip().splitlines()[0]},
    )


@app.exception_handler(pg_errors.CheckViolation)
async def handle_check(request: Request, exc: pg_errors.CheckViolation):
    log.warning("CHECK 约束冲突 %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=422,
        content={"detail": "CHECK 约束冲突：数据不满足业务规则", "db_error": str(exc).strip().splitlines()[0]},
    )


@app.exception_handler(RuntimeError)
async def handle_no_pool(request: Request, exc: RuntimeError):
    """
    连接池未就绪 / 数据库连不上时，返回**可读的 503**，而不是 500 Internal Server Error。

    WHY：启动时 init_pool() 失败的话，_pool 仍是 None，
    之后任何请求进到 open_tx() 都会抛 RuntimeError("连接池未初始化")。
    默认行为是 500 + 一整页堆栈，用户根本看不出"其实是没连上数据库"。
    这里把它翻译成"服务暂不可用 + 具体排查步骤"，前端能把这句话直接显示在登录框下面。
    """
    if "连接池未初始化" not in str(exc):
        raise exc     # 不是这个问题就交回默认处理，不要吞掉真正的编程错误

    ok, info = healthy()
    log.warning("数据库不可用，拒绝请求 %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=503,
        content={
            "detail": (
                "数据库未就绪，无法登录。请确认：① PostgreSQL 已启动；"
                "② 已按 README 执行 sql/01_init.sql、02_index_trigger_view.sql、03_seed.sql；"
                "③ 环境变量 ICH_DB_HOST / ICH_DB_PORT / ICH_DB_NAME / ICH_DB_USER / ICH_DB_PASSWORD 正确。"
                f"（当前探测结果：{info}）"
            ),
            "db_error": info,
            "hint": "可用 python -m app.cli check 做完整自检",
        },
        headers={"Retry-After": "5"},
    )


# ---------------------------------------------------------------- 静态前端
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 母题库参考图（data/motifs/）与作品原图（data/uploads/）需要能被 <img> 直接引用。
# WHY 不走 /api/asset/{id}/image：母题参考图是**公开的公有领域素材**，不含任何
# 权利人隐私，不需要鉴权也不需要每次走一遍权限查询；而作品原图仍走带鉴权的接口。
DATA_DIR = BASE_DIR / "data"
if DATA_DIR.exists():
    app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")


@app.get("/", include_in_schema=False)
def index():
    """首页 = 演示控制台（单页应用）。"""
    idx = STATIC_DIR / "index.html"
    if not idx.exists():
        return JSONResponse({"detail": "前端未构建，请检查 app/static/index.html 是否存在"}, status_code=500)
    return FileResponse(idx)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    p = STATIC_DIR / "favicon.ico"
    if p.exists():
        return FileResponse(p)
    return JSONResponse({}, status_code=204)
