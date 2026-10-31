#!/usr/bin/env python3
"""Real tar/gzip/AES-GCM, with confined Docker and S3 transport fixtures."""
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import web_app_storage as storage
import web_app_backup_s3 as remote

spec=importlib.util.spec_from_file_location('s3fixtures',pathlib.Path(__file__).with_name('test-web-backup-s3.py'));fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
APP,DEP,JOB='a'*15,'b'*15,'c'*15
CID='9'*64;HELPER='sha256:'+'e'*64


def tar_bytes(files):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w')as archive:
        for name,data in files.items():
            member=tarfile.TarInfo(name);member.size=len(data);archive.addfile(member,io.BytesIO(data))
    return output.getvalue()


class Process:
    def __init__(self,owner,volume,load):
        self.owner,self.volume,self.load=owner,volume,load;self.returncode=None;self.pid=999999
        self.stdout=io.BytesIO(tar_bytes(owner.volumes[volume]['files']))if not load else None
        self.stdin=fixtures.Sink()if load else None
    def poll(self):return self.returncode
    def wait(self,timeout=None):
        if self.returncode is None:
            if self.load:
                with tarfile.open(fileobj=io.BytesIO(self.stdin.saved))as archive:self.owner.volumes[self.volume]['files']={member.name:archive.extractfile(member).read()for member in archive if member.isfile()}
            self.returncode=0
        return self.returncode


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name).resolve();self.remote=fixtures.FakeS3();self.running=True;self.calls=[];self.processes=[]
        self.volume='ph-web-data-'+APP+'-uploads-v1';self.files={'db.sqlite':b'private database bytes','folder/upload.txt':b'hello'}
        self.volumes={self.volume:{'Name':self.volume,'Driver':'local','Options':None,'Labels':{'pockethost.web.app':APP,'pockethost.web.volume':'uploads'},'files':dict(self.files)}}
        storage._save(self.root,APP,{'version':1,'appId':APP,'volumes':{'uploads':{'name':'uploads','activeVolume':self.volume,'target':'/data','sizeMiB':16,'readOnly':False}}})
        self.job={'id':JOB,'appId':APP,'name':'uploads','lease':'lease','operation':'backup'}
        self.patches=[patch.dict(os.environ,{**fixtures.ENV,'WEB_APPS_STORAGE_HELPER_IMAGE':HELPER}),patch.object(remote,'_client',return_value=self.remote),patch.object(remote,'_docker_command',return_value=['/usr/bin/docker','--host','unix:///run/user/1001/docker.sock','image','load']),patch.object(storage.subprocess,'Popen',side_effect=self.popen),patch.object(remote,'_kill',side_effect=lambda process:setattr(process,'returncode',-15)if process.returncode is None else None),patch.object(remote.shutil,'disk_usage',return_value=SimpleNamespace(free=100*1024**3))]
        for item in self.patches:item.start()
    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.temp.cleanup()
    def api(self,path,body):return {'ok':True}
    def docker(self,args,**kwargs):
        self.calls.append(args)
        if args[:2]==['image','inspect']:return json.dumps([{'Id':HELPER}])
        if args[:2]==['volume','inspect']:
            if args[2]not in self.volumes:raise subprocess.CalledProcessError(1,args)
            return json.dumps([{key:value for key,value in self.volumes[args[2]].items()if key!='files'}])
        if args[:2]==['volume','create']:
            volume=args[-1];self.volumes[volume]={'Name':volume,'Driver':'local','Options':None,'Labels':{'pockethost.web.app':APP,'pockethost.web.volume':'uploads'},'files':{}};return volume
        if args[:2]==['volume','rm']:self.volumes.pop(args[2],None);return ''
        if args[0]=='ps':return(CID if '--no-trunc'in args else CID[:12])if self.running else ''
        if args[0]=='inspect':return json.dumps([{'Id':CID,'Name':'/ph-web-'+APP+'-'+DEP,'State':{'Running':self.running},'Config':{'User':'10001:10001','Labels':{'pockethost.web':'true','pockethost.web.app':APP}},'HostConfig':{'ReadonlyRootfs':True,'PortBindings':{}},'NetworkSettings':{'Ports':{}},'Mounts':[{'Type':'volume','Name':self.volume}]}])
        if args[0]=='stop':self.running=False;return CID
        if args[0]=='start':self.running=True;return CID
        if args[0]=='run':return '1 /volume'if'du'in args else ''
        raise AssertionError(args)
    def popen(self,args,**kwargs):
        mount=args[args.index('--mount')+1];volume=mount.split('source=')[1].split(',')[0];load=args[-2:]==['-xf','-']
        self.assertTrue(kwargs['start_new_session']);self.assertIn('--read-only',args);self.assertIn('--network',args);self.assertIn('none',args);self.assertNotIn('type=bind',mount)
        if load:self.assertIn('--interactive',args,'Docker run must forward the authenticated tar stream to extractor stdin')
        process=Process(self,volume,load);self.processes.append(process);return process
    def backup(self):return storage.snapshot(self.root,self.job,self.docker,self.api)
    def restore_job(self,backup):return{**self.job,'id':'d'*15,'operation':'restore','backup':backup}
    def test_named_mounts_never_accept_host_paths_or_overlapping_targets(self):
        for value in [[{'name':'uploads','target':'/etc','sizeMiB':16}],[{'name':'uploads','target':'/data/../etc'}],[{'name':'uploads','target':'/data'},{'name':'other','target':'/data/x'}]]:
            with self.assertRaises(RuntimeError):storage.validate_volumes(value)
        mounts=storage.prepare_mounts(self.root,APP,[{'name':'uploads','target':'/data','sizeMiB':16,'readOnly':False}],None,self.docker)
        self.assertEqual(mounts,[{'Type':'volume','Source':self.volume,'Target':'/data','ReadOnly':False,'VolumeOptions':{'NoCopy':True}}])
        self.volumes[self.volume]['Options']={'device':'/host','type':'none','o':'bind'}
        with self.assertRaises(RuntimeError):storage.prepare_mounts(self.root,APP,[{'name':'uploads','target':'/data','sizeMiB':16}],None,self.docker)
    def test_real_crypto_snapshot_pauses_and_resumes_only_own_site(self):
        result=self.backup();self.assertTrue(self.running)
        body=self.remote.objects[result['object']]['Body'];self.assertEqual(body[:5],b'PHWV1');self.assertNotIn(self.files['db.sqlite'],body)
        self.assertEqual(result['dataBytes'],sum(map(len,self.files.values())));self.assertTrue(any(args[0]=='stop'for args in self.calls));self.assertTrue(any(args[0]=='start'for args in self.calls))
    def test_restore_uses_new_volume_after_authenticated_get_and_rollback_keeps_old_data(self):
        backup=self.backup();result=storage.restore(self.root,self.restore_job(backup),self.docker,self.api)
        self.assertFalse(self.running);self.assertEqual(storage._load(self.root,APP)['volumes']['uploads']['activeVolume'],result['activeVolume'])
        self.assertEqual(self.volumes[result['activeVolume']]['files'],self.files);self.assertEqual(self.volumes[self.volume]['files'],self.files)
        self.assertEqual(self.remote.gets,2)
        storage.rollback_restore(self.root,APP,{**result,'name':'uploads'},self.docker)
        self.assertTrue(self.running);self.assertEqual(storage._load(self.root,APP)['volumes']['uploads']['activeVolume'],self.volume)
    def test_tamper_is_refused_before_any_extract_or_service_pause(self):
        backup=self.backup();value=self.remote.objects[backup['object']];value['Body']=value['Body'][:-1]+bytes([value['Body'][-1]^1]);before=len(self.processes)
        with self.assertRaises(Exception):storage.restore(self.root,self.restore_job(backup),self.docker,self.api)
        self.assertTrue(self.running);self.assertEqual(len(self.processes),before);self.assertEqual(storage._load(self.root,APP)['volumes']['uploads']['activeVolume'],self.volume)
    def test_escaping_tar_members_are_refused_and_site_resumes(self):
        self.volumes[self.volume]['files']={'../outside':b'bad'}
        with self.assertRaises(RuntimeError):self.backup()
        self.assertTrue(self.running);self.assertTrue(any(name=='abort'for name,_ in self.remote.calls))
    def test_insufficient_restore_disk_never_starts_extract_or_pauses_services(self):
        backup=self.backup();before=len(self.processes)
        with patch.object(remote.shutil,'disk_usage',return_value=SimpleNamespace(free=remote.MIN_FREE)):
            with self.assertRaises(BackupS3Error):storage.restore(self.root,self.restore_job(backup),self.docker,self.api)
        self.assertTrue(self.running);self.assertEqual(len(self.processes),before)
    def test_restart_journal_resumes_interrupted_services_and_never_other_app(self):
        self.running=False;storage._journal(self.root,self.job,pausedContainers=[CID])
        def api(path,body):return{'status':'running'}if body.get('inspect')else{'ok':True}
        self.assertTrue(storage.tick(self.root,api,self.docker));self.assertTrue(self.running);self.assertFalse((self.root/'storage-operation.json').exists())
        with self.assertRaises(RuntimeError):storage.rollback_restore(self.root,APP,{'name':'uploads','activeVolume':'ph-web-data-'+('z'*15)+'-uploads-r'+JOB,'previousVolume':self.volume,'previousContainers':[CID]},self.docker)
    def test_snapshot_deletion_is_exact_and_does_not_touch_active_volume(self):
        backup=self.backup();result=storage._delete_snapshot(self.job)
        self.assertTrue(result['deleted']);self.assertNotIn(backup['object'],self.remote.objects);self.assertIn(self.volume,self.volumes)
    def test_snapshot_deletion_keeps_objects_of_a_recovery_capture_prefix(self):
        with patch.dict(os.environ,{'WEB_BACKUPS_S3_PREFIX':'app1/platform-recovery/'+'c'*32+'/refs/web'}):
            backup=self.backup();before=dict(self.remote.objects);self.assertTrue(storage._delete_snapshot(self.job)['deleted'])
        self.assertIn(backup['object'],before);self.assertEqual(self.remote.objects,before);self.assertFalse(any(name=='delete'for name,_ in self.remote.calls))
    def test_snapshot_deletion_refuses_a_mistyped_bucket_instead_of_reporting_deleted(self):
        backup=self.backup();before=dict(self.remote.objects);self.remote.bucket_missing=True
        with self.assertRaises(remote.BackupS3Error):storage._delete_snapshot(self.job)
        self.assertEqual(self.remote.objects,before);self.assertFalse(any(name=='delete'for name,_ in self.remote.calls))
    def test_dynamic_port_or_foreign_service_is_rejected_before_any_service_stops(self):
        original=self.docker
        def dynamic(args,**kwargs):
            if args[0]=='inspect':
                info=json.loads(original(args,**kwargs))[0];info['HostConfig']['PortBindings']={'8080/tcp':[{'HostIp':'127.0.0.1','HostPort':''}]};info['NetworkSettings']['Ports']={'8080/tcp':[{'HostIp':'127.0.0.1','HostPort':'32787'}]};return json.dumps([info])
            return original(args,**kwargs)
        with self.assertRaisesRegex(RuntimeError,'dynamique'):
            with storage.quiesce(dynamic,APP):pass
        self.assertFalse(any(args[0]=='stop'for args in self.calls));self.assertTrue(self.running)
        def foreign(args,**kwargs):
            if args[0]=='inspect':
                info=json.loads(original(args,**kwargs))[0];info['Config']['Labels']['pockethost.web.app']='z'*15;return json.dumps([info])
            return original(args,**kwargs)
        with self.assertRaises(RuntimeError):
            with storage.quiesce(foreign,APP):pass
        self.assertFalse(any(args[0]=='stop'for args in self.calls))
    def test_quiesce_journals_before_stop_so_a_crash_can_always_resume_the_same_cid(self):
        recorded=[]
        with storage.quiesce(self.docker,APP,journal=lambda paused:recorded.append((list(paused),self.running))):self.assertFalse(self.running)
        self.assertEqual(recorded,[([CID],True)]);self.assertTrue(self.running)
    def test_resume_waits_for_http_on_the_same_explicit_port_and_refuses_a_changed_binding(self):
        import http.server,threading,time
        ready=threading.Event();paths=[];status=[200]
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(handler):paths.append(handler.path);handler.send_response(status[0] if ready.is_set()and handler.path=='/ready'else 503);handler.end_headers()
            def log_message(*args):pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);port=str(server.server_port);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=json.loads(self.docker(['inspect',CID]))[0];base['HostConfig']['PortBindings']={'8080/tcp':[{'HostIp':'127.0.0.1','HostPort':port}]};base['NetworkSettings']['Ports']={'8080/tcp':[{'HostIp':'127.0.0.1','HostPort':port}]}
        base['Config']['Labels']['pockethost.web.healthpath']='/ready';timer=threading.Timer(.35,ready.set);timer.start();started=time.monotonic()
        try:
            storage._wait_resumed(lambda *args,**kwargs:json.dumps([base]),APP,CID,['http://127.0.0.1:'+port],timeout=2)
            self.assertGreaterEqual(time.monotonic()-started,.35)
            self.assertEqual(set(paths),{'/ready'})
            status[0]=404
            with self.assertRaises(RuntimeError):storage._wait_resumed(lambda *args,**kwargs:json.dumps([base]),APP,CID,['http://127.0.0.1:'+port],timeout=.25)
            status[0]=200
            base['NetworkSettings']['Ports']['8080/tcp'][0]['HostPort']=str(int(port)+1)
            with self.assertRaises(RuntimeError):storage._wait_resumed(lambda *args,**kwargs:json.dumps([base]),APP,CID,['http://127.0.0.1:'+port],timeout=.2)
        finally:timer.cancel();server.shutdown();server.server_close();thread.join(1)


from web_app_backup_s3 import BackupS3Error
if __name__=='__main__':unittest.main()
