# Running Standard Physics for real shops

One Ubuntu Droplet, one Block Storage volume, three containers from the image
in `Dockerfile`. Caddy terminates TLS and is the only thing bound to a public
port; the API holds the scans and runs the reconstruction worker; the
workspace serves the pages.

The phone talks to the API directly rather than through the workspace, because
a scan bundle can reach the 1 GB ceiling in `Settings.max_artifact_bytes` and
there is no reason to push that through a Next rewrite.

```
 iPhone ──── https://api.standardphysics.app ───┐
                                          ├── Caddy ──┬── api  + /mnt volume
 Browser ─── https://standardphysics.app ───┘           └── web
```

Everything lives in `deploy/digitalocean/`.

## What you need first

- **A domain.** Two names, one for the API and one for the workspace. The
  iPhone app refuses a plain `http://` address for anything but a machine on
  the local network, so a bare IP will not do: Let's Encrypt does not issue
  certificates for IP addresses.
- **A Droplet.** Ubuntu 24.04, 2 vCPU and 4 GB. The workspace is a Next build
  and a 2 GB box runs out of memory partway through it; `setup.sh` adds swap,
  which covers the gap but does not replace the memory.
- **A Block Storage volume**, 10 GB to start, attached to that Droplet. Scans
  go on it rather than the Droplet's own disk so the box can be rebuilt or
  resized without losing a shop.

## Provision the box

Point both names at the Droplet's public IP in DNS and let them resolve.
Caddy asks Let's Encrypt for a certificate on its first start, and that fails
if the names do not already point here.

Then, on the Droplet as root:

```bash
git clone https://github.com/Imhaohao/standardphysics.git
cd standardphysics/deploy/digitalocean
VOLUME_NAME=standardphysics-scans ./setup.sh
```

That installs Docker, mounts the volume, adds swap, closes every port but SSH
and the two Caddy needs, and turns on unattended security updates. It never
formats a disk that already holds a filesystem, so running it again on a box
with scans on it is safe.

## Secrets

```bash
cp env.example .env
$EDITOR .env
```

Fill in the two domains, `SCANS_PATH` as the script printed it, and the keys.
Generate the session secret on the box:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set a spend limit on the OpenRouter account and turn on zero data retention
before the first shop scans anything. Every scan sends photographs of
somebody's business to that endpoint.

Leave `WANDB_*` empty. Tracing a deployment that holds real shops sends their
rooms somewhere else.

## Start it

```bash
docker compose up -d --build
docker compose logs -f caddy    # watch the certificate arrive
curl https://api.standardphysics.app/health
```

The first build takes a while: it installs the Python packages and builds the
workspace on the box.

## Blender

The image carries Blender 5.2.1, pinned, because the texture bake and the
picture beside each finding are rendered by it. Debian's package is no use:
`check_blender.py` shows 4.0.2 still advertises `*.usd` and cannot import a
USDZ, so the binary comes from blender.org.

Only `render_finding` and the texture bake need it, and only the bake has no
fallback, so a server without Blender looks like scans that work and reports
with no pictures in them. `doctor.sh` asks the container for its version.

It adds about 366 MB to the image, and blender.org publishes no arm64 Linux
build of this version, which is what keeps the Droplet on x86_64.

## When it will not start

```bash
./doctor.sh
```

It checks the configuration, the mount and its ownership, swap, the two names
in DNS, and every container's state, then prints the command to run for each
thing that is wrong. It changes nothing itself.

The symptom is almost never the cause here. Caddy reporting that its
dependency failed to start says only that the API exited, and the API usually
exited because it could not write to `/data`.

## Updating

From your own machine, which is the usual way:

```bash
scripts/deploy.sh
```

It pulls master on the Droplet, rebuilds, and runs `doctor.sh`, streaming the
lot back. Only one deploy runs at a time: a second is refused rather than
queued, because two of them racing to recreate a container leave the name
taken, the stack half torn down and the site answering 502. It stops if you have commits master does not, because the Droplet
pulls from GitHub and a deploy that quietly ships the previous commit is worse
than one that refuses. `SP_DEPLOY_HOST` moves it to another box.

On the Droplet itself it is the two commands the script runs:

```bash
git pull
docker compose up -d --build
```

The API restarts, which interrupts any reconstruction in flight. Those jobs
are requeued on the next start by `requeue_interrupted_jobs`, so an update
during a busy afternoon costs time rather than a scan.

## One container holds the database

The database is SQLite on the volume and the worker claims jobs from it, so
the API is one container and stays one container. Two would be two workers
racing the same queue. Moving the store to Postgres is what lifts that, and
is worth doing when more than one person is scanning at a time.

## Backups

A volume snapshot is not a database backup: SQLite may be mid-write when the
snapshot is taken. DigitalOcean's snapshots are a floor to fall back on, not
the plan. For a real copy:

```bash
docker compose exec api /opt/venv/bin/python -c \
  "import sqlite3; s=sqlite3.connect('/data/standardphysics.sqlite3'); \
   d=sqlite3.connect('/data/backup.sqlite3'); s.backup(d)"
scp root@<droplet>:/mnt/standardphysics-scans/backup.sqlite3 .
```

The scan artifacts sit beside it under the same mount and are the larger half.
Until they are on Spaces or another object store, losing the volume loses the
rooms. `doctl compute volume-action snapshot` schedules nothing on its own, so
put it in cron or take one before each update.

## Pointing the app at it

The iPhone app ships with both addresses compiled in, set in
`apps/ios/project.yml`:

```
CAPTURE_API_BASE_URL: https://api.standardphysics.app
CAPTURE_WORKSPACE_BASE_URL: https://standardphysics.app
```

The connection screen stays in the app for development, and an owner never has
to open it. `AppEnvironment` prefers anything already saved in `UserDefaults`,
so a phone that was pointed at a laptop keeps pointing there until someone
clears it.

## Cost

| | |
|---|---|
| Droplet | 2 vCPU, 4 GB, Ubuntu 24.04 |
| Block Storage | 10 GB, grows with the shops |
| Domain | one, two records |
| Model calls | per scan |

DigitalOcean prices the first two and publishes current rates. Measure the
model calls yourself with `scripts/scan_cost.py`, which reads what OpenRouter
actually billed for one scan rather than estimating it.
