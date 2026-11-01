#!/usr/bin/env python3
"""Privileged recovery runner; browser-facing mode exposes only filtered control metadata.

Operator commands (same private --config): captures, hold, release, record-drill, sweep.
"""
import argparse
import datetime
import fcntl
import json
import os
import pathlib
import queue
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import web_platform_recovery as engine
import web_platform_recovery_control as control

STOP = threading.Event()
PLAN_CHECK = {}
EVENT_PATH = '/api/web-apps/_platform/recovery-events'
EVENT_ATTEMPTS = 5
PLATFORM = re.compile(r'^[a-z0-9][a-z0-9-]{0,40}$')


class VerificationFailed(engine.RecoveryError):
    pass


STAGES = {'prepare': 'Préparation des sauvegardes', 'capture': 'Capture chiffrée et envoi S3', 'verify': 'Vérification de la capture', 'drill': 'Reconstruction isolée et suspendue', 'retention': 'Application de la rétention', 'complete': 'Terminée'}


def config_file(filename):
    try: value = json.loads(engine.private_file(filename).read_bytes())
    except Exception: raise engine.RecoveryError('Configuration privée du service invalide.') from None
    required = ['stateDir', 'planFile', 'keyFile', 'drillRoot', 'controlUrl', 'superuserIdentity', 'superuserPasswordFile']
    if not isinstance(value, dict): raise engine.RecoveryError('Configuration fixe de reprise incomplète.')
    value['platform'] = value.get('platform', 'app1')
    if value.get('version') != 1 or not isinstance(value['platform'], str) or not PLATFORM.fullmatch(value['platform']) or any(not isinstance(value.get(name), str) or not value[name] for name in required): raise engine.RecoveryError('Configuration fixe de reprise incomplète.')
    for name in ['stateDir', 'planFile', 'keyFile', 'drillRoot', 'superuserPasswordFile']:
        path = pathlib.Path(value[name])
        if not path.is_absolute() or path.resolve() != path: raise engine.RecoveryError('Chemin privé de reprise non canonique.')
    url = urllib.parse.urlsplit(value['controlUrl'])
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', '::1') or not url.port or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
        raise engine.RecoveryError('Le service exige le contrôleur local sur loopback.')
    selection = value.get('drillSelect', ['controller'])
    if not isinstance(selection, list) or not selection or len(selection) > 1000 or any(item != 'controller' and not re.fullmatch('[a-z0-9]{15}', item or '') for item in selection): raise engine.RecoveryError('Sélection de test fixe invalide.')
    if value.get('adminUserId') and not re.fullmatch('[a-z0-9]{15}', value['adminUserId']): raise engine.RecoveryError('Administrateur technique invalide.')
    value['drillSelect'] = list(dict.fromkeys(selection))
    for name, default in [('executionEnabled', True), ('drillVerify', True), ('drillCleanup', True), ('sweepEnabled', True), ('planFromInventory', False), ('protectCustomerBackups', True)]:
        value[name] = value.get(name, default)
        if type(value[name]) is not bool: raise engine.RecoveryError('Verrou privé de reprise invalide.')
    for name, default, low, high in [('drillMinFreeGiB', 15, 15, 1024), ('maxDrillBytes', 2 * 1024**3, 1, 512 * 1024**3), ('preflightTimeoutSeconds', 3600, 60, 14400), ('captureTimeoutSeconds', 14400, 60, 86400), ('sweepMinAgeHours', 48, 6, 720)]:
        value[name] = value.get(name, default)
        if type(value[name]) is not int or not low <= value[name] <= high: raise engine.RecoveryError('Limite fixe de reprise invalide.')
    if value['drillVerify'] and 'controller' not in value['drillSelect']: raise engine.RecoveryError('Le contrôle des tests exige le contrôleur dans drillSelect.')
    if value.get('dataRoot') is not None and (not isinstance(value['dataRoot'], str) or not pathlib.Path(value['dataRoot']).is_absolute() or pathlib.Path(value['dataRoot']).resolve() != pathlib.Path(value['dataRoot'])): raise engine.RecoveryError('Chemin privé de reprise non canonique.')
    events = value.get('failureEvents', {})
    if not isinstance(events, dict) or set(events) - {'enabled', 'path', 'secretFile'} or type(events.get('enabled', False)) is not bool or not re.fullmatch(r'/api/[A-Za-z0-9_/-]{1,200}', events.get('path', EVENT_PATH)) or '..' in events.get('path', '') or not isinstance(events.get('secretFile', ''), str):
        raise engine.RecoveryError('Notification d’échec de reprise invalide.')
    value['failureEvents'] = {'enabled': events.get('enabled', False), 'path': events.get('path', EVENT_PATH), 'secretFile': events.get('secretFile', '')}
    if value['failureEvents']['secretFile']:
        secret_path = pathlib.Path(value['failureEvents']['secretFile'])
        if not secret_path.is_absolute() or secret_path.resolve() != secret_path: raise engine.RecoveryError('Chemin privé de reprise non canonique.')
        engine.private_file(secret_path)
    plan = engine.load_plan(value['planFile']); engine.load_key(value['keyFile']); engine.private_file(value['superuserPasswordFile'])
    if plan['platform'] != value['platform']: raise engine.RecoveryError('Le plan privé décrit une autre plateforme que ce service.')
    os.environ['WEB_PLATFORM_RECOVERY_STATE_DIR'] = value['stateDir']; control.state_root()
    os.environ.setdefault('WEB_PLATFORM_RECOVERY_PREFIX', value['platform'] + '/platform-recovery')
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


