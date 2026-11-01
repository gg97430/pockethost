#!/usr/bin/env python3
"""Private relay admission and lifecycle; no Docker daemon or production access."""
import copy
import hashlib
import json
import os
import pathlib
import socket
import shlex
import subprocess
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

import web_app_private_db as private
import web_app_project_limits as project_limits
import web_app_runtime as runtime
import web_app_storage as storage

APP, OTHER, DEP, BINDING, INSTANCE = ['a' * 15, 'b' * 15, 'c' * 15, 'd' * 15, 'e' * 15]
CID, PROXY = '1' * 64, '2' * 64
TOKEN, REVISION = 'Q' * 64, 'R' * 24
IMAGE = 'trusted/helper@sha256:' + '3' * 64
HELPER_IMAGE = 'sha256:' + '3' * 64


def binding(**values):
    return {'id': BINDING, 'appId': APP, 'instanceId': INSTANCE, 'token': TOKEN, 'revision': REVISION,
            'port': 4825, 'available': True, **values}


def policy(rows=None, **values):
    return {'version': 1, 'enabled': True, 'applications': [APP, OTHER],
            'bindings': [binding()] if rows is None else rows, **values}


def application():
    return {'Id': CID, 'Config': {'Labels': {'pockethost.web': 'true', 'pockethost.web.app': APP,
        'pockethost.web.deployment': DEP, 'pockethost.web.service': 'web'}, 'Env': ['KOLAB_DB_4825_URL=http://127.0.0.1:4825']},
        'State': {'Running': True, 'StartedAt': '2026-10-31T14:00:00Z'}}


class HostStatusTests(unittest.TestCase):
    def setUp(self):
        self.hash = 'a' * 64
        self.state = {'version': 1, 'ready': True, 'checkedAt': 100000, 'configHash': self.hash, 'namespace': 'net:[123456]'}

    def test_regular_small_fresh_exact_status_is_accepted(self):
        self.assertEqual(private.proxy_status(json.dumps(self.state), self.hash, now=100001), self.state)

    def test_status_rejects_wrong_hash_stale_future_and_invalid_fields(self):
        for update in [{'configHash': 'b' * 64}, {'checkedAt': 94000}, {'checkedAt': 100001}, {'checkedAt': True},
                       {'checkedAt': float('nan')}, {'checkedAt': 10**310}, {'ready': False}, {'version': True}, {'namespace': 'host'}]:
            with self.subTest(update=update), self.assertRaises(RuntimeError):
                private.proxy_status(json.dumps({**self.state, **update}), self.hash, now=100000)

    def test_raw_status_rejects_oversized_and_invalid_json(self):
        for raw in [b'x' * 1025, b'not json', b'[]', b'']:
            with self.assertRaises(RuntimeError): private.proxy_status(raw, self.hash, now=100000)

    def test_fixed_coreutils_command_rejects_symlink_fifo_and_limits_output(self):
        with tempfile.TemporaryDirectory(prefix='kds-', dir='/tmp') as directory:
            file = pathlib.Path(directory) / 'status.json'
            command = private.PROXY_STATUS_COMMAND.replace('/tmp/kolab-db-proxy-status.json', shlex.quote(str(file)))
            def run(): return subprocess.run(['/bin/sh', '-ec', command], capture_output=True, timeout=1)
            file.write_text(json.dumps(self.state)); result = run()
            self.assertEqual(result.returncode, 0); self.assertEqual(json.loads(result.stdout), self.state)
            target = file.with_name('other.json'); file.rename(target); file.symlink_to(target)
            self.assertNotEqual(run().returncode, 0); file.unlink(); os.mkfifo(file)
            self.assertNotEqual(run().returncode, 0); file.unlink(); file.mkdir()
            self.assertNotEqual(run().returncode, 0); file.rmdir(); file.write_bytes(b'x' * 4096)
            result = run(); self.assertEqual(result.returncode, 0); self.assertEqual(len(result.stdout), 1025)
            with self.assertRaises(RuntimeError): private.proxy_status(result.stdout, self.hash, now=100000)

    def test_only_exact_official_image_and_confinement_allow_lightweight_exec(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {'WEB_APPS_STORAGE_HELPER_IMAGE': HELPER_IMAGE}):
            docker = mock.Mock(return_value=json.dumps(self.state))
            manager = private.PrivateDatabases('http://127.0.0.1:8091', 'secret', 'socket', directory, docker)
            info = {'Id': PROXY, 'Image': HELPER_IMAGE,
                    'Config': {'User': '10001:10001', 'Labels': {'pockethost.web': 'true', 'pockethost.web.private-db': 'true'}},
                    'HostConfig': {'ReadonlyRootfs': True}}
            with mock.patch.object(private.time, 'time', return_value=100.001):
                self.assertEqual(manager.proxy_status(info, self.hash), self.state)
            args = docker.call_args.args[0]
            self.assertEqual(args[-6:], ['/usr/bin/timeout', '--signal=KILL', '2', '/bin/sh', '-ec', private.PROXY_STATUS_COMMAND])
            self.assertEqual(docker.call_args.kwargs['timeout'], 5); self.assertNotIn('/usr/local/bin/node', args)
            for changed in [{'Image': 'sha256:' + '9' * 64}, {'Image': 'untrusted/site:latest'},
                            {'Config': {**info['Config'], 'User': '0'}}, {'HostConfig': {'ReadonlyRootfs': False}},
                            {'Config': {**info['Config'], 'Labels': {'pockethost.web': 'true'}}}]:
                docker.reset_mock()
                with self.subTest(changed=changed), self.assertRaises(RuntimeError): manager.proxy_status({**info, **changed}, self.hash)
                docker.assert_not_called()


class HostProbeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='kdp-', dir='/tmp'); self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name).resolve(); self.path = self.root / (APP + '.sock')
        self.listener = socket.socket(socket.AF_UNIX); self.listener.bind(str(self.path)); self.listener.listen(16); self.listener.settimeout(.05)
        self.stop = threading.Event(); self.requests = []; self.tails = []; self.children = []; self.reply = b'OK\n'; self.delay = 0
        self.thread = threading.Thread(target=self.serve); self.thread.start(); self.addCleanup(self.close)

    def serve(self):
        while not self.stop.is_set():
            try: peer, _ = self.listener.accept()
            except (socket.timeout, OSError): continue
            child = threading.Thread(target=self.answer, args=(peer,)); self.children.append(child); child.start()

    def answer(self, peer):
        with peer:
            peer.settimeout(.5)
            try:
                data = bytearray()
                while b'\n' not in data and len(data) < 1024:
                    chunk = peer.recv(1024 - len(data))
                    if not chunk: return
                    data.extend(chunk)
                value = json.loads(data); self.requests.append(value)
                time.sleep(self.delay)
                answer = self.reply if value.get('token') == TOKEN else b'ERR DENIED\n'
                if answer: peer.sendall(answer)
                self.tails.append(peer.recv(1))
            except (OSError, ValueError): pass

    def close(self):
        self.stop.set(); self.listener.close(); self.thread.join(1)
        for child in self.children: child.join(1)

    def test_host_probe_authenticates_then_closes_without_http_payload(self):
        self.assertTrue(private.probe_bridge(APP, [binding()], self.root, timeout=.3))
        for child in self.children: child.join(1)
        self.assertEqual(self.requests, [{'version': 1, 'bindingId': BINDING, 'token': TOKEN}]); self.assertEqual(self.tails, [b''])
        self.assertFalse(private.probe_bridge(APP, [binding(token='X' * 64)], self.root, timeout=.3))

    def test_unavailable_oversized_and_incomplete_ack_fail_closed(self):
        for reply in [b'ERR UNAVAILABLE\n', b'x' * 1025, b'OK']:
            with self.subTest(reply=reply[:32]):
                self.reply = reply
                self.assertFalse(private.probe_bridge(APP, [binding()], self.root, timeout=.05))

    def test_parallel_timeout_bounds_four_bindings_and_invalid_input_never_connects(self):
        self.reply = b''; self.delay = .15; started = time.monotonic()
        rows = [binding(id=letter * 15) for letter in 'dfgh']
        self.assertFalse(private.probe_bridge(APP, rows, self.root, timeout=.05))
        self.assertLess(time.monotonic() - started, .18)
        before = len(self.requests)
        for rows in [[binding(id=[])], [binding(token='short')], [binding(), binding()], [binding()] * 5]:
            self.assertFalse(private.probe_bridge(APP, rows, self.root, timeout=.05))
        self.assertEqual(len(self.requests), before)


