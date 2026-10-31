#!/usr/bin/env python3
"""Actual disposable PocketBase: source route authorization and runtime-only publication transactions."""
import argparse,concurrent.futures,copy,json,os,pathlib,secrets,shutil,socket,sqlite3,subprocess,tempfile,time,urllib.error,urllib.request
p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=pathlib.Path);p.add_argument('--pocketbase',required=True);args=p.parse_args()
os.umask(0o077);root=pathlib.Path(tempfile.mkdtemp(prefix='ph-source-apply-api-')).resolve();process=None;checks=[]
with socket.socket()as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
base=f'http://127.0.0.1:{port}'
def req(path,t='',body=None,method=None,status=200,headers=False):
 request=urllib.request.Request(base+path,method=method or('POST'if body is not None else'GET'),headers={'Content-Type':'application/json',**({'Authorization':t}if t else{})},data=None if body is None else json.dumps(body).encode())
 try:
  with urllib.request.urlopen(request,timeout=25)as response:
   raw=response.read();assert response.status==status,(path,response.status,status);value=json.loads(raw)if raw else None;return(value,dict(response.headers))if headers else value
 except urllib.error.HTTPError as error:
  raw=error.read()
  if error.code==status:return json.loads(raw)if raw else None
  raise AssertionError(f'{path}: {error.code} '+raw.decode())
