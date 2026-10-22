"""一次 AutoDL 登录（可无头 / 可见），成功后导出 storage_state 并写结果文件。

由服务端以子进程方式调用：
  - 无头模式：尝试静默登录，几秒内出结果（若站点要求人机验证则返回 captcha）
  - 可见模式：把账号密码填好并提交，把滑块留给用户自己拖
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402

from app import config, creds  # noqa: E402

LOGIN_URL = "https://www.autodl.com/login"
INSTANCE_URL = "https://www.autodl.com/console/instance/list"
RESULT_PATH = config.DATA_DIR / "login_result.json"
LOG_PATH = config.DATA_DIR / "login_child.log"
SHOT_PATH = config.DATA_DIR / "captcha_shot.png"


def log(msg: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def write_result(**kw) -> None:
    kw.setdefault("ts", int(time.time()))
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(kw, ensure_ascii=False), encoding="utf-8")


def human_type(page, el, text: str) -> None:
    el.scroll_into_view_if_needed()
    el.click()
    page.wait_for_timeout(random.randint(120, 320))
    for ch in text:
        el.type(ch, delay=random.randint(55, 150))
    page.wait_for_timeout(random.randint(150, 350))
    if not (el.input_value() or "").strip():
        el.fill(text)


def has_captcha(page) -> bool:
    try:
        if page.query_selector("#nc_1_n1z, .nc_scale, .captcha, iframe[src*='captcha']"):
            return True
        return "安全验证" in (page.inner_text("body") or "")
    except Exception:  # noqa: BLE001
        return False


def error_text(page) -> str:
    out = []
    for sel in [".el-message__content", ".el-message", ".el-form-item__error",
                ".el-notification__content"]:
        try:
            for el in page.query_selector_all(sel):
                t = (el.inner_text() or "").strip()
                if t:
                    out.append(t)
        except Exception:  # noqa: BLE001
            pass
    return " | ".join(dict.fromkeys(out))


def kill_stale_browsers() -> None:
    """清掉仍占用登录专用 profile 的残留 Chrome，否则新窗口会启动失败。"""
    profile = str(config.LOGIN_PROFILE_DIR)
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{profile}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        time.sleep(1.0)
    except Exception as e:  # noqa: BLE001
        log(f"kill_stale_browsers failed: {e}")


def launch(p, visible: bool):
    kill_stale_browsers()
    kwargs = dict(
        channel="chrome",
        headless=not visible,
        args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        no_viewport=True,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            return p.chromium.launch_persistent_context(str(config.LOGIN_PROFILE_DIR), **kwargs)
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"launch failed (attempt {attempt + 1}): {e}")
            kill_stale_browsers()
            time.sleep(2.0)
    raise last_err  # type: ignore[misc]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--visible", action="store_true")
    ap.add_argument("--no-submit", action="store_true", help="只填好账号密码，不点登录（联调用）")
    ap.add_argument("--force-login", action="store_true", help="跳过会话复用，强制走一次登录")
    ap.add_argument("--timeout", type=int, default=0)
    args = ap.parse_args()

    phone = (os.environ.get("AUTODL_PHONE") or "").strip()
    password = os.environ.get("AUTODL_PASSWORD") or ""
    if not phone or not password:
        stored = creds.load()
        if not stored:
            write_result(status="error", detail="没有可用的账号密码")
            return 1
        phone, password = stored

    timeout = args.timeout or (300 if args.visible else 90)
    write_result(status="running", visible=args.visible)

    with sync_playwright() as p:
        try:
            ctx = launch(p, args.visible)
        except Exception as e:  # noqa: BLE001
            log(f"launch gave up: {e}")
            write_result(status="error", detail="浏览器启动失败（可能有残留 Chrome 占用配置），请重试")
            return 1
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            # 1) 会话还有效就不用登录了
            page.goto(INSTANCE_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2000)
            if "/login" not in page.url and not args.force_login:
                ctx.storage_state(path=str(config.STORAGE_STATE_PATH))
                write_result(status="ok", detail="复用已有会话")
                return 0

            # 2) 登录
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(random.randint(1800, 3000))
            phone_el = page.query_selector('input[placeholder="请输入手机号"]')
            pwd_el = page.query_selector('input[placeholder="请输入密码"]') or page.query_selector(
                'input[type="password"]')
            if not phone_el or not pwd_el:
                write_result(status="error", detail="找不到登录输入框，页面结构可能变了")
                return 1
            human_type(page, phone_el, phone)
            time.sleep(random.uniform(0.3, 0.8))
            human_type(page, pwd_el, password)
            time.sleep(random.uniform(0.4, 0.9))

            page.click("button:has-text('登录')" if not args.no_submit else "body")

            deadline = time.time() + timeout
            captcha_seen = False
            shot_taken = False
            while time.time() < deadline:
                if "/console" in page.url:
                    ctx.storage_state(path=str(config.STORAGE_STATE_PATH))
                    write_result(status="ok", detail="登录成功")
                    return 0
                if has_captcha(page):
                    captcha_seen = True
                    if not shot_taken:
                        shot_taken = True
                        log(f"captcha detected, url={page.url}")
                        try:
                            page.screenshot(path=str(SHOT_PATH))
                            frames = page.evaluate(
                                "(sel) => [...document.querySelectorAll(sel)].map(e => "
                                "({cls: e.className, rect: e.getBoundingClientRect().toJSON()}))",
                                "div, iframe",
                            )
                            big = [f for f in frames if f["rect"]["width"] > 200 and f["rect"]["height"] > 100]
                            log("visible boxes: " + json.dumps(big[:15], ensure_ascii=False))
                        except Exception as e:  # noqa: BLE001
                            log(f"screenshot failed: {e}")
                    if not args.visible:
                        write_result(status="captcha", detail="需要人机验证（滑块）")
                        return 2
                err = error_text(page)
                if err and ("密码" in err or "账号" in err or "不存在" in err):
                    write_result(status="bad_credentials", detail=err)
                    return 3
                page.wait_for_timeout(700)

            if captcha_seen:
                write_result(status="captcha", detail="等待滑块验证超时")
                return 2
            write_result(status="timeout", detail=error_text(page) or "登录超时")
            return 4
        except Exception as e:  # noqa: BLE001
            write_result(status="error", detail=f"{type(e).__name__}: {e}")
            return 1
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    sys.exit(main())
