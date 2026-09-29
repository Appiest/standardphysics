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

from .receive_deadlines import ReceiveDeadlines
from .repository_jobs import MAX_INTERRUPTIONS
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


def _path(name: str) -> pathlib.Path | None:
    raw = os.environ.get(name)
    return pathlib.Path(raw).expanduser() if raw else None


def _email_set(name: str) -> frozenset[str]:
    return frozenset(part.strip().casefold() for part in os.environ.get(name, "").split(",") if part.strip())


def _bounded_integer(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name)
    value = default if raw is None else int(raw)
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _positive_dollars(name: str, default: float) -> float:
    """A spending limit in dollars. NaN, infinity, zero or less would each let the budget check
    pass every request or none, so they stop the server at startup instead."""
    raw = os.environ.get(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number of dollars, like 0.50, not {raw!r}")
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
    max_owner_uploads: int = 4
    """How many uploads one account may stream at once, from SP_MAX_OWNER_UPLOADS. The phone sends one
    artifact at a time, and at most two while optional files follow the core ones; more is refused with a 429."""
    max_concurrent_uploads: int = 32
    """How many uploads the whole server streams at once, from SP_MAX_CONCURRENT_UPLOADS; more is refused with a 503."""
    max_owner_model_runs: int = 2
    """How many model previews and loops one account may run at once, from SP_MAX_OWNER_MODEL_RUNS; more is
    refused with a 429. The web app runs one at a time, and a second tab makes two."""
    max_concurrent_model_runs: int = 4
    """How many model previews and loops the whole server runs at once, from SP_MAX_CONCURRENT_MODEL_RUNS; more
    is refused with a 503. Each holds a request thread while it waits on the model server."""
    max_request_body_bytes: int = 1024 * 1024
    """The largest body any route but the streamed uploads accepts, from SP_MAX_REQUEST_BODY_BYTES. A larger
    one is refused with a 413 before it is read (see `request_size`). JSON bodies carry moves, answers and
    ids rather than geometry, which travels as an uploaded artifact."""
    max_concurrent_validations: int = 1
    """How many uploaded files are checked at once, from SP_MAX_CONCURRENT_VALIDATIONS; the rest wait their
    turn. Checking the largest mesh holds a few hundred megabytes, and the API has 3.2 GB for everything."""
    upload_idle_seconds: int = 120
    """How long a request body may go without a byte arriving before it is dropped with a 408, from
    SP_UPLOAD_IDLE_SECONDS. Dropping it releases its upload reservation and deletes what it staged."""
    upload_total_seconds: int = 2 * 60 * 60
    """How long any one request body may take to arrive in all, from SP_UPLOAD_TOTAL_SECONDS. The largest
    artifact allowed, 1 GiB, arrives in about 70 minutes at 2 Mbit/s."""
    staging_max_age_seconds: int = 3600
    """How long a staged upload may go unwritten before it counts as abandoned and is deleted, from
    SP_STAGING_MAX_AGE_SECONDS. A streaming upload writes its file every few milliseconds, so an hour
    of silence means the request that owned it is gone, usually in a restart."""
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
    jobs_in_own_process: bool = False
    """Every job but a photo bake runs in a process of its own, killed at its kind's deadline.

    A thread can't be stopped from outside, so a stage stuck in a C extension,
    a network call that never returns or a loop that never ends would hold the
    worker's thread, and every job queued behind it, for ever. A child process
    can be killed. Each child imports the stack again, about 120 MB, and the
    worker runs one such job at a time beside at most one bake. The server
    turns this on; tests leave it off so their stand-in stages run where they
    can see them. SP_JOBS_IN_PROCESS=1 turns it back off.
    """
    process_timeout_seconds: float = 60 * 60
    """How long a process job (ingest, discovery and the first check) may run, from SP_PROCESS_TIMEOUT_SECONDS.

    With `jobs_in_own_process` on, as on the server, a job still running at its
    deadline is killed with everything it started and failed. On the worker's
    own thread, where Python can't be interrupted from outside, the job checks
    its deadline at each stage boundary instead and fails there once it is past,
    discarding what the late stage produced; /health/ready reports a job past
    its deadline as degraded while it is still inside a stage. Each default sits
    far above what the job's slowest step is allowed on its own: a model request
    here gives up after two minutes and a Blender run after five.
    """
    assess_timeout_seconds: float = 20 * 60
    """How long an assess job (the rule check and its model calls) may run, from SP_ASSESS_TIMEOUT_SECONDS."""
    display_timeout_seconds: float = 30 * 60
    """How long a display job may run, from SP_DISPLAY_TIMEOUT_SECONDS. It runs Blender once for the
    geometry and once per finding for its still, each run killed after five minutes."""
    simulate_timeout_seconds: float = 4 * 60 * 60
    """How long a simulation may run, from SP_SIMULATE_TIMEOUT_SECONDS. A deep one runs its trials, a
    thousand by default, once for each of up to nine redesign rounds. On the worker's own thread the
    job checks its deadline between rounds, so a run past it stops after the round in progress."""
    rearrange_timeout_seconds: float = 60 * 60
    """How long a layout suggestion may run, from SP_REARRANGE_TIMEOUT_SECONDS. It asks the model at most
    twenty times, four rounds in each of up to five windows, and an OpenRouter request gives up after two
    minutes, so forty minutes of asking is the most a working suggestion takes. The Fireworks path can also
    wait ten minutes for a cold deployment, and its requests give up after five, so raise this for it."""
    furniture_timeout_seconds: float = 3 * 60 * 60
    """How long a furniture refinement may run, from SP_FURNITURE_TIMEOUT_SECONDS. It fits every chair,
    sofa, table, bed and stool in the build one after another; the script gives each fit up to forty
    minutes and each comparison render five. A run stopped here keeps the fits it finished, and retrying
    it picks up from the first object it had not reached."""
    furniture_python: pathlib.Path | None = None
    """SP_FURNITURE_PYTHON: the interpreter of the virtualenv SPAR3D is installed in. Furniture
    refinement runs only when this and `furniture_source` both name something that exists; the
    production image carries neither, so there it stays off and /health/details says why."""
    furniture_source: pathlib.Path | None = None
    """SP_FURNITURE_SOURCE: the SPAR3D source checkout, which inference runs from."""
    max_job_interruptions: int = MAX_INTERRUPTIONS
    """How many runs of one job restarts may cut short before startup fails it instead of queueing
    it again, from SP_MAX_JOB_INTERRUPTIONS. Retrying the scan clears the count."""
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
    offload_url: str | None = None
    """SP_OFFLOAD_URL: a machine with more memory that runs photo bakes and Blender steps for this
    one (`offload.py`), such as http://100.x.y.z:8790 over Tailscale. Unset, everything runs here."""
    offload_token: str | None = field(default=None, repr=False)
    """SP_OFFLOAD_TOKEN: the shared secret that machine was started with."""

    @property
    def database_path(self) -> pathlib.Path:
        return self.data_dir / "standardphysics.sqlite3"

    def receive_deadlines(self) -> ReceiveDeadlines:
        return ReceiveDeadlines(self.upload_idle_seconds, self.upload_total_seconds)

    def job_deadline_seconds(self, kind: str) -> float:
        """How long a job of this kind may run. A photo bake's deadline is its kill timeout.

        A kind missing from this table gets the shortest deadline here, and a warning in the log,
        rather than no deadline at all: a job with none would hold its worker loop for ever.
        """
        deadlines = {
            "process": self.process_timeout_seconds,
            "assess": self.assess_timeout_seconds,
            "display": self.display_timeout_seconds,
            "simulate": self.simulate_timeout_seconds,
            "texture": self.bake_timeout_seconds,
            "rearrange": self.rearrange_timeout_seconds,
            "furniture": self.furniture_timeout_seconds,
        }
        if kind not in deadlines:
            log.warning("a %s job has no deadline of its own, so it gets the shortest one", kind)
            return min(deadlines.values())
        return deadlines[kind]

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
            jobs_in_own_process=not _flag("SP_JOBS_IN_PROCESS"),
            bake_timeout_seconds=_bounded_integer("SP_BAKE_TIMEOUT_SECONDS", 45 * 60, 60, 86_400),
            process_timeout_seconds=_bounded_integer("SP_PROCESS_TIMEOUT_SECONDS", 60 * 60, 60, 86_400),
            assess_timeout_seconds=_bounded_integer("SP_ASSESS_TIMEOUT_SECONDS", 20 * 60, 60, 86_400),
            display_timeout_seconds=_bounded_integer("SP_DISPLAY_TIMEOUT_SECONDS", 30 * 60, 60, 86_400),
            simulate_timeout_seconds=_bounded_integer("SP_SIMULATE_TIMEOUT_SECONDS", 4 * 60 * 60, 60, 7 * 86_400),
            rearrange_timeout_seconds=_bounded_integer("SP_REARRANGE_TIMEOUT_SECONDS", 60 * 60, 60, 86_400),
            furniture_timeout_seconds=_bounded_integer("SP_FURNITURE_TIMEOUT_SECONDS", 3 * 60 * 60, 60, 86_400),
            furniture_python=_path("SP_FURNITURE_PYTHON"),
            furniture_source=_path("SP_FURNITURE_SOURCE"),
            max_job_interruptions=_bounded_integer("SP_MAX_JOB_INTERRUPTIONS", MAX_INTERRUPTIONS, 1, 100),
            team_emails=_email_set("SP_TEAM_EMAILS"),
            apns_key=_secret("SP_APNS_KEY", "SP_APNS_KEY_PATH"),
            apns_key_id=os.environ.get("SP_APNS_KEY_ID") or None,
            apns_team_id=os.environ.get("SP_APNS_TEAM_ID") or None,
            apns_topic=os.environ.get("SP_APNS_TOPIC") or "com.standardphysics.capture",
            git_sha=os.environ.get("SP_GIT_SHA") or "unknown",
            offload_url=os.environ.get("SP_OFFLOAD_URL") or None,
            offload_token=os.environ.get("SP_OFFLOAD_TOKEN") or None,
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
            rearrange_cost_cap_dollars=_positive_dollars("SP_REARRANGE_COST_CAP_DOLLARS", 0.50),
            rearrange_deployment=os.environ.get("SP_REARRANGE_DEPLOYMENT") or None,
            rearrange_keep_warm_seconds=_bounded_integer("SP_REARRANGE_KEEP_WARM_SECONDS", 300, 0, 3600),
            rearrange_fake_model=_flag("SP_REARRANGE_FAKE_MODEL"),
            fireworks_api_key=os.environ.get("FIREWORKS_API_KEY") or None,
            openrouter_api_key=os.environ.get("OPENROUTER_API_KEY") or None,
            max_owner_uploads=_bounded_integer("SP_MAX_OWNER_UPLOADS", cls.max_owner_uploads, 1, 1_000),
            max_concurrent_uploads=_bounded_integer("SP_MAX_CONCURRENT_UPLOADS", cls.max_concurrent_uploads, 1, 10_000),
            max_owner_model_runs=_bounded_integer("SP_MAX_OWNER_MODEL_RUNS", cls.max_owner_model_runs, 1, 100),
            max_concurrent_model_runs=_bounded_integer(
                "SP_MAX_CONCURRENT_MODEL_RUNS", cls.max_concurrent_model_runs, 1, 1_000
            ),
            max_request_body_bytes=_bounded_integer(
                "SP_MAX_REQUEST_BODY_BYTES", cls.max_request_body_bytes, 1024, 64 * 1024 * 1024
            ),
            max_concurrent_validations=_bounded_integer(
                "SP_MAX_CONCURRENT_VALIDATIONS", cls.max_concurrent_validations, 1, 16
            ),
            upload_idle_seconds=_bounded_integer("SP_UPLOAD_IDLE_SECONDS", cls.upload_idle_seconds, 1, 86_400),
            upload_total_seconds=_bounded_integer(
                "SP_UPLOAD_TOTAL_SECONDS", cls.upload_total_seconds, 1, 7 * 86_400
            ),
            staging_max_age_seconds=_bounded_integer(
                "SP_STAGING_MAX_AGE_SECONDS", cls.staging_max_age_seconds, 60, 7 * 86_400
            ),
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
