#!/usr/bin/env python3
"""Actual disposable PocketBase test: admin APIs, secret storage, webhook and worker protocol."""
import argparse,pathlib,tempfile,subprocess,json,os,urllib.request,urllib.error,secrets,socket,shutil,time,hmac,hashlib,sys
parser=argparse.ArgumentParser();parser.add_argument('--source',type=pathlib.Path,required=True);parser.add_argument('--pocketbase',required=True);args=parser.parse_args()
os.umask(0o077);root=pathlib.Path(tempfile.mkdtemp(prefix='ph-web-api-test-')).resolve();process=None;checks=[]
with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
base=f'http://127.0.0.1:{port}';worker=secrets.token_urlsafe(32)
def req(path,t='',body=None,method=None,status=200,headers=None,raw=None):
 request=urllib.request.Request(base+path,method=method or ('POST' if body is not None or raw is not None else 'GET'),headers={'Content-Type':'application/json',**({'Authorization':t}if t else{}),**(headers or{})},data=raw if raw is not None else None if body is None else json.dumps(body).encode())
 try:
  with urllib.request.urlopen(request,timeout=30) as r:
   data=r.read();assert r.status==status,(path,r.status,status);return json.loads(data) if data else None
 except urllib.error.HTTPError as e:
  if e.code==status:return None
  raise AssertionError(f'{path}: {e.code} '+e.read().decode())
