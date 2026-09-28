"""Finds and fetches free stock b-roll from Mixkit (free licence, commercial use, no attribution needed).

  stock.py search <term> [<term>...]   downloads 360p previews into <scratch>/stock/<term>/ and prints id and title
  stock.py fetch <id> <name>           downloads the full-size clip to public/broll/stock-<name>.mp4, cropped to 1080x1920
"""

import re
import subprocess
import sys
import urllib.request
from pathlib import Path

REELS = Path(__file__).resolve().parents[1]
PREVIEWS = Path(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[1] == "search-into" else None
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"}


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60) as response:
        return response.read()


def listing(term: str):
    page = get(f"https://mixkit.co/free-stock-video/{term}/").decode("utf-8", "ignore")
    titles = {int(i): slug.replace("-", " ") for slug, i in re.findall(r'href="/free-stock-video/([a-z0-9-]+)-(\d+)/"', page)}
    previews = re.findall(r"https://assets\.mixkit\.co/[^\"']+?/(\d+)-(?:video-)?360\.mp4", page)
    urls = re.findall(r"https://assets\.mixkit\.co/[^\"']+?-360\.mp4", page)
    return [(int(i), titles.get(int(i), "?"), url) for i, url in zip(previews, urls)]


def search(target: Path, terms):
    for term in terms:
        folder = target / term
        folder.mkdir(parents=True, exist_ok=True)
        for clip_id, title, url in dict.fromkeys(listing(term)):
            destination = folder / f"{clip_id}.mp4"
            if not destination.exists():
                destination.write_bytes(get(url))
            print(term, clip_id, title)


def full_size_url(clip_id: int):
    page = get(f"https://mixkit.co/free-stock-video/x-{clip_id}/").decode("utf-8", "ignore")
    candidates = re.findall(rf"https://assets\.mixkit\.co/[^\"']*?{clip_id}[^\"']*?-(?:video-)?(?:1080|720)\.mp4", page)
    return sorted(candidates, key=lambda url: "1080" not in url)[0]


def fetch(clip_id: int, name: str):
    source = REELS / "public" / "broll" / f"stock-{name}.source.mp4"
    source.write_bytes(get(full_size_url(clip_id)))
    target = REELS / "public" / "broll" / f"stock-{name}.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(source), "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,format=yuv420p",
         "-an", "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-g", "15", str(target)],
        check=True,
    )
    source.unlink()
    print("fetched", name)


if __name__ == "__main__":
    if sys.argv[1] == "search":
        search(Path(sys.argv[2]), sys.argv[3:])
    else:
        fetch(int(sys.argv[2]), sys.argv[3])
