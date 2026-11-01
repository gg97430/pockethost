/**
 * Source « Dépôt Git (clé de déploiement) » : une clé Ed25519 par dépôt (GitHub refuse la même clé sur deux dépôts),
 * partagée par les applications et environnements du même dépôt. La clé privée est chiffrée avec WEB_APPS_MASTER_KEY et
 * ne quitte le contrôleur que vers le worker local (claim). Les clés d'hôte sont épinglées : valeurs publiées pour
 * github.com, gitlab.com et bitbucket.org, sinon scan + confirmation explicite des empreintes (TOFU). Routes
 * enregistrées dans hooks.ts ; claim, validateConfig et webhook câblés dans api.ts. Permissions : source.git.configure
 * (plateforme : créer, supprimer, rattacher), source.hostKey.scan et source.deployKey.rotate (admin, débit limité),
 * app.configure (test, branches), app.read (lecture : clé publique et empreintes). La source suit le projet de ses
 * applications ; dès qu'une application qui l'utilise est hors de ce projet (sans projet, détachée, restaurée
 * ailleurs), seul un administrateur de la plateforme peut la gérer.
 */
import { parseSshEd25519PublicKey } from '../../../../../common/sshPublicKey'
import type { WebPermission } from '../../../../../common/webAccess'
import {
  WEB_GIT_HOST_KEY_TYPES,
  parseGitRemote,
  webGitBranchValid,
  webGitDisplayUrl,
  webGitFingerprintSet,
  webGitHostKeyPreset,
  webGitProvider,
  webGitRepository,
} from '../../../../../common/webGit'
import { isWebPlatformAdmin, requirePlatformAdmin, requireWebPermission, requireWebUser } from './access'
import {
  decryptWebValue,
  encryptWebValue,
  webConfig,
  webEnabled,
  webInput,
  webJson,
  webSourceBuildSupported,
} from './api'
import { webMembership } from './projects'

/**
 * Transport SSH durci du contrôleur (keygen, keyscan, ls-remote, test). Aucune sortie brute de ssh/git n'est renvoyée :
 * les erreurs sont des messages français fixes. Chaque connexion revérifie que l'hôte se résout uniquement vers des
 * adresses publiques et épingle HostName sur l'adresse vérifiée (pas de rebinding DNS).
 */
