# -*- coding: utf-8 -*-
"""端到端测试：模拟手机目录 → 扫描 → 整理 → 去重 → 包含相机。"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
sys.path.insert(0, str(PROJ))

from phonesort.config import load_config, save_config

PY = sys.executable
SRC = PROJ / "测试素材"
OUT = PROJ / "测试输出"
OUT2 = PROJ / "测试输出-含相机"
DEDUPE = PROJ / "测试输出-去重"
DEDUPE_SRC = PROJ / "测试素材-去重"

failures = []


def check(cond, message):
    if cond:
        print(f"  PASS  {message}")
    else:
        failures.append(message)
        print(f"  FAIL  {message}")


def run(args):
    # 沙箱环境可能禁止管道，用文件重定向收集输出
    log_path = PROJ / "tests" / "_last_output.txt"
    with open(log_path, "w", encoding="utf-8") as handle:
        code = subprocess.run(
            [PY, "-m", "phonesort"] + args,
            cwd=str(PROJ),
            stdout=handle,
            stderr=subprocess.STDOUT,
        ).returncode
    text = log_path.read_text(encoding="utf-8", errors="replace")
    return types.SimpleNamespace(returncode=code, stdout=text)


def main():
    print("[1] 生成测试素材")
    subprocess.run([PY, str(HERE / "make_testdata.py")], check=True, stdout=subprocess.DEVNULL)
    for path in (OUT, OUT2, DEDUPE):
        shutil.rmtree(path, ignore_errors=True)
    shutil.rmtree(DEDUPE_SRC, ignore_errors=True)

    print("[2] 扫描预览")
    result = run(["scan", "--source-dir", str(SRC)])
    check(result.returncode == 0, "scan 退出码为 0")
    check("扫描到媒体：12" in result.stdout, "扫描到 12 个媒体文件（Music 被 roots 过滤）")
    check("相机拍摄、将排除：3" in result.stdout, "相机排除 3 个")
    check("截图" in result.stdout and "微信" in result.stdout, "预览里出现截图/微信分组")

    print("[3] 正式整理")
    result = run(["run", "--source-dir", str(SRC), "--out", str(OUT)])
    check(result.returncode == 0, "run 退出码为 0")
    check("已归档：9 个" in result.stdout, "归档 9 个")
    check("相机排除：3 个" in result.stdout, "排除 3 个相机文件")

    expected = {
        "截图/Screenshot_20240102_121212.png": True,
        "截图/Screenshot_2024-01-03-13-13-13.jpg": True,
        "微信/mmexport1700000000000.jpg": True,
        "微信/image.jpg": True,
        "QQ/qq_pic.jpg": True,
        "下载/baidu_image.jpg": True,
        "录屏/screen_record.mp4": True,
        "修图/meitu_001.jpg": True,
        "其他/random_no_exif.jpg": True,
    }
    for rel in expected:
        check((OUT / rel).exists(), f"存在 {rel}")
    check(not (OUT / "相机").exists(), "没有生成相机分组")
    camera_files = list(OUT.rglob("IMG_20240101_101010.jpg")) + list(OUT.rglob("VID_20240101_101010.mp4"))
    check(not camera_files, "相机文件没有被复制")
    check(list(OUT.glob("整理报告-*.csv")), "生成了 CSV 报告")
    check((OUT / ".phonesort" / "state.json").exists(), "生成了增量记录")

    print("[4] 再跑一次（增量去重）")
    result = run(["run", "--source-dir", str(SRC), "--out", str(OUT)])
    check("已归档：0 个" in result.stdout, "第二次不再复制")
    check("重复跳过：9 个" in result.stdout, "第二次跳过 9 个")

    print("[5] 包含相机拍摄")
    result = run(["run", "--source-dir", str(SRC), "--out", str(OUT2), "--include-camera"])
    check(result.returncode == 0, "include-camera 退出码为 0")
    check((OUT2 / "相机" / "IMG_20240101_101010.jpg").exists(), "相机照片进入“相机”分组")
    check((OUT2 / "相机" / "VID_20240101_101010.mp4").exists(), "相机视频进入“相机”分组")
    check((OUT2 / "相机" / "random_camera_exif.jpg").exists(), "EXIF 相机照片进入“相机”分组")

    print("[6] 内容去重")
    (DEDUPE_SRC / "a").mkdir(parents=True, exist_ok=True)
    (DEDUPE_SRC / "b").mkdir(parents=True, exist_ok=True)
    data = b"same-content-for-dedupe" * 100
    (DEDUPE_SRC / "a" / "one.jpg").write_bytes(data)
    (DEDUPE_SRC / "b" / "two.jpg").write_bytes(data)
    result = run(["run", "--source-dir", str(DEDUPE_SRC), "--out", str(DEDUPE)])
    check("已归档：1 个" in result.stdout, "相同内容只归档 1 个")
    check("重复跳过：1 个" in result.stdout, "相同内容跳过 1 个")

    print("[7] 自定义分组（壁纸）")
    out3 = PROJ / "测试输出-自定义"
    shutil.rmtree(out3, ignore_errors=True)
    cfg = load_config(PROJ / "groups.yaml")
    cfg["groups"].insert(0, {
        "name": "壁纸",
        "enabled": True,
        "rules": [{"path": "*/Pictures/random_no_exif.jpg"}],
    })
    custom = PROJ / "tests" / "_custom_groups.yaml"
    save_config(cfg, custom)
    result = run(["run", "--source-dir", str(SRC), "--out", str(out3), "--config", str(custom)])
    check(result.returncode == 0, "自定义配置运行成功")
    check((out3 / "壁纸" / "random_no_exif.jpg").exists(), "文件进入自定义分组“壁纸”")
    check(not (out3 / "其他" / "random_no_exif.jpg").exists(), "不再进入“其他”")

    print("[8] 仅预览")
    out4 = PROJ / "测试输出-仅预览"
    shutil.rmtree(out4, ignore_errors=True)
    result = run(["run", "--source-dir", str(SRC), "--out", str(out4), "--dry-run"])
    check(result.returncode == 0, "dry-run 运行成功")
    check(not (out4 / "截图").exists(), "dry-run 不创建分组目录")
    state_path = out4 / ".phonesort" / "state.json"
    empty = True
    if state_path.exists():
        empty = not json.loads(state_path.read_text(encoding="utf-8")).get("files")
    check(empty, "dry-run 不记录已处理文件")

    print()
    if failures:
        print(f"共 {len(failures)} 项失败：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("全部测试通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
