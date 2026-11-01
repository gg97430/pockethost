#!/usr/bin/env python3
"""Private fixtures: exact native S3 references, two conditional reads and paused local restore."""
import copy
import hashlib
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


class Body(io.BytesIO):
    def __init__(self, value, client): super().__init__(value); self.client = client
    def read(self, size=-1):
        if size > self.client.largest_read: self.client.largest_read = size
        return super().read(size)


class NativeS3:
    def __init__(self, objects, mode=''):
        self.objects, self.mode, self.calls, self.heads, self.gets, self.largest_read = objects, mode, [], {}, {}, 0
    def head_object(self, **options):
        key = options['Key']; self.calls.append(('head', options)); self.heads[key] = self.heads.get(key, 0) + 1
        if self.mode == 'auth': raise RuntimeError('AccessDenied private-native-secret')
        if key not in self.objects: raise RuntimeError('MissingObject private-native-secret')
        body = self.objects[key]; etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        if self.mode == 'post-change' and self.heads[key] > 1: etag = '"changed"'
        if options.get('IfMatch') and options['IfMatch'] != etag: raise RuntimeError('PreconditionFailed private-native-secret')
        result = {'ETag': etag, 'ContentLength': len(body), 'Metadata': {'content-origin': 'fixture'}, 'VersionId': 'version-1'}
        if self.mode == 'no-etag': result.pop('ETag')
        if self.mode == 'metadata-change' and self.heads[key] > 1: result['Metadata'] = {'content-origin': 'changed'}
        return result
    def get_object(self, **options):
        key = options['Key']; self.calls.append(('get', options)); self.gets[key] = self.gets.get(key, 0) + 1
        body = self.objects[key]; etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        if self.mode == 'between-change' and self.gets[key] == 2: raise RuntimeError('PreconditionFailed private-native-secret')
        if options.get('IfMatch') != etag or 'VersionId' in options: raise RuntimeError('Unconditional or historical GET forbidden private-native-secret')
        if self.mode == 'same-etag-corruption' and self.gets[key] == 2: body = bytes([body[0] ^ 1]) + body[1:]
        payload = body[:-1] if self.mode == 'truncated' else body
        return {'ETag': etag, 'ContentLength': len(body), 'VersionId': 'version-1', 'Body': Body(payload, self)}
    def list_objects_v2(self, **_options): raise AssertionError('Native buckets must not be listed')
    def delete_object(self, **_options): raise AssertionError('Native objects must never be deleted')
    def put_object(self, **_options): raise AssertionError('Native objects must never be written')
    def close(self): self.calls.append(('close', {}))


