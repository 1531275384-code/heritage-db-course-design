"""在一个进程内启动 uvicorn（线程）→ 跑 e2e → 关闭。

WHY 要这样：本工具的执行环境会回收后台进程（`cmd &` 起的服务，
下一次 Bash 调用时已经不存在了），导致「起服务」和「测接口」必须在同一次调用里完成。
线程内起 uvicorn 比 nohup/& 更可控，也便于 CI 里一条命令跑完就退出。

用法：
    python tests/run_e2e_inline.py
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# 必须在 import app 之前设，lifespan 才读得到
os.environ.setdefault("ICH_DEMO_MODE", "1")
os.environ.setdefault("ICH_LOG_LEVEL", "WARNING")


def pick_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    import uvicorn

    from app.main import app

    port = pick_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)

    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    deadline = time.time() + 40
    while not server.started:
        if time.time() > deadline:
            print("❌ uvicorn 启动超时")
            return 2
        time.sleep(0.1)
    print(f"✅ 服务已启动：http://127.0.0.1:{port}\n")

    try:
        sys.argv = ["e2e", f"http://127.0.0.1:{port}"]
        import runpy
        runpy.run_path(str(ROOT / "tests" / "e2e_demo.py"), run_name="__main__")
        rc = 0
    except SystemExit as e:
        rc = int(e.code or 0)
    finally:
        server.should_exit = True
        t.join(timeout=10)
    return rc


if __name__ == "__main__":
    sys.exit(main())
