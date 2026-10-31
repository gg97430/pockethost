#!/usr/bin/env python3
"""Disposable native PocketBase checks for continuous-health configuration and transitions."""
import argparse
import datetime
import json
import os
import pathlib
import secrets
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True, type=pathlib.Path)
parser.add_argument('--pocketbase', required=True)
parser.add_argument('--bundle', type=pathlib.Path)
args = parser.parse_args()
os.umask(0o077)
root = pathlib.Path(tempfile.mkdtemp(prefix='ph-health-api-')).resolve()
process = None
checks = []
with socket.socket() as server:
    server.bind(('127.0.0.1', 0))
    port = server.getsockname()[1]
base = f'http://127.0.0.1:{port}'
secret = secrets.token_urlsafe(40)


def request(path, token='', body=None, method=None, status=200, worker=False):
    call = urllib.request.Request(base + path, method=method or ('POST' if body is not None else 'GET'),
        headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {}), **({'X-Web-Worker': secret} if worker else {})},
        data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(call, timeout=15) as response:
            data = response.read()
            assert response.status == status, (path, response.status, status)
            assert response.headers.get('Cache-Control') == 'no-store' if path.startswith('/api/web-apps') else True
            return json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        if error.code == status:
            return json.loads(error.read())
        raise AssertionError(f'{path}: {error.code} ' + error.read().decode())


