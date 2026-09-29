# Handover: object discovery and the scanned surface

Everything here was run against **jerry's house**, a real 110-second capture:
218 photos, 218 camera poses, a LiDAR mesh. No fixtures, no sample data.

Suites: `pytest packages/agents/tests packages/pipeline/tests tests` and
`pytest services/api/tests`, run separately because two suites share file
names. Both green.

## What works end to end

Capture on the phone, upload, ingest, object discovery, people removed from
the mesh, ADA checks with citations and 3D locations, a still rendered per
finding, the photo-painted scan, the web viewer, drag-to-rearrange with live
re-checking, and the printed report.

**Marking the customer route is not optional.** Without one the route rules
are stripped and the shop reads as eight door findings. With one, 14 rules run
and five findings pin to objects RoomPlan never boxed.

## The one idea worth carrying

RoomPlan's generated boxes **do not sit on the real surfaces**. Measured
against the LiDAR under each box: a chair 21 inches out, a bench 20, a window
seat 12, stairs 9, a sofa 8. That single fact caused four separate bugs, and
anything still misbehaving should be suspected of it first.

| symptom | why |
| --- | --- |
| A keyboard painted flat on a desk | photos projected onto a plane floating above the real surface slide sideways with viewing angle |
| Objects fitted as wide flat slabs | the real tabletop was never claimed by the oversized box, so it stayed in the pool and clustered together with the laptop |
| Objects missing entirely | a laptop on the real tabletop sits *inside* a box 21 inches too tall, so it was marked already-measured and deleted. **103 of 118 laptop rectangles died this way** |
| Sparse, speckled box textures | most of a tabletop disagrees with the scan by more than any tolerance, so it is rejected |

Fixed by: dropping the surface a thing rests on before clustering, letting
furniture claim its body but not its top 10 cm, and painting the scan rather
than the boxes.

## Settled, do not redo

**Photos are stored sideways.** The phone is held upright and its sensor
delivers landscape. The detector was reading rooms rotated ninety degrees and
calling a laptop a chair. Frames are stood upright using the orientation the
phone records, and boxes are turned back into sensor pixels before anything
projects through them. Projection itself is untouched and correct: model
geometry lands exactly on the backpack in a real frame.

**Three theories I tested and killed**, so nobody spends the night on them
again: the photo/geometry alignment is right; the LiDAR mesh *is* loaded into
the bake, 492,428 triangles; and the depth tolerance is not the cause of the
smearing (tightening it moved 20 rejected samples to 22 out of 196).

**Counting frames is not counting looks.** Keyframes land twice a second, so a
dozen in a row are one viewpoint seen twelve times. Counting separate places
the phone stood dropped 55 objects to 27 and, with them, a turn falsely
reported at 30.6 inches against a requirement of 48, and a route falsely
reported at 26.4 against 36. **We were reporting violations that were not
there.**

## Known limits

**Only one laptop of three or four.** After every fix, 85 of 97 laptop
rectangles still hold fewer than 25 LiDAR points. I swept voxel size and the
point floor: 12 to 15 carve either way. Apple's scene reconstruction smooths a
laptop into the desk, so the geometry mostly is not there. This is a hardware
ceiling, not a threshold. Anyone attacking it should work from the depth map
rather than the fused mesh.

**Discovered footprints are about 8 inches out**, heights about 3. A phone
walking past a cabinet only sees its front, so depth comes back as the
thickness of the photographed surface. Route findings should be read off
RoomPlan's nodes; discovered objects mean "something is here", and their
`quality` field says which are worth trusting. I tried extending wall-backed
objects to the wall and it made footprints worse, 8.6 inches to 13.5, so it is
reverted rather than sitting in the tree.

**People are removed from discovery's point cloud, not from the painted
scan.** Someone sitting on a sofa during the capture is still in the coloured
mesh. The same person-rectangles already computed could mask those vertices;
nobody has wired it.

## Next, roughly in order

1. **Take people out of the painted scan.** The detections exist, the mask
   logic exists in `discovery/people.py`, and the scan painter does not use it.
2. **The scan glTF is 31 MB.** The decimation in `blender_scripts/colour_scan.py`
   is not firing; it should thin to 260k triangles and does not. A phone will
   struggle until it does.
3. **Only the route findings reach discovered objects.** Counter height, reach
   ranges and clear floor space all ignore them.
4. **Re-record `datasets/replays/living-room/detections.json`.** Those answers
   were read from sideways photos, so the names in them are wrong. The replay
   test reads structure rather than meaning, so it still passes, but the file
   should be re-made now that frames are read upright.

## Running it

```bash
./start.sh                       # API on :8787, web on :3000
```

The API needs `SP_PREVIEW_UNVERIFIED_RULES=1` for checks to run at all; without
it the ledger is empty, no rule is verified, and the shop correctly reports
that checks have not started.

`.env` holds the keys and is gitignored. Discovery reads photos through
`DISCOVERY_API_KEY` / `DISCOVERY_BASE_URL` / `DISCOVERY_MODEL`, currently
Fireworks, falling back to the OpenRouter values. **The OpenRouter key is
spent**, $9.50 of $10.

Detections are cached per photo under `services/api/var/scans/<id>/detections`,
keyed by the photo's bytes, the model, and which way up it was shown. A rebuild
of an already-read capture costs nothing and takes seconds; a fresh capture is
about 218 requests.

— B
