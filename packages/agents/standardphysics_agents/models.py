"""Every model call in this lane goes through OpenRouter.

One place, so the key, the provider pinning and the zero data retention header
are set once rather than at each call site. A scan is the inside of somebody's
shop, so retention is denied on every request as well as on the account, and
the provider OpenRouter reports comes back on the answer and gets stored.

Weave records each call as the `model.openrouter` op. Its automatic OpenRouter
integration is switched off in `tracing.init`, because the loop opens its model
spans by hand and a patched client would record the same call again. For the
same reason OpenRouter's own Broadcast to Weave setting has to stay off.

Nothing here is required to run. With no key configured the call reports that
and the caller falls back to something local and labelled, the same way the
router does.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from .router.decision import Rejected
from .tracing import traced

API_KEY_ENV = "OPENROUTER_API_KEY"
MODEL_ENV = "OPENROUTER_MODEL"
BASE_URL_ENV = "OPENROUTER_BASE_URL"

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-astra"

PROVIDER_ROUTING = {
    "order": ["openai"],
    "allow_fallbacks": False,
    "data_collection": "deny",
}
"""One backend for the whole demo, and nothing retained.

Pinning the provider keeps a rehearsal and the live run on the same model.
`data_collection: deny` is the per-request half of zero data retention; the
account setting is the other half and a person sets that one.
"""

REQUEST_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class ModelAnswer:
    payload: dict
    provider: str | None
    model: str | None


def _client(api_key: str, base_url: str) -> Any:
    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url=base_url, timeout=REQUEST_TIMEOUT_SECONDS)


class OpenRouter:
    """Structured output, or a reason it could not be had."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        client: Any = None,
    ) -> None:
        self.api_key = api_key or os.environ.get(API_KEY_ENV)
        self.model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
        self.base_url = base_url or os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.api_key) or self._client is not None

    def client(self) -> Any | None:
        if self._client is None and self.api_key:
            self._client = _client(self.api_key, self.base_url)
        return self._client

    @traced("model.openrouter")
    def structured(
        self,
        instruction: str,
        payload: dict,
        schema: dict,
        schema_name: str,
        *,
        max_tokens: int | None = None,
    ) -> ModelAnswer | Rejected:
        """One call, one JSON object shaped by `schema`.

        `max_tokens` caps the answer. Without it OpenRouter reserves the model's
        whole output budget up front, and a nearly empty account refuses a call
        that only needed a sentence.
        """
        if not self.configured:
            return Rejected("openrouter_not_configured")
        try:
            client = self.client()
            if client is None:
                return Rejected("openrouter_not_configured")
            response = client.chat.completions.create(**self._request(
                instruction, payload, schema, schema_name, max_tokens=max_tokens
            ))
        except Exception as error:  # a third party being down decides nothing
            status = getattr(error, "status_code", None)
            return Rejected(f"model_error:{type(error).__name__}" + (f":{status}" if status else ""))
        return self._answer(response)

    def _request(
        self,
        instruction: str,
        payload: dict,
        schema: dict,
        schema_name: str,
        *,
        max_tokens: int | None = None,
    ) -> dict:
        request = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": json.dumps(payload)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
            "extra_body": {"provider": PROVIDER_ROUTING},
        }
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        return request

    def _answer(self, response: Any) -> ModelAnswer | Rejected:
        content = self._content(response)
        if content is None:
            return Rejected("model_returned_nothing")
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return Rejected("model_returned_not_json")
        if not isinstance(parsed, dict):
            return Rejected("model_returned_not_an_object")
        return ModelAnswer(
            payload=parsed,
            provider=getattr(response, "provider", None),
            model=getattr(response, "model", None),
        )

    @staticmethod
    def _content(response: Any) -> str | None:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return None
        message = getattr(choices[0], "message", None)
        return getattr(message, "content", None)
