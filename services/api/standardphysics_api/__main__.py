"""python -m standardphysics_api serves the API.

Port 8787 is what the phone and the web workspace expect locally. A hosting
platform usually names the port it has opened in PORT, so that wins when it is
set: the process has to listen where the platform is already routing.

The access log goes through `access_log`, so a share link's token never reaches it.
"""

import logging
import os

import uvicorn
from fastapi import FastAPI

from . import access_log
from .app import create_app

DEFAULT_PORT = 8787


def port() -> int:
    return int(os.environ.get("PORT") or os.environ.get("SP_API_PORT") or DEFAULT_PORT)


def server_config(app: FastAPI, host: str, listen_port: int) -> uvicorn.Config:
    return uvicorn.Config(app, host=host, port=listen_port, workers=1, log_config=access_log.logging_config())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.Server(server_config(create_app(), os.environ.get("SP_API_HOST", "0.0.0.0"), port())).run()
