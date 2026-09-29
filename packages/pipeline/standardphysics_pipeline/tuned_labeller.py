"""The fine-tuned labeller: Qwen3.8-27B with a LoRA trained on Opus's answers, served from Fireworks' Serverless
Training pool at token prices with no idle cost.

The pool samples a checkpoint only while a training session holds it, so a labelling run opens a session from the
saved state, exports a sampling checkpoint, and sends plain completions requests: the chat rendered in Qwen's
template with thinking switched off, and the photos as data URLs. The transport takes astra's chat body and returns
a chat-shaped payload, so astra's parsing and every check on the answer stay the same.

Trained on Opus answers corrected to an answer key (Claude Fable 5.1, GPT-6 Astra, Gemini 3.8 Flash, adjudicated),
public kiosk, terminal and seating photos, and several views of every real scan. In cross-validation it named 82 of
93 held-out objects right against production Opus's 91, level with Opus in rooms whose furniture it had seen.
"""

from __future__ import annotations

import base64
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

STATE_ENV = "LABEL_STATE"
"""Saved training state to serve, as account/run/name, e.g. amelia-team/run-6800718d223f45ec85e0eb34ead5a0dd/epoch-1."""
KEY_ENV = "FIREWORKS_API_KEY"
POOL_URL = "https://api.fireworks.ai/training/v1/serverless"
BASE_MODEL = "accounts/fireworks/models/qwen3p8-27b"
LORA_CONFIG = {"rank": 16, "alpha": 32, "train_unembed": True, "train_mlp": True, "train_attn": True}
IMAGE_SIDE = 640
"""The adapter was trained on photos shrunk to 640 px on the long side; serve it the same."""
MAX_ANSWER_TOKENS = 1200
REQUEST_TIMEOUT_SECONDS = 120.0
SETUP_TIMEOUT_SECONDS = 90.0
HEARTBEAT_SECONDS = 30.0
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
"""408 is the pool's 'not finished yet'; the rest are transient."""
IMAGE_TOKEN = "<|vision_start|><|image_pad|><|vision_end|>"


class PoolError(ValueError):
    """The pool answered in a way the labeller cannot use. A ValueError, so astra falls back to the phone's labels."""


class SessionGone(PoolError):
    """The pool no longer holds the session behind the sampling checkpoint."""


def configured() -> bool:
    return bool(os.environ.get(STATE_ENV) and os.environ.get(KEY_ENV))


def model_name() -> str:
    return f"tuned:{os.environ.get(STATE_ENV, '')}"


def render_prompt(messages: list[dict[str, Any]]) -> str:
    """Qwen3.8's chat template with thinking disabled, one image placeholder per photo, matching training."""
    parts = []
    for message in messages:
        parts.append(f"<|im_start|>{message['role']}\n{_text_of(message.get('content'))}<|im_end|>\n")
    return "".join(parts) + "<|im_start|>assistant\n<think>\n\n</think>\n\n"


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    pieces = []
    for part in content or []:
        if part.get("type") == "text":
            pieces.append(part.get("text", ""))
        elif part.get("type") in {"image", "image_url"}:
            pieces.append(IMAGE_TOKEN)
    return "".join(pieces)


def image_urls(messages: list[dict[str, Any]]) -> list[str]:
    urls = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            urls += [part["image_url"]["url"] for part in content if part.get("type") == "image_url"]
    return [shrink(url) for url in urls]


