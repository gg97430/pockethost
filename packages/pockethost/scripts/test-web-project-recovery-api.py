#!/usr/bin/env python3
"""Disposable native PB/API tests; replaces the fixed service-key path only in its private compiled fixture copy."""
import argparse,base64,hashlib,hmac,json,os,pathlib,secrets,shutil,socket,sqlite3,struct,subprocess,tempfile,time,urllib.error,urllib.request
p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=pathlib.Path);p.add_argument('--pocketbase',required=True);args=p.parse_args()
os.umask(0o077);root=pathlib.Path(tempfile.mkdtemp(prefix='kolab-project-api-')).resolve();process=None;worker_secret=secrets.token_urlsafe(40);service_secret=secrets.token_hex(32)
key=root/'service.key';key.write_text(service_secret);key.chmod(0o600)
with socket.socket()as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
base='http://127.0.0.1:'+str(port)
def req(path,token='',body=None,method=None,status=200,service=False,worker=False):
    headers={'Content-Type':'application/json',**({'Authorization':token}if token else{}),**({'X-Project-Recovery':service_secret}if service else{}),**({'X-Web-Worker':worker_secret}if worker else{})}
    request=urllib.request.Request(base+path,method=method or('POST'if body is not None else'GET'),headers=headers,data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(request,timeout=20)as response:assert response.status==status,(path,response.status,status);raw=response.read();return json.loads(raw)if raw else None
    except urllib.error.HTTPError as error:
        raw=error.read()
        if error.code==status:return json.loads(raw)if raw else None
        raise AssertionError(path+': '+str(error.code)+' '+raw.decode())from None
def private(path,body=None,status=200):return req('/api/web-project-recovery/_worker/'+path,body=body or{},status=status,service=True)
try:
    source=args.source/'packages/pockethost/src/mothership-app';hooks=root/'hooks';hooks.mkdir()
    compiled=(source/'pb_hooks/mothership.js').read_text();assert '/etc/kolab/project-recovery-controller.key'in compiled
    (hooks/'mothership.js').write_text(compiled.replace('/etc/kolab/project-recovery-controller.key',str(key)).replace('/run/kolab-platform-maintenance/admission.json',str(root/'admission.json')))
    routes=[('GET','/api/web-project-recovery','HandleProjectRecoveryGet',True),('GET','/api/web-project-recovery/projects/{projectId}/preview','HandleProjectRecoveryPreview',True),('POST','/api/web-project-recovery/projects/{projectId}/runs','HandleProjectRecoveryQueue',True),('POST','/api/web-project-recovery/_worker/claim','HandleProjectRecoveryClaim',False),('POST','/api/web-project-recovery/_worker/report','HandleProjectRecoveryReport',False),('POST','/api/account/totp/setup','HandleDashboardTotpSetup',True),('POST','/api/account/totp/confirm','HandleDashboardTotpConfirm',True)]
    text='\n'.join('routerAdd('+json.dumps(method)+','+json.dumps(path)+',e=>require(`${__hooks}/mothership`).'+handler+'(e)'+(',$apis.requireAuth("users")'if auth else'')+');'for method,path,handler,auth in routes)
    text+='\nonBootstrap(e=>{e.next();require(`${__hooks}/mothership`).InitializeDashboardTotp()});routerUse(e=>require(`${__hooks}/mothership`).GuardDashboardTotp(e));onRecordAuthRequest(e=>require(`${__hooks}/mothership`).HandleDashboardTotpAuth(e),"users");\n'
    text+='routerAdd("POST","/fixture/encrypt",e=>e.json(200,{encrypted:require(`${__hooks}/mothership`).encryptWebValue(e.requestInfo().body.value)}));'
    (hooks/'fixture.pb.js').write_text(text)
    env={**os.environ,'WEB_APPS_ENABLED':'true','WEB_APPS_MASTER_KEY':'R'*32,'WEB_APPS_WORKER_SECRET':worker_secret,'APEX_DOMAIN':'example.invalid','DATA_ROOT':str(root/'data'),'WEB_APPS_ROOT':str(root/'web')}
    data=root/'pb_data';password='Q'+secrets.token_urlsafe(30);email='project@example.invalid'
    subprocess.run([args.pocketbase,'superuser','upsert',email,password,'--dir',str(data),'--hooksDir',str(root/'empty'),'--migrationsDir',str(source/'pb_migrations')],env=env,check=True,capture_output=True)
    log=(root/'process.log').open('wb');process=subprocess.Popen([args.pocketbase,'serve','--dir',str(data),'--hooksDir',str(hooks),'--migrationsDir',str(source/'pb_migrations'),'--http','127.0.0.1:'+str(port)],env=env,stdout=log,stderr=subprocess.STDOUT)
    for _ in range(100):
        if process.poll()is not None:raise RuntimeError('Fixture startup failed')
        try:req('/api/health');break
        except OSError:time.sleep(.1)
    su=req('/api/collections/_superusers/auth-with-password',body={'identity':email,'password':password})['token']
    actor=req('/api/collections/users/records',su,{'email':email,'username':'projectqa','password':password,'passwordConfirm':password,'verified':True,'superAdmin':True,'subscription':'premium','subscription_quantity':1})
    token=req('/api/collections/users/auth-with-password',body={'identity':email,'password':password})['token']
    req('/api/web-project-recovery',token,status=403)
    setup=req('/api/account/totp/setup',token,{'password':password});totp=base64.b32decode(setup['secret']+'='*(-len(setup['secret'])%8));hashed=hmac.new(totp,struct.pack('>Q',int(time.time())//30),hashlib.sha1).digest();offset=hashed[-1]&15;code=str((struct.unpack('>I',hashed[offset:offset+4])[0]&0x7fffffff)%1000000).zfill(6)
    token=req('/api/account/totp/confirm',token,{'password':password,'code':code})['token']
    req('/api/web-project-recovery',status=401);req('/api/collections/web_project_recovery_runs/records',token,status=403)
    req('/api/web-project-recovery/_worker/claim',body={},worker=True,status=403)
    project=req('/api/collections/web_projects/records',su,{'name':'Fixture project','repository':'','actorId':actor['id']})
    published={'env':{'PB_ADMIN_PASSWORD':{'value':'fixture-native-credential','runtime':True,'build':False}},'runtime':{'kind':'image'}}
    encrypted=req('/fixture/encrypt',body={'value':published})['encrypted']
    app=req('/api/collections/web_apps/records',su,{'uid':actor['id'],'name':'Fixture project app','status':'running','config':{}})
    deployment=req('/api/collections/web_deployments/records',su,{'appId':app['id'],'action':'deploy','status':'ready','snapshotEncrypted':encrypted,'result':{'imageId':'sha256:'+'a'*64}})
    req('/api/collections/web_apps/records/'+app['id'],su,{'activeDeployment':deployment['id']},'PATCH')
    req('/api/collections/web_app_environments/records',su,{'projectId':project['id'],'appId':app['id'],'environment':'prod','slug':'fixture','state':'active'})
    prefix='/api/web-project-recovery/projects/'+project['id']
    first=req(prefix+'/preview',token);assert 'fixture-native-credential'not in json.dumps(first)
    private('claim',{'cleanupOnly':True});queued=req(prefix+'/runs',token,{'operation':'drill','revision':first['revision']},status=202)['run']
    with sqlite3.connect(data/'data.db')as db:
        raw=db.execute('SELECT snapshotEncrypted FROM web_project_recovery_runs WHERE id=?',(queued['id'],)).fetchone()[0]
        assert 'fixture-native-credential'not in raw
    job=private('claim')['job'];assert job['id']==queued['id']and 'fixture-native-credential'in json.dumps(job['snapshot'])
    # A future same-day PB deadline must not expire on cleanup/heartbeat claims.
    assert private('claim',{'cleanupOnly':True})['job']is None
    history=req('/api/web-project-recovery',token)['runs'];assert history[0]['status']=='running'
    proof={'id':job['id'],'lease':job['lease']}
    private('report',{**proof,'status':'running','stage':'restore'})
    private('report',{**proof,'status':'ready','result':{}},400)
    private('report',{**proof,'lease':'X'*80,'status':'failed','result':{}},403)
    result={'scope':'isolated-http-health','completeArtifacts':True,'runtimeStarted':True,'noExternalNetwork':True,'mailsDisabled':True,'scheduledTasksDisabled':True,'workersInert':True,'cleanedUp':True,'apps':1,'databases':0,'images':1,'archiveBytes':100,'services':[{'appId':app['id'],'service':'web','status':'running-isolated'}]}
    private('report',{**proof,'status':'ready','result':result})
    private('report',{**proof,'status':'ready','result':result},403)
    state=req('/api/web-project-recovery',token);assert state['runs'][0]['status']=='ready'and not state['runs'][0]['result']['applicationVerified']
    assert 'fixture-native-credential'not in json.dumps(state)and job['lease']not in json.dumps(state)
    # Native transaction reads maintenance itself; a stale caller's false flag
    # cannot claim after the root publication gate is acquired.
    queued=req(prefix+'/runs',token,{'operation':'drill','revision':first['revision']},status=202)['run']
    admission=root/'admission.json';admission.write_text(json.dumps({'version':1,'owner':'qa-project-maintenance','startedAt':int(time.time()),'expiresAt':int(time.time())+300}));admission.chmod(0o600)
    assert private('claim',{'cleanupOnly':False})['job']is None
    assert req('/api/web-project-recovery',token)['runs'][0]['status']=='queued'
    admission.unlink();resumed=private('claim',{'cleanupOnly':False})['job'];assert resumed['id']==queued['id']
    private('report',{'id':resumed['id'],'lease':resumed['lease'],'status':'failed','result':{},'errorCode':'fixture_done'})
    req(prefix+'/runs',token,{'operation':'drill','revision':'0'*64},status=409)
    # Unmapped public native URL and external object storage block full coverage.
    published['env']['POCKETBASE_URL']={'value':'https://unknown.example.invalid','runtime':True,'build':False}
    encrypted=req('/fixture/encrypt',body={'value':published})['encrypted'];req('/api/collections/web_deployments/records/'+deployment['id'],su,{'snapshotEncrypted':encrypted},'PATCH')
    blocked=req(prefix+'/preview',token);assert blocked['blockers'][0]['name']=='POCKETBASE_URL'
    req(prefix+'/runs',token,{'operation':'drill','revision':blocked['revision']},status=400)
    del published['env']['POCKETBASE_URL'];encrypted=req('/fixture/encrypt',body={'value':published})['encrypted'];req('/api/collections/web_deployments/records/'+deployment['id'],su,{'snapshotEncrypted':encrypted},'PATCH')
    instance=req('/api/collections/instances/records',su,{'uid':actor['id'],'subdomain':'fixturedb','version':'0.39.*','power':False,'status':'idle','secrets':{'NATIVE_FIXTURE_SECRET':'native-secret-fixture'}})
    req('/api/collections/web_private_db_bindings/records',su,{'appId':app['id'],'instanceId':instance['id'],'actorId':actor['id'],'port':4825,'enabled':True,'revision':'R'*24})
    other=req('/api/collections/web_apps/records',su,{'uid':actor['id'],'name':'Other fixture app','status':'stopped','config':{}})
    req('/api/collections/web_private_db_bindings/records',su,{'appId':other['id'],'instanceId':instance['id'],'actorId':actor['id'],'port':4825,'enabled':True,'revision':'S'*24})
    published['env']['POCKETBASE_URL']={'value':'https://fixturedb.example.invalid','runtime':True,'build':False}
    encrypted=req('/fixture/encrypt',body={'value':published})['encrypted'];req('/api/collections/web_deployments/records/'+deployment['id'],su,{'snapshotEncrypted':encrypted},'PATCH')
    current=req(prefix+'/preview',token);assert not current['blockers']and current['databases'][0]['externalApplicationBindingsCount']==1
    assert 'native-secret-fixture'not in json.dumps(current)
    req(prefix+'/runs',token,{'operation':'drill','revision':current['revision']},status=202);job=private('claim')['job']
    assert job['snapshot']['databases'][0]['configuration']['secrets']['NATIVE_FIXTURE_SECRET']=='native-secret-fixture'
    req('/api/collections/web_project_recovery_runs/records/'+job['id'],su,{'deadline':'2000-01-01 00:00:00.000Z'},'PATCH')
    private('claim',{'cleanupOnly':True});assert req('/api/web-project-recovery',token)['runs'][0]['errorCode']=='lease_expired'
    # Reconcile a future lease after verified root cleanup, without waiting 2h.
    req(prefix+'/runs',token,{'operation':'drill','revision':current['revision']},status=202);orphan=private('claim')['job']
    private('claim',{'recoverOrphans':True},400)
    private('claim',{'cleanupOnly':True,'recoverOrphans':True})
    assert req('/api/web-project-recovery',token)['runs'][0]['errorCode']=='worker_lost'
    with sqlite3.connect(data/'data.db')as db:assert db.execute('SELECT leaseHash,snapshotEncrypted FROM web_project_recovery_runs WHERE id=?',(orphan['id'],)).fetchone()==('','')
    req(prefix+'/runs',token,{'operation':'drill','revision':current['revision']},status=202);next_job=private('claim')['job'];assert next_job and next_job['id']!=orphan['id']
    private('report',{'id':next_job['id'],'lease':next_job['lease'],'status':'failed','result':{},'errorCode':'fixture_done'})
    private('claim',{'cleanupOnly':True,'blocked':True});req(prefix+'/runs',token,{'operation':'drill','revision':current['revision']},status=503)
    req('/api/collections/users/records/'+actor['id'],su,{'superAdmin':False},'PATCH');req('/api/web-project-recovery',token,status=403)
    print(json.dumps({'passed':True,'checks':['mandatory_totp_superadmin_locked_history','dedicated_root_key_rejects_web_worker_secret','encrypted_snapshots_no_public_secret','same_day_lease_survives_cleanup_and_one_use_reports','honest_complete_inventory_no_business_verification_claim','unmapped_database_blocked_and_revisions','expired_lease_and_cleanup_block_new_jobs','root_restart_reconciles_future_orphan_lease_without_waiting_and_next_job_claims','native_admission_gate_blocks_stale_false_claim_and_resumes_after_release']}))
except Exception:
    if(root/'process.log').exists():print((root/'process.log').read_text()[-4000:])
    raise
finally:
    if process:
        process.terminate()
        try:process.wait(timeout=8)
        except subprocess.TimeoutExpired:process.kill();process.wait()
    shutil.rmtree(root,ignore_errors=True)
