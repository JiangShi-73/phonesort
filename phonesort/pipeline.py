# -*- coding: utf-8 -*-
"""整理流水线：扫描 → 排除相机 → 复制 → 识别 → 分组归档。"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .config import APP_DIR, enabled_groups
from .metadata import (
    guess_taken_at,
    human_size,
    read_image_metadata,
    read_video_metadata,
    sha256_file,
)
from .rules import (
    MediaInfo,
    camera_reason,
    classify,
    exif_camera_reason,
)

STATUS_OK = "已归档"
STATUS_DUP = "重复跳过"
STATUS_CAMERA = "相机排除"
STATUS_ERROR = "失败"

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(value: str, fallback: str = "未命名") -> str:
    value = _ILLEGAL.sub("_", str(value)).strip().rstrip(".")
    return value[:180] or fallback


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


@dataclass
class Summary:
    total: int = 0
    imported: int = 0
    duplicates: int = 0
    camera_skipped: int = 0
    errors: int = 0
    bytes_copied: int = 0
    by_group: Counter = field(default_factory=Counter)
    by_group_bytes: Counter = field(default_factory=Counter)
    started_at: str = ""
    finished_at: str = ""
    report_path: str = ""
    dry_run: bool = False

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "imported": self.imported,
            "duplicates": self.duplicates,
            "camera_skipped": self.camera_skipped,
            "errors": self.errors,
            "bytes_copied": self.bytes_copied,
            "by_group": dict(self.by_group),
            "by_group_bytes": {k: human_size(v) for k, v in self.by_group_bytes.items()},
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "report_path": self.report_path,
            "dry_run": self.dry_run,
        }


class Manifest:
    """增量同步记录：避免重复导入。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = {"version": 1, "files": {}, "hashes": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data["files"] = loaded.get("files", {}) or {}
                    self.data["hashes"] = loaded.get("hashes", {}) or {}
            except Exception:
                pass
        self._dirty = 0

    @staticmethod
    def key(source_id: str, storage: str, rel_path: str) -> str:
        raw = f"{source_id}|{storage}|{rel_path}".encode("utf-8", "replace")
        return hashlib.sha1(raw).hexdigest()

    def lookup(self, key: str, size: int, mtime: Optional[datetime]):
        item = self.data["files"].get(key)
        if not item:
            return None
        if int(item.get("size", -1)) != int(size):
            return None
        if mtime is not None and item.get("mtime"):
            try:
                old = datetime.fromisoformat(item["mtime"])
                if abs((old - mtime).total_seconds()) > 2:
                    return None
            except ValueError:
                pass
        return item

    def remember(self, key: str, size: int, mtime: Optional[datetime], dest: str, digest: str, group: str):
        self.data["files"][key] = {
            "size": int(size),
            "mtime": mtime.isoformat(timespec="seconds") if mtime else "",
            "dest": dest,
            "hash": digest,
            "group": group,
            "at": datetime.now().isoformat(timespec="seconds"),
        }
        if digest:
            self.data["hashes"][digest] = dest
        self._dirty += 1

    def seen_hash(self, digest: str) -> Optional[str]:
        return self.data["hashes"].get(digest)

    def save(self, force: bool = False):
        if not force and self._dirty == 0:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        self._dirty = 0


class Organizer:
    def __init__(
        self,
        cfg: dict,
        source,
        out_dir: str | Path,
        include_camera: Optional[bool] = None,
        dry_run: bool = False,
        limit: int = 0,
        on_event: Optional[Callable[[dict], None]] = None,
        cancel=None,
    ):
        self.cfg = cfg
        self.source = source
        self.out_dir = Path(out_dir).expanduser().resolve()
        self.include_camera = bool(cfg.get("camera", {}).get("exclude", True) is False) if include_camera is None else include_camera
        self.dry_run = dry_run
        self.limit = limit
        self.on_event = on_event or (lambda event: None)
        self.cancel = cancel
        self.state_dir = self.out_dir / ".phonesort"
        self.staging_root = self.state_dir / "staging"
        self.manifest = Manifest(self.state_dir / "state.json")
        self.ai_cfg = cfg.get("ai", {}) or {}
        self.ai_enabled = bool(self.ai_cfg.get("enabled"))
        self.classifier = None
        self.ai_failed = False
        self._csv = None
        self._csv_file = None

    # ------------------------------------------------------------- helpers
    def _emit(self, **event):
        try:
            self.on_event(event)
        except Exception:
            pass

    def _cancelled(self) -> bool:
        return self.cancel is not None and self.cancel.is_set()

    def _collect(self) -> list:
        files = []
        last = [0]

        def progress(count, path):
            if count - last[0] >= 25:
                last[0] = count
                self._emit(type="scan_progress", count=count, path=path)

        self._emit(type="scan_start")
        for remote in self.source.list_media(self.cfg, on_progress=progress, cancel=self.cancel):
            files.append(remote)
            if self.limit and len(files) >= self.limit:
                break
        self._emit(type="scan_done", count=len(files))
        return files

    def _info_from_remote(self, remote, deep: bool = False) -> MediaInfo:
        taken = guess_taken_at(remote.name, remote.mtime)
        info = MediaInfo(
            rel_path=remote.rel_path,
            name=remote.name,
            ext=remote.ext,
            size=remote.size,
            media=remote.media,
            mtime=remote.mtime,
            taken_at=taken,
            storage=remote.storage,
            source_id=remote.source_id,
        )
        if deep and remote.media == "image":
            path = remote.handle if isinstance(remote.handle, str) else None
            if path:
                meta = read_image_metadata(path)
                info.width = int(meta.get("width") or 0)
                info.height = int(meta.get("height") or 0)
                info.make = meta.get("make", "")
                info.model = meta.get("model", "")
                info.software = meta.get("software", "")
                if meta.get("taken_at"):
                    info.taken_at = meta["taken_at"]
        return info

    def _fill_from_file(self, info: MediaInfo, path: Path):
        if info.media == "image":
            meta = read_image_metadata(path)
            info.width = int(meta.get("width") or 0)
            info.height = int(meta.get("height") or 0)
            info.make = meta.get("make", "")
            info.model = meta.get("model", "")
            info.software = meta.get("software", "")
            if meta.get("taken_at"):
                info.taken_at = meta["taken_at"]
        elif info.media == "video":
            meta = read_video_metadata(path)
            if meta.get("taken_at"):
                info.taken_at = meta["taken_at"]

    def _template(self, template: str, info: MediaInfo, group: str) -> str:
        dt = info.taken_at or info.mtime
        mapping = {
            "orig": info.stem,
            "name": info.name,
            "ext": info.ext.lstrip("."),
            "group": group,
            "storage": info.storage,
            "date": dt.strftime("%Y%m%d") if dt else "未知日期",
            "time": dt.strftime("%H%M%S") if dt else "000000",
            "year": dt.strftime("%Y") if dt else "未知年份",
            "month": dt.strftime("%m") if dt else "00",
            "day": dt.strftime("%d") if dt else "00",
        }
        try:
            return template.format_map(_SafeDict(mapping))
        except Exception:
            return template

    def _dest_for(self, group: str, info: MediaInfo) -> Path:
        options = self.cfg.get("options", {})
        group_dir = self.out_dir / safe_name(group, "其他")
        sub_template = (options.get("subfolder") or "").strip()
        if sub_template:
            rendered = self._template(sub_template, info, group)
            for part in rendered.replace("\\", "/").split("/"):
                if part.strip():
                    group_dir = group_dir / safe_name(part)
        name_template = (options.get("filename") or "{orig}").strip() or "{orig}"
        rendered_name = safe_name(self._template(name_template, info, group), info.stem)
        if "{ext}" not in name_template:
            rendered_name += info.ext.lower()
        target = group_dir / rendered_name
        if target.exists() and not options.get("overwrite"):
            stem, suffix = target.stem, target.suffix
            for index in range(1, 1000):
                candidate = target.with_name(f"{stem} ({index}){suffix}")
                if not candidate.exists():
                    return candidate
        return target

    def _model_dir(self) -> Path:
        raw = self.ai_cfg.get("model_dir", "models/clip-vit-base-patch32")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = APP_DIR / path
        return path

    def _ensure_classifier(self):
        if self.classifier is not None:
            return self.classifier
        from .vision import ClipClassifier, model_ready

        model_dir = self._model_dir()
        if not model_ready(model_dir):
            raise RuntimeError(f"找不到 CLIP 模型：{model_dir}")
        labels = self.ai_cfg.get("labels") or {}
        self.classifier = ClipClassifier(model_dir, labels)
        return self.classifier

    def _ai_decision(self, path):
        if not self.ai_enabled or self.ai_failed:
            return None
        try:
            classifier = self._ensure_classifier()
            return classifier.decide(
                path,
                float(self.ai_cfg.get("threshold", 0.32)),
                float(self.ai_cfg.get("margin", 0.08)),
                self.ai_cfg.get("thresholds") or {},
            )
        except Exception as exc:
            self.ai_failed = True
            self._emit(type="warning", message=f"内容识别不可用：{exc}")
            return None

    def _decide_group(self, info: MediaInfo, ai_hit, cfg: dict):
        """返回 (分组, 说明, 是否按相机排除)。"""
        camera_group = cfg.get("camera", {}).get("group", "相机")
        cam = camera_reason(info, cfg)
        allow_camera_probe = self.ai_enabled and bool(self.ai_cfg.get("include_camera"))
        if cam and not self.include_camera and not allow_camera_probe:
            return None, cam, True

        group, why, explicit = classify(info, cfg)
        ai_group = ai_hit[0] if ai_hit else None
        if ai_group and (self.ai_cfg.get("override_rules", True) or not explicit):
            return ai_group, f"AI {ai_group} {ai_hit[1]:.2f}", False

        exif_cam = exif_camera_reason(info, cfg)
        if cam:
            if self.include_camera:
                return camera_group, cam, False
            return None, cam, True
        if exif_cam and not explicit:
            if self.include_camera:
                return camera_group, exif_cam, False
            return None, exif_cam, True
        return group, why, False

    # ------------------------------------------------------------- preview
    def preview(self) -> dict:
        files = self._collect()
        deep = self.source.kind == "folder"
        by_group: Counter = Counter()
        sizes: Counter = Counter()
        camera = 0
        examples: dict[str, list] = {}
        for remote in files:
            if self._cancelled():
                break
            info = self._info_from_remote(remote, deep=deep)
            ai_hit = None
            if self.ai_enabled and deep and info.media == "image" and isinstance(remote.handle, str):
                ai_hit = self._ai_decision(remote.handle)
            group, why, excluded = self._decide_group(info, ai_hit, self.cfg)
            if excluded:
                camera += 1
                continue
            by_group[group] += 1
            sizes[group] += info.size
            examples.setdefault(group, [])
            if len(examples[group]) < 3:
                examples[group].append(info.rel_path)
        return {
            "total": len(files),
            "camera": camera,
            "by_group": dict(by_group),
            "by_group_bytes": {k: human_size(v) for k, v in sizes.items()},
            "examples": examples,
            "ai_skipped": bool(self.ai_enabled and self.source.kind != "folder"),
        }

    # ------------------------------------------------------------- run
    def _open_report(self):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.report_path = self.out_dir / f"整理报告-{stamp}.csv"
        self._csv_file = open(self.report_path, "w", encoding="utf-8-sig", newline="")
        self._csv = csv.writer(self._csv_file)
        self._csv.writerow(["状态", "分组", "文件名", "手机内路径", "大小(MB)", "拍摄时间", "目标位置", "说明"])

    def _write_row(self, status, group, info, dest, note):
        if self._csv is None:
            return
        self._csv.writerow([
            status,
            group,
            info.name,
            info.rel_path,
            f"{info.size / 1024 / 1024:.2f}" if info.size else "",
            (info.taken_at or info.mtime).strftime("%Y-%m-%d %H:%M:%S") if (info.taken_at or info.mtime) else "",
            str(dest) if dest else "",
            note,
        ])

    def run(self) -> Summary:
        summary = Summary(started_at=datetime.now().isoformat(timespec="seconds"), dry_run=self.dry_run)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._open_report()
        shutil.rmtree(self.staging_root, ignore_errors=True)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        run_staging = self.staging_root / f"run-{int(time.time())}"
        run_staging.mkdir(parents=True, exist_ok=True)
        try:
            files = self._collect()
            summary.total = len(files)
            total = len(files)
            for index, remote in enumerate(files, 1):
                if self._cancelled():
                    break
                self._emit(type="file_start", index=index, total=total, name=remote.name, rel=remote.rel_path)
                try:
                    self._process_one(remote, index, summary, run_staging)
                except Exception as exc:
                    summary.errors += 1
                    info = self._info_from_remote(remote)
                    self._write_row(STATUS_ERROR, "", info, "", str(exc))
                    self._emit(type="file_done", index=index, total=total, status="error",
                               group="", name=remote.name, error=str(exc))
                if index % 10 == 0:
                    self.manifest.save()
        finally:
            self.manifest.save(force=True)
            if self._csv_file is not None:
                self._csv_file.close()
            shutil.rmtree(run_staging, ignore_errors=True)
            summary.finished_at = datetime.now().isoformat(timespec="seconds")
            summary.report_path = str(getattr(self, "report_path", ""))
        self._emit(type="done", summary=summary.to_dict())
        return summary

    def _process_one(self, remote, index: int, summary: Summary, run_staging: Path):
        cfg = self.cfg
        info = self._info_from_remote(remote)
        key = Manifest.key(remote.source_id, remote.storage, remote.rel_path)
        known = self.manifest.lookup(key, remote.size, remote.mtime)
        if known and not self.dry_run:
            summary.duplicates += 1
            summary.by_group[known.get("group", "已归档")] += 1
            self._write_row(STATUS_DUP, known.get("group", ""), info, known.get("dest", ""), "之前已整理过")
            self._emit(type="file_done", index=index, total=summary.total, status="duplicate",
                       group=known.get("group", ""), name=remote.name)
            return

        cam = camera_reason(info, cfg)
        allow_camera_probe = self.ai_enabled and bool(self.ai_cfg.get("include_camera"))
        if cam and not self.include_camera and not allow_camera_probe:
            summary.camera_skipped += 1
            self._write_row(STATUS_CAMERA, "", info, "", cam)
            self._emit(type="file_done", index=index, total=summary.total, status="camera",
                       group="", name=remote.name)
            return

        if self.dry_run:
            group, why, excluded = self._decide_group(info, None, cfg)
            if excluded or group is None:
                summary.camera_skipped += 1
                self._write_row(STATUS_CAMERA, "", info, "", why or "相机")
                self._emit(type="file_done", index=index, total=summary.total, status="camera",
                           group="", name=remote.name)
                return
            dest = self._dest_for(group, info)
            summary.imported += 1
            summary.by_group[group] += 1
            summary.by_group_bytes[group] += info.size
            summary.bytes_copied += info.size
            self._write_row("预览", group, info, dest, "仅预览，未复制")
            self._emit(type="file_done", index=index, total=summary.total, status="ok",
                       group=group, name=remote.name)
            return

        # 1) 复制到临时目录
        item_dir = run_staging / f"{index:05d}"
        item_dir.mkdir(parents=True, exist_ok=True)
        local = self.source.copy_to(remote, item_dir)
        if not local.exists():
            raise RuntimeError("复制后找不到文件")
        info.size = local.stat().st_size or info.size
        self._fill_from_file(info, local)

        # 2) 内容识别（可选）
        ai_hit = None
        if self.ai_enabled and info.media == "image":
            ai_hit = self._ai_decision(local)

        # 3) 规则 + 相机 + AI 综合判定
        group, why, excluded = self._decide_group(info, ai_hit, cfg)
        if excluded or group is None:
            summary.camera_skipped += 1
            self._write_row(STATUS_CAMERA, "", info, "", why or "相机")
            self._emit(type="file_done", index=index, total=summary.total, status="camera",
                       group="", name=remote.name)
            shutil.rmtree(item_dir, ignore_errors=True)
            return

        # 3) 去重
        digest = sha256_file(local)
        if cfg.get("options", {}).get("dedupe", True):
            seen = self.manifest.seen_hash(digest)
            if seen:
                summary.duplicates += 1
                self._write_row(STATUS_DUP, group, info, seen, "内容重复")
                self._emit(type="file_done", index=index, total=summary.total, status="duplicate",
                           group=group, name=remote.name)
                shutil.rmtree(item_dir, ignore_errors=True)
                return

        # 4) 归档
        dest = self._dest_for(group, info)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(local), str(dest))
        shutil.rmtree(item_dir, ignore_errors=True)
        self.manifest.remember(key, info.size, remote.mtime, str(dest), digest, group)
        summary.imported += 1
        summary.bytes_copied += info.size
        summary.by_group[group] += 1
        summary.by_group_bytes[group] += info.size
        self._write_row(STATUS_OK, group, info, dest, why)
        self._emit(type="file_done", index=index, total=summary.total, status="ok",
                   group=group, name=remote.name)


def format_summary(summary: Summary) -> str:
    lines = []
    lines.append("=" * 56)
    lines.append("整理完成" + ("（预览模式，未实际复制）" if summary.dry_run else ""))
    lines.append(f"扫描到媒体：{summary.total} 个")
    lines.append(f"已归档：{summary.imported} 个（{human_size(summary.bytes_copied)}）")
    lines.append(f"重复跳过：{summary.duplicates} 个")
    lines.append(f"相机排除：{summary.camera_skipped} 个")
    lines.append(f"失败：{summary.errors} 个")
    if summary.by_group:
        lines.append("-" * 56)
        for group, count in summary.by_group.most_common():
            lines.append(f"  {group:<10} {count:>5} 个   {human_size(summary.by_group_bytes.get(group, 0))}")
    if summary.report_path:
        lines.append("-" * 56)
        lines.append(f"报告：{summary.report_path}")
    lines.append("=" * 56)
    return "\n".join(lines)
