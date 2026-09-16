"""Render recorded sweep evidence as an MP4; never rerun or invent a route.

Install standardphysics-agents[replay], then run:
python -m standardphysics_agents.evaluation.sweep_video runs/accessibility-2m.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

SIZE = (1920, 1080)
FPS = 30
RUN_SECONDS = 5
CARD_SECONDS = 4
BACKGROUND = "#10191e"
TEXT = "#eef0e9"
MUTED = "#a7b3b8"
FLOOR = "#d7d6c9"
SOLID = "#36464e"
GRID_LINE = "#c8c9bd"
FIT = "#79c8b3"
BLOCKED = "#ee9b84"
BOARD_X, BOARD_Y, BOARD_SIZE = 64, 170, 768
DETAIL_X = 970


def _font(size: int) -> Any:
    candidates = (
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


FONTS = {size: _font(size) for size in (22, 26, 30, 34, 42, 54, 64, 104)}


def _text(draw: Any, xy: tuple[float, float], value: str, size: int = 30,
          color: str = TEXT) -> None:
    draw.text(xy, value, font=FONTS[size], fill=color)


def _canvas() -> tuple[Any, Any]:
    frame = Image.new("RGB", SIZE, BACKGROUND)
    return frame, ImageDraw.Draw(frame)


def _footer(draw: Any) -> None:
    _text(draw, (64, 1000), "Synthetic 2D layouts. 12-inch cells. Circular body-width approximation.", 26, MUTED)
    _text(draw, (64, 1038), "Geometry evidence only. This does not determine ADA compliance or lawsuit risk.", 22, MUTED)


def _point(cell: list[int], cell_size: float) -> tuple[float, float]:
    row, col = cell
    return BOARD_X + (col + 0.5) * cell_size, BOARD_Y + (row + 0.5) * cell_size


def _board(draw: Any, run: dict[str, Any]) -> float:
    rows = run["occupied"]
    cell_size = BOARD_SIZE / max(len(rows), len(rows[0]))
    for row, cells in enumerate(rows):
        for col, occupied in enumerate(cells):
            x, y = BOARD_X + col * cell_size, BOARD_Y + row * cell_size
            draw.rectangle((x, y, x + cell_size, y + cell_size),
                           fill=SOLID if occupied else FLOOR,
                           outline=SOLID if occupied else GRID_LINE)
    return cell_size


def _destination(draw: Any, point: tuple[float, float]) -> None:
    x, y = point
    draw.ellipse((x-15, y-15, x+15, y+15), fill=BACKGROUND, outline=TEXT, width=3)
    draw.ellipse((x-5, y-5, x+5, y+5), fill=TEXT)


def stop_index(run: dict[str, Any]) -> int:
    """Stop on the last fitting cell before the first insufficient clearance."""
    widths = run["path_widths_inches"]
    for index, width in enumerate(widths):
        if width + 1e-8 < run["body_width_inches"]:
            return max(0, index - 1)
    return max(0, len(widths) - 1)


def _avatar(draw: Any, point: tuple[float, float], radius: float,
            color: str) -> None:
    x, y = point
    draw.ellipse((x-radius, y-radius, x+radius, y+radius),
                 fill=BACKGROUND, outline=color, width=4)
    draw.rounded_rectangle((x-12, y-13, x+12, y+13), radius=4, fill=color)
    draw.line((x-19, y-17, x-19, y+17), fill=color, width=5)
    draw.line((x+19, y-17, x+19, y+17), fill=color, width=5)


def _animate_route(draw: Any, run: dict[str, Any], progress: float,
                   cell_size: float) -> None:
    points = [_point(cell, cell_size) for cell in run["path"]]
    _destination(draw, _point(run["goal"], cell_size))
    if not points:
        _avatar(draw, _point(run["start"], cell_size), 28, BLOCKED)
        return
    for point in points:
        x, y = point
        draw.ellipse((x-2, y-2, x+2, y+2), fill=SOLID)
    end = stop_index(run)
    # Move cell to cell: interpolation would invent unsimulated swept geometry.
    index = min(end, int(progress * (end + 1)))
    travelled = points[:index+1]
    color = FIT if run["fits"] else BLOCKED
    if len(travelled) > 1:
        draw.line(travelled, fill=BACKGROUND, width=13, joint="curve")
        draw.line(travelled, fill=color, width=7, joint="curve")
    if not run["fits"] and end + 1 < len(points):
        x, y = points[end+1]
        draw.line((x-12, y-12, x+12, y+12), fill=BLOCKED, width=5)
        draw.line((x-12, y+12, x+12, y-12), fill=BLOCKED, width=5)
    radius = run["body_width_inches"] / run["cell_size_inches"] * cell_size / 2
    _avatar(draw, points[index], radius, color)


def _run_details(draw: Any, run: dict[str, Any], index: int, count: int,
                 progress: float) -> None:
    color = FIT if run["fits"] else BLOCKED
    _text(draw, (DETAIL_X, 185), f"Run {index+1:02d} of {count}", 42)
    _text(draw, (DETAIL_X, 253), f"Layout {run['layout']+1:,}   Evaluation {run['evaluation']:,}", 26, MUTED)
    result = "Route fits" if run["fits"] else "Route is too tight"
    if not run["reachable"]:
        result = "No connected route"
    _text(draw, (DETAIL_X, 350), result, 64, color)
    _text(draw, (DETAIL_X, 455), "Wheelchair profile width", 30, MUTED)
    _text(draw, (DETAIL_X, 495), f"{run['body_width_inches']:g} inches", 54)
    _text(draw, (DETAIL_X, 590), "Route bottleneck in this grid", 30, MUTED)
    _text(draw, (DETAIL_X, 630), f"{run['bottleneck_width_inches']:.1f} inches", 54)
    status = "Replaying the measured path"
    if progress >= 1:
        status = "Destination reached" if run["fits"] else "Stops before insufficient clearance"
    if not run["reachable"]:
        status = "Start and destination are disconnected"
    _text(draw, (DETAIL_X, 748), status, 30, color)
    _text(draw, (DETAIL_X, 810), "Route search and connectivity oracle agree"
          if run["oracle_agrees"] else "Algorithm disagreement requires review", 26, MUTED)
    for tick in range(count):
        x = DETAIL_X + tick * 74
        draw.rectangle((x, 916, x+58, 924), fill=color if tick == index else SOLID)


def run_frame(run: dict[str, Any], index: int, count: int, elapsed: float) -> Any:
    frame, draw = _canvas()
    _text(draw, (64, 55), "Can a wheelchair reach the destination?", 54)
    cell_size = _board(draw, run)
    progress = min(1.0, max(0.0, (elapsed-0.5) / (RUN_SECONDS-1.5)))
    _animate_route(draw, run, progress, cell_size)
    _run_details(draw, run, index, count, progress)
    _footer(draw)
    return frame


def title_frame(result: dict[str, Any], *, closing: bool = False) -> Any:
    frame, draw = _canvas()
    _text(draw, (100, 110), "Standard Physics", 42, FIT)
    _text(draw, (100, 245), f"{result['evaluations']:,}", 104)
    _text(draw, (100, 380), "route-profile checks completed", 54)
    _text(draw, (100, 495), f"{result['layouts']:,} generated layouts   {result['routes']:,} route searches", 34, MUTED)
    if closing:
        _text(draw, (100, 620), f"{result['failures']:,} algorithm disagreements", 54, FIT)
        _text(draw, (100, 715), "TypeSafe can choose the next action; measured geometry checks it.", 34)
        _text(draw, (100, 780), "This geometry sweep made zero model calls.", 30, MUTED)
    else:
        _text(draw, (100, 620), f"Replay {len(result['recorded_runs'])} actual runs", 54)
        _text(draw, (100, 715), "Selected to show both fitting and too-tight routes.", 34)
        _text(draw, (100, 780), "These examples are not a statistical sample.", 30, MUTED)
    _text(draw, (100, 888), f"Reproducible seed {result['seed']}", 26, MUTED)
    _footer(draw)
    return frame


def render(result_path: Path, output: Path) -> dict[str, Any]:
    result = json.loads(result_path.read_text())
    runs = result.get("recorded_runs", [])
    if not runs:
        raise ValueError("No recorded runs. Run accessibility-sweep with --record-runs 10 first.")
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = imageio_ffmpeg.write_frames(
        str(output), SIZE, fps=FPS, codec="libx264", pix_fmt_out="yuv420p",
        quality=8, macro_block_size=1, output_params=["-movflags", "+faststart"],
    )
    writer.send(None)
    try:
        for _ in range(CARD_SECONDS * FPS):
            writer.send(title_frame(result).tobytes())
        for index, run in enumerate(runs):
            for tick in range(RUN_SECONDS * FPS):
                writer.send(run_frame(run, index, len(runs), tick/FPS).tobytes())
        for _ in range(CARD_SECONDS * FPS):
            writer.send(title_frame(result, closing=True).tobytes())
    finally:
        writer.close()
    poster = output.with_suffix(".png")
    run_frame(runs[0], 0, len(runs), RUN_SECONDS).save(poster)
    manifest = {
        "video": str(output.resolve()), "source": str(result_path.resolve()),
        "source_digest": result["digest"], "seed": result["seed"],
        "width": SIZE[0], "height": SIZE[1], "fps": FPS,
        "duration_seconds": 2*CARD_SECONDS + len(runs)*RUN_SECONDS,
        "selection": "Distinct layouts; prefer equal fit and too-tight cases for the first profile; not representative.",
        "chapters": [{"start_seconds": CARD_SECONDS+i*RUN_SECONDS,
                      "evaluation": run["evaluation"], "layout": run["layout"],
                      "fits": run["fits"]} for i, run in enumerate(runs)],
        "scope": "Coarse synthetic geometry only; no legal determination; zero model calls.",
    }
    output.with_suffix(".json").write_text(json.dumps(manifest, indent=2)+"\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("--out", type=Path, default=Path("runs/accessibility-ten-runs.mp4"))
    args = parser.parse_args()
    print(json.dumps(render(args.result, args.out), indent=2))


if __name__ == "__main__":
    main()
