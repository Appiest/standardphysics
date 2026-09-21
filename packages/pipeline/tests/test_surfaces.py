"""Counting things on measured surfaces, against a real scanned room.

The room here came off a phone. The counter does not: a test of arithmetic should
not need a model, and what it returns is fixed so that the number coming out the
other end can be checked by hand.
"""

from __future__ import annotations

import ast
import pathlib

import numpy as np
import pytest
from standardphysics_pipeline import surfaces
from standardphysics_pipeline.surfaces import depth as depth_module
from standardphysics_pipeline.surfaces import faces as face_module

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCANS = ROOT / "services/api/var/scans"


def _literals(tree: ast.AST) -> list[str]:
    """Every string in the source except the ones that are documentation.

    A docstring and a note under a constant are both a string standing alone as a
    statement, and neither can ever reach a comparison. Everything else can.
    """
    bodies = [
        node.body for node in ast.walk(tree) if isinstance(getattr(node, "body", None), list)
    ]
    prose = {
        statement.value
        for body in bodies
        for statement in body
        if isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node not in prose
    ]


def _a_scan() -> pathlib.Path | None:
    for candidate in sorted(SCANS.glob("*")) if SCANS.is_dir() else []:
        if (candidate / "artifacts" / "poses").is_file() and any(
            (candidate / "artifacts").glob("frame-*")
        ):
            return candidate
    return None


@pytest.fixture(scope="module")
def scan() -> surfaces.Scan:
    directory = _a_scan()
    if directory is None:
        pytest.skip("no scan with video on this machine")
    return surfaces.open_scan(directory)


@pytest.fixture(scope="module")
def region(scan) -> object:
    objects = [node for node in scan.graph.nodes if node.kind == "object"]
    if not objects:
        pytest.skip("the scan found no regions")
    return max(objects, key=lambda node: node.dimensions.x)


class TestFacesAreMeasured:
    def test_a_region_has_two_broad_sides(self, region):
        faces = surfaces.faces_of(region)
        assert [face.side for face in faces] == [1, -1]

    def test_a_face_carries_the_region_s_own_extent(self, region):
        width, _, height = region.dimensions.as_tuple()
        for face in surfaces.faces_of(region):
            assert face.width == pytest.approx(width)
            assert face.height == pytest.approx(height)
            assert face.area == pytest.approx(width * height)

    def test_every_face_is_big_enough_to_be_worth_photographing(self, scan):
        """The rule holds over every region a real scan found, rather than over
        one shrunk on purpose to make the rule fire."""
        for node in scan.graph.nodes:
            for face in surfaces.faces_of(node):
                assert face.width >= face_module.MINIMUM_FACE_METRES
                assert face.height >= face_module.MINIMUM_FACE_METRES

    def test_a_region_too_small_for_a_face_is_left_out(self, scan):
        big = [
            node
            for node in scan.graph.nodes
            if min(node.dimensions.x, node.dimensions.z) >= face_module.MINIMUM_FACE_METRES
        ]
        faces = [face for node in scan.graph.nodes for face in surfaces.faces_of(node)]
        assert len(faces) == 2 * len(big)


