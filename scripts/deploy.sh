#!/usr/bin/env bash
# Deploys what is on master to the Droplet, from here.
#
#   scripts/deploy.sh
#
# One command instead of a session: it pulls on the box, fetches the image,
# and runs doctor.sh, streaming everything back. Set SP_DEPLOY_HOST in your
# shell if the box moves.
#
# The image it starts is the one CI tested. The publish job in ci.yml tags a
# master commit's image as ghcr.io/imhaohao/standardphysics:<sha> only after
# every check in that workflow passed for the commit, and this pulls that tag.
# When the tag cannot be pulled (CI still running or failed, or the package
# not readable by the box), it refuses with exit code 66 rather than ship
# something nothing tested. SP_DEPLOY_BUILD=1 builds the image on the box
# instead, says so, and records the deploy as untested-local-build.
# SP_DEPLOY_IMAGE points at another registry repository.
#
# Either way the image is tagged standardphysics:<sha>. After the restart it
# runs deploy/digitalocean/check_serving.py in the API container every five
# seconds, for up to SP_DEPLOY_READY_SECONDS (180 by default), until
# /health/ready answers 200, /health/details reports the commit just deployed
# and the workspace serves its sign-in page. It also asks
# https://$APP_DOMAIN/api/auth/session, from APP_DOMAIN in the box's .env,
# and expects the API's signed-out 401, which proves Caddy's certificate and
# its routing of the browser's /api requests. SP_DEPLOY_PUBLIC_ORIGIN names
# another origin, and a box with no APP_DOMAIN skips it. Only then does it add a line to
# /var/log/standardphysics-deploys.log on the box with the commit and the
# registry digest it started, or untested-local-build, so that file lists only
# deploys that came up and is the list of what to roll back to. A deploy that
# never passes the check is left running for you to look at, is not written
# down, prints the rollback to the last deploy in that file, runs doctor.sh
# and exits 70. docs/DEPLOY.md has the rollback. A box left on an older
# commit by a rollback goes back to master here before it pulls.
#
# It drains the API before the restart. Once the image is ready it turns on
# the drain flag in the API container (python -m standardphysics_api.drain on),
# so requests that would queue new work get 503 and a minute to retry, and the
# worker starts no queued job. Then it waits, up to SP_DEPLOY_DRAIN_SECONDS
# (1200 by default), for the job the worker is running to finish, because the
# restart throws away whatever a bake has done so far. Queued jobs wait in the
# database and the new worker takes them. A job still running at the end of
# that wait refuses the deploy with exit code 75, and a queue it cannot read
# refuses it with 69: a stopped or wedged API is exactly when nobody knows
# what it was doing. SP_DEPLOY_FORCE=1 goes ahead in either case. The drain
# is turned off once the new container is serving, and on every way out
# before that, so a failed deploy never leaves the site refusing work.
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
READY_SECONDS="${SP_DEPLOY_READY_SECONDS:-180}"
DRAIN_SECONDS="${SP_DEPLOY_DRAIN_SECONDS:-1200}"
PUBLIC_ORIGIN="${SP_DEPLOY_PUBLIC_ORIGIN:-}"