class Controller:
    def __init__(self, config):
        self.config, self.super_token, self.token = config, '', ''
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    def request(self, path, body=None, method=None, superuser=False, anonymous=False):
        if not path.startswith('/api/') or '://' in path: raise engine.RecoveryError('Route locale de préparation invalide.')
        token = '' if anonymous else self.super_token if superuser else self.token
        request = urllib.request.Request(self.config['controlUrl'].rstrip('/') + path, data=None if body is None else json.dumps(body).encode(), method=method or ('POST' if body is not None else 'GET'), headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {})})
        try:
            with self.opener.open(request, timeout=30) as response:
                if not 200 <= response.status < 300: raise engine.RecoveryError('Le contrôleur refuse la préparation des sauvegardes.')
                raw = response.read(4 * 1024**2 + 1)
                if len(raw) > 4 * 1024**2: raise engine.RecoveryError('Réponse du contrôleur trop volumineuse.')
                return json.loads(raw) if raw else {}
        except Exception: raise engine.RecoveryError('Le contrôleur local ne confirme pas les sauvegardes nécessaires.') from None
    def authenticate(self):
        password = engine.private_file(self.config['superuserPasswordFile']).read_text().strip()
        if not password or len(password) > 1000: raise engine.RecoveryError('Identité technique privée invalide.')
        auth = self.request('/api/collections/_superusers/auth-with-password', {'identity': self.config['superuserIdentity'], 'password': password}, anonymous=True)
        self.super_token = auth.get('token', '')
        if not self.super_token: raise engine.RecoveryError('Authentification technique du contrôleur impossible.')
        actor = self.config.get('adminUserId')
        if not actor:
            users = self.records('users', 'superAdmin=true', fields='id,superAdmin')
            if not users: raise engine.RecoveryError('Aucun administrateur dashboard ne peut autoriser la préparation.')
            actor = users[0]['id']
        auth = self.request('/api/collections/users/impersonate/' + actor, {'duration': 3600}, superuser=True)
        if not auth.get('token') or auth.get('record', {}).get('superAdmin') is not True: raise engine.RecoveryError('Le compte dashboard technique n’est pas administrateur.')
        self.token = auth['token']
    def records(self, collection, filter='', fields=''):
        if not re.fullmatch('[A-Za-z0-9_]+', collection): raise engine.RecoveryError('Collection de préparation invalide.')
        result, page = [], 1
        while page <= 1000:
            query = urllib.parse.urlencode({'page': page, 'perPage': 100, 'filter': filter, **({'fields': fields} if fields else {})})
            response = self.request('/api/collections/' + collection + '/records?' + query, superuser=True)
            result.extend(response.get('items', []))
            if page >= response.get('totalPages', 1): return result
            page += 1
        raise engine.RecoveryError('Inventaire de préparation trop volumineux.')


def heartbeat(config, online=True):
    enabled = config.get('executionEnabled', True)
    control.atomic_json(control.state_root() / 'worker.json', {'configured': True, 'online': online, 'lastSeenAt': control.stamp(), 'executionEnabled': enabled, 'capabilities': {'canCapture': enabled, 'canDrill': enabled}, 'drillSelection': config['drillSelect'], 'platform': config.get('platform', 'app1'), 'planCheck': dict(PLAN_CHECK)})


def update(run, stage=None, **values):
    if stage:
        run['stageCode'] = stage; run['stage'] = STAGES[stage]
    run.update(values)
    # Holds are written by operator commands under the state lock: the stored copy wins.
    try: stored = control.read_json(control.state_root() / 'runs' / (run['id'] + '.json'))
    except Exception: stored = {}
    if isinstance(stored, dict) and isinstance(stored.get('holds'), list): run['holds'] = stored['holds']
    control.save_run(run)


def mutate_run(ident, change):
    """Fresh read-modify-write of one run under the state lock (never call while holding it)."""
    with control.state_lock():
        row = next((value for value in control.all_runs() if value['id'] == ident), None)
        if row is not None and change(row) is not False: control.save_run(row)
        return row


def poll(config, run, predicate, deadline):
    while time.monotonic() < deadline:
        if STOP.is_set(): raise engine.RecoveryError('Service de reprise interrompu.')
        heartbeat(config)
        value = predicate()
        if value is not None: return value
        time.sleep(2)
    raise engine.RecoveryError('Les sauvegardes nécessaires ne sont pas prêtes dans le délai prévu.')


PLATFORM_BACKUPS = 'platform-web-backups.json'


def platform_backup_ids():
    value = control.read_json(control.state_root() / PLATFORM_BACKUPS, {}) or {}
    return set(value.get('ids', [])) if isinstance(value, dict) and isinstance(value.get('ids'), list) else set()


def remember_platform_backup(backup_id):
    # Only IDs this service created; the dashboard actor may be a real administrator.
    with control.state_lock():
        ids = [item for item in (control.read_json(control.state_root() / PLATFORM_BACKUPS, {}) or {}).get('ids', []) if isinstance(item, str) and item != backup_id][-4999:]
        control.atomic_json(control.state_root() / PLATFORM_BACKUPS, {'version': 1, 'ids': [*ids, backup_id]})


def web_backup_choice(ready, retain, platform_ids, protect=True, excluded=lambda: False):
    """None: create a new archive. Otherwise the ready archive to reuse, because an older
    controller's per-app retention (every ready row counts) would evict a backup this
    service did not create: customers' manual or scheduled archives are never pushed out.
    Rows stored with trigger 'platform' are outside the per-app count; once the controller
    says so (`excluded()`, asked only when it changes the outcome) a fresh archive is always made."""
    ready = sorted((row for row in ready if row.get('trigger') != 'platform'), key=lambda row: (row.get('created') or '', row.get('id') or ''), reverse=True)
    evicted = ready[max(1, retain) - 1:]
    if not protect or all(row.get('id') in platform_ids for row in evicted) or excluded(): return None
    reusable = [row for row in ready if row.get('s3State') == 'ready']
    if not reusable: raise engine.RecoveryError('Une nouvelle sauvegarde évincerait une sauvegarde client et aucune archive existante n’est copiée sur S3.')
    return reusable[0]


