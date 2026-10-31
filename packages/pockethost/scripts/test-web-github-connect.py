#!/usr/bin/env python3
"""Actual disposable PocketBase: GitHub manifest states, encrypted pending setup and atomic activation."""
import argparse,pathlib,tempfile,subprocess,json,os,urllib.request,urllib.error,secrets,socket,shutil,time,sys,sqlite3,re
p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=pathlib.Path);p.add_argument('--pocketbase',required=True);args=p.parse_args()
os.umask(0o077);root=pathlib.Path(tempfile.mkdtemp(prefix='ph-github-connect-test-')).resolve();process=None;checks=[]
with socket.socket()as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
base=f'http://127.0.0.1:{port}';source=args.source/'packages/pockethost/src/mothership-app'
def req(path,t='',body=None,method=None,status=200):
 request=urllib.request.Request(base+path,method=method or('POST'if body is not None else'GET'),headers={'Content-Type':'application/json',**({'Authorization':t}if t else{})},data=None if body is None else json.dumps(body).encode())
 try:
  with urllib.request.urlopen(request,timeout=20)as r:
   raw=r.read();assert r.status==status,(path,r.status,status);return json.loads(raw)if raw else None
 except urllib.error.HTTPError as e:
  body=e.read().decode()
  if e.code==status:return json.loads(body)if body else None
  raise AssertionError(f'{path}: HTTP{e.code} '+body)
def dbrow(table,ident):
 with sqlite3.connect(root/'pb_data/data.db')as db:
  db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM '+table+' WHERE id=?',(ident,)).fetchone();return dict(row)if row else None
def activecipher():
 with sqlite3.connect(root/'pb_data/data.db')as db:
  row=db.execute('SELECT valueEncrypted FROM web_app_settings WHERE key="github"').fetchone();return row[0]if row else None
# Le remplacement de la connexion principale est explicite (sans mode, l'assistant AJOUTE une connexion).
def start(t,**body):return req('/api/web-apps/github/connect/start',t,{'mode':'replace','connectionId':'default',**body},status=201)
def convert(s,t,code='a'*40,status=200):return req('/api/web-apps/github/connect/manifest',t,{'state':s['state'],'code':code},status=status)
def finish(s,t,ident='123',status=200):return req('/api/web-apps/github/connect/install',t,{'state':s['state'],'installationId':ident},status=status)
def install_retry(session,t):return req('/api/web-apps/github/connect/install/start',t,{'sessionId':session})
def no_secrets(value):
 text=json.dumps(value)
 for secret in ['PENDING-PEM-SECRET','PENDING-WEBHOOK-SECRET','UNUSED-OAUTH-CLIENT-SECRET','ACTIVE-PRIVATE-KEY','ACTIVE-WEBHOOK-SECRET']:
  assert secret not in text,secret
