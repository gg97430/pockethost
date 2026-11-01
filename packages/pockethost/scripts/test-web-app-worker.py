#!/usr/bin/env python3
import unittest,tempfile,pathlib,importlib.util,json,unittest.mock as mock,io,tarfile,subprocess,time,sys,threading,types,os,http.server,socketserver
from web_app_runtime import ResourcePressure
SOURCE=pathlib.Path(__file__).with_name('web-app-worker.py')
spec=importlib.util.spec_from_file_location('worker',SOURCE);w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
class WorkerTests(unittest.TestCase):
 def setUp(self):
  # Les tests d'archives utilisent quelques octets ; le disque du poste ne doit pas choisir leur scénario.
  self.disk=mock.patch('web_app_assets.shutil.disk_usage',return_value=types.SimpleNamespace(free=64*1024**3));self.disk.start();self.addCleanup(self.disk.stop)
  self.admission=mock.patch.object(w,'admit_resources',return_value={});self.admission.start()
  self.secret=mock.patch.object(w,'SECRET','worker-private-key-for-tests-only'*2);self.secret.start();self.addCleanup(self.secret.stop)
  self.metadata=mock.patch.object(w.Job,'next_metadata',return_value=('sha256:'+'d'*64,{'mode':'next-start','directory':'.'}));self.metadata.start();self.addCleanup(self.metadata.stop)
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name).resolve();self.previous=w.ROOT;w.ROOT=self.root
  self.value={'id':'a'*15,'appId':'b'*15,'action':'deploy','commit':'c'*40,'snapshot':{'config':{'repository':'owner/repo','rootDirectory':'.','generatedDomain':'web-example.monappli.re','healthPath':'/'},'env':{'SECRET':{'value':'secret-across-chunks','build':True,'runtime':True}},'domains':[]}}
 def tearDown(self):self.admission.stop();w.ROOT=self.previous;self.tmp.cleanup()
 def job(self):return w.Job(self.value)
 def test_default_and_symbolic_docker_sockets_are_refused(self):
  for p in ['/var/run/docker.sock','unix:///var/run/docker.sock','/tmp/docker.sock','/run/user/0/docker.sock']:
   with self.assertRaises(RuntimeError):w.validate_socket(p)
 def test_logs_redact_values_across_pipe_chunks(self):
  j=self.job();j.log('before secret-across-');j.log('chunks after');j.log('',True)
  text=j.logpath.read_text();self.assertNotIn('secret-',text);self.assertNotIn('chunks',text);self.assertIn('[masqué]',text)
 def test_public_configuration_preserves_build_diagnostics(self):
  env=self.value['snapshot']['env']
  for name,value in [('NODE_ENV','production'),('GESTASSUR_DEFAULT_ENVIRONMENT','test'),('CLIENT_SPACE_ENVIRONMENT','production'),('SYNC_CONCURRENCY','24'),('FEATURE_ENABLED','true'),('PGSSLMODE','require'),('DATABASE_SSL','require')]:env[name]={'value':value,'build':True}
  j=self.job();text='@playwright/test vitest 1.24 optimized production build true require(\"fs\")';j.log(text,True)
  self.assertEqual(j.logpath.read_text(),text)
 def test_short_passwords_and_overlapping_public_values_stay_secret(self):
  env=self.value['snapshot']['env'];env['APP_ENVIRONMENT']={'value':'test'};env['ADMIN_PASSWORD']={'value':'test'};env['AUTH_CONCURRENCY']={'value':'24'};env['TOKEN_ENABLED']={'value':'true'}
  j=self.job();j.log('password=te');j.log('st number=24 flag=true',True)
  self.assertEqual(j.logpath.read_text(),'password=[masqué] number=[masqué] flag=[masqué]')
  self.assertFalse(w.public_configuration('APP_ENVIRONMENT','custom-private-value'))
  self.assertFalse(w.public_configuration('DATABASE_SSL','private-certificate-value'))
 def test_google_fonts_failure_has_an_actionable_diagnostic(self):
  j=self.job()
  with self.assertRaisesRegex(w.CommandFailed,'16.4.3'):
   j.run([sys.executable,'-c',"import sys;print('next/font/google queries have exactly one entry');print('secret-across-chunks');sys.exit(1)"])
  self.assertNotIn('secret-across-chunks',j.logpath.read_text())
  self.assertIn('[masqué]',j.logpath.read_text())
 def test_project_build_deadline_terminates_the_running_process(self):
  j=self.job();j.stage_key='build';j.build_started=time.monotonic()-2
  with mock.patch('web_app_project_limits.build_seconds',return_value=1):
   with self.assertRaisesRegex(RuntimeError,'construction de ce projet'):j.run([sys.executable,'-c','import time;time.sleep(30)'])
  self.assertIsNone(j.process)
 def test_multiline_secrets_are_redacted_even_with_buildkit_line_prefixes(self):
  lines=['0123456789abcdef','private-second-line','short']
  self.value['snapshot']['env']['PEM']={'value':'\n'.join(lines),'build':True};j=self.job()
  for chunk in ['#8 1.2 '+lines[0][:7],lines[0][7:]+'\n#8 1.3 '+lines[1]+'\n']:j.log(chunk)
  j.log('',True);text=j.logpath.read_text()
  for line in lines[:2]:self.assertNotIn(line,text)
  self.assertEqual(text.count('[masqué]'),2)
 def test_next_metadata_reads_a_stopped_container_without_running_image_code(self):
  self.metadata.stop();j=self.job();ident='e'*64;image='sha256:'+'d'*64;calls=[]
  payload=json.dumps({'mode':'next-start','directory':'.'}).encode();stream=io.BytesIO()
  with tarfile.open(fileobj=stream,mode='w')as archive:
   member=tarfile.TarInfo('ph-server.json');member.size=len(payload);archive.addfile(member,io.BytesIO(payload))
  def request(method,path,body=None,**kwargs):
   calls.append((method,path,body))
   if method=='POST':return {'Id':ident}
   if method=='GET':return stream.getvalue()
   return {}
  info={'Id':image,'Config':{'Labels':{'pockethost.web':'true','pockethost.web.app':j.app,'pockethost.web.runtime':'nextjs'}}}
  with mock.patch.object(w,'docker',return_value=json.dumps([info]))as docker,mock.patch.object(w,'docker_json',side_effect=request):self.assertEqual(j.next_metadata('tag'),(image,{'mode':'next-start','directory':'.'}))
  self.assertEqual(docker.call_args.args[0],['image','inspect','tag'])
  self.assertEqual([method for method,_,_ in calls],['POST','GET','DELETE']);self.assertFalse(any(path.endswith('/start')for _,path,_ in calls));self.assertEqual(calls[0][2]['HostConfig']['NetworkMode'],'none')
 def test_next_metadata_refuses_foreign_or_wrong_runtime_images_before_container_creation(self):
  self.metadata.stop();j=self.job()
  for app,kind in [(j.app,'dockerfile'),('f'*15,'nextjs'),(j.app,None)]:
   info={'Id':'sha256:'+'d'*64,'Config':{'Labels':{'pockethost.web':'true','pockethost.web.app':app,'pockethost.web.runtime':kind}}}
   with mock.patch.object(w,'docker',return_value=json.dumps([info])),mock.patch.object(w,'docker_json')as request:
    with self.assertRaisesRegex(RuntimeError,'déploiement complet'):j.next_metadata('image')
   request.assert_not_called()
 def test_next_metadata_rejects_symlinks_and_oversized_files_and_always_removes_reader(self):
  self.metadata.stop();j=self.job();info={'Id':'sha256:'+'d'*64,'Config':{'Labels':{'pockethost.web':'true','pockethost.web.app':j.app,'pockethost.web.runtime':'nextjs'}}}
  for symlink,size in [(True,0),(False,4097)]:
   stream=io.BytesIO()
   with tarfile.open(fileobj=stream,mode='w')as archive:
    member=tarfile.TarInfo('ph-server.json');member.size=size
    if symlink:member.type=tarfile.SYMTYPE;member.linkname='/etc/passwd'
    archive.addfile(member,io.BytesIO(b'x'*size))
   with mock.patch.object(w,'docker',return_value=json.dumps([info])),mock.patch.object(w,'docker_json',side_effect=[{'Id':'e'*64},stream.getvalue(),{}])as request:
    with self.assertRaisesRegex(RuntimeError,'Métadonnées'):j.next_metadata('image')
   self.assertEqual(request.call_args.args,('DELETE','/containers/'+('e'*64)+'?force=true'))
 def test_next_build_caches_are_private_stable_and_full_rebuild_is_fresh(self):
  from web_app_build import cache_namespace
  scope=cache_namespace(self.value['appId'],w.SECRET)
  self.assertEqual(cache_namespace(self.value['appId'],w.SECRET),scope)
  self.assertNotEqual(cache_namespace('e'*15,w.SECRET),scope);self.assertNotEqual(cache_namespace(self.value['appId'],'different-worker-secret'*3),scope)
  with self.assertRaises(RuntimeError):cache_namespace(self.value['appId'],'')
  context=self.root/'context';context.mkdir();(context/'package.json').write_text('{"dependencies":{"next":"16"}}');(context/'package-lock.json').write_text('{}');work=self.root/'work';work.mkdir()
  for full in [False,True]:
   self.value['snapshot']['fullRebuild']=full;j=self.job()
   with mock.patch.object(j,'run')as run:j.build(context,work)
   recipe=(work/'Dockerfile').read_text();expected=scope+('-'+j.id if full else '')
   self.assertIn('id='+expected+'-npm',recipe);self.assertIn('id='+expected+'-next,target=/src/.next/cache',recipe)
   self.assertNotIn('id=ph-web-'+j.app+'-npm,',recipe);self.assertIn('type=docker,compression=uncompressed',run.call_args.args[0]);self.assertIn('pockethost.web.runtime=nextjs',run.call_args.args[0])
 def test_manifest_updates_only_own_app_and_removes_deleted_domains(self):
  other={'host':'other.monappli.re','appId':'d'*15,'target':'http://127.0.0.1:1234','active':True}
  w.atomic(self.root/'routes.json',{'version':1,'routes':[other,{'host':'deleted.example','appId':'b'*15,'target':'http://127.0.0.1:9999','active':True,'certificate':True}]})
  j=self.job();j.routes('http://127.0.0.1:3456');r=json.loads((self.root/'routes.json').read_text())['routes'];self.assertIn(other,r);self.assertEqual(len(r),2);self.assertNotIn('deleted.example',json.dumps(r));self.assertEqual((self.root/'routes.json').stat().st_mode&0o777,0o640)
 def test_stop_manifest_uses_inactive_empty_target(self):
  j=self.job();j.routes('',False);r=json.loads((self.root/'routes.json').read_text())['routes'][0];self.assertFalse(r['active']);self.assertEqual(r['target'],'')
 def test_build_uses_controlled_dockerfile_and_secret_mounts(self):
  context=self.root/'context';context.mkdir();(context/'package.json').write_text(json.dumps({'dependencies':{'next':'16.2.0'}}));(context/'package-lock.json').write_text('{}');work=self.root/'work';work.mkdir();j=self.job()
  with mock.patch.object(j,'run') as run:image=j.build(context,work)
  args=run.call_args.args[0];self.assertIn('--secret',args);self.assertNotIn('secret-across-chunks',' '.join(args));docker=(work/'Dockerfile').read_text();self.assertIn('--mount=type=secret',docker);self.assertIn('next-start',docker);self.assertIn('standalone',docker);self.assertIn('USER 10001',docker);self.assertNotIn('secret-across-chunks',docker)
 def test_runtime_next_directory_uses_initialized_scoped_volume_and_bounded_cache(self):
  calls=[];requests=[]
  def docker(args,**kwargs):
   calls.append(args)
   if args[:2]==['run','--rm']:return json.dumps({'mode':'next-start','directory':'.'})
   if args[0]=='inspect':return json.dumps([{'Image':'sha256:'+'d'*64,'NetworkSettings':{'Ports':{'3000/tcp':[{'HostIp':'127.0.0.1','HostPort':'12345'}]}}}])
   if args[0]=='exec':return '1024 /app/.next'
   return 'container'
  def request(method,path,body=None):
   requests.append((method,path,body));return {'Id':'f'*64}if'/create?'in path else{}
  with mock.patch('web_app_runtime.loopback_port',return_value='12345'),mock.patch.object(w,'docker',side_effect=docker),mock.patch.object(w,'docker_json',side_effect=request),mock.patch.object(w,'api',return_value={}),mock.patch.object(w,'publish_runtime_stats'):r=self.job().start('image')
  self.assertEqual(requests[0][1],'/networks/create');config=requests[1][2];host=config['HostConfig'];volume='ph-web-next-'+('b'*15)+'-'+('a'*15)
  self.assertEqual(host['NetworkMode'],'ph-web-net-'+self.value['appId']+'-'+self.value['id'])
  self.assertTrue(host['ReadonlyRootfs']);self.assertEqual(host['Mounts'],[{'Type':'volume','Source':volume,'Target':'/app/.next'}]);self.assertEqual(host['Tmpfs']['/app/.next/cache'],'rw,nosuid,uid=10001,gid=10001,size=128m');self.assertEqual(host['Tmpfs']['/tmp'],'rw,noexec,nosuid,size=128m')
  self.assertEqual(host['Memory'],768*1024**2);self.assertEqual(host['MemorySwap'],host['Memory']);self.assertEqual(host['NanoCpus'],500000000);self.assertEqual(host['PidsLimit'],256);self.assertEqual(host['CapDrop'],['ALL']);self.assertEqual(host['SecurityOpt'],['no-new-privileges']);self.assertEqual(config['User'],'10001:10001');self.assertEqual(host['PortBindings'],{'3000/tcp':[{'HostIp':'127.0.0.1','HostPort':'12345'}]})
  self.assertTrue(any(args[:2]==['volume','create']and args[-1]==volume for args in calls));self.assertEqual(requests[2],('POST','/containers/'+('f'*64)+'/start',None));self.assertEqual(r['volume'],volume);self.assertEqual(r['target'],'http://127.0.0.1:12345')
  self.assertEqual(r['image'],'image');self.assertEqual(r['imageId'],'sha256:'+'d'*64)
  budget=w.load(self.root/'runtime-budgets'/('a'*15+'.json'));self.assertEqual(budget['baselineBytes'],1024*1024);self.assertEqual(budget['path'],'/app/.next')
 def test_runtime_env_json_preserves_literals_without_contaminating_host_clients(self):
  marker=self.root/'never-executed';literal='first\nquote\'"` equals=a#b $HOME ${TOKEN} $(touch '+str(marker)+')\nlast\\';payloads=[];clients=[];host_environment=dict(os.environ)
  values={'MULTILINE':literal,'DOLLARS':'\\$HOME \\\\$TOKEN $$ tail$','EMPTY':'','PATH':'/usr/local/bin:/usr/bin:/app/runtime-only-bin','LD_PRELOAD':'/app/runtime-only-preload.so'}
  self.value['snapshot']['env']={name:{'value':value,'build':False,'runtime':True}for name,value in values.items()};self.value['snapshot']['env']['BUILD_ONLY']={'value':'build-only-private-value','build':True,'runtime':False}
  class Handler(http.server.BaseHTTPRequestHandler):
   def do_POST(handler):
    body=handler.rfile.read(int(handler.headers.get('Content-Length','0')));payloads.append((handler.path,json.loads(body)if body else None,dict(os.environ)));result=json.dumps({'Id':'e'*64}).encode()if'/create?'in handler.path else b'';handler.send_response(201 if result else 204);handler.send_header('Content-Length',str(len(result)));handler.end_headers();handler.wfile.write(result)
   def log_message(*args):pass
  socketpath=str(self.root/'docker-test.sock')
  def client(args,**kwargs):
   clients.append((args,kwargs,dict(os.environ)));command=args[3:]
   if command[:2]==['run','--rm']:return json.dumps({'mode':'next-start','directory':'.'})
   if command[:2]==['volume','create']:return command[-1]
   if command[0]=='inspect':return json.dumps([{'Image':'sha256:'+'d'*64,'NetworkSettings':{'Ports':{'3000/tcp':[{'HostIp':'127.0.0.1','HostPort':'23456'}]}}}])
   if command[0]=='exec':return'2048 /app/.next'
   raise AssertionError(command)
  with socketserver.UnixStreamServer(socketpath,Handler)as server:
   thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
   try:
    with mock.patch('web_app_runtime.loopback_port',return_value='23456'),mock.patch.object(w,'SOCKET',socketpath),mock.patch.object(w,'validate_socket')as validate,mock.patch.object(w.subprocess,'check_output',side_effect=client),mock.patch.object(w,'api',return_value={}),mock.patch.object(w,'publish_runtime_stats'):
     result=self.job().start('runtime-image');self.assertEqual(validate.call_count,3)
   finally:server.shutdown();thread.join(timeout=2)
  self.assertFalse(thread.is_alive());self.assertEqual(result['target'],'http://127.0.0.1:23456');self.assertEqual(len(payloads),3);self.assertEqual(payloads[0][0],'/v1.47/networks/create');self.assertTrue(payloads[1][0].startswith('/v1.47/containers/create?name=ph-web-'));self.assertEqual(payloads[2][0],'/v1.47/containers/'+('e'*64)+'/start')
  received=dict(item.split('=',1)for item in payloads[1][1]['Env']);self.assertEqual({name:received[name]for name in values},values);self.assertNotIn('BUILD_ONLY',received);self.assertEqual(received['NODE_ENV'],'production');self.assertEqual(received['HOSTNAME'],'0.0.0.0');self.assertEqual(received['PORT'],'3000');self.assertEqual(received['NODE_OPTIONS'],'--max-old-space-size=512')
  self.assertEqual(dict(os.environ),host_environment);self.assertFalse(marker.exists());self.assertTrue(clients)
  for args,kwargs,inherited in clients:
   self.assertEqual(inherited,host_environment);self.assertIsNone(kwargs.get('env'));self.assertNotIn('--env-file',args);self.assertNotIn(literal,json.dumps(args));self.assertNotIn(values['PATH'],json.dumps(args));self.assertNotIn(values['LD_PRELOAD'],json.dumps(args))
  for _,_,inherited in payloads:self.assertEqual(inherited,host_environment)
  for path in self.root.rglob('*'):
   if path.is_file():self.assertNotIn(literal,path.read_text());self.assertNotIn(values['LD_PRELOAD'],path.read_text())
  self.assertFalse(list(self.root.rglob('*.env')))
 def test_apply_runtime_reuses_published_image_without_fetch_or_build(self):
  self.value['action']='apply-runtime';self.value['snapshot']['rollbackResult']={'image':'published-image'};previous={'version':1,'routes':[{'host':'old','appId':'b'*15,'target':'http://127.0.0.1:1234','active':True}]};w.atomic(self.root/'routes.json',previous);j=self.job();result={'container':'candidate','image':'published-image','target':'http://127.0.0.1:4567'}
  with mock.patch.object(w,'api',return_value={'desiredCommit':'d'*40}),mock.patch.object(w,'publish_runtime_stats'),mock.patch.object(w,'resources')as resources,mock.patch.object(j,'fetch')as fetch,mock.patch.object(j,'build')as build,mock.patch.object(j,'start',return_value=result)as start,mock.patch.object(j,'health')as health,mock.patch.object(j,'cache_assets')as assets:
   self.assertEqual(j.execute(),result)
  resources.assert_not_called();fetch.assert_not_called();build.assert_not_called();start.assert_called_once_with('published-image');health.assert_called_once_with(result);assets.assert_called_once_with(result);self.assertEqual(w.load(self.root/'routes.json')['routes'][0]['target'],result['target']);self.assertEqual(w.load(self.root/'current-job.json')['previous'],previous)
 def test_next_publication_refuses_an_unverifiable_image_id(self):
  def docker(args,**kwargs):
   if args[:2]==['run','--rm']:return json.dumps({'mode':'next-start','directory':'.'})
   if args[0]=='inspect':return json.dumps([{'Image':'mutable-tag','NetworkSettings':{'Ports':{'3000/tcp':[{'HostIp':'127.0.0.1','HostPort':'12345'}]}}}])
   return'container'
  with mock.patch.object(w,'docker',side_effect=docker),mock.patch.object(w,'docker_json',return_value={'Id':'f'*64}),mock.patch.object(w,'api',return_value={}),mock.patch.object(w,'publish_runtime_stats'):
   with self.assertRaisesRegex(RuntimeError,'immuable'):self.job().start('image')
 def test_storage_recreate_refuses_old_dynamic_binding_before_any_stop(self):
  self.value['snapshot']['storage']=[{'name':'uploads','target':'/data','sizeMiB':16,'readOnly':False}];j=self.job();ident='f'*64;calls=[]
  def docker(args,**kwargs):
   calls.append(args)
   if args[0]=='ps':return ident if '--no-trunc'in args else ident[:12]
   if args[0]=='inspect':return json.dumps([{'Id':ident,'Name':'/ph-web-'+j.app+'-'+('d'*15),'State':{'Running':True},'Config':{'User':'10001:10001','Labels':{'pockethost.web':'true','pockethost.web.app':j.app}},'HostConfig':{'ReadonlyRootfs':True,'PortBindings':{'3000/tcp':[{'HostIp':'127.0.0.1','HostPort':''}]}},'NetworkSettings':{'Ports':{'3000/tcp':[{'HostIp':'127.0.0.1','HostPort':'32787'}]}}}])
   raise AssertionError(args)
  with mock.patch.object(w,'docker',side_effect=docker):
   with self.assertRaisesRegex(RuntimeError,'dynamique'):j.quiesce_storage()
  self.assertFalse(any(args[0]=='stop'for args in calls));self.assertFalse((self.root/'current-job.json').exists())
 def test_storage_recreate_requests_full_docker_ids_for_the_durable_resume_journal(self):
  self.value['snapshot']['storage']=[{'name':'uploads','target':'/data','sizeMiB':16,'readOnly':False}];j=self.job();ident='e'*64
  def docker(args,**kwargs):
   if args[0]=='ps':return ident if '--no-trunc'in args else ident[:12]
   if args[0]=='inspect':return json.dumps([{'Id':ident,'Name':'/ph-web-'+j.app+'-'+('d'*15),'State':{'Running':True},'Config':{'User':'10001:10001','Labels':{'pockethost.web':'true','pockethost.web.app':j.app}},'HostConfig':{'ReadonlyRootfs':True,'PortBindings':{'3000/tcp':[{'HostIp':'127.0.0.1','HostPort':'32787'}]}},'NetworkSettings':{'Ports':{'3000/tcp':[{'HostIp':'127.0.0.1','HostPort':'32787'}]}}}])
   if args[0]=='stop':self.assertEqual(args[-1],ident);return ident
   raise AssertionError(args)
  with mock.patch.object(w,'docker',side_effect=docker):j.quiesce_storage()
  self.assertEqual(j.recreate_previous,[ident]);self.assertEqual(w.load(self.root/'current-job.json')['recreatePrevious'],[ident])
 def test_stop_execute_does_not_return_before_front_and_auxiliary_are_stopped(self):
  self.value['action']='stop';j=self.job();front_entered=threading.Event();aux_entered=threading.Event();front_release=threading.Event();aux_release=threading.Event();returned=threading.Event();heartbeats=threading.Event();states={'front':True,'aux':True};calls=[];results=[];errors=[]
  def docker(args,**kwargs):
   calls.append(args)
   if args[:2]==['ps','-aq']:return'front aux'
   if args[:2]==['ps','-q']:return' '.join(name for name,running in states.items()if running)
   if args[0]=='inspect':
    name=args[1];return json.dumps([{'Name':'/ph-web-'+j.app+'-'+('d'*15)+(''if name=='front'else'-aux'),'Config':{'Labels':{'pockethost.web':'true','pockethost.web.app':j.app,'pockethost.web.deployment':'d'*15}},'State':{'Running':states[name]}}])
   if args[0]=='stop':
    name=args[-1];(front_entered if name=='front'else aux_entered).set();release=front_release if name=='front'else aux_release
    if not release.wait(2):raise RuntimeError('test stop was not released')
    states[name]=False;return name
   raise AssertionError(args)
  def api(*args):heartbeats.set();return{}
  def execute():
   try:results.append(j.execute())
   except Exception as error:errors.append(error)
   finally:returned.set()
  with mock.patch.object(w,'docker',side_effect=docker),mock.patch.object(w,'api',side_effect=api),mock.patch.object(w,'CERT_HEARTBEAT_SECONDS',.01):
   thread=threading.Thread(target=execute);thread.start()
   try:
    self.assertTrue(front_entered.wait(1));self.assertTrue(heartbeats.wait(1));self.assertFalse(returned.is_set());front_release.set()
    self.assertTrue(aux_entered.wait(1));self.assertFalse(returned.is_set());self.assertFalse(states['front']);self.assertTrue(states['aux']);aux_release.set();thread.join(2)
   finally:front_release.set();aux_release.set();thread.join(2)
  self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(results,[{}]);self.assertTrue(returned.is_set());self.assertFalse(any(states.values()));self.assertTrue(any(args[:2]==['ps','-q']for args in calls));self.assertFalse(w.load(self.root/'routes.json')['routes'][0]['active'])
 def test_stop_refuses_ready_when_docker_still_reports_a_running_service(self):
  self.value['action']='stop';j=self.job()
  def docker(args,**kwargs):
   if args[0]=='ps':return'container'
   if args[0]=='inspect':return json.dumps([{'Name':'/ph-web-'+j.app+'-'+('d'*15),'Config':{'Labels':{'pockethost.web':'true','pockethost.web.app':j.app}},'State':{'Running':True}}])
   if args[0]=='stop':raise subprocess.CalledProcessError(1,args)
   raise AssertionError(args)
  with mock.patch.object(w,'docker',side_effect=docker),mock.patch.object(w,'api',return_value={}):
   with self.assertRaisesRegex(RuntimeError,'incomplet'):j.execute()
 def test_apply_runtime_health_failure_keeps_previous_route_and_image(self):
  self.value['action']='apply-runtime';self.value['snapshot']['rollbackResult']={'image':'published-image'};previous={'version':1,'routes':[{'host':'old','appId':'b'*15,'target':'http://127.0.0.1:1234','active':True}]};w.atomic(self.root/'routes.json',previous);j=self.job();result={'container':'candidate','image':'published-image','target':'http://127.0.0.1:4567'}
  with mock.patch.object(w,'api',return_value={}),mock.patch.object(w,'publish_runtime_stats'),mock.patch.object(w,'docker',return_value='')as docker,mock.patch.object(j,'fetch')as fetch,mock.patch.object(j,'build')as build,mock.patch.object(j,'start',return_value=result),mock.patch.object(j,'health',side_effect=RuntimeError('health failed')),mock.patch.object(j,'cache_assets')as assets:
   with self.assertRaisesRegex(RuntimeError,'health failed'):j.execute()
  fetch.assert_not_called();build.assert_not_called();assets.assert_not_called();self.assertEqual(w.load(self.root/'routes.json'),previous);self.assertFalse(j.published);self.assertFalse(any(call.args[0][:2]==['image','rm']for call in docker.call_args_list));self.assertTrue(any(call.args[0][0]=='rm'for call in docker.call_args_list))
 def test_ci_cancellation_at_publish_cleans_candidate_before_any_route_switch(self):
  self.value['action']='ci-deploy';self.value['snapshot']['runtime']={'kind':'image','image':'ghcr.io/owner/site@sha256:'+('d'*64)};self.value['snapshot']['ci']={'expiresAt':(w.datetime.datetime.now(w.datetime.timezone.utc)+w.datetime.timedelta(hours=1)).isoformat()}
  previous={'version':1,'routes':[{'appId':self.value['appId'],'host':'old','target':'http://127.0.0.1:1234','active':True}]};w.atomic(self.root/'routes.json',previous);j=self.job();result={'runtimeKind':'image','container':'candidate','image':'owned','target':'http://127.0.0.1:4567','public':True}
  def api(path,body):return{'desiredCommit':self.value['commit'],'cancelRequested':body.get('stageKey')=='publish'}
  with mock.patch.object(w,'api',side_effect=api),mock.patch.object(w,'publish_runtime_stats'),mock.patch.object(w,'resources'),mock.patch.object(j,'build',return_value='owned'),mock.patch.object(j,'start',return_value=result),mock.patch.object(j,'health'),mock.patch.object(j,'cleanup_failure')as cleanup,mock.patch.object(j,'routes')as routes:
   with self.assertRaises(w.JobCancelled):j.execute()
  routes.assert_not_called();cleanup.assert_called_once();self.assertEqual(w.load(self.root/'routes.json'),previous)

 def test_empty_docker_archive_has_safe_error_and_explicit_working_directory(self):
  calls=[]
  class Proc:
   stdout=io.BytesIO(b'')
   def poll(self):return 1
   def wait(self,**kw):return 1
  def popen(*args,**kw):calls.append(kw);kw['stderr'].write(b'stat -: permission denied');kw['stderr'].flush();return Proc()
  j=self.job()
  with mock.patch.object(w.subprocess,'Popen',side_effect=popen),mock.patch.object(j,'heartbeat'):
   with self.assertRaisesRegex(RuntimeError,'export'):j.cache_assets({'container':'candidate','serverDirectory':'.'})
  self.assertEqual(calls[0]['cwd'],self.root);self.assertIn('permission denied',j.logpath.read_text())
 def test_static_tar_prefix_is_stripped_without_hiding_chunk_files(self):
  stream=io.BytesIO()
  with tarfile.open(fileobj=stream,mode='w') as archive:
   member=tarfile.TarInfo('static/chunks/main.js');member.size=8;archive.addfile(member,io.BytesIO(b'compiled'))
  class Proc:
   stdout=io.BytesIO(stream.getvalue())
   def poll(self):return 0
   def wait(self,**kw):return 0
  with mock.patch.object(w.subprocess,'Popen',return_value=Proc()),mock.patch.object(w.Job,'heartbeat'):self.job().cache_assets({'container':'candidate','serverDirectory':'.'})
  asset=self.root/'assets'/('b'*15)/('a'*15)/'chunks/main.js';self.assertEqual(asset.read_bytes(),b'compiled');self.assertEqual(asset.stat().st_mode&0o777,0o640)
 def test_failed_static_export_removes_staging_and_leaves_previous_publication(self):
  import web_app_assets as assets
  j=self.job();old=self.root/'assets'/j.app/'previous';old.mkdir(parents=True);(old/'live.js').write_bytes(b'live');w.atomic(self.root/'routes.json',{'version':1,'routes':[{'appId':j.app,'target':'old'}]})
  stream=io.BytesIO()
  with tarfile.open(fileobj=stream,mode='w')as archive:
   member=tarfile.TarInfo('static/large');member.size=4;archive.addfile(member,io.BytesIO(b'four'))
  process=types.SimpleNamespace(stdout=io.BytesIO(stream.getvalue()),poll=lambda:0)
  with mock.patch.object(w.subprocess,'Popen',return_value=process),mock.patch.object(j,'heartbeat'),mock.patch.object(assets,'MAX_BYTES',3),self.assertRaises(RuntimeError):j.cache_assets({'container':'candidate','serverDirectory':'.'})
  self.assertEqual(list(old.parent.iterdir()),[old]);self.assertEqual((old/'live.js').read_bytes(),b'live');self.assertEqual(w.load(self.root/'routes.json')['routes'][0]['target'],'old')
 def test_hung_static_export_is_killed_and_staging_removed(self):
  import web_app_assets as assets
  j=self.job();process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],stdout=subprocess.PIPE)
  with mock.patch.object(w.subprocess,'Popen',return_value=process),mock.patch.object(j,'heartbeat'),mock.patch.object(assets,'MAX_SECONDS',.15),self.assertRaisesRegex(RuntimeError,'délai maximal'):j.cache_assets({'container':'candidate','serverDirectory':'.'})
  self.assertIsNotNone(process.poll());self.assertEqual(list((self.root/'assets'/j.app).iterdir()),[])
 def test_multiple_lockfiles_fail_before_build(self):
  context=self.root/'context';context.mkdir();(context/'package.json').write_text('{"dependencies":{"next":"16.2.0"}}');(context/'package-lock.json').write_text('{}');(context/'yarn.lock').write_text('');work=self.root/'work';work.mkdir()
  with self.assertRaises(RuntimeError):self.job().build(context,work)
 def test_publication_rpc_failure_restores_previous_route(self):
  previous={'version':1,'routes':[{'host':'old','appId':'b'*15,'target':'http://127.0.0.1:1234','active':True}]};w.atomic(self.root/'routes.json',previous)
  j=self.job();result={'container':'candidate','image':'image','target':'http://127.0.0.1:4567'}
  def report(path,body):
   if body.get('stage')=='Publication vérifiée':raise RuntimeError('Network failure')
   return {'desiredCommit':'c'*40}
  with mock.patch.object(w,'api',side_effect=report),mock.patch.object(w,'resources'),mock.patch.object(w,'docker',return_value=''),mock.patch.object(j,'fetch',return_value=self.root),mock.patch.object(j,'build',return_value='image'),mock.patch.object(j,'start',return_value=result),mock.patch.object(j,'health'),mock.patch.object(j,'cache_assets'):
   with self.assertRaises(RuntimeError):j.execute()
  self.assertEqual(w.load(self.root/'routes.json'),previous)
 def test_crash_reconciliation_rolls_back_only_unacknowledged_version(self):
  previous={'version':1,'routes':[{'host':'old','appId':'b'*15,'active':True}]};w.atomic(self.root/'routes.json',{'version':1,'routes':[]});w.atomic(self.root/'current-job.json',{'id':'a'*15,'previous':previous,'candidate':'ph-web-candidate'})
  with mock.patch.object(w,'api',side_effect=[{'status':'running'},{'ok':True}]),mock.patch.object(w,'docker') as docker:w.reconcile()
  self.assertEqual(w.load(self.root/'routes.json'),previous);self.assertFalse((self.root/'current-job.json').exists());docker.assert_called_once()
 def test_acknowledged_publication_survives_reconciliation(self):
  current={'version':1,'routes':[{'host':'new'}]};w.atomic(self.root/'routes.json',current);w.atomic(self.root/'current-job.json',{'id':'a'*15,'previous':{'version':1,'routes':[]},'candidate':'ph-web-candidate'})
  with mock.patch.object(w,'api',return_value={'status':'ready'}),mock.patch.object(w,'docker') as docker:w.reconcile()
  self.assertEqual(w.load(self.root/'routes.json'),current);docker.assert_not_called()
 def test_cancel_before_publication_preserves_old_route_and_never_copies_assets(self):
  previous={'version':1,'routes':[{'host':'old','appId':'b'*15,'target':'http://127.0.0.1:1234','active':True}]};w.atomic(self.root/'routes.json',previous)
  j=self.job();result={'container':'candidate','image':'image','target':'http://127.0.0.1:4567'}
  def report(path,body):return {'desiredCommit':'c'*40,'cancelRequested':body.get('stageKey')=='publish'}
  with mock.patch.object(w,'api',side_effect=report),mock.patch.object(w,'publish_runtime_stats'),mock.patch.object(w,'resources'),mock.patch.object(w,'docker',return_value=''),mock.patch.object(j,'fetch',return_value=self.root),mock.patch.object(j,'build',return_value='image'),mock.patch.object(j,'start',return_value=result),mock.patch.object(j,'health'),mock.patch.object(j,'cache_assets')as assets:
   with self.assertRaises(w.JobCancelled):j.execute()
  assets.assert_not_called();self.assertEqual(w.load(self.root/'routes.json'),previous);self.assertFalse(j.published)
 def test_cancel_running_build_terminates_client_process_group(self):
  j=self.job();processes=[];popen=subprocess.Popen
  def launch(*args,**kwargs):
   p=popen(*args,**kwargs);processes.append(p);return p
  started=time.monotonic()
  with mock.patch.object(w,'api',return_value={'cancelRequested':True}),mock.patch.object(w,'resources'),mock.patch.object(w,'check_runtime_budgets'),mock.patch.object(w,'publish_runtime_stats'),mock.patch.object(w.subprocess,'Popen',side_effect=launch):
   with self.assertRaises(w.JobCancelled):j.run([sys.executable,'-c','import time;print("build started",flush=True);time.sleep(60)'])
  self.assertLess(time.monotonic()-started,12);self.assertEqual(len(processes),1);self.assertIsNotNone(processes[0].returncode);self.assertLess(processes[0].returncode,0);self.assertIsNone(j.process)
 def test_cancelled_worker_recovery_restores_previous_route(self):
  previous={'version':1,'routes':[{'host':'old','appId':'b'*15,'active':True}]};w.atomic(self.root/'routes.json',{'version':1,'routes':[]});w.atomic(self.root/'current-job.json',{'id':'a'*15,'previous':previous,'candidate':'ph-web-candidate'})
  with mock.patch.object(w,'api',side_effect=[{'status':'running','cancelRequested':True},{'ok':True}])as api,mock.patch.object(w,'docker'):w.reconcile()
  self.assertEqual(w.load(self.root/'routes.json'),previous);self.assertEqual(api.call_args_list[-1].args[1]['status'],'cancelled');self.assertFalse((self.root/'current-job.json').exists())
 def test_runtime_sampling_reports_only_published_owned_container(self):
  name='ph-web-'+('b'*15)+'-'+('a'*15)
  w.atomic(self.root/'routes.json',{'version':1,'routes':[{'appId':'b'*15,'target':'http://127.0.0.1:12345','active':True}]})
  def docker(args,**kwargs):
   if args[0]=='ps':return 'one\ntwo\n'
   if args[0]=='inspect':return json.dumps([{'Name':'/'+name,'Config':{'Labels':{'pockethost.web.app':'b'*15},'Env':['PRIVATE_KEY=do-not-return']},'HostConfig':{'Memory':768*1024**2},'NetworkSettings':{'Ports':{'3000/tcp':[{'HostIp':'127.0.0.1','HostPort':'12345'}]}}},{'Name':'/not-a-managed-web-container','Config':{'Labels':{},'Env':['OTHER_SECRET=hidden']}}])
   if args[0]=='stats':return json.dumps({'Name':name,'CPUPerc':'15.23%','MemUsage':'12.5MiB / 768MiB','PIDs':'4'})+'\n'+json.dumps({'Name':'not-a-managed-web-container','CPUPerc':'99%','MemUsage':'1GiB / 2GiB'})
   raise AssertionError(args)
  with mock.patch.object(w,'docker',side_effect=docker):w.publish_runtime_stats(True)
  value=w.load(self.root/'runtime-status.json');stats=value['applications']['b'*15];self.assertEqual(stats['cpuPercent'],15.23);self.assertEqual(stats['memoryBytes'],13107200);self.assertEqual(stats['memoryLimitBytes'],768*1024**2);self.assertEqual(stats['pids'],4);self.assertEqual(len(value['applications']),1);self.assertNotIn('PRIVATE',json.dumps(value));self.assertNotIn('OTHER_SECRET',json.dumps(value))
 def test_blocking_certificate_keeps_heartbeat_and_stops_before_terminal_report(self):
  self.value['action']='domain';self.value['snapshot']['domain']='custom.example.org';self.value['snapshot']['domains']=[{'host':'custom.example.org','certificate':False}]
  w.atomic(self.root/'routes.json',{'version':1,'routes':[{'appId':'b'*15,'host':'web-example.monappli.re','active':True,'target':'http://127.0.0.1:1234'}]})
  event=threading.Event();calls=[]
  def report(path,body):calls.append(body);event.set();return {}
  def certificate(*args):
   self.assertTrue(event.wait(timeout=1),'No heartbeat while certificate helper was blocked');return {'certificate':True,'certificateExpiresAt':'2030-01-01T00:00:00Z'}
  module=types.SimpleNamespace(provision_web_domain_certificate=certificate)
  with mock.patch.dict(sys.modules,{'web_app_domains':module}),mock.patch.object(w,'api',side_effect=report),mock.patch.object(w,'CERT_HEARTBEAT_SECONDS',.01):
   result=self.job().execute();count=len(calls);time.sleep(.04);self.assertEqual(len(calls),count)
  self.assertTrue(result['certificate']);self.assertGreater(count,0);self.assertTrue(all(c['stageKey']=='domain'for c in calls));self.assertTrue(w.load(self.root/'worker-status.json')['ready'])
 def test_image_reuse_fingerprint_lookup_and_bounded_memory(self):
  profile={'kind':'auto','port':3000}
  with mock.patch.object(w,'SECRET','s'*32):
   base=self.job().build_fingerprint(profile);self.assertRegex(base,'^[a-f0-9]{64}$');self.assertEqual(self.job().build_fingerprint(profile),base)
   self.value['snapshot']['env']['RUNTIME_ONLY']={'value':'x','build':False,'runtime':True};self.assertEqual(self.job().build_fingerprint(profile),base)
   for change in [lambda v:v.update(commit='d'*40),lambda v:v['snapshot']['env']['SECRET'].update(value='other'),lambda v:v['snapshot']['config'].update(rootDirectory='apps/web')]:
    value=json.loads(json.dumps(self.value));change(value);self.assertNotEqual(w.Job(value).build_fingerprint(profile),base)
   self.assertNotEqual(self.job().build_fingerprint({**profile,'port':8080}),base);self.assertNotIn('secret-across-chunks',base)
   self.assertEqual(self.job().build_fingerprint({'kind':'image','image':'x@sha256:'+'1'*64}),'');self.assertEqual(self.job().build_fingerprint({'kind':'compose'}),'')
   self.value['action']='rollback';self.assertEqual(self.job().build_fingerprint(profile),'');self.value['action']='deploy'
  with mock.patch.object(w,'SECRET',''):self.assertEqual(self.job().build_fingerprint(profile),'')
  ident='sha256:'+'e'*64;images={ident:{'Id':ident,'Config':{'Labels':{'pockethost.web':'true','pockethost.web.app':'b'*15}}}}
  def docker(args,**kwargs):
   if args[:2]==['image','inspect'] and args[2] in images:return json.dumps([images[args[2]]])
   raise subprocess.CalledProcessError(1,args)
  j=self.job();self.assertEqual(j.reusable_image('f'*64),'')
  j.remember_image('f'*64,{'image':'ph-web/'+'b'*15+':'+'a'*15,'imageId':ident})
  with mock.patch.object(w,'docker',side_effect=docker):
   self.assertEqual(j.reusable_image('f'*64),ident);self.assertEqual(j.reusable_image('0'*64),'')
   images[ident]['Config']['Labels']['pockethost.web.app']='c'*15;self.assertEqual(j.reusable_image('f'*64),'')
   del images[ident];self.assertEqual(j.reusable_image('f'*64),'')
  for index in range(7):j.remember_image(str(index)*64,{'image':'sha256:'+str(index)*64})
  j.remember_image('6'*64,{'image':'sha256:'+'9'*64});j.remember_image('8'*64,{'image':'ph-web/tag-without-id'})
  entries=w.load(self.root/'image-reuse'/('b'*15+'.json'))['entries']
  self.assertEqual([e['fingerprint'][0] for e in entries],['6','5','4','3','2']);self.assertEqual(entries[0]['image'],'sha256:'+'9'*64)
 def test_deploy_reuses_published_image_without_fetching_or_building(self):
  ident='sha256:'+'e'*64;started=[];stages=[]
  job=self.job()
  def stage(name):stages.append(name);return {'desiredCommit':self.value['commit']}
  with mock.patch.object(w,'resources'),mock.patch.object(w,'SECRET','s'*32),mock.patch.object(job,'reusable_image',return_value=ident),mock.patch.object(job,'fetch_source',side_effect=AssertionError('fetch')),mock.patch.object(job,'build',side_effect=AssertionError('build')),\
   mock.patch.object(job,'start',side_effect=lambda image:started.append(image) or {'image':'ph-web/x','imageId':ident,'container':'c','target':'http://127.0.0.1:1','serverDirectory':'.'}),mock.patch.object(job,'health'),mock.patch.object(job,'stage',side_effect=stage),\
   mock.patch.object(job,'cache_assets'),mock.patch.object(job,'checkpoint'),mock.patch.object(job,'routes'),mock.patch.object(job,'remember_image') as remember,mock.patch.object(w,'api',return_value={'desiredCommit':self.value['commit']}),mock.patch.object(job,'heartbeat'),\
   mock.patch.dict(sys.modules,{'web_app_runtime':types.SimpleNamespace(validate_profile=lambda value:{'kind':'nextjs','resources':{}},RuntimeEngine=None,ResourcePressure=ResourcePressure)}):
   job.execute()
  self.assertEqual(started,[ident]);self.assertEqual(stages[:2],['build','publish']);self.assertNotIn('fetch',stages);remember.assert_called_once()
  self.assertIn('Image réutilisée',job.logpath.read_text())
 def test_full_rebuild_skips_image_reuse(self):
  self.value['snapshot']['fullRebuild']=True;job=self.job();built=[]
  with mock.patch.object(w,'resources'),mock.patch.object(w,'SECRET','s'*32),mock.patch.object(job,'reusable_image',side_effect=AssertionError('reuse')),mock.patch.object(job,'fetch_source',return_value=self.root),mock.patch.object(job,'build',side_effect=lambda context,work:built.append(1) or 'ph-web/x:y'),\
   mock.patch.object(job,'start',return_value={'image':'ph-web/x','imageId':'sha256:'+'e'*64,'container':'c','target':'http://127.0.0.1:1','serverDirectory':'.'}),mock.patch.object(job,'health'),mock.patch.object(job,'stage',return_value={'desiredCommit':self.value['commit']}),\
   mock.patch.object(job,'cache_assets'),mock.patch.object(job,'checkpoint'),mock.patch.object(job,'routes'),mock.patch.object(job,'remember_image') as remember,mock.patch.object(w,'api',return_value={'desiredCommit':self.value['commit']}),mock.patch.object(job,'heartbeat'),\
   mock.patch.dict(sys.modules,{'web_app_runtime':types.SimpleNamespace(validate_profile=lambda value:{'kind':'nextjs','resources':{}},RuntimeEngine=None,ResourcePressure=ResourcePressure)}):
   job.execute()
  self.assertEqual(built,[1]);remember.assert_called_once();self.assertIn('Reconstruction complète demandée',job.logpath.read_text())
 def test_failed_health_keeps_masked_site_logs_in_deployment_log(self):
  job=self.job();front='ph-web-'+'b'*15+'-'+'a'*15;calls=[]
  def run(args,**kwargs):calls.append(args[-1]);return types.SimpleNamespace(stdout='Ready in 300ms\n',stderr='Error: EACCES secret-across-chunks\n')
  with mock.patch.object(w.subprocess,'run',side_effect=run):job.site_logs({'container':front,'services':[{'container':front},{'container':front+'-worker'},{'container':'ph-web-other-app'}]})
  text=job.logpath.read_text();self.assertEqual(calls,[front,front+'-worker'])
  self.assertIn('Dernières lignes du journal du site (web)',text);self.assertIn('(worker)',text);self.assertIn('EACCES',text);self.assertNotIn('secret-across-chunks',text)
 def test_health_does_not_follow_redirects(self):
  handler=w.NoRedirect();self.assertIsNone(handler.redirect_request(None,None,302,'found',{},'http://private.example'))

 def test_cleanup_keeps_images_pinned_by_backups_beyond_normal_three_versions(self):
  import web_app_backups
  ids=['1'*64,'2'*64,'3'*64,'4'*64];calls=[];tags={ident:'ph-web/'+self.value['appId']+':'+str(index)*15 for index,ident in enumerate(ids,1)}
  def docker(args,**kwargs):
   calls.append(args)
   if args[:2]==['image','ls']:return ' '.join(ids)
   if args[:2]==['image','inspect']:
    ident=args[2];return json.dumps([{'Id':'sha256:'+ident,'Created':ident,'RepoTags':[tags[ident]],'Config':{'Labels':{'pockethost.web.app':self.value['appId']}}}])
   if args[:2]==['ps','-aq']:return ''
   return ''
  with mock.patch.object(w,'docker',side_effect=docker),mock.patch.object(web_app_backups,'protected_images',return_value={tags[ids[0]]})as protected:
   retained=self.job().cleanup(tags[ids[-1]])
  protected.assert_called_once_with(self.root,self.value['appId'],w.api)
  self.assertIn(tags[ids[0]],retained);self.assertIn('sha256:'+ids[0],retained);self.assertFalse(any(args[:2]==['image','rm']for args in calls))
 def test_cleanup_stops_before_removal_when_backup_pins_cannot_be_verified(self):
  import web_app_backups
  calls=[]
  def docker(args,**kwargs):calls.append(args);return ''
  with mock.patch.object(w,'docker',side_effect=docker),mock.patch.object(web_app_backups,'protected_images',side_effect=web_app_backups.BackupError('invalid pins')):
   with self.assertRaises(web_app_backups.BackupError):self.job().cleanup('image')
  self.assertFalse(any(args[0]in['rm','stop','buildx']or args[:2]==['image','rm']for args in calls))

 def test_cleanup_keeps_current_immutable_sha_without_any_backup_pin(self):
  import web_app_backups
  ids=['1'*64,'2'*64,'3'*64,'4'*64];removed=[]
  def docker(args,**kwargs):
   if args[:2]==['image','ls']:return ' '.join(ids)
   if args[:2]==['image','inspect']:
    ident=args[2];return json.dumps([{'Id':'sha256:'+ident,'Created':ident,'RepoTags':[],'Config':{'Labels':{'pockethost.web.app':self.value['appId']}}}])
   if args[:2]==['image','rm']:removed.append(args[2])
   return ''
  current='sha256:'+ids[0]
  with mock.patch.object(w,'docker',side_effect=docker),mock.patch.object(web_app_backups,'protected_images',return_value=set()):retained=self.job().cleanup(current)
  self.assertIn(current,retained);self.assertNotIn(current,removed)
 def test_auto_build_stage_label_and_worker_status_publishes_railpack(self):
  self.value['snapshot']['runtime']={'kind':'auto'};calls=[]
  with mock.patch.object(w,'api',side_effect=lambda path,body:calls.append(body)or{}),mock.patch.object(w,'publish_runtime_stats'),mock.patch.object(w,'RAILPACK_TOOLS',True):
   j=self.job();j.stage('build');j.stage('fetch')
  self.assertEqual(calls[0]['stage'],'Construction automatique (Railpack)');self.assertEqual(calls[1]['stage'],'Récupération du commit');self.assertIn('Construction automatique (Railpack)',j.logpath.read_text())
  self.assertIs(w.load(self.root/'worker-status.json')['railpack'],True)
  self.value['snapshot']['runtime']={'kind':'dockerfile'};self.assertEqual(self.job().label('build'),w.STAGES['build'])
 def test_prerequisites_never_fail_on_missing_railpack(self):
  import web_app_railpack
  with mock.patch.object(web_app_railpack,'_verified',side_effect=FileNotFoundError()):self.assertFalse(web_app_railpack.railpack_ready(lambda *a,**k:''))
 def test_builder_quotas_come_from_environment_and_recreate_only_on_change(self):
  with mock.patch.dict(os.environ,{},clear=False):
   for key in ['WEB_APPS_BUILD_CPUS','WEB_APPS_BUILD_MEMORY','WEB_APPS_BUILD_CACHE','WEB_APPS_BUILDKIT_IMAGE']:os.environ.pop(key,None)
   self.assertEqual(w.builder_options(),{'memory':'3g','cpu-period':'100000','cpu-quota':'100000','cpu-shares':'128','memory-swap':'3g','network':w.BUILDER_NETWORK});self.assertEqual(w.build_cache_limit(),'2gb')
   os.environ.update(WEB_APPS_BUILD_CPUS='4',WEB_APPS_BUILD_MEMORY='8G',WEB_APPS_BUILD_CACHE='20gb')
   self.assertEqual(w.builder_options()['cpu-quota'],'400000');self.assertEqual(w.builder_options()['memory'],'8g');self.assertEqual(w.build_cache_limit(),'20gb')
   for key,bad in [('WEB_APPS_BUILD_CPUS','0'),('WEB_APPS_BUILD_CPUS','64'),('WEB_APPS_BUILD_CPUS','1;rm'),('WEB_APPS_BUILD_MEMORY','8gb'),('WEB_APPS_BUILD_MEMORY','0g')]:
    with mock.patch.dict(os.environ,{key:bad}),self.assertRaises(RuntimeError):w.builder_options()
   with mock.patch.dict(os.environ,{'WEB_APPS_BUILD_CACHE':'--all'}):self.assertEqual(w.build_cache_limit(),'2gb')
   info=json.dumps({'SecurityOptions':['name=rootless'],'CgroupVersion':'2','CgroupDriver':'systemd'})
   for listed,expected in [('cpu-shares="128" memory-swap="8g" memory="8g" network="ph-web-build-network" cpu-period="100000" cpu-quota="400000"',[]),('memory="3g" network="bridge" cpu-period="100000" cpu-quota="100000"',['rm','create'])]:
    calls=[]
    def docker(args,**kwargs):
     calls.append(args)
     if args[0]=='info':return info
     if args[:2]==['network','inspect']:return json.dumps([{'Driver':'bridge','Labels':{'pockethost.web.builder':'true'}}])
     if args[:2]==['buildx','inspect'] and len(args)==3:return 'Name: ph-web-builder\nDriver: docker-container\n Driver Options: '+listed+'\nBuildKit daemon flags: --oci-worker-net=bridge\n'
     if args[:2]==['ps','-aq']:return 'abc\n'
     if args[0]=='inspect':return json.dumps([{'HostConfig':{'Memory':8*1024**3,'CpuQuota':400000,'CpuPeriod':100000,'CpuShares':128,'MemorySwap':8*1024**3,'NetworkMode':w.BUILDER_NETWORK},'Config':{'Cmd':w.BUILDER_FLAGS}}])
     return ''
    with mock.patch.object(w,'secure_builder_definition'),mock.patch.object(w,'validate_socket'),mock.patch.object(w,'docker',side_effect=docker),mock.patch.dict(sys.modules,{'web_app_railpack':types.SimpleNamespace(railpack_ready=lambda d:False)}):
     # Le contrôle final compare le conteneur BuildKit aux quotas configurés (et non plus à 3 Go / 1 CPU figés).
     self.assertTrue(w.prerequisites())
    self.assertEqual([c[1] for c in calls if c[:1]==['buildx'] and c[1] in('rm','create')],expected)
    if expected:self.assertIn('--keep-state',calls[[c[1] for c in calls].index('rm')]);self.assertIn('cpu-quota=400000',calls[[c[1] for c in calls].index('create')])
 def test_builder_image_pin_is_optional_strict_and_added_to_driver_options(self):
  image='moby/buildkit@sha256:'+'a'*64
  with mock.patch.dict(os.environ,{},clear=False):
   os.environ.pop('WEB_APPS_BUILDKIT_IMAGE',None)
   self.assertNotIn('image',w.builder_options())
   with mock.patch.dict(os.environ,{'WEB_APPS_BUILDKIT_IMAGE':image}):self.assertEqual(w.builder_options()['image'],image)
   for invalid in ['moby/buildkit:buildx-stable-1','moby/buildkit@sha256:abc','other/buildkit@sha256:'+'a'*64,'moby/buildkit@sha256:'+'A'*64,'moby/buildkit@sha256:'+'a'*64+';true']:
    with mock.patch.dict(os.environ,{'WEB_APPS_BUILDKIT_IMAGE':invalid}),self.assertRaises(RuntimeError):w.builder_options()
  calls=[];actual_image='sha256:'+'b'*64
  def docker(args,**kwargs):
   calls.append(args)
   if args[0]=='info':return json.dumps({'SecurityOptions':['name=rootless'],'CgroupVersion':'2','CgroupDriver':'systemd'})
   if args[:2]==['network','inspect']:return json.dumps([{'Driver':'bridge','Labels':{'pockethost.web.builder':'true'}}])
   if args[:2]==['buildx','inspect']and len(args)==3:raise subprocess.CalledProcessError(1,args)
   if args[:2]==['ps','-aq']:return 'abc\n'
   if args[:2]==['image','inspect']:return json.dumps([{'Id':'sha256:'+'b'*64}])
   if args[0]=='inspect':return json.dumps([{'Image':actual_image,'HostConfig':{'Memory':3*1024**3,'CpuQuota':100000,'CpuPeriod':100000,'CpuShares':128,'MemorySwap':3*1024**3,'NetworkMode':w.BUILDER_NETWORK},'Config':{'Cmd':w.BUILDER_FLAGS}}])
   return ''
  with mock.patch.dict(os.environ,{'WEB_APPS_BUILDKIT_IMAGE':image,'WEB_APPS_BUILD_CPUS':'1','WEB_APPS_BUILD_MEMORY':'3g'}),mock.patch.object(w,'secure_builder_definition'),mock.patch.object(w,'validate_socket'),mock.patch.object(w,'docker',side_effect=docker),mock.patch.dict(sys.modules,{'web_app_railpack':types.SimpleNamespace(railpack_ready=lambda d:False)}):self.assertTrue(w.prerequisites())
  self.assertTrue(any(args[:2]==['buildx','create']and'image='+image in args for args in calls))
  actual_image='sha256:'+'c'*64
  with mock.patch.dict(os.environ,{'WEB_APPS_BUILDKIT_IMAGE':image,'WEB_APPS_BUILD_CPUS':'1','WEB_APPS_BUILD_MEMORY':'3g'}),mock.patch.object(w,'secure_builder_definition'),mock.patch.object(w,'validate_socket'),mock.patch.object(w,'docker',side_effect=docker),mock.patch.dict(sys.modules,{'web_app_railpack':types.SimpleNamespace(railpack_ready=lambda d:False)}),self.assertRaises(RuntimeError):w.prerequisites()
 def test_builder_definition_removes_all_entitlements_and_refuses_unknown_schema(self):
  config=self.root/'buildx';path=config/'instances'/w.BUILDER;path.parent.mkdir(parents=True)
  value={'Name':w.BUILDER,'Driver':'docker-container','Nodes':[{'Name':w.BUILDER+'0','Endpoint':'unix://'+w.SOCKET,'Flags':['--allow-insecure-entitlement=network.host','--allow-insecure-entitlement=security.insecure'],'Files':None}]}
  path.write_text(json.dumps(value))
  with mock.patch.dict(os.environ,{'BUILDX_CONFIG':str(config)}):
   w.secure_builder_definition();self.assertEqual(json.loads(path.read_text())['Nodes'][0]['Flags'],['--oci-worker-net=bridge']);self.assertEqual(path.stat().st_mode&0o777,0o600)
   value['Nodes'][0]['Files']={'buildkitd.toml':'untrusted'};path.write_text(json.dumps(value))
   with self.assertRaises(RuntimeError):w.secure_builder_definition()
   self.assertEqual(json.loads(path.read_text()),value)
 def test_run_accepts_listed_exit_codes_and_enforces_command_limit(self):
  j=self.job()
  with mock.patch.object(w,'resources'),mock.patch.object(w,'check_runtime_budgets'),mock.patch.object(w,'api',return_value={}),mock.patch.object(w,'publish_runtime_stats'):
   self.assertEqual(j.run([sys.executable,'-c','raise SystemExit(75)'],ok=(0,75)),75)
   self.assertEqual(j.run([sys.executable,'-c','pass']),0)
   with self.assertRaises(w.CommandFailed):j.run([sys.executable,'-c','raise SystemExit(75)'])
   started=time.monotonic()
   with self.assertRaises(w.CommandTimeout):j.run([sys.executable,'-c','import time;time.sleep(30)'],limit=1)
   self.assertLess(time.monotonic()-started,12);self.assertIsNone(j.process)
  self.assertTrue(issubclass(w.CommandTimeout,RuntimeError))
 def test_run_watch_aborts_and_kills_the_command(self):
  j=self.job();seen=[]
  def watch():
   seen.append(1)
   if len(seen)>1:raise RuntimeError('sortie trop volumineuse')
  with mock.patch.object(w,'resources'),mock.patch.object(w,'check_runtime_budgets'),mock.patch.object(w,'api',return_value={}),mock.patch.object(w,'publish_runtime_stats'):
   started=time.monotonic()
   with self.assertRaisesRegex(RuntimeError,'sortie trop volumineuse'):j.run([sys.executable,'-c','import time;time.sleep(30)'],watch=watch)
   self.assertLess(time.monotonic()-started,12);self.assertIsNone(j.process)
 def test_worker_starts_without_railpack_module(self):
  # Fichier web_app_railpack.py oublié au déploiement : le worker démarre et publie railpack=false.
  with mock.patch.dict(sys.modules,{'web_app_railpack':None}):
   other=importlib.util.module_from_spec(importlib.util.spec_from_file_location('worker_without_railpack',SOURCE));spec2=other.__spec__;spec2.loader.exec_module(other)
   self.assertTrue(issubclass(other.CommandTimeout,RuntimeError))
   def docker(args,**kwargs):
    if args[0]=='info':return json.dumps({'SecurityOptions':['name=rootless'],'CgroupVersion':'2','CgroupDriver':'systemd'})
    if args[:2]==['network','inspect']:return json.dumps([{'Driver':'bridge','Labels':{'pockethost.web.builder':'true'}}])
    if args[0]=='ps':return 'builder-id'
    if args[0]=='inspect':return json.dumps([{'HostConfig':{'Memory':3*1024**3,'CpuQuota':100000,'CpuPeriod':100000,'CpuShares':128,'MemorySwap':3*1024**3,'NetworkMode':w.BUILDER_NETWORK},'Config':{'Cmd':w.BUILDER_FLAGS}}])
    return ''
   other.RAILPACK_TOOLS=True
   with mock.patch.object(other,'secure_builder_definition'),mock.patch.object(other,'validate_socket'),mock.patch.object(other,'docker',side_effect=docker):self.assertTrue(other.prerequisites())
   self.assertIs(other.RAILPACK_TOOLS,False)
 def test_railpack_configuration_values_are_not_masked_for_auto(self):
  self.value['snapshot']['env']['RAILPACK_NODE_VERSION']={'value':'22','build':True}
  self.assertIn('22',self.job().secrets)
  self.value['snapshot']['runtime']={'kind':'auto'};j=self.job();self.assertNotIn('22',j.secrets);self.assertIn('secret-across-chunks',j.secrets)
  j.log('node 22.11.0 secret-across-chunks\n',True);text=j.logpath.read_text();self.assertIn('node 22.11.0',text);self.assertNotIn('secret-across-chunks',text)
 def test_stop_serializes_the_entire_operation_with_private_database_reconciliation(self):
  import web_app_private_db as private
  j=self.job();lock=threading.RLock();entered=threading.Event();finished=threading.Event()
  j._stop_old=mock.Mock(side_effect=lambda *args:entered.set())
  def stop():
   try:j.stop_old('keep',True)
   finally:finished.set()
  with mock.patch.dict(os.environ,{'WEB_DB_BRIDGE_ENABLED':'true'}),mock.patch.object(private,'_manager',types.SimpleNamespace(operations_lock=lock)):
   with lock:
    thread=threading.Thread(target=stop);thread.start();self.assertFalse(entered.wait(.05))
   thread.join(2);self.assertFalse(thread.is_alive());self.assertTrue(finished.is_set());self.assertTrue(entered.is_set())
  j._stop_old.assert_called_once_with('keep',True)
if __name__=='__main__':unittest.main()
