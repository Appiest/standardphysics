# Getting the app to shop owners

TestFlight first, the App Store after a few real shops have been through it.
Both need the same signed build and the same App Store Connect record, and
TestFlight review is hours rather than days.

Everything the repository can settle is settled. What is left needs an Apple
account, a hosted server and a phone with LiDAR.

## Already in the repository

| | Where |
|---|---|
| Team, bundle id, deployment target | `apps/ios/project.yml` |
| Where the server addresses go | `CAPTURE_API_BASE_URL`, `CAPTURE_WORKSPACE_BASE_URL`, empty until there is a domain |
| App icon, light, dark and tinted | `apps/ios/scripts/make_app_icon.py` |
| Export compliance answered | `ITSAppUsesNonExemptEncryption: false` |
| Devices without ARKit excluded | `UIRequiredDeviceCapabilities` |
| Devices without LiDAR told why | `AppRootView.swift`, `UnsupportedDeviceView` |
| Deleting an account, in the app | Home screen, under the shop name |
| Deleting an account, on the server | `DELETE /api/account` |
| Privacy policy | `/privacy` on the workspace |

Regenerate the Xcode project after editing `project.yml`:

```bash
brew install xcodegen
cd apps/ios && xcodegen generate
```

## Before the first upload

The server has to be up first, at the address compiled into the app. See
[`DEPLOY.md`](DEPLOY.md). A build that points at a laptop is a build App Review
cannot sign in to.

In App Store Connect, create the app record with bundle id
`com.standardphysics.capture`, and set the privacy policy URL to
`https://app.<your domain>/privacy`. Make sure the address on that page can
receive mail before you submit; App Review does write to it.

## Archive and upload

```bash
cd apps/ios
xcodebuild -project StandardPhysics.xcodeproj -scheme StandardPhysics \
  -configuration Release -destination 'generic/platform=iOS' \
  -archivePath build/StandardPhysics.xcarchive archive

xcodebuild -exportArchive -archivePath build/StandardPhysics.xcarchive \
  -exportOptionsPlist ExportOptions.plist -exportPath build/export
```

`ExportOptions.plist` is not in the repository because it carries signing
choices rather than code. Xcode writes one for you the first time you use
Product, Archive, Distribute App.

## What to answer about privacy

The answers have to match what the code actually uploads, which is
`ArtifactKind` in `packages/contracts/standardphysics_contracts/scan.py`.

| Apple's question | Answer |
|---|---|
| Contact info | Email address, for account management, not linked to tracking |
| User content | Photos, video and "other user content", the room model and mesh, for app functionality |
| Identifiers | None |
| Usage data, diagnostics | None |
| Tracking | No |

Photographs go to a model provider to identify what is in the room. That is a
third-party processor under app functionality, not tracking, and the provider
is set to retain nothing.

## Review notes

App Review runs the app on real hardware, and the wrong hardware shows them a
sentence instead of an app. Say so plainly in the notes:

> Standard Physics measures a shop against ADA clearance rules, so it needs the
> LiDAR scanner: an iPhone 12 Pro or later Pro model, or a 2020 or later iPad
> Pro. On a device without LiDAR the app explains what is needed and stops.
>
> Sign in with the demo account below. It has a shop already scanned, so you can
> reach the findings and the 3D model without walking a room. To scan, tap Start
> scanning and walk a circuit of any room.
>
> Deleting the account is on the home screen, under the shop name, as required
> by 5.1.1(v). The demo account is restored between reviews, so please do use it.

Seed that demo account on the Droplet with a password you choose, in
`deploy/digitalocean/.env`:

```
SP_SEED_SAMPLE_SHOP=1
SP_SEED_OWNER_PASSWORD=<a password you pick>
```

Then `docker compose up -d` to pick it up. Take the two lines out again once
the app is live, so a synthetic shop never appears beside real ones.

## Screenshots

`TARGETED_DEVICE_FAMILY` is `1,2`, so the App Store asks for both an iPhone and
an iPad set. Dropping to `"1"` makes it iPhone-only and halves the work, at the
cost of the LiDAR iPad Pros that `UnsupportedDeviceView` currently welcomes.

Required sizes are a 6.9 inch iPhone and a 13 inch iPad. The screens worth
showing are the scan in progress with its coverage overlay, the review of the
captured room, and a finding on the 3D model with its measurement and citation.

## TestFlight

Upload the build, wait for processing, then add external testers and submit for
Beta App Review. The review wants the same notes and the same demo account as
above. Once it clears, a tester gets a link and installs it like any app, and
you can push a new build the same day without waiting again.

Walk the first few shops yourself and watch someone else hold the phone. The
scan is the part that goes wrong, and it goes wrong in ways no test catches:
people walk too fast, stop halfway, or point the phone at the floor.
