#!/usr/bin/env python3
"""Offline end-to-end tests for the deploy-key Git source: a fake `ssh` on PATH execs git-upload-pack locally."""
import unittest,tempfile,pathlib,importlib.util,json,unittest.mock as mock,subprocess,sys,os,shutil,socket,stat,time
HERE=pathlib.Path(__file__).parent;sys.path.insert(0,str(HERE))
import web_app_git as g
import web_app_runtime as runtime
spec=importlib.util.spec_from_file_location('worker',HERE/'web-app-worker.py');w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
GIT=['git','-c','user.name=t','-c','user.email=t@example.invalid','-c','init.defaultBranch=main','-c','protocol.file.allow=always']
HOSTKEY={'type':'ssh-ed25519','key':'AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl'}
FAKE_SSH=r'''#!%(python)s
import json,os,shlex,stat,sys,pathlib
argv=sys.argv[1:];i=0;config=None
while i<len(argv):
 a=argv[i]
 if a in('-F','-o','-p','-l','-i'):
  if a=='-F':config=argv[i+1]
  i+=2;continue
 if a.startswith('-'):i+=1;continue
 break
host=argv[i];command=shlex.split(' '.join(argv[i+1:]))
mode=json.loads(pathlib.Path(%(control)r).read_text())
cfg=pathlib.Path(config);folder=cfg.parent
text=cfg.read_text();identity=[l.split(None,1)[1] for l in text.splitlines() if l.strip().startswith('IdentityFile ')][0].strip()
record={'argv':sys.argv[1:],'host':host,'command':command,'config':text,'known':(folder/'known_hosts').read_text(),'idMode':oct(os.stat(identity).st_mode&0o777),'dirMode':oct(os.stat(folder).st_mode&0o777),
 'env':{k:v for k,v in os.environ.items() if k in('HOME','SSH_AUTH_SOCK','GIT_CONFIG_GLOBAL','GIT_CONFIG_NOSYSTEM','GIT_ALLOW_PROTOCOL','GIT_TERMINAL_PROMPT')}}
with open(%(record)r,'a') as f:f.write(json.dumps(record)+'\n')
if mode.get('leak'):sys.stderr.write(pathlib.Path(identity).read_text());sys.stderr.flush()
if mode.get('v0'):os.environ.pop('GIT_PROTOCOL',None)
if command[0]!='git-upload-pack' or len(command)!=2:sys.exit(2)
os.execvp('git-upload-pack',['git-upload-pack',os.path.join(%(server)r,command[1].lstrip('/'))])
'''

def git(*args,cwd=None):return subprocess.run([*GIT,*args],cwd=cwd,check=True,capture_output=True,text=True).stdout.strip()

