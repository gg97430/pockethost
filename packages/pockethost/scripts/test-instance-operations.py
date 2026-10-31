#!/usr/bin/env python3
"""Integration test using two disposable PocketBase processes; never touches live data."""
import argparse,hashlib,json,os,pathlib,re,secrets,shutil,socket,sqlite3,subprocess,tarfile,tempfile,time,urllib.error,urllib.request
p=argparse.ArgumentParser();p.add_argument('--pocketbase',required=True);p.add_argument('--source',type=pathlib.Path,required=True);args=p.parse_args()
os.umask(0o077)
root=pathlib.Path(tempfile.mkdtemp(prefix='ph-operations-test-'));processes=[];handles=[]
def port():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
cp,np=port(),port();control=f'http://127.0.0.1:{cp}';native=f'http://127.0.0.1:{np}'
def req(base,path,token='',body=None,method=None,expected=200):
 request=urllib.request.Request(base+path,method=method or ('POST' if body is not None else 'GET'),headers={'Content-Type':'application/json',**({'Authorization':token} if token else {})},data=None if body is None else json.dumps(body).encode())
 try:
  with urllib.request.urlopen(request,timeout=120) as r:
   raw=r.read();assert r.status==expected,(r.status,expected);return json.loads(raw) if raw else None
 except urllib.error.HTTPError as e:
  if e.code==expected:return None
  raise AssertionError(f'{path}: HTTP {e.code}: '+e.read().decode())
def launch(folder,hookdir,migrations,env,listen):
 folder.mkdir(parents=True,exist_ok=True);log=(folder/'process.log').open('wb');handles.append(log)
 process=subprocess.Popen([args.pocketbase,'serve','--dir',str(folder/'pb_data'),'--http',f'127.0.0.1:{listen}','--hooksDir',str(hookdir),'--migrationsDir',str(migrations)],env=env,stdout=log,stderr=subprocess.STDOUT);processes.append(process)
 for _ in range(100):
  if process.poll() is not None:raise RuntimeError('Process exited: '+(folder/'process.log').read_text()[-4000:])
  try:req(f'http://127.0.0.1:{listen}','/api/health');return
  except (AssertionError,OSError):time.sleep(.1)
 raise RuntimeError('Startup timeout')
