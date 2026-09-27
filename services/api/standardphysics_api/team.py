"""Who is on the Standard Physics team, and how someone joins or leaves it.

Being on the team is a flag on the account's row, and only someone with a shell
on the server can set it:

    python -m standardphysics_api.team grant someone@standardphysics.app
    python -m standardphysics_api.team revoke someone@standardphysics.app
    python -m standardphysics_api.team list

It used to follow from the email alone: an account whose address was on
SP_TEAM_EMAILS was the team. Sign-up never confirms an email, so whoever first
registered an unused address from that list got the builders' tools. The list
is now read once per database, by `adopt_allowlist`, to carry the accounts the
team had already made over to the flag. An account made after that step is an
owner until someone grants it.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys

from . import accounts
from .db import Database, first_time
from .settings import Settings

log = logging.getLogger(__name__)

ALLOWLIST_STEP = "team_role_from_allowlist"


def grant(connection: sqlite3.Connection, email: str) -> bool:
    """Put the account with this email on the team. False when no account has it."""
    return _set_team(connection, email, True)


def revoke(connection: sqlite3.Connection, email: str) -> bool:
    """Take the account with this email off the team. False when no account has it."""
    return _set_team(connection, email, False)


def _set_team(connection: sqlite3.Connection, email: str, on_team: bool) -> bool:
    cursor = connection.execute(
        "UPDATE owners SET team = ? WHERE email = ? AND guest = 0", (int(on_team), accounts.normalize_email(email))
    )
    return cursor.rowcount == 1


def members(connection: sqlite3.Connection) -> list[str]:
    return [row["email"] for row in connection.execute("SELECT email FROM owners WHERE team = 1 ORDER BY email")]


def adopt_allowlist(database: Database, allowlist: frozenset[str]) -> int:
    """Grant the team role to the saved accounts on the allowlist today, once per database.

    Returns how many accounts it granted. Every later start finds the step
    recorded and grants nobody, so a sign-up with a listed email stays an owner.
    """
    with database.transaction() as connection:
        if not first_time(connection, ALLOWLIST_STEP):
            return 0
        granted = [email for email in sorted(allowlist) if grant(connection, email)]
    if granted:
        log.info("granted the team role to %d existing account(s) on SP_TEAM_EMAILS", len(granted))
    return len(granted)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Grant, revoke or list the team role.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("grant", "revoke"):
        commands.add_parser(name).add_argument("email")
    commands.add_parser("list")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database = Database(Settings.from_environment().database_path)
    with database.transaction() as connection:
        if args.command == "list":
            print("\n".join(members(connection)) or "Nobody is on the team.")
            return 0
        changed = grant(connection, args.email) if args.command == "grant" else revoke(connection, args.email)
    if not changed:
        print(f"No saved account has the email {args.email}.", file=sys.stderr)
        return 1
    print(f"{args.email} is {'now' if args.command == 'grant' else 'no longer'} on the team.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
