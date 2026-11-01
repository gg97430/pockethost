#!/usr/bin/env python3
"""Continuous, read-only web-container probes. Runs independently of deployment jobs."""
import datetime
import http.client
import json
import re
import socket
import ssl
import threading
import time
import urllib.request

ID = re.compile(r'^[a-z0-9]{15}$')
HEX = re.compile(r'^[a-f0-9]{64}$')
TARGET = re.compile(r'^http://127\.0\.0\.1:([1-9][0-9]{3,4})$')
ERRORS = {'', 'http_error', 'timeout', 'connection_error', 'container_unavailable', 'target_mismatch'}
FQDN = re.compile(r'^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')
# Detailed public-path results; the stable `errorCode` above keeps the controller's accepted vocabulary.
PUBLIC_ERRORS = {'', 'not_checked', 'public_dns', 'public_address_refused', 'public_timeout', 'public_connection',
                 'public_tls', 'public_http_error'}
PUBLIC_TO_ERROR = {'public_timeout': 'timeout', 'public_http_error': 'http_error'}
USER_AGENT = 'KolabHosting-Health/1'


def safe_path(value):
    return (isinstance(value, str) and len(value) <= 512
            and re.fullmatch(r'/[A-Za-z0-9/_~.\-]*', value) is not None
            and '//' not in value and not any(part in ('.', '..') for part in value.split('/')))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HealthControl:
    def __init__(self, url, secret):
        match = re.fullmatch(r'http://127\.0\.0\.1:([1-9][0-9]{3,4})/?', url)
        if not match or not 1024 <= int(match[1]) <= 65535 or not isinstance(secret, str) or len(secret) < 32:
            raise ValueError('Private health controller configuration required')
        self.url = url.rstrip('/')
        self.secret = secret
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def send(self, endpoint, body):
        if endpoint not in ('config', 'report'):
            raise ValueError('Invalid health endpoint')
        request = urllib.request.Request(self.url + '/api/web-apps/_health/' + endpoint,
            headers={'Content-Type': 'application/json', 'X-Web-Worker': self.secret},
            data=json.dumps(body).encode(), method='POST')
        with self.opener.open(request, timeout=8) as response:
            data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise ValueError('Health control response too large')
            return json.loads(data)


def valid_public_host(value):
    """Optional real domain probed over HTTPS:443 with SNI. IP literals and single labels are refused."""
    return isinstance(value, str) and FQDN.fullmatch(value) is not None and not value.endswith(('.local', '.internal', '.localhost'))


def valid_config(config):
    if not isinstance(config, dict) or not all(ID.fullmatch(config.get(key, '')) for key in ('appId', 'deploymentId')):
        return False
    if config.get('containerName') != 'ph-web-' + config['appId'] + '-' + config['deploymentId']:
        return False
    if config.get('publicHost') not in (None, '') and not valid_public_host(config['publicHost']):
        return False
    target = TARGET.fullmatch(config.get('expectedTarget', ''))
    return (target is not None and 1024 <= int(target[1]) <= 65535
        and safe_path(config.get('path')) and re.fullmatch(r'[A-Za-z0-9]{24}', config.get('revision', '')) is not None
        and all(type(config.get(key)) is int and low <= config[key] <= high
                for key, low, high in (('intervalSeconds', 60, 3600), ('timeoutSeconds', 1, 10), ('failureThreshold', 1, 10))))


def published_target(config, info):
    """The inspected container port must match the controller's published target."""
    if not valid_config(config) or not isinstance(info, dict):
        return None
    labels = info.get('Config', {}).get('Labels') or {}
    host = info.get('HostConfig') or {}
    ports = [binding for key, values in info.get('NetworkSettings', {}).get('Ports', {}).items() if key.endswith('/tcp') for binding in (values or [])]
    if (not HEX.fullmatch(info.get('Id', '')) or not info.get('State', {}).get('Running')
        or info.get('Config', {}).get('User') != '10001:10001'
        or labels.get('pockethost.web') != 'true' or labels.get('pockethost.web.app') != config['appId']
        or labels.get('pockethost.web.deployment') != config['deploymentId']
        or host.get('Privileged') or host.get('ReadonlyRootfs') is not True or host.get('Binds')
        or any(mount.get('Type') == 'bind' for mount in info.get('Mounts', []))
        or len(ports) != 1 or ports[0].get('HostIp') != '127.0.0.1'):
        return None
    port = ports[0].get('HostPort', '')
    target = 'http://127.0.0.1:' + port if isinstance(port, str) else ''
    if target != config['expectedTarget']:
        return None
    return int(port)


