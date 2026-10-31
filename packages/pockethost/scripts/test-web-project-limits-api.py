#!/usr/bin/env python3
"""Disposable PocketBase: optional project policies, RBAC, queue admission, worker registry and global limiter preservation."""
import argparse, concurrent.futures, json, os, pathlib, secrets, shutil, socket, subprocess, tempfile, time, urllib.request, urllib.error

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=pathlib.Path, required=True)
parser.add_argument('--pocketbase', required=True)
args = parser.parse_args()
os.umask(0o077)
temporary = tempfile.TemporaryDirectory(prefix='ph-project-limits-')
root = pathlib.Path(temporary.name).resolve()
with socket.socket() as s: s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
origin = f'http://127.0.0.1:{port}'
worker = secrets.token_urlsafe(40)
process = None

def request(path, token='', body=None, method=None, status=200, headers=None):
    req = urllib.request.Request(origin + path, data=None if body is None else json.dumps(body).encode(), method=method or ('POST' if body is not None else 'GET'), headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {}), **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            assert response.status == status, (path, response.status, status)
            return json.load(response)
    except urllib.error.HTTPError as error:
        text = error.read()
        assert error.code == status, (path, error.code, status, text.decode())
        return json.loads(text)

try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks = root / 'hooks'; hooks.mkdir()
    shutil.copy(source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    shutil.copy(source / 'src/lib/handlers/webApps/hooks.ts', hooks / 'web.pb.js')
    env = {**os.environ, 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_MASTER_KEY': 'k' * 32, 'WEB_APPS_WORKER_SECRET': worker, 'WEB_APPS_ROOT': str(root / 'web'), 'APEX_DOMAIN': 'example.invalid'}
    password = 'P' + secrets.token_urlsafe(32); data = root / 'data'
    subprocess.run([args.pocketbase, 'superuser', 'upsert', 'su@example.invalid', password, '--dir', str(data), '--hooksDir', str(root / 'empty'), '--migrationsDir', str(source / 'pb_migrations')], env=env, check=True, capture_output=True)
    with (root / 'process.log').open('wb') as log:
        process = subprocess.Popen([args.pocketbase, 'serve', '--http', f'127.0.0.1:{port}', '--dir', str(data), '--hooksDir', str(hooks), '--migrationsDir', str(source / 'pb_migrations')], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        if process.poll() is not None: raise RuntimeError((root / 'process.log').read_text()[-3000:])
        try: request('/api/health'); break
        except OSError: time.sleep(.1)
    su = request('/api/collections/_superusers/auth-with-password', body={'identity': 'su@example.invalid', 'password': password})['token']
    initial_global = request('/api/settings', su)['rateLimits']
    tokens = {}
    for name in ['platform', 'admin', 'developer', 'viewer', 'outsider']:
        user = request('/api/collections/users/records', su, {'email': name + '@example.invalid', 'username': name, 'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': name == 'platform', 'subscription': 'premium', 'subscription_quantity': 1})
        tokens[name] = request('/api/collections/users/auth-with-password', body={'identity': user['email'], 'password': password})['token']
    def site(name):
        return request('/api/web-apps', tokens['platform'], {'name': name, 'runtime': {'kind': 'image', 'image': 'ghcr.io/team/site@sha256:' + 'a' * 64}}, status=201)['app']['id']
    a, b, other = site('A'), site('B'), site('Other')
    project = request('/api/web-apps/' + a + '/project', tokens['platform'], {'name': 'Limited project', 'environment': 'prod'}, status=201)['project']
    project_id = project['id']
    request('/api/web-apps/' + b + '/project/attach', tokens['platform'], {'projectId': project_id, 'environment': 'staging', 'slug': 'staging'})
    assert not project['limits']['traffic']['enabled'] and not project['limits']['resources']['enabled']
    for name, role in [('admin', 'admin'), ('developer', 'developer'), ('viewer', 'viewer')]:
        request(f'/api/web-projects/{project_id}/members', tokens['platform'], {'email': name + '@example.invalid', 'role': role}, status=201)
    limits = project['limits']; limits['traffic'].update(enabled=True, requestsPerMinute=20, concurrentRequests=2)
    limits['resources'].update(enabled=True, cpuMillicores=1000, memoryMiB=1024, buildMinutes=2, queuedBuilds=1)
    path = '/api/web-projects/' + project_id
    for name, code in [('developer', 403), ('viewer', 403), ('outsider', 404)]:
        request(path, tokens[name], {'limits': limits}, method='PATCH', status=code)
    saved = request(path, tokens['admin'], {'limits': limits}, method='PATCH')['project']
    assert saved['limits'] == limits and saved['name'] == project['name']
    invalid = json.loads(json.dumps(limits)); invalid['traffic']['enabled'] = 'true'
    request(path, tokens['admin'], {'limits': invalid}, method='PATCH', status=400)
    invalid = json.loads(json.dumps(limits)); invalid['resources']['memoryMiB'] = 0
    request(path, tokens['admin'], {'limits': invalid}, method='PATCH', status=400)
    request('/api/collections/web_projects/records', tokens['admin'], status=403)
    assert request('/api/settings', su)['rateLimits'] == initial_global
    headers = {'X-Web-Worker': worker}
    request('/api/web-apps/_worker/project-limits', body={}, status=401)
    request('/api/web-apps/_worker/project-limits', body={}, headers={**headers, 'X-Forwarded-For': '127.0.0.1'}, status=403)
    registry = request('/api/web-apps/_worker/project-limits', body={}, headers=headers)
    assert registry['projects'][project_id] == limits and registry['apps'][a] == registry['apps'][b] == project_id
    request('/api/web-apps/' + a + '/deploy', tokens['admin'], {}, status=202)
    request('/api/web-apps/' + b + '/deploy', tokens['admin'], {}, status=429)
    request('/api/web-apps/' + other + '/deploy', tokens['platform'], {}, status=202)
    claimed = request('/api/web-apps/_worker/claim', body={}, headers=headers)['job']
    if claimed['appId'] != a:
        request('/api/web-apps/_worker/report', body={'id': claimed['id'], 'status': 'failed', 'error': 'Fixture'}, headers=headers)
        claimed = request('/api/web-apps/_worker/claim', body={}, headers=headers)['job']
    assert claimed['appId'] == a and claimed['snapshot']['projectPolicy']['limits'] == limits
    assert set(claimed['snapshot']['projectPolicy']['appIds']) == {a, b}
    request('/api/web-apps/_worker/report', body={'id': claimed['id'], 'status': 'failed', 'error': 'Fixture'}, headers=headers)
    limits['resources']['enabled'] = False
    saved = request(path, tokens['admin'], {'limits': limits}, method='PATCH')['project']
    assert saved['limits']['traffic']['enabled'] and not saved['limits']['resources']['enabled']
    request('/api/web-apps/' + a + '/deploy', tokens['admin'], {}, status=202)
    request('/api/web-apps/' + b + '/deploy', tokens['admin'], {}, status=202)
    request(path, tokens['platform'], {'name': 'Renamed'}, method='PATCH')
    projects = request('/api/web-projects', tokens['admin'])['items']
    assert next(p for p in projects if p['id'] == project_id)['limits'] == limits
    assert request('/api/settings', su)['rateLimits'] == initial_global
    print(json.dumps({'passed': True, 'checks': ['default_disabled', 'independent_toggles', 'admin_write_only', 'outsider_hidden', 'strict_validation', 'locked_collection', 'global_limiter_unchanged', 'private_worker_registry', 'shared_project_policy', 'queue_429_with_project_isolation', 'claim_current_policy', 'disable_restores_queue_admission', 'rename_preserves_policy']}))
except Exception:
    if (root / 'process.log').exists(): print((root / 'process.log').read_text()[-2500:])
    raise
finally:
    if process:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
    temporary.cleanup()
