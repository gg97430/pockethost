#!/usr/bin/env python3
import datetime
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

import web_platform_recovery_control as control
import web_platform_recovery as engine
import web_platform_recovery_service as service


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.root = pathlib.Path(self.temporary.name).resolve(); self.state = self.root / 'state'; self.state.mkdir(mode=0o700)
        self.environment = mock.patch.dict(os.environ, {'WEB_PLATFORM_RECOVERY_STATE_DIR': str(self.state), 'WEB_PLATFORM_RECOVERY_PREFIX': 'app1/platform-recovery', 'WEB_BACKUPS_S3_PREFIX': 'app1/web-app-backups'}, clear=False); self.environment.start()
        self.key = self.root / 'outer.key'; self.key.write_text('ab' * 32); self.key.chmod(0o600)
        self.password = self.root / 'superuser.password'; self.password.write_text('synthetic-private-password'); self.password.chmod(0o600)
        self.plan = self.root / 'plan.json'; self.plan.write_text(json.dumps({'version': 1, 'platform': 'app1', 'controller': {'database': str(self.root / 'controller.db')}, 'instances': []})); self.plan.chmod(0o600)
        self.config = {'version': 1, 'platform': 'app1', 'stateDir': str(self.state), 'planFile': str(self.plan), 'keyFile': str(self.key), 'drillRoot': str(self.root / 'drills'), 'controlUrl': 'http://127.0.0.1:8091', 'superuserIdentity': 'technical@example.invalid', 'superuserPasswordFile': str(self.password), 'drillSelect': ['controller'], 'drillMinFreeGiB': 15, 'maxDrillBytes': 2 * 1024**3, 'preflightTimeoutSeconds': 60, 'captureTimeoutSeconds': 60, 'sweepEnabled': False}
        self.cfgfile = self.root / 'service.json'; self.cfgfile.write_text(json.dumps(self.config)); self.cfgfile.chmod(0o600); service.STOP.clear()
    def tearDown(self): self.environment.stop(); self.temporary.cleanup()
    def run_record(self, ident='a' * 32):
        value = {'id': ident, 'operation': 'capture', 'status': 'queued', 'created': control.stamp()}; control.save_run(value); return value
    def test_private_fixed_config_and_loopback_only(self):
        self.assertEqual(service.config_file(self.cfgfile)['drillSelect'], ['controller'])
        for value in [{**self.config, 'controlUrl': 'https://outside.invalid'}, {**self.config, 'drillSelect': ['../../outside']}, {**self.config, 'drillMinFreeGiB': 5}]:
            self.cfgfile.write_text(json.dumps(value))
            with self.assertRaises(engine.RecoveryError): service.config_file(self.cfgfile)
    def test_child_environment_never_inherits_host_injection_or_dashboard_password(self):
        with mock.patch.dict(os.environ, {'PATH': '/bad', 'LD_PRELOAD': '/bad.so', 'PYTHONPATH': '/bad-modules', 'SECRET_APP_VALUE': 'private', 'WEB_BACKUPS_S3_ACCESS_KEY_ID': 'synthetic-s3-id'}):
            child = service.child_environment()
        self.assertEqual(child['PATH'], '/usr/local/bin:/usr/bin:/bin'); self.assertNotIn('LD_PRELOAD', child); self.assertNotIn('PYTHONPATH', child); self.assertNotIn('SECRET_APP_VALUE', child); self.assertNotIn('synthetic-private-password', json.dumps(child)); self.assertEqual(child['WEB_BACKUPS_S3_ACCESS_KEY_ID'], 'synthetic-s3-id')
    def test_private_install_gate_publishes_heartbeat_without_claim_or_schedule(self):
        run = self.run_record(); config = {**self.config, 'executionEnabled': False}
        with mock.patch.object(control, 'schedule_tick') as schedule, mock.patch.object(service, 'process_job') as execute:
            self.assertFalse(service.worker_once(config))
        schedule.assert_not_called(); execute.assert_not_called()
        with mock.patch.object(control, 'skip_locked_occurrence', side_effect=control.ControlError('x')) as skip: self.assertFalse(service.worker_once(config))
        skip.assert_called_once()
        self.assertEqual(control.all_runs()[0]['status'], 'queued')
        heartbeat = control.read_json(self.state / 'worker.json'); self.assertTrue(heartbeat['online']); self.assertFalse(heartbeat['capabilities']['canCapture']); self.assertFalse(heartbeat['capabilities']['canDrill'])
    def test_preflight_images_compose_auxiliaries_and_volume_before_capture(self):
        run = self.run_record(); app_id, backup_id, volume_id = 'a' * 15, 'b' * 15, 'c' * 15
        prefix = 'app1/web-app-backups'; digest1, digest2 = 'sha256:' + '1' * 64, 'sha256:' + '2' * 64; calls = []
        class Controller:
            def __init__(self, _config): pass
            def authenticate(self): calls.append(('authenticate', None))
            def records(self, collection, filter='', fields=''):
                if collection in ('web_app_environments', 'web_app_backup_settings'): return []
                if collection == 'web_app_backups' and 'queued' in filter: return []
                if collection == 'web_app_backups': return [{'id': backup_id, 'appId': app_id, 'imageId': digest1, 's3Object': prefix + '/apps/' + app_id + '/archives/' + backup_id + '.phwb', 's3ImageObject': prefix + '/apps/' + app_id + '/images/one.phwi', 's3ImageObjects': {digest1: prefix + '/apps/' + app_id + '/images/one.phwi', digest2: prefix + '/apps/' + app_id + '/images/two.phwi'}, 'runtimeImages': {'front': digest1, 'database': digest2}, 'finishedAt': control.stamp()}]
                if collection == 'web_storage_jobs' and 'queued' in filter: return []
                if collection == 'web_storage_jobs': return [{'id': volume_id, 'appId': app_id, 'name': 'uploads', 'result': {'object': prefix + '/apps/' + app_id + '/volumes/uploads/' + volume_id + '.phwv', 'checkedAt': control.stamp()}}]
                raise AssertionError(collection)
            def request(self, path, body=None, method=None, superuser=False, anonymous=False):
                calls.append((path, body))
                if path.startswith('/api/web-apps?'): return {'items': [{'id': app_id, 'activeDeployment': 'd' * 15}], 'hasMore': False}
                if path.endswith('/backups'): return {'backup': {'id': backup_id}}
                if '/collections/web_app_backups/records/' in path: return {'status': 'ready', 's3State': 'ready'}
                if path.endswith('/storage'): return {'volumes': [{'name': 'uploads'}]}
                if path.endswith('/uploads/backup'): return {'job': {'id': volume_id}}
                if '/collections/web_storage_jobs/records/' in path: return {'status': 'ready'}
                raise AssertionError(path)
        with mock.patch.object(service, 'Controller', Controller): filename = service.prepare_plan(self.config, run)
        value = json.loads(filename.read_text()); images = [ref for ref in value['s3Refs'] if ref['kind'] == 'image']; volumes = [ref for ref in value['s3Refs'] if ref['kind'] == 'volume']
        self.assertEqual({ref['imageId'] for ref in images}, {digest1, digest2}); self.assertTrue(all(ref['backupIds'] == [backup_id] and ref['required'] for ref in images)); self.assertEqual(len(volumes), 1); self.assertEqual({ref['group'] for ref in value['s3Refs']}, {'web'}); self.assertTrue(any(path.endswith('/uploads/backup') for path, _ in calls)); self.assertEqual(filename.stat().st_mode & 0o777, 0o600)
    def test_web_prefix_is_never_derived_and_shared_images_keep_every_owner(self):
        app_id, first, second, digest = 'a' * 15, 'b' * 15, 'c' * 15, 'sha256:' + '1' * 64; prefix = 'app2/web-app-backups'; created = []
        image = prefix + '/apps/' + app_id + '/images/one.phwi'; sqlite3.connect(self.root / 'controller.db').close()
        class Controller:
            def __init__(self, _config): pass
            def authenticate(self): pass
            def records(self, collection, filter='', fields=''):
                if collection == 'web_app_backups' and 'queued' in filter: return [{'id': second, 'appId': app_id, 'status': 'running'}]
                if collection == 'web_app_backups': return [{'id': ident, 'appId': app_id, 'imageId': digest, 's3Object': prefix + '/apps/' + app_id + '/archives/' + ident + '.phwb', 's3ImageObject': image, 'finishedAt': control.stamp()} for ident in (first, second)]
                return []
            def request(self, path, body=None, method=None, superuser=False, anonymous=False):
                if path.startswith('/api/web-apps?'): return {'items': [{'id': app_id, 'activeDeployment': 'd' * 15}], 'hasMore': False}
                if path.endswith('/backups'): created.append(path); return {'backup': {'id': second}}
                if '/collections/web_app_backups/records/' in path: return {'status': 'ready', 's3State': 'ready'}
                if path.endswith('/storage'): return {'volumes': []}
                raise AssertionError(path)
        # app2 without WEB_BACKUPS_S3_PREFIX: refuse before creating any site backup (no 'app2/…' guess
        # that would not match keys the worker wrote elsewhere).
        with mock.patch.dict(os.environ, {'WEB_BACKUPS_S3_PREFIX': ''}), mock.patch.object(service, 'Controller', Controller), self.assertRaises(engine.RecoveryError) as error: service.prepare_plan(self.config, self.run_record())
        self.assertIn('WEB_BACKUPS_S3_PREFIX', str(error.exception)); self.assertEqual(created, [])
        with mock.patch.dict(os.environ, {'WEB_BACKUPS_S3_PREFIX': prefix}), mock.patch.object(service, 'Controller', Controller): value = json.loads(service.prepare_plan(self.config, self.run_record('b' * 32)).read_text())
        images = [ref for ref in value['s3Refs'] if ref['kind'] == 'image']
        self.assertEqual(len(images), 1); self.assertEqual(images[0]['backupIds'], [first, second]); self.assertTrue(images[0]['required']); self.assertEqual({ref['prefix'] for ref in value['s3Refs']}, {prefix})
    def test_nightly_capture_never_evicts_customer_archives_through_app_retention(self):
        ready = lambda *rows: [{'id': ident, 'created': created, 's3State': state, 'sourceDeploymentId': 'd' * 15} for ident, created, state in rows]
        customer = ready(('m1', '2026-01-03', 'ready'), ('m2', '2026-01-02', 'ready'), ('m3', '2026-01-01', 'ready'))
        self.assertEqual(service.web_backup_choice(customer, 3, set())['id'], 'm1')  # A new row would push m3 out: reuse.
        self.assertIsNone(service.web_backup_choice(customer[:2], 3, set()))  # Room left: nothing is evicted.
        self.assertIsNone(service.web_backup_choice(ready(('p2', '2026-01-03', 'ready'), ('m1', '2026-01-02', 'ready'), ('p1', '2026-01-01', 'ready')), 3, {'p1', 'p2'}))  # Only our own oldest goes.
        self.assertIsNone(service.web_backup_choice(customer, 3, set(), protect=False))
        self.assertIsNone(service.web_backup_choice([{**customer[0], 'trigger': 'platform'}, *customer[1:]], 3, set()))  # Controller excludes platform rows.
        self.assertEqual(service.web_backup_choice(ready(('m1', '2026-01-03', 'failed'), ('m2', '2026-01-02', 'ready')), 1, set())['id'], 'm2')
        with self.assertRaises(engine.RecoveryError): service.web_backup_choice(ready(('m1', '2026-01-03', 'failed')), 1, set())
        # Controller with trigger=platform outside the customer retention: always a fresh archive (never
        # yesterday's customer code), even without a reusable S3 copy; asked only when it changes the outcome.
        asked = []
        def excluded(): asked.append(1); return True
        self.assertIsNone(service.web_backup_choice(customer, 3, set(), excluded=excluded)); self.assertEqual(asked, [1])
        self.assertIsNone(service.web_backup_choice(ready(('m1', '2026-01-03', 'failed')), 1, set(), excluded=excluded))
        self.assertIsNone(service.web_backup_choice(customer[:2], 3, set(), excluded=excluded)); self.assertEqual(len(asked), 2)
        app_id, created = 'a' * 15, []; rows = {'web_app_backups': customer}; listed = []
        class Controller:
            def __init__(self, _config): pass
            def authenticate(self): pass
            def records(self, collection, filter='', fields=''):
                if collection == 'web_app_backup_settings': return [{'appId': app_id, 'retention': 3}]
                if collection == 'web_app_backups' and 'queued' in filter: return []
                if collection == 'web_app_backups': return rows['web_app_backups']
                return []
            def request(self, path, body=None, method=None, superuser=False, anonymous=False):
                if path.startswith('/api/web-apps?'): return {'items': [{'id': app_id, 'activeDeployment': 'e' * 15}], 'hasMore': False}
                if path.endswith('/backups') and body is None: listed.append(path); return {'items': [], **({'platformRetention': rows['capability']} if rows.get('capability') else {})}
                if path.endswith('/backups'): created.append((path, body)); return {'backup': {'id': 'p' * 15}}
                if '/collections/web_app_backups/records/' in path: return {'status': 'ready', 's3State': 'ready'}
                if path.endswith('/storage'): return {'volumes': []}
                raise AssertionError(path)
        run = self.run_record()
        with mock.patch.object(service, 'Controller', Controller): service.prepare_plan(self.config, run)
        self.assertEqual(created, []); self.assertEqual(run['webBackups'], {'created': 0, 'reused': 1, 'staleReused': [app_id]})
        self.assertEqual(control.public_job(run)['webBackups'], {'created': 0, 'reused': 1, 'staleReused': 1}); self.assertNotIn('"' + app_id + '"', json.dumps(control.public_job(run)))
        rows['web_app_backups'] = customer[:2]; run = self.run_record('b' * 32)
        with mock.patch.object(service, 'Controller', Controller): service.prepare_plan(self.config, run)
        self.assertEqual(created, [(f'/api/web-apps/{app_id}/backups', {'trigger': 'platform'})]); self.assertEqual(run['webBackups']['created'], 1); self.assertEqual(service.platform_backup_ids(), {'p' * 15})
        self.assertEqual((self.state / service.PLATFORM_BACKUPS).stat().st_mode & 0o777, 0o600)
        # Same full customer set on a controller publishing the capability: a fresh platform archive, no reuse.
        self.assertEqual(listed, [f'/api/web-apps/{app_id}/backups']); rows.update({'web_app_backups': customer, 'capability': 'excluded'}); run = self.run_record('c' * 32)
        with mock.patch.object(service, 'Controller', Controller): service.prepare_plan(self.config, run)
        self.assertEqual(len(created), 2); self.assertEqual(run['webBackups']['created'], 1); self.assertEqual(run['webBackups']['reused'], 0); self.assertEqual(len(listed), 2)

    def test_capture_records_ready_only_after_verified_complete_result(self):
        run = self.run_record()
        result = {'status': 'complete', 'verified': True, 'coverage': {'complete': True}, 'object': 'app1/platform-recovery/capture/platform.phpr', 'references': []}
        with mock.patch.object(service, 'prepare_plan', return_value=self.plan), mock.patch.object(service, 'run_cli', return_value=result): service.process_job(self.config, run)
        self.assertEqual(run['status'], 'ready'); self.assertTrue(run['verified']); self.assertTrue(run['result']['retained'])
        run = self.run_record('b' * 32)
        with mock.patch.object(service, 'prepare_plan', return_value=self.plan), mock.patch.object(service, 'run_cli', return_value={**result, 'coverage': {'complete': False}}): service.process_job(self.config, run)
        self.assertEqual(run['status'], 'failed')
    def test_retention_preserves_n_newest_and_last_successful_drill(self):
        control.atomic_json(self.state / 'settings.json', {'enabled': False, 'hourUTC': 2, 'minuteUTC': 30, 'retention': 2})
        captures = []
        for number in range(1, 6):
            row = {'id': f'{number:032x}', 'operation': 'capture', 'status': 'ready', 'created': f'2026-10-{number:02}T00:00:00Z', 'result': {'verified': True, 'coverage': {'complete': True}, 'object': f'app1/platform-recovery/snapshot-{number}/platform.phpr', 'references': []}}
            control.save_run(row); captures.append(row)
        control.save_run({'id': 'd' * 32, 'operation': 'drill', 'status': 'ready', 'created': '2026-10-06T00:00:00Z', 'finishedAt': '2026-10-06T00:00:00Z', 'sourceRunId': captures[0]['id'], 'verified': True, 'result': {'paused': True}})
        deleted = []
        with mock.patch.object(engine, 's3_settings', return_value={'prefix': 'app1/platform-recovery'}), mock.patch.object(engine, 's3_client', return_value=object()), mock.patch.object(engine, 'purge_capture', side_effect=lambda _a, _b, key, _refs: deleted.append(key)):
            result = service.apply_retention(captures[-1], captures[-1]['result'])
        self.assertEqual(result['removed'], 2); self.assertEqual(set(deleted), {'app1/platform-recovery/snapshot-2/platform.phpr', 'app1/platform-recovery/snapshot-3/platform.phpr'})
        stored = {row['id']: row for row in control.all_runs()}; self.assertFalse(stored[captures[1]['id']]['result']['retained']); self.assertNotEqual(stored[captures[0]['id']]['result'].get('retained'), False)
    def test_partial_retention_remains_non_drillable_without_hiding_new_capture(self):
        control.atomic_json(self.state / 'settings.json', {'enabled': False, 'hourUTC': 2, 'minuteUTC': 30, 'retention': 1})
        old = {'id': 'a' * 32, 'operation': 'capture', 'status': 'ready', 'created': '2026-10-01T00:00:00Z', 'result': {'verified': True, 'coverage': {'complete': True}, 'object': 'app1/platform-recovery/old/platform.phpr', 'references': []}}; control.save_run(old)
        new = {'id': 'b' * 32, 'operation': 'capture', 'status': 'ready', 'created': '2026-10-02T00:00:00Z', 'result': {'verified': True, 'coverage': {'complete': True}, 'object': 'app1/platform-recovery/new/platform.phpr', 'references': []}}; control.save_run(new)
        with mock.patch.object(engine, 's3_settings', return_value={'prefix': 'app1/platform-recovery'}), mock.patch.object(engine, 's3_client', return_value=object()), mock.patch.object(engine, 'purge_capture', side_effect=RuntimeError('private-provider-detail')):
            result = service.apply_retention(new, new['result'])
        stored = {row['id']: row for row in control.all_runs()}; self.assertTrue(result['delayed']); self.assertTrue(stored[old['id']]['result']['retentionPending']); self.assertFalse(control.public_job(stored[old['id']])['recoverable']); self.assertTrue(control.public_job(stored[new['id']])['recoverable']); self.assertNotIn('private-provider-detail', json.dumps(stored))
    def test_child_json_feedback_is_parsed_without_a_shell_or_password_args(self):
        run = self.run_record(); captured = []
        class Process:
            pid = 999999
            def __init__(self, args, **kwargs): captured.append((args, kwargs)); self.stdout = io.StringIO(json.dumps({'status': 'complete', 'verified': True, 'coverage': {'complete': True}}) + '\n'); self.stderr = io.StringIO(json.dumps({'phase': 'upload', 'encryptedBytes': 123}) + '\n')
            def poll(self): return 0
            def wait(self, timeout=None): return 0
        with mock.patch.object(service.subprocess, 'Popen', Process): result = service.run_cli(self.config, run, ['backup', '--plan', str(self.plan), '--key-file', str(self.key)])
        self.assertTrue(result['verified']); self.assertEqual(run['bytes'], 123); self.assertNotIn('synthetic-private-password', json.dumps(captured[0][0])); self.assertNotIn('shell', captured[0][1])

    def capture_row(self, number, **extra):
        row = {'id': f'{number:032x}', 'operation': 'capture', 'status': 'ready', 'created': f'2026-10-{number:02}T00:00:00Z', 'result': {'verified': True, 'coverage': {'complete': True}, 'backupId': f'backup-{number}', 'object': f'app1/platform-recovery/backup-{number}/platform.phpr', 'references': []}}
        row.update(extra); control.save_run(row); return row
    def s3(self):
        return mock.patch.object(engine, 's3_settings', return_value={'prefix': 'app1/platform-recovery'}), mock.patch.object(engine, 's3_client', return_value=object())
    def test_platform_comes_from_config_with_app1_default(self):
        for name in ['platform']: self.config.pop(name)
        self.cfgfile.write_text(json.dumps(self.config)); self.assertEqual(service.config_file(self.cfgfile)['platform'], 'app1')
        plan = json.loads(self.plan.read_text()); plan['platform'] = 'app2'; self.plan.write_text(json.dumps(plan))
        with self.assertRaises(engine.RecoveryError): service.config_file(self.cfgfile)  # Plan of another platform.
        self.cfgfile.write_text(json.dumps({**self.config, 'platform': 'app2'}))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('WEB_PLATFORM_RECOVERY_PREFIX', None); config = service.config_file(self.cfgfile)
            self.assertEqual(os.environ['WEB_PLATFORM_RECOVERY_PREFIX'], 'app2/platform-recovery')
        service.heartbeat(config); self.assertEqual(control.read_json(self.state / 'worker.json')['platform'], 'app2')
        self.cfgfile.write_text(json.dumps({**self.config, 'platform': '../app2'}))
        with self.assertRaises(engine.RecoveryError): service.config_file(self.cfgfile)
    def test_capture_is_durably_ready_before_purge_and_killed_purge_resumes(self):
        control.atomic_json(self.state / 'settings.json', {'enabled': False, 'hourUTC': 2, 'minuteUTC': 30, 'retention': 1})
        old = self.capture_row(1); run = self.run_record('f' * 32)
        class Killed(BaseException): pass
        def killed(_client, _config, key, _references):
            stored = {row['id']: row for row in control.all_runs()}
            self.assertEqual(stored[run['id']]['status'], 'ready'); self.assertTrue(stored[old['id']]['result']['retentionPending'])
            raise Killed()
        result = {'status': 'complete', 'verified': True, 'coverage': {'complete': True}, 'object': 'app1/platform-recovery/new/platform.phpr', 'references': []}
        settings, client = self.s3()
        with settings, client, mock.patch.object(service, 'prepare_plan', return_value=self.plan), mock.patch.object(service, 'run_cli', return_value=result), mock.patch.object(engine, 'purge_capture', side_effect=killed):
            with self.assertRaises(Killed): service.process_job(self.config, run)
        stored = {row['id']: row for row in control.all_runs()}
        self.assertEqual(stored[run['id']]['status'], 'ready'); self.assertTrue(control.public_job(stored[run['id']])['recoverable']); self.assertFalse(control.public_job(stored[old['id']])['recoverable'])
        purged = []
        settings, client = self.s3()
        with settings, client, mock.patch.object(engine, 'purge_capture', side_effect=lambda _a, _b, key, _refs: purged.append(key)):
            self.assertEqual(service.resume_retention(), {'resumed': 1, 'pending': 1})
        self.assertEqual(purged, [old['result']['object']]); self.assertFalse({row['id']: row for row in control.all_runs()}[old['id']]['result']['retained'])
    def drill_fixture(self):
        source = self.capture_row(1); run = {'id': 'e' * 32, 'operation': 'drill', 'status': 'queued', 'created': control.stamp(), 'sourceRunId': source['id']}; control.save_run(run)
        target = pathlib.Path(self.config['drillRoot']) / run['id']
        def restored(_config, _run, arguments):
            target.mkdir(parents=True); (target / 'secret-copy.db').write_text('restored production data'); return {'status': 'restored-paused', 'paused': True, 'noServicesStarted': True, 'databasesRestored': ['controller']}
        return run, target, restored
    def test_drill_runs_verify_only_then_removes_copy_and_keeps_report(self):
        run, target, restored = self.drill_fixture(); calls = []
        def probe(command, **kwargs):
            calls.append(command); report = pathlib.Path(command[command.index('--report') + 1]); report.write_text('{}'); report.chmod(0o600)
            return types.SimpleNamespace(returncode=0, stdout=json.dumps({'passed': True, 'databases': 1, 'files': 4, 'fileBytes': 10, 'mfaSidecars': 1}) + '\n')
        with mock.patch.object(service, 'run_cli', side_effect=restored), mock.patch.object(service.subprocess, 'run', side_effect=probe): service.process_job(self.config, run)
        self.assertEqual(run['status'], 'ready'); self.assertFalse(target.exists()); self.assertTrue(run['result']['cleanedUp'])
        self.assertEqual(run['result']['verification']['files'], 4); self.assertIn('--verify-only', calls[0]); self.assertEqual(calls[0][calls[0].index('--expected-databases') + 1], '1')
        self.assertTrue((self.state / 'reports' / (run['id'] + '-verify.json')).exists()); self.assertEqual((self.state / 'reports').stat().st_mode & 0o777, 0o700)
        self.assertTrue(control.public_job(run)['result']['verification']['passed'])
    def test_failed_drill_verification_is_reported_and_copy_still_removed(self):
        run, target, restored = self.drill_fixture()
        with mock.patch.object(service, 'run_cli', side_effect=restored), mock.patch.object(service.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1, stdout=json.dumps({'passed': False}))): service.process_job(self.config, run)
        self.assertEqual(run['status'], 'failed'); self.assertEqual(run['errorCode'], 'VERIFICATION'); self.assertFalse(target.exists()); self.assertTrue(run['cleanedUp'])
        leftover = pathlib.Path(self.config['drillRoot']) / ('a' * 32); leftover.mkdir(); (pathlib.Path(self.config['drillRoot']) / 'not-a-run').mkdir()
        self.assertEqual(service.sweep_drills(self.config)['removed'], 1); self.assertFalse(leftover.exists()); self.assertTrue((pathlib.Path(self.config['drillRoot']) / 'not-a-run').exists())
    def test_restart_after_sigkill_fails_stale_runs_sweeps_and_emits_secret_free_events(self):
        config = {**self.config, 'sweepEnabled': True, 'failureEvents': {'enabled': True, 'path': service.EVENT_PATH, 'secretFile': ''}}
        capture = {'id': 'c' * 32, 'operation': 'capture', 'status': 'running', 'created': control.stamp(), 'stageCode': 'capture', 'privateBackupId': 'killed-backup', 'privateEngineError': 'private engine detail', 'trigger': 'scheduled'}
        drill = {'id': 'd' * 32, 'operation': 'drill', 'status': 'running', 'created': control.stamp(), 'sourceRunId': 'a' * 32}
        for row in [capture, drill]: control.save_run(row)
        copy = pathlib.Path(config['drillRoot']) / drill['id']; copy.mkdir(parents=True); (copy / 'data.db').write_text('copy')
        sent, swept = [], []
        with mock.patch.object(service, 'sweep_storage', side_effect=lambda _config, failed=(), dry_run=False: swept.append(set(failed)) or {'objectsDeleted': 2}), mock.patch.object(service, 'send_event', side_effect=lambda _config, payload: sent.append(payload) or True):
            self.assertFalse(service.worker_once(config))
        stored = {row['id']: row for row in control.all_runs()}
        self.assertEqual({row['status'] for row in stored.values()}, {'failed'}); self.assertFalse(copy.exists()); self.assertEqual(swept, [{'killed-backup'}])
        self.assertEqual(stored[capture['id']]['sweep'], {'objectsDeleted': 2}); self.assertEqual(stored[capture['id']]['failureEvent']['status'], 'sent')
        self.assertEqual({payload['operation'] for payload in sent}, {'capture', 'drill'}); self.assertEqual(next(p for p in sent if p['operation'] == 'capture')['trigger'], 'scheduled')
        self.assertNotIn('private engine detail', json.dumps(sent)); self.assertEqual(sent[0]['event'], 'recovery_failed'); self.assertTrue(sent[0]['eventKey'].startswith('platform-recovery:'))
    def test_failure_event_is_off_by_default_retries_and_uses_worker_header_on_loopback(self):
        run = {'id': 'b' * 32, 'operation': 'capture', 'status': 'failed', 'created': control.stamp(), 'errorCode': 'CONFIGURATION'}; control.save_run(run)
        service.after_failure(self.config, run); self.assertNotIn('failureEvent', run)
        secret = self.root / 'worker.secret'; secret.write_text('w' * 40); secret.chmod(0o600)
        config = {**self.config, 'failureEvents': {'enabled': True, 'path': service.EVENT_PATH, 'secretFile': str(secret)}}; requests = []
        class Response:
            status = 202
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size): return b'{}'
        class Opener:
            def open(self, request, timeout): requests.append(request); return Response()
        with mock.patch.object(service.urllib.request, 'build_opener', return_value=Opener()): self.assertTrue(service.send_event(config, service.failure_payload(config, run)))
        self.assertEqual(requests[0].full_url, 'http://127.0.0.1:8091' + service.EVENT_PATH); self.assertEqual(requests[0].get_header('X-web-worker'), 'w' * 40)
        self.assertEqual(json.loads(requests[0].data)['runId'], run['id'])
        run['failureEvent'] = {'status': 'pending', 'attempts': 0}; now = datetime.datetime.now(datetime.timezone.utc)
        with mock.patch.object(service, 'send_event', side_effect=OSError('refused')):
            for attempt in range(1, service.EVENT_ATTEMPTS + 1):
                service.deliver_event(config, run, now + datetime.timedelta(days=attempt)); self.assertEqual(run['failureEvent']['attempts'], attempt)
            self.assertEqual(run['failureEvent']['status'], 'failed')
    def test_cli_hold_and_recorded_drill_protect_their_source_from_retention(self):
        control.atomic_json(self.state / 'settings.json', {'enabled': False, 'hourUTC': 2, 'minuteUTC': 30, 'retention': 1})
        captures = [self.capture_row(number) for number in range(1, 5)]
        held = service.hold_capture(captures[0]['id'], 24, 'test complet 7 bases'); self.assertEqual(held['object'], captures[0]['result']['object'])
        report = self.root / 'verify.json'; report.write_text(json.dumps({'passed': True, 'backupId': 'backup-2', 'databases': [{'id': 'controller'}, {'id': 'a' * 15}], 'files': 3})); report.chmod(0o600)
        recorded = service.record_drill(captures[1]['id'], report); self.assertEqual(recorded['databases'], 2)
        purged = []; settings, client = self.s3()
        with settings, client, mock.patch.object(engine, 'purge_capture', side_effect=lambda _a, _b, key, _refs: purged.append(key)):
            service.apply_retention(captures[-1], captures[-1]['result'])
            self.assertEqual(purged, [captures[2]['result']['object']])
            service.release_capture(captures[0]['id']); service.apply_retention(captures[-1], captures[-1]['result'])
        self.assertIn(captures[0]['result']['object'], purged); self.assertNotIn(captures[1]['result']['object'], purged)
        drill = next(row for row in control.all_runs() if row['operation'] == 'drill'); self.assertEqual((drill['trigger'], drill['status'], drill['sourceRunId']), ('cli', 'ready', captures[1]['id']))
        report.write_text(json.dumps({'passed': True, 'backupId': 'other-backup', 'databases': [{'id': 'controller'}]}))
        with self.assertRaises(engine.RecoveryError): service.record_drill(captures[1]['id'], report)
        with self.assertRaises(engine.RecoveryError): service.hold_capture(captures[0]['id'], 24)  # Purged: no longer recoverable.
    def test_sweep_protects_ready_and_active_captures_and_targets_failed_ones(self):
        self.capture_row(1); self.capture_row(2, status='failed', privateBackupId='failed-backup', result={}); self.capture_row(3, status='running', privateBackupId='active-backup', result={})
        calls = []; settings, client = self.s3()
        with settings, client, mock.patch.object(engine, 'sweep_orphans', side_effect=lambda _c, _cfg, protected, failed, age, dry_run=False: calls.append((protected, failed, age, dry_run)) or {}):
            service.sweep_storage({**self.config, 'sweepMinAgeHours': 6}, {'extra'}, dry_run=True)
        self.assertEqual(calls[0], ({'backup-1', 'active-backup'}, {'failed-backup', 'extra'}, 6 * 3600, True))
    def test_plan_from_inventory_follows_new_instances_at_prepare_time(self):
        data = self.root / 'data'; controller = data / 'mothership/pb_data/data.db'; controller.parent.mkdir(parents=True); new = 'n' * 15
        with sqlite3.connect(controller) as db: db.execute('CREATE TABLE instances(id TEXT,version TEXT)'); db.execute('INSERT INTO instances VALUES(?,?)', (new, '0.39.*'))
        (data / 'instances' / new / 'pb_data').mkdir(parents=True); (data / 'instances' / new / 'pb_data/data.db').write_bytes(b'')
        self.plan.write_text(json.dumps({'version': 1, 'platform': 'app1', 'controller': {'database': str(controller)}, 'instances': [], 'hostPaths': [{'source': '/opt/pb', 'target': 'platform/pocketbase'}]}))
        class Controller:
            def __init__(self, _config): pass
            def authenticate(self): pass
            def records(self, *_args, **_kwargs): return []
            def request(self, path, *_args, **_kwargs): return {'items': [], 'hasMore': False}
        with mock.patch.object(service, 'Controller', Controller): prepared = json.loads(service.prepare_plan({**self.config, 'planFromInventory': True}, self.run_record()).read_text())
        self.assertEqual([item['id'] for item in prepared['instances']], [new]); self.assertEqual(prepared['hostPaths'][0]['target'], 'platform/pocketbase')
        with mock.patch.object(service, 'Controller', Controller): static = json.loads(service.prepare_plan(self.config, self.run_record('9' * 32)).read_text())
        self.assertEqual(static['instances'], [])

    def test_engine_budget_failure_maps_to_insufficient_space(self):
        run, target, _restored = self.drill_fixture()
        def refused(_config, current, _arguments):
            current['privateEngineError'] = 'La sélection dépasse le budget fixe du test de reconstruction.'; raise engine.RecoveryError('Le moteur n’a pas confirmé une capture vérifiée.')
        with mock.patch.object(service, 'run_cli', side_effect=refused): service.process_job(self.config, run)
        self.assertEqual(run['errorCode'], 'INSUFFICIENT_SPACE'); self.assertNotIn('budget', json.dumps(control.public_job(run)))


if __name__ == '__main__': unittest.main()
