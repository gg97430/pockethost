#!/usr/bin/env python3
"""Actual disposable PocketBase: literal env imports, optimistic revisions and audited disclosure."""
import argparse,pathlib,tempfile,subprocess,json,os,urllib.request,urllib.error,secrets,socket,shutil,time,sqlite3
p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=pathlib.Path);p.add_argument('--pocketbase',required=True);args=p.parse_args()
os.umask(0o077);root=pathlib.Path(tempfile.mkdtemp(prefix='ph-web-env-test-')).resolve();process=None;checks=[]
with socket.socket()as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
base=f'http://127.0.0.1:{port}'
def req(path,t='',body=None,method=None,status=200):
 request=urllib.request.Request(base+path,method=method or('POST'if body is not None else'GET'),headers={'Content-Type':'application/json',**({'Authorization':t}if t else{})},data=None if body is None else json.dumps(body).encode())
 try:
  with urllib.request.urlopen(request,timeout=25)as r:
   raw=r.read();assert r.status==status,(path,r.status,status);return json.loads(raw)if raw else None
 except urllib.error.HTTPError as e:
  if e.code==status:return json.loads(e.read())
  raise AssertionError(f'{path}: {e.code} '+e.read().decode())
try:
 source=args.source/'packages/pockethost/src/mothership-app';hooks=root/'hooks';hooks.mkdir();shutil.copy(source/'pb_hooks/mothership.js',hooks/'mothership.js')
 text=''
 for method,path,handler in [('GET','/api/web-apps/{id}/environment','HandleWebEnvironmentGet'),('POST','/api/web-apps/{id}/environment/reveal','HandleWebEnvironmentReveal'),('POST','/api/web-apps/{id}/environment/preview','HandleWebEnvironmentPreview'),('PUT','/api/web-apps/{id}/environment','HandleWebEnvironmentPut')]:
  text+=f'routerAdd({json.dumps(method)},{json.dumps(path)},e=>require(`${{__hooks}}/mothership`).{handler}(e),$apis.requireAuth("users"));\n'
 text+='''routerAdd('POST','/test/publish',e=>{if(!e.auth?.getBool('superAdmin'))throw new ForbiddenError();const mod=require(`${__hooks}/mothership`),r=$app.findRecordById('web_apps',e.requestInfo().body.id),job=new Record($app.findCollectionByNameOrId('web_deployments'));job.set('appId',r.id);job.set('action','deploy');job.set('status','ready');job.set('commit','c'.repeat(40));job.set('snapshotEncrypted',mod.encryptWebValue({config:JSON.parse(r.getString('config')),env:mod.decryptWebValue(r.getString('envEncrypted'))}));$app.save(job);r.set('activeDeployment',job.id);r.set('desiredCommit','c'.repeat(40));r.set('status','running');$app.save(r);return e.json(200,{published:true})},$apis.requireAuth('users'));\n'''
 (hooks/'test.pb.js').write_text(text)
 env={**os.environ,'WEB_APPS_ENABLED':'true','WEB_APPS_MASTER_KEY':'e'*32,'WEB_APPS_ROOT':str(root/'web'),'DATA_ROOT':str(root/'data'),'APEX_DOMAIN':'example.invalid'}
 password='P'+secrets.token_urlsafe(32);email='env@example.invalid';data=root/'pb_data'
 subprocess.run([args.pocketbase,'superuser','upsert',email,password,'--dir',str(data),'--hooksDir',str(root/'empty-hooks'),'--migrationsDir',str(source/'pb_migrations')],env=env,check=True,capture_output=True)
 log=(root/'process.log').open('wb');process=subprocess.Popen([args.pocketbase,'serve','--dir',str(data),'--hooksDir',str(hooks),'--migrationsDir',str(source/'pb_migrations'),'--http',f'127.0.0.1:{port}'],env=env,stdout=log,stderr=subprocess.STDOUT)
 for _ in range(100):
  if process.poll()is not None:raise RuntimeError('Startup failed: '+(root/'process.log').read_text()[-2000:])
  try:req('/api/health');break
  except OSError:time.sleep(.1)
 su=req('/api/collections/_superusers/auth-with-password',body={'identity':email,'password':password})['token']
 def user(name,admin):
  record=req('/api/collections/users/records',su,{'email':name+'@example.invalid','password':password,'passwordConfirm':password,'username':name,'verified':True,'superAdmin':admin,'subscription':'premium','subscription_quantity':1})
  return record,req('/api/collections/users/auth-with-password',body={'identity':record['email'],'password':password})['token']
 actor,token=user('adminenv',True);_,plain=user('plainenv',False);_,other=user('otherenv',True)
 app=req('/api/collections/web_apps/records',su,{'name':'Env fixture','uid':actor['id'],'status':'stopped','config':{'repository':'owner/repo','branch':'main','rootDirectory':'.','generatedDomain':'web-fixture.example.invalid'}});ident=app['id'];path='/api/web-apps/'+ident+'/environment'
 req(path,status=401);req(path,plain,status=404);req('/api/collections/web_environment_audit/records',token,status=403)
 initial=req(path,token);assert initial['variables']==[]and len(initial['revision'])==64
 checks.append('roles_locked_audit_and_masked_default')
 def preview(text,mode='merge',revision=None):return req(path+'/preview',token,{'revision':revision or req(path,token)['revision'],'text':text,'mode':mode})
 def apply(text,mode='merge'):
  revision=req(path,token)['revision'];plan=preview(text,mode,revision);return req(path,token,{'revision':revision,'text':text,'mode':mode,'previewToken':plan['previewToken']},'PUT')
 original="# pockethost: build=true runtime=true\nPUBLIC=https://example.invalid/a=b#comment\n# pockethost: build=false runtime=true\nSECRET='env-private-$HOME-$(keep)'\nMULTILINE='line1\nline2'\nEMPTY=\n"
 # Unquoted hashes are comments; quoted multiline and dollars remain literal.
 saved=apply(original);assert len(saved['variables'])==4and 'env-private'not in json.dumps(saved)
 masked=req(path,token);assert '__POCKETHOST_MASKED__'in masked['dotenv'];assert all('value'not in row for row in masked['variables'])
 request=urllib.request.Request(base+path+'/reveal',headers={'Authorization':token,'Content-Type':'application/json'},data=b'{}')
 with urllib.request.urlopen(request)as r:revealed=json.load(r);assert'no-store'in r.headers['Cache-Control']and r.headers['Pragma']=='no-cache'
 values={r['name']:r['value']for r in revealed['variables']};assert values['SECRET']=='env-private-$HOME-$(keep)'and values['MULTILINE']=='line1\nline2'and values['EMPTY']==''
 records=req('/api/collections/web_environment_audit/records?perPage=100',su)['items'];assert any(r['action']=='revealed'for r in records);assert 'env-private'not in json.dumps(records)
 checks.append('literal_multiline_empty_export_masked_and_explicit_reveal_audit')
 before=req(path,token);apply(before['dotenv'],'replace');assert {r['name']:r['value']for r in req(path+'/reveal',token,{})['variables']}==values
 assert revealed['nextDotenv'].startswith('# pockethost-export: nextjs\n')and '\\$HOME'in revealed['nextDotenv'];apply(revealed['nextDotenv'],'replace');assert{r['name']:r['value']for r in req(path+'/reveal',token,{})['variables']}==values
 def cipher():
  with sqlite3.connect((data/'data.db').as_uri()+'?mode=ro',uri=True)as db:return db.execute('SELECT envEncrypted FROM web_apps WHERE id=?',(ident,)).fetchone()[0]
 encrypted=cipher()
 for bad in ['A=secret\nA=two','NODE_ENV=private','BAD.NAME=private','A="not-closed']:
  failure=req(path+'/preview',token,{'revision':req(path,token)['revision'],'text':bad,'mode':'replace'},status=400);assert cipher()==encrypted;assert not any(value in json.dumps(failure)for value in ['secret','private','not-closed'])
 checks.append('masked_roundtrip_and_invalid_import_atomic_no_secret_values_in_errors')
 rev=req(path,token)['revision'];candidate="SECRET='new-secret'";plan=preview(candidate,'merge',rev)
 req(path,other,{'revision':rev,'text':candidate,'mode':'merge','previewToken':plan['previewToken']},'PUT',400)
 req(path,token,{'revision':rev,'text':candidate,'mode':'merge','previewToken':'bad'},'PUT',400)
 apply("EMPTY='different'")
 req(path,token,{'revision':rev,'text':candidate,'mode':'merge','previewToken':plan['previewToken']},'PUT',400)
 checks.append('preview_bound_to_actor_exact_content_and_optimistic_revision')
 p=preview("ONLY='remaining'",'replace');assert p['changes']['removed']==['EMPTY','MULTILINE','PUBLIC','SECRET'];assert p['total']==1
 apply("ONLY='remaining'",'replace');req('/test/publish',token,{'id':ident});assert req(path,token)['pendingChanges']is False
 apply("# pockethost: build=false runtime=true\nRUNTIME='live'");runtime=req(path,token);assert runtime['runtimeOnlyChanges']and runtime['canApplyRuntime']and not runtime['requiresRebuild']
 apply("ONLY='new build value'");assert req(path,token)['requiresRebuild']and not req(path,token)['canApplyRuntime']
 checks.append('replace_preview_removals_and_published_build_runtime_comparison')
 rev=req(path,token)['revision'];rows=[{'name':'WEIRD','value':'all\'"`quotes\nsecond','build':False,'runtime':True},{'name':'ZERO','value':'','setEmpty':True,'build':False,'runtime':True}]
 plan=req(path+'/preview',token,{'revision':rev,'rows':rows,'mode':'replace'});saved=req(path,token,{'revision':rev,'rows':rows,'mode':'replace','previewToken':plan['previewToken']},'PUT')
 reveal=req(path+'/reveal',token,{});assert reveal['exportWarnings']==['WEIRD'];assert{r['name']:r['value']for r in reveal['variables']}=={'WEIRD':'all\'"`quotes\nsecond','ZERO':''};assert'all\''not in saved['dotenv']
 checks.append('normal_structured_arbitrary_quotes_multiline_and_explicit_empty')
 print(json.dumps({'passed':True,'checks':checks}),flush=True)
except Exception:
 print('Private disposable test failure:',str(root),flush=True)
 if(root/'process.log').exists():print((root/'process.log').read_text()[-2500:])
 raise
finally:
 if process:
  process.terminate()
  try:process.wait(timeout=10)
  except subprocess.TimeoutExpired:process.kill();process.wait()
 shutil.rmtree(root,ignore_errors=True)
