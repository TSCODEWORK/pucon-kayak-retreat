"""
Generate icon.icns for the Mac app from the PKR logo image.
Run: python3 create_icon.py
Requires Pillow: pip install pillow
"""

import os
import shutil
import subprocess
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:
    print("Installing Pillow...")
    os.system("/usr/bin/python3 -m pip install -q pillow")
    from PIL import Image, ImageOps


LOGO_PATH = Path("static/img/pkr-logo.jpg")


def make_icon_frame(size: int, logo: Image.Image) -> Image.Image:
    """Place the PKR logo on a dark teal rounded-square background."""
    import math

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # ── Rounded-square background ────────────────────────────────────────────
    pad = size // 12
    r   = size // 5
    bg  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    from PIL import ImageDraw
    d = ImageDraw.Draw(bg)

    def fill_rounded(draw, x0, y0, x1, y1, radius, color):
        draw.rectangle([x0 + radius, y0, x1 - radius, y1], fill=color)
        draw.rectangle([x0, y0 + radius, x1, y1 - radius], fill=color)
        for cx, cy in [(x0, y0), (x1 - radius*2, y0),
                       (x0, y1 - radius*2), (x1 - radius*2, y1 - radius*2)]:
            draw.ellipse([cx, cy, cx + radius*2, cy + radius*2], fill=color)

    fill_rounded(d, pad, pad, size - pad, size - pad, r, (15, 61, 74, 255))

    # ── Subtle wave strip at bottom ──────────────────────────────────────────
    wave_y = int(size * 0.78)
    steps  = size * 2
    pts    = []
    for i in range(steps + 1):
        x = pad + (i / steps) * (size - 2 * pad)
        y = wave_y + math.sin(i / steps * 4 * math.pi) * (size * 0.025)
        pts.append((x, y))
    pts += [(size - pad, size - pad), (pad, size - pad)]
    d.polygon(pts, fill=(8, 145, 178, 100))

    img = Image.alpha_composite(img, bg)

    # ── Logo: fit inside the background, centred, with padding ──────────────
    inner = size - pad * 4
    logo_copy = logo.copy().convert("RGBA")

    # Make white / near-white pixels transparent so the teal bg shows through
    data = logo_copy.getdata()
    new_data = []
    for r_val, g_val, b_val, a_val in data:
        if r_val > 220 and g_val > 220 and b_val > 220:
            new_data.append((r_val, g_val, b_val, 0))
        else:
            new_data.append((r_val, g_val, b_val, a_val))
    logo_copy.putdata(new_data)

    # Tint the remaining (dark) pixels white so logo is white-on-teal
    tinted = Image.new("RGBA", logo_copy.size, (255, 255, 255, 255))
    tinted.putalpha(logo_copy.split()[3])  # use logo alpha as mask

    logo_copy.thumbnail((inner, inner), Image.LANCZOS)
    tinted_small = tinted.resize(logo_copy.size, Image.LANCZOS)

    x_off = (size - tinted_small.width)  // 2
    y_off = (size - tinted_small.height) // 2 - int(size * 0.02)  # slightly above centre
    img.paste(tinted_small, (x_off, y_off), tinted_small)

    return img


def main():
    if not LOGO_PATH.exists():
        print(f"Logo not found at {LOGO_PATH} — falling back to drawn icon")
        # Could call the old draw_icon here, but for now just error out
        raise FileNotFoundError(f"Logo image missing: {LOGO_PATH}")

    logo = Image.open(LOGO_PATH)

    iconset_dir = Path("icon.iconset")
    iconset_dir.mkdir(exist_ok=True)

    specs = [
        ("icon_16x16.png",       16),
        ("icon_16x16@2x.png",    32),
        ("icon_32x32.png",       32),
        ("icon_32x32@2x.png",    64),
        ("icon_64x64.png",       64),
        ("icon_64x64@2x.png",   128),
        ("icon_128x128.png",    128),
        ("icon_128x128@2x.png", 256),
        ("icon_256x256.png",    256),
        ("icon_256x256@2x.png", 512),
        ("icon_512x512.png",    512),
        ("icon_512x512@2x.png",1024),
    ]

    print("Generating icon PNGs from PKR logo…")
    for filename, size in specs:
        frame = make_icon_frame(size, logo)
        frame.save(iconset_dir / filename, "PNG")
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
