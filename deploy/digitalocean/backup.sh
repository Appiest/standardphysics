#!/usr/bin/env bash
# Backs up the scan volume: the database and every file beside it, as one
# dated snapshot in SP_BACKUP_DEST. backup-lib.sh says what a destination can
# be. setup.sh installs a timer that runs this nightly once SP_BACKUP_DEST is
# set in .env.
#
#   ./backup.sh
#
# The database is copied with SQLite's online backup, never with cp. The API
# keeps it in WAL mode, so the newest writes can sit in a separate -wal file
# until a checkpoint moves them over, and a copy of the main file alone can be
# missing them or catch a page halfway written. The backup API reads one
# consistent state of the whole database while the API goes on writing.
#
# It runs inside the API container, as the user that owns the volume. Opening
# the database from the host as root would leave root-owned -wal and -shm
# files behind whenever the API was not holding them, and the API could then
# no longer open its own database. SP_BACKUP_SNAPSHOT_WITH=host copies from
# this machine instead, for when the stack is down; run it as uid 10001 then.
#
# The database is copied before the scans, so every file the copy lists is
# already on disk when the scans are read. A scan uploaded in between lands in
# the snapshot without a row, which a restore reports and nothing else minds.
#
# Each snapshot is a full directory tree, but a file that has not changed
# since the last snapshot is a hard link to it (rsync --link-dest), so a night
# costs the space of what changed. SP_BACKUP_KEEP snapshots are kept, 14 by
# default, and older ones are deleted after a new one finishes.
set -euo pipefail

cd "$(dirname "$0")"
# shellcheck source=backup-lib.sh
. ./backup-lib.sh

STAGING=.backup-staging
VOLUME="$(setting SCANS_PATH)"
SP_BACKUP_DEST="$(setting SP_BACKUP_DEST)"
KEEP="$(setting SP_BACKUP_KEEP 14)"
SNAPSHOT_WITH="$(setting SP_BACKUP_SNAPSHOT_WITH container)"
PYTHON="$(setting SP_BACKUP_PYTHON python3)"

SNAPSHOT_SCRIPT='
import pathlib
import sqlite3
import sys

source, copy_path = sys.argv[1], pathlib.Path(sys.argv[2])
copy_path.parent.mkdir(parents=True, exist_ok=True)
copy_path.unlink(missing_ok=True)
live = sqlite3.connect(source, timeout=60)
copy = sqlite3.connect(copy_path)
live.backup(copy)
copy.execute("PRAGMA journal_mode=DELETE")
copy.close()
live.close()
'

require_settings() {
  require_destination
  [ -n "$VOLUME" ] || die "SCANS_PATH is not set. Set it in .env."
  [ -f "$VOLUME/$DATABASE_NAME" ] || die "No database at $VOLUME/$DATABASE_NAME."
  [[ "$KEEP" =~ ^[1-9][0-9]*$ ]] || die "SP_BACKUP_KEEP must be a whole number of snapshots, at least 1."
  command -v rsync >/dev/null || die "rsync is not installed: apt-get install rsync"
}

snapshot_database_in_container() {
  docker compose exec -T api /opt/venv/bin/python -c "$SNAPSHOT_SCRIPT" \
    "/data/$DATABASE_NAME" "/data/$STAGING/$DATABASE_NAME"
}

snapshot_database_on_host() {
  "$PYTHON" -c "$SNAPSHOT_SCRIPT" "$VOLUME/$DATABASE_NAME" "$VOLUME/$STAGING/$DATABASE_NAME"
}

snapshot_database() {
  case "$SNAPSHOT_WITH" in
    container) snapshot_database_in_container ;;
    host) snapshot_database_on_host ;;
    *) die "SP_BACKUP_SNAPSHOT_WITH is '$SNAPSHOT_WITH'; it can be container or host." ;;
  esac
}

remove_staging() {
  rm -rf "${VOLUME:?}/$STAGING"
}

remove_unfinished_snapshots() {
  at_destination find "$(destination_directory)" -maxdepth 1 -name '*.partial' -exec rm -rf {} +
}

# --link-dest is relative to the directory being written, so ../<previous>
# means the same thing on this machine and on a remote one.
copy_volume() {
  local target="$1" previous="$2" link_to_previous=()
  [ -z "$previous" ] || link_to_previous=(--link-dest="../$previous")
  rsync -a ${link_to_previous[@]+"${link_to_previous[@]}"} \
    --exclude="/$DATABASE_NAME" --exclude="/$DATABASE_NAME-*" \
    --exclude="/$STAGING" --exclude=/lost+found \
    "$VOLUME/" "$SP_BACKUP_DEST/$target/"
  rsync -a "$VOLUME/$STAGING/$DATABASE_NAME" "$SP_BACKUP_DEST/$target/$DATABASE_NAME"
}

prune_old_snapshots() {
  local snapshots count stale
  snapshots="$(list_snapshots)"
  count="$(printf '%s\n' "$snapshots" | grep -c . || true)"
  [ "$count" -gt "$KEEP" ] || return 0
  printf '%s\n' "$snapshots" | head -n "$((count - KEEP))" | while read -r stale; do
    at_destination rm -rf "$(destination_directory)/$stale"
    echo "Deleted the snapshot from $stale"
  done
}

main() {
  require_settings
  local name previous
  name="$(date -u +%Y-%m-%dT%H%M%SZ)"
  at_destination mkdir -p "$(destination_directory)"
  remove_unfinished_snapshots
  previous="$(list_snapshots | tail -1)"
  [ "$previous" != "$name" ] || die "A snapshot named $name already exists. Wait a second and run it again."

  trap remove_staging EXIT
  snapshot_database
  copy_volume "$name.partial" "$previous"
  at_destination mv "$(destination_directory)/$name.partial" "$(destination_directory)/$name"
  echo "Backed up $VOLUME to $SP_BACKUP_DEST/$name"
  prune_old_snapshots
}

main "$@"
