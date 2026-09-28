#!/usr/bin/env bash
# Prepares a fresh Ubuntu Droplet to run Standard Physics. Run it as root:
#
#   VOLUME_NAME=standardphysics-scans ./setup.sh
#
# It installs Docker, mounts the Block Storage volume, gives the box swap so a
# 4 GB Droplet can build the workspace, and closes every port but SSH and the
# two Caddy needs. It installs a logrotate rule for the deploy log that
# scripts/deploy.sh appends to. It also installs a nightly backup timer, left off until .env
# names somewhere to send the backups, and a monitor that runs every five
# minutes, left off until .env names somewhere to send alerts. Running it
# twice changes nothing the second time.
#
# It never formats a disk that already holds a filesystem. A volume carrying
# last month's scans is not a blank disk, and the check below is the only
# thing standing between the two.
set -euo pipefail

# DigitalOcean accepts lowercase letters, numbers and hyphens in a volume
# name, and no underscores. The name goes into the device path verbatim.
VOLUME_NAME="${VOLUME_NAME:-standardphysics-scans}"
DEVICE="/dev/disk/by-id/scsi-0DO_Volume_${VOLUME_NAME}"
MOUNT_POINT="/mnt/${VOLUME_NAME}"
SWAPFILE="/swapfile"
SWAP_SIZE_MB=2048
HERE="$(cd "$(dirname "$0")" && pwd)"
BACKUP_UNIT=standardphysics-backup
MONITOR_UNIT=standardphysics-monitor

log() { printf '\n== %s\n' "$1"; }

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "Run this as root: sudo VOLUME_NAME=$VOLUME_NAME $0" >&2
    exit 1
  fi
}

install_docker() {
  if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
    log "Docker is already here"
    return
  fi
  log "Installing Docker"
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
}

mount_volume() {
  if [ ! -e "$DEVICE" ]; then
    echo "No volume at $DEVICE." >&2
    echo "Attach a Block Storage volume named '$VOLUME_NAME' to this Droplet first," >&2
    echo "or set VOLUME_NAME to the name of the one you attached." >&2
    exit 1
  fi

  if blkid "$DEVICE" >/dev/null 2>&1; then
    log "Volume already has a filesystem, leaving it alone"
  else
    log "Formatting the empty volume"
    mkfs.ext4 -F "$DEVICE"
  fi

  mkdir -p "$MOUNT_POINT"
  if ! grep -q "$MOUNT_POINT" /etc/fstab; then
    echo "$DEVICE $MOUNT_POINT ext4 defaults,nofail,discard 0 2" >> /etc/fstab
  fi
  mountpoint -q "$MOUNT_POINT" || mount "$MOUNT_POINT"

  # The container runs as uid 10001, declared in the Dockerfile.
  chown -R 10001:10001 "$MOUNT_POINT"
  log "Scans will live in $MOUNT_POINT"
}

add_swap() {
  if swapon --show | grep -q "$SWAPFILE"; then
    log "Swap is already on"
    return
  fi
  log "Adding ${SWAP_SIZE_MB}MB of swap so the workspace build fits"
  fallocate -l "${SWAP_SIZE_MB}M" "$SWAPFILE"
  chmod 600 "$SWAPFILE"
  mkswap "$SWAPFILE" >/dev/null
  swapon "$SWAPFILE"
  grep -q "$SWAPFILE" /etc/fstab || echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab
}

close_ports() {
  log "Allowing SSH and the web, refusing the rest"
  ufw allow OpenSSH >/dev/null
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
  ufw --force enable >/dev/null
}

# Cubic halves its window on every lost packet, so one download over home
# Wi-Fi crawled at 1.5 MB/s; BBR paces by measured bandwidth and holds
# 11 MB/s on the same path. Caddy's compose entry asks for bbr too, which
# only works once the module is loaded here.
use_bbr() {
  log "Sending with BBR"
  echo tcp_bbr > /etc/modules-load.d/bbr.conf
  modprobe tcp_bbr
  printf 'net.core.default_qdisc = fq\nnet.ipv4.tcp_congestion_control = bbr\n' > /etc/sysctl.d/90-bbr.conf
  sysctl -q -p /etc/sysctl.d/90-bbr.conf
}

