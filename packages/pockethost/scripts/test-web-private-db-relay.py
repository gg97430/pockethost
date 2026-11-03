#!/usr/bin/env python3
"""Real local sockets; no Docker, remote server, credentials or database needed."""
import asyncio
import contextlib
import hashlib
import http.server
import importlib.util
import io
import json
import os
import pathlib
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

SOURCE = pathlib.Path(__file__).with_name('web_private_db_broker.py')
spec = importlib.util.spec_from_file_location('private_db_broker', SOURCE)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
APP, OTHER, BINDING, SECOND, INSTANCE = 'a' * 15, 'b' * 15, 'c' * 15, 'd' * 15, 'e' * 15
TOKEN = 'Q' * 64
REVISION = 'R' * 24
NODE = shutil.which('node')
PROXY = SOURCE.with_name('web_private_db_proxy.cjs')
WAIT = SOURCE.with_name('web_private_db_wait.cjs')
TARGET = SOURCE.with_name('web_private_db_target.cjs')
CONTAINER, EPOCH = '1' * 64, '2' * 64


def target_path(root=b.TARGETS_ROOT, container=CONTAINER, epoch=EPOCH):
    return str(pathlib.Path(root) / INSTANCE / hashlib.sha256((container + ':' + epoch).encode()).hexdigest()[:32] / 't.sock')


def row(target, **kwargs):
    return {'id': BINDING, 'appId': APP, 'instanceId': INSTANCE, 'tokenHash': hashlib.sha256(TOKEN.encode()).hexdigest(),
            'revision': REVISION, 'targetSocket': target, 'targetContainerId': CONTAINER if target else None,
            'targetEpoch': EPOCH if target else None, **kwargs}


def config(target, **kwargs):
    return {'version': 1, 'enabled': True, 'applications': [APP, OTHER], 'bindings': [row(target, **kwargs)]}


def ephemeral_port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        return connection.getsockname()[1]


class ConfigurationTests(unittest.TestCase):
    def test_only_immutable_namespace_targets_and_complete_unique_grants_are_accepted(self):
        target = target_path()
        self.assertEqual(b.validate_configuration(config(target))[1][BINDING]['targetSocket'], target)
        for bad in [40000, '127.0.0.1:8090', '/run/docker.sock', target.replace(INSTANCE, APP), target.replace('/t.sock', '/../../docker.sock'), True]:
            with self.subTest(target=bad), self.assertRaises(b.InvalidConfiguration):
                b.validate_configuration(config(bad))
        for old in [22, 3000, 5174, 8091, 40000, None]:
            with self.assertRaises(b.InvalidConfiguration): b.validate_configuration(config(None, targetPort=old))
        for patch in [{'appId': OTHER + '/..'}, {'id': '../../etc/passwd'}, {'appId': 'z' * 15}, {'tokenHash': 'invalid'}, {'revision': []}]:
            with self.subTest(patch=patch), self.assertRaises(b.InvalidConfiguration):
                b.validate_configuration(config(target, **patch))
        value = config(target); value['bindings'].append(row(target))
        with self.assertRaises(b.InvalidConfiguration): b.validate_configuration(value)
        self.assertEqual(b.validate_configuration(config(None, available=False))[1][BINDING]['targetSocket'], None)
        value = config(target); value['enabled'] = False
        self.assertEqual(b.validate_configuration(value)[1], {})

    def test_controller_is_loopback_only_and_never_follows_redirect(self):
        for control in ['https://127.0.0.1:8091', 'http://example.com', 'http://user:pass@127.0.0.1', 'http://127.0.0.1/other']:
            with self.subTest(control=control), self.assertRaises(b.InvalidConfiguration):
                b.fetch_configuration(control, 's' * 48)
        requests = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get('X-DB-Bridge')))
                self.send_response(302); self.send_header('Location', 'http://127.0.0.1:1/secret'); self.end_headers()
            def log_message(self, *args): pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with self.assertRaises(b.InvalidConfiguration): b.fetch_configuration('http://127.0.0.1:' + str(server.server_port), 's' * 48)
            self.assertEqual(requests, [(b.API_PATH, 's' * 48)])
        finally: server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_configuration_read_has_absolute_deadline_and_bounded_bytes(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers()
                try:
                    for _ in range(20): self.wfile.write(b' '); self.wfile.flush(); time.sleep(.05)
                except (BrokenPipeError, ConnectionResetError): pass
            def log_message(self, *args): pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler); server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start(); started = time.monotonic()
        try:
            with self.assertRaises((TimeoutError, socket.timeout)):
                b.fetch_configuration('http://127.0.0.1:' + str(server.server_port), 's' * 48, timeout=.15)
            self.assertLess(time.monotonic() - started, .6)
        finally: server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_controller_responses_with_content_length_and_closed_socket_are_read(self):
        value = config(target_path()); body = json.dumps(value).encode()
        for protocol, framing in [('HTTP/1.0', 'length'), ('HTTP/1.1', 'length'), ('HTTP/1.1', 'chunked'), ('HTTP/1.0', 'eof'), ('HTTP/1.1', 'truncated')]:
            with self.subTest(protocol=protocol, framing=framing):
                class Handler(http.server.BaseHTTPRequestHandler):
                    protocol_version = protocol
                    def do_GET(self):
                        self.send_response(200)
                        if framing == 'length': self.send_header('Content-Length', str(len(body)))
                        if framing == 'truncated': self.send_header('Content-Length', str(len(body) + 10))
                        if framing == 'chunked': self.send_header('Transfer-Encoding', 'chunked')
                        self.send_header('Connection', 'close'); self.end_headers()
                        self.wfile.write(('%x\r\n' % len(body)).encode() + body + b'\r\n0\r\n\r\n' if framing == 'chunked' else body)
                    def log_message(self, *args): pass
                server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler); server.daemon_threads = True
                thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
                try:
                    if framing == 'truncated':
                        with self.assertRaises(b.InvalidConfiguration): b.fetch_configuration('http://127.0.0.1:' + str(server.server_port), 's' * 48)
                    else: self.assertEqual(b.fetch_configuration('http://127.0.0.1:' + str(server.server_port), 's' * 48), value)
                finally: server.shutdown(); server.server_close(); thread.join(timeout=2)


class SocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # macOS' default TMPDIR is too long for an AF_UNIX pathname.
        self.temporary = tempfile.TemporaryDirectory(prefix='kdb-', dir='/tmp')
        self.root = pathlib.Path(self.temporary.name).resolve()
        self.upstream_connections = set()
        self.upstream_bytes = 0
        self.read_gate = asyncio.Event(); self.read_gate.set()
        self.targets = self.root / 't'; self.targets.mkdir(mode=0o700)
        os.chown(self.targets, -1, os.getgid())
        self.target = target_path(self.targets)
        pathlib.Path(self.target).parent.mkdir(parents=True)
        os.chmod(pathlib.Path(self.target).parent.parent, 0o700); os.chmod(pathlib.Path(self.target).parent, 0o770)
        for directory in [pathlib.Path(self.target).parent.parent, pathlib.Path(self.target).parent]: os.chown(directory, -1, os.getgid())
        self.server = await asyncio.start_unix_server(self.echo, self.target); os.chmod(self.target, 0o660); os.chown(self.target, -1, os.getgid())
        self.broker = b.Broker(self.root / 's', stale_seconds=30, idle_seconds=2, targets_directory=self.targets,
            target_uid=os.getuid(), target_gid=os.getgid(), target_directory_uid=os.getuid())
        await self.broker.apply_configuration(config(self.target))
        self.clients = []

    async def echo(self, reader, writer):
        self.upstream_connections.add(writer)
        try:
            await self.read_gate.wait()
            while True:
                data = await reader.read(65536)
                if not data: break
                self.upstream_bytes += len(data)
                writer.write(data); await writer.drain()
        except (OSError, ConnectionError): pass
        finally:
            self.upstream_connections.discard(writer); writer.close()

    async def asyncTearDown(self):
        self.read_gate.set()
        for writer in self.clients: writer.close()
        await self.broker.close()
        self.server.close(); await self.server.wait_closed()
        for writer in self.upstream_connections: writer.close()
        await asyncio.sleep(.01)
        self.temporary.cleanup()

    async def connect(self, app=APP, ident=BINDING, token=TOKEN, suffix=b''):
        reader, writer = await asyncio.open_unix_connection(str(self.broker.socket_path(app)))
        self.clients.append(writer)
        writer.write(json.dumps({'version': 1, 'bindingId': ident, 'token': token}).encode() + b'\n' + suffix)
        await writer.drain()
        return reader, writer, await asyncio.wait_for(reader.readline(), 1)

    async def test_authenticated_link_streams_binary_and_preserves_half_close(self):
        reader, writer, result = await self.connect(suffix=b'first payload')
        self.assertEqual(result, b'OK\n'); self.assertEqual(await reader.readexactly(13), b'first payload')
        payload = bytes(range(256)) * 3000
        async def send(): writer.write(payload); await writer.drain()
        received, _ = await asyncio.gather(reader.readexactly(len(payload)), send())
        self.assertEqual(received, payload)
        writer.write_eof()
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')

    async def test_capability_cannot_cross_application_or_guess_identity(self):
        for app, ident, token in [(OTHER, BINDING, TOKEN), (APP, SECOND, TOKEN), (APP, BINDING, 'X' * 64)]:
            with self.subTest(app=app, ident=ident):
                _, _, result = await self.connect(app, ident, token)
                self.assertEqual(result, b'ERR DENIED\n')
        self.assertFalse(self.upstream_connections)

    async def test_oversized_incomplete_and_duplicate_handshakes_are_rejected(self):
        for data in [b'x' * 2048 + b'\n', b'{"version":1,"version":1}\n', b'{}\n']:
            reader, writer = await asyncio.open_unix_connection(str(self.broker.socket_path(APP))); self.clients.append(writer)
            writer.write(data); await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readline(), 1), b'ERR DENIED\n')
        self.broker.handshake_seconds = .05
        reader, writer = await asyncio.open_unix_connection(str(self.broker.socket_path(APP))); self.clients.append(writer)
        writer.write(b'{')
        self.assertEqual(await asyncio.wait_for(reader.readline(), 1), b'ERR DENIED\n')

    async def test_revocation_closes_active_flows_but_preserves_socket_inode(self):
        reader, writer, _ = await self.connect(); writer.write(b'live'); await writer.drain(); self.assertEqual(await reader.readexactly(4), b'live')
        inode = self.broker.socket_path(APP).stat().st_ino
        value = config(self.target); value['bindings'] = []
        await self.broker.apply_configuration(value)
        self.assertEqual(self.broker.socket_path(APP).stat().st_ino, inode)
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
        _, _, denied = await self.connect(); self.assertEqual(denied, b'ERR DENIED\n')
        await self.broker.apply_configuration(config(self.target))
        _, _, restored = await self.connect(); self.assertEqual(restored, b'OK\n')

    async def test_target_change_offline_and_stale_control_close_active_flows(self):
        reader, _, _ = await self.connect()
        await self.broker.apply_configuration(config(None, available=False))
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
        _, _, offline = await self.connect(); self.assertEqual(offline, b'ERR UNAVAILABLE\n')
        await self.broker.apply_configuration(config(self.target))
        reader, _, _ = await self.connect()
        self.broker.stale_seconds = .05
        watchdog = asyncio.create_task(self.broker.watchdog())
        try:
            self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
            _, _, unavailable = await self.connect(); self.assertEqual(unavailable, b'ERR UNAVAILABLE\n')
        finally: watchdog.cancel(); await asyncio.gather(watchdog, return_exceptions=True)

    async def test_per_binding_and_preauth_limits_bound_open_connections(self):
        self.broker.max_binding_connections = 1
        _, _, okay = await self.connect(); self.assertEqual(okay, b'OK\n')
        _, _, busy = await self.connect(); self.assertEqual(busy, b'ERR BUSY\n')
        self.broker.max_app_connections = 1
        _, _, busy = await self.connect(); self.assertEqual(busy, b'ERR BUSY\n')

    async def test_default_binding_allows_a_normal_parallel_server_render_burst(self):
        # A page can perform dozens of independent reads while keep-alive
        # connections from previous requests remain open. No live DB is used.
        for _ in range(48):
            reader, writer, result = await self.connect()
            self.assertEqual(result, b'OK\n')
            writer.write(b'page');await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(4),1),b'page')
        self.assertLessEqual(len(self.broker.sessions),self.broker.max_app_connections)

    async def test_live_policy_keeps_existing_streams_and_bounds_new_ones(self):
        reader, writer, _ = await self.connect()
        value = config(self.target)
        value['limits'] = {'total': 4, 'perApp': 2, 'perBinding': 1, 'perDatabase': 2}
        await self.broker.apply_configuration(value)
        writer.write(b'kept'); await writer.drain()
        self.assertEqual(await reader.readexactly(4), b'kept')
        self.assertEqual((await self.connect())[2], b'ERR BUSY\n')
        value['limits']['perBinding'] = 2
        await self.broker.apply_configuration(value)
        self.assertEqual((await self.connect())[2], b'OK\n')

    async def test_socket_collisions_are_not_deleted_and_removed_apps_disconnect(self):
        third = 'f' * 15; path = self.broker.socket_path(third); path.symlink_to(self.root / 'outside')
        value = config(self.target); value['applications'].append(third)
        with self.assertRaises(b.InvalidConfiguration): await self.broker.apply_configuration(value)
        self.assertTrue(path.is_symlink())
        reader, _, _ = await self.connect()
        await self.broker.apply_configuration({'version': 1, 'applications': [], 'bindings': []})
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
        self.assertFalse(self.broker.socket_path(APP).exists())

    async def test_target_symlink_permissions_and_replaced_inode_fail_closed(self):
        os.chmod(self.target, 0o666)
        _, _, denied = await self.connect(); self.assertEqual(denied, b'ERR UNAVAILABLE\n')
        os.chmod(self.target, 0o660)
        original = pathlib.Path(self.target); moved = original.with_name('old.sock'); original.rename(moved); original.symlink_to(moved)
        try:
            _, _, denied = await self.connect(); self.assertEqual(denied, b'ERR UNAVAILABLE\n')
        finally: original.unlink(); moved.rename(original)
        identity = self.broker.target_identity
        called = 0
        def changed(binding):
            nonlocal called
            called += 1
            value = identity(binding)
            return value if called == 1 else value[:-1] + [(0, 0)]
        with mock.patch.object(self.broker, 'target_identity', changed):
            _, _, denied = await self.connect(); self.assertEqual(denied, b'ERR UNAVAILABLE\n')

    async def test_slow_database_propagates_backpressure_and_revocation_does_not_wait(self):
        self.read_gate.clear()
        reader, writer, result = await self.connect(); self.assertEqual(result, b'OK\n')
        writer.write(b'x' * (16 * 1024 * 1024))
        drain = asyncio.create_task(writer.drain())
        await asyncio.sleep(.1)
        self.assertFalse(drain.done(), 'A stalled database must stop the client writer')
        await self.broker.apply_configuration(config(None, available=False))
        with contextlib.suppress(ConnectionError, OSError): await asyncio.wait_for(drain, 1)
        with contextlib.suppress(ConnectionError, OSError): self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')

    async def test_stopped_target_cannot_reach_a_different_namespace(self):
        reader, writer, result = await self.connect(); self.assertEqual(result, b'OK\n')
        other_epoch = '3' * 64; new_path = pathlib.Path(target_path(self.targets, epoch=other_epoch))
        new_path.parent.mkdir(mode=0o770); os.chmod(new_path.parent, 0o770)
        os.chown(new_path.parent, -1, os.getgid())
        replacement = await asyncio.start_unix_server(self.echo, str(new_path)); os.chmod(new_path, 0o660); os.chown(new_path, -1, os.getgid())
        try:
            self.server.close(); await self.server.wait_closed(); pathlib.Path(self.target).unlink(missing_ok=True)
            _, _, stopped = await self.connect(); self.assertEqual(stopped, b'ERR UNAVAILABLE\n')
            # A controller-authorized new immutable namespace explicitly revokes
            # all streams into the former namespace before accepting its target.
            await self.broker.apply_configuration(config(str(new_path), targetEpoch=other_epoch))
            self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
            _, _, restored = await self.connect(); self.assertEqual(restored, b'OK\n')
        finally: replacement.close(); await replacement.wait_closed()