def shrink(url: str) -> str:
    from PIL import Image

    header, data = url.split(",", 1)
    with Image.open(io.BytesIO(base64.b64decode(data))) as image:
        if max(image.size) <= IMAGE_SIDE:
            return url
        image = image.convert("RGB")
        image.thumbnail((IMAGE_SIDE, IMAGE_SIDE))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _post(path: str, payload: dict, api_key: str, timeout: float) -> dict:
    request = urllib.request.Request(
        f"{POOL_URL}{path}", data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {api_key}", "X-Api-Key": api_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise SessionGone(error.read()[:200].decode(errors="replace")) from error
        raise


def _post_retrying(path: str, payload: dict, api_key: str, deadline: float) -> dict:
    while True:
        try:
            return _post(path, payload, api_key, timeout=max(1.0, deadline - time.monotonic()))
        except urllib.error.HTTPError as error:
            if error.code not in RETRY_STATUSES or time.monotonic() >= deadline:
                raise
            time.sleep(1.0)


class Session:
    """One training session holding the saved adapter, and the sampling checkpoint exported from it."""

    def __init__(self, state: str, api_key: str):
        self.state, self.api_key = state, api_key
        self.session_id = ""
        self.sampling_model = ""
        self.beat_at = 0.0
        self.lock = threading.Lock()

    def model(self) -> str:
        with self.lock:
            if not self.sampling_model:
                self._open()
            elif time.monotonic() - self.beat_at > HEARTBEAT_SECONDS:
                self._heartbeat()
            return self.sampling_model

    def reset(self) -> None:
        with self.lock:
            self.sampling_model = ""

    def _job(self, path: str, payload: dict, deadline: float, wanted: str) -> Any:
        """Start a pool job and poll it until its result carries `wanted`; under load it can answer before then."""
        started = _post_retrying(path, payload, self.api_key, deadline)
        if "request_id" not in started:
            raise PoolError(f"{path} returned no job: {str(started)[:200]}")
        while True:
            result = _post_retrying("/api/v1/retrieve_future", {"request_id": started["request_id"],
                                                                "allow_metadata_only": True}, self.api_key, deadline)
            if "error" in result:
                raise PoolError(f"{path} failed: {str(result['error'])[:200]}")
            if result.get(wanted):
                return result[wanted]
            if time.monotonic() >= deadline:
                raise PoolError(f"{path} never returned {wanted}")
            time.sleep(1.0)

    def _open(self) -> None:
        deadline = time.monotonic() + SETUP_TIMEOUT_SECONDS
        opened = _post_retrying("/api/v1/create_session", {"tags": [], "user_metadata": {},
                                "type": "create_session"}, self.api_key, deadline)
        if not opened.get("session_id"):
            raise PoolError(f"create_session returned no session: {str(opened)[:200]}")
        self.session_id = opened["session_id"]
        model_id = self._job("/api/v1/create_model", {"session_id": self.session_id, "model_seq_id": 0,
                             "base_model": BASE_MODEL, "lora_config": LORA_CONFIG, "type": "create_model"},
                             deadline, "model_id")
        self._job("/api/v1/load_weights", {"model_id": model_id, "path": self.state, "optimizer": False,
                  "seq_id": 1, "type": "load_weights"}, deadline, "path")
        exported = self._job("/api/v1/save_weights_for_sampler", {"model_id": model_id, "path": "labeller",
                             "seq_id": 2, "type": "save_weights_for_sampler", "checkpoint_type": "base"},
                             deadline, "path")
        account = exported.split("/", 1)[0]
        self.sampling_model = f"accounts/{account}/trainingSessions/{self.session_id}/checkpoints/{exported}"
        self.beat_at = time.monotonic()

    def _heartbeat(self) -> None:
        _post_retrying("/api/v1/session_heartbeat", {"session_id": self.session_id, "type": "session_heartbeat"},
                       self.api_key, time.monotonic() + 10.0)
        self.beat_at = time.monotonic()


_session: Session | None = None
_session_lock = threading.Lock()


def session() -> Session:
    global _session
    state, api_key = os.environ[STATE_ENV], os.environ[KEY_ENV]
    with _session_lock:
        if _session is None or _session.state != state or _session.api_key != api_key:
            _session = Session(state, api_key)
        return _session


def transport(url: str, body: dict, headers: dict[str, str]) -> dict:
    """Astra's chat body in, a chat-shaped payload out; one session reopen when the pool has dropped it."""
    held = session()
    try:
        return _complete(held, body)
    except SessionGone:
        held.reset()
        return _complete(held, body)


def _complete(held: Session, body: dict) -> dict:
    messages = body["messages"]
    payload = {
        "model": held.model(), "prompt": render_prompt(messages), "images": image_urls(messages),
        "max_tokens": min(int(body.get("max_tokens") or MAX_ANSWER_TOKENS), MAX_ANSWER_TOKENS),
        "temperature": 0.0, "stream": False,
    }
    answer = _post_retrying("/inference/v1/completions", payload, held.api_key,
                            time.monotonic() + REQUEST_TIMEOUT_SECONDS)
    text = (answer.get("choices") or [{}])[0].get("text", "")
    return {"choices": [{"message": {"content": text}}], "usage": answer.get("usage")}
