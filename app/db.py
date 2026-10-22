"""SQLite 存储：采样点、采集日志、实例最新状态。"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterable

from .config import DATA_DIR

DB_PATH = DATA_DIR / "monitor.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       INTEGER NOT NULL,
    instance TEXT    NOT NULL,
    cpu      REAL,
    gpu      REAL
);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);
CREATE INDEX IF NOT EXISTS idx_samples_inst_ts ON samples(instance, ts);

CREATE TABLE IF NOT EXISTS scrape_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      INTEGER NOT NULL,
    ok      INTEGER NOT NULL,
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_log_ts ON scrape_log(ts);

CREATE TABLE IF NOT EXISTS instances (
    name      TEXT PRIMARY KEY,
    first_seen INTEGER,
    last_seen  INTEGER,
    last_cpu   REAL,
    last_gpu   REAL
);
"""

_lock = threading.Lock()


@contextmanager
def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with _lock, connect() as conn:
        conn.executescript(SCHEMA)


def add_samples(ts: int, rows: Iterable[dict[str, Any]]) -> int:
    """rows: [{instance, cpu, gpu}]，CPU/GPU 都是 None 的行跳过。"""
    payload = [
        (ts, r["instance"], r.get("cpu"), r.get("gpu"))
        for r in rows
        if r.get("instance") and (r.get("cpu") is not None or r.get("gpu") is not None)
    ]
    if not payload:
        return 0
    with _lock, connect() as conn:
        conn.executemany(
            "INSERT INTO samples(ts, instance, cpu, gpu) VALUES (?,?,?,?)", payload
        )
        # 更新实例表
        for ts_, name, cpu, gpu in payload:
            conn.execute(
                """
                INSERT INTO instances(name, first_seen, last_seen, last_cpu, last_gpu)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    last_seen=excluded.last_seen,
                    last_cpu=excluded.last_cpu,
                    last_gpu=excluded.last_gpu
                """,
                (name, ts_, ts_, cpu, gpu),
            )
    return len(payload)


def log_scrape(ok: bool, message: str) -> None:
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO scrape_log(ts, ok, message) VALUES (?,?,?)",
            (int(time.time()), 1 if ok else 0, message[:500]),
        )


def last_status() -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT ts, ok, message FROM scrape_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def list_instances() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT name, first_seen, last_seen, last_cpu, last_gpu
            FROM instances ORDER BY last_seen DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def series(minutes: int) -> tuple[int, list[dict[str, Any]]]:
    """按时间桶聚合，返回 (bucket_seconds, rows)。"""
    now = int(time.time())
    since = now - max(1, minutes) * 60
    span = max(60, now - since)
    # 目标点数 ~ 500
    bucket = max(1, int(span / 500))
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT (ts / :b) * :b AS bucket, instance,
                   AVG(cpu) AS cpu, AVG(gpu) AS gpu
            FROM samples
            WHERE ts >= :since
            GROUP BY bucket, instance
            ORDER BY bucket
            """,
            {"b": bucket, "since": since},
        ).fetchall()
    return bucket, [dict(r) for r in rows]


def prune(days: int) -> int:
    cutoff = int(time.time()) - max(1, days) * 86400
    with _lock, connect() as conn:
        n1 = conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,)).rowcount
        conn.execute("DELETE FROM scrape_log WHERE ts < ?", (cutoff,))
        return n1
