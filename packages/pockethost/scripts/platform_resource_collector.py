#!/usr/bin/env python3
"""Operator-only, read-only Linux resource sampler; APIs consume prebuilt JSON.

Docker is queried only through its container-list GET endpoint. Its response is
immediately projected onto accounting metadata; neither inspect, Env, commands,
logs nor tenant processes are consulted. CPU and memory come from cgroup v2.
Disk attribution is an allocated-directory estimate, not an image-layer charge.
"""
import argparse
import concurrent.futures
import datetime
import fcntl
import grp
import http.client
import json
import math
import multiprocessing
import os
import pathlib
import re
import signal
import socket
import sqlite3
import stat
import sys
import time

CATEGORIES = ('databases', 'hosting', 'builds', 'platform', 'backups', 'system')
RANGES = {'15m': 900, '1h': 3600, '24h': 86400, '7d': 604800}
INTERVAL = 15
RETENTION = 7 * 86400
MAX_ROWS = RETENTION // INTERVAL + 1
ID = re.compile(r'[a-z0-9]{15}\Z')
CID = re.compile(r'[a-f0-9]{64}\Z')
RESOURCE_ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9:_-]{0,95}\Z')
DOCKER_SCOPE = re.compile(r'docker-([a-f0-9]{64})\.scope\Z')
METRICS = ('cpuPercent', 'memoryBytes', 'diskBytes')
MAX_DOCKER_BYTES = 8 * 1024 * 1024


def iso(timestamp=None):
    return datetime.datetime.fromtimestamp(time.time() if timestamp is None else timestamp,
                                          datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def bounded_text(path, limit=128 * 1024):
    with open(path, 'r', encoding='utf-8') as handle:
        value = handle.read(limit + 1)
    if len(value) > limit:
        raise ValueError('Oversized counter')
    return value


def numeric_file(path):
    value = bounded_text(path, 128).strip()
    return None if value == 'max' else int(value)


def key_values(text):
    result = {}
    for line in text.splitlines():
        pieces = line.replace(':', '').split()
        if len(pieces) >= 2:
            result[pieces[0]] = int(pieces[1])
    return result


def host_counters(proc):
    lines = bounded_text(proc / 'stat').splitlines()
    values = [int(x) for x in lines[0].split()[1:9]]  # guest already included in user/nice
    if len(values) < 4 or any(x < 0 for x in values):
        raise ValueError('Invalid CPU counter')
    cores = sum(bool(re.match(r'cpu\d+ ', x)) for x in lines)
    if not 1 <= cores <= 65536:
        raise ValueError('Invalid CPU count')
    memory = key_values(bounded_text(proc / 'meminfo'))
    total, available = memory['MemTotal'] * 1024, memory['MemAvailable'] * 1024
    if total <= 0 or not 0 <= available <= total:
        raise ValueError('Invalid memory counter')
    return {'cpu': (sum(values), values[3] + (values[4] if len(values) > 4 else 0)),
            'cpuCount': cores, 'memoryTotalBytes': total, 'memoryAvailableBytes': available,
            'memoryUsedBytes': total - available,
            'uptimeSeconds': max(0.0, float(bounded_text(proc / 'uptime', 256).split()[0]))}


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=2)
        self.unix_path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.unix_path)


