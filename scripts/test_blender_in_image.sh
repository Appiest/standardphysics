#!/usr/bin/env bash
# Runs the regression tests that need Blender inside a built image, which has it.
#
#   scripts/test_blender_in_image.sh standardphysics:ci
#
# The Python job in CI has no Blender, so these tests skip there, and a broken
# export, render or bake would pass it. The image carries the pinned Blender
# the server runs, so this is where they can run for real.
#
# The checkout is mounted read-only and pytest is installed into /tmp for the
# one run, so the image that ships holds no tests. The pytest pins come from
# requirements-dev.lock, the versions the Python job uses. /opt/blender goes on
# PATH because some of these tests look for a `blender` command rather than
# asking blender_path().
#
# A skip that names Blender fails the run. In this image Blender is meant to be
# there, and a test that quietly skipped would prove nothing.
set -euo pipefail

IMAGE="${1:?usage: scripts/test_blender_in_image.sh <image>}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

BLENDER_TESTS=(
  tests/test_render.py
  tests/test_real_ingest.py
  tests/test_audit_lane_b.py
  tests/test_texture_baker.py
  packages/pipeline/tests/test_scan_atlas.py
  packages/pipeline/tests/test_scan_glb_colour.py
)

pytest_pins() {
  grep -E '^(pytest|iniconfig|pluggy|pygments)==' "$REPO_ROOT/requirements-dev.lock" | tr '\n' ' '
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
      && python -m pytest -p no:cacheprovider -q -rs ${BLENDER_TESTS[*]}"
}

main() {
  local log
  log="$(mktemp)"
  run_in_image | tee "$log"
  if grep -E '^SKIPPED.*[Bb]lender' "$log" >/dev/null; then
    echo "Tests skipped for want of Blender inside $IMAGE, which is meant to have it." >&2
    exit 1
  fi
}

main "$@"
