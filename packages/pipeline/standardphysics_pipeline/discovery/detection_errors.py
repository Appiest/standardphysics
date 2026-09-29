"""The ways a detector request fails, split by whether asking again can help."""

from __future__ import annotations


class DetectionError(RuntimeError):
    """The frame could not be read, or the model did not answer."""


class DetectionAuthError(DetectionError):
    """Authentication or authorization failure (401, 403, missing key). Never retried."""


class DetectionSchemaError(DetectionError):
    """Malformed schema or unreadable model response. Never retried."""


class DetectionTransientError(DetectionError):
    """Rate limit (429), server error (500/502/503/504), network timeout. Retried with bounded backoff."""


class DetectionRateLimited(DetectionTransientError):
    """HTTP 429, with the wait the host asked for when it said."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