export const GIT_SOURCE_READER = String.raw`
import sys,json,os,re,base64,hashlib,ipaddress,pathlib,queue,shlex,signal,socket,subprocess,tempfile,threading
os.umask(0o077)
HOST=re.compile(r'(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]')
LOCAL=re.compile(r'(^|\.)(localhost|local|internal|localdomain|home\.arpa|arpa)$')
USER=re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}')
PATH=re.compile(r'[A-Za-z0-9._~/-]{1,250}')
BRANCH=re.compile(r'[A-Za-z0-9_./-]{1,200}')
TYPES=('ssh-ed25519','ecdsa-sha2-nistp256','ecdsa-sha2-nistp384','ecdsa-sha2-nistp521','ssh-rsa')
ALIAS='pockethost-git';LIMIT=5000;OUTPUT=4000000;BLOCKED=[ipaddress.ip_network(n)for n in ['64:ff9b::/96','64:ff9b:1::/48','2002::/16','2001::/32']]
MESSAGES={'invalid':'Adresse du dépôt Git invalide.','ssrf':'Cette adresse Git n’est pas autorisée : seuls les serveurs publics sont acceptés.','dns':'Nom d’hôte Git introuvable.','unreachable':'Le serveur Git est injoignable sur ce port.','timeout':'Le serveur Git n’a pas répondu à temps. Réessayez.','hostkey':'L’empreinte du serveur Git ne correspond pas aux clés confirmées. Vérifiez l’hôte puis confirmez de nouveau ses empreintes.','unpinned':'Confirmez d’abord les empreintes du serveur Git.','publickey':'Le serveur Git refuse la clé de déploiement. Ajoutez la clé publique au dépôt (lecture seule) puis réessayez.','repository':'Dépôt introuvable ou inaccessible avec cette clé de déploiement.','branch':'Branche introuvable dans ce dépôt.','nokeys':'Le serveur Git n’a présenté aucune clé d’hôte exploitable.','tools':'git et openssh-client doivent être installés sur ce serveur.','keygen':'La génération de la clé de déploiement a échoué.','failed':'L’accès au dépôt Git a échoué.'}
class Fail(Exception):
 def __init__(self,code):self.code=code;super().__init__(code)
def timeout(*args):raise TimeoutError()
def fingerprint(blob):return 'SHA256:'+base64.b64encode(hashlib.sha256(base64.b64decode(blob)).digest()).decode().rstrip('=')
def remote(value):
 if not isinstance(value,dict):raise Fail('invalid')
 host=str(value.get('host')or'');user=str(value.get('sshUser')or'git');path=str(value.get('path')or'');port=value.get('port',22)
 if not HOST.fullmatch(host)or LOCAL.search(host)or not USER.fullmatch(user)or type(port)is not int or not 1<=port<=65535:raise Fail('invalid')
 relative=path[1:]if path.startswith('/')else path
 if not PATH.fullmatch(path)or not relative or relative[0]in'-/'or any(p in['','.','..']for p in relative.split('/')):raise Fail('invalid')
 return {'host':host,'port':port,'user':user,'path':path}
def public(ip):
 mapped=getattr(ip,'ipv4_mapped',None)
 return ip.is_global and not ip.is_multicast and not(mapped and not mapped.is_global)and not getattr(ip,'sixtofour',None)and not getattr(ip,'teredo',None)and not any(ip.version==6 and ip in n for n in BLOCKED)
def addresses(host,port):
 result=queue.Queue(maxsize=1)
 def resolve():
  try:result.put(socket.getaddrinfo(host,port,type=socket.SOCK_STREAM))
  except Exception:result.put(None)
 threading.Thread(target=resolve,daemon=True).start()
 try:values=result.get(timeout=3)
 except queue.Empty:raise Fail('timeout')from None
 if not values:raise Fail('dns')
 checked=[]
 for family,kind,proto,_,address in values:
  if family not in(socket.AF_INET,socket.AF_INET6):raise Fail('ssrf')
  try:ip=ipaddress.ip_address(str(address[0]).split('%')[0])
  except ValueError:raise Fail('ssrf')from None
  if not public(ip):raise Fail('ssrf')
  if str(ip)not in checked:checked.append(str(ip))
 return sorted(checked,key=lambda a:':'in a)
def host_key(kind,key):
 if kind not in TYPES or not isinstance(key,str)or not 16<=len(key)<=4096:return None
 try:blob=base64.b64decode(key,validate=True)
 except Exception:return None
 if len(blob)<8 or blob[4:4+int.from_bytes(blob[:4],'big')]!=kind.encode():return None
 return {'type':kind,'key':key,'fingerprint':fingerprint(key)}
def pinned(values):
 keys=[k for k in(host_key(v.get('type'),v.get('key'))for v in(values if isinstance(values,list)else[])[:10]if isinstance(v,dict))if k]
 if not keys:raise Fail('unpinned')
 return keys
def ssh_config(folder,ip,target):
 q=lambda v:'"'+v+'"'
 options=[('HostName',ip),('Port',str(target['port'])),('User',target['user']),('HostKeyAlias',target['host']),('IdentityFile',q(folder+'/id_ed25519')),('IdentitiesOnly','yes'),('IdentityAgent','none'),('UserKnownHostsFile',q(folder+'/known_hosts')),('GlobalKnownHostsFile','/dev/null'),('StrictHostKeyChecking','yes'),('UpdateHostKeys','no'),('CheckHostIP','no'),('VerifyHostKeyDNS','no'),('BatchMode','yes'),('PasswordAuthentication','no'),('KbdInteractiveAuthentication','no'),('HostbasedAuthentication','no'),('PubkeyAuthentication','yes'),('PreferredAuthentications','publickey'),('ProxyCommand','none'),('ProxyJump','none'),('ForwardAgent','no'),('ForwardX11','no'),('ForwardX11Trusted','no'),('ClearAllForwardings','yes'),('PermitLocalCommand','no'),('RequestTTY','no'),('Tunnel','no'),('ControlMaster','no'),('ControlPath','none'),('AddKeysToAgent','no'),('EnableSSHKeysign','no'),('ConnectTimeout','8'),('ConnectionAttempts','1'),('ServerAliveInterval','5'),('ServerAliveCountMax','2'),('LogLevel','ERROR')]
 return 'Host '+ALIAS+'\n'+''.join(' '+k+' '+v+'\n'for k,v in options)
def git_env(folder,config):
 return {'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':folder,'XDG_CONFIG_HOME':folder,'LANG':'C','LC_ALL':'C','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_ALLOW_PROTOCOL':'ssh','GIT_PROTOCOL_FROM_USER':'0','GIT_TERMINAL_PROMPT':'0','GIT_ASKPASS':'/bin/false','SSH_ASKPASS':'/bin/false','SSH_ASKPASS_REQUIRE':'never','GIT_SSH_VARIANT':'ssh','GIT_SSH_COMMAND':'ssh -F '+shlex.quote(config)}
GIT=['git','-c','protocol.allow=never','-c','protocol.ssh.allow=always','-c','credential.helper=','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null']
def classify(text):
 t=text.lower()
 if 'host key verification failed'in t or 'remote host identification has changed'in t or 'no matching host key type'in t:return 'hostkey'
 if 'permission denied'in t:return 'publickey'
 if 'could not resolve hostname'in t:return 'dns'
 if 'timed out'in t:return 'timeout'
 if any(s in t for s in['connection refused','no route to host','network is unreachable','connection closed','connection reset']):return 'unreachable'
 if any(s in t for s in['not found','could not be found','does not exist','not a git repository','does not appear to be a git repository','could not read from remote repository']):return 'repository'
 return 'failed'
def run_git(folder,env,args,limit=OUTPUT,seconds=30):
 stderr=open(folder+'/stderr','w+b');expired=[]
 try:proc=subprocess.Popen(GIT+args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=stderr,env=env,cwd=folder,start_new_session=True)
 except FileNotFoundError:raise Fail('tools')from None
 def kill():
  expired.append(1)
  try:os.killpg(proc.pid,signal.SIGKILL)
  except Exception:pass
 timer=threading.Timer(seconds,kill);timer.start();out=b'';truncated=False
 try:
  while True:
   chunk=proc.stdout.read(65536)
   if not chunk:break
   out+=chunk
   if len(out)>limit:truncated=True;kill();break
  code=proc.wait()
 finally:
  timer.cancel()
  if proc.poll()is None:
   kill();proc.wait()
  proc.stdout.close()
 stderr.seek(0);text=stderr.read(20000).decode('utf-8','replace');stderr.close()
 if expired and not truncated:raise Fail('timeout')
 if code!=0 and not truncated:raise Fail(classify(text))
 return out,truncated
def parse_refs(out,limit=LIMIT):
 rows=[];head='';truncated=False
 for line in out.decode('utf-8','replace').splitlines():
  m=re.fullmatch(r'ref: refs/heads/(\S+)\tHEAD',line)
  if m:
   if BRANCH.fullmatch(m.group(1))and '..'not in m.group(1):head=m.group(1)
   continue
  m=re.fullmatch(r'([0-9a-f]{40})\t(\S+)',line)
  if not m:continue
  if len(rows)>=limit:truncated=True;break
  rows.append((m.group(2),m.group(1)))
 return rows,head,truncated
def branch_name(ref):
 name=ref[11:]if ref.startswith('refs/heads/')else''
 return name if BRANCH.fullmatch(name)and '..'not in name and name[0]not in'-/'and not name.endswith('/')else''
def ls_remote(data,args):
 target=remote(data.get('remote'));keys=pinned(data.get('hostKeys'));private=data.get('privateKey')
 if not isinstance(private,str)or not private.startswith('-----BEGIN OPENSSH PRIVATE KEY-----')or len(private)>10000:raise Fail('invalid')
 ip=addresses(target['host'],target['port'])[0]
 with tempfile.TemporaryDirectory(prefix='web-git-')as folder:
  os.chmod(folder,0o700);config=folder+'/ssh_config'
  for name,text in[('id_ed25519',private.rstrip('\n')+'\n'),('known_hosts',''.join(target['host']+' '+k['type']+' '+k['key']+'\n'for k in keys)),('ssh_config',ssh_config(folder,ip,target))]:
   pathlib.Path(folder,name).write_text(text);os.chmod(folder+'/'+name,0o600)
  return run_git(folder,git_env(folder,config),['ls-remote',*args,'--',ALIAS+':'+target['path'],*data.get('patterns',[])])
def keygen(comment):
 if not re.fullmatch(r'[A-Za-z0-9@._-]{1,64}',comment):raise Fail('keygen')
 try:
  from cryptography.hazmat.primitives import serialization as s
  from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
  key=Ed25519PrivateKey.generate();private=key.private_bytes(s.Encoding.PEM,s.PrivateFormat.OpenSSH,s.NoEncryption()).decode();public=key.public_key().public_bytes(s.Encoding.OpenSSH,s.PublicFormat.OpenSSH).decode()
 except ImportError:
  with tempfile.TemporaryDirectory(prefix='web-git-key-')as folder:
   try:subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C',comment,'-f',folder+'/k'],check=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
   except FileNotFoundError:raise Fail('tools')from None
   except Exception:raise Fail('keygen')from None
   private=pathlib.Path(folder,'k').read_text();public=pathlib.Path(folder,'k.pub').read_text()
 parts=public.split()
 if len(parts)<2 or parts[0]!='ssh-ed25519'or not private.startswith('-----BEGIN OPENSSH PRIVATE KEY-----')or host_key(parts[0],parts[1])is None:raise Fail('keygen')
 return {'privateKey':private,'publicKey':parts[0]+' '+parts[1]+' '+comment,'fingerprint':fingerprint(parts[1])}
def keyscan(data):
 target=remote(dict(data.get('remote')or{},path='scan'));ip=addresses(target['host'],target['port'])[0]
 try:out=subprocess.run(['ssh-keyscan','-T','5','-t','ed25519,ecdsa,rsa','-p',str(target['port']),ip],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15).stdout[:200000].decode('utf-8','replace')
 except FileNotFoundError:raise Fail('tools')from None
 except subprocess.TimeoutExpired:raise Fail('timeout')from None
 keys=[]
 for line in out.splitlines():
  parts=line.split()
  if len(parts)<3 or line.startswith('#'):continue
  key=host_key(parts[1],parts[2])
  if key and all(k['fingerprint']!=key['fingerprint']for k in keys):keys.append(key)
  if len(keys)>=10:break
 if not keys:raise Fail('nokeys')
 order={t:i for i,t in enumerate(TYPES)};keys.sort(key=lambda k:order[k['type']])
 return {'hostKeys':keys}
def main(data):
 action=data.get('action')
 if action=='keygen':return keygen(str(data.get('comment')or'pockethost-web-deploy'))
 if action=='keyscan':return keyscan(data)
 if action in['ls-remote','test']:
  mode=data.get('mode')if action=='ls-remote'else'test';branch=data.get('branch')or''
  if branch and(not isinstance(branch,str)or not branch_name('refs/heads/'+branch)):raise Fail('invalid')
  if mode=='heads':
   out,cut=ls_remote(dict(data,patterns=['HEAD','refs/heads/*']),['--symref']);rows,head,truncated=parse_refs(out)
   items=[{'name':branch_name(ref),'commit':sha}for ref,sha in rows if branch_name(ref)]
   return {'branches':items,'defaultBranch':head,'truncated':truncated or cut,'skipped':sum(1 for ref,sha in rows if ref!='HEAD'and not branch_name(ref))}
  if mode=='resolve':
   if not branch:raise Fail('invalid')
   out,cut=ls_remote(dict(data,patterns=['refs/heads/'+branch]),[]);rows,head,truncated=parse_refs(out)
   found=[sha for ref,sha in rows if ref=='refs/heads/'+branch]
   if not found:raise Fail('branch')
   return {'commit':found[0]}
  if mode=='test':
   out,cut=ls_remote(dict(data,patterns=['HEAD']+(['refs/heads/'+branch]if branch else[])),['--symref']);rows,head,truncated=parse_refs(out)
   found=[sha for ref,sha in rows if branch and ref=='refs/heads/'+branch]
   return {'ok':True,'defaultBranch':head,'head':next((sha for ref,sha in rows if ref=='HEAD'),''),'branchFound':bool(found)if branch else None,'commit':found[0]if found else''}
 raise Fail('invalid')
signal.signal(signal.SIGALRM,timeout);signal.alarm(45)
try:
 p=pathlib.Path(sys.argv[1]);data=json.loads(p.read_text());p.unlink()
 print(json.dumps(main(data)))
except Exception as error:
 code=error.code if isinstance(error,Fail)else 'timeout'if isinstance(error,(TimeoutError,subprocess.TimeoutExpired))else 'failed'
 print(json.dumps({'error':MESSAGES.get(code,MESSAGES['failed']),'code':code}))
finally:signal.alarm(0)
`