try:
 hooks=root/'hooks';hooks.mkdir();source=args.source/'packages/pockethost/src/mothership-app'
 bundle=(source/'pb_hooks/mothership.js').read_text()
 needle='${protocol}://${subdomain}.${apex}/api/ph-service-accounts/execute'
 assert needle in bundle
 bundle=bundle.replace(needle,native+'/api/ph-service-accounts/execute')
 (hooks/'mothership.js').write_text(bundle)
 routes=[('GET','actions','HandleInstanceActionLog'),('GET','backup-checks','HandleInstanceBackupChecksGet'),('POST','backup-checks','HandleInstanceBackupCheckCreate'),('PUT','backup-checks/policy','HandleInstanceBackupCheckPolicy'),('GET','service-accounts','HandleInstanceServiceAccounts'),('POST','service-accounts','HandleInstanceServiceAccounts'),('PUT','','HandleInstanceUpdate')]
 script='onBootstrap(e=>{e.next();require(`${__hooks}/mothership`).BackupCheckBootstrap()});\nrouterUse(e=>require(`${__hooks}/mothership`).AuditInstanceRequest(e));\n'
 for method,path,handler in routes:
  script+=f'routerAdd({json.dumps(method)},{json.dumps("/api/instance/{id}"+("/"+path if path else ""))},e=>{{try{{return require(`${{__hooks}}/mothership`).{handler}(e)}}catch(error){{return e.json(error.status || 500,{{error:String(error),details:JSON.parse(JSON.stringify(error))}})}}}},$apis.requireAuth("users"));\n'
 script+='routerAdd("POST","/api/instance/{id}/operations/consume",e=>require(`${__hooks}/mothership`).HandleInstanceOperationConsume(e));\n'
 script+='routerAdd("POST","/__test/run",e=>{require(`${__hooks}/mothership`).BackupCheckDispatcher();return e.json(200,{ok:true})},$apis.requireSuperuserAuth());\n'
 script+='routerAdd("GET","/api/versions",e=>e.json(200,{versions:["0.39.*"]}));\n'
 (hooks/'test.pb.js').write_text(script)
 data=root/'data';env=dict(os.environ,DATA_ROOT=str(data),APP_URL=control,APEX_DOMAIN='example.invalid');env.pop('PH_SECRET',None)
 (root/'control').mkdir()
 old_migrations=root/'old-migrations';shutil.copytree(source/'pb_migrations',old_migrations);(old_migrations/'1793152800_created_instance_operations.js').unlink()
 password=secrets.token_urlsafe(30);email='operations@example.invalid'
 subprocess.run([args.pocketbase,'superuser','upsert',email,password,'--dir',str(root/'control/pb_data'),'--hooksDir',str(root/'empty-hooks'),'--migrationsDir',str(old_migrations)],check=True,capture_output=True,env=env)
 launch(root/'control',hooks,source/'pb_migrations',env,cp)
 for attempt in range(10):
  try:su=req(control,'/api/collections/_superusers/auth-with-password',body={'identity':email,'password':password})['token'];break
  except AssertionError:
   if attempt==9:raise
   time.sleep(.2)
 tokens=[];users=[]
 for n in range(2):
  identity=f'op{n}@example.invalid'
  user=req(control,'/api/collections/users/records',su,{'email':identity,'username':f'operator{n}','password':password,'passwordConfirm':password,'verified':True,'subscription':'premium','subscription_quantity':10});users.append(user)
  tokens.append(req(control,'/api/collections/users/auth-with-password',body={'identity':identity,'password':password})['token'])
 instance=req(control,'/api/collections/instances/records',su,{'uid':users[0]['id'],'subdomain':'operations-test','version':'0.39.*','power':True,'status':'idle'})
 ident=instance['id'];api='/api/instance/'+ident
 nenv=dict(env,PH_INSTANCE_ID=ident,PH_MOTHERSHIP_URL=control,PH_CONSOLE_SSO_ALLOW_HTTP='1')
 launch(root/'native',args.source/'packages/pockethost/src/instance-app/v23/pb_hooks',args.source/'packages/pockethost/src/instance-app/v23/pb_migrations',nenv,np)
 for section in ['actions','backup-checks','service-accounts']:
  req(control,api+'/'+section,expected=401);req(control,api+'/'+section,tokens[1],expected=403)
 created=req(control,api+'/service-accounts',tokens[0],{'action':'create','label':'TEST application'})
 def login(secret,expected=200):return req(native,'/api/collections/_superusers/auth-with-password',body={'identity':secret['email'],'password':secret['password']},expected=expected)
 old_token=login(created)['token']
 listing=req(control,api+'/service-accounts',tokens[0]);assert len(listing['accounts'])==1 and listing['accounts'][0]['lastAuthAt'];assert 'password' not in json.dumps(listing)
 rotated=req(control,api+'/service-accounts',tokens[0],{'action':'rotate','account':created['id']})
 login(created,400);req(native,'/api/collections',old_token,expected=401);new_token=login(rotated)['token']
 req(control,api+'/service-accounts',tokens[0],{'action':'revoke','account':created['id']});login(rotated,400);req(native,'/api/collections',new_token,expected=401)
 req(control,api+'/service-accounts',tokens[1],{'action':'create','label':'forbidden'},expected=403)
 req(native,'/api/ph-service-accounts/execute',body={'ticket':'x'*64},expected=401)
 req(control,api+'/operations/consume',body={'ticket':'x'*64},expected=401)
 # Restore-check queue uses a disposable customer archive, leaving its original untouched.
 instance_folder=data/'instances'/ident/'pb_data';instance_folder.mkdir(parents=True)
 dbpath=instance_folder/'data.db'
 with sqlite3.connect(dbpath) as db:db.execute('CREATE TABLE _collections(id TEXT)');db.execute("INSERT INTO _collections VALUES ('safe')")
 original=hashlib.sha256(dbpath.read_bytes()).hexdigest();archive=data/'backups/instances'/ident/'fixture.tar.gz';archive.parent.mkdir(parents=True)
 with tarfile.open(archive,'w:gz') as tar:tar.add(instance_folder,arcname='pb_data')
 backup=req(control,'/api/collections/instance_backups/records',su,{'user':users[0]['id'],'instance':ident,'status':'ready','kind':'manual','filename':archive.name,'localPath':str(archive),'checksum':hashlib.sha256(archive.read_bytes()).hexdigest(),'sizeBytes':dbpath.stat().st_size,'compressedBytes':archive.stat().st_size})
 queued=req(control,api+'/backup-checks',tokens[0],{},expected=202)['check']
 duplicate=req(control,api+'/backup-checks',tokens[0],{},expected=202)['check'];assert duplicate['id']==queued['id']
 req(control,'/__test/run',su,{})
 checks=req(control,api+'/backup-checks',tokens[0]);assert checks['items'][0]['status']=='passed',checks
 assert hashlib.sha256(dbpath.read_bytes()).hexdigest()==original
 req(control,api+'/backup-checks/policy',tokens[0],{'enabled':True,'intervalDays':7},method='PUT')
 assert req(control,api+'/backup-checks',tokens[0])['policy']['enabled']
 req(control,api,tokens[0],{'fields':{'power':False,'secrets':{'test':'private-setting-secret'}}},method='PUT')
 req(control,api,tokens[0],{'fields':{'power':True}},method='PUT')
 entries=req(control,api+'/actions',tokens[0])['items'];actions=[r['action'] for r in entries]
 for action in ['service-account.create','service-account.rotate','service-account.revoke','backup-check.passed','instance.started','instance.stopped']:assert action in actions,actions
 assert 'private-setting-secret' not in json.dumps(entries)
 assert created['password'] not in json.dumps(entries) and rotated['password'] not in json.dumps(entries)
 print(json.dumps({'permissions':'passed','technical_create_rotate_revoke':'passed','last_auth_tracking':'passed','old_tokens_invalidated':'passed','backup_restoration_and_unchanged_source':'passed','audit_privacy':'passed','work_dir':str(root)}))
finally:
 for process in reversed(processes):
  process.terminate()
  try:process.wait(timeout=10)
  except subprocess.TimeoutExpired:process.kill();process.wait()
 for handle in handles:handle.close()
 shutil.rmtree(root)
