"""The fast widest-route search and the box-first shape tests give exactly the answers of the plain versions."""

import heapq
import math
import random

import numpy as np
import pytest
from standardphysics_pipeline.footprints import closer_than, gap_between, touching
from standardphysics_pipeline.occupancy import Grid
from standardphysics_pipeline.routes import NEIGHBOURS, _bottleneck_search, _retrace


def _plain_search(grid, walkable, search_field, sources, goals):
    """The search as first written, over (row, col) tuples and numpy arrays."""
    best = np.full(grid.shape, -1.0)
    came_from = {}
    queue = []
    for row, col in sources:
        best[row, col] = search_field[row, col]
        queue.append((-best[row, col], (int(row), int(col))))
    heapq.heapify(queue)
    while queue:
        negative_width, cell = heapq.heappop(queue)
        width = -negative_width
        if width < best[cell]:
            continue
        if goals[cell]:
            return came_from, cell
        for d_row, d_col in NEIGHBOURS:
            neighbour = (cell[0] + d_row, cell[1] + d_col)
            if grid.contains(*neighbour) and walkable[neighbour]:
                candidate = min(width, search_field[neighbour])
                if candidate > best[neighbour]:
                    best[neighbour] = candidate
                    came_from[neighbour] = cell
                    heapq.heappush(queue, (-candidate, neighbour))
    return came_from, None


def _room(seed: int):
    rng = np.random.default_rng(seed)
    rows, cols = rng.integers(8, 40, size=2)
    walkable = rng.random((rows, cols)) > 0.25
    # Few distinct widths, so many cells tie and the tie-breaking order is exercised.
    field = np.round(rng.random((rows, cols)) * 4) / 4
    field[rng.random((rows, cols)) > 0.9] = np.inf
    goals = np.zeros((rows, cols), dtype=bool)
    goals[-3:, -3:] = True
    sources = np.argwhere(walkable[:3, :3])
    grid = Grid(0.0, 0.0, 0.1, ~walkable, np.full((rows, cols), -1, dtype=np.int32), [])
    return grid, walkable, field, sources, goals


@pytest.mark.parametrize("seed", range(40))
def test_the_fast_search_finds_the_same_route_cell_for_cell(seed):
    grid, walkable, field, sources, goals = _room(seed)
    plain_came, plain_arrival = _plain_search(grid, walkable, field, sources, goals)
    fast_came, fast_arrival = _bottleneck_search(grid, walkable, field, sources, goals)
    assert fast_arrival == plain_arrival
    if plain_arrival is not None:
        assert _retrace(fast_came, fast_arrival) == _retrace(plain_came, plain_arrival)


def _rectangle(rng: random.Random):
    x, y = rng.uniform(-3, 3), rng.uniform(-3, 3)
    half_x, half_y, angle = rng.uniform(0.05, 1.0), rng.uniform(0.05, 1.0), rng.uniform(0, math.pi)
    cos_t, sin_t = math.cos(angle), math.sin(angle)
    return [(x + lx * cos_t - ly * sin_t, y + lx * sin_t + ly * cos_t)
            for lx, ly in ((-half_x, -half_y), (half_x, -half_y), (half_x, half_y), (-half_x, half_y))]


def test_the_box_first_tests_agree_with_the_exact_gap():
    rng = random.Random(7)
    for _ in range(4000):
        a, b = _rectangle(rng), _rectangle(rng)
        within = rng.uniform(0.0, 1.0)
        assert touching(a, b) == (gap_between(a, b) == 0.0)
        assert closer_than(a, b, within) == (gap_between(a, b) < within)


def test_shapes_a_hair_apart_are_measured_rather_than_boxed():
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    for gap in (0.0, 1e-9, 1e-7, 1e-3):
        beside = [(x + 1.0 + gap, y) for x, y in square]
        assert touching(square, beside) == (gap_between(square, beside) == 0.0)
        assert closer_than(square, beside, 1e-3) == (gap_between(square, beside) < 1e-3)
