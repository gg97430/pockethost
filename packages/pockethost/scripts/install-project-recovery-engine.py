#!/usr/bin/env python3
"""Independent rootless rehearsal engine UID1002; no existing service is restarted.

This account has no credentials, source mounts, inbound ports or login shell.
Only the root recovery coordinator can reach both the source hosting engine and
this private destination. Docker layers/state under this account are disposable;
back up the root coordinator config/key and installer, never live drill copies.
"""
import argparse
import fcntl
import grp
import importlib.util
import json
import os
import pathlib
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import web_project_recovery as engine

USER='kolab-recovery'
UID=GID=1002
HOME=pathlib.Path('/home/kolab-recovery')
RUNTIME=pathlib.Path('/run/user/1002')
SOCKET=str(RUNTIME/'docker.sock')
SOURCE='/run/user/1001/docker.sock'
SYSTEM_BIN_DIRS=('/usr/bin','/usr/local/bin','/usr/sbin')
INSTALL_STAGE='preflight'


def protected_executable(candidate):
    candidate=pathlib.Path(candidate)
    # Distribution packages may expose a root-owned symlink in /usr/local/bin.
    # Check both its original directory chain and the resolved target, without
    # trusting PATH inherited by the privileged installer.
    if candidate.lstat().st_uid!=0:raise ValueError('System executable entry must belong to root')
    resolved=candidate.resolve(strict=True)
    for path in [*candidate.parents,resolved,*resolved.parents]:
        info=path.stat()
        if info.st_uid!=0 or info.st_mode&0o022:raise ValueError('System executable path is writable outside root')
    info=resolved.stat()
    if not stat.S_ISREG(info.st_mode)or not info.st_mode&0o111:raise ValueError('System executable is not a regular executable')
    return str(candidate)


def system_executable(name,directories=SYSTEM_BIN_DIRS):
    if not re.fullmatch(r'[a-z][a-z0-9.-]*',name):raise ValueError('Invalid system executable name')
    for directory in directories:
        candidate=pathlib.Path(directory)/name
        if os.path.lexists(candidate):return protected_executable(candidate)
    raise ValueError('Missing rootless prerequisite '+name)


def command(args, timeout=30, capture=True):
    return subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE if capture else None,stderr=subprocess.PIPE if capture else None,timeout=timeout,check=True,text=True)


def subids(raw):
    rows=[]
    for line in raw.splitlines():
        if not line.strip()or line.lstrip().startswith('#'):continue
        parts=line.split(':')
        if len(parts)!=3 or not re.fullmatch(r'[a-zA-Z0-9_-]+',parts[0])or not parts[1].isdigit()or not parts[2].isdigit():raise ValueError('Invalid subordinate ID file')
        start,count=int(parts[1]),int(parts[2])
        if start<65536 or count<1 or start+count>2**32:raise ValueError('Unsafe subordinate ID range')
        rows.append((parts[0],start,count))
    ordered=sorted(rows,key=lambda row:row[1])
    if any(a[1]+a[2]>b[1]for a,b in zip(ordered,ordered[1:])):raise ValueError('Overlapping subordinate ID ranges')
    return rows


def next_range(uid_rows,gid_rows):
    high=max([1048576,*[start+count for _name,start,count in [*uid_rows,*gid_rows]]])
    start=((high+65535)//65536)*65536
    if start+65536>2**32:raise ValueError('No subordinate ID range available')
    return start


def identity():
    try:by_uid=pwd.getpwuid(UID)
    except KeyError:by_uid=None
    try:by_name=pwd.getpwnam(USER)
    except KeyError:by_name=None
    try:group_id=grp.getgrgid(GID)
    except KeyError:group_id=None
    try:group_name=grp.getgrnam(USER)
    except KeyError:group_name=None
    if by_uid and by_uid.pw_name!=USER or by_name and (by_name.pw_uid!=UID or by_name.pw_gid!=GID or by_name.pw_dir!=str(HOME)):
        raise ValueError('UID1002/name occupied by an unrelated account; no renumbering performed')
    if group_id and group_id.gr_name!=USER or group_name and group_name.gr_gid!=GID:raise ValueError('GID1002/name occupied by an unrelated group')
    return by_name is not None,group_name is not None


def private_home():
    if HOME.resolve()!=HOME or HOME.stat().st_uid!=UID or HOME.stat().st_mode&0o077:raise ValueError('Recovery account home must be private owner1002 mode0700')
    if RUNTIME.exists()and(RUNTIME.resolve()!=RUNTIME or RUNTIME.stat().st_uid!=UID or RUNTIME.stat().st_mode&0o077):raise ValueError('Runtime directory must be private owner1002 mode0700')


def as_user(args,timeout=60):
    return command(['/usr/sbin/runuser','-u',USER,'--','/usr/bin/env','-i','HOME='+str(HOME),'USER='+USER,'LOGNAME='+USER,'PATH=/usr/local/bin:/usr/bin:/bin','XDG_RUNTIME_DIR='+str(RUNTIME),'DBUS_SESSION_BUS_ADDRESS=unix:path='+str(RUNTIME/'bus'),*args],timeout)


def write_root(path,raw):
    path=pathlib.Path(path)
    if path.parent.resolve()!=path.parent:raise ValueError('Non-canonical system configuration directory')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o644)
    with os.fdopen(fd,'w')as stream:os.fchmod(stream.fileno(),0o644);stream.write(raw);stream.flush();os.fsync(stream.fileno())
    os.chown(path,0,0)


