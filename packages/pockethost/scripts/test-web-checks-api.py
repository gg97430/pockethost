#!/usr/bin/env python3
"""Disposable native PocketBase: permissions/TOTP, encrypted credentials, leases and truthful results."""
import argparse,base64,concurrent.futures,hashlib,hmac,json,os,pathlib,secrets,shutil,socket,sqlite3,struct,subprocess,tempfile,time,urllib.error,urllib.request,sys,importlib.util,select,socketserver,threading
from unittest.mock import patch
p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=pathlib.Path);p.add_argument('--pocketbase',required=True);args=p.parse_args()
os.umask(0o077);root=pathlib.Path(tempfile.mkdtemp(prefix='kolab-check-api-')).resolve();process=None;secret=secrets.token_urlsafe(40);checks=[]
with socket.socket()as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
base='http://127.0.0.1:'+str(port)
def req(path,token='',body=None,method=None,status=200,worker=False):
    request=urllib.request.Request(base+path,method=method or('POST'if body is not None else'GET'),headers={'Content-Type':'application/json',**({'Authorization':token}if token else{}),**({'X-Web-Worker':secret}if worker else{})},data=None if body is None else json.dumps(body).encode())
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
    routes=[('GET','/api/web-apps/{id}/checks','HandleWebChecksGet',True),('PUT','/api/web-apps/{id}/checks','HandleWebChecksPut',True),('POST','/api/web-apps/{id}/checks/run','HandleWebChecksRun',True),('POST','/api/web-apps/_checks/claim','HandleWebChecksClaim',False),('POST','/api/web-apps/_checks/report','HandleWebChecksReport',False),('POST','/api/account/totp/setup','HandleDashboardTotpSetup',True),('POST','/api/account/totp/confirm','HandleDashboardTotpConfirm',True),('POST','/api/web-apps/{id}/actions/{action}','HandleWebAppAction',True),('PATCH','/api/web-apps/{id}/runtime/profile','HandleWebRuntimeUpdate',True)]
    routes += [('POST','/api/web-apps/_worker/claim','HandleWebWorkerClaim',False),('POST','/api/web-apps/_worker/report','HandleWebWorkerReport',False)]
    routes += [('GET','/api/admin/monitoring','HandleOperatorMonitoring',True)]
    text='\n'.join('routerAdd('+json.dumps(method)+','+json.dumps(path)+',e=>require(`${__hooks}/mothership`).'+handler+'(e)'+(',$apis.requireAuth("users")'if auth else'')+');'for method,path,handler,auth in routes)
    text+='\nonBootstrap(e=>{e.next();require(`${__hooks}/mothership`).InitializeDashboardTotp()});routerUse(e=>require(`${__hooks}/mothership`).GuardDashboardTotp(e));onRecordAuthRequest(e=>require(`${__hooks}/mothership`).HandleDashboardTotpAuth(e),"users");\n'
    (hooks/'checks.pb.js').write_text(text)
    env={**os.environ,'WEB_APPS_ENABLED':'true','WEB_APPS_MASTER_KEY':'c'*32,'WEB_APPS_WORKER_SECRET':secret,'WEB_APPS_ROOT':str(root/'web'),'DATA_ROOT':str(root/'data'),'APEX_DOMAIN':'example.invalid'}
    data=root/'pb_data';password='P'+secrets.token_urlsafe(32);email='checks@example.invalid'
    subprocess.run([args.pocketbase,'superuser','upsert',email,password,'--dir',str(data),'--hooksDir',str(root/'empty'),'--migrationsDir',str(source/'pb_migrations')],env=env,check=True,capture_output=True)
    log=(root/'process.log').open('wb');process=subprocess.Popen([args.pocketbase,'serve','--dir',str(data),'--hooksDir',str(hooks),'--migrationsDir',str(source/'pb_migrations'),'--http','127.0.0.1:'+str(port)],env=env,stdout=log,stderr=subprocess.STDOUT)
    for _ in range(100):
        if process.poll()is not None:raise RuntimeError('Fixture startup failed')
        try:req('/api/health');break
        except OSError:time.sleep(.1)
    su=req('/api/collections/_superusers/auth-with-password',body={'identity':email,'password':password})['token']
    actor=req('/api/collections/users/records',su,{'email':email,'username':'checkadmin','password':password,'passwordConfirm':password,'verified':True,'superAdmin':True,'subscription':'premium','subscription_quantity':1})
    token=req('/api/collections/users/auth-with-password',body={'identity':email,'password':password})['token']
    setup=req('/api/account/totp/setup',token,{'password':password});key=base64.b32decode(setup['secret']+'='*(-len(setup['secret'])%8));digest=hmac.new(key,struct.pack('>Q',int(time.time())//30),hashlib.sha1).digest();offset=digest[-1]&15;code=str((struct.unpack('>I',digest[offset:offset+4])[0]&0x7fffffff)%1000000).zfill(6)
    token=req('/api/account/totp/confirm',token,{'password':password,'code':code})['token']
    app=req('/api/collections/web_apps/records',su,{'uid':actor['id'],'name':'Check fixture','status':'running','activeDeployment':'d'*15,'config':{'source':'github','repository':'','branch':'main','rootDirectory':'.','healthPath':'/','generatedDomain':'fixture.example.invalid'}})
    ident=app['id'];path='/api/web-apps/'+ident+'/checks'
    req(path,status=401);req('/api/web-apps/_checks/claim',body={},status=403)
    for collection in ['web_app_checks','web_app_check_runs']:req('/api/collections/'+collection+'/records',token,status=403)
    initial=req(path,token);assert not initial['settings']['config']['preflight']['enabled']and not initial['history']
    config=initial['settings']['config'];jid='j'*15
    journey={'id':jid,'name':'Compte de lecture','enabled':True,'intervalMinutes':5,'domain':'generated','auth':{'kind':'json','path':'/api/auth/login','identityField':'email','passwordField':'password','identity':'dedicated@example.invalid','tokenField':'token'},'steps':[{'name':'Tableau de bord','path':'/api/me','status':200,'jsonField':'active','expected':'true'}]}
    config['journeys']=[journey]
    draft={'config':config,'revision':'','secrets':{jid:{'password':'fixture-dedicated-password'}}}
    for mutate in [lambda v:v['config']['journeys'][0].update(domain='http://169.254.169.254/'),lambda v:v['config']['journeys'][0]['steps'][0].update(method='POST'),lambda v:v['config']['journeys'][0]['auth'].update(path='/api/invoices/send')]:
        bad=json.loads(json.dumps(draft));mutate(bad);req(path,token,bad,'PUT',400)
    saved=req(path,token,draft,'PUT');revision=saved['revision'];req(path,token,draft,'PUT',409)
    current=req(path,token);assert current['settings']['hasSecret'][jid]and 'fixture-dedicated-password'not in json.dumps(current)
    with sqlite3.connect(data/'data.db')as db:
        row=db.execute('SELECT secretsEncrypted,config FROM web_app_checks WHERE appId=?',(ident,)).fetchone()
        assert 'fixture-dedicated-password'not in ''.join(row)
    checks.append('totp_permissions_locked_collections_scoped_declarative_validation_revision_and_encryption')
    queued=req(path+'/run',token,{'journeyId':jid},status=202)['runs'];assert len(queued)==1
    assert not req(path+'/run',token,{'journeyId':jid},status=202)['runs']
    # Copying a journey configuration to another app must not suppress its runs.
    app2=req('/api/collections/web_apps/records',su,{'uid':actor['id'],'name':'Independent check fixture','status':'running','activeDeployment':'e'*15,'config':app['config']})
    path2='/api/web-apps/'+app2['id']+'/checks'
    saved2=req(path2,token,draft,'PUT')
    assert len(req(path2+'/run',token,{'journeyId':jid},status=202)['runs'])==1
    config2=json.loads(json.dumps(config));config2['journeys']=[]
    req(path2,token,{'config':config2,'revision':saved2['revision'],'secrets':{}},'PUT')
    assert private('claim',{'cleanupOnly':True})['job']is None
    with concurrent.futures.ThreadPoolExecutor(max_workers=2)as pool:claimed=list(pool.map(lambda _:private('claim')['job'],range(2)))
    assert sum(bool(row)for row in claimed)==1;job=next(row for row in claimed if row)
    assert job['input']['password']=='fixture-dedicated-password'
    proof={'id':job['id'],'proof':job['proof']}
    private('report',{**proof,'proof':'X'*80,'result':{'status':'passed','errorCode':'','steps':[]}},401)
    private('report',{**proof,'result':{'status':'passed','errorCode':'','steps':[]}},400)
    result={'status':'passed','errorCode':'','steps':[{'name':'Connexion du compte de test','status':'passed','httpStatus':200,'code':'','durationMs':10},{'name':'Tableau de bord','status':'passed','httpStatus':200,'code':'','durationMs':20}],'durationMs':30}
    private('report',{**proof,'result':result});private('report',{**proof,'result':result},401)
    current=req(path,token);assert current['summary'][jid]['status']=='passed'
    assert all(value not in json.dumps(current)for value in [job['proof'],'fixture-dedicated-password'])
    with sqlite3.connect(data/'data.db')as db:assert db.execute('SELECT leaseHash,snapshotEncrypted FROM web_app_check_runs WHERE id=?',(job['id'],)).fetchone()==('','')
    checks.append('manual_dedup_single_global_lease_truthful_complete_results_one_use_proof_and_secret_scrubbing')
    # A changed configuration cancels a queued run; prior results cannot appear current.
    req(path+'/run',token,{'journeyId':jid},status=202)
    config['journeys'][0]['name']='Compte de lecture renommé'
    saved=req(path,token,{'config':config,'revision':revision,'secrets':{}},'PUT');revision=saved['revision']
    assert private('claim')['job']is None and not req(path,token)['summary']
    current=req(path,token);assert current['history'][0]['status']=='cancelled'and current['settings']['hasSecret'][jid]
    # Expired work can be reconciled during admission maintenance without claiming the next job.
    req(path+'/run',token,{'journeyId':jid},status=202);job=private('claim')['job'];assert job
    req('/api/collections/web_app_check_runs/records/'+job['id'],su,{'deadline':'2000-01-01 00:00:00.000Z'},'PATCH')
    private('report',{'id':job['id'],'proof':job['proof'],'result':result},401)
    assert private('claim',{'cleanupOnly':True})['job']is None
    assert req(path,token)['history'][0]['status']=='failed'
    checks.append('configuration_changes_cancel_stale_runs_and_maintenance_reconciles_expired_work_without_claims')
    # The preflight contract travels in the immutable deployment snapshot, without journey passwords.
    config['preflight'].update(enabled=True,requiredEnvironment=[{'name':'REQUIRED_TOKEN','scope':'runtime'}])
    saved=req(path,token,{'config':config,'revision':revision,'secrets':{}},'PUT')
    req('/api/web-apps/'+ident+'/runtime/profile',token,{'profile':{'kind':'image','image':'ghcr.io/fixture/image@sha256:'+'a'*64},'revision':''},'PATCH')
    req('/api/web-apps/'+ident+'/actions/deploy',token,{},status=202)
    with sqlite3.connect(data/'data.db')as db:
        assert db.execute('SELECT count(*) FROM web_deployments WHERE appId=?',(ident,)).fetchone()[0]==1
    deployment=req('/api/web-apps/_worker/claim',body={},worker=True)['job']
    assert deployment['snapshot']['checks']['preflight']['requiredEnvironment']==[{'name':'REQUIRED_TOKEN','scope':'runtime'}]
    assert 'fixture-dedicated-password'not in json.dumps(deployment)
    # Execute the actual worker until the required-variable rejection. No build,
    # Docker call, candidate container, published route or customer process is touched.
    scripts=args.source/'packages/pockethost/scripts';sys.path.insert(0,str(scripts))
    # Real HTTP health/password authentication over the exact Unix handshake,
    # using only this disposable PocketBase and no production account or broker.
    import web_app_preflight as preflight
    binding={'id':'b'*15,'token':'T'*64}
    class Relay(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(4);line=bytearray()
            while not line.endswith(b'\n')and len(line)<1024:line.extend(self.request.recv(1))
            assert json.loads(line)=={'version':1,'bindingId':binding['id'],'token':binding['token']}
            self.request.sendall(b'OK\n')
            with socket.create_connection(('127.0.0.1',port),3)as upstream:
                while True:
                    for source in select.select([self.request,upstream],[],[],4)[0]:
                        raw=source.recv(65536)
                        if not raw:return
                        (upstream if source is self.request else self.request).sendall(raw)
    with socketserver.ThreadingUnixStreamServer(str(root/(ident+'.sock')),Relay)as relay:
        thread=threading.Thread(target=relay.serve_forever,daemon=True);thread.start()
        try:
            with patch.object(preflight,'SOCKET_ROOT',root):
                assert preflight.private_request(ident,binding,'GET','/api/health')==200
                assert preflight.private_request(ident,binding,'POST','/api/collections/_superusers/auth-with-password',{'identity':email,'password':password})==200
                assert preflight.private_request(ident,binding,'POST','/api/collections/_superusers/auth-with-password',{'identity':email,'password':'invalid-fixture-password'})==400
        finally:relay.shutdown();thread.join(timeout=5)
    checks.append('real_private_unix_health_and_password_authentication_against_disposable_database')
    spec=importlib.util.spec_from_file_location('checks_worker',scripts/'web-app-worker.py');worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    def worker_api(action,body):return req('/api/web-apps/_worker/'+action,body=body,worker=True)
    (root/'worker').mkdir()
    with patch.object(worker,'ROOT',root/'worker'),patch.object(worker,'api',side_effect=worker_api),patch.object(worker,'docker',side_effect=AssertionError('Preflight must run before Docker')):
        job=worker.Job(deployment)
        try:job.execute();raise AssertionError('Missing required variable accepted')
        except RuntimeError as error:assert 'contrôles avant déploiement'in str(error).lower()
        assert 'REQUIRED_TOKEN'in job.logpath.read_text()
    checks.append('immutable_preflight_snapshot_excludes_journey_secrets_and_real_worker_refuses_before_any_docker_operation')
    # The functional child is a root-owned trusted file in the opt-in Linux QA
    # source tree. It can fail DNS safely without touching any business endpoint.
    from web_app_checks import Checks
    child=Checks(base,secret,root)
    input_={'host':'fixture.example.invalid','password':'','journey':{**journey,'auth':{'kind':'none','path':'','identityField':'','passwordField':'','identity':'','tokenField':''}}}
    result=child.execute({'id':'q'*15,'proof':'P'*80,'input':input_})
    assert result['status']=='failed'and result['errorCode']=='origin_unavailable'
    checks.append('trusted_child_executes_with_isolated_environment_and_public_dns_refusal')
    # Operator diagnostics consume sanitized current observations and recent PB
    # dates, and never turn a failed build into a claim that the site is down.
    folder=root/'data/monitoring';folder.mkdir(parents=True)
    now=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
    report={'version':1,'checkedAt':now,'host':'fixture','domain':'example.invalid','checks':[{'id':'database:'+'b'*15,'group':'databases','name':'Fixture DB','state':'error','detail':'HTTP 503'}]}
    (folder/'status.json').write_text(json.dumps(report))
    req('/api/collections/web_deployments/records',su,{'appId':ident,'action':'deploy','status':'failed','finishedAt':now,'error':'La commande de construction a échoué. private-fixture-secret'})
    observed=req('/api/admin/monitoring',token)
    assert observed['available']and observed['diagnostics']['eventInventoryAvailable']
    assert any(row['category']=='build'for row in observed['diagnostics']['events'])
    assert 'private-fixture-secret'not in json.dumps(observed)
    checks.append('native_operator_diagnostics_current_dates_and_secret_free_categories')
    # A project-owned operational setting may outlive its author, but an optional
    # credential-bearing journey must be suspended when that author loses access.
    project=req('/api/collections/web_projects/records',su,{'name':'Access revocation fixture','repository':'fixture/read-only','actorId':actor['id']})
    req('/api/collections/web_app_environments/records',su,{'projectId':project['id'],'appId':app2['id'],'environment':'production','slug':'production','state':'active'})
    current2=req(path2,token)
    req(path2,token,{'config':config,'revision':current2['settings']['revision'],'secrets':{jid:{'password':'fixture-dedicated-password'}}},'PUT')
    queued2=req(path2+'/run',token,{'journeyId':jid},status=202)['runs'][0]
    req('/api/collections/users/records/'+actor['id'],su,{'superAdmin':False},'PATCH')
    assert private('claim')['job']is None
    with sqlite3.connect(data/'data.db')as db:assert db.execute('SELECT status FROM web_app_check_runs WHERE id=?',(queued2['id'],)).fetchone()[0]=='cancelled'
    checks.append('project_scoped_journeys_cancel_when_the_configuration_author_loses_access')
    req(path,token,status=404)
    print(json.dumps({'passed':True,'checks':checks}))
except Exception:
    if(root/'process.log').exists():print((root/'process.log').read_text()[-4000:])
    raise
finally:
    if process:
        process.terminate()
        try:process.wait(timeout=8)
        except subprocess.TimeoutExpired:process.kill();process.wait()
    shutil.rmtree(root,ignore_errors=True)
