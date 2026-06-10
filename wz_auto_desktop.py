from __future__ import annotations

import contextlib
import io
import queue
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
APP_VERSION = "0.0.7"


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


class PreviewWindow(tk.Toplevel):
    def __init__(self, master: "AutoDesktopApp"):
        super().__init__(master)
        self.title("实时游戏页面预览")
        self.geometry("920x560")
        self.minsize(640, 400)
        self.configure(bg="#111827")
        self.preview_image = None
        self.label = tk.Label(
            self,
            text="等待截图",
            bg="#111827",
            fg="#cbd5e1",
            font=("Microsoft YaHei UI", 12),
            anchor=tk.CENTER,
        )
        self.label.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.protocol("WM_DELETE_WINDOW", master.close_preview)

    def show_image(self, img) -> None:
        max_w = max(600, self.label.winfo_width() - 20)
        max_h = max(360, self.label.winfo_height() - 20)
        preview = img.copy()
        preview.thumbnail((max_w, max_h))
        self.preview_image = ImageTk.PhotoImage(preview)
        self.label.configure(image=self.preview_image, text="")


class AutoDesktopApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"王者荣耀自动练级工具 v{APP_VERSION}")
        self.geometry("650x230")
        self.resizable(False, False)

        self.cfg = wz_auto.load_config(CONFIG_PATH)
        self.adb = self._new_adb()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.preview_window: PreviewWindow | None = None
        self.preview_worker: threading.Thread | None = None
        self.preview_stop_event = threading.Event()
        self.latest_image = None

        self.interval = tk.DoubleVar(value=float(self.cfg.get("interval", 1.2)))
        self.status = tk.StringVar(value="就绪")
        self.device_text = tk.StringVar(value=f"设备：{self.cfg.get('device') or '未检测'}")
        self.log_text = tk.StringVar(value="日志：等待操作")

        self._configure_style()
        self._build_ui()
        self.after(120, self._drain_events)
        self.after(300, self.refresh_devices)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#f4f6fb")
        style.configure("Main.TLabel", background="#f4f6fb", foreground="#111827", font=("Microsoft YaHei UI", 10))
        style.configure("Meta.TLabel", background="#f4f6fb", foreground="#334155", font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background="#f4f6fb", foreground="#111827", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("Footer.TLabel", background="#f4f6fb", foreground="#64748b", font=("Microsoft YaHei UI", 9))
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(14, 7))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 7))

    def reload_config(self) -> None:
        self.cfg = wz_auto.load_config(CONFIG_PATH)
        self.adb = self._new_adb()

    def _new_adb(self) -> wz_auto.AdbClient:
        adb_path = wz_auto.resolve_adb_path(self.cfg.get("adb_path"))
        device = self.cfg.get("device") or "127.0.0.1:5555"
        display = str(self.cfg.get("display") or "2")
        return wz_auto.AdbClient(adb_path, device, display)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(root)
        top.pack(fill=tk.X)
        ttk.Label(top, text="王者荣耀自动练级工具", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(top, text=f"版本 {APP_VERSION}", style="Meta.TLabel").pack(side=tk.RIGHT)
        ttk.Label(top, textvariable=self.status, style="Meta.TLabel").pack(side=tk.RIGHT, padx=(0, 18))

        info = ttk.Frame(root)
        info.pack(fill=tk.X, pady=(16, 10))
        ttk.Label(info, textvariable=self.device_text, style="Main.TLabel").pack(side=tk.LEFT)
        ttk.Label(info, text="间隔秒", style="Main.TLabel").pack(side=tk.LEFT, padx=(24, 5))
        ttk.Spinbox(info, from_=0.5, to=30, increment=0.1, textvariable=self.interval, width=6).pack(side=tk.LEFT)
        ttk.Label(info, text="推荐 1.0-1.5 秒", style="Main.TLabel").pack(side=tk.LEFT, padx=(10, 0))

        buttons = ttk.Frame(root)
        buttons.pack(fill=tk.X, pady=(4, 12))
        ttk.Button(buttons, text="刷新设备", command=self.refresh_devices).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="自动检测", command=self.auto_config).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="开始", style="Primary.TButton", command=self.start_loop).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="停止", command=self.stop_loop).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="实时游戏页面预览", command=self.open_preview).pack(side=tk.LEFT)

        log = ttk.Frame(root)
        log.pack(fill=tk.X, pady=(2, 12))
        ttk.Label(log, textvariable=self.log_text, style="Meta.TLabel", anchor=tk.W).pack(fill=tk.X)

        ttk.Label(root, text="Designed by 小北", style="Footer.TLabel").pack(side=tk.BOTTOM, anchor=tk.CENTER)

    def open_preview(self) -> None:
        if self.preview_window is None or not self.preview_window.winfo_exists():
            self.preview_window = PreviewWindow(self)
        self.preview_window.deiconify()
        self.preview_window.lift()
        if self.latest_image is not None:
            self.preview_window.show_image(self.latest_image)
        self._start_preview_worker()

    def close_preview(self) -> None:
        self.preview_stop_event.set()
        if self.preview_window is not None and self.preview_window.winfo_exists():
            self.preview_window.withdraw()

    def _start_preview_worker(self) -> None:
        if self.preview_worker and self.preview_worker.is_alive():
            return
        self.preview_stop_event.clear()

        def worker():
            while not self.preview_stop_event.is_set():
                try:
                    # The main automation loop already refreshes the popup.
                    # When the loop is idle, keep the preview window live.
                    if not (self.worker and self.worker.is_alive()):
                        self.reload_config()
                        img = self.adb.capture()
                        self.events.put(("preview", img))
                except Exception as exc:
                    self.log_line(f"[preview error] {exc}")
                time.sleep(max(0.5, float(self.interval.get())))

        self.preview_worker = threading.Thread(target=worker, daemon=True)
        self.preview_worker.start()

    def _capture_preview_once(self) -> None:
        self.reload_config()
        img = self.adb.capture()
        self.events.put(("preview", img))
        self.log_line("[preview] 已更新实时游戏页面预览")

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
                self.log_text.set(f"日志：{payload}")
            elif kind == "status":
                self.status.set(str(payload))
            elif kind == "device":
                self.device_text.set(str(payload))
            elif kind == "preview":
                self.latest_image = payload
                if self.preview_window is not None and self.preview_window.winfo_exists():
                    self.preview_window.show_image(payload)
        self.after(120, self._drain_events)

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
            configured = str(self.cfg.get("device") or "")
            device = configured if configured in devices else (devices[0] if devices else configured)
            if device and device != configured:
                wz_auto.update_config_file(CONFIG_PATH, {"device": device})
                self.reload_config()
                self.log_line(f"[auto] device={device} 已写入 config.yaml")
            self.events.put(("device", f"设备：{device} 已连接" if device in devices else "设备：未确认连接"))
            self.log_line("[device] 刷新完成")

        self._run_bg("刷新设备中", task)

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
            self.events.put(("preview", result["image"]))
            self.events.put(("device", f"设备：{result['device']} 已连接"))
            self.log_line(f"[auto] display={result['display']} score={result['display_score']:.3f}")

        self._run_bg("自动检测中", task)

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
                self.events.put(("device", f"设备：{result['device']} 已连接"))
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
