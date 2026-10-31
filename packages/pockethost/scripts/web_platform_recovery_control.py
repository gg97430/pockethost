#!/usr/bin/env python3
"""Private file queue for platform recovery; API mode never opens keys or runs captures."""
import contextlib
import datetime
import fcntl
import json
import os
import pathlib
import re
import secrets
import stat

ID = re.compile(r'^[a-f0-9]{32}$')
DATABASE = re.compile(r'^(?:controller|[a-z0-9]{15})$')
MAX_FILE = 256 * 1024
MAX_RUNS = 10000
ACTIVE = ('queued', 'running')


class ControlError(RuntimeError):
    pass


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')


def state_root():
    raw = os.environ.get('WEB_PLATFORM_RECOVERY_STATE_DIR', '')
    path = pathlib.Path(raw).absolute()
    if not raw or not pathlib.Path(raw).is_absolute() or path.resolve() != path:
        raise ControlError('Service de reprise non configuré.')
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or (os.geteuid() != 0 and info.st_uid != os.geteuid()):
        raise ControlError('Répertoire de reprise privé requis.')
    return path


def read_json(path, default=None):
    path = pathlib.Path(path)
    if path.absolute() != path or path.resolve() != path:
        raise ControlError('Fichier de reprise privé invalide.')
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {} if default is None else default
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > MAX_FILE or (os.geteuid() != 0 and info.st_uid != os.geteuid()):
            raise ControlError('Métadonnées de reprise privées invalides.')
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            raw = stream.read(MAX_FILE + 1)
        if len(raw) > MAX_FILE: raise ControlError('Métadonnées de reprise trop volumineuses.')
        return json.loads(raw)
    finally:
        os.close(descriptor)


