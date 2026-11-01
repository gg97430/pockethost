#!/usr/bin/env python3
"""Real disposable PocketBase: per-project web roles, members API, escalation rules and central permission guard."""
import argparse, json, os, pathlib, secrets, shutil, socket, subprocess, tempfile, time, urllib.error, urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True, type=pathlib.Path)
parser.add_argument('--pocketbase', required=True)
args = parser.parse_args()
os.umask(0o077)
temporary = tempfile.TemporaryDirectory(prefix='ph-web-members-')
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
        ('GET', '/api/web-access/me', 'HandleWebAccessMe'),
        ('GET', '/api/web-projects/{projectId}/members', 'HandleWebProjectMembersList'),
        ('POST', '/api/web-projects/{projectId}/members', 'HandleWebProjectMemberAdd'),
        ('GET', '/api/web-projects/{projectId}/members/audit', 'HandleWebProjectMembersAudit'),
        ('POST', '/api/web-projects/{projectId}/members/leave', 'HandleWebProjectMemberLeave'),
        ('PATCH', '/api/web-projects/{projectId}/members/{memberId}', 'HandleWebProjectMemberUpdate'),
        ('DELETE', '/api/web-projects/{projectId}/members/{memberId}', 'HandleWebProjectMemberRemove'),
        ('POST', '/api/web-apps/{id}/project', 'HandleWebAppProjectCreate'),
        ('GET', '/api/web-access/users/{userId}/projects', 'HandleWebUserProjectsGet'),
        ('PUT', '/api/web-access/users/{userId}/projects', 'HandleWebUserProjectsPut'),
    ]
    text = ''.join(f'routerAdd({json.dumps(m)},{json.dumps(p)},e=>require(`${{__hooks}}/mothership`).{h}(e),$apis.requireAuth("users"));\n' for m, p, h in routes)
    # Direct probe of the central guard: {target, permission} -> {role, projectId, appId} or the guard's HTTP error.
    text += """routerAdd('POST','/test/permission',e=>{const m=require(`${__hooks}/mothership`),i=e.requestInfo().body,a=m.requireWebPermission(e,i.target,i.permission);return e.json(200,{role:a.role,memberRole:a.memberRole,projectId:a.project?a.project.id:'',appId:a.app?a.app.id:'',ids:m.listAccessibleWebAppIds(a.user),apps:(()=>{const q=m.webAppAccessQuery(a.user,'id','developer');return $app.findRecordsByFilter('web_apps',q.filter,'-created',100,0,q.params).map(r=>r.id)})(),projects:(()=>{const q=m.webProjectAccessQuery(a.user,'id');return $app.findRecordsByFilter('web_projects',q.filter,'-created',100,0,q.params).map(r=>r.id)})(),viewerApps:(()=>{const q=m.webAppAccessQuery(a.user,'id','admin');return $app.findRecordsByFilter('web_apps',q.filter,'',100,0,q.params).map(r=>r.id)})(),setting:i.settingAppId?m.webAppSettingActive(i.settingAppId,i.actorId):null,repo:i.repository!==undefined&&a.project?m.canSetWebRepository(a.project,a.user,i.repository,i.current||''):null,actor:i.actorId?m.webActorCan(i.actorId,i.target,i.permission):null,view:a.app?m.webAppAccessView(a.user,a.app):null})},$apis.requireAuth('users'));\n"""
    (hooks / 'test.pb.js').write_text(text)
    env = {**os.environ, 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_MASTER_KEY': 'p' * 32, 'WEB_APPS_WORKER_SECRET': secrets.token_urlsafe(40), 'WEB_APPS_ROOT': str(root / 'web'), 'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid'}
    password, email = 'P' + secrets.token_urlsafe(32), 'members@example.invalid'  # never starts with '-' (CLI flag)
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
    users = {name: user(name) for name in ['owner', 'owner2', 'admin', 'dev', 'viewer', 'outsider', 'suspended', 'unverified', 'assigned']}
    request('/api/collections/users/records/' + users['unverified'][0]['id'], su, {'verified': False}, method='PATCH')
    request('/api/collections/users/records/' + users['suspended'][0]['id'], su, {'suspension': 'abuse'}, method='PATCH')
    tok = {name: value[1] for name, value in users.items()}; uid = {name: value[0]['id'] for name, value in users.items()}

    def app(name):
        return request('/api/collections/web_apps/records', su, {'name': name, 'uid': platform_row['id'], 'status': 'stopped', 'config': {'repository': 'owner/repo', 'branch': 'main', 'rootDirectory': '.', 'healthPath': '/', 'generatedDomain': 'web-' + secrets.token_hex(6) + '.example.invalid'}})['id']
    app_id, legacy_id = app('Projet'), app('Legacy')
    project_id = request(f'/api/web-apps/{app_id}/project', platform, {'name': 'Équipe A', 'environment': 'prod'}, status=201)['project']['id']
    request('/api/collections/web_project_members/records', tok['owner'], status=403)
    checks = []

    # GET me: no memberships yet.
    me = request('/api/web-access/me', tok['owner']); assert me['enabled'] is True and me['isPlatformAdmin'] is False and me['hasAccess'] is False and me['projects'] == []
    me = request('/api/web-access/me', platform); assert me['isPlatformAdmin'] and me['allProjects'] and me['hasAccess'] and me['projects'] == [] and all(me['globalCapabilities'].values())
    request('/api/web-access/me', status=401)
    members = f'/api/web-projects/{project_id}/members'
    request(members, tok['owner'], status=404); request(members, tok['suspended'], status=403)
    checks.append('me_contract_platform_sees_all_no_implicit_membership')

    # Platform admin seeds the first owner; generic error for unknown or suspended accounts.
    owner_member = request(members, platform, {'email': 'OWNER@example.invalid', 'role': 'owner'}, status=201)['member']
    assert owner_member['userId'] == uid['owner'] and owner_member['role'] == 'owner' and owner_member['invitedBy']['id'] == platform_row['id']
    unknown = request(members, tok['owner'], {'email': 'nobody@example.invalid', 'role': 'viewer'}, status=400)['message']
    assert request(members, tok['owner'], {'email': 'suspended@example.invalid', 'role': 'viewer'}, status=400)['message'] == unknown
    assert request(members, tok['owner'], {'email': 'unverified@example.invalid', 'role': 'viewer'}, status=400)['message'] == unknown
    assert request(members, tok['owner'], {'email': 'example.invalid', 'role': 'viewer'}, status=400)['message'] != unknown
    assert request(members, tok['owner'], {'email': '%@example.invalid', 'role': 'viewer'}, status=400)['message'] == unknown
    request(members, tok['owner'], {'email': 'owner@example.invalid', 'role': 'viewer'}, status=400)
    request(members, tok['owner'], {'email': 'admin@example.invalid', 'role': 'root'}, status=400)
    admin_member = request(members, tok['owner'], {'email': 'admin@example.invalid', 'role': 'admin'}, status=201)['member']
    # Admin manages only lower roles and never admin/owner rows.
    request(members, tok['admin'], {'email': 'owner2@example.invalid', 'role': 'admin'}, status=403)
    request(members, tok['admin'], {'email': 'owner2@example.invalid', 'role': 'owner'}, status=403)
    dev_member = request(members, tok['admin'], {'email': 'dev@example.invalid', 'role': 'viewer'}, status=201)['member']
    dev_member = request(f'{members}/{dev_member["id"]}', tok['admin'], {'role': 'developer'}, method='PATCH')['member']; assert dev_member['role'] == 'developer'
    request(f'{members}/{dev_member["id"]}', tok['admin'], {'role': 'admin'}, method='PATCH', status=403)
    request(f'{members}/{admin_member["id"]}', tok['admin'], {'role': 'viewer'}, method='PATCH', status=403)
    request(f'{members}/{owner_member["id"]}', tok['admin'], method='DELETE', status=403)
    request(members, tok['dev'], {'email': 'viewer@example.invalid', 'role': 'viewer'}, status=403)
    viewer_member = request(members, tok['admin'], {'email': 'viewer@example.invalid', 'role': 'viewer'}, status=201)['member']
    listed = request(members, tok['viewer']); assert [row['role'] for row in listed['items']] == ['owner', 'admin', 'developer', 'viewer'] and listed['role'] == 'viewer' and not listed['capabilities']['members.manage']
    assert next(row for row in listed['items'] if row['self'])['userId'] == uid['viewer']
    request(f'{members}/audit', tok['viewer'], status=403)
    checks.append('generic_unknown_account_error_admin_cannot_escalate_or_touch_admin_owner_rows')

    # Last owner protection (change, remove, leave), then a second owner unlocks it.
    request(f'{members}/{owner_member["id"]}', tok['owner'], {'role': 'admin'}, method='PATCH', status=400)
    request(f'{members}/{owner_member["id"]}', platform, method='DELETE', status=400)
    request(f'{members}/leave', tok['owner'], {}, status=400)
    request(members, tok['owner'], {'email': 'owner2@example.invalid', 'role': 'owner'}, status=201)
    request(f'{members}/leave', tok['owner'], {})
    request(members, tok['owner'], status=404)
    request(f'{members}/leave', tok['viewer'], {}); request(f'{members}/leave', tok['viewer'], {}, status=404)
    request(f'{members}/{dev_member["id"]}', tok['owner2'], {'role': 'viewer'}, method='PATCH')
    request(f'{members}/{dev_member["id"]}', tok['owner2'], {'role': 'developer'}, method='PATCH')
    other_project = request(f'/api/web-apps/{app("Autre")}/project', platform, {'name': 'Équipe B', 'environment': 'dev'}, status=201)['project']['id']
    request(f'/api/web-projects/{other_project}/members/{dev_member["id"]}', platform, method='DELETE', status=404)
    audit = request(f'{members}/audit', tok['owner2']); assert {'member-added', 'member-role-changed', 'member-left'} <= {row['action'] for row in audit['items']}
    checks.append('always_one_owner_leave_and_cross_project_member_ids_rejected')

    # Central guard: roles, platform-only, legacy apps and non-enumerable ids.
    def perm(token, target, permission, status=200, **extra): return request('/test/permission', token, {'target': target, 'permission': permission, **extra}, status=status)
    assert perm(tok['dev'], {'appId': app_id}, 'app.deploy')['role'] == 'developer'
    perm(tok['dev'], {'appId': app_id}, 'variables.reveal', 403)
    perm(tok['admin'], {'appId': app_id}, 'variables.reveal')
    perm(tok['admin'], {'appId': app_id}, 'members.manageAdmins', 403)
    perm(tok['owner2'], {'appId': app_id}, 'app.repository', 403); perm(tok['owner2'], {'appId': 'zzzzzzzzzzzzzzz'}, 'app.attach', 403)
    missing = perm(tok['dev'], {'appId': 'zzzzzzzzzzzzzzz'}, 'app.read', 404)['message']
    assert perm(tok['dev'], {'appId': legacy_id}, 'app.read', 404)['message'] == missing
    assert perm(tok['outsider'], {'appId': app_id}, 'app.read', 404)['message'] == missing
    perm(tok['dev'], {'projectId': other_project}, 'project.read', 404); perm(tok['suspended'], {'appId': app_id}, 'app.read', 403)
    perm(tok['dev'], {'appId': 'bad'}, 'app.read', 400)
    probe = perm(platform, {'appId': legacy_id}, 'app.repository'); assert probe['role'] == 'platform' and probe['ids'] is None
    probe = perm(tok['dev'], {'appId': app_id}, 'app.read'); assert probe['ids'] == [app_id] and probe['apps'] == [app_id] and probe['viewerApps'] == [] and probe['projects'] == [project_id] and probe['projectId'] == project_id and probe['view']['capabilities']['app.deploy'] and not probe['view']['capabilities']['terminal.open']
    environment_id = request(f'/api/collections/web_app_environments/records?filter=appId%3D%22{app_id}%22', su)['items'][0]['id']
    assert perm(tok['dev'], {'environmentId': environment_id}, 'app.read')['appId'] == app_id
    perm(tok['dev'], {'projectId': other_project, 'environmentId': environment_id}, 'app.read', 404)
    # Long-lived credentials re-check the actor's current role.
    assert perm(platform, {'appId': app_id}, 'app.deploy', actorId=uid['dev'])['actor'] is True
    assert perm(platform, {'appId': app_id}, 'terminal.open', actorId=uid['dev'])['actor'] is False
    assert perm(platform, {'appId': app_id}, 'app.read', actorId=uid['outsider'])['actor'] is False
    request('/api/collections/users/records/' + uid['dev'], su, {'suspension': 'x'}, method='PATCH')
    assert perm(platform, {'appId': app_id}, 'app.read', actorId=uid['dev'])['actor'] is False
    me = request('/api/web-access/me', tok['owner2']); assert me['hasAccess'] and me['projects'][0]['role'] == 'owner' and me['projects'][0]['capabilities']['project.rename'] and not me['projects'][0]['capabilities']['platform.github']
    # App-scoped settings survive the author's departure; legacy apps follow the author's superAdmin status.
    assert perm(platform, {'appId': app_id}, 'app.read', settingAppId=app_id, actorId=uid['dev'])['setting'] is True
    assert perm(platform, {'appId': legacy_id}, 'app.read', settingAppId=legacy_id, actorId=uid['dev'])['setting'] is False
    assert perm(platform, {'appId': legacy_id}, 'app.read', settingAppId=legacy_id, actorId=platform_row['id'])['setting'] is True
    # Repository allowlist: empty => platform only; listed repo => admin+; unchanged repo is not a change.
    assert perm(tok['owner2'], {'appId': app_id}, 'app.read', repository='other/repo')['repo'] is False
    assert perm(tok['owner2'], {'appId': app_id}, 'app.read', repository='owner/repo', current='owner/repo')['repo'] is True
    request(f'/api/collections/web_projects/records/{project_id}', su, {'allowedRepositories': ['Other/Repo']}, method='PATCH')
    assert perm(tok['owner2'], {'appId': app_id}, 'app.read', repository='other/repo')['repo'] is True
    request(members, tok['owner2'], {'email': 'viewer@example.invalid', 'role': 'viewer'}, status=201)
    assert perm(tok['viewer'], {'appId': app_id}, 'app.read', repository='other/repo')['repo'] is False
    assert perm(platform, {'appId': app_id}, 'app.read', repository='any/repo')['repo'] is True
    # Superadmin assigns projects to a user (UserInstanceAccess pattern).
    assigned = f"/api/web-access/users/{uid['assigned']}/projects"
    request(assigned, tok['owner2'], status=403)
    view = request(assigned, platform); assert {row['projectId'] for row in view['items']} == {project_id, other_project} and all(row['role'] is None for row in view['items'])
    view = request(assigned, platform, {'projects': [{'projectId': project_id, 'role': 'developer'}, {'projectId': other_project, 'role': 'owner'}]}, method='PUT')
    assert {row['projectId']: row['role'] for row in view['items']} == {project_id: 'developer', other_project: 'owner'}
    request(assigned, platform, {'projects': [{'projectId': project_id, 'role': 'root'}]}, method='PUT', status=400)
    request(assigned, platform, {'projects': [{'projectId': project_id, 'role': 'viewer'}]}, method='PUT', status=400)  # last owner of other_project
    view = request(assigned, platform, {'projects': [{'projectId': project_id, 'role': 'viewer'}, {'projectId': other_project, 'role': 'owner'}]}, method='PUT')
    assert {row['projectId']: row['role'] for row in view['items']} == {project_id: 'viewer', other_project: 'owner'}
    me = request('/api/web-access/me', users['assigned'][1]); assert sorted(p['role'] for p in me['projects']) == ['owner', 'viewer']
    request(f"/api/web-access/users/zzzzzzzzzzzzzzz/projects", platform, status=404)
    checks.append('central_guard_roles_platform_only_legacy_404_uniform_environment_resolution_actor_revalidation')
    checks.append('app_settings_survive_author_repository_allowlist_superadmin_user_project_assignment')
    # Non-source engines (image) can form a project without a repository.
    image_id = request('/api/collections/web_apps/records', su, {'name': 'Image', 'uid': platform_row['id'], 'status': 'stopped', 'config': {'repository': '', 'branch': 'main', 'rootDirectory': '.', 'healthPath': '/', 'generatedDomain': 'web-img.example.invalid'}})['id']
    request('/api/collections/web_runtime_profiles/records', su, {'appId': image_id, 'profile': {'kind': 'image', 'image': 'ghcr.io/acme/site@sha256:' + 'a' * 64, 'port': 3000}, 'revision': 'r' * 24})
    image_project = request(f'/api/web-apps/{image_id}/project', platform, {'name': 'Images', 'environment': 'prod'}, status=201)['project']
    assert image_project['repository'] == ''
    checks.append('non_source_engine_project_without_repository')
    # Cascade: deleting the project removes its memberships.
    request(f'/api/collections/web_projects/records/{project_id}', su, method='DELETE', status=204)
    assert request(f'/api/collections/web_project_members/records?filter=project%3D%22{project_id}%22', su)['totalItems'] == 0
    checks.append('project_delete_cascades_memberships')
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
