#!/usr/bin/env python3
"""Disposable PocketBase: per-project roles on projects/environments/previews/runtimes/storage/backups routes."""
import argparse, json, os, pathlib, secrets, shutil, socket, subprocess, tempfile, time, urllib.error, urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True, type=pathlib.Path)
parser.add_argument('--pocketbase', required=True)
args = parser.parse_args()
os.umask(0o077)
temporary = tempfile.TemporaryDirectory(prefix='ph-web-rbac-g3-')
root = pathlib.Path(temporary.name).resolve()
process = None
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
origin = f'http://127.0.0.1:{port}'


def request(path, token='', body=None, method=None, status=200):
    value = urllib.request.Request(origin + path, method=method or ('POST' if body is not None else 'GET'), headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {})}, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(value, timeout=25) as response:
            assert response.status == status, (path, response.status, status)
            data = response.read(); return json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        data = error.read()
        if error.code == status: return json.loads(data) if data else None
        raise AssertionError(f'{method or ""} {path}: {error.code} != {status}: ' + data.decode()) from None


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks = root / 'hooks'; hooks.mkdir(); shutil.copy(source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    routes = [
        ('POST', '/api/web-projects/{projectId}/members', 'HandleWebProjectMemberAdd'),
        ('POST', '/api/web-apps/{id}/project', 'HandleWebAppProjectCreate'),
        ('POST', '/api/web-apps/{id}/project/attach', 'HandleWebAppProjectAttach'),
        ('DELETE', '/api/web-apps/{id}/project', 'HandleWebAppProjectDetach'),
        ('GET', '/api/web-apps/{id}/environments', 'HandleWebAppEnvironments'),
        ('POST', '/api/web-apps/{id}/environments/clone', 'HandleWebEnvironmentClone'),
        ('POST', '/api/web-apps/{id}/environments/promotion/preview', 'HandleWebEnvironmentPromotionPreview'),
        ('POST', '/api/web-apps/{id}/environments/promote', 'HandleWebEnvironmentPromote'),
        ('GET', '/api/web-projects', 'HandleWebProjectsList'),
        ('PATCH', '/api/web-projects/{projectId}', 'HandleWebProjectUpdate'),
        ('GET', '/api/web-projects/{projectId}/variables/{environment}', 'HandleWebProjectVariablesGet'),
        ('PUT', '/api/web-projects/{projectId}/variables/{environment}', 'HandleWebProjectVariablesPut'),
        ('POST', '/api/web-projects/{projectId}/variables/{environment}/reveal', 'HandleWebProjectVariablesReveal'),
        ('POST', '/api/web-projects/{projectId}/variables/{environment}/preview', 'HandleWebProjectVariablesPreview'),
        ('POST', '/api/web-projects/{projectId}/previews/{number}/refresh', 'HandleWebPreviewRefresh'),
        ('POST', '/api/web-projects/{projectId}/previews/{previewId}/close', 'HandleWebPreviewClose'),
        ('GET', '/api/web-apps/{id}/runtime/profile', 'HandleWebRuntimeGet'),
        ('PATCH', '/api/web-apps/{id}/runtime/profile', 'HandleWebRuntimeUpdate'),
        ('POST', '/api/web-apps/{id}/runtime/profile/preview', 'HandleWebRuntimePreview'),
        ('GET', '/api/web-apps/{id}/storage', 'HandleWebStorageGet'),
        ('PATCH', '/api/web-apps/{id}/storage', 'HandleWebStorageUpdate'),
        ('POST', '/api/web-apps/{id}/storage/{name}/{operation}', 'HandleWebStorageAction'),
        ('GET', '/api/web-apps/{id}/storage/history', 'HandleWebStorageHistory'),
        ('GET', '/api/web-apps/{id}/backups', 'HandleWebBackupsList'),
        ('POST', '/api/web-apps/{id}/backups', 'HandleWebBackupCreate'),
        ('PUT', '/api/web-apps/{id}/backups/settings', 'HandleWebBackupSettings'),
        ('DELETE', '/api/web-apps/{id}/backups/{backupId}', 'HandleWebBackupDelete'),
        ('GET', '/api/web-apps/{id}/backups/{backupId}/download', 'HandleWebBackupDownload'),
        ('GET', '/api/web-apps/{id}/notifications', 'HandleWebNotificationsGet'),
        ('PUT', '/api/web-apps/{id}/notifications', 'HandleWebNotificationsPut'),
        ('POST', '/api/web-apps/{id}/backups/{backupId}/restore/preview', 'HandleWebBackupRestorePreview'),
    ]
    text = ''.join(f'routerAdd({json.dumps(m)},{json.dumps(p)},e=>require(`${{__hooks}}/mothership`).{h}(e),$apis.requireAuth("users"));\n' for m, p, h in routes)
    (hooks / 'test.pb.js').write_text(text)
    env = {**os.environ, 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_MASTER_KEY': 'p' * 32, 'WEB_APPS_WORKER_SECRET': 'W' + secrets.token_urlsafe(40), 'WEB_APPS_ROOT': str(root / 'web'), 'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid'}
    password, email = 'P' + secrets.token_urlsafe(32), 'su@example.invalid'
    data = root / 'pb_data'
    subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(data), '--hooksDir', str(root / 'empty-hooks'), '--migrationsDir', str(source / 'pb_migrations')], env=env, check=True, capture_output=True)
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
    platform_row, platform = user('platform', True)
    tok = {name: user(name)[1] for name in ['owner', 'admin', 'dev', 'viewer', 'outsider']}

    def app(name, repo='owner/repo'):
        return request('/api/collections/web_apps/records', su, {'name': name, 'uid': platform_row['id'], 'status': 'stopped', 'config': {'repository': repo, 'branch': 'main', 'rootDirectory': '.', 'healthPath': '/', 'generatedDomain': 'web-' + secrets.token_hex(6) + '.example.invalid'}})['id']
    prod, staging, legacy, other = app('Prod'), app('Staging'), app('Legacy'), app('Autre', 'other/repo')
    project = request(f'/api/web-apps/{prod}/project', platform, {'name': 'Équipe A', 'environment': 'prod'}, status=201)['project']['id']
    other_project = request(f'/api/web-apps/{other}/project', platform, {'name': 'Équipe B', 'environment': 'prod'}, status=201)['project']['id']
    request(f'/api/web-apps/{staging}/project/attach', platform, {'projectId': project, 'environment': 'staging'})
    for name, role in [('owner', 'owner'), ('admin', 'admin'), ('dev', 'developer'), ('viewer', 'viewer')]:
        request(f'/api/web-projects/{project}/members', platform, {'email': name + '@example.invalid', 'role': role}, status=201)
    checks = []

    # Projects list: filtered, with effective role and capabilities; superAdmin unchanged.
    assert request('/api/web-projects', tok['outsider'])['items'] == []
    listed = request('/api/web-projects', tok['dev'])['items']
    assert [p['id'] for p in listed] == [project] and listed[0]['role'] == 'developer' and listed[0]['capabilities']['app.deploy'] and not listed[0]['capabilities']['variables.reveal']
    everything = request('/api/web-projects', platform)['items']
    assert {p['id'] for p in everything} == {project, other_project} and all(p['role'] == 'platform' for p in everything)
    request('/api/web-projects', status=401)
    # Project settings: admin edits previews, only owner renames, only superAdmin edits the allowlist.
    base = {'name': 'Équipe A', 'previewEnabled': False, 'previewMax': 3, 'previewTtlHours': 24}
    request(f'/api/web-projects/{project}', tok['dev'], base, method='PATCH', status=403)
    assert request(f'/api/web-projects/{project}', tok['admin'], {**base, 'previewMax': 2}, method='PATCH')['project']['previewMax'] == 2
    request(f'/api/web-projects/{project}', tok['admin'], {**base, 'name': 'Renommé'}, method='PATCH', status=403)
    # Dashboard shape for an admin who cannot rename: no name field, current name kept.
    nameless = {k: v for k, v in base.items() if k != 'name'}
    assert request(f'/api/web-projects/{project}', tok['admin'], {**nameless, 'previewMax': 3}, method='PATCH')['project']['name'] == 'Équipe A'
    assert request(f'/api/web-projects/{project}', tok['owner'], {**base, 'name': 'Renommé'}, method='PATCH')['project']['name'] == 'Renommé'
    base['name'] = 'Renommé'
    request(f'/api/web-projects/{project}', tok['owner'], {**base, 'allowedRepositories': ['acme/other']}, method='PATCH', status=403)
    request(f'/api/web-projects/{project}', tok['owner'], {**base, 'allowedRepositories': []}, method='PATCH')
    assert request(f'/api/web-projects/{project}', platform, {**base, 'allowedRepositories': ['Acme/Other', 'acme/other']}, method='PATCH')['project']['allowedRepositories'] == ['Acme/Other']
    request(f'/api/web-projects/{project}', tok['admin'], {**base, 'allowedRepositories': ['acme/other']}, method='PATCH')
    request(f'/api/web-projects/{project}', platform, {**base, 'allowedRepositories': ['bad']}, method='PATCH', status=400)
    request(f'/api/web-projects/{other_project}', tok['owner'], base, method='PATCH', status=404)
    checks.append('projects_list_filtered_rename_owner_allowlist_platform')

    # Shared variables: names for viewer, write for developer, reveal for admin only.
    vars_url = f'/api/web-projects/{project}/variables/prod'
    request(vars_url, tok['outsider'], status=404)
    current = request(vars_url, tok['viewer'])
    preview_body = {'revision': current['revision'], 'text': 'SECRET_KEY=hunter2\n', 'mode': 'merge'}
    request(vars_url + '/preview', tok['viewer'], preview_body, status=403)
    plan = request(vars_url + '/preview', tok['dev'], preview_body)
    assert 'hunter2' not in json.dumps(plan)
    saved = request(vars_url, tok['dev'], {**preview_body, 'previewToken': plan['previewToken']}, method='PUT')
    assert 'hunter2' not in json.dumps(saved) and [v['name'] for v in saved['variables']] == ['SECRET_KEY']
    assert 'hunter2' not in json.dumps(request(vars_url, tok['viewer']))
    request(vars_url + '/reveal', tok['dev'], {}, status=403)
    assert request(vars_url + '/reveal', tok['admin'], {})['variables'][0]['value'] == 'hunter2'
    checks.append('shared_variables_names_write_reveal_by_role')

    # Environments: viewer reads, outsider/legacy 404, platform-only project moves, admin clones, promotion needs app.deploy on both.
    assert len(request(f'/api/web-apps/{prod}/environments', tok['viewer'])['items']) == 2
    missing = request(f'/api/web-apps/{prod}/environments', tok['outsider'], status=404)['message']
    assert request(f'/api/web-apps/{legacy}/environments', tok['owner'], status=404)['message'] == missing
    assert request(f'/api/web-apps/{legacy}/environments', platform)['legacy'] is True
    request(f'/api/web-apps/{legacy}/project', tok['owner'], {'name': 'X', 'environment': 'prod'}, status=403)
    request(f'/api/web-apps/{legacy}/project/attach', tok['owner'], {'projectId': project, 'environment': 'dev'}, status=403)
    request(f'/api/web-apps/{staging}/project', tok['owner'], method='DELETE', status=403)
    request(f'/api/web-apps/{prod}/environments/clone', tok['dev'], {'environment': 'dev', 'slug': 'dev'}, status=403)
    cloned = request(f'/api/web-apps/{prod}/environments/clone', tok['admin'], {'environment': 'dev', 'slug': 'dev'}, status=201)['app']
    assert request(f'/api/web-apps/{cloned["id"]}/environments', tok['viewer'])['membership']['projectId'] == project
    request(f'/api/web-apps/{prod}/environments/promotion/preview', tok['viewer'], {'targetAppId': staging}, status=403)
    request(f'/api/web-apps/{prod}/environments/promotion/preview', tok['dev'], {'targetAppId': other}, status=404)
    request(f'/api/web-apps/{prod}/environments/promotion/preview', tok['dev'], {'targetAppId': staging}, status=400)  # guard passed, no publication
    request(f'/api/web-apps/{prod}/environments/promote', tok['dev'], {'targetAppId': other}, status=404)
    checks.append('environments_read_platform_moves_admin_clone_promotion_both_sides')

    # Previews: developer refreshes/closes in its project only.
    request(f'/api/web-projects/{project}/previews/1/refresh', tok['viewer'], {}, status=403)
    assert request(f'/api/web-projects/{project}/previews/1/refresh', tok['dev'], {}, status=202)['accepted']
    request(f'/api/web-projects/{other_project}/previews/1/refresh', tok['dev'], {}, status=404)
    env_row = request(f'/api/collections/web_app_environments/records?filter=appId%3D%22{prod}%22', su)['items'][0]['id']
    other_env = request(f'/api/collections/web_app_environments/records?filter=appId%3D%22{other}%22', su)['items'][0]['id']
    request(f'/api/web-projects/{project}/previews/{env_row}/close', tok['dev'], {}, status=404)  # not a preview
    request(f'/api/web-projects/{project}/previews/{other_env}/close', tok['dev'], {}, status=404)
    request(f'/api/web-projects/{project}/previews/{env_row}/close', tok['viewer'], {}, status=403)
    checks.append('previews_manage_developer_scoped_to_project')

    # Runtime profile, storage and backups.
    profile = request(f'/api/web-apps/{prod}/runtime/profile', tok['viewer'])
    request(f'/api/web-apps/{prod}/runtime/profile', tok['outsider'], status=404)
    request(f'/api/web-apps/{prod}/runtime/profile', tok['dev'], {'profile': profile['profile'], 'revision': profile['revision']}, method='PATCH', status=403)
    request(f'/api/web-apps/{prod}/runtime/profile/preview', tok['dev'], {'profile': profile['profile']}, status=403)
    request(f'/api/web-apps/{prod}/runtime/profile/preview', tok['admin'], {'profile': profile['profile']})
    request(f'/api/web-apps/{prod}/runtime/profile', tok['admin'], {'profile': profile['profile'], 'revision': profile['revision']}, method='PATCH')
    storage = request(f'/api/web-apps/{prod}/storage', tok['viewer'])
    request(f'/api/web-apps/{prod}/storage/history', tok['viewer'])
    request(f'/api/web-apps/{legacy}/storage', tok['viewer'], status=404)
    request(f'/api/web-apps/{prod}/storage', tok['dev'], {'volumes': [], 'retention': 7, 'revision': storage['revision']}, method='PATCH', status=403)
    request(f'/api/web-apps/{prod}/storage', tok['admin'], {'volumes': [], 'retention': 7, 'revision': storage['revision']}, method='PATCH')
    request(f'/api/web-apps/{prod}/storage/data/backup', tok['dev'], {}, status=403)
    request(f'/api/web-apps/{prod}/storage/data/backup', tok['admin'], {}, status=400)  # guard passed, volume not registered
    request(f'/api/web-apps/{prod}/backups', tok['viewer'])
    request(f'/api/web-apps/{prod}/backups', tok['outsider'], status=404)
    request(f'/api/web-apps/{prod}/backups', tok['viewer'], {}, status=403)
    request(f'/api/web-apps/{prod}/backups', tok['dev'], {}, status=400)  # guard passed, nothing published
    settings = {'enabled': False, 'hourUTC': 3, 'minuteUTC': 0, 'retention': 3}
    request(f'/api/web-apps/{prod}/backups/settings', tok['dev'], settings, method='PUT', status=403)
    request(f'/api/web-apps/{prod}/backups/settings', tok['admin'], settings, method='PUT')
    request(f'/api/web-apps/{prod}/backups/zzzzzzzzzzzzzzz/download', tok['dev'], status=403)
    request(f'/api/web-apps/{prod}/backups/zzzzzzzzzzzzzzz/restore/preview', tok['dev'], {}, status=403)
    request(f'/api/web-apps/{prod}/backups/zzzzzzzzzzzzzzz', tok['dev'], method='DELETE', status=403)
    request(f'/api/web-apps/{prod}/backups/zzzzzzzzzzzzzzz', tok['admin'], method='DELETE', status=404)
    checks.append('runtime_storage_backups_by_role_legacy_404')

    # Alert channels belong to the team: moving an application to another customer's project clears the previous
    # team's webhook and recipients (pending deliveries are cancelled by the new revision); the same lineage keeps them.
    def alerts(ident): return request(f'/api/web-apps/{ident}/notifications', platform)
    def hook(ident, url):
        return request(f'/api/web-apps/{ident}/notifications', platform, {'revision': alerts(ident)['revision'], 'webhook': {'enabled': True, 'url': url, 'secret': 's' * 40}}, method='PUT')
    moved, kept = app('Déplacée', 'shared/repo'), app('Gardée', 'shared/repo')
    team_c = request(f'/api/web-apps/{app("Équipe C", "shared/repo")}/project', platform, {'name': 'Équipe C', 'environment': 'prod'}, status=201)['project']['id']
    team_d = request(f'/api/web-apps/{app("Équipe D", "shared/repo")}/project', platform, {'name': 'Équipe D', 'environment': 'prod'}, status=201)['project']['id']
    for ident in [moved, kept]:
        request(f'/api/web-apps/{ident}/project/attach', platform, {'projectId': team_c, 'environment': 'staging', 'slug': 'staging-' + ident[:5]})
    first = hook(moved, 'https://receiver.example.invalid/team-c'); hook(kept, 'https://receiver.example.invalid/team-c-kept')
    assert first['webhook']['enabled'] and first['webhook']['hasSecret']
    request(f'/api/web-apps/{moved}/project', platform, method='DELETE'); request(f'/api/web-apps/{kept}/project', platform, method='DELETE')
    assert alerts(moved)['webhook']['configured']  # Detached: still the same team's application until it joins another.
    request(f'/api/web-apps/{moved}/project/attach', platform, {'projectId': team_d, 'environment': 'staging'})
    after = alerts(moved)
    assert not after['webhook']['configured'] and not after['webhook']['enabled'] and not after['webhook']['hasSecret'] and not after['email']['enabled'], after
    assert after['revision'] and after['revision'] != first['revision'] and after['webhook']['format'] == 'generic'
    row = request(f'/api/collections/web_notification_settings/records?filter=appId%3D%22{moved}%22', su)['items'][0]
    assert row['enabled'] is False and row['activatedAt'] == ''
    audit = request(f'/api/collections/web_project_audit/records?perPage=50&filter=projectId%3D%22{team_d}%22', su)['items']
    assert any(item['action'] == 'application-attached' and item['details'].get('notificationsReset') is True for item in audit), audit
    request(f'/api/web-apps/{kept}/project/attach', platform, {'projectId': team_c, 'environment': 'staging', 'slug': 'staging-' + kept[:5]})
    assert alerts(kept)['webhook']['configured'] and alerts(kept)['webhook']['enabled']
    checks.append('team_change_clears_previous_team_alert_channels_same_lineage_keeps_them')
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
