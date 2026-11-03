#!/usr/bin/env python3
"""Opt-in Linux integration test. Signals only a disposable, uniquely named QA service."""
import json,os,pathlib,secrets,sqlite3,subprocess,tempfile,time
from platform_release_guard import WorkerGuard


def main():
    if os.geteuid()!=0 or not pathlib.Path('/run/systemd/system').exists():
        raise SystemExit('Run explicitly as root on a systemd QA host.')
    token=secrets.token_hex(6)
    unit='kolab-platform-guard-qa-'+token+'.service'
    root=pathlib.Path(tempfile.mkdtemp(prefix='kolab-guard-qa-',dir='/tmp'))
    root.chmod(0o755)
    code=root/'web-app-worker.py'
    code.write_text("import pathlib,time,os\np=pathlib.Path('/tmp/kolab-guard-heartbeat-"+token+"')\nwhile True:\n p.write_text(str(os.getpid())+' '+str(time.time()))\n time.sleep(.05)\n")
    code.chmod(0o644)
    pulse=pathlib.Path('/tmp/kolab-guard-heartbeat-'+token)
    database=root/'control.db'
    with sqlite3.connect(database)as db:
        for table in ['web_deployments','web_storage_jobs','web_task_runs','web_app_backups']:
            db.execute('CREATE TABLE '+table+'(status TEXT)')
        db.execute('CREATE TABLE instance_backups(status TEXT, restoreState TEXT)')
        db.execute('CREATE TABLE web_notification_deliveries(status TEXT,leaseAt TEXT)')
    try:
        subprocess.run(['systemd-run','--unit='+unit,'--property=User=1001','--property=KillMode=control-group','--property=TimeoutStopSec=10','/usr/bin/python3',str(code)],check=True,capture_output=True)
        deadline=time.monotonic()+5
        while not pulse.exists():
            if time.monotonic()>deadline:raise RuntimeError('QA worker did not start')
            time.sleep(.05)
        guard=WorkerGuard(database,root,unit,str(code))
        old=guard.show('MainPID')
        with guard.quiescent(timeout=5)as handle:
            assert guard.identity(int(old))==handle['birth']
            assert pathlib.Path('/proc/'+old+'/status').read_text().split('State:')[1].lstrip().startswith('T')
            guard.restart_frozen(handle)
        new=guard.show('MainPID')
        deadline=time.monotonic()+3
        while pulse.read_text().split()[0]!=new:
            if time.monotonic()>deadline:raise RuntimeError('New QA worker did not resume')
            time.sleep(.05)
        assert old!=new and guard.show('ActiveState')=='active'
        print(json.dumps({'ok':True,'dummyWorkerReplaced':True,'customerProcessesSignalled':False}))
    finally:
        subprocess.run(['systemctl','stop',unit],capture_output=True)
        subprocess.run(['systemctl','reset-failed',unit],capture_output=True)
        pulse.unlink(missing_ok=True)
        import shutil
        shutil.rmtree(root)


if __name__=='__main__':main()
