"""截图验证：登录后截取仪表盘。"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent.parent / "data" / "dashboard.png"
URL = "http://127.0.0.1:8000"


def main() -> int:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        page = b.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1.5)
        errors = []
        page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.goto(URL, wait_until="networkidle")
        page.fill("#lg-pass", "admin123")
        page.click("button[type=submit]")
        page.wait_for_timeout(3500)
        page.screenshot(path=str(OUT), full_page=True)
        print("saved", OUT)
        print("console errors:", errors or "none")
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
