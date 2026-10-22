"""离线验证：用一段仿 Element UI 表格的 HTML，跑一遍 JS_ROWS + parse_table。

不连接 AutoDL，用来证明“按行取 td[5] 的 div/div[2] 文本 → 解析 CPU/GPU%”这条链路正确。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

from app.scraper import JS_ROWS, parse_table, parse_usage

HTML = """
<!doctype html><html><head><meta charset="utf-8"></head><body>
<div class="el-table">
  <div class="el-table__header-wrapper">
    <table><thead><tr>
      <th>实例名称</th><th>状态</th><th>GPU</th><th>区域</th><th>CPU/GPU 使用率</th><th>操作</th>
    </tr></thead></table>
  </div>
  <div class="el-table__body-wrapper is-scrolling-none">
    <table class="el-table__body"><tbody>
      <tr>
        <td><div class="cell">autodl-3090-01<br><span>region-3</span></div></td>
        <td><div class="cell">运行中</div></td>
        <td><div class="cell">RTX 3090</div></td>
        <td><div class="cell">西北B区</div></td>
        <td><div><div class="cell">使用率</div><div class="cell">CPU 12.5%  GPU 87.3%</div></div></td>
        <td><div class="cell">关机</div></td>
      </tr>
      <tr>
        <td><div class="cell">autodl-a100-02</div></td>
        <td><div class="cell">运行中</div></td>
        <td><div class="cell">A100</div></td>
        <td><div class="cell">内蒙A区</div></td>
        <td><div><div class="cell">使用率</div><div class="cell">CPU 3%  GPU 0%</div></div></td>
        <td><div class="cell">关机</div></td>
      </tr>
      <tr>
        <td><div class="cell">autodl-4090-03</div></td>
        <td><div class="cell">已关机</div></td>
        <td><div class="cell">4090</div></td>
        <td><div class="cell">西北B区</div></td>
        <td><div><div class="cell">使用率</div><div class="cell">--</div></div></td>
        <td><div class="cell">开机</div></td>
      </tr>
    </tbody></table>
  </div>
</div></body></html>
"""

CFG = {"autodl": {"cpu_gpu_col": 5, "name_col": 1}}


def main() -> int:
    print("--- 单测 parse_usage ---")
    cases = [
        "CPU 12.5%  GPU 87.3%",
        "12% / 45%",
        "CPU:3% GPU:0%",
        "CPU 5%  GPU显存 3.2/24G",
        "--",
        "",
    ]
    for c in cases:
        print(f"  {c!r:32} -> {parse_usage(c)}")

    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        page = b.new_page()
        page.set_content(HTML, wait_until="domcontentloaded")
        data = page.evaluate(JS_ROWS)
        page.close()
        b.close()

    print("\n--- 表头 ---")
    print(" ", data["headers"])
    print(f"--- 抓到 {len(data['rows'])} 行 ---")
    for r in data["rows"]:
        print("  ", r)

    rows = parse_table(data, CFG)
    print("\n--- 解析结果 ---")
    for r in rows:
        print(f"  {r['instance']:<18} CPU={r['cpu']}  GPU={r['gpu']}   ← {r['raw']!r}")

    ok = (
        len(rows) == 3
        and rows[0]["instance"] == "autodl-3090-01"
        and rows[0]["cpu"] == 12.5
        and rows[0]["gpu"] == 87.3
        and rows[1]["cpu"] == 3.0
        and rows[1]["gpu"] == 0.0
        and rows[2]["cpu"] is None
        and rows[2]["gpu"] is None
    )
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
