# -*- coding: utf-8 -*-
"""命令行入口。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import (
    APP_DIR,
    DEFAULT_CONFIG,
    DEFAULT_CONFIG_PATH,
    EXAMPLE_CONFIG_PATH,
    ensure_config,
    load_config,
    save_config,
)
from .pipeline import Organizer, format_summary
from .source import FolderSource, MtpSource, list_devices


def _setup_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _build_source(args, cfg):
    if getattr(args, "source_dir", None):
        return FolderSource(args.source_dir)
    device = getattr(args, "device", None) or cfg.get("device", {}).get("name_contains", "")
    return MtpSource(device or None, getattr(args, "storage", None))


def _add_common(parser):
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="分组配置文件（YAML）")
    parser.add_argument("--out", default=str(APP_DIR / "整理结果"), help="输出目录")
    parser.add_argument("--source-dir", default="", help="改用本地文件夹作为来源（测试或已挂载）")
    parser.add_argument("--device", default="", help="手机名称包含的关键字，默认第一台")
    parser.add_argument("--storage", action="append", default=[], help="只扫描指定存储（可重复）")
    parser.add_argument("--include-camera", action="store_true", help="连相机拍摄的照片也一起整理")
    parser.add_argument("--ai", dest="ai", action="store_true", help="启用内容识别（二次元/梗图）")
    parser.add_argument("--no-ai", dest="ai", action="store_false", help="关闭内容识别")
    parser.add_argument("--ai-backend", choices=["clip", "api"], default=None,
                        help="识别方式：clip=本地模型，api=调用 DeepSeek 视觉接口")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少个文件（调试用）")
    parser.set_defaults(ai=None)


def cmd_devices(args):
    try:
        devices = list_devices()
    except Exception as exc:
        print(f"检测失败：{exc}")
        return 2
    if not devices:
        print("未检测到手机。")
        print("请确认：USB 已连接、手机选择“传输文件 / MTP”、手机上点“允许”。")
        return 2
    print(f"检测到 {len(devices)} 台设备：")
    for index, device in enumerate(devices, 1):
        print(f"  {index}. {device.name}")
        for storage in device.storages:
            print(f"       - {storage}")
    return 0


def cmd_init(args):
    target = Path(args.output or DEFAULT_CONFIG_PATH)
    if target.exists() and not args.force:
        print(f"已存在：{target}（加 --force 覆盖）")
        return 0
    if EXAMPLE_CONFIG_PATH.exists() and target != EXAMPLE_CONFIG_PATH:
        target.write_text(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        save_config(DEFAULT_CONFIG, target)
    print(f"已生成配置：{target}")
    return 0


def cmd_scan(args):
    ensure_config()
    cfg = load_config(args.config)
    if args.ai is not None:
        cfg.setdefault("ai", {})["enabled"] = bool(args.ai)
    if args.ai_backend:
        cfg.setdefault("ai", {})["backend"] = args.ai_backend
    try:
        source = _build_source(args, cfg)
    except Exception as exc:
        print(f"打开来源失败：{exc}")
        return 2
    organizer = Organizer(
        cfg, source, args.out,
        include_camera=args.include_camera,
        dry_run=True,
        limit=args.limit,
    )
    result = organizer.preview()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"来源：{source.name}（{source.kind}）")
    print(f"扫描到媒体：{result['total']} 个")
    if result["camera"]:
        print(f"相机拍摄、将排除：{result['camera']} 个")
    print("-" * 48)
    for group, count in sorted(result["by_group"].items(), key=lambda kv: -kv[1]):
        size = result["by_group_bytes"].get(group, "")
        print(f"  {group:<10} {count:>5} 个   {size}")
        for example in result["examples"].get(group, [])[:2]:
            print(f"       · {example}")
    print("-" * 48)
    if result.get("ai_skipped"):
        print("提示：手机预览不读取图片内容，二次元/梗图会在正式整理时识别。")
    print("这是预览，不会复制任何文件。")
    return 0


def cmd_run(args):
    ensure_config()
    cfg = load_config(args.config)
    if args.ai is not None:
        cfg.setdefault("ai", {})["enabled"] = bool(args.ai)
    if args.ai_backend:
        cfg.setdefault("ai", {})["backend"] = args.ai_backend
    try:
        source = _build_source(args, cfg)
    except Exception as exc:
        print(f"打开来源失败：{exc}")
        return 2
    last = [0]

    def on_event(event):
        if event.get("type") == "scan_done":
            print(f"扫描完成：{event['count']} 个媒体文件")
        elif event.get("type") == "file_start":
            index = event["index"]
            if index - last[0] >= 20:
                last[0] = index
                print(f"  处理中 {index}/{event['total']}  {event['name'][:40]}")
        elif event.get("type") == "file_done" and event.get("status") == "error":
            print(f"  [失败] {event.get('name')}：{event.get('error', '')}")

    organizer = Organizer(
        cfg, source, args.out,
        include_camera=args.include_camera,
        dry_run=args.dry_run,
        limit=args.limit,
        on_event=on_event,
    )
    try:
        summary = organizer.run()
    except KeyboardInterrupt:
        print("\n已中断（已处理的文件已保存）。")
        return 130
    print(format_summary(summary))
    return 0


def cmd_setup_ai(args):
    from .model_download import download_model

    target = Path(args.dir) if args.dir else APP_DIR / "models" / "clip-vit-base-patch32"
    print(f"下载到：{target}")
    download_model(target)
    print("模型已就绪。")
    return 0


def cmd_gui(args):
    from .gui import main as gui_main
    return gui_main()


def build_parser():
    parser = argparse.ArgumentParser(
        prog="phonesort",
        description="把手机里“不是相机拍”的图片/视频，按你预设的分组整理到电脑。",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_devices = sub.add_parser("devices", help="列出已连接的手机")
    p_devices.set_defaults(func=cmd_devices)

    p_init = sub.add_parser("init", help="生成默认分组配置")
    p_init.add_argument("--output", default="", help="输出路径")
    p_init.add_argument("--force", action="store_true", help="覆盖已有文件")
    p_init.set_defaults(func=cmd_init)

    p_scan = sub.add_parser("scan", help="预览会怎么分组（不复制）")
    _add_common(p_scan)
    p_scan.add_argument("--json", action="store_true", help="输出 JSON")
    p_scan.set_defaults(func=cmd_scan)

    p_run = sub.add_parser("run", help="开始整理")
    _add_common(p_run)
    p_run.add_argument("--dry-run", action="store_true", help="只走流程不实际复制")
    p_run.set_defaults(func=cmd_run)

    p_ai = sub.add_parser("setup-ai", help="下载内容识别模型（约 580MB）")
    p_ai.add_argument("--dir", default="", help="模型保存目录")
    p_ai.set_defaults(func=cmd_setup_ai)

    p_gui = sub.add_parser("gui", help="打开图形界面")
    p_gui.set_defaults(func=cmd_gui)

    return parser


def main(argv=None):
    _setup_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
