#!/usr/bin/env python3
"""Disposable PocketBase and real worker commands: supersession and prompt queue handoff."""
import argparse,base64,hashlib,hmac,importlib.util,json,os,pathlib,secrets,shutil,socket,struct,subprocess,sys,tempfile,threading,time,urllib.request,urllib.error
from unittest.mock import patch
p=argparse.ArgumentParser();p.add_argument('--source',type=pathlib.Path,required=True);p.add_argument('--pocketbase',required=True);args=p.parse_args()
os.umask(0o077);temporary=tempfile.TemporaryDirectory(prefix='kolab-queue-');root=pathlib.Path(temporary.name).resolve();process=None;checks=[]
with socket.socket()as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
origin='http://127.0.0.1:'+str(port);secret=secrets.token_urlsafe(40)
def request(path,token='',body=None,method=None,status=200,private=False):
 headers={'Content-Type':'application/json',**({'Authorization':token}if token else{}),**({'X-Web-Worker':secret}if private else{})}
 req=urllib.request.Request(origin+path,headers=headers,data=None if body is None else json.dumps(body).encode(),method=method or('GET'if body is None else'POST'))
 try:
  with urllib.request.urlopen(req,timeout=15)as r:assert r.status==status;return json.load(r)
 except urllib.error.HTTPError as e:
  if e.code==status:return None
  raise AssertionError(path+': '+str(e.code)+' '+e.read().decode())from None