class PolicyTests(unittest.TestCase):
    def test_only_unique_authorized_bindings_and_bounded_ports_are_accepted(self):
        self.assertEqual(private.validate_bindings(policy())[APP], [binding()])
        for update in [{'port': 80}, {'port': 1023}, {'port': 65536}, {'port': '4825'}, {'port': True},
                       {'token': 'short'}, {'token': '!' * 64}, {'revision': 'short'}, {'available': 1}, {'databaseRunning': 'true'},
                       {'appId': 'z' * 15}, {'id': '../unsafe'}, {'instanceId': []}]:
            with self.subTest(update=update), self.assertRaises(ValueError):
                private.validate_bindings(policy([binding(**update)]))
        for rows in [[binding(), binding()], [binding(), binding(id='f' * 15)],
                     [binding(id=letter * 15, port=4825 + index) for index, letter in enumerate('defgh')]]:
            with self.assertRaises(ValueError): private.validate_bindings(policy(rows))
        self.assertEqual(private.validate_bindings(policy(enabled=False)), {})
        self.assertEqual(len(private.validate_bindings(policy([binding(), binding(id='f' * 15, appId=OTHER)]))), 2)

    def test_policy_versions_applications_and_sizes_are_strict(self):
        for value in [[], policy(version=2), policy(enabled='true'), policy(applications=['../unsafe']),
                      policy(applications=[APP] * 501), policy(rows=[binding()] * 2001)]:
            with self.subTest(value=str(value)[:120]), self.assertRaises(ValueError): private.validate_bindings(value)


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='kdm-', dir='/tmp'); self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name).resolve()
        self.image = {'Entrypoint': ['/usr/local/bin/node'], 'Cmd': ['server.js'],
                      'Env': ['NODE_OPTIONS=--require=/app/image-preload.js', 'NODE_PATH=/app/image-modules', 'LD_PRELOAD=/app/image.so', 'LD_LIBRARY_PATH=/app/lib']}
        self.docker = mock.Mock(side_effect=self.image_inspect)
        self.manager = private.PrivateDatabases('http://127.0.0.1:8091', 'worker-secret', 'runtime-socket', self.root, self.docker)
        self.manager.api = mock.Mock(return_value=policy())
        self.job = types.SimpleNamespace(app=APP, id=DEP, snapshot={})
        self.config = {'Image': IMAGE, 'Cmd': ['server.js', 'literal;$(never-execute)'], 'Env': ['NODE_OPTIONS=--max-old-space-size=333',
            'KOLAB_DB_4825_URL=invalid', 'NORMAL=value'], 'Labels': {'pockethost.web.app': APP},
            'HostConfig': {'Memory': 512 * 1024**2, 'NanoCpus': 250000000, 'Mounts': []}}

    def image_inspect(self, args, **kwargs):
        if args == ['image', 'inspect', IMAGE]: return json.dumps([{'Config': self.image}])
        raise AssertionError('Unexpected Docker call: ' + repr(args))

    def admission(self):
        patches = [mock.patch.object(runtime, 'admit'), mock.patch.object(project_limits, 'admit'),
                   mock.patch.object(runtime, 'prepare_tooling', return_value='trusted-tooling'),
                   mock.patch.object(runtime, 'tooling_node_prefix', return_value=['/trusted/loader', '--library-path', '/trusted/lib', '/trusted/node'])]
        values = []
        for patcher in patches:
            values.append(patcher.start()); self.addCleanup(patcher.stop)
        return values

    def test_prepare_reserves_sidecar_quota_and_restores_preloads_only_for_child(self):
        admit, project_admit, tooling, _ = self.admission()
        result = self.manager.prepare(self.job, self.config)
        admit.assert_called_once_with(self.docker, 608, 300)
        project_admit.assert_called_once_with(self.root, self.job, self.docker, 608, 300)
        self.assertEqual(self.config['Entrypoint'][-2:], ['/run/kolab-wait.cjs', '--'])
        self.assertEqual(self.config['Cmd'], ['/usr/local/bin/node', 'server.js', 'literal;$(never-execute)'])
        self.assertTrue(self.config['HostConfig']['Init'])
        env = dict(item.split('=', 1) for item in self.config['Env'])
        self.assertEqual({name: env[name] for name in ['NODE_OPTIONS', 'NODE_PATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH']},
                         {'NODE_OPTIONS': '', 'NODE_PATH': '', 'LD_PRELOAD': '', 'LD_LIBRARY_PATH': ''})
        self.assertEqual(env['KOLAB_DB_4825_URL'], 'http://127.0.0.1:4825'); self.assertEqual(env['NORMAL'], 'value')
        mounts = self.config['HostConfig']['Mounts']; self.assertTrue(all(m['ReadOnly'] for m in mounts))
        self.assertNotIn('/run/kolab-db.sock', [m['Target'] for m in mounts])
        self.assertNotIn(TOKEN, json.dumps(self.config)); self.assertNotIn(TOKEN, json.dumps(result))
        environment = json.loads((pathlib.Path(result['home']) / 'wait-env/config.json').read_text())
        self.assertEqual(environment, {'version': 1, 'environment': {'NODE_OPTIONS': '--max-old-space-size=333', 'NODE_PATH': '/app/image-modules',
            'LD_PRELOAD': '/app/image.so', 'LD_LIBRARY_PATH': '/app/lib'}})

    def test_prepare_rejects_quota_and_unsafe_commands_before_container_mutation(self):
        admit, project_admit, _, _ = self.admission(); admit.side_effect = RuntimeError('quota')
        original = copy.deepcopy(self.config)
        with self.assertRaises(RuntimeError): self.manager.prepare(self.job, self.config)
        self.assertEqual(self.config, original); self.docker.assert_not_called()
        admit.side_effect = None
        for command in ['node server.js', [None], ['node\0bad'], ['node'] * 101]:
            with self.subTest(command=command):
                self.config = copy.deepcopy(original); self.config['Cmd'] = command
                with self.assertRaises(RuntimeError): self.manager.prepare(self.job, self.config)
                self.assertNotIn('pockethost.web.private-db.wait', self.config['Labels'])

    def test_unbound_apps_remain_unmodified_and_control_failure_does_not_reuse_grants(self):
        self.admission(); original = copy.deepcopy(self.config); self.manager.api.return_value = policy([])
        self.assertIsNone(self.manager.prepare(self.job, self.config)); self.assertEqual(self.config, original)
        self.manager.bindings = {APP: [binding()]}; self.manager.last_good = time.monotonic()
        self.manager.api.side_effect = OSError('controller unavailable')
        with self.assertRaises(RuntimeError): self.manager.for_app(APP, force=True)
        self.manager.last_good = time.monotonic() - 11
        self.assertEqual(self.manager.for_app(OTHER, force=True), [])

    def test_state_files_reject_symlink_replacement(self):
        outside = self.root / 'outside'; outside.write_text('preserved'); link = self.root / 'state'
        link.symlink_to(outside)
        with self.assertRaises(RuntimeError): private.atomic(link, TOKEN)
        self.assertEqual(outside.read_text(), 'preserved')
        link.unlink(); link.with_suffix('.tmp').symlink_to(outside)
        with self.assertRaises(RuntimeError): private.atomic(link, TOKEN)
        self.assertEqual(outside.read_text(), 'preserved')


