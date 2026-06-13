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
from tkinter import messagebox

from PIL import ImageTk

import wz_auto


APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.yaml"
APP_VERSION = "1.0.1"

BG = "#1f252f"
PANEL = "#333b49"
CARD = "#49515f"
CARD_DARK = "#38414d"
BORDER = "#687280"
TEXT = "#f8fafc"
MUTED = "#a8b0bd"
SUBTLE = "#7f8794"
GREEN = "#38d97a"
GREEN_DARK = "#246a45"
PRIMARY = "#5d8cff"
PRIMARY_DARK = "#416ee6"
DANGER = "#ef4444"
DANGER_DARK = "#b91c1c"
BUTTON = "#4b5563"
BUTTON_HOVER = "#5b6573"
BLACK_BAR = "#303844"


FRIENDLY_STATES = {
    "00_in_game_minimap_guard": "游戏中，等待对局结束",
    "01_home": "进入首页，点击对战",
    "02_battle_5v5": "进入对战，选择 5v5",
    "03_arena_ai": "进入王者峡谷，选择人机",
    "04_ai_start_practice": "进入人机模式，开始练习",
    "05_room_start_match": "进入房间，开始匹配",
    "06_match_success_confirm": "匹配成功，点击确认",
    "07_hero_select_open_list": "进入选英雄，打开英雄列表",
    "07_hero_list_choose_mid_tab": "选择中路分类",
    "07_hero_mid_pick_milaidi": "选择米莱狄",
    "07_hero_select_confirm": "确认英雄",
    "07_hero_select_confirm_gold": "确认英雄",
    "08_loading": "游戏加载中，等待进入对局",
    "09_in_game_wait": "游戏中，等待对局结束",
    "10_defeat_tap_continue": "失败，点击继续",
    "10_result_continue_prompt": "结算页面，点击继续",
    "10_victory_badge_tap_continue": "胜利，点击继续",
    "10_victory_tap_continue": "胜利，点击继续",
    "11_performance_continue": "表现页面，点击继续",
    "11_victory_mvp_continue": "MVP 页面，点击继续",
    "12_scoreboard_continue": "战绩页面，返回房间",
    "12_victory_scoreboard_back_room": "战绩页面，返回房间",
}


