#!/usr/bin/env python3
"""Disposable native PocketBase: cron CRUD, bounded worker leases, deduplication and redacted history."""
import argparse,base64,hashlib,hmac,struct,concurrent.futures,datetime,json,os,pathlib,secrets,shutil,socket,sqlite3,subprocess,tempfile,time,urllib.error,urllib.request
p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=pathlib.Path);p.add_argument('--pocketbase',required=True);args=p.parse_args();os.umask(0o077);root=pathlib.Path(tempfile.mkdtemp(prefix='ph-web-cron-api-')).resolve();process=None;checks=[];secret=secrets.token_urlsafe(40)
with socket.socket()as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
base=f'http://127.0.0.1:{port}'
def req(path,t='',body=None,method=None,status=200,worker=False):
 request=urllib.request.Request(base+path,method=method or('POST'if body is not None else'GET'),headers={'Content-Type':'application/json',**({'Authorization':t}if t else{}),**({'X-Web-Worker':secret}if worker else{})},data=None if body is None else json.dumps(body).encode())
 try:
  with urllib.request.urlopen(request,timeout=25)as response:raw=response.read();assert response.status==status,(path,response.status,status);return json.loads(raw)if raw else None
 except urllib.error.HTTPError as error:
  raw=error.read()
  if error.code==status:return json.loads(raw)if raw else None
  raise AssertionError(f'{path}: {error.code} '+raw.decode())
