"""Where Astra sends a labelling request: the endpoint, its key and options, and a bounded read of the reply."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

from . import tuned_labeller

API_KEY_ENV = "OPENROUTER_API_KEY"
MODEL_ENV = "LABEL_MODEL"
"""Labelling has its own model setting rather than OPENROUTER_MODEL, which other calls share.

Default: Gemini 3.8 Flash through OpenRouter. Open weights on Fireworks were tried first, as the provider policy
prefers for volume work, and fell short on what the checks read. In a production test on four held-out rooms,
scored against an answer key built from Claude Fable 5.1, GPT-6 Astra, Gemini and adjudication, Gemini made 88 of
90 built-in calls right (the fine-tuned Qwen3.8-27B on Fireworks 81, production Opus 86) and named 84 of 93
objects right (Qwen 85, Opus 91), for about $0.12 a room against Opus's $1.09. Built-in calls decide whether the
solver may move a piece, so they weigh most. DeepSeek v4.1 Flash on Fireworks repeated the phone's category on
every object. An unusable answer falls back to local labels rather than a second model.

LABEL_STATE switches to the fine-tuned Qwen instead (tuned_labeller). A model named accounts/fireworks/... goes to
Fireworks; anything else goes to OpenRouter. OPENROUTER_BASE_URL still overrides the host. gpt-6-astra is retired:
2.5 times Opus's price would push a scan past the $1.50 it is allowed."""
BASE_URL_ENV = "OPENROUTER_BASE_URL"
FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemini-3.8-flash"


FIREWORKS_HOST = "api.fireworks.ai"
OPENROUTER_HOST = "openrouter.ai"
PROVIDER_KEYS = {FIREWORKS_HOST: "FIREWORKS_API_KEY", OPENROUTER_HOST: API_KEY_ENV}
"""The environment variable holding each hosted provider's key, keyed by the chat endpoint's host."""
REASONING_OFF_BY_HOST: dict[str, dict[str, Any]] = {FIREWORKS_HOST: {"reasoning_effort": "none"}}
"""Mirrors discovery/detect.py's host table: hosts that accept turning reasoning off, and how each spells it."""


MAKER_PROVIDERS = {"google": ["google-vertex", "google-ai-studio"], "qwen": ["alibaba"]}
"""OpenRouter names some makers' own endpoints differently from the maker prefix in the model id. Pinning to the
bare prefix matched no endpoint for Gemini and Qwen, so every request 404ed and labelling fell back to the phone."""


def provider_routing(model: str) -> dict:
    """Pin the request to the provider that makes the model, and retain nothing."""
    maker = model.split("/")[0]
    return {"order": MAKER_PROVIDERS.get(maker, [maker]), "allow_fallbacks": False, "data_collection": "deny"}


def base_url() -> str:
    override = os.environ.get(BASE_URL_ENV)
    if override:
        return override.rstrip("/")
    return FIREWORKS_BASE_URL if label_model().startswith("accounts/fireworks/") else OPENROUTER_BASE_URL


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
    if tuned_labeller.configured():
        return tuned_labeller.model_name()
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
