# Resilience

This is the failure model for Standard Physics. Each row names a failure, what the system does about it, and the test that proves it. Paths are relative to the repository root, and API tests live in [`services/api/tests`](services/api/tests). [ARCHITECTURE.md](ARCHITECTURE.md) describes the components these rows refer to.

## Jobs and the worker

| Failure | What happens | Proof |
| --- | --- | --- |
| The server dies mid-job | At startup every job left running is queued again with its interruption count raised by one. | [`test_worker_resilience.py`](services/api/tests/test_worker_resilience.py) `test_an_interrupted_job_is_queued_again_and_its_interruption_is_counted` |
| A simulation is interrupted | It is failed instead of re-run. Its budget of paid model calls lives only in the process that ran it, so a re-run would spend the owner's limit a second time without asking. | [`test_job_recovery.py`](services/api/tests/test_job_recovery.py) `test_a_restart_queues_interrupted_jobs_again_but_fails_an_interrupted_simulation` |
| A layout suggestion is interrupted | It is failed with a message asking the owner to ask again, and its Fireworks deployment is scaled back down. | [`test_rearrangement.py`](services/api/tests/test_rearrangement.py) `test_a_restart_fails_the_running_job_and_scales_the_deployment_down` |
| One input crashes the server every time it runs | After `SP_MAX_JOB_INTERRUPTIONS` interrupted runs (3 by default) startup fails the job with the count in its error instead of queueing it again. Retrying the scan clears the count. | [`test_worker_resilience.py`](services/api/tests/test_worker_resilience.py) `test_a_job_interrupted_on_every_run_is_stopped_at_the_ceiling` |
| A second API process opens the same database | An exclusive `flock` beside the database lets one process run jobs. The second serves requests in standby and takes the queue when the first exits. | [`worker_lock.py`](services/api/standardphysics_api/worker_lock.py), `test_a_second_worker_on_the_same_database_refuses_to_run_jobs` |
| A stage hangs | The stage runs in a spawned child that leads its own process group. At its kind's deadline the child is killed with everything it started, the job fails, and the lane moves on. | [`test_worker_jobs_in_own_process.py`](services/api/tests/test_worker_jobs_in_own_process.py) `test_a_stage_that_hangs_is_killed_at_its_deadline_and_the_next_job_still_runs` |
| A rearrange or furniture job hangs | Both kinds have deadlines of their own and are killed the same way. | `test_a_hung_furniture_or_rearrange_job_is_killed_at_its_deadline` |
| A new job kind ships without a deadline | `Settings.job_deadline_seconds` gives an unknown kind the shortest deadline and logs a warning. | `test_an_unknown_kind_still_gets_a_finite_deadline` |
| The database stays locked | The loop logs the error and retries after a backoff that doubles up to a minute. Writing a job's outcome waits out a lock for 60 seconds at most, and other database errors are raised at once. | [`test_worker_resilience.py`](services/api/tests/test_worker_resilience.py) `test_the_loop_survives_a_locked_database_and_keeps_claiming`, `test_a_database_that_stays_locked_while_settling_is_given_up_on` |
| A job meets an error that clears by itself | A locked database or a provider timeout is retried up to 3 runs in all and never past the job's deadline. Ordinary failures are not retried. | `test_transient_retries_are_bounded`, `test_an_ordinary_failure_is_not_tried_again` |
| A job's outcome cannot be written | The job stays running for the next start to queue again, and `/health/ready` reports it as degraded until then. | `test_a_permanent_database_error_while_settling_is_not_retried` |
| A worker loop stalls or dies | `/health/ready` returns 503 for a stalled loop or a job past its deadline. `/health` fails only for a dead loop, because restarting a slow job throws its work away. | `test_a_loop_stuck_outside_any_job_is_reported_stalled`, `test_health_fails_when_a_worker_loop_has_died` |

The per-kind deadlines live in [`settings.py`](services/api/standardphysics_api/settings.py) and are each configurable.

| Kind | Default deadline |
| --- | --- |
| `process` | 60 minutes |
| `assess` | 20 minutes |
| `display` | 30 minutes |
| `texture` | 45 minutes |
| `rearrange` | 60 minutes |
| `furniture` | 3 hours |
| `simulate` | 4 hours |

