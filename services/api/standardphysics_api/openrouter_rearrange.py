"""The rearrangement model through the repository's zero-retention OpenRouter client."""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from standardphysics_agents.models import OpenRouter, provider_routing

from .fireworks import ModelFailed, Sampling

PRICE_URL = "https://openrouter.ai/api/v1/models"


class BudgetReached(Exception):
    """The next model request could exceed the per-click limit."""


def model_price(model: str) -> tuple[float, float]:
    """Published USD per token, loaded once per job before any billable request."""
    try:
        with urllib.request.urlopen(PRICE_URL, timeout=30) as response:
            listing = json.load(response)["data"]
        pricing = next(row["pricing"] for row in listing if row["id"] == model)
        return float(pricing["prompt"]), float(pricing["completion"])
    except (urllib.error.URLError, KeyError, StopIteration, TypeError, ValueError) as error:
        raise ModelFailed("We couldn't check the model's price. Try again in a minute.") from error


@dataclass
class OpenRouterRearrange:
    api_key: str = field(repr=False)
    model: str
    reasoning_effort: str
    token_cap: int
    cost_cap_dollars: float
    price: Callable[[str], tuple[float, float]] = model_price
    router: OpenRouter | None = field(default=None, repr=False)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_dollars: float = 0.0
    calls: int = 0
    controls_deployment: bool = False
    _prices: tuple[float, float] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.router is None:
            self.router = OpenRouter(api_key=self.api_key, model=self.model, timeout=120.0)

    def reset_job(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_dollars = 0.0
        self.calls = 0

    def _price(self) -> tuple[float, float]:
        if self._prices is None:
            self._prices = self.price(self.model)
        return self._prices

    def _client(self) -> Any:
        client = self.router.client() if self.router is not None else None
        if client is None:
            raise ValueError("OpenRouter has no API key")
        return client

    def _reserve(self, messages: list[dict]) -> None:
        prompt_price, completion_price = self._price()
        # UTF-8 byte count is a conservative upper bound for text tokens.
        prompt_limit = sum(len(message["content"].encode("utf-8")) for message in messages)
        worst = prompt_limit * prompt_price + self.token_cap * completion_price
        if self.cost_dollars + worst > self.cost_cap_dollars:
            raise BudgetReached()

    def complete(self, messages: list[dict], sampling: Sampling) -> list[str]:
        self._reserve(messages)
        try:
            response = self._client().chat.completions.create(
                model=self.model, messages=messages, temperature=sampling.temperature,
                max_tokens=self.token_cap,
                extra_body={"provider": provider_routing(self.model), "usage": {"include": True},
                            "reasoning": {"effort": self.reasoning_effort}},
            )
        except Exception as error:
            raise ModelFailed("We couldn't reach the model. Try again in a minute.") from error
        self._record(response, messages)
        choices = getattr(response, "choices", None) or []
        return [getattr(choice.message, "content", None) or "" for choice in choices[:1]]

    def _record(self, response: Any, messages: list[dict]) -> None:
        usage = getattr(response, "usage", None)
        choices = getattr(response, "choices", None) or []
        answer = getattr(choices[0].message, "content", "") if choices else ""
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        if prompt == 0:
            prompt = max(1, sum(len(item["content"].encode("utf-8")) for item in messages))
        if completion == 0:
            completion = max(1, len((answer or "").encode("utf-8")))
        quoted = getattr(usage, "cost", None)
        prompt_price, completion_price = self._price()
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        estimated = prompt * prompt_price + completion * completion_price
        try:
            reported = float(quoted) if quoted is not None else float("nan")
        except (TypeError, ValueError):
            reported = float("nan")
        self.cost_dollars += reported if math.isfinite(reported) and reported >= 0 else estimated
        self.calls += 1

    def allow_one_replica(self) -> None:
        return None

    def scale_to_zero(self) -> None:
        return None


@dataclass
class FakeOpenRouter:
    answer: Callable[[list[dict]], list[str]]
    model: str = "anthropic/claude-opus-5.5"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_dollars: float = 0.0
    calls: int = 0
    controls_deployment: bool = False

    def reset_job(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_dollars = 0.0
        self.calls = 0

    def complete(self, messages: list[dict], sampling: Sampling) -> list[str]:
        self.calls += 1
        return self.answer(messages)[:1]

    def allow_one_replica(self) -> None:
        return None

    def scale_to_zero(self) -> None:
        return None
