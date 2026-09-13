# Audit to B: fixes landed in your files

These fix findings in `PROGRESS.md`. Each has a regression test in `tests/test_audit_lane_b.py` that fails on `d1cd65a`. Pull before you edit these files again.

| Finding | File | Change |
|---|---|---|
| A-5 | `occupancy.py` | `BLOCKING_HEIGHT` is 1/4 in, from ADA 2010 303. At 0.23 m a 7.9 in planter across the aisle read as open floor. Person C should confirm the value against the source. |
| A-7 | `measure.py` | The grid cache keys on the full transform, dimensions and kind. Resizing a node used to reuse the old grid. |
| A-8 | `measure.py`, `footprints.py` | `counter_approach` places and turns the 30 by 48 in space with the counter, in front of its local minus-Y face. `footprints.rotation_about_z` is the shared helper. |
| A-11 | `ingest.py` | An element with no `confidence` gets `needs_another_look` instead of `measured`. |
| A-2 | `check_blender.py` | `blender_path` tries `$BLENDER`, then `blender` on `PATH`, then the macOS app. |
| A-12 | `tests/test_audit_lane_b.py` | Checks the committed `shop.glb` names every node by ID, and a fresh export where Blender is installed. |
| A-13 | `locus.py` | `region_locus` takes `rotation` and `circle`. Pass the counter's rotation for its clear floor space and `circle=True` for a turning space. |

## Still open in your lane

The audit no longer edits your files while your agent is active. Each finding below has a strict expected failure in `tests/test_audit_open_findings.py`. When you fix one, delete its `xfail` marker in the same push; a strict XPASS fails CI until you do.

- **A-14, A-15:** 403.5.2 widths come from grid clearance at points on the route. At a turn that is the distance to the pivot's corner, so a 43/49/43 in turn reads 0.00/43.31/43.31 in and a 61 in turn reads 35.43 in. The audit's fix, which conflicted with `fd43203`: for the approach point, the apex and the leaving point, take the nearest point on the pivot, then the footprint gap from the pivot to the nearest blocking node within about 45 degrees of that direction, skipping nodes that touch the pivot. That returned exactly 43, 49 and 43 in.
- **A-6:** route width still ignores everything near a stop instead of only its `anchor_node_id`, so a 20 in gap 0.2 m inside the entrance reads 31 in. You found that exempting the anchor's clear space regressed leg 1; the pinned test only asks that the entrance gap reads 20 in.
- **A-9:** `door_clear_width` should set `needs_measurement=True`. Lane C's door check already turns it into a request.
- **A-22:** leg 1's 29.79 in is not a pinch the route crosses. The counter and table_1 do not overlap in x, and the grid width where the route passes is 57 in. Details in `PROGRESS.md`.

## `0f0e01b` turned CI red (A-32)

Making the zone widths optional was right, but Lane C's `checks/turn_width.py` compares them with numbers and raises on the fixture shop's leg 1. CI runs three suites; run all three before a push that changes a type another lane reads:

```bash
python -m pytest && python -m pytest packages/agents -q && python -m pytest services/api/tests -q
```

The audit branch carries the Lane C fix. Nothing in your files changed.

## `d74f0e7` fixed CI, with one side effect (A-34)

All three suites pass. Withholding a partly measured turn also means Lane C's gap for an unmeasured turn never fires, so on the fixture shop leg 1's turn is absent from both the findings and the unevaluated list. Bringing it back is Lane C's opt-in through `require_measured=False`; it is recorded as A-34 so it is not lost.

## `022004d`: a sealed route names the wrong case (A-40)

On the fixture with `case_east` stretched wall to wall, `route_clear_width` names `case_west` and `case_east`. Removing `case_west` alone leaves the route blocked. `grid.owner` holds one owner per cell, and `case_west` keeps all 2,304 of its cells after the stretch, so `_would_open` frees cells `case_east` still covers. Rebuilding the grid without the node, or tracking every owner of a cell, answers the question the function asks. Pinned as a strict expected failure. Your run lengths check out: 112.8, 0.0, 51.3 and 0.0.

## Scanned meshes never reach the viewer (A-56, A-57)

- **A-56, high.** `usdz_to_glb` names meshes with RoomPlan's upper-case identifiers, while nodes carry lower-case `str(UUID)`. With Blender at `279ff84`, 0 mesh names match a node exactly on all four real rooms, so the viewer places no scanned mesh. Writing the name as `str(uuid.UUID(identifier))` would match the contract. `fully_identified` counts renames, so it stays true while nothing matches.
- **A-57, medium.** Phone exports import each element twice. Stripping `.001` in `d0947b8` maps both to one identity, and the second rename gets `.001` again: all 26 of `test1`'s nodes appear as `<id>` and `<id>.001`. Keep one object per element.
- Both are pinned as strict expected failures that skip without Blender. A-40 is fixed in `1ebff37`.

## A-59: the counter approach uses a fixed face

`counter_approach` measures in front of the local minus-Y face. On `test1`'s storage object marked as the counter, that face has 0.0 in of depth, while a face 90 degrees round has 145.7 by 39.4 in. RoomPlan's axes do not say which side customers use, so measuring all four faces and reporting the most open one, or taking the front from the owner, would avoid a false problem on real scans.
