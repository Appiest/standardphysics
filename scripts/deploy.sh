#!/usr/bin/env bash
# Deploys what is on master to the Droplet, from here.
#
#   scripts/deploy.sh
#
# One command instead of a session: it pulls on the box, fetches or builds the
# image, and runs doctor.sh, streaming everything back. Set SP_DEPLOY_HOST in
# your shell if the box moves.
#
# The image it starts is the one CI tested. The image job in ci.yml pushes each
# master commit that passed the smoke test to GHCR as
# ghcr.io/imhaohao/standardphysics:<sha>, and this pulls that tag. When the
# commit has no published image yet (CI still running, or the package not yet
# readable by the box), or SP_DEPLOY_BUILD=1 asks for it, the box builds the
# image itself, as it did before CI published anything. SP_DEPLOY_IMAGE points
# at another registry repository.
#
# Either way the image is tagged standardphysics:<sha>, and each deploy adds a
# line to /var/log/standardphysics-deploys.log on the box with the commit and
# the registry digest it started, or built-on-droplet, so what to roll back to
# is written down. docs/DEPLOY.md has the rollback. A box left on an older
# commit by a rollback goes back to master here before it pulls.
#
# It refuses to deploy while the API has jobs queued or running. The restart
# throws away whatever a bake has done so far, so it waits for the queue to
# empty unless SP_DEPLOY_FORCE=1 says to go anyway. A queue it cannot read is a
# refusal too, not an empty queue: a stopped or wedged API is exactly when
# nobody knows what it was doing. The image is pulled or built before the
# queue is read, so the build's minutes are not part of the window in which a new upload can
# start a job that the restart then kills. docs/DEPLOY.md says what is left.
#
# It refuses to deploy behind your own work. The Droplet pulls master from
# GitHub, so a commit still sitting on this laptop is not going anywhere, and
# a deploy that silently ships the previous commit is worse than one that
# stops and says so.
set -euo pipefail

HOST="${SP_DEPLOY_HOST:-root@api.standardphysics.app}"
DIR="${SP_DEPLOY_DIR:-/root/standardphysics}"
LOCK="${SP_DEPLOY_LOCK:-/var/lock/standardphysics-deploy}"
HISTORY="${SP_DEPLOY_HISTORY:-/var/log/standardphysics-deploys.log}"
FORCE="${SP_DEPLOY_FORCE:-}"
BUILD="${SP_DEPLOY_BUILD:-}"
IMAGE="${SP_DEPLOY_IMAGE:-ghcr.io/imhaohao/standardphysics}"

# Read the way the Droplet's own tools read the queue: the API container's
# Python opening the database it holds, read-only, so this cannot take a lock
# a job needs. The image has no sqlite3 command.
IN_FLIGHT_QUERY="import sqlite3
database = sqlite3.connect('file:/data/standardphysics.sqlite3?mode=ro', uri=True)
query = \"SELECT COUNT(*) FROM jobs WHERE state IN ('queued', 'running')\"
print(database.execute(query).fetchone()[0])"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

say() { printf '\n== %s\n' "$1"; }

# Whichever remote is the one the Droplet pulls from. A fork has it as
# "upstream"; a plain clone has it as "origin". Checking against the wrong one
# silently skips the guard below, so find it rather than assume it.
master_remote() {
  local remote
  for remote in upstream origin; do
    if git remote get-url "$remote" >/dev/null 2>&1; then
      echo "$remote"
      return 0
    fi
  done
  return 1
}

warn_about_unpushed() {
  cd "$REPO_ROOT"
  local remote
  remote="$(master_remote)" || {
    echo "No upstream or origin remote here, so I cannot tell whether master has your work." >&2
    echo "Deploying anyway; check yourself that what you want is on master." >&2
    return 0
  }
  git fetch "$remote" --quiet 2>/dev/null || return 0
  local ahead
  ahead="$(git rev-list --count "$remote/master..HEAD" 2>/dev/null || echo 0)"
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
git checkout --quiet master
git pull --ff-only
export GIT_SHA=\$(git rev-parse HEAD)
cd deploy/digitalocean
published='$IMAGE':\$GIT_SHA
if [ '$BUILD' != 1 ] && docker pull --quiet \"\$published\"; then
  docker tag \"\$published\" standardphysics:\$GIT_SHA
  docker tag \"\$published\" standardphysics:latest
  origin=\$(docker image inspect --format '{{index .RepoDigests 0}}' \"\$published\")
else
  echo \"Could not pull \$published, because CI has not published it yet or the box cannot read the package, so building it here.\"
  docker compose build
  origin=built-on-droplet
fi
in_flight=\$(docker compose exec -T api /opt/venv/bin/python -c $(printf %q "$IN_FLIGHT_QUERY") 2>/dev/null) || in_flight=unknown
if [ '$FORCE' != 1 ] && ! [[ \"\$in_flight\" =~ ^[0-9]+\$ ]]; then
  echo 'I could not read the job queue from the API container, so I cannot tell what a restart would interrupt.' >&2
  echo 'Check it with docker compose ps and ./doctor.sh, or run with SP_DEPLOY_FORCE=1 to deploy anyway.' >&2
  exit 69
fi
if [ '$FORCE' != 1 ] && [ \"\$in_flight\" -gt 0 ]; then
  echo \"The API has \$in_flight job(s) queued or running, and a deploy restarts it.\" >&2
  echo 'Wait for them to finish and run this again, which reuses the image it just fetched,' >&2
  echo 'or run with SP_DEPLOY_FORCE=1 to interrupt them.' >&2
  exit 75
fi
docker compose up -d
echo \"\$(date -u +%Y-%m-%dT%H:%M:%SZ) \$GIT_SHA \$origin\" >> '$HISTORY'
./doctor.sh"
}

main() {
  warn_about_unpushed
  deploy
  say "Done. https://standardphysics.app"
}

main "$@"