@unittest.skipUnless(shutil.which('git') and shutil.which('git-upload-pack'),'git requis')
class GitFetchTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.base=pathlib.Path(self.tmp.name).resolve()
  self.server=self.base/'server';(self.server/'owner').mkdir(parents=True);self.bin=self.base/'bin';self.bin.mkdir()
  self.control=self.base/'control.json';self.record=self.base/'record.jsonl';self.mode({})
  ssh=self.bin/'ssh';ssh.write_text(FAKE_SSH%{'python':sys.executable,'control':str(self.control),'record':str(self.record),'server':str(self.server)});ssh.chmod(0o755)
  self.env=mock.patch.dict(os.environ,{'PATH':str(self.bin)+os.pathsep+os.environ.get('PATH','')});self.env.start()
  self.dns=mock.patch.object(g.socket,'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('140.82.112.3',22))]);self.dns.start()
  self.reserve=mock.patch.dict(g.LIMITS,{'reserveBytes':0});self.reserve.start()
  self.key=self.private_key();self.commits=self.repository()
  self.work=self.base/'work';self.work.mkdir();(self.base/'root').mkdir();self.rootpatch=mock.patch.object(w,'ROOT',self.base/'root');self.rootpatch.start()
 def tearDown(self):self.rootpatch.stop();self.reserve.stop();self.dns.stop();self.env.stop();self.tmp.cleanup()
 def mode(self,value):self.control.write_text(json.dumps(value))
 def records(self):return [json.loads(l) for l in self.record.read_text().splitlines()] if self.record.exists() else []
 def private_key(self):
  if shutil.which('ssh-keygen'):
   subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C','test','-f',str(self.base/'k')],check=True,capture_output=True);return (self.base/'k').read_text()
  return '-----BEGIN OPENSSH PRIVATE KEY-----\n'+'b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n'*3+'-----END OPENSSH PRIVATE KEY-----\n'
 def repository(self,extra=None):
  src=self.base/'src';shutil.rmtree(src,ignore_errors=True);src.mkdir();git('init','-q',cwd=src)
  (src/'package.json').write_text('{"name":"site"}');(src/'Dockerfile').write_text('FROM scratch\n');git('add','.',cwd=src);git('commit','-qm','one',cwd=src);first=git('rev-parse','HEAD',cwd=src)
  (src/'app').mkdir();(src/'app'/'index.js').write_text('ok');os.symlink('app/index.js',src/'inside-link')
  (src/'.gitmodules').write_text('[submodule "sub"]\n\tpath = vendor/sub\n\turl = ssh://evil.example/x\n')
  if extra:extra(src)
  git('add','.',cwd=src);git('update-index','--add','--cacheinfo','160000,'+first+',vendor/sub',cwd=src);git('commit','-qm','two',cwd=src);second=git('rev-parse','HEAD',cwd=src)
  bare=self.server/'owner'/'repo.git';shutil.rmtree(bare,ignore_errors=True);git('clone','-q','--bare',str(src),str(bare))
  return [first,second]
 def source(self,commit=None,**extra):return {'host':'git.example.com','port':22,'sshUser':'git','path':'owner/repo.git','privateKey':self.key,'hostKeys':[HOSTKEY],'commit':commit or self.commits[-1],'branch':'main',**extra}
 def leftovers(self):return list(self.work.glob('git-ssh-*'))
 def fetch(self,**kwargs):
  logs=[];kw={'log':logs.append,**kwargs};source=kw.pop('source',None) or self.source()
  try:return g.fetch_git(source,self.work,**kw),''.join(logs)
  finally:self.assertEqual(self.leftovers(),[],'le dossier de clé temporaire doit toujours être supprimé')

 def test_fetch_by_sha_checks_out_exact_commit_without_git_metadata_or_submodules(self):
  src,log=self.fetch()
  self.assertEqual((src/'app'/'index.js').read_text(),'ok');self.assertTrue((src/'inside-link').is_symlink())
  self.assertFalse((src/'.git').exists());self.assertEqual(list((src/'vendor'/'sub').iterdir())if(src/'vendor'/'sub').is_dir()else[],[]);self.assertIn('Sous-modules ignorés',log)
  [record]=self.records();self.assertEqual(record['host'],g.ALIAS);self.assertEqual(record['command'],['git-upload-pack','owner/repo.git'])
  self.assertEqual(record['idMode'],'0o600');self.assertEqual(record['dirMode'],'0o700');self.assertEqual(record['known'],'git.example.com ssh-ed25519 '+HOSTKEY['key']+'\n')
  for line in [' HostName 140.82.112.3',' HostKeyAlias git.example.com',' StrictHostKeyChecking yes',' IdentitiesOnly yes',' IdentityAgent none',' BatchMode yes',' ProxyCommand none',' ProxyJump none',' PermitLocalCommand no',' ForwardAgent no',' ForwardX11 no',' ClearAllForwardings yes',' ConnectTimeout 8',' GlobalKnownHostsFile /dev/null',' UpdateHostKeys no']:self.assertIn(line+'\n',record['config'])
  self.assertEqual(record['argv'][:2][0],'-F');self.assertNotIn('SSH_AUTH_SOCK',record['env']);self.assertEqual(record['env']['GIT_CONFIG_GLOBAL'],'/dev/null');self.assertEqual(record['env']['GIT_ALLOW_PROTOCOL'],'ssh');self.assertTrue(record['env']['HOME'].startswith(str(self.work/'git-ssh-')))
  self.assertNotIn('evil',json.dumps(self.records()))
 def test_server_refusing_sha_wants_falls_back_to_branch_and_verifies_presence(self):
  self.mode({'v0':True});src,log=self.fetch(source=self.source(self.commits[0]))
  self.assertIn('repli sur la branche main',log);self.assertTrue((src/'package.json').is_file());self.assertFalse((src/'app').exists());self.assertEqual(len(self.records()),2)
 def test_commit_absent_or_beyond_fallback_depth_is_refused(self):
  with self.assertRaisesRegex(RuntimeError,'introuvable'):self.fetch(source=self.source('d'*40))
  self.assertFalse((self.work/'source'/'app').exists())
  shutil.rmtree(self.work/'source');self.mode({'v0':True})
  with mock.patch.object(g,'FALLBACK_DEPTH',1),self.assertRaisesRegex(RuntimeError,'introuvable'):self.fetch(source=self.source(self.commits[0]))
 def test_no_branch_and_refused_sha_fails_clearly(self):
  self.mode({'v0':True})
  with self.assertRaisesRegex(RuntimeError,'aucune branche'):self.fetch(source=self.source(self.commits[0],branch=''))
 def test_symlink_escaping_the_checkout_is_refused(self):
  self.commits=self.repository(lambda src:os.symlink('../../../../etc/passwd',src/'app'/'evil'))
  with self.assertRaisesRegex(RuntimeError,'Lien source hors du projet'):self.fetch()
  self.commits=self.repository(lambda src:os.symlink('/etc',src/'abs'));shutil.rmtree(self.work/'source')
  with self.assertRaisesRegex(RuntimeError,'Lien source hors du projet'):self.fetch()
 def test_entry_size_and_pack_limits_are_enforced(self):
  with self.assertRaisesRegex(RuntimeError,'trop volumineux'):self.fetch(limits={'entries':2})
  self.assertFalse((self.work/'source'/'package.json').exists())
  shutil.rmtree(self.work/'source')
  with self.assertRaisesRegex(RuntimeError,'trop volumineux'):self.fetch(limits={'bytes':10})
  shutil.rmtree(self.work/'source');self.commits=self.repository(lambda src:(src/'blob.bin').write_bytes(os.urandom(300000)))
  # Over-size pack: index-pack hits RLIMIT_FSIZE; reported as too big, no branch fallback (a single ssh connection).
  self.record.unlink(missing_ok=True)
  with self.assertRaisesRegex(RuntimeError,'256 Mio compressés'):self.fetch(limits={'packBytes':100000})
  self.assertEqual(len(self.records()),1)
  shutil.rmtree(self.work/'source');src,_=self.fetch(limits={'packBytes':4*1024**2});self.assertEqual((src/'blob.bin').stat().st_size,300000)
 def test_tree_bomb_is_cut_while_streaming_ls_tree(self):
  # 4 levels of 1000 entries sharing one subtree: 10^12 logical entries in a pack of a few KiB.
  bare=self.server/'owner'/'repo.git';shutil.rmtree(bare);git('init','-q','--bare',str(bare))
  run=lambda args,data:subprocess.run([*GIT,'-C',str(bare),*args],input=data,check=True,capture_output=True,text=True).stdout.strip()
  obj=run(['hash-object','-w','--stdin'],'x');kind='100644 blob'
  for level in range(4):obj=run(['mktree'],''.join(f'{kind} {obj}\tn{i}\n'for i in range(1000)));kind='040000 tree'
  commit=run(['commit-tree',obj,'-m','bomb'],'');run(['update-ref','refs/heads/main',commit],'')
  started=time.monotonic()
  with self.assertRaisesRegex(RuntimeError,'100 000 fichiers'):self.fetch(source=self.source(commit))
  self.assertLess(time.monotonic()-started,30);self.assertEqual([p.name for p in (self.work/'source').iterdir()],['.git'])
  env={'PATH':os.environ['PATH']}
  self.assertEqual(g.tree_stats(['printf','100644 blob '+'a'*40+' 7\\tok\\0'],env,g.LIMITS),(7,1,0))
  with self.assertRaises(g.GitCommandFailed):g.tree_stats(['printf','garbage\\0'],env,g.LIMITS)
 def test_disk_reserve_is_checked_before_checkout(self):
  with mock.patch.dict(g.LIMITS,{'reserveBytes':10**18}),self.assertRaisesRegex(RuntimeError,'réserve de 15 Gio'):self.fetch()
  self.assertFalse((self.work/'source'/'package.json').exists())
 def test_validation_refuses_injection_and_unpinned_hosts(self):
  base=self.source()
  for change in [{'host':'-oProxyCommand=x'},{'host':'a..b'},{'host':'ext::sh'},{'host':'git.example.com\n'},{'port':0},{'port':70000},{'port':'22'},{'port':True},{'sshUser':'-l'},{'sshUser':'a b'},
                 {'path':'../x'},{'path':'owner/../x'},{'path':'-x/y'},{'path':'a//b'},{'path':'a/b c'},{'path':'a:b'},{'path':'a/\x00'},{'branch':'-x'},{'branch':'a..b'},{'branch':'a@{1}'},{'branch':'x.lock'},
                 {'commit':'C'*40},{'commit':'c'*39},{'privateKey':'nope'},{'privateKey':None},{'hostKeys':[]},{'hostKeys':[{'type':'ssh-dss','key':HOSTKEY['key']}]},{'hostKeys':[{'type':'ssh-ed25519','key':'a b'}]}]:
   with self.subTest(change=change),self.assertRaises(RuntimeError):g.validate({**base,**change})
  self.assertEqual(g.validate({**base,'host':'Git.Example.com','path':'/srv/git/x.git','port':2222})['host'],'git.example.com')
  with self.assertRaisesRegex(RuntimeError,'Clé de déploiement'):self.fetch(source={**base,'privateKey':''})
 def test_vetted_address_refuses_non_public_answers(self):
  self.dns.stop()
  try:
   for address in ['127.0.0.1','10.0.0.1','192.168.1.1','169.254.169.254','100.64.0.1','0.0.0.0','::1','fe80::1','fc00::1','::ffff:127.0.0.1','::ffff:8.8.8.8','64:ff9b::a00:1','64:ff9b::808:808','2002:c0a8:101::1','2001::1','224.0.0.1']:
    family=socket.AF_INET6 if ':' in address else socket.AF_INET
    with self.subTest(address=address),mock.patch.object(g.socket,'getaddrinfo',return_value=[(family,socket.SOCK_STREAM,6,'',(address,22))]),self.assertRaisesRegex(RuntimeError,'adresses publiques'):g.vetted_address('git.example.com',22)
   with mock.patch.object(g.socket,'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',22)),(socket.AF_INET,socket.SOCK_STREAM,6,'',('10.0.0.1',22))]),self.assertRaises(RuntimeError):g.vetted_address('git.example.com',22)
   for literal in ['127.0.0.1','::1','10.1.2.3']:
    with self.subTest(literal=literal),self.assertRaises(RuntimeError):g.vetted_address(literal,22)
   with mock.patch.object(g.socket,'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',22))]):self.assertEqual(g.vetted_address('git.example.com',22),'8.8.8.8')
   self.assertEqual(g.vetted_address('8.8.4.4',22),'8.8.4.4')
  finally:self.dns.start()
 @unittest.skipUnless(os.path.exists('/usr/bin/ssh'),'OpenSSH requis')
 def test_generated_ssh_config_is_accepted_by_openssh(self):
  folder=pathlib.Path(tempfile.mkdtemp(dir=self.work))
  try:
   _,_,config=g.ssh_files(folder,g.validate({**self.source(),'port':2222}),'140.82.112.3')
   output=subprocess.run(['/usr/bin/ssh','-F',str(config),'-G',g.ALIAS],capture_output=True,text=True,check=True).stdout.lower()
   for line in ['hostname 140.82.112.3','port 2222','user git','hostkeyalias git.example.com','stricthostkeychecking true','identitiesonly yes','batchmode yes','forwardagent no','forwardx11 no','clearallforwardings yes','permitlocalcommand no','connecttimeout 8','userknownhostsfile '+str(folder/'known_hosts').lower(),'globalknownhostsfile /dev/null']:
    self.assertIn(line+'\n',output)
   self.assertFalse([l for l in output.splitlines() if l.startswith(('proxycommand ','proxyjump ','localcommand ','remoteforward ','localforward ','dynamicforward '))]);self.assertEqual(stat.S_IMODE(folder.stat().st_mode),0o700)
  finally:shutil.rmtree(folder)

 # Worker integration: Job.fetch_source dispatch, Job.run heartbeats, log masking.
 def job(self,source='git',leak=False):
  self.mode({'leak':leak});root=self.base/'root'
  value={'id':'a'*15,'appId':'b'*15,'action':'deploy','commit':self.commits[-1],'snapshot':{'config':{'repository':'git:git.example.com/owner/repo','rootDirectory':'.','generatedDomain':'web.example.re','healthPath':'/',**({'source':source}if source else{})},'env':{},'domains':[]},
         **({'git':self.source()}if source=='git'else{'github':{}})}
  return w.Job(value),root
 def worker(self,root):
  return [mock.patch.object(w,'api',return_value={}),mock.patch.object(w,'publish_runtime_stats'),mock.patch.object(w,'admit_resources',return_value={})]
 def test_worker_fetches_git_source_through_job_run_and_masks_the_private_key(self):
  patches=self.worker(self.base/'root')
  for p in patches:p.start()
  try:
   j,root=self.job(leak=True)
   with mock.patch.object(j,'fetch')as github:src=j.fetch_source(self.work)
   github.assert_not_called();self.assertEqual((src/'app'/'index.js').read_text(),'ok');self.assertFalse((src/'.git').exists());self.assertEqual(self.leftovers(),[])
   text=j.logpath.read_text();body=[l for l in self.key.splitlines() if not l.startswith('-----')]
   self.assertIn('[masqué]',text);self.assertNotIn('PRIVATE KEY-----\n'+body[0],text)
   for line in body:self.assertNotIn(line,text)
  finally:
   for p in reversed(patches):p.stop()
 def test_worker_failure_removes_key_dir_and_reports_french_error(self):
  patches=self.worker(self.base/'root')
  for p in patches:p.start()
  try:
   j,root=self.job();j.value['commit']='e'*40;j.value['git']['commit']='e'*40
   with self.assertRaisesRegex(RuntimeError,'introuvable'):j.fetch_source(self.work)
   self.assertEqual(self.leftovers(),[]);self.assertNotIn(self.key.splitlines()[1],j.logpath.read_text())
   j,root=self.job();j.value['git']['commit']='f'*40
   with self.assertRaisesRegex(RuntimeError,'incohérent'):j.fetch_source(self.work)
   j,root=self.job();j.value.pop('git')
   with self.assertRaisesRegex(RuntimeError,'Identifiants du dépôt Git absents'):j.fetch_source(self.work)
   j,root=self.job()
   with mock.patch.object(g.shutil,'which',return_value=None),self.assertRaisesRegex(RuntimeError,'Git et OpenSSH'):j.fetch_source(self.work)
  finally:
   for p in reversed(patches):p.stop()
 def test_github_path_is_unchanged_when_source_is_not_git(self):
  for source in [None,'github']:
   j,root=self.job(source=source)
   with self.subTest(source=source),mock.patch.object(j,'fetch',return_value='ctx')as github,mock.patch.object(j,'fetch_git')as deploy_key:
    self.assertEqual(j.fetch_source(self.work),'ctx');github.assert_called_once_with(self.work);deploy_key.assert_not_called()
  self.assertEqual(self.records(),[])
 def test_job_masks_git_private_key_lines(self):
  j,_=self.job()
  self.assertIn(self.key,j.secrets)
  for line in self.key.splitlines()[1:-1]:
   if len(line)>=20:self.assertIn(line,j.secrets)

