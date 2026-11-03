#!/usr/bin/env python3
"""No remote writes: fixed Docker exec contract, redaction, control lease, real Node watchdog."""
import importlib.util,io,json,os,pathlib,secrets,shutil,socket,struct,subprocess,sys,tempfile,time,unittest,unittest.mock as mock
spec=importlib.util.spec_from_file_location('web_app_tasks',pathlib.Path(__file__).with_name('web_app_tasks.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class DummySocket:
 def shutdown(self,*args):pass
class DummyConnection:
 def close(self):pass
class Response(io.BytesIO):
 status=200
class FakeDocker:
 def __init__(self,frames=None,code=0):self.frames=frames or[b'hello private-across-',b'chunks\n'];self.code=code;self.cleanups=[];self.creates=[]
 def inspect(self,job):return'f'*64
 def create(self,*args):self.creates.append(args);return'e'*64
 def attach(self,ident):return DummyConnection(),Response(b''.join(bytes([1,0,0,0])+struct.pack('>I',len(frame))+frame for frame in self.frames)),DummySocket()
 def request(self,*args):return{'Running':False,'ExitCode':self.code}
 def cleanup(self,*args):self.cleanups.append(args)
class TaskTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);self.worker=m.TaskWorker('http://127.0.0.1:8091','s'*40,'/run/user/1001/docker.sock',self.root);self.job={'id':'a'*15,'taskId':'b'*15,'appId':'c'*15,'deploymentId':'d'*15,'containerName':'ph-web-'+('c'*15)+'-'+('d'*15),'proof':'P'*80,'command':'node scripts/clean.js','timeoutSeconds':3,'secrets':['private-across-chunks']};self.worker.engine=FakeDocker();self.calls=[];self.worker.api=self.api
 def tearDown(self):self.tmp.cleanup()
 def api(self,path,body):self.calls.append((path,body));return{'valid':True}
 def report(self):return next(body for path,body in self.calls if path=='report')
 def test_constructor_rejects_remote_or_redirect_capable_controls(self):
  for url in ['https://api.example.com','http://localhost:8091','http://127.0.0.1:8091/path','http://user:pass@127.0.0.1:8091','http://127.0.0.1:8091?token=x']:
   with self.assertRaises(ValueError):m.TaskWorker(url,'s'*40,'socket',self.root)
  with self.assertRaises(ValueError):m.TaskWorker('http://127.0.0.1','short','socket',self.root)
 def test_redacts_split_utf8_secrets_and_bounds_last_bytes(self):
  redactor=m.Redactor(['secret-multiline\n$VALUE','é-secret']);redactor.feed(b'before secret-multiline\n$');redactor.feed(b'VALUE after \xc3');redactor.feed(b'\xa9-secret');self.assertEqual(redactor.finish(),'before [masqué] after [masqué]')
  redactor=m.Redactor([]);redactor.feed(b'a'*100000);self.assertEqual(len(redactor.finish()),m.MAX_LOG);self.assertTrue(redactor.truncated)
 def test_fixed_exec_is_uid_10001_no_host_shell_or_mount_and_container_deadline(self):
  engine=m.DockerTasks('/run/user/1001/docker.sock');calls=[]
  engine.request=lambda method,path,body=None:(calls.append((method,path,body))or{'Id':'e'*64});marker='a'*64;self.assertEqual(engine.create('f'*64,marker,900,'echo $SECRET'),'e'*64)
  body=calls[0][2];self.assertEqual(body['User'],'10001:10001');self.assertFalse(body['Privileged']);self.assertFalse(body['Tty']);self.assertFalse(body['AttachStdin']);self.assertNotIn('Mounts',body);self.assertIn('NODE_OPTIONS=',body['Env']);self.assertIn('LD_PRELOAD=',body['Env']);self.assertEqual(body['Cmd'][:5],['/usr/bin/timeout','--signal=TERM','--kill-after=2s','905s','/usr/local/bin/node']);self.assertEqual(body['Cmd'][-4:],[marker,'900','echo $SECRET','{}']);self.assertIn("cp.spawn(shell,shellArgs",body['Cmd'][6]);self.assertIn('setTimeout(()=>finish(124)',body['Cmd'][6]);self.assertIn('PH_WEB_TASK_RUN=',m.CLEANUP)
 def test_inspection_refuses_unpublished_privileged_other_uid_and_host_bind(self):
  engine=m.DockerTasks('/run/user/1001/docker.sock');valid={'Id':'f'*64,'Name':'/'+self.job['containerName'],'State':{'Running':True},'Config':{'User':'10001:10001','Labels':{'pockethost.web':'true','pockethost.web.app':'c'*15,'pockethost.web.deployment':'d'*15}},'HostConfig':{'ReadonlyRootfs':True,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges']},'Mounts':[]};engine.request=lambda *args:valid;self.assertEqual(engine.inspect(self.job),'f'*64)
  for field,value in [('User','root'),('User','0:0')]:
   with mock.patch.dict(valid['Config'],{field:value}):
    with self.assertRaises(RuntimeError):engine.inspect(self.job)
  with mock.patch.dict(valid['HostConfig'],{'Privileged':True}):
   with self.assertRaises(RuntimeError):engine.inspect(self.job)
  valid['Mounts']=[{'Type':'bind','Source':'/var/run/docker.sock'}]
  with self.assertRaises(RuntimeError):engine.inspect(self.job)
 def test_portable_tools_are_exact_readonly_and_keep_uid_deadline_and_neutral_loader_env(self):
  from web_app_runtime import tooling_volume_name,tooling_node_prefix
  image='sha256:'+'e'*64;volume=tooling_volume_name(image);engine=m.DockerTasks('/run/user/1001/docker.sock');calls=[]
  valid={'Id':'f'*64,'Name':'/'+self.job['containerName'],'State':{'Running':True},'Config':{'User':'10001:10001','Env':['NODE_OPTIONS=--require /evil'],'Labels':{'pockethost.web':'true','pockethost.web.app':'c'*15,'pockethost.web.deployment':'d'*15,'pockethost.web.tooling':volume}},'HostConfig':{'ReadonlyRootfs':True,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges']},'Mounts':[{'Type':'volume','Name':volume,'Destination':'/__pockethost_tools','RW':False}]}
  def request(method,path,body=None):
   if method=='GET':return valid
   calls.append(body);return{'Id':'e'*64}
  engine.request=request
  with mock.patch.dict(os.environ,{'WEB_APPS_STORAGE_HELPER_IMAGE':image}):engine.inspect(self.job)
  engine.create('f'*64,'a'*64,3,'echo hi');engine.cleanup('f'*64,'a'*64)
  self.assertEqual(calls[0]['Cmd'][:4],tooling_node_prefix());self.assertEqual(calls[1]['Cmd'][:4],tooling_node_prefix());self.assertEqual(calls[1]['Cmd'][-2:],['a'*64,'']);self.assertEqual(calls[0]['User'],'10001:10001');self.assertIn('NODE_OPTIONS=',calls[0]['Env'])
  valid['Mounts'][0]['RW']=True
  with mock.patch.dict(os.environ,{'WEB_APPS_STORAGE_HELPER_IMAGE':image}):
   with self.assertRaises(RuntimeError):engine.inspect(self.job)

 def test_private_database_readiness_mounts_match_exact_published_app_and_deployment(self):
  import copy
  root='/home/pockethost-web/data';home=root+'/private-db-runtime/'+self.job['appId']+'/'+self.job['deploymentId']+'-web'
  valid={'Id':'f'*64,'Name':'/'+self.job['containerName'],'State':{'Running':True},'Config':{'User':'10001:10001','Labels':{'pockethost.web':'true','pockethost.web.app':self.job['appId'],'pockethost.web.deployment':self.job['deploymentId'],'pockethost.web.private-db.wait':'true'}},'HostConfig':{'ReadonlyRootfs':True,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges']},'Mounts':[
   {'Type':'bind','Source':home+'/ready','Destination':'/run/kolab-ready','RW':False},
   {'Type':'bind','Source':home+'/wait-env','Destination':'/run/kolab-wait-env','RW':False},
   {'Type':'bind','Source':'/opt/pockethost-web/web_private_db_wait.cjs','Destination':'/run/kolab-wait.cjs','RW':False}]}
  engine=m.DockerTasks('/run/user/1001/docker.sock');engine.request=lambda *args:valid
  with mock.patch.dict(os.environ,{'WEB_APPS_ROOT':root}):self.assertEqual(engine.inspect(self.job),'f'*64)
  cases=[]
  for index,field,value in [(0,'RW',True),(0,'RW',None),(0,'Source',home.replace(self.job['appId'],'z'*15)+'/ready'),(1,'Source',home.replace(self.job['deploymentId'],'z'*15)+'/wait-env'),(2,'Source','/etc/passwd'),(0,'Destination','/run/kolab-ready/subdir')]:
   bad=copy.deepcopy(valid);bad['Mounts'][index][field]=value;cases.append(bad)
  bad=copy.deepcopy(valid);bad['Mounts'].pop();cases.append(bad)
  bad=copy.deepcopy(valid);bad['Mounts'][1]=copy.deepcopy(bad['Mounts'][0]);cases.append(bad)
  bad=copy.deepcopy(valid);bad['Config']['Labels'].pop('pockethost.web.private-db.wait');cases.append(bad)
  bad=copy.deepcopy(valid);bad['HostConfig']['Binds']=['/etc:/host:ro'];cases.append(bad)
  for mount in [{'Type':'bind','Source':'/etc','Destination':'/host','RW':False},{'Type':'volume','Name':'ph-web-next-'+self.job['appId']+'-'+self.job['deploymentId'],'Destination':'/run/kolab-ready/injected'},{'Type':'tmpfs','Destination':'/run'}]:
   bad=copy.deepcopy(valid);bad['Mounts'].append(mount);cases.append(bad)
  for bad in cases:
   engine.request=lambda *args:bad
   with mock.patch.dict(os.environ,{'WEB_APPS_ROOT':root}),self.assertRaises(RuntimeError):engine.inspect(self.job)
  engine.request=lambda *args:valid
  for root in ['relative','/home/pockethost-web/../data','/home/pockethost-web/data/']:
   with mock.patch.dict(os.environ,{'WEB_APPS_ROOT':root}),self.assertRaises(RuntimeError):engine.inspect(self.job)
  # Compose fronts keep the published container name but use their own service directory.
  valid['Config']['Labels']['pockethost.web.service']='app'
  for index in [0,1]:valid['Mounts'][index]['Source']=valid['Mounts'][index]['Source'].replace(self.job['deploymentId']+'-web/',self.job['deploymentId']+'-app/')
  with mock.patch.dict(os.environ,{'WEB_APPS_ROOT':'/home/pockethost-web/data'}):self.assertEqual(engine.inspect(self.job),'f'*64)
  for service in ['web','../app','app/../../foreign','App','a'*32,'']:
   with mock.patch.dict(valid['Config']['Labels'],{'pockethost.web.service':service}),mock.patch.dict(os.environ,{'WEB_APPS_ROOT':'/home/pockethost-web/data'}),self.assertRaises(RuntimeError):engine.inspect(self.job)

 def test_exact_persistent_next_cache_matches_existing_storage_mount_policy(self):
  engine=m.DockerTasks('/run/user/1001/docker.sock')
  valid={'Id':'f'*64,'Name':'/'+self.job['containerName'],'State':{'Running':True},'Config':{'User':'10001:10001','Labels':{'pockethost.web':'true','pockethost.web.app':self.job['appId'],'pockethost.web.deployment':self.job['deploymentId']}},'HostConfig':{'ReadonlyRootfs':True,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges']},'Mounts':[{'Type':'volume','Name':'ph-web-data-'+self.job['appId']+'-next-cache-v1','Destination':'/app/.next/cache','RW':True}]}
  engine.request=lambda *args:valid;self.assertEqual(engine.inspect(self.job),'f'*64)
  for path in ['/app/.next','/app/.next/cache/subdir','/app/.next/cache/../server','/etc']:
   with mock.patch.dict(valid['Mounts'][0],{'Destination':path}),self.assertRaises(RuntimeError):engine.inspect(self.job)
  with mock.patch.dict(valid['Mounts'][0],{'Name':'ph-web-data-'+('z'*15)+'-next-cache-v1'}),self.assertRaises(RuntimeError):engine.inspect(self.job)

 def test_launcher_errors_produce_stage_diagnostic_without_raw_exception_or_secrets(self):
  for method,phrase in [('inspect','Validation du conteneur'),('create','Création de la commande'),('attach','Démarrage ou connexion'),('request','Lecture du code de sortie')]:
   self.calls=[];self.worker.engine=FakeDocker()
   def fail(*args):raise RuntimeError('RAW-PRIVATE-ENGINE-DETAIL private-across-chunks')
   setattr(self.worker.engine,method,fail);self.worker.execute(self.job);report=self.report()
   self.assertEqual(report['status'],'failed');self.assertEqual(report['exitCode'],-1);self.assertIn(phrase,report['log']);self.assertNotIn('RAW-PRIVATE',report['log']);self.assertNotIn('private-across-chunks',report['log'])
   if method=='inspect':self.assertEqual(self.worker.engine.creates,[]);self.assertEqual(self.worker.engine.cleanups,[])
  self.calls=[];self.worker.engine=FakeDocker();self.worker.engine.cleanup=fail;self.worker.execute(self.job)
  self.assertEqual(self.report()['reason'],'cleanup_failed');self.assertIn('Nettoyage',self.report()['log']);self.assertNotIn('RAW-PRIVATE',self.report()['log'])

 def test_cleanup_exec_receives_run_start_and_rejects_invalid_values(self):
  engine=m.DockerTasks('/run/user/1001/docker.sock');calls=[]
  engine.request=lambda method,path,body=None:(calls.append(body)or{'Id':'e'*64})
  engine.cleanup('f'*64,'a'*64,1700000000000);engine.cleanup('f'*64,'a'*64,True);engine.cleanup('f'*64,'a'*64,-5);engine.cleanup('f'*64,'a'*64,'1;kill -9 -1')
  cmds=[body['Cmd']for body in calls if body and 'Cmd'in body];self.assertEqual([cmd[-1]for cmd in cmds],['1700000000000','','',''])
  self.assertTrue(all(cmd[:3]==['/usr/bin/timeout','--kill-after=1s','4s']and cmd[-3]==m.CLEANUP and cmd[-2]=='a'*64 for cmd in cmds))
 def test_success_reads_docker_frames_redacts_and_cleans_marker(self):
  before=int(time.time()*1000)-2000;self.worker.execute(self.job);since=self.worker.engine.cleanups[0][2];self.assertTrue(before<=since<=int(time.time()*1000)-2000)
  self.worker.engine.cleanups=[];self.calls=[];self.worker.execute(self.job);report=self.report();self.assertEqual(report['status'],'succeeded');self.assertEqual(report['exitCode'],0);self.assertEqual(report['log'],'hello [masqué]\n');self.assertEqual(len(self.worker.engine.cleanups),1);self.assertEqual(len(self.worker.engine.cleanups[0][1]),64);self.assertEqual(self.calls[0][0],'validate');self.assertNotIn('private-across-chunks',json.dumps(report))
 def test_stderr_nonzero_and_invalid_frame_cleanup(self):
  self.worker.engine=FakeDocker([b'failure'],2);self.worker.execute(self.job);self.assertEqual(self.report()['status'],'failed');self.assertEqual(self.report()['exitCode'],2)
  self.calls=[];self.worker.engine.attach=lambda _:(DummyConnection(),Response(b'bad-frame!'),DummySocket());self.worker.execute(self.job);self.assertEqual(self.report()['status'],'failed');self.assertEqual(len(self.worker.engine.cleanups),2)
 def test_lease_revocation_and_shutdown_always_cleanup(self):
  self.worker.api=lambda path,body:({'valid':False}if path=='validate'else self.api(path,body));self.worker.execute(self.job);self.assertEqual(self.report()['reason'],'container_changed');self.assertEqual(len(self.worker.engine.cleanups),1)
  self.calls=[];self.worker.stop.set();self.worker.execute(self.job);self.assertEqual(self.report()['reason'],'worker_shutdown');self.assertEqual(len(self.worker.engine.cleanups),2)
 def test_job_options_and_durations_are_bounded(self):
  for change in [{'timeoutSeconds':0},{'timeoutSeconds':901},{'timeoutSeconds':True},{'command':'a\0b'},{'appId':'mothership'},{'proof':'bad'},{'secrets':['x'*200001]}]:
   with self.assertRaises(RuntimeError):self.worker.validate_job({**self.job,**change})
 def test_unsafe_sockets_are_rejected_before_contact(self):
  for path in ['/var/run/docker.sock','/run/user/0/docker.sock','/tmp/docker.sock']:
   with self.assertRaises(RuntimeError):m.DockerTasks(path).verify()
class NodeWatchdogTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.node=shutil.which('node');cls.unshare=None
  # Real escape test only inside a private PID namespace: the host /proc is never scanned.
  if cls.node and sys.platform.startswith('linux')and shutil.which('unshare'):
   probe=['unshare','--user','--map-root-user','--pid','--fork','--mount-proc']
   try:
    if subprocess.run(probe+['/bin/sh','-c','test -r /proc/1/stat'],capture_output=True,timeout=10).returncode==0:cls.unshare=probe
   except (OSError,subprocess.SubprocessError):pass
 def pick(self,rows,own,since):
  script=m.TARGETS+'process.stdout.write(JSON.stringify(pick('+json.dumps(rows)+','+str(own)+','+json.dumps(since)+').sort((a,b)=>a-b)))'
  return json.loads(subprocess.run([self.node,'-e',script],capture_output=True,check=True,timeout=10).stdout)
 def test_cleanup_selection_kills_escaped_task_processes_never_site_or_other_execs(self):
  if not self.node:self.skipTest('Node required')
  since=1000000;before=since-60000;after=since+5000
  def row(pid,ppid,sid,start,ours=False):return{'pid':pid,'ppid':ppid,'sid':sid,'start':start,'ours':ours}
  rows=[
   row(1,0,1,before),row(2,1,1,after), # site PID 1 and a worker it forked during the run
   row(7,1,7,before),row(8,7,7,after), # dumb-init style site session: new children keep its session
   row(90,0,90,after),row(91,90,90,after), # /usr/bin/timeout exec root and the unmarked supervisor
   row(100,91,100,after,True),row(101,100,100,after,True), # detached task shell (new session) and its child
   row(102,101,100,after), # child that dropped the marker (env -u)
   row(120,1,120,after),row(121,120,120,after), # env -u PH_WEB_TASK_RUN setsid daemon reparented to PID 1, and its child
   row(130,1,100,after), # double fork without setsid: orphan keeps the task session
   row(200,0,200,after),row(201,200,200,after), # web terminal or another exec tree
   row(300,1,300,before), # detached before this run: not ours to kill
   row(310,1,305,before),row(311,310,305,after), # double-forked site daemon (session leader 305 exited) and a worker it respawned during the run
   row(400,0,400,after)] # the cleanup helper itself
  self.assertEqual(self.pick(rows,400,since),[100,101,102,120,121,130])
  # The in-container watchdog never targets itself, its timeout parent (exit code preserved) or other execs.
  self.assertEqual(self.pick(rows,91,since),[100,101,102,120,121,130])
  # Unknown boot time or invalid worker timestamp: only the marked tree.
  self.assertEqual(self.pick([{**r,'start':0}for r in rows],400,since),[100,101,102])
  for invalid in [None,0,-1,'1000000']:self.assertEqual(self.pick(rows,400,invalid),[100,101,102])
  for script in [m.SUPERVISOR,m.CLEANUP]:subprocess.run([self.node,'-e','new Function('+json.dumps(script)+')'],check=True,timeout=10)
 def test_linux_namespace_watchdog_kills_env_unset_setsid_escape(self):
  if not self.unshare:self.skipTest('Linux user+PID namespace required')
  with tempfile.TemporaryDirectory()as folder:
   pid_file=pathlib.Path(folder)/'pid'
   # The escapee drops the marker, opens a new session and is reparented to the namespace PID 1 (sh).
   command='env -u PH_WEB_TASK_RUN setsid /bin/sh -c \'echo $$ > "$0"; exec sleep 60 </dev/null >/dev/null 2>&1\' '+str(pid_file)+' & sleep 0.5; exit 0'
   inner='"$0" -e "$1" "$2" 5 "$3"; echo "exit:$?"; sleep 0.5; pid=$(cat "$4"); echo "pid:$pid"; if read -r _ _ state _ </proc/$pid/stat; then echo "state:$state"; else echo state:gone; fi 2>/dev/null'
   result=subprocess.run(self.unshare+['/bin/sh','-c',inner,self.node,m.SUPERVISOR,secrets.token_hex(32),command,str(pid_file)],capture_output=True,timeout=20);out=result.stdout.decode()
   self.assertIn('exit:0',out,result.stderr.decode());self.assertRegex(out,r'pid:[1-9][0-9]*\n');self.assertTrue('state:gone'in out or 'state:Z'in out,out)
 def bridge(self):
  if sys.platform.startswith('linux'):return''
  # macOS test bridge for /proc: expose only the supervisor's own detached child
  # group, discovered by ps. Production Linux uses the real inherited marker.
  return r'''
const __fs=require('fs'),__cp=require('child_process'),__spawn=__cp.spawn;let __group=0;
__cp.spawn=function(...args){const child=__spawn.apply(this,args);__group=child.pid;return child};
const __read=__fs.readFileSync.bind(__fs),__dirs=__fs.readdirSync.bind(__fs);
function __rows(){try{return __cp.execFileSync('/bin/ps',['-axo','pid=,ppid=,pgid=']).toString().trim().split('\n').map(x=>x.trim().split(/\s+/)).filter(x=>+x[2]===__group)}catch{return[]}}
function __pids(){return __rows().map(x=>x[0])}
__fs.readdirSync=function(path,...args){return path==='/proc'?__pids():__dirs(path,...args)};
// No btime on macOS: start stays unknown (0), so only the marked tree is selected, never host processes.
__fs.readFileSync=function(path,...args){const text=String(path),stat=/^\/proc\/(\d+)\/stat$/.exec(text);if(text==='/proc/stat')throw new Error('no /proc');if(stat){const row=__rows().find(x=>x[0]===stat[1]);if(!row)throw new Error('gone');return row[0]+' (bridge) S '+row[1]+' '+row[2]+' '+row[2]+' 0'.repeat(20)}return /^\/proc\/\d+\/environ$/.test(text)?Buffer.from('PH_WEB_TASK_RUN='+process.argv[1]+String.fromCharCode(0)):__read(path,...args)};
'''
 def test_real_node_watchdog_terminates_command_at_deadline(self):
  if not self.node:self.skipTest('Node required')
  start=time.monotonic();result=subprocess.run([self.node,'-e',self.bridge()+m.SUPERVISOR,secrets.token_hex(32),'1','sleep 60'],capture_output=True,timeout=7);self.assertEqual(result.returncode,124,result.stderr.decode());self.assertLess(time.monotonic()-start,4)
 def test_supervisor_restores_loader_values_only_for_the_command(self):
  if not self.node:self.skipTest('Node required')
  value='--require /app/never-load-in-supervisor.cjs';result=subprocess.run([self.node,'-e',self.bridge()+m.SUPERVISOR,secrets.token_hex(32),'3','printf "%s" "$NODE_OPTIONS"',json.dumps({'NODE_OPTIONS':value})],capture_output=True,timeout=7);self.assertEqual(result.returncode,0,result.stderr.decode());self.assertEqual(result.stdout.decode(),value)
 def test_real_node_normal_exit_reaps_background_children(self):
  if not self.node:self.skipTest('Node required')
  start=time.monotonic();result=subprocess.run([self.node,'-e',self.bridge()+m.SUPERVISOR,secrets.token_hex(32),'5','sleep 60 & echo background:$!; exit 0'],capture_output=True,timeout=7);self.assertEqual(result.returncode,0,result.stderr.decode());self.assertIn('background:',result.stdout.decode());self.assertLess(time.monotonic()-start,4)
if __name__=='__main__':unittest.main()
