"""Uvicorn's access log, with the share token taken out of every path it records.

A share link's token is the whole of its authority: anyone holding
/r/<token> reads that report for 30 days (see `sharing`). The workspace asks
the API for /api/shared/<token>, so the default access log would write a
working credential for every report opened through a link into a file that
log collectors, backups and anyone reading the server's output can see.
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any

from uvicorn.config import LOGGING_CONFIG

SHARE_TOKEN_SEGMENT = re.compile(r"(/api/shared/|/r/)[^/?#\s\"]+")
PLACEHOLDER = "<token>"


def without_tokens(text: str) -> str:
    return SHARE_TOKEN_SEGMENT.sub(rf"\g<1>{PLACEHOLDER}", text)


class ShareTokenFilter(logging.Filter):
    """Replaces the token in each access record's arguments, where Uvicorn keeps the request path."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(without_tokens(arg) if isinstance(arg, str) else arg for arg in record.args)
        if isinstance(record.msg, str):
            record.msg = without_tokens(record.msg)
        return True


def logging_config() -> dict[str, Any]:
    """Uvicorn's own logging config with `ShareTokenFilter` on the access logger."""
    config = copy.deepcopy(LOGGING_CONFIG)
    config["filters"] = {"share_tokens": {"()": ShareTokenFilter}}
    config["loggers"]["uvicorn.access"]["filters"] = ["share_tokens"]
    return config
