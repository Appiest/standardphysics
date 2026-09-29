# B is verifying the photo-to-model maths. Nobody else take this.

## What I am doing right now

`packages/pipeline/standardphysics_pipeline/textures/camera.py` has **no tests**.
It is the module every photo-driven feature stands on — texturing, labelling,
and the object identification in `docs/OBJECTS.md` — and a sign error in it is
invisible: every projection lands somewhere plausible, just wrong.

I am writing the tests that pin the conventions, before anything else is built
on top of it:

- a point straight ahead of the camera lands on the principal point
- **ARKit +Y is up, so a higher point lands higher in the image** (smaller row).
  This is the one that catches a flipped sign and the one I care most about
- a point to the right lands right of centre
- a point behind the camera reports depth <= 0
- the floor drop in `capture_to_room` puts a floor point at room z = 0
- `resized()` moves the principal point but not the principal ray, so the
  calibration resolution and the stored JPEG resolution agree

If any of those fail I will report it rather than fix it quietly, because the
answer changes what `textures/` and `astra.py` mean.

## Please do not

- Add tests to `packages/pipeline/.../textures/` — I am in that directory.
- Change `coords.capture_to_room` or `ARKIT_TO_PIXEL_AXES` until this lands.

## What I read to get here

Four audits of the whole codebase are written up in `docs/OBJECTS.md`, pushed
earlier. The parts that matter to other lanes:

- **An empty verification ledger makes an unchecked shop look compliant.**
  `rules_checked=0` and an empty findings list are saved and served with no
  signal that nothing ran. This is the most serious thing found and it is not
  mine — flagging it for whoever owns the report.
- **The scanned GLB renders about 1.4 m below the room.** `usdz_to_glb` never
  applies the floor drop that ingest applies to everything else. The box
  fallback is correct, so this only bites when the scanned path succeeds.
- **A door erases whatever overlaps it.** `_punch` clears occupancy
  unconditionally, so shelving in a doorway disappears and the route reads open.
- **Closed doors are passable.** `isOpen` is parsed at ingest and discarded.
- **After the first save the LiDAR mesh never returns** to the viewer, in any
  mode, though it stays on disk and is still served.

## Jerry: two things you already fixed

Frame artifact ids now come from the filename rather than a position, and
`photo-manifest.json` binds poses to frames with checksums. Both were on my
blocker list this morning and both are closed. Thank you.

---

## Result: the projection is correct

13 tests in `packages/pipeline/tests/test_camera.py`. Every convention holds:

- a point straight ahead lands on the principal point
- **a higher point lands higher in the image** — ARKit +Y is up, image rows count
  down, and the sign is right
- right is right, and a point behind the camera reports depth <= 0
- depth is metres along the view direction
- the floor drop moves the camera and the room together, so the same physical
  point measures the same depth at any floor height
- `resized()` scales the focal length and moves the principal point without
  moving the principal ray, so calibration resolution and stored JPEG agree
- a version-1 record is refused rather than projected with a guessed resolution

One thing I got wrong and the code got right: I expected a floor point to appear
*above* the image centre. It appears below, because the camera stands 1.4 m above
the floor and is looking down at it. The code was right; my expectation was not.

**`PoseRecord.projectable` requires `image_orientation == "sensor"`**, so a
rotated image is refused rather than silently projected onto the wrong thing.
That is the correct answer to the orientation question: the JPEG is written
straight from the pixel buffer and never turned, the intrinsics describe it as
stored, and rotating it is a display concern that must not reach this maths.

`pytest.ini` did not include `packages/pipeline/tests`, so nothing in that
directory has ever run in CI. Added. Whole suite: 817 passed, 4 xfailed.
