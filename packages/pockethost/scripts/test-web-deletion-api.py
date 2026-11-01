#!/usr/bin/env python3
"""Disposable PocketBase: owner-only, asynchronous deletion and cross-project isolation.

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
    with (hooks / 'test.pb.js').open('a') as f:
        f.write('onRecordCreate(e=>require(`${__hooks}/mothership`).GuardWebDeletingRecord(e));\nonRecordUpdate(e=>require(`${__hooks}/mothership`).GuardWebDeletingRecord(e));\n')
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

    def worker(path, body=None, status=200, headers=None):
        endpoint='/api/web-apps/'+path[1:]if path.startswith('/')else'/api/web-apps/_worker/deletions/'+path
        req = urllib.request.Request(origin + endpoint, data=json.dumps(body or {}).encode(), headers={'Content-Type': 'application/json', 'X-Web-Worker': env['WEB_APPS_WORKER_SECRET'], **(headers or {})})
        try:
            with urllib.request.urlopen(req,timeout=15) as r: code,data=r.status,json.load(r)
        except urllib.error.HTTPError as e: code,data=e.code,json.load(e)
        assert code==status,(path,code,data)
        return data
    def delete_app(ident, name, token=platform, status=202):
        return request('/api/web-apps/'+ident,token,{'confirmation':name},'DELETE',status)
    def delete_project(ident, name, token=platform, status=202):
        return request('/api/web-projects/'+ident,token,{'confirmation':name,'deleteContents':True},'DELETE',status)
    main=app('Site principal','acme/main'); sibling=app('Voisin','acme/neighbor'); foreign=app('Autre équipe','acme/foreign')
    put(main,{'projectId':project_a});put(sibling,{'projectId':project_a});put(foreign,{'projectId':project_b})
    # No role below owner, inaccessible target, inactive admin or anonymous can queue removal.
    for who in ['adm_a_own_b','dev_a','view_a']:delete_app(main,'Site principal',tok[who],403)
    delete_app(main,'Site principal',tok['outsider'],404);delete_app(main,'Site principal','',401);delete_app(main,'Site principal',tok['suspended'],403)
    delete_app(main,'site principal',tok['own_a'],400)
    delete_project(project_a,'Équipe A',tok['dev_a'],403);delete_project(project_a,'Équipe A',tok['outsider'],404)
    assert not records('web_deletion_jobs','id!=""');checks.append('owner_only_exact_name_no_enumeration')
    storage_journal=root/'web/storage-operation.json';storage_journal.parent.mkdir(parents=True,exist_ok=True);storage_journal.write_text('{}')
    delete_app(main,'Site principal',tok['own_a'],400);storage_journal.unlink()
    pending=request('/api/collections/web_deployments/records',su,{'appId':main,'action':'stop','status':'queued'})
    delete_app(main,'Site principal',tok['own_a'],400)
    request('/api/collections/web_deployments/records/'+pending['id'],su,{'status':'cancelled'},'PATCH')
    policy=request('/api/collections/web_app_backup_settings/records',su,{'appId':main,'enabled':True})
    backup=request('/api/collections/web_app_backups/records',su,{'appId':main,'actorId':uid['platform'],'status':'ready','operation':'create'})
    volume_backup=request('/api/collections/web_storage_jobs/records',su,{'appId':main,'actorId':uid['platform'],'name':'uploads','status':'ready','operation':'backup'})
    task_runs=[];deliveries=[]
    for number, app_id in enumerate([main,foreign]):
        task_runs.append(request('/api/collections/web_task_runs/records',su,{'appId':app_id,'taskId':str(number)*15,'actorId':uid['platform'],'taskName':'Fixture','occurrence':'fixture','trigger':'manual','status':'queued','timeoutSeconds':1}))
        deliveries.append(request('/api/collections/web_notification_deliveries/records',su,{'appId':app_id,'actorId':uid['platform'],'settingsId':'n'*15,'settingsRevision':'r'*24,'channel':'email','event':'fixture','eventKey':str(number)*64,'trigger':'event','status':'pending','nextAttemptAt':'2020-01-01 00:00:00.000Z'}))
    result=delete_app(main,'Site principal',tok['own_a']);ident=result['deletion']['id']
    assert request('/api/web-apps/'+main,platform)['app']['autoDeploy']is False
    # Completing/suppressing old queue entries must not freeze the shared task or
    # notification workers. Both fixtures are deliberately non-executable/non-deliverable.
    worker('/_tasks/claim');worker('/_notifications/claim')
    assert all(records('web_task_runs','id="'+r['id']+'"')[0]['status']=='failed'for r in task_runs)
    assert all(records('web_notification_deliveries','id="'+r['id']+'"')[0]['status']=='suppressed'for r in deliveries)
    assert records('web_app_backups','appId="'+main+'"')[0]['status']=='deleting'
    assert records('web_app_backup_settings','appId="'+main+'"')[0]['enabled']is False
    assert delete_app(main,'Site principal',tok['own_a'])['deletion']['id']==ident
    assert worker('claim')['job']is None
    request('/api/collections/web_app_backups/records/'+backup['id'],su,{'status':'deleted'},'PATCH')
    assert worker('claim')['job']is None
    assert records('web_storage_jobs','id="'+volume_backup['id']+'"')[0]['operation']=='delete'
    request('/api/collections/web_storage_jobs/records/'+volume_backup['id'],su,{'status':'deleted'},'PATCH')
    job=worker('claim')['job'];assert job['id']==ident and [x['id']for x in job['apps']]==[main]
    assert not worker('claim')['job']
    worker('report',{'id':ident,'lease':'wrong','status':'ready','removed':True},403)
    worker('report',{'id':ident,'lease':job['lease'],'status':'ready'},400)
    checks.append('busy_jobs_refused_backup_deletion_wait_and_worker_lease')
    # Permission checks and model hooks cover stale writes, deployment queues, membership moves and child inserts.
    request('/api/web-apps/'+main,tok['own_a'],{'name':'Changed'},'PATCH',400)
    request('/api/web-apps/'+main+'/stop',tok['own_a'],{},status=400)
    request('/api/collections/web_apps/records/'+main,su,{'deletionId':'','name':'Stale write'},'PATCH',400)
    request('/api/collections/web_app_environments/records/'+records('web_app_environments','appId="'+main+'"')[0]['id'],su,{'projectId':project_b},'PATCH',400)
    request('/api/collections/web_app_domains/records',su,{'appId':main,'hostname':'blocked.example.invalid'},status=400)
    assert request('/api/web-apps/'+main,tok['view_a'])['app']['deletion']['id']==ident
    worker('claim',headers={'X-Forwarded-For':'127.0.0.1'},status=403)
    checks.append('tombstones_block_api_and_stale_model_writes_reads_stay_available')
    worker('report',{'id':ident,'lease':job['lease'],'status':'failed','error':'fixture'})
    assert delete_app(main,'Site principal',tok['own_a'])['deletion']['status']=='queued'
    retried=worker('claim')['job'];assert retried['id']==ident and retried['lease']!=job['lease']
    worker('report',{'id':ident,'lease':retried['lease'],'status':'ready','removed':True})
    worker('report',{'id':ident,'lease':retried['lease'],'status':'ready','removed':True})
    request('/api/web-apps/'+main,platform,status=404)
    fails(400,'~a été supprimée','/api/collections/web_app_domains/records',su,{'appId':main,'hostname':'late.example.invalid'})
    for collection in ['web_app_environments','web_deployments','web_app_backup_settings','web_app_backups','web_app_domains']:
        assert not records(collection,'appId="'+main+'"'),collection
    assert request('/api/web-apps/'+foreign,platform)['app']['name']=='Autre équipe'
    assert request('/api/web-apps/'+sibling,platform)['access']['projectId']==project_a
    checks.append('retry_same_scope_idempotent_completion_no_other_app_touched')
    # Project cascade is all-or-nothing at admission and removes all environments including previews.
    preview=app('Preview','acme/preview')
    request('/api/collections/web_app_environments/records',su,{'projectId':project_a,'appId':preview,'environment':'preview','slug':'pr-1','state':'active','previewNumber':1,'sourceAppId':sibling})
    pending=request('/api/collections/web_deployments/records',su,{'appId':preview,'action':'stop','status':'queued'})
    delete_project(project_a,'Équipe A',tok['own_a'],400)
    assert not request('/api/web-apps/'+sibling,platform)['app']['deletion']
    request('/api/collections/web_deployments/records/'+pending['id'],su,{'status':'cancelled'},'PATCH')
    deleted=delete_project(project_a,'Équipe A',tok['own_a']);jid=deleted['deletion']['id']
    assert records('web_projects','id="'+project_a+'"')[0]['deletionId']==jid
    request('/api/web-projects/'+project_a,tok['own_a'],{'name':'Renamed'},'PATCH',400)
    request('/api/collections/web_projects/records/'+project_a,su,{'deletionId':''},'PATCH',400)
    extra=app('Nouveau site','acme/new')
    request('/api/collections/web_app_environments/records',su,{'projectId':project_a,'appId':extra,'environment':'prod','slug':'new','state':'active'},status=400)
    claim=worker('claim')['job'];assert {v['id']for v in claim['apps']}=={sibling,preview}
    worker('report',{'id':jid,'lease':claim['lease'],'status':'ready','removed':True})
    assert not records('web_projects','id="'+project_a+'"')
    assert not records('web_project_members','project="'+project_a+'"')
    assert not records('web_app_environments','projectId="'+project_a+'"')
    assert not records('web_project_variables','projectId="'+project_a+'"')
    assert request('/api/web-apps/'+foreign,platform)['app']['name']=='Autre équipe'
    receipt=records('web_deletion_jobs','id="'+jid+'"')[0];assert receipt['status']=='ready'and receipt['actorId']==uid['own_a']
    assert any(r['action']=='project-deleted'for r in audit(project_a))
    request('/api/collections/web_deletion_jobs/records',tok['own_ab'],status=403)
    checks.append('full_project_cascade_preview_members_variables_audit_and_locked_receipts')
    empty=request('/api/web-projects',platform,{'name':'Vide'},status=201)['project']['id']
    deletion=delete_project(empty,'Vide');job=worker('claim')['job'];assert job['apps']==[]
    worker('report',{'id':job['id'],'lease':job['lease'],'status':'ready','removed':True})
    assert not records('web_projects','id="'+empty+'"')
    checks.append('empty_project_uses_same_confirmed_workflow')
    print(json.dumps({'passed':len(checks),'checks':checks}))
finally:
    if process:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait()
    if 'log'in locals():log.close()
    if 'checks'in locals()and len(checks)<6:
        try:print((root/'process.log').read_text()[-6000:])
        except OSError:pass
    temporary.cleanup()
