# -*- coding: utf-8 -*-
"""媒体来源：本地文件夹 / Windows MTP 手机（Shell COM）。"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from .config import all_extensions, media_kind


@dataclass
class DeviceInfo:
    name: str
    kind: str = "mtp"
    storages: list[str] = field(default_factory=list)


@dataclass
class RemoteFile:
    rel_path: str
    name: str
    ext: str
    size: int
    mtime: Optional[datetime]
    media: str
    storage: str = ""
    source_id: str = ""
    handle: object = None


def _norm(value: str) -> str:
    return (value or "").replace("\\", "/")


def _allowed_dir(rel: str, roots: list[str]) -> bool:
    """只扫描 roots 指定的目录（空列表 = 扫描全部）。"""
    if not roots:
        return True
    rel_l = rel.lower().strip("/")
    if not rel_l:
        return False
    for root in roots:
        root_l = _norm(str(root)).lower().strip("/")
        if not root_l:
            continue
        if rel_l == root_l or rel_l.startswith(root_l + "/"):
            return True
        if root_l.startswith(rel_l + "/"):
            return True
    return False


def _skip_dir(rel: str, name: str, cfg: dict) -> bool:
    rel_l = rel.lower().strip("/")
    name_l = name.lower()
    for pattern in cfg.get("scan", {}).get("skip_dirs", []):
        p = _norm(str(pattern)).lower().strip("/")
        if not p:
            continue
        if "/" in p:
            if rel_l == p or rel_l.startswith(p + "/") or f"/{p}/" in f"/{rel_l}/":
                return True
        elif name_l == p:
            return True
    return False


class FolderSource:
    """本地文件夹来源（用于测试，或手机以盘符/共享方式挂载时）。"""

    kind = "folder"

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise NotADirectoryError(f"来源文件夹不存在：{self.root}")
        self.name = self.root.name
        self.source_id = f"folder:{self.root}"

    def list_media(self, cfg: dict, on_progress=None, cancel=None) -> Iterator[RemoteFile]:
        exts = all_extensions(cfg)
        min_size = int(cfg.get("scan", {}).get("min_size_kb", 0)) * 1024
        max_size = int(cfg.get("scan", {}).get("max_size_mb", 0)) * 1024 * 1024
        max_depth = int(cfg.get("scan", {}).get("max_depth", 15))
        roots = [str(r) for r in cfg.get("scan", {}).get("roots", [])]
        if roots:
            # 只有当所选文件夹看起来像手机存储（含 DCIM/Pictures 等）时才套用 roots；
            # 用户随手选一个普通文件夹时扫描全部。
            try:
                top = {p.name.lower() for p in self.root.iterdir() if p.is_dir()}
            except OSError:
                top = set()
            if not any(str(r).split("/")[0].lower() in top for r in roots):
                roots = []
        count = 0
        for dirpath, dirnames, filenames in os.walk(self.root):
            if cancel is not None and cancel.is_set():
                return
            rel_dir = os.path.relpath(dirpath, self.root)
            rel_dir = "" if rel_dir == "." else _norm(rel_dir)
            depth = 0 if not rel_dir else rel_dir.count("/") + 1
            if depth >= max_depth:
                dirnames[:] = []
                continue
            kept = []
            for d in dirnames:
                child_rel = f"{rel_dir}/{d}" if rel_dir else d
                if d.startswith(".") or _skip_dir(child_rel, d, cfg):
                    continue
                if not _allowed_dir(child_rel, roots):
                    continue
                kept.append(d)
            dirnames[:] = kept
            if roots and not rel_dir:
                continue
            for filename in filenames:
                if filename.startswith("."):
                    continue
                ext = os.path.splitext(filename)[1].lower()
                if ext not in exts:
                    continue
                full = os.path.join(dirpath, filename)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if min_size and st.st_size < min_size:
                    continue
                if max_size and st.st_size > max_size:
                    continue
                rel = f"{rel_dir}/{filename}" if rel_dir else filename
                mtime = datetime.fromtimestamp(st.st_mtime)
                count += 1
                yield RemoteFile(
                    rel_path=rel,
                    name=filename,
                    ext=ext,
                    size=st.st_size,
                    mtime=mtime,
                    media=media_kind(ext, cfg),
                    storage=self.name,
                    source_id=self.source_id,
                    handle=full,
                )
                if on_progress and count % 50 == 0:
                    on_progress(count, rel)
        if on_progress:
            on_progress(count, "")

    def copy_to(self, remote: RemoteFile, dest_dir: str | Path) -> Path:
        import shutil

        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / remote.name
        shutil.copy2(remote.handle, target)
        return target


# ---------------------------------------------------------------- MTP / Shell

def _com_init() -> None:
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except Exception:
        pass


def _dispatch():
    try:
        import win32com.client
    except ImportError as exc:
        raise RuntimeError(
            "缺少 pywin32 组件。请先运行：python -m pip install -r requirements.txt"
        ) from exc

    return win32com.client.Dispatch("Shell.Application")


def _attr(obj, name: str, default=None):
    try:
        value = getattr(obj, name)
        return value() if callable(value) else value
    except Exception:
        return default


def _get_folder(item):
    try:
        folder = item.GetFolder
    except Exception:
        return None
    if folder is None:
        return None
    # win32com 的 Folder 对象本身可调用，不能直接用 callable 判断；
    # 真正的 Folder 有 Items 方法，方法对象没有。
    if callable(folder) and not hasattr(folder, "Items"):
        try:
            folder = folder()
        except Exception:
            return None
    return folder


def _items(folder) -> list:
    out = []
    try:
        collection = folder.Items()
        count = collection.Count
        for index in range(count):
            try:
                out.append(collection.Item(index))
            except Exception:
                continue
    except Exception:
        return out
    return out


def _is_folder(item) -> bool:
    return bool(_attr(item, "IsFolder", False))


def _is_filesystem(item) -> bool:
    return bool(_attr(item, "IsFileSystem", False))


_STORAGE_HINTS = (
    "internal storage", "internal shared storage", "内部存储", "手机存储",
    "存储", "sd card", "sd卡", "存储卡", "外置存储", "phone",
)
_MEDIA_ROOTS = {
    "dcim", "pictures", "download", "downloads", "movies", "tencent",
    "documents", "图库", "music", "android",
}


def _looks_like_storage(name: str) -> bool:
    low = (name or "").lower()
    return any(hint in low for hint in _STORAGE_HINTS)


def _looks_like_media_root(name: str) -> bool:
    return (name or "").lower() in _MEDIA_ROOTS


def _iter_devices(shell):
    """遍历“此电脑”下的便携设备，返回 (名称, 设备文件夹, [(存储名, 文件夹)])。"""
    pc = shell.NameSpace(17)
    if pc is None:
        return
    for item in _items(pc):
        if not _is_folder(item) or _is_filesystem(item):
            continue
        folder = _get_folder(item)
        if folder is None:
            continue
        kids = _items(folder)
        storages = []
        for kid in kids:
            if not _is_folder(kid):
                continue
            if _looks_like_storage(_attr(kid, "Name", "")):
                sub = _get_folder(kid)
                if sub is not None:
                    storages.append((_attr(kid, "Name", ""), sub))
        if not storages:
            if any(_looks_like_media_root(_attr(kid, "Name", "")) for kid in kids if _is_folder(kid)):
                storages.append((_attr(item, "Name", ""), folder))
            else:
                continue
        yield _attr(item, "Name", ""), folder, storages


def list_devices() -> list[DeviceInfo]:
    """列出已连接的 MTP 设备（手机需选择“传输文件”模式）。"""
    _com_init()
    devices: list[DeviceInfo] = []
    try:
        shell = _dispatch()
    except RuntimeError:
        raise
    except Exception:
        return devices
    try:
        for name, _folder, storages in _iter_devices(shell):
            devices.append(DeviceInfo(name=name, kind="mtp", storages=[s[0] for s in storages]))
    except Exception:
        return devices
    return devices


class MtpSource:
    """通过 Windows Shell（资源管理器同款 MTP 通道）读取手机。"""

    kind = "mtp"

    def __init__(self, device_name: str | None = None, storages: list[str] | None = None):
        _com_init()
        self.shell = _dispatch()
        self.device_name = ""
        self.source_id = ""
        self._storages: list[tuple[str, object]] = []
        for name, _folder, storage_list in _iter_devices(self.shell):
            if device_name and device_name.lower() not in name.lower():
                continue
            self.device_name = name
            self._storages = storage_list
            break
        if not self._storages:
            raise RuntimeError(
                "未检测到手机。请确认：1) USB 已连接；2) 手机上选择“传输文件 / MTP”；"
                "3) 手机上点“允许”访问数据。"
            )
        if storages:
            wanted = {s.lower() for s in storages}
            self._storages = [s for s in self._storages if s[0].lower() in wanted] or self._storages
        self.name = self.device_name
        self.source_id = f"mtp:{self.device_name}"
        self.storage_names = [s[0] for s in self._storages]

    def list_media(self, cfg: dict, on_progress=None, cancel=None) -> Iterator[RemoteFile]:
        exts = all_extensions(cfg)
        min_size = int(cfg.get("scan", {}).get("min_size_kb", 0)) * 1024
        max_size = int(cfg.get("scan", {}).get("max_size_mb", 0)) * 1024 * 1024
        max_depth = int(cfg.get("scan", {}).get("max_depth", 15))
        roots = [str(r) for r in cfg.get("scan", {}).get("roots", [])]
        total = 0
        for storage_name, folder in self._storages:
            stack = [(folder, "", 0)]
            while stack:
                if cancel is not None and cancel.is_set():
                    return
                current, prefix, depth = stack.pop()
                for item in _items(current):
                    name = _attr(item, "Name", "")
                    if not name:
                        continue
                    rel = f"{prefix}/{name}" if prefix else name
                    if _is_folder(item):
                        if depth >= max_depth or name.startswith("."):
                            continue
                        if _skip_dir(rel, name, cfg):
                            continue
                        if not _allowed_dir(rel, roots):
                            continue
                        sub = _get_folder(item)
                        if sub is not None:
                            stack.append((sub, rel, depth + 1))
                        continue
                    if roots and not prefix:
                        continue
                    ext = os.path.splitext(name)[1].lower()
                    if ext not in exts:
                        continue
                    size = _attr(item, "Size", 0) or 0
                    try:
                        size = int(size)
                    except Exception:
                        size = 0
                    if min_size and size and size < min_size:
                        continue
                    if max_size and size and size > max_size:
                        continue
                    mtime = _attr(item, "ModifyDate", None)
                    if not isinstance(mtime, datetime):
                        mtime = None
                    total += 1
                    yield RemoteFile(
                        rel_path=rel,
                        name=name,
                        ext=ext,
                        size=size,
                        mtime=mtime,
                        media=media_kind(ext, cfg),
                        storage=storage_name,
                        source_id=self.source_id,
                        handle=item,
                    )
                    if on_progress and total % 25 == 0:
                        on_progress(total, f"{storage_name}/{rel}")
        if on_progress:
            on_progress(total, "")

    def copy_to(self, remote: RemoteFile, dest_dir: str | Path) -> Path:
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / remote.name
        folder = self.shell.NameSpace(str(dest_dir))
        if folder is None:
            raise RuntimeError(f"无法打开临时目录：{dest_dir}")
        try:
            folder.CopyHere(remote.handle, 4 | 16 | 1024)
        except Exception as exc:
            raise RuntimeError(f"复制失败：{remote.name}（{exc}）") from exc
        timeout = max(60.0, min(3600.0, 30.0 + (remote.size or 0) / (1024 * 1024) * 3.0))
        deadline = time.time() + timeout
        last_size = -1
        stable = 0
        while time.time() < deadline:
            if target.exists():
                try:
                    current = target.stat().st_size
                except OSError:
                    current = 0
                if remote.size and current >= remote.size:
                    return target
                if not remote.size and current > 0:
                    if current == last_size:
                        stable += 1
                        if stable >= 3:
                            return target
                    else:
                        stable = 0
                        last_size = current
            time.sleep(0.4)
        # 兜底：目录里出现了同名（或被系统改名）的文件
        try:
            candidates = sorted(dest_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            for candidate in candidates:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    return candidate
        except OSError:
            pass
        raise TimeoutError(f"复制超时：{remote.rel_path}（文件较大或手机连接不稳定）")
