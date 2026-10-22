"""AutoDL 采集器。

数据来源（均为登录态下的真实接口/页面）：
  1) 实例列表页：每行的「健康状态」单元格给出  CPU xx%  / 内存 xx%
  2) 每台实例的「实例监控」(AutoPanel)：/autopanel/v1/monitor 返回 JSON，
     n.g[i].u = GPU 利用率(%)，n.c / sys.lc = CPU 利用率。
     该页面必须从控制台点击进入才会授权，所以按用户的操作方式模拟点击。

浏览器只在单个后台线程里操作（Playwright 同步 API 的线程约束），
外部通过命令队列交互：scrape / probe。
"""
from __future__ import annotations

import queue
import re
import threading
import time
import traceback
from concurrent.futures import Future
from typing import Any

from . import config, db
from .config import STORAGE_STATE_PATH

# ---------------------------------------------------------------- DOM 抓取

JS_ROWS = r"""
() => {
  const toText = el => (el.innerText || '')
      .replace(/\u00a0/g, ' ')
      .replace(/\r/g, '')
      .trim();
  const rowsOf = tb => Array.from(tb.querySelectorAll(':scope > tr'))
      .map(tr => Array.from(tr.querySelectorAll(':scope > td')).map(toText));

  const wrappers = Array.from(document.querySelectorAll('div[class*="is-scrolling-none"]'));
  let best = null, bestN = -1;
  for (const w of wrappers) {
    const tb = w.querySelector(':scope > table > tbody') || w.querySelector('table tbody');
    if (!tb) continue;
    const n = tb.querySelectorAll(':scope > tr').length;
    if (n > bestN) { bestN = n; best = tb; }
  }

  let fixed = [];
  for (const f of document.querySelectorAll('div[class*="el-table__fixed"]')) {
    const tb = f.querySelector('table tbody');
    if (tb && tb.querySelectorAll(':scope > tr').length > fixed.length) {
      fixed = rowsOf(tb);
    }
  }

  let headers = [];
  for (const t of document.querySelectorAll('table')) {
    const ths = t.querySelectorAll('thead th');
    if (ths.length > headers.length) headers = Array.from(ths).map(toText);
  }

  return {
    url: location.href,
    hasLoginForm: !!document.querySelector('input[type="password"]'),
    bodyText: (document.body ? document.body.innerText : '').slice(0, 2000),
    headers: headers,
    rows: best ? rowsOf(best).filter(r => r.length) : [],
    fixed: fixed.filter(r => r.length),
  };
}
"""

JS_PROBE = r"""
(xp) => {
  try {
    const r = document.evaluate(xp, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
    const out = [];
    for (let i = 0; i < Math.min(r.snapshotLength, 50); i++) {
      const n = r.snapshotItem(i);
      out.push({tag: n.tagName, text: (n.innerText || n.textContent || '').trim(),
                html: (n.outerHTML || '').slice(0, 300)});
    }
    return {count: r.snapshotLength, nodes: out};
  } catch (e) {
    return {error: String(e)};
  }
}
"""


# ---------------------------------------------------------------- 解析

_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def parse_usage(text: str) -> tuple[float | None, float | None, float | None]:
    """从单元格文本解析 (cpu%, gpu%, mem%)。

    真实格式： "正常 CPU 40% 内存 28%"（没有 GPU）
    GPU 只从 AutoPanel 的监控接口取，绝不用位置猜。
    """
    if not text:
        return None, None, None
    t = text.replace("：", ":").replace("\n", " ")

    def find(label: str) -> float | None:
        m = re.search(rf"{label}[^0-9]{{0,8}}(\d+(?:\.\d+)?)\s*%", t, re.I)
        return float(m.group(1)) if m else None

    cpu = find("CPU")
    gpu = find("GPU")
    mem = find("内存")

    # 兼容 "12% / 45%" 这类纯数字格式（且不含内存/显存字样时）
    if cpu is None and gpu is None and "内存" not in t and "显存" not in t:
        nums = [float(x) for x in _PCT.findall(t)]
        if len(nums) >= 2:
            cpu, gpu = nums[0], nums[1]
        elif len(nums) == 1:
            cpu = nums[0]

    for v in (cpu, gpu):
        if v is not None and not (0 <= v <= 100):
            return None, None, mem
    return cpu, gpu, mem