def controller_excludes_platform(client, app_id):
    """True when the controller keeps trigger=platform archives out of the per-app retention
    (capability published by GET /api/web-apps/{id}/backups). Then a fresh platform archive never
    evicts a customer backup and must always be created: reusing the newest customer archive would
    capture yesterday's code. An older controller, or no answer, keeps the protection."""
    try: return client.request(f'/api/web-apps/{app_id}/backups').get('platformRetention') == 'excluded'
    except Exception: return False


def prepare_plan(config, run):
    plan = engine.load_plan(config['planFile'])
    if config.get('planFromInventory'):
        import web_platform_recovery_plan as recovery_plan  # Lazy: API mode never needs it.
        # Instances created or deleted since the private plan was written no longer break captures.
        plan, _report = recovery_plan.build_plan(plan, recovery_plan.data_root_of(plan, config.get('dataRoot')), allow_without_database=True)
    client = Controller(config); client.authenticate()
    # The engine assigns its final capture ID; this intermediate ID is never used as a path input.
    plan.pop('backupId', None)
    # Exactly the worker's WEB_BACKUPS_S3_PREFIX (no default): a derived value would not match the
    # stored s3Object keys of another directory and every capture would stop on 'Référence S3 source invalide'.
    references = list(plan.get('s3Refs', [])); prefix = os.environ.get('WEB_BACKUPS_S3_PREFIX', '')
    if prefix and not re.fullmatch('[A-Za-z0-9][A-Za-z0-9/_-]{0,199}', prefix): raise engine.RecoveryError('Préfixe des sauvegardes web invalide.')
    apps, page = [], 1
    while True:
        response = client.request('/api/web-apps?' + urllib.parse.urlencode({'page': page})); apps.extend(response.get('items', []))
        if not response.get('hasMore'): break
        page += 1
        if page > 1000: raise engine.RecoveryError('Trop de sites à préparer.')
    memberships = {row['appId']: row for row in client.records('web_app_environments', fields='appId,environment') if row.get('appId')}
    apps = [app for app in apps if app.get('activeDeployment') and memberships.get(app['id'], {}).get('environment') != 'preview']
    if apps and not prefix: raise engine.RecoveryError('WEB_BACKUPS_S3_PREFIX est requis dans l’environnement du service : la même valeur que le worker web de cet hôte.')
    deadline = time.monotonic() + config['preflightTimeoutSeconds']; completed = 0
    retention = {row['appId']: row.get('retention') for row in client.records('web_app_backup_settings', fields='appId,retention') if row.get('appId')} if apps else {}
    platform_ids, summary, capability = platform_backup_ids(), {'created': 0, 'reused': []}, {}
    for app in apps:
        app_id = app['id']
        if not re.fullmatch('[a-z0-9]{15}', app_id): raise engine.RecoveryError('Application de préparation invalide.')
        update(run, 'prepare', progress={'completed': completed, 'total': max(1, len(apps)), 'unit': 'items'})
        current = client.records('web_app_backups', f'appId="{app_id}" && (status="queued" || status="running")', fields='id,appId,status')
        if current:
            pending_id = current[0]['id']
        else:
            ready = client.records('web_app_backups', f'appId="{app_id}" && status="ready"', fields='id,created,s3State,sourceDeploymentId,trigger')
            retain = retention.get(app_id) if type(retention.get(app_id)) is int and retention.get(app_id) > 0 else 3
            def excluded():
                # Controller-wide capability: asked once per run, only when it changes the choice.
                if 'excluded' not in capability: capability['excluded'] = controller_excludes_platform(client, app_id)
                return capability['excluded']
            reused = web_backup_choice(ready, retain, platform_ids, config.get('protectCustomerBackups', True), excluded)
            if reused:
                pending_id = reused['id']; summary['reused'].append({'appId': app_id, 'backupId': pending_id, 'stale': reused.get('sourceDeploymentId') != app['activeDeployment']})
            else:
                # 'platform' is ignored by controllers that predate the retention exclusion (still 'manual').
                created = client.request(f'/api/web-apps/{app_id}/backups', {'trigger': 'platform'}); pending_id = created['backup']['id']
                remember_platform_backup(pending_id); platform_ids.add(pending_id); summary['created'] += 1
        def backup_ready():
            row = client.request('/api/collections/web_app_backups/records/' + pending_id, superuser=True)
            if row.get('status') == 'failed' or row.get('status') == 'ready' and row.get('s3State') not in ('ready', 'uploading', 'pending'): raise engine.RecoveryError('Une sauvegarde d’image ou sa copie S3 a échoué.')
            return row if row.get('status') == 'ready' and row.get('s3State') == 'ready' else None
        poll(config, run, backup_ready, deadline)
        backups = client.records('web_app_backups', f'appId="{app_id}" && status="ready" && s3State="ready"', fields='id,appId,imageId,runtimeImages,s3Object,s3ImageObject,s3ImageObjects,finishedAt,created')
        for backup in backups:
            date = backup.get('finishedAt') or backup.get('created')
            if backup.get('s3Object'): references.append({'key': backup['s3Object'], 'prefix': prefix, 'group': 'web', 'appId': app_id, 'kind': 'archive', 'required': backup['id'] == pending_id, 'snapshotAt': date, 'metadata': {'app-id': app_id, 'backup-id': backup['id']}})
            images = backup.get('runtimeImages') or {}
            image_objects = backup.get('s3ImageObjects') or {}
            if backup.get('imageId') and backup.get('s3ImageObject'): image_objects = {**image_objects, backup['imageId']: backup['s3ImageObject']}
            for digest, object_key in image_objects.items():
                # New handlers use digest->object; tolerate service->object paired with runtimeImages.
                image_id = digest if re.fullmatch('sha256:[a-f0-9]{64}', digest) else images.get(digest)
                if not image_id: raise engine.RecoveryError('Une image auxiliaire ne possède pas de référence immuable.')
                # backupIds: the engine drops this image only once every owning backup is retired.
                references.append({'key': object_key, 'prefix': prefix, 'group': 'web', 'appId': app_id, 'imageId': image_id, 'kind': 'image', 'backupIds': [backup['id']], 'required': backup['id'] == pending_id, 'snapshotAt': date, 'metadata': {'app-id': app_id, 'image-id': image_id}})
        storage = client.request(f'/api/web-apps/{app_id}/storage')
        for volume in storage.get('volumes', []):
            name = volume.get('name')
            if not re.fullmatch('[a-z][a-z0-9-]{0,30}', name or ''): raise engine.RecoveryError('Volume de préparation invalide.')
            pending = client.records('web_storage_jobs', f'appId="{app_id}" && (status="queued" || status="running")', fields='id,status')
            if pending:
                old_id = pending[0]['id']
                poll(config, run, lambda: True if client.request('/api/collections/web_storage_jobs/records/' + old_id, superuser=True).get('status') not in ('queued', 'running') else None, deadline)
            created = client.request(f'/api/web-apps/{app_id}/storage/{name}/backup', {}); job_id = created['job']['id']
            def volume_ready():
                value = client.request('/api/collections/web_storage_jobs/records/' + job_id, superuser=True)
                if value.get('status') == 'failed': raise engine.RecoveryError('Un snapshot de volume a échoué.')
                return value if value.get('status') == 'ready' else None
            poll(config, run, volume_ready, deadline)
        volumes = client.records('web_storage_jobs', f'appId="{app_id}" && status="ready" && operation="backup"', fields='id,appId,name,result,finishedAt,created')
        for volume in volumes:
            result = volume.get('result') or {}; object_key = result.get('object')
            if object_key: references.append({'key': object_key, 'prefix': prefix, 'group': 'web', 'appId': app_id, 'volume': volume['name'], 'kind': 'volume', 'snapshotAt': result.get('checkedAt') or volume.get('finishedAt') or volume.get('created'), 'metadata': {'app-id': app_id, 'volume-name': volume['name'], 'backup-id': volume['id']}})
        completed += 1; update(run, 'prepare', progress={'completed': completed, 'total': max(1, len(apps)), 'unit': 'items'})
    # Immutable objects can be referenced by several snapshots; copy each key only once.
    unique = {}
    for reference in references:
        key = reference.get('key')
        if key in unique and unique[key].get('imageId') and reference.get('imageId') and unique[key]['imageId'] != reference['imageId']: raise engine.RecoveryError('Référence S3 ambiguë.')
        if key in unique and reference.get('kind') == 'image' == unique[key].get('kind'):
            # Shared image: keep every owner so that one retired backup never drops it.
            reference = {**reference, 'backupIds': sorted(set(unique[key].get('backupIds', [])) | set(reference.get('backupIds', []))), 'required': unique[key].get('required') is True or reference.get('required') is True}
        unique[key] = reference
    plan['s3Refs'] = list(unique.values())
    engine.reconcile_retired_archives(plan)
    update(run, webBackups={'created': summary['created'], 'reused': len(summary['reused']), 'staleReused': sorted(item['appId'] for item in summary['reused'] if item['stale'])})
    filename = control.state_root() / 'prepared-plan.json'; control.atomic_json(filename, plan)
    return filename


