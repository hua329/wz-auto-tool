from __future__ import annotations

import contextlib
import io
import json
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
THEME_PATH = APP_DIR / "theme.json"
APP_VERSION = "1.0.1"
FONT = "Microsoft YaHei UI"
MONO = "Consolas"

PRESETS = [
    {"name": "深海", "bg": "#172b4b", "panel": "#28436d", "c1": "#2468f2", "c2": "#8b42ff", "c3": "#39d66b", "s1": 40, "s2": 30, "s3": 22},
    {"name": "夜幕", "bg": "#111420", "panel": "#252c43", "c1": "#6d5dfc", "c2": "#cc4d86", "c3": "#4dc7ff", "s1": 36, "s2": 28, "s3": 20},
    {"name": "极光", "bg": "#031f1a", "panel": "#173d3a", "c1": "#19e38b", "c2": "#23bdff", "c3": "#9bf7bd", "s1": 40, "s2": 32, "s3": 18},
    {"name": "玫瑰", "bg": "#23101b", "panel": "#4a2236", "c1": "#f05287", "c2": "#b84cff", "c3": "#ff846e", "s1": 42, "s2": 30, "s3": 18},
    {"name": "琥珀", "bg": "#241500", "panel": "#4b3211", "c1": "#e69216", "c2": "#df5b18", "c3": "#ffc857", "s1": 40, "s2": 28, "s3": 22},
    {"name": "霜白", "bg": "#1d232c", "panel": "#374151", "c1": "#c7d7ef", "c2": "#9fb4db", "c3": "#edf4ff", "s1": 30, "s2": 22, "s3": 16},
]

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


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def blend(fg: str, bg: str, alpha: float) -> str:
    fr, fg_, fb = hex_to_rgb(fg)
    br, bg_, bb = hex_to_rgb(bg)
    return rgb_to_hex((
        int(fr * alpha + br * (1 - alpha)),
        int(fg_ * alpha + bg_ * (1 - alpha)),
        int(fb * alpha + bb * (1 - alpha)),
    ))


def load_theme() -> dict:
    if THEME_PATH.exists():
        try:
            data = json.loads(THEME_PATH.read_text(encoding="utf-8"))
            if {"bg", "panel", "c1", "c2", "c3", "s1", "s2", "s3"}.issubset(data):
                return data
        except Exception:
            pass
    return PRESETS[0].copy()


def save_theme(theme: dict) -> None:
    try:
        THEME_PATH.write_text(json.dumps(theme, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def friendly_log_text(text: str) -> str:
    match = re.search(r"\[(?:tap|state|dry-run)\]\s+([A-Za-z0-9_]+)", text)
    if not match:
        match = re.search(r"\[followup tap\]\s+([A-Za-z0-9_]+)", text)
    if match:
        return FRIENDLY_STATES.get(match.group(1), "识别到页面，继续执行")
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
    if text.startswith("[device]"):
        return "设备刷新完成"
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
        self.configure(bg="#0f172a")
        self.preview_image = None

        header = tk.Frame(self, bg="#111827", height=48)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="实时游戏页面预览", bg="#111827", fg="#f8fafc", font=(FONT, 13, "bold")).pack(
            side=tk.LEFT, padx=18, pady=12
        )
        tk.Label(header, text="关闭窗口不会停止循环", bg="#111827", fg="#94a3b8", font=(FONT, 9)).pack(
            side=tk.RIGHT, padx=18
        )

        body = tk.Frame(self, bg="#0f172a")
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)
        self.label = tk.Label(
            body,
            text="等待截图",
            bg="#1e293b",
            fg="#64748b",
            font=(FONT, 12),
            anchor=tk.CENTER,
            bd=0,
            highlightbackground="#334155",
            highlightthickness=1,
        )
        self.label.pack(fill=tk.BOTH, expand=True)
        self.protocol("WM_DELETE_WINDOW", master.close_preview)

    def show_image(self, img) -> None:
        width = max(680, self.label.winfo_width() - 18)
        height = max(400, self.label.winfo_height() - 18)
        preview = img.copy()
        preview.thumbnail((width, height))
        self.preview_image = ImageTk.PhotoImage(preview)
        self.label.configure(image=self.preview_image, text="")


class AutoDesktopApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"王者荣耀自动练级工具 v{APP_VERSION}")
        self.geometry("1060x820")
        self.resizable(False, False)

        self.theme = load_theme()
        self.cfg = wz_auto.load_config(CONFIG_PATH)
        self.adb = self._new_adb()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.preview_window: PreviewWindow | None = None
        self.preview_worker: threading.Thread | None = None
        self.preview_stop_event = threading.Event()
        self.latest_image = None
        self.loop_count = 0
        self.device_value = self.cfg.get("device") or "未检测"
        self.connected = False
        self.running = False
        self.latest_log = "等待开始循环..."
        self.latest_time = "--:--:--"
        self.embedded_widgets: list[tk.Widget] = []

        self.interval = tk.DoubleVar(value=float(self.cfg.get("interval", 1.2)))
        self.s1 = tk.IntVar(value=int(self.theme.get("s1", 40)))
        self.s2 = tk.IntVar(value=int(self.theme.get("s2", 30)))
        self.s3 = tk.IntVar(value=int(self.theme.get("s3", 22)))

        self.canvas = tk.Canvas(self, width=1060, height=820, bd=0, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self._render()
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

    def _panel(self) -> str:
        return self.theme["panel"]

    def _glass(self, alpha: float = 0.18) -> str:
        return blend("#ffffff", self._panel(), alpha)

    def _line(self, alpha: float = 0.28) -> str:
        return blend("#ffffff", self._panel(), alpha)

    def _muted(self, alpha: float = 0.56) -> str:
        return blend("#ffffff", self._panel(), alpha)

    def _round_rect(self, x1, y1, x2, y2, radius, **kwargs):
        points = [
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1,
        ]
        return self.canvas.create_polygon(points, smooth=True, **kwargs)

    def _draw_blobs(self):
        bg = self.theme["bg"]
        colors = [
            blend(self.theme["c1"], bg, self.s1.get() / 100 * 0.55),
            blend(self.theme["c2"], bg, self.s2.get() / 100 * 0.48),
            blend(self.theme["c3"], bg, self.s3.get() / 100 * 0.42),
        ]
        self.canvas.create_oval(-130, -100, 460, 360, fill=colors[0], outline="")
        self.canvas.create_oval(600, -120, 1210, 360, fill=colors[1], outline="")
        self.canvas.create_oval(250, 430, 1110, 980, fill=colors[2], outline="")

    def _text(self, x, y, text, *, size=13, fill="#ffffff", weight="normal", anchor="nw", font=None):
        return self.canvas.create_text(x, y, text=text, fill=fill, anchor=anchor, font=font or (FONT, size, weight))

    def _pill(self, x1, y1, x2, y2, text, fill, outline, fg, dot=None):
        self._round_rect(x1, y1, x2, y2, 18, fill=fill, outline=outline, width=2)
        if dot:
            self.canvas.create_oval(x1 + 14, y1 + 13, x1 + 24, y1 + 23, fill=dot, outline="")
            self._text(x1 + 32, y1 + 8, text, size=13, fill=fg, weight="bold")
        else:
            self._text((x1 + x2) / 2, y1 + 8, text, size=12, fill=fg, weight="bold", anchor="n")

    def _button(self, x1, y1, x2, y2, text, command, tag, icon=""):
        fill = self._glass(0.13)
        hover = self._glass(0.22)
        outline = self._line(0.35)
        self._round_rect(x1, y1, x2, y2, 12, fill=fill, outline=outline, width=1, tags=(tag, f"{tag}_shape"))
        self._text((x1 + x2) / 2, y1 + 15, f"{icon} {text}".strip(), size=16, fill="#ffffff", weight="bold", anchor="n", font=(FONT, 16, "bold"))
        self.canvas.addtag_withtag(tag, f"{tag}_shape")
        self.canvas.tag_bind(tag, "<Button-1>", lambda _event: command())
        self.canvas.tag_bind(tag, "<Enter>", lambda _event: self.canvas.itemconfigure(f"{tag}_shape", fill=hover))
        self.canvas.tag_bind(tag, "<Leave>", lambda _event: self.canvas.itemconfigure(f"{tag}_shape", fill=fill))

    def _embedded_scale(self, x, y, width, variable, from_, to, command, trough):
        scale = tk.Scale(
            self.canvas,
            from_=from_,
            to=to,
            resolution=0.1 if isinstance(variable, tk.DoubleVar) else 1,
            orient=tk.HORIZONTAL,
            variable=variable,
            showvalue=False,
            length=width,
            bg=self._glass(0.05),
            troughcolor=trough,
            activebackground="#f8fafc",
            highlightthickness=0,
            bd=0,
            sliderrelief=tk.FLAT,
            command=command,
        )
        self.embedded_widgets.append(scale)
        self.canvas.create_window(x, y, window=scale, anchor="nw", width=width, height=38)
        return scale

    def _render(self):
        for widget in self.embedded_widgets:
            widget.destroy()
        self.embedded_widgets.clear()
        self.canvas.delete("all")
        self.configure(bg=self.theme["bg"])
        self.canvas.configure(bg=self.theme["bg"])

        self._draw_blobs()
        panel = self._panel()

        self._round_rect(18, 10, 1042, 790, 34, fill=panel, outline=self._line(0.42), width=2)
        self.canvas.create_line(18, 92, 1042, 92, fill=self._line(0.22), width=1)

        self._round_rect(42, 30, 84, 72, 12, fill=self._glass(0.22), outline=self._line(0.50), width=2)
        self._text(63, 40, "王", size=17, fill="#ffffff", weight="bold", anchor="n")
        self._text(100, 42, "王者荣耀自动练级", size=20, fill="#ffffff", weight="bold")
        self._pill(278, 36, 352, 66, f"v{APP_VERSION}", self._glass(0.15), self._line(0.35), self._muted(0.86))
        for i, color in enumerate(("#ff5f57", "#febc2e", "#28c840")):
            self.canvas.create_oval(950 + i * 24, 45, 966 + i * 24, 61, fill=color, outline="")

        self._status_cards()
        self._interval_card()
        self._log_card()
        self._buttons_card()
        self._theme_card()
        self._text(42, 770, "Designed by 小北", size=11, fill=self._muted(0.65), weight="bold")
        self.loop_text = self._text(930, 770, f"循环次数：{self.loop_count}", size=11, fill=self._muted(0.65), anchor="nw", font=(MONO, 11))

    def _card(self, x1, y1, x2, y2, r=22):
        self._round_rect(x1 + 3, y1 + 5, x2 + 3, y2 + 5, r, fill=blend("#000000", self._panel(), 0.12), outline="")
        self._round_rect(x1, y1, x2, y2, r, fill=self._glass(0.16), outline=self._line(0.36), width=2)

    def _status_cards(self):
        self._card(42, 120, 500, 260)
        self._card(520, 120, 1018, 260)
        label = self._muted(0.60)
        value = blend("#ffffff", self._panel(), 0.92)
        self._text(64, 142, "设备状态", size=13, fill=label, weight="bold")
        self.device_item = self._text(64, 174, self.device_value, size=16, fill=value, font=(MONO, 16))
        if self.connected:
            self._pill(64, 214, 160, 250, "已连接", blend("#2bd971", self._panel(), 0.34), blend("#2bd971", self._panel(), 0.65), "#98ffc4", dot="#4bf082")
        else:
            self._pill(64, 214, 170, 250, "未连接", blend("#ff4b6a", self._panel(), 0.25), blend("#ff4b6a", self._panel(), 0.50), "#ffc1cd", dot="#ff6b82")

        self._text(542, 142, "运行状态", size=13, fill=label, weight="bold")
        self._text(542, 174, "识别循环", size=15, fill=self._muted(0.54), weight="bold")
        if self.running:
            self._pill(542, 214, 640, 250, "运行中", blend("#98e840", self._panel(), 0.28), blend("#98e840", self._panel(), 0.58), "#c9ff89", dot="#99f34d")
        else:
            self._pill(542, 214, 640, 250, "待机中", blend("#4a90ff", self._panel(), 0.28), blend("#4a90ff", self._panel(), 0.58), "#9cc8ff", dot="#65adff")

    def _interval_card(self):
        self._card(42, 278, 1018, 364)
        self._text(64, 300, "识别间隔", size=13, fill=self._muted(0.60), weight="bold")
        self._text(64, 329, "0.5s", size=12, fill=self._muted(0.45), font=(MONO, 12))
        self._text(880, 329, "3.0s", size=12, fill=self._muted(0.45), font=(MONO, 12))
        self.interval_value_item = self._text(952, 324, f"{self.interval.get():.1f}s", size=16, fill="#ffffff", weight="bold", font=(MONO, 16, "bold"))
        self._embedded_scale(126, 319, 730, self.interval, 0.5, 3.0, self._on_interval_change, self._line(0.18))

    def _log_card(self):
        self._card(42, 382, 1018, 492)
        self._text(64, 404, "最新日志", size=13, fill=self._muted(0.60), weight="bold")
        self._round_rect(64, 436, 996, 474, 13, fill=blend("#000000", self._panel(), 0.25), outline="")
        self.log_time_item = self._text(88, 446, self.latest_time, size=13, fill=self._muted(0.44), font=(MONO, 13))
        self.log_item = self._text(188, 446, self.latest_log, size=14, fill="#a6ff91", font=(MONO, 14))

    def _buttons_card(self):
        self._card(42, 510, 1018, 660)
        self._button(64, 532, 350, 586, "刷新设备", self.refresh_devices, "btn_refresh", "↻")
        self._button(370, 532, 656, 586, "自动检测", self.auto_config, "btn_auto", "⌗")
        self._button(676, 532, 996, 586, "实时预览", self.open_preview, "btn_preview", "▣")
        self._button(64, 598, 500, 642, "开始循环", self.start_loop, "btn_start", "▷")
        self._button(520, 598, 996, 642, "停止", self.stop_loop, "btn_stop", "□")

    def _theme_card(self):
        self.canvas.create_line(42, 682, 1018, 682, fill=self._line(0.16), width=1)
        self._text(42, 704, "主题调色", size=12, fill=self._muted(0.60), weight="bold")
        for i, preset in enumerate(PRESETS):
            x = 42 + i * 52
            outline = "#ffffff" if self.theme.get("name") == preset["name"] else blend("#000000", self._panel(), 0.32)
            self.canvas.create_oval(x, 734, x + 38, 772, fill=blend(preset["c1"], preset["bg"], 0.60), outline=outline, width=3, tags=(f"preset_{i}",))
            self.canvas.tag_bind(f"preset_{i}", "<Button-1>", lambda _e, p=preset: self._set_preset(p))
        self._theme_slider(380, 704, "背景色 A", self.theme["c1"], self.s1, self._on_theme_slider)
        self._theme_slider(380, 746, "背景色 B", self.theme["c2"], self.s2, self._on_theme_slider)

    def _theme_slider(self, x, y, label, color, variable, command):
        self._text(x, y + 8, label, size=12, fill=self._muted(0.60), weight="bold")
        self._round_rect(x + 104, y, 936, y + 34, 7, fill=blend("#000000", self._panel(), 0.28), outline="")
        self._embedded_scale(x + 122, y + 1, 700, variable, 0, 100, command, color)
        self._text(954, y + 8, f"{variable.get()}%", size=12, fill=self._muted(0.70), font=(MONO, 12))

    def _on_interval_change(self, value: str):
        self.canvas.itemconfigure(self.interval_value_item, text=f"{float(value):.1f}s")

    def _on_theme_slider(self, _value: str):
        self.theme["s1"] = int(self.s1.get())
        self.theme["s2"] = int(self.s2.get())
        save_theme(self.theme)

    def _set_preset(self, preset: dict):
        self.theme = dict(preset)
        self.s1.set(int(self.theme["s1"]))
        self.s2.set(int(self.theme["s2"]))
        self.s3.set(int(self.theme["s3"]))
        save_theme(self.theme)
        self._render()

    def _update_dynamic(self):
        self.canvas.itemconfigure(self.device_item, text=self.device_value)
        self.canvas.itemconfigure(self.log_time_item, text=self.latest_time)
        self.canvas.itemconfigure(self.log_item, text=self.latest_log)
        self.canvas.itemconfigure(self.loop_text, text=f"循环次数：{self.loop_count}")

    def log_line(self, text: str) -> None:
        self.events.put(("log", text))

    def set_status(self, text: str) -> None:
        self.events.put(("status", text))

    def _drain_events(self) -> None:
        rerender = False
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.latest_time = time.strftime("%H:%M:%S")
                self.latest_log = friendly_log_text(str(payload))
            elif kind == "status":
                new_running = str(payload) == "循环运行中"
                if new_running != self.running:
                    self.running = new_running
                    rerender = True
            elif kind == "device":
                value = str(payload)
                self.connected = value.endswith(" 已连接")
                self.device_value = value.replace(" 已连接", "")
                rerender = True
            elif kind == "preview":
                self.latest_image = payload
                if self.preview_window and self.preview_window.winfo_exists():
                    self.preview_window.show_image(payload)
            elif kind == "loop_count":
                self.loop_count += 1
        if rerender:
            self._render()
        else:
            self._update_dynamic()
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
            except Exception as exc:  # noqa: BLE001
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
                self.log_line(f"[auto] device={device}")
            self.events.put(("device", f"{device} 已连接" if device in devices else "未确认连接"))
            self.log_line("[device] refresh done")

        self._run_bg("刷新设备中", task)

    def auto_config(self) -> None:
        def task():
            self.reload_config()
            result = wz_auto.auto_detect_config(self.cfg)
            wz_auto.update_config_file(
                CONFIG_PATH,
                {"adb_path": result["adb_path"], "device": result["device"], "display": result["display"]},
            )
            self.reload_config()
            self.events.put(("preview", result["image"]))
            self.events.put(("device", f"{result['device']} 已连接"))
            self.log_line(f"[auto] display={result['display']} score={result['display_score']:.3f}")

        self._run_bg("自动检测中", task)

    def start_loop(self) -> None:
        def task():
            self.reload_config()
            self.cfg["interval"] = float(self.interval.get())
            wz_auto.update_config_file(CONFIG_PATH, {"interval": float(self.interval.get())})
            try:
                result = wz_auto.auto_detect_config(self.cfg)
                wz_auto.update_config_file(
                    CONFIG_PATH,
                    {"adb_path": result["adb_path"], "device": result["device"], "display": result["display"]},
                )
                self.reload_config()
                self.events.put(("preview", result["image"]))
                self.events.put(("device", f"{result['device']} 已连接"))
                self.log_line(f"[auto] device={result['device']} display={result['display']}")
            except Exception as exc:  # noqa: BLE001
                self.log_line(f"[auto warning] {exc}")
            self.log_line("[loop] start enable_clicks=True")
            while not self.stop_event.is_set():
                img = self.adb.capture()
                self.events.put(("preview", img))
                match_result = wz_auto.find_state(img, self.cfg.get("states", []), verbose=False)
                if match_result:
                    with LogCapture(self.log_line):
                        wz_auto.perform_action(self.adb, img, match_result, dry_run=False)
                    self.events.put(("loop_count", None))
                    time.sleep(match_result.delay_after)
                else:
                    self.log_line("[wait] no configured state matched")
                    time.sleep(max(0.3, float(self.interval.get())))
            self.log_line("[loop] stopped")

        self._run_bg("循环运行中", task)

    def stop_loop(self) -> None:
        self.stop_event.set()
        self.log_line("[stop] stop requested")

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
        if self.preview_window and self.preview_window.winfo_exists():
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
                except Exception as exc:  # noqa: BLE001
                    self.log_line(f"[preview error] {exc}")
                time.sleep(max(0.5, float(self.interval.get())))

        self.preview_worker = threading.Thread(target=worker, daemon=True)
        self.preview_worker.start()


if __name__ == "__main__":
    AutoDesktopApp().mainloop()
