# moffitt-regions

Two corners of Moffitt Library as discovery sees them after carving, recorded
from the production walks' cached detections so the growth step can be tested
on real geometry.

| file | what it is |
| --- | --- |
| `entrance.json`, `entrance-points.npz` | the south walk (`5e66b9c6`) around the security gates and the service desk |
| `platform.json`, `platform-points.npz` | the east walk (`67b1fc64`) around the low wooden platform |

Each `.json` holds the walls and floors in that corner (`sheets`) and the
detections whose carved boxes land there (`candidates`, in carve order). Each
`.npz` holds `unclaimed`, the mesh points no measured node owns, and
`points_000` onward, the points each candidate was carved to.

**No photos are here**, only mesh points and the rectangles a vision model drew.
