#!/usr/bin/env bash
# Prepare an independent rootless Docker engine. Existing Docker/PM2 services are never restarted.
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then echo 'Run with sudo.' >&2; exit 1; fi
WEB_USER=pockethost-web
WEB_HOME=/home/pockethost-web
if [[ ! -f /sys/fs/cgroup/cgroup.controllers ]]; then echo 'cgroup v2 is required.' >&2; exit 1; fi
for required in docker dockerd-rootless-setuptool.sh rootlesskit; do
  command -v "$required" >/dev/null || { echo "Missing $required. Install Docker rootless extras separately." >&2; exit 1; }
done
# Install only missing prerequisites. Do not upgrade Docker or restart running services.
packages=()
command -v newuidmap >/dev/null || packages+=(uidmap)
command -v slirp4netns >/dev/null || packages+=(slirp4netns)
command -v certbot >/dev/null || packages+=(certbot)
# Deploy-key Git sources: worker fetch (git + ssh) and controller key scan/ls-remote (ssh-keyscan, ssh-keygen).
command -v git >/dev/null || packages+=(git)
{ command -v ssh && command -v ssh-keyscan && command -v ssh-keygen; } >/dev/null || packages+=(openssh-client)
python3 -c 'import boto3, cryptography' >/dev/null 2>&1 || packages+=(python3-boto3 python3-cryptography)
if ((${#packages[@]})); then
  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l
  apt-get update -qq
  apt-get install -y --no-install-recommends "${packages[@]}"
fi
if ! id "$WEB_USER" >/dev/null 2>&1; then useradd --create-home --user-group --shell /bin/bash "$WEB_USER"; fi
web_uid=$(id -u "$WEB_USER")
WEB_APPS_MEMORY_MAX=${WEB_APPS_MEMORY_MAX:-5G}
if [[ ! "$WEB_APPS_MEMORY_MAX" =~ ^[1-9][0-9]*G$ ]]; then echo "WEB_APPS_MEMORY_MAX must use whole GiB, for example 5G." >&2; exit 1; fi
if ! awk -F: -v name="$WEB_USER" '$1==name && $3>=65536 {found=1} END {exit !found}' /etc/subuid; then
  echo 'Missing subordinate UID range for pockethost-web; provision a non-overlapping range first.' >&2; exit 1
fi
if ! awk -F: -v name="$WEB_USER" '$1==name && $3>=65536 {found=1} END {exit !found}' /etc/subgid; then
  echo 'Missing subordinate GID range for pockethost-web.' >&2; exit 1
fi
install -d -m 0750 -o "$WEB_USER" -g "$WEB_USER" "$WEB_HOME/data"
install -d -m 0750 -o "$WEB_USER" -g "$WEB_USER" "$WEB_HOME/data/tls" "$WEB_HOME/data/acme" "$WEB_HOME/data/acme-config"
install -d -m 0755 "/etc/systemd/system/user@${web_uid}.service.d" "/etc/systemd/system/user-${web_uid}.slice.d"
cat > "/etc/systemd/system/user@${web_uid}.service.d/web-delegation.conf" <<'UNIT'
[Service]
Delegate=cpu cpuset io memory pids
UNIT
cat > "/etc/systemd/system/user-${web_uid}.slice.d/web-limits.conf" <<UNIT
[Slice]
CPUQuota=200%
MemoryMax=${WEB_APPS_MEMORY_MAX}
MemorySwapMax=0
TasksMax=2048
UNIT
# Bound the monitored-volume overshoot and keep web disk traffic below the host's capacity.
io_source=$(findmnt -n -o SOURCE / || true)
io_parent=$(lsblk -n -o PKNAME "$io_source" 2>/dev/null | head -n 1 || true)
io_device=${io_parent:+/dev/${io_parent}}
io_device=${io_device:-${io_source}}
if [[ -b "$io_device" ]]; then
  printf '\nIOReadBandwidthMax=%s 100M\nIOWriteBandwidthMax=%s 25M\n' "$io_device" "$io_device" >> "/etc/systemd/system/user-${web_uid}.slice.d/web-limits.conf"
fi
systemctl daemon-reload
loginctl enable-linger "$WEB_USER"
systemctl start "user@${web_uid}.service"
# The socket/context belongs only to the dedicated web user.
runuser -u "$WEB_USER" -- env HOME="$WEB_HOME" XDG_RUNTIME_DIR="/run/user/${web_uid}" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${web_uid}/bus" \
  dockerd-rootless-setuptool.sh install --force
runuser -u "$WEB_USER" -- env HOME="$WEB_HOME" XDG_RUNTIME_DIR="/run/user/${web_uid}" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${web_uid}/bus" \
  systemctl --user enable --now docker
# Allow the existing firewall process to read only the routing/TLS files through a group.
usermod -a -G "$WEB_USER" ubuntu
runuser -u "$WEB_USER" -- env HOME="$WEB_HOME" DOCKER_HOST="unix:///run/user/${web_uid}/docker.sock" \
  docker info --format '{{json .SecurityOptions}} {{.CgroupVersion}} {{.CgroupDriver}}'
echo "Rootless web engine prepared for UID ${web_uid}; existing PocketBase services were not restarted."
# Install the worker unit, but leave first activation to the operator after the pilot.
cat > /etc/systemd/system/pockethost-web-worker.service <<UNIT
[Unit]
Description=PocketHost web applications worker (independent rootless engine)
After=network-online.target user@${web_uid}.service
Wants=network-online.target
Requires=user@${web_uid}.service

[Service]
Type=simple
User=${WEB_USER}
Group=${WEB_USER}
Slice=user-${web_uid}.slice
WorkingDirectory=${WEB_HOME}/data
EnvironmentFile=${WEB_HOME}/web-apps.env
ExecStart=/usr/bin/python3 /opt/pockethost-web/web-app-worker.py
Restart=always
RestartSec=5
UMask=0027
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
UNIT
# The broker shares the worker's restricted control environment and rootless resource budget.
# Install a bundled CommonJS file at this path before first activation; no source tree is exposed.
cat > /etc/systemd/system/pockethost-web-terminal.service <<UNIT
[Unit]
Description=PocketHost web applications interactive terminal broker
After=network-online.target user@${web_uid}.service
Wants=network-online.target
Requires=user@${web_uid}.service

[Service]
Type=simple
User=${WEB_USER}
Group=${WEB_USER}
Slice=user-${web_uid}.slice
WorkingDirectory=${WEB_HOME}/data
EnvironmentFile=${WEB_HOME}/web-apps.env
UnsetEnvironment=WEB_APPS_TERMINAL_PORT WEB_BACKUPS_S3_ENDPOINT WEB_BACKUPS_S3_BUCKET WEB_BACKUPS_S3_REGION WEB_BACKUPS_S3_PREFIX WEB_BACKUPS_S3_ACCESS_KEY_ID WEB_BACKUPS_S3_SECRET_ACCESS_KEY WEB_BACKUPS_S3_IMAGE_KEY
ExecStart=/usr/bin/node /opt/pockethost-web/terminal-broker.cjs
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
echo 'Worker and terminal units installed, not started. Enable after configuration and pilot verification.'
# Construction automatique (Railpack), facultative : binaires root:root vérifiés par empreinte (voir WEB_HOSTING.md).
if [[ "${INSTALL_RAILPACK:-0}" == 1 && -x "$(dirname "$0")/install-railpack.sh" ]]; then "$(dirname "$0")/install-railpack.sh"
else echo 'Construction automatique : exécuter install-railpack.sh pour activer Railpack, puis redémarrer le worker.'; fi
