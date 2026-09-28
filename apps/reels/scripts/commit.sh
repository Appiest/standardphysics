#!/bin/zsh
# Usage: scripts/commit.sh "<message>" <path> [<path>...]
# Commits only the given paths and pushes the branch. Several agents share this checkout, so a busy git index is retried.
message="$1"; shift
trailer=$'\n\nCo-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>'
for attempt in {1..20}; do
  if git add -- "$@" 2>/dev/null && git commit -q -m "$message$trailer" -- "$@"; then
    break
  fi
  sleep $((RANDOM % 4 + 1))
done
for attempt in {1..10}; do
  git push -q origin HEAD 2>/dev/null && { git log --oneline -1; exit 0; }
  sleep $((RANDOM % 5 + 2))
done
echo "push failed" >&2
exit 1