class LifecycleTests(unittest.TestCase):
    setUpBase = ManagerTests.setUp
    image_inspect = ManagerTests.image_inspect
    admission = ManagerTests.admission

    def setUp(self):
        self.setUpBase(); self.admission()
        self.app, self.proxy = application(), None
        self.app['Config']['Labels']['pockethost.web.private-db.wait'] = 'true'
        self.created, self.removed, self.executed = [], [], []
        self.probe_ready = True
        self.namespace = 'net:[123456]'
        self.init_ticks = '100'
        self.socket_root = self.root / 'broker'; self.socket_root.mkdir()
        self.socket_path = self.socket_root / (APP + '.sock')
        self.bind_socket()
        for patcher in [mock.patch.object(private, 'SOCKET_ROOT', self.socket_root), mock.patch.object(storage, '_helper', return_value=IMAGE)]:
            patcher.start(); self.addCleanup(patcher.stop)
        self.docker.side_effect = self.engine
        self.manager.proxy_status = mock.Mock(side_effect=lambda proxy, config_hash: {'namespace': self.namespace})
        self.manager.probe = mock.Mock(side_effect=lambda app, rows: self.probe_ready)

    def bind_socket(self):
        descriptor = socket.socket(socket.AF_UNIX); descriptor.bind(str(self.socket_path)); self.addCleanup(descriptor.close)

    def engine(self, args, **kwargs):
        if args[:2] == ['image', 'inspect']: return self.image_inspect(args)
        if args[0] == 'create':
            self.created.append(args)
            labels = {args[index + 1].split('=', 1)[0]: args[index + 1].split('=', 1)[1] for index, value in enumerate(args) if value == '--label'}
            self.proxy = {'Id': str(len(self.created) + 1) * 64, 'Name': '/' + args[args.index('--name') + 1], 'Config': {'Labels': labels},
                          'State': {'Running': False, 'StartedAt': 'proxy-start-' + str(len(self.created)), 'Health': {'Status': 'healthy'}}}
            return self.proxy['Id']
        if args[0] == 'start':
            self.proxy['State']['Running'] = True; return self.proxy['Id']
        if args[:2] == ['rm', '-f']:
            self.removed.append(args[-1]); self.proxy = None; return ''
        if args[0] == 'exec':
            self.executed.append(args)
            if '--probe' in args and not self.probe_ready: raise RuntimeError('broker not ready')
            if '/proc/1/stat' in args[-1]: return self.identity()
            return self.namespace if 'readlinkSync' in args[-1] else ''
        if args[0] == 'ps':
            return ' '.join([self.app['Id']] + ([self.proxy['Id']] if self.proxy else []))
        if args[0] == 'inspect':
            if args[1:3] == ['--type', 'container']:
                if args[3] == self.app['Id']: return json.dumps([self.app])
                if self.proxy and args[3] in (self.proxy['Id'], self.proxy['Name'].lstrip('/')): return json.dumps([self.proxy])
                raise RuntimeError('not found')
            items = {self.app['Id']: self.app}
            if self.proxy: items[self.proxy['Id']] = self.proxy
            return json.dumps([items[ident] for ident in args[1:]])
        raise AssertionError('Unexpected Docker operation: ' + repr(args))

    def identity(self):
        return self.namespace + ':' + self.init_ticks

    def test_sidecar_has_only_app_socket_in_immutable_parent_namespace(self):
        self.assertTrue(self.manager.ensure(self.app, [binding()]))
        args = self.created[0]
        self.assertEqual(args[args.index('--network') + 1], 'container:' + CID)
        for flag in ['--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--user=10001:10001',
                     '--memory=96m', '--memory-swap=96m', '--cpus=0.05', '--pids-limit=64', '--init']: self.assertIn(flag, args)
        mounts = [args[index + 1] for index, item in enumerate(args) if item == '--mount']
        self.assertTrue(any('source=' + str(self.socket_path) + ',target=/run/kolab-db.sock,readonly' in mount for mount in mounts))
        self.assertFalse(any('docker.sock' in mount or '/run/kolab-db-targets' in mount for mount in mounts))
        self.assertNotIn('--publish', args); self.assertNotIn(TOKEN, ' '.join(args))
        home, _, ready = self.manager.paths(APP, DEP, 'web')
        self.assertEqual((ready / 'ready').read_text(), self.identity())
        self.assertEqual(json.loads((home / 'config/config.json').read_text())['bindings'][0]['token'], TOKEN)

    def test_app_restart_and_broker_socket_inode_each_recreate_sidecar(self):
        self.manager.ensure(self.app, [binding()]); initial = self.proxy['Id']
        self.manager.ensure(self.app, [binding()]); self.assertEqual(len(self.created), 1)
        self.app['State']['StartedAt'] += '-restart'; self.namespace = 'net:[234567]'
        self.manager.ensure(self.app, [binding()]); self.assertIn(initial, self.removed); self.assertEqual(len(self.created), 2)
        before = self.proxy['Id']; self.socket_path.rename(self.socket_path.with_suffix('.old')); self.bind_socket()
        self.manager.ensure(self.app, [binding()]); self.assertIn(before, self.removed); self.assertEqual(len(self.created), 3)
        _, _, ready = self.manager.paths(APP, DEP, 'web'); self.assertEqual((ready / 'ready').read_text(), self.identity())

    def test_token_revision_update_refreshes_config_without_recreating_namespace(self):
        self.manager.ensure(self.app, [binding()]); initial = self.proxy['Id']; before = len(self.executed)
        changed = binding(token='S' * 64, revision='T' * 24)
        self.manager.ensure(self.app, [changed]); self.assertEqual(self.proxy['Id'], initial); self.assertEqual(len(self.created), 1)
        self.assertGreater(len(self.executed), before)
        _, folder, _ = self.manager.paths(APP, DEP, 'web')
        self.assertEqual(json.loads((folder / 'config.json').read_text())['bindings'], [{k: changed[k] for k in ('id', 'port', 'token', 'revision')}])

    def test_revocation_removes_proxy_but_releases_app_restart_gate(self):
        self.manager.ensure(self.app, [binding()]); initial = self.proxy['Id']
        self.manager.api.side_effect = lambda action, body=None: policy([]) if action == 'worker' else {}
        self.manager.last_fetch = 0; self.namespace = 'net:[345678]'
        self.manager.tick()
        self.assertIsNone(self.proxy); self.assertIn(initial, self.removed)
        _, _, ready = self.manager.paths(APP, DEP, 'web'); self.assertEqual((ready / 'ready').read_text(), self.identity())

    def test_foreign_same_named_container_is_not_adopted_or_removed(self):
        self.manager.ensure(self.app, [binding()]); self.proxy['Config']['Labels']['pockethost.web.app'] = OTHER
        with self.assertRaises(RuntimeError): self.manager.ensure(self.app, [binding()])
        self.assertFalse(self.removed)

    def test_stale_control_removes_capabilities_without_releasing_new_namespace(self):
        self.manager.ensure(self.app, [binding()]); initial = self.proxy['Id']
        self.manager.last_good = time.monotonic() - 11; self.manager.last_fetch = 0
        self.manager.api.side_effect = OSError('control unavailable')
        self.namespace = 'net:[456789]'
        with self.assertRaises(OSError): self.manager.tick()
        self.assertIsNone(self.proxy); self.assertIn(initial, self.removed)
        _, _, ready = self.manager.paths(APP, DEP, 'web')
        self.assertNotEqual((ready / 'ready').read_text(), self.identity())

    def test_long_compose_service_uses_bounded_stable_name_and_label(self):
        for service in ['x' * 31, 'x' * 30 + 'y']:
            with self.subTest(service=service):
                self.proxy = None
                self.app['Config']['Labels']['pockethost.web.service'] = service
                self.assertTrue(self.manager.ensure(self.app, [binding()]))
                args = self.created[-1]; name = args[args.index('--name') + 1]
                suffix = name.removeprefix('ph-web-' + APP + '-' + DEP + '-')
                self.assertEqual(suffix, 'private-db-' + hashlib.sha256(service.encode()).hexdigest()[:16])
                self.assertLessEqual(len(suffix), 31)
                self.assertEqual(self.proxy['Config']['Labels']['pockethost.web.service'], suffix)
                before = len(self.created); self.manager.ensure(self.app, [binding()]); self.assertEqual(len(self.created), before)

    def test_parent_stop_and_restart_make_stale_running_snapshot_unusable(self):
        stale = copy.deepcopy(self.app)
        for state in [{'Running': False, 'StartedAt': stale['State']['StartedAt']},
                      {'Running': True, 'StartedAt': stale['State']['StartedAt'] + '-new'}]:
            with self.subTest(state=state):
                self.app['State'] = state
                self.assertFalse(self.manager.ensure(stale, [binding()]))
                self.assertFalse(self.created)
                self.assertFalse((self.manager.folder / APP).exists())
        self.app = copy.deepcopy(stale); self.manager.ensure(self.app, [binding()]); self.proxy['State']['Running'] = False
        self.app['State']['Running'] = False
        self.assertFalse(self.manager.ensure(stale, [binding()]))
        self.assertFalse(self.proxy['State']['Running'])

    def test_tick_waiting_behind_stop_lock_rechecks_parent_before_creation(self):
        stale = copy.deepcopy(self.app); started = threading.Event(); finished = threading.Event(); result = []
        def tick():
            started.set()
            try: result.append(self.manager.ensure(stale, [binding()]))
            finally: finished.set()
        with self.manager.operations_lock:
            thread = threading.Thread(target=tick); thread.start(); self.assertTrue(started.wait(1))
            self.assertFalse(finished.wait(.05)); self.app['State']['Running'] = False
        thread.join(2); self.assertFalse(thread.is_alive()); self.assertEqual(result, [False]); self.assertFalse(self.created)
        self.assertFalse((self.manager.folder / APP).exists())

    def test_initial_marker_waits_for_ack_of_running_database_then_retries(self):
        self.probe_ready = False; rows = [binding(databaseRunning=True, available=False)]
        self.assertFalse(self.manager.ensure(self.app, rows))
        home, _, ready = self.manager.paths(APP, DEP, 'web')
        self.assertFalse((ready / 'ready').exists()); self.assertFalse((home / 'proof.json').exists())
        self.assertEqual(self.manager.probe.call_args.args[0], APP)
        self.assertEqual(self.manager.probe.call_args.args[1][0]['id'], BINDING)
        self.probe_ready = True
        self.assertTrue(self.manager.ensure(self.app, rows)); self.assertEqual((ready / 'ready').read_text(), self.identity())
        self.assertEqual(json.loads((home / 'proof.json').read_text())['probeBindings'], [BINDING])

    def test_explicitly_stopped_database_does_not_prevent_application_start(self):
        self.probe_ready = False
        self.assertTrue(self.manager.ensure(self.app, [binding(databaseRunning=False, available=False)]))
        self.manager.probe.assert_not_called()
        # Older controllers have no databaseRunning field: available=true still
        # requires the handshake, while available=false cannot block startup.
        self.proxy = None
        home, _, ready = self.manager.paths(APP, DEP, 'web'); (ready / 'ready').unlink(); (home / 'proof.json').unlink()
        self.assertFalse(self.manager.ensure(self.app, [binding()]))
        self.manager.probe.assert_called()

    def test_foreground_wait_retries_ack_but_has_a_bounded_deadline(self):
        self.probe_ready = False
        with mock.patch.object(private.time, 'sleep', side_effect=lambda _: setattr(self, 'probe_ready', True)):
            self.assertTrue(self.manager.ensure(self.app, [binding(databaseRunning=True)], wait=True))
        self.proxy = None; self.probe_ready = False
        home, _, ready = self.manager.paths(APP, DEP, 'web'); (ready / 'ready').unlink(); (home / 'proof.json').unlink()
        with mock.patch.object(private.time, 'monotonic', side_effect=[0, 31]):
            with self.assertRaises(RuntimeError): self.manager.ensure(self.app, [binding(databaseRunning=True)], wait=True)
        self.assertFalse((ready / 'ready').exists())

    def test_reused_network_namespace_requires_new_pid1_identity_after_restart(self):
        rows = [binding(databaseRunning=True)]
        self.assertTrue(self.manager.ensure(self.app, rows))
        home, _, ready = self.manager.paths(APP, DEP, 'web'); old_identity = (ready / 'ready').read_text()
        self.app['State']['StartedAt'] += '-restart'; self.init_ticks = '200'; self.probe_ready = False
        self.assertFalse(self.manager.ensure(self.app, rows))
        self.assertEqual((ready / 'ready').read_text(), old_identity)
        self.assertNotEqual(old_identity, self.identity())
        self.probe_ready = True; self.assertTrue(self.manager.ensure(self.app, rows))
        self.assertEqual((ready / 'ready').read_text(), self.identity())
        self.assertEqual(json.loads((home / 'proof.json').read_text())['identity'], self.identity())
        commands = [args for args in self.executed if '/proc/1/stat' in args[-1]]
        self.assertTrue(commands)
        for args in commands:
            for name in ('NODE_OPTIONS', 'NODE_PATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH'): self.assertIn(name + '=', args)

    def test_application_namespace_must_match_proxy_before_marker(self):
        original = self.engine
        def mismatched(args, **kwargs):
            if args[0] == 'exec' and '/proc/1/stat' in args[-1]: return 'net:[999999]:100'
            return original(args, **kwargs)
        self.docker.side_effect = mismatched
        self.assertFalse(self.manager.ensure(self.app, [binding()]))
        home, _, ready = self.manager.paths(APP, DEP, 'web'); self.assertFalse((ready / 'ready').exists()); self.assertFalse((home / 'proof.json').exists())

    def test_legacy_app_does_not_need_node_in_its_own_filesystem(self):
        del self.app['Config']['Labels']['pockethost.web.private-db.wait']
        self.assertTrue(self.manager.ensure(self.app, [binding()]))
        self.assertFalse(any('/proc/1/stat' in args[-1] for args in self.executed))
        _, _, ready = self.manager.paths(APP, DEP, 'web'); self.assertEqual((ready / 'ready').read_text(), self.namespace)

    def test_deleted_parent_cannot_recreate_a_released_state_directory(self):
        snapshot = copy.deepcopy(self.app); self.app['State']['Running'] = False
        self.manager.release_unbound(snapshot)
        self.assertFalse((self.manager.folder / APP).exists())

    def test_old_healthcheck_recipe_is_recreated_with_headroom_and_same_resources(self):
        self.manager.ensure(self.app, [binding()]); initial = self.proxy['Id']
        old_recipe = hashlib.sha256((private.SCRIPT_ROOT / 'web_private_db_proxy.cjs').read_bytes()
            + os.environ.get('WEB_APPS_STORAGE_HELPER_IMAGE', '').encode()).hexdigest()
        self.assertNotEqual(self.manager.recipe, old_recipe)
        self.proxy['Config']['Labels']['pockethost.web.private-db.recipe'] = old_recipe
        self.assertTrue(self.manager.ensure(self.app, [binding()]))
        self.assertIn(initial, self.removed)
        command = self.created[-1]
        for option in ('--health-timeout=5s', '--health-interval=2s', '--cpus=0.05', '--memory=96m', '--pids-limit=64'):
            self.assertIn(option, command)
        with mock.patch.object(private, 'PROXY_HEALTH_TIMEOUT', '6s'):
            changed = private.PrivateDatabases('http://127.0.0.1:8091', 'worker-secret', 'runtime-socket', self.root, self.docker)
        self.assertNotEqual(changed.recipe, self.manager.recipe)

    def test_proxy_readiness_uses_no_node_exec_and_cache_avoids_repeated_status_reads(self):
        self.assertTrue(self.manager.ensure(self.app, [binding()]))
        self.assertTrue(self.executed)
        self.assertTrue(all(args[0] != 'exec' or '/proc/1/stat' in args[-1] for args in self.executed))
        self.assertEqual(self.manager.proxy_status.call_count, 1); self.assertEqual(self.manager.probe.call_count, 1)
        self.manager.ensure(self.app, [binding()])
        self.assertEqual(self.manager.proxy_status.call_count, 1); self.assertEqual(self.manager.probe.call_count, 1)

    def test_broker_socket_replaced_after_host_ack_cannot_release_old_mounted_proxy(self):
        def swapped(app, rows):
            self.socket_path.rename(self.socket_path.with_suffix('.retained')); self.bind_socket(); return True
        self.manager.probe.side_effect = swapped
        self.assertFalse(self.manager.ensure(self.app, [binding()]))
        home, _, ready = self.manager.paths(APP, DEP, 'web')
        self.assertFalse((ready / 'ready').exists()); self.assertFalse((home / 'proof.json').exists())
        self.manager.probe.side_effect = lambda app, rows: True
        self.assertTrue(self.manager.ensure(self.app, [binding()])); self.assertEqual(len(self.created), 2)

    def test_socket_replaced_during_app_identity_exec_is_rechecked_before_marker(self):
        original = self.engine
        def replaced(args, **kwargs):
            if args[0] == 'exec' and '/proc/1/stat' in args[-1]:
                self.socket_path.rename(self.socket_path.with_suffix('.retained')); self.bind_socket()
            return original(args, **kwargs)
        self.docker.side_effect = replaced
        self.assertFalse(self.manager.ensure(self.app, [binding()]))
        _, _, ready = self.manager.paths(APP, DEP, 'web'); self.assertFalse((ready / 'ready').exists())

    def test_concurrent_ensure_shares_one_proof_read_probe_and_marker_write(self):
        entered = threading.Event(); release = threading.Event(); second_started = threading.Event(); second_finished = threading.Event()
        results, errors = [], []
        def slow_status(proxy, config_hash):
            entered.set()
            if not release.wait(2): raise RuntimeError('test deadline')
            return {'namespace': self.namespace}
        self.manager.proxy_status.side_effect = slow_status
        def ensure(second=False):
            if second: second_started.set()
            try: results.append(self.manager.ensure(self.app, [binding()], wait=True))
            except Exception as error: errors.append(error)
            finally:
                if second: second_finished.set()
        original_atomic = private.atomic
        with mock.patch.object(private, 'atomic', wraps=original_atomic) as writes:
            first = threading.Thread(target=ensure); first.start(); self.assertTrue(entered.wait(1))
            second = threading.Thread(target=ensure, args=(True,)); second.start(); self.assertTrue(second_started.wait(1))
            self.assertFalse(second_finished.wait(.05))
            self.assertEqual(self.manager.proxy_status.call_count, 1)
            release.set(); first.join(2); second.join(2)
            self.assertFalse(first.is_alive()); self.assertFalse(second.is_alive())
            self.assertEqual(errors, []); self.assertEqual(results, [True, True])
            self.assertEqual(self.manager.proxy_status.call_count, 1); self.assertEqual(self.manager.probe.call_count, 1)
            self.assertEqual(len([call for call in writes.call_args_list if pathlib.Path(call.args[0]).name == 'ready']), 1)
            self.assertEqual(len([call for call in writes.call_args_list if pathlib.Path(call.args[0]).name == 'proof.json']), 1)
        self.assertEqual(len([args for args in self.executed if '/proc/1/stat' in args[-1]]), 1)


if __name__ == '__main__': unittest.main()
