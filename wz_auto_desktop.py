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
    {"name": "默认", "bg": "#0f1923", "c1": "#1a56db", "c2": "#7c3aed", "c3": "#0e7490", "s1": 40, "s2": 30, "s3": 20},
    {"name": "夜幕", "bg": "#0d0d14", "c1": "#6450dc", "c2": "#c83c78", "c3": "#3cb4dc", "s1": 35, "s2": 28, "s3": 22},
    {"name": "极光", "bg": "#021a10", "c1": "#14dc8c", "c2": "#14b4dc", "c3": "#50f0a0", "s1": 38, "s2": 32, "s3": 18},
    {"name": "玫瑰", "bg": "#1a0812", "c1": "#dc3c78", "c2": "#b428c8", "c3": "#f06450", "s1": 42, "s2": 30, "s3": 20},
    {"name": "琥珀", "bg": "#1a1000", "c1": "#dc8c14", "c2": "#c85014", "c3": "#f0b428", "s1": 40, "s2": 28, "s3": 22},
    {"name": "霜白", "bg": "#1a1e24", "c1": "#b4c8dc", "c2": "#8ca0c8", "c3": "#c8d2e6", "s1": 30, "s2": 22, "s3": 16},
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


def alpha_blend(fg_hex: str, bg_hex: str, alpha: float) -> str:
    fr, fg, fb = hex_to_rgb(fg_hex)
    br, bg, bb = hex_to_rgb(bg_hex)
    r = int(fr * alpha + br * (1 - alpha))
    g = int(fg * alpha + bg * (1 - alpha))
    b = int(fb * alpha + bb * (1 - alpha))
    return f"#{r:02x}{g:02x}{b:02x}"


def glass_color(bg: str, alpha: float = 0.13) -> str:
    return alpha_blend("#ffffff", bg, alpha)


def glass_border(bg: str, alpha: float = 0.28) -> str:
    return alpha_blend("#ffffff", bg, alpha)


def load_theme() -> dict:
    if THEME_PATH.exists():
        try:
            data = json.loads(THEME_PATH.read_text(encoding="utf-8"))
            if {"bg", "c1", "c2", "c3", "s1", "s2", "s3"}.issubset(data):
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


def dark_btn(parent, text, command, bg, fg, hover, width=10):
    btn = tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=hover,
        activeforeground=fg,
        relief=tk.FLAT,
        bd=0,
        width=width,
        height=2,
        cursor="hand2",
        font=(FONT, 10, "bold"),
        highlightthickness=1,
        highlightbackground=glass_border(bg, 0.22),
    )
    btn.bind("<Enter>", lambda _e: btn.configure(bg=hover))
    btn.bind("<Leave>", lambda _e: btn.configure(bg=bg))
    return btn


class AutoDesktopApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"王者荣耀自动练级工具 v{APP_VERSION}")
        self.geometry("540x660")
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

        self.interval = tk.DoubleVar(value=float(self.cfg.get("interval", 1.2)))
        self.device_text = tk.StringVar(value=self.cfg.get("device") or "未检测")
        self.log_text = tk.StringVar(value="等待操作")

        self._apply_theme(self.theme, rebuild=False)
        self._build_ui()
        self.after(120, self._drain_events)
        self.after(300, self.refresh_devices)

    def _apply_theme(self, theme: dict, rebuild=True) -> None:
        self.theme = theme
        bg = theme["bg"]
        self.configure(bg=bg)
        self._bg = bg
        self._gc = glass_color(bg, 0.11)
        self._gb = glass_border(bg, 0.26)
        self._gc_btn = glass_color(bg, 0.18)
        if rebuild:
            self._rebuild()

    def _rebuild(self) -> None:
        for widget in self.winfo_children():
            widget.destroy()
        self._build_ui()

    def reload_config(self) -> None:
        self.cfg = wz_auto.load_config(CONFIG_PATH)
        self.adb = self._new_adb()

    def _new_adb(self) -> wz_auto.AdbClient:
        adb_path = wz_auto.resolve_adb_path(self.cfg.get("adb_path"))
        device = self.cfg.get("device") or "127.0.0.1:5555"
        display = str(self.cfg.get("display") or "2")
        return wz_auto.AdbClient(adb_path, device, display)

    def _build_ui(self) -> None:
        bg = self._bg
        top_bg = glass_color(bg, 0.06)

        title_bar = tk.Frame(self, bg=top_bg, height=46)
        title_bar.pack(fill=tk.X)
        title_bar.pack_propagate(False)
        tk.Frame(title_bar, bg=glass_border(bg, 0.14), height=1).pack(fill=tk.X, side=tk.BOTTOM)

        title_left = tk.Frame(title_bar, bg=top_bg)
        title_left.pack(side=tk.LEFT, padx=14, pady=8)
        logo = tk.Frame(title_left, bg=glass_color(bg, 0.22), width=26, height=26)
        logo.pack(side=tk.LEFT)
        logo.pack_propagate(False)
        tk.Label(logo, text="王", bg=glass_color(bg, 0.22), fg="#ffffff", font=(FONT, 12, "bold")).pack(expand=True)
        tk.Label(title_left, text="  王者荣耀自动练级", bg=top_bg, fg="#ffffff", font=(FONT, 12, "bold")).pack(
            side=tk.LEFT
        )
        version = tk.Frame(title_left, bg=glass_color(bg, 0.12), highlightbackground=glass_border(bg, 0.20), highlightthickness=1)
        version.pack(side=tk.LEFT, padx=(8, 0))
        tk.Label(version, text=f"v{APP_VERSION}", bg=glass_color(bg, 0.12), fg=glass_border(bg, 0.60), font=(FONT, 9)).pack(
            padx=6, pady=2
        )

        dots = tk.Frame(title_bar, bg=top_bg)
        dots.pack(side=tk.RIGHT, padx=14)
        for color in ("#ff5f57", "#febc2e", "#28c840"):
            tk.Label(dots, bg=color, width=2, font=(FONT, 6)).pack(side=tk.LEFT, padx=2)

        outer = tk.Frame(self, bg=bg)
        outer.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(outer, bg=bg, bd=0, highlightthickness=0)
        scrollbar = tk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        body = tk.Frame(canvas, bg=bg)
        body_id = canvas.create_window((0, 0), window=body, anchor="nw")

        def resize_body(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(body_id, width=event.width)

        body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", resize_body)
        canvas.bind_all("<MouseWheel>", lambda event: canvas.yview_scroll(int(-1 * (event.delta / 120)), "units"))

        self._build_body(body)

        footer = tk.Frame(self, bg=top_bg, height=32)
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        footer.pack_propagate(False)
        tk.Frame(footer, bg=glass_border(bg, 0.12), height=1).pack(fill=tk.X, side=tk.TOP)
        tk.Label(footer, text="Designed by 小北", bg=top_bg, fg=glass_border(bg, 0.58), font=(FONT, 9, "bold")).pack(
            side=tk.LEFT, padx=14
        )
        self.loop_lbl = tk.Label(footer, text="循环次数：0", bg=top_bg, fg=glass_border(bg, 0.55), font=(MONO, 9))
        self.loop_lbl.pack(side=tk.RIGHT, padx=14)

    def _card(self, parent) -> tk.Frame:
        return tk.Frame(parent, bg=self._gc, highlightbackground=self._gb, highlightthickness=1)

    def _clabel(self, parent, text: str) -> None:
        tk.Label(parent, text=text, bg=self._gc, fg=glass_border(self._bg, 0.52), font=(FONT, 8, "bold")).pack(
            anchor=tk.W, padx=12, pady=(9, 3)
        )

    def _badge(self, parent, text: str, tint_hex: str) -> tk.Frame:
        tint_bg = alpha_blend(tint_hex, self._gc, 0.22)
        tint_fg = alpha_blend(tint_hex, "#ffffff", 0.75)
        tint_bd = alpha_blend(tint_hex, self._gc, 0.42)
        frame = tk.Frame(parent, bg=tint_bg, highlightbackground=tint_bd, highlightthickness=1)
        tk.Label(frame, text=text, bg=tint_bg, fg=tint_fg, font=(FONT, 9, "bold")).pack(padx=8, pady=3)
        return frame

    def _build_body(self, body: tk.Frame) -> None:
        bg = self._bg
        gc = self._gc
        gb = self._gb

        def row2():
            frame = tk.Frame(body, bg=bg)
            frame.pack(fill=tk.X, padx=12, pady=(12, 0))
            frame.columnconfigure(0, weight=1)
            frame.columnconfigure(1, weight=1)
            return frame

        row = row2()
        device_card = self._card(row)
        device_card.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self._clabel(device_card, "设备状态")
        device_row = tk.Frame(device_card, bg=gc)
        device_row.pack(fill=tk.X, padx=12, pady=(0, 10))
        tk.Label(device_row, textvariable=self.device_text, bg=gc, fg=glass_border(bg, 0.85), font=(MONO, 10)).pack(side=tk.LEFT)
        self.conn_badge = self._badge(device_row, "● 检测中", "#5090ff")
        self.conn_badge.pack(side=tk.RIGHT)

        run_card = self._card(row)
        run_card.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self._clabel(run_card, "运行状态")
        run_row = tk.Frame(run_card, bg=gc)
        run_row.pack(fill=tk.X, padx=12, pady=(0, 10))
        tk.Label(run_row, text="识别循环", bg=gc, fg=glass_border(bg, 0.45), font=(FONT, 10)).pack(side=tk.LEFT)
        self.run_badge = self._badge(run_row, "● 待机中", "#5090ff")
        self.run_badge.pack(side=tk.RIGHT)

        interval_card = self._card(body)
        interval_card.pack(fill=tk.X, padx=12, pady=(8, 0))
        self._clabel(interval_card, "识别间隔")
        interval_row = tk.Frame(interval_card, bg=gc)
        interval_row.pack(fill=tk.X, padx=12, pady=(0, 12))
        tk.Label(interval_row, text="0.5s", bg=gc, fg=glass_border(bg, 0.38), font=(MONO, 9)).pack(side=tk.LEFT)
        self.iv_scale = tk.Scale(
            interval_row,
            from_=0.5,
            to=3.0,
            resolution=0.1,
            orient=tk.HORIZONTAL,
            variable=self.interval,
            bg=gc,
            fg=glass_border(bg, 0.85),
            troughcolor=glass_color(bg, 0.20),
            highlightthickness=0,
            bd=0,
            sliderrelief=tk.FLAT,
            activebackground="#ffffff",
            showvalue=False,
            length=250,
        )
        self.iv_scale.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)
        tk.Label(interval_row, text="3.0s", bg=gc, fg=glass_border(bg, 0.38), font=(MONO, 9)).pack(side=tk.LEFT)
        self.iv_lbl = tk.Label(interval_row, text=f"{self.interval.get():.1f}s", bg=gc, fg=glass_border(bg, 0.90), font=(MONO, 12, "bold"), width=5)
        self.iv_lbl.pack(side=tk.LEFT, padx=(8, 0))
        self.interval.trace_add("write", lambda *_: self.iv_lbl.configure(text=f"{self.interval.get():.1f}s"))

        log_card = self._card(body)
        log_card.pack(fill=tk.X, padx=12, pady=(8, 0))
        self._clabel(log_card, "最新日志")
        log_bg = alpha_blend("#000000", bg, 0.25)
        log_box = tk.Frame(log_card, bg=log_bg, highlightbackground=glass_border(bg, 0.12), highlightthickness=1)
        log_box.pack(fill=tk.X, padx=12, pady=(0, 10))
        self.log_time_lbl = tk.Label(log_box, text="--:--:--", bg=log_bg, fg=glass_border(bg, 0.38), font=(MONO, 10))
        self.log_time_lbl.pack(side=tk.LEFT, padx=(10, 0), pady=7)
        tk.Label(log_box, textvariable=self.log_text, bg=log_bg, fg=alpha_blend("#b4ff8c", "#ffffff", 0.85), font=(MONO, 10), anchor=tk.W).pack(
            side=tk.LEFT, padx=(8, 10), fill=tk.X, expand=True
        )

        button_card = self._card(body)
        button_card.pack(fill=tk.X, padx=12, pady=(8, 0))
        button_inner = tk.Frame(button_card, bg=gc)
        button_inner.pack(fill=tk.X, padx=10, pady=10)

        row3 = tk.Frame(button_inner, bg=gc)
        row3.pack(fill=tk.X, pady=(0, 6))
        row3.columnconfigure((0, 1, 2), weight=1)
        ghost_bg = glass_color(bg, 0.14)
        ghost_hover = glass_color(bg, 0.22)
        dark_btn(row3, "刷新设备", self.refresh_devices, ghost_bg, glass_border(bg, 0.75), ghost_hover, 10).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        dark_btn(row3, "自动检测", self.auto_config, alpha_blend("#3c8cff", bg, 0.32), glass_border("#a0c8ff", 0.90), alpha_blend("#3c8cff", bg, 0.44), 10).grid(row=0, column=1, sticky="ew", padx=2)
        dark_btn(row3, "实时预览", self.open_preview, ghost_bg, glass_border(bg, 0.75), ghost_hover, 10).grid(row=0, column=2, sticky="ew", padx=(4, 0))

        row_buttons = tk.Frame(button_inner, bg=gc)
        row_buttons.pack(fill=tk.X)
        row_buttons.columnconfigure((0, 1), weight=1)
        self.start_btn = dark_btn(row_buttons, "▷  开始循环", self.start_loop, alpha_blend("#28b464", bg, 0.38), alpha_blend("#b4ffd0", "#ffffff", 0.95), alpha_blend("#28b464", bg, 0.50), 14)
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        dark_btn(row_buttons, "□  停止", self.stop_loop, alpha_blend("#dc3c3c", bg, 0.26), alpha_blend("#ffb4b4", "#ffffff", 0.90), alpha_blend("#dc3c3c", bg, 0.38), 14).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        separator = tk.Frame(body, bg=glass_border(bg, 0.12), height=1)
        separator.pack(fill=tk.X, padx=12, pady=(10, 0))

        theme_card = self._card(body)
        theme_card.pack(fill=tk.X, padx=12, pady=(0, 12))
        self._clabel(theme_card, "主题调色")
        swatch_row = tk.Frame(theme_card, bg=gc)
        swatch_row.pack(fill=tk.X, padx=12, pady=(0, 8))
        self._preset_btns = []
        for preset in PRESETS:
            swatch_bg = alpha_blend(preset["c1"], preset["bg"], 0.55)
            border = "#ffffff" if self.theme.get("name") == preset["name"] else glass_border(bg, 0.22)
            btn = tk.Label(swatch_row, text=preset["name"], bg=swatch_bg, fg="#ffffff", font=(FONT, 9, "bold"), padx=8, pady=4, cursor="hand2", highlightbackground=border, highlightthickness=2)
            btn.pack(side=tk.LEFT, padx=(0, 5))
            btn.bind("<Button-1>", lambda _e, p=preset, b=btn: self._pick_preset(p, b))
            self._preset_btns.append((preset["name"], btn))

    def _pick_preset(self, preset: dict, clicked_btn: tk.Label) -> None:
        for _name, btn in self._preset_btns:
            btn.configure(highlightbackground=glass_border(self._bg, 0.22))
        clicked_btn.configure(highlightbackground="#ffffff")
        theme = dict(preset)
        save_theme(theme)
        self._apply_theme(theme, rebuild=True)

    def log_line(self, text: str) -> None:
        self.events.put(("log", text))

    def set_status(self, text: str) -> None:
        self.events.put(("status", text))

    def _set_badge(self, badge_frame, text: str, tint_hex: str) -> None:
        tint_bg = alpha_blend(tint_hex, self._gc, 0.22)
        tint_fg = alpha_blend(tint_hex, "#ffffff", 0.75)
        tint_bd = alpha_blend(tint_hex, self._gc, 0.42)
        badge_frame.configure(bg=tint_bg, highlightbackground=tint_bd)
        badge_frame.winfo_children()[0].configure(text=text, bg=tint_bg, fg=tint_fg)

    def _drain_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log_time_lbl.configure(text=time.strftime("%H:%M:%S"))
                self.log_text.set(friendly_log_text(str(payload)))
            elif kind == "status":
                status = str(payload)
                if status == "循环运行中":
                    self._set_badge(self.run_badge, "● 运行中", "#7cdc3c")
                    self.start_btn.configure(state=tk.DISABLED)
                else:
                    self._set_badge(self.run_badge, "● 待机中", "#5090ff")
                    self.start_btn.configure(state=tk.NORMAL)
            elif kind == "device":
                value = str(payload)
                connected = value.endswith(" 已连接")
                self.device_text.set(value.replace(" 已连接", ""))
                self._set_badge(self.conn_badge, "● 已连接" if connected else "● 未连接", "#28c864" if connected else "#dc3c3c")
            elif kind == "preview":
                self.latest_image = payload
                if self.preview_window and self.preview_window.winfo_exists():
                    self.preview_window.show_image(payload)
            elif kind == "loop_count":
                self.loop_count += 1
                self.loop_lbl.configure(text=f"循环次数：{self.loop_count}")
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
