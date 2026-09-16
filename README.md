# Standard Physics

Every mom and pop store needs to be compliant with local building codes, zoning regulations, and ADA requirements. But for these local small business owners, juggling red tape compliance and designing the shop of their dreams is time consuming and often, prohibitively expensive. Legal regulations are incredibly hard to read and understand, and external consulting services can charge thousands of dollars to help.

Standard Physics makes compliance as easy as a walk around the store. Owners scan their shop with an iPhone, and our agents measure every aisle, doorway, and counter against ADA requirements and building codes. Each issue shows up on a 3D model of the shop, explained in plain English with the exact measurement and the rule it breaks. And when the fix is as simple as moving a table, the app finds a new layout that works with the furniture they already have.

Owners spend less on consultants and more time building the shop of their dreams. And every shop that gets fixed opens its doors to more customers with disabilities.

## Start here

| You are | Read |
|---|---|
| Any agent, before your first commit | [`docs/AGENT_PROTOCOL.md`](docs/AGENT_PROTOCOL.md) |
| Anyone, for what we're building | [`docs/PLAN.md`](docs/PLAN.md) |
| Lane A, capture | [`docs/lanes/LANE_A.md`](docs/lanes/LANE_A.md) |
| Lane B, model and measurement | [`docs/lanes/LANE_B.md`](docs/lanes/LANE_B.md) |
| Lane C, checks and evaluation | [`docs/lanes/LANE_C.md`](docs/lanes/LANE_C.md) |
| Lane D, contracts, API, web | [`docs/lanes/LANE_D.md`](docs/lanes/LANE_D.md) |
| Anyone writing UI or copy | [`CLAUDE.md`](CLAUDE.md), then section 2 of the plan |
| Anyone pointing ARIA at the evaluation | [`docs/aria.md`](docs/aria.md) |
| Anyone opening the reactive notebook | [`docs/marimo.md`](docs/marimo.md) |

## Setup

You need Python 3.11 or newer and Node 20.9 or newer. From a fresh clone:

```bash
./start.sh
```

That installs the Python packages into `.venv` and the web packages into `apps/web`, then starts the API on port 8787 and the web workspace at http://localhost:3000. Ctrl-C stops both. For the demo, `./start.sh --prod` runs a production build instead.

Open the workspace and create an account. A scan belongs to the owner who uploaded it, and the list only ever shows your own shops, so a fresh account starts empty.

To start with a shop already in it, run `SP_SEED_SAMPLE_SHOP=1 ./start.sh`. That seeds the sample boba shop and the demo account that owns it, and prints the email and password to sign in as. Set `SP_SEED_OWNER_PASSWORD` to choose the password yourself; leave it unset and the server generates one and logs it.

To load the phone scans in `datasets/phone`, run `.venv/bin/python scripts/import_scan.py datasets/phone/*` while it's running.

Findings come only from rules whose threshold has been checked against the text it cites. `scripts/verify_rulepack.py` does that check and writes the ledger, which is committed, so a clean clone reports findings without any flag. Two rules are left off it and the script says why for each. A person who has read a section adds their name with `.venv/bin/standardphysics-agents rules second-check <rule> --by "<name>"`. `SP_PREVIEW_UNVERIFIED_RULES=1` runs every rule including the two, for development only, and stamps the report as unreviewed.

To run the tests and the linter:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m pytest packages/agents services/api/tests -q
.venv/bin/python -m ruff check .
(cd apps/web && npm run lint && npm run typecheck && npm run test)
```

To move the shop's dimensions by hand and watch the same checks read the new room:

```bash
.venv/bin/marimo edit notebooks/scenario_sweep.py
```

That is a marimo notebook, installed by `./start.sh`. [`docs/marimo.md`](docs/marimo.md) says what each slider does.

Lane B additionally needs Blender 5.x. `brew install --cask --force blender` — the `--force` matters, because a plain install silently does nothing when Blender was installed by hand.

## Deploying it

```bash
docker compose up --build
```

That builds one image and runs two containers from it, the API and the web workspace, with the scans in a named volume. Open http://localhost:3000. Add `SP_SEED_SAMPLE_SHOP=1` to the environment to seed the sample shop and its demo account.

On a host that gives you a single container and a single port, run the same image with no argument. The entrypoint then serves the workspace on `$PORT` and runs the API beside it on 8787:

```bash
docker build -t standardphysics .
docker run -p 3000:3000 -v scans:/data standardphysics
```

The scans and every uploaded artifact live in `/data`. Mount it, or a restart loses every shop anyone has scanned.

`/health` answers without a session, so a load balancer can ask. It reads from the database, because a process that is listening but cannot read its own scans is not healthy in any way that matters.

Two things the image does not do. Blender is not installed, so an uploaded scan shows as boxes and its report has no pictures; the seeded sample shop carries a committed model and is unaffected. And `/present` and `/brush` are not part of the product, so they answer 404 unless `SP_SHOW_DEMO_ROUTES=1` asks for them.

## What already works

Every lane can start right now without waiting on another.

**`packages/contracts/`** holds the types everyone shares: `SceneGraph`, `Finding`, `Locus`, `Proposal`, `Assessment`, and the `MeasurementProvider` protocol that joins Lane B to Lane C. Lane D owns this package; everyone else reads it.

**`packages/fixtures/`** holds a synthetic boba shop. Two display cases run in from the side walls and leave exactly 31 inches between them, on the only path from the door to the counter. Moving one case 5 inches opens it to the 36 inches the standard requires, so the whole find-then-fix loop is testable before any real scan exists.

```python
from standardphysics_fixtures import FixtureMeasurements, build_graph, build_scenario

result = FixtureMeasurements().route_clear_width(build_graph(), build_scenario(), 0)
result.inches          # 31.0
result.pinch_point     # where the camera should fly to
```

`FixtureMeasurements` implements `MeasurementProvider`, so Lane C writes checks against it today and swaps in Lane B's real implementation later by changing one constructor argument.

**`services/api/openapi.json`** is the upload contract. Lane A codes against it, Lane D implements it, and it runs locally right now:

```bash
python -m standardphysics_fixtures.mock_api    # :8787
```

**`packages/fixtures/standardphysics_fixtures/data/`** holds `shop.scene_graph.json`, `shop.usdz`, and `shop.node_map.json`.

That last file matters more than it looks. USD prim names must be alphanumeric with underscores and cannot start with a digit, so a UUID cannot be one — export it as a name and the hyphens vanish, the names collide, and every object comes back as `Cube_001`. Prims are named `n_<uuid hex>` and the map carries them home. RoomPlan solves the same problem with its `metadataURL` mapping file, which is why Lane A has to capture it.
