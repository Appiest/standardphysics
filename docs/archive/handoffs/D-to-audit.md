# D to audit: A-6 and A-9 fields are approved

Brendan, Lane D's person, approved both fields as proposed in
`audit-to-D.md`. They land in the next Lane D push, with defaults that keep
today's behaviour.

- **A-6:** `Stop.anchor_node_id: UUID | None = None`. The fixture scenario
  anchors Entrance and Exit to the front wall, Counter and Pickup to the
  counter, and Seat to table_3.
- **A-9:** `WidthResult.needs_measurement: bool = False`.

Two things the audit may want to track:

- **Sealed doorways.** Walls seal their doorways in `occupancy.build_grid`.
  Reproduced at `28e64c1`: all 17 samples across the front door are occupied,
  and a stop 1 m outside the door cannot reach the counter. See `D-to-B.md`.
- **The Seat stop.** It sat at the centre of table_3, so leg 3 measured 6.89 in
  from inside a table. The fixture moves it into open floor.

---

## Lane D findings, answered

| Finding | State | Fix |
|---|---|---|
| A-38 | fixed in `7b72fdc` | The 3 s wall-clock assertion is gone. Speed is tracked in `D-to-B.md` with the profile. |
| A-29 | fixed | `complete` on a `failed` scan queues its process job again, and the worker reads the newest `room_json`. Your pinned test passes, so its `xfail` marker is removed. |
| A-31 | fixed | Render lookup sorts revision directories as numbers. `xfail` removed. |
| A-35 | fixed | `save_layout` checks the latest revision inside its write transaction, and owner saves use a plain `INSERT`, so a lost race is a 409. `xfail` removed. |
| A-39 | fixed | `verified_by_human` is false for the preview reviewer. `Report.preview` marks such a report, and the printed page opens with a notice. |
| A-37 | fixed | A stale save returns `"a newer layout was saved since this one started"`. The web reloads the newer layout and says so, instead of pointing at red pieces. |
| A-33 | fixed | The workspace uses `scanStatus`, so a failed scan reads as failed. Only a scan that has an assessment with no findings says everything passes. |
| A-36 | fixed | `scene.glb` answers with `X-Exported-Revision`, and the viewer places meshes from that revision's layout. |
| A-30 | documented | The worker docstring now says to run one API process per database. The demo runs one, with `workers=1`. |

## A-42, A-43 and A-44

| Finding | State | Fix |
|---|---|---|
| A-43 | fixed | A retry queues every failed stage except display, and sets `measuring` or `checking` to match what it queued. A scan with nothing failed is left alone. Your pinned test passes, so its `xfail` marker is removed. |
| A-44 | fixed | `Assessment.rules_checked` is the number of tier 1 rules the ledger verified when the assessment ran. With 0, both the shops page and the workspace say "Checks start once a person reviews the rules" rather than a pass. |
| A-42 | fixed | The shop page asks for the assessment of the revision on screen. While that is missing and the scan is still being worked on, the panel says "Checking this layout" and the page refreshes every 2 s. Before and after appears only once both sides have their own assessment. |

## A-46: the complaint is public, and it confirms the 47 inches

RECAP has a public copy of the complaint:
`https://storage.courtlistener.com/recap/gov.uscourts.cand.390547/gov.uscourts.cand.390547.1.0_1.pdf`.
Its header reads `Case 5:22-cv-00283-VKD Document 1 Filed 01/14/22`. Paragraph
12 reads: "the sales counter was too high. While there was a lowered section,
transactions take place at the higher counter, which is located about 47 inches
above the finish floor. The point-of-sale machines were located on the higher
counters." Paragraphs 2, 3 and 8 name Happy Lemon at 919 Story Rd., San Jose,
and a December 2021 visit. The fixture's 47 in, the paragraph and the `5:`
prefix all hold. Whether the pitch should name the business and plaintiff is
for the team, and I have raised it with Brendan.

## A-48 and A-49

- **A-48.** Lane A's `a6e14f7` burst added `lidar_mesh` to `ArtifactKind`,
  along with the `LidarMesh` contract and its validation, so the manifests now
  validate. Lane D reviewed those contract additions and kept them.
- **A-49.** Fixed in `edfa652`. A real scan gets a suggested route from
  `GET /api/scans/{id}/scenario/suggestion`. `PUT /api/scans/{id}/scenario`
  saves the route the owner confirms and queues the assessment. On `ravida` that
  produces findings. `services/api/tests/test_route.py` covers both phone scans.

## A-45

Fixed. `Stages.propose` and the new `Stages.ask` run on `search_measure`, a
second `PipelineMeasurements` under its own `_search_lock`. A fix search or a
question no longer waits on, or holds up, a layout check or a queued
assessment. `services/api/tests/test_ask.py` holds the search lock and checks
that a layout check still answers.
