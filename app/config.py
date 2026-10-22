"""配置管理：首次运行自动生成 data/config.json。"""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
WEB_DIR = BASE_DIR / "web"
CONFIG_PATH = DATA_DIR / "config.json"
STORAGE_STATE_PATH = DATA_DIR / "storage_state.json"
LOGIN_PROFILE_DIR = DATA_DIR / "login-profile"

# 用户提供的 XPath：实例列表每一行第 5 列里，div/div[2] 即 CPU/GPU 利用率那个节点
DEFAULT_XPATH = (
    '//div[contains(concat(" ", normalize-space(@class), " "), " is-scrolling-none ")]'
    '/table/tbody/tr/td[5]/div/div[2]'
)

DEFAULTS: dict[str, Any] = {
    "app": {
        "host": "127.0.0.1",
        "port": 8000,
        "username": "admin",
        "password_hash": "",
        "bound": False,
        "secret": "",
    },
    "autodl": {
        "url": "https://www.autodl.com/console/instance/list",
        "interval_sec": 15,
        "headless": True,
        "browser_channel": "chrome",
        # 表格列号（从 1 开始）
        "cpu_gpu_col": 5,
        "name_col": 1,
        "xpath": DEFAULT_XPATH,
    },
    "retention_days": 7,
}

_lock = threading.RLock()
_cache: dict[str, Any] | None = None


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(force: bool = False) -> dict[str, Any]:
    global _cache
    with _lock:
        if _cache is not None and not force:
            return _cache
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        raw: dict[str, Any] = {}
        if CONFIG_PATH.exists():
            try:
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
        cfg = _merge(DEFAULTS, raw)
        _cache = cfg
        return cfg


def save(cfg: dict[str, Any]) -> None:
    global _cache
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _cache = cfg


def ensure_initialized() -> dict[str, Any]:
    """补齐 secret / 默认密码，落盘一次。"""
    import secrets as _secrets

    from . import auth

    cfg = load(force=True)
    changed = False
    if not cfg["app"].get("secret"):
        cfg["app"]["secret"] = _secrets.token_hex(32)
        changed = True
    if not cfg["app"].get("password_hash"):
        cfg["app"]["password_hash"] = auth.hash_password("admin123")
        changed = True
    if changed or not CONFIG_PATH.exists():
        save(cfg)
    return cfg
