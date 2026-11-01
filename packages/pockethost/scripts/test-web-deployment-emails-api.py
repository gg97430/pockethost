#!/usr/bin/env python3
"""Native PB + local SMTP integration checks. Uses only a disposable loopback dataset, never a live controller."""
import argparse
import datetime
import email.policy
from email.parser import BytesParser
import json
import os
import pathlib
import secrets
import shutil
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True, type=pathlib.Path)
parser.add_argument('--pocketbase', required=True)
parser.add_argument('--bundle', type=pathlib.Path)
args = parser.parse_args()
os.umask(0o077)
root = pathlib.Path(tempfile.mkdtemp(prefix='ph-deployment-emails-')).resolve()
process = None
completed = False
checks = []
messages = []
rejections = [0]
lock = threading.Lock()


class SMTP(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b'220 localhost test SMTP\r\n')
        recipients = []
        while True:
            line = self.rfile.readline(1048576)
            if not line:
                return
            command = line.decode('ascii', 'replace').strip()
            verb = command.split(' ', 1)[0].upper()
            if verb in ('EHLO', 'HELO'):
                self.wfile.write(b'250-localhost\r\n250 SIZE 4000000\r\n')
            elif verb == 'MAIL':
                recipients = []
                self.wfile.write(b'250 OK\r\n')
            elif verb == 'RCPT':
                recipients.append(command.split(':', 1)[1].strip().strip('<>'))
                self.wfile.write(b'250 OK\r\n')
            elif verb == 'DATA':
                self.wfile.write(b'354 End with dot\r\n')
                content = bytearray()
                while True:
                    part = self.rfile.readline(4000000)
                    if part in (b'.\r\n', b'.\n', b''):
                        break
                    content.extend(part[1:] if part.startswith(b'..') else part)
                    if len(content) > 4000000:
                        raise AssertionError('Unbounded SMTP message')
                with lock:
                    reject = rejections[0] > 0
                    if reject:
                        rejections[0] -= 1
                    else:
                        messages.append((recipients, BytesParser(policy=email.policy.default).parsebytes(bytes(content))))
                self.wfile.write(b'451 Temporary failure\r\n' if reject else b'250 Queued\r\n')
            elif verb == 'QUIT':
                self.wfile.write(b'221 Bye\r\n')
                return
            elif verb in ('RSET', 'NOOP'):
                self.wfile.write(b'250 OK\r\n')
            else:
                self.wfile.write(b'502 Unsupported\r\n')


class LocalSMTP(socketserver.ThreadingTCPServer):
    daemon_threads = True


smtp = LocalSMTP(('127.0.0.1', 0), SMTP)
threading.Thread(target=smtp.serve_forever, daemon=True).start()
with socket.socket() as server:
    server.bind(('127.0.0.1', 0))
    port = server.getsockname()[1]
base = f'http://127.0.0.1:{port}'
secret = secrets.token_urlsafe(40)


def request(path, token='', body=None, method=None, status=200, worker=False):
    call = urllib.request.Request(base + path, method=method or ('POST' if body is not None else 'GET'),
        headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {}),
                 **({'X-Web-Worker': secret} if worker else {})},
        data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(call, timeout=20) as response:
            data = response.read()
            assert response.status == status, (path, response.status, status)
            return json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        if error.code == status:
            return json.loads(error.read())
        raise AssertionError(f'{path}: {error.code} ' + error.read().decode())


def worker(endpoint, body=None, status=200):
    return request('/api/web-apps/_notifications/' + endpoint, body=body or {}, status=status, worker=True)


def now(delta=0):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=delta)).isoformat()


