from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import io
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageStat

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "config.yaml"
DEFAULT_ADB = Path(r"C:\Program Files\Tencent\GameAssist\Application\6.10.5910.509\adb.exe")
TEMPLATE_BUNDLE = BASE_DIR / "templates.dat"
HIDDEN_SUBPROCESS_KWARGS = (
    {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    if os.name == "nt"
    else {}
)


def resolve_adb_path(configured: str | None = None) -> Path:
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    candidates.append(DEFAULT_ADB)

    env_adb = os.environ.get("ADB")
    if env_adb:
        candidates.append(Path(env_adb))

    where_adb = shutil.which("adb")
    if where_adb:
        candidates.append(Path(where_adb))

    for root in (
        Path(r"C:\Program Files\Tencent\GameAssist\Application"),
        Path(r"C:\Program Files (x86)\Tencent\GameAssist\Application"),
    ):
        if root.exists():
            candidates.extend(sorted(root.glob(r"*\adb.exe"), reverse=True))

    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def list_connected_devices(adb_path: Path) -> list[str]:
    proc = subprocess.run(
        [str(adb_path), "devices", "-l"],
        text=True,
        capture_output=True,
        timeout=10,
        **HIDDEN_SUBPROCESS_KWARGS,
    )
    devices: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices


def display_quality(img: Image.Image) -> float:
    w, h = img.size
    arr = np.asarray(img.resize((160, 90)).convert("L"), dtype=np.float32)
    mean = float(arr.mean())
    std = float(arr.std())
    if mean < 5.0 or std < 4.0:
        return 0.0
    aspect = w / max(1, h)
    aspect_score = max(0.0, 1.0 - abs(aspect - (16 / 9)) / 0.8)
    size_score = min(1.0, (w * h) / (1280 * 720))
    contrast_score = min(1.0, std / 55.0)
    return size_score * 0.45 + aspect_score * 0.35 + contrast_score * 0.20


def auto_detect_config(cfg: dict[str, Any]) -> dict[str, Any]:
    adb_path = resolve_adb_path(cfg.get("adb_path"))
    if not adb_path.exists():
        raise FileNotFoundError(f"adb not found: {adb_path}")

    devices = list_connected_devices(adb_path)
    if not devices:
        raise RuntimeError("no connected ADB device found")
    configured_device = str(cfg.get("device") or "")
    device = configured_device if configured_device in devices else devices[0]

    candidates: list[tuple[float, str, Image.Image]] = []
    for display in [str(x) for x in range(0, 6)]:
        try:
            img = AdbClient(adb_path, device, display).capture()
        except Exception:
            continue
        score = display_quality(img)
        candidates.append((score, display, img))
    if not candidates:
        raise RuntimeError("no display could be captured")
    candidates.sort(key=lambda item: item[0], reverse=True)
    score, display, img = candidates[0]
    if score <= 0:
        raise RuntimeError("captured displays look blank")
    return {
        "adb_path": str(adb_path),
        "device": device,
        "display": display,
        "display_score": score,
        "image": img,
        "devices": devices,
    }


def update_config_file(path: Path, values: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    for key, value in values.items():
        line = f'{key}: "{value}"' if key == "display" else f"{key}: {value}"
        pattern = rf"(?m)^{re.escape(key)}:\s*.*$"
        if re.search(pattern, text):
            text = re.sub(pattern, lambda _m, replacement=line: replacement, text, count=1)
        else:
            text = line + "\n" + text
    path.write_text(text, encoding="utf-8")


@dataclasses.dataclass
class MatchResult:
    name: str
    score: float
    action: dict[str, Any]
    delay_after: float


class AdbClient:
    def __init__(self, adb: Path, device: str, display: str):
        self.adb = adb
        self.device = device
        self.display = display

    def run(self, *args: str, timeout: int = 15) -> subprocess.CompletedProcess[str]:
        cmd = [str(self.adb)]
        if self.device:
            cmd += ["-s", self.device]
        cmd += list(args)
        return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, **HIDDEN_SUBPROCESS_KWARGS)

    def capture(self) -> Image.Image:
        cmd = [str(self.adb)]
        if self.device:
            cmd += ["-s", self.device]
        cmd += ["exec-out", "screencap", "-d", self.display, "-p"]
        data = subprocess.check_output(cmd, timeout=20, **HIDDEN_SUBPROCESS_KWARGS)
        if not data.startswith(b"\x89PNG"):
            raise RuntimeError("ADB screencap did not return PNG data.")
        return Image.open(io.BytesIO(data)).convert("RGB")

    def tap(self, x: int, y: int) -> None:
        proc = self.run("shell", "input", "-d", self.display, "tap", str(x), str(y), timeout=10)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "ADB tap failed")


