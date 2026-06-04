from __future__ import annotations

import zipfile
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
OUT_FILE = BASE_DIR / "templates.dat"


def main() -> int:
    if not TEMPLATES_DIR.exists():
        if OUT_FILE.exists():
            print(f"templates folder not found; keep existing {OUT_FILE}")
            return 0
        raise FileNotFoundError(f"templates folder not found: {TEMPLATES_DIR}")
    images = sorted(TEMPLATES_DIR.glob("*.png"))
    if not images:
        raise RuntimeError("no .png templates found")
    if OUT_FILE.exists():
        OUT_FILE.unlink()
    with zipfile.ZipFile(OUT_FILE, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for image in images:
            zf.write(image, f"templates/{image.name}")
            print(f"packed templates/{image.name}")
    print(f"created {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
