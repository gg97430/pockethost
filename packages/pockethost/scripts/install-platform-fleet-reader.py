#!/usr/bin/env python3
"""Idempotent root setup for fixed-peer, forced-command fleet reads.

Two phases, over the operator's existing trusted administrative SSH transport:
  prepare app4 -> public keys (one outbound key per peer) + this host public key
  connect      <- JSON public material from the other peers on stdin

No private key leaves its source host. Host public keys MUST be collected through
the existing authenticated transport; this installer never uses ssh-keyscan or
trust-on-first-use. Installing this reader does not restart a service.
"""
import base64
import json
import os
import pathlib
import pwd
import stat
import subprocess
import sys
import tempfile

SCRIPT = pathlib.Path('/opt/pockethost-web/platform_fleet_reader.py')
ROOT = pathlib.Path('/etc/kolab/fleet')
HOME = pathlib.Path('/var/lib/kolab-fleet-reader')
USERNAME = 'kolab-fleet'
SUDOERS = pathlib.Path('/etc/sudoers.d/kolab-fleet-reader')
MANIFEST = pathlib.Path('/var/lib/kolab-platform-release.json')
UNIT_ROOT = pathlib.Path('/etc/systemd/system')
UNITS = ('pockethost-fleet-reader.socket', 'pockethost-fleet-reader@.service')
PEERS = {
    'app3': {'address': '135.125.67.39', 'hostname': 'app3.monappli.re'},
    'app4': {'address': '51.255.69.154', 'hostname': 'app4.monappli.re'},
    'app5': {'address': '217.182.134.72', 'hostname': 'app5.monappli.re'},
}
START = '# BEGIN KOLAB FLEET READER'
END = '# END KOLAB FLEET READER'


def public_key(value):
    if not isinstance(value, str) or len(value) > 300 or '\n' in value or '\r' in value:
        raise ValueError('Invalid public key')
    parts = value.split()
    if len(parts) not in (2, 3) or parts[0] != 'ssh-ed25519':
        raise ValueError('Invalid public key')
    raw = base64.b64decode(parts[1], validate=True)
    if len(raw) != 51 or raw[:19] != b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20':
        raise ValueError('Invalid public key')
    return 'ssh-ed25519 ' + parts[1]


