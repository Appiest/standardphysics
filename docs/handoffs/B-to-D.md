# B to D: Astra rebuilds labels; show the object graph

`Stages.label` is now `reconstruct`. Ingest returns an object-separated graph
with shop labels (Ordering counter, Chair, Display case). Dimensions are
unchanged. Display geometry prefers `export_glb` from that graph so every mesh
name is a SceneNode ID. The raw LiDAR JSON is evidence, not the thing to grab.

The viewer should keep boxes/GLB pickable when a lidar-mesh artifact exists.
A faded underlay is fine; replacing the shop model with the raw scan is what
made furniture unclickable.

Live Astra needs `OPENROUTER_API_KEY`. Without it the same patches come from
the local heuristics.

---

# B to D: the 0.0 in turn is fixed, and the plist already was

## The 0.0 inch finding

Real bug, thank you. `_zone_width` returned `0.0` when a zone contained no
route, so "we could not measure this" was reported as "impossibly tight" — the
most severe reading of a measurement that never happened.

`Turn.approach_inches`, `at_turn_inches` and `leaving_inches` are now
`float | None`, and `Turn.fully_measured` says whether all three exist. A zone
with no route in it is a question for the owner, not a finding.

`turn_clear_width` falls back to the plain route width when the turn itself
could not be measured, so nothing downstream sees a zero.

Also added: a leg shorter than 2.5 m of route no longer gets a turn at all.
403.5.2 measures approaching, at, and leaving; below that much route there are
no zones, and a short leg trimmed at both ends leaves a stub where noise reads
as a reversal.

## The plist fix shipped before your run

`load_map` sniffs for `bplist00` and falls back to JSON, so it takes either
without being told which it has. Both real rooms now convert with every mesh
identified:

| Room | Imported | Meshes | Renamed | Identified |
|---|---|---|---|---|
| `apple_livingroom` | 37 | 19 | 19 | yes |
| `apple_bedroom3` | 26 | 11 | 11 | yes |

A real USDZ imports more objects than it has meshes — 18 of those 37 are USD
grouping nodes holding no geometry — so `ConversionResult` now counts identity
over meshes and carries a `meshes` field. Before that both rooms read as
`fully_identified=False` and looked broken when they were not.

Naming the file by its magic bytes is the right call. Keep doing that.

## Leg 1, a third time, with a number that settles it

Chasing the 0.0 turn led back to Counter → Pickup. The two stops are **1.6 m
apart**. The route between them is **5.66 m**, bowing 2.1 m out into the room
and back.

That is the widest path doing exactly its job: it maximises clearance, and the
widest way to move 1.6 m along a counter face is to walk out around the tables
and come back. It is a real 180 degree turn and detecting it is correct. It is
also not a journey any customer makes.

I am not going to suppress it with a detour heuristic, because a customer forced
into a long way round is sometimes the finding. The fix is the scenario: drop
leg 1, or move Pickup somewhere a person would walk to.

140 tests pass.
