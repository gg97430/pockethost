#!/usr/bin/env python3
"""Install the project rehearsal control service; never alters customer processes.

Run after coherent native API/hooks and helper modules have been staged. This
installs/enables a NEW idle platform service; it never restarts the routing edge,
Docker, a native database or a published application. An existing active rehearsal
must drain before replacing its code.
"""
import argparse
import json
import os
import pathlib
import re
import secrets
import shutil
import shlex
import stat
import subprocess
import sys

ROOT = pathlib.Path('/etc/kolab')
STATE = pathlib.Path('/var/lib/kolab-project-recovery')
INSTALL = pathlib.Path('/opt/pockethost-web')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data-root',required=True);parser.add_argument('--binary-root',required=True);parser.add_argument('--instance-app-root',required=True);parser.add_argument('--helper-image',required=True);parser.add_argument('--source-docker-socket',default='/run/user/1001/docker.sock');parser.add_argument('--sandbox-docker-socket',default='/run/user/1002/docker.sock');parser.add_argument('--activate',action='store_true');args=parser.parse_args()
    if os.geteuid()!=0:raise RuntimeError('Installer as root')
    for value in (args.data_root,args.binary_root,args.instance_app_root):
        path=pathlib.Path(value)
        if not path.is_absolute()or path.resolve()!=path or not path.exists():raise RuntimeError('Canonical existing configuration required')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',args.helper_image)or args.source_docker_socket!='/run/user/1001/docker.sock'or args.sandbox_docker_socket!='/run/user/1002/docker.sock':raise RuntimeError('Pinned tooling and rootless socket required')
    if STATE.exists():
        if STATE.resolve()!=STATE or STATE.stat().st_uid!=0 or STATE.stat().st_mode&0o077:raise RuntimeError('Unsafe rehearsal state')
        status=STATE/'status.json'
        if status.exists()and json.loads(status.read_text()).get('runId'):raise RuntimeError('Active/finalizing project rehearsal must drain before replacement')
    STATE.mkdir(mode=0o700,exist_ok=True);ROOT.mkdir(mode=0o755,exist_ok=True);INSTALL.mkdir(mode=0o755,parents=True,exist_ok=True)
    for directory in (ROOT,INSTALL):
        if directory.resolve()!=directory or directory.stat().st_uid!=0 or directory.stat().st_mode&0o022:raise RuntimeError('Root-owned protected installation directory required')
    key=ROOT/'project-recovery.key'
    if not os.path.lexists(key):
        descriptor=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(descriptor,'w')as stream:stream.write(secrets.token_hex(32));stream.flush();os.fsync(stream.fileno())
    if key.resolve()!=key or key.stat().st_uid!=0 or key.stat().st_mode&0o077:raise RuntimeError('Unsafe recovery key')
    config={'version':1,'dataRoot':args.data_root,'binaryRoot':args.binary_root,'instanceAppRoot':args.instance_app_root,'stateRoot':str(STATE),'keyFile':str(key),'sourceDockerSocket':args.source_docker_socket,'sandboxDockerSocket':args.sandbox_docker_socket,'helperImage':args.helper_image,'controlUrl':'http://127.0.0.1:8091'}
    lock_root=pathlib.Path('/run/kolab-project-recovery');lock_root.mkdir(mode=0o755,exist_ok=True)
    if lock_root.resolve()!=lock_root or lock_root.stat().st_uid!=0 or lock_root.stat().st_mode&0o022:raise RuntimeError('Unsafe runtime lock directory')
    lock_path=lock_root/'drill.lock'
    descriptor=os.open(lock_path,os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600);os.fchmod(descriptor,0o600);os.close(descriptor)
    path=ROOT/'project-recovery.json'
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'w')as stream:os.fchmod(stream.fileno(),0o600);json.dump(config,stream)
    source=pathlib.Path(__file__).resolve().parent
    for name in ('web_project_recovery.py','web_project_recovery_service.py','web_platform_recovery.py','database_memory_reservations.py','platform_maintenance.py'):
        target=INSTALL/name
        if target.exists()and target.is_symlink():raise RuntimeError('Unsafe installed helper')
        raw=(source/name).read_bytes()
        descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o644)
        with os.fdopen(descriptor,'wb')as stream:os.fchmod(stream.fileno(),0o644);stream.write(raw)
        os.chown(target,0,0)
    # Dedicated root/controller capability, inaccessible to UID1001 worker.
    service_key=ROOT/'project-recovery-controller.key'
    if not os.path.lexists(service_key):
        descriptor=os.open(service_key,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
        with os.fdopen(descriptor,'w')as stream: stream.write(secrets.token_hex(32));stream.flush();os.fsync(stream.fileno())
        os.chown(service_key,0,1000);service_key.chmod(0o640)
    if service_key.resolve()!=service_key or service_key.stat().st_uid!=0 or service_key.stat().st_gid!=1000 or service_key.stat().st_mode&0o777!=0o640:raise RuntimeError('Unsafe dedicated service key')
    unit=(source/'systemd/pockethost-project-recovery.service').read_text()
    target=pathlib.Path('/etc/systemd/system/pockethost-project-recovery.service');target.write_text(unit);target.chmod(0o644)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    if args.activate:subprocess.run(['systemctl','enable','--now','pockethost-project-recovery.service'],check=True)
    print(json.dumps({'installed':True,'activated':args.activate,'customerProcessesChanged':False,'keyFile':str(key)}))


if __name__=='__main__':main()
