"""Where the pipeline reports each call it makes to a hosted model, so a process that traces can trace it.

The pipeline sits below the package that owns tracing and can't import it. A tracing process hands `report_to`
its tracer, and every call made through `reported` then appears in the trace under `name`, with `summary` as its
inputs and the reply as its output. Until then `reported` just makes the call. A summary names the model and
counts what was sent; it never holds a key, an address or a photo.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")
Reporter = Callable[[str, dict[str, Any], Callable[[], Any]], Any]


def _unreported(name: str, summary: dict[str, Any], call: Callable[[], T]) -> T:
    return call()


_reporter: Reporter = _unreported


def report_to(reporter: Reporter) -> None:
    """Send every later call made through `reported` to `reporter`."""
    global _reporter
    _reporter = reporter


def reported(name: str, summary: dict[str, Any], call: Callable[[], T]) -> T:
    """`call()`, reported as `name` with `summary` to whatever `report_to` installed."""
    result: T = _reporter(name, summary, call)
    return result


def images_in(messages: list[dict[str, Any]]) -> int:
    """How many images a chat request carries, so a summary can say so without holding them."""
    count = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            count += sum(1 for part in content if isinstance(part, dict) and part.get("type") == "image_url")
    return count
