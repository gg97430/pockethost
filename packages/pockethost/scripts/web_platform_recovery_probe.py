#!/usr/bin/env python3
"""Verify an inert restore; native startup requires a separate Linux network namespace.

`native-proof` (root) launches the native start in a transient systemd unit with
PrivateNetwork=true as the restore owner; dry-run by default, prints the exact command.
"""
import argparse
import contextlib
import hashlib
import json
import os
import pathlib
import platform
import pwd
import re
import secrets
import shlex
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.request

import web_platform_recovery as engine


def digest(filename):
    result = hashlib.sha256()
    with open(filename, 'rb') as source:
        for chunk in iter(lambda: source.read(1024**2), b''): result.update(chunk)
    return result.hexdigest()


def write_report(filename, value):
    filename = pathlib.Path(filename).absolute()
    if filename.parent.resolve() != filename.parent or filename.exists() and filename.resolve() != filename:
        raise engine.RecoveryError('Rapport privé non canonique.')
    fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as target:
        os.fchmod(target.fileno(), 0o600); json.dump(value, target, sort_keys=True); target.flush(); os.fsync(target.fileno())


def isolated_network(host_inode=None):
    try:
        # Unprivileged Ubuntu cannot inspect PID1's namespace. The root-owned
        # transient-unit launcher may provide that inode, never a browser value.
        host_inode = host_inode if host_inode is not None else os.stat('/proc/1/ns/net').st_ino
        interfaces = {line.split(':', 1)[0].strip() for line in pathlib.Path('/proc/net/dev').read_text().splitlines()[2:]}
        return type(host_inode) is int and host_inode > 0 and os.stat('/proc/self/ns/net').st_ino != host_inode and interfaces == {'lo'}
    except OSError: return False


@contextlib.contextmanager
def readonly(filename):
    # A copy started natively (or by an earlier attempt) is in WAL mode: see engine.connect_copy.
    connection = engine.connect_copy(filename)
    try: yield connection
    finally: connection.close()


def load_restore(directory, expected, owners=None):
    root = pathlib.Path(directory).absolute()
    if root.resolve() != root or not root.is_dir() or root.stat().st_mode & 0o077 or owners and root.stat().st_uid not in owners: raise engine.RecoveryError('Le test exige une restauration privée canonique.')
    marker = json.loads(engine.private_file(root / 'RECOVERY_PAUSED.json', owners).read_bytes())
    manifest = json.loads(engine.private_file(root / 'recovery-manifest.json', owners).read_bytes())
    if marker.get('paused') is not True or marker.get('noServicesStarted') is not True or marker.get('backupId') != manifest.get('backupId'):
        raise engine.RecoveryError('La restauration n’est pas confirmée comme suspendue.')
    chosen = set(marker.get('databasesRestored', [])); definitions = {item['id']: item for item in manifest.get('databases', []) if item['id'] in chosen}
    if len(definitions) != expected or len(chosen) != expected or 'controller' not in chosen: raise engine.RecoveryError('La couverture des bases du test est incomplète.')
    return root, marker, manifest, chosen, definitions


def verify_restore(directory, expected):
    root, marker, manifest, chosen, definitions = load_restore(directory, expected)
    result = {'backupId': marker['backupId'], 'verifiedAt': engine.stamp(), 'databases': [], 'files': 0, 'fileBytes': 0, 'mfaSidecars': 0, 'hostFilesRestored': marker.get('hostFilesRestored', True) is True, 'nativeArchivesRestored': marker.get('nativeArchivesRestored', True) is True, 'nativeHealthy': 0, 'noExternalNetwork': False}
    for ident, definition in definitions.items():
        filename = root / engine.safe_relative(definition['target']) / 'data.db'
        with readonly(filename) as connection:
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]: raise engine.RecoveryError('Une base reconstruite échoue au contrôle SQLite.')
            rows = engine.settings_records(connection)
            if not rows: raise engine.RecoveryError('Les paramètres natifs suspendus sont illisibles.')
            for _column, _name, raw in rows:
                try: settings = json.loads(raw)
                except Exception: raise engine.RecoveryError('Les paramètres natifs suspendus sont illisibles.') from None
                if settings.get('smtp', {}).get('enabled') is not False or settings.get('s3', {}).get('enabled') is not False or settings.get('backups', {}).get('cron') != '' or settings.get('backups', {}).get('s3', {}).get('enabled') is not False:
                    raise engine.RecoveryError('Un service natif n’est pas suspendu dans la copie.')
            key_restored = False
            if engine.totp_configured(connection) or definition.get('mfa', {}).get('keyCaptured'):
                key_target = definition['target'] + '/' + engine.TOTP_SIDECAR
                description = next((item for item in manifest.get('files', []) if item['target'] == key_target), None)
                if description is None: raise engine.RecoveryError('Une clé TOTP ne figure pas dans la capture authentifiée.')
                try: keyfile = engine.private_file(root / key_target)
                except Exception: raise engine.RecoveryError('Une clé TOTP restaurée est absente ou n’est pas privée.') from None
                if keyfile.stat().st_mode & 0o777 != 0o600 or digest(keyfile) != description['sha256']: raise engine.RecoveryError('Une clé TOTP restaurée ne correspond pas au manifeste privé.')
                key_restored = True; result['mfaSidecars'] += 1
            result['databases'].append({'id': ident, 'integrity': 'ok', 'tables': len(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()), 'nativeSettingsPaused': True, 'mfaSidecarRestored': key_restored, 'sha256BeforeNativeProbe': digest(filename)})
    for description in manifest.get('files', []):
        relative = description['target']
        instance = re.match(r'^instances/([a-z0-9]{15})/', relative)
        if relative.startswith('controller/') and 'controller' not in chosen or instance and instance.group(1) not in chosen: continue
        if relative.startswith('host-files/') and not result['hostFilesRestored'] or relative.startswith('native-backups/') and not result['nativeArchivesRestored']: continue
        filename = root / engine.safe_relative(description['target'])
        if not filename.is_file() or filename.resolve() != filename or filename.stat().st_size != description['size'] or digest(filename) != description['sha256']:
            raise engine.RecoveryError('Un fichier restauré ne correspond pas au manifeste authentifié.')
        result['files'] += 1; result['fileBytes'] += description['size']
    return root, marker, manifest, definitions, result


