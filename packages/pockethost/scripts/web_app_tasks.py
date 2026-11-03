"""Independent scheduled-command thread for the dedicated rootless web Docker engine."""
import datetime,fcntl,http.client,json,os,pathlib,queue,re,secrets,socket,struct,threading,time,urllib.parse,urllib.request
IDENT=re.compile(r'[a-z0-9]{15}\Z');HEX=re.compile(r'[a-f0-9]{64}\Z');MAX_LOG=65536
# Process selection shared by the in-container watchdog and the cleanup exec, ported from
# terminalCleanupTargets (src/webApps/terminalTools.ts). The environment marker alone is not
# enough: `env -u PH_WEB_TASK_RUN setsid ...` drops it and the detached process is reparented
# to PID 1. We therefore also kill (1) every descendant of a marked process and (2) every
# process started during the run in a new Unix session, outside the tree of another Docker
# exec (ppid 0: terminal, other task, worker). A session holding any process that predates the
# run belongs to the site, even when its leader exited (double-forked `gunicorn --daemon`). `start` is in ms epoch from btime + jiffies (0 when unknown: marker tree only).
TARGETS=r'''
function pick(rows,self,since){if(!(typeof since==='number'&&Number.isFinite(since)&&since>0))since=Infinity;const byPid=new Map(rows.map(r=>[r.pid,r])),init=byPid.get(1),baseline=new Set([0,init?init.sid:-1]);for(const r of rows)if(r.start>0&&r.start<since)baseline.add(r.sid);const kinds=new Map();function kind(pid){if(kinds.has(pid))return kinds.get(pid);kinds.set(pid,'app');const r=byPid.get(pid);let v='app';if(!r)v='app';else if(r.ours)v='ours';else if(pid===self)v='self';else if(pid!==1&&r.ppid===0)v='foreign';else if(r.ppid>1)v=kind(r.ppid);kinds.set(pid,v);return v}return rows.filter(r=>r.pid>1&&r.pid!==self&&(kind(r.pid)==='ours'||(kind(r.pid)==='app'&&r.start>=since&&!baseline.has(r.sid)))).map(r=>r.pid)}
function table(key){const fs=require('fs');let btime=0;try{btime=+/^btime ([0-9]+)$/m.exec(fs.readFileSync('/proc/stat','utf8'))[1]*1000}catch{}const rows=[];for(const x of fs.readdirSync('/proc')){if(!/^[1-9][0-9]*$/.test(x))continue;try{const s=fs.readFileSync('/proc/'+x+'/stat','utf8'),f=s.slice(s.lastIndexOf(')')+2).split(' ');let ours=false;try{ours=fs.readFileSync('/proc/'+x+'/environ').toString().split(String.fromCharCode(0)).includes(key)}catch{}rows.push({pid:+x,ppid:+f[1],sid:+f[3],start:btime?btime+(+f[19])*10:0,ours})}catch{}}return rows}
'''
# The watchdog lives INSIDE the container. Losing this Python worker or HTTP attachment
# does not remove its deadline. Only the child inherits the unique cleanup marker; the
# supervisor itself (and its `timeout` parent) stay unmarked so the exit code survives.
SUPERVISOR=TARGETS+r'''
const fs=require('fs'),cp=require('child_process');const [marker,seconds,command,restoreText]=process.argv.slice(1);const restore=JSON.parse(restoreText||'{}');if(!/^[a-f0-9]{64}$/.test(marker)||!/^\d{1,3}$/.test(seconds)||+seconds<1||+seconds>900)process.exit(125);
const key='PH_WEB_TASK_RUN='+marker,since=Date.now()-2000;let closing=false,timer;
function targets(){return pick(table(key),process.pid,since)}
function kill(signal){for(const p of targets()){try{process.kill(+p,signal)}catch{}}}
function finish(code){if(closing)return;closing=true;clearTimeout(timer);kill('SIGTERM');setTimeout(()=>{kill('SIGKILL');setTimeout(()=>process.exit(code),100)},300)}
const portableShell=!fs.existsSync('/bin/sh');const shell=portableShell?'/__pockethost_tools/ld.so':'/bin/sh';const shellArgs=portableShell?['--library-path','/__pockethost_tools/lib','/__pockethost_tools/sh','-lc',command]:['-lc',command];const child=cp.spawn(shell,shellArgs,{detached:true,stdio:'inherit',env:{...process.env,...restore,PH_WEB_TASK_RUN:marker}});child.on('error',()=>finish(125));child.on('exit',(code,signal)=>finish(Number.isInteger(code)?code:signal==='SIGTERM'?143:137));timer=setTimeout(()=>finish(124),+seconds*1000);for(const signal of ['SIGTERM','SIGHUP','SIGINT'])process.on(signal,()=>finish(143));
'''
# `since` (ms epoch) comes from the worker; without a valid value only the marker tree is killed.
# Two KILL passes catch processes respawned between the TERM and the first KILL.
CLEANUP=TARGETS+r'''
const [marker,sinceText]=process.argv.slice(1);if(!/^[a-f0-9]{64}$/.test(marker))process.exit(1);const key='PH_WEB_TASK_RUN='+marker,since=/^[1-9][0-9]{0,15}$/.test(sinceText||'')?+sinceText:Infinity;function kill(s){for(const p of pick(table(key),process.pid,since)){try{process.kill(p,s)}catch{}}}kill('SIGTERM');setTimeout(()=>{kill('SIGKILL');setTimeout(()=>{kill('SIGKILL');setTimeout(()=>process.exit(0),100)},700)},300)
'''
def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
class Redactor:
 def __init__(self,values):self.values=sorted({value.encode()for value in values if value},key=len,reverse=True);self.pending=b'';self.output=b'';self.truncated=False;self.hold=max([len(value)for value in self.values]+[1])
 def feed(self,data,final=False):
  self.pending+=data;cut=len(self.pending)if final else max(0,len(self.pending)-self.hold)
  for value in self.values:
   start=self.pending.find(value,max(0,cut-len(value)+1))
   if start>=0 and start<cut<start+len(value):cut=start
  output,self.pending=self.pending[:cut],self.pending[cut:]
  for value in self.values:output=output.replace(value,b'[masqu\xc3\xa9]')
  self.output+=output
  if len(self.output)>MAX_LOG:self.output=self.output[-MAX_LOG:];self.truncated=True
 def finish(self):self.feed(b'',True);return self.output.decode(errors='replace')
