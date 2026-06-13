"""
Generate icon.icns for the Mac app.
Run: python3 create_icon.py
Requires Pillow: pip install pillow
"""

import os
import shutil
import subprocess
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Installing Pillow...")
    os.system("/usr/bin/python3 -m pip install -q pillow")
    from PIL import Image, ImageDraw, ImageFont


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded square background — teal gradient simulation (solid dark teal)
    pad = size // 10
    r = size // 5  # corner radius

    def rounded_rect(draw, xy, radius, fill):
        x0, y0, x1, y1 = xy
        draw.rectangle([x0 + radius, y0, x1 - radius, y1], fill=fill)
        draw.rectangle([x0, y0 + radius, x1, y1 - radius], fill=fill)
        draw.ellipse([x0, y0, x0 + radius * 2, y0 + radius * 2], fill=fill)
        draw.ellipse([x1 - radius * 2, y0, x1, y0 + radius * 2], fill=fill)
        draw.ellipse([x0, y1 - radius * 2, x0 + radius * 2, y1], fill=fill)
        draw.ellipse([x1 - radius * 2, y1 - radius * 2, x1, y1], fill=fill)

    # Background: dark teal
    rounded_rect(draw, (pad, pad, size - pad, size - pad), r, (15, 61, 74, 255))

    # Water wave (lighter teal) at bottom third
    wave_y = int(size * 0.62)
    wave_pts = []
    import math
    steps = size * 2
    for i in range(steps + 1):
        x = pad + (i / steps) * (size - 2 * pad)
        y = wave_y + math.sin(i / steps * 4 * math.pi) * (size * 0.04)
        wave_pts.append((x, y))
    wave_pts += [(size - pad, size - pad), (pad, size - pad)]
    draw.polygon(wave_pts, fill=(8, 145, 178, 180))  # teal-600 semi

    # Second lighter wave
    wave_pts2 = []
    for i in range(steps + 1):
        x = pad + (i / steps) * (size - 2 * pad)
        y = wave_y + size * 0.07 + math.sin(i / steps * 4 * math.pi + 1) * (size * 0.03)
        wave_pts2.append((x, y))
    wave_pts2 += [(size - pad, size - pad), (pad, size - pad)]
    draw.polygon(wave_pts2, fill=(6, 182, 212, 140))  # teal-500

    # Kayak paddle — horizontal bar
    cx, cy = size // 2, int(size * 0.4)
    paddle_w = int(size * 0.55)
    paddle_h = int(size * 0.07)
    shaft_h  = int(size * 0.04)

    # Shaft
    draw.rectangle(
        [cx - paddle_w // 2, cy - shaft_h // 2, cx + paddle_w // 2, cy + shaft_h // 2],
        fill=(255, 255, 255, 230),
    )
    # Left blade
    blade_w = int(size * 0.14)
    blade_h = int(size * 0.22)
    draw.ellipse(
        [cx - paddle_w // 2 - blade_w // 2, cy - blade_h // 2,
         cx - paddle_w // 2 + blade_w // 2, cy + blade_h // 2],
        fill=(255, 255, 255, 210),
    )
    # Right blade
    draw.ellipse(
        [cx + paddle_w // 2 - blade_w // 2, cy - blade_h // 2,
         cx + paddle_w // 2 + blade_w // 2, cy + blade_h // 2],
        fill=(255, 255, 255, 210),
    )

    # Kayak hull (simple ellipse)
    hull_w = int(size * 0.42)
    hull_h = int(size * 0.12)
    hull_y = int(size * 0.56)
    draw.ellipse(
        [cx - hull_w // 2, hull_y - hull_h // 2,
         cx + hull_w // 2, hull_y + hull_h // 2],
        fill=(34, 197, 94, 220),  # green-500
    )

    return img


def main():
    iconset_dir = Path("icon.iconset")
    iconset_dir.mkdir(exist_ok=True)

    specs = [
        ("icon_16x16.png",      16),
        ("icon_16x16@2x.png",   32),
        ("icon_32x32.png",      32),
        ("icon_32x32@2x.png",   64),
        ("icon_64x64.png",      64),
        ("icon_64x64@2x.png",  128),
        ("icon_128x128.png",   128),
        ("icon_128x128@2x.png",256),
        ("icon_256x256.png",   256),
        ("icon_256x256@2x.png",512),
        ("icon_512x512.png",   512),
        ("icon_512x512@2x.png",1024),
    ]

    print("Generating icon PNGs…")
    for filename, size in specs:
        img = draw_icon(size)
        img.save(iconset_dir / filename, "PNG")
        print(f"  {filename}")

    print("Converting to icon.icns via iconutil…")
    result = subprocess.run(
        ["iconutil", "-c", "icns", str(iconset_dir)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("ERROR:", result.stderr)
        return

    shutil.rmtree(iconset_dir)
    print("Done — icon.icns created.")


if __name__ == "__main__":
    main()
