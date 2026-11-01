#!/usr/bin/env python3
"""Telemetry isolation, truthful remote state and restore-worker safety."""
import unittest,tempfile,pathlib,sqlite3,json,subprocess,re,os,hashlib,time
SOURCE=pathlib.Path(__file__).resolve().parents[1]/'src/mothership-app/src/lib/handlers/instance/api/litestreamTelemetry.ts'
SCRIPT=re.search(r'LITESTREAM_TELEMETRY = String.raw`(.*?)`',SOURCE.read_text(),re.S).group(1)
BOOTSTRAP=r'''
import sys,types,runpy,json,subprocess,urllib.request,datetime,os
mode=os.environ['TEST_MODE'];root=sys.argv[2];ident=sys.argv[4]
subprocess.check_output=lambda args,**kw:json.dumps([{'database':root+'/instances/'+ident+'/pb_data/data.db','status':'ok','local_txid':'000000000000000a','wal_size':'4 KiB'}]) if args[0]=='litestream' else json.dumps([{'name':'pockethost-litestream','pm2_env':{'status':'online','secret':'do-not-export'},'monit':{'cpu':1,'memory':100}}])
class Response:
 def read(self,n):return ('litestream_sync_error_count{db="'+root+'/instances/'+ident+'/pb_data/data.db"} 2\nlitestream_sync_error_count{db="/other/tenant/data.db"} 999\nlitestream_replica_operation_bytes{replica_type="s3"} 999999\n').encode()
urllib.request.urlopen=lambda *a,**kw:Response()
class Client:
 def list_objects_v2(self,**kw):
  assert kw['Prefix']=='safe-prefix/';assert kw['Bucket']=='bucket'
  if mode=='denied':
   e=Exception('PRIVATE-SECRET');e.response={'Error':{'Code':'AccessDenied'}};raise e
  return {'Contents':[{'Key':'safe-prefix/0/0000000000000001-000000000000000a.ltx','Size':123,'LastModified':datetime.datetime.now(datetime.timezone.utc)}],**({'NextContinuationToken':'next'} if mode=='partial' else {})}
 def list_multipart_uploads(self,**kw):
  if mode=='multipart-denied':raise Exception('PRIVATE-SECRET')
  return {'Uploads':[{'Key':'safe-prefix/0/000000000000000b-000000000000000b.ltx','UploadId':'private-id','Initiated':datetime.datetime.now(datetime.timezone.utc)}]}
 def list_parts(self,**kw):return {'Parts':[{'Size':40}]}
class Session:
 def create_client(self,*a,**kw):return Client()
b=types.ModuleType('botocore');b.__path__=[];b.session=types.ModuleType('botocore.session');b.session.get_session=lambda:Session();b.config=types.ModuleType('botocore.config');b.config.Config=lambda **kw:None
sys.modules.update({'botocore':b,'botocore.session':b.session,'botocore.config':b.config})
sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name='__main__')
'''
class TelemetryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name).resolve();self.ident='a'*15;self.folder=self.root/'instances'/self.ident/'pb_data';self.folder.mkdir(parents=True);self.db=self.folder/'data.db';self.ls=self.root/'litestream';self.ls.mkdir();self.script=self.ls/'telemetry-worker.py';self.script.write_text(SCRIPT);self.script.chmod(0o600)
  with sqlite3.connect(self.db) as db:db.execute('CREATE TABLE _collections (id TEXT)');db.execute("INSERT INTO _collections VALUES ('collection')")
  control=self.root/'mothership/pb_data';control.mkdir(parents=True)
  with sqlite3.connect(control/'data.db') as db:
   db.execute('CREATE TABLE instance_litestream_replicas (instance TEXT,enabled BOOL,s3Bucket TEXT,replicaPath TEXT,s3Region TEXT,s3Endpoint TEXT,s3AccessKeyId TEXT,s3SecretAccessKey TEXT,s3ForcePathStyle BOOL)')
   db.execute('INSERT INTO instance_litestream_replicas VALUES (?,?,?,?,?,?,?,?,?)',(self.ident,1,'bucket','safe-prefix','gra','https://s3.example','PRIVATE-KEY','PRIVATE-SECRET',1))
  (self.ls/'litestream.yml').write_text('private-config')
 def tearDown(self):self.tmp.cleanup()
 def read(self,mode='normal'):
  return json.loads(subprocess.check_output(['python3','-c',BOOTSTRAP,str(self.script),str(self.root),str(self.ls),self.ident,'read'],env={**os.environ,'TEST_MODE':mode},text=True))
 def test_remote_positions_metrics_isolation_and_privacy(self):
  staged=self.folder/'.data.db-litestream/ltx/0';staged.mkdir(parents=True);(staged/'000000000000000b-000000000000000b.ltx').write_bytes(b'x'*100)
  before=hashlib.sha256(self.db.read_bytes()).hexdigest();r=self.read();self.assertEqual(r['positionState'],'caught-up');self.assertEqual(r['metrics'],{'sync_error_count':2});self.assertEqual(r['remote']['objects'],1);self.assertEqual(r['remote']['uploads'][0]['bytes'],40);self.assertEqual(r['remote']['uploads'][0]['totalBytes'],100)
  for secret in ['PRIVATE-KEY','PRIVATE-SECRET','do-not-export','private-id','/other/tenant']:self.assertNotIn(secret,json.dumps(r))
  self.assertEqual(before,hashlib.sha256(self.db.read_bytes()).hexdigest());self.assertEqual((self.ls/'telemetry').stat().st_mode&0o777,0o700)
 def test_partial_inventory_cannot_claim_caught_up(self):
  r=self.read('partial');self.assertTrue(r['remote']['partial']);self.assertEqual(r['positionState'],'unknown');self.assertEqual(r['remote']['objects'],3)
 def test_remote_denial_is_redacted(self):
  r=self.read('denied');self.assertFalse(r['remote']['ok']);self.assertIn('refusé',r['remote']['error']);self.assertNotIn('PRIVATE-SECRET',json.dumps(r))
 def test_optional_multipart_permissions_do_not_hide_valid_inventory(self):
  r=self.read('multipart-denied');self.assertTrue(r['remote']['ok']);self.assertFalse(r['remote']['multipartAvailable']);self.assertEqual(r['positionState'],'caught-up')
 def test_cached_read_does_not_repeat_remote_access(self):
  self.read();r=self.read('denied');self.assertTrue(r['remote']['ok']);self.assertEqual(len(r['history']),1)
 def test_destination_change_invalidates_remote_cache(self):
  self.read()
  with sqlite3.connect(self.root/'mothership/pb_data/data.db') as db:db.execute("UPDATE instance_litestream_replicas SET s3Endpoint='https://another.example'")
  r=self.read('denied');self.assertFalse(r['remote']['ok']);self.assertEqual(len(r['history']),1)
 def test_disabled_policy_no_remote_checks(self):
  with sqlite3.connect(self.root/'mothership/pb_data/data.db') as db:db.execute('UPDATE instance_litestream_replicas SET enabled=0')
  r=self.read('denied');self.assertFalse(r['enabled']);self.assertEqual(r['remote'],{})
 def test_abandoned_queue_is_reported_interrupted(self):
  self.read();state=self.ls/'telemetry'/(self.ident+'-verification.json');state.write_text(json.dumps({'status':'queued','startedAt':'2020-01-01T00:00:00+00:00'}))
  self.assertEqual(self.read()['verification']['status'],'interrupted')
 def test_symlink_rejected(self):
  other=self.folder/'other';self.db.rename(other);self.db.symlink_to(other)
  with self.assertRaises(subprocess.CalledProcessError):self.read()
 def test_restore_worker_lock_cleanup_and_readonly_source(self):
  binary=self.root/'bin';binary.mkdir();fake=binary/'litestream'
  fake.write_text('#!/usr/bin/env python3\nimport sys,shutil,time\ntime.sleep(1)\nshutil.copyfile(sys.argv[-1],sys.argv[sys.argv.index("-o")+1])\n');fake.chmod(0o700)
  env={**os.environ,'PATH':str(binary)+':'+os.environ['PATH']};before=self.db.read_bytes()
  args=['python3',str(self.script),str(self.root),str(self.ls),self.ident,'verify']
  self.assertTrue(json.loads(subprocess.check_output(args,env=env))['started']);second=json.loads(subprocess.check_output(args,env=env));self.assertIn('déjà',second['error'])
  state=self.ls/'telemetry'/(self.ident+'-verification.json')
  for _ in range(100):
   result=json.loads(state.read_text())
   if result['status'] in ['passed','failed']:break
   time.sleep(.1)
  self.assertEqual(result['status'],'passed');self.assertEqual(result['lastSuccess']['collections'],1);self.assertEqual(result['lastSuccess']['integrity'],'ok');self.assertEqual(self.db.read_bytes(),before);self.assertEqual(list((self.ls/'telemetry').glob('restore-*')),[])
  fake.write_text('#!/bin/sh\nexit 1\n');fake.chmod(0o700)
  self.assertTrue(json.loads(subprocess.check_output(args,env=env))['started'])
  for _ in range(100):
   result=json.loads(state.read_text())
   if result['status'] in ['passed','failed']:break
   time.sleep(.1)
  self.assertEqual(result['status'],'failed');self.assertEqual(result['lastSuccess']['integrity'],'ok');self.assertEqual(len(result['history']),2);self.assertEqual(list((self.ls/'telemetry').glob('restore-*')),[])
if __name__=='__main__':unittest.main()
