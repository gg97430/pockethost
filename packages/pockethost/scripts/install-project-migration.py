#!/usr/bin/env python3
"""Install operator tooling only. Does not start, stop or restart any service."""
import argparse,json,os,pathlib,re,secrets,shutil,stat,tempfile

def atomic(path, data, mode, group=0):
    if path.is_symlink():raise RuntimeError('Symbolic configuration refused')
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb')as stream:
            os.fchmod(stream.fileno(),mode);os.fchown(stream.fileno(),0,group);stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(name):os.unlink(name)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('data-root','binary-root','web-root','helper-image','instance-app-root'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    if os.geteuid()!=0:raise RuntimeError('Root installer required')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',args.helper_image):raise RuntimeError('Pinned helper required')
    for value in (args.data_root,args.binary_root,args.web_root,args.instance_app_root):
        path=pathlib.Path(value)
        if not path.is_absolute()or path.resolve()!=path or not path.is_dir():raise RuntimeError('Canonical source root required')
    folder=pathlib.Path('/etc/kolab');folder.mkdir(mode=0o755,exist_ok=True)
    if folder.resolve()!=folder or folder.stat().st_uid!=0 or folder.stat().st_mode&0o022:raise RuntimeError('Root-protected configuration required')
    key=folder/'project-migration-control.key'
    if not key.exists():
        with open(key,'x')as stream:os.fchmod(stream.fileno(),0o440);stream.write(''.join(secrets.choice('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')for _ in range(80)))
        os.chown(key,0,1000)
    info=key.lstat()
    if not stat.S_ISREG(info.st_mode)or info.st_nlink!=1 or info.st_uid!=0 or info.st_gid!=1000 or info.st_mode&0o027:raise RuntimeError('Unsafe migration control key')
    state=pathlib.Path('/var/lib/kolab-project-migration');state.mkdir(mode=0o700,exist_ok=True)
    if state.resolve()!=state or state.stat().st_uid!=0 or state.stat().st_mode&0o077:raise RuntimeError('Private migration state required')
    config={'version':1,'dataRoot':args.data_root,'binaryRoot':args.binary_root,'webRoot':args.web_root,'instanceAppRoot':args.instance_app_root,'stateRoot':str(state),'helperImage':args.helper_image,'dockerSocket':'/run/user/1001/docker.sock','sandboxDockerSocket':'/run/user/1002/docker.sock'}
    file=folder/'project-migration.json'
    atomic(file,json.dumps(config).encode(),0o600)
    source=pathlib.Path(__file__).resolve().parent/'web_project_migration.py';target=pathlib.Path('/opt/pockethost-web/web_project_migration.py')
    if target.is_symlink():raise RuntimeError('Unsafe installed module')
    parent=target.parent
    if parent.resolve()!=parent or parent.stat().st_uid!=0 or parent.stat().st_mode&0o022:raise RuntimeError('Root-protected module directory required')
    atomic(target,source.read_bytes(),0o644)
    print(json.dumps({'installed':True,'servicesChanged':False,'customerProcessesChanged':False}))

if __name__=='__main__':main()
