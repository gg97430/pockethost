#!/usr/bin/env python3
"""No production services: authenticated streaming captures and inert, isolated rebuilds."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('platform_recovery', pathlib.Path(__file__).with_name('web_platform_recovery.py'))
r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)
dump_sql = lambda source: b'\n'.join(b''.join(parts) for parts in r.dump_database(source)).decode()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.root = pathlib.Path(self.temporary.name).resolve()
        self.key = bytes(range(32)); self.keyfile = self.root / 'outer.key'; self.keyfile.write_text(self.key.hex()); self.keyfile.chmod(0o600)
        self.instance_id = 'a' * 15; self.collection_id = 'b' * 15; self.record_id = 'c' * 15
        self.controller = self.root / 'controller.db'; self.instance = self.root / 'instance.db'; self.storage = self.root / 'storage'
        with sqlite3.connect(self.controller) as db:
            db.executescript('CREATE TABLE instances(id TEXT PRIMARY KEY,name TEXT);CREATE TABLE web_apps(id TEXT,status TEXT);CREATE TABLE web_deployments(id TEXT,status TEXT);CREATE TABLE web_scheduled_tasks(id TEXT,enabled INTEGER);CREATE TABLE web_app_health(id TEXT,enabled INTEGER);CREATE TABLE web_projects(id TEXT,previewEnabled INTEGER);CREATE TABLE _params(key TEXT,value TEXT);CREATE TABLE _superusers(id TEXT,tokenKey TEXT);')
            db.execute('INSERT INTO instances VALUES(?,?)', (self.instance_id, 'Fixture instance'))
            db.execute("INSERT INTO web_apps VALUES('app','running')"); db.execute("INSERT INTO web_deployments VALUES('job','queued')"); db.execute("INSERT INTO web_scheduled_tasks VALUES('task',1)"); db.execute("INSERT INTO web_app_health VALUES('health',1)"); db.execute("INSERT INTO web_projects VALUES('project',1)"); db.execute("INSERT INTO _superusers VALUES('admin','old-token-key')")
            db.execute('INSERT INTO _params VALUES(?,?)', ('settings', json.dumps({'smtp': {'enabled': True, 'password': 'smtp-private-synthetic'}, 'backups': {'cron': '0 * * * *', 's3': {'enabled': True}}, 'filesystem': {'s3': {'enabled': True}}})))
        with sqlite3.connect(self.instance) as db:
            db.executescript('CREATE TABLE _collections(id TEXT,name TEXT,type TEXT,fields TEXT);CREATE TABLE documents(id TEXT,attachment TEXT,payload TEXT);')
            db.execute('INSERT INTO _collections VALUES(?,?,?,?)', (self.collection_id, 'documents', 'base', json.dumps([{'name': 'attachment', 'type': 'file'}])))
            db.execute('INSERT INTO documents VALUES(?,?,?)', (self.record_id, 'document.txt', 'private-record-$HOME-\nsecond-line'))
        filename = self.storage / self.collection_id / self.record_id / 'document.txt'; filename.parent.mkdir(parents=True); filename.write_bytes(b'private-original-file')
        self.host = self.root / 'source-hooks.pb.js'; self.host.write_text('throw new Error("must-never-execute")')
        self.plan = {'version': 1, 'platform': 'app1', 'backupId': '20261029T000000Z-test', 'controller': {'database': str(self.controller)}, 'instances': [{'id': self.instance_id, 'database': str(self.instance), 'storage': str(self.storage)}], 'hostPaths': [{'source': str(self.host), 'target': 'hooks/source-hooks.pb.js'}], 'metadata': {'versions': {'pocketbase': 'fixture'}}}
        self.disk = mock.patch.object(r.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=100 * 1024**3)); self.disk.start()

    def tearDown(self): self.disk.stop(); self.temporary.cleanup()

    def capture(self, plan=None, notify=None):
        output = io.BytesIO(); manifest = r.capture_to_sink(plan or self.plan, self.key, self.keyfile, output, notify)
        return output.getvalue(), manifest

    def restore(self, data, target='restored', select=None, host_files=False):
        destination = self.root / target
        marker = r.restore_archive(lambda: io.BytesIO(data), self.key, destination, select, host_files)
        return destination, marker

    def mfa_sources(self):
        plan = {**self.plan, 'controller': {**self.plan['controller']}, 'instances': [{**self.plan['instances'][0]}]}
        keys = {}
        for ident, definition in [('controller', plan['controller']), (self.instance_id, plan['instances'][0])]:
            folder = self.root / ('mfa-' + ident); folder.mkdir(mode=0o700)
            filename = folder / 'data.db'; r.shutil.copyfile(definition['database'], filename); definition['database'] = str(filename)
            keyfile = folder / r.TOTP_SIDECAR; value = (b'C' if ident == 'controller' else b'I') * 32 + b'\n'; keyfile.write_bytes(value); keyfile.chmod(0o600); keys[ident] = (keyfile, value)
            with sqlite3.connect(filename) as db:
                db.execute('CREATE TABLE ph_two_factor(enabled INTEGER,secret TEXT,pendingSecret TEXT)')
                db.execute('INSERT INTO ph_two_factor VALUES(?,?,?)', (1, 'encrypted-secret-' + ident, 'encrypted-pending-' + ident))
        return plan, keys

    def test_stream_capture_and_complete_authenticated_paused_restore(self):
        original = self.controller.read_bytes(); data, manifest = self.capture()
        self.assertNotIn(b'private-record', data); self.assertNotIn(b'smtp-private', data); self.assertNotIn(self.key.hex().encode(), data)
        self.assertEqual(r.inspect_archive(lambda: io.BytesIO(data), self.key)['backupId'], self.plan['backupId'])
        destination, marker = self.restore(data, host_files=True)
        self.assertTrue(marker['paused']); self.assertTrue(marker['noServicesStarted']); self.assertTrue(marker['requiredSystemd']['PrivateNetwork'])
        self.assertFalse(manifest['coverage']['databaseAndFilesAtomic'])
        with sqlite3.connect(destination / 'controller/pb_data/data.db') as db:
            self.assertEqual(db.execute('SELECT enabled FROM web_scheduled_tasks').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT enabled FROM web_app_health').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT previewEnabled FROM web_projects').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT status FROM web_deployments').fetchone()[0], 'cancelled')
            self.assertEqual(db.execute('SELECT status FROM web_apps').fetchone()[0], 'stopped')
            settings = json.loads(db.execute('SELECT value FROM _params').fetchone()[0]); self.assertFalse(settings['smtp']['enabled']); self.assertEqual(settings['backups']['cron'], ''); self.assertFalse(settings['filesystem']['s3']['enabled'])
            self.assertNotEqual(db.execute('SELECT tokenKey FROM _superusers').fetchone()[0], 'old-token-key')
        with sqlite3.connect(destination / f'instances/{self.instance_id}/pb_data/data.db') as db:
            self.assertEqual(db.execute('SELECT payload FROM documents').fetchone()[0], 'private-record-$HOME-\nsecond-line')
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual((destination / f'instances/{self.instance_id}/pb_data/storage/{self.collection_id}/{self.record_id}/document.txt').read_bytes(), b'private-original-file')
        hook = destination / 'host-files/hooks/source-hooks.pb.js'; self.assertEqual(hook.stat().st_mode & 0o777, 0o600)
        self.assertFalse((destination / 'controller/pb_hooks').exists()); self.assertEqual(list((destination / '.disabled-hooks').iterdir()), [])
        self.assertEqual(self.controller.read_bytes(), original)

    def test_tamper_and_wrong_key_fail_before_target_materialization(self):
        data, _ = self.capture(); corrupted = data[:-1] + bytes([data[-1] ^ 1])
        with self.assertRaises(r.RecoveryError): self.restore(corrupted, 'corrupt-target')
        self.assertFalse((self.root / 'corrupt-target').exists())
        with self.assertRaises(r.RecoveryError): r.restore_archive(lambda: io.BytesIO(data), b'x' * 32, self.root / 'wrong-key')
        self.assertFalse((self.root / 'wrong-key').exists())

    def test_existing_destination_and_symlink_sources_are_rejected(self):
        data, _ = self.capture(); target = self.root / 'existing'; target.mkdir(); marker = target / 'do-not-touch'; marker.write_text('existing')
        with self.assertRaises(r.RecoveryError): self.restore(data, 'existing')
        self.assertEqual(marker.read_text(), 'existing')
        source = self.storage / 'bad-link'; source.symlink_to(self.keyfile)
        with self.assertRaises(r.RecoveryError): self.capture()

    def test_referenced_file_missing_aborts_instead_of_claiming_protection(self):
        (self.storage / self.collection_id / self.record_id / 'document.txt').unlink()
        with self.assertRaisesRegex(r.RecoveryError, 'référencés'): self.capture()

    def test_unreadable_directory_aborts_instead_of_silently_omitting_files(self):
        def denied_walk(*_args, **kwargs):
            kwargs['onerror'](PermissionError('private source path'))
            yield  # Match the lazy os.walk API.
        with mock.patch.object(r.os, 'walk', denied_walk):
            with self.assertRaisesRegex(r.RecoveryError, 'illisible'): r.regular_tree(self.storage)

    def test_host_dependency_directories_are_excluded_before_symbolic_link_validation(self):
        source = self.root / 'source-project'; source.mkdir(); (source / 'source.py').write_text('restorable source')
        (source / 'node_modules').symlink_to(self.storage, target_is_directory=True)
        data, manifest = self.capture({**self.plan, 'hostPaths': [{'source': str(source), 'target': 'project'}]})
        self.assertEqual([item['target'] for item in manifest['files'] if item['target'].startswith('host-files/')], ['host-files/project/source.py'])
        (source / 'unsafe-link').symlink_to(self.keyfile)
        with self.assertRaises(r.RecoveryError): self.capture({**self.plan, 'hostPaths': [{'source': str(source), 'target': 'project'}]})

    def test_controller_instance_omission_requires_explicit_partial_scope(self):
        plan = {**self.plan, 'instances': []}
        with self.assertRaisesRegex(r.RecoveryError, 'instances'): self.capture(plan)
        _, manifest = self.capture({**plan, 'allowPartial': True})
        self.assertFalse(manifest['coverage']['complete']); self.assertEqual(manifest['coverage']['instancesMissing'], [self.instance_id])

    def test_live_sqlite_writes_continue_and_read_snapshot_keeps_old_rows(self):
        writer = sqlite3.connect(self.instance); writer.execute('PRAGMA journal_mode=WAL'); writer.execute('PRAGMA wal_autocheckpoint=0'); writer.execute('CREATE TABLE live_wal_marker(value INTEGER)'); writer.commit()
        def notify(stage, ident):
            if stage == 'snapshot' and ident == self.instance_id:
                writer.execute('INSERT INTO documents VALUES(?,?,?)', ('d' * 15, '', 'new-after-snapshot')); writer.commit()
        try: data, manifest = self.capture(notify=notify)
        finally: writer.close()
        destination, _ = self.restore(data)
        with sqlite3.connect(self.instance) as db: self.assertEqual(db.execute('SELECT count(*) FROM documents').fetchone()[0], 2)
        with sqlite3.connect(destination / f'instances/{self.instance_id}/pb_data/data.db') as db: self.assertEqual(db.execute('SELECT count(*) FROM documents').fetchone()[0], 1)
        self.assertEqual(next(value for value in manifest['databases'] if value['id'] == self.instance_id)['tables']['documents'], 1)

    def test_text_nul_unicode_binary_and_numeric_values_round_trip_without_sqlite_quote_truncation(self):
        value = 'prefix\x00after-nul-😀-é\nline\x00end'
        with sqlite3.connect(self.instance) as db: db.execute('UPDATE documents SET payload=?', (value,))
        data, _ = self.capture(); destination, _ = self.restore(data)
        with sqlite3.connect(destination / f'instances/{self.instance_id}/pb_data/data.db') as db:
            self.assertEqual(db.execute('SELECT payload FROM documents').fetchone()[0], value)
        with sqlite3.connect(':memory:') as source, sqlite3.connect(':memory:') as target:
            source.execute('CREATE TABLE "z_odd\'\""table"(id INTEGER PRIMARY KEY AUTOINCREMENT,value TEXT,raw BLOB,real_value REAL,nullable TEXT)')
            source.execute('INSERT INTO "z_odd\'\""table"(value,raw,real_value,nullable) VALUES(?,?,?,?)', (value, b'\x00\xff\x01\x00', 1.2345678901234567, None))
            source.execute('INSERT INTO "z_odd\'\""table"(id,value) VALUES(?,?)', (1000, 'deleted-sequence-value')); source.execute('DELETE FROM "z_odd\'\""table" WHERE id=1000')
            target.executescript(dump_sql(source))
            self.assertEqual(source.execute('SELECT * FROM "z_odd\'\""table"').fetchall(), target.execute('SELECT * FROM "z_odd\'\""table"').fetchall())
            self.assertEqual(target.execute('SELECT seq FROM sqlite_sequence').fetchone()[0], 1000)

    def test_views_exist_before_their_instead_of_triggers_are_reconstructed(self):
        with sqlite3.connect(':memory:') as source, sqlite3.connect(':memory:') as target:
            source.executescript('CREATE TABLE records(value TEXT);CREATE VIEW editable AS SELECT value FROM records;CREATE TRIGGER edit_view INSTEAD OF INSERT ON editable BEGIN INSERT INTO records(value) VALUES(new.value);END;INSERT INTO editable VALUES("original");')
            target.executescript(dump_sql(source)); target.execute('INSERT INTO editable VALUES(?)', ('after-restore',))
            self.assertEqual(target.execute('SELECT value FROM records').fetchall(), [('original',), ('after-restore',)])

    def test_orphaned_empty_sqlite_sequence_after_dropped_table_is_preserved(self):
        with sqlite3.connect(':memory:') as source, sqlite3.connect(':memory:') as target:
            source.executescript('CREATE TABLE removed(id INTEGER PRIMARY KEY AUTOINCREMENT);DROP TABLE removed;CREATE TABLE records(value TEXT);INSERT INTO records VALUES("original");')
            target.executescript(dump_sql(source))
            self.assertEqual(target.execute('SELECT count(*) FROM sqlite_sequence').fetchone()[0], 0)
            self.assertEqual(target.execute('SELECT value FROM records').fetchone()[0], 'original')
            self.assertIsNone(target.execute("SELECT name FROM sqlite_master WHERE name LIKE '__pockethost_recovery_sequence_init%'").fetchone())

    def test_session_invalidation_preserves_business_columns_named_token_key(self):
        with sqlite3.connect(self.controller) as db:
            db.executescript('CREATE TABLE business_cache(id TEXT,tokenKey TEXT);CREATE TABLE app_auth(id TEXT,tokenKey TEXT);CREATE TABLE _collections(id TEXT,name TEXT,type TEXT,fields TEXT);')
            db.execute('INSERT INTO business_cache VALUES(?,?)', ('business', 'exact-business-\x00-value'))
            db.execute('INSERT INTO app_auth VALUES(?,?)', ('user', 'old-auth-signing-key'))
            db.execute('INSERT INTO _collections VALUES(?,?,?,?)', ('auth-fixture', 'app_auth', 'auth', '[]'))
        data, _ = self.capture(); destination, _ = self.restore(data)
        with sqlite3.connect(destination / 'controller/pb_data/data.db') as db:
            self.assertEqual(db.execute('SELECT tokenKey FROM business_cache').fetchone()[0], 'exact-business-\x00-value')
            self.assertNotEqual(db.execute('SELECT tokenKey FROM app_auth').fetchone()[0], 'old-auth-signing-key')

    def test_offline_pauses_do_not_fire_business_sql_triggers_and_definitions_survive(self):
        with sqlite3.connect(self.controller) as db:
            db.executescript('CREATE TABLE auth_audit(value TEXT);CREATE TRIGGER audit_token_change AFTER UPDATE OF tokenKey ON _superusers BEGIN INSERT INTO auth_audit VALUES("audit-event");END;')
        data, _ = self.capture(); destination, _ = self.restore(data)
        with sqlite3.connect(destination / 'controller/pb_data/data.db') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM auth_audit').fetchone()[0], 0)
            self.assertIsNotNone(db.execute("SELECT sql FROM sqlite_master WHERE name='audit_token_change'").fetchone())
            db.execute('UPDATE _superusers SET tokenKey=?', ('future-key',))
            self.assertEqual(db.execute('SELECT count(*) FROM auth_audit').fetchone()[0], 1)

    def test_unsupported_virtual_tables_and_utf16_fail_closed(self):
        with sqlite3.connect(':memory:') as source:
            source.execute('CREATE VIRTUAL TABLE search USING fts5(body)')
            with self.assertRaisesRegex(r.RecoveryError, 'virtuelle'): list(r.dump_database(source))
        with sqlite3.connect(':memory:') as source:
            source.execute("PRAGMA encoding='UTF-16le'"); source.execute('CREATE TABLE example(value TEXT)')
            with self.assertRaisesRegex(r.RecoveryError, 'UTF-8'): list(r.dump_database(source))

    def test_text_bytes_with_invalid_utf8_are_preserved_without_python_decoding(self):
        with sqlite3.connect(':memory:') as source, sqlite3.connect(':memory:') as target:
            source.execute('CREATE TABLE records(value TEXT)'); source.execute("INSERT INTO records VALUES(CAST(X'61FF0062' AS TEXT))")
            target.executescript(dump_sql(source))
            self.assertEqual(target.execute('SELECT typeof(value),hex(value) FROM records').fetchone(), ('text', '61FF0062'))

    def test_wal_growth_and_disk_reserve_guards_fail_closed(self):
        wal = pathlib.Path(str(self.instance) + '-wal'); guard = r.SourceGuard(self.root, self.instance, wal_limit=8)
        wal.write_bytes(bytes(16))
        with self.assertRaisesRegex(r.RecoveryError, 'WAL'): guard.check(force=True)
        wal.unlink()
        with mock.patch.object(r.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=r.MIN_FREE - 1)):
            with self.assertRaisesRegex(r.RecoveryError, '15 Gio'): self.capture()

    def test_selected_drill_skips_other_databases_without_skipping_authentication(self):
        data, _ = self.capture(); destination, marker = self.restore(data, select=['controller'])
        self.assertEqual(marker['databasesRestored'], ['controller']); self.assertFalse((destination / 'instances').exists()); self.assertFalse((destination / 'host-files').exists())

    def test_bulk_reconstruction_groups_inserts_in_transactions(self):
        data, _ = self.capture(); connect = sqlite3.connect; traces = []
        def instrument(*args, **kwargs):
            connection = connect(*args, **kwargs)
            if str(args[0]).endswith('data.db.reconstructing'): connection.set_trace_callback(traces.append)
            return connection
        with mock.patch.object(r.sqlite3, 'connect', instrument): self.restore(data)
        inside, inserts, largest = False, 0, 0
        for sql in traces:
            if sql.strip().upper().startswith('BEGIN'): inside = True; inserts = 0
            elif sql.strip().upper().startswith('COMMIT'): largest = max(largest, inserts); inside = False
            elif sql.strip().upper().startswith('INSERT'):
                self.assertTrue(inside); inserts += 1
        self.assertGreater(largest, 1)

    def test_large_text_rows_keep_capture_peak_memory_bounded(self):
        # Peak RSS of a fresh process, not tracemalloc: hex()||concat in SQL held about 13 native
        # copies of each large TEXT value (2 GiB OOM in production on a 5.7 GB base).
        database, size = self.root / 'large.db', 16 * 1024**2
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE records(id TEXT,payload TEXT,extra TEXT)')
            for index in range(2): db.execute('INSERT INTO records VALUES(?,?,?)', (str(index), os.urandom(size // 2).hex(), 'x\x00y'))
        code = '''import importlib.util, resource, sys, types
from unittest import mock
from cryptography.hazmat.primitives.ciphers import Cipher
spec = importlib.util.spec_from_file_location('r', sys.argv[1]); r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)
sink = types.SimpleNamespace(write=len); peak = lambda: resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
with mock.patch.object(r.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=100 * 1024**3)):
    before = peak(); manifest = r.capture_to_sink({'version': 1, 'platform': 'app1', 'backupId': 'memory', 'controller': {'database': sys.argv[2]}}, bytes(range(32)), sys.argv[3], sink)
print(peak() - before, manifest['databases'][0]['tables']['records'])'''
        result = subprocess.run([sys.executable, '-c', code, spec.origin, str(database), str(self.keyfile)], capture_output=True, text=True, check=True)
        delta, rows = map(int, result.stdout.split())
        self.assertEqual(rows, 2); self.assertLess(delta, 10 * size, f'pic mémoire {delta // 1024**2} Mio pour des lignes de {size // 1024**2} Mio')

    def test_many_authenticated_sql_blocks_restore_all_rows_and_nul_text(self):
        with sqlite3.connect(self.instance) as db:
            for index in range(40):
                db.execute('INSERT INTO documents VALUES(?,?,?)', (f'e{index:014d}', '', f'row-{index}\x00' + 'full-payload-😀' * 60))
            original = db.execute('SELECT id,attachment,payload FROM documents ORDER BY id').fetchall()
        with mock.patch.object(r, 'SQL_PART', 128):
            data, manifest = self.capture(); destination, marker = self.restore(data)
        self.assertTrue(all(len(value['sqlObjects']) > 3 for value in manifest['databases']))
        with sqlite3.connect(destination / f'instances/{self.instance_id}/pb_data/data.db') as db:
            self.assertEqual(db.execute('SELECT id,attachment,payload FROM documents ORDER BY id').fetchall(), original)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertTrue(marker['paused'])

    def test_private_outer_key_and_its_exclusion_are_enforced(self):
        with mock.patch.dict(os.environ, {'WEB_BACKUPS_S3_IMAGE_KEY': self.key.hex()}):
            with self.assertRaises(r.RecoveryError): r.load_key(self.keyfile)
        self.keyfile.chmod(0o644)
        with self.assertRaises(r.RecoveryError): r.load_key(self.keyfile)
        self.keyfile.chmod(0o600)
        plan = {**self.plan, 'hostPaths': [{'source': str(self.keyfile), 'target': 'forbidden.key'}]}
        with self.assertRaises(r.RecoveryError): self.capture(plan)

    def test_mfa_sidecars_follow_database_restore_with_without_host_files_and_selected_subset(self):
        plan, keys = self.mfa_sources(); data, manifest = self.capture(plan)
        self.assertEqual(sum(item.get('source') == 'pockethost-totp-key' for item in manifest['files']), 2)
        self.assertNotIn((b'C' * 32).decode(), json.dumps(manifest)); self.assertNotIn(b'C' * 32, data)
        for host_files in [False, True]:
            target, _ = self.restore(data, 'mfa-' + str(host_files), host_files=host_files)
            for ident, (_source, value) in keys.items():
                base = target / ('controller/pb_data' if ident == 'controller' else f'instances/{ident}/pb_data')
                key = base / r.TOTP_SIDECAR; self.assertEqual(key.read_bytes(), value); self.assertEqual(key.stat().st_mode & 0o777, 0o600)
                with sqlite3.connect(base / 'data.db') as db:
                    self.assertEqual(db.execute('SELECT secret,pendingSecret FROM ph_two_factor').fetchone(), ('encrypted-secret-' + ident, 'encrypted-pending-' + ident))
        target, _ = self.restore(data, 'mfa-selected', select=[self.instance_id])
        self.assertFalse((target / 'controller').exists())
        self.assertEqual((target / f'instances/{self.instance_id}/pb_data/{r.TOTP_SIDECAR}').read_bytes(), keys[self.instance_id][1])

    def test_mfa_missing_key_pending_configuration_and_changed_key_fail_closed(self):
        for enabled, secret, pending in [(1, 'encrypted', ''), (0, '', 'pending-encrypted'), (1, '', '')]:
            with self.subTest(enabled=enabled, pending=bool(pending)):
                with sqlite3.connect(self.instance) as db:
                    db.execute('CREATE TABLE IF NOT EXISTS ph_two_factor(enabled INTEGER,secret TEXT,pendingSecret TEXT)'); db.execute('DELETE FROM ph_two_factor'); db.execute('INSERT INTO ph_two_factor VALUES(?,?,?)', (enabled, secret, pending))
                with self.assertRaisesRegex(r.RecoveryError, 'Clé TOTP manquante'): self.capture()
        # This case deliberately restores size/mtime: the private content digest
        # must bind the key to the SQL snapshot, rather than trusting only stat.
        with sqlite3.connect(self.instance) as db: db.execute('DROP TABLE ph_two_factor')
        plan, keys = self.mfa_sources(); filename = keys['controller'][0]
        def change_key(phase, ident):
            if phase == 'snapshot' and ident == 'controller':
                before = filename.stat(); filename.write_bytes(b'Z' * before.st_size); os.utime(filename, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(r.RecoveryError, 'clé TOTP'): self.capture(plan, change_key)

    def test_mfa_sidecar_whitelist_and_outer_key_hardlink_exclusion(self):
        plan, _keys = self.mfa_sources(); _data, manifest = self.capture(plan)
        value = next(item for item in manifest['files'] if item.get('source') == 'pockethost-totp-key'); value['target'] = 'controller/pb_data/.unexpected-secret'
        with self.assertRaises(r.RecoveryError): r.validate_manifest(manifest, {item['name']: item for item in manifest['objects']})
        os.link(self.keyfile, self.root / r.TOTP_SIDECAR)
        with self.assertRaisesRegex(r.RecoveryError, 'lien physique'): self.capture()

    def test_only_copied_dependencies_cover_active_images_and_fresh_volumes(self):
        image = 'sha256:' + 'd' * 64; app = 'e' * 15
        with sqlite3.connect(':memory:') as db:
            db.executescript('CREATE TABLE web_apps(id TEXT,activeDeployment TEXT);CREATE TABLE web_deployments(id TEXT,result TEXT);CREATE TABLE web_storage_settings(appId TEXT,volumes TEXT);')
            db.execute('INSERT INTO web_apps VALUES(?,?)', (app, 'published'))
            db.execute('INSERT INTO web_deployments VALUES(?,?)', ('published', json.dumps({'imageId': image})))
            db.execute('INSERT INTO web_storage_settings VALUES(?,?)', (app, json.dumps([{'name': 'uploads'}])))
            references = [{'appId': app, 'imageId': image}, {'appId': app, 'volume': 'uploads', 'snapshotAt': r.stamp()}]
            with self.assertRaises(r.RecoveryError): r.web_dependency_coverage(db, {'s3Refs': references})
            self.assertEqual(r.web_dependency_coverage(db, {'capturedS3Refs': references}), ([], [], []))
            references[1]['snapshotAt'] = '2020-01-01T00:00:00Z'
            with self.assertRaises(r.RecoveryError): r.web_dependency_coverage(db, {'capturedS3Refs': references})

    def test_ephemeral_previews_do_not_block_capture_and_cannot_restart_after_restore(self):
        app = 'e' * 15
        with sqlite3.connect(self.controller) as db:
            db.executescript("ALTER TABLE web_apps ADD COLUMN activeDeployment TEXT DEFAULT '';ALTER TABLE web_apps ADD COLUMN desiredCommit TEXT DEFAULT '';ALTER TABLE web_deployments ADD COLUMN result TEXT DEFAULT '{}';CREATE TABLE web_app_environments(appId TEXT,environment TEXT,state TEXT);")
            db.execute('INSERT INTO web_apps(id,status,activeDeployment,desiredCommit) VALUES(?,?,?,?)', (app, 'running', 'preview-job', 'preview-commit'))
            db.execute('INSERT INTO web_app_environments VALUES(?,?,?)', (app, 'preview', 'ready'))
        data, manifest = self.capture(); destination, marker = self.restore(data)
        self.assertEqual(manifest['coverage']['ephemeralPreviewsExcluded'], [app]); self.assertTrue(manifest['coverage']['complete'])
        with sqlite3.connect(destination / 'controller/pb_data/data.db') as db:
            self.assertIsNone(db.execute('SELECT id FROM web_apps WHERE id=?', (app,)).fetchone())
            self.assertEqual(db.execute('SELECT appId,state FROM web_app_environments').fetchone(), ('', 'closed'))
        self.assertTrue(marker['paused'])

    def test_recovery_keys_replace_metadata_and_share_one_web_prefix(self):
        prefix = 'app1/web-app-backups'; recovered = 'app1/platform-recovery/capture/refs/web'
        archive, image, volume = [prefix + '/apps/' + self.instance_id + '/' + tail for tail in ['backups/example.phwb', 'images/image.phwi', 'volumes/uploads/volume.phwv']]
        manifest = {'s3Refs': [{'originalKey': value, 'recoveryKey': recovered + value[len(prefix):], 'sourcePrefix': prefix, 'recoveryPrefix': recovered, 'group': 'web'} for value in [archive, image, volume]]}
        with sqlite3.connect(':memory:') as db:
            db.executescript('CREATE TABLE web_app_backups(id TEXT,s3Object TEXT,s3ImageObject TEXT,s3ImageObjects TEXT);CREATE TABLE web_storage_jobs(id TEXT,result TEXT);')
            db.execute('INSERT INTO web_app_backups VALUES(?,?,?,?)', ('backup', archive, image, json.dumps({'sha256:example': image})))
            db.execute('INSERT INTO web_storage_jobs VALUES(?,?)', ('volume', json.dumps({'object': volume, 'unrelated': 'unchanged'})))
            result = r.remap_metadata(db, manifest)
            self.assertEqual(result['environment'], {'WEB_BACKUPS_S3_PREFIX': recovered})
            row = db.execute('SELECT s3Object,s3ImageObject,s3ImageObjects FROM web_app_backups').fetchone()
            self.assertTrue(all(prefix not in value for value in row)); self.assertEqual(json.loads(row[2])['sha256:example'], recovered + image[len(prefix):])
            self.assertEqual(json.loads(db.execute('SELECT result FROM web_storage_jobs').fetchone()[0]), {'object': recovered + volume[len(prefix):], 'unrelated': 'unchanged'})
        ambiguous = {'s3Refs': [*manifest['s3Refs'], {**manifest['s3Refs'][0], 'recoveryPrefix': recovered + '-other', 'recoveryKey': recovered + '-other/apps/other'}]}
        with self.assertRaises(r.RecoveryError): r.recovery_prefixes(ambiguous)

    def test_purge_is_confined_to_committed_platform_capture_and_refs(self):
        config = {'bucket': 'bucket', 'prefix': 'app1/platform-recovery'}; base = config['prefix'] + '/capture/'
        class Client:
            def __init__(client, references): client.deleted = []; client.references = references
            def head_object(client, **kwargs): return {'ETag': 'fixed'}
            def get_object(client, **kwargs): return {'Body': io.BytesIO(json.dumps({'backupId': 'capture', 'object': base + 'platform.phpr', 'references': client.references}).encode())}
            def delete_object(client, **kwargs): client.deleted.append(kwargs['Key'])
        client = Client([base + 'refs/web/apps/example.phwi'])
        self.assertEqual(r.purge_capture(client, config, base + 'platform.phpr'), 'capture')
        self.assertEqual(client.deleted[-2:], [base + 'platform.phpr', base + 'commit.json'])
        for invalid in ['app1/web-app-backups/apps/example.phwi', config['prefix'] + '/capture/nested/platform.phpr', config['prefix'] + '/../platform.phpr']:
            with self.assertRaises(r.RecoveryError): r.purge_capture(client, config, invalid)
        evil = Client(['app1/web-app-backups/apps/source.phwi'])
        with self.assertRaises(r.RecoveryError): r.purge_capture(evil, config, base + 'platform.phpr')
        self.assertEqual(evil.deleted, [])

    def test_multipart_is_bounded_and_copies_survive_source_reference_deletion(self):
        class S3:
            def __init__(client): client.objects = {}; client.uploads = {}; client.max_part = 0
            def create_multipart_upload(client, **kw): client.uploads['upload'] = {'key': kw['Key'], 'metadata': kw.get('Metadata', {}), 'parts': {}}; return {'UploadId': 'upload'}
            def upload_part(client, **kw): client.max_part = max(client.max_part, len(kw['Body'])); client.uploads[kw['UploadId']]['parts'][kw['PartNumber']] = kw['Body']; return {'ETag': str(kw['PartNumber'])}
            def complete_multipart_upload(client, **kw):
                upload = client.uploads[kw['UploadId']]; body = b''.join(upload['parts'][part['PartNumber']] for part in kw['MultipartUpload']['Parts']); client.objects[upload['key']] = (body, upload['metadata']); return {}
            def abort_multipart_upload(client, **kw): client.uploads.pop(kw['UploadId'], None)
            def head_object(client, **kw):
                body, metadata = client.objects[kw['Key']]; return {'ContentLength': len(body), 'ETag': hashlib.sha256(body).hexdigest(), 'Metadata': metadata}
            def copy_object(client, **kw): client.objects[kw['Key']] = client.objects[kw['CopySource']['Key']]; return {}
            def get_object(client, **kw):
                head = client.head_object(**kw)
                if kw.get('IfMatch') != head['ETag']: raise RuntimeError('precondition')
                return {**head, 'Body': io.BytesIO(client.objects[kw['Key']][0])}
        client = S3(); config = {'bucket': 'bucket', 'prefix': 'app1/platform-recovery'}
        source = 'app1/web-app-backups/apps/' + self.instance_id + '/images/immutable.phwi'; client.objects[source] = (b'encrypted-image-reference', {'kind': 'image'})
        plan = {**self.plan, 's3Refs': [{'key': source, 'prefix': 'app1/web-app-backups', 'group': 'web-backups', 'metadata': {'kind': 'image'}}]}
        copied = r.copy_references(client, config, plan, self.plan['backupId']); del client.objects[source]
        self.assertEqual(client.objects[copied[0]['recoveryKey']][0], b'encrypted-image-reference')
        sink = r.MultipartSink(client, config, 'app1/platform-recovery/capture/platform.phpr', {})
        r.capture_to_sink(self.plan, self.key, self.keyfile, sink); sink.finish()
        factory = r.s3_factory(client, config, 'app1/platform-recovery/capture/platform.phpr')
        self.assertEqual(r.inspect_archive(factory, self.key)['backupId'], self.plan['backupId'])
        self.assertLessEqual(client.max_part, r.PART)

    def test_copy_falls_back_when_provider_rejects_copy_condition(self):
        class Unsupported(Exception): response = {'Error': {'Code': 'NotImplemented'}}
        class S3:
            def __init__(client, swap=False): client.objects = {}; client.swap = swap; client.copies = 0
            def head_object(client, **kw):
                body, metadata = client.objects[kw['Key']]; return {'ContentLength': len(body), 'ETag': hashlib.sha256(body).hexdigest(), 'Metadata': metadata}
            def copy_object(client, **kw):
                if 'CopySourceIfMatch' in kw: raise Unsupported()
                client.copies += 1; client.objects[kw['Key']] = client.objects[kw['CopySource']['Key']]
                if client.swap: client.objects[kw['CopySource']['Key']] = (b'replaced', {'kind': 'image'})
                return {}
            def delete_object(client, **kw): client.objects.pop(kw['Key'], None)
        config = {'bucket': 'bucket', 'prefix': 'app1/platform-recovery'}
        source = 'app1/web-app-backups/apps/' + self.instance_id + '/images/immutable.phwi'
        plan = {**self.plan, 's3Refs': [{'key': source, 'prefix': 'app1/web-app-backups', 'group': 'web-backups', 'metadata': {'kind': 'image'}}]}
        client = S3(); client.objects[source] = (b'encrypted-image-reference', {'kind': 'image'})
        copied = r.copy_references(client, config, dict(plan), self.plan['backupId'])
        self.assertEqual((client.copies, client.objects[copied[0]['recoveryKey']][0]), (1, b'encrypted-image-reference'))
        # A source replaced during the unconditional copy is refused and the copy removed.
        client = S3(swap=True); client.objects[source] = (b'encrypted-image-reference', {'kind': 'image'})
        with self.assertRaises(r.RecoveryError): r.copy_references(client, config, dict(plan), self.plan['backupId'])
        self.assertEqual(sorted(client.objects), [source])

    def test_settings_lookup_prefers_id_then_legacy_key_and_keeps_storage_class(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE _params(id TEXT,key TEXT,value TEXT)')
            db.execute('INSERT INTO _params VALUES(?,?,?)', ('', 'settings', json.dumps({'s3': {'enabled': False}, 'smtp': {'enabled': True}})))
            self.assertEqual(r.settings_records(db)[0][:2], ('key', 'settings'))  # Legacy key column found when id holds no settings.
            db.execute('INSERT INTO _params VALUES(?,?,?)', ('settings', '', json.dumps({'smtp': {'enabled': True}})))
            self.assertEqual(r.settings_records(db)[0][:2], ('id', 'settings'))
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE _params(key TEXT,value TEXT)'); db.execute('INSERT INTO _params VALUES(?,?)', ('settings', json.dumps({'smtp': {'enabled': True, 'host': 'mail.invalid'}, 's3': {'enabled': False}, 'backups': {'cron': '0 3 * * *'}})))
            state = {}; r.pause_database(db, state)
            raw = db.execute('SELECT value FROM _params').fetchone()[0]; self.assertIsInstance(raw, str); self.assertFalse(json.loads(raw)['smtp']['enabled'])
            self.assertEqual(state['nativeSettings'][0], {'column': 'key', 'name': 'settings', 'smtp': True, 's3': False, 'filesystemS3': None, 'backupsCron': '0 3 * * *', 'backupsS3': None})
            self.assertIsNone(r.native_storage_config(db))

    def test_restore_records_exact_pre_pause_state_for_real_loss_unpause(self):
        with sqlite3.connect(self.controller) as db:
            db.execute("INSERT INTO web_app_health VALUES('health-off',0)"); db.execute("INSERT INTO web_apps VALUES('app-idle','stopped')")
        data, _ = self.capture(); destination, marker = self.restore(data)
        self.assertEqual(marker['pauseStateFile'], r.PAUSE_STATE)
        state = json.loads((destination / r.PAUSE_STATE).read_text()); self.assertEqual((destination / r.PAUSE_STATE).stat().st_mode & 0o777, 0o600)
        controller = state['databases']['controller']
        self.assertEqual(controller['tables']['web_app_health']['enabledIds'], ['health'])  # Disabled rows stay disabled after un-pause.
        self.assertEqual(controller['webAppsStatus'], {'app': 'running', 'app-idle': 'stopped'})
        self.assertTrue(controller['nativeSettings'][0]['smtp']); self.assertEqual(controller['nativeSettings'][0]['backupsCron'], '0 * * * *')
        self.assertNotIn('smtp-private', json.dumps(state))

    def test_external_credentials_count_github_connections_by_key_without_decrypting(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE web_app_settings(id TEXT,key TEXT,valueEncrypted TEXT)')
            tables = lambda: {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn('githubConnections', r.external_credentials(db, tables()))  # No connection: nothing reported.
            # Not a ciphertext: proves the count never decrypts (no master key during the pause).
            db.executemany('INSERT INTO web_app_settings VALUES(?,?,?)', [('p', 'github', 'not-base64!'), ('b', 'github:' + 'b1' * 7 + 'c', 'opaque'), ('c', 'github:' + 'c' * 15, '')])
            db.executemany('INSERT INTO web_app_settings VALUES(?,?,?)', [('x', 'github:short', 'opaque'), ('y', 'GITHUB', 'opaque'), ('z', 'github:' + 'A' * 15, 'opaque'), ('w', 'githubx', 'opaque'), ('v', 'other', 'opaque')])
            self.assertEqual(r.external_credentials(db, tables()), {'githubConnections': {'count': 3}})
            self.assertNotIn('opaque', json.dumps(r.external_credentials(db, tables())))
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE web_app_settings(id TEXT,value TEXT)')  # Older or foreign schema: ignored, no error.
            self.assertEqual(r.external_credentials(db, {'web_app_settings'}), {})

    def test_pause_reports_github_connections_kept(self):
        with sqlite3.connect(self.controller) as db:
            db.execute('CREATE TABLE web_app_settings(id TEXT,key TEXT,valueEncrypted TEXT)')
            db.executemany('INSERT INTO web_app_settings VALUES(?,?,?)', [('p', 'github', 'cipher-principal'), ('b', 'github:' + 'b' * 15, 'cipher-extra')])
        data, _ = self.capture(); destination, marker = self.restore(data)
        pauses = json.loads((destination / 'RECOVERY_PAUSED.json').read_text())['databasePauses']['controller']
        self.assertEqual(pauses['credentialsKept']['githubConnections'], {'count': 2})
        with sqlite3.connect(destination / 'controller/pb_data/data.db') as db:  # Report only: the rows are untouched.
            self.assertEqual(db.execute('SELECT key,valueEncrypted FROM web_app_settings ORDER BY key').fetchall(), [('github', 'cipher-principal'), ('github:' + 'b' * 15, 'cipher-extra')])

    def test_inspect_summary_reports_compressed_and_materialised_restore_size(self):
        data, manifest = self.capture(); summary = r.summary(manifest, len(data)); estimate = summary['restoreEstimate']
        database_bytes = sum(value['databaseBytes'] for value in manifest['databases'])
        self.assertEqual(estimate['compressedBytes'], len(data)); self.assertEqual(estimate['all']['databaseBytes'], database_bytes)
        self.assertEqual(estimate['all']['databaseFilesBytes'], len(b'private-original-file'))
        host = self.host.stat().st_size; self.assertEqual(estimate['all']['hostFilesBytes'], host)
        self.assertEqual(estimate['allWithHostFiles']['restoreBytes'], estimate['all']['restoreBytes'] + host)
        self.assertEqual(estimate['requiredFreeBytesDefault'], r.MIN_FREE + estimate['all']['restoreBytes'])
        self.assertEqual(next(value for value in summary['databases'] if value['id'] == self.instance_id)['fileBytes'], len(b'private-original-file'))
        budget = r.budget_check(manifest, self.root / 'planned-target', ['controller'], min_free_bytes=r.MIN_FREE)
        self.assertEqual(budget['databases'], 1); self.assertEqual(budget['databaseFilesBytes'], 0); self.assertTrue(budget['fits'])
        destination, _ = self.restore(data, select=['controller'])  # Same budget function as the restore guard.
        with mock.patch.object(r.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=r.MIN_FREE)):
            self.assertFalse(r.budget_check(manifest, self.root / 'other', None)['fits'])
        with self.assertRaises(r.RecoveryError): r.budget_check(manifest, None, ['z' * 15])

    def test_instance_without_database_is_reported_only_while_its_file_is_absent(self):
        ghost = 'f' * 15; plan = {**self.plan, 'instances': [*self.plan['instances'], {'id': ghost, 'database': str(self.root / 'ghost/pb_data/data.db'), 'storage': str(self.storage), 'withoutDatabase': True}]}
        with sqlite3.connect(self.controller) as db: db.execute('INSERT INTO instances VALUES(?,?)', (ghost, 'never started'))
        _, manifest = self.capture(plan)
        self.assertEqual(manifest['coverage']['instancesWithoutDatabase'], [ghost]); self.assertTrue(manifest['coverage']['complete'])
        self.assertNotIn(ghost, [value['id'] for value in manifest['databases']])
        (self.root / 'ghost/pb_data').mkdir(parents=True); r.shutil.copyfile(self.instance, self.root / 'ghost/pb_data/data.db')
        _, manifest = self.capture(plan); self.assertIn(ghost, [value['id'] for value in manifest['databases']])
        with self.assertRaises(r.RecoveryError): self.capture({**self.plan, 'instances': [*self.plan['instances'], {'id': ghost, 'database': str(self.root / 'absent.db')}]})

    def test_sweep_removes_only_uncommitted_failed_or_old_objects_never_protected_or_committed(self):
        config = {'bucket': 'bucket', 'prefix': 'app1/platform-recovery'}; now = r.datetime.datetime(2026, 10, 29, tzinfo=r.datetime.timezone.utc)
        old, fresh = now - r.datetime.timedelta(days=3), now - r.datetime.timedelta(hours=1); base = config['prefix'] + '/'
        objects = {base + 'failed/refs/web/a.phwi': fresh, base + 'failed/platform.phpr': fresh, base + 'stale/platform.phpr': old, base + 'young/platform.phpr': fresh, base + 'cli/platform.phpr': old, base + 'cli/commit.json': old, base + 'kept/platform.phpr': old, 'app1/web-app-backups/apps/x.phwb': old, base + 'other-tool/apps/x.phwb': old, base + 'mixed/platform.phpr': old, base + 'mixed/notes.json': old}
        uploads = [{'Key': base + 'other-tool/apps/y.phwb', 'UploadId': '5', 'Initiated': old}, {'Key': base + 'failed/platform.phpr', 'UploadId': '1', 'Initiated': fresh}, {'Key': base + 'young/platform.phpr', 'UploadId': '2', 'Initiated': fresh}, {'Key': base + 'kept/platform.phpr', 'UploadId': '3', 'Initiated': old}, {'Key': base + 'oldupload/platform.phpr', 'UploadId': '4', 'Initiated': old}]
        class S3:
            def __init__(client): client.deleted, client.aborted = [], []
            def list_multipart_uploads(client, **kw): return {'Uploads': [item for item in uploads if item['Key'].startswith(kw['Prefix'])], 'IsTruncated': False}
            def abort_multipart_upload(client, **kw): client.aborted.append(kw['UploadId'])
            def list_objects_v2(client, **kw):
                keys = sorted(key for key in objects if key.startswith(kw['Prefix']))
                start = int(kw.get('ContinuationToken', 0)); page = keys[start:start + 3]
                return {'Contents': [{'Key': key, 'LastModified': objects[key]} for key in page], 'IsTruncated': start + 3 < len(keys), 'NextContinuationToken': str(start + 3)}
            def delete_object(client, **kw): client.deleted.append(kw['Key'])
        client = S3(); preview = r.sweep_orphans(client, config, {'kept'}, {'failed'}, now=now, dry_run=True)
        self.assertEqual((client.deleted, client.aborted), ([], [])); self.assertEqual(preview['capturesRemoved'], ['failed', 'stale'])
        result = r.sweep_orphans(client, config, {'kept'}, {'failed'}, now=now)
        self.assertEqual(sorted(client.aborted), ['1', '4']); self.assertEqual(result['capturesRemoved'], ['failed', 'stale'])
        self.assertEqual(sorted(client.deleted), sorted([base + 'failed/refs/web/a.phwi', base + 'failed/platform.phpr', base + 'stale/platform.phpr']))
        self.assertEqual(result['committedKept'], 1); self.assertEqual(result['recentKept'], 1); self.assertEqual(result['foreignKept'], 2)

    def test_shared_bucket_requires_distinct_recovery_and_site_backup_directories(self):
        # app1, app2 and app3 may share one bucket: each platform keeps its own directories.
        base = {'WEB_PLATFORM_S3_ENDPOINT': 'https://s3.gra.example.net', 'WEB_PLATFORM_S3_BUCKET': 'shared-bucket', 'WEB_PLATFORM_S3_ACCESS_KEY_ID': 'id', 'WEB_PLATFORM_S3_SECRET_ACCESS_KEY': 'secret'}
        for recovery, web in [('app2/platform-recovery', 'app2/web-app-backups'), ('app2/platform-recovery', 'app/web-app-backups'), ('app/platform-recovery', 'app2/platform-recovery-old')]:
            with mock.patch.dict(os.environ, {**base, 'WEB_PLATFORM_RECOVERY_PREFIX': recovery, 'WEB_BACKUPS_S3_PREFIX': web}): self.assertEqual(r.s3_settings()['prefix'], recovery)
        for recovery, web in [('app2', 'app2/web-app-backups'), ('app2/platform-recovery', 'app2/platform-recovery'), ('app2/platform-recovery/x', 'app2/platform-recovery'), ('app2/platform-recovery', 'app2/platform-recovery/web')]:
            with mock.patch.dict(os.environ, {**base, 'WEB_PLATFORM_RECOVERY_PREFIX': recovery, 'WEB_BACKUPS_S3_PREFIX': web}), self.assertRaises(r.RecoveryError): r.s3_settings()
        config = {'bucket': 'shared-bucket', 'prefix': 'app2'}
        with self.assertRaises(r.RecoveryError): r._copy_references(None, config, {'s3Refs': [{'key': 'app2/web-app-backups/a.phwb', 'prefix': 'app2/web-app-backups', 'group': 'web'}]}, 'capture', [])

    def test_recovery_prefix_is_explicit_and_a_failover_needs_a_new_recovery_directory(self):
        base = {'WEB_PLATFORM_S3_ENDPOINT': 'https://s3.gra.example.net', 'WEB_PLATFORM_S3_BUCKET': 'shared-bucket', 'WEB_PLATFORM_S3_ACCESS_KEY_ID': 'id', 'WEB_PLATFORM_S3_SECRET_ACCESS_KEY': 'secret'}
        with mock.patch.dict(os.environ, base), self.assertRaises(r.RecoveryError) as error:
            os.environ.pop('WEB_PLATFORM_RECOVERY_PREFIX', None); r.s3_settings()
        self.assertIn('WEB_PLATFORM_RECOVERY_PREFIX', str(error.exception))
        # §5.10: the new host keeps serving site backups from the restored capture's refs/web folder.
        restored = 'app1/platform-recovery/' + 'f' * 32 + '/refs/web'
        with mock.patch.dict(os.environ, {**base, 'WEB_PLATFORM_RECOVERY_PREFIX': 'app1/platform-recovery', 'WEB_BACKUPS_S3_PREFIX': restored}), self.assertRaises(r.RecoveryError) as error: r.s3_settings()
        self.assertIn('nouveau WEB_PLATFORM_RECOVERY_PREFIX', str(error.exception))
        with mock.patch.dict(os.environ, {**base, 'WEB_PLATFORM_RECOVERY_PREFIX': 'app1/platform-recovery-20261029', 'WEB_BACKUPS_S3_PREFIX': restored}): self.assertEqual(r.s3_settings()['prefix'], 'app1/platform-recovery-20261029')

    def test_image_of_a_backup_retired_mid_capture_is_dropped_but_shared_images_stay(self):
        app, retired, live = 'a' * 15, 'r' * 15, 'l' * 15; prefix = 'app1/web-app-backups'; database = self.root / 'retention.db'
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE web_app_backups(id TEXT,appId TEXT,status TEXT,operation TEXT,s3Object TEXT)')
            db.execute('INSERT INTO web_app_backups VALUES(?,?,?,?,?)', (retired, app, 'ready', '', prefix + '/apps/' + app + '/archives/' + retired + '.phwb'))
            db.execute('INSERT INTO web_app_backups VALUES(?,?,?,?,?)', (live, app, 'ready', '', prefix + '/apps/' + app + '/archives/' + live + '.phwb'))
        key = lambda name: prefix + '/apps/' + app + '/' + name
        own, shared = 'sha256:' + '1' * 64, 'sha256:' + '2' * 64
        refs = [{'key': key('archives/' + retired + '.phwb'), 'prefix': prefix, 'group': 'web', 'appId': app, 'kind': 'archive', 'required': False, 'metadata': {'app-id': app, 'backup-id': retired}},
                {'key': key('archives/' + live + '.phwb'), 'prefix': prefix, 'group': 'web', 'appId': app, 'kind': 'archive', 'required': True, 'metadata': {'app-id': app, 'backup-id': live}},
                {'key': key('images/own.phwi'), 'prefix': prefix, 'group': 'web', 'appId': app, 'imageId': own, 'kind': 'image', 'backupIds': [retired], 'metadata': {'app-id': app, 'image-id': own}},
                {'key': key('images/shared.phwi'), 'prefix': prefix, 'group': 'web', 'appId': app, 'imageId': shared, 'kind': 'image', 'backupIds': [retired, live], 'metadata': {'app-id': app, 'image-id': shared}}]
        class Gone(Exception): response = {'Error': {'Code': '404'}}
        class S3:
            def __init__(client): client.objects = {item['key']: (b'x' * 64, dict(item['metadata'])) for item in refs}; client.deleted = []
            def head_object(client, **kw):
                if kw['Key'] not in client.objects: raise Gone()
                body, metadata = client.objects[kw['Key']]; return {'ContentLength': len(body), 'ETag': 'e-' + kw['Key'][-12:], 'Metadata': metadata}
            def copy_object(client, **kw):
                if kw['CopySource']['Key'] not in client.objects: raise Gone()
                client.objects[kw['Key']] = client.objects[kw['CopySource']['Key']]; return {}
            def delete_object(client, **kw): client.deleted.append(kw['Key']); client.objects.pop(kw['Key'], None)
        client = S3(); config = {'bucket': 'bucket', 'prefix': 'app1/platform-recovery'}
        # Retention retires the oldest backup while the capture copies: archive and own image are gone.
        with sqlite3.connect(database) as db: db.execute("UPDATE web_app_backups SET status='deleting' WHERE id=?", (retired,))
        del client.objects[key('archives/' + retired + '.phwb')]; del client.objects[key('images/own.phwi')]
        plan = {'controller': {'database': str(database)}, 's3Refs': [dict(item) for item in refs]}
        copied = r.copy_references(client, config, plan, 'capture')
        self.assertEqual(sorted(item['originalKey'] for item in copied), sorted([key('archives/' + live + '.phwb'), key('images/shared.phwi')]))
        self.assertEqual(plan['retiredArchiveReferencesExcluded'], 2)
        # A retired owner never drops an image another live backup still pins, nor a required one.
        self.assertFalse(r.retired_web_archive(plan, refs[3])); self.assertFalse(r.retired_web_archive(plan, {**refs[2], 'required': True}))
        self.assertTrue(r.retired_web_archive(plan, refs[2])); self.assertFalse(r.retired_web_archive(plan, {**refs[2], 'backupIds': []}))


if __name__ == '__main__': unittest.main()