def docker_resources(endpoint):
    """No arbitrary endpoint, method, query, inspect, or Docker mutation exists."""
    connection = UnixHTTP(endpoint['socket'])
    deadline = time.monotonic() + 2
    try:
        connection.request('GET', '/containers/json?all=1&limit=1001', headers={'Host': 'localhost'})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('Docker unavailable')
        chunks, size = [], 0
        while not response.isclosed() and response.length != 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Docker deadline')
            if response.fp is not None:
                response.fp.raw._sock.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_DOCKER_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_DOCKER_BYTES:
                raise ValueError('Oversized Docker response')
        if response.length not in (0, None):
            raise ValueError('Truncated Docker response')
        body = b''.join(chunks)
        rows = json.loads(body)
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValueError('Invalid Docker response')
        results, unknown = [], 0
        for entry in rows:
            if not isinstance(entry, dict) or not CID.fullmatch(str(entry.get('Id', ''))):
                raise ValueError('Invalid container identity')
            # Only these metadata fields are projected. Never retain the raw row.
            cid, labels = entry['Id'], entry.get('Labels') or {}
            names, state = entry.get('Names') or [], entry.get('State')
            if not isinstance(labels, dict) or not isinstance(names, list):
                raise ValueError('Invalid container metadata')
            category, kind, ident = None, None, None
            if endpoint['kind'] == 'rootless':
                app = labels.get('pockethost.web.app', '')
                if labels.get('pockethost.web') == 'true' and isinstance(app, str) and ID.fullmatch(app):
                    category, kind, ident = 'hosting', 'application', app
                elif any(name == '/buildx_buildkit_ph-web-builder0' for name in names):
                    category, kind, ident = 'builds', 'builder', 'builder'
            else:
                native = next((x[1:] for x in names if isinstance(x, str) and x.startswith('/') and ID.fullmatch(x[1:])), None)
                target = labels.get('kolab.db-target.instance', '')
                if native:
                    category, kind, ident = 'databases', 'database', native
                elif labels.get('kolab.db-target.role') == 'target' and isinstance(target, str) and ID.fullmatch(target):
                    category, kind, ident = 'databases', 'database', target
            if category:
                results.append({'cid': cid, 'id': ident, 'category': category, 'kind': kind,
                                'state': 'running' if state == 'running' else 'stopped' if state in ('exited', 'created', 'dead') else 'unknown'})
            elif state == 'running':
                unknown += 1
        return results, unknown
    finally:
        connection.close()


def discover_scopes(root, wanted, max_dirs=30000):
    """Read the container scope, never its /init child or the enclosing user@ unit."""
    result, stack, visited = {}, [root], 0
    while stack:
        path = stack.pop()
        visited += 1
        if visited > max_dirs:
            raise ValueError('Cgroup discovery bounded')
        try:
            for entry in os.scandir(path):
                if not entry.is_dir(follow_symlinks=False):
                    continue
                match = DOCKER_SCOPE.fullmatch(entry.name)
                if match:
                    if match.group(1) in wanted:
                        result[match.group(1)] = pathlib.Path(entry.path)
                    continue  # no walk into build/run descendants
                if len(pathlib.Path(entry.path).relative_to(root).parts) <= 16:
                    stack.append(pathlib.Path(entry.path))
        except FileNotFoundError:
            continue
    return result


def nonoverlapping(items):
    """Keep specific accounting roots once; reject parents and duplicate paths."""
    paths = [item['path'] for item in items]
    accepted, rejected = [], []
    for item in items:
        path = item['path']
        if any(path != other and path in other.parents for other in paths):
            rejected.append(item)
        elif any(other['path'] == path for other in accepted):
            rejected.append(item)
        else:
            accepted.append(item)
    return accepted, rejected


def cgroup_counters(path):
    info = path.stat()
    cpu = key_values(bounded_text(path / 'cpu.stat', 8192))['usage_usec']
    current = numeric_file(path / 'memory.current')
    inactive = key_values(bounded_text(path / 'memory.stat', 32768)).get('inactive_file', 0)
    limit = numeric_file(path / 'memory.max')
    if cpu < 0 or current is None or current < 0 or inactive < 0:
        raise ValueError('Invalid cgroup counters')
    return {'identity': (info.st_dev, info.st_ino), 'usage': cpu,
            'memory': max(0, current - min(current, inactive)), 'limit': limit}


def cpu_attribution_uncertainty(total_ticks, cpu_count, group_count):
    """Diagnostic budget only; never modifies a host/category measurement.

    /proc/stat's eight CPU time buckets are tick-accounted across CPUs, whereas
    cgroup usage has microsecond precision and is read separately. Conservatively
    allow one tick per CPU/bucket plus the rounding of each group and host value.
    The budget shrinks over longer windows and never exceeds 0.5 host CPU points.
    A negative system residual stays unknown even inside this uncertainty.
    """
    if total_ticks is None or total_ticks <= 0 or cpu_count <= 0:
        return 0.0
    tick_budget = 100 * 8 * cpu_count / total_ticks
    rounding_budget = .0005 * (group_count + 1)
    return min(.5, tick_budget + rounding_budget)


