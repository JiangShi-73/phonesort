# -*- coding: utf-8 -*-
"""配置加载与默认分组规则。"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = APP_DIR / "groups.yaml"
EXAMPLE_CONFIG_PATH = APP_DIR / "groups.example.yaml"

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "device": {
        "name_contains": "",
    },
    "scan": {
        "roots": [
            "DCIM",
            "Pictures",
            "Download",
            "Downloads",
            "Movies",
            "Tencent",
            "图库",
            "Documents",
            "Android/media",
            "WeiXin",
            "WeChat",
            "QQ",
            "Huawei",
        ],
        "skip_dirs": [
            "Android/data",
            "Android/obb",
            ".thumbnails",
            ".trash",
            ".Trash",
            "LOST.DIR",
            "$RECYCLE.BIN",
            "System Volume Information",
            "tencent/MicroMsg/backup",
        ],
        "extensions": {
            "image": [
                ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif",
                ".bmp", ".dng", ".tif", ".tiff", ".avif",
            ],
            "video": [
                ".mp4", ".mov", ".m4v", ".mkv", ".avi", ".3gp", ".webm",
                ".wmv", ".flv", ".ts", ".mpg", ".mpeg",
            ],
        },
        "min_size_kb": 0,
        "max_size_mb": 0,
        "max_depth": 15,
    },
    "camera": {
        "exclude": True,
        "group": "相机",
        "paths": [
            "*/DCIM/Camera/*",
            "*/DCIM/100ANDRO/*",
            "*/DCIM/Video/*",
            "*/图库/相机/*",
            "*/相机/*",
            "*/Camera/*",
        ],
        "name_patterns": [
            "^IMG_\\d{8}_\\d{6}",
            "^VID_\\d{8}_\\d{6}",
            "^MVIMG_",
            "^PXL_\\d{8}_",
            "^DSC_?\\d{4}",
            "^PANO_",
            "^GOPR\\d{4}",
            "^DJI_\\d{4}",
        ],
        "exif_make_patterns": [
            "HUAWEI", "HONOR", "Xiaomi", "Redmi", "samsung", "OPPO", "vivo",
            "OnePlus", "realme", "Google", "Apple", "Meizu", "nubia",
            "motorola", "SONY", "Nokia", "Nothing", "ASUS", "Lenovo",
        ],
    },
    "groups": [
        {
            "name": "截图",
            "enabled": True,
            "rules": [
                {"path": "*/Screenshots/*"},
                {"path": "*/DCIM/Screenshots/*"},
                {"path": "*/图库/截图/*"},
                {"name": "^(Screenshot|ScreenShot|screenshot|截屏|屏幕截图)"},
                {"name": "^\\d{8}_\\d{6}_screenshot"},
            ],
        },
        {
            "name": "微信",
            "enabled": True,
            "rules": [
                {"path": "*/WeiXin/*"},
                {"path": "*/MicroMsg/*"},
                {"path": "*/WeChat/*"},
                {"name": "^(mmexport|wx_camera_)"},
                {"name": "^microMsg\\."},
            ],
        },
        {
            "name": "QQ",
            "enabled": True,
            "rules": [
                {"path": "*/QQ_Images/*"},
                {"path": "*/Tencent/QQ*"},
                {"path": "*/QQ/*"},
                {"name": "^(qq_|QQ图片)"},
            ],
        },
        {
            "name": "录屏",
            "enabled": True,
            "rules": [
                {"path": "*/ScreenRecorder*/*"},
                {"path": "*/Screen Recordings/*"},
                {"path": "*/录屏/*"},
                {"name": "^(Screenrecorder|ScreenRecording|screen-recorder)"},
            ],
        },
        {
            "name": "下载",
            "enabled": True,
            "rules": [
                {"path": "*/Download/*"},
                {"path": "*/Downloads/*"},
                {"path": "*/Browser/*"},
                {"path": "*/browser/*"},
                {"path": "*/UC Downloads/*"},
            ],
        },
        {
            "name": "修图",
            "enabled": True,
            "rules": [
                {"exif_software": "美图|Meitu|Snapseed|Lightroom|PicsArt|醒图|VSCO|黄油相机|InShot|CapCut|剪映|Wink|Faceu|B612"},
                {"path": "*/Meitu/*"},
                {"path": "*/美图秀秀/*"},
                {"path": "*/Snapseed/*"},
                {"path": "*/Lightroom/*"},
            ],
        },
        {
            "name": "文档扫描",
            "enabled": True,
            "rules": [
                {"path": "*/CamScanner/*"},
                {"path": "*/扫描全能王/*"},
                {"path": "*/scans/*"},
                {"path": "*/Scan/*"},
            ],
        },
        {
            "name": "视频",
            "enabled": True,
            "rules": [
                {"media": "video"},
            ],
        },
        {
            "name": "其他",
            "enabled": True,
            "default": True,
            "rules": [],
        },
    ],
    "ai": {
        "enabled": False,
        "backend": "clip",
        "api": {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-flash",
            "api_key_env": "DEEPSEEK_API_KEY",
            "detail": "low",
            "max_side": 1024,
            "timeout": 60,
            "retries": 2,
            "threshold": 0.55,
        },
        "model_dir": "models/clip-vit-base-patch32",
        "threshold": 0.30,
        "margin": 0.05,
        "thresholds": {
            "二次元": 0.35,
            "梗图": 0.20,
        },
        "include_camera": False,
        "override_rules": True,
        "labels": {
            "二次元": [
                "an anime illustration",
                "a cartoon drawing",
                "a manga page",
                "an anime character portrait",
                "a digital illustration of an anime girl",
                "a cosplay photo of a person in costume",
                "a cosplayer wearing a wig and costume",
                "a photo of someone dressed as an anime character",
            ],
            "梗图": [
                "an internet meme",
                "a reaction image with a caption",
                "a funny meme picture",
                "a sticker with text",
                "a screenshot of a funny chat",
                "a meme with impact font text",
                "an image macro with white caption text",
                "a picture with a funny caption",
                "a meme template",
                "a cartoon reaction face",
                "a Chinese internet meme",
                "a Chinese sticker with text",
                "a biaoqingbao sticker",
                "a reaction image with Chinese characters",
                "a meme of an animal with a caption",
            ],
            "其他": [
                "a normal photograph",
                "a photo of a person",
                "a portrait photo of a person",
                "a selfie",
                "a group photo",
                "a screenshot of an app",
                "a screenshot of a website",
                "a document scan",
                "a landscape photo",
                "a street photo",
                "a building",
                "a food photo",
                "a product photo",
                "a photo of a cat",
                "a photo of a dog",
                "a photo of an animal",
                "a wildlife photo",
                "a photo of furniture with measurement labels",
                "a diagram with measurements and arrows",
                "a product photo with size annotations",
                "an instructional image with text labels",
                "a photo of a room with dimensions",
                "a screenshot of a document",
                "a receipt or form",
                "a photo of a building interior",
                "a QR code",
                "a payment QR code poster",
                "a poster with a QR code",
                "a screenshot of a payment page",
                "an advertisement poster with Chinese text",
                "a business card",
                "a coupon or ticket",
                "a group chat invitation QR code",
                "a photo of a store sign",
                "a photo of a sign with Chinese characters",
                "a photo of a campus building",
                "a photo of a statue in a plaza",
                "a photo of a shop window",
                "a photo of a street scene",
                "a photo of a poster on a wall",
                "a photo of an outdoor sculpture",
                "a photo of a building entrance",
            ],
        },
    },
    "options": {
        "subfolder": "",
        "filename": "{orig}",
        "dedupe": True,
        "unknown_date_folder": "未知日期",
        "overwrite": False,
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    """递归合并配置，override 优先。"""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: str | Path | None = None) -> dict:
    """读取 YAML 配置并与默认值合并。"""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError(f"配置文件格式不正确：{cfg_path}")
        cfg = deep_merge(cfg, data)
    cfg["_config_path"] = str(cfg_path)
    return cfg


def save_config(cfg: dict, path: str | Path) -> Path:
    data = {k: v for k, v in cfg.items() if not k.startswith("_")}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, width=120)
    return target


def ensure_config() -> Path:
    """确保用户配置文件存在；不存在时从示例复制。"""
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    if EXAMPLE_CONFIG_PATH.exists():
        DEFAULT_CONFIG_PATH.write_text(
            EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
    else:
        save_config(DEFAULT_CONFIG, DEFAULT_CONFIG_PATH)
    return DEFAULT_CONFIG_PATH


def enabled_groups(cfg: dict) -> list[dict]:
    return [g for g in cfg.get("groups", []) if g.get("enabled", True)]


def all_extensions(cfg: dict) -> set[str]:
    exts = cfg["scan"]["extensions"]
    return {e.lower() for e in exts.get("image", []) + exts.get("video", [])}


def media_kind(ext: str, cfg: dict) -> str:
    ext = ext.lower()
    if ext in {e.lower() for e in cfg["scan"]["extensions"].get("video", [])}:
        return "video"
    return "image"