try:
 source=args.source/'packages/pockethost/src/mothership-app';hooks=root/'hooks';hooks.mkdir();shutil.copy(source/'pb_hooks/mothership.js',hooks/'mothership.js');shutil.copy(source/'src/lib/handlers/webApps/hooks.ts',hooks/'web.pb.js')
 with (hooks/'web.pb.js').open('a') as hook:hook.write("\nonRecordUpdate(e=>{let reject=false;try{$os.stat($os.getenv('DATA_ROOT')+'/reject-terminal');reject=true}catch{}if(reject&&e.record.getString('status')==='ready')throw new BadRequestError('Simulated terminal failure');e.next()},'web_deployments')\n")
 env={**os.environ,'WEB_APPS_ENABLED':'true','WEB_APPS_ROOT':str(root/'web'),'WEB_APPS_MASTER_KEY':'a'*32,'WEB_APPS_WORKER_SECRET':worker,'APEX_DOMAIN':'example.invalid','APP_HOST':'app.example.invalid','DATA_ROOT':str(root/'data')}
 tools=root/'tools';tools.mkdir();shim=tools/'python3';shim.write_text('#!'+sys.executable+'\nimport sys,json,pathlib,os\np=pathlib.Path(sys.argv[-1])\ntry:d=json.loads(p.read_text())\nexcept Exception:d={}\nif d.get("action")=="commit":\n p.unlink();print(json.dumps({"sha":"d"*40}));sys.exit()\nos.execv('+repr(sys.executable)+',[ '+repr(sys.executable)+' ]+sys.argv[1:])\n');shim.chmod(0o700);env['PATH']=str(tools)+':'+env['PATH']
 password='P'+secrets.token_urlsafe(32);email='operator@example.invalid';db=root/'pb_data'
 subprocess.run([args.pocketbase,'superuser','upsert',email,password,'--dir',str(db),'--hooksDir',str(root/'empty-hooks'),'--migrationsDir',str(source/'pb_migrations')],env=env,check=True,capture_output=True)
 log=(root/'process.log').open('wb');process=subprocess.Popen([args.pocketbase,'serve','--dir',str(db),'--hooksDir',str(hooks),'--migrationsDir',str(source/'pb_migrations'),'--http',f'127.0.0.1:{port}'],env=env,stdout=log,stderr=subprocess.STDOUT)
 for _ in range(100):
  if process.poll() is not None:raise RuntimeError('Startup failed: '+(root/'process.log').read_text()[-3000:])
  try:req('/api/health');break
  except OSError:time.sleep(.1)
 su=req('/api/collections/_superusers/auth-with-password',body={'identity':email,'password':password})['token']
 user=req('/api/collections/users/records',su,{'email':email,'password':password,'passwordConfirm':password,'username':'webtest','verified':True,'superAdmin':False,'subscription':'premium','subscription_quantity':10})
 token=req('/api/collections/users/auth-with-password',body={'identity':email,'password':password})['token']
 req('/api/web-apps',status=401);assert req('/api/web-apps',token)['items']==[];req('/api/web-apps',token,{'name':'Denied','repository':'owner/repository'},status=403);req('/api/collections/web_apps/records',token,status=403)
 checks.append('anonymous_nonadmin_and_raw_collection_denied')
 req('/api/collections/users/records/'+user['id'],su,{'superAdmin':True},'PATCH');token=req('/api/collections/users/auth-with-password',body={'identity':email,'password':password})['token']
 assert req('/api/web-apps',token)['items']==[]
 app=req('/api/web-apps',token,{'name':'Sample','repository':'owner/repository','branch':'main','env':[{'name':'API_SECRET','value':'private-env-value','build':True,'runtime':True}]},status=201)['app'];ident=app['id']
 assert app['generatedDomain']=='web-'+ident+'.example.invalid';assert 'private-env-value' not in json.dumps(app)
 req('/api/web-apps/'+ident,token,{'env':[{'name':'API_SECRET','value':'','build':True,'runtime':True}]},'PATCH')
 req('/api/web-apps/'+ident,token,{'rootDirectory':'../../private'},'PATCH',400)
 req('/api/web-apps/'+ident,token,{'repository':'https://evil.invalid/x'},'PATCH',400)
 req('/api/web-apps/'+ident,token,{'env':[{'name':'NODE_OPTIONS','value':'bad'}]},'PATCH',400)
 checks.append('metadata_roundtrip_env_masked_preserved_and_paths_validated')
 secret='webhook-secret-test-'+secrets.token_urlsafe(32)
 s=req('/api/web-apps/settings',token,{'githubAppId':'123','githubAppSlug':'example-app','githubInstallationId':'456','webhookSecret':secret},'PUT')['settings'];assert s['hasWebhookSecret'];assert secret not in json.dumps(s)
 before=req('/api/collections/web_apps/records/'+ident,su);assert 'private-env-value' not in json.dumps(before) # native superuser can inspect ciphertext, never plaintext
 req('/api/web-apps/_worker/claim',body={},status=401)
 body=json.dumps({'ref':'refs/heads/main','after':'c'*40,'repository':{'full_name':'owner/repository'},'installation':{'id':456}}).encode();signature='sha256='+hmac.new(secret.encode(),body,hashlib.sha256).hexdigest();headers={'X-GitHub-Delivery':'delivery-1','X-GitHub-Event':'push','X-Hub-Signature-256':signature}
 req('/api/web-apps/github/webhook',raw=body,headers={**headers,'X-Hub-Signature-256':'bad'},status=401)
 result=req('/api/web-apps/github/webhook',raw=body,headers=headers,status=202);assert result['queued']==1
 duplicate=req('/api/web-apps/github/webhook',raw=body,headers=headers);assert duplicate['duplicate']
 replayed=req('/api/web-apps/github/webhook',raw=body,headers={**headers,'X-GitHub-Delivery':'unsigned-new-guid'});assert replayed['duplicate'] # X-GitHub-Delivery is unsigned: the key is the signed body
 checks.append('signed_webhook_queues_and_deduplicates')
 wh={'X-Web-Worker':worker};job=req('/api/web-apps/_worker/claim',body={},headers=wh)['job'];assert set(job['github'])=={'githubAppId','githubInstallationId','privateKey'};assert 'webhookSecret'not in job['github'];assert job['commit']=='c'*40;assert job['snapshot']['env']['API_SECRET']['value']=='private-env-value';assert req('/api/web-apps/_worker/claim',body={},headers=wh)['job'] is None
 assert req('/api/web-apps/_worker/report',body={'id':job['id'],'inspect':True},headers=wh)['status']=='running'
 (root/'data').mkdir(exist_ok=True);(root/'data/reject-terminal').write_text('1')
 req('/api/web-apps/_worker/report',body={'id':job['id'],'status':'ready','result':{'image':'ph-web/test:v1'}},headers=wh,status=400)
 assert req('/api/web-apps/'+ident,token)['app']['activeDeployment']==''
 assert req('/api/web-apps/_worker/report',body={'id':job['id'],'inspect':True},headers=wh)['status']=='running'
 (root/'data/reject-terminal').unlink();checks.append('terminal_failure_rolls_back_app_and_job_transaction')
 req('/api/web-apps/_worker/report',body={'id':job['id'],'status':'ready','result':{'image':'ph-web/test:v1','target':'http://127.0.0.1:12345'}},headers=wh)
 app=req('/api/web-apps/'+ident,token)['app'];assert app['activeDeployment']==job['id'];assert app['status']=='running';assert 'private-env-value' not in json.dumps(app)
 checks.append('single_worker_claim_decrypts_only_for_worker_and_commits_ready')
 rollback=req('/api/web-apps/'+ident+'/rollback',token,{'deploymentId':job['id']},status=202)['deployment'];assert rollback['action']=='rollback'
 running=req('/api/web-apps/_worker/claim',body={},headers=wh)['job'];req('/api/web-apps/_worker/report',body={'id':running['id'],'status':'failed','error':'Controlled failure'},headers=wh)
 assert req('/api/web-apps/'+ident,token)['app']['activeDeployment']==job['id'];checks.append('failed_rollback_retains_published_version')
 # No queued action accepts arbitrary snapshots or commits through the public API.
 stop=req('/api/web-apps/'+ident+'/stop',token,{'config':{'rootDirectory':'../../'},'env':{}},status=202)['deployment'];running=req('/api/web-apps/_worker/claim',body={},headers=wh)['job'];assert running['snapshot']['config']['rootDirectory']=='.'
 req('/api/web-apps/_worker/report',body={'id':running['id'],'status':'ready'},headers=wh);assert req('/api/web-apps/'+ident,token)['app']['status']=='stopped';checks.append('public_action_cannot_override_job_snapshot')
 req('/api/collections/instances/records',su,{'uid':user['id'],'subdomain':'web-reserved','status':'idle','version':'0.39.*'},status=400)
 native=req('/api/collections/instances/records',su,{'uid':user['id'],'subdomain':'native-db','status':'idle','version':'0.39.*'})
 req('/api/collections/instances/records/'+native['id'],su,{'subdomain':'web-reserved'},'PATCH',400)
 req('/api/collections/web_app_domains/records',su,{'appId':ident,'host':'www.example.com','status':'pending_dns','ownershipToken':'a'*40})
 req('/api/collections/instances/records/'+native['id'],su,{'cname':'WWW.EXAMPLE.COM.'},'PATCH',400)
 req('/api/collections/instances/records/'+native['id'],su,{'power':False},'PATCH');checks.append('native_instance_requests_cannot_steal_web_namespace_or_domains')
 req('/api/web-apps/settings',token,{'privateKey':'-----BEGIN PRIVATE KEY-----\nfixture-only\n-----END PRIVATE KEY-----'},'PUT')
 payload={'ref':'refs/heads/main','after':'e'*40,'repository':{'full_name':'owner/repository'},'installation':{'id':456}}
 raw=json.dumps(payload).encode();sig='sha256='+hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
 r=req('/api/web-apps/github/webhook',raw=raw,headers={**headers,'X-GitHub-Delivery':'delivery-newer','X-Hub-Signature-256':sig},status=202);assert r['queued']==1
 headjob=req('/api/web-apps/_worker/claim',body={},headers=wh)['job'];assert headjob['commit']=='d'*40
 payload['after']='c'*40;payload['before']='b'*40;raw=json.dumps(payload).encode();sig='sha256='+hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
 r=req('/api/web-apps/github/webhook',raw=raw,headers={**headers,'X-GitHub-Delivery':'delivery-delayed-old','X-Hub-Signature-256':sig},status=202);assert r['queued']==0
 assert req('/api/web-apps/'+ident,token)['app']['desiredCommit']=='d'*40
 req('/api/web-apps/_worker/report',body={'id':headjob['id'],'status':'failed','error':'Fixture complete'},headers=wh)
 checks.append('out_of_order_signed_push_resolves_branch_head_and_deduplicates_running_commit')
 # Diagnostics retain trusted metadata only; an invalid signature cannot poison the dedupe key.
 invalid_raw=json.dumps({'ref':'refs/heads/main','after':'f'*40,'repository':{'full_name':'owner/repository'},'installation':{'id':456}}).encode()
 req('/api/web-apps/github/webhook',raw=invalid_raw,headers={**headers,'X-GitHub-Delivery':'not-poisoned','X-Hub-Signature-256':'bad'},status=401)
 req('/api/web-apps/github/webhook',raw=invalid_raw,headers={**headers,'X-GitHub-Delivery':'not-poisoned','X-Hub-Signature-256':'sha256='+hmac.new(secret.encode(),invalid_raw,hashlib.sha256).hexdigest()},status=202)
 diag=req('/api/web-apps/'+ident+'/webhooks',token)['items'];assert any(x['deliveryId']=='gh-default-'+hashlib.sha256(invalid_raw).hexdigest()for x in diag);assert next(x for x in diag if x['deliveryId']=='gh-default-'+hashlib.sha256(body).hexdigest())['duplicateCount']==2
 assert all('private-env-value'not in json.dumps(x)and secret not in json.dumps(x)for x in diag)
 checks.append('webhook_diagnostics_are_trusted_bounded_metadata_not_payloads')
 # Cancel a queued immutable retry. It cannot be claimed or change the published version.
 req('/api/web-apps/'+ident,token,{'repository':'owner/renamed','branch':'other','env':[{'name':'API_SECRET','value':'edited-secret','build':True,'runtime':True}]},'PATCH')
 q=req('/api/web-apps/'+ident+'/deployments/'+headjob['id']+'/retry',token,{'commit':'f'*40,'snapshotEncrypted':'forged'},status=202)['deployment'];assert q['retriedFrom']==headjob['id']and q['commit']=='d'*40
 cancelled=req('/api/web-apps/'+ident+'/deployments/'+q['id']+'/cancel',token,{},status=202)['deployment'];assert cancelled['status']=='cancelled'and cancelled['cancelRequested']
 assert req('/api/web-apps/_worker/claim',body={},headers=wh)['job']is None
 req('/api/web-apps/'+ident+'/deployments/'+q['id']+'/cancel',token,{},status=400)
 checks.append('queued_cancellation_is_terminal_and_cannot_be_claimed')
 q=req('/api/web-apps/'+ident+'/deployments/'+q['id']+'/retry',token,{},status=202)['deployment'];run=req('/api/web-apps/_worker/claim',body={},headers=wh)['job'];assert run['id']==q['id'];assert run['snapshot']['config']['repository']=='owner/repository'and run['snapshot']['config']['branch']=='main'and run['snapshot']['env']['API_SECRET']['value']=='private-env-value'
 cancel=req('/api/web-apps/'+ident+'/deployments/'+run['id']+'/cancel',token,{},status=202)['deployment'];assert cancel['status']=='running'and cancel['cancelRequested']
 heartbeat=req('/api/web-apps/_worker/report',body={'id':run['id'],'stageKey':'publish','stage':'Publication'},headers=wh);assert heartbeat['cancelRequested']and not heartbeat['publicationStartedAt']
 req('/api/web-apps/_worker/report',body={'id':run['id'],'status':'ready','result':{'image':'must-not-publish'}},headers=wh,status=400)
 req('/api/web-apps/_worker/report',body={'id':run['id'],'status':'failed','error':'Cancelled work'},headers=wh)
 record=next(j for j in req('/api/web-apps/'+ident+'/deployments',token)['items']if j['id']==run['id']);assert record['status']=='cancelled'and record['error']==''
 assert req('/api/web-apps/'+ident,token)['app']['activeDeployment']==job['id']
 checks.append('running_cancellation_prevents_publication_and_retry_preserves_original_snapshot')
 q=req('/api/web-apps/'+ident+'/deployments/'+run['id']+'/retry',token,{},status=202)['deployment'];run=req('/api/web-apps/_worker/claim',body={},headers=wh)['job']
 for key,label in [('build','Construction Next.js'),('health','Contrôle de santé'),('publish','Publication')]:
  heartbeat=req('/api/web-apps/_worker/report',body={'id':run['id'],'stageKey':key,'stage':label},headers=wh)
 assert heartbeat['publicationStartedAt']and not heartbeat['cancelRequested']
 req('/api/web-apps/'+ident+'/deployments/'+run['id']+'/cancel',token,{},status=400)
 req('/api/web-apps/_worker/report',body={'id':run['id'],'status':'ready','stage':'Terminé','result':{'image':'ph-web/test:v2'}},headers=wh)
 record=next(j for j in req('/api/web-apps/'+ident+'/deployments',token)['items']if j['id']==run['id']);assert [p['key']for p in record['phases']]==['fetch','build','health','publish'];assert all(p.get('finishedAt')and p['durationMs']>=0 for p in record['phases'])
 assert record['heartbeatAt']and not record['cancellable'];published=run['id']
 checks.append('publication_gate_serializes_cancel_race_and_records_real_phase_timings')
 # A controller claim lost before the worker marker exists must not block all later deployments forever.
 q=req('/api/web-apps/'+ident+'/deploy',token,{},status=202)['deployment'];run=req('/api/web-apps/_worker/claim',body={},headers=wh)['job']
 req('/api/collections/web_deployments/records/'+run['id'],su,{'heartbeatAt':'2000-01-01T00:00:00.000Z'},'PATCH')
 assert req('/api/web-apps/_worker/claim',body={},headers=wh)['job']is None
 record=next(j for j in req('/api/web-apps/'+ident+'/deployments',token)['items']if j['id']==run['id']);assert record['status']=='failed'and record['stage']=='Interrompu'
 assert req('/api/web-apps/'+ident,token)['app']['activeDeployment']==published
 checks.append('expired_worker_claim_recovers_without_replacing_published_site')
 # Incremental log cursor uses byte offsets, handles Unicode and log rewrite, and refuses symlinks.
 logs=root/'web/logs';logs.mkdir(parents=True,exist_ok=True);logfile=logs/(run['id']+'.log');logfile.write_text('x'*200000)
 url='/api/web-apps/'+ident+'/deployments/'+run['id']+'/logs'
 first=req(url,token);assert len(first['text'])==65536 and first['truncated']and first['cursor']
 with logfile.open('a')as out:out.write('nouvelle ligne é\n')
 second=req(url+'?cursor='+first['cursor'],token);assert second['text']=='nouvelle ligne é\n'and not second['reset']
 empty=req(url+'?cursor='+second['cursor'],token);assert empty['text']==''
 logfile.write_text('rotated');rotated=req(url+'?cursor='+second['cursor'],token);assert rotated['reset']and rotated['text']=='rotated'
 logfile.unlink();private=root/'private.txt';private.write_text('private-file-must-not-be-read');logfile.symlink_to(private);req(url,token,status=400)
 checks.append('incremental_logs_bound_reads_follow_utf8_reset_after_rotation_and_refuse_symlinks')
 (root/'web/runtime-status.json').write_text(json.dumps({'applications':{ident:{'available':True,'checkedAt':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'state':'running','cpuPercent':2.3,'memoryBytes':123,'memoryLimitBytes':456,'pids':7,'env':'not-returned'},'other-tenant':{'memoryBytes':999999}}}))
 stats=req('/api/web-apps/'+ident+'/runtime',token);assert stats['available']and stats['scope']=='application-container'and stats['memoryBytes']==123;assert 'env'not in stats and '999999'not in json.dumps(stats)
 (root/'web/runtime-status.json').write_text(json.dumps({'applications':{ident:{'available':True,'checkedAt':'2000-01-01T00:00:00Z','memoryBytes':123}}}));assert not req('/api/web-apps/'+ident+'/runtime',token)['available']
 checks.append('runtime_stats_are_per_application_allowlisted_and_expire')
 # Retry uses current domain ownership rather than resurrecting the old domain snapshot.
 domain=req('/api/collections/web_app_domains/records',su,{'appId':ident,'host':'reassigned.example.org','status':'active','ownershipToken':'z'*40,'certificateExpiresAt':'2030-01-01T00:00:00.000Z'})
 q=req('/api/web-apps/'+ident+'/deploy',token,{},status=202)['deployment'];run=req('/api/web-apps/_worker/claim',body={},headers=wh)['job'];assert any(d['host']=='reassigned.example.org'for d in run['snapshot']['domains'])
 req('/api/web-apps/_worker/report',body={'id':run['id'],'status':'failed','error':'Fixture domain move'},headers=wh)
 other=req('/api/web-apps',token,{'name':'Other','repository':'owner/other'},status=201)['app'];req('/api/collections/web_app_domains/records/'+domain['id'],su,{'appId':other['id']},'PATCH')
 q=req('/api/web-apps/'+ident+'/deployments/'+run['id']+'/retry',token,{},status=202)['deployment'];run=req('/api/web-apps/_worker/claim',body={},headers=wh)['job'];assert not any(d['host']=='reassigned.example.org'for d in run['snapshot']['domains'])
 req('/api/web-apps/_worker/report',body={'id':run['id'],'status':'failed','error':'Fixture complete'},headers=wh)
 req('/api/web-apps/'+ident+'/deployments/'+rollback['id']+'/retry',token,{},status=400)
 checks.append('retry_refreshes_domains_and_cannot_reclaim_a_reassigned_hostname')
 # Backup pins retain more than five images, with both tag and immutable SHA references.
 kept=['ph-web/'+ident+':'+('ref'+str(index).zfill(12))for index in range(12)]+['sha256:'+'e'*64]
 missing='ph-web/'+ident+':'+'r'*15
 retention_rows=[req('/api/collections/web_deployments/records',su,{'appId':ident,'actorId':user['id'],'action':'deploy','status':'ready','result':{'image':image,'available':True}})for image in kept+[missing]]
 req('/api/web-apps/_worker/report',body={'id':retention_rows[0]['id'],'retainedImages':kept},headers=wh)
 for index,row in enumerate(retention_rows):assert req('/api/collections/web_deployments/records/'+row['id'],su)['result']['available']==(index<len(kept))
 foreign='ph-web/'+other['id']+':'+'f'*15
 req('/api/web-apps/_worker/report',body={'id':retention_rows[0]['id'],'retainedImages':kept+[foreign]},headers=wh,status=400)
 assert req('/api/collections/web_deployments/records/'+retention_rows[0]['id'],su)['result']['available']is True
 checks.append('retained_images_accept_more_than_ten_scoped_tags_and_sha_ids_reject_foreign_app_and_reconcile_availability')
 print(json.dumps({'passed':True,'checks':checks}))
finally:
 if process:
  process.terminate()
  try:process.wait(timeout=10)
  except subprocess.TimeoutExpired:process.kill();process.wait()
 shutil.rmtree(root)
