#!/usr/bin/env python3
"""Project recovery capsules: authenticated full artifacts and networkless rehearsal.

Never stop/freeze/restart an original service. `capture` reads only selected native
SQLite databases, files, published images and named volumes. Native DBs use the
existing read-transaction recovery engine. The complete flag is refused on missing
or unstable dependencies; application/distributed consistency is a separate field.
All execution uses restored copies in a NEW network=none namespace. This module is
an operator engine: file paths are trusted installer/config inputs, not API inputs.
"""
import argparse
import contextlib
import fcntl
import hashlib
import io
import http.client
import socket
import json
import os
import pathlib
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import web_platform_recovery as recovery

ID = re.compile(r'^[a-z0-9]{15}$')
IMAGE = re.compile(r'^sha256:[a-f0-9]{64}$')
NAME = re.compile(r'^[a-z][a-z0-9-]{0,30}$')
LIMIT = 32 * 1024**3
RESERVE = 15 * 1024**3
MAX_FILES = 100000
SOURCE_VOLUME_ROOT = pathlib.Path('/home/pockethost-web/.local/share/docker/volumes')


class ProjectRecoveryError(recovery.RecoveryError): pass


def encoded(value): return recovery.encoded(value)
def open_regular(path):
    path = pathlib.Path(path).absolute()
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory); os.close(directory); directory = child
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(descriptor); raise ProjectRecoveryError('Fichier non régulier ou lié hors du projet refusé.')
        return os.fdopen(descriptor, 'rb')
    finally: os.close(directory)


def digest(path):
    value = hashlib.sha256()
    with open_regular(path) as stream:
        for block in iter(lambda: stream.read(1024**2), b''): value.update(block)
    return value.hexdigest()


def private_json(path, value):
    path = pathlib.Path(path).absolute()
    if path.parent.resolve() != path.parent or os.path.lexists(path) and path.resolve() != path:
        raise ProjectRecoveryError('Chemin privé non canonique.')
    with open(path, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o600); stream.write(encoded(value)); stream.flush(); os.fsync(stream.fileno())


def canonical(path, directory=False):
    path = pathlib.Path(path).absolute()
    if path.resolve() != path or (not path.is_dir() if directory else not path.is_file()):
        raise ProjectRecoveryError('Source absente ou symbolique refusée.')
    return path


def validate_snapshot(value):
    if not isinstance(value, dict) or value.get('version') != 1 or not ID.fullmatch(value.get('projectId', '')):
        raise ProjectRecoveryError('Projet de reprise invalide.')
    if not isinstance(value.get('apps'), list) or not 1 <= len(value['apps']) <= 20:
        raise ProjectRecoveryError('Sélection de une à vingt applications requise.')
    apps, databases = set(), set()
    for app in value['apps']:
        if not isinstance(app, dict) or not ID.fullmatch(app.get('id', '')) or app['id'] in apps or not ID.fullmatch(app.get('deploymentId', '')):
            raise ProjectRecoveryError('Application publiée invalide ou dupliquée.')
        apps.add(app['id'])
        if not isinstance(app.get('bindings', []), list) or len(app.get('bindings', [])) > 4:
            raise ProjectRecoveryError('Liaisons de dépendances invalides.')
        ports = set()
        for binding in app.get('bindings', []):
            if not ID.fullmatch(binding.get('instanceId', '')) or type(binding.get('port')) is not int or not 1024 <= binding['port'] <= 65535 or binding['port'] in ports:
                raise ProjectRecoveryError('Port ou base de dépendance invalide.')
            ports.add(binding['port']); databases.add(binding['instanceId'])
        if not isinstance(app.get('externalDependencies', []), list) or any(not isinstance(name, str) or len(name) > 100 for name in app.get('externalDependencies', [])):
            raise ProjectRecoveryError('Dépendances externes invalides.')
    if len(databases) > 20: raise ProjectRecoveryError('Vingt bases maximum par reprise.')
    return sorted(apps), sorted(databases)


