#!/usr/bin/env python3
"""Construction automatique (Railpack), sans réseau ni Docker : faux job.run, faux docker, vrais Job.log et RuntimeEngine."""
import hashlib,importlib.util,json,os,pathlib,re,sys,tempfile,types,unittest,unittest.mock as mock
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import web_app_railpack as rp
import web_app_runtime as runtime
spec=importlib.util.spec_from_file_location('worker_railpack',pathlib.Path(__file__).with_name('web-app-worker.py'));w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)

APP,DEP='b'*15,'a'*15
BUILDER='ghcr.io/railwayapp/railpack-builder:mise-'+rp.RAILPACK['mise'];RUNTIME='ghcr.io/railwayapp/railpack-runtime:mise-'+rp.RAILPACK['mise']
SOCKET='/run/user/1001/docker.sock';KEY='k'*40;IMAGE_ID='sha256:'+'9'*64
def plan():
 return {'steps':[{'name':'packages:mise','inputs':[{'image':BUILDER}],'commands':[{'path':'/mise/shims'},{'cmd':'mise install'}],'caches':['mise'],'secrets':['*']},
  {'name':'build','inputs':[{'step':'packages:mise'}],'commands':[{'cmd':'npm run build'},{'image':'example.org/tools:1','src':'/x','dest':'/x'}],'caches':['npm-install'],'secrets':['UNKNOWN']}],
  'caches':{'mise':{'directory':'/mise','type':'shared'},'npm-install':{'directory':'/root/.npm','type':'shared'}},'secrets':['STALE'],
  'deploy':{'base':{'image':RUNTIME},'inputs':[{'step':'build','include':['.']}],'startCommand':'npm run start'}}
def info(**extra):
 return {'detectedProviders':['node'],'resolvedPackages':{'node':{'name':'node','requestedVersion':'22','resolvedVersion':'22.11.0'},'pnpm':{'name':'pnpm','resolvedVersion':'10.4.1'}},'logs':[{'level':'info','msg':'secret-build-value appears in a log'}],**extra}


class Base(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name).resolve();self.previous=w.ROOT;w.ROOT=self.root
  self.context=self.root/'source';self.context.mkdir();(self.context/'package.json').write_text('{"scripts":{"start":"node index.js"}}');(self.context/'index.js').write_text('')
  self.env={'API_KEY':{'value':'secret-build-value','build':True,'runtime':False},'RAILPACK_SPA_OUTPUT_DIR':{'value':'dist','build':True,'runtime':False},'RUNTIME_ONLY':{'value':'runtime-only-value','build':False,'runtime':True}}
  self.runtime={'kind':'auto','startCommand':'npm run start','buildCommand':'npm run build'};self.config={'repository':'owner/repo','rootDirectory':'.'}
  self.codes=[0];self.calls=[];self.docker_calls=[];self.requests=[];self.plan=plan();self.info=info();self.raw_info=None;self.write_info=True;self.frontend=True
  rp._hashes.clear();rp._pull_after[0]=0;rp._pulling.clear()
 def tearDown(self):w.ROOT=self.previous;self.tmp.cleanup()
 def engine(self):
  job=w.Job({'id':DEP,'appId':APP,'action':'deploy','commit':'c'*40,'snapshot':{'config':self.config,'env':self.env,'runtime':self.runtime,**getattr(self,'extra',{})}})
  def run(args,cwd=None,env=None,budget=True,limit=None,ok=(0,),watch=None):
   self.calls.append({'args':list(args),'env':dict(env or {}),'limit':limit,'ok':ok,'watch':watch})
   if 'prepare' not in args:return 0
   code=self.codes.pop(0) if self.codes else 0
   if isinstance(code,BaseException):raise code
   mount=lambda name:pathlib.Path(next(a for a in args if a.endswith(',dst=/out/'+name)).split('src=',1)[1].split(',dst=')[0])
   if self.write_info:mount('info.json').write_text(self.raw_info if self.raw_info is not None else json.dumps(self.info))
   if code==0:mount('plan.json').write_text(self.plan if isinstance(self.plan,str) else json.dumps(self.plan))
   watch()
   if code not in ok:raise w.CommandFailed('échec')
   return code
  job.run=run
  def docker(args,**kwargs):
   self.docker_calls.append(list(args))
   if args[:2]==['image','inspect']:
    if args[2]==rp.pinned(rp.RAILPACK['frontend']) and not self.frontend:raise w.subprocess.CalledProcessError(1,args)
    return json.dumps([{'Id':'sha256:'+'f'*64,'Config':{'Labels':{}}}])
   return ''
  def request(method,path,body=None):
   self.requests.append((method,path,body))
   return {'Id':IMAGE_ID}if path.startswith('/commit')else{'Id':'e'*64}
  engine=runtime.RuntimeEngine(job,docker,request,self.root,SOCKET);engine.hmac_key=KEY
  return engine
 def build(self,engine=None,work=None):
  engine=engine or self.engine();work=work or pathlib.Path(tempfile.mkdtemp(dir=self.root))
  with mock.patch.object(rp,'tools_ready',return_value=True),mock.patch.object(rp.time,'sleep'):return engine.build(self.context,work)
 def prepares(self):return [call for call in self.calls if 'prepare' in call['args']]
 def buildx(self):return next(call for call in self.calls if 'buildx' in call['args'])
 def removed(self):return [args for args in self.docker_calls if args==['rm','-f','ph-web-'+APP+'-'+DEP+'-railpack']]
 def error(self):
  with self.assertRaises(RuntimeError)as caught:self.build()
  return str(caught.exception)


