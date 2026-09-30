# Security

A scan is the inside of somebody's shop, so Standard Physics treats every floor plan, photo and finding as private to its owner. This file describes how the system enforces that and how to report a problem. API tests live in [`services/api/tests`](services/api/tests).

## Reporting a vulnerability

Please open a private security advisory on this repository from the Security tab on GitHub (Security, then Advisories, then "Report a vulnerability"). The advisory is visible only to you and the maintainers. Include the affected route or file, the steps to reproduce it, and what an attacker gains. Please don't open a public issue for a vulnerability.

## Authentication

| Mechanism | What it does | Proof |
| --- | --- | --- |
| Passwords | Stored as scrypt digests with a per-password salt, cost 2^14. Passwords shorter than 10 characters are refused. | [`accounts.py`](services/api/standardphysics_api/accounts.py), [`test_auth.py`](services/api/tests/test_auth.py) `test_two_owners_with_the_same_password_do_not_share_a_hash` |
| Sessions | A 32-byte random token. The database keeps only its SHA-256, so a copy of the database holds no live session. The browser gets it as an `HttpOnly`, `SameSite=Lax` cookie that is `Secure` over HTTPS, and the phone sends it as a bearer header. | [`auth.py`](services/api/standardphysics_api/auth.py), `test_the_session_cookie_is_not_readable_by_scripts`, `test_the_token_is_never_in_the_response_body` |
| Signing out | Deletes the session row, so the token stops working on every device. | `test_signing_out_kills_the_token_everywhere` |
| Sign-in throttling | Each account allows 10 wrong passwords and each network 30 attempts per 5 minutes, counted before any scrypt work. An unknown email costs exactly one scrypt call, the same as a wrong password, so timing does not reveal which emails exist. Each network may create 10 accounts an hour. | [`attempt_limiter.py`](services/api/standardphysics_api/attempt_limiter.py), [`test_sign_in_throttle.py`](services/api/tests/test_sign_in_throttle.py) |
| Sign in with Apple | The identity token's signature, issuer and audience are verified against Apple's keys. A flood of unknown key ids fetches Apple's keys once, and each network may try 30 times per window. | [`apple_identity.py`](services/api/standardphysics_api/apple_identity.py), [`test_apple_sign_in.py`](services/api/tests/test_apple_sign_in.py) |

### Account linking with Apple

Sign-up never confirms an email, so someone could register another person's address first. When Apple signs in with an email it has verified and a password account already holds that email, the Apple ID takes the account over. The squatter's password, sessions and push tokens are revoked in the same transaction ([`owner_accounts.py`](services/api/standardphysics_api/owner_accounts.py)). An email Apple has not verified claims nothing, and an email an Apple account holds cannot be signed up with a password ([`test_guests.py`](services/api/tests/test_guests.py) `test_apple_takes_back_an_email_someone_else_registered_first`, `test_an_unverified_apple_email_claims_nothing`).

## Authorization

Every scan route lives under `/api/scans`, and one middleware settles ownership before any handler runs ([`auth.py`](services/api/standardphysics_api/auth.py)). A route added later is covered the moment it is registered. A scan that belongs to someone else answers 404, the same as a scan that does not exist, so a stranger cannot learn which ids are real.

The routes parse scan ids as UUIDs, which also accept hyphenless, uppercase, braced and `urn:uuid:` spellings. The middleware refuses every spelling except the canonical one, so a stranger gets the same 404 whichever spelling they try (`test_another_spelling_of_someone_elses_scan_id_is_still_not_theirs`).

The team tools (the ask box, the improvement loop, simulations, rebuilds and combining rooms) answer 403 to anyone without the team role. The role is a flag on the account that only someone with a shell on the server can set, with `python -m standardphysics_api.team grant <email>` ([`team.py`](services/api/standardphysics_api/team.py), [`test_team.py`](services/api/tests/test_team.py)). Signing up with a team email grants nothing.

## Share links

An owner can share a read-only report with a contractor or an inspector ([`sharing.py`](services/api/standardphysics_api/sharing.py)). The link carries a random 24-byte token, and the database keeps only its SHA-256. Every route under `/api/shared` is a `GET`, so a link can read the report, the scene and the finding stills but can change nothing. A link expires after 30 days and the owner can revoke it at any time ([`test_sharing_and_plans.py`](services/api/tests/test_sharing_and_plans.py)).

A share token is a working credential, so the API's access log replaces it with `<token>` in every path it records ([`access_log.py`](services/api/standardphysics_api/access_log.py), [`test_access_log.py`](services/api/tests/test_access_log.py)).

## Input limits

| Limit | Value | Proof |
| --- | --- | --- |
| JSON request body | 1 MB, refused with 413 by declared length or while reading a chunked body | [`test_request_size.py`](services/api/tests/test_request_size.py) |
| Artifact upload | 1 GiB per artifact, with per-scan, per-account and disk limits checked from the headers before the body is read | [`test_budgets.py`](services/api/tests/test_budgets.py), [`test_upload_contract.py`](services/api/tests/test_upload_contract.py) |
| Upload integrity | The server hashes every upload and refuses a SHA-256 mismatch. An artifact id that climbs out of the store is refused. | `test_a_wrong_checksum_is_rejected_and_nothing_is_kept`, `test_an_artifact_id_cannot_climb_out_of_the_store` |
| usdz expansion | At most 1,000 entries, 256 MB expanded and 64 MB on disk, with no entry path that climbs out | [`test_usdz_validation.py`](services/api/tests/test_usdz_validation.py) |
| LiDAR mesh | At most 640 MB, validated one part at a time against the `LidarMesh` contract | [`test_mesh_validation_load.py`](services/api/tests/test_mesh_validation_load.py) |
| Offload parcels | A tar member that is a link or names a path outside the extraction directory is refused | [`test_offload.py`](services/api/tests/test_offload.py) |

