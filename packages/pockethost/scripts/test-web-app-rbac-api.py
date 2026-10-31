#!/usr/bin/env python3
"""RBAC for api.ts/apply/source/domains/environment routes against a disposable PocketBase."""
import argparse, json, os, pathlib, secrets, shutil, socket, subprocess, tempfile, time, urllib.error, urllib.request
parser = argparse.ArgumentParser(); parser.add_argument('--source', required=True, type=pathlib.Path); parser.add_argument('--pocketbase', required=True); args = parser.parse_args()
temporary = tempfile.TemporaryDirectory(prefix='ph-web-rbac-'); root = pathlib.Path(temporary.name).resolve(); process = None
with socket.socket() as sock: sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
origin = f'http://127.0.0.1:{port}'
def request(path, token='', body=None, method=None, status=200):
    value = urllib.request.Request(origin + path, method=method or ('POST' if body is not None else 'GET'), headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {})}, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(value, timeout=25) as response:
            data = response.read(); assert response.status == status, (path, response.status, status, data); return json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        data = error.read()
        if error.code == status: return json.loads(data) if data else None
        raise AssertionError(f'{method or ""} {path}: {error.code} != {status}: ' + data.decode()) from None
try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks = root / 'hooks'; hooks.mkdir(); shutil.copy(source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    routes = [('GET','/api/web-apps','HandleWebAppsList'),('POST','/api/web-apps','HandleWebAppCreate'),('GET','/api/web-apps/settings','HandleWebSettingsGet'),('GET','/api/web-apps/repositories','HandleWebRepositories'),('GET','/api/web-apps/repositories/{repositoryId}/branches','HandleWebBranches'),('GET','/api/web-apps/github/health','HandleWebGithubHealth'),
      ('GET','/api/web-apps/{id}','HandleWebAppGet'),('PATCH','/api/web-apps/{id}','HandleWebAppUpdate'),('GET','/api/web-apps/{id}/deployments','HandleWebDeployments'),('GET','/api/web-apps/{id}/deployments/{deploymentId}/logs','HandleWebDeploymentLogs'),('POST','/api/web-apps/{id}/{action}','HandleWebAppAction'),
      ('POST','/api/web-apps/{id}/deployments/{deploymentId}/cancel','HandleWebDeploymentCancel'),('GET','/api/web-apps/{id}/runtime','HandleWebRuntime'),('GET','/api/web-apps/{id}/webhooks','HandleWebWebhookDeliveries'),
      ('GET','/api/web-apps/{id}/domains','HandleWebDomainsList'),('POST','/api/web-apps/{id}/domains','HandleWebDomainCreate'),('DELETE','/api/web-apps/{id}/domains/{domainId}','HandleWebDomainDelete'),
      ('GET','/api/web-apps/{id}/environment','HandleWebEnvironmentGet'),('POST','/api/web-apps/{id}/environment/reveal','HandleWebEnvironmentReveal'),('POST','/api/web-apps/{id}/environment/preview','HandleWebEnvironmentPreview'),('PUT','/api/web-apps/{id}/environment','HandleWebEnvironmentPut'),
      ('POST','/api/web-apps/{id}/apply-runtime','HandleWebApplyRuntime'),('POST','/api/web-apps/{id}/project','HandleWebAppProjectCreate'),('POST','/api/web-projects/{projectId}/members','HandleWebProjectMemberAdd')]
    (hooks / 'test.pb.js').write_text(''.join(f'routerAdd({json.dumps(m)},{json.dumps(p)},e=>require(`${{__hooks}}/mothership`).{h}(e),$apis.requireAuth("users"));\n' for m, p, h in routes))
    env = {**os.environ, 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_MASTER_KEY': 'p' * 32, 'WEB_APPS_WORKER_SECRET': secrets.token_urlsafe(40), 'WEB_APPS_ROOT': str(root / 'web'), 'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid'}
    password, email = 'P' + secrets.token_urlsafe(32), 'rbac@example.invalid'; data = root / 'pb_data'
    subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(data), '--hooksDir', str(root / 'empty'), '--migrationsDir', str(source / 'pb_migrations')], env=env, check=True, capture_output=True)
    log = (root / 'process.log').open('wb')
    process = subprocess.Popen([args.pocketbase, 'serve', '--dir', str(data), '--hooksDir', str(hooks), '--migrationsDir', str(source / 'pb_migrations'), '--http', f'127.0.0.1:{port}'], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        if process.poll() is not None: raise RuntimeError('PocketBase failed to start')
        try: request('/api/health'); break
        except OSError: time.sleep(.1)
    su = request('/api/collections/_superusers/auth-with-password', body={'identity': email, 'password': password})['token']
    def user(name, admin=False):
        row = request('/api/collections/users/records', su, {'email': name + '@example.invalid', 'username': name, 'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': admin, 'subscription': 'premium', 'subscription_quantity': 1})
        return row, request('/api/collections/users/auth-with-password', body={'identity': row['email'], 'password': password})['token']
    prow, platform = user('platform', True)
    tok = {n: user(n)[1] for n in ['admin', 'dev', 'viewer', 'outsider']}
    def app(name, repo='owner/repo', rid=7):
        return request('/api/collections/web_apps/records', su, {'name': name, 'uid': prow['id'], 'status': 'stopped', 'config': {'repository': repo, 'repositoryId': rid, 'branch': 'main', 'rootDirectory': '.', 'healthPath': '/', 'generatedDomain': 'web-' + secrets.token_hex(6) + '.example.invalid'}})['id']
    app_id, legacy_id = app('Projet'), app('Legacy')
    project_id = request(f'/api/web-apps/{app_id}/project', platform, {'name': 'Équipe', 'environment': 'prod'}, status=201)['project']['id']
    for n, r in [('admin', 'admin'), ('dev', 'developer'), ('viewer', 'viewer')]:
        request(f'/api/web-projects/{project_id}/members', platform, {'email': n + '@example.invalid', 'role': r}, status=201)
    A = f'/api/web-apps/{app_id}'; checks = []
    # List filtering + access view
    items = request('/api/web-apps', tok['viewer'])['items']; assert [i['id'] for i in items] == [app_id] and items[0]['access']['role'] == 'viewer' and not items[0]['access']['capabilities']['app.deploy'], items
    assert request('/api/web-apps', tok['outsider'])['items'] == []
    assert {i['id'] for i in request('/api/web-apps', platform)['items']} == {app_id, legacy_id}
    request('/api/web-apps', status=401)
    checks.append('list_filtered_by_membership')
    # Uniform 404 / platform-only
    for t in ['outsider', 'viewer']: request(f'/api/web-apps/{legacy_id}', tok[t], status=404)
    request(A, tok['outsider'], status=404); request('/api/web-apps/zzzzzzzzzzzzzzz', tok['outsider'], status=404); request('/api/web-apps/bad', tok['outsider'], status=400)
    g = request(A, tok['viewer']); assert g['access']['role'] == 'viewer' and g['app']['id'] == app_id
    assert request(A, platform)['access']['role'] == 'platform'
    for path in ['/api/web-apps/settings', '/api/web-apps/repositories', '/api/web-apps/github/health']: request(path, tok['admin'], status=403)
    request('/api/web-apps', tok['admin'], {'name': 'x'}, status=403)
    request('/api/web-apps/repositories/7/branches', tok['admin'], status=403)
    request(f'/api/web-apps/repositories/8/branches?appId={app_id}', tok['admin'], status=403)
    request(f'/api/web-apps/repositories/7/branches?appId={app_id}', tok['dev'], status=403)
    request(f'/api/web-apps/repositories/7/branches?appId={legacy_id}', tok['admin'], status=404)
    request(f'/api/web-apps/repositories/7/branches?appId={app_id}', tok['admin'], status=400)  # GitHub not configured -> reader error, but authorized
    checks.append('uniform_404_platform_only_branches_scoped')
    # Read routes for viewer
    for path in [f'{A}/deployments', f'{A}/runtime', f'{A}/webhooks', f'{A}/domains']: request(path, tok['viewer'])
    request(f'{A}/deployments', tok['outsider'], status=404)
    # PATCH composite
    request(A, tok['viewer'], {'name': 'Projet'}, method='PATCH')  # unchanged -> app.read
    request(A, tok['viewer'], {'env': [{'name': 'A', 'value': '1', 'runtime': True}]}, method='PATCH', status=403)
    request(A, tok['dev'], {'env': [{'name': 'SECRET', 'value': 'hunter2', 'runtime': True}], 'name': 'Projet', 'branch': 'main'}, method='PATCH')
    request(A, tok['dev'], {'name': 'Renommé'}, method='PATCH', status=403); request(A, tok['dev'], {'branch': 'dev'}, method='PATCH', status=403)
    request(A, tok['admin'], {'name': 'Renommé', 'branch': 'dev', 'autoDeploy': False}, method='PATCH')
    request(A, tok['admin'], {'repository': 'other/private'}, method='PATCH', status=403)
    request(A, tok['admin'], {'repository': 'OWNER/repo'}, method='PATCH')  # same repo, case only
    request(A, platform, {'repository': 'owner/repo', 'repositoryId': 7}, method='PATCH')
    checks.append('patch_composite_permissions')
    # Variables: names for viewer, reveal admin only
    envd = request(f'{A}/environment', tok['viewer']); assert 'hunter2' not in json.dumps(envd) and [v['name'] for v in envd['variables']] == ['SECRET']
    request(f'{A}/environment/reveal', tok['dev'], {}, status=403); request(f'{A}/environment/reveal', tok['viewer'], {}, status=403)
    assert 'hunter2' in json.dumps(request(f'{A}/environment/reveal', tok['admin'], {}))
    request(f'{A}/environment/preview', tok['viewer'], {}, status=403)
    body = {'revision': envd['revision'], 'mode': 'merge', 'text': 'B=2\n'}
    pv = request(f'{A}/environment/preview', tok['dev'], body); assert 'hunter2' not in json.dumps(pv)
    request(f'{A}/environment', tok['dev'], {**body, 'previewToken': pv['previewToken']}, method='PUT')
    request(f'{A}/environment', tok['admin'], {**body, 'previewToken': pv['previewToken']}, method='PUT', status=400)  # token bound to actor
    checks.append('variables_names_only_reveal_admin')
    # Deploy actions / apply / domains
    request(f'{A}/deploy', tok['viewer'], {}, status=403); request(f'{A}/apply-runtime', tok['viewer'], {}, status=403)
    request(f'{A}/stop', tok['dev'], {}, status=202)
    dep = request(f'{A}/deployments', tok['viewer'])['items'][0]['id']
    request(f'{A}/deployments/{dep}/logs', tok['viewer']); request(f'{A}/deployments/{dep}/cancel', tok['viewer'], {}, status=403)
    request(f'{A}/deployments/{dep}/cancel', tok['dev'], {}, status=400)  # authorized; stop jobs are not cancellable
    request(f'{A}/apply-runtime', tok['dev'], {}, status=400)
    request(f'{A}/domains', tok['dev'], {'hostname': 'shop.example.org'}, status=403)
    dom = request(f'{A}/domains', tok['admin'], {'hostname': 'shop.example.org'}, status=201)['domain']['id']
    request(f'{A}/domains/{dom}', tok['dev'], method='DELETE', status=403); request(f'{A}/domains/{dom}', tok['admin'], method='DELETE', status=400)  # authorized; queued stop job blocks
    checks.append('deploy_developer_domains_admin')
    print(json.dumps({'passed': True, 'checks': checks}))
except Exception:
    print('process log:', (root / 'process.log').read_text()[-3000:] if (root / 'process.log').exists() else ''); raise
finally:
    if process: process.terminate(); process.wait(10)