@unittest.skipUnless(NODE, 'Node is required for the trusted sidecar contract')
class ProxyTests(SocketTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.config_file, self.status_file = self.root / 'config.json', self.root / 'status.json'
        self.local_port = ephemeral_port()
        self.proxy = None
        self.write_config([{'id': BINDING, 'port': self.local_port, 'token': TOKEN, 'revision': REVISION}])
        self.proxy = await asyncio.create_subprocess_exec(NODE, str(PROXY), '--config', str(self.config_file), '--socket', str(self.broker.socket_path(APP)),
            '--status', str(self.status_file), '--poll-ms', '40', '--idle-ms', '1000', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await self.wait_ready()

    def write_config(self, bindings):
        path = self.config_file.with_suffix('.tmp'); path.write_text(json.dumps({'version': 1, 'bindings': bindings})); path.replace(self.config_file)

    async def test_loopback_proxy_preserves_parallel_reads_on_one_binding(self):
        for _ in range(48):
            reader,writer=await asyncio.open_connection('127.0.0.1',self.local_port)
            self.clients.append(writer);writer.write(b'read');await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(4),2),b'read')
        self.assertLessEqual(len(self.broker.sessions),self.broker.max_app_connections)

    async def test_proxy_reloads_limits_without_disconnecting_an_existing_reader(self):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.local_port); self.clients.append(writer)
        writer.write(b'open'); await writer.drain(); self.assertEqual(await reader.readexactly(4), b'open')
        value = json.loads(self.config_file.read_text()); value['limits'] = {'perApp': 1, 'perBinding': 1}
        temporary = self.config_file.with_suffix('.new'); temporary.write_text(json.dumps(value)); temporary.replace(self.config_file)
        expected = hashlib.sha256(self.config_file.read_bytes()).hexdigest()
        for _ in range(100):
            if self.status_file.exists() and json.loads(self.status_file.read_text()).get('configHash') == expected:break
            await asyncio.sleep(.02)
        else:self.fail('Proxy did not acknowledge new policy')
        writer.write(b'kept'); await writer.drain(); self.assertEqual(await reader.readexactly(4), b'kept')
        blocked, other = await asyncio.open_connection('127.0.0.1', self.local_port); self.clients.append(other)
        self.assertEqual(await asyncio.wait_for(blocked.read(), 1), b'')
        value['limits'] = {'perApp': 2, 'perBinding': 2}; temporary.write_text(json.dumps(value)); temporary.replace(self.config_file)
        await asyncio.sleep(.12)
        accepted, third = await asyncio.open_connection('127.0.0.1', self.local_port); self.clients.append(third)
        third.write(b'next'); await third.drain(); self.assertEqual(await asyncio.wait_for(accepted.readexactly(4), 1), b'next')

    async def wait_ready(self):
        for _ in range(100):
            if self.status_file.exists():
                try:
                    value = json.loads(self.status_file.read_text())
                    if value.get('ready'): return value
                except ValueError: pass
            if self.proxy and self.proxy.returncode is not None: self.fail('Sidecar exited')
            await asyncio.sleep(.02)
        self.fail('Sidecar did not become ready')

    async def asyncTearDown(self):
        if self.proxy:
            if self.proxy.returncode is None: self.proxy.terminate()
            stdout, stderr = await asyncio.wait_for(self.proxy.communicate(), 3)
            self.assertNotIn(TOKEN.encode(), stdout + stderr)
        await super().asyncTearDown()

    async def test_sidecar_binds_only_loopback_and_streams_payload(self):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.local_port); self.clients.append(writer)
        payload = b'GET /realtime HTTP/1.1\r\nUpgrade: websocket\r\n\r\n' + b'\x81\x83\x00\x01\xff\x00' * 10000
        writer.write(payload); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(len(payload)), 2), payload)
        with self.assertRaises((OSError, asyncio.TimeoutError)): await asyncio.wait_for(asyncio.open_connection('127.0.0.2', self.local_port), .5)
        check = await asyncio.create_subprocess_exec(NODE, str(PROXY), '--check', '--config', str(self.config_file), '--status', str(self.status_file))
        self.assertEqual(await check.wait(), 0)

    async def test_sidecar_removes_revoked_listeners_and_existing_streams(self):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.local_port); self.clients.append(writer)
        writer.write(b'live'); await writer.drain(); self.assertEqual(await reader.readexactly(4), b'live')
        self.write_config([])
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
        await asyncio.sleep(.06)
        with self.assertRaises(OSError): await asyncio.open_connection('127.0.0.1', self.local_port)

    async def test_sidecar_rejects_bad_config_and_checker_rejects_stale_hash(self):
        self.config_file.write_text('{')
        await asyncio.sleep(.1)
        self.assertFalse(self.status_file.exists())
        with self.assertRaises(OSError): await asyncio.open_connection('127.0.0.1', self.local_port)
        self.write_config([{'id': BINDING, 'port': self.local_port, 'token': TOKEN, 'revision': REVISION}]); await self.wait_ready()
        self.proxy.send_signal(signal.SIGSTOP)
        try:
            state = json.loads(self.status_file.read_text()); state['checkedAt'] = 0; self.status_file.write_text(json.dumps(state))
            check = await asyncio.create_subprocess_exec(NODE, str(PROXY), '--check', '--config', str(self.config_file), '--status', str(self.status_file))
            self.assertEqual(await check.wait(), 1)
        finally: self.proxy.send_signal(signal.SIGCONT)

    async def run_probe(self, ids=BINDING, socket_path=None):
        process = await asyncio.create_subprocess_exec(NODE, str(PROXY), '--probe', ids, '--config', str(self.config_file),
            '--socket', str(socket_path or self.broker.socket_path(APP)), '--handshake-ms', '100',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 2)
        self.assertEqual(stdout, b''); self.assertEqual(stderr, b'')
        return process.returncode

    async def test_startup_probe_waits_for_broker_ack_without_sending_http(self):
        await self.broker.apply_configuration(config(None, available=False))
        self.assertEqual(await self.run_probe(), 1)
        self.assertEqual(self.upstream_bytes, 0)
        await self.broker.apply_configuration(config(self.target))
        self.assertEqual(await self.run_probe(), 0)
        self.assertEqual(self.upstream_bytes, 0)
        self.assertEqual(await self.run_probe(SECOND), 1)
        self.assertEqual(await self.run_probe(BINDING + ',' + BINDING), 1)

    async def test_startup_probe_rejects_oversized_missing_and_negative_ack(self):
        path = self.root / 'ack.sock'
        for answer in [b'ERR UNAVAILABLE\n', b'x' * 1025, b'OK', b'{"ok":true}\n']:
            with self.subTest(answer=answer[:32]):
                async def rejected(reader, writer):
                    await reader.readline(); writer.write(answer); await writer.drain()
                    if b'\n' not in answer and len(answer) <= 1024: await asyncio.sleep(.2)
                    writer.close()
                server = await asyncio.start_unix_server(rejected, str(path))
                try: self.assertEqual(await self.run_probe(socket_path=path), 1)
                finally: server.close(); await server.wait_closed(); path.unlink(missing_ok=True)