No API response carries a server file path (`test_no_response_carries_a_server_path`).

## Secrets

Provider keys (OpenRouter, Fireworks, W&B, the APNs key and the offload token) live in the `.env` file on the droplet, or in a file it names. [`settings.py`](services/api/standardphysics_api/settings.py) loads them into the API process at startup, and the key fields are excluded from the settings object's `repr`. The web workspace has no `NEXT_PUBLIC_` variables and no provider key in its source, and the iPhone app carries none. Both clients reach models only through the API.

| Where a key could leak | What keeps it out | Proof |
| --- | --- | --- |
| Job records | The API stores each provider request's provider, model, request id and usage, and never a secret or a pixel | [`test_job_attempts.py`](services/api/tests/test_job_attempts.py) `test_provider_failure_categories_are_visible_and_secret_free` |
| Weave traces | The router's model spans record what was sent and received without the key or the `Authorization` header | [`test_router.py`](packages/agents/tests/test_router.py) `test_the_key_never_reaches_the_trace` |
| Object `repr` in logs | The model chooser's `repr` leaves out its key | [`test_model_chooser.py`](services/api/tests/test_model_chooser.py) `test_the_key_never_appears_in_the_choosers_repr` |
| The wrong provider | Photo detection sends each host its own key, so a Fireworks endpoint never receives the OpenRouter key | [`detector_transport.py`](packages/pipeline/standardphysics_pipeline/discovery/detector_transport.py) |
| Git history | gitleaks scans every commit in CI | [`ci.yml`](.github/workflows/ci.yml) |

## Model provider privacy

OpenRouter requests carry `data_collection: deny`, the per-request half of zero data retention, and are pinned to the provider that makes the model with fallbacks off. That covers photo detection ([`detector_transport.py`](packages/pipeline/standardphysics_pipeline/discovery/detector_transport.py)), object labelling ([`astra_transport.py`](packages/pipeline/standardphysics_pipeline/astra_transport.py)), the agents' OpenRouter client ([`models.py`](packages/agents/standardphysics_agents/models.py)) and layout suggestions ([`openrouter_rearrange.py`](services/api/standardphysics_api/openrouter_rearrange.py)). [`test_ask.py`](packages/agents/tests/test_ask.py) and [`test_detection_throughput.py`](packages/pipeline/tests/test_detection_throughput.py) assert the routing on the request.

Fireworks receives layout suggestions when `SP_REARRANGE_PROVIDER=fireworks` ([`fireworks.py`](services/api/standardphysics_api/fireworks.py)), labelling from the fine-tuned labeller ([`tuned_labeller.py`](packages/pipeline/standardphysics_pipeline/tuned_labeller.py)), and photo detection when `DISCOVERY_BASE_URL` points at it. OpenRouter's routing rules travel only to OpenRouter, because other hosts reject them.

## Infrastructure

| Control | Where |
| --- | --- |
| The API and the workspace run as the non-root `physics` user (uid 10001) | [`Dockerfile`](Dockerfile) |
| Every base image is pinned by digest, and a test fails on one that is not | [`Dockerfile`](Dockerfile), [`test_release_workflow.py`](tests/test_release_workflow.py) `test_every_dockerfile_base_is_pinned_by_digest` |
| Caddy is pinned by digest, terminates TLS with Let's Encrypt, and is the only container with a public port | [`docker-compose.yml`](deploy/digitalocean/docker-compose.yml), [`Caddyfile`](deploy/digitalocean/Caddyfile) |
| The API container is capped at 3.2 GB and 1.75 CPUs and the workspace at 512 MB and 1 CPU, so a runaway process dies inside its own container | [`docker-compose.yml`](deploy/digitalocean/docker-compose.yml) |
| The droplet's firewall allows only SSH, 80 and 443 | [`setup.sh`](deploy/digitalocean/setup.sh) |
| Every GitHub Action is pinned to a commit SHA | [`ci.yml`](.github/workflows/ci.yml), [`ios.yml`](.github/workflows/ios.yml) |
| The offload machine checks its bearer token in constant time and refuses a caller on another commit or Blender version | [`offload.py`](services/api/standardphysics_api/offload.py) |

## CI security gates

The `publish` job in [`ci.yml`](.github/workflows/ci.yml) lists every check in `needs:`, so a failing gate stops the image from reaching the tag production pulls.

| Gate | What it checks |
| --- | --- |
| gitleaks | Every commit in the history of the commit being released, with the scanner's archive verified against a pinned SHA-256. `.gitleaksignore` names each false positive by fingerprint. |
| pip-audit | Every package in `requirements.lock`, which is what the image installs |
| npm audit | The workspace's production dependencies, failing on high severity |
| grype | The exact image the image job tested, read by digest from GHCR on master, failing on a critical vulnerability that has a fix |

After the gates pass, `publish` retags that same digest and fails if the published digest differs from the one that was tested ([`test_release_workflow.py`](tests/test_release_workflow.py) `test_the_vulnerability_scan_reads_the_tested_image`).
