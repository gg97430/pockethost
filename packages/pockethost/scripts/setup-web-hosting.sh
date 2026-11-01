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
# Validate the host profile before package installation or any configuration write.
: "${WEB_APPS_MEMORY_MAX:?Set WEB_APPS_MEMORY_MAX explicitly (whole GiB, e.g. 16G).}"
: "${WEB_APPS_CPU_QUOTA:?Set WEB_APPS_CPU_QUOTA explicitly (percent, e.g. 900%).}"
WEB_OPERATOR=ubuntu
WEB_NODE_BINARY=${WEB_NODE_BINARY:-/usr/bin/node}
[[ "$WEB_APPS_MEMORY_MAX" =~ ^[1-9][0-9]*G$ ]] || { echo 'Invalid memory budget.' >&2; exit 1; }
[[ "$WEB_APPS_CPU_QUOTA" =~ ^[1-9][0-9]*%$ ]] || { echo 'Invalid CPU quota.' >&2; exit 1; }
[[ "$(id -u "$WEB_OPERATOR")" == 1000 && "$(id -g "$WEB_OPERATOR")" == 1000 ]] || { echo 'The private DB bridge requires ubuntu=1000:1000.' >&2; exit 1; }
for database in passwd group; do
  entry=$(getent "$database" 1001 || true)
  if [[ -n "$entry" && "${entry%%:*}" != "$WEB_USER" ]]; then echo 'UID/GID 1001 is occupied: use a reviewed identity migration, not automatic renumbering.' >&2; exit 1; fi
done
if id "$WEB_USER" >/dev/null 2>&1; then
  [[ "$(id -u "$WEB_USER")" == 1001 && "$(id -g "$WEB_USER")" == 1001 && "$(getent passwd "$WEB_USER" | cut -d: -f6)" == "$WEB_HOME" ]] || { echo 'Existing web account does not match the supported profile.' >&2; exit 1; }
fi
[[ "$WEB_NODE_BINARY" =~ ^/[A-Za-z0-9_./-]+$ && -x "$WEB_NODE_BINARY" ]] || { echo 'Provide an absolute WEB_NODE_BINARY path.' >&2; exit 1; }
"$WEB_NODE_BINARY" -e 'if(Number(process.versions.node.split(".")[0]) < 24) process.exit(1)' || { echo 'Node >=24 required.' >&2; exit 1; }
python3 - "$WEB_APPS_MEMORY_MAX" "$WEB_APPS_CPU_QUOTA" <<'PROFILE'
import os, pathlib, sys
memory = int(sys.argv[1][:-1]) * 1024**3
host = int(next(line.split()[1] for line in pathlib.Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:'))) * 1024
if memory > host - 1024**3 or int(sys.argv[2][:-1]) > (os.cpu_count() or 1) * 100:
    sys.exit('Budgets exceed the host capacity (keep at least 1 GiB outside web hosting).')
PROFILE
# Reconfiguration of an active host must be planned, never silently change live quotas/units.
for unit in pockethost-web-worker.service pockethost-web-terminal.service; do
  if systemctl is-active --quiet "$unit"; then echo "$unit is active: use the documented update procedure." >&2; exit 1; fi
done
if [[ "${INSTALL_RAILPACK:-0}" == 1 && ! -x "$(dirname "$0")/install-railpack.sh" ]]; then
  echo 'Railpack installer missing: provision the pinned tools from the guide before continuing.' >&2; exit 1
fi
if [[ "${INSTALL_RAILPACK:-0}" == 1 ]]; then
  : "${RAILPACK_BINARY_SOURCE:?Provide the pinned Railpack binary.}"
  : "${MISE_BINARY_SOURCE:?Provide the pinned mise binary.}"
fi
bash "$(dirname "$0")/install-server-prerequisites.sh"
if ! id "$WEB_USER" >/dev/null 2>&1; then
  getent group "$WEB_USER" >/dev/null || groupadd --gid 1001 "$WEB_USER"
  [[ "$(getent group "$WEB_USER" | cut -d: -f3)" == 1001 ]] || { echo 'Web group must use GID 1001.' >&2; exit 1; }
  useradd --uid 1001 --gid "$WEB_USER" --home-dir "$WEB_HOME" --create-home --shell /bin/bash "$WEB_USER"
fi
web_uid=$(id -u "$WEB_USER")
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
CPUQuota=${WEB_APPS_CPU_QUOTA}
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
if [[ ! -f "$WEB_HOME/.config/systemd/user/docker.service" ]]; then
  runuser -u "$WEB_USER" -- env HOME="$WEB_HOME" XDG_RUNTIME_DIR="/run/user/${web_uid}" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${web_uid}/bus" \
    dockerd-rootless-setuptool.sh install
fi
runuser -u "$WEB_USER" -- env HOME="$WEB_HOME" XDG_RUNTIME_DIR="/run/user/${web_uid}" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${web_uid}/bus" \
  systemctl --user enable --now docker
# Allow the existing firewall process to read only the routing/TLS files through a group.
usermod -a -G "$WEB_USER" "$WEB_OPERATOR"
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
ExecStart=${WEB_NODE_BINARY} /opt/pockethost-web/terminal-broker.cjs
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
else echo 'Construction automatique : installer les binaires épinglés avec install-railpack.sh avant la recette.'; fi
