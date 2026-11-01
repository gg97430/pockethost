#!/usr/bin/env python3
"""Real disposable PocketBase: per-project web roles enforced on every Applications web route.

Registers every routerAdd of webApps/hooks.ts (no crons, no record hooks), then checks each permission tier on
representative routes of every handler file, list filtering, cross-project IDOR, member escalation, last owner,
platform-only routes, the repository allowlist and the re-check of long-lived credentials (CI token, terminal).
No Docker, no SSH, no GitHub: denied calls stop at the guard, allowed calls use inputs rejected before any I/O.
"""
import argparse, json, os, pathlib, re, secrets, shutil, socket, subprocess, tempfile, time, urllib.error, urllib.parse, urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True, type=pathlib.Path)
parser.add_argument('--pocketbase', required=True)
args = parser.parse_args()
os.umask(0o077)
temporary = tempfile.TemporaryDirectory(prefix='ph-web-access-')
root = pathlib.Path(temporary.name).resolve()
process = None
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
origin = f'http://127.0.0.1:{port}'
worker_secret = secrets.token_urlsafe(40)
DENY, PLATFORM, LEGACY_PLATFORM = 'Droits insuffisants sur ce projet.', 'Action réservée aux administrateurs de la plateforme.', 'Acces superadmin requis.'
HIDDEN = {'Application introuvable.', 'Projet introuvable.'}
ROLES = ['viewer', 'developer', 'admin', 'owner']
Z = 'z' * 15


