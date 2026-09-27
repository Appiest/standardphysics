# shellcheck shell=bash
# Shared by backup.sh and restore.sh: reading settings, and reaching the place
# the snapshots live, which is either a directory on this machine or a
# directory on another one over ssh. Sourced, never run.
#
# SP_BACKUP_DEST decides which. A path is local:
#
#   SP_BACKUP_DEST=/mnt/standardphysics-backups
#
# and host:path is another machine reached over ssh, which needs rsync there
# too and a key this box can use without a passphrase:
#
#   SP_BACKUP_DEST=backup@203.0.113.7:/srv/standardphysics
#
# An object store such as Spaces or S3 is not a destination. Snapshots share
# unchanged files with the one before through hard links, and an object store
# has none, so every night would upload every scan again.

# shellcheck disable=SC2034  # read by the scripts that source this
DATABASE_NAME=standardphysics.sqlite3
SNAPSHOT_NAME_PATTERN='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6}Z$'

die() {
  echo "$1" >&2
  exit 1
}

env_file_value() {
  [ -f .env ] || return 0
  grep -E "^$1=" .env | tail -1 | cut -d= -f2-
}

# The environment first, then .env beside these scripts, then the default.
setting() {
  local name="$1" default="${2:-}" value
  value="$(printenv "$name" || true)"
  [ -n "$value" ] || value="$(env_file_value "$name")"
  printf '%s' "${value:-$default}"
}

destination_is_remote() {
  [[ "$SP_BACKUP_DEST" =~ ^[^/]+: ]]
}

require_destination() {
  [ -n "$SP_BACKUP_DEST" ] || die "SP_BACKUP_DEST is not set, so there is nowhere to keep snapshots. Set it in .env."
  destination_is_remote || [[ "$SP_BACKUP_DEST" == /* ]] \
    || die "SP_BACKUP_DEST is '$SP_BACKUP_DEST'. Give a full path, or host:path for another machine."
}

destination_directory() {
  if destination_is_remote; then
    printf '%s' "${SP_BACKUP_DEST#*:}"
  else
    printf '%s' "$SP_BACKUP_DEST"
  fi
}

# Runs one command where the snapshots are. Each argument is quoted for the
# remote shell, so a path with a space in it arrives as one path.
at_destination() {
  if destination_is_remote; then
    # shellcheck disable=SC2029  # quoting here, on this side, is the point
    ssh "${SP_BACKUP_DEST%%:*}" "$(printf '%q ' "$@")"
  else
    "$@"
  fi
}

# Every finished snapshot, oldest first. The names are UTC timestamps, so
# sorting them as text sorts them by time, and a half-written one, still
# named .partial, never matches.
list_snapshots() {
  at_destination ls -1 "$(destination_directory)" | grep -E "$SNAPSHOT_NAME_PATTERN" | sort || true
}