## Queue fairness

Jobs are claimed per lane, ordered by `queued_at` with measuring jobs first ([`repository_jobs.py`](services/api/standardphysics_api/repository_jobs.py)). A deploy that changes the checks queues a re-check of every shop at startup, and a walk uploaded afterwards is still measured before those re-checks ([`test_job_order.py`](services/api/tests/test_job_order.py), [`test_recheck_after_deploy.py`](services/api/tests/test_recheck_after_deploy.py)). Renders run on the Blender lane, so a queue of re-renders never holds up measuring ([`test_render_lane.py`](services/api/tests/test_render_lane.py)). A simulation that runs for hours sits on its own lane and never holds up a new scan (`test_a_running_simulation_does_not_hold_up_a_new_scan`).

## Load and admission

| Failure | What happens | Proof |
| --- | --- | --- |
| The queue floods | A request that would queue work checks the queue length in the same transaction that inserts the job. At `SP_MAX_QUEUED_JOBS` (200) it gets 503 with `Retry-After` and nothing is kept. Follow-up jobs a finished job queues are never refused. | [`budgets.py`](services/api/standardphysics_api/budgets.py), [`test_queue_admission.py`](services/api/tests/test_queue_admission.py) |
| One account uploads without end | Each account holds at most 25 scans and 6 GB, checked from the headers before the body is read. | [`test_budgets.py`](services/api/tests/test_budgets.py) `test_an_owner_over_budget_is_refused_before_the_body_is_read` |
| Too many uploads at once | Each account streams at most 4 uploads (429) and the server at most 32 (503), both with `Retry-After`. | `test_uploads_past_the_concurrency_cap_are_refused_with_a_retry` |
| The data volume fills | New scans and uploads are refused with 507 below 1 GB free. Every upload in flight holds a reservation of its declared bytes, so two uploads that together cross the floor are not both taken. | `test_two_uploads_that_together_cross_the_disk_floor_are_not_both_taken` |
| Too many model calls at once | Each account runs at most 2 model previews (429) and the server at most 4 (503). | [`test_model_provider.py`](services/api/tests/test_model_provider.py) `test_slots_cap_each_owner_with_a_429_and_the_server_with_a_503` |

## Uploads and input

| Failure | What happens | Proof |
| --- | --- | --- |
| The phone loses signal | The phone records each artifact the server accepted and resumes against the same scan. A repeated upload of the same bytes answers 200 and stores nothing twice. | [`ResumableUploadStoreTests.swift`](apps/ios/StandardPhysicsTests/ResumableUploadStoreTests.swift), [`test_upload_contract.py`](services/api/tests/test_upload_contract.py) |
| Bytes are corrupted in transit | The server hashes what arrived and refuses a SHA-256 mismatch with 400, keeping nothing. | `test_a_wrong_checksum_is_rejected_and_nothing_is_kept` |
| An upload goes quiet or trickles | Every body is read against an idle deadline (120 s) and a total deadline (2 h). Either one answers 408, releases the reservation and deletes the staged file. | [`receive_deadlines.py`](services/api/standardphysics_api/receive_deadlines.py), [`test_slow_uploads.py`](services/api/tests/test_slow_uploads.py) |
| A restart abandons staged files | Staging files are swept at startup and hourly after an hour without a write. | `test_staging_files_abandoned_before_a_restart_are_swept_at_startup` |
| A JSON body is huge | Every route except the streamed uploads refuses a body over 1 MB with 413, by declared length or while reading a chunked body. | [`request_size.py`](services/api/standardphysics_api/request_size.py), [`test_request_size.py`](services/api/tests/test_request_size.py) |
| A usdz is a zip bomb | The archive is inspected from its central directory. It is refused past 1,000 entries, 256 MB expanded or 64 MB on disk, and for any entry path that climbs out. | [`usdz_validation.py`](services/api/standardphysics_api/usdz_validation.py), [`test_usdz_validation.py`](services/api/tests/test_usdz_validation.py) |
| A mesh is enormous | The LiDAR mesh is validated one part at a time on a worker thread, one file at a time, so `/health` keeps answering while a 400 MB mesh is checked. | [`lidar_mesh.py`](services/api/standardphysics_api/lidar_mesh.py), [`test_mesh_validation_load.py`](services/api/tests/test_mesh_validation_load.py) |

