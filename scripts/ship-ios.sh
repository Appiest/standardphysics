#!/usr/bin/env bash
# Archives the app and sends it to TestFlight, without opening Xcode.
#
#   scripts/ship-ios.sh
#
# Needs an App Store Connect API key, which is what lets xcodebuild create the
# distribution certificate and the profile on its own, and what altool uploads
# with. Make one at App Store Connect, Users and Access, Integrations, App
# Store Connect API. The .p8 downloads once and cannot be downloaded again.
#
# Put these in your shell profile:
#
#   export SP_ASC_KEY_ID=XXXXXXXXXX
#   export SP_ASC_ISSUER_ID=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
#   export SP_ASC_KEY_PATH=~/.appstoreconnect/private_keys/AuthKey_XXXXXXXXXX.p8
#
# The key is a credential. It belongs in a file with 600 on it, never in the
# repository and never pasted into a chat window.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/../apps/ios" && pwd)"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="${TMPDIR:-/tmp}/standardphysics-ship"
ARCHIVE="$BUILD_DIR/StandardPhysics.xcarchive"
EXPORT_DIR="$BUILD_DIR/export"

say() { printf '\n== %s\n' "$1"; }

require_key() {
  local missing=0
  for name in SP_ASC_KEY_ID SP_ASC_ISSUER_ID SP_ASC_KEY_PATH; do
    if [ -z "${!name:-}" ]; then echo "$name is not set" >&2; missing=1; fi
  done
  [ "$missing" = "0" ] || { sed -n '3,18p' "$0" >&2; exit 1; }
  KEY_PATH="${SP_ASC_KEY_PATH/#\~/$HOME}"
  [ -f "$KEY_PATH" ] || { echo "No key file at $KEY_PATH" >&2; exit 1; }
}

bump_build_number() {
  local current next
  current="$(grep -oE 'CFBundleVersion: "[0-9]+"' "$PROJECT_DIR/project.yml" | grep -oE '[0-9]+')"
  next=$((current + 1))
  say "Build $current to $next"
  sed -i '' "s/CFBundleVersion: \"$current\"/CFBundleVersion: \"$next\"/" "$PROJECT_DIR/project.yml"
  (cd "$PROJECT_DIR" && xcodegen generate >/dev/null)
  BUILD_NUMBER="$next"
}

archive() {
  say "Archiving"
  rm -rf "$ARCHIVE" "$EXPORT_DIR"
  xcodebuild -project "$PROJECT_DIR/StandardPhysics.xcodeproj" \
    -scheme StandardPhysics -configuration Release \
    -destination 'generic/platform=iOS' -archivePath "$ARCHIVE" \
    -allowProvisioningUpdates \
    -authenticationKeyPath "$KEY_PATH" \
    -authenticationKeyID "$SP_ASC_KEY_ID" \
    -authenticationKeyIssuerID "$SP_ASC_ISSUER_ID" \
    archive
}

export_ipa() {
  say "Exporting"
  cat > "$BUILD_DIR/ExportOptions.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>method</key><string>app-store-connect</string>
  <key>destination</key><string>export</string>
  <key>signingStyle</key><string>automatic</string>
  <key>uploadSymbols</key><true/>
</dict>
</plist>
PLIST
  xcodebuild -exportArchive -archivePath "$ARCHIVE" \
    -exportOptionsPlist "$BUILD_DIR/ExportOptions.plist" \
    -exportPath "$EXPORT_DIR" \
    -allowProvisioningUpdates \
    -authenticationKeyPath "$KEY_PATH" \
    -authenticationKeyID "$SP_ASC_KEY_ID" \
    -authenticationKeyIssuerID "$SP_ASC_ISSUER_ID"
}

upload() {
  local ipa
  ipa="$(find "$EXPORT_DIR" -name '*.ipa' | head -1)"
  [ -n "$ipa" ] || { echo "No .ipa came out of the export" >&2; exit 1; }
  say "Uploading $(basename "$ipa")"
  # altool ignores a path and only looks in a handful of conventional
  # directories, so it is told which one to look in rather than given the file.
  # xcodebuild, two steps earlier, takes the path and not the directory.
  API_PRIVATE_KEYS_DIR="$(dirname "$KEY_PATH")" \
    xcrun altool --upload-app -f "$ipa" -t ios \
      --apiKey "$SP_ASC_KEY_ID" --apiIssuer "$SP_ASC_ISSUER_ID"
}

main() {
  require_key
  bump_build_number
  archive
  export_ipa
  upload
  say "Build $BUILD_NUMBER is uploaded. It reaches TestFlight once processing finishes."
  echo "   Commit the build number: git -C $REPO_ROOT add apps/ios && git -C $REPO_ROOT commit -m 'A: build $BUILD_NUMBER'"
}

main "$@"
