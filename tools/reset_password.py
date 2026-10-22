"""重置面板登录密码。

用法:
  reset_password.bat            -> 重置为 admin123
  reset_password.bat 新密码      -> 重置为指定密码
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import auth, config  # noqa: E402


def main() -> None:
    new = sys.argv[1].strip() if len(sys.argv) > 1 else "admin123"
    if len(new) < 6:
        print("[error] 密码至少 6 位")
        sys.exit(1)
    cfg = config.ensure_initialized()
    cfg["app"]["password_hash"] = auth.hash_password(new)
    cfg["app"]["bound"] = True
    config.save(cfg)
    print(f"[ok] 登录密码已重置为: {new}")


if __name__ == "__main__":
    main()
