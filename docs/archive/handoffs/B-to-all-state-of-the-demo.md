# Where the demo stands, 5am

Everything below was run against **jerry's house**: a real 110-second capture,
218 photos, 218 camera poses, a LiDAR mesh. No fixture, no sample data.

## The whole path works

| step | state |
| --- | --- |
| Capture on the phone | LiDAR, video and 2 Hz photos, version 2 poses |
| Upload | 218 frames, poses, mesh, room.json, walkthrough |
| Ingest | 29 nodes from RoomPlan |
| **Discovery** | **27 more objects RoomPlan has no category for** |
| People removed | 12,927 mesh points, from 59 frames that had someone in them |
| Checks | 9 rules, 8 findings with ADA citations and 3D locations |
| Renders | one still per finding, from Blender |
| Textured LiDAR | 48 frames baked, 14% of surfaces carry photo colour |
| Web | floor plan, textured 3D model, findings, questions, ask box |
| Rearrange | drag a piece, every check re-runs, save the layout |
| Report | prints |

## What discovery finds that RoomPlan does not

Laptops with the screen standing up, a 3D printer, a backpack, a kettlebell, a
dumbbell, a book, a bottle, shelving, a rug. On this capture RoomPlan boxed 14
objects and discovery added 51.

## How accurate the boxes are

RoomPlan's parametric dimensions are Apple's calibrated output, so carving the
objects **it** boxed and comparing is a measurement rather than a claim.

| | median error |
| --- | --- |
| Height | 3.1 in |
| Footprint sides | 8.6 in |
| Centre position | 7.0 in |

**Heights are good and footprints are not.** The cause is physical: a phone
walked past a cabinet only ever sees its front, so the depth comes back as the
thickness of the surface that was photographed. A fireplace 20 in deep carves
at 7 in.

That error runs the wrong way. Depth an object loses is width the aisle appears
to gain. **Route findings should be read from RoomPlan's own nodes, which carry
Apple's dimensions, and discovered objects should be read as "there is
something here" rather than as measurements.** Their `quality` says as much:
`measured` needs four frames agreeing, and everything else is
`needs_another_look`.

I tried extending wall-backed objects to the wall they stand against. It made
the footprints worse, 8.6 in to 13.5 in, because it also extended objects that
were not truncated. Reverted rather than shipped.

## Two bugs worth knowing about

**Doors.** 404.2.3 measures the clear width with the door open 90 degrees, and
a scan sees the hole in the wall. We reported the hole as the answer, which
passed any doorway whose opening cleared 32 in while the real gap might be 30.
An opening already under the requirement is still a certain problem. An opening
over it is now a question with a tape measure attached.

**Dimension lines.** They were drawn along world X through the centre of
whatever was measured. On a door turned 78 degrees out of the grid, the length
was right and the line spanned nothing a person could walk through.

## Spend

The OpenRouter key is at **$9.50 of $10**. Two full passes over 218 frames did
that. Answers are now cached per photo, so rebuilding a scan costs nothing, but
a new capture costs about $2 to read. Top the key up before the demo.

— B

## Closed: the door render (was: frames badly)

`render_finding` produces a usable still for route findings and a flat grey one
for the doorway. Three things are ruled out, measured rather than guessed:

- The camera stands **inside** the floor polygon, not outside the building.
- No node contains the camera position, and the sight line to the door passes
  through no object at 25, 50 or 75 per cent.
- The dimension line itself is right: both endpoints share a y, sit at z = 0.05,
  and span 0.809 m, which is the 31.84 in reported.

It was the wall. `render_finding.py` added every node as a solid cube, doors
included, so the doorway was behind an unbroken slab and no camera position
was ever going to help. `build_glb.py` already cuts openings out of walls for
the viewer's geometry; the renderer now uses that same code, and a door
finding shows a doorway you can see through.

The label needed a second fix. It sits in the plane of the wall, so the jambs
cut it in half and "31.8 in" rendered as ".8 in". It now stands a metre toward
the camera, clear of what it measures.

I also tried framing portals from standing height rather than from above.
That was treating the symptom, and it is reverted.


## A fragment in an aisle invents a violation

Discovery counted a frame as a look. Keyframes land twice a second, so a dozen
of them in a row counted as a dozen looks at something the phone saw once from
a doorway. Fragments came through with enough apparent support to be emitted,
and some of them stood in the aisle.

They were not harmless. On this capture they produced:

- a turn reported as **30.6 in**, a red finding, against a requirement of 48
- a route reported as **26.4 in**, against a requirement of 36

Counting separate places the phone stood, rather than frames, drops 55 objects
to 27 and those findings become **51.5 in, which passes** and **36.2 in**. We
were reporting a violation that was not there.

What survives is the real furniture: the sofa, the shelving, the chairs, the
lamps, a backpack, a laptop, a kettlebell, a book, a shoe. Half of them carry
`needs_another_look`, including things seen in a dozen frames from one spot,
which is the honest reading of standing still and pointing.

## Findings now reach the objects discovery found

With a customer route marked, 14 rules run and 5 findings pin to objects
RoomPlan never boxed: a shoe and a kettlebell on the exit path, a laptop and a
backpack narrowing the passing space and the route.

They come out as questions rather than problems, because a discovered object in
the pinch carries `needs_another_look`. That is the right way round: an object
carved to within about eight inches should ask for a better look before it
accuses anybody.

**Nothing runs these rules until a route is marked.** Without one the
route rules are stripped and the shop reads as eight door findings. The
"Mark the customer route" button is not decoration; it is what turns the
checks on.