def secure_directory(path, mode):
    path = pathlib.Path(path)
    path.mkdir(parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
        raise ValueError('Untrusted directory')
    os.chmod(path, mode)


def read(path, maximum=16384, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & (0o077 if private else 0o022) or info.st_size > maximum:
            raise ValueError('Untrusted file')
        return os.read(fd, maximum + 1)
    finally:
        os.close(fd)


def write(path, content, mode=0o600):
    path = pathlib.Path(path)
    if path.exists() or path.is_symlink():
        read(path, max(len(content) + 1, 65536))
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def identity(name):
    if name not in PEERS:
        raise ValueError('Unknown server')
    manifest = json.loads(read(MANIFEST, 4 * 1024 * 1024, private=True))
    if manifest.get('hostname') != PEERS[name]['hostname']:
        raise ValueError('Server identity differs from release manifest')


def install_local_socket():
    config = json.loads(read(ROOT / 'config.json', private=True))
    identity(config.get('self'))
    source = pathlib.Path(__file__).resolve().parent / 'systemd'
    for name in UNITS:
        candidate = source / name
        if candidate.exists():
            info = candidate.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
                raise ValueError('Untrusted reader unit source')
            write(UNIT_ROOT / name, candidate.read_bytes(), 0o644)
        read(UNIT_ROOT / name, 16384)
    subprocess.run(['/usr/bin/systemctl', 'daemon-reload'], check=True, capture_output=True, timeout=15)
    subprocess.run(['/usr/bin/systemctl', 'enable', '--now', 'pockethost-fleet-reader.socket'], check=True, capture_output=True, timeout=15)
    return {'socketEnabled': True}


def prepare(name):
    identity(name)
    read(SCRIPT, 65536)
    secure_directory(ROOT, 0o700)
    secure_directory(ROOT / 'keys', 0o700)
    secure_directory(HOME, 0o755)
    # sshd checks authorized_keys under the reader's UID; root owns the public
    # directory/file but they must remain readable/traversable by that UID.
    secure_directory(HOME / '.ssh', 0o755)
    try:
        account = pwd.getpwnam(USERNAME)
        if account.pw_uid < 1 or account.pw_dir != str(HOME) or account.pw_shell != '/bin/sh':
            raise ValueError('Unexpected reader account')
    except KeyError:
        subprocess.run(['/usr/sbin/useradd', '--system', '--user-group', '--home-dir', str(HOME), '--shell', '/bin/sh', '--password', '*', USERNAME], check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    keys = {}
    for peer in PEERS:
        if peer == name:
            continue
        path = ROOT / 'keys' / peer
        if not path.exists():
            if path.is_symlink():
                raise ValueError('Untrusted key path')
            subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'kolab-fleet:' + name + ':' + peer, '-f', str(path)], check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        read(path, private=True)
        keys[peer] = public_key(read(path.with_suffix('.pub')).decode().strip())
    if not (ROOT / 'known_hosts').exists():
        write(ROOT / 'known_hosts', b'')
    if not (ROOT / 'config.json').exists():
        write(ROOT / 'config.json', json.dumps({'version': 1, 'self': name, 'peers': []}).encode())
    else:
        current = json.loads(read(ROOT / 'config.json', private=True))
        if current.get('self') != name:
            raise ValueError('Existing fleet identity differs')
    sudoers = (
        '# Kolab read-only fleet helper. Never grant shell/SQL/Docker access.\n'
        'kolab-fleet ALL=(root) NOPASSWD: /usr/bin/python3 -I /opt/pockethost-web/platform_fleet_reader.py serve\n'
        'ubuntu ALL=(root) NOPASSWD: /usr/bin/python3 -I /opt/pockethost-web/platform_fleet_reader.py fetch *\n'
    ).encode()
    with tempfile.NamedTemporaryFile(dir=SUDOERS.parent, prefix='.kolab-fleet-', delete=False) as handle:
        temporary = pathlib.Path(handle.name)
        handle.write(sudoers)
    try:
        os.chmod(temporary, 0o440)
        subprocess.run(['/usr/sbin/visudo', '-cf', str(temporary)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        write(SUDOERS, sudoers, 0o440)
    finally:
        temporary.unlink(missing_ok=True)
    install_local_socket()
    return {'version': 1, 'self': name, 'hostname': PEERS[name]['hostname'], 'hostKey': public_key(read('/etc/ssh/ssh_host_ed25519_key.pub').decode().strip()), 'keys': keys}


def connect(value):
    if not isinstance(value, dict) or set(value) != {'version', 'self', 'peers'} or type(value['version']) is not int or value['version'] != 1 or value['self'] not in PEERS:
        raise ValueError('Invalid trust input')
    name = value['self']
    identity(name)
    if not isinstance(value['peers'], list) or len(value['peers']) > 2:
        raise ValueError('Invalid peer material')
    clean = {}
    for item in value['peers']:
        if not isinstance(item, dict) or set(item) != {'key', 'publicKey', 'hostKey'} or item['key'] not in PEERS or item['key'] == name or item['key'] in clean:
            raise ValueError('Invalid peer identity')
        clean[item['key']] = (public_key(item['publicKey']), public_key(item['hostKey']))
        read(ROOT / 'keys' / item['key'], private=True)
    # Force exactly this non-mutating reader. `restrict` forbids PTY, forwarding,
    # agent/X11, user rc and TCP forwarding. `from` binds each key to its peer IP.
    command = '/usr/bin/sudo -n /usr/bin/python3 -I /opt/pockethost-web/platform_fleet_reader.py serve'
    lines = [START]
    for peer, (key, _) in sorted(clean.items()):
        lines.append('from="' + PEERS[peer]['address'] + '",restrict,command="' + command + '" ' + key + ' kolab-fleet:' + peer + ':' + name)
    lines.append(END)
    authorized = HOME / '.ssh/authorized_keys'
    existing = read(authorized, 65536).decode() if authorized.exists() or authorized.is_symlink() else ''
    if existing.count(START) != existing.count(END) or existing.count(START) > 1:
        raise ValueError('Invalid existing managed key block')
    if START in existing:
        before, rest = existing.split(START, 1)
        _, after = rest.split(END, 1)
        existing = before.rstrip() + '\n' + after.lstrip('\n')
    # Dedicated reader home contains no private key, controller data or secrets.
    write(authorized, (existing.rstrip() + ('\n' if existing.strip() else '') + '\n'.join(lines) + '\n').encode(), 0o644)
    write(ROOT / 'known_hosts', ''.join(PEERS[peer]['address'] + ' ' + hostkey + '\n' for peer, (_, hostkey) in sorted(clean.items())).encode())
    write(ROOT / 'config.json', json.dumps({'version': 1, 'self': name, 'peers': sorted(clean)}).encode())
    return {'version': 1, 'self': name, 'peers': sorted(clean), 'restartRequired': False}


def main():
    if os.geteuid() != 0:
        raise PermissionError('Root installer required')
    os.umask(0o077)
    if len(sys.argv) == 3 and sys.argv[1] == 'prepare':
        result = prepare(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] == 'connect':
        content = sys.stdin.buffer.read(16385)
        if len(content) > 16384:
            raise ValueError('Trust material too large')
        result = connect(json.loads(content))
    elif len(sys.argv) == 2 and sys.argv[1] == 'socket':
        result = install_local_socket()
    else:
        raise ValueError('Use prepare SERVER or connect')
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"error":"fleet_trust_setup_failed"}')
        sys.exit(1)
