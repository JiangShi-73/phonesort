# -*- coding: utf-8 -*-
"""分组规则匹配与分类。"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class MediaInfo:
    rel_path: str
    name: str
    ext: str
    size: int
    media: str
    mtime: Optional[datetime] = None
    taken_at: Optional[datetime] = None
    width: int = 0
    height: int = 0
    make: str = ""
    model: str = ""
    software: str = ""
    storage: str = ""
    source_id: str = ""

    @property
    def stem(self) -> str:
        if self.ext and self.name.lower().endswith(self.ext.lower()):
            return self.name[: -len(self.ext)]
        return self.name

    @property
    def date(self) -> Optional[datetime]:
        return self.taken_at or self.mtime


def norm_path(value: str) -> str:
    return (value or "").replace("\\", "/").strip("/")


def _regex(pattern: str, text: str) -> bool:
    try:
        return re.search(pattern, text, re.I) is not None
    except re.error:
        return False


def path_match(pattern: str, rel_path: str) -> bool:
    pat = norm_path(pattern)
    rel = norm_path(rel_path).lower()
    if not pat:
        return False
    if pat.lower().startswith("re:"):
        return _regex(pat[3:], rel)
    if any(ch in pat for ch in "*?["):
        if fnmatch.fnmatchcase(rel, pat.lower()):
            return True
        # 允许 "*/Download/*" 匹配顶层的 "Download/xxx"
        return fnmatch.fnmatchcase("/" + rel, pat.lower())
    return pat.lower() in rel


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _check(key: str, value, info: MediaInfo):
    """返回 (是否匹配, 说明)；未知键返回 (None, '')。"""
    if key in ("path", "paths"):
        for pat in _as_list(value):
            if path_match(str(pat), info.rel_path):
                return True, f"路径 {pat}"
        return False, ""
    if key in ("name", "names", "name_regex"):
        for pat in _as_list(value):
            if _regex(str(pat), info.name):
                return True, f"文件名 {pat}"
        return False, ""
    if key in ("ext", "exts"):
        exts = {("." + str(e).lower().lstrip(".")) for e in _as_list(value)}
        return (info.ext.lower() in exts), "扩展名"
    if key == "media":
        return (info.media == str(value).lower()), f"类型 {value}"
    if key == "min_size_kb":
        return info.size >= float(value) * 1024, f"大小≥{value}KB"
    if key == "max_size_kb":
        return info.size <= float(value) * 1024, f"大小≤{value}KB"
    if key == "min_width":
        return info.width >= int(value), f"宽≥{value}"
    if key == "min_height":
        return info.height >= int(value), f"高≥{value}"
    if key == "max_width":
        return info.width and info.width <= int(value), f"宽≤{value}"
    if key == "max_height":
        return info.height and info.height <= int(value), f"高≤{value}"
    if key == "ratio_min":
        return bool(info.width and info.height) and info.width / info.height >= float(value), f"比例≥{value}"
    if key == "ratio_max":
        return bool(info.width and info.height) and info.width / info.height <= float(value), f"比例≤{value}"
    if key in ("exif_make", "exif_model", "exif_software"):
        text = {"exif_make": info.make, "exif_model": info.model, "exif_software": info.software}[key]
        if not text:
            return False, ""
        return _regex(str(value), text), f"{key} {value}"
    if key == "date_after":
        dt = info.date
        if dt is None:
            return False, ""
        return dt >= parse_date(value), f"日期≥{value}"
    if key == "date_before":
        dt = info.date
        if dt is None:
            return False, ""
        return dt < parse_date(value), f"日期<{value}"
    return None, ""


def parse_date(value) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("/", "-").replace(".", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return datetime.min


def match_rule(rule: dict, info: MediaInfo) -> tuple[bool, str]:
    reasons: list[str] = []
    if not rule:
        return False, ""
    for key, value in rule.items():
        ok, why = _check(key, value, info)
        if ok is None:
            continue
        if not ok:
            return False, ""
        if why:
            reasons.append(why)
    return True, "、".join(reasons) if reasons else "规则匹配"


def camera_reason(info: MediaInfo, cfg: dict) -> Optional[str]:
    """按路径/文件名判断是否相机拍摄。"""
    cam = cfg.get("camera", {})
    for pat in cam.get("paths", []):
        if path_match(str(pat), info.rel_path):
            return f"相机目录（{pat}）"
    for pat in cam.get("name_patterns", []):
        if _regex(str(pat), info.name):
            return f"相机文件名（{pat}）"
    return None


def exif_camera_reason(info: MediaInfo, cfg: dict) -> Optional[str]:
    """按 EXIF 厂商判断是否相机拍摄。"""
    text = f"{info.make} {info.model}".strip()
    if not text:
        return None
    for pat in cfg.get("camera", {}).get("exif_make_patterns", []):
        if _regex(str(pat), text):
            return f"EXIF 厂商（{text}）"
    return None


def classify(info: MediaInfo, cfg: dict) -> tuple[str, str, bool]:
    """按顺序匹配分组，返回 (分组名, 原因, 是否命中显式规则)。"""
    for group in cfg.get("groups", []):
        if not group.get("enabled", True):
            continue
        if group.get("default"):
            return group.get("name", "其他"), "默认分组", False
        for rule in group.get("rules") or []:
            ok, why = match_rule(rule, info)
            if ok:
                return group.get("name", "未命名"), why, True
    return "其他", "未匹配任何规则", False


def describe_groups(cfg: dict) -> list[dict]:
    out = []
    for group in cfg.get("groups", []):
        out.append({
            "name": group.get("name", ""),
            "enabled": group.get("enabled", True),
            "default": bool(group.get("default")),
            "rules": len(group.get("rules") or []),
        })
    return out