def call(method, path, token='', body=None, private=False):
    if body is None and method in ('POST', 'PUT', 'PATCH'): body = {}
    headers = {'Content-Type': 'application/json', **({'Authorization': token} if token else {}), **({'X-Web-Worker': worker_secret} if private else {})}
    value = urllib.request.Request(origin + path, method=method, headers=headers, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(value, timeout=30) as response:
            data = response.read(); return response.status, (json.loads(data) if data else None)
    except urllib.error.HTTPError as error:
        data = error.read()
        try: return error.code, json.loads(data) if data else None
        except ValueError: return error.code, {'message': data.decode(errors='replace')}


def request(path, token='', body=None, method=None, status=200, private=False):
    code, data = call(method or ('POST' if body is not None else 'GET'), path, token, body, private)
    assert code == status, f'{method or ""} {path}: {code} != {status}: {json.dumps(data, ensure_ascii=False)[:600]}'
    return data


def message(data): return (data or {}).get('message', '') if isinstance(data, dict) else ''


def denied(who, method, path, body=None):
    code, data = call(method, path, tok[who], body)
    assert code == 403 and message(data) == DENY, f'{who} {method} {path}: expected 403 role denial, got {code} {message(data)}'


def platform_only(who, method, path, body=None):
    code, data = call(method, path, tok[who], body)
    assert code == 403 and message(data) in (PLATFORM, LEGACY_PLATFORM), f'{who} {method} {path}: expected 403 platform-only, got {code} {message(data)}'


def hidden(who, method, path, body=None):
    code, data = call(method, path, tok[who], body)
    assert code == 404 and message(data) in HIDDEN, f'{who} {method} {path}: expected uniform 404, got {code} {message(data)}'
    return message(data)


def allowed(who, method, path, body=None):
    """Past the guard: anything but 401, a role/platform 403 or the uniform 404 (validation errors are fine)."""
    code, data = call(method, path, tok[who], body)
    blocked = code == 401 or (code == 403 and message(data) in (DENY, PLATFORM, LEGACY_PLATFORM, 'Accès refusé.')) or (code == 404 and message(data) in HIDDEN)
    assert not blocked, f'{who} {method} {path}: expected the guard to pass, got {code} {message(data)}'
    return code, data


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks = root / 'hooks'; hooks.mkdir(); shutil.copy(source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    # Every web route exactly as hooks.ts registers it (method, path, handler, auth middleware).
    registered = re.findall(r"routerAdd\(\s*'(\w+)',\s*'([^']+)',\s*\(e\)\s*=>\s*require\(`\$\{__hooks\}/mothership`\)\.(\w+)\(e\)\s*(,\s*\$apis\.requireAuth\('users'\))?\s*,?\s*\)", (source / 'src/lib/handlers/webApps/hooks.ts').read_text())
    assert len(registered) > 100, len(registered)
    middleware = ',$apis.requireAuth("users")'
    text = ''.join(f'routerAdd({json.dumps(m)},{json.dumps(p)},e=>require(`${{__hooks}}/mothership`).{h}(e){middleware if auth else ""});\n' for m, p, h, auth in registered)
    (hooks / 'test.pb.js').write_text(text)
    env = {**os.environ, 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_MASTER_KEY': 'k' * 32, 'WEB_APPS_WORKER_SECRET': worker_secret, 'WEB_APPS_ROOT': str(root / 'web'), 'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid', 'WEB_APPS_PLATFORM_ALIASES': 'shop.example.invalid', 'WEB_APPS_PUBLIC_IP': '203.0.113.10'}
    (root / 'web').mkdir(exist_ok=True)
    (root / 'web/worker-status.json').write_text(json.dumps({'httpsReady': True}))
    password, email = 'P' + secrets.token_urlsafe(32), 'access@example.invalid'  # never starts with '-' (CLI flag)
    data_dir = root / 'pb_data'
    subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(data_dir), '--hooksDir', str(root / 'empty-hooks'), '--migrationsDir', str(source / 'pb_migrations')], env=env, check=True, capture_output=True)
    log = (root / 'process.log').open('wb')
    process = subprocess.Popen([args.pocketbase, 'serve', '--dir', str(data_dir), '--hooksDir', str(hooks), '--migrationsDir', str(source / 'pb_migrations'), '--http', f'127.0.0.1:{port}'], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(150):
        if process.poll() is not None: raise RuntimeError('PocketBase failed to start')
        try: request('/api/health'); break
        except OSError: time.sleep(.1)
    su = request('/api/collections/_superusers/auth-with-password', body={'identity': email, 'password': password})['token']
    checks = []

    def user(name, admin=False):
        row = request('/api/collections/users/records', su, {'email': name + '@example.invalid', 'username': name, 'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': admin, 'subscription': 'premium', 'subscription_quantity': 1})
        return row, request('/api/collections/users/auth-with-password', body={'identity': row['email'], 'password': password})['token']
    names = ['platform', 'owner', 'admin', 'developer', 'viewer', 'outsider', 'suspended', 'newcomer']
    accounts = {name: user(name, name == 'platform') for name in names}
    tok = {name: value[1] for name, value in accounts.items()}; uid = {name: value[0]['id'] for name, value in accounts.items()}

    def app(name, repository):
        return request('/api/collections/web_apps/records', su, {'name': name, 'uid': uid['platform'], 'status': 'stopped', 'config': {'repository': repository, 'branch': 'main', 'rootDirectory': '.', 'healthPath': '/', 'generatedDomain': 'web-' + secrets.token_hex(6) + '.example.invalid'}})['id']
    a1, b1, legacy = app('Site A', 'team-a/site'), app('Site B', 'team-b/private'), app('Legacy', 'team-a/site')
    project_a = request(f'/api/web-apps/{a1}/project', tok['platform'], {'name': 'Équipe A', 'environment': 'prod'}, status=201)['project']['id']
    project_b = request(f'/api/web-apps/{b1}/project', tok['platform'], {'name': 'Équipe B', 'environment': 'prod'}, status=201)['project']['id']
    members_a, members_b = f'/api/web-projects/{project_a}/members', f'/api/web-projects/{project_b}/members'

    def add(path, who, target, role, status=201): return request(path, tok[who], {'email': target + '@example.invalid', 'role': role}, status=status)
    owner_row = add(members_a, 'platform', 'owner', 'owner')['member']
    admin_row = add(members_a, 'owner', 'admin', 'admin')['member']
    developer_row = add(members_a, 'admin', 'developer', 'developer')['member']
    viewer_row = add(members_a, 'admin', 'viewer', 'viewer')['member']
    add(members_a, 'owner', 'suspended', 'admin'); outsider_row = add(members_b, 'platform', 'outsider', 'owner')['member']
    request('/api/collections/users/records/' + uid['suspended'], su, {'suspension': 'abuse'}, method='PATCH')
    # Environments manage (admin): a second app in project A, created by a project admin without superAdmin.
    a2 = request(f'/api/web-apps/{a1}/environments/clone', tok['admin'], {'environment': 'staging'}, status=201)['app']['id']
    # Published container for A1 (terminal, logs).
    dep = request('/api/collections/web_deployments/records', su, {'appId': a1, 'action': 'deploy', 'status': 'ready'})['id']
    request('/api/collections/web_deployments/records/' + dep, su, {'result': {'container': f'ph-web-{a1}-{dep}', 'image': f'ph-web/{a1}:{dep}'}}, method='PATCH')
    request('/api/collections/web_apps/records/' + a1, su, {'activeDeployment': dep, 'status': 'running'}, method='PATCH')
    dep_b = request('/api/collections/web_deployments/records', su, {'appId': b1, 'action': 'deploy', 'status': 'ready'})['id']
    env_a1 = request(f'/api/collections/web_app_environments/records?filter=' + urllib.parse.quote(f'appId="{a1}"'), su)['items'][0]['id']
    env_b1 = request(f'/api/collections/web_app_environments/records?filter=' + urllib.parse.quote(f'appId="{b1}"'), su)['items'][0]['id']
    request('/api/collections/web_project_members/records', tok['owner'], status=403)
    checks.append('fixture_two_projects_four_roles_admin_clone_without_superadmin')

    # 1. Lists only show accessible projects/apps; the dashboard contract follows the same roles.
    def app_ids(who): return {row['id'] for row in request('/api/web-apps', tok[who])['items']}
    assert app_ids('viewer') == {a1, a2} and app_ids('owner') == {a1, a2} and app_ids('outsider') == {b1}
    assert app_ids('newcomer') == set() and {a1, a2, b1, legacy} <= app_ids('platform')
    listed = request('/api/web-apps', tok['viewer'])['items']
    assert all(row['access']['role'] == 'viewer' and row['access']['projectId'] == project_a and row['access']['capabilities']['app.read'] and not row['access']['capabilities']['app.deploy'] for row in listed)
    assert next(row for row in request('/api/web-apps', tok['platform'])['items'] if row['id'] == legacy)['access']['role'] == 'platform'
    request('/api/web-apps', tok['suspended'], status=403); request('/api/web-apps', status=401)
    projects = request('/api/web-projects', tok['developer'])['items']
    assert [row['id'] for row in projects] == [project_a] and projects[0]['role'] == 'developer' and projects[0]['capabilities']['variables.write'] and not projects[0]['capabilities']['variables.reveal']
    assert [row['id'] for row in request('/api/web-projects', tok['outsider'])['items']] == [project_b]
    assert request('/api/web-projects', tok['newcomer'])['items'] == [] and {project_a, project_b} <= {row['id'] for row in request('/api/web-projects', tok['platform'])['items']}
    me = request('/api/web-access/me', tok['viewer']); assert me['hasAccess'] and not me['isPlatformAdmin'] and [(p['id'], p['role']) for p in me['projects']] == [(project_a, 'viewer')] and not me['globalCapabilities']['platform.github']
    me = request('/api/web-access/me', tok['newcomer']); assert not me['hasAccess'] and me['projects'] == []
    me = request('/api/web-access/me', tok['suspended']); assert not me['hasAccess'] and me['projects'] == []
    me = request('/api/web-access/me', tok['platform']); assert me['isPlatformAdmin'] and me['hasAccess'] and all(me['globalCapabilities'].values())
    detail = request(f'/api/web-apps/{a1}', tok['developer']); assert detail['access']['role'] == 'developer' and detail['access']['capabilities']['app.deploy'] and not detail['access']['capabilities']['terminal.open']
    checks.append('lists_filtered_by_membership_access_view_and_me_contract')

    # 2. Every user route classified, then each permission tier checked on every handler file.
    A, P = f'/api/web-apps/{a1}', f'/api/web-projects/{project_a}'
    matrix = [
        # api.ts
        ('viewer', 'GET', '/api/web-apps/{id}', A, None), ('viewer', 'GET', '/api/web-apps/{id}/deployments', A + '/deployments', None),
        ('logs', 'GET', '/api/web-apps/{id}/deployments/{deploymentId}/logs', f'{A}/deployments/{dep}/logs', None),
        ('viewer', 'GET', '/api/web-apps/{id}/runtime', A + '/runtime', None), ('viewer', 'GET', '/api/web-apps/{id}/webhooks', A + '/webhooks', None),
        ('developer', 'POST', '/api/web-apps/{id}/{action}', A + '/unknown-action', {}),
        ('developer', 'POST', '/api/web-apps/{id}/deployments/{deploymentId}/cancel', f'{A}/deployments/{dep}/cancel', {}),
        ('developer', 'POST', '/api/web-apps/{id}/deployments/{deploymentId}/retry', f'{A}/deployments/{dep}/retry', {}),
        ('viewer', 'PATCH', '/api/web-apps/{id}', A, {}),
        ('admin', 'GET', '/api/web-apps/repositories/{repositoryId}/branches', f'/api/web-apps/repositories/987654/branches?appId={a1}', None),
        # domains.ts
        ('viewer', 'GET', '/api/web-apps/{id}/domains', A + '/domains', None), ('admin', 'POST', '/api/web-apps/{id}/domains', A + '/domains', {'hostname': ''}),
        ('admin', 'POST', '/api/web-apps/{id}/domains/{domainId}/verify', f'{A}/domains/{Z}/verify', {}), ('admin', 'DELETE', '/api/web-apps/{id}/domains/{domainId}', f'{A}/domains/{Z}', None),
        # environment.ts
        ('viewer', 'GET', '/api/web-apps/{id}/environment', A + '/environment', None), ('admin', 'POST', '/api/web-apps/{id}/environment/reveal', A + '/environment/reveal', {}),
        ('developer', 'POST', '/api/web-apps/{id}/environment/preview', A + '/environment/preview', {'bogus': True}), ('developer', 'PUT', '/api/web-apps/{id}/environment', A + '/environment', {'bogus': True}),
        # terminal.ts
        ('admin', 'POST', '/api/web-apps/{id}/terminal/session', A + '/terminal/session', {'shell': 'zsh'}), ('admin', 'GET', '/api/web-apps/{id}/terminal/sessions', A + '/terminal/sessions', None),
        ('admin', 'POST', '/api/web-apps/{id}/terminal/sessions/{sessionId}/close', f'{A}/terminal/sessions/{Z}/close', {}),
        # apply.ts
        ('developer', 'POST', '/api/web-apps/{id}/apply-runtime', A + '/apply-runtime', {'bogus': True}),
        # health.ts
        ('viewer', 'GET', '/api/web-apps/{id}/health', A + '/health', None), ('viewer', 'GET', '/api/web-apps/{id}/health/history', A + '/health/history', None),
        ('admin', 'PATCH', '/api/web-apps/{id}/health', A + '/health', {'bogus': True}),
        # scheduledTasks.ts
        ('viewer', 'GET', '/api/web-apps/{id}/tasks', A + '/tasks', None), ('viewer', 'GET', '/api/web-apps/{id}/task-runs', A + '/task-runs', None),
        ('admin', 'POST', '/api/web-apps/{id}/tasks', A + '/tasks', {'bogus': True}), ('admin', 'PATCH', '/api/web-apps/{id}/tasks/{taskId}', f'{A}/tasks/{Z}', {'bogus': True}),
        ('admin', 'DELETE', '/api/web-apps/{id}/tasks/{taskId}', f'{A}/tasks/{Z}', None), ('developer', 'POST', '/api/web-apps/{id}/tasks/{taskId}/run', f'{A}/tasks/{Z}/run', {}),
        # backups.ts
        ('viewer', 'GET', '/api/web-apps/{id}/backups', A + '/backups', None), ('developer', 'POST', '/api/web-apps/{id}/backups', A + '/backups', {'bogus': True}),
        ('admin', 'PUT', '/api/web-apps/{id}/backups/settings', A + '/backups/settings', {'bogus': True}), ('admin', 'DELETE', '/api/web-apps/{id}/backups/{backupId}', f'{A}/backups/{Z}', None),
        ('admin', 'GET', '/api/web-apps/{id}/backups/{backupId}/download', f'{A}/backups/{Z}/download', None),
        ('admin', 'POST', '/api/web-apps/{id}/backups/{backupId}/restore/preview', f'{A}/backups/{Z}/restore/preview', {}),
        ('admin', 'POST', '/api/web-apps/{id}/backups/{backupId}/restore', f'{A}/backups/{Z}/restore', {}), ('admin', 'POST', '/api/web-apps/{id}/backups/{backupId}/recover', f'{A}/backups/{Z}/recover', {}),
        # observability.ts
        ('logs', 'GET', '/api/web-apps/{id}/runtime-logs', A + '/runtime-logs', None),
        # notifications.ts (settings list recipient e-mails: notifications.manage, see report)
        ('admin', 'GET', '/api/web-apps/{id}/notifications', A + '/notifications', None), ('viewer', 'GET', '/api/web-apps/{id}/notifications/history', A + '/notifications/history', None),
        ('admin', 'PUT', '/api/web-apps/{id}/notifications', A + '/notifications', {'bogus': True}), ('admin', 'POST', '/api/web-apps/{id}/notifications/test', A + '/notifications/test', {'bogus': True}),
        # environments.ts
        ('viewer', 'GET', '/api/web-apps/{id}/environments', A + '/environments', None), ('admin', 'POST', '/api/web-apps/{id}/environments/clone', A + '/environments/clone', {'environment': 'bogus'}),
        ('developer', 'POST', '/api/web-apps/{id}/environments/promotion/preview', A + '/environments/promotion/preview', {'targetAppId': a2}),
        ('developer', 'POST', '/api/web-apps/{id}/environments/promote', A + '/environments/promote', {'targetAppId': a2}),
        # projects.ts
        ('admin', 'PATCH', '/api/web-projects/{projectId}', P, {'bogus': True}), ('viewer', 'GET', '/api/web-projects/{projectId}/variables/{environment}', P + '/variables/prod', None),
        # projectGroups.ts (project A still has sites: the allowed call stops at 400 « contient encore », nothing deleted)
        ('owner', 'DELETE', '/api/web-projects/{projectId}', P, None),
        ('admin', 'POST', '/api/web-projects/{projectId}/variables/{environment}/reveal', P + '/variables/prod/reveal', {}),
        ('developer', 'POST', '/api/web-projects/{projectId}/variables/{environment}/preview', P + '/variables/prod/preview', {'bogus': True}),
        ('developer', 'PUT', '/api/web-projects/{projectId}/variables/{environment}', P + '/variables/prod', {'bogus': True}),
        # previews.ts
        ('developer', 'POST', '/api/web-projects/{projectId}/previews/{number}/refresh', P + '/previews/abc/refresh', {}),
        ('developer', 'POST', '/api/web-projects/{projectId}/previews/{previewId}/close', f'{P}/previews/{env_a1}/close', {}),
        # runtimes.ts
        ('viewer', 'GET', '/api/web-apps/{id}/runtime/profile', A + '/runtime/profile', None), ('admin', 'PATCH', '/api/web-apps/{id}/runtime/profile', A + '/runtime/profile', {'bogus': True}),
        ('admin', 'POST', '/api/web-apps/{id}/runtime/profile/preview', A + '/runtime/profile/preview', {'bogus': True}),
        # storage.ts
        ('viewer', 'GET', '/api/web-apps/{id}/storage', A + '/storage', None), ('viewer', 'GET', '/api/web-apps/{id}/storage/history', A + '/storage/history', None),
        ('admin', 'PATCH', '/api/web-apps/{id}/storage', A + '/storage', {'bogus': True}), ('admin', 'POST', '/api/web-apps/{id}/storage/{name}/{operation}', A + '/storage/missing/backup', {}),
        ('admin', 'POST', '/api/web-apps/{id}/storage/{name}/restore/preview', A + '/storage/missing/restore/preview', {}),
        # ci.ts
        ('admin', 'GET', '/api/web-apps/{id}/deploy-tokens', A + '/deploy-tokens', None), ('admin', 'POST', '/api/web-apps/{id}/deploy-tokens', A + '/deploy-tokens', {'bogus': True}),
        ('admin', 'DELETE', '/api/web-apps/{id}/deploy-tokens/{tokenId}', f'{A}/deploy-tokens/{Z}', None),
        # members.ts
        ('viewer', 'GET', '/api/web-projects/{projectId}/members', P + '/members', None), ('admin', 'GET', '/api/web-projects/{projectId}/members/audit', P + '/members/audit', None),
        ('admin', 'POST', '/api/web-projects/{projectId}/members', P + '/members', {'email': 'not-an-email', 'role': 'viewer'}),
        ('admin', 'PATCH', '/api/web-projects/{projectId}/members/{memberId}', f'{P}/members/{Z}', {'role': 'viewer'}),
        ('admin', 'DELETE', '/api/web-projects/{projectId}/members/{memberId}', f'{P}/members/{Z}', None),
    ]
    # Platform-only: every project role (owner included) gets 403. 'run' => also executed once by the superAdmin.
    platform = [
        ('GET', '/api/web-apps/cloudflare/settings', '/api/web-apps/cloudflare/settings', None, True),
        ('PUT', '/api/web-apps/cloudflare/settings', '/api/web-apps/cloudflare/settings', {'bogus': True}, False),
        ('POST', '/api/web-apps/{id}/domains/{domainId}/cloudflare/preview', f'{A}/domains/{Z}/cloudflare/preview', {}, False),
        ('POST', '/api/web-apps/{id}/domains/{domainId}/cloudflare/apply', f'{A}/domains/{Z}/cloudflare/apply', {}, False),
        ('POST', '/api/web-apps', '/api/web-apps', {}, True), ('GET', '/api/web-apps/settings', '/api/web-apps/settings', None, True),
        ('PUT', '/api/web-apps/settings', '/api/web-apps/settings', {'bogus': True}, False), ('GET', '/api/web-apps/repositories', '/api/web-apps/repositories', None, False),
        ('POST', '/api/web-apps/github/connect/start', '/api/web-apps/github/connect/start', {}, False), ('POST', '/api/web-apps/github/connect/manifest', '/api/web-apps/github/connect/manifest', {}, False),
        ('POST', '/api/web-apps/github/connect/install/start', '/api/web-apps/github/connect/install/start', {}, False),
        ('POST', '/api/web-apps/github/connect/install', '/api/web-apps/github/connect/install', {}, False),
        ('POST', '/api/web-apps/github/connect/install/check', '/api/web-apps/github/connect/install/check', {}, False),
        ('GET', '/api/web-apps/github/connect/status', '/api/web-apps/github/connect/status', None, True), ('GET', '/api/web-apps/github/health', '/api/web-apps/github/health', None, False),
        # Several GitHub App connections (platform settings only; 403 before 404 for an unknown id).
        ('GET', '/api/web-apps/github/connections', '/api/web-apps/github/connections', None, True),
        ('POST', '/api/web-apps/github/connections', '/api/web-apps/github/connections', {}, False),
        ('PATCH', '/api/web-apps/github/connections/{connectionId}', '/api/web-apps/github/connections/zzzzzzzzzzzzzzz', {'label': 'X'}, False),
        ('DELETE', '/api/web-apps/github/connections/{connectionId}', '/api/web-apps/github/connections/zzzzzzzzzzzzzzz', None, False),
        ('POST', '/api/web-apps/{id}/project', A + '/project', {'name': 'Nouveau', 'environment': 'dev'}, True),
        ('POST', '/api/web-apps/{id}/project/attach', A + '/project/attach', {'projectId': project_b, 'environment': 'dev'}, True),
        ('DELETE', '/api/web-apps/{id}/project', A + '/project', None, False),
        ('POST', '/api/web-projects', '/api/web-projects', {'name': ''}, False),
        ('GET', '/api/web-platform-recovery', '/api/web-platform-recovery', None, False), ('GET', '/api/web-platform-recovery/runs', '/api/web-platform-recovery/runs', None, False),
        ('PUT', '/api/web-platform-recovery/settings', '/api/web-platform-recovery/settings', {'bogus': True}, False),
        ('POST', '/api/web-platform-recovery/captures', '/api/web-platform-recovery/captures', {}, False),
        ('POST', '/api/web-platform-recovery/captures/{backupId}/drill', f'/api/web-platform-recovery/captures/{Z}/drill', {}, False),
        # Platform alert channel (superAdmins only; the per-app channels keep notifications.manage / alerts.test).
        ('GET', '/api/web-platform-notifications', '/api/web-platform-notifications', None, False),
        ('PUT', '/api/web-platform-notifications', '/api/web-platform-notifications', {'bogus': True}, False),
        ('POST', '/api/web-platform-notifications/test', '/api/web-platform-notifications/test', {'channel': 'email'}, False),
        ('GET', '/api/web-platform-notifications/history', '/api/web-platform-notifications/history', None, False),
        ('GET', '/api/web-access/users/{userId}/projects', f'/api/web-access/users/{uid["viewer"]}/projects', None, True),
        ('PUT', '/api/web-access/users/{userId}/projects', f'/api/web-access/users/{uid["viewer"]}/projects', {'bogus': True}, True),
    ]
    # Authenticated routes whose access is not a single project tier (lists, me, leave): covered by dedicated checks.
    special = {('GET', '/api/web-apps'), ('GET', '/api/web-projects'), ('GET', '/api/web-access/me'), ('POST', '/api/web-projects/{projectId}/members/leave')}
    # Site groups: grouped list (membership filter) and attach/detach/move (app.attach/app.detach, app.move on both
    # projects): every role × case is checked by scripts/test-web-project-groups-api.py.
    special |= {('GET', '/api/web-projects/groups'), ('PUT', '/api/web-apps/{id}/project')}
    # Git deploy-key sources: target = web_git_sources.projectId (not an app/project path id); every route × role is
    # checked by scripts/test-web-git-source-api.py.
    special |= {('POST', '/api/web-git-sources'), ('GET', '/api/web-git-sources/{sourceId}'), ('DELETE', '/api/web-git-sources/{sourceId}'), ('GET', '/api/web-git-sources/{sourceId}/branches'), ('POST', '/api/web-git-sources/{sourceId}/{action}')}
    classified = {(m, t) for _, m, t, _, _ in matrix} | {(m, t) for m, t, _, _, _ in platform} | special
    unclassified = sorted(f'{m} {p}' for m, p, _, auth in registered if auth and (m, p) not in classified)
    assert not unclassified, 'routes without a permission check in this test: ' + ', '.join(unclassified)
    tier = {'logs': 'viewer'}
    for minimum, method, template, path, body in matrix:
        need = tier.get(minimum, minimum)
        for role in ROLES[:ROLES.index(need)]: denied(role, method, path, body)
        allowed(need, method, path, body)
        if method == 'GET': allowed('owner', method, path, body)
        # A superAdmin would reach GitHub for any branch listing: never from a test.
        if method == 'GET' and '/repositories/' not in template: allowed('platform', method, path, body)
        if '/web-projects/' in template and '/previews/{previewId}' not in template:
            assert hidden('outsider', method, path, body) == 'Projet introuvable.', path
        elif template != '/api/web-apps/repositories/{repositoryId}/branches': hidden('outsider', method, path, body)
        code, data = call(method, path, tok['suspended'], body); assert code == 403, (path, code)
        code, data = call(method, path, '', body); assert code == 401, (path, code)
    for method, template, path, body, run in platform:
        for role in ROLES + ['outsider']: platform_only(role, method, path, body)
        if run: allowed('platform', method, path, body)
    checks.append(f'{len(matrix)}_project_routes_each_tier_denied_below_allowed_at_min_outsider_404_and_{len(platform)}_platform_routes_403_for_owners')

    # 3. Cross-project IDOR: ids of another team behave exactly like unknown ids, sub-resources stay bound to their app.
    missing = hidden('owner', 'GET', f'/api/web-apps/{Z}')
    for target in [b1, legacy]:
        assert hidden('owner', 'GET', f'/api/web-apps/{target}') == missing
        assert hidden('owner', 'GET', f'/api/web-apps/{target}/environment') == missing
        assert hidden('owner', 'POST', f'/api/web-apps/{target}/terminal/session', {}) == missing
    assert hidden('owner', 'GET', f'/api/web-projects/{project_b}/variables/prod') == hidden('owner', 'GET', f'/api/web-projects/{Z}/variables/prod') == 'Projet introuvable.'
    hidden('owner', 'POST', f'{P}/previews/{env_b1}/close', {})
    hidden('outsider', 'GET', f'/api/web-apps/{a1}/deployments/{dep}/logs')
    code, _ = call('GET', f'{A}/deployments/{dep_b}/logs', tok['owner']); assert code in (400, 403, 404), code
    code, _ = call('POST', f'{A}/environments/promotion/preview', tok['owner'], {'targetAppId': b1}); assert code in (400, 403, 404), code
    request(f'{members_a}/{outsider_row["id"]}', tok['owner'], {'role': 'viewer'}, method='PATCH', status=404)
    request(f'{members_a}/{outsider_row["id"]}', tok['owner'], method='DELETE', status=404)
    request(members_b, tok['owner'], status=404); request(f'/api/web-access/users/{uid["outsider"]}/projects', tok['owner'], status=403)
    platform_only('admin', 'GET', '/api/web-apps/repositories/987654/branches')  # no ?appId=: GitHub-wide listing
    hidden('outsider', 'GET', f'/api/web-apps/repositories/987654/branches?appId={a1}')
    code, data = call('GET', f'/api/web-apps/repositories/987654/branches?appId={a1}', tok['admin']); assert code == 403 and message(data) not in (DENY, PLATFORM), (code, data)
    checks.append('cross_project_ids_uniform_404_member_rows_and_branches_bound_to_own_app')

    # 4. Secrets: variable names for viewers, values only for admin+ (reveal), writes for developer+.
    request(A, tok['developer'], {'env': [{'name': 'API_SECRET', 'value': 'private-app-value', 'build': False, 'runtime': True}]}, method='PATCH')
    names_only = request(A + '/environment', tok['viewer']); assert 'API_SECRET' in json.dumps(names_only) and 'private-app-value' not in json.dumps(names_only)
    assert 'private-app-value' not in json.dumps(request(A, tok['viewer'])) and 'private-app-value' not in json.dumps(request('/api/web-apps', tok['viewer']))
    denied('developer', 'POST', A + '/environment/reveal', {})
    assert 'private-app-value' in json.dumps(request(A + '/environment/reveal', tok['admin'], {}))
    shared = P + '/variables/prod'; revision = request(shared, tok['developer'])['revision']
    body = {'revision': revision, 'text': "SHARED_TOKEN='private-shared-value'", 'mode': 'replace'}
    preview = request(shared + '/preview', tok['developer'], body); assert 'private-shared-value' not in json.dumps(preview)
    request(shared, tok['developer'], {**body, 'previewToken': preview['previewToken']}, method='PUT')
    masked = request(shared, tok['viewer']); assert 'SHARED_TOKEN' in json.dumps(masked) and 'private-shared-value' not in json.dumps(masked)
    denied('developer', 'POST', shared + '/reveal', {})
    assert 'private-shared-value' in json.dumps(request(shared + '/reveal', tok['admin'], {}))
    checks.append('variable_names_for_viewers_values_for_admins_writes_for_developers')

    # 5. PATCH composite and the repository allowlist (the shared GitHub App must not leak another team's repository).
    request(A, tok['viewer'], {}, method='PATCH'); denied('viewer', 'PATCH', A, {'name': 'Renommé'})
    denied('developer', 'PATCH', A, {'branch': 'release'}); denied('developer', 'PATCH', A, {'autoDeploy': not request(A, tok['viewer'])['app']['autoDeploy']})
    assert request(A, tok['admin'], {'branch': 'release'}, method='PATCH')['app']['branch'] == 'release'
    request(A, tok['admin'], {'branch': 'main'}, method='PATCH')
    for who in ['admin', 'owner']: platform_only(who, 'PATCH', A, {'repository': 'team-b/private'})
    request('/api/collections/web_projects/records/' + project_a, su, {'allowedRepositories': ['team-a/site-next']}, method='PATCH')
    platform_only('owner', 'PATCH', A, {'repository': 'team-b/private'}); assert call('PATCH', A, tok['developer'], {'repository': 'team-a/site-next'})[0] == 403
    code, data = call('PATCH', A, tok['admin'], {'repository': 'team-a/site-next'})
    assert code in (200, 400) and message(data) not in (DENY, PLATFORM, LEGACY_PLATFORM), (code, data)
    if code == 200: request(A, tok['platform'], {'repository': 'team-a/site'}, method='PATCH')
    assert request(f'/api/web-apps/{a1}', tok['platform'])['app']['repository'] == 'team-a/site'
    checks.append('composite_patch_permissions_and_repository_allowlist_platform_only_outside_it')

    # 6. Members: escalation, admin cannot touch admin/owner rows, always one owner, project rename for owners.
    add(members_a, 'admin', 'newcomer', 'admin', 403); add(members_a, 'admin', 'newcomer', 'owner', 403); add(members_a, 'developer', 'newcomer', 'viewer', 403)
    request(f'{members_a}/{admin_row["id"]}', tok['admin'], {'role': 'viewer'}, method='PATCH', status=403)
    request(f'{members_a}/{owner_row["id"]}', tok['admin'], method='DELETE', status=403)
    request(f'{members_a}/{viewer_row["id"]}', tok['admin'], {'role': 'admin'}, method='PATCH', status=403)
    request(f'{members_a}/{owner_row["id"]}', tok['owner'], {'role': 'admin'}, method='PATCH', status=400)
    request(f'{members_a}/leave', tok['owner'], {}, status=400); request(f'{members_a}/{owner_row["id"]}', tok['platform'], method='DELETE', status=400)
    unknown = message(add(members_a, 'owner', 'nobody', 'viewer', 400)); assert message(add(members_a, 'owner', 'suspended', 'viewer', 400)) == unknown
    newcomer_row = add(members_a, 'owner', 'newcomer', 'owner')['member']
    request(f'{members_a}/{newcomer_row["id"]}', tok['owner'], {'role': 'viewer'}, method='PATCH')
    request(f'{members_a}/leave', tok['newcomer'], {}); hidden('newcomer', 'GET', A)
    settings = {'name': 'Équipe A', 'previewEnabled': False, 'previewMax': 1, 'previewTtlHours': 1}
    request(P, tok['admin'], settings, method='PATCH'); denied('admin', 'PATCH', P, {**settings, 'name': 'Équipe A2'})
    assert request(P, tok['owner'], {**settings, 'name': 'Équipe A2'}, method='PATCH')['project']['name'] == 'Équipe A2'
    audit = {row['action'] for row in request(f'{members_a}/audit', tok['admin'])['items']}; assert {'member-added', 'member-role-changed', 'member-left'} <= audit
    checks.append('member_escalation_last_owner_generic_unknown_account_and_owner_only_rename')

    # 7. Long-lived credentials re-check the actor's CURRENT role: CI token (ci.manage) and terminal (terminal.open).
    runtime = f'/api/web-apps/{a2}/runtime/profile'; image_repo = 'ghcr.io/team-a/site'
    request(runtime, tok['admin'], {'profile': {'kind': 'image', 'image': image_repo + '@sha256:' + 'a' * 64}, 'revision': request(runtime, tok['admin'])['revision']}, method='PATCH')
    # Image-only engine (no GitHub source), as a CI pipeline publishes it: nothing below reaches GitHub.
    config = request('/api/collections/web_apps/records/' + a2, su)['config']
    request('/api/collections/web_apps/records/' + a2, su, {'config': {**config, 'repository': '', 'repositoryId': 0, 'autoDeploy': False}}, method='PATCH')
    denied('developer', 'POST', f'/api/web-apps/{a2}/deploy-tokens', {'name': 'CI', 'imageRepository': image_repo, 'expiresInDays': 7})
    issued = request(f'/api/web-apps/{a2}/deploy-tokens', tok['admin'], {'name': 'CI', 'imageRepository': image_repo, 'expiresInDays': 7}, status=201); bearer = 'Bearer ' + issued['token']
    def ci(number): return {'image': image_repo + '@sha256:' + format(number, '064x'), 'commit': 'b' * 40, 'branch': 'main', 'requestId': f'workflow:{number}'}
    request('/api/web-apps/ci/deploy', bearer, ci(1), status=202)
    request(f'{members_a}/{admin_row["id"]}', tok['owner'], {'role': 'viewer'}, method='PATCH')
    request('/api/web-apps/ci/deploy', bearer, ci(2), status=401)
    request(f'{members_a}/{admin_row["id"]}', tok['owner'], {'role': 'admin'}, method='PATCH')
    request('/api/web-apps/ci/deploy', bearer, ci(3), status=202)
    # Terminal: grant, broker claim, periodic validation; demotion and removal both revoke the live session.
    def terminal():
        issued = request(A + '/terminal/session', tok['admin'], {}, status=201)
        claim = request('/api/web-apps/_terminal/claim', body={'token': issued['token']}, private=True)
        return {'sessionId': claim['sessionId'], 'proof': claim['proof'], 'containerId': 'f' * 64}
    def valid(lease): return request('/api/web-apps/_terminal/validate', body=lease, private=True)['valid']
    lease = terminal(); assert valid(lease)
    request(f'{members_a}/{admin_row["id"]}', tok['owner'], {'role': 'developer'}, method='PATCH'); assert not valid(lease)
    denied('admin', 'POST', A + '/terminal/session', {})
    request(f'{members_a}/{admin_row["id"]}', tok['owner'], {'role': 'admin'}, method='PATCH')
    lease = terminal(); assert valid(lease)
    request(f'{members_a}/{admin_row["id"]}', tok['owner'], method='DELETE'); assert not valid(lease)
    request('/api/web-apps/ci/deploy', bearer, ci(4), status=401); hidden('admin', 'GET', A)
    checks.append('ci_token_and_terminal_session_revoked_by_demotion_or_removal')

    # 8. Suspension cuts every remaining access immediately, including for an owner.
    request('/api/collections/users/records/' + uid['owner'], su, {'suspension': 'abuse'}, method='PATCH')
    request(A, tok['owner'], status=403); request(members_a, tok['owner'], status=403)
    me = request('/api/web-access/me', tok['owner']); assert me['projects'] == [] and not me['hasAccess']
    checks.append('suspended_owner_loses_access')

    # 9. Spec: the CI token is re-checked against ci.manage (admin), like token creation. A developer cannot create or
    # revoke tokens, so a token created before a demotion to developer must stop working too.
    request(members_a, tok['platform'], {'email': 'admin@example.invalid', 'role': 'admin'}, status=201)
    admin_row = next(row for row in request(members_a, tok['platform'])['items'] if row['userId'] == uid['admin'])
    bearer = 'Bearer ' + request(f'/api/web-apps/{a2}/deploy-tokens', tok['admin'], {'name': 'CI 2', 'imageRepository': image_repo, 'expiresInDays': 7}, status=201)['token']
    request('/api/web-apps/ci/deploy', bearer, ci(5), status=202)
    request(f'{members_a}/{admin_row["id"]}', tok['platform'], {'role': 'developer'}, method='PATCH')
    request(f'/api/web-apps/{a2}/deploy-tokens', tok['admin'], status=403)
    request('/api/web-apps/ci/deploy', bearer, ci(6), status=401)
    checks.append('ci_token_revoked_when_author_loses_ci_manage')

    # 10. Security review regressions (cross-project leaks once project admins got these routes).
    request(f'{members_a}/{admin_row["id"]}', tok['platform'], {'role': 'admin'}, method='PATCH')
    # 10a. Operator-curated platform aliases need no DNS proof: only the platform allocates (or verifies) them.
    alias = 'shop.example.invalid'
    platform_only('admin', 'POST', A + '/domains', {'hostname': alias}); platform_only('outsider', 'POST', f'/api/web-apps/{b1}/domains', {'hostname': alias})
    alias_row = request(A + '/domains', tok['platform'], {'hostname': alias}, status=201)['domain']; assert not alias_row['ownershipRequired']
    platform_only('admin', 'POST', f'{A}/domains/{alias_row["id"]}/verify', {})
    request(f'{A}/domains/{alias_row["id"]}', tok['admin'], method='DELETE')
    checks.append('platform_alias_claim_and_verify_platform_only')
    # 10b. An unproven (pending_dns) claim never holds a hostname against another tenant; the first proof wins.
    victim = 'api.victim.example.org'
    pending_a = request(A + '/domains', tok['admin'], {'hostname': victim}, status=201)['domain']; assert pending_a['status'] == 'pending_dns'
    pending_b = request(f'/api/web-apps/{b1}/domains', tok['outsider'], {'hostname': victim}, status=201)['domain']
    request(A + '/domains', tok['admin'], {'hostname': victim}, status=400)  # the same application twice
    request('/api/collections/web_app_domains/records/' + pending_a['id'], su, {'status': 'active'}, method='PATCH')
    code, _ = call('PATCH', '/api/collections/web_app_domains/records/' + pending_b['id'], su, {'status': 'active'}); assert code == 400, code  # partial unique index
    code, data = call('POST', f'/api/web-apps/{b1}/domains/{pending_b["id"]}/verify', tok['outsider'], {}); assert code == 400 and message(data) == 'Ce domaine n’est pas disponible.', (code, data)
    request(f'/api/web-apps/{b1}/domains/{pending_b["id"]}', tok['outsider'], method='DELETE')
    request(f'/api/web-apps/{b1}/domains', tok['outsider'], {'hostname': victim}, status=400)
    checks.append('pending_domain_claims_do_not_block_other_tenants_first_proof_wins')
    # 10c. Without variables.reveal, the preview diff is the same for a right and a wrong guess (app and shared).
    def guess(who, value):
        revision = request(A + '/environment', tok[who])['revision']
        out = request(A + '/environment/preview', tok[who], {'revision': revision, 'mode': 'merge', 'rows': [{'name': 'API_SECRET', 'value': value}]})
        return out['changes'], out['requiresRebuild']
    assert guess('developer', 'private-app-value') == guess('developer', 'wrong-guess') and 'API_SECRET' in guess('developer', 'private-app-value')[0]['updated']
    assert 'API_SECRET' in guess('admin', 'private-app-value')[0]['preserved']
    def shared_guess(who, value):
        revision = request(shared, tok[who])['revision']
        return request(shared + '/preview', tok[who], {'revision': revision, 'text': f"SHARED_TOKEN='{value}'", 'mode': 'merge'})['changes']
    assert shared_guess('developer', 'private-shared-value') == shared_guess('developer', 'nope') and 'SHARED_TOKEN' in shared_guess('admin', 'private-shared-value')['preserved']
    checks.append('variable_preview_never_confirms_a_guessed_value_without_reveal')
    # 10d. Member additions are rate limited per project admin (no mass probing of which e-mails have an account).
    codes = [call('POST', members_a, tok['admin'], {'email': f'ghost{i}@example.invalid', 'role': 'viewer'})[0] for i in range(25)]
    assert set(codes) <= {400, 429} and codes[-1] == 429, codes
    request(members_a, tok['platform'], {'email': 'ghost-platform@example.invalid', 'role': 'viewer'}, status=400)
    checks.append('member_additions_rate_limited_per_project_admin')
    # 10e. An app moved from team B to team A never replays team B's history, image or shared secrets.
    shared_b = f'/api/web-projects/{project_b}/variables/prod'; body_b = {'revision': request(shared_b, tok['outsider'])['revision'], 'text': "TEAM_B_SECRET='team-b-only'", 'mode': 'replace'}
    request(shared_b, tok['outsider'], {**body_b, 'previewToken': request(shared_b + '/preview', tok['outsider'], body_b)['previewToken']}, method='PUT')
    old_ready = request(f'/api/web-apps/{b1}/stop', tok['outsider'], {}, status=202)['deployment']['id']
    old_failed = request(f'/api/web-apps/{b1}/stop', tok['outsider'], {}, status=202)['deployment']['id']
    request('/api/collections/web_deployments/records/' + old_ready, su, {'action': 'deploy', 'status': 'ready', 'commit': 'a' * 40, 'result': {'image': f'ph-web/{b1}:{old_ready}'}}, method='PATCH')
    request('/api/collections/web_deployments/records/' + old_failed, su, {'action': 'deploy', 'status': 'failed', 'commit': 'a' * 40}, method='PATCH')
    request('/api/collections/web_apps/records/' + b1, su, {'activeDeployment': old_ready, 'status': 'running'}, method='PATCH')
    request(f'/api/web-apps/{b1}/project', tok['platform'], method='DELETE')
    config_b = request('/api/collections/web_apps/records/' + b1, su)['config']
    request('/api/collections/web_apps/records/' + b1, su, {'config': {**config_b, 'repository': 'team-a/site'}}, method='PATCH')
    attach = {'projectId': project_a, 'environment': 'dev', 'slug': 'moved'}
    code, data = call('POST', f'/api/web-apps/{b1}/project/attach', tok['platform'], attach); assert code == 400 and 'Arrêtez' in message(data), (code, data)
    request('/api/collections/web_apps/records/' + b1, su, {'status': 'stopped'}, method='PATCH')
    request(f'/api/web-apps/{b1}/project/attach', tok['platform'], attach)
    assert 'team-b-only' not in json.dumps(request(f'/api/web-apps/{b1}/environment/reveal', tok['admin'], {})) and 'TEAM_B_SECRET' not in json.dumps(request(f'/api/web-apps/{b1}/environment', tok['viewer']))
    hidden('outsider', 'GET', f'/api/web-apps/{b1}')
    def foreign(who, method, path, body=None):
        code, data = call(method, path, tok[who], body); assert code == 403 and 'autre projet' in message(data), (who, path, code, data)
    foreign('developer', 'POST', f'/api/web-apps/{b1}/deployments/{old_failed}/retry', {})
    foreign('developer', 'POST', f'/api/web-apps/{b1}/rollback', {'deploymentId': old_ready})
    foreign('developer', 'POST', f'/api/web-apps/{b1}/start', {})
    foreign('developer', 'POST', f'/api/web-apps/{b1}/backups', {})
    checks.append('moved_app_strips_old_shared_secrets_and_never_replays_foreign_history')
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
