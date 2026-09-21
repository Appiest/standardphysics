"""Draws the app icon into StandardPhysics/Resources/Assets.xcassets.

The icon is a plan view of the thing the app looks for: an aisle pinched by a
counter, with the clearance across the narrow point dimensioned in the same
blue the app marks a finding in. It is the 403.5.1 failure that sends most
shops their first demand letter, drawn at the moment of measuring.

No lettering. An icon renders at 60 points on a home screen, where a number
would be a smudge, and the shapes carry it instead.

Run it after changing any colour in AppTheme:

    python3 apps/ios/scripts/make_app_icon.py
"""

from __future__ import annotations

import json
import pathlib

from PIL import Image, ImageDraw

SIZE = 1024
SUPERSAMPLE = 4
ASSETS = pathlib.Path(__file__).resolve().parents[1] / "StandardPhysics/Resources/Assets.xcassets"
ICON_SET = ASSETS / "AppIcon.appiconset"

# The sheet, the ink and the accent are AppTheme's, so the icon and the first
# screen behind it are the same drawing.
PAPER = (0xF6, 0xF5, 0xF1)
GRID_MINOR = (0xEC, 0xEB, 0xE5)
GRID_MAJOR = (0xE0, 0xDE, 0xD6)
INK = (0x1B, 0x1C, 0x1E)
ACCENT = (0x2F, 0x5E, 0x9E)

DARK_PAPER = (0x16, 0x17, 0x1A)
DARK_GRID_MINOR = (0x1F, 0x21, 0x25)
DARK_GRID_MAJOR = (0x27, 0x2A, 0x2F)
DARK_INK = (0xEA, 0xE8, 0xE2)
DARK_ACCENT = (0x7C, 0xA8, 0xDE)

# The walls run off all four edges, so the white band between them reads as a
# room the icon is a window onto rather than as three stripes on a card.
LEFT_WALL = (0, 188)
RIGHT_WALL = (836, SIZE)
COUNTER = (596, 836, 296, 728)

DIMENSION_Y = SIZE // 2
DIMENSION_WEIGHT = 46
ARROWHEAD_LENGTH = 96
ARROWHEAD_HALF_WIDTH = 52


class Palette:
    def __init__(self, paper, grid_minor, grid_major, ink, accent):
        self.paper = paper
        self.grid_minor = grid_minor
        self.grid_major = grid_major
        self.ink = ink
        self.accent = accent


LIGHT = Palette(PAPER, GRID_MINOR, GRID_MAJOR, INK, ACCENT)
DARK = Palette(DARK_PAPER, DARK_GRID_MINOR, DARK_GRID_MAJOR, DARK_INK, DARK_ACCENT)


def draw_grid(draw: ImageDraw.ImageDraw, palette: Palette, scale: int) -> None:
    for step, colour, weight in ((64, palette.grid_minor, 2), (256, palette.grid_major, 3)):
        for offset in range(0, SIZE + 1, step):
            position = offset * scale
            draw.line([(position, 0), (position, SIZE * scale)], fill=colour, width=weight * scale)
            draw.line([(0, position), (SIZE * scale, position)], fill=colour, width=weight * scale)


def draw_walls(draw: ImageDraw.ImageDraw, palette: Palette, scale: int) -> None:
    """Two walls bleeding off the sheet, and the counter narrowing the aisle."""
    for left, right in (LEFT_WALL, RIGHT_WALL):
        draw.rectangle([left * scale, 0, right * scale, SIZE * scale], fill=palette.ink)
    left, right, top, bottom = COUNTER
    draw.rectangle([left * scale, top * scale, right * scale, bottom * scale], fill=palette.ink)


def draw_dimension(draw: ImageDraw.ImageDraw, palette: Palette, scale: int) -> None:
    """The measurement across the pinch, arrowheads touching both faces."""
    start, end = LEFT_WALL[1], COUNTER[0]

    draw.rectangle(
        [
            start * scale,
            (DIMENSION_Y - DIMENSION_WEIGHT // 2) * scale,
            end * scale,
            (DIMENSION_Y + DIMENSION_WEIGHT // 2) * scale,
        ],
        fill=palette.accent,
    )

    for x, direction in ((start, 1), (end, -1)):
        tip = x * scale
        base = (x + direction * ARROWHEAD_LENGTH) * scale
        draw.polygon(
            [
                (tip, DIMENSION_Y * scale),
                (base, (DIMENSION_Y - ARROWHEAD_HALF_WIDTH) * scale),
                (base, (DIMENSION_Y + ARROWHEAD_HALF_WIDTH) * scale),
            ],
            fill=palette.accent,
        )


def render(palette: Palette) -> Image.Image:
    scale = SUPERSAMPLE
    canvas = Image.new("RGB", (SIZE * scale, SIZE * scale), palette.paper)
    draw = ImageDraw.Draw(canvas)
    draw_grid(draw, palette, scale)
    draw_walls(draw, palette, scale)
    draw_dimension(draw, palette, scale)
    return canvas.resize((SIZE, SIZE), Image.LANCZOS)


def tinted(image: Image.Image) -> Image.Image:
    """iOS supplies the hue for a tinted icon and reads only the luminance."""
    return image.convert("L").convert("RGB")


CONTENTS = {
    "images": [
        {"filename": "icon-light.png", "idiom": "universal", "platform": "ios", "size": "1024x1024"},
        {
            "appearances": [{"appearance": "luminosity", "value": "dark"}],
            "filename": "icon-dark.png",
            "idiom": "universal",
            "platform": "ios",
            "size": "1024x1024",
        },
        {
            "appearances": [{"appearance": "luminosity", "value": "tinted"}],
            "filename": "icon-tinted.png",
            "idiom": "universal",
            "platform": "ios",
            "size": "1024x1024",
        },
    ],
    "info": {"author": "xcode", "version": 1},
}


def main() -> None:
    ICON_SET.mkdir(parents=True, exist_ok=True)
    light = render(LIGHT)
    dark = render(DARK)
    light.save(ICON_SET / "icon-light.png")
    dark.save(ICON_SET / "icon-dark.png")
    tinted(dark).save(ICON_SET / "icon-tinted.png")
    (ICON_SET / "Contents.json").write_text(json.dumps(CONTENTS, indent=2) + "\n")
    (ASSETS / "Contents.json").write_text(
        json.dumps({"info": {"author": "xcode", "version": 1}}, indent=2) + "\n"
    )
    print(f"wrote {ICON_SET}")


if __name__ == "__main__":
    main()