def _resolve_public(host):
    """Same SSRF policy as webhooks: every resolved address must be globally routable."""
    from web_app_notifications import NotificationError, public_addresses
    try:
        return public_addresses(host)
    except NotificationError as error:
        raise PublicProbeError('public_address_refused' if error.code == 'ssrf' else
                               'public_timeout' if error.code == 'timeout' else 'public_dns') from None


def _public_context():
    return ssl.create_default_context()


class PublicProbeError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__('Public probe failed')


def public_probe(config):
    """HTTPS GET on the real domain (DNS, TLS/SNI, proxy path). Redirects are never followed and bodies never read."""
    host, timeout = config['publicHost'], config['timeoutSeconds']
    started = time.monotonic()
    result = {'publicStatusCode': 0, 'publicDurationMs': 0, 'publicErrorCode': 'public_connection'}
    sock = None
    timer = None
    deadline_hit = threading.Event()
    try:
        addresses = _resolve_public(host)
        if not addresses:
            raise PublicProbeError('public_dns')
        for index, (family, kind, proto, address) in enumerate(addresses):
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise PublicProbeError('public_timeout')
            raw = socket.socket(family, kind, proto)
            raw.settimeout(remaining)
            try:
                raw.connect(address)
                break
            except OSError as error:
                raw.close()
                if index == len(addresses) - 1:
                    raise PublicProbeError('public_timeout' if isinstance(error, socket.timeout) else 'public_connection') from None
        active = [raw]  # wrap_socket detaches raw: the timer must shut whichever socket is live.
        def abort():
            deadline_hit.set()
            try:
                active[0].shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(max(0, timeout - (time.monotonic() - started)), abort)
        timer.daemon = True
        timer.start()
        try:
            sock = active[0] = _public_context().wrap_socket(raw, server_hostname=host)
        except ssl.SSLError:
            raw.close()
            raise PublicProbeError('public_timeout' if deadline_hit.is_set() else 'public_tls') from None
        except OSError as error:
            raw.close()
            raise PublicProbeError('public_timeout' if deadline_hit.is_set() or isinstance(error, socket.timeout) else 'public_connection') from None
        connection = http.client.HTTPSConnection(host, 443, timeout=timeout)
        connection.sock = sock
        connection.request('GET', config['path'], headers={'User-Agent': USER_AGENT, 'Connection': 'close'})
        response = connection.getresponse()
        if deadline_hit.is_set():
            raise PublicProbeError('public_timeout')
        if not 100 <= response.status <= 599:
            raise PublicProbeError('public_connection')
        result['publicStatusCode'] = response.status
        # 3xx is a reachable proxy answer; its Location (possibly a private address) is never contacted.
        result['publicErrorCode'] = '' if 200 <= response.status < 400 else 'public_http_error'
    except PublicProbeError as error:
        result['publicErrorCode'] = error.code
    except (socket.timeout, TimeoutError):
        result['publicErrorCode'] = 'public_timeout'
    except Exception:
        result['publicErrorCode'] = 'public_timeout' if deadline_hit.is_set() else 'public_connection'
    finally:
        if timer is not None:
            timer.cancel()
        if sock is not None:
            sock.close()
        result['publicDurationMs'] = min(120000, max(0, round((time.monotonic() - started) * 1000)))
    return result


