#!/usr/bin/env python3
"""Read-only, fixed-peer Kolab fleet inventory.

`fetch` is invoked by the local authenticated controller, `serve` exclusively by
the restricted SSH key. Neither mode launches Docker, wakes a tenant or reads
application configuration, passwords, tokens, environment values or log bodies.
"""
import base64
import concurrent.futures
import contextlib
import datetime
import json
import math
import os
import pathlib
import re
import resource
import signal
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time

PEERS = {
    'app3': {'address': '135.125.67.39', 'hostname': 'app3.monappli.re'},
    'app4': {'address': '51.255.69.154', 'hostname': 'app4.monappli.re'},
    'app5': {'address': '217.182.134.72', 'hostname': 'app5.monappli.re'},
}
CONFIG = pathlib.Path('/etc/kolab/fleet/config.json')
ROOT = CONFIG.parent
DATABASE = pathlib.Path('/home/ubuntu/.local/share/pockethost/data/mothership/pb_data/data.db')
RESOURCES = pathlib.Path('/var/lib/kolab-resource-monitoring/resources-15m.json')
MONITORING = pathlib.Path('/home/ubuntu/.local/share/pockethost/data/monitoring/status.json')
LIMIT = 500
MAX_OUTPUT = 768 * 1024
LOCAL_SOCKET = pathlib.Path('/run/kolab-fleet-reader.sock')
STATES = ('ok', 'warning', 'error', 'unknown', 'paused')


class AccessDenied(Exception):
    pass


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')


def valid_email(value):
    return isinstance(value, str) and len(value) <= 254 and re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", value
    ) is not None


def request(value):
    if not isinstance(value, dict) or set(value) != {'version', 'email'} or type(value['version']) is not int or value['version'] != 1 or not valid_email(value['email']):
        raise ValueError('Invalid request')
    return value['email'].lower()


