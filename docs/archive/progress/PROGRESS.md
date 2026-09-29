# Audit log

Every push to `master` is audited against its lane document, the plan's invariants, `packages/contracts`, CI on the pushed commit, path ownership in `docs/archive/AGENT_PROTOCOL.md`, and whether `docs/archive/progress/PROGRESS_<LANE>.json` matches the code. A finding is recorded only after it is reproduced by running code or read directly from the diff.

Status is one of `open`, `fixed in <commit>`, or `blocked` with the person or lane it waits on.

The audit fixes code only in files no lane agent is actively changing. For lanes with an active agent, each open finding is pinned as a strict expected failure in `tests/test_audit_open_findings.py`: CI stays green while the bug exists, and the push that fixes it must delete the marker.

## Pushes

| Push | Lane | Verdict | Findings |
|---|---|---|---|
| `21bbc65` Add shared contracts, fixture shop, and the upload contract | D | Pass with notes | A-1 |
| `a95c77e` B: Blender check and coordinate conversion | B | Pass with notes | A-2, A-3 |
| `1a6487e` B: occupancy grid, widest path, and exact clearance measurement | B | **Fail** | A-4, A-5, A-6, A-7, A-8, A-9, A-10 |
| `bb6e303` B: parse CapturedRoom exports into a SceneGraph | B | Pass with notes | A-3, A-11 |
| `d0d0ea5` B: GLB export, and reconcile the lane docs with OpenRouter | B | Pass with notes | A-3, A-12 |
| `d2a982a` Fix CI: install packages/pipeline | B | Pass | Resolves A-4 |
| `d1cd65a` B: build the locus that points a finding at the model | B | Pass with notes | A-13 |
| `28e64c1` B: implement ADA 403.5.2, and convert scanned USDZ | B | **Fail** | A-14, A-15, A-16 |
| `a4eb74a` B: render findings for the report | B | Pass with notes | A-17 |
| `9dd969d` D: announce contract fields and the assess seam | D | Pass | Reports A-20, A-21 |
| `9ced7bc` C: rule pack, tier 1 checks, finding copy | C | Pass with notes | A-23, A-24 |
| `b5306c8` D: stop anchors, needs_measurement, graph_hash, street scenario | D | Pass | Resolves A-21; unblocks A-6 and A-9 |
| `0132fb2` B: cut doorways out of walls, bound the search | B | Pass with notes | Resolves A-20; A-6 still open; handoff misreads A-22 |
| `898984f` D: rule units, contract vocabulary, Lane C in CI | D | Pass with notes | Resolves A-23 and A-24; A-18 |
| `2abf6bc` to `29bfb95` A: capture app, export, upload (7 pushes) | A | Not yet audited | Needs an Xcode build and test run |
| `8a161d1` C: the fix agent and the entrypoint | C | Pass with notes | A-26 |
| `1414cf4` D: room beside the display cases, point_inches | D | **Fail** | CI red; A-18, A-27 |
| `fd43203` B: height locus, measured counter approach, run length | B | **Fail** | A-14 and A-15 persist; declines A-6 |
| `ceaa390` D: the documented five inch fix is a legal move | D | Pass with notes | CI green again; A-18 |
| `eb56ef4` B: populate point_inches | B | Pass | |
| `01582fd` D: real RoomPlan exports | D | Pass with notes | Reports A-25 |
| `f9531f0` Plan: pitch to shop owners | Plan | Not a lane push | |
| `7aa85c8` D: scaffold the web app, generate contract types | D | **Fail** | A-28 |
| `5d09e4d` B: stand the room on its floor, read both ends of an object | B | Pass | Resolves A-25 |
| `a260626` D: the API, running every stage a real scan goes through | D | Pass with notes | A-29, A-30, A-31; its web run still failed on A-28 |
| `0aa64b8` D: generate Next route types before typechecking | D | Pass | Resolves A-28 |
| `0f0e01b` B: an unmeasured turn zone reports nothing, not zero inches | B | **Fail** | CI red; A-32 |
| `2fad000` D: the shop viewer, finding callouts and the findings list | D | Pass with notes | A-33; web workflow green |
| `5897919` D: report the turn check crash that turned master red | D | Pass | Reports A-32 to B and C |
| `f32018b` D: record the API and viewer as ready for other lanes | D | Pass | Progress file matches the code; CI still red from A-32 |
| `586f765` C: the router, the fix loop, the labelled dataset and the gate | C | Pass with notes | Resolves A-32; its note that Lane C's tests never run on master is out of date |
| `d74f0e7` B: withhold a partly measured turn instead of handing out a None | B | Pass with notes | Also resolves A-32; A-34 |
| `a9ce65c` D: check a layout while it is dragged, and save one | D | **Fail** | CI red (A-38); A-35; makes A-31 reachable |
| `9be20af` D: profile the drag re-check for Lane B | D | Pass | Handoff only; CI still red from A-38 |
| `945b8a4` D: drag furniture, see the checks update, and save the layout | D | Pass with notes | A-36, A-37; the drag and turn maths match Lane C's `move_node` |
| `52ec453` D: the printable report, with who reviewed each rule | D | Pass with notes | A-39 |
| `d201984` D: record rearrange and the report as ready | D | Pass | Progress file matches the code |
| `022004d` B: name what sealed a blocked route, and measure runs outside the exemption | B | **Fail** | CI red by design (A-41); A-40; run lengths match the handoff |
| `6841172` D: before and after, scrubbing between two layouts | D | Pass with notes | A-42 |
| `7b72fdc` D: take the wall-clock limit out of the layout test | D | Pass | Resolves A-38 |
| `a10d6da` D: fix the audit's Lane D findings A-29 through A-39 | D | Pass with notes | Resolves A-29, A-31, A-33, A-35, A-37, A-39; A-30 documented; A-36 partly; introduces A-43; A-44 |
| `a00eda7` B: put agents and api on the local test path | B | Pass | Lets a plain local `pytest` import the audit tests' Lane C and API modules; 153 passed and 7 expected failures after it |
| `fff9e60` D: find a layout that fixes a finding, then try it | D | Pass with notes | A-45 |
| `2b0e2b0` D: the fixture counter stands 47 inches | D | Pass with notes | A-46; edits Lane C's `dataset.py` and `test_checks.py` again, as A-18 records; rule threshold still 36 in |
| `20f54d9` D: retry the stage that failed, and never call an unchecked shop a pass | D | Pass with notes | Resolves A-42, A-43 and A-44; older stored assessments lack `rules_checked` |
| `abb3cf6` D: record before and after, fix suggestions and the lawsuit counter | D | Pass | Progress file matches the code |
| `a6e14f7` Add the two LiDAR scans captured on the phone app: test1 and ravida | A | Pass with notes | A-47 to A-52; landed on a red `master` it did not cause |
| `94439b3` C: the ask box takes any question, not two phrasings | C | Pass with notes | Resolves A-41, CI green; A-54, A-55; A-26 still open |
| `d0947b8` B: keep every mesh identified on a scan off our own phone | B | Pass with notes | A-56, A-57 |
| `407c94c` D: merge the pitch deck into the web app at /present | D | Pass | Deck facts checked against their sources: 8,667 filings, $4,000, 96% and 42% of 3,611 case reports, the 47 in counter |
| `ee0304a` D: confirm the lawsuit counter height from the public complaint | D | Pass | Closes A-46 |
| `1ebff37` B: answer what sealed a route by rasterising without the node | B | Pass | Resolves A-40 |
| `2913c95` through `279ff84`, about thirty pushes from all four lanes | A-D | Suites pass | Not read line by line. At `b860fec`, 571 tests pass with 7 expected failures, including the mesh tests with Blender |
| `e7b331e` D: check a scan before its route, and let the owner mark the counter | D | Pass with notes | Resolves A-49; A-58, A-59 |
| `b39f625` D: say a layout passes only as far as the checks that ran | D | Pass | |
| `1955734` D: record counter marking, early checks and the phone origin fix | D | Pass | Progress file only |
| `feb65b4` C: keep running when Weave turns a key away | C | Pass | A rejected key or no network now leaves tracing off with a warning |
| `eb96dc4` D: the server traces its run to Weave | D | Pass with notes | Authored from Lane C into `services/api`, `start.sh` and `.env.example`, the cross-lane edit A-18 records; tests stand in for the Weave account |
| `dce5fde` C, D: note the Weave wiring in both progress files | C, D | Pass | Progress files only |
| `184e423` D: Fix what I can runs Lane C's loop and shows every pass | D | Pass | The loop runs on the search cache, routed by TypeSafe or the labelled local policy; kept moves add up per piece |

