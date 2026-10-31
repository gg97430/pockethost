#!/usr/bin/env python3
"""Native disposable PocketBase: scoped runtime logs and explicitly configured alert channels.

SMTP is an in-process loopback fixture and the worker is played by this script (claim/report/email routes); the
recovery service is a local stub runner. No external endpoint, webhook or recipient is ever contacted.
"""
import argparse,datetime,email,email.policy,hashlib,json,os,pathlib,secrets,shutil,socket,socketserver,subprocess,tempfile,threading,time,urllib.error,urllib.parse,urllib.request
p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=pathlib.Path);p.add_argument('--pocketbase',required=True);args=p.parse_args();os.umask(0o077);root=pathlib.Path(tempfile.mkdtemp(prefix='ph-web-observability-')).resolve();process=None;checks=[];worker=secrets.token_urlsafe(40);messages=[];smtp=None
APP_URL='https://app.kolab.example';APP_HOST='app.kolab.example'
with socket.socket()as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
base=f'http://127.0.0.1:{port}'
def stamp(delta=0):return(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=delta)).isoformat().replace('T',' ')
def req(path,t='',body=None,method=None,status=200,private=False,header=None):
 headers={'Content-Type':'application/json',**({'Authorization':t}if t else{}),**({'X-Web-Worker':worker}if private else{}),**(header or{})}
 request=urllib.request.Request(base+path,method=method or('POST'if body is not None else'GET'),headers=headers,data=None if body is None else json.dumps(body).encode())
 try:
  with urllib.request.urlopen(request,timeout=25)as response:raw=response.read();assert response.status==status,(path,response.status,status);return json.loads(raw)if raw else None
 except urllib.error.HTTPError as error:
  raw=error.read()
  if error.code==status:return json.loads(raw)if raw else None
  raise AssertionError(f'{path}: {error.code} '+raw.decode())
def control(path,body=None,status=200):return req('/api/web-apps/_notifications/'+path,body={}if body is None else body,status=status,private=True)
def records(collection,filter=''):return req('/api/collections/'+collection+'/records?perPage=500'+('&filter='+urllib.parse.quote(filter)if filter else''),su)['items']
def patch(collection,ident,body):return req('/api/collections/'+collection+'/records/'+ident,su,body,'PATCH')
def rescan():
 # The claim scans each channel at most every 15 s: make every channel due now.
 for row in records('web_notification_settings'):patch('web_notification_settings',row['id'],{'scanAfter':'2000-01-01 00:00:00.000Z'})
def drain(disk=None,limit=30):
 jobs=[]
 for _ in range(limit):
  value=control('claim',{}if disk is None else{'diskFreeBytes':disk});job=value['job']
  if not job:return jobs,value
  assert'private-runtime-secret'not in json.dumps(job['payload'])
  if job['channel']=='email':assert control('email',{'id':job['id'],'proof':job['proof']})['sent']
  control('report',{'id':job['id'],'proof':job['proof'],'success':True,'httpStatus':204 if job['channel']=='webhook'else 0});jobs.append(job)
 raise AssertionError('notification queue not drained')
def mail(raw):
 message=email.message_from_bytes(raw,policy=email.policy.default);return str(message['Subject']),message.get_body(preferencelist=('plain',)).get_content(),message
def threaded(function):
 box={};thread=threading.Thread(target=lambda:box.update(value=function()),daemon=True);thread.start();return thread,box
def claim_one(predicate=lambda job:True,tries=60):
 for _ in range(tries):
  job=control('claim')['job']
  if job and predicate(job):return job
  if job:control('report',{'id':job['id'],'proof':job['proof'],'success':True,'httpStatus':204})
  time.sleep(.1)
 raise AssertionError('expected job was never claimed')
class SMTP(socketserver.StreamRequestHandler):
 def handle(self):
  self.wfile.write(b'220 fixture.local ESMTP\r\n')
  while True:
   line=self.rfile.readline()
   if not line:return
   verb=line.decode(errors='replace').split(' ',1)[0].strip().upper()
   if verb in['EHLO','HELO']:self.wfile.write(b'250-fixture.local\r\n250 8BITMIME\r\n')
   elif verb=='DATA':
    self.wfile.write(b'354 Finish with dot\r\n');value=[]
    while True:
     row=self.rfile.readline()
     if row in[b'.\r\n',b'']:break
     value.append(row)
    messages.append(b''.join(value));self.wfile.write(b'250 Queued\r\n')
   elif verb=='QUIT':self.wfile.write(b'221 Bye\r\n');return
   else:self.wfile.write(b'250 OK\r\n')
