#!/usr/bin/env python3
"""No Docker dependency: verify namespace identity, confinement and target provenance."""
import copy
import importlib.util
import json
import os
import time
import pathlib
import socket
import stat
import tempfile
import types
import unittest
from unittest import mock

MODULE = pathlib.Path(__file__).with_name('web_private_db_provider.py')
spec = importlib.util.spec_from_file_location('web_private_db_provider', MODULE)
provider_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provider_module)


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        script = self.root / 'target.cjs'
        script.write_text('// trusted static fixture')
        self.ident = 'a' * 15
        self.cid, self.target_cid = 'b' * 64, 'c' * 64
        self.config = {'dataRoot': str(self.root / 'data'), 'targetRoot': str(self.root), 'targetScript': str(script), 'databaseImage': 'trusted/pocketbase',
                       'targetImage': 'trusted/node@sha256:' + 'd' * 64, 'ids': [self.ident]}
        self.provider = provider_module.Provider(self.config)
        self.base = {'Id': self.cid, 'Name': '/' + self.ident, 'Config': {'Image': 'trusted/pocketbase:latest'},
                     'State': {'Running': True, 'StartedAt': '2026-10-31T12:34:56.123456789Z'},
                     'Mounts': [{'Destination': '/home/pockethost', 'Source': str(self.root / 'data/instances' / self.ident)}]}
        self.target = self.provider.database(self.base)
        self.info = {'Id': self.target_cid, 'Name': '/' + self.target['name'],
                     'Config': {'Image': self.config['targetImage'], 'User': '10001:1000',
                                'Entrypoint': ['/usr/local/bin/node'], 'Cmd': ['/run/kolab-target.cjs'],
                                'Healthcheck': {'Test': ['CMD-SHELL', '/usr/local/bin/node /run/kolab-target.cjs --check'],
                                                'Interval': 5000000000, 'Timeout': 5000000000, 'Retries': 1, 'StartPeriod': 1000000000},
                                'Labels': {provider_module.ROLE: 'target', provider_module.INSTANCE: self.ident,
                                           provider_module.PARENT: self.cid, provider_module.EPOCH: self.target['epoch'], provider_module.RECIPE: self.provider.recipe}},
                     'HostConfig': {'NetworkMode': 'container:' + self.cid, 'ReadonlyRootfs': True, 'Privileged': False,
                                    'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges'], 'Memory': 96 * 1024 * 1024,
                                    'NanoCpus': 50000000, 'PidsLimit': 64},
                     'State': {'Running': True, 'Health': {'Status': 'healthy'}},
                     'Mounts': [{'Type': 'bind', 'Source': str(self.target['directory']), 'Destination': '/run/target', 'RW': True},
                                {'Type': 'bind', 'Source': str(self.provider.target_script), 'Destination': '/run/kolab-target.cjs', 'RW': False}]}

    def test_actual_db_identity_and_image_mount_are_required(self):
        self.assertIsNotNone(self.target)
        # Published ports are deliberately irrelevant to the new provider.
        self.base['NetworkSettings'] = {'Ports': {'8090/tcp': [{'HostPort': '8091'}]}}
        self.assertEqual(self.provider.database(self.base)['containerId'], self.cid)
        for change in [{'Name': '/unrelated'}, {'Id': '../socket'}, {'State': {'Running': False}},
                       {'Config': {'Image': 'trusted/pocketbase-evil:latest'}},
                       {'Mounts': [{'Destination': '/home/pockethost', 'Source': str(self.root / 'data/instances' / ('e' * 15))}]}]:
            value = copy.deepcopy(self.base)
            value.update(change)
            self.assertIsNone(self.provider.database(value), change)

    def test_epoch_and_container_identity_pin_distinct_namespaces(self):
        value = copy.deepcopy(self.base)
        value['State']['StartedAt'] += '-restart'
        restarted = self.provider.database(value)
        self.assertNotEqual(restarted['directory'], self.target['directory'])
        value['Id'] = 'e' * 64
        self.assertNotEqual(self.provider.database(value)['directory'], restarted['directory'])
        self.assertFalse(self.provider.trusted(self.info, restarted))
        self.assertLess(len(('/run/kolab-db-targets/' + self.ident + '/' + self.target['directory'].name + '/t.sock').encode()), 108)

    def test_target_confinement_and_all_mounts_verified(self):
        self.assertTrue(self.provider.trusted(self.info, self.target))
        for key, bad in [('NetworkMode', 'host'), ('ReadonlyRootfs', False), ('Privileged', True), ('CapAdd', ['SYS_ADMIN']),
                         ('CapDrop', []), ('SecurityOpt', []), ('Memory', 0), ('NanoCpus', 0), ('PidsLimit', 0)]:
            value = copy.deepcopy(self.info)
            value['HostConfig'][key] = bad
            self.assertFalse(self.provider.trusted(value, self.target), key)
        for key, bad in [('Image', 'trusted/node:latest'), ('User', '0'), ('Cmd', ['/bin/sh']), ('Entrypoint', ['/bin/sh'])]:
            value = copy.deepcopy(self.info)
            value['Config'][key] = bad
            self.assertFalse(self.provider.trusted(value, self.target), key)
        for mounts in [self.info['Mounts'] + [{'Source': '/var/run/docker.sock', 'Destination': '/var/run/docker.sock'}],
                       [self.info['Mounts'][0], self.info['Mounts'][0]],
                       [{**self.info['Mounts'][0], 'Source': '/run/another-db'}, self.info['Mounts'][1]],
                       [self.info['Mounts'][0], {**self.info['Mounts'][1], 'RW': True}]]:
            self.assertFalse(self.provider.trusted({**self.info, 'Mounts': mounts}, self.target))

    def test_socket_owner_group_type_and_mode(self):
        good = {'st_mode': stat.S_IFSOCK | 0o660, 'st_uid': 10001, 'st_gid': 1000}
        with mock.patch.object(pathlib.Path, 'lstat', return_value=types.SimpleNamespace(**good)):
            self.assertTrue(self.provider.available(self.info, self.target))
        for changed in [{'st_uid': 1000}, {'st_gid': 1001}, {'st_mode': stat.S_IFSOCK | 0o666}, {'st_mode': stat.S_IFLNK | 0o660}]:
            with mock.patch.object(pathlib.Path, 'lstat', return_value=types.SimpleNamespace(**{**good, **changed})):
                self.assertFalse(self.provider.available(self.info, self.target), changed)
        info = copy.deepcopy(self.info)
        info['State']['Health']['Status'] = 'starting'
        self.assertFalse(self.provider.available(info, self.target))

    def test_create_has_only_trusted_namespace_and_immutable_image(self):
        self.provider.leaf = mock.Mock()
        self.provider.docker = mock.Mock(side_effect=[(True, self.target_cid), (True, '')])
        self.provider.create(self.target)
        command = self.provider.docker.call_args_list[0].args
        self.assertEqual(command[command.index('--network') + 1], 'container:' + self.cid)
        self.assertIn(self.config['targetImage'], command)
        self.assertNotIn('--privileged', command)
        self.assertNotIn('--publish', command)
        self.assertNotIn('/var/run/docker.sock', ' '.join(command))
        self.assertEqual(self.provider.docker.call_args_list[1].args, ('start', self.target_cid))
        with self.assertRaises(ValueError):
            provider_module.Provider({**self.config, 'targetImage': 'trusted/node:latest'})
        with self.assertRaises(ValueError):
            provider_module.Provider({**self.config, 'ids': ['../other']})

    def test_live_target_returned_without_host_port_and_stale_target_removed(self):
        self.provider.directory = mock.Mock()
        self.provider.leaf = mock.Mock()
        self.provider.inspect = mock.Mock(side_effect=[[self.base], [self.info]])
        self.provider.docker = mock.Mock(return_value=(True, self.target_cid))
        self.provider.available = mock.Mock(return_value=True)
        result = self.provider.reconcile()[self.ident]
        self.assertEqual(result['targetContainerId'], self.cid)
        self.assertNotIn('targetPort', result)
        # A restarted DB cannot reuse the old target even when its own CID is unchanged.
        provider = provider_module.Provider(self.config)
        provider.directory = mock.Mock()
        provider.leaf = mock.Mock()
        provider.create = mock.Mock()
        base = copy.deepcopy(self.base)
        base['State']['StartedAt'] += '-restart'
        provider.inspect = mock.Mock(side_effect=[[base], [self.info]])
        provider.docker = mock.Mock(return_value=(True, self.target_cid))
        self.assertFalse(provider.reconcile())
        self.assertIn(mock.call('rm', '--force', self.target_cid), provider.docker.call_args_list)
        provider.create.assert_called_once()

    def test_no_grants_removes_only_our_managed_targets(self):
        provider = provider_module.Provider({**self.config, 'ids': []})
        provider.directory = mock.Mock()
        outsider = copy.deepcopy(self.info)
        outsider['Config']['Labels'] = {}
        provider.inspect = mock.Mock(side_effect=[[], [self.info, outsider]])
        provider.docker = mock.Mock(return_value=(True, self.target_cid))
        self.assertEqual(provider.reconcile(), {})
        removals = [call for call in provider.docker.call_args_list if call.args[0] == 'rm']
        self.assertEqual(removals, [mock.call('rm', '--force', self.target_cid)])

    def test_target_script_recipe_requires_recreation(self):
        self.assertTrue(self.provider.trusted(self.info, self.target))
        self.provider.target_script.write_text('// upgraded trusted static fixture')
        upgraded = provider_module.Provider(self.config)
        self.assertNotEqual(upgraded.recipe, self.provider.recipe)
        self.assertTrue(upgraded.managed(self.info))
        self.assertFalse(upgraded.trusted(self.info, self.target))

    def test_old_short_healthcheck_is_recreated_without_changing_resource_limits(self):
        info = copy.deepcopy(self.info)
        info['Config']['Healthcheck']['Timeout'] = 2000000000
        self.assertTrue(self.provider.managed(info))
        self.assertFalse(self.provider.trusted(info, self.target))
        self.assertTrue(self.provider.trusted(self.info, self.target))
        self.provider.leaf = mock.Mock()
        self.provider.docker = mock.Mock(side_effect=[(True, self.target_cid), (True, '')])
        self.provider.create(self.target)
        command = self.provider.docker.call_args_list[0].args
        self.assertEqual(command[command.index('--health-timeout') + 1], '5s')
        self.assertEqual(command[command.index('--cpus') + 1], '0.05')
        self.assertEqual(command[command.index('--memory') + 1], '96m')

    def test_pinned_database_b_keeps_private_namespace_available(self):
        image = 'sha256:' + 'e' * 64
        configured = provider_module.Provider({**self.config, 'databaseImages': {self.ident: image}})
        base = copy.deepcopy(self.base)
        base['Config']['Image'] = image
        base['Image'] = image
        self.assertIsNotNone(configured.database(base))
        configured.directory = mock.Mock()
        configured.leaf = mock.Mock()
        configured.inspect = mock.Mock(side_effect=[[base], [self.info]])
        configured.docker = mock.Mock(return_value=(True, self.target_cid))
        configured.available = mock.Mock(return_value=True)
        self.assertTrue(configured.reconcile()[self.ident]['available'])


    def test_pinned_database_requires_both_exact_config_reference_and_immutable_id(self):
        image = 'sha256:' + 'e' * 64
        configured = provider_module.Provider({**self.config, 'databaseImages': {self.ident: image}})
        base = copy.deepcopy(self.base)
        base['Config']['Image'] = image
        base['Image'] = image
        for reference, actual in [('trusted/pocketbase:latest', image), (image, 'sha256:' + 'f' * 64), (image + ':suffix', image), (image + '@x', image)]:
            value = copy.deepcopy(base)
            value['Config']['Image'], value['Image'] = reference, actual
            self.assertIsNone(configured.database(value))
        other = 'f' * 15
        configured = provider_module.Provider({**self.config, 'ids': [self.ident, other], 'databaseImages': {self.ident: image, other: 'sha256:' + 'f' * 64}})
        base['Name'] = '/' + other
        base['Mounts'][0]['Source'] = str(self.root / 'data/instances' / other)
        self.assertIsNone(configured.database(base))


    def test_operator_image_map_is_bounded_strict_and_subset_of_granted_ids(self):
        image = 'sha256:' + 'e' * 64
        for value in [None, [], {self.ident: 'trusted/pocketbase:latest'}, {self.ident: image + ':suffix'}, {self.ident: image.upper()}, {'f' * 15: image}, {'../elsewhere': image}]:
            with self.assertRaises(ValueError):
                provider_module.Provider({**self.config, 'databaseImages': value})
        ids = [str(i).zfill(15) for i in range(257)]
        with self.assertRaises(ValueError):
            provider_module.Provider({**self.config, 'ids': ids, 'databaseImages': {ident: image for ident in ids}})


    def test_contention_cache_is_bound_to_the_same_operator_image_policy(self):
        image = 'sha256:' + 'e' * 64
        configured = provider_module.Provider({**self.config, 'databaseImages': {self.ident: image}})
        target = {'available': True, 'targetSocket': str(self.target['directory'] / 't.sock'), 'targetContainerId': self.cid, 'targetEpoch': self.target['epoch'], '_device': 42, '_inode': 99, '_databaseImage': image}
        path = self.root / '.provider-state.json'
        path.write_text(json.dumps({'checkedAt': time.time(), 'targets': {self.ident: target}}))
        file_info = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=os.geteuid(), st_gid=1000)
        def inode(path):
            if path.name == 't.sock': return types.SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=10001, st_gid=1000, st_dev=42, st_ino=99)
            return types.SimpleNamespace(st_mode=stat.S_IFDIR | (0o770 if path == self.target['directory'] else 0o700), st_uid=os.geteuid(), st_gid=1000)
        with mock.patch.object(os, 'fstat', return_value=file_info), mock.patch.object(pathlib.Path, 'lstat', autospec=True, side_effect=inode):
            self.assertTrue(configured.cache()[self.ident]['available'])
            configured.database_images[self.ident] = 'sha256:' + 'f' * 64
            self.assertEqual(configured.cache(), {})


    def test_runtime_directory_symlink_is_rejected(self):
        link = self.root / 'unsafe'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.provider.directory(link, 0o700)

    def test_contention_cache_has_short_ttl_and_pinned_socket_inode(self):
        target = {'available': True, 'targetSocket': str(self.target['directory'] / 't.sock'),
                  'targetContainerId': self.cid, 'targetEpoch': self.target['epoch'], '_device': 42, '_inode': 51, '_databaseImage': self.provider.database_image}
        file = self.root / '.provider-state.json'
        fd_info = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=os.geteuid(), st_gid=1000)
        def metadata(path):
            if path.name == 't.sock':
                return types.SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=10001, st_gid=1000, st_dev=42, st_ino=51)
            return types.SimpleNamespace(st_mode=stat.S_IFDIR | (0o770 if path == self.target['directory'] else 0o700),
                                         st_uid=os.geteuid(), st_gid=1000)
        file.write_text(json.dumps({'checkedAt': 100, 'targets': {self.ident: target}}))
        with mock.patch.object(provider_module.time, 'time', return_value=103), mock.patch.object(provider_module.os, 'fstat', return_value=fd_info), mock.patch.object(pathlib.Path, 'lstat', metadata):
            self.assertEqual(self.provider.cache()[self.ident]['targetSocket'], target['targetSocket'])
        with mock.patch.object(provider_module.time, 'time', return_value=106), mock.patch.object(provider_module.os, 'fstat', return_value=fd_info), mock.patch.object(pathlib.Path, 'lstat', metadata):
            self.assertEqual(self.provider.cache(), {})
        target['_inode'] = 52
        file.write_text(json.dumps({'checkedAt': 100, 'targets': {self.ident: target}}))
        with mock.patch.object(provider_module.time, 'time', return_value=103), mock.patch.object(provider_module.os, 'fstat', return_value=fd_info), mock.patch.object(pathlib.Path, 'lstat', metadata):
            self.assertEqual(self.provider.cache(), {})

    def test_cache_is_only_used_for_lock_contention(self):
        self.provider.directory = mock.Mock()
        self.provider.cache = mock.Mock(return_value={'cached': True})
        self.provider.docker = mock.Mock()
        with mock.patch.object(provider_module.fcntl, 'flock', side_effect=BlockingIOError):
            self.assertEqual(self.provider.reconcile(), {'cached': True})
        self.provider.docker.assert_not_called()
        self.provider.cache.reset_mock()
        self.provider.inspect = mock.Mock(side_effect=OSError)
        with self.assertRaises(OSError):
            self.provider.reconcile()
        self.provider.cache.assert_not_called()


if __name__ == '__main__':
    unittest.main()
