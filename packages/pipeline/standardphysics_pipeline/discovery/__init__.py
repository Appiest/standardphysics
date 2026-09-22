"""Objects RoomPlan never boxed: found in the LiDAR, named from the photos."""

from .boxes import claimed_by_any, contained_fraction, inside, resting_parent
from .cache import DetectionCache
from .carve import CarvedBox, carve, fit_box
from .clusters import dominant_cluster, voxel_components
from .crops import crop_box_of, crop_id_for, save_crop
from .detect import Detection, DetectionError, detect_objects
from .discover import DiscoveryError, DiscoveryInputs, DiscoveryResult, discover_objects
from .merge import Candidate, DiscoveredObject, merge_candidates
from .people import PeopleRemoval, without_people
from .semantic_corrections import (
    SOFA_NAMES,
    TABLE_NAMES,
    WHITEBOARD_NAMES,
    apply_secondary_semantic_corrections,
    correct_furniture_label,
    detect_and_attach_whiteboard,
)

__all__ = [
    "Candidate", "CarvedBox", "Detection", "DetectionCache", "DetectionError", "DiscoveredObject",
    "DiscoveryError", "DiscoveryInputs", "DiscoveryResult", "PeopleRemoval",
    "SOFA_NAMES", "TABLE_NAMES", "WHITEBOARD_NAMES",
    "apply_secondary_semantic_corrections",
    "carve", "claimed_by_any", "contained_fraction", "correct_furniture_label",
    "crop_box_of", "crop_id_for",
    "detect_and_attach_whiteboard", "detect_objects",
    "discover_objects", "dominant_cluster", "fit_box", "inside",
    "merge_candidates", "resting_parent", "save_crop", "voxel_components", "without_people",
]