const COLLECTION = 'web_git_sources'
const ID = /^[a-z0-9]{15}$/
const WEBHOOK_ID = /^[A-Za-z0-9]{24}$/
const SCAN_LIFETIME = 10 * 60 * 1000
const RATE_STORE = 'web_git_source_rate'
const RATE_MAX = 6
/** Livraisons webhook acceptées par minute et par source avant ls-remote (chaque relecture bloque jusqu'à 45 s). */
const WEBHOOK_RATE_MAX = 20
/**
 * Relectures de tête (déployer, CI) par minute et par source : chacune lance ssh/git et bloque jusqu'à 45 s. Même
 * plafond pour les appels GitHub synchrones (assertWebGithubRate).
 */
const RESOLVE_RATE_MAX = 12
const BRANCHES_MAX = 5000
const RATE_WINDOW = 60 * 1000
const KEY_COMMENT = 'pockethost-web-deploy'
const FINGERPRINT = /^SHA256:[A-Za-z0-9+\/]{43}$/
const FAILED = 'Le contrôle du dépôt Git a échoué. Réessayez.'

type HostKey = { type: string; key: string; fingerprint: string; preset?: boolean }
/** Appelé dans la transaction du webhook pour chaque application concernée ; true si un déploiement est ajouté. */
export type WebGitWebhookQueue = (tx: any, app: core.Record, commit: string) => boolean

const now = () => new Date().toISOString()
const transport = (action: string, values: Record<string, unknown>): any => {
  const file = `${$app.dataDir()}/.web-git-source-${$security.randomString(24)}.json`
  try {
    $os.writeFile(file, JSON.stringify({ action, ...values }), 0o600 as any)
    const result = JSON.parse(toString($os.cmd('python3', '-c', GIT_SOURCE_READER, file).output()))
    if (!result || typeof result !== 'object') throw new Error('Invalid git response')
    return result
  } catch {
    return { error: FAILED, code: 'failed' }
  } finally {
    try {
      $os.remove(file)
    } catch {}
  }
}
/** Appel du lecteur ; une réponse {error} devient une BadRequestError au message fixe. */
export const webGitSource = (action: string, values: Record<string, unknown> = {}) => {
  const result = transport(action, values)
  if (result.error) throw new BadRequestError(`${result.error}`.slice(0, 300))
  return result
}

