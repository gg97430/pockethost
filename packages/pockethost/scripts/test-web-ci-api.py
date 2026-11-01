#!/usr/bin/env python3
"""Disposable native PocketBase: scoped CI tokens, idempotence and ephemeral credentials."""
import argparse
import concurrent.futures
import hashlib
import json
import os
import pathlib
import secrets
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True, type=pathlib.Path)
parser.add_argument('--pocketbase', required=True)
parser.add_argument('--bundle', type=pathlib.Path)
args = parser.parse_args()
os.umask(0o077)
temporary = tempfile.TemporaryDirectory(prefix='ph-web-ci-api-')
root = pathlib.Path(temporary.name).resolve()
process = None
checks = []
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
origin = f'http://127.0.0.1:{port}'
worker_secret = secrets.token_urlsafe(40)


def request(path, token='', body=None, method=None, status=200, private=False):
    headers = {'Content-Type': 'application/json', **({'Authorization': token} if token else {}), **({'X-Web-Worker': worker_secret} if private else {})}
    call = urllib.request.Request(origin + path, method=method or ('POST' if body is not None else 'GET'), headers=headers, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(call, timeout=30) as response:
            raw = response.read()
            assert response.status == status, (path, response.status, status)
            if path.startswith('/api/web-apps'):
                assert response.headers.get('Cache-Control') == 'no-store', path
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        if error.code == status:
            return json.loads(raw)
        raise AssertionError(f'{path}: {error.code}: ' + raw.decode()) from None


def worker(endpoint, body, status=200):
    return request('/api/web-apps/_worker/' + endpoint, body=body, private=True, status=status)


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks = root / 'hooks'; hooks.mkdir()
    shutil.copy(args.bundle or source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    routes = [
        ('GET', '/api/web-apps/{id}/deploy-tokens', 'HandleWebCiTokensList', True),
        ('POST', '/api/web-apps/{id}/deploy-tokens', 'HandleWebCiTokensCreate', True),
        ('DELETE', '/api/web-apps/{id}/deploy-tokens/{tokenId}', 'HandleWebCiTokenRevoke', True),
        ('POST', '/api/web-apps/ci/deploy', 'HandleWebCiDeploy', False),
        ('GET', '/api/web-apps/{id}/runtime/profile', 'HandleWebRuntimeGet', True),
        ('PATCH', '/api/web-apps/{id}/runtime/profile', 'HandleWebRuntimeUpdate', True),
        ('POST', '/api/web-apps/{id}/actions/{action}', 'HandleWebAppAction', True),
        ('GET', '/api/web-apps/{id}/deployments', 'HandleWebDeployments', True),
        ('POST', '/api/web-apps/{id}/deployments/{deploymentId}/cancel', 'HandleWebDeploymentCancel', True),
        ('POST', '/api/web-apps/_worker/claim', 'HandleWebWorkerClaim', False),
        ('POST', '/api/web-apps/_worker/report', 'HandleWebWorkerReport', False),
    ]
    script = '\n'.join('routerAdd(' + json.dumps(method) + ',' + json.dumps(path) + ',e=>require(`${__hooks}/mothership`).' + handler + '(e)' + (',$apis.requireAuth("users")' if auth else '') + ');' for method, path, handler, auth in routes)
    # Test-only, loopback server, authenticated administrator: inspect/deform encrypted snapshots.
    script += """
routerAdd('POST','/test/job',e=>{const m=require(`${__hooks}/mothership`);m.requirePlatformAdmin(e);const i=e.requestInfo().body,r=$app.findRecordById('web_deployments',i.id),s=m.decryptWebValue(r.getString('snapshotEncrypted'));if(i.action==='expire'){s.ci.expiresAt='2000-01-01T00:00:00.000Z';r.set('snapshotEncrypted',m.encryptWebValue(s));$app.save(r)}if(i.action==='stale'){r.set('heartbeatAt','2000-01-01T00:00:00.000Z');$app.save(r)}return e.json(200,{id:r.id,appId:r.getString('appId'),status:r.getString('status'),snapshot:m.decryptWebValue(r.getString('snapshotEncrypted'))})},$apis.requireAuth('users'));
routerAdd('POST','/test/maintenance',e=>{const m=require(`${__hooks}/mothership`);m.requirePlatformAdmin(e);m.HandleWebCiMaintenance();return e.json(200,{ok:true})},$apis.requireAuth('users'));
"""
    (hooks / 'test.pb.js').write_text(script)
    env = {**os.environ, 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_MASTER_KEY': 'i' * 32, 'WEB_APPS_WORKER_SECRET': worker_secret, 'WEB_APPS_ROOT': str(root / 'web'), 'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid'}
    data = root / 'pb_data'
    password, email = 'P' + secrets.token_urlsafe(32), 'ci@example.invalid'
    subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(data), '--hooksDir', str(root / 'empty'), '--migrationsDir', str(source / 'pb_migrations')], env=env, check=True, capture_output=True)
    log = (root / 'process.log').open('wb')
    process = subprocess.Popen([args.pocketbase, 'serve', '--dir', str(data), '--hooksDir', str(hooks), '--migrationsDir', str(source / 'pb_migrations'), '--http', f'127.0.0.1:{port}'], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        if process.poll() is not None: raise RuntimeError('PocketBase startup failed')
        try: request('/api/health'); break
        except OSError: time.sleep(.1)
    su = request('/api/collections/_superusers/auth-with-password', body={'identity': email, 'password': password})['token']
    def user(name, admin):
        row = request('/api/collections/users/records', su, {'email': name + '@example.invalid', 'username': name, 'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': admin, 'subscription': 'premium', 'subscription_quantity': 1})
        return row, request('/api/collections/users/auth-with-password', body={'identity': row['email'], 'password': password})['token']
    actor, token = user('ciadmin', True); _, plain = user('ciplain', False)
    config = {'repository': '', 'branch': 'main', 'rootDirectory': '.', 'healthPath': '/', 'generatedDomain': 'ci.example.invalid'}
    def app(name):
        return request('/api/collections/web_apps/records', su, {'name': name, 'uid': actor['id'], 'status': 'stopped', 'config': config})
    first, second = app('CI fixture'), app('CI isolated fixture')
    ident, other_id = first['id'], second['id']
    base = '/api/web-apps/' + ident; tokens = base + '/deploy-tokens'; runtime = base + '/runtime/profile'
    image_repo = 'ghcr.io/owner/ci-fixture'; initial_image = image_repo + '@sha256:' + 'a' * 64
    request(runtime, token, {'profile': {'kind': 'image', 'image': initial_image}, 'revision': ''}, method='PATCH')
    request(tokens, status=401); request(tokens, plain, status=404)
    request(tokens, body={}, status=401); request(tokens, plain, {}, status=404)
    for collection in ['web_ci_tokens', 'web_ci_deliveries']:
        request('/api/collections/' + collection + '/records', token, status=403)
        request('/api/collections/' + collection + '/records', plain, {}, status=403)
    request('/api/web-apps/ci/deploy', body={}, status=401)
    request('/api/web-apps/ci/deploy', 'Bearer phci.invalid.invalid', {}, status=401)
    token_body = {'name': 'GitHub Actions fixture', 'imageRepository': image_repo, 'expiresInDays': 7}
    invalid_tokens = [{**token_body, 'expiresInDays': value} for value in [0, 91, 1.5, '7']] + [{**token_body, 'name': ''}, {**token_body, 'name': 'x' * 81}, {**token_body, 'imageRepository': 'owner/image'}, {**token_body, 'imageRepository': 'ghcr.io/owner/../image'}, {**token_body, 'unknown': True}]
    for invalid in invalid_tokens: request(tokens, token, invalid, status=400)
    issued = request(tokens, token, token_body, status=201); bearer = 'Bearer ' + issued['token']; token_id = issued['item']['id']; raw_secret = issued['token'].split('.')[-1]
    with sqlite3.connect(data / 'data.db') as db:
        row = db.execute('SELECT appId,imageRepository,tokenHash FROM web_ci_tokens WHERE id=?', (token_id,)).fetchone()
        assert row == (ident, image_repo, hashlib.sha256(raw_secret.encode()).hexdigest())
        assert raw_secret not in (data / 'data.db').read_bytes().decode(errors='ignore')
    assert issued['deployUrl'] == 'https://app.example.invalid/api/web-apps/ci/deploy'
    listed = request(tokens, token); assert raw_secret not in json.dumps(listed) and 'tokenHash' not in json.dumps(listed)
    request('/api/web-apps/' + other_id + '/deploy-tokens/' + token_id, token, method='DELETE', status=404)
    checks.append('admin_only_locked_tables_hashed_token_exact_app_repository_scope_and_validation')
    payload = {'image': image_repo + '@sha256:' + 'b' * 64, 'commit': 'b' * 40, 'branch': 'main', 'requestId': 'workflow:1'}
    registry = {'server': 'ghcr.io', 'username': 'fixture-actor', 'password': 'ephemeral-registry-private-value'}
    invalid_payloads = [
        {**payload, 'image': image_repo + ':latest'}, {**payload, 'image': 'ghcr.io/owner/other@sha256:' + 'b' * 64},
        {**payload, 'image': payload['image'] + '0'}, {**payload, 'image': payload['image'].upper()},
        {**payload, 'commit': 'b' * 39}, {**payload, 'requestId': ''}, {**payload, 'requestId': 'x' * 101},
        {**payload, 'requestId': 'bad/request'}, {**payload, 'branch': 'other'}, {**payload, 'appId': other_id},
        {**payload, 'registry': {**registry, 'server': 'ghcr.io.evil.invalid'}},
        {**payload, 'registry': {**registry, 'password': 'short'}}, {**payload, 'registry': {**registry, 'username': 'bad\nuser'}},
        {**payload, 'registry': {**registry, 'unknown': True}}, {**payload, 'registry': None},
    ]
    for invalid in invalid_payloads: request('/api/web-apps/ci/deploy', bearer, invalid, status=400)
    request('/api/collections/web_ci_tokens/records/' + token_id, su, {'expiresAt': '2000-01-01T00:00:00.000Z'}, method='PATCH')
    request('/api/web-apps/ci/deploy', bearer, payload, status=401)
    request('/api/collections/web_ci_tokens/records/' + token_id, su, {'expiresAt': '2099-01-01T00:00:00.000Z'}, method='PATCH')
    request('/api/collections/users/records/' + actor['id'], su, {'superAdmin': False}, method='PATCH')
    request('/api/web-apps/ci/deploy', bearer, payload, status=401)
    request('/api/collections/users/records/' + actor['id'], su, {'superAdmin': True}, method='PATCH')
    revoked = request(tokens, token, token_body, status=201)
    request(tokens + '/' + revoked['item']['id'], token, method='DELETE')
    request('/api/web-apps/ci/deploy', 'Bearer ' + revoked['token'], payload, status=401)
    checks.append('digest_only_branch_exact_registry_host_expiration_revocation_and_actor_revalidation')
    body = {**payload, 'registry': registry}
    def submit(value=body): return request('/api/web-apps/ci/deploy', bearer, value, status=202)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        submissions = list(pool.map(lambda _: submit(), range(6)))
    job_id = submissions[0]['deployment']['id']
    assert len({item['deployment']['id'] for item in submissions}) == 1 and sum(not item['duplicate'] for item in submissions) == 1
    assert registry['password'] not in json.dumps(submissions) and raw_secret not in json.dumps(submissions)
    with sqlite3.connect(data / 'data.db') as db:
        assert db.execute('SELECT COUNT(*) FROM web_ci_deliveries WHERE tokenId=? AND requestId=?', (token_id, payload['requestId'])).fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM web_deployments WHERE appId=?', (ident,)).fetchone()[0] == 1
        encrypted = db.execute('SELECT snapshotEncrypted FROM web_deployments WHERE id=?', (job_id,)).fetchone()[0]
        assert registry['password'] not in encrypted
    request('/api/web-apps/ci/deploy', bearer, {**body, 'image': image_repo + '@sha256:' + 'c' * 64}, status=400)
    waiting = submit(); assert waiting['duplicate'] and waiting['deployment']['id'] == job_id
    claim = worker('claim', {})['job']; assert claim['id'] == job_id and claim['appId'] == ident
    assert claim['snapshot']['runtime']['registry'] == registry and claim['snapshot']['runtime']['image'] == payload['image']
    assert submit()['deployment']['status'] == 'running'
    worker('report', {'id': job_id, 'stageKey': 'publish', 'stage': 'Publication vérifiée'})
    worker('report', {'id': job_id, 'status': 'ready', 'result': {'image': 'ph-web/' + ident + ':' + job_id, 'container': 'ph-web-' + ident + '-' + job_id, 'target': 'http://127.0.0.1:39999'}})
    published = request(runtime, token); assert published['profile']['image'] == payload['image'] and not published['registry']['hasPassword']
    completed = submit(); assert completed['duplicate'] and completed['deployment']['status'] == 'ready'
    stored = request('/test/job', token, {'id': job_id}); assert 'registry' not in stored['snapshot']['runtime'] and stored['snapshot']['ci']['ephemeralRegistry'] is False
    assert registry['password'] not in json.dumps(request(base + '/deployments', token))
    manual = request(base + '/actions/deploy', token, {}, status=202)['deployment']
    manual_claim = worker('claim', {})['job']; assert manual_claim['id'] == manual['id'] and manual_claim['snapshot']['runtime']['image'] == payload['image'] and 'registry' not in manual_claim['snapshot']['runtime']
    worker('report', {'id': manual['id'], 'status': 'failed', 'error': 'Fixture complete'})
    checks.append('six_concurrent_requests_one_job_poll_idempotence_worker_decryption_ready_scrub_and_next_manual_image')
    def next_body(number): return {**body, 'requestId': 'workflow:' + str(number), 'image': image_repo + '@sha256:' + format(number, '064x')}
    failed = submit(next_body(2))['deployment']; worker('claim', {})
    worker('report', {'id': failed['id'], 'status': 'failed', 'error': 'Fixture failure'})
    assert 'registry' not in request('/test/job', token, {'id': failed['id']})['snapshot']['runtime']
    cancelled = submit(next_body(3))['deployment']; worker('claim', {})
    worker('report', {'id': cancelled['id'], 'status': 'cancelled'})
    assert 'registry' not in request('/test/job', token, {'id': cancelled['id']})['snapshot']['runtime']
    stale = submit(next_body(4))['deployment']; worker('claim', {})
    request('/test/job', token, {'id': stale['id'], 'action': 'stale'}); assert worker('claim', {})['job'] is None
    stale_record = request('/test/job', token, {'id': stale['id']}); assert stale_record['status'] == 'failed' and 'registry' not in stale_record['snapshot']['runtime']
    expired = submit(next_body(5))['deployment']; request('/test/job', token, {'id': expired['id'], 'action': 'expire'})
    assert worker('claim', {})['job'] is None
    expired_record = request('/test/job', token, {'id': expired['id']}); assert expired_record['status'] == 'failed' and 'registry' not in expired_record['snapshot']['runtime']
    checks.append('failed_cancelled_stale_and_expired_claim_scrub_ephemeral_credentials')
    superseded = submit(next_body(6))['deployment']; newer = submit(next_body(7))['deployment']
    replaced = request('/test/job', token, {'id': superseded['id']}); assert replaced['status'] == 'cancelled' and 'registry' not in replaced['snapshot']['runtime']
    # A manual deploy supersedes a queued CI job and must clear its ephemeral secret too.
    maintenance = request('/api/collections/web_deployments/records', su, {'appId': ident, 'action': 'domain', 'status': 'queued', 'stage': 'Fixture maintenance'}, status=200)
    manual = request(base + '/actions/deploy', token, {}, status=202)['deployment']
    assert request('/api/collections/web_deployments/records/' + maintenance['id'], su)['status'] == 'queued'
    request('/api/collections/web_deployments/records/' + maintenance['id'], su, method='DELETE', status=204)
    replaced = request('/test/job', token, {'id': newer['id']}); assert replaced['status'] == 'cancelled' and 'registry' not in replaced['snapshot']['runtime']
    worker('claim', {}); worker('report', {'id': manual['id'], 'status': 'failed', 'error': 'Fixture complete'})
    queued_cancel = submit(next_body(8))['deployment']; assert queued_cancel['cancellable']
    cancelled_reply = request(base + '/deployments/' + queued_cancel['id'] + '/cancel', token, {}, status=202)
    assert cancelled_reply['deployment']['status'] == 'cancelled'
    assert 'registry' not in request('/test/job', token, {'id': queued_cancel['id']})['snapshot']['runtime']
    running_cancel = submit(next_body(9))['deployment']; worker('claim', {})
    request(base + '/deployments/' + running_cancel['id'] + '/cancel', token, {}, status=202)
    worker('report', {'id': running_cancel['id'], 'status': 'ready', 'result': {}}, status=400)
    worker('report', {'id': running_cancel['id'], 'status': 'failed', 'error': 'Cancellation acknowledged'})
    cancelled_record = request('/test/job', token, {'id': running_cancel['id']})
    assert cancelled_record['status'] == 'cancelled' and 'registry' not in cancelled_record['snapshot']['runtime']
    checks.append('queued_ci_manual_supersession_and_dashboard_cancel_scrub_immediately')
    for index in range(9):
        request(tokens, token, {**token_body, 'name': 'Bounded fixture ' + str(index)}, status=201)
    request(tokens, token, {**token_body, 'name': 'Eleventh active token'}, status=400)
    assert len([item for item in request(tokens, token)['items'] if not item['revokedAt']]) == 10
    checks.append('maximum_ten_active_tokens_per_app')
    print(json.dumps({'passed': True, 'checks': checks}), flush=True)
except Exception:
    if (root / 'process.log').exists(): print((root / 'process.log').read_text()[-2500:])
    raise
finally:
    if process:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
    temporary.cleanup()
