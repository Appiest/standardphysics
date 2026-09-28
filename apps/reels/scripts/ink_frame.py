"""Freeze one frame of a b-roll clip and redraw it as ink line art (XDoG), for the blueprint freeze-frames."""

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

REELS = Path(__file__).resolve().parents[1]
INK = np.array([13, 13, 12], dtype=np.float64)


def grab_frame(clip: str, frame: int) -> Image.Image:
    target = Path(tempfile.mkdtemp()) / "frame.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(REELS / "public" / "broll" / f"{clip}.mp4"), "-vf", f"select=eq(n\\,{frame})", "-frames:v", "1", str(target)],
        check=True,
    )
    return Image.open(target).convert("RGB")


def xdog(gray: np.ndarray, sigma=2.0, k=1.6, tau=0.995, epsilon=-0.002, phi=300.0) -> np.ndarray:
    near = gaussian_filter(gray, sigma)
    far = gaussian_filter(gray, sigma * k)
    difference = near - tau * far
    return np.where(difference >= epsilon, 1.0, 1.0 + np.tanh(phi * (difference - epsilon)))


def ink_layer(photo: Image.Image) -> Image.Image:
    gray = gaussian_filter(np.asarray(photo.convert("L"), dtype=np.float64) / 255.0, 1.0)
    coverage = 1.0 - np.clip(xdog(gray), 0, 1)
    rgba = np.zeros((*gray.shape, 4), dtype=np.uint8)
    rgba[..., :3] = INK
    rgba[..., 3] = (coverage * 255).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def main(clip: str, frame: int):
    target = REELS / "public" / "stills"
    target.mkdir(parents=True, exist_ok=True)
    photo = grab_frame(clip, frame)
    photo.save(target / f"{clip}-{frame}-photo.jpg", quality=92)
    ink_layer(photo).save(target / f"{clip}-{frame}-ink.png")
    print(clip, frame)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]))
