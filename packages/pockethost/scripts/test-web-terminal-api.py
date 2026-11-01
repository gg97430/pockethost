#!/usr/bin/env python3
"""Disposable PocketBase integration for terminal grants; no Docker or remote writes."""
import argparse,pathlib,tempfile,subprocess,json,os,urllib.request,urllib.error,secrets,socket,shutil,time,sqlite3,datetime
p=argparse.ArgumentParser();p.add_argument('--source',type=pathlib.Path,required=True);p.add_argument('--pocketbase',required=True);args=p.parse_args()
os.umask(0o077);root=pathlib.Path(tempfile.mkdtemp(prefix='ph-terminal-api-')).resolve();process=None;checks=[]
with socket.socket()as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
base=f'http://127.0.0.1:{port}';secret=secrets.token_urlsafe(40)
def req(path,t='',body=None,method=None,status=200,broker=False):
 request=urllib.request.Request(base+path,method=method or('POST'if body is not None else'GET'),headers={'Content-Type':'application/json',**({'Authorization':t}if t else{}),**({'X-Web-Worker':secret}if broker else{})},data=None if body is None else json.dumps(body).encode())
 try:
  with urllib.request.urlopen(request,timeout=15)as r:
   data=r.read();assert r.status==status,(path,r.status,status);return json.loads(data)if data else None
 except urllib.error.HTTPError as e:
  if e.code==status:return None
  raise AssertionError(f'{path}: {e.code} '+e.read().decode())
