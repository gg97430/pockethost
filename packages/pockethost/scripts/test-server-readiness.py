#!/usr/bin/env python3
"""Regression coverage for the failure modes seen during fleet provisioning."""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('readiness', SCRIPTS / 'check-server-readiness.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
PROFILE = {**json.loads((SCRIPTS / 'server-profile.example.json').read_text()), 'allocationMode':'fixed', 'siteCpu':4.1, 'siteMemoryGiB':6}


class ReadinessTests(unittest.TestCase):
    def test_admission_includes_builder_sites_and_reserve(self):
        self.assertEqual(r.budget_errors(PROFILE, 12, 31), [])
        # The former app4 budget: 4.1 CPU already reserved + a 4 CPU builder > 8 CPU.
        self.assertTrue(r.budget_errors({**PROFILE, 'webCpu': 8}, 12, 31))
        self.assertTrue(r.budget_errors({**PROFILE, 'siteMemoryGiB': 9}, 12, 31))

    def test_adaptive_profile_does_not_add_cpu_peak_ceilings(self):
        profile = {**PROFILE, 'allocationMode':'adaptive', 'webCpu':8}
        self.assertEqual(r.budget_errors(profile, 12, 31), [])
        self.assertTrue(r.budget_errors({**profile, 'buildMemoryGiB':16},12,31))
        self.assertTrue(r.budget_errors({**profile, 'allocationMode':'unlimited'},12,31))

    def test_host_capacity_and_invalid_budgets_fail_closed(self):
        for key in ('webCpu', 'webMemoryGiB', 'buildCpu', 'buildMemoryGiB',
                    'siteCpu', 'siteMemoryGiB', 'reserveCpu', 'reserveMemoryGiB'):
            for invalid in (None, -1, 0, True, '4', float('inf'), float('nan')):
                self.assertTrue(r.budget_errors({**PROFILE, key: invalid}, 12, 31))
        self.assertTrue(r.budget_errors(PROFILE, 8, 31))
        self.assertTrue(r.budget_errors(PROFILE, 12, 16))

    def test_literal_env_does_not_execute_or_expand_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'env'
            marker = pathlib.Path(directory) / 'executed'
            path.write_text('export WEB_APPS_BUILD_CPUS=4\nWEB_APPS_BUILD_MEMORY="8g"\nEMPTY=\n# note\nVALUE=\'$(touch ' + str(marker) + ')\'\n')
            values = r.env_values(path)
            self.assertEqual(values['WEB_APPS_BUILD_CPUS'], '4')
            self.assertEqual(values['WEB_APPS_BUILD_MEMORY'], '8g')
            self.assertEqual(values['EMPTY'], '')
            self.assertFalse(marker.exists())
            path.write_text('SECRET=first second\n')
            with self.assertRaises(ValueError):
                r.env_values(path)

    def test_secret_permissions_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'env'
            path.write_text('PRIVATE=value')
            path.chmod(0o600)
            self.assertTrue(r.secure_file(path, os.getuid(), private=True))
            path.chmod(0o640)
            self.assertFalse(r.secure_file(path, os.getuid(), private=True))
            path.chmod(0o600)
            link = pathlib.Path(directory) / 'alias'
            link.symlink_to(path)
            self.assertFalse(r.secure_file(link, os.getuid(), private=True))

    def test_interrupted_inspection_is_failure_without_secret_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'profile.json'
            path.write_text(json.dumps(PROFILE))
            out = io.StringIO()
            with patch.object(r.os, 'geteuid', return_value=0), patch.object(r, 'run', side_effect=ValueError('TOP_SECRET')):
                with patch.object(r.sys, 'argv', ['check', '--profile', str(path)]), contextlib.redirect_stdout(out):
                    self.assertEqual(r.main(), 1)
            self.assertNotIn('TOP_SECRET', out.getvalue())
            result = json.loads(out.getvalue())
            self.assertFalse(result['hostChecksPassed'])
            self.assertTrue(result['functionalAcceptanceStillRequired'])

    def test_false_result_never_marks_host_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'profile.json'
            path.write_text('{}')
            def inspect(profile, check):
                check('restore-tools', False, 'sqlite3 missing')
                check('docker', True, 'ok')
            with patch.object(r.os, 'geteuid', return_value=0), patch.object(r, 'run', side_effect=inspect):
                with patch.object(r.sys, 'argv', ['check', '--profile', str(path)]), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(r.main(), 1)

    def test_prerequisites_include_restore_and_cloudflare_tools(self):
        result = subprocess.run(['bash', str(SCRIPTS / 'install-server-prerequisites.sh'), '--list'], capture_output=True, text=True, check=True)
        packages = result.stdout.splitlines()
        for package in ('cpulimit', 'sqlite3', 'unzip', 'python3-boto3', 'python3-cryptography', 'python3-certbot-dns-cloudflare', 'dbus-user-session'):
            self.assertIn(package, packages)
        self.assertEqual(len(packages), len(set(packages)))

    def test_retired_installer_refuses_instead_of_touching_host(self):
        result = subprocess.run(['bash', str(SCRIPTS.parents[2] / 'setup.sh')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Aucune modification', result.stderr)

    def test_runtime_inventory_and_installed_drift(self):
        self.assertTrue(all(path.is_file() for path in r.runtime_artifacts()))
        self.assertIn('web_app_runtime.py', {p.name for p in r.runtime_artifacts()})
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory) / 'release.py'
            target = pathlib.Path(directory) / 'installed.py'
            source.write_text('fixed build context')
            target.write_text('fixed build context')
            with patch.object(r, 'secure_file', return_value=True):
                self.assertTrue(r.artifact_matches(source, target))
                target.write_text('old broken build context')
                self.assertFalse(r.artifact_matches(source, target))
            with patch.object(r, 'secure_file', return_value=False):
                self.assertFalse(r.artifact_matches(source, target))

    def test_railpack_refuses_wrong_binary_symlink_or_fifo(self):
        import hashlib
        spec = importlib.util.spec_from_file_location('railpack_installer', SCRIPTS / 'install-railpack.py')
        installer = importlib.util.module_from_spec(spec)
        import sys
        with patch.object(sys, 'path', [str(SCRIPTS), *sys.path]):
            spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as directory:
            binary = pathlib.Path(directory) / 'binary'
            data = b'verified binary fixture'
            binary.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            self.assertEqual(installer.verified_bytes(binary, digest), data)
            with self.assertRaises(ValueError):
                installer.verified_bytes(binary, '0' * 64)
            link = pathlib.Path(directory) / 'link'
            link.symlink_to(binary)
            with self.assertRaises(OSError):
                installer.verified_bytes(link, digest)
            fifo = pathlib.Path(directory) / 'fifo'
            os.mkfifo(fifo)
            with self.assertRaises(ValueError):
                installer.verified_bytes(fifo, digest)

    def test_internal_ports_require_actual_loopback_listener(self):
        good = 'LISTEN 0 511 127.0.0.1:8091 0.0.0.0:*\nLISTEN 0 511 [::1]:3000 [::]:*'
        self.assertTrue(all(r.loopback_listeners(good).values()))
        self.assertFalse(any(r.loopback_listeners('').values()))
        for public in ('0.0.0.0', '*', '[::]', '203.0.113.1'):
            self.assertFalse(r.loopback_listeners(good.replace('127.0.0.1', public))['8091'])

    def test_ingress_requires_actual_drops_before_docker_accepts(self):
        user = '-N DOCKER-USER\n-A DOCKER-USER -j KOLAB-DOCKER-IN'
        forward = '-P FORWARD DROP\n-A FORWARD -j DOCKER-USER'
        guard = '-A KOLAB-DOCKER-IN -i eth0 -m conntrack --ctdir ORIGINAL -j DROP'
        self.assertTrue(r.ingress_protected(user, forward, guard, {'eth0'}))
        self.assertFalse(r.ingress_protected(user, forward, '', {'eth0'}))
        self.assertFalse(r.ingress_protected(user, forward, guard, {'eth0', 'eth1'}))
        self.assertFalse(r.ingress_protected('-A DOCKER-USER -j ACCEPT\n' + user, forward, guard, {'eth0'}))

    def test_memory_units_match_worker_configuration(self):
        self.assertEqual(r.memory_bytes('8g'), 8 * 1024**3)
        self.assertEqual(r.memory_bytes('512m'), 512 * 1024**2)
        with self.assertRaises(ValueError):
            r.memory_bytes('unlimited')


if __name__ == '__main__':
    unittest.main()
