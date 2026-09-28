#!/usr/bin/env bash
# Runs the regression tests that need Blender inside a built image, which has it.
#
#   scripts/test_blender_in_image.sh standardphysics:ci
#
# The Python job in CI has no Blender, so these tests skip there, and a broken
# export, render or bake would pass it. The image carries the pinned Blender
# the server runs, so this is where they can run for real.
#
# Every test file that decides whether to run by asking blender_path() or
# looking for a `blender` command runs here, so a new Blender test joins by
# using either check rather than by being added to a list. A hand-kept list
# once left three of them, the camera raster among them, skipping in every job.
#
# The checkout is mounted read-only and pytest is installed into /tmp for the
# one run, so the image that ships holds no tests. The pytest pins, and trimesh,
# which the camera raster test builds its fixture meshes with, come from
# requirements-dev.lock, the versions the Python job uses. /opt/blender goes on
# PATH for the tests that look for a `blender` command.
#
# A skip that names Blender, or that could not import a module, fails the run.
# In this image Blender is meant to be there, and a test that quietly skipped
# would prove nothing.
set -euo pipefail

IMAGE="${1:?usage: scripts/test_blender_in_image.sh <image>}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

blender_tests() {
  git -C "$REPO_ROOT" grep -l -E 'blender_path\(|which\("blender"\)' -- '*test_*.py' | tr '\n' ' '
}

pytest_pins() {
  grep -E '^(pytest|iniconfig|pluggy|pygments|trimesh)==' "$REPO_ROOT/requirements-dev.lock" | tr '\n' ' '
}

run_in_image() {
  docker run --rm \
    --volume "$REPO_ROOT:/src:ro" \
    --workdir /src \
    --env HOME=/tmp \
    --env PYTHONDONTWRITEBYTECODE=1 \
    --env PYTHONPATH=/tmp/pytest \
    --env PATH=/opt/blender:/opt/venv/bin:/usr/local/bin:/usr/bin:/bin \
    --entrypoint bash \
    "$IMAGE" -c "pip install --quiet --no-cache-dir --no-deps --target /tmp/pytest $(pytest_pins) \
      && python -m pytest -p no:cacheprovider -q -rs $(blender_tests)"
}

main() {
  local log
  log="$(mktemp)"
  run_in_image | tee "$log"
  if grep -E '^SKIPPED.*([Bb]lender|could not import)' "$log" >/dev/null; then
    echo "Tests skipped for want of Blender or a module inside $IMAGE, which is meant to have them." >&2
    exit 1
  fi
}

main "$@"
