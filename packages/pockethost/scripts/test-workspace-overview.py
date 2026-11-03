#!/usr/bin/env python3
"""Real disposable PocketBase: home counters, membership isolation, no secrets and superadmin global scope."""
import argparse,json,os,pathlib,secrets,shutil,socket,subprocess,tempfile,time,urllib.error,urllib.request
p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=pathlib.Path);p.add_argument('--pocketbase',required=True);args=p.parse_args()
os.umask(0o077)
process=None
with tempfile.TemporaryDirectory(prefix='kolab-workspace-test-') as folder:
 root=pathlib.Path(folder);source=args.source/'packages/pockethost/src/mothership-app';hooks=root/'hooks';hooks.mkdir();shutil.copy(source/'pb_hooks/mothership.js',hooks/'mothership.js')
 (hooks/'fixture.pb.js').write_text('routerAdd("GET","/api/workspace/overview",e=>{try{return require(`${__hooks}/mothership`).HandleWorkspaceOverview(e)}catch(error){return e.json(error.status||500,{message:String(error)})}},$apis.requireAuth("users"));')
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 url=f'http://127.0.0.1:{port}'
 env={**os.environ,'WEB_APPS_ENABLED':'true','PLATFORM_RESOURCES_ROOT':str(root/'resources'),'DATA_ROOT':str(root/'data'),'APEX_DOMAIN':'example.invalid'}
 def req(path,token='',body=None,method=None,status=200):
  r=urllib.request.Request(url+path,headers={'Content-Type':'application/json',**({'Authorization':token} if token else {})},data=json.dumps(body).encode() if body is not None else None,method=method or ('POST' if body is not None else 'GET'))
  try:
   with urllib.request.urlopen(r,timeout=15) as response:
    assert response.status==status,(response.status,status)
    raw=response.read();return json.loads(raw) if raw else None
  except urllib.error.HTTPError as error:
   if error.code==status:return None
   raise AssertionError(str(error.code)+': '+error.read().decode())
 try:
  password='T'+secrets.token_urlsafe(32);email='admin@example.invalid'
  subprocess.run([args.pocketbase,'superuser','upsert',email,password,'--dir',str(root/'db'),'--hooksDir',str(root/'none'),'--migrationsDir',str(source/'pb_migrations')],env=env,check=True,capture_output=True)
  with (root/'pb.log').open('wb') as log:
   process=subprocess.Popen([args.pocketbase,'serve','--dir',str(root/'db'),'--hooksDir',str(hooks),'--migrationsDir',str(source/'pb_migrations'),'--http',f'127.0.0.1:{port}'],env=env,stdout=log,stderr=subprocess.STDOUT)
  for _ in range(100):
   if process.poll() is not None:raise RuntimeError((root/'pb.log').read_text()[-4000:])
   try:req('/api/health');break
   except OSError:time.sleep(.1)
  su=req('/api/collections/_superusers/auth-with-password',body={'identity':email,'password':password})['token']
  def create(collection,body):return req('/api/collections/'+collection+'/records',su,body)
  def patch(collection,id,body):return req('/api/collections/'+collection+'/records/'+id,su,body,'PATCH')
  def user(name,admin=False):
   row=create('users',{'email':name+'@example.invalid','username':name,'password':password,'passwordConfirm':password,'verified':True,'subscription':'free','superAdmin':admin,'subscription_quantity':0})
   return row,req('/api/collections/users/auth-with-password',body={'identity':row['email'],'password':password})['token']
  admin,at=user('platform',True);member,mt=user('viewer');outsider,ot=user('outsider')
  base=create('instances',{'uid':admin['id'],'subdomain':'shared-base','power':True,'status':'failed','version':'0.39.*'})
  hidden=create('instances',{'uid':admin['id'],'subdomain':'hidden-base','power':False,'status':'idle','version':'0.39.*'})
  patch('users',member['id'],{'managedInstances':[base['id']]})
  project=create('web_projects',{'name':'Visible project'});other=create('web_projects',{'name':'Hidden project'})
  membership=create('web_project_members',{'project':project['id'],'user':member['id'],'role':'viewer'})
  def app(name,projectId):
   row=create('web_apps',{'name':name,'uid':admin['id'],'status':'running','lastError':'SECRET-ERROR-MUST-NOT-LEAK','config':{'repository':'team/site','branch':'main','rootDirectory':'.','healthPath':'/','generatedDomain':name+'.example.invalid'}})
   create('web_app_environments',{'projectId':projectId,'appId':row['id'],'environment':'prod','slug':name,'state':'ready'})
   return row
  a=app('visible-app',project['id']);b=app('hidden-app',other['id'])
  from datetime import datetime,timezone,timedelta
  now=datetime.now(timezone.utc);today=now.isoformat();old=(now-timedelta(days=2)).isoformat()
  dep=create('web_deployments',{'appId':a['id'],'action':'deploy','status':'ready','finishedAt':today,'snapshotEncrypted':'SECRET-SNAPSHOT','error':'SECRET-ERROR-MUST-NOT-LEAK'})
  create('web_deployments',{'appId':a['id'],'action':'deploy','status':'failed','finishedAt':today})
  create('web_deployments',{'appId':a['id'],'action':'deploy','status':'failed','finishedAt':old})
  create('web_deployments',{'appId':a['id'],'action':'deploy','status':'running'})
  create('web_deployments',{'appId':a['id'],'action':'stop','status':'ready','finishedAt':today})
  create('web_deployments',{'appId':b['id'],'action':'deploy','status':'failed','finishedAt':today})
  patch('web_apps',a['id'],{'activeDeployment':dep['id']})
  r=req('/api/workspace/overview',mt);c=r['counts']
  assert c['databases']==1 and c['databasesFailed']==1 and c['applications']==1 and c['projects']==1 and c['projectsWithErrors']==1 and c['deploymentsInProgress']==1,c
  assert r['deployments24h']=={'ready':1,'failed':1,'cancelled':0},r['deployments24h']
  assert len(r['recentDeployments'])==4 and all(d['appId']==a['id'] for d in r['recentDeployments'])
  assert any(i['servingPreviousVersion'] for i in r['attention']) and r['resources'] is None
  assert not r['canCreateBase'] and not r['canCreateApp']
  assert 'SECRET' not in json.dumps(r) and 'hidden-' not in json.dumps(r)
  empty=req('/api/workspace/overview',ot);assert not any(empty['counts'].values()) and not empty['attention'] and not empty['recentDeployments']
  all_=req('/api/workspace/overview',at);assert all_['counts']['databases']==2 and all_['counts']['projectsWithErrors']==2 and all_['canCreateBase'] and all_['canCreateApp']
  patch('web_apps',a['id'],{'lastError':''})
  assert req('/api/workspace/overview',mt)['counts']['projectsWithErrors']==0
  req('/api/collections/web_project_members/records/'+membership['id'],su,method='DELETE',status=204)
  assert req('/api/workspace/overview',mt)['counts']['applications']==0
  patch('users',member['id'],{'suspension':'blocked'})
  req('/api/workspace/overview',mt,status=403);req('/api/workspace/overview',status=401)
  print(json.dumps({'home_summary':'passed','rbac_isolation_and_live_revocation':'passed','counts_and_24h_window':'passed','superadmin_global':'passed','previous_version_indicator':'passed','no_secret_serialization':'passed','anonymous_and_suspended_denied':'passed'}))
 finally:
  if process:
   process.terminate()
   try:process.wait(timeout=10)
   except subprocess.TimeoutExpired:process.kill();process.wait()
