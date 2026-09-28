#!/usr/bin/env bash
# Checks the things that stop this deployment from starting, and says what to
# run for each one that is wrong. Read-only: it changes nothing.
#
#   ./doctor.sh
#
# Written because the symptom is almost never the cause. Caddy reporting that
# its dependency failed to start tells you nothing about a mount point owned
# by the wrong user, which is what it usually is.
set -uo pipefail

cd "$(dirname "$0")" || exit 1

PASS=0
FAIL=0

ok()   { printf '  ok    %s\n' "$1"; PASS=$((PASS + 1)); }
bad()  { printf '  BAD   %s\n' "$1"; printf '        fix: %s\n' "$2"; FAIL=$((FAIL + 1)); }
note() { printf '  --    %s\n' "$1"; }
head_() { printf '\n%s\n' "$1"; }

env_value() {
  [ -f .env ] || return 1
  grep -E "^$1=" .env | tail -1 | cut -d= -f2-
}

check_env_file() {
  head_ "Configuration"
  if [ ! -f .env ]; then
    bad ".env is missing" "cp env.example .env && nano .env"
    return
  fi
  ok ".env exists"

  for key in API_DOMAIN APP_DOMAIN SCANS_PATH OPENROUTER_API_KEY; do
    if [ -z "$(env_value "$key")" ]; then
      bad "$key is empty" "set it in .env"
    else
      ok "$key is set"
    fi
  done
}

check_mount() {
  head_ "Scan storage"
  local path
  path="$(env_value SCANS_PATH)"
  if [ -z "$path" ]; then
    note "no SCANS_PATH to check"
    return
  fi

  if [ ! -d "$path" ]; then
    bad "$path does not exist" "VOLUME_NAME=standardphysics-scans ./setup.sh"
    return
  fi
  ok "$path exists"

  if mountpoint -q "$path"; then
    ok "$path is the attached volume"
  else
    bad "$path is on the Droplet disk, not the volume" \
        "check the volume is attached, then VOLUME_NAME=standardphysics-scans ./setup.sh"
  fi

  local owner
  owner="$(stat -c '%u' "$path")"
  if [ "$owner" = "10001" ]; then
    ok "$path is owned by the container's user"
  else
    bad "$path is owned by uid $owner, the container runs as 10001" \
        "chown -R 10001:10001 $path"
  fi

  note "space: $(df -h --output=avail "$path" | tail -1 | tr -d ' ') free"
}

check_memory() {
  head_ "Memory"
  local swap_kb
  swap_kb="$(awk '/SwapTotal/ {print $2}' /proc/meminfo)"
  if [ "${swap_kb:-0}" -gt 0 ]; then
    ok "swap is on ($((swap_kb / 1024))MB)"
  else
    bad "no swap, the workspace build will be killed partway" "./setup.sh"
  fi
  note "ram: $(free -h | awk '/^Mem:/ {print $2 " total, " $7 " available"}')"
}

check_dns() {
  head_ "Names"
  local here
  here="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo unknown)"
  note "this droplet: $here"

  for key in API_DOMAIN APP_DOMAIN; do
    local name resolved
    name="$(env_value "$key")"
    [ -z "$name" ] && continue
    resolved="$(getent hosts "$name" | awk '{print $1}' | head -1)"
    if [ -z "$resolved" ]; then
      bad "$name does not resolve" "add an A record for it pointing at $here, then wait"
    elif [ "$resolved" = "$here" ] || [ "$here" = "unknown" ]; then
      ok "$name resolves to $resolved"
    else
      bad "$name resolves to $resolved, not $here" "correct the A record"
    fi
  done
}

check_containers() {
  head_ "Containers"
  if ! docker compose ps --format '{{.Service}} {{.State}}' >/dev/null 2>&1; then
    bad "docker compose cannot read this project" "run this from deploy/digitalocean"
    return
  fi

  local any=0
  while read -r service state; do
    [ -z "$service" ] && continue
    any=1
    if [ "$state" = "running" ]; then
      ok "$service is $state"
    else
      bad "$service is $state" "docker compose logs $service"
    fi
  done < <(docker compose ps -a --format '{{.Service}} {{.State}}')

  [ "$any" = "0" ] && note "nothing started yet: docker compose up -d --build"
}

check_blender() {
  head_ "Blender"
  local version
  version="$(docker compose exec -T api /opt/blender/blender --version 2>/dev/null | head -1)"
  if [ -n "$version" ]; then
    ok "$version"
  else
    bad "the API container has no working Blender" \
        "rebuild the image: docker compose up -d --build"
    note "without it every texture build fails and reports come out with no pictures"
  fi
}

check_monitoring() {
  head_ "Alerts"
  if [ -z "$(env_value SP_ALERT_WEBHOOK)" ]; then
    note "off: nobody hears about an outage until SP_ALERT_WEBHOOK is set in .env"
    return
  fi
  if systemctl is-active --quiet standardphysics-monitor.timer 2>/dev/null; then
    ok "monitor.sh runs every five minutes"
  else
    bad "SP_ALERT_WEBHOOK is set but the monitor timer is not running" \
        "systemctl enable --now standardphysics-monitor.timer"
  fi
}

show_recent_errors() {
  head_ "Last words from the API"
  local lines
  lines="$(docker compose logs --tail 15 --no-log-prefix api 2>/dev/null)"
  if [ -z "$lines" ]; then
    note "no logs yet"
  else
    printf '%s\n' "$lines" | sed 's/^/        /'
  fi
}

main() {
  check_env_file
  check_mount
  check_memory
  check_dns
  check_containers
  check_blender
  check_monitoring
  show_recent_errors

  head_ "Summary"
  printf '  %d fine, %d to fix\n\n' "$PASS" "$FAIL"
  [ "$FAIL" -eq 0 ]
}

main "$@"
