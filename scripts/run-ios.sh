#!/usr/bin/env bash
# Builds the app and puts it on a phone, without opening Xcode.
#
#   scripts/run-ios.sh              the connected device, or the simulator
#   scripts/run-ios.sh --simulator  always the simulator
#
# A device gives you the scan; the simulator gives you everything else and
# builds in a fraction of the time. Scanning needs LiDAR and the simulator has
# none, so it opens on the unsupported-device screen unless it is told this is
# a demo, which the simulator run does for you.
set -euo pipefail

BUNDLE_ID="com.standardphysics.capture"
PROJECT_DIR="$(cd "$(dirname "$0")/../apps/ios" && pwd)"
BUILD_DIR="${TMPDIR:-/tmp}/standardphysics-ios"
SIMULATOR_NAME="${SP_SIMULATOR:-iPhone 17 Pro}"

say() { printf '\n== %s\n' "$1"; }

connected_device() {
  xcrun devicectl list devices 2>/dev/null \
    | awk -F'  +' '/physical/ && /available/ {print $3}' \
    | grep -oE '[0-9A-Fa-f-]{25,}' \
    | head -1
}

build_and_run_on_device() {
  local udid="$1"
  say "Building for the phone"
  xcodebuild -project "$PROJECT_DIR/StandardPhysics.xcodeproj" \
    -scheme StandardPhysics -configuration Debug \
    -destination "id=$udid" -derivedDataPath "$BUILD_DIR" \
    -allowProvisioningUpdates build

  local app="$BUILD_DIR/Build/Products/Debug-iphoneos/StandardPhysics.app"
  say "Installing"
  xcrun devicectl device install app --device "$udid" "$app"
  say "Launching"
  xcrun devicectl device process launch --device "$udid" "$BUNDLE_ID"
}

simulator_udid() {
  # By identifier, not by name. A name that matches nothing makes xcodebuild
  # fall back to whatever it can find, which is My Mac, and the error then
  # complains about macOS rather than about the name.
  xcrun simctl list devices available -j \
    | python3 -c "
import json, sys
name = sys.argv[1]
runtimes = json.load(sys.stdin)['devices']
for devices in runtimes.values():
    for device in devices:
        if device['name'] == name:
            print(device['udid'])
            raise SystemExit
" "$SIMULATOR_NAME"
}

build_and_run_on_simulator() {
  local udid
  udid="$(simulator_udid)"
  if [ -z "$udid" ]; then
    echo "No simulator called '$SIMULATOR_NAME'. Set SP_SIMULATOR to one from: xcrun simctl list devices available" >&2
    exit 1
  fi
  say "Building for $SIMULATOR_NAME"
  xcodebuild -project "$PROJECT_DIR/StandardPhysics.xcodeproj" \
    -scheme StandardPhysics -configuration Debug \
    -destination "id=$udid" \
    -derivedDataPath "$BUILD_DIR" build

  local app="$BUILD_DIR/Build/Products/Debug-iphonesimulator/StandardPhysics.app"
  say "Installing on $SIMULATOR_NAME"
  xcrun simctl boot "$udid" 2>/dev/null || true
  # Simulator.app lives inside the selected Xcode, and is not findable by name
  # on a machine where Spotlight has not indexed it.
  open "$(xcode-select -p)/Applications/Simulator.app" 2>/dev/null || true
  xcrun simctl install "$udid" "$app"
  # Past the unsupported-device screen, since no simulator has LiDAR.
  SIMCTL_CHILD_SIMULATOR_CAPTURE_DEMO=1 xcrun simctl launch "$udid" "$BUNDLE_ID"
}

main() {
  if [ "${1:-}" = "--simulator" ]; then
    build_and_run_on_simulator
    return
  fi
  local udid
  udid="$(connected_device || true)"
  if [ -n "$udid" ]; then
    build_and_run_on_device "$udid"
  else
    say "No phone connected, using the simulator"
    build_and_run_on_simulator
  fi
}

main "$@"