class ProfileTests(Base):
 def test_1_profile(self):
  value=runtime.validate_profile({'kind':'auto'});self.assertEqual((value['port'],value['command'],value['entrypoint']),(3000,[],[]));self.assertNotIn('startCommand',value)
  self.assertEqual(runtime.validate_profile({'kind':'auto','startCommand':'uvicorn main:app','buildCommand':''})['startCommand'],'uvicorn main:app')
  for bad in [{'startCommand':'a\nb'},{'startCommand':'a\rb'},{'startCommand':'x'*1001},{'buildCommand':'a\x00b'},{'startCommand':['npm']},{'command':['npm','start']},{'entrypoint':['sh']},{'buildTarget':'build'},{'unknown':1}]:
   with self.assertRaises(RuntimeError,msg=bad):runtime.validate_profile({'kind':'auto',**bad})
  with self.assertRaises(RuntimeError):runtime.validate_profile({'kind':'dockerfile','startCommand':'npm start'})
  self.assertIn('auto',runtime.KINDS)


class ContextTests(Base):
 def test_2_copy_excludes_private_and_generated_trees_at_every_level(self):
  for folder in ['.git','node_modules','.next','sub/.git','sub/node_modules','sub/.next','sub/keep']:(self.context/folder).mkdir(parents=True,exist_ok=True);(self.context/folder/'f').write_text('x')
  for name in ['.env','.env.production','sub/.env','sub/.env.local']:(self.context/name).write_text('SECRET=1')
  (self.context/'.envrc').write_text('kept');os.symlink('index.js',self.context/'alias.js')
  src=rp.copy_context(self.context,'.',self.root)
  names={str(p.relative_to(src))for p in src.rglob('*')}
  for absent in ['.git','node_modules','.next','.env','.env.production','sub/.git','sub/node_modules','sub/.next','sub/.env','sub/.env.local']:self.assertNotIn(absent,names)
  for present in ['package.json','sub/keep/f','.envrc','alias.js']:self.assertIn(present,names)
  self.assertTrue((src/'alias.js').is_symlink())
 def test_2_copy_is_readable_by_the_unprivileged_site_user(self):
  # Dépôt récupéré sous umask 027 : l'UID 10001 du site doit pouvoir lire public/ dans l'image.
  (self.context/'public').mkdir();(self.context/'public/logo.svg').write_text('<svg/>');(self.context/'run.sh').write_text('#!/bin/sh')
  (self.context/'public/logo.svg').chmod(0o640);(self.context/'run.sh').chmod(0o750);(self.context/'public').chmod(0o750)
  src=rp.copy_context(self.context,'.',self.root)
  self.assertEqual({name:(src/name).stat().st_mode&0o777 for name in ['.','public','public/logo.svg','run.sh','package.json']},{'.':0o755,'public':0o755,'public/logo.svg':0o644,'run.sh':0o755,'package.json':0o644})
 def test_2_symlinks_and_root_directory_are_confined(self):
  for index,target in enumerate(['/etc/passwd','../../','../outside']):
   context=self.root/('c'+str(index));context.mkdir();os.symlink(target,context/'x')
   with self.assertRaisesRegex(RuntimeError,'Lien symbolique hors du projet refusé'):rp.copy_context(context,'.',self.root/('w'+str(index)))
  for bad in ['../x','missing']:
   with self.assertRaisesRegex(RuntimeError,'Dossier du projet invalide'):rp.copy_context(self.context,bad,self.root/'bad')
  (self.context/'apps/web').mkdir(parents=True);(self.context/'apps/web/index.html').write_text('<p>')
  src=rp.copy_context(self.context,'apps/web',self.root/'ok')
  self.assertEqual(sorted(p.name for p in src.iterdir()),['index.html'])


