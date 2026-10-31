#!/usr/bin/env python3
import importlib.util
import io
import json
import os
import pathlib
import sqlite3
import tempfile
import types
import unittest
from unittest import mock

import web_platform_recovery as engine
import web_platform_recovery_probe as probe

PAUSED = {'smtp': {'enabled': False}, 's3': {'enabled': False}, 'backups': {'cron': '', 's3': {'enabled': False}}}


def fixture(root, params='CREATE TABLE _params(id TEXT,value BLOB)', column='id', instances=(), binaries=()):
    root.chmod(0o700); database = root / 'controller/pb_data'; database.mkdir(parents=True)
    with sqlite3.connect(database / 'data.db') as connection:
        connection.execute(params); connection.execute(f'INSERT INTO _params({column},value) VALUES(?,?)', ('settings', json.dumps(PAUSED).encode()))
        connection.execute('CREATE TABLE instances(id TEXT,version TEXT)'); connection.executemany('INSERT INTO instances VALUES(?,?)', instances)
    files = [{'target': target, 'size': 4, 'sha256': sha} for target, sha in binaries]
    manifest = {'backupId': 'capture', 'databases': [{'id': 'controller', 'target': 'controller/pb_data'}], 'files': files}
    for name, value in [('RECOVERY_PAUSED.json', {'backupId': 'capture', 'paused': True, 'noServicesStarted': True, 'databasesRestored': ['controller'], 'hostFilesRestored': False}), ('recovery-manifest.json', manifest)]:
        target = root / name; target.write_text(json.dumps(value)); target.chmod(0o600)
    return manifest


