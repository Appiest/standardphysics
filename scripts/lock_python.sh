#!/usr/bin/env bash
# Re-resolves the pinned Python dependencies after a pyproject changes.
#
#   scripts/lock_python.sh             keep existing pins where they still fit
#   scripts/lock_python.sh --upgrade   move everything to the newest release
#
# Writes requirements.lock (what the image installs) and requirements-dev.lock
# (what CI and start.sh install). Both are universal, so one file serves Linux
# CI, the x86_64 image and a developer's Mac. The repository's own packages are
# left out: they are installed from the checkout with --no-deps -e.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/" >&2; exit 1; }

LOCAL_PACKAGES=(
  loopforge
  standardphysics-contracts
  standardphysics-fixtures
  standardphysics-pipeline
  standardphysics-agents
  standardphysics-api
)
HEADER="# Pinned by uv pip compile rather than uv.lock, because plain pip installs a requirements file and CI, the image and start.sh all have pip."

lock() {
  local source="$1" target="$2" skip_local=()
  shift 2
  for package in "${LOCAL_PACKAGES[@]}"; do skip_local+=(--no-emit-package "$package"); done
  uv pip compile "$source" --quiet --universal --python-version 3.11 "$@" \
    "${skip_local[@]}" --custom-compile-command "scripts/lock_python.sh" -o "$target"
  { echo "$HEADER"; cat "$target"; } >"$target.tmp"
  mv "$target.tmp" "$target"
}

lock requirements.in requirements.lock "$@"
lock requirements-dev.in requirements-dev.lock "$@"
echo "Wrote requirements.lock and requirements-dev.lock"
