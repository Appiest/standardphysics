# scripts

Most of the 343 files here are not code the product runs. About 180 are Python, shell and Swift, and 162 are run outputs kept as evidence in [`shop_pilot/assets`](shop_pilot/assets). Of the 74 scripts at the top level, only the first five groups below are used by the product, CI or the team's day-to-day work. The rest are Moffitt Library and photo-mesh research, run by hand, and a few of them are imported by tests.

The production image copies this whole folder because the API runs the SPAR3D furniture scripts from here.

## Run by the product

| Script | What it does |
|---|---|
| `spar3d_furniture_experiment.py` | Fits a SPAR3D mesh to a photographed chair or sofa. The API's `furniture` job runs it on a server configured for it. |
| `spar3d_infer.py`, `spar3d_render.py` | One SPAR3D inference in its own venv, and the Blender renders that check it. Called by the script above. |
| `benchmark_precedent_constraints.py` | The constraint benchmark the agents CLI runs across synthetic rooms. |

## CI and the production image

| Script | What it does |
|---|---|
| `coverage_floors.py` | Fails CI when a module the service leans on loses test coverage. |
| `skipped_tests.py` | Summarises the tests a pytest run skipped. |
| `smoke_image.sh`, `smoke_scan.py` | Proves a built image can hold an account and measure a real room before anyone deploys it. |
| `test_blender_in_image.sh` | Runs the Blender-dependent tests inside the built image. |

## Deploying and the phone build

| Script | What it does |
|---|---|
| `deploy.sh` | Deploys master's tested image to the droplet. See [`docs/DEPLOY.md`](../docs/DEPLOY.md). |
| `lock_python.sh` | Re-resolves the pinned Python dependencies after a `pyproject.toml` changes. |
| `run-ios.sh` | Builds the app and installs it on a phone or the simulator. |
| `ship-ios.sh` | Archives the app and uploads it to TestFlight. |

## Working with scans

| Script | What it does |
|---|---|
| `import_scan.py` | Uploads a captured scan directory to a running API. |
| `import_capture.py` | Imports one phone capture directory as its own scan. |
| `merge_captures.py`, `merge_room_usdz.py` | Puts several captures of one floor into one scan, ready to align by hand. |
| `combine_scans.py`, `build_full_floor.py` | Combines placed captures into one scan that carries all their measurements. |
| `export_placed_floor.py`, `export_scan_capture.py` | Writes a stored scan back out as the capture directory the phone uploaded. |
| `rediscover_scan.py` | Runs object discovery again on a stored scan and saves a new revision. |
| `scan_cost.py` | Measures what one scan costs in model calls. |
| `verify_rulepack.py` | Checks each rule's threshold against the text it cites and writes the ledger. |

## Fine-tuning and training data

| Path | What it is |
|---|---|
| [`finetune/`](finetune) | The rearrangement fine-tuning runs: data builders, evaluations and their tests. |
| `finetune_ledger.py` | Collects every run's numbers into `notebooks/public/finetune_ledger.json` for the notebook and the pitch deck. |
| `audit_rooms.py`, `import_research_dataset.py`, `stage_external_sources.py` | Convert and audit public indoor-scene datasets for training rooms. |

## Other folders

| Path | What it is |
|---|---|
| [`tests/`](tests) | Tests for the deploy, backup, monitoring and release scripts, and for the research scripts other code imports. |
| [`shop_pilot/`](shop_pilot) | An acceptance evaluator that re-checks a pilot's receipts from the raw artifacts. `assets/` holds the runs it checked. |
| [`emergence_spike/`](emergence_spike), [`book_count_spike/`](book_count_spike) | One-off experiments, each with the question it tested at the top. |

## Moffitt Library and photo-mesh research

Run by hand while reconstructing Moffitt Library and benchmarking photo meshes and Gaussian splats. Nothing in the product runs them. The ones marked with a test are imported by one, so keep them working.

| Script | What it does |
|---|---|
| `alignment_probe.py`, `moffett_image_registration.py`, `moffett_raw_photo_matches.py`, `verify_moffett_overlap.py` | Find and verify alignments between Moffitt captures from image features. |
| `prepare_moffett_photogrammetry.py`, `reconstruct_moffett_chunks.py`, `reconstruct_moffett_floor.py`, `refine_moffett_cameras.py`, `colmap_poses.py` | Photogrammetry on the Moffitt captures. |
| `align_moffett_floor.py` (tests), `bake_library_textures.py` | Align the four Moffitt captures into one floor and paint it. |
| `refresh_moffett_evidence.py`, `refresh_moffett_furniture.py` | Replay Moffitt photos through discovery and SPAR3D and publish new labels and meshes. |
| `bake_moffett_photo_mesh.py`, `bake_moffett_vertex_baseline.py`, `generate_surface_materials.py`, `photo_mesh_glb.py`, `glb_colors.py`, `glb_scene.py` | Build and inspect photo-textured meshes. |
| `render_photo_mesh_raster.py` (tests), `render_calibrated_photo_mesh.py`, `render_frozen_views.py`, `render_library_coloured_scan.py`, `render_library_preview.py`, `make_photo_mesh_comparison.py`, `diagnose_hole_visibility.py` | Render and compare photo meshes. |
| `evaluate_photo_mesh_500.py` (tests), `evaluate_candidate.py` (tests), `evaluate_pilot.py`, `freeze_photo_mesh_splits.py`, `freeze_render_pilot.py`, `build_render_pilot_dataset.py`, `repair_split_parity.py` | The photo-mesh and render-efficiency benchmarks. |
| `run_splats.py`, `run_controlled_pilot.py`, `build_surface_splats.py`, `train_structured_splats.py`, `train_deepfusion_splats.py`, `export_moffett_splat_dataset.py`, `compress_moffett_splat.py` (tests), `compress_moffett_splat.cc` | Gaussian splat training and compression. |
| `mask_capture_people.swift`, `reconstruct_capture.swift` | macOS command-line tools: Vision person masks for capture photos, and a RealityKit photogrammetry reconstruction. |
| `verify_outlet_repair_acceptance.py` (tests), `demonstrate_gate_f.py`, `_verify_merge.py` | Acceptance checks from earlier lanes; `demonstrate_gate_f.py` writes a synthetic fixture, not a real result. |
