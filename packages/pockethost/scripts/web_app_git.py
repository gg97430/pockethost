"""Generic SSH Git source (deploy key): pinned host keys, vetted IP, hardened ssh/git, bounded checkout."""
import ipaddress,os,pathlib,queue,re,shlex,shutil,socket,subprocess,sys,tempfile,threading

ALIAS='ph-git-source'
HOST=re.compile(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*')
USER=re.compile(r'[A-Za-z0-9_][A-Za-z0-9._-]{0,31}')
PATH=re.compile(r'/?[A-Za-z0-9_.~@+-][A-Za-z0-9_.~@+/-]{0,249}')
BRANCH=re.compile(r'[A-Za-z0-9_][A-Za-z0-9._/+-]{0,249}')
KEY_TYPES={'ssh-ed25519','ecdsa-sha2-nistp256','ecdsa-sha2-nistp384','ecdsa-sha2-nistp521','ssh-rsa'}
LIMITS={'packBytes':256*1024**2,'bytes':2*1024**3,'entries':100000,'reserveBytes':15*1024**3}
FALLBACK_DEPTH=50
TOO_BIG_PACK='Dépôt Git trop volumineux (256 Mio compressés maximum).'
TOO_BIG_TREE='Dépôt Git trop volumineux (2 Gio et 100 000 fichiers maximum).'
# Applies RLIMIT_FSIZE then execs git: the kept pack (fetch.unpackLimit=1) and every checked-out file are bounded.
LIMITER='import os,resource,signal,sys\nn=int(sys.argv[1]);resource.setrlimit(resource.RLIMIT_FSIZE,(n,n))\nfor s in (signal.SIGPIPE,signal.SIGXFSZ):signal.signal(s,signal.SIG_DFL)\nos.execvp(sys.argv[2],sys.argv[2:])'
GIT_CONFIG=['-c','protocol.allow=never','-c','protocol.ssh.allow=always','-c','protocol.file.allow=never','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false','-c','core.sshCommand=','-c','submodule.recurse=false','-c','fetch.recurseSubmodules=false','-c','fetch.unpackLimit=1','-c','transfer.unpackLimit=1','-c','gc.auto=0','-c','maintenance.auto=false','-c','advice.detachedHead=false','-c','init.defaultBranch=ph','-c','core.symlinks=true','-c','core.autocrlf=false','-c','credential.helper=']

class GitCommandFailed(RuntimeError):
 """A git/ssh subprocess exited non-zero (the only failure that may trigger the branch fallback)."""
 def __init__(self):super().__init__('Commande Git refusée.')

def public_ip(value):
 try:ip=ipaddress.ip_address(value)
 except ValueError:return False
 if ip.version==6 and(ip.ipv4_mapped or ip.sixtofour or ip.teredo or ip in ipaddress.ip_network('64:ff9b::/96')or ip in ipaddress.ip_network('64:ff9b:1::/48')):return False
 return ip.is_global and not ip.is_multicast

def vetted_address(host,port,timeout=5):
 """Resolve once and pin the result: every answer must be a public unicast address (no rebinding mix)."""
 try:literal=ipaddress.ip_address(host);return str(literal) if public_ip(host) else _refuse()
 except ValueError:pass
 result=queue.Queue(maxsize=1)
 def resolve():
  try:result.put(socket.getaddrinfo(host,port,type=socket.SOCK_STREAM))
  except Exception:result.put(None)
 threading.Thread(target=resolve,name='web-git-dns',daemon=True).start()
 try:values=result.get(timeout=timeout)
 except queue.Empty:raise RuntimeError('Résolution DNS de l’hôte Git trop lente.')from None
 if not values:raise RuntimeError('Hôte Git introuvable.')
 addresses=[]
 for family,_,_,_,address in values:
  if family not in(socket.AF_INET,socket.AF_INET6)or not public_ip(address[0]):_refuse()
  addresses.append(str(ipaddress.ip_address(address[0])))
 return addresses[0]

def _refuse():raise RuntimeError('Hôte Git refusé : seules les adresses publiques sont autorisées.')

def validate(job_git,commit=None):
 """Strict validation of the claim payload; nothing reaches argv/config unless it matches."""
 if not isinstance(job_git,dict):raise RuntimeError('Source Git invalide.')
 host=str(job_git.get('host','')).lower();port=job_git.get('port',22);user=str(job_git.get('sshUser')or'git');path=str(job_git.get('path',''))
 branch=str(job_git.get('branch')or'');commit=commit or str(job_git.get('commit',''));key=job_git.get('privateKey')
 is_ip=False
 try:ipaddress.ip_address(host);is_ip=True
 except ValueError:pass
 if not(is_ip or HOST.fullmatch(host))or host.startswith('-'):raise RuntimeError('Hôte Git invalide.')
 if isinstance(port,bool)or not isinstance(port,int)or not 1<=port<=65535:raise RuntimeError('Port SSH invalide.')
 if not USER.fullmatch(user):raise RuntimeError('Utilisateur SSH invalide.')
 parts=path.strip('/').split('/')
 if not PATH.fullmatch(path)or path.startswith('-')or'//'in path or any(p in('','.','..')or p.startswith('-')for p in parts):raise RuntimeError('Chemin du dépôt Git invalide.')
 if branch and(not BRANCH.fullmatch(branch)or'..'in branch or'//'in branch or'@{'in branch or branch.endswith(('/','.','.lock'))or'/.'in branch):raise RuntimeError('Branche Git invalide.')
 if not re.fullmatch('[a-f0-9]{40}',commit):raise RuntimeError('Commit Git invalide.')
 if not isinstance(key,str)or len(key)>20000 or not re.fullmatch(r'-----BEGIN OPENSSH PRIVATE KEY-----\r?\n[A-Za-z0-9+/=\r\n]+-----END OPENSSH PRIVATE KEY-----\s*',key):raise RuntimeError('Clé de déploiement absente ou invalide.')
 keys=job_git.get('hostKeys')
 if not isinstance(keys,list)or not 1<=len(keys)<=20:raise RuntimeError('Empreinte de l’hôte Git non confirmée.')
 pinned=[]
 for item in keys:
  kind=item.get('type')if isinstance(item,dict)else None;value=item.get('key')if isinstance(item,dict)else None
  if kind not in KEY_TYPES or not isinstance(value,str)or not re.fullmatch(r'[A-Za-z0-9+/]{16,4096}={0,3}',value):raise RuntimeError('Empreinte de l’hôte Git invalide.')
  pinned.append((kind,value))
 return {'host':host,'port':port,'user':user,'path':path,'branch':branch,'commit':commit,'key':key.replace('\r\n','\n').strip()+'\n','hostKeys':pinned}

def ssh_files(folder,source,address):
 """Private 0700 dir: id 0600, known_hosts from pinned keys only, -F config (system/user config ignored)."""
 folder=pathlib.Path(folder);folder.chmod(0o700)
 def write(name,text):
  fd=os.open(folder/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
  with os.fdopen(fd,'w')as f:f.write(text)
  return folder/name
 identity=write('id',source['key'])
 known=write('known_hosts',''.join(source['host']+' '+kind+' '+value+'\n'for kind,value in source['hostKeys']))
 options=[('HostName',address),('Port',source['port']),('User',source['user']),('HostKeyAlias',source['host']),('IdentityFile',identity),('IdentitiesOnly','yes'),('IdentityAgent','none'),('AddKeysToAgent','no'),('CertificateFile','none'),
  ('BatchMode','yes'),('PreferredAuthentications','publickey'),('PubkeyAuthentication','yes'),('PasswordAuthentication','no'),('KbdInteractiveAuthentication','no'),('HostbasedAuthentication','no'),('GSSAPIAuthentication','no'),
  ('StrictHostKeyChecking','yes'),('UserKnownHostsFile',known),('GlobalKnownHostsFile','/dev/null'),('UpdateHostKeys','no'),('CheckHostIP','no'),('VerifyHostKeyDNS','no'),('HashKnownHosts','no'),
  ('ForwardAgent','no'),('ForwardX11','no'),('ForwardX11Trusted','no'),('ClearAllForwardings','yes'),('PermitLocalCommand','no'),('ProxyCommand','none'),('ProxyJump','none'),('Tunnel','no'),('RequestTTY','no'),
  ('ControlMaster','no'),('ControlPath','none'),('ConnectTimeout',8),('ServerAliveInterval',15),('ServerAliveCountMax',2),('LogLevel','ERROR')]
 config=write('config','Host '+ALIAS+'\n'+''.join(' '+name+' '+str(value)+'\n'for name,value in options))
 return identity,known,config

def git_environment(folder,config):
 ssh=shutil.which('ssh')
 if not ssh:raise RuntimeError('OpenSSH doit être installé sur le serveur web pour ce dépôt.')
 return {'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':str(folder),'XDG_CONFIG_HOME':str(folder),'LC_ALL':'C','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_ALLOW_PROTOCOL':'ssh','GIT_PROTOCOL_FROM_USER':'0','GIT_TERMINAL_PROMPT':'0','GIT_ASKPASS':'/bin/false','SSH_ASKPASS':'/bin/false','SSH_ASKPASS_REQUIRE':'never','GIT_LFS_SKIP_SMUDGE':'1','GIT_SSH_VARIANT':'ssh','GIT_SSH_COMMAND':shlex.join([ssh,'-F',str(config)])}

def default_run(log):
 def run(args,cwd,env):
  try:result=subprocess.run(args,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=600)
  except subprocess.TimeoutExpired:raise RuntimeError('Délai de récupération du dépôt Git dépassé.')from None
  log(result.stdout.decode(errors='replace'))
  if result.returncode:raise GitCommandFailed()
 return run

def fetch_git(job_git,dest,limits=None,log=None,run=None,commit=None,root=None):
 """Fetch exactly `commit` into dest/'source' and return that path. `run(args,cwd,env)` must raise
 GitCommandFailed on a non-zero exit (the worker passes Job.run for heartbeats/cancel/sanitized logs)."""
 limits={**LIMITS,**(limits or {})};log=log or(lambda text:None);run=run or default_run(log);dest=pathlib.Path(dest)
 if not(shutil.which('git')and shutil.which('ssh')):raise RuntimeError('Git et OpenSSH doivent être installés sur le serveur web pour ce dépôt.')
 source=validate(job_git,commit);commit=source['commit']
 address=vetted_address(source['host'],source['port'])
 keys=pathlib.Path(tempfile.mkdtemp(prefix='git-ssh-',dir=dest))
 src=dest/'source'
 try:
  _,_,config=ssh_files(keys,source,address);env=git_environment(keys,config)
  git=['git',*GIT_CONFIG];limited=lambda size,args:[sys.executable,'-I','-c',LIMITER,str(size),*args]
  def local(*args,timeout=120):
   result=subprocess.run([*git,'-C',str(src),*args],env=env,stdin=subprocess.DEVNULL,capture_output=True,timeout=timeout)
   if result.returncode:raise GitCommandFailed()
   return result.stdout
  if src.exists():raise RuntimeError('Dossier source déjà présent.')
  subprocess.run([*git,'init','-q','--template=',str(src)],env=env,stdin=subprocess.DEVNULL,capture_output=True,timeout=60,check=True)
  local('remote','add','origin',ALIAS+':'+source['path'])
  log('Récupération de '+commit[:12]+' depuis '+source['host']+(':'+str(source['port'])if source['port']!=22 else'')+' ('+address+').\n')
  fetch=['-C',str(src),'fetch','-q','--no-tags','--no-recurse-submodules','--no-write-commit-graph']
  try:run(limited(limits['packBytes'],[*git,*fetch,'--depth','1','origin',commit]),str(dest),env);by_sha=True
  except GitCommandFailed:
   # index-pack killed by RLIMIT_FSIZE: the pack is over the cap, retrying by branch would only download more.
   if pack_overflow(src,limits):raise RuntimeError(TOO_BIG_PACK)from None
   if not source['branch']:raise RuntimeError('Le serveur Git refuse la récupération par commit et aucune branche n’est connue.')from None
   log('Récupération par commit refusée, repli sur la branche '+source['branch']+'.\n')
   try:run(limited(limits['packBytes'],[*git,*fetch,'--depth',str(FALLBACK_DEPTH),'origin','refs/heads/'+source['branch']]),str(dest),env)
   except GitCommandFailed:
    if pack_overflow(src,limits):raise RuntimeError(TOO_BIG_PACK)from None
    raise RuntimeError('Impossible de récupérer le dépôt Git. Vérifiez la clé de déploiement (lecture seule), l’empreinte de l’hôte et la branche.')from None
   by_sha=False
  try:fetched=local('rev-parse','--verify','--quiet',(('FETCH_HEAD'if by_sha else commit)+'^{commit}')).decode().strip()
  except GitCommandFailed:fetched=''
  if fetched!=commit:raise RuntimeError('Le commit demandé est introuvable sur le dépôt Git (branche déplacée ou réécrite). Relancez le déploiement.')
  pack=sum(p.stat().st_size for p in(src/'.git').rglob('*')if p.is_file()and not p.is_symlink())
  if pack>limits['packBytes']+64*1024**2:raise RuntimeError(TOO_BIG_PACK)
  # Pre-check the tree like the tarball members (streamed, never buffered): size, entry count, then the disk reserve.
  total,entries,gitlinks=tree_stats([*git,'-C',str(src),'ls-tree','-r','-l','-z','--full-tree',commit],env,limits)
  if shutil.disk_usage(root or dest).free<limits['reserveBytes']+total:raise RuntimeError('Récupération suspendue : la taille du dépôt consommerait la réserve de 15 Gio.')
  try:run(limited(limits['bytes'],[*git,'-C',str(src),'checkout','-q','--detach',commit]),str(dest),env)
  except GitCommandFailed:raise RuntimeError('Extraction du dépôt Git impossible.')from None
  if local('rev-parse','HEAD').decode().strip()!=commit:raise RuntimeError('Le commit extrait ne correspond pas au commit demandé.')
  if gitlinks:log('Sous-modules ignorés : ils ne sont pas récupérés ('+str(gitlinks)+').\n')
  dotgit=src/'.git'
  if dotgit.is_symlink()or not dotgit.is_dir():raise RuntimeError('Extraction du dépôt Git invalide.')
  shutil.rmtree(dotgit)
  check_tree(src,limits)
  return src
 except subprocess.TimeoutExpired:raise RuntimeError('Délai de récupération du dépôt Git dépassé.')from None
 except (OSError,ValueError,subprocess.SubprocessError,GitCommandFailed):raise RuntimeError('Récupération du dépôt Git impossible.')from None
 finally:shutil.rmtree(keys,ignore_errors=True)

def pack_overflow(src,limits):
 """True when a (tmp_)pack reached RLIMIT_FSIZE: git then exits non-zero exactly like a refused fetch."""
 folder=pathlib.Path(src)/'.git'/'objects'/'pack'
 try:return folder.is_dir()and any(p.is_file()and not p.is_symlink()and p.stat().st_size>=limits['packBytes']for p in folder.iterdir())
 except OSError:return False

def tree_stats(args,env,limits,timeout=300):
 """Streams `ls-tree -r -l -z` and stops at the first cap. Shared subtrees (tree bomb) expand to billions of entries
 from a few KiB of pack, so the output is never buffered and git is killed as soon as a limit is crossed."""
 proc=subprocess.Popen(args,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
 expired=[]
 def kill():
  try:os.killpg(proc.pid,9)
  except OSError:pass
 timer=threading.Timer(timeout,lambda:(expired.append(1),kill()));timer.daemon=True;timer.start()
 total=entries=gitlinks=0;rest=b'';refused=''
 try:
  while not refused:
   chunk=proc.stdout.read(65536)
   if not chunk:break
   *lines,rest=(rest+chunk).split(b'\0')
   if len(rest)>16384:refused='invalid';break
   for line in lines:
    meta,tab,_=line.partition(b'\t');fields=meta.split()
    if not tab or len(fields)!=4:refused='invalid';break
    entries+=1
    if fields[1]==b'commit':gitlinks+=1
    elif fields[3].isdigit():total+=int(fields[3])
    if entries>limits['entries']or total>limits['bytes']:refused='size';break
 finally:
  timer.cancel()
  if refused:kill()
  proc.stdout.close();code=proc.wait()
 if refused=='size':raise RuntimeError(TOO_BIG_TREE)
 if expired:raise RuntimeError('Délai de récupération du dépôt Git dépassé.')
 if refused or code or rest:raise GitCommandFailed()
 return total,entries,gitlinks

def check_tree(src,limits=None):
 """No symlink may resolve outside the checkout; no special files; same size/entry caps after checkout."""
 limits={**LIMITS,**(limits or {})};base=pathlib.Path(src).resolve();total=0;entries=0
 for folder,dirs,files in os.walk(base,followlinks=False):
  for name in dirs+files:
   path=pathlib.Path(folder)/name;info=path.lstat();entries+=1
   if path.is_symlink():
    if not(path.parent/os.readlink(path)).resolve().is_relative_to(base):raise RuntimeError('Lien source hors du projet.')
   elif path.is_file():total+=info.st_size
   elif not path.is_dir():raise RuntimeError('Fichier spécial refusé dans le dépôt Git.')
   if name=='.git':raise RuntimeError('Métadonnées Git imbriquées refusées.')
  if entries>limits['entries']or total>limits['bytes']:raise RuntimeError(TOO_BIG_TREE)