class _UnixHTTP(http.client.HTTPConnection):
 def __init__(self,path,timeout=8):super().__init__('localhost',timeout=timeout);self.path=path
 def connect(self):self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(self.timeout);self.sock.connect(self.path)
def valid_readiness_mounts(value,job):
 """Accept only the published service's exact platform-owned, read-only DB mounts."""
 if value.get('HostConfig',{}).get('Binds'):return False
 mounts=value.get('Mounts',[]);binds=[mount for mount in mounts if mount.get('Type')=='bind']
 if (value.get('Config',{}).get('Labels')or{}).get('pockethost.web.private-db.wait')!='true':return not binds
 root=os.environ.get('WEB_APPS_ROOT','/home/pockethost-web/data')
 if not os.path.isabs(root)or os.path.normpath(root)!=root or not all(IDENT.fullmatch(str(job.get(key,'')))for key in ['appId','deploymentId']):return False
 service=(value.get('Config',{}).get('Labels')or{}).get('pockethost.web.service','web')
 if not isinstance(service,str)or not re.fullmatch('[a-z][a-z0-9-]{0,30}',service):return False
 home=os.path.join(root,'private-db-runtime',job['appId'],job['deploymentId']+'-'+service)
 expected={'/run/kolab-ready':home+'/ready','/run/kolab-wait-env':home+'/wait-env','/run/kolab-wait.cjs':'/opt/pockethost-web/web_private_db_wait.cjs'}
 if len(binds)!=3 or len({mount.get('Destination')for mount in binds})!=3:return False
 for mount in mounts:
  destination=mount.get('Destination','')
  overlaps=any(destination==path or destination.startswith(path+'/')or path.startswith(destination+'/')for path in expected)
  if mount.get('Type')=='bind':
   if mount.get('RW')is not False or destination not in expected or mount.get('Source')!=expected[destination]:return False
  elif overlaps:return False
 return True
