"""
本地一键启动脚本。

等价于：python -m uvicorn app.main:app --reload --port 8000

如果只是想「跑起来看看」，更推荐 python bootstrap.py —— 它会先诊断环境
（依赖/数据库/pgvector/AI 后端），再决定用真库还是演示模式。

之所以单独提供 run.py：
  1. Windows 下 `python -m uvicorn` 在某些终端里 uvicorn 的 reload 子进程
     会丢掉当前工作目录，导致 `app.main` 导入失败；这里显式写入 sys.path。
  2. 启动前先做一次轻量自检（数据库能否连通），把问题**在启动时**说清楚，
     而不是等用户点了"上传"才看到 500。

用法：
    python run.py                      # 默认 127.0.0.1:8000，开启热重载
    python run.py --port 9000          # 换端口
    python run.py --host 0.0.0.0       # 允许局域网访问
    python run.py --no-reload          # 关闭热重载（演示/生产更稳）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def preflight() -> None:
    """
    启动前自检：数据库不通时给出醒目提示，但**不阻止启动**。

    所有 print 都带 flush=True —— 否则在管道重定向（如 `python run.py > log`）
    或某些 Windows 终端下，这段提示会被块缓冲吞掉，
    用户只能看到 uvicorn 的启动日志，反而看不到最关键的错误原因。
    """
    from app.config import settings
    from app.db import close_pool, healthy, init_pool

    # 先打印 AI 后端信息会夹杂降级日志，这里统一走 _p 便于控制刷新
    def _p(msg: str = "") -> None:
        print(msg, flush=True)

    _p("=" * 70)
    _p("  非遗数字资产分级确权与溯源系统")
    _p("=" * 70)

    try:
        init_pool()
        ok, info = healthy()
    except Exception as exc:  # noqa: BLE001
        ok, info = False, f"{type(exc).__name__}: {exc}"

    if ok:
        _p(f"  ✓ 数据库   {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")
        _p(f"             {info}")
    else:
        import os as _os
        mode = _os.getenv("ICH_DEMO_MODE", "auto").strip().lower()
        _p(f"  ✗ 数据库   {info}")
        _p()
        if mode == "0":
            _p("    ICH_DEMO_MODE=0（强制真库）：数据接口会返回 503，不会降级。请检查：")
            _p("      1) PostgreSQL 是否已启动")
            _p("      2) 是否已按 README 执行 sql/01_init.sql、02、03")
            _p("      3) ICH_DB_HOST / PORT / NAME / USER / PASSWORD 是否正确")
        else:
            _p("    → 自动降级为演示模式（内存存储）。界面功能完整，但数据库约束不生效。")
            _p("      想要真库：docker compose up -d 后重跑 sql/01~03（见 README §1.0）")
            _p("      要禁止降级：设 ICH_DEMO_MODE=0")

    try:
        from app.ai.features import current_backend_info
        ai = current_backend_info()
        tag = "深度模型" if ai["is_deep_model"] else "轻量手工特征"
        _p(f"  ✓ AI 后端  {ai['backend']} v{ai['version']}  dim={ai['dim']}  [{tag}]")
    except Exception as exc:  # noqa: BLE001
        _p(f"  ✗ AI 后端  初始化失败：{type(exc).__name__}: {exc}")

    close_pool()
    _p("=" * 70)
    _p()


def main() -> int:
    parser = argparse.ArgumentParser(description="启动非遗确权系统 Web 服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    parser.add_argument("--no-reload", action="store_true", help="关闭代码热重载")
    args = parser.parse_args()

    preflight()

    import uvicorn

    shown = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    print(f"  演示页面  http://{shown}:{args.port}/", flush=True)
    print(f"  接口文档  http://{shown}:{args.port}/docs", flush=True)
    print("  按 Ctrl+C 停止\n", flush=True)

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
        reload_dirs=[str(ROOT / "app")],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