const parse = (url: unknown) => {
  try {
    return parseGitRemote(url)
  } catch (error) {
    throw new BadRequestError(error instanceof Error ? error.message : 'Adresse SSH du dépôt invalide.')
  }
}
const findSource = (id: string, store: any = $app): core.Record => {
  if (!ID.test(id || '')) throw new BadRequestError('Dépôt Git invalide.')
  try {
    return store.findRecordById(COLLECTION, id)
  } catch {
    throw new NotFoundError('Dépôt Git introuvable.')
  }
}
const findCanonical = (canonical: string, store: any = $app): core.Record | null => {
  try {
    return store.findFirstRecordByFilter(COLLECTION, 'canonical={:canonical}', { canonical })
  } catch {
    return null
  }
}
const remoteOf = (row: core.Record) => ({
  host: row.getString('host'),
  port: row.getInt('port') || 22,
  sshUser: row.getString('sshUser') || 'git',
  path: row.getString('path'),
})
const hostKeysOf = (row: core.Record): HostKey[] => {
  try {
    const value = JSON.parse(row.getString('hostKeys') || '[]')
    return Array.isArray(value)
      ? value.filter(
          (k: any) =>
            k &&
            (WEB_GIT_HOST_KEY_TYPES as readonly string[]).indexOf(k.type) >= 0 &&
            typeof k.key === 'string' &&
            FINGERPRINT.test(k.fingerprint)
        )
      : []
  } catch {
    return []
  }
}
const privateKeyOf = (row: core.Record) => `${decryptWebValue(row.getString('privateKeyEncrypted')).privateKey || ''}`
const hostKeysConfirmed = (row: core.Record) => !!row.getString('hostKeysConfirmedAt') && hostKeysOf(row).length > 0
/** Une source est prête quand une clé privée existe et que des clés d'hôte sont confirmées. */
export const assertWebGitSourceReady = (row: core.Record) => {
  if (!row.getString('privateKeyEncrypted')) throw new BadRequestError('Clé de déploiement absente : régénérez-la.')
  if (!hostKeysConfirmed(row)) throw new BadRequestError('Confirmez d’abord les empreintes du serveur Git.')
}
const credentials = (row: core.Record) => {
  assertWebGitSourceReady(row)
  return {
    remote: remoteOf(row),
    privateKey: privateKeyOf(row),
    hostKeys: hostKeysOf(row).map((k) => ({ type: k.type, key: k.key })),
  }
}
const origin = () => {
  const host = $os.getenv('APP_HOST') || `app.${$os.getenv('APEX_DOMAIN') || 'monappli.re'}`
  return /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(host) ? `https://${host}` : ''
}
const webhookUrl = (row: core.Record) =>
  origin() && WEBHOOK_ID.test(row.getString('webhookId'))
    ? `${origin()}/api/web-git-sources/webhook/${row.getString('webhookId')}`
    : ''

/** Applications dont la configuration référence cette source (source='git', gitSourceId). */
export const webGitSourceApps = (sourceId: string, store: any = $app): core.Record[] =>
  (store.findRecordsByFilter('web_apps', '', '', 2000, 0) as core.Record[]).filter((row) => {
    const c = webConfig(row)
    return c.source === 'git' && c.gitSourceId === sourceId
  })
/** Projet de chaque application qui utilise la source ('' : application sans projet), hors `exceptAppId`. */
export const webGitSourceAppProjects = (sourceId: string, store: any = $app, exceptAppId = ''): string[] =>
  webGitSourceApps(sourceId, store)
    .filter((row) => row.id !== exceptAppId)
    .map((row) => webMembership(row.id, store)?.getString('projectId') || '')
/**
 * Après un détachement ou une restauration de `app` : la source suit le seul projet de ses applications, ou
 * n'appartient plus à aucun projet (superAdmin seul). Plusieurs projets : rattachement inchangé, sourceAccess réserve
 * alors la source à la plateforme. `app` peut porter une configuration pas encore enregistrée (restauration).
 */
export const reconcileWebGitSourceProject = (app: core.Record, store: any = $app) => {
  const config = webConfig(app)
  if (config.source !== 'git') return
  let row: core.Record
  try {
    row = findSource(`${config.gitSourceId || ''}`, store)
  } catch {
    return
  }
  const projects = [
      ...webGitSourceAppProjects(row.id, store, app.id),
      webMembership(app.id, store)?.getString('projectId') || '',
    ].filter((id, index, all) => !!id && all.indexOf(id) === index),
    next = projects.length > 1 ? row.getString('projectId') : projects[0] || ''
  if (next === row.getString('projectId')) return
  row.set('projectId', next)
  store.save(row)
}
/** Cible RBAC d'une source : son projet (si défini) et les applications qui l'utilisent. */
export const webGitSourceTarget = (sourceId: string, store: any = $app): { projectId?: string; appIds: string[] } => {
  const row = findSource(sourceId, store),
    projectId = row.getString('projectId')
  return { ...(projectId ? { projectId } : {}), appIds: webGitSourceApps(row.id, store).map((r) => r.id) }
}
/** Champs non secrets à écrire dans web_apps.config pour rattacher une application à la source. */
export const webGitAppConfig = (sourceId: string, store: any = $app) => {
  const row = findSource(sourceId, store)
  return { source: 'git', gitSourceId: row.id, repository: webGitRepository({ canonical: row.getString('canonical') }) }
}

/** Vue publique : jamais de clé privée, de secret de webhook ni de scan en attente. */
export const serializeWebGitSource = (row: core.Record, store: any = $app) => {
  const remote = remoteOf(row),
    keys = hostKeysOf(row),
    canonical = row.getString('canonical')
  return {
    id: row.id,
    ...remote,
    canonical,
    repository: webGitRepository({ canonical }),
    url: webGitDisplayUrl(remote),
    provider: webGitProvider(remote.host),
    projectId: row.getString('projectId'),
    publicKey: row.getString('publicKey'),
    fingerprint: row.getString('fingerprint'),
    hostKeys: keys.map((k) => ({ type: k.type, fingerprint: k.fingerprint, preset: !!k.preset })),
    hostKeysPreset: keys.some((k) => !!k.preset),
    hostKeysConfirmed: hostKeysConfirmed(row),
    hostKeysConfirmedAt: row.getString('hostKeysConfirmedAt'),
    ready: !!row.getString('privateKeyEncrypted') && hostKeysConfirmed(row),
    webhook: { url: webhookUrl(row), configured: !!row.getString('webhookSecretEncrypted') },
    lastCheckAt: row.getString('lastCheckAt'),
    lastCheckStatus: row.getString('lastCheckStatus'),
    lastError: row.getString('lastError'),
    rotatedAt: row.getString('rotatedAt'),
    appCount: webGitSourceApps(row.id, store).length,
    created: row.getString('created'),
    updated: row.getString('updated'),
  }
}

