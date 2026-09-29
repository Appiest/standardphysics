# B is building object discovery. Nobody else take this.

Finding the things in a shop that RoomPlan never boxes — the payment terminal,
the monitor, the laptop, the kettle — and giving each one a measured box so the
checks can reason about it and the owner can move it.

## Why it is needed

RoomPlan returns Apple's residential furniture categories. A shop is full of
objects outside that list, and today they reach us as LiDAR and nothing else.
On the real `test1` capture, **only 9.7% of the mesh sits inside any node box**,
and **104,000 points stand between 0.4 m and 2.0 m** with no object at all.
Every one of those is something a customer can walk into and we cannot name.

## What it does

One new package, `packages/pipeline/standardphysics_pipeline/discovery/`.

| Module | Job |
| --- | --- |
| `detect.py` | One vision pass per frame: what is in the picture, and the rectangle around it |
| `people.py` | Every person's surface taken out of the mesh before anything measures it |
| `carve.py` | A rectangle plus the mesh becomes one measured box |
| `clusters.py` | Separates an object from what it stands on and what is behind it |
| `merge.py` | The same object seen from several frames becomes one object |
| `boxes.py` | What a known node already owns, and what a new object rests on |
| `discover.py` | Runs the whole pass and emits `SceneNode`s |

**The photo only supplies a name and a rectangle. Every number comes from the
LiDAR.** A confident wrong label produces a mislabelled box, never a wrong
measurement. New nodes carry `labeled_by="discovery"`, `parent_id` set to the
surface they rest on, and `quality="needs_another_look"` when only one frame
saw them.

The vision model is configurable through `DISCOVERY_MODEL` and defaults to
`google/gemini-3.8-flash`, chosen because it returns pixel rectangles rather
than prose. It is not Astra: Astra keeps its job of naming the boxes RoomPlan
already made.

A frame the model cannot read raises and is counted in
`DiscoveryResult.failures`. Nothing here returns an empty list to mean the
network was down.

## What changed outside my lane

- `packages/contracts/.../scene.py` — `LabelSource` gains `"discovery"`. Additive.
- `packages/pipeline/.../lidar.py` — new; the mesh loader that was private to
  `textures/bake.py` now lives here, and `bake.py` imports it. One definition.

## What a capture must carry

Discovery needs **version 2 pose metadata** — `frame_id`, `image_width`,
`image_height`, `calibration_width`, `calibration_height`, and
`image_orientation: "sensor"` — and it needs the **frames uploaded** as
`frames` artifacts alongside `poses` and `lidar_mesh`.

**The two captures in `datasets/phone/` predate this.** Both carry version 1
poses with no frame ids, and neither uploaded any frames. `load_cameras` on
them raises `no photo has version 2 camera metadata`. Texture baking needs the
same thing, so this blocks both features until a capture from the current app
lands.

## Lane A: what I need from the phone

1. A capture whose `poses.json` records are `metadata_version: 2`.
2. Frames uploaded as `frames` artifacts, with the photo manifest.
3. People filtering left on. `SceneCaptureConfiguration` already requests
   `personSegmentationWithDepth`; server-side removal is a second pass over
   what still gets through, not a replacement.

## Lane C and D: what changes for you

Nothing yet. Discovery runs after ingest and adds nodes to the same
`SceneGraph`, so rules, measurements and the viewer see them as ordinary
objects. A discovered node is movable when the model judged it movable, so the
fix agent may propose moving a terminal.

— B