def probe(config, docker):
    """No response bodies, redirects, credentials or project environment are read."""
    if not valid_config(config):
        raise ValueError('Invalid health configuration')
    started = time.monotonic()
    result = {'appId': config['appId'], 'deploymentId': config['deploymentId'], 'revision': config['revision'],
              'containerId': '', 'statusCode': 0, 'durationMs': 0, 'errorCode': 'container_unavailable'}
    if config.get('publicHost'):
        result.update(publicHost=config['publicHost'], publicStatusCode=0, publicDurationMs=0, publicErrorCode='not_checked')
    connection = None
    timer = None
    deadline_hit = threading.Event()
    try:
        raw = docker(['inspect', config['containerName']], timeout=5)
        if not isinstance(raw, str) or len(raw) > 2 * 1024 * 1024:
            raise ValueError('Invalid container inspection')
        values = json.loads(raw)
        if not isinstance(values, list) or len(values) != 1:
            raise ValueError('Invalid container inspection')
        info = values[0]
        port = published_target(config, info)
        if port is None:
            result['errorCode'] = 'target_mismatch'
            return result
        result['containerId'] = info['Id']
        http_started = time.monotonic()
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=config['timeoutSeconds'])
        connection.connect()
        active_socket = connection.sock
        def abort():
            deadline_hit.set()
            try:
                active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(max(0, config['timeoutSeconds'] - (time.monotonic() - http_started)), abort)
        timer.daemon = True
        timer.start()
        # http.client never follows Location headers. Proxy environment is not consulted.
        connection.request('GET', config['path'], headers={'User-Agent': USER_AGENT, 'Connection': 'close'})
        response = connection.getresponse()
        if deadline_hit.is_set():
            raise TimeoutError('Health deadline exceeded')
        if not 100 <= response.status <= 599:
            raise ValueError('Invalid HTTP status')
        result['statusCode'] = response.status
        result['errorCode'] = '' if 200 <= response.status < 400 else 'http_error'
    except TimeoutError:
        result['errorCode'] = 'timeout'
    except Exception:
        result['errorCode'] = 'timeout' if deadline_hit.is_set() else 'connection_error' if result['containerId'] else 'container_unavailable'
    finally:
        if timer is not None:
            timer.cancel()
        if connection is not None:
            connection.close()
        result['durationMs'] = min(120000, max(0, round((time.monotonic() - started) * 1000)))
        result['checkedAt'] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')
    if config.get('publicHost') and result['errorCode'] == '':
        # Loopback answered: the public path must answer too, otherwise DNS/TLS/proxy is down for visitors.
        result.update(public_probe(config))
        if result['publicErrorCode']:
            result['errorCode'] = PUBLIC_TO_ERROR.get(result['publicErrorCode'], 'connection_error')
        result['durationMs'] = min(120000, max(0, round((time.monotonic() - started) * 1000)))
        result['checkedAt'] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')
    return result


class HealthMonitor:
    def __init__(self, control, docker, refresh_seconds=15, max_parallel=4):
        self.control = control
        self.docker = docker
        self.refresh_seconds = max(5, min(60, refresh_seconds))
        self.max_parallel = max(1, min(4, max_parallel))
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._configs = {}
        self._due = {}
        self._pending = {}
        self._next_refresh = 0
        self.thread = threading.Thread(target=self._run, name='web-health-monitor', daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self, timeout=15):
        self._stop.set()
        self.thread.join(timeout=max(0, min(30, timeout)))

    def _refresh(self):
        configs = {}
        for page in range(1, 11):
            value = self.control.send('config', {'page': page})
            if not isinstance(value, dict) or not isinstance(value.get('items'), list) or len(value['items']) > 100:
                raise ValueError('Invalid health configuration page')
            for item in value['items']:
                if valid_config(item):
                    configs[item['appId']] = item
            if not value.get('hasMore'):
                break
        for app_id, item in configs.items():
            old = self._configs.get(app_id)
            if not old or (old['revision'], old['deploymentId']) != (item['revision'], item['deploymentId']):
                self._due[app_id] = 0
        self._configs = configs
        self._due = {key: value for key, value in self._due.items() if key in configs}

    def tick(self):
        now = time.monotonic()
        if now >= self._next_refresh:
            self._next_refresh = now + self.refresh_seconds
            try:
                self._refresh()
            except Exception:
                # Fail closed after controller loss: do not keep probing stale configurations.
                self._configs = {}
        with self._lock:
            candidates = sorted((item for key, item in self._configs.items()
                                 if key not in self._pending and self._due.get(key, 0) <= now),
                                key=lambda item: (self._due.get(item['appId'], 0), item['appId']))
            for item in candidates[:max(0, self.max_parallel - len(self._pending))]:
                app_id = item['appId']
                self._due[app_id] = now + item['intervalSeconds']
                thread = threading.Thread(target=self._check, args=(dict(item),), name='web-health-probe', daemon=True)
                self._pending[app_id] = thread
                thread.start()

    def _check(self, config):
        try:
            result = probe(config, self.docker)
            if not self._stop.is_set():
                self.control.send('report', result)
        except Exception:
            pass  # Never print exception text: container or HTTP output could contain secrets.
        finally:
            with self._lock:
                self._pending.pop(config['appId'], None)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                pass
            self._stop.wait(1)


def start_health_monitor(control_url, secret, docker):
    """Start once after the worker lock and rootless prerequisites have succeeded."""
    return HealthMonitor(HealthControl(control_url, secret), docker).start()
