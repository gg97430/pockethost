#!/usr/bin/env python3
import copy
import pathlib
import tempfile
import sqlite3
import unittest
from unittest.mock import patch
import platform_memory_allocator as m

APP, DEP, DB, CID = 'a' * 15, 'b' * 15, 'c' * 15, 'd' * 64
M = m.MIB


class Policy(unittest.TestCase):
    def policy(self, current=740, working=730, limit=768, floor=256, ceiling=8192, previous=None, now=2000):
        return m.propose(current*M, working*M, limit*M, floor*M, ceiling*M, previous or {}, now)

    def test_early_growth(self):
        target, why, _ = self.policy()
        self.assertEqual(why, 'grow')
        self.assertGreaterEqual(target, 1152*M)

    def test_no_continuous_growth_at_ceiling(self):
        target, why, _ = self.policy(current=7900, working=7800, limit=8192)
        self.assertEqual((target, why), (8192*M, 'ceiling'))

    def test_no_shrink_without_calm_period(self):
        self.assertEqual(self.policy(current=100, working=100, limit=2048)[0], 2048*M)
        self.assertEqual(self.policy(current=100, working=100, limit=2048, previous={'lowSince':500}, now=2000)[0], 2048*M)

    def test_shrink_after_thirty_minutes_only_by_quarter(self):
        target, why, _ = self.policy(current=100, working=100, limit=2048, previous={'lowSince':1}, now=2000)
        self.assertEqual((target, why), (1536*M, 'shrink'))

    def test_new_activity_resets_shrink_clock(self):
        _, why, since = self.policy(current=1000, working=1000, limit=2048, previous={'lowSince':1})
        self.assertEqual((why, since), ('stable', 2000))

    def test_recent_change_prevents_shrink(self):
        self.assertEqual(self.policy(current=100, working=100, limit=2048, previous={'lowSince':1,'changedAt':1900})[0],2048*M)

    def test_file_cache_does_not_trigger_growth(self):
        _, why, _ = self.policy(current=1900, working=100, limit=2048)
        self.assertEqual(why, 'stable')

    def test_never_shrink_into_live_memory(self):
        target, _, _ = self.policy(current=2000, working=100, limit=2048, previous={'lowSince':1})
        self.assertEqual(target, 2048*M)

    def test_unlimited_database_starts_above_raw_usage(self):
        target, why, _ = self.policy(current=7000, working=400, limit=0, floor=512, ceiling=16000)
        self.assertEqual(why, 'initial')
        self.assertGreaterEqual(target, 7256*M)

    def test_too_large_database_is_not_forcibly_capped(self):
        self.assertEqual(self.policy(current=9000, working=400, limit=0, floor=512, ceiling=8192)[:2],(0,'live-memory-protected'))

    def test_swap_policy_and_memory_only_updates(self):
        body=m.update_body({'Memory':768*M,'MemorySwap':768*M,'MemoryReservation':256*M},1152*M)
        self.assertEqual(body,{'Memory':1152*M,'MemorySwap':1152*M,'MemoryReservation':256*M})
        self.assertEqual(m.update_body({'Memory':0,'MemorySwap':0,'MemoryReservation':0},512*M)['MemorySwap'],-1)
        self.assertEqual(m.update_body({'Memory':512*M,'MemorySwap':768*M,'MemoryReservation':0},1024*M)['MemorySwap'],1280*M)

    def test_strict_project_budget_wins(self):
        reg={'version':1,'apps':{APP:'project'},'projects':{'project':{'resources':{'enabled':True,'memoryMiB':4096}}}}
        self.assertEqual(m.project_allowance(reg,APP,[(APP,768*M)]),0)
        reg['projects']['project']['resources']['enabled']=False
        self.assertGreater(m.project_allowance(reg,APP,[]),8192*M)
        with self.assertRaises(ValueError):m.project_allowance({},APP,[])


