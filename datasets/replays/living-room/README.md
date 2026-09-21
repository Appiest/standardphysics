# living-room

One 110-second walk from the iPhone app, kept so discovery can be tested against
a real room instead of a room somebody typed out.

| file | what it is |
| --- | --- |
| `room.json` | RoomPlan's export: 29 measured surfaces and objects |
| `poses.json` | 218 camera keyframes at 2 Hz, version 2 metadata |
| `lidar-mesh.json` | the AR mesh, every third anchor kept |
| `detections.json` | what a vision model returned for each of the 218 photos |

**The photos themselves are not here.** People walk through 59 of them. The
detections were recorded once from those photos and committed instead, which
keeps the test repeatable, free to run, and clear of anybody's face.

`packages/pipeline/tests/test_discovery_on_a_real_scan.py` reads this and skips
when it is absent.

**The detections here were read from sideways photos.** The phone was held
upright and stores its frames as the sensor delivers them, on their side, and
discovery did not turn them upright before asking. So these answers name a
laptop on somebody's knees a chair. They are kept because the test reads
structure from them rather than meaning: that a real mesh and real poses
produce objects of a sensible size in sensible places. Re-record them against
upright frames when there is budget, and the names will be worth reading too.