class PrepareTests(Base):
 def test_3_prepare_is_a_confined_disposable_container(self):
  w.atomic(self.root/'railpack-versions'/(APP+'.json'),['node@22.11.0','bad value','pnpm@10.4.1'])
  with mock.patch.dict(os.environ,{'GITHUB_TOKEN':'ghs_marker_token'}):self.assertEqual(self.build(),IMAGE_ID)
  call=self.prepares()[0];args=call['args'];text=' '.join(args)
  self.assertEqual((call['limit'],call['ok']),(300,(0,1,75)))
  for flag in ['--read-only','--security-opt no-new-privileges','--memory 512m','--memory-swap 512m','--pids-limit 128','--network bridge','--cap-drop ALL','--cpus 0.5','--user 0:0','--rm']:self.assertIn(flag,text)
  self.assertEqual(args[args.index('--tmpfs')+1],'/tmp:rw,exec,nosuid,nodev,size=256m')
  # /out n'est jamais monté en dossier : seuls deux fichiers réguliers pré-créés, surveillés par watch.
  self.assertFalse(any(a.endswith(',dst=/out') for a in args))
  outs=[a for a in args if ',dst=/out/' in a];self.assertEqual([a.rsplit('dst=',1)[1] for a in outs],['/out/plan.json','/out/info.json']);self.assertTrue(callable(call['watch']))
  for a in outs:self.assertTrue(pathlib.Path(a.split('src=',1)[1].split(',dst=')[0]).is_file())
  self.assertIn('--label pockethost.web.role=railpack-prepare',text);self.assertNotIn('pockethost.web=true',text)
  self.assertIn('type=bind,src='+rp.RAILPACK['binary']+',dst=/usr/local/bin/ph-railpack,readonly',args)
  self.assertIn('type=bind,src='+rp.RAILPACK['miseBinary']+',dst=/tmp/railpack/mise/mise-'+rp.RAILPACK['mise']+',readonly',args)
  self.assertTrue(any(re.fullmatch(r'type=bind,src=.*/railpack-src,dst=/app,readonly',a)for a in args))
  image=args[args.index('--entrypoint')+2];self.assertEqual(args[args.index('--entrypoint')+1],'/usr/local/bin/ph-railpack');self.assertIn('@sha256:',image);self.assertEqual(image,rp.pinned(rp.RAILPACK['frontend']))
  self.assertEqual(args[args.index('--start-cmd')+1],'npm run start');self.assertEqual(args[args.index('--build-cmd')+1],'npm run build')
  self.assertEqual(args[args.index('--env')+1],'RAILPACK_SPA_OUTPUT_DIR=dist');self.assertEqual(args.count('--env'),1)
  self.assertEqual([args[i+1]for i,a in enumerate(args)if a=='--previous'],['node@22.11.0','pnpm@10.4.1'])
  for call in self.calls:
   self.assertNotIn('secret-build-value',json.dumps(call['args']));self.assertNotIn('runtime-only-value',json.dumps(call['args']))
   self.assertNotIn('ghs_marker_token',json.dumps(call['args']));self.assertNotIn('GITHUB_TOKEN',call['env'])
  self.assertEqual(len(self.removed()),1)
  self.assertEqual(json.loads((self.root/'railpack-versions'/(APP+'.json')).read_text()),['node@22.11.0','pnpm@10.4.1'])
  self.assertEqual((self.root/'railpack-versions'/(APP+'.json')).stat().st_mode&0o777,0o640)
 def test_3_container_is_removed_after_failure_and_timeout(self):
  self.codes=[2];self.assertIn('échec',self.error());self.assertEqual(len(self.removed()),1)
  self.docker_calls.clear();self.codes=[w.CommandTimeout('délai')];self.assertEqual(self.error(),'Analyse Railpack trop longue (5 minutes maximum).');self.assertEqual(len(self.removed()),1)
  self.docker_calls.clear();self.codes=[w.JobCancelled('annulé')]
  with self.assertRaises(w.JobCancelled):self.build()
  self.assertEqual(len(self.removed()),1)
 def test_3_railpack_configuration_values_are_single_line(self):
  self.env['RAILPACK_PACKAGES']={'value':'node\nrm','build':True}
  self.assertEqual(self.error(),'Variable RAILPACK_PACKAGES invalide (une ligne, 4096 caractères maximum).')
 def test_3_missing_railpack_is_refused_in_french(self):
  with mock.patch.object(rp,'tools_ready',return_value=False):
   with self.assertRaisesRegex(RuntimeError,'Railpack n’est pas installé sur ce serveur.*0\\.42\\.0.*install-railpack\\.sh'):self.engine().build(self.context,self.root)
 def test_3_worker_secret_is_required(self):
  engine=self.engine();engine.hmac_key='court'
  with self.assertRaisesRegex(RuntimeError,'Secret du worker requis'):self.build(engine)
  self.assertEqual(self.calls,[])
 def test_3_missing_frontend_is_pulled_inside_the_job(self):
  self.frontend=False;self.assertEqual(self.build(),IMAGE_ID)
  pull=self.calls[0];self.assertEqual(pull['args'][-2:],['pull',rp.pinned(rp.RAILPACK['frontend'])]);self.assertEqual(pull['limit'],300);self.assertNotIn('GITHUB_TOKEN',pull['env'])
  self.calls.clear();self.frontend=False;fail=self.engine();original=fail.job.run
  def slow(args,**kwargs):
   if 'pull' in args:raise w.CommandTimeout('délai')
   return original(args,**kwargs)
  fail.job.run=slow
  with self.assertRaisesRegex(RuntimeError,'Téléchargement du frontend Railpack trop long'):self.build(fail)