def resolve_version(version, versions):
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.(?:\d+|\*)', version): raise engine.RecoveryError('Version native à vérifier invalide.')
    matches = [value for value in versions if value.startswith(version[:-1])] if version.endswith('*') else [value for value in versions if value == version]
    if not matches: raise engine.RecoveryError('Le binaire correspondant manque dans la capture.')
    return max(matches, key=lambda value: tuple(int(part) for part in value.split('.')))


def native_arch(machine=None):
    # Same asset suffix as PocketBaseBinaryService (linux_<amd64|arm64>).
    value = (machine or platform.machine()).lower()
    if value in ('x86_64', 'amd64'): return 'linux_amd64'
    if value in ('aarch64', 'arm64'): return 'linux_arm64'
    raise engine.RecoveryError('Architecture native non prise en charge pour la preuve de démarrage.')


def native_binaries(root, manifest, definitions, runtime_root, controller_version, arch=None, check=True):
    """Resolve and hash-check every binary before anything starts: {ident: (version, path)}."""
    arch = arch or native_arch(); runtime_root = pathlib.Path(runtime_root).absolute()
    if runtime_root.resolve() != runtime_root: raise engine.RecoveryError('Racine des binaires non canonique.')
    binaries = {}
    for item in manifest.get('files', []):
        match = re.fullmatch(r'host-files/platform/pocketbase/(\d+\.\d+\.\d+)/(linux_amd64|linux_arm64)/pocketbase', item['target'])
        if match and match.group(2) == arch: binaries[match.group(1)] = item['sha256']
    versions = {'controller': resolve_version(controller_version, binaries)}
    with readonly(root / definitions['controller']['target'] / 'data.db') as connection:
        versions.update({ident: resolve_version(version, binaries) for ident, version in connection.execute('SELECT id,version FROM instances') if ident in definitions})
    if set(versions) != set(definitions): raise engine.RecoveryError('Une instance ne possède pas de version native vérifiable.')
    result, checked = {}, {}
    for ident, version in versions.items():
        binary = runtime_root / version / arch / 'pocketbase'
        if check and version not in checked:
            if binary.resolve() != binary or not binary.is_file() or not os.access(binary, os.X_OK) or digest(binary) != binaries[version]: raise engine.RecoveryError('Le binaire natif diffère de celui de la capture.')
            checked[version] = True
        result[ident] = (version, binary)
    return result