const generateKey = () => {
  const r = webGitSource('keygen', { comment: KEY_COMMENT })
  let publicKey = ''
  try {
    publicKey = parseSshEd25519PublicKey(`${r.publicKey || ''}`).normalized
  } catch {}
  if (
    !publicKey ||
    publicKey.length > 200 ||
    !FINGERPRINT.test(r.fingerprint || '') ||
    typeof r.privateKey !== 'string' ||
    !r.privateKey.startsWith('-----BEGIN OPENSSH PRIVATE KEY-----')
  )
    throw new BadRequestError('La génération de la clé de déploiement a échoué.')
  return {
    publicKey,
    fingerprint: r.fingerprint as string,
    privateKeyEncrypted: encryptWebValue({ privateKey: r.privateKey }),
  }
}
const rateLimit = (
  sourceId: string,
  store = RATE_STORE,
  max = RATE_MAX,
  message = 'Trop de contrôles Git pour ce dépôt. Réessayez dans une minute.'
) => {
  $app.store().setFunc(store, (raw: unknown) => {
    const at = Date.now(),
      all: Record<string, number[]> = raw ? JSON.parse(`${raw}`) : {}
    for (const id of Object.keys(all)) {
      all[id] = (all[id] || []).filter((t) => t > at - RATE_WINDOW)
      if (!all[id]!.length) delete all[id]
    }
    const hits = all[sourceId] || []
    if (hits.length >= max) throw new TooManyRequestsError(message)
    hits.push(at)
    all[sourceId] = hits
    return JSON.stringify(all)
  })
}
export const webRateLimit = rateLimit
/**
 * Appels synchrones vers GitHub : chacun lance un python3 qui bloque jusqu'à 30 s une VM du pool de hooks. Plafond par
 * cible (application, ou projet pour une preview) ET par acteur ; l'acteur d'abord, pour qu'un compte déjà au plafond
 * n'épuise pas le quota de son application.
 */
export const assertWebGithubRate = (targetId: string, actorId: string) => {
  const message = 'Trop de demandes pour cette application. Réessayez dans une minute.'
  if (actorId) rateLimit(actorId, `${RATE_STORE}_github_actor`, RESOLVE_RATE_MAX, message)
  rateLimit(targetId, `${RATE_STORE}_github_target`, RESOLVE_RATE_MAX, message)
}
/**
 * Garde d'accès : source rattachée à un projet => rôle de projet ; source sans projet => superAdmin. Une source
 * inaccessible répond comme une source inexistante (pas d'énumération). Une application hors du projet (sans projet,
 * détachée, restaurée dans un projet de récupération) utilise encore la source : une rotation ou un nouvel épinglage la
 * casserait sans que ce projet en ait la charge, donc superAdmin seul (403).
 */
const sourceAccess = (e: core.RequestEvent, permission: WebPermission) => {
  const user = requireWebUser(e),
    row = findSource(e.request!.pathValue('sourceId')),
    projectId = row.getString('projectId')
  if (!projectId) {
    if (!isWebPlatformAdmin(user)) throw new NotFoundError('Dépôt Git introuvable.')
    return { row, user: requirePlatformAdmin(e) }
  }
  try {
    requireWebPermission(e, { projectId }, permission)
  } catch (error) {
    if (error instanceof NotFoundError) throw new NotFoundError('Dépôt Git introuvable.')
    throw error
  }
  if (!isWebPlatformAdmin(user) && webGitSourceAppProjects(row.id).some((id) => id !== projectId))
    throw new ForbiddenError(
      'Ce dépôt Git sert aussi une application hors de ce projet : seul un administrateur de la plateforme peut le gérer.'
    )
  return { row, user }
}

/** POST /api/web-git-sources {url, projectId?} — superAdmin (source.git.configure). Idempotent par dépôt (canonical). */
export const HandleWebGitSourceCreate = (e: core.RequestEvent) => {
  const actor = requirePlatformAdmin(e),
    input = webInput(e),
    remote = parse(input.url),
    projectId = typeof input.projectId === 'string' ? input.projectId : ''
  if (projectId) {
    if (!ID.test(projectId)) throw new BadRequestError('Projet invalide.')
    try {
      $app.findRecordById('web_projects', projectId)
    } catch {
      throw new NotFoundError('Projet introuvable.')
    }
  }
  const adopt = (row: core.Record) => {
    const current = row.getString('projectId')
    if (projectId && current && current !== projectId)
      throw new BadRequestError(
        'Ce dépôt Git est déjà relié à un autre projet : une clé de déploiement ne peut servir qu’un seul dépôt.'
      )
    if (projectId && !current) {
      row.set('projectId', projectId)
      $app.save(row)
    }
    return webJson(e, { source: serializeWebGitSource(row), created: false })
  }
  const existing = findCanonical(remote.canonical)
  if (existing) return adopt(existing)
  const key = generateKey(),
    preset = webGitHostKeyPreset(remote.host, remote.port),
    row = new Record($app.findCollectionByNameOrId(COLLECTION))
  row.set('host', remote.host)
  row.set('port', remote.port)
  row.set('sshUser', remote.sshUser)
  row.set('path', remote.path)
  row.set('canonical', remote.canonical)
  row.set('projectId', projectId)
  row.set('publicKey', key.publicKey)
  row.set('fingerprint', key.fingerprint)
  row.set('privateKeyEncrypted', key.privateKeyEncrypted)
  row.set(
    'hostKeys',
    preset.map((k) => ({ ...k, preset: true }))
  )
  if (preset.length) {
    row.set('hostKeysConfirmedAt', now())
    row.set('hostKeysConfirmedBy', actor.id)
  }
  row.set('webhookId', $security.randomString(24))
  row.set('actorId', actor.id)
  try {
    $app.save(row)
  } catch (error) {
    // Création concurrente du même dépôt : l'index unique sur canonical garde une seule clé.
    const again = findCanonical(remote.canonical)
    if (again) return adopt(again)
    throw error
  }
  return webJson(e, { source: serializeWebGitSource(row), created: true }, 201)
}
/** GET /api/web-git-sources/{sourceId} — lecture (app.read) : clé publique, empreintes, état. */
export const HandleWebGitSourceGet = (e: core.RequestEvent) =>
  webJson(e, { source: serializeWebGitSource(sourceAccess(e, 'app.read').row) })