class ExitCodeTests(Base):
 def test_4_network_retry_once(self):
  self.codes=[75,0];self.assertEqual(self.build(),IMAGE_ID);self.assertEqual(len(self.prepares()),2);self.assertEqual(len(self.removed()),2)
  self.calls.clear();self.codes=[75,75]
  self.assertEqual(self.error(),'Analyse Railpack interrompue par un problème réseau temporaire (résolution des versions). Relancez le déploiement.');self.assertEqual(len(self.prepares()),2)
 def test_4_unknown_language_and_refusal_messages(self):
  self.codes=[1];self.info=info(detectedProviders=[]);self.config['rootDirectory']='.'
  self.assertIn('Railpack n’a reconnu aucun langage dans « . »',self.error())
  self.codes=[1];self.info=info(logs=[{'level':'warn','msg':'w'},{'level':'error','msg':'secret-build-value '+'x'*400}])
  message=self.error();self.assertTrue(message.startswith('Analyse Railpack refusée : '));self.assertNotIn('secret-build-value',message);self.assertLessEqual(len(message),len('Analyse Railpack refusée : ')+300)
 def test_4_unreadable_info_or_plan(self):
  self.raw_info='not json';self.assertEqual(self.error(),'Plan Railpack illisible.')
  self.raw_info=None;self.plan=None;self.assertEqual(self.error(),'Plan Railpack illisible.')
  big=self.root/'big.json';big.write_text('{"a":"'+'x'*(2*1024**2)+'"}')
  with self.assertRaisesRegex(RuntimeError,'Plan Railpack illisible'):rp._read(big)
 def test_4_generation_error_without_info_is_a_refusal(self):
  # cli/prepare.go : erreur de GenerateBuildResultForCommand -> code 1 avant writeInfoFile.
  self.codes=[1];self.write_info=False;self.assertEqual(self.error(),'Analyse Railpack refusée : consultez le journal de construction.')
  self.codes=[0];self.write_info=False;self.assertEqual(self.error(),'Plan Railpack illisible.')
 def test_4_special_files_never_block_and_output_is_bounded(self):
  fifo=self.root/'fifo.json';os.mkfifo(fifo);folder=self.root/'dir.json';folder.mkdir();link=self.root/'link.json';os.symlink(self.root/'big',link);(self.root/'big').write_text('{}')
  for path in [fifo,folder,link,self.root/'absent.json']:
   with self.assertRaisesRegex(RuntimeError,'Plan Railpack illisible'):rp._read(path)
  self.assertFalse(rp._written(self.root/'absent.json'))
  self.plan='{"a":"'+'x'*(2*1024**2)+'"}';self.assertEqual(self.error(),'Analyse Railpack refusée : sortie trop volumineuse (2 Mio maximum).');self.assertEqual(len(self.removed()),1)


