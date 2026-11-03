#!/usr/bin/env python3
"""Disposable native PocketBase: permissions/TOTP, encrypted credentials, leases and truthful results."""
import argparse,base64,concurrent.futures,hashlib,hmac,json,os,pathlib,secrets,shutil,socket,sqlite3,struct,subprocess,tempfile,time,urllib.error,urllib.request,sys,importlib.util,select,socketserver,threading
from unittest.mock import patch
p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=pathlib.Path);p.add_argument('--pocketbase',required=True);args=p.parse_args()
os.umask(0o077);root=pathlib.Path(tempfile.mkdtemp(prefix='kolab-check-api-')).resolve();process=None;secret='X'*80;checks=[]
with socket.socket()as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
base='http://127.0.0.1:'+str(port)
def req(path,token='',body=None,method=None,status=200,worker=False):
    request=urllib.request.Request(base+path,method=method or('POST'if body is not None else'GET'),headers={'Content-Type':'application/json',**({'Authorization':token}if token else{}),**({'X-Project-Migration':secret}if worker else{})},data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(request,timeout=20)as response:
            assert response.status==status,(path,response.status,status);raw=response.read();return json.loads(raw)if raw else None
    except urllib.error.HTTPError as error:
        raw=error.read()
        if error.code==status:return json.loads(raw)if raw else None
        raise AssertionError(path+': '+str(error.code)+' '+raw.decode())from None
def private(path,body=None,status=200):return req('/api/web-apps/_checks/'+path,body=body or{},worker=True,status=status)
try:
    source=args.source/'packages/pockethost/src/mothership-app';hooks=root/'hooks';hooks.mkdir();shutil.copy(source/'pb_hooks/mothership.js',hooks/'mothership.js')
    routes=[('GET', '/api/web-project-migrations', 'HandleProjectMigrationsGet', True), ('POST', '/api/web-project-migrations/{id}/approve', 'HandleProjectMigrationApprove', True), ('POST', '/api/web-project-migrations/{id}/release', 'HandleProjectMigrationRelease', True), ('POST', '/api/web-project-migrations/{id}/dns-approve', 'HandleProjectMigrationDnsApprove', True), ('POST', '/api/web-project-migration/_operator/{action}', 'HandleProjectMigrationOperator', False), ('POST', '/api/account/totp/setup', 'HandleDashboardTotpSetup', True), ('POST', '/api/account/totp/confirm', 'HandleDashboardTotpConfirm', True), ('POST', '/api/web-apps/{id}/actions/{action}', 'HandleWebAppAction', True), ('PATCH', '/api/web-apps/{id}', 'HandleWebAppUpdate', True)]
    text='\n'.join('routerAdd('+json.dumps(method)+','+json.dumps(path)+',e=>require(`${__hooks}/mothership`).'+handler+'(e)'+(',$apis.requireAuth("users")'if auth else'')+');'for method,path,handler,auth in routes)
    text+='\nonBootstrap(e=>{e.next();require(`${__hooks}/mothership`).InitializeDashboardTotp()});routerUse(e=>require(`${__hooks}/mothership`).GuardDashboardTotp(e));onRecordAuthRequest(e=>require(`${__hooks}/mothership`).HandleDashboardTotpAuth(e),"users");\n'
    text+='\nonRecordCreate(e=>require(`${__hooks}/mothership`).GuardProjectMigrationRecord(e));onRecordUpdate(e=>require(`${__hooks}/mothership`).GuardProjectMigrationRecord(e));onRecordDelete(e=>require(`${__hooks}/mothership`).GuardProjectMigrationDelete(e));onRecordUpdateRequest(e=>require(`${__hooks}/mothership`).GuardProjectMigrationRequest(e));onRecordDeleteRequest(e=>require(`${__hooks}/mothership`).GuardProjectMigrationRequest(e));'
    (hooks/'checks.pb.js').write_text(text)
    (root/'migration.key').write_text(secret);(root/'migration.key').chmod(0o600)
    env={**os.environ,'WEB_APPS_ENABLED':'true','WEB_APPS_MASTER_KEY':'c'*32,'WEB_APPS_WORKER_SECRET':secret,'WEB_APPS_ROOT':str(root/'web'),'DATA_ROOT':str(root/'data'),'APEX_DOMAIN':'app5.monappli.re','PUBLIC_HOST':'app5.monappli.re','WEB_PROJECT_MIGRATION_CONTROL_KEY_FILE':str(root/'migration.key'),'WEB_PROJECT_MIGRATION_ADMISSION_FILE':str(root/'admission.json')}
    data=root/'pb_data';password='P'+secrets.token_urlsafe(32);email='checks@example.invalid'
    subprocess.run([args.pocketbase,'superuser','upsert',email,password,'--dir',str(data),'--hooksDir',str(root/'empty'),'--migrationsDir',str(source/'pb_migrations')],env=env,check=True,capture_output=True)
    log=(root/'process.log').open('wb');process=subprocess.Popen([args.pocketbase,'serve','--dir',str(data),'--hooksDir',str(hooks),'--migrationsDir',str(source/'pb_migrations'),'--http','127.0.0.1:'+str(port)],env=env,stdout=log,stderr=subprocess.STDOUT)
    for _ in range(100):
        if process.poll()is not None:raise RuntimeError('Fixture startup failed')
        try:req('/api/health');break
        except OSError:time.sleep(.1)
    su=req('/api/collections/_superusers/auth-with-password',body={'identity':email,'password':password})['token']
    actor=req('/api/collections/users/records',su,{'email':email,'username':'checkadmin','password':password,'passwordConfirm':password,'verified':True,'superAdmin':True,'subscription':'premium','subscription_quantity':250})
    token=req('/api/collections/users/auth-with-password',body={'identity':email,'password':password})['token']
    setup=req('/api/account/totp/setup',token,{'password':password});key=base64.b32decode(setup['secret']+'='*(-len(setup['secret'])%8));digest=hmac.new(key,struct.pack('>Q',int(time.time())//30),hashlib.sha1).digest();offset=digest[-1]&15;code=str((struct.unpack('>I',digest[offset:offset+4])[0]&0x7fffffff)%1000000).zfill(6)
    token=req('/api/account/totp/confirm',token,{'password':password,'code':code})['token']
    sys.path.insert(0,str(args.source/'packages/pockethost/scripts'))
    import web_project_migration as migration
    from unittest.mock import patch
    def op(action,body,status=200):return req('/api/web-project-migration/_operator/'+action,body=body,status=status,worker=True)
    req('/api/web-project-migrations',status=401)
    req('/api/web-project-migration/_operator/state',body={},status=403)
    req('/api/collections/web_project_migrations/records',token,status=403)
    def fixture(compose=False):
        value={'projectId':'c'*15,'projectName':'Projet migration épreuve','revision':'f'*64,'migrationSourceBarrier':{'version':1,'projectId':'c'*15,'source':'app4','sourceWritesPaused':True,'sourceCronsPaused':True,'sourceWorkersPaused':True,'attestationOnly':True},'apps':[{'id':'a'*15,'name':'Application fixture','deploymentId':'d'*15,'configuration':{'config':{'repository':'fixture/source','branch':'main','rootDirectory':'.','healthPath':'/','generatedDomain':'web-source.app4.monappli.re'},'env':{'PRIVATE_PASSWORD':{'value':'private-migration-fixture','build':False,'runtime':True},'SMTP_PASSWORD':{'value':'private-mail-fixture','build':False,'runtime':True}},'runtime':{'kind':'auto','resources':{'memoryMiB':768}},'storage':[]},'bindings':[{'instanceId':'b'*15,'port':4825}],'scheduledTasks':[{'id':'g'*15,'name':'Sync','command':'node scripts/sync.js --token=fixture-only-secret','schedule':'0 2 * * *','timezone':'Indian/Reunion','timeoutSeconds':60,'enabled':True}],'domains':[{'host':('compose'if compose else'single')+'.example.invalid','status':'active'}],'externalDependencies':[],'services':[{'name':'web','front':True,'image':'sha256:'+'1'*64,'volumes':[],'environment':['SMTP_PASSWORD=private-mail-fixture']}]}],'databases':{'b'*15:{'version':'0.40.2','configuration':{'secrets':[]},'memoryReservationMiB':0,'memoryMaximumMiB':4096,'support':[]}}}
        if compose:
            value['apps'][0]['services'].append({'name':'worker','front':False,'image':'sha256:'+'2'*64,'volumes':[],'environment':[]})
            value['apps'][0]['configuration']['runtime']={'kind':'compose','compose':{'front':'web','services':{'web':{'image':'ghcr.io/fixture/web@sha256:'+'1'*64,'port':3000},'worker':{'image':'ghcr.io/fixture/worker@sha256:'+'2'*64,'port':3001}}}}
        manifest={'platform':'app4','metadata':{'projectId':value['projectId']}}
        with patch.object(migration,'capsule',return_value=(value,manifest)),patch.object(migration.recovery.recovery,'restore_budget',return_value={'restoreBytes':1024}):plan=migration.build_plan(root,'app5',actor['id'],'3'*64)
        return value,plan
    def path(plan,action):return '/api/web-project-migrations/'+plan['id']+'/'+action
    value,plan=fixture()
    prepared=op('prepare',{'plan':plan})['item']
    assert prepared['status']=='review' and all(s not in json.dumps(prepared)for s in ['private-migration-fixture','private-mail-fixture','fixture-only-secret'])
    op('take',{'id':plan['id'],'revision':plan['revision']},400)
    req(path(plan,'approve'),token,{'revision':plan['revision'],'confirmation':'wrong'},status=400)
    req(path(plan,'approve'),token,{'revision':plan['revision'],'confirmation':plan['projectName']})
    # The queue's write lock is acquired before rereading a trusted maintenance lease.
    (root/'admission.json').write_text(json.dumps({'version':1,'owner':'fixture-maintenance','startedAt':int(time.time()),'expiresAt':int(time.time())+60}));(root/'admission.json').chmod(0o644)
    op('take',{'id':plan['id'],'revision':plan['revision']},503)
    assert op('state',{'id':plan['id'],'revision':plan['revision']})['item']['status']=='approved'
    (root/'admission.json').unlink()
    op('take',{'id':plan['id'],'revision':plan['revision']})
    images={plan['applications'][0]['targetId']:{'web':'sha256:'+'9'*64}}
    commit={'id':plan['id'],'revision':plan['revision'],'plan':plan,'capsule':value,'images':images}
    op('commit',commit,400)
    commit['drill']={'sandboxUid':1002,'capsuleRevision':plan['capsuleRevision'],'manifestSha256':plan['manifestSha256'],'noExternalNetwork':True,'cleanedUp':True,'services':[{'appId':'a'*15,'service':'web','status':'running-isolated'}]}
    op('commit',commit)
    target=plan['applications'][0]['targetId'];db=plan['databases'][0]['targetId']
    current=op('state',{'id':plan['id'],'revision':plan['revision']})['item'];assert current['status']=='imported_paused'and current['plan']['drill']['sandboxUid']==1002
    req('/api/web-apps/'+target,token,{'name':'Edit blocked'},'PATCH',400)
    req('/api/web-apps/'+target+'/actions/start',token,{},status=400)
    req('/api/collections/instances/records/'+db,token,{'power':True},'PATCH',403)
    req('/api/collections/instances/records/'+db,su,{'power':True},'PATCH',400)
    req('/api/collections/instances/records/'+db,token,{'power':True,'migrationLock':''},'PATCH',403)
    with sqlite3.connect(data/'data.db')as connection:
        assert connection.execute('SELECT power,migrationLock FROM instances WHERE id=?',(db,)).fetchone()==(0,plan['id'])
        row=connection.execute('SELECT envEncrypted,config,status FROM web_apps WHERE id=?',(target,)).fetchone();assert row[2]=='stopped'and 'private-migration-fixture'not in row[0]and json.loads(row[1])['autoDeploy']is False
        assert connection.execute('SELECT enabled FROM web_scheduled_tasks WHERE appId=?',(target,)).fetchone()[0]==0
    checks.append('totp_operator_scope_ciphertext_exact_plan_owner_and_suspended_native_resources')
    # Failed disk verification can happen before rollback API: no state mutation;
    # once requested, queued operations stay blocked until controlled removal.
    op('rollback',{'id':plan['id'],'revision':plan['revision']})
    req('/api/web-apps/'+target+'/actions/start',token,{},status=400)
    op('rolled-back',{'id':plan['id'],'revision':plan['revision']})
    assert op('state',{'id':plan['id'],'revision':plan['revision']})['item']['status']=='rolled_back'
    with sqlite3.connect(data/'data.db')as connection:assert connection.execute('SELECT count(*) FROM instances WHERE id=?',(db,)).fetchone()[0]==0
    checks.append('queue_admission_pause_does_not_claim_and_model_locks_block_early_start_and_metadata_edits')
    # A compose app containing a worker still requires its front to be running.
    value,plan=fixture(True);op('prepare',{'plan':plan});req(path(plan,'approve'),token,{'revision':plan['revision'],'confirmation':plan['projectName']});op('take',{'id':plan['id'],'revision':plan['revision']})
    images={plan['applications'][0]['targetId']:{'web':'sha256:'+'7'*64,'worker':'sha256:'+'8'*64}}
    drill={'sandboxUid':1002,'capsuleRevision':plan['capsuleRevision'],'manifestSha256':plan['manifestSha256'],'noExternalNetwork':True,'cleanedUp':True,'services':[{'appId':'a'*15,'service':'web','status':'running-isolated'},{'appId':'a'*15,'service':'worker','status':'inert-worker-preserved'}]}
    op('commit',{'id':plan['id'],'revision':plan['revision'],'plan':plan,'capsule':value,'images':images,'drill':drill})
    req(path(plan,'release'),token,{'revision':plan['revision'],'confirmation':plan['projectName'],'keepDeliveryDisabled':True},status=400)
    req(path(plan,'release'),token,{'revision':plan['revision'],'confirmation':plan['projectName'],'keepDeliveryDisabled':True,'sourceWorkersPaused':True})
    class FakeCf:
        def zone(self,host):return 'a'*32
        def call(self,*args):return []
    preview=migration.dns_preview(plan,FakeCf());op('dns-preview',{'id':plan['id'],'revision':plan['revision'],'preview':preview})
    consent={'revision':plan['revision'],'confirmation':plan['projectName'],'dnsRevision':preview['revision'],'sourceWritesPaused':True,'sourceCronsPaused':True,'targetVerified':True,'nonAtomicAccepted':True}
    req(path(plan,'dns-approve'),token,consent,status=400)
    target=plan['applications'][0]['targetId'];req('/api/collections/web_apps/records/'+target,su,{'status':'running'},'PATCH')
    req(path(plan,'dns-approve'),token,consent)
    op('dns-ready',{'id':plan['id'],'revision':plan['revision'],'dnsRevision':preview['revision']})
    # The UI preview contains exact old/new DNS, but never contains controller,
    # application environment, delivery credentials or task command values.
    public=req('/api/web-project-migrations',token)
    assert 'dnsPreview'in public['items'][0] and all(secret_value not in json.dumps(public)for secret_value in ['private-mail-fixture','private-migration-fixture','fixture-only-secret',secret])
    checks.append('separate_import_and_dns_consent_exact_preview_front_with_worker_guard_and_secret_free_public_view')
    print(json.dumps({'passed':True,'checks':checks}))
except Exception:
    if(root/'process.log').exists():print((root/'process.log').read_text()[-5000:])
    raise
finally:
    if process:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait()
    shutil.rmtree(root)
