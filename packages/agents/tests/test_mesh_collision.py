from uuid import uuid4

from standardphysics_agents.mesh_collision import MeshCollisionIndex
from standardphysics_contracts import LidarMesh, LidarMeshPart, Mat4, SceneNode, Vec3


def mesh(vertices, triangles):
    return LidarMesh(floorY=0, parts=[LidarMeshPart(id=uuid4(), transform=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1], vertices=vertices, triangles=triangles)])


def test_long_route_crosses_triangle_far_from_vertices_and_centroid():
    index = MeshCollisionIndex(mesh([-10,0,0, 10,0,0, -10,2,0], [0,1,2]))
    assert index.collides([Vec3(x=5,y=-2,z=0), Vec3(x=5,y=2,z=0)], 2)
    assert not index.collides([Vec3(x=15,y=-2,z=0), Vec3(x=15,y=2,z=0)], 2)


def test_horizontal_surface_at_device_height_blocks_but_floor_does_not():
    path = [Vec3(x=0,y=-2,z=0), Vec3(x=0,y=2,z=0)]
    for height, expected in [(0, False), (0.4, True), (2.5, False)]:
        index = MeshCollisionIndex(mesh([-1,height,1, 1,height,1, 0,height,-1], [0,1,2]))
        assert index.collides(path, 2) is expected


def test_band_clipping_does_not_project_high_parts_of_sloped_surface_onto_route():
    # The triangle rises above the mobility band long before reaching x=9.
    index = MeshCollisionIndex(mesh([0,0,0, 10,10,0, 10,10,1], [0,1,2]))
    assert not index.collides([Vec3(x=9,y=-2,z=0), Vec3(x=9,y=2,z=0)], 2)
    assert index.collides([Vec3(x=0.2,y=-2,z=0), Vec3(x=0.2,y=2,z=0)], 2)


def test_capsule_radius_and_floor_offset_are_used():
    captured = mesh([-10,1,0, 10,1,0, -10,2,0], [0,1,2]).model_copy(update={'floorY': 1})
    index = MeshCollisionIndex(captured)
    assert index.collides([Vec3(x=0,y=0.2,z=0)], 10)
    assert not index.collides([Vec3(x=0,y=0.3,z=0)], 10)


def test_movable_footprint_exclusion_keeps_unrelated_fixed_geometry():
    captured = mesh(
        [
            -0.4, 0.4, 0,
            0.4, 0.4, 0,
            -0.4, 1.0, 0,
            2, 0.4, -0.5,
            2, 0.4, 0.5,
            2, 1.0, 0,
        ],
        [0, 1, 2, 3, 4, 5],
    )
    furniture = SceneNode(
        id=uuid4(),
        kind="object",
        label="Chair",
        raw_category="chair",
        dimensions=Vec3(x=1, y=1, z=1),
        transform=Mat4.translation(0, 0, 0.5),
        movable=True,
    )
    original = MeshCollisionIndex(captured)
    filtered = original.excluding_nodes([furniture])
    assert original.collides([Vec3(x=0, y=-1, z=0), Vec3(x=0, y=1, z=0)], 2)
    assert not filtered.collides([Vec3(x=0, y=-1, z=0), Vec3(x=0, y=1, z=0)], 2)
    assert filtered.collides([Vec3(x=2, y=-1, z=0), Vec3(x=2, y=1, z=0)], 2)