def child_environment():
    allowed = ('WEB_BACKUPS_S3_', 'WEB_PLATFORM_S3_')
    return {'PATH': '/usr/local/bin:/usr/bin:/bin', 'PYTHONUNBUFFERED': '1', **{name: value for name, value in os.environ.items() if name.startswith(allowed) or name in ('WEB_PLATFORM_RECOVERY_PREFIX', 'WEB_APPS_MASTER_KEY')}}


def run_cli(config, run, arguments):
    command = [sys.executable, str(pathlib.Path(__file__).with_name('web_platform_recovery.py')), *arguments]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=child_environment(), start_new_session=True, cwd=control.state_root())
    events = queue.Queue()
    def read_lines(stream):
        try:
            for line in stream:
                if len(line) <= 128 * 1024:
                    try: events.put(json.loads(line))
                    except Exception: pass
        finally: events.put(None); stream.close()
    readers = [threading.Thread(target=read_lines, args=(stream,), daemon=True) for stream in [process.stdout, process.stderr]]
    for thread in readers: thread.start()
    finished, final, started, last_heartbeat = 0, None, time.monotonic(), 0
    try:
        while finished < 2 or process.poll() is None:
            if STOP.is_set() or time.monotonic() - started > config['captureTimeoutSeconds']:
                os.killpg(process.pid, signal.SIGTERM)
                try: process.wait(timeout=30)
                except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait()
                raise engine.RecoveryError('Le service de reprise a interrompu ce traitement.')
            if time.monotonic() - last_heartbeat > 5: heartbeat(config); last_heartbeat = time.monotonic()
            try: value = events.get(timeout=1)
            except queue.Empty: continue
            if value is None: finished += 1; continue
            if not isinstance(value, dict): continue
            if value.get('phase') == 'start' and isinstance(value.get('backupId'), str) and re.fullmatch('[A-Za-z0-9-]{1,80}', value['backupId']):
                run['privateBackupId'] = value['backupId']; control.save_run(run)
            if value.get('phase') in ('snapshot', 'database-complete', 'sql-progress'): update(run, 'capture')
            if value.get('phase') == 'verify': update(run, 'verify')
            if value.get('status') == 'failed':
                detail = value.get('error')
                run['privateEngineError'] = detail[:1000] if isinstance(detail, str) else 'Le moteur de reprise a échoué.'
                control.save_run(run)
            if type(value.get('encryptedBytes')) is int and value['encryptedBytes'] >= 0: update(run, bytes=value['encryptedBytes'])
            if value.get('status') in ('complete', 'partial', 'restored-paused'): final = value
        if process.wait() != 0 or not final: raise engine.RecoveryError('Le moteur n’a pas confirmé une capture vérifiée.')
        return final
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait()
        for thread in readers: thread.join(timeout=2)


