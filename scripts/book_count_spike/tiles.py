"""Counting a bounded patch instead of a whole photograph.

The first experiment asked for a count across a whole frame and got a spread of
two to one on the same image, with a books-per-metre figure ranging over a factor
of thirty-nine. Most of that came from the model's own guess at how many metres of
shelf were in view, which is the number the architecture says a model must never
supply.

So this asks a smaller question. The frame is cut into tiles, each tile is counted
on its own, and the tiles are counted several times each. Two things come out: is a
count steady when the thing being counted is bounded, and does the sum of the parts
agree with the whole. If small patches are steady, an answer is the engine
multiplying a measured shelf length by a density read off a patch, and the model
never sees a metre.

    .venv/bin/python scripts/book_count_spike/tiles.py <scan directory> <frame>
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
COLUMNS = 3
ROWS = 3

INSTRUCTION = """You are shown a small crop from a photograph of library shelving.

Count the book spines you can see in this crop. Count every spine you can make out
as a separate object, including partial ones at the edges. If there are no books,
answer 0.

Answer with the count and nothing else.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["spines"],
    "properties": {"spines": {"type": "integer"}},
}


def tiles_of(path: pathlib.Path) -> list[tuple[str, bytes]]:
    image = Image.open(path).rotate(-90, expand=True).convert("RGB")
    width, height = image.size
    cut = []
    for row in range(ROWS):
        for column in range(COLUMNS):
            box = (
                column * width // COLUMNS,
                row * height // ROWS,
                (column + 1) * width // COLUMNS,
                (row + 1) * height // ROWS,
            )
            buffer = io.BytesIO()
            image.crop(box).save(buffer, format="JPEG", quality=90)
            cut.append((f"r{row}c{column}", buffer.getvalue()))
    return cut


def count(jpeg: bytes, client, model: str) -> int | None:
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": INSTRUCTION},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": "How many book spines are in this crop?"},
                ]},
            ],
            response_format={"type": "json_schema", "json_schema": {
                "name": "spine_count", "strict": True, "schema": SCHEMA,
            }},
        )
        return int(json.loads(response.choices[0].message.content)["spines"])
    except Exception:
        return None


def main() -> int:
    scan = pathlib.Path(sys.argv[1])
    frame = scan / (sys.argv[2] if len(sys.argv) > 2 else "frame-0132")
    if not frame.exists():
        print(f"{frame} is not there.")
        return 1

    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ["DISCOVERY_API_KEY"],
        base_url=os.environ["DISCOVERY_BASE_URL"],
        timeout=180.0,
    )
    model = os.environ["DISCOVERY_MODEL"]
    cut = tiles_of(frame)
    print(f"{frame.name}, {len(cut)} tiles, {REPEATS} readings each\n")

    work = [(name, jpeg) for name, jpeg in cut for _ in range(REPEATS)]
    with ThreadPoolExecutor(max_workers=9) as pool:
        answers = list(pool.map(lambda item: (item[0], count(item[1], client, model)), work))

    totals: dict[str, list[int]] = {}
    for name, value in answers:
        if value is not None:
            totals.setdefault(name, []).append(value)

    medians = []
    for name, values in sorted(totals.items()):
        low, high = min(values), max(values)
        middle = statistics.median(values)
        medians.append(middle)
        ratio = f"{high / low:.1f}x" if low else "from zero"
        print(f"  {name}: {values} median {middle:.0f}, {ratio}")

    print(f"\nTiles add up to {sum(medians):.0f} spines in this frame.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
