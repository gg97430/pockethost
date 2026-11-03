#!/usr/bin/env python3
"""Bounded live RAM allocation for Kolab's two Docker engines.

No tenant command, environment, secret, network, CPU setting or process is changed.
Only verified running customer containers can receive a memory-only Docker update.
The operator explicitly enables automatic memory for adaptive hosting and native DBs.
"""
import argparse
from contextlib import closing
import datetime
import fcntl
import http.client
import json
import math
import os
import pathlib
import re
import socket
import sqlite3
import stat
import time

MIB = 1024 ** 2
ID = re.compile(r'[a-z0-9]{15}\Z')
CID = re.compile(r'[a-f0-9]{64}\Z')
CGROUP = pathlib.Path('/sys/fs/cgroup')
STATE = pathlib.Path('/var/lib/kolab-memory-allocation')
MAX_BYTES = 8 * 1024 ** 2


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def read_json(path, trusted=False):
    path = pathlib.Path(path)
    if path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Invalid JSON file')
    if trusted:
        s = path.stat()
        if s.st_uid != 0 or s.st_mode & 0o022:
            raise ValueError('Operator-owned configuration required')
    return json.loads(path.read_text())


def atomic(path, value, mode=0o600):
    temp = path.with_name('.' + path.name + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        os.fchmod(f.fileno(), mode)
        json.dump(value, f, separators=(',', ':'))
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=4)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class Engine:
    def __init__(self, path, kind):
        expected = '/var/run/docker.sock' if kind == 'databases' else '/run/user/1001/docker.sock'
        if path != expected or not stat.S_ISSOCK(os.stat(path).st_mode):
            raise ValueError('Dedicated Docker socket required')
        self.path, self.kind = path, kind

    def request(self, method, path, body=None):
        if not (method == 'GET' and (path == '/containers/json?all=0' or re.fullmatch(r'/containers/[a-f0-9]{64}/json', path))
                or method == 'POST' and re.fullmatch(r'/containers/[a-f0-9]{64}/update', path)):
            raise ValueError('Docker operation refused')
        conn = UnixHTTP(self.path)
        try:
            conn.request(method, '/v1.47' + path, body=None if body is None else json.dumps(body),
                         headers={'Content-Type': 'application/json'})
            r = conn.getresponse()
            raw = r.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES or not 200 <= r.status < 300:
                raise ValueError('Docker refused the operation')
            return json.loads(raw) if raw else {}
        finally:
            conn.close()

    def inspect(self, cid):
        if not CID.fullmatch(cid):
            raise ValueError('Invalid container identity')
        r = self.request('GET', '/containers/' + cid + '/json')
        # Project immediately: never retain Env, commands or logs.
        return {'cid': r['Id'], 'name': r['Name'].lstrip('/'), 'pid': r['State']['Pid'],
                'running': r['State']['Running'], 'labels': r['Config'].get('Labels') or {},
                'host': {k: r['HostConfig'].get(k, 0) for k in ('Memory', 'MemorySwap', 'MemoryReservation')},
                'mounts': [(m['Source'], m['Destination']) for m in r.get('Mounts', [])]}

    def update(self, item, target):
        body = update_body(item['host'], target)
        result = self.request('POST', '/containers/' + item['cid'] + '/update', body)
        if result.get('Warnings'):
            raise ValueError('Docker memory update returned a warning')
        actual = self.inspect(item['cid'])
        if actual['host']['Memory'] != target or not actual['running']:
            raise ValueError('Memory update not confirmed')


def update_body(host, target):
    if not isinstance(target, int) or target < 64 * MIB:
        raise ValueError('Invalid memory target')
    old, swap = host['Memory'], host['MemorySwap']
    # Preserve pre-existing swap policy, including unlimited native DB swap.
    combined = -1 if swap == -1 or old == 0 and swap == 0 else target + max(0, swap - old)
    return {'Memory': target, 'MemorySwap': combined,
            'MemoryReservation': min(host['MemoryReservation'], target)}