def parse_table(data: dict[str, Any], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    a = cfg["autodl"]
    usage_col = max(0, int(a.get("cpu_gpu_col", 5)) - 1)
    name_col = max(0, int(a.get("name_col", 1)) - 1)
    rows = data.get("rows") or []
    fixed = data.get("fixed") or []

    out: list[dict[str, Any]] = []
    for i, cells in enumerate(rows):
        cell0 = ""
        if i < len(fixed) and fixed[i]:
            cell0 = fixed[i][0] or ""
        elif name_col < len(cells):
            cell0 = cells[name_col] or ""
        lines = [
            ln.strip() for ln in cell0.split("\n")
            if ln.strip() and ln.strip() != "设置名称"
        ]
        # 首格格式：「西北B区 / 713机」换行「5d0f46b652-8a6192e7」换行「设置名称」
        name = lines[1] if len(lines) >= 2 else (lines[0] if lines else f"实例{i + 1}")
        name = re.sub(r"\s+", " ", name).strip()

        usage = cells[usage_col] if usage_col < len(cells) else ""
        status = cells[1] if len(cells) > 1 else ""
        cpu, gpu, mem = parse_usage(usage)
        if cpu is None and gpu is None and mem is None:
            for c in cells:
                if "%" in c:
                    cpu, gpu, mem = parse_usage(c)
                    if cpu is not None or gpu is not None:
                        usage = c
                        break
        out.append({
            "instance": name,
            "status": status,
            "cpu": cpu,
            "gpu": gpu,
            "mem": mem,
            "raw": usage,
            "row_index": i,
        })
    return out


def metrics_from_monitor(data: dict[str, Any]) -> tuple[float | None, float | None]:
    """从 /autopanel/v1/monitor 的 data 里取最近一条的 (cpu%, gpu%)。"""
    nl = data.get("nl") or []
    sysd = data.get("sys") or {}
    if not nl:
        return None, None
    last = nl[-1]
    n = last.get("n") or {}

    gpu = None
    us = [g.get("u") for g in (n.get("g") or []) if isinstance(g.get("u"), (int, float))]
    if us:
        gpu = float(max(us))

    cpu = None
    lc = sysd.get("lc")
    try:
        if lc and n.get("c") is not None:
            cpu = max(0.0, min(100.0, float(n["c"]) / float(lc) * 100.0))
    except (TypeError, ZeroDivisionError):
        cpu = None
    return cpu, gpu


# ---------------------------------------------------------------- 采集线程


class NeedLogin(Exception):
    pass


class Scraper:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "status": "starting",
            "message": "初始化中",
            "last_ok_ts": 0,
            "last_ts": 0,
            "rows": [],
            "raw": {},
            "interval": 0,
        }
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="autodl-scraper", daemon=True)
        self._pw = None
        self._browser = None
        self._ctx = None
        self._state_mtime = 0.0
        self._next_run = 0.0
        self._monitor: dict[str, dict] = {}

    # ---------------------------------------------------------- 对外
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._q.put(("stop", None, None))

    def trigger(self) -> None:
        self._q.put(("scrape", None, None))

    def probe(self, xpath: str, timeout: float = 60.0) -> dict[str, Any]:
        fut: Future = Future()
        self._q.put(("probe", xpath, fut))
        try:
            return fut.result(timeout=timeout)
        except Exception as e:  # noqa: BLE001
            return {"error": f"浏览器忙或超时：{e}"}

    # ---------------------------------------------------------- 内部
    def _set(self, status: str, message: str, **extra: Any) -> None:
        self.state.update({"status": status, "message": message, "last_ts": int(time.time())})
        self.state.update(extra)

    def _ensure_browser(self) -> None:
        from playwright.sync_api import sync_playwright

        cfg = config.load()
        a = cfg["autodl"]

        try:
            mtime = STORAGE_STATE_PATH.stat().st_mtime
        except OSError:
            mtime = 0.0
        if self._browser is not None and mtime != self._state_mtime:
            self._close_browser()
        self._state_mtime = mtime

        if self._browser is not None:
            return
        if not STORAGE_STATE_PATH.exists():
            raise NeedLogin("尚未登录 AutoDL，请在面板首页用 AutoDL 账号登录")

        self._pw = sync_playwright().start()
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        errors: list[str] = []
        for ch in [a.get("browser_channel") or "chrome", "msedge", None]:
            try:
                self._browser = self._pw.chromium.launch(
                    channel=ch, headless=bool(a.get("headless", True)), args=args
                )
                break
            except Exception as e:  # noqa: BLE001
                errors.append(f"{ch or 'chromium'}: {e}")
        if self._browser is None:
            raise RuntimeError("启动浏览器失败：" + " | ".join(errors))

        self._ctx = self._browser.new_context(
            storage_state=str(STORAGE_STATE_PATH),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1600, "height": 1000},
            ignore_https_errors=True,
        )
        self._ctx.set_default_timeout(25_000)
        self._ctx.on("response", self._on_response)

    def _on_response(self, resp) -> None:
        try:
            if "/autopanel/v1/monitor" not in resp.url:
                return
            body = resp.json()
            data = (body or {}).get("data") or {}
            if data.get("sys"):
                host = resp.url.split("/autopanel/")[0]
                self._monitor[host] = data
        except Exception:  # noqa: BLE001
            pass

    def _close_browser(self) -> None:
        for obj, name in ((self._ctx, "close"), (self._browser, "close")):
            try:
                getattr(obj, name)()
            except Exception:  # noqa: BLE001
                pass
        try:
            self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._ctx = self._browser = self._pw = None

    def _open_page(self):
        self._ensure_browser()
        page = self._ctx.new_page()
        page.goto(config.load()["autodl"]["url"], wait_until="domcontentloaded", timeout=45_000)
        return page

    def _row_monitor_metrics(self, row_element) -> tuple[float | None, float | None]:
        """点击该行的「实例监控」，等 AutoPanel 的监控接口返回，读 GPU/CPU。"""
        link = None
        for el in row_element.query_selector_all("span, a, div"):
            try:
                if (el.inner_text() or "").strip() == "实例监控":
                    link = el
                    break
            except Exception:  # noqa: BLE001
                continue
        if link is None:
            return None, None

        self._monitor.clear()
        newpage = None
        try:
            with self._ctx.expect_page(timeout=12_000) as pinfo:
                link.click()
            newpage = pinfo.value
        except Exception:  # noqa: BLE001
            newpage = None

        deadline = time.time() + 12
        data = None
        while time.time() < deadline:
            if self._monitor:
                data = next(iter(self._monitor.values()))
                break
            if newpage is not None:
                try:
                    newpage.wait_for_timeout(300)
                except Exception:  # noqa: BLE001
                    break
            else:
                time.sleep(0.3)

        try:
            if newpage is not None:
                newpage.close()
        except Exception:  # noqa: BLE001
            pass
        if not data:
            return None, None
        return metrics_from_monitor(data)

    def _scrape(self) -> None:
        cfg = config.load()
        page = None
        try:
            page = self._open_page()
            page.wait_for_timeout(1500)
            if "/login" in page.url:
                raise NeedLogin("登录已失效，请在面板首页用 AutoDL 账号重新登录")

            try:
                page.wait_for_selector(
                    'div[class*="is-scrolling-none"] table tbody tr', timeout=20_000
                )
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(600)

            data = page.evaluate(JS_ROWS)
            if data.get("hasLoginForm") and not data.get("rows"):
                raise NeedLogin("登录已失效，请在面板首页用 AutoDL 账号重新登录")

            rows = parse_table(data, cfg)

            # 逐台点开「实例监控」补 GPU 数据（只有运行中的实例才有）
            trs = page.query_selector_all(
                'div[class*="is-scrolling-none"] table tbody tr'
            )
            gpu_ok = 0
            for r in rows:
                idx = r["row_index"]
                if idx >= len(trs):
                    continue
                if "运行" not in (r.get("status") or ""):
                    continue
                cpu_m, gpu_m = self._row_monitor_metrics(trs[idx])
                if cpu_m is not None and r["cpu"] is None:
                    r["cpu"] = cpu_m
                if gpu_m is not None:
                    r["gpu"] = gpu_m
                    gpu_ok += 1
                page.wait_for_timeout(200)

            ts = int(time.time())
            self.state["raw"] = {
                "url": data.get("url"),
                "bodyText": (data.get("bodyText") or "")[:800],
                "headers": data.get("headers"),
                "rows": data.get("rows"),
                "fixed": data.get("fixed"),
                "parsed": rows,
                "ts": ts,
            }
            n = db.add_samples(ts, rows)
            if not rows:
                self._set("empty", "页面已打开，但没解析到实例行（列表为空或选择器需校准）", rows=[])
                db.log_scrape(False, "no rows parsed")
            else:
                cpu_n = sum(1 for r in rows if r["cpu"] is not None)
                self._set(
                    "ok",
                    f"采集成功：{len(rows)} 个实例，CPU {cpu_n} 个有值 / GPU {gpu_ok} 个有值",
                    last_ok_ts=ts,
                    rows=rows,
                )
                db.log_scrape(True, f"rows={len(rows)} gpu={gpu_ok} saved={n}")
        except NeedLogin as e:
            self._set("need_login", str(e))
            db.log_scrape(False, f"need_login: {e}")
            self._close_browser()
        except Exception as e:  # noqa: BLE001
            self._set("error", f"采集失败：{e}")
            db.log_scrape(False, f"{e}\n{traceback.format_exc()[-800:]}")
            self._close_browser()
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass

    def _probe(self, xpath: str) -> dict[str, Any]:
        page = None
        try:
            page = self._open_page()
            page.wait_for_timeout(3000)
            result = page.evaluate(JS_PROBE, xpath)
            result["url"] = page.url
            return result
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass

    def _run(self) -> None:
        next_prune = 0.0
        while not self._stop.is_set():
            try:
                cmd = self._q.get(timeout=0.5)
            except queue.Empty:
                cmd = None

            if cmd is not None:
                kind, arg, fut = cmd
                if kind == "stop":
                    break
                if kind == "scrape":
                    self._next_run = 0.0
                if kind == "probe":
                    try:
                        fut.set_result(self._probe(arg))
                    except Exception as e:  # noqa: BLE001
                        fut.set_exception(e)
                    continue

            now = time.time()
            interval = max(5, int(config.load()["autodl"].get("interval_sec", 15)))
            self.state["interval"] = interval
            if now >= self._next_run:
                self._scrape()
                # 需要登录时降到 10 分钟一次，避免反复撞登录页被当成异常流量
                delay = 600 if self.state["status"] == "need_login" else interval
                self._next_run = time.time() + delay
            if time.time() > next_prune:
                try:
                    db.prune(int(config.load().get("retention_days", 7)))
                except Exception:  # noqa: BLE001
                    pass
                next_prune = time.time() + 3600

        self._close_browser()


scraper = Scraper()
