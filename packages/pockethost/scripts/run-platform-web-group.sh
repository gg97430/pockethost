#!/usr/bin/env bash
# Refresh the supplementary group for a new controller/firewall child of an older PM2 daemon.
set -euo pipefail
case "${1:-}" in mothership|firewall) ;; *) echo 'Expected mothership or firewall' >&2; exit 1 ;; esac
shifted_service=$1
shift
if [[ ${1:-} != serve || $# -ne 1 ]]; then echo 'Expected serve' >&2; exit 1; fi
exec sudo -n -u ubuntu -- /bin/bash -c 'cd /home/ubuntu/pockethost && exec /usr/bin/pnpm prod:cli "$@"' -- "$shifted_service" serve
