"""插入演示数据，方便先看界面效果（不连 AutoDL）。

用法: .venv\\Scripts\\python.exe tools\\seed_demo.py
"""
from __future__ import annotations

import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402


def main() -> None:
    db.init()
    now = int(time.time())
    hours = 3
    step = 15
    names = ["autodl-3090-01", "autodl-a100-02"]
    rows = 0
    for t in range(now - hours * 3600, now, step):
        k = (t - now) / 3600.0
        batch = []
        for i, name in enumerate(names):
            base = 35 + 25 * math.sin(k * 2.2 + i * 1.7) + random.uniform(-6, 6)
            gpu = 60 + 30 * math.sin(k * 1.4 + i * 2.3) + random.uniform(-8, 8)
            batch.append({
                "instance": name,
                "cpu": max(0.5, min(99.5, base)),
                "gpu": max(0.0, min(100.0, gpu)),
            })
        rows += db.add_samples(t, batch)
    print(f"seeded {rows} samples for {len(names)} instances")


if __name__ == "__main__":
    main()
