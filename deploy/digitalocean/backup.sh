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
# The database is copied before the scans. An upload writes its file before
# it commits its row, so every artifact the database copy lists already has
# its file on disk when the scans are read; copying the scans first would
# miss the file of any upload that landed in between. A scan uploaded after
# the database copy lands in the snapshot without a row, which is harmless.
#
# A deletion goes the other way: the API commits the rows gone first and
# removes the files after, so a scan deleted after the database copy can be
# listed in the snapshot with its files already gone. After the copy, every
# listed artifact without a file is looked up in the live database. If its
# row is gone there too, it was deleted during the backup, and its name goes
# into artifacts-deleted-during-backup.txt in the snapshot. restore.sh reads
# it and deletes those scans from the restored copy, finishing the delete. If its row is still there, the file
# really is missing: the snapshot is kept, older ones are not pruned, since
# they may hold the only copy, and the backup exits 2 so the timer fails.
#
# Each snapshot is a full directory tree, but a file that has not changed
# since the last snapshot is a hard link to it (rsync --link-dest), so a night
# costs the space of what changed. SP_BACKUP_KEEP snapshots are kept, 14 by
# default, and older ones are deleted after a new one finishes.
#
# One backup runs at a time. A second, from the timer catching up while one
# started by hand is still going, exits 75 instead of copying alongside it.
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
LOCK="$(setting SP_BACKUP_LOCK /var/lock/standardphysics-backup)"

# Reads, on stdin, the path of every artifact file the new snapshot holds.
# Prints each listed artifact whose file is missing and whose row is still in
# the live database, and writes the ones whose row has gone to the manifest.
RECONCILE_SCRIPT='
import pathlib
import sqlite3
import sys

snapshot, live, manifest = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
copied = set()
for line in sys.stdin:
    parts = line.strip().split("/")
    if len(parts) >= 4:
        copied.add((parts[-3], parts[-1]))
listed = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True).execute("SELECT scan_id, id FROM artifacts")
absent = [(scan, artifact) for scan, artifact in listed if (scan, artifact) not in copied]
live_database = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
query = "SELECT 1 FROM artifacts WHERE scan_id = ? AND id = ?"
still_listed = {row for row in absent if live_database.execute(query, row).fetchone()}
deleted = [f"{scan}/{artifact}" for scan, artifact in absent if (scan, artifact) not in still_listed]
manifest.unlink(missing_ok=True)
if deleted:
    manifest.write_text("\n".join(deleted) + "\n")
for scan, artifact in sorted(still_listed):
    print(f"{scan}/{artifact}")
'

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
  [[ "$SNAPSHOT_WITH" =~ ^(container|host)$ ]] \
    || die "SP_BACKUP_SNAPSHOT_WITH is '$SNAPSHOT_WITH'; it can be container or host."
  command -v rsync >/dev/null || die "rsync is not installed: apt-get install rsync"
  command -v flock >/dev/null || die "flock is not installed: apt-get install util-linux"
}

hold_the_backup_lock() {
  exec 9>"$LOCK"
  if ! flock -n 9; then
    echo "Another backup is already running (it holds $LOCK), so this one stops here." >&2
    exit 75
  fi
}

# Where the Python that reads the database finds the volume.
volume_seen_by_python() {
  if [ "$SNAPSHOT_WITH" = container ]; then
    printf /data
  else
    printf '%s' "$VOLUME"
  fi
}

run_python_beside_the_database() {
  local script="$1"
  shift
  if [ "$SNAPSHOT_WITH" = container ]; then
    docker compose exec -T api /opt/venv/bin/python -c "$script" "$@"
  else
    "$PYTHON" -c "$script" "$@"
  fi
}

snapshot_database() {
  local seen
  seen="$(volume_seen_by_python)"
  run_python_beside_the_database "$SNAPSHOT_SCRIPT" "$seen/$DATABASE_NAME" "$seen/$STAGING/$DATABASE_NAME"
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

# A snapshot of a volume with no scans yet has no scans directory to list.
copied_artifact_files() {
  at_destination find "$(destination_directory)/$1/scans" -path '*/artifacts/*' -type f 2>/dev/null || true
}

# Prints the artifacts that are really missing, and leaves the ones deleted
# during the backup in the staging manifest.
reconcile_with_live_database() {
  local target="$1" seen
  seen="$(volume_seen_by_python)"
  copied_artifact_files "$target" | run_python_beside_the_database "$RECONCILE_SCRIPT" \
    "$seen/$STAGING/$DATABASE_NAME" "$seen/$DATABASE_NAME" "$seen/$STAGING/$DELETED_DURING_BACKUP"
}

copy_deletion_manifest() {
  local manifest="$VOLUME/$STAGING/$DELETED_DURING_BACKUP"
  [ -f "$manifest" ] || return 0
  rsync -a "$manifest" "$SP_BACKUP_DEST/$1/$DELETED_DURING_BACKUP"
  echo "$(grep -c . "$manifest") artifact(s) were deleted while the backup ran; restore.sh removes their scans from a restored copy."
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

prune_unless_files_were_lost() {
  local lost="$1"
  if [ -z "$lost" ]; then
    prune_old_snapshots
    return 0
  fi
  echo "The live database lists these artifacts, but their files are missing from the volume:" >&2
  printf '%s\n' "$lost" | sed 's/^/  /' >&2
  echo "No older snapshot was deleted, since one of them may hold the only copy." >&2
  exit 2
}

main() {
  require_settings
  hold_the_backup_lock
  local name previous lost
  name="$(date -u +%Y-%m-%dT%H%M%SZ)"
  at_destination mkdir -p "$(destination_directory)"
  remove_unfinished_snapshots
  previous="$(list_snapshots | tail -1)"
  [ "$previous" != "$name" ] || die "A snapshot named $name already exists. Wait a second and run it again."

  trap remove_staging EXIT
  snapshot_database
  copy_volume "$name.partial" "$previous"
  lost="$(reconcile_with_live_database "$name.partial")"
  copy_deletion_manifest "$name.partial"
  at_destination mv "$(destination_directory)/$name.partial" "$(destination_directory)/$name"
  echo "Backed up $VOLUME to $SP_BACKUP_DEST/$name"
  prune_unless_files_were_lost "$lost"
}

main "$@"