class PlanTests(Base):
 def test_5_plan_is_rewritten(self):
  value,custom=rp.validate_plan(plan(),APP,{},['B_VAR','A_VAR'])
  self.assertEqual(set(value['caches']),{'ph-web-'+APP+'-mise','ph-web-'+APP+'-npm-install'})
  self.assertEqual(value['steps'][0]['caches'],['ph-web-'+APP+'-mise']);self.assertEqual(value['steps'][1]['caches'],['ph-web-'+APP+'-npm-install'])
  self.assertEqual(value['secrets'],['A_VAR','B_VAR']);self.assertEqual(value['steps'][0]['secrets'],['*']);self.assertEqual(value['steps'][1]['secrets'],['*'])
  self.assertEqual(value['steps'][0]['inputs'][0]['image'],BUILDER+'@'+rp.RAILPACK['images'][BUILDER]);self.assertEqual(value['deploy']['base']['image'],RUNTIME+'@'+rp.RAILPACK['images'][RUNTIME])
  self.assertEqual(custom,['example.org/tools:1']);self.assertEqual(value['steps'][1]['commands'][1]['image'],'example.org/tools:1')
  self.assertEqual(plan()['caches'].keys(),{'mise','npm-install'})
  # Sous-liste -> ['*'] (sinon hachage dans alpine:latest avec tous les secrets) ; [] reste [].
  subset=plan();subset['steps'][1]['secrets']=['A_VAR'];subset['steps'][0]['secrets']=[];value=rp.validate_plan(subset,APP,{},['A_VAR'])[0]
  self.assertEqual(value['steps'][1]['secrets'],['*']);self.assertEqual(value['steps'][0]['secrets'],[])
 def test_5_real_railpack_0_42_plan(self):
  # Plan réellement produit par railpack v0.42.0 (projet Node minimal) : il annonce "$schema", retiré avant le frontend.
  real=json.loads('{"$schema":"https://schema.railpack.com","caches":{"apt":{"directory":"/var/cache/apt","type":"locked"},"apt-lists":{"directory":"/var/lib/apt/lists","type":"locked"},"node-modules":{"directory":"/app/node_modules/.cache","type":"shared"},"npm-install":{"directory":"/root/.npm","type":"shared"}},"deploy":{"base":{"step":"packages:apt:runtime"},"inputs":[{"include":["/mise/shims","/mise/installs","/usr/local/bin/mise","/etc/mise/config.toml","/root/.local/state/mise"],"step":"packages:mise"},{"include":["/app/node_modules"],"step":"build"},{"exclude":["node_modules",".yarn"],"include":["/root/.cache","."],"step":"build"}],"startCommand":"npm run start","variables":{"CI":"true","NODE_ENV":"production","NPM_CONFIG_FETCH_RETRIES":"5","NPM_CONFIG_FUND":"false","NPM_CONFIG_PRODUCTION":"false","NPM_CONFIG_UPDATE_NOTIFIER":"false","RAILPACK_VERSION":"0.42.0"}},"steps":[{"assets":{"generated-mise-toml":"[tools]\\n  node = \\"24.22.0\\"\\n\\n[settings]\\n  idiomatic_version_file_enable_tools = [\\"python\\", \\"node\\", \\"ruby\\", \\"elixir\\", \\"go\\", \\"java\\", \\"yarn\\", \\"pnpm\\", \\"bun\\", \\"deno\\", \\"dotnet\\", \\"rust\\"]\\n  minimum_release_age = \\"14d\\"\\n  paranoid = true\\n  trusted_config_paths = [\\"/app\\"]\\n  [settings.node]\\n    verify = false\\n"},"commands":[{"path":"/mise/shims"},{"customName":"create mise config","name":"generated-mise-toml","path":"/etc/mise/config.toml"},{"cmd":"mise install","customName":"install mise packages: node"}],"inputs":[{"image":"ghcr.io/railwayapp/railpack-builder:mise-2026.10.8"}],"name":"packages:mise","variables":{"MISE_CACHE_DIR":"/mise/cache","MISE_CONFIG_DIR":"/mise","MISE_DATA_DIR":"/mise","MISE_INSTALLS_DIR":"/mise/installs","MISE_SHIMS_DIR":"/mise/shims"}},{"caches":["npm-install"],"commands":[{"path":"/app/node_modules/.bin"},{"cmd":"mkdir -p /app/node_modules/.cache"},{"dest":"package.json","src":"package.json"},{"cmd":"npm install"}],"inputs":[{"step":"packages:mise"}],"name":"install","variables":{"CI":"true","NODE_ENV":"production","NPM_CONFIG_FETCH_RETRIES":"5","NPM_CONFIG_FUND":"false","NPM_CONFIG_PRODUCTION":"false","NPM_CONFIG_UPDATE_NOTIFIER":"false"}},{"caches":["node-modules"],"commands":[{"cmd":"npm run build"}],"inputs":[{"step":"install"},{"include":["."],"local":true}],"name":"build","secrets":["*"]},{"caches":["apt","apt-lists"],"commands":[{"cmd":"sh -c \'apt-get update && apt-get install -y libatomic1\'","customName":"install apt packages: libatomic1"}],"inputs":[{"image":"ghcr.io/railwayapp/railpack-runtime:mise-2026.10.8"}],"name":"packages:apt:runtime"}]}')
  value,custom=rp.validate_plan(real,APP,{},['A_VAR'])
  self.assertNotIn('$schema',value);self.assertEqual(custom,[]);self.assertEqual(value['deploy']['startCommand'],'npm run start')
  self.assertEqual(set(value['caches']),{'ph-web-'+APP+'-'+name for name in real['caches']})
  self.assertTrue(all('@sha256:' in item['image'] for step in value['steps'] for item in step.get('inputs',[]) if 'image' in item))
  # Seule l'étape qui reçoit le projet local se termine par la remise des droits de lecture pour l'UID 10001.
  self.assertEqual({step['name']:step['commands'][-1]['cmd'] for step in value['steps'] if step['commands'][-1]['cmd']==rp.READABLE},{'build':rp.READABLE})
  with self.assertRaisesRegex(RuntimeError,'inattendu'):rp.validate_plan({**real,'$schema':5},APP,{},[])
 def test_5_plan_refusals(self):
  for change,message in [(lambda p:p['steps'][0].update(caches=['unknown']),'inattendu'),(lambda p:p.update(extra=1),'inattendu'),(lambda p:p['deploy'].pop('startCommand'),'aucune commande de démarrage'),(lambda p:p['deploy'].update(startCommand='  '),'aucune commande de démarrage'),(lambda p:p['deploy']['base'].update(image='x'*300),'inattendu'),(lambda p:p['steps'][0]['inputs'][0].update(image=5),'inattendu')]:
   value=plan();change(value)
   with self.assertRaisesRegex(RuntimeError,message):rp.validate_plan(value,APP,{},[])
  with self.assertRaisesRegex(RuntimeError,'Nom de variable de build invalide'):rp.validate_plan(plan(),APP,{},['BAD-NAME'])