`609db3d`, `9028d14`, `b90e570`, `d3f7d95` and `1a06655` change only the plan and lane documents. A-1 covers the lane document errors from `9028d14`.

## Lane B against its done criteria

| Criterion | State |
|---|---|
| Tape-measured doorway matches the SceneGraph within 3 cm | Blocked on the first real scan from Lane A |
| Fixture pinch comes back as 31 in with the right coordinate | Met |
| Lane C calls the real functions instead of stubs | Met in `9ced7bc` |

Tasks 8 (Astra label) and 9 (Astra clean) wait on an OpenRouter key. Task 4 is covered by `usdz_to_glb`, which has not met a real RoomPlan export. `docs/archive/progress/PROGRESS_B.json` lists the 403.5.2 turn rule as done, which A-14 and A-15 contradicted until the audit fix.

## Findings

### A-1 Lane documents point at files that do not exist
Low. `fixed in 7fb2d22`.

`LANE_A.md` names `packages/fixtures/mock_api.py` and `python -m fixtures.mock_api`; the module is `standardphysics_fixtures.mock_api`, which the README gets right. `LANE_B.md` and `LANE_C.md` name `packages/fixtures/shop.room.json`; no room.json fixture exists, only `data/shop.scene_graph.json`. `LANE_C.md` names `packages/fixtures/stub_measurements.py`; it lives at `packages/fixtures/standardphysics_fixtures/stub_measurements.py`.

### A-2 The Blender self-test only works on a Mac with Blender in /Applications
Low. `fixed in 2a5f763`.

`check_blender.BLENDER` is hard-coded to `/Applications/Blender.app/Contents/MacOS/Blender`, with no fallback to `blender` on `PATH`. The lane document asks for a check every machine can run.

### A-3 Lane B pushes edit files outside Lane B's paths
Low. `blocked` on Boris agreeing who owns root files and `tests/`.

Lane B owns `packages/pipeline/**`, `docs/archive/progress/PROGRESS_B.json` and `docs/archive/handoffs/B-to-*.md`. Its pushes also changed `pytest.ini` (`a95c77e`), `.gitignore` and `.env.example` (`bb6e303`), `docs/archive/lanes/LANE_C.md`, `docs/archive/lanes/LANE_D.md` and Lane D's `packages/fixtures/standardphysics_fixtures/data/shop.glb` (`d0d0ea5`), and `.github/workflows/ci.yml` and `pyproject.toml` (`d2a982a`). No damage found, but the protocol says to write a handoff instead.

### A-4 CI was red from `1a6487e` through `1a06655`
High. `fixed in d2a982a`.

CI never installed `packages/pipeline`, so `numpy` was missing and `test_coords.py`, `test_ingest.py` and `test_measure.py` failed to import. Five pushes landed on a red `master`. `d2a982a` adds the package; CI is green on `d2a982a` and `d1cd65a`, and 54 tests pass in a clean environment.

### A-5 Solid obstructions under 9 inches are treated as open floor
High. `fixed in 77dd362`. Person C still needs to verify the 1/4 in value against the source.

`occupancy.BLOCKING_HEIGHT = 0.23` lets anything lower than 9 in be rolled over, citing toe clearance. Toe clearance is space beneath an element; it does not make a solid object passable. ADA 2010 303 allows at most 1/4 in of vertical change untreated and requires a ramp above 1/2 in. This breaks the lane rule that no allowance may shrink an obstacle.

Reproduced: a solid 7.9 in tall barrier across leg 0, 1.2 m from the entrance, reports **31.00 in, reachable**.

### A-6 The endpoint exemption hides obstructions at the entrance
High. `fixed in 1084818`. `Stop.anchor_node_id` landed in `b5306c8`. `0132fb2` made the exemption shrink on short legs but still ignores the anchor. `fd43203` decided against anchor-based exemption after an attempt regressed leg 1, and said the fix belongs in the scenario. Pinned by `tests/test_audit_open_findings.py`.

