#!/bin/bash
# First-boot setup for the Motionly instance (EC2 user data; the template sets
# MOTIONLY_DATA_VOLUME_ID above this script). Releases are applied later over
# SSM Run Command (scripts/deploy), never through SSH.
#   - Mounts the persistent EBS data volume at /srv/motionly, creating its file
#     system only when the volume is blank.
#   - Installs Docker, a pinned, checksum-verified Docker Compose plugin and cosign.
#     The AWS CLI and python3 ship with Amazon Linux 2023.
set -euo pipefail

COMPOSE_VERSION=v5.6.0
COMPOSE_SHA256=40343e21ca777173e69cff5dbafeb37c6f81f3b0d57d9e597f036e95eb63e76a
# Verifies signed releases (scripts/deploy/apply_release.sh); the same release
# the supply-chain workflow signs with (sigstore/cosign-installer v3.8.1).
COSIGN_VERSION=v2.4.3
COSIGN_SHA256=caaad125acef1cb81d58dcdc454a1e429d09a750d1e9e2b3ed1aed8964454708
DATA_MOUNT=/srv/motionly

: "${MOTIONLY_DATA_VOLUME_ID:?the template sets MOTIONLY_DATA_VOLUME_ID}"
# On Nitro instances the EBS serial is the volume id without its dash.
device="/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${MOTIONLY_DATA_VOLUME_ID/-/}"
for _ in $(seq 1 120); do
  [ -e "$device" ] && break
  sleep 5
done
if [ ! -e "$device" ]; then
  echo "motionly-bootstrap: data volume $MOTIONLY_DATA_VOLUME_ID never attached" >&2
  exit 1
fi
if ! blkid "$device" >/dev/null 2>&1; then
  mkfs.xfs -L motionly-data "$device"
fi
mkdir -p "$DATA_MOUNT"
uuid=$(blkid -s UUID -o value "$device")
if ! grep -q "UUID=$uuid" /etc/fstab; then
  echo "UUID=$uuid $DATA_MOUNT xfs defaults,nofail,noatime 0 2" >>/etc/fstab
fi
mountpoint -q "$DATA_MOUNT" || mount "$DATA_MOUNT"
chmod 0750 "$DATA_MOUNT"

dnf install -y docker
mkdir -p /etc/docker
# Docker's data root lives on the data volume so images and volumes survive
# instance replacement.
cat >/etc/docker/daemon.json <<JSON
{"data-root": "$DATA_MOUNT/docker", "log-driver": "local", "log-opts": {"max-size": "50m", "max-file": "5"}}
JSON
plugins=/usr/local/lib/docker/cli-plugins
mkdir -p "$plugins"
curl -fsSL -o "$plugins/docker-compose" \
  "https://github.com/docker/compose/releases/download/$COMPOSE_VERSION/docker-compose-linux-x86_64"
echo "$COMPOSE_SHA256  $plugins/docker-compose" | sha256sum -c -
chmod 0755 "$plugins/docker-compose"
curl -fsSL -o /usr/local/bin/cosign \
  "https://github.com/sigstore/cosign/releases/download/$COSIGN_VERSION/cosign-linux-amd64"
echo "$COSIGN_SHA256  /usr/local/bin/cosign" | sha256sum -c -
chmod 0755 /usr/local/bin/cosign
systemctl enable --now docker
docker compose version