class BuildxTests(Base):
 def test_6_buildx_arguments_and_secret_files(self):
  self.build();args=self.buildx()['args'];text=' '.join(args)
  self.assertIn('BUILDKIT_SYNTAX='+rp.RAILPACK['frontend'],args);self.assertIn('@sha256:',rp.RAILPACK['frontend'])
  cache=next(a for a in args if a.startswith('cache-key='));self.assertRegex(cache,'^cache-key=ph-web-'+APP+'-[a-f0-9]{16}$')
  import hmac as _hmac;self.assertEqual(cache.rsplit('-',1)[1],_hmac.new(KEY.encode(),('railpack-cache:'+APP).encode(),hashlib.sha256).hexdigest()[:16])
  self.assertEqual(args[args.index('--builder')+1],'ph-web-builder');self.assertNotIn('github-token',text);self.assertNotIn('secret-build-value',text);self.assertNotIn('--platform',args)
  digest=next(a for a in args if a.startswith('secrets-hash=')).split('=',1)[1];self.assertRegex(digest,'^[a-f0-9]{64}$')
  secrets=[args[i+1]for i,a in enumerate(args)if a=='--secret'];self.assertEqual([s.split(',')[0]for s in secrets],['id=API_KEY','id=RAILPACK_SPA_OUTPUT_DIR'])
  for item in secrets:
   path=pathlib.Path(item.split('src=',1)[1]);self.assertEqual(path.stat().st_mode&0o777,0o600);self.assertEqual(path.parent.stat().st_mode&0o777,0o700)
  self.assertEqual(pathlib.Path(secrets[0].split('src=',1)[1]).read_text(),'secret-build-value')
  planfile=pathlib.Path(args[args.index('-f')+1]);context=pathlib.Path(args[-1])
  self.assertEqual(context.name,'railpack-src');self.assertFalse(planfile.is_relative_to(context));self.assertEqual(planfile.stat().st_mode&0o777,0o600)
  written=json.loads(planfile.read_text());self.assertEqual(written['secrets'],['API_KEY','RAILPACK_SPA_OUTPUT_DIR']);self.assertTrue(all(k.startswith('ph-web-'+APP+'-')for k in written['caches']))
  self.assertEqual(args[args.index('-t')+1],'ph-web/'+APP+':'+DEP)
  self.calls.clear();self.build();self.assertIn('secrets-hash='+digest,self.buildx()['args'])
  self.calls.clear();self.env['API_KEY']['value']='changed';self.build();self.assertNotIn('secrets-hash='+digest,self.buildx()['args'])
 def test_6_full_rebuild_ignores_layer_and_package_caches(self):
  self.build();args=self.buildx()['args'];self.assertNotIn('--no-cache',args);cache=next(a for a in args if a.startswith('cache-key='))
  self.calls.clear();self.extra={'fullRebuild':True};self.build();args=self.buildx()['args']
  self.assertIn('--no-cache',args);self.assertEqual(next(a for a in args if a.startswith('cache-key=')),cache+'-'+DEP)
 def test_3_prune_dependencies_option(self):
  self.runtime={**self.runtime,'pruneDependencies':True};self.build();args=self.prepares()[0]['args']
  self.assertEqual(sorted(args[i+1] for i,a in enumerate(args) if a=='--env'),['RAILPACK_PRUNE_DEPS=true','RAILPACK_SPA_OUTPUT_DIR=dist'])
  # Une variable du site reste prioritaire sur la case du profil.
  self.calls.clear();self.env['RAILPACK_PRUNE_DEPS']={'value':'false','build':True,'runtime':False};self.build();args=self.prepares()[0]['args']
  self.assertIn('RAILPACK_PRUNE_DEPS=false',args);self.assertNotIn('RAILPACK_PRUNE_DEPS=true',args)
  self.assertTrue(runtime.validate_profile({'kind':'auto','pruneDependencies':True})['pruneDependencies']);self.assertNotIn('pruneDependencies',runtime.validate_profile({'kind':'auto','pruneDependencies':False}))
  for bad in [{'kind':'dockerfile','pruneDependencies':True},{'kind':'auto','pruneDependencies':1},{'kind':'auto','pruneDependencies':'true'}]:
   with self.assertRaises(RuntimeError):runtime.validate_profile(bad)
 def test_6_no_build_variables_means_empty_hash_and_no_secret(self):
  self.env={};self.build();args=self.buildx()['args'];self.assertIn('secrets-hash=',args);self.assertNotIn('--secret',args)