`routes.ENDPOINT_EXEMPTION` ignores every cell within 0.75 m of each stop so a counter does not set its own route's bottleneck. The entrance stop sits 0.3 m inside the front wall, so the doorway itself is exempt, and so is anything placed just inside it. The exemption was disclosed in the first `B-to-C.md` and dropped from the rewrite in `d1cd65a`.

Reproduced: a 20 in gap 0.2 m inside the entrance reports **31.00 in**. The same gap 1.2 m inside reports 20.00 in. Reproduced again at `0132fb2`.

### A-7 The measurement cache ignores object size
Medium. `fixed in 77dd362`.

`measure._signature` keys the cached grid on node ID, `m[3]`, `m[7]` and `m[0]`. Resizing an object reuses the old grid, and so does any rotation that leaves `m[0]` unchanged, such as the same angle in the other direction.

Reproduced: seal the aisle by widening `case_east` without moving it. The provider that already measured the shop returns **reachable=True, 0.00 in**; a fresh provider returns reachable=False.

### A-8 The counter approach ignores the counter's rotation
High. `fixed in 77dd362`.

`counter_approach` always places the 48 by 30 in clear floor space on the counter's minus-Y side, using the unrotated depth. The code calls this a forward approach, but a 48 in side running along the counter is the parallel-approach orientation.

Reproduced: rotate the counter 90 degrees. Its footprint spans x -0.35 to 0.35 and y 2.00 to 5.20, the approach centre lands at (0.00, 2.87) **inside the counter**, and the check returns **fits=True**.

### A-9 Door clear width reports the door leaf
Medium. `fixed in 49ce3f7`. `WidthResult.needs_measurement` landed in `b5306c8`; at `01582fd` `door_clear_width` still returns it as false. Lane C's door check in `8a161d1` already turns the flag into a request. Pinned by `tests/test_audit_open_findings.py`.

`door_clear_width` returns the larger dimension of the door node. ADA 2010 404.2.3 measures between the door face and the stop with the door open 90 degrees, which is narrower than the leaf. A door whose leaf is 32 in passes while its clear width fails.

Reproduced: the fixture's 0.900 m door reports 35.43 in with no deduction. `WidthResult` has no field for "needs a measurement", so an honest fix needs a contract change.

### A-10 The 180 degree turn width is the route width
Medium. Superseded by `28e64c1`, which measures the turn; see A-14 and A-15.

Build task 7 requires clear width at a 180 degree turn, which is a tier 1 check in `LANE_C.md` (ADA 2010 403.5.2). `turn_clear_width` returns `route_clear_width`. `B-to-C.md` discloses this, but `docs/archive/progress/PROGRESS_B.json` marks the provider done.

### A-11 Ingest invents a confidence when the export has none
Medium. `fixed in 77dd362`.

`ingest._node` reads `element.get("confidence", "high")`, so an element with no confidence becomes `quality="measured"`. The module says the parser raises rather than inventing a value, and the plan treats unverified geometry as needing another look.

### A-12 GLB export has no automated test
Low. `fixed in 750045b`. CI has no Blender, so only the committed file is checked there.

`export_glb` and `glb_node_names` have no test, and CI has no Blender. Checked by hand: the committed `shop.glb` has 19 nodes, each named with its SceneGraph node ID.

### A-13 Region loci are always axis-aligned squares
Low. `fixed in 2a5f763`. Callers still have to pass the counter's rotation and `circle=True` for a turning space.

`region_locus` draws an axis-aligned rectangle with a camera fixed to look from minus Y. A turning space is a 60 in circle, and a rotated clear floor space draws in the wrong orientation once A-8 is fixed.

### A-14 Turn widths measure the pivot's corner, not the gap
High. `fixed in 1d6a685`. Pinned by `tests/test_audit_open_findings.py`.

`28e64c1` measured each 403.5.2 width as grid clearance times two at points on the route. At a turn that is the distance to the pivot's corner rather than to the wall across from it, and the approach zone can start inside an occupied cell.

Reproduced on a room with 42 in lanes and a 48 in turn, which meets the rule: approaching **0.00**, at the turn **41.34**, leaving **41.34**, and `turn_clear_width` returns **0 in**. In `tests/test_turns.py`'s own U-shaped shop the leaving width reads 37.40 in against a real 149.61 in. Still reproduced at `01582fd`: a turn with 43 in lanes and 49 in at the turn reads 0.00, 43.31 and 43.31 in. The audit's fix measured each width as the exact footprint gap from the pivot to the obstacle facing it; it conflicted with `fd43203`'s rewrite of `turns.py` and is described in `docs/archive/handoffs/audit-to-B.md` for Lane B to apply.

### A-15 The 60 inch exemption never applies
High. `fixed in 1d6a685`. Pinned by `tests/test_audit_open_findings.py`.

The same undermeasurement reads a 60 in turn as 35.43 in, so 403.5.2's exception cannot trigger. Reproduced on a room with 36 in lanes and a 60 in turn: `in_scope=True`, `passes=False`. At `01582fd`, after `fd43203` removed those properties, a 61 in turn reads 35.43 in.

### A-16 The turn tests check no measured width
Medium. `fixed in 1d6a685`. `fd43203` rewrote `tests/test_turns.py` without checking a measured width. Pinned by `tests/test_audit_open_findings.py` until Lane B's own tests do.

`tests/test_turns.py` asserts only that each width is above zero and that a pivot exists, so A-14 and A-15 shipped with CI green, and `B-to-audit.md` reported A-10 fixed on that basis. Lane C's 105 tests also pass with and without the fix, so nothing downstream caught it either.

### A-17 A report camera can sit outside the room
Low. `fixed in 790381d`.

`a4eb74a` sizes the framing from the blocking objects. For a pinch between the south wall and a table 30 in from it, the camera lands at (-3.19, -3.57, 7.04), past the wall at x = -3. It is 7 m up, so the wall may not block the view. `tests/test_render.py` checks only the y coordinate.

### A-18 Lane D's push edits Lane C's files
Low. Noted.

`898984f` changes `packages/agents/standardphysics_agents/rules/pack.py` and `packages/agents/tests/test_rulepack.py`. The commit says Lane D's person approved it and asked for the one caller to be fixed in the same push; the protocol asks for a handoff instead. `1414cf4` also edits `tests/test_audit_lane_b.py` and Lane B's `tests/test_measure.py`, and `ceaa390` edits Lane C's `tests/test_fix.py`.