class TestPatchesTileTheFace:
    def test_a_patch_is_the_size_it_says(self, region):
        face = surfaces.faces_of(region)[0]
        for patch in surfaces.patches_on(region, face, size=0.6):
            assert patch.area == pytest.approx(0.36)

    def test_the_patches_fit_inside_the_face(self, region):
        face = surfaces.faces_of(region)[0]
        patches = surfaces.patches_on(region, face, size=0.6)
        across = int(face.width // 0.6)
        up = int(face.height // 0.6)
        assert len(patches) == across * up

    def test_no_patch_reaches_beyond_the_region(self, region):
        face = surfaces.faces_of(region)[0]
        middle = np.array(region.transform.position.as_tuple())
        reach = float(np.linalg.norm(np.array(region.dimensions.as_tuple()))) / 2 + 0.1
        for patch in surfaces.patches_on(region, face):
            assert np.linalg.norm(patch.corners - middle, axis=1).max() <= reach

    def test_the_two_sides_look_opposite_ways(self, region):
        front, back = surfaces.faces_of(region)
        one = surfaces.patches_on(region, front)[0].normal
        other = surfaces.patches_on(region, back)[0].normal
        assert float(np.dot(one, other)) == pytest.approx(-1, abs=1e-6)


class TestViews:
    def test_a_patch_the_walk_passed_is_found_in_a_frame(self, scan, region):
        """One side or the other, since a walk only goes down one aisle."""
        found = [
            surfaces.best_view(patch, scan.cameras)
            for face in surfaces.faces_of(region)
            for patch in surfaces.patches_on(region, face)
        ]
        assert any(view is not None for view in found)

    def test_a_camera_behind_a_face_does_not_see_it(self, scan, region):
        """A unit is not transparent, so its far side is unseen, not empty."""
        for face in surfaces.faces_of(region):
            for patch in surfaces.patches_on(region, face):
                view = surfaces.best_view(patch, scan.cameras)
                if view is None:
                    continue
                camera = next(c for c in scan.cameras if c.frame_id == view.frame_id)
                towards = patch.corners.mean(axis=0) - camera.position
                assert float(np.dot(patch.normal, towards)) < 0

    def test_the_patches_the_walk_missed_come_back_as_nothing(self, scan):
        """This walk went down one aisle, so most of the room has no view of it.

        The patches with no frame are real patches of a real room, not a rectangle
        moved somewhere nobody could stand.
        """
        every = [
            patch
            for node in scan.graph.nodes
            for face in surfaces.faces_of(node)
            for patch in surfaces.patches_on(node, face)
        ]
        missed = [p for p in every if surfaces.best_view(p, scan.cameras) is None]
        assert missed, "a walk that saw every patch of a room would be a first"
        assert len(missed) < len(every), "and one that saw none would be a bug"

    def test_a_crop_comes_back_as_an_image(self, scan, region):
        for face in surfaces.faces_of(region):
            for patch in surfaces.patches_on(region, face):
                view = surfaces.best_view(patch, scan.cameras)
                if view:
                    assert surfaces.cut_out(view, scan.frames).startswith(b"\xff\xd8")
                    return
        pytest.skip("no patch of this region was photographed")


@pytest.fixture(scope="module")
def views_by_face(scan) -> dict[str, list]:
    """The best candidate view of every patch, worked out once.

    Choosing them means projecting every patch into every frame, which is slow
    enough that doing it per test made this file take eleven minutes.
    """
    found: dict[str, list] = {}
    for node in scan.graph.nodes:
        for face in surfaces.faces_of(node):
            kept = [
                view
                for patch in surfaces.patches_on(node, face)
                for view in surfaces.candidates(patch, scan.cameras)[:1]
            ]
            if kept:
                found[face.name] = kept
    return found


@pytest.fixture(scope="module")
def kept_by_face(scan, views_by_face) -> dict[str, tuple[int, int]]:
    return {
        name: (len(surfaces.seen(views, scan.cameras, scan.cloud)), len(views))
        for name, views in views_by_face.items()
    }


class TestSomethingInTheWay:
    """A crop cut to the right shape can still show the wrong surface.

    Everything here is measured. A cloud of points invented to stand in the way
    proves the arithmetic runs and proves nothing about whether a bookcase in this
    room hides the wall behind it, which is the only question worth asking.
    """

    def test_with_nothing_measured_nothing_is_ruled_out(self, scan, views_by_face):
        """No LiDAR is a reason to know less, not a reason to drop everything."""
        views = [view for found in views_by_face.values() for view in found]
        survived = surfaces.seen(views, scan.cameras, np.zeros((0, 3)))
        assert len(survived) == len(views)
        assert {id(view) for view in survived} == {id(view) for view in views}

    def test_a_surface_with_something_in_front_of_it_loses_views(self, kept_by_face):
        assert any(kept < all_of for kept, all_of in kept_by_face.values()), (
            "in a room of shelving, something stands in front of something"
        )

    def test_what_the_camera_really_hit_is_kept(self, kept_by_face):
        """On a bookcase the things on it are the nearest measured surface."""
        assert any(kept == all_of for kept, all_of in kept_by_face.values()), (
            "no face kept all its views, so the test rejects what it should keep"
        )

    def test_no_surviving_view_has_the_measurements_in_front_of_it(self, scan, views_by_face):
        """The property the whole test exists for, over a sample of what it passed.

        Re-projecting the cloud costs a second a view, so this walks a spread of
        them rather than all four hundred.
        """
        views = [view for found in views_by_face.values() for view in found]
        survived = surfaces.seen(views, scan.cameras, scan.cloud)
        for view in survived[:: max(1, len(survived) // 12)]:
            camera = next(c for c in scan.cameras if c.frame_id == view.frame_id)
            columns, rows, depth = camera.project(scan.cloud)
            ahead = depth > 0
            points = np.stack([columns[ahead], rows[ahead]], axis=1)
            inside = depth_module._inside_quad(points, np.asarray(view.outline))
            if int(inside.sum()) < depth_module.ENOUGH_POINTS:
                continue
            nearer = depth[ahead][inside] < view.distance - depth_module.IN_FRONT_METRES
            assert nearer.mean() <= depth_module.HIDDEN_SHARE


class TestTheArithmeticIsTheEngine_s:
    def test_a_steady_count_scales_by_measured_area(self, scan):
        result = surfaces.tally(
            scan.graph, scan.cameras, scan.frames, "things", lambda _jpeg, _thing: 9,
            readings=1, workers=4, cloud=scan.cloud,
        )
        for surface in result.found:
            assert surface.density == pytest.approx(9 / 0.36)
            if surface.inferred:
                assert surface.estimate == pytest.approx(surface.density * surface.face.area)

    def test_a_barely_photographed_face_is_not_extrapolated(self, scan):
        """The part of a wall a walk passes is the part the shelving is against.

        Scaling what was found there across the whole wall assumes the rest looks
        the same, when the seen part was chosen by where the things were.
        """
        result = surfaces.tally(
            scan.graph, scan.cameras, scan.frames, "things", lambda _jpeg, _thing: 5,
            readings=1, workers=4, cloud=scan.cloud,
        )
        thin = [surface for surface in result.found if not surface.inferred]
        if not thin:
            pytest.skip("every face in this scan was well photographed")
        for surface in thin:
            assert surface.estimate == surface.counted
            assert "too little seen" in surfaces.report(result)

    def test_a_surface_with_things_on_it_never_scales_to_nothing(self, scan):
        """A sparse surface used to vanish.

        The estimate was the middle patch's density, so a floor holding hundreds
        of things in one corner and nothing anywhere else reported zero, while the
        same run said hundreds had been counted on it.
        """
        counts = iter([40] + [0] * 10_000)

        def mostly_empty(_jpeg: bytes, _thing: str) -> int:
            return next(counts)

        result = surfaces.tally(
            scan.graph, scan.cameras, scan.frames, "things", mostly_empty,
            readings=1, workers=1,
        )
        assert result.counted == 40
        assert result.estimate > 0

    def test_the_estimate_never_falls_below_what_was_counted(self, scan):
        result = surfaces.tally(
            scan.graph, scan.cameras, scan.frames, "things", lambda _jpeg, _thing: 3,
            readings=1, workers=4,
        )
        assert result.estimate >= result.counted

    def test_nothing_seen_is_nothing_claimed(self, scan):
        result = surfaces.tally(
            scan.graph, scan.cameras, scan.frames, "things", lambda _jpeg, _thing: 0,
            readings=1, workers=4,
        )
        assert result.counted == 0
        assert result.estimate == 0
        assert result.found == []

    def test_a_counter_that_cannot_tell_is_not_a_zero(self, scan):
        result = surfaces.tally(
            scan.graph, scan.cameras, scan.frames, "things", lambda _jpeg, _thing: None,
            readings=1, workers=4,
        )
        assert result.seen_area == 0
        assert result.coverage == 0

    def test_coverage_never_exceeds_the_surface_there_is(self, scan):
        result = surfaces.tally(
            scan.graph, scan.cameras, scan.frames, "things", lambda _jpeg, _thing: 1,
            readings=1, workers=4,
        )
        assert 0 < result.coverage <= 1
        assert result.seen_area <= result.surface_area

    def test_what_was_not_photographed_is_said_out_loud(self, scan):
        result = surfaces.tally(
            scan.graph, scan.cameras, scan.frames, "things", lambda _jpeg, _thing: 2,
            readings=1, workers=4,
        )
        if result.coverage < 1:
            assert "never photographed" in surfaces.report(result)


class TestNothingIsKeyedToAName:
    """The question supplies the word, and no code here compares against it."""

    def test_no_module_names_a_kind_of_thing(self):
        """No string the code can compare against is the name of a thing.

        Prose is exempt: the docstrings say what the module is for, and a word in
        a sentence never reaches a comparison. Every other literal does.
        """
        named = {"shelf", "shelves", "book", "books", "storage", "table", "chair"}
        for path in pathlib.Path(face_module.__file__).parent.glob("*.py"):
            for value in _literals(ast.parse(path.read_text())):
                assert value.casefold() not in named, f"{path.name} carries {value!r}"

    def test_the_counted_word_only_travels_to_the_model(self, scan):
        seen: list[str] = []

        def counter(_jpeg: bytes, thing: str) -> int:
            seen.append(thing)
            return 1

        surfaces.tally(
            scan.graph, scan.cameras, scan.frames, "wug", counter, readings=1, workers=4
        )
        assert seen and set(seen) == {"wug"}


class TestARefusalRatherThanAGuess:
    def test_no_model_configured_is_an_error(self, monkeypatch):
        for name in ("DISCOVERY_API_KEY", "DISCOVERY_BASE_URL", "DISCOVERY_MODEL"):
            monkeypatch.delenv(name, raising=False)
        with pytest.raises(surfaces.NoCounterConfigured):
            surfaces.counter()

    def test_a_scan_with_no_video_is_refused(self, tmp_path):
        with pytest.raises(surfaces.ScanNotReadable):
            surfaces.open_scan(tmp_path)
