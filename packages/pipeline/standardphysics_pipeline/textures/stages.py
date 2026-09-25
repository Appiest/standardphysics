"""How long each step of a texture build took, written to the log as it finishes."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager

log = logging.getLogger("standardphysics.textures")


@contextmanager
def timed(step: str):
    started = time.monotonic()
    yield
    log.info("texture step %s took %.1f s", step, time.monotonic() - started)
