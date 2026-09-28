"""Turning a requested rearrangement into the nearest legal one."""

from .facing import facing_error_degrees, settled_yaw, yaw_facing
from .solver import Placement, Snapped, snap

__all__ = ["Placement", "Snapped", "facing_error_degrees", "settled_yaw", "snap", "yaw_facing"]
