# D to B: walls seal their doorways

## A route cannot pass through a door

`build_grid` leaves doors out of the grid, which is right. Nothing removes the
door from the wall it sits in, though, so the wall stays solid across the
opening. RoomPlan reports a wall at its full length and puts each door inside
it as a separate surface.

Reproduced at `28e64c1` on the fixture shop:

- The front door is 0.90 m wide at (0.00, -4.00), and the south wall runs 6 m
  along the same line.
- 17 of 17 samples taken 5 cm apart across the door opening are occupied, and
  the wall owns every one.
- A stop 1 m outside the door has no route to the counter.
  `route_clear_width` returns `reachable=False` with no blockers.

The tests pass only because the fixture's Entrance stop sits 0.3 m inside the
room, so no leg ever crosses the door. On a real scan, any route that starts
outside or leads into a back room fails. It either comes back unreachable, or
`_nearest_free` snaps the stop to the other side of the wall.

**Suggested fix:** mark the walls, then clear every cell that a `door` or
`opening` footprint covers. `door_clear_width` and the 404.2.4 maneuvering
check will need the opening as open floor anyway.

`build_street_scenario()` in `standardphysics_fixtures` is coming in my next
push. Its entrance stop sits 1 m outside the front door, so a regression test
can cross the doorway.

## Fixture scenario: legs 2 and 3 change, leg 0 does not

- **Seat moves.** It sits at (-2.0, -2.4), which is the centre of table_3. On
  the current code, leg 3 measures 6.89 in because the route starts inside a
  table. Seat moves to open floor beside that table. Every test in `tests/`
  uses leg 0 only.
- **Stops get anchors.** Each stop gains the `anchor_node_id` the audit asked
  for in A-6. Entrance and Exit anchor to the front wall, Counter and Pickup to
  the counter, and Seat to table_3. Nothing in your code changes until you read
  the field.

Leg 1, from Counter to Pickup, measures 29.79 in between two stops that stand
0.15 m from the counter face. Please check whether that number is the endpoint
exemption or a real pinch.

## Contract fields landing next

Both are optional, and their defaults keep today's behaviour:

- `Stop.anchor_node_id: UUID | None = None` (audit A-6)
- `WidthResult.needs_measurement: bool = False` (audit A-9)

`standardphysics_contracts.graph_hash(graph)` is coming too. Use it wherever a
graph hash is needed, so a hash computed in one lane matches another lane's.

## One ask: clearance along a route

The viewer should colour a `path` annotation by the clearance at each point.
`Annotation.points` has no values for that. If `widest_path` can return the
clearance in inches at each point, I will add an optional per-point field to
`Annotation`. Tell me whether that is cheap on your side.

---

## Update: shorter display cases, a rebuilt GLB, and `point_inches`

- **Display cases.** Each case now stops 6 inches short of its side wall
  (`CASE_WALL_GAP_INCHES`), so Lane C's documented 5 inch fix is a legal move.
  The aisle is still exactly 31 in, and the fix still reaches 36.
- **Two of your tests.** Both sealed the aisle by widening `case_east` until it
  met `case_west`, which now leaves a 6 in route past the wall. Each test now
  stretches `case_east` wall to wall instead, and checks the same thing:
  - `tests/test_measure.py::test_a_blocked_route_reports_unreachable`
  - `tests/test_audit_lane_b.py::test_a7_resizing_an_object_rebuilds_the_cached_grid`
- **Rebuilt files.** `shop.glb` and `shop.usdz` are rebuilt from the new graph
  with Blender 5.2.1.
- **`point_inches`.** `Annotation.point_inches: list[float | None] | None` is
  in. Please return `None` for points inside the exemption, as you offered. The
  viewer will then colour only the values that mean something.

---

## Urgent: on a real scan the floor is not at z = 0

`packages/fixtures/standardphysics_fixtures/data/real/` now holds two real
RoomPlan exports from Apple's WWDC23 sample (MIT-style licence, notice
included). Each has `room.json`, the mapping file and the USDZ. `parse_room_json`
reads all 11 rooms in that sample without an error.

RoomPlan puts the origin wherever the phone started, so the floor of a real
scan sits about 1.4 m below z = 0. `blocks_floor` compares an object's top
against `BLOCKING_HEIGHT` in absolute z, which gets both directions wrong. In
`apple_livingroom.room.json` the floor is at z = -1.436:

| Object | Bottom | Top | `blocks_floor` |
|---|---|---|---|
| Sofa (3 of them) | -1.436 | -0.558 | **False** |
| Table (2) | -1.436 | -0.974 / -0.927 | **False** |
| Oven | -1.436 | -0.546 | **False** |
| Television, wall-mounted | -0.224 | +0.620 | **True** |
| Storage, wall-mounted | -0.463 | +0.979 | **True** |

So every sofa and table reads as open floor, and a wall-mounted cabinet whose
underside is 0.97 m up reads as a floor obstruction. Counter height, camera
eye height and render framing all assume the same floor.

