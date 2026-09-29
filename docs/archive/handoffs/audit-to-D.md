# Audit to D: two contract fields the measurements need

Both come from findings in `docs/archive/progress/PROGRESS.md`. Each is an optional field whose default keeps today's behaviour for every existing caller. Nothing in `packages/contracts/` has been edited; the protocol leaves that to you and your person.

## A-6: a stop needs to say which fixture it is at

Route width ignores everything within 0.75 m of each stop, so a counter does not set its own route's bottleneck. That also hides a 20 in gap 0.2 m inside the entrance, which reports 31 in.

The pipeline cannot tell a stop's own fixture from an obstruction beside it, because `Stop` holds only a name and a position.

**Proposal:** `Stop.anchor_node_id: UUID | None = None`. Route width then ignores only that node near the stop, and a stop with no anchor gets no exemption. The fixture scenario would anchor Counter and Pickup to the counter, Seat to its table, and Entrance and Exit to the front wall.

## A-9: a width needs a way to ask for a measurement

`door_clear_width` returns the door leaf. ADA 2010 404.2.3 measures from the door face to the stop with the door open 90 degrees, which a scan does not capture. Today a door with a 32 in leaf passes.

**Proposal:** `WidthResult.needs_measurement: bool = False`. `door_clear_width` sets it, and Lane C turns the result into a question with a photo request instead of a pass.

Both landed in `b5306c8`.

---

## The API in `a260626`

The upload contract matches what Lane A's app sends: paths, both headers, snake_case bodies, artifact IDs and kinds, the coverage dictionary, and 200 or 201 on upload. `services/api/tests` passes, 23 of 23. Three findings, details and repros in `docs/archive/progress/PROGRESS.md`:

- **A-29, medium.** A scan that fails processing stays `failed` for good. The app's "Try the upload again" re-sends the same IDs, which return 200, and `complete` ignores anything not `uploading`. A corrected `room.json` gets 409 under the same ID, and under a new ID the worker would still read the oldest one. Pinned as a strict expected failure in `tests/test_audit_open_findings.py` using the new ID path. If you choose a different recovery design, replace that test with one for your design and delete the marker.
- **A-30, low.** `Worker.start` requeues every `running` job, so a second API process on the same database runs the first one's jobs again. That contradicts the docstring in `worker.py`.
- **A-31, low, not reachable yet.** The render route sorts revision directories as strings, so `9` beats `10`. It bites once an edit endpoint creates revisions.

## The viewer in `2fad000`

- **A-33, low.** `Workspace.tsx` says "Checking your shop" for any scan with a scene and no findings that is not `ready`, so a `failed` scan looks in progress forever. A `ready` scan with no findings says "Findings show up here once the shop is checked", while the shops page says "Everything we checked passes". Using `scanStatus` in the workspace would make the two agree.

## Rearranging in `a9ce65c`

- **A-35, medium, pinned.** `save_layout` checks the base revision outside its write transaction and inserts with `INSERT OR IGNORE`, so a save that loses a race to another save on the same base returns 201 while its layout is dropped. Reproduced at `9be20af`; details in `docs/archive/progress/PROGRESS.md`. Checking the latest revision inside the transaction and treating an ignored insert as a 409 would fix it.
- **A-31 is now reachable, pinned.** Saved layouts pass revision 9, and after eleven saves on the sample shop the render route serves revision 9's image for a revision 11 assessment. Sorting the revision directories as numbers fixes it.

## Dragging in `945b8a4`

The browser's `moveNode` matches Lane C's `move_node`, pointer deltas reach the floor with the right sign, and stale check answers are dropped by sequence.

- **A-37, low.** A 409 for a stale base shows "Check the pieces marked in red" with nothing red, and with no refresh every retry sends the same stale base. Reading the `error` body and refreshing the scene on a stale base fixes both.
- **A-36, low.** The viewer treats revision 0 as the layout the GLB came from. If the first GLB is a box export from a later revision, which happens when revision 0 exported nothing, its boxes are moved twice. Recording which revision a GLB was exported from would close it.

## CI is red from `a9ce65c` (A-38), and the report's verified flag (A-39)

- **A-38, high.** `test_the_documented_fix_clears_the_aisle` asserts a layout check under 3.0 s. CI took 3.42 s at `9be20af`, and CI failed on both `a9ce65c` and `9be20af`. Routing each leg once per layout is the real fix; until then the time limit belongs in a benchmark, not the unit suite.
- **A-39, medium.** With the preview ledger, `GET /api/scans/{id}/report` returns `verified_by_human: true` for all 17 rules, each reviewed by "unverified preview (development only)". Set the flag from the reviewer, and mark a preview report at the top of the page.

## `6841172` and `7b72fdc`