enable_unattended_upgrades() {
  log "Turning on security updates"
  apt-get install -y -qq unattended-upgrades
  dpkg-reconfigure -f noninteractive unattended-upgrades
}

# The containers' own logs are capped in docker-compose.yml. This covers the
# files the deploy scripts append to on the host, which nothing else trims.
# logrotate refuses a rule file that anyone but root can write, hence 0644.
install_log_rotation() {
  log "Rotating the deploy log"
  apt-get install -y -qq logrotate
  install -m 0644 -o root -g root "$HERE/logrotate.conf" /etc/logrotate.d/standardphysics
}

env_has() {
  [ -f "$HERE/.env" ] && grep -Eq "^$1=.+" "$HERE/.env"
}

# The timer is installed every time and only switched on once .env names a
# destination, because a backup with nowhere to go fails every night and a
# failing unit nobody asked for teaches people to ignore failing units.
#
# 10:30 UTC is half past two or three in the morning in California, depending
# on daylight saving, when nobody is scanning a shop. Persistent catches up on a night the box was off, and the idle I/O
# class lets a bake or an upload go first.
install_backup_timer() {
  log "Installing the nightly backup timer"
  apt-get install -y -qq rsync
  cat > "/etc/systemd/system/$BACKUP_UNIT.service" <<UNIT
[Unit]
Description=Back up the Standard Physics database and scans
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
ExecStart=$HERE/backup.sh
Nice=10
IOSchedulingClass=idle
UNIT
  cat > "/etc/systemd/system/$BACKUP_UNIT.timer" <<UNIT
[Unit]
Description=Back up Standard Physics every night

[Timer]
OnCalendar=*-*-* 10:30:00 UTC
RandomizedDelaySec=15m
Persistent=true

[Install]
WantedBy=timers.target
UNIT
  systemctl daemon-reload
  if env_has SP_BACKUP_DEST; then
    systemctl enable --now "$BACKUP_UNIT.timer"
    log "Backups run nightly to the SP_BACKUP_DEST in .env"
  else
    log "Backups are off until SP_BACKUP_DEST is set in .env"
  fi
}

# Off until .env has SP_ALERT_WEBHOOK, for the same reason as the backups:
# monitor.sh with nowhere to send an alert says so and does nothing.
# StateDirectory gives it /var/lib/standardphysics-monitor, where it keeps the
# checks that were failing last time, so one outage sends one message.
install_monitor_timer() {
  log "Installing the five-minute monitor"
  cat > "/etc/systemd/system/$MONITOR_UNIT.service" <<UNIT
[Unit]
Description=Check Standard Physics and alert when something breaks or recovers
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$HERE/monitor.sh
StateDirectory=$MONITOR_UNIT
UNIT
  cat > "/etc/systemd/system/$MONITOR_UNIT.timer" <<UNIT
[Unit]
Description=Check Standard Physics every five minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
UNIT
  systemctl daemon-reload
  if env_has SP_ALERT_WEBHOOK; then
    systemctl enable --now "$MONITOR_UNIT.timer"
    log "Alerts go to the SP_ALERT_WEBHOOK in .env"
  else
    log "Monitoring is off until SP_ALERT_WEBHOOK is set in .env"
  fi
}

main() {
  require_root
  install_docker
  mount_volume
  add_swap
  close_ports
  use_bbr
  enable_unattended_upgrades
  install_log_rotation
  install_backup_timer
  install_monitor_timer

  cat <<NEXT

Done. What is left:

  1. Point both names in DNS at this Droplet's public IP, and wait for them
     to resolve. Caddy cannot get a certificate before they do.
  2. Clone the repository, then:
       cd standardphysics/deploy/digitalocean
       cp env.example .env
       \$EDITOR .env          # the domains, SCANS_PATH=$MOUNT_POINT, the keys
  3. GIT_SHA=\$(git rev-parse HEAD) docker compose up -d --build
  4. curl https://<your api domain>/health
  5. Set SP_BACKUP_DEST in .env, then turn on the nightly backup:
       systemctl enable --now $BACKUP_UNIT.timer
  6. Set SP_ALERT_WEBHOOK in .env, then turn on the monitor:
       systemctl enable --now $MONITOR_UNIT.timer

NEXT
}

main "$@"
