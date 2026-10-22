"""AutoDL 登录引导（人工完成，程序只保存会话）。

AutoDL 的密码登录会弹「请完成安全验证」滑块，这一步必须由人拖动完成，
程序不做任何绕过的尝试。登录成功后的会话（storage_state）会被保存，
监控面板直接复用，直到会话过期再重跑一次本脚本。
"""
from __future__ import annotations

import sys
import time

from playwright.sync_api import sync_playwright

from app import config

INSTANCE_URL = "https://www.autodl.com/console/instance/list"


def has_captcha(page) -> bool:
    try:
        if page.query_selector("#nc_1_n1z, .nc_scale, .captcha, iframe[src*='captcha']"):
            return True
        return "安全验证" in (page.inner_text("body") or "")
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    cfg = config.load()
    profile = config.LOGIN_PROFILE_DIR
    profile.mkdir(parents=True, exist_ok=True)

    print("=" * 64)
    print("  即将打开浏览器，请手动完成 AutoDL 登录：")
    print("  1) 输入手机号 + 密码，点「登录」")
    print("  2) 如果弹出「请完成安全验证」滑块，请自己拖动完成（程序不会代劳）")
    print("  3) 看到实例列表后，回到本窗口按回车保存会话")
    print("=" * 64)

    with sync_playwright() as p:
        ctx = None
        for ch in ["chrome", "msedge", None]:
            try:
                ctx = p.chromium.launch_persistent_context(
                    str(profile),
                    channel=ch,
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
                    no_viewport=True,
                    locale="zh-CN",
                )
                break
            except Exception as e:  # noqa: BLE001
                print(f"[warn] {ch or 'chromium'} 启动失败: {e}")
        if ctx is None:
            print("[error] 找不到可用浏览器，请安装 Chrome 或 Edge 后重试")
            sys.exit(1)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(INSTANCE_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1500)
        if has_captcha(page):
            print("[提示] 页面出现安全验证，请在弹出的窗口里手动拖动滑块。")

        input("\n看到实例列表后按回车保存会话 >>> ")

        # 校验：真的进到 console 才算登录成功，避免把无效会话覆盖掉
        try:
            page.goto(INSTANCE_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2500)
            logged_in = "/login" not in page.url and not has_captcha(page)
            url = page.url
        except Exception as e:  # noqa: BLE001
            logged_in, url = False, f"校验异常: {e}"

        if not logged_in:
            print(f"[warn] 看起来还没有登录成功（当前页面 {url}）。")
            print("[warn] 已取消保存，旧的会话保持不变。请重跑本脚本再登录一次。")
            time.sleep(1)
            ctx.close()
            sys.exit(2)

        ctx.storage_state(path=str(config.STORAGE_STATE_PATH))
        print(f"\n[ok] 会话已保存: {config.STORAGE_STATE_PATH}")
        print("[ok] 现在可以运行 start.bat 启动监控面板（会自动恢复采集）")
        time.sleep(0.3)
        ctx.close()


if __name__ == "__main__":
    main()
