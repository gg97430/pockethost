#!/usr/bin/env python3
"""Opt-in Linux QA: live memory edits of one disposable native-image container."""
import json,os,pathlib,secrets,shutil,sqlite3,subprocess,tempfile,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
from unittest.mock import patch
import platform_operator as operator
import platform_memory_allocator as allocator
import database_memory_reservations as reservations
import database_memory_management as management


def docker(*args):return subprocess.check_output(['docker',*args],text=True,timeout=20)


def main():
    if os.geteuid()!=0:raise SystemExit('Explicit root Docker QA host required')
    # Reuse only the immutable image ID of a native PocketBase container, never
    # its environment, command, network, volume or authentication data.
    ids=docker('ps','-q').split()
    infos=json.loads(docker('inspect',*ids))if ids else[]
    image=next(row['Image']for row in infos if len(row['Name'].lstrip('/'))==15 and any(m['Destination']=='/home/pockethost'for m in row.get('Mounts',[])))
    root=pathlib.Path(tempfile.mkdtemp(prefix='kolab-operator-qa-'))
    ident=secrets.token_hex(8)[:15];token=secrets.token_hex(16);cid=None
    folder=root/'instances'/ident;folder.mkdir(parents=True)
    database=root/'control.db'
    with sqlite3.connect(database)as db:
        db.executescript('CREATE TABLE instances(id TEXT);CREATE TABLE web_runtime_profiles(appId TEXT,profile TEXT);CREATE TABLE web_apps(id TEXT,activeDeployment TEXT);CREATE TABLE instance_backups(instance TEXT,status TEXT,restoreState TEXT);')
        db.execute('INSERT INTO instances VALUES(?)',(ident,))
    root.chmod(0o755);database.chmod(0o600);os.chown(database,1000,1000)
    config={'database':str(database),'instancesRoot':str(root/'instances'),'webRoot':str(root),'webMaximumMiB':8192,'databaseMaximumMiB':16384}
    policy=root/'reservations.json';policy.write_text('{"version":1,"databases":{}}')
    mode=root/'management.json';mode.write_text('{"version":1,"databases":{}}')
    configuration=root/'configuration.json';configuration.write_text(json.dumps(config))
    (root/'project-limits.json').write_text('{"version":1,"apps":{},"projects":{}}')
    try:
        cid=docker('run','-d','--name',ident,'--label','kolab.qa='+token,'--network','none','--read-only','--cap-drop','ALL','--pids-limit','16','--memory','1g','--memory-swap','-1','--mount','type=bind,src='+str(folder)+',dst=/home/pockethost','--entrypoint','/bin/sh',image,'-c','sleep 120').strip()
        before=json.loads(docker('inspect',cid))[0]['State']
        with patch.object(reservations,'POLICY',policy),patch.object(management,'PATH',mode),patch.object(management,'LOCK',root/'memory.lock'),patch.object(operator,'POLICY',policy),patch.object(operator,'MANAGEMENT',mode),patch.object(operator,'CONFIG',configuration):
            def edit(kind,amount):return operator.set_memory({'id':ident,'mode':kind,'maximumMiB':amount,'revision':operator.revision(operator.load_policy(),operator.load_management())})
            fixed=edit('fixed',2048)
            host=json.loads(docker('inspect',cid))[0]['HostConfig'];assert host['Memory']==host['MemoryReservation']==2048*1024**2
            maximum=min(16384,config['databaseMaximumMiB'],allocator.host_memory()[0]//(2*1024**2))
            adaptive=edit('adaptive',maximum)
            host=json.loads(docker('inspect',cid))[0]['HostConfig'];assert host['Memory']==min(4096,maximum)*1024**2 and host['MemoryReservation']==6*1024**2
            state,report=allocator.cycle(config,[allocator.Engine('/var/run/docker.sock','databases')],{cid:{'externalOverride':True}},dry_run=True)
            assert report['resources'][0]['decision']not in ('external-override','fixed-database-limit','unavailable'),report
            assert state[cid]['managementRevision']==operator.load_management()[ident]['revision']
        after=json.loads(docker('inspect',cid))[0]['State']
        assert before['Pid']==after['Pid']and before['StartedAt']==after['StartedAt']and after['Running']
        assert not fixed['restartRequired']and not adaptive['restartRequired']
        print(json.dumps({'ok':True,'fixedThenAdaptive':True,'allocatorAcknowledged':True,'samePidAndStartedAt':True,'customerContainersModified':False}))
    finally:
        if cid:
            info=json.loads(docker('inspect',cid))[0]
            assert info['Name']=='/'+ident and info['Config']['Labels'].get('kolab.qa')==token
            docker('rm','-f',cid)
        shutil.rmtree(root)


if __name__=='__main__':main()
