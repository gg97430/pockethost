#!/usr/bin/env python3
"""Real localhost HTTP fixtures; no Docker engine or remote server is touched."""
import copy
import http.server
import importlib.util
import json
import pathlib
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))  # web_app_health shares the webhook SSRF resolver.
spec = importlib.util.spec_from_file_location('web_app_health', pathlib.Path(__file__).with_name('web_app_health.py'))
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)
APP, DEPLOYMENT = 'a' * 15, 'b' * 15


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    hits = []
    redirect = ''

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.hits.append((self.path, dict(self.headers)))
        if self.path == '/slow':
            # Headers trickle faster than a socket read timeout. The total deadline must still win.
            try:
                self.wfile.write(b'HTTP/1.1 200 OK\r\nX-Slow: ')
                self.wfile.flush()
                for _ in range(20):
                    time.sleep(.15)
                    self.wfile.write(b'a')
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        code = 503 if self.path == '/failure' else 302 if self.path == '/redirect' else 200
        self.send_response(code)
        if code == 302:
            self.send_header('Location', self.redirect)
        self.send_header('Content-Length', '100000000')
        self.end_headers()  # Deliberately no body: probes must not wait for or retain it.


class HealthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server(('127.0.0.1', 0), Handler)
        cls.other = Server(('127.0.0.1', 0), Handler)
        Handler.redirect = f'http://127.0.0.1:{cls.other.server_port}/never-follow'
        for server in (cls.server, cls.other):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.other.shutdown()
        cls.server.server_close(); cls.other.server_close()

    def setUp(self):
        Handler.hits.clear()
        self.config = dict(appId=APP, deploymentId=DEPLOYMENT, containerName=f'ph-web-{APP}-{DEPLOYMENT}',
            expectedTarget=f'http://127.0.0.1:{self.server.server_port}', path='/health', intervalSeconds=60,
            timeoutSeconds=1, failureThreshold=3, revision='r' * 24)
        self.info = {'Id': 'f' * 64, 'State': {'Running': True},
            'Config': {'User': '10001:10001', 'Env': ['PRIVATE=do-not-store'], 'Labels': {'pockethost.web': 'true', 'pockethost.web.app': APP, 'pockethost.web.deployment': DEPLOYMENT}},
            'HostConfig': {'Privileged': False, 'ReadonlyRootfs': True, 'Binds': []},
            'NetworkSettings': {'Ports': {'3000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(self.server.server_port)}]}}}
        self.calls = []

    def docker(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return json.dumps([self.info])

    def test_safe_paths_and_configuration_limits(self):
        for path in ['/', '/api/health', '/ready-check']:
            self.assertTrue(health.safe_path(path))
        for path in ['//169.254.169.254', '/?secret=value', '/../../', '/a%2fb', '/x\ny', '/a\\b', 'http://evil']:
            self.assertFalse(health.safe_path(path))
        for value in ['http://169.254.169.254:80', 'http://localhost:8091', 'http://127.0.0.1:8091/path']:
            with self.assertRaises(ValueError): health.HealthControl(value, 's' * 40)
        bad = dict(self.config, intervalSeconds=59)
        with self.assertRaises(ValueError): health.probe(bad, self.docker)
        self.assertEqual(self.calls, [])

    def test_real_http_success_ignores_bodies_and_has_no_credentials(self):
        started = time.monotonic()
        result = health.probe(self.config, self.docker)
        self.assertLess(time.monotonic() - started, .8)
        self.assertEqual(result['statusCode'], 200)
        self.assertEqual(result['errorCode'], '')
        self.assertEqual(self.calls, [(['inspect', self.config['containerName']], {'timeout': 5})])
        self.assertNotIn('do-not-store', json.dumps(result))
        headers = {k.lower(): v for k, v in Handler.hits[0][1].items()}
        self.assertFalse(any(k in headers for k in ['cookie', 'authorization', 'x-web-worker']))

    def test_http_failure_and_no_redirect_even_to_another_loopback_port(self):
        self.assertEqual(health.probe(dict(self.config, path='/failure'), self.docker)['errorCode'], 'http_error')
        result = health.probe(dict(self.config, path='/redirect'), self.docker)
        self.assertEqual(result['statusCode'], 302)
        self.assertEqual([path for path, _ in Handler.hits], ['/failure', '/redirect'])

    def test_total_deadline_stops_slow_header_trickling(self):
        started = time.monotonic()
        result = health.probe(dict(self.config, path='/slow'), self.docker)
        self.assertEqual(result['errorCode'], 'timeout')
        self.assertLess(time.monotonic() - started, 1.8)

    def test_published_frontend_can_use_a_non_nextjs_container_port(self):
        self.info['NetworkSettings']['Ports'] = {'8080/tcp': self.info['NetworkSettings']['Ports']['3000/tcp']}
        self.assertEqual(health.probe(self.config, self.docker)['statusCode'], 200)

    def test_foreign_container_and_port_are_never_probed(self):
        for change in ['label', 'port', 'user', 'mount']:
            original = copy.deepcopy(self.info)
            if change == 'label': self.info['Config']['Labels']['pockethost.web.app'] = 'x' * 15
            if change == 'port': self.info['NetworkSettings']['Ports']['3000/tcp'][0]['HostPort'] = '8091'
            if change == 'user': self.info['Config']['User'] = 'root'
            if change == 'mount': self.info['HostConfig']['Binds'] = ['/etc:/etc']
            self.assertEqual(health.probe(self.config, self.docker)['errorCode'], 'target_mismatch')
            self.info = original
        self.assertEqual(Handler.hits, [])

    def test_background_monitor_keeps_probing_while_main_thread_is_busy(self):
        reported = threading.Event()
        config = self.config
        class Control:
            def send(self, endpoint, body):
                if endpoint == 'config': return {'items': [config], 'hasMore': False}
                self.result = body; reported.set(); return {'accepted': True}
        control = Control()
        monitor = health.HealthMonitor(control, self.docker).start()
        try:
            # Simulates a deployment blocking the worker's main execution loop.
            self.assertTrue(reported.wait(2))
            self.assertEqual(control.result['statusCode'], 200)
        finally: monitor.stop()

    def test_monitor_bounds_parallelism_and_stops_using_stale_configuration(self):
        release = threading.Event()
        configs = [dict(self.config, appId=f'{i:015d}', containerName=f'ph-web-{i:015d}-{DEPLOYMENT}') for i in range(10)]
        class Control:
            failed = False
            def send(self, endpoint, body):
                if self.failed: raise OSError()
                return {'items': configs, 'hasMore': False} if endpoint == 'config' else {'accepted': True}
        def blocked(*args, **kwargs): release.wait(2); return json.dumps([])
        control = Control(); monitor = health.HealthMonitor(control, blocked)
        monitor.tick()
        with monitor._lock: self.assertEqual(len(monitor._pending), 4)
        control.failed = True; monitor._next_refresh = 0; monitor.tick()
        self.assertEqual(monitor._configs, {})
        monitor._stop.set(); release.set()


class PublicHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    hits = []
    redirect = ''

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.hits.append((self.path, dict(self.headers)))
        code = 503 if self.path == '/public-failure' else 302 if self.path == '/redirect' else 200
        self.send_response(code)
        if code == 302:
            self.send_header('Location', self.redirect)
        self.send_header('Content-Length', '0')
        self.end_headers()


@unittest.skipUnless(shutil.which('openssl'), 'openssl requis')
class PublicProbeTests(unittest.TestCase):
    """Local TLS endpoint standing in for the real domain: no DNS query or remote host is contacted."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        folder = pathlib.Path(cls.tmp.name)
        (folder / 'openssl.cnf').write_text('[req]\ndistinguished_name=dn\nprompt=no\nx509_extensions=ext\n[dn]\nCN=site.example.com\n'
                                            '[ext]\nsubjectAltName=DNS:site.example.com\nbasicConstraints=critical,CA:TRUE\n')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2', '-keyout', str(folder / 'key.pem'),
                        '-out', str(folder / 'cert.pem'), '-config', str(folder / 'openssl.cnf')], check=True, capture_output=True)
        cls.cert = folder / 'cert.pem'
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.cert, folder / 'key.pem')
        cls.sni = []
        context.sni_callback = lambda sock, name, ctx: cls.sni.append(name)
        cls.loopback = Server(('127.0.0.1', 0), Handler)
        cls.public = Server(('127.0.0.1', 0), PublicHandler)
        cls.public.socket = context.wrap_socket(cls.public.socket, server_side=True)
        cls.private = Server(('127.0.0.1', 0), Handler)
        PublicHandler.redirect = f'http://127.0.0.1:{cls.private.server_port}/never-follow'
        for server in (cls.loopback, cls.public, cls.private):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.silent = socket.socket()
        cls.silent.bind(('127.0.0.1', 0))
        cls.silent.listen(4)  # TCP completes, TLS never starts.

    @classmethod
    def tearDownClass(cls):
        for server in (cls.loopback, cls.public, cls.private):
            server.shutdown(); server.server_close()
        cls.silent.close()
        cls.tmp.cleanup()

    def setUp(self):
        Handler.hits.clear(); PublicHandler.hits.clear(); self.sni.clear()
        self.config = dict(appId=APP, deploymentId=DEPLOYMENT, containerName=f'ph-web-{APP}-{DEPLOYMENT}',
            expectedTarget=f'http://127.0.0.1:{self.loopback.server_port}', path='/health', intervalSeconds=60,
            timeoutSeconds=1, failureThreshold=3, revision='r' * 24, publicHost='site.example.com')
        self.info = {'Id': 'f' * 64, 'State': {'Running': True},
            'Config': {'User': '10001:10001', 'Labels': {'pockethost.web': 'true', 'pockethost.web.app': APP, 'pockethost.web.deployment': DEPLOYMENT}},
            'HostConfig': {'Privileged': False, 'ReadonlyRootfs': True, 'Binds': []},
            'NetworkSettings': {'Ports': {'3000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(self.loopback.server_port)}]}}}
        self.port = self.public.server_port
        original = ssl.create_default_context
        self.patches = [mock.patch.object(health, '_resolve_public', lambda host: [(socket.AF_INET, socket.SOCK_STREAM, 6, ('127.0.0.1', self.port))]),
                        mock.patch.object(health, '_public_context', lambda: original(cafile=str(self.cert)))]
        for patch in self.patches: patch.start()

    def tearDown(self):
        for patch in self.patches: patch.stop()

    def docker(self, args, **kwargs):
        return json.dumps([self.info])

    def check(self, **changes):
        result = health.probe(dict(self.config, **changes), self.docker)
        self.assertIn(result['errorCode'], health.ERRORS)
        self.assertIn(result.get('publicErrorCode', ''), health.PUBLIC_ERRORS)
        return result

    def test_public_https_uses_sni_real_host_and_no_credentials(self):
        result = self.check()
        self.assertEqual((result['errorCode'], result['statusCode'], result['publicErrorCode'], result['publicStatusCode']), ('', 200, '', 200))
        self.assertEqual(self.sni, ['site.example.com'])
        path, headers = PublicHandler.hits[0]
        headers = {k.lower(): v for k, v in headers.items()}
        self.assertEqual((path, headers['host']), ('/health', 'site.example.com'))
        self.assertTrue(headers['user-agent'].startswith('KolabHosting'))
        self.assertFalse(any(k in headers for k in ['cookie', 'authorization', 'x-web-worker']))

    def test_public_redirect_counts_as_reachable_and_is_never_followed(self):
        result = self.check(path='/redirect')
        self.assertEqual((result['errorCode'], result['publicStatusCode']), ('', 302))
        self.assertEqual([path for path, _ in Handler.hits], ['/redirect'])  # Loopback only; the private Location is untouched.

    def test_wrong_certificate_is_tls_failure_while_loopback_is_healthy(self):
        result = self.check(publicHost='other.example.com')
        self.assertEqual((result['statusCode'], result['errorCode'], result['publicErrorCode']), (200, 'connection_error', 'public_tls'))
        self.assertEqual(PublicHandler.hits, [])

    def test_public_http_error_and_handshake_deadline(self):
        result = self.check(path='/public-failure')
        self.assertEqual((result['statusCode'], result['errorCode'], result['publicErrorCode'], result['publicStatusCode']), (200, 'http_error', 'public_http_error', 503))
        self.port = self.silent.getsockname()[1]
        started = time.monotonic()
        result = self.check()
        self.assertEqual((result['errorCode'], result['publicErrorCode']), ('timeout', 'public_timeout'))
        self.assertLess(time.monotonic() - started, 2.5)

    def test_private_or_unresolvable_domain_is_refused_before_connecting(self):
        self.patches[0].stop()
        try:
            for answer, code in [([(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.5', 443))], 'public_address_refused'),
                                 ([(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('169.254.169.254', 443))], 'public_address_refused'),
                                 (socket.gaierror(), 'public_dns')]:
                real = socket.getaddrinfo
                def resolver(host, *args, **kwargs):
                    if host != 'site.example.com':
                        return real(host, *args, **kwargs)  # Loopback probe still resolves 127.0.0.1 locally.
                    if isinstance(answer, Exception):
                        raise answer
                    return answer
                with mock.patch.object(socket, 'getaddrinfo', resolver):
                    result = self.check()
                self.assertEqual((result['errorCode'], result['publicErrorCode']), ('connection_error', code))
        finally:
            self.patches[0].start()
        self.assertEqual(PublicHandler.hits, [])

    def test_loopback_failure_skips_public_probe_and_bad_hosts_are_invalid(self):
        result = self.check(path='/failure')
        self.assertEqual(result['errorCode'], 'http_error')
        PublicHandler.hits.clear()
        self.info['Config']['User'] = 'root'
        result = self.check()
        self.assertEqual((result['errorCode'], result['publicErrorCode']), ('target_mismatch', 'not_checked'))
        self.assertEqual(PublicHandler.hits, [])
        for host in ['127.0.0.1', 'localhost', 'a..example.com', 'Site.Example.com', 'x.internal', 'site.example.com:8443', 'é.example.com']:
            self.assertFalse(health.valid_config(dict(self.config, publicHost=host)), host)
        self.assertTrue(health.valid_config(dict(self.config, publicHost='')))
        self.assertNotIn('publicErrorCode', health.probe(dict(self.config, publicHost=''), self.docker))


if __name__ == '__main__': unittest.main()
