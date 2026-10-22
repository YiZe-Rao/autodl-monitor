"""抓取 AutoDL 登录页的视觉与结构，作为复刻参考。"""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent.parent / "data"


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        page = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1.5)
        page.goto("https://www.autodl.com/login", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / "autodl_login.png"))
        info = page.evaluate(r"""
        () => {
          const cs = el => {
            if (!el) return null;
            const s = getComputedStyle(el);
            return {bg: s.backgroundColor, color: s.color, font: s.fontSize + ' ' + s.fontFamily,
                    radius: s.borderRadius, border: s.border, shadow: s.boxShadow,
                    w: el.getBoundingClientRect().width, h: el.getBoundingClientRect().height};
          };
          const txt = el => (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 200);
          const inputs = [...document.querySelectorAll('input')].map(i => ({
            type: i.type, placeholder: i.placeholder, cls: i.className, style: cs(i)}));
          const buttons = [...document.querySelectorAll('button, .el-button')].map(b => ({
            text: txt(b), cls: b.className, style: cs(b)}));
          const tabs = [...document.querySelectorAll('[class*="tab"], [role="tab"]')].map(t => ({
            text: txt(t), cls: t.className, style: cs(t)}));
          return {
            title: document.title,
            bodyStyle: cs(document.body),
            rootStyle: cs(document.body.firstElementChild),
            h1: [...document.querySelectorAll('h1,h2,h3')].map(e => txt(e)),
            labels: [...document.querySelectorAll('label')].map(txt),
            inputs, buttons, tabs,
            allText: document.body.innerText.replace(/\n{2,}/g, '\n').slice(0, 1500)
          };
        }
        """)
        (OUT / "autodl_login.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(info, ensure_ascii=False, indent=2)[:4000])
        b.close()


if __name__ == "__main__":
    main()
