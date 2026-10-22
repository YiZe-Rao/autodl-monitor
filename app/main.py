"""FastAPI 应用：登录、REST API、静态页面。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, config, creds, db, ratelimit
from . import scraper as scraper_mod

WEB_DIR = config.WEB_DIR
scraper = scraper_mod.scraper


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.ensure_initialized()
    db.init()
    scraper.start()
    yield
    scraper.stop()


app = FastAPI(title="AutoDL 实例监控", lifespan=lifespan)


def current_user(authorization: str | None = Header(default=None)) -> str:
    cfg = config.load()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    user = auth.check_token(authorization.split(" ", 1)[1].strip(), cfg["app"]["secret"])
    if not user:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return user


class LoginBody(BaseModel):
    username: str
    password: str


class SettingsBody(BaseModel):
    interval_sec: int | None = None
    cpu_gpu_col: int | None = None
    name_col: int | None = None
    xpath: str | None = None
    headless: bool | None = None


class ProbeBody(BaseModel):
    xpath: str


class PasswordBody(BaseModel):
    old_password: str
    new_password: str


def _identifier_ok(ident: str, cfg: dict[str, Any]) -> bool:
    """登录名允许：配置里的账号名，或 11 位手机号（单账号，手机号作别名）。"""
    ident = (ident or "").strip().replace(" ", "").replace("-", "")
    if not ident:
        return False
    return ident == cfg["app"]["username"] or (ident.isdigit() and len(ident) == 11)


_LOGIN_WINDOW = 600      # 统计窗口：10 分钟
_LOGIN_MAX_FAIL = 5      # 窗口内最多允许 5 次失败
_login_fail: dict[str, list[float]] = {}
_login_lock = threading.Lock()


def _client_ip(req: Request) -> str:
    return (req.client.host if req.client else "local") or "local"


def _login_guard(ip: str) -> None:
    now = time.time()
    with _login_lock:
        arr = [t for t in _login_fail.get(ip, []) if now - t < _LOGIN_WINDOW]
        _login_fail[ip] = arr
        if len(arr) >= _LOGIN_MAX_FAIL:
            wait = int(_LOGIN_WINDOW - (now - arr[0])) + 1
            raise HTTPException(
                status_code=429,
                detail=f"登录尝试过多，请 {max(1, wait // 60)} 分钟后再试",
            )


def _login_fail_add(ip: str) -> None:
    with _login_lock:
        _login_fail.setdefault(ip, []).append(time.time())


def _login_fail_clear(ip: str) -> None:
    with _login_lock:
        _login_fail.pop(ip, None)


LOGIN_RESULT = config.DATA_DIR / "login_result.json"
_login_jobs: dict[str, dict[str, Any]] = {}
_job_lock = threading.Lock()


def _spawn_login(visible: bool, phone: str, password: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["AUTODL_PHONE"] = phone
    env["AUTODL_PASSWORD"] = password
    cmd = [sys.executable, str(config.BASE_DIR / "tools" / "auto_login.py"), "--force-login"]
    if visible:
        cmd.append("--visible")
    flags = 0 if visible else getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        cmd, cwd=str(config.BASE_DIR), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
    )


def _login_result() -> dict[str, Any] | None:
    try:
        return json.loads(LOGIN_RESULT.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _issue_token(phone: str) -> str:
    return auth.make_token(phone, config.load()["app"]["secret"])


@app.get("/api/site-info")
def site_info() -> dict[str, Any]:
    return {
        "has_session": config.STORAGE_STATE_PATH.exists(),
        "has_credentials": creds.exists(),
        "scrape_status": scraper.state.get("status"),
    }


@app.get("/api/login/status")
def login_status(sid: str) -> dict[str, Any]:
    with _job_lock:
        job = _login_jobs.get(sid)
    if not job:
        raise HTTPException(status_code=404, detail="登录会话不存在或已过期")
    res = _login_result() or {}
    st = res.get("status")
    proc = job.get("proc")
    child_dead = proc is not None and proc.poll() is not None

    if st == "ok":
        creds.save(job["phone"], job["password"])
        ratelimit.autodl_login_limiter.record(True)
        job["status"] = "ok"
        scraper.trigger()
        return {"status": "ok", "token": _issue_token(job["phone"])}
    if st in ("bad_credentials", "error", "timeout", "captcha"):
        ratelimit.autodl_login_limiter.record(False)
        job["status"] = "failed"
        return {"status": "failed", "detail": res.get("detail") or "登录失败"}
    if child_dead:
        ratelimit.autodl_login_limiter.record(False)
        job["status"] = "failed"
        return {"status": "failed", "detail": "登录窗口异常退出，请重试（详见 data/login_child.log）"}
    return {"status": "running", "detail": "等待完成安全验证…"}


@app.post("/api/login")
def login(body: LoginBody, request: Request) -> Any:
    """用 AutoDL 账号密码登录：成功即拿到会话，面板直接可用。"""
    ip = _client_ip(request)
    _login_guard(ip)
    phone = (body.username or "").strip().replace(" ", "")
    password = body.password or ""
    if not (phone.isdigit() and len(phone) == 11):
        raise HTTPException(status_code=400, detail="请输入 11 位手机号")
    if not password:
        raise HTTPException(status_code=400, detail="请输入密码")

    # 账密与本机保存的一致、且已有会话：直接进入，不再打扰 AutoDL
    stored = creds.load()
    if (
        stored
        and stored[0] == phone
        and stored[1] == password
        and config.STORAGE_STATE_PATH.exists()
        and scraper.state.get("status") in ("ok", "empty", "starting")
    ):
        _login_fail_clear(ip)
        scraper.trigger()
        return {"status": "ok", "token": _issue_token(phone), "reused": True}

    allowed, why, wait = ratelimit.autodl_login_limiter.check()
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"AutoDL 登录过于频繁（{why}），请 {max(1, wait // 60)} 分钟后再试",
        )

    LOGIN_RESULT.unlink(missing_ok=True)
    proc = _spawn_login(False, phone, password)
    try:
        proc.wait(timeout=110)
    except subprocess.TimeoutExpired:
        proc.kill()
        ratelimit.autodl_login_limiter.record(False)
        raise HTTPException(status_code=504, detail="登录超时，请重试")

    res = _login_result() or {"status": "error", "detail": "登录进程没有返回结果"}
    st = res.get("status")

    if st == "ok":
        creds.save(phone, password)
        ratelimit.autodl_login_limiter.record(True)
        _login_fail_clear(ip)
        scraper.trigger()
        return {"status": "ok", "token": _issue_token(phone)}

    if st == "captcha":
        # 站点要求人机验证：打开窗口让用户自己拖滑块（不做任何绕过）
        sid = uuid.uuid4().hex
        with _job_lock:
            _login_jobs[sid] = {"phone": phone, "password": password, "status": "running"}
        LOGIN_RESULT.unlink(missing_ok=True)
        proc = _spawn_login(True, phone, password)
        with _job_lock:
            _login_jobs[sid]["proc"] = proc
        return JSONResponse(
            status_code=202,
            content={
                "status": "captcha",
                "sid": sid,
                "detail": "已打开登录窗口：请拖动滑块完成安全验证，完成后本页自动进入",
            },
        )

    ratelimit.autodl_login_limiter.record(False)
    _login_fail_add(ip)
    raise HTTPException(status_code=401, detail=res.get("detail") or "AutoDL 登录失败")


@app.get("/api/state")
def state(_user: str = Depends(current_user)) -> dict[str, Any]:
    now = int(time.time())
    instances = db.list_instances()
    st = dict(scraper.state)
    st.pop("raw", None)
    for inst in instances:
        inst["age_sec"] = now - (inst.get("last_seen") or now)
    return {
        "now": now,
        "scraper": st,
        "last_log": db.last_status(),
        "instances": instances,
        "storage_state": config.STORAGE_STATE_PATH.exists(),
    }


@app.get("/api/series")
def series(minutes: int = 60, _user: str = Depends(current_user)) -> dict[str, Any]:
    minutes = max(1, min(minutes, 60 * 24 * 30))
    bucket, rows = db.series(minutes)
    return {"minutes": minutes, "bucket": bucket, "rows": rows}


@app.post("/api/scrape")
def trigger(_user: str = Depends(current_user)) -> dict[str, Any]:
    scraper.trigger()
    return {"ok": True}


@app.get("/api/debug")
def debug(_user: str = Depends(current_user)) -> dict[str, Any]:
    raw = scraper.state.get("raw") or {}
    return {
        "ts": raw.get("ts"),
        "url": raw.get("url"),
        "bodyText": raw.get("bodyText"),
        "headers": raw.get("headers"),
        "rows": (raw.get("rows") or [])[:10],
        "fixed": (raw.get("fixed") or [])[:10],
        "parsed": (raw.get("parsed") or [])[:10],
        "config": config.load()["autodl"],
    }


@app.post("/api/probe")
def probe(body: ProbeBody, _user: str = Depends(current_user)) -> dict[str, Any]:
    return scraper.probe(body.xpath)


@app.post("/api/settings")
def update_settings(body: SettingsBody, _user: str = Depends(current_user)) -> dict[str, Any]:
    cfg = config.load(force=True)
    a = cfg["autodl"]
    if body.interval_sec is not None:
        a["interval_sec"] = max(5, min(int(body.interval_sec), 3600))
    if body.cpu_gpu_col is not None:
        a["cpu_gpu_col"] = max(1, int(body.cpu_gpu_col))
    if body.name_col is not None:
        a["name_col"] = max(1, int(body.name_col))
    if body.xpath is not None and body.xpath.strip():
        a["xpath"] = body.xpath.strip()
    if body.headless is not None:
        a["headless"] = bool(body.headless)
    config.save(cfg)
    scraper.trigger()
    return {"ok": True, "autodl": a}


@app.post("/api/password")
def change_password(body: PasswordBody, _user: str = Depends(current_user)) -> dict[str, Any]:
    cfg = config.load(force=True)
    if not auth.verify_password(body.old_password, cfg["app"]["password_hash"]):
        raise HTTPException(status_code=400, detail="原密码不正确")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")
    cfg["app"]["password_hash"] = auth.hash_password(body.new_password)
    config.save(cfg)
    return {"ok": True}


@app.exception_handler(HTTPException)
async def http_exc(_r: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
