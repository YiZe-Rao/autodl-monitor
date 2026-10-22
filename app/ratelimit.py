"""登录频率限制：最小间隔 + 每小时/每天次数上限 + 失败指数退避。

状态落盘在 data/ratelimit.json，重启不会被绕过。
"""
from __future__ import annotations

import json
import random
import threading
import time
from pathlib import Path

from . import config

STATE_PATH = config.DATA_DIR / "ratelimit.json"
_lock = threading.RLock()


class LoginLimiter:
    def __init__(
        self,
        name: str,
        min_interval: int = 300,
        per_hour: int = 3,
        per_day: int = 10,
        backoff: tuple[int, ...] = (600, 1800, 3600, 7200),
    ) -> None:
        self.name = name
        self.min_interval = min_interval
        self.per_hour = per_hour
        self.per_day = per_day
        self.backoff = backoff

    # ---------------- 状态 ----------------
    def _load(self) -> dict:
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        return data.get(self.name) or {"attempts": [], "failures": 0, "next_allowed": 0}

    def _save(self, st: dict) -> None:
        with _lock:
            try:
                data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            data[self.name] = st
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------------- 判定 ----------------
    def check(self) -> tuple[bool, str, int]:
        """返回 (是否允许, 原因, 还需等待的秒数)。"""
        with _lock:
            st = self._load()
            now = time.time()
            attempts = [a for a in st.get("attempts", []) if now - a < 86400]
            last = attempts[-1] if attempts else 0

            wait = max(0.0, st.get("next_allowed", 0) - now)
            if wait > 0:
                return False, "冷却中", int(wait) + 1

            if last and now - last < self.min_interval:
                return False, "两次尝试间隔太短", int(self.min_interval - (now - last)) + 1

            last_hour = len([a for a in attempts if now - a < 3600])
            if last_hour >= self.per_hour:
                oldest = min(a for a in attempts if now - a < 3600)
                return False, f"每小时最多 {self.per_hour} 次", int(3600 - (now - oldest)) + 1

            if len(attempts) >= self.per_day:
                return False, f"每天最多 {self.per_day} 次", int(86400 - (now - min(attempts))) + 1

            return True, "可以尝试", 0

    def record(self, ok: bool) -> None:
        with _lock:
            st = self._load()
            now = time.time()
            st["attempts"] = [a for a in st.get("attempts", []) if now - a < 86400] + [now]
            if ok:
                st["failures"] = 0
                st["next_allowed"] = now + self.min_interval
            else:
                n = int(st.get("failures", 0)) + 1
                st["failures"] = n
                base = self.backoff[min(n - 1, len(self.backoff) - 1)]
                st["next_allowed"] = now + base + random.randint(30, 180)
            self._save(st)

    def summary(self) -> dict:
        with _lock:
            st = self._load()
            now = time.time()
            attempts = [a for a in st.get("attempts", []) if now - a < 86400]
            return {
                "attempts_24h": len(attempts),
                "attempts_1h": len([a for a in attempts if now - a < 3600]),
                "failures": st.get("failures", 0),
                "next_allowed_in": max(0, int(st.get("next_allowed", 0) - now)),
            }


autodl_login_limiter = LoginLimiter(
    "autodl_login",
    min_interval=60,          # 两次登录尝试至少间隔 60 秒
    per_hour=6,
    per_day=20,
    backoff=(300, 900, 1800, 3600),
)
