# -*- coding: utf-8 -*-
"""手机内移动：按日期筛选 -> DSH 识图 -> 移入手机相册（电脑不留图）。"""
from __future__ import annotations

import argparse
import csv
import os
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

PROJ = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJ))

from phonesort.config import load_config
from phonesort.source import MtpSource, _dispatch, _items, _attr, _get_folder
from phonesort.vision_api import ApiClassifier

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif", ".avif"}
TARGETS = {"二次元", "梗图"}
DEFAULT_ALBUMS = {
    "二次元": "Pictures/Gallery/owner/二刺螈",
    "梗图": "Pictures/Gallery/owner/梗图",
}
ADB = PROJ / "tools" / "platform-tools" / "adb.exe"
ADB_TMP = PROJ / "tools" / "tmp"


def parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").astimezone()


def item_date(item):
    try:
        value = item.ExtendedProperty("System.DateModified")
    except Exception:
        return None
    if isinstance(value, datetime):
        return value.astimezone()
    return None


def child(folder, name: str):
    for item in _items(folder):
        if _attr(item, "Name", "") == name:
            return item
    return None


def navigate(folder, parts):
    node = folder
    for part in parts:
        item = child(node, part)
        if item is None:
            return None
        node = _get_folder(item)
        if node is None:
            return None
    return node


def find_source(storage, rel_path: str):
    parts = rel_path.replace("\\", "/").split("/")
    folder = storage
    for part in parts[:-1]:
        item = child(folder, part)
        if item is None:
            return None
        folder = _get_folder(item)
    return child(folder, parts[-1])


def adb_env():
    env = os.environ.copy()
    ADB_TMP.mkdir(parents=True, exist_ok=True)
    env["TMP"] = str(ADB_TMP)
    env["TEMP"] = str(ADB_TMP)
    return env


def adb_run(args, timeout=120):
    if not ADB.exists():
        return None
    return subprocess.run([str(ADB)] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=timeout, env=adb_env())


def adb_device():
    if not ADB.exists():
        return False
    try:
        adb_run(["start-server"], timeout=30)
        proc = adb_run(["devices"], timeout=30)
        if not proc:
            return False
        for line in proc.stdout.splitlines():
            if line.endswith("\tdevice"):
                return True
    except Exception:
        pass
    return False


def adb_pull(rel_path: str, dest: Path):
    proc = adb_run(["pull", "/sdcard/" + rel_path, str(dest)], timeout=180)
    if proc is None or proc.returncode != 0 or not dest.exists():
        raise RuntimeError((proc.stderr if proc else "adb 不可用")[:200])


def adb_move(rel_path: str, album: str):
    src = "/sdcard/" + rel_path
    dest_dir = "/sdcard/" + album
    command = "mkdir -p {0} && mv {1} {2}".format(
        shlex.quote(dest_dir), shlex.quote(src), shlex.quote(dest_dir + "/" + Path(rel_path).name)
    )
    proc = adb_run(["shell", command], timeout=60)
    if proc is None or proc.returncode != 0:
        raise RuntimeError((proc.stderr if proc else "adb 不可用")[:200])
    check = adb_run(["shell", "ls " + shlex.quote(dest_dir + "/" + Path(rel_path).name)], timeout=30)
    if check is None or check.returncode != 0:
        raise RuntimeError("移动后校验失败")


