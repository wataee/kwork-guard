from __future__ import annotations

import logging
from pathlib import Path
from PIL import Image, ImageDraw

ROOT_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT_DIR / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
ICON_PATH = ASSETS_DIR / "cockpit.ico"

logger = logging.getLogger("cockpit.icon")


def generate_cockpit_icon(output_path: Path | None = None) -> Path:
    target = output_path if output_path else ICON_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = 8
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=48,
        fill=(18, 22, 32, 255),
        outline=(0, 200, 255, 255),
        width=6,
    )

    badge_margin = 36
    draw.rounded_rectangle(
        [badge_margin, badge_margin, size - badge_margin, size - badge_margin],
        radius=32,
        fill=(28, 34, 48, 255),
        outline=(255, 180, 0, 255),
        width=4,
    )

    k_color = (0, 220, 255, 255)
    draw.polygon([(64, 60), (96, 60), (96, 196), (64, 196)], fill=k_color)
    draw.polygon([(96, 128), (160, 60), (196, 60), (124, 134)], fill=(255, 190, 20, 255))
    draw.polygon([(118, 126), (166, 196), (202, 196), (144, 114)], fill=(255, 190, 20, 255))

    draw.ellipse([116, 212, 140, 228], fill=(0, 255, 128, 255))

    sizes = [(16, 16), (32, 32), (48, 48), (256, 256)]
    img.save(target, format="ICO", sizes=sizes)
    return target


if __name__ == "__main__":
    generate_cockpit_icon()
