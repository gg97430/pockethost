"""Explicit operator approvals for native database memory changes."""
import pathlib,re,os,stat,fcntl
from contextlib import contextmanager
from database_memory_reservations import trusted_json

PATH=pathlib.Path('/etc/kolab/database-memory-management.json')
LOCK=pathlib.Path('/run/kolab-platform-maintenance/memory.lock')


@contextmanager
def policy_lock():
    if os.geteuid()!=0:raise PermissionError('Root operator required')
    LOCK.parent.mkdir(mode=0o755,exist_ok=True)
    parent=LOCK.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode)or parent.st_uid!=0 or parent.st_mode&0o022:raise ValueError('Untrusted operator directory')
    with os.fdopen(os.open(LOCK,os.O_CREAT|os.O_NOFOLLOW|os.O_RDWR,0o600),'r+')as lock:
        row=os.fstat(lock.fileno())
        if not stat.S_ISREG(row.st_mode)or row.st_uid!=0 or row.st_mode&0o077:raise ValueError('Untrusted operator lock')
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield


def parse(value):
    if not isinstance(value,dict)or set(value)!={'version','databases'}or type(value['version'])is not int or value['version']!=1:
        raise ValueError('Invalid database memory management')
    rows=value['databases']
    if not isinstance(rows,dict)or len(rows)>1000:raise ValueError('Invalid database memory management')
    for key,row in rows.items():
        if (not re.fullmatch(r'[a-z0-9]{15}',key)or not isinstance(row,dict)or set(row)!={'mode','maximumMiB','revision'}or
            row['mode']not in ('adaptive','fixed')or type(row['maximumMiB'])is not int or not 512<=row['maximumMiB']<=524288 or
            not isinstance(row['revision'],str)or not re.fullmatch(r'[a-f0-9]{32}',row['revision'])):
            raise ValueError('Invalid database memory management')
    return rows


def load():
    try:return parse(trusted_json(PATH))
    except FileNotFoundError:return {}
