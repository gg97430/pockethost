#!/usr/bin/env python3
"""Install only the fixed operator socket; preserve policies and running work."""
import os
import pathlib
import stat
import subprocess
import tempfile

UNITS = ('pockethost-platform-operator.socket', 'pockethost-platform-operator@.service')


def trusted(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Root-owned immutable operator file required')


def main():
    if os.geteuid() != 0:
        raise PermissionError('Root installer required')
    for name in ['platform_operator.py', 'platform_operator_socket.py']:
        trusted(pathlib.Path('/opt/pockethost-web') / name)
    source = pathlib.Path(__file__).resolve().parent / 'systemd'
    for name in UNITS:
        target = pathlib.Path('/etc/systemd/system') / name
        candidate = source / name
        if candidate.exists():
            info = candidate.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
                raise ValueError('Untrusted unit source')
            fd, temporary = tempfile.mkstemp(prefix='.kolab-operator-', dir=target.parent)
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o644)
                stream.write(candidate.read_bytes())
            os.replace(temporary, target)
        trusted(target)
    runtime = pathlib.Path('/run/kolab-platform-maintenance')
    runtime.mkdir(mode=0o755, exist_ok=True)
    info = runtime.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Untrusted operator runtime directory')
    subprocess.run(['systemctl', 'daemon-reload'], check=True, capture_output=True, timeout=15)
    subprocess.run(['systemctl', 'enable', '--now', UNITS[0]], check=True, capture_output=True, timeout=15)
    print('{"operatorSocketEnabled":true}')


if __name__ == '__main__':
    main()