def native_directory(root):
    """Fresh private attempt directory; removes only earlier attempts it created itself."""
    parent = root / 'native-probe'
    if os.path.lexists(parent):
        info = os.lstat(parent)
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid(): raise engine.RecoveryError('Dossier de preuve native existant non privé ; nettoyage refusé.')
        entries = list(parent.iterdir())
        if any(not re.fullmatch(r'attempt-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}', item.name) or item.is_symlink() or not item.is_dir() for item in entries):
            raise engine.RecoveryError('Contenu inattendu dans le dossier de preuve native ; nettoyage refusé.')
        for item in entries: shutil.rmtree(item)
        removed = len(entries)
    else:
        parent.mkdir(mode=0o700); removed = 0
    private = parent / ('attempt-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + secrets.token_hex(4)); private.mkdir(mode=0o700)
    return private, removed


def native_probe(root, manifest, definitions, runtime_root, controller_version, result, host_inode=None, arch=None):
    if not isolated_network(host_inode): raise engine.RecoveryError('Le démarrage natif exige une unité Linux avec PrivateNetwork=true.')
    result['noExternalNetwork'] = True
    resolved = native_binaries(root, manifest, definitions, runtime_root, controller_version, arch)
    private, result['previousNativeAttemptsRemoved'] = native_directory(root)
    result['nativeAttempt'] = private.name
    empty = [private / name for name in ['hooks', 'migrations', 'public', 'home']]
    for directory in empty: directory.mkdir(mode=0o700)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for ident, definition in definitions.items():
        version, binary = resolved[ident]
        with socket.socket() as listener: listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
        log_path = private / (ident + '.log'); log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        command = [str(binary), 'serve', '--http=127.0.0.1:' + str(port), '--dir=' + str(root / definition['target']), '--hooksDir=' + str(empty[0]), '--migrationsDir=' + str(empty[1]), '--publicDir=' + str(empty[2]), '--automigrate=false', '--dev=false', '--hooksWatch=false']
        child = None; healthy = False
        try:
            with os.fdopen(log_fd, 'wb') as log:
                child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, env={'PATH': '/usr/bin:/bin', 'HOME': str(empty[3]), 'TZ': 'UTC'}, cwd=private)
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline and child.poll() is None:
                    try:
                        with opener.open('http://127.0.0.1:' + str(port) + '/api/health', timeout=1) as response:
                            if response.status == 200 and len(response.read(8193)) <= 8192: healthy = True; break
                    except Exception: time.sleep(.2)
                if not healthy: raise engine.RecoveryError('Une base restaurée refuse le démarrage PocketBase isolé.')
        finally:
            if child is not None and child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try: child.wait(timeout=15)
                except subprocess.TimeoutExpired: os.killpg(child.pid, signal.SIGKILL); child.wait()
        result['nativeHealthy'] += 1
        next(item for item in result['databases'] if item['id'] == ident).update(nativeHealthy=True, version=version, stopped=True)
        with readonly(root / definition['target'] / 'data.db') as connection:
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]: raise engine.RecoveryError('Le contrôle après démarrage natif a échoué.')
    result['nativeProcessesStopped'] = True


UNIT_PROPERTIES = ['PrivateNetwork=yes', 'NoNewPrivileges=yes', 'PrivateTmp=yes', 'PrivateDevices=yes', 'ProtectSystem=strict', 'ProtectHome=read-only', 'IPAddressDeny=any', 'IPAddressAllow=localhost', 'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6', 'MemoryMax=2G', 'CPUQuota=100%', 'TasksMax=256', 'UMask=0077']
MAX_SECONDS = (300, 1800, 21600)  # min, default, max for the whole unit (verify + native starts).


def native_proof_command(args, user, host_inode, unit):
    probe = str(pathlib.Path(__file__).absolute())
    command = [args.systemd_run, '--uid=' + str(user.pw_uid), '--gid=' + str(user.pw_gid), '--unit=' + unit, '--wait', '--pipe', '--collect', '--quiet']
    for value in UNIT_PROPERTIES + ['RuntimeMaxSec=' + str(args.max_seconds), 'ReadWritePaths=' + str(args.root), 'ReadWritePaths=' + str(pathlib.Path(args.report).absolute().parent), 'WorkingDirectory=' + str(args.root)]: command += ['-p', value]
    command += ['--', args.python, '-E', '-s', probe, '--root', str(args.root), '--expected-databases', str(args.expected_databases), '--report', str(pathlib.Path(args.report).absolute()), '--runtime-root', str(args.runtime_root), '--controller-version', args.controller_version, '--host-netns-inode', str(host_inode)]
    if args.arch: command += ['--arch', args.arch]
    return command


