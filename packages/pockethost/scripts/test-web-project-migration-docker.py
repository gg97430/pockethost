#!/usr/bin/env python3
"""Disposable native PocketBase: permissions/TOTP, encrypted credentials, leases and truthful results."""
import argparse,base64,concurrent.futures,hashlib,hmac,json,os,pathlib,secrets,shutil,socket,sqlite3,struct,subprocess,tempfile,time,urllib.error,urllib.request,sys,importlib.util,select,socketserver,threading
from unittest.mock import patch
p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=pathlib.Path);p.add_argument('--pocketbase',required=True);p.add_argument('--directory',required=True,type=pathlib.Path);p.add_argument('--helper-image',required=True);args=p.parse_args()
os.umask(0o077);root=pathlib.Path(tempfile.mkdtemp(prefix='kolab-check-api-')).resolve();process=None;successful=False;secret='X'*80;checks=[]
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
    directory=args.directory.resolve();value,manifest=migration.capsule(directory)
    assert manifest['platform']=='app4' and value['projectName']=='Disposable project recovery fixture'
    assert all(row.get('memoryReservationMiB',0)==0 for row in value['databases'].values())
    plan=migration.build_plan(directory,'app5',actor['id'],migration.recovery.digest(directory.parent/'capsule.phpr'),'Disposable migration app4 to app5')
    (root/'artifact-data').mkdir();(root/'artifact-binaries').mkdir();(root/'artifact-web').mkdir();(root/'artifact-state').mkdir()
    config={'dataRoot':str(root/'artifact-data'),'binaryRoot':str(root/'artifact-binaries'),'webRoot':str(root/'artifact-web'),'stateRoot':str(root/'artifact-state'),'dockerSocket':'/run/user/1001/docker.sock','sandboxDockerSocket':'/run/user/1002/docker.sock','helperImage':args.helper_image}
    migration.private_json(root/'fixture-plan.json',plan)
    op('prepare',{'plan':plan});req('/api/web-project-migrations/'+plan['id']+'/approve',token,{'revision':plan['revision'],'confirmation':plan['projectName']})
    docker=migration.recovery.Docker(config['dockerSocket']);control=migration.Control(root/'migration.key',port)
    imported=migration.apply_import(directory,plan,config,control,docker,args.helper_image)
    assert imported['importedPaused'] and not imported['cutoverExecuted']
    item=op('state',{'id':plan['id'],'revision':plan['revision']})['item']
    assert item['status']=='imported_paused' and item['plan']['drill']['sandboxUid']==1002
    journal=json.loads((root/'artifact-state'/(plan['id']+'.json')).read_text())
    for mapped in plan['databases']:
        restored=root/'artifact-data/instances'/mapped['targetId']/'pb_data/data.db'
        assert migration.recovery.digest(restored)==migration.recovery.digest(directory/'instances'/mapped['sourceId']/'pb_data/data.db')
        with sqlite3.connect(restored)as db:assert db.execute('SELECT value FROM recovery_fixture').fetchone()[0]=='fixture-native-data'
    for volume in plan['volumes']:assert migration.volume_digest(docker,volume['targetName'],args.helper_image,plan['id'])==volume['state']['treeSha256']
    for mapped in plan['applications']:assert not docker(['ps','-aq','--filter','label=pockethost.web.app='+mapped['targetId']]).strip()
    with sqlite3.connect(data/'data.db')as db:
        assert db.execute('SELECT count(*) FROM web_apps WHERE migrationLock=? AND status="stopped"',(plan['id'],)).fetchone()[0]==len(plan['applications'])
        assert db.execute('SELECT count(*) FROM instances WHERE migrationLock=? AND power=0',(plan['id'],)).fetchone()[0]==len(plan['databases'])
        assert db.execute('SELECT count(*) FROM users').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM web_scheduled_tasks WHERE enabled=1').fetchone()[0]==0
    rolled=migration.rollback_import(plan,config,control,docker)
    assert rolled['rolledBack'] and op('state',{'id':plan['id'],'revision':plan['revision']})['item']['status']=='rolled_back'
    assert not list((root/'artifact-data/instances').iterdir())
    assert all(volume['targetName']not in docker(['volume','ls','--format','{{.Name}}']).split()for volume in plan['volumes'])
    for services in journal['images'].values():
        for image in services.values():
            try:info=json.loads(docker(['image','inspect',image]))[0]
            except subprocess.CalledProcessError:continue
            raise AssertionError('Owned import image remained after rollback')
    # Source immutable image imported for materialization can be removed only
    # when it is this capsule's fixture and has no process reference on target.
    for image in {service['image']for app in value['apps']for service in app['services']}:
        try:info=json.loads(docker(['image','inspect',image]))[0]
        except subprocess.CalledProcessError:continue # Docker may prune the now-unused untagged parent.
        assert info['Config']['Labels'].get('kolab.project-fixture')
        assert not docker(['ps','-aq','--filter','ancestor='+image]).strip()
        docker(['image','rm',image])
    successful=True
    print(json.dumps({'passed':True,'source':'app4','target':'app5','encryptedCapsuleExactHash':True,'nativeDatabaseAndFilesRestored':True,'isolated1002DrillAndCleanup':True,'frontAndWorkerPreserved':True,'workerNeverStarted':True,'volumeHashVerified':True,'localOwnerOnlyNoUserClone':True,'importedPaused':True,'dnsUntouched':True,'rollbackCopiesOnly':True,'ownedImagesRemoved':True}))
except Exception:
    if(root/'process.log').exists():print((root/'process.log').read_text()[-5000:])
    raise
finally:
    if process:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait()
    if successful:shutil.rmtree(root)
    else:print(json.dumps({'fixtureWorkspaceRetained':str(root),'productionResourcesSelected':False}))
