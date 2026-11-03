#!/usr/bin/env python3
"""Synthetic Linux counters, HTTP Unix sockets, disk trees and bounded history."""
import copy
import importlib.util
import json
import os
import pathlib
import socketserver
import sqlite3
import stat
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('platform_resource_collector', HERE / 'platform_resource_collector.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
APP = 'a' * 15
DB = 'b' * 15
WEB_CID = '1' * 64
DB_CID = '2' * 64
BUILD_CID = '3' * 64


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.proc, self.cgroup = self.root / 'proc', self.root / 'cgroup'
        self.proc.mkdir()
        self.cgroup.mkdir()
        self.config = {'version': 1, 'hostname': 'node.example.test', 'outputDir': str(self.root / 'out'),
                       'readGroup': 'root', 'docker': [], 'services': [],
                       'filesystems': [{'path': str(self.root), 'mount': '/'}], 'diskRoots': []}
        self.write_host()

    def tearDown(self):
        self.temp.cleanup()

    def write_host(self, user=100, idle=900, cores=4, available=512):
        self.proc.joinpath('stat').write_text('cpu  %d 0 0 %d 0 0 0 0 80 0\n' % (user, idle) +
                                            ''.join('cpu%d 1 0 0 9 0 0 0 0 0 0\n' % index for index in range(cores)))
        self.proc.joinpath('meminfo').write_text('MemTotal: 1024 kB\nMemFree: 1 kB\nMemAvailable: %d kB\n' % available)
        self.proc.joinpath('uptime').write_text('100.25 50.5\n')

    def scope(self, cid=WEB_CID, usage=100, memory=100000, inactive=20000, parent=None):
        path = (parent or self.cgroup / 'system.slice') / ('docker-' + cid + '.scope')
        self.counters(path, usage, memory, inactive)
        return path

    def counters(self, path, usage=100, memory=100000, inactive=20000, limit=200000):
        path.mkdir(parents=True, exist_ok=True)
        path.joinpath('cpu.stat').write_text('usage_usec %d\nuser_usec 10\nsystem_usec 5\n' % usage)
        path.joinpath('memory.current').write_text(str(memory))
        path.joinpath('memory.stat').write_text('inactive_file %d\nactive_file 100\n' % inactive)
        path.joinpath('memory.max').write_text(str(limit))

    def collector(self, rows=None, services=None):
        config = copy.deepcopy(self.config)
        if rows is not None:
            config['docker'] = [{'kind': 'rootless', 'socket': '/unused'}]
        if services is not None:
            config['services'] = services
        return m.Collector(config, self.proc, self.cgroup, docker_reader=lambda _endpoint: (rows or [], 0))

    @staticmethod
    def row(cid=WEB_CID, ident=APP, category='hosting', kind='application', state='running'):
        return {'cid': cid, 'id': ident, 'category': category, 'kind': kind, 'state': state}

    @staticmethod
    def category(sample, ident):
        return next(row for row in sample['categories'] if row['id'] == ident)


class CounterTests(Fixture):
    def test_host_memavailable_and_cpu_guest_not_double_counted(self):
        value = m.host_counters(self.proc)
        self.assertEqual(value['cpu'], (1000, 900))
        self.assertEqual(value['cpuCount'], 4)
        self.assertEqual(value['memoryUsedBytes'], 512 * 1024)
        self.assertEqual(value['uptimeSeconds'], 100.25)

    def test_first_cpu_unknown_then_host_normalised_all_cores(self):
        scope = self.scope(usage=100)
        collector = self.collector([self.row()])
        first = collector.sample(now=1)
        self.assertIsNone(first['host']['cpuPercent'])
        self.assertIsNone(first['resources'][0]['cpuPercent'])
        self.assertEqual(first['resources'][0]['memoryBytes'], 80000)
        self.write_host(user=500, idle=1100)
        self.counters(scope, usage=15000100)
        second = collector.sample(now=16)
        self.assertEqual(second['host']['cpuPercent'], 66.667)
        self.assertEqual(second['resources'][0]['cpuPercent'], 25)
        self.assertEqual(self.category(second, 'system')['cpuPercent'], 41.667)

    def test_counter_reset_and_cpu_hotplug_are_unknown(self):
        scope = self.scope(usage=1000)
        collector = self.collector([self.row()])
        collector.sample(now=1)
        self.counters(scope, usage=1)
        self.write_host(user=1, idle=1)
        reset = collector.sample(now=16)
        self.assertIsNone(reset['host']['cpuPercent'])
        self.assertIsNone(reset['resources'][0]['cpuPercent'])
        self.write_host(user=100, idle=100, cores=2)
        self.counters(scope, usage=10000)
        hotplug = collector.sample(now=31)
        self.assertIsNone(hotplug['host']['cpuPercent'])
        self.assertIsNone(hotplug['resources'][0]['cpuPercent'])

    def test_cpu_uncertainty_is_bounded_and_shrinks_over_longer_windows(self):
        self.assertEqual(m.cpu_attribution_uncertainty(None, 12, 26), 0)
        self.assertEqual(m.cpu_attribution_uncertainty(1, 12, 26), .5)
        self.assertAlmostEqual(m.cpu_attribution_uncertainty(1000000, 12, 26), .0231)
        self.assertLess(m.cpu_attribution_uncertainty(1000000, 12, 26), m.cpu_attribution_uncertainty(18000, 12, 26))

    def cpu_overshoot_sample(self, percentage):
        scope = self.scope(usage=100)
        collector = self.collector([self.row()])
        collector.sample(now=1)
        self.write_host(user=250, idle=1350)  # 150 busy / 600 total ticks = 25%
        self.counters(scope, usage=100 + round(percentage / 100 * 15 * 1000000 * 4))
        return collector.sample(now=16)

    def test_small_cpu_discrepancy_keeps_all_values_and_unknown_system(self):
        sample = self.cpu_overshoot_sample(25.2)
        self.assertEqual(sample['host']['cpuPercent'], 25)
        self.assertEqual(self.category(sample, 'hosting')['cpuPercent'], 25.2)
        self.assertEqual(sample['resources'][0]['cpuPercent'], 25.2)
        self.assertIsNone(self.category(sample, 'system')['cpuPercent'])
        self.assertNotIn('cpu_attribution_exceeds_host', sample['errors'])

    def test_cpu_discrepancy_at_budget_boundary_does_not_invent_zero(self):
        sample = self.cpu_overshoot_sample(25.5)
        self.assertIsNone(self.category(sample, 'system')['cpuPercent'])
        self.assertEqual(self.category(sample, 'hosting')['cpuPercent'], 25.5)
        self.assertNotIn('cpu_attribution_exceeds_host', sample['errors'])

    def test_cpu_discrepancy_above_budget_still_reports_error(self):
        sample = self.cpu_overshoot_sample(25.501)
        self.assertEqual(sample['host']['cpuPercent'], 25)
        self.assertEqual(self.category(sample, 'hosting')['cpuPercent'], 25.501)
        self.assertIsNone(self.category(sample, 'system')['cpuPercent'])
        self.assertIn('cpu_attribution_exceeds_host', sample['errors'])

    def test_inode_replacement_does_not_reuse_counter_baseline(self):
        scope = self.scope(usage=100)
        collector = self.collector([self.row()])
        collector.sample(now=1)
        scope.rename(scope.with_name('old.scope'))
        self.scope(usage=10000000)
        self.write_host(user=500, idle=1100)
        sample = collector.sample(now=16)
        self.assertIsNone(sample['resources'][0]['cpuPercent'])

    def test_scope_parent_includes_init_and_build_descendants(self):
        scope = self.scope(BUILD_CID, usage=100, memory=400000, inactive=100000)
        self.counters(scope / 'init', usage=1, memory=1, inactive=0)
        self.counters(scope / 'buildkit-run', usage=1, memory=1, inactive=0)
        collector = self.collector([self.row(BUILD_CID, 'builder', 'builds', 'builder')])
        sample = collector.sample(now=1)
        self.assertEqual(sample['resources'][0]['memoryBytes'], 300000)
        self.assertEqual(m.discover_scopes(self.cgroup, {BUILD_CID})[BUILD_CID], scope)

    def test_parent_service_rejected_without_double_counting(self):
        parent = self.cgroup / 'user.slice/user-1001.slice/user@1001.service/app.slice/docker.service'
        self.counters(parent, memory=400000, inactive=0)
        self.scope(parent=parent, memory=100000, inactive=0)
        service = {'id': 'platform:daemon', 'category': 'platform', 'cgroup': str(parent.relative_to(self.cgroup))}
        sample = self.collector([self.row()], [service]).sample(now=1)
        self.assertEqual(self.category(sample, 'hosting')['memoryBytes'], 100000)
        self.assertIsNone(self.category(sample, 'platform')['memoryBytes'])
        self.assertIn('overlapping_cgroup_rejected', sample['errors'])

    def test_missing_service_and_container_scopes_never_fake_zero(self):
        service = {'id': 'platform:missing', 'category': 'platform', 'cgroup': 'system.slice/missing.service'}
        sample = self.collector([self.row()], [service]).sample(now=1)
        self.assertIsNone(self.category(sample, 'hosting')['memoryBytes'])
        self.assertIsNone(self.category(sample, 'platform')['memoryBytes'])
        self.assertEqual(next(row for row in sample['resources'] if row['kind'] == 'service')['state'], 'unknown')
        self.assertIn('service_cgroup_unavailable', sample['errors'])
        self.assertIn('container_cgroup_unavailable', sample['errors'])

    def test_duplicate_cgroup_different_service_ids_is_accounted_once(self):
        path = self.cgroup / 'system.slice/shared.service'
        self.counters(path, memory=100000, inactive=0)
        services = [{'id': 'platform:first', 'category': 'platform', 'cgroup': 'system.slice/shared.service'},
                    {'id': 'platform:second', 'category': 'platform', 'cgroup': 'system.slice/shared.service'}]
        selected, rejected = m.nonoverlapping([{'id': row['id'], 'path': path} for row in services])
        self.assertEqual(len(selected), 1)
        self.assertEqual(len(rejected), 1)
        sample = self.collector(services=services).sample(now=1)
        measured = [row['memoryBytes'] for row in sample['resources'] if row['memoryBytes'] is not None]
        self.assertEqual(measured, [100000])
        self.assertIn('overlapping_cgroup_rejected', sample['errors'])

    def test_stopped_container_has_observed_zero_without_cgroup(self):
        sample = self.collector([self.row(state='stopped')]).sample(now=1)
        self.assertEqual(sample['resources'][0]['state'], 'stopped')
        self.assertEqual(sample['resources'][0]['memoryBytes'], 0)
        self.assertEqual(sample['resources'][0]['cpuPercent'], 0)

    def test_app_multiple_services_aggregate_and_memory_workingset_clamps(self):
        self.scope(WEB_CID, memory=100000, inactive=200000)
        self.scope(DB_CID, memory=100000, inactive=0)
        sample = self.collector([self.row(), self.row(DB_CID)]).sample(now=1)
        self.assertEqual(len(sample['resources']), 1)
        self.assertEqual(sample['resources'][0]['memoryBytes'], 100000)
        self.assertEqual(sample['resources'][0]['memoryLimitBytes'], 400000)

    def test_memory_exceeding_host_estimate_keeps_real_group_and_null_residual(self):
        self.scope(memory=600000, inactive=0)
        sample = self.collector([self.row()]).sample(now=1)
        self.assertEqual(self.category(sample, 'hosting')['memoryBytes'], 600000)
        self.assertIsNone(self.category(sample, 'system')['memoryBytes'])
        self.assertIn('memory_attribution_exceeds_host', sample['errors'])

    def test_docker_failure_sanitized_and_unavailable_not_zero(self):
        collector = self.collector([])
        def failed(_endpoint):
            raise RuntimeError('TOP_SECRET_ENV_AND_PRIVATE_PATH')
        collector.docker_reader = failed
        sample = collector.sample(now=1)
        self.assertIn('docker_rootless_unavailable', sample['errors'])
        self.assertIsNone(self.category(sample, 'hosting')['memoryBytes'])
        self.assertNotIn('TOP_SECRET', json.dumps(sample))
        self.assertNotIn(str(self.root), json.dumps(sample))

    def test_missing_memavailable_is_unavailable(self):
        self.proc.joinpath('meminfo').write_text('MemTotal: 1024 kB\nMemFree: 100 kB\n')
        with self.assertRaises(KeyError):
            self.collector().sample(now=1)


class DockerHTTPTests(Fixture):
    def serve(self, body, status=200, framing='length'):
        path = str(self.root / 'docker.sock')
        requests = []
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                requests.append(self.rfile.readline().decode().strip())
                while self.rfile.readline().strip():
                    pass
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                if framing == 'chunked':
                    self.wfile.write(('HTTP/1.1 %d OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n' % status).encode() +
                                     ('%x\r\n' % len(data)).encode() + data + b'\r\n0\r\n\r\n')
                else:
                    length = len(data) + (10 if framing == 'truncated' else 0)
                    self.wfile.write(('HTTP/1.1 %d OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n' % (status, length)).encode() + data)
        server = socketserver.UnixStreamServer(path, Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return path, requests

    def test_only_fixed_get_and_whitelisted_output(self):
        secret = 'SYNTHETIC_SECRET_NEVER_EXPORT'
        path, requests = self.serve([{'Id': WEB_CID, 'Names': ['/arbitrary-private-name'], 'State': 'running',
                                     'Labels': {'pockethost.web': 'true', 'pockethost.web.app': APP, 'untrusted': secret},
                                     'Env': [secret], 'Command': secret}])
        rows, unknown = m.docker_resources({'kind': 'rootless', 'socket': path})
        self.assertEqual(requests, ['GET /containers/json?all=1&limit=1001 HTTP/1.1'])
        self.assertEqual(rows, [self.row()])
        self.assertEqual(unknown, 0)
        self.assertNotIn(secret, json.dumps(rows))
        self.assertNotIn('private-name', json.dumps(rows))

    def test_native_and_target_aggregate_same_native_id(self):
        path, _ = self.serve([{'Id': DB_CID, 'Names': ['/' + DB], 'State': 'running', 'Labels': {}},
                              {'Id': WEB_CID, 'Names': ['/target'], 'State': 'running',
                               'Labels': {'kolab.db-target.role': 'target', 'kolab.db-target.instance': DB}}])
        rows, _ = m.docker_resources({'kind': 'rootful', 'socket': path})
        self.assertEqual([row['id'] for row in rows], [DB, DB])
        self.assertTrue(all(row['category'] == 'databases' for row in rows))

    def test_oversized_or_invalid_docker_data_fails(self):
        path, _ = self.serve(b'not-json')
        with self.assertRaises(ValueError):
            m.docker_resources({'kind': 'rootless', 'socket': path})

    def test_fixed_builder_and_unknown_container(self):
        path, _ = self.serve([{'Id': BUILD_CID, 'Names': ['/buildx_buildkit_ph-web-builder0'], 'State': 'running'},
                              {'Id': WEB_CID, 'Names': ['/unknown-private-tenant'], 'State': 'running'}])
        rows, unknown = m.docker_resources({'kind': 'rootless', 'socket': path})
        self.assertEqual(rows, [self.row(BUILD_CID, 'builder', 'builds', 'builder')])
        self.assertEqual(unknown, 1)

    def test_http_chunked_response_supported(self):
        path, _ = self.serve([], framing='chunked')
        self.assertEqual(m.docker_resources({'kind': 'rootless', 'socket': path}), ([], 0))

    def test_http_truncated_response_rejected(self):
        path, _ = self.serve([], framing='truncated')
        with self.assertRaises(ValueError):
            m.docker_resources({'kind': 'rootless', 'socket': path})


class DiskTests(Fixture):
    def test_nested_roots_hardlinks_and_symlinks_count_once(self):
        data = self.root / 'data'
        cache = data / 'cache'
        cache.mkdir(parents=True)
        hosting = data / 'content'
        hosting.write_bytes(b'x' * 8192)
        os.link(hosting, data / 'same-content')
        build = cache / 'state'
        build.write_bytes(b'y' * 16384)
        outside = self.root / 'outside'
        outside.write_bytes(b'z' * 32768)
        (cache / 'escape').symlink_to(outside)
        result = m.scan_disk([{'path': str(data), 'category': 'hosting', 'resourceId': APP},
                              {'path': str(cache), 'category': 'builds', 'resourceId': 'builder'}])
        self.assertEqual(result['categories']['hosting'], (data.stat().st_blocks + hosting.stat().st_blocks) * 512)
        self.assertEqual(result['categories']['builds'], (cache.stat().st_blocks + build.stat().st_blocks) * 512)
        self.assertEqual(sum(result['categories'].values()),
                         sum(path.stat().st_blocks for path in (data, hosting, cache, build)) * 512)
        self.assertEqual(result['errors'], [])

    def test_missing_disk_root_invalidates_category_and_partial_resource(self):
        data = self.root / 'valid'
        data.mkdir()
        result = m.scan_disk([{'path': str(data), 'category': 'hosting', 'resourceId': APP},
                              {'path': str(self.root / 'missing'), 'category': 'hosting', 'resourceId': APP}])
        self.assertIsNone(result['categories']['hosting'])
        self.assertNotIn(APP, result['resources'])
        self.assertEqual(result['errors'], ['disk_scan_incomplete'])

    def test_unconfigured_scan_and_budget_are_explicit(self):
        result = m.scan_disk([])
        self.assertIsNone(result['categories']['hosting'])
        self.assertIn('disk_attribution_unconfigured', result['errors'])
        with self.assertRaises(TimeoutError):
            m.scan_disk([{'path': str(self.root), 'category': 'hosting'}], max_entries=0)

    def test_same_resource_id_across_categories_does_not_mix_disk(self):
        first, second = self.root / 'one', self.root / 'two'
        first.mkdir()
        second.mkdir()
        (first / 'data').write_bytes(b'x' * 8192)
        (second / 'data').write_bytes(b'y' * 32768)
        result = m.scan_disk([{'path': str(first), 'category': 'hosting', 'resourceId': APP},
                              {'path': str(second), 'category': 'databases', 'resourceId': APP}])
        self.assertEqual(result['resources']['hosting:' + APP], result['categories']['hosting'])
        self.assertEqual(result['resources']['databases:' + APP], result['categories']['databases'])
        self.assertNotEqual(result['resources']['hosting:' + APP], result['resources']['databases:' + APP])

    def test_statvfs_distinct_device_not_duplicate_mountpaths(self):
        rows, errors = m.filesystem_usage([{'path': str(self.root), 'mount': '/'},
                                           {'path': str(self.proc), 'mount': '/boot'},
                                           {'path': str(self.root / 'missing'), 'mount': '/boot/efi'}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(errors, ['filesystem_unavailable'])
        self.assertGreater(rows[0]['totalBytes'], 0)

    def test_disk_cache_async_retains_error_and_staleness(self):
        disk = m.DiskCache([])
        try:
            result, errors = disk.poll(time.monotonic())
            self.assertIsNone(result)
            self.assertIn('disk_attribution_pending', errors)
            until = time.monotonic() + 5
            while disk.result is None and time.monotonic() < until:
                time.sleep(.01)
                disk.poll(time.monotonic())
            self.assertIsNotNone(disk.result)
            now = time.monotonic()
            disk.last_success = now - 700
            disk.last_attempt = now
            result, errors = disk.poll(now)
            self.assertIsNotNone(result)
            self.assertIn('disk_attribution_stale', errors)
        finally:
            disk.close()

    def test_disk_no_paths_exported_and_overestimate_not_fake_residual(self):
        disk = {'categories': {category: 10 ** 20 for category in m.CATEGORIES[:-1]},
                'resources': {}, 'collectedAt': m.iso(), 'errors': []}
        sample = self.collector().sample(disk, now=1)
        self.assertIsNone(self.category(sample, 'system')['diskBytes'])
        self.assertIn('disk_attribution_exceeds_host', sample['errors'])
        self.assertNotIn(str(self.root), json.dumps(sample))


class HistoryTests(Fixture):
    def test_retention_and_four_views_are_bounded_and_sorted(self):
        history = m.History(self.root / 'history.db')
        self.addCleanup(history.close)
        count = m.MAX_ROWS + 15
        width = 4 + len(m.CATEGORIES) * 3
        history.db.executemany('INSERT INTO samples VALUES (' + ','.join('?' * width) + ')',
                               ((index * 15000, *([float(index % 100)] * (width - 1))) for index in range(count)))
        history.db.commit()
        now_ms = count * 15000
        sample = self.collector().sample(now=1)
        history.append(sample, now_ms)
        kept, oldest = history.db.execute('SELECT COUNT(*),MIN(timestamp) FROM samples').fetchone()
        self.assertLessEqual(kept, m.MAX_ROWS)
        self.assertGreaterEqual(oldest, now_ms - m.RETENTION * 1000)
        for range_id in m.RANGES:
            view = history.view(range_id, now_ms)
            self.assertLessEqual(len(view['points']), 240)
            timestamps = [point['timestamp'] for point in view['points']]
            self.assertEqual(timestamps, sorted(set(timestamps)))
            self.assertEqual(view['retentionDays'], 7)
            self.assertEqual(set(view['points'][-1]['categories']), set(m.CATEGORIES))
        self.assertEqual(stat.S_IMODE((self.root / 'history.db').stat().st_mode), 0o600)

    def test_unavailable_samples_stay_null_and_actual_averages(self):
        history = m.History(self.root / 'history.db')
        self.addCleanup(history.close)
        history.append(None, 15000)
        point = history.view('1h', 15000)['points'][0]
        self.assertIsNone(point['cpuPercent'])
        self.assertTrue(all(value is None for category in point['categories'].values() for value in category.values()))
        self.assertNotIn('path', ','.join(row[1] for row in history.db.execute('PRAGMA table_info(samples)')))

    def test_atomic_json_modes_and_dto_all_nullable_keys(self):
        sample = self.collector().sample(now=1)
        history = m.History(self.root / 'history.db')
        self.addCleanup(history.close)
        history.append(sample, 15000)
        for range_id in m.RANGES:
            value = {'available': True, 'stale': False, 'range': range_id, 'sample': sample,
                     'history': history.view(range_id, 15000)}
            path = self.root / ('resources-' + range_id + '.json')
            m.atomic_json(path, value)
            self.assertEqual(json.loads(path.read_text()), value)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            self.assertFalse(path.with_name('.' + path.name + '.tmp').exists())
        self.assertEqual(len(sample['categories']), 6)
        self.assertTrue(all(set(m.METRICS) <= set(row) for row in sample['categories']))


class ConfigTests(Fixture):
    def write_config(self, config=None):
        path = self.root / 'config.json'
        path.write_text(json.dumps(config or self.config))
        return path

    def test_config_accepts_only_fixed_operator_schema(self):
        self.assertEqual(m.load_config(self.write_config(), trusted=False), self.config)
        for extra in ({'command': 'tenant-script'}, {'env': {'SECRET': 'private'}}):
            config = {**self.config, **extra}
            with self.assertRaises(ValueError):
                m.load_config(self.write_config(config), trusted=False)

    def test_broad_user_unit_and_path_traversal_rejected(self):
        for path in ['user.slice/user-1001.slice/user@1001.service', '../outside.service',
                     '/absolute.service', 'system.slice/../../outside.service', 'system.slice/bad\n.service']:
            config = copy.deepcopy(self.config)
            config['services'] = [{'id': 'platform:daemon', 'category': 'platform', 'cgroup': path}]
            with self.assertRaises(ValueError):
                m.load_config(self.write_config(config), trusted=False)

    def test_only_the_separate_recovery_user_slice_can_be_accounted_as_a_whole(self):
        service={'id':'backups:project-sandbox','category':'backups','cgroup':'user.slice/user-1002.slice'}
        config=copy.deepcopy(self.config);config['services']=[service]
        self.assertEqual(m.load_config(self.write_config(config),trusted=False)['services'],[service])
        for change in [{'id':'platform:whole-host'},{'category':'hosting'},{'cgroup':'user.slice/user-1001.slice'},{'cgroup':'user.slice/user-1002.slice/user@1002.service'}]:
            config['services']=[{**service,**change}]
            with self.assertRaises(ValueError):m.load_config(self.write_config(config),trusted=False)
        parent=self.cgroup/'user.slice/user-1002.slice';self.counters(parent,memory=170000,inactive=20000)
        sample=self.collector(services=[service]).sample(now=1)
        row=next(row for row in sample['resources']if row['id']=='backups:project-sandbox')
        self.assertEqual(row['category'],'backups');self.assertEqual(row['memoryBytes'],150000)

    def test_config_symlink_and_non_private_files_fail_before_read(self):
        path = self.write_config()
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            m.load_config(path)
        link = self.root / 'link.json'
        link.symlink_to(path)
        with self.assertRaises(ValueError):
            m.load_config(link)

    def test_two_services_cannot_configure_same_cgroup_even_with_alias_spelling(self):
        for second in ['system.slice/shared.service', 'system.slice//shared.service']:
            config = copy.deepcopy(self.config)
            config['services'] = [{'id': 'platform:first', 'category': 'platform', 'cgroup': 'system.slice/shared.service'},
                                  {'id': 'platform:second', 'category': 'platform', 'cgroup': second}]
            with self.assertRaises(ValueError):
                m.load_config(self.write_config(config), trusted=False)

    def test_config_rejects_arbitrary_docker_scheme_root_disk_and_resource_label(self):
        for change in [
            {'docker': [{'kind': 'rootful', 'socket': 'tcp://public:2375'}]},
            {'diskRoots': [{'path': '/', 'category': 'hosting'}]},
            {'diskRoots': [{'path': '/var/lib/example', 'category': 'hosting', 'resourceId': 'private/path'}]},
        ]:
            with self.assertRaises(ValueError):
                m.load_config(self.write_config({**self.config, **change}), trusted=False)


if __name__ == '__main__':
    unittest.main()
