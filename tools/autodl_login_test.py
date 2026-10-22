"""一次性的 AutoDL 登录诊断：经过频率限制器，只允许发起一次登录尝试。

用法（凭据从环境变量读，不落盘）：
  $env:AUTODL_PHONE="..."; $env:AUTODL_PASSWORD="..."
  python tools/autodl_login_test.py [--headless]

只把「真的提交到了登录接口」的请求计入频率限制；被前端校验挡住、没发出请求的不算。
无论成败都会截图到 data/autodl_login_result.png。
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402

from app import config  # noqa: E402
from app.ratelimit import autodl_login_limiter  # noqa: E402

LOGIN_URL = "https://www.autodl.com/login"


def human_type(page, el, text: str) -> None:
    el.scroll_into_view_if_needed()
    el.click()
    page.wait_for_timeout(random.randint(120, 350))
    for ch in text:
        el.type(ch, delay=random.randint(60, 160))
    page.wait_for_timeout(random.randint(150, 400))
    if not (el.input_value() or "").strip():
        el.fill(text)          # 兜底：某些组件逐字符输入会被清空


def main() -> int:
    phone = (os.environ.get("AUTODL_PHONE") or "").strip()
    password = os.environ.get("AUTODL_PASSWORD") or ""
    if not phone or not password:
        print("[error] 需要环境变量 AUTODL_PHONE / AUTODL_PASSWORD")
        return 2

    ok_allowed, why, wait = autodl_login_limiter.check()
    print(f"[limiter] allowed={ok_allowed} reason={why} wait={wait}s")
    if not ok_allowed:
        print(f"[skip] 未发起登录，需等待 {wait} 秒：{why}")
        return 3

    headless = "--headless" in sys.argv
    out = config.DATA_DIR / "autodl_login_result.png"
    result = {"ok": False, "url": "", "message": "", "request_sent": False,
              "screenshot": str(out)}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        ctx = browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport=None,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()

        sent: list[str] = []

        def on_request(req):
            u = req.url
            if "autodl.com" in u and req.method == "POST" and ("login" in u or "auth" in u):
                sent.append(u)

        page.on("request", on_request)

        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(random.randint(2500, 4000))

            phone_el = page.query_selector('input[placeholder="请输入手机号"]')
            pwd_el = page.query_selector('input[placeholder="请输入密码"]') or page.query_selector(
                'input[type="password"]'
            )
            if not phone_el or not pwd_el:
                result["message"] = "找不到手机号/密码输入框"
                page.screenshot(path=str(out))
                raise SystemExit(0)

            human_type(page, phone_el, phone)
            time.sleep(random.uniform(0.4, 0.9))
            human_type(page, pwd_el, password)
            time.sleep(random.uniform(0.6, 1.2))

            # 提交前自检：两个框都必须有值，否则宁可不发请求
            if not (phone_el.input_value() or "").strip() or not (pwd_el.input_value() or "").strip():
                result["message"] = "提交前自检失败：输入框为空，已放弃提交"
                page.screenshot(path=str(out))
                raise SystemExit(0)

            page.click("button:has-text('登录')")
            page.wait_for_timeout(1500)
            result["request_sent"] = bool(sent)

            for _ in range(12):
                if "/console" in page.url:
                    break
                page.wait_for_timeout(1000)

            result["url"] = page.url
            if "/console" in page.url:
                result["ok"] = True
                result["message"] = "登录成功"
                ctx.storage_state(path=str(config.STORAGE_STATE_PATH))
            else:
                texts = []
                for sel in [".el-message__content", ".el-message", ".el-form-item__error",
                            ".el-notification__content", ".err-msg"]:
                    try:
                        for el in page.query_selector_all(sel):
                            t = (el.inner_text() or "").strip()
                            if t:
                                texts.append(t)
                    except Exception:
                        pass
                has_captcha = bool(
                    page.query_selector("#nc_1_n1z, .nc_scale, .captcha, iframe[src*='captcha']")
                )
                if has_captcha:
                    texts.append("[检测到滑块/验证码]")
                result["message"] = " | ".join(dict.fromkeys(texts)) or "未跳转，原因未知"
            page.screenshot(path=str(out))
        except SystemExit:
            pass
        except Exception as e:  # noqa: BLE001
            result["message"] = f"异常：{e}"
            try:
                page.screenshot(path=str(out))
            except Exception:
                pass
        finally:
            try:
                ctx.close()
                browser.close()
            except Exception:
                pass

    if result["request_sent"]:
        autodl_login_limiter.record(result["ok"])
        result["counted"] = True
    else:
        result["counted"] = False

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("[limiter] " + json.dumps(autodl_login_limiter.summary(), ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
