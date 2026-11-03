"""Private database relay lifecycle on the rootless runtime engine.

Only the trusted sidecar receives the per-application Unix socket and capabilities.
The application shares its own network namespace with that sidecar, never the host.
"""
import hashlib
import contextlib
import concurrent.futures
import json
import math
import os
import pathlib
import re
import shutil
import socket as sockets
import stat
import threading
import time
import types
import urllib.request
from web_private_db_cpu import unlimited, cpu_unlimited
from web_private_db_limits import limits

ID = re.compile(r'^[a-z0-9]{15}$')
CID = re.compile(r'^[a-f0-9]{64}$')
TOKEN = re.compile(r'^[A-Za-z0-9]{64}$')
REVISION = re.compile(r'^[A-Za-z0-9]{24}$')
SERVICE = re.compile(r'^[a-z][a-z0-9-]{0,30}$')
MARKER_IDENTITY = re.compile(r'^(net:\[[0-9]+\]):[0-9]+$')
APPLICATION_IDENTITY_SCRIPT = """const fs=require('node:fs');const ns=fs.readlinkSync('/proc/self/ns/net');const s=fs.readFileSync('/proc/1/stat','utf8');const end=s.lastIndexOf(')');const ticks=s.slice(end+1).trim().split(/\\s+/)[19];if(end<0||!/^net:\\[\\d+\\]$/.test(ns)||!/^\\d+$/.test(ticks||''))throw Error('identity');process.stdout.write(ns+':'+ticks);"""
MEMORY_MIB, CPU_MILLICORES = 96, 50
PROXY_HEALTH_TIMEOUT = '5s'
STALE_SECONDS = 10
# RootlessKit copies /run when its daemon starts. New paths there are invisible
# to Docker bind mounts until a daemon restart; /var/lib stays shared.
SOCKET_ROOT = pathlib.Path('/var/lib/kolab-db-bridge')
SCRIPT_ROOT = pathlib.Path(__file__).resolve().parent
_manager = None
PROXY_STATUS_NAME = 'kolab-db-proxy-status.json'
PROXY_STATUS_COMMAND = 'test ! -L /tmp/kolab-db-proxy-status.json && test -f /tmp/kolab-db-proxy-status.json && exec /usr/bin/head -c 1025 -- /tmp/kolab-db-proxy-status.json'


def proxy_status(raw, config_hash, now=None):
    if isinstance(raw, str): raw = raw.encode('utf-8')
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 1024:
        raise RuntimeError('État privé trop volumineux ou invalide.')
    try:
        value = json.loads(raw)
        checked = value.get('checkedAt')
        age = (time.time() * 1000 if now is None else now) - checked if type(checked) in (int, float) and math.isfinite(checked) else -1
        if (value.get('version') != 1 or type(value.get('version')) is not int or value.get('ready') is not True
                or value.get('configHash') != config_hash or not 0 <= age < 6000
                or not isinstance(value.get('namespace'), str) or not re.fullmatch(r'net:\[[0-9]+\]', value['namespace'])):
            raise ValueError('status fields')
        return value
    except (ValueError, TypeError, AttributeError, OverflowError, OSError) as error:
        raise RuntimeError('État privé invalide ou périmé.') from error


def probe_bridge(app, rows, directory=SOCKET_ROOT, timeout=2):
    """Only the authenticated bridge handshake, with no HTTP/database payload."""
    if not isinstance(app, str) or not ID.fullmatch(app) or not isinstance(rows, list) or not 1 <= len(rows) <= 4:
        return False
    if any(not isinstance(row, dict) or not isinstance(row.get('id'), str) or not ID.fullmatch(row['id'])
            or not isinstance(row.get('token'), str) or not TOKEN.fullmatch(row['token']) for row in rows):
        return False
    if len({row['id'] for row in rows}) != len(rows):
        return False
    def probe(row):
        deadline = time.monotonic() + min(timeout, 2)
        try:
            with sockets.socket(sockets.AF_UNIX, sockets.SOCK_STREAM) as peer:
                peer.settimeout(max(.001, deadline - time.monotonic())); peer.connect(str(pathlib.Path(directory) / (app + '.sock')))
                peer.settimeout(max(.001, deadline - time.monotonic()))
                peer.sendall((json.dumps({'version': 1, 'bindingId': row['id'], 'token': row['token']}, separators=(',', ':')) + '\n').encode())
                response = bytearray()
                while len(response) <= 1024:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0: return False
                    peer.settimeout(remaining)
                    chunk = peer.recv(1025 - len(response))
                    if not chunk: return False
                    response.extend(chunk)
                    if len(response) > 1024: return False
                    if b'\n' in response: return response.split(b'\n', 1)[0] == b'OK'
                return False
        except (OSError, ValueError, TimeoutError):
            return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(rows)) as pool:
        return all(pool.map(probe, rows))