class Integration(unittest.TestCase):
    def setUp(self):
        policy=patch.object(m,'load_policy',return_value={});policy.start();self.addCleanup(policy.stop)
        management=patch.object(m,'load_database_management',return_value={});management.start();self.addCleanup(management.stop)
        self.item={'cid':CID,'name':'ph-web-'+APP+'-'+DEP,'pid':123,'running':True,
                   'labels':{'pockethost.web':'true','pockethost.web.app':APP,'pockethost.web.deployment':DEP},
                   'host':{'Memory':768*M,'MemorySwap':768*M,'MemoryReservation':256*M},'mounts':[]}
        self.profile={APP:{'allocation':'adaptive'}}
        self.config={'database':'/controller','webRoot':'/web','instancesRoot':'/instances','webMaximumMiB':8192,'databaseMaximumMiB':16384}
        self.updates=[]
        outer=self
        class Fake:
            kind='hosting'
            def request(self,*args):return [{'Id':CID,'Names':[outer.item['name']],'Labels':outer.item['labels']}]
            def inspect(self,*args):return copy.deepcopy(outer.item)
            def update(self,item,target):outer.updates.append(target)
        self.engine=Fake()

    def cycle(self, state=None, available=22000*M, busy=None, registry=None, pressure=False, snapshot=(740*M,730*M), test_scope=None):
        reg=registry or {'version':1,'apps':{},'projects':{}}
        with patch.object(m,'host_memory',return_value=(32000*M,available)),patch.object(m,'database_inventory',return_value=({DB},self.profile,{APP:DEP},busy or set())),patch.object(m,'read_json',return_value=reg),patch.object(m,'scope',return_value=test_scope or pathlib.Path('/scope')),patch.object(m,'counters',return_value=snapshot),patch.object(m,'pressure',return_value=pressure),patch.object(m,'ancestor_headroom',return_value=20000*M):
            return m.cycle(self.config,[self.engine],state or {},now=2000)

    def test_growth_is_verified_and_remembered(self):
        state,report=self.cycle()
        self.assertEqual(len(self.updates),1)
        self.assertEqual(state[CID]['baseline']['Memory'],768*M)
        self.assertEqual(report['resources'][0]['decision'],'grow')

    def test_host_reserve_blocks_growth(self):
        _,report=self.cycle(available=4800*M)
        self.assertEqual(self.updates,[])
        self.assertEqual(report['resources'][0]['decision'],'server-reserve')

    def test_pressure_does_not_freeze_active_service_with_plenty_of_ram(self):
        self.cycle(pressure=True)
        self.assertEqual(len(self.updates),1)

    def test_pressure_with_little_headroom_still_blocks_growth(self):
        self.cycle(pressure=True,available=5300*M)
        self.assertEqual(self.updates,[])

    def test_database_does_not_shrink_an_idle_learned_cap(self):
        self.engine.kind='databases'
        self.item.update(name=DB,mounts=[('/instances/'+DB,'/home/pockethost')])
        self.item['labels']={'pockethost.database.memory':'adaptive'}
        self.item['host']={'Memory':8192*M,'MemorySwap':-1,'MemoryReservation':0}
        state,report=self.cycle({CID:{'lastApplied':8192*M,'lowSince':0,'changedAt':0}})
        self.assertEqual(self.updates,[])
        self.assertEqual(state['database:'+DB]['memoryBytes'],8192*M)
        self.assertEqual(report['resources'][0]['allocatedBytes'],8192*M)

    def test_recreated_database_reuses_previous_learned_cap(self):
        self.engine.kind='databases'
        self.item.update(name=DB,mounts=[('/instances/'+DB,'/home/pockethost')])
        self.item['labels']={'pockethost.database.memory':'adaptive'}
        self.item['host']={'Memory':4096*M,'MemorySwap':-1,'MemoryReservation':0}
        state,report=self.cycle({'database:'+DB:{'memoryBytes':8192*M}})
        self.assertEqual(self.updates,[8192*M])
        self.assertEqual(state['database:'+DB]['memoryBytes'],8192*M)

    def test_unused_dedicated_database_ram_is_not_shared_with_web(self):
        with patch.object(m,'load_policy',return_value={DB:32*1024*M}):
            with self.assertRaises(ValueError):self.cycle()
        # 16 GiB reserved on a 32 GiB host, while 20 GiB remain available:
        # physical RAM looks ample, but the unconsumed commitment leaves no growth budget.
        with patch.object(m,'load_policy',return_value={DB:16384*M}):
            self.cycle(available=20000*M)
        self.assertEqual(self.updates,[])

    def test_limit_pressure_grows_even_after_short_peak_has_subsided(self):
        with tempfile.TemporaryDirectory()as t:
            p=pathlib.Path(t);(p/'memory.events').write_text('max 10\noom 0\n')
            (p/'memory.peak').write_text(str(760*M))
            _,report=self.cycle(snapshot=(200*M,180*M),test_scope=p)
            self.assertGreaterEqual(self.updates[0],1792*M)
            self.assertEqual(report['resources'][0]['decision'],'grow')

    def test_explicit_project_budget_blocks_growth(self):
        reg={'version':1,'apps':{APP:'project'},'projects':{'project':{'resources':{'enabled':True,'memoryMiB':4096}}}}
        _,report=self.cycle(registry=reg)
        self.assertEqual(self.updates,[])
        self.assertEqual(report['resources'][0]['decision'],'project-budget')

    def test_manual_docker_change_is_preserved(self):
        _,report=self.cycle({CID:{'lastApplied':1024*M}})
        self.assertEqual(self.updates,[])
        self.assertEqual(report['resources'][0]['decision'],'external-override')

    def test_fixed_app_and_helpers_are_not_managed(self):
        self.profile[APP]['allocation']='fixed'
        self.assertEqual(self.cycle()[1]['resources'],[])
        self.profile[APP]['allocation']='adaptive'
        self.item['name']+='-private-db'
        self.assertEqual(self.cycle()[1]['resources'],[])

    def test_old_or_unpublished_containers_are_not_managed(self):
        self.item['labels']['pockethost.web.deployment']='z'*15
        self.assertEqual(self.cycle()[1]['resources'],[])

    def test_database_mount_and_controller_membership_required(self):
        item=copy.deepcopy(self.item);item.update(name=DB,mounts=[('/instances/'+DB,'/home/pockethost')])
        self.assertEqual(m.ownership(item,'databases',{DB},{},{},'/instances'),DB)
        self.assertIsNone(m.ownership(item,'databases',set(),{},{},'/instances'))
        item['mounts']=[('/other','/home/pockethost')]
        self.assertIsNone(m.ownership(item,'databases',{DB},{},{},'/instances'))

    def test_database_restore_and_existing_manual_limit_are_preserved(self):
        self.engine.kind='databases'
        self.item.update(name=DB,mounts=[('/instances/'+DB,'/home/pockethost')])
        self.item['host']={'Memory':0,'MemorySwap':0,'MemoryReservation':0}
        _,report=self.cycle(busy={DB})
        self.assertEqual(self.updates,[])
        self.assertEqual(report['resources'][0]['decision'],'backup-or-restore')
        self.item['host']['Memory']=2048*M
        _,report=self.cycle()
        self.assertEqual(self.updates,[])
        self.assertEqual(report['resources'][0]['decision'],'fixed-database-limit')

    def test_database_policy_sets_a_safe_first_limit(self):
        self.engine.kind='databases'
        self.item.update(name=DB,mounts=[('/instances/'+DB,'/home/pockethost')])
        self.item['host']={'Memory':0,'MemorySwap':0,'MemoryReservation':0}
        _,report=self.cycle()
        self.assertEqual(len(self.updates),1)
        self.assertGreater(self.updates[0],740*M)
        self.assertEqual(report['resources'][0]['decision'],'initial')

    def test_explicit_operator_approval_reenables_adaptive_mode_without_container_restart(self):
        self.engine.kind='databases'
        self.item.update(name=DB,mounts=[('/instances/'+DB,'/home/pockethost')],labels={'pockethost.database.memory':'fixed'})
        self.item['host']={'Memory':4096*M,'MemorySwap':-1,'MemoryReservation':0}
        old={CID:{'externalOverride':True,'lastApplied':2048*M,'baseline':{'Memory':2048*M,'MemorySwap':-1,'MemoryReservation':0}}}
        with patch.object(m,'load_database_management',return_value={DB:{'mode':'adaptive','maximumMiB':8192,'revision':'a'*32}}):
            state,report=self.cycle(state=old,snapshot=(3800*M,3700*M))
        self.assertEqual(report['resources'][0]['decision'],'grow')
        self.assertEqual(state[CID]['managementRevision'],'a'*32)
        self.assertNotIn('externalOverride',state[CID]);self.assertGreater(self.updates[0],4096*M)

    def test_fixed_reservation_always_wins_over_adaptive_labels(self):
        self.engine.kind='databases'
        self.item.update(name=DB,mounts=[('/instances/'+DB,'/home/pockethost')],labels={'pockethost.database.memory':'adaptive'})
        self.item['host']={'Memory':4096*M,'MemorySwap':-1,'MemoryReservation':4096*M}
        with patch.object(m,'load_policy',return_value={DB:4096*M}):
            _,report=self.cycle(snapshot=(3800*M,3700*M))
        self.assertEqual(report['resources'][0]['decision'],'fixed-database-limit');self.assertEqual(self.updates,[])

    def test_root_configuration_must_not_be_writable(self):
        with tempfile.TemporaryDirectory() as t:
            p=pathlib.Path(t)/'config';p.write_text('{}');p.chmod(0o666)
            with self.assertRaises(ValueError):m.read_json(p,trusted=True)


