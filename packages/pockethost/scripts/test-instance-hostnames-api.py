#!/usr/bin/env python3
"""Disposable native API checks for stable instance URLs. Never starts Docker, mail or a production controller."""
import argparse,hashlib,hmac,json,os,pathlib,secrets,shutil,socket,subprocess,tempfile,time,urllib.error,urllib.request
parser=argparse.ArgumentParser();parser.add_argument('--source',required=True,type=pathlib.Path);parser.add_argument('--pocketbase',required=True);args=parser.parse_args()
os.umask(0o077);root=pathlib.Path(tempfile.mkdtemp(prefix='ph-hostnames-api-')).resolve();process=None;completed=False;checks=[]
with socket.socket()as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
base='http://127.0.0.1:'+str(port)
def api(path,token='',body=None,method=None,status=200):
 req=urllib.request.Request(base+path,headers={'Content-Type':'application/json',**({'Authorization':token}if token else{})},data=None if body is None else json.dumps(body).encode(),method=method or('POST'if body is not None else'GET'))
 try:
  with urllib.request.urlopen(req,timeout=15)as response:
   assert response.status==status,(path,response.status,status);raw=response.read();return json.loads(raw)if raw else None
 except urllib.error.HTTPError as error:
  value=json.loads(error.read())
  if error.code!=status:raise AssertionError((path,error.code,status,value))from None
  return value