def mtp_move(shell, storage, source_item, album: str):
    parts = album.split("/")
    album_folder = navigate(storage, parts)
    if album_folder is None:
        raise RuntimeError("手机相册不存在：" + album)
    try:
        album_folder.MoveHere(source_item, 0)
    except Exception as exc:
        raise RuntimeError("MTP MoveHere 失败：" + str(exc)[:160])
    for _ in range(15):
        if child(album_folder, _attr(source_item, "Name", "")) is not None:
            return
        time.sleep(1)
    raise RuntimeError("移动后相册里没找到文件")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-03-01", help="只处理这个日期之后的图片")
    parser.add_argument("--until", default="", help="可选：截止日期（含）")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 张（试跑）")
    parser.add_argument("--apply", action="store_true", help="真正移动；不加只做识别和报告")
    parser.add_argument("--workers", type=int, default=1, help="下载/识别的并发数（默认 1）")
    parser.add_argument("--config", default=str(PROJ / "groups.yaml"))
    args = parser.parse_args()

    cfg = load_config(args.config)
    since = parse_date(args.since)
    until = parse_date(args.until) if args.until else None
    albums = dict(DEFAULT_ALBUMS)
    albums.update(cfg.get("phone_move", {}).get("albums") or {})

    src = MtpSource()
    storage = src._storages[0][1]
    shell = _dispatch()
    use_adb = adb_device()
    print("设备：{} | ADB：{} | 日期 >= {}".format(src.name, "可用" if use_adb else "不可用", args.since))

    candidates = []
    scanned = 0
    skipped_date = 0

    def scan_progress(count, path):
        if count and count % 500 == 0:
            print("扫描 {} 个文件...".format(count), flush=True)

    for remote in src.list_media(cfg, on_progress=scan_progress):
        if remote.ext.lower() not in IMAGE_EXTS:
            continue
        scanned += 1
        when = item_date(remote.handle)
        if when is None:
            skipped_date += 1
            continue
        if when < since:
            continue
        if until is not None and when > until:
            continue
        candidates.append((remote, when))
        if args.limit and len(candidates) >= args.limit:
            break

    print("图片 {} 张，日期命中 {} 张，日期未知 {} 张".format(scanned, len(candidates), skipped_date))
    if not candidates:
        return 0

    classifier = ApiClassifier(cfg.get("ai", {}).get("api") or {}, cfg.get("ai", {}).get("labels") or {})
    temp_dir = PROJ / ".phone_move_tmp" / ("run-{:%H%M%S}".format(datetime.now()))
    temp_dir.mkdir(parents=True, exist_ok=True)
    log_path = PROJ / ("手机整理日志-{:%Y%m%d-%H%M%S}.csv".format(datetime.now()))
    log_file = open(log_path, "w", encoding="utf-8-sig", newline="")
    writer = csv.writer(log_file)
    writer.writerow(["文件", "日期", "识别结果", "置信度", "动作", "说明"])

    def prepare(remote, local):
        if use_adb:
            adb_pull(remote.rel_path, local)
        else:
            src.copy_to(remote, temp_dir)
        return classifier.classify(local)

    moved = 0
    kept = 0
    errors = 0
    done = 0
    workers = max(1, args.workers) if use_adb else 1
    print("并发：{}".format(workers), flush=True)
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for index, (remote, when) in enumerate(candidates):
                local = temp_dir / ("{:04d}_{}".format(index, remote.name))
                futures[pool.submit(prepare, remote, local)] = (remote, when, local)
            for future in as_completed(futures):
                remote, when, local = futures[future]
                action = "保持"
                try:
                    result = future.result()
                    group = result["group"]
                    confidence = result["confidence"]
                    if group in TARGETS and confidence >= classifier.min_confidence:
                        if args.apply:
                            album = albums.get(group) or DEFAULT_ALBUMS[group]
                            if use_adb:
                                adb_move(remote.rel_path, album)
                                action = "已移动到手机相册"
                            else:
                                source = find_source(storage, remote.rel_path)
                                if source is None:
                                    raise RuntimeError("找不到手机原文件")
                                mtp_move(shell, storage, source, album)
                                action = "已移动到手机相册"
                            moved += 1
                        else:
                            action = "将移动（未执行）"
                    else:
                        kept += 1
                    writer.writerow([remote.name, when.strftime("%Y-%m-%d %H:%M"), group,
                                     "{:.2f}".format(confidence), action, result.get("reason", "")])
                    log_file.flush()
                    print("{} | {} | {} {:.2f} | {}".format(remote.name, when.strftime("%Y-%m-%d"), group, confidence, action), flush=True)
                except Exception as exc:
                    errors += 1
                    writer.writerow([remote.name, when.strftime("%Y-%m-%d %H:%M"), "", "", "失败", str(exc)[:200]])
                    log_file.flush()
                    print("{} | 失败：{}".format(remote.name, str(exc)[:160]), flush=True)
                finally:
                    try:
                        local.unlink(missing_ok=True)
                    except Exception:
                        pass
                    done += 1
                    if done % 10 == 0:
                        print("进度 {}/{}".format(done, len(candidates)), flush=True)
    finally:
        log_file.close()
        shutil.rmtree(temp_dir, ignore_errors=True)

    print("完成：移动 {}，保持 {}，失败 {}；日志：{}".format(moved, kept, errors, log_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
