"""Server configuration, read once at startup.

Keys live in the repo-root `.env` and are loaded into this process only. They
never appear in a response, a log line or the web build.
"""

from __future__ import annotations

import logging
import os
import pathlib
import secrets
from dataclasses import dataclass

from standardphysics_agents.env_file import load_dotenv
from standardphysics_agents.tracing import ENTITY_ENV, PROJECT_ENV

from .store import ScanQuota

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
log = logging.getLogger(__name__)
DEFAULT_DATA_DIR = pathlib.Path(__file__).resolve().parents[1] / "var"


def _flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


def _secret(name: str, path_name: str) -> str | None:
    """A secret given inline, or the contents of the file another variable names.

    A file that can't be read is logged and treated as unset, so a wrong path
    turns off what the secret is for instead of stopping the whole server.
    """
    if os.environ.get(name):
        return os.environ[name].replace("\\n", "\n")
    path = os.environ.get(path_name)
    if not path:
        return None
    try:
        return pathlib.Path(path).read_text()
    except OSError as error:
        log.warning("%s=%s can't be read (%s), so it is ignored", path_name, path, error.strerror)
        return None


def _email_set(name: str) -> frozenset[str]:
    return frozenset(part.strip().casefold() for part in os.environ.get(name, "").split(",") if part.strip())


def _bounded_integer(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name)
    value = default if raw is None else int(raw)
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


