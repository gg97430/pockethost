#!/usr/bin/env python3
"""Synthetic, read-only reader and trust provisioning tests. Never contacts a host."""
import base64
import importlib.util
import json
import os
import pathlib
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

HERE = pathlib.Path(__file__).resolve().parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = load('fleet_reader', 'platform_fleet_reader.py')
installer = load('fleet_install', 'install-platform-fleet-reader.py')
KEY = 'ssh-ed25519 ' + base64.b64encode(b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20' + b'x' * 32).decode()


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.database = self.root / 'data.db'
        self.metrics = self.root / 'resources.json'
        self.monitor = self.root / 'monitoring.json'
        with sqlite3.connect(self.database) as db:
            db.executescript("CREATE TABLE users(id TEXT, email TEXT, verified BOOLEAN, superAdmin BOOLEAN, suspension TEXT, password TEXT); CREATE TABLE instances(id TEXT, subdomain TEXT, status TEXT, secret TEXT); CREATE TABLE web_apps(id TEXT, name TEXT, status TEXT, config TEXT); CREATE TABLE web_deployments(status TEXT, finishedAt TEXT);")
            db.executemany('INSERT INTO users VALUES (?,?,?,?,?,?)', [
                ('a' * 15, 'admin@example.invalid', 1, 1, '', 'secret-user-password'),
                ('b' * 15, 'member@example.invalid', 1, 0, '', 'secret-user-password'),
                ('c' * 15, 'unverified@example.invalid', 0, 1, '', 'secret-user-password'),
                ('d' * 15, 'suspended@example.invalid', 1, 1, 'reason', 'secret-user-password'),
            ])
            db.execute('INSERT INTO instances VALUES (?,?,?,?)', ('e' * 15, 'Test database', 'running', 'secret-native-token'))
            db.execute('INSERT INTO web_apps VALUES (?,?,?,?)', ('f' * 15, '<script>Fixture</script>', 'running', '{"secret":"private-value"}'))
            db.execute("INSERT INTO web_deployments VALUES ('queued','')")
            db.execute("INSERT INTO web_deployments VALUES ('failed','2999-01-01 00:00:00')")
        self.metrics.write_text(json.dumps({'available': True, 'sample': {'version': 1, 'collectedAt': m.timestamp(), 'host': {'cpuPercent': 12.5, 'memoryTotalBytes': 1024, 'memoryUsedBytes': 512, 'diskTotalBytes': 2048, 'diskUsedBytes': 1024, 'password': 'cache-secret'}, 'resources': [{'kind': 'database', 'id': 'e' * 15, 'cpuPercent': 1, 'memoryBytes': 123, 'diskBytes': 234}]}}))
        self.monitor.write_text(json.dumps({'version': 1, 'checkedAt': m.timestamp(), 'state': 'ok', 'counts': {'error': 0}, 'checks': [{'state': 'error', 'detail': 'secret detail'}]}))

    def tearDown(self):
        self.temp.cleanup()

    def local(self, email='admin@example.invalid'):
        return m.local_snapshot(email, 'app4', self.database, self.metrics, self.monitor)

    def test_remote_actor_must_be_verified_active_superadmin_every_read(self):
        for email in ['member@example.invalid', 'unverified@example.invalid', 'suspended@example.invalid', 'absent@example.invalid']:
            with self.assertRaises(m.AccessDenied):
                self.local(email)
        self.assertEqual(self.local('ADMIN@EXAMPLE.INVALID')['access'], 'granted')
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE users SET suspension='now blocked' WHERE email='admin@example.invalid'")
        with self.assertRaises(m.AccessDenied):
            self.local()

    def test_snapshot_only_selects_explicit_public_fields_and_recomputes_health(self):
        before = self.database.read_bytes()
        value = self.local()
        self.assertEqual(value['counts']['databasesRunning'], 1)
        self.assertEqual(value['counts']['applications'], 1)
        self.assertEqual(value['counts']['deploymentsQueued'], 1)
        self.assertEqual(value['counts']['deploymentsFailed24h'], 1)
        self.assertEqual(value['inventory'][0]['memoryBytes'], 123)
        self.assertEqual(value['monitoring']['state'], 'error')
        self.assertEqual(value['monitoring']['counts']['error'], 1)
        self.assertFalse(value['metrics']['stale'])
        for secret in ['secret-user-password', 'secret-native-token', 'private-value', 'cache-secret', 'secret detail']:
            self.assertNotIn(secret, json.dumps(value))
        self.assertEqual(before, self.database.read_bytes())

    def test_missing_corrupt_and_stale_cache_never_prove_health(self):
        self.metrics.unlink()
        self.monitor.write_text('{bad json')
        value = self.local()
        self.assertIsNone(value['metrics'])
        self.assertIsNone(value['monitoring'])
        self.assertEqual(value['inventory'][0]['memoryBytes'], None)
        self.monitor.write_text(json.dumps({'version': 1, 'checkedAt': '2000-01-01T00:00:00Z', 'checks': [{'state': 'ok'}]}))
        self.assertTrue(self.local()['monitoring']['stale'])
        self.assertTrue(m.old('2999-01-01T00:00:00Z', 180, 1))

    def test_inventory_limit_explicitly_reports_incomplete(self):
        with patch.object(m, 'LIMIT', 1):
            value = self.local()
        self.assertEqual(len(value['inventory']), 1)
        self.assertTrue(value['truncated'])
        self.assertEqual(value['counts']['applications'], 1)

    def test_requests_allow_no_host_path_command_or_privilege_parameters(self):
        for value in [{'version': 1, 'email': 'admin@example.invalid', 'host': 'app1'}, {'version': 1, 'email': 'admin@example.invalid\nwhoami'}, {'version': 1, 'email': '$(whoami)'}, {'version': 1, 'email': 'admin@example.invalid', 'superAdmin': True}]:
            with self.assertRaises(ValueError):
                m.request(value)
        self.assertEqual(m.request({'version': 1, 'email': 'ADMIN@EXAMPLE.INVALID'}), 'admin@example.invalid')
        self.assertEqual(set(m.PEERS), {'app3', 'app4', 'app5'})

    def test_socket_protocol_is_bounded_and_requires_message_eof(self):
        sender, receiver = socket.socketpair()
        with sender, receiver:
            sender.sendall(b'{"version":1}')
            sender.shutdown(socket.SHUT_WR)
            self.assertEqual(m.receive(receiver, 32), b'{"version":1}')
        sender, receiver = socket.socketpair()
        with sender, receiver:
            sender.sendall(b'x' * 33)
            sender.shutdown(socket.SHUT_WR)
            with self.assertRaises(ValueError):m.receive(receiver, 32)

    def test_unprivileged_socket_client_accepts_only_root_socket_and_peer(self):
        content=json.dumps({'version':1,'email':'ADMIN@example.invalid'})
        info=os.stat_result((stat.S_IFSOCK|0o660,0,0,1,0,1000,0,0,0,0))
        connection=MagicMock();connection.__enter__.return_value=connection
        connection.recv.side_effect=[json.dumps({'version':1,'servers':[]}).encode(),b'']
        with patch.object(m.os,'geteuid',return_value=1000),patch.object(m.pathlib.Path,'lstat',return_value=info),patch.object(m.socket,'socket',return_value=connection),patch.object(m,'peer_uid',return_value=0):
            self.assertEqual(m.local_request(content)['version'],1)
            self.assertEqual(json.loads(connection.sendall.call_args.args[0]),{'version':1,'email':'admin@example.invalid'})
            connection.shutdown.assert_called_once_with(socket.SHUT_WR)
        with patch.object(m.os,'geteuid',return_value=1001),patch.object(m.socket,'socket')as connect:
            with self.assertRaises(PermissionError):m.local_request(content)
            connect.assert_not_called()
        for change in [{0:stat.S_IFSOCK|0o666},{0:stat.S_IFLNK|0o660},{4:1000},{5:1001}]:
            bad=list(info)
            for key,value in change.items():bad[key]=value
            with patch.object(m.os,'geteuid',return_value=1000),patch.object(m.pathlib.Path,'lstat',return_value=os.stat_result(bad)),patch.object(m.socket,'socket')as connect:
                with self.assertRaises(ValueError):m.local_request(content)
                connect.assert_not_called()
        with patch.object(m.os,'geteuid',return_value=1000),patch.object(m.pathlib.Path,'lstat',return_value=info),patch.object(m.socket,'socket',return_value=connection),patch.object(m,'peer_uid',return_value=1000):
            with self.assertRaises(PermissionError):m.local_request(content)

    def test_ssh_uses_pinned_fixed_target_root_key_and_stdin_no_commands(self):
        def run(args, **kwargs):
            self.assertEqual(args[-1], 'kolab-fleet@217.182.134.72')
            self.assertIn('StrictHostKeyChecking=yes', args)
            self.assertIn('IdentityAgent=none', args)
            self.assertIn('ClearAllForwardings=yes', args)
            self.assertIn('HostKeyAlgorithms=ssh-ed25519', args)
            self.assertNotIn('admin@example.invalid', args)
            self.assertEqual(json.loads(kwargs['input']), {'version': 1, 'email': 'admin@example.invalid'})
            self.assertEqual(kwargs['env'], {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
            kwargs['stdout'].write(json.dumps(m.unavailable('app5', 'denied')).encode())
            return subprocess.CompletedProcess(args, 0)
        with patch.object(m.subprocess, 'run', run):
            self.assertEqual(m.remote_snapshot('app5', 'admin@example.invalid')['access'], 'denied')
        with patch.object(m.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ssh', 8)):
            self.assertEqual(m.remote_snapshot('app5', 'admin@example.invalid')['access'], 'unavailable')

    def test_ssh_wrong_peer_or_error_output_is_not_trusted_or_exposed(self):
        def run(args, **kwargs):
            kwargs['stdout'].write(json.dumps({'key': 'app3', 'hostname': 'evil', 'access': 'granted', 'password': 'do-not-return'}).encode())
            return subprocess.CompletedProcess(args, 0)
        with patch.object(m.subprocess, 'run', run):
            value = m.remote_snapshot('app5', 'admin@example.invalid')
        self.assertEqual(value['access'], 'unavailable')
        self.assertNotIn('do-not-return', json.dumps(value))

    def test_public_key_strict_shape_and_no_option_injection(self):
        self.assertEqual(m.public_key(KEY + ' trusted-comment'), KEY)
        self.assertEqual(installer.public_key(KEY + ' trusted-comment'), KEY)
        for value in ['ssh-rsa AAAA', KEY + '\ncommand="sh"', KEY + ' a b', 'ssh-ed25519 ' + base64.b64encode(b'not-a-key').decode()]:
            for module in [m, installer]:
                with self.assertRaises(ValueError):
                    module.public_key(value)

    def test_trust_files_deny_symlink_non_root_and_writable(self):
        path = self.root / 'trust'
        path.write_text('trusted')
        path.chmod(0o600)
        # macOS tests need no privilege; mock only ownership, not symlink checks.
        actual = os.fstat
        def root_info(fd):
            info = list(actual(fd)); info[4] = 0; return os.stat_result(info)
        with patch.object(m.os, 'fstat', root_info):
            self.assertEqual(m.trusted_file(path, 100, 0o077), b'trusted')
            path.chmod(0o666)
            with self.assertRaises(ValueError): m.trusted_file(path, 100)
            link = self.root / 'link'; link.symlink_to(path)
            with self.assertRaises(OSError): m.trusted_file(link, 100)

    def test_install_connect_preserves_unmanaged_keys_no_service_restart(self):
        root, home = self.root / 'config', self.root / 'home'
        (root / 'keys').mkdir(parents=True)
        (home / '.ssh').mkdir(parents=True)
        (root / 'keys/app5').write_text('private-key-stays-local')
        authorized = home / '.ssh/authorized_keys'
        authorized.write_text('# existing unrelated comment\n')
        def read(path, maximum=16384, private=False):
            return pathlib.Path(path).read_bytes()
        def write(path, content, mode=0o600):
            pathlib.Path(path).write_bytes(content)
            pathlib.Path(path).chmod(mode)
        payload = {'version': 1, 'self': 'app4', 'peers': [{'key': 'app5', 'publicKey': KEY, 'hostKey': KEY}]}
        with patch.object(installer, 'ROOT', root), patch.object(installer, 'HOME', home), patch.object(installer, 'identity'), patch.object(installer, 'read', read), patch.object(installer, 'write', write), patch.object(installer.subprocess, 'run', side_effect=AssertionError('No service operation')):
            self.assertFalse(installer.connect(payload)['restartRequired'])
            first = authorized.read_bytes()
            installer.connect(payload)
            self.assertEqual(authorized.read_bytes(), first)
            self.assertIn('from="217.182.134.72",restrict,command="/usr/bin/sudo -n /usr/bin/python3 -I /opt/pockethost-web/platform_fleet_reader.py serve"', first.decode())
            self.assertEqual(authorized.stat().st_mode & 0o777, 0o644)
            self.assertNotIn('private-key-stays-local', (root / 'config.json').read_text())
            self.assertEqual(json.loads((root / 'config.json').read_text())['peers'], ['app5'])
            self.assertEqual((root / 'known_hosts').read_text(), '217.182.134.72 ' + KEY + '\n')
            with self.assertRaises(ValueError): installer.connect({**payload, 'peers': payload['peers'] * 2})

    def test_runtime_drops_root_for_sqlite_before_threads_and_restores_for_ssh(self):
        info = list(self.database.lstat()); info[4] = 1000; info = os.stat_result(info)
        calls = []
        with patch.object(m, 'DATABASE', self.database), patch.object(m.pathlib.Path, 'lstat', return_value=info), patch.object(m.os, 'geteuid', return_value=0), patch.object(m.threading, 'active_count', return_value=1), patch.object(m.os, 'getegid', return_value=123), patch.object(m.os, 'getgroups', return_value=[123]), patch.object(m.os, 'setgroups', side_effect=lambda v:calls.append(('groups',v))), patch.object(m.os, 'setegid', side_effect=lambda v:calls.append(('gid',v))), patch.object(m.os, 'seteuid', side_effect=lambda v:calls.append(('uid',v))), patch.object(m, 'local_snapshot', side_effect=lambda email,name:calls.append(('snapshot',email,name)) or {'safe':True}):
            self.assertEqual(m.controller_snapshot('admin@example.invalid','app4'), {'safe':True})
        self.assertEqual(calls,[('groups',[]),('gid',1000),('uid',1000),('snapshot','admin@example.invalid','app4'),('uid',0),('gid',123),('groups',[123])])


if __name__ == '__main__':
    unittest.main()
