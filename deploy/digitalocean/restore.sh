#!/usr/bin/env bash
# Copies one snapshot that backup.sh made into a new directory, then checks
# the copy: SQLite's integrity check on the database, how many scans and
# artifacts it lists, and whether every artifact it lists has its file with
# the sha256 the database recorded when it was uploaded.
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
# database is sound but some artifact it lists has no file, or has a file
# whose bytes no longer hash to the recorded sha256. The second is corruption,
# in the snapshot or on the disk under it.
#
# A scan deleted while the backup ran is still listed in the snapshot's
# database with its files gone, and backup.sh names its artifacts in
# artifacts-deleted-during-backup.txt. The restored copy is made to agree
# with the delete: the scan's rows go from every table with a scan_id column,
# which is the set the API's own delete clears, and so does whatever of its
# files were copied. The manifest is removed once it has been applied, so the
# copy holds what the live volume held once that delete had finished.
set -euo pipefail

CALLER_DIRECTORY="$PWD"
cd "$(dirname "$0")"
# shellcheck source=backup-lib.sh
. ./backup-lib.sh

SP_BACKUP_DEST="$(setting SP_BACKUP_DEST)"
PYTHON="$(setting SP_BACKUP_PYTHON python3)"

CHECK_SCRIPT='
import hashlib
import pathlib
import shutil
import sqlite3
import sys

root = pathlib.Path(sys.argv[1])
deletion_manifest = root / sys.argv[3]
database = sqlite3.connect(root / sys.argv[2])


def tables_naming_a_scan():
    names = [name for (name,) in database.execute("SELECT name FROM sqlite_master WHERE type = \"table\"")]
    return [name for name in names
            if any(column[1] == "scan_id" for column in database.execute(f"PRAGMA table_info(\"{name}\")"))]


def scans_deleted_during_backup():
    if not deletion_manifest.is_file():
        return []
    return sorted({line.split("/")[0] for line in deletion_manifest.read_text().split()})


def remove_scans_deleted_during_backup():
    children = tables_naming_a_scan()
    for scan in scans_deleted_during_backup():
        for table in children:
            database.execute(f"DELETE FROM \"{table}\" WHERE scan_id = ?", (scan,))
        database.execute("DELETE FROM scans WHERE id = ?", (scan,))
        shutil.rmtree(root / "scans" / scan, ignore_errors=True)
        print(f"removed a scan its owner deleted while the backup ran: {scan}")
    database.commit()
    deletion_manifest.unlink(missing_ok=True)


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact_problem(scan, artifact, recorded_sha256):
    path = root / "scans" / scan / "artifacts" / artifact
    if not path.is_file():
        return "missing file:"
    if file_sha256(path) != recorded_sha256:
        return "corrupt file:"
    return None


integrity = database.execute("PRAGMA integrity_check").fetchone()[0]
print(f"integrity check: {integrity}")
if integrity != "ok":
    sys.exit(1)
remove_scans_deleted_during_backup()
scans = database.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
listed = database.execute("SELECT scan_id, id, sha256 FROM artifacts ORDER BY scan_id, id").fetchall()
files = sum(1 for path in root.glob("scans/*/artifacts/*") if path.is_file())
print(f"scans:           {scans}")
print(f"artifacts:       {len(listed)} listed, {files} files")
problems = [(artifact_problem(*row), f"{row[0]}/{row[1]}") for row in listed]
problems = [(problem, name) for problem, name in problems if problem]
for problem, name in problems:
    print(f"{problem:<16} {name}")
sys.exit(2 if problems else 0)
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
  "$PYTHON" -c "$CHECK_SCRIPT" "$target" "$DATABASE_NAME" "$DELETED_DURING_BACKUP"
}

main "$@"