def filesystem_usage(entries):
    filesystems, devices, errors = [], set(), []
    for entry in entries:
        try:
            info = os.stat(entry['path'])
            if info.st_dev in devices:
                continue
            fs = os.statvfs(entry['path'])
            total, used, available = fs.f_blocks * fs.f_frsize, (fs.f_blocks - fs.f_bfree) * fs.f_frsize, fs.f_bavail * fs.f_frsize
            if total <= 0 or min(used, available) < 0:
                raise ValueError('Invalid filesystem counters')
            devices.add(info.st_dev)
            filesystems.append({'mount': entry['mount'], 'totalBytes': total, 'usedBytes': used,
                                'availableBytes': available, 'usedPercent': round(100 * used / total, 3)})
        except (OSError, ValueError):
            errors.append('filesystem_unavailable')
    if not filesystems:
        raise ValueError('No persistent filesystem available')
    return filesystems, errors


def scan_disk(roots, max_entries=5000000, deadline_seconds=240):
    """One walk per physical tree, nested ownership overrides, allocated blocks.

    Symlinks are never followed. Hardlinks count once across categories. Shared
    Docker layers are deliberately charged to the configured Docker root once;
    nested BuildKit state/cache can have its own category. Reflinks can make this
    an estimate; the caller reports attribution errors rather than fake residue.
    """
    totals = {category: 0 for category in CATEGORIES if category != 'system'}
    if not roots:
        return {'categories': {category: None for category in totals}, 'resources': {},
                'collectedAt': iso(), 'errors': ['disk_attribution_unconfigured']}
    per_resource, failures, canonical, top = {}, set(), {}, []
    visited_dirs, hardlinks = set(), set()
    end, count = time.monotonic() + deadline_seconds, 0
    for root in roots:
        path = pathlib.Path(root['path'])
        try:
            if path.is_symlink() or not path.is_dir():
                raise ValueError('Invalid disk root')
            path = path.resolve(strict=True)
            if str(path) in canonical:
                raise ValueError('Duplicate disk root')
            canonical[str(path)] = root
        except (OSError, ValueError):
            failures.add(root['category'])
    paths = sorted(canonical, key=lambda value: (len(pathlib.Path(value).parts), value))
    # Include explicit roots even below an intervening unscanned mount. The
    # directory inode set prevents duplicate walks when a parent reaches them.
    top = paths
    stack = []
    for path in reversed(top):
        try:
            stack.append((path, canonical[path], os.stat(path).st_dev))
        except OSError:
            failures.add(canonical[path]['category'])
    while stack:
        path, owner, device = stack.pop()
        count += 1
        if count > max_entries or time.monotonic() > end:
            raise TimeoutError('Disk scan bounded')
        owner = canonical.get(path, owner)
        try:
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                continue
            # Explicit nested roots may cross a device; other mounts are skipped.
            if info.st_dev != device:
                if path not in canonical:
                    continue
                device = info.st_dev
            identity = (info.st_dev, info.st_ino)
            if stat.S_ISDIR(info.st_mode):
                if identity in visited_dirs:
                    continue
                visited_dirs.add(identity)
            elif not stat.S_ISREG(info.st_mode):
                continue
            elif info.st_nlink > 1:
                if identity in hardlinks:
                    continue
                hardlinks.add(identity)
            size = info.st_blocks * 512
            totals[owner['category']] += size
            if owner.get('resourceId'):
                key = owner['category'] + ':' + owner['resourceId']
                per_resource[key] = per_resource.get(key, 0) + size
            if stat.S_ISDIR(info.st_mode):
                with os.scandir(path) as entries:
                    for entry in entries:
                        stack.append((entry.path, owner, device))
                        if len(stack) > 100000 or len(visited_dirs) > 500000 or len(hardlinks) > 500000:
                            raise TimeoutError('Disk scan memory bounded')
        except OSError:
            failures.add(owner['category'])
    for category in failures:
        totals[category] = None
    if failures:
        # A partially counted resource must not masquerade as a complete value.
        per_resource = {key: value for key, value in per_resource.items() if key.split(':', 1)[0] not in failures}
    return {'categories': totals, 'resources': per_resource, 'collectedAt': iso(),
            'errors': ['disk_scan_incomplete'] if failures else []}


def disk_child(roots, sender):
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        os.nice(19)
        # Linux ioprio_set(IOPRIO_WHO_PROCESS, 0, IOPRIO_CLASS_IDLE << 13).
        # The service also uses IOSchedulingClass=idle; no executable is spawned.
        result = scan_disk(roots)
        sender.send({'ok': True, 'result': result})
    except Exception:
        sender.send({'ok': False})
    finally:
        sender.close()


