"""Can a model count the books in the stacks, and can the count be scaled?

Nothing in the app can answer "how many books do I have". RoomPlan returns six
boxes it calls storage; the books are in the video and the mesh and in no part of
the scene graph. Before building an answer, two things have to be true, and this
measures both rather than assuming them.

**Is a count repeatable?** The same frame is counted several times. A model that
says 40, then 120, then 75 about one photograph cannot be scaled into anything,
however good the geometry underneath it is.

**Is the density stable?** A frame shows a few metres of one shelf. An answer has
to come from books per metre of shelf, multiplied by the metres of shelf the scan
measured, because no single frame sees the whole room and frames overlap. If the
density is steady across frames the multiplication is sound, and if it swings by
a factor of three it is not.

The model is asked for a count and for what it can see. It is never asked how
many books are in the room: that is the engine's multiplication, and this script
reports what the engine would have to multiply.

    .venv/bin/python scripts/book_count_spike/count.py <scan directory>
"""

from __future__ import annotations

import base64
import io
import json
import os
import pathlib
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

REPEATS = 3
MAX_EDGE = 1400
FRAME_STRIDE = 12

INSTRUCTION = """You are shown one photograph from a walk through a room.

Count what you can actually see. Do not estimate what is out of frame, and do not
guess at the whole room.

- spines: how many book spines are individually distinguishable in this image.
  Count a spine only if you can see it as a separate object. If there are no
  books, this is 0.
- levels: how many distinct horizontal shelf levels carrying books are visible.
- run_metres: the total length of shelf, in metres, that those levels span within
  this image. Judge it against the objects you can see; a library shelf bay is
  usually about 0.9 metres wide.
- confidence: how sure you are of the spine count, from 0 to 1.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["spines", "levels", "run_metres", "confidence"],
    "properties": {
        "spines": {"type": "integer"},
        "levels": {"type": "integer"},
        "run_metres": {"type": "number"},
        "confidence": {"type": "number"},
    },
}


def upright(path: pathlib.Path) -> bytes:
    """The phone stores these sideways, and a sideways shelf is a hard read."""
    image = Image.open(path).rotate(-90, expand=True)
    image.thumbnail((MAX_EDGE, MAX_EDGE))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def look(jpeg: bytes, client, model: str) -> dict | None:
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": INSTRUCTION},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": "Count what is in this photograph."},
                ]},
            ],
            response_format={"type": "json_schema", "json_schema": {
                "name": "shelf_reading", "strict": True, "schema": SCHEMA,
            }},
        )
    except Exception as error:
        print(f"    call failed: {type(error).__name__}", file=sys.stderr)
        return None
    try:
        return json.loads(response.choices[0].message.content)
    except (json.JSONDecodeError, TypeError, AttributeError, IndexError):
        return None


def readings_for(jpeg: bytes, client, model: str) -> list[dict]:
    with ThreadPoolExecutor(max_workers=REPEATS) as pool:
        got = pool.map(lambda _: look(jpeg, client, model), range(REPEATS))
    return [reading for reading in got if reading]


def density(reading: dict) -> float | None:
    """Books per metre of shelf, which is the only thing worth scaling."""
    metres = reading["run_metres"] * max(1, reading["levels"])
    return reading["spines"] / metres if metres > 0 else None


def spread(values: list[float]) -> str:
    if len(values) < 2:
        return "one reading"
    low, high = min(values), max(values)
    middle = statistics.median(values)
    return f"{middle:.0f} (from {low:.0f} to {high:.0f}, {high / low:.1f}x)" if low else "zero"


def main() -> int:
    scan = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    frames = sorted(scan.glob("frame-*"))[::FRAME_STRIDE]
    if not frames:
        print(f"No frames in {scan}. This scan carries no video.")
        return 1

    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ["DISCOVERY_API_KEY"],
        base_url=os.environ["DISCOVERY_BASE_URL"],
        timeout=180.0,
    )
    model = os.environ["DISCOVERY_MODEL"]
    print(f"{len(frames)} frames, {REPEATS} readings each, {model}\n")

    densities: list[float] = []
    for path in frames:
        readings = readings_for(upright(path), client, model)
        if not readings:
            print(f"{path.name}: no reading came back")
            continue
        spines = [float(reading["spines"]) for reading in readings]
        if max(spines) == 0:
            print(f"{path.name}: no books in view")
            continue
        found = [value for value in (density(reading) for reading in readings) if value]
        densities.extend(found)
        levels = statistics.median(reading["levels"] for reading in readings)
        metres = statistics.median(reading["run_metres"] for reading in readings)
        per_metre = f"{statistics.median(found):.0f} per shelf metre" if found else "no run seen"
        print(
            f"{path.name}: spines {spread(spines)}"
            f" | {levels:.0f} levels over {metres:.1f} m | {per_metre}"
        )

    if not densities:
        print("\nNothing countable was found in any frame.")
        return 1
    low, high = min(densities), max(densities)
    print(
        f"\nBooks per shelf metre across every frame: median {statistics.median(densities):.0f},"
        f" from {low:.0f} to {high:.0f} ({high / low:.1f}x)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
