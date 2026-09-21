"""The raw AR mesh exchanged between the iOS capture and viewer clients."""

from __future__ import annotations

import math
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_validator,
)

MAX_PART_VERTICES = 1_000_000
MAX_PART_TRIANGLES = 2_000_000
MAX_TOTAL_VERTICES = 8_000_000
MAX_TOTAL_TRIANGLES = 16_000_000


def _lidar_mesh_schema(schema: dict) -> None:
    # The shared schema generator treats defaulted fields as required for
    # response models. These two fields are genuinely optional for older
    # captures, so retain only the geometry requirement here.
    schema["required"] = ["parts"]


class LidarMeshPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    # ARKit's SIMD matrix is column-major: [column0 rows0...3, ...].
    transform: list[float] = Field(min_length=16, max_length=16)
    vertices: list[float] = Field(min_length=3, max_length=MAX_PART_VERTICES * 3)
    triangles: list[StrictInt] = Field(min_length=3, max_length=MAX_PART_TRIANGLES * 3)

    @model_validator(mode="before")
    @classmethod
    def reject_non_numeric_arrays(cls, value):
        if not isinstance(value, dict):
            return value
        for key in ("transform", "vertices"):
            values = value.get(key)
            if isinstance(values, list) and any(type(item) not in (int, float) for item in values):
                raise ValueError(f"{key} must contain only numbers")
        values = value.get("triangles")
        if isinstance(values, list) and any(type(item) is not int for item in values):
            raise ValueError("triangles must contain only integers")
        return value

    @model_validator(mode="after")
    def validate_geometry(self) -> LidarMeshPart:
        if len(self.vertices) % 3:
            raise ValueError("vertices must contain xyz triples")
        if len(self.triangles) % 3:
            raise ValueError("triangles must contain index triples")
        if not all(math.isfinite(value) for value in self.transform):
            raise ValueError("transform must contain finite values")
        if not all(math.isfinite(value) for value in self.vertices):
            raise ValueError("vertices must contain finite values")
        if not _is_rigid_affine(self.transform):
            raise ValueError("transform must be a rigid affine matrix")
        vertex_count = len(self.vertices) // 3
        if any(index < 0 or index >= vertex_count for index in self.triangles):
            raise ValueError("triangle index is out of range")
        return self


class LidarMesh(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_lidar_mesh_schema)

    parts: list[LidarMeshPart] = Field(min_length=1, max_length=4096)
    peopleFilteringEnabled: StrictBool | None = None
    floorY: float | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_non_numeric_floor(cls, value):
        if isinstance(value, dict) and "floorY" in value:
            floor_y = value["floorY"]
            if floor_y is not None and type(floor_y) not in (int, float):
                raise ValueError("floorY must be a number")
        return value

    @model_validator(mode="after")
    def validate_totals(self) -> LidarMesh:
        vertex_count = sum(len(part.vertices) // 3 for part in self.parts)
        triangle_count = sum(len(part.triangles) // 3 for part in self.parts)
        if vertex_count > MAX_TOTAL_VERTICES:
            raise ValueError("mesh exceeds total vertex limit")
        if triangle_count > MAX_TOTAL_TRIANGLES:
            raise ValueError("mesh exceeds total triangle limit")
        if self.floorY is not None and not math.isfinite(self.floorY):
            raise ValueError("floorY must be finite")
        return self


def _is_rigid_affine(values: list[float]) -> bool:
    # Column-major 4x4: the final row is [m03, m13, m23, m33].
    if not (
        math.isclose(values[3], 0.0, abs_tol=1e-3)
        and math.isclose(values[7], 0.0, abs_tol=1e-3)
        and math.isclose(values[11], 0.0, abs_tol=1e-3)
        and math.isclose(values[15], 1.0, abs_tol=1e-3)
    ):
        return False
    columns = (
        (values[0], values[1], values[2]),
        (values[4], values[5], values[6]),
        (values[8], values[9], values[10]),
    )
    for column in columns:
        if not math.isclose(sum(value * value for value in column), 1.0, abs_tol=1e-3):
            return False
    if any(
        not math.isclose(sum(left * right for left, right in zip(first, second)), 0.0, abs_tol=1e-3)
        for index, first in enumerate(columns)
        for second in columns[index + 1 :]
    ):
        return False
    determinant = (
        columns[0][0] * (columns[1][1] * columns[2][2] - columns[1][2] * columns[2][1])
        - columns[1][0] * (columns[0][1] * columns[2][2] - columns[0][2] * columns[2][1])
        + columns[2][0] * (columns[0][1] * columns[1][2] - columns[0][2] * columns[1][1])
    )
    return math.isclose(determinant, 1.0, abs_tol=1e-3)
