# -*- coding: utf-8 -*-
"""下载 CLIP 模型（ModelScope 镜像，国内可直连）。"""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path
from typing import Callable

BASE = "https://www.modelscope.cn/models/openai-mirror/clip-vit-base-patch32/resolve/master"
FILES = [
    "config.json",
    "preprocessor_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
    "special_tokens_map.json",
    "pytorch_model.bin",
]


def download_model(target: str | Path, progress: Callable[[str], None] = print) -> Path:
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        dest = target / name
        if dest.exists() and dest.stat().st_size > 0:
            progress(f"已存在：{name}")
            continue
        url = f"{BASE}/{name}"
        tmp = dest.with_name(dest.name + ".part")
        progress(f"下载 {name} ...")
        try:
            with urllib.request.urlopen(url, timeout=90) as response, open(tmp, "wb") as handle:
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if total and done // (10 * 1024 * 1024) != (done - len(chunk)) // (10 * 1024 * 1024):
                        progress(f"  {name}: {done / 1e6:.0f}/{total / 1e6:.0f} MB")
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        progress(f"完成：{name}")
    return target