### A-20 Walls seal their doorways
High. `fixed in 0132fb2`. Reported by Lane D in `9dd969d`.

Reproduced at `28e64c1`: 19 of 19 samples across the front door belong to the south wall, and a stop 1 m outside the door cannot reach the counter. At `0132fb2` that route reaches the counter at 31 in.

### A-21 The Seat stop stood inside a table
Medium. `fixed in b5306c8`. Reported by Lane D in `9dd969d`.

Reproduced: Seat sat at (-2.0, -2.4), the centre of table_3, and leg 3 measured 6.89 in from inside the table. It now measures 79.23 in.

### A-22 A route width is taken from two obstacles the route never passes between
High. `fixed in e61229b`. Pinned by `tests/test_audit_open_findings.py`.

`measure._exact_width` reports the footprint gap between the two obstacles nearest the pinch whether or not the route passes between them. Leg 1, Counter to Pickup, reports **29.79 in** between the counter's corner and table_1. The counter spans x -1.6 to 1.6 and table_1 spans x -2.3 to -1.7, so the walk from x -0.8 to x 0.8 never goes between them, and the grid width at the pinch is **57.09 in** at `0132fb2` and at `01582fd`. `B-to-D.md` in `0132fb2` calls this a real pinch, and `C-to-B.md` treats it as a second route finding.

### A-23 Two graph hashes disagreed
High. `fixed in 898984f`.

At `b5306c8`, `contracts.graph_hash` and Lane C's `hashing.graph_hash` returned different hashes for the same fixture, so an assessment and a proposal could not agree on whether a layout was stale. At `898984f` they match, including after a change of quality.

### A-24 Lane C's tests did not run in CI
Medium. `fixed in 898984f`.

`pytest.ini` collects only `tests/`, and CI did not install `packages/agents`, so `9ced7bc`'s 105 tests never gated `master`. `898984f` installs the package and runs them.

### A-25 Real exports put the floor about 1.4 m below z = 0
High. `fixed in 5d09e4d`. Reported by Lane D in `01582fd`.

`blocks_floor` compares an object's top with 1/4 in above z = 0, but RoomPlan's origin is wherever the phone started. In Apple's sample exports the floor sits at z = -1.47 m in `apple_bedroom3` and -1.44 m in `apple_livingroom`, so the bed, the table and the chair in the bedroom, and 8 of 13 objects in the living room including both sofas, read as open floor. On a real scan a route would pass straight through furniture. At `5d09e4d` ingest shifts the room so the floor is at z = 0: every object in the bedroom blocks, and the three that do not in the living room hang above 27 in, where ADA 2010 307 treats them as protruding objects.

### A-26 The fix agent lets furniture overlap a wall by up to 1 cm
Low. `fixed in a2ed9d2`.

`fix/constraints.py` shrinks both the moved node and the obstacle by `OVERLAP_TOLERANCE` = 5 mm before testing for a collision, so the effective allowance is 10 mm, not the 5 mm its docstring states. Reproduced at `8a161d1`: sliding `case_east` 9 mm into the east wall reports no violation; 11 mm reports a collision. Re-measurement uses the real footprints, so no width passes on this, but the proposed arrangement cannot be built.

### A-27 CI was red at `1414cf4` and `fd43203`
Medium. `fixed in ceaa390`.

`1414cf4` shortened the display cases, which made Lane C's `test_the_documented_five_inch_fix_puts_a_case_inside_the_wall` fail on `master`. `fd43203` landed on top of the red build. `ceaa390` flipped the test to assert the move is now legal. `1414cf4` also rewrote the A-7 regression to move `case_east` as well as resize it, so the test no longer isolates a resize; a resize-only seal no longer blocks the route in the new fixture, so it was left as is.

### A-28 The new web workflow fails its typecheck
High. `fixed in 0aa64b8`. Lane D.

`7aa85c8` adds the "Web and contracts" workflow, and its first run fails at `npm run typecheck` with `src/app/layout.tsx(9,50): error TS2304: Cannot find name 'LayoutProps'`. `LayoutProps<"/">` is a route type Next.js generates into `.next/types`, and both `.next/` and `next-env.d.ts` are gitignored. The workflow runs `tsc --noEmit` before `next build`, so nothing has generated the type when `tsc` reads it. Generating the route types first, or typechecking after the build, would fix it. The "Generated types match the contracts" job in the same workflow passes. `0aa64b8` runs `next typegen` before `tsc`, and the workflow passes.

### A-29 A scan that fails processing can never be processed again
Medium. `fixed in a10d6da`. Lane D.

`a260626` marks a scan `failed` when its process job raises, and nothing queues it again. Reproduced at `a260626` with a `room.json` that is not JSON: the scan goes `failed`. The iOS app shows "Try the upload again" for that state, but its retry re-sends the same IDs and bytes, which return 200, and `complete` returns the scan still `failed` because it only acts on `uploading`. A readable `room.json` under the same ID is refused with 409. Under a new ID it is stored with 201, but `complete` still returns `failed`, and the worker would read the oldest `room_json` anyway (`artifact_of_kind` orders by `created_at`). Pinned in `tests/test_audit_open_findings.py` using the new ID path; if Lane D picks another recovery design, replace the test with one for that design.

### A-30 Starting a second API process runs the other process's jobs again
Low. `documented in a10d6da`, accepted: the demo runs one process. Lane D.

`worker.py` says a job runs once even when two API processes share a database, but `Worker.start` requeues every `running` job, including jobs another live process is running. Reproduced at `a260626`: worker A claims the process job, worker B starts, and both run it. `python -m standardphysics_api` starts one process, so this only bites if a second one is started against the same `var/`. A lease with an expiry, or requeueing only jobs owned by this process, would match the docstring.

### A-31 A finding's render can come from an older revision
Medium. `fixed in a10d6da`. Lane D.

`GET /api/scans/{id}/renders/{finding}.png` picks `sorted(glob("*/renders/<finding>.png"))[-1]`. The revision directories sort as strings, so revision `9` sorts after `10`, and finding IDs do not include the revision (`findings.py` hashes the scan ID and rule key). Once a scan passes ten revisions, a finding shows its revision 9 image. When this was found no endpoint created a revision. The same route answers `not ready` for an unknown scan where every other route says `no scan`.

