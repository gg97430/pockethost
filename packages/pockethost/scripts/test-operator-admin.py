#!/usr/bin/env python3
"""Test operator administration against disposable PocketBase and a local-only SMTP capture server."""
import argparse,json,os,pathlib,secrets,shutil,socket,socketserver,subprocess,tempfile,threading,time,urllib.request,urllib.error
parser=argparse.ArgumentParser();parser.add_argument('--source',required=True,type=pathlib.Path);parser.add_argument('--pocketbase',required=True);args=parser.parse_args()
os.umask(0o077);root=pathlib.Path(tempfile.mkdtemp(prefix='ph-admin-test-'));process=None
captured=[]
checks=[]
class SMTP(socketserver.StreamRequestHandler):
 def handle(self):
  self.wfile.write(b'220 localhost ESMTP capture\r\n');data=False;lines=[]
  while True:
   line=self.rfile.readline()
   if not line:return
   if data:
    if line==b'.\r\n':captured.append(b''.join(lines));data=False;self.wfile.write(b'250 queued\r\n')
    else:lines.append(line)
   elif line.upper().startswith(b'EHLO'):self.wfile.write(b'250-localhost\r\n250-AUTH PLAIN LOGIN\r\n250 SIZE 1000000\r\n')
   elif line.upper().startswith(b'AUTH'):self.wfile.write(b'535 5.7.8 credentials rejected\r\n')
   elif line.upper().startswith(b'DATA'):data=True;lines=[];self.wfile.write(b'354 go\r\n')
   elif line.upper().startswith(b'QUIT'):self.wfile.write(b'221 bye\r\n');return
   else:self.wfile.write(b'250 ok\r\n')
class Server(socketserver.ThreadingTCPServer):allow_reuse_address=True;daemon_threads=True
smtp=Server(('127.0.0.1',0),SMTP);threading.Thread(target=smtp.serve_forever,daemon=True).start()
with socket.socket() as socket_:socket_.bind(('127.0.0.1',0));port=socket_.getsockname()[1]
base=f'http://127.0.0.1:{port}'
def req(path,token='',body=None,method=None,status=200):
 request=urllib.request.Request(base+path,method=method or ('POST' if body is not None else 'GET'),headers={'Content-Type':'application/json',**({'Authorization':token} if token else {})},data=None if body is None else json.dumps(body).encode())
 try:
  with urllib.request.urlopen(request,timeout=40) as response:
   data=response.read();assert response.status==status;return json.loads(data) if data else None
 except urllib.error.HTTPError as error:
  if error.code==status:return None
  raise AssertionError(f'{path}: HTTP {error.code}: '+error.read().decode())
