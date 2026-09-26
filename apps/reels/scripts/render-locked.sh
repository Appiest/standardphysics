#!/bin/zsh
# Usage: scripts/render-locked.sh <CompositionId> [<CompositionId>...]
# The photo-textured Moffitt model is heavy, so renders from different agents take turns instead of running at once.
lock=/tmp/standardphysics-reels-render.lock
until mkdir "$lock" 2>/dev/null; do sleep 5; done
trap 'rmdir "$lock"' EXIT
CONCURRENCY=${CONCURRENCY:-3} node "${0:A:h}/render-all.mjs" "$@"
