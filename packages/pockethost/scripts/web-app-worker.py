#!/usr/bin/env python3
"""One-worker Next.js deployment engine. Never uses the PocketBase Docker daemon."""
import os,sys,json,pathlib,time,datetime,subprocess,urllib.request,urllib.error,tempfile,tarfile,shutil,signal,fcntl,re,secrets,base64,socket,shlex,hashlib,hmac,threading,contextlib,io
os.umask(0o027)
ROOT=pathlib.Path(os.environ.get('WEB_APPS_ROOT','/home/pockethost-web/data')).absolute()
SOCKET=os.environ.get('WEB_APPS_DOCKER_SOCKET','')
CONTROL=os.environ.get('WEB_APPS_CONTROL_URL','http://127.0.0.1:8091').rstrip('/')
SECRET=os.environ.get('WEB_APPS_WORKER_SECRET','')
BUILDER='ph-web-builder'
BUILDER_NETWORK='ph-web-build-network'
BUILDER_FLAGS=['--oci-worker-net=bridge']
GIT_TOOLS=False
RAILPACK_TOOLS=False
CERT_HEARTBEAT_SECONDS=10
# Empreinte du code de construction : toute mise à jour du worker invalide les images réutilisables.
RECIPE=hashlib.sha256(b''.join(path.read_bytes() for path in sorted([pathlib.Path(__file__).resolve(),*pathlib.Path(__file__).resolve().parent.glob('web_app_*.py')]))).hexdigest()
STAGES={'restore-backup':'Restauration de la sauvegarde','apply-runtime':'Application des variables','fetch':'Récupération du commit','build':'Construction Next.js','start':'Démarrage de la nouvelle version','health':'Contrôle de santé','publish':'Publication','stop':'Arrêt','domain':'Certificat HTTPS','domains-sync':'Mise à jour des domaines'}
STAGE_BUILD={'auto':'Construction automatique (Railpack)'}

def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def atomic(path,value):
 path.parent.mkdir(parents=True,exist_ok=True,mode=0o750);tmp=path.with_name(path.name+'.'+secrets.token_hex(4)+'.tmp')
 with open(tmp,'x') as f:json.dump(value,f)
 tmp.chmod(0o640);os.replace(tmp,path)
def load(path,default=None):
 try:return json.loads(path.read_text())
 except (FileNotFoundError,ValueError):return {} if default is None else default

def api(path,body):
 if not re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{3,4}',CONTROL)or not 1024<=int(CONTROL.rsplit(':',1)[1])<=65535 or len(SECRET)<32:raise RuntimeError('Contrôleur loopback et secret privé requis.')
 request=urllib.request.Request(CONTROL+'/api/web-apps/_worker/'+path,headers={'Content-Type':'application/json','X-Web-Worker':SECRET},data=json.dumps(body).encode())
 with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=30)as response:
  raw=response.read(16*1024**2+1)
  if len(raw)>16*1024**2:raise RuntimeError('Réponse du contrôleur trop volumineuse.')
  return json.loads(raw)

def docker(args,**kwargs):
 return subprocess.check_output(['docker','--host','unix://'+SOCKET,*args],stderr=subprocess.DEVNULL,text=True,cwd=kwargs.pop('cwd',ROOT),timeout=kwargs.pop('timeout',20),**kwargs)

def docker_json(method,path,body=None,binary=False):
 import http.client
 class UnixHTTP(http.client.HTTPConnection):
  def connect(self):
   self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(self.timeout);self.sock.connect(SOCKET)
 validate_socket(SOCKET)
 connection=UnixHTTP('localhost',timeout=30)
 try:
  payload=None if body is None else json.dumps(body).encode()
  connection.request(method,'/v1.47'+path,body=payload,headers={'Content-Type':'application/json'})
  response=connection.getresponse();raw=response.read(65537)
  if len(raw)>65536:raise RuntimeError('Réponse du moteur web trop volumineuse.')
  if not 200<=response.status<300:raise RuntimeError('Le moteur web a refusé le démarrage du conteneur (HTTP '+str(response.status)+').')
  return raw if binary else json.loads(raw)if raw else {}
 finally:connection.close()

def validate_socket(path):
 if not re.fullmatch(r'/run/user/[1-9][0-9]*/docker\.sock',path):raise RuntimeError('Un socket Docker rootless dédié est requis.')
 if pathlib.Path(path).resolve()!=pathlib.Path(path):raise RuntimeError('Socket Docker symbolique refusé.')
 if not pathlib.Path(path).is_socket():raise RuntimeError('Le moteur Docker dédié ne répond pas.')

def builder_options():
 """Quotas du builder BuildKit : WEB_APPS_BUILD_CPUS (0.5 à 32, défaut 1) et WEB_APPS_BUILD_MEMORY (ex. 8g, défaut 3g)."""
 cpus=os.environ.get('WEB_APPS_BUILD_CPUS','1').strip();memory=os.environ.get('WEB_APPS_BUILD_MEMORY','3g').strip().lower()
 if not re.fullmatch(r'[0-9]{1,2}(\.[0-9])?',cpus) or not 0.5<=float(cpus)<=32:raise RuntimeError('WEB_APPS_BUILD_CPUS invalide (0.5 à 32).')
 if not re.fullmatch(r'[1-9][0-9]{0,4}[mg]',memory):raise RuntimeError('WEB_APPS_BUILD_MEMORY invalide (ex. 3g).')
 wanted={'memory':memory,'cpu-period':'100000','cpu-quota':str(int(float(cpus)*100000)),'cpu-shares':'128','memory-swap':memory,'network':BUILDER_NETWORK}
 image=os.environ.get('WEB_APPS_BUILDKIT_IMAGE','').strip()
 if image:
  if not re.fullmatch(r'moby/buildkit@sha256:[a-f0-9]{64}',image):raise RuntimeError('WEB_APPS_BUILDKIT_IMAGE invalide (digest officiel requis).')
  wanted['image']=image
 return wanted

def build_cache_limit():
 value=os.environ.get('WEB_APPS_BUILD_CACHE','2gb').strip().lower()
 return value if re.fullmatch(r'[1-9][0-9]{0,4}(mb|gb)',value) else '2gb'

def secure_builder_definition():
 # Buildx 0.30 injects network.host even with explicit buildkitd flags and an
 # empty entitlement configuration. Before bootstrap, replace only our single
 # dedicated node's flags under Buildx's own store lock (store/store.go). An
 # unknown schema fails closed instead of booting an unrestricted builder.
 config=pathlib.Path(os.environ.get('BUILDX_CONFIG')or pathlib.Path(os.environ.get('DOCKER_CONFIG')or pathlib.Path.home()/'.docker')/'buildx')
 with open(config/'.lock','a')as lock:
  fcntl.flock(lock,fcntl.LOCK_EX);path=config/'instances'/BUILDER;value=json.loads(path.read_text());nodes=value.get('Nodes')
  if value.get('Name')!=BUILDER or value.get('Driver')!='docker-container'or not isinstance(nodes,list)or len(nodes)!=1:raise RuntimeError('Définition du builder dédiée invalide.')
  node=nodes[0]
  if node.get('Name')!=BUILDER+'0'or node.get('Endpoint')!='unix://'+SOCKET or not isinstance(node.get('Flags'),list)or node.get('Files'):raise RuntimeError('Définition du builder non reconnue : démarrage refusé.')
  node['Flags']=BUILDER_FLAGS.copy();atomic(path,value);path.chmod(0o600)

def prerequisites():
 from web_app_runtime import allocation_mode
 allocation_mode()
 validate_socket(SOCKET)
 info=json.loads(docker(['info','--format','{{json .}}']))
 if not any('rootless' in x for x in info.get('SecurityOptions',[])):raise RuntimeError('Le moteur Docker doit être rootless.')
 if str(info.get('CgroupVersion'))!='2' or info.get('CgroupDriver')!='systemd':raise RuntimeError('Cgroup v2 et systemd sont requis pour les quotas.')
 wanted=builder_options()
 try:network=json.loads(docker(['network','inspect',BUILDER_NETWORK]))[0]
 except subprocess.CalledProcessError:
  docker(['network','create','--driver','bridge','--label','pockethost.web.builder=true',BUILDER_NETWORK])
  network=json.loads(docker(['network','inspect',BUILDER_NETWORK]))[0]
 if network.get('Driver')!='bridge' or network.get('Labels',{}).get('pockethost.web.builder')!='true':raise RuntimeError('Le réseau dédié du builder est invalide.')
 try:current=docker(['buildx','inspect',BUILDER])
 except subprocess.CalledProcessError:current=None
 # Ressources changées dans web-apps.env : builder recréé au démarrage du worker, cache BuildKit conservé (--keep-state).
 if current is not None and (dict(re.findall(r'([a-z-]+)="([^"]*)"',next((line for line in current.splitlines() if line.strip().startswith('Driver Options:')),'')))!=wanted or next((line.split(':',1)[1].strip()for line in current.splitlines()if line.strip().startswith('BuildKit daemon flags:')),'')!=' '.join(BUILDER_FLAGS)):
  docker(['buildx','rm','--keep-state',BUILDER],timeout=120);current=None
 if current is None:
  docker(['buildx','create','--name',BUILDER,'--driver','docker-container',*[part for key,value in wanted.items() for part in ('--driver-opt',key+'='+value)]],timeout=60)
  secure_builder_definition()
 global GIT_TOOLS;GIT_TOOLS=bool(shutil.which('git')and shutil.which('ssh'))
 # Railpack facultatif : son absence (module compris) ne bloque jamais le worker (construction automatique refusée en français).
 global RAILPACK_TOOLS
 try:
  from web_app_railpack import railpack_ready
  RAILPACK_TOOLS=railpack_ready(docker)
 except Exception:RAILPACK_TOOLS=False
 docker(['buildx','inspect',BUILDER,'--bootstrap'],timeout=180)
 ids=docker(['ps','-aq','--filter','name=buildx_buildkit_'+BUILDER]).split()
 if len(ids)!=1:raise RuntimeError('Builder isolé introuvable.')
 inspected=json.loads(docker(['inspect',ids[0]]))[0];config=inspected['HostConfig']
 memory=int(wanted['memory'][:-1])*1024**(3 if wanted['memory'].endswith('g') else 2)
 if config.get('Memory')!=memory or config.get('CpuQuota')!=int(wanted['cpu-quota']) or config.get('CpuPeriod')!=100000 or config.get('CpuShares')!=128 or config.get('MemorySwap')!=memory or config.get('NetworkMode')!=BUILDER_NETWORK:raise RuntimeError('Les quotas ou le réseau du builder ne sont pas appliqués.')
 if inspected.get('Config',{}).get('Cmd')!=BUILDER_FLAGS:raise RuntimeError('Les permissions du builder ne sont pas restreintes.')
 if wanted.get('image'):
  expected=json.loads(docker(['image','inspect',wanted['image']]))[0]
  if inspected.get('Image')!=expected.get('Id'):raise RuntimeError("L’image du builder ne correspond pas au digest configuré.")
 return True

