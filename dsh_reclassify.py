# -*- coding: utf-8 -*-
"""用 DSH 接入的 opencode-go 视觉模型，对整理结果做高并发二次分类。"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

PROJ = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJ))

from phonesort.config import load_config
from phonesort.vision_api import ApiClassifier

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif", ".avif"}
TARGETS = {"二次元", "梗图"}


def move_to(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        for index in range(1, 1000):
            candidate = dest.with_name(f"{stem} ({index}){suffix}")
            if not candidate.exists():
                dest = candidate
                break
    shutil.move(str(src), str(dest))
    return dest


def group_counts(out: Path) -> dict:
    data = {}
    for folder in out.iterdir():
        if folder.is_dir() and not folder.name.startswith("."):
            data[folder.name] = sum(1 for item in folder.rglob("*") if item.is_file())
    return dict(sorted(data.items(), key=lambda kv: -kv[1]))


def write_progress(out: Path, done: int, total: int, started: float):
    elapsed = max(time.time() - started, 0.001)
    rate = done / elapsed
    eta = (total - done) / rate if rate > 0 else 0
    data = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "processed": done,
        "total": total,
        "percent": round(done / total * 100, 1) if total else 0,
        "rate_per_minute": round(rate * 60, 1),
        "eta_minutes": round(eta / 60, 1),
        "groups": group_counts(out),
    }
    (out / "进度.js").write_text(
        "window.__PHONE_SORT_PROGRESS = " + json.dumps(data, ensure_ascii=False) + ";",
        encoding="utf-8",
    )
    lines = [
        "DSH 视觉模型 · 二次分类进度",
        f"更新时间：{data['time']}",
        f"已处理：{done} / {total}（{data['percent']}%）",
        f"速度：{data['rate_per_minute']} 张/分钟",
        f"预计剩余：{data['eta_minutes']} 分钟",
        "",
        "各分组文件数：",
    ]
    for name, count in data["groups"].items():
        lines.append(f"  {name}: {count}")
    (out / "进度.txt").write_text("\n".join(lines), encoding="utf-8")


def load_state(path: Path) -> set:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("done") or [])
    except Exception:
        return set()


def save_state(path: Path, done: set):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"done": sorted(done)}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def file_key(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return f"{path.name}|{size}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(PROJ / "整理结果"))
    parser.add_argument("--config", default=str(PROJ / "groups.yaml"))
    parser.add_argument("--workers", type=int, default=24, help="并发请求数（默认 24）")
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--model", default="", help="覆盖 ai.api.model")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 张（调试）")
    parser.add_argument("--reset", action="store_true", help="清空续跑记录，重新分类")
    args = parser.parse_args()

    out = Path(args.out).expanduser().resolve()
    if not out.is_dir():
        print(f"找不到输出目录：{out}")
        return 2

    cfg = load_config(args.config)
    api_cfg = dict(cfg.get("ai", {}).get("api") or {})
    if args.model:
        api_cfg["model"] = args.model
    if args.threshold is not None:
        api_cfg["threshold"] = args.threshold
    api_cfg["retries"] = max(int(api_cfg.get("retries") or 4), 4)
    classifier = ApiClassifier(api_cfg, cfg.get("ai", {}).get("labels") or {})

    state_path = out / ".phonesort" / "reclassify_state.json"
    done_keys = set() if args.reset else load_state(state_path)

    # 停掉 pass 1 的进度看门狗
    marker = out / ".stop_progress"
    marker.write_text("stop", encoding="utf-8")
    time.sleep(1.5)
    marker.unlink(missing_ok=True)

    files = []
    for folder in out.iterdir():
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
                files.append((path, folder.name))

    pending = [(path, group) for path, group in files if file_key(path) not in done_keys]
    if args.limit:
        pending = pending[:args.limit]

    total = len(pending)
    print(f"图片总数 {len(files)}，待处理 {total}，并发 {args.workers}，模型 {api_cfg.get('model')}")
    if total == 0:
        print("没有需要处理的图片。")
        return 0

    report_path = out / f"重分类报告-{datetime.now():%Y%m%d-%H%M%S}.csv"
    report = open(report_path, "w", encoding="utf-8-sig", newline="")
    writer = csv.writer(report)
    writer.writerow(["文件", "原分组", "新分组", "置信度", "说明"])

    lock = threading.Lock()
    done = 0
    moved = 0
    errors = 0
    started = time.time()

    def work(path: Path):
        return classifier.classify(path)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(work, path): (path, group) for path, group in pending}
        for future in as_completed(futures):
            path, old_group = futures[future]
            key = file_key(path)
            success = False
            try:
                result = future.result()
                success = True
                new_group = result["group"]
                confidence = result["confidence"]
                reason = result.get("reason", "")
                if new_group in TARGETS and confidence >= classifier.min_confidence:
                    if old_group != new_group:
                        move_to(path, out / new_group)
                        moved += 1
                    row = (path.name, old_group, new_group, f"{confidence:.2f}", reason)
                elif old_group in TARGETS and new_group == "其他":
                    move_to(path, out / "其他")
                    moved += 1
                    row = (path.name, old_group, "其他", f"{confidence:.2f}", reason)
                else:
                    row = (path.name, old_group, old_group, f"{confidence:.2f}", "保持")
            except Exception as exc:
                errors += 1
                row = (path.name, old_group, "", "", f"ERROR {exc}"[:200])

            with lock:
                writer.writerow(row)
                report.flush()
                done += 1
                if success:
                    done_keys.add(key)
                if done % 5 == 0:
                    write_progress(out, done, total, started)
                    save_state(state_path, done_keys)
                    print(f"  {done}/{total}  moved={moved} errors={errors}", flush=True)

    save_state(state_path, done_keys)
    write_progress(out, total, total, started)
    report.close()
    print(f"完成：{total} 张，移动 {moved} 张，失败 {errors} 张")
    print(f"报告：{report_path}")
    print(f"续跑记录：{state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