def apply_retention(run, result):
    settings = control.get_settings(); rows = control.all_runs()
    captures = [row for row in rows if row['operation'] == 'capture' and (row['id'] == run['id'] or row['status'] == 'ready') and row.get('result', {}).get('verified') is True and row.get('result', {}).get('coverage', {}).get('complete') is True and row.get('result', {}).get('retained') is not False]
    captures.sort(key=lambda value: (value.get('created', ''), value['id']), reverse=True)
    keep = {row['id'] for row in captures[:settings['retention']]}; keep.add(run['id'])
    keep.update(row['id'] for row in rows if row['operation'] == 'capture' and control.active_hold(row))
    drills = [row for row in rows if row['operation'] == 'drill' and (row['status'] in ('queued', 'running') or row['status'] == 'ready' and row.get('verified') is True and row.get('result', {}).get('paused') is True)]
    if drills:
        # Always preserve the source of the last successful drill, even outside the N newest captures.
        ready = [row for row in drills if row['status'] == 'ready']
        if ready: keep.add(max(ready, key=lambda value: (value.get('finishedAt', ''), value['id'])).get('sourceRunId'))
        keep.update(row.get('sourceRunId') for row in drills if row['status'] in ('queued', 'running'))
    candidates = [row for row in captures if row['id'] not in keep]
    if not candidates: return {'removed': 0, 'retained': len(keep)}
    configuration = engine.s3_settings(); client = engine.s3_client(configuration); removed = 0; delayed = False
    for old in candidates:
        def mark(row):
            if control.active_hold(row): return False
            row.setdefault('result', {})['retentionPending'] = True
        row = mutate_run(old['id'], mark)
        if row is None or control.active_hold(row): continue
        if purge_one(client, configuration, row): removed += 1
        else: delayed = True  # An incomplete purge must never remain drill-eligible.
    return {'removed': removed, 'retained': len(keep), 'delayed': delayed}


def purge_one(client, configuration, row):
    try: engine.purge_capture(client, configuration, row['result'].get('object'), row['result'].get('references'))
    except Exception: return False
    mutate_run(row['id'], lambda fresh: fresh.setdefault('result', {}).update(retained=False, retentionPending=False, purgedAt=control.stamp()))
    return True


def resume_retention():
    """A purge interrupted by SIGKILL/restart is finished before any new capture."""
    pending = [row for row in control.all_runs() if row['operation'] == 'capture' and row.get('result', {}).get('retentionPending') is True and row['result'].get('retained') is not False and not control.active_hold(row)]
    if not pending: return {'resumed': 0}
    configuration = engine.s3_settings(); client = engine.s3_client(configuration)
    return {'resumed': sum(purge_one(client, configuration, row) for row in pending), 'pending': len(pending)}


def capture_backup_id(row):
    value = row.get('privateBackupId')
    if isinstance(value, str): return value
    key = row.get('result', {}).get('object') if isinstance(row.get('result'), dict) else None
    return key.split('/')[-2] if isinstance(key, str) and key.count('/') >= 2 else None


def sweep_storage(config, failed=(), dry_run=False):
    rows = control.all_runs()
    protected = {capture_backup_id(row) for row in rows if row['status'] in control.ACTIVE or row['operation'] == 'capture' and row['status'] == 'ready' and row.get('result', {}).get('retained') is not False}
    failures = {capture_backup_id(row) for row in rows if row['operation'] == 'capture' and row['status'] == 'failed'} | set(failed)
    configuration = engine.s3_settings(); client = engine.s3_client(configuration)
    return engine.sweep_orphans(client, configuration, protected - {None}, failures - {None}, config.get('sweepMinAgeHours', 48) * 3600, dry_run=dry_run)


def remove_drill(config, target):
    """Delete one drillRoot/<runId> copy (production data, TOTP keys, host files)."""
    root = pathlib.Path(config['drillRoot']); target = pathlib.Path(target)
    if target.parent != root or not control.ID.fullmatch(target.name): return False
    if not os.path.lexists(target): return True
    if target.is_symlink() or not target.is_dir(): return False
    shutil.rmtree(target)
    return not os.path.lexists(target)


def sweep_drills(config, dry_run=False):
    root = pathlib.Path(config['drillRoot'])
    if not root.is_dir() or root.is_symlink(): return {'removed': 0}
    active = {row['id'] for row in control.all_runs() if row['status'] in control.ACTIVE}
    leftovers = [item for item in root.iterdir() if control.ID.fullmatch(item.name) and item.name not in active]
    return {'removed': len(leftovers) if dry_run else sum(remove_drill(config, item) for item in leftovers), 'dryRun': dry_run}


