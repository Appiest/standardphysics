"""Every call the API makes to Fireworks: asking the rearrangement model, and starting or stopping its deployment.

The model is a fine-tuned LoRA served by an on-demand deployment, which is
billed per GPU-second while a replica runs and nothing while it is at zero.
So a suggestion lets the deployment run one replica (`allow_one_replica`),
asks, and later lets it fall back to zero (`scale_to_zero`). Both are PATCHes
of the deployment's replica bounds:

    PATCH https://api.fireworks.ai/v1/accounts/<account>/deployments/<id>
    {"minReplicaCount": 0, "maxReplicaCount": 1}    may run
    {"minReplicaCount": 0, "maxReplicaCount": 0}    scaled to zero

A request to a deployment at zero is not queued. Fireworks answers 503 with
the code DEPLOYMENT_SCALING_UP and starts a replica, which for a 27B model
takes minutes; `complete` turns that answer into `ModelWarming` so the caller
can wait and ask again.

The API key is sent in a header and never logged or put in an error message.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Protocol

CHAT_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
CONTROL_URL = "https://api.fireworks.ai/v1"
SCALING_UP = "DEPLOYMENT_SCALING_UP"
CONTROL_TIMEOUT_SECONDS = 30.0

Transport = Callable[[str, str, dict, dict, float], tuple[int, dict]]
"""(method, url, json body, headers, timeout seconds) -> (HTTP status, parsed JSON body)."""


class ModelWarming(Exception):
    """The deployment is at zero and Fireworks has started a replica; ask again in a while."""


class ModelFailed(Exception):
    """The model could not answer. The message is written for the owner."""


@dataclass(frozen=True)
class Sampling:
    """How the training evaluation sampled the model (`serverless_train.Plan`): four answers at 0.7."""

    attempts: int = 4
    temperature: float = 0.7
    max_tokens: int = 512


class RearrangeModel(Protocol):
    controls_deployment: bool

    def complete(self, messages: list[dict], sampling: Sampling) -> list[str]: ...

    def allow_one_replica(self) -> None: ...

    def scale_to_zero(self) -> None: ...


def urllib_transport(method: str, url: str, body: dict, headers: dict, timeout: float) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method=method,
                                     headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, _json_or_empty(error.read())
    except (TimeoutError, socket.timeout) as error:
        raise ModelFailed("The model took too long to answer. Try again in a minute.") from error
    except urllib.error.URLError as error:
        raise ModelFailed("We couldn't reach the model. Try again in a minute.") from error


def _json_or_empty(raw: bytes) -> dict:
    try:
        parsed = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _error_code(body: dict) -> str | None:
    error = body.get("error")
    return error.get("code") if isinstance(error, dict) else None


def deployment_name(deployment: str, model: str) -> str:
    """The deployment's full resource name; a bare id borrows the account from the model's name."""
    if deployment.startswith("accounts/"):
        return deployment
    parts = model.split("/")
    if len(parts) < 2 or parts[0] != "accounts":
        raise ValueError("SP_REARRANGE_DEPLOYMENT needs to be accounts/<account>/deployments/<id>")
    return f"accounts/{parts[1]}/deployments/{deployment}"


@dataclass
class FireworksModel:
    api_key: str = field(repr=False)
    model: str
    deployment: str | None = None
    timeout_seconds: float = 300.0
    transport: Transport = urllib_transport

    @property
    def controls_deployment(self) -> bool:
        return self.deployment is not None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def complete(self, messages: list[dict], sampling: Sampling) -> list[str]:
        body = {"model": self.model, "messages": messages, "n": sampling.attempts,
                "temperature": sampling.temperature, "max_tokens": sampling.max_tokens}
        status, reply = self.transport("POST", CHAT_URL, body, self._headers(), self.timeout_seconds)
        if status == 503 and _error_code(reply) == SCALING_UP:
            raise ModelWarming()
        if status != 200:
            raise ModelFailed(f"The model answered with an error (HTTP {status}). Try again in a minute.")
        return [str((choice.get("message") or {}).get("content") or "") for choice in reply.get("choices", [])]

    def _set_replicas(self, maximum: int) -> None:
        name = deployment_name(self.deployment or "", self.model)
        body = {"minReplicaCount": 0, "maxReplicaCount": maximum}
        status, _ = self.transport("PATCH", f"{CONTROL_URL}/{name}", body, self._headers(), CONTROL_TIMEOUT_SECONDS)
        if status != 200:
            raise ModelFailed(f"Fireworks wouldn't change the deployment (HTTP {status}).")

    def allow_one_replica(self) -> None:
        self._set_replicas(1)

    def scale_to_zero(self) -> None:
        self._set_replicas(0)


@dataclass
class FakeFireworks:
    """A stand-in with no network, for tests and for SP_REARRANGE_FAKE_MODEL in development.

    `answer` turns the prompt into completions. The first `warmups` requests
    get `ModelWarming`, as a deployment at zero would answer. Every call is
    recorded in `calls` so a test can read the order things happened in.
    """

    answer: Callable[[list[dict]], list[str]]
    warmups: int = 0
    controls_deployment: bool = True
    failure: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def complete(self, messages: list[dict], sampling: Sampling) -> list[str]:
        self.calls.append("complete")
        if self.warmups > 0:
            self.warmups -= 1
            raise ModelWarming()
        if self.failure is not None:
            raise self.failure
        return self.answer(messages)[: sampling.attempts]

    def allow_one_replica(self) -> None:
        self.calls.append("allow_one_replica")

    def scale_to_zero(self) -> None:
        self.calls.append("scale_to_zero")