def atomic_json(path, value):
    path = pathlib.Path(path)
    if not path.is_absolute() or path.parent.resolve() != path.parent:
        raise ControlError('Répertoire de reprise privé invalide.')
    parent = path.parent.stat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_mode & 0o077:
        raise ControlError('Répertoire de reprise privé invalide.')
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
    if len(raw) > MAX_FILE: raise ControlError('Métadonnées de reprise trop volumineuses.')
    temporary = path.parent / ('.' + path.name + '-' + secrets.token_hex(12))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        if os.geteuid() == 0: os.fchown(descriptor, parent.st_uid, parent.st_gid)
        with os.fdopen(os.dup(descriptor), 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(parent_fd)
        finally: os.close(parent_fd)
    finally:
        os.close(descriptor)
        try: temporary.unlink()
        except FileNotFoundError: pass


@contextlib.contextmanager
def state_lock():
    root = state_root()
    descriptor = os.open(root / 'state.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ControlError('Verrou de reprise privé invalide.')
        if os.geteuid() == 0:
            owner = root.stat(); os.fchown(descriptor, owner.st_uid, owner.st_gid)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def all_runs():
    root = state_root(); directory = root / 'runs'
    if not directory.exists(): return []
    if directory.resolve() != directory or not directory.is_dir() or directory.stat().st_mode & 0o077:
        raise ControlError('Historique de reprise privé invalide.')
    files = list(directory.glob('*.json'))
    if len(files) > MAX_RUNS: raise ControlError('Historique de reprise trop volumineux.')
    result = []
    for filename in files:
        if not ID.fullmatch(filename.stem): continue
        row = read_json(filename)
        if not isinstance(row, dict) or row.get('id') != filename.stem or row.get('operation') not in ('capture', 'drill') or row.get('status') not in (*ACTIVE, 'ready', 'failed'):
            raise ControlError('État de reprise invalide ; aucun nouveau traitement lancé.')
        result.append(row)
    return sorted(result, key=lambda value: (value.get('created', ''), value['id']), reverse=True)


def save_run(run):
    if not isinstance(run, dict) or not ID.fullmatch(run.get('id', '')):
        raise ControlError('Identifiant de reprise invalide.')
    directory = state_root() / 'runs'
    if not directory.exists():
        directory.mkdir(mode=0o700)
        if os.geteuid() == 0:
            owner = state_root().stat(); os.chown(directory, owner.st_uid, owner.st_gid)
    atomic_json(directory / (run['id'] + '.json'), run)


def consumed_dates(stored):
    # A day is consumed once its capture was queued, or skipped when the schedule was armed after its time.
    return {value for value in [stored.get('lastScheduledDate'), stored.get('skippedScheduledDate')] if isinstance(value, str)} if isinstance(stored, dict) else set()


def next_run(settings, now=None, stored=None):
    if not settings['enabled']: return ''
    now = now or datetime.datetime.now(datetime.timezone.utc)
    due = now.replace(hour=settings['hourUTC'], minute=settings['minuteUTC'], second=0, microsecond=0)
    if due <= now or due.date().isoformat() in consumed_dates(stored): due += datetime.timedelta(days=1)
    return due.isoformat(timespec='milliseconds')


def get_settings():
    value = read_json(state_root() / 'settings.json')
    if not isinstance(value, dict): raise ControlError('Planification de reprise invalide.')
    result = {'enabled': value.get('enabled', False), 'hourUTC': value.get('hourUTC', 2), 'minuteUTC': value.get('minuteUTC', 30), 'retention': value.get('retention', 3)}
    if type(result['enabled']) is not bool or any(type(result[key]) is not int or not low <= result[key] <= high for key, low, high in [('hourUTC', 0, 23), ('minuteUTC', 0, 59), ('retention', 1, 30)]):
        raise ControlError('Planification de reprise invalide.')
    result['nextRunAt'] = next_run(result, stored=value)
    return result


SCHEDULE_MESSAGES = {'disabled': 'Planification désactivée.', 'active': '', 'notConfigured': 'Service de reprise non configuré : aucune capture planifiée ne sera lancée.', 'executionDisabled': 'Planification enregistrée, mais l’exécution est verrouillée sur le serveur (executionEnabled=false) : aucune capture ne sera lancée tant que ce verrou n’est pas levé.', 'serviceOffline': 'Service de reprise hors ligne : la capture planifiée attendra son retour.'}


def schedule_view(settings, state):
    """Public schedule: nextRunAt only when the service will really run it."""
    value = dict(settings)
    if not settings['enabled']: status = 'disabled'
    elif not state.get('configured'): status = 'notConfigured'
    elif state.get('executionEnabled') is False: status = 'executionDisabled'
    elif not state.get('worker', {}).get('online'): status = 'serviceOffline'
    else: status = 'active'
    value.update(status=status, statusMessage=SCHEDULE_MESSAGES[status])
    if status != 'active': value['plannedRunAt'] = value.get('nextRunAt', ''); value['nextRunAt'] = ''
    return value


def enqueue(operation, actor_id='', source=None, trigger='manual'):
    if any(row['status'] in ACTIVE for row in all_runs()):
        raise ControlError('BUSY')
    run = {'id': secrets.token_hex(16), 'operation': operation, 'status': 'queued', 'created': stamp(), 'actorId': actor_id, 'trigger': trigger}
    if source: run['sourceRunId'] = source
    save_run(run)
    return run


def schedule_tick():
    with state_lock():
        settings = get_settings(); now = datetime.datetime.now(datetime.timezone.utc)
        if not settings['enabled']: return None
        due = now.replace(hour=settings['hourUTC'], minute=settings['minuteUTC'], second=0, microsecond=0)
        stored = read_json(state_root() / 'settings.json'); rows = all_runs()
        # The run itself is the durable occurrence marker if a crash preceded settings.json.
        already_queued = any(row.get('trigger') == 'scheduled' and row.get('created', '')[:10] == now.date().isoformat() for row in rows)
        if now < due or now.date().isoformat() in consumed_dates(stored) or already_queued or any(row['status'] in ACTIVE for row in rows): return None
        run = enqueue('capture', trigger='scheduled')
        stored.update({key: settings[key] for key in ['enabled', 'hourUTC', 'minuteUTC', 'retention']})
        stored['lastScheduledDate'] = now.date().isoformat(); atomic_json(state_root() / 'settings.json', stored)
        return run


def skip_locked_occurrence():
    """executionEnabled=false at the daily time consumes that day: lifting the lock later the
    same day must not snapshot every site volume mid-day (runToday or a manual capture can)."""
    with state_lock():
        settings = get_settings(); now = datetime.datetime.now(datetime.timezone.utc); today = now.date().isoformat()
        if not settings['enabled']: return False
        due = now.replace(hour=settings['hourUTC'], minute=settings['minuteUTC'], second=0, microsecond=0)
        stored = read_json(state_root() / 'settings.json')
        if now < due or today in consumed_dates(stored) or any(row.get('trigger') == 'scheduled' and row.get('created', '')[:10] == today for row in all_runs()): return False
        stored['skippedScheduledDate'] = today; atomic_json(state_root() / 'settings.json', stored)
        return True


def safe_number(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else 0


def public_coverage(value):
    if not isinstance(value, dict): return None
    result = {'complete': value.get('complete') is True, 'databaseAndFilesAtomic': False}
    if value.get('scope') == 'persistent-platform-resources': result['scope'] = 'persistent-platform-resources'
    for field in ['instancesMissing', 'externalStorage', 'unprotectedWebImages', 'unprotectedVolumes', 'ephemeralPreviewsExcluded']:
        entries = value.get(field, [])
        result[field + 'Count'] = len(entries) if isinstance(entries, list) else safe_number(value.get(field + 'Count', entries))
    return result


def public_job(run):
    if not run: return None
    result = run.get('result') if isinstance(run.get('result'), dict) else {}
    output = {key: run[key] for key in ['id', 'operation', 'status', 'created', 'startedAt', 'finishedAt'] if isinstance(run.get(key), str)}
    if run.get('trigger') in ('manual', 'scheduled', 'cli'): output['trigger'] = run['trigger']
    stages = {'prepare': 'Préparation des sauvegardes des sites et volumes', 'capture': 'Capture chiffrée des bases et fichiers', 'verify': 'Vérification de l’archive distante', 'drill': 'Récupération dans un espace isolé', 'retention': 'Vérification de la rétention', 'complete': 'Traitement terminé'}
    output['stage'] = stages.get(run.get('stageCode'), {'queued': 'En attente du service dédié', 'running': 'Traitement en cours', 'ready': 'Traitement terminé', 'failed': 'Traitement interrompu'}.get(run['status'], ''))
    if run.get('error') or run['status'] == 'failed':
        output['error'] = {'INSUFFICIENT_SPACE': 'Espace libre insuffisant pour conserver la réserve de 15 Gio.', 'DEPENDENCIES_NOT_READY': 'Des sauvegardes d’images ou de volumes sont encore indisponibles.', 'CONFIGURATION': 'La configuration privée du service doit être vérifiée.', 'VERIFICATION': 'Le contrôle de la copie reconstruite a échoué ; la capture source n’est pas confirmée.'}.get(run.get('errorCode'), 'Le traitement a échoué. Vérifiez la configuration et l’état du service de reprise.')
    output['verified'] = run.get('verified') is True or result.get('verified') is True
    output['retained'] = result.get('retained', True) is not False
    output['retentionPending'] = result.get('retentionPending') is True
    output['recoverable'] = run['operation'] == 'capture' and run['status'] == 'ready' and output['verified'] and output['retained'] and not output['retentionPending'] and bool(result.get('object'))
    output['coverage'] = public_coverage(run.get('coverage') or result.get('coverage'))
    output['held'] = active_hold(run)
    for field in ['bytes', 'encryptedBytes']:
        if field in run or field in result: output[field] = safe_number(run.get(field, result.get(field)))
    restored = result.get('databasesRestored', [])
    output['result'] = {'verified': output['verified'], 'coverage': output['coverage'], 'paused': result.get('paused') is True, 'noServicesStarted': result.get('noServicesStarted') is True, 'databasesRestored': [value for value in restored if isinstance(value, str) and DATABASE.fullmatch(value)][:1000] if isinstance(restored, list) else []}
    if isinstance(result.get('verification'), dict):
        check = result['verification']; output['result']['verification'] = {'passed': check.get('passed') is True, 'databases': safe_number(check.get('databases')), 'files': safe_number(check.get('files')), 'mfaSidecars': safe_number(check.get('mfaSidecars'))}
    if run['operation'] == 'drill' and 'cleanedUp' in result: output['result']['cleanedUp'] = result.get('cleanedUp') is True
    if isinstance(run.get('webBackups'), dict):
        # Site archives reused instead of created (customer retention protected); counts only.
        backups = run['webBackups']; output['webBackups'] = {'created': safe_number(backups.get('created')), 'reused': safe_number(backups.get('reused')), 'staleReused': len(backups['staleReused']) if isinstance(backups.get('staleReused'), list) else 0}
    if isinstance(run.get('progress'), dict):
        progress = run['progress']; completed, total = safe_number(progress.get('completed')), safe_number(progress.get('total'))
        if 0 < total and completed <= total and progress.get('unit') in ('bytes', 'items'):
            output['progress'] = {'completed': completed, 'total': total, 'unit': progress['unit']}
    return output


def active_hold(run, now=None):
    """CLI drills hold their source capture against retention until the hold expires."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    for hold in run.get('holds', []) if isinstance(run.get('holds'), list) else []:
        try:
            if datetime.datetime.fromisoformat(str(hold.get('until', '')).replace('Z', '+00:00')) > now: return True
        except (ValueError, TypeError, AttributeError): continue
    return False


def worker_view():
    value = read_json(state_root() / 'worker.json')
    if not isinstance(value, dict): raise ControlError('État du service de reprise invalide.')
    try:
        last = datetime.datetime.fromisoformat(value.get('lastSeenAt', '').replace('Z', '+00:00'))
        age = (datetime.datetime.now(datetime.timezone.utc) - last).total_seconds()
    except (ValueError, TypeError): age = float('inf')
    configured = bool(value) and value.get('configured', True) is True
    online = configured and value.get('online', True) is True and 0 <= age <= 180
    capabilities = value.get('capabilities') if isinstance(value.get('capabilities'), dict) else {}
    selection = value.get('drillSelection', [])
    plan = value.get('planCheck') if isinstance(value.get('planCheck'), dict) else {}
    plan_view = {'checked': plan.get('checked') is True, 'drift': plan.get('drift') is True, 'added': safe_number(plan.get('added')), 'removed': safe_number(plan.get('removed')), 'checkedAt': plan.get('checkedAt', '') if isinstance(plan.get('checkedAt'), str) else ''}
    return {'configured': configured, 'executionEnabled': configured and value.get('executionEnabled', capabilities.get('canCapture', True)) is True, 'planCheck': plan_view, 'worker': {'online': online, 'lastSeenAt': value.get('lastSeenAt', '') if isinstance(value.get('lastSeenAt'), str) else ''}, 'capabilities': {'canCapture': online and capabilities.get('canCapture', True) is True, 'canDrill': online and capabilities.get('canDrill', False) is True}, 'drillSelection': [value for value in selection if isinstance(value, str) and DATABASE.fullmatch(value)] if isinstance(selection, list) else []}


def api(operation, input):
    try:
        if not isinstance(input, dict): raise ControlError('Options de reprise invalides.')
        with state_lock():
            if operation == 'status':
                rows = all_runs(); state = worker_view()
                state.update(settings=schedule_view(get_settings(), state), latest=public_job(next((row for row in rows if row['operation'] == 'capture' and row['status'] == 'ready'), None)), active=public_job(next((row for row in rows if row['status'] in ACTIVE), None)))
                return {'status': 200, 'response': state}
            if operation == 'runs':
                page = input.get('page', 1)
                if set(input) - {'page'} or type(page) is not int or not 1 <= page <= 100000: raise ControlError('Page de reprise invalide.')
                rows = all_runs()
                return {'status': 200, 'response': {'items': [public_job(row) for row in rows[(page - 1) * 20:page * 20]], 'page': page, 'perPage': 20, 'totalItems': len(rows), 'totalPages': max(1, (len(rows) + 19) // 20)}}
            actor = input.get('actorId', '')
            if not isinstance(actor, str) or not re.fullmatch(r'[a-z0-9]{15}', actor): raise ControlError('Administrateur de reprise requis.')
            if operation == 'settings':
                required = {'enabled', 'hourUTC', 'minuteUTC', 'retention', 'actorId'}
                if not required <= set(input) or set(input) - required - {'runToday'} or type(input.get('runToday', False)) is not bool: raise ControlError('Planification de reprise invalide.')
                if type(input['enabled']) is not bool or any(type(input[key]) is not int or not low <= input[key] <= high for key, low, high in [('hourUTC', 0, 23), ('minuteUTC', 0, 59), ('retention', 1, 30)]): raise ControlError('Planification de reprise invalide.')
                previous = get_settings(); stored = read_json(state_root() / 'settings.json')
                now = datetime.datetime.now(datetime.timezone.utc); today = now.date().isoformat()
                due = now.replace(hour=input['hourUTC'], minute=input['minuteUTC'], second=0, microsecond=0)
                armed = input['enabled'] and (not previous['enabled'] or (previous['hourUTC'], previous['minuteUTC']) != (input['hourUTC'], input['minuteUTC']))
                stored.update({key: input[key] for key in required})
                # Arming the schedule after today's time waits for tomorrow unless runToday is explicit.
                if input.get('runToday') is True: stored.pop('skippedScheduledDate', None)
                elif armed and now >= due and stored.get('lastScheduledDate') != today: stored['skippedScheduledDate'] = today
                atomic_json(state_root() / 'settings.json', stored)
                try: state = worker_view()
                except ControlError: state = {'configured': False, 'worker': {'online': False}}
                view = schedule_view(get_settings(), state)
                immediate = view['status'] == 'active' and now >= due and today not in consumed_dates(stored)
                return {'status': 200, 'response': {'settings': view, 'immediate': immediate}}
            state = worker_view()
            if not state['worker']['online']: raise ControlError('Le service de reprise est hors ligne.')
            if operation == 'capture' and set(input) == {'actorId'}:
                if not state['capabilities']['canCapture']: raise ControlError('La capture est indisponible.')
                run = enqueue('capture', actor)
            elif operation == 'drill' and set(input) == {'actorId', 'backupId'}:
                ident = input.get('backupId')
                if not isinstance(ident, str) or not ID.fullmatch(ident): raise ControlError('Capture de reprise invalide.')
                source = next((row for row in all_runs() if row['id'] == ident), None)
                if not state['capabilities']['canDrill'] or not source or source['operation'] != 'capture' or source['status'] != 'ready' or not public_job(source)['recoverable']:
                    raise ControlError('Cette capture vérifiée ne peut pas être testée.')
                run = enqueue('drill', actor, source=ident)
            else: raise ControlError('Options de reprise invalides.')
            return {'status': 202, 'response': {'job': public_job(run)}}
    except ControlError as error:
        if operation == 'status': return {'status': 200, 'response': {'configured': False, 'worker': {'online': False}, 'settings': {'enabled': False, 'hourUTC': 2, 'minuteUTC': 30, 'retention': 3, 'nextRunAt': '', 'status': 'notConfigured', 'statusMessage': SCHEDULE_MESSAGES['notConfigured']}, 'unavailable': True}}
        return {'status': 409 if str(error) == 'BUSY' else 400, 'response': {'error': 'Une opération de reprise est déjà active.' if str(error) == 'BUSY' else str(error)}}
    except Exception:
        return {'status': 503, 'response': {'error': 'Le contrôle de reprise est indisponible. Vérifiez le service dédié.'}}