class ProbeTests(unittest.TestCase):
    def test_namespace_requires_both_host_difference_and_only_loopback(self):
        with mock.patch.object(probe.os, 'stat', side_effect=lambda path: types.SimpleNamespace(st_ino=200 if 'self' in str(path) else 100)), mock.patch.object(pathlib.Path, 'read_text', return_value='header\nheader\nlo: 0\n'):
            self.assertTrue(probe.isolated_network(100)); self.assertFalse(probe.isolated_network(200))
        with mock.patch.object(probe.os, 'stat', return_value=types.SimpleNamespace(st_ino=200)), mock.patch.object(pathlib.Path, 'read_text', return_value='header\nheader\nlo: 0\neth0: 0\n'):
            self.assertFalse(probe.isolated_network(100))
    def test_runtime_versions_are_captured_exact_matches(self):
        self.assertEqual(probe.resolve_version('0.39.*', ['0.39.8', '0.39.11', '0.40.6']), '0.39.11')
        with self.assertRaises(engine.RecoveryError): probe.resolve_version('0.39.7', ['0.39.11'])
        with self.assertRaises(engine.RecoveryError): probe.resolve_version('../../source', ['0.39.11'])
    def test_probe_requires_suspended_native_settings_and_all_file_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve(); root.chmod(0o700); database = root / 'controller/pb_data'; database.mkdir(parents=True)
            with sqlite3.connect(database / 'data.db') as connection:
                connection.execute('CREATE TABLE _params(id TEXT,value BLOB)'); connection.execute('INSERT INTO _params VALUES(?,?)', ('settings', json.dumps({'smtp': {'enabled': False}, 's3': {'enabled': False}, 'backups': {'cron': '', 's3': {'enabled': False}}}).encode()))
            captured = root / 'file'; captured.write_bytes(b'original')
            manifest = {'backupId': 'capture', 'databases': [{'id': 'controller', 'target': 'controller/pb_data'}], 'files': [{'target': 'file', 'size': 8, 'sha256': probe.digest(captured)}]}
            for name, value in [('RECOVERY_PAUSED.json', {'backupId': 'capture', 'paused': True, 'noServicesStarted': True, 'databasesRestored': ['controller']}), ('recovery-manifest.json', manifest)]:
                f = root / name; f.write_text(json.dumps(value)); f.chmod(0o600)
            self.assertEqual(probe.verify_restore(root, 1)[-1]['files'], 1)
            captured.write_bytes(b'changed!')
            with self.assertRaises(engine.RecoveryError): probe.verify_restore(root, 1)
            captured.write_bytes(b'original')
            with sqlite3.connect(database / 'data.db') as connection:
                connection.execute('UPDATE _params SET value=?', (json.dumps({'smtp': {'enabled': True}}).encode(),))
            with self.assertRaises(engine.RecoveryError): probe.verify_restore(root, 1)
    def test_native_start_is_rejected_before_spawning_without_isolation(self):
        with mock.patch.object(probe, 'isolated_network', return_value=False), mock.patch.object(probe.subprocess, 'Popen') as spawn:
            with self.assertRaises(engine.RecoveryError): probe.native_probe(None, {}, {}, None, None, {})
        spawn.assert_not_called()
    def test_mfa_key_is_checked_from_database_manifest_bytes_and_private_mode_before_native(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve(); root.chmod(0o700); folder = root / 'controller/pb_data'; folder.mkdir(parents=True)
            with sqlite3.connect(folder / 'data.db') as connection:
                connection.executescript('CREATE TABLE _params(id TEXT,value BLOB);CREATE TABLE ph_two_factor(enabled INTEGER,secret TEXT,pendingSecret TEXT);')
                connection.execute('INSERT INTO _params VALUES(?,?)', ('settings', json.dumps({'smtp': {'enabled': False}, 's3': {'enabled': False}, 'backups': {'cron': '', 's3': {'enabled': False}}}).encode()))
                connection.execute('INSERT INTO ph_two_factor VALUES(?,?,?)', (1, 'preserved-encrypted-secret', 'preserved-encrypted-pending'))
            key = folder / engine.TOTP_SIDECAR; key.write_bytes(b'K' * 32); key.chmod(0o600)
            manifest = {'backupId': 'mfa-capture', 'databases': [{'id': 'controller', 'target': 'controller/pb_data', 'mfa': {'configured': True, 'keyCaptured': True}}], 'files': [{'target': 'controller/pb_data/' + engine.TOTP_SIDECAR, 'size': 32, 'sha256': probe.digest(key)}]}
            for name, value in [('RECOVERY_PAUSED.json', {'backupId': 'mfa-capture', 'paused': True, 'noServicesStarted': True, 'databasesRestored': ['controller']}), ('recovery-manifest.json', manifest)]:
                file = root / name; file.write_text(json.dumps(value)); file.chmod(0o600)
            self.assertEqual(probe.verify_restore(root, 1)[-1]['mfaSidecars'], 1)
            key.chmod(0o644)
            with self.assertRaises(engine.RecoveryError): probe.verify_restore(root, 1)
            key.chmod(0o600); key.write_bytes(b'X' * 32)
            with self.assertRaises(engine.RecoveryError): probe.verify_restore(root, 1)
            key.unlink()
            with self.assertRaises(engine.RecoveryError): probe.verify_restore(root, 1)
            manifest['databases'][0]['mfa'] = {'configured': False, 'keyCaptured': False}; manifest['files'] = []; (root / 'recovery-manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(engine.RecoveryError): probe.verify_restore(root, 1)

    def test_selected_database_probe_ignores_unselected_key_and_unrequested_host_files_but_requires_its_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve(); root.chmod(0o700); folder = root / 'controller/pb_data'; folder.mkdir(parents=True)
            with sqlite3.connect(folder / 'data.db') as connection:
                connection.executescript('CREATE TABLE _params(id TEXT,value BLOB);CREATE TABLE ph_two_factor(enabled INTEGER,secret TEXT,pendingSecret TEXT);')
                connection.execute('INSERT INTO _params VALUES(?,?)', ('settings', json.dumps({'smtp': {'enabled': False}, 's3': {'enabled': False}, 'backups': {'cron': '', 's3': {'enabled': False}}}).encode()))
                connection.execute('INSERT INTO ph_two_factor VALUES(?,?,?)', (1, 'encrypted-selected', ''))
            key = folder / engine.TOTP_SIDECAR; key.write_bytes(b'S' * 32); key.chmod(0o600)
            other = 'a' * 15
            manifest = {'backupId': 'selected-mfa', 'databases': [{'id': 'controller', 'target': 'controller/pb_data', 'mfa': {'configured': True, 'keyCaptured': True}}, {'id': other, 'target': f'instances/{other}/pb_data', 'mfa': {'configured': True, 'keyCaptured': True}}], 'files': [{'target': 'controller/pb_data/' + engine.TOTP_SIDECAR, 'size': 32, 'sha256': probe.digest(key)}, {'target': f'instances/{other}/pb_data/' + engine.TOTP_SIDECAR, 'size': 32, 'sha256': '0' * 64}, {'target': 'host-files/configuration/private.env', 'size': 100, 'sha256': '0' * 64}]}
            for name, value in [('RECOVERY_PAUSED.json', {'backupId': 'selected-mfa', 'paused': True, 'noServicesStarted': True, 'databasesRestored': ['controller'], 'hostFilesRestored': False, 'nativeArchivesRestored': False}), ('recovery-manifest.json', manifest)]:
                file = root / name; file.write_text(json.dumps(value)); file.chmod(0o600)
            proof = probe.verify_restore(root, 1)[-1]; self.assertEqual(proof['mfaSidecars'], 1); self.assertEqual(proof['files'], 1); self.assertFalse(proof['hostFilesRestored'])
            key.unlink()
            with self.assertRaises(engine.RecoveryError): probe.verify_restore(root, 1)

    def test_legacy_key_settings_schema_is_verified_like_the_engine_pause(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve(); fixture(root, 'CREATE TABLE _params(key TEXT,value TEXT)', 'key')
            self.assertEqual(probe.verify_restore(root, 1)[-1]['databases'][0]['nativeSettingsPaused'], True)
            with sqlite3.connect(root / 'controller/pb_data/data.db') as connection: connection.execute('UPDATE _params SET value=?', (json.dumps({**PAUSED, 'smtp': {'enabled': True}}),))
            with self.assertRaises(engine.RecoveryError): probe.verify_restore(root, 1)
    def test_binary_lookup_follows_the_host_architecture(self):
        self.assertEqual(probe.native_arch('x86_64'), 'linux_amd64'); self.assertEqual(probe.native_arch('aarch64'), 'linux_arm64'); self.assertEqual(probe.native_arch('arm64'), 'linux_arm64')
        with self.assertRaises(engine.RecoveryError): probe.native_arch('riscv64')
        with tempfile.TemporaryDirectory() as temporary:
            base = pathlib.Path(temporary).resolve(); root = base / 'restore'; root.mkdir(); runtime = base / 'pocketbase'
            binary = runtime / '0.39.2/linux_arm64/pocketbase'; binary.parent.mkdir(parents=True); binary.write_bytes(b'arm!'); binary.chmod(0o755)
            manifest = fixture(root, binaries=[('host-files/platform/pocketbase/0.39.2/linux_arm64/pocketbase', probe.digest(binary)), ('host-files/platform/pocketbase/0.39.9/linux_amd64/pocketbase', '0' * 64)])
            definitions = probe.load_restore(root, 1)[-1]
            self.assertEqual(probe.native_binaries(root, manifest, definitions, runtime, '0.39.*', 'linux_arm64'), {'controller': ('0.39.2', binary)})
            with self.assertRaises(engine.RecoveryError): probe.native_binaries(root, manifest, definitions, runtime, '0.39.*', 'linux_amd64')
            binary.write_bytes(b'evil')
            with self.assertRaises(engine.RecoveryError): probe.native_binaries(root, manifest, definitions, runtime, '0.39.*', 'linux_arm64')
    def test_native_attempt_directory_retries_after_failure_and_refuses_foreign_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            first, removed = probe.native_directory(root); (first / 'controller.log').write_text('failed attempt'); self.assertEqual(removed, 0)
            second, removed = probe.native_directory(root); self.assertEqual(removed, 1); self.assertFalse(first.exists()); self.assertTrue(second.is_dir())
            self.assertEqual((root / 'native-probe').stat().st_mode & 0o777, 0o700)
            (root / 'native-probe' / 'operator-notes.txt').write_text('keep')
            with self.assertRaises(engine.RecoveryError): probe.native_directory(root)
            self.assertTrue((root / 'native-probe' / 'operator-notes.txt').exists())
            other = pathlib.Path(temporary).resolve() / 'other'; other.mkdir(); (other / 'native-probe').symlink_to(root)
            with self.assertRaises(engine.RecoveryError): probe.native_directory(other)
    def test_native_proof_dry_run_prints_isolated_systemd_command_after_hash_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = pathlib.Path(temporary).resolve(); root = base / 'restore'; root.mkdir(); runtime = base / 'pocketbase'; reports = base / 'reports'; reports.mkdir()
            binary = runtime / '0.39.2/linux_amd64/pocketbase'; binary.parent.mkdir(parents=True); binary.write_bytes(b'amd!'); binary.chmod(0o755)
            fixture(root, binaries=[('host-files/platform/pocketbase/0.39.2/linux_amd64/pocketbase', probe.digest(binary))])
            user = probe.pwd.getpwuid(os.getuid()).pw_name
            arguments = ['--root', str(root), '--expected-databases', '1', '--report', str(reports / 'native.json'), '--runtime-root', str(runtime), '--controller-version', '0.39.*', '--user', user, '--arch', 'linux_amd64']
            output = io.StringIO()
            with mock.patch.object(probe.subprocess, 'run') as spawn, mock.patch('sys.stdout', output): self.assertEqual(probe.native_proof(arguments), 0)
            spawn.assert_not_called(); plan = json.loads(output.getvalue())
            self.assertEqual(plan['mode'], 'dry-run'); self.assertTrue(plan['hashesVerified']); self.assertEqual(plan['binaries'], {'controller': '0.39.2'})
            command = plan['command']
            for value in ['PrivateNetwork=yes', 'IPAddressDeny=any', 'NoNewPrivileges=yes', '--wait', '--collect', '--host-netns-inode', '--uid=' + str(os.getuid()), 'ReadWritePaths=' + str(root)]: self.assertIn(value, command)
            self.assertNotIn('--verify-only', command); self.assertEqual(plan['shell'], probe.shlex.join(command))
            self.assertIn('RuntimeMaxSec=1800', command); self.assertEqual(plan['maxSeconds'], 1800)
            output = io.StringIO()  # Large databases (app3): the whole unit budget is configurable, bounded.
            with mock.patch.object(probe.subprocess, 'run'), mock.patch('sys.stdout', output): self.assertEqual(probe.native_proof([*arguments, '--max-seconds', '14400']), 0)
            self.assertIn('RuntimeMaxSec=14400', json.loads(output.getvalue())['command']); self.assertNotIn('RuntimeMaxSec=1800', json.loads(output.getvalue())['command'])
            for bad in ('60', '86400'):
                with self.assertRaises(engine.RecoveryError): probe.native_proof([*arguments, '--max-seconds', bad])
            with self.assertRaises(engine.RecoveryError): probe.native_proof([*arguments[:4], '--report', str(root / 'inside.json'), *arguments[6:]])
            binary.write_bytes(b'evil')
            with self.assertRaises(engine.RecoveryError): probe.native_proof(arguments)
            with mock.patch.object(probe.pwd, 'getpwnam', return_value=types.SimpleNamespace(pw_uid=0, pw_gid=0)):
                with self.assertRaises(engine.RecoveryError): probe.native_proof(arguments)

    def test_copy_left_in_wal_mode_by_a_native_start_stays_checkable(self):
        with tempfile.TemporaryDirectory() as temporary:
            filename = pathlib.Path(temporary).resolve() / 'data.db'
            connection = sqlite3.connect(filename); connection.execute('PRAGMA journal_mode=WAL'); connection.execute('CREATE TABLE t(x)'); connection.commit(); connection.close()
            for suffix in ['-wal', '-shm']: pathlib.Path(str(filename) + suffix).unlink(missing_ok=True)
            before = filename.read_bytes()
            with probe.readonly(filename) as connection:
                self.assertEqual(connection.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
                with self.assertRaises(sqlite3.OperationalError): connection.execute('INSERT INTO t VALUES(1)')
            self.assertEqual(filename.read_bytes(), before)


if __name__ == '__main__': unittest.main()
