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
#
# It ships only a commit that is already on origin/master, from a clean tree,
# with a successful CI run and a successful iOS run for that exact commit, so
# TestFlight gets what the checks passed. The iOS workflow runs only when its
# paths change; for any other commit, start it by hand with
# `gh workflow run ios.yml --ref master` and ship once it passes.
# SP_SHIP_UNVERIFIED=1 skips those checks, for an emergency, and says so.
#
# Each upload appends its build number and commit to apps/ios/testflight-builds.log,
# which goes into the same commit as the build number.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/../apps/ios" && pwd)"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="${TMPDIR:-/tmp}/standardphysics-ship"
ARCHIVE="$BUILD_DIR/StandardPhysics.xcarchive"
EXPORT_DIR="$BUILD_DIR/export"
SHIPPED_LOG="$PROJECT_DIR/testflight-builds.log"
REQUIRED_WORKFLOWS=(ci.yml ios.yml)

say() { printf '\n== %s\n' "$1"; }
refuse() { echo "Not shipping: $1" >&2; exit 1; }

require_clean_tree() {
  [ -z "$(git -C "$REPO_ROOT" status --porcelain)" ] ||
    refuse "the working tree has uncommitted changes, so the build would not match any commit."
}

require_on_origin_master() {
  git -C "$REPO_ROOT" fetch origin master --quiet ||
    refuse "could not fetch origin/master to check HEAD against it."
  git -C "$REPO_ROOT" merge-base --is-ancestor HEAD origin/master ||
    refuse "HEAD $SHIPPED_COMMIT is not on origin/master. Merge and push it first."
}

workflow_passed() {
  local conclusions
  conclusions="$(gh run list --commit "$SHIPPED_COMMIT" --workflow "$1" --json conclusion --jq '.[].conclusion')" ||
    return 1
  grep -qx success <<< "$conclusions"
}

require_green_workflows() {
  local workflow
  for workflow in "${REQUIRED_WORKFLOWS[@]}"; do
    workflow_passed "$workflow" ||
      refuse "$workflow has no successful run for $SHIPPED_COMMIT. Check \`gh run list --commit $SHIPPED_COMMIT\`."
  done
}

warn_unverified() {
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" >&2
  echo "!! SP_SHIP_UNVERIFIED=1: shipping $SHIPPED_COMMIT without checking" >&2
  echo "!! the tree, origin/master or CI. Testers get whatever this is." >&2
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" >&2
}

require_verified_commit() {
  SHIPPED_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  if [ "${SP_SHIP_UNVERIFIED:-}" = "1" ]; then
    warn_unverified
    VERIFICATION="unverified"
    return
  fi
  require_clean_tree
  require_on_origin_master
  require_green_workflows
  VERIFICATION="verified"
}

record_shipped_build() {
  printf '%s build %s commit %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$BUILD_NUMBER" \
    "$SHIPPED_COMMIT" "$VERIFICATION" >> "$SHIPPED_LOG"
}

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
  sed "s/CFBundleVersion: \"$current\"/CFBundleVersion: \"$next\"/" "$PROJECT_DIR/project.yml" > "$PROJECT_DIR/project.yml.next"
  mv "$PROJECT_DIR/project.yml.next" "$PROJECT_DIR/project.yml"
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
  require_verified_commit
  mkdir -p "$BUILD_DIR"
  bump_build_number
  archive
  export_ipa
  upload
  record_shipped_build
  say "Build $BUILD_NUMBER of $SHIPPED_COMMIT is uploaded. It reaches TestFlight once processing finishes."
  echo "   Commit the build number: git -C $REPO_ROOT add apps/ios && git -C $REPO_ROOT commit -m 'A: build $BUILD_NUMBER'"
}

main "$@"
