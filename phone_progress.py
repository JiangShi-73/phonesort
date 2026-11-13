# -*- coding: utf-8 -*-
"""手机内移动的实时进度：写 整理结果/进度.js 和 进度.txt。"""
import csv
import glob
import json
import os
import time
from collections import deque
from datetime import datetime
from pathlib import Path

PROJ = Path(__file__).resolve().parent
OUT = PROJ / "整理结果"
TOTAL = int(os.environ.get("PHONE_MOVE_TOTAL", "2643"))


def latest_log():
    logs = sorted(glob.glob(str(PROJ / "手机整理日志-*.csv")), key=os.path.getmtime)
    return Path(logs[-1]) if logs else None


def read_counts(path):
    done = moved = kept = errors = 0
    try:
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if not row:
                    continue
                done += 1
                action = row[4] if len(row) > 4 else ""
                if "已移动" in action:
                    moved += 1
                elif "失败" in action:
                    errors += 1
                else:
                    kept += 1
    except Exception:
        pass
    return done, moved, kept, errors


def write(done, moved, kept, errors, rate, eta):
    OUT.mkdir(exist_ok=True)
    data = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "processed": done,
        "total": TOTAL,
        "percent": round(done / TOTAL * 100, 1) if TOTAL else 0,
        "rate_per_minute": round(rate, 1),
        "eta_minutes": round(eta, 1),
        "groups": {"已移动": moved, "保持原处": kept, "失败": errors},
    }
    (OUT / "进度.js").write_text(
        "window.__PHONE_SORT_PROGRESS = " + json.dumps(data, ensure_ascii=False) + ";",
        encoding="utf-8",
    )
    lines = [
        "手机内移动 · 分类进度",
        f"更新时间：{data['time']}",
        f"已处理：{done} / {TOTAL}（{data['percent']}%）",
        f"速度：{data['rate_per_minute']} 张/分钟",
        f"预计剩余：{data['eta_minutes']} 分钟",
        "",
        f"已移动：{moved}",
        f"保持原处：{kept}",
        f"失败：{errors}",
    ]
    (OUT / "进度.txt").write_text("\n".join(lines), encoding="utf-8")


def main():
    samples = deque()
    last_done = -1
    last_change = time.time()
    while True:
        path = latest_log()
        if path is None:
            time.sleep(3)
            continue
        done, moved, kept, errors = read_counts(path)
        now = time.time()
        samples.append((now, done))
        while samples and now - samples[0][0] > 30:
            samples.popleft()
        rate = 0.0
        if len(samples) >= 2 and samples[-1][0] > samples[0][0]:
            rate = (samples[-1][1] - samples[0][1]) / (samples[-1][0] - samples[0][0]) * 60
        eta = (TOTAL - done) / rate if rate > 0 else 0
        write(done, moved, kept, errors, rate, eta)
        if done != last_done:
            last_done = done
            last_change = now
        if done >= TOTAL or now - last_change > 180:
            break
        time.sleep(3)


if __name__ == "__main__":
    main()
