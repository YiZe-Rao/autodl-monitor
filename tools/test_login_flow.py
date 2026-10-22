"""联调测试：用假的子进程模拟「无头登录 -> 需要滑块 -> 用户拖完 -> 成功」，验证接口链路。

不访问 AutoDL，也不产生真实登录尝试。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402


class FakeProc:
    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def fake_spawn(visible: bool, phone: str, password: str) -> FakeProc:
    if not visible:
        m.LOGIN_RESULT.write_text(
            json.dumps({"status": "captcha", "detail": "需要人机验证（滑块）"}), encoding="utf-8"
        )
    else:
        def finish():
            time.sleep(1.2)
            m.LOGIN_RESULT.write_text(
                json.dumps({"status": "ok", "detail": "登录成功"}), encoding="utf-8"
            )
        threading.Thread(target=finish, daemon=True).start()
    return FakeProc()


def main() -> int:
    m._spawn_login = fake_spawn
    m.LOGIN_RESULT.unlink(missing_ok=True)

    with TestClient(m.app) as client:
        info = client.get("/api/site-info").json()
        print("site-info:", info)

        r = client.post("/api/login", json={"username": "13437010521", "password": "secret123"})
        print("login:", r.status_code, r.json())
        assert r.status_code == 202, "应返回 202（需要滑块）"
        sid = r.json()["sid"]

        state = client.get(f"/api/login/status?sid={sid}").json()
        print("status(立刻):", state)
        assert state["status"] in ("running", "ok")

        time.sleep(2.0)
        state = client.get(f"/api/login/status?sid={sid}").json()
        print("status(2s):", state)
        assert state["status"] == "ok", "滑块完成后应返回 ok"
        token = state["token"]

        dash = client.get("/api/state", headers={"Authorization": f"Bearer {token}"})
        print("dashboard auth:", dash.status_code)
        assert dash.status_code == 200

        bad = client.post("/api/login", json={"username": "123", "password": "x"})
        print("bad phone:", bad.status_code, bad.json())
        assert bad.status_code == 400

    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