def private(path,body,status=200):return req('/api/web-apps/_terminal/'+path,body=body,broker=True,status=status)
try:
 source=args.source/'packages/pockethost/src/mothership-app';hooks=root/'hooks';hooks.mkdir();shutil.copy(source/'pb_hooks/mothership.js',hooks/'mothership.js')
 routes=[('POST','/api/web-apps','HandleWebAppCreate',True),('POST','/api/web-apps/{id}/terminal/session','HandleWebTerminalSession',True),('GET','/api/web-apps/{id}/terminal/sessions','HandleWebTerminalHistory',True),('POST','/api/web-apps/{id}/terminal/sessions/{sessionId}/close','HandleWebTerminalRevoke',True),('POST','/api/web-apps/_terminal/claim','HandleWebTerminalClaim',False),('POST','/api/web-apps/_terminal/validate','HandleWebTerminalValidate',False),('POST','/api/web-apps/_terminal/close','HandleWebTerminalClose',False)]
 (hooks/'terminal.pb.js').write_text('\n'.join('routerAdd('+json.dumps(method)+','+json.dumps(path)+',e=>require(`${__hooks}/mothership`).'+handler+'(e)'+(',$apis.requireAuth("users")'if auth else'')+');'for method,path,handler,auth in routes))
 env={**os.environ,'WEB_APPS_ENABLED':'true','WEB_APPS_MASTER_KEY':'a'*32,'WEB_APPS_WORKER_SECRET':secret,'WEB_APPS_ROOT':str(root/'web'),'APEX_DOMAIN':'example.invalid','DATA_ROOT':str(root/'data')}
 password='P'+secrets.token_urlsafe(32);email='operator@example.invalid';db=root/'pb_data'
 subprocess.run([args.pocketbase,'superuser','upsert',email,password,'--dir',str(db),'--hooksDir',str(root/'empty'),'--migrationsDir',str(source/'pb_migrations')],env=env,check=True,capture_output=True)
 log=(root/'process.log').open('wb');process=subprocess.Popen([args.pocketbase,'serve','--dir',str(db),'--hooksDir',str(hooks),'--migrationsDir',str(source/'pb_migrations'),'--http',f'127.0.0.1:{port}'],env=env,stdout=log,stderr=subprocess.STDOUT)
 for _ in range(100):
  if process.poll()is not None:raise RuntimeError((root/'process.log').read_text()[-2000:])
  try:req('/api/health');break
  except OSError:time.sleep(.1)
 su=req('/api/collections/_superusers/auth-with-password',body={'identity':email,'password':password})['token']
 user=req('/api/collections/users/records',su,{'email':email,'username':'terminaladmin','password':password,'passwordConfirm':password,'verified':True,'superAdmin':False,'subscription':'premium','subscription_quantity':1},status=200)
 token=req('/api/collections/users/auth-with-password',body={'identity':email,'password':password})['token']
 req('/api/web-apps/'+'a'*15+'/terminal/session',body={},status=401);req('/api/web-apps/'+'a'*15+'/terminal/session',token,{},status=404)
 req('/api/collections/web_terminal_sessions/records',token,status=403)
 req('/api/collections/users/records/'+user['id'],su,{'superAdmin':True},'PATCH');token=req('/api/collections/users/auth-with-password',body={'identity':email,'password':password})['token']
 app=req('/api/web-apps',token,{'name':'Terminal sample','repository':'owner/repo'},status=201)['app'];ident=app['id'];path='/api/web-apps/'+ident+'/terminal/session'
 req(path,token,{},status=400)
 job=req('/api/collections/web_deployments/records',su,{'appId':ident,'action':'deploy','status':'ready'});dep=job['id'];container='ph-web-'+ident+'-'+dep
 req('/api/collections/web_deployments/records/'+dep,su,{'result':{'container':container,'image':'ph-web/'+ident+':'+dep}},'PATCH')
 req('/api/collections/web_apps/records/'+ident,su,{'activeDeployment':dep,'status':'running'},'PATCH')
 req(path,token,{'container':'mothership'},status=400);req(path,token,{'shell':'/bin/sh'},status=400)
 checks.append('roles_raw_collection_options_and_publication_boundaries')
 issued=req(path,token,{'shell':'sh'},status=201);assert len(issued['token'])==80 and issued['wsPath']=='/api/web-terminal/connect'
 second=req(path,token,{},status=201);req(path,token,{},status=400)
 req('/api/web-apps/_terminal/claim',body={'token':issued['token']},status=401)
 claimed=private('claim',{'token':issued['token']});assert claimed['containerName']==container and claimed['actorId']==user['id'];private('claim',{'token':issued['token']},401)
 with sqlite3.connect(db/'data.db')as conn:
  row=conn.execute('SELECT grantHash,leaseHash FROM web_terminal_sessions WHERE id=?',(issued['sessionId'],)).fetchone();assert row[0]==''and row[1]and row[1]!=claimed['proof']
 checks.append('actor_scoped_limits_hashed_single_use_grants_and_private_claim')
 payload={'sessionId':claimed['sessionId'],'proof':claimed['proof'],'containerId':'f'*64}
 assert private('validate',payload)['valid'];history=req('/api/web-apps/'+ident+'/terminal/sessions',token)
 assert all(x not in json.dumps(history)for x in [issued['token'],claimed['proof'],container,'f'*64]);assert history['items'][0]['status']in ['issued','open']
 private('validate',{**payload,'containerId':'e'*64});assert private('close',payload,401)is None
 req('/api/web-apps/'+ident+'/terminal/sessions/'+second['sessionId']+'/close',token,{})
 checks.append('first_container_binding_and_history_contains_no_grants_or_commands')
 revoked=req(path,token,{},status=201);claim=private('claim',{'token':revoked['token']});data={'sessionId':claim['sessionId'],'proof':claim['proof'],'containerId':'f'*64}
 assert private('validate',data)['valid'];req('/api/collections/users/records/'+user['id'],su,{'superAdmin':False},'PATCH');assert not private('validate',data)['valid']
 req('/api/collections/users/records/'+user['id'],su,{'superAdmin':True},'PATCH')
 changed=req(path,token,{},status=201);claim=private('claim',{'token':changed['token']});req('/api/collections/web_apps/records/'+ident,su,{'status':'stopped'},'PATCH');assert not private('validate',{'sessionId':claim['sessionId'],'proof':claim['proof'],'containerId':'f'*64})['valid']
 req('/api/collections/web_apps/records/'+ident,su,{'status':'running'},'PATCH')
 checks.append('live_actor_role_and_published_container_revocation')
 expired=req(path,token,{},status=201);req('/api/collections/web_terminal_sessions/records/'+expired['sessionId'],su,{'expiresAt':'2000-01-01 00:00:00.000Z'},'PATCH');private('claim',{'token':expired['token']},401)
 granted=req(path,token,{},status=201);claim=private('claim',{'token':granted['token']});data={'sessionId':claim['sessionId'],'proof':claim['proof'],'containerId':'f'*64};assert private('validate',data)['valid'];private('close',{**data,'reason':'disconnected'})
 row=req('/api/collections/web_terminal_sessions/records/'+granted['sessionId'],su);assert row['status']=='closed'and row['closedAt']and row['openedAt']and row['reason']=='disconnected'
 checks.append('expiry_and_session_metadata_audit_without_transcripts')
 print(json.dumps({'passed':True,'checks':checks}))
finally:
 if process:
  process.terminate()
  try:process.wait(timeout=5)
  except subprocess.TimeoutExpired:process.kill();process.wait()
 shutil.rmtree(root)