def friendly_log_text(text: str) -> str:
    state_match = re.search(r"\[(?:tap|state|dry-run)\]\s+([A-Za-z0-9_]+)", text)
    if not state_match:
        state_match = re.search(r"\[followup tap\]\s+([A-Za-z0-9_]+)", text)
    if state_match:
        return FRIENDLY_STATES.get(state_match.group(1), "识别到页面，继续执行")
    if text.startswith("[wait]"):
        return "等待识别当前页面"
    if text.startswith("[loop] start"):
        return "开始循环"
    if text.startswith("[loop] stopped"):
        return "循环已停止"
    if text.startswith("[stop]"):
        return "已请求停止"
    if text.startswith("[auto] start"):
        return "开始自动检测设备"
    if text.startswith("[auto] device=") or text.startswith("[auto] display="):
        return "自动检测完成"
    if text.startswith("[auto warning]"):
        return "自动检测提醒，请确认模拟器已开启 ADB"
    if text.startswith("[preview error]"):
        return "实时预览更新失败"
    if text.startswith("[error]"):
        return "运行出错，请检查设备连接"
    if text.startswith("List of devices attached"):
        return "正在读取 ADB 设备列表"
    return text


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
        self.configure(bg=BG)
        self.preview_image = None

        header = tk.Frame(self, bg=PANEL, height=54)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header,
            text="实时游戏页面预览",
            bg=PANEL,
            fg=TEXT,
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(side=tk.LEFT, padx=18)
        tk.Label(
            header,
            text="关闭此窗口不会停止循环",
            bg=PANEL,
            fg=MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.RIGHT, padx=18)

        body = tk.Frame(self, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)
        self.label = tk.Label(
            body,
            text="等待截图",
            bg="#111827",
            fg=MUTED,
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
        self.geometry("940x690")
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
        self.interval_text = tk.StringVar(value=f"{self.interval.get():.1f}s")
        self.device_text = tk.StringVar(value=self.cfg.get("device") or "未检测")
        self.connection_text = tk.StringVar(value="检测中")
        self.display_text = tk.StringVar(value=f"Display {self.cfg.get('display') or '自动'}")
        self.run_state_text = tk.StringVar(value="待命")
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
        shell.pack(fill=tk.BOTH, expand=True, padx=34, pady=16)

        panel = tk.Frame(shell, bg=PANEL, highlightbackground=BORDER, highlightthickness=2)
        panel.pack(fill=tk.BOTH, expand=True)

        self._build_header(panel)
        body = tk.Frame(panel, bg=PANEL)
        body.pack(fill=tk.BOTH, expand=True, padx=24, pady=18)

        self._build_status_cards(body)
        self._build_interval_card(body)
        self._build_log_card(body)
        self._build_actions(body)
        self._build_footer(body)

    def _build_header(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, bg=PANEL, height=92)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        icon = tk.Label(
            header,
            text="王",
            bg="#4b5563",
            fg=TEXT,
            width=3,
            height=1,
            font=("Microsoft YaHei UI", 22, "bold"),
            highlightbackground="#8a94a3",
            highlightthickness=2,
        )
        icon.pack(side=tk.LEFT, padx=(24, 14), pady=20)

        title_box = tk.Frame(header, bg=PANEL)
        title_box.pack(side=tk.LEFT, pady=20)
        title_row = tk.Frame(title_box, bg=PANEL)
        title_row.pack(anchor=tk.W)
        tk.Label(
            title_row,
            text="王者荣耀自动练级",
            bg=PANEL,
            fg=TEXT,
            font=("Microsoft YaHei UI", 19, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            title_row,
            text=f"v{APP_VERSION}",
            bg="#4b5563",
            fg="#cbd5e1",
            padx=10,
            pady=3,
            font=("Microsoft YaHei UI", 10, "bold"),
            highlightbackground="#798392",
            highlightthickness=1,
        ).pack(side=tk.LEFT, padx=(14, 0))
        tk.Label(
            title_box,
            text="自动检测 · 循环点击 · 对局等待",
            bg=PANEL,
            fg=MUTED,
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor=tk.W, pady=(6, 0))

        lights = tk.Frame(header, bg=PANEL)
        lights.pack(side=tk.RIGHT, padx=24, pady=28)
        for color in ("#ff5f56", "#ffbd2e", "#27c93f"):
            tk.Label(lights, text="●", bg=PANEL, fg=color, font=("Microsoft YaHei UI", 18)).pack(side=tk.LEFT, padx=4)

        tk.Frame(parent, bg="#556071", height=1).pack(fill=tk.X)

    def _build_status_cards(self, parent: tk.Frame) -> None:
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill=tk.X, pady=(6, 14))
        self._status_card(row, "设备状态", self.device_text, self.connection_text).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 12))
        self._status_card(row, "运行状态", self.display_text, self.run_state_text).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _status_card(self, parent: tk.Frame, title: str, value: tk.StringVar, badge: tk.StringVar) -> tk.Frame:
        card = tk.Frame(parent, bg=CARD, height=126, highlightbackground=BORDER, highlightthickness=2)
        card.pack_propagate(False)
        tk.Label(card, text=title, bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor=tk.W, padx=18, pady=(18, 8))
        tk.Label(card, textvariable=value, bg=CARD, fg=TEXT, font=("Consolas", 13)).pack(anchor=tk.W, padx=18)
        badge_label = tk.Label(
            card,
            textvariable=badge,
            bg=GREEN_DARK,
            fg="#a7f3d0",
            padx=14,
            pady=6,
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        badge_label.pack(anchor=tk.W, padx=18, pady=(18, 0))
        if badge is self.connection_text:
            self.connection_badge = badge_label
        else:
            self.run_badge = badge_label
        return card

    def _build_interval_card(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg=CARD, height=94, highlightbackground=BORDER, highlightthickness=2)
        card.pack(fill=tk.X, pady=(0, 14))
        card.pack_propagate(False)

        top = tk.Frame(card, bg=CARD)
        top.pack(fill=tk.X, padx=18, pady=(15, 8))
        tk.Label(top, text="识别间隔", bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 11, "bold")).pack(side=tk.LEFT)
        tk.Label(top, text="推荐 1.0-1.5 秒", bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 10)).pack(side=tk.RIGHT)

        line = tk.Frame(card, bg=CARD)
        line.pack(fill=tk.X, padx=18)
        tk.Label(line, text="0.5s", bg=CARD, fg=SUBTLE, font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT, padx=(0, 10))
        tk.Scale(
            line,
            from_=0.5,
            to=3.0,
            resolution=0.1,
            variable=self.interval,
            orient=tk.HORIZONTAL,
            showvalue=False,
            bg=CARD,
            troughcolor="#687280",
            activebackground=PRIMARY,
            highlightthickness=0,
            length=660,
            command=self._on_interval_change,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(line, text="3.0s", bg=CARD, fg=SUBTLE, font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT, padx=(10, 14))
        tk.Label(line, textvariable=self.interval_text, bg=CARD, fg=TEXT, width=5, font=("Consolas", 14, "bold")).pack(side=tk.RIGHT)

    def _build_log_card(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg=CARD, height=88, highlightbackground=BORDER, highlightthickness=2)
        card.pack(fill=tk.X, pady=(0, 14))
        card.pack_propagate(False)
        tk.Label(card, text="最新日志", bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor=tk.W, padx=18, pady=(13, 8))
        tk.Label(
            card,
            textvariable=self.log_text,
            bg=BLACK_BAR,
            fg="#9af28f",
            anchor=tk.W,
            padx=16,
            font=("Consolas", 12),
        ).pack(fill=tk.X, padx=18, ipady=10)

    def _build_actions(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg=CARD, height=150, highlightbackground=BORDER, highlightthickness=2)
        card.pack(fill=tk.X, pady=(0, 18))
        card.pack_propagate(False)

        row1 = tk.Frame(card, bg=CARD)
        row1.pack(fill=tk.X, padx=18, pady=(18, 8))
        self._button(row1, "刷新设备", self.refresh_devices, BUTTON, BUTTON_HOVER).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        self._button(row1, "自动检测", self.auto_config, BUTTON, BUTTON_HOVER).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        self._button(row1, "实时预览", self.open_preview, BUTTON, BUTTON_HOVER).pack(side=tk.LEFT, fill=tk.X, expand=True)

        row2 = tk.Frame(card, bg=CARD)
        row2.pack(fill=tk.X, padx=18, pady=(6, 0))
        self._button(row2, "开始循环", self.start_loop, PRIMARY, PRIMARY_DARK).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        self._button(row2, "停止", self.stop_loop, DANGER, DANGER_DARK).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _build_footer(self, parent: tk.Frame) -> None:
        tk.Frame(parent, bg="#606a78", height=1).pack(fill=tk.X, pady=(0, 14))
        footer = tk.Frame(parent, bg=PANEL)
        footer.pack(fill=tk.X)
        tk.Label(footer, text="主题调色", bg=PANEL, fg=MUTED, font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT)
        dots = tk.Frame(footer, bg=PANEL)
        dots.pack(side=tk.LEFT, padx=16)
        for color in ("#102449", "#111827", "#042f2e", "#4c0519", "#78350f", "#e5e7eb"):
            tk.Label(dots, text="●", bg=PANEL, fg=color, font=("Microsoft YaHei UI", 21)).pack(side=tk.LEFT, padx=5)
        tk.Label(footer, text="Designed by 小北", bg=PANEL, fg="#d1d5db", font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.RIGHT)

    def _button(self, parent: tk.Frame, text: str, command, bg: str, active_bg: str) -> tk.Button:
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=TEXT,
            activebackground=active_bg,
            activeforeground=TEXT,
            relief=tk.FLAT,
            bd=0,
            height=2,
            cursor="hand2",
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        btn.bind("<Enter>", lambda _e: btn.configure(bg=active_bg))
        btn.bind("<Leave>", lambda _e: btn.configure(bg=bg))
        return btn

    def _on_interval_change(self, value: str) -> None:
        self.interval_text.set(f"{float(value):.1f}s")

    def _set_connection(self, text: str, ok: bool) -> None:
        self.connection_text.set(text)
        if hasattr(self, "connection_badge"):
            self.connection_badge.configure(bg=GREEN_DARK if ok else "#6b1f2a", fg="#a7f3d0" if ok else "#fecaca")

    def _set_running(self, text: str, running: bool) -> None:
        self.run_state_text.set(text)
        if hasattr(self, "run_badge"):
            self.run_badge.configure(bg=GREEN_DARK if running else "#4b5563", fg="#a7f3d0" if running else "#d1d5db")

    def open_preview(self) -> None:
        if self.preview_window and self.preview_window.winfo_exists():
            self.preview_window.lift()
            return
        self.preview_window = PreviewWindow(self)
        self.preview_stop_event.clear()
        self._start_preview_worker()
        if self.latest_image is not None:
            self.preview_window.show_image(self.latest_image)

    def close_preview(self) -> None:
        self.preview_stop_event.set()
        if self.preview_window and self.preview_window.winfo_exists():
            self.preview_window.destroy()
        self.preview_window = None

    def _start_preview_worker(self) -> None:
        if self.preview_worker and self.preview_worker.is_alive():
            return

        def run():
            while not self.preview_stop_event.is_set():
                try:
                    image = self.adb.screencap()
                    self.events.put(("preview", image))
                except Exception as exc:  # noqa: BLE001
                    self.events.put(("log", f"[preview error] {exc}"))
                    time.sleep(2.0)
                time.sleep(max(0.6, float(self.interval.get())))

        self.preview_worker = threading.Thread(target=run, daemon=True)
        self.preview_worker.start()

    def _drain_events(self) -> None:
        while True:
            try:
                typ, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if typ == "status":
                text = str(payload)
                running = "运行" in text
                self._set_running("运行中" if running else text, running)
            elif typ == "device":
                self.device_text.set(str(payload))
            elif typ == "connection":
                ok, text = payload
                self._set_connection(str(text), bool(ok))
            elif typ == "display":
                self.display_text.set(f"Display {payload}")
            elif typ == "log":
                friendly = friendly_log_text(str(payload))
                self.log_text.set(f"{time.strftime('%H:%M:%S')}  {friendly}")
            elif typ == "preview":
                self.latest_image = payload
                if self.preview_window and self.preview_window.winfo_exists():
                    self.preview_window.show_image(payload)
        self.after(120, self._drain_events)

    def _run_bg(self, name: str, target) -> None:
        def wrapped():
            self.events.put(("status", f"{name}中"))
            try:
                target()
            except Exception as exc:  # noqa: BLE001
                self.events.put(("log", f"[error] {exc}"))
                self.events.put(("status", "出错"))
                self.events.put(("connection", (False, "未连接")))
            else:
                if not (self.worker and self.worker.is_alive()):
                    self.events.put(("status", "待命"))

        threading.Thread(target=wrapped, daemon=True).start()

    def refresh_devices(self) -> None:
        def task():
            self.reload_config()
            with LogCapture(self.log_line):
                devices = self.adb.devices()
            connected = [line.split()[0] for line in devices.splitlines() if "\tdevice" in line]
            if connected:
                device = self.cfg.get("device") or connected[0]
                if device not in connected:
                    device = connected[0]
                self.events.put(("device", device))
                self.events.put(("connection", (True, "已连接")))
            else:
                self.events.put(("device", "未检测"))
                self.events.put(("connection", (False, "未连接")))
                self.events.put(("log", "[error] no connected ADB device found"))

        self._run_bg("刷新设备", task)

    def auto_config(self) -> None:
        def task():
            with LogCapture(self.log_line):
                print("[auto] start detect emulator and display")
                cfg = wz_auto.load_config(CONFIG_PATH)
                adb_path = wz_auto.resolve_adb_path(cfg.get("adb_path"))
                client = wz_auto.AdbClient(adb_path, cfg.get("device") or "127.0.0.1:5555", str(cfg.get("display") or "2"))
                devices_text = client.devices()
                devices = [line.split()[0] for line in devices_text.splitlines() if "\tdevice" in line]
                if not devices:
                    ports = wz_auto.scan_local_adb_ports()
                    for port in ports:
                        candidate = f"127.0.0.1:{port}"
                        try:
                            wz_auto.run_adb(adb_path, ["connect", candidate])
                        except Exception:
                            pass
                    devices_text = client.devices()
                    devices = [line.split()[0] for line in devices_text.splitlines() if "\tdevice" in line]
                if not devices:
                    raise RuntimeError("no connected ADB device found")
                device = devices[0]
                client = wz_auto.AdbClient(adb_path, device, str(cfg.get("display") or "2"))
                display, score = wz_auto.detect_display(client)
                cfg["adb_path"] = str(adb_path)
                cfg["device"] = device
                cfg["display"] = str(display)
                wz_auto.save_config(CONFIG_PATH, cfg)
                print(f"[auto] adb_path={adb_path}")
                print(f"[auto] device={device}")
                print(f"[auto] display={display} score={score:.3f}")
            self.reload_config()
            self.events.put(("device", self.cfg.get("device") or "未检测"))
            self.events.put(("connection", (True, "已连接")))
            self.events.put(("display", self.cfg.get("display") or "自动"))

        self._run_bg("自动检测", task)

    def start_loop(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("提示", "循环已经在运行")
            return

        self.cfg["interval"] = float(self.interval.get())
        wz_auto.save_config(CONFIG_PATH, self.cfg)
        self.reload_config()
        self.stop_event.clear()

        def run():
            self.events.put(("status", "循环运行中"))
            try:
                with LogCapture(self.log_line):
                    wz_auto.loop(self.adb, self.cfg, self.stop_event)
            except Exception as exc:  # noqa: BLE001
                self.events.put(("log", f"[error] {exc}"))
                self.events.put(("status", "出错"))
            finally:
                self.events.put(("status", "待命"))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def stop_loop(self) -> None:
        self.stop_event.set()
        self.log_line("[stop] stop requested")
        self.events.put(("status", "待命"))

    def log_line(self, text: str) -> None:
        self.events.put(("log", text))


if __name__ == "__main__":
    AutoDesktopApp().mainloop()