class OwnershipTests(Base):
 def test_7_image_is_reowned(self):
  self.assertEqual(self.build(),IMAGE_ID)
  self.assertIn(['image','inspect','ph-web/'+APP+':'+DEP],self.docker_calls)
  method,path,body=next(r for r in self.requests if r[1].startswith('/commit'))
  self.assertIn('repo=ph-web%2F'+APP,path);self.assertIn('tag='+DEP,path);self.assertEqual(body['User'],'10001:10001')
  self.assertEqual({k:body['Labels'][k]for k in ['pockethost.web','pockethost.web.app','pockethost.web.deployment']},{'pockethost.web':'true','pockethost.web.app':APP,'pockethost.web.deployment':DEP})


class JournalTests(Base):
 def test_8_detection_is_logged_and_masked(self):
  engine=self.engine();self.build(engine);text=engine.job.logpath.read_text()
  self.assertIn('Railpack 0.42.0 · fournisseur détecté : node',text);self.assertIn('Outils résolus : node 22.11.0, pnpm 10.4.1',text);self.assertIn('Commande de démarrage : npm run start',text)
  self.assertIn('Images de base : '+BUILDER+'@sha256:',text);self.assertIn('Image de base personnalisée (non épinglée par la plateforme) : example.org/tools:1',text)
  self.assertNotIn('secret-build-value',text);self.assertIn('[masqué] appears in a log',text)
  self.assertIn('secret-build-value',engine.job.secrets);self.assertNotIn('dist',engine.job.secrets)
 def test_8_php_provider_images_are_labelled_as_floating_tags(self):
  self.plan['steps'][0]['inputs']=[{'image':'dunglas/frankenphp:php8.4.3-trixie'}];self.plan['steps'][1]['commands'][1]['image']='composer:latest'
  engine=self.engine();self.build(engine);text=engine.job.logpath.read_text()
  for image in ['dunglas/frankenphp:php8.4.3-trixie','composer:latest']:self.assertIn('Image imposée par le fournisseur Railpack, tag mobile non épinglé par la plateforme : '+image,text)
  self.assertNotIn('Image de base personnalisée',text)
  self.assertEqual(rp.providers({'detectedProviders':[{'name':'python'},'go',3]}),['python','go']);self.assertEqual(rp.packages({'resolvedPackages':[{'name':'go','resolvedVersion':'1.23.4'},'x']}),[('go','1.23.4')]);self.assertEqual(rp.packages({'resolvedPackages':'x'}),[])


