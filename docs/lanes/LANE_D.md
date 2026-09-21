# Lane D — Contracts, API, web

**You own the seams and the screens.** Everyone codes against your contracts, every artifact lands in your API, and the thing judges actually look at is your viewer.

Read `docs/PLAN.md` sections 5, 6, 9, 10 and 11. Read `docs/AGENT_PROTOCOL.md` before your first commit.

## You own

```
packages/contracts/**
packages/fixtures/**
services/api/**
apps/web/**
.github/**
PROGRESS_D.json
docs/handoffs/D-to-*.md
```

You are the only lane that writes `packages/contracts/`. Three other lanes read it, so a change there is a change to everyone's work: announce it in a handoff before you make it.

## You can rely on today

Contracts and fixtures are already committed, including a synthetic boba shop with a deliberate 31-inch pinch and a mock upload server. Your first job is to keep them honest as reality arrives.

## Human tasks — flag these to your person

| What | Why an agent can't | When |
|---|---|---|
| W&B project and public visibility settings | Account access | **First hour** |
| Every key from `.env.example` present in `.env` on the demo machine | Account access | **First hour** |
| Confirm submission deadline and eligibility | Organizer conversation | **First hour** |
| Keep the demo machine stable and awake | Physical machine | Continuous |
| Submit a working version by noon Sunday | Someone clicks submit | **Sun 12:00** |
| Three timed rehearsals | People talking | Sun 11:00 |
| A team project on W&B with Smart features on, then an ARIA session ([`docs/aria.md`](../aria.md)) | ARIA only answers in a team project, and an org admin turns the feature on | **Sun 09:00** |

## Build order

**1. Contracts frozen.** Confirm `packages/contracts/` matches what the other three lanes need. Generate `apps/web/src/types/contracts.ts` from the Pydantic JSON Schema — nobody hand-writes a TypeScript interface mirroring a Python model. *Done when the generator runs in CI and a contract change breaks the web build loudly.*

**1b. Credentials.** `.env.example` lists every variable the server needs.
Load them server-side only: the iOS app, the browser, Git, prompts and Weave
traces never see a key. You own this boundary, and every other lane depends on
you holding it.

**2. API.** FastAPI with SQLite. Scan creation, idempotent per-artifact upload with checksums, finalize, polling, assessment retrieval, proposals, report. Finalization idempotently queues the pipeline — there is no separate "check my shop" button, and retrying finalize creates no duplicate jobs.

**3. Artifact store.** Local filesystem keyed by validated IDs, never by a user-supplied path. Keep the raw capture and every revision.

**4. Web shell.** Next.js, TypeScript, Tailwind. Responsive, because this same build runs inside Lane A's WebView on a phone. Follow `CLAUDE.md`: neutral surfaces, one calm accent, red reserved for failures only.

**5. Viewer.** React Three Fiber loading the GLB. Orbit, pan, zoom, top-down toggle. Select a mesh, get back a SceneGraph node ID — Lane B keeps those IDs stable through export, and if selection breaks, that is a handoff to B, not a workaround here.

**6. The callout system.** The feature the demo lives on.

Selecting a finding does five things at once: the camera tweens to the finding's `camera` pose over 700 ms on an ease-out curve, everything outside `node_ids` drops to 15% opacity, the responsible objects get an outline, the annotation draws, and the card expands to show the fix.

Three annotation shapes: `dimension_line` (arrowed line with the measurement), `region` (translucent floor polygon, red where it fails), `path` (polyline colored by local clearance).

The measurement label is what sells this. Project the 3D midpoint to screen space and position an HTML overlay — cheaper and far more readable than a 3D text mesh. **31 in** rendered legibly beside the actual gap is the single image people remember.

**7. Findings list.** Real problems first, questions second. Each card leads with the plain sentence, then the measurement, then the fix.

**8. Rearrange.** Drag movable nodes on the floor, rotate about Z. Fixed nodes don't move and dimensions never change. Each drop sends the base revision plus a monotonically increasing edit sequence — discard late responses so an old result cannot recolor newer geometry. Target under a second. Locked items read as locked through contrast and a lock mark, not a text label.

**9. Ask box.** Natural language in, a visible structured request out, showing what will move and what is locked. Show `6 chairs -> 6 chairs` so the owner can see nothing was thrown away.

**10. Before and after.** Scrub control, what moved, whether every check passes.

**11. Report.** Print-ready. One block per finding with its render, measurement, fix and citation. Then next steps as actions. Then **What we checked** — the paths measured, the rules checked, who reviewed it and when.

**12. CI.** Typecheck, pytest, contract generation. Clean-clone startup with one command, tested on a machine that has never run it.

**13. The reactive notebook.** `notebooks/scenario_sweep.py`, described in [`docs/marimo.md`](../marimo.md). Sliders set the shop's dimensions and the routine walked through it, and the same `run_case` the evaluation grid calls reads the room they build. The notebook draws findings and reads scorers; it measures nothing itself. *Done when moving a slider changes a finding, and the sweep chart's sampled boundary lands on the number the check cites.*

## Copy

You write more user-facing strings than any other lane. Section 2 of the plan is not a style note, it is a requirement: short sentences, ordinary words, inches, no jargon, and never a sentence whose job is to deny or disclaim. That includes reassurance nobody asked for. If a string would only land with someone already suspicious, cut it.

## Done means

A scan appears in the workspace within 60 seconds of capture, every finding frames the right object from a legible angle with a readable measurement, the full flow works from a keyboard, the report prints, the viewer holds 60 fps on the demo machine, and a clean clone starts with one command.
