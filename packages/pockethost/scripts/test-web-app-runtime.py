#!/usr/bin/env python3
import base64
import contextlib
import json
import os
import pathlib
import tempfile
import types
import unittest
from unittest.mock import patch, Mock
import web_app_runtime as runtime
from web_app_build import dependency_plan, cache_namespace

APP,DEP='a'*15,'b'*15
DIGEST='ghcr.io/owner/site@sha256:'+'c'*64


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.policy=patch.dict(os.environ,{'WEB_APPS_ALLOCATION_MODE':'fixed'});self.policy.start();self.addCleanup(self.policy.stop)
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name).resolve()
    def tearDown(self):self.temp.cleanup()
    def memory(self,current,inactive=0,maximum=5120,scope='web'):
        return [{'scope':scope,'limitBytes':maximum*1024**2,'currentBytes':current*1024**2,'reclaimableBytes':min(current,inactive)*1024**2,'workingSetBytes':max(0,current-inactive)*1024**2}]
    def test_worker_subdirectory_selects_its_dockerfile_copy_context_and_ignore_rules(self):
        child=self.root/'mail-worker';child.mkdir();(child/'Dockerfile').write_text('FROM node:24-alpine\nCOPY package.json ./\n')
        (self.root/'Dockerfile').write_text('FROM unrelated-root-application\n')
        (child/'package.json').write_text('{"name":"mail-worker"}')
        (self.root/'package.json').write_text('{"name":"unrelated-root"}')
        work=self.root/'work';work.mkdir()
        for kind in ['worker','dockerfile','compose']:
            profile={'kind':kind}if kind!='compose'else{'kind':'compose','compose':{'front':'web','services':{'web':{'dockerfile':'Dockerfile'}}}}
            run=Mock();job=types.SimpleNamespace(app=APP,id=DEP,secrets=[],run=run,snapshot={'runtime':profile,'config':{'rootDirectory':'mail-worker'},'env':{}})
            engine=runtime.RuntimeEngine(job,lambda *a,**k:'',lambda *a:None,self.root,'/run/user/1001/docker.sock')
            engine.hmac_key='fixture-worker-secret-not-a-real-credential'
            with patch.object(runtime,'build_environment',return_value={}):
                engine.build(self.root,work,{'dockerfile':'Dockerfile','_service':'web'}if kind=='compose'else None)
            args=run.call_args.args[0]
            self.assertEqual(args[-1],str(child));self.assertEqual(args[args.index('-f')+1],str(child/'Dockerfile'))
        self.assertIn('.git',(child/'.dockerignore').read_text());self.assertFalse((self.root/'.dockerignore').exists())
    def test_build_context_rejects_missing_file_traversal_and_symbolic_roots(self):
        (self.root/'file').write_text('not a directory')
        (self.root/'linked').symlink_to(self.root,target_is_directory=True)
        self.assertEqual(runtime.build_context(self.root,'.'),self.root)
        for name in ['missing','file','../outside','/tmp','linked','linked/nested']:
            with self.subTest(name=name),self.assertRaises(RuntimeError):runtime.build_context(self.root,name)
    def test_all_profiles_and_immutable_images(self):
        for kind in runtime.KINDS:
            if kind=='compose':continue
            value=runtime.validate_profile({'kind':kind,**({'image':DIGEST}if kind in('image','worker')else{})})
            self.assertEqual(value['kind'],kind)
            if kind=='worker':self.assertIsNone(value['port'])
        for image in ['nginx:latest','host/repo','ghcr.io/owner/site@sha256:short']:
            with self.assertRaises(RuntimeError):runtime.validate_profile({'kind':'image','image':image})
    def test_compose_forbids_host_capabilities_binds_and_cycles(self):
        base={'kind':'compose','compose':{'front':'web','services':{'web':{'image':DIGEST},'cache':{'image':DIGEST}}}}
        value=runtime.validate_profile(base)
        self.assertEqual(set(value['compose']['order']),{'web','cache'})
        for key,data in [('privileged',True),('network_mode','host'),('volumes',['/etc:/data']),('devices',['/dev/kvm']),('cap_add',['SYS_ADMIN']),('ports',['80:80']),('env_file','.env'),('environment',{'SECRET':'literal'})]:
            bad=json.loads(json.dumps(base));bad['compose']['services']['web'][key]=data
            with self.assertRaises(RuntimeError):runtime.validate_profile(bad)
        base['compose']['services']['web']['depends_on']=['cache'];base['compose']['services']['cache']['depends_on']=['web']
        with self.assertRaises(RuntimeError):runtime.validate_profile(base)
    def test_compose_aggregate_limits_are_not_per_service_limits(self):
        source={'kind':'compose','compose':{'front':'web','services':{name:{'image':DIGEST,'resources':{'memoryMiB':5000,'cpuMillicores':500,'pids':256}}for name in ['web','other']}}}
        with self.assertRaises(RuntimeError):runtime.validate_profile(source)
    def test_cgroup_admission_checks_reserved_and_actual_memory_and_cpu(self):
        def docker(args):
            return 'id'if args[0]=='ps'else json.dumps([{'HostConfig':{'Memory':768*1024**2,'NanoCpus':500000000}}])
        with patch.object(runtime,'effective_memory',return_value=self.memory(100)):
            runtime.admit(docker,768,500)
            with self.assertRaises(RuntimeError):runtime.admit(docker,1536,1000,build=True)
        with patch.object(runtime,'effective_memory',return_value=self.memory(4600)):
            with self.assertRaises(RuntimeError):runtime.admit(docker,64,50)
    def test_workspace_dependency_layers_precede_source_and_keep_secrets_out(self):
        (self.root/'packages/web').mkdir(parents=True)
        (self.root/'package.json').write_text(json.dumps({'workspaces':['packages/*'],'packageManager':'pnpm@10.1.0'}))
        (self.root/'packages/web/package.json').write_text(json.dumps({'dependencies':{'next':'16'}}))
        (self.root/'pnpm-lock.yaml').write_text('lockfileVersion: 9.0')
        (self.root/'pnpm-workspace.yaml').write_text('packages: [packages/*]')
        before,install,after=dependency_plan(self.root,'packages/web','pnpm','pnpm-lock.yaml','.',APP)
        self.assertIn('packages/web/package.json',before);self.assertNotIn('COPY . .',before)
        self.assertIn('--mount=type=cache',install);self.assertIn('--mount=type=secret',install);self.assertIn('--store-dir /pnpm/store',install)
        self.assertEqual(after,'COPY . .\n');self.assertIn('pnpm@10.1.0',install)
    def test_project_install_scripts_preserve_source_availability(self):
        (self.root/'package.json').write_text(json.dumps({'scripts':{'postinstall':'node scripts/setup.js'}}));(self.root/'package-lock.json').write_text('{}')
        before,install,after=dependency_plan(self.root,'.','npm','package-lock.json','.',APP)
        self.assertNotIn('COPY . .',before);self.assertIn('npm ci --ignore-scripts',install)
        self.assertTrue(after.startswith('COPY . .\n'));self.assertIn('npm rebuild',after)
        for hook in ('prepublish','preprepare','prepare','postprepare'):self.assertIn('npm run '+hook+' --if-present',after)
    def test_pnpm_defers_workspace_scripts_and_rebuilds_pending_native_dependencies(self):
        (self.root/'package.json').write_text(json.dumps({'packageManager':'pnpm@10.0.0','scripts':{'postinstall':'node scripts/setup.js'}}));(self.root/'pnpm-lock.yaml').write_text('lockfileVersion: 9.0')
        before,install,after=dependency_plan(self.root,'.','pnpm','pnpm-lock.yaml','.',cache_namespace(APP,'private-worker-secret'*3))
        self.assertNotIn('COPY . .',before);self.assertIn('--ignore-scripts',install)
        self.assertTrue(after.startswith('COPY . .\n'));self.assertIn('pnpm rebuild -r --pending',after);self.assertIn('pnpm install --offline --frozen-lockfile',after)
    def test_yarn_lifecycle_keeps_source_before_install(self):
        (self.root/'package.json').write_text(json.dumps({'packageManager':'yarn@4.0.0','scripts':{'postinstall':'node scripts/setup.js'}}));(self.root/'yarn.lock').write_text('')
        before,install,after=dependency_plan(self.root,'.','yarn','yarn.lock','.',APP)
        self.assertEqual(before,'COPY . .\n');self.assertEqual(after,'');self.assertNotIn('--ignore-scripts',install)
    def test_local_file_dependencies_are_available_to_the_install_layer(self):
        # Cas réel (lsconnect) : "gsap": "file:vendor/gsap-3.15.0.tgz" faisait échouer npm ci avant « COPY . . ».
        (self.root/'vendor').mkdir();(self.root/'vendor/gsap-3.15.0.tgz').write_bytes(b'tgz');(self.root/'package-lock.json').write_text('{}')
        (self.root/'package.json').write_text(json.dumps({'dependencies':{'next':'16','gsap':'file:vendor/gsap-3.15.0.tgz'}}))
        before,install,after=dependency_plan(self.root,'.','npm','package-lock.json','.',APP)
        self.assertIn('COPY ["vendor/gsap-3.15.0.tgz", "/src/vendor/gsap-3.15.0.tgz"]',before);self.assertEqual(after,'COPY . .\n')
        # Un dossier local impose la copie complète avant l'installation ; un chemin hors du dépôt ou symbolique est refusé.
        (self.root/'libs/ui').mkdir(parents=True);(self.root/'package.json').write_text(json.dumps({'dependencies':{'ui':'file:libs/ui'}}))
        self.assertEqual(dependency_plan(self.root,'.','npm','package-lock.json','.',APP)[0],'COPY . .\n')
        for spec in ['file:../outside.tgz','link:linked']:
            if spec=='link:linked':(self.root/'linked').symlink_to(self.root/'libs/ui')
            (self.root/'package.json').write_text(json.dumps({'dependencies':{'x':spec}}))
            with self.assertRaises(RuntimeError):dependency_plan(self.root,'.','npm','package-lock.json','.',APP)
    def test_sequential_build_uses_reclaimable_cache_but_reserves_full_future_build_headroom(self):
        with patch.object(runtime,'effective_memory',return_value=self.memory(3500,2500)):
            runtime.admit(lambda args:'',build=True)
        with patch.object(runtime,'effective_memory',return_value=self.memory(1600)):
            with self.assertRaises(RuntimeError):runtime.admit(lambda args:'',build=True)
    def test_loopback_selection_preserves_prior_publication_targets_and_uses_kernel_free_port(self):
        (self.root/'runtime-publications.json').write_text(json.dumps({APP:{'target':'http://127.0.0.1:32787','state':'stopped'}}))
        (self.root/'routes.json').write_text(json.dumps({'routes':[{'target':'http://127.0.0.1:32790'}]}))
        port=runtime.loopback_port(self.root);self.assertTrue(port.isdigit());self.assertNotIn(port,['32787','32790'])
        with runtime.socket.socket(runtime.socket.AF_INET,runtime.socket.SOCK_STREAM)as listener:listener.bind(('127.0.0.1',int(port)))

    def test_build_admission_follows_builder_quotas_and_web_slice_cpu(self):
        # app3 agrandi : builder 4 CPU / 8 Gio, groupe web cpu.max 800 % ; l'ancien budget figé (2 CPU, 3 Gio) refusait tout build.
        root=self.root/'cgroup';slice=root/'user.slice'/('user-'+str(os.getuid())+'.slice');slice.mkdir(parents=True)
        (root/'user.slice'/'cpu.max').write_text('max 100000\n');(slice/'cpu.max').write_text('800000 100000\n')
        def sites(args):return'a b c'if args[0]=='ps'else json.dumps([{'HostConfig':{'Memory':768*1024**2,'NanoCpus':500000000}}]*3)
        with patch.object(runtime,'CGROUP_ROOT',root),patch.object(runtime,'effective_memory',return_value=[{'scope':'s','limitBytes':16*1024**3,'currentBytes':3*1024**3,'reclaimableBytes':0,'workingSetBytes':3*1024**3}]),patch.dict(os.environ,{'WEB_APPS_BUILD_CPUS':'4','WEB_APPS_BUILD_MEMORY':'8g'}):
            self.assertEqual(runtime.effective_cpu_millicores(),8000);self.assertEqual(runtime.build_reservation(),(8192,4000))
            self.assertEqual(runtime.admit(sites,build=True)['candidateBytes'],8192*1024**2)
            with self.assertRaises(RuntimeError):runtime.admit(sites,512,2600,build=True)
            (slice/'cpu.max').write_text('200000 100000\n')
            with self.assertRaises(RuntimeError):runtime.admit(sites,build=True)
            (slice/'cpu.max').write_text('max 100000\n');self.assertEqual(runtime.effective_cpu_millicores(),(os.cpu_count() or 2)*1000)
        with patch.dict(os.environ,{'WEB_APPS_BUILD_CPUS':'abc','WEB_APPS_BUILD_MEMORY':'8gb'}):self.assertEqual(runtime.build_reservation(),(3072,1000))
    def test_realistic_inactive_cache_preserves_raw_metric_and_all_hard_reservations(self):
        with patch.object(runtime,'effective_memory',return_value=self.memory(4492,3042)),patch.object(runtime,'effective_cpu_millicores',return_value=2000):
            value=runtime.admit(lambda args:'',256,200)
            self.assertEqual(value['currentBytes'],4492*1024**2)
            self.assertEqual(value['workingSetBytes'],1450*1024**2)
            self.assertEqual(value['reclaimableBytes'],3042*1024**2)
            self.assertEqual(runtime.admit(lambda args:'',build=True)['candidateBytes'],3072*1024**2)
            def reserved(args):return'id'if args[0]=='ps'else json.dumps([{'HostConfig':{'Memory':4096*1024**2,'NanoCpus':500000000}}])
            with self.assertRaises(RuntimeError):runtime.admit(reserved,768,200)
            with self.assertRaises(RuntimeError):runtime.admit(lambda args:'',64,2001)
        with patch.object(runtime,'effective_memory',return_value=self.memory(4492)):
            with self.assertRaises(RuntimeError):runtime.admit(lambda args:'',256,200)

    def test_memory_reader_uses_keyed_inactive_file_only_checks_all_ancestors_and_fails_conservatively(self):
        cgroups=self.root/'cgroups';parent=cgroups/'user.slice';base=parent/'user-1234.slice';base.mkdir(parents=True)
        def files(path,maximum,current,stats):
            (path/'memory.max').write_text(str(maximum*1024**2));(path/'memory.current').write_text(str(current*1024**2));(path/'memory.stat').write_text(stats)
        stats='active_file '+str(412*1024**2)+'\nslab_reclaimable '+str(270*1024**2)+'\ninactive_file '+str(3042*1024**2)+'\nanon '+str(746*1024**2)+'\n'
        files(base,5120,4492,stats);(parent/'memory.max').write_text('max')
        with patch.object(runtime,'CGROUP_ROOT',cgroups),patch('os.getuid',return_value=1234):
            rows=runtime.effective_memory();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['workingSetBytes'],1450*1024**2)
            runtime.admit(lambda args:'',256,200)
            files(parent,8192,7900,'inactive_file 0\n')
            self.assertEqual(len(runtime.effective_memory()),2)
            with self.assertRaises(RuntimeError):runtime.admit(lambda args:'',128,200)
            (parent/'memory.max').write_text('max')
            for invalid in [None,'inactive_file -1\n','inactive_file invalid\n','inactive_file 1\ninactive_file 1\n','active_file 999999999999\nslab_reclaimable 999999999999\n']:
                if invalid is None:(base/'memory.stat').unlink(missing_ok=True)
                else:(base/'memory.stat').write_text(invalid)
                self.assertEqual(runtime.effective_memory()[0]['workingSetBytes'],4492*1024**2)
                with self.assertRaises(RuntimeError):runtime.admit(lambda args:'',256,200)
            (base/'memory.stat').write_text('inactive_file '+str(9999*1024**2)+'\n')
            self.assertEqual(runtime.effective_memory()[0]['workingSetBytes'],0)
            (base/'memory.max').unlink()
            with self.assertRaises(RuntimeError):runtime.effective_memory()

    def test_tooling_volume_is_fixed_by_helper_and_rejects_changed_owner_metadata(self):
        helper='sha256:'+'e'*64;volume=runtime.tooling_volume_name(helper)
        calls=[]
        def docker(args,**kwargs):
            calls.append(args)
            if args[:2]==['volume','inspect']:return json.dumps([{'Driver':'local','Options':None,'Labels':{'pockethost.tools.image':helper}}])
            return json.dumps({'format':1,'image':helper})
        with patch('web_app_storage._helper',return_value=helper):self.assertEqual(runtime.prepare_tooling(docker),volume)
        self.assertTrue(any('type=volume,source='+volume+',target=/tools,readonly'in args for args in calls))
        self.assertEqual(runtime.tooling_node_prefix()[0],'/__pockethost_tools/ld.so')
        def foreign(*args,**kwargs):return json.dumps([{'Driver':'local','Options':{'device':'/host'},'Labels':{'pockethost.tools.image':helper}}])
        with patch('web_app_storage._helper',return_value=helper):
            with self.assertRaises(RuntimeError):runtime.prepare_tooling(foreign)

    def test_registry_build_config_is_private_and_never_in_argv(self):
        job=types.SimpleNamespace(snapshot={'runtime':{'kind':'image','image':DIGEST,'registry':{'server':'ghcr.io','username':'ci','password':'private-value'}}},secrets=[])
        env=runtime.build_environment(job,self.root)
        file=pathlib.Path(env['DOCKER_CONFIG'])/'config.json'
        self.assertEqual(file.stat().st_mode&0o777,0o600)
        auth=json.loads(file.read_text())['auths']['ghcr.io']['auth']
        self.assertEqual(base64.b64decode(auth),b'ci:private-value');self.assertIn(auth,job.secrets)
    def test_exact_cached_private_digest_redeploys_without_registry_credentials_or_network(self):
        image_id='sha256:'+'f'*64
        job=types.SimpleNamespace(app=APP,id=DEP,snapshot={'runtime':{'kind':'image','image':DIGEST}},secrets=[])
        engine=runtime.RuntimeEngine(job,lambda *args,**kwargs:json.dumps([{'Id':image_id,'RepoDigests':[DIGEST],'Config':{}}]),lambda *args:None,self.root,'/run/user/1001/docker.sock')
        with patch.object(runtime.http.client,'HTTPConnection',side_effect=AssertionError('network must not be used')):
            self.assertEqual(engine.pull(DIGEST),image_id)

    def test_cached_digest_pulled_by_another_app_is_never_reused_without_registry_auth(self):
        # Shared Docker cache: project B pulled DIGEST with its own credentials; app A only knows the digest.
        image_id='sha256:'+'f'*64;calls=[];contacted=[]
        def docker(args,**kwargs):
            calls.append(args)
            if args[:2]==['image','inspect']and args[2].startswith('ph-web-pull/'):raise __import__('subprocess').CalledProcessError(1,args)
            if args[:2]==['image','inspect']:return json.dumps([{'Id':image_id,'RepoDigests':[DIGEST],'Config':{}}])
            return ''
        class Registry(runtime.http.client.HTTPConnection):
            def connect(self):pass
            def request(self,method,url,headers=None,**kwargs):contacted.append((url,dict(headers or {})))
            def getresponse(self):return types.SimpleNamespace(status=200,readline=lambda size=-1:b'')
            def close(self):pass
        job=types.SimpleNamespace(app=APP,id=DEP,snapshot={'runtime':{'kind':'image','image':DIGEST}},secrets=[])
        engine=runtime.RuntimeEngine(job,docker,lambda *args:None,self.root,'/run/user/1001/docker.sock')
        with patch.object(runtime.http.client,'HTTPConnection',Registry),patch('shutil.disk_usage',return_value=types.SimpleNamespace(free=100*1024**3)):
            self.assertEqual(engine.pull(DIGEST),image_id)
        self.assertEqual(len(contacted),1,'the registry must authorize this app before the cached digest is used')
        self.assertIn(['image','tag',image_id,'ph-web-pull/'+APP+':'+'c'*64],calls)

    def test_foreign_promotion_image_is_rejected_before_commit(self):
        job=types.SimpleNamespace(app=APP,id=DEP,snapshot={'runtime':{'kind':'image','image':DIGEST}})
        engine=runtime.RuntimeEngine(job,lambda *args,**kwargs:json.dumps([{'Id':'sha256:'+'f'*64,'Config':{'Labels':{'pockethost.web.app':'x'*15}}}]),lambda *args: self.fail('foreign image committed'),self.root,'/run/user/1001/docker.sock')
        with self.assertRaises(RuntimeError):engine.own_image('sha256:'+'f'*64,source_app='z'*15)
    def test_implicit_and_nested_tooling_image_volumes_are_rejected_before_create(self):
        job=types.SimpleNamespace(app=APP,id=DEP,snapshot={'runtime':{'kind':'image','image':DIGEST},'env':{}})
        for destination in ['/data','/__pockethost_tools/node']:
            requests=[]
            info={'Id':'sha256:'+'f'*64,'Config':{'Labels':{'pockethost.web.app':APP,'pockethost.web':'true'},'Volumes':{destination:{}}}}
            engine=runtime.RuntimeEngine(job,lambda *args,**kwargs:json.dumps([info]),lambda method,path,body=None:requests.append(path)or{'Id':'f'*64},self.root,'/run/user/1001/docker.sock')
            with patch.object(runtime,'admit'),patch.object(runtime,'prepare_tooling',return_value='ph-web-tools-'+('e'*20)):
                with self.assertRaises(RuntimeError):engine.start('owned',[])
            self.assertFalse(any(path.startswith('/containers/create')for path in requests))
            with self.assertRaises(RuntimeError):engine.own_image('sha256:'+'f'*64)
            self.assertFalse(any(path.startswith('/containers/create')for path in requests))

    def test_declared_volume_requires_exact_managed_target_and_temp_is_removed_with_volumes(self):
        info={'Id':'sha256:'+'f'*64,'Config':{'Labels':{'pockethost.web.app':APP},'Volumes':{'/data':{}}}}
        job=types.SimpleNamespace(app=APP,id=DEP,snapshot={'runtime':{'kind':'image','image':DIGEST},'storage':[{'name':'uploads','target':'/data','sizeMiB':16,'readOnly':False}]})
        calls=[]
        def request(method,path,body=None):calls.append((method,path,body));return{'Id':'f'*64}
        engine=runtime.RuntimeEngine(job,lambda *args,**kwargs:json.dumps([info]),request,self.root,'/run/user/1001/docker.sock')
        engine.own_image('sha256:'+'f'*64)
        create=next(body for method,path,body in calls if path=='/containers/create')
        self.assertIn('/data',create['HostConfig']['Tmpfs'])
        self.assertTrue(any(method=='DELETE'and path.endswith('&v=true')for method,path,_ in calls))
        with self.assertRaises(RuntimeError):runtime.validate_image_volumes(info,{'/data/subfolder'})

    def test_generic_next_image_keeps_scoped_next_volume_and_bounded_cache(self):
        job=types.SimpleNamespace(app=APP,id=DEP,snapshot={'runtime':{'kind':'image','image':DIGEST},'env':{}})
        calls=[]
        def docker(args,**kwargs):
            if args[:2]==['image','inspect']:return json.dumps([{'Id':'sha256:'+'f'*64,'Config':{'Labels':{'pockethost.web.app':APP,'pockethost.web':'true','pockethost.framework':'nextjs','pockethost.next.directory':'/app/.next'}}}])
            return json.dumps([{'NetworkSettings':{'Ports':{'3000/tcp':[{'HostIp':'127.0.0.1','HostPort':'39999'}]}}}])
        def request(method,path,body=None):calls.append((path,body));return{'Id':'f'*64}
        with patch.object(runtime,'admit'),patch.object(runtime,'loopback_port',return_value='39999'),patch.object(runtime,'prepare_tooling',return_value='ph-web-tools-'+('e'*20)),patch('web_app_storage.prepare_mounts',return_value=[]),patch('web_app_storage._measure',return_value=1024):
            result=runtime.RuntimeEngine(job,docker,request,self.root,'/run/user/1001/docker.sock').start('owned',[])
        config=next(body for path,body in calls if path.startswith('/containers/create'))
        self.assertIn({'Type':'volume','Source':'ph-web-next-'+APP+'-'+DEP,'Target':'/app/.next'},config['HostConfig']['Mounts'])
        self.assertEqual(config['HostConfig']['Tmpfs']['/app/.next/cache'],'rw,nosuid,uid=10001,gid=10001,size=128m')
        self.assertEqual(result['framework'],'nextjs');self.assertEqual(result['serverDirectory'],'.')

    def test_runtime_container_has_only_its_named_volume_and_private_network(self):
        job=types.SimpleNamespace(app=APP,id=DEP,snapshot={'runtime':{'kind':'image','image':DIGEST,'port':8080},'env':{'A':{'value':'literal\n$HOME','runtime':True}}})
        requests=[]
        def docker(args,**kwargs):
            if args[:2]==['image','inspect']:return json.dumps([{'Id':'sha256:'+'f'*64,'Config':{'Labels':{'pockethost.web.app':APP,'pockethost.web':'true'}}}])
            return json.dumps([{'NetworkSettings':{'Ports':{'8080/tcp':[{'HostIp':'127.0.0.1','HostPort':'39999'}]}}}])
        def request(method,path,body=None):
            requests.append((method,path,body));return {'Id':'f'*64}
        with patch.object(runtime,'admit'),patch.object(runtime,'loopback_port',return_value='39999'),patch.object(runtime,'prepare_tooling',return_value='ph-web-tools-'+('e'*20)),patch('web_app_storage.prepare_mounts',return_value=[]):
            result=runtime.RuntimeEngine(job,docker,request,self.root,'/run/user/1001/docker.sock').start('owned',[])
        config=next(body for _,path,body in requests if path.startswith('/containers/create'))
        self.assertEqual(config['User'],'10001:10001');self.assertTrue(config['HostConfig']['ReadonlyRootfs']);self.assertEqual(config['HostConfig']['CapDrop'],['ALL']);self.assertNotIn('Binds',config['HostConfig'])
        # Docker merges LABELs inherited from an untrusted image. These two
        # reserved roles must always be overridden for ordinary app services.
        inherited={'pockethost.web.private-db':'true','pockethost.web.private-db.wait':'true'}
        merged={**inherited,**config['Labels']}
        self.assertEqual(merged['pockethost.web.private-db'],'false');self.assertEqual(merged['pockethost.web.private-db.wait'],'false')
        self.assertEqual(config['HostConfig']['PortBindings'],{'8080/tcp':[{'HostIp':'127.0.0.1','HostPort':'39999'}]})
        self.assertIn('A=literal\n$HOME',config['Env']);self.assertEqual(result['target'],'http://127.0.0.1:39999');self.assertEqual(result['image'],'sha256:'+'f'*64)

    def test_auto_runtime_gets_port_writable_tmp_home_and_keeps_bash_command(self):
        job=types.SimpleNamespace(app=APP,id=DEP,snapshot={'runtime':{'kind':'auto','port':8000,'startCommand':'python main.py'},'env':{'HOME':{'value':'/tmp/custom','runtime':True},'BUILD_ONLY':{'value':'hidden','build':True}}})
        requests=[]
        def docker(args,**kwargs):
            if args[:2]==['image','inspect']:return json.dumps([{'Id':'sha256:'+'f'*64,'Config':{'Labels':{'pockethost.web.app':APP,'pockethost.web':'true'}}}])
            return json.dumps([{'NetworkSettings':{'Ports':{'8000/tcp':[{'HostIp':'127.0.0.1','HostPort':'39998'}]}}}])
        def request(method,path,body=None):requests.append((path,body));return {'Id':'f'*64}
        with patch.object(runtime,'admit'),patch.object(runtime,'loopback_port',return_value='39998'),patch.object(runtime,'prepare_tooling',return_value='ph-web-tools-'+('e'*20)),patch('web_app_storage.prepare_mounts',return_value=[]):
            result=runtime.RuntimeEngine(job,docker,request,self.root,'/run/user/1001/docker.sock').start('owned',[])
        config=next(body for path,body in requests if path.startswith('/containers/create'))
        env=dict(item.split('=',1)for item in config['Env'])
        self.assertEqual(env['PORT'],'8000');self.assertEqual(env['HOME'],'/tmp/custom');self.assertEqual(env['XDG_CONFIG_HOME'],'/tmp/.config');self.assertEqual(env['XDG_DATA_HOME'],'/tmp/.local/share');self.assertEqual(env['MISE_CACHE_DIR'],'/tmp/.cache/mise')
        self.assertNotIn('BUILD_ONLY',env);self.assertNotIn('Cmd',config);self.assertNotIn('Entrypoint',config);self.assertEqual(result['runtimeKind'],'auto');self.assertEqual(result['target'],'http://127.0.0.1:39998')
        job.snapshot['runtime']={'kind':'image','image':DIGEST};requests.clear()
        with patch.object(runtime,'admit'),patch.object(runtime,'loopback_port',return_value='39998'),patch.object(runtime,'prepare_tooling',return_value='ph-web-tools-'+('e'*20)),patch('web_app_storage.prepare_mounts',return_value=[]):
            job.snapshot['env']={}
            try:runtime.RuntimeEngine(job,docker,request,self.root,'/run/user/1001/docker.sock').start('owned',[])
            except RuntimeError:pass
        config=next(body for path,body in requests if path.startswith('/containers/create'))
        self.assertFalse(any(item.startswith(('HOME=','XDG_'))for item in config['Env']))

    def test_auto_build_delegates_to_railpack_only_for_the_site_itself(self):
        job=types.SimpleNamespace(app=APP,id=DEP,snapshot={'runtime':{'kind':'auto'}})
        engine=runtime.RuntimeEngine(job,lambda *a,**k:'',lambda *a:None,self.root,'/run/user/1001/docker.sock')
        with patch('web_app_railpack.build',return_value='sha256:'+'a'*64)as build:
            self.assertEqual(engine.build(self.root,self.root),'sha256:'+'a'*64)
        build.assert_called_once_with(engine,self.root,self.root)


if __name__=='__main__':unittest.main()
