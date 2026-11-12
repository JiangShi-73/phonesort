# -*- coding: utf-8 -*-
"""文件元数据：EXIF、拍摄时间、视频创建时间、哈希。"""
from __future__ import annotations

import hashlib
import os
import re
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

EXIF_MAKE = 271
EXIF_MODEL = 272
EXIF_SOFTWARE = 305
EXIF_DATETIME = 306
EXIF_DATETIME_ORIGINAL = 36867
EXIF_IFD = 0x8769

_DATE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?:IMG|VID|MVIMG|PXL|DSC|PANO)[_-]?(\d{8})[_-](\d{6})", re.I), "compact"),
    (re.compile(r"Screenshot[_-](\d{4})-(\d{2})-(\d{2})[ _-](\d{2})[.:-](\d{2})[.:-](\d{2})", re.I), "full"),
    (re.compile(r"Screenshot[_-](\d{8})[_-](\d{6})", re.I), "compact"),
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _-](\d{2})[.:-](\d{2})[.:-](\d{2})"), "full"),
    (re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})(?!\d)"), "full"),
]

_EPOCH_PATTERNS = [
    re.compile(r"(?:mmexport|wx_camera_|microMsg\.)(\d{13})", re.I),
    re.compile(r"(?<!\d)(1[5-9]\d{11})(?!\d)"),
]


def _safe_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return value.decode(enc).strip("\x00").strip()
            except Exception:
                continue
        return ""
    return str(value).strip("\x00").strip()


def parse_exif_datetime(value) -> Optional[datetime]:
    text = _safe_str(value)
    if not text:
        return None
    m = re.match(r"(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})", text)
    if not m:
        m = re.match(r"(\d{4})[:\-](\d{2})[:\-](\d{2})", text)
        if not m:
            return None
        y, mo, d = (int(x) for x in m.groups())
        try:
            return datetime(y, mo, d)
        except ValueError:
            return None
    y, mo, d, hh, mm, ss = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, hh, mm, ss)
    except ValueError:
        return None


def read_image_metadata(path: str | Path) -> dict:
    """读取图片的 EXIF / 尺寸。失败时返回空字典。"""
    out: dict = {}
    try:
        from PIL import Image

        with Image.open(path) as im:
            out["width"], out["height"] = im.size
            exif = im.getexif()
            if exif:
                make = _safe_str(exif.get(EXIF_MAKE))
                model = _safe_str(exif.get(EXIF_MODEL))
                software = _safe_str(exif.get(EXIF_SOFTWARE))
                dt = exif.get(EXIF_DATETIME)
                try:
                    ifd = exif.get_ifd(EXIF_IFD)
                except Exception:
                    ifd = {}
                dt_original = ifd.get(EXIF_DATETIME_ORIGINAL) if ifd else None
                if make:
                    out["make"] = make
                if model:
                    out["model"] = model
                if software:
                    out["software"] = software
                taken = parse_exif_datetime(dt_original) or parse_exif_datetime(dt)
                if taken:
                    out["taken_at"] = taken
    except Exception:
        return out
    return out


def _iter_atoms(f, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        header = f.read(8)
        if len(header) < 8:
            break
        size = struct.unpack(">I", header[:4])[0]
        typ = header[4:8].decode("latin-1", "replace")
        header_size = 8
        if size == 1:
            extra = f.read(8)
            if len(extra) < 8:
                break
            size = struct.unpack(">Q", extra)[0]
            header_size = 16
        elif size == 0:
            size = end - pos
        if size < header_size:
            break
        yield typ, pos, pos + size, header_size
        pos += size


def _mp4_creation_time(path: str | Path) -> Optional[datetime]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            moov = None
            for typ, start, end, _ in _iter_atoms(f, 0, file_size):
                if typ == "moov":
                    moov = (start, end)
                    break
            if not moov:
                return None
            for typ, start, end, header_size in _iter_atoms(f, moov[0] + 8, moov[1]):
                if typ != "mvhd":
                    continue
                f.seek(start + header_size)
                payload = f.read(min(32, end - start - header_size))
                if not payload:
                    return None
                version = payload[0]
                if version == 1 and len(payload) >= 16:
                    seconds = struct.unpack(">Q", payload[4:12])[0]
                elif len(payload) >= 8:
                    seconds = struct.unpack(">I", payload[4:8])[0]
                else:
                    return None
                if seconds <= 0:
                    return None
                base = datetime(1904, 1, 1, tzinfo=timezone.utc)
                return (base + timedelta(seconds=seconds)).astimezone().replace(tzinfo=None)
    except Exception:
        return None
    return None


def read_video_metadata(path: str | Path) -> dict:
    out: dict = {}
    ext = Path(path).suffix.lower()
    if ext in {".mp4", ".mov", ".m4v", ".3gp"}:
        taken = _mp4_creation_time(path)
        if taken:
            out["taken_at"] = taken
    return out


def guess_taken_at(name: str, mtime: Optional[datetime] = None) -> Optional[datetime]:
    """从文件名猜拍摄时间，失败时退回文件修改时间。"""
    stem = Path(name).stem
    for pattern, kind in _DATE_PATTERNS:
        m = pattern.search(stem)
        if not m:
            continue
        try:
            if kind == "compact":
                y, mo, d = int(m.group(1)[:4]), int(m.group(1)[4:6]), int(m.group(1)[6:8])
                hh, mm, ss = int(m.group(2)[:2]), int(m.group(2)[2:4]), int(m.group(2)[4:6])
            else:
                y, mo, d, hh, mm, ss = (int(x) for x in m.groups()[:6])
            return datetime(y, mo, d, hh, mm, ss)
        except (ValueError, IndexError):
            continue
    for pattern in _EPOCH_PATTERNS:
        m = pattern.search(stem)
        if not m:
            continue
        try:
            value = int(m.group(1))
            if 10**12 <= value < 10**13:
                return datetime.fromtimestamp(value / 1000.0)
        except (ValueError, OSError):
            continue
    return mtime


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"