**Suggested fix:** in ingest, shift every node so the floor's z is 0, then let
nothing else change. Measure a blocker by its bottom as well as its top, so an
object whose underside clears 27 in stays out of the floor grid and belongs to
the protruding-objects check instead.

Two other things the real files settle for you:

- **`parentIdentifier`.** Every door, window and opening carries its parent
  wall's identifier. Reading it into `SceneNode.parent_id` tells the doorway
  carve-out exactly which wall to cut.
- **The mapping file is a binary plist** (`bplist00`), not JSON, even though
  Lane A names it `room.metadata.json`. Read it with `plistlib.loads`.
  `usdz_to_glb` should accept both.
- **Unknown top-level keys.** Real exports carry `coreModel` (a base64 blob,
  60-90% of the file), `sections`, `story` and `version`. Newer ones also carry
  `referenceOriginTransform`. None has a top-level `identifier`.

`tests/test_real_exports.py` pins what already works.

---

## `usdz_to_glb` on a real export: the mapping is a plist

The API now runs your stages on uploads. I replayed Apple's real living room
(`data/real/apple_livingroom.*`) through upload, finalize and the job queue with
Blender 5.2.1:

- `parse_room_json` produced the graph, and the scan was ready in about 2
  seconds.
- `usdz_to_glb` opened the USDZ, taking 35 ms, but printed no `USDZ_CONVERTED`.
  `load_map` calls `json.loads` on the mapping file, and a real mapping is a
  binary plist. Try `plistlib.loads` first and fall back to JSON. The API names
  the file `room.metadata.plist` when its bytes start with `bplist`, and
  `room.metadata.json` otherwise.
- The API fell back to `export_glb`, which named all 22 GLB nodes by SceneGraph
  ID.

## A finding at 0.0 inches on the sample shop

With every rule verified for preview, the fixture reports "The turn around the
display case is too tight" measured at **0.0 in**. The aisle and counter
findings read 31.0, 29.79 and 43.3 as expected. A 0.0 in turn looks like
`turn_detail` returning an unmeasured zone as zero. Lane C sees it too.

---

## `0f0e01b` broke Lane C's turn check

Returning `None` for an unmeasured turn zone is right. Lane C's
`turn_verdict` still compares each zone with `<`, so `assess` now raises on
the fixture shop, and CI on `0f0e01b` is red. Details are in `D-to-C.md`.
Please agree the fix with Lane C before either of you pushes it.

---

## A drag re-check takes about 2 s, and the target is under 1 s

Dragging furniture calls `assess` on every drop, through
`POST /api/scans/{id}/layout-checks`. On the fixture shop, with every rule
enabled, one call takes 1.7 to 2.3 s. The profile of one call on a moved
layout:

| Where | Calls | Time |
|---|---|---|
| `routes.widest_path` | **20** | 3.86 s of 4.26 s under cProfile |
| `occupancy.Grid.contains` | 4,535,305 | 0.81 s |
| `measure.route_clear_width` | 16 | 3.26 s |

The same four legs get routed again for route width, turn detail, run length,
passing space and exit path, all on the same layout. Caching the widest path
per graph hash and leg inside `PipelineMeasurements` would drop that to 4
routes. Moving the bounds check out of the Python loop would speed up each
route as well.

---

## The counter-height render and camera are too close to read

The printed report (`/scans/<id>/report`) now shows your `render_finding`
stills, and the viewer flies to each locus camera. For "The ordering counter is
too high to order from", both show a flat close-up of the counter front with
the vertical line and nothing else. Nobody can tell what they are looking at.
The route renders read well. Pulling the height camera back to show the counter
against the floor and a nearby table would fix both views.

---

## The counter leads the demo now, at 47 inches

The pitch example is a real lawsuit, Whitaker v. T Rock Inc. (N.D. Cal. No.
5:22-cv-00283). Its complaint, paragraph 12, puts the ordering counter at about
47 inches, against the 36 in that ADA 2010 904.4.1 allows. The fixture counter
now stands exactly 47 in, up from 43.3 in. The route pinch is unchanged, and
`shop.glb` and `shop.usdz` are rebuilt. Your tests read the height from the
graph, so they still pass.

That makes the counter height the first finding people see. **The height
locus camera matters most now.** Today it frames a flat close-up of the counter
front with a vertical line, in both the viewer and `render_finding`. The shot
needs to show:

- the counter against the floor
- the 47 in line running up its front
- enough of the room to read as a shop

The deck in `apps/web` draws a 36 in long lowered section next to it, so leave
a little room at one end of the counter.

---

## Real phone scans: the floor node's dimensions swap depth and height

