#!/usr/bin/env python3
"""Mock S3 transport and Docker processes; real streaming gzip/AES-GCM is exercised."""
import copy
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('web_app_backup_s3', pathlib.Path(__file__).with_name('web_app_backup_s3.py'))
s3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s3)
APP, BACKUP, DEPLOYMENT, IMAGE = 'a' * 15, 'b' * 15, 'c' * 15, 'sha256:' + 'd' * 64
ENV = {'WEB_BACKUPS_S3_ENDPOINT': 'https://s3.example.invalid/', 'WEB_BACKUPS_S3_BUCKET': 'private-backups',
    'WEB_BACKUPS_S3_ACCESS_KEY_ID': 'private-access', 'WEB_BACKUPS_S3_SECRET_ACCESS_KEY': 'private-secret',
    'WEB_BACKUPS_S3_IMAGE_KEY': 'f' * 64, 'WEB_BACKUPS_S3_PREFIX': 'app1/web-app-backups', 'WEB_BACKUPS_S3_REGION': 'gra'}


class Missing(Exception):
    response = {'Error': {'Code': 'NoSuchKey'}}


class Bare404(Exception):
    response = {'Error': {'Code': '404'}}


class NoSuchBucket(Exception):
    response = {'Error': {'Code': 'NoSuchBucket'}}


class FakeS3:
    def __init__(self):
        self.objects, self.uploads, self.calls = {}, {}, []
        self.fail_part = False
        self.second_get_changed = False
        self.gets = 0
        self.bucket_missing = False
        self.bucket_lost_after = None  # head_bucket answers this many times, then the bucket is gone
        self.head_error = None

    def head_bucket(self, **kwargs):
        self.calls.append(('head_bucket', kwargs))
        if self.bucket_lost_after is not None:
            if self.bucket_lost_after <= 0: raise Bare404()
            self.bucket_lost_after -= 1
        if self.bucket_missing: raise Missing()
        return {}

    def _object(self, key, data, metadata):
        self.objects[key] = {'Body': bytes(data), 'Metadata': dict(metadata), 'ETag': '"' + hashlib.md5(data).hexdigest() + '"'}

    def head_object(self, **kwargs):
        self.calls.append(('head', kwargs))
        if self.head_error: raise self.head_error
        if kwargs['Key'] not in self.objects: raise Bare404() if self.bucket_lost_after is not None else Missing()
        value = self.objects[kwargs['Key']]
        return {'Metadata': value['Metadata'], 'ContentLength': len(value['Body']), 'ETag': value['ETag']}

    def create_multipart_upload(self, **kwargs):
        self.calls.append(('create', kwargs))
        self.uploads['upload'] = {'parts': {}, 'metadata': kwargs['Metadata'], 'key': kwargs['Key']}
        return {'UploadId': 'upload'}

    def upload_part(self, **kwargs):
        self.calls.append(('part', {**kwargs, 'Body': len(kwargs['Body'])}))
        if self.fail_part: raise OSError('private AWS response must not escape')
        self.uploads[kwargs['UploadId']]['parts'][kwargs['PartNumber']] = kwargs['Body']
        return {'ETag': '"part-' + str(kwargs['PartNumber']) + '"'}

    def complete_multipart_upload(self, **kwargs):
        self.calls.append(('complete', kwargs))
        value = self.uploads.pop(kwargs['UploadId'])
        self._object(value['key'], b''.join(value['parts'][key] for key in sorted(value['parts'])), value['metadata'])
        return {'ETag': self.objects[value['key']]['ETag']}

    def abort_multipart_upload(self, **kwargs):
        self.calls.append(('abort', kwargs)); self.uploads.pop(kwargs['UploadId'], None)
        return {}

    def put_object(self, **kwargs):
        self.calls.append(('put', {**kwargs, 'Body': len(kwargs['Body'])}))
        self._object(kwargs['Key'], kwargs['Body'], kwargs['Metadata'])
        return {}

    def get_object(self, **kwargs):
        self.calls.append(('get', kwargs)); self.gets += 1
        value = self.objects[kwargs['Key']]
        if kwargs.get('IfMatch') != value['ETag'] or (self.second_get_changed and self.gets == 2):
            raise OSError('IfMatch rejected')
        return {'Body': io.BytesIO(value['Body']), 'Metadata': value['Metadata'], 'ETag': value['ETag'], 'ContentLength': len(value['Body'])}

    def delete_object(self, **kwargs):
        self.calls.append(('delete', kwargs)); self.objects.pop(kwargs['Key'], None)
        return {}


