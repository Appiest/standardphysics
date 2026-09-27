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
GIT_SHA=$(git rev-parse HEAD) docker compose up -d --build
docker compose logs -f caddy    # watch the certificate arrive
curl https://api.standardphysics.app/health
```

The first build takes a while: it installs the Python packages and builds the
workspace on the box.

`GIT_SHA` names the commit being built. The image is tagged
`standardphysics:<sha>` as well as `standardphysics:latest`, and the API
reports the commit at `/health/details`, which is how a rollback knows what
it is rolling back from. Left out, the tag falls back to `latest` and the API
reports `unknown`.

The compose file caps the API at 3.2 GB and 1.75 cores and the workspace at
512 MB and one core, sized so a photo bake of up to about 3 GB fits and Caddy,
Docker and SSH still have room. The comment at the top of
`docker-compose.yml` has the arithmetic. On a bigger Droplet, raise them
there.

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

It also waits while the API has jobs queued or running. The restart stops
the API, and a bake interrupted ten minutes in starts again from nothing, so
the script counts the unfinished rows in the `jobs` table, through the API
container's own Python, and stops with exit code 75 if there are any. It
stops with exit code 69 if it cannot read the queue at all, because a
stopped or wedged API is when nobody knows what it was doing. To deploy
anyway in either case:

```bash
SP_DEPLOY_FORCE=1 scripts/deploy.sh
```

The order on the box is pull, build, read the queue, restart. The build takes
minutes and the old API keeps serving through it, so the queue is read after
the build and immediately before `docker compose up -d` swaps the containers.
A refused deploy leaves the new image built, and running the script again
once the queue drains reuses it from the cache. The build also moves the
`standardphysics:latest` tag to the new image, so a bare `docker compose up -d`
typed on the box without `GIT_SHA` would start it.

A window remains. An upload that finalises between the queue read and the
moment the old container stops, about a second, queues a job the check did
not see. That job is not lost: `requeue_interrupted_jobs` puts every job left
`running` back in the queue on the next start, and a job still `queued`
simply waits for the new worker. What the window costs is the progress of a
job that started in that second. Closing it completely needs the worker to
stop claiming jobs while a maintenance flag is set, which lives in
`worker.py` and has not been built.

Each deploy appends the time and the commit to
`/var/log/standardphysics-deploys.log` on the Droplet. That file is the list of
commits you can roll back to.

On the Droplet itself it is the commands the script runs:

```bash
git checkout master
git pull
export GIT_SHA=$(git rev-parse HEAD)
docker compose build
# count the unfinished jobs, as above, and stop here if there are any
docker compose up -d
```

## Rolling back

Every deploy leaves its image behind, tagged with its commit, so going back
to an earlier one reuses that image rather than building it again. On the
Droplet:

```bash
cat /var/log/standardphysics-deploys.log     # pick the commit to go back to
docker image ls standardphysics              # check its image is still here
cd /root/standardphysics
git checkout <sha>
cd deploy/digitalocean
GIT_SHA=<sha> docker compose up -d
curl -s https://api.standardphysics.app/health/details   # "commit" is now <sha>
```

Leave `--build` off. With it, compose rebuilds from the checked-out source,
which gives the same result far more slowly. Without it, compose finds
`standardphysics:<sha>` and starts it. If that image has been pruned, the
command builds it from the checked-out commit instead.

The checkout also rolls back `docker-compose.yml` and `Caddyfile` to that
commit, which is what you want: the image and the configuration it was
deployed with go back together.

The database is not rolled back with the code. Schema changes here only add
tables and columns (`_add_missing_columns` in `db.py`), so an older server
normally runs on a newer database without noticing. If the release being
undone wrote data the older code cannot read, restore the database from the
backup taken before that release (below).

The next `scripts/deploy.sh` returns the box to master before it pulls, so
rolling forward again is an ordinary deploy.

Old images take a few GB each. Clear out the ones you will not roll back to
with `docker image rm standardphysics:<sha>`, keeping the last few.

## One container holds the database

The database is SQLite on the volume and the worker claims jobs from it, so
the API is one container and stays one container. Two would be two workers
racing the same queue. Moving the store to Postgres is what lifts that, and
is worth doing when more than one person is scanning at a time.

## Backups

`deploy/digitalocean/backup.sh` takes one snapshot of everything on the
volume: the database, and the scans and keys beside it. Set where the
snapshots go in `.env`:

```bash
SP_BACKUP_DEST=/mnt/standardphysics-backups          # a path on this box
SP_BACKUP_DEST=backup@203.0.113.7:/srv/standardphysics   # or another box, over ssh
SP_BACKUP_KEEP=14
```

A local path should be on a second Block Storage volume, not the scans
volume, or the backup is lost with the thing it backs up. A remote target
needs rsync installed there and an ssh key on this box that works without a
passphrase. Spaces and other object stores are not supported, because
snapshots share unchanged files through hard links and an object store has
none.

Then turn on the nightly run, which `setup.sh` installed switched off:

```bash
systemctl enable --now standardphysics-backup.timer
systemctl start standardphysics-backup.service   # one now, to see it work
journalctl -u standardphysics-backup.service
```

How it works, and why:

- The database is copied with SQLite's online backup API, run by the API
  container's own Python. A plain file copy is not a backup here. The API
  keeps the database in WAL mode, where recent writes live in a separate
  `-wal` file until a checkpoint, so copying the main file alone can lose them.
- The volume is then copied with `rsync --link-dest` into a directory named
  for the UTC time, like `2026-09-27T103000Z`. A file that has not changed
  since the previous snapshot becomes a hard link to it, so each snapshot is a
  complete tree that costs only the space of what changed.
- The database goes first because an upload writes its file before it
  commits its row. Every artifact the database copy lists therefore already
  has its file on disk when rsync reads the volume. Copying the files first
  would miss the file of any upload that landed in between, and uploads are
  far more common than deletions.
- A deletion runs the other way round: the API commits the rows gone, then
  removes the files. A scan deleted after the database copy is still listed
  in the snapshot, with its files already gone. So after the copy, every
  listed artifact without a file is looked up in the live database. If its
  row has gone there too, it was deleted during the backup, and its name goes
  into `artifacts-deleted-during-backup.txt` in the snapshot. If its row is
  still there, the file is really missing: the backup keeps the snapshot,
  deletes no older one, since an older one may hold the only copy, names the
  files and exits 2, which fails the systemd unit.
- A snapshot is written as `<name>.partial` and renamed when it finishes, so
  a backup that dies halfway never looks like a good one.
- After a snapshot finishes, all but the newest `SP_BACKUP_KEEP` are deleted.
- One backup runs at a time, serialised with `flock` on
  `/var/lock/standardphysics-backup`. A second one, such as the timer
  catching up while a manual run is going, exits 75 without touching
  anything.

Run `backup.sh` by hand before anything risky, such as a rollback past a
release that changed stored data.

DigitalOcean's volume snapshots are still worth having as a floor, but they
catch SQLite mid-write, and `doctl compute volume-action snapshot` schedules
nothing on its own.

### Restoring

`restore.sh` copies a snapshot into a new directory and checks it. It never
writes over the live volume.

```bash
cd /root/standardphysics/deploy/digitalocean
./restore.sh                                 # lists the snapshots
./restore.sh latest /root/restored
```

It prints SQLite's integrity check, the number of scans, and the number of
artifacts the database lists against the number of files. It exits 1 if the
database is damaged, and 2 if some listed artifact has no file, naming each
one. Artifacts the backup recorded as deleted while it ran are printed on
their own lines and do not count as missing. A scan uploaded while the backup
ran can show up as a file the database does not list yet, which is harmless.

To put a checked copy back under the API:

```bash
docker compose stop api web
rsync -a /mnt/standardphysics-scans/ /mnt/standardphysics-backups/before-restore/
rsync -a --delete --exclude=/lost+found /root/restored/ /mnt/standardphysics-scans/
chown -R 10001:10001 /mnt/standardphysics-scans
docker compose start api web
```

The first copy keeps what was live, in case the restore was the mistake; put
it anywhere with room. `--delete` makes the volume hold exactly the restored
files, including removing the old `-wal` and `-shm` files, which belong to the
database being replaced. The volume stays mounted throughout, because it is a
mount point and moving it would move the mount.

## What to alert on

Nothing here pages anyone yet. These are the conditions worth an alert, what
to poll for each, and whether `/health/details` already answers it. Its body
looks like this:

```json
{
  "worker": {"lock": "held", "loops": {
    "jobs": {"state": "busy", "heartbeat_seconds": 4.0,
             "job": {"kind": "process", "id": 812, "running_seconds": 41.2}},
    "textures": {"state": "idle", "heartbeat_seconds": 1.0, "job": null}}},
  "oldest_queued_job_seconds": 38,
  "commit": "9a2e29a…"
}
```

| Condition | Alert when | Where to read it | In `/health/details` |
| --- | --- | --- | --- |
| Queue age | `oldest_queued_job_seconds` over 1800. A whole-floor bake runs about 15 minutes, so a job waiting twice that means the worker is stuck or far behind. | `/health/details` | Yes |
| Worker stall | `/health` answers 503 because a loop has died, or a loop's `state` is `stalled`, or a `busy` loop's `job.running_seconds` passes the longest bake you expect | `/health`, `/health/details` | Yes |
| Disk free | Under 15% or 5 GB free on the scans volume, or on the backup destination. Uploads and bakes write there, and SQLite fails every write once it is full. | `df -h /mnt/standardphysics-scans`, or the `space:` line of `doctor.sh` | No |
| Failed backup | The unit failed, or the newest snapshot is more than 26 hours old. `backup.sh` exits 2 when a file the live database lists is missing, and 75 when another backup was already running. | `systemctl is-failed standardphysics-backup.service`, `./restore.sh` with no arguments lists the snapshots | No |
| Failed deploy | `scripts/deploy.sh` exits non-zero: 75 means jobs were in flight, 69 means the queue could not be read, anything else means the pull, build or restart failed. After a deploy, the `commit` in `/health/details` should match the last line of `/var/log/standardphysics-deploys.log`, which only records deploys that got as far as the restart. | the script's exit code, `/health/details` | The commit only |
| Tracing off | Only when `WANDB_PROJECT` is set on purpose and traces stop arriving. `tracing_status()` in `standardphysics_agents.tracing` reports whether traces are sent and why not. | the API log's `weave tracing is off` warning | Not yet wired in |

A cron job on the Droplet that curls `/health/details` and runs `df` every
few minutes, posting to a webhook when a row trips, covers the first four.
DigitalOcean's uptime checks can watch `/health` from outside.

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