def attachment(message):
    parts = list(message.iter_attachments())
    assert len(parts) == 1, [part.get_content_type() for part in message.walk()]
    part = parts[0]
    filename = part.get_filename()
    assert filename and filename.endswith('.log') and filename.isascii() and '/' not in filename
    data = part.get_payload(decode=True)
    assert len(data) <= 1048576, len(data)
    return data.decode('utf-8')


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks, migrations = root / 'hooks', root / 'migrations'
    hooks.mkdir()
    migrations.mkdir()
    shutil.copy(args.bundle or source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    for migration in (source / 'pb_migrations').glob('*.js'):
        if migration.name.split('_')[0].isdigit() and int(migration.name.split('_')[0]) <= 1793271900:
            shutil.copy(migration, migrations / migration.name)
    routes = [('GET', '/api/web-platform-notifications', 'HandleWebPlatformNotificationsGet', True),
              ('PUT', '/api/web-platform-notifications', 'HandleWebPlatformNotificationsPut', True),
              ('POST', '/api/web-platform-notifications/test', 'HandleWebPlatformNotificationTest', True),
              ('GET', '/api/web-apps/{id}/notifications', 'HandleWebNotificationsGet', True),
              ('PUT', '/api/web-apps/{id}/notifications', 'HandleWebNotificationsPut', True),
              ('POST', '/api/web-apps/{id}/notifications/test', 'HandleWebNotificationTest', True),
              ('POST', '/api/web-apps/_notifications/claim', 'HandleWebNotificationClaim', False),
              ('POST', '/api/web-apps/_notifications/email', 'HandleWebNotificationEmail', False),
              ('POST', '/api/web-apps/_notifications/report', 'HandleWebNotificationReport', False)]
    fixture = hooks / 'email-tests.pb.js'
    fixture.write_text('\n'.join('routerAdd(' + json.dumps(method) + ',' + json.dumps(path) +
        ',e=>require(`${__hooks}/mothership`).' + handler + '(e)' +
        (',$apis.requireAuth("users")' if auth else '') + ');' for method, path, handler, auth in routes))
    with fixture.open('a') as output:
        output.write("""
routerAdd('POST','/test/failure',e=>{
 if(e.auth?.collection().name!=='_superusers')throw new ForbiddenError();
 const b=e.requestInfo().body,m=require(`${__hooks}/mothership`),a=$app.findRecordById('web_apps',b.appId);
 if(b.env!==undefined){a.set('envEncrypted',m.encryptWebValue(b.env));$app.save(a)}
 const j=new Record($app.findCollectionByNameOrId('web_deployments'));
 j.set('appId',a.id);j.set('action',b.action||'deploy');j.set('status',b.status||'failed');j.set('commit','c'.repeat(40));
 j.set('stage','Construction');j.set('error',b.error||'La construction a échoué.');
 j.set('snapshotEncrypted',b.invalidCipher?'invalid':m.encryptWebValue(b.snapshot||{config:{repository:'frozen/repository',branch:'frozen-branch'},env:{},runtime:{kind:'nextjs'}}));
 j.set('finishedAt',b.finishedAt||new Date().toISOString());$app.save(j);return e.json(200,{id:j.id})
},$apis.requireAuth('_superusers'));
""")
    env = {**os.environ, 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_WORKER_SECRET': secret,
           'WEB_APPS_MASTER_KEY': 'e' * 32, 'WEB_APPS_ROOT': str(root / 'web'),
           'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid',
           'APP_URL': 'https://dashboard.example.invalid'}
    password, address = 'P' + secrets.token_urlsafe(32), 'root@example.invalid'
    data = root / 'pb_data'
    subprocess.run([args.pocketbase, 'superuser', 'upsert', address, password, '--dir', str(data),
        '--hooksDir', str(root / 'empty'), '--migrationsDir', str(migrations)], env=env, check=True, capture_output=True)
    log = (root / 'process.log').open('wb')
    process = subprocess.Popen([args.pocketbase, 'serve', '--dir', str(data), '--hooksDir', str(hooks),
        '--migrationsDir', str(migrations), '--http', f'127.0.0.1:{port}'], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError((root / 'process.log').read_text()[-3000:])
        try:
            request('/api/health')
            break
        except OSError:
            time.sleep(.1)
    su = request('/api/collections/_superusers/auth-with-password', body={'identity': address, 'password': password})['token']
    settings = request('/api/settings', su)
    request('/api/settings', su, {'smtp': {**settings['smtp'], 'enabled': True, 'host': '127.0.0.1',
        'port': smtp.server_address[1], 'tls': False, 'username': '', 'password': ''},
        'meta': {**settings['meta'], 'senderAddress': 'alerts@example.invalid', 'senderName': 'Kolab Hosting'}}, 'PATCH')

    def user(name, admin=True):
        row = request('/api/collections/users/records', su, {'email': name + '@example.invalid', 'username': name,
            'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': admin,
            'subscription': 'premium', 'subscription_quantity': 1})
        return row, request('/api/collections/users/auth-with-password', body={'identity': row['email'], 'password': password})['token']

    actor, token = user('operator')
    second, second_token = user('operator2')
    _, plain = user('plain', False)

    def app(name):
        return request('/api/collections/web_apps/records', su, {'name': name, 'uid': actor['id'], 'status': 'stopped',
            'config': {'repository': 'pending/configuration', 'branch': 'pending-branch', 'generatedDomain': 'web-email.example.invalid'}})

    site, other = app('Email fixture'), app('Other app')
    (root / 'web' / 'logs').mkdir(parents=True)

    def failure(application=site, **values):
        time.sleep(.003)
        return request('/test/failure', su, {'appId': application['id'], **values})['id']

    def patch(collection, ident, values):
        return request('/api/collections/' + collection + '/records/' + ident, su, values, 'PATCH')

    def claim():
        return worker('claim')['job']

    def send(job, expected=None):
        before = len(messages)
        worker('email', {'id': job['id'], 'proof': job['proof']})
        assert len(messages) == before + 1
        if expected is not None:
            assert set(messages[-1][0]) == set(expected), messages[-1][0]
        worker('report', {'id': job['id'], 'proof': job['proof'], 'success': True, 'httpStatus': 0})
        return messages[-1][1]

    historical_success = failure(status='ready', finishedAt=now(-86400))
    historical = failure(finishedAt=now(-86400))
    (root / 'web' / 'logs' / (historical + '.log')).write_text('HISTORY-MUST-NOT-BE-SENT\n')
    request('/api/web-platform-notifications', plain, status=403)
    platform = request('/api/web-platform-notifications', token)
    recipients = [actor['email'], second['email']]
    request('/api/web-platform-notifications', token, {'revision': platform['revision'],
        'email': {'enabled': True, 'recipients': recipients}, 'events': ['app_deployment_failed'],
        'copyAppEvents': True}, 'PUT')
    assert claim() is None and not messages
    checks.append('platform_activation_ignores_historical_failures_and_nonoperators')
    success_before_opt_in = failure(status='ready')
    platform = request('/api/web-platform-notifications', token)
    request('/api/web-platform-notifications', token, {'revision': platform['revision'],
        'events': ['app_deployment_failed', 'app_deployment_succeeded']}, 'PUT')
    assert claim() is None and not messages
    checks.append('enabling_success_notifications_never_replays_previous_successes')


    frozen_env = {'OLD_SECRET': {'value': 'snapshot-secret-value', 'build': True, 'runtime': True},
        'PRIVATE_KEY': {'value': '-----BEGIN KEY-----\nlong-private-key-line-123456789\n-----END KEY-----', 'build': True},
        'NODE_ENV': {'value': 'production'}, 'PGSSLMODE': {'value': 'require'}, 'PORT': {'value': '3000'}}
    current_env = {'NEW_SECRET': {'value': 'current-secret-value', 'runtime': True}}
    ident = failure(snapshot={'config': {'repository': 'frozen/repository', 'branch': 'frozen-branch'},
        'env': frozen_env, 'runtime': {'kind': 'nextjs'}}, env=current_env,
        error='snapshot-secret-value current-secret-value')
    content = ('\x1b[31mÉchec UTF-8\x1b[0m\n#20 long-private-key-line-123456789\n'
        'snapshot-secret-value current-secret-value\nproduction require 3000\nhttps://user:pass@example.invalid/path\n')
    (root / 'web' / 'logs' / (ident + '.log')).write_text(content)
    job = claim()
    assert job and job['payload']['details']['deploymentId'] == ident and job['payload']['application']['id'] == site['id']
    assert claim() is None
    before = len(messages)
    worker('email', {'id': job['id'], 'proof': job['proof']})
    worker('email', {'id': job['id'], 'proof': job['proof']})
    assert len(messages) == before + 1
    assert set(messages[-1][0]) == set(recipients)
    message = messages[-1][1]
    text = attachment(message)
    body = message.get_body(preferencelist=('plain',)).get_content()
    assert 'Échec UTF-8' in text and '\x1b' not in text and 'production require 3000' in text
    for value in ['snapshot-secret-value', 'current-secret-value', 'long-private-key-line-123456789', 'user:pass']:
        assert value not in text and value not in body
    assert '[masqué]' in text and 'frozen/repository' in body and 'frozen-branch' in body and 'c' * 40 in body
    assert 'pending/configuration' not in body and 'https://dashboard.example.invalid/applications/' + site['id'] in body
    worker('report', {'id': job['id'], 'proof': job['proof'], 'success': True, 'httpStatus': 0})
    worker('email', {'id': job['id'], 'proof': job['proof']}, 401)
    assert claim() is None
    checks.append('global_scan_without_app_settings_utf8_mime_attachment_snapshot_current_multiline_masks_and_double_send_idempotency')

    # Publication (ready), never a build in progress; successes have no attached build log.
    building = failure(status='building')
    assert claim() is None
    for action in ['deploy', 'ci-deploy']:
        success_id = failure(status='ready', action=action)
        queued = claim()
        assert queued and queued['payload']['event'] == 'app_deployment_succeeded'
        assert queued['payload']['details']['deploymentId'] == success_id
        message = send(queued, recipients)
        assert not list(message.iter_attachments())
        body = message.get_body(preferencelist=('plain',)).get_content()
        assert 'réussi' in str(message['Subject']) and success_id in body
        assert 'frozen/repository' in body and 'frozen-branch' in body and 'c' * 40 in body
        assert claim() is None
    # Starting an existing image or issuing a certificate isn't a new source deployment.
    failure(status='ready', action='start')
    failure(status='ready', action='domain')
    assert claim() is None
    checks.append('success_only_after_publication_manual_and_ci_metadata_no_attachment_and_no_cooldown_loss')

    for mode in ['missing', 'symlink', 'fifo', 'oversize', 'invalid-cipher']:
        ident = failure(invalidCipher=mode == 'invalid-cipher')
        path = root / 'web' / 'logs' / (ident + '.log')
        if mode == 'symlink':
            target = root / 'forbidden.log'
            target.write_text('FORBIDDEN-TARGET-CONTENT\n')
            path.symlink_to(target)
        elif mode == 'fifo':
            os.mkfifo(path)
        elif mode == 'oversize':
            path.write_text('PARTIAL-SENSITIVE-PREFIX' + 'x' * 1048576 + '\nfinal-error-line\n')
        elif mode == 'invalid-cipher':
            path.write_text('MUST-NOT-RELEASE-WITHOUT-DECRYPTION\n')
        text = attachment(send(claim(), recipients))
        if mode == 'oversize':
            assert 'tronqué' in text and 'final-error-line' in text and 'PARTIAL-SENSITIVE-PREFIX' not in text
        else:
            assert 'Journal indisponible' in text and 'FORBIDDEN' not in text and 'MUST-NOT-RELEASE' not in text
    checks.append('missing_symlink_fifo_oversize_and_invalid_encryption_fail_closed_without_losing_alert')

    # The same short literal is public configuration AND a password: the password always wins.
    ident = failure(snapshot={'config': {}, 'runtime': {'kind': 'nextjs'}, 'env': {
        'PASSWORD': {'value': 'require'}, 'PGSSLMODE': {'value': 'require'}, 'PIN_SECRET': {'value': 'x'}}})
    (root / 'web' / 'logs' / (ident + '.log')).write_text('require\n' + 'x\n' * 300000 + 'last-error\n')
    text = attachment(send(claim(), recipients))
    assert 'require' not in text and 'x' not in text and '[masqué]\n' in text and '[masqu[masqué]' not in text
    assert 'last-error' in text and len(text.encode()) <= 1048576 and 'tronqué' in text
    checks.append('short_secret_public_collision_one_pass_masking_and_post_mask_byte_bound')

    foreign = failure(other)
    (root / 'web' / 'logs' / (foreign + '.log')).write_text('FOREIGN-LOG-CONTENT\n')
    foreign_job = claim()
    send(foreign_job, recipients)
    own = failure()
    own_job = claim()
    record = request('/api/collections/web_notification_deliveries/records/' + own_job['id'], su)
    forged = record['payload']
    forged['details']['deploymentId'] = foreign
    patch('web_notification_deliveries', own_job['id'], {'payload': forged})
    assert 'FOREIGN' not in attachment(send(own_job, recipients))
    checks.append('foreign_deployment_id_cannot_disclose_another_application_log')

    for _ in range(14):
        failure(action='ci-deploy' if _ % 2 else 'deploy')
    sent_before = len(messages)
    for _ in range(14):
        send(claim(), recipients)
    assert len(messages) == sent_before + 14 and claim() is None
    checks.append('one_email_per_push_manual_ci_failure_without_five_minute_or_hourly_suppression')

    ident = failure()
    job = claim()
    with lock:
        rejections[0] = 3
    for attempt in range(1, 4):
        worker('email', {'id': job['id'], 'proof': job['proof']}, 400)
        worker('report', {'id': job['id'], 'proof': job['proof'], 'success': False, 'httpStatus': 0, 'errorClass': 'smtp'})
        row = request('/api/collections/web_notification_deliveries/records/' + job['id'], su)
        assert row['attempts'] == attempt and row['status'] == ('failed' if attempt == 3 else 'pending')
        if attempt < 3:
            patch('web_notification_deliveries', job['id'], {'nextAttemptAt': now(-1)})
            job = claim()
    assert claim() is None
    checks.append('three_smtp_attempts_with_existing_backoff_and_leases')

    # Configured app channels still dedupe the optional platform copy against the global scan.
    app_alert = '/api/web-apps/' + site['id'] + '/notifications'
    current = request(app_alert, token)
    request(app_alert, token, {'revision': current['revision'], 'email': {'enabled': True, 'recipients': recipients},
        'events': ['deployment_failed', 'deployment_succeeded']}, 'PUT')
    ident = failure()
    first, second_job = claim(), claim()
    assert first and second_job and first['scope'] != second_job['scope'] and claim() is None
    # Removing app-level recipient rights after enqueue must also remove the address from delivery.
    patch('users', second['id'], {'superAdmin': False})
    for queued in [first, second_job]:
        send(queued, [actor['email']] if queued['scope'] == 'app' else recipients)
    checks.append('app_platform_deduplication_and_app_recipient_rights_revalidated_at_send')

    success_id = failure(status='ready')
    # App scans run at most every 15 seconds; advance only the disposable fixture's scan schedule.
    for settings_row in request('/api/collections/web_notification_settings/records', su)['items']:
        patch('web_notification_settings', settings_row['id'], {'scanAfter': now(-16)})
    first, second_job = claim(), claim()
    assert first and second_job and first['scope'] != second_job['scope'] and claim() is None
    for queued in [first, second_job]:
        message = send(queued, [actor['email']] if queued['scope'] == 'app' else recipients)
        assert not list(message.iter_attachments()) and 'réussi' in str(message['Subject'])
    checks.append('success_app_channel_and_platform_scan_share_one_copy_key')

    before = len(messages)
    test = request(app_alert + '/test', token, {'channel': 'email', 'event': 'deployment_failed'})
    assert test['done'] and len(messages) == before + 1 and 'Journal fictif' in attachment(messages[-1][1])
    assert 'FOREIGN-LOG' not in attachment(messages[-1][1])
    checks.append('manual_synthetic_test_never_opens_a_real_deployment_log')

    # No notifications from orphaned/deleting/suspended applications.
    for state in ['deleting', 'suspended']:
        excluded = app('Excluded ' + state)
        patch('web_apps', excluded['id'], {'status': state})
        failure(excluded)
        failure(excluded, status='ready')
    assert claim() is None
    checks.append('deleting_and_suspended_applications_ignored')
    completed = True
    print(json.dumps({'ok': True, 'checks': checks, 'messages': len(messages)}, indent=2))
finally:
    if process:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    smtp.shutdown()
    smtp.server_close()
    if not completed:
        print('Disposable test dataset:', root)
        if (root / 'process.log').exists():
            print((root / 'process.log').read_text()[-4000:])
    else:
        shutil.rmtree(root)