/** POST .../{sourceId}/scan — admin (source.hostKey.scan). Hôte publié : vérifie la concordance ; sinon prépare un TOFU. */
export const HandleWebGitSourceScan = (e: core.RequestEvent) => {
  const { row, user } = sourceAccess(e, 'source.hostKey.scan')
  rateLimit(row.id)
  const remote = remoteOf(row),
    scanned = webGitSource('keyscan', { remote: { host: remote.host, port: remote.port } }),
    keys: HostKey[] = (Array.isArray(scanned.hostKeys) ? scanned.hostKeys : [])
      .filter(
        (k: any) =>
          k && (WEB_GIT_HOST_KEY_TYPES as readonly string[]).indexOf(k.type) >= 0 && FINGERPRINT.test(k.fingerprint)
      )
      .slice(0, 10)
      .map((k: any) => ({ type: k.type, key: `${k.key}`, fingerprint: k.fingerprint })),
    preset = webGitHostKeyPreset(remote.host, remote.port)
  if (!keys.length) throw new BadRequestError('Le serveur Git n’a présenté aucune clé d’hôte exploitable.')
  if (preset.length) {
    const published = preset.map((k) => k.fingerprint)
    if (!keys.some((k) => published.indexOf(k.fingerprint) >= 0))
      throw new BadRequestError(
        'Les clés présentées par ce serveur ne correspondent pas aux empreintes publiées par l’hébergeur. Connexion refusée.'
      )
    if (!hostKeysConfirmed(row)) {
      // Normalement épinglé à la création ; une ligne incomplète est réparée avec les seules clés publiées.
      row.set(
        'hostKeys',
        preset.map((k) => ({ ...k, preset: true }))
      )
      row.set('hostKeysConfirmedAt', now())
      row.set('hostKeysConfirmedBy', user.id)
      $app.save(row)
    }
    return webJson(e, {
      preset: true,
      matches: true,
      confirmed: true,
      hostKeys: preset.map((k) => ({ type: k.type, fingerprint: k.fingerprint })),
      source: serializeWebGitSource(row),
    })
  }
  const nonce = $security.randomString(48),
    expiresAt = new Date(Date.now() + SCAN_LIFETIME).toISOString(),
    current = webGitFingerprintSet(hostKeysOf(row).map((k) => k.fingerprint)).join(',')
  row.set(
    'pendingHostKeysEncrypted',
    encryptWebValue({ nonceHash: $security.sha256(nonce), expiresAt, keys, actorId: user.id, ...remote })
  )
  $app.save(row)
  return webJson(e, {
    preset: false,
    nonce,
    expiresAt,
    hostKeys: keys.map((k) => ({ type: k.type, fingerprint: k.fingerprint })),
    changed: !!current && current !== webGitFingerprintSet(keys.map((k) => k.fingerprint)).join(','),
  })
}
/** POST .../{sourceId}/confirm-host-keys {nonce, fingerprints[]} — admin. Les empreintes doivent égaler le dernier scan. */
export const HandleWebGitSourceConfirmHostKeys = (e: core.RequestEvent) => {
  const { row: initial, user } = sourceAccess(e, 'source.hostKey.scan'),
    input = webInput(e)
  if (typeof input.nonce !== 'string' || !/^[A-Za-z0-9]{48}$/.test(input.nonce))
    throw new BadRequestError('Ce scan est invalide ou expiré. Relancez le scan.')
  const confirmed = webGitFingerprintSet(input.fingerprints)
  let saved: core.Record = initial
  $app.runInTransaction((tx) => {
    const row = tx.findRecordById(COLLECTION, initial.id) as core.Record,
      remote = remoteOf(row)
    if (webGitHostKeyPreset(remote.host, remote.port).length)
      throw new BadRequestError('Les empreintes de cet hébergeur sont déjà épinglées.')
    const pending = row.getString('pendingHostKeysEncrypted')
      ? decryptWebValue(row.getString('pendingHostKeysEncrypted'))
      : {}
    if (
      !pending.nonceHash ||
      !$security.equal(pending.nonceHash, $security.sha256(input.nonce)) ||
      !(Date.parse(pending.expiresAt) > Date.now()) ||
      pending.host !== remote.host ||
      pending.port !== remote.port
    )
      throw new BadRequestError('Ce scan est invalide ou expiré. Relancez le scan.')
    const keys: HostKey[] = Array.isArray(pending.keys) ? pending.keys : []
    if (!confirmed.length || confirmed.join(',') !== webGitFingerprintSet(keys.map((k) => k.fingerprint)).join(','))
      throw new BadRequestError('Les empreintes confirmées ne correspondent pas au dernier scan. Relancez le scan.')
    row.set(
      'hostKeys',
      keys.map((k) => ({ type: k.type, key: k.key, fingerprint: k.fingerprint, preset: false }))
    )
    row.set('hostKeysConfirmedAt', now())
    row.set('hostKeysConfirmedBy', user.id)
    row.set('pendingHostKeysEncrypted', '')
    row.set('lastCheckStatus', '')
    row.set('lastError', '')
    tx.save(row)
    saved = row
  })
  return webJson(e, { source: serializeWebGitSource(saved) })
}
const recordCheck = (id: string, status: string, error: string) => {
  try {
    const row = $app.findRecordById(COLLECTION, id)
    row.set('lastCheckAt', now())
    row.set('lastCheckStatus', status.slice(0, 30))
    row.set('lastError', error.slice(0, 500))
    $app.save(row)
    return row
  } catch {
    return null
  }
}
/** POST .../{sourceId}/test {branch?} — admin. Résultat {ok, error?, code?} en 200 : un refus est un résultat. */
export const HandleWebGitSourceTest = (e: core.RequestEvent) => {
  const { row } = sourceAccess(e, 'app.configure'),
    input = webInput(e),
    branch = typeof input.branch === 'string' ? input.branch : ''
  if (branch && !webGitBranchValid(branch)) throw new BadRequestError('Branche invalide.')
  rateLimit(row.id)
  const r = transport('test', { ...credentials(row), branch }),
    error = r.error ? `${r.error}`.slice(0, 300) : '',
    code = error ? `${r.code || 'failed'}` : branch && !r.branchFound ? 'branch' : 'ok',
    updated = recordCheck(row.id, code, error || (code === 'branch' ? 'Branche introuvable dans ce dépôt.' : '')) || row
  return webJson(e, {
    ok: !error,
    ...(error ? { error, code } : {}),
    defaultBranch: error ? '' : `${r.defaultBranch || ''}`,
    branchFound: error || !branch ? null : !!r.branchFound,
    commit: error ? '' : `${r.commit || ''}`,
    source: serializeWebGitSource(updated),
  })
}
/** GET .../{sourceId}/branches?page&perPage&search — admin. Même forme que WebBranchPage (+ defaultBranch, truncated). */
export const HandleWebGitSourceBranches = (e: core.RequestEvent) => {
  const { row } = sourceAccess(e, 'app.configure'),
    query = e.request!.url!.query(),
    page = Math.max(1, Math.min(1000, Number(query.get('page')) || 1)),
    perPage = Math.max(1, Math.min(BRANCHES_MAX, Number(query.get('perPage')) || 50)),
    search = (query.get('search') || '').trim().toLowerCase().slice(0, 100)
  rateLimit(row.id)
  const r = webGitSource('ls-remote', { ...credentials(row), mode: 'heads' }),
    all = (Array.isArray(r.branches) ? r.branches : []).filter(
      (b: any) => webGitBranchValid(b?.name) && /^[a-f0-9]{40}$/.test(b?.commit || '')
    ) as { name: string; commit: string }[],
    defaultBranch = webGitBranchValid(r.defaultBranch) ? r.defaultBranch : '',
    matches = all
      .filter((b) => !search || b.name.toLowerCase().indexOf(search) >= 0)
      .sort((a, b) => (a.name === defaultBranch ? -1 : b.name === defaultBranch ? 1 : a.name < b.name ? -1 : 1))
  return webJson(e, {
    items: matches.slice((page - 1) * perPage, page * perPage),
    page,
    perPage,
    totalItems: matches.length,
    hasMore: page * perPage < matches.length,
    defaultBranch,
    truncated: !!r.truncated,
  })
}
/**
 * Tête de branche (40 hex) par ls-remote. Pour queueWebDeployment, le webhook et la vérification CI. Débit limité par
 * source (429) ; le webhook a son propre plafond et passe `limited = false`.
 */
