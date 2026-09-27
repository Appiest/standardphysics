# D to A: the workspace URL, the bridge, and sessions

## Sessions: none for this build

The workspace needs no sign-in for the hackathon build, a decision Brendan made
in Lane D. Load the workspace URL directly in your `WKWebView`, with no cookie
exchange. The web app never sees an upload credential, because it has none.

If sign-in is added later, I will define the exchange endpoint and the expired
session response here first. Nothing will change on your side before that
handoff lands. You can drop "Lane D must provide the web session handoff
contract" from `blocked_on`.

## What the web build matches from your code

| Your code | The web and API |
|---|---|
| `WORKSPACE_BASE_URL` + `/scans/{scanID}` on :3000 | The shop page lives at `/scans/[scanId]` |
| `nativeCapture` message handler, body `"scanShop"` | **Scan your shop** posts `"scanShop"` to `window.webkit.messageHandlers.nativeCapture` when it exists |
| `API_BASE_URL` default `http://127.0.0.1:8787` | The real API serves on :8787, the mock's port |
| Artifact ids `room-usdz`, `room-json`, `room-metadata`, `poses`, `coverage`, `walkthrough`, `frame-0000` | All are valid ids. The API allows letters, digits, `.`, `_` and `-` |
| `coverage.json` as `{uuid: {observed_fraction, viewpoint_count}}` | The API reads that dictionary into `Scan.coverage` as it is |

The web workspace calls the API through its own origin (`/api/*` on :3000), so
your origin allowlist needs no second entry.

## Two things to confirm on a real scan

- **The mapping file.** RoomPlan writes it as a binary plist in every real
  example we found, including Apple's WWDC23 sample, even when the file name
  ends in `.json`. The API stores the bytes as they are, so nothing breaks
  either way. Tell me if yours turns out to be JSON.
- **Finalize timing.** Finalize as soon as `room-usdz` and `room-json` are up,
  and keep uploading the video and frames after that. The real API accepts
  artifacts after `complete`, so the model is ready while the walkthrough is
  still uploading.

## 2026-09-23: the coverage history, and the gate is yours to redraw (A-47)

`93a720c` changed `CoverageEngine`: it keeps every camera the capture feeds it and replays all of them when a surface's geometry changes and at reconciliation. Before, each RoomPlan refinement dropped the views that had missed the surface's previous shape, which is how high-confidence walls came back at zero. `testFinalReconciliationPreservesOnlyMatchingIDsAndStartsUnknownIDsAtZero` became `testFinalReconciliationScoresEverySurfaceAgainstTheWholeWalk`, and `testARefinedWallKeepsTheViewsOfWhereItEndsUp` is new. All 86 iOS tests pass on the iPhone 17 Pro simulator.

The thresholds are untouched. `docs/progress/PROGRESS.md` under A-47 has a table of what each policy finishes on test1 and ravida: no policy that needs every surface finished completes either scan, because every chair is at medium confidence and furniture sides against walls cannot be seen. Deciding what "Room surfaces covered" asks for is your call; the table is the data for it.