try:
 source=args.source/'packages/pockethost/src/mothership-app';hooks=root/'hooks';hooks.mkdir();shutil.copy(source/'pb_hooks/mothership.js',hooks/'mothership.js')
 routes=[('GET','/api/web-apps/{id}/runtime-logs','HandleWebRuntimeLogs',True),('POST','/api/web-apps/_observability/config','HandleWebRuntimeLogConfig',False),('GET','/api/web-apps/{id}/notifications','HandleWebNotificationsGet',True),('PUT','/api/web-apps/{id}/notifications','HandleWebNotificationsPut',True),('GET','/api/web-apps/{id}/notifications/history','HandleWebNotificationHistory',True),('POST','/api/web-apps/{id}/notifications/test','HandleWebNotificationTest',True),('POST','/api/web-apps/_notifications/claim','HandleWebNotificationClaim',False),('POST','/api/web-apps/_notifications/report','HandleWebNotificationReport',False),('POST','/api/web-apps/_notifications/email','HandleWebNotificationEmail',False),
  ('GET','/api/web-platform-notifications','HandleWebPlatformNotificationsGet',True),('PUT','/api/web-platform-notifications','HandleWebPlatformNotificationsPut',True),('POST','/api/web-platform-notifications/test','HandleWebPlatformNotificationTest',True),('GET','/api/web-platform-notifications/history','HandleWebPlatformNotificationHistory',True),('POST','/api/web-apps/_platform/recovery-events','HandleWebPlatformRecoveryEvent',False),('POST','/api/web-apps/_worker/backups/claim','HandleWebBackupClaim',False),('POST','/api/web-apps/_worker/backups/report','HandleWebBackupReport',False)]
 text='\n'.join('routerAdd('+json.dumps(method)+','+json.dumps(path)+',e=>require(`${__hooks}/mothership`).'+handler+'(e)'+(',$apis.requireAuth("users")'if auth else'')+');'for method,path,handler,auth in routes)
 text+='''routerAdd('POST','/test/fixture',e=>{if(!e.auth?.getBool('superAdmin'))throw new ForbiddenError();const mod=require(`${__hooks}/mothership`),i=e.requestInfo().body||{},app=new Record($app.findCollectionByNameOrId('web_apps')),env={TOKEN:{value:'private-runtime-secret',build:false,runtime:true}};app.set('uid',e.auth.id);app.set('name',i.name||'Observability fixture');app.set('status','running');app.set('config',{repository:'owner/repo',branch:'main',generatedDomain:'web-fixture.example.invalid'});app.set('envEncrypted',mod.encryptWebValue(env));$app.save(app);const job=new Record($app.findCollectionByNameOrId('web_deployments'));job.set('appId',app.id);job.set('action','deploy');job.set('status','ready');job.set('snapshotEncrypted',mod.encryptWebValue({env}));$app.save(job);job.set('result',{container:'ph-web-'+app.id+'-'+job.id});$app.save(job);app.set('activeDeployment',job.id);$app.save(app);return e.json(200,{id:app.id,deploymentId:job.id})},$apis.requireAuth('users'));
routerAdd('POST','/test/emit',e=>{if(!e.auth?.getBool('superAdmin'))throw new ForbiddenError();const i=e.requestInfo().body;return e.json(200,{created:require(`${__hooks}/mothership`).emitWebAlert($app,i.scope,i.appId||'',i.event,i.key,i.details||{})})},$apis.requireAuth('users'));
routerAdd('POST','/test/watchdog',e=>{if(!e.auth?.getBool('superAdmin'))throw new ForbiddenError();require(`${__hooks}/mothership`).HandleWebAlertWatchdog();return e.json(200,{ok:true})},$apis.requireAuth('users'));'''
 (hooks/'observability.pb.js').write_text(text)
 # Recovery service stub: the fixed runner contract ({status, response}) for the watchdog's read-only status/runs calls.
 stub=root/'recovery'/'service.py';stub.parent.mkdir();state_file=stub.with_name('state.json')
 stub.write_text("import json,pathlib,sys\nstate=json.loads(pathlib.Path(__file__).with_name('state.json').read_text())\nprint(json.dumps({'status':200,'response':state[sys.argv[2]]}))\n")
 def recovery_state(runs):state_file.write_text(json.dumps({'status':{'configured':True,'worker':{'online':True,'lastSeenAt':stamp()},'settings':{'enabled':True},'latest':{'id':'c'*32,'finishedAt':stamp(-60)}},'runs':{'items':runs,'page':1}}))
 recovery_state([])
 env={**os.environ,'WEB_APPS_ENABLED':'true','WEB_APPS_MASTER_KEY':'e'*32,'WEB_APPS_WORKER_SECRET':worker,'WEB_APPS_ROOT':str(root/'web'),'DATA_ROOT':str(root/'data'),'APEX_DOMAIN':'example.invalid','APP_URL':APP_URL,'APP_HOST':APP_HOST,'WEB_PLATFORM_RECOVERY_RUNNER':str(stub)};password=secrets.token_urlsafe(32).lstrip('-');address='observability@example.invalid';data=root/'pb_data'
 subprocess.run([args.pocketbase,'superuser','upsert',address,password,'--dir',str(data),'--hooksDir',str(root/'empty'),'--migrationsDir',str(source/'pb_migrations')],env=env,check=True,capture_output=True)
 log=(root/'process.log').open('wb');process=subprocess.Popen([args.pocketbase,'serve','--dir',str(data),'--hooksDir',str(hooks),'--migrationsDir',str(source/'pb_migrations'),'--http',f'127.0.0.1:{port}'],env=env,stdout=log,stderr=subprocess.STDOUT)
 for _ in range(100):
  if process.poll()is not None:raise RuntimeError((root/'process.log').read_text()[-2000:])
  try:req('/api/health');break
  except OSError:time.sleep(.1)
 su=req('/api/collections/_superusers/auth-with-password',body={'identity':address,'password':password})['token']
 def user(name,admin):
  row=req('/api/collections/users/records',su,{'email':name+'@example.invalid','username':name,'password':password,'passwordConfirm':password,'verified':True,'superAdmin':admin,'subscription':'premium','subscription_quantity':1});return row,req('/api/collections/users/auth-with-password',body={'identity':row['email'],'password':password})['token']
 actor,token=user('obsadmin',True);_,plain=user('obsplain',False);_,second=user('obssecond',True);app=req('/test/fixture',token,{});ident=app['id'];path='/api/web-apps/'+ident;settingsPath=path+'/notifications';operator=actor['email']
 for route in[path+'/runtime-logs',settingsPath,settingsPath+'/history']:req(route,status=401);req(route,plain,status=404)
 for collection in['web_notification_settings','web_notification_deliveries','web_alert_events','web_platform_notification_settings']:req('/api/collections/'+collection+'/records',token,status=403)
 req('/api/web-apps/_observability/config',body={},status=401);req('/api/web-apps/_notifications/claim',body={},status=401)
 # Same-host proxies (firewall, dashboard) reach PocketBase from 127.0.0.1: a relayed request is never "local", even with
 # the worker secret, and the proxies refuse the worker routes before relaying (404, nothing reaches PocketBase).
 for relay in[{'X-Forwarded-For':'203.0.113.9'},{'X-Forwarded-Host':APP_HOST},{'X-Real-IP':'203.0.113.9'},{'Forwarded':'for=203.0.113.9'},{'Via':'1.1 edge'}]:
  for route in['/api/web-apps/_notifications/claim','/api/web-apps/_notifications/report','/api/web-apps/_platform/recovery-events','/api/web-apps/_observability/config','/api/web-apps/_worker/backups/claim']:req(route,body={},status=403,private=True,header=relay)
 dashboard=root/'dashboard-build';dashboard.mkdir();(dashboard/'index.html').write_text('Home')
 with socket.socket()as s:s.bind(('127.0.0.1',0));proxyPort=s.getsockname()[1]
 proxy=subprocess.Popen(['node',str(args.source/'packages/dashboard/scripts/serve-static-build.cjs'),str(dashboard)],env={**os.environ,'HOST':'127.0.0.1','PORT':str(proxyPort),'MOTHERSHIP_PROXY_TARGET':base},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 try:
  def via(path,body=None):
   request=urllib.request.Request(f'http://127.0.0.1:{proxyPort}'+path,method='POST'if body is not None else'GET',headers={'Content-Type':'application/json','X-Web-Worker':worker},data=None if body is None else json.dumps(body).encode())
   try:
    with urllib.request.urlopen(request,timeout=10)as response:return response.status,response.read()
   except urllib.error.HTTPError as error:return error.code,error.read()
  for _ in range(50):
   try:via('/');break
   except OSError:time.sleep(.1)
  before=len(records('web_platform_notification_settings'))
  for relayed in['/api/web-apps/_notifications/claim','/api/web-apps/%5Fnotifications/claim','/api/web-apps/%255Fplatform/recovery-events','//api//web-apps//_worker/backups/claim','/api/x/../web-apps/_notifications/claim','/API/web-apps/_notifications/claim']:
   status,_=via(relayed,{});assert status==404,(relayed,status)
  # The claim would have created the platform row: nothing reached the controller.
  assert len(records('web_platform_notification_settings'))==before
  assert via('/api/health')[0]==200
  # Defence in depth: the proxy also marks what it relays (X-Forwarded-For), refused by the controller.
  assert via('/api/web-apps/'+ident+'/notifications')[0]==401
 finally:proxy.terminate();proxy.wait(timeout=5)
 checks.append('worker_routes_refuse_relayed_requests_and_dashboard_proxy_never_relays_them')
 first=control('claim');assert first['job']is None and first['platformWebhook']is None;initial=req(settingsPath,token);assert not initial['webhook']['enabled']and not initial['email']['enabled']and not initial['webhook']['configured']and initial['webhook']['format']=='generic';assert initial['healthMonitoring']=='disabled'and not initial['inProject']and'disk_low'not in initial['events'];req(settingsPath+'/test',token,{'channel':'webhook'},status=400);checks.append('admin_scopes_private_worker_locked_collections_and_zero_sends_without_channel')
 descriptor=req('/api/web-apps/_observability/config',body={},private=True)['items'][0];assert descriptor['appId']==ident and descriptor['secrets']==['private-runtime-secret'];folder=root/'web/runtime-logs';folder.mkdir(parents=True);rows=[{'id':format(i,'024x'),'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'stream':'stderr'if i%2 else'stdout','level':'error'if i%2 else'info','message':'private-runtime-secret échec '+str(i)}for i in range(230)];filename=folder/(ident+'.json');filename.write_text(json.dumps({'version':1,'appId':ident,'deploymentId':app['deploymentId'],'generation':'a'*16,'checkedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'state':'running','records':rows}))
 latest=req(path+'/runtime-logs',token);assert len(latest['items'])==100and latest['hasOlder'];assert'private-runtime-secret'not in json.dumps(latest)and'[masqué]'in json.dumps(latest,ensure_ascii=False);older=req(path+'/runtime-logs?cursor='+latest['olderCursor'],token);assert len(older['items'])==100;last=req(path+'/runtime-logs?cursor='+older['olderCursor'],token);assert len(last['items'])==30and not last['hasOlder'];filtered=req(path+'/runtime-logs?q=%C3%89CHEC%20229&level=error',token);assert len(filtered['items'])==1
 for query in['cursor=../../x','level=sql','q='+'x'*101,'from=bad','from=2030-01-01&to=2020-01-01']:req(path+'/runtime-logs?'+query,token,status=400)
 filename.unlink();private=root/'private';private.write_text('private-file');filename.symlink_to(private);assert req(path+'/runtime-logs',token)['items']==[];checks.append('runtime_utf8_all_retained_pagination_literal_filters_secret_redaction_symlink_refusal')
 for payload in[{'revision':'','webhook':{'enabled':True,'url':'http://receiver.example.invalid'}},{'revision':'','email':{'enabled':True,'recipients':['ops@example.invalid']}},{'revision':'','webhook':{'enabled':True,'url':'https://user:pass@receiver.example.invalid'}},{'revision':'','webhook':{'enabled':True,'url':'https://receiver.example.invalid/x','format':'teams'}}]:req(settingsPath,token,payload,'PUT',400)
 assert'hooks.slack.com'in req(settingsPath,token,{'revision':'','webhook':{'enabled':True,'url':'https://receiver.example.invalid/x','format':'slack'}},'PUT',400)['message']
 assert'discord.com'in req(settingsPath,token,{'revision':'','webhook':{'enabled':True,'url':'https://hooks.slack.com/services/T0/B0/XYZ','format':'discord'}},'PUT',400)['message'];checks.append('slack_discord_generic_format_validation_with_french_messages')
 allEvents=['health_degraded','health_recovered','deployment_failed','backup_failed','runtime_stopped','task_failed','disk_low','disk_recovered','certificate_expiring','certificate_failed']
 cfg=req(settingsPath,token,{'revision':'','webhook':{'enabled':True,'url':'https://receiver.example.invalid/private-hook','secret':'explicit-test-secret-123'},'events':allEvents,'cooldownSeconds':60},'PUT');assert cfg['webhook']['configured']and cfg['webhook']['hasSecret']and'generatedSecret'not in cfg;assert'private-hook'not in json.dumps(cfg)and'explicit-test-secret'not in json.dumps(cfg);req(settingsPath,token,{'revision':'','webhook':{'enabled':False}},'PUT',400)
 # Reveal-once HMAC secret: generated secrets come back once; rotation cancels deliveries signed with the old one.
 req('/test/emit',token,{'scope':'app','appId':ident,'event':'runtime_stopped','key':'rotation'});job=control('claim')['job'];assert job and job['payload']['event']=='runtime_stopped';control('report',{'id':job['id'],'proof':job['proof'],'success':False,'httpStatus':503,'errorCode':'http_refused','errorClass':'http_5xx'})
 rotated=req(settingsPath,token,{'revision':cfg['revision'],'webhook':{'enabled':True,'rotateSecret':True}},'PUT');assert len(rotated['generatedSecret'])==48 and rotated['revision']!=cfg['revision']
 assert'generatedSecret'not in req(settingsPath,token)and req(settingsPath,token)['webhook']['hasSecret'];patch('web_notification_deliveries',job['id'],{'nextAttemptAt':'2000-01-01 00:00:00.000Z'});assert control('claim')['job']is None
 assert req('/api/collections/web_notification_deliveries/records/'+job['id'],su)['status']=='suppressed';again=req(settingsPath,token,{'revision':rotated['revision'],'webhook':{'enabled':True}},'PUT');assert'generatedSecret'not in again
 req('/api/collections/web_app_health_events/records',su,{'appId':ident,'status':'degraded','fromStatus':'healthy','reason':'failure_threshold','checkedAt':stamp(),'httpStatus':503})
 req('/api/collections/web_deployments/records',su,{'appId':ident,'action':'deploy','status':'failed','finishedAt':stamp(),'stage':'Construction','error':'build failed: private-runtime-secret leaked\nsecond line'})
 failedBackup=req('/api/collections/web_app_backups/records',su,{'appId':ident,'status':'ready','s3State':'failed','trigger':'manual','s3Error':'copy failed private-runtime-secret'})
 req('/api/collections/web_app_backups/records',su,{'appId':ident,'status':'failed','s3State':'failed','trigger':'platform','error':'platform capture archive'})
 req('/api/collections/web_app_domains/records',su,{'appId':ident,'host':'custom.example.invalid','status':'active','ownershipToken':'a'*40,'certificateExpiresAt':(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=10)).isoformat()})
 req('/api/collections/web_task_runs/records',su,{'appId':ident,'taskId':'t'*15,'actorId':actor['id'],'taskName':'Purge','occurrence':'manual:1','trigger':'manual','status':'failed','timeoutSeconds':5,'exitCode':3,'finishedAt':stamp(),'log':'private-runtime-secret in log'})
 assert req('/test/emit',token,{'scope':'app','appId':ident,'event':'runtime_stopped','key':'runtime:fixture','details':{'reason':'cache_or_disk'}})['created'];assert not req('/test/emit',token,{'scope':'app','appId':ident,'event':'runtime_stopped','key':'runtime:fixture'})['created']
 claimed,value=drain(10*1024**3)
 assert sorted(job['payload']['event']for job in claimed)==sorted(['health_degraded','deployment_failed','backup_failed','disk_low','certificate_expiring','task_failed','runtime_stopped']),[job['payload']['event']for job in claimed];assert value['platformWebhook']is None
 job=next(job for job in claimed if job['payload']['event']=='deployment_failed');assert job['format']=='generic'and job['trigger']=='auto'and job['scope']=='app'and job['secret']==rotated['generatedSecret'];payload=job['payload']
 assert payload['version']==2 and payload['title']=='Échec de déploiement'and payload['application']['dashboardUrl']==APP_URL+'/applications/'+ident+'?tab=deployments'and payload['application']['url']=='https://web-fixture.example.invalid'
 assert payload['details']['summary']=='build failed: [masqué] leaked'and payload['details']['stage']=='Construction';task=next(job for job in claimed if job['payload']['event']=='task_failed');assert task['payload']['details']['taskName']=='Purge'and'log'not in task['payload']['details']
 assert control('claim',{'diskFreeBytes':10*1024**3})['job']is None;checks.append('explicit_encrypted_webhook_config_event_scans_outbox_tasks_dedup_disk_certificates_summaries_without_secrets')
 # One backup_failed per backup row, whatever happens to s3State afterwards; crash-looped backups alert once failed.
 patch('web_app_backups',failedBackup['id'],{'s3State':'ready'});patch('web_app_backups',failedBackup['id'],{'s3State':'failed','s3Error':'again'});rescan();assert drain()[0]==[]
 patch('web_app_backups',failedBackup['id'],{'status':'deleting'});rescan();assert drain()[0]==[]
 assert len(records('web_notification_deliveries',f'event="backup_failed" && appId="{ident}"'))==1
 looping=req('/api/collections/web_app_backups/records',su,{'appId':ident,'status':'running','s3State':'pending','trigger':'scheduled','operation':'create','leaseAttempts':3,'leaseAt':'2000-01-01 00:00:00.000Z'})
 req('/api/web-apps/_worker/backups/claim',body={},private=True);assert req('/api/collections/web_app_backups/records/'+looping['id'],su)['status']=='failed'
 rescan();drain();alerts=records('web_notification_deliveries',f'event="backup_failed" && appId="{ident}"')
 # Within the 60 s cooldown the second backup_failed is grouped (rateLimited), never lost: it is summarised hourly.
 assert len(alerts)==2 and[row['payload']['details']['backupId']for row in alerts if row['payload']['details']['backupId']==looping['id']]==[looping['id']];checks.append('no_double_backup_failed_on_s3_state_change_and_crash_loop_alerts_once')
 # Retention or manual deletion that fails (S3 down, bucket lost): reported failures never exhaust the lease budget nor
 # alert the customer; only a crash loop (no report) abandons it, alerts the platform channel and is retried a day later.
 pruned=req('/api/collections/web_app_backups/records',su,{'appId':ident,'status':'deleting','operation':'delete','s3State':'ready','trigger':'scheduled','leaseAttempts':0})
 def claim_backup(target):
  for _ in range(20):
   job=req('/api/web-apps/_worker/backups/claim',body={},private=True)['job'];assert job,'deletion never claimed'
   if job['id']==target:return job
   req('/api/web-apps/_worker/backups/report',body={'id':job['id'],'lease':job['lease'],'status':'deleted'},private=True)
  raise AssertionError('deletion never claimed')
 for attempt in range(5):
  job=claim_backup(pruned['id']);assert job['operation']=='delete'and job['attempt']==1,job
  req('/api/web-apps/_worker/backups/report',body={'id':job['id'],'lease':job['lease'],'status':'failed','failureCode':'engine_error','attempts':1,'error':'La suppression S3 a échoué. La copie locale est conservée.'},private=True)
  row=req('/api/collections/web_app_backups/records/'+pruned['id'],su);assert row['status']=='deleting'and row['leaseAttempts']==0,row
  patch('web_app_backups',pruned['id'],{'leaseAt':'2000-01-01 00:00:00.000Z'})
 patch('web_app_backups',pruned['id'],{'leaseAttempts':3});job=claim_backup(pruned['id']);assert job['attempt']==4
 req('/api/web-apps/_worker/backups/report',body={'id':job['id'],'lease':job['lease'],'status':'failed','failureCode':'attempts_exhausted','attempts':4,'error':'Sauvegarde abandonnée après 3 tentatives interrompues.'},private=True)
 row=req('/api/collections/web_app_backups/records/'+pruned['id'],su);assert row['status']=='failed'and row['operation']=='delete'and'suppression'in row['error']and'Sauvegarde abandonnée'not in row['error'],row
 rescan();drain();assert not[item for item in records('web_notification_deliveries',f'event="backup_failed" && appId="{ident}"')if item['payload']['details'].get('backupId')==pruned['id']]
 cleanup=records('web_alert_events','scope="platform" && event="backup_cleanup_failed"');assert len(cleanup)==1 and cleanup[0]['details']=={'appId':ident,'backupId':pruned['id'],'attempts':4},cleanup
 patch('web_app_backups',pruned['id'],{'finishedAt':stamp(-2*86400)});job=claim_backup(pruned['id']);assert job['attempt']==1
 req('/api/web-apps/_worker/backups/report',body={'id':job['id'],'lease':job['lease'],'status':'deleted'},private=True);assert req('/api/collections/web_app_backups/records/'+pruned['id'],su)['status']=='deleted'
 checks.append('failed_deletions_keep_retrying_never_alert_the_customer_crash_loop_goes_to_platform_and_retries_daily')
 req('/api/collections/web_app_health_events/records',su,{'appId':ident,'status':'healthy','fromStatus':'degraded','reason':'recovered','checkedAt':stamp(),'httpStatus':200});rescan();jobs,_=drain(20*1024**3);assert sorted(job['payload']['event']for job in jobs)==['disk_recovered','health_recovered'];checks.append('health_recovered_and_host_disk_recovery')
 # Synchronous test per event type: the request waits for the real worker report (played here) and returns it.
 req(settingsPath+'/test',token,{'channel':'webhook','event':'nope'},status=400);req(settingsPath+'/test',token,{'channel':'webhook','extra':1},status=400)
 thread,box=threaded(lambda:req(settingsPath+'/test',token,{'channel':'webhook','event':'deployment_failed'}));job=claim_one()
 assert job['trigger']=='test'and job['payload']['event']=='deployment_failed'and job['payload']['details']['drill']is True and job['payload']['title']=='Échec de déploiement';control('report',{'id':job['id'],'proof':job['proof'],'success':True,'httpStatus':204,'errorCode':'','errorClass':'','durationMs':120,'final':True});thread.join(20)
 assert box['value']['done']and box['value']['result']['status']=='sent'and box['value']['result']['httpStatus']==204
 for _ in range(3):
  thread,box=threaded(lambda:req(settingsPath+'/test',token,{'channel':'webhook'}));job=claim_one();assert job['payload']['event']=='test';control('report',{'id':job['id'],'proof':job['proof'],'success':True,'httpStatus':200});thread.join(20);assert box['value']['result']['status']=='sent'
 thread,box=threaded(lambda:req(settingsPath+'/test',token,{'channel':'webhook','event':'health_degraded'}));job=claim_one()
 control('report',{'id':job['id'],'proof':job['proof'],'success':False,'httpStatus':0,'errorCode':'invalid_format','errorClass':'bogus'},400)
 control('report',{'id':job['id'],'proof':job['proof'],'success':False,'httpStatus':0,'errorCode':'invalid_format','errorClass':'format','durationMs':3,'final':True});thread.join(20);result=box['value']['result']
 assert box['value']['done']and result['status']=='failed'and result['attempts']==1 and'Slack/Discord'in result['lastError'],result
 silent=req(settingsPath+'/test',token,{'channel':'webhook','event':'backup_failed'},status=202);assert not silent['done']and silent['result']['status']=='pending'
 assert req(settingsPath+'/history?id='+silent['result']['id'],token)['items'][0]['id']==silent['result']['id'];job=claim_one();assert job['id']==silent['result']['id'];control('report',{'id':job['id'],'proof':job['proof'],'success':True,'httpStatus':204})
 checks.append('synchronous_test_send_per_event_type_with_real_worker_result_unknown_error_class_refused_and_silent_worker_202')
 req('/api/collections/web_deployments/records',su,{'appId':ident,'action':'domain','status':'failed','finishedAt':stamp()});rescan();job=control('claim')['job'];assert job['payload']['event']=='certificate_failed'
 for attempt in range(1,4):
  assert job;control('report',{'id':job['id'],'proof':job['proof'],'success':False,'httpStatus':503,'errorCode':'http_refused','errorClass':'http_5xx'});control('report',{'id':job['id'],'proof':job['proof'],'success':True,'httpStatus':204},401)
  if attempt<3:patch('web_notification_deliveries',job['id'],{'nextAttemptAt':'2000-01-01 00:00:00.000Z'});job=control('claim')['job']
 history=req(settingsPath+'/history',token);row=next(row for row in history['items']if row['id']==job['id']);assert row['status']=='failed'and row['attempts']==3 and row['lastError']=='Le canal est en erreur (HTTP 5xx).';assert all('proof'not in row and'payload'not in row for row in history['items']);checks.append('bounded_three_attempt_retry_backoff_one_use_lease_and_safe_history')
 app2=req('/test/fixture',token,{'name':'Second fixture'})['id'];path2='/api/web-apps/'+app2+'/notifications'
 fresh=req(path2,token,{'revision':'','webhook':{'enabled':True,'url':'https://receiver.example.invalid/second'}},'PUT');assert len(fresh['generatedSecret'])==48
 slack=req(path2,token,{'revision':fresh['revision'],'webhook':{'enabled':True,'url':'https://hooks.slack.com/services/T000/B000/XXXX','format':'slack'}},'PUT');assert'generatedSecret'not in slack and slack['webhook']['format']=='slack'
 req(path2,token,{'revision':slack['revision'],'webhook':{'enabled':True,'rotateSecret':True}},'PUT',400);checks.append('reveal_once_generated_and_rotated_hmac_secret_suppresses_old_signed_deliveries')
 again=req(settingsPath,token)
 smtp=socketserver.ThreadingTCPServer(('127.0.0.1',0),SMTP);threading.Thread(target=smtp.serve_forever,daemon=True).start();req('/api/settings',su,{'meta':{'senderAddress':'alerts@example.invalid','senderName':'Kolab Hosting'},'smtp':{'enabled':True,'host':'127.0.0.1','port':smtp.server_address[1],'username':'','password':'','tls':False,'authMethod':'PLAIN'}},'PATCH')
 req(settingsPath,token,{'revision':again['revision'],'webhook':{'enabled':False},'email':{'enabled':True,'recipients':['ops@example.invalid']}},'PUT',400)
 cfg=req(settingsPath,token,{'revision':again['revision'],'webhook':{'enabled':False},'email':{'enabled':True,'recipients':[operator]}},'PUT');assert cfg['smtpAvailable']
 result=req(settingsPath+'/test',token,{'channel':'email','event':'deployment_failed'});assert result['done']and result['result']['status']=='sent'and len(messages)==1
 subject,body,message=mail(messages[0]);assert subject=='[Kolab Hosting] TEST · Échec de déploiement · Observability fixture',subject;assert'Kolab Hosting'in str(message['From'])
 assert APP_URL+'/applications/'+ident+'?tab=deployments'in body and APP_URL+'/applications/'+ident+'?tab=notifications'in body and'— Kolab Hosting · '+APP_HOST in body and'private-runtime-secret'not in body
 req('/api/collections/web_deployments/records',su,{'appId':ident,'action':'deploy','status':'failed','finishedAt':stamp(),'error':'private-runtime-secret again: https://evil.example/login'});rescan();job=control('claim')['job'];assert job['channel']=='email';lease={'id':job['id'],'proof':job['proof']};assert control('email',lease)['sent'];assert control('email',lease)['sent'];assert len(messages)==2;control('report',{**lease,'success':True,'httpStatus':0})
 subject,body,detailMail=mail(messages[1]);assert subject=='[Kolab Hosting] Échec de déploiement · Observability fixture'and'Détail : [masqué] again'in body and b'private-runtime-secret'not in messages[1],(subject,body)
 html=detailMail.get_body(preferencelist=('html',)).get_content();assert'href="https://evil'not in html and'https://evil.example/login'in html and f'href="{APP_URL}/applications/{ident}?tab=notifications"'in html,html;checks.append('kolab_hosting_branded_email_with_deep_link_test_marker_idempotent_within_lease')
 for _ in range(9):assert req(settingsPath+'/test',token,{'channel':'email'})['result']['status']=='sent'
 assert'Dix tests'in req(settingsPath+'/test',token,{'channel':'email'},status=429)['message'];checks.append('tests_never_grouped_and_capped_at_ten_per_channel_hour')
 # Per-host disk dedup: one host transition reaches a destination once, however many applications report it.
 for target in[path2]:
  current=req(target,token);req(target,token,{'revision':current['revision'],'webhook':{'enabled':False},'email':{'enabled':True,'recipients':[operator]},'events':allEvents},'PUT')
 rescan();drain(10*1024**3);assert len(records('web_notification_deliveries','event="disk_low" && channel="email"'))==1;checks.append('per_host_disk_transition_deduplicated_by_destination_across_apps')
 # Platform channel: superAdmins only, its own outbox, recovery push route and watchdog.
 req('/api/web-platform-notifications',plain,status=403);req('/api/web-platform-notifications',status=401);platform=req('/api/web-platform-notifications',token);assert platform['revision']==''and'recovery_failed'in platform['availableEvents']and'app_backup_failed'not in platform['events']
 platform=req('/api/web-platform-notifications',token,{'revision':'','webhook':{'enabled':True,'url':'https://ops.example.invalid/platform'},'email':{'enabled':True,'recipients':[]},'useOperator':True},'PUT');assert len(platform['generatedSecret'])==48 and platform['email']['recipients']==[operator]
 hook=control('claim')['platformWebhook'];assert hook=={'url':'https://ops.example.invalid/platform','secret':platform['generatedSecret'],'format':'generic','host':APP_HOST},hook
 run='a'*32;event={'version':1,'event':'recovery_failed','eventKey':'platform-recovery:'+run,'platform':'app1','operation':'capture','runId':run,'trigger':'scheduled','stage':'capture','errorCode':'VERIFICATION','occurredAt':stamp(),'message':'La capture de reprise du serveur a échoué.'}
 req('/api/web-apps/_platform/recovery-events',body=event,status=401);req('/api/web-apps/_platform/recovery-events',body=event,status=401,header={'X-Web-Worker':'x'*40})
 for bad in[{'eventKey':'platform-recovery:'+'b'*32},{'errorCode':'PRIVATE'},{'message':'x'*201},{'operation':'restore'}]:req('/api/web-apps/_platform/recovery-events',body={**event,**bad},status=400,private=True)
 assert req('/api/web-apps/_platform/recovery-events',body=event,status=202,private=True)['accepted'];assert req('/api/web-apps/_platform/recovery-events',body=event,status=202,private=True)['accepted']
 key=hashlib.sha256(('platform-recovery:'+run).encode()).hexdigest();assert len(records('web_alert_events',f'scope="platform" && eventKey="{key}"'))==1
 jobs,_=drain();platformJobs=[job for job in jobs if job['scope']=='platform'];assert sorted((job['channel'],job['payload']['event'])for job in platformJobs)==[('email','recovery_failed'),('webhook','recovery_failed')],jobs
 webhookJob=next(job for job in platformJobs if job['channel']=='webhook');assert webhookJob['payload']['application']is None and webhookJob['payload']['dashboardUrl']==APP_URL+'/applications/recovery'and webhookJob['secret']==platform['generatedSecret']
 subject,body,_=mail(messages[-1]);assert subject=='[Kolab Hosting] Échec de capture ou de test de reprise · Plateforme '+APP_HOST,subject
 later='d'*32;recovery_state([{'id':run,'status':'failed','operation':'capture','stage':'Capture','error':'Le contrôle a échoué.','finishedAt':stamp()},{'id':later,'status':'failed','operation':'drill','stage':'Récupération','error':'Le traitement a échoué.','finishedAt':stamp()},{'id':'e'*32,'status':'failed','operation':'drill','finishedAt':'2001-01-01 00:00:00.000Z'},{'id':'f'*32,'status':'ready','operation':'capture','finishedAt':stamp()}])
 req('/test/watchdog',token,{});drain();rows=records('web_notification_deliveries','event="recovery_failed" && scope="platform"')
 # Same runId as the push: no second alert. The other failed run is new (grouped by the cooldown, never lost); runs
 # finished before the channel was activated are ignored.
 assert sorted((row['channel'],row['payload']['details']['runId'],row['rateLimited'])for row in rows)==[('email',run,False),('email',later,True),('webhook',run,False),('webhook',later,True)],rows
 assert len(records('web_alert_events','scope="platform" && event="recovery_failed"'))==2;checks.append('recovery_push_route_worker_auth_idempotent_key_shared_with_watchdog_poll_backstop')
 row=records('web_platform_notification_settings')[0];patch('web_platform_notification_settings',row['id'],{'lastClaimAt':stamp(-240),'workerDown':False});count=len(messages)
 req('/test/watchdog',token,{});row=records('web_platform_notification_settings')[0];assert row['workerDown'];assert len(messages)==count+1 and mail(messages[-1])[0].startswith('[Kolab Hosting] Worker web injoignable')
 down=records('web_notification_deliveries','event="worker_unresponsive"');assert sorted((item['channel'],item['status'])for item in down)==[('email','sent'),('webhook','pending')],down
 jobs,_=drain();assert sorted(job['payload']['event']for job in jobs)==['worker_recovered','worker_recovered','worker_unresponsive'],[job['payload']['event']for job in jobs];assert not records('web_platform_notification_settings')[0]['workerDown'];checks.append('watchdog_worker_unresponsive_email_without_worker_then_worker_recovered')
 result=req('/api/web-platform-notifications/test',token,{'channel':'email','event':'disk_low'});assert result['done']and result['result']['status']=='sent'and mail(messages[-1])[0].startswith('[Kolab Hosting] TEST · Espace disque faible')
 req('/api/web-platform-notifications/test',plain,{'channel':'email'},status=403);assert all(item['event']not in['recovery_failed','worker_unresponsive']for item in req(settingsPath+'/history',token)['items']);assert req('/api/web-platform-notifications/history',token)['items']
 platform=req('/api/web-platform-notifications',token);req('/api/web-platform-notifications',token,{'revision':platform['revision'],'copyAppEvents':True,'events':platform['events']+['app_deployment_failed']},'PUT')
 req('/api/collections/web_deployments/records',su,{'appId':app2,'action':'deploy','status':'failed','finishedAt':stamp(),'error':'boom'});rescan();jobs,_=drain();copies=[job for job in jobs if job['payload']['event']=='app_deployment_failed'];assert copies and all(job['scope']=='platform'and job['payload']['details']['appId']==app2 for job in copies);checks.append('platform_channel_superadmin_only_separate_history_test_send_and_optional_app_copies')
 # Alerts are application settings: a demoted legacy author pauses them without disabling, nothing is consumed.
 req('/api/collections/users/records/'+actor['id'],su,{'superAdmin':False},'PATCH');req('/api/collections/web_task_runs/records',su,{'appId':ident,'taskId':'t'*15,'actorId':actor['id'],'taskName':'Late','occurrence':'manual:2','trigger':'manual','status':'timed_out','timeoutSeconds':5,'exitCode':124,'finishedAt':stamp()});rescan()
 assert all(job['scope']=='platform'for job in drain()[0]);paused=req(settingsPath,second);assert paused['paused']and not paused['pausedLegacy'];assert all(row['enabled']for row in records('web_notification_settings',f'appId="{ident}"'))
 req('/api/collections/users/records/'+actor['id'],su,{'superAdmin':True},'PATCH');rescan();jobs,_=drain();assert[job['payload']['event']for job in jobs if job['scope']=='app'and job['payload']['application']['id']==ident]==['task_failed'];checks.append('demoted_legacy_author_pauses_without_silent_disable_and_resumes_without_losing_events')
 print(json.dumps({'passed':True,'checks':checks}),flush=True)
except Exception:
 print('Disposable test failure:',root,flush=True)
 if(root/'process.log').exists():print((root/'process.log').read_text()[-2500:])
 raise
finally:
 if smtp:smtp.shutdown();smtp.server_close()
 if process:
  process.terminate()
  try:process.wait(timeout=8)
  except subprocess.TimeoutExpired:process.kill();process.wait()
 shutil.rmtree(root,ignore_errors=True)
