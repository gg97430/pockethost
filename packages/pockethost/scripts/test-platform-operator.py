#!/usr/bin/env python3
import copy,pathlib,unittest,sqlite3,tempfile
from contextlib import ExitStack,nullcontext
from unittest.mock import patch
import platform_operator as m
import database_memory_management as management

DB='a'*15;CID='b'*64;M=m.MIB


class MemoryEdits(unittest.TestCase):
    def setUp(self):
        self.policy={};self.management={};self.writes=[];self.calls=[]
        self.item={'cid':CID,'name':DB,'pid':123,'running':True,'labels':{'pockethost.database.memory':'adaptive'},
            'host':{'Memory':4096*M,'MemoryReservation':0,'MemorySwap':-1},'mounts':[('/instances/'+DB,'/home/pockethost')]}
        self.raw=700*M
        outer=self
        class Engine:
            def __init__(self,*args):pass
            def inspect(self,cid):return copy.deepcopy(outer.item)
            def request(self,method,path,body=None):
                outer.calls.append((method,path,body))
                if method=='GET':return [{'Id':CID,'Names':['/'+DB]}]
                outer.item['host'].update(body);return {}
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name,value in [('load_policy',lambda:dict(self.policy)),('load_management',lambda:copy.deepcopy(self.management)),('policy_lock',lambda:nullcontext()),('read_json',lambda *a,**k:{'database':'/control','instancesRoot':'/instances','databaseMaximumMiB':16384}),('database_inventory',lambda *a:({DB},{},{},set())),('host_memory',lambda:(64000*M,50000*M)),('Engine',Engine),('scope',lambda *a:pathlib.Path('/scope')),('counters',lambda *a:(self.raw,self.raw)),('atomic',lambda path,value,mode:self.writes.append((path,copy.deepcopy(value))))]:self.stack.enter_context(patch.object(m,name,value))
        self.stack.enter_context(patch.object(m.os,'geteuid',return_value=0))
        self.stack.enter_context(patch.object(m,'assert_memory_editable',return_value=None))
        self.stack.enter_context(patch.object(m,'controller_sql',lambda *args:nullcontext()))
    def request(self,**extra):return {'id':DB,'mode':'fixed','maximumMiB':2048,'revision':m.revision(self.policy,self.management),**extra}
    def test_live_fixed_reservation_changes_only_memory_on_same_process(self):
        result=m.set_memory(self.request())
        self.assertFalse(result['restartRequired']);self.assertEqual(self.item['pid'],123)
        updates=[body for method,path,body in self.calls if method=='POST']
        self.assertEqual(updates,[{'Memory':2048*M,'MemoryReservation':2048*M,'MemorySwap':-1}])
        self.assertEqual(self.writes[0][1]['databases'],{DB:2048})
    def test_switch_to_adaptive_releases_reservation_without_restart(self):
        self.policy={DB:4096*M};self.item['host']['MemoryReservation']=4096*M
        result=m.set_memory(self.request(mode='adaptive',maximumMiB=16384))
        self.assertFalse(result['restartRequired']);self.assertEqual(self.item['host']['MemoryReservation'],6*M)
        self.assertEqual(self.writes[0][1]['databases'],{})
        self.assertEqual(self.writes[-1][1]['databases'][DB]['mode'],'adaptive')
    def test_rejects_lowering_into_live_memory_without_any_mutation(self):
        self.raw=2000*M
        with self.assertRaises(ValueError):m.set_memory(self.request())
        self.assertFalse(self.writes);self.assertFalse(any(method=='POST'for method,*_ in self.calls))
    def test_revision_conflict_and_capacity_refusal_do_not_touch_docker(self):
        with self.assertRaises(m.Conflict):m.set_memory(self.request(revision='0'*64))
        self.assertFalse(self.calls)
        with self.assertRaises(ValueError):m.set_memory(self.request(maximumMiB=65536))
        self.assertFalse(self.calls)
    def test_rejects_unknown_id_and_backup_restore(self):
        with self.assertRaises(ValueError):m.set_memory(self.request(id='c'*15))
        with patch.object(m,'database_inventory',return_value=({DB},{},{},{DB})):
            with self.assertRaises(ValueError):m.set_memory(self.request())
        self.assertFalse(self.calls)
    def test_schema_is_bounded_and_never_accepts_paths_or_boolean_amounts(self):
        for value in [self.request(id='../'+DB),self.request(maximumMiB=True),self.request(maximumMiB=0),{**self.request(),'command':'restart'},self.request(mode='unlimited')]:
            with self.assertRaises(ValueError):m.set_memory(value)
        self.assertFalse(self.calls)
    def test_management_document_requires_an_explicit_approval_identity(self):
        row={'mode':'adaptive','maximumMiB':8192,'revision':'a'*32}
        self.assertEqual(management.parse({'version':1,'databases':{DB:row}})[DB],row)
        for invalid in [{**row,'revision':'x'},{**row,'maximumMiB':True},{**row,'mode':'shell'},{**row,'extra':1}]:
            with self.assertRaises(ValueError):management.parse({'version':1,'databases':{DB:invalid}})
    def test_migration_lock_refuses_edits_before_any_docker_or_policy_mutation(self):
        with patch.object(m,'assert_memory_editable',side_effect=m.Conflict('migration locked')):
            with self.assertRaises(m.Conflict):m.set_memory(self.request())
        self.assertFalse(self.calls);self.assertFalse(self.writes)


class MigrationMemoryLock(unittest.TestCase):
    def test_old_schema_is_accepted_but_new_locked_instance_is_refused(self):
        with tempfile.TemporaryDirectory()as root:
            filename=pathlib.Path(root).resolve()/'control.db'
            with sqlite3.connect(filename)as db:db.execute('CREATE TABLE instances(id TEXT PRIMARY KEY)');db.execute('INSERT INTO instances VALUES(?)',(DB,))
            m.assert_memory_editable(filename,DB)
            with sqlite3.connect(filename)as db:db.execute('ALTER TABLE instances ADD migrationLock TEXT DEFAULT ""')
            m.assert_memory_editable(filename,DB)
            with sqlite3.connect(filename)as db:db.execute('UPDATE instances SET migrationLock=? WHERE id=?',('locked-migration',DB))
            with self.assertRaises(m.Conflict):m.assert_memory_editable(filename,DB)


if __name__=='__main__':unittest.main()
