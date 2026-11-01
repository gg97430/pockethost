#!/usr/bin/env python3
"""Redeployment quiesce and relay reattachment; no real Docker/HTTP/BDD."""
import importlib.util
import json
import pathlib
import socket
import tempfile
import types
import unittest
from unittest import mock
import web_app_private_db as private
import web_app_runtime as runtime
import web_app_project_limits as projects
import web_app_storage as storage

HERE=pathlib.Path(__file__).resolve().parent
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,HERE/file)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
w=load('private_storage_worker','web-app-worker.py')
fixtures=load('private_storage_fixtures','test-web-storage-private-db.py')
APP,DEP,CID,SIDE=fixtures.APP,fixtures.DEP,fixtures.CID,fixtures.SIDE


class PrivateWorkerStorage(unittest.TestCase):
    def test_redeploy_quiesce_journals_and_stops_two_wrapped_parents_only(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=pathlib.Path(tmp).resolve();other='7'*64;calls=[]
            base={'Id':CID,'State':{'Running':True},'Config':{'User':'10001:10001','Labels':{'pockethost.web':'true','pockethost.web.app':APP}},
                  'HostConfig':{'ReadonlyRootfs':True},'NetworkSettings':{'Ports':{}}}
            parents={CID:fixtures.wrapped(base,root,'app'),other:fixtures.wrapped({**base,'Id':other},root,'typesense',False)}
            def docker(args,**kwargs):
                calls.append(args)
                if args[0]=='ps':return' '.join([SIDE,CID,other])
                if args[0]=='inspect':return json.dumps([fixtures.sidecar()if args[1]==SIDE else parents[args[1]]])
                if args[0]=='stop':return args[-1]
                self.fail(args)
            job=types.SimpleNamespace(app=APP,id=DEP,snapshot={'storage':[{'readOnly':False}]},value={'action':'deploy'})
            with mock.patch.object(w,'ROOT',root),mock.patch.object(w,'docker',side_effect=docker),mock.patch.dict('os.environ',{'WEB_APPS_ROOT':str(root)}):
                w.Job.quiesce_storage(job)
            self.assertEqual(job.recreate_previous,[CID,other])
            self.assertEqual(json.loads((root/'current-job.json').read_text())['recreatePrevious'],[CID,other])
            self.assertEqual([args[-1]for args in calls if args[0]=='stop'],[CID,other])

    def test_private_manager_recreates_relay_after_parent_stop_and_restart(self):
        with tempfile.TemporaryDirectory(prefix='kds-',dir='/tmp')as tmp:
            root=pathlib.Path(tmp).resolve();broker=root/'broker';broker.mkdir()
            peer=socket.socket(socket.AF_UNIX);peer.bind(str(broker/(APP+'.sock')));self.addCleanup(peer.close)
            row={'id':'d'*15,'appId':APP,'instanceId':'e'*15,'port':4825,'token':'Q'*64,'revision':'R'*24,'available':True}
            app={'Id':CID,'Config':{'Labels':{'pockethost.web':'true','pockethost.web.app':APP,'pockethost.web.deployment':DEP,
                'pockethost.web.service':'web','pockethost.web.private-db.wait':'true'},'Env':['KOLAB_DB_4825_URL=http://127.0.0.1:4825']},
                'State':{'Running':True,'StartedAt':'synthetic-first'}}
            state={'proxy':None,'created':0}
            def docker(args,**kwargs):
                if args[0]=='ps':return' '.join([CID]+([state['proxy']['Id']]if state['proxy']else[]))
                if args[0]=='inspect':
                    names=[args[3]]if args[1:3]==['--type','container']else args[1:]
                    output=[]
                    for name in names:
                        if name==CID:output.append(app)
                        elif state['proxy']and name in(state['proxy']['Id'],state['proxy']['Name']):output.append(state['proxy'])
                        else:raise RuntimeError('Synthetic container absent')
                    return json.dumps(output)
                if args[0]=='create':
                    state['created']+=1
                    labels={args[i+1].split('=',1)[0]:args[i+1].split('=',1)[1]for i,arg in enumerate(args)if arg=='--label'}
                    state['proxy']={'Id':str(state['created']+2)*64,'Name':args[args.index('--name')+1],'Config':{'Labels':labels},
                        'State':{'Running':False,'StartedAt':'proxy-'+str(state['created']),'Health':{'Status':'healthy'}}}
                    return state['proxy']['Id']
                if args[0]=='start':state['proxy']['State']['Running']=True;return state['proxy']['Id']
                if args[:2]==['rm','-f']:state['proxy']=None;return''
                self.fail('Unexpected Docker operation: '+repr(args))
            manager=private.PrivateDatabases('http://127.0.0.1:8091','synthetic-worker-secret','socket',root,docker)
            manager.api=mock.Mock(return_value={'version':1,'enabled':True,'applications':[APP],'bindings':[row]})
            manager.proxy_status=mock.Mock(return_value={'namespace':'net:[123456]'})
            manager.application_identity=mock.Mock(return_value='net:[123456]:100')
            manager.probe=mock.Mock(return_value=True)
            with mock.patch.object(private,'SOCKET_ROOT',broker),mock.patch.object(runtime,'admit'),mock.patch.object(projects,'admit'),mock.patch.object(storage,'_helper',return_value='trusted/helper@sha256:'+'3'*64):
                manager.refresh(force=True);self.assertTrue(manager.ensure(app,[row]));previous=state['proxy']['Id']
                app['State']['Running']=False;manager.tick();self.assertIsNone(state['proxy'])
                app['State'].update(Running=True,StartedAt='synthetic-restart');manager.tick()
            self.assertIsNotNone(state['proxy']);self.assertNotEqual(state['proxy']['Id'],previous)
            self.assertEqual(state['proxy']['Config']['Labels']['pockethost.web.private-db.started'],'synthetic-restart')
            home,_,ready=manager.paths(APP,DEP,'web')
            self.assertEqual((ready/'ready').read_text(),'net:[123456]:100')
            self.assertEqual(json.loads((home/'proof.json').read_text())['appStartedAt'],'synthetic-restart')


if __name__=='__main__':unittest.main()
