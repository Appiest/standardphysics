"""Cut the people out of a b-roll clip as transparent WebP frames, using masks from segment_people.swift."""

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageFilter

REELS = Path(__file__).resolve().parents[1]


def extract_frames(clip: Path, target: Path):
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), str(target / "f_%04d.png")], check=True)


def cut_out(frame: Path, mask: Path, target: Path):
    alpha = Image.open(mask).convert("L").filter(ImageFilter.GaussianBlur(1.2))
    person = Image.open(frame).convert("RGB")
    person.putalpha(alpha)
    person.save(target, "WEBP", quality=88)


def main(name: str):
    work = Path(tempfile.mkdtemp())
    extract_frames(REELS / "public" / "broll" / f"{name}.mp4", work / "frames")
    subprocess.run(["swift", str(REELS / "scripts" / "segment_people.swift"), str(work / "frames"), str(work / "masks")], check=True)
    target = REELS / "public" / "cutouts" / name
    target.mkdir(parents=True, exist_ok=True)
    frames = sorted((work / "frames").glob("*.png"))
    for frame in frames:
        cut_out(frame, work / "masks" / frame.name, target / f"{frame.stem}.webp")
    print(name, len(frames), "frames")


if __name__ == "__main__":
    for clip_name in sys.argv[1:]:
        main(clip_name)
