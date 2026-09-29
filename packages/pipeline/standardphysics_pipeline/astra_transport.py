"""Where Astra sends a labelling request: the endpoint, its key and options, and a bounded read of the reply."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

API_KEY_ENV = "OPENROUTER_API_KEY"
MODEL_ENV = "LABEL_MODEL"
"""Labelling has its own model setting rather than OPENROUTER_MODEL, which other calls share. On a real Share
Tea scan, DeepSeek v4.1 Flash on Fireworks matched Opus on the counters that mattered: both 35.7 in RoomPlan
boxes came back not movable and both 44 in bar-counter tables came back "Table" and not movable, for $0.04
against Opus's $0.73. It did not tell bar stools from chairs, calling both "Chair"; movability still came back
right. An unusable answer falls back to local labels rather than a second model: Kimi K3 ran to the
wall-clock deadline on the same scan before finishing every batch, at five times DeepSeek's cost. Set
LABEL_MODEL to use another model, including anthropic/claude-opus-5.5 through OPENROUTER_API_KEY and
OPENROUTER_BASE_URL. gpt-6-astra is retired: 2.5 times Opus's price would push a scan past the $1.50 it is
allowed."""
BASE_URL_ENV = "OPENROUTER_BASE_URL"
DEFAULT_BASE_URL = "https://api.fireworks.ai/inference/v1"
DEFAULT_MODEL = "accounts/fireworks/models/deepseek-v4p1-flash"


FIREWORKS_HOST = "api.fireworks.ai"
OPENROUTER_HOST = "openrouter.ai"
PROVIDER_KEYS = {FIREWORKS_HOST: "FIREWORKS_API_KEY", OPENROUTER_HOST: API_KEY_ENV}
"""The environment variable holding each hosted provider's key, keyed by the chat endpoint's host."""
REASONING_OFF_BY_HOST: dict[str, dict[str, Any]] = {FIREWORKS_HOST: {"reasoning_effort": "none"}}
"""Mirrors discovery/detect.py's host table: hosts that accept turning reasoning off, and how each spells it."""


def provider_routing(model: str) -> dict:
    """Pin the request to the provider that makes the model, and retain nothing."""
    return {"order": [model.split("/")[0]], "allow_fallbacks": False, "data_collection": "deny"}


def base_url() -> str:
    return (os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL).rstrip("/")


def endpoint_host(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or ""


def api_key_env() -> str:
    return PROVIDER_KEYS.get(endpoint_host(base_url()), API_KEY_ENV)


def request_options(model: str, host: str) -> dict[str, Any]:
    """Fields only the host in use accepts: OpenRouter's routing and usage reporting, or reasoning turned off."""
    if host == OPENROUTER_HOST:
        return {"reasoning": {"effort": "low"}, "provider": provider_routing(model), "usage": {"include": True}}
    return dict(REASONING_OFF_BY_HOST.get(host, {}))


def label_model() -> str:
    return os.environ.get(MODEL_ENV) or DEFAULT_MODEL


REQUEST_TIMEOUT_SECONDS = 120.0
MAX_RESPONSE_BYTES = 2_000_000
Transport = Callable[[str, dict, dict[str, str]], dict]


def chat_url() -> str:
    return f"{base_url()}/chat/completions"


def chat_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def openrouter_post(url: str, body: dict, headers: dict[str, str], *, deadline: float | None = None) -> dict:
    deadline = min(deadline or float("inf"), time.monotonic() + REQUEST_TIMEOUT_SECONDS)
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("openrouter_request_deadline")
    with urllib.request.urlopen(request, timeout=remaining) as response:
        raw = read_response(response, deadline)
    if time.monotonic() > deadline:
        raise TimeoutError("openrouter_request_deadline")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("openrouter_not_an_object")
    return parsed


def read_response(response: Any, deadline: float) -> bytes:
    """Read a response under one deadline, including slow heartbeat chunks."""
    chunks: list[bytes] = []
    total = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("openrouter_response_deadline")
        _set_response_timeout(response, remaining)
        chunk = response.read1(min(64 * 1024, MAX_RESPONSE_BYTES - total + 1))
        if not chunk:
            break
        if not isinstance(chunk, (bytes, bytearray)):
            raise ValueError("openrouter_response_not_bytes")
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ValueError("openrouter_response_too_large")
        chunks.append(bytes(chunk))
    return b"".join(chunks)


def _set_response_timeout(response: Any, seconds: float) -> None:
    stream = getattr(response, "fp", None)
    raw = getattr(stream, "raw", None)
    socket = getattr(raw, "_sock", None) or getattr(stream, "_sock", None)
    setter = getattr(socket, "settimeout", None)
    if callable(setter):
        setter(max(0.001, seconds))