# Read the way the Droplet's own tools read the queue: the API container's
# Python opening the database it holds, read-only, so this cannot take a lock
# a job needs. The image has no sqlite3 command.
RUNNING_QUERY="import sqlite3
database = sqlite3.connect('file:/data/standardphysics.sqlite3?mode=ro', uri=True)
query = \"SELECT COUNT(*) FROM jobs WHERE state = 'running'\"
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
api_python() {
  docker compose exec -T api /opt/venv/bin/python \"\$@\"
}
running_jobs() {
  api_python -c $(printf %q "$RUNNING_QUERY") 2>/dev/null || echo unknown
}
refuse_unreadable_queue() {
  echo 'I could not read the job queue from the API container, so I cannot tell what a restart would interrupt.' >&2
  echo 'Check it with docker compose ps and ./doctor.sh, or run with SP_DEPLOY_FORCE=1 to deploy anyway.' >&2
  exit 69
}
# A bake or a discovery run holds up to about a gigabyte, and building the
# image beside one on a 4 GB box has killed jobs, so a local build waits for
# the worker to be idle.
refuse_while_busy() {
  running=\$(running_jobs)
  [ '$FORCE' = 1 ] && return 0
  [[ \"\$running\" =~ ^[0-9]+\$ ]] || refuse_unreadable_queue
  if [ \"\$running\" -gt 0 ]; then
    echo \"The API has \$running job(s) running, and building the image beside them can run the box out of memory.\" >&2
    echo 'Wait for them to finish and run this again, or run with SP_DEPLOY_FORCE=1 to build anyway.' >&2
    exit 75
  fi
}
release_drain() {
  api_python -m standardphysics_api.drain off >/dev/null 2>&1 && return 0
  docker compose run --rm -T --no-deps --entrypoint /opt/venv/bin/python api -m standardphysics_api.drain off >/dev/null 2>&1 && return 0
  echo 'I could not turn the drain off, so the API is refusing new work. On the box run:' >&2
  echo \"  cd '$DIR/deploy/digitalocean' && docker compose exec api /opt/venv/bin/python -m standardphysics_api.drain off\" >&2
}
# Each deploy leaves a 6 GB image behind, and a day of them filled the 77 GB
# disk. The image now serving and the last one that came up, which is the
# rollback, stay; the rest and the build cache go. A failure here is not a
# failed deploy, because the new commit is already serving.
remove_old_images() {
  docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(standardphysics|$IMAGE):[0-9a-f]{40}\$' | while read -r ref; do
    case \${ref##*:} in
      \"\$1\"|\"\$2\") ;;
      *) docker rmi \"\$ref\" >/dev/null 2>&1 || true ;;
    esac
  done || true
  docker image prune -f >/dev/null 2>&1 || true
  docker builder prune -af >/dev/null 2>&1 || true
}
# The drain stops new work being queued or started, and the trap turns it off
# on any way out. A worker that read the flag a moment before it was set may
# still be claiming a job, so the first look at the queue waits a beat.
drain_then_wait_for_running_jobs() {
  trap release_drain EXIT
  if ! api_python -m standardphysics_api.drain on && [ '$FORCE' != 1 ]; then
    refuse_unreadable_queue
  fi
  sleep 2
  waited=0
  running=\$(running_jobs)
  until [ '$FORCE' = 1 ] || [ \"\$running\" = 0 ]; do
    [[ \"\$running\" =~ ^[0-9]+\$ ]] || refuse_unreadable_queue
    if [ \"\$waited\" -ge '$DRAIN_SECONDS' ]; then
      echo \"After \$waited seconds the API still has \$running job(s) running, and a deploy restarts it.\" >&2
      echo 'Run this again once they finish, which reuses the image it just fetched,' >&2
      echo 'or run with SP_DEPLOY_FORCE=1 to interrupt them.' >&2
      exit 75
    fi
    echo \"Waiting for \$running running job(s) to finish. New work is refused until the deploy is done.\"
    sleep 5
    waited=\$((waited + 5))
    running=\$(running_jobs)
  done
}
if [ '$BUILD' = 1 ]; then
  refuse_while_busy
  echo \"SP_DEPLOY_BUILD=1: building \$GIT_SHA here. This image is untested; CI has not run its checks against it.\" >&2
  docker compose build
  origin=untested-local-build
elif docker pull --quiet \"\$published\"; then
  docker tag \"\$published\" standardphysics:\$GIT_SHA
  docker tag \"\$published\" standardphysics:latest
  origin=\$(docker image inspect --format '{{index .RepoDigests 0}}' \"\$published\")
else
  echo \"There is no tested image for this commit: \$published could not be pulled.\" >&2
  echo 'CI publishes it once every check has passed on master. Wait for that run to go green,' >&2
  echo 'or check that this box can read the package (docs/DEPLOY.md, The image CI tested).' >&2
  echo 'SP_DEPLOY_BUILD=1 builds it on the box instead, untested.' >&2
  exit 66
fi
public_origin='$PUBLIC_ORIGIN'
app_domain=\$(grep -E '^APP_DOMAIN=' .env 2>/dev/null | tail -n 1 | cut -d= -f2-) || true
[ -n \"\$public_origin\" ] || [ -z \"\$app_domain\" ] || public_origin=https://\$app_domain
drain_then_wait_for_running_jobs
docker compose up -d
waited=0
until verdict=\$(docker compose exec -T api /opt/venv/bin/python - \"\$GIT_SHA\" \"\$public_origin\" < check_serving.py 2>&1); do
  if [ \"\$waited\" -ge '$READY_SECONDS' ]; then
    echo \"After \$waited seconds the new stack is still not serving \$GIT_SHA: \$verdict\" >&2
    previous=\$(cat '$HISTORY.1' '$HISTORY' 2>/dev/null | tail -n 1 | cut -d ' ' -f 2) || true
    if [ -n \"\$previous\" ]; then
      echo \"To go back to \$previous, the last deploy that came up:\" >&2
      echo \"  cd '$DIR' && git checkout \$previous && cd deploy/digitalocean && GIT_SHA=\$previous docker compose up -d\" >&2
    else
      echo 'No earlier deploy is written down in $HISTORY, so there is no commit to name for a rollback.' >&2
    fi
    ./doctor.sh || true
    exit 70
  fi
  sleep 5
  waited=\$((waited + 5))
done
echo \"\$verdict\"
release_drain
trap - EXIT
rollback=\$(cat '$HISTORY.1' '$HISTORY' 2>/dev/null | tail -n 1 | cut -d ' ' -f 2) || true
echo \"\$(date -u +%Y-%m-%dT%H:%M:%SZ) \$GIT_SHA \$origin\" >> '$HISTORY'
remove_old_images \"\$GIT_SHA\" \"\$rollback\"
./doctor.sh"
}

main() {
  warn_about_unpushed
  deploy
  say "Done. https://standardphysics.app"
}

main "$@"
