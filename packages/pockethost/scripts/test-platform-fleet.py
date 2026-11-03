#!/usr/bin/env python3
"""Fleet publication contract: require every active target and roll all activated hosts back on failure."""
import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('platform_fleet', pathlib.Path(__file__).with_name('platform-fleet.py'))
fleet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fleet)


class PairedPublication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.targets = {name: {} for name in fleet.ACTIVE}
        self.data = {'id': 'release-test-20261101', 'sourceHash': 'a' * 64, 'targets': {}}
        for name in ['installer.py', *[host+'.tar.gz' for host in fleet.ACTIVE]]:
            raw = name.encode()
            (self.root / name).write_bytes(raw)
            row = {'file': name, 'sha256': hashlib.sha256(raw).hexdigest()}
            if name == 'installer.py':
                self.data['installer'] = row
            else:
                self.data['targets'][name[:4]] = row
        self.save()

    def save(self):
        (self.root / 'release.json').write_text(json.dumps(self.data))

    def execute(self, fail=None):
        events = []

        def upload(host, *_):
            events.append(('upload', host))

        def phase(host, target, data, name):
            events.append((name, host))
            if (name, host) == fail:
                raise RuntimeError('Simulated host failure')

        try:
            fleet.publish(self.root, self.targets, upload, phase)
        except RuntimeError:
            if fail is None:
                raise
        return events

    def test_all_preparations_precede_activation_and_both_verify_before_commit(self):
        self.assertEqual(self.execute(), [(phase, host) for phase in ['upload', 'prepare', 'activate', 'verify', 'commit'] for host in fleet.ACTIVE])

    def test_missing_target_refused_before_any_upload(self):
        del self.data['targets']['app4']
        self.save()
        with self.assertRaisesRegex(ValueError, 'app3 ET app4 ET app5'):
            self.execute()

    def test_retired_target_cannot_replace_active_target(self):
        self.data['targets']['app1'] = self.data['targets'].pop('app4')
        self.save()
        with self.assertRaises(ValueError):
            self.execute()

    def test_changed_archive_refused(self):
        (self.root / 'app4.tar.gz').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'Archive modifiée'):
            self.execute()

    def test_symlink_artifact_refused(self):
        p = self.root / 'app4.tar.gz'
        p.unlink()
        p.symlink_to(self.root / 'app3.tar.gz')
        with self.assertRaises(ValueError):
            self.execute()

    def test_path_escape_refused(self):
        self.data['installer']['file'] = '../installer.py'
        self.save()
        with self.assertRaisesRegex(ValueError, 'Nom de fichier'):
            self.execute()

    def test_prepare_failure_leaves_both_servers_unactivated(self):
        events = self.execute(('prepare', 'app4'))
        self.assertFalse(any(name in ['activate', 'commit', 'rollback'] for name, _ in events))

    def test_partial_second_activation_rolls_back_both_servers(self):
        events = self.execute(('activate', 'app4'))
        self.assertEqual(events[-2:], [('rollback', 'app4'), ('rollback', 'app3')])
        self.assertNotIn(('commit', 'app3'), events)

    def test_verification_failure_rolls_back_both_servers(self):
        events = self.execute(('verify', 'app3'))
        self.assertEqual(events[-len(fleet.ACTIVE):], [('rollback', host) for host in reversed(fleet.ACTIVE)])

    def test_commit_failure_reverts_the_server_already_committed_too(self):
        events = self.execute(('commit', 'app4'))
        self.assertIn(('commit', 'app3'), events)
        self.assertEqual(events[-len(fleet.ACTIVE):], [('rollback', host) for host in reversed(fleet.ACTIVE)])

    def test_missing_app5_refused(self):
        del self.data['targets']['app5']
        self.save()
        with self.assertRaises(ValueError):
            self.execute()

    def test_partial_third_activation_rolls_back_all(self):
        events = self.execute(('activate', 'app5'))
        self.assertEqual(events[-3:], [('rollback', host) for host in reversed(fleet.ACTIVE)])
        self.assertFalse(any(name == 'commit' for name, _ in events))

    def test_pilots_both_verify_before_app3_is_uploaded(self):
        self.data['waves'] = [['app4','app5'],['app3']]; self.save()
        events=self.execute()
        for pilot in ['app4','app5']:
            self.assertLess(events.index(('verify',pilot)),events.index(('upload','app3')))
        self.assertEqual([host for name,host in events if name=='activate'],['app4','app5','app3'])

    def test_failed_pilot_never_touches_app3(self):
        self.data['waves'] = [['app4','app5'],['app3']]; self.save()
        events=self.execute(('verify','app5'))
        self.assertFalse(any(host=='app3'for _,host in events))
        self.assertEqual(events[-2:],[('rollback','app5'),('rollback','app4')])

    def test_app3_preflight_failure_rolls_back_validated_pilots(self):
        self.data['waves'] = [['app4','app5'],['app3']]; self.save()
        events=self.execute(('prepare','app3'))
        self.assertNotIn(('activate','app3'),events)
        self.assertEqual(events[-2:],[('rollback','app5'),('rollback','app4')])

    def test_invalid_waves_refused(self):
        for waves in [[],[[]],[['app4','app5']],[['app4','app5'],['app3','app3']],[['app4','app5'],['app2']],['app4']]:
            self.data['waves']=waves;self.save()
            with self.assertRaises(ValueError):self.execute()


if __name__ == '__main__':
    unittest.main()
