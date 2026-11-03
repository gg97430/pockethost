#!/usr/bin/env python3
import copy
import json
import os
import pathlib
import stat
import tempfile
import types
import unittest
from unittest import mock

import web_private_db_cpu as cpu


class RelayCpuTests(unittest.TestCase):
    def setUp(self):
        self.app = 'a' * 15
        self.parent = 'b' * 64
        self.rules = {'version': 1, 'applications': [self.app], 'instances': ['c' * 15]}
        self.relay = {'Config': {'Labels': {'pockethost.web.app': self.app,
            'pockethost.web.private-db': 'true', 'pockethost.web.private-db.parent': self.parent}},
            'HostConfig': {'NanoCpus': 0, 'Memory': 96 * 1024**2, 'PidsLimit': 64, 'NetworkMode': 'container:' + self.parent}}

    def test_only_authorized_relay_can_burst_without_losing_reserved_cpu(self):
        with mock.patch.object(cpu, 'policy', return_value=self.rules):
            self.assertEqual(cpu.accounted_nano_cpus(self.relay), 50_000_000)
            for label, value in [('pockethost.web.app', 'd' * 15), ('pockethost.web.private-db', 'false'),
                                 ('pockethost.web.private-db.parent', 'e' * 64)]:
                other = copy.deepcopy(self.relay); other['Config']['Labels'][label] = value
                self.assertEqual(cpu.accounted_nano_cpus(other), 0)
            for key, value in [('Memory', 128 * 1024**2), ('PidsLimit', 128), ('NetworkMode', 'host')]:
                other = copy.deepcopy(self.relay); other['HostConfig'][key] = value
                self.assertEqual(cpu.accounted_nano_cpus(other), 0)
            self.assertEqual(cpu.accounted_nano_cpus({'HostConfig': {'NanoCpus': 2_000_000_000}}), 2_000_000_000)

    def test_effective_quota_accounts_for_docker_retaining_legacy_nano_cpus(self):
        self.assertTrue(cpu.cpu_unlimited({'NanoCpus': 50_000_000, 'CpuQuota': -1}))
        self.assertTrue(cpu.cpu_unlimited({'NanoCpus': 0, 'CpuQuota': 0}))
        self.assertFalse(cpu.cpu_unlimited({'NanoCpus': 50_000_000, 'CpuQuota': 0}))
        self.assertFalse(cpu.cpu_unlimited({'NanoCpus': 0, 'CpuQuota': 5000}))
        self.assertFalse(cpu.cpu_unlimited({}))

    def test_operator_policy_requires_root_owned_regular_file_and_explicit_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            file = pathlib.Path(folder) / 'policy.json'
            with mock.patch.object(cpu, 'POLICY_FILE', file):
                self.assertEqual(cpu.policy(), {'applications': [], 'instances': []})
                file.write_text(json.dumps(self.rules)); file.chmod(0o644)
                trusted = types.SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o644, st_size=file.stat().st_size)
                with mock.patch.object(cpu.os, 'fstat', return_value=trusted):
                    self.assertTrue(cpu.unlimited('applications', self.app))
                    self.assertFalse(cpu.unlimited('instances', self.app))
                    file.write_text(json.dumps({**self.rules, 'applications': ['*']}))
                    with self.assertRaises(RuntimeError): cpu.policy()
                for update in [{'st_uid': 1001}, {'st_mode': stat.S_IFREG | 0o666}]:
                    with mock.patch.object(cpu.os, 'fstat', return_value=types.SimpleNamespace(**{**vars(trusted), **update})):
                        with self.assertRaises(RuntimeError): cpu.policy()


if __name__ == '__main__':
    unittest.main()
