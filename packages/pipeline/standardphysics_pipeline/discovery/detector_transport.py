"""Asking the vision model: which endpoint and key, how long to wait, when to retry, and how many requests run at once."""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from typing import Any, Callable

from ..model_calls import images_in, reported
from .detection_errors import (
    DetectionAuthError,
    DetectionError,
    DetectionRateLimited,
    DetectionSchemaError,
    DetectionTransientError,
)

log = logging.getLogger(__name__)


MODEL_ENV = "DISCOVERY_MODEL"
API_KEY_ENV = "DISCOVERY_API_KEY"
BASE_URL_ENV = "DISCOVERY_BASE_URL"
FALLBACK_KEY_ENV = "OPENROUTER_API_KEY"
FALLBACK_BASE_URL_ENV = "OPENROUTER_BASE_URL"
DEFAULT_MODEL = "google/gemini-3.8-flash"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_HOST = "openrouter.ai"
FIREWORKS_HOST = "api.fireworks.ai"
HOST_KEYS = {FIREWORKS_HOST: "FIREWORKS_API_KEY", OPENROUTER_HOST: FALLBACK_KEY_ENV}
"""Each host's usual key, used when DISCOVERY_API_KEY is unset, so a Fireworks endpoint never gets the OpenRouter key."""
HOST_DEFAULT_MODELS = {FIREWORKS_HOST: "accounts/fireworks/models/deepseek-v4p1-flash"}
"""The model a host serves when DISCOVERY_MODEL is blank; DEFAULT_MODEL is an OpenRouter name Fireworks answers 404 to."""
PROVIDER_ROUTING = {"data_collection": "deny"}
"""OpenRouter's own routing rules. Every other host rejects or ignores them, so
they travel only when the request is going to OpenRouter."""
REASONING_OFF_BY_HOST: dict[str, dict[str, Any]] = {"api.fireworks.ai": {"reasoning_effort": "none"}}
"""Hosts that accept turning the model's reasoning off, and how each spells it.

OpenRouter is absent on purpose: its default Gemini answers HTTP 400
"Reasoning is mandatory for this endpoint" to `reasoning_effort`, to
`reasoning.effort` and to `reasoning.enabled`, and a 400 is never retried, so
sending it there would lose every frame."""
REQUEST_TIMEOUT_SECONDS = 120.0
MAX_ATTEMPTS = 4
RATE_LIMITED_ATTEMPTS = 8
FIRST_BACKOFF_SECONDS = 1.5
BACKOFF_GROWTH = 2.5
MAX_RATE_LIMIT_WAIT = 60.0


MAX_RESPONSE_BYTES = 4_000_000


Transport = Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]]


class RateLimitGate:
    """One pause shared by every request in flight.

    When one request is told to slow down, the budget is spent for all of them:
    a thread that keeps asking only spends its own attempts on 429s. So a rate
    limit holds the gate, and every request waits at it before it is sent.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._open_at = 0.0

    def hold(self, seconds: float) -> None:
        with self._lock:
            self._open_at = max(self._open_at, time.monotonic() + seconds)

    def wait(self) -> None:
        with self._lock:
            delay = self._open_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)


RATE_LIMIT_GATE = RateLimitGate()

DETECTOR_SLOTS_IN_FLIGHT = 16
"""Detector requests in flight across the whole process, whichever scan asked.

