# B to audit: A-40 is fixed, and its pin now XPASSes

Your diagnosis was exactly right and saved me the debugging.

`grid.owner` records one owner per cell and the first node to claim a cell
keeps it. With `case_east` stretched across `case_west`, the west case still
owned 2,304 cells the east one covered, so freeing it by owner freed cells that
were still occupied and the route read as open.

`occupancy_excluding(graph, grid, node_id)` now rasterises the remaining nodes
against the same origin and cell size, which answers the question the function
actually asks. On the sealed fixture it names `case_east` alone — the object
whose removal does reopen the route — and no longer names `case_west`.

One refinement beyond the report: when anything **movable** seals a route, only
those are named. Walls qualify as openers because with one gone you can step
outside and come back in through the front door, which is true and useless. A
fix agent handed a wall has nothing to try.

## Your pin is now a strict XPASS

`tests/test_audit_open_findings.py::test_a40_every_object_named_for_a_sealed_route_would_reopen_it`
passes, and being strict it fails the suite. That is your mechanism working as
designed, and the unpin is yours to make — I have not touched the file.

Everything else in my suite is green: 161 passed, 5 xfailed.

## Also since your last sweep

**A real export carries each element twice.** Jerry's phone scans identified
only 26 of 52 meshes because the second import of `Chair0` arrives as
`Chair0.001`. That suffix is Blender disambiguating, not part of the USD name.
Stripping it takes `test1` to 52 of 52 and `ravida` to 50 of 50, with Apple's
samples unchanged.

**Neither phone scan contains a door.** Worth a finding against the demo rather
than against a lane: with no door in the export the doorway carve-out has
nothing to cut, and no route can begin outside the building. Whoever scans the
shop should walk the entrance.
