#!/usr/bin/env python3
"""Real disposable PocketBase: isolated project variables, promotions and trusted PR lifecycle."""
import argparse, importlib.util, json, os, pathlib, secrets, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True, type=pathlib.Path)
parser.add_argument('--pocketbase', required=True)
args = parser.parse_args()
os.umask(0o077)
temporary = tempfile.TemporaryDirectory(prefix='ph-web-environments-')
root = pathlib.Path(temporary.name).resolve()
process = None
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
origin = f'http://127.0.0.1:{port}'
worker_secret = secrets.token_urlsafe(40)


def request(path, token='', body=None, method=None, status=200, private=False):
    value = urllib.request.Request(origin + path, method=method or ('POST' if body is not None else 'GET'), headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {}), **({'X-Web-Worker': worker_secret} if private else {})}, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(value, timeout=25) as response:
            assert response.status == status, (path, response.status, status)
            data = response.read(); return json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        data = error.read()
        if error.code == status: return json.loads(data)
        raise AssertionError(f'{path}: {error.code}: ' + data.decode()) from None


def worker(path, body): return request('/api/web-apps/_worker/' + path, body=body, private=True)


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks = root / 'hooks'; hooks.mkdir(); shutil.copy(source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    routes = [
        ('GET', '/api/web-projects', 'HandleWebProjectsList'),
        ('PATCH', '/api/web-projects/{projectId}', 'HandleWebProjectUpdate'),
        ('GET', '/api/web-projects/{projectId}/variables/{environment}', 'HandleWebProjectVariablesGet'),
        ('POST', '/api/web-projects/{projectId}/variables/{environment}/reveal', 'HandleWebProjectVariablesReveal'),
        ('POST', '/api/web-projects/{projectId}/variables/{environment}/preview', 'HandleWebProjectVariablesPreview'),
        ('PUT', '/api/web-projects/{projectId}/variables/{environment}', 'HandleWebProjectVariablesPut'),
        ('GET', '/api/web-apps/{id}/environments', 'HandleWebAppEnvironments'),
        ('POST', '/api/web-apps/{id}/project', 'HandleWebAppProjectCreate'),
        ('POST', '/api/web-apps/{id}/project/attach', 'HandleWebAppProjectAttach'),
        ('DELETE', '/api/web-apps/{id}/project', 'HandleWebAppProjectDetach'),
        ('POST', '/api/web-apps/{id}/environments/clone', 'HandleWebEnvironmentClone'),
        ('POST', '/api/web-apps/{id}/environments/promotion/preview', 'HandleWebEnvironmentPromotionPreview'),
        ('POST', '/api/web-apps/{id}/environments/promote', 'HandleWebEnvironmentPromote'),
        ('POST', '/api/web-projects/{projectId}/previews/{number}/refresh', 'HandleWebPreviewRefresh'),
        ('POST', '/api/web-projects/{projectId}/previews/{previewId}/close', 'HandleWebPreviewClose'),
        ('POST', '/api/web-apps/_worker/claim', 'HandleWebWorkerClaim'),
        ('POST', '/api/web-apps/_worker/report', 'HandleWebWorkerReport'),
        ('POST', '/api/web-apps/{id}/backups', 'HandleWebBackupCreate'),
        ('GET', '/api/web-apps/{id}/runtime/profile', 'HandleWebRuntimeGet'),
        ('PATCH', '/api/web-apps/{id}/runtime/profile', 'HandleWebRuntimeUpdate'),
        ('GET', '/api/web-apps/{id}/storage', 'HandleWebStorageGet'),
        ('PATCH', '/api/web-apps/{id}/storage', 'HandleWebStorageUpdate'),
    ]
    text = ''
    for method, path, handler in routes:
        auth = '' if '/_worker/' in path else ',$apis.requireAuth("users")'
        text += f'routerAdd({json.dumps(method)},{json.dumps(path)},e=>require(`${{__hooks}}/mothership`).{handler}(e){auth});\n'
    text += """routerAdd('POST','/test/project',e=>{if(!e.auth?.getBool('superAdmin'))throw new ForbiddenError();const m=require(`${__hooks}/mothership`),i=e.requestInfo().body;if(i.action==='webhook')return e.json(200,m.handleWebPreviewWebhook(i.body,i.delivery));if(i.action==='maintenance'){m.HandleWebPreviewMaintenance();return e.json(200,{ok:true})}const r=$app.findRecordById('web_apps',i.appId);if(i.action==='change'){if(i.env)r.set('envEncrypted',m.encryptWebValue(i.env));if(i.config)r.set('config',i.config);if(i.name)r.set('name',i.name);$app.save(r)}if(i.action==='publish'){const resolved=m.resolveWebEnvironment(r),d=new Record($app.findCollectionByNameOrId('web_deployments'));d.set('appId',r.id);d.set('action','deploy');d.set('status','ready');d.set('commit',i.commit||'c'.repeat(40));d.set('snapshotEncrypted',m.encryptWebValue({config:JSON.parse(r.getString('config')),env:resolved.env,projectEnvironment:resolved.context}));d.set('result',{image:'ph-web/'+r.id+':'+('d'.repeat(15)),imageId:'sha256:'+('e'.repeat(64)),available:true});$app.save(d);r.set('activeDeployment',d.id);r.set('desiredCommit',d.getString('commit'));r.set('status','running');$app.save(r)}if(i.action==='queue')return e.json(200,{id:m.queueWebDeployment(r,i.jobAction,e.auth.id,i.extra||{}).id});if(i.action==='backup-export')return e.json(200,m.exportWebEnvironmentBackup(r));if(i.action==='backup-restore'){m.restoreWebEnvironmentBackup(r,i.state);return e.json(200,{ok:true})}return e.json(200,{name:r.getString('name'),config:JSON.parse(r.getString('config')),env:m.decryptWebValue(r.getString('envEncrypted')),resolved:m.resolveWebEnvironment(r),activeDeployment:r.getString('activeDeployment'),desiredCommit:r.getString('desiredCommit')})},$apis.requireAuth('users'));\n"""
    (hooks / 'test.pb.js').write_text(text)
    fakebin = root / 'bin'; fakebin.mkdir(); github_state = root / 'github-state.json'; github_state.write_text('{}')
    wrapper = fakebin / 'python3'
    wrapper.write_text('#!' + sys.executable + '\n' + "import json,os,pathlib,sys\np=pathlib.Path(sys.argv[-1])\nif len(sys.argv)==4 and p.is_file() and p.name.startswith('.web-github-'):\n d=json.loads(p.read_text());p.unlink();assert d['action']=='pull-request';state=json.loads(pathlib.Path(os.environ['PH_TEST_GITHUB_STATE']).read_text());print(json.dumps(state[str(d['number'])]));sys.exit(0)\nos.execv(" + repr(sys.executable) + ',[' + repr(sys.executable) + ']+sys.argv[1:])\n')
    wrapper.chmod(0o700)
    env = {**os.environ, 'PATH': str(fakebin) + os.pathsep + os.environ.get('PATH', ''), 'PH_TEST_GITHUB_STATE': str(github_state), 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_MASTER_KEY': 'p' * 32, 'WEB_APPS_WORKER_SECRET': worker_secret, 'WEB_APPS_ROOT': str(root / 'web'), 'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid'}
    password, email = 'P' + secrets.token_urlsafe(32), 'projects@example.invalid'
    data = root / 'pb_data'
    subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(data), '--hooksDir', str(root / 'empty-hooks'), '--migrationsDir', str(source / 'pb_migrations')], env=env, check=True, capture_output=True)
    log = (root / 'process.log').open('wb')
    process = subprocess.Popen([args.pocketbase, 'serve', '--dir', str(data), '--hooksDir', str(hooks), '--migrationsDir', str(source / 'pb_migrations'), '--http', f'127.0.0.1:{port}'], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        if process.poll() is not None: raise RuntimeError('PocketBase failed to start')
        try: request('/api/health'); break
        except OSError: time.sleep(.1)
    su = request('/api/collections/_superusers/auth-with-password', body={'identity': email, 'password': password})['token']
    def user(name, admin):
        row = request('/api/collections/users/records', su, {'email': name + '@example.invalid', 'username': name, 'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': admin, 'subscription': 'premium', 'subscription_quantity': 1})
        return row, request('/api/collections/users/auth-with-password', body={'identity': row['email'], 'password': password})['token']
    actor, token = user('projectadmin', True); _, plain = user('projectreader', False); _, other = user('projectother', True)
    def app(name): return request('/api/collections/web_apps/records', su, {'name': name, 'uid': actor['id'], 'status': 'stopped', 'config': {'repository': 'owner/repo', 'branch': 'main', 'rootDirectory': '.', 'healthPath': '/', 'generatedDomain': 'web-' + secrets.token_hex(6) + '.example.invalid'}})
    def fixture(ident, action='state', **extra): return request('/test/project', token, {'appId': ident, 'action': action, **extra})
    def finish_jobs():
        for job in request('/api/collections/web_deployments/records?perPage=500', su)['items']:
            if job['status'] in ['queued', 'running']: request('/api/collections/web_deployments/records/' + job['id'], su, {'status': 'failed'}, method='PATCH')
    dev = app('Development'); dev_id = dev['id']; base = '/api/web-apps/' + dev_id
    dev_local = {'BUILD_TOKEN': {'value': 'same-build', 'build': True, 'runtime': True}, 'RUNTIME': {'value': 'DEV_RUNTIME_PRIVATE', 'build': False, 'runtime': True}, 'DEV_ONLY': {'value': 'dev-only-private', 'build': False, 'runtime': True}}
    fixture(dev_id, 'change', env=dev_local)
    legacy = fixture(dev_id); assert legacy['resolved']['env'] == dev_local and legacy['resolved']['context']['environment'] == 'legacy'
    request(base + '/environments', status=401); request(base + '/environments', plain, status=404); request('/api/collections/web_project_variables/records', token, status=403)
    project = request(base + '/project', token, {'name': 'Project fixture', 'environment': 'dev'}, status=201)['project']; project_id = project['id']
    def variables(scope, text):
        path = f'/api/web-projects/{project_id}/variables/{scope}'; revision = request(path, token)['revision']; body = {'revision': revision, 'text': text, 'mode': 'replace'}
        preview = request(path + '/preview', token, body); return request(path, token, {**body, 'previewToken': preview['previewToken']}, method='PUT')
    variables('dev', "# pockethost: build=true runtime=false\nBUILD_SHARED='shared-build'\n# pockethost: build=false runtime=true\nDEV_SHARED='dev-shared-private'")
    variables('prod', "# pockethost: build=true runtime=false\nBUILD_SHARED='shared-build'\n# pockethost: build=false runtime=true\nPROD_SHARED='prod-shared-private'")
    variables('preview', "# pockethost: build=false runtime=true\nPREVIEW_ONLY='preview-private'")
    masked = request(f'/api/web-projects/{project_id}/variables/prod', token); assert masked['revealed'] is False and 'prod-shared-private' not in json.dumps(masked) and all('value' not in row for row in masked['variables'])
    scope_path = f'/api/web-projects/{project_id}/variables/prod'; stale_body = {'revision': masked['revision'], 'text': "OTHER='x'", 'mode': 'merge'}; stale_preview = request(scope_path + '/preview', token, stale_body)
    request(scope_path, other, {**stale_body, 'previewToken': stale_preview['previewToken']}, method='PUT', status=400)
    variables('prod', "# pockethost: build=true runtime=false\nBUILD_SHARED='shared-build'\n# pockethost: build=false runtime=true\nPROD_SHARED='prod-shared-private'")
    request(scope_path, token, {**stale_body, 'previewToken': stale_preview['previewToken']}, method='PUT', status=400)
    for invalid in ['A=x\nA=y', 'NODE_OPTIONS=bad']:
        request(scope_path + '/preview', token, {'revision': request(scope_path, token)['revision'], 'text': invalid, 'mode': 'replace'}, status=400)
    checks = ['legacy_compatibility_roles_masked_shared_variables_actor_revision_and_validation']
    prod = request(base + '/environments/clone', token, {'environment': 'prod', 'branch': 'release'}, status=201)['app']; prod_id = prod['id']
    assert fixture(prod_id)['env'] == {} and 'DEV_SHARED' not in fixture(prod_id)['resolved']['env'] and fixture(prod_id)['resolved']['env']['PROD_SHARED']['value'] == 'prod-shared-private'
    assert prod['generatedDomain'] != dev['config']['generatedDomain'] and prod['branch'] == 'release'
    request(base + '/environments/clone', token, {'environment': 'prod', 'branch': 'release'}, status=400)
    request(base + '/environments/clone', token, {'environment': 'preview', 'copyVariables': True}, status=400)
    copied = request(base + '/environments/clone', token, {'environment': 'staging', 'copyVariables': True}, status=201)['app']; assert fixture(copied['id'])['env'] == dev_local
    prod_local = {'BUILD_TOKEN': {'value': 'same-build', 'build': True, 'runtime': True}, 'RUNTIME': {'value': 'PROD_RUNTIME_PRIVATE', 'build': False, 'runtime': True}, 'PROD_ONLY': {'value': 'prod-only-private', 'build': False, 'runtime': True}}
    fixture(prod_id, 'change', env=prod_local); fixture(dev_id, 'publish')
    preview = request(base + '/environments/promotion/preview', token, {'targetAppId': prod_id}); assert preview['mode'] == 'image' and 'DEV_ONLY' not in preview['variableNames'] and 'PROD_ONLY' in preview['variableNames']
    fixture(prod_id, 'change', name='Concurrent target edit')
    request(base + '/environments/promote', token, preview, status=400)
    preview = request(base + '/environments/promotion/preview', token, {'targetAppId': prod_id}); promoted = request(base + '/environments/promote', token, preview, status=202)['deployment']
    job = worker('claim', {})['job']; assert job['id'] == promoted['id'] and job['action'] == 'promote' and job['snapshot']['rollbackResult']['sourceAppId'] == dev_id and job['snapshot']['rollbackResult']['imageSource'] == 'sha256:' + 'e' * 64
    assert job['snapshot']['env']['RUNTIME']['value'] == 'PROD_RUNTIME_PRIVATE' and 'DEV_ONLY' not in job['snapshot']['env'] and 'DEV_SHARED' not in job['snapshot']['env']
    fixture(prod_id, 'change', name='Changed during publication')
    request('/api/web-apps/_worker/report', body={'id': job['id'], 'stageKey': 'publish'}, private=True, status=400)
    finish_jobs()
    variables('prod', "# pockethost: build=true runtime=false\nBUILD_SHARED='different-production-build'\n# pockethost: build=false runtime=true\nPROD_SHARED='prod-shared-private'")
    preview = request(base + '/environments/promotion/preview', token, {'targetAppId': prod_id}); assert preview['mode'] == 'build'
    promoted = request(base + '/environments/promote', token, preview, status=202)['deployment']; job = worker('claim', {})['job']; assert job['action'] == 'deploy' and job['commit'] == 'c' * 40 and job['snapshot']['env']['BUILD_SHARED']['value'] == 'different-production-build'
    finish_jobs(); checks.append('clones_default_no_secrets_domains_branches_and_target_only_promotion_image_or_rebuild_conflicts')
    # The preview is created and queued inside one transaction. Its immutable
    # worker snapshot must read the just-created profile/volumes through that
    # same transaction, rather than silently seeing global default settings.
    source_runtime_path = f'/api/web-apps/{prod_id}/runtime/profile'
    source_profile = request(source_runtime_path, token)
    source_profile = request(source_runtime_path, token, {'profile': {'kind': 'nextjs', 'port': 3000, 'resources': {'memoryMiB': 256, 'cpuMillicores': 200, 'pids': 64}}, 'revision': source_profile['revision']}, method='PATCH')['profile']
    source_storage_path = f'/api/web-apps/{prod_id}/storage'
    source_storage = request(source_storage_path, token)
    source_volumes = [{'name': 'preview-data', 'target': '/data', 'sizeMiB': 16, 'readOnly': False}]
    source_storage = request(source_storage_path, token, {'volumes': source_volumes, 'revision': source_storage['revision'], 'retention': 1}, method='PATCH')
    assert source_storage['volumes'] == source_volumes
    settings = {'name': 'Project fixture', 'previewEnabled': True, 'previewSourceAppId': prod_id, 'previewMax': 1, 'previewTtlHours': 1}
    request('/api/web-projects/' + project_id, token, settings, method='PATCH')
    def pr(number, association='MEMBER', fork=False, state='open', commit='f' * 40, draft=False):
        all_prs = json.loads(github_state.read_text()); all_prs[str(number)] = {'number': number, 'state': state, 'draft': draft, 'author_association': association, 'head': {'sha': commit, 'ref': 'feature-' + str(number), 'repo': {'id': 2 if fork else 1, 'full_name': 'outside/fork' if fork else 'owner/repo'}}, 'base': {'repo': {'id': 1, 'full_name': 'owner/repo'}}}; github_state.write_text(json.dumps(all_prs))
    def webhook(number, delivery, action='synchronize'): return request('/test/project', token, {'action': 'webhook', 'delivery': delivery, 'body': {'action': action, 'number': number, 'repository': {'full_name': 'owner/repo'}, 'pull_request': {'head': {'sha': '0' * 40}}}})
    for number, association, fork in [(1, 'OWNER', True), (2, 'CONTRIBUTOR', False), (3, 'COLLABORATOR', False)]:
        pr(number, association, fork); assert webhook(number, 'delivery-' + str(number))['queued'] == 0
    pr(4); assert webhook(4, 'trusted-pr-4')['queued'] == 1
    assert webhook(4, 'trusted-pr-4')['duplicate']
    contexts = request(base + '/environments', token)['items']; member = next(row for row in contexts if row['previewNumber'] == 4); preview_id = member['appId']; state = fixture(preview_id)
    assert state['env'] == {} and set(state['resolved']['env']) == {'PREVIEW_ONLY'} and 'PROD_RUNTIME_PRIVATE' not in json.dumps(state) and 'prod-shared-private' not in json.dumps(state)
    request('/api/web-apps/' + preview_id + '/backups', token, {}, status=400)
    preview_job = worker('claim', {})['job']; assert preview_job['appId'] == preview_id and set(preview_job['snapshot']['env']) == {'PREVIEW_ONLY'} and preview_job['snapshot']['projectEnvironment']['environment'] == 'preview'
    assert preview_job['snapshot']['runtime']['resources'] == source_profile['resources'] == {'memoryMiB': 256, 'cpuMillicores': 200, 'pids': 64}
    assert preview_job['snapshot']['runtime']['kind'] == 'nextjs' and preview_job['snapshot']['runtime']['port'] == 3000
    assert preview_job['snapshot']['storage'] == source_volumes
    assert 'PROD_RUNTIME_PRIVATE' not in json.dumps(preview_job['snapshot']) and 'prod-shared-private' not in json.dumps(preview_job['snapshot'])
    finish_jobs()
    pr(5, 'OWNER'); assert webhook(5, 'quota-pr-5')['queued'] == 0
    pr(4, commit='a' * 40); assert webhook(4, 'newer-pr-4')['queued'] == 1 and fixture(preview_id)['desiredCommit'] == 'a' * 40
    finish_jobs(); pr(4, state='closed'); assert webhook(4, 'closed-pr-4', 'closed')['closed'] == 1
    removal = worker('claim', {})['job']; assert removal['action'] == 'preview-remove' and removal['appId'] == preview_id and removal['snapshot']['preview'] == {'appId': preview_id, 'sourceAppId': prod_id, 'isPreview': True}
    assert removal['snapshot']['env'] == {}
    worker('report', {'id': removal['id'], 'status': 'ready', 'result': {'removed': True}})
    request('/test/project', token, {'action': 'maintenance'})
    request('/api/collections/web_apps/records/' + preview_id, su, status=404)
    assert request('/api/collections/web_apps/records/' + prod_id, su)['id'] == prod_id
    member = next(row for row in request(base + '/environments', token)['items'] if row['previewNumber'] == 4); assert member['state'] == 'closed' and member['appId'] == ''
    pr(5, 'OWNER'); assert webhook(5, 'after-cleanup-pr-5')['queued'] == 1
    member = next(row for row in request(base + '/environments', token)['items'] if row['previewNumber'] == 5)
    request('/api/collections/web_app_environments/records/' + member['id'], su, {'expiresAt': '2020-01-01T00:00:00.000Z'}, method='PATCH')
    request('/test/project', token, {'action': 'maintenance'}); assert next(row for row in request(base + '/environments', token)['items'] if row['previewNumber'] == 5)['state'] == 'closing'
    checks.append('trusted_same_repo_owner_member_only_no_prod_secrets_authoritative_head_quota_close_and_ttl_cleanup')
    checks.append('transactional_preview_queue_copies_source_runtime_256m_200millicores_and_isolated_16m_named_volume')
    state = fixture(dev_id, 'backup-export'); original = request(f'/api/web-projects/{project_id}/variables/dev', token)['revision']
    fixture(dev_id, 'backup-restore', state=state)
    recovered = request(base + '/environments', token); assert recovered['project']['id'] != project_id and 'récupération' in recovered['project']['name']
    assert request(f'/api/web-projects/{project_id}/variables/dev', token)['revision'] == original and fixture(dev_id)['resolved']['env']['DEV_SHARED']['value'] == 'dev-shared-private'
    checks.append('project_backup_restores_scoped_variables_in_isolated_recovery_project')
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
