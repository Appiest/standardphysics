"""The room JSON a model reads, drawn as a top-down floor plan.

The plan is drawn only from `room_view` output, never from the scene graph, so
an image-conditioned model is shown exactly the facts the text gives it. Any
difference it makes is the difference seeing them makes.

Movable pieces are orange and tagged with the start of their id. Fixed pieces
are grey, darker when they stand on a counter. A door's keep-clear area is
tinted teal. Problems are red: a circle the size of the clear space the rule
asks for, centred where the checker measured it. Grid lines are one metre
apart and labelled in metres, matching the JSON coordinates.
"""

from __future__ import annotations

import base64
import io
import json
import math

from PIL import Image, ImageDraw, ImageFont

IMAGE_SIZE = 768
MARGIN = 40
INCH = 0.0254
TAG_LENGTH = 4
PALETTE = {
    "background": "#f7f6f2", "grid": "#d4d0c6", "axis_text": "#6b6a66",
    "wall": "#2e3336", "door": "#3f8f89", "keep_clear": "#bfe0dc", "fixed": "#9aa3a1",
    "on_counter": "#5f6866", "movable": "#e07b28", "tag": "#1d1d1b", "stop": "#2a62c9", "problem": "#d6312b",
}


class _Canvas:
    def __init__(self, view: dict):
        left, bottom, right, top = _extent(view)
        self.left, self.bottom = left, bottom
        self.scale = (IMAGE_SIZE - 2 * MARGIN) / max(right - left, top - bottom, 1.0)
        self.bounds = (left, bottom, right, top)
        self.image = Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE), PALETTE["background"])
        self.draw = ImageDraw.Draw(self.image, "RGBA")
        self.font = ImageFont.load_default(size=13)

    def point(self, xy) -> tuple[float, float]:
        return (MARGIN + (xy[0] - self.left) * self.scale, IMAGE_SIZE - MARGIN - (xy[1] - self.bottom) * self.scale)

    def polygon(self, corners, fill: str, outline: str | None = None) -> None:
        self.draw.polygon([self.point(corner) for corner in corners], fill=fill, outline=outline)

    def label(self, xy, text: str, color: str) -> None:
        x, y = self.point(xy)
        self.draw.text((x, y), text, fill=color, font=self.font, anchor="mm", stroke_width=2,
                       stroke_fill=PALETTE["background"])


def _points(view: dict):
    for wall in view.get("walls", []):
        yield wall[:2]
        yield wall[2:]
    for group in ("doors", "fixed_objects", "movable_objects"):
        for piece in view.get(group, []):
            yield from piece.get("corners", [])


def _extent(view: dict) -> tuple[float, float, float, float]:
    points = list(_points(view))
    if not points:
        raise ValueError("the room view has nothing to draw")
    xs, ys = [x for x, _ in points], [y for _, y in points]
    return min(xs), min(ys), max(xs), max(ys)


def _grid(canvas: _Canvas) -> None:
    left, bottom, right, top = canvas.bounds
    for metre in range(math.ceil(left), math.floor(right) + 1):
        canvas.draw.line([canvas.point((metre, bottom)), canvas.point((metre, top))], fill=PALETTE["grid"])
        x, _ = canvas.point((metre, bottom))
        canvas.draw.text((x, IMAGE_SIZE - MARGIN / 2), f"x={metre}", fill=PALETTE["axis_text"], font=canvas.font,
                         anchor="mm")
    for metre in range(math.ceil(bottom), math.floor(top) + 1):
        canvas.draw.line([canvas.point((left, metre)), canvas.point((right, metre))], fill=PALETTE["grid"])
        _, y = canvas.point((left, metre))
        canvas.draw.text((MARGIN / 2, y), f"y={metre}", fill=PALETTE["axis_text"], font=canvas.font, anchor="mm")


def _centre(corners) -> tuple[float, float]:
    return sum(x for x, _ in corners) / len(corners), sum(y for _, y in corners) / len(corners)


def _pieces(canvas: _Canvas, view: dict) -> None:
    for door in view.get("doors", []):
        canvas.polygon(door["keep_clear"], PALETTE["keep_clear"])
    for wall in view.get("walls", []):
        canvas.draw.line([canvas.point(wall[:2]), canvas.point(wall[2:])], fill=PALETTE["wall"], width=5)
    for door in view.get("doors", []):
        canvas.polygon(door["corners"], PALETTE["door"])
    for piece in sorted(view.get("fixed_objects", []), key=lambda piece: piece.get("on_a_counter", False)):
        shade = PALETTE["on_counter" if piece.get("on_a_counter") else "fixed"]
        canvas.polygon(piece["corners"], shade, outline=PALETTE["wall"])
    for piece in view.get("fixed_objects", []):
        if "id" in piece:
            canvas.label(_centre(piece["corners"]), piece["id"][:TAG_LENGTH], PALETTE["tag"])
    for piece in view.get("movable_objects", []):
        canvas.polygon(piece["corners"], PALETTE["movable"], outline=PALETTE["tag"])
        canvas.label(_centre(piece["corners"]), piece["id"][:TAG_LENGTH], PALETTE["tag"])


def _marks(canvas: _Canvas, view: dict) -> None:
    for stop in view.get("route_stops", []):
        x, y = canvas.point(stop["at"])
        canvas.draw.ellipse([x - 5, y - 5, x + 5, y + 5], fill=PALETTE["stop"])
        canvas.label((stop["at"][0], stop["at"][1] + 12 / canvas.scale), stop["name"], PALETTE["stop"])
    for index, problem in enumerate(view.get("problems", []), start=1):
        if not problem.get("at"):
            continue
        x, y = canvas.point(problem["at"])
        radius = max(8.0, (problem.get("required_inches") or 0) * INCH * canvas.scale / 2)
        canvas.draw.ellipse([x - radius, y - radius, x + radius, y + radius], outline=PALETTE["problem"], width=3)
        canvas.label(problem["at"], f"P{index}", PALETTE["problem"])


def render(view: dict) -> Image.Image:
    canvas = _Canvas(view)
    _grid(canvas)
    _pieces(canvas, view)
    _marks(canvas, view)
    return canvas.image


def data_url(view: dict) -> str:
    buffer = io.BytesIO()
    render(view).save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


PLAN_NOTE = (
    "The image is this room drawn from above, from the same JSON: orange pieces are movable and tagged with the "
    f"first {TAG_LENGTH} characters of their id, grey pieces are fixed (darker ones stand on counters), teal areas "
    "are floor a door keeps clear, red circles P1, P2... are the problems at the size of clear space they need, "
    "blue dots are route stops, and grid lines are one metre apart."
)


def with_plan(message: dict) -> dict:
    """A user message with the plan of the room its JSON describes attached before the text."""
    payload = json.loads(message["content"])
    view = payload.get("room", payload)
    return {"role": message["role"], "content": [
        {"type": "image_url", "image_url": {"url": data_url(view)}},
        {"type": "text", "text": f"{PLAN_NOTE}\n\n{message['content']}"},
    ]}
