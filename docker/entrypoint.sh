#!/usr/bin/env bash
# Runs the API, the web workspace, or both in one container.
#
#   api   the FastAPI service, on $PORT or 8787
#   web   the Next workspace, on $PORT or 3000
#   all   both, with the workspace on $PORT and the API on 8787 beside it
#
# "all" is for a host that gives you one container and one port. Compose runs
# two containers from this same image instead, so each can be scaled or
# restarted on its own.
set -euo pipefail

ROLE="${1:-all}"
API_PORT="${SP_API_PORT:-8787}"

start_api() {
  SP_API_PORT="$API_PORT" PORT="$API_PORT" exec /opt/venv/bin/python -m standardphysics_api
}

start_web() {
  cd /app/apps/web
  exec node_modules/.bin/next start -p "${PORT:-3000}"
}

# Next is ready in a fraction of a second and the API takes several, so a web
# process started alongside it proxied the first requests to a closed port and
# answered them with a 500. Waits for the API's own /health, and gives up if
# the API exits or stays silent for SP_API_READY_SECONDS.
wait_for_api() {
  local api_pid="$1" deadline=$((SECONDS + ${SP_API_READY_SECONDS:-120}))
  until node -e "fetch('http://127.0.0.1:$API_PORT/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"; do
    kill -0 "$api_pid" 2>/dev/null || { echo "the API exited before it answered /health" >&2; return 1; }
    [ "$SECONDS" -lt "$deadline" ] || { echo "the API did not answer /health in time" >&2; return 1; }
    sleep 1
  done
}

case "$ROLE" in
  api) start_api ;;
  web) start_web ;;
  all)
    SP_API_PORT="$API_PORT" PORT="$API_PORT" /opt/venv/bin/python -m standardphysics_api &
    api_pid=$!
    # A web process that outlives a dead API serves errors to every visitor, so
    # the container goes down with whichever half stops first.
    trap 'kill -TERM "$api_pid" 2>/dev/null || true' TERM INT
    wait_for_api "$api_pid" || { kill -TERM "$api_pid" 2>/dev/null || true; exit 1; }
    cd /app/apps/web
    SP_API_ORIGIN="http://127.0.0.1:$API_PORT" node_modules/.bin/next start -p "${PORT:-3000}" &
    web_pid=$!
    wait -n "$api_pid" "$web_pid"
    kill -TERM "$api_pid" "$web_pid" 2>/dev/null || true
    wait
    ;;
  *)
    echo "unknown role: $ROLE. Use api, web or all." >&2
    exit 2
    ;;
esac
