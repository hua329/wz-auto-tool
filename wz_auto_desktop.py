from __future__ import annotations

import contextlib
import io
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from PIL import ImageTk

import wz_auto


APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.yaml"
APP_VERSION = "0.0.8"

BG = "#edf2f7"
CARD = "#ffffff"
INK = "#111827"
MUTED = "#64748b"
LINE = "#d9e2ec"
HEADER = "#172033"
PRIMARY = "#2563eb"
PRIMARY_HOVER = "#1d4ed8"
DANGER = "#dc2626"
DANGER_HOVER = "#b91c1c"
SECONDARY = "#e2e8f0"
SECONDARY_HOVER = "#cbd5e1"


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
        self.geometry("980x600")
        self.minsize(720, 440)
        self.configure(bg="#0f172a")
        self.preview_image = None

        header = tk.Frame(self, bg="#111827", height=48)
        header.pack(fill=tk.X)
        tk.Label(
            header,
            text="实时游戏页面预览",
            bg="#111827",
            fg="#f8fafc",
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(side=tk.LEFT, padx=16)
        tk.Label(
            header,
            text="窗口关闭后不会停止自动循环",
            bg="#111827",
            fg="#94a3b8",
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.RIGHT, padx=16)

        body = tk.Frame(self, bg="#0f172a")
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        self.label = tk.Label(
            body,
            text="等待截图",
            bg="#020617",
            fg="#94a3b8",
            font=("Microsoft YaHei UI", 12),
            anchor=tk.CENTER,
            bd=0,
        )
        self.label.pack(fill=tk.BOTH, expand=True)
        self.protocol("WM_DELETE_WINDOW", master.close_preview)

    def show_image(self, img) -> None:
        max_w = max(680, self.label.winfo_width() - 18)
        max_h = max(400, self.label.winfo_height() - 18)
        preview = img.copy()
        preview.thumbnail((max_w, max_h))
        self.preview_image = ImageTk.PhotoImage(preview)
        self.label.configure(image=self.preview_image, text="")


class AutoDesktopApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"王者荣耀自动练级工具 v{APP_VERSION}")
        self.geometry("760x360")
        self.resizable(False, False)
        self.configure(bg=BG)

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
        self.device_text = tk.StringVar(value=self.cfg.get("device") or "未检测")
        self.display_text = tk.StringVar(value=f"Display {self.cfg.get('display') or '自动'}")
        self.log_text = tk.StringVar(value="等待操作")

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
        shell = tk.Frame(self, bg=BG)
        shell.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        self._build_header(shell)
        self._build_status_cards(shell)
        self._build_actions(shell)
        self._build_log(shell)
        tk.Label(shell, text="Designed by 小北", bg=BG, fg="#7b8794", font=("Microsoft YaHei UI", 9)).pack(side=tk.BOTTOM)

    def _build_header(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, bg=HEADER, height=76, highlightthickness=0)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        left = tk.Frame(header, bg=HEADER)
        left.pack(side=tk.LEFT, padx=18, pady=12)
        tk.Label(
            left,
            text="王者荣耀自动练级工具",
            bg=HEADER,
            fg="#ffffff",
            font=("Microsoft YaHei UI", 17, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            left,
            text="自动检测 · 循环点击 · 对局等待",
            bg=HEADER,
            fg="#b6c2d2",
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor=tk.W, pady=(3, 0))

        right = tk.Frame(header, bg=HEADER)
        right.pack(side=tk.RIGHT, padx=18)
        self.status_pill = tk.Label(
            right,
            textvariable=self.status,
            bg="#0f766e",
            fg="#ecfeff",
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=14,
            pady=6,
        )
        self.status_pill.pack(side=tk.RIGHT)
        tk.Label(
            right,
            text=f"v{APP_VERSION}",
            bg=HEADER,
            fg="#cbd5e1",
            font=("Microsoft YaHei UI", 10),
        ).pack(side=tk.RIGHT, padx=(0, 14))

    def _build_status_cards(self, parent: tk.Frame) -> None:
        row = tk.Frame(parent, bg=BG)
        row.pack(fill=tk.X, pady=(12, 10))
        self._card(row, "ADB 设备", self.device_text, 255).pack(side=tk.LEFT, padx=(0, 10))
        self._card(row, "画面编号", self.display_text, 150).pack(side=tk.LEFT, padx=(0, 10))

        interval = tk.Frame(row, bg=CARD, width=300, height=76, highlightbackground=LINE, highlightthickness=1)
        interval.pack(side=tk.LEFT, fill=tk.X, expand=True)
        interval.pack_propagate(False)
        tk.Label(interval, text="识别间隔", bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor=tk.W, padx=14, pady=(10, 2))
        line = tk.Frame(interval, bg=CARD)
        line.pack(anchor=tk.W, padx=14)
        tk.Spinbox(
            line,
            from_=0.5,
            to=30,
            increment=0.1,
            textvariable=self.interval,
            width=6,
            font=("Microsoft YaHei UI", 10),
            relief=tk.SOLID,
            bd=1,
            justify=tk.CENTER,
        ).pack(side=tk.LEFT)
        tk.Label(line, text="秒", bg=CARD, fg=INK, font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT, padx=(6, 12))
        tk.Label(line, text="推荐 1.0-1.5", bg="#eef6ff", fg="#2563eb", font=("Microsoft YaHei UI", 9), padx=8, pady=2).pack(side=tk.LEFT)

    def _card(self, parent: tk.Frame, title: str, value: tk.StringVar, width: int) -> tk.Frame:
        card = tk.Frame(parent, bg=CARD, width=width, height=76, highlightbackground=LINE, highlightthickness=1)
        card.pack_propagate(False)
        tk.Label(card, text=title, bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor=tk.W, padx=14, pady=(10, 2))
        tk.Label(card, textvariable=value, bg=CARD, fg=INK, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor=tk.W, padx=14)
        return card

    def _build_actions(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg=CARD, height=82, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill=tk.X)
        card.pack_propagate(False)

        row = tk.Frame(card, bg=CARD)
        row.pack(fill=tk.X, padx=12, pady=14)
        self._button(row, "刷新设备", self.refresh_devices, SECONDARY, INK, SECONDARY_HOVER, 108).pack(side=tk.LEFT, padx=(0, 8))
        self._button(row, "自动检测", self.auto_config, SECONDARY, INK, SECONDARY_HOVER, 108).pack(side=tk.LEFT, padx=(0, 8))
        self._button(row, "开始", self.start_loop, PRIMARY, "#ffffff", PRIMARY_HOVER, 108).pack(side=tk.LEFT, padx=(0, 8))
        self._button(row, "停止", self.stop_loop, DANGER, "#ffffff", DANGER_HOVER, 96).pack(side=tk.LEFT, padx=(0, 8))
        self._button(row, "实时游戏页面预览", self.open_preview, "#111827", "#ffffff", "#1f2937", 170).pack(side=tk.LEFT)

    def _button(self, parent: tk.Frame, text: str, command, bg: str, fg: str, active_bg: str, width: int) -> tk.Button:
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=active_bg,
            activeforeground=fg,
            relief=tk.FLAT,
            bd=0,
            width=max(8, width // 10),
            height=2,
            cursor="hand2",
            font=("Microsoft YaHei UI", 10, "bold" if bg in (PRIMARY, DANGER, "#111827") else "normal"),
        )
        btn.bind("<Enter>", lambda _e: btn.configure(bg=active_bg))
        btn.bind("<Leave>", lambda _e: btn.configure(bg=bg))
        return btn

    def _build_log(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg=CARD, height=54, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill=tk.X, pady=(10, 8))
        card.pack_propagate(False)
        tk.Label(card, text="最新日志", bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT, padx=(14, 10))
        tk.Label(card, textvariable=self.log_text, bg=CARD, fg=INK, font=("Consolas", 9), anchor=tk.W).pack(side=tk.LEFT, fill=tk.X, expand=True)

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
                    if not (self.worker and self.worker.is_alive()):
                        self.reload_config()
                        img = self.adb.capture()
                        self.events.put(("preview", img))
                except Exception as exc:
                    self.log_line(f"[preview error] {exc}")
                time.sleep(max(0.5, float(self.interval.get())))

        self.preview_worker = threading.Thread(target=worker, daemon=True)
        self.preview_worker.start()

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
                self.log_text.set(str(payload))
            elif kind == "status":
                self.status.set(str(payload))
                self.status_pill.configure(bg="#0f766e" if str(payload) == "就绪" else "#2563eb")
            elif kind == "device":
                self.device_text.set(str(payload))
            elif kind == "display":
                self.display_text.set(str(payload))
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
            self.events.put(("device", f"{device} 已连接" if device in devices else "未确认连接"))
            self.events.put(("display", f"Display {self.cfg.get('display') or '自动'}"))
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
            self.events.put(("device", f"{result['device']} 已连接"))
            self.events.put(("display", f"Display {result['display']}"))
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
                self.events.put(("device", f"{result['device']} 已连接"))
                self.events.put(("display", f"Display {result['display']}"))
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
