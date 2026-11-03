#!/usr/bin/env python3
"""Opt-in real disposable capture/import rehearsal; no existing tenant is selected.

Capture stage creates two uniquely labelled fixture containers, one local volume,
plain synthetic native DB + files/hooks, then a complete encrypted capsule. Import
stage (may run on a DIFFERENT host) validates the reviewed mapping/capacity and
runs restored front+DB in a networkless namespace, keeping the worker inert.
`clean` removes only the exact fixture IDs with the matching fixture label.
Use a NEW private workspace; transfer only capsule.phpr + transfer.key between hosts.
"""
import argparse
import json
import os
import pathlib
import secrets
import shutil
import sqlite3
import string
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import web_project_recovery as engine


def ident(): return ''.join(secrets.choice(string.ascii_lowercase+string.digits) for _ in range(15))
def write(path,value): engine.private_json(path,value)


def capture(args):
    root=pathlib.Path(args.workspace).absolute()
    if root.resolve()!=root or root.exists():raise RuntimeError('New canonical fixture workspace required')
    root.mkdir(mode=0o700,parents=True);(root/'FIXTURE_ONLY').write_text('kolab-project-recovery-qa-v1');(root/'FIXTURE_ONLY').chmod(0o600)
    app,db,project,deployment=ident(),ident(),ident(),ident();label=secrets.token_hex(12)
    docker=engine.Docker(args.docker_socket)
    volume='ph-web-data-'+app+'-data-v1';front='ph-web-'+app+'-'+deployment;worker=front+'-worker'
    state={'label':label,'appId':app,'sourceContainers':[],'sourceVolumes':[volume],'sourceImages':[]}
    write(root/'source-state.json',state)
    try:
        docker(['volume','create','--label','pockethost.web.app='+app,'--label','kolab.project-fixture='+label,volume])
        docker(['run','--rm','--network','none','--label','kolab.project-fixture='+label,'--read-only','--cap-drop','ALL','--user','0:0','--memory','64m','--memory-swap','64m','--cpus','0.1','--pids-limit','32','--security-opt','no-new-privileges','--mount','type=volume,source='+volume+',target=/volume',args.helper_image,'/bin/sh','-ec','mkdir /volume/empty; printf fixture-file > /volume/upload.txt'])
        native=root/'source/instances'/db/'pb_data';native.mkdir(parents=True,mode=0o700)
        binary=root/'binaries'/args.version/'linux_amd64/pocketbase';binary.parent.mkdir(parents=True,mode=0o700);shutil.copyfile(args.pocketbase,binary);binary.chmod(0o755)
        password=secrets.token_urlsafe(32)
        subprocess.run([str(binary),'superuser','upsert','fixture@example.invalid',password,'--dir',str(native),'--hooksDir',str(root/'empty-hooks'),'--migrationsDir',str(root/'empty-migrations')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=30)
        with sqlite3.connect(native/'data.db')as connection:connection.executescript("CREATE TABLE recovery_fixture(value TEXT); INSERT INTO recovery_fixture VALUES('fixture-native-data');")
        storage=native/'storage/fixture';storage.mkdir(parents=True);(storage/'document.txt').write_text('native-fixture-file')
        hooks=native.parent/'pb_hooks';hooks.mkdir();(hooks/'never-execute.pb.js').write_text('throw new Error("copied hooks must remain inert")')
        public=native.parent/'pb_public';public.mkdir();(public/'index.html').write_text('fixture-public')
        platform_hooks=root/'instance-app/v23/pb_hooks';platform_hooks.mkdir(parents=True);(platform_hooks/'platform.pb.js').write_text('throw new Error("platform hooks must remain inert")')
        node="require('http').createServer(async(q,s)=>{try{let r=await fetch('http://127.0.0.1:4825/api/health',{signal:AbortSignal.timeout(1500)});s.writeHead(r.ok?200:503);s.end('fixture')}catch{s.writeHead(503);s.end('fixture')}}).listen(3000,'0.0.0.0')"
        # Derive one disposable image from the approved local helper without
        # network/builds. Its unique immutable ID must travel through the capsule
        # and is absent from the separate recovery engine before the rehearsal.
        holder='kolab-project-fixture-image-'+label
        state['sourceContainers'].append(holder);write(root/'source-state.json',state)
        docker(['create','--name',holder,'--network','none','--read-only','--user','10001:10001','--cap-drop','ALL','--security-opt','no-new-privileges','--label','kolab.project-fixture='+label,'--entrypoint','/usr/local/bin/node',args.helper_image,'-e','setInterval(()=>{},1000)'])
        reply=docker(['commit','--pause=false','--change','LABEL pockethost.web.app='+app,'--change','LABEL kolab.project-fixture='+label,holder],timeout=30)
        candidates=[line.strip()for line in reply.splitlines()if engine.IMAGE.fullmatch(line.strip())]
        assert len(candidates)==1
        fixture_image=candidates[0]
        state['sourceImages'].append(fixture_image);write(root/'source-state.json',state)
        docker(['rm',holder]);state['sourceContainers'].remove(holder);write(root/'source-state.json',state)
        labels=['--label','pockethost.web=true','--label','pockethost.web.app='+app,'--label','pockethost.web.deployment='+deployment,'--label','kolab.project-fixture='+label]
        state['sourceContainers'].append(front);write(root/'source-state.json',state)
        docker(['create','--name',front,'--network','none','--read-only','--user','10001:10001','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','128m','--memory-swap','128m','--cpus','0.25','--pids-limit','64',*labels,'--label','pockethost.web.service=web','--label','pockethost.web.front=true','--publish','127.0.0.1::3000','--mount','type=volume,source='+volume+',target=/data,readonly','--entrypoint','/usr/local/bin/node',fixture_image,'-e',node])
        docker(['start',front])
        state['sourceContainers'].append(worker);write(root/'source-state.json',state)
        docker(['create','--name',worker,'--network','none','--read-only','--user','10001:10001','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','64m','--memory-swap','64m','--cpus','0.1','--pids-limit','32',*labels,'--label','pockethost.web.service=worker','--label','pockethost.web.front=false','--entrypoint','/usr/local/bin/node',fixture_image,'-e','setInterval(()=>{},1000)'])
        docker(['start',worker])
        state['identities']=[{'name':row['Name'],'id':row['Id'],'startedAt':row['State']['StartedAt']}for row in json.loads(docker(['inspect',front,worker]))];write(root/'source-state.json',state)
        key=root/'transfer.key';key.write_text(secrets.token_hex(32));key.chmod(0o600)
        snapshot={'version':1,'platform':args.platform,'projectId':project,'projectName':'Disposable project recovery fixture','revision':'f'*64,'apps':[{'id':app,'name':'Fixture app','deploymentId':deployment,'bindings':[{'instanceId':db,'port':4825}],'externalDependencies':[],'configuration':{'runtime':{'kind':'compose'}},'scheduledTasks':[{'id':ident(),'name':'Must remain inert','schedule':'* * * * *','command':'throw never execute','enabled':True}]}],'databases':[{'id':db,'version':args.version}]}
        write(root/'snapshot.json',snapshot)
        result=engine.capture(snapshot,root/'source',root/'binaries',root/'capsule.phpr',key,docker,root/'instance-app')
        current=[{'name':row['Name'],'id':row['Id'],'startedAt':row['State']['StartedAt']}for row in json.loads(docker(['inspect',front,worker]))]
        assert current==state['identities'],'Original fixture process restarted'
        mappings={'applications':[{'sourceId':app,'targetId':ident(),'name':'Fixture target'}],'databases':[{'sourceId':db,'targetId':ident(),'name':'Fixture target DB'}],'environment':[{'appId':app,'name':'DATABASE_URL','source':'http://127.0.0.1:4825','target':'http://127.0.0.1:4825','scope':'runtime'}],'domains':[],'workers':[{'appId':app,'service':'worker','enabled':False}],'scheduledTasks':[{'appId':app,'enabled':False}]}
        write(root/'mappings.json',mappings);write(root/'capture-result.json',result)
        print(json.dumps({'passed':True,'stage':'capture','originalFixtureIdentityUnchanged':True,'completeArtifacts':result['completeArtifacts'],'sha256':result['sha256'],'archiveBytes':result['archiveBytes']}))
    except Exception:
        clean(args);raise


def import_(args):
    root=engine.canonical(args.workspace,True);docker=engine.Docker(args.sandbox_docker_socket)
    if not (root/'FIXTURE_ONLY').exists():raise RuntimeError('Fixture marker missing')
    archive=root/'capsule.phpr';key=root/'transfer.key';manifest=engine.recovery.inspect_archive(lambda:open(archive,'rb'),engine.recovery.load_key(key))
    mappings=json.loads(engine.recovery.private_file(root/'mappings.json').read_text())
    plan=engine.migration_plan(manifest,args.platform,mappings,shutil.disk_usage(root).free)
    write(root/'migration-plan.json',plan)
    imported=engine.migration_import(archive,key,root/'imported',plan,args.sha256)
    probe=engine.sandbox_drill(root/'imported',docker,args.helper_image,memory_mib=128)
    assert probe['runtimeStarted']and probe['noExternalNetwork']and probe['workersInert'],probe
    assert any(row['status']=='inert-worker-preserved'for row in probe['services'])
    assert any(row['status']=='running-isolated'for row in probe['services'])
    assert not probe['applicationVerified']and not imported['cutoverExecuted']
    capsule=engine.verify_materialized(root/'imported',manifest)
    unique_images={service['image']for app in capsule['apps']for service in app['services']if service['image']!=args.helper_image}
    assert unique_images and all(not docker.image_exists(image)for image in unique_images),'Newly imported fixture image left in sandbox engine'
    assert docker.image_exists(args.helper_image),'Pre-existing helper was removed'
    if(root/'source-state.json').exists():
        state=json.loads(engine.recovery.private_file(root/'source-state.json').read_text());source=engine.Docker(args.docker_socket)
        current=[{'name':row['Name'],'id':row['Id'],'startedAt':row['State']['StartedAt']}for row in json.loads(source(['inspect',*state['sourceContainers']]))]
        assert current==state['identities'],'Original source fixture changed during independent drill'
        assert all(source.image_exists(image)for image in state.get('sourceImages',[])),'Source image was removed by target cleanup'
    write(root/'proof.json',{'import':imported,'probe':probe})
    print(json.dumps({'passed':True,'stage':'import','completeArtifacts':True,'nativeDependencyAndFrontHTTP':True,'workerStayedInert':True,'importedUniqueImageRemoved':True,'helperImagePreserved':True,'noExternalNetwork':True,'cutoverExecuted':False,'allSandboxContainersRemoved':not docker(['ps','-aq','--filter','label=kolab.project-drill']).strip()}))


def clean(args):
    root=pathlib.Path(args.workspace).absolute()
    if root.resolve()!=root or not(root/'FIXTURE_ONLY').is_file():raise RuntimeError('Unsafe fixture cleanup')
    docker=engine.Docker(args.docker_socket)
    if(root/'source-state.json').exists():
        state=json.loads(engine.recovery.private_file(root/'source-state.json').read_text())
        for name in state['sourceContainers']:
            try:info=json.loads(docker(['inspect',name]))[0]
            except subprocess.CalledProcessError:continue
            assert info['Config']['Labels']['kolab.project-fixture']==state['label'];docker(['rm','-f',name])
        for name in state['sourceVolumes']:
            try:info=json.loads(docker(['volume','inspect',name]))[0]
            except subprocess.CalledProcessError:continue
            assert info['Labels']['kolab.project-fixture']==state['label'];docker(['volume','rm',name])
        for image in state.get('sourceImages',[]):
            try:info=json.loads(docker(['image','inspect',image]))[0]
            except subprocess.CalledProcessError:continue
            assert info['Config']['Labels']['kolab.project-fixture']==state['label'];docker(['image','rm',image])
    journal=root/'imported/sandbox-state.json'
    if journal.exists():engine.cleanup_sandbox(json.loads(engine.recovery.private_file(journal).read_text()),engine.Docker(args.sandbox_docker_socket))
    shutil.rmtree(root)
    print(json.dumps({'passed':True,'stage':'cleanup'}))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('stage',choices=['capture','import','clean']);parser.add_argument('--workspace',required=True);parser.add_argument('--platform',choices=['app4','app5'],required=True);parser.add_argument('--docker-socket',default='/run/user/1001/docker.sock');parser.add_argument('--sandbox-docker-socket',default='/run/user/1002/docker.sock');parser.add_argument('--helper-image',required=True);parser.add_argument('--pocketbase');parser.add_argument('--version',default='0.39.11');parser.add_argument('--sha256');args=parser.parse_args()
    if os.geteuid()!=0:raise RuntimeError('Root required for isolated test filesystem reads')
    if args.stage=='capture':capture(args)
    elif args.stage=='import':import_(args)
    else:clean(args)


if __name__=='__main__':main()
