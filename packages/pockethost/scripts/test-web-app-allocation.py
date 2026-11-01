#!/usr/bin/env python3
"""Adaptive admission, bounded peaks, pressure and cancellation; no Docker required."""
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import Mock, patch

import web_app_runtime as r

spec = importlib.util.spec_from_file_location('allocation_worker', pathlib.Path(__file__).with_name('web-app-worker.py'))
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
MIB = 1024**2


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'WEB_APPS_ALLOCATION_MODE':'adaptive', 'WEB_APPS_BUILD_CPUS':'4', 'WEB_APPS_BUILD_MEMORY':'8g'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.host_reader=r.host_memory
        host=patch.object(r,'host_memory',return_value={'totalBytes':32768*MIB,'availableBytes':24000*MIB})
        host.start();self.addCleanup(host.stop)
        self.row = {'scope':str(self.root), 'limitBytes':16384*MIB, 'currentBytes':2048*MIB, 'workingSetBytes':2048*MIB}
        memory = patch.object(r, 'effective_memory', side_effect=lambda:[self.row.copy()])
        memory.start(); self.addCleanup(memory.stop)
        cpu = patch.object(r, 'effective_cpu_millicores', return_value=8000)
        cpu.start(); self.addCleanup(cpu.stop)
        self.containers = [{'HostConfig':{'Memory':4096*MIB, 'MemoryReservation':256*MIB, 'NanoCpus':4000000000}} for _ in range(4)]
        self.docker = Mock(side_effect=lambda args:'a b c d' if args[0]=='ps' else json.dumps(self.containers))

    def test_idle_sites_do_not_reserve_their_full_cpu_and_ram_peaks(self):
        value = r.admit(self.docker, build=True)
        self.assertEqual(value['reservedBytes'], 16384*MIB)
        self.assertEqual(value['requestedBytes'], 1024*MIB)
        self.assertEqual(value['candidateBytes'], 8192*MIB)
        with self.assertRaises(RuntimeError): r.admit(self.docker, build=True, mode='fixed')

    def test_startup_request_is_distinct_from_the_memory_peak(self):
        profile = r.validate_profile({'resources':{'memoryMiB':8192}, 'memoryRequestMiB':512})
        self.assertEqual(r.admission_memory(profile), 512)
        host = r.resource_config(profile, profile['resources'])
        self.assertEqual(host['MemoryReservation'], 512*MIB)
        self.assertEqual(host['Memory'], 8192*MIB)
        self.assertEqual(host['MemorySwap'], host['Memory'])
        r.admit(self.docker, r.admission_memory(profile), profile['resources']['cpuMillicores'])

    def test_requests_cannot_overcommit_the_pool_even_if_containers_are_idle(self):
        for row in self.containers: row['HostConfig']['MemoryReservation'] = 4096*MIB
        with self.assertRaises(r.ResourcePressure): r.admit(self.docker, 256, 500)

    def test_live_usage_and_every_ancestor_remain_binding(self):
        self.row['workingSetBytes'] = 15500*MIB
        with self.assertRaises(r.ResourcePressure): r.admit(self.docker, 256, 500)
        self.row['workingSetBytes'] = 2048*MIB
        parent = {**self.row, 'limitBytes':4096*MIB}
        with patch.object(r, 'effective_memory', return_value=[self.row, parent]):
            with self.assertRaises(r.ResourcePressure): r.admit(self.docker, build=True)

    def test_memory_pressure_defers_new_work_but_does_not_abort_on_pressure_alone(self):
        (self.root/'memory.pressure').write_text('some avg10=15.10 avg60=0.00 avg300=0.00 total=1\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n')
        with self.assertRaises(r.ResourcePressure): r.admit(self.docker, build=True)
        r.admit(self.docker)  # Existing command polling isn't killed just for a past PSI spike.
        (self.root/'memory.pressure').write_text('some avg10=0.00 avg60=0.00 avg300=0.00 total=1\n')
        r.admit(self.docker, build=True)

    def test_host_memory_reserve_protects_databases_outside_the_web_cgroup(self):
        with patch.object(r,'host_memory',return_value={'totalBytes':32768*MIB,'availableBytes':1024*MIB}):
            with self.assertRaises(r.ResourcePressure): r.admit(self.docker,build=True)
        with patch.object(r,'host_memory',side_effect=RuntimeError('unverifiable')):
            with self.assertRaises(RuntimeError): r.admit(self.docker,256,500)

    def test_missing_or_corrupt_host_counters_are_not_treated_as_free_memory(self):
        path=self.root/'meminfo'
        with patch.object(r,'PROC_MEMINFO',path):
            # Read the real implementation while the admission fixtures mock its caller.
            original = self.host_reader
            for content in ('MemTotal: 1000 kB\n', 'MemTotal: 1000 kB\nMemAvailable: 2000 kB\n', 'MemTotal: invalid kB\nMemAvailable: 1 kB\n'):
                path.write_text(content)
                with self.assertRaises(RuntimeError): original()
            path.write_text('MemTotal: 1000 kB\nMemAvailable: 500 kB\n')
            self.assertEqual(original()['availableBytes'],500*1024)

    def test_corrupt_pressure_or_missing_hard_limit_fails_closed(self):
        (self.root/'memory.pressure').write_text('some avg10=nan\n')
        with self.assertRaises(RuntimeError): r.admit(self.docker, build=True)
        (self.root/'memory.pressure').unlink()
        self.containers[0]['HostConfig']['Memory'] = 0
        with self.assertRaises(RuntimeError): r.admit(self.docker, 256, 500)

    def test_legacy_fixed_commitments_and_operator_rollback_are_preserved(self):
        for row in self.containers: row['HostConfig'].pop('MemoryReservation')
        with self.assertRaises(r.ResourcePressure): r.admit(self.docker, 256, 500)
        profile = r.validate_profile({})
        with patch.dict(os.environ, {'WEB_APPS_ALLOCATION_MODE':'fixed'}):
            self.assertEqual(r.admission_memory(profile), profile['resources']['memoryMiB'])
            self.assertNotIn('MemoryReservation', r.resource_config(profile, profile['resources']))
            with self.assertRaises(RuntimeError): r.admit(self.docker, build=True)

    def test_priority_changes_relative_cpu_weight_without_removing_limits(self):
        weights = []
        for priority in ('low','normal','high'):
            profile = r.validate_profile({'priority':priority, 'resources':{'cpuMillicores':4000,'memoryMiB':4096}})
            host = r.resource_config(profile, profile['resources'])
            weights.append(host['CpuShares'])
            self.assertEqual(host['NanoCpus'],4000000000)
            self.assertEqual(host['Memory'],4096*MIB)
        self.assertEqual(weights, [256,1024,2048])

    def test_grouped_services_share_the_app_priority_and_memory_request(self):
        image = 'example.org/site@sha256:'+'a'*64
        profile = r.validate_profile({'kind':'compose', 'memoryRequestMiB':384, 'compose':{'front':'web','services':{
            'web':{'image':image,'resources':{'memoryMiB':512,'cpuMillicores':500}},
            'cache':{'image':image,'resources':{'memoryMiB':1024,'cpuMillicores':500}}
        }}})
        hosts = [r.resource_config(profile, value['resources']) for value in profile['compose']['services'].values()]
        self.assertEqual(sum(host['MemoryReservation'] for host in hosts),384*MIB)
        self.assertEqual(sum(host['CpuShares'] for host in hosts),1024)

    def test_invalid_policy_or_baseline_cannot_disable_limits(self):
        for invalid in [{'allocation':'unlimited'}, {'priority':'root'}, {'allocation':None}, {'memoryRequestMiB':None}, {'memoryRequestMiB':0},
                        {'memoryRequestMiB':769}, {'allocation':'fixed','memoryRequestMiB':64}, {'resources':{'memoryMiB':8193}}, {'resources':{'cpuMillicores':4001}}]:
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError): r.validate_profile(invalid)
        self.assertEqual(r.validate_profile({'allocation':'adaptive','resources':{'memoryMiB':8192}})['resources']['memoryMiB'],8192)
        with self.assertRaises(RuntimeError): r.validate_profile({'allocation':'fixed','resources':{'memoryMiB':1537}})

    def test_wait_renews_lease_and_resumes_without_stopping_sites(self):
        job = Mock()
        with patch.object(w,'admit_resources',side_effect=[r.ResourcePressure('pressure'),{'ok':True}]), patch.object(w.time,'sleep') as sleep:
            self.assertEqual(w.Job.wait_resources(job,build=True),{'ok':True})
        job.heartbeat.assert_called_once_with(True)
        job.log.assert_called_once()
        sleep.assert_called_once_with(5)
        job.stop_old.assert_not_called()

    def test_wait_cancellation_and_timeout_propagate_without_retrying_other_failures(self):
        job = Mock()
        with patch.object(w,'admit_resources',side_effect=r.ResourcePressure('pressure')), patch.object(w.time,'monotonic',side_effect=[0,121]), patch.object(w.time,'sleep') as sleep:
            with self.assertRaises(r.ResourcePressure): w.Job.wait_resources(job,build=True)
        sleep.assert_not_called()
        job.heartbeat.side_effect = RuntimeError('cancelled')
        with patch.object(w,'admit_resources',side_effect=r.ResourcePressure('pressure')):
            with self.assertRaisesRegex(RuntimeError,'cancelled'): w.Job.wait_resources(job,build=True)
        job.heartbeat.side_effect = None
        with patch.object(w,'admit_resources',side_effect=RuntimeError('configuration')), patch.object(w.time,'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError,'configuration'): w.Job.wait_resources(job,build=True)
        sleep.assert_not_called()


if __name__ == '__main__': unittest.main()
