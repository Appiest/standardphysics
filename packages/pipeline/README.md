# Lane B — model and measurement

Turns a scan into a measured model other agents can query.

## Blender

**Pinned: 5.2.1 LTS or newer.** 4.0.2 cannot import USDZ.

```bash
brew install --cask --force blender
```

The `--force` matters. A plain `brew install --cask blender` over a hand-installed Blender reports the cask as not installed, exits 0, and changes nothing.

Verify with a real round trip, not by reading `filter_glob` — that property reads `*.usd` on both the working and the broken version:

```bash
python -m standardphysics_pipeline.check_blender
```

## Layout

| File | Does |
|---|---|
| `check_blender.py` | Proves this machine can import USDZ |
| `coords.py` | RoomPlan's Y-up meters to our Z-up meters, once, on ingest |
| `ingest.py` | `room.json` to `SceneGraph` |
| `astra.py` | OpenRouter label/clean, or local shop heuristics |
| `occupancy.py` | Floor rasterization and the distance transform |
| `routes.py` | Widest path, bottleneck width, and the pinch point |
| `measure.py` | The `MeasurementProvider` Lane C calls |

## Before you push

CI runs three suites. `pytest` alone runs only the first, which is how a change
in this lane turned master red without failing anything locally:

```bash
PY=packages/contracts:packages/fixtures:packages/pipeline:packages/agents:services/api
python -m pytest -q
PYTHONPATH=$PY python -m pytest packages/agents -q
PYTHONPATH=$PY python -m pytest services/api/tests -q
```

Lane C consumes this lane's types directly, so a change to a return type here
breaks their checks and never touches a test in `tests/`.
