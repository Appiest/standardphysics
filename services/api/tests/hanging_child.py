"""Functions a spawned child runs in the worker tests. Kept free of heavy imports so the child starts fast."""

from __future__ import annotations

import os
import subprocess
import time


def hang_after_writing_pid(path: str) -> None:
    with open(path, "w") as handle:
        handle.write(str(os.getpid()))
    time.sleep(3600)


def hang_like_a_bake(settings, scan_id, build_id) -> None:
    time.sleep(3600)


def hang_in_a_grandchild(path: str) -> None:
    grandchild = subprocess.Popen(["sleep", "3600"])
    with open(path, "w") as handle:
        handle.write(str(grandchild.pid))
    grandchild.wait()


def raise_value_error(message: str) -> None:
    raise ValueError(message)


def double(number: int) -> int:
    return number * 2
