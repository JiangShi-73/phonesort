# -*- coding: utf-8 -*-
"""每 3 秒统计一次整理进度，写入 整理结果/进度.js 和 进度.txt。"""
import json
import time
from datetime import datetime
from pathlib import Path

PROJ = Path(__file__).resolve().parent
OUT = PROJ / "整理结果"
DEFAULT_TOTAL = 4421
MAX_SECONDS = 6 * 3600


def read_total():
    try:
        return int((OUT / ".phonesort" / "scan_total.txt").read_text(encoding="utf-8").strip())
    except Exception:
        return DEFAULT_TOTAL


def csv_processed():
    reports = sorted(OUT.glob("整理报告-*.csv"))
    if not reports:
        return 0
    try:
        with open(reports[-1], "rb") as handle:
            lines = sum(1 for _ in handle)
        return max(0, lines - 1)
    except OSError:
        return 0


def group_counts():
    data = {}
    if not OUT.exists():
        return data
    for folder in OUT.iterdir():
        if folder.is_dir() and not folder.name.startswith("."):
            data[folder.name] = sum(1 for item in folder.rglob("*") if item.is_file())
    return dict(sorted(data.items(), key=lambda kv: -kv[1]))


def main():
    started = time.time()
    first_processed = None
    first_time = started
    while True:
        if (OUT / ".stop_progress").exists():
            break
        if time.time() - started > MAX_SECONDS:
            break
        total = read_total()
        processed = csv_processed()
        if first_processed is None or processed < first_processed:
            first_processed = processed
            first_time = time.time()
        elapsed = time.time() - first_time
        rate = 0.0
        if elapsed > 5 and processed >= first_processed:
            rate = (processed - first_processed) / elapsed
        remaining = max(0, total - processed)
        eta = remaining / rate if rate > 0 else 0
        data = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "processed": processed,
            "total": total,
            "percent": round(processed / total * 100, 1) if total else 0,
            "rate_per_minute": round(rate * 60, 1),
            "eta_minutes": round(eta / 60, 1),
            "groups": group_counts(),
        }
        (OUT / "进度.js").write_text(
            "window.__PHONE_SORT_PROGRESS = " + json.dumps(data, ensure_ascii=False) + ";",
            encoding="utf-8",
        )
        lines = [
            "手机相册分组整理 · 进度",
            f"更新时间：{data['time']}",
            f"已处理：{processed} / {total}（{data['percent']}%）",
            f"速度：{data['rate_per_minute']} 个/分钟",
            f"预计剩余：{data['eta_minutes']} 分钟",
            "",
            "各分组文件数：",
        ]
        for name, count in data["groups"].items():
            lines.append(f"  {name}: {count}")
        (OUT / "进度.txt").write_text("\n".join(lines), encoding="utf-8")
        if processed >= total:
            break
        time.sleep(3)


if __name__ == "__main__":
    main()
