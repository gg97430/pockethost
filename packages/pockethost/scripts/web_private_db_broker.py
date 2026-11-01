#!/usr/bin/env python3
"""Bounded, revocable per-application Unix -> namespace-pinned PocketBase bridge.

The controller owns authorization and the immutable Docker namespace lookup.
This process only accepts its fixed loopback API and SHA256 capabilities.
It never reads Docker sockets, application environment variables, or log bodies.
"""
import argparse
import asyncio
import hashlib
import hmac
import http.client
import json
import os
import pathlib
import re
import signal
import socket
import stat
import struct
import time
import urllib.parse

API_PATH = '/api/web-apps/_private-db/broker'
IDENT = re.compile(r'^[a-z0-9]{15}$')
TOKEN = re.compile(r'^(?:[A-Za-z0-9]{64}|[A-Za-z0-9]{80})$')
REVISION = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
HASH = re.compile(r'^[a-f0-9]{64}$')
HANDSHAKE_BYTES = 1024
CONFIG_BYTES = 1024 * 1024
CHUNK_BYTES = 65536
MAX_APPS = 1000
MAX_PER_APP = 4
TARGETS_ROOT = pathlib.Path('/run/kolab-db-targets')


class InvalidConfiguration(ValueError):
    pass


def unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate key')
        result[key] = value
    return result


def validate_configuration(value, targets_root=TARGETS_ROOT):
    if not isinstance(value, dict) or value.get('version') != 1 or type(value.get('version')) is not int:
        raise InvalidConfiguration('Version')
    if 'enabled' in value and type(value['enabled']) is not bool:
        raise InvalidConfiguration('Enabled')
    apps, rows = value.get('applications'), value.get('bindings')
    if not isinstance(apps, list) or len(apps) > MAX_APPS or any(not isinstance(x, str) or not IDENT.fullmatch(x) for x in apps):
        raise InvalidConfiguration('Applications')
    if len(set(apps)) != len(apps) or not isinstance(rows, list) or len(rows) > MAX_APPS * MAX_PER_APP:
        raise InvalidConfiguration('Bindings')
    applications = set(apps)
    bindings, counts = {}, {}
    for item in rows:
        if not isinstance(item, dict):
            raise InvalidConfiguration('Binding')
        ident, app, instance = item.get('id'), item.get('appId'), item.get('instanceId')
        digest, revision = item.get('tokenHash'), item.get('revision')
        if any(not isinstance(x, str) or not IDENT.fullmatch(x) for x in (ident, app, instance)):
            raise InvalidConfiguration('Identity')
        if app not in applications or ident in bindings or not isinstance(digest, str) or not HASH.fullmatch(digest):
            raise InvalidConfiguration('Capability')
        if not isinstance(revision, str) or not REVISION.fullmatch(revision):
            raise InvalidConfiguration('Revision')
        # A host port may be reassigned to another tenant after a database restart.
        # Old controller responses must never silently restore that architecture.
        if 'targetPort' in item:
            raise InvalidConfiguration('Obsolete target')
        target, container, epoch = item.get('targetSocket'), item.get('targetContainerId'), item.get('targetEpoch')
        available = item.get('available', target is not None)
        if type(available) is not bool:
            raise InvalidConfiguration('Availability')
        if target is not None:
            if any(not isinstance(x, str) or not HASH.fullmatch(x) for x in (container, epoch)):
                raise InvalidConfiguration('Target identity')
            leaf = hashlib.sha256((container + ':' + epoch).encode('ascii')).hexdigest()[:32]
            expected = pathlib.Path(targets_root) / instance / leaf / 't.sock'
            if target != str(expected) or len(os.fsencode(target)) > 103:
                raise InvalidConfiguration('Target path')
        if available and target is None:
            raise InvalidConfiguration('Missing target')
        if not available and target is None and (container is not None or epoch is not None):
            raise InvalidConfiguration('Incomplete target')
        counts[app] = counts.get(app, 0) + 1
        if counts[app] > MAX_PER_APP:
            raise InvalidConfiguration('Application limit')
        bindings[ident] = {
            'id': ident, 'appId': app, 'instanceId': instance,
            'tokenHash': digest, 'revision': revision,
            'targetSocket': target if available else None,
            'targetContainerId': container if available else None,
            'targetEpoch': epoch if available else None,
        }
    return applications, bindings if value.get('enabled', True) else {}


