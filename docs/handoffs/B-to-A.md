# B to A: frames are load-bearing now, and two scans arrived without them

## The short version

Capture works. A scan airdropped from the phone today carries 119 JPEGs at
1920x1440, a 41 MB walkthrough, the mesh, the room and the poses. Nothing about
recording needs fixing.

But `datasets/phone/test1` and `datasets/phone/ravida` have **no frames and no
video**, while their `poses.json` is complete: 440 entries spanning the full 240
seconds, every one naming a `frames/frame_NNNN.jpg` that was never written.

Two paths in `FrameRecorder` produce exactly that:

- `RecordingResult.recovered(from:)` sets `videoURL: nil` and filters
  `frameURLs` to files that still exist. A recovered scan loses its imagery by
  design.
- `makeResult` filters the same way, so a run where `jpegEncodingFailed` fired
  for every frame yields an empty list and no warning upstream.

Complete poses argue against a mid-scan interruption, which points at encoding
rather than recovery, but the artifacts cannot settle it. The device log can.

**Worth making loud rather than silent.** A scan that reaches the server with
poses and no frames is now a scan we cannot identify objects in, and nothing
currently says so.

## Why this matters more than it did this morning

The architecture is changing underneath you, and the change makes frames
essential rather than nice to have.

RoomPlan only boxes its own category list. On the scan you airdropped, a room
containing at least six monitors, two laptops, a speaker, backpacks and framed
posters came back as **six chairs and three tables**, with zero doors and zero
windows. On `test1`, only **9.7%** of the captured mesh falls inside any object
box, leaving **104,000 points between 0.4 m and 2.0 m** unaccounted for. That
band is where a payment terminal lives, and a payment terminal is what ADA 2010
308 and 309 are about.

So objects will stop coming only from `CapturedRoom.objects`. The plan is:

1. Cluster the mesh that no box claims into connected components.
2. Identify each cluster from the frames whose camera frustum contains it.
3. Promote it to a `SceneNode` with dimensions, a label and a collider, so it
   blocks routes, moves when dragged, and can be checked.

Nothing in your capture flow has to change for this. What changes is the
consequence of a missing frame: it used to cost a nicer report, and now it costs
an object we cannot name.

## Three things from the data that affect the app

**Frames are stored rotated.** `frame-0007` is 1920x1440 landscape while
`poses.json` records `orientation: portrait`. Whoever consumes them has to apply
that, and it is cheaper for you to record it correctly than for four lanes to
each guess. The pose entries already carry the field, so this is only a note.

**No scan so far contains a door.** `test1`, `ravida` and the airdropped scan
all report zero doors and zero windows. Without a door, no route can begin
outside the building and the doorway carve-out in the grid has nothing to cut.
Worth a line in the capture guidance telling people to walk the entrance.

**Intrinsics are present and correct** — `[1341.07, 0, 0, 0, 1341.07, 0,
964.03, 725.45, 1]` — which is what makes projecting a mesh cluster into a frame
possible at all. Keep them.

## What I am building

Mesh clustering and cluster-to-frame projection, in `packages/pipeline`. It
needs no change from you. When it lands, a scan with frames gets named objects
and a scan without gets geometry it cannot label, which is the argument for
making the missing-frames case loud on the phone.
