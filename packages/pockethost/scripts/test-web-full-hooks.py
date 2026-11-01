#!/usr/bin/env python3
"""Start the exact complete generated PocketBase hooks, then exercise real routed contracts.

Everything lives in a temporary DATA_ROOT. Zero PocketBase instances and no Docker
worker are created. External command shims block Docker/SSH/network CLIs defensively.
"""
import argparse
import hashlib
import json
import os
import pathlib
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True, type=pathlib.Path)
parser.add_argument('--pocketbase', required=True)
parser.add_argument('--bundle', type=pathlib.Path)
parser.add_argument('--hooks', type=pathlib.Path)
args = parser.parse_args()
os.umask(0o077)
temporary = tempfile.TemporaryDirectory(prefix='ph-web-full-hooks-')
root = pathlib.Path(temporary.name).resolve()
process = None
checks = []
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
origin = f'http://127.0.0.1:{port}'


def request(path, token='', body=None, method=None, status=200):
    call = urllib.request.Request(origin + path, method=method or ('POST' if body is not None else 'GET'), headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {})}, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(call, timeout=20) as response:
            raw = response.read(); assert response.status == status, (path, response.status, status)
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        if error.code == status: return json.loads(raw) if raw else None
        raise AssertionError(f'{path}: {error.code}: ' + raw.decode()) from None


def stop():
    global process
    if not process: return
    process.terminate()
    try: process.wait(timeout=10)
    except subprocess.TimeoutExpired: process.kill(); process.wait()
    process = None


