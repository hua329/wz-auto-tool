from __future__ import annotations

import contextlib
import io
import queue
import re
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import ImageTk

import wz_auto


APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.yaml"
APP_VERSION = "0.0.4"


class LogCapture(contextlib.AbstractContextManager):
    def __init__(self, callback):
        self.callback = callback
        self.buffer = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.buffer)

    def __enter__(self):
        self.redirect.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.redirect.__exit__(exc_type, exc, tb)
        text = self.buffer.getvalue().strip()
        if text:
            for line in text.splitlines():
                self.callback(line)
        return False


class AutoDesktopApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"王者荣耀自动练级工具 v{APP_VERSION}")
        self.geometry("1100x760")
        self.minsize(980, 680)

        self.cfg = wz_auto.load_config(CONFIG_PATH)
        self.adb = self._new_adb()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.preview_image = None

        self.interval = tk.DoubleVar(value=float(self.cfg.get("interval", 1.2)))
        self.status = tk.StringVar(value="就绪")

        self._build_ui()
        self.after(120, self._drain_events)
        self.after(300, self.refresh_devices)

    def reload_config(self) -> None:
        self.cfg = wz_auto.load_config(CONFIG_PATH)
        self.adb = self._new_adb()

    def _new_adb(self) -> wz_auto.AdbClient:
        adb_path = wz_auto.resolve_adb_path(self.cfg.get("adb_path"))
        device = self.cfg.get("device") or "127.0.0.1:5555"
        display = str(self.cfg.get("display") or "2")
        return wz_auto.AdbClient(adb_path, device, display)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(root)
        top.pack(fill=tk.X)

        self.device_label = ttk.Label(top, text="设备：检查中")
        self.device_label.pack(side=tk.LEFT)

        ttk.Label(top, text="间隔秒").pack(side=tk.LEFT, padx=(20, 4))
        ttk.Spinbox(top, from_=0.5, to=30, increment=0.5, textvariable=self.interval, width=6).pack(side=tk.LEFT)
        ttk.Label(top, text="推荐 1.0-1.5 秒").pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(top, textvariable=self.status, foreground="#1f5f99").pack(side=tk.RIGHT)
        ttk.Label(top, text=f"版本 {APP_VERSION}").pack(side=tk.RIGHT, padx=(0, 16))

        buttons = ttk.Frame(root)
        buttons.pack(fill=tk.X, pady=(10, 8))

        ttk.Button(buttons, text="刷新设备", command=self.refresh_devices).pack(side=tk.LEFT)
        ttk.Button(buttons, text="自动检测", command=self.auto_config).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="开始循环", command=self.start_loop).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="停止", command=self.stop_loop).pack(side=tk.LEFT, padx=6)

        middle = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        middle.pack(fill=tk.BOTH, expand=True)

        preview_frame = ttk.Labelframe(middle, text="ADB 画面预览")
        self.preview = ttk.Label(preview_frame, anchor=tk.CENTER)
        self.preview.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        middle.add(preview_frame, weight=3)

        log_frame = ttk.Labelframe(middle, text="日志")
        self.log = tk.Text(log_frame, height=20, wrap=tk.WORD)
        self.log.configure(state=tk.DISABLED)
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        middle.add(log_frame, weight=2)

    def log_line(self, text: str) -> None:
        self.events.put(("log", text))

    def set_status(self, text: str) -> None:
        self.events.put(("status", text))

    def _drain_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log.configure(state=tk.NORMAL)
                self.log.insert(tk.END, str(payload) + "\n")
                self.log.see(tk.END)
                self.log.configure(state=tk.DISABLED)
            elif kind == "status":
                self.status.set(str(payload))
            elif kind == "preview":
                img = payload
                self._show_preview(img)
        self.after(120, self._drain_events)

    def _show_preview(self, img) -> None:
        max_w, max_h = 660, 430
        preview = img.copy()
        preview.thumbnail((max_w, max_h))
        self.preview_image = ImageTk.PhotoImage(preview)
        self.preview.configure(image=self.preview_image)

    def _run_bg(self, name: str, func) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("正在运行", "已有任务正在运行，请先停止或等待完成。")
            return
        self.stop_event.clear()

        def wrapped():
            self.set_status(name)
            try:
                func()
            except Exception as exc:
                self.log_line(f"[error] {exc}")
            finally:
                self.set_status("就绪")

        self.worker = threading.Thread(target=wrapped, daemon=True)
        self.worker.start()

    def refresh_devices(self) -> None:
        def task():
            self.reload_config()
            adb_path = wz_auto.resolve_adb_path(self.cfg.get("adb_path"))
            devices = wz_auto.ensure_connected_devices(adb_path, self.cfg.get("device"))
            proc = wz_auto.adb_command(adb_path, "devices", "-l", timeout=10)
            output = proc.stdout.strip() or proc.stderr.strip()
            self.events.put(("log", output or "List of devices attached"))
            configured = str(self.cfg.get("device") or "")
            device = configured if configured in devices else (devices[0] if devices else configured)
            if device and device != configured:
                wz_auto.update_config_file(CONFIG_PATH, {"device": device})
                self.reload_config()
                self.log_line(f"[auto] device={device} 已写入 config.yaml")
            text = f"设备：{device} 已连接" if device in devices else "设备：未确认连接"
            self.events.put(("device", text))
            self.device_label.after(0, lambda: self.device_label.configure(text=text))

        self._run_bg("刷新设备", task)

    def auto_config(self) -> None:
        def task():
            self.reload_config()
            result = wz_auto.auto_detect_config(self.cfg)
            wz_auto.update_config_file(
                CONFIG_PATH,
                {
                    "adb_path": result["adb_path"],
                    "device": result["device"],
                    "display": result["display"],
                },
            )
            self.reload_config()
            img = result["image"]
            self.events.put(("preview", img))
            text = f"设备：{result['device']} 已连接"
            self.device_label.after(0, lambda: self.device_label.configure(text=text))
            self.log_line(f"[auto] adb_path={result['adb_path']}")
            self.log_line(f"[auto] device={result['device']}")
            self.log_line(f"[auto] display={result['display']} score={result['display_score']:.3f}")
            self.log_line(f"[auto] devices={', '.join(result['devices'])}")
            self.log_line("[auto] 已写入 config.yaml")

        self._run_bg("自动检测", task)

    def start_loop(self) -> None:
        def task():
            self.reload_config()
            try:
                result = wz_auto.auto_detect_config(self.cfg)
                wz_auto.update_config_file(
                    CONFIG_PATH,
                    {
                        "adb_path": result["adb_path"],
                        "device": result["device"],
                        "display": result["display"],
                    },
                )
                self.reload_config()
                self.events.put(("preview", result["image"]))
                self.device_label.after(0, lambda: self.device_label.configure(text=f"设备：{result['device']} 已连接"))
                self.log_line(f"[auto] device={result['device']} display={result['display']}")
            except Exception as exc:
                self.log_line(f"[auto warning] {exc}")
            self.log_line("[loop] start enable_clicks=True")
            while not self.stop_event.is_set():
                img = self.adb.capture()
                self.events.put(("preview", img))
                result = wz_auto.find_state(img, self.cfg.get("states", []), verbose=False)
                if result:
                    with LogCapture(self.log_line):
                        wz_auto.perform_action(self.adb, img, result, dry_run=False)
                    time.sleep(result.delay_after)
                else:
                    self.log_line("[wait] no configured state matched")
                    time.sleep(max(0.3, float(self.interval.get())))
            self.log_line("[loop] stopped")

        self._run_bg("循环运行中", task)

    def stop_loop(self) -> None:
        self.stop_event.set()
        self.log_line("[stop] stop requested")


if __name__ == "__main__":
    app = AutoDesktopApp()
    app.mainloop()