def verify_drill(config, run, target):
    reports = control.state_root() / 'reports'
    if not reports.exists():
        reports.mkdir(mode=0o700)
        if os.geteuid() == 0:
            owner = control.state_root().stat(); os.chown(reports, owner.st_uid, owner.st_gid)
    report = reports / (run['id'] + '-verify.json')
    command = [sys.executable, str(pathlib.Path(__file__).with_name('web_platform_recovery_probe.py')), '--root', str(target), '--expected-databases', str(len(config['drillSelect'])), '--report', str(report), '--verify-only']
    try:
        completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, env=child_environment(), cwd=control.state_root(), timeout=config['captureTimeoutSeconds'], start_new_session=True)
        value = json.loads(completed.stdout.strip().splitlines()[-1]); code = completed.returncode
    except Exception: value, code = {}, 1
    return {'passed': code == 0 and value.get('passed') is True, **{name: control.safe_number(value.get(name)) for name in ['databases', 'files', 'fileBytes', 'mfaSidecars']}, 'report': report.name}


def events_config(config):
    return config.get('failureEvents') or {'enabled': False, 'path': EVENT_PATH, 'secretFile': ''}


def event_secret(config):
    filename = events_config(config)['secretFile']
    value = engine.private_file(filename).read_text().strip() if filename else os.environ.get('WEB_APPS_WORKER_SECRET', '')
    if len(value) < 32 or len(value) > 512 or any(char in value for char in '\r\n\x00'): raise engine.RecoveryError('Secret local du worker absent.')
    return value


def failure_payload(config, run):
    """Secret-free platform event; eventKey is the idempotency key for the controller."""
    capture = run['operation'] == 'capture'
    return {'version': 1, 'event': 'recovery_failed', 'eventKey': 'platform-recovery:' + run['id'], 'platform': config.get('platform', 'app1'), 'operation': run['operation'], 'runId': run['id'], 'trigger': run.get('trigger', 'manual') if run.get('trigger') in ('manual', 'scheduled', 'cli') else 'manual', 'stage': run.get('stageCode', '') if run.get('stageCode') in STAGES else '', 'errorCode': run.get('errorCode', 'CONFIGURATION'), 'occurredAt': run.get('finishedAt') or control.stamp(), 'message': 'La capture de reprise du serveur a échoué.' if capture else 'Le test de reprise du serveur a échoué.'}


def send_event(config, payload):
    request = urllib.request.Request(config['controlUrl'].rstrip('/') + events_config(config)['path'], data=json.dumps(payload).encode(), method='POST', headers={'Content-Type': 'application/json', 'X-Web-Worker': event_secret(config)})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=10) as response:
        response.read(65536); return 200 <= response.status < 300


def deliver_event(config, run, now=None):
    event = run.get('failureEvent')
    if not events_config(config)['enabled'] or not isinstance(event, dict) or event.get('status') != 'pending': return False
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        if event.get('nextAttemptAt') and datetime.datetime.fromisoformat(event['nextAttemptAt']) > now: return False
    except (TypeError, ValueError): pass
    event['attempts'] = event.get('attempts', 0) + 1
    try: sent = send_event(config, failure_payload(config, run))
    except Exception: sent = False
    if sent: event.update(status='sent', sentAt=control.stamp()); event.pop('nextAttemptAt', None)
    elif event['attempts'] >= EVENT_ATTEMPTS: event['status'] = 'failed'
    else: event['nextAttemptAt'] = (now + datetime.timedelta(seconds=60 * 2 ** (event['attempts'] - 1))).isoformat(timespec='milliseconds')
    update(run, failureEvent=event)
    return sent


def after_failure(config, run):
    if run['operation'] == 'capture' and config.get('sweepEnabled', True):
        try: run['sweep'] = {key: value for key, value in sweep_storage(config, {capture_backup_id(run)}).items() if key != 'capturesRemoved'}
        except Exception: run['sweep'] = {'error': True}
    if events_config(config)['enabled'] and not isinstance(run.get('failureEvent'), dict): run['failureEvent'] = {'status': 'pending', 'attempts': 0, 'eventKey': 'platform-recovery:' + run['id']}
    update(run)
    deliver_event(config, run)


def maintenance(config):
    try: resume_retention()
    except Exception: pass
    for row in control.all_runs():
        if row['status'] == 'failed' and isinstance(row.get('failureEvent'), dict) and row['failureEvent'].get('status') == 'pending': deliver_event(config, row)


