#!/usr/bin/env bash
# Deploys what is on master to the Droplet, from here.
#
#   scripts/deploy.sh
#
# One command instead of a session: it pulls on the box, rebuilds, and runs
# doctor.sh, streaming everything back. Set SP_DEPLOY_HOST in your shell if the
# box moves.
#
# It refuses to deploy behind your own work. The Droplet pulls master from
# GitHub, so a commit still sitting on this laptop is not going anywhere, and
# a deploy that silently ships the previous commit is worse than one that
# stops and says so.
set -euo pipefail

HOST="${SP_DEPLOY_HOST:-root@api.standardphysics.app}"
DIR="${SP_DEPLOY_DIR:-/root/standardphysics}"
LOCK="${SP_DEPLOY_LOCK:-/var/lock/standardphysics-deploy}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

say() { printf '\n== %s\n' "$1"; }

warn_about_unpushed() {
  cd "$REPO_ROOT"
  git fetch upstream --quiet 2>/dev/null || return 0
  local ahead
  ahead="$(git rev-list --count upstream/master..HEAD 2>/dev/null || echo 0)"
  if [ "$ahead" -gt 0 ]; then
    echo "You have $ahead commit(s) not on master. The Droplet pulls master, so they will not ship." >&2
    echo "Push them first, or run with SP_DEPLOY_ANYWAY=1 to deploy master as it stands." >&2
    [ "${SP_DEPLOY_ANYWAY:-}" = "1" ] || exit 1
  fi
  if [ -n "$(git status --porcelain)" ]; then
    echo "Note: this working tree has uncommitted changes. They are not part of this deploy."
  fi
}

deploy() {
  say "Deploying master to $HOST"
  # The commands go as an argument, not on stdin. A heredoc takes stdin over,
  # and ssh then has no way to ask for a key passphrase: it gives up and
  # reports publickey, which reads as a key the server will not accept rather
  # than a question it could not ask. -t gives the prompt a terminal to use.
  #
  # The lock is on the box and not on this machine, because two people on two
  # laptops collide the same way one person running it twice does. Two deploys
  # racing to recreate a container leave the name taken, the stack half torn
  # down and the site answering 502, which is how this was learned.
  ssh -t "$HOST" "set -euo pipefail
exec 9>'$LOCK'
if ! flock -n 9; then
  echo 'Another deploy is already running on this box. Wait for it to finish.' >&2
  exit 75
fi
cd '$DIR'
git pull --ff-only
cd deploy/digitalocean
docker compose up -d --build
./doctor.sh"
}

main() {
  warn_about_unpushed
  deploy
  say "Done. https://standardphysics.app"
}

main "$@"
