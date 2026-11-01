#!/usr/bin/env python3
import json, pathlib, tempfile, types, unittest
from unittest.mock import Mock
from web_app_project_limits import admit, build_seconds, policy, sync

class ProjectLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root = pathlib.Path(self.tmp.name)
        self.app = 'a' * 15; self.other = 'b' * 15; self.project = 'c' * 15
        self.limits = {'traffic': {'enabled': False}, 'resources': {'enabled': True, 'cpuMillicores': 1000, 'memoryMiB': 1024, 'buildMinutes': 2, 'queuedBuilds': 1}}
        self.job = types.SimpleNamespace(app=self.app, snapshot={'projectPolicy': {'projectId': self.project, 'limits': self.limits, 'appIds': [self.app, self.other]}})
    def docker(self, rows):
        def call(args):
            if args[0] == 'ps': return ' '.join(rows)
            return json.dumps([rows[args[-1]]])
        return call
    def row(self, app, memory=768, cpu=500, running=True):
        return {'Config': {'Labels': {'pockethost.web.app': app}}, 'State': {'Running': running}, 'HostConfig': {'Memory': memory*1024**2, 'NanoCpus': cpu*1_000_000}}
    def test_project_aggregate_includes_sibling_sites_and_previous_version(self):
        docker = self.docker({'old': self.row(self.app), 'sibling': self.row(self.other, 128, 100)})
        with self.assertRaisesRegex(RuntimeError, 'Quota CPU/RAM'): admit(self.root, self.job, docker, 256, 100)
        admit(self.root, self.job, docker, 128, 100)
    def test_unrelated_projects_and_stopped_services_do_not_consume_quota(self):
        docker = self.docker({'foreign': self.row('d'*15, 10000, 10000), 'stopped': self.row(self.other, 10000, 10000, False)})
        admit(self.root, self.job, docker, 1024, 1000)
    def test_disabled_quotas_never_inspect_or_stop_any_container(self):
        self.limits['resources']['enabled'] = False; docker = Mock()
        admit(self.root, self.job, docker, 100000, 100000); docker.assert_not_called()
        self.assertEqual(build_seconds(self.root, self.job), 1800)
    def test_live_registry_overrides_claim_and_can_disable_limits(self):
        current = json.loads(json.dumps(self.limits)); current['resources']['enabled'] = False
        (self.root/'project-limits.json').write_text(json.dumps({'version':1, 'projects':{self.project:current}, 'apps':{self.app:self.project}}))
        self.assertFalse(policy(self.root, self.job)['limits']['resources']['enabled'])
        self.assertEqual(build_seconds(self.root, self.job), 1800)
    def test_initial_claim_policy_is_enforced_without_registry_and_bad_file_falls_back(self):
        self.assertEqual(build_seconds(self.root, self.job), 120)
        (self.root/'project-limits.json').write_text('broken')
        self.assertEqual(build_seconds(self.root, self.job), 120)
    def test_unassigned_site_has_no_project_quota(self):
        self.job.snapshot = {}; docker = Mock(); admit(self.root, self.job, docker, 9999, 9999); docker.assert_not_called()
    def test_registry_fetch_uses_private_worker_endpoint_and_atomic_write(self):
        value = {'version':1, 'projects':{self.project:self.limits}, 'apps':{self.app:self.project}}
        api = Mock(return_value=value); atomic = Mock(); sync(self.root, api, atomic)
        api.assert_called_once_with('project-limits', {}); atomic.assert_called_once_with(self.root/'project-limits.json', value)

if __name__ == '__main__': unittest.main()
