#!/usr/bin/env python3
"""Disposable controller + native PocketBase, real TOTP and SSO; Docker is mocked."""
import argparse,base64,hashlib,hmac,json,os,pathlib,secrets,shutil,socket,struct,subprocess,tempfile,time,urllib.request,urllib.error
p=argparse.ArgumentParser();p.add_argument('--source',type=pathlib.Path,required=True);p.add_argument('--pocketbase',required=True);a=p.parse_args()
os.umask(0o077);root=pathlib.Path(tempfile.mkdtemp(prefix='kolab-request-limits-'));processes=[];checks=[]
password=secrets.token_urlsafe(32);email='admin@example.invalid';instance='e'*15
def port():
 with socket.socket()as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
controller_port,native_port=port(),port();base=f'http://127.0.0.1:{controller_port}';native=f'http://127.0.0.1:{native_port}'
def request(url,token='',body=None,method=None,status=200):
 r=urllib.request.Request(url,headers={'Content-Type':'application/json',**({'Authorization':token}if token else{})},data=None if body is None else json.dumps(body).encode(),method=method or('POST'if body is not None else'GET'))
 try:
  with urllib.request.urlopen(r,timeout=30)as response:
   assert response.status==status;return json.load(response)
 except urllib.error.HTTPError as e:
  if e.code==status:return None
  raise AssertionError(f'{url}: expected{status}, received{e.code}: '+e.read().decode()[:300])
def start(folder,port_,env,migrations):
 command=[a.pocketbase,'serve','--dir',str(folder/'data'),'--hooksDir',str(folder/'hooks'),'--migrationsDir',str(migrations),'--http',f'127.0.0.1:{port_}']
 log=(folder/'process.log').open('wb');proc=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env);processes.append(proc)
 for _ in range(100):
  if proc.poll()is not None:raise RuntimeError((folder/'process.log').read_text()[-1000:])
  try:request(f'http://127.0.0.1:{port_}/api/health');return
  except OSError:time.sleep(.1)
 raise TimeoutError('Test process did not start')