def admit_resources(memory_mib=0,cpu_millicores=0,build=False,mode=None):
 from web_app_runtime import admit
 return admit(docker,memory_mib,cpu_millicores,build,mode)

def resources():
 admit_resources()
 if shutil.disk_usage(ROOT).free<15*1024**3:raise RuntimeError('Construction suspendue : moins de 15 Gio d’espace libre.')

class JobCancelled(RuntimeError):
 pass

class CommandFailed(RuntimeError):
 pass

try:from web_app_railpack import CommandTimeout
except ImportError:
 class CommandTimeout(RuntimeError):
  pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None

def public_configuration(name,value,auto=False):
 # Les réglages littéraux ne sont pas des secrets. Ne jamais déduire ce statut de
 # la seule longueur : un mot de passe court reste masqué, même égal à un réglage.
 if re.search(r'(?:^|_)(?:SECRET|PASSWORD|TOKEN|KEY|AUTH|CREDENTIALS?)(?:_|$)',name):return False
 if auto and name.startswith('RAILPACK_'):return True
 if name.startswith('NEXT_PUBLIC_'):return True
 if name in ('PGSSLMODE','DATABASE_SSL'):return value in ('disable','allow','prefer','require','verify-ca','verify-full','true','false')
 if name=='NODE_ENV'or name.endswith('_ENVIRONMENT'):return value in ('production','development','test','staging','preview')
 if name.endswith('_ENABLED'):return value in ('true','false','0','1')
 if name=='PORT'or name.endswith(('_CONCURRENCY','_TIMEOUT_MS')):return isinstance(value,str)and bool(re.fullmatch(r'[0-9]{1,7}',value))
 return False

