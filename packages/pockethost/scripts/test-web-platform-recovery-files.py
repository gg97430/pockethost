#!/usr/bin/env python3
"""Synthetic SQLite file references; no production data, network or services."""
import hashlib
import copy
import importlib.util
import io
import json
import pathlib
import sqlite3
import tempfile
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('recovery', pathlib.Path(__file__).with_name('web_platform_recovery.py'))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class FileReferenceTests(unittest.TestCase):
    collection_id = 'b' * 15
    record_id = 'c' * 15
    instance_id = 'a' * 15
    fields = json.dumps([{'name': 'attachment', 'type': 'file'}])

    def database(self, filename=':memory:'):
        connection = sqlite3.connect(filename)
        connection.executescript('CREATE TABLE _collections(id TEXT,name TEXT,type TEXT,fields);CREATE TABLE documents(id TEXT,attachment);')
        connection.execute('INSERT INTO _collections VALUES(?,?,?,?)', (self.collection_id, 'documents', 'base', self.fields))
        connection.execute('INSERT INTO documents VALUES(?,?)', (self.record_id, ''))
        return connection

    def reference(self, filename):
        return '/'.join([self.collection_id, self.record_id, filename])

    def stored(self, connection, value, jsonb=False):
        if jsonb:
            connection.execute('UPDATE documents SET attachment=jsonb(?)', (json.dumps(value),))
        else:
            connection.execute('UPDATE documents SET attachment=?', (value,))

    def test_text_single_list_and_empty_values_keep_their_existing_meaning(self):
        with self.database() as connection:
            for value, expected in [
                ('document.txt', {self.reference('document.txt')}),
                ('["first.txt","second.txt"]', {self.reference('first.txt'), self.reference('second.txt')}),
                ('', set()),
                (None, set()),
                ('[]', set()),
            ]:
                with self.subTest(value=value):
                    self.stored(connection, value)
                    self.assertEqual(r.file_references(connection), expected)

    def test_jsonb_single_element_multiple_elements_and_empty_arrays(self):
        with self.database() as connection:
            for filenames in [['document.txt'], ['first.txt', 'second.txt'], []]:
                with self.subTest(filenames=filenames):
                    self.stored(connection, filenames, jsonb=True)
                    self.assertEqual(connection.execute('SELECT typeof(attachment) FROM documents').fetchone()[0], 'blob')
                    self.assertEqual(r.file_references(connection), {self.reference(name) for name in filenames})

    def test_jsonb_collection_schema_and_file_value_are_both_supported(self):
        with self.database() as connection:
            connection.execute('UPDATE _collections SET fields=jsonb(fields)')
            self.stored(connection, ['document.txt'], jsonb=True)
            self.assertEqual(r.file_references(connection), {self.reference('document.txt')})

    def test_jsonb_non_arrays_nested_or_non_string_elements_fail_closed(self):
        with self.database() as connection:
            for value in ['document.txt', {}, None, True, 7, [['nested.txt']], [None], [1], [False], ['']]:
                with self.subTest(value=value):
                    self.stored(connection, value, jsonb=True)
                    with self.assertRaises(r.RecoveryError):
                        r.file_references(connection)

    def test_corrupt_blob_values_never_turn_into_missing_or_empty_references(self):
        with self.database() as connection:
            valid = connection.execute('SELECT jsonb(?)', ('["document.txt"]',)).fetchone()[0]
            for value in [b'', b'\xff', b'\x00', valid[:-1], b'not-a-json-file-list']:
                with self.subTest(length=len(value)):
                    self.stored(connection, value)
                    with self.assertRaises(r.RecoveryError):
                        r.file_references(connection)

    def test_invalid_text_lists_and_paths_stay_rejected(self):
        with self.database() as connection:
            for value in ['[', '[null]', '[1]', '[[]]', '[""]']:
                with self.subTest(value=value):
                    self.stored(connection, value)
                    with self.assertRaises(r.RecoveryError):
                        r.file_references(connection)
            for filename in ['../escape.txt', 'nested/file.txt', 'nested\\file.txt', '..', '.', '/absolute.txt', 'nul\x00file.txt']:
                for encoding in ['single', 'text-array', 'jsonb-array']:
                    with self.subTest(filename=filename, encoding=encoding):
                        value = [filename] if encoding == 'jsonb-array' else json.dumps([filename]) if encoding == 'text-array' else filename
                        self.stored(connection, value, jsonb=encoding == 'jsonb-array')
                        with self.assertRaises(r.RecoveryError):
                            r.file_references(connection)

    def policy(self, raw='/uploads/catalog/missing.png'):
        return {'version': 1, 'reason': 'operator-approved-missing-assets', 'approvedAt': '2026-10-31T12:00:00Z', 'entries': [{'instanceId': self.instance_id, 'collectionId': self.collection_id, 'recordId': self.record_id, 'field': 'attachment', 'rawValueSha256': hashlib.sha256(raw.encode()).hexdigest()}]}

    def test_exclusions_are_exact_operator_approvals_and_default_remains_strict(self):
        raw = '/uploads/catalog/missing.png'
        with self.database() as connection:
            self.stored(connection, raw)
            with self.assertRaises(r.RecoveryError): r.file_references(connection)
            self.assertEqual(r.file_references(connection, self.policy(raw), self.instance_id), set())
            self.assertEqual(connection.execute('SELECT attachment FROM documents').fetchone()[0], raw)
            self.stored(connection, raw + '.changed')
            with self.assertRaises(r.RecoveryError): r.file_references(connection, self.policy(raw), self.instance_id)

    def test_exclusions_do_not_extend_to_new_rows_fields_databases_or_stale_entries(self):
        raw = '/uploads/catalog/missing.png'
        with self.database() as connection:
            self.stored(connection, raw)
            for change in [{'instanceId': 'z' * 15}, {'collectionId': 'other'}, {'recordId': 'z' * 15}, {'field': 'other'}]:
                policy = self.policy(raw)
                policy['entries'][0].update(change)
                with self.assertRaises(r.RecoveryError): r.file_references(connection, policy, self.instance_id)
            connection.execute('INSERT INTO documents VALUES(?,?)', ('d' * 15, raw))
            with self.assertRaises(r.RecoveryError): r.file_references(connection, self.policy(raw), self.instance_id)
            connection.execute('DELETE FROM documents')
            with self.assertRaises(r.RecoveryError): r.file_references(connection, self.policy(raw), self.instance_id)

    def test_approval_policy_validation_rejects_broad_or_ambiguous_scopes(self):
        valid = self.policy()
        invalid = []
        for changes in [{'reason': 'ignore-any-file'}, {'approvedAt': '2026-10-31T12:00:00'}, {'entries': []}, {'wildcard': True}, {'entries': valid['entries'] * 2}, {'entries': valid['entries'] * 257}]:
            invalid.append({**copy.deepcopy(valid), **changes})
        for changes in [{'instanceId': '*'}, {'collectionId': '../outside'}, {'recordId': '*'}, {'field': '*'}, {'rawValueSha256': '*'}, {'prefix': '/uploads/'}]:
            policy = copy.deepcopy(valid)
            policy['entries'][0].update(changes)
            invalid.append(policy)
        for policy in invalid:
            with self.subTest(policyKeys=list(policy)):
                with self.assertRaises(r.RecoveryError): r.validate_file_reference_exclusions(policy, {self.instance_id})
        with self.assertRaises(r.RecoveryError): r.validate_file_reference_exclusions(valid, {'z' * 15})

    def test_approved_hash_never_overrides_text_upload_prefix_or_path_validation(self):
        with self.database() as connection:
            for raw in ['valid-native.txt', '/other/missing.png', '/uploads/../escape.txt', '/uploads//bad.txt', '/uploads/nul\x00file', '/uploads/bad\\file', '/uploads/file?query', '/uploads/file#fragment']:
                self.stored(connection, raw)
                with self.subTest(raw=raw):
                    with self.assertRaises(r.RecoveryError): r.file_references(connection, self.policy(raw), self.instance_id)
            self.stored(connection, ['/uploads/catalog/missing.png'], jsonb=True)
            with self.assertRaises(r.RecoveryError): r.file_references(connection, self.policy(), self.instance_id)

    def capture_fixture(self, root, missing=False, exclusions=False):
        controller, instance, storage = root / 'controller.db', root / 'instance.db', root / 'storage'
        with sqlite3.connect(controller) as connection:
            connection.execute('CREATE TABLE instances(id TEXT)')
            connection.execute('INSERT INTO instances VALUES(?)', (self.instance_id,))
        with self.database(instance) as connection:
            self.stored(connection, ['document.txt'], jsonb=True)
            original_blob = connection.execute('SELECT attachment FROM documents').fetchone()[0]
            if exclusions:
                connection.execute('INSERT INTO documents VALUES(?,?)', ('d' * 15, '/uploads/catalog/missing.png'))
        filename = storage / self.reference('document.txt')
        filename.parent.mkdir(parents=True)
        if not missing:
            filename.write_bytes(b'synthetic file contents')
        key = bytes(range(32))
        keyfile = root / 'outer.key'
        keyfile.write_text(key.hex())
        keyfile.chmod(0o600)
        plan = {'version': 1, 'platform': 'app1', 'backupId': 'synthetic-jsonb-files', 'controller': {'database': str(controller)}, 'instances': [{'id': self.instance_id, 'database': str(instance), 'storage': str(storage)}]}
        if exclusions:
            plan['fileReferenceExclusions'] = self.policy()
            plan['fileReferenceExclusions']['entries'][0]['recordId'] = 'd' * 15
        before = hashlib.sha256(instance.read_bytes()).digest()
        output = io.BytesIO()
        manifest = r.capture_to_sink(plan, key, keyfile, output)
        self.assertEqual(hashlib.sha256(instance.read_bytes()).digest(), before)
        return output.getvalue(), manifest, key, original_blob

    def test_capture_and_paused_restore_keep_raw_jsonb_and_its_referenced_file(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(r.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=100 * 1024**3)):
            root = pathlib.Path(temporary).resolve()
            data, manifest, key, original_blob = self.capture_fixture(root)
            database = next(item for item in manifest['databases'] if item['id'] == self.instance_id)
            self.assertEqual(database['files']['referenced'], 1)
            target = root / 'restored'
            marker = r.restore_archive(lambda: io.BytesIO(data), key, target)
            self.assertTrue(marker['paused'])
            with sqlite3.connect(target / 'instances' / self.instance_id / 'pb_data/data.db') as connection:
                self.assertEqual(connection.execute('SELECT typeof(attachment),attachment FROM documents').fetchone(), ('blob', original_blob))
                self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual((target / 'instances' / self.instance_id / 'pb_data/storage' / self.reference('document.txt')).read_bytes(), b'synthetic file contents')

    def test_jsonb_reference_to_an_absent_file_still_aborts_the_capture(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(r.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=100 * 1024**3)):
            with self.assertRaises(r.RecoveryError):
                self.capture_fixture(pathlib.Path(temporary).resolve(), missing=True)

    def test_approved_exclusion_round_trip_is_explicit_and_keeps_native_files_and_raw_sql(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(r.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=100 * 1024**3)):
            root = pathlib.Path(temporary).resolve()
            data, manifest, key, original_blob = self.capture_fixture(root, exclusions=True)
            policy = manifest['fileReferenceExclusions']
            coverage = r.file_exclusion_coverage(policy)
            self.assertEqual(r.inspect_archive(lambda: io.BytesIO(data), key)['fileReferenceExclusions'], policy)
            self.assertEqual(r.summary(manifest)['coverage']['approvedFileReferenceExclusions'], coverage)
            self.assertNotIn(b'/uploads/catalog/missing.png', data)
            target = root / 'restored'
            marker = r.restore_archive(lambda: io.BytesIO(data), key, target)
            self.assertEqual(marker['coverage']['approvedFileReferenceExclusions'], coverage)
            self.assertEqual(json.loads((target / 'recovery-manifest.json').read_text())['fileReferenceExclusions'], policy)
            with sqlite3.connect(target / 'instances' / self.instance_id / 'pb_data/data.db') as connection:
                self.assertEqual(connection.execute('SELECT attachment FROM documents WHERE id=?', (self.record_id,)).fetchone()[0], original_blob)
                self.assertEqual(connection.execute('SELECT attachment FROM documents WHERE id=?', ('d' * 15,)).fetchone()[0], '/uploads/catalog/missing.png')
            self.assertEqual((target / 'instances' / self.instance_id / 'pb_data/storage' / self.reference('document.txt')).read_bytes(), b'synthetic file contents')
            bad = copy.deepcopy(manifest)
            bad['fileReferenceExclusions']['entries'][0]['rawValueSha256'] = '0' * 64
            observed = {entry['name']: entry for entry in bad['objects']}
            with self.assertRaises(r.RecoveryError): r.validate_manifest(bad, observed)
            bad['coverage']['approvedFileReferenceExclusions'] = r.file_exclusion_coverage(bad['fileReferenceExclusions'])
            with mock.patch.object(r, 'inspect_archive', return_value=bad), self.assertRaises(r.RecoveryError):
                r.restore_archive(lambda: io.BytesIO(data), key, root / 'wrong-policy')

    def test_inventory_generator_preserves_exact_policy_and_rejects_removed_scope(self):
        import web_platform_recovery_plan as generator
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            controller = root / 'mothership/pb_data/data.db'
            controller.parent.mkdir(parents=True)
            with sqlite3.connect(controller) as connection:
                connection.execute('CREATE TABLE instances(id TEXT,version TEXT)')
                connection.execute('INSERT INTO instances VALUES(?,?)', (self.instance_id, 'fixture'))
            base = {'version': 1, 'platform': 'app1', 'controller': {'database': str(controller)}, 'fileReferenceExclusions': self.policy()}
            generated, _ = generator.build_plan(base, root, allow_without_database=True)
            self.assertEqual(generated['fileReferenceExclusions'], base['fileReferenceExclusions'])
            with sqlite3.connect(controller) as connection: connection.execute('DELETE FROM instances')
            with self.assertRaises(generator.engine.RecoveryError): generator.build_plan(base, root, allow_without_database=True)


if __name__ == '__main__':
    unittest.main()