def process_job(config, run):
    update(run, 'prepare', status='running', startedAt=control.stamp(), error='')
    try:
        if run['operation'] == 'capture':
            plan_file = prepare_plan(config, run); update(run, 'capture')
            result = run_cli(config, run, ['backup', '--plan', str(plan_file), '--key-file', config['keyFile']])
            if result.get('verified') is not True or result.get('coverage', {}).get('complete') is not True: raise engine.RecoveryError('La capture ne protège pas toutes les ressources prévues.')
            result['retained'] = True
            # Durably ready BEFORE any purge: a crash during retention can never lose the newest capture.
            update(run, 'retention', status='ready', finishedAt=control.stamp(), result=result, verified=True, coverage=result.get('coverage', {}))
            try: result['retention'] = apply_retention(run, result)
            except Exception: result['retention'] = {'removed': 0, 'delayed': True}
            if config.get('sweepEnabled', True):
                try: result['sweep'] = {key: value for key, value in sweep_storage(config).items() if key != 'capturesRemoved'}
                except Exception: result['sweep'] = {'error': True}
        elif run['operation'] == 'drill':
            source = next((value for value in control.all_runs() if value['id'] == run.get('sourceRunId') and value['operation'] == 'capture' and value['status'] == 'ready'), None)
            object_key = source.get('result', {}).get('object') if source else None
            prefix = os.environ.get('WEB_PLATFORM_RECOVERY_PREFIX', 'app1/platform-recovery')
            if not isinstance(object_key, str) or not object_key.startswith(prefix + '/') or not object_key.endswith('/platform.phpr'): raise engine.RecoveryError('Capture source du test invalide.')
            target = pathlib.Path(config['drillRoot']) / run['id']
            if target.exists() or target.resolve() != target: raise engine.RecoveryError('Le test exige une nouvelle cible privée.')
            update(run, 'drill'); cleaned = None
            try:
                result = run_cli(config, run, ['restore', '--object', object_key, '--key-file', config['keyFile'], '--target', str(target), '--select', ','.join(config['drillSelect']), '--min-free-gib', str(config['drillMinFreeGiB']), '--max-bytes', str(config['maxDrillBytes'])])
                if result.get('paused') is not True or result.get('noServicesStarted') is not True: raise engine.RecoveryError('La reconstruction n’est pas confirmée comme suspendue.')
                if config.get('drillVerify', True):
                    update(run, 'verify'); result['verification'] = verify_drill(config, run, target)
                    if not result['verification']['passed']: run['verification'] = result['verification']; raise VerificationFailed('Le contrôle de la copie reconstruite a échoué.')
                result['verified'] = True
            finally:
                if config.get('drillCleanup', True):
                    try: cleaned = remove_drill(config, target)
                    except Exception: cleaned = False
                    run['cleanedUp'] = cleaned
            if cleaned is not None: result['cleanedUp'] = cleaned
        else: raise engine.RecoveryError('Opération de service non autorisée.')
        update(run, 'complete', status='ready', finishedAt=control.stamp(), verified=True, coverage=result.get('coverage', {}), result=result)
    except Exception as error:
        # The engine's own message (kept private) tells disk/budget failures apart from configuration.
        detail = (str(error) + ' ' + str(run.get('privateEngineError', ''))) if isinstance(error, engine.RecoveryError) else ''
        code = 'VERIFICATION' if isinstance(error, VerificationFailed) else 'INSUFFICIENT_SPACE' if 'espace' in detail.lower() or 'Gio' in detail or 'budget' in detail or 'réserve disque' in detail.lower() else 'DEPENDENCIES_NOT_READY' if run.get('stageCode') == 'prepare' else 'CONFIGURATION'
        update(run, status='failed', finishedAt=control.stamp(), errorCode=code, error='Traitement de reprise interrompu ; les services d’origine sont conservés.')
        after_failure(config, run)


def worker_once(config):
    heartbeat(config)
    if config.get('executionEnabled', True) is False:
        try: control.skip_locked_occurrence()
        except Exception: pass  # Heartbeat-only mode must never fail on settings state.
        return False
    control.schedule_tick()
    with control.state_lock():
        rows = control.all_runs()
        stale = [row for row in rows if row['status'] == 'running']
        for row in stale: update(row, status='failed', finishedAt=control.stamp(), error='Service interrompu avant confirmation.', errorCode='CONFIGURATION')
        queued = sorted((row for row in rows if row['status'] == 'queued'), key=lambda value: (value['created'], value['id']))
        run = queued[0] if queued else None
        if run: update(run, 'prepare', status='running', startedAt=control.stamp())
    for row in stale:
        # SIGKILL/restart: sweep that capture's partial objects and drill copy, then report it.
        if row['operation'] == 'drill' and config.get('drillCleanup', True):
            try: remove_drill(config, pathlib.Path(config['drillRoot']) / row['id'])
            except Exception: pass
        after_failure(config, row)
    if not run:
        maintenance(config); return False
    process_job(config, run); heartbeat(config)
    return True


def refresh_plan_check(config):
    try:
        import web_platform_recovery_plan as recovery_plan
        plan = recovery_plan.read_plan(config['planFile']); root = recovery_plan.data_root_of(plan, config.get('dataRoot'))
        if config.get('planFromInventory'): plan, _report = recovery_plan.build_plan(plan, root, allow_without_database=True)
        value = recovery_plan.drift(plan, root)
        state = {'checked': True, 'drift': value['drift'], 'added': len(value['added']), 'removed': len(value['removed']), 'checkedAt': value['checkedAt']}
    except Exception: state = {'checked': False, 'checkedAt': control.stamp()}
    PLAN_CHECK.clear(); PLAN_CHECK.update(state)
    return state


def recoverable_captures():
    return [{'id': row['id'], 'created': row.get('created', ''), 'trigger': row.get('trigger', ''), 'object': row['result'].get('object'), 'backupId': row['result'].get('backupId'), 'databases': len(row['result'].get('databases', [])), 'held': control.active_hold(row), 'restoreEstimate': row['result'].get('restoreEstimate')} for row in control.all_runs() if row['operation'] == 'capture' and control.public_job(row)['recoverable']]


def hold_capture(source, hours, label=''):
    until = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)).isoformat(timespec='milliseconds')
    def change(row):
        if row['operation'] != 'capture' or not control.public_job(row)['recoverable']: raise engine.RecoveryError('Seule une capture vérifiée et conservée peut être protégée.')
        row['holds'] = [value for value in row.get('holds', []) if isinstance(value, dict)][-19:] + [{'id': secrets.token_hex(8), 'until': until, 'label': label[:120], 'createdAt': control.stamp(), 'by': 'cli'}]
    row = mutate_run(source, change)
    if row is None: raise engine.RecoveryError('Capture inconnue.')
    return {'sourceRunId': source, 'object': row['result']['object'], 'holdUntil': until}


