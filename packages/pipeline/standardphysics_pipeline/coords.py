"""RoomPlan's axes to ours, converted once, here, on ingest.

Two things differ and both are easy to get wrong silently.

**Handedness of the up axis.** ARKit and RoomPlan are right-handed with **Y
up** and -Z forward. We are right-handed with **Z up**, because that is what
Blender, USD and floor plans use. The change of basis is a +90 degree rotation
about X, so a world point moves (x, y, z) -> (x, -z, y).

**Matrix layout.** `simd_float4x4` is **column-major**, and Swift's JSONEncoder
writes it as four columns in order. Our `Mat4` is **row-major**. So the
translation ARKit puts at flat indices 12, 13, 14 belongs at our indices 3, 7,
11. Reading an ARKit matrix as row-major puts the position in the bottom row
and every object lands at the origin.

Nothing downstream converts axes again. Inches appear only at the UI boundary.
"""

from __future__ import annotations

from standardphysics_contracts import Mat4, Vec3


def point_to_z_up(x: float, y: float, z: float) -> Vec3:
    """A world point from Y-up to Z-up."""
    return Vec3(x=x, y=-z, z=y)


def point_to_y_up(x: float, y: float, z: float) -> Vec3:
    """The inverse, for anything handed back to an ARKit-shaped consumer."""
    return Vec3(x=x, y=z, z=-y)


def dimensions_to_z_up(x: float, y: float, z: float) -> Vec3:
    """RoomPlan reports extents in a Y-up local frame as (width, height, depth).

    Extents are unsigned, so this is a swap rather than a rotation: height
    becomes our Z and depth becomes our Y.
    """
    return Vec3(x=x, y=z, z=y)


def _columns_to_rows(flat: list[float]) -> list[list[float]]:
    """Column-major flat 16 to a row-major 4x4 nested list."""
    columns = [flat[0:4], flat[4:8], flat[8:12], flat[12:16]]
    return [[columns[c][r] for c in range(4)] for r in range(4)]


_BASIS = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, -1.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]
_BASIS_INV = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, -1.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]


def _multiply(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [
        [sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)]
        for r in range(4)
    ]


def capture_to_room(floor_height: float) -> Mat4:
    """ARKit world to the room frame: turn Y-up into Z-up, then lower the floor to z = 0.

    `floor_height` is the floor's ARKit Y, which becomes its Z after the turn.
    """
    rows = [list(row) for row in _BASIS]
    rows[2][3] = -floor_height
    return Mat4(m=[value for row in rows for value in row])


def transform_from_arkit(flat_columns: list[float]) -> Mat4:
    """An ARKit `simd_float4x4`, as Swift serializes it, to our Mat4.

    Conjugating by the basis change keeps rotation and translation consistent:
    a rotated counter stays rotated the same way relative to its new up axis.
    """
    if len(flat_columns) != 16:
        raise ValueError(f"expected 16 floats, got {len(flat_columns)}")
    rows = _columns_to_rows(flat_columns)
    converted = _multiply(_multiply(_BASIS, rows), _BASIS_INV)
    return Mat4(m=[value for row in converted for value in row])
