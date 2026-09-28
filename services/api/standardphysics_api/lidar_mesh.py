"""Strict validation for uploaded raw AR mesh JSON."""

from __future__ import annotations

import json

from pydantic import ValidationError
from standardphysics_contracts import LidarMesh

MAX_LIDAR_MESH_BYTES = 640 * 1024 * 1024
"""The largest mesh on file, from the Moffitt library's full-floor walk, is 408 MB of JSON.
The whole document is parsed in memory to be checked, so the cap sits at about
one and a half times that rather than at the general artifact cap."""


class InvalidLidarMesh(ValueError):
    pass


def validate_lidar_mesh(payload: bytes) -> LidarMesh:
    try:
        document = json.loads(payload)
        return LidarMesh.model_validate(document)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValidationError, RecursionError) as error:
        raise InvalidLidarMesh("invalid lidar mesh") from error