The account's generated-token budget is shared by every scan, so the cap has to
be too: sixteen at once is what one walk reads at without a rate limit (see
`discover.DETECTION_WORKERS`)."""


class DetectorSlots:
    """A process-wide cap on requests in flight, where an urgent request always goes first.

    Discovery for a walk that has ended is urgent: someone is waiting for it.
    Reading photos while a walk is still going on is not, so it only takes a
    slot no urgent request is waiting for, and a finished walk is never slowed
    down by someone else's walk that is still in progress.
    """

    def __init__(self, size: int = DETECTOR_SLOTS_IN_FLIGHT) -> None:
        self._free = size
        self._urgent_waiting = 0
        self._changed = threading.Condition()

    def acquire(self, urgent: bool) -> None:
        with self._changed:
            if urgent:
                self._urgent_waiting += 1
                self._changed.wait_for(lambda: self._free > 0)
                self._urgent_waiting -= 1
            else:
                self._changed.wait_for(lambda: self._free > 0 and not self._urgent_waiting)
            self._free -= 1

    def release(self) -> None:
        with self._changed:
            self._free += 1
            self._changed.notify_all()


DETECTOR_SLOTS = DetectorSlots()


def model_answer(transport: Transport | None, body: dict[str, Any], api_key: str, urgent: bool = True) -> dict[str, Any]:
    """The model's reply, asked for again after a blip or a rate limit until the attempts run out, and reported as
    one model call however many attempts it took."""
    summary = {"model": body.get("model"), "host": endpoint_host(), "images": images_in(body.get("messages", [])),
               "urgent": urgent}
    return reported("model.detect", summary, lambda: _answer_with_retries(transport, body, api_key, urgent))


def _answer_with_retries(transport: Transport | None, body: dict[str, Any], api_key: str, urgent: bool) -> dict[str, Any]:
    attempt = 0
    while True:
        attempt += 1
        RATE_LIMIT_GATE.wait()
        try:
            return _post_in_a_slot(transport, body, api_key, urgent)
        except DetectionRateLimited as error:
            if attempt >= RATE_LIMITED_ATTEMPTS:
                raise
            wait = min(MAX_RATE_LIMIT_WAIT, error.retry_after or _backoff(attempt))
            log.info("rate limited on attempt %d, holding every request %.1fs (host asked %s)",
                     attempt, wait, error.retry_after)
            RATE_LIMIT_GATE.hold(wait)
        except (DetectionAuthError, DetectionSchemaError):
            raise
        except DetectionError:
            if attempt >= MAX_ATTEMPTS:
                raise
            time.sleep(_backoff(attempt))


def _post_in_a_slot(transport: Transport | None, body: dict[str, Any], api_key: str, urgent: bool) -> dict[str, Any]:
    DETECTOR_SLOTS.acquire(urgent)
    try:
        return _post(transport, body, api_key)
    finally:
        DETECTOR_SLOTS.release()


def _backoff(attempt: int) -> float:
    """A widening wait, spread out so retries from many frames do not land together."""
    return FIRST_BACKOFF_SECONDS * (BACKOFF_GROWTH ** (attempt - 1)) * (0.5 + random.random())


def configured_api_key() -> str:
    return os.environ.get(API_KEY_ENV) or os.environ.get(HOST_KEYS.get(endpoint_host(), FALLBACK_KEY_ENV)) or ""


def base_url() -> str:
    configured = os.environ.get(BASE_URL_ENV) or os.environ.get(FALLBACK_BASE_URL_ENV)
    return (configured or DEFAULT_BASE_URL).rstrip("/")


def endpoint_host() -> str:
    return urllib.parse.urlsplit(base_url()).hostname or "unknown"


def request_options() -> dict[str, Any]:
    """Host-specific request fields: OpenRouter's routing rules, or reasoning turned off."""
    host = endpoint_host()
    if host == OPENROUTER_HOST:
        return {"provider": PROVIDER_ROUTING}
    return dict(REASONING_OFF_BY_HOST.get(host, {}))


def answer_model() -> str:
    return os.environ.get(MODEL_ENV) or HOST_DEFAULT_MODELS.get(endpoint_host(), DEFAULT_MODEL)


def answer_identity() -> str:
    """What decides the answer besides the photo: the model, and whether it was allowed to reason.

    The cache is keyed by this, so an answer given with reasoning on is never
    served as one given with it off.
    """
    model = answer_model()
    reasoning = REASONING_OFF_BY_HOST.get(endpoint_host())
    return model if not reasoning else f"{model}|{json.dumps(reasoning, sort_keys=True)}"


def _post(transport: Transport | None, body: dict[str, Any], api_key: str) -> dict[str, Any]:
    url = f"{base_url()}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        return (transport or _openrouter_post)(url, body, headers)
    except DetectionError:
        raise
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise DetectionAuthError(f"the vision model rejected authentication: HTTP {error.code}") from error
        if error.code in (400, 422):
            raise DetectionSchemaError(f"the vision model rejected request schema: HTTP {error.code}") from error
        if error.code == 429:
            raise DetectionRateLimited(
                "the vision model is rate limited: HTTP 429", _retry_after(error.headers)
            ) from error
        raise DetectionTransientError(f"the vision model had a transient HTTP error: HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise DetectionTransientError(f"the vision model timed out or network failed: {error}") from error
    except (OSError, ValueError) as error:
        raise DetectionError(f"the vision model did not answer: {error}") from error


def _retry_after(headers: Any) -> float | None:
    """The host's Retry-After, in seconds or as a date, or nothing when it did not say."""
    value = headers.get("Retry-After") if headers is not None else None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def _openrouter_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read(MAX_RESPONSE_BYTES))
