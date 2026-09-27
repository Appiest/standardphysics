# moffitt-bins

The carved candidates from one corner of the "moffitt full floor" scan
(`b9c4029c-50a7-49bd-90b0-94f19c714f70`), where a row of four bins stands a few
feet from a pair of long upholstered benches.

| file | what it is |
| --- | --- |
| `candidates.json` | the 22 bin, bench and sofa detections whose carved boxes land in that corner, in carve order |
| `candidate-points.npz` | the room-frame LiDAR points each one was carved to, as `points_00` onward |

They were produced by replaying discovery on the server against its cached
detections, stopping after carving and before merging. The box of each
candidate is `fit_box` of its points, exactly as `carve` returns it.

**No photos are here**, only mesh points and the rectangles a vision model drew.
The detector drew the four bins as four rectangles in frames 30021, 30039 and
30048, which is what `test_merging_a_real_row_of_bins.py` holds the merge to.