def enabled():
    return os.environ.get('WEB_DB_BRIDGE_ENABLED') == 'true'


def validate_bindings(value):
    if not isinstance(value, dict) or value.get('version') != 1 or type(value.get('enabled')) is not bool:
        raise ValueError('Invalid private database policy')
    applications = value.get('applications', [])
    rows = value.get('bindings', [])
    if not isinstance(applications, list) or len(applications) > 500 or any(not isinstance(x, str) or not ID.fullmatch(x) for x in applications):
        raise ValueError('Invalid application policy')
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError('Invalid binding policy')
    output, ports, identifiers = {}, set(), set()
    for row in rows:
        if (not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not ID.fullmatch(row[k]) for k in ('id', 'appId', 'instanceId'))
                or row['appId'] not in applications or not isinstance(row.get('token'), str) or not TOKEN.fullmatch(row['token'])
                or not isinstance(row.get('revision'), str) or not REVISION.fullmatch(row['revision'])
                or type(row.get('port')) is not int or not 1024 <= row['port'] <= 65535
                or type(row.get('available')) is not bool):
            raise ValueError('Invalid private database binding')
        if 'databaseRunning' in row and type(row['databaseRunning']) is not bool:
            raise ValueError('Invalid private database availability')
        key = (row['appId'], row['port'])
        if key in ports or row['id'] in identifiers:
            raise ValueError('Duplicate private database binding')
        ports.add(key); identifiers.add(row['id'])
        output.setdefault(row['appId'], []).append(dict(row))
        if len(output[row['appId']]) > 4:
            raise ValueError('Too many private database bindings')
    return output if value['enabled'] else {}


def atomic(path, value, mode=0o444):
    path = pathlib.Path(path)
    temporary = path.with_name(path.name + '.tmp')
    if path.is_symlink() or temporary.is_symlink():
        raise RuntimeError('Unsafe private database state')
    with open(temporary, 'w') as output:
        output.write(value)
    temporary.chmod(mode)
    os.replace(temporary, path)


def _directory(path, mode):
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError('Unsafe private database directory')
    path.chmod(mode)
    return path


