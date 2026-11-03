#!/usr/bin/env python3
import contextlib,copy,hashlib,json,os,pathlib,tempfile,unittest
from unittest.mock import patch
import web_project_migration as m

APP,DB,PROJECT,DEPLOY='a'*15,'b'*15,'c'*15,'d'*15

class Control:
    def __init__(self):self.calls=[]
    def call(self,action,body):self.calls.append((action,body));return {'item':{'status':'imported_paused'}}

class Cloudflare:
    def __init__(self,rows=None):
        self.rows=copy.deepcopy(rows or []);self.calls=[];self.fail_after=None;self.failed=False;self.counter=0
    def zone(self,host):return 'z'*32
    def call(self,method,path,body=None):
        self.calls.append((method,path,copy.deepcopy(body)))
        if method=='GET':
            if '?'in path:return copy.deepcopy(self.rows)
            return copy.deepcopy(next(row for row in self.rows if row['id']==path.rsplit('/',1)[1]))
        if method=='PATCH':
            row=next(row for row in self.rows if row['id']==path.rsplit('/',1)[1]);row.update(body);result=copy.deepcopy(row)
        elif method=='POST':
            self.counter+=1;result={**body,'id':'new'+str(self.counter),'tags':body.get('tags',[]),'settings':body.get('settings',{})};self.rows.append(copy.deepcopy(result))
        elif method=='DELETE':
            ident=path.rsplit('/',1)[1];self.rows=[row for row in self.rows if row['id']!=ident];result={'id':ident}
        else:raise AssertionError(method)
        if self.fail_after==method and not self.failed:self.failed=True;raise OSError('simulated lost response after mutation')
        return result

