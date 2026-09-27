#!/usr/bin/env bash
# Copies one snapshot that backup.sh made into a new directory, then checks
# the copy: SQLite's integrity check on the database, how many scans and
# artifacts it lists, and whether every artifact it lists has its file.
#
#   ./restore.sh                               # list the snapshots
#   ./restore.sh latest /root/restored
#   ./restore.sh 2026-09-27T103000Z /root/restored
#
# It never writes over the live volume, and it refuses a directory that
# already holds anything. Putting a checked copy back under the API is a
# separate step, written out in docs/DEPLOY.md, so a restore can be tried and
# inspected at any time without touching what is running.
#
# Exits 1 when the copy fails or the database is damaged, and 2 when the
# database is sound but some artifacts it lists have no file.
set -euo pipefail

CALLER_DIRECTORY="$PWD"
cd "$(dirname "$0")"
# shellcheck source=backup-lib.sh
. ./backup-lib.sh

SP_BACKUP_DEST="$(setting SP_BACKUP_DEST)"
PYTHON="$(setting SP_BACKUP_PYTHON python3)"

CHECK_SCRIPT='
import pathlib
import sqlite3
import sys

root = pathlib.Path(sys.argv[1])
database = sqlite3.connect(f"file:{root / sys.argv[2]}?mode=ro", uri=True)
integrity = database.execute("PRAGMA integrity_check").fetchone()[0]
scans = database.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
listed = database.execute("SELECT scan_id, id FROM artifacts").fetchall()
missing = [f"{scan}/{artifact}" for scan, artifact in listed
           if not (root / "scans" / scan / "artifacts" / artifact).is_file()]
files = sum(1 for path in root.glob("scans/*/artifacts/*") if path.is_file())
print(f"integrity check: {integrity}")
print(f"scans:           {scans}")
print(f"artifacts:       {len(listed)} listed, {files} files")
for name in missing:
    print(f"missing file:    {name}")
if integrity != "ok":
    sys.exit(1)
sys.exit(2 if missing else 0)
'

usage() {
  echo "Usage: $0 <snapshot|latest> <new directory>" >&2
  echo "Snapshots in $SP_BACKUP_DEST:" >&2
  list_snapshots | sed 's/^/  /' >&2
  exit 1
}

chosen_snapshot() {
  local wanted="$1" snapshots
  snapshots="$(list_snapshots)"
  [ -n "$snapshots" ] || die "There are no snapshots in $SP_BACKUP_DEST."
  if [ "$wanted" = latest ]; then
    printf '%s\n' "$snapshots" | tail -1
    return
  fi
  printf '%s\n' "$snapshots" | grep -qxF "$wanted" || die "There is no snapshot named $wanted in $SP_BACKUP_DEST."
  printf '%s' "$wanted"
}

from_caller() {
  case "$1" in
    /*) printf '%s' "$1" ;;
    *) printf '%s' "$CALLER_DIRECTORY/$1" ;;
  esac
}

require_empty_directory() {
  [ ! -e "$1" ] || [ -z "$(ls -A "$1")" ] \
    || die "$1 already holds files. Restore into a new directory, so nothing is written over."
}

main() {
  require_destination
  [ "$#" -eq 2 ] || usage
  local snapshot target
  target="$(from_caller "$2")"
  snapshot="$(chosen_snapshot "$1")"
  require_empty_directory "$target"
  mkdir -p "$target"
  rsync -a "$SP_BACKUP_DEST/$snapshot/" "$target/"
  [ -f "$target/$DATABASE_NAME" ] || die "The snapshot $snapshot has no $DATABASE_NAME."
  echo "Restored $snapshot into $target"
  "$PYTHON" -c "$CHECK_SCRIPT" "$target" "$DATABASE_NAME"
}

main "$@"