## Model providers

| Failure | What happens | Proof |
| --- | --- | --- |
| A provider stalls | Photo detection requests give up after 120 seconds and retry with backoff. The model chooser enforces a reply deadline, including against a reply that drips in. | [`detector_transport.py`](packages/pipeline/standardphysics_pipeline/discovery/detector_transport.py), [`test_model_provider.py`](services/api/tests/test_model_provider.py) `test_a_reply_that_drips_in_past_the_deadline_times_out` |
| A provider returns garbage | Replies are capped in size before parsing, and a malformed reply raises a typed error instead of reaching the checks. | [`test_model_provider.py`](services/api/tests/test_model_provider.py) `test_an_oversized_reply_is_refused_before_it_is_parsed`, [`test_astra.py`](tests/test_astra.py) `test_response_reader_rejects_a_body_over_the_cap` |
| A layout suggestion runs up a bill | Each suggestion has a token cap and a dollar cap. A cap that is NaN, infinite, zero or negative stops the server at startup. | [`test_rearrangement.py`](services/api/tests/test_rearrangement.py) `test_a_cost_cap_no_request_could_exceed_is_refused_at_startup` |
| Weave stops answering | Tracing init and the final flush each run on a daemon thread with a deadline, so neither startup nor a job child's exit waits on W&B. | [`test_tracing.py`](services/api/tests/test_tracing.py) `test_a_child_whose_flush_never_returns_still_exits_and_reports` |
| The offload machine fails | A refusal, an unreachable box or a stalled answer falls back to running the stage on the droplet. | [`test_offload.py`](services/api/tests/test_offload.py) |

## Deploys

[`scripts/deploy.sh`](scripts/deploy.sh) deploys the image CI tested, and [`scripts/tests/test_deploy.py`](scripts/tests/test_deploy.py) runs it against a fake box.

1. It takes an exclusive `flock` on the droplet, so a second deploy from any laptop stops with exit 75.
2. It pulls `ghcr.io/imhaohao/standardphysics:<sha>` and refuses a commit CI never published.
3. It turns on the drain flag ([`drain.py`](services/api/standardphysics_api/drain.py)). Requests that would queue work get 503 with a minute to retry, and the worker starts no queued job. It then waits up to 20 minutes for the running job to finish, and a queue it cannot read refuses the deploy.
4. It restarts the stack and runs [`check_serving.py`](deploy/digitalocean/check_serving.py) until `/health/ready` is 200, `/health/details` reports the new commit, the workspace serves its sign-in page, and `https://$APP_DOMAIN/api/auth/session` returns the API's 401 through Caddy.
5. Only then does it record the commit and image digest in the deploy history. A deploy that never passes is not recorded, prints the rollback command for the last good commit and exits 70.

The drain is turned off on every way out, so a failed deploy never leaves the site refusing work (`test_a_failed_deploy_turns_the_drain_off_so_the_site_takes_work_again`).

## Backups and restore

[`backup.sh`](deploy/digitalocean/backup.sh) runs nightly on a systemd timer. It copies the database with SQLite's online backup API, which captures writes still in the WAL, and then copies the scans as hard-linked snapshots. A file missing from under its row fails the backup and keeps every older snapshot. [`restore.sh`](deploy/digitalocean/restore.sh) restores into a new directory only, then runs SQLite's integrity check and verifies every artifact's SHA-256 against the database. [`scripts/tests/test_backup_restore.py`](scripts/tests/test_backup_restore.py) proves a restored snapshot matches the volume it was taken from.

## Monitoring and alerting

[`monitor.sh`](deploy/digitalocean/monitor.sh) runs every five minutes and asks the API through Caddy, the same way the phone does. It checks readiness, the oldest queued job's age, free space on the scans volume and on the system disk, the newest backup's age, and whether Weave is delivering traces. It sends one message when the set of failing checks changes and resends an alert the webhook refused. [`scripts/tests/test_monitor.py`](scripts/tests/test_monitor.py) covers each check.