class Job:
 def __init__(self,value):
  self.value=value;self.id=value['id'];self.app=value['appId'];self.snapshot=value['snapshot'];self.started=time.monotonic();self.process=None;self.logpath=ROOT/'logs'/(self.id+'.log');self.logpath.parent.mkdir(exist_ok=True,mode=0o750)
  auto=(self.snapshot.get('runtime')or{}).get('kind')=='auto'
  self.secrets=[v['value'] for k,v in self.snapshot.get('env',{}).items() if v.get('value') and not public_configuration(k,v['value'],auto)]
  self.secrets += [value.get('github',{}).get(k,'') for k in ['privateKey','webhookSecret']]
  key=value.get('git',{}).get('privateKey','') if isinstance(value.get('git'),dict) else ''
  if isinstance(key,str) and key:self.secrets += [key]+[line.strip() for line in key.splitlines() if len(line.strip())>=20 and not line.startswith('-----')]
  self.secrets += [self.snapshot.get('runtime',{}).get('registry',{}).get('password','')]
  self.secrets=list(dict.fromkeys([secret for secret in self.secrets if secret]+[line.strip()for secret in self.secrets if isinstance(secret,str) for line in secret.splitlines()if len(line.strip())>=12]))
  self.secrets.sort(key=len,reverse=True)
  self.pending_log='';self.previous=None;self.published=False;self.candidate='';self.stage_key='fetch'if value.get('action')=='deploy'else value.get('action','start');self.last_heartbeat=0
 def log(self,text,flush=False):
  self.pending_log+=text
  limit=max([len(x)for x in self.secrets if x]+[1])
  cut=len(self.pending_log) if flush else max(0,len(self.pending_log)-limit)
  for secret in self.secrets:
   if secret:
    start=self.pending_log.find(secret,max(0,cut-len(secret)+1))
    if start>=0 and start<cut<start+len(secret):cut=start
  output,self.pending_log=self.pending_log[:cut],self.pending_log[cut:]
  for secret in self.secrets:
   if secret:output=output.replace(secret,'[masqué]')
  with open(self.logpath,'a') as f:f.write(output)
  if self.logpath.stat().st_size>10*1024**2:
   text=self.logpath.read_bytes()[-5*1024**2:];self.logpath.write_bytes(text)

 def check_cancel(self,response):
  if self.value.get('action')in ['deploy','ci-deploy'] and (response.get('cancelRequested') or response.get('status')=='cancelled'):
   raise JobCancelled('Déploiement annulé. La version publiée est conservée.')
  return response
 def heartbeat(self,force=False):
  if not force and time.monotonic()-self.last_heartbeat<5:return {}
  response=api('report',{'id':self.id,'stageKey':self.stage_key,'stage':self.label(self.stage_key)})
  self.last_heartbeat=time.monotonic();self.check_cancel(response)
  atomic(ROOT/'worker-status.json',{'ready':True,'rootless':True,'checkedAt':stamp(),'deployment':self.id,'git':GIT_TOOLS,'railpack':RAILPACK_TOOLS})
  publish_runtime_stats();return response
 def stage(self,name):
  if name=='build':self.build_started=time.monotonic()
  self.stage_key=name;self.log('\n'+self.label(name)+'\n',True);return self.heartbeat(True)
 def label(self,key):
  return STAGE_BUILD.get((self.snapshot.get('runtime')or{}).get('kind'),STAGES.get(key,key))if key=='build'else STAGES.get(key,key)

 @contextlib.contextmanager
 def blocking_heartbeat(self):
  # Blocking certificates, stops and restores must keep the lease/UI fresh. Join before
  # the caller can report a terminal state so no late heartbeat reaches a ready job.
  stop=threading.Event();errors=[]
  def pulse():
   while not stop.wait(CERT_HEARTBEAT_SECONDS):
    try:
     api('report',{'id':self.id,'stageKey':self.stage_key,'stage':self.label(self.stage_key)})
     self.last_heartbeat=time.monotonic()
     atomic(ROOT/'worker-status.json',{'ready':True,'rootless':True,'checkedAt':stamp(),'deployment':self.id,'git':GIT_TOOLS,'railpack':RAILPACK_TOOLS})
    except Exception as error:errors.append(error);return
  thread=threading.Thread(target=pulse,name='web-blocking-heartbeat',daemon=True);thread.start()
  try:yield
  finally:
   stop.set();thread.join(timeout=35)
   if thread.is_alive():raise RuntimeError('Le contrôleur ne confirme plus le traitement en cours.')
  if errors:raise RuntimeError('La connexion au contrôleur a été interrompue pendant le traitement. Réessayez.')

 def run(self,args,cwd=None,env=None,budget=True,limit=None,ok=(0,),watch=None):
  # Output is drained from a pipe and sanitized before storage. No plaintext subprocess logs.
  # limit: per-command seconds (CommandTimeout); ok: accepted exit codes, the code is returned; watch: called every second, raises to abort.
  import selectors
  self.process=subprocess.Popen(args,cwd=cwd or ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
  selector=selectors.DefaultSelector();selector.register(self.process.stdout,selectors.EVENT_READ);heartbeat=time.monotonic();began=heartbeat
  try:
   while True:
    if time.monotonic()-self.started>1800:raise RuntimeError('Délai maximal de construction dépassé (30 minutes).')
    if self.stage_key=='build':
     from web_app_project_limits import build_seconds
     if time.monotonic()-getattr(self,'build_started',self.started)>build_seconds(ROOT,self):raise RuntimeError('Durée maximale de construction de ce projet dépassée. La version publiée est conservée.')
    if limit is not None and time.monotonic()-began>limit:raise CommandTimeout('Délai maximal de la commande dépassé.')
    if watch:watch()
    if budget and time.monotonic()-heartbeat>5:
     resources();check_runtime_budgets();self.heartbeat(True);heartbeat=time.monotonic()
    for key,_ in selector.select(timeout=1):
     chunk=os.read(key.fileobj.fileno(),8192)
     if chunk:self.log(chunk.decode(errors='replace'))
     else:selector.unregister(key.fileobj)
    if self.process.poll() is not None and not selector.get_map():break
   code=self.process.returncode
   if code not in ok:
    self.log('',True)
    # Diagnostic sur le journal déjà masqué, sans conserver de sortie brute.
    with self.logpath.open('rb')as output:
     output.seek(max(0,self.logpath.stat().st_size-65536));tail=output.read().decode(errors='replace')
    if 'next/font/google queries have exactly one entry'in tail:
     raise CommandFailed('Échec Next.js : cette version ne gère pas certaines URL de Google Fonts. Mettez Next.js à jour vers 16.4.3 ou utilisez des polices locales (next/font/local), puis redéployez.')
    raise CommandFailed('La commande de construction a échoué. Consultez les journaux.')
   return code
  finally:
   selector.close()
   if self.process.poll() is None:
    os.killpg(self.process.pid,signal.SIGTERM)
    try:self.process.wait(timeout=10)
    except subprocess.TimeoutExpired:os.killpg(self.process.pid,signal.SIGKILL);self.process.wait()
   if self.process.stdout:self.process.stdout.close()
   self.process=None;self.log('',True)

 def token(self):
  config=self.value.get('github',{})
  if not(config.get('privateKey') and config.get('githubAppId') and config.get('githubInstallationId')):return ''
  b64=lambda b:base64.urlsafe_b64encode(b).rstrip(b'=').decode();now=int(time.time())
  part=b64(b'{"alg":"RS256","typ":"JWT"}')+'.'+b64(json.dumps({'iat':now-60,'exp':now+540,'iss':config['githubAppId']}).encode())
  with tempfile.TemporaryDirectory(dir=ROOT) as folder:
   key=pathlib.Path(folder)/'key.pem';key.write_text(config['privateKey']);key.chmod(0o600)
   signature=subprocess.check_output(['openssl','dgst','-sha256','-sign',str(key)],input=part.encode(),stderr=subprocess.DEVNULL)
  req=urllib.request.Request('https://api.github.com/app/installations/'+str(config['githubInstallationId'])+'/access_tokens',headers={'Authorization':'Bearer '+part+'.'+b64(signature),'User-Agent':'PocketHost-Web','Accept':'application/vnd.github+json'},data=b'{}')
  with urllib.request.urlopen(req,timeout=20) as r:token=json.load(r)['token']
  self.secrets.append(token);return token

 def fetch(self,folder):
  repo=self.snapshot['config']['repository'];commit=self.value['commit']
  if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) or not re.fullmatch('[a-f0-9]{40}',commit):raise RuntimeError('Source GitHub invalide.')
  token=self.token();headers={'User-Agent':'PocketHost-Web','Accept':'application/vnd.github+json',**({'Authorization':'Bearer '+token}if token else{})}
  req=urllib.request.Request('https://api.github.com/repos/'+repo+'/tarball/'+commit,headers=headers)
  archive=folder/'source.tar.gz';total=0
  with urllib.request.urlopen(req,timeout=30) as response,open(archive,'wb') as out:
   while True:
    chunk=response.read(1024**2)
    if not chunk:break
    total+=len(chunk);self.heartbeat()
    if total>256*1024**2:raise RuntimeError('Archive source trop volumineuse (256 Mio maximum).')
    out.write(chunk)
  destination=folder/'source';destination.mkdir()
  with tarfile.open(archive,'r:gz') as tar:
   members=tar.getmembers()
   if sum(m.size for m in members)>2*1024**3 or len(members)>100000:raise RuntimeError('Archive source trop volumineuse.')
   for member in members:
    target=destination/member.name
    if not target.resolve().is_relative_to(destination.resolve()) or member.isdev() or member.isfifo():raise RuntimeError('Archive source invalide.')
    if member.issym() or member.islnk():
     link=(target.parent/member.linkname) if member.issym() else (destination/member.linkname)
     if not link.resolve().is_relative_to(destination.resolve()):raise RuntimeError('Lien source hors du projet.')
   if shutil.disk_usage(ROOT).free<15*1024**3+sum(member.size for member in members):raise RuntimeError('Extraction suspendue : la taille du dépôt consommerait la réserve de 15 Gio.')
   with self.blocking_heartbeat():tar.extractall(destination,filter='data')
  dirs=list(destination.iterdir())
  if len(dirs)!=1 or not dirs[0].is_dir():raise RuntimeError('Structure GitHub invalide.')
  self.heartbeat(True);return dirs[0]

 def fetch_source(self,folder):
  return self.fetch_git(folder) if self.snapshot['config'].get('source')=='git' else self.fetch(folder)

 def fetch_git(self,folder):
  # Deploy-key source: job.git comes from the claim (current web_git_sources row). Never touches GitHub.
  import web_app_git
  git=self.value.get('git')
  if not isinstance(git,dict):raise RuntimeError('Identifiants du dépôt Git absents. Relancez le déploiement.')
  if git.get('commit') and git.get('commit')!=self.value.get('commit'):raise RuntimeError('Commit Git incohérent avec le déploiement.')
  def run(args,cwd=None,env=None):
   try:self.run(args,cwd=cwd,env=env)
   except CommandFailed:raise web_app_git.GitCommandFailed() from None
  context=web_app_git.fetch_git(git,folder,log=self.log,run=run,commit=self.value.get('commit',''),root=ROOT)
  self.heartbeat(True);return context

 def wait_resources(self,build=False,timeout=120):
  from web_app_runtime import ResourcePressure
  deadline=time.monotonic()+timeout;announced=False
  while True:
   try:return admit_resources(build=build)
   except ResourcePressure:
    self.heartbeat(True)  # Cancellation and lease renewal continue while waiting.
    if time.monotonic()>=deadline:raise ResourcePressure('Capacité toujours insuffisante après 120 s d’attente. Réessayez plus tard ; les sites publiés sont conservés.')from None
    if not announced:self.log('Attente de ressources : priorité aux sites publiés, contrôle toutes les 5 secondes (120 s maximum).\n');announced=True
    time.sleep(5)

 def build(self,context,work):
  from web_app_runtime import RuntimeEngine,validate_profile
  profile=validate_profile(self.snapshot.get('runtime'))
  if profile['kind']!='nextjs':
   engine=RuntimeEngine(self,docker,docker_json,ROOT,SOCKET);engine.hmac_key=SECRET
   if profile['kind']=='compose':
    images={}
    for name in profile['compose']['order']:
     item=profile['compose']['services'][name]
     if not item.get('image'):self.wait_resources(build=True)
     images[name]=engine.build(context,work,{**item,'_service':name})
    return images
   if not profile.get('image'):self.wait_resources(build=True)
   return engine.build(context,work)
  self.wait_resources(build=True)
  cfg=self.snapshot['config'];relative=cfg.get('rootDirectory') or '.';app=context/relative
  if not app.resolve().is_relative_to(context.resolve()):raise RuntimeError('Dossier du projet invalide.')
  package=json.loads((app/'package.json').read_text())
  if 'next' not in {**package.get('dependencies',{}),**package.get('devDependencies',{})}:raise RuntimeError('Le dossier sélectionné ne contient pas de projet Next.js.')
  locks=[name for name in ['pnpm-lock.yaml','package-lock.json','yarn.lock'] if (context/name).is_file()]
  if not locks and app!=context:locks=[name for name in ['pnpm-lock.yaml','package-lock.json','yarn.lock'] if (app/name).is_file()]
  if len(locks)!=1:raise RuntimeError('Un seul lockfile npm, pnpm ou Yarn est requis.')
  manager={'pnpm-lock.yaml':'pnpm','package-lock.json':'npm','yarn.lock':'yarn'}[locks[0]]
  package_root='.' if (context/locks[0]).is_file() else relative
  version=package.get('packageManager','')
  if version and not re.fullmatch(r'(npm|pnpm|yarn)@[0-9][A-Za-z0-9.+-]{0,80}',version):raise RuntimeError('Version de gestionnaire invalide.')
  install={'pnpm':'pnpm install --frozen-lockfile','npm':'npm ci','yarn':'yarn install --immutable' if version.startswith('yarn@') and not version.startswith('yarn@1.') else 'yarn install --frozen-lockfile'}[manager]
  dockerfile=work/'Dockerfile'
  # Garder les liens relatifs de Turbopack/pnpm lors des copies /src -> /out -> /app.
  # fs.cpSync les réécrit sinon vers /src, absent de l’image finale.
  normalize="const fs=require('fs'),p=require('path');let files=[];function walk(d){for(const e of fs.readdirSync(d,{withFileTypes:true})){if(e.name==='node_modules')continue;const f=p.join(d,e.name);if(e.isDirectory())walk(f);else if(e.name==='server.js')files.push(f)}}if(fs.existsSync('.next/standalone'))walk('.next/standalone');const expected='.next/standalone/"+relative.strip('./')+"/server.js';let f=files.includes(expected)?expected:files.find(f=>f==='.next/standalone/server.js');if(f){const rel=p.relative('.next/standalone',f),dest=p.join('/out',p.dirname(rel));fs.cpSync('.next/standalone','/out',{recursive:true,verbatimSymlinks:true});fs.mkdirSync(p.join(dest,'.next'),{recursive:true});fs.cpSync('.next/static',p.join(dest,'.next/static'),{recursive:true,verbatimSymlinks:true});if(fs.existsSync('public'))fs.cpSync('public',p.join(dest,'public'),{recursive:true,verbatimSymlinks:true});fs.writeFileSync('/out/ph-server.json',JSON.stringify(rel));}else{if(!fs.existsSync('.next/BUILD_ID'))throw Error('Next.js build absent');fs.cpSync('/src','/out',{recursive:true,verbatimSymlinks:true,filter:(s)=>!s.endsWith('/.next/cache')&&!s.endsWith('/node_modules/.cache')});fs.writeFileSync('/out/ph-server.json',JSON.stringify({mode:'next-start',directory:"+json.dumps(relative)+"}));}"
  launcher="const fs=require('fs'),p=require('path');const cfg=JSON.parse(fs.readFileSync('/app/ph-server.json'));if(typeof cfg==='string'){const f=p.join('/app',cfg);process.chdir(p.dirname(f));require(f);}else{const root=p.resolve('/app',cfg.directory);process.chdir(root);const cli=require.resolve('next/dist/bin/next',{paths:[root]});process.argv=[process.execPath,cli,'start','-H','0.0.0.0','-p','3000'];require(cli);}"

  from web_app_build import dependency_plan,cache_namespace
  scope=cache_namespace(self.app,SECRET)+('-'+self.id if self.snapshot.get('fullRebuild') is True else '')
  manifest_copy, dependency_run, source_copy=dependency_plan(context,relative,manager,locks[0],package_root,scope)
  next_cache='--mount=type=cache,id='+scope+'-next,target=/src/'+str(pathlib.PurePosixPath(relative)/'.next/cache')+',sharing=locked '
  dockerfile.write_text('FROM node:24-bookworm-slim AS build\nWORKDIR /src\n'+manifest_copy+'WORKDIR /src/'+package_root+'\n'+dependency_run+'WORKDIR /src\n'+source_copy+'WORKDIR /src/'+relative+'\nARG PH_BUILD_NONCE\nRUN '+next_cache+'--mount=type=secret,id=env set -e; set -a; . /run/secrets/env; set +a; '+manager+' run build\nRUN node -e '+shlex.quote(normalize)+'\nFROM node:24-bookworm-slim\nWORKDIR /app\nENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000\nCOPY --from=build --chown=10001:10001 /out/ ./\nRUN printf %s '+shlex.quote(launcher)+' > /app/ph-start.cjs\nUSER 10001:10001\nCMD ["node","/app/ph-start.cjs"]\n')
  ignore=context/'.dockerignore';previous=ignore.read_text() if ignore.is_file() else '';ignore.write_text(previous+'\n.git\n**/node_modules\n**/.next\n**/.env\n**/.env.*\n')
  envfile=work/'build.env';envfile.write_text('\n'.join(name+'='+shlex.quote(v['value'])for name,v in self.snapshot.get('env',{}).items() if v.get('build'))+'\n');envfile.chmod(0o600)
  image='ph-web/'+self.app+':'+self.id
  from web_app_runtime import build_environment
  self.run(['docker','--host','unix://'+SOCKET,'buildx','build','--builder',BUILDER,'--output','type=docker,compression=uncompressed','--progress','plain',*(['--no-cache'] if self.snapshot.get('fullRebuild') is True else []),'--label','pockethost.web=true','--label','pockethost.web.app='+self.app,'--label','pockethost.web.runtime=nextjs','--label','pockethost.web.deployment='+self.id,'--build-arg','PH_BUILD_NONCE='+self.id,'--secret','id=env,src='+str(envfile),'-f',str(dockerfile),'-t',image,str(context)],env={**build_environment(self,work),'DOCKER_HOST':'unix://'+SOCKET})
  return image

 def next_metadata(self,image):
  info=json.loads(docker(['image','inspect',image]))[0];labels=(info.get('Config')or{}).get('Labels')or{}
  if labels.get('pockethost.web')!='true' or labels.get('pockethost.web.app')!=self.app or labels.get('pockethost.web.runtime')!='nextjs':raise RuntimeError('Cette image ne provient pas du runtime Next.js. Effectuez un déploiement complet.')
  image_id=info.get('Id','')
  if not re.fullmatch('sha256:[a-f0-9]{64}',image_id):raise RuntimeError('L’identifiant immuable de l’image publiée est invalide.')
  # Docker archive lit le fichier d'un conteneur ARRÊTÉ. Aucun binaire de l'image n'est exécuté.
  temporary=docker_json('POST','/containers/create?name=ph-web-'+self.app+'-'+self.id+'-metadata',{'Image':image_id,'User':'10001:10001','Cmd':['/unused'],'Labels':{'pockethost.web':'true','pockethost.web.app':self.app,'pockethost.web.deployment':self.id},'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges'],'Memory':64*1024**2,'PidsLimit':32}}).get('Id','')
  if not re.fullmatch('[a-f0-9]{64}',temporary):raise RuntimeError('Conteneur de lecture invalide.')
  try:
   raw=docker_json('GET','/containers/'+temporary+'/archive?path=/app/ph-server.json',binary=True)
   with tarfile.open(fileobj=io.BytesIO(raw),mode='r:')as archive:
    members=archive.getmembers()
    if len(members)!=1 or not members[0].isfile() or members[0].name!='ph-server.json' or not 0<members[0].size<=4096:raise RuntimeError('Métadonnées Next.js invalides.')
    server=json.load(archive.extractfile(members[0]))
   return image_id,server
  finally:docker_json('DELETE','/containers/'+temporary+'?force=true')

 def quiesce_storage(self):
  if hasattr(self,'recreate_previous')or not any(not item.get('readOnly')for item in self.snapshot.get('storage',[])):return
  self.recreate_previous=[]
  from web_app_storage import restartable_container
  for ident in docker(['ps','-q','--no-trunc','--filter','label=pockethost.web=true','--filter','label=pockethost.web.app='+self.app]).split():
   info=json.loads(docker(['inspect',ident]))[0]
   if info.get('Id')!=ident:raise RuntimeError('Identité du service du site invalide.')
   labels=info.get('Config',{}).get('Labels')or{}
   if labels.get('pockethost.web.app')!=self.app or labels.get('pockethost.web')!='true':raise RuntimeError('Service étranger avant la pause du stockage.')
   if labels.get('pockethost.web.private-db')=='true':continue
   restartable_container(info,self.app)
   self.recreate_previous.append(ident)
  current=load(ROOT/'current-job.json')
  atomic(ROOT/'current-job.json',{**current,'id':self.id,'appId':self.app,'action':self.value['action'],'recreatePrevious':self.recreate_previous,'storageRestore':self.snapshot.get('storageRestore')})
  for ident in self.recreate_previous:docker(['stop','-t','30',ident],timeout=40)

 def start(self,image):
  self.stage('start')
  from web_app_runtime import RuntimeEngine,validate_profile,loopback_port,resource_config,admission_memory,allocation_mode
  profile=validate_profile(self.snapshot.get('runtime'))
  if profile['kind']!='nextjs':
   from web_app_project_limits import admit as admit_project
   quotas=[item['resources']for item in profile['compose']['services'].values()]if profile['kind']=='compose'else[profile['resources']]
   admit_project(ROOT,self,docker,sum(q['memoryMiB']for q in quotas),sum(q['cpuMillicores']for q in quotas))
   self.quiesce_storage()
   if self.value.get('action')=='restore-backup':
    from web_app_backup_s3 import ensure_backup_image
    for ref in set(image.values())if isinstance(image,dict)else[image]:
     if isinstance(ref,str)and ref.startswith('sha256:'):
      with self.blocking_heartbeat():ensure_backup_image(ROOT,ref,self.app,docker,max_seconds=max(1,1800-(time.monotonic()-self.started)))
   self.runtime_engine=RuntimeEngine(self,docker,docker_json,ROOT,SOCKET)
   with self.blocking_heartbeat():return self.runtime_engine.start(image,self.snapshot.get('storage',[]))
  admit_resources(admission_memory(profile),profile['resources']['cpuMillicores'],mode=profile['allocation'])
  if self.value.get('action')=='restore-backup' or image.startswith('sha256:'):
   from web_app_backup_s3 import ensure_backup_image
   with self.blocking_heartbeat():ensure_backup_image(ROOT,image,self.app,docker,max_seconds=max(1,1800-(time.monotonic()-self.started)))
  name='ph-web-'+self.app+'-'+self.id
  verified_image,server=self.next_metadata(image)
  from web_app_project_limits import admit as admit_project
  admit_project(ROOT,self,docker,profile['resources']['memoryMiB'],profile['resources']['cpuMillicores'])
  self.quiesce_storage()
  # Root filesystem is immutable except Next's own cache and /tmp.
  serverdir=server.get('directory','.') if isinstance(server,dict) and server.get('mode')=='next-start' else str(pathlib.PurePosixPath(server).parent)
  if not re.fullmatch(r'[A-Za-z0-9_./-]+',serverdir) or pathlib.PurePosixPath(serverdir).is_absolute() or '..' in pathlib.PurePosixPath(serverdir).parts:raise RuntimeError('Chemin Next.js invalide.')
  cache='/app/'+str(pathlib.PurePosixPath(serverdir)/'.next/cache')
  nextdir='/app/'+str(pathlib.PurePosixPath(serverdir)/'.next')
  volume='ph-web-next-'+self.app+'-'+self.id
  docker(['volume','create','--label','pockethost.web=true','--label','pockethost.web.app='+self.app,'--label','pockethost.web.deployment='+self.id,volume])
  values={k:v['value']for k,v in self.snapshot.get('env',{}).items()if v.get('runtime')};values.update(NODE_ENV='production',HOSTNAME='0.0.0.0',PORT='3000',NODE_OPTIONS='--max-old-space-size='+str(max(32,min(4096,profile['resources']['memoryMiB']*2//3))))
  if any(not isinstance(v,str)or '\x00'in v for v in values.values()):raise RuntimeError('Valeur d’environnement invalide.')
  # Send an Env JSON array through the dedicated Unix socket. Newlines stay literal;
  # values never become host process environment, argv, temp files or image layers.
  host_port=loopback_port(ROOT)
  network='ph-web-net-'+self.app+'-'+self.id
  docker_json('POST','/networks/create',{'Name':network,'Driver':'bridge','Internal':False,'Labels':{'pockethost.web':'true','pockethost.web.app':self.app,'pockethost.web.deployment':self.id}})
  config={'Image':verified_image,'User':'10001:10001','Env':[k+'='+v for k,v in values.items()],
   'Labels':{'pockethost.web':'true','pockethost.web.app':self.app,'pockethost.web.deployment':self.id,'pockethost.web.private-db':'false','pockethost.web.private-db.wait':'false','pockethost.web.healthpath':self.snapshot['config'].get('healthPath','/')},
   'ExposedPorts':{'3000/tcp':{}},'HostConfig':{
    'NetworkMode':network,**resource_config(profile,profile['resources']),
    'SecurityOpt':['no-new-privileges'],'CapDrop':['ALL'],'ReadonlyRootfs':True,
    'Mounts':[{'Type':'volume','Source':volume,'Target':nextdir}],
    'Tmpfs':{'/tmp':'rw,noexec,nosuid,size=128m',cache:'rw,nosuid,uid=10001,gid=10001,size=128m'},
    'RestartPolicy':{'Name':'unless-stopped'},'LogConfig':{'Type':'json-file','Config':{'max-size':'10m','max-file':'3'}},
    'PortBindings':{'3000/tcp':[{'HostIp':'127.0.0.1','HostPort':host_port}]}}}
  config['Labels'].update({'pockethost.web.allocation':allocation_mode(profile['allocation']),'pockethost.web.priority':profile['priority']})
  from web_app_storage import prepare_mounts
  config['HostConfig']['Mounts']+=prepare_mounts(ROOT,self.app,self.snapshot.get('storage',[]),None,docker)
  from web_app_private_db import prepare_app,attach_app
  private_database=prepare_app(self,config,ROOT,docker)
  import urllib.parse
  created=docker_json('POST','/containers/create?name='+urllib.parse.quote(name,safe=''),config)
  container_id=created.get('Id','')
  if not re.fullmatch('[a-f0-9]{64}',container_id):raise RuntimeError('Réponse du moteur web invalide.')
  docker_json('POST','/containers/'+container_id+'/start')
  if private_database:
   with self.blocking_heartbeat():attach_app(container_id)
  info=json.loads(docker(['inspect',name]))[0];bindings=info['NetworkSettings']['Ports']['3000/tcp'];image_id=info.get('Image','')
  if not re.fullmatch('sha256:[a-f0-9]{64}',image_id):raise RuntimeError('L’identifiant immuable de l’image publiée est invalide.')
  if len(bindings)!=1 or bindings[0]['HostIp']!='127.0.0.1'or bindings[0]['HostPort']!=host_port:raise RuntimeError('Le port loopback réservé ne correspond pas à la publication.')
  baseline=int(docker(['exec',name,'du','-sk',nextdir]).split()[0])*1024
  atomic(ROOT/'runtime-budgets'/(self.id+'.json'),{'appId':self.app,'deploymentId':self.id,'container':name,'volume':volume,'path':nextdir,'baselineBytes':baseline})
  return {'image':image,'imageId':image_id,'container':name,'volume':volume,'target':'http://127.0.0.1:'+bindings[0]['HostPort'],'serverPath':server,'serverDirectory':serverdir}

 def health(self,result):
  if result.get('runtimeKind') and result.get('runtimeKind')!='nextjs':
   deadline=time.monotonic()+120;good=0;self.stage('health')
   while time.monotonic()<deadline:
    infos=[json.loads(docker(['inspect',item['container']]))[0]for item in result.get('services',[])]
    valid=bool(infos)and all(info.get('State',{}).get('Running')and info.get('State',{}).get('Health',{}).get('Status','healthy')=='healthy'for info in infos)
    self.heartbeat();good=good+1 if valid else 0
    if good>=3:break
    time.sleep(1)
   else:
    self.site_logs(result);raise RuntimeError('Un service du candidat ne passe pas le contrôle de santé. La publication précédente est conservée.')
   if not result.get('public',True):return
  self.stage('health');good=0;deadline=time.monotonic()+120;path=self.snapshot['config'].get('healthPath','/')
  while time.monotonic()<deadline:
   try:
    request=urllib.request.Request(result['target']+path,headers={'Host':self.snapshot['config']['generatedDomain'],'X-Forwarded-Proto':'https'})
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=5) as response:ok=200<=response.status<400
   except urllib.error.HTTPError as e:ok=300<=e.code<400
   except Exception:ok=False
   self.heartbeat()
   # Trois réponses consécutives, espacées d'une seconde, avant publication.
   good=good+1 if ok else 0
   if good>=3:return
   time.sleep(1)
  self.site_logs(result)
  raise RuntimeError('La nouvelle version ne passe pas le contrôle de santé. La version publiée est conservée.')

 def site_logs(self,result):
  """Avant suppression du candidat refusé : ses dernières lignes de journal vont dans le journal du déploiement (valeurs masquées)."""
  for name in dict.fromkeys([result.get('container'),*[item.get('container') for item in result.get('services',[])]]):
   if not isinstance(name,str) or not name.startswith('ph-web-'+self.app+'-'+self.id):continue
   try:output=subprocess.run(['docker','--host','unix://'+SOCKET,'logs','--tail','60',name],capture_output=True,text=True,errors='replace',cwd=ROOT,timeout=20)
   except Exception:continue
   lines=(output.stdout+output.stderr).strip()[-12000:]
   if lines:self.log('\nDernières lignes du journal du site ('+(name.removeprefix('ph-web-'+self.app+'-'+self.id).lstrip('-') or 'web')+') :\n'+lines+'\n')
  self.log('',True)

 def routes(self,target,active=True,certificate_host=None):
  manifest=load(ROOT/'routes.json',{'version':1,'routes':[]});others=[r for r in manifest.get('routes',[])if r['appId']!=self.app]
  current={}
  entries=[{'host':self.snapshot['config']['generatedDomain'],'certificate':False}]+self.snapshot.get('domains',[])
  for item in entries:
   host=item['host'];cert=bool(item.get('certificate') or host==certificate_host)
   current[host]={'appId':self.app,'host':host,'target':target,'active':bool(active and (host==self.snapshot['config']['generatedDomain'] or cert)),'certificate':cert}
  manifest={'version':1,'routes':others+list(current.values())};atomic(ROOT/'routes.json',manifest)

 def cache_assets(self,result):
  from web_app_assets import AssetReader,extract_assets,MAX_SECONDS
  parent=ROOT/'assets'/self.app;parent.mkdir(parents=True,exist_ok=True,mode=0o750);folder=parent/self.id
  path='/app/'+str(pathlib.PurePosixPath(result['serverDirectory'])/'.next/static')
  # Explicit working directory also makes sudo-launched diagnostics safe: a rootless
  # user cannot stat Docker's '-' destination from another user's private home.
  with tempfile.TemporaryFile() as errors,tempfile.TemporaryDirectory(prefix='.export-',dir=parent)as staging:
   proc=subprocess.Popen(['docker','--host','unix://'+SOCKET,'cp',result['container']+':'+path+'/.','-'],cwd=ROOT,stdout=subprocess.PIPE,stderr=errors,bufsize=0)
   try:
    reader=AssetReader(proc.stdout,ROOT,self.heartbeat);extract_assets(reader,pathlib.Path(staging))
    if proc.wait(timeout=max(.1,min(10,MAX_SECONDS-(time.monotonic()-reader.started)))):raise RuntimeError('Extraction des fichiers statiques impossible.')
    pathlib.Path(staging).chmod(0o750);os.rename(staging,folder)
   except (tarfile.TarError,OSError,subprocess.TimeoutExpired):
    errors.seek(0);detail=errors.read(4096).decode(errors='replace')
    if detail:self.log('Docker : '+detail+'\n',True)
    raise RuntimeError('L’export des fichiers statiques a échoué. Vérifiez le conteneur et les journaux Docker.') from None
   finally:
    if proc.poll() is None:proc.kill();proc.wait()
    if proc.stdout:proc.stdout.close()

 def cleanup(self,current_image):
  rows=[]
  ids=docker(['image','ls','-q','--filter','label=pockethost.web.app='+self.app]).split()
  for ident in set(ids):
   info=json.loads(docker(['image','inspect',ident]))[0]
   if info.get('Config',{}).get('Labels',{}).get('pockethost.web.app')==self.app:rows.append(info)
  rows.sort(key=lambda r:r['Created'],reverse=True)
  from web_app_backups import protected_images
  protected=protected_images(ROOT,self.app,api)
  recent_deployments=[]
  for row in rows:
   dep=(row.get('Config',{}).get('Labels')or{}).get('pockethost.web.deployment')
   if dep and dep not in recent_deployments:recent_deployments.append(dep)
  keep={r['Id']for r in rows if(r.get('Config',{}).get('Labels')or{}).get('pockethost.web.deployment')in recent_deployments[:3]}or{r['Id']for r in rows[:3]}
  keep.update(r['Id'] for r in rows if r['Id'] in protected or any(tag in protected for tag in r.get('RepoTags',[])))
  for r in rows:
   if r['Id']==current_image or current_image in r.get('RepoTags',[]):keep.add(r['Id'])
  containers=[json.loads(docker(['inspect',ident]))[0] for ident in docker(['ps','-aq','--filter','label=pockethost.web=true','--filter','label=pockethost.web.app='+self.app]).split()]
  containers.sort(key=lambda r:r['Created'],reverse=True);deployments=[]
  for info in containers:
   dep=(info.get('Config',{}).get('Labels')or{}).get('pockethost.web.deployment')
   if dep and dep not in deployments:deployments.append(dep)
  recent={info['Id']for info in containers if(info.get('Config',{}).get('Labels')or{}).get('pockethost.web.deployment')in deployments[:3]}if deployments else{c['Id']for c in containers[:3]}
  for info in containers:
   if info['Id'] not in recent and not info['State']['Running']:
    docker(['rm',info['Id']])
    for mount in info.get('Mounts',[]):
     name=mount.get('Name','')
     if name.startswith('ph-web-next-'+self.app+'-'):
      try:docker(['volume','rm',name])
      except Exception:pass
    dep=info.get('Config',{}).get('Labels',{}).get('pockethost.web.deployment','')
    if re.fullmatch('[a-z0-9]{15}',dep):(ROOT/'runtime-budgets'/(dep+'.json')).unlink(missing_ok=True)
  keep.update(info.get('Image')for info in containers if info.get('Image')and(info.get('State',{}).get('Running')or info['Id']in recent))
  for r in rows:
   if r['Id'] not in keep:
    try:docker(['image','rm',r['Id']])
    except subprocess.CalledProcessError:pass
  assets=ROOT/'assets'/self.app
  if assets.exists():
   for folder in sorted(assets.iterdir(),key=lambda p:p.stat().st_mtime,reverse=True)[3:]:
    if folder.is_dir() and not folder.is_symlink():shutil.rmtree(folder)
  from web_app_storage import prune_unused_volumes
  prune_unused_volumes(ROOT,self.app,docker)
  for ident in docker(['network','ls','-q','--filter','label=pockethost.web.app='+self.app]).split():
   info=json.loads(docker(['network','inspect',ident]))[0]
   if info.get('Labels',{}).get('pockethost.web.app')!=self.app or not info.get('Name','').startswith('ph-web-net-'+self.app+'-'):raise RuntimeError('Réseau étranger pendant le nettoyage.')
   if not info.get('Containers'):
    try:docker(['network','rm',ident])
    except subprocess.CalledProcessError:pass
  try:docker(['buildx','prune','--builder',BUILDER,'--force','--keep-storage',build_cache_limit()],timeout=60)
  except Exception:pass
  return [reference for r in rows if r['Id'] in keep for reference in [r['Id'],*r.get('RepoTags',[])]]

 def stop_old(self,except_name='',strict=False):
  from web_app_private_db import private_database_operations
  with private_database_operations():return self._stop_old(except_name,strict)

 def _stop_old(self,except_name='',strict=False):
  for ident in docker(['ps','-aq','--filter','label=pockethost.web=true','--filter','label=pockethost.web.app='+self.app]).split():
   info=json.loads(docker(['inspect',ident]))[0]
   labels=info.get('Config',{}).get('Labels')or{}
   if labels.get('pockethost.web')!='true'or labels.get('pockethost.web.app')!=self.app or not re.fullmatch('ph-web-'+self.app+'-[a-z0-9]{15}(?:-[a-z][a-z0-9-]{0,30})?',info.get('Name','').lstrip('/')):raise RuntimeError('Arrêt d’un conteneur étranger refusé.')
   if info['Name'].lstrip('/')==except_name or (except_name and info.get('Config',{}).get('Labels',{}).get('pockethost.web.deployment')==self.id):continue
   if not info.get('State',{}).get('Running'):continue
   try:docker(['stop','-t','60',ident],timeout=70)
   except subprocess.CalledProcessError:pass
   except (subprocess.TimeoutExpired,OSError):
    if not strict:raise
  if strict:
   # A stop may consume its whole grace period for each service. Acknowledge only
   # after Docker confirms that the application has no remaining running service.
   if docker(['ps','-q','--filter','label=pockethost.web=true','--filter','label=pockethost.web.app='+self.app]).split():raise RuntimeError('L’arrêt est incomplet : un service de cette application reste actif.')

 def checkpoint(self,candidate=''):
  self.previous=load(ROOT/'routes.json',{'version':1,'routes':[]});self.candidate=candidate;self.published=True
  atomic(ROOT/'current-job.json',{'id':self.id,'previous':self.previous,'candidate':candidate,'action':self.value['action'],'appId':self.app,'recreatePrevious':getattr(self,'recreate_previous',[]),'storageRestore':self.snapshot.get('storageRestore')})

 def build_fingerprint(self,profile):
  """HMAC de tout ce qui façonne l'image : commit, configuration, runtime, variables de build et code de construction."""
  if self.value.get('action')!='deploy' or not SECRET or profile['kind']=='compose' or (profile['kind'] in('image','worker') and profile.get('image')):return ''
  build={name:item.get('value','') for name,item in (self.snapshot.get('env') or {}).items() if isinstance(item,dict) and item.get('build')}
  payload=json.dumps({'commit':self.value.get('commit'),'config':self.snapshot.get('config'),'runtime':profile,'env':build,'recipe':RECIPE},sort_keys=True,default=str)
  return hmac.new(SECRET.encode(),payload.encode(),hashlib.sha256).hexdigest()

 def reusable_image(self,fingerprint):
  if not fingerprint:return ''
  entry=next((item for item in load(ROOT/'image-reuse'/(self.app+'.json'),{'entries':[]}).get('entries',[]) if isinstance(item,dict) and hmac.compare_digest(str(item.get('fingerprint','')),fingerprint)),None)
  if not entry or not re.fullmatch('sha256:[a-f0-9]{64}',str(entry.get('image',''))):return ''
  try:info=json.loads(docker(['image','inspect',entry['image']]))[0]
  except Exception:return ''
  labels=(info.get('Config') or {}).get('Labels') or {}
  return info['Id'] if info.get('Id')==entry['image'] and labels.get('pockethost.web')=='true' and labels.get('pockethost.web.app')==self.app else ''

 def remember_image(self,fingerprint,result):
  """Seule une version publiée (santé vérifiée) devient réutilisable ; 5 empreintes par site au plus."""
  image=result.get('imageId') or result.get('image') or ''
  if not fingerprint or not isinstance(image,str) or not re.fullmatch('sha256:[a-f0-9]{64}',image):return
  path=ROOT/'image-reuse'/(self.app+'.json')
  entries=[item for item in load(path,{'entries':[]}).get('entries',[]) if isinstance(item,dict) and item.get('fingerprint')!=fingerprint][:4]
  atomic(path,{'entries':[{'fingerprint':fingerprint,'image':image,'deployment':self.id},*entries]})

 def cleanup_failure(self):
  for ident in docker(['ps','-aq','--filter','label=pockethost.web.app='+self.app,'--filter','label=pockethost.web.deployment='+self.id]).split():
   info=json.loads(docker(['inspect',ident]))[0];labels=info.get('Config',{}).get('Labels')or{}
   if labels.get('pockethost.web.app')!=self.app or labels.get('pockethost.web.deployment')!=self.id or not info.get('Name','').lstrip('/').startswith('ph-web-'+self.app+'-'+self.id):raise RuntimeError('Nettoyage d’un candidat étranger refusé.')
   docker(['rm','-f',ident])
  if hasattr(self,'runtime_engine'):
   for ident in reversed(self.runtime_engine.created):
    try:docker_json('DELETE','/containers/'+ident+'?force=true')
    except Exception:pass
   try:docker_json('DELETE','/networks/'+self.runtime_engine.network)
   except Exception:pass
  if self.snapshot.get('storageRestore'):
   from web_app_storage import rollback_restore
   rollback_restore(ROOT,self.app,self.snapshot['storageRestore'],docker)
  if getattr(self,'recreate_previous',[]):
   from web_app_storage import _resume
   _resume(docker,self.app,self.recreate_previous)
  name='ph-web-'+self.app+'-'+self.id
  try:docker(['rm','-f',name])
  except Exception:pass
  try:docker(['volume','rm','ph-web-next-'+self.app+'-'+self.id])
  except Exception:pass
  try:docker(['network','rm','ph-web-net-'+self.app+'-'+self.id])
  except Exception:pass
  (ROOT/'runtime-budgets'/(self.id+'.json')).unlink(missing_ok=True)
  if self.value['action']=='deploy':
   try:docker(['image','rm','ph-web/'+self.app+':'+self.id])
   except Exception:pass
  folder=ROOT/'assets'/self.app/self.id
  if folder.is_dir() and not folder.is_symlink():shutil.rmtree(folder)

 def execute(self):
  action=self.value['action'];result={};candidate=''
  from web_app_runtime import validate_profile,RuntimeEngine
  profile=validate_profile(self.snapshot.get('runtime'))
  if action=='preview-remove':
   proof=self.snapshot.get('preview')or{}
   if proof.get('appId')!=self.app or not proof.get('isPreview') or not re.fullmatch('[a-z0-9]{15}',proof.get('sourceAppId',''))or proof['sourceAppId']==self.app:raise RuntimeError('Suppression preview refusée : preuve de périmètre invalide.')
   atomic(ROOT/'preview-deletions'/(self.app+'.json'),{'appId':self.app,'jobId':self.id})
   self.routes('',False)
   ids=docker(['ps','-aq','--filter','label=pockethost.web=true','--filter','label=pockethost.web.app='+self.app]).split()
   for ident in ids:
    info=json.loads(docker(['inspect',ident]))[0]
    if info.get('Config',{}).get('Labels',{}).get('pockethost.web.app')!=self.app or not info.get('Name','').lstrip('/').startswith('ph-web-'+self.app+'-'):raise RuntimeError('Ressource preview étrangère refusée.')
    docker(['rm','-f',ident])
   for ident in set(docker(['image','ls','-q','--filter','label=pockethost.web.app='+self.app]).split()):
    info=json.loads(docker(['image','inspect',ident]))[0]
    if info.get('Config',{}).get('Labels',{}).get('pockethost.web.app')!=self.app:raise RuntimeError('Image preview étrangère refusée.')
    docker(['image','rm',info['Id']])
   for ident in docker(['network','ls','-q','--filter','label=pockethost.web.app='+self.app]).split():
    info=json.loads(docker(['network','inspect',ident]))[0]
    if info.get('Labels',{}).get('pockethost.web.app')!=self.app or not info.get('Name','').startswith('ph-web-net-'+self.app+'-'):raise RuntimeError('Réseau preview étranger refusé.')
    docker(['network','rm',ident])
   from web_app_storage import prune_unused_volumes
   prune_unused_volumes(ROOT,self.app,docker,delete_all=True)
   for volume in docker(['volume','ls','-q','--filter','label=pockethost.web.app='+self.app]).split():
    if volume.startswith('ph-web-next-'+self.app+'-'):docker(['volume','rm',volume])
   storage_file=ROOT/'storage'/(self.app+'.json')
   if storage_file.resolve()==storage_file:storage_file.unlink(missing_ok=True)
   for namespace in ['assets','runtime-budgets']:
    folder=ROOT/namespace/self.app
    if folder.is_dir()and not folder.is_symlink():shutil.rmtree(folder)
   return {'removed':True}
  if action=='ci-deploy':
   expires=self.snapshot.get('ci',{}).get('expiresAt','')
   if not expires or datetime.datetime.fromisoformat(expires.replace('Z','+00:00')).timestamp()<time.time():raise RuntimeError('Les identifiants temporaires CI ont expiré. Relancez le pipeline.')
  if action in ['deploy','ci-deploy']:self.heartbeat(True)
  if action=='stop':
   self.checkpoint();self.routes('',False)
   with self.blocking_heartbeat():self.stop_old(strict=True)
   return {}
  if action=='domains-sync':
   routes=load(ROOT/'routes.json',{'routes':[]})['routes'];published=next((r for r in routes if r['appId']==self.app and r.get('active')),None)
   self.checkpoint();self.routes(published['target'] if published else '',bool(published))
   return {}
  if action=='domain':
   from web_app_domains import provision_web_domain_certificate
   routes=load(ROOT/'routes.json',{'routes':[]})['routes'];published=next((r for r in routes if r['appId']==self.app and r.get('active')),None)
   if not published:raise RuntimeError('Publiez le site avant de configurer son domaine.')
   self.checkpoint();self.routes(published['target'])
   with self.blocking_heartbeat():result=provision_web_domain_certificate(ROOT,self.snapshot['domain'],os.environ.get('WEB_APPS_PUBLIC_IP',''),os.environ.get('WEB_APPS_CERT_EMAIL',''))
   self.routes(published['target'],certificate_host=self.snapshot['domain']);return result
  published=False;previous=None;fingerprint=''
  try:
   if action=='promote':
    source=self.snapshot.get('rollbackResult',{})
    if not re.fullmatch('[a-z0-9]{15}',source.get('sourceAppId',''))or source['sourceAppId']==self.app or not re.fullmatch('sha256:[a-f0-9]{64}',source.get('imageSource','')):raise RuntimeError('Image de promotion invalide.')
    engine=RuntimeEngine(self,docker,docker_json,ROOT,SOCKET);owned=engine.own_image(source['imageSource'],source_app=source['sourceAppId']);result=self.start(owned)
   elif action in ['rollback','start','apply-runtime','restore-backup']:
    previous=self.snapshot.get('rollbackResult',{});result=self.start(previous.get('images')if profile['kind']=='compose'else previous.get('image',''))
   else:
    self.wait_resources();resources();fingerprint=self.build_fingerprint(profile)
    if self.snapshot.get('fullRebuild') is True:reused='';self.log('Reconstruction complète demandée : aucune image réutilisée, cache de construction ignoré.\n')
    else:reused=self.reusable_image(fingerprint)
    if reused:
     self.stage('build');self.log('Image réutilisée : même commit, même configuration et mêmes variables de build. Aucune reconstruction.\n')
     result=self.start(reused)
    else:
     self.stage('fetch')
     with tempfile.TemporaryDirectory(prefix='build-',dir=ROOT) as work:
      work=pathlib.Path(work);needs_source=profile['kind']not in ['image','worker']or not profile.get('image');needs_source=any(not item.get('image')for item in profile['compose']['services'].values())if profile['kind']=='compose'else needs_source;context=self.fetch_source(work)if needs_source else None;self.stage('build');result=self.start(self.build(context,work))
   candidate=result['container'];self.health(result);response=self.stage('publish')
   if action in ['deploy','ci-deploy'] and response.get('desiredCommit')!=self.value['commit']:raise RuntimeError('Un commit plus récent est en attente. Cette version ne sera pas publiée.')
   if not result.get('runtimeKind')or result['runtimeKind']=='nextjs'or result.get('framework')=='nextjs':self.cache_assets(result)
   self.checkpoint(candidate);previous=self.previous;self.routes(result['target'],result.get('public',True));published=True;response=api('report',{'id':self.id,'stage':'Publication vérifiée'})
   if action in ['deploy','ci-deploy'] and response.get('desiredCommit')!=self.value['commit']:
    atomic(ROOT/'routes.json',previous);raise RuntimeError('Un commit plus récent a remplacé cette publication.')
   if action=='deploy':self.remember_image(fingerprint,result)
   # Existing requests drain before the former container stops. No automatic retry of POSTs.
   return result
  except Exception:
   if published and previous is not None:atomic(ROOT/'routes.json',previous);self.published=False
   self.cleanup_failure()
   raise

_last_runtime_sample=0
def metric_bytes(value):
 match=re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*([kKMGT]?i?B)',value.strip())
 if not match:return None
 unit=match[2];power={'B':0,'kB':1,'KB':1,'KiB':1,'MB':2,'MiB':2,'GB':3,'GiB':3,'TB':4,'TiB':4}.get(unit)
 if power is None:return None
 return round(float(match[1])*(1024 if 'i' in unit else 1000)**power)
def publish_runtime_stats(force=False):
 global _last_runtime_sample
 if not force and time.monotonic()-_last_runtime_sample<30:return
 _last_runtime_sample=time.monotonic();checked=stamp();applications={};routes=load(ROOT/'routes.json',{'routes':[]}).get('routes',[]);publications=load(ROOT/'runtime-publications.json')
 active={r['appId']:r.get('target')for r in routes if re.fullmatch('[a-z0-9]{15}',r.get('appId',''))and r.get('active')}
 known={r['appId']for r in routes if re.fullmatch('[a-z0-9]{15}',r.get('appId',''))}|{app for app in publications if re.fullmatch('[a-z0-9]{15}',app)}
 for app in known:applications[app]={'available':app not in active and publications.get(app,{}).get('state')!='running','checkedAt':checked,'state':'stopped'if app not in active and publications.get(app,{}).get('state')!='running'else'unknown'}
 try:
  ids=docker(['ps','-q','--filter','label=pockethost.web=true']).split()[:100]
  infos=json.loads(docker(['inspect',*ids]))if ids else[]
  stats=[json.loads(line)for line in docker(['stats','--no-stream','--format','{{json .}}',*ids],timeout=15).splitlines()]if ids else[]
  by_name={r.get('Name'):r for r in stats};counts={}
  for info in infos:
   name=info.get('Name','').lstrip('/');labels=info.get('Config',{}).get('Labels')or{};app=labels.get('pockethost.web.app','');deployment=labels.get('pockethost.web.deployment','')
   legacy=re.fullmatch('ph-web-'+app+'-([a-z0-9]{15})(?:-[a-z][a-z0-9-]{0,30})?',name)
   if not deployment and legacy:deployment=legacy[1]
   if not re.fullmatch('[a-z0-9]{15}',app)or not re.fullmatch('[a-z0-9]{15}',deployment)or not name.startswith('ph-web-'+app+'-'+deployment):continue
   publication=publications.get(app,{})
   ports=[binding for values in info.get('NetworkSettings',{}).get('Ports',{}).values()for binding in(values or[])]
   targets={'http://127.0.0.1:'+p['HostPort']for p in ports if p.get('HostIp')=='127.0.0.1'}
   if publication.get('deploymentId')!=deployment and active.get(app)not in targets:continue
   if publication.get('state')=='stopped':continue
   row=by_name.get(name,{});cpu=row.get('CPUPerc','').rstrip('%');memory=metric_bytes(row.get('MemUsage','').split('/')[0]);pids=row.get('PIDs','')
   if not re.fullmatch('[0-9]+(?:\.[0-9]+)?',cpu)or memory is None:continue
   if not counts.get(app):applications[app]={'available':True,'checkedAt':checked,'state':'running','scope':'application-container','cpuPercent':0,'memoryBytes':0,'memoryLimitBytes':0,'pids':0,'services':0}
   current=applications[app];current['cpuPercent']+=float(cpu);current['memoryBytes']+=memory;current['memoryLimitBytes']+=int(info.get('HostConfig',{}).get('Memory',0));current['pids']+=int(pids)if pids.isdigit()else 0;counts[app]=counts.get(app,0)+1;current['services']=counts[app]
   if counts[app]>1:current['scope']='application-services'
 except Exception:
  for app in known:applications[app]={'available':False,'checkedAt':checked,'state':'unknown','lastError':'Mesures du conteneur indisponibles.'}
 atomic(ROOT/'runtime-status.json',{'checkedAt':checked,'applications':applications})

def check_runtime_budgets():
 folder=ROOT/'runtime-budgets';alerts=load(ROOT/'runtime-alerts.json')
 if not folder.exists():return '; '.join(alerts.values())
 for path in list(folder.glob('*.json'))[:100]:
  item=load(path)
  if not re.fullmatch('[a-z0-9]{15}',item.get('appId','')) or not re.fullmatch('[a-z0-9]{15}',item.get('deploymentId','')):continue
  expected='ph-web-'+item['appId']+'-'+item['deploymentId']+('-'+item['service']if re.fullmatch('[a-z][a-z0-9-]{0,30}',item.get('service',''))else'')
  if item.get('container')!=expected:continue
  try:
   info=json.loads(docker(['inspect',expected]))[0]
   if not info['State']['Running']:continue
   if re.fullmatch('sha256:[a-f0-9]{64}',os.environ.get('WEB_APPS_STORAGE_HELPER_IMAGE',''))and item.get('volume'):
    from web_app_storage import _measure
    size=_measure(docker,item['appId'],item['volume'])
   else:size=int(docker(['exec',expected,'du','-sk',item['path']]).split()[0])*1024
   if size>item['baselineBytes']+256*1024**2 or shutil.disk_usage(ROOT).free<15*1024**3:
    docker(['stop','-t','15',expected],timeout=25)
    alerts[item['appId']]='Application '+item['appId']+' arrêtée : limite du cache Next.js ou espace disque insuffisant.';atomic(ROOT/'runtime-alerts.json',alerts)
    api('report',{'id':item['deploymentId'],'runtimeStopped':alerts[item['appId']]})
  except Exception:continue
 return '; '.join(alerts.values())

def reconcile():
 orphan=load(ROOT/'current-job.json')
 if not orphan.get('id'):return
 state=api('report',{'id':orphan['id'],'inspect':True})
 if state.get('status')!='ready':
  if orphan.get('previous'):atomic(ROOT/'routes.json',orphan['previous'])
  if orphan.get('candidate'):
   try:docker(['rm','-f',orphan['candidate']])
   except Exception:pass
  if orphan.get('appId') and orphan.get('action'):
   dummy=Job({'id':orphan['id'],'appId':orphan['appId'],'action':orphan['action'],'snapshot':{'config':{},'env':{},'storageRestore':orphan.get('storageRestore')}});dummy.recreate_previous=orphan.get('recreatePrevious',[]);dummy.cleanup_failure()
  if state.get('status')=='running':api('report',{'id':orphan['id'],'status':'cancelled'if state.get('cancelRequested')else'failed','error':''if state.get('cancelRequested')else'Worker interrompu pendant le déploiement. La version précédente reste publiée.','stage':'Annulé'if state.get('cancelRequested')else'Interrompu'})
 (ROOT/'current-job.json').unlink(missing_ok=True)

def main():
 allocation_label=os.environ.get('WEB_APPS_ALLOCATION_MODE','adaptive')
 if allocation_label not in ('adaptive','fixed'):allocation_label='unknown'
 if not re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{3,4}',CONTROL)or not 1024<=int(CONTROL.rsplit(':',1)[1])<=65535 or len(SECRET)<32:raise RuntimeError('Configuration locale du worker invalide.')
 if ROOT.resolve()!=ROOT:raise RuntimeError('Répertoire du worker symbolique refusé.')
 ROOT.mkdir(parents=True,exist_ok=True,mode=0o750)
 lock=open(ROOT/'worker.lock','a+')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise RuntimeError('Un worker est déjà actif.')
 ready=False;lastcheck=0;background={}
 def backup_loop():
  from web_app_backups import tick
  while True:
   try:tick(ROOT,api,docker)
   except Exception:pass
   time.sleep(5)
 while True:
  try:
   if time.monotonic()-lastcheck>30:
    prerequisites();ready=True;lastcheck=time.monotonic()
    if 'health'not in background:
     from web_app_health import start_health_monitor
     background['health']=start_health_monitor(CONTROL,SECRET,docker)
    if 'project-limits'not in background:
     from web_app_project_limits import start_monitor
     background['project-limits']=start_monitor(ROOT,api,atomic)
    if 'tasks'not in background:
     from web_app_tasks import start_task_runner
     background['tasks']=start_task_runner(CONTROL,SECRET,SOCKET,ROOT)
    if 'runtime-logs'not in background:
     from web_app_observability import start_runtime_log_collector
     background['runtime-logs']=start_runtime_log_collector(CONTROL,SECRET,SOCKET,ROOT)
    if 'notifications'not in background:
     from web_app_notifications import start_notification_dispatcher
     background['notifications']=start_notification_dispatcher(CONTROL,SECRET,ROOT)
    if 'private-databases'not in background:
     from web_app_private_db import start_private_database_runner
     background['private-databases']=start_private_database_runner(CONTROL,SECRET,SOCKET,ROOT,docker)
    if 'storage'not in background:
     from web_app_storage import start_storage_monitor
     background['storage']=start_storage_monitor(ROOT,docker,api)
    if 'backups'not in background:
     background['backups']=threading.Thread(target=backup_loop,name='web-backups',daemon=True);background['backups'].start()
   reconcile();warning=check_runtime_budgets();publish_runtime_stats()
   atomic(ROOT/'worker-status.json',{'ready':ready,'rootless':ready,'allocationMode':allocation_label,'checkedAt':stamp(),'error':warning,'git':GIT_TOOLS,'railpack':RAILPACK_TOOLS,'httpsReady':bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',os.environ.get('WEB_APPS_CERT_EMAIL',''))and shutil.which('certbot'))})
   try:
    from web_app_storage import tick as storage_tick
    if storage_tick(ROOT,api,docker):time.sleep(1);continue
   except Exception:time.sleep(5);continue
   from web_app_deletion import tick as deletion_tick
   if deletion_tick(ROOT,api,docker,atomic,load):continue
   value=api('claim',{}).get('job')
   if value:
    atomic(ROOT/'current-job.json',{'id':value['id'],'appId':value['appId'],'action':value['action']});job=Job(value)
    acknowledged=False
    try:
     job.heartbeat(True);result=job.execute()
     try:api('report',{'id':job.id,'status':'ready','stage':'Terminé','result':result});acknowledged=True
     except Exception:
      for attempt in range(6):
       time.sleep(5)
       try:
        state=api('report',{'id':job.id,'inspect':True})
        if state.get('status')=='ready':acknowledged=True;break
        api('report',{'id':job.id,'status':'ready','stage':'Terminé','result':result});acknowledged=True;break
       except Exception:continue
      if not acknowledged:raise RuntimeError('Le contrôleur ne confirme pas la publication. Réconciliation requise.')
     publications=load(ROOT/'runtime-publications.json')
     if job.value['action']=='preview-remove':publications.pop(job.app,None)
     elif result.get('container'):publications[job.app]={**result,'deploymentId':job.id,'state':'running'}
     elif job.value['action']=='stop'and job.app in publications:publications[job.app]['state']='stopped'
     atomic(ROOT/'runtime-publications.json',publications)
     alerts=load(ROOT/'runtime-alerts.json');alerts.pop(job.app,None);atomic(ROOT/'runtime-alerts.json',alerts)
     if job.value['action']!='stop'and job.published and result.get('container'):
      try:job.stop_old(result['container']);retained=job.cleanup(result['image']);api('report',{'id':job.id,'retainedImages':retained})
      except Exception:job.log('Nettoyage différé ; la version publiée reste active.\n',True)
    except Exception as e:
     message=str(e) if isinstance(e,RuntimeError) else 'Le déploiement a échoué. Vérifiez '+('le dépôt Git'if job.snapshot.get('config',{}).get('source')=='git'else'GitHub')+', les ressources et les journaux.'
     job.log('\n'+message+'\n',True)
     if not acknowledged:
      # Unknown controller state keeps both healthy containers and the recovery marker.
      try:
       state=api('report',{'id':job.id,'inspect':True})
       if state.get('status')!='ready':
        if job.published and job.previous is not None:atomic(ROOT/'routes.json',job.previous)
        if job.candidate:
         try:docker(['rm','-f',job.candidate])
         except Exception:pass
        job.cleanup_failure()
        api('report',{'id':job.id,'status':'cancelled'if isinstance(e,JobCancelled)else'failed','error':''if isinstance(e,JobCancelled)else message,'stage':'Annulé'if isinstance(e,JobCancelled)else'Échec'})
       acknowledged=True
      except Exception:raise RuntimeError('Réconciliation requise avant le prochain déploiement.')
    finally:
     if acknowledged:(ROOT/'current-job.json').unlink(missing_ok=True)
    if acknowledged:continue
  except Exception as e:
   message=str(e) if isinstance(e,RuntimeError) else 'Worker indisponible : vérifiez le moteur dédié et la connexion locale.'
   atomic(ROOT/'worker-status.json',{'ready':False,'rootless':ready,'allocationMode':allocation_label,'checkedAt':stamp(),'error':message,'railpack':RAILPACK_TOOLS})
  time.sleep(5)

if __name__=='__main__':main()