class Sink(io.BytesIO):
    def close(self):
        self.saved = self.getvalue()
        super().close()


class FakeProcess:
    def __init__(self, data=b'', loading=False):
        self.stdout = None if loading else io.BytesIO(data)
        self.stdin = Sink() if loading else None
        self.returncode = None
        self.pid = 999999
        self.killed = False

    def poll(self): return self.returncode
    def wait(self, timeout=None): self.returncode = 0 if self.returncode is None else self.returncode; return self.returncode


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='web-s3-test-')
        self.root = pathlib.Path(self.temp.name).resolve()
        self.remote = FakeS3()
        self.tar = b'runtime-secret must remain encrypted\n' + os.urandom(256000)
        self.processes, self.process_args, self.reports = [], [], []
        self.present = True
        self.foreign = False
        self.job = {'id': BACKUP, 'appId': APP, 'lease': 'lease', 'imageRef': f'ph-web/{APP}:{DEPLOYMENT}'}
        payload = 'opaque-pocketbase-ciphertext'
        archive = {'format': 'pockethost-web-backup', 'version': 1, 'id': BACKUP, 'appId': APP, 'imageId': IMAGE,
            'imageRef': self.job['imageRef'], 'imageBytes': len(self.tar), 'payloadEncrypted': payload, 'payloadSha256': hashlib.sha256(payload.encode()).hexdigest()}
        raw = json.dumps(archive).encode()
        folder = self.root / 'backups' / APP; folder.mkdir(parents=True)
        (folder / (BACKUP + '.phwb')).write_bytes(raw)
        self.result = {'imageId': IMAGE, 'checksum': hashlib.sha256(raw).hexdigest(), 'archiveBytes': len(raw), 'imageBytes': len(self.tar)}
        self.env = patch.dict(os.environ, ENV, clear=False); self.env.start()
        self.client = patch.object(s3, '_client', return_value=self.remote); self.client.start()
        self.command = patch.object(s3, '_docker_command', side_effect=lambda action, image_id=None: ['/usr/bin/docker', '--host', 'unix:///run/user/1001/docker.sock', 'image', action] + ([image_id] if image_id else [])); self.command.start()
        self.popen = patch.object(s3.subprocess, 'Popen', side_effect=self.process); self.popen.start()
        self.kill = patch.object(s3, '_kill', side_effect=self.kill_process); self.kill.start()
        self.disk = patch.object(s3.shutil, 'disk_usage', return_value=SimpleNamespace(free=100 * 1024**3))
        self.disk_usage = self.disk.start()

    def tearDown(self):
        self.disk.stop(); self.kill.stop(); self.popen.stop(); self.command.stop(); self.client.stop(); self.env.stop(); self.temp.cleanup()

    def process(self, args, **kwargs):
        loading = args[-1] == 'load'
        if loading: self.assertGreaterEqual(self.remote.gets, 2, 'Docker load must follow a full authenticated first GET')
        process = FakeProcess(self.tar, loading=loading)
        self.processes.append(process); self.process_args.append((args, kwargs))
        return process

    def kill_process(self, process):
        if process.poll() is None:
            process.killed = True; process.returncode = -15
            if hasattr(process, 'unblock'): process.unblock.set()

    def docker(self, args, **kwargs):
        self.assertEqual(args, ['image', 'inspect', IMAGE])
        self.assertEqual(kwargs, {'timeout': 10})
        if not self.present:
            # Loaded images become visible after the import process completes.
            if not any(process.stdin is not None and process.returncode == 0 for process in self.processes):
                raise subprocess.CalledProcessError(1, args)
        return json.dumps([{'Id': IMAGE, 'Config': {'Labels': {'pockethost.web': 'true', 'pockethost.web.app': 'x' * 15 if self.foreign else APP}}}])

    def api(self, endpoint, body): self.reports.append((endpoint, body)); return {}
    def mirror(self): return s3.mirror_backup(self.root, self.job, self.result, self.docker, api=self.api)

    def test_roundtrip_real_encryption_two_pinned_gets_and_no_plaintext_or_image_file(self):
        mirrored = self.mirror()
        self.assertEqual(mirrored['s3State'], 'ready')
        image = self.remote.objects[mirrored['s3ImageObject']]['Body']
        self.assertEqual(image[:5], b'PHWI1')
        self.assertNotIn(b'runtime-secret', image)
        self.assertEqual(len(list(self.root.rglob('*'))), 3)  # backups, app, existing metadata archive only
        self.assertTrue(all(call[1].get('ACL') == 'private' for call in self.remote.calls if call[0] in ('create', 'put')))
        self.present = False
        self.assertEqual(s3.ensure_backup_image(self.root, IMAGE, APP, self.docker), IMAGE)
        self.assertEqual(self.processes[-1].stdin.saved, self.tar)
        gets = [value for name, value in self.remote.calls if name == 'get']
        self.assertEqual(len(gets), 2); self.assertEqual(gets[0]['IfMatch'], gets[1]['IfMatch'])
        self.assertTrue(all(options['start_new_session'] for _, options in self.process_args))
        self.assertTrue(self.reports and self.reports[0][1]['s3State'] == 'uploading')
        self.assertNotIn('private-secret', json.dumps(self.reports))

    def test_immutable_image_is_uploaded_once_and_existing_local_image_needs_no_s3(self):
        self.mirror(); self.mirror()
        self.assertEqual(len([1 for name, _ in self.remote.calls if name == 'create']), 1)
        self.assertEqual(len([1 for name, _ in self.remote.calls if name == 'put']), 1)
        self.assertEqual(s3.ensure_backup_image(self.root, IMAGE, APP, self.docker), IMAGE)
        self.assertEqual(self.remote.gets, 0)

    def test_large_image_uses_8mib_parts_and_bounded_stream_reads(self):
        self.tar = os.urandom(s3.PART + 100000)
        self.result['imageBytes'] = len(self.tar)
        self.mirror()
        sizes = [value['Body'] for name, value in self.remote.calls if name == 'part']
        self.assertEqual(sizes[0], 8 * 1024**2)
        self.assertTrue(all(size <= s3.PART for size in sizes))
        self.assertGreaterEqual(len(sizes), 2)

    def test_multipart_failure_aborts_and_kills_only_the_export_group(self):
        self.remote.fail_part = True
        self.tar = os.urandom(s3.PART + 100000)
        with self.assertRaises(s3.BackupS3Error) as error: self.mirror()
        self.assertNotIn('private AWS', str(error.exception))
        self.assertTrue(any(name == 'abort' for name, _ in self.remote.calls))
        self.assertTrue(self.processes[0].killed)
        self.assertTrue((self.root / 'backups' / APP / (BACKUP + '.phwb')).is_file())

    def test_tampered_ciphertext_is_refused_before_docker_load(self):
        mirrored = self.mirror(); self.present = False
        value = self.remote.objects[mirrored['s3ImageObject']]
        data = bytearray(value['Body']); data[-1] ^= 1; value['Body'] = bytes(data)
        before = len(self.processes)
        with self.assertRaises(s3.BackupS3Error): s3.ensure_backup_image(self.root, IMAGE, APP, self.docker)
        self.assertEqual(len(self.processes), before)

    def test_aad_blocks_ciphertext_moved_to_another_application(self):
        mirrored = self.mirror(); self.present = False
        config = s3._settings(); other = 'z' * 15
        key = s3._keys(config, other, image_id=IMAGE)[1]
        value = copy.deepcopy(self.remote.objects[mirrored['s3ImageObject']])
        value['Metadata']['app-id'] = other; self.remote.objects[key] = value
        def absent(*args, **kwargs): raise subprocess.CalledProcessError(1, args)
        before = len(self.processes)
        with self.assertRaises(s3.BackupS3Error): s3.ensure_backup_image(self.root, IMAGE, other, absent)
        self.assertEqual(len(self.processes), before)

    def test_changed_etag_and_foreign_metadata_are_refused(self):
        mirrored = self.mirror(); self.present = False; self.remote.second_get_changed = True
        before = len(self.processes)
        with self.assertRaises(s3.BackupS3Error): s3.ensure_backup_image(self.root, IMAGE, APP, self.docker)
        self.assertEqual(len(self.processes), before)
        self.remote.objects[mirrored['s3ImageObject']]['Metadata']['app-id'] = 'x' * 15
        with self.assertRaises(s3.BackupS3Error): s3.ensure_backup_image(self.root, IMAGE, APP, self.docker)

    def test_invalid_archive_foreign_image_and_oversize_export_fail_closed(self):
        self.foreign = True
        with self.assertRaises(s3.BackupS3Error): self.mirror()
        self.assertEqual(self.processes, [])
        self.foreign = False
        with patch.object(s3, 'MAX_COMPRESSED', 100):
            with self.assertRaises(s3.BackupS3Error): self.mirror()
        self.assertTrue(any(name == 'abort' for name, _ in self.remote.calls))
        self.result['checksum'] = '0' * 64
        with self.assertRaises(s3.BackupS3Error): self.mirror()

    def test_retention_keeps_shared_or_queued_image_and_deletes_only_exact_objects(self):
        mirrored = self.mirror()
        job = {**self.job, **mirrored, 'imageId': IMAGE}
        queued = {'id': 'e' * 15, 'appId': APP, 'imageRef': self.job['imageRef'], 'imageId': ''}
        api = lambda endpoint, body: {'items': [queued]}
        s3.delete_remote_backup(self.root, job, api, self.docker)
        self.assertNotIn(mirrored['s3Object'], self.remote.objects)
        self.assertIn(mirrored['s3ImageObject'], self.remote.objects)
        s3.delete_remote_backup(self.root, job, lambda endpoint, body: {'items': []}, self.docker)
        self.assertNotIn(mirrored['s3ImageObject'], self.remote.objects)
        self.assertTrue(all(name not in ('list', 'delete_many') for name, _ in self.remote.calls))
        with self.assertRaises(s3.BackupS3Error): s3.delete_remote_backup(self.root, {**job, 's3Object': 'another/app/archive'}, api, self.docker)

    def test_capture_prefix_after_failover_is_never_deleted_by_retention(self):
        # §5.10: the restored rows point into <recovery>/<captureId>/refs/web; retention must not damage the capture.
        prefix = 'app1/platform-recovery/' + 'c' * 32 + '/refs/web'
        with patch.dict(os.environ, {'WEB_BACKUPS_S3_PREFIX': prefix}, clear=False):
            mirrored = self.mirror()
            self.assertTrue(mirrored['s3Object'].startswith(prefix + '/apps/'))
            before, calls = dict(self.remote.objects), len(self.remote.calls)
            job = {**self.job, **mirrored, 'imageId': IMAGE}
            self.assertEqual(s3.delete_remote_backup(self.root, job, lambda endpoint, body: {'items': []}, self.docker)['s3State'], 'deleted')
            self.assertEqual(self.remote.objects, before)
            self.assertFalse(any(name == 'delete' for name, _ in self.remote.calls[calls:]))
            with self.assertRaises(s3.BackupS3Error): s3.delete_remote_backup(self.root, {**job, 's3Object': 'another/app/archive'}, lambda *args: {'items': []}, self.docker)
        self.assertFalse(s3.capture_prefix({'PREFIX': 'app1/web-app-backups'}))
        self.assertTrue(s3.capture_prefix({'PREFIX': prefix}))

    def recovery_job(self, mirrored):
        archive = json.loads((self.root / 'backups' / APP / (BACKUP + '.phwb')).read_text())
        return {**self.job, **self.result, **mirrored, 'payloadEncrypted': archive['payloadEncrypted']}

    def test_recovers_only_verified_metadata_archive_atomically_with_private_mode(self):
        mirrored = self.mirror(); job = self.recovery_job(mirrored)
        filename = self.root / 'backups' / APP / (BACKUP + '.phwb')
        original = filename.read_bytes(); filename.unlink(); before = len(self.processes)
        result = s3.ensure_backup_archive(self.root, job, self.docker, api=self.api)
        self.assertEqual(filename.read_bytes(), original)
        self.assertEqual(filename.stat().st_mode & 0o777, 0o640)
        self.assertEqual(result['checksum'], self.result['checksum'])
        self.assertEqual(len(self.processes), before)
        self.assertFalse(list(filename.parent.glob('*.tmp')))
        filename.write_bytes(b'corrupted local file')
        s3.ensure_backup_archive(self.root, job, self.docker)
        self.assertEqual(filename.read_bytes(), original)

    def test_recover_rejects_tamper_payload_mismatch_and_symlinks(self):
        mirrored = self.mirror(); job = self.recovery_job(mirrored)
        filename = self.root / 'backups' / APP / (BACKUP + '.phwb')
        filename.unlink()
        with self.assertRaises(s3.BackupS3Error): s3.ensure_backup_archive(self.root, {**job, 'payloadEncrypted': 'wrong'}, self.docker)
        self.assertFalse(filename.exists())
        value = self.remote.objects[mirrored['s3Object']]
        original = value['Body']; value['Body'] = b'x' + original[1:]
        with self.assertRaises(s3.BackupS3Error): s3.ensure_backup_archive(self.root, job, self.docker)
        self.assertFalse(filename.exists()); value['Body'] = original
        outside = self.root / 'outside.phwb'; outside.write_bytes(b'untouched')
        filename.symlink_to(outside)
        with self.assertRaises(s3.BackupS3Error): s3.ensure_backup_archive(self.root, job, self.docker)
        self.assertEqual(outside.read_bytes(), b'untouched')

    def test_stream_deadline_kills_blocked_export_and_aborts_multipart(self):
        class Blocked:
            def __init__(self, event): self.event = event
            def read(self, count): self.event.wait(2); return b''
            def close(self): pass
        def blocked(args, **kwargs):
            process = FakeProcess(); process.unblock = threading.Event(); process.stdout = Blocked(process.unblock)
            self.processes.append(process); return process
        with patch.object(s3, 'DEADLINE', .05), patch.object(s3.subprocess, 'Popen', side_effect=blocked):
            started = time.monotonic()
            with self.assertRaises(s3.BackupS3Error): self.mirror()
            self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(self.processes[-1].killed)
        self.assertTrue(any(name == 'abort' for name, _ in self.remote.calls))

    def test_restore_streams_large_compressible_tar_without_losing_decoder_output(self):
        self.tar = b'A' * (8 * 1024**2)
        self.mirror(); self.present = False
        s3.ensure_backup_image(self.root, IMAGE, APP, self.docker)
        self.assertEqual(self.processes[-1].stdin.saved, self.tar)

    def test_low_disk_refuses_restore_before_second_get_or_docker_load(self):
        self.mirror(); self.present = False
        before = len(self.processes)
        self.disk_usage.return_value = SimpleNamespace(free=s3.MIN_FREE + 2 * len(self.tar) - 1)
        with self.assertRaises(s3.BackupS3Error):
            s3.ensure_backup_image(self.root, IMAGE, APP, self.docker)
        self.assertEqual(len(self.processes), before)
        self.assertEqual(self.remote.gets, 1)

    def test_gzip_bomb_is_rejected_during_authenticated_first_pass_before_load(self):
        self.tar = b'A' * (1024 * 1024)
        self.mirror(); self.present = False
        before = len(self.processes)
        with patch.object(s3, 'MAX_TAR', 128 * 1024):
            with self.assertRaises(s3.BackupS3Error):
                s3.ensure_backup_image(self.root, IMAGE, APP, self.docker)
        self.assertEqual(len(self.processes), before)
        self.assertEqual(self.remote.gets, 1)

    def test_disk_drop_during_load_kills_import_at_periodic_byte_check(self):
        self.tar = b'A' * (9 * 1024**2)
        self.mirror(); self.present = False
        self.disk_usage.side_effect = [SimpleNamespace(free=100 * 1024**3),
            SimpleNamespace(free=100 * 1024**3), SimpleNamespace(free=s3.MIN_FREE - 1)]
        with self.assertRaises(s3.BackupS3Error):
            s3.ensure_backup_image(self.root, IMAGE, APP, self.docker)
        self.assertTrue(self.processes[-1].killed)
        self.assertIsNotNone(self.processes[-1].stdin)

    def test_retention_finds_partial_mirrors_without_a_final_object_acknowledgement(self):
        mirrored = self.mirror()
        job = {**self.job, 'imageId': IMAGE, 's3Object': '', 's3ImageObject': ''}
        s3.delete_remote_backup(self.root, job, lambda *args: {'items': []}, self.docker)
        self.assertNotIn(mirrored['s3Object'], self.remote.objects)
        self.assertNotIn(mirrored['s3ImageObject'], self.remote.objects)
        self.assertEqual(self.reports[0][1]['s3Object'], mirrored['s3Object'])
        self.assertEqual(self.reports[0][1]['s3ImageObject'], mirrored['s3ImageObject'])

    def test_unknown_pins_fail_closed_before_any_remote_deletion(self):
        mirrored = self.mirror(); job = {**self.job, **mirrored, 'imageId': IMAGE}
        with self.assertRaises(s3.BackupS3Error):
            s3.delete_remote_backup(self.root, job, lambda *args: {'items': [{'id': 'e' * 15, 'appId': APP}]}, self.docker)
        self.assertIn(mirrored['s3Object'], self.remote.objects)
        self.assertIn(mirrored['s3ImageObject'], self.remote.objects)

    def test_compose_mirrors_all_images_and_preserves_auxiliary_shared_pins(self):
        auxiliary='sha256:'+'e'*64
        filename=self.root/'backups'/APP/(BACKUP+'.phwb')
        archive=json.loads(filename.read_text());archive.update(images={'web':IMAGE,'cache':auxiliary},imageSizes={IMAGE:123,auxiliary:456},totalImageBytes=579)
        raw=json.dumps(archive).encode();filename.write_bytes(raw);self.result.update(images=archive['images'],checksum=hashlib.sha256(raw).hexdigest(),archiveBytes=len(raw))
        def docker(args,**kwargs):
            return json.dumps([{'Id':args[2],'Config':{'Labels':{'pockethost.web':'true','pockethost.web.app':APP}}}])
        mirrored=s3.mirror_backup(self.root,self.job,self.result,docker,api=self.api)
        self.assertEqual(set(mirrored['s3ImageObjects']),{IMAGE,auxiliary})
        self.assertEqual(len([1 for name,_ in self.remote.calls if name=='create']),2)
        self.assertEqual(self.reports[0][1]['s3ImageObjects'],mirrored['s3ImageObjects'])
        job={**self.job,**mirrored,'imageId':IMAGE,'images':archive['images']}
        pins=[{'id':'f'*15,'appId':APP,'imageRef':f'ph-web/{APP}:'+('g'*15),'imageId':'sha256:'+'9'*64,'images':{'cache':auxiliary}}]
        s3.delete_remote_backup(self.root,job,lambda *args:{'items':pins},docker)
        self.assertNotIn(mirrored['s3ImageObjects'][IMAGE],self.remote.objects)
        self.assertIn(mirrored['s3ImageObjects'][auxiliary],self.remote.objects)

    def test_single_image_mirror_keeps_legacy_metadata_shape(self):
        self.assertNotIn('s3ImageObjects',self.mirror())

    def test_never_falls_back_to_a_host_root_docker_socket(self):
        self.command.stop()
        try:
            for socket in ['', '/var/run/docker.sock', '/run/user/0/docker.sock', 'unix:///run/user/1001/docker.sock']:
                with patch.dict(os.environ, {'WEB_APPS_DOCKER_SOCKET': socket}, clear=False):
                    with self.assertRaises(s3.BackupS3Error): s3._docker_command('save', IMAGE)
            self.assertEqual(self.processes, [])
        finally:
            self.command.start()

    def test_mistyped_bucket_never_reads_404_as_absent_objects(self):
        # A wrong WEB_BACKUPS_S3_BUCKET answers HEAD object with a bare 404 too: nothing may be
        # skipped as already uploaded, nor marked deleted while the real objects stay orphaned.
        mirrored = self.mirror()
        job = {**self.job, **mirrored, 'imageId': IMAGE}
        before = dict(self.remote.objects)
        self.remote.bucket_missing = True
        with self.assertRaises(s3.BackupS3Error) as error:
            s3.delete_remote_backup(self.root, job, lambda endpoint, body: {'items': []}, self.docker)
        self.assertIn('WEB_BACKUPS_S3_BUCKET', str(error.exception))
        self.assertEqual(self.remote.objects, before)
        self.assertFalse(any(name == 'delete' for name, _ in self.remote.calls))
        self.remote.objects.clear(); mark = len(self.remote.calls)
        with self.assertRaises(s3.BackupS3Error): self.mirror()
        self.assertFalse(any(name in ('head', 'create', 'put') for name, _ in self.remote.calls[mark:]))

    def test_bucket_lost_mid_job_never_turns_404_into_absent_or_deleted(self):
        # The job-level bucket check passed, then the bucket vanished (or access was lost): each later HEAD 404 must be
        # confirmed by head_bucket, so nothing is marked deleted while objects may stay orphaned.
        mirrored = self.mirror()
        job = {**self.job, **mirrored, 'imageId': IMAGE}
        archive = mirrored['s3Object']
        kept = self.remote.objects.pop(archive)
        self.remote.bucket_lost_after = 1
        with self.assertRaises(s3.BackupS3Error) as error:
            s3.delete_remote_backup(self.root, job, lambda endpoint, body: {'items': []}, self.docker)
        self.assertIn('WEB_BACKUPS_S3_BUCKET', str(error.exception))
        self.assertFalse(any(name == 'delete' for name, _ in self.remote.calls))
        self.assertGreaterEqual(sum(1 for name, _ in self.remote.calls if name == 'head_bucket'), 2)
        # Same 404 with a bucket that answers: the archive is really absent and the image copy is deleted.
        self.remote.bucket_lost_after = 5
        self.assertEqual(s3.delete_remote_backup(self.root, job, lambda endpoint, body: {'items': []}, self.docker)['s3State'], 'deleted')
        self.remote.objects[archive] = kept

    def test_no_such_bucket_and_unconfirmed_404_always_raise(self):
        error = Bare404()
        with self.assertRaises(s3.BackupS3Error): s3._missing(error)
        with self.assertRaises(s3.BackupS3Error): s3._missing(NoSuchBucket(), self.remote, {'BUCKET': 'b'})
        self.assertFalse(s3._missing(OSError('private network error'), self.remote, {'BUCKET': 'b'}))
        self.assertTrue(s3._missing(error, self.remote, {'BUCKET': 'b'}))
        self.remote.head_error = NoSuchBucket()
        with self.assertRaises(s3.BackupS3Error) as raised: self.mirror()
        self.assertIn('WEB_BACKUPS_S3_BUCKET', str(raised.exception))
        self.assertFalse(any(name in ('create', 'put') for name, _ in self.remote.calls))

    def test_prefix_is_required_so_shared_buckets_never_mix_hosts(self):
        # app1, app2 and app3 may share one bucket: a host without its own prefix must not
        # silently write into another host's directory.
        with patch.dict(os.environ, {'WEB_BACKUPS_S3_PREFIX': ''}, clear=False):
            with self.assertRaises(s3.BackupS3Error) as error: self.mirror()
            self.assertIn('WEB_BACKUPS_S3_PREFIX', str(error.exception))
            self.assertNotIn('private-access', str(error.exception))
        self.assertFalse(any(name in ('create', 'put') for name, _ in self.remote.calls))
        with patch.dict(os.environ, {'WEB_BACKUPS_S3_PREFIX': 'app2/web-app-backups'}, clear=False):
            mirrored = self.mirror()
        self.assertTrue(mirrored['s3Object'].startswith('app2/web-app-backups/apps/' + APP + '/archives/'))
        self.assertTrue(all(key.startswith('app2/web-app-backups/') for key in self.remote.objects))

    def test_incomplete_configuration_is_generic_and_unconfigured_mirror_is_disabled(self):
        with patch.dict(os.environ, {key: '' for key in ENV}, clear=False):
            self.assertEqual(self.mirror()['s3State'], 'disabled')
        with patch.dict(os.environ, {'WEB_BACKUPS_S3_IMAGE_KEY': ''}, clear=False):
            with self.assertRaises(s3.BackupS3Error) as error: self.mirror()
            self.assertNotIn('private-access', str(error.exception))


if __name__ == '__main__': unittest.main()
