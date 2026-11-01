#!/usr/bin/env python3
"""File-control tests: no capture process, private data never enters public responses."""
import concurrent.futures
import datetime
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('control', pathlib.Path(__file__).with_name('web_platform_recovery_control.py'))
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
ACTOR = 'a' * 15


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ph-recovery-control-')
        self.root = pathlib.Path(self.temp.name).resolve(); self.root.chmod(0o700)
        self.env = patch.dict(os.environ, {'WEB_PLATFORM_RECOVERY_STATE_DIR': str(self.root)}); self.env.start()
        c.atomic_json(self.root / 'worker.json', {'configured': True, 'online': True, 'lastSeenAt': c.stamp(), 'capabilities': {'canDrill': True}, 'drillSelection': ['controller', 'b' * 15]})

    def tearDown(self): self.env.stop(); self.temp.cleanup()

    def ready(self, number=1, **extra):
        row = {'id': format(number, '032x'), 'operation': 'capture', 'status': 'ready', 'created': f'2026-10-{number % 28 + 1:02}T02:30:00Z', 'result': {'object': 'private/object/key', 'verified': True, 'coverage': {'complete': True}}}
        row.update(extra); c.save_run(row); return row

    def test_disabled_default_settings_and_service_presence(self):
        result = c.api('status', {})
        self.assertEqual(result['status'], 200); self.assertTrue(result['response']['configured'])
        self.assertFalse(result['response']['settings']['enabled']); self.assertIsNone(c.schedule_tick())
        (self.root / 'worker.json').unlink()
        status = c.api('status', {})['response']; self.assertFalse(status['configured']); self.assertFalse(status['worker']['online'])
        self.assertEqual(c.api('capture', {'actorId': ACTOR})['status'], 400)

    def test_strict_settings_and_no_unknown_arguments(self):
        settings = {'actorId': ACTOR, 'enabled': True, 'hourUTC': 23, 'minuteUTC': 59, 'retention': 30}
        self.assertEqual(c.api('settings', settings)['status'], 200)
        for key, value in [('hourUTC', 24), ('minuteUTC', -1), ('enabled', 1), ('retention', 31), ('retention', True)]:
            self.assertEqual(c.api('settings', {**settings, key: value})['status'], 400)
        for op, body in [('capture', {'actorId': ACTOR, 'plan': '/etc/shadow'}), ('drill', {'actorId': ACTOR, 'backupId': '../outside'}), ('settings', {**settings, 'key': 'private'}), ('capture', {'actorId': 'bad'})]:
            self.assertEqual(c.api(op, body)['status'], 400)
        self.assertFalse(c.all_runs())

    def test_concurrent_capture_enqueues_one_job(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: c.api('capture', {'actorId': ACTOR}), range(8)))
        self.assertEqual(sum(value['status'] == 202 for value in results), 1)
        self.assertEqual(sum(value['status'] == 409 for value in results), 7)
        self.assertEqual(len(c.all_runs()), 1)
        row = c.all_runs()[0]; self.assertEqual(row['actorId'], ACTOR)
        self.assertEqual((self.root / 'runs' / (row['id'] + '.json')).stat().st_mode & 0o777, 0o600)

    def test_schedule_utc_one_occurrence_under_concurrency(self):
        c.atomic_json(self.root / 'settings.json', {'enabled': True, 'hourUTC': 2, 'minuteUTC': 30, 'retention': 3})
        actual = datetime.datetime
        class Fixed(actual):
            @classmethod
            def now(cls, tz=None): return actual(2026, 10, 29, 2, 30, tzinfo=datetime.timezone.utc)
        with patch.object(c.datetime, 'datetime', Fixed):
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool: results = list(pool.map(lambda _: c.schedule_tick(), range(5)))
            self.assertEqual(sum(value is not None for value in results), 1)
            run = c.all_runs()[0]; run['status'] = 'ready'; c.save_run(run)
            self.assertIsNone(c.schedule_tick())
            stored = c.read_json(self.root / 'settings.json'); del stored['lastScheduledDate']; c.atomic_json(self.root / 'settings.json', stored)
            self.assertIsNone(c.schedule_tick())  # Durable run prevents duplicate after an interrupted settings write.
            stored['lastScheduledDate'] = '2026-10-29'; c.atomic_json(self.root / 'settings.json', stored)
        self.assertEqual(c.read_json(self.root / 'settings.json')['lastScheduledDate'], '2026-10-29')

    def test_schedule_waits_until_due_and_preserves_busy_occurrence(self):
        c.atomic_json(self.root / 'settings.json', {'enabled': True, 'hourUTC': 23, 'minuteUTC': 59, 'retention': 3})
        actual = datetime.datetime
        class Fixed(actual):
            @classmethod
            def now(cls, tz=None): return actual(2026, 10, 29, 12, 0, tzinfo=datetime.timezone.utc)
        with patch.object(c.datetime, 'datetime', Fixed): self.assertIsNone(c.schedule_tick())
        c.api('capture', {'actorId': ACTOR})
        c.atomic_json(self.root / 'settings.json', {'enabled': True, 'hourUTC': 0, 'minuteUTC': 0, 'retention': 3})
        with patch.object(c.datetime, 'datetime', Fixed): self.assertIsNone(c.schedule_tick())
        self.assertNotIn('lastScheduledDate', c.read_json(self.root / 'settings.json'))

    def test_public_view_drops_objects_paths_keys_commands_errors_and_actor(self):
        private = 'bare-private-token-1234567890'
        row = self.ready(result={'object': '/private/bucket/key', 'verified': True, 'password': private, 'coverage': {'complete': False, 'instancesMissing': ['x'] * 1200, 'unprotectedWebImages': ['private/path'], 'ephemeralPreviewsExcluded': ['private-preview-id'] * 2, 'scope': 'persistent-platform-resources'}, 'databasesRestored': ['controller', '../../private']}, stage=private, error=private, key=private, actorId=ACTOR)
        view = c.public_job(row); raw = json.dumps(view)
        for value in [private, '/private', '../../private', ACTOR, 'object', 'password']:
            self.assertNotIn(value, raw)
        self.assertEqual(view['coverage']['instancesMissingCount'], 1200)
        self.assertEqual(view['coverage']['unprotectedWebImagesCount'], 1)
        self.assertEqual(view['coverage']['ephemeralPreviewsExcludedCount'], 2)
        self.assertNotIn('private-preview-id', raw)
        self.assertEqual(view['result']['databasesRestored'], ['controller'])

    def test_drill_source_must_be_verified_ready_capture_with_object(self):
        row = self.ready()
        result = c.api('drill', {'actorId': ACTOR, 'backupId': row['id']}); self.assertEqual(result['status'], 202)
        run = c.all_runs()[0]; self.assertEqual(run['operation'], 'drill'); self.assertEqual(run['sourceRunId'], row['id'])
        run['status'] = 'failed'; c.save_run(run)
        row['result']['verified'] = False; c.save_run(row)
        self.assertEqual(c.api('drill', {'actorId': ACTOR, 'backupId': row['id']})['status'], 400)

    def test_retention_keeps_history_but_prevents_drill_of_purged_capture(self):
        source = self.ready()
        self.assertTrue(c.public_job(source)['recoverable'])
        source['result']['retentionPending'] = True; c.save_run(source)
        self.assertFalse(c.public_job(source)['recoverable'])
        self.assertTrue(c.public_job(source)['retentionPending'])
        self.assertEqual(c.api('drill', {'actorId': ACTOR, 'backupId': source['id']})['status'], 400)
        source['result']['retained'] = False; source['result']['retentionPending'] = False; c.save_run(source)
        view = c.public_job(source)
        self.assertFalse(view['retained']); self.assertFalse(view['recoverable'])
        self.assertEqual(c.api('drill', {'actorId': ACTOR, 'backupId': source['id']})['status'], 400)
        self.assertEqual(c.api('runs', {'page': 1})['response']['totalItems'], 1)

    def test_pagination_exhaustive_and_latest_ignores_drills_and_failures(self):
        for number in range(1, 42): self.ready(number)
        newest = c.all_runs()[0]
        self.ready(100, operation='drill', created='2099-01-01T00:00:00Z')
        self.ready(101, status='failed', created='2099-01-02T00:00:00Z')
        pages = [c.api('runs', {'page': page})['response'] for page in range(1, 4)]
        self.assertEqual([len(page['items']) for page in pages], [20, 20, 3])
        self.assertEqual(len({row['id'] for page in pages for row in page['items']}), 43)
        self.assertEqual(c.api('status', {})['response']['latest']['id'], newest['id'])
        for page in [0, -1, 1.5, True, 100001]: self.assertEqual(c.api('runs', {'page': page})['status'], 400)

    def test_stale_or_naive_worker_heartbeat_disables_actions(self):
        for timestamp in ['2000-01-01T00:00:00Z', '2026-10-29T02:30:00', 'invalid']:
            c.atomic_json(self.root / 'worker.json', {'online': True, 'lastSeenAt': timestamp})
            self.assertFalse(c.api('status', {})['response']['worker']['online'])
            self.assertEqual(c.api('capture', {'actorId': ACTOR})['status'], 400)

    def test_real_service_api_wrapper_never_opens_private_config(self):
        script = pathlib.Path(__file__).with_name('web_platform_recovery_service.py')
        def call(operation, body):
            result = subprocess.run([sys.executable, str(script), 'api', operation, json.dumps(body)], capture_output=True, text=True, check=True, env=os.environ.copy())
            self.assertFalse(result.stderr)
            return json.loads(result.stdout)
        source = self.ready()
        result = call('drill', {'actorId': ACTOR, 'backupId': source['id']})
        self.assertEqual(result['status'], 202)
        status = call('status', {})['response']; history = call('runs', {'page': 1})['response']
        self.assertEqual(status['latest']['id'], source['id']); self.assertEqual(status['active']['operation'], 'drill')
        self.assertEqual(len(history['items']), 2)
        self.assertNotIn('private/object/key', json.dumps([status, history]))
        self.assertNotIn('sourceRunId', json.dumps(history))

    def test_symlinks_broad_permissions_and_corrupt_job_fail_closed(self):
        self.root.chmod(0o755); self.assertFalse(c.api('status', {})['response']['configured']); self.root.chmod(0o700)
        outside = self.root / 'outside'; outside.write_text('{}'); outside.chmod(0o600)
        (self.root / 'settings.json').symlink_to(outside)
        self.assertFalse(c.api('status', {})['response']['configured']); (self.root / 'settings.json').unlink()
        self.ready(); filename = next((self.root / 'runs').glob('*.json')); filename.write_text('{broken'); filename.chmod(0o600)
        self.assertEqual(c.api('capture', {'actorId': ACTOR})['status'], 503)

    def fixed(self, hour, minute=0):
        actual = datetime.datetime
        class Fixed(actual):
            @classmethod
            def now(cls, tz=None): return actual(2026, 10, 29, hour, minute, tzinfo=datetime.timezone.utc)
        return patch.object(c.datetime, 'datetime', Fixed)

    def test_enabling_after_daily_time_waits_for_tomorrow_unless_run_today(self):
        settings = {'actorId': ACTOR, 'enabled': True, 'hourUTC': 21, 'minuteUTC': 30, 'retention': 3}
        with self.fixed(22):
            c.atomic_json(self.root / 'worker.json', {'configured': True, 'online': True, 'lastSeenAt': c.stamp(), 'capabilities': {'canDrill': True}})
            response = c.api('settings', settings)['response']
            self.assertFalse(response['immediate']); self.assertEqual(response['settings']['nextRunAt'][:16], '2026-10-30T21:30')
            self.assertIsNone(c.schedule_tick()); self.assertEqual(c.all_runs(), [])
            self.assertEqual(c.read_json(self.root / 'settings.json')['skippedScheduledDate'], '2026-10-29')
            c.api('settings', {**settings, 'retention': 4}); self.assertIsNone(c.schedule_tick())  # Re-saving keeps the skip.
            response = c.api('settings', {**settings, 'runToday': True})['response']; self.assertTrue(response['immediate'])
            self.assertEqual(c.schedule_tick()['trigger'], 'scheduled')
        self.assertEqual(c.api('settings', {**settings, 'runToday': 'yes'})['status'], 400)

    def test_execution_locked_at_daily_time_consumes_that_day(self):
        settings = {'actorId': ACTOR, 'enabled': True, 'hourUTC': 21, 'minuteUTC': 30, 'retention': 3}
        with self.fixed(20): c.api('settings', settings); self.assertFalse(c.skip_locked_occurrence())  # Before the time: nothing consumed.
        with self.fixed(22):
            self.assertTrue(c.skip_locked_occurrence()); self.assertFalse(c.skip_locked_occurrence())
            self.assertIsNone(c.schedule_tick()); self.assertEqual(c.all_runs(), [])  # Lock lifted at 22:00: no mid-day capture.
            c.api('settings', {**settings, 'runToday': True}); self.assertEqual(c.schedule_tick()['trigger'], 'scheduled')

    def test_enabling_before_daily_time_and_catch_up_after_outage_are_unchanged(self):
        settings = {'actorId': ACTOR, 'enabled': True, 'hourUTC': 21, 'minuteUTC': 30, 'retention': 3}
        with self.fixed(20): c.api('settings', settings)
        self.assertNotIn('skippedScheduledDate', c.read_json(self.root / 'settings.json'))
        with self.fixed(23): self.assertIsNotNone(c.schedule_tick())  # Service offline at 21:30: today still runs.
        stored = c.read_json(self.root / 'settings.json'); self.assertEqual(stored['lastScheduledDate'], '2026-10-29')
        with self.fixed(10): self.assertEqual(c.next_run(c.get_settings(), stored=stored)[:16], '2026-10-30T21:30')  # Today already consumed.
        with self.fixed(21, 40): self.assertEqual(c.get_settings()['nextRunAt'][:10], '2026-10-30')

    def test_locked_execution_is_reported_instead_of_a_next_run(self):
        c.atomic_json(self.root / 'worker.json', {'configured': True, 'online': True, 'lastSeenAt': c.stamp(), 'executionEnabled': False, 'capabilities': {'canCapture': False, 'canDrill': False}})
        settings = {'actorId': ACTOR, 'enabled': True, 'hourUTC': 23, 'minuteUTC': 59, 'retention': 3}
        response = c.api('settings', settings)['response']['settings']
        self.assertEqual(response['status'], 'executionDisabled'); self.assertEqual(response['nextRunAt'], ''); self.assertIn('executionEnabled=false', response['statusMessage']); self.assertTrue(response['plannedRunAt'])
        status = c.api('status', {})['response']; self.assertFalse(status['executionEnabled']); self.assertEqual(status['settings']['status'], 'executionDisabled')
        c.atomic_json(self.root / 'worker.json', {'configured': True, 'online': True, 'lastSeenAt': '2000-01-01T00:00:00Z', 'executionEnabled': True})
        self.assertEqual(c.api('status', {})['response']['settings']['status'], 'serviceOffline')
        c.atomic_json(self.root / 'worker.json', {'configured': True, 'online': True, 'lastSeenAt': c.stamp(), 'executionEnabled': True})
        view = c.api('status', {})['response']['settings']; self.assertEqual(view['status'], 'active'); self.assertTrue(view['nextRunAt'])
        c.api('settings', {**settings, 'enabled': False}); self.assertEqual(c.api('status', {})['response']['settings']['status'], 'disabled')

    def test_public_view_reports_hold_verification_and_cleanup_without_paths(self):
        row = self.ready(holds=[{'until': '2999-01-01T00:00:00+00:00', 'label': '/private/label'}])
        self.assertTrue(c.public_job(row)['held']); row['holds'] = [{'until': '2000-01-01T00:00:00+00:00'}]; self.assertFalse(c.public_job(row)['held'])
        drill = {'id': 'd' * 32, 'operation': 'drill', 'status': 'ready', 'trigger': 'cli', 'result': {'paused': True, 'cleanedUp': True, 'verification': {'passed': True, 'databases': 2, 'files': 3, 'report': '/private/report.json'}}}
        view = c.public_job(drill); self.assertEqual(view['trigger'], 'cli'); self.assertTrue(view['result']['cleanedUp']); self.assertEqual(view['result']['verification']['databases'], 2)
        self.assertNotIn('/private', json.dumps(view))
        failed = {'id': 'e' * 32, 'operation': 'drill', 'status': 'failed', 'errorCode': 'VERIFICATION', 'error': 'x'}
        self.assertIn('contrôle', c.public_job(failed)['error'])


if __name__ == '__main__': unittest.main()