try:
 hooks=root/'hooks';hooks.mkdir();shutil.copy(source/'pb_hooks/mothership.js',hooks/'mothership.js')
 routes=[('POST','/github/connect/start','Start'),('POST','/github/connect/manifest','Manifest'),('POST','/github/connect/install/start','InstallStart'),('POST','/github/connect/install','Install'),('POST','/github/connect/install/check','InstallCheck'),('GET','/github/connect/status','Status')]
 script=''
 for method,path,name in routes:script+=f'routerAdd({json.dumps(method)},{json.dumps("/api/web-apps"+path)},e=>require(`${{__hooks}}/mothership`).HandleWebGithubConnect{name}(e),$apis.requireAuth("users"));\n'
 for method,path,handler in [('GET','/api/web-apps/settings','HandleWebSettingsGet'),('PUT','/api/web-apps/settings','HandleWebSettingsPut')]:script+=f'routerAdd({json.dumps(method)},{json.dumps(path)},e=>require(`${{__hooks}}/mothership`).{handler}(e),$apis.requireAuth("users"));\n'
 script+="routerAdd('POST','/test/maintenance',e=>{require(`${__hooks}/mothership`).HandleWebGithubConnectMaintenance();return e.json(200,{ok:true})});\n"
 script+="onRecordUpdate(e=>{let reject=false;try{$os.stat($os.getenv('DATA_ROOT')+'/reject-connection');reject=true}catch{}if(reject&&e.record.getString('status')==='completed')throw new BadRequestError('Simulated connection commit failure');e.next()},'web_github_setup_sessions');\n"
 (hooks/'test.pb.js').write_text(script)
 tools=root/'tools';tools.mkdir();shim=tools/'python3'
 shim.write_text('#!'+sys.executable+'\n'+r'''
import sys,json,pathlib,os
p=pathlib.Path(sys.argv[-1])
try:d=json.loads(p.read_text())
except Exception:d={}
a=d.get('action')
if a in ['convert','verify-installation','discover-installation']:
 p.unlink()
 if a=='convert':
  if d['code']=='b'*40:out={'error':'Le code GitHub est expiré.'}
  else:out={'result':{'id':42,'slug':'pockethost-test','owner':{'id':77,'login':'test-owner'},'pem':'-----BEGIN RSA PRIVATE KEY-----\nPENDING-PEM-SECRET\n-----END RSA PRIVATE KEY-----','webhook_secret':'PENDING-WEBHOOK-SECRET-1234567890','client_id':'unused-client','client_secret':'UNUSED-OAUTH-CLIENT-SECRET','permissions':{'contents':'read','metadata':'read'},'events':['push']}}
 else:
  ident=d.get('installationId','123')
  if ident=='999':out={'error':'Installation inconnue.'}
  else:out={'result':{'installationId':ident,'accountId':'88'if ident=='998'else'77','account':'test-owner','repositoryCount':0 if ident=='997'else 3}}
 print(json.dumps(out));sys.exit()
os.execv(REAL,[REAL]+sys.argv[1:])
'''.replace('REAL',repr(sys.executable)))
 shim.chmod(0o700)
 env={**os.environ,'PATH':str(tools)+':'+os.environ['PATH'],'WEB_APPS_ENABLED':'true','WEB_APPS_MASTER_KEY':'a'*32,'WEB_APPS_ROOT':str(root/'web'),'DATA_ROOT':str(root/'data'),'APP_HOST':'app.example.invalid','APEX_DOMAIN':'example.invalid'}
 password='P'+secrets.token_urlsafe(32);email='operator@example.invalid';db=root/'pb_data'
 subprocess.run([args.pocketbase,'superuser','upsert',email,password,'--dir',str(db),'--hooksDir',str(root/'empty-hooks'),'--migrationsDir',str(source/'pb_migrations')],env=env,check=True,capture_output=True)
 log=(root/'process.log').open('wb');process=subprocess.Popen([args.pocketbase,'serve','--dir',str(db),'--hooksDir',str(hooks),'--migrationsDir',str(source/'pb_migrations'),'--http',f'127.0.0.1:{port}'],env=env,stdout=log,stderr=subprocess.STDOUT)
 for _ in range(100):
  if process.poll()is not None:raise RuntimeError('Startup failed: '+(root/'process.log').read_text()[-2500:])
  try:req('/api/health');break
  except OSError:time.sleep(.1)
 su=req('/api/collections/_superusers/auth-with-password',body={'identity':email,'password':password})['token']
 def user(label,admin):
  mail=label+'@example.invalid';u=req('/api/collections/users/records',su,{'email':mail,'username':label,'password':password,'passwordConfirm':password,'verified':True,'superAdmin':admin,'subscription':'premium','subscription_quantity':1})
  token=req('/api/collections/users/auth-with-password',body={'identity':mail,'password':password})['token'];return u,token
 op,t=user('connectadmin',True);other,ot=user('anotheradmin',True);ordinary,nt=user('ordinary',False)
 req('/api/web-apps/github/connect/start',body={},status=401);req('/api/web-apps/github/connect/start',nt,{},status=403);req('/api/collections/web_github_setup_sessions/records',t,status=403)
 req('/api/web-apps/github/connect/start',t,{'organization':'../bad'},status=400)
 req('/api/web-apps/settings',t,{'githubAppId':'1','githubAppSlug':'existing-app','githubInstallationId':'2','privateKey':'-----BEGIN RSA PRIVATE KEY-----\nACTIVE-PRIVATE-KEY','webhookSecret':'ACTIVE-WEBHOOK-SECRET-1234567890123456'},'PUT');original=activecipher();assert original
 # Sans mode : ajout d'une connexion (URL de webhook dédiée), la principale n'est pas touchée.
 added=req('/api/web-apps/github/connect/start',t,{},status=201);hook=added['manifest']['hook_attributes']['url']
 assert added['mode']=='add'and re.fullmatch(r'[a-z0-9]{15}',added['connectionId'])and hook=='https://app.example.invalid/api/web-apps/github/webhook/'+added['connectionId'];assert activecipher()==original
 checks.append('administrator_access_locked_collection_and_existing_connection')
 s=start(t);assert s['mode']=='replace'and s['connectionId']=='default'and s['manifest']['hook_attributes']['url']=='https://app.example.invalid/api/web-apps/github/webhook';assert s['manifest']['public']is False and s['manifest']['default_permissions']=={'contents':'read','metadata':'read','pull_requests':'read'}and s['manifest']['default_events']==['push','pull_request'];assert s['manifest']['redirect_url']=='https://app.example.invalid/applications/settings/github/manifest';assert s['createUrl'].startswith('https://github.com/settings/apps/new?state=')
 raw=dbrow('web_github_setup_sessions',s['sessionId']);assert s['state']not in json.dumps(raw)and len(raw['manifestStateHash'])==64
 convert(s,ot,status=403);assert dbrow('web_github_setup_sessions',s['sessionId'])['status']=='created';assert req('/api/web-apps/github/connect/status',ot)['pending']is None
 convert(s,t,code='not-valid',status=400);m=convert(s,t);no_secrets(m);assert m['app']=={'id':'42','slug':'pockethost-test','owner':'test-owner'};assert activecipher()==original
 raw=dbrow('web_github_setup_sessions',s['sessionId']);assert 'PENDING-PEM-SECRET'not in raw['pendingEncrypted']and raw['pendingEncrypted'];assert m['state']!=s['state'];convert(s,t,status=400)
 checks.append('manifest_state_actor_binding_single_use_encrypted_pending_no_active_overwrite')
 # A manifest registered under another organization must not become an accepted pending App.
 mismatch=start(ot,organization='different-org');assert '/organizations/different-org/' in mismatch['createUrl'];convert(mismatch,ot,status=400);assert activecipher()==original
 org=start(ot,organization='test-owner');assert '/organizations/test-owner/' in org['createUrl'];org_m=convert(org,ot);no_secrets(org_m);assert org_m['app']['owner']=='test-owner'

 finish(m,t,ident='bad',status=400);finish(m,ot,status=403);finish(m,t,ident='999',status=400);assert activecipher()==original;finish(m,t,status=400)
 i=install_retry(s['sessionId'],t);finish(i,t,ident='998',status=400);assert activecipher()==original
 i=install_retry(s['sessionId'],t);finish(i,t,ident='997',status=400);assert activecipher()==original
 i=install_retry(s['sessionId'],t);no_secrets(req('/api/web-apps/github/connect/status',t))
 (root/'data').mkdir(exist_ok=True);(root/'data/reject-connection').touch();finish(i,t,status=400);assert activecipher()==original
 assert dbrow('web_github_setup_sessions',s['sessionId'])['status']=='installation_failed';(root/'data/reject-connection').unlink()
 i=install_retry(s['sessionId'],t);done=finish(i,t);assert done['connected']and done['repositoryCount']==3;no_secrets(done)
 assert activecipher()!=original;raw=dbrow('web_github_setup_sessions',s['sessionId']);assert raw['status']=='completed'and raw['pendingEncrypted']==''and raw['installStateHash']==''
 no_secrets(req('/api/web-apps/settings',t));finish(i,t,status=400)
 checks.append('installation_identity_replay_no_repository_and_atomic_commit_failure_protection')
 # Pending credentials cannot overwrite a different administrator's newer manual configuration.
 s=start(t);m=convert(s,t)
 req('/api/web-apps/settings',ot,{'githubAppId':'11','githubAppSlug':'newer-active','githubInstallationId':'22'},'PUT');newer=activecipher();finish(m,t,status=400);assert activecipher()==newer
 checks.append('concurrent_active_connection_change_preserved')
 s=start(t);m=convert(s,t);req('/api/web-apps/github/connect/install/check',ot,{'sessionId':s['sessionId']},status=403)
 done=req('/api/web-apps/github/connect/install/check',t,{'sessionId':s['sessionId']});assert done['connected'];finish(m,t,status=400);newer=activecipher()
 req('/api/web-apps/github/connect/install/check',t,{'sessionId':s['sessionId']},status=400)
 checks.append('installed_app_recovery_actor_binding_and_callback_invalidation')
 s=start(t);req('/api/collections/web_github_setup_sessions/records/'+s['sessionId'],su,{'expiresAt':'2020-01-01T00:00:00Z'},'PATCH');convert(s,t,status=400);assert activecipher()==newer
 s=start(t);m=convert(s,t);new=start(t);finish(m,t,status=400);assert dbrow('web_github_setup_sessions',s['sessionId'])['status']=='cancelled'
 s=start(t);convert(s,t,code='b'*40,status=400);assert dbrow('web_github_setup_sessions',s['sessionId'])['status']=='manifest_failed';convert(s,t,status=400)
 checks.append('expiry_restart_cancellation_and_failed_code_consumption')
 req('/api/collections/web_github_setup_sessions/records/'+s['sessionId'],su,{'expiresAt':'2020-01-01T00:00:00Z'},'PATCH');fresh=start(ot);req('/test/maintenance',body={});assert dbrow('web_github_setup_sessions',s['sessionId'])is None;assert dbrow('web_github_setup_sessions',fresh['sessionId'])['status']=='created';assert req('/api/web-apps/github/connect/status',ot)['pending']['sessionId']==fresh['sessionId'];assert activecipher()==newer
 assert not list(db.glob('.web-github-connect-*'))
 checks.append('expired_pending_cleanup_and_private_payload_removal')
 print(json.dumps({'passed':True,'checks':checks}))
except:
 if(root/'process.log').exists():print((root/'process.log').read_text()[-2500:],file=sys.stderr)
 raise
finally:
 if process:
  process.terminate()
  try:process.wait(timeout=5)
  except subprocess.TimeoutExpired:process.kill();process.wait()
 shutil.rmtree(root)