class Migration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name).resolve()
        self.value={'projectId':PROJECT,'projectName':'Projet épreuve','revision':'f'*64,'apps':[{'id':APP,'name':'App','deploymentId':DEPLOY,'configuration':{'config':{'generatedDomain':'web-old.app4.monappli.re'},'env':{'POCKETBASE_URL':{'value':'http://127.0.0.1:4825','build':False,'runtime':True},'APP_URL':{'value':'https://web-old.app4.monappli.re/','build':False,'runtime':True},'PRIVATE_SECRET':{'value':'not-public','build':False,'runtime':True}},'runtime':{'kind':'auto','resources':{'memoryMiB':768}},'storage':[]},'bindings':[{'instanceId':DB,'port':4825,'publicHosts':['database.app4.monappli.re']}],'scheduledTasks':[{'id':'g'*15,'name':'Sync','command':'node scripts/sync.js','schedule':'0 2 * * *','timezone':'Indian/Reunion','timeoutSeconds':60,'enabled':True}],'domains':[{'host':'site.example.com','status':'active'}],'externalDependencies':[],'services':[{'name':'web','front':True,'image':'sha256:'+'1'*64,'volumes':[]},{'name':'worker','front':False,'image':'sha256:'+'2'*64,'volumes':[]}]}],'databases':{DB:{'version':'0.40.2','memoryReservationMiB':32768,'memoryMaximumMiB':32768,'support':[]}}}
        self.manifest={'platform':'app4','metadata':{'projectId':PROJECT}}
        self.cp=patch.object(m,'capsule',side_effect=lambda directory:(self.value,self.manifest));self.cp.start()
        self.budget=patch.object(m.recovery.recovery,'restore_budget',return_value={'restoreBytes':1024});self.budget.start()
    def tearDown(self):self.cp.stop();self.budget.stop();self.temp.cleanup()
    def plan(self):return m.build_plan(self.root,'app5','h'*15,'3'*64)
    def record(self,typ='A',ident='old',content='192.0.2.10'):
        return {'id':ident,'type':typ,'name':'site.example.com','content':content,'proxied':False,'ttl':300,'comment':'before','tags':['keep-me'],'settings':{}}
    def test_exact_new_ids_maps_every_variable_binding_worker_task_and_domain(self):
        plan=self.plan();self.assertEqual(plan['projectName'],'Projet épreuve · migration');self.assertEqual(len(plan['environment']),3);self.assertEqual(len(plan['bindings']),1);self.assertEqual(len(plan['workers']),1);self.assertEqual(len(plan['scheduledTasks']),1)
        self.assertTrue(plan['applications'][0]['hasFront']);self.assertFalse(plan['scheduledTasks'][0]['enabled']);self.assertEqual(plan['databases'][0]['memoryReservationMiB'],32768)
        self.assertGreater(plan['requiredMemoryMiB'],32768)
        self.assertNotIn('not-public',json.dumps(m.public_plan(plan)))
        self.assertNotEqual(plan['applications'][0]['targetId'],APP)
        for key in ('environment','bindings','workers','scheduledTasks','domains'):
            broken=copy.deepcopy(plan);broken[key]=[];broken['revision']=m.digest_value({k:v for k,v in broken.items()if k!='revision'})
            with self.assertRaises(m.MigrationError):m.validate_plan(broken,self.value,self.manifest)
    def test_build_urls_and_custom_hooks_shared_writers_block_release(self):
        self.value['apps'][0]['configuration']['env']['APP_URL']['build']=True
        self.value['databases'][DB]['support']=[{'artifact':'native-support/'+DB+'/pb_hooks','state':{'files':1}}]
        self.value['databases'][DB]['externalApplicationBindingsCount']=2
        plan=self.plan();self.assertEqual({row['code']for row in plan['blockers']},{'build_url_requires_rebuild','native_hooks_require_review','shared_database_writers'})
        self.assertTrue(next(row['value']for row in plan['environment']if row['name']=='APP_URL').endswith('.app5.monappli.re/'))
    def test_fixed_reservation_cannot_disappear_even_in_a_rehashed_plan(self):
        plan=self.plan();plan['databases'][0]['memoryReservationMiB']=0;plan['revision']=m.digest_value({k:v for k,v in plan.items()if k!='revision'})
        with self.assertRaises(m.MigrationError):m.validate_plan(plan,self.value,self.manifest)
    def test_atomic_private_json_and_executable_modes_are_retained_without_special_bits(self):
        target=self.root/'state.json';m.private_json(target,{'value':'secret'});self.assertEqual(target.stat().st_mode&0o777,0o600)
        with patch.object(m.os,'replace',side_effect=OSError('power cut')):
            with self.assertRaises(OSError):m.private_json(target,{'value':'later'})
        self.assertEqual(json.loads(target.read_text()),{'value':'secret'})
        source=self.root/'source';source.mkdir();(source/'start.sh').write_text('exit 0');(source/'start.sh').chmod(0o4755)
        with patch.object(m.recovery.recovery,'require_space'):m.bounded_copy_tree(source,self.root/'copy')
        self.assertEqual((self.root/'copy/start.sh').stat().st_mode&0o7777,0o755)
    def test_dns_preview_is_read_only_explicitly_proxied_and_new_ttl_independent_of_old_plan(self):
        plan=self.plan();plan['expiresAt']=0;cf=Cloudflare([self.record()]);preview=m.dns_preview(plan,cf)
        self.assertTrue(preview['changes'][0]['after']['proxied']);self.assertGreater(preview['expiresAt'],m.epoch());self.assertTrue(all(call[0]=='GET'for call in cf.calls))
        self.assertIn(plan['id'],preview['changes'][0]['after']['comment'])
    def test_lost_patch_response_and_aaaa_deletion_are_reconciled_and_rolled_back(self):
        for failure in ('PATCH','DELETE'):
            cf=Cloudflare([self.record(),self.record('AAAA','v6','2001:db8::1')]);preview=m.dns_preview(self.plan(),cf);cf.fail_after=failure;journal=self.root/(failure+'.json')
            with self.assertRaises(OSError):m.dns_cutover(preview,Control(),cf,journal,preview['revision'])
            restored=[m.dns_record(row)for row in cf.rows]
            self.assertEqual({(row['type'],row['content'])for row in restored},{('A','192.0.2.10'),('AAAA','2001:db8::1')})
            count=len(cf.calls);m.dns_rollback(journal,cf);self.assertEqual(len(cf.calls),count)
    def test_ambiguous_post_is_identified_by_migration_marker_and_removed(self):
        cf=Cloudflare();preview=m.dns_preview(self.plan(),cf);cf.fail_after='POST';journal=self.root/'post.json'
        with self.assertRaises(OSError):m.dns_cutover(preview,Control(),cf,journal,preview['revision'])
        self.assertEqual(cf.rows,[]);self.assertTrue(json.loads(journal.read_text())['rolledBack'])
    def test_partial_dns_rollback_lost_ipv6_response_is_idempotent_and_checks_origin(self):
        cf=Cloudflare([self.record(),self.record('AAAA','v6','2001:db8::1')]);preview=m.dns_preview(self.plan(),cf);journal=self.root/'partial.json'
        m.dns_cutover(preview,Control(),cf,journal,preview['revision']);cf.fail_after='POST'
        with self.assertRaises(OSError):m.dns_rollback(journal,cf)
        self.assertTrue(json.loads(journal.read_text())['changes'][0]['primaryRestored'])
        cf.rows[0]['content']='192.0.2.99'
        with self.assertRaises(m.MigrationError):m.dns_rollback(journal,cf)
        cf.rows[0]['content']='192.0.2.10';m.dns_rollback(journal,cf)
        self.assertEqual(len([row for row in cf.rows if row['type']=='AAAA']),1)
    def test_orphaned_import_rollback_is_locked_and_reconciles_before_cleanup(self):
        plan=self.plan();config={'stateRoot':str(self.root)}
        control=Control();control.call=lambda action,body:control.calls.append((action,body))or{'item':{'status':'importing'}}
        with m.migration_lock(plan,config):
            with self.assertRaises(BlockingIOError):
                with m.migration_lock(plan,config):pass
        with patch.object(m,'_rollback_import',return_value={'rolledBack':True})as cleanup:
            self.assertTrue(m.rollback_import(plan,config,control,None)['rolledBack']);cleanup.assert_called_once()
        self.assertEqual([row[0]for row in control.calls],['state','fail'])
        self.assertEqual(json.loads((self.root/(plan['id']+'.json')).read_text())['databases'],[])
    def test_memory_policy_cas_precedes_destructive_rollback_and_own_removal_resumes(self):
        plan=self.plan();ident=plan['databases'][0]['targetId'];amount=32768
        config={'stateRoot':str(self.root),'dataRoot':str(self.root/'data'),'webRoot':str(self.root/'web'),'helperImage':'sha256:'+'1'*64}
        journal=self.root/(plan['id']+'.json');state={'id':plan['id'],'revision':plan['revision'],'databases':[],'volumes':[],'images':{},'containers':[],'storageManifests':[],'memoryCommitments':{ident:amount},'memoryApplied':True}
        m.private_json(journal,state);control=Control();docker=lambda args:''
        with patch('database_memory_management.policy_lock',side_effect=contextlib.nullcontext),patch('database_memory_reservations.load_policy',return_value={ident:(amount+512)*1024**2}):
            with self.assertRaises(m.MigrationError):m._rollback_import(plan,config,control,docker)
        self.assertEqual(control.calls,[]);self.assertFalse(json.loads(journal.read_text()).get('rollbackStarted',False))
        state.update(rollbackStarted=True,memoryRemovalStarted=True);m.private_json(journal,state)
        with patch('database_memory_management.policy_lock',side_effect=contextlib.nullcontext),patch('database_memory_reservations.load_policy',return_value={}),patch('platform_memory_allocator.atomic')as atomic:
            self.assertTrue(m._rollback_import(plan,config,control,docker)['rolledBack']);atomic.assert_called_once()
        self.assertTrue(json.loads(journal.read_text())['memoryRemoved'])
    def test_partial_artifact_intents_and_stopped_own_holder_are_reconciled(self):
        plan=self.plan();app=plan['applications'][0]['targetId'];db=plan['databases'][0]['targetId'];holder='kolab-migration-'+plan['id']+'-volume-1';volume='ph-web-data-'+app+'-data-v1'
        config={'stateRoot':str(self.root),'dataRoot':str(self.root/'data'),'webRoot':str(self.root/'web'),'helperImage':'sha256:'+'1'*64}
        journal=self.root/(plan['id']+'.json');state={'id':plan['id'],'revision':plan['revision'],'databases':[{'id':db,'path':str(self.root/'data/instances'/db),'sha256':''}],'volumes':[{'name':volume,'sha256':'a'*64,'created':True}],'images':{},'containers':[],'storageManifests':[{'path':str(self.root/'web/storage'/(app+'.json')),'sha256':'f'*64,'created':False}]}
        m.private_json(journal,state);commands=[]
        def docker(args):
            commands.append(args)
            if args[:2]==['volume','ls']:return volume
            if args[:2]==['volume','inspect']:return json.dumps([{'Labels':{'kolab.project-migration':plan['id']},'Driver':'local','Options':{}}])
            if args[0]=='inspect':return json.dumps([{'Config':{'Labels':{'kolab.project-migration':plan['id']}},'State':{'Running':False}}])
            if args[0]=='ps':return holder
            return ''
        with patch.object(m,'volume_digest',return_value='a'*64):self.assertTrue(m._rollback_import(plan,config,Control(),docker)['rolledBack'])
        self.assertIn(['rm','-f',holder],commands);self.assertIn(['volume','rm',volume],commands)
    def test_dns_outside_edits_and_multi_origin_are_never_overwritten(self):
        plan=self.plan();cf=Cloudflare([self.record()]);preview=m.dns_preview(plan,cf);cf.rows[0]['content']='192.0.2.88'
        with self.assertRaises(m.MigrationError):m.dns_cutover(preview,Control(),cf,self.root/'conflict.json',preview['revision'])
        self.assertEqual(cf.rows[0]['content'],'192.0.2.88');self.assertFalse(any(call[0]in('POST','PATCH','DELETE')for call in cf.calls))
        with self.assertRaises(m.MigrationError):m.dns_preview(plan,Cloudflare([self.record(),self.record(ident='other')]))

if __name__=='__main__':unittest.main()