try:
 source=args.source/'packages/pockethost/src/mothership-app';hooks=root/'hooks';migrations=root/'migrations';hooks.mkdir();migrations.mkdir()
 shutil.copyfile(source/'pb_hooks/mothership.js',hooks/'mothership.js')
 for file in(source/'pb_migrations').glob('*.js'):
  if file.name.split('_')[0].isdigit()and int(file.name.split('_')[0])<=1793272000:shutil.copyfile(file,migrations/file.name)
 (hooks/'hostnames.pb.js').write_text("""
 onRecordCreateRequest(e=>require(`${__hooks}/mothership`).GuardInstanceHostnamesRequest(e),'instances');
 onRecordUpdateRequest(e=>require(`${__hooks}/mothership`).GuardInstanceHostnamesRequest(e),'instances');
 onRecordCreate(e=>require(`${__hooks}/mothership`).ValidateInstanceHostnames(e),'instances');
 onRecordUpdate(e=>require(`${__hooks}/mothership`).ValidateInstanceHostnames(e),'instances');
 routerAdd('PUT','/api/instance/{id}',e=>require(`${__hooks}/mothership`).HandleInstanceUpdate(e),$apis.requireAuth('users'));
 routerAdd('GET','/api/instance/{id}/console',e=>require(`${__hooks}/mothership`).HandleInstanceConsoleInfo(e));
 routerAdd('POST','/api/web-apps/github/webhook',e=>require(`${__hooks}/mothership`).HandleWebGithubWebhook(e));
 routerAdd('POST','/api/web-apps/github/webhook/{connectionId}',e=>require(`${__hooks}/mothership`).HandleWebGithubConnectionWebhook(e));
 routerAdd('POST','/__test/github-settings',e=>{
  for (const [key,secret] of [['github','synthetic-secret-connection-a'],['github:50a164e0ef1c85e','synthetic-secret-connection-b']]) {
   const row=new Record($app.findCollectionByNameOrId('web_app_settings'));row.set('key',key);row.set('valueEncrypted',$security.encrypt(JSON.stringify({webhookSecret:secret,githubInstallationId:'123'}),$os.getenv('WEB_APPS_MASTER_KEY')));$app.save(row);
  }return e.json(200,{ok:true});
 },$apis.requireAuth('_superusers'));
 routerAdd('POST','/api/web-apps/{id}/domains',e=>require(`${__hooks}/mothership`).HandleWebDomainCreate(e),$apis.requireAuth('users'));
 routerAdd('POST','/api/web-apps/{id}/domains/{domainId}/verify',e=>require(`${__hooks}/mothership`).HandleWebDomainVerify(e),$apis.requireAuth('users'));
 """)
 env={**os.environ,'APEX_DOMAIN':'app4.example.test','APP_HOST':'app4.example.test','APP_URL':'https://app4.example.test','WEB_APPS_ENABLED':'true','WEB_APPS_PUBLIC_IP':'192.0.2.55','WEB_APPS_ROOT':str(root/'web'),'WEB_APPS_MASTER_KEY':'h'*32}
 (root/'web').mkdir();(root/'web/worker-status.json').write_text('{"httpsReady":true}')
 password='P'+secrets.token_urlsafe(30);email='root@example.invalid';data=root/'data'
 subprocess.run([args.pocketbase,'superuser','upsert',email,password,'--dir',str(data),'--hooksDir',str(root/'empty'),'--migrationsDir',str(migrations)],env=env,capture_output=True,check=True)
 output=(root/'server.log').open('wb');process=subprocess.Popen([args.pocketbase,'serve','--http','127.0.0.1:'+str(port),'--dir',str(data),'--hooksDir',str(hooks),'--migrationsDir',str(migrations)],env=env,stdout=output,stderr=subprocess.STDOUT)
 for _ in range(100):
  if process.poll()is not None:raise RuntimeError((root/'server.log').read_text()[-4000:])
  try:api('/api/health');break
  except OSError:time.sleep(.1)
 su=api('/api/collections/_superusers/auth-with-password',body={'identity':email,'password':password})['token']
 def user(name,platform):
  record=api('/api/collections/users/records',su,{'email':name+'@example.invalid','username':name,'verified':True,'password':password,'passwordConfirm':password,'superAdmin':platform,'subscription':'premium','subscription_quantity':20})
  return record,api('/api/collections/users/auth-with-password',body={'identity':record['email'],'password':password})['token']
 owner,owner_token=user('owner',False);operator,operator_token=user('operator',True)
 # Explicitly relax only this disposable collection API rule so the guards themselves are exercised.
 collection=api('/api/collections/instances',su);api('/api/collections/instances',su,{'createRule':'@request.auth.id != ""','updateRule':'@request.auth.id != ""'},'PATCH')
 def create(name,**values):return api('/api/collections/instances/records',su,{'subdomain':name,'uid':owner['id'],'version':'0.39.11','power':False,'status':'idle',**values})
 def patch(record,values,token=su,status=200):return api('/api/collections/instances/records/'+record['id'],token,values,'PATCH',status)
 one=create('client-one',publicHostname='CLIENT.ONE-OLD.EXAMPLE.TEST.',legacyHostnames=['ID.ONE-OLD.EXAMPLE.TEST.','client.one-old.example.test'])
 two=create('client-two',publicHostname='client.two-old.example.test',legacyHostnames=['id.two-old.example.test'])
 assert one['publicHostname']=='client.one-old.example.test'and one['legacyHostnames']==['client.one-old.example.test','id.one-old.example.test']
 read=api('/api/collections/instances/records/'+one['id'],owner_token);assert read['publicHostname']==one['publicHostname']and read['legacyHostnames']==one['legacyHostnames']
 checks.append('normalization_and_owner_read_access')
 patch(one,{'publicHostname':'hijack.example.test'},owner_token,403)
 patch(one,{'legacyHostnames':['hijack.example.test']},owner_token,403)
 api('/api/collections/instances/records',owner_token,{'subdomain':'owner-new','uid':owner['id'],'version':'0.39.11','publicHostname':'hijack.example.test'},status=403)
 api('/api/instance/'+one['id'],owner_token,{'fields':{'publicHostname':'hijack.example.test'}},'PUT',403)
 patch(one,{'power':False},owner_token)
 patch(one,{'publicHostname':'client.one-old.example.test'},operator_token)
 api('/api/instance/'+one['id'],operator_token,{'fields':{'publicHostname':'client.one-old.example.test','legacyHostnames':one['legacyHostnames']}},'PUT')
 checks.append('platform_only_native_create_update_and_custom_api_guard_without_blocking_ordinary_owner_changes')
 for values in [{'publicHostname':two['publicHostname']},{'legacyHostnames':two['legacyHostnames']},{'cname':two['publicHostname']},{'legacyHostnames':[two['id']+'.app4.example.test']},{'publicHostname':'app4.example.test'}]:patch(one,values,status=400)
 api('/api/instance/'+one['id'],owner_token,{'fields':{'cname':two['publicHostname']}},'PUT',400)
 for bad in ['https://evil.test','*.evil.test','evil.test:443','evil.test/path']:
  patch(one,{'legacyHostnames':[bad]},status=400)
 patch(one,{'legacyHostnames':['alias'+str(i)+'.example.test'for i in range(17)]},status=400)
 checks.append('bidirectional_alias_cname_canonical_and_reserved_host_collisions_plus_invalid_hosts')
 patch(one,{'cname':'custom.client.example.test'})
 assert api('/api/instance/'+one['id']+'/console')['url']=='https://custom.client.example.test/_/ph-auth'
 patch(one,{'cname':''})
 assert api('/api/instance/'+one['id']+'/console')['url']=='https://client.one-old.example.test/_/ph-auth'
 legacy=create('legacy-node')
 assert api('/api/instance/'+legacy['id']+'/console')['url']=='https://legacy-node.app4.example.test/_/ph-auth'
 checks.append('console_uses_stable_primary_with_revocable_cname_and_legacy_node_fallback')
 app=api('/api/collections/web_apps/records',su,{'name':'Alias fixture','uid':operator['id'],'status':'stopped','config':{'generatedDomain':'web-fixture.app4.example.test','repository':'','branch':'main'}})
 for host in ['monappli.re','app.monappli.re','app2.monappli.re','app4.monappli.re','pockethost-central.monappli.re','pockethost-central.app2.monappli.re',one['publicHostname'],two['publicHostname'],*one['legacyHostnames'],one['id']+'.app4.example.test']:
  result=api('/api/web-apps/'+app['id']+'/domains',operator_token,{'hostname':host},status=400)
  assert result['message']=='Ce domaine n’est pas disponible.'
 patch(one,{'cname':'revocable.custom.example.test'})
 api('/api/web-apps/'+app['id']+'/domains',operator_token,{'hostname':'revocable.custom.example.test'},status=400)
 patch(one,{'cname':''})
 api('/api/web-apps/'+app['id']+'/domains',operator_token,{'hostname':'revocable.custom.example.test'},status=201)
 domain=api('/api/collections/web_app_domains/records',su,{'appId':app['id'],'host':'claimed.web.example.test','status':'active','ownershipToken':'a'*40})
 patch(one,{'legacyHostnames':['claimed.web.example.test']},status=400)
 patch(one,{'publicHostname':'web-fixture.app4.example.test'},status=400)
 pending=api('/api/web-apps/'+app['id']+'/domains',operator_token,{'hostname':'pending.web.example.test'},status=201)['domain']
 patch(one,{'legacyHostnames':[*one['legacyHostnames'],'pending.web.example.test']})
 result=api('/api/web-apps/'+app['id']+'/domains/'+pending['id']+'/verify',operator_token,{},status=400)
 assert result['message']=='Ce domaine n’est pas disponible.'
 checks.append('web_claims_cannot_take_public_legacy_or_canonical_database_hosts_and_pending_claim_rechecks_ownership')
 api('/__test/github-settings',su,{})
 raw=' { "installation": {"id": 123}, "text": "é raw bytes" }\n'.encode()
 def github(path,secret,status):
  signature='sha256='+hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
  req=urllib.request.Request(base+path,method='POST',data=raw,headers={'Content-Type':'application/json','X-Hub-Signature-256':signature,'X-GitHub-Delivery':secrets.token_hex(12),'X-GitHub-Event':'ping'})
  try:
   with urllib.request.urlopen(req,timeout=15)as response:assert response.status==status,(path,response.status,status)
  except urllib.error.HTTPError as error:
   if error.code!=status:raise AssertionError((path,error.code,status))from None
 github('/api/web-apps/github/webhook','synthetic-secret-connection-a',200)
 github('/api/web-apps/github/webhook/50a164e0ef1c85e','synthetic-secret-connection-b',200)
 github('/api/web-apps/github/webhook','synthetic-secret-connection-b',401)
 github('/api/web-apps/github/webhook/50a164e0ef1c85e','synthetic-secret-connection-a',401)
 checks.append('native_github_default_and_imported_connection_verify_only_their_own_raw_body_hmac')
 completed=True;print(json.dumps({'ok':True,'checks':checks},indent=2))
finally:
 if process:
  process.terminate()
  try:process.wait(timeout=10)
  except subprocess.TimeoutExpired:process.kill();process.wait()
 if completed:shutil.rmtree(root)
 else:
  print('Disposable fixture retained:',root)
  if(root/'server.log').exists():print((root/'server.log').read_text()[-5000:])