def start(directory, label):
    global process
    output = (root / (label + '.log')).open('wb')
    process = subprocess.Popen([args.pocketbase, 'serve', '--dir', str(data), '--hooksDir', str(directory), '--migrationsDir', str(source / 'pb_migrations'), '--http', f'127.0.0.1:{port}'], env=env, stdout=output, stderr=subprocess.STDOUT)
    for _ in range(150):
        if process.poll() is not None:
            raise RuntimeError(label + ' startup failed: ' + (root / (label + '.log')).read_text()[-6000:])
        try:
            health = request('/api/health')
            if health.get('code') == 200: return
        except OSError: time.sleep(.1)
    raise RuntimeError(label + ' health check timed out')


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks = root / 'pb_hooks'; hooks.mkdir()
    original_hooks = args.hooks or source / 'pb_hooks/mothership.pb.js'
    shutil.copy(original_hooks, hooks / 'mothership.pb.js')
    shutil.copy(args.bundle or source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    # Exact generated registration file: do not replace its routes with test-only copies.
    assert hashlib.sha256((hooks / 'mothership.pb.js').read_bytes()).digest() == hashlib.sha256(original_hooks.read_bytes()).digest()
    assert len(list(hooks.glob('*.pb.js'))) == 1
    if (source / 'pb_admin_ext').exists(): shutil.copytree(source / 'pb_admin_ext', root / 'pb_admin_ext')
    tools = root / 'blocked-tools'; tools.mkdir()
    # git/ssh-keyscan/ssh-keygen: the controller's Git reader runs git with a fixed PATH, so no ready source is created.
    for command in ['docker', 'ssh', 'scp', 'curl', 'wget', 'git', 'ssh-keyscan', 'ssh-keygen']:
        shim = tools / command
        shim.write_text('#!' + sys.executable + '\nimport pathlib,sys\nwith pathlib.Path(' + repr(str(root / 'blocked-commands.log')) + ').open("a") as f:f.write(' + repr(command) + '+"\\n")\nsys.exit(79)\n')
        shim.chmod(0o700)
    isolated = root / 'data'; isolated.mkdir()
    env = {'PATH': str(tools) + os.pathsep + os.environ.get('PATH', ''), 'LANG': 'C.UTF-8', 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_MASTER_KEY': 'f' * 32, 'WEB_APPS_WORKER_SECRET': secrets.token_urlsafe(40), 'WEB_APPS_ROOT': str(root / 'web'), 'DATA_ROOT': str(isolated), 'APEX_DOMAIN': 'example.invalid', 'APP_HOST': 'app.example.invalid', 'APP_URL': origin}
    data = root / 'pb_data'; empty = root / 'empty-hooks'; empty.mkdir()
    email, password = 'fullhooks@example.invalid', 'P' + secrets.token_urlsafe(32)
    subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(data), '--hooksDir', str(empty), '--migrationsDir', str(source / 'pb_migrations')], env=env, check=True, capture_output=True)
    # Seed with stock hooks disabled, avoiding signup/notification side effects entirely.
    start(empty, 'seed')
    su = request('/api/collections/_superusers/auth-with-password', body={'identity': email, 'password': password})['token']
    actor = request('/api/collections/users/records', su, {'email': email, 'username': 'fullhooksadmin', 'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': True, 'subscription': 'premium', 'subscription_quantity': 1})
    config = {'repository': '', 'branch': 'main', 'rootDirectory': '.', 'healthPath': '/', 'generatedDomain': 'fullhooks.example.invalid'}
    app = request('/api/collections/web_apps/records', su, {'name': 'Full hooks fixture', 'uid': actor['id'], 'status': 'stopped', 'config': config})
    project = request('/api/collections/web_projects/records', su, {'name': 'Full hooks project', 'repository': 'fixture/repository', 'actorId': actor['id'], 'previewEnabled': False, 'previewMax': 1, 'previewTtlHours': 24})
    assert request('/api/collections/instances/records', su)['totalItems'] == 0
    stop()
    # Register every real routerAdd/routerUse/onBootstrap/cron hook together.
    start(hooks, 'full-hooks')
    assert request('/api/health')['code'] == 200
    text = (root / 'full-hooks.log').read_text()
    assert 'conflicts with pattern' not in text and 'panic:' not in text
    checks.append('exact_complete_generated_hooks_start_and_health_200_without_mux_conflicts')
    token = request('/api/collections/users/auth-with-password', body={'identity': email, 'password': password})['token']
    su = request('/api/collections/_superusers/auth-with-password', body={'identity': email, 'password': password})['token']
    request('/api/web-apps', status=401)
    assert request('/api/web-apps', token)['items'][0]['id'] == app['id']
    projects = request('/api/web-projects', token); assert projects['items'][0]['id'] == project['id']
    updated = request('/api/web-projects/' + project['id'], token, {'name': 'Updated through actual project route', 'previewEnabled': False, 'previewMax': 1, 'previewTtlHours': 24}, method='PATCH')
    assert updated['project']['name'] == 'Updated through actual project route'
    assert request('/api/web-apps/' + app['id'] + '/health', token)['config']['enabled'] is False
    checks.append('global_projects_prefix_and_instance_health_routes_are_distinct_and_callable')
    # Git deploy-key routes: the public webhook and the generic {sourceId}/{action} route coexist without a mux panic.
    request('/api/web-git-sources/webhook/' + 'A' * 24, body={}, status=404)
    request('/api/web-git-sources/abcdefghijklmno/scan', body={}, status=401)
    request('/api/web-git-sources/abcdefghijklmno', status=401)
    request('/api/web-git-sources/abcdefghijklmno', token, status=404)
    request('/api/web-git-sources/abcdefghijklmno/branches', token, status=404)
    checks.append('git_source_webhook_and_action_routes_registered_without_conflict')
    storage = '/api/web-apps/' + app['id'] + '/storage'
    initial = request(storage, token); assert initial['volumes'] == []
    configured = request(storage, token, {'volumes': [{'name': 'uploads', 'target': '/data', 'sizeMiB': 16, 'readOnly': False}], 'revision': initial['revision'], 'retention': 1}, method='PATCH')
    assert configured['volumes'][0]['name'] == 'uploads'
    unknown = request(storage + '/uploads/destroy', token, {}, status=400)
    assert 'Opération de stockage inconnue' in unknown['message']
    restore = request(storage + '/uploads/restore', token, {}, status=400)
    assert 'Prévisualisez la restauration' in restore['message']
    backup = request(storage + '/uploads/backup', token, {}, status=201)['job']
    assert backup['operation'] == 'backup' and backup['status'] == 'queued'
    history = request(storage + '/history', token); assert history['items'][0]['id'] == backup['id']
    assert request('/api/collections/instances/records', su)['totalItems'] == 0
    checks.append('real_storage_operation_wildcard_backup_201_restore_preview_guard_and_unknown_rejection')
    blocked = (root / 'blocked-commands.log').read_text().splitlines() if (root / 'blocked-commands.log').exists() else []
    assert not any(command in ['ssh', 'scp', 'curl', 'wget', 'git', 'ssh-keyscan', 'ssh-keygen'] for command in blocked), blocked
    # No real Docker executable is ever reachable; bootstrap may attempt a read-only status sample.
    print(json.dumps({'passed': True, 'registeredRoutes': (hooks / 'mothership.pb.js').read_text().count('routerAdd('), 'health': 200, 'instances': 0, 'dockerExecuted': False, 'blockedStatusSamples': len(blocked), 'checks': checks}), flush=True)
except Exception:
    if (root / 'full-hooks.log').exists(): print((root / 'full-hooks.log').read_text()[-6000:])
    raise
finally:
    stop(); temporary.cleanup()
