# Mission

## What production ready means here

It does not mean a demo survives a walkthrough, and it does not mean a judge is
impressed. It means the app runs in production, ten people use it on their own
rooms, and every one of them says it is immaculate and does everything it is
supposed to do. Until ten real users say that, this is not finished.

That bar rules out most of what the repository currently does. The app was
built to be demonstrated: the sample shop carries a committed model, several
stages degrade quietly when an input is missing, and the parts a demo never
reaches were never made to work. Every one of those shortcuts is now a defect.

## What the app has to do

### 1. Check every ADA provision, not eighteen of them

The rule pack holds eighteen rules. Each one needs a hand-written check in
`packages/agents/standardphysics_agents/checks/` that knows which surfaces to
measure and how, so the pack grows one Python function at a time. The 2010
Standards run to several hundred provisions, and writing a function per
provision is not a plan.

A rule has to become data expressed in a vocabulary of measurements the engine
already knows how to take. Adding a provision then means writing down what it
requires, not writing code. Coverage is measured against the published standard
rather than against the list of rules we happen to have.

The engine also has to know which rules apply to the room in front of it. Title
III governs public accommodations; a dorm room is not one, and telling a
student their bedroom violates a service-counter rule is worse than saying
nothing. Applicability is part of the rule, not an assumption baked into the
pipeline.

### 2. Identify every object in a scan

RoomPlan boxes the furniture categories Apple ships. Everything else in a real
room arrives as raw LiDAR with no name: the pens, the kettle, the whiteboard,
the laptop, the poster on the wall. `packages/pipeline/standardphysics_pipeline/discovery/`
already finds some of them from the photos, and it runs once, over a handful of
frames, and gives up quietly when an input is missing.

Every object a person would point at has to come back named, measured and
placed. That includes what is written on things: asking what the whiteboard said
means the text on its surface was read and kept.

### 3. Build a real 3D model of whatever was scanned

Scan a dorm room and the result should read like a Blender scene of that room.
The blanket is a separate object from the bed, the bed is separate from the
floor, and the model knows the chain that holds them up.

The dorm room is an example, not the specification. The same scan of somewhere
nobody has been should produce the same quality of model: things we have no
name for, standing in relations we have no word for, held together by whatever
actually holds them together there. If the structure the app can represent is a
list somebody wrote down in advance, it fails the moment it meets a world that
list did not cover.

So the relations are discovered, not declared. `rests on` is a name a model
coined for a dorm room because a geometric predicate held between two measured
regions, and the predicate is stored alongside the name so anything that needs
to know can re-run it. Somewhere stranger, different predicates hold and get
different names, and nothing above has to change.

Today `SceneNode.parent_id` exists and `discovery.boxes.resting_parent` fills it
one level deep by testing whether one box floats above another's top face.
`NodeKind` is a six-member list of wall, door, window, opening, floor and
object, and 91 places in the code ask what kind of thing something is. All of that is the assumption
this requirement removes.

### 4. Answer anything, and show the answer

These are the questions, verbatim, that the app has to handle:

- Are all of the pens in my dorm room?
- What did it say on my whiteboard?
- How could I arrange this room if I had another roommate move in?

`ask/` today parses a question into one of eight kinds and hands it to one of
eight executors. Anything outside that set is rejected. None of the three
questions above is in the set, and adding them as a ninth, tenth and eleventh
kind is the same mistake as writing a check per ADA provision.

Those three are examples. They are not the specification either, and building
a primitive per example is the same failure one level down: a question about
something nobody listed fails exactly the way a ninth question kind would.

The code must not know what kinds of question exist, and it must not know what
kinds of thing exist either. A model reads the question and writes an
expression over measured geometry, authoring whatever predicate the question
needs rather than picking one off a list. Asked which things are precariously
balanced, it composes that test out of centroids, footprints and contact areas
instead of needing someone to have written `precariously_balanced` first.

Nothing about pens, whiteboards or roommates appears anywhere in the source,
and neither does anything about walls, floors or counters.

Two constraints hold that together:

**The model never supplies a number about the room.** This split already exists
in `ask/query.py` and it is the best idea in the codebase. Working out that "how
tall is my desk" is a question about height is judgment, and a model is good at
it. Knowing the desk is 29.5 inches is measurement, and a model is not. Every
number in every answer is read off the scene or taken by the measurement
provider.

**Nothing reaches the screen unverified.** Before an answer renders, the system
checks that it references nodes that exist, that it carries data, and that what
it claims matches what the primitives returned. A question that cannot be
answered says so. Nobody ever looks at an empty screen.

## Model providers

Two, with a policy rather than a habit. Fireworks runs open-source models and
takes the high-volume work: per-frame detection, OCR, captioning, embeddings,
classification. OpenRouter takes the frontier work that open weights cannot do
well: planning an answer, composing a view, proposing a layout.

A call goes to OpenRouter only when an open-source model on Fireworks cannot do
the job. That decision is written down per call site, not left to whoever is
editing.

## The bar this is held to

**Never test on synthetic data.** Not a fixture shop, not a hand-built room in
a test file, not a generated one. A synthetic scene contains exactly the
structure whoever wrote it thought to put in, which is the assumption this
whole design exists to remove, so passing against one proves nothing at all.
Real scans only, and more of them than the two we have.

A checklist of deleted files and greps that come back empty is something to
game. What counts is a held-out suite: a model writes eighty questions about a
scanned scene, no two answerable the same way, regenerated each run so they
cannot be memorised. A judge scores each answer against the real scene rather
than against an expected string. Around one in eight of the questions has no
answer in the scan, and saying so is a pass while answering confidently is a
failure.

The same suite then runs against the scene with every coined name replaced by a
nonsense token. Structural questions have to score the same. Any drop is
something keyed to English names for earthly objects.

## What this requires

The existing app takes many shortcuts. Meeting this bar means restructuring it
rather than extending it, and some of what is there now gets deleted. That is
expected and is not a reason to soften any of the four requirements.
