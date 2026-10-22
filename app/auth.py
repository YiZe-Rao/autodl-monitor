"""登录口令哈希 + 自签 token（无第三方依赖）。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

_ITER = 120_000


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _ITER)
    return f"pbkdf2${_ITER}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, iters, salt, digest = stored.split("$")
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iters)
        )
        return hmac.compare_digest(dk.hex(), digest)
    except Exception:
        return False


def _sign(raw: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()[:40]


def make_token(username: str, secret: str, ttl_sec: int = 7 * 86400) -> str:
    payload = {"u": username, "exp": int(time.time()) + ttl_sec}
    raw = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"{raw}.{_sign(raw, secret)}"


def check_token(token: str, secret: str) -> str | None:
    """返回用户名，失败返回 None。"""
    try:
        raw, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sign(raw, secret)):
            return None
        pad = "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(raw + pad))
        if int(payload.get("exp", 0)) < time.time():
            return None
        return str(payload.get("u") or "") or None
    except Exception:
        return None
