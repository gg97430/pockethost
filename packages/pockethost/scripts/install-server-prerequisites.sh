#!/usr/bin/env bash
# Shared Debian/Ubuntu prerequisites for database restores AND web hosting.
# Installs missing packages only. Docker, Node, configuration and service activation
# remain explicit steps in the server guide. No dist-upgrade or service restart.
set -euo pipefail
packages=(
  ca-certificates curl git openssh-client openssh-server
  python3 python3-boto3 python3-cryptography awscli build-essential pkg-config
  tar gzip zip unzip rsync sqlite3 jq util-linux cpulimit
  uidmap dbus-user-session slirp4netns fuse-overlayfs
  ufw fail2ban unattended-upgrades logrotate
  certbot python3-certbot-dns-cloudflare
)
if [[ "${1:-}" == --list ]]; then printf '%s\n' "${packages[@]}"; exit 0; fi
if (($#)); then echo 'Usage: install-server-prerequisites.sh [--list]' >&2; exit 2; fi
if ((EUID != 0)); then echo 'Run with sudo.' >&2; exit 1; fi
command -v apt-get >/dev/null || { echo 'Debian/Ubuntu with apt-get required.' >&2; exit 1; }
missing=()
for package in "${packages[@]}"; do
  status=$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)
  [[ "$status" == 'install ok installed' ]] || missing+=("$package")
done
if ((${#missing[@]})); then
  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l
  apt-get update -qq
  apt-get install -y --no-install-recommends "${missing[@]}"
fi
# Validate actual executables: Python's sqlite3 module does not supply the CLI.
for binary in cpulimit sqlite3 tar gzip nice ionice unzip python3 git ssh ssh-keygen ssh-keyscan certbot; do
  command -v "$binary" >/dev/null || { echo "Missing executable: $binary" >&2; exit 1; }
done
python3 -c 'import boto3, cryptography, sqlite3'
aws --version
echo 'Prerequisites installed. Configure and validate the host before activation.'
