"""A box drawn around LiDAR faces nothing has claimed says where a surface was seen, not what stands on the floor."""

import uuid

from standardphysics_contracts import Mat4, SceneNode, Vec3
from standardphysics_pipeline.discovery.mesh_surfaces import UNCLAIMED_SURFACE
from standardphysics_pipeline.occupancy import blocks_floor


def _box(raw_category: str, size=(10.35, 9.6, 4.15)) -> SceneNode:
    return SceneNode(id=uuid.uuid4(), kind="surface_candidate", label="Unidentified vertical surface", raw_category=raw_category,
                     dimensions=Vec3(x=size[0], y=size[1], z=size[2]), transform=Mat4.translation(0.0, 0.0, size[2] / 2),
                     quality="needs_another_look", movable=False, labeled_by="lidar")


def test_a_room_sized_box_around_unclaimed_faces_blocks_no_floor():
    assert not blocks_floor(_box(UNCLAIMED_SURFACE))


def test_the_same_box_named_as_a_standing_thing_still_blocks_the_floor():
    assert blocks_floor(_box("storage", size=(1.2, 0.6, 0.9)))