def private(path,body=None,status=200):return req('/api/web-apps/_tasks/'+path,body={}if body is None else body,status=status,worker=True)
try:
 source=args.source/'packages/pockethost/src/mothership-app';hooks=root/'hooks';hooks.mkdir();shutil.copy(source/'pb_hooks/mothership.js',hooks/'mothership.js')
 routes=[('GET','/api/web-apps/{id}/tasks','HandleWebTasksList',True),('POST','/api/web-apps/{id}/tasks','HandleWebTasksCreate',True),('PATCH','/api/web-apps/{id}/tasks/{taskId}','HandleWebTaskUpdate',True),('DELETE','/api/web-apps/{id}/tasks/{taskId}','HandleWebTaskDelete',True),('POST','/api/web-apps/{id}/tasks/{taskId}/run','HandleWebTaskRun',True),('GET','/api/web-apps/{id}/task-runs','HandleWebTaskRuns',True),('POST','/api/web-apps/_tasks/claim','HandleWebTaskClaim',False),('POST','/api/web-apps/_tasks/validate','HandleWebTaskValidate',False),('POST','/api/web-apps/_tasks/report','HandleWebTaskReport',False)]
 routes += [('POST','/api/account/totp/setup','HandleDashboardTotpSetup',True),('POST','/api/account/totp/confirm','HandleDashboardTotpConfirm',True)]
 text='\n'.join('routerAdd('+json.dumps(method)+','+json.dumps(path)+',e=>require(`${__hooks}/mothership`).'+handler+'(e)'+(',$apis.requireAuth("users")'if auth else'')+');'for method,path,handler,auth in routes)
 text+='\nonBootstrap(e=>{e.next();require(`${__hooks}/mothership`).InitializeDashboardTotp()});routerUse(e=>require(`${__hooks}/mothership`).GuardDashboardTotp(e));onRecordAuthRequest(e=>require(`${__hooks}/mothership`).HandleDashboardTotpAuth(e),"users");\n'
 text+='''
routerAdd('POST','/test/fixture',e=>{if(!e.auth?.getBool('superAdmin'))throw new ForbiddenError();const mod=require(`${__hooks}/mothership`),app=new Record($app.findCollectionByNameOrId('web_apps')),config={repository:'owner/repo',branch:'main',rootDirectory:'.',healthPath:'/',generatedDomain:'web-fixture.example.invalid'},env={TOKEN:{value:'private-stdout-token',build:false,runtime:true}};app.set('uid',e.auth.id);app.set('name','Cron fixture');app.set('status','running');app.set('config',config);app.set('envEncrypted',mod.encryptWebValue(env));$app.save(app);const job=new Record($app.findCollectionByNameOrId('web_deployments'));job.set('appId',app.id);job.set('action','deploy');job.set('status','ready');job.set('snapshotEncrypted',mod.encryptWebValue({config,env}));$app.save(job);job.set('result',{container:'ph-web-'+app.id+'-'+job.id,image:'ph-web/'+app.id+':'+job.id});$app.save(job);app.set('activeDeployment',job.id);$app.save(app);return e.json(200,{id:app.id,deployment:job.id})},$apis.requireAuth('users'));
'''
 (hooks/'scheduled-tasks.pb.js').write_text(text)
 env={**os.environ,'WEB_APPS_ENABLED':'true','WEB_APPS_MASTER_KEY':'e'*32,'WEB_APPS_WORKER_SECRET':secret,'WEB_APPS_ROOT':str(root/'web'),'DATA_ROOT':str(root/'data'),'APEX_DOMAIN':'example.invalid'};password=secrets.token_urlsafe(32);email='cron@example.invalid';data=root/'pb_data'
 subprocess.run([args.pocketbase,'superuser','upsert',email,password,'--dir',str(data),'--hooksDir',str(root/'empty'),'--migrationsDir',str(source/'pb_migrations')],env=env,check=True,capture_output=True)
 log=(root/'process.log').open('wb');process=subprocess.Popen([args.pocketbase,'serve','--dir',str(data),'--hooksDir',str(hooks),'--migrationsDir',str(source/'pb_migrations'),'--http',f'127.0.0.1:{port}'],env=env,stdout=log,stderr=subprocess.STDOUT)
 for _ in range(100):
  if process.poll()is not None:raise RuntimeError((root/'process.log').read_text()[-2000:])
  try:req('/api/health');break
  except OSError:time.sleep(.1)
 su=req('/api/collections/_superusers/auth-with-password',body={'identity':email,'password':password})['token']
 def user(name,admin):
  row=req('/api/collections/users/records',su,{'email':name+'@example.invalid','username':name,'password':password,'passwordConfirm':password,'verified':True,'superAdmin':admin,'subscription':'premium','subscription_quantity':1})
  token=req('/api/collections/users/auth-with-password',body={'identity':row['email'],'password':password})['token']
  setup=req('/api/account/totp/setup',token,{'password':password});key=base64.b32decode(setup['secret']+'='*(-len(setup['secret'])%8));digest=hmac.new(key,struct.pack('>Q',int(time.time())//30),hashlib.sha1).digest();offset=digest[-1]&15;code=str((struct.unpack('>I',digest[offset:offset+4])[0]&0x7fffffff)%1000000).zfill(6)
  token=req('/api/account/totp/confirm',token,{'password':password,'code':code})['token']
  return row,token
 actor,token=user('cronadmin',True);_,plain=user('cronplain',False);app=req('/test/fixture',token,{});ident=app['id'];path='/api/web-apps/'+ident+'/tasks';history='/api/web-apps/'+ident+'/task-runs'
 for method,route in [('GET',path),('POST',path),('GET',history),('PATCH',path+'/'+'a'*15),('DELETE',path+'/'+'a'*15),('POST',path+'/'+'a'*15+'/run')]:req(route,body={}if method in ['POST','PATCH']else None,method=method,status=401);req(route,plain,{}if method in ['POST','PATCH']else None,method,status=404)
 for collection in ['web_scheduled_tasks','web_task_runs']:req('/api/collections/'+collection+'/records',token,status=403)
 req('/api/web-apps/_tasks/claim',body={},status=401);checks.append('admin_only_crud_history_locked_collections_and_private_worker_secret')
 draft={'name':'Maintenance','command':'printf "$TOKEN"; echo private-command-marker','schedule':'*/15 * * * *','enabled':False,'timeoutSeconds':5,'timezone':'UTC'}
 for invalid in [{'schedule':'* * * * * *'},{'schedule':'@daily'},{'schedule':'0 0 31 2 *'},{'schedule':'0 0 * * MON'},{'timeoutSeconds':0},{'timeoutSeconds':901},{'timeoutSeconds':1.5},{'timezone':'Europe/Paris'},{'command':'bad\0command'},{'container':'mothership'}]:req(path,token,{**draft,**invalid},status=400)
 task=req(path,token,draft,status=201)['task'];taskId=task['id'];taskPath=path+'/'+taskId;assert task['timezone']=='UTC'and task['command']==draft['command']and not task['enabled'];assert req(path,token)['worker']['ready']is False
 with sqlite3.connect((data/'data.db').as_uri()+'?mode=ro',uri=True)as db:encrypted=db.execute('SELECT commandEncrypted FROM web_scheduled_tasks WHERE id=?',(taskId,)).fetchone()[0];assert'private-command-marker'not in encrypted
 req(taskPath,token,{'name':'Night maintenance','enabled':True,'schedule':'0 2 * * *'},'PATCH');assert req(path,token)['items'][0]['enabled'];req(taskPath,token,{'enabled':False},'PATCH');checks.append('strict_utc_cron_duration_command_validation_and_encrypted_crud')
 # Explicit timezone changes recalculate due dates; ordinary edits preserve legacy UTC.
 assert req(path,token)['items'][0]['timezone']=='UTC'
 tz=req(path,token,{**draft,'name':'Reunion','timezone':'Indian/Reunion','schedule':'0 2 * * *'},status=201)['task']
 assert tz['timezone']=='Indian/Reunion'and datetime.datetime.fromisoformat(tz['nextRunAt'].replace('Z','+00:00')).hour==22
 same=req(path+'/'+tz['id'],token,{'name':'Reunion renamed'},'PATCH')['task'];assert same['nextRunAt']==tz['nextRunAt']and same['timezone']=='Indian/Reunion'
 req(path+'/'+tz['id'],token,method='DELETE');checks.append('reunion_timezone_and_legacy_utc_edits_preserved')
 req('/api/collections/web_apps/records/'+ident,su,{'status':'stopped'},'PATCH');req(taskPath+'/run',token,{},status=400);req('/api/collections/web_apps/records/'+ident,su,{'status':'running'},'PATCH')
 queued=req(taskPath+'/run',token,{},status=202)['run'];req(taskPath+'/run',token,{},status=400);assert queued['trigger']=='manual'and queued['status']=='queued'and'private-command-marker'not in json.dumps(queued)
 first=private('claim')['job'];assert first['id']==queued['id']and first['containerName']=='ph-web-'+ident+'-'+app['deployment'];assert first['command']==draft['command']and first['timeoutSeconds']==5and first['secrets']==['private-stdout-token'];assert private('claim')['job']is None
 lease={'id':first['id'],'proof':first['proof']};assert private('validate',lease)['valid'];private('validate',{**lease,'proof':'X'*80},401)
 req('/api/collections/web_apps/records/'+ident,su,{'status':'stopped'},'PATCH');assert not private('validate',lease)['valid'];req('/api/collections/web_apps/records/'+ident,su,{'status':'running'},'PATCH')
 report={**lease,'status':'succeeded','exitCode':0,'log':'x'*70000+'private-stdout-token','truncated':False};completed=private('report',report)['run'];assert completed['status']=='succeeded'and completed['exitCode']==0and completed['finishedAt']and completed['truncated'];assert'private-stdout-token'not in completed['log']and'[masqué]'in completed['log'];assert len(completed['log'])<=65536;private('report',report,401)
 rows=req(history,token)['items'];assert rows[0]['log']=='';assert'[masqué]'in req(history+'?logRunId='+first['id'],token)['items'][0]['log'];assert all(value not in json.dumps(req(path,token))for value in [first['proof'],'private-stdout-token'])
 with sqlite3.connect((data/'data.db').as_uri()+'?mode=ro',uri=True)as db:row=db.execute('SELECT leaseHash,snapshotEncrypted FROM web_task_runs WHERE id=?',(first['id'],)).fetchone();assert row==('','')
 checks.append('manual_overlap_global_single_claim_publication_validation_redacted_bounded_logs_and_one_use_proof')
 # Two independent queued tasks can race claims but only one runs on the server.
 extra=req(path,token,{**draft,'name':'Second task'},status=201)['task'];req(taskPath+'/run',token,{},status=202);req(path+'/'+extra['id']+'/run',token,{},status=202)
 with concurrent.futures.ThreadPoolExecutor(max_workers=2)as pool:claims=list(pool.map(lambda _:private('claim')['job'],range(2)))
 assert len([job for job in claims if job])==1;active=next(job for job in claims if job);private('report',{'id':active['id'],'proof':active['proof'],'status':'failed','exitCode':2,'log':'failure','truncated':False});nextJob=private('claim')['job'];assert nextJob
 req('/api/collections/web_task_runs/records/'+nextJob['id'],su,{'deadline':'2000-01-01 00:00:00.000Z'},'PATCH');assert private('claim')['job']is None;expired=req('/api/collections/web_task_runs/records/'+nextJob['id'],su);assert expired['status']=='timed_out'and expired['exitCode']==124;private('validate',{'id':nextJob['id'],'proof':nextJob['proof']},401);checks.append('concurrent_worker_claims_and_expired_lease_release_single_server_slot')
 # Rewinding the same occurrence cannot create another automatic run.
 past=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=1)).replace(second=0,microsecond=0).isoformat().replace('T',' ')
 req('/api/collections/web_scheduled_tasks/records/'+taskId,su,{'enabled':True,'schedule':'* * * * *','nextRunAt':past},'PATCH');automatic=private('claim')['job'];assert automatic and automatic['taskId']==taskId;private('report',{'id':automatic['id'],'proof':automatic['proof'],'status':'succeeded','exitCode':0,'log':'cron ran','truncated':False});req('/api/collections/web_scheduled_tasks/records/'+taskId,su,{'nextRunAt':past},'PATCH');assert private('claim')['job']is None
 with sqlite3.connect((data/'data.db').as_uri()+'?mode=ro',uri=True)as db:assert db.execute('SELECT COUNT(*) FROM web_task_runs WHERE taskId=? AND trigger=?',(taskId,'cron')).fetchone()[0]==1
 assert next(row for row in req(path,token)['items']if row['id']==taskId)['lastSuccessAt']
 old=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(hours=2)).replace(second=0,microsecond=0).isoformat().replace('T',' ')
 req('/api/collections/web_scheduled_tasks/records/'+taskId,su,{'nextRunAt':old,'catchUp':'once','maxCatchUpDelayMinutes':15},'PATCH');assert private('claim')['job']is None
 missed=next(row for row in req(path,token)['items']if row['id']==taskId);assert missed['lastRun']['status']=='missed'and missed['lastMissedAt'];assert missed['lastSuccessAt']
 req('/api/collections/web_scheduled_tasks/records/'+taskId,su,{'nextRunAt':past,'catchUp':'skip'},'PATCH');assert private('claim')['job']is None
 checks.append('bounded_catchup_missed_occurrences_and_last_success')
 req('/api/collections/users/records/'+actor['id'],su,{'superAdmin':False},'PATCH');req('/api/collections/web_scheduled_tasks/records/'+taskId,su,{'nextRunAt':past},'PATCH');assert private('claim')['job']is None;assert not req('/api/collections/web_scheduled_tasks/records/'+taskId,su)['enabled']
 # The legacy suspension is never silent: one pushed task_failed alert, without the command or its log.
 events=[row for row in req('/api/collections/web_alert_events/records?perPage=100',su)['items']if row['appId']==ident];assert len(events)==1 and events[0]['event']=='task_failed'and events[0]['details']['status']=='disabled'and events[0]['details']['reason']=='rights',events;assert'command'not in json.dumps(events[0]['details'])
 req('/api/collections/users/records/'+actor['id'],su,{'superAdmin':True},'PATCH');checks.append('utc_occurrence_transaction_dedup_skips_missed_runs_and_revoked_actor_disables_schedule_with_one_alert')
 # Preserve history after deletion and paginate beyond the first ten runs.
 req(taskPath,token,method='DELETE');assert len(req(path,token)['items'])==1
 for index in range(12):req('/api/collections/web_task_runs/records',su,{'appId':ident,'taskId':taskId,'actorId':actor['id'],'taskName':'Deleted task','occurrence':'fixture:'+str(index),'trigger':'manual','status':'succeeded','timeoutSeconds':5,'exitCode':0})
 firstPage=req(history,token);secondPage=req(history+'?page=2',token);assert len(firstPage['items'])==10and firstPage['hasMore']and secondPage['items'];assert not set(row['id']for row in firstPage['items'])&set(row['id']for row in secondPage['items']);checks.append('deletion_keeps_execution_history_and_history_is_paginated')
 print(json.dumps({'passed':True,'checks':checks}),flush=True)
except Exception:
 print('Disposable test failure:',str(root),flush=True)
 if(root/'process.log').exists():print((root/'process.log').read_text()[-3000:])
 raise
finally:
 if process:
  process.terminate()
  try:process.wait(timeout=8)
  except subprocess.TimeoutExpired:process.kill();process.wait()
 shutil.rmtree(root,ignore_errors=True)