def private(endpoint, body, status=200):
    return request('/api/web-apps/_worker/storage/' + endpoint, body=body, status=status, worker=True)


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks = root / 'hooks'
    hooks.mkdir()
    shutil.copy(args.bundle or source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    migrations = root / 'migrations'
    migrations.mkdir()
    for migration in (source / 'pb_migrations').glob('*.js'):
        if migration.name.split('_')[0].isdigit() and int(migration.name.split('_')[0]) <= 1793270299:
            shutil.copy(migration, migrations / migration.name)
    routes = [('POST','/api/web-apps','HandleWebAppCreate',True),('GET','/api/web-apps','HandleWebAppsList',True),('GET','/api/web-apps/{id}/runtime/profile','HandleWebRuntimeGet',True),('PATCH','/api/web-apps/{id}/runtime/profile','HandleWebRuntimeUpdate',True),('POST','/api/web-apps/{id}/runtime/profile/preview','HandleWebRuntimePreview',True),('GET','/api/web-apps/{id}/storage','HandleWebStorageGet',True),('PATCH','/api/web-apps/{id}/storage','HandleWebStorageUpdate',True),('GET','/api/web-apps/{id}/storage/history','HandleWebStorageHistory',True),('POST','/api/web-apps/{id}/storage/{name}/restore/preview','HandleWebStorageRestorePreview',True),('POST','/api/web-apps/{id}/storage/{name}/{operation}','HandleWebStorageAction',True),('POST','/api/web-apps/_worker/storage/claim','HandleWebStorageClaim',False),('POST','/api/web-apps/_worker/storage/report','HandleWebStorageReport',False)]
    (hooks / 'health.pb.js').write_text('\n'.join('routerAdd(' + json.dumps(method) + ',' + json.dumps(path) + ',e=>require(`${__hooks}/mothership`).' + handler + '(e)' + (',$apis.requireAuth("users")' if auth else '') + ');' for method, path, handler, auth in routes))
    with (hooks / 'health.pb.js').open('a') as output:
        output.write("\nrouterAdd('POST','/test/publish',e=>{const mod=require(`${__hooks}/mothership`),app=$app.findRecordById('web_apps',e.requestInfo().body.id),job=new Record($app.findCollectionByNameOrId('web_deployments'));job.set('appId',app.id);job.set('action','deploy');job.set('status','ready');job.set('commit','c'.repeat(40));job.set('snapshotEncrypted',mod.encryptWebValue({config:JSON.parse(app.getString('config')),env:{},runtime:mod.runtimeWorkerSnapshot(app),storage:mod.storageWorkerSnapshot(app)}));$app.save(job);job.set('result',{image:'sha256:'+'f'.repeat(64),container:'ph-web-'+app.id+'-'+job.id,target:'http://127.0.0.1:39999'});$app.save(job);app.set('activeDeployment',job.id);app.set('status','running');$app.save(app);return e.json(200,{deploymentId:job.id})},$apis.requireAuth('users'));\n")
    with (hooks / 'health.pb.js').open('a')as output:
        output.write("\nrouterAdd('GET','/test/deployment/{id}',e=>{if(!e.auth?.getBool('superAdmin'))throw new ForbiddenError();const mod=require(`${__hooks}/mothership`),row=$app.findRecordById('web_deployments',e.request.pathValue('id')),snap=mod.decryptWebValue(row.getString('snapshotEncrypted'));return e.json(200,{image:snap.rollbackResult?.image,repository:snap.config?.repository,runtimePort:snap.runtime?.port,storageNames:(snap.storage||[]).map(x=>x.name),commit:row.getString('commit'),hasCi:!!snap.ci,hasPromotion:!!snap.promotion,hasRestoreBackup:!!snap.restoreBackup,hasStorageRestore:!!snap.storageRestore})},$apis.requireAuth('users'));\n")
    env = {**os.environ, 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_WORKER_SECRET': secret, 'WEB_APPS_MASTER_KEY': 'h' * 32,
        'WEB_APPS_ROOT': str(root / 'web'), 'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid'}
    password, email = 'P' + secrets.token_urlsafe(32), 'health@example.invalid'
    data = root / 'pb_data'
    subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(data), '--hooksDir', str(root / 'empty'), '--migrationsDir', str(migrations)], env=env, check=True, capture_output=True)
    log = (root / 'process.log').open('wb')
    process = subprocess.Popen([args.pocketbase, 'serve', '--dir', str(data), '--hooksDir', str(hooks), '--migrationsDir', str(migrations), '--http', f'127.0.0.1:{port}'], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError((root / 'process.log').read_text()[-2500:])
        try:
            request('/api/health')
            break
        except OSError:
            time.sleep(.1)
    su = request('/api/collections/_superusers/auth-with-password', body={'identity': email, 'password': password})['token']
    def user(name, admin):
        row = request('/api/collections/users/records', su, {'email': name + '@example.invalid', 'username': name, 'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': admin, 'subscription': 'premium', 'subscription_quantity': 1})
        return row, request('/api/collections/users/auth-with-password', body={'identity': row['email'], 'password': password})['token']
    actor, token = user('healthadmin', True)
    _, plain = user('healthplain', False)
    digest='ghcr.io/owner/site@sha256:'+'d'*64
    image_app=request('/api/web-apps',token,{'name':'No Git image','runtime':{'kind':'image','image':digest,'port':8080}},status=201)['app']
    assert image_app['repository']=='' and image_app['runtimeKind']=='image' and not image_app['sourceDeployable']
    assert request('/api/web-apps/'+image_app['id']+'/runtime/profile',token)['profile']['image']==digest
    worker_app=request('/api/web-apps',token,{'name':'No Git worker','runtime':{'kind':'worker','image':digest}},status=201)['app']
    assert worker_app['runtimeKind']=='worker' and request('/api/web-apps/'+worker_app['id']+'/runtime/profile',token)['profile']['port']is None
    for kind in ['nextjs','auto','dockerfile','worker']:
        request('/api/web-apps',token,{'name':'Missing source','runtime':{'kind':kind}},status=400)
    compose_app=request('/api/web-apps',token,{'name':'No Git Compose','runtime':{'kind':'compose','compose':{'front':'web','services':{'web':{'image':digest},'cache':{'image':digest}}}}},status=201)['app']
    assert not compose_app['sourceDeployable'] and compose_app['repository']==''
    request('/api/web-apps',token,{'name':'Missing Compose source','runtime':{'kind':'compose','compose':{'front':'web','services':{'web':{'image':digest},'cache':{'dockerfile':'Dockerfile'}}}}},status=400)
    old_app=request('/api/web-apps',token,{'name':'Legacy Next','repository':'owner/repo'},status=201)['app']
    assert old_app['runtimeKind']=='nextjs' and old_app['sourceDeployable']
    checks.append('atomic_image_and_background_worker_creation_without_github_preserves_legacy_source_rules')
    auto_app=request('/api/web-apps',token,{'name':'Railpack site','repository':'owner/auto','runtime':{'kind':'auto','startCommand':'npm run start'}},status=201)['app']
    assert auto_app['runtimeKind']=='auto' and auto_app['sourceDeployable']
    auto='/api/web-apps/'+auto_app['id']+'/runtime/profile';current=request(auto,token)
    assert current['profile']['kind']=='auto' and current['profile']['port']==3000 and current['profile']['startCommand']=='npm run start' and 'buildCommand' not in current['profile']
    assert 'auto' in current['capabilities']['kinds'] and current['capabilities']['railpackAvailable'] is False
    wanted={'kind':'auto','port':8000,'startCommand':'uvicorn main:app --host 0.0.0.0 --port $PORT','buildCommand':'pip install -r requirements.txt'}
    saved=request(auto,token,{'profile':wanted,'revision':current['revision']},'PATCH')['profile']
    assert {key:saved[key] for key in wanted}==wanted and saved['command']==[] and saved['entrypoint']==[]
    assert {key:request(auto,token)['profile'][key] for key in wanted}==wanted
    request(auto,token,{'profile':{**wanted,'startCommand':''}},'PATCH');assert 'startCommand' not in request(auto,token)['profile']
    request(auto,token,{'profile':{**wanted,'pruneDependencies':True}},'PATCH');assert request(auto,token)['profile']['pruneDependencies'] is True
    request(auto,token,{'profile':{**wanted,'pruneDependencies':False}},'PATCH');assert 'pruneDependencies' not in request(auto,token)['profile']
    for bad in [{'kind':'auto','command':['npm','start']},{'kind':'auto','entrypoint':['sh']},{'kind':'auto','buildTarget':'build'},{'kind':'auto','startCommand':'a\nb'},{'kind':'auto','startCommand':'x'*1001},{'kind':'dockerfile','startCommand':'npm start'},{'kind':'image','image':digest,'buildCommand':'make'},{'kind':'auto','pruneDependencies':'true'},{'kind':'dockerfile','pruneDependencies':True}]:
        request(auto,token,{'profile':bad},'PATCH',400);request(auto+'/preview',token,{'profile':bad},status=400)
    warnings=request(auto+'/preview',token,{'profile':wanted})['warnings']
    assert any('PORT=8000' in item for item in warnings) and any('RAILPACK_' in item for item in warnings) and any('Railpack n’est pas installé' in item for item in warnings)
    assert any('composer:latest' in item and 'non épinglés' in item for item in warnings)
    assert not any('Railpack' in item for item in request(auto+'/preview',token,{'profile':{'kind':'dockerfile'}})['warnings'])
    listed=request('/api/web-apps',token);assert listed['capabilities']['railpackAvailable'] is False and listed['capabilities']['gitAvailable'] is False
    (root/'web').mkdir(exist_ok=True);(root/'web'/'worker-status.json').write_text(json.dumps({'ready':True,'rootless':True,'checkedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'git':True,'railpack':True}))
    assert request(auto,token)['capabilities']['railpackAvailable'] is True and request('/api/web-apps',token)['capabilities']['railpackAvailable'] is True
    assert not any('Railpack n’est pas installé' in item for item in request(auto+'/preview',token,{'profile':wanted})['warnings'])
    (root/'web'/'worker-status.json').unlink()
    checks.append('auto_railpack_profile_text_commands_preview_warnings_and_capability')
    app=request('/api/collections/web_apps/records',su,{'name':'Runtime fixture','uid':actor['id'],'status':'stopped','config':{'repository':'owner/repo','branch':'main','rootDirectory':'.','healthPath':'/','generatedDomain':'web-runtime.example.invalid'}})
    ident=app['id'];runtime='/api/web-apps/'+ident+'/runtime/profile';storage='/api/web-apps/'+ident+'/storage'
    request(runtime,status=401);request(runtime,plain,status=404)
    for collection in ['web_runtime_profiles','web_storage_settings','web_storage_jobs']:request('/api/collections/'+collection+'/records',token,status=403)
    assert request(runtime,token)['profile']['kind']=='nextjs'
    image='ghcr.io/owner/site@sha256:'+'d'*64
    saved=request(runtime,token,{'profile':{'kind':'image','image':image,'port':8080},'revision':'','registry':{'server':'ghcr.io','username':'fixture','password':'registry-private-value'}},'PATCH')
    assert saved['registry']['hasPassword'] and 'registry-private-value'not in json.dumps(saved)
    with sqlite3.connect(data/'data.db')as db:
        cipher=db.execute('SELECT registryEncrypted FROM web_runtime_profiles WHERE appId=?',(ident,)).fetchone()[0]
        assert cipher and 'registry-private-value'not in cipher
    request(runtime,token,{'profile':{'kind':'image','image':image},'revision':'old'},'PATCH',400)
    for bad in [{'kind':'image','image':'nginx:latest'},{'kind':'dockerfile','dockerfile':'../outside'},{'kind':'worker','resources':{'memoryMiB':99999}}]:request(runtime,token,{'profile':bad},'PATCH',400)
    checks.append('admin_only_profiles_locked_collections_immutable_images_and_encrypted_registry')
    compose={'kind':'compose','compose':{'front':'web','services':{'web':{'image':image},'cache':{'image':image}}}}
    plan=request(runtime+'/preview',token,{'profile':compose});assert plan['strategy']=='rolling'and plan['resources']['memoryMiB']<=1536
    for key,value in [('privileged',True),('network_mode','host'),('volumes',['/etc:/data']),('ports',['80:80']),('environment',{'TOKEN':'literal'})]:
        bad=json.loads(json.dumps(compose));bad['compose']['services']['web'][key]=value;request(runtime+'/preview',token,{'profile':bad},status=400)
    checks.append('compose_preflight_denies_privileges_host_mounts_ports_and_literal_secrets')
    config=request(storage,token);assert config['volumes']==[]and config['limits']['hardQuota']is False
    volumes=[{'name':'uploads','target':'/data','sizeMiB':16,'readOnly':False}]
    config=request(storage,token,{'volumes':volumes,'revision':config['revision'],'retention':1},'PATCH');assert config['retention']==1
    assert request(runtime+'/preview',token,{'profile':compose})['strategy']=='recreate'
    for bad in [[{'name':'uploads','target':'/etc'}],[{'name':'uploads','target':'/data/../etc'}],[{'name':'uploads','target':'/data'},{'name':'other','target':'/data/x'}]]:request(storage,token,{'volumes':bad},'PATCH',400)
    checks.append('named_volume_target_quota_validation_and_explicit_recreate_strategy')
    def backup():
        row=request(storage+'/uploads/backup',token,{},status=201)['job'];claim=private('claim',{})['job'];assert claim['id']==row['id']and claim['operation']=='backup';result={'object':'app1/web-app-backups/apps/'+ident+'/volumes/uploads/'+row['id']+'.phwv','bytes':128,'dataBytes':4,'files':1,'sourceVolume':'ph-web-data-'+ident+'-uploads-v1'};return row,claim,result
    first,claim,result=backup()
    request(storage+'/uploads/backup',token,{},status=400)
    private('report',{'id':first['id'],'lease':'wrong','status':'running'},401)
    assert private('report',{'id':first['id'],'lease':claim['lease'],'status':'running'})['ok']
    private('report',{'id':first['id'],'lease':claim['lease'],'status':'ready','result':result})
    assert private('report',{'id':first['id'],'lease':claim['lease'],'inspect':True})['status']=='ready'
    assert request(storage+'/history',token)['items'][0]['status']=='ready'
    checks.append('exclusive_storage_jobs_hashed_lease_progress_and_paginated_history')
    request('/test/publish',token,{'id':ident})
    saved_profile=request(runtime,token);request(runtime,token,{'profile':{**saved_profile['profile'],'port':9090},'revision':saved_profile['revision']},'PATCH')
    saved_app=request('/api/collections/web_apps/records/'+ident,su);request('/api/collections/web_apps/records/'+ident,su,{'config':{**saved_app['config'],'repository':'pending/changes'}},'PATCH')
    preview=request(storage+'/uploads/restore/preview',token,{'backupId':first['id']})
    _,other=user('otherstorage',True)
    body={'backupId':first['id'],'previewId':preview['previewId'],'previewToken':preview['previewToken']}
    request(storage+'/uploads/restore',other,body,status=400)
    queued=request(storage+'/uploads/restore',token,body,status=201)['job'];restoring=private('claim',{})['job'];assert restoring['id']==queued['id']and restoring['backup']['backupId']==first['id']
    restored={'activeVolume':'ph-web-data-'+ident+'-uploads-r'+queued['id'],'previousVolume':'ph-web-data-'+ident+'-uploads-v1','previousContainers':[],'dataBytes':4,'backupId':first['id'],'restartRequired':True}
    private('report',{'id':queued['id'],'lease':restoring['lease'],'status':'ready','result':restored})
    private('report',{'id':queued['id'],'lease':restoring['lease'],'status':'ready','result':restored})
    deploys=request('/api/collections/web_deployments/records?filter='+urllib.parse.quote('appId="'+ident+'" && action="apply-runtime"'),su)['items'];assert len(deploys)==1
    frozen=request('/test/deployment/'+deploys[0]['id'],token)
    assert frozen['image']=='sha256:'+'f'*64 and frozen['repository']=='owner/repo'and frozen['runtimePort']==8080 and frozen['storageNames']==['uploads']and frozen['commit']=='c'*40 and frozen['hasStorageRestore']
    assert not any(frozen[key]for key in ['hasCi','hasPromotion','hasRestoreBackup'])
    request(storage+'/uploads/restore',token,body,status=400)
    checks.append('restore_preview_bound_to_actor_revision_and_source_then_idempotent_restart_queue')
    second,claim2,result2=backup();private('report',{'id':second['id'],'lease':claim2['lease'],'status':'ready','result':result2})
    deletion=private('claim',{})['job'];assert deletion['id']==first['id']and deletion['operation']=='delete'
    private('report',{'id':deletion['id'],'lease':deletion['lease'],'status':'ready','result':{'deleted':True}})
    history=request(storage+'/history?perPage=1&page=2',token);assert len(history['items'])==1and history['hasMore']and history['totalItems']>=3
    checks.append('retention_queues_exact_snapshot_deletion_and_history_paginates_all_records')
    changed_preview=request(storage+'/uploads/restore/preview',token,{'backupId':second['id']})
    changed_job=request(storage+'/uploads/restore',token,{'backupId':second['id'],'previewId':changed_preview['previewId'],'previewToken':changed_preview['previewToken']},status=201)['job'];changed_claim=private('claim',{})['job']
    request('/test/publish',token,{'id':ident})
    private('report',{'id':changed_job['id'],'lease':changed_claim['lease'],'status':'ready','result':{**restored,'activeVolume':'ph-web-data-'+ident+'-uploads-r'+changed_job['id']}},400)
    private('report',{'id':changed_job['id'],'lease':changed_claim['lease'],'status':'failed'})
    checks.append('storage_restart_refuses_a_publication_changed_after_preview')
    print(json.dumps({'passed':True,'checks':checks}),flush=True)
except Exception:
    if (root / 'process.log').exists():
        print((root / 'process.log').read_text()[-2500:], flush=True)
    raise
finally:
    if process:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
    shutil.rmtree(root, ignore_errors=True)