try:
 source=args.source/'packages/pockethost/src/mothership-app';migrations=root/'migrations';shutil.copytree(source/'pb_migrations',migrations);hooks=root/'hooks';hooks.mkdir();shutil.copy(source/'pb_hooks/mothership.js',hooks/'mothership.js')
 script='onBootstrap(e=>{e.next();require(`${__hooks}/mothership`).InitializeDashboardTotp()});\nrouterUse(e=>require(`${__hooks}/mothership`).GuardDashboardTotp(e));\n'
 routes=[('GET','/api/instance/{id}/overview','HandleInstanceOverview'),('PUT','/api/instance/{id}','HandleInstanceUpdate'),('GET','/api/instances/metrics','HandleInstancesMetrics'),('GET','/api/instance/{id}/monitoring','HandleInstanceMonitoringGet'),('GET','/api/instance/{id}/backups','HandleInstanceBackupsList'),('GET','/api/instance/{id}/actions','HandleInstanceActionLog'),('POST','/api/instance/{id}/console','HandleInstanceConsoleTicket'),('POST','/api/instance/{id}/console/consume','HandleInstanceConsoleConsume'),('GET','/api/admin/overview','HandleOperatorAdminOverview'),('POST','/api/admin/users','HandleOperatorAdminCreateUser'),('PATCH','/api/admin/users/{id}','HandleOperatorAdminUpdateUser'),('PUT','/api/admin/settings','HandleOperatorAdminUpdateSettings'),('POST','/api/admin/settings/smtp/test','HandleOperatorAdminTestSMTP'),('POST','/api/admin/settings/backup-s3/test','HandleOperatorAdminTestBackupS3'),('GET','/api/admin/disk-cleanup','HandleOperatorAdminDiskCleanupPreview'),('POST','/api/admin/disk-cleanup','HandleOperatorAdminDiskCleanupRun')]
 for method,path,handler in routes:
  script+=f'routerAdd({json.dumps(method)},{json.dumps(path)},e=>{{try{{return require(`${{__hooks}}/mothership`).{handler}(e)}}catch(error){{return e.json(error.status||500,{{message:String(error),data:JSON.parse(JSON.stringify(error))}})}}}},$apis.requireAuth("users"));\n'
 script+='onModelValidate(e=>{if(e.model.tableName()==="_params"){let fail=false;try{$os.stat($os.getenv("DATA_ROOT")+"/reject-settings");fail=true}catch{}if(fail)throw new Error("Simulated native settings validation failure")}e.next()});\n'
 (hooks/'test.pb.js').write_text(script)
 tools=root/'bin';tools.mkdir();docker=tools/'docker';docker.write_text('#!/usr/bin/env python3\nimport pathlib,sys\nif pathlib.Path('+repr(str(root/'docker-fail'))+').exists():sys.exit(1)\nprint("container000001")\n');docker.chmod(0o700)
 env=dict(os.environ,DATA_ROOT=str(root/'data'),APP_URL=base,APEX_DOMAIN='example.invalid',PH_SUPPORT_EMAIL='support@example.invalid');env.pop('PH_SECRET',None);env['PATH']=str(tools)+':'+env['PATH']
 password='Test-'+secrets.token_urlsafe(30);email='admin@example.invalid';db=root/'pb_data'
 subprocess.run([args.pocketbase,'superuser','upsert',email,password,'--dir',str(db),'--hooksDir',str(root/'empty-hooks'),'--migrationsDir',str(migrations)],check=True,capture_output=True,env=env)
 log=(root/'process.log').open('wb');process=subprocess.Popen([args.pocketbase,'serve','--dir',str(db),'--hooksDir',str(hooks),'--migrationsDir',str(migrations),'--http',f'127.0.0.1:{port}'],env=env,stdout=log,stderr=subprocess.STDOUT)
 for _ in range(100):
  if process.poll() is not None:raise RuntimeError('Startup failed: '+(root/'process.log').read_text()[-4000:])
  try:req('/api/health');break
  except OSError:time.sleep(.1)
 su=req('/api/collections/_superusers/auth-with-password',body={'identity':email,'password':password})['token']
 admin=req('/api/collections/users/records',su,{'email':email,'username':'admintest','password':password,'passwordConfirm':password,'verified':True,'superAdmin':True,'subscription':'premium','subscription_quantity':50})
 token=req('/api/collections/users/auth-with-password',body={'identity':email,'password':password})['token']
 initial=req('/api/admin/overview',token)
 assert initial['settings']['smtp']['port']==587,'Missing SMTP_PORT must preserve 587'
 checks.append('default_smtp_port')
 result=req('/api/admin/settings',token,{'notes':'settings-roundtrip','serverTimezone':'Indian/Reunion'},'PUT')
 assert result['settings']['notes']=='settings-roundtrip','JSON body lost or settings not persisted'
 before=req('/api/admin/overview',token)['settings']
 req('/api/admin/settings',token,{'smtp':{'senderAddress':'Display name <bad@example.invalid>'}},'PUT',400)
 assert req('/api/admin/overview',token)['settings']==before
 checks.append('invalid_sender_rejected_without_partial_save')
 settings={'smtp':{'enabled':True,'host':'127.0.0.1','port':smtp.server_address[1],'username':'','password':'','authMethod':'PLAIN','tls':False,'localName':'localhost','senderName':'Test SMTP','senderAddress':'sender@example.invalid'}}
 result=req('/api/admin/settings',token,settings,'PUT');assert result['settings']['smtp']['host']=='127.0.0.1'
 draft=dict(settings['smtp'],senderName='Unsaved SMTP draft')
 result=req('/api/admin/settings/smtp/test',token,{'testEmail':'capture@example.invalid','smtp':draft})
 assert req('/api/admin/overview',token)['settings']['smtp']['senderName']=='Test SMTP'
 checks.append('smtp_test_does_not_save_draft')
 assert len(captured)==1,'Test message not captured locally'
 assert 'password' not in json.dumps(result)
 req('/api/admin/settings/smtp/test',token,{'testEmail':'bad address','smtp':draft},status=400)
 assert len(captured)==1
 checks.append('smtp_target_validation')
 req('/api/admin/settings/smtp/test',token,{'testEmail':'capture@example.invalid','smtp':dict(draft,username='reject',password='dummy')},status=400)
 assert not list(db.glob('.smtp-test-*'))
 checks.append('smtp_auth_rejection_and_private_file_cleanup')
 (root/'data').mkdir(exist_ok=True);(root/'data/reject-settings').touch()
 req('/api/admin/settings',token,{'notes':'must-rollback'},'PUT',500)
 (root/'data/reject-settings').unlink()
 assert req('/api/admin/overview',token)['settings']['notes']=='settings-roundtrip'
 checks.append('native_failure_rolls_back_operator_record')
 literal='  literal secret with spaces  '
 req('/api/admin/settings',token,{'smtp':{'username':'test','password':literal}},'PUT')
 req('/api/admin/settings',token,{'smtp':{'password':''}},'PUT')
 raw=req('/api/collections/settings/records?perPage=100',su)['items'];stored=next(r['value'] for r in raw if r['name']=='operator_settings');stored=json.loads(stored) if isinstance(stored,str) else stored
 assert stored['smtp']['password']==literal
 assert literal not in json.dumps(req('/api/admin/overview',token))
 req('/api/admin/settings',token,{'smtp':{'username':''}},'PUT')
 checks.append('secret_preserved_and_never_exposed')
 req('/api/admin/settings',token,{'backupS3':{'enabled':True}},'PUT',400)
 req('/api/admin/settings',token,{'serverTimezone':'NoSuch/Timezone'},'PUT',400)
 req('/api/admin/settings',token,{'defaultUserQuota':-1},'PUT',400)
 checks.append('s3_timezone_quota_validation')
 created=req('/api/admin/users',token,{'email':'created@example.invalid','password':password,'verified':True,'subscription_quantity':3})['user']
 req('/api/admin/users/'+created['id'],token,{'subscription_quantity':-1},'PATCH',400)
 req('/api/admin/users/'+admin['id'],token,{'superAdmin':False},'PATCH',400)
 req('/api/admin/users/'+admin['id'],token,{'suspension':'blocked'},'PATCH',400)
 checks.append('last_active_admin_protected')
 secondary=req('/api/admin/users',token,{'email':'secondary@example.invalid','password':password,'verified':True,'superAdmin':True})['user']
 secondary_token=req('/api/collections/users/auth-with-password',body={'identity':'secondary@example.invalid','password':password})['token']
 req('/api/admin/users/'+secondary['id'],token,{'suspension':'suspended'},'PATCH')
 req('/api/admin/overview',secondary_token,status=403)
 checks.append('suspended_operator_denied')
 spaced='  TestPasswordWithSpaces  '
 req('/api/admin/users',token,{'email':'spaces@example.invalid','password':spaced,'verified':True})
 req('/api/collections/users/auth-with-password',body={'identity':'spaces@example.invalid','password':spaced})
 checks.append('literal_user_password_preserved')
 edited=req('/api/admin/users/'+created['id'],token,{'subscription_quantity':7,'suspension':'Test suspendu'},'PATCH')['user'];assert edited['subscription_quantity']==7 and edited['suspension']=='Test suspendu'
 normal=req('/api/collections/users/auth-with-password',body={'identity':'created@example.invalid','password':password})['token']
 req('/api/admin/overview',normal,status=403);req('/api/admin/settings',normal,{'notes':'forbidden'},'PUT',403);req('/api/admin/overview',status=401)
 # Only disposable directories are eligible; Docker is simulated for fail-closed tests.
 owned=req('/api/collections/instances/records',su,{'uid':admin['id'],'subdomain':'owned-test','version':'0.39.*','power':False,'status':'idle'})
 # Additional access never changes the owner; exercise actual native rules and dashboard endpoints.
 other=req('/api/collections/instances/records',su,{'uid':admin['id'],'subdomain':'other-test','version':'0.39.*','power':False,'status':'idle'})
 shared=req('/api/admin/users',token,{'email':'shared@example.invalid','password':password,'verified':True,'managedInstances':[owned['id'],owned['id']]})['user']
 assert shared['managedInstances']==[owned['id']]
 shared_token=req('/api/collections/users/auth-with-password',body={'identity':shared['email'],'password':password})['token']
 assert {r['id'] for r in req('/api/collections/instances/records',shared_token)['items']}=={owned['id']}
 req('/api/collections/instances/records/'+other['id'],shared_token,status=404)
 assert req('/api/collections/instances/records/'+owned['id'],su)['uid']==admin['id']
 overview=req('/api/admin/overview',token)
 assert {r['id'] for r in overview['instances']}=={owned['id'],other['id']}
 assert set(overview['instances'][0])=={'id','subdomain','uid'}
 checks.append('create_shared_access_without_owner_change_or_secret_exposure')
 api='/api/instance/'+owned['id']
 for endpoint in ['/overview','/monitoring','/backups','/actions']:req(api+endpoint,shared_token)
 assert set(req('/api/instances/metrics',shared_token)['instances'])=={owned['id']}, req('/api/instances/metrics',shared_token)
 req(api,shared_token,{'fields':{'power':True}},'PUT')
 ticket=req(api+'/console',shared_token,{})['ticket']
 assert req(api+'/console/consume',shared_token,{'ticket':ticket})['user']==admin['id']
 req('/api/instance/'+other['id']+'/console',shared_token,{},status=403)
 ticket=req(api+'/console',shared_token,{})['ticket']
 checks.append('attached_base_metrics_settings_backups_monitoring_and_console')
 # Omitted access is preserved; explicit empty access revokes immediately for the existing session and pending ticket.
 assert req('/api/admin/users/'+shared['id'],token,{'subscription_quantity':4},'PATCH')['user']['managedInstances']==[owned['id']]
 req('/api/admin/users/'+shared['id'],token,{'managedInstances':[]},'PATCH')
 assert not req('/api/collections/instances/records',shared_token)['items']
 req(api+'/console',shared_token,{},status=403)
 req(api+'/console/consume',shared_token,{'ticket':ticket},status=403)
 req(api,shared_token,{'fields':{'power':False}},'PUT',400)
 req(api,token,{'fields':{'power':False}},'PUT')
 checks.append('revocation_applies_to_existing_session_and_console_ticket')
 global_user=req('/api/admin/users',token,{'email':'global@example.invalid','password':password,'verified':True,'superAdmin':True,'managedInstances':[owned['id']]})['user']
 global_token=req('/api/collections/users/auth-with-password',body={'identity':global_user['email'],'password':password})['token']
 assert global_user['superAdmin'] is True
 assert len(req('/api/collections/instances/records',global_token)['items'])==2
 req('/api/admin/users/'+global_user['id'],token,{'superAdmin':False},'PATCH')
 assert {r['id'] for r in req('/api/collections/instances/records',global_token)['items']}=={owned['id']}
 req('/api/admin/users/'+global_user['id'],token,{'suspension':'blocked'},'PATCH')
 assert not req('/api/collections/instances/records',global_token)['items']
 req(api+'/console',global_token,{},status=403)
 checks.append('superuser_global_access_demotion_and_suspension')
 for value in ['invalid',None,[1],['not-a-record'],['x'*15]]:
  req('/api/admin/users',token,{'email':'invalid-access@example.invalid','password':password,'managedInstances':value},status=400)
 assert not req('/api/collections/users/records?filter=email%3D%22invalid-access%40example.invalid%22',su)['items']
 req('/api/admin/users/'+shared['id'],shared_token,{'managedInstances':[other['id']]},'PATCH',403)
 req('/api/collections/users/records/'+shared['id'],shared_token,{'managedInstances':[other['id']],'superAdmin':True},'PATCH',403)
 for grants in [{'superAdmin':True},{'managedInstances':[owned['id']]},{'managedInstances+':[owned['id']]}]:
  req('/api/collections/users/records',body={'email':'escalate@example.invalid','username':'escalate','password':password,'passwordConfirm':password,**grants},status=400)
 checks.append('invalid_grants_atomic_and_privilege_escalation_denied')
 for ident in [owned['id'],'container000001','orphan000000001']:
  folder=root/'data/instances'/ident/'pb_data';folder.mkdir(parents=True);(folder/'keep.txt').write_text('fixture')
 preview=req('/api/admin/disk-cleanup',token)['cleanup'];assert preview['orphanCount']==1
 req('/api/admin/disk-cleanup',token,{},status=400)
 extra=root/'data/instances/orphan000000002/pb_data';extra.mkdir(parents=True);(extra/'keep.txt').write_text('fixture')
 req('/api/admin/disk-cleanup',token,{'snapshot':preview['snapshot']},status=400)
 (root/'docker-fail').touch();req('/api/admin/disk-cleanup',token,status=400);req('/api/admin/disk-cleanup',token,{'snapshot':preview['snapshot']},status=400);(root/'docker-fail').unlink()
 assert (root/'data/instances/orphan000000001').exists()
 fresh=req('/api/admin/disk-cleanup',token)['cleanup'];clean=req('/api/admin/disk-cleanup',token,{'snapshot':fresh['snapshot']})['cleanup'];assert clean['removedCount']==2
 assert (root/'data/instances'/owned['id']).exists() and (root/'data/instances/container000001').exists()
 checks.append('cleanup_snapshot_recheck_and_docker_failure')
 print(json.dumps({'settings_roundtrip':'passed','smtp_local_capture':'passed','user_create_update':'passed','permissions':'passed','disk_cleanup':'passed','checks':checks}))
finally:
 if process:
  process.terminate()
  try:process.wait(timeout=10)
  except subprocess.TimeoutExpired:process.kill();process.wait()
 smtp.shutdown();smtp.server_close()
 if 'log' in locals():log.close()
 shutil.rmtree(root)
