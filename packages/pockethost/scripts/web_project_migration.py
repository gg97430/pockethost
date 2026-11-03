#!/usr/bin/env python3
"""Reviewed project imports. New IDs only; imported services remain suspended.

The CLI runs locally on an operator-selected host. It never copies an SSH key,
stops a source service, starts a business worker or changes DNS during import.
Cloudflare cutover is a separate, explicitly approved operation with a journal.
"""
import argparse
import contextlib
import fcntl
import datetime
import hashlib
import http.client
import json
import os
import pathlib
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import web_project_recovery as recovery

FLEET = {'app3': {'host': 'app3.monappli.re', 'address': '135.125.67.39'}, 'app4': {'host': 'app4.monappli.re', 'address': '51.255.69.154'}, 'app5': {'host': 'app5.monappli.re', 'address': '217.182.134.72'}}
ID = re.compile(r'[a-z0-9]{15}\Z')
SHA = re.compile(r'[a-f0-9]{64}\Z')
HOST = re.compile(r'(?=.{4,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z')
PRIVATE = pathlib.Path('/etc/kolab/project-migration-control.key')
RESERVE = 15 * 1024**3


class MigrationError(recovery.ProjectRecoveryError): pass


def identifier(): return ''.join(secrets.choice('abcdefghijklmnopqrstuvwxyz0123456789') for _ in range(15))
def digest_value(value): return hashlib.sha256(recovery.encoded(value)).hexdigest()
def epoch(): return int(time.time())


def private_json(filename, value):
    filename = pathlib.Path(filename).absolute()
    if filename.parent.resolve() != filename.parent or filename.is_symlink(): raise MigrationError('Chemin privé non canonique.')
    descriptor, temporary = tempfile.mkstemp(prefix='.' + filename.name + '-', dir=filename.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600); stream.write(recovery.encoded(value)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, filename)
        directory = os.open(filename.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def capsule(directory):
    directory = recovery.canonical(directory, True)
    manifest = json.loads(recovery.recovery.private_file(directory / 'recovery-manifest.json').read_text())
    value = recovery.verify_materialized(directory, manifest)
    if manifest.get('coverage', {}).get('complete') is not True: raise MigrationError('La capsule est incomplète.')
    return value, manifest


def checked_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value): raise MigrationError('Identifiant de migration invalide.')
    return value


def target_host(target):
    if target not in FLEET: raise MigrationError('Serveur cible inconnu.')
    return FLEET[target]['host']


def source_environment(app):
    env = app.get('configuration', {}).get('env', {})
    if not isinstance(env, dict) or len(env) > 500: raise MigrationError('Inventaire de variables invalide.')
    for name, entry in env.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,119}', name) or not isinstance(entry, dict) or not isinstance(entry.get('value'), str) or '\x00' in entry['value'] or any(type(entry.get(scope)) is not bool for scope in ('build', 'runtime')):
            raise MigrationError('Variable source invalide.')
    return env


