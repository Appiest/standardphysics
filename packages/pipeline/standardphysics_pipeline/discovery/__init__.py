"""Objects RoomPlan never boxed: found in the LiDAR, named from the photos."""

from .boxes import claimed_by_any, contained_fraction, inside, resting_parent
from .cache import DetectionCache
from .carve import CarvedBox, carve, fit_box
from .clusters import dominant_cluster, voxel_components
from .detect import Detection, DetectionError, detect_objects
from .discover import DiscoveryError, DiscoveryInputs, DiscoveryResult, discover_objects
from .merge import Candidate, DiscoveredObject, merge_candidates
from .people import PeopleRemoval, without_people

__all__ = [
    "Candidate", "CarvedBox", "Detection", "DetectionCache", "DetectionError", "DiscoveredObject",
    "DiscoveryError", "DiscoveryInputs", "DiscoveryResult", "PeopleRemoval",
    "carve", "claimed_by_any", "contained_fraction", "detect_objects",
    "discover_objects", "dominant_cluster", "fit_box", "inside",
    "merge_candidates", "resting_parent", "voxel_components", "without_people",
]
