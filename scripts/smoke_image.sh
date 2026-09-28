#!/usr/bin/env bash
# Proves a built image can hold an account and measure a room before anyone
# deploys it.
#
#   scripts/smoke_image.sh [image]      default image: standardphysics:ci
#
# Starts the image in its "api" role, waits for /health, then signs up, signs
# in again from a fresh cookie jar and reads the session back. Then
# scripts/smoke_scan.py uploads Apple's sample RoomPlan bedroom, waits for the
# worker to make it ready, and checks the assessment, the Blender-rendered
# finding picture and the Blender-exported model; its docstring says what it
# does without model keys, which CI never has. Then it starts
# the "all" role and checks the workspace serves its sign-in page and proxies
# /api through to the API beside it, which is the rewrite `next build` baked in.
# CI runs this after building the image; it runs the same way on a laptop.
set -euo pipefail

IMAGE="${1:-standardphysics:ci}"
API_PORT="${SMOKE_API_PORT:-18787}"
WEB_PORT="${SMOKE_WEB_PORT:-13000}"
SCAN_SECONDS="${SMOKE_SCAN_SECONDS:-300}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
EMAIL="smoke-$(date +%s)@example.com"
PASSWORD="smoke-test-password"
CONTAINERS=()

cleanup() {
  for container in "${CONTAINERS[@]}"; do docker rm -f "$container" >/dev/null 2>&1 || true; done
  rm -rf "$WORK"
}
trap cleanup EXIT

fail() {
  echo "smoke test failed: $1" >&2
  for container in "${CONTAINERS[@]}"; do
    echo "--- logs from $container" >&2
    docker logs --tail 80 "$container" >&2 || true
  done
  exit 1
}

start() {
  local name="$1" role="$2"
  shift 2
  docker run -d --name "$name" "$@" "$IMAGE" "$role" >/dev/null
  CONTAINERS+=("$name")
}

wait_for() {
  local url="$1" seconds="$2"
  for _ in $(seq 1 "$seconds"); do
    curl -fsS "$url" >/dev/null 2>&1 && return
    sleep 1
  done
  fail "$url did not answer within ${seconds}s"
}

expect_status() {
  local wanted="$1" label="$2"
  shift 2
  local status
  status="$(curl -sS -o "$WORK/body" -w '%{http_code}' "$@")"
  [ "$status" = "$wanted" ] || fail "$label answered $status, expected $wanted: $(head -c 400 "$WORK/body")"
  echo "ok  $label ($status)"
}

json_body() {
  printf '{"email":"%s","password":"%s"%s}' "$EMAIL" "$PASSWORD" "${1:-}"
}

check_account_round_trip() {
  local api="http://127.0.0.1:$API_PORT"
  expect_status 201 "sign up" -c "$WORK/signup.jar" -H 'content-type: application/json' \
    -d "$(json_body ',"shop_name":"Smoke test shop"')" "$api/api/auth/sign-up"
  expect_status 200 "sign in" -c "$WORK/signin.jar" -H 'content-type: application/json' \
    -d "$(json_body)" "$api/api/auth/sign-in"
  expect_status 200 "session" -b "$WORK/signin.jar" "$api/api/auth/session"
  grep -q "\"$EMAIL\"" "$WORK/body" || fail "the session names someone else: $(cat "$WORK/body")"
  expect_status 401 "wrong password" -H 'content-type: application/json' \
    -d "{\"email\":\"$EMAIL\",\"password\":\"not-the-password\"}" "$api/api/auth/sign-in"
}

check_a_real_scan() {
  python3 "$REPO/scripts/smoke_scan.py" --api "http://127.0.0.1:$API_PORT" --timeout "$SCAN_SECONDS" \
    || fail "the image did not process the sample bedroom"
}

check_workspace() {
  local web="http://127.0.0.1:$WEB_PORT"
  expect_status 200 "workspace sign-in page" "$web/sign-in"
  expect_status 401 "workspace proxies /api" "$web/api/auth/session"
}

start sp-smoke-api api -p "127.0.0.1:$API_PORT:8787"
wait_for "http://127.0.0.1:$API_PORT/health" 90
check_account_round_trip
check_a_real_scan

start sp-smoke-all all -p "127.0.0.1:$WEB_PORT:3000"
wait_for "http://127.0.0.1:$WEB_PORT/sign-in" 90
check_workspace

echo "The image passed its smoke test."