class DiskCache:
    def __init__(self, roots):
        self.roots, self.result, self.process, self.receiver = roots, None, None, None
        self.started, self.last_attempt, self.last_success, self.failed = 0, -math.inf, -math.inf, False

    def poll(self, now):
        if self.process is not None:
            if self.receiver.poll():
                try:
                    message = self.receiver.recv()
                    self.failed = not message.get('ok', False)
                    if not self.failed:
                        self.result = message['result']
                        self.last_success = now
                except (EOFError, OSError):
                    self.failed = True
                self._close()
            elif not self.process.is_alive() or now - self.started > 250:
                self.failed = True
                self._close()
        if self.process is None and now - self.last_attempt >= 300:
            context = multiprocessing.get_context('fork')
            self.receiver, sender = context.Pipe(duplex=False)
            self.process = context.Process(target=disk_child, args=(self.roots, sender), daemon=True)
            self.process.start()
            sender.close()
            self.started = self.last_attempt = now
        errors = []
        if self.result is None:
            errors.append('disk_attribution_pending' if not self.failed else 'disk_scan_unavailable')
        elif self.failed or now - self.last_success > 600:
            errors.append('disk_attribution_stale')
        return self.result, errors

    def _close(self):
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=2)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=1)
        self.receiver.close()
        self.process = self.receiver = None

    def close(self):
        if self.process is not None:
            self._close()


def atomic_json(path, value, group_id=None):
    temporary = path.with_name('.' + path.name + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o640)
        if group_id is not None:
            os.fchown(descriptor, 0, group_id)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, separators=(',', ':'), allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class History:
    def __init__(self, path):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA secure_delete=ON')
        columns = ['timestamp INTEGER PRIMARY KEY', 'cpu REAL', 'memory INTEGER', 'disk INTEGER']
        columns.extend(category + '_' + metric + ' REAL' for category in CATEGORIES for metric in ('cpu', 'memory', 'disk'))
        self.db.execute('CREATE TABLE IF NOT EXISTS samples (' + ','.join(columns) + ')')
        self.db.commit()

    def append(self, sample, timestamp):
        categories = {row['id']: row for row in sample['categories']} if sample else {}
        values = [timestamp, sample['host']['cpuPercent'] if sample else None,
                  sample['host']['memoryUsedBytes'] if sample else None,
                  sample['host']['diskUsedBytes'] if sample else None]
        values.extend(categories.get(category, {}).get(metric) for category in CATEGORIES for metric in METRICS)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO samples VALUES (' + ','.join('?' for _ in values) + ')', values)
            self.db.execute('DELETE FROM samples WHERE timestamp < ?', (timestamp - RETENTION * 1000,))
            self.db.execute('DELETE FROM samples WHERE timestamp NOT IN (SELECT timestamp FROM samples ORDER BY timestamp DESC LIMIT ?)', (MAX_ROWS,))

    def view(self, range_id, now_ms):
        duration = RANGES[range_id]
        interval = max(INTERVAL, math.ceil(duration / 240 / INTERVAL) * INTERVAL)
        width = interval * 1000
        # At most 240 wall-clock aligned buckets, with no invented zero samples.
        last_bucket = now_ms // width
        first_bucket = last_bucket - min(240, math.ceil(duration / interval)) + 1
        names = ['cpu', 'memory', 'disk'] + [category + '_' + metric for category in CATEGORIES for metric in ('cpu', 'memory', 'disk')]
        expression = ','.join('AVG(' + name + ')' for name in names)
        rows = self.db.execute('SELECT CAST(timestamp / ? AS INTEGER), ' + expression +
                               ' FROM samples WHERE timestamp >= ? AND timestamp <= ? GROUP BY 1 ORDER BY 1',
                               (width, first_bucket * width, now_ms)).fetchall()
        points = []
        for row in rows:
            def value(index):
                return None if row[index] is None else round(row[index], 3)
            points.append({'timestamp': int(row[0]) * width, 'cpuPercent': value(1), 'memoryBytes': value(2), 'diskBytes': value(3),
                           'categories': {category: {metric: value(4 + number * 3 + position) for position, metric in enumerate(METRICS)}
                                          for number, category in enumerate(CATEGORIES)}})
        first = self.db.execute('SELECT MIN(timestamp) FROM samples').fetchone()[0]
        return {'points': points, 'firstCollectedAt': iso(first / 1000) if first is not None else None,
                'intervalSeconds': interval, 'retentionDays': 7}

    def close(self):
        self.db.close()