def build_plan(directory, target, owner_id, archive_sha256, project_name=None, overrides=None, now=None):
    """Exact coverage is derived from the authenticated restored capsule, never a caller list."""
    value, manifest = capsule(directory)
    checked_id(owner_id)
    if not SHA.fullmatch(archive_sha256 or ''): raise MigrationError('Empreinte de l’archive requise.')
    source = manifest.get('platform')
    if source not in FLEET or source == target: raise MigrationError('Deux serveurs distincts de la flotte sont requis.')
    host = target_host(target); migration_id = identifier(); created = epoch() if now is None else now
    project_label = project_name or (value.get('projectName') or 'Projet') + ' · migration'
    if not isinstance(project_label, str) or not project_label.strip() or len(project_label) > 80: raise MigrationError('Nom cible requis, 80 caractères maximum.')
    overrides = overrides or {}
    if not isinstance(overrides, dict) or set(overrides) - {'environment', 'tasks', 'databaseSubdomains'}: raise MigrationError('Surcharges explicites invalides.')
    applications = [{'sourceId': app['id'], 'targetId': identifier(), 'sourceDeployment': app['deploymentId'], 'targetDeployment': identifier(), 'name': app.get('name') or app['id'], 'hasFront': any(service['front'] for service in app['services'])} for app in value['apps']]
    by_app = {row['sourceId']: row for row in applications}; databases = []
    for ident, description in value['databases'].items():
        subdomain = overrides.get('databaseSubdomains', {}).get(ident, 'migr-' + ident[:6] + '-' + migration_id[:6])
        if not isinstance(subdomain, str) or subdomain.startswith('web-') or not re.fullmatch(r'[a-z][a-z0-9-]{1,58}[a-z0-9]', subdomain): raise MigrationError('Sous-domaine de base invalide.')
        reserved = description.get('memoryReservationMiB', 0)
        maximum = description.get('memoryMaximumMiB', 4096)
        if type(reserved) is not int or reserved < 0 or type(maximum) is not int or maximum < 0: raise MigrationError('Budget natif invalide.')
        databases.append({'sourceId': ident, 'targetId': identifier(), 'subdomain': subdomain, 'version': description['version'], 'targetHost': subdomain + '.' + host, 'memoryReservationMiB': reserved, 'memoryRequestMiB': reserved or min(maximum or 4096, 4096)})
    by_db = {row['sourceId']: row for row in databases}
    replacements = {}
    for app in value['apps']:
        generated = app.get('configuration', {}).get('config', {}).get('generatedDomain')
        if generated: replacements[generated] = 'web-' + by_app[app['id']]['targetId'] + '.' + host
        for binding in app.get('bindings', []):
            for previous in binding.get('publicHosts', []):
                if not HOST.fullmatch(previous): raise MigrationError('Domaine source de base invalide.')
                replacements[previous] = by_db[binding['instanceId']]['targetHost']
    environment, tasks, bindings, domains, volumes, blockers = [], [], [], [], [], []
    for ident, description in value['databases'].items():
        if any(pathlib.PurePosixPath(support['artifact']).name in ('pb_hooks','pb_migrations') and support.get('state',{}).get('files') for support in description.get('support',[])):
            blockers.append({'code':'native_hooks_require_review','appId':'','name':ident})
        if description.get('externalApplicationBindingsCount',0): blockers.append({'code':'shared_database_writers','appId':'','name':ident})
    for app in value['apps']:
        mapped = by_app[app['id']]
        unknown_env = set(overrides.get('environment', {}).get(app['id'], {})) - set(source_environment(app))
        if unknown_env: raise MigrationError('Une surcharge cible une variable inexistante.')
        for name, entry in source_environment(app).items():
            next_value = overrides.get('environment', {}).get(app['id'], {}).get(name, entry['value'])
            if not isinstance(next_value, str) or '\x00' in next_value: raise MigrationError('Valeur de surcharge invalide.')
            explicit = name in overrides.get('environment', {}).get(app['id'], {})
            changed_hosts = []
            if not explicit:
                for old, new in replacements.items():
                    # Only whole URL host boundaries are rewritten, never arbitrary IDs/secrets.
                    pattern = re.compile(r'(?<=://)' + re.escape(old) + r'(?=[:/\s\?#]|$)')
                    replaced = pattern.sub(new, next_value)
                    if replaced != next_value: changed_hosts.append(old)
                    next_value = replaced
            if entry['build'] and next_value != entry['value']:
                blockers.append({'code': 'build_url_requires_rebuild', 'appId': app['id'], 'name': name})
            environment.append({'appSourceId': app['id'], 'appTargetId': mapped['targetId'], 'name': name, 'build': entry['build'], 'runtime': entry['runtime'], 'action': 'override' if explicit else 'rewrite-url' if changed_hosts else 'preserve', 'value': next_value, 'sourceSha256': hashlib.sha256(entry['value'].encode()).hexdigest()})
        for task in app.get('scheduledTasks', []):
            command = overrides.get('tasks', {}).get(task['id'], task['command'])
            if not isinstance(command, str) or not command or len(command) > 10000 or '\x00' in command: raise MigrationError('Commande planifiée invalide.')
            for old, new in replacements.items(): command = command.replace('://' + old + '/', '://' + new + '/')
            tasks.append({'sourceId': task['id'], 'targetId': identifier(), 'appSourceId': app['id'], 'appTargetId': mapped['targetId'], 'name': task['name'], 'schedule': task['schedule'], 'timezone': task.get('timezone') or 'UTC', 'timeoutSeconds': task.get('timeoutSeconds') or 60, 'sourceEnabled': task.get('enabled') is True, 'enabled': False, 'command': command})
        for binding in app.get('bindings', []):
            bindings.append({'targetId': identifier(), 'appSourceId': app['id'], 'appTargetId': mapped['targetId'], 'instanceSourceId': binding['instanceId'], 'instanceTargetId': by_db[binding['instanceId']]['targetId'], 'port': binding['port']})
        for domain in app.get('domains', []):
            if not HOST.fullmatch(domain.get('host', '')): raise MigrationError('Domaine de projet invalide.')
            domains.append({'targetId': identifier(), 'appSourceId': app['id'], 'appTargetId': mapped['targetId'], 'host': domain['host'], 'sourceStatus': domain.get('status', ''), 'targetStatus': 'pending', 'proxied': True})
        declared = app.get('configuration', {}).get('storage', [])
        for item in declared:
            matching = [volume for service in app['services'] for volume in service['volumes'] if volume['name'].startswith('ph-web-data-' + app['id'] + '-' + item['name'] + '-')]
            if not matching: raise MigrationError('Un volume déclaré manque dans la capsule.')
            if len({volume['state']['treeSha256'] for volume in matching}) != 1: raise MigrationError('Un volume partagé a des captures différentes.')
            volumes.append({'appSourceId': app['id'], 'appTargetId': mapped['targetId'], 'name': item['name'], 'targetName': 'ph-web-data-' + mapped['targetId'] + '-' + item['name'] + '-v1', 'artifact': matching[0]['artifact'], 'state': matching[0]['state'], 'configuration': item})
        if app.get('externalDependencies'): blockers.extend({'code': 'external_dependency', 'appId': app['id'], 'name': dep} for dep in app['externalDependencies'])
    source_tasks = {task['id'] for app in value['apps'] for task in app.get('scheduledTasks', [])}
    if set(overrides.get('tasks', {})) - source_tasks or set(overrides.get('databaseSubdomains', {})) - set(by_db): raise MigrationError('Surcharge hors du projet.')
    size = recovery.recovery.restore_budget(manifest, host_files=True)['restoreBytes']
    memory = sum(row['memoryRequestMiB'] for row in databases)
    for app in value['apps']:
        runtime = app.get('configuration', {}).get('runtime', {})
        memory += runtime.get('memoryRequestMiB') or min(runtime.get('resources', {}).get('memoryMiB') or 768, 768)
    plan = {'version': 1, 'id': migration_id, 'projectId': value['projectId'], 'targetProjectId': identifier(), 'projectName': project_label.strip(), 'source': source, 'target': target, 'ownerId': owner_id, 'archiveSha256': archive_sha256, 'manifestSha256': digest_value(manifest), 'capsuleRevision': value.get('revision', ''), 'createdAt': created, 'expiresAt': created + 1800,
            'requiredBytes': RESERVE + 3 * size, 'restoreBytes': size, 'requiredMemoryMiB': memory, 'applications': applications, 'databases': databases, 'environment': environment, 'bindings': bindings, 'scheduledTasks': tasks, 'domains': domains, 'volumes': volumes,
            'workers': [{'appSourceId': app['id'], 'appTargetId': by_app[app['id']]['targetId'], 'service': service['name'], 'enabled': False} for app in value['apps'] for service in app['services'] if not service['front']],
            'blockers': blockers, 'writeBarrier':value.get('migrationSourceBarrier'), 'mode': 'paused-import', 'autoDeploy': False, 'cronsEnabled': False, 'mailsEnabled': False, 'sourcePreserved': True, 'cutoverExecuted': False,
            'limitations': ['Les écritures distribuées ne sont pas atomiques : gel des écritures source requis avant une bascule réelle.', 'Les sources Git et les services externes restent propres au serveur cible : reconnexion requise avant un nouveau build.', 'Les caches Next sont conservés dans la capsule ; le runtime cible les régénère.', 'Un rollback DNS ne réconcilie pas les écritures réalisées sur la cible.']}
    plan['revision'] = digest_value(plan)
    validate_plan(plan, value, manifest)
    return plan


def validate_plan(plan, value, manifest, allow_expired=False):
    if not isinstance(plan, dict) or plan.get('version') != 1 or plan.get('mode') != 'paused-import' or plan.get('source') not in FLEET or plan.get('target') not in FLEET or plan['source'] == plan['target']:
        raise MigrationError('Plan de migration invalide.')
    if plan.get('revision') != digest_value({key: val for key, val in plan.items() if key != 'revision'}): raise MigrationError('Le plan a changé depuis sa révision.')
    if not allow_expired and (type(plan.get('expiresAt')) is not int or plan['expiresAt'] < epoch()): raise MigrationError('Le plan de migration a expiré.')
    if plan['manifestSha256'] != digest_value(manifest) or plan['projectId'] != value['projectId'] or plan['source'] != manifest['platform']: raise MigrationError('La capsule ne correspond pas au plan.')
    checked_id(plan['id']); checked_id(plan['ownerId']); checked_id(plan['targetProjectId'])
    ids = [plan['targetProjectId']]
    def exact(rows, expected, source_key='sourceId', extra=None):
        if not isinstance(rows, list) or len(rows) != len(expected) or {row[source_key] for row in rows} != set(expected): raise MigrationError('Le mapping ne couvre pas exactement le projet.')
        if extra:
            for row in rows: extra(row)
    exact(plan['applications'], [app['id'] for app in value['apps']]); exact(plan['databases'], value['databases'])
    app_map = {row['sourceId']: row['targetId'] for row in plan['applications']}; db_map = {row['sourceId']: row['targetId'] for row in plan['databases']}
    for row in plan['applications']:
        checked_id(row['sourceDeployment']); checked_id(row['targetDeployment']); ids.extend([row['targetId'], row['targetDeployment']])
        if row['sourceDeployment'] != next(app['deploymentId'] for app in value['apps'] if app['id'] == row['sourceId']): raise MigrationError('Publication source différente.')
        if row['hasFront'] is not any(service['front'] for app in value['apps'] if app['id'] == row['sourceId'] for service in app['services']): raise MigrationError('Le frontal source est différent.')
    for row in plan['databases']:
        ids.append(row['targetId'])
        if row['version'] != value['databases'][row['sourceId']]['version'] or row['targetHost'] != row['subdomain'] + '.' + target_host(plan['target']): raise MigrationError('Mapping de base différent.')
        if row['memoryReservationMiB'] != value['databases'][row['sourceId']].get('memoryReservationMiB', 0): raise MigrationError('Une réservation RAM explicite de la source doit être conservée.')
    def coverage(category, observed, actual):
        if len(observed) != len(actual) or set(observed) != set(actual): raise MigrationError('Mapping incomplet : ' + category)
    coverage('variables', [(row['appSourceId'], row['name']) for row in plan['environment']], [(app['id'], name) for app in value['apps'] for name in source_environment(app)])
    coverage('tâches', [row['sourceId'] for row in plan['scheduledTasks']], [row['id'] for app in value['apps'] for row in app.get('scheduledTasks', [])])
    coverage('liaisons', [(row['appSourceId'], row['instanceSourceId'], row['port']) for row in plan['bindings']], [(app['id'], row['instanceId'], row['port']) for app in value['apps'] for row in app.get('bindings', [])])
    coverage('domaines', [(row['appSourceId'], row['host']) for row in plan['domains']], [(app['id'], row['host']) for app in value['apps'] for row in app.get('domains', [])])
    coverage('workers', [(row['appSourceId'], row['service']) for row in plan['workers']], [(app['id'], row['name']) for app in value['apps'] for row in app['services'] if not row['front']])
    coverage('volumes', [(row['appSourceId'], row['name']) for row in plan['volumes']], [(app['id'], row['name']) for app in value['apps'] for row in app.get('configuration', {}).get('storage', [])])
    for category in ('environment', 'scheduledTasks', 'bindings', 'domains', 'volumes', 'workers'):
        for row in plan[category]:
            if app_map.get(row['appSourceId']) != row['appTargetId']: raise MigrationError('Application cible incohérente.')
            if 'targetId' in row: ids.append(row['targetId'])
    for row in plan['bindings']:
        if db_map.get(row['instanceSourceId']) != row['instanceTargetId']: raise MigrationError('Base cible incohérente.')
    for ident in ids: checked_id(ident)
    if len(ids) != len(set(ids)) or set(ids) & ({app['id'] for app in value['apps']} | set(value['databases'])): raise MigrationError('La migration exige exclusivement de nouveaux identifiants.')
    return plan