def verify(helper_image):
    private_home()
    meta=json.loads(engine.Docker(SOCKET)(['info','--format','{{json .}}']))
    if not any('rootless'in value for value in meta.get('SecurityOptions',[])) or meta.get('CgroupVersion')!='2'or meta.get('CgroupDriver')!='systemd':raise ValueError('Rootless cgroupv2/systemd engine not confirmed')
    storage=engine.canonical(meta['DockerRootDir'],True)
    if not storage.is_relative_to(HOME):raise ValueError('Recovery Docker data root outside its private home')
    entry=os.stat(SOCKET)
    if not stat.S_ISSOCK(entry.st_mode)or entry.st_uid!=UID:raise ValueError('Destination socket identity differs')
    deny=subprocess.run(['/usr/sbin/runuser','-u','pockethost-web','--','/usr/bin/python3','-I','-c',"import socket;s=socket.socket(socket.AF_UNIX);s.connect('/run/user/1002/docker.sock')"],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
    if deny.returncode==0:raise ValueError('Hosting worker can access recovery socket; isolation refused')
    limits={name:command(['systemctl','show','user-1002.slice','--property='+name,'--value']).stdout.strip()for name in ['MemoryMax','MemorySwapMax','TasksMax','CPUQuotaPerSecUSec']}
    if limits['MemoryMax']!=str(4*1024**3)or limits['MemorySwapMax']!='0'or limits['TasksMax']!='512'or limits['CPUQuotaPerSecUSec']not in('1s','1000000'):
        raise ValueError('Independent recovery slice limits not confirmed')
    observed=json.loads(engine.Docker(SOCKET)(['image','inspect',helper_image]))[0]
    if observed.get('Id')!=helper_image:raise ValueError('Approved helper missing in independent engine')
    return {'rootless':True,'uid':UID,'privateSocket':SOCKET,'hostingUid1001Denied':True,'memoryMaxBytes':4*1024**3,'swapMaxBytes':0,'cpuPercent':100,'tasksMax':512,'approvedHelperImage':helper_image,'sourceEngineChanged':False}


def install(helper_image,execute=False):
    global INSTALL_STAGE
    INSTALL_STAGE='preflight'
    if os.geteuid()!=0:raise ValueError('Root installer required')
    if not engine.IMAGE.fullmatch(helper_image):raise ValueError('Exact approved helper image SHA256 required')
    exists,group_exists=identity()
    if not exists and HOME.exists():raise ValueError('New account home already exists; inspect it before installation')
    executables={name:system_executable(name)for name in ('docker','dockerd-rootless-setuptool.sh','rootlesskit','newuidmap','newgidmap','loginctl')}
    if not pathlib.Path('/sys/fs/cgroup/cgroup.controllers').is_file():raise ValueError('cgroupv2 required')
    uids=subids(pathlib.Path('/etc/subuid').read_text());gids=subids(pathlib.Path('/etc/subgid').read_text())
    if not execute:return {'dryRun':True,'uid':UID,'home':str(HOME),'accountExists':exists,'newSubrangeIfNeeded':next_range(uids,gids),'memoryMaxBytes':4*1024**3,'sourceEngineChanged':False,'rootlessSetupExecutable':executables['dockerd-rootless-setuptool.sh']}
    with open('/run/kolab-project-recovery-engine-install.lock','a')as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        INSTALL_STAGE='account-and-subordinate-ids'
        if not group_exists:command(['/usr/sbin/groupadd','--gid',str(GID),USER])
        if not exists:command(['/usr/sbin/useradd','--uid',str(UID),'--gid',str(GID),'--home-dir',str(HOME),'--create-home','--shell','/usr/sbin/nologin','--no-log-init',USER])
        HOME.chmod(0o700)
        # useradd may already allocate one valid non-overlapping range. Preserve
        # it exactly; never move any pre-existing UID/GID mapping.
        uids=subids(pathlib.Path('/etc/subuid').read_text());gids=subids(pathlib.Path('/etc/subgid').read_text())
        chosen_uids=[row for row in uids if row[0]==USER];chosen_gids=[row for row in gids if row[0]==USER]
        if len(chosen_uids)>1 or len(chosen_gids)>1 or any(row[2]<65536 for row in chosen_uids+chosen_gids):raise ValueError('Unsupported existing subordinate range for recovery user')
        start=next_range(uids,gids)
        if not chosen_uids:command(['/usr/sbin/usermod','--add-subuids',str(start)+'-'+str(start+65535),USER])
        if not chosen_gids:command(['/usr/sbin/usermod','--add-subgids',str(start)+'-'+str(start+65535),USER])
        subids(pathlib.Path('/etc/subuid').read_text());subids(pathlib.Path('/etc/subgid').read_text())
        delegation=pathlib.Path('/etc/systemd/system/user@1002.service.d');delegation.mkdir(mode=0o755,exist_ok=True)
        slice_dir=pathlib.Path('/etc/systemd/system/user-1002.slice.d');slice_dir.mkdir(mode=0o755,exist_ok=True)
        write_root(delegation/'recovery-delegation.conf','[Service]\nDelegate=cpu cpuset io memory pids\n')
        write_root(slice_dir/'recovery-limits.conf','[Slice]\nCPUQuota=100%\nMemoryHigh=3G\nMemoryMax=4G\nMemorySwapMax=0\nTasksMax=512\nIOWeight=10\n')
        INSTALL_STAGE='new-user-manager'
        command(['systemctl','daemon-reload']);command([executables['loginctl'],'enable-linger',USER]);command(['systemctl','start','user@1002.service'])
        RUNTIME.chmod(0o700);private_home()
        docker_config=HOME/'.config/docker';docker_config.mkdir(mode=0o700,parents=True,exist_ok=True);os.chown(HOME/'.config',UID,GID);os.chown(docker_config,UID,GID)
        config=docker_config/'daemon.json'
        if not config.exists():
            config.write_text(json.dumps({'log-driver':'json-file','log-opts':{'max-size':'1m','max-file':'1'}}));config.chmod(0o600);os.chown(config,UID,GID)
        INSTALL_STAGE='new-rootless-setup'
        if not(HOME/'.config/systemd/user/docker.service').exists():as_user([executables['dockerd-rootless-setuptool.sh'],'install'],180)
        INSTALL_STAGE='new-rootless-start'
        as_user(['/usr/bin/systemctl','--user','enable','--now','docker'],60)
        for _ in range(60):
            try:
                engine.Docker(SOCKET)(['info','--format','{{json .SecurityOptions}}']);break
            except Exception:time.sleep(.5)
        target=engine.Docker(SOCKET)
        INSTALL_STAGE='approved-helper-image'
        try:available=json.loads(target(['image','inspect',helper_image]))[0].get('Id')==helper_image
        except Exception:available=False
        if not available:
            source=engine.Docker(SOURCE)
            if json.loads(source(['image','inspect',helper_image]))[0].get('Id')!=helper_image:raise ValueError('Source approved helper missing')
            with tempfile.TemporaryDirectory(prefix='kolab-recovery-helper-',dir='/var/lib')as folder:
                root=pathlib.Path(folder);root.chmod(0o700);archive=root/'helper.tar'
                source.export(helper_image,archive);engine.recovery.require_space(HOME,engine.RESERVE+2*archive.stat().st_size);target.load(archive)
        INSTALL_STAGE='isolation-verification'
        return verify(helper_image)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--helper-image',required=True);parser.add_argument('--execute',action='store_true');parser.add_argument('--verify-only',action='store_true');args=parser.parse_args()
    result=verify(args.helper_image)if args.verify_only else install(args.helper_image,args.execute)
    print(json.dumps(result))


if __name__=='__main__':
    try:main()
    except Exception as error:
        result={'passed':False,'stage':INSTALL_STAGE,'error':str(error)if isinstance(error,ValueError)else'Independent recovery engine installation failed; existing engines were not restarted.'}
        # Only this credential-free account's setup/start commands may expose
        # bounded diagnostics. No worker environment or customer log is read.
        if isinstance(error,subprocess.CalledProcessError)and INSTALL_STAGE in ('new-rootless-setup','new-rootless-start'):
            result['diagnostic']=re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',str(error.stderr or error.stdout or ''))[-2048:]
        print(json.dumps(result));sys.exit(1)