def totp(secret):
 digest=hmac.new(base64.b32decode(secret),struct.pack('>Q',int(time.time()//30)),hashlib.sha1).digest();offset=digest[-1]&15
 return str((struct.unpack('>I',digest[offset:offset+4])[0]&0x7fffffff)%1000000).zfill(6)
try:
 source=a.source/'packages/pockethost';controller=root/'controller';n=root/'native';controller.mkdir();n.mkdir()
 migrations=root/'migrations';shutil.copytree(source/'src/mothership-app/pb_migrations',migrations)
 for folder in [controller,n]:(folder/'hooks').mkdir();(folder/'empty').mkdir()
 shutil.copy(source/'src/mothership-app/pb_hooks/mothership.js',controller/'hooks/mothership.js')
 for name in ['_ph_totp.js','_ph_totp.pb.js','_ph_admin_sync.pb.js','_ph_lib.js']:shutil.copy(source/'src/instance-app/v23/pb_hooks'/name,n/'hooks'/name)
 script='onBootstrap(e=>{e.next();require(`${__hooks}/mothership`).InitializeDashboardTotp()});\nrouterUse(e=>require(`${__hooks}/mothership`).GuardDashboardTotp(e));\nonRecordAuthRequest(e=>require(`${__hooks}/mothership`).HandleDashboardTotpAuth(e),"users");\n'
 routes=[('GET','/api/admin/request-limits/web','HandleOperatorWebRequestLimit','users'),('PUT','/api/admin/request-limits/web','HandleOperatorWebRequestLimit','users'),('GET','/api/admin/request-limits','HandleOperatorRequestLimits','users'),('PUT','/api/admin/request-limits','HandleOperatorRequestLimitsPut','users'),('GET','/api/admin/request-limits/databases/{id}','HandleOperatorDatabaseRequestLimits','users'),('PUT','/api/admin/request-limits/databases/{id}','HandleOperatorDatabaseRequestLimits','users'),('POST','/api/account/totp/setup','HandleDashboardTotpSetup','users'),('POST','/api/account/totp/confirm','HandleDashboardTotpConfirm','users'),('POST','/api/instance/{id}/console/consume','HandleInstanceConsoleConsume','')]
 for method,path,handler,realm in routes:script+=f'routerAdd({json.dumps(method)},{json.dumps(path)},e=>require(`${{__hooks}}/mothership`).{handler}(e)'+(f',$apis.requireAuth({json.dumps(realm)})'if realm else'')+');\n'
 (controller/'hooks/routes.pb.js').write_text(script)
 tools=root/'bin';tools.mkdir();data_root=root/'tenant-data';(data_root/'instances'/instance).mkdir(parents=True)
 info={'Id':'a'*64,'Name':'/'+instance,'Image':'sha256:'+'b'*64,'Config':{'Image':'benallfree/pockethost-instance:fixture','Env':['PH_INSTANCE_ID='+instance]},'State':{'Running':True,'StartedAt':'2026-11-02T00:00:00Z'},'HostConfig':{},'Mounts':[{'Source':str(data_root/'instances'/instance),'Destination':'/home/pockethost','Type':'bind'}],'NetworkSettings':{'Ports':{'8090/tcp':[{'HostIp':'127.0.0.1','HostPort':str(native_port)}]}}}
 docker=tools/'docker';docker.write_text('#!/usr/bin/env python3\nimport json\nprint('+repr(json.dumps([info]))+')\n');docker.chmod(0o700)
 env={**os.environ,'PATH':str(tools)+':'+os.environ['PATH'],'DATA_ROOT':str(data_root),'APEX_DOMAIN':'example.invalid','APP_URL':base,'INSTANCE_REQUEST_LIMITS_SCRIPT':str(source/'scripts/instance_request_limits.py'),'PH_INSTANCE_IMAGE_OVERRIDES':''}
 for folder,mi in [(controller,migrations),(n,n/'empty')]:subprocess.run([a.pocketbase,'superuser','upsert',email,password,'--dir',str(folder/'data'),'--hooksDir',str(folder/'empty'),'--migrationsDir',str(mi)],check=True,capture_output=True,env=env)
 start(controller,controller_port,env,migrations)
 start(n,native_port,{**env,'PH_INSTANCE_ID':instance,'PH_MOTHERSHIP_URL':base,'PH_CONSOLE_SSO_ALLOW_HTTP':'1'},n/'empty')
 su=request(base+'/api/collections/_superusers/auth-with-password',body={'identity':email,'password':password})['token']
 nsu=request(native+'/api/collections/_superusers/auth-with-password',body={'identity':email,'password':password})['token']
 def user(address,superadmin):return request(base+'/api/collections/users/records',su,{'email':address,'username':'user'+secrets.token_hex(4),'password':password,'passwordConfirm':password,'verified':True,'superAdmin':superadmin,'subscription_quantity':10,'subscription':'premium'})
 admin=user(email,True);normal=user('normal@example.invalid',False)
 request(base+'/api/collections/instances/records',su,{'id':instance,'uid':admin['id'],'subdomain':'fixture','version':'0.40.*','power':True,'status':'active'})
 def login(address):return request(base+'/api/collections/users/auth-with-password',body={'identity':address,'password':password})['token']
 def enroll(token):
  value=request(base+'/api/account/totp/setup',token,{'password':password})
  return request(base+'/api/account/totp/confirm',token,{'password':password,'code':totp(value['secret'])})['token']
 pending=login(email);path=base+'/api/admin/request-limits';dbpath=path+'/databases/'+instance
 request(path,status=401);request(path,pending,status=403);request(dbpath,pending,status=403)
 token=enroll(pending);other=enroll(login(normal['email']))
 for target in [path,dbpath,path+'/web']:
  request(target,other,status=403);request(target,other,{},'PUT',403)
 checks.append('anonymous_regular_and_unenrolled_admin_denied')
 before=request(path,token);assert before['limits']=={'total':512,'perApp':128,'perBinding':128,'perDatabase':128}
 changed={**before,'limits':{'total':64,'perApp':16,'perBinding':8,'perDatabase':32}}
 saved=request(path,token,changed,'PUT');assert saved['limits']==changed['limits'];assert request(path,token)==saved
 request(path,token,before,'PUT',409)
 for patch in [{'perApp':0},{'perBinding':128},{'total':True},{'total':2048}]:request(path,token,{'revision':saved['revision'],'limits':{**saved['limits'],**patch}},'PUT',400)
 assert request(path,token)==saved;checks.append('relay_defaults_save_validation_and_stale_conflict')
 web=request(path+'/web',token);assert web['requestsPerMinute']==1200
 disabled=request(path+'/web',token,{'requestsPerMinute':0,'revision':web['revision']},'PUT');assert request(path+'/web',token)==disabled
 request(path+'/web',token,web,'PUT',409)
 request(path+'/web',token,{'requestsPerMinute':-1,'revision':disabled['revision']},'PUT',400)
 checks.append('web_limit_disabled_roundtrip_and_stale_validation')
 seed=request(native+'/api/settings',nsu);seed['rateLimits'].update(enabled=True,excludedIPs=['127.0.0.2'])
 request(native+'/api/settings',nsu,{'rateLimits':seed['rateLimits']},'PATCH')
 before_native=request(native+'/api/settings',nsu);accounts_before=request(native+'/api/collections/_superusers/records',nsu)['totalItems']
 value=request(dbpath,token);assert value['enabled']is True and value['excludedIPCount']==1
 desired={'enabled':False,'rules':[{'label':'/api/','audience':'@guest','duration':15,'maxRequests':600}],'revision':value['revision']}
 updated=request(dbpath,token,desired,'PUT');assert updated['enabled']is False;assert request(dbpath,token)==updated
 after_native=request(native+'/api/settings',nsu)
 assert {k:v for k,v in before_native.items()if k!='rateLimits'}=={k:v for k,v in after_native.items()if k!='rateLimits'}
 assert after_native['rateLimits']['excludedIPs']==['127.0.0.2'];assert after_native['rateLimits']['rules']==desired['rules']
 assert request(native+'/api/collections/_superusers/records',nsu)['totalItems']==accounts_before
 request(dbpath,token,desired,'PUT',409)
 request(dbpath,token,{**desired,'revision':updated['revision'],'enabled':'false'},'PUT',400)
 assert request(dbpath,token)==updated
 assert not any(word in json.dumps(updated).lower()for word in ['password','token','smtp','secret'])
 assert not list((controller/'data').glob('.request-limits-*.json'))
 checks.append('native_sso_roundtrip_preserves_accounts_other_settings_and_ip_exceptions')
 request(base+'/api/collections/instances/records/'+instance,su,{'power':False},'PATCH');request(dbpath,token,status=400)
 checks.append('stopped_database_not_started_or_modified')
 print(json.dumps({'ok':True,'checks':checks}))
except Exception:
 print('Private test evidence:',root,file=__import__('sys').stderr);raise
finally:
 for proc in reversed(processes):
  if proc.poll()is None:proc.terminate();proc.wait(timeout=10)
 # Keep failed fixture logs private for diagnosis; success contains no reusable credentials.
 if 'stopped_database_not_started_or_modified'in checks:shutil.rmtree(root)
