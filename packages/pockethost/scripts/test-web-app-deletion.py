import copy,json,pathlib,tempfile,unittest,threading,types,os
from unittest import mock
import web_app_deletion as d

APP,OTHER,DEP,JOB='a'*15,'b'*15,'c'*15,'d'*15
class DeletionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=pathlib.Path(self.tmp.name).resolve();self.calls=[];self.reports=[]
  self.job={'id':JOB,'lease':'L'*40,'kind':'app','targetId':APP,'apps':[{'id':APP,'deployments':[DEP],'taskRuns':[]}]}
  self.container={'Id':'owned','Name':'/ph-web-'+APP+'-'+DEP,'Config':{'Labels':{'pockethost.web':'true','pockethost.web.app':APP}}}
  self.volume='ph-web-data-'+APP+'-uploads-v1';self.volume_info={'Labels':{'pockethost.web.app':APP},'Driver':'local','Options':None}
  self.network={'Id':'network','Name':'ph-web-net-'+APP+'-'+DEP,'Labels':{'pockethost.web.app':APP},'Containers':{}}
  self.image={'Id':'sha256:'+'e'*64,'Config':{'Labels':{'pockethost.web.app':APP}},'RepoTags':['ph-web/'+APP+':'+DEP]}
  self.exists={'container':True,'volume':True,'image':True,'network':True};self.atomic(self.root/'routes.json',{'version':1,'routes':[{'appId':APP},{'appId':OTHER}]});self.atomic(self.root/'runtime-publications.json',{APP:{},OTHER:{'live':True}})
 def atomic(self,path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
 def load(self,path,default=None):return json.loads(path.read_text())if path.exists()else(default if default is not None else{})
 def api(self,path,body):
  if path=='deletions/claim':return {'job':copy.deepcopy(self.job)}
  self.reports.append(body);return {'ok':True}
 def docker(self,args,**kw):
  self.calls.append(args)
  if args[0]=='ps':return 'owned'if self.exists['container']else''
  if args[0]=='inspect':return json.dumps([self.container])
  if args[:2]==['rm','-f']:self.exists['container']=False
  if args[:2]==['volume','ls']:return self.volume if self.exists['volume']else''
  if args[:2]==['volume','inspect']:return json.dumps([self.volume_info])
  if args[:2]==['volume','rm']:self.exists['volume']=False
  if args[:2]==['image','ls']:return 'image'if self.exists['image']else''
  if args[:2]==['image','inspect']:return json.dumps([self.image])
  if args[:2]==['image','rm']:self.exists['image']=False
  if args[:2]==['network','ls']:return 'network'if self.exists['network']else''
  if args[:2]==['network','inspect']:return json.dumps([self.network])
  if args[:2]==['network','rm']:self.exists['network']=False
  return ''
 def tick(self):return d.tick(self.root,self.api,self.docker,self.atomic,self.load)
 def test_valid_scope_deletes_owned_resources_and_files_preserves_neighbor(self):
  own=self.root/'assets'/APP;own.mkdir(parents=True);(own/'chunk').write_text('private')
  other=self.root/'assets'/OTHER;other.mkdir();(other/'chunk').write_text('live')
  self.atomic(self.root/'storage'/(APP+'.json'),{});self.atomic(self.root/'runtime-logs'/(APP+'.json'),{})
  self.assertTrue(self.tick());self.assertFalse(any(self.exists.values()));self.assertFalse(own.exists());self.assertEqual((other/'chunk').read_text(),'live')
  self.assertEqual(self.load(self.root/'routes.json')['routes'],[{'appId':OTHER}]);self.assertEqual(self.load(self.root/'runtime-publications.json'),{OTHER:{'live':True}})
  self.assertEqual(self.reports[-1]['status'],'ready');self.assertTrue(self.reports[-1]['removed']);self.assertTrue((self.root/'preview-deletions'/(APP+'.json')).exists())
  self.assertFalse(any(c[:2]==['image','rm']and '-f'in c for c in self.calls))
 def test_foreign_container_label_or_name_never_reaches_remove(self):
  for key,bad in [('label',OTHER),('name','/ph-web-'+OTHER+'-'+DEP)]:
   with self.subTest(key=key):
    self.calls=[]
    if key=='label':self.container['Config']['Labels']['pockethost.web.app']=bad
    else:self.container['Config']['Labels']['pockethost.web.app']=APP;self.container['Name']=bad
    self.tick();self.assertFalse(any(c[0]=='rm'for c in self.calls));self.assertEqual(self.reports[-1]['status'],'failed')
 def test_foreign_or_external_volume_is_preserved(self):
  for field,value in [('Labels',{'pockethost.web.app':OTHER}),('Driver','nfs'),('Options',{'device':'/host'})]:
   with self.subTest(field=field):
    original=copy.deepcopy(self.volume_info);self.volume_info[field]=value;self.calls=[];self.tick()
    self.assertFalse(any(c[:2]==['volume','rm']for c in self.calls));self.assertEqual(self.reports[-1]['status'],'failed');self.volume_info=original
 def test_network_with_other_containers_is_preserved(self):
  self.network['Containers']={'foreign':{}};self.tick();self.assertFalse(any(c[:2]==['network','rm']for c in self.calls));self.assertEqual(self.reports[-1]['status'],'failed')
 def test_symlink_never_deletes_external_directory_and_failure_can_resume(self):
  target=self.root/'foreign';target.mkdir();(target/'data').write_text('keep');(self.root/'assets').mkdir();(self.root/'assets'/APP).symlink_to(target)
  self.tick();self.assertEqual((target/'data').read_text(),'keep');self.assertEqual(self.reports[-1]['status'],'failed')
  (self.root/'assets'/APP).unlink();self.tick();self.assertEqual(self.reports[-1]['status'],'ready')
 def test_invalid_scope_rejected_before_docker_or_file_mutation(self):
  for change in [{'targetId':OTHER},{'apps':[{'id':'../escape','deployments':[],'taskRuns':[]}]},{'lease':'x'},{'apps':[self.job['apps'][0]]*2}]:
   with self.subTest(change=change):
    job={**self.job,**change}
    with self.assertRaises(RuntimeError):d.validate(job)
  self.assertEqual(self.calls,[])
 def test_empty_project_and_retry_after_partial_cleanup(self):
  self.volume_info['Driver']='nfs';self.tick();self.assertFalse(self.exists['container']);self.assertTrue(self.exists['volume'])
  self.volume_info['Driver']='local';self.tick();self.assertEqual(self.reports[-1]['status'],'ready')
  self.job.update(kind='project',apps=[]);self.tick();self.assertEqual(self.reports[-1]['status'],'ready')
 def test_private_database_state_directory_is_removed_and_sibling_preserved(self):
  own=self.root/'private-db-runtime'/APP/(DEP+'-web');own.mkdir(parents=True)
  (own/'config.json').write_text('private-capability')
  sibling=self.root/'private-db-runtime'/OTHER/(DEP+'-web');sibling.mkdir(parents=True)
  (sibling/'config.json').write_text('other-capability')
  self.tick()
  self.assertEqual(self.reports[-1]['status'],'ready');self.assertFalse((self.root/'private-db-runtime'/APP).exists())
  self.assertEqual((sibling/'config.json').read_text(),'other-capability')
 def test_deletion_waits_for_private_database_operations_before_any_mutation(self):
  import web_app_private_db as private
  lock=threading.RLock();started=threading.Event();finished=threading.Event();errors=[];pulses=[]
  def remove():
   started.set()
   try:d.remove_app(self.root,self.job['apps'][0],self.docker,self.atomic,self.load,lambda stage:pulses.append(stage))
   except Exception as error:errors.append(error)
   finally:finished.set()
  with mock.patch.dict(os.environ,{'WEB_DB_BRIDGE_ENABLED':'true'}),mock.patch.object(private,'_manager',types.SimpleNamespace(operations_lock=lock)):
   with lock:
    thread=threading.Thread(target=remove);thread.start();self.assertTrue(started.wait(1))
    self.assertFalse(finished.wait(.05));self.assertEqual(self.calls,[]);self.assertEqual(pulses,[])
   thread.join(2);self.assertFalse(thread.is_alive());self.assertTrue(finished.is_set())
  self.assertEqual(errors,[]);self.assertFalse(any(self.exists.values()))

if __name__=='__main__':unittest.main()
