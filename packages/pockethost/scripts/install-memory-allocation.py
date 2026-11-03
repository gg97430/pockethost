#!/usr/bin/env python3
"""Enable the common RAM manager after a new Kolab host's engines are ready.
Existing operator configuration is preserved. No container is restarted.
The fleet publisher uses a transactional installer for existing servers.
"""
import json
import os
import pathlib
import shutil
import subprocess

assert os.geteuid() == 0, 'Run with sudo'
source = pathlib.Path(__file__).resolve().parent
destination = pathlib.Path('/opt/pockethost-web')
assert pathlib.Path('/run/user/1001/docker.sock').is_socket(), 'Dedicated rootless engine must be ready'
database = pathlib.Path('/home/ubuntu/.local/share/pockethost/data/mothership/pb_data/data.db')
assert database.is_file(), 'Initialize the native controller first'
destination.mkdir(parents=True, exist_ok=True)
for name in ['platform_memory_allocator.py', 'systemd/pockethost-memory-allocation.service']:
    target = destination / pathlib.Path(name).name
    shutil.copyfile(source / name, target)
    target.chmod(0o644)
    os.chown(target, 0, 0)
unit = pathlib.Path('/etc/systemd/system/pockethost-memory-allocation.service')
shutil.copyfile(destination / unit.name, unit)
os.chown(unit, 0, 0)
config = pathlib.Path('/etc/kolab/memory-allocation.json')
if not config.exists():
    config.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'database': str(database), 'instancesRoot': str(database.parents[2] / 'instances'),
                   'webRoot': '/home/pockethost-web/data', 'webMaximumMiB': 8192,
                   'databaseMaximumMiB': 16384}, f)
subprocess.run(['systemctl', 'daemon-reload'], check=True)
subprocess.run(['systemctl', 'enable', '--now', unit.name], check=True)
print('RAM manager enabled. Verify its status.json and the platform supervision page.')