@dataclass(frozen=True)
class Settings:
    data_dir: pathlib.Path = DEFAULT_DATA_DIR
    max_artifact_bytes: int = 1024 * 1024 * 1024
    max_scan_artifacts: int = ScanQuota.max_artifacts
    """How many artifacts one scan may hold, from SP_MAX_SCAN_ARTIFACTS. See `store.ScanQuota`."""
    max_scan_bytes: int = ScanQuota.max_bytes
    """How many bytes one scan's artifacts may add up to, from SP_MAX_SCAN_BYTES."""
    preview_unverified_rules: bool = False
    """Development only. Runs every rule as if a person had verified it, so the
    viewer has findings to draw before the rule pack is reviewed."""
    seed_sample_shop: bool = False
    """Off. The workspace shows scans that came off a phone, never a fixture.

    A synthetic shop in the list is indistinguishable from a real one at a
    glance, and a demo that shows invented findings about an invented room is
    worse than an empty list. Set SP_SEED_SAMPLE_SHOP=1 if you want it back.
    """
    seed_owner_email: str = "demo@standardphysics.app"
    """The account the sample shop belongs to, when SP_SEED_SAMPLE_SHOP is on."""
    seed_owner_password: str = ""
    """Set by SP_SEED_OWNER_PASSWORD, or generated at startup and logged.

    Generating it means the repository carries no password that works against
    every deployment of this server.
    """
    weave_project: str | None = None
    """Traces go to Weave when this is set, and nowhere when it is not. Only
    `from_environment` fills it in, so a server built in a test stays local."""
    weave_entity: str | None = None
    auto_deep_simulation: bool = False
    auto_deep_samples: int = 1000
    auto_deep_typesafe_call_limit: int = 3000
    auto_deep_astra_rounds: int = 4
    auto_deep_exhaustive_evaluations: int = 1_000_000
    bake_in_own_process: bool = False
    """Photo bakes run in a process of their own rather than on the API's thread.

    A bake is minutes of Python arithmetic, and on a worker thread it holds the
    interpreter lock the whole time, so every page waited behind it: requests
    that take twenty milliseconds took four seconds, and some never finished.
    The server turns this on; tests leave it off so their stand-in bakes run
    where they can see them. SP_BAKE_IN_PROCESS=1 turns it back off.
    """
    bake_timeout_seconds: float = 45 * 60
    """How long a bake in its own process may run before it is killed and its job failed.

    The longest real bake, a whole floor on the two-core droplet, takes about
    fifteen minutes, so three times that only ever stops a bake that has hung.
    SP_BAKE_TIMEOUT_SECONDS changes it.
    """
    team_emails: frozenset[str] = frozenset()
    """The team's emails before the team was a role, from SP_TEAM_EMAILS (comma separated).

    Read once per database: the saved accounts with these emails on the day the
    server first starts with the team role are granted it (`team.adopt_allowlist`).
    A sign-up with one of these emails after that is an owner, because sign-up
    never confirms an email. Grant anyone later with `python -m standardphysics_api.team`.
    """
    apple_audiences: frozenset[str] = frozenset({"com.standardphysics.capture"})
    """The app ids a Sign in with Apple token may be issued for, from SP_APPLE_AUDIENCES.
    The iPhone app's bundle id, plus a Services ID if the web ever signs in with Apple."""
    apns_key: str | None = None
    """The team's APNs .p8 key, from SP_APNS_KEY or the file at SP_APNS_KEY_PATH. No key, no pushes."""
    apns_key_id: str | None = None
    apns_team_id: str | None = None
    apns_topic: str = "com.standardphysics.capture"
    evidence_settle_seconds: float = 30.0
    """Quiet time before late evidence auto-queues exactly one semantic job.

    A phone uploads its evidence over minutes: 465 frames arrive one by one,
    the photo manifest last. Queueing per arriving frame would run hundreds of
    provider jobs on partial evidence. When a complete unprocessed bundle has
    not changed for this long (or an explicit /complete arrives), one semantic
    job is queued. Zero keeps the immediate per-artifact behavior for tests.
    """
    git_sha: str = "unknown"
    """The commit the image was built from, from SP_GIT_SHA, which the Dockerfile
    bakes in from the GIT_SHA build argument. /health/details reports it, so the
    commit to roll back from is on the server rather than in someone's memory."""

    @property
    def database_path(self) -> pathlib.Path:
        return self.data_dir / "standardphysics.sqlite3"

    @classmethod
    def from_environment(cls) -> Settings:
        load_dotenv(REPO_ROOT / ".env")
        return cls(
            data_dir=pathlib.Path(os.environ.get("SP_DATA_DIR", DEFAULT_DATA_DIR)),
            preview_unverified_rules=_flag("SP_PREVIEW_UNVERIFIED_RULES"),
            seed_sample_shop=_flag("SP_SEED_SAMPLE_SHOP"),
            seed_owner_email=os.environ.get("SP_SEED_OWNER_EMAIL", "demo@standardphysics.app"),
            seed_owner_password=os.environ.get("SP_SEED_OWNER_PASSWORD") or secrets.token_urlsafe(12),
            weave_project=os.environ.get(PROJECT_ENV) or None,
            weave_entity=os.environ.get(ENTITY_ENV) or None,
            auto_deep_simulation=_flag("SP_AUTO_DEEP_SIMULATION"),
            bake_in_own_process=not _flag("SP_BAKE_IN_PROCESS"),
            bake_timeout_seconds=_bounded_integer("SP_BAKE_TIMEOUT_SECONDS", 45 * 60, 60, 86_400),
            team_emails=_email_set("SP_TEAM_EMAILS"),
            apns_key=_secret("SP_APNS_KEY", "SP_APNS_KEY_PATH"),
            apns_key_id=os.environ.get("SP_APNS_KEY_ID") or None,
            apns_team_id=os.environ.get("SP_APNS_TEAM_ID") or None,
            apns_topic=os.environ.get("SP_APNS_TOPIC") or "com.standardphysics.capture",
            git_sha=os.environ.get("SP_GIT_SHA") or "unknown",
            apple_audiences=_email_set("SP_APPLE_AUDIENCES") or frozenset({"com.standardphysics.capture"}),
            max_scan_artifacts=_bounded_integer("SP_MAX_SCAN_ARTIFACTS", ScanQuota.max_artifacts, 1, 1_000_000),
            max_scan_bytes=_bounded_integer("SP_MAX_SCAN_BYTES", ScanQuota.max_bytes, 1, 2**50),
            evidence_settle_seconds=_bounded_integer(
                "SP_EVIDENCE_SETTLE_SECONDS", 30, 0, 86_400
            ),
            auto_deep_samples=_bounded_integer(
                "SP_AUTO_DEEP_SAMPLES", 1000, 1, 10_000
            ),
            auto_deep_typesafe_call_limit=_bounded_integer(
                "SP_AUTO_DEEP_TYPESAFE_CALL_LIMIT", 3000, 1, 50_000
            ),
            auto_deep_astra_rounds=_bounded_integer(
                "SP_AUTO_DEEP_ASTRA_ROUNDS", 4, 1, 8
            ),
            auto_deep_exhaustive_evaluations=_bounded_integer(
                "SP_AUTO_DEEP_EVALUATIONS", 1_000_000, 40, 5_000_000
            ),
        )
