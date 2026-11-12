# -*- coding: utf-8 -*-
"""生成一套模拟手机目录，用于测试。"""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent / "测试素材"


def make_exif(make=None, model=None, software=None, dt=None):
    exif = Image.Exif()
    if make:
        exif[271] = make
    if model:
        exif[272] = model
    if software:
        exif[305] = software
    if dt:
        exif[36867] = dt
    return exif


def image(rel: str, size=(80, 60), color=(180, 90, 60), exif=None):
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, color)
    if exif is None:
        im.save(path)
    else:
        im.save(path, exif=exif)


def video(rel: str):
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 2048)


def main():
    if ROOT.exists():
        shutil.rmtree(ROOT)
    # 相机拍摄（应被排除）
    image("DCIM/Camera/IMG_20240101_101010.jpg", (4032, 3024), (10, 20, 30),
          make_exif("HUAWEI", "HUAWEI Mate 60", None, "2024:01:01 10:10:10"))
    video("DCIM/Camera/VID_20240101_101010.mp4")
    # 截图
    image("DCIM/Screenshots/Screenshot_20240102_121212.png", (1080, 2400), (40, 40, 40))
    image("Pictures/Screenshots/Screenshot_2024-01-03-13-13-13.jpg", (1080, 2400), (50, 50, 50))
    # 微信
    image("Pictures/WeiXin/mmexport1700000000000.jpg", (600, 800), (20, 120, 20))
    image("Android/media/com.tencent.mm/WeChat/image.jpg", (600, 800), (30, 130, 30))
    # QQ
    image("Tencent/QQ_Images/qq_pic.jpg", (500, 500), (20, 20, 160))
    # 下载
    image("Download/baidu_image.jpg", (900, 600), (160, 120, 20))
    # 录屏
    video("Movies/ScreenRecorder/screen_record.mp4")
    # 修图（EXIF 软件）
    image("Pictures/Meitu/meitu_001.jpg", (700, 900), (200, 100, 160),
          make_exif(None, None, "美图秀秀", "2024:01:05 09:00:00"))
    # 其他：无 EXIF
    image("Pictures/random_no_exif.jpg", (400, 300), (90, 90, 90))
    # 其他目录里的相机照片（EXIF 应识别并排除）
    image("Pictures/random_camera_exif.jpg", (400, 300), (90, 60, 60),
          make_exif("Xiaomi", "Xiaomi 14", None, "2024:01:06 08:00:00"))
    # 不在扫描 roots 里的目录（应被跳过）
    image("Music/ignored.jpg", (200, 200), (10, 10, 10))
    print(f"测试素材已生成：{ROOT}")


if __name__ == "__main__":
    main()