class Collector:
    def __init__(self, config, proc='/proc', cgroup='/sys/fs/cgroup', docker_reader=docker_resources):
        self.config, self.proc, self.cgroup = config, pathlib.Path(proc), pathlib.Path(cgroup)
        self.docker_reader, self.previous_host, self.previous_cgroups = docker_reader, None, {}

    def sample(self, disk=None, disk_errors=None, now=None):
        fixed_clock = now is not None
        now = time.monotonic() if now is None else now
        errors = list(disk_errors or [])
        counters = host_counters(self.proc)
        host_cpu = None
        host_delta_ticks = None
        same_cores = self.previous_host is not None and self.previous_host['cpuCount'] == counters['cpuCount']
        if same_cores:
            total, idle = (counters['cpu'][i] - self.previous_host['cpu'][i] for i in range(2))
            if total > 0 and 0 <= idle <= total:
                host_cpu = round(100 * (total - idle) / total, 3)
                host_delta_ticks = total
        self.previous_host = counters
        filesystems, fs_errors = filesystem_usage(self.config['filesystems'])
        errors += fs_errors
        host = {key: value for key, value in counters.items() if key != 'cpu'}
        host.update({'hostname': self.config['hostname'], 'cpuPercent': host_cpu,
                     'diskTotalBytes': sum(row['totalBytes'] for row in filesystems),
                     'diskUsedBytes': sum(row['usedBytes'] for row in filesystems),
                     'diskAvailableBytes': sum(row['availableBytes'] for row in filesystems)})
        container_rows, failed_categories = [], set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [(endpoint, pool.submit(self.docker_reader, endpoint)) for endpoint in self.config['docker']]
            for endpoint, future in futures:
                try:
                    rows, unknown = future.result()
                    container_rows.extend(rows)
                    if unknown:
                        errors.append('unclassified_containers')
                except Exception:
                    errors.append('docker_' + endpoint['kind'] + '_unavailable')
                    failed_categories.update(('databases',) if endpoint['kind'] == 'rootful' else ('hosting', 'builds'))
        try:
            scopes = discover_scopes(self.cgroup, {row['cid'] for row in container_rows if row['state'] == 'running'})
        except (OSError, ValueError):
            scopes = {}
            errors.append('cgroup_discovery_unavailable')
        groups, resources = [], {}
        for row in container_rows:
            key = (row['category'], row['id'])
            resource = resources.setdefault(key, {'id': row['id'], 'category': row['category'], 'kind': row['kind'],
                                                   'state': row['state'], 'cpuPercent': 0, 'memoryBytes': 0,
                                                   'memoryLimitBytes': 0, 'diskBytes': None})
            if row['state'] == 'unknown':
                resource['cpuPercent'] = resource['memoryBytes'] = resource['memoryLimitBytes'] = None
                failed_categories.add(row['category'])
            if row['state'] == 'running':
                resource['state'] = 'running'
                path = scopes.get(row['cid'])
                if path:
                    groups.append({**row, 'path': path, 'key': key})
                else:
                    resource['cpuPercent'] = resource['memoryBytes'] = resource['memoryLimitBytes'] = None
                    failed_categories.add(row['category'])
                    errors.append('container_cgroup_unavailable')
        for unit in self.config['services']:
            key = (unit['category'], unit['id'])
            path = self.cgroup / unit['cgroup']
            resources[key] = {'id': unit['id'], 'category': unit['category'], 'kind': 'service',
                              'state': 'unknown', 'cpuPercent': None, 'memoryBytes': None,
                              'memoryLimitBytes': None, 'diskBytes': None}
            if path.is_dir():
                resources[key]['state'] = 'running'
                resources[key]['cpuPercent'] = resources[key]['memoryBytes'] = resources[key]['memoryLimitBytes'] = 0
                groups.append({**unit, 'path': path, 'key': key})
            else:
                failed_categories.add(unit['category'])
                errors.append('service_cgroup_unavailable')
        groups, rejected = nonoverlapping(groups)
        if rejected:
            errors.append('overlapping_cgroup_rejected')
            for group in rejected:
                failed_categories.add(group['category'])
                resources[group['key']]['cpuPercent'] = resources[group['key']]['memoryBytes'] = None
        next_counters = {}
        for group in groups:
            resource = resources[group['key']]
            try:
                value = cgroup_counters(group['path'])
                value['readTime'] = now if fixed_clock else time.monotonic()
                old = self.previous_cgroups.get(str(group['path']))
                cpu = None
                period = value['readTime'] - old['readTime'] if old else None
                if same_cores and old and period and period > 0 and old['identity'] == value['identity'] and value['usage'] >= old['usage']:
                    cpu = min(100.0, round(100 * (value['usage'] - old['usage']) / (period * 1000000 * counters['cpuCount']), 3))
                next_counters[str(group['path'])] = value
                resource['cpuPercent'] = None if cpu is None or resource['cpuPercent'] is None else resource['cpuPercent'] + cpu
                resource['memoryBytes'] = None if resource['memoryBytes'] is None else resource['memoryBytes'] + value['memory']
                resource['memoryLimitBytes'] = None if value['limit'] is None or resource['memoryLimitBytes'] is None else resource['memoryLimitBytes'] + value['limit']
            except (OSError, ValueError, KeyError):
                resource['cpuPercent'] = resource['memoryBytes'] = resource['memoryLimitBytes'] = None
                failed_categories.add(group['category'])
                errors.append('cgroup_counter_unavailable')
        self.previous_cgroups = next_counters
        categories = []
        for category in CATEGORIES:
            rows = [row for row in resources.values() if row['category'] == category]
            def summed(metric):
                return None if category in failed_categories or any(row[metric] is None for row in rows) else sum(row[metric] for row in rows)
            cpu = summed('cpuPercent')
            if cpu is not None and cpu > 100:
                cpu = None
                errors.append('cpu_attribution_exceeds_host')
            categories.append({'id': category, 'resourceCount': len(rows), 'cpuPercent': cpu,
                               'memoryBytes': summed('memoryBytes'),
                               'diskBytes': disk['categories'].get(category) if disk else None})
        system = categories[-1]
        cpu_uncertainty = cpu_attribution_uncertainty(host_delta_ticks, counters['cpuCount'], len(groups))
        for metric, total in [('cpuPercent', host_cpu), ('memoryBytes', host['memoryUsedBytes'])]:
            values = [row[metric] for row in categories[:-1]]
            known = sum(value for value in values if value is not None)
            if total is None or any(value is None for value in values) or known > total:
                system[metric] = None
                if total is not None and known > total and (metric != 'cpuPercent' or known - total > cpu_uncertainty):
                    errors.append('cpu_attribution_exceeds_host' if metric == 'cpuPercent' else 'memory_attribution_exceeds_host')
            else:
                system[metric] = round(total - known, 3)
        if disk:
            errors += disk.get('errors', [])
            values = [row['diskBytes'] for row in categories[:-1]]
            known = sum(value for value in values if value is not None)
            if any(value is None for value in values) or known > host['diskUsedBytes']:
                system['diskBytes'] = None
                if known > host['diskUsedBytes']:
                    errors.append('disk_attribution_exceeds_host')
            else:
                system['diskBytes'] = host['diskUsedBytes'] - known
            for row in resources.values():
                row['diskBytes'] = disk['resources'].get(row['category'] + ':' + row['id'])
        return {'version': 1, 'collectedAt': iso(), 'diskCollectedAt': disk['collectedAt'] if disk else None,
                'diskAttribution': 'directory-estimate', 'host': host, 'categories': categories,
                'resources': sorted(resources.values(), key=lambda row: (row['category'], row['id'])),
                'filesystems': filesystems, 'errors': sorted(set(errors))}


