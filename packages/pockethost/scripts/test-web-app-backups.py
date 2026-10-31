#!/usr/bin/env python3
"""Encrypted archive durability, quota guards, retention deletion and image pin safety."""
import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('web_backups', pathlib.Path(__file__).with_name('web_app_backups.py'))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name).resolve()
        self.app = 'a' * 15
        self.job = {'id': 'b' * 15, 'appId': self.app, 'operation': 'create', 'lease': 'private-worker-lease', 'payloadEncrypted': 'authenticated-encrypted-controller-payload', 'imageRef': 'ph-web/' + self.app + ':' + 'c' * 15, 'commit': 'd' * 40, 'createdAt': '2026-10-29T00:00:00.000Z'}
        self.image = {'Id': 'sha256:' + 'e' * 64, 'Size': 1024**3, 'Config': {'Labels': {'pockethost.web.app': self.app}}}
        self.disk = mock.patch.object(b.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=30 * 1024**3))
        self.disk.start()

    def tearDown(self):
        self.disk.stop()
        self.temporary.cleanup()

    def docker(self, args):
        self.assertEqual(args, ['image', 'inspect', self.job['imageRef']])
        return json.dumps([self.image])

    def api(self, path, body):
        self.assertEqual(path, 'backups/pins')
        return {'items': []}

    def create(self, job=None):
        return b.create_archive(self.root, job or self.job, self.api, self.docker)

    def test_archive_is_small_encrypted_payload_with_hash_and_immutable_image_pin(self):
        result = self.create()
        filename = b._path(self.root, self.app, self.job['id'])
        raw = filename.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), result['checksum'])
        archive, _, size = b.read_archive(self.root, self.app, self.job['id'], result['checksum'])
        self.assertEqual(size, len(raw))
        self.assertEqual(archive['payloadEncrypted'], self.job['payloadEncrypted'])
        self.assertEqual(archive['imageId'], self.image['Id'])
        self.assertNotIn('MASTER_KEY', raw.decode())
        self.assertEqual(filename.stat().st_mode & 0o777, 0o640)
        self.assertEqual(b.protected_images(self.root, self.app, self.api), {self.image['Id'], self.job['imageRef']})

    def test_retried_creation_is_idempotent_after_lost_acknowledgement(self):
        first = self.create()
        second = self.create()
        self.assertEqual(first, second)
        self.assertEqual(len(b._manifest(self.root)['backups']), 1)

    def test_archive_tamper_or_symlink_is_rejected_without_rewriting(self):
        result = self.create()
        filename = b._path(self.root, self.app, self.job['id'])
        original = filename.read_bytes()
        filename.write_bytes(original.replace(b'2026', b'2027'))
        with self.assertRaises(b.BackupError):
            b.read_archive(self.root, self.app, self.job['id'], result['checksum'])
        with self.assertRaises(b.BackupError):
            self.create()
        filename.unlink()
        target = self.root / 'unrelated'
        target.write_bytes(original)
        filename.symlink_to(target)
        with self.assertRaises(b.BackupError):
            b.delete_archive(self.root, self.job)
        self.assertEqual(target.read_bytes(), original)

    def test_queued_controller_backups_pin_images_before_worker_creates_manifest(self):
        api = lambda path, body: {'items': [{'id': self.job['id'], 'appId': self.app, 'imageRef': self.job['imageRef'], 'imageId': '', 'imageBytes': 0}]}
        self.assertEqual(b.protected_images(self.root, self.app, api), {self.job['imageRef']})

    def test_bad_manifest_or_controller_failure_stops_pruning(self):
        b._base(self.root).joinpath('pins.json').write_text('{invalid')
        with self.assertRaisesRegex(b.BackupError, 'nettoyage'):
            b.protected_images(self.root, self.app, self.api)
        b._base(self.root).joinpath('pins.json').unlink()
        with self.assertRaises(RuntimeError):
            b.protected_images(self.root, self.app, mock.Mock(side_effect=RuntimeError('controller unavailable')))

    def test_disk_and_global_unique_image_budget_guards_do_not_drop_existing_pins(self):
        self.create()
        before = b._manifest(self.root)
        self.disk.stop()
        self.disk = mock.patch.object(b.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=b.MIN_FREE - 1))
        self.disk.start()
        with self.assertRaisesRegex(b.BackupError, '15 Gio'):
            self.create({**self.job, 'id': 'f' * 15})
        self.assertEqual(b._manifest(self.root), before)
        self.disk.stop()
        self.disk = mock.patch.object(b.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=30 * 1024**3))
        self.disk.start()
        with mock.patch.object(b, 'IMAGE_BUDGET', self.image['Size']):
            self.create({**self.job, 'id': 'f' * 15})  # Same image counts once.
            self.image = {**self.image, 'Id': 'sha256:' + '1' * 64}
            with self.assertRaisesRegex(b.BackupError, '6 Gio'):
                self.create({**self.job, 'id': 'g' * 15})
        self.assertEqual(len(b._manifest(self.root)['backups']), 2)

    def test_retention_deletion_keeps_a_shared_image_pinned_until_last_archive(self):
        self.create()
        other = {**self.job, 'id': 'f' * 15}
        self.create(other)
        b.delete_archive(self.root, self.job)
        self.assertFalse(b._path(self.root, self.app, self.job['id']).exists())
        self.assertIn(self.image['Id'], b.protected_images(self.root, self.app, self.api))
        b.delete_archive(self.root, other)
        self.assertEqual(b.protected_images(self.root, self.app, self.api), set())

    def test_s3_failure_keeps_local_ready_and_reports_no_secret_in_error(self):
        calls = []
        def api(path, body):
            calls.append((path, body))
            return {'job': self.job} if path == 'backups/claim' else {'items': []} if path == 'backups/pins' else {'ok': True}
        remote = types.SimpleNamespace(mirror_backup=mock.Mock(side_effect=RuntimeError('private-access-key-must-never-leak')))
        with mock.patch.dict(sys.modules, {'web_app_backup_s3': remote}):
            result = b.tick(self.root, api, self.docker, force=True)
        self.assertEqual(result['status'], 'ready')
        reports = [body for path, body in calls if path == 'backups/report']
        self.assertEqual([body['status'] for body in reports], ['running', 'ready'])
        self.assertEqual(reports[-1]['s3State'], 'failed')
        self.assertNotIn('private-access-key', json.dumps(reports))
        b.read_archive(self.root, self.app, self.job['id'], reports[-1]['checksum'])

    def test_lost_local_metadata_ack_is_recoverable_and_not_reported_failed(self):
        reports = []
        def api(path, body):
            if path == 'backups/claim': return {'job': self.job}
            if path == 'backups/pins': return {'items': []}
            reports.append(body)
            raise RuntimeError('connection lost')
        with self.assertRaises(RuntimeError):
            b.tick(self.root, api, self.docker, force=True)
        self.assertEqual([body['status'] for body in reports], ['running'])
        b.read_archive(self.root, self.app, self.job['id'])
        self.assertIn(self.image['Id'], b.protected_images(self.root, self.app, self.api))

    def test_remote_retention_failure_preserves_local_archive_and_pin(self):
        self.create()
        calls = []
        job = {**self.job, 'operation': 'delete', 's3Object': 'private-prefix/archive'}
        def api(path, body):
            calls.append((path, body))
            return {'job': job} if path == 'backups/claim' else {'ok': True}
        remote = types.SimpleNamespace(delete_remote_backup=mock.Mock(side_effect=RuntimeError('OVH refused')))
        with mock.patch.dict(sys.modules, {'web_app_backup_s3': remote}):
            self.assertEqual(b.tick(self.root, api, self.docker, force=True)['status'], 'failed')
        self.assertTrue(b._path(self.root, self.app, self.job['id']).exists())
        self.assertIn(self.image['Id'], b.protected_images(self.root, self.app, self.api))

    def test_compose_archive_pins_every_auxiliary_sha_and_counts_unique_sizes(self):
        auxiliary = 'sha256:' + '1' * 64
        job = {**self.job, 'imageRef': self.image['Id'], 'images': {'web': self.image['Id'], 'worker': auxiliary}}
        def docker(args):
            self.assertEqual(args[:2], ['image', 'inspect'])
            return json.dumps([{**self.image, 'Id': args[2], 'Size': 2 * 1024**3 if args[2] == auxiliary else self.image['Size']}])
        result = b.create_archive(self.root, job, self.api, docker)
        archive, _, _ = b.read_archive(self.root, self.app, job['id'], result['checksum'])
        self.assertEqual(archive['images'], job['images'])
        self.assertEqual(result['imageBytes'], 1024**3)
        self.assertEqual(result['totalImageBytes'], 3 * 1024**3)
        self.assertEqual(b.protected_images(self.root, self.app, self.api), {self.image['Id'], auxiliary})
        self.assertEqual(b.create_archive(self.root, job, self.api, docker), result)
        queued = lambda path, body: {'items': [{'id': job['id'], 'appId': self.app, 'imageRef': job['imageRef'], 'imageId': '', 'imageBytes': 0, 'images': job['images'], 'imageSizes': {}}]}
        b.delete_archive(self.root, job)
        self.assertEqual(b.protected_images(self.root, self.app, queued), {self.image['Id'], auxiliary})

    def test_compose_shared_image_is_counted_once_and_missing_foreign_auxiliary_fails_closed(self):
        shared = {**self.job, 'imageRef': self.image['Id'], 'images': {'web': self.image['Id'], 'worker': self.image['Id']}}
        docker = lambda args: json.dumps([self.image])
        with mock.patch.object(b, 'IMAGE_BUDGET', self.image['Size']):
            result = b.create_archive(self.root, shared, self.api, docker)
        self.assertEqual(result['totalImageBytes'], self.image['Size'])
        before = b._manifest(self.root)
        foreign = 'sha256:' + '2' * 64
        bad = {**shared, 'id': 'f' * 15, 'images': {'web': self.image['Id'], 'worker': foreign}}
        def other(args):
            return json.dumps([self.image if args[2] == self.image['Id'] else {**self.image, 'Id': foreign, 'Config': {'Labels': {'pockethost.web.app': 'z' * 15}}}])
        with self.assertRaises(b.BackupError): b.create_archive(self.root, bad, self.api, other)
        self.assertEqual(b._manifest(self.root), before)
        invalid = {**shared, 'id': 'g' * 15, 'images': {str(i): self.image['Id'] for i in range(5)}}
        with self.assertRaises(b.BackupError): b.create_archive(self.root, invalid, self.api, docker)

    def test_compose_manifest_and_archive_reject_missing_front_and_inconsistent_sizes(self):
        job = {**self.job, 'imageRef': self.image['Id'], 'images': {'web': self.image['Id']}}
        result = b.create_archive(self.root, job, self.api, lambda args: json.dumps([self.image]))
        path = b._path(self.root, self.app, job['id'])
        archive = json.loads(path.read_bytes())
        archive['images'] = {'web': 'sha256:' + '3' * 64}
        path.write_bytes(b._encode(archive))
        with self.assertRaises(b.BackupError): b.read_archive(self.root, self.app, job['id'])
        manifest = b._manifest(self.root)
        manifest['backups'][job['id']]['imageSizes'] = {}
        b._atomic(b._base(self.root) / 'pins.json', b._encode(manifest))
        with self.assertRaises(b.BackupError): b.protected_images(self.root, self.app, self.api)

    def crash_api(self, job, calls):
        def api(path, body):
            calls.append((path, body))
            return {'job': job} if path == 'backups/claim' else {'items': []} if path == 'backups/pins' else {'ok': True}
        return api

    def test_crash_looping_backup_ends_failed_with_reason_after_bounded_attempts(self):
        class Crash(BaseException):
            pass  # Stands for an OOM kill / worker restart: nothing is reported and the controller re-queues.
        calls = []
        def crashing(args):
            raise Crash()
        for attempt in range(1, 4):
            with self.assertRaises(Crash):
                b.tick(self.root, self.crash_api(self.job, calls), crashing, force=True)
            self.assertEqual(b._ledger(self.root)[self.job['id'] + ':create']['count'], attempt)
        self.assertEqual([path for path, _ in calls if path == 'backups/report'], [])
        docker = mock.Mock(side_effect=AssertionError('must not run again'))
        result = b.tick(self.root, self.crash_api(self.job, calls), docker, force=True)
        self.assertEqual(result, {'id': self.job['id'], 'status': 'failed', 'failureCode': 'attempts_exhausted'})
        report = [body for path, body in calls if path == 'backups/report'][-1]
        self.assertEqual((report['status'], report['failureCode'], report['attempts'], report['lease']), ('failed', 'attempts_exhausted', 4, self.job['lease']))
        self.assertIn('3 tentatives', report['error'])
        self.assertLessEqual(len(report['error']), 500)
        docker.assert_not_called()
        self.assertEqual(b._ledger(self.root), {})
        self.assertFalse(b._path(self.root, self.app, self.job['id']).exists())
        self.assertEqual(b._ledger_path(self.root).stat().st_mode & 0o777, 0o640)

    def test_controller_attempt_count_and_limit_are_honoured(self):
        calls = []
        job = {**self.job, 'attempt': 2, 'maxAttempts': 1}
        self.assertEqual(b.tick(self.root, self.crash_api(job, calls), mock.Mock(), force=True)['failureCode'], 'attempts_exhausted')
        self.assertEqual([body for path, body in calls if path == 'backups/report'][-1]['attempts'], 2)
        calls.clear()
        job = {**self.job, 'attempt': 1, 'maxAttempts': 99}  # Out-of-range limits fall back to the default 3.
        self.assertEqual(b.tick(self.root, self.crash_api(job, calls), self.docker, force=True)['status'], 'ready')

    def test_success_and_ordinary_failure_clear_ledger_with_failure_code(self):
        calls = []
        self.image = {**self.image, 'Config': {'Labels': {'pockethost.web.app': 'z' * 15}}}
        self.assertEqual(b.tick(self.root, self.crash_api(self.job, calls), self.docker, force=True)['status'], 'failed')
        report = [body for path, body in calls if path == 'backups/report'][-1]
        self.assertEqual((report['failureCode'], report['attempts']), ('backup_error', 1))
        self.assertEqual(b._ledger(self.root), {})
        calls.clear()
        self.image = {**self.image, 'Config': {'Labels': {'pockethost.web.app': self.app}}}
        def api(path, body):
            calls.append((path, body))
            if path == 'backups/pins': raise OSError('private engine text')
            return {'job': self.job} if path == 'backups/claim' else {'ok': True}
        self.assertEqual(b.tick(self.root, api, self.docker, force=True)['status'], 'failed')
        report = [body for path, body in calls if path == 'backups/report'][-1]
        self.assertEqual(report['failureCode'], 'engine_error')
        self.assertNotIn('engine text', json.dumps(report))

    def test_corrupt_ledger_never_blocks_and_exhausted_delete_keeps_archive(self):
        b._base(self.root).joinpath('attempts.json').write_text('{broken')
        calls = []
        self.assertEqual(b.tick(self.root, self.crash_api(self.job, calls), self.docker, force=True)['status'], 'ready')
        job = {**self.job, 'operation': 'delete', 'attempt': 5}
        self.assertEqual(b.tick(self.root, self.crash_api(job, calls), self.docker, force=True)['failureCode'], 'attempts_exhausted')
        self.assertTrue(b._path(self.root, self.app, self.job['id']).exists())
        self.assertIn(self.image['Id'], b.protected_images(self.root, self.app, self.api))
        target = self.root / 'elsewhere.json'
        target.write_text('{}')
        b._ledger_path(self.root).unlink()
        b._base(self.root).joinpath('attempts.json').symlink_to(target)
        with self.assertRaises(b.BackupError):
            b.begin_attempt(self.root, self.job)
        self.assertEqual(target.read_text(), '{}')


if __name__ == '__main__':
    unittest.main()
