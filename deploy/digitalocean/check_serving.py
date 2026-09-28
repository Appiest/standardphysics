"""Whether the stack that just started is the one that was deployed. Run inside the API container:

    docker compose exec -T api /opt/venv/bin/python - <commit> [<public origin>] < check_serving.py

It passes when /health/ready answers 200, /health/details reports <commit>,
and the workspace serves its sign-in page. Otherwise it prints the first thing
that is wrong and exits 1. It runs in the container because the API publishes
no port: only Caddy and its neighbours on the compose network can reach it,
and the image's Python is the one on the box that is sure to be there.

Those three are asked directly, past Caddy. Given a public origin, such as
https://standardphysics.app, it also asks that origin for /api/auth/session
with no cookie and expects the API's 401, which proves Caddy holds a
certificate and sends the browser's /api requests to the API rather than to
the workspace. An empty public origin skips that, for a box with no domain.
The two origins after it are for tests.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

API_ORIGIN = "http://127.0.0.1:8787"
WEB_ORIGIN = "http://web:3000"
SESSION_ROUTE = "/api/auth/session"
SIGNED_OUT_STATUS = 401


def fetched(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")[:300]
        raise SystemExit(f"{url} answered {error.code}: {body}") from None
    except OSError as error:
        raise SystemExit(f"{url} did not answer: {error}") from None


def signed_out_answer(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except OSError as error:
        raise SystemExit(f"{url} did not answer: {error}") from None


def is_api_problem(body: bytes) -> bool:
    try:
        return "error" in json.loads(body)
    except (ValueError, TypeError):
        return False


def check_public_routing(public_origin: str) -> None:
    url = f"{public_origin}{SESSION_ROUTE}"
    status, body = signed_out_answer(url)
    if status != SIGNED_OUT_STATUS or not is_api_problem(body):
        excerpt = body.decode(errors="replace")[:300]
        raise SystemExit(f"{url} answered {status}, not the {SIGNED_OUT_STATUS} the API gives a signed-out browser: {excerpt}")


def check_serving(
    commit: str, public_origin: str = "", api_origin: str = API_ORIGIN, web_origin: str = WEB_ORIGIN
) -> None:
    fetched(f"{api_origin}/health/ready")
    served = json.loads(fetched(f"{api_origin}/health/details")).get("commit")
    if served != commit:
        raise SystemExit(f"the API is serving commit {served}, not {commit}")
    fetched(f"{web_origin}/sign-in")
    if not public_origin:
        print(f"the API and the workspace are serving {commit}")
        return
    check_public_routing(public_origin)
    print(f"the API and the workspace are serving {commit}, and {public_origin} routes /api to the API")


if __name__ == "__main__":
    check_serving(*sys.argv[1:])
