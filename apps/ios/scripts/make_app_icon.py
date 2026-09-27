"""Installs the Standard Physics mark (V2.1) as the app icon.

The mark is white S and P letters with width arrows on navy #060061. Its
source is the SVG in apps/web/public/brand; pass the 1024 px PNG exported
from it, since Pillow cannot rasterise SVG:

    python3 apps/ios/scripts/make_app_icon.py standard-physics-icon-1024.png

The navy icon is already dark, so it serves dark mode as well. The tinted
variant is its luminance, which is what iOS reads before applying the hue.
"""

from __future__ import annotations

import json
import pathlib
import sys

from PIL import Image

SIZE = 1024
ASSETS = pathlib.Path(__file__).resolve().parents[1] / "StandardPhysics/Resources/Assets.xcassets"
ICON_SET = ASSETS / "AppIcon.appiconset"

CONTENTS = {
    "images": [
        {"filename": "icon-light.png", "idiom": "universal", "platform": "ios", "size": "1024x1024"},
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


def load_icon(path: pathlib.Path) -> Image.Image:
    """App Store icons must be opaque and exactly 1024 px square."""
    icon = Image.open(path).convert("RGB")
    if icon.size != (SIZE, SIZE):
        icon = icon.resize((SIZE, SIZE), Image.LANCZOS)
    return icon


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    icon = load_icon(pathlib.Path(sys.argv[1]))
    ICON_SET.mkdir(parents=True, exist_ok=True)
    icon.save(ICON_SET / "icon-light.png")
    icon.convert("L").convert("RGB").save(ICON_SET / "icon-tinted.png")
    (ICON_SET / "Contents.json").write_text(json.dumps(CONTENTS, indent=2) + "\n")
    print(f"wrote {ICON_SET}")


if __name__ == "__main__":
    main()