`a9ce65c` adds `POST /api/scans/{id}/revisions`, so revisions now pass 9. Reproduced at `9be20af` on the sample shop: after eleven saved layouts the latest assessment is revision 11, and the render route serves the image rendered for revision 9. Pinned in `tests/test_audit_open_findings.py`.

### A-32 CI is red at `0f0e01b`: Lane C's turn check crashes on an unmeasured zone
High. `fixed in 586f765` and `d74f0e7`. Lane B change, Lane C file.

`0f0e01b` makes `Turn.approach_inches`, `at_turn_inches` and `leaving_inches` `float | None`, so an unmeasured zone no longer reads 0.0 in. That part is right. `checks/turn_width.py` in Lane C still compares every zone with a number, so `assess` raises `TypeError: '<' not supported between instances of 'NoneType' and 'float'` on the fixture shop, whose leg 1 turn now measures None/57.1/78.7 in. CI fails on `pytest packages/agents` (19 failed, 11 errors) and `services/api/tests` (the sample shop's assess job fails, so it never becomes `ready`). The commit message reports 140 tests passing, which is the root suite only.

Both lanes fixed it within two minutes. Lane C's `586f765` skips a turn with a missing or zero zone and records the gap, and Lane B's `d74f0e7` withholds such a turn from `turn_detail` by default. The audit withdrew its own patch to `turn_width.py` before it was pushed and kept two regressions in `tests/test_audit_lane_c.py` that hold under either design: the fixture shop assesses without raising, and no turn finding comes from an unmeasured zone. All three suites pass at `9be20af`.

The new 2.5 m minimum route for a turn was checked against a 36 in turn in rooms 2.5 to 4.0 m deep: every room that reported a turn before `0f0e01b` still does, with the same at-turn width.

### A-33 The viewer says a failed scan is still being checked
Low. `fixed in a10d6da`. Lane D.

`Workspace.tsx` shows "Checking your shop" whenever a scan has a scene, no findings, and a state other than `ready`. A scan whose ingest succeeded and whose assess failed is `failed` with a scene, so it reads as in progress forever. That is the sample shop on `master` right now, because of A-32. A `ready` scan with no findings shows "Findings show up here once the shop is checked", which contradicts the shops page's "Everything we checked passes" for the same scan; with no rules verified, every real scan lands there. Read from the diff: the list page uses `scanStatus`, the workspace does not.

The rest of `2fad000` checked out: the web view loads `/scans/<server scan id>` from the upload response, the `nativeCapture` message and `scanShop` action match `WorkspaceScreen.swift`, `Locus.camera` is required by the contract, and the Z-up to Y-up conversion, wall cut and region outline are correct.

### A-34 A turn that could not be fully measured disappears without a trace
Low. `fixed in eb28c10`. Lane B and Lane C.

Since `d74f0e7`, `turn_detail` returns None for a partly measured turn unless the caller passes `require_measured=False`. Lane C's `586f765` records such a turn as unevaluated, but only when it receives one, so that path no longer runs. Checked at `d74f0e7` on the fixture shop: leg 1's turn exists with `require_measured=False`, the assessment's unevaluated list holds only `exit_path`, and no turn finding is reported. Neither the owner nor the team is told a turn was seen and not checked, so a turn nobody measured reads the same as no turn. Both lanes' handoffs suggest turning it into a question for the owner.

### A-35 Two saves on the same layout both succeed, and one is lost
Medium. `fixed in a10d6da`. Lane D.

`save_layout` in `layout.py` compares `base_revision` with the latest revision outside the write transaction, then inserts with `INSERT OR IGNORE`. A save whose check passes before another save writes the same revision has its insert silently ignored, and still returns 201 with its own layout as the new revision. Reproduced at `9be20af` by landing a save that moves `case_west` inside a save that moves `case_east`, both on revision 0: both return revision 1, and the stored revision 1 holds only the `case_west` move. Two clients saving on the same base at nearly the same moment can hit it. Checking the latest revision inside the transaction, and treating an ignored insert as a conflict, would fix it. Pinned in `tests/test_audit_open_findings.py`.

### A-36 A box model exported from a later revision is moved twice
Low. `partly fixed in a10d6da`. Lane D.

`945b8a4`'s `page.tsx` passes revision 0 to the viewer as the layout the GLB was exported from. The worker exports display geometry on the first display job that finds no GLB, and when the scanned mesh cannot be converted it falls back to boxes built from that job's revision (`worker.py` `_display`, `stages.geometry`). If revision 0 exported nothing, for example with Blender missing, and a later saved revision exports boxes, those boxes already stand where that revision put them, and `displayMatrix` applies the move from revision 0 again. The scanned-mesh path is unaffected, because the USDZ always holds the original layout. Read from the diffs of `a260626` and `945b8a4`. Recording which revision a GLB was exported from, and giving the viewer that graph, would close it.

`a10d6da` serves `X-Exported-Revision` from the revision whose job wrote the GLB, and the viewer places meshes from that layout. That fixes boxes. A scanned mesh converted from the USDZ always holds the original layout, though, and `_store_geometry` records the job's revision for both paths, so when the first successful conversion happens on a later revision the viewer now places those meshes from the wrong layout. Read from `a10d6da`.

### A-37 A save refused as stale tells the owner to fix red pieces, and every retry fails
Low. `fixed in a10d6da`. Lane D.

`useArrangement.save` answers every failure with "That layout couldn't be saved. Check the pieces marked in red." A 409 for a stale base, meaning someone else saved first, marks nothing red. The page only refreshes after a successful save, so `scene.revision` stays stale and every retry sends the same base and gets 409 again until the page is reloaded. Read from `945b8a4`. Telling the two 409s apart by their `error` body, and refreshing the scene on a stale base, would let the owner recover. A-35 is the race the server's check misses; this is the refusal it does make.

### A-38 CI is red from `a9ce65c`: a wall-clock limit in the layout test
High. `fixed in 7b72fdc`. Lane D.

`test_the_documented_fix_clears_the_aisle` in `services/api/tests/test_layout.py` asserts that one layout check finishes in under 3.0 s. Lane D's own profile in `9be20af` puts a check at 1.7 to 2.3 s on a development machine, and CI took 3.42 s at `9be20af`, so the API step fails with 1 failed and 28 passed. CI failed on both `a9ce65c` and `9be20af`, every run finished since the test arrived. The speed-up Lane D asked Lane B for, routing each leg once per layout, is the real fix. Until then, a time limit on shared CI runners belongs in a benchmark, not the unit suite.

### A-39 A preview report says a person verified every rule
Medium. `fixed in a10d6da`. Lane D.

`report.py` sets `check.verified_by_human = True` on every rule the ledger verifies, and `preview_ledger` records every rule under the reviewer "unverified preview (development only)". Reproduced at `d201984` with the preview ledger: all 17 rules come back with `verified_by_human: true`. The printed table shows the preview reviewer's name, but the contract field says the opposite, and nothing at the top of the printed report marks it as a preview. Setting the flag from whether the reviewer is the preview reviewer, and marking a preview report at the top, would keep the two from disagreeing.

### A-40 A sealed route names a display case that is not sealing it
Medium. `fixed in 1ebff37`. Lane B.

`what_sealed_the_route` in `022004d` asks, for each object, whether removing its cells reconnects the two sides of the route. The occupancy grid records one owner per cell, so where two objects overlap, removing one frees cells the other still covers. Reproduced at `022004d` with the fixture aisle sealed by stretching `case_east` wall to wall: the route names `case_west` and `case_east`, but taking `case_west` away alone leaves the route blocked, and only `case_east` or a wall reopens it. `case_west` keeps all 2,304 of its cells after the stretch, which is why `_would_open` reports it. The fix agent is then pointed at a case that cannot clear the aisle. Pinned in `tests/test_audit_open_findings.py`: every object named must reopen the route when it alone is removed.

### A-41 CI is red at `022004d` by design: Lane C's labelled case expects the old behaviour
High. `fixed in 94439b3`. Lane C label, Lane B change.

`022004d` makes a blocked route name its obstacles, so the router now picks `FIX` for the `blocked_but_movable` case, whose label still expects `ASK_OWNER`. `test_the_router_picks_the_right_action_every_time` fails with a score of 0.96875, 31 of 32 cases, reproduced locally at `022004d`. The commit and `B-to-C.md` say so and leave the one-line label change to Lane C, which respects path ownership, but `master` stays red until Lane C takes it. CI on `022004d` also carries A-38.

### A-42 Right after a save, before and after show the same findings
Low. `fixed in 20f54d9`. Lane D.

`page.tsx` loads the latest assessment that exists, and `6841172` compares it with the previous revision's own assessment. A save writes the new revision at once and assesses it in a queued job, so until that job finishes the scene is the new revision while the latest assessment is still the old one. Reproduced at `6841172` on the sample shop: right after saving the documented fix, `scene` is revision 1, `assessment` is revision 0, and `assessment?revision=1` answers 404. With the preview rules, the panel then shows 3 things to fix on both sides of a layout that clears one, and "Fixed by this layout" is empty. The page refreshes once more after 3 s, so a slower assessment leaves it stale until a reload. Asking for `assessment?revision=<scene revision>` and showing "Checking" on a 404 would keep the two sides honest.

### A-43 Retrying a scan whose assessment failed leaves it measuring forever
Medium. `fixed in 20f54d9`. Lane D. Introduced by `a10d6da`.

`a10d6da`'s retry in `_finalize` sets any `failed` scan to `measuring` but requeues only failed `process` jobs. A scan also fails when its `assess` job raises, and then nothing is queued. Reproduced at `a10d6da` on the sample shop with an `assess` stage that raises: the scan is `failed` with one failed `assess` job, `complete` answers `measuring`, and after the worker drains the scan is still `measuring` with the same failed job. The iOS app polls until a scan is ready or failed, so it would wait forever. Before `a10d6da` the same scan stayed `failed`. Pinned in `tests/test_audit_open_findings.py`: with `assess` still broken, a retried scan must end `failed`, not `measuring`.

### A-44 With no rule verified, a scan reads "Everything we checked passes"
Medium. `fixed in 20f54d9`, for assessments made from then on. Lane D.

Until a person reviews the rule pack every check is off, and `assess` returns an assessment with no findings. Reproduced at `a10d6da` with the default ledger: the sample shop is `ready` and its assessment has 0 findings. `scanStatus` turns an assessment with nothing needing attention into "Everything we checked passes", which the shops page has shown since `2fad000` and the workspace shows since `a10d6da`'s change for A-33. Nothing was checked, so it reads as a clean pass. `assess(...).unevaluated` records why, but the assess stage only logs it. Carrying the count of evaluated rules into the assessment, and saying that no rules are switched on yet when it is zero, would keep an unchecked scan from looking compliant.

`20f54d9` adds `Assessment.rules_checked`, and `scanStatus` says checks start once a person reviews the rules when it is `0`. An assessment stored before `20f54d9` has no `rules_checked`, and `scanStatus` treats only `0` as unchecked, so an older assessment with no findings still reads "Everything we checked passes" until the scan is assessed again. A database created before `20f54d9`, such as a demo machine's, keeps such assessments; treating a missing count as unchecked would cover them. The A-43 test passes as a plain test at `abb3cf6`, with 155 root and 37 API tests passing.

### A-45 Asking for a fix stalls every drag check for about three seconds
Low. `fixed in 5dd31f6`. Lane D.

`fff9e60`'s `Stages.propose` runs Lane C's fix search inside the same lock as `assess`, which the layout check also takes. Measured at `2b0e2b0` on the sample shop with the preview rules: a layout check alone takes 1.49 s, one proposal takes 3.33 s, and a layout check sent 0.3 s after a proposal starts takes 4.21 s. Queued assessments wait behind a proposal the same way. The lock protects Lane B's measurement cache, so giving the fix search its own `PipelineMeasurements` would likely let a drag check run alongside it.

### A-46 The lawsuit behind the fixture counter is only partly verified
Low. `verified in ee0304a` from the public complaint. Lane D.

`2b0e2b0` sets the fixture counter to 47 in and cites Whitaker v. T Rock Inc., N.D. Cal. No. 5:22-cv-00283, complaint paragraph 12. The case exists: CourtListener lists Whitaker v. T Rock Inc., 22-cv-00283-JST, filed January 14, 2022, over the Happy Lemon shop in San Jose. The 47 in figure, the paragraph number and the `5:` division prefix could not be confirmed from public sources, because the complaint is behind PACER. Search results also describe the plaintiff as a serial ADA filer, including a dismissal reported by CBS San Francisco. Before the pitch names a real business and plaintiff, someone should read the complaint and decide whether this is the example to lead with.

The public complaint settles it. Its header reads Case 5:22-cv-00283-VKD, filed 01/14/22, and paragraph 12 puts the counter about 47 inches above the finish floor after a December 2021 visit, so the figure, the paragraph and the `5:` prefix all hold; the earlier doubt about the prefix came from a search snippet. Paragraph 12 also says the shop had a lowered section and the transaction happened at the higher counter, which matters to how the pitch frames the fix. The deck's other facts in `407c94c` match their sources.

### A-47 The coverage engine never reaches "done" on a real scan
High. `open`, partly fixed in `93a720c`. Lane A, and a decision for the people in it.

`93a720c` fixed why walls read zero: the engine replayed only the cameras that had seen a surface's previous shape, so each RoomPlan refinement discarded earlier views. It now replays every camera. Replaying the committed poses against each final room the way the engine does, with nothing else changed:

| Policy | test1 walls | test1 all surfaces | ravida walls | ravida all surfaces |
|---|---|---|---|---|
| Code: 90%, 3 views, 3 m, 50° | 2 of 9 | 2 of 26 | 0 of 8 | 0 of 25 |
| Documents: 70%, 2 views, 5 m, 60° | 9 of 9 | 10 of 26 | 7 of 8 | 8 of 25 |
| Documents, with faces against a wall or other furniture left out | 9 of 9 | 11 of 26 | 7 of 8 | 8 of 25 |
| Same, and confidence asked only of walls, floors, doors, windows and openings | 9 of 9 | 15 of 26 | 7 of 8 | 11 of 25 |

No policy that needs every surface finished completes either scan. What stops it, beyond the thresholds: RoomPlan reports every chair at medium confidence, which no amount of walking changes; furniture sides pressed against walls and other furniture cannot be seen; and the floor needs walking over, because from standing height a floor point more than about 2.4 m away is seen at over 60 degrees. The ravida wall that stays at 1% is a 0.38 m stub. Deciding what "Room surfaces covered" should require, for example the walls and openings only, is the call for Lane A; the table is the data for it.

`CoverageSnapshot.isComplete` needs every surface done, and `SurfaceCoverage.isDone` at `a6e14f7` asked for 70% observed area, two viewpoints and high confidence. Replaying the committed `coverage.json` against the confidence in `room.json`:

```
== test1  surfaces=26  done@0.70/2vp=2  done@0.90/3vp=0 | walls 9: 2 / 0
== ravida surfaces=25  done@0.70/2vp=5  done@0.90/3vp=3 | walls 8: 5 / 3
```

`test1` ran 239.58 s, hitting the four minute cap, and finished with 2 of 26 surfaces done. Two of its walls sit at `observed_fraction 0.000, viewpoint_count 0` while RoomPlan reconstructed them at high confidence, so the frustum, normal and distance test rejected all 440 poses for them. No object in `test1` passes 0.660, so the gate is unreachable however long the owner walks. Neither scan could have shown "You've got the whole shop", and the lane document's criterion that walking half a room marks exactly that half done reads the other way: a fully walked room marks 8% done.

`CoveragePolicy` has since moved to 0.90 observed, three viewpoints, 3 m and 50 degrees, which takes `test1` to 0 of 26 and away from the 70%, two viewpoint, 5 m, 60 degree rule in `LANE_A.md` and `docs/archive/PLAN.md` section 3. Either the documents or the policy is wrong.

### A-48 A scan manifest claims an artifact kind the contract did not define
Medium. `fixed in 6f704a5`. Lane A raises it, Lane D owns the contract.

Both `datasets/phone/*/scan.json` list `"kind": "lidar_mesh"`, and the commit says the checksums match the uploaded artifacts. At `a6e14f7` `ArtifactKind` was `room_usdz`, `room_json`, `room_metadata`, `walkthrough_mp4`, `frames`, `poses`, `coverage`. Validating each listed file against the contract rejects exactly one in both scans:

```
CONTRACT REJECTS lidar-mesh.json -> Input should be 'room_usdz', 'room_json', 'room_metadata',
  'walkthrough_mp4', 'frames', 'poses' or 'coverage' [input_value='lidar_mesh']
```

`lidar_mesh` reached the contract twelve commits later in `6f704a5`, a Lane A commit that wrote `packages/contracts/`, `services/api/` and `apps/web/`. Changing anything in `packages/contracts/` is on the protocol's must-not-decide-alone list.

### A-49 A real uploaded scan never gets a Scenario, so nothing about it is ever checked
High. `fixed in e7b331e`. Lane D.

Reproduced by pushing `test1`'s six uploadable artifacts through the real app:

```
create 201 18e99b14-8643-47b4-9532-766ab6cdf2ae uploading
complete 200 measuring
scan state: ready   coverage entries: 26
assessment: 404 {"error":"not ready"}
scenario:   404 {"error":"not ready"}
scene: 200 nodes 26
```

`worker._assess` reads `repo.get_scenario(...)` and, when it is `None`, skips `stages.assess` and marks the scan `ready`. The only caller of `repo.save_scenario` is `seed.py`, which runs for the seeded sample shop. Every real capture therefore ingests, turns `ready`, and carries no assessment. `scanStatus` short-circuits on `assessment === null` and returns "Ready", which `Workspace.tsx:188` prints as the whole findings panel. Distinct from A-44, where the assessment existed with no findings; here there is none.

### A-50 Neither real scan contains a door or an opening
Medium. `open`. Lane A captures it; blocks Lane B and Lane C.

```
test1:  doors=0 openings=0 windows=0
ravida: doors=0 openings=0 windows=1
```

`docs/archive/PLAN.md` section 1 measures the path in the door, and Lane C's tier 1 includes ADA 2010 404.2.3 door clear width. With no door node neither check has an input, and Lane B's outstanding criterion that a tape-measured doorway match the SceneGraph within 3 cm is still blocked after the scan arrived. The doorway subtraction in `occupancy.CUTS_THROUGH_WALLS` and the A-20 fix have nothing to act on.

The walls also do not enclose either room: summing wall lengths against the floor extent gives `test1` 11.23 m of wall around a 43.6 m perimeter, and `ravida` 18.15 m around 36.4 m. Seven of `test1`'s nine walls are 0.36 to 0.48 m slivers, 4.4 m tall, and they are the same walls A-47 shows were never observed.

### A-51 Two mesh files are most of the git history
Low. `open`. Lane A.

The two mesh blobs are 31,840,294 and 26,631,156 bytes, and `du -sh .git` is 17M, so about 15 MB of a 17 MB history is two JSON arrays of floats, permanently. gzip -9 takes the same content to 8,153,561 and 6,835,790 bytes. `.gitignore` has no rule for `datasets/`. A binary form, or keeping the mesh out of git and referencing it by checksum, leaves the scan just as reproducible.

### A-52 A hand-written manifest mirrors a contract model
Low. `open`. Lane A.

`datasets/phone/*/scan.json` uses `scan_id`, `captured_at` and `files[].file/kind/artifact_id/sha256/bytes`; `contracts.Scan` uses `id`, `created_at`, `artifacts[]`, `coverage[]` and `content_hash`. `docs/archive/PLAN.md` section 5 makes `packages/contracts` the only source of truth for a shape. The manifest is useful and should either take the contract shape or become a model in contracts.

### A-53 A new top-level `datasets/` tree belongs to no lane
Low. `open`. Lane A and the humans.

All 14 files in `a6e14f7` land under `datasets/phone/`, which no lane document claims: Lane A owns `apps/ios/**`, B `packages/pipeline/**`, C `packages/agents/**`, D contracts, fixtures, api and web. No handoff, README or lane document says what the tree is or that it is now the canonical place for a real scan. `docs/archive/handoffs/B-to-A.md` had asked for the export at `packages/fixtures/standardphysics_fixtures/data/real/`, where the existing `tests/test_real_ingest.py` parameterisation already looks, so the real-export suite did not pick these up and Lane B wrote fresh cases instead. The commit also carries no lane prefix, which the protocol asks for. This is the Lane A analogue of A-3.

### A-54 Setting the OpenRouter key breaks every model call
Medium. `open`. Lane C.

`models.py` imports `openai` only when a key is configured, and `packages/agents/pyproject.toml` does not declare `openai`, so a clean install has no such module. `OpenRouter.structured` calls `self.client()` before its `try`, so the import error escapes instead of becoming a `Rejected` the caller falls back from. Reproduced at `94439b3` with a key set and no `openai` installed: `ModuleNotFoundError: No module named 'openai'`. Still true at `279ff84`. The failure appears only once someone sets the key the plan asks for, which is the moment the demo depends on it. Declaring `openai` and moving the client construction inside the `try` would close it.

### A-55 The model calls ask providers not to train, but do not ask for zero retention
Medium. `open`. Lane C.

`PROVIDER_ROUTING` sends `"data_collection": "deny"`, and the docstring calls that "the per-request half of zero data retention". OpenRouter's provider routing documentation separates the two: `data_collection` controls whether to use providers that may store data, and a distinct `zdr` field restricts routing to zero data retention endpoints. The plan requires zero data retention for scans of the inside of a shop, and `zdr` is not set. Still true at `279ff84`. Adding `"zdr": True` and correcting the docstring would match the requirement.

### A-56 Every real scan's meshes are named in upper case, so the viewer shows boxes
High. `open`. Lane B, with the viewer lookup in Lane D.

`usdz_to_glb` names each mesh with the identifier from RoomPlan's mapping file, which is upper case. `parse_room_json` gives nodes `str(UUID)`, which is lower case, and the viewer finds a node's mesh with a case-sensitive `meshes.get(node.id)`. Reproduced with Blender at `d0947b8` and again at `279ff84`: `apple_bedroom3` converts with `fully_identified` true, yet 0 of its mesh names match a node id exactly and 11 match ignoring case; `apple_livingroom` 0 and 19; `test1` 0 and 26; `ravida` 0 and 25. No scanned mesh is ever placed, and every real scan renders as boxes. `fully_identified` does not notice because it counts renames, not matches. Pinned in `tests/test_audit_open_findings.py`, skipped where Blender is missing, so CI does not run it.

### A-57 Each phone scan element converts twice, and the copy cannot match its node
Medium. `open`. Lane B.

A phone export carries each element once as a parametric box and once as a mesh. `d0947b8` maps both to the same identity by stripping Blender's `.001`, but renaming the second object to a name the first already holds makes Blender add `.001` back. Reproduced with Blender at `d0947b8` and `279ff84`: `test1` has 88 names, and all 26 of its nodes appear as both `<id>` and `<id>.001`; `ravida` likewise for 25 nodes. Which of the two a viewer picks up is arbitrary, and the copy can never match a node. Keeping one object per element, or naming the parametric one distinctly, would give each node one mesh. Pinned in `tests/test_audit_open_findings.py`, skipped without Blender.

### A-58 Unmarking a counter leaves the object locked
Low. `open`. Lane D.

`labels._as_counter` sets `label`, `labeled_by` and `movable: False`; `_as_scanned`, the undo, restores only the label. Reproduced at `1955734` on the sample shop by marking `table_1` and unmarking it: `movable` goes from true to false and stays false, `labeled_by` stays `owner`, and the label comes back as the raw category `table` rather than the scanned `Table`. The owner cannot drag a piece they marked by mistake. Restoring `movable`, `labeled_by` and the label from the revision before the mark would make it an undo.

### A-59 The counter approach is measured on a side no customer may use
Medium. `open`. Lane B measures it, Lane C and Lane D rely on it.

`counter_approach` places the 30 by 48 in clear floor space in front of the counter's local minus-Y face. On the fixture that face looks into the shop, but a real RoomPlan object's axes say nothing about which side customers stand on, and the owner-marking flow in `e7b331e` has no way to say so either. Reproduced at `1955734` with `test1`'s storage object, 0.49 by 0.52 m, marked as the counter: as scanned the approach measures 45.3 by 0.0 in and fails, and Lane C reports "There's not enough room to pull up to the service counter" at 0.0 in. Turned 90 degrees, the same object has 145.7 by 39.4 in of clear floor, which fits. Measuring every face and using the most open one, or letting the owner say which side is the front, would keep a real scan from failing on the wrong side.