class DockerTasks:
 def __init__(self,path):self.path=path
 def verify(self):
  match=re.fullmatch(r'/run/user/([1-9][0-9]*)/docker\.sock',self.path)
  if not match or os.getuid()==0 or int(match[1])!=os.getuid()or pathlib.Path(self.path).resolve()!=pathlib.Path(self.path)or not pathlib.Path(self.path).is_socket():raise RuntimeError('Moteur rootless dédié requis.')
  info=self.request('GET','/info')
  if not any('rootless'in value for value in info.get('SecurityOptions',[]))or str(info.get('CgroupVersion'))!='2'or info.get('CgroupDriver')!='systemd':raise RuntimeError('Moteur rootless et quotas requis.')
 def request(self,method,path,body=None):
  connection=_UnixHTTP(self.path)
  try:
   connection.request(method,'/v1.47'+path,body=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'});response=connection.getresponse();raw=response.read(1000000)
   if not 200<=response.status<300:raise RuntimeError('Le moteur web refuse cette opération.')
   return json.loads(raw)if raw else{}
  finally:connection.close()
 def inspect(self,job):
  expected='ph-web-'+job['appId']+'-'+job['deploymentId']
  if job['containerName']!=expected:raise RuntimeError('Conteneur publié invalide.')
  value=self.request('GET','/containers/'+expected+'/json');config=value.get('Config',{});host=value.get('HostConfig',{});labels=config.get('Labels',{})or{}
  if not HEX.fullmatch(value.get('Id',''))or value.get('Name','').lstrip('/')!=expected or not value.get('State',{}).get('Running')or config.get('User')!='10001:10001'or labels.get('pockethost.web')!='true'or labels.get('pockethost.web.app')!=job['appId']or labels.get('pockethost.web.deployment')!=job['deploymentId']or host.get('Privileged')or not host.get('ReadonlyRootfs'):raise RuntimeError('Le conteneur publié ne respecte pas son isolation.')
  if not valid_readiness_mounts(value,job):raise RuntimeError('Montages de connexion privée non autorisés dans le conteneur du site.')
  from web_app_runtime import tooling_volume_name,tooling_node_prefix
  tools_image=os.environ.get('WEB_APPS_STORAGE_HELPER_IMAGE','');tools=tooling_volume_name(tools_image)if re.fullmatch('sha256:[a-f0-9]{64}',tools_image)else'';portable=False
  for mount in value.get('Mounts',[]):
   if mount.get('Type')not in ['volume','tmpfs','bind']or 'docker.sock'in str(mount.get('Source','')):raise RuntimeError('Montage non autorisé dans le conteneur du site.')
   if mount.get('Type')=='volume':
    source=mount.get('Name','');destination=mount.get('Destination','')
    if tools and source==tools and labels.get('pockethost.web.tooling')==tools and destination=='/__pockethost_tools'and mount.get('RW')is False:portable=True
    elif source=='ph-web-next-'+job['appId']+'-'+job['deploymentId']:pass
    elif re.fullmatch('ph-web-data-'+job['appId']+r'-[a-z][a-z0-9-]{0,30}-(?:v1|r[a-z0-9]{15})',source)and(destination=='/app/.next/cache'or re.fullmatch(r'/(?:data|mnt|app/(?:data|uploads|storage))(?:/[A-Za-z0-9_.-]+)*',destination))and not any(part in ('.','..')for part in destination.split('/')):pass
    else:raise RuntimeError('Montage non autorisé dans le conteneur du site.')
  self.node_prefix=tooling_node_prefix()if portable else['/usr/local/bin/node']
  self.portable=portable
  if 'ALL'not in host.get('CapDrop',[])or not any(str(v).startswith('no-new-privileges')for v in host.get('SecurityOpt',[]))or host.get('CapAdd')or host.get('NetworkMode')=='host'or host.get('PidMode')=='host':raise RuntimeError('Privilèges du conteneur invalides.')
  self.restore_environment={}
  for entry in config.get('Env',[]):
   name,sep,original_value=entry.partition('=')
   if sep and re.match(r'^(LD_|NODE_OPTIONS$|NODE_PATH$|OPENSSL_CONF$|OPENSSL_MODULES$|GLIBC_TUNABLES$)',name):self.restore_environment[name]=original_value
  return value['Id']
 def create(self,container,marker,seconds,command):
  if not HEX.fullmatch(container)or not HEX.fullmatch(marker)or not isinstance(seconds,int)or not 1<=seconds<=900:raise RuntimeError('Exécution invalide.')
  value=self.request('POST','/containers/'+container+'/exec',{'User':'10001:10001','Privileged':False,'AttachStdin':False,'AttachStdout':True,'AttachStderr':True,'Tty':False,'Env':[name+'='for name in sorted(set(getattr(self,'restore_environment',{}))|{'NODE_OPTIONS','NODE_PATH','LD_PRELOAD','LD_LIBRARY_PATH','LD_AUDIT','OPENSSL_CONF','OPENSSL_MODULES','GLIBC_TUNABLES'})],'Cmd':([]if getattr(self,'portable',False)else['/usr/bin/timeout','--signal=TERM','--kill-after=2s',str(seconds+5)+'s'])+getattr(self,'node_prefix',['/usr/local/bin/node'])+['-e',SUPERVISOR,marker,str(seconds),command,json.dumps(getattr(self,'restore_environment',{}))]})
  if not HEX.fullmatch(value.get('Id','')):raise RuntimeError('Identifiant Docker invalide.')
  return value['Id']
 def attach(self,ident):
  if not HEX.fullmatch(ident):raise RuntimeError('Exécution Docker invalide.')
  connection=_UnixHTTP(self.path,8);connection.request('POST','/v1.47/exec/'+ident+'/start',body=b'{"Detach":false,"Tty":false}',headers={'Content-Type':'application/json'});raw_socket=connection.sock;response=connection.getresponse()
  if response.status not in [101,200]:connection.close();raise RuntimeError('Le moteur refuse la commande.')
  raw_socket.settimeout(None);return connection,response,raw_socket
 def cleanup(self,container,marker,since=None):
  if not HEX.fullmatch(container)or not HEX.fullmatch(marker):return
  since=str(since)if type(since)is int and since>0 else''
  value=self.request('POST','/containers/'+container+'/exec',{'User':'10001:10001','Privileged':False,'AttachStdin':False,'AttachStdout':False,'AttachStderr':False,'Tty':False,'Env':[name+'='for name in sorted(set(getattr(self,'restore_environment',{}))|{'NODE_OPTIONS','NODE_PATH','LD_PRELOAD','LD_LIBRARY_PATH','LD_AUDIT','OPENSSL_CONF','OPENSSL_MODULES','GLIBC_TUNABLES'})],'Cmd':([]if getattr(self,'portable',False)else['/usr/bin/timeout','--kill-after=1s','4s'])+getattr(self,'node_prefix',['/usr/local/bin/node'])+['-e',CLEANUP,marker,since]})
  if HEX.fullmatch(value.get('Id','')):self.request('POST','/exec/'+value['Id']+'/start',{'Detach':False,'Tty':False})
class TaskWorker:
 def __init__(self,control_url,secret,docker_socket,root,stop_event=None):
  url=urllib.parse.urlsplit(control_url)
  if url.scheme!='http'or url.hostname not in ['127.0.0.1','::1']or url.username or url.password or url.query or url.fragment or url.path not in ['', '/']or len(secret)<32:raise ValueError('Contrôleur local et secret privé requis.')
  self.control=control_url.rstrip('/');self.secret=secret;self.root=pathlib.Path(root).absolute();self.stop=stop_event or threading.Event();self.engine=DockerTasks(docker_socket)
 def api(self,path,body):
  from platform_maintenance import claim_response
  paused=claim_response(path,'tasks')
  if paused is not None:return paused
  request=urllib.request.Request(self.control+'/api/web-apps/_tasks/'+path,headers={'Content-Type':'application/json','X-Web-Worker':self.secret},data=json.dumps(body).encode())
  # No redirects: worker credentials never leave the configured loopback controller.
  class NoRedirect(urllib.request.HTTPRedirectHandler):
   def redirect_request(self,*args,**kwargs):return None
  with urllib.request.build_opener(NoRedirect).open(request,timeout=15)as response:return json.loads(response.read(1000000))
 def status(self,ready,run=''):
  value={'ready':ready,'checkedAt':stamp(),'runId':run};temp=self.root/('.tasks-status-'+secrets.token_hex(8))
  try:
   fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o640)
   with os.fdopen(fd,'w')as output:json.dump(value,output)
   os.replace(temp,self.root/'tasks-status.json')
  finally:
   try:temp.unlink()
   except FileNotFoundError:pass
 def validate_job(self,job):
  if not isinstance(job,dict)or not all(IDENT.fullmatch(str(job.get(key,'')))for key in ['id','taskId','appId','deploymentId'])or not re.fullmatch('[A-Za-z0-9]{80}',str(job.get('proof','')))or not isinstance(job.get('command'),str)or not 1<=len(job['command'])<=4000 or '\0'in job['command']or type(job.get('timeoutSeconds'))is not int or not 1<=job['timeoutSeconds']<=900 or not isinstance(job.get('secrets'),list)or len(job['secrets'])>200 or any(not isinstance(value,str)for value in job['secrets'])or sum(len(value)for value in job['secrets'])>200000:raise RuntimeError('Mission de tâche invalide.')
 def execute(self,job):
  self.validate_job(job);marker=secrets.token_hex(32);container='';connection=response=raw_socket=None;reader=None;reader_stop=threading.Event();chunks=queue.Queue(maxsize=32);redactor=Redactor(job['secrets']);reason='';code=-1;status='failed';buffer=b'';started=time.monotonic();last_check=started-10;since=int(time.time()*1000)-2000;stage='inspect'
  try:
   container=self.engine.inspect(job);stage='create';ident=self.engine.create(container,marker,job['timeoutSeconds'],job['command']);stage='attach';connection,response,raw_socket=self.engine.attach(ident);stage='output'
   def read():
    try:
     while not reader_stop.is_set():
      data=response.fp.read1(8192)if response.status==101 else response.read1(8192)
      while not reader_stop.is_set():
       try:chunks.put(data,timeout=.2);break
       except queue.Full:continue
      if not data:return
    except Exception:
     try:chunks.put_nowait(None)
     except queue.Full:pass
   reader=threading.Thread(target=read,name='web-task-output',daemon=True);reader.start()
   while True:
    if self.stop.is_set():reason='worker_shutdown';raise RuntimeError()
    elapsed=time.monotonic()-started
    if elapsed>job['timeoutSeconds']+8:status='timed_out';code=124;raise RuntimeError()
    if time.monotonic()-last_check>5:
     if not self.api('validate',{'id':job['id'],'proof':job['proof']}).get('valid'):reason='container_changed';raise RuntimeError()
     self.status(True,job['id']);last_check=time.monotonic()
    try:data=chunks.get(timeout=.25)
    except queue.Empty:continue
    if data is None:raise RuntimeError()
    if data==b'':break
    buffer+=data
    while len(buffer)>=8:
     stream=buffer[0];length=struct.unpack('>I',buffer[4:8])[0]
     if stream not in [1,2]or buffer[1:4]!=b'\0\0\0'or length>1048576:raise RuntimeError()
     if len(buffer)<8+length:break
     redactor.feed(buffer[8:8+length]);buffer=buffer[8+length:]
   if buffer:raise RuntimeError()
   stage='result';details=self.engine.request('GET','/exec/'+ident+'/json');code=details.get('ExitCode',-1)
   if details.get('Running')or type(code)is not int or not -1<=code<=255:raise RuntimeError()
   status='succeeded'if code==0 else'timed_out'if code in [124,137]and time.monotonic()-started>=job['timeoutSeconds']else'failed'
  except Exception:
   if not reason and status!='timed_out':
    # Never expose raw engine exceptions, container environment or command text.
    diagnostic={'inspect':'Validation du conteneur publié refusée (isolation ou montages).','create':'Création de la commande refusée par le moteur.','attach':'Démarrage ou connexion à la commande impossible.','output':'Lecture de la sortie ou validation de l’exécution impossible.','result':'Lecture du code de sortie impossible.'}[stage]
    redactor.feed(('\n[Kolab] '+diagnostic+'\n').encode())
  finally:
   if container:
    try:self.engine.cleanup(container,marker,since)
    except Exception:
     status='failed';reason='cleanup_failed';redactor.feed('\n[Kolab] Nettoyage des processus de la tâche impossible.\n'.encode())
   reader_stop.set()
   if raw_socket:
    try:raw_socket.shutdown(socket.SHUT_RDWR)
    except OSError:pass
   if reader:reader.join(timeout=2)
   if response:
    try:response.close()
    except OSError:pass
   if connection:connection.close()
  log=redactor.finish();self.api('report',{'id':job['id'],'proof':job['proof'],'status':status,'exitCode':code,'log':log,'truncated':redactor.truncated,'reason':reason});self.status(True)
 def loop(self):
  self.root.mkdir(parents=True,exist_ok=True,mode=0o750)
  if self.root.resolve()!=self.root:return
  fd=os.open(self.root/'.tasks-worker.lock',os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
  try:
   try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:return
   while not self.stop.is_set():
    try:
     self.engine.verify();self.status(True);job=self.api('claim',{}).get('job')
     if job:self.execute(job)
    except Exception:
     try:self.status(False)
     except OSError:pass
    self.stop.wait(2)
  finally:
   try:self.status(False)
   except OSError:pass
   os.close(fd)
def start_task_runner(control_url,secret,docker_socket,root,stop_event=None):
 worker=TaskWorker(control_url,secret,docker_socket,root,stop_event);thread=threading.Thread(target=worker.loop,name='web-scheduled-tasks',daemon=True);thread.start();return thread
