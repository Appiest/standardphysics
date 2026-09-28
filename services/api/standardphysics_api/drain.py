"""Whether the API is draining for a deploy, and how someone with a shell on the server turns it on and off:

    python -m standardphysics_api.drain on
    python -m standardphysics_api.drain off
    python -m standardphysics_api.drain status

scripts/deploy.sh turns it on before it reads the job queue and off once the
new container is serving, or as soon as the deploy fails. While it is on, a
request that would queue new work is refused with 503 and a time to retry (see
`budgets.admit_new_job`), and the worker claims no queued job but finishes the
one it is running. Artifact uploads carry on: their scan was admitted already,
and its job is queued only when the phone finishes it, which is refused.

The flag is a file named `draining` in the data directory, beside the
database, so the old container, the new one and this command all see the same
one and it outlives the restart. The API never clears it by itself: a flag
left behind by a deploy that died half way shows as `draining: true` in
/health/details until someone runs `off`.
"""

from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

from .errors import ApiProblem
from .settings import Settings

FLAG_NAME = "draining"
DRAIN_RETRY_SECONDS = 60
UPDATING = "Standard Physics is updating; try again in a minute."


def flag_path(data_dir: pathlib.Path) -> pathlib.Path:
    return data_dir / FLAG_NAME


def is_draining(data_dir: pathlib.Path) -> bool:
    return flag_path(data_dir).exists()


def turn_on(data_dir: pathlib.Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    flag_path(data_dir).touch()


def turn_off(data_dir: pathlib.Path) -> None:
    flag_path(data_dir).unlink(missing_ok=True)


def refuse_new_work(connection: sqlite3.Connection) -> None:
    """Refuse the job being asked for while the data directory holding this connection's database is draining.

    Admission sees only the connection, and the database always lives in the data directory, so the
    directory is found from the file the connection has open. An in-memory database has no directory
    and never drains.
    """
    data_dir = _database_directory(connection)
    if data_dir is not None and is_draining(data_dir):
        raise ApiProblem(503, UPDATING, headers={"Retry-After": str(DRAIN_RETRY_SECONDS)})


def _database_directory(connection: sqlite3.Connection) -> pathlib.Path | None:
    main_file = next((row[2] for row in connection.execute("PRAGMA database_list") if row[1] == "main"), "")
    return pathlib.Path(main_file).parent if main_file else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Turn the deploy drain on or off, or say whether it is on.")
    parser.add_argument("command", choices=("on", "off", "status"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data_dir = Settings.from_environment().data_dir
    if args.command == "on":
        turn_on(data_dir)
    elif args.command == "off":
        turn_off(data_dir)
    state = "draining: new work is refused and no queued job is started" if is_draining(data_dir) else "not draining"
    print(f"{flag_path(data_dir)}: {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