def load_config(path, trusted=True):
    path = pathlib.Path(path)
    if trusted:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('Configuration must be root-owned 0600 regular file')
        for parent in path.parents:
            info = parent.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('Unsafe configuration directory')
    config = json.loads(bounded_text(path, 128 * 1024))
    if set(config) != {'version', 'hostname', 'outputDir', 'readGroup', 'docker', 'services', 'filesystems', 'diskRoots'} or config['version'] != 1:
        raise ValueError('Invalid collector configuration')
    if not isinstance(config['hostname'], str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,199}', config['hostname']):
        raise ValueError('Invalid curated hostname')
    def absolute(value):
        return isinstance(value, str) and value.startswith('/') and '\0' not in value and '..' not in pathlib.PurePath(value).parts and len(value) <= 4096
    if not absolute(config['outputDir']) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', str(config['readGroup'])):
        raise ValueError('Invalid output configuration')
    if not isinstance(config['docker'], list) or len(config['docker']) > 2:
        raise ValueError('Invalid Docker configuration')
    kinds = set()
    for endpoint in config['docker']:
        if (set(endpoint) != {'kind', 'socket'} or endpoint['kind'] not in ('rootless', 'rootful')
                or endpoint['kind'] in kinds or not absolute(endpoint['socket'])):
            raise ValueError('Invalid Docker endpoint')
        kinds.add(endpoint['kind'])
    if not isinstance(config['services'], list) or len(config['services']) > 64:
        raise ValueError('Invalid services')
    ids, cgroups = set(), set()
    for service in config['services']:
        cg = pathlib.PurePosixPath(service.get('cgroup', ''))
        if (set(service) != {'id', 'category', 'cgroup'} or not RESOURCE_ID.fullmatch(str(service['id']))
                or service['id'] in ids or str(cg) in cgroups or service['category'] not in ('platform', 'backups', 'builds')
                or cg.is_absolute() or '..' in cg.parts or not cg.parts
                or any(not re.fullmatch(r'[A-Za-z0-9_@.:-]+', part) or part == '.' for part in cg.parts)
                or not cg.name.endswith('.service') or re.fullmatch(r'user@\d+\.service', cg.name)):
            raise ValueError('Invalid service accounting root')
        ids.add(service['id'])
        cgroups.add(str(cg))
    if not isinstance(config['filesystems'], list) or not 1 <= len(config['filesystems']) <= 16:
        raise ValueError('Invalid persistent filesystems')
    for fs in config['filesystems']:
        if set(fs) != {'path', 'mount'} or not absolute(fs['path']) or not re.fullmatch(r'/[a-zA-Z0-9/_-]{0,63}', str(fs['mount'])):
            raise ValueError('Invalid filesystem')
    if not isinstance(config['diskRoots'], list) or len(config['diskRoots']) > 128:
        raise ValueError('Invalid disk roots')
    for root in config['diskRoots']:
        if (not {'path', 'category'} <= set(root) or set(root) - {'path', 'category', 'resourceId'}
                or not absolute(root['path']) or root['path'] == '/' or root['category'] not in CATEGORIES[:-1]
                or ('resourceId' in root and not RESOURCE_ID.fullmatch(str(root['resourceId'])))):
            raise ValueError('Invalid disk attribution root')
    return config


