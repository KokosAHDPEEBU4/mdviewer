"""Regenerate assets/icon.ico (run from the project root: python tools/make_icon.py).

Drawn with Pillow so the icon is reproducible instead of a binary blob nobody
can edit: dark rounded tile, "M" glyph, accent underline.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)
SCALE = 8

BG = (21, 21, 26, 255)
BORDER = (62, 62, 76, 255)
GLYPH = (240, 240, 245, 255)
ACCENT = (108, 178, 255, 255)
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\arial.ttf",
)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _tile(size: int) -> Image.Image:
    s = size * SCALE
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    radius = int(s * 0.2)
    draw.rounded_rectangle(
        (int(s * 0.03), int(s * 0.03), s - int(s * 0.03), s - int(s * 0.03)),
        radius=radius,
        fill=BG,
        outline=BORDER,
        width=max(1, int(s * 0.018)),
    )

    font = _font(int(s * 0.62))
    text = "M"
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((s - (box[2] - box[0])) / 2 - box[0], s * 0.13 - box[1]),
        text,
        font=font,
        fill=GLYPH,
    )

    bar = (int(s * 0.24), int(s * 0.79), int(s * 0.76), int(s * 0.86))
    draw.rounded_rectangle(bar, radius=int(s * 0.035), fill=ACCENT)

    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    images = [_tile(size) for size in SIZES]
    OUT.parent.mkdir(exist_ok=True)
    images[-1].save(
        OUT,
        format="ICO",
        sizes=[(size, size) for size in SIZES],
        append_images=images[:-1],
    )
    print(f"written {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()