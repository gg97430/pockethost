#!/usr/bin/env python3
"""Disposable PocketBase: hosting projects as groups of sites (grouped list, create, delete, attach/detach/move).

Registers every routerAdd of webApps/hooks.ts, then checks the contract of GET /api/web-projects/groups,
POST/DELETE /api/web-projects and PUT /api/web-apps/{id}/project for every role (refusals included), the move
guards (previews, running deployments, repositories, preview model, shared variables), multiRepository and the
promotion rule inside a group. No Docker, no network: denied calls stop at the guard, allowed calls do no I/O.
"""
import argparse, json, os, pathlib, re, secrets, shutil, socket, subprocess, tempfile, time, urllib.error, urllib.parse, urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True, type=pathlib.Path)
parser.add_argument('--pocketbase', required=True)
args = parser.parse_args()
os.umask(0o077)
temporary = tempfile.TemporaryDirectory(prefix='ph-web-groups-')
root = pathlib.Path(temporary.name).resolve()
process = None
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
origin = f'http://127.0.0.1:{port}'
DENY, NOT_PROJECT, NOT_APP = 'Droits insuffisants sur ce projet.', 'Projet introuvable.', 'Application introuvable.'
PLATFORM = {'Action réservée aux administrateurs de la plateforme.', 'Acces superadmin requis.'}


def call(method, path, token='', body=None):
    if body is None and method in ('POST', 'PUT', 'PATCH'): body = {}
    value = urllib.request.Request(origin + path, method=method, headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {})}, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(value, timeout=30) as response:
            data = response.read(); return response.status, (json.loads(data) if data else None)
    except urllib.error.HTTPError as error:
        data = error.read()
        try: return error.code, json.loads(data) if data else None
        except ValueError: return error.code, {'message': data.decode(errors='replace')}


def request(path, token='', body=None, method=None, status=200):
    code, data = call(method or ('POST' if body is not None else 'GET'), path, token, body)
    assert code == status, f'{method or ""} {path}: {code} != {status}: {json.dumps(data, ensure_ascii=False)[:600]}'
    return data


def fails(status, text, path, token='', body=None, method=None):
    """Refusal with an exact message (str) or a message fragment (text starting with '~') or one of a set."""
    data = request(path, token, body, method, status)
    message = (data or {}).get('message', '')
    if isinstance(text, set): ok = message in text
    elif text.startswith('~'): ok = text[1:] in message
    else: ok = message == text
    assert ok, f'{method or ""} {path}: expected {text!r}, got {message!r}'
    return message


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks = root / 'hooks'; hooks.mkdir(); shutil.copy(source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    # Every web route exactly as hooks.ts registers it (method, path, handler, auth middleware).
    registered = re.findall(r"routerAdd\(\s*'(\w+)',\s*'([^']+)',\s*\(e\)\s*=>\s*require\(`\$\{__hooks\}/mothership`\)\.(\w+)\(e\)\s*(,\s*\$apis\.requireAuth\('users'\))?\s*,?\s*\)", (source / 'src/lib/handlers/webApps/hooks.ts').read_text())
    wanted = {('GET', '/api/web-projects/groups'), ('POST', '/api/web-projects'), ('DELETE', '/api/web-projects/{projectId}'), ('PUT', '/api/web-apps/{id}/project')}
    assert wanted <= {(m, p) for m, p, _, auth in registered if auth}, 'site group routes missing from hooks.ts'
    middleware = ',$apis.requireAuth("users")'
    (hooks / 'test.pb.js').write_text(''.join(f'routerAdd({json.dumps(m)},{json.dumps(p)},e=>require(`${{__hooks}}/mothership`).{h}(e){middleware if auth else ""});\n' for m, p, h, auth in registered))
    # Same master key convention as test-web-project-environments.py: shared variables are encrypted.
    env = {**os.environ, 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_MASTER_KEY': 'g' * 32, 'WEB_APPS_WORKER_SECRET': 'W' + secrets.token_urlsafe(40), 'WEB_APPS_ROOT': str(root / 'web'), 'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid'}
    password, email = 'P' + secrets.token_urlsafe(32), 'groups@example.invalid'
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
    names = ['platform', 'own_ab', 'own_a', 'adm_a_own_b', 'own_a_view_b', 'dev_a', 'view_a', 'adm_b', 'outsider', 'suspended']
    accounts = {name: user(name, name == 'platform') for name in names}
    tok = {name: value[1] for name, value in accounts.items()}; uid = {name: value[0]['id'] for name, value in accounts.items()}
    platform = tok['platform']

    def q(value): return urllib.parse.quote(value)
    def records(collection, filter_):
        return request(f'/api/collections/{collection}/records?perPage=200&filter=' + q(filter_), su)['items']
    def app(name, repository):
        return request('/api/collections/web_apps/records', su, {'name': name, 'uid': uid['platform'], 'status': 'stopped', 'config': {'repository': repository, 'branch': 'main', 'rootDirectory': '.', 'healthPath': '/', 'generatedDomain': 'web-' + secrets.token_hex(6) + '.example.invalid'}})['id']
    def put(ident, body, token=None, status=200): return request(f'/api/web-apps/{ident}/project', token or platform, body, method='PUT', status=status)
    def project_of(ident): return request(f'/api/web-apps/{ident}', platform)['access']['projectId']
    def add(project, who, role): request(f'/api/web-projects/{project}/members', platform, {'email': who + '@example.invalid', 'role': role}, status=201)
    def groups(who): return request('/api/web-projects/groups', tok[who])
    def group(data, project): return next(g for g in data['groups'] if g['project']['id'] == project)
    def audit(project): return records('web_project_audit', f'projectId="{project}"')
    def base(name): return {'name': name, 'previewEnabled': False, 'previewMax': 3, 'previewTtlHours': 24}

    project_a = request('/api/web-projects', platform, {'name': 'Équipe A'}, status=201)['project']['id']
    project_b = request('/api/web-projects', platform, {'name': 'Équipe B'}, status=201)['project']['id']
    for who, role in [('own_ab', 'owner'), ('own_a', 'owner'), ('adm_a_own_b', 'admin'), ('own_a_view_b', 'owner'), ('dev_a', 'developer'), ('view_a', 'viewer'), ('suspended', 'viewer')]: add(project_a, who, role)
    for who, role in [('own_ab', 'owner'), ('adm_a_own_b', 'owner'), ('own_a_view_b', 'viewer'), ('adm_b', 'admin')]: add(project_b, who, role)
    request('/api/collections/users/records/' + uid['suspended'], su, {'suspension': 'abuse'}, method='PATCH')

    # 1. Create: superAdmin only, empty group (multiRepository, no repository), creator is an explicit owner.
    created = request('/api/web-projects', platform, {'name': 'Client Avenir'}, status=201)['project']
    assert created['multiRepository'] is True and created['repository'] == '' and created['memberRole'] == 'owner' and created['role'] == 'platform', created
    assert created['siteCount'] == 0 and created['previewCount'] == 0 and created['allowedRepositories'] == [] and all(created['capabilities'].values())
    members = request(f'/api/web-projects/{created["id"]}/members', platform)['items']
    assert [(m['userId'], m['role']) for m in members] == [(uid['platform'], 'owner')], members
    fails(403, PLATFORM, '/api/web-projects', tok['own_a'], {'name': 'Projet pirate'})
    request('/api/web-projects', '', {'name': 'Anonyme'}, status=401)
    request('/api/web-projects', tok['suspended'], {'name': 'Suspendu'}, status=403)
    fails(400, 'Nom de projet requis (80 caractères maximum).', '/api/web-projects', platform, {'name': '   '})
    fails(400, 'Nom de projet requis (80 caractères maximum).', '/api/web-projects', platform, {'name': 'a' * 81})
    fails(400, '~porte déjà ce nom', '/api/web-projects', platform, {'name': ' client avenir '})
    # Accents: SQLite only folds ASCII case, the comparison is done on every name in JS.
    accent = request('/api/web-projects', platform, {'name': 'Été'}, status=201)['project']['id']
    fails(400, '~porte déjà ce nom', '/api/web-projects', platform, {'name': 'été'})
    assert request(f'/api/web-projects/{accent}', platform, method='DELETE')['deleted'] is True
    checks.append('create_platform_only_owner_row_validation_and_case_insensitive_duplicates')

    # Sites of A (different repositories: A is a group), one preview, one application without project.
    main = app('Site principal', 'acme/main'); api = app('API', 'acme/api'); tpl = app('Modèle', 'acme/tpl'); orphan = app('Orpheline', 'acme/orphan')
    for ident in [main, api, tpl]: assert put(ident, {'projectId': project_a})['app']['access']['projectId'] == project_a
    preview = app('Preview 1', 'acme/tpl')
    request('/api/collections/web_app_environments/records', su, {'projectId': project_a, 'appId': preview, 'environment': 'preview', 'slug': 'pr-1', 'state': 'active', 'sourceAppId': tpl, 'previewNumber': 1, 'expiresAt': '2099-01-01 00:00:00.000Z'})

    # 2. Grouped list: membership filter, one role per project, previews counted apart, « Sans projet » for superAdmins.
    data = groups('outsider'); assert data['groups'] == [] and data['unassigned'] is None and data['enabled'] is True and 'workerReady' in data['capabilities']
    data = groups('view_a'); assert [g['project']['id'] for g in data['groups']] == [project_a] and data['unassigned'] is None and not data['isPlatformAdmin']
    a = group(data, project_a)
    assert a['project']['role'] == 'viewer' and a['project']['memberRole'] == 'viewer' and a['project']['siteCount'] == 3 and a['project']['previewCount'] == 1, a['project']
    assert [x['id'] for x in a['apps']] == [api, tpl, main, preview], [x['name'] for x in a['apps']]
    assert a['apps'][-1]['membership']['environment'] == 'preview' and a['apps'][-1]['membership']['previewNumber'] == 1
    assert all(x['access']['projectId'] == project_a and x['access']['role'] == 'viewer' and not x['access']['capabilities']['app.move'] for x in a['apps'])
    assert next(x for x in a['apps'] if x['id'] == main)['membership']['slug'] == 'site-principal'
    data = groups('platform'); assert {created['id'], project_a, project_b} <= {g['project']['id'] for g in data['groups']} and data['isPlatformAdmin']
    assert group(data, created['id'])['apps'] == [] and group(data, created['id'])['project']['siteCount'] == 0
    assert [x['id'] for x in data['unassigned']['apps']] == [orphan] and data['unassigned']['apps'][0]['access']['role'] == 'platform' and data['unassigned']['apps'][0]['membership'] is None
    assert [g['project']['name'] for g in data['groups']] == sorted(g['project']['name'] for g in data['groups']) and data['truncated'] is False
    assert all(x['access']['capabilities']['app.move'] for x in group(groups('own_ab'), project_a)['apps'])
    split = groups('adm_a_own_b'); assert not group(split, project_a)['project']['capabilities']['app.move'] and group(split, project_b)['project']['capabilities']['app.move']
    request('/api/web-projects/groups', tok['suspended'], status=403); request('/api/web-projects/groups', status=401)
    checks.append('groups_filtered_by_membership_roles_counts_previews_and_unassigned_for_platform')

    # Deployment badge: the published version can stay online while another job is queued/running.
    assert all(x['deploymentInProgress'] is False for x in a['apps'])
    def deployment(ident, state):
        return request('/api/collections/web_deployments/records', su, {'appId': ident, 'action': 'deploy', 'status': state})['id']
    def finish(ident, state):
        request('/api/collections/web_deployments/records/' + ident, su, {'status': state}, method='PATCH')
    running = deployment(main, 'running')
    queued_main = deployment(main, 'queued')
    published = deployment(main, 'ready')
    queued_api = deployment(api, 'queued')
    queued_orphan = deployment(orphan, 'queued')
    request('/api/collections/web_apps/records/' + main, su, {'status': 'running', 'activeDeployment': published}, method='PATCH')
    listed = {x['id']: x for x in group(groups('view_a'), project_a)['apps']}
    assert listed[main]['status'] == 'running' and listed[main]['activeDeployment'] == published
    assert {ident for ident, value in listed.items() if value['deploymentInProgress']} == {main, api}
    assert groups('platform')['unassigned']['apps'][0]['deploymentInProgress'] is True
    assert groups('outsider')['groups'] == [] and groups('outsider')['unassigned'] is None
    finish(queued_main, 'cancelled')
    assert next(x for x in group(groups('view_a'), project_a)['apps'] if x['id'] == main)['deploymentInProgress'] is True
    finish(running, 'ready'); finish(queued_api, 'failed'); finish(queued_orphan, 'cancelled')
    assert all(x['deploymentInProgress'] is False for x in group(groups('view_a'), project_a)['apps'])
    assert groups('platform')['unassigned']['apps'][0]['deploymentInProgress'] is False
    request('/api/collections/web_apps/records/' + main, su, {'status': 'stopped', 'activeDeployment': ''}, method='PATCH')
    checks.append('deployment_badge_includes_queued_running_and_unassigned_excludes_terminal_preserves_scope_and_online_state')

    # 3. Move A -> B refusals: owner on BOTH projects; the application never leaves A.
    move = {'projectId': project_b}
    fails(404, NOT_PROJECT, f'/api/web-apps/{main}/project', tok['own_a'], move, 'PUT')
    fails(403, DENY, f'/api/web-apps/{main}/project', tok['own_a_view_b'], move, 'PUT')
    fails(403, DENY, f'/api/web-apps/{main}/project', tok['adm_a_own_b'], move, 'PUT')
    for who in ['dev_a', 'view_a']: fails(403, DENY, f'/api/web-apps/{main}/project', tok[who], move, 'PUT')
    fails(404, NOT_APP, f'/api/web-apps/{main}/project', tok['outsider'], move, 'PUT')
    request(f'/api/web-apps/{main}/project', tok['suspended'], move, 'PUT', status=403)
    assert project_of(main) == project_a
    checks.append('move_refused_below_owner_on_origin_or_target_uniform_404_app_stays')

    # 4. Move success by an owner of A and B: A's members lose access, B's gain it; both audits.
    moved = put(main, move, tok['own_ab'])
    assert moved['app']['access']['projectId'] == project_b and moved['app']['access']['role'] == 'owner' and moved['fromProjectId'] == project_a, moved
    assert moved['app']['membership']['slug'] == 'site-principal' and moved['app']['membership']['environment'] == 'prod' and moved['previewsDisabled'] is False
    request(f'/api/web-apps/{main}', tok['view_a'], status=404); request(f'/api/web-apps/{main}', tok['adm_b'])
    assert any(r['action'] == 'application-detached' and r['details'].get('movedTo') == project_b and r['details'].get('appId') == main for r in audit(project_a))
    assert any(r['action'] == 'application-attached' and r['details'].get('movedFrom') == project_a and r['details'].get('appId') == main for r in audit(project_b))
    checks.append('move_by_owner_of_both_switches_access_and_audits_both_projects')

    # 5. Short names: derived from the application name, unique in the group; an explicit taken one is refused.
    v1, v2, v3 = app('Vitrine', 'acme/vitrine'), app('Vitrine', 'acme/vitrine-2'), app('Autre', 'acme/autre')
    assert put(v1, {'projectId': project_b})['app']['membership']['slug'] == 'vitrine'
    assert put(v2, {'projectId': project_b})['app']['membership']['slug'] == 'vitrine-2'
    fails(400, '~déjà utilisé', f'/api/web-apps/{v3}/project', platform, {'projectId': project_b, 'slug': 'vitrine'}, 'PUT')
    fails(400, '~Nom court attendu', f'/api/web-apps/{v3}/project', platform, {'projectId': project_b, 'slug': 'Vitrine!'}, 'PUT')
    placed = put(v3, {'projectId': project_b, 'slug': 'autre-site', 'environment': 'staging'})['app']['membership']
    assert placed['slug'] == 'autre-site' and placed['environment'] == 'staging', placed
    checks.append('slugs_derived_from_name_unique_in_group_explicit_collision_refused')

    # 6. Attach / detach: superAdmins only; a detached site is visible to the platform only.
    fails(403, PLATFORM, f'/api/web-apps/{main}/project', tok['own_ab'], {'projectId': None}, 'PUT')
    out = put(main, {'projectId': None})
    assert out['app']['access']['projectId'] == '' and out['app']['membership'] is None and out['fromProjectId'] == project_b
    assert main in [x['id'] for x in groups('platform')['unassigned']['apps']]
    request(f'/api/web-apps/{main}', tok['own_ab'], status=404)
    fails(404, NOT_APP, f'/api/web-apps/{main}/project', tok['own_ab'], {'projectId': project_b}, 'PUT')
    back = put(main, {'projectId': project_b})
    assert back['app']['access']['projectId'] == project_b and back['app']['membership']['slug'] == 'site-principal'
    fails(400, '~déjà dans ce projet', f'/api/web-apps/{main}/project', platform, {'projectId': project_b}, 'PUT')
    fails(400, 'Cette application n’appartient pas à un projet.', f'/api/web-apps/{orphan}/project', platform, {'projectId': None}, 'PUT')
    fails(400, 'Projet invalide.', f'/api/web-apps/{orphan}/project', platform, {'projectId': 'nope'}, 'PUT')
    fails(404, NOT_PROJECT, f'/api/web-apps/{orphan}/project', platform, {'projectId': 'z' * 15}, 'PUT')
    checks.append('attach_detach_platform_only_unassigned_hidden_from_members_same_project_refused')

    # 7. A preview never moves; a queued deployment blocks the move.
    fails(400, '~preview temporaire', f'/api/web-apps/{preview}/project', platform, {'projectId': project_b}, 'PUT')
    queued = request('/api/collections/web_deployments/records', su, {'appId': api, 'action': 'deploy', 'status': 'queued'})['id']
    fails(400, '~déploiement en cours', f'/api/web-apps/{api}/project', platform, {'projectId': project_b}, 'PUT')
    request('/api/collections/web_deployments/records/' + queued, su, {'status': 'failed'}, method='PATCH')
    assert project_of(api) == project_a and project_of(preview) == project_a
    checks.append('preview_and_running_deployment_never_move')

    # 8. Repositories: a single-repository target keeps its invariant, a group accepts any repository.
    mono_app = app('Mono', 'acme/mono')
    project_m = request(f'/api/web-apps/{mono_app}/project', platform, {'name': 'Mono', 'environment': 'prod'}, status=201)['project']['id']
    fails(400, '~même dépôt', f'/api/web-apps/{api}/project', platform, {'projectId': project_m}, 'PUT')
    assert put(api, {'projectId': project_b})['app']['access']['projectId'] == project_b
    checks.append('single_repository_target_refuses_other_repository_group_accepts_it')

    # 9. Preview model: open previews from the site block the move; the model leaving disables A's previews.
    fails(400, '~Fermez d’abord', f'/api/web-apps/{tpl}/project', platform, {'projectId': project_b}, 'PUT')
    model = app('Gabarit', 'acme/gabarit'); put(model, {'projectId': project_a})
    request('/api/collections/web_projects/records/' + project_a, su, {'previewEnabled': True, 'previewSourceAppId': model}, method='PATCH')
    result = put(model, {'projectId': project_b}, tok['own_ab'])
    assert result['previewsDisabled'] is True, result
    after = request('/api/collections/web_projects/records/' + project_a, su)
    assert after['previewEnabled'] is False and after['previewSourceAppId'] == ''
    assert any(r['action'] == 'settings-updated' and r['details'].get('reason') == 'preview-source-moved' for r in audit(project_a))
    checks.append('open_previews_block_move_preview_model_move_disables_previews')

    # 10. Shared variables of A (names only): dropped unless copySharedVariables.
    def shared(project, text):
        url = f'/api/web-projects/{project}/variables/prod'
        body = {'revision': request(url, platform)['revision'], 'text': text, 'mode': 'merge'}
        request(url, platform, {**body, 'previewToken': request(url + '/preview', platform, body)['previewToken']}, method='PUT')
    shared(project_a, 'X_SHARED=valeur-equipe-a\n')
    var1, var2 = app('Variables 1', 'acme/var1'), app('Variables 2', 'acme/var2')
    put(var1, {'projectId': project_a}); put(var2, {'projectId': project_a})
    names = lambda ident: [v['name'] for v in request(f'/api/web-apps/{ident}/environments', platform)['effectiveVariables']]
    assert 'X_SHARED' in names(var1)
    fails(400, 'Le choix de copie des variables doit être explicite.', f'/api/web-apps/{var1}/project', tok['own_ab'], {'projectId': project_b, 'copySharedVariables': 'oui'}, 'PUT')
    dropped = put(var1, {'projectId': project_b}, tok['own_ab'])
    assert dropped['droppedVariables'] == ['X_SHARED'] and dropped['copiedVariables'] == [] and 'X_SHARED' not in names(var1), dropped
    copied = put(var2, {'projectId': project_b, 'copySharedVariables': True}, tok['own_ab'])
    assert 'X_SHARED' in copied['copiedVariables'] and copied['droppedVariables'] == [] and 'X_SHARED' in names(var2), copied
    assert 'valeur-equipe-a' not in json.dumps(dropped) + json.dumps(copied)
    # Local variables + shared variables of the target: same 100 limit as a deployment, checked before saving.
    limit_1 = request('/api/web-projects', platform, {'name': 'Limite 1'}, status=201)['project']['id']
    limit_2 = request('/api/web-projects', platform, {'name': 'Limite 2'}, status=201)['project']['id']
    shared(limit_1, ''.join(f'UN_{i}=x\n' for i in range(60))); shared(limit_2, ''.join(f'DEUX_{i}=x\n' for i in range(50)))
    full_1, full_2 = app('Plein 1', 'acme/plein-1'), app('Plein 2', 'acme/plein-2')
    put(full_1, {'projectId': limit_1}); put(full_1, {'projectId': None})  # detach: 60 variables kept locally
    fails(400, '~100 variables maximum', f'/api/web-apps/{full_1}/project', platform, {'projectId': limit_2, 'copySharedVariables': True}, 'PUT')
    assert project_of(full_1) == ''
    put(full_2, {'projectId': limit_1})
    fails(400, '~100 variables maximum', f'/api/web-apps/{full_2}/project', platform, {'projectId': limit_2, 'copySharedVariables': True}, 'PUT')
    assert project_of(full_2) == limit_1
    checks.append('shared_variables_dropped_by_default_copied_on_request_names_only_merged_limit_checked')

    # 11. Delete: owner or superAdmin, refused while a site or a preview cleanup remains; audit kept.
    doomed = request('/api/web-projects', platform, {'name': 'À supprimer'}, status=201)['project']['id']
    for who, role in [('own_a', 'owner'), ('adm_b', 'admin'), ('dev_a', 'developer'), ('view_a', 'viewer')]: add(doomed, who, role)
    site = app('Éphémère', 'acme/ephemere'); put(site, {'projectId': doomed})
    url = f'/api/web-projects/{doomed}'
    for who in ['view_a', 'dev_a', 'adm_b']: fails(403, DENY, url, tok[who], method='DELETE')
    fails(404, NOT_PROJECT, url, tok['outsider'], method='DELETE')
    fails(400, '~contient encore 1 site', url, tok['own_a'], method='DELETE')
    put(site, {'projectId': None})
    closing = request('/api/collections/web_app_environments/records', su, {'projectId': doomed, 'appId': '', 'environment': 'preview', 'slug': 'pr-9', 'state': 'closing', 'previewNumber': 9})['id']
    fails(400, '~nettoyage', url, tok['own_a'], method='DELETE')
    request('/api/collections/web_app_environments/records/' + closing, su, {'state': 'closed'}, method='PATCH')
    shared(doomed, 'DOOMED_SECRET=x\n')
    git = request('/api/collections/web_git_sources/records', su, {'host': 'git.example.invalid', 'port': 22, 'sshUser': 'git', 'path': 'acme/doomed.git', 'canonical': 'git.example.invalid:22/acme/doomed.git', 'projectId': doomed, 'webhookId': secrets.token_hex(12)})['id']
    assert request(url, tok['own_a'], method='DELETE') == {'deleted': True, 'projectId': doomed}
    assert doomed not in [g['project']['id'] for g in groups('platform')['groups']]
    assert records('web_project_members', f'project="{doomed}"') == [] and records('web_project_variables', f'projectId="{doomed}"') == [] and records('web_app_environments', f'projectId="{doomed}"') == []
    assert any(r['action'] == 'project-deleted' for r in audit(doomed)) and any(r['action'] == 'project-created' for r in audit(doomed))
    assert request('/api/collections/web_git_sources/records/' + git, su)['projectId'] == ''
    assert request(f'/api/web-projects/{created["id"]}', platform, method='DELETE')['deleted'] is True
    fails(404, NOT_PROJECT, f'/api/web-projects/{created["id"]}', platform, method='DELETE')
    checks.append('delete_owner_or_platform_refused_while_sites_or_cleanup_remain_audit_kept')

    # 12. Rename a group: owner (existing rule), never admin.
    renamed = request(f'/api/web-projects/{project_a}', tok['own_a'], base('Équipe A · sites'), method='PATCH')['project']
    assert renamed['name'] == 'Équipe A · sites' and renamed['multiRepository'] is True
    fails(403, DENY, f'/api/web-projects/{project_a}', tok['adm_a_own_b'], base('Renommé par un admin'), method='PATCH')
    checks.append('rename_group_owner_only')

    # 17. Migration / non-regression: historic projects stay single-repository and keep the same-repository rule.
    assert next(p for p in request('/api/web-projects', platform)['items'] if p['id'] == project_m)['multiRepository'] is False
    assert group(groups('platform'), project_m)['project']['multiRepository'] is False
    fails(400, '~même dépôt', f'/api/web-apps/{app("Ailleurs", "acme/elsewhere")}/project/attach', platform, {'projectId': project_m, 'environment': 'dev'})
    checks.append('historic_projects_single_repository_same_repository_rule_kept')

    # 16. /api/web-access/me: new permission levels and multiRepository on every project.
    add(project_m, 'own_a', 'owner')
    me = request('/api/web-access/me', tok['own_a'])
    assert me['permissions']['app.move'] == 'owner' and me['permissions']['app.attach'] == 'platform' and me['permissions']['app.detach'] == 'platform'
    assert me['globalCapabilities']['app.attach'] is False and me['globalCapabilities']['app.detach'] is False
    flags = {p['id']: p['multiRepository'] for p in me['projects']}; assert flags[project_a] is True and flags[project_m] is False, flags
    checks.append('me_permissions_move_owner_attach_detach_platform_and_multi_repository_flag')

    # 13. multiRepository: superAdmin only, one way, explicit boolean.
    m_url = f'/api/web-projects/{project_m}'
    fails(403, PLATFORM, m_url, tok['own_a'], {**base('Mono'), 'multiRepository': True}, method='PATCH')
    fails(400, 'Le choix multi-dépôt doit être explicite.', m_url, platform, {**base('Mono'), 'multiRepository': 'yes'}, method='PATCH')
    assert request(m_url, platform, {**base('Mono'), 'multiRepository': True}, method='PATCH')['project']['multiRepository'] is True
    fails(400, 'Un groupe de sites ne peut pas redevenir un projet mono-dépôt.', m_url, platform, {**base('Mono'), 'multiRepository': False}, method='PATCH')
    assert request(m_url, tok['own_a'], {**base('Mono'), 'multiRepository': True}, method='PATCH')['project']['multiRepository'] is True  # same value: no effect
    checks.append('multi_repository_platform_only_irreversible_explicit')

    # 14. Previews of a group: the project repository follows the model, whose repository is then locked.
    empty = app('Sans dépôt', ''); put(empty, {'projectId': project_b})
    b_url = f'/api/web-projects/{project_b}'
    fails(400, 'Le modèle de preview doit avoir un dépôt GitHub.', b_url, platform, {**base('Équipe B'), 'previewEnabled': True, 'previewSourceAppId': empty}, method='PATCH')
    enabled = request(b_url, platform, {**base('Équipe B'), 'previewEnabled': True, 'previewSourceAppId': api}, method='PATCH')['project']
    assert enabled['repository'] == 'acme/api' and enabled['previewEnabled'] is True, enabled
    fails(400, '~sert de modèle', f'/api/web-apps/{api}', platform, {'repository': 'acme/api-v2'}, method='PATCH')
    assert request(f'/api/web-apps/{api}', platform)['app']['repository'] == 'acme/api'
    # Omitted preview settings keep the stored values (a stale screen never rewrites them); given ones are validated.
    kept = request(b_url, tok['adm_a_own_b'], {'name': 'Équipe B · sites'}, method='PATCH')['project']
    assert kept['name'] == 'Équipe B · sites' and kept['previewEnabled'] is True and kept['previewSourceAppId'] == api and kept['previewMax'] == 3, kept
    fails(400, 'Nom, quota 1 à 5 et durée 1 à 72 heures requis.', b_url, platform, {'previewMax': 9}, method='PATCH')
    # Another preview repository while previews are open: refused (PR numbers would be reused across repositories).
    opened = request('/api/collections/web_app_environments/records', su, {'projectId': project_b, 'appId': '', 'environment': 'preview', 'slug': 'pr-5', 'state': 'closing', 'previewNumber': 5})['id']
    other = {'previewEnabled': True, 'previewSourceAppId': v1}
    fails(400, 'Fermez d’abord les previews ouvertes avant de changer de modèle de preview.', b_url, platform, other, method='PATCH')
    assert request(b_url, platform, {'previewEnabled': False}, method='PATCH')['project']['repository'] == 'acme/api'
    fails(400, '~Fermez d’abord les previews ouvertes', b_url, platform, other, method='PATCH')
    request('/api/collections/web_app_environments/records/' + opened, su, {'state': 'closed'}, method='PATCH')
    assert request(b_url, platform, other, method='PATCH')['project']['repository'] == 'acme/vitrine'
    assert request(b_url, platform, {'previewSourceAppId': api, 'previewEnabled': True}, method='PATCH')['project']['repository'] == 'acme/api'
    checks.append('group_previews_repository_follows_model_model_repository_locked_open_previews_block_change')

    # 15. Promotion inside a group: same site only (shared repository or clone lineage).
    fails(400, '~même site', f'/api/web-apps/{api}/environments/promotion/preview', platform, {'targetAppId': v1})
    clone = request(f'/api/web-apps/{api}/environments/clone', platform, {'environment': 'staging', 'slug': 'api-staging'}, status=201)['app']['id']
    fails(400, '~Publiez une version', f'/api/web-apps/{api}/environments/promotion/preview', platform, {'targetAppId': clone})
    checks.append('group_promotion_same_site_family_only')

    print(json.dumps({'ok': True, 'checks': checks}, ensure_ascii=False), flush=True)
except Exception:
    if (root / 'process.log').exists(): print((root / 'process.log').read_text()[-3000:])
    raise
finally:
    if process:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
    temporary.cleanup()
