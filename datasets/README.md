# datasets

Real captures the tests run against, so a check is proven on a room somebody walked instead of one somebody typed out. Nothing here ships: `.dockerignore` keeps the folder out of the production image.

It comes to about 65 MB, and 94% of that is three LiDAR meshes. They stay in git because many tests read them with no skip, including the iOS tests in `ios.yml`, and Git LFS would charge its bandwidth quota on every CI checkout.

| Path | Size | What it is | Read by |
|---|---|---|---|
| [`phone/test1`](phone/test1) | 31 MB | A 4-minute iPhone walk exported exactly as the app uploads it: RoomPlan's `room.json` and `room.usdz`, the LiDAR mesh, camera poses and coverage | 15 tests across the pipeline, agents, API, web and iOS, the evaluation captures, `ios.yml` and three scripts |
| [`phone/ravida`](phone/ravida) | 26 MB | A second 2.5-minute walk in the same format | 9 tests (6 of them the API's), the fine-tuning data builders, the shop pilot and the evaluation captures; only `tests/test_display_of_real_scans.py` reads its mesh |
| [`replays/living-room`](replays/living-room) | 6 MB | One 110-second walk with its cached detections, for testing discovery on a real room | `packages/pipeline` discovery and object-shape tests |
| [`replays/moffitt-regions`](replays/moffitt-regions) | 1.8 MB | Two corners of Moffitt Library after carving | The region-growth tests |
| [`replays/moffitt-floor`](replays/moffitt-floor) | 0.3 MB | A whole Moffitt floor's scene graph, 704 nodes from four walks | The display-export timing test |
| [`replays/moffitt-bins`](replays/moffitt-bins) | 0.1 MB | The carved candidates around a row of four bins | The bin-merging test and a web motion test |
| `external_sources.json` | 2 KB | Pinned licences and revisions of public indoor-scene datasets under review for training | `scripts/stage_external_sources.py` |

Each `replays/` folder has its own README saying exactly what was recorded and why it is kept.