- **A-38 is fixed in `7b72fdc`.**
- **A-42, low.** Right after a save the page pairs revision 1's layout with revision 0's assessment, because it asks for the latest assessment rather than the scene's revision. Before and after then show the same findings until the queued assessment lands and the page happens to refresh. Request `assessment?revision=<scene.revision>` and show "Checking" on a 404.

## `a10d6da`, checked

Verified by running code: the A-29, A-31 and A-35 tests pass without their markers, eight concurrent saves on one base give one 201 and seven 409s, and a preview report has `preview: true` with no rule marked `verified_by_human`. A-33 and A-37 read correctly, and A-30 as documented is accepted.

- **A-43, medium, introduced here, pinned.** `complete` on a `failed` scan sets `measuring` but requeues only failed `process` jobs. When `assess` was the job that failed, nothing runs and the scan stays `measuring`, so the app waits forever. Requeue whichever job failed, or leave the scan `failed` when there is nothing to retry.
- **A-44, medium.** With no rule verified, the sample shop is `ready` with 0 findings, and `scanStatus` says "Everything we checked passes" on both the shops page and the workspace. Say that no rules are switched on yet instead.
- **A-36, partly fixed.** `X-Exported-Revision` is right for boxes. A GLB converted from the USDZ holds the original layout whatever revision's job made it, so it should report revision 0.

## `fff9e60` and `2b0e2b0`

- **A-45, low.** A proposal holds the assess lock for about 3.3 s on the sample shop, so a drag check sent during one took 4.21 s instead of 1.49 s. Giving the fix search its own measurement provider would likely let drags run alongside it.
- **A-46, low.** The case is real (CourtListener: Whitaker v. T Rock Inc., 22-cv-00283-JST, Happy Lemon, San Jose), but the 47 in figure, paragraph 12 and the `5:` prefix could not be confirmed without the complaint. Read it before the pitch names the business, and weigh that search results describe the plaintiff as a serial filer.
- `2b0e2b0` edits two numbers in Lane C's files. The handoff says so and the change is mechanical, but it is the cross-lane edit A-18 records.

## The Lane A burst `1ebff37..24eecbc`

- **A-49, high, pinned.** A real uploaded scan never gets a Scenario, so nothing about it is ever checked. This is the demo path.

Reproduced twice, once from the phone artifacts and once from a fixture export:

```
put room-json room_json 201 / put room-usdz room_usdz 201
complete 200 -> measuring
state      : ready
assessment : 404 {"error":"not ready"}
scenario   : 404 {"error":"not ready"}
scene      : 200 nodes 11
```

`worker._assess` reads `repo.get_scenario(...)` and, when it is `None`, skips `stages.assess` and marks the scan `ready`. The only caller of `repo.save_scenario` in the repository is `seed.py`, which runs for the seeded sample shop. The sample shop is checked and every real scan is not.

`scanStatus` then short-circuits on `assessment === null` and returns "Ready", and `Workspace.tsx:188` prints that string as the entire findings panel. An owner who scans their shop sees the word "Ready" and no findings.

This is not A-44. There the assessment existed with no rule verified; here there is none.

Giving an uploaded scan a Scenario closes it. The stops can come from the graph — an entrance at the doorway, the counter at the largest fixture with a register, a seat in the seating cluster — or be asked of the owner on the scan screen. Either way `_assess` must stop treating a missing Scenario as a reason to call a scan `ready`. A scan that could not be checked is not ready. Pinned at `tests/test_audit_open_findings.py::test_a49_a_real_scan_that_is_ready_has_been_checked`; delete the marker in the push that fixes it.

- **A-48, medium.** `lidar_mesh` was added to `ArtifactKind`, along with `packages/contracts/standardphysics_contracts/lidar.py`, `services/api/lidar_mesh.py` and eight `apps/web/src/` files, by Lane A in `6f704a5`. Contracts are your exclusive write, and the protocol puts a contract change on the must-not-decide-alone list. Confirm the shape is what you want before it sets.

## A-56 touches the viewer's mesh lookup

`ShopModel` finds a node's mesh with `meshes.get(node.id)`. Lane B's converter currently names meshes in upper case, so nothing matches on a real scan and it renders as boxes. The fix belongs in the converter, but a lower-cased lookup would also keep the viewer safe from either side's casing. A-46 is closed by your complaint check in `ee0304a`.

## `e7b331e`: counter marking (A-58, A-59)

- A-49 is fixed. Checks without a route look right: no route rule runs, and no finding mentions the placeholder stops.
- **A-58, low.** Unmarking restores the label but leaves `movable: False` and `labeled_by: owner`, so a piece marked by mistake stays locked. Restore the fields from the revision before the mark.
- **A-59, medium.** On `test1`, the object you mark is measured from its local minus-Y face, which has 0 in of clear floor, so it fails "not enough room to pull up"; a side 90 degrees round has 39.4 in and fits. The measurement is Lane B's, but the marking flow could ask which side customers stand on.
