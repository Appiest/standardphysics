# Lane B — Model and measurement

**You turn a scan into a measured 3D model that other agents can query.** Astra labels what things are, Blender renders them, and your measurement functions answer every geometric question the checks ask.

Read `docs/PLAN.md` sections 6 and 7. Read `docs/AGENT_PROTOCOL.md` before your first commit.

## You own

```
packages/pipeline/**
docs/progress/PROGRESS_B.json
docs/handoffs/B-to-*.md
```

## You can rely on today

- `packages/contracts/` — `SceneGraph`, `SceneNode`, `MeasurementProvider`
- `packages/fixtures/standardphysics_fixtures/data/shop.scene_graph.json` and `shop.usdz` — a synthetic boba shop with a deliberate 31-inch pinch between two display cases. You do not wait for Lane A to start.
- Lane C codes against the `MeasurementProvider` protocol in contracts. **Implement that interface exactly.** It is the seam between your lane and theirs.

## Human tasks — flag these to your person

| What | Why an agent can't | When |
|---|---|---|
| Install current Blender on each teammate's machine | Installs land outside the repo | **First hour** |
| Tape-measure one real doorway against the SceneGraph | Someone has to hold a tape measure | Within 30 min of the first real scan |
| OpenRouter key in `.env`, zero data retention on, credit limit set | Account access and a billing decision | **First hour** |

The tape measure check takes five minutes and it is what makes every number downstream defensible. Do not skip it.

## Blender

5.2.1 LTS imports USDZ. 4.0.2 does not. `brew install --cask blender` silently no-ops on a hand-installed Blender — use `--force`.

Do not trust `filter_glob`; it reads `*.usd` on both versions. Verify with a round trip:

```bash
/Applications/Blender.app/Contents/MacOS/Blender --background --python-expr "
import bpy
p = '/tmp/t.usdz'
bpy.ops.wm.usd_export(filepath=p)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.usd_import(filepath=p)
print('OK' if 'Cube' in bpy.data.objects else 'BROKEN')
"
```

Ship that as `packages/pipeline/standardphysics_pipeline/check_blender.py` so every machine can self-test.

## Build order

**1. Blender check script.** The round trip above, as a command anyone can run. *Done when it prints OK or a useful failure.*

**2. Coordinate conversion.** RoomPlan is Y-up meters; we are Z-up meters. Convert once, on ingest, in `coords.py`. Write the test first and pin both axis order and sign. *Done when a known transform round-trips and the test would catch a flipped axis.*

**3. SceneGraph from `room.json`.** Measurements come from the parametric data, never from the mesh. Preserve RoomPlan's UUIDs as node IDs, keep `raw_category`, map confidence to `quality`. *Done when the fixture produces a graph whose node count and dimensions match by hand.*

**4. USDZ import.** `bpy.ops.wm.usd_import` headless. Preserve the metadata mapping so mesh nodes trace back to `CapturedRoom` UUIDs. If the importer drops it, generate display geometry directly from canonical node IDs instead. *Done when every mesh object maps to a SceneGraph node.*

**5. Occupancy grid.** Rasterize the floor to 25 mm cells from the XY footprints of every node touching the floor. *Done when the fixture's aisle shows the expected free corridor.*

**6. Widest path.** The one genuinely hard piece, and it stays deterministic. Compute the distance transform of the occupancy grid. To find the widest path between two stops, binary-search the clearance radius: keep cells whose distance exceeds it, flood-fill for connectivity, bisect. Return the bottleneck width **and the pinch cell** — that coordinate is what the 3D callout points at.

Roughly 60 lines of NumPy and SciPy. Do not build a pose-and-heading search planner. *Done when the fixture returns 31 inches and a pinch point between the two known tables.*

**7. Implement `MeasurementProvider`.** Clear width along a leg, clear width at a 180-degree turn, turning space, door clear width, counter height and approach. Each returns the value, the units, and the locus. Push a handoff to Lane C the moment it is real so they can stop stubbing.

### Calling Astra

Every model call goes through OpenRouter using the OpenAI SDK. Nothing in this
lane talks to a provider directly, and no key ever leaves the server.

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)
response = client.chat.completions.create(
    model=os.environ.get("OPENROUTER_MODEL", "openai/gpt-6-astra"),
    messages=[...],
    tools=[...],          # the patch interface, never free-form geometry
    extra_body={"provider": {"order": ["openai"], "allow_fallbacks": False}},
)
```

Pin the provider so the demo runs on one backend, and store the provider and
model OpenRouter reports on every response. Scans of a real shop are private,
so zero data retention is required on the account and on each request.

**8. Astra: label.** Given a node's raw category, dimensions, position relative to walls and doors, and the two or three keyframes whose frustum contains it, decide what the object is and whether it moves. Output a structured patch validated against the contract — never free-form geometry. *Done when the fixture's counter is labeled "ordering counter" and marked immovable.*

**9. Astra: clean.** Split obviously-merged nodes, flag walls that should close but don't, mark implausible detections. A suspected obstacle becomes a question, never a reported violation. Children of a split keep the parent's conservative occupied envelope.

**10. GLB export.** Stable node IDs, under 8 MB. Compression must not break node selection — Lane D's viewer selects by node ID. *Done when D can click a mesh and get back a SceneGraph UUID.*

**11. Finding renders.** Per finding, place a camera that shows the problem legibly and render 1200x800. Reuse unchanged renders rather than blocking every preview on Blender.

## Rules you enforce

No tolerance, epsilon, or percentage allowance may shrink an obstacle until a check passes. Low confidence alone never clears floor space. Relabeling never removes a node from collision occupancy and never unlocks a fixture. Only ingest writes dimensions.

## Done means

A tape-measured doorway matches the SceneGraph within 3 cm, the fixture's known pinch comes back as 31 inches with the right coordinate, and Lane C is calling your real functions instead of stubs.
