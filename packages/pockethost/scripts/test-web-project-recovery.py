#!/usr/bin/env python3
import hashlib
import importlib.util
import json
import pathlib
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import web_project_recovery as r
r_inventory=r.app_inventory

APP, DB, PROJECT, DEPLOY = 'a'*15, 'b'*15, 'c'*15, 'd'*15
IMAGE = 'sha256:'+'e'*64


class FakeDocker:
    def __init__(self, volume):
        self.volume = volume; self.calls = []
    def __call__(self, args, **_kwargs):
        self.calls.append(args)
        if args[:2] == ['ps','-q']: return 'f'*64
        if args[0] == 'inspect':
            return json.dumps([{'Id':'f'*64,'Image':IMAGE,'Config':{'User':'10001:10001','Env':['DATABASE_URL=http://127.0.0.1:4825','SMTP_PASSWORD=private-mail'],'Cmd':['node','server.js'],'Entrypoint':[], 'Labels':{'pockethost.web.app':APP,'pockethost.web.deployment':DEPLOY,'pockethost.web.service':'web','pockethost.web.front':'true'}},'HostConfig':{'PortBindings':{'3000/tcp':[{'HostIp':'127.0.0.1','HostPort':'24567'}]}},'Mounts':[{'Type':'volume','Name':'ph-web-data-'+APP+'-data-v1','Destination':'/data','RW':True}],'State':{'Running':True,'StartedAt':'unchanged'}}])
        if args[:2] == ['volume','inspect']: return json.dumps([{'Driver':'local','Options':None,'Labels':{'pockethost.web.app':APP},'Mountpoint':str(self.volume)}])
        if args[:2] == ['image','inspect']: return json.dumps([{'Id':IMAGE,'Config':{'Cmd':['node','server.js'],'Entrypoint':[]}}])
        raise AssertionError(args)
    def export(self, image, filename):
        self.calls.append(['save',image]); filename.write_bytes(b'synthetic-image-artifact')


class ProjectRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = pathlib.Path(self.temp.name).resolve()
        self.key = self.root/'recovery.key'; self.key.write_text('12'*32); self.key.chmod(0o600)
        self.data=self.root/'data'; self.binary=self.root/'binaries'; self.volume=self.root/'volumes'/('ph-web-data-'+APP+'-data-v1')/'_data'
        self.volume.mkdir(parents=True); (self.volume/'empty').mkdir(); (self.volume/'file').write_bytes(b'private-volume-content');(self.volume/'file').chmod(0o750)
        home=self.data/'instances'/DB/'pb_data';home.mkdir(parents=True)
        with sqlite3.connect(home/'data.db') as db:
            db.executescript('CREATE TABLE business(id TEXT,value TEXT);CREATE TABLE _params(key TEXT,value TEXT);')
            db.execute('INSERT INTO business VALUES(?,?)',('original','private-business-value'))
            db.execute('INSERT INTO _params VALUES(?,?)',('settings',json.dumps({'smtp':{'enabled':True},'backups':{'cron':'* * * * *'},'s3':{'enabled':False}})))
        binpath=self.binary/'0.39.11/linux_amd64';binpath.mkdir(parents=True);(binpath/'pocketbase').write_bytes(b'synthetic-binary')
        self.snapshot={'version':1,'projectId':PROJECT,'platform':'app4','revision':'a'*64,'apps':[{'id':APP,'deploymentId':DEPLOY,'bindings':[{'instanceId':DB,'port':4825}],'externalDependencies':[]}],'databases':[{'id':DB,'version':'0.39.11'}]}
        hooks=home.parent/'pb_hooks';hooks.mkdir();(hooks/'preserved.pb.js').write_text('throw must remain inert')
        sidecar=home/r.recovery.TOTP_SIDECAR;sidecar.write_bytes(b'fixture-native-mfa-key');sidecar.chmod(0o600)
        self.docker=FakeDocker(self.volume)
        self.inventory=patch.object(r,'app_inventory',wraps=lambda docker,app:r_inventory(docker,app,self.root/'volumes'));self.inventory.start()
        self.space=patch.object(r.recovery.shutil,'disk_usage',return_value=types.SimpleNamespace(free=1000*1024**3));self.space.start()
    def tearDown(self):self.inventory.stop();self.space.stop();self.temp.cleanup()
    def capture(self):
        path=self.root/'capsule.phpr'; result=r.capture(self.snapshot,self.data,self.binary,path,self.key,self.docker); return path,result
    def test_full_capsule_reconstructs_database_files_images_and_empty_volume_without_source_mutations(self):
        before=(self.data/'instances'/DB/'pb_data/data.db').read_bytes()
        path,result=self.capture()
        self.assertTrue(result['completeArtifacts']);self.assertFalse(result['applicationVerified'])
        self.assertNotIn(b'private-business',path.read_bytes());self.assertNotIn(b'private-volume',path.read_bytes())
        target=self.root/'restore';status=r.restore(path,self.key,target,result['sha256'])
        self.assertTrue(status['noServicesStarted']);self.assertTrue(status['paused'])
        self.assertEqual((target/'host-files/volumes'/APP/'web-0/file').read_bytes(),b'private-volume-content')
        self.assertEqual((target/'instances'/DB/'pb_data'/r.recovery.TOTP_SIDECAR).read_bytes(),b'fixture-native-mfa-key')
        self.assertEqual((target/'host-files/native-support'/DB/'pb_hooks/preserved.pb.js').read_text(),'throw must remain inert')
        self.assertTrue((target/'host-files/volumes'/APP/'web-0/empty').is_dir())
        self.assertEqual((target/'host-files/volumes'/APP/'web-0/file').stat().st_mode&0o777,0o750)
        with sqlite3.connect(target/'instances'/DB/'pb_data/data.db') as connection:
            self.assertEqual(connection.execute('SELECT value FROM business').fetchone()[0],'private-business-value')
            self.assertFalse(json.loads(connection.execute('SELECT value FROM _params').fetchone()[0])['smtp']['enabled'])
        self.assertEqual((self.data/'instances'/DB/'pb_data/data.db').read_bytes(),before)
        self.assertFalse(any(call[0] in ('stop','restart','pause','rm','update','start') for call in self.docker.calls))
    def test_daemon_cannot_redirect_root_volume_reads_to_private_host_paths(self):
        original=self.docker.__class__.__call__
        def lie(docker,args,**kwargs):
            if args[:2]==['volume','inspect']:return json.dumps([{'Driver':'local','Options':None,'Labels':{'pockethost.web.app':APP},'Mountpoint':str(self.key.parent)}])
            return original(docker,args,**kwargs)
        with patch.object(FakeDocker,'__call__',lie):
            with self.assertRaisesRegex(r.ProjectRecoveryError,'Point de volume'):self.capture()
        self.assertFalse((self.root/'capsule.phpr').exists())
    def test_sqlite_online_snapshot_bounds_pages_and_disk_during_backup(self):
        source=self.data/'instances'/DB/'pb_data/data.db'
        with patch.object(r,'LIMIT',1):
            with self.assertRaisesRegex(r.ProjectRecoveryError,'Snapshot SQLite trop volumineux'):
                r.sqlite_snapshot_child(source,self.root/'too-large.db')
        with patch.object(r.recovery,'require_space',side_effect=r.recovery.RecoveryError('disk reserve')):
            with self.assertRaisesRegex(r.recovery.RecoveryError,'disk reserve'):
                r.sqlite_snapshot_child(source,self.root/'disk-full.db')
    def test_symlink_and_mutable_files_fail_closed(self):
        (self.volume/'unsafe').symlink_to(self.key)
        with self.assertRaises(r.recovery.RecoveryError):self.capture()
        (self.volume/'unsafe').unlink()
        state=r.tree_state(self.volume)
        original=r.tree_state
        def changed(path):
            value=original(path)
            if pathlib.Path(path)==self.volume:value['file'][0]+=1
            return value
        with patch.object(r,'tree_state',side_effect=[state,changed(self.volume)]):
            with self.assertRaises(r.ProjectRecoveryError):r.copy_stable_tree(self.volume,self.root/'copy')
    def test_corrupt_transfers_and_changed_plans_never_materialize_destination(self):
        archive,result=self.capture(); manifest=r.recovery.inspect_archive(lambda:open(archive,'rb'),r.recovery.load_key(self.key))
        maps={key:[]for key in ('applications','databases','environment','domains','workers','scheduledTasks')}
        plan=r.migration_plan(manifest,'app5',maps,1000*1024**3)
        target=self.root/'import'
        bad={**plan,'target':'app3'}
        with self.assertRaises(r.ProjectRecoveryError):r.migration_import(archive,self.key,target,bad,result['sha256'])
        with self.assertRaises(r.ProjectRecoveryError):r.migration_import(archive,self.key,target,plan,'0'*64)
        self.assertFalse(target.exists())
        done=r.migration_import(archive,self.key,target,plan,result['sha256'])
        self.assertFalse(done['cutoverExecuted']);self.assertTrue(done['sourcePreserved']);self.assertTrue(done['paused'])
    def test_external_dependencies_refused_instead_of_false_complete(self):
        self.snapshot['apps'][0]['externalDependencies']=['R2_BUCKET']
        with self.assertRaises(r.ProjectRecoveryError):self.capture()
    def test_runtime_environment_delivery_secrets_removed_and_namespace_never_has_host_port_or_original_mount(self):
        env=r.safe_environment(['SMTP_PASSWORD=secret','R2_TOKEN=x','PB_PASSWORD=fixture','NODE_OPTIONS=--require=evil'])
        self.assertIn('PB_PASSWORD=fixture',env);self.assertFalse(any('secret'in item or item=='R2_TOKEN=x' or item=='NODE_OPTIONS=--require=evil' for item in env))
        args=r.sandbox_base('kolab-drill-'+'a'*16+'-copy',IMAGE)
        self.assertIn('none',args);self.assertNotIn('--publish',args);self.assertIn('no-new-privileges',args)
    def test_aggregate_copy_budget_and_host_link_escape_are_refused(self):
        budget=r.CopyBudget(maximum=(self.volume/'file').stat().st_size+1)
        r.copy_stable_tree(self.volume,self.root/'first-copy',budget)
        with self.assertRaises(r.ProjectRecoveryError):r.copy_stable_tree(self.volume,self.root/'second-copy',budget)
        linked=self.root/'directory-link';linked.symlink_to(self.volume,target_is_directory=True)
        with self.assertRaises(OSError):r.digest(linked/'file')
        import os
        os.link(self.volume/'file',self.root/'hard-link')
        with self.assertRaises(r.ProjectRecoveryError):r.digest(self.root/'hard-link')
    def test_failed_docker_cleanup_retains_private_journal_and_blocks_future_rehearsals(self):
        import web_project_recovery_service as service
        run='q'*15;work=self.root/run;work.mkdir();restored=work/'restored';restored.mkdir()
        journal=restored/'sandbox-state.json';r.private_json(journal,{'runId':'a'*16,'containers':['kolab-drill-'+'a'*16+'-copy'],'volumes':[]})
        # process requires a new directory; restore creates the retained journal.
        import shutil
        shutil.rmtree(work)
        reports=[]
        def restore(*args,**kwargs):
            restored.mkdir();r.private_json(journal,{'runId':'a'*16,'containers':['kolab-drill-'+'a'*16+'-copy'],'volumes':[]})
        def api(path,body):reports.append((path,body));return{}
        with patch.object(service.engine,'capture',return_value={'sha256':'a'*64}),patch.object(service.engine,'restore',side_effect=restore),patch.object(service.engine,'sandbox_drill',side_effect=r.ProjectRecoveryError('cleanup failed')),patch.object(service.engine,'cleanup_sandbox',side_effect=r.ProjectRecoveryError('cleanup failed')),patch.object(service,'api',side_effect=api):
            service.process({'stateRoot':str(self.root),'sourceDockerSocket':'/run/user/1001/docker.sock','sandboxDockerSocket':'/run/user/1002/docker.sock','dataRoot':str(self.data),'binaryRoot':str(self.binary),'instanceAppRoot':str(self.root),'keyFile':str(self.key),'helperImage':IMAGE},{'id':run,'lease':'P'*80,'snapshot':self.snapshot})
        self.assertTrue(journal.exists());self.assertTrue((self.root/'cleanup-pending.json').exists())
        self.assertEqual(reports[-1][1]['status'],'failed');self.assertFalse(reports[-1][1]['result']['cleanedUp'])
        self.assertEqual(json.loads((self.root/'status.json').read_text())['runId'],run)
    def test_private_environment_only_uses_unix_body_and_copy_api_refuses_host_network(self):
        from unittest.mock import MagicMock
        args=r.sandbox_base('kolab-drill-'+'a'*16+'-web',IMAGE)+['--entrypoint','/usr/local/bin/node',IMAGE,'server.js']
        connection=MagicMock();response=MagicMock();response.status=201;response.read.return_value=json.dumps({'Id':'f'*64}).encode();connection.getresponse.return_value=response
        with patch.object(r.http.client,'HTTPConnection',return_value=connection),patch.object(r.subprocess,'run',side_effect=AssertionError('No secret CLI')):
            r.Docker('/run/user/1002/docker.sock').create_sandbox(args,['PB_PASSWORD=private-fixture-value'])
        request=connection.request.call_args
        self.assertNotIn('private-fixture-value',request.args[1]);body=json.loads(request.kwargs['body'])
        self.assertEqual(body['Env'],['PB_PASSWORD=private-fixture-value']);self.assertFalse(body['HostConfig']['PublishAllPorts']);self.assertEqual(body['HostConfig']['PortBindings'],{})
        self.assertNotIn('private-fixture-value',str(args))
        bad=list(args);bad[bad.index('none')]='host'
        with self.assertRaises(r.ProjectRecoveryError):r.Docker('/run/user/1002/docker.sock').create_sandbox(bad,[])
        with self.assertRaises(r.ProjectRecoveryError):r.Docker('/run/user/1001/docker.sock').create_sandbox(args,[])
    def test_lost_terminal_ack_keeps_marker_then_reconciles_without_waiting_for_lease(self):
        import web_project_recovery_service as service
        run='q'*15
        config={'stateRoot':str(self.root),'sourceDockerSocket':'/run/user/1001/docker.sock','sandboxDockerSocket':'/run/user/1002/docker.sock','dataRoot':str(self.data),'binaryRoot':str(self.binary),'instanceAppRoot':str(self.root),'keyFile':str(self.key),'helperImage':IMAGE}
        job={'id':run,'lease':'P'*80,'snapshot':self.snapshot}
        with patch.object(service.engine,'capture',return_value={'sha256':'a'*64}),patch.object(service.engine,'restore'),patch.object(service.engine,'sandbox_drill',return_value={'runtimeStarted':True}),patch.object(service,'api',side_effect=OSError('lost response')),patch.object(service.time,'sleep'):
            with self.assertRaises(OSError):service.process(config,job)
        self.assertFalse((self.root/run).exists())
        self.assertEqual(json.loads((self.root/'status.json').read_text())['runId'],run)
        with patch.object(service,'api',return_value={})as api:
            service.reconcile(config)
        api.assert_called_once_with('claim',{'cleanupOnly':True,'recoverOrphans':True})
        self.assertEqual(json.loads((self.root/'status.json').read_text())['runId'],'')
    def test_low_capacity_and_expired_review_refused(self):
        archive,result=self.capture();manifest=r.recovery.inspect_archive(lambda:open(archive,'rb'),r.recovery.load_key(self.key))
        maps={key:[]for key in ('applications','databases','environment','domains','workers','scheduledTasks')}
        plan=r.migration_plan(manifest,'app5',maps,0)
        self.assertFalse(plan['capacityFits'])
        with self.assertRaises(r.ProjectRecoveryError):r.migration_import(archive,self.key,self.root/'no',plan,result['sha256'])
        plan=r.migration_plan(manifest,'app5',maps,1000*1024**3,now=0)
        with self.assertRaises(r.ProjectRecoveryError):r.migration_import(archive,self.key,self.root/'no',plan,result['sha256'])


if __name__=='__main__':unittest.main()
