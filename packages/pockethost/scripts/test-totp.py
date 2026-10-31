#!/usr/bin/env python3
"""Exercise dashboard and native-console TOTP against disposable PocketBase processes."""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def totp(secret, offset=0):
    counter = int(time.time() // 30) + offset
    digest = hmac.new(base64.b32decode(secret), struct.pack('>Q', counter), hashlib.sha1).digest()
    return str((struct.unpack('>I', digest[digest[-1] & 15:][:4])[0] & 0x7fffffff) % 1000000).zfill(6)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pocketbase', required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--migrations', type=Path, required=True)
    parser.add_argument('--instance-hooks', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--keep-running', action='store_true')
    parser.add_argument('--realm', choices=['both', 'dashboard', 'console'], default='both')
    args = parser.parse_args()
    os.umask(0o077)
    root = args.work_dir.resolve() if args.work_dir else Path(tempfile.mkdtemp(prefix='pockethost-totp-'))
    root.mkdir(parents=True, exist_ok=True)
    processes, logs = [], []
    report = {'work_dir': str(root), 'checks': {}}
    password = secrets.token_urlsafe(30)
    email = 'totp-test@example.invalid'

    def request(base, path, token='', method='GET', body=None, expected=200):
        req = urllib.request.Request(base + path, method=method,
            headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {})},
            data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read())
                assert response.status == expected, (path, response.status, expected)
                return result
        except urllib.error.HTTPError as error:
            result = json.loads(error.read())
            assert error.code == expected, (path, error.code, expected, result)
            return result

    def launch(folder, native, extra_env=None):
        address = port()
        base = f'http://127.0.0.1:{address}'
        log = (folder / 'process.log').open('ab')
        logs.append(log)
        process = subprocess.Popen([args.pocketbase, 'serve', '--dir', str(folder / 'data'), '--http', f'127.0.0.1:{address}',
            '--hooksDir', str(folder / 'hooks'), '--migrationsDir', str(folder / 'migrations')], stdout=log, stderr=subprocess.STDOUT,
            env=dict(os.environ, **(extra_env or {})))
        processes.append(process)
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError('PocketBase exited; inspect process.log')
            try:
                request(base, '/api/health')
                return base, process
            except urllib.error.URLError:
                time.sleep(.1)
        raise TimeoutError('PocketBase did not start')

    try:
        fixtures = []
        for native in [False, True]:
            realm = 'console' if native else 'dashboard'
            if args.realm != 'both' and args.realm != realm:
                continue
            folder = root / realm
            folder.mkdir()
            hooks = folder / 'hooks'
            hooks.mkdir()
            (folder / 'migrations').mkdir()
            if native:
                for filename in ['_ph_totp.js', '_ph_totp.pb.js', '_ph_admin_sync.pb.js', '_ph_lib.js']:
                    shutil.copy(args.instance_hooks / filename, hooks / filename)
            else:
                shutil.copy(args.bundle, hooks / 'mothership.js')
                shutil.copytree(args.migrations, folder / 'migrations', dirs_exist_ok=True, ignore=shutil.ignore_patterns('._*'))
                script = 'onBootstrap((e)=>{e.next();require(`${__hooks}/mothership`).InitializeDashboardTotp()});\n'
                script += 'routerUse((e)=>require(`${__hooks}/mothership`).GuardDashboardTotp(e));\n'
                script += 'onRecordAuthRequest((e)=>require(`${__hooks}/mothership`).HandleDashboardTotpAuth(e), "users");\n'
                for method, suffix, name in [('GET','', 'Status'),('POST','/setup','Setup'),('POST','/confirm','Confirm'),('POST','/disable','Disable'),('POST','/recovery','Regenerate')]:
                    script += f'routerAdd("{method}","/api/account/totp{suffix}",(e)=>require(`${{__hooks}}/mothership`).HandleDashboardTotp{name}(e),$apis.requireAuth("users"));\n'
                (hooks / 'test.pb.js').write_text(script)
            (hooks / 'mint.pb.js').write_text('routerAdd("GET","/__test/unmarked/{collection}/{id}",(e)=>{const r=$app.findRecordById(e.request.pathValue("collection"),e.request.pathValue("id"));return e.json(200,{token:r.newAuthToken()})},$apis.requireSuperuserAuth());')
            # Apply migrations before starting the real HTTP process.
            subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(folder / 'data'),
                '--hooksDir', str(folder / 'no-hooks'), '--migrationsDir', str(folder / 'migrations')], check=True, capture_output=True)
            base, process = launch(folder, native)
            # A collection snapshot may replace the built-in auth collection during the first HTTP boot.
            # Upsert again after migrations, as production provisioning does.
            subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(folder / 'data'),
                '--hooksDir', str(folder / 'no-hooks'), '--migrationsDir', str(folder / 'migrations')], check=True, capture_output=True)
            admin = request(base, '/api/collections/_superusers/auth-with-password', method='POST', body={'identity': email, 'password': password})
            if native:
                auth = admin
                auth_collection, api = '_superusers', '/api/ph-totp'
            else:
                request(base, '/api/collections/users/records', admin['token'], 'POST', {
                    'email': email, 'password': password, 'passwordConfirm': password, 'username': 'totptest',
                    'verified': True, 'subscription': 'premium', 'subscription_quantity': 100, 'superAdmin': True,
                })
                auth = request(base, '/api/collections/users/auth-with-password', method='POST', body={'identity': email, 'password': password})
                auth_collection, api = 'users', '/api/account/totp'
            api_status = api + '/status' if native else api
            token = auth['token']
            auth_url = f'/api/collections/{auth_collection}/auth-with-password'
            record_id = auth['record']['id']
            assert request(base, api_status, token)['enabled'] is False
            request(base, api + '/setup', token, 'POST', {'password': 'wrong-password'}, expected=401)
            setup = request(base, api + '/setup', token, 'POST', {'password': password})
            assert len(setup['secret']) == 32 and setup['qrSvg'].startswith('<svg') and setup['uri'].startswith('otpauth://')
            request(base, api + '/confirm', token, 'POST', {'password': password, 'code': 'not-a-code'}, expected=401)
            enabled = request(base, api + '/confirm', token, 'POST', {'password': password, 'code': totp(setup['secret'])})
            assert len(set(enabled['recoveryCodes'])) == 10
            codes = enabled['recoveryCodes']
            token = enabled['token']
            assert request(base, api_status, token)['enabled'] is True
            key_path = folder / 'data/.pockethost-totp.key'
            assert key_path.stat().st_mode & 0o777 == 0o600
            import sqlite3
            with sqlite3.connect(folder / 'data/data.db') as database:
                encrypted = database.execute('SELECT secret FROM ph_two_factor').fetchone()[0]
                assert encrypted and setup['secret'] not in encrypted
            challenge = request(base, auth_url, method='POST', body={'identity': email, 'password': password}, expected=401)
            assert challenge['data']['totpRequired']['code'] == 'totp_required', challenge
            request(base, api_status, auth['token'], expected=401)
            login = request(base, auth_url, method='POST', body={'identity': email, 'password': password, 'totpCode': codes[0]})
            token = login['token']
            assert request(base, api_status, token)['recoveryCodesRemaining'] == 9
            request(base, auth_url, method='POST', body={'identity': email, 'password': password, 'totpCode': codes[0]}, expected=401)
            # Confirm that a freshly minted, otherwise valid PocketBase JWT cannot bypass the factor.
            root_token = token if native else admin['token']
            unmarked = request(base, f'/__test/unmarked/{auth_collection}/{record_id}', root_token)['token']
            request(base, api_status, unmarked, expected=401)
            request(base, f'/api/collections/{auth_collection}/auth-refresh', unmarked, 'POST', expected=401)
            refreshed = request(base, f'/api/collections/{auth_collection}/auth-refresh', token, 'POST')
            token = refreshed['token']
            assert request(base, api_status, token)['enabled'] is True
            report['checks'][realm + '_password_only_and_unmarked_jwt_rejected'] = True
            report['checks'][realm + '_encrypted_secret_and_private_key'] = True
            report['checks'][realm + '_recovery_one_use_and_refresh'] = True

            # Restart proves that the encryption key and replay state survive process replacement.
            process.terminate()
            process.wait(timeout=10)
            sync_env = {}
            if native:
                with sqlite3.connect(folder / 'data/data.db') as database:
                    native_record = database.execute('SELECT tokenKey, password FROM _superusers WHERE id = ?', (record_id,)).fetchone()
                record_id = secrets.token_hex(8)[:15]
                sync_env['ADMIN_SYNC'] = json.dumps({'id': record_id, 'email': email, 'tokenKey': native_record[0], 'passwordHash': native_record[1]})
            base, process = launch(folder, native, sync_env)
            if native:
                request(base, auth_url, method='POST', body={'identity': email, 'password': password}, expected=401)
            login = request(base, auth_url, method='POST', body={'identity': email, 'password': password, 'totpCode': codes[1]})
            token = login['token']
            if native:
                assert login['record']['id'] == record_id and request(base, api_status, token)['enabled']
                report['checks']['console_totp_survives_owner_id_change_on_transfer'] = True
            # A future step within the permitted clock skew exercises the authenticator, independently of recovery codes.
            authenticator_code = totp(setup['secret'], 1)
            token = request(base, auth_url, method='POST', body={'identity': email, 'password': password, 'totpCode': authenticator_code})['token']
            request(base, auth_url, method='POST', body={'identity': email, 'password': password, 'totpCode': authenticator_code}, expected=401)
            renewed = request(base, api + '/recovery', token, 'POST', {'password': password, 'code': codes[2]})['recoveryCodes']
            request(base, auth_url, method='POST', body={'identity': email, 'password': password, 'totpCode': codes[3]}, expected=401)
            token = request(base, auth_url, method='POST', body={'identity': email, 'password': password, 'totpCode': renewed[0]})['token']
            for _ in range(5):
                request(base, auth_url, method='POST', body={'identity': email, 'password': password, 'totpCode': 'invalid-code'}, expected=401)
            request(base, auth_url, method='POST', body={'identity': email, 'password': password, 'totpCode': renewed[1]}, expected=429)
            root_token = token if native else admin['token']
            states = request(base, '/api/collections/ph_two_factor/records', root_token)['items']
            request(base, '/api/collections/ph_two_factor/records/' + states[0]['id'], root_token, 'PATCH', {'failures': 0, 'lockedUntil': ''})
            disabled = request(base, api + '/disable', token, 'POST', {'password': password, 'code': renewed[2]})
            assert request(base, api_status, disabled['token'])['enabled'] is False
            login = request(base, auth_url, method='POST', body={'identity': email, 'password': password})
            assert login['token']
            report['checks'][realm + '_restart_authenticator_replay_and_lockout'] = True
            report['checks'][realm + '_recovery_rotation_and_disable'] = True
            fixtures.append({'base': base, 'realm': realm, 'email': email, 'password': password})
        report['result'] = 'passed'
        (root / 'browser-fixture.json').write_text(json.dumps(fixtures))
    except Exception as error:
        report['result'] = 'failed'
        report['error'] = str(error)
        raise
    finally:
        if not (args.keep_running and report.get('result') == 'passed'):
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
        for log in logs:
            log.close()
        (root / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