class RuntimeContextTests(unittest.TestCase):
 def test_dockerfile_and_compose_contexts_exclude_git_metadata(self):
  with tempfile.TemporaryDirectory() as tmp:
   context=pathlib.Path(tmp).resolve();(context/'Dockerfile').write_text('FROM scratch\n');(context/'.dockerignore').write_text('node_modules\n!.git\n')
   runtime.exclude_vcs(context,context/'Dockerfile');runtime.exclude_vcs(context,context/'Dockerfile')
   text=(context/'.dockerignore').read_text();self.assertTrue(text.startswith('node_modules\n!.git\n'));self.assertTrue(text.endswith('\n.git\n**/.git\n'));self.assertEqual(text.count('**/.git'),1)
   (context/'web').mkdir();(context/'web'/'Dockerfile').write_text('FROM scratch\n');(context/'web'/'Dockerfile.dockerignore').write_text('!.git')
   runtime.exclude_vcs(context,context/'web'/'Dockerfile');self.assertTrue((context/'web'/'Dockerfile.dockerignore').read_text().endswith('\n.git\n**/.git\n'))
   other=pathlib.Path(tmp+'-x');other.mkdir()
   try:
    (context/'.dockerignore').unlink();os.symlink(other/'ignore',context/'.dockerignore')
    with self.assertRaises(RuntimeError):runtime.exclude_vcs(context,context/'Dockerfile')
   finally:shutil.rmtree(other)
  with tempfile.TemporaryDirectory() as tmp:
   context=pathlib.Path(tmp).resolve();(context/'Dockerfile').write_text('FROM scratch\n');runtime.exclude_vcs(context,context/'Dockerfile')
   self.assertEqual((context/'.dockerignore').read_text(),'\n.git\n**/.git\n');self.assertFalse((context/'Dockerfile.dockerignore').exists())

if __name__=='__main__':unittest.main()
