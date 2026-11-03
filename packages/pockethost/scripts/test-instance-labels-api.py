#!/usr/bin/env python3
"""Exercise label updates against a disposable native controller; never touches customer bases."""
import argparse,json,os,pathlib,secrets,shutil,socket,subprocess,tempfile,time,urllib.error,urllib.request
p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=pathlib.Path);p.add_argument('--pocketbase',required=True);p.add_argument('--bundle',type=pathlib.Path);args=p.parse_args();os.umask(0o077)
root=pathlib.Path(tempfile.mkdtemp(prefix='ph-labels-api-'));proc=None;ok=False
with socket.socket()as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
base='http://127.0.0.1:'+str(port)
def api(path,token='',body=None,method=None,status=200):
 req=urllib.request.Request(base+path,data=json.dumps(body).encode()if body is not None else None,method=method,headers={'Content-Type':'application/json',**({'Authorization':token}if token else{})})
 try:
  with urllib.request.urlopen(req,timeout=20)as r:assert r.status==status;return json.load(r)
 except urllib.error.HTTPError as e:
  value=json.loads(e.read());assert e.code==status,(path,e.code,status,value);return value
try:
 source=args.source/'packages/pockethost/src/mothership-app';hooks=root/'hooks';migrations=root/'migrations';hooks.mkdir();migrations.mkdir()
 shutil.copyfile(args.bundle or source/'pb_hooks/mothership.js',hooks/'mothership.js')
 for file in(source/'pb_migrations').glob('*.js'):
  if file.name=='1793518500_added_instance_project_label.js'or(file.name.split('_')[0].isdigit()and int(file.name.split('_')[0])<=1793272000):shutil.copyfile(file,migrations/file.name)
 (hooks/'labels.pb.js').write_text("routerAdd('PUT','/api/instance/{id}',e=>require(`${__hooks}/mothership`).HandleInstanceUpdate(e),$apis.requireAuth('users'));\n")
 env={**os.environ,'APEX_DOMAIN':'app4.example.test','WEB_APPS_ENABLED':'true'};password='P'+secrets.token_urlsafe(30);data=root/'data'
 subprocess.run([args.pocketbase,'superuser','upsert','root@example.invalid',password,'--dir',str(data),'--hooksDir',str(root/'empty'),'--migrationsDir',str(migrations)],env=env,capture_output=True,check=True)
 with(root/'server.log').open('wb')as log:proc=subprocess.Popen([args.pocketbase,'serve','--http','127.0.0.1:'+str(port),'--dir',str(data),'--hooksDir',str(hooks),'--migrationsDir',str(migrations)],env=env,stdout=log,stderr=subprocess.STDOUT)
 for _ in range(100):
  if proc.poll()is not None:raise RuntimeError('Fixture exited')
  try:api('/api/health');break
  except OSError:time.sleep(.1)
 su=api('/api/collections/_superusers/auth-with-password',body={'identity':'root@example.invalid','password':password})['token']
 def user(name,admin=False):
  u=api('/api/collections/users/records',su,{'email':name+'@example.invalid','username':name,'verified':True,'password':password,'passwordConfirm':password,'superAdmin':admin,'subscription':'premium','subscription_quantity':20})
  return u,api('/api/collections/users/auth-with-password',body={'identity':u['email'],'password':password})['token']
 owner,token=user('owner');other,outsider=user('other');admin,operator=user('operator',True)
 row=api('/api/collections/instances/records',su,{'subdomain':'labels-fixture','uid':owner['id'],'version':'0.39.11','power':True,'status':'running'})
 path='/api/instance/'+row['id'];record='/api/collections/instances/records/'+row['id']
 api(path,token,{'fields':{'projectLabel':'  Socie\u0301te\u0301   LAPUB '}},'PUT');current=api(record,su)
 assert current['projectLabel']=='Société LAPUB'and current['power']and current['status']=='running'and current['uid']==owner['id']
 api(path,outsider,{'fields':{'projectLabel':'other'}},'PUT',400)
 for invalid in [None,{},['label'],'x'*61,'line\nbreak']:
  api(path,token,{'fields':{'projectLabel':invalid}},'PUT',400)
 assert api(record,su)['projectLabel']=='Société LAPUB'
 api(path,operator,{'fields':{'projectLabel':''}},'PUT');assert api(record,su)['projectLabel']==''
 ok=True;print(json.dumps({'ok':True,'checks':['label_saved_on_running_base','normalization','owner_and_operator_access','unrelated_user_denied','invalid_values_rejected_without_data_loss','label_removable_without_runtime_changes']}))
finally:
 if proc:
  proc.terminate()
  try:proc.wait(timeout=10)
  except subprocess.TimeoutExpired:proc.kill();proc.wait()
 if ok:shutil.rmtree(root)
 else:print('Fixture retained:',root);print((root/'server.log').read_text()[-4000:]if(root/'server.log').exists()else'')
