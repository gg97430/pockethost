#!/usr/bin/env python3
"""Seal root-owned project tooling configuration for the platform backup account.

The archive key never becomes readable by the backup account. Restore produces a
new private quarantine, never installs capabilities or activates a copied job.
Transfer capsules remain operator artifacts and must be kept separately.
"""
import argparse, base64, hashlib, hmac, json, os, pathlib, re, secrets, stat, sys, tempfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from platform_fleet_recovery import read

FILES = {name: pathlib.Path('/etc/kolab') / name for name in (
    'project-recovery.json', 'project-recovery.key', 'project-recovery-controller.key',
    'project-migration.json', 'project-migration-control.key')}
JOURNALS = pathlib.Path('/var/lib/kolab-project-migration')
OUTPUT = pathlib.Path('/var/lib/kolab-project-tools-recovery/configuration.json.enc')
SERVERS = ('app3', 'app4', 'app5')
MAXIMUM = 8 * 1024 * 1024


def wrapping_key(filename, owners=(0, 1000)):
    raw = read(filename, 128, owners, True).decode().strip()
    if not re.fullmatch('[a-fA-F0-9]{64}', raw): raise ValueError('Invalid wrapping key')
    return hmac.new(bytes.fromhex(raw), b'Kolab project tools recovery v1', hashlib.sha256).digest()


def valid_inventory(files):
    if not isinstance(files, dict) or not set(FILES) <= set(files) or len(files) > 261:
        raise ValueError('Incomplete configuration inventory')
    if any(name not in FILES and not re.fullmatch(r'journals/[a-z0-9]{15}\.json', name) for name in files):
        raise ValueError('Unexpected restore destination')


def encode(files, server, key):
    valid_inventory(files)
    if server not in SERVERS: raise ValueError('Invalid server')
    raw = json.dumps({'version': 1, 'server': server, 'files': {n: base64.b64encode(v).decode() for n, v in sorted(files.items())}}, separators=(',', ':')).encode()
    if len(raw) > MAXIMUM: raise ValueError('Oversized inventory')
    nonce = secrets.token_bytes(12)
    return json.dumps({'version': 1, 'server': server, 'nonce': base64.b64encode(nonce).decode(), 'ciphertext': base64.b64encode(AESGCM(key).encrypt(nonce, raw, ('Kolab project tools v1 ' + server).encode())).decode()}, separators=(',', ':')).encode()


def decode(raw, server, key):
    if len(raw) > MAXIMUM * 2 or server not in SERVERS: raise ValueError('Invalid export')
    value = json.loads(raw)
    if set(value) != {'version', 'server', 'nonce', 'ciphertext'} or value['version'] != 1 or value['server'] != server:
        raise ValueError('Wrong server export')
    plain = AESGCM(key).decrypt(base64.b64decode(value['nonce'], validate=True), base64.b64decode(value['ciphertext'], validate=True), ('Kolab project tools v1 ' + server).encode())
    value = json.loads(plain)
    if set(value) != {'version', 'server', 'files'} or value['version'] != 1 or value['server'] != server: raise ValueError('Invalid inventory')
    valid_inventory(value['files'])
    return {n: base64.b64decode(v, validate=True) for n, v in value['files'].items()}


def seal(config, server):
    if os.geteuid() != 0: raise PermissionError('Root sealing required')
    configuration = json.loads(read(config, 16384, (0, 1000), True))
    files = {name: read(path, 16384) for name, path in FILES.items()}
    if JOURNALS.exists():
        info = JOURNALS.lstat()
        if JOURNALS.resolve() != JOURNALS or info.st_uid != 0 or info.st_mode & 0o077: raise ValueError('Untrusted journal directory')
        for path in sorted(JOURNALS.iterdir()):
            if re.fullmatch(r'[a-z0-9]{15}\.json', path.name): files['journals/' + path.name] = read(path, 262144, private=True)
    key = wrapping_key(configuration['keyFile'])
    raw = encode(files, server, key)
    if decode(raw, server, key) != files: raise ValueError('Export verification failed')
    OUTPUT.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    info = OUTPUT.parent.lstat()
    if OUTPUT.parent.resolve() != OUTPUT.parent or info.st_uid != 0 or info.st_mode & 0o022: raise ValueError('Untrusted export directory')
    os.chown(OUTPUT.parent, 0, 1000); os.chmod(OUTPUT.parent, 0o750)
    if OUTPUT.is_symlink(): raise ValueError('Symbolic output refused')
    fd, temporary = tempfile.mkstemp(prefix='.configuration-', dir=OUTPUT.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            os.fchmod(out.fileno(), 0o640); os.fchown(out.fileno(), 0, 1000); out.write(raw); out.flush(); os.fsync(out.fileno())
        os.replace(temporary, OUTPUT)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return {'sealed': True, 'server': server, 'files': len(files), 'sha256': hashlib.sha256(raw).hexdigest(), 'sourcePermissionsChanged': False}


def unseal(source, key_file, server, destination):
    files = decode(read(source, MAXIMUM * 2, (0, 1000, os.geteuid())), server, wrapping_key(key_file, (0, 1000, os.geteuid())))
    destination = pathlib.Path(destination).absolute()
    if destination.resolve() != destination or destination.exists(): raise ValueError('New quarantine required')
    info = destination.parent.stat()
    if info.st_uid != os.geteuid() or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022: raise ValueError('Private caller-owned parent required')
    destination.mkdir(mode=0o700)
    for name, raw in files.items():
        path = destination / name; path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open('xb') as out: os.fchmod(out.fileno(), 0o600); out.write(raw)
    return {'unsealed': True, 'server': server, 'files': len(files), 'liveConfigurationChanged': False}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest='action', required=True)
    p = commands.add_parser('seal'); p.add_argument('--config', required=True); p.add_argument('--server', choices=SERVERS, required=True)
    p = commands.add_parser('unseal'); p.add_argument('--source', required=True); p.add_argument('--key-file', required=True); p.add_argument('--server', choices=SERVERS, required=True); p.add_argument('--destination', required=True)
    args = parser.parse_args()
    print(json.dumps(seal(args.config, args.server) if args.action == 'seal' else unseal(args.source, args.key_file, args.server, args.destination)))


if __name__ == '__main__':
    try: main()
    except Exception: print('{"error":"project_tools_recovery_export_failed"}'); raise SystemExit(1)