@unittest.skipUnless(NODE, 'Node is required for the namespace-pinned target')
class TargetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='kdt-', dir='/tmp')
        self.root = pathlib.Path(self.temporary.name).resolve(); os.chown(self.root, -1, os.getgid())
        self.path, self.status = self.root / 't.sock', self.root / 'status.json'
        self.upstreams, self.clients = set(), []
        self.server = await asyncio.start_server(self.echo, '127.0.0.1', 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.process = None

    async def echo(self, reader, writer):
        self.upstreams.add(writer)
        try:
            while True:
                data = await reader.read(65536)
                if not data: break
                writer.write(data); await writer.drain()
        except (OSError, ConnectionError): pass
        finally: self.upstreams.discard(writer); writer.close()

    def options(self, **extra):
        return {'socket': str(self.path), 'status': str(self.status), 'tcpPort': self.port, 'pollMs': 40, **extra}

    async def launch(self, **extra):
        script = "const t=require(process.argv[1]).createTarget(JSON.parse(process.argv[2]));t.ready.catch(()=>{process.exitCode=1});process.on('SIGTERM',()=>t.stop());"
        self.process = await asyncio.create_subprocess_exec(NODE, '-e', script, str(TARGET), json.dumps(self.options(**extra)),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        for _ in range(100):
            if self.status.exists(): return
            if self.process.returncode is not None: self.fail('Target exited')
            await asyncio.sleep(.02)
        self.fail('Target did not become ready')

    async def check(self):
        script = "require(process.argv[1]).check(JSON.parse(process.argv[2])).then(ok=>process.exitCode=ok?0:1)"
        process = await asyncio.create_subprocess_exec(NODE, '-e', script, str(TARGET), json.dumps(self.options()))
        return await process.wait()

    async def asyncTearDown(self):
        for writer in self.clients: writer.close()
        if self.process:
            if self.process.returncode is None: self.process.terminate()
            await asyncio.wait_for(self.process.communicate(), 3)
        self.server.close(); await self.server.wait_closed()
        for writer in self.upstreams: writer.close()
        self.temporary.cleanup()

    async def test_target_streams_http_websocket_and_binary_without_interpretation(self):
        await self.launch()
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o660); self.assertEqual(await self.check(), 0)
        reader, writer = await asyncio.open_unix_connection(str(self.path)); self.clients.append(writer)
        payload = b'GET /api/realtime HTTP/1.1\r\nUpgrade: websocket\r\n\r\n' + bytes(range(256)) * 2048
        async def send(): writer.write(payload); await writer.drain()
        received, _ = await asyncio.gather(reader.readexactly(len(payload)), send()); self.assertEqual(received, payload)
        writer.write_eof(); self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')

    async def test_target_policy_changes_without_restarting_or_closing_existing_connections(self):
        policy = self.root / 'limits.json'; policy.write_text(json.dumps({'maxConnections': 1}))
        await self.launch(limits=str(policy))
        reader, writer = await asyncio.open_unix_connection(str(self.path)); self.clients.append(writer)
        writer.write(b'one'); await writer.drain(); self.assertEqual(await reader.readexactly(3), b'one')
        blocked, other = await asyncio.open_unix_connection(str(self.path)); self.clients.append(other)
        self.assertEqual(await asyncio.wait_for(blocked.read(), 1), b'')
        policy.write_text(json.dumps({'maxConnections': 2}))
        accepted, third = await asyncio.open_unix_connection(str(self.path)); self.clients.append(third)
        third.write(b'two'); await third.drain(); self.assertEqual(await accepted.readexactly(3), b'two')
        policy.write_text(json.dumps({'maxConnections': 1}))
        writer.write(b'kept'); await writer.drain(); self.assertEqual(await reader.readexactly(4), b'kept')

    async def test_database_stop_removes_readiness_and_cannot_select_a_new_port(self):
        await self.launch(); self.assertEqual(await self.check(), 0)
        self.server.close(); await self.server.wait_closed()
        for writer in list(self.upstreams): writer.close()
        await asyncio.sleep(.1)
        self.assertFalse(self.status.exists()); self.assertEqual(await self.check(), 1)
        reader, writer = await asyncio.open_unix_connection(str(self.path)); self.clients.append(writer)
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
        process = await asyncio.create_subprocess_exec(NODE, str(TARGET), '--port', str(self.port),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await process.communicate(); self.assertNotEqual(process.returncode, 0)

    async def test_connection_count_and_idle_timeout_are_bounded(self):
        await self.launch(maxConnections=1, idleMs=150)
        reader, writer = await asyncio.open_unix_connection(str(self.path)); self.clients.append(writer)
        writer.write(b'live'); await writer.drain(); self.assertEqual(await reader.readexactly(4), b'live')
        second, other = await asyncio.open_unix_connection(str(self.path)); self.clients.append(other)
        self.assertEqual(await asyncio.wait_for(second.read(), 1), b'')
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')

    async def test_existing_symlink_is_not_removed_or_followed(self):
        outside = self.root / 'outside'; outside.write_text('preserved'); self.path.symlink_to(outside)
        script = "require(process.argv[1]).createTarget(JSON.parse(process.argv[2])).ready.catch(()=>process.exitCode=1)"
        self.process = await asyncio.create_subprocess_exec(NODE, '-e', script, str(TARGET), json.dumps(self.options()),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await asyncio.wait_for(self.process.wait(), 2)
        self.assertNotEqual(self.process.returncode, 0); self.assertTrue(self.path.is_symlink()); self.assertEqual(outside.read_text(), 'preserved')


@unittest.skipUnless(NODE, 'Node is required for the startup wrapper')
class WaitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='private db wait '); self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name).resolve(); self.ready = self.root / 'ready'; self.namespace = self.root / 'netns'
        self.namespace.symlink_to('net:[123456]')
        self.pid_stat = self.root / 'pid-stat'; self.write_stat(100)

    def write_stat(self, ticks):
        self.pid_stat.write_text('1 (init (trusted)) ' + ' '.join(['S'] + ['0'] * 18 + [str(ticks)] + ['0'] * 8))

    def command(self, child, timeout=500):
        return [NODE, str(WAIT), '--ready-file', str(self.ready), '--netns-file', str(self.namespace), '--pid-stat-file', str(self.pid_stat), '--environment-file', str(self.root / 'environment.json'), '--timeout-ms', str(timeout), '--', NODE, '-e', child]

    def test_current_namespace_is_required_and_arguments_are_not_interpreted_by_shell(self):
        self.ready.write_text('net:[123456]:100')
        marker = self.root / 'never-created'; literal = '$(touch ' + str(marker) + '); literal'
        command = self.command('console.log(process.argv[1]);process.exit(23)') + [literal]
        result = subprocess.run(command, text=True, capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 23); self.assertEqual(result.stdout.strip(), literal); self.assertFalse(marker.exists())

    def test_previous_namespace_missing_and_symlink_markers_cannot_start_app(self):
        for mode in ['previous', 'same_namespace_previous_start', 'namespace_only', 'missing', 'symlink', 'fifo']:
            with self.subTest(mode=mode):
                if self.ready.exists() or self.ready.is_symlink(): self.ready.unlink()
                if mode == 'previous': self.ready.write_text('net:[999999]:100')
                elif mode == 'same_namespace_previous_start': self.ready.write_text('net:[123456]:99')
                elif mode == 'namespace_only': self.ready.write_text('net:[123456]')
                elif mode == 'symlink':
                    target = self.root / 'target'; target.write_text('net:[123456]:100'); self.ready.symlink_to(target)
                elif mode == 'fifo': os.mkfifo(self.ready)
                result = subprocess.run(self.command('console.log("SHOULD_NOT_START")', timeout=50), text=True, capture_output=True, timeout=2)
                self.assertEqual(result.returncode, 75); self.assertNotIn('SHOULD_NOT_START', result.stdout)

    def test_signals_reach_the_child_and_exit_status_is_preserved(self):
        self.ready.write_text('net:[123456]:100')
        process = subprocess.Popen(self.command('process.on("SIGTERM",()=>process.exit(42));console.log("READY");setInterval(()=>{},1000)'), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'READY'); process.terminate(); stdout, stderr = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 42)
        finally:
            if process.poll() is None: process.kill(); process.communicate()

    def test_preload_environment_is_restored_only_after_readiness(self):
        marker = self.root / 'preload-ran'
        preload = self.root / 'preload.cjs'; preload.write_text('require("node:fs").writeFileSync(' + json.dumps(str(marker)) + ',"yes")')
        environment_file = self.root / 'environment.json'
        environment_file.write_text(json.dumps({'version': 1, 'environment': {'NODE_OPTIONS': '--require=' + json.dumps(str(preload)), 'NODE_PATH': '/app/extra', 'LD_LIBRARY_PATH': '/app/lib', 'LD_PRELOAD': ''}}))
        # No startup capability exists yet: reading this trusted restoration file
        # must not cause a preload to execute in the wrapper process.
        result = subprocess.run(self.command('console.log("STARTED")', timeout=50), text=True, capture_output=True, timeout=2)
        self.assertEqual(result.returncode, 75); self.assertFalse(marker.exists())
        self.ready.write_text('net:[123456]:100')
        result = subprocess.run(self.command('console.log(process.env.NODE_PATH)'), text=True, capture_output=True, timeout=2)
        self.assertEqual(result.returncode, 0); self.assertEqual(result.stdout.strip(), '/app/extra'); self.assertTrue(marker.exists())
        environment_file.write_text(json.dumps({'version': 1, 'environment': {'OTHER_SECRET': TOKEN}}))
        result = subprocess.run(self.command('console.log("STARTED")'), text=True, capture_output=True, timeout=2)
        self.assertEqual(result.returncode, 75); self.assertNotIn('STARTED', result.stdout); self.assertNotIn(TOKEN, result.stderr)

    def test_reused_network_namespace_waits_for_current_pid1_start_ticks(self):
        self.ready.write_text('net:[123456]:100'); self.write_stat(200)
        process = subprocess.Popen(self.command('console.log("CURRENT_START")', timeout=1000), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            time.sleep(.15); self.assertIsNone(process.poll(), 'A stale marker must not release the new wrapper')
            self.ready.write_text('net:[123456]:200')
            stdout, stderr = process.communicate(timeout=2)
            self.assertEqual(process.returncode, 0); self.assertEqual(stdout.strip(), 'CURRENT_START')
        finally:
            if process.poll() is None: process.kill(); process.communicate()


if __name__ == '__main__':
    unittest.main()