def run(config):
    if os.geteuid() != 0:
        raise ValueError('Collector requires root-owned system counters')
    output = pathlib.Path(config['outputDir'])
    group = grp.getgrnam(config['readGroup']).gr_gid
    output.mkdir(mode=0o750, parents=True, exist_ok=True)
    info = output.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Unsafe output directory')
    for parent in output.parents:
        info = parent.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unsafe output parent directory')
    os.chown(output, 0, group)
    os.chmod(output, 0o750)
    lock = os.open(output / '.collector.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    history = History(output / 'history.sqlite3')
    collector, disks = Collector(config), DiskCache(config['diskRoots'])
    stop = False
    def stopping(_signum, _frame):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, stopping)
    signal.signal(signal.SIGINT, stopping)
    try:
        deadline = time.monotonic()
        while not stop:
            result, errors = disks.poll(time.monotonic())
            try:
                sample = collector.sample(result, errors)
            except Exception:
                sample = None  # old cache freshness is checked independently by API
            now_ms = int(time.time() * 1000)
            history.append(sample, now_ms)
            for range_id in RANGES:
                atomic_json(output / ('resources-' + range_id + '.json'),
                            {'available': sample is not None, 'stale': False, 'range': range_id,
                             'sample': sample, 'history': history.view(range_id, now_ms)}, group)
            deadline += INTERVAL
            if deadline < time.monotonic():
                deadline = time.monotonic() + INTERVAL
            while not stop and time.monotonic() < deadline:
                time.sleep(min(1, deadline - time.monotonic()))
    finally:
        disks.close()
        history.close()
        os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--check-config', action='store_true')
    args = parser.parse_args()
    config = load_config(args.config)
    if args.check_config:
        print('Resource collector configuration valid')
    else:
        run(config)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Never stringify an exception: paths/socket details are private.
        print('Resource collector failed', file=sys.stderr)
        sys.exit(1)
