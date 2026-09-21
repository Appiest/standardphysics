# Seeing what is actually in the room

Four audits of the codebase, and a plan for the thing the product cannot do yet:
know that a payment terminal is a payment terminal.

Every claim here was read out of the code or measured from a real scan. Where
something is uncertain it says so.

---

## 1. The gap, in one number

RoomPlan detects a fixed residential category list: table, chair, storage, bed,
sink, oven, dishwasher, sofa, television, fireplace, stairs. It has no category
for a payment terminal, a register, a display case, a queue rail or a monitor.

On `datasets/phone/test1`, a working office:

| | |
|---|---|
| Mesh points captured | 554,250 |
| Points inside any object box | 53,579 (**9.7%**) |
| Unclaimed points between 0.4 m and 2.0 m | **104,000** |

On the scan airdropped from the phone, a room containing six monitors, two
laptops, a speaker, backpacks and framed posters came back as **six chairs and
three tables**, with zero doors and zero windows.

That band between waist and head height is where a card reader lives, and ADA
2010 308 and 309 are about exactly that object.

### What this costs, check by check

`checks/roles.py` resolves "which box is the thing this rule is about" by exact
string match on `node.label`. `ingest.py:116` sets that label from RoomPlan's
category verbatim. So on a real scan:

| Check | Status today | Needs |
|---|---|---|
| `service_counter_height` | cannot fire — a counter scans as `storage` | a node labelled from `SERVICE_COUNTER_LABELS` |
| `service_counter_approach` | cannot fire — same gate | same |
| `point_of_sale_height` | **unreachable on every path that exists**, including owner marking: it needs both a lowered section and a POS node, and `mark_counter` only ever writes `"service counter"` | a `card reader` node and a `lowered counter section` node |
| `protruding_objects` | cannot fire — needs wall-mounted nodes RoomPlan never emits | mounted nodes with `parent_id` set to their wall |
| `reach_range`, `restroom_turning_space` | wired to `_no_nodes`; permanently a question with `subject: None` | operable-part nodes |
| `dining_surface_height` | degraded — works, but cannot tell a dining table from a side table | a real role, not a category |

And one consequence nobody asked for: `ingest._is_movable` makes every
non-plumbed object movable. A built-in counter scans as `storage`, so it is
movable, so the fix agent will cheerfully propose sliding it across the room.
The only thing preventing that today is the owner pressing "mark as counter",
which also sets `movable=False` as a side effect.

---

## 2. What we already capture and then throw away

The phone records more than the pipeline reads. These are not missing features;
they are collected, uploaded, stored, and never opened.

| Data | Captured | Read by |
|---|---|---|
| `frames` — 2 Hz JPEGs at full camera resolution | yes, one artifact each | **nothing, anywhere** |
| `poses` — per-frame transform + intrinsics | yes, 440 entries on test1 | **nothing** |
| `walkthrough_mp4` — H.264 1280x960 | yes, 41 MB on one scan | **nothing** |
| `coverage` — per-surface observed fraction | yes | parsed, stored, served; no consumer in `packages/` or the web app |
| `lidar_mesh` — 554k points, 112 parts | yes | the viewer, on revision 0 only. Never reconciled against the boxes |
| ARKit per-face classification | **requested** via `.meshWithClassification` | never read. `LidarMesh.Part` has no field for it |
| Person segmentation | **requested** via `.personSegmentationWithDepth` | never read |

Two of those deserve emphasis.

**ARKit already labels every mesh face.** `.meshWithClassification` makes the
phone classify each triangle as wall, floor, ceiling, table, seat, window, door
or none. We ask for it, the device spends the power computing it, and
`LidarMesh.init(anchors:)` reads only `vertices` and `faces`. It is free
segmentation evidence, discarded at the source.

**People are not filtered.** `peopleFilteringEnabled: true` appears in every
mesh file and in `A-to-D.md`, and it is a recorded claim rather than an
operation. Nothing reads the segmentation buffer; nothing removes person
geometry from the mesh or masks a pixel in a frame. A person standing still gets
meshed as furniture. The frames we have contain four people.

---

## 3. Bugs found, worst first

**3.1 An unchecked shop is indistinguishable from a compliant one.**
`rules/data/verification.json` ships as `{"entries": []}`. With no verified
rules `pack.enabled()` returns `[]`, `run_checks` runs nothing, and the
assessment is saved with `findings=[]` and `rules_checked=0`. The one signal
that nothing was checked, `Pass.unevaluated`, is logged server-side and dropped
before `save_assessment`. A shop owner can be shown a clean report about a
building nobody examined. For an accessibility product this is the most
serious thing in the audit.