def fetch_configuration(control, secret, timeout=3):
    """No environment proxy, redirect, DNS lookup or unlimited response read."""
    url = urllib.parse.urlsplit(control)
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', '::1') or url.path not in ('', '/') or url.query or url.fragment or url.username or url.password:
        raise InvalidConfiguration('Controller address')
    if not isinstance(secret, str) or not re.fullmatch(r'[\x21-\x7e]{32,128}', secret):
        raise InvalidConfiguration('Controller capability')
    connection = http.client.HTTPConnection(url.hostname, url.port or 8091, timeout=timeout)
    deadline = time.monotonic() + timeout
    try:
        connection.request('GET', API_PATH, headers={'X-DB-Bridge': secret, 'Accept': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise InvalidConfiguration('Controller response')
        length = response.getheader('Content-Length')
        if length and (not length.isdigit() or int(length) > CONFIG_BYTES):
            raise InvalidConfiguration('Configuration size')
        result = bytearray()
        while True:
            # read1() closes response.fp as soon as Content-Length bytes have
            # arrived. HTTP/1.0 and Connection: close may also have detached the
            # connection socket already, so EOF must precede socket inspection.
            if response.length == 0:
                break
            if response.isclosed():
                if response.length not in (None, 0):
                    raise InvalidConfiguration('Incomplete controller response')
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Configuration deadline')
            sock = connection.sock or getattr(getattr(response.fp, 'raw', None), '_sock', None)
            if sock is None:
                raise InvalidConfiguration('Controller stream')
            sock.settimeout(remaining)
            chunk = response.read1(min(16384, CONFIG_BYTES + 1 - len(result)))
            if not chunk:
                if response.length not in (None, 0):
                    raise InvalidConfiguration('Incomplete controller response')
                break
            result.extend(chunk)
            if len(result) > CONFIG_BYTES:
                raise InvalidConfiguration('Configuration size')
        return json.loads(result, object_pairs_hook=unique_json)
    finally:
        connection.close()


class Session:
    def __init__(self, app, writer):
        self.app = app
        self.writer = writer
        self.upstream = None
        self.binding = None
        self.task = asyncio.current_task()
        self.last_io = time.monotonic()

    def close(self):
        self.writer.close()
        if self.upstream:
            self.upstream.close()
        if self.task and not self.task.done():
            self.task.cancel()


class Broker:
    def __init__(self, directory, control='http://127.0.0.1:8091', secret='', *,
                 poll_seconds=3, stale_seconds=10, handshake_seconds=2, idle_seconds=120,
                 max_connections=512, max_app_connections=128, max_binding_connections=32,
                 targets_directory=TARGETS_ROOT, target_uid=10001, target_gid=1000, target_directory_uid=1000):
        self.directory = pathlib.Path(directory).absolute()
        self.control, self.secret = control, secret
        self.poll_seconds, self.stale_seconds = poll_seconds, stale_seconds
        self.handshake_seconds, self.idle_seconds = handshake_seconds, idle_seconds
        self.max_connections, self.max_app_connections = max_connections, max_app_connections
        self.max_binding_connections = max_binding_connections
        self.targets_directory = pathlib.Path(targets_directory).absolute()
        self.target_uid, self.target_gid = target_uid, target_gid
        self.target_directory_uid = target_directory_uid
        self.bindings, self.servers, self.inodes = {}, {}, {}
        self.sessions = set()
        self.last_good = None
        self.stopped = asyncio.Event()
        self.tasks = []
        self._state = ''

    def fresh(self):
        return self.last_good is not None and time.monotonic() - self.last_good <= self.stale_seconds

    def announce(self, state):
        if state != self._state:
            self._state = state
            print('Kolab private database bridge: ' + state, flush=True)

    def socket_path(self, app):
        return self.directory / (app + '.sock')

    async def apply_configuration(self, value):
        applications, bindings = validate_configuration(value, self.targets_directory)
        if self.directory.is_symlink() or self.directory.resolve() != self.directory:
            raise InvalidConfiguration('Socket directory')
        self.directory.mkdir(mode=0o710, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o710)
        for app in applications - set(self.servers):
            path = self.socket_path(app)
            if path.exists() or path.is_symlink():
                info = path.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                    raise InvalidConfiguration('Socket collision')
                path.unlink()
            async def accepted(reader, writer, app=app):
                await self.handle(app, reader, writer)
            server = await asyncio.start_unix_server(accepted, path=str(path), limit=HANDSHAKE_BYTES + 1, backlog=64)
            os.chmod(path, 0o666)
            self.servers[app] = server
            self.inodes[app] = path.lstat().st_ino
        self.bindings = bindings
        self.last_good = time.monotonic()
        # Revocation also closes already authenticated keep-alive / WebSocket flows.
        for session in list(self.sessions):
            if session.app not in applications or (session.binding and bindings.get(session.binding['id']) != session.binding):
                session.close()
        for app in set(self.servers) - applications:
            server = self.servers.pop(app)
            server.close()
            await server.wait_closed()
            self.unlink_socket(app)
        self.announce('ready')

    def unlink_socket(self, app):
        path = self.socket_path(app)
        try:
            info = path.lstat()
            if stat.S_ISSOCK(info.st_mode) and info.st_ino == self.inodes.get(app):
                path.unlink()
        except FileNotFoundError:
            pass
        self.inodes.pop(app, None)

    async def reject(self, writer, reason):
        try:
            writer.write(('ERR ' + reason + '\n').encode('ascii'))
            await asyncio.wait_for(writer.drain(), .5)
        except (OSError, asyncio.TimeoutError, ConnectionError):
            pass
        writer.close()

    def target_identity(self, binding):
        """Check the complete trusted directory chain without following symlinks."""
        root = self.targets_directory
        path = pathlib.Path(binding['targetSocket'])
        if root.resolve() != root:
            raise InvalidConfiguration('Target root')
        directories = (root, path.parent.parent, path.parent)
        identities = []
        for index, directory in enumerate(directories):
            info = directory.lstat()
            expected_mode = 0o770 if index == 2 else 0o700
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != self.target_directory_uid or info.st_gid != self.target_gid or stat.S_IMODE(info.st_mode) != expected_mode:
                raise InvalidConfiguration('Target directory')
            identities.append((info.st_dev, info.st_ino))
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != self.target_uid or info.st_gid != self.target_gid or stat.S_IMODE(info.st_mode) != 0o660:
            raise InvalidConfiguration('Target socket')
        identities.append((info.st_dev, info.st_ino))
        return identities

    def verify_target_peer(self, writer):
        # Linux validates the actual connected endpoint, even if a path changed
        # between lstat and connect. Tests on macOS still exercise inode checks.
        if hasattr(socket, 'SO_PEERCRED'):
            peer = writer.get_extra_info('socket')
            _, uid, gid = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != self.target_uid or gid != self.target_gid:
                raise InvalidConfiguration('Target peer')

    async def handle(self, app, reader, writer):
        if not self.fresh():
            await self.reject(writer, 'UNAVAILABLE')
            return
        if len(self.sessions) >= self.max_connections or sum(x.app == app for x in self.sessions) >= self.max_app_connections:
            await self.reject(writer, 'BUSY')
            return
        session = Session(app, writer)
        self.sessions.add(session)
        pumps = []
        try:
            try:
                line = await asyncio.wait_for(reader.readline(), self.handshake_seconds)
                if not line.endswith(b'\n') or len(line) > HANDSHAKE_BYTES:
                    raise ValueError('Handshake size')
                request = json.loads(line, object_pairs_hook=unique_json)
                if not isinstance(request, dict) or set(request) != {'version', 'bindingId', 'token'} or type(request['version']) is not int or request['version'] != 1:
                    raise ValueError('Handshake')
                ident, token = request['bindingId'], request['token']
                if not isinstance(ident, str) or not IDENT.fullmatch(ident) or not isinstance(token, str) or not TOKEN.fullmatch(token):
                    raise ValueError('Capability')
                binding = self.bindings.get(ident)
                if not binding or binding['appId'] != app or not hmac.compare_digest(hashlib.sha256(token.encode('ascii')).hexdigest(), binding['tokenHash']):
                    raise ValueError('Capability')
            except (ValueError, UnicodeError, asyncio.TimeoutError, asyncio.LimitOverrunError):
                await self.reject(writer, 'DENIED')
                return
            if not self.fresh() or not binding['targetSocket']:
                await self.reject(writer, 'UNAVAILABLE')
                return
            if sum(bool(x.binding and x.binding['id'] == ident) for x in self.sessions) >= self.max_binding_connections:
                await self.reject(writer, 'BUSY')
                return
            session.binding = dict(binding)
            try:
                identity = self.target_identity(binding)
                upstream_reader, upstream_writer = await asyncio.wait_for(asyncio.open_unix_connection(binding['targetSocket'], limit=CHUNK_BYTES), 3)
                session.upstream = upstream_writer
                self.verify_target_peer(upstream_writer)
                if identity != self.target_identity(binding):
                    raise InvalidConfiguration('Target changed')
            except (OSError, asyncio.TimeoutError, InvalidConfiguration):
                await self.reject(writer, 'UNAVAILABLE')
                return
            if not self.fresh() or self.bindings.get(ident) != session.binding:
                await self.reject(writer, 'UNAVAILABLE')
                return
            writer.write(b'OK\n')
            await asyncio.wait_for(writer.drain(), self.handshake_seconds)
            session.last_io = time.monotonic()

            async def pipe(source, destination):
                while True:
                    try:
                        data = await asyncio.wait_for(source.read(CHUNK_BYTES), min(1, self.idle_seconds))
                    except asyncio.TimeoutError:
                        if time.monotonic() - session.last_io >= self.idle_seconds:
                            raise
                        continue
                    if not data:
                        if destination.can_write_eof():
                            destination.write_eof()
                        return
                    session.last_io = time.monotonic()
                    destination.write(data)
                    await asyncio.wait_for(destination.drain(), self.idle_seconds)

            pumps = [asyncio.create_task(pipe(reader, upstream_writer)), asyncio.create_task(pipe(upstream_reader, writer))]
            await asyncio.gather(*pumps)
        except (OSError, asyncio.TimeoutError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            for task in pumps:
                task.cancel()
            if pumps:
                await asyncio.gather(*pumps, return_exceptions=True)
            self.sessions.discard(session)
            writer.close()
            if session.upstream:
                session.upstream.close()

    async def poll(self):
        while not self.stopped.is_set():
            try:
                value = await asyncio.to_thread(fetch_configuration, self.control, self.secret)
                await self.apply_configuration(value)
            except Exception:
                self.announce('controller unavailable')
            try:
                await asyncio.wait_for(self.stopped.wait(), self.poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def watchdog(self):
        while not self.stopped.is_set():
            if not self.fresh():
                for session in list(self.sessions):
                    session.close()
            try:
                await asyncio.wait_for(self.stopped.wait(), min(.25, self.stale_seconds / 2))
            except asyncio.TimeoutError:
                pass

    async def run(self):
        self.tasks = [asyncio.create_task(self.poll()), asyncio.create_task(self.watchdog())]
        await self.stopped.wait()
        await self.close()

    async def close(self):
        self.stopped.set()
        for task in self.tasks:
            task.cancel()
        for session in list(self.sessions):
            session.close()
        await asyncio.gather(*(s.task for s in list(self.sessions)), *self.tasks, return_exceptions=True)
        for app, server in list(self.servers.items()):
            server.close()
            await server.wait_closed()
            self.unlink_socket(app)
        self.servers.clear()


def main():
    parser = argparse.ArgumentParser(description='Kolab private database bridge')
    parser.add_argument('--directory', default='/var/lib/kolab-db-bridge')
    parser.add_argument('--control', default=os.environ.get('WEB_DB_BRIDGE_CONTROL', 'http://127.0.0.1:8091'))
    args = parser.parse_args()
    os.umask(0o077)
    broker = Broker(args.directory, args.control, os.environ.get('WEB_DB_BRIDGE_SECRET', ''))
    async def serve():
        loop = asyncio.get_running_loop()
        for number in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(number, broker.stopped.set)
        await broker.run()
    asyncio.run(serve())


if __name__ == '__main__':
    main()