try:
 source=args.source/'packages/pockethost/src/mothership-app';hooks=root/'hooks';hooks.mkdir();shutil.copy(source/'pb_hooks/mothership.js',hooks/'mothership.js')
 routes=[('GET','/api/web-apps/github/health','HandleWebGithubHealth'),('GET','/api/web-apps/repositories','HandleWebRepositories'),('GET','/api/web-apps/repositories/{repositoryId}/branches','HandleWebBranches'),('POST','/api/web-apps/{id}/apply-runtime','HandleWebApplyRuntime'),('GET','/api/web-apps/{id}/environment','HandleWebEnvironmentGet')]
 text='\n'.join('routerAdd('+json.dumps(method)+','+json.dumps(path)+',e=>require(`${__hooks}/mothership`).'+handler+'(e),$apis.requireAuth("users"));'for method,path,handler in routes)
 text+='''
routerAdd('POST','/test/fixture',e=>{
 if(!e.auth?.getBool('superAdmin'))throw new ForbiddenError();const mod=require(`${__hooks}/mothership`),b=e.requestInfo().body;
 const app=new Record($app.findCollectionByNameOrId('web_apps'));app.set('uid',e.auth.id);app.set('name','Runtime fixture');app.set('status','running');app.set('config',b.configAfter);app.set('envEncrypted',mod.encryptWebValue(b.envAfter));app.set('desiredCommit',b.desiredCommit||'c'.repeat(40));$app.save(app);
 const job=new Record($app.findCollectionByNameOrId('web_deployments'));job.set('appId',app.id);job.set('actorId',e.auth.id);job.set('action','deploy');job.set('status',b.activeStatus||'ready');job.set('commit','c'.repeat(40));job.set('snapshotEncrypted',mod.encryptWebValue({config:b.configBefore,env:b.envBefore}));$app.save(job);job.set('result',{image:b.invalidImage?'other/app:invalid':'ph-web/'+app.id+':'+job.id,container:'ph-web-'+app.id+'-'+job.id,available:b.available!==false,port:32100});$app.save(job);app.set('activeDeployment',job.id);$app.save(app);
 if(b.pending){const pending=new Record($app.findCollectionByNameOrId('web_deployments'));pending.set('appId',app.id);pending.set('action','deploy');pending.set('status',b.pending);$app.save(pending)}
 return e.json(200,{id:app.id,publishedJob:job.id})
},$apis.requireAuth('users'));
routerAdd('GET','/test/job/{id}',e=>{if(!e.auth?.getBool('superAdmin'))throw new ForbiddenError();const mod=require(`${__hooks}/mothership`),job=$app.findRecordById('web_deployments',e.request.pathValue('id'));return e.json(200,{snapshot:mod.decryptWebValue(job.getString('snapshotEncrypted')),encrypted:job.getString('snapshotEncrypted'),actorId:job.getString('actorId'),commit:job.getString('commit'),action:job.getString('action'),status:job.getString('status')})},$apis.requireAuth('users'));
'''
 (hooks/'source-apply.pb.js').write_text(text)
 env={**os.environ,'WEB_APPS_ENABLED':'true','WEB_APPS_MASTER_KEY':'e'*32,'WEB_APPS_ROOT':str(root/'web'),'DATA_ROOT':str(root/'data'),'APEX_DOMAIN':'example.invalid','APP_HOST':'app.example.invalid'}
 password='P'+secrets.token_urlsafe(32);email='sourceapply@example.invalid';data=root/'pb_data'
 subprocess.run([args.pocketbase,'superuser','upsert',email,password,'--dir',str(data),'--hooksDir',str(root/'empty-hooks'),'--migrationsDir',str(source/'pb_migrations')],env=env,check=True,capture_output=True)
 log=(root/'process.log').open('wb');process=subprocess.Popen([args.pocketbase,'serve','--dir',str(data),'--hooksDir',str(hooks),'--migrationsDir',str(source/'pb_migrations'),'--http',f'127.0.0.1:{port}'],env=env,stdout=log,stderr=subprocess.STDOUT)
 for _ in range(100):
  if process.poll()is not None:raise RuntimeError('Startup failed: '+(root/'process.log').read_text()[-2000:])
  try:req('/api/health');break
  except OSError:time.sleep(.1)
 su=req('/api/collections/_superusers/auth-with-password',body={'identity':email,'password':password})['token']
 def user(name,admin):
  actor=req('/api/collections/users/records',su,{'email':name+'@example.invalid','password':password,'passwordConfirm':password,'username':name,'verified':True,'superAdmin':admin,'subscription':'premium','subscription_quantity':1});return actor,req('/api/collections/users/auth-with-password',body={'identity':actor['email'],'password':password})['token']
 actor,token=user('adminsource',True);_,plain=user('plainsource',False)
 for method,path in [('GET','/api/web-apps/github/health'),('GET','/api/web-apps/repositories'),('GET','/api/web-apps/repositories/7/branches'),('POST','/api/web-apps/'+'a'*15+'/apply-runtime')]:
  req(path,body={}if method=='POST'else None,method=method,status=401);req(path,plain,{}if method=='POST'else None,method,status=404 if path.endswith('/apply-runtime') else 403)
 req('/api/collections/web_app_settings/records',token,status=403);checks.append('all_source_and_apply_routes_require_active_admin_and_settings_stay_locked')
 health,headers=req('/api/web-apps/github/health',token,headers=True);assert not health['configured']and not health['ok']and health['checkedAt'];assert headers['Cache-Control']=='no-store';assert health['webhook']['url']=='https://app.example.invalid/api/web-apps/github/webhook'
 req('/api/collections/web_webhook_deliveries/records',su,{'deliveryId':'fixture-verified-push','connectionId':'default','event':'push','repository':'owner/repo','status':'queued','lastReceivedAt':'2026-10-29 09:00:00.000Z'})
 health=req('/api/web-apps/github/health',token);assert health['webhook']['lastStatus']=='queued'and health['webhook']['lastReceivedAt'].startswith('2026-10-29');assert 'privateKey'not in json.dumps(health)
 repos,headers=req('/api/web-apps/repositories?page=2&perPage=25&search=private',token,headers=True);assert repos['items']==[]and repos['page']==2 and repos['perPage']==25 and repos['searchScope']=='authorized-repositories'and not repos['configured'];assert headers['Cache-Control']=='no-store'
 bounded=req('/api/web-apps/repositories?page=99999&perPage=99999',token);assert bounded['page']==1000and bounded['perPage']==100
 for ident in ['0','-7','evil','1%27OR%271','123456789012345678901']:req('/api/web-apps/repositories/'+ident+'/branches',token,status=400)
 req('/api/web-apps/repositories/7/branches',token,status=400);checks.append('native_source_health_webhook_feedback_pagination_bounds_and_branch_validation')
 config={'repository':'owner/repo','repositoryId':7,'branch':'main','rootDirectory':'.','healthPath':'/','generatedDomain':'web-fixture.example.invalid','autoDeploy':True}
 original={'BUILD':{'value':'build-v1','build':True,'runtime':False},'SERVER':{'value':'runtime-v1','build':False,'runtime':True}}
 runtime=copy.deepcopy(original);runtime['SERVER']['value']='runtime-v2\nline2-$LITERAL'
 def fixture(env_after=None,config_after=None,**extra):return req('/test/fixture',token,{'envBefore':original,'envAfter':runtime if env_after is None else env_after,'configBefore':config,'configAfter':config if config_after is None else config_after,**extra})
 def jobs(ident):
  with sqlite3.connect((data/'data.db').as_uri()+'?mode=ro',uri=True)as db:return db.execute('SELECT COUNT(*) FROM web_deployments WHERE appId=? AND action=?',(ident,'apply-runtime')).fetchone()[0]
 app=fixture();ident=app['id'];flags=req('/api/web-apps/'+ident+'/environment',token);assert flags['canApplyRuntime']and flags['runtimeOnlyChanges']and not flags['requiresRebuild']
 req('/api/collections/web_app_domains/records',su,{'appId':ident,'host':'custom.example.invalid','status':'active','ownershipToken':'fixture-domain-ownership-token'})
 response,headers=req('/api/web-apps/'+ident+'/apply-runtime',token,{},status=202,headers=True);queued=response['deployment'];assert headers['Cache-Control']=='no-store';assert queued['action']=='apply-runtime'and queued['status']=='queued';assert 'runtime-v2'not in json.dumps(response)
 private=req('/test/job/'+queued['id'],token);assert private['actorId']==actor['id']and private['commit']=='c'*40;assert private['snapshot']['env']==runtime;assert private['snapshot']['config']==config;assert private['snapshot']['rollbackResult']['image']=='ph-web/'+ident+':'+app['publishedJob'];assert private['snapshot']['domains'][0]['host']=='custom.example.invalid'and private['snapshot']['domains'][0]['certificate'];assert 'runtime-v2'not in private['encrypted']
 req('/api/web-apps/'+ident+'/apply-runtime',token,{},status=400);assert jobs(ident)==1;checks.append('runtime_only_apply_reuses_published_image_current_encrypted_environment_domains_and_actor')
 rejected=[]
 def reject(label,**kwargs):
  app=fixture(**kwargs);req('/api/web-apps/'+app['id']+'/apply-runtime',token,{},status=400);assert jobs(app['id'])==0;rejected.append(label)
 reject('unchanged',env_after=original)
 changed=copy.deepcopy(runtime);changed['BUILD']['value']='build-v2';reject('build_value_changed',env_after=changed)
 changed=copy.deepcopy(runtime);changed['SERVER']['build']=True;reject('build_scope_changed',env_after=changed)
 changed=copy.deepcopy(runtime);del changed['BUILD'];reject('build_variable_removed',env_after=changed)
 for field,value in [('repository','owner/other'),('branch','develop'),('rootDirectory','apps/site')]:reject(field+'_changed',config_after={**config,field:value})
 reject('desired_commit_changed',desiredCommit='d'*40)
 reject('unavailable_image',available=False);reject('invalid_image',invalidImage=True);reject('failed_publication',activeStatus='failed')
 for status in ['queued','running']:reject('existing_'+status,pending=status)
 checks.append('reject_without_queueing:'+','.join(rejected))
 # Both HTTP requests race; the transaction must leave only one new publication.
 app=fixture();ident=app['id']
 def race():
  try:req('/api/web-apps/'+ident+'/apply-runtime',token,{},status=202);return 202
  except AssertionError as error:
   if ': 400 'in str(error):return 400
   raise
 with concurrent.futures.ThreadPoolExecutor(max_workers=2)as pool:statuses=list(pool.map(lambda _:race(),range(2)))
 assert sorted(statuses)==[202,400]and jobs(ident)==1,(statuses,jobs(ident));checks.append('simultaneous_apply_requests_create_exactly_one_job')
 print(json.dumps({'passed':True,'checks':checks}),flush=True)
except Exception:
 print('Disposable test failure:',str(root),flush=True)
 if(root/'process.log').exists():print((root/'process.log').read_text()[-3000:])
 raise
finally:
 if process:
  process.terminate()
  try:process.wait(timeout=10)
  except subprocess.TimeoutExpired:process.kill();process.wait()
 shutil.rmtree(root,ignore_errors=True)