def public_plan(plan):
    result = {key: plan[key] for key in ('version', 'id', 'revision', 'source', 'target', 'projectId', 'targetProjectId', 'projectName', 'ownerId', 'archiveSha256', 'createdAt', 'expiresAt', 'requiredBytes', 'restoreBytes', 'requiredMemoryMiB', 'applications', 'databases', 'bindings', 'domains', 'workers', 'blockers', 'mode', 'limitations')}
    result['environment'] = [{key: row[key] for key in ('appSourceId', 'appTargetId', 'name', 'build', 'runtime', 'action')} for row in plan['environment']]
    result['scheduledTasks'] = [{key: row[key] for key in ('sourceId', 'targetId', 'appTargetId', 'name', 'schedule', 'timezone', 'sourceEnabled', 'enabled')} for row in plan['scheduledTasks']]
    result['volumes'] = [{key: row[key] for key in ('appTargetId', 'name', 'targetName')} | {'bytes': row['state']['bytes']} for row in plan['volumes']]
    return result


class Control:
    def __init__(self, keyfile=PRIVATE, port=8091):
        path = pathlib.Path(keyfile).absolute(); info = path.stat()
        if path.resolve() != path or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o027 or info.st_size > 100:
            raise MigrationError('Clé locale root, non modifiable par le contrôleur, requise.')
        self.key = path.read_text().strip()
        if not re.fullmatch(r'[A-Za-z0-9]{80}', self.key) or type(port) is not int or not 1024 <= port <= 65535: raise MigrationError('Clé opérateur locale invalide.')
        self.origin = 'http://127.0.0.1:' + str(port)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs): return None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    def call(self, action, body):
        if action not in ('export-snapshot', 'prepare', 'take', 'commit', 'fail', 'rollback', 'rolled-back', 'state', 'dns-preview', 'dns-ready', 'dns-result'): raise MigrationError('Action opérateur invalide.')
        request = urllib.request.Request(self.origin + '/api/web-project-migration/_operator/' + action, data=recovery.encoded(body), headers={'Content-Type': 'application/json', 'X-Project-Migration': self.key})
        with self.opener.open(request, timeout=20) as response:
            raw = response.read(4 * 1024**2 + 1)
            if len(raw) > 4 * 1024**2: raise MigrationError('Réponse du contrôleur trop volumineuse.')
            return json.loads(raw)


def private_docker_request(docker, method, path, value=None):
    socket_path = docker.command[-1].removeprefix('unix://')
    class Connection(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); self.sock.settimeout(30); self.sock.connect(socket_path)
    connection = Connection('localhost', timeout=30)
    try:
        connection.request(method, path, body=None if value is None else recovery.encoded(value), headers={'Content-Type': 'application/json'})
        response = connection.getresponse(); raw = response.read(1024**2 + 1)
        if not 200 <= response.status < 300 or len(raw) > 1024**2: raise MigrationError('Le moteur a refusé l’artefact de migration.')
        return json.loads(raw) if raw else {}
    finally: connection.close()


def own_image(docker, source, app_id, deployment_id, service, migration_id):
    """Commit a stopped metadata-only container; no instruction from the image is run."""
    image = json.loads(docker(['image', 'inspect', source]))[0]
    if image.get('Id') != source: raise MigrationError('Image immuable différente.')
    name = 'kolab-migration-' + migration_id + '-' + app_id + '-' + service
    temporary = private_docker_request(docker, 'POST', '/containers/create?' + urllib.parse.urlencode({'name': name}), {'Image': source, 'User': '10001:10001', 'Cmd': ['/unused'], 'Labels': {'kolab.project-migration': migration_id}, 'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges'], 'Memory': 64 * 1024**2, 'PidsLimit': 32}})['Id']
    try:
        labels = {**(image.get('Config', {}).get('Labels') or {}), 'pockethost.web': 'true', 'pockethost.web.app': app_id, 'pockethost.web.deployment': deployment_id, 'pockethost.web.service': service, 'kolab.project-migration': migration_id}
        result = private_docker_request(docker, 'POST', '/commit?' + urllib.parse.urlencode({'container': temporary, 'repo': 'ph-web/' + app_id, 'tag': deployment_id + '-' + service, 'pause': 'false'}), {**(image.get('Config') or {}), 'Labels': labels, 'User': '10001:10001'})
        if not recovery.IMAGE.fullmatch(result.get('Id', '')): raise MigrationError('Empreinte de l’image cible invalide.')
        return result['Id']
    finally:
        info = json.loads(docker(['inspect', temporary]))[0]
        if info.get('Config', {}).get('Labels', {}).get('kolab.project-migration') != migration_id or info.get('State', {}).get('Running'): raise MigrationError('Conteneur de métadonnées non conforme ; nettoyage refusé.')
        private_docker_request(docker, 'DELETE', '/containers/' + temporary + '?v=true')


def bounded_copy_tree(source, destination, reserve=RESERVE):
    source = recovery.canonical(source, True)
    if destination.exists() or destination.resolve() != destination: raise MigrationError('La cible de fichiers existe déjà.')
    state = recovery.tree_state(source)
    recovery.recovery.require_space(destination.parent, reserve + sum(row[0] for row in state.values()))
    destination.mkdir(mode=0o700, parents=True)
    for current, dirs, _files in os.walk(source):
        for name in dirs: (destination / (pathlib.Path(current) / name).relative_to(source)).mkdir(mode=(pathlib.Path(current) / name).stat().st_mode & 0o777, parents=True, exist_ok=True)
    for name in state:
        target = destination / name; target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(source / name, 'rb') as inp, open(target, 'xb') as out:
            os.fchmod(out.fileno(), (source / name).stat().st_mode & 0o777)
            while True:
                block = inp.read(1024**2)
                if not block: break
                recovery.recovery.require_space(destination.parent, reserve + len(block)); out.write(block)
    result = recovery.tree_state(destination)
    if {name: (row[0], row[3]) for name, row in result.items()} != {name: (row[0], row[3]) for name, row in state.items()}: raise MigrationError('Copie de fichiers incomplète.')
    return digest_value({name: [row[0], row[3]] for name, row in result.items()})