For both `datasets/phone/ravida` and `test1`, `parse_room_json` gives the
floor `dimensions` of (9.78, 0.00, 8.41) and (11.22, 0.00, 10.56), which is
8 to 10 m tall and zero deep. RoomPlan writes a floor as [x, y, 0] with its
local z pointing up, so `dimensions_to_z_up` is swapping the wrong pair for
floors. Nothing in Lane D reads the floor's size today; I use the wall
footprints for the room outline instead. Anything that takes the room's extent
from the floor node will be wrong, and a floor footprint currently has no
depth at all.

---

## Tonight: two checks that tell an owner they pass when they may not

Brendan wants the app to do what the README pitch says. I read both of these
from the code at `256ced3`; they are the Lane B half of the fixes.

### `door_clear_width` never sets `needs_measurement`

`measure.py:311` has a docstring saying the opening comes back with
`needs_measurement` set. The code at `measure.py:323-328` returns
`max(door.dimensions.x, door.dimensions.y)` and never sets it. ADA 2010 404.2.3
measures between the door face and the stop with the door open 90 degrees, and
the open leaf and the stop both sit inside RoomPlan's opening. So a 33 in
opening passes as 33 in clear when the real clear width can be under 32.

Lane C already turns the flag into a request for one tape measurement
(`checks/door_width.py:22-33`, from `eb28c10`). Setting
`needs_measurement=True` on every door result closes this. An opening already
under 32 in can stay a plain fail if you want, since the clear width can only
be smaller.

### `turning_space` reports a circle, and `passing_space` reads it as a square

`measure.py:295-309` returns `inches_wide` and `inches_deep` both equal to
twice the clearance at the point. That describes the largest clear circle.
`passing_space.py:100-101` passes it to `fits_square`, and 403.5.3 asks for a
60 by 60 inch square. A square fits only when its corners are clear too, so a
point with a 60 in clear circle can fail the square. A conservative answer
is a square side of `clearance * sqrt(2)`, which fits at any rotation.
Searching rotations for the exact side is better if it is cheap. Please agree
the field meaning with Lane C before either of you pushes, as with `0f0e01b`.

### Still open and on the pitch path

- **A-6, the endpoint exemption.** The 0.75 m around a stop still hides
  anything just inside the entrance (`routes.py`). The pitch now says we check
  doorways, and that is the one place the exemption hides most.
- **The tape check.** No real scan has been tape-measured yet, and neither
  phone scan in `datasets/phone` contains a door. Your done criterion needs a
  scan with a real door and a tape measure against it. If someone can scan a
  space with a door before the venue closes, that one number backs every
  measurement in the demo.

---

## Correction: the door flag waits on Lane C

My last section said setting `needs_measurement` on every door closes the door
width problem. You already tried that and held it in `B-to-C.md`, because it
fails 15 of Lane C's tests. That call belongs to Lane C, and I have moved the
ask to `D-to-C.md`. Nothing new for you here until they decide.

`docs/archive/handoffs/D-to-C-rule-review.md` also notes that `service_counter_approach`
centres its rectangle on the high counter (x = 0.46 m on the sample shop)
instead of on the lowered section beside it (x = -1.14 m). 904.4.1 places the
clear floor space "adjacent to the 36 inch (915 mm) minimum length of counter".

## 2026-09-23: changes Lane D made in your files

Pull before you touch `occupancy.py`, `routes.py`, `turns.py`, `measure.py`, `footprints.py` or `locus.py`. Every change has a test, and the fixture shop's four legs still read 31.0, 59.06, 31.0 and 102.89 in.

| Commit | What changed | Why |
|---|---|---|
| `b6da05c` | Obstacles are drawn at least two cells across, and walls at least `2 * INDOOR_MARGIN`. A trip between two stops on the floor never gets the outside, and is blocked when the floor cannot join them. `PathResult.clearance` is the field the route was searched in. A stop inside an object is reached anywhere on its standing room. | RoomPlan walls have no thickness, so none reached the grid: routes ran round the room's edge and through doorway cuts. Details in the commit. |
| `1084818` | `widest_path` takes the stops' anchors. Near an anchored stop only floor nearest the anchor, or no closer to its nearest obstacle than the stop, is exempt. | A-6: a 20 in gap inside the entrance read 31 in. |
| `1d6a685` | Turn widths are rays from the pivot to the first obstacle beyond it; the width at the turn is cast off the pivot's end; the pivot is the nearest element whose end the apex lies past. | A-14, A-15, A-16. |
| `9944fbf` | `routes.runs_below` and `measure.route_runs_below` report every narrow stretch; `longest_run_below` is built on it. | 403.5.1's 48 inch separation condition. |
| `2382bd3` | `PipelineMeasurements.largest_square`. | 403.5.3's space is a square, not a circle. |
| `790381d` | `camera_for` takes the room outline and keeps the camera over the floor. | A-17. |

Two things are yours to decide. `turning_space` still reports `fits=diameter >= 60.0`, a second copy of the pack's 60 that `environment_physics.py` relies on. And the widest path's tie-break is still the queue's cell order, which is why routes wander after their pinch; the turn detector's thresholds were tuned against those shapes, so changing one means retuning the other.
