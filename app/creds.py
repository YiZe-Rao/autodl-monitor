"""AutoDL 账号密码的本机加密存储（Windows DPAPI，仅当前用户可解密）。"""
from __future__ import annotations

import base64
import ctypes
import json
from ctypes import wintypes
from pathlib import Path

from . import config

CRED_PATH = config.DATA_DIR / "credentials.dat"


class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    kernel32 = ctypes.windll.kernel32
    crypt32 = ctypes.windll.crypt32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _BLOB()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError(f"DPAPI {'protect' if protect else 'unprotect'} failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def save(phone: str, password: str) -> None:
    raw = json.dumps({"phone": phone, "password": password}).encode("utf-8")
    try:
        payload = b"DPAPI1" + _dpapi(raw, True)
    except Exception:  # 非 Windows 兜底（不推荐）
        payload = b"PLAIN1" + base64.b64encode(raw)
    CRED_PATH.parent.mkdir(parents=True, exist_ok=True)
    CRED_PATH.write_bytes(payload)


def load() -> tuple[str, str] | None:
    try:
        data = CRED_PATH.read_bytes()
    except OSError:
        return None
    try:
        if data.startswith(b"DPAPI1"):
            raw = _dpapi(data[6:], False)
        elif data.startswith(b"PLAIN1"):
            raw = base64.b64decode(data[6:])
        else:
            return None
        obj = json.loads(raw.decode("utf-8"))
        return obj.get("phone", ""), obj.get("password", "")
    except Exception:
        return None


def exists() -> bool:
    return CRED_PATH.exists()


def clear() -> None:
    try:
        CRED_PATH.unlink()
    except OSError:
        pass