**3.2 The scanned GLB renders 1.4 m below the room.** Ingest shifts every node
so the floor sits at z=0, and every height rule, the ground plane, annotations
and stop markers depend on it. `usdz_to_glb.py` imports RoomPlan's USDZ, whose
origin is wherever the phone started, renames the meshes and re-exports without
subtracting the floor. When the scanned path succeeds the room floats below its
own floor; once furniture moves, moved nodes snap to the shifted position and
unmoved ones do not, so the scene sits at two heights at once.

**3.3 A frame cannot be matched to the pose that took it.** `poses.json` refers
to images by filename. `ScanExporter` uploads them under positional ids
(`frame-%04d`) assigned *after* failed frames are filtered out. Lose
`frame_0003.jpg` and `frame_0004.jpg` uploads as `frame-0003`. Nothing carries
the mapping. Any vision pass that projects geometry into "the frame that saw it"
would silently use the wrong camera.

**3.4 Recovery discards a video that exists.** `RecordingResult.recovered` hard
codes `videoURL: nil`, and `RoomCaptureController` falls back to it on any
recoverable stop failure. A good MP4 on disk is dropped. Separately,
`CaptureRecovery` writes `[]` into a missing `poses.json`, producing a file that
looks like a clean capture of nothing. Between them these explain `test1` and
`ravida` arriving with complete poses and no imagery.

**3.5 Frames upload in a race they can lose silently.** Optional artifacts run
concurrently with the completion poll. If the scan reaches `ready` first the
remaining frames are cancelled, and the error path only reports when the state
is *not* ready — so a truncated frame set produces no message at all. `POST
/complete` fires before any optional artifact is attempted.

**3.6 A door erases whatever overlaps it.** `_punch` clears occupancy
unconditionally, so shelving parked in a doorway is erased along with the door
and the route reads as open.

**3.7 Every door is a hole.** `isOpen` is parsed at ingest and discarded; closed
doors are passable.

**3.8 An overlapped object cannot be named.** `grid.owner` records one owner per
cell, first claimer wins. `_nearest_owners` and `blockers_at` rank by cells a
node owns, so a node whose cells were all claimed by an overlapping neighbour is
dropped from the answer even when `_would_open` has just proved it is the thing
sealing the route. This is the sibling of A-40, which was fixed only inside
`_would_open`.

**3.9 A stop inside furniture can snap through a wall.** `_nearest_free` takes a
clearance argument and never uses it, snapping by Manhattan distance over all
free cells.

**3.10 The scan disappears after the first save.** `capturedMeshUrl` returns the
mesh only for `revision === 0` outside layout preview. Rearrange once and the
product is a box model permanently, in every mode, though the mesh is still on
disk and still served. The GLB is likewise built once for revision 0 and never
re-exported.

**3.11 `label_accuracy` measures nothing.** The scorer runs the role finders
over the case's own input graph and compares against labels the same fixture
author wrote. It scores hand-written labels against themselves and reports 1.0
without ever inspecting what the checks attached to.

---

## 4. What the model can and cannot do

Settled by experiment, not argument.

**It cannot invent geometry, and must not.** LiDAR measured the room. A model
asked to produce room dimensions would produce fiction, and every number
downstream — every clearance, every finding, every report handed to a
contractor — would inherit it.

**It can build a mesh to measurements we supply.** Given hard constraints
0.32 x 0.18 x 0.11 m and "card payment terminal", `openai/gpt-6-astra` wrote 300
lines of Blender Python; our headless Blender ran it and produced a sloped body
with a screen recess, a keypad and a card slot, measuring **0.3200 x 0.1800 x
0.1100**, sitting on z=0, 8,548 vertices. The dimensions were verified
programmatically before anything was rendered.

That is the whole contract: **LiDAR sets every number, the model supplies only
identity and shape, and the result is checked against the number it was given.**
Astra has no computer of its own; it writes the script and our existing headless
Blender driver executes it.

---

## 5. The architecture

### 5.1 Identify first, then carve

The obvious approach — cluster the mesh, then name the clusters — does not work.
A laptop resting on a desk is spatially connected to the desk, so Euclidean
clustering returns one blob. You cannot segment before you know what you are
looking at.

So vision leads and geometry measures:

```
frames + poses ──► segment and name in 2D
                        │
                        ▼
        project each mask into 3D using pose + intrinsics
                        │
                        ▼
        intersect with the LiDAR mesh ──► that object's points
                        │
                        ▼
        fit an oriented box ──► SceneNode(label, role, movable, parent_id)
                        │
                        ▼
        collider, route blocking, checks, rearrangement
```

