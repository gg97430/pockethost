"""Root-owned, expiring admission pause. Running work and relay heartbeats continue."""
import json
import os
import pathlib
import re
import stat
import time
import tempfile
import fcntl
from contextlib import contextmanager

PATH=pathlib.Path('/run/kolab-platform-maintenance/admission.json')
MAX_SECONDS=1800


@contextmanager
def _operator_lock(path):
    if os.geteuid()!=0:raise PermissionError('Root-owned maintenance control required')
    parent=pathlib.Path(path).parent
    parent.mkdir(mode=0o755,parents=True,exist_ok=True)
    s=parent.lstat()
    if not stat.S_ISDIR(s.st_mode)or s.st_uid!=0 or s.st_mode&0o022:
        raise ValueError('Untrusted maintenance directory')
    fd=os.open(parent/'operator.lock',os.O_CREAT|os.O_NOFOLLOW|os.O_RDWR,0o600)
    with os.fdopen(fd,'r+')as lock:
        s=os.fstat(lock.fileno())
        if not stat.S_ISREG(s.st_mode)or s.st_uid!=0 or s.st_mode&0o077:
            raise ValueError('Untrusted maintenance lock')
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield


def acquire(owner,seconds=600,path=PATH):
    now=int(time.time());value={'version':1,'owner':owner,'startedAt':now,'expiresAt':now+seconds}
    parse(value,now)
    path=pathlib.Path(path)
    with _operator_lock(path):
        old=status(path)
        if old['paused']and old['owner']!=owner:raise RuntimeError('Une autre maintenance est déjà active.')
        fd,name=tempfile.mkstemp(prefix='.admission-',dir=path.parent)
        try:
            with os.fdopen(fd,'w')as f:
                os.fchmod(f.fileno(),0o644);json.dump(value,f);f.flush();os.fsync(f.fileno())
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)
    return parse(value,now)


def release(owner,path=PATH):
    path=pathlib.Path(path)
    with _operator_lock(path):
        old=status(path)
        if old['owner']!=owner:raise RuntimeError('La maintenance appartient à une autre intervention.')
        path.unlink()
    return status(path)


def parse(value,now):
    if (not isinstance(value,dict) or set(value)!={'version','owner','startedAt','expiresAt'} or
        type(value['version'])is not int or value['version']!=1 or
        not isinstance(value['owner'],str)or not re.fullmatch(r'[a-z0-9][a-z0-9-]{4,80}',value['owner'])or
        any(type(value[key])is not int for key in ['startedAt','expiresAt'])or
        not 0<value['expiresAt']-value['startedAt']<=MAX_SECONDS or value['startedAt']>now+5):
        raise ValueError('Invalid maintenance lease')
    return {'paused':now<value['expiresAt'],'owner':value['owner'],'startedAt':value['startedAt'],'expiresAt':value['expiresAt'],'error':''}


def status(path=PATH,now=None):
    now=time.time()if now is None else now
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd)as f:
            s=os.fstat(f.fileno())
            if not stat.S_ISREG(s.st_mode)or s.st_uid!=0 or s.st_mode&0o022 or s.st_size>4096:
                raise ValueError('Untrusted maintenance lease')
            value=f.read(4097)
            if len(value)>4096:raise ValueError('Maintenance lease too large')
            return parse(json.loads(value),now)
    except FileNotFoundError:
        return {'paused':False,'owner':'','startedAt':None,'expiresAt':None,'error':''}
    except (OSError,ValueError,TypeError,KeyError):
        return {'paused':True,'owner':'','startedAt':None,'expiresAt':None,'error':'Configuration de maintenance invérifiable ; nouvelles opérations suspendues.'}


def claim_response(path,scope='worker'):
    claims={'worker':{'claim','storage/claim','deletions/claim','backups/claim'},'tasks':{'claim'},'notifications':{'claim'},'recovery':{'claim'},'checks':{'claim'}}
    if path not in claims.get(scope,set()):return None
    state=status()
    if not state['paused']:return None
    return {'job':None,'jobs':[],'run':None,'items':[],'deliveries':[],'maintenance':state}
