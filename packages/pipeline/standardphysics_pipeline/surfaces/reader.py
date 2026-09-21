"""The one thing a model is asked to do here: count what is in a small picture.

It is given a crop and a word, and it answers with an integer. It is never told
how big the crop is, never asked how much of the room it covers, and never asked
for a total. Those are measurements, and they come from the scan.

With nothing configured this returns a counter that refuses, rather than one that
guesses. A count nobody took is not a count.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

INSTRUCTION = """You are shown a small crop of a photograph taken inside a room.

Count how many of the named thing are visible in this crop. Count each one you can
make out as a separate object, including ones partly cut off at the edge. If none
are visible, answer 0. Do not guess at what might be outside the crop.

Answer with the count and how sure you are.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["count", "confidence"],
    "properties": {
        "count": {"type": "integer", "minimum": 0},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

API_KEY_ENV = "DISCOVERY_API_KEY"
BASE_URL_ENV = "DISCOVERY_BASE_URL"
MODEL_ENV = "DISCOVERY_MODEL"
REQUEST_TIMEOUT_SECONDS = 120.0
LEAST_CONFIDENCE = 0.2
"""Below this the model is saying it could not tell, and a shrug is not a zero."""


class NoCounterConfigured(RuntimeError):
    """Raised rather than handing back something that invents counts."""


def counter(client: Any = None, model: str | None = None):
    """A callable that counts one crop, or a refusal to make one."""
    model = model or os.environ.get(MODEL_ENV)
    client = client or _client()
    if client is None or not model:
        raise NoCounterConfigured(
            f"Set {API_KEY_ENV}, {BASE_URL_ENV} and {MODEL_ENV} to count from photographs."
        )

    def count(jpeg: bytes, thing: str) -> int | None:
        return _count(client, model, jpeg, thing)

    return count


def _client() -> Any | None:
    key, base_url = os.environ.get(API_KEY_ENV), os.environ.get(BASE_URL_ENV)
    if not key or not base_url:
        return None
    from openai import OpenAI

    return OpenAI(api_key=key, base_url=base_url, timeout=REQUEST_TIMEOUT_SECONDS)


def _count(client: Any, model: str, jpeg: bytes, thing: str) -> int | None:
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": INSTRUCTION},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": f"How many {thing} are visible in this crop?"},
                    ],
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "visible_count", "strict": True, "schema": SCHEMA},
            },
        )
    except Exception:  # a provider being down is not a count of zero
        return None
    return _read(response)


def _read(response: Any) -> int | None:
    try:
        payload = json.loads(response.choices[0].message.content)
    except (json.JSONDecodeError, TypeError, AttributeError, IndexError):
        return None
    if float(payload.get("confidence", 0)) < LEAST_CONFIDENCE:
        return None
    return int(payload["count"])
