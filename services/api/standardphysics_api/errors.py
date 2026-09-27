from __future__ import annotations

from standardphysics_contracts import ApiError


class ApiProblem(Exception):
    def __init__(
        self, status: int, error: str, need: list[str] | None = None, headers: dict[str, str] | None = None
    ):
        self.status, self.body = status, ApiError(error=error, need=need)
        self.headers = headers