class Docker:
    """One explicitly selected rootless engine; no shell and bounded output/time."""
    def __init__(self, socket='/run/user/1001/docker.sock'):
        if not re.fullmatch(r'/run/user/[1-9][0-9]{0,7}/docker.sock', socket):
            raise ProjectRecoveryError('Moteur rootless requis.')
        self.socket = socket
        self.command = ['/usr/bin/docker', '--host', 'unix://' + socket]
    def __call__(self, args, timeout=30, input=None):
        result = subprocess.run(self.command + args, input=input, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, check=True)
        if len(result.stdout) > 16 * 1024**2: raise ProjectRecoveryError('Réponse Docker trop volumineuse.')
        return result.stdout.decode()
    def image_exists(self, image):
        if not IMAGE.fullmatch(image): raise ProjectRecoveryError('Image de copie invalide.')
        connection = http.client.HTTPConnection('localhost', timeout=10)
        def connect():
            connection.sock = socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); connection.sock.settimeout(10); connection.sock.connect(self.socket)
        connection.connect = connect
        try:
            connection.request('GET','/v1.47/images/' + image + '/json')
            response = connection.getresponse(); raw=response.read(4*1024**2+1)
            if response.status==404:return False
            if response.status!=200 or len(raw)>4*1024**2 or json.loads(raw).get('Id')!=image: raise ProjectRecoveryError('Inventaire des images non confirmé.')
            return True
        finally: connection.close()
    def info(self):
        connection = http.client.HTTPConnection('localhost', timeout=4)
        def connect():
            connection.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);connection.sock.settimeout(4);connection.sock.connect(self.socket)
        connection.connect=connect
        try:
            connection.request('GET','/v1.47/info');response=connection.getresponse();raw=response.read(4*1024**2+1)
            if response.status!=200 or len(raw)>4*1024**2:raise ProjectRecoveryError('Moteur de copie indisponible.')
            return json.loads(raw)
        finally:connection.close()
    def create_sandbox(self, args, environment):
        # Private values travel only in the Unix API body, never CLI argv or
        # the root Docker client's environment (which could influence loaders).
        if self.socket != '/run/user/1002/docker.sock' or not args or args[0]!='create': raise ProjectRecoveryError('Création de copie invalide.')
        config={'Env':environment,'Labels':{},'HostConfig':{'ReadonlyRootfs':True,'CapDrop':['ALL'],'CapAdd':[],'SecurityOpt':['no-new-privileges'],'Mounts':[],'Tmpfs':{},'PortBindings':{},'PublishAllPorts':False}}
        remaining=list(args[1:]);name=''
        while remaining and not IMAGE.fullmatch(remaining[0]):
            flag=remaining.pop(0)
            if flag=='--read-only':continue
            if not remaining:raise ProjectRecoveryError('Option de copie incomplète.')
            value=remaining.pop(0)
            if flag=='--name':name=value
            elif flag=='--label':key,content=value.split('=',1);config['Labels'][key]=content
            elif flag=='--network':config['HostConfig']['NetworkMode']=value
            elif flag=='--user':config['User']=value
            elif flag=='--cap-drop':
                if value!='ALL':raise ProjectRecoveryError('Capacités de copie invalides.')
            elif flag=='--security-opt':
                if value!='no-new-privileges':raise ProjectRecoveryError('Sécurité de copie invalide.')
            elif flag in ('--memory','--memory-swap'):
                if not re.fullmatch(r'[1-9][0-9]{1,3}m',value):raise ProjectRecoveryError('Mémoire de copie invalide.')
                config['HostConfig']['Memory'if flag=='--memory'else'MemorySwap']=int(value[:-1])*1024**2
            elif flag=='--cpus':config['HostConfig'].update(CpuPeriod=100000,CpuQuota=int(float(value)*100000))
            elif flag=='--pids-limit':config['HostConfig']['PidsLimit']=int(value)
            elif flag=='--tmpfs':target,options=value.split(':',1);config['HostConfig']['Tmpfs'][target]=options
            elif flag=='--mount':
                parts=dict(part.split('=',1)if '='in part else(part,'true')for part in value.split(','))
                if parts.get('type')!='volume'or not parts.get('source','').startswith('kolab-drill-')or set(parts)-{'type','source','target','readonly'}:raise ProjectRecoveryError('Montage de copie étranger.')
                config['HostConfig']['Mounts'].append({'Type':'volume','Source':parts['source'],'Target':parts['target'],'ReadOnly':parts.get('readonly')=='true','VolumeOptions':{'NoCopy':True}})
            elif flag=='--workdir':config['WorkingDir']=value
            elif flag=='--entrypoint':config['Entrypoint']=[value]if value else[]
            else:raise ProjectRecoveryError('Option de copie non autorisée.')
        if not remaining or not re.fullmatch(r'kolab-drill-[a-f0-9]{16}-[a-z0-9-]{1,80}',name):raise ProjectRecoveryError('Identité de copie manquante.')
        config['Image']=remaining.pop(0);config['Cmd']=remaining
        run_id=name.split('-')[2];host=config['HostConfig'];network=host.get('NetworkMode','')
        if config.get('User')!='10001:10001' or config['Labels'].get('kolab.project-drill')!=run_id or (network!='none' and not re.fullmatch('container:kolab-drill-'+run_id+r'-[a-z0-9-]{1,80}',network)) or not 64*1024**2<=host.get('Memory',0)<=512*1024**2 or host.get('MemorySwap')!=host['Memory'] or not 1<=host.get('CpuQuota',0)<=50000 or not 1<=host.get('PidsLimit',0)<=128:raise ProjectRecoveryError('Isolation de copie invalide.')
        raw=encoded(config)
        if len(raw)>2*1024**2:raise ProjectRecoveryError('Configuration de copie hors bornes.')
        connection=http.client.HTTPConnection('localhost',timeout=20)
        def connect():
            connection.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);connection.sock.settimeout(20);connection.sock.connect(self.socket)
        connection.connect=connect
        try:
            connection.request('POST','/v1.47/containers/create?name='+name,body=raw,headers={'Content-Type':'application/json'})
            response=connection.getresponse();payload=response.read(65537)
            if response.status!=201 or len(payload)>65536:raise ProjectRecoveryError('Le moteur de copie refuse cette configuration.')
            if not re.fullmatch(r'[a-f0-9]{64}',json.loads(payload).get('Id','')):raise ProjectRecoveryError('Identité de copie non confirmée.')
        finally:connection.close()
    def export(self, image, destination):
        if not IMAGE.fullmatch(image): raise ProjectRecoveryError('Image immuable requise.')
        with open(destination, 'xb') as out:
            os.fchmod(out.fileno(), 0o600)
            process = subprocess.Popen(self.command + ['image', 'save', image], stdout=out, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 600
                while process.poll() is None:
                    recovery.require_space(destination.parent, RESERVE)
                    if destination.stat().st_size > LIMIT or time.monotonic() > deadline: raise ProjectRecoveryError('Export image hors budget.')
                    time.sleep(.2)
                if process.returncode: raise ProjectRecoveryError('Export image impossible.')
            finally:
                if process.poll() is None: process.kill(); process.wait(timeout=5)
    def copy(self, source, target, store):
        process = subprocess.Popen(self.command + ['cp', str(source) + '/.', target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 600
        try:
            while process.poll() is None:
                recovery.require_space(store, RESERVE)
                if time.monotonic() > deadline: raise ProjectRecoveryError('Copie rootless hors délai.')
                time.sleep(.2)
            if process.returncode: raise ProjectRecoveryError('Matérialisation rootless impossible.')
        finally:
            if process.poll() is None: process.kill(); process.wait(timeout=5)
    def load(self, filename):
        with open(filename, 'rb') as source:
            subprocess.run(self.command + ['image', 'load'], stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600, check=True)


class CopyBudget:
    def __init__(self, maximum=LIMIT): self.maximum, self.used = maximum, 0
    def add(self, amount, root):
        if type(amount) is not int or amount < 0 or self.used + amount > self.maximum: raise ProjectRecoveryError('Budget agrégé des artefacts dépassé.')
        recovery.require_space(root, RESERVE + amount)
        self.used += amount


def copy_file(source, destination, budget):
    with open_regular(canonical(source)) as inp, open(destination, 'xb') as out:
        os.fchmod(out.fileno(), 0o600)
        for chunk in iter(lambda: inp.read(1024**2), b''):
            budget.add(len(chunk), destination.parent); out.write(chunk)


def tree_state(root):
    root = canonical(root, True)
    rows = recovery.regular_tree(root)
    if len(rows) > MAX_FILES: raise ProjectRecoveryError('Trop de fichiers pour une reprise bornée.')
    total, result = 0, {}
    for name in sorted(rows):
        path = canonical(root / name); info = path.stat(); total += info.st_size
        if total > LIMIT: raise ProjectRecoveryError('Fichiers hors budget de reprise.')
        result[name] = [info.st_size, info.st_mtime_ns, info.st_ino, digest(path), info.st_mode & 0o777]
    return result


def copy_stable_tree(source, destination, budget=None):
    source = canonical(source, True); before = tree_state(source); budget = budget or CopyBudget()
    destination.mkdir(mode=0o700)
    # Keep empty directories as declarative metadata; the common encrypted
    # capture format stores regular files only.
    directories = []
    for folder, children, _files in os.walk(source, followlinks=False):
        for name in children:
            child = canonical(pathlib.Path(folder) / name, True)
            relative = str(child.relative_to(source)); directories.append(relative)
            (destination / relative).mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, row in before.items():
        origin = canonical(source / name); target = destination / name; target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        copy_file(origin, target, budget)
        if target.stat().st_size != row[0] or digest(target) != row[3]: raise ProjectRecoveryError('Fichier modifié pendant la copie.')
    if tree_state(source) != before: raise ProjectRecoveryError('Volume modifié pendant la capture ; aucune cohérence annoncée.')
    return {'files': len(before), 'bytes': sum(row[0] for row in before.values()), 'treeSha256': hashlib.sha256(encoded({name: [row[0], row[3]] for name, row in before.items()})).hexdigest(), 'directories': directories, 'modes': {name:row[4]for name,row in before.items()}}


def app_inventory(docker, app, volume_root=SOURCE_VOLUME_ROOT):
    ids = docker(['ps', '-q', '--no-trunc', '--filter', 'label=pockethost.web.app=' + app['id']]).split()
    if not 1 <= len(ids) <= 8: raise ProjectRecoveryError('Services publiés absents ou inventaire ambigu.')
    rows = json.loads(docker(['inspect', *ids])); selected = []
    for row in rows:
        config, host = row.get('Config') or {}, row.get('HostConfig') or {}
        labels = config.get('Labels') or {}
        if labels.get('pockethost.web.private-db') == 'true': continue
        if labels.get('pockethost.web.app') != app['id'] or labels.get('pockethost.web.deployment') != app['deploymentId']:
            raise ProjectRecoveryError('Un déploiement est en transition. Réessayez après sa publication.')
        if not IMAGE.fullmatch(row.get('Image', '')) or host.get('Privileged') or host.get('Binds') or not row.get('State', {}).get('Running'):
            raise ProjectRecoveryError('Service non pris en charge pour une copie isolée.')
        service = labels.get('pockethost.web.service') or 'web'
        if not NAME.fullmatch(service): raise ProjectRecoveryError('Nom de service invalide.')
        volumes = []
        observed_binds = [mount for mount in row.get('Mounts', []) if mount.get('Type')=='bind']
        if observed_binds:
            wait_root=pathlib.Path('/home/pockethost-web/data/private-db-runtime')/app['id']/(app['deploymentId']+'-'+service)
            expected={str(wait_root/'ready'):'/run/kolab-ready',str(wait_root/'wait-env'):'/run/kolab-wait-env','/opt/pockethost-web/web_private_db_wait.cjs':'/run/kolab-wait.cjs'}
            if labels.get('pockethost.web.private-db.wait')!='true' or len(observed_binds)!=3 or len({mount.get('Destination')for mount in observed_binds})!=3 or any(mount.get('RW')is not False or expected.get(mount.get('Source'))!=mount.get('Destination') for mount in observed_binds): raise ProjectRecoveryError('Montage source non couvert par la reprise.')
        for mount in row.get('Mounts', []):
            if mount.get('Type') == 'bind':
                # The private DB launch wrapper's trusted read-only mounts are not
                # business data. The normal image entrypoint is restored instead.
                if mount.get('RW') is False and mount.get('Destination') in ('/run/kolab-ready', '/run/kolab-wait-env', '/run/kolab-wait.cjs'): continue
                raise ProjectRecoveryError('Montage externe non couvert par la reprise.')
            if mount.get('Type') == 'volume' and mount.get('Destination') == '/__pockethost_tools' and mount.get('RW') is False and re.fullmatch(r'ph-web-tools-[a-f0-9]{20}', mount.get('Name', '')): continue
            if mount.get('Type') != 'volume' or not mount.get('Name', '').startswith(('ph-web-data-' + app['id'] + '-', 'ph-web-next-' + app['id'] + '-')):
                raise ProjectRecoveryError('Volume non déclaré ou étranger.')
            actual = json.loads(docker(['volume', 'inspect', mount['Name']]))[0]
            if actual.get('Driver') != 'local' or actual.get('Options') or actual.get('Labels', {}).get('pockethost.web.app') != app['id']:
                raise ProjectRecoveryError('Volume indirect ou étranger.')
            # The UID1001 daemon can report arbitrary paths. Never treat its
            # Mountpoint or DockerRootDir as authority for root filesystem reads.
            expected = pathlib.Path(volume_root) / mount['Name'] / '_data'
            if actual.get('Mountpoint') != str(expected): raise ProjectRecoveryError('Point de volume hors du moteur source approuvé.')
            expected = canonical(expected, True)
            if pathlib.Path(volume_root) == SOURCE_VOLUME_ROOT and expected.parent.stat().st_uid != 1001: raise ProjectRecoveryError('Propriétaire du volume source non confirmé.')
            volumes.append({'source': str(expected), 'target': mount['Destination'], 'readOnly': mount.get('RW') is False, 'name': mount['Name']})
        image = json.loads(docker(['image', 'inspect', row['Image']]))[0]
        if image.get('Id') != row['Image']: raise ProjectRecoveryError('Image publiée incohérente.')
        # Runtime private DB wait entrypoint is a platform wrapper. Its original
        # image command is the only executable retained in the capsule.
        original = image.get('Config') or {}
        entrypoint, command = config.get('Entrypoint') or [], config.get('Cmd') or []
        if labels.get('pockethost.web.private-db.wait') == 'true':
            if '/run/kolab-wait.cjs' not in entrypoint or not command: raise ProjectRecoveryError('Lanceur privé non reconnu.')
            entrypoint, command = [command[0]], command[1:]
        if not isinstance(entrypoint, list) or not isinstance(command, list) or len(entrypoint + command) > 100 or any(not isinstance(x, str) or '\0' in x for x in entrypoint + command): raise ProjectRecoveryError('Commande capturée invalide.')
        ports = [int(k.split('/')[0]) for k, v in (host.get('PortBindings') or {}).items() if v and re.fullmatch(r'[1-9][0-9]{0,4}/tcp', k)]
        selected.append({'name': service, 'image': row['Image'], 'entrypoint': entrypoint, 'command': command, 'port': ports[0] if len(ports) == 1 else None, 'healthPath': labels.get('pockethost.web.healthpath') or '/',
                         'environment': config.get('Env') or [], 'workdir': config.get('WorkingDir') or '', 'volumes': volumes,
                         'front': labels.get('pockethost.web.front') == 'true' or labels.get('pockethost.web.front') is None and app.get('configuration', {}).get('runtime', {}).get('kind') != 'worker' and service == 'web', 'sourceId': row['Id'], 'sourceStartedAt': row.get('State', {}).get('StartedAt')})
    if not selected or len(selected) > 4 or len({row['name'] for row in selected}) != len(selected): raise ProjectRecoveryError('Inventaire de services invalide.')
    return selected


def require_host_headroom(sandbox=False):
    if sys.platform != 'linux': return  # Pure fake-filesystem tests on macOS.
    from database_memory_reservations import reserved_headroom
    values = {line.split(':')[0]: int(line.split()[1]) * 1024 for line in pathlib.Path('/proc/meminfo').read_text().splitlines() if ':' in line}
    needed = 1024**3
    if sandbox:
        try: current = int(pathlib.Path('/sys/fs/cgroup/user.slice/user-1002.slice/memory.current').read_text())
        except (OSError,ValueError): raise ProjectRecoveryError('Budget global de reprise illisible.')
        needed += max(0, 4*1024**3 - current)
    if values['MemAvailable'] - reserved_headroom() - max(2*1024**3, values['MemTotal']*15//100) < needed:
        raise ProjectRecoveryError('Capacité RAM insuffisante pour préserver les services publiés et la pointe de recette.')


def observed_native(ident, data_root, binary_root):
    # Read-only native engine inventory; never execute inside or update a source.
    if not pathlib.Path('/var/run/docker.sock').exists(): return None
    from platform_memory_allocator import Engine
    engine = Engine('/var/run/docker.sock', 'databases')
    rows = [row for row in engine.request('GET', '/containers/json?all=0') if '/' + ident in row.get('Names', [])]
    if not rows: return None
    if len(rows) != 1: raise ProjectRecoveryError('Identité native ambiguë.')
    info = engine.request('GET', '/containers/' + rows[0]['Id'] + '/json')
    if info.get('Name') != '/' + ident or not info.get('State', {}).get('Running'): raise ProjectRecoveryError('Identité native non confirmée.')
    mounts = info.get('Mounts', [])
    if not any(m.get('Source') == str(data_root / 'instances' / ident) and m.get('Destination') == '/home/pockethost' for m in mounts): raise ProjectRecoveryError('Répertoire natif différent.')
    binaries = [m for m in mounts if m.get('Destination') == '/home/pockethost/pocketbase' and m.get('RW') is False]
    if len(binaries) != 1: raise ProjectRecoveryError('Binaire natif non confirmé.')
    binary = canonical(binaries[0]['Source'])
    if not binary.is_relative_to(binary_root): raise ProjectRecoveryError('Binaire natif hors du cache approuvé.')
    relative = binary.relative_to(binary_root)
    if len(relative.parts) != 3 or relative.parts[1:] != ('linux_amd64', 'pocketbase') or not re.fullmatch(r'0\.[0-9]{1,3}\.[0-9]{1,4}', relative.parts[0]): raise ProjectRecoveryError('Version native exécutée invalide.')
    return {'version':relative.parts[0],'memoryMaximumMiB':int(info.get('HostConfig',{}).get('Memory',0))//1024**2}


def native_sqlite_snapshot(source, destination):
    # SQLite may create WAL/SHM bookkeeping even for mode=ro. Do that as the
    # original DB owner, in a separate process, never as the root coordinator.
    source = canonical(source); owner = source.stat()
    if owner.st_size > LIMIT: raise ProjectRecoveryError('Snapshot SQLite trop volumineux.')
    # /tmp is tmpfs on the fleet: large databases must use disk-backed scratch.
    # PrivateTmp gives this service its own /var/tmp view without source writes.
    recovery.require_space('/var/tmp', RESERVE + owner.st_size)
    with tempfile.TemporaryDirectory(prefix='kolab-native-sqlite-', dir='/var/tmp') as directory:
        directory = pathlib.Path(directory).resolve(); os.chmod(directory, 0o700)
        if os.geteuid() == 0:
            os.chown(directory, owner.st_uid, owner.st_gid)
            prefix = ['/usr/bin/setpriv', '--reuid=' + str(owner.st_uid), '--regid=' + str(owner.st_gid), '--clear-groups', '--']
        elif os.geteuid() == owner.st_uid: prefix = []
        else: raise ProjectRecoveryError('Le snapshot SQLite doit être lu avec son propriétaire natif.')
        staged = directory / 'data.db'
        command = prefix + [sys.executable, '-I', str(pathlib.Path(__file__).resolve()), 'sqlite-snapshot', str(source), str(staged)]
        subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={'PATH':'/usr/bin:/bin'}, timeout=1800, check=True)
        copy_file(canonical(staged), destination, CopyBudget())


def sqlite_snapshot_child(source, target):
    source = canonical(source); target = pathlib.Path(target).absolute()
    if target.resolve() != target or target.exists() or source.stat().st_uid != os.geteuid(): raise ProjectRecoveryError('Snapshot natif non autorisé.')
    with contextlib.closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)) as reader, contextlib.closing(sqlite3.connect(target)) as writer:
        target.chmod(0o600); reader.execute('PRAGMA query_only=ON')
        page_size = reader.execute('PRAGMA page_size').fetchone()[0]
        def progress(_status, _remaining, total):
            if total * page_size > LIMIT: raise ProjectRecoveryError('Snapshot SQLite trop volumineux.')
            recovery.require_space(target.parent, RESERVE + 8 * 1024**2)
        reader.backup(writer, pages=256, progress=progress, sleep=.02)
        if writer.execute('PRAGMA integrity_check').fetchall() != [('ok',)]: raise ProjectRecoveryError('Snapshot SQLite invalide.')


def capture(snapshot, data_root, binary_root, destination, keyfile, docker, instance_app_root=None):
    apps, database_ids = validate_snapshot(snapshot)
    require_host_headroom()
    if any(app.get('externalDependencies') for app in snapshot['apps']): raise ProjectRecoveryError('Un stockage ou service externe déclaré n’est pas couvert par cette capsule.')
    data_root, binary_root = canonical(data_root, True), canonical(binary_root, True)
    destination = pathlib.Path(destination).absolute(); keyfile = recovery.private_file(keyfile)
    key = recovery.load_key(keyfile)
    if destination.resolve() != destination or destination.exists(): raise ProjectRecoveryError('Archive de destination déjà présente ou symbolique.')
    recovery.require_space(destination.parent, RESERVE + LIMIT)
    with tempfile.TemporaryDirectory(prefix='kolab-project-capture-', dir=destination.parent) as temporary:
        work = pathlib.Path(temporary); os.chmod(work, 0o700)
        # A synthetic inventory contains no controller credentials or unrelated
        # accounts/projects. Existing recovery's scoped integrity rules still apply.
        controller = work / 'inventory.db'
        with sqlite3.connect(controller) as connection:
            connection.execute('CREATE TABLE instances(id TEXT PRIMARY KEY)')
            connection.execute('CREATE TABLE _params(key TEXT PRIMARY KEY,value TEXT)')
            connection.execute('INSERT INTO _params VALUES(?,?)', ('settings', json.dumps({'smtp': {'enabled': False}, 's3': {'enabled': False}, 'backups': {'cron': '', 's3': {'enabled': False}}})))
            connection.executemany('INSERT INTO instances VALUES(?)', [(ident,) for ident in database_ids])
        instances, native_before, versions, native_hosts, support_states = [], {}, {}, [], []
        copy_budget = CopyBudget()
        def support_tree(source, target):
            copied = work / target; copied.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            state = copy_stable_tree(source, copied, copy_budget); support_states.append((str(source), tree_state(source)))
            for relative in tree_state(copied): native_hosts.append({'source': str(copied / relative), 'target': target + '/' + relative})
            return {'artifact': target, 'state': state}
        by_id = {row['id']: row for row in snapshot.get('databases', [])}
        for ident in database_ids:
            description = by_id.get(ident) or {}; version = description.get('version', '')
            observed = observed_native(ident, data_root, binary_root)
            if re.fullmatch(r'0\.[0-9]{1,3}\.\*', version):
                actual = observed['version'] if observed else None
                candidates = [path.name for path in binary_root.iterdir() if re.fullmatch(r'0\.[0-9]{1,3}\.[0-9]{1,4}', path.name) and path.name.startswith(version[:-1])]
                if actual and actual.startswith(version[:-1]): version = actual
                elif len(candidates) == 1: version = candidates[0]
                else: raise ProjectRecoveryError('Version native ambiguë : choisir le binaire exact avant la reprise.')
            if not re.fullmatch(r'0\.[0-9]{1,3}\.[0-9]{1,4}', version): raise ProjectRecoveryError('Version native exacte requise.')
            home = canonical(data_root / 'instances' / ident / 'pb_data', True)
            native_before[ident] = tree_state(home / 'storage') if (home / 'storage').exists() else {}
            binary = canonical(binary_root / version / 'linux_amd64' / 'pocketbase')
            copy = work / ('pocketbase-' + ident); copy_file(binary, copy, copy_budget); copy.chmod(0o700); versions[ident] = {'version': version, 'sha256': digest(copy), 'configuration': description.get('configuration', {}), 'externalApplicationBindingsCount': int(description.get('externalApplicationBindingsCount',0)), 'support': [], 'memoryReservationMiB': __import__('database_memory_reservations').load_policy().get(ident, 0)//1024**2, 'memoryMaximumMiB':observed.get('memoryMaximumMiB',0) if observed else 0, 'excluded': ['historical-backups', 'logs.db', 'SQLite-WAL-and-SHM-sidecars']}
            native_home = home.parent
            for name in ('pb_hooks', 'pb_public', 'pb_migrations'):
                source = native_home / name
                if source.exists(): versions[ident]['support'].append(support_tree(canonical(source, True), 'native-support/' + ident + '/' + name))
            for source in sorted(native_home.iterdir()):
                if source.is_symlink(): raise ProjectRecoveryError('Lien natif non couvert refusé.')
                if source.name in ('pb_data', 'pb_hooks', 'pb_public', 'pb_migrations', 'logs', 'backups', 'pocketbase'): continue
                if source.is_dir(): versions[ident]['support'].append(support_tree(canonical(source, True), 'native-support/' + ident + '/' + source.name))
                elif source.is_file():
                    source = canonical(source); staged = work / ('native-support/' + ident + '/' + source.name); staged.parent.mkdir(mode=0o700, parents=True, exist_ok=True); copy_file(source, staged, copy_budget); staged.chmod(0o600)
                    if digest(staged) != digest(source): raise ProjectRecoveryError('Configuration native instable.')
                    native_hosts.append({'source': str(staged), 'target': 'native-support/' + ident + '/' + source.name})
                else: raise ProjectRecoveryError('Source native non régulière refusée.')
            if instance_app_root is not None:
                realm = 'v23' if tuple(map(int, version.split('.')[:2])) >= (0, 23) else 'v22'
                support = canonical(pathlib.Path(instance_app_root) / realm, True)
                versions[ident]['platformSupport'] = support_tree(support, 'native-platform/' + ident)
            native_snapshot = work / ('snapshot-' + ident); native_snapshot.mkdir(mode=0o700)
            native_sqlite_snapshot(home / 'data.db', native_snapshot / 'data.db')
            copy_budget.add((native_snapshot / 'data.db').stat().st_size, work)
            sidecar = home / recovery.TOTP_SIDECAR
            if sidecar.exists():
                before = recovery.totp_sidecar_state(home / 'data.db'); copy_file(sidecar, native_snapshot / recovery.TOTP_SIDECAR, copy_budget)
                if recovery.totp_sidecar_state(home / 'data.db') != before: raise ProjectRecoveryError('Clé TOTP modifiée pendant sa capture.')
            definition = {'id': ident, 'database': str(native_snapshot / 'data.db')}
            with contextlib.closing(recovery.connect_copy(native_snapshot / 'data.db')) as copied_db:
                native_storage = recovery.native_storage_config(copied_db)
                if native_storage is not None:
                    references = recovery.file_references(copied_db)
                    client = recovery.native_s3_client(native_storage)
                    try:
                        for object_key in references: copy_budget.add(recovery.native_head(client, native_storage, object_key)['size'], work)
                    finally: recovery.close_native(client)
            if (home / 'storage').exists():
                staged_storage = native_snapshot / 'storage'
                copy_stable_tree(home / 'storage', staged_storage, copy_budget)
                definition['storage'] = str(staged_storage)
            instances.append(definition)
        inventories, hosts, volume_states, image_ids = [], native_hosts, [], set()
        for app in snapshot['apps']:
            services = app_inventory(docker, app)
            for service in services:
                image_ids.add(service['image'])
                for number, volume in enumerate(service['volumes']):
                    target = 'volumes/' + app['id'] + '/' + service['name'] + '-' + str(number)
                    folder = work / target; folder.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    state = copy_stable_tree(volume['source'], folder, copy_budget); volume_states.append((volume['source'], tree_state(volume['source'])))
                    hosts.append({'source': str(folder), 'target': target}); volume['artifact'] = target; volume['state'] = state; del volume['source']
                service.pop('sourceId', None)
            inventories.append({**app, 'services': services})
        for image in sorted(image_ids):
            target = 'images/' + image[7:] + '.tar'; filename = work / target; filename.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            docker.export(image, filename); copy_budget.add(filename.stat().st_size, filename.parent); hosts.append({'source': str(filename), 'target': target})
        for ident in database_ids: hosts.append({'source': str(work / ('pocketbase-' + ident)), 'target': 'bin/pocketbase-' + ident})
        capsule = {'version': 1, 'projectId': snapshot['projectId'], 'projectName': snapshot.get('projectName', ''), 'revision': snapshot.get('revision', ''), 'apps': inventories, 'databases': versions,
                   'capturedAt': recovery.stamp(), 'migrationSourceBarrier': snapshot.get('migrationSourceBarrier'), 'consistency': {'nativeSQLite': 'read-transaction', 'files': 'hashes-before-and-after-capture', 'distributedTransaction': False},
                   'externalDependencies': sorted({name for app in inventories for name in app.get('externalDependencies', [])}), 'scheduledTasksDisabled': True, 'businessWorkersStarted': False}
        capsule_file = work / 'project.json'; private_json(capsule_file, capsule); hosts.append({'source': str(capsule_file), 'target': 'project.json'})
        plan = {'version': 1, 'platform': snapshot.get('platform', 'kolab-project'), 'backupId': secrets.token_hex(16), 'controller': {'database': str(controller)}, 'instances': instances,
                'hostPaths': hosts, 'metadata': {'kind': 'project-capsule', 'projectId': snapshot['projectId'], 'projectRevision': snapshot.get('revision', ''), 'projectFormat': 1}}
        try:
            with open(destination, 'xb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                class BoundedSink:
                    total = 0
                    def write(self, raw):
                        self.total += len(raw)
                        if self.total > LIMIT: raise ProjectRecoveryError('Archive chiffrée hors budget.')
                        recovery.require_space(destination.parent, RESERVE + len(raw)); return stream.write(raw)
                    def flush(self): stream.flush()
                manifest = recovery.capture_to_sink(plan, key, keyfile, BoundedSink()); stream.flush(); os.fsync(stream.fileno())
            for ident, before in native_before.items():
                storage = data_root / 'instances' / ident / 'pb_data/storage'
                if (tree_state(storage) if storage.exists() else {}) != before: raise ProjectRecoveryError('Fichiers natifs modifiés pendant la capture ; recommencez pour une copie cohérente.')
            for source, before in volume_states + support_states:
                if tree_state(source) != before: raise ProjectRecoveryError('Un volume a changé avant la fin de la capture.')
            # Confirm published identities, not just metadata. No Docker mutation.
            for app in snapshot['apps']:
                original = next(row for row in inventories if row['id'] == app['id'])
                fresh = app_inventory(docker, app)
                if [(row['name'], row['image'], row['sourceStartedAt']) for row in original['services']] != [(row['name'], row['image'], row['sourceStartedAt']) for row in fresh]:
                    raise ProjectRecoveryError('La publication a changé pendant la capture.')
            checked = recovery.inspect_archive(lambda: open(destination, 'rb'), key)
            if checked['metadata'] != plan['metadata'] or checked['coverage'].get('complete') is not True: raise ProjectRecoveryError('Couverture de reprise incomplète.')
            return {'completeArtifacts': True, 'applicationVerified': False, 'archiveBytes': destination.stat().st_size, 'sha256': digest(destination), 'apps': len(apps), 'databases': len(database_ids), 'images': len(image_ids), 'distributedTransaction': False, 'externalDependencies': capsule['externalDependencies'], 'backupId': manifest['backupId']}
        except Exception:
            destination.unlink(missing_ok=True); raise


def verify_materialized(root, manifest):
    root = canonical(root, True)
    capsule = json.loads(recovery.private_file(root / 'host-files/project.json').read_text())
    validate_snapshot(capsule)
    if manifest.get('metadata', {}).get('projectId') != capsule['projectId']: raise ProjectRecoveryError('Projet de capsule incohérent.')
    files = {row['target']: row for row in manifest['files']}
    for row in manifest['files']:
        if row['target'].startswith('host-files/'):
            filename = canonical(root / recovery.safe_relative(row['target']))
            if filename.stat().st_size != row['size'] or digest(filename) != row['sha256']: raise ProjectRecoveryError('Artefact restauré incomplet.')
    for ident, description in capsule['databases'].items():
        binary = canonical(root / ('host-files/bin/pocketbase-' + ident))
        if digest(binary) != description['sha256']: raise ProjectRecoveryError('Binaire natif restauré différent.')
        binary.chmod(0o755)
        for support in [*description.get('support', []), *([description['platformSupport']] if description.get('platformSupport') else [])]:
            directory = root / 'host-files' / recovery.safe_relative(support['artifact']); directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            for name in support['state'].get('directories', []): (directory / recovery.safe_relative(name)).mkdir(mode=0o700, parents=True, exist_ok=True)
            for name, mode in support['state'].get('modes', {}).items(): canonical(directory / recovery.safe_relative(name)).chmod(mode & 0o777)
            observed = tree_state(directory)
            if hashlib.sha256(encoded({name: [row[0], row[3]] for name, row in observed.items()})).hexdigest() != support['state']['treeSha256']: raise ProjectRecoveryError('Support natif incomplet.')
        connection = recovery.connect_copy(root / f'instances/{ident}/pb_data/data.db')
        try:
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]: raise ProjectRecoveryError('Base restaurée invalide.')
            for _column, _name, raw in recovery.settings_records(connection):
                settings = json.loads(raw)
                if settings.get('smtp', {}).get('enabled') is not False or settings.get('backups', {}).get('cron') != '': raise ProjectRecoveryError('Mails/crons natifs non désactivés.')
        finally: connection.close()
    for app in capsule['apps']:
        for service in app['services']:
            if 'host-files/images/' + service['image'][7:] + '.tar' not in files: raise ProjectRecoveryError('Image manquante.')
            for volume in service['volumes']:
                directory = root / 'host-files' / recovery.safe_relative(volume['artifact'])
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                for name in volume['state'].get('directories', []):
                    (directory / recovery.safe_relative(name)).mkdir(mode=0o700, parents=True, exist_ok=True)
                directory = canonical(directory, True)
                for name, mode in volume['state'].get('modes', {}).items(): canonical(directory / recovery.safe_relative(name)).chmod(mode & 0o777)
                observed = tree_state(directory)
                # Inode/mtime change on restoration; content inventory remains exact.
                if len(observed) != volume['state']['files'] or sum(row[0] for row in observed.values()) != volume['state']['bytes'] or hashlib.sha256(encoded({name: [row[0], row[3]] for name, row in observed.items()})).hexdigest() != volume['state']['treeSha256']: raise ProjectRecoveryError('Volume restauré incomplet.')
    return capsule


def restore(archive, keyfile, destination, expected_sha256=None, max_bytes=LIMIT):
    archive = recovery.private_file(archive); key = recovery.load_key(keyfile)
    if expected_sha256 and digest(archive) != expected_sha256: raise ProjectRecoveryError('Empreinte de transfert incorrecte.')
    factory = lambda: open(archive, 'rb'); manifest = recovery.inspect_archive(factory, key)
    if manifest.get('metadata', {}).get('kind') != 'project-capsule' or manifest['coverage'].get('complete') is not True:
        raise ProjectRecoveryError('Une capsule complète de projet est requise.')
    budget = recovery.restore_budget(manifest, host_files=True)
    if budget['restoreBytes'] > max_bytes: raise ProjectRecoveryError('Capacité insuffisante pour cette copie.')
    marker = recovery.restore_archive(factory, key, destination, host_files=True, max_restore_bytes=max_bytes, min_free_bytes=RESERVE)
    capsule = verify_materialized(destination, manifest)
    return {'completeArtifacts': True, 'applicationVerified': False, 'paused': True, 'noExternalNetwork': True, 'noServicesStarted': True, 'projectId': capsule['projectId'], 'restoreBytes': budget['restoreBytes'], 'backupId': marker['backupId']}


def safe_environment(entries):
    # No real delivery/storage/AI credentials enter the executable app copies.
    # PB access credentials are retained only because they address sandbox copies.
    result = []
    forbidden = re.compile(r'(SMTP|BREVO|SENDGRID|MAILGUN|RESEND|AWS|R2_|S3_|STRIPE|OPENAI|DEEPGRAM|WEBHOOK|CRON_SECRET)', re.I)
    for entry in entries:
        if not isinstance(entry, str) or '=' not in entry: raise ProjectRecoveryError('Environnement capturé invalide.')
        name, value = entry.split('=', 1)
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name): raise ProjectRecoveryError('Variable invalide.')
        if forbidden.search(name) or name in ('NODE_OPTIONS', 'NODE_PATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'PYTHONPATH', 'PYTHONHOME'):
            result.append(name + '='); continue
        result.append(name + '=' + value)
    return result + ['KOLAB_RECOVERY_DRILL=1', 'DISABLE_CRON=true', 'CRON_ENABLED=false', 'SMTP_ENABLED=false', 'MAIL_ENABLED=false']


def sandbox_base(name, image, anchor=None, memory_mib=512):
    if type(memory_mib) is not int or not 64 <= memory_mib <= 512: raise ProjectRecoveryError('Budget de copie invalide.')
    if not re.fullmatch(r'kolab-drill-[a-f0-9]{16}-[a-z0-9-]{1,80}', name) or not IMAGE.fullmatch(image): raise ProjectRecoveryError('Identité de copie invalide.')
    return ['create', '--name', name, '--label', 'kolab.project-drill=' + name.split('-')[2], '--network', 'container:' + anchor if anchor else 'none',
            '--read-only', '--user', '10001:10001', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--memory', str(memory_mib) + 'm', '--memory-swap', str(memory_mib) + 'm', '--cpus', '0.5', '--pids-limit', '128',
            '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=128m,uid=10001,gid=10001']


def cleanup_sandbox(state, docker):
    run_id = state.get('runId', '')
    if not re.fullmatch(r'[a-f0-9]{16}', run_id): raise ProjectRecoveryError('Journal de copie invalide.')
    failed = []
    # Inventory access must work before a missing object is treated as removed.
    present_containers = set(docker(['ps', '-a', '--format', '{{.Names}}']).split())
    present_volumes = set(docker(['volume', 'ls', '--format', '{{.Name}}']).split())
    for category, rows in [('containers', state.get('containers', [])), ('volumes', state.get('volumes', []))]:
        if not isinstance(rows, list) or len(rows) > 200: raise ProjectRecoveryError('Journal de nettoyage hors bornes.')
        for name in reversed(rows):
            if not isinstance(name, str) or not re.fullmatch('kolab-drill-' + run_id + r'-[a-z0-9-]{1,80}', name): raise ProjectRecoveryError('Objet de nettoyage étranger.')
            try:
                if category == 'containers':
                    if name not in present_containers: continue
                    info = json.loads(docker(['inspect', name]))[0]
                    if info.get('Config', {}).get('Labels', {}).get('kolab.project-drill') != run_id: raise ProjectRecoveryError('Identité de nettoyage différente.')
                    docker(['rm', '-f', name])
                else:
                    if name not in present_volumes: continue
                    info = json.loads(docker(['volume', 'inspect', name]))[0]
                    if info.get('Labels', {}).get('kolab.project-drill') != run_id: raise ProjectRecoveryError('Volume de nettoyage différent.')
                    docker(['volume', 'rm', name])
            except Exception: failed.append(name)
    images = state.get('images', [])
    if not isinstance(images, list) or len(images)>80 or any(not IMAGE.fullmatch(image)for image in images): raise ProjectRecoveryError('Journal des images invalide.')
    for image in images:
        try:
            if not docker.image_exists(image): continue
            docker(['image', 'rm', image])
            if docker.image_exists(image): raise ProjectRecoveryError('Image de copie non supprimée.')
        except Exception: failed.append(image)
    if failed: raise ProjectRecoveryError('Le nettoyage de copies isolées reste incomplet. Aucun service d’origine touché.')


def _sandbox_drill(root, docker, helper_image, max_seconds=120, memory_mib=512):
    """Start selected restored front services and native copies, never business workers.

    One network=none namespace per application. Native bindings retain their
    loopback ports. No host ports, DNS, proxy, Docker socket or production mount.
    A read-only HTTP health does not prove arbitrary user workflows.
    """
    if getattr(docker, 'socket', None) != '/run/user/1002/docker.sock': raise ProjectRecoveryError('Les copies exigent le moteur de reprise isolé UID1002.')
    socket_path = pathlib.Path(docker.socket); socket_info = socket_path.stat(); runtime_info = socket_path.parent.stat()
    if not stat.S_ISSOCK(socket_info.st_mode) or socket_info.st_uid != 1002 or runtime_info.st_uid != 1002 or runtime_info.st_mode & 0o077: raise ProjectRecoveryError('Socket de reprise privé non confirmé.')
    root = canonical(root, True)
    marker = json.loads(recovery.private_file(root / 'RECOVERY_PAUSED.json').read_text())
    manifest = json.loads(recovery.private_file(root / 'recovery-manifest.json').read_text())
    if marker.get('paused') is not True or marker.get('noServicesStarted') is not True: raise ProjectRecoveryError('Copie suspendue requise.')
    capsule = verify_materialized(root, manifest)
    if not IMAGE.fullmatch(helper_image) or type(max_seconds) is not int or not 10 <= max_seconds <= 600: raise ProjectRecoveryError('Outillage ou durée invalide.')
    helper = json.loads(docker(['image', 'inspect', helper_image]))[0]
    store = canonical(docker.info()['DockerRootDir'], True)
    if helper.get('Id') != helper_image: raise ProjectRecoveryError('Image auxiliaire différente.')
    # Trusted helper must include Node+tar. The helper path is installer-owned,
    # not derived from a customer image or browser-supplied command.
    run_id, created, volumes, outcomes, imported_images = secrets.token_hex(8), [], [], [], []
    common_prefix = 'kolab-drill-' + run_id + '-'
    started = time.monotonic()
    journal = root / 'sandbox-state.json'
    def checkpoint(): private_json(journal, {'runId': run_id, 'containers': created, 'volumes': volumes, 'images': imported_images})
    def admit():
        require_host_headroom(sandbox=True)
        if len(created) >= 8: raise ProjectRecoveryError('Trop de copies simultanées pour la recette.')
    def materialize(source, name):
        source = canonical(source, True)
        required = sum(item[0] for item in tree_state(source).values())
        recovery.require_space(store, RESERVE + required)
        volume = common_prefix + name
        volumes.append(volume); checkpoint(); docker(['volume', 'create', '--label', 'kolab.project-drill=' + run_id, volume])
        # `docker cp` into a stopped rootless helper accepts only our restored
        # private directory; no source mount is exposed inside any container.
        holder = common_prefix + name + '-copy'
        args = sandbox_base(holder, helper_image, memory_mib=64); args[args.index('10001:10001')] = '0:0'
        args += ['--cap-add', 'CHOWN', '--mount', 'type=volume,source=' + volume + ',target=/copy', '--entrypoint', '/bin/sh', helper_image, '-c', 'chown -R 10001:10001 /copy']
        admit(); created.append(holder); checkpoint(); docker(args)
        docker.copy(source, holder + ':/copy', store)
        docker(['start', '-a', holder], timeout=60)
        docker(['rm', holder]); created.remove(holder); checkpoint()
        return volume
    try:
        images = sorted({service['image'] for app in capsule['apps'] for service in app['services']})
        for image in images:
            exists = docker.image_exists(image)
            if not exists:
                imported_images.append(image); checkpoint(); docker.load(root / 'host-files/images' / (image[7:] + '.tar'))
            if json.loads(docker(['image', 'inspect', image]))[0].get('Id') != image: raise ProjectRecoveryError('Image importée différente.')
        for app in capsule['apps']:
            anchor = common_prefix + app['id'] + '-anchor'
            anchor_args = sandbox_base(anchor, helper_image, memory_mib=64)
            anchor_args += ['--env', 'NODE_OPTIONS=', '--env', 'NODE_PATH=', '--env', 'LD_PRELOAD=', '--env', 'LD_LIBRARY_PATH=', '--entrypoint', '/usr/local/bin/node', helper_image, '-e', 'setInterval(()=>{},1000)']
            admit(); created.append(anchor); checkpoint(); docker(anchor_args); docker(['start', anchor])
            anchor_info = json.loads(docker(['inspect', anchor]))[0]
            if anchor_info.get('HostConfig', {}).get('NetworkMode') != 'none' or anchor_info.get('NetworkSettings', {}).get('Ports'): raise ProjectRecoveryError('Isolement réseau non confirmé.')
            chosen = app.get('bindings') or []
            for index, binding in enumerate(chosen):
                ident = binding['instanceId']; binary = canonical(root / ('host-files/bin/pocketbase-' + ident))
                data = materialize(root / f'instances/{ident}/pb_data', app['id'] + '-db' + str(index))
                name = common_prefix + app['id'] + '-db' + str(index)
                with tempfile.TemporaryDirectory(prefix='kolab-native-binary-', dir=root) as binary_folder:
                    copied_binary = pathlib.Path(binary_folder) / 'pocketbase'; shutil.copyfile(binary, copied_binary); copied_binary.chmod(0o755)
                    binary_volume = materialize(pathlib.Path(binary_folder), app['id'] + '-bin' + str(index))
                args = sandbox_base(name, helper_image, anchor, memory_mib=memory_mib)
                args += ['--mount', 'type=volume,source=' + data + ',target=/data', '--mount', 'type=volume,source=' + binary_volume + ',target=/kolab,readonly',
                         '--entrypoint', '/kolab/pocketbase', helper_image, 'serve', '--http=127.0.0.1:' + str(binding['port']), '--dir=/data', '--hooksDir=/tmp/empty-hooks', '--migrationsDir=/tmp/empty-migrations']
                admit(); created.append(name); checkpoint(); docker(args); docker(['start', name])
                if anchor is None:
                    info = json.loads(docker(['inspect', name]))[0]
                    if info.get('HostConfig', {}).get('NetworkMode') != 'none' or info.get('NetworkSettings', {}).get('Ports'): raise ProjectRecoveryError('Isolement réseau non confirmé.')
                anchor = anchor or name
            for service in app['services']:
                if not service['front']:
                    outcomes.append({'appId': app['id'], 'service': service['name'], 'status': 'inert-worker-preserved'}); continue
                name = common_prefix + app['id'] + '-' + service['name']; args = sandbox_base(name, service['image'], anchor, memory_mib=memory_mib)
                copied_environment=[]
                for entry in safe_environment(service['environment']):
                    name_part, value_part = entry.split('=', 1)
                    parsed = urllib.parse.urlsplit(value_part) if value_part.startswith(('http://','https://')) else None
                    if parsed and parsed.hostname:
                        matched = next((binding for binding in app.get('bindings', []) if parsed.hostname in binding.get('publicHosts', [])), None)
                        if matched: entry = name_part + '=' + urllib.parse.urlunsplit(('http', '127.0.0.1:' + str(matched['port']), parsed.path, parsed.query, parsed.fragment))
                    copied_environment.append(entry)
                for index, volume in enumerate(service['volumes']):
                    target = volume['target']
                    if not isinstance(target, str) or not target.startswith('/') or ',' in target or '\x00' in target: raise ProjectRecoveryError('Cible de copie invalide.')
                    copied = materialize(root / 'host-files' / recovery.safe_relative(volume['artifact']), app['id'] + '-' + service['name'] + '-v' + str(index))
                    args += ['--mount', 'type=volume,source=' + copied + ',target=' + target + (',readonly' if volume.get('readOnly') else '')]
                if service['workdir']: args += ['--workdir', service['workdir']]
                entrypoint = service.get('entrypoint') or []
                args += ['--entrypoint', entrypoint[0] if entrypoint else '']
                args += [service['image'], *entrypoint[1:], *service.get('command', [])]
                admit(); created.append(name); checkpoint(); docker.create_sandbox(args, copied_environment); docker(['start', name])
                deadline = min(started + max_seconds, time.monotonic() + 45)
                alive = False
                port, health_path = service.get('port'), service.get('healthPath', '/')
                if type(port) is not int or not 1 <= port <= 65535 or not re.fullmatch(r'/[A-Za-z0-9_./-]{0,200}', health_path): raise ProjectRecoveryError('Sonde HTTP de recette non déclarée.')
                while time.monotonic() < deadline:
                    info = json.loads(docker(['inspect', name]))[0]
                    if info.get('State', {}).get('Running'):
                        probe_name = common_prefix + app['id'] + '-probe-' + secrets.token_hex(3)
                        if probe_name not in created: created.append(probe_name); checkpoint()
                        probe = sandbox_base(probe_name, helper_image, anchor, memory_mib=64)
                        probe[0] = 'run'; probe.insert(1, '--rm')
                        probe += ['--env', 'NODE_OPTIONS=', '--env', 'NODE_PATH=', '--env', 'LD_PRELOAD=', '--env', 'LD_LIBRARY_PATH=', '--entrypoint', '/usr/local/bin/node', helper_image, '-e', "fetch(process.argv[1],{redirect:'manual',signal:AbortSignal.timeout(2000)}).then(r=>process.exit(r.status>=200&&r.status<400?0:1)).catch(()=>process.exit(1))", 'http://127.0.0.1:' + str(port) + health_path]
                        try: docker(probe, timeout=5); alive = True; break
                        except Exception: pass
                    elif info.get('State', {}).get('Status') == 'exited': break
                    time.sleep(.5)
                outcomes.append({'appId': app['id'], 'service': service['name'], 'status': 'running-isolated' if alive else 'failed'})
            cleanup_sandbox({'runId': run_id, 'containers': created, 'volumes': volumes, 'images': []}, docker)
            created.clear(); volumes.clear(); checkpoint()
        healthy = any(row['status'] == 'running-isolated' for row in outcomes) and all(row['status'] in ('running-isolated', 'inert-worker-preserved') for row in outcomes)
        return {'completeArtifacts': True, 'runtimeStarted': healthy, 'applicationVerified': False, 'noExternalNetwork': True, 'mailsDisabled': True, 'scheduledTasksDisabled': True,
                'workersInert': True, 'services': outcomes, 'externalDependencies': capsule.get('externalDependencies', []), 'durationSeconds': round(time.monotonic() - started, 1), 'scope': 'isolated-http-health' if any(row['status']=='running-isolated' for row in outcomes) else 'artifacts-only',
                'message': 'Artefacts reconstruits. Les processus frontaux sont démarrés sans réseau sortant ; les parcours métier et services externes nécessitent une recette dédiée.'}
    finally:
        cleanup_sandbox({'runId': run_id, 'containers': created, 'volumes': volumes, 'images': imported_images}, docker)
        journal.unlink(missing_ok=True)


def sandbox_drill(root, docker, helper_image, max_seconds=120, memory_mib=512):
    if getattr(docker, 'socket', None) != '/run/user/1002/docker.sock': raise ProjectRecoveryError('Les copies exigent le moteur de reprise isolé UID1002.')
    require_host_headroom(sandbox=True)
    lock_root = pathlib.Path('/run/kolab-project-recovery'); lock_root.mkdir(mode=0o755,exist_ok=True)
    if lock_root.resolve()!=lock_root or lock_root.stat().st_uid!=0 or lock_root.stat().st_mode&0o022: raise ProjectRecoveryError('Répertoire de verrou root requis.')
    descriptor = os.open(str(lock_root / 'drill.lock'), os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'w') as lock:
        info = os.fstat(lock.fileno())
        if info.st_uid != 0 or info.st_mode & 0o077: raise ProjectRecoveryError('Verrou de recette root requis.')
        try: fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise ProjectRecoveryError('Une recette isolée est déjà active.') from None
        return _sandbox_drill(root, docker, helper_image, max_seconds, memory_mib)


def migration_plan(manifest, target, mappings, available_bytes, now=None):
    """Concrete immutable import plan. Cutover is never executed by this engine."""
    if manifest.get('metadata', {}).get('kind') != 'project-capsule' or manifest.get('coverage', {}).get('complete') is not True: raise ProjectRecoveryError('Capsule de projet complète requise.')
    if target not in ('app3', 'app4', 'app5') or type(available_bytes) is not int or available_bytes < 0: raise ProjectRecoveryError('Cible ou capacité invalide.')
    if not isinstance(mappings, dict) or set(mappings) != {'applications', 'databases', 'environment', 'domains', 'workers', 'scheduledTasks'}: raise ProjectRecoveryError('Mapping complet requis.')
    for category, rows in mappings.items():
        if not isinstance(rows, list) or len(rows) > 200 or any(not isinstance(row, dict) for row in rows): raise ProjectRecoveryError('Mapping invalide.')
    estimate = recovery.restore_budget(manifest, host_files=True)['restoreBytes']
    fits = available_bytes >= RESERVE + 2 * estimate
    date = int(time.time() if now is None else now)
    plan = {'version': 1, 'projectId': manifest['metadata']['projectId'], 'source': manifest['platform'], 'target': target, 'backupId': manifest['backupId'],
            'manifestSha256': hashlib.sha256(encoded(manifest)).hexdigest(), 'mappings': mappings, 'createdAt': date, 'expiresAt': date + 1800,
            'requiredBytes': RESERVE + 2 * estimate, 'availableBytes': available_bytes, 'capacityFits': fits,
            'importMode': 'isolated-paused', 'sourcePreserved': True, 'mailsDisabled': True, 'workersDisabled': True, 'cronsDisabled': True,
            'dnsPreview': [{'domain': row.get('domain'), 'before': row.get('before'), 'after': row.get('after'), 'proxy': row.get('proxy', True)} for row in mappings['domains']],
            'rollback': {'beforeCutover': 'remove-isolated-import', 'afterCutover': 'restore-reviewed-dns-values-and-keep-source', 'databaseWritesReversible': False},
            'cutoverExecuted': False, 'requiresConcreteCutoverApproval': True}
    plan['revision'] = hashlib.sha256(encoded(plan)).hexdigest()
    return plan


def migration_import(archive, keyfile, destination, plan, expected_sha256, max_bytes=LIMIT):
    if not isinstance(plan, dict) or plan.get('capacityFits') is not True or plan.get('importMode') != 'isolated-paused' or type(plan.get('expiresAt')) is not int or plan['expiresAt'] < time.time(): raise ProjectRecoveryError('Plan expiré ou capacité insuffisante.')
    review = {key: value for key, value in plan.items() if key != 'revision'}
    if plan.get('revision') != hashlib.sha256(encoded(review)).hexdigest(): raise ProjectRecoveryError('Révision du plan incorrecte.')
    if not re.fullmatch(r'[a-f0-9]{64}', expected_sha256 or ''): raise ProjectRecoveryError('Empreinte de transfert requise.')
    archive = recovery.private_file(archive); manifest = recovery.inspect_archive(lambda: open(archive, 'rb'), recovery.load_key(keyfile))
    if hashlib.sha256(encoded(manifest)).hexdigest() != plan['manifestSha256']: raise ProjectRecoveryError('La capture a changé depuis le plan.')
    recovery.require_space(pathlib.Path(destination).parent, plan['requiredBytes'])
    result = restore(archive, keyfile, destination, expected_sha256, max_bytes)
    private_json(pathlib.Path(destination) / 'MIGRATION_PAUSED.json', {**plan, 'importedAt': recovery.stamp(), 'sha256': expected_sha256})
    return {**result, 'planRevision': plan['revision'], 'target': plan['target'], 'cutoverExecuted': False, 'sourcePreserved': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest='action', required=True)
    cap = commands.add_parser('capture'); cap.add_argument('--snapshot', required=True); cap.add_argument('--data-root', required=True); cap.add_argument('--binary-root', required=True); cap.add_argument('--archive', required=True); cap.add_argument('--key-file', required=True); cap.add_argument('--docker-socket', default='/run/user/1001/docker.sock'); cap.add_argument('--instance-app-root')
    for name in ('restore', 'drill', 'migration-plan', 'migration-import'):
        sub = commands.add_parser(name); sub.add_argument('--archive', required=name != 'drill'); sub.add_argument('--key-file', required=name != 'drill'); sub.add_argument('--destination', required=name != 'migration-plan')
        if name in ('restore', 'migration-import'): sub.add_argument('--sha256', required=True)
        if name == 'drill': sub.add_argument('--helper-image', required=True); sub.add_argument('--docker-socket', default='/run/user/1002/docker.sock')
        if name in ('migration-plan', 'migration-import'): sub.add_argument('--plan', required=True)
        if name == 'migration-plan': sub.add_argument('--target', choices=['app3', 'app4', 'app5'], required=True); sub.add_argument('--mappings', required=True); sub.add_argument('--available-bytes', type=int, required=True)
    args = parser.parse_args()
    if args.action == 'capture': result = capture(json.loads(recovery.private_file(args.snapshot).read_text()), args.data_root, args.binary_root, args.archive, args.key_file, Docker(args.docker_socket), args.instance_app_root)
    elif args.action == 'restore': result = restore(args.archive, args.key_file, args.destination, args.sha256)
    elif args.action == 'drill': result = sandbox_drill(args.destination, Docker(args.docker_socket), args.helper_image)
    elif args.action == 'migration-plan':
        manifest = recovery.inspect_archive(lambda: open(recovery.private_file(args.archive), 'rb'), recovery.load_key(args.key_file))
        result = migration_plan(manifest, args.target, json.loads(recovery.private_file(args.mappings).read_text()), args.available_bytes); private_json(args.plan, result)
    else: result = migration_import(args.archive, args.key_file, args.destination, json.loads(recovery.private_file(args.plan).read_text()), args.sha256)
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        if len(sys.argv) == 4 and sys.argv[1] == 'sqlite-snapshot': sqlite_snapshot_child(sys.argv[2], sys.argv[3])
        else: main()
    except Exception as error:
        # Private engine failures never emit process stderr, env or source paths.
        print(json.dumps({'status': 'failed', 'code': type(error).__name__, 'error': str(error) if isinstance(error, ProjectRecoveryError) else 'La reprise isolée a échoué.'})); sys.exit(1)
