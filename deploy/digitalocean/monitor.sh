#!/usr/bin/env bash
# Checks that the deployment is doing its job and tells someone when it stops,
# and again when it recovers. setup.sh installs a systemd timer that runs this
# every 5 minutes once SP_ALERT_WEBHOOK is set in .env.
#
#   ./monitor.sh
#
# Four checks, each against a threshold in .env:
#
#   readiness  /health/ready answers 200. It fails while a worker loop has
#              died, stalled, or run a job past its deadline, which /health,
#              the one the container healthcheck asks, deliberately does not.
#   queue      oldest_queued_job_seconds from /health/details is at most
#              SP_MONITOR_QUEUE_SECONDS, 1800 by default. A whole-floor bake
#              runs about 15 minutes, so twice that means the worker is stuck
#              or far behind.
#   disk       the scans volume has at least SP_MONITOR_MIN_FREE_PERCENT free,
#              15 by default. SQLite fails every write once it is full.
#   backup     the newest snapshot in SP_BACKUP_DEST is at most
#              SP_MONITOR_BACKUP_HOURS old, 26 by default, which is one nightly
#              run plus its randomised delay and some slack. Skipped when
#              SP_BACKUP_DEST is empty.
#
# SP_MONITOR_URL is where the API is asked, https://$API_DOMAIN by default, so
# the request goes through Caddy and its certificate the way the phone's does.
#
# It sends one message when the set of failing checks changes, naming what
# started failing and what recovered, and nothing while an outage stays the
# same. The failing set is kept in SP_MONITOR_STATE between runs, and is only
# updated once a message has been delivered, so a webhook that was down
# hears about the change on the next run instead of never.
#
# SP_ALERT_WEBHOOK takes either kind of endpoint:
#
#   an ntfy topic   https://ntfy.sh/<topic>, sent as plain text with a Title
#                   header, so the ntfy app on a phone shows it as is.
#                   SP_ALERT_FORMAT=ntfy does the same for a self-hosted ntfy.
#   anything else   a JSON POST of {"text": "..."}, the shape a Slack incoming
#                   webhook takes and most chat tools accept.
#
# It runs on the box it watches, so it cannot report the box itself being
# down. docs/DEPLOY.md pairs it with an outside uptime check for that.
set -uo pipefail

cd "$(dirname "$0")" || exit 1
# shellcheck source=backup-lib.sh
. ./backup-lib.sh

WEBHOOK="$(setting SP_ALERT_WEBHOOK)"
ALERT_FORMAT="$(setting SP_ALERT_FORMAT)"
API_URL="$(setting SP_MONITOR_URL "https://$(setting API_DOMAIN)")"
SCANS="$(setting SCANS_PATH)"
SP_BACKUP_DEST="$(setting SP_BACKUP_DEST)"
QUEUE_LIMIT_SECONDS="$(setting SP_MONITOR_QUEUE_SECONDS 1800)"
MIN_FREE_PERCENT="$(setting SP_MONITOR_MIN_FREE_PERCENT 15)"
BACKUP_LIMIT_HOURS="$(setting SP_MONITOR_BACKUP_HOURS 26)"
STATE="$(setting SP_MONITOR_STATE /var/lib/standardphysics-monitor/failing)"
PYTHON="$(setting SP_MONITOR_PYTHON python3)"
TAB=$'\t'

OLDEST_QUEUED_SCRIPT='
import json
import sys

print(int(json.load(sys.stdin).get("oldest_queued_job_seconds") or 0))
'

SNAPSHOT_AGE_HOURS_SCRIPT='
import datetime
import sys

