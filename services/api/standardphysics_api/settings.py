"""Server configuration, read once at startup.

Keys live in the repo-root `.env` and are loaded into this process only. They
never appear in a response, a log line or the web build.
"""

from __future__ import annotations

import logging
import math
import os
import pathlib
import secrets
from dataclasses import dataclass, field

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
    max_owner_scans: int = 25
    """How many scans one account may hold, from SP_MAX_OWNER_SCANS. The team is exempt (see `budgets`)."""
    max_owner_bytes: int = 6 * 1024 * 1024 * 1024
    """How many uploaded bytes one account may hold across its scans, from SP_MAX_OWNER_BYTES.

    The largest walk on file, the Moffitt library's full floor, is 2.5 GB, so an
    owner has room for two walks that size. A shop is far smaller than a library
    floor. The team, whose account holds about ten Moffitt-scale walks, is exempt.
    """
    max_queued_jobs: int = 200
    """How many jobs may wait in the queue before finalizing a scan is refused with a 503, from SP_MAX_QUEUED_JOBS.
    A finalized walk queues a handful of jobs, so this is dozens of walks waiting at once."""
    min_free_disk_bytes: int = 1024 * 1024 * 1024
    """The free space kept on the data volume, from SP_MIN_FREE_DISK_BYTES. Below it, new scans and
    uploads are refused with a 507. It is at least one artifact at the largest size allowed, so an
    upload admitted just above the floor can't run the 10 GB volume out of space by itself."""
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
    """Set by SP_SEED_OWNER_PASSWORD, or generated at startup and written to a file.
    It is used, and the file written, only when the demo account does not exist
    yet; an existing account keeps the password it was created with.

    Generating it means the repository carries no password that works against
    every deployment of this server. It never goes to the log, because the log
    reaches everyone who deploys and anything that collects it.
    """
    seed_owner_password_generated: bool = False
    """True when SP_SEED_OWNER_PASSWORD was unset and the password was made up at startup."""
    weave_project: str | None = None
    """Traces go to Weave when this is set, and nowhere when it is not. Only
    `from_environment` fills it in, so a server built in a test stays local."""
    weave_entity: str | None = None
    waitlist_admin_token: str | None = None
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
    process_timeout_seconds: float = 60 * 60
    """How long a process job (ingest, discovery and the first check) may run, from SP_PROCESS_TIMEOUT_SECONDS.

    These four deadlines are for jobs that run on the worker's own thread,
    where Python can't be interrupted from outside. The job checks its deadline
    at each stage boundary and fails there once it is past, discarding what the
    late stage produced; /health/ready reports a job past its deadline as
    degraded while it is still inside a stage. Each default sits far above what
    the job's slowest step is allowed on its own: a model request here gives up
    after two minutes and a Blender run after five.
    """
    assess_timeout_seconds: float = 20 * 60
    """How long an assess job (the rule check and its model calls) may run, from SP_ASSESS_TIMEOUT_SECONDS."""
    display_timeout_seconds: float = 30 * 60
    """How long a display job may run, from SP_DISPLAY_TIMEOUT_SECONDS. It runs Blender once for the
    geometry and once per finding for its still, each run killed after five minutes."""
    simulate_timeout_seconds: float = 4 * 60 * 60
    """How long a simulation may run, from SP_SIMULATE_TIMEOUT_SECONDS. A deep one runs its trials, a
    thousand by default, once for each of up to nine redesign rounds. The job checks its deadline
    between rounds, so a run past it stops after the round in progress."""
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
    rearrange_provider: str = "openrouter"
    rearrange_model: str | None = None
    """SP_REARRANGE_MODEL is the Fireworks model when that provider is selected."""
    rearrange_openrouter_model: str = "anthropic/claude-opus-5.5"
    rearrange_reasoning_effort: str = "low"
    rearrange_token_cap: int = 4000
    rearrange_cost_cap_dollars: float = 0.50
    rearrange_deployment: str | None = None
    """SP_REARRANGE_DEPLOYMENT: the on-demand deployment serving it, as accounts/<a>/deployments/<id>
    or a bare id. When set, a suggestion lets it run one replica and scales it back to zero after."""
    rearrange_keep_warm_seconds: int = 300
    """SP_REARRANGE_KEEP_WARM_SECONDS: how long the deployment stays up after a suggestion."""
    rearrange_fake_model: bool = False
    """SP_REARRANGE_FAKE_MODEL=1, development only: a local stand-in answers instead of Fireworks."""
    fireworks_api_key: str | None = field(default=None, repr=False)
    """FIREWORKS_API_KEY, from the repo-root .env."""
    openrouter_api_key: str | None = field(default=None, repr=False)

    @property
    def database_path(self) -> pathlib.Path:
        return self.data_dir / "standardphysics.sqlite3"

    def job_deadline_seconds(self, kind: str) -> float:
        """How long a job of this kind may run. A photo bake's deadline is its kill timeout.

        A kind with no deadline gets none; the worker fails such a job as soon as it runs it.
        """
        return {
            "process": self.process_timeout_seconds,
            "assess": self.assess_timeout_seconds,
            "display": self.display_timeout_seconds,
            "simulate": self.simulate_timeout_seconds,
            "texture": self.bake_timeout_seconds,
        }.get(kind, math.inf)

    @classmethod
    def from_environment(cls) -> Settings:
        load_dotenv(REPO_ROOT / ".env")
        return cls(
            data_dir=pathlib.Path(os.environ.get("SP_DATA_DIR", DEFAULT_DATA_DIR)),
            preview_unverified_rules=_flag("SP_PREVIEW_UNVERIFIED_RULES"),
            seed_sample_shop=_flag("SP_SEED_SAMPLE_SHOP"),
            seed_owner_email=os.environ.get("SP_SEED_OWNER_EMAIL", "demo@standardphysics.app"),
            seed_owner_password=os.environ.get("SP_SEED_OWNER_PASSWORD") or secrets.token_urlsafe(12),
            seed_owner_password_generated=not os.environ.get("SP_SEED_OWNER_PASSWORD"),
            weave_project=os.environ.get(PROJECT_ENV) or None,
            weave_entity=os.environ.get(ENTITY_ENV) or None,
            waitlist_admin_token=os.environ.get("SP_WAITLIST_ADMIN_TOKEN") or None,
            auto_deep_simulation=_flag("SP_AUTO_DEEP_SIMULATION"),
            bake_in_own_process=not _flag("SP_BAKE_IN_PROCESS"),
            bake_timeout_seconds=_bounded_integer("SP_BAKE_TIMEOUT_SECONDS", 45 * 60, 60, 86_400),
            process_timeout_seconds=_bounded_integer("SP_PROCESS_TIMEOUT_SECONDS", 60 * 60, 60, 86_400),
            assess_timeout_seconds=_bounded_integer("SP_ASSESS_TIMEOUT_SECONDS", 20 * 60, 60, 86_400),
            display_timeout_seconds=_bounded_integer("SP_DISPLAY_TIMEOUT_SECONDS", 30 * 60, 60, 86_400),
            simulate_timeout_seconds=_bounded_integer("SP_SIMULATE_TIMEOUT_SECONDS", 4 * 60 * 60, 60, 7 * 86_400),
            team_emails=_email_set("SP_TEAM_EMAILS"),
            apns_key=_secret("SP_APNS_KEY", "SP_APNS_KEY_PATH"),
            apns_key_id=os.environ.get("SP_APNS_KEY_ID") or None,
            apns_team_id=os.environ.get("SP_APNS_TEAM_ID") or None,
            apns_topic=os.environ.get("SP_APNS_TOPIC") or "com.standardphysics.capture",
            git_sha=os.environ.get("SP_GIT_SHA") or "unknown",
            apple_audiences=_email_set("SP_APPLE_AUDIENCES") or frozenset({"com.standardphysics.capture"}),
            max_scan_artifacts=_bounded_integer("SP_MAX_SCAN_ARTIFACTS", ScanQuota.max_artifacts, 1, 1_000_000),
            max_scan_bytes=_bounded_integer("SP_MAX_SCAN_BYTES", ScanQuota.max_bytes, 1, 2**50),
            max_owner_scans=_bounded_integer("SP_MAX_OWNER_SCANS", cls.max_owner_scans, 1, 1_000_000),
            max_owner_bytes=_bounded_integer("SP_MAX_OWNER_BYTES", cls.max_owner_bytes, 1, 2**50),
            max_queued_jobs=_bounded_integer("SP_MAX_QUEUED_JOBS", cls.max_queued_jobs, 1, 1_000_000),
            min_free_disk_bytes=_bounded_integer("SP_MIN_FREE_DISK_BYTES", cls.min_free_disk_bytes, 0, 2**50),
            rearrange_provider=os.environ.get("SP_REARRANGE_PROVIDER", "openrouter"),
            rearrange_model=os.environ.get("SP_REARRANGE_MODEL") or None,
            rearrange_openrouter_model=os.environ.get("SP_REARRANGE_OPENROUTER_MODEL", "anthropic/claude-opus-5.5"),
            rearrange_reasoning_effort=os.environ.get("SP_REARRANGE_REASONING_EFFORT", "low"),
            rearrange_token_cap=_bounded_integer("SP_REARRANGE_TOKEN_CAP", 4000, 1024, 16000),
            rearrange_cost_cap_dollars=float(os.environ.get("SP_REARRANGE_COST_CAP_DOLLARS", "0.50")),
            rearrange_deployment=os.environ.get("SP_REARRANGE_DEPLOYMENT") or None,
            rearrange_keep_warm_seconds=_bounded_integer("SP_REARRANGE_KEEP_WARM_SECONDS", 300, 0, 3600),
            rearrange_fake_model=_flag("SP_REARRANGE_FAKE_MODEL"),
            fireworks_api_key=os.environ.get("FIREWORKS_API_KEY") or None,
            openrouter_api_key=os.environ.get("OPENROUTER_API_KEY") or None,
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