def trusted_file(path, maximum, mode=0o022):
    """No symlink following; all mutable trust files belong to root."""
    path = pathlib.Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & mode or info.st_size > maximum:
            raise ValueError('Untrusted file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            value = stream.read(maximum + 1)
        if len(value) > maximum:
            raise ValueError('Oversized file')
        return value
    finally:
        os.close(fd)


def public_key(value):
    if not isinstance(value, str) or len(value) > 300 or '\n' in value or '\r' in value:
        raise ValueError('Invalid host key')
    parts = value.split()
    if len(parts) not in (2, 3) or parts[0] != 'ssh-ed25519':
        raise ValueError('Invalid host key')
    try:
        raw = base64.b64decode(parts[1], validate=True)
        if len(raw) != 51 or raw[:19] != b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20':
            raise ValueError('Invalid host key')
    except Exception as exc:
        raise ValueError('Invalid host key') from exc
    return parts[0] + ' ' + parts[1]


def configuration():
    raw = json.loads(trusted_file(CONFIG, 8192, 0o077))
    if not isinstance(raw, dict) or set(raw) != {'version', 'self', 'peers'} or type(raw['version']) is not int or raw['version'] != 1 or raw['self'] not in PEERS:
        raise ValueError('Invalid fleet configuration')
    if not isinstance(raw['peers'], list) or len(raw['peers']) > 2 or len(set(raw['peers'])) != len(raw['peers']):
        raise ValueError('Invalid fleet peers')
    if any(peer not in PEERS or peer == raw['self'] for peer in raw['peers']):
        raise ValueError('Invalid fleet peers')
    for peer in raw['peers']:
        trusted_file(ROOT / 'keys' / peer, 8192, 0o077)
    trusted_file(ROOT / 'known_hosts', 8192, 0o077)
    return raw


def cached_json(path):
    """Collector caches are data only, not trust or execution configuration."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024 * 1024:
            raise ValueError('Invalid cache')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            value = stream.read(4 * 1024 * 1024 + 1)
        if len(value) > 4 * 1024 * 1024:
            raise ValueError('Invalid cache')
        return json.loads(value)
    finally:
        os.close(fd)


def finite(value, maximum=2**53 - 1):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum else None


def date(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.astimezone(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
    except ValueError:
        return None


def old(value, seconds, now):
    normalized = date(value)
    if not normalized:
        return True
    age = now - datetime.datetime.fromisoformat(normalized.replace('Z', '+00:00')).timestamp()
    return age > seconds or age < -30


def metric_cache(path=RESOURCES, now=None):
    try:
        raw = cached_json(path)
        sample = raw['sample']
        host = sample['host']
        if raw.get('available') is not True or sample.get('version') != 1:
            raise ValueError('Missing metrics')
        fields = ('cpuPercent', 'memoryTotalBytes', 'memoryUsedBytes', 'diskTotalBytes', 'diskUsedBytes')
        values = {key: finite(host.get(key), 100 if key == 'cpuPercent' else 2**53 - 1) for key in fields}
        collected = date(sample.get('collectedAt'))
        rows = sample.get('resources', [])
        if not isinstance(rows, list) or len(rows) > 4096:
            raise ValueError('Invalid metrics')
        measures = {}
        for row in rows:
            if isinstance(row, dict) and row.get('kind') in ('database', 'application') and isinstance(row.get('id'), str) and re.fullmatch(r'[a-z0-9]{15}', row['id']):
                measures[(row['kind'], row['id'])] = {
                    'cpuPercent': finite(row.get('cpuPercent'), 100),
                    'memoryBytes': finite(row.get('memoryBytes')),
                    'diskBytes': finite(row.get('diskBytes')),
                }
        return {'collectedAt': collected, 'stale': raw.get('stale') is True or old(collected, 60, time.time() if now is None else now), **values}, measures
    except Exception:
        return None, {}


def monitoring_cache(path=MONITORING, now=None):
    try:
        raw = cached_json(path)
        checks = raw['checks']
        if raw.get('version') != 1 or not isinstance(checks, list) or len(checks) > 10000:
            raise ValueError('Invalid monitoring')
        counts = {state: 0 for state in STATES}
        for row in checks:
            if not isinstance(row, dict) or row.get('state') not in STATES:
                raise ValueError('Invalid monitoring')
            counts[row['state']] += 1
        return {'checkedAt': date(raw.get('checkedAt')), 'stale': old(raw.get('checkedAt'), 180, time.time() if now is None else now), 'state': next((s for s in ('error', 'warning', 'unknown') if counts[s]), 'ok'), 'counts': counts}
    except Exception:
        return None


def local_snapshot(email, name, database=DATABASE, resources=RESOURCES, monitoring=MONITORING):
    """Each read revalidates the remote/local actor. Never query secret columns."""
    if name not in PEERS or not valid_email(email):
        raise ValueError('Invalid actor')
    with contextlib.closing(sqlite3.connect(pathlib.Path(database).as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        actor = db.execute('SELECT id FROM users WHERE lower(email)=? AND verified=1 AND superAdmin=1 AND coalesce(suspension,\'\')=\'\' LIMIT 1', (email.lower(),)).fetchone()
        if actor is None:
            raise AccessDenied()
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {'databases': 0, 'applications': 0, 'databasesRunning': 0, 'applicationsRunning': 0, 'deploymentsRunning': 0, 'deploymentsQueued': 0, 'deploymentsFailed24h': 0}
        inventory = []
        for table, kind, label in [('instances', 'database', 'databases'), ('web_apps', 'application', 'applications')]:
            if table not in tables:
                continue
            counts[label] = db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
            counts[label + 'Running'] = db.execute('SELECT count(*) FROM ' + table + ' WHERE status=\'running\'').fetchone()[0]
            # Names only: never serialize PocketBase records, config or environment.
            for ident, title, status in db.execute('SELECT id,' + ('subdomain' if kind == 'database' else 'name') + ',status FROM ' + table + ' ORDER BY id LIMIT ?', (max(0, LIMIT - len(inventory)),)):
                if not re.fullmatch(r'[a-z0-9]{15}', str(ident)):
                    continue
                inventory.append({'id': ident, 'kind': kind, 'name': str(title or ('Base' if kind == 'database' else 'Application'))[:200], 'status': str(status or 'unknown')[:40]})
        if 'web_deployments' in tables:
            for state in ('running', 'queued'):
                counts['deployments' + state.capitalize()] = db.execute('SELECT count(*) FROM web_deployments WHERE status=?', (state,)).fetchone()[0]
            counts['deploymentsFailed24h'] = db.execute("SELECT count(*) FROM web_deployments WHERE status='failed' AND finishedAt>=?", ((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'),)).fetchone()[0]
        db.rollback()
    metrics, measures = metric_cache(resources)
    for row in inventory:
        row.update(measures.get((row['kind'], row['id']), {'cpuPercent': None, 'memoryBytes': None, 'diskBytes': None}))
    return {'key': name, 'hostname': PEERS[name]['hostname'], 'access': 'granted', 'checkedAt': timestamp(), 'metrics': metrics, 'monitoring': monitoring_cache(monitoring), 'counts': counts, 'inventory': inventory, 'truncated': counts['databases'] + counts['applications'] > len(inventory)}


def unavailable(name, code):
    return {'key': name, 'hostname': PEERS[name]['hostname'], 'access': code, 'checkedAt': timestamp()}


def controller_snapshot(email, name):
    """SQLite WAL bookkeeping must never be created with root ownership.

    The root wrapper reads only trust files/SSH keys. Read controller data under
    its actual service UID, before any peer worker threads exist, without
    changing database permissions. Restore root only for the outbound SSH keys.
    """
    info = DATABASE.lstat()
    if os.geteuid() != 0 or info.st_uid != 1000 or not stat.S_ISREG(info.st_mode) or threading.active_count() != 1:
        raise ValueError('Unexpected controller identity')
    group, groups = os.getegid(), os.getgroups()
    try:
        os.setgroups([])
        os.setegid(1000)
        os.seteuid(1000)
        return local_snapshot(email, name)
    finally:
        os.seteuid(0)
        os.setegid(group)
        os.setgroups(groups)


def remote_snapshot(peer, email):
    # Fixed host/user/arguments and a root-owned key per peer. No shell, proxies,
    # agent forwarding, arbitrary SSH command, password or trust-on-first-use.
    args = ['/usr/bin/ssh', '-T', '-F', '/dev/null', '-i', str(ROOT / 'keys' / peer),
            '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(ROOT / 'known_hosts'),
            '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'HostKeyAlgorithms=ssh-ed25519',
            '-o', 'UpdateHostKeys=no', '-o', 'PreferredAuthentications=publickey',
            '-o', 'ProxyCommand=none', '-o', 'ProxyJump=none',
            '-o', 'ConnectTimeout=3', '-o', 'ConnectionAttempts=1', '-o', 'ForwardAgent=no',
            '-o', 'ClearAllForwardings=yes', '-o', 'ServerAliveInterval=2', '-o', 'ServerAliveCountMax=1',
            'kolab-fleet@' + PEERS[peer]['address']]
    try:
        with tempfile.TemporaryFile() as output:
            child = subprocess.run(args, input=json.dumps({'version': 1, 'email': email}).encode(), stdout=output, stderr=subprocess.DEVNULL, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, timeout=8, check=False)
            output.seek(0)
            content = output.read(MAX_OUTPUT + 1)
        if child.returncode != 0 or len(content) > MAX_OUTPUT:
            raise ValueError('Peer unavailable')
        raw = json.loads(content)
        if raw.get('key') != peer or raw.get('hostname') != PEERS[peer]['hostname'] or raw.get('access') not in ('granted', 'denied', 'unavailable'):
            raise ValueError('Invalid peer result')
        return raw
    except Exception:
        return unavailable(peer, 'unavailable')


def fetch(email, config):
    # Local account can have been suspended between native auth and invocation.
    local = controller_snapshot(email, config['self'])
    result = {config['self']: local}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(remote_snapshot, peer, email): peer for peer in config['peers']}
        for future, peer in [(future, peer) for future, peer in futures.items()]:
            result[peer] = future.result()
    return {'version': 1, 'checkedAt': timestamp(), 'self': config['self'], 'servers': [result.get(peer, unavailable(peer, 'notConfigured')) for peer in PEERS]}


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024**2, 256 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT, MAX_OUTPUT))
    signal.alarm(12)


def peer_uid(connection):
    if hasattr(socket, 'SO_PEERCRED'):
        return struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))[1]
    if hasattr(connection, 'getpeereid'):
        return connection.getpeereid()[0]
    raise ValueError('Peer credentials unavailable')


def receive(connection, maximum):
    content = bytearray()
    while True:
        chunk = connection.recv(min(8192, maximum + 1 - len(content)))
        if not chunk:
            return bytes(content)
        content.extend(chunk)
        if len(content) > maximum:
            raise ValueError('Oversized socket message')


def local_request(content):
    """Unprivileged client: NoNewPrivileges stays enabled in the controller."""
    if os.geteuid() != 1000 or len(content) > 4096:
        raise PermissionError('Controller identity required')
    email = request(json.loads(content))
    info = LOCAL_SOCKET.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0 or info.st_gid != 1000 or stat.S_IMODE(info.st_mode) != 0o660:
        raise ValueError('Untrusted reader socket')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(12)
        connection.connect(str(LOCAL_SOCKET))
        if peer_uid(connection) != 0:
            raise PermissionError('Root reader required')
        connection.sendall(json.dumps({'version': 1, 'email': email}).encode())
        connection.shutdown(socket.SHUT_WR)
        value = json.loads(receive(connection, MAX_OUTPUT))
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('Invalid reader response')
    return value


def socket_content():
    """One systemd-accepted connection, authenticated by kernel UID, no command selection."""
    with socket.socket(fileno=os.dup(0)) as connection:
        if connection.family != socket.AF_UNIX or peer_uid(connection) != 1000:
            raise PermissionError('Controller peer required')
        connection.settimeout(3)
        return receive(connection, 4096)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == 'request':
        limits()
        print(json.dumps(local_request(sys.argv[2]), separators=(',', ':'), ensure_ascii=False))
        return
    if os.geteuid() != 0:
        raise PermissionError('Root helper required')
    limits()
    if len(sys.argv) not in (2, 3) or sys.argv[1] not in ('serve', 'fetch', 'socket'):
        raise ValueError('Invalid command')
    config = configuration()
    if sys.argv[1] == 'serve':
        if len(sys.argv) != 2 or os.environ.get('SSH_ORIGINAL_COMMAND', ''):
            raise ValueError('Forced reader only')
        content = sys.stdin.buffer.read(4097)
    elif sys.argv[1] == 'socket':
        if len(sys.argv) != 2:
            raise ValueError('Invalid command')
        content = socket_content()
    else:
        if len(sys.argv) != 3:
            raise ValueError('Invalid command')
        content = sys.argv[2]
    if len(content) > 4096:
        raise ValueError('Invalid request')
    email = request(json.loads(content))
    try:
        value = controller_snapshot(email, config['self']) if sys.argv[1] == 'serve' else fetch(email, config)
    except AccessDenied:
        value = unavailable(config['self'], 'denied') if sys.argv[1] == 'serve' else {'version': 1, 'checkedAt': timestamp(), 'self': config['self'], 'servers': [unavailable(peer, 'denied') for peer in PEERS]}
    print(json.dumps(value, separators=(',', ':'), ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Never return exception text, SQL, filesystem paths or SSH output.
        print('{"error":"fleet_unavailable"}')
        sys.exit(1)
