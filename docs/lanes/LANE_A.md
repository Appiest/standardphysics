# Lane A — Capture

**You build the iPhone app that scans the shop.** One pass records LiDAR and video together, and the app tells the owner where to point next until the room is covered.

Read `docs/PLAN.md` sections 1, 2 and 3. Read `docs/AGENT_PROTOCOL.md` before your first commit.

## You own

```
apps/ios/**
docs/progress/PROGRESS_A.json
docs/handoffs/A-to-*.md
```

Nothing else. The results screens inside the app are Lane D's web build in a WebView — you own the shell and the handoff, not the screens.

## You can rely on today

- `packages/contracts/` — the JSON shapes you upload
- `services/api/openapi.json` — the upload endpoints, as a contract, before D implements them
- `packages/fixtures/standardphysics_fixtures/mock_api.py` — a local mock server. `python -m standardphysics_fixtures.mock_api` serves the real upload contract on `:8787` so you are never blocked on Lane D.

## Human tasks — flag these to your person immediately

| What | Why an agent can't | When |
|---|---|---|
| Apple ID signing and device registration in Xcode | Requires an interactive login and a physical device | **First 45 minutes** |
| Trusting the developer certificate on the phone | Physical device, Settings app | With the above |
| Walking the shop and the venue to record real scans | Someone has to hold the phone and walk | By 2:00 PM |
| Handing the phone to a stranger to test the coverage guidance | The whole point is whether a person understands it | By 6:00 PM |

Put each in `needs_human` in `docs/progress/PROGRESS_A.json` the moment you reach it, then keep building against fixtures.

## Build order

**1. Project and signing.** SwiftUI app, iOS 17 target. Get a hello-world build on the physical LiDAR phone. *(Human: signing.)* Everything after this can proceed on the simulator except LiDAR itself.

**2. Device check.** On launch, check `RoomCaptureSession.isSupported`. A supported device goes straight to the start screen. An unsupported one gets one friendly line naming what to use — following section 2, so it says what works, not what doesn't.

**3. Capture screen.** `RoomCaptureView` with its guided interface. Delegate: `captureView(shouldPresent:error:)` for raw data, `captureView(didPresent:error:)` for the processed room, `captureSession(_:didUpdate:)` for live updates. Record and Done states. *Done when a walk around a room produces a `CapturedRoom` you can print.*

**4. Frame tap.** A `CADisplayLink` reading `captureSession.arSession.currentFrame`. Do not replace RoomPlan's ARSession delegate and do not start a second camera session. Save a keyframe at 2 Hz: full-resolution JPEG plus `camera.transform`, `camera.intrinsics`, orientation, timestamp. Skip frames whose tracking state is limited. *Done when a two-minute walk yields ~240 frames with poses.*

**5. Video.** `AVAssetWriter` with a pixel buffer adaptor off the same frames, 15 fps at 1280x960, encoded on a background queue. Drop to 10 fps when `ProcessInfo.thermalState` hits `.serious`. Cap a session at four minutes. *Done when `walkthrough.mp4` plays back and stays under ~150 MB.*

**6. Coverage engine.** The feature that makes this usable by someone who has never scanned anything.

Per surface, track observed area. A wall patch counts as observed when it is inside the camera frustum, within 5 m, and viewed at under 60 degrees off the surface normal. Require two viewpoints at least 1 m apart, so one glance from the doorway is not coverage. Combine with RoomPlan's own `low`/`medium`/`high` confidence. A surface is done at 70% observed area, two separated viewpoints, and high confidence.

Write `coverage.json` with per-surface observed fraction and viewpoint count. *Done when walking half a room marks exactly that half done.*

**7. Coverage UI.** A floor-plan minimap along the bottom that fills in solid as walls are covered, unfinished stretches hollow and pulsing. An arrow in the AR view pointing at the nearest unfinished area. One instruction at a time above the map: "Point the phone at the back wall." When everything is done: **"You've got the whole shop."**

Never show a percentage. Never show the word confidence. Pressing Done early always works.

**8. Review screen.** The captured room in 3D on device, name it, then upload.

**9. Upload.** `POST /api/scans` to create, idempotent `PUT /api/scans/{id}/artifacts/{artifact_id}` with checksum per artifact, `POST /api/scans/{id}/complete` to finalize. Persist which artifacts completed so an interrupted upload resumes per artifact. Build against the mock server. Assume the venue wifi is bad — a failed upload never loses a local capture.

**10. Status.** Poll and show one of: Uploading, Measuring your shop, Checking, Ready.

**11. WebView shell.** Lane D's responsive build in an authenticated `WKWebView`. You own session handoff and the native capture callbacks. Allowlist navigation and bridge messages. Keep provider keys server-side and never hand an upload token to arbitrary web content.

## Artifacts you produce

| File | From |
|---|---|
| `room.usdz` + metadata mapping | `capturedRoom.export(to:metadataURL:exportOptions:)` |
| `room.json` | `JSONEncoder().encode(capturedRoom)` |
| `walkthrough.mp4` | AVAssetWriter |
| `frames/*.jpg` + `poses.json` | Display link tap |
| `coverage.json` | Coverage engine |

Capture the `metadataURL` mapping. It links USDZ node names to `CapturedRoom` element UUIDs, and every tap-to-locate feature downstream depends on that link.

## Done means

A person who has never used the app scans an unfamiliar room, the guidance gets them to full coverage without help, and the scan appears in the web workspace within 60 seconds of pressing Done.
