#!/usr/bin/env python3
"""Compose quotas, public Docker ARG and health dependencies; synthetic Docker only."""
import contextlib
import copy
import json
import os
import pathlib
import tempfile
import types
import unittest
from unittest.mock import patch
import web_app_runtime as runtime

APP, DEP, CID = 'a' * 15, 'b' * 15, 'c' * 64
IMAGE = 'typesense/typesense@sha256:' + 'd' * 64


def profile():
    return {'kind': 'compose', 'compose': {'front': 'app', 'services': {
        'app': {'dockerfile': 'Dockerfile', 'resources': {'cpuMillicores': 500, 'memoryMiB': 768, 'pids': 256},
                'depends_on': {'typesense': {'condition': 'service_healthy'}}},
        'typesense': {'image': IMAGE, 'port': 8108, 'resources': {'cpuMillicores': 2000, 'memoryMiB': 2048, 'pids': 256},
                      'healthcheck': {'test': ['CMD', 'bash', '-c', 'exit 0'], 'startPeriodSeconds': 10}}
    }}}


class ComposeCompatibility(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def engine(self, docker, runtime_profile=None):
        job = types.SimpleNamespace(app=APP, id=DEP, snapshot={'runtime': runtime_profile or profile()}, heartbeat=lambda: None)
        return runtime.RuntimeEngine(job, docker, lambda *_args: None, self.root, '/run/user/1001/docker.sock')

    def info(self, health='starting', running=True, app=APP):
        return json.dumps([{'Id': CID, 'Config': {'Labels': {'pockethost.web.app': app, 'pockethost.web.deployment': DEP,
                                                           'pockethost.web.service': 'typesense'}},
                            'State': {'Running': running, 'Health': {'Status': health}}}])

    def test_typesense_budget_is_aggregate_bounded_and_ordinary_limits_unchanged(self):
        value = runtime.validate_profile(profile())
        self.assertEqual(value['resources'], {'cpuMillicores': 2500, 'memoryMiB': 2816, 'pids': 512})
        self.assertEqual(value['compose']['order'], ['typesense', 'app'])
        self.assertEqual(value['compose']['services']['typesense']['healthcheck']['StartPeriod'], 10 * 10**9)
        for resources in [{'cpuMillicores': 1001}, {'memoryMiB': 1537}]:
            with self.assertRaises(RuntimeError):
                runtime.validate_profile({'kind': 'dockerfile', 'resources': resources})
        for field, value in [('cpuMillicores', 2001), ('memoryMiB', 4097), ('pids', 257)]:
            source = profile()
            for item in source['compose']['services'].values():
                item['resources'] = {'cpuMillicores': 2000, 'memoryMiB': 4096, 'pids': 256}
            source['compose']['services']['app']['resources'][field] = value
            with self.assertRaises(RuntimeError):
                runtime.validate_profile(source)

    def test_default_list_dependencies_and_legacy_explicit_service_budgets_survive(self):
        source = profile()
        source['compose']['services']['app']['depends_on'] = ['typesense']
        value = runtime.validate_profile(source)
        self.assertEqual(value['compose']['services']['app']['depends_on'], ['typesense'])
        self.assertEqual(value['resources']['cpuMillicores'], 2500)
        value['compose']['services']['typesense'].pop('healthcheck')
        value['compose'].pop('order')  # internal Python ordering is not an API profile field
        self.assertEqual(runtime.validate_profile(value)['resources'], value['resources'])

    def test_invalid_and_one_shot_completion_conditions_are_refused(self):
        for condition in [{'condition': 'service_completed_successfully'}, {'condition': 'service_healthy', 'required': False},
                          {'other': 'service_healthy'}, 'service_healthy']:
            source = profile()
            source['compose']['services']['app']['depends_on']['typesense'] = condition
            with self.assertRaises(RuntimeError):
                runtime.validate_profile(source)
        source = profile()
        source['compose']['services']['typesense']['depends_on'] = {'app': {'condition': 'service_started'}}
        with self.assertRaises(RuntimeError):
            runtime.validate_profile(source)

    def test_public_build_args_are_name_only_values_in_client_env_and_secrets_stay_mount_only(self):
        (self.root / 'Dockerfile').write_text('FROM scratch\nARG NEXT_PUBLIC_SITE_URL\n')
        build_env = {'NEXT_PUBLIC_SITE_URL': {'value': 'https://site.test/?q=$literal\n"quoted"', 'build': True},
                     'NEXT_PUBLIC_EMPTY': {'value': '', 'build': True}, 'NEXT_PUBLIC_RUNTIME': {'value': 'runtime only', 'runtime': True},
                     'PRIVATE_API_KEY': {'value': 'synthetic-secret-value', 'build': True},
                     'NODE_OPTIONS': {'value': '--require /bad', 'build': True}}
        calls = []
        job = types.SimpleNamespace(app=APP, id=DEP, snapshot={'runtime': {'kind': 'dockerfile'}, 'env': build_env}, secrets=[],
                                    run=lambda args, **kwargs: calls.append((args, kwargs)))
        with patch.dict(os.environ, {'PATH': '/synthetic-bin'}, clear=True):
            engine=runtime.RuntimeEngine(job, lambda *_args: '', lambda *_args: None, self.root, '/socket')
            engine.hmac_key='synthetic-worker-secret-at-least-32'
            engine.build(self.root, self.root)
        args, options = calls[0]
        names = [args[index + 1] for index, value in enumerate(args) if value == '--build-arg']
        self.assertEqual(names, ['NEXT_PUBLIC_EMPTY', 'NEXT_PUBLIC_SITE_URL','BUILDKIT_CACHE_MOUNT_NS'])
        self.assertEqual(options['env']['NEXT_PUBLIC_SITE_URL'], build_env['NEXT_PUBLIC_SITE_URL']['value'])
        self.assertEqual(options['env']['NEXT_PUBLIC_EMPTY'], '')
        self.assertNotIn('PRIVATE_API_KEY', options['env'])
        self.assertNotIn('synthetic-secret-value', ' '.join(args))
        self.assertNotIn(engine.hmac_key,' '.join(args))
        self.assertNotIn(options['env']['BUILDKIT_CACHE_MOUNT_NS'],' '.join(args))
        self.assertIn(options['env']['BUILDKIT_CACHE_MOUNT_NS'],job.secrets)
        self.assertFalse(any(name + '=' in arg for name in names for arg in args))
        self.assertIn('--secret', args)
        self.assertEqual((self.root / 'runtime-build.env').stat().st_mode & 0o777, 0o600)

    def test_dockerfile_cache_namespace_is_per_app_and_full_rebuild_and_missing_key_fails(self):
        (self.root/'Dockerfile').write_text('FROM scratch\nRUN --mount=type=cache,id=pnpm-store,target=/cache echo synthetic\n')
        calls=[]
        job=types.SimpleNamespace(app=APP,id=DEP,snapshot={'runtime':{'kind':'dockerfile'},'env':{'BUILDKIT_CACHE_MOUNT_NS':{'value':'attacker-chosen','build':True}}},secrets=[],run=lambda args,**kwargs:calls.append((args,kwargs)))
        engine=runtime.RuntimeEngine(job,lambda *_a:'',lambda *_a:None,self.root,'/socket')
        with self.assertRaises(RuntimeError):engine.build(self.root,self.root)
        self.assertEqual(calls,[])
        engine.hmac_key='synthetic-worker-secret-at-least-32'
        with patch.dict(os.environ,{},clear=True):
            engine.build(self.root,self.root)
            normal=calls[-1][1]['env']['BUILDKIT_CACHE_MOUNT_NS']
            job.app='z'*15;engine.build(self.root,self.root)
            other=calls[-1][1]['env']['BUILDKIT_CACHE_MOUNT_NS']
            job.app=APP;job.snapshot['fullRebuild']=True;engine.build(self.root,self.root)
            fresh=calls[-1][1]['env']['BUILDKIT_CACHE_MOUNT_NS']
        self.assertNotEqual(normal,other)
        self.assertNotEqual(normal,fresh)
        self.assertNotEqual(normal,'attacker-chosen')

    def test_public_arg_invalid_nul_and_unscoped_values_never_forward(self):
        with self.assertRaises(RuntimeError):
            runtime.public_build_arguments({'NEXT_PUBLIC_BAD': {'value': 'bad\0value', 'build': True}})
        self.assertEqual(runtime.public_build_arguments({'NEXT_PUBLIC_BAD=OTHER': {'value': 'x', 'build': True},
                                                       'NEXT_PUBLIC_NO_SCOPE': {'value': 'x'}}), {})

    def test_health_dependency_waits_only_own_candidate_and_never_execs(self):
        calls, statuses = [], iter(['starting', 'healthy'])
        def docker(args, **kwargs):
            calls.append(args)
            return self.info(next(statuses))
        engine = self.engine(docker)
        item = engine.profile['compose']['services']['app']
        with patch.object(runtime.time, 'sleep'):
            engine.wait_dependencies(item, {'typesense': CID}, runtime.time.monotonic() + 10)
        self.assertEqual(calls, [['inspect', CID], ['inspect', CID]])
        with self.assertRaises(RuntimeError):
            self.engine(lambda *_args, **_kwargs: self.info('healthy', app='z' * 15)).wait_dependencies(item, {'typesense': CID}, runtime.time.monotonic() + 10)

    def test_failed_or_missing_health_and_shared_deadline_fail_closed(self):
        item = runtime.validate_profile(profile())['compose']['services']['app']
        for health, running in [('unhealthy', True), (None, True), ('healthy', False)]:
            with self.assertRaises(RuntimeError):
                self.engine(lambda *_a, **_k: self.info(health, running)).wait_dependencies(item, {'typesense': CID}, runtime.time.monotonic() + 10)
        called = []
        with self.assertRaises(RuntimeError):
            self.engine(lambda *_a, **_k: called.append(True)).wait_dependencies(item, {'typesense': CID}, runtime.time.monotonic() - 1)
        self.assertEqual(called, [])

    def test_full_start_waits_for_typesense_before_creating_web_and_keeps_quotas(self):
        events, configs, states = [], {}, iter(['starting','healthy'])
        def docker(args,**kwargs):
            if args[:2]==['image','inspect']:
                return json.dumps([{'Id':'f'*64,'Config':{'Labels':{'pockethost.web':'true','pockethost.web.app':APP}}}])
            if args==['inspect',CID]:
                status=next(states);events.append('health:'+status);return self.info(status)
            return json.dumps([{'NetworkSettings':{'Ports':{'3000/tcp':[{'HostIp':'127.0.0.1','HostPort':'39999'}]}}}])
        def request(method,path,body=None):
            if path.startswith('/containers/create'):
                service=body['Labels']['pockethost.web.service'];configs[service]=body;events.append('create:'+service)
                return {'Id':CID if service=='typesense'else'e'*64}
            return {'Id':'network'}
        engine=self.engine(docker);engine.request=request
        with patch.object(runtime,'admit')as admission,patch.object(runtime,'prepare_tooling',return_value='tools'),\
             patch.object(runtime,'loopback_port',return_value='39999'),patch.object(runtime.time,'sleep'),\
             patch('web_app_storage.prepare_mounts',return_value=[]),patch('web_app_project_limits.admit'),\
             patch('web_app_private_db.prepare_app',return_value=False):
            result=engine.start({'app':'webimage','typesense':'searchimage'},[])
        self.assertLess(events.index('health:healthy'),events.index('create:app'))
        self.assertEqual(admission.call_args.args[1:],(2816,2500))
        self.assertEqual(configs['typesense']['HostConfig']['NanoCpus'],2000*1000000)
        self.assertEqual(configs['typesense']['Healthcheck']['StartPeriod'],10*10**9)
        self.assertNotIn('PortBindings',configs['typesense']['HostConfig'])
        self.assertTrue(all(config['HostConfig']['ReadonlyRootfs']for config in configs.values()))
        self.assertEqual(result['target'],'http://127.0.0.1:39999')

    def test_larger_profiles_still_cannot_bypass_parent_or_project_admission(self):
        rows=[{'HostConfig':{'Memory':4032*1024**2,'NanoCpus':3600000000}}]
        docker=lambda args:'id'if args[0]=='ps'else json.dumps(rows)
        constraints=[{'scope':'web','limitBytes':16*1024**3,'currentBytes':3*1024**3,'reclaimableBytes':0,'workingSetBytes':3*1024**3}]
        with patch.object(runtime,'effective_memory',return_value=constraints),patch.object(runtime,'effective_cpu_millicores',return_value=8000),\
             patch.dict(os.environ,{'WEB_APPS_BUILD_CPUS':'4','WEB_APPS_BUILD_MEMORY':'8g'}):
            runtime.admit(docker,build=True)
            with patch.object(runtime,'effective_cpu_millicores',return_value=6000):
                with self.assertRaises(RuntimeError):runtime.admit(docker,build=True)
        import web_app_project_limits as projects
        job=types.SimpleNamespace(app=APP,snapshot={})
        policy={'appIds':[APP],'limits':{'resources':{'enabled':True,'memoryMiB':4096,'cpuMillicores':3000}}}
        with patch.object(projects,'policy',return_value=policy):
            projects.admit(self.root,job,lambda _args:'',2816,2500)
            with self.assertRaises(RuntimeError):projects.admit(self.root,job,lambda _args:'',2816,3001)

    def test_only_exact_next_cache_is_added_and_compose_mounts_stay_service_scoped(self):
        import web_app_storage as storage
        volumes=[{'name':'next-cache','target':'/app/.next/cache','sizeMiB':256},
                 {'name':'typesense-data','target':'/data','sizeMiB':2048}]
        self.assertEqual(storage.validate_volumes(volumes)[0]['target'],'/app/.next/cache')
        for target in ['/app/.next','/app/.next/server','/app/node_modules','/app/.next/cache/images',
                       '/app/.next/cache-extra','/app/.next/cache/../server','/app/.next/cache\n']:
            with self.assertRaises(RuntimeError):storage.validate_volumes([{**volumes[0],'target':target}])
        source=profile();source['compose']['services']['app']['volumes']=['next-cache:/app/.next/cache']
        source['compose']['services']['typesense']['volumes']=['typesense-data:/data']
        job=types.SimpleNamespace(app=APP,id=DEP,snapshot={'runtime':source,'storage':volumes})
        engine=runtime.RuntimeEngine(job,lambda *_a:'',lambda *_a:None,self.root,'/socket')
        self.assertEqual(engine.managed_targets('app',{'Config':{}}),{'/tmp','/app/.next/cache'})
        self.assertEqual(engine.managed_targets('typesense',{'Config':{}}),{'/tmp','/data'})
        with patch.object(storage,'_create'):
            mounted=storage.prepare_mounts(self.root,APP,volumes,['next-cache:/app/.next/cache'],lambda *_a:'')
        self.assertTrue(mounted[0]['VolumeOptions']['NoCopy'])
        self.assertFalse(mounted[0]['ReadOnly'])
        self.assertEqual(mounted[0]['Target'],'/app/.next/cache')
        with self.assertRaises(RuntimeError):storage.validate_volumes([{**volumes[0],'sizeMiB':2049}])


if __name__ == '__main__':
    unittest.main()
