#!/usr/bin/env python3
"""Real disposable PocketBase: per-project roles on health, runtime logs, tasks, alerts, CI tokens, terminal, recovery and GitHub connect."""
import argparse, json, os, pathlib, secrets, shutil, socket, subprocess, tempfile, time, urllib.error, urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True, type=pathlib.Path)
parser.add_argument('--pocketbase', required=True)
args = parser.parse_args()
os.umask(0o077)
temporary = tempfile.TemporaryDirectory(prefix='ph-web-ops-roles-')
root = pathlib.Path(temporary.name).resolve()
process = None
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
origin = f'http://127.0.0.1:{port}'
worker_secret = 'W' + secrets.token_urlsafe(40)


def call(path, token='', body=None, method=None, headers=None):
    value = urllib.request.Request(origin + path, method=method or ('POST' if body is not None else 'GET'), headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {}), **(headers or {})}, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(value, timeout=25) as response:
            data = response.read(); return response.status, json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        data = error.read(); return error.code, json.loads(data) if data else None


def request(path, token='', body=None, method=None, status=200, headers=None):
    code, data = call(path, token, body, method, headers)
    assert code == status, f'{method or ""} {path}: {code} != {status}: {data}'
    return data


def private(path, body, status=200): return request('/api/web-apps/' + path, body=body, status=status, headers={'X-Web-Worker': worker_secret})


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks = root / 'hooks'; hooks.mkdir(); shutil.copy(source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    authed = [
        ('POST', '/api/web-apps/{id}/project', 'HandleWebAppProjectCreate'),
        ('POST', '/api/web-projects/{projectId}/members', 'HandleWebProjectMemberAdd'),
        ('PATCH', '/api/web-projects/{projectId}/members/{memberId}', 'HandleWebProjectMemberUpdate'),
        ('DELETE', '/api/web-projects/{projectId}/members/{memberId}', 'HandleWebProjectMemberRemove'),
        ('GET', '/api/web-apps/{id}/health', 'HandleWebHealthGet'),
        ('PATCH', '/api/web-apps/{id}/health', 'HandleWebHealthUpdate'),
        ('GET', '/api/web-apps/{id}/health/history', 'HandleWebHealthHistory'),
        ('GET', '/api/web-apps/{id}/runtime-logs', 'HandleWebRuntimeLogs'),
        ('GET', '/api/web-apps/{id}/tasks', 'HandleWebTasksList'),
        ('POST', '/api/web-apps/{id}/tasks', 'HandleWebTasksCreate'),
        ('PATCH', '/api/web-apps/{id}/tasks/{taskId}', 'HandleWebTaskUpdate'),
        ('DELETE', '/api/web-apps/{id}/tasks/{taskId}', 'HandleWebTaskDelete'),
        ('POST', '/api/web-apps/{id}/tasks/{taskId}/run', 'HandleWebTaskRun'),
        ('GET', '/api/web-apps/{id}/task-runs', 'HandleWebTaskRuns'),
        ('GET', '/api/web-apps/{id}/notifications', 'HandleWebNotificationsGet'),
        ('PUT', '/api/web-apps/{id}/notifications', 'HandleWebNotificationsPut'),
        ('GET', '/api/web-apps/{id}/notifications/history', 'HandleWebNotificationHistory'),
        ('POST', '/api/web-apps/{id}/notifications/test', 'HandleWebNotificationTest'),
        ('GET', '/api/web-apps/{id}/deploy-tokens', 'HandleWebCiTokensList'),
        ('POST', '/api/web-apps/{id}/deploy-tokens', 'HandleWebCiTokensCreate'),
        ('DELETE', '/api/web-apps/{id}/deploy-tokens/{tokenId}', 'HandleWebCiTokenRevoke'),
        ('POST', '/api/web-apps/{id}/terminal/session', 'HandleWebTerminalSession'),
        ('GET', '/api/web-apps/{id}/terminal/sessions', 'HandleWebTerminalHistory'),
        ('POST', '/api/web-apps/{id}/terminal/sessions/{sessionId}/close', 'HandleWebTerminalRevoke'),
        ('GET', '/api/web-platform-recovery', 'HandleWebPlatformRecoveryStatus'),
        ('GET', '/api/web-platform-recovery/runs', 'HandleWebPlatformRecoveryRuns'),
        ('GET', '/api/web-apps/github/connect/status', 'HandleWebGithubConnectStatus'),
        ('POST', '/api/web-apps/github/connect/start', 'HandleWebGithubConnectStart'),
    ]
    public = [
        ('POST', '/api/web-apps/ci/deploy', 'HandleWebCiDeploy'),
        ('POST', '/api/web-apps/_terminal/claim', 'HandleWebTerminalClaim'),
        ('POST', '/api/web-apps/_terminal/validate', 'HandleWebTerminalValidate'),
        ('POST', '/api/web-apps/_tasks/claim', 'HandleWebTaskClaim'),
    ]
    text = ''.join(f'routerAdd({json.dumps(m)},{json.dumps(p)},e=>require(`${{__hooks}}/mothership`).{h}(e),$apis.requireAuth("users"));\n' for m, p, h in authed)
    text += ''.join(f'routerAdd({json.dumps(m)},{json.dumps(p)},e=>require(`${{__hooks}}/mothership`).{h}(e));\n' for m, p, h in public)
    (hooks / 'test.pb.js').write_text(text)
    env = {**os.environ, 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_MASTER_KEY': 'p' * 32, 'WEB_APPS_WORKER_SECRET': worker_secret, 'WEB_APPS_ROOT': str(root / 'web'), 'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid'}
    (root / 'web').mkdir()
    password, email = 'P' + secrets.token_urlsafe(32), 'ops@example.invalid'  # never starts with '-' (CLI flag)
    data = root / 'pb_data'
    subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(data), '--hooksDir', str(root / 'empty-hooks'), '--migrationsDir', str(source / 'pb_migrations')], env=env, check=True, capture_output=True)
    log = (root / 'process.log').open('wb')
    process = subprocess.Popen([args.pocketbase, 'serve', '--dir', str(data), '--hooksDir', str(hooks), '--migrationsDir', str(source / 'pb_migrations'), '--http', f'127.0.0.1:{port}'], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        if process.poll() is not None: raise RuntimeError('PocketBase failed to start')
        try: call('/api/health'); break
        except OSError: time.sleep(.1)
    su = request('/api/collections/_superusers/auth-with-password', body={'identity': email, 'password': password})['token']

    def user(name, admin=False):
        row = request('/api/collections/users/records', su, {'email': name + '@example.invalid', 'username': name, 'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': admin, 'subscription': 'premium', 'subscription_quantity': 1})
        return row, request('/api/collections/users/auth-with-password', body={'identity': row['email'], 'password': password})['token']
    platform_row, platform = user('platform', True)
    users = {name: user(name) for name in ['owner', 'admin', 'dev', 'viewer', 'outsider', 'suspended']}
    tok = {name: value[1] for name, value in users.items()}; uid = {name: value[0]['id'] for name, value in users.items()}

    def app(name):
        return request('/api/collections/web_apps/records', su, {'name': name, 'uid': platform_row['id'], 'status': 'stopped', 'config': {'repository': 'owner/repo', 'branch': 'main', 'rootDirectory': '.', 'healthPath': '/', 'generatedDomain': 'web-' + secrets.token_hex(6) + '.example.invalid'}})['id']
    app_id, legacy_id = app('Projet'), app('Legacy')
    project_id = request(f'/api/web-apps/{app_id}/project', platform, {'name': 'Équipe Ops', 'environment': 'prod'}, status=201)['project']['id']
    members = f'/api/web-projects/{project_id}/members'
    member = {}
    for name, role in [('owner', 'owner'), ('admin', 'admin'), ('dev', 'developer'), ('viewer', 'viewer'), ('suspended', 'viewer')]:
        member[name] = request(members, platform, {'email': name + '@example.invalid', 'role': role}, status=201)['member']
    request('/api/collections/users/records/' + uid['suspended'], su, {'suspension': 'abuse'}, method='PATCH')
    # A published container so terminal sessions and manual task runs can go past the publication check.
    dep = request('/api/collections/web_deployments/records', su, {'appId': app_id, 'action': 'deploy', 'status': 'ready'})['id']
    request(f'/api/collections/web_deployments/records/{dep}', su, {'result': {'container': f'ph-web-{app_id}-{dep}', 'image': f'ph-web/{app_id}:{dep}'}}, method='PATCH')
    request(f'/api/collections/web_apps/records/{app_id}', su, {'activeDeployment': dep, 'status': 'running'}, method='PATCH')
    base, legacy = f'/api/web-apps/{app_id}', f'/api/web-apps/{legacy_id}'
    checks = []

    def matrix(path, allowed, method=None, body=None, ok=200):
        """allowed = lowest role that passes; lower roles get 403, outsiders/legacy 404, suspended 403."""
        order = ['viewer', 'dev', 'admin', 'owner']
        for name in order:
            code, payload = call(path, tok[name], body, method)
            if order.index(name) < order.index(allowed): assert code == 403, (path, name, code, payload)
            else: assert code == ok if isinstance(ok, int) else code in ok, (path, name, code, payload)
        assert call(path, tok['outsider'], body, method)[0] == 404, path
        assert call(path, tok['suspended'], body, method)[0] == 403, path
        assert call(path.replace(app_id, legacy_id), tok['owner'], body, method)[0] == 404, path
        code = call(path, platform, body, method)[0]; assert code != 403 and code != 404, (path, 'platform', code)

    # Health and runtime logs.
    matrix(f'{base}/health', 'viewer'); matrix(f'{base}/health/history', 'viewer'); matrix(f'{base}/runtime-logs', 'viewer')
    matrix(f'{base}/health', 'admin', 'PATCH', {'enabled': False})
    request(f'{base}/health', tok['admin'], {'enabled': True, 'path': '/api/health'}, method='PATCH')
    request(f'{base}/health', tok['dev'], status=200)
    checks.append('health_and_runtime_logs_viewer_read_admin_configure_outsider_legacy_404')

    # Scheduled tasks: definitions admin, manual run developer, history viewer.
    matrix(f'{base}/tasks', 'viewer'); matrix(f'{base}/task-runs', 'viewer')
    for name in ['viewer', 'dev']: request(f'{base}/tasks', tok[name], {'name': 'x', 'command': 'echo ok', 'schedule': '*/15 * * * *'}, status=403)
    task = request(f'{base}/tasks', tok['admin'], {'name': 'Nettoyage', 'command': 'echo ok', 'schedule': '*/15 * * * *', 'enabled': False}, status=201)['task']
    request(f'{base}/tasks/{task["id"]}', tok['dev'], {'name': 'Renommée'}, method='PATCH', status=403)
    request(f'{base}/tasks/{task["id"]}', tok['owner'], {'name': 'Renommée'}, method='PATCH')
    request(f'{base}/tasks/{task["id"]}/run', tok['viewer'], {}, status=403)
    request(f'{legacy}/tasks/{task["id"]}/run', tok['dev'], {}, status=404)
    run = request(f'{base}/tasks/{task["id"]}/run', tok['dev'], {}, status=202)['run']; assert run['status'] == 'queued'
    request(f'{base}/tasks/{task["id"]}', tok['dev'], method='DELETE', status=403)
    # The task belongs to the app: the author's demotion does not cancel it (spec §6).
    request(f'{members}/{member["admin"]["id"]}', tok['owner'], {'role': 'viewer'}, method='PATCH')
    job = private('_tasks/claim', {})['job']; assert job and job['id'] == run['id'], job
    request(f'{members}/{member["admin"]["id"]}', tok['owner'], {'role': 'admin'}, method='PATCH')
    checks.append('tasks_viewer_history_developer_manual_run_admin_definitions_settings_survive_author')

    # Alerts: recipients are only shown to alert managers; delivery history is viewer-readable.
    # Recipients must be verified project members (or superAdmins), hence a member address here.
    matrix(f'{base}/notifications', 'admin'); matrix(f'{base}/notifications/history', 'viewer')
    for name in ['viewer', 'dev']:
        request(f'{base}/notifications', tok[name], {'revision': '', 'email': {'enabled': False, 'recipients': ['owner@example.invalid']}}, method='PUT', status=403)
        request(f'{base}/notifications/test', tok[name], {'channel': 'email'}, status=403)
    saved = request(f'{base}/notifications', tok['admin'], {'revision': '', 'email': {'enabled': False, 'recipients': ['owner@example.invalid']}}, method='PUT')
    assert saved['email']['recipients'] == ['owner@example.invalid'], saved
    request(f'{base}/notifications/test', tok['admin'], {'channel': 'webhook'}, status=400)  # past the guard: channel not configured
    checks.append('alerts_recipients_admin_only_history_viewer_test_admin')

    # CI tokens: admin manages them; the bearer re-checks the author's CURRENT ci.manage right on each call.
    matrix(f'{base}/deploy-tokens', 'admin')
    request(f'{base}/deploy-tokens', tok['dev'], {'name': 'CI', 'imageRepository': 'ghcr.io/acme/site'}, status=403)
    created = request(f'{base}/deploy-tokens', tok['admin'], {'name': 'CI', 'imageRepository': 'ghcr.io/acme/site'}, status=201)
    bearer = 'Bearer ' + created['token']
    payload = {'image': 'ghcr.io/acme/site@sha256:' + 'a' * 64, 'commit': 'b' * 40, 'requestId': 'run-1'}
    assert call('/api/web-apps/ci/deploy', bearer, payload)[0] != 401
    request(f'{members}/{member["admin"]["id"]}', tok['owner'], {'role': 'developer'}, method='PATCH')
    # Demoted below admin: the author can no longer list or revoke the token, so it stops deploying.
    assert request('/api/web-apps/ci/deploy', bearer, payload, status=401)['message'] == 'Accès de déploiement révoqué.'
    request(f'{members}/{member["admin"]["id"]}', tok['owner'], {'role': 'viewer'}, method='PATCH')
    assert request('/api/web-apps/ci/deploy', bearer, payload, status=401)['message'] == 'Accès de déploiement révoqué.'
    request(f'{members}/{member["admin"]["id"]}', tok['owner'], {'role': 'admin'}, method='PATCH')
    assert call('/api/web-apps/ci/deploy', bearer, payload)[0] != 401
    request(f'{members}/{member["admin"]["id"]}', tok['owner'], method='DELETE')
    request('/api/web-apps/ci/deploy', bearer, payload, status=401)
    member['admin'] = request(members, tok['owner'], {'email': 'admin@example.invalid', 'role': 'admin'}, status=201)['member']
    request(f'{base}/deploy-tokens/{created["item"]["id"]}', tok['dev'], method='DELETE', status=403)
    request(f'{base}/deploy-tokens/{created["item"]["id"]}', tok['admin'], method='DELETE')
    checks.append('ci_tokens_admin_only_bearer_revalidates_author_ci_manage_on_each_call')

    # Terminal: admin only; the broker revalidation closes the session once the author loses terminal.open.
    matrix(f'{base}/terminal/sessions', 'admin')
    for name in ['viewer', 'dev']: request(f'{base}/terminal/session', tok[name], {}, status=403)
    request(f'{base}/terminal/session', tok['outsider'], {}, status=404); request(f'{legacy}/terminal/session', tok['owner'], {}, status=404)
    issued = request(f'{base}/terminal/session', tok['admin'], {'shell': 'sh'}, status=201)
    claim = private('_terminal/claim', {'token': issued['token']}); assert claim['actorId'] == uid['admin']
    lease = {'sessionId': claim['sessionId'], 'proof': claim['proof'], 'containerId': 'f' * 64}
    assert private('_terminal/validate', lease)['valid']
    request(f'{members}/{member["admin"]["id"]}', tok['owner'], {'role': 'developer'}, method='PATCH')
    assert not private('_terminal/validate', lease)['valid']
    request(f'{members}/{member["admin"]["id"]}', tok['owner'], {'role': 'admin'}, method='PATCH')
    pending = request(f'{base}/terminal/session', tok['admin'], {}, status=201)
    request(f'{members}/{member["admin"]["id"]}', tok['owner'], method='DELETE')
    request('/api/web-apps/_terminal/claim', body={'token': pending['token']}, status=403, headers={'X-Web-Worker': worker_secret})
    request(f'{base}/terminal/sessions/{pending["sessionId"]}/close', tok['dev'], {}, status=403)
    request(f'{base}/terminal/sessions/{pending["sessionId"]}/close', tok['owner'], {})
    checks.append('terminal_admin_only_claim_and_periodic_validate_follow_current_role')

    # Platform-only areas: members of any role are refused before any lookup.
    for path, method, body in [('/api/web-platform-recovery', None, None), ('/api/web-platform-recovery/runs', None, None), ('/api/web-apps/github/connect/status', None, None), ('/api/web-apps/github/connect/start', 'POST', {})]:
        for name in ['owner', 'viewer', 'outsider', 'suspended']: assert call(path, tok[name], body, method)[0] == 403, (path, name)
        assert call(path, '', body, method)[0] == 401, path
        assert call(path, platform, body, method)[0] not in (401, 403), path
    checks.append('recovery_and_github_connect_platform_only')
    print(json.dumps({'passed': True, 'checks': checks}), flush=True)
except Exception:
    if (root / 'process.log').exists(): print((root / 'process.log').read_text()[-3000:])
    raise
finally:
    if process:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
    temporary.cleanup()
