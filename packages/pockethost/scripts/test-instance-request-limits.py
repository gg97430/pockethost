#!/usr/bin/env python3
import copy
import http.server
import json
import threading
import unittest
import pathlib
import tempfile
import types
from unittest import mock
import instance_request_limits as native
from web_private_db_limits import limits


class PolicyTests(unittest.TestCase):
    def test_native_container_identity_and_loopback_publication_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve(); ident = 'e' * 15
            value = {'Name': '/' + ident, 'Id': 'a' * 64, 'State': {'Running': True, 'StartedAt': 'epoch'},
                     'Config': {'Image': 'trusted/database:v1', 'Env': ['PH_INSTANCE_ID=' + ident]}, 'HostConfig': {},
                     'Mounts': [{'Type': 'bind', 'Destination': '/home/pockethost', 'Source': str(root / 'instances' / ident)}],
                     'NetworkSettings': {'Ports': {'8090/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '32800'}]}}}
            config = {'instance': ident, 'dataRoot': str(root), 'databaseImage': 'trusted/database'}
            def inspect(data):return native.inspect_database(config, runner=lambda *a, **kw: types.SimpleNamespace(stdout=json.dumps([data])))
            self.assertEqual(inspect(value), ('a' * 64, 'epoch', 32800))
            variants = []
            for section, key, bad in [('Config', 'Image', 'foreign/database:v1'), ('Config', 'Env', ['PH_INSTANCE_ID=' + 'f' * 15]), ('State', 'Running', False), ('HostConfig', 'Privileged', True)]:
                item = copy.deepcopy(value); item[section][key] = bad; variants.append(item)
            item = copy.deepcopy(value); item['NetworkSettings']['Ports']['8090/tcp'][0]['HostIp'] = '0.0.0.0'; variants.append(item)
            item = copy.deepcopy(value); item['Mounts'][0]['Source'] = str(root / 'instances' / ('f' * 15)); variants.append(item)
            for item in variants:
                with self.assertRaises(native.SettingsError):inspect(item)

    def test_invalid_values_and_inconsistent_ceilings_fail_closed(self):
        for value in [True, {}, {'total': -1}, {**limits(), 'perBinding': 256}, {**limits(), 'total': 1025},
                      {**limits(), 'perApp': True}, {**limits(), 'perDatabase': 1}]:
            with self.subTest(value=value), self.assertRaises(ValueError): limits(value)

    def test_native_rule_validation_rejects_injection_and_non_integer_values(self):
        value = {'enabled': False, 'rules': [{'label': '/api/', 'audience': '@guest', 'duration': 10, 'maxRequests': 300}], 'revision': 'a' * 64}
        native.validate_input(value)
        for patch in [{'duration': True}, {'maxRequests': 0}, {'label': '/api/\nInjected'}, {'audience': '*'}]:
            bad = copy.deepcopy(value); bad['rules'][0].update(patch)
            with self.subTest(patch=patch), self.assertRaises(native.SettingsError): native.validate_input(bad)


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.settings = {'rateLimits': {'enabled': True, 'excludedIPs': ['127.0.0.2'], 'rules': []},
                         'smtp': {'password': 'private-fixture'}, 'trustedProxy': {'headers': ['X-Real-Ip']}}
        self.connections, self.patches, self.close_after_login = [], [], False
        fixture = self
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def reply(self, body):
                raw = json.dumps(body).encode(); self.send_response(200)
                self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(raw)))
                if fixture.close_after_login and self.path.endswith('/login'):self.send_header('Connection', 'close');self.close_connection=True
                self.end_headers(); self.wfile.write(raw)
            def do_POST(self):
                fixture.connections.append(self.client_address)
                self.rfile.read(int(self.headers['Content-Length']))
                assert self.path == '/api/ph-console/login'
                self.reply({'token': 'native-private-token', 'record': {'email': 'private-user'}})
            def do_GET(self):
                fixture.connections.append(self.client_address)
                assert self.path == '/api/settings' and self.headers['Authorization'] == 'native-private-token'
                self.reply(fixture.settings)
            def do_PATCH(self):
                fixture.connections.append(self.client_address)
                value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fixture.patches.append(value); fixture.settings.update(value); self.reply(fixture.settings)
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.config = {'action': 'read', 'ticket': 'A' * 64}
        self.identity = ('a' * 64, 'epoch', self.server.server_port)
    def tearDown(self):self.server.shutdown(); self.server.server_close()
    def execute(self):return native.execute(self.config, inspect=lambda _: self.identity)
    def test_roundtrip_keeps_unrelated_settings_and_credentials_private(self):
        value = self.execute(); self.assertNotIn('private', json.dumps(value))
        before = copy.deepcopy(self.settings)
        self.config.update(action='update', input={'enabled': False, 'rules': [], 'revision': value['revision']})
        value = self.execute(); self.assertFalse(value['enabled'])
        self.assertEqual(self.settings['smtp'], before['smtp']); self.assertEqual(self.settings['trustedProxy'], before['trustedProxy'])
        self.assertEqual(self.settings['rateLimits']['excludedIPs'], ['127.0.0.2'])
        self.assertEqual(set(self.patches[0]), {'rateLimits'})
        self.assertEqual(len(set(self.connections[-4:])), 1, 'Settings requests must reuse one TCP connection')
    def test_stale_edit_does_not_patch_native_settings(self):
        self.config.update(action='update', input={'enabled': False, 'rules': [], 'revision': 'b' * 64})
        with self.assertRaises(native.SettingsError) as error:self.execute()
        self.assertEqual(error.exception.status, 409); self.assertEqual(self.patches, [])
    def test_closed_connection_is_never_reopened_on_a_potentially_reassigned_port(self):
        self.close_after_login = True
        with self.assertRaises(native.SettingsError):self.execute()
        self.assertEqual(len(self.connections), 1); self.assertEqual(self.patches, [])
    def test_database_restart_during_connect_aborts_before_transmitting_grant(self):
        with mock.patch.object(native, 'inspect_database'):
            identities = iter([self.identity, ('b' * 64, 'new', self.server.server_port)])
            with self.assertRaises(native.SettingsError):native.execute(self.config, inspect=lambda _: next(identities))
        self.assertEqual(self.connections, [])


if __name__ == '__main__':unittest.main()
