#!/usr/bin/env python3
"""Disposable native controller: operator-only monitoring, stale/missing snapshots and secret-free output."""
import argparse,json,os,pathlib,secrets,shutil,socket,sqlite3,subprocess,tempfile,time,urllib.request,urllib.error
p=argparse.ArgumentParser();p.add_argument('--source',type=pathlib.Path,required=True);p.add_argument('--pocketbase',required=True);a=p.parse_args();os.umask(0o077)
temporary=tempfile.TemporaryDirectory(prefix='kolab-monitor-api-');root=pathlib.Path(temporary.name);process=None
with socket.socket()as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
def api(path,body=None,token='',status=200,method=None,headers=None):
 req=urllib.request.Request('http://127.0.0.1:'+str(port)+path,data=None if body is None else json.dumps(body).encode(),method=method,headers={'Content-Type':'application/json',**({'Authorization':token}if token else{}),**(headers or{})})
 try:
  with urllib.request.urlopen(req,timeout=15)as response:
   assert response.status==status;return json.load(response)
 except urllib.error.HTTPError as e:
  assert e.code==status,(path,e.code,e.read().decode());return None
try:
 source=a.source/'packages/pockethost/src/mothership-app';hooks=root/'hooks';hooks.mkdir();shutil.copy(source/'pb_hooks/mothership.js',hooks/'mothership.js')
 (hooks/'monitor.pb.js').write_text("routerAdd('GET','/api/admin/monitoring',e=>require(`${__hooks}/mothership`).HandleOperatorMonitoring(e),$apis.requireAuth('users'));routerAdd('PATCH','/api/admin/monitoring',e=>require(`${__hooks}/mothership`).HandleOperatorMonitoringUpdate(e),$apis.requireAuth('users'));routerAdd('GET','/api/admin/_monitoring/config',e=>require(`${__hooks}/mothership`).HandleOperatorMonitoringDeliveryConfig(e),$apis.requireAuth('_superusers'))")
 env={**os.environ,'DATA_ROOT':str(root),'WEB_APPS_ENABLED':'false','WEB_APPS_MASTER_KEY':'m'*32};password='P'+secrets.token_urlsafe(32);data=root/'data'
 subprocess.run([a.pocketbase,'superuser','upsert','monitor@example.invalid',password,'--dir',str(data),'--hooksDir',str(root/'empty'),'--migrationsDir',str(source/'pb_migrations')],env=env,check=True,capture_output=True)
 with(root/'process.log').open('wb')as log:process=subprocess.Popen([a.pocketbase,'serve','--http','127.0.0.1:'+str(port),'--dir',str(data),'--hooksDir',str(hooks),'--migrationsDir',str(source/'pb_migrations')],env=env,stdout=log,stderr=subprocess.STDOUT)
 for _ in range(100):
  try:api('/api/health');break
  except OSError:time.sleep(.1)
 su=api('/api/collections/_superusers/auth-with-password',{'identity':'monitor@example.invalid','password':password})['token'];tokens={}
 for name,admin,suspended in [('admin',True,''),('member',False,''),('suspended',True,'paused')]:
  api('/api/collections/users/records',{'email':name+'@example.invalid','username':name,'password':password,'passwordConfirm':password,'verified':True,'superAdmin':admin,'suspension':suspended,'subscription':'premium','subscription_quantity':1},su)
  tokens[name]=api('/api/collections/users/auth-with-password',{'identity':name+'@example.invalid','password':password})['token']
 path='/api/admin/monitoring';api(path,status=401);api(path,token=tokens['member'],status=403);api(path,token=tokens['suspended'],status=403)
 missing=api(path,token=tokens['admin']);assert not missing['available']and missing['stale']and missing['report']is None
 settings=missing['settings'];assert not settings['emailEnabled']and not settings['discordEnabled']and'discordWebhook'not in settings
 api('/api/collections/platform_monitoring_settings/records',token=tokens['member'],status=403)
 api(path,{'revision':''},tokens['member'],status=403,method='PATCH')
 for body in [{'revision':'','discordEnabled':True},{'revision':'','discordWebhook':'http://127.0.0.1:8091/private'},{'revision':'','emailEnabled':True,'emailRecipients':['person@example.invalid']},{'revision':'','emailEnabled':'true'}]:api(path,body,tokens['admin'],status=400,method='PATCH')
 webhook='https://discord.com/api/webhooks/123456789012345678/'+'x'*60
 saved=api(path,{'revision':'','discordEnabled':True,'discordWebhook':webhook},tokens['admin'],method='PATCH')['settings'];assert saved['hasDiscordWebhook']and saved['discordEnabled']and webhook not in json.dumps(saved)
 with sqlite3.connect(data/'data.db')as db:encrypted=db.execute('select discordWebhookEncrypted from platform_monitoring_settings').fetchone()[0]
 assert encrypted and webhook not in encrypted
 private='/api/admin/_monitoring/config';api(private,status=401);api(private,token=tokens['admin'],status=403);api(private,token=su,status=403,headers={'X-Forwarded-For':'127.0.0.1'});assert api(private,token=su)['discordWebhook']==webhook
 api(path,{'revision':'','discordEnabled':False},tokens['admin'],status=400,method='PATCH')
 preserved=api(path,{'revision':saved['revision'],'discordWebhook':''},tokens['admin'],method='PATCH')['settings'];assert preserved['hasDiscordWebhook']
 disabled=api(path,{'revision':preserved['revision'],'discordEnabled':False,'clearDiscordWebhook':True},tokens['admin'],method='PATCH')['settings'];assert not disabled['hasDiscordWebhook']
 native=api('/api/settings',token=su);native['smtp'].update(enabled=True,host='127.0.0.1',port=25252,username='fixture',password='fixture-secret',tls=False);native['meta'].update(senderName='Fixture',senderAddress='sender@example.invalid');api('/api/settings',{'smtp':native['smtp'],'meta':native['meta']},su,method='PATCH')
 both=api(path,{'revision':disabled['revision'],'emailEnabled':True,'emailRecipients':['recipient@example.invalid'],'discordEnabled':True,'discordWebhook':webhook},tokens['admin'],method='PATCH')['settings'];assert both['emailEnabled']and both['discordEnabled']and both['smtpReady']
 assert api(private,token=su)['smtp']['password']=='fixture-secret'
 folder=root/'monitoring';folder.mkdir();report={'version':1,'checkedAt':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'host':'test','domain':'app.example.invalid','password':'not-public','state':'ok','counts':{'error':0},'checks':[{'id':'database:test','group':'databases','name':'BDD test','state':'error','detail':'HTTP 503','token':'not-public','href':'https://outside.invalid'}]};(folder/'status.json').write_text(json.dumps(report))
 result=api(path,token=tokens['admin']);assert result['available']and not result['stale']and result['report']['state']=='error';assert 'not-public'not in json.dumps(result)and'href'not in result['report']['checks'][0]
 report['checkedAt']='2000-01-01T00:00:00Z';(folder/'status.json').write_text(json.dumps(report));assert api(path,token=tokens['admin'])['stale']
 (folder/'status.json').write_text('{bad');assert not api(path,token=tokens['admin'])['available']
 print(json.dumps({'passed':True,'checks':['auth_required','operator_only','suspended_denied','missing_not_healthy','current_report','recompute_summary','no_secret_or_external_link','stale_visible','corrupt_not_healthy','channels_default_off','locked_channel_collection','operator_only_configuration','strict_toggles','smtp_required_for_email','discord_url_allowlist','webhook_never_returned','revision_conflict','blank_preserves_webhook','explicit_webhook_removal']}))
except Exception:
 if(root/'process.log').exists():print((root/'process.log').read_text()[-2000:])
 raise
finally:
 if process:
  process.terminate()
  try:process.wait(timeout=10)
  except subprocess.TimeoutExpired:process.kill();process.wait()
 temporary.cleanup()