Every number in the resulting node comes from the mesh. The model contributes
the label, the role and the movability, and nothing else.

### 5.2 Three priors we are currently ignoring

Before any model call, three sources of free evidence:

1. **ARKit per-face classification** — wall, floor, ceiling, table, seat,
   window, door. Requires storing `anchor.geometry.classification` alongside
   vertices, which is a schema addition and a few lines in
   `LidarMeshRecorder`.
2. **RoomPlan's own boxes** — already correct where they exist. A new node must
   never contradict one; it fills the gaps between them.
3. **Plane removal** — the floor and the desk surfaces are the things that
   connect otherwise separate objects. Removing them first makes the remaining
   geometry separable, which makes a mesh cluster a useful *cross-check* on a
   vision mask rather than a substitute for one.

### 5.3 Verification, which is the point

A named object is a claim. Three independent sources must agree before it
becomes a node a check can fail a shop on:

| Source | Says |
|---|---|
| Vision over frames | this is a card reader |
| LiDAR mesh | there is an obstacle of these dimensions in this place |
| RoomPlan / ARKit class | nothing here, or a compatible coarse class |

Disagreement is information, not an error to hide. If the vision mask projects
onto a volume the mesh says is empty, the identification is wrong. If a box
claims 4.32 x 1.04 m and the mesh fills 60% of that footprint, RoomPlan merged
two objects. Both outcomes set `quality = needs_another_look`, which the
existing `findings.resolve` already downgrades from a finding to a question.

### 5.4 Appearance, last

Once an object is named and measured, Astra writes Blender Python for its shape,
constrained to the measured extents and validated against them before use. This
is the part that makes the viewer show a terminal instead of a grey cuboid, and
it is deliberately last: it changes nothing about correctness, and it is the
only part a demo can survive without.

---

## 6. Order of work

Sequenced so each step is useful alone and nothing depends on a later one.

**Stage 0 — stop lying.** 3.1 first: an assessment that checked nothing must say
so. `rules_checked` and the unevaluated list belong in the saved assessment and
on the report, and a report with zero verified rules must be visibly a preview.
This is independent of everything else here and should land before any demo.

**Stage 1 — fix the plumbing the plan depends on.**
- 3.3, frame-to-pose identity. Upload frames under their filename, or carry an
  explicit map. Nothing downstream can be trusted until a frame can be tied to
  its camera.
- 3.4 and 3.5, so a scan stops silently losing its imagery.
- 3.2, the 1.4 m offset, since it makes the scanned viewer path unusable today.

**Stage 2 — keep what the phone already computes.** Store ARKit's per-face
classification. Apply person segmentation rather than recording that we asked
for it. Both are on-device changes with no server work, and both make Stage 3
cheaper and better.

**Stage 3 — the identification pass.** Fill `Stages.label`, which exists and is
`pass_through` today, with: project mesh into frames, segment and name, fit
boxes, emit nodes with `label`, `role`, `movable`, `parent_id`, `labeled_by =
"astra"`, and `quality` set by whether the three sources agreed.

**Stage 4 — make the new nodes count.** `roles.py` should resolve by an explicit
`role` field rather than by matching English labels. `movable` should come from
the role, not from a residential category blocklist. Raise `max_tier` past 1 so
the reach-range and protruding-object rules that this whole exercise exists to
enable can actually run.

**Stage 5 — appearance.** Astra plus Blender per object, constrained to measured
extents. Keep the scanned mesh visible after the first save (3.10) so the
photoreal room does not vanish the moment someone rearranges it.

---

## 7. Open questions

1. **Does RoomPlan's second `run` preserve our ARKit configuration?**
   `RoomCaptureController` runs the session with `.meshWithClassification` and
   person segmentation, then calls `captureSession.run(configuration:)`. The
   comment asserts iOS 17+ preserves it. Nothing in the code proves it, and
   Stage 2 depends on the answer.

2. **Which segmentation model?** The identification pass needs masks, not just a
   caption. Options are an on-device Vision request during capture, a server
   segmentation model, or asking a vision LLM for boxes and accepting coarser
   masks. This determines Stage 3's cost and latency.

3. **How much does a wrong label cost?** A misidentified terminal produces a
   finding about the wrong object. The `needs_another_look` downgrade is the
   safety valve; whether it is strict enough is worth a decision before this
   ships to anyone real.

4. **Where do generated meshes live?** Per-scan, or a cache keyed by role and
   dimensions? A cache makes the demo instant and makes two shops' terminals
   look identical.
