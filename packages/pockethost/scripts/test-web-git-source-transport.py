#!/usr/bin/env python3
"""Deploy-key git transport (GIT_SOURCE_READER): real keygen, mocked DNS/ssh, local bare repo, no network."""
import builtins,io,json,os,pathlib,re,shlex,signal,socket,stat,subprocess,sys,tempfile,unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1]
FILE=ROOT/'src/mothership-app/src/lib/handlers/webApps/gitSource.ts'
SCRIPT=re.search(r'GIT_SOURCE_READER = String.raw`(.*?)`',FILE.read_text(),re.S).group(1)
PRESETS=ROOT/'src/common/webGit.ts'
PUBLIC=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('140.82.112.3',22))]
ED='AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl'
SHA='a'*40
REAL_POPEN=subprocess.Popen
def real(args):
 proc=REAL_POPEN(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,stdin=subprocess.DEVNULL,text=True);out,_=proc.communicate(timeout=10);return proc.returncode,out
def addr(ip,port=22):return (socket.AF_INET6 if ':'in ip else socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,port)+((0,0)if ':'in ip else()))
class FakePopen:
 """Records the git invocation and snapshots the private folder while it exists."""
 calls=[];stdout=b'';stderr=b'';code=0;raise_on_read=None
 def __init__(self,args,stdin=None,stdout=None,stderr=None,env=None,cwd=None,start_new_session=False):
  folder=pathlib.Path(cwd);snapshot={n:(folder/n).read_text()for n in['ssh_config','known_hosts','id_ed25519']if(folder/n).exists()}
  modes={n:stat.S_IMODE((folder/n).stat().st_mode)for n in snapshot};modes['.']=stat.S_IMODE(folder.stat().st_mode)
  parsed=real(['ssh','-G','-F',str(folder/'ssh_config'),'pockethost-git']);known=real(['ssh-keygen','-F','github.com','-f',str(folder/'known_hosts')])
  FakePopen.calls.append({'args':args,'env':env,'cwd':cwd,'session':start_new_session,'files':snapshot,'modes':modes,'ssh_G':parsed[1],'ssh_G_code':parsed[0],'known':known[0]})
  if stderr is not None:stderr.write(FakePopen.stderr);stderr.flush()
  # Never a real process group: the reader's killpg on truncation must not reach the test runner.
  self.pid=2**30;self.stdout=Reader(FakePopen.stdout,FakePopen.raise_on_read);self.returncode=None
 def wait(self,timeout=None):self.returncode=FakePopen.code;return FakePopen.code
 def poll(self):return self.returncode
class Reader(io.BytesIO):
 def __init__(self,data,error):super().__init__(data);self.error=error
 def read(self,n=-1):
  if self.error:raise self.error
  return super().read(n)
class GitSourceTransportTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory(prefix='ph-git-transport-');cls.folder=pathlib.Path(cls.tmp.name)
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C','fixture','-f',str(cls.folder/'fixture')],check=True)
  cls.private=(cls.folder/'fixture').read_text()
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def setUp(self):FakePopen.calls=[];FakePopen.stdout=b'';FakePopen.stderr=b'';FakePopen.code=0;FakePopen.raise_on_read=None
 def payload(self,**extra):return {'remote':{'host':'github.com','port':22,'sshUser':'git','path':'o/r.git'},'privateKey':self.private,'hostKeys':[{'type':'ssh-ed25519','key':ED}],**extra}
 def run_reader(self,action,dns=PUBLIC,popen=FakePopen,run=None,namespace=None,**extra):
  file=self.folder/'payload.json';file.write_text(json.dumps({'action':action,**extra}));file.chmod(0o600)
  output=io.StringIO();before=sys.argv;sys.argv=['reader',str(file)];g={'__name__':'__main__'}
  def resolve(host,port,*a,**k):
   if isinstance(dns,Exception):raise dns
   return dns
  patches=[mock.patch.object(socket,'getaddrinfo',side_effect=resolve),mock.patch.object(subprocess,'Popen',popen),mock.patch('sys.stdout',output)]
  if run is not None:patches.append(mock.patch.object(subprocess,'run',side_effect=run))
  try:
   for p in patches:p.start()
   exec(compile(SCRIPT,'reader','exec'),g)
  finally:
   for p in reversed(patches):p.stop()
   sys.argv=before;signal.alarm(0)
  self.assertFalse(file.exists());text=output.getvalue();self.assertTrue(action=='keygen'or 'PRIVATE KEY'not in text);self.assertNotIn('SECRET-STDERR',text)
  if namespace is not None:namespace.update(g)
  return json.loads(text)
 # keygen
 def check_key(self,value):
  self.assertEqual(set(value),{'privateKey','publicKey','fingerprint'})
  parts=value['publicKey'].split();self.assertEqual(parts[0],'ssh-ed25519');self.assertEqual(parts[2],'pockethost-web-deploy');self.assertTrue(value['privateKey'].startswith('-----BEGIN OPENSSH PRIVATE KEY-----'))
  pub=self.folder/'gen.pub';pub.write_text(value['publicKey']+'\n')
  listed=subprocess.run(['ssh-keygen','-lf',str(pub),'-E','sha256'],capture_output=True,text=True,check=True).stdout.split()
  self.assertEqual(listed[1],value['fingerprint']);self.assertRegex(value['fingerprint'],r'^SHA256:[A-Za-z0-9+/]{43}$')
  key=self.folder/'gen';key.write_text(value['privateKey']);key.chmod(0o600)
  derived=subprocess.run(['ssh-keygen','-y','-f',str(key)],capture_output=True,text=True,check=True).stdout.split()
  self.assertEqual(derived[:2],parts[:2])
 def test_keygen_with_cryptography_matches_ssh_keygen_fingerprint(self):
  self.check_key(self.run_reader('keygen',comment='pockethost-web-deploy'))
 def test_keygen_falls_back_to_ssh_keygen(self):
  real=builtins.__import__
  def blocked(name,*args,**kwargs):
   if name.startswith('cryptography'):raise ImportError(name)
   return real(name,*args,**kwargs)
  with mock.patch.object(builtins,'__import__',side_effect=blocked):value=self.run_reader('keygen',popen=REAL_POPEN,comment='pockethost-web-deploy')
  self.check_key(value)
 def test_keygen_rejects_unsafe_comment(self):
  self.assertEqual(self.run_reader('keygen',comment='x -f /etc/passwd')['code'],'keygen')
 # SSRF and input validation
 def test_keyscan_refuses_non_public_addresses_before_any_process(self):
  def never(*a,**k):raise AssertionError('process started')
  for ip in ['127.0.0.1','10.1.2.3','172.16.0.1','192.168.1.1','169.254.169.254','100.64.0.1','0.0.0.0','224.0.0.1','255.255.255.255','::1','fe80::1%eth0','fc00::1','::ffff:127.0.0.1','::ffff:10.0.0.1','64:ff9b::a00:1','64:ff9b:1::1','2002:7f00:1::1','2001:0:4136:e378:8000:63bf:3fff:fdd2']:
   value=self.run_reader('keyscan',dns=[addr(ip)],run=never,popen=never,remote={'host':'git.example.com','port':22});self.assertEqual(value['code'],'ssrf',ip);self.assertIn('serveurs publics',value['error'])
  value=self.run_reader('keyscan',dns=[addr('140.82.112.3'),addr('10.0.0.1')],run=never,remote={'host':'git.example.com','port':22});self.assertEqual(value['code'],'ssrf')
  self.assertEqual(self.run_reader('keyscan',dns=socket.gaierror('nope'),run=never,remote={'host':'git.example.com','port':22})['code'],'dns')
 def test_reader_revalidates_remote(self):
  def never(*a,**k):raise AssertionError('process started')
  bad=[{'host':'-oProxyCommand.example.com'},{'host':'localhost'},{'host':'127.0.0.1'},{'host':'git.local'},{'host':'GitHub.com'},{'port':0},{'port':65536},{'port':'22'},{'port':True},{'sshUser':'-oX'},{'path':'../etc/passwd'},{'path':'o/../r'},{'path':'-u/r'},{'path':'//r'},{'path':'o r'},{'path':'ext::sh'},{'path':''}]
  for change in bad:
   remote=dict(self.payload()['remote'],**change);value=self.run_reader('ls-remote',run=never,popen=never,**self.payload(remote=remote,mode='heads'));self.assertEqual(value['code'],'invalid',change)
  self.assertEqual(self.run_reader('ls-remote',popen=never,**self.payload(mode='resolve',branch='a..b'))['code'],'invalid')
  self.assertEqual(self.run_reader('ls-remote',popen=never,**self.payload(mode='resolve',branch='-x'))['code'],'invalid')
  self.assertEqual(self.run_reader('ls-remote',popen=never,**self.payload(mode='heads',privateKey='not a key'))['code'],'invalid')
  self.assertEqual(self.run_reader('ls-remote',popen=never,**self.payload(mode='heads',hostKeys=[]))['code'],'unpinned')
  self.assertEqual(self.run_reader('ls-remote',popen=never,**self.payload(mode='heads',hostKeys=[{'type':'ssh-rsa','key':ED}]))['code'],'unpinned')
  self.assertEqual(self.run_reader('nope')['code'],'invalid')
 def test_keyscan_uses_vetted_ip_and_parses_keys(self):
  seen=[];rsa='AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk='
  def run(cmd,**kwargs):
   seen.append(cmd);out=f'# 140.82.112.3:2222 SSH-2.0-x\n[140.82.112.3]:2222 ssh-rsa {rsa}\n[140.82.112.3]:2222 ssh-ed25519 {ED}\n[140.82.112.3]:2222 ssh-ed25519 {ED}\nx ssh-dss AAAAB3NzaC1kc3M=\nx ssh-ed25519 not-base64!\nx ssh-rsa {ED}\n'
   return subprocess.CompletedProcess(cmd,0,out.encode(),b'SECRET-STDERR')
  value=self.run_reader('keyscan',dns=[addr('2606:50c0:8000::153',2222),addr('140.82.112.3',2222)],run=run,remote={'host':'git.example.com','port':2222})
  self.assertEqual(seen,[['ssh-keyscan','-T','5','-t','ed25519,ecdsa,rsa','-p','2222','140.82.112.3']])
  self.assertEqual([k['type']for k in value['hostKeys']],['ssh-ed25519','ssh-rsa'])
  self.assertEqual(value['hostKeys'][0]['fingerprint'],'SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU');self.assertEqual(value['hostKeys'][1]['fingerprint'],'SHA256:uNiVztksCsDhcc0u9e8BujQXVUpKZIDTMczCvj3tD2s')
  self.assertEqual(self.run_reader('keyscan',run=lambda cmd,**k:subprocess.CompletedProcess(cmd,1,b'',b''),remote={'host':'git.example.com','port':22})['code'],'nokeys')
  def missing(cmd,**k):raise FileNotFoundError(cmd[0])
  self.assertEqual(self.run_reader('keyscan',run=missing,remote={'host':'git.example.com','port':22})['code'],'tools')
 # hardened ssh/git invocation
 def test_exact_hardened_ssh_and_git_invocation(self):
  FakePopen.stdout=('ref: refs/heads/main\tHEAD\n'+SHA+'\tHEAD\n'+SHA+'\trefs/heads/main\n').encode()
  value=self.run_reader('ls-remote',dns=[addr('2606:50c0:8000::153'),addr('140.82.112.3')],**self.payload(mode='heads'))
  self.assertEqual(value['branches'],[{'name':'main','commit':SHA}]);self.assertEqual(value['defaultBranch'],'main')
  call=FakePopen.calls[0];args=call['args'];folder=call['cwd']
  self.assertEqual(args,['git','-c','protocol.allow=never','-c','protocol.ssh.allow=always','-c','credential.helper=','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null','ls-remote','--symref','--','pockethost-git:o/r.git','HEAD','refs/heads/*'])
  self.assertTrue(call['session']);self.assertFalse(pathlib.Path(folder).exists())
  self.assertEqual(call['env'],{'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':folder,'XDG_CONFIG_HOME':folder,'LANG':'C','LC_ALL':'C','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_ALLOW_PROTOCOL':'ssh','GIT_PROTOCOL_FROM_USER':'0','GIT_TERMINAL_PROMPT':'0','GIT_ASKPASS':'/bin/false','SSH_ASKPASS':'/bin/false','SSH_ASKPASS_REQUIRE':'never','GIT_SSH_VARIANT':'ssh','GIT_SSH_COMMAND':'ssh -F '+shlex.quote(folder+'/ssh_config')})
  self.assertEqual(call['modes'],{'.':0o700,'ssh_config':0o600,'known_hosts':0o600,'id_ed25519':0o600})
  self.assertEqual(call['files']['id_ed25519'],self.private);self.assertEqual(call['files']['known_hosts'],'github.com ssh-ed25519 '+ED+'\n');self.assertEqual(call['known'],0)
  lines=call['files']['ssh_config'].splitlines();self.assertEqual(lines[0],'Host pockethost-git')
  options=dict(line.strip().split(' ',1)for line in lines[1:])
  self.assertEqual(options,{'HostName':'140.82.112.3','Port':'22','User':'git','HostKeyAlias':'github.com','IdentityFile':f'"{folder}/id_ed25519"','IdentitiesOnly':'yes','IdentityAgent':'none','UserKnownHostsFile':f'"{folder}/known_hosts"','GlobalKnownHostsFile':'/dev/null','StrictHostKeyChecking':'yes','UpdateHostKeys':'no','CheckHostIP':'no','VerifyHostKeyDNS':'no','BatchMode':'yes','PasswordAuthentication':'no','KbdInteractiveAuthentication':'no','HostbasedAuthentication':'no','PubkeyAuthentication':'yes','PreferredAuthentications':'publickey','ProxyCommand':'none','ProxyJump':'none','ForwardAgent':'no','ForwardX11':'no','ForwardX11Trusted':'no','ClearAllForwardings':'yes','PermitLocalCommand':'no','RequestTTY':'no','Tunnel':'no','ControlMaster':'no','ControlPath':'none','AddKeysToAgent':'no','EnableSSHKeysign':'no','ConnectTimeout':'8','ConnectionAttempts':'1','ServerAliveInterval':'5','ServerAliveCountMax':'2','LogLevel':'ERROR'})
  # ssh itself accepts the file and resolves the alias to the vetted address only.
  self.assertEqual(call['ssh_G_code'],0);g=dict(l.split(' ',1)for l in call['ssh_G'].splitlines()if ' 'in l)
  self.assertEqual(g['hostname'],'140.82.112.3');self.assertEqual(g['hostkeyalias'],'github.com');self.assertEqual(g['stricthostkeychecking'],'true');self.assertEqual(g['batchmode'],'yes');self.assertEqual(g['identitiesonly'],'yes');self.assertEqual(g['forwardagent'],'no');self.assertEqual(g['permitlocalcommand'],'no');self.assertNotIn('proxycommand',g);self.assertNotIn('proxyjump',g);self.assertEqual(g['globalknownhostsfile'],'/dev/null')
 def test_port_user_and_absolute_path_reach_ssh_config_and_url(self):
  FakePopen.stdout=(SHA+'\trefs/heads/dev\n').encode()
  value=self.run_reader('ls-remote',dns=[addr('140.82.112.3',2222)],**self.payload(remote={'host':'code.example.org','port':2222,'sshUser':'forgejo','path':'/srv/git/app.git'},mode='resolve',branch='dev'))
  self.assertEqual(value,{'commit':SHA});call=FakePopen.calls[0]
  self.assertEqual(call['args'][-3:],['--','pockethost-git:/srv/git/app.git','refs/heads/dev'])
  self.assertIn(' Port 2222\n',call['files']['ssh_config']);self.assertIn(' User forgejo\n',call['files']['ssh_config']);self.assertIn(' HostKeyAlias code.example.org\n',call['files']['ssh_config'])
  self.assertEqual(call['files']['known_hosts'],'code.example.org ssh-ed25519 '+ED+'\n')
 # ls-remote parsing
 def test_heads_parsing_validation_and_cap(self):
  rows=['ref: refs/heads/main\tHEAD',SHA+'\tHEAD',SHA+'\trefs/heads/main',SHA+'\trefs/heads/a..b',SHA+'\trefs/heads/-x','b'*64+'\trefs/heads/sha256',SHA+'\trefs/tags/v1','junk line']+[f'{SHA}\trefs/heads/b{i:05}'for i in range(6000)]
  FakePopen.stdout='\n'.join(rows).encode();value=self.run_reader('ls-remote',**self.payload(mode='heads'))
  self.assertTrue(value['truncated']);self.assertEqual(value['defaultBranch'],'main');self.assertEqual(value['skipped'],3)
  names=[b['name']for b in value['branches']];self.assertEqual(names[0],'main');self.assertNotIn('a..b',names);self.assertNotIn('-x',names);self.assertNotIn('sha256',names);self.assertEqual(len(names),5000-5+1)
  FakePopen.stdout=('x'*4100000).encode();self.assertTrue(self.run_reader('ls-remote',**self.payload(mode='heads'))['truncated'])
 def test_resolve_requires_exact_branch_and_test_reports_default_branch(self):
  FakePopen.stdout=(('c'*40)+'\trefs/heads/x/main\n').encode();self.assertEqual(self.run_reader('ls-remote',**self.payload(mode='resolve',branch='main'))['code'],'branch')
  FakePopen.stdout=(SHA+'\trefs/heads/main\n').encode();self.assertEqual(self.run_reader('ls-remote',**self.payload(mode='resolve',branch='main')),{'commit':SHA})
  self.assertEqual(FakePopen.calls[-1]['args'][-4:],['ls-remote','--','pockethost-git:o/r.git','refs/heads/main'])
  FakePopen.stdout=('ref: refs/heads/trunk\tHEAD\n'+'d'*40+'\tHEAD\n'+SHA+'\trefs/heads/main\n').encode()
  self.assertEqual(self.run_reader('test',**self.payload(branch='main')),{'ok':True,'defaultBranch':'trunk','head':'d'*40,'branchFound':True,'commit':SHA})
  self.assertEqual(FakePopen.calls[-1]['args'][-6:],['ls-remote','--symref','--','pockethost-git:o/r.git','HEAD','refs/heads/main'])
  FakePopen.stdout=b'';self.assertEqual(self.run_reader('test',**self.payload()),{'ok':True,'defaultBranch':'','head':'','branchFound':None,'commit':''})
 # fixed error mapping
 def test_errors_are_fixed_messages_without_stderr(self):
  cases={'git@github.com: Permission denied (publickey).':'publickey','Host key verification failed.':'hostkey','@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@':'hostkey','ERROR: Repository not found.':'repository','remote: The project you were looking for could not be found':'repository','ssh: connect to host x port 22: Connection refused':'unreachable','ssh: connect to host x port 22: Operation timed out':'timeout','fatal: something odd':'failed'}
  for text,code in cases.items():
   FakePopen.stderr=(text+'\nSECRET-STDERR\n').encode();FakePopen.code=128
   value=self.run_reader('test',**self.payload());self.assertEqual(value['code'],code,text);self.assertNotIn('SECRET',value['error']);self.assertNotIn(text,value['error'])
  FakePopen.stderr=b'';FakePopen.code=0;FakePopen.raise_on_read=TimeoutError()
  self.assertEqual(self.run_reader('test',**self.payload())['code'],'timeout')
  def missing(*a,**k):raise FileNotFoundError('git')
  value=self.run_reader('test',popen=missing,**self.payload());self.assertEqual(value['code'],'tools');self.assertIn('openssh-client',value['error'])
 # presets
 def test_presets_match_ssh_keygen_and_reader_fingerprint(self):
  text=PRESETS.read_text();g={};self.run_reader('nope',namespace=g)
  entries=re.findall(r"type: '([a-z0-9-]+)',\s*key: '([A-Za-z0-9+/=]+)',\s*fingerprint: '(SHA256:[A-Za-z0-9+/]+)'",text);self.assertEqual(len(entries),9)
  hosts=re.findall(r"'([a-z.]+\.(?:com|org))': \[",text);self.assertEqual(hosts,['github.com','gitlab.com','bitbucket.org'])
  known=self.folder/'presets';known.write_text(''.join(f'host {t} {k}\n'for t,k,_ in entries))
  listed=[l.split()[1]for l in subprocess.run(['ssh-keygen','-lf',str(known),'-E','sha256'],capture_output=True,text=True,check=True).stdout.splitlines()]
  self.assertEqual(listed,[f for _,_,f in entries])
  for t,k,f in entries:self.assertEqual(g['host_key'](t,k)['fingerprint'],f)
  self.assertIn('SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU',listed);self.assertIn('SHA256:eUXGGm1YGsMAS7vkcx6JOJdOGHPem5gQp4taiCfCLB8',listed);self.assertIn('SHA256:ybgmFkzwOSotHTHLJgHO0QN8L0xErw6vd0VhFA9m3SM',listed)
 # end to end against a local bare repository through a fake ssh
 def test_end_to_end_ls_remote_with_local_upload_pack(self):
  repo=self.folder/'e2e';(repo/'srv').mkdir(parents=True,exist_ok=True);work=repo/'work'
  git=lambda *a,cwd=None:subprocess.run(['git',*a],cwd=cwd,check=True,capture_output=True,text=True).stdout.strip()
  if not (repo/'srv/o/r.git').exists():
   git('init','-q','-b','trunk',str(work));(work/'f').write_text('x');git('-c','user.email=a@b','-c','user.name=a','add','f',cwd=work);git('-c','user.email=a@b','-c','user.name=a','commit','-qm','x',cwd=work);git('branch','feature/y',cwd=work)
   (repo/'srv/o').mkdir(parents=True);git('clone','-q','--bare',str(work),str(repo/'srv/o/r.git'))
  head=git('rev-parse','HEAD',cwd=work);log=repo/'ssh.log';fake=repo/'fake-ssh'
  fake.write_text('#!/usr/bin/env python3\nimport os,shlex,subprocess,sys\nopen(%r,"a").write(repr(sys.argv[1:])+"\\n")\nassert sys.argv[1]=="-F" and "pockethost-git" in sys.argv\ncmd=shlex.split(sys.argv[-1])\nassert cmd[0]=="git-upload-pack"\nsys.exit(subprocess.call(["git","upload-pack",cmd[1]],cwd=%r))\n'%(str(log),str(repo/'srv')));fake.chmod(0o700)
  def popen(args,**kwargs):
   env=dict(kwargs['env']);self.assertTrue(env['GIT_SSH_COMMAND'].startswith('ssh -F '));env['GIT_SSH_COMMAND']=env['GIT_SSH_COMMAND'].replace('ssh -F',str(fake)+' -F',1);env['PATH']=os.environ['PATH'];kwargs['env']=env
   return REAL_POPEN(args,**kwargs)
  value=self.run_reader('ls-remote',popen=popen,**self.payload(mode='heads'))
  self.assertEqual(value['defaultBranch'],'trunk');self.assertEqual(sorted(b['name']for b in value['branches']),['feature/y','trunk']);self.assertTrue(all(b['commit']==head for b in value['branches']))
  self.assertEqual(self.run_reader('ls-remote',popen=popen,**self.payload(mode='resolve',branch='feature/y')),{'commit':head})
  self.assertEqual(self.run_reader('test',popen=popen,**self.payload(branch='nope'))['branchFound'],False)
  self.assertIn("'pockethost-git'",log.read_text())
if __name__=='__main__':unittest.main()
