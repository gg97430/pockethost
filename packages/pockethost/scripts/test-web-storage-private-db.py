#!/usr/bin/env python3
"""Private-wrapper snapshot regression; synthetic Docker plus real tar/AES fixtures."""
import copy
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import web_app_storage as storage
from web_app_private_db import SCRIPT_ROOT

HERE=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('volume_fixtures',HERE/'test-web-storage.py')
fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
APP,DEP,CID=fixtures.APP,fixtures.DEP,fixtures.CID
SIDE='8'*64


def wrapped(info,root,service='web',front=True):
    info=copy.deepcopy(info)
    info['Name']='/ph-web-'+APP+'-'+DEP+(''if front else'-'+service)
    info['Config']['Labels'].update({'pockethost.web.deployment':DEP,'pockethost.web.service':service,
        'pockethost.web.front':'true'if front else'false','pockethost.web.private-db':'false','pockethost.web.private-db.wait':'true'})
    home=root/'private-db-runtime'/APP/(DEP+'-'+service)
    pairs=[(home/'ready','/run/kolab-ready'),(home/'wait-env','/run/kolab-wait-env'),(SCRIPT_ROOT/'web_private_db_wait.cjs','/run/kolab-wait.cjs')]
    for path,_ in pairs[:2]:path.mkdir(parents=True,exist_ok=True)
    info['HostConfig']['Mounts']=[{'Type':'bind','Source':str(path),'Target':target,'ReadOnly':True}for path,target in pairs]
    info['Mounts']=[{'Type':'bind','Source':str(path),'Destination':target,'RW':False}for path,target in pairs]
    return info


def sidecar():
    return {'Id':SIDE,'Config':{'Labels':{'pockethost.web':'true','pockethost.web.app':APP,'pockethost.web.private-db':'true'}},
        'HostConfig':{'Binds':['/host:/run/kolab-db.sock:ro']},'Mounts':[{'Type':'bind','Source':'/host','Destination':'/run/kolab-db.sock','RW':False}]}


class PrivateStorageTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.StorageTests('test_real_crypto_snapshot_pauses_and_resumes_only_own_site')
        self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.root=self.fixture.root
        self.env=mock.patch.dict('os.environ',{'WEB_APPS_ROOT':str(self.root)})
        self.env.start();self.addCleanup(self.env.stop)

    def info(self):return json.loads(self.fixture.docker(['inspect',CID]))[0]

    def docker(self,args,**kwargs):
        if args[0]=='ps':return CID+' '+SIDE if self.fixture.running else SIDE
        if args==['inspect',SIDE]:return json.dumps([sidecar()])
        if args==['inspect',CID]:return json.dumps([wrapped(self.info(),self.root)])
        if args[0]in('start','stop')and args[-1]==SIDE:self.fail('Storage must never stop or resume a private sidecar')
        return self.fixture.docker(args,**kwargs)

    def test_three_exact_readonly_mounts_accept_front_auxiliary_and_historical_next(self):
        for service,front in [('app',True),('typesense',False),('web',True)]:
            info=wrapped(self.info(),self.root,service,front)
            self.assertEqual(storage.restartable_container(info,APP),[])
        info=wrapped(self.info(),self.root)
        info['Config']['Labels'].pop('pockethost.web.service');info['Config']['Labels'].pop('pockethost.web.front')
        self.assertEqual(storage.restartable_container(info,APP),[])

    def test_foreign_root_service_deployment_and_noncanonical_sources_are_refused(self):
        base=wrapped(self.info(),self.root,'app')
        def change_label(info,key,value):info['Config']['Labels'][key]=value
        mutations=[lambda v:change_label(v,'pockethost.web.service','typesense'),
                   lambda v:change_label(v,'pockethost.web.deployment','z'*15),
                   lambda v:change_label(v,'pockethost.web.app','z'*15),
                   lambda v:v['Mounts'][0].update(Source=v['Mounts'][0]['Source']+'/../ready'),
                   lambda v:v['HostConfig']['Mounts'][0].update(Source='/other/private-db-runtime/'+APP+'/'+DEP+'-app/ready')]
        for mutate in mutations:
            info=copy.deepcopy(base);mutate(info)
            with self.assertRaises(RuntimeError):storage.restartable_container(info,APP)
        with mock.patch.dict('os.environ',{'WEB_APPS_ROOT':str(self.root/'other')}):
            with self.assertRaises(RuntimeError):storage.restartable_container(base,APP)

    def test_writable_missing_extra_duplicate_and_legacy_binds_are_all_refused(self):
        base=wrapped(self.info(),self.root)
        changes=[lambda v:v['Mounts'][0].update(RW=True),lambda v:v['HostConfig']['Mounts'][0].update(ReadOnly=False),
                 lambda v:v['Mounts'].pop(),lambda v:v['HostConfig']['Mounts'].pop(),
                 lambda v:v['Mounts'].append({'Type':'bind','Source':'/run/secret','Destination':'/run/kolab-db.sock','RW':False}),
                 lambda v:v['HostConfig']['Mounts'].append({'Type':'bind','Source':'/run/token','Target':'/run/token','ReadOnly':True}),
                 lambda v:v['Mounts'][0].update(Source='/unknown',Destination=None),
                 lambda v:v['HostConfig']['Mounts'][0].update(Source='/unknown',Target=None),
                 lambda v:v['Mounts'].__setitem__(1,copy.deepcopy(v['Mounts'][0])),
                 lambda v:v['HostConfig'].update(Binds=['/host:/run/kolab-ready:ro']),
                 lambda v:v['Config']['Labels'].update({'pockethost.web.private-db.wait':'false'}),
                 lambda v:v.update(Mounts=[]),lambda v:v['HostConfig'].update(Mounts=[])]
        for change in changes:
            info=copy.deepcopy(base);change(info)
            with self.assertRaises(RuntimeError):storage.restartable_container(info,APP)

    def test_symlinked_private_directory_is_refused(self):
        info=wrapped(self.info(),self.root)
        path=pathlib.Path(info['Mounts'][0]['Source']);path.rename(path.with_name('outside'))
        path.symlink_to(path.with_name('outside'),target_is_directory=True)
        with self.assertRaises(RuntimeError):storage.restartable_container(info,APP)

    def test_real_crypto_snapshot_ignores_sidecar_and_journals_only_parent(self):
        result=storage.snapshot(self.root,self.fixture.job,self.docker,self.fixture.api)
        self.assertTrue(self.fixture.running)
        journal=json.loads((self.root/'storage-operation.json').read_text())
        self.assertEqual(journal['pausedContainers'],[CID])
        self.assertEqual(result['dataBytes'],sum(map(len,self.fixture.files.values())))
        self.assertEqual([args[-1]for args in self.fixture.calls if args[0]=='stop'],[CID])
        self.assertEqual([args[-1]for args in self.fixture.calls if args[0]=='start'],[CID])

    def test_failed_snapshot_and_journal_recovery_resume_only_parent(self):
        with self.assertRaisesRegex(RuntimeError,'fixture failure'):
            with storage.quiesce(self.docker,APP):raise RuntimeError('fixture failure')
        self.assertTrue(self.fixture.running)
        self.fixture.running=False;storage._journal(self.root,self.fixture.job,pausedContainers=[CID])
        api=lambda _path,body:{'status':'running'}if body.get('inspect')else{'ok':True}
        self.assertTrue(storage.tick(self.root,api,self.docker));self.assertTrue(self.fixture.running)
        self.assertFalse((self.root/'storage-operation.json').exists())
        with self.assertRaises(RuntimeError):storage._resume(self.docker,APP,[SIDE])

    def test_all_parents_start_before_any_health_wait(self):
        other='7'*64;events=[]
        def docker(args,**kwargs):
            if args[0]=='inspect':
                info=wrapped(self.info(),self.root,'app'if args[1]==CID else'typesense',args[1]==CID)
                info['Id']=args[1];info['State']['Running']=False;return json.dumps([info])
            if args[0]=='start':events.append(('start',args[1]));return args[1]
            self.fail(args)
        def wait(_docker,_app,ident,_targets):
            self.assertEqual(events[:2],[('start',CID),('start',other)]);events.append(('wait',ident))
        with mock.patch.object(storage,'_wait_resumed',side_effect=wait):storage._resume(docker,APP,[CID,other])
        self.assertEqual(events[-2:],[('wait',CID),('wait',other)])

    def test_foreign_sidecar_or_wrong_role_does_not_bypass_validation(self):
        for value in ('false','TRUE',True):
            info=sidecar();info['Config']['Labels']['pockethost.web.private-db']=value
            docker=lambda args,**kwargs:SIDE if args[0]=='ps'else json.dumps([info])
            with self.assertRaises(RuntimeError):
                with storage.quiesce(docker,APP):self.fail('Unexpected quiesce')
        info=sidecar();info['Config']['Labels']['pockethost.web.app']='z'*15
        with self.assertRaises(RuntimeError):
            with storage.quiesce(lambda args,**kwargs:SIDE if args[0]=='ps'else json.dumps([info]),APP):self.fail('Foreign helper accepted')


if __name__=='__main__':unittest.main()