class InventoryLifetime(unittest.TestCase):
    def test_repeated_inventory_closes_every_connection(self):
        with tempfile.TemporaryDirectory() as t:
            p=pathlib.Path(t)/'inventory.sqlite3'
            db=sqlite3.connect(p)
            try:
                db.executescript('CREATE TABLE instances(id TEXT);CREATE TABLE web_runtime_profiles(appId TEXT,profile TEXT);CREATE TABLE web_apps(id TEXT,activeDeployment TEXT);CREATE TABLE instance_backups(instance TEXT,status TEXT,restoreState TEXT);')
            finally:db.close()
            connect=sqlite3.connect;opened=[]
            def track(*args,**kwargs):
                db=connect(*args,**kwargs);opened.append(db);return db
            with patch.object(m.sqlite3,'connect',side_effect=track):
                for _ in range(250):self.assertEqual(m.database_inventory(p),(set(),{},{},set()))
            for db in opened:
                with self.assertRaises(sqlite3.ProgrammingError):db.execute('SELECT 1')

    def test_failed_inventory_also_closes_connection(self):
        with tempfile.TemporaryDirectory() as t:
            p=pathlib.Path(t)/'incomplete.sqlite3';db=sqlite3.connect(p);db.close();connect=sqlite3.connect;opened=[]
            def track(*args,**kwargs):
                db=connect(*args,**kwargs);opened.append(db);return db
            with patch.object(m.sqlite3,'connect',side_effect=track):
                with self.assertRaises(sqlite3.OperationalError):m.database_inventory(p)
            with self.assertRaises(sqlite3.ProgrammingError):opened[0].execute('SELECT 1')


if __name__=='__main__':unittest.main()
