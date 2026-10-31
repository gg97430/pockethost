"""Disposable native PocketBase: Cloudflare authorization, encrypted previews, confirmation and replay."""
import argparse,json,os,pathlib,secrets,shutil,socket,subprocess,tempfile,time,urllib.request,urllib.error,sqlite3
p=argparse.ArgumentParser();p.add_argument('--source',type=pathlib.Path,required=True);p.add_argument('--pocketbase',required=True);args=p.parse_args()
with tempfile.TemporaryDirectory(prefix='kolab-cloudflare-') as tmp:
 root=pathlib.Path(tmp).resolve();source=args.source/'packages/pockethost/src/mothership-app';hooks=root/'hooks';hooks.mkdir();empty=root/'empty';empty.mkdir();web=root/'web';web.mkdir();(web/'worker-status.json').write_text(json.dumps({'httpsReady':False}))
 shutil.copy(source/'pb_hooks/mothership.js',hooks/'mothership.js')
 h=(source/'src/lib/handlers/webApps/hooks.ts').read_text();h=h[h.rfind("routerAdd(",0,h.index("/api/web-apps/cloudflare/settings")): ]
 (hooks/'web.pb.js').write_text(h+"\nrouterAdd('POST','/api/web-apps/{id}/domains/{domainId}/verify',e=>require(`${__hooks}/mothership`).HandleWebDomainVerify(e),$apis.requireAuth('users'))")
 helper=root/'helper.py';helper.write_text('''import json,sys,pathlib
p=pathlib.Path(sys.argv[1]);d=json.loads(p.read_text());p.unlink()
r={'hostname':d['hostname'],'zone':{'id':'zone','name':'lapub.re'},'current':[],'changes':[{'action':'create','before':None,'after':{'type':'A','name':d['hostname'],'content':d['publicIp']}}],'revision':'revision1'}
if d['action']=='apply':
 with open(__LOG__,'a') as f:f.write('applied\\n')
 r={'applied':True,'changes':1}
print(json.dumps({'result':r}))
'''.replace('__LOG__',repr(str(root/'apply.log'))))
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 env={**os.environ,'WEB_APPS_ENABLED':'true','WEB_APPS_MASTER_KEY':'k'*32,'WEB_APPS_ROOT':str(web),'WEB_APPS_PUBLIC_IP':'135.125.67.39','WEB_APPS_CLOUDFLARE_HELPER':str(helper),'MOTHERSHIP_CLOUDFLARE_API_TOKEN':'secret-canary-cloudflare-token','APEX_DOMAIN':'example.invalid','APP_HOST':'app.example.invalid','PH_SUPPORT_EMAIL':'support@example.invalid','DATA_ROOT':str(root/'data')}
 password='P'+secrets.token_urlsafe(24);data=root/'pb_data';migrations=source/'pb_migrations'
 subprocess.run([args.pocketbase,'superuser','upsert','su@example.invalid',password,'--dir',str(data),'--hooksDir',str(empty),'--migrationsDir',str(migrations)],env=env,check=True,capture_output=True)
 log=(root/'log').open('wb');process=subprocess.Popen([args.pocketbase,'serve','--dir',str(data),'--hooksDir',str(hooks),'--migrationsDir',str(migrations),'--http',f'127.0.0.1:{port}'],env=env,stdout=log,stderr=subprocess.STDOUT)
 responses=[]
 def req(path,token='',body=None,method=None,status=200):
  q=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=None if body is None else json.dumps(body).encode(),method=method,headers={'Content-Type':'application/json',**({'Authorization':token} if token else {})})
  try:
   with urllib.request.urlopen(q,timeout=20) as r:code=r.status;text=r.read()
  except urllib.error.HTTPError as e:code=e.code;text=e.read()
  assert code==status,(path,code,text[:500]);responses.append(text.decode());return json.loads(text)
 try:
  for _ in range(100):
   if process.poll() is not None:raise RuntimeError((root/'log').read_text()[-2500:])
   try:req('/api/health');break
   except OSError:time.sleep(.1)
  su=req('/api/collections/_superusers/auth-with-password',body={'identity':'su@example.invalid','password':password})['token']
  def user(name,admin):
   r=req('/api/collections/users/records',su,{'email':name+'@example.invalid','username':name,'password':password,'passwordConfirm':password,'verified':True,'superAdmin':admin,'subscription':'premium','subscription_quantity':5})
   return r['id'],req('/api/collections/users/auth-with-password',body={'identity':r['email'],'password':password})['token']
  uid,admin=user('admin',True);_,other=user('other',True);_,member=user('member',False)
  app=req('/api/collections/web_apps/records',su,{'name':'DNS test','uid':uid,'status':'running','config':{'repository':'acme/web','repositoryId':1,'branch':'main','rootDirectory':'.','healthPath':'/'}})['id']
  domain=req('/api/collections/web_app_domains/records',su,{'appId':app,'host':'1.lapub.re','status':'pending_dns','ownershipToken':'x'*40})['id'];base=f'/api/web-apps/{app}/domains/{domain}'
  req('/api/web-apps/cloudflare/settings',status=401);req('/api/web-apps/cloudflare/settings',member,status=403)
  status=req('/api/web-apps/cloudflare/settings',admin);assert status['configured'] and not status['https']['ready']
  req(base+'/verify',admin,{},status=400)
  req(base+'/cloudflare/preview',member,{},status=403)
  preview=req(base+'/cloudflare/preview',admin,{})
  req(base+'/cloudflare/apply',admin,{'previewId':preview['previewId'],'confirmHostname':'wrong.lapub.re'},status=400)
  req(base+'/cloudflare/apply',other,{'previewId':preview['previewId'],'confirmHostname':'1.lapub.re'},status=400)
  assert not (root/'apply.log').exists()
  req(base+'/cloudflare/apply',admin,{'previewId':preview['previewId'],'confirmHostname':'1.lapub.re'})
  # Reset rate only in isolated test DB, so replay rejection is checked independently of rate limiter.
  time.sleep(.05)
  req(base+'/cloudflare/apply',admin,{'previewId':preview['previewId'],'confirmHostname':'1.lapub.re'},status=400)
  assert (root/'apply.log').read_text()=='applied\n'
  req('/api/web-apps/cloudflare/settings',admin,{'token':'new-cloudflare-secret-'+'x'*30},method='PUT')
  assert 'new-cloudflare-secret-' not in ''.join(responses) and 'secret-canary-cloudflare-token' not in ''.join(responses)
  with sqlite3.connect(data/'data.db') as c:assert 'new-cloudflare-secret-' not in c.execute("select valueEncrypted from web_app_settings where key='cloudflare'").fetchone()[0]
  print('Native Cloudflare API: auth, permission, HTTPS preflight, preview, explicit hostname, actor binding, one-time apply, encrypted/redacted token passed')
 finally:process.terminate();process.wait(timeout=10);log.close()
