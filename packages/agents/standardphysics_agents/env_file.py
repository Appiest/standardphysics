"""Keys from a `.env` file, loaded into this process only.

The API server and the evaluation command lines read the same repo-root file,
so the loader lives here, below both of them.
"""

from __future__ import annotations

import os
import pathlib

REPO_ENV_FILE = pathlib.Path(__file__).resolve().parents[3] / ".env"
"""The repo-root `.env`, when running from a checkout."""


def load_dotenv(path: pathlib.Path) -> None:
    """KEY=value lines, without overriding anything already in the environment."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
