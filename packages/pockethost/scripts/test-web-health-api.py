#!/usr/bin/env python3
"""Disposable native PocketBase checks for continuous-health configuration and transitions."""
import argparse
import datetime
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
root = pathlib.Path(tempfile.mkdtemp(prefix='ph-health-api-')).resolve()
process = None
checks = []
with socket.socket() as server:
    server.bind(('127.0.0.1', 0))
    port = server.getsockname()[1]
base = f'http://127.0.0.1:{port}'
secret = secrets.token_urlsafe(40)


def request(path, token='', body=None, method=None, status=200, worker=False):
    call = urllib.request.Request(base + path, method=method or ('POST' if body is not None else 'GET'),
        headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {}), **({'X-Web-Worker': secret} if worker else {})},
        data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(call, timeout=15) as response:
            data = response.read()
            assert response.status == status, (path, response.status, status)
            assert response.headers.get('Cache-Control') == 'no-store' if path.startswith('/api/web-apps') else True
            return json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        if error.code == status:
            return json.loads(error.read())
        raise AssertionError(f'{path}: {error.code} ' + error.read().decode())


def private(endpoint, body, status=200):
    return request('/api/web-apps/_health/' + endpoint, body=body, status=status, worker=True)


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks = root / 'hooks'
    hooks.mkdir()
    shutil.copy(args.bundle or source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    migrations = root / 'migrations'
    migrations.mkdir()
    # All migrations: alertState (pending recovery alert) lives in a later migration.
    for migration in (source / 'pb_migrations').glob('*.js'):
        if migration.name.split('_')[0].isdigit():
            shutil.copy(migration, migrations / migration.name)
    routes = [('GET', '/api/web-apps/{id}/health', 'HandleWebHealthGet', True),
        ('PATCH', '/api/web-apps/{id}/health', 'HandleWebHealthUpdate', True),
        ('GET', '/api/web-apps/{id}/health/history', 'HandleWebHealthHistory', True),
        ('POST', '/api/web-apps/_health/config', 'HandleWebHealthConfig', False),
        ('POST', '/api/web-apps/_health/report', 'HandleWebHealthReport', False)]
    (hooks / 'health.pb.js').write_text('\n'.join('routerAdd(' + json.dumps(method) + ',' + json.dumps(path) + ',e=>require(`${__hooks}/mothership`).' + handler + '(e)' + (',$apis.requireAuth("users")' if auth else '') + ');' for method, path, handler, auth in routes))
    env = {**os.environ, 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_WORKER_SECRET': secret, 'WEB_APPS_MASTER_KEY': 'h' * 32,
        'WEB_APPS_ROOT': str(root / 'web'), 'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid'}
    password, email = 'P' + secrets.token_urlsafe(32), 'health@example.invalid'
    data = root / 'pb_data'
    subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(data), '--hooksDir', str(root / 'empty'), '--migrationsDir', str(migrations)], env=env, check=True, capture_output=True)
    log = (root / 'process.log').open('wb')
    process = subprocess.Popen([args.pocketbase, 'serve', '--dir', str(data), '--hooksDir', str(hooks), '--migrationsDir', str(migrations), '--http', f'127.0.0.1:{port}'], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError((root / 'process.log').read_text()[-2500:])
        try:
            request('/api/health')
            break
        except OSError:
            time.sleep(.1)
    su = request('/api/collections/_superusers/auth-with-password', body={'identity': email, 'password': password})['token']
    def user(name, admin):
        row = request('/api/collections/users/records', su, {'email': name + '@example.invalid', 'username': name, 'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': admin, 'subscription': 'premium', 'subscription_quantity': 1})
        return row, request('/api/collections/users/auth-with-password', body={'identity': row['email'], 'password': password})['token']
    actor, token = user('healthadmin', True)
    _, plain = user('healthplain', False)
    app = request('/api/collections/web_apps/records', su, {'name': 'Health fixture', 'uid': actor['id'], 'status': 'running', 'config': {'healthPath': '/ready', 'repository': 'owner/repo', 'generatedDomain': 'web.example.invalid'}})
    ident, path = app['id'], '/api/web-apps/' + app['id'] + '/health'
    deployment = request('/api/collections/web_deployments/records', su, {'appId': ident, 'action': 'deploy', 'status': 'ready'})
    dep = deployment['id']
    request('/api/collections/web_deployments/records/' + dep, su, {'result': {'container': f'ph-web-{ident}-{dep}', 'target': 'http://127.0.0.1:32789'}}, 'PATCH')
    request('/api/collections/web_apps/records/' + ident, su, {'activeDeployment': dep}, 'PATCH')
    request(path, status=401)
    request(path, plain, status=404)
    request('/api/collections/web_app_health/records', token, status=403)
    request('/api/collections/web_app_health_events/records', token, status=403)
    initial = request(path, token)
    assert initial['config']['path'] == '/ready' and not initial['config']['enabled'] and not initial['automaticRecovery']
    request('/api/web-apps/_health/config', token, {}, status=401)
    checks.append('admin_only_locked_collections_private_worker_and_opt_in_defaults')
    def save(body):
        return request(path, token, {**body, 'revision': request(path, token)['config']['revision']}, 'PATCH')
    enabled = save({'enabled': True, 'path': '/api/health', 'intervalSeconds': 60, 'timeoutSeconds': 2, 'failureThreshold': 3})
    assert enabled['state']['status'] == 'unknown'
    revision = enabled['config']['revision']
    for bad in [{'path': '//169.254.169.254'}, {'path': '/x?secret=private'}, {'path': '/a%2fb'}, {'path': '/../../'}, {'path': '/line\nprivate'}, {'intervalSeconds': 59}, {'timeoutSeconds': 11}, {'failureThreshold': 0}, {'enabled': 'yes'}, {'target': 'http://127.0.0.1:8091'}]:
        request(path, token, bad, 'PATCH', 400)
        assert request(path, token)['config']['revision'] == revision
    request(path, token, {'revision': 'old', 'enabled': False}, 'PATCH', 400)
    assert request('/api/collections/web_apps/records/' + ident, su)['config']['healthPath'] == '/ready'
    configs = private('config', {})
    assert len(configs['items']) == 1 and configs['items'][0]['expectedTarget'] == 'http://127.0.0.1:32789'
    assert all(word not in json.dumps(configs) for word in ['envEncrypted', 'snapshotEncrypted', 'privateKey'])
    checks.append('safe_paths_bounded_options_revision_and_independent_publication_health_path')
    now = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=10)
    sequence = 0
    def report(status=503, error='http_error', **extra):
        global sequence
        sequence += 1
        body = {'appId': ident, 'deploymentId': dep, 'revision': revision, 'containerId': 'f' * 64, 'statusCode': status, 'durationMs': 17, 'errorCode': error, 'checkedAt': (now + datetime.timedelta(milliseconds=sequence)).isoformat(), **extra}
        return private('report', body), body
    for failures in (1, 2):
        assert report()[0]['accepted']
        state = request(path, token)['state']
        assert state['status'] == 'unknown' and state['consecutiveFailures'] == failures
    accepted, last = report()
    assert accepted['accepted'] and request(path, token)['state']['status'] == 'degraded'
    assert not private('report', last)['accepted']
    assert request(path, token)['state']['consecutiveFailures'] == 3
    assert report(200, '')[0]['accepted']
    assert request(path, token)['state']['status'] == 'healthy'
    reasons = [item['reason'] for item in request(path + '/history', token)['items']]
    assert 'failure_threshold' in reasons and 'recovered' in reasons
    checks.append('consecutive_failure_threshold_recovery_and_replay_deduplication')
    assert not report(200, '', revision='x' * 24)[0]['accepted']
    request('/api/collections/web_apps/records/' + ident, su, {'status': 'stopped'}, 'PATCH')
    assert request(path, token)['state']['status'] == 'paused' and private('config', {})['items'] == []
    assert not report(200, '')[0]['accepted']
    request('/api/collections/web_apps/records/' + ident, su, {'status': 'running'}, 'PATCH')
    row = request('/api/collections/web_app_health/records', su)['items'][0]
    request('/api/collections/web_app_health/records/' + row['id'], su, {'lastCheckedAt': (now - datetime.timedelta(minutes=10)).isoformat()}, 'PATCH')
    assert request(path, token)['state']['status'] == 'stale'
    other = request('/api/collections/web_deployments/records', su, {'appId': ident, 'action': 'deploy', 'status': 'ready'})
    request('/api/collections/web_apps/records/' + ident, su, {'activeDeployment': other['id']}, 'PATCH')
    assert not report(200, '')[0]['accepted'] and request(path, token)['state']['status'] == 'unknown'
    request('/api/collections/web_apps/records/' + ident, su, {'activeDeployment': dep}, 'PATCH')
    checks.append('stale_results_stopped_apps_and_changed_publications_are_separated')
    # Public probe target: reserved/non-public hosts are never probed; a real domain (custom first) is.
    assert private('config', {})['items'][0]['publicHost'] == ''
    request('/api/collections/web_apps/records/' + ident, su, {'config': {'healthPath': '/ready', 'repository': 'owner/repo', 'generatedDomain': 'web-fixture.monappli.re'}}, 'PATCH')
    assert private('config', {})['items'][0]['publicHost'] == 'web-fixture.monappli.re'
    request('/api/collections/web_app_domains/records', su, {'appId': ident, 'host': 'www.client-site.fr', 'status': 'active', 'ownershipToken': 'a' * 40})
    assert private('config', {})['items'][0]['publicHost'] == 'www.client-site.fr'
    checks.append('public_host_prefers_active_custom_domain_and_skips_non_public_hosts')
    revision = save({'failureThreshold': 1})['config']['revision']
    for bad in [{'publicErrorCode': 'nope'}, {'publicDurationMs': -1}, {'publicStatusCode': 700}, {'publicHost': 5}]:
        private('report', {'appId': ident, 'deploymentId': dep, 'revision': revision, 'containerId': 'f' * 64, 'statusCode': 0, 'durationMs': 17, 'errorCode': 'connection_error', 'checkedAt': (now + datetime.timedelta(seconds=1)).isoformat(), **bad}, 400)
    assert report(0, 'connection_error', publicHost='www.client-site.fr', publicStatusCode=0, publicDurationMs=3100, publicErrorCode='public_tls')[0]['accepted']
    state = request(path, token)['state']
    assert state['status'] == 'degraded' and state['lastResult'] == 'public_tls'
    event = request(path + '/history', token)['items'][0]
    assert event['status'] == 'degraded' and event['reason'] == 'public_tls'
    checks.append('public_probe_fields_validated_stored_and_named_in_the_degraded_event')
    # Degraded on deployment A, healthy on deployment B: the recovery is still owed and recorded.
    request('/api/collections/web_deployments/records/' + other['id'], su, {'result': {'container': f"ph-web-{ident}-{other['id']}", 'target': 'http://127.0.0.1:32790'}}, 'PATCH')
    request('/api/collections/web_apps/records/' + ident, su, {'activeDeployment': other['id']}, 'PATCH')
    assert report(200, '', deploymentId=other['id'])[0]['accepted']
    event = request(path + '/history', token)['items'][0]
    assert (event['fromStatus'], event['status'], event['reason'], event['deploymentId']) == ('degraded', 'healthy', 'recovered', other['id']), event
    assert report(200, '', deploymentId=other['id'])[0]['accepted']
    assert request(path + '/history', token)['items'][0]['id'] == event['id']
    checks.append('recovery_after_redeploy_is_recorded_once_with_from_degraded')
    # Health-path fix during an incident keeps the recovery owed; disabling monitoring cancels it.
    assert report(503, 'http_error', deploymentId=other['id'])[0]['accepted'] and request(path, token)['state']['status'] == 'degraded'
    revision = save({'path': '/healthz'})['config']['revision']
    assert request(path, token)['state']['status'] == 'unknown'
    assert report(200, '', deploymentId=other['id'])[0]['accepted']
    event = request(path + '/history', token)['items'][0]
    assert (event['fromStatus'], event['reason']) == ('degraded', 'recovered'), event
    assert report(503, 'http_error', deploymentId=other['id'])[0]['accepted']
    save({'enabled': False})
    revision = save({'enabled': True})['config']['revision']
    assert report(200, '', deploymentId=other['id'])[0]['accepted']
    assert request(path + '/history', token)['items'][0]['reason'] == 'healthy'
    checks.append('health_path_change_keeps_owed_recovery_and_disable_cancels_it')
    request('/api/collections/web_apps/records/' + ident, su, {'activeDeployment': dep}, 'PATCH')
    revision = save({'failureThreshold': 1})['config']['revision']
    recent = (now - datetime.timedelta(days=1)).isoformat().replace('T', ' ')
    old = (now - datetime.timedelta(days=40)).isoformat().replace('T', ' ')
    with sqlite3.connect(data / 'data.db') as db:
        db.executemany('INSERT INTO web_app_health_events (id,appId,deploymentId,actorId,fromStatus,status,reason,checkedAt,httpStatus,durationMs,consecutiveFailures,created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', [
            (secrets.token_hex(8)[:15], ident, dep, '', 'healthy', 'degraded', 'failure_threshold', old if i == 0 else recent, 503, 10, 3, old if i == 0 else recent) for i in range(1100)])
    assert report()[0]['accepted']
    first = request(path + '/history?page=1&perPage=100', token)
    second = request(path + '/history?page=2&perPage=100', token)
    last_page = request(path + '/history?page=10&perPage=100', token)
    assert first['totalItems'] == 1000 and len(first['items']) == len(second['items']) == 100 and first['hasMore'] and not last_page['hasMore']
    assert not set(item['id'] for item in first['items']) & set(item['id'] for item in second['items'])
    with sqlite3.connect(data / 'data.db') as db:
        assert db.execute('SELECT COUNT(*) FROM web_app_health_events WHERE checkedAt=?', (old,)).fetchone()[0] == 0
    assert request(path + '/history?perPage=500', token)['perPage'] == 100
    checks.append('real_paginated_history_with_1000_event_and_30_day_retention')
    print(json.dumps({'passed': True, 'checks': checks}), flush=True)
except Exception:
    if (root / 'process.log').exists():
        print((root / 'process.log').read_text()[-2500:], flush=True)
    raise
finally:
    if process:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
    shutil.rmtree(root, ignore_errors=True)
