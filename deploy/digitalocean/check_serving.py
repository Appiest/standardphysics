"""Whether the stack that just started is the one that was deployed. Run inside the API container:

    docker compose exec -T api /opt/venv/bin/python - <commit> < check_serving.py

It passes when /health/ready answers 200, /health/details reports <commit>,
and the workspace serves its sign-in page. Otherwise it prints the first thing
that is wrong and exits 1. It runs in the container because the API publishes
no port: only Caddy and its neighbours on the compose network can reach it,
and the image's Python is the one on the box that is sure to be there. The
two origins after the commit are for tests.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

API_ORIGIN = "http://127.0.0.1:8787"
WEB_ORIGIN = "http://web:3000"


def fetched(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")[:300]
        raise SystemExit(f"{url} answered {error.code}: {body}") from None
    except OSError as error:
        raise SystemExit(f"{url} did not answer: {error}") from None


def check_serving(commit: str, api_origin: str = API_ORIGIN, web_origin: str = WEB_ORIGIN) -> None:
    fetched(f"{api_origin}/health/ready")
    served = json.loads(fetched(f"{api_origin}/health/details")).get("commit")
    if served != commit:
        raise SystemExit(f"the API is serving commit {served}, not {commit}")
    fetched(f"{web_origin}/sign-in")
    print(f"the API and the workspace are serving {commit}")


if __name__ == "__main__":
    check_serving(*sys.argv[1:])
