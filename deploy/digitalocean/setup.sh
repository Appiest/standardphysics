#!/usr/bin/env bash
# Prepares a fresh Ubuntu Droplet to run Standard Physics. Run it as root:
#
#   VOLUME_NAME=standardphysics_scans ./setup.sh
#
# It installs Docker, mounts the Block Storage volume, gives the box swap so a
# 4 GB Droplet can build the workspace, and closes every port but SSH and the
# two Caddy needs. Running it twice changes nothing the second time.
#
# It never formats a disk that already holds a filesystem. A volume carrying
# last month's scans is not a blank disk, and the check below is the only
# thing standing between the two.
set -euo pipefail

VOLUME_NAME="${VOLUME_NAME:-standardphysics_scans}"
DEVICE="/dev/disk/by-id/scsi-0DO_Volume_${VOLUME_NAME}"
MOUNT_POINT="/mnt/${VOLUME_NAME}"
SWAPFILE="/swapfile"
SWAP_SIZE_MB=2048

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

enable_unattended_upgrades() {
  log "Turning on security updates"
  apt-get install -y -qq unattended-upgrades
  dpkg-reconfigure -f noninteractive unattended-upgrades
}

main() {
  require_root
  install_docker
  mount_volume
  add_swap
  close_ports
  enable_unattended_upgrades

  cat <<NEXT

Done. What is left:

  1. Point both names in DNS at this Droplet's public IP, and wait for them
     to resolve. Caddy cannot get a certificate before they do.
  2. Clone the repository, then:
       cd standardphysics/deploy/digitalocean
       cp env.example .env
       \$EDITOR .env          # the domains, SCANS_PATH=$MOUNT_POINT, the keys
  3. docker compose up -d --build
  4. curl https://<your api domain>/health

NEXT
}

main "$@"