class NativeS3Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.root = pathlib.Path(self.temporary.name).resolve()
        self.ident, self.collection, self.record = 'a' * 15, 'b' * 15, 'c' * 15
        self.key = bytes(range(32)); self.keyfile = self.root / 'outer.key'; self.keyfile.write_text(self.key.hex()); self.keyfile.chmod(0o600)
        self.controller, self.database = self.root / 'controller.db', self.root / 'instance.db'
        self.settings = {'smtp': {'enabled': True}, 's3': {'enabled': True, 'endpoint': 'https://native.example.invalid/', 'bucket': 'native-bucket', 'region': 'gra', 'accessKey': 'private-native-access', 'secret': 'private-native-secret', 'forcePathStyle': True}, 'backups': {'cron': '0 * * * *', 's3': {'enabled': True}}}
        with sqlite3.connect(self.controller) as connection:
            connection.execute('CREATE TABLE instances(id TEXT)'); connection.execute('INSERT INTO instances VALUES(?)', (self.ident,))
        with sqlite3.connect(self.database) as connection:
            connection.executescript('CREATE TABLE _params(id TEXT,value BLOB);CREATE TABLE _collections(id TEXT,name TEXT,type TEXT,fields TEXT);CREATE TABLE documents(id TEXT,attachment TEXT);')
            connection.execute('INSERT INTO _params VALUES(?,?)', ('settings', json.dumps(self.settings).encode()))
            connection.execute('INSERT INTO _collections VALUES(?,?,?,?)', (self.collection, 'documents', 'base', json.dumps([{'name': 'attachment', 'type': 'file'}])))
            connection.execute('INSERT INTO documents VALUES(?,?)', (self.record, 'document.bin'))
        self.object_key = self.collection + '/' + self.record + '/document.bin'
        self.contents = (b'private-content-\x00\xff' * 10000)
        self.objects = {self.object_key: self.contents, 'unreferenced/unrelated/private.bin': b'must-not-be-read'}
        self.plan = {'version': 1, 'platform': 'app1', 'backupId': 'native-s3-fixture', 'controller': {'database': str(self.controller)}, 'instances': [{'id': self.ident, 'database': str(self.database), 'storage': str(self.root / 'empty-local-storage'), 'initializeWalIndex': True}], 'metadata': {}}
        self.disk = mock.patch.object(engine.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=100 * 1024**3)); self.disk.start()
    def tearDown(self): self.disk.stop(); self.temporary.cleanup()
    def capture(self, client, notify=None, expected_secret='private-native-secret'):
        output = io.BytesIO()
        def factory(config):
            self.assertEqual(config['secret'], expected_secret); self.assertEqual(config['access'], 'private-native-access'); self.assertEqual(config['endpoint'], 'https://native.example.invalid')
            return client
        with mock.patch.object(engine, 'native_s3_client', side_effect=factory): manifest = engine.capture_to_sink(self.plan, self.key, self.keyfile, output, notify)
        return output.getvalue(), manifest
    def test_exact_references_two_ifmatch_reads_sha_and_paused_local_restore_without_key_metadata(self):
        client = NativeS3(self.objects)
        with mock.patch.dict(os.environ, {'WEB_PLATFORM_S3_SECRET_ACCESS_KEY': 'wrong-outer-key', 'WEB_BACKUPS_S3_ACCESS_KEY_ID': 'wrong-image-access'}): data, manifest = self.capture(client)
        self.assertEqual(manifest['coverage']['nativeS3Files'], 1); self.assertEqual(manifest['coverage']['nativeS3Databases'], [self.ident]); self.assertTrue(manifest['coverage']['complete'])
        self.assertEqual(client.gets, {self.object_key: 2}); self.assertEqual(client.heads, {self.object_key: 2}); self.assertLessEqual(client.largest_read, engine.CHUNK)
        for secret in ['private-native-secret', 'private-native-access', 'native-bucket', 'native.example.invalid', 'wrong-outer-key']:
            self.assertNotIn(secret, json.dumps(manifest)); self.assertNotIn(secret.encode(), data)
        source_before = copy.deepcopy(self.objects)
        target = self.root / 'restored'; marker = engine.restore_archive(lambda: io.BytesIO(data), self.key, target)
        self.assertEqual((target / f'instances/{self.ident}/pb_data/storage/{self.object_key}').read_bytes(), self.contents)
        self.assertEqual(self.objects, source_before)
        with sqlite3.connect(target / f'instances/{self.ident}/pb_data/data.db') as connection:
            settings = json.loads(connection.execute("SELECT value FROM _params WHERE id='settings'").fetchone()[0])
            self.assertFalse(settings['s3']['enabled']); self.assertFalse(settings['smtp']['enabled']); self.assertEqual(settings['backups']['cron'], '')
        self.assertTrue(marker['paused']); self.assertTrue(marker['noServicesStarted'])
        self.assertEqual(manifest['files'][0]['sha256'], hashlib.sha256(self.contents).hexdigest())
    def test_missing_or_denied_source_aborts_and_never_exposes_credentials(self):
        for objects, mode in [({}, ''), (self.objects, 'auth')]:
            with self.subTest(mode=mode):
                client = NativeS3(objects, mode)
                with self.assertRaises(engine.RecoveryError) as failure: self.capture(client)
                self.assertNotIn('private-native', str(failure.exception)); self.assertEqual(client.gets, {})
    def test_absent_etag_is_rejected_before_any_file_payload(self):
        client = NativeS3(self.objects, 'no-etag')
        with self.assertRaises(engine.RecoveryError): self.capture(client)
        self.assertEqual(client.gets, {})
    def test_changed_between_reads_same_etag_corruption_truncation_and_final_metadata_fail_closed(self):
        for mode in ['between-change', 'same-etag-corruption', 'truncated', 'post-change', 'metadata-change']:
            with self.subTest(mode=mode):
                client = NativeS3(self.objects, mode); before = copy.deepcopy(self.objects)
                with self.assertRaises(engine.RecoveryError) as failure: self.capture(client)
                self.assertNotIn('private-native', str(failure.exception)); self.assertEqual(self.objects, before)
    def test_invalid_source_credentials_are_not_replaced_by_platform_credentials(self):
        with sqlite3.connect(self.database) as connection:
            settings = copy.deepcopy(self.settings); settings['s3']['secret'] = ''; connection.execute('UPDATE _params SET value=?', (json.dumps(settings).encode(),))
        with mock.patch.dict(os.environ, {'WEB_PLATFORM_S3_SECRET_ACCESS_KEY': 'valid-looking-outer-secret'}), mock.patch.object(engine, 'native_s3_client') as factory:
            with self.assertRaises(engine.RecoveryError): engine.capture_to_sink(self.plan, self.key, self.keyfile, io.BytesIO())
        factory.assert_not_called()
    def test_database_file_references_and_credentials_are_bound_to_same_wal_snapshot(self):
        writer = sqlite3.connect(self.database); writer.execute('PRAGMA journal_mode=WAL'); writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('CREATE TABLE snapshot_wal_marker(value INTEGER)'); writer.commit()
        next_record = 'd' * 15; future_key = self.collection + '/' + next_record + '/future.bin'; self.objects[future_key] = b'future-content'
        def notify(phase, ident):
            if phase == 'snapshot' and ident == self.ident:
                writer.execute('INSERT INTO documents VALUES(?,?)', (next_record, 'future.bin'))
                settings = copy.deepcopy(self.settings); settings['s3']['secret'] = 'future-native-secret'; writer.execute('UPDATE _params SET value=?', (json.dumps(settings).encode(),)); writer.commit()
        client = NativeS3(self.objects)
        try: data, manifest = self.capture(client, notify)
        finally: writer.close()
        self.assertEqual(client.gets, {self.object_key: 2}); self.assertEqual(manifest['coverage']['nativeS3Files'], 1)
        target = self.root / 'snapshot'; engine.restore_archive(lambda: io.BytesIO(data), self.key, target)
        with sqlite3.connect(target / f'instances/{self.ident}/pb_data/data.db') as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM documents').fetchone()[0], 1)
        next_client = NativeS3(self.objects); _, next_manifest = self.capture(next_client, expected_secret='future-native-secret')
        self.assertEqual(set(next_client.gets), {self.object_key, future_key}); self.assertEqual(next_manifest['coverage']['nativeS3Files'], 2)
    def test_zero_byte_native_file_is_a_valid_restorable_object(self):
        self.objects[self.object_key] = b''; data, _ = self.capture(NativeS3(self.objects))
        target = self.root / 'empty-file'; engine.restore_archive(lambda: io.BytesIO(data), self.key, target)
        self.assertEqual((target / f'instances/{self.ident}/pb_data/storage/{self.object_key}').read_bytes(), b'')


if __name__ == '__main__': unittest.main()