def memory_admission(plan):
    from database_memory_reservations import reserved_headroom
    values = {line.split(':')[0]: int(line.split()[1]) * 1024 for line in pathlib.Path('/proc/meminfo').read_text().splitlines() if ':' in line}
    reserve = max(2 * 1024**3, values['MemTotal'] * 15 // 100)
    if values['MemAvailable'] - reserved_headroom() - reserve < plan['requiredMemoryMiB'] * 1024**2:
        raise MigrationError('La RAM disponible et les engagements existants ne permettent pas le projet cible.')


def install_memory_commitments(plan, journal, artifacts):
    from database_memory_management import policy_lock
    from database_memory_reservations import POLICY, load_policy, MIB
    from platform_memory_allocator import atomic
    with policy_lock():
        memory_admission(plan)
        policy = load_policy(); additions = {row['targetId']: row['memoryReservationMiB'] * MIB for row in plan['databases'] if row['memoryReservationMiB']}
        if set(additions) & set(policy): raise MigrationError('Un engagement RAM cible existe déjà.')
        artifacts['memoryCommitments'] = {key: value // MIB for key, value in additions.items()}; private_json(journal, artifacts)
        if additions: atomic(POLICY, {'version': 1, 'databases': {key: value // MIB for key, value in {**policy, **additions}.items()}}, 0o644)
        artifacts['memoryApplied'] = True; private_json(journal, artifacts)


def materialize(directory, plan, config, docker, helper_image):
    """Create fresh native paths/images/volumes. Never starts an imported app/database."""
    value, manifest = capsule(directory); validate_plan(plan, value, manifest)
    data_root = recovery.canonical(config['dataRoot'], True); binary_root = recovery.canonical(config['binaryRoot'], True); web_root = recovery.canonical(config['webRoot'], True)
    state_root = recovery.canonical(config['stateRoot'], True); journal = state_root / (plan['id'] + '.json')
    previous = json.loads(recovery.recovery.private_file(journal).read_text()) if journal.exists() else {}
    if previous and (previous.get('id')!=plan['id'] or previous.get('phase')!='drill-complete' or any(previous.get(key) for key in ('databases','volumes','storageManifests','images'))): raise MigrationError('Un journal de cette migration existe déjà.')
    recovery.recovery.require_space(data_root, plan['requiredBytes']); recovery.recovery.require_space(web_root, plan['requiredBytes'])
    memory_admission(plan)
    artifacts = {**previous, 'version': 1, 'id': plan['id'], 'revision': plan['revision'], 'databases': [], 'volumes': [], 'images': {}, 'containers': [], 'storageManifests': [], 'committed': False,'phase':'materializing'}
    def save(): private_json(journal, artifacts)
    save()
    instance_parent=data_root/'instances'
    if not instance_parent.exists():instance_parent.mkdir(mode=0o700);os.chown(instance_parent,1000,1000)
    for row in plan['databases']:
        ident, old = row['targetId'], row['sourceId']; target = data_root / 'instances' / ident
        if target.exists() or target.resolve() != target: raise MigrationError('Répertoire de base cible déjà présent.')
        artifacts['databases'].append({'id': ident, 'path': str(target), 'sha256': ''}); save()
        bounded_copy_tree(pathlib.Path(directory) / 'instances' / old / 'pb_data', target / 'pb_data')
        description = value['databases'][old]
        support_root = pathlib.Path(directory) / 'host-files/native-support' / old
        if support_root.exists():
            for source in support_root.iterdir():
                if source.is_dir(): bounded_copy_tree(source, target / ('.migration-disabled-' + source.name if source.name in ('pb_hooks','pb_migrations') else source.name))
                else:
                    recovery.recovery.require_space(target, RESERVE + source.stat().st_size)
                    with open(source, 'rb') as inp, open(target / source.name, 'xb') as out:
                        os.fchmod(out.fileno(), 0o600); shutil.copyfileobj(inp, out, 1024**2)
                    if recovery.digest(source) != recovery.digest(target / source.name): raise MigrationError('Support natif incomplet.')
        binary = binary_root / row['version'] / 'linux_amd64' / 'pocketbase'
        captured = pathlib.Path(directory) / 'host-files/bin' / ('pocketbase-' + old)
        if binary.exists():
            if recovery.digest(binary) != recovery.digest(captured): raise MigrationError('Le binaire cible diffère de la version capturée.')
        else:
            binary.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            with open(binary, 'xb') as out, open(captured, 'rb') as inp: shutil.copyfileobj(inp, out)
            binary.chmod(0o755)
        # Source record owners are never cloned. Only the selected target account owns the copy.
        for current, dirs, files in os.walk(target):
            os.chown(current, 1000, 1000); os.chmod(current, 0o700)
            for name in files: os.chown(pathlib.Path(current) / name, 1000, 1000)
        artifacts['databases'][-1]['sha256'] = digest_value({name: [item[0], item[3]] for name, item in recovery.tree_state(target).items()}); save()
    for app in value['apps']:
        mapped = next(row for row in plan['applications'] if row['sourceId'] == app['id']); owned = {}
        for service in app['services']:
            image = service['image']
            try: exists = json.loads(docker(['image', 'inspect', image]))[0].get('Id') == image
            except Exception: exists = False
            if not exists: docker.load(pathlib.Path(directory) / 'host-files/images' / (image[7:] + '.tar'))
            owned[service['name']] = own_image(docker, image, mapped['targetId'], mapped['targetDeployment'], service['name'], plan['id'])
            artifacts['images'][mapped['targetId']] = owned; save()
    for row in plan['volumes']:
        name = row['targetName']; existing = docker(['volume', 'ls', '--format', '{{.Name}}']).split()
        if name in existing: raise MigrationError('Le volume cible existe déjà.')
        artifacts['volumes'].append({'name': name, 'appId': row['appTargetId'], 'sha256': row['state']['treeSha256'],'created':False}); save()
        docker(['volume', 'create', '--label', 'pockethost.web=true', '--label', 'pockethost.web.app=' + row['appTargetId'], '--label', 'pockethost.web.volume=' + row['name'], '--label', 'kolab.project-migration=' + plan['id'], name])
        artifacts['volumes'][-1]['created']=True;save()
        inspected = json.loads(docker(['volume', 'inspect', name]))[0]
        if inspected.get('Driver') != 'local' or inspected.get('Options'): raise MigrationError('Volume cible non local.')
        # Never write to a host Mountpoint supplied by the rootless daemon.
        # The Docker API receives only this already authenticated project tree.
        holder = 'kolab-migration-' + plan['id'] + '-volume-' + str(len(artifacts['volumes']))
        artifacts['containers'].append(holder); save()
        docker(['create', '--name', holder, '--label', 'kolab.project-migration=' + plan['id'], '--network', 'none', '--read-only', '--user', '0:0', '--cap-drop', 'ALL', '--cap-add', 'CHOWN', '--security-opt', 'no-new-privileges', '--memory', '64m', '--memory-swap', '64m', '--cpus', '0.1', '--pids-limit', '32', '--mount', 'type=volume,source=' + name + ',target=/volume', '--entrypoint', '/bin/sh', helper_image, '-c', 'chown -R 10001:10001 /volume'])
        source = recovery.canonical(pathlib.Path(directory) / 'host-files' / recovery.recovery.safe_relative(row['artifact']), True)
        process = subprocess.Popen(docker.command + ['cp', str(source) + '/.', holder + ':/volume'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 600
            while process.poll() is None:
                recovery.recovery.require_space(web_root, RESERVE + row['state']['bytes'] + 1024**2)
                if time.monotonic() > deadline: raise MigrationError('Copie de volume hors délai.')
                time.sleep(.1)
            if process.returncode: raise MigrationError('Copie de volume incomplète.')
        finally:
            if process.poll() is None: process.kill(); process.wait(timeout=5)
        docker(['start', '-a', holder], timeout=60); docker(['rm', holder]); artifacts['containers'].remove(holder); save()
    for row in plan['applications']:
        selected = [volume for volume in plan['volumes'] if volume['appTargetId'] == row['targetId']]
        if not selected: continue
        target = web_root / 'storage' / (row['targetId'] + '.json')
        if target.exists(): raise MigrationError('Manifeste de stockage cible présent.')
        if not target.parent.exists():target.parent.mkdir(mode=0o750,parents=True);os.chown(target.parent,1001,1001)
        state = {'version': 1, 'appId': row['targetId'], 'volumes': {volume['name']: {**volume['configuration'], 'activeVolume': volume['targetName']} for volume in selected}}
        artifacts['storageManifests'].append({'path':str(target),'sha256':digest_value(state),'created':False});save()
        private_json(target, state); os.chown(target, 1001, 1001); target.chmod(0o640)
        artifacts['storageManifests'][-1]['created']=True;save()
    install_memory_commitments(plan, journal, artifacts)
    return artifacts


def volume_digest(docker, name, helper_image, migration_id):
    script = r"const fs=require('fs'),c=require('crypto'),p=require('path');let rows={},count=0,total=0;function walk(d){for(const e of fs.readdirSync(d,{withFileTypes:true})){const f=p.join(d,e.name),s=fs.lstatSync(f);if(s.isSymbolicLink()||(!s.isDirectory()&&!s.isFile()))throw Error('unsafe');if(s.isDirectory())walk(f);else{if(++count>100000||(total+=s.size)>34359738368)throw Error('budget');const h=c.createHash('sha256'),fd=fs.openSync(f,'r'),b=Buffer.alloc(1048576);let n;while((n=fs.readSync(fd,b,0,b.length,null)))h.update(b.subarray(0,n));fs.closeSync(fd);rows[p.relative('/volume',f)]=[s.size,h.digest('hex')];}}}walk('/volume');function enc(v){return Array.isArray(v)?'['+v.map(enc).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>enc(k)+':'+enc(v[k])).join(',')+'}':JSON.stringify(v).replace(/[\u007f-\uffff]/g,x=>'\\u'+x.charCodeAt(0).toString(16).padStart(4,'0'));}console.log(c.createHash('sha256').update(enc(rows)).digest('hex'));"
    holder = 'kolab-migration-' + migration_id + '-verify-' + secrets.token_hex(4)
    args = ['run', '--name', holder, '--rm', '--label', 'kolab.project-migration=' + migration_id, '--network', 'none', '--read-only', '--user', '10001:10001', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--memory', '128m', '--memory-swap', '128m', '--cpus', '0.2', '--pids-limit', '32', '--mount', 'type=volume,source=' + name + ',target=/volume,readonly']
    for key in ('NODE_OPTIONS','NODE_PATH','LD_PRELOAD','LD_LIBRARY_PATH'): args.extend(['--env',key+'='])
    args.extend(['--entrypoint','/usr/local/bin/node',helper_image,'-e',script])
    try:
        result = docker(args, timeout=120).strip()
        if not SHA.fullmatch(result): raise MigrationError('Vérification de volume impossible.')
        return result
    finally:
        if holder in docker(['ps','-a','--format','{{.Names}}']).split():
            info = json.loads(docker(['inspect',holder]))[0]
            if info.get('Config',{}).get('Labels',{}).get('kolab.project-migration') != migration_id: raise MigrationError('Identité de vérification différente.')
            docker(['rm','-f',holder])


@contextlib.contextmanager
def migration_lock(plan, config):
    root = recovery.canonical(config['stateRoot'], True)
    checked_id(plan['id'])
    descriptor = os.open(root / (plan['id'] + '.lock'), os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, 'a') as stream:
        info = os.fstat(stream.fileno())
        if info.st_uid != os.geteuid() or info.st_mode & 0o077: raise MigrationError('Verrou opérateur non privé.')
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _apply_import(directory, plan, config, control, docker, helper_image):
    value, manifest = capsule(directory); validate_plan(plan, value, manifest)
    control.call('take', {'id': plan['id'], 'revision': plan['revision']})
    try:
        journal=pathlib.Path(config['stateRoot'])/(plan['id']+'.json')
        if journal.exists(): raise MigrationError('Réconciliez le journal existant avant un nouvel import.')
        sandbox_socket=config.get('sandboxDockerSocket','/run/user/1002/docker.sock')
        if sandbox_socket==config['dockerSocket']: raise MigrationError('Un moteur isolé distinct est requis pour la recette.')
        started={'version':1,'id':plan['id'],'revision':plan['revision'],'databases':[],'volumes':[],'images':{},'containers':[],'storageManifests':[],'phase':'drill','sandboxDirectory':str(recovery.canonical(directory,True)),'sandboxSocket':sandbox_socket}
        private_json(journal,started)
        probe=recovery.sandbox_drill(directory,recovery.Docker(sandbox_socket),helper_image)
        if pathlib.Path(directory,'sandbox-state.json').exists(): raise MigrationError('Le nettoyage de la recette isolée reste incomplet.')
        expected={(app['id'],service['name']):'running-isolated'if service['front']else'inert-worker-preserved'for app in value['apps']for service in app['services']}
        observed={(row['appId'],row['service']):row['status']for row in probe.get('services',[])}
        if observed!=expected or probe.get('noExternalNetwork')is not True or probe.get('completeArtifacts')is not True: raise MigrationError('La recette isolée ne couvre pas les services du projet.')
        started.update(phase='drill-complete',drill={'capsuleRevision':plan['capsuleRevision'],'manifestSha256':plan['manifestSha256'],'services':probe['services'],'noExternalNetwork':True,'cleanedUp':True,'sandboxUid':1002});private_json(journal,started)
        artifacts = materialize(directory, plan, config, docker, helper_image)
        result = control.call('commit', {'id': plan['id'], 'revision': plan['revision'], 'plan': plan, 'capsule': value, 'images': artifacts['images'],'drill':artifacts['drill']})
        artifacts['committed'] = True; private_json(pathlib.Path(config['stateRoot']) / (plan['id'] + '.json'), artifacts)
        return {'importedPaused': True, 'sourcePreserved': True, 'cutoverExecuted': False, 'id': plan['id'], 'result': result}
    except Exception as error:
        with contextlib.suppress(Exception):
            failed_journal=pathlib.Path(config['stateRoot'])/(plan['id']+'.json')
            if failed_journal.exists():
                state=json.loads(recovery.recovery.private_file(failed_journal).read_text())
                state['failure']={'type':type(error).__name__,'message':str(error)[:1200]if isinstance(error,recovery.ProjectRecoveryError)else'Opération refusée ; consulter le contrôle natif et la phase du journal privé.'}
                private_json(failed_journal,state)
        with contextlib.suppress(Exception): control.call('fail', {'id': plan['id'], 'revision': plan['revision']})
        raise


def _rollback_import(plan, config, control, docker):
    """Only untouched locked copies. All CAS checks precede the first removal."""
    journal = pathlib.Path(config['stateRoot']) / (plan['id'] + '.json')
    state = json.loads(recovery.recovery.private_file(journal).read_text())
    if state.get('id') != plan['id'] or state.get('revision') != plan['revision']: raise MigrationError('Journal de migration incohérent.')
    if state.get('rolledBack'): return {'rolledBack': True, 'sourcePreserved': True, 'databaseWritesReversible': False}
    resumed = state.get('rollbackStarted') is True
    if state.get('sandboxDirectory'):
        sandbox_root=recovery.canonical(state['sandboxDirectory'],True);sandbox_journal=sandbox_root/'sandbox-state.json'
        if sandbox_journal.exists(): recovery.cleanup_sandbox(json.loads(recovery.recovery.private_file(sandbox_journal).read_text()),recovery.Docker(state['sandboxSocket']));sandbox_journal.unlink()
    present_volumes = set(docker(['volume','ls','--format','{{.Name}}']).split())
    present_containers = set(docker(['ps','-a','--format','{{.Names}}']).split())
    recovered_holders=set(state.get('containers',[]))|set(docker(['ps','-a','--format','{{.Names}}','--filter','label=kolab.project-migration='+plan['id']]).split())
    for name in recovered_holders:
        if name not in present_containers:continue
        info=json.loads(docker(['inspect',name]))[0]
        if not name.startswith('kolab-migration-'+plan['id']+'-') or info.get('Config',{}).get('Labels',{}).get('kolab.project-migration')!=plan['id'] or info.get('State',{}).get('Running'):
            raise MigrationError('Un auxiliaire de copie est actif ou étranger ; réconciliation manuelle requise.')
    state['containers']=sorted(recovered_holders)
    for row in state['databases']:
        target = pathlib.Path(row['path'])
        if target != pathlib.Path(config['dataRoot']) / 'instances' / row['id'] or target.resolve() != target: raise MigrationError('Répertoire de retour arrière étranger.')
        if not target.exists() and (resumed or not row['sha256']): continue
        target = recovery.canonical(target, True)
        if row['sha256'] and digest_value({name: [item[0], item[3]] for name, item in recovery.tree_state(target).items()}) != row['sha256']: raise MigrationError('La base importée a changé ; retour arrière automatique refusé.')
    for row in state['storageManifests']:
        if pathlib.Path(row['path'])not in {pathlib.Path(config['webRoot'])/'storage'/(item['targetId']+'.json')for item in plan['applications']}:raise MigrationError('Manifeste de copie étranger.')
        if not pathlib.Path(row['path']).exists() and (resumed or not row.get('created',True)): continue
        if recovery.digest(row['path']) != row['sha256']: raise MigrationError('Le stockage a été modifié ; retour arrière refusé.')
    for row in state['volumes']:
        if row['name'] not in present_volumes:
            if resumed or not row.get('created',True): continue
            raise MigrationError('Un volume de copie a disparu ; revue manuelle requise.')
        info = json.loads(docker(['volume','inspect',row['name']]))[0]
        if info.get('Labels',{}).get('kolab.project-migration') != plan['id'] or info.get('Driver') != 'local' or info.get('Options'): raise MigrationError('Volume de copie étranger.')
        if set(docker(['ps','-a','--format','{{.Names}}','--filter','volume='+row['name']]).split())-recovered_holders: raise MigrationError('Un volume importé est utilisé ; retour arrière refusé.')
        if row.get('sha256') and volume_digest(docker,row['name'],config['helperImage'],plan['id']) != row['sha256']: raise MigrationError('Un volume contient de nouvelles écritures ; retour arrière refusé.')
    # Check owned immutable images before deleting any metadata or files.
    # Include a commit whose HTTP response/journal save was interrupted.
    candidate_images = set(image for services in state.get('images',{}).values() for image in services.values())
    candidate_images.update(docker(['image','ls','-q','--no-trunc','--filter','label=kolab.project-migration='+plan['id']]).split())
    owned_images = []
    for image in candidate_images:
        try: info=json.loads(docker(['image','inspect',image]))[0]
        except subprocess.CalledProcessError:
            if resumed: continue
            raise MigrationError('Une image importée a disparu ; revue manuelle requise.')
        labels=info.get('Config',{}).get('Labels',{})
        if labels.get('kolab.project-migration')!=plan['id'] or labels.get('pockethost.web.app')not in {row['targetId']for row in plan['applications']}: raise MigrationError('Image de retour arrière étrangère.')
        if docker(['ps','-aq','--filter','ancestor='+image]).strip(): raise MigrationError('Une image importée est utilisée ; retour arrière refusé.')
        owned_images.append(image)
    if state.get('memoryCommitments') and not state.get('memoryRemoved'):
        from database_memory_management import policy_lock
        from database_memory_reservations import POLICY, load_policy, MIB
        with policy_lock():
            policy=load_policy()
            for ident,amount in state['memoryCommitments'].items():
                if ident in policy and policy[ident] != amount*MIB or ident not in policy and state.get('memoryApplied') and not state.get('memoryRemovalStarted'): raise MigrationError('Un engagement RAM a changé ; retour arrière refusé avant tout retrait.')
    # No controller state change when a disk/policy validation above fails.
    control.call('rollback',{'id':plan['id'],'revision':plan['revision']})
    state['rollbackStarted']=True;private_json(journal,state)
    if state.get('memoryCommitments') and not state.get('memoryRemoved'):
        from platform_memory_allocator import atomic
        with policy_lock():
            policy=load_policy()
            for ident,amount in state['memoryCommitments'].items():
                if ident in policy and policy[ident]!=amount*MIB: raise MigrationError('Un engagement RAM a changé ; aucun artefact retiré.')
            state['memoryRemovalStarted']=True;private_json(journal,state)
            atomic(POLICY,{'version':1,'databases':{key:value//MIB for key,value in policy.items()if key not in state['memoryCommitments']}},0o644)
        state['memoryRemoved']=True;private_json(journal,state)
    for name in reversed(state.get('containers',[])):
        if not name.startswith('kolab-migration-'+plan['id']+'-'): raise MigrationError('Conteneur de copie étranger.')
        if name not in present_containers: continue
        info=json.loads(docker(['inspect',name]))[0]
        if info.get('Config',{}).get('Labels',{}).get('kolab.project-migration')!=plan['id']: raise MigrationError('Étiquette de copie différente.')
        docker(['rm','-f',name])
    for row in state['volumes']:
        if row['name'] in present_volumes: docker(['volume','rm',row['name']])
    for row in state['databases']:
        if pathlib.Path(row['path']).exists(): shutil.rmtree(row['path'])
    for row in state['storageManifests']: pathlib.Path(row['path']).unlink(missing_ok=True)
    for image in owned_images: docker(['image','rm','--no-prune',image])
    control.call('rolled-back',{'id':plan['id'],'revision':plan['revision']})
    state['rolledBack']=True;private_json(journal,state)
    return {'rolledBack':True,'sourcePreserved':True,'databaseWritesReversible':False}


def apply_import(directory, plan, config, control, docker, helper_image):
    with migration_lock(plan,config): return _apply_import(directory,plan,config,control,docker,helper_image)


def rollback_import(plan, config, control, docker):
    with migration_lock(plan,config):
        remote=control.call('state',{'id':plan['id'],'revision':plan['revision']})['item']
        if remote['status']=='rolled_back':return {'rolledBack':True,'sourcePreserved':True,'databaseWritesReversible':False}
        if remote['status']=='importing':
            # Flock proves no cooperative CLI is still executing this import on
            # this target. Orphaned copies can be reconciled after SIGKILL.
            control.call('fail',{'id':plan['id'],'revision':plan['revision']})
        journal=pathlib.Path(config['stateRoot'])/(plan['id']+'.json')
        if not journal.exists():
            # No filesystem mutation precedes the first durable journal.
            private_json(journal,{'version':1,'id':plan['id'],'revision':plan['revision'],'databases':[],'volumes':[],'containers':[],'images':{},'storageManifests':[]})
        return _rollback_import(plan,config,control,docker)


class Cloudflare:
    """Fixed official API origin only, no redirect/proxy, no token in arguments/output."""
    def __init__(self, tokenfile):
        self.token = recovery.recovery.private_file(tokenfile).read_text().strip()
        if not self.token or len(self.token) > 500: raise MigrationError('Jeton Cloudflare privé requis.')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs): return None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    def call(self, method, path, body=None):
        if not path.startswith('/zones') or '\\' in path or '..' in path: raise MigrationError('Route DNS invalide.')
        request = urllib.request.Request('https://api.cloudflare.com/client/v4' + path, method=method, data=None if body is None else recovery.encoded(body), headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        with self.opener.open(request, timeout=15) as response:
            raw = response.read(1024**2 + 1)
            if len(raw) > 1024**2: raise MigrationError('Réponse DNS trop volumineuse.')
            value = json.loads(raw)
            if value.get('success') is not True: raise MigrationError('Cloudflare a refusé le changement DNS.')
            return value['result']
    def zone(self, host):
        for start in range(0, host.count('.')):
            name = '.'.join(host.split('.')[start:]); rows = self.call('GET', '/zones?' + urllib.parse.urlencode({'name': name, 'status': 'active', 'per_page': 50}))
            exact = [row for row in rows if row.get('name') == name]
            if len(exact) == 1: return exact[0]['id']
        raise MigrationError('Zone Cloudflare introuvable ou ambiguë.')


def dns_record(row):
    return {key: row.get(key) for key in ('id', 'type', 'name', 'content', 'proxied', 'ttl')} | {'comment': row.get('comment') or '', 'tags': row.get('tags') or [], 'settings': row.get('settings') or {}}


def dns_preview(plan, client):
    changes = []
    for domain in plan['domains']:
        host = domain['host']
        if not HOST.fullmatch(host) or host in [row['host'] for row in changes]: raise MigrationError('Domaine de bascule invalide ou dupliqué.')
        zone = client.zone(host); rows = client.call('GET', '/zones/' + zone + '/dns_records?' + urllib.parse.urlencode({'name': host, 'per_page': 100}))
        addresses = [row for row in rows if row.get('type') in ('A', 'AAAA', 'CNAME')]
        primary = [row for row in addresses if row.get('type') in ('A', 'CNAME')]
        if len(primary) > 1 or len(addresses) > 10: raise MigrationError('Plusieurs origines DNS : revue manuelle requise.')
        changes.append({'host': host, 'zoneId': zone, 'before': [dns_record(row) for row in addresses], 'after': {'type': 'A', 'name': host, 'content': FLEET[plan['target']]['address'], 'proxied': True, 'ttl': 1, 'comment': 'Kolab migration ' + plan['id'] + ' ' + plan['revision'][:16]}})
    preview = {'version': 1, 'migrationId': plan['id'], 'planRevision': plan['revision'], 'source': plan['source'], 'target': plan['target'], 'changes': changes, 'createdAt': epoch(), 'expiresAt': epoch() + 900, 'requiresSourceWritesPaused': True, 'requiresSourceCronsPaused': True, 'requiresTargetVerified': True, 'databaseWritesReversible': False}
    preview['revision'] = digest_value(preview)
    return preview


def dns_cutover(preview, control, client, journal_path, approval):
    if preview.get('revision') != digest_value({key: val for key, val in preview.items() if key != 'revision'}) or preview.get('expiresAt', 0) < epoch() or approval != preview['revision']: raise MigrationError('Aperçu DNS expiré ou non approuvé.')
    control.call('dns-ready', {'id': preview['migrationId'], 'revision': preview['planRevision'], 'dnsRevision': preview['revision']})
    journal_path = pathlib.Path(journal_path)
    if journal_path.exists(): raise MigrationError('Un journal DNS existe déjà.')
    journal = {'version': 1, 'migrationId': preview['migrationId'], 'planRevision': preview['planRevision'], 'dnsRevision': preview['revision'], 'changes': [], 'complete': False}
    private_json(journal_path, journal)
    try:
        for change in preview['changes']:
            zone, host = change['zoneId'], change['host']
            current = client.call('GET', '/zones/' + zone + '/dns_records?' + urllib.parse.urlencode({'name': host, 'per_page': 100}))
            observed = sorted([dns_record(row) for row in current if row.get('type') in ('A', 'AAAA', 'CNAME')], key=lambda row: row['id'])
            if observed != sorted(change['before'], key=lambda row: row['id']): raise MigrationError('Le DNS a changé depuis l’aperçu ; aucune écriture supplémentaire.')
            primary = next((row for row in observed if row['type'] in ('A', 'CNAME')), None)
            # Intent and existing IDs are durable BEFORE a remote mutation. A lost
            # HTTP response can be reconciled by exact before/after values.
            applied = {'zoneId': zone, 'host': host, 'before': observed, 'after': change['after'], 'primaryId': primary['id'] if primary else '', 'primaryIntent': True, 'aaaaIntents': [row for row in observed if row['type'] == 'AAAA'], 'primaryRestored': False, 'restoredAaaa': []}
            journal['changes'].append(applied); private_json(journal_path, journal)
            result = client.call('PATCH', '/zones/' + zone + '/dns_records/' + primary['id'], change['after']) if primary else client.call('POST', '/zones/' + zone + '/dns_records', change['after'])
            if {key: result.get(key) for key in change['after']} != change['after']: raise MigrationError('Cloudflare ne confirme pas les valeurs prévues.')
            applied['primaryId'] = result['id']; private_json(journal_path, journal)
            for row in applied['aaaaIntents']: client.call('DELETE', '/zones/' + zone + '/dns_records/' + row['id'])
        journal['complete'] = True; private_json(journal_path, journal)
        control.call('dns-result', {'id': preview['migrationId'], 'revision': preview['planRevision'], 'dnsRevision': preview['revision'], 'status': 'cutover'})
        return {'dnsChanged': True, 'sourcePreserved': True, 'databaseWritesReversible': False}
    except Exception:
        journal['needsRollback'] = True; private_json(journal_path, journal)
        try: dns_rollback(journal_path, client)
        except Exception: pass  # journal retained; never overwrite a conflicting external edit
        raise


def dns_rollback(journal_path, client):
    journal_path = recovery.recovery.private_file(journal_path); journal = json.loads(journal_path.read_text())
    if journal.get('rolledBack') is True: return {'dnsRolledBack': True, 'databaseWritesReversible': False, 'sourcePreserved': True}
    for change in reversed(journal['changes']):
        zone, ident = change['zoneId'], change.get('primaryId')
        path = '/zones/' + zone + '/dns_records'
        rows = client.call('GET', path + '?' + urllib.parse.urlencode({'name': change['host'], 'per_page': 100}))
        addresses = [dns_record(row) for row in rows if row.get('type') in ('A', 'AAAA', 'CNAME')]
        previous = next((row for row in change['before'] if row['type'] in ('A', 'CNAME')), None)
        if not change.get('primaryRestored'):
            if ident:
                current = next((row for row in addresses if row['id'] == ident), None)
            else:
                # Ambiguous POST: only one exact newly-created value is ours.
                candidates = [row for row in addresses if {key: row.get(key) for key in change['after']} == change['after'] and row['id'] not in {old['id'] for old in change['before']}]
                if len(candidates) > 1: raise MigrationError('Création DNS ambiguë ; revue manuelle requise.')
                current = candidates[0] if candidates else None
                ident = current['id'] if current else ''
                if ident: change['primaryId'] = ident; private_json(journal_path, journal)
            if previous and current and dns_record(current) == previous:
                pass  # PUT did not apply, or a prior rollback response was lost
            elif current is None and previous:
                raise MigrationError('L’origine DNS précédente a disparu ; revue manuelle requise.')
            elif current:
                if {key: current.get(key) for key in change['after']} != change['after']: raise MigrationError('Le DNS a changé depuis la bascule ; retour arrière automatique refusé.')
                if previous: client.call('PATCH', path + '/' + ident, {key: val for key, val in previous.items() if key not in ('id','tags','settings')})
                else: client.call('DELETE', path + '/' + ident)
            elif any(row['type'] in ('A', 'CNAME') for row in addresses):
                raise MigrationError('Une autre origine DNS a été créée ; revue manuelle requise.')
            change['primaryRestored'] = True; private_json(journal_path, journal)
        # An earlier rollback may have restored the origin before an IPv6
        # response was lost. Recheck it on retry before adding any address.
        rows = client.call('GET', path + '?' + urllib.parse.urlencode({'name': change['host'], 'per_page': 100}))
        primary_rows = [dns_record(row) for row in rows if row.get('type') in ('A','CNAME')]
        if (previous and primary_rows != [previous]) or (not previous and primary_rows):
            raise MigrationError('L’origine DNS restaurée a changé ; reprise du retour arrière refusée.')
        for before in change.get('aaaaIntents', []):
            rows = client.call('GET', path + '?' + urllib.parse.urlencode({'name': change['host'], 'per_page': 100}))
            same = [row for row in rows if row.get('type') == 'AAAA' and all(row.get(key) == value for key, value in before.items() if key != 'id')]
            conflicts = [row for row in rows if row.get('id') == before['id'] and dns_record(row) != before]
            if conflicts: raise MigrationError('Une adresse IPv6 a été modifiée ; revue manuelle requise.')
            if not same: client.call('POST', path, {key: val for key, val in before.items() if key != 'id'})
            if before['id'] not in change['restoredAaaa']: change['restoredAaaa'].append(before['id']); private_json(journal_path, journal)
        change['rolledBack'] = True; private_json(journal_path, journal)
    journal['rolledBack'] = True; journal['needsRollback'] = False; private_json(journal_path, journal)
    return {'dnsRolledBack': True, 'databaseWritesReversible': False, 'sourcePreserved': True}


def main():
    if os.geteuid()!=0:raise MigrationError('Une session opérateur root est requise pour le CLI de migration.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control-key', default=str(PRIVATE)); parser.add_argument('--control-port', type=int, default=8091)
    commands = parser.add_subparsers(dest='action', required=True)
    export = commands.add_parser('export'); export.add_argument('--project-id',required=True);export.add_argument('--revision',required=True);export.add_argument('--config',required=True);export.add_argument('--output',required=True);export.add_argument('--source-writes-paused-confirmation')
    plan = commands.add_parser('plan'); plan.add_argument('--directory', required=True); plan.add_argument('--target', choices=FLEET, required=True); plan.add_argument('--owner-id', required=True); plan.add_argument('--archive-sha256', required=True); plan.add_argument('--project-name'); plan.add_argument('--overrides'); plan.add_argument('--output', required=True); plan.add_argument('--submit', action='store_true')
    for name in ('import', 'rollback'):
        sub = commands.add_parser(name); sub.add_argument('--plan', required=True); sub.add_argument('--config', required=True)
        if name == 'import': sub.add_argument('--directory', required=True)
    dns = commands.add_parser('dns-preview'); dns.add_argument('--plan', required=True); dns.add_argument('--cloudflare-token-file', required=True); dns.add_argument('--output', required=True);dns.add_argument('--submit',action='store_true')
    cutover = commands.add_parser('dns-cutover'); cutover.add_argument('--preview', required=True); cutover.add_argument('--approval', required=True); cutover.add_argument('--journal', required=True); cutover.add_argument('--cloudflare-token-file', required=True)
    rollback = commands.add_parser('dns-rollback'); rollback.add_argument('--journal', required=True); rollback.add_argument('--cloudflare-token-file', required=True)
    arguments = parser.parse_args()
    if arguments.action == 'export':
        checked_id(arguments.project_id)
        if not SHA.fullmatch(arguments.revision):raise MigrationError('Révision source requise.')
        config=json.loads(recovery.recovery.private_file(arguments.config).read_text());output=pathlib.Path(arguments.output).absolute()
        if output.exists()or output.resolve()!=output:raise MigrationError('Un répertoire d’export vierge est requis.')
        output.mkdir(mode=0o700)
        key=output/'capsule.key'
        with open(key,'x')as stream:os.fchmod(stream.fileno(),0o600);stream.write(secrets.token_hex(32))
        approval=arguments.source_writes_paused_confirmation
        if approval and approval!=arguments.project_id:raise MigrationError('Confirmez l’identifiant exact du projet dont les écritures sont gelées.')
        selected=Control(arguments.control_key,arguments.control_port).call('export-snapshot',{'projectId':arguments.project_id,'revision':arguments.revision,**({'confirmation':approval,'sourceWritesPaused':True,'sourceCronsPaused':True,'sourceWorkersPaused':True}if approval else{})})['snapshot']
        result=recovery.capture(selected,config['dataRoot'],config['binaryRoot'],output/'project.phpr',key,recovery.Docker(config['dockerSocket']),config.get('instanceAppRoot'))
        private_json(output/'transfer.json',{'version':1,'projectId':selected['projectId'],'sourceRevision':selected['revision'],'archiveSha256':result['sha256'],'archiveBytes':result['archiveBytes'],'capsuleKeyScope':'this-project-only','ephemeralKey':True})
        result={'exported':True,'sourcePreserved':True,'archiveSha256':result['sha256'],'archiveBytes':result['archiveBytes'],'ephemeralKey':True,'instructions':'Transférer uniquement project.phpr, capsule.key et transfer.json par SSH opérateur ; ne jamais transférer une clé de serveur.'}
    elif arguments.action == 'plan':
        override = json.loads(recovery.recovery.private_file(arguments.overrides).read_text()) if arguments.overrides else None
        value = build_plan(arguments.directory, arguments.target, arguments.owner_id, arguments.archive_sha256, arguments.project_name, override)
        private_json(arguments.output, value)
        if arguments.submit: Control(arguments.control_key, arguments.control_port).call('prepare', {'plan': value})
        result = public_plan(value)
    elif arguments.action in ('import', 'rollback'):
        plan = json.loads(recovery.recovery.private_file(arguments.plan).read_text()); config = json.loads(recovery.recovery.private_file(arguments.config).read_text()); docker = recovery.Docker(config['dockerSocket']); control = Control(arguments.control_key, arguments.control_port)
        result = apply_import(arguments.directory, plan, config, control, docker, config['helperImage']) if arguments.action == 'import' else rollback_import(plan, config, control, docker)
    elif arguments.action == 'dns-preview':
        plan = json.loads(recovery.recovery.private_file(arguments.plan).read_text()); result = dns_preview(plan, Cloudflare(arguments.cloudflare_token_file)); private_json(arguments.output, result)
        if arguments.submit:Control(arguments.control_key,arguments.control_port).call('dns-preview',{'id':plan['id'],'revision':plan['revision'],'preview':result})
    elif arguments.action == 'dns-cutover': result = dns_cutover(json.loads(recovery.recovery.private_file(arguments.preview).read_text()), Control(arguments.control_key, arguments.control_port), Cloudflare(arguments.cloudflare_token_file), arguments.journal, arguments.approval)
    else: result = dns_rollback(arguments.journal, Cloudflare(arguments.cloudflare_token_file))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try: main()
    except Exception:
        print(json.dumps({'status': 'failed', 'errorCode': 'project_migration_refused', 'message': 'La migration a été refusée. Consultez le plan et le journal privés ; aucune donnée source n’est modifiée.'})); sys.exit(1)