class PrivateDatabases:
    def __init__(self, control, secret, socket, root, docker):
        self.control, self.secret, self.socket = control.rstrip('/'), secret, socket
        self.root, self.docker = pathlib.Path(root), docker
        self.folder = _directory(self.root / 'private-db-runtime', 0o700)
        self.bindings, self.last_good, self.last_fetch = {}, 0.0, 0.0
        self.connection_limits = limits()
        self.last_report, self.report_fingerprint = 0.0, ''
        self.policy_lock, self.operations_lock = threading.RLock(), threading.RLock()
        self.stop = threading.Event()
        self.recipe = hashlib.sha256((SCRIPT_ROOT / 'web_private_db_proxy.cjs').read_bytes()
            + os.environ.get('WEB_APPS_STORAGE_HELPER_IMAGE', '').encode()
            + ('\0health-timeout=' + PROXY_HEALTH_TIMEOUT).encode()).hexdigest()

    def api(self, action, body=None):
        request = urllib.request.Request(self.control + '/api/web-apps/_private-db/' + action,
            headers={'Content-Type': 'application/json', 'X-Web-Worker': self.secret},
            data=None if body is None else json.dumps(body, separators=(',', ':')).encode())
        with urllib.request.urlopen(request, timeout=3) as response:
            raw = response.read(512 * 1024 + 1)
            if len(raw) > 512 * 1024:
                raise ValueError('Private database policy too large')
            return json.loads(raw)

    def refresh(self, force=False):
        with self.policy_lock:
            now = time.monotonic()
            if not force and now - self.last_fetch < 2:
                return
            self.last_fetch = now
            raw = self.api('worker')
            policy = limits(raw.get('limits'))
            value = validate_bindings(raw)
            self.connection_limits = policy
            self.bindings, self.last_good = value, time.monotonic()

    def for_app(self, app, force=False):
        try:
            self.refresh(force)
        except Exception:
            if self.bindings.get(app):
                raise RuntimeError('Les accès privés aux bases ne peuvent pas être vérifiés. Réessayez.') from None
        with self.policy_lock:
            if time.monotonic() - self.last_good > STALE_SECONDS:
                return []
            return [dict(row) for row in self.bindings.get(app, [])]

    def paths(self, app, deployment, service):
        if not ID.fullmatch(app) or not ID.fullmatch(deployment) or not SERVICE.fullmatch(service):
            raise RuntimeError('Identité du relais privé invalide.')
        parent = _directory(self.folder / app, 0o700)
        home = _directory(parent / (deployment + '-' + service), 0o700)
        return home, _directory(home / 'config', 0o755), _directory(home / 'ready', 0o755)

    def prepare(self, job, config, service='web', tooling=None):
        rows = self.for_app(job.app, force=True)
        if not rows:
            return None
        from web_app_runtime import admit, prepare_tooling, tooling_node_prefix
        from web_app_project_limits import admit as admit_project
        host = config['HostConfig']
        admit(self.docker, host['Memory'] // 1024**2 + MEMORY_MIB, host['NanoCpus'] // 1000000 + CPU_MILLICORES)
        admit_project(self.root, job, self.docker, host['Memory'] // 1024**2 + MEMORY_MIB, host['NanoCpus'] // 1000000 + CPU_MILLICORES)
        original = json.loads(self.docker(['image', 'inspect', config['Image']]))[0]['Config']
        entry = config.get('Entrypoint', original.get('Entrypoint') or [])
        command = config.get('Cmd', original.get('Cmd') or [])
        if (not isinstance(entry, list) or not isinstance(command, list) or not entry + command
                or len(entry + command) > 100 or any(not isinstance(x, str) or '\0' in x for x in entry + command)):
            raise RuntimeError('Commande de démarrage incompatible avec l’accès privé.')
        home, _, ready = self.paths(job.app, job.id, service)
        # Docker also inherits image ENV values not present in Config.Env. Clear
        # preloads explicitly for the trusted wrapper, then restore only for the
        # original application after the relay has bound localhost.
        preload_names = {'NODE_OPTIONS', 'NODE_PATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH'}
        effective = {}
        for item in [*(original.get('Env') or []), *config.get('Env', [])]:
            key, separator, value = item.partition('=')
            if separator and key in preload_names:
                effective[key] = value
        wait_env = _directory(home / 'wait-env', 0o755)
        atomic(wait_env / 'config.json', json.dumps({'version': 1, 'environment': effective}, separators=(',', ':')))
        mounts = host.setdefault('Mounts', [])
        if not any(m.get('Target') == '/__pockethost_tools' for m in mounts):
            tooling = tooling or prepare_tooling(self.docker)
            mounts.append({'Type': 'volume', 'Source': tooling, 'Target': '/__pockethost_tools', 'ReadOnly': True})
        mounts.extend([
            {'Type': 'bind', 'Source': str(ready), 'Target': '/run/kolab-ready', 'ReadOnly': True},
            {'Type': 'bind', 'Source': str(SCRIPT_ROOT / 'web_private_db_wait.cjs'), 'Target': '/run/kolab-wait.cjs', 'ReadOnly': True},
            {'Type': 'bind', 'Source': str(wait_env), 'Target': '/run/kolab-wait-env', 'ReadOnly': True},
        ])
        automatic = {'KOLAB_DB_' + str(row['port']) + '_URL': 'http://127.0.0.1:' + str(row['port']) for row in rows}
        config['Env'] = [v for v in config.get('Env', []) if v.split('=', 1)[0] not in automatic and v.split('=', 1)[0] not in preload_names]
        config['Env'] += [key + '=' for key in sorted(preload_names)]
        config['Env'] += [name + '=' + value for name, value in automatic.items()]
        config['Labels']['pockethost.web.private-db.wait'] = 'true'
        config['Entrypoint'] = tooling_node_prefix() + ['/run/kolab-wait.cjs', '--']
        config['Cmd'] = entry + command
        host['Init'] = True
        return {'appId': job.app, 'deployment': job.id, 'service': service, 'home': str(home)}

    def _existing(self, name):
        try:
            return json.loads(self.docker(['inspect', '--type', 'container', name], timeout=10))[0]
        except Exception:
            return None

    def _remove(self, info):
        labels = info.get('Config', {}).get('Labels') or {}
        if labels.get('pockethost.web.private-db') != 'true' or not CID.fullmatch(info.get('Id', '')):
            raise RuntimeError('Relais privé étranger refusé.')
        self.docker(['rm', '-f', info['Id']], timeout=15)

    def application_identity(self, info, namespace=None):
        from web_app_runtime import tooling_node_prefix
        identity = self.docker(['exec', '--user', '10001:10001', '--env', 'NODE_OPTIONS=', '--env', 'NODE_PATH=', '--env', 'LD_PRELOAD=', '--env', 'LD_LIBRARY_PATH=',
            info['Id'], *tooling_node_prefix(), '-e', APPLICATION_IDENTITY_SCRIPT], timeout=10).strip()
        matched = MARKER_IDENTITY.fullmatch(identity)
        if not matched or (namespace is not None and matched.group(1) != namespace):
            raise RuntimeError('Identité de démarrage de l’application invalide.')
        return identity

    def proxy_status(self, proxy, config_hash):
        image = os.environ.get('WEB_APPS_STORAGE_HELPER_IMAGE', '')
        config, host = proxy.get('Config') or {}, proxy.get('HostConfig') or {}
        labels = config.get('Labels') or {}
        if (not re.fullmatch(r'sha256:[a-f0-9]{64}', image) or proxy.get('Image') != image
                or not isinstance(proxy.get('Id'), str) or not CID.fullmatch(proxy['Id'])
                or config.get('User') != '10001:10001' or host.get('ReadonlyRootfs') is not True
                or labels.get('pockethost.web') != 'true' or labels.get('pockethost.web.private-db') != 'true'):
            raise RuntimeError('Image du relais privé non approuvée.')
        raw = self.docker(['exec', '--user', '10001:10001', '--env', 'LD_PRELOAD=', '--env', 'LD_LIBRARY_PATH=', proxy['Id'],
            '/usr/bin/timeout', '--signal=KILL', '2', '/bin/sh', '-ec', PROXY_STATUS_COMMAND], timeout=5)
        return proxy_status(raw, config_hash)

    def probe(self, app, rows):
        return probe_bridge(app, rows, directory=SOCKET_ROOT)

    def ensure(self, info, rows, wait=False):
        labels = info.get('Config', {}).get('Labels') or {}
        app, deployment = labels.get('pockethost.web.app', ''), labels.get('pockethost.web.deployment', '')
        service = labels.get('pockethost.web.service') or 'web'
        ident = info.get('Id', '')
        if not CID.fullmatch(ident) or not info.get('State', {}).get('Running'):
            return False
        suffix = 'private-db-' + service
        if len(suffix) > 31:
            suffix = 'private-db-' + hashlib.sha256(service.encode()).hexdigest()[:16]
        name = 'ph-web-' + app + '-' + deployment + '-' + suffix
        socket_path = SOCKET_ROOT / (app + '.sock')
        metadata = os.lstat(socket_path)
        if not stat.S_ISSOCK(metadata.st_mode):
            raise RuntimeError('Le relais privé du serveur n’est pas disponible.')
        socket_identity = str(metadata.st_dev) + ':' + str(metadata.st_ino)
        def socket_unchanged():
            current = os.lstat(socket_path)
            return stat.S_ISSOCK(current.st_mode) and str(current.st_dev) + ':' + str(current.st_ino) == socket_identity
        started = info['State'].get('StartedAt', '')
        value = {'version': 1, 'limits': {k: self.connection_limits[k] for k in ('perApp', 'perBinding')},
                 'bindings': [{k: row[k] for k in ('id', 'port', 'token', 'revision')} for row in rows]}
        text = json.dumps(value, separators=(',', ':'))
        with self.operations_lock:
            # A background tick can have captured Running=true before waiting
            # behind stop_old. Never create/restart a proxy from that snapshot.
            current = self._existing(ident)
            if (not current or not current.get('State', {}).get('Running')
                    or current['State'].get('StartedAt', '') != started):
                return False
            current_labels = current.get('Config', {}).get('Labels') or {}
            if (current.get('Id') != ident or current_labels.get('pockethost.web.app') != app
                    or current_labels.get('pockethost.web.deployment') != deployment
                    or (current_labels.get('pockethost.web.service') or 'web') != service):
                raise RuntimeError('Parent du relais privé étranger refusé.')
            home, config_dir, ready = self.paths(app, deployment, service)
            file = config_dir / 'config.json'
            if not file.exists() or file.read_text() != text:
                atomic(file, text)
            proxy = self._existing(name)
            cpu = '0' if unlimited('applications', app) else '0.05'
            if proxy:
                own = proxy.get('Config', {}).get('Labels') or {}
                if own.get('pockethost.web.app') != app or own.get('pockethost.web.deployment') != deployment or own.get('pockethost.web.private-db') != 'true':
                    raise RuntimeError('Identité du relais privé invalide.')
                if (own.get('pockethost.web.private-db.parent') != ident or own.get('pockethost.web.private-db.started') != started
                        or own.get('pockethost.web.private-db.socket') != socket_identity or own.get('pockethost.web.private-db.recipe') != self.recipe):
                    self._remove(proxy); proxy = None
                elif cpu == '0' and not cpu_unlimited(proxy.get('HostConfig') or {}):
                    self.docker(['update', '--cpu-quota', '-1', proxy['Id']], timeout=15)
                elif cpu != '0' and cpu_unlimited(proxy.get('HostConfig') or {}):
                    self._remove(proxy); proxy = None
            if proxy is None:
                from web_app_storage import _helper
                from web_app_runtime import admit
                from web_app_project_limits import admit as admit_project
                admit(self.docker, MEMORY_MIB, CPU_MILLICORES)
                admit_project(self.root, types.SimpleNamespace(app=app, snapshot={}), self.docker, MEMORY_MIB, CPU_MILLICORES)
                image = _helper(self.docker)
                args = ['create', '--name', name, '--network', 'container:' + ident,
                    '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--user=10001:10001',
                    '--memory=96m', '--memory-swap=96m', '--cpus=' + cpu, '--pids-limit=64', '--init', '--restart=unless-stopped',
                    '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=2m,uid=10001,gid=10001',
                    '--log-opt', 'max-size=1m', '--log-opt', 'max-file=2',
                    '--entrypoint', '/usr/local/bin/node', '--env', 'NODE_OPTIONS=--max-old-space-size=40',
                    '--health-cmd', 'node /run/kolab-proxy.cjs --check', '--health-interval=2s', '--health-timeout=' + PROXY_HEALTH_TIMEOUT, '--health-retries=3',
                    '--label', 'pockethost.web=true', '--label', 'pockethost.web.app=' + app,
                    '--label', 'pockethost.web.deployment=' + deployment, '--label', 'pockethost.web.private-db=true',
                    '--label', 'pockethost.web.service=' + suffix, '--label', 'pockethost.web.front=false',
                    '--label', 'pockethost.web.private-db.parent=' + ident, '--label', 'pockethost.web.private-db.started=' + started,
                    '--label', 'pockethost.web.private-db.socket=' + socket_identity,
                    '--label', 'pockethost.web.private-db.recipe=' + self.recipe,
                    '--mount', 'type=bind,source=' + str(config_dir) + ',target=/run/kolab-config,readonly',
                    '--mount', 'type=bind,source=' + str(socket_path) + ',target=/run/kolab-db.sock,readonly',
                    '--mount', 'type=bind,source=' + str(SCRIPT_ROOT / 'web_private_db_proxy.cjs') + ',target=/run/kolab-proxy.cjs,readonly',
                    image, '/run/kolab-proxy.cjs']
                if cpu == '0':
                    args.insert(1, '--cpu-quota=-1')
                self.docker(args, timeout=20)
                self.docker(['start', name], timeout=15)
            elif not proxy.get('State', {}).get('Running') and not proxy.get('State', {}).get('Restarting'):
                self.docker(['start', proxy['Id']], timeout=15)
        deadline = time.monotonic() + (30 if wait else 0)
        probe_rows = [row for row in rows if row.get('databaseRunning', row['available']) is True]
        probe_ids = sorted(row['id'] for row in probe_rows)
        while True:
            # The deployment path and background tick share one proof. Reload
            # its cache after acquiring the stop/delete operations lock, so a
            # waiter does not repeat cold reads or race atomic marker writes.
            with self.operations_lock:
                proxy = self._existing(name)
                healthy = proxy and proxy.get('State', {}).get('Running') and proxy['State'].get('Health', {}).get('Status') == 'healthy'
                if healthy:
                    proof_path = home / 'proof.json'
                    try:proof = json.loads(proof_path.read_text())
                    except (OSError, ValueError):proof = {}
                    wanted = {'proxy': proxy['Id'], 'startedAt': proxy['State'].get('StartedAt'), 'appStartedAt': started, 'config': hashlib.sha256(text.encode()).hexdigest(),
                              'probeBindings': probe_ids}
                    namespace, identity = proof.get('namespace', ''), proof.get('identity', '')
                    wrapped = labels.get('pockethost.web.private-db.wait') == 'true'
                    matched = MARKER_IDENTITY.fullmatch(identity)
                    valid_identity = bool(matched and matched.group(1) == namespace) if wrapped else identity == namespace
                    if all(proof.get(k) == v for k, v in wanted.items()) and re.fullmatch(r'net:\[[0-9]+\]', namespace):
                        if valid_identity and (ready / 'ready').exists() and (ready / 'ready').read_text() == identity and socket_unchanged():
                            return True
                    try:
                        namespace = self.proxy_status(proxy, wanted['config'])['namespace']
                        if probe_rows and not self.probe(app, probe_rows):
                            raise RuntimeError('Liaison privée indisponible.')
                        identity = self.application_identity(info, namespace) if wrapped else namespace
                        if not socket_unchanged():
                            raise RuntimeError('Socket du relais privé remplacé.')
                        atomic(ready / 'ready', identity)
                        atomic(proof_path, json.dumps({**wanted, 'namespace': namespace, 'identity': identity}), 0o600)
                        return True
                    except Exception:
                        healthy = False
            if time.monotonic() >= deadline:
                if wait:
                    raise RuntimeError('Le relais privé de la base ne démarre pas correctement.')
                return False
            time.sleep(.5)

    def attach(self, container, wait=True):
        info = json.loads(self.docker(['inspect', container]))[0]
        labels = info.get('Config', {}).get('Labels') or {}
        rows = self.for_app(labels.get('pockethost.web.app', ''), force=True)
        if rows:return self.ensure(info, rows, wait)
        self.release_unbound(info)
        return False

    def release_unbound(self, info):
        """A revoked connection must not stop an otherwise unbound app from restarting."""
        labels = info.get('Config', {}).get('Labels') or {}
        if labels.get('pockethost.web.private-db.wait') != 'true' or not info.get('State', {}).get('Running'):
            return
        started = info['State'].get('StartedAt', '')
        with self.operations_lock:
            current = self._existing(info.get('Id', ''))
            if not current or not current.get('State', {}).get('Running') or current['State'].get('StartedAt', '') != started:
                return
            home, _, ready = self.paths(labels.get('pockethost.web.app', ''), labels.get('pockethost.web.deployment', ''), labels.get('pockethost.web.service') or 'web')
            marker = home / 'released-started-at'
            if marker.exists() and marker.read_text() == started:
                return
            identity = self.application_identity(info)
            atomic(ready / 'ready', identity)
            atomic(marker, started, 0o600)

    def tick(self):
        try:
            self.refresh()
        except Exception:
            pass
        with self.policy_lock:
            policy = dict(self.bindings) if time.monotonic() - self.last_good <= STALE_SECONDS else {}
        ids = self.docker(['ps', '-aq', '--filter', 'label=pockethost.web=true']).split()
        infos = json.loads(self.docker(['inspect', *ids])) if ids else []
        parents, proxies = [], []
        for info in infos:
            labels = info.get('Config', {}).get('Labels') or {}
            if labels.get('pockethost.web.private-db') == 'true':
                proxies.append(info)
            else:
                parents.append(info)
        current, reports = set(), {}
        for info in parents:
            labels = info.get('Config', {}).get('Labels') or {}
            app = labels.get('pockethost.web.app', '')
            rows = policy.get(app, [])
            if not rows or not info.get('State', {}).get('Running'):
                continue
            current.add(info['Id'])
            try:
                good = self.ensure(info, rows)
                error = '' if good else 'proxy_unavailable'
            except Exception:
                good, error = False, 'broker_unavailable'
            environment = dict(v.split('=', 1) for v in info.get('Config', {}).get('Env', []) if '=' in v)
            for row in rows:
                result = reports.setdefault(row['id'], {'id': row['id'], 'appId': app, 'revision': row['revision'],
                    'status': 'ready' if good and row['available'] else 'unavailable',
                    'errorCode': error or ('' if row['available'] else 'base_stopped'), 'serviceCount': 0, 'needsRestart': False})
                result['serviceCount'] += int(good)
                result['needsRestart'] |= environment.get('KOLAB_DB_' + str(row['port']) + '_URL') != 'http://127.0.0.1:' + str(row['port'])
                if not good:
                    result['status'], result['errorCode'] = 'unavailable', error
        for info in proxies:
            labels = info.get('Config', {}).get('Labels') or {}
            if labels.get('pockethost.web.private-db.parent') not in current:
                with self.operations_lock:
                    self._remove(info)
        if time.monotonic() - self.last_good <= STALE_SECONDS:
            for info in parents:
                labels = info.get('Config', {}).get('Labels') or {}
                if not policy.get(labels.get('pockethost.web.app')):
                    self.release_unbound(info)
        live_apps = {x.get('Config', {}).get('Labels', {}).get('pockethost.web.app') for x in parents}
        for directory in self.folder.iterdir():
            if directory.is_dir() and not directory.is_symlink() and ID.fullmatch(directory.name) and directory.name not in live_apps and directory.name not in policy:
                shutil.rmtree(directory)
        for rows in policy.values():
            for row in rows:
                reports.setdefault(row['id'], {'id': row['id'], 'appId': row['appId'], 'revision': row['revision'],
                    'status': 'unavailable', 'errorCode': 'application_stopped', 'serviceCount': 0, 'needsRestart': False})
        output = list(reports.values())
        fingerprint = hashlib.sha256(json.dumps(output, sort_keys=True).encode()).hexdigest()
        if fingerprint != self.report_fingerprint or time.monotonic() - self.last_report > 15:
            self.api('status', {'bindings': output})
            self.report_fingerprint, self.last_report = fingerprint, time.monotonic()
        atomic(self.root / 'private-db-status.json', json.dumps({'version': 1, 'checkedAt': time.time(), 'bindings': output}), 0o640)

    def loop(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception:
                pass
            self.stop.wait(2)


def private_database_operations():
    """Serialize runtime stops with background sidecar creation/restarts."""
    return _manager.operations_lock if enabled() and _manager is not None else contextlib.nullcontext()


def prepare_app(job, config, root, docker, service='web', tooling=None):
    if not enabled() or _manager is None:
        return None
    return _manager.prepare(job, config, service, tooling)


def attach_app(container):
    if enabled() and _manager is not None:
        return _manager.attach(container)
    return False


def start_private_database_runner(control, secret, socket, root, docker):
    global _manager
    if not enabled():
        return None
    _manager = PrivateDatabases(control, secret, socket, root, docker)
    thread = threading.Thread(target=_manager.loop, name='web-private-databases', daemon=True)
    thread.start()
    return thread
