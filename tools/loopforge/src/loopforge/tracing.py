from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


class TraceSink(Protocol):
    def record(self, event: str, **fields: object) -> None: ...


class JsonlTraceSink:
    """Append-only local traces that work without third-party accounts or SDKs."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path

    def record(self, event: str, **fields: object) -> None:
        payload = {
            "at": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as trace:
            trace.write(json.dumps(payload, sort_keys=True) + "\n")


class FanoutTraceSink:
    def __init__(self, *sinks: TraceSink) -> None:
        self.sinks = sinks

    def record(self, event: str, **fields: object) -> None:
        for sink in self.sinks:
            sink.record(event, **fields)


class WeaveTraceSink:
    """Optional W&B Weave call tracing, initialized only when explicitly requested."""

    def __init__(self, project: str) -> None:
        try:
            import weave
        except ImportError as error:
            raise RuntimeError("Weave tracing requires: python -m pip install -e '.[observability]'") from error
        weave.init(project)

        @weave.op()
        def capture(event: str, fields: dict[str, object]) -> None:
            return None

        self._capture = capture

    def record(self, event: str, **fields: object) -> None:
        self._capture(event, fields)