export const resolveWebGitCommit = (
  source: core.Record | string,
  branch: string,
  store: any = $app,
  limited = true
) => {
  const row = typeof source === 'string' ? findSource(source, store) : source
  if (!webGitBranchValid(branch)) throw new BadRequestError('Branche invalide.')
  const access = credentials(row)
  if (limited) rateLimit(row.id, `${RATE_STORE}_resolve`, RESOLVE_RATE_MAX)
  const r = webGitSource('ls-remote', { ...access, mode: 'resolve', branch })
  if (!/^[a-f0-9]{40}$/.test(r.commit || '')) throw new BadRequestError('Commit Git invalide.')
  return r.commit as string
}
/** Identifiants du job worker (claim) : job.git. Lit la ligne ACTUELLE (une rotation s'applique aux jobs en file). */
export const webGitJobCredentials = (source: core.Record | string, commit = '', branch = '', store: any = $app) => {
  const row = typeof source === 'string' ? findSource(source, store) : source,
    c = credentials(row)
  return {
    host: c.remote.host,
    port: c.remote.port,
    sshUser: c.remote.sshUser,
    path: c.remote.path,
    privateKey: c.privateKey,
    hostKeys: c.hostKeys,
    commit,
    branch,
  }
}
/** POST .../{sourceId}/rotate-key {confirm?} — admin (source.deployKey.rotate). confirm:true requis si utilisée. */
export const HandleWebGitSourceRotateKey = (e: core.RequestEvent) => {
  const { row } = sourceAccess(e, 'source.deployKey.rotate'),
    input = webInput(e),
    appCount = webGitSourceApps(row.id).length
  if (appCount && input.confirm !== true)
    throw new BadRequestError(
      `Cette clé sert à ${appCount} application${appCount > 1 ? 's' : ''}. Confirmez la rotation : ajoutez la nouvelle clé publique au dépôt avant le prochain déploiement.`
    )
  rateLimit(row.id)
  const key = generateKey()
  row.set('publicKey', key.publicKey)
  row.set('fingerprint', key.fingerprint)
  row.set('privateKeyEncrypted', key.privateKeyEncrypted)
  row.set('rotatedAt', now())
  row.set('lastCheckStatus', '')
  row.set('lastError', '')
  $app.save(row)
  return webJson(e, {
    publicKey: key.publicKey,
    fingerprint: key.fingerprint,
    appCount,
    notice: 'Ajoutez la nouvelle clé au dépôt, puis supprimez l’ancienne chez l’hébergeur.',
    source: serializeWebGitSource(row),
  })
}
/** POST .../{sourceId}/rotate-webhook-secret {disable?} — admin (source.deployKey.rotate). Secret affiché une fois. */
export const HandleWebGitSourceRotateWebhookSecret = (e: core.RequestEvent) => {
  const { row } = sourceAccess(e, 'source.deployKey.rotate'),
    input = webInput(e)
  if (input.disable === true) {
    row.set('webhookSecretEncrypted', '')
    $app.save(row)
    return webJson(e, { source: serializeWebGitSource(row) })
  }
  rateLimit(row.id)
  const secret = $security.randomString(48)
  if (!WEBHOOK_ID.test(row.getString('webhookId'))) row.set('webhookId', $security.randomString(24))
  row.set('webhookSecretEncrypted', encryptWebValue({ secret }))
  $app.save(row)
  return webJson(e, { secret, url: webhookUrl(row), source: serializeWebGitSource(row) })
}
/** DELETE /api/web-git-sources/{sourceId} — superAdmin (source.git.configure). Refusé tant qu'une application l'utilise. */
export const HandleWebGitSourceDelete = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  const row = findSource(e.request!.pathValue('sourceId')),
    appCount = webGitSourceApps(row.id).length
  if (appCount)
    throw new BadRequestError(
      `Ce dépôt Git est encore utilisé par ${appCount} application${appCount > 1 ? 's' : ''}. Détachez-les avant de supprimer la clé.`
    )
  $app.delete(row)
  return webJson(e, { deleted: true, notice: 'Supprimez aussi la clé de déploiement chez l’hébergeur du dépôt.' })
}
const ACTIONS: Record<string, (e: core.RequestEvent) => any> = {
  scan: HandleWebGitSourceScan,
  'confirm-host-keys': HandleWebGitSourceConfirmHostKeys,
  test: HandleWebGitSourceTest,
  'rotate-key': HandleWebGitSourceRotateKey,
  'rotate-webhook-secret': HandleWebGitSourceRotateWebhookSecret,
}
/**
 * POST /api/web-git-sources/{sourceId}/{action}. Une seule route générique : des routes littérales `/{sourceId}/scan`
 * entreraient en conflit (mux Go) avec `/webhook/{webhookId}`.
 */
export const HandleWebGitSourceAction = (e: core.RequestEvent) => {
  const handler = ACTIONS[e.request!.pathValue('action')]
  if (!handler || !Object.prototype.hasOwnProperty.call(ACTIONS, e.request!.pathValue('action')))
    throw new NotFoundError('Action inconnue.')
  return handler(e)
}

