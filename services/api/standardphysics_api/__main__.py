"""python -m standardphysics_api serves the API.

Port 8787 is what the phone and the web workspace expect locally. A hosting
platform usually names the port it has opened in PORT, so that wins when it is
set: the process has to listen where the platform is already routing.
"""

import logging
import os

import uvicorn

from .app import create_app

DEFAULT_PORT = 8787


def port() -> int:
    return int(os.environ.get("PORT") or os.environ.get("SP_API_PORT") or DEFAULT_PORT)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host=os.environ.get("SP_API_HOST", "0.0.0.0"), port=port(), workers=1)