def native_proof(argv):
    parser = argparse.ArgumentParser(prog='web_platform_recovery_probe.py native-proof', description='Root launcher for the isolated native start proof (dry-run unless --execute).')
    parser.add_argument('--root', required=True); parser.add_argument('--expected-databases', type=int, required=True); parser.add_argument('--report', required=True)
    parser.add_argument('--runtime-root', required=True); parser.add_argument('--controller-version', required=True); parser.add_argument('--user', required=True, help='Owner of the restore (never root)')
    parser.add_argument('--arch', choices=['linux_amd64', 'linux_arm64']); parser.add_argument('--execute', action='store_true')
    parser.add_argument('--max-seconds', type=int, default=MAX_SECONDS[1], help='RuntimeMaxSec of the unit (300-21600; large databases such as app3 need more)')
    parser.add_argument('--systemd-run', default='/usr/bin/systemd-run'); parser.add_argument('--systemctl', default='/usr/bin/systemctl'); parser.add_argument('--python', default='/usr/bin/python3')
    args = parser.parse_args(argv)
    if not MAX_SECONDS[0] <= args.max_seconds <= MAX_SECONDS[2]: raise engine.RecoveryError('Durée maximale de la preuve native hors bornes (300 à 21600 secondes).')
    try: user = pwd.getpwnam(args.user)
    except KeyError: raise engine.RecoveryError('Utilisateur de la restauration inconnu.') from None
    if user.pw_uid == 0: raise engine.RecoveryError('La preuve native ne s’exécute jamais en root.')
    args.root = pathlib.Path(args.root).absolute(); report = pathlib.Path(args.report).absolute()
    if report.parent.resolve() != report.parent or report == args.root or args.root in report.parents: raise engine.RecoveryError('Le rapport doit être hors de la copie, dans un dossier canonique.')
    # Pre-flight as the launcher: marker, coverage and exact binary hashes, before any unit exists.
    root, _marker, manifest, _chosen, definitions = load_restore(args.root, args.expected_databases, (user.pw_uid,))
    resolved = native_binaries(root, manifest, definitions, args.runtime_root, args.controller_version, args.arch)
    if args.execute and os.geteuid() != 0: raise engine.RecoveryError('Le lanceur natif exige root pour lire l’espace réseau de PID 1.')
    try: host_inode = os.stat('/proc/1/ns/net').st_ino
    except OSError:
        if args.execute: raise engine.RecoveryError('Espace réseau de PID 1 illisible.') from None
        host_inode = '<INODE_NETNS_PID1>'
    unit = 'pockethost-recovery-probe-' + secrets.token_hex(4)
    command = native_proof_command(args, user, host_inode, unit)
    plan = {'mode': 'execute' if args.execute else 'dry-run', 'unit': unit, 'command': command, 'shell': shlex.join(command), 'binaries': {ident: version for ident, (version, _path) in sorted(resolved.items())}, 'hashesVerified': True, 'maxSeconds': args.max_seconds}
    if not args.execute: print(json.dumps(plan)); return 0
    try: completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=args.max_seconds + 100)
    finally:
        # Stop afterwards even if the launcher was interrupted; a collected unit is already gone.
        subprocess.run([args.systemctl, 'stop', unit], stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    active = subprocess.run([args.systemctl, 'is-active', unit], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30).stdout.strip()
    try: outcome = json.loads(completed.stdout.strip().splitlines()[-1])
    except Exception: outcome = {'passed': False}
    passed = completed.returncode == 0 and outcome.get('passed') is True and outcome.get('nativeHealthy') == args.expected_databases and outcome.get('noExternalNetwork') is True and active != 'active'
    print(json.dumps({**plan, 'passed': passed, 'unitStopped': active != 'active', 'probe': {name: outcome.get(name) for name in ['passed', 'databases', 'nativeHealthy', 'noExternalNetwork', 'error']}}))
    return 0 if passed else 1


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == 'native-proof':
        try: sys.exit(native_proof(sys.argv[2:]))
        except engine.RecoveryError as error: print(json.dumps({'passed': False, 'error': str(error)})); sys.exit(1)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True); parser.add_argument('--expected-databases', type=int, required=True); parser.add_argument('--report', required=True)
    parser.add_argument('--runtime-root'); parser.add_argument('--controller-version'); parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--host-netns-inode', type=int, help='PID1 network namespace inode, supplied by the privileged unit launcher')
    parser.add_argument('--arch', choices=['linux_amd64', 'linux_arm64'], help='Captured binary architecture (default: this host)')
    args = parser.parse_args(); result = {'passed': False, 'verifiedAt': engine.stamp()}
    try:
        root, marker, manifest, definitions, result = verify_restore(args.root, args.expected_databases)
        if not args.verify_only:
            if not args.runtime_root or not args.controller_version: raise engine.RecoveryError('Le test natif exige les binaires et la version du contrôleur.')
            native_probe(root, manifest, definitions, args.runtime_root, args.controller_version, result, args.host_netns_inode, args.arch)
        result['passed'] = True; write_report(args.report, result)
        print(json.dumps({'passed': True, 'databases': len(result['databases']), 'files': result['files'], 'fileBytes': result['fileBytes'], 'mfaSidecars': result['mfaSidecars'], 'nativeHealthy': result['nativeHealthy'], 'noExternalNetwork': result['noExternalNetwork']}))
    except Exception:
        result.update(passed=False, error='La preuve de reprise isolée n’est pas complète. Consultez les journaux privés de la copie.')
        write_report(args.report, result); print(json.dumps({'passed': False, 'error': result['error']})); sys.exit(1)


if __name__ == '__main__': main()
