# -*- coding: utf-8 -*-
"""图形界面（tkinter）。"""
from __future__ import annotations

import os
import queue
import threading
import traceback
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import APP_DIR, ensure_config, load_config
from .pipeline import Organizer, format_summary
from .source import FolderSource, MtpSource, list_devices

FONT = "Microsoft YaHei UI"
BG = "#f4f5f7"
CARD = "#ffffff"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("手机相册分组整理")
        self.geometry("1000x860")
        self.minsize(900, 740)
        self.configure(bg=BG)

        self.cfg_path = ensure_config()
        self.cfg = load_config(self.cfg_path)
        self.devices = []
        self.groups_state: dict[str, bool] = {}
        self.queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.busy = False

        self._init_style()
        self._build_ui()
        self._load_groups()
        self.after(120, self._poll)
        self._refresh_devices()

    # ------------------------------------------------------------- style
    def _init_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure(".", font=(FONT, 10))
        style.configure("Title.TLabel", font=(FONT, 17, "bold"), background=CARD, foreground="#1f2329")
        style.configure("Sub.TLabel", font=(FONT, 9), background=CARD, foreground="#7a7f87")
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabelframe", background=BG)
        style.configure("TLabelframe.Label", font=(FONT, 10, "bold"), foreground="#333333")
        style.configure("Treeview", font=(FONT, 10), rowheight=26, background=CARD, fieldbackground=CARD)
        style.configure("Treeview.Heading", font=(FONT, 9, "bold"))
        style.configure("Accent.TButton", font=(FONT, 10, "bold"))
        style.configure("Hint.TLabel", font=(FONT, 9), foreground="#8a8f98")

    # ------------------------------------------------------------- ui
    def _build_ui(self):
        header = ttk.Frame(self, style="Card.TFrame", padding=(20, 14, 20, 12))
        header.pack(fill="x")
        ttk.Label(header, text="手机相册分组整理", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="连接手机后，自动跳过相机拍摄的照片/视频，把截图、微信、QQ、下载、录屏等按你的分组归档。",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        body = ttk.Frame(self, padding=(16, 12, 16, 12))
        body.pack(fill="both", expand=True)

        # --- 来源
        src = ttk.LabelFrame(body, text=" 手机 / 来源 ", padding=10)
        src.pack(fill="x")
        src.columnconfigure(1, weight=1)

        ttk.Label(src, text="设备：").grid(row=0, column=0, sticky="w")
        self.device_var = tk.StringVar()
        self.device_box = ttk.Combobox(src, textvariable=self.device_var, state="readonly", width=34)
        self.device_box.grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.device_box.bind("<<ComboboxSelected>>", lambda _e: self._update_storages())
        self.refresh_btn = ttk.Button(src, text="刷新", width=8, command=self._refresh_devices)
        self.refresh_btn.grid(row=0, column=2, sticky="w")
        ttk.Label(src, text="存储：").grid(row=0, column=3, sticky="w", padx=(18, 4))
        self.storage_var = tk.StringVar(value="全部存储")
        self.storage_box = ttk.Combobox(src, textvariable=self.storage_var, state="readonly", width=20)
        self.storage_box.grid(row=0, column=4, sticky="w")

        ttk.Label(src, text="或本地文件夹：").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.source_dir_var = tk.StringVar()
        ttk.Entry(src, textvariable=self.source_dir_var).grid(
            row=1, column=1, columnspan=3, sticky="ew", pady=(10, 0), padx=(0, 8)
        )
        ttk.Button(src, text="浏览…", width=8, command=self._browse_source).grid(
            row=1, column=4, sticky="w", pady=(10, 0)
        )
        ttk.Label(
            src,
            text="提示：手机需用 USB 连接，并在手机上选择“传输文件 / MTP”，再点“允许”。",
            style="Hint.TLabel",
        ).grid(row=2, column=0, columnspan=5, sticky="w", pady=(8, 0))

        # --- 输出
        out = ttk.LabelFrame(body, text=" 输出位置 ", padding=10)
        out.pack(fill="x", pady=(10, 0))
        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="保存到：").grid(row=0, column=0, sticky="w")
        self.out_var = tk.StringVar(value=str(APP_DIR / "整理结果"))
        ttk.Entry(out, textvariable=self.out_var).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(out, text="浏览…", width=8, command=self._browse_out).grid(row=0, column=2, sticky="w")
        ttk.Button(out, text="打开", width=8, command=self._open_out).grid(row=0, column=3, sticky="w", padx=(8, 0))

        # --- 分组
        groups = ttk.LabelFrame(body, text=" 我的分组（点击第一列 ☑ 可启用/停用） ", padding=10)
        groups.pack(fill="both", expand=False, pady=(10, 0))
        columns = ("on", "name", "rules", "count", "note")
        self.tree = ttk.Treeview(groups, columns=columns, show="headings", height=6)
        headings = {"on": "启用", "name": "分组", "rules": "规则数", "count": "匹配", "note": "说明"}
        widths = {"on": 50, "name": 160, "rules": 70, "count": 70, "note": 520}
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor="w" if col in ("name", "note") else "center")
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(groups, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<Button-1>", self._toggle_group)

        group_bar = ttk.Frame(body)
        group_bar.pack(fill="x", pady=(6, 0))
        ttk.Button(group_bar, text="编辑分组规则", command=self._edit_groups).pack(side="left")
        ttk.Button(group_bar, text="重新加载分组", command=self._reload_groups).pack(side="left", padx=(8, 0))
        ttk.Label(group_bar, text="规则写在 groups.yaml，可用记事本编辑。", style="Hint.TLabel").pack(
            side="left", padx=(12, 0)
        )

        # --- 选项
        options = ttk.Frame(body)
        options.pack(fill="x", pady=(8, 0))
        self.include_camera_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options, text="包含相机拍摄的照片/视频（默认排除）", variable=self.include_camera_var
        ).pack(side="left")
        self.dry_run_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options, text="仅预览，不实际复制", variable=self.dry_run_var).pack(
            side="left", padx=(20, 0)
        )

        # --- 操作
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(10, 0))
        self.scan_btn = ttk.Button(actions, text="扫描预览", command=self._start_scan)
        self.scan_btn.pack(side="left")
        self.run_btn = ttk.Button(actions, text="开始整理", style="Accent.TButton", command=self._start_run)
        self.run_btn.pack(side="left", padx=(8, 0))
        self.cancel_btn = ttk.Button(actions, text="取消", command=self._cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=(8, 0))

        # --- 进度
        progress_frame = ttk.Frame(body)
        progress_frame.pack(fill="x", pady=(12, 0))
        self.progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        self.status_var = tk.StringVar(value="准备就绪")
        ttk.Label(progress_frame, textvariable=self.status_var, style="Hint.TLabel").pack(anchor="w", pady=(4, 0))

        # --- 日志
        log_frame = ttk.LabelFrame(body, text=" 运行日志 ", padding=6)
        log_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.log_text = tk.Text(
            log_frame, height=5, wrap="word", font=(FONT, 9), bg="#1e1f22", fg="#d7dae0",
            insertbackground="#d7dae0", relief="flat", padx=8, pady=6,
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=log_scroll.set, state="disabled")

    # ------------------------------------------------------------- helpers
    def _log(self, message: str):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_busy(self, busy: bool):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for button in (self.scan_btn, self.run_btn, self.refresh_btn):
            button.configure(state=state)
        self.cancel_btn.configure(state="normal" if busy else "disabled")

    def _selected_storages(self) -> list[str]:
        value = self.storage_var.get().strip()
        if not value or value == "全部存储":
            return []
        return [value]

    def _make_source(self):
        folder = self.source_dir_var.get().strip()
        if folder:
            return FolderSource(folder)
        device = self.device_var.get().strip()
        if not device:
            raise RuntimeError("没有选择设备。请先连接手机并点“刷新”。")
        return MtpSource(device, self._selected_storages())

    def _load_groups(self):
        self.tree.delete(*self.tree.get_children())
        self.groups_state = {}
        for group in self.cfg.get("groups", []):
            name = group.get("name", "未命名")
            enabled = bool(group.get("enabled", True))
            self.groups_state[name] = enabled
            note = "默认分组（兜底）" if group.get("default") else ""
            self.tree.insert("", "end", iid=name, values=(
                "☑" if enabled else "☐", name, len(group.get("rules") or []), "-", note,
            ))

    def _toggle_group(self, event):
        if self.busy:
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.groups_state[row] = not self.groups_state.get(row, True)
        self.tree.set(row, "on", "☑" if self.groups_state[row] else "☐")

    def _apply_group_states(self):
        for group in self.cfg.get("groups", []):
            name = group.get("name", "")
            if name in self.groups_state:
                group["enabled"] = self.groups_state[name]

    def _refresh_devices(self):
        if self.busy:
            return
        self.status_var.set("正在检测手机…")
        self.refresh_btn.configure(state="disabled")

        def work():
            try:
                devices = list_devices()
                self.queue.put(("devices", devices))
            except Exception as exc:
                self.queue.put(("devices_error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _update_storages(self):
        name = self.device_var.get()
        device = next((d for d in self.devices if d.name == name), None)
        values = ["全部存储"] + (device.storages if device else [])
        self.storage_box.configure(values=values)
        self.storage_var.set("全部存储")

    def _browse_source(self):
        path = filedialog.askdirectory(title="选择要整理的本地文件夹")
        if path:
            self.source_dir_var.set(path)

    def _browse_out(self):
        path = filedialog.askdirectory(title="选择输出文件夹")
        if path:
            self.out_var.set(path)

    def _open_out(self):
        path = Path(self.out_var.get().strip() or (APP_DIR / "整理结果"))
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))

    def _edit_groups(self):
        os.startfile(str(self.cfg_path))

    def _reload_groups(self):
        try:
            self.cfg = load_config(self.cfg_path)
            self._load_groups()
            self._log("已重新加载分组配置。")
        except Exception as exc:
            messagebox.showerror("配置错误", str(exc))

    # ------------------------------------------------------------- workers
    def _start_scan(self):
        if self.busy:
            return
        self.cfg = load_config(self.cfg_path)
        self._apply_group_states()
        out_dir = self.out_var.get().strip()
        if not out_dir:
            messagebox.showwarning("提示", "请先选择输出位置。")
            return
        self._begin_work("正在扫描…")
        self._log("开始扫描（只预览，不复制）。")

        def work():
            try:
                source = self._make_source()
                organizer = Organizer(
                    self.cfg, source, out_dir,
                    include_camera=self.include_camera_var.get(),
                    dry_run=True,
                    on_event=lambda event: self.queue.put(("event", event)),
                    cancel=self.cancel_event,
                )
                result = organizer.preview()
                self.queue.put(("preview", result))
            except Exception as exc:
                self.queue.put(("error", f"{exc}\n{traceback.format_exc(limit=2)}"))

        self._spawn(work)

    def _start_run(self):
        if self.busy:
            return
        self.cfg = load_config(self.cfg_path)
        self._apply_group_states()
        out_dir = self.out_var.get().strip()
        if not out_dir:
            messagebox.showwarning("提示", "请先选择输出位置。")
            return
        dry = self.dry_run_var.get()
        self._begin_work("正在整理…")
        self._log("开始整理" + ("（预览模式，不会复制文件）" if dry else "。"))

        def work():
            try:
                source = self._make_source()
                organizer = Organizer(
                    self.cfg, source, out_dir,
                    include_camera=self.include_camera_var.get(),
                    dry_run=dry,
                    on_event=lambda event: self.queue.put(("event", event)),
                    cancel=self.cancel_event,
                )
                summary = organizer.run()
                self.queue.put(("done", summary))
            except Exception as exc:
                self.queue.put(("error", f"{exc}\n{traceback.format_exc(limit=2)}"))

        self._spawn(work)

    def _spawn(self, work):
        self.cancel_event.clear()
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _begin_work(self, status: str):
        self._set_busy(True)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set(status)

    def _cancel(self):
        self.cancel_event.set()
        self.status_var.set("正在取消…")

    # ------------------------------------------------------------- queue
    def _poll(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "devices":
                    self._on_devices(payload)
                elif kind == "devices_error":
                    self.status_var.set("检测手机失败")
                    self._log(f"检测手机失败：{payload}")
                elif kind == "event":
                    self._on_event(payload)
                elif kind == "preview":
                    self._on_preview(payload)
                elif kind == "done":
                    self._on_done(payload)
                elif kind == "error":
                    self._on_error(payload)
        except queue.Empty:
            pass
        self.after(120, self._poll)

    def _on_devices(self, devices):
        self.devices = devices
        names = [d.name for d in devices]
        self.device_box.configure(values=names)
        if names:
            self.device_var.set(names[0])
            self._update_storages()
            self.status_var.set(f"已连接：{names[0]}")
            self._log(f"检测到手机：{names[0]}")
        else:
            self.device_var.set("")
            self.storage_box.configure(values=["全部存储"])
            self.storage_var.set("全部存储")
            self.status_var.set("未检测到手机")
            self._log("未检测到手机。请连接 USB 并选择“传输文件 / MTP”。")
        self.refresh_btn.configure(state="normal")

    def _on_event(self, event):
        kind = event.get("type")
        if kind == "scan_progress":
            self.status_var.set(f"正在扫描… 已发现 {event.get('count', 0)} 个")
        elif kind == "scan_done":
            total = event.get("count", 0)
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=max(total, 1), value=0)
            self.status_var.set(f"扫描完成，共 {total} 个媒体文件")
            self._log(f"扫描完成：{total} 个媒体文件")
        elif kind == "file_start":
            self.progress.configure(maximum=max(event.get("total", 1), 1), value=event.get("index", 1) - 1)
            self.status_var.set(f"[{event.get('index')}/{event.get('total')}] {event.get('name', '')}")
        elif kind == "file_done":
            if event.get("status") == "error":
                self._log(f"失败：{event.get('name')} — {event.get('error', '')}")

    def _on_preview(self, result):
        self._finish_work()
        for group, count in result.get("by_group", {}).items():
            if self.tree.exists(group):
                self.tree.set(group, "count", str(count))
        total = result.get("total", 0)
        lines = [f"预览：共 {total} 个，相机排除 {result.get('camera', 0)} 个"]
        for group, count in sorted(result.get("by_group", {}).items(), key=lambda kv: -kv[1]):
            lines.append(f"  {group}：{count} 个（{result.get('by_group_bytes', {}).get(group, '')}）")
        self._log("\n".join(lines))
        self.status_var.set(f"预览完成：共 {total} 个，相机排除 {result.get('camera', 0)} 个")
        messagebox.showinfo("扫描预览", "\n".join(lines))

    def _on_done(self, summary):
        self._finish_work()
        for group, count in summary.by_group.items():
            if self.tree.exists(group):
                self.tree.set(group, "count", str(count))
        text = format_summary(summary)
        self._log(text)
        self.status_var.set("整理完成" if not summary.dry_run else "预览完成")
        messagebox.showinfo("完成", text)

    def _on_error(self, message):
        self._finish_work()
        self.status_var.set("出错了")
        self._log(f"错误：{message}")
        messagebox.showerror("出错", message)

    def _finish_work(self):
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self._set_busy(False)


def main() -> int:
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
