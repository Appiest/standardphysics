from __future__ import annotations

import json
import os
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class DraftModel(Protocol):
    def complete(self, prompt: str) -> str: ...


class OpenAICompatibleModel:
    """Minimal client for OpenAI-compatible chat completion endpoints."""

    def __init__(self, model: str, base_url: str | None = None, api_key: str | None = None) -> None:
        self.model = model
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required when using the openai model")

    def complete(self, prompt: str) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            }
        ).encode()
        request = Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=60) as response:
                payload = json.load(response)
        except HTTPError as error:
            raise RuntimeError(f"model endpoint rejected the request: HTTP {error.code}") from error
        except URLError as error:
            raise RuntimeError(f"could not reach model endpoint: {error.reason}") from error
        try:
            return payload["choices"][0]["message"]["content"].strip()
        except (IndexError, KeyError, TypeError) as error:
            raise RuntimeError("model endpoint returned no assistant content") from error


class ScriptedDemoModel:
    """Credential-free demo model that intentionally needs one repair cycle."""

    def complete(self, prompt: str) -> str:
        if "REVISION REQUEST" in prompt:
            return self._complete_draft(include_safety=True)
        return self._complete_draft(include_safety=False)

    @staticmethod
    def _complete_draft(include_safety: bool) -> str:
        sections = [
            ("Problem", "Teams lose confidence in autonomous agents when failures disappear instead of becoming evidence for the next attempt."),
            ("Loop Design", "A builder drafts an artifact, a deterministic gate checks explicit requirements, and a critic returns only actionable gaps. The builder repairs the draft, while the engine retains the best-scoring version and stops at a fixed budget."),
            ("Observability", "Every prompt, candidate score, feedback item, and termination reason is emitted as a timestamped JSONL trace. The same events can be forwarded to Weave during the demo."),
            ("Evaluation", "The demo evaluates section coverage and minimum evidence depth. Acceptance requires every rubric item, and regression is impossible because lower-scoring candidates are retained only as trace evidence."),
            ("Demo", "Run the included brief with the local scripted model to show a failed first draft, its precise critique, and the repaired passing result in under a minute."),
        ]
        if include_safety:
            sections.insert(3, ("Safety", "The loop has a hard iteration cap, keeps append-only local traces, and never executes generated commands or writes to user projects. Human review remains the final publication gate."))
        return "\n\n".join(f"## {heading}\n{body}" for heading, body in sections)

