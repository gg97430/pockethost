"""Quiesce only the platform worker; never signal a customer/container process."""
from contextlib import contextmanager
from contextlib import closing
import json
import os
import pathlib
import signal
import sqlite3
import stat
import subprocess
import sys
import time

UNIT='pockethost-web-worker.service'
SCRIPT='/opt/pockethost-web/web-app-worker.py'
PROJECT_RECOVERY_STATUS=pathlib.Path('/var/lib/kolab-project-recovery/status.json')


def database_write_barrier(database):
    """Wait for earlier native writers without writing records or changing owners.

    Native root-operation claims acquire their writer lock BEFORE checking the
    admission gate. Once this barrier passes, earlier claims have committed and
    subsequent claims see the gate. A separate child avoids changing the UID of
    concurrent customer probes and avoids root-owned SQLite WAL bookkeeping.
    """
    path=pathlib.Path(database);info=path.lstat()
    owners=(0,1000)if os.geteuid()==0 else(os.geteuid(),)
    if not stat.S_ISREG(info.st_mode)or info.st_uid not in owners:raise ValueError('Unexpected controller database owner')
    code="import sqlite3,sys,pathlib\nwith sqlite3.connect(pathlib.Path(sys.argv[1]).as_uri()+'?mode=rw',uri=True,timeout=2)as db:\n db.execute('BEGIN IMMEDIATE')\n db.execute('PRAGMA query_only=ON')\n db.execute('SELECT count(*) FROM sqlite_master').fetchone()\n db.rollback()\n"
    identity={'user':info.st_uid,'group':info.st_gid,'extra_groups':[]}if os.geteuid()==0 else{}
    subprocess.run([sys.executable,'-I','-c',code,str(path)],check=True,capture_output=True,timeout=4,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},**identity)


def busy(database,web_root):
    result={}
    conditions={
        'web_deployments':"status='running'",
        'web_storage_jobs':"status='running'",
        'web_task_runs':"status='running'",
        'web_app_backups':"status='running'",
        'instance_backups':"status='running' OR restoreState='running'",
        'web_notification_deliveries':"status='sending' AND leaseAt>=datetime('now','-5 minutes')",
    }
    with closing(sqlite3.connect(pathlib.Path(database).as_uri()+'?mode=ro',uri=True,timeout=2))as db:
        db.execute('PRAGMA query_only=ON')
        for table,condition in conditions.items():
            count=db.execute('SELECT count(*) FROM '+table+' WHERE '+condition).fetchone()[0]
            if count:result[table]=count
        for table,condition in {'web_app_check_runs':"status='running'",'web_project_recovery_runs':"status='running'",'web_project_migrations':"status IN ('importing','rolling_back')"}.items():
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                count=db.execute('SELECT count(*) FROM '+table+' WHERE '+condition).fetchone()[0]
                if count:result[table]=count
    root=pathlib.Path(web_root)
    if (root/'current-job.json').exists():result['worker_finalization']=1
    try:
        if json.loads((root/'tasks-status.json').read_text()).get('runId'):result['task_finalization']=1
    except FileNotFoundError:pass
    try:
        if json.loads((root/'checks-status.json').read_text()).get('runId'):result['check_finalization']=1
    except FileNotFoundError:pass
    try:
        if json.loads(PROJECT_RECOVERY_STATUS.read_text()).get('runId'):result['project_recovery_finalization']=1
    except FileNotFoundError:pass
    return result


class WorkerGuard:
    def __init__(self,database,web_root,unit=UNIT,script=SCRIPT):
        self.database,self.web_root,self.unit,self.script=database,web_root,unit,script

    def show(self,property):
        return subprocess.check_output(['systemctl','show',self.unit,'--property='+property,'--value'],text=True,timeout=5).strip()

    def identity(self,pid):
        if type(pid)is not int or pid<=1:raise ValueError('Invalid platform worker PID')
        root=pathlib.Path('/proc')/str(pid)
        if root.stat().st_uid!=1001:raise ValueError('Unexpected platform worker owner')
        command=(root/'cmdline').read_bytes().split(b'\0')
        if self.script.encode()not in command:raise ValueError('Unexpected platform worker command')
        # Start ticks prevent signals to a reused PID. Only the verified service PID is accepted.
        return (root/'stat').read_text().rsplit(')',1)[1].split()[19]

    def send(self,pid,birth,kind):
        if self.identity(pid)!=birth or int(self.show('MainPID'))!=pid:
            raise ValueError('Platform worker changed before signal')
        os.kill(pid,kind)

    @contextmanager
    def quiescent(self,timeout=180):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if busy(self.database,self.web_root):time.sleep(.5);continue
            pid=int(self.show('MainPID'));birth=self.identity(pid);self.send(pid,birth,signal.SIGSTOP)
            try:
                # Settle claims already on the loopback socket before freezing. Background data
                # relays live in separate processes; this short check does not stop them.
                time.sleep(.2)
                database_write_barrier(self.database)
                if busy(self.database,self.web_root):continue
                yield {'pid':pid,'birth':birth}
                return
            finally:
                try:self.send(pid,birth,signal.SIGCONT)
                except (OSError,ValueError):pass
        raise RuntimeError('Des opérations actives empêchent encore la mise à jour ; aucun service client arrêté.')

    def restart_frozen(self,token):
        self.send(token['pid'],token['birth'],0)
        subprocess.run(['systemctl','restart','--no-block',self.unit],check=True,capture_output=True,timeout=5)
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            state,pid=self.show('ActiveState'),int(self.show('MainPID'))
            # systemd itself normally resumes a stopped process after SIGTERM.
            # A fast replacement can skip the observable deactivating state.
            if state=='active'and pid not in (0,token['pid']):
                try:self.identity(pid)
                except (OSError,ValueError):pass  # systemd may still be setting UID/exec.
                else:return
            if state=='deactivating'and pid==token['pid']:
                try:self.send(token['pid'],token['birth'],signal.SIGCONT)
                except (OSError,ValueError):pass  # systemd may have completed its stop.
            time.sleep(.05)
        raise RuntimeError('Le nouveau worker de plateforme ne répond pas encore.')
