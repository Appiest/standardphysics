#!/usr/bin/env bash
# Starts Standard Physics from a clean clone: installs what's missing, then runs
# the API on :8787 and the web workspace on :3000. Ctrl-C stops both.
#
#   ./start.sh          development server, reloads on save
#   ./start.sh --prod   production build, for the demo
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

MODE="dev"
case "${1:-}" in
  "") ;;
  --prod) MODE="prod" ;;
  -h | --help) sed -n 2,6p "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "Unknown option: $1. Run ./start.sh --help." >&2; exit 2 ;;
esac

fail() { echo "$1" >&2; exit 1; }

hash_files() {
  if command -v sha256sum >/dev/null; then cat "$@" | sha256sum | cut -d' ' -f1
  else cat "$@" | shasum -a 256 | cut -d' ' -f1; fi
}

find_python() {
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null &&
      "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      echo "$candidate"; return
    fi
  done
  fail "Python 3.11 or newer is required. Install it, then run ./start.sh again."
}

check_node() {
  command -v node >/dev/null || fail "Node 20.9 or newer is required. Install it, then run ./start.sh again."
  node -e 'const [a, b] = process.versions.node.split(".").map(Number); process.exit(a > 20 || (a === 20 && b >= 9) ? 0 : 1)' ||
    fail "Node $(node -v) is too old. Next.js needs 20.9 or newer."
}

check_port_free() {
  if command -v lsof >/dev/null && lsof -iTCP:"$1" -sTCP:LISTEN -t >/dev/null 2>&1; then
    fail "Port $1 is already in use. Stop whatever is running there, then run ./start.sh again."
  fi
}

install_into_venv() {
  # A .venv that uv created has no pip inside it, so ask uv to do the install.
  if .venv/bin/python -m pip --version >/dev/null 2>&1; then
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet "$@"
  elif command -v uv >/dev/null; then
    VIRTUAL_ENV="$ROOT/.venv" uv pip install --quiet "$@"
  else
    fail "The .venv has no pip in it. Delete .venv, then run ./start.sh again."
  fi
}

install_python() {
  local stamp=".venv/.installed" wanted
  wanted="$(hash_files "$0" pyproject.toml packages/*/pyproject.toml services/api/pyproject.toml)"
  [ -x .venv/bin/python ] || { echo "Creating .venv"; "$(find_python)" -m venv .venv; }
  [ "$(cat "$stamp" 2>/dev/null)" = "$wanted" ] && return
  echo "Installing Python packages"
  install_into_venv -e . -e packages/contracts -e packages/fixtures -e packages/pipeline \
    -e "packages/agents[observability]" -e "services/api[test]"
  echo "$wanted" >"$stamp"
}

install_web() {
  local stamp="apps/web/node_modules/.installed" wanted
  wanted="$(hash_files apps/web/package.json apps/web/package-lock.json)"
  [ "$(cat "$stamp" 2>/dev/null)" = "$wanted" ] && return
  echo "Installing web packages"
  (cd apps/web && npm ci --no-audit --no-fund --loglevel=error)
  echo "$wanted" >"$stamp"
}

wait_for_api() {
  for _ in $(seq 1 60); do
    curl -fsS http://127.0.0.1:8787/api/scans >/dev/null 2>&1 && return
    kill -0 "$API_PID" 2>/dev/null || fail "The API stopped while starting. Its log is above."
    sleep 1
  done
  fail "The API did not answer on :8787 within 60 seconds."
}

check_node
check_port_free 8787
check_port_free 3000
install_python
install_web

if ! command -v blender >/dev/null && [ -z "${BLENDER:-}" ] && [ ! -d /Applications/Blender.app ]; then
  echo "Blender isn't installed, so uploaded scans show as boxes and the report has no pictures."
  echo "To fix that on a Mac: brew install --cask --force blender"
fi
[ -f .env ] || echo "No .env yet. Scans are checked without one; copy .env.example to .env for model calls."

.venv/bin/python -m standardphysics_api &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true' EXIT INT TERM
wait_for_api

cd apps/web
if [ "$MODE" = "prod" ]; then
  npm run build
  echo "Open http://localhost:3000"
  npm run start
else
  echo "Open http://localhost:3000"
  npm run dev
fi
