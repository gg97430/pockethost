The root-owned collector samples Linux host and cgroup-v2 counters every 15
seconds. The authenticated controller reads only its four already calculated
JSON caches; it never launches a sampler, Docker command, or filesystem scan.

Install `platform_resource_collector.py` in `/opt/pockethost-web`, the service
template in systemd, and a reviewed configuration at
`/etc/kolab/app4/resources/collector.json` (root:root 0600, all parent directories
root-owned and not group/world writable). The example contains service names
that must be checked against the host's actual ControlGroup values. Only exact
service cgroups are allowed: a `user@1001.service` parent includes containers and
must never be configured as the rootless Docker daemon's accounting root.

The output directory is outside DATA_ROOT to keep live history out of database
recovery archives. It is root:ubuntu 0750; `resources-{15m,1h,24h,7d}.json` are
0640, `history.sqlite3` and the lock are private 0600. Caches follow
`common/platformResources.ts`. CPU 100% means every logical host CPU combined.
The first CPU sample and reset counters are unknown, not zero. Host RAM uses
MemTotal minus MemAvailable; cgroups use memory.current minus inactive_file.
These Linux estimates can differ: a negative system residual becomes null with
an explicit diagnostic, rather than altering the measured categories.
For CPU only, a tick/rounding uncertainty budget capped at 0.5 host percentage
points suppresses a small discrepancy diagnostic. All measured values and the
null negative system residual remain unchanged; larger discrepancies still warn.

Disk paths are fixed operator configuration. Add `diskRoots` entries with an
absolute `path`, a category (`databases`, `hosting`, `builds`, `platform`, or
`backups`), and optionally a curated `resourceId` matching an app/database ID.
Use the instance data roots and rootful Docker root for databases; WEB_ROOT and
the rootless Docker root for hosting; classify nested BuildKit state/cache as
builds, backup/archive/escrow roots as backups, releases/tools as platform.
Nested roots override their parent category, and physical directory/hardlink
inodes are counted once across the walk. Shared Docker image layers remain one
directory estimate and are not charged again to individual containers.
Filesystem capacity is deduplicated by st_dev. The system category is the
nonnegative residual of filesystem used bytes. An estimate exceeding filesystem
usage, a partial scan, an unconfigured scan, or stale results is explicitly
marked. No symlink is followed. Scans run in a lower-priority child at most once
per five minutes, with time, entry-count, and memory bounds.

SQLite keeps only seven days (at most 40,321 samples) and stores no paths,
commands, environment variables, container names, logs, or secrets. History
contains host totals and six category aggregates, not unbounded resource IDs.
Four views use at most 240 time buckets; values are averages of known samples,
and wholly unavailable buckets remain null. The API checks snapshot freshness
independently (60 seconds). Disk attribution has its separate collected date and
diagnostics. `directory-estimate` does not promise accurate reflink/CoW charging.

The collector makes only Docker GET `/containers/json?all=1&limit=1001` calls on
the two configured Unix sockets. It whitelists ID, classification labels, state,
and the fixed naming conventions; raw Docker responses never reach the caches.
It has no inspect/exec/log or mutation route. The service has no IP network,
read-only system/home/cgroup mounts and a single writable state directory.
No tenant-provided path, socket, command, or configuration is accepted.

Run local regression tests with:

    python3 packages/pockethost/scripts/test-platform-resource-collector.py

Validate the installed private configuration without collecting or writing:

    sudo python3 -I -B /opt/pockethost-web/platform_resource_collector.py --config /etc/kolab/app4/resources/collector.json --check-config