taken = datetime.datetime.strptime(sys.argv[1], "%Y-%m-%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
print(int((datetime.datetime.now(datetime.timezone.utc) - taken).total_seconds() // 3600))
'

JSON_BODY_SCRIPT='
import json
import sys

print(json.dumps({"text": sys.stdin.read()}))
'

# Each check prints why it failed and returns 1, or prints nothing.
check_readiness() {
  local status
  status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$API_URL/health/ready" 2>/dev/null)"
  [ "$status" = 200 ] && return 0
  if [ "$status" = 000 ] || [ -z "$status" ]; then
    echo "$API_URL/health/ready could not be reached"
  else
    echo "$API_URL/health/ready answered $status"
  fi
  return 1
}

check_queue() {
  local details oldest
  details="$(curl -fsS --max-time 15 "$API_URL/health/details" 2>/dev/null)" \
    || { echo "$API_URL/health/details could not be read"; return 1; }
  oldest="$(printf '%s' "$details" | "$PYTHON" -c "$OLDEST_QUEUED_SCRIPT" 2>/dev/null)" \
    || { echo "$API_URL/health/details did not say how old the queue is"; return 1; }
  [ "$oldest" -le "$QUEUE_LIMIT_SECONDS" ] && return 0
  echo "the oldest queued job has waited ${oldest}s, over the ${QUEUE_LIMIT_SECONDS}s limit"
  return 1
}

check_disk() {
  [ -n "$SCANS" ] || { echo "SCANS_PATH is not set, so the scans volume cannot be measured"; return 1; }
  local size available free_percent
  read -r size available < <(df -Pk "$SCANS" 2>/dev/null | awk 'NR == 2 {print $2, $4}')
  [[ "${size:-}" =~ ^[1-9][0-9]*$ ]] || { echo "df could not measure $SCANS"; return 1; }
  free_percent=$((available * 100 / size))
  [ "$free_percent" -ge "$MIN_FREE_PERCENT" ] && return 0
  echo "$SCANS has ${free_percent}% free ($((available / 1048576)) GB), under the ${MIN_FREE_PERCENT}% limit"
  return 1
}

check_backup() {
  [ -n "$SP_BACKUP_DEST" ] || return 0
  local newest age_hours
  newest="$(list_snapshots | tail -1)"
  [ -n "$newest" ] || { echo "$SP_BACKUP_DEST holds no finished snapshot"; return 1; }
  age_hours="$("$PYTHON" -c "$SNAPSHOT_AGE_HOURS_SCRIPT" "$newest")" \
    || { echo "the snapshot name $newest is not a time"; return 1; }
  [ "$age_hours" -le "$BACKUP_LIMIT_HOURS" ] && return 0
  echo "the newest snapshot, $newest, is ${age_hours} hours old, over the ${BACKUP_LIMIT_HOURS}-hour limit"
  return 1
}

# One line per failing check: its name, a tab, and why.
failing_checks() {
  local name reason
  for name in readiness queue disk backup; do
    reason="$("check_$name")" || printf '%s\t%s\n' "$name" "$reason"
  done
}

# The lines of the second list whose check is not in the first.
checks_only_in() {
  awk -F "$TAB" 'NR == FNR { seen[$1] = 1; next } NF && !($1 in seen)' <(printf '%s\n' "$1") <(printf '%s\n' "$2")
}

describe_changes() {
  local previous="$1" current="$2" started cleared
  started="$(checks_only_in "$previous" "$current")"
  cleared="$(checks_only_in "$current" "$previous")"
  [ -n "$started" ] || [ -n "$cleared" ] || return 0
  echo "Standard Physics at $API_URL"
  [ -z "$started" ] || printf '%s\n' "$started" | awk -F "$TAB" '{ print "Failing: " $1 ": " $2 }'
  [ -z "$cleared" ] || printf '%s\n' "$cleared" | awk -F "$TAB" '{ print "Recovered: " $1 " (it was: " $2 ")" }'
  [ -n "$current" ] || echo "Every check passes now."
}

sends_to_ntfy() {
  [ "$ALERT_FORMAT" = ntfy ] || { [ -z "$ALERT_FORMAT" ] && [[ "$WEBHOOK" == https://ntfy.sh/* ]]; }
}

notify() {
  local message="$1" priority=default
  [[ "$message" == *"Failing: "* ]] && priority=high
  if sends_to_ntfy; then
    curl -fsS --max-time 15 -H "Title: Standard Physics monitor" -H "Priority: $priority" \
      --data-binary "$message" "$WEBHOOK" >/dev/null
    return
  fi
  printf '%s' "$message" | "$PYTHON" -c "$JSON_BODY_SCRIPT" \
    | curl -fsS --max-time 15 -H 'Content-Type: application/json' --data-binary @- "$WEBHOOK" >/dev/null
}

remember() {
  mkdir -p "$(dirname "$STATE")"
  printf '%s\n' "$1" > "$STATE.new" && mv "$STATE.new" "$STATE"
}

main() {
  if [ -z "$WEBHOOK" ]; then
    echo "SP_ALERT_WEBHOOK is not set, so there is nobody to tell. Monitoring is off."
    exit 0
  fi
  local previous current message
  previous="$(cat "$STATE" 2>/dev/null || true)"
  current="$(failing_checks)"
  message="$(describe_changes "$previous" "$current")"
  printf '%s\n' "${current:-every check passes}"
  if [ -n "$message" ] && ! notify "$message"; then
    echo "Could not deliver the alert to SP_ALERT_WEBHOOK; the next run tries again." >&2
    exit 1
  fi
  remember "$current"
}

main "$@"