def release_capture(source):
    row = mutate_run(source, lambda value: value.update(holds=[]))
    if row is None: raise engine.RecoveryError('Capture inconnue.')
    return {'sourceRunId': source, 'released': True}


def record_drill(source, report_file, native_file=None):
    """Register a CLI drill: its source becomes the protected 'last successful drill' capture."""
    def load(filename):
        try: return json.loads(engine.private_file(filename).read_bytes())
        except engine.RecoveryError: raise
        except Exception: raise engine.RecoveryError('Rapport de test illisible.') from None
    report = load(report_file); native = load(native_file) if native_file else None
    with control.state_lock():
        capture = next((row for row in control.all_runs() if row['id'] == source and row['operation'] == 'capture' and control.public_job(row)['recoverable']), None)
        if capture is None: raise engine.RecoveryError('Seule une capture vérifiée et conservée peut être associée à un test.')
        databases = [value.get('id') for value in report.get('databases', []) if isinstance(value, dict)]
        if report.get('passed') is not True or report.get('backupId') != capture['result'].get('backupId') or 'controller' not in databases: raise engine.RecoveryError('Le rapport ne prouve pas un test réussi de cette capture.')
        if native is not None and (native.get('passed') is not True or native.get('backupId') != report['backupId'] or native.get('nativeHealthy') != len(databases) or native.get('noExternalNetwork') is not True): raise engine.RecoveryError('La preuve native ne correspond pas à ce test.')
        result = {'paused': True, 'noServicesStarted': True, 'verified': True, 'databasesRestored': databases, 'verification': {'passed': True, 'databases': len(databases), 'files': control.safe_number(report.get('files')), 'fileBytes': control.safe_number(report.get('fileBytes')), 'mfaSidecars': control.safe_number(report.get('mfaSidecars'))}}
        if native is not None: result['nativeProof'] = {'passed': True, 'nativeHealthy': native['nativeHealthy']}
        run = {'id': secrets.token_hex(16), 'operation': 'drill', 'status': 'ready', 'trigger': 'cli', 'created': control.stamp(), 'startedAt': report.get('verifiedAt') if isinstance(report.get('verifiedAt'), str) else control.stamp(), 'finishedAt': control.stamp(), 'sourceRunId': source, 'actorId': '', 'verified': True, 'result': result}
        control.save_run(run)
    return {'runId': run['id'], 'sourceRunId': source, 'databases': len(databases), 'nativeProof': native is not None}


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == 'api':
        raw = sys.argv[3] if len(sys.argv) >= 4 else sys.stdin.read(65537)
        try: value = json.loads(raw or '{}') if len(raw) <= 65536 else None
        except Exception: value = None
        print(json.dumps(control.api(sys.argv[2], value))); return
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('operation', choices=['serve', 'once', 'captures', 'hold', 'release', 'record-drill', 'sweep']); parser.add_argument('--config', required=True)
    parser.add_argument('--source', help='Capture run id (32 hex)'); parser.add_argument('--hours', type=int, default=48); parser.add_argument('--label', default='')
    parser.add_argument('--report', help='verify-only probe report'); parser.add_argument('--native-report'); parser.add_argument('--execute', action='store_true', help='sweep: delete instead of listing')
    arguments = parser.parse_args()
    config = config_file(arguments.config)
    if arguments.operation not in ('serve', 'once'):
        if arguments.operation in ('hold', 'release', 'record-drill') and not control.ID.fullmatch(arguments.source or ''): raise engine.RecoveryError('Capture source invalide.')
        if arguments.operation == 'captures': value = {'captures': recoverable_captures()}
        elif arguments.operation == 'hold':
            if not 1 <= arguments.hours <= 720: raise engine.RecoveryError('Durée de protection de 1 à 720 heures.')
            value = hold_capture(arguments.source, arguments.hours, arguments.label)
        elif arguments.operation == 'release': value = release_capture(arguments.source)
        elif arguments.operation == 'record-drill':
            if not arguments.report: raise engine.RecoveryError('Rapport --report requis.')
            value = record_drill(arguments.source, arguments.report, arguments.native_report)
        else: value = {'storage': sweep_storage(config, dry_run=not arguments.execute), 'drills': sweep_drills(config, dry_run=not arguments.execute)}
        print(json.dumps(value)); return
    lock = os.open(control.state_root() / 'runner.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    if os.geteuid() == 0:
        owner = control.state_root().stat(); os.fchown(lock, owner.st_uid, owner.st_gid)
    owned = False
    try:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return
        owned = True
        signal.signal(signal.SIGTERM, lambda *_args: STOP.set()); signal.signal(signal.SIGINT, lambda *_args: STOP.set())
        refresh_plan_check(config); checked = time.monotonic()
        try: sweep_drills(config)
        except Exception: pass
        while not STOP.is_set():
            if time.monotonic() - checked > 600: refresh_plan_check(config); checked = time.monotonic()
            worker_once(config)
            if arguments.operation == 'once': break
            STOP.wait(5)
    finally:
        if owned: heartbeat(config, online=False)
        os.close(lock)


if __name__ == '__main__':
    try: main()
    except engine.RecoveryError as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}), file=sys.stderr); sys.exit(1)
    except Exception:
        print(json.dumps({'status': 'failed', 'error': 'Le service de reprise exige une configuration privée valide.'}), file=sys.stderr); sys.exit(1)