def worker(path,body):return request('/api/web-apps/_worker/'+path,body=body,private=True)
try:
 source=args.source/'packages/pockethost/src/mothership-app';hooks=root/'hooks';hooks.mkdir();shutil.copyfile(source/'pb_hooks/mothership.js',hooks/'mothership.js')
 routes=[('POST','/api/web-apps/{id}/actions/{action}','HandleWebAppAction',True),('PATCH','/api/web-apps/{id}/runtime/profile','HandleWebRuntimeUpdate',True),('GET','/api/web-apps/{id}/deployments','HandleWebDeployments',True),('POST','/api/web-apps/_worker/claim','HandleWebWorkerClaim',False),('POST','/api/web-apps/_worker/report','HandleWebWorkerReport',False),('POST','/api/account/totp/setup','HandleDashboardTotpSetup',True),('POST','/api/account/totp/confirm','HandleDashboardTotpConfirm',True)]
 text='\n'.join('routerAdd('+json.dumps(method)+','+json.dumps(path)+',e=>require(`${__hooks}/mothership`).'+handler+'(e)'+(',$apis.requireAuth("users")'if auth else'')+');'for method,path,handler,auth in routes)
 text+='\nonBootstrap(e=>{e.next();require(`${__hooks}/mothership`).InitializeDashboardTotp()});routerUse(e=>require(`${__hooks}/mothership`).GuardDashboardTotp(e));onRecordAuthRequest(e=>require(`${__hooks}/mothership`).HandleDashboardTotpAuth(e),"users");\n'
 text+='routerAdd("POST","/test/expire",e=>{if(e.auth?.collection().name!=="_superusers")throw new ForbiddenError();const m=require(`${__hooks}/mothership`),r=$app.findRecordById("web_deployments",e.requestInfo().body.id),s=m.decryptWebValue(r.getString("snapshotEncrypted"));s.ci={expiresAt:"2000-01-01T00:00:00.000Z"};r.set("action","ci-deploy");r.set("snapshotEncrypted",m.encryptWebValue(s));$app.save(r);return e.json(200,{ok:true})},$apis.requireAuth("_superusers"));\n';(hooks/'queue.pb.js').write_text(text)
 env={**os.environ,'WEB_APPS_ENABLED':'true','WEB_APPS_MASTER_KEY':'q'*32,'WEB_APPS_WORKER_SECRET':secret,'WEB_APPS_ROOT':str(root/'web'),'DATA_ROOT':str(root/'data'),'APEX_DOMAIN':'example.invalid'};data=root/'pb_data';password='P'+secrets.token_urlsafe(32);email='queue@example.invalid'
 subprocess.run([args.pocketbase,'superuser','upsert',email,password,'--dir',str(data),'--hooksDir',str(root/'empty'),'--migrationsDir',str(source/'pb_migrations')],env=env,check=True,capture_output=True)
 log=(root/'process.log').open('wb');process=subprocess.Popen([args.pocketbase,'serve','--dir',str(data),'--hooksDir',str(hooks),'--migrationsDir',str(source/'pb_migrations'),'--http','127.0.0.1:'+str(port)],env=env,stdout=log,stderr=subprocess.STDOUT)
 for _ in range(100):
  if process.poll()is not None:raise RuntimeError('Fixture startup failed')
  try:request('/api/health');break
  except OSError:time.sleep(.1)
 su=request('/api/collections/_superusers/auth-with-password',body={'identity':email,'password':password})['token']
 actor=request('/api/collections/users/records',su,{'email':email,'username':'queueadmin','password':password,'passwordConfirm':password,'verified':True,'superAdmin':True,'subscription':'premium','subscription_quantity':1})
 token=request('/api/collections/users/auth-with-password',body={'identity':email,'password':password})['token'];setup=request('/api/account/totp/setup',token,{'password':password});key=base64.b32decode(setup['secret']+'='*(-len(setup['secret'])%8));digest=hmac.new(key,struct.pack('>Q',int(time.time())//30),hashlib.sha1).digest();offset=digest[-1]&15;code=str((struct.unpack('>I',digest[offset:offset+4])[0]&0x7fffffff)%1000000).zfill(6);token=request('/api/account/totp/confirm',token,{'password':password,'code':code})['token']
 def app(name):
  row=request('/api/collections/web_apps/records',su,{'uid':actor['id'],'name':name,'status':'stopped','desiredCommit':'a'*40,'config':{'repository':'','branch':'main','rootDirectory':'.','healthPath':'/','generatedDomain':name.lower()+'.example.invalid'}})
  request('/api/web-apps/'+row['id']+'/runtime/profile',token,{'profile':{'kind':'image','image':'ghcr.io/fixture/image@sha256:'+'a'*64},'revision':''},'PATCH');return row['id']
 a,b=app('First'),app('Other')
 def queue(owner,commit='a'*40):
  request('/api/collections/web_apps/records/'+owner,su,{'desiredCommit':commit},'PATCH');return request('/api/web-apps/'+owner+'/actions/deploy',token,{},status=202)['deployment']['id']
 def record(ident):return request('/api/collections/web_deployments/records/'+ident,su)
 # A different application's queued job must not cancel this one.
 first=queue(a);running=worker('claim',{})['job'];assert running['id']==first
 other=queue(b);heartbeat=worker('report',{'id':first,'stageKey':'build','stage':'Construction'});assert not heartbeat['cancelRequested']and not heartbeat['superseded']
 replacement=queue(a);heartbeat=worker('report',{'id':first,'stageKey':'publish','stage':'Publication'})
 assert heartbeat['cancelRequested']and heartbeat['superseded']and not heartbeat['publicationStartedAt']
 assert worker('claim',{})['job']is None,'No second worker before candidate cleanup'
 worker('report',{'id':first,'status':'cancelled','stage':'Annulé'});assert record(first)['status']=='cancelled'
 assert worker('claim',{})['job']['id']==other;worker('report',{'id':other,'status':'failed','error':'Controlled failure'})
 assert worker('claim',{})['job']['id']==replacement,'No expired-lease wait after failure';worker('report',{'id':replacement,'status':'ready','result':{}})
 checks.append('same_commit_replacement_other_app_isolation_cleanup_lease_and_immediate_failed_handoff')
 # A started publication finishes; a newer push cannot cancel its critical section.
 current=queue(a);worker('claim',{});heartbeat=worker('report',{'id':current,'stageKey':'publish','stage':'Publication'});assert heartbeat['publicationStartedAt']
 newer=queue(a,'b'*40);heartbeat=worker('report',{'id':current,'stageKey':'publish','stage':'Publication vérifiée'});assert not heartbeat['cancelRequested']and not heartbeat['superseded']
 worker('report',{'id':current,'status':'ready','result':{}});assert worker('claim',{})['job']['id']==newer;worker('report',{'id':newer,'status':'failed','error':'Fixture complete'})
 checks.append('publication_gate_preserves_committed_switch_and_releases_the_next_job')
 expired=queue(a);request('/test/expire',su,{'id':expired});after_expired=queue(b)
 response=worker('claim',{});assert response['job']is None and response['retryClaim']and record(expired)['status']=='failed'
 assert worker('claim',{})['job']['id']==after_expired;worker('report',{'id':after_expired,'status':'failed','error':'Fixture complete'})
 checks.append('unclaimable_expired_job_requests_immediate_next_claim_without_idle_sleep')
 # Real sleeping build process + real native worker heartbeat; no Docker or tenant source involved.
 active=queue(a,'c'*40);job=worker('claim',{})['job'];spec=importlib.util.spec_from_file_location('queue_worker',args.source/'packages/pockethost/scripts/web-app-worker.py');w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w);(root/'web').mkdir(exist_ok=True)
 errors=[];started=threading.Event();j=None
 with patch.object(w,'ROOT',root/'web'),patch.object(w,'api',side_effect=worker),patch.object(w,'publish_runtime_stats'),patch.object(w,'resources'),patch.object(w,'check_runtime_budgets'):
  j=w.Job(job)
  def build():
   started.set()
   try:j.run([sys.executable,'-c','import time;print("build started",flush=True);time.sleep(60)'])
   except Exception as error:errors.append(error)
  thread=threading.Thread(target=build);thread.start();assert started.wait(2);next_id=queue(a,'d'*40);began=time.monotonic();thread.join(timeout=9)
  assert not thread.is_alive()and len(errors)==1 and isinstance(errors[0],w.JobCancelled),errors
  assert time.monotonic()-began<8 and j.process is None
  worker('report',{'id':active,'status':'cancelled'});assert worker('claim',{})['job']['id']==next_id;worker('report',{'id':next_id,'status':'failed','error':'Fixture complete'})
 checks.append('actual_running_command_cancelled_within_one_heartbeat_then_next_claim_immediate')
 print(json.dumps({'ok':True,'checks':checks}))
finally:
 if process is not None:
  process.terminate()
  try:process.wait(timeout=5)
  except subprocess.TimeoutExpired:process.kill();process.wait()
 if 'log'in locals():log.close()
 temporary.cleanup()