def load_config(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is not available. Install pyyaml or keep using the bundled environment.")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def crop_box(img: Image.Image, box: list[float] | None) -> Image.Image:
    if not box:
        return img
    w, h = img.size
    left, top, right, bottom = box
    if max(box) <= 1.0:
        px = (int(left * w), int(top * h), int(right * w), int(bottom * h))
    else:
        px = (int(left), int(top), int(right), int(bottom))
    return img.crop(px)


def gray_small(img: Image.Image, size: tuple[int, int] = (160, 90)) -> np.ndarray:
    arr = np.asarray(img.convert("L").resize(size, Image.Resampling.BILINEAR), dtype=np.float32)
    return arr


def template_bundle_name(path: Path) -> str:
    try:
        return path.relative_to(BASE_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def template_exists(path: Path) -> bool:
    if path.exists():
        return True
    if TEMPLATE_BUNDLE.exists():
        name = template_bundle_name(path)
        try:
            with zipfile.ZipFile(TEMPLATE_BUNDLE) as zf:
                return name in zf.namelist()
        except zipfile.BadZipFile:
            return False
    return False


def load_template_image(path: Path) -> Image.Image:
    if path.exists():
        return Image.open(path).convert("RGB")
    if TEMPLATE_BUNDLE.exists():
        name = template_bundle_name(path)
        with zipfile.ZipFile(TEMPLATE_BUNDLE) as zf:
            with zf.open(name) as f:
                return Image.open(io.BytesIO(f.read())).convert("RGB")
    raise FileNotFoundError(path)


def similarity(
    current: Image.Image,
    ref_path: Path,
    ref_crop: list[float] | None,
    current_crop: list[float] | None = None,
) -> float:
    current = crop_box(current, current_crop)
    ref = load_template_image(ref_path)
    ref = crop_box(ref, ref_crop)
    ref = ref.resize(current.size, Image.Resampling.BILINEAR)

    a = gray_small(current)
    b = gray_small(ref)

    # Blend two cheap signals. Absolute difference is stable for full-screen states;
    # correlation helps when brightness varies because of animation overlays.
    diff_score = 1.0 - float(np.mean(np.abs(a - b)) / 255.0)
    aa = a - float(a.mean())
    bb = b - float(b.mean())
    denom = float(np.linalg.norm(aa) * np.linalg.norm(bb))
    corr = 0.0 if denom == 0 else float(np.sum(aa * bb) / denom)
    corr_score = (corr + 1.0) / 2.0
    return max(0.0, min(1.0, diff_score * 0.65 + corr_score * 0.35))


def region_mean_rgb(img: Image.Image, region: list[float]) -> tuple[float, float, float]:
    roi = crop_box(img, region)
    return tuple(float(x) for x in ImageStat.Stat(roi).mean[:3])


def heuristic_score(img: Image.Image, rule: dict[str, Any]) -> float:
    kind = rule.get("heuristic")
    if kind == "gold_button":
        region = rule["region"]
        roi = crop_box(img, region)
        arr = np.asarray(roi, dtype=np.float32)
        r = arr[:, :, 0]
        g = arr[:, :, 1]
        b = arr[:, :, 2]
        # Gold buttons may occupy only part of the configured region, so use
        # both an average color signal and a bright-gold pixel ratio.
        mean_score = (((float(r.mean()) + float(g.mean())) / 2.0) - float(b.mean())) / 120.0
        gold_mask = (r > 120) & (g > 85) & (r > b + 35) & (g > b + 15)
        ratio_score = float(gold_mask.mean()) * 5.0
        warm = ((r + g) / 2.0) - b
        percentile_score = float(np.percentile(warm, 90)) / 150.0
        return max(0.0, min(1.0, max(mean_score, ratio_score, percentile_score)))
    if kind == "blue_menu":
        region = rule["region"]
        r, g, b = region_mean_rgb(img, region)
        return max(0.0, min(1.0, (b - r + 35.0) / 120.0))
    if kind == "victory_screen":
        roi = crop_box(img, rule.get("region", [0.26, 0.08, 0.78, 0.40]))
        arr = np.asarray(roi, dtype=np.float32)
        r = arr[:, :, 0]
        g = arr[:, :, 1]
        b = arr[:, :, 2]
        brightness = arr.mean(axis=2)
        # The first victory splash is a bright sky scene with very large warm
        # text. Later MVP/score pages can also contain warm labels, but their
        # center-top region is much darker. Gate on brightness first.
        if float(np.percentile(brightness, 75)) < 185.0:
            return 0.0
        orange = (r > 110) & (g > 45) & (r > g + 25) & (g > b + 10)
        brown = (r > 80) & (g > 35) & (r > b + 30) & (g > b)
        warm_ratio = float((orange | brown).mean())
        contrast = float(np.percentile(r - b, 90)) / 180.0
        return max(0.0, min(1.0, max(warm_ratio * 18.0, contrast)))
    return 0.0


def resolve_template(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def find_state(img: Image.Image, rules: list[dict[str, Any]], verbose: bool = False) -> MatchResult | None:
    best: tuple[str, float] | None = None
    for rule in rules:
        if rule.get("enabled", True) is False:
            continue
        name = rule["name"]
        threshold = float(rule.get("threshold", 0.80))
        mode = rule.get("mode", "reference")

        if mode == "reference":
            ref = resolve_template(rule["template"])
            if not template_exists(ref):
                if verbose:
                    print(f"[skip] {name}: missing template {ref}")
                continue
            score = similarity(img, ref, rule.get("reference_crop"), rule.get("current_crop"))
        elif mode == "heuristic":
            score = heuristic_score(img, rule)
        else:
            continue

        if best is None or score > best[1]:
            best = (name, score)
        if verbose:
            print(f"[score] {name}: {score:.3f} threshold={threshold:.3f}")
        if score >= threshold:
            return MatchResult(
                name=name,
                score=score,
                action=rule.get("action", {"type": "none"}),
                delay_after=float(rule.get("delay_after", 1.0)),
            )
    if verbose and best:
        print(f"[miss] best={best[0]} score={best[1]:.3f}")
    return None


def action_point(action: dict[str, Any], size: tuple[int, int]) -> tuple[int, int]:
    w, h = size
    x = float(action["x"])
    y = float(action["y"])
    if action.get("coord", "norm") == "norm":
        return int(round(x * w)), int(round(y * h))
    return int(round(x)), int(round(y))


def perform_action(adb: AdbClient, img: Image.Image, result: MatchResult, dry_run: bool) -> None:
    action = result.action
    kind = action.get("type", "none")
    if kind == "none":
        print(f"[state] {result.name} score={result.score:.3f}; no action")
        return
    if kind != "tap":
        print(f"[state] {result.name} score={result.score:.3f}; unsupported action={kind}")
        return
    x, y = action_point(action, img.size)
    if dry_run:
        print(f"[dry-run] {result.name} score={result.score:.3f}; would tap ({x}, {y})")
    else:
        print(f"[tap] {result.name} score={result.score:.3f}; tap ({x}, {y})")
        adb.tap(x, y)
    for followup in action.get("followup_taps", []):
        delay = float(followup.get("delay", 0.0))
        fx, fy = action_point(followup, img.size)
        if dry_run:
            print(f"[dry-run followup] {result.name}; after {delay:.1f}s would tap ({fx}, {fy})")
        else:
            if delay > 0:
                time.sleep(delay)
            print(f"[followup tap] {result.name}; tap ({fx}, {fy})")
            adb.tap(fx, fy)


def save_capture(img: Image.Image, prefix: str) -> Path:
    out = BASE_DIR / "captures"
    out.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out / f"{stamp}-{prefix}.png"
    img.save(path)
    return path


def run_loop(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    adb_path = resolve_adb_path(args.adb or cfg.get("adb_path"))
    if not adb_path.exists():
        raise FileNotFoundError(f"adb not found: {adb_path}")
    device = args.device or cfg.get("device") or "emulator-5554"
    display = str(args.display or cfg.get("display") or "2")
    adb = AdbClient(adb_path, device, display)
    rules = cfg.get("states", [])
    dry_run = not args.enable_clicks

    print(f"[config] adb={adb_path}")
    print(f"[config] device={device} display={display} dry_run={dry_run}")

    interval = float(args.interval or cfg.get("interval", 1.2))
    while True:
        img = adb.capture()
        if args.save_debug:
            print(f"[capture] {save_capture(img, 'loop')}")
        result = find_state(img, rules, verbose=args.verbose)
        if result:
            perform_action(adb, img, result, dry_run=dry_run)
            time.sleep(result.delay_after)
        else:
            print("[wait] no configured state matched")
            time.sleep(interval)
        if args.once:
            break


def list_devices(args: argparse.Namespace) -> None:
    adb = resolve_adb_path(args.adb)
    proc = subprocess.run(
        [str(adb), "devices", "-l"],
        text=True,
        capture_output=True,
        timeout=10,
        **HIDDEN_SUBPROCESS_KWARGS,
    )
    print(proc.stdout.strip())
    if proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)


def auto_config(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    result = auto_detect_config(cfg)
    config_path = Path(args.config)
    update_config_file(
        config_path,
        {
            "adb_path": result["adb_path"],
            "device": result["device"],
            "display": result["display"],
        },
    )
    img = result["image"]
    path = save_capture(img, f"auto-display-{result['display']}")
    print(f"[auto] adb_path={result['adb_path']}")
    print(f"[auto] device={result['device']}")
    print(f"[auto] display={result['display']} score={result['display_score']:.3f}")
    print(f"[auto] devices={', '.join(result['devices'])}")
    print(f"[auto] saved preview={path} size={img.size[0]}x{img.size[1]}")


def test_capture(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    adb_path = resolve_adb_path(args.adb or cfg.get("adb_path"))
    device = args.device or cfg.get("device") or "emulator-5554"
    display = str(args.display or cfg.get("display") or "2")
    img = AdbClient(adb_path, device, display).capture()
    path = save_capture(img, "test")
    print(f"[ok] saved {path} size={img.size[0]}x{img.size[1]}")


def bootstrap_clicks(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    adb_path = resolve_adb_path(args.adb or cfg.get("adb_path"))
    device = args.device or cfg.get("device") or "emulator-5554"
    display = str(args.display or cfg.get("display") or "2")
    adb = AdbClient(adb_path, device, display)
    dry_run = not args.enable_clicks
    steps = cfg.get("bootstrap_steps", [])
    if not steps:
        print("[bootstrap] no steps configured")
        return
    img = adb.capture()
    w, h = img.size
    for step in steps:
        x = int(round(float(step["x"]) * w))
        y = int(round(float(step["y"]) * h))
        name = step.get("name", "step")
        if dry_run:
            print(f"[dry-run bootstrap] {name}: would tap ({x}, {y})")
        else:
            print(f"[bootstrap tap] {name}: tap ({x}, {y})")
            adb.tap(x, y)
        time.sleep(float(step.get("delay_after", 1.5)))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Tencent emulator screen recognizer and tapper.")

    def add_common(target: argparse.ArgumentParser, defaults: bool = True) -> None:
        missing = None if defaults else argparse.SUPPRESS
        false = False if defaults else argparse.SUPPRESS
        config_default = str(DEFAULT_CONFIG) if defaults else argparse.SUPPRESS
        target.add_argument("--config", default=config_default)
        target.add_argument("--adb", default=missing)
        target.add_argument("--device", default=missing)
        target.add_argument("--display", default=missing)
        target.add_argument("--interval", type=float, default=missing)
        target.add_argument(
            "--enable-clicks",
            action="store_true",
            default=false,
            help="Actually tap through ADB. Default is dry-run.",
        )
        target.add_argument("--once", action="store_true", default=false)
        target.add_argument("--verbose", action="store_true", default=false)
        target.add_argument("--save-debug", action="store_true", default=false)

    add_common(p)

    sub = p.add_subparsers(dest="cmd")
    add_common(sub.add_parser("devices"), defaults=False)
    add_common(sub.add_parser("auto-config"), defaults=False)
    add_common(sub.add_parser("test-capture"), defaults=False)
    add_common(sub.add_parser("bootstrap-clicks"), defaults=False)
    add_common(sub.add_parser("run"), defaults=False)
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    if args.cmd == "devices":
        list_devices(args)
    elif args.cmd == "auto-config":
        auto_config(args, cfg)
    elif args.cmd == "test-capture":
        test_capture(args, cfg)
    elif args.cmd == "bootstrap-clicks":
        bootstrap_clicks(args, cfg)
    else:
        run_loop(args, cfg)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[stop] interrupted")
