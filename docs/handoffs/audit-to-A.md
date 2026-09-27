# Audit to Lane A

Findings from `a6e14f7` and the twelve-commit burst `1ebff37..24eecbc`. Reproductions are in `docs/progress/PROGRESS.md`.

## A-47 The coverage engine never reaches "done" on a real scan (High)

Your own committed data says the completion gate is unreachable. Replaying `datasets/phone/*/coverage.json` against the confidence in `room.json`:

```
== test1  surfaces=26  done@0.70/2vp=2  done@0.90/3vp=0 | walls 9: 2 / 0
== ravida surfaces=25  done@0.70/2vp=5  done@0.90/3vp=3 | walls 8: 5 / 3
```

`test1` ran 239.58 s, hit the four minute cap, and finished 2 of 26. Two of its walls read `observed_fraction 0.000, viewpoint_count 0` while RoomPlan reconstructed them at high confidence, so the frustum, normal and distance test rejected all 440 poses for those surfaces. No object in `test1` exceeds 0.660.

What to do:
- Use the committed `poses.json` and `room.json` as a replayable fixture. A wall RoomPlan reconstructed at high confidence from a four minute walk must not come back at zero observed area. That case belongs in `CoverageEngineTests.swift`.
- Then re-derive the thresholds against real data rather than upward. `CoveragePolicy` now asks 0.90 observed, three viewpoints, 3 m and 50 degrees, which takes `test1` to 0 of 26.
- `LANE_A.md` and `docs/PLAN.md` section 3 both say 70% observed, two viewpoints at least 1 m apart, within 5 m, under 60 degrees. The code no longer matches either. Change the documents with the reason or restore the values; right now a reader cannot tell which is intended.

## A-50 Neither real scan contains a door or an opening (Medium)

```
test1:  doors=0 openings=0 windows=0
ravida: doors=0 openings=0 windows=1
```

Lane B's remaining done criterion is a tape-measured doorway matching the SceneGraph within 3 cm, and Lane C's tier 1 includes ADA 2010 404.2.3 door clear width. Both are still blocked after the scan arrived. The walls also do not enclose either room: `test1` has 11.23 m of wall around a 43.6 m floor perimeter, and seven of its nine walls are 0.36 to 0.48 m slivers 4.4 m tall.

The capture guidance needs to walk the owner to the entrance and along each wall. A third scan that contains a door unblocks two lanes.

## A-48 A manifest claims an artifact kind the contract did not define (Medium)

`datasets/phone/*/scan.json` lists `"kind": "lidar_mesh"`, which `ArtifactKind` rejected at `a6e14f7`; it reached the contract twelve commits later in your `6f704a5`. Changing `packages/contracts/` is on the protocol's must-not-decide-alone list, and it is Lane D's exclusive write. Ask Lane D in `A-to-D.md` and let them make the change.

## A-51, A-52, A-53 (Low)

- The two mesh blobs are 31.8 MB and 26.6 MB, so about 15 MB of a 17 MB `.git` is two JSON arrays of floats, permanently. A binary form, or keeping the mesh out of git behind a checksum, loses nothing.
- `scan.json` hand-writes a shape that mirrors `contracts.Scan`. Give it the contract shape or add the model to contracts.
- `datasets/` belongs to no lane document, has no README, and `B-to-A.md` had asked for the export under `packages/fixtures/standardphysics_fixtures/data/real/`, where `tests/test_real_ingest.py` already looks. Claim the path in `LANE_A.md` or move the data. `a6e14f7` also carries no lane prefix.
