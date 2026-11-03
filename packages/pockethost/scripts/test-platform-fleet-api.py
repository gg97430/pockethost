#!/usr/bin/env python3
"""Disposable native auth/TOTP reception for the read-only fleet route.

Default: only requests that must fail before invoking the helper. --helper-real
also exercises the installed fixed-path helper using a random .invalid fixture
email absent from the real controller: every fleet target must deny, with no
resource data. This option never authenticates as a real operator or modifies
the real controller. No mail, Docker command or tenant endpoint is invoked.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=pathlib.Path, required=True)
    parser.add_argument('--pocketbase', required=True)
    parser.add_argument('--helper-real', action='store_true')
    parser.add_argument('--no-new-privileges', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    process = None
    with tempfile.TemporaryDirectory(prefix='kolab-fleet-api-') as temporary:
        root = pathlib.Path(temporary)
        source = args.source / 'packages/pockethost/src/mothership-app'
        hooks = root / 'hooks'
        hooks.mkdir()
        shutil.copy(source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
        routes = [
            ('GET', '/api/admin/fleet', 'HandleOperatorFleet'),
            ('POST', '/api/account/totp/setup', 'HandleDashboardTotpSetup'),
            ('POST', '/api/account/totp/confirm', 'HandleDashboardTotpConfirm'),
        ]
        code = '\n'.join('routerAdd(' + json.dumps(method) + ',' + json.dumps(path) + ',e=>require(`${__hooks}/mothership`).' + handler + '(e),$apis.requireAuth("users"));' for method, path, handler in routes)
        code += '\nonBootstrap(e=>{e.next();require(`${__hooks}/mothership`).InitializeDashboardTotp()});routerUse(e=>require(`${__hooks}/mothership`).GuardDashboardTotp(e));onRecordAuthRequest(e=>require(`${__hooks}/mothership`).HandleDashboardTotpAuth(e),"users");\n'
        (hooks / 'fleet.pb.js').write_text(code)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        origin = 'http://127.0.0.1:' + str(port)
        env = {**os.environ, 'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid', 'WEB_APPS_ENABLED': 'false'}
        data = root / 'pb_data'
        password = 'P' + secrets.token_urlsafe(32)
        email = 'fleet-' + secrets.token_hex(12) + '@example.invalid'

        def api(path, token='', body=None, method=None, status=200):
            request = urllib.request.Request(origin + path, method=method or ('POST' if body is not None else 'GET'), data=None if body is None else json.dumps(body).encode(), headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {})})
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    assert response.status == status, (path, response.status, status)
                    return json.load(response)
            except urllib.error.HTTPError as error:
                content = error.read()
                assert error.code == status, (path, error.code, status, content.decode()[:500])
                return json.loads(content) if content else None

        def enroll(token):
            setup = api('/api/account/totp/setup', token, {'password': password})
            key = base64.b32decode(setup['secret'] + '=' * (-len(setup['secret']) % 8))
            digest = hmac.new(key, struct.pack('>Q', int(time.time()) // 30), hashlib.sha1).digest()
            offset = digest[-1] & 15
            otp = str((struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000).zfill(6)
            return api('/api/account/totp/confirm', token, {'password': password, 'code': otp})['token']

        try:
            subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(data), '--hooksDir', str(root / 'empty'), '--migrationsDir', str(source / 'pb_migrations')], env=env, check=True, capture_output=True)
            with (root / 'process.log').open('wb') as log:
                prefix = ['setpriv', '--no-new-privs'] if args.no_new_privileges else []
                process = subprocess.Popen(prefix + [args.pocketbase, 'serve', '--http', '127.0.0.1:' + str(port), '--dir', str(data), '--hooksDir', str(hooks), '--migrationsDir', str(source / 'pb_migrations')], env=env, stdout=log, stderr=subprocess.STDOUT)
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('Disposable controller did not start')
                try:
                    api('/api/health')
                    break
                except OSError:
                    time.sleep(.1)
            if args.no_new_privileges:
                assert 'NoNewPrivs:\t1' in pathlib.Path('/proc/' + str(process.pid) + '/status').read_text()
            su = api('/api/collections/_superusers/auth-with-password', body={'identity': email, 'password': password})['token']
            api('/api/admin/fleet', status=401)
            api('/api/admin/fleet', su, status=403)
            tokens = {}
            for label, admin, verified, suspended in [('admin', True, True, ''), ('member', False, True, ''), ('unverified', True, False, ''), ('suspended', True, True, 'paused')]:
                address = email if label == 'admin' else label + '-' + email
                api('/api/collections/users/records', su, {'email': address, 'username': 'fleet' + label, 'password': password, 'passwordConfirm': password, 'verified': verified, 'superAdmin': admin, 'suspension': suspended, 'subscription': 'premium', 'subscription_quantity': 1})
                password_only = api('/api/collections/users/auth-with-password', body={'identity': address, 'password': password})['token']
                denied = api('/api/admin/fleet', password_only, status=403)
                assert denied['data']['totpEnrollmentRequired']['code'] == 'totp_enrollment_required'
                tokens[label] = enroll(password_only)
            for label in ['member', 'unverified', 'suspended']:
                api('/api/admin/fleet', tokens[label], status=403)
            checks = ['native_users_only', 'mandatory_first_login_totp', 'active_verified_superadmin_only']
            if args.no_new_privileges:
                checks.append('real_controller_no_new_privileges_retained')
            if args.helper_real:
                result = api('/api/admin/fleet?email=root@example.invalid&host=app1', tokens['admin'])
                assert result['version'] == 1 and len(result['servers']) == 3
                assert {row['key'] for row in result['servers']} == {'app3', 'app4', 'app5'}
                assert all(row['access'] == 'denied' and 'inventory' not in row and 'counts' not in row for row in result['servers'])
                assert email not in json.dumps(result) and password not in json.dumps(result)
                checks.append('installed_helper_random_fixture_identity_denied_no_real_resource_or_secret_returned')
            print(json.dumps({'passed': True, 'checks': checks}))
        finally:
            if process:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    main()
