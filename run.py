"""入口：python run.py"""
from __future__ import annotations

import sys
import webbrowser

import uvicorn

from app import config
from app.main import app  # noqa: F401  (触发 import 检查)


def main() -> None:
    cfg = config.ensure_initialized()
    host = cfg["app"].get("host", "127.0.0.1")
    port = int(cfg["app"].get("port", 8000))
    url = f"http://{host}:{port}"
    print("=" * 56)
    print("  AutoDL 实例监控 已启动")
    print(f"  面板地址: {url}")
    print(f"  默认账号: {cfg['app']['username']} / admin123")
    print("  首次使用请先运行 login.bat 完成 AutoDL 登录")
    print("=" * 56)
    if "--open" in sys.argv:
        webbrowser.open(url)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