const header = (e: core.RequestEvent, name: string) => e.request!.header.get(name) || ''
const signed = (e: core.RequestEvent, raw: string, secret: string) => {
  const hex = $security.hs256(raw, secret)
  // GitHub, Gitea/Forgejo récents et Bitbucket Cloud : « sha256=<hex> ».
  for (const name of ['X-Hub-Signature-256', 'X-Hub-Signature']) {
    const value = header(e, name)
    if (value && $security.equal('sha256=' + hex, value.toLowerCase())) return true
  }
  for (const name of ['X-Gitea-Signature', 'X-Forgejo-Signature', 'X-Gogs-Signature']) {
    const value = header(e, name)
    if (value && $security.equal(hex, value.toLowerCase())) return true
  }
  const token = header(e, 'X-Gitlab-Token')
  return !!token && $security.equal(secret, token)
}
const pushOf = (body: any) => {
  if (body?.push && Array.isArray(body.push.changes)) {
    // Bitbucket Cloud : première modification de branche (les autres branches du même lot sont ignorées).
    const change = body.push.changes.slice(0, 20).find((c: any) => c?.new?.type === 'branch')
    return change
      ? { branch: `${change.new.name || ''}`, after: `${change.new.target?.hash || ''}`, deleted: false }
      : { branch: '', after: '', deleted: body.push.changes.some((c: any) => c && c.closed) }
  }
  const ref = typeof body?.ref === 'string' && body.ref.length <= 211 ? body.ref : '',
    after = typeof body?.after === 'string' ? body.after : ''
  return {
    branch: ref.startsWith('refs/heads/') ? ref.slice(11) : '',
    after,
    deleted: body?.deleted === true || /^0{40}$/.test(after),
  }
}
/**
 * Traitement d'un webhook signé : vérifie la signature, déduplique, ignore tags/suppressions/autres branches, relit la
 * tête par ls-remote hors transaction puis enregistre la livraison. Retourne la liste {appId, commit} à déployer ;
 * `queue` (fourni par api.ts au câblage) ajoute réellement chaque déploiement dans la même transaction.
 */
export const webGitWebhookDeliver = (e: core.RequestEvent, queue?: WebGitWebhookQueue) => {
  if (!webEnabled()) throw new NotFoundError()
  const hookId = e.request!.pathValue('webhookId')
  if (!WEBHOOK_ID.test(hookId)) throw new NotFoundError()
  let source: core.Record
  try {
    source = $app.findFirstRecordByFilter(COLLECTION, 'webhookId={:id}', { id: hookId })
  } catch {
    throw new NotFoundError()
  }
  const encrypted = source.getString('webhookSecretEncrypted'),
    secret = encrypted ? `${decryptWebValue(encrypted).secret || ''}` : '',
    raw = readerToString(e.request!.body)
  if (secret.length < 32 || raw.length > 10000000 || !signed(e, raw, secret))
    throw new UnauthorizedError('Signature Git invalide.')
  let body: any
  try {
    body = JSON.parse(raw.startsWith('payload=') ? decodeURIComponent(raw.slice(8).replace(/\+/g, ' ')) : raw)
  } catch {
    throw new BadRequestError('Charge utile JSON attendue.')
  }
  const event = (
      header(e, 'X-GitHub-Event') ||
      header(e, 'X-Gitea-Event') ||
      header(e, 'X-Forgejo-Event') ||
      header(e, 'X-Gogs-Event') ||
      header(e, 'X-Gitlab-Event') ||
      header(e, 'X-Event-Key')
    ).slice(0, 60),
    // Les en-têtes d'identifiant de livraison ne sont pas signés : la déduplication porte sur le corps signé (une
    // relivraison légitime renvoie le même corps ; deux pushs distincts ont toujours des corps différents). L'id de
    // source (et non webhookId, qui fait partie de l'URL) : la liste des livraisons est lisible par les lecteurs.
    delivery = `git-${source.id}-${$security.sha256(raw)}`,
    push = ['push', 'Push Hook', 'repo:push'].indexOf(event) >= 0,
    change = pushOf(body),
    branch = webGitBranchValid(change.branch) ? change.branch : '',
    repository = webGitRepository({ canonical: source.getString('canonical') }).slice(0, 250),
    ignored = !push || change.deleted || !branch
  if (!ignored && !/^[a-f0-9]{40}$/.test(change.after)) throw new BadRequestError('Commit invalide.')
  const bump = (tx: any) => {
    let row: core.Record | undefined
    try {
      row = tx.findFirstRecordByFilter('web_webhook_deliveries', 'deliveryId={:id}', { id: delivery })
    } catch {}
    if (!row) return false
    row.set('duplicateCount', Math.min(100000, row.getInt('duplicateCount') + 1))
    row.set('lastReceivedAt', now())
    tx.save(row)
    return true
  }
  let duplicate = false
  $app.runInTransaction((tx) => {
    duplicate = bump(tx)
  })
  if (duplicate) return { status: 200, body: { accepted: true, duplicate: true }, deploy: [] as any[] }
  const matches = (row: core.Record) => {
    const c = webConfig(row)
    return c.autoDeploy && c.branch === branch && webSourceBuildSupported(row)
  }
  const matching = ignored ? [] : webGitSourceApps(source.id).filter(matches)
  // Plafond par source avant toute relecture ls-remote (jeton GitLab statique ou corps signé rejoué en boucle).
  if (matching.length) rateLimit(source.id, `${RATE_STORE}_webhook`, WEBHOOK_RATE_MAX)
  // Relecture hors transaction : une livraison tardive ne peut pas publier une révision plus ancienne.
  const head = matching.length ? resolveWebGitCommit(source, branch, $app, false) : ignored ? '' : change.after
  const deploy: { appId: string; commit: string }[] = []
  let queued = 0
  $app.runInTransaction((tx) => {
    if (bump(tx)) {
      duplicate = true
      return
    }
    const seen = new Record(tx.findCollectionByNameOrId('web_webhook_deliveries'))
    seen.set('deliveryId', delivery)
    seen.set('event', event)
    seen.set('repository', repository)
    seen.set('branch', branch)
    seen.set('payloadCommit', /^[a-f0-9]{40}$/.test(change.after) ? change.after : '')
    seen.set('resolvedCommit', head)
    seen.set('lastReceivedAt', now())
    if (!ignored)
      for (const row of webGitSourceApps(source.id, tx).filter(matches)) {
        deploy.push({ appId: row.id, commit: head })
        if (queue && queue(tx, row, head)) queued++
      }
    seen.set('queued', queued)
    seen.set('status', queued ? 'queued' : deploy.length && !queue ? 'planned' : 'ignored')
    seen.set(
      'note',
      queued
        ? 'Déploiement ajouté à la file.'
        : ignored
          ? change.deleted
            ? 'Suppression de branche : aucun déploiement.'
            : 'Événement sans déploiement.'
          : !deploy.length
            ? 'Aucune application automatique ne correspond à ce dépôt et à cette branche.'
            : queue
              ? 'Cette version est déjà publiée ou en cours de déploiement.'
              : 'Déploiement à planifier.'
    )
    tx.save(seen)
  })
  if (duplicate) return { status: 200, body: { accepted: true, duplicate: true }, deploy: [] as any[] }
  return { status: queued ? 202 : 200, body: { accepted: true, queued }, deploy }
}
/**
 * POST /api/web-git-sources/webhook/{webhookId} (sans authentification, corps ≤ 10 Mo). Aucun identifiant interne
 * renvoyé.
 */
export const HandleWebGitSourceWebhook = (e: core.RequestEvent, queue?: WebGitWebhookQueue) => {
  const result = webGitWebhookDeliver(e, queue)
  return webJson(e, result.body, result.status)
}