def scope(pid, cid):
    if not isinstance(pid, int) or pid <= 1 or not CID.fullmatch(cid):
        raise ValueError('Invalid process identity')
    text = pathlib.Path('/proc/' + str(pid) + '/cgroup').read_text().strip()
    if not text.startswith('0::/') or '\n' in text:
        raise ValueError('Cgroup v2 required')
    parts = pathlib.PurePosixPath(text[3:]).parts
    if '..' in parts or 'docker-' + cid + '.scope' not in parts:
        raise ValueError('Container cgroup mismatch')
    end = parts.index('docker-' + cid + '.scope')
    p = CGROUP.joinpath(*parts[1:end + 1])
    if p.resolve() != p:
        raise ValueError('Cgroup symlink refused')
    return p


def counters(p):
    current = int((p / 'memory.current').read_text())
    entries = dict(line.split() for line in (p / 'memory.stat').read_text().splitlines())
    inactive = int(entries['inactive_file'])
    if current < 0 or inactive < 0:
        raise ValueError('Invalid memory counter')
    return current, max(0, current - inactive)


def ancestor_headroom(p):
    result = []
    for parent in p.parents:
        if parent == CGROUP:
            break
        if not parent.is_relative_to(CGROUP):
            raise ValueError('Invalid cgroup ancestor')
        raw = (parent / 'memory.max').read_text().strip()
        if raw == 'max':
            continue
        maximum = int(raw)
        _, used = counters(parent)
        result.append(max(0, maximum - used - max(512 * MIB, maximum // 10)))
    return min(result, default=2 ** 63)


def pressure(p):
    # Existing counters are mandatory if PSI is provided; optional on older kernels.
    file = p / 'memory.pressure'
    if not file.exists():
        return False
    for line in file.read_text().splitlines():
        parts = line.split()
        if parts and parts[0] == 'some':
            value = float(dict(t.split('=') for t in parts[1:])['avg10'])
            if not 0 <= value <= 100:
                raise ValueError('Invalid memory pressure')
            return value >= 10
    raise ValueError('Invalid memory pressure')


def host_memory():
    values = {line.split(':')[0]: int(line.split()[1]) * 1024
              for line in pathlib.Path('/proc/meminfo').read_text().splitlines() if ':' in line}
    total, free = values['MemTotal'], values['MemAvailable']
    if not 0 < free <= total:
        raise ValueError('Invalid host memory')
    return total, free


def database_inventory(path):
    # sqlite3.Connection's context manager only commits/rolls back: it does not
    # close the connection. A five-second daemon must release its WAL descriptors
    # deterministically, including on inventory errors, without waiting for GC.
    with closing(sqlite3.connect(pathlib.Path(path).as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        db.execute('PRAGMA query_only=ON')
        ids = {r[0] for r in db.execute('SELECT id FROM instances')}
        profiles = {r[0]: json.loads(r[1]) for r in db.execute('SELECT appId,profile FROM web_runtime_profiles')}
        apps = {r[0]: r[1] for r in db.execute('SELECT id,activeDeployment FROM web_apps')}
        busy = {r[0] for r in db.execute("SELECT instance FROM instance_backups WHERE status='running' OR restoreState='running'")}
    if any(not ID.fullmatch(x) for x in ids | set(apps)):
        raise ValueError('Invalid platform inventory')
    return ids, profiles, apps, busy


def ownership(item, kind, ids, profiles, apps, instances_root):
    if not item['running'] or not CID.fullmatch(item['cid']):
        return None
    if kind == 'databases':
        ident = item['name']
        expected = str(pathlib.Path(instances_root) / ident)
        if ident not in ids or not any(src == expected and dest == '/home/pockethost' for src, dest in item['mounts']):
            return None
        return ident
    labels = item['labels']
    app, dep = labels.get('pockethost.web.app', ''), labels.get('pockethost.web.deployment', '')
    if (labels.get('pockethost.web') != 'true' or labels.get('pockethost.web.private-db') == 'true'
            or labels.get('pockethost.web.allocation') == 'fixed'
            or not ID.fullmatch(app) or not ID.fullmatch(dep) or apps.get(app) != dep
            or not item['name'].startswith('ph-web-' + app + '-' + dep)
            or profiles.get(app, {}).get('allocation', 'adaptive') != 'adaptive'):
        return None
    # Require the exact base name or a Compose service present in the trusted profile.
    suffix = item['name'][len('ph-web-' + app + '-' + dep):]
    services = profiles.get(app, {}).get('compose', {}).get('services', {})
    if suffix and (not suffix.startswith('-') or suffix[1:] not in services):
        return None
    return app


def round_up(value):
    return math.ceil(value / (64 * MIB)) * 64 * MIB


def propose(current, working, limit, floor, ceiling, previous, now):
    """Pure policy: grow early, shrink slowly, never reclaim live application memory."""
    low_since = previous.get('lowSince', now) if limit > 0 and working < limit * 0.35 else now
    target, reason = limit, 'stable'
    if limit == 0:
        target = round_up(max(floor, current + 256 * MIB, working * 2 + 256 * MIB))
        reason = 'initial'
    elif working >= limit * 0.70:
        target = round_up(max(limit * 1.5, current + 256 * MIB, working * 1.5 + 256 * MIB))
        reason = 'grow'
    elif now - low_since >= 1800 and now - previous.get('changedAt', 0) >= 1800:
        target = round_up(max(floor, limit * 0.75, current + 256 * MIB, working * 2 + 256 * MIB))
        reason = 'shrink'
    target = max(floor, min(ceiling, target))
    if reason == 'shrink' and target >= limit:
        return limit, 'stable', low_since
    if target < current + 128 * MIB:
        # Particularly important for first management of unlimited DBs and caches.
        return limit, 'live-memory-protected', low_since
    if target == limit or limit > 0 and abs(target - limit) < 64 * MIB:
        return limit, 'ceiling' if reason == 'grow' else 'stable', low_since
    return target, reason, low_since


def project_allowance(registry, app, limits):
    if registry.get('version') != 1 or not isinstance(registry.get('apps'), dict) or not isinstance(registry.get('projects'), dict):
        raise ValueError('Project budgets unavailable')
    project = registry['apps'].get(app)
    if not project:
        return 2 ** 63
    resources = registry['projects'][project].get('resources', {})
    if not resources.get('enabled'):
        return 2 ** 63
    # Explicit aggregate budgets take precedence. Do not race a worker admitting
    # a candidate publication against a controller resizing another service.
    # Shrinking is safe; growth requires lifting this explicit project budget.
    return 0


def cycle(config, engines, state, dry_run=False, now=None, before_update=None):
    now = time.time() if now is None else now
    total, available = host_memory()
    reserve = max(2 * 1024 ** 3, total * 15 // 100)
    free = max(0, available - reserve)
    host_pressure = pressure(CGROUP)
    ids, profiles, apps, busy = database_inventory(config['database'])
    registry = read_json(pathlib.Path(config['webRoot']) / 'project-limits.json')
    inventory = []
    for engine in engines:
        rows = engine.request('GET', '/containers/json?all=0')
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValueError('Invalid Docker inventory')
        for row in rows:
            # Root-owned databases / app IDs come from native controller inventory.
            names, labels = row.get('Names', []), row.get('Labels') or {}
            if engine.kind == 'databases' and not any(n.lstrip('/') in ids for n in names):
                continue
            if engine.kind == 'hosting' and labels.get('pockethost.web') != 'true':
                continue
            item = engine.inspect(row['Id'])
            owner = ownership(item, engine.kind, ids, profiles, apps, config['instancesRoot'])
            if owner:
                item.update(engine=engine, kind=engine.kind, owner=owner)
                inventory.append(item)
    limits = [(i['owner'], i['host']['Memory']) for i in inventory if i['kind'] == 'hosting']
    next_state, report = {}, []
    for item in sorted(inventory, key=lambda i: (i['kind'] != 'databases', i['owner'], i['cid'])):
        cid, host, kind, owner = item['cid'], item['host'], item['kind'], item['owner']
        old = state.get(cid, {})
        if old.get('pendingTarget') == host['Memory']:
            old = {**old, 'lastApplied': host['Memory']}
        baseline = old.get('baseline', host)
        entry = {**old, 'baseline': baseline, 'owner': owner, 'kind': kind, 'checkedAt': now}
        reason = None
        if old.get('lastApplied', host['Memory']) != host['Memory'] or old.get('externalOverride'):
            reason = 'external-override'
            entry['externalOverride'] = True
        elif kind == 'databases' and baseline['Memory'] > 0:
            reason = 'fixed-database-limit'
        elif kind == 'databases' and owner in busy:
            reason = 'backup-or-restore'
        try:
            p = scope(item['pid'], cid)
            current, working = counters(p)
            floor = max(256 * MIB if kind == 'hosting' else 512 * MIB, host['MemoryReservation'])
            default_max = config['webMaximumMiB'] * MIB if kind == 'hosting' else config['databaseMaximumMiB'] * MIB
            ceiling = max(baseline['Memory'], min(default_max, total // (4 if kind == 'hosting' else 2)))
            if kind == 'hosting':
                count = sum(i['kind'] == kind and i['owner'] == owner for i in inventory)
                ceiling = max(baseline['Memory'], ceiling // count // (64 * MIB) * (64 * MIB))
            target, decision, low_since = propose(current, working, host['Memory'], floor, ceiling, old, now)
            entry['lowSince'] = low_since
            reason = reason or decision
            delta = target - host['Memory'] if host['Memory'] else target
            if reason in ('grow', 'initial') and (host_pressure or pressure(p) or delta > min(free, ancestor_headroom(p))):
                reason = 'server-reserve'
            if reason in ('grow', 'initial') and kind == 'hosting' and delta > project_allowance(registry, owner, limits):
                reason = 'project-budget'
            if reason in ('grow', 'shrink', 'initial'):
                # Recheck identity and raw usage immediately before the only mutation.
                fresh = item['engine'].inspect(cid)
                live, _ = counters(p)
                if not fresh['running'] or fresh['pid'] != item['pid'] or fresh['host'] != host or target < live + 128 * MIB:
                    raise ValueError('Container changed before allocation')
                if not dry_run:
                    if before_update:
                        before_update(cid, {**entry, 'pendingTarget': target, 'lastApplied': host['Memory']})
                    item['engine'].update(item, target)
                    entry.update(lastApplied=target, changedAt=now)
                    entry.pop('pendingTarget', None)
                    free -= max(0, delta)
                    limits.append((owner, delta)) if kind == 'hosting' else None
            entry.update(memoryBytes=working, rawMemoryBytes=current, allocatedBytes=target if reason in ('grow', 'shrink', 'initial') else host['Memory'],
                         ceilingBytes=ceiling, floorBytes=floor, decision=reason)
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
            entry['decision'] = 'unavailable'
        next_state[cid] = entry
        report.append({k: entry[k] for k in ('owner', 'kind', 'decision', 'memoryBytes', 'allocatedBytes', 'ceilingBytes') if k in entry})
    return next_state, {'checkedAt': stamp(), 'reserveBytes': reserve, 'availableBytes': available,
                        'dryRun': dry_run, 'resources': report}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='/etc/kolab/memory-allocation.json')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError('Operator root service required')
    config = read_json(args.config, trusted=True)
    expected = {'database', 'instancesRoot', 'webRoot', 'webMaximumMiB', 'databaseMaximumMiB'}
    if set(config) != expected or any(not isinstance(config[k], int) or not 512 <= config[k] <= 32768 for k in ('webMaximumMiB', 'databaseMaximumMiB')):
        raise ValueError('Invalid memory allocation configuration')
    for key in ('database', 'instancesRoot', 'webRoot'):
        p = pathlib.Path(config[key])
        if not p.is_absolute() or p.resolve() != p:
            raise ValueError('Invalid allocation path')
    worker_env = pathlib.Path('/home/pockethost-web/web-apps.env').read_text()
    if re.search(r'^WEB_APPS_ALLOCATION_MODE=[\"\x27]?fixed[\"\x27]?\s*$', worker_env, re.M):
        raise ValueError('Automatic memory disabled by fixed server allocation')
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(STATE / '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            state = read_json(STATE / 'state.json', trusted=True)
        except FileNotFoundError:
            state = {}
        engines = [Engine('/var/run/docker.sock', 'databases'), Engine('/run/user/1001/docker.sock', 'hosting')]
        while True:
            try:
                journal = dict(state)
                def before_update(cid, entry):
                    journal[cid] = entry
                    atomic(STATE / 'state.json', journal)
                next_state, report = cycle(config, engines, state, args.dry_run, before_update=before_update)
                if not args.dry_run:
                    atomic(STATE / 'state.json', next_state)
                    atomic(STATE / 'status.json', report, 0o640)
                    state = next_state
                if args.once:
                    print(json.dumps(report))
            except Exception as exc:
                # Never echo Docker responses, metadata or tenant secrets.
                print('Memory allocation deferred: ' + type(exc).__name__, flush=True)
                if args.once:
                    raise SystemExit(1) from None
            if args.once:
                return
            time.sleep(5)


if __name__ == '__main__':
    main()