class ReadyTests(Base):
 def files(self):
  folder=self.root/'opt';folder.mkdir(exist_ok=True);folder.chmod(0o755);binary=folder/'railpack';mise=folder/'mise'
  binary.write_bytes(b'railpack');mise.write_bytes(b'mise');binary.chmod(0o755);mise.chmod(0o755)
  return {**rp.RAILPACK,'binary':str(binary),'sha256':hashlib.sha256(b'railpack').hexdigest(),'miseBinary':str(mise),'miseSha256':hashlib.sha256(b'mise').hexdigest()}
 def test_9_railpack_ready(self):
  config=self.files();ok=lambda *a,**k:'[]'
  def missing(args,**kwargs):raise w.subprocess.CalledProcessError(1,args)
  pulls=[]
  def pull_ok(args,**kwargs):
   if args[0]=='image':raise w.subprocess.CalledProcessError(1,args)
   pulls.append(args);return ''
  with mock.patch.object(rp,'OWNER',os.getuid()):
   self.assertTrue(rp.railpack_ready(ok,config))
   # Frontend absent : réponse immédiate False, téléchargement en arrière-plan (jamais dans la boucle principale).
   self.assertFalse(rp.railpack_ready(pull_ok,config));rp._pulling[0].join(5);self.assertEqual(pulls,[['pull',rp.pinned(rp.RAILPACK['frontend'])]])
   self.assertFalse(rp.railpack_ready(missing,config));rp._pulling[0].join(5);self.assertGreater(rp._pull_after[0],rp.time.monotonic())
   self.assertFalse(rp.railpack_ready(pull_ok,config));self.assertEqual(len(pulls),1)
   self.assertFalse(rp.railpack_ready(ok,{**config,'sha256':'0'*64}))
   link=self.root/'opt'/'link';os.symlink(config['binary'],link);self.assertFalse(rp.railpack_ready(ok,{**config,'binary':str(link)}))
   pathlib.Path(config['miseBinary']).chmod(0o775);self.assertFalse(rp.railpack_ready(ok,config));pathlib.Path(config['miseBinary']).chmod(0o755)
   self.assertFalse(rp.railpack_ready(ok,{**config,'binary':str(self.root/'absent')}))
   self.assertTrue(rp.railpack_ready(ok,config))
  self.assertFalse(rp.railpack_ready(ok,config))
  self.assertEqual(rp.pinned('ghcr.io/a/b:v1@sha256:'+'1'*64),'ghcr.io/a/b@sha256:'+'1'*64);self.assertEqual(rp.pinned('host:5000/b:v1@sha256:'+'1'*64),'host:5000/b@sha256:'+'1'*64)


class ConstantTests(unittest.TestCase):
 def test_10_install_script_matches_constants(self):
  path=os.environ.get('RAILPACK_INSTALL_SCRIPT','')
  if not path or not pathlib.Path(path).is_file():self.skipTest('RAILPACK_INSTALL_SCRIPT absent')
  values=dict(re.findall(r'^([A-Z_0-9]+)=(\S+)$',pathlib.Path(path).read_text(),re.M))
  self.assertEqual(values['RAILPACK_VERSION'],rp.RAILPACK['version']);self.assertEqual(values['RAILPACK_BIN_SHA256'],rp.RAILPACK['sha256'])
  self.assertEqual(values['MISE_VERSION'],rp.RAILPACK['mise']);self.assertEqual(values['MISE_BIN_SHA256'],rp.RAILPACK['miseSha256']);self.assertEqual(values['FRONTEND_REF'],rp.RAILPACK['frontend'])
  self.assertTrue(rp.RAILPACK['binary'].endswith('/bin/railpack'));self.assertTrue(rp.RAILPACK['miseBinary'].endswith('/mise-'+rp.RAILPACK['mise']))


if __name__=='__main__':unittest.main()
