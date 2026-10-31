"""Small encrypted web-app archives and immutable-image pins. No Docker image export.

Bounded attempts (controller contract, see backups.ts):
 claim job may carry `attempt` (1-based count of leases for this backup, controller side) and `maxAttempts` (1-10,
 default 3). The worker also keeps a private ledger (backups/attempts.json, fsynced BEFORE any work) so a job whose
 worker crashes, is OOM-killed or loses its acknowledgement is counted even if the controller only re-queues it.
 Beyond the limit the job is not run again: it is reported {status:'failed', error, failureCode:'attempts_exhausted',
 attempts}. Every failed report carries failureCode in {'attempts_exhausted','backup_error','engine_error'} and attempts.
"""
import hashlib
import json
import os
import pathlib
import re
import secrets
import shutil
import threading
import time
import datetime

MAX_ARCHIVE = 512 * 1024
MAX_ARCHIVES = 64 * 1024**2
MIN_FREE = 15 * 1024**3
IMAGE_BUDGET = 6 * 1024**3
MAX_ATTEMPTS = 3
LEDGER_KEEP = 14 * 86400
_last_tick = {}
_tick_lock = threading.Lock()


class BackupError(RuntimeError):
    pass


def _id(value):
    if not isinstance(value, str) or not re.fullmatch('[a-z0-9]{15}', value):
        raise BackupError('Identifiant de sauvegarde invalide.')
    return value


def _image(value, app_id):
    return isinstance(value, str) and bool(re.fullmatch('sha256:[a-f0-9]{64}', value) or re.fullmatch('ph-web/' + _id(app_id) + ':[a-z0-9]{15}', value))


def _images(value):
    if value is None: return {}
    if not isinstance(value, dict) or len(value) > 4 or any(not isinstance(name, str) or not re.fullmatch('[a-z][a-z0-9-]{0,30}', name) or not isinstance(ref, str) or not re.fullmatch('sha256:[a-f0-9]{64}', ref) for name, ref in value.items()):
        raise BackupError('Inventaire des images invalide.')
    return value

def _sizes(value):
    if value is None: return {}
    if not isinstance(value, dict) or len(value) > 4 or any(not isinstance(ref, str) or not re.fullmatch('sha256:[a-f0-9]{64}', ref) or type(size) is not int or size < 0 for ref, size in value.items()):
        raise BackupError('Tailles des images invalides.')
    return value

def _pin_sizes(pin):
    sizes = _sizes(pin.get('imageSizes'))
    return sizes or {pin['imageId']: pin['sizeBytes']}

def _base(root):
    root = pathlib.Path(root).absolute()
    base = root / 'backups'
    if root.resolve() != root or base.resolve() != base:
        raise BackupError('Répertoire de sauvegarde symbolique refusé.')
    base.mkdir(mode=0o750, parents=True, exist_ok=True)
    return base


def _path(root, app_id, backup_id):
    filename = _base(root) / _id(app_id) / (_id(backup_id) + '.phwb')
    if filename.resolve() != filename:
        raise BackupError('Archive symbolique refusée.')
    return filename


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def _atomic(path, data):
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with open(temporary, 'xb') as stream:
            os.chmod(temporary, 0o640)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _manifest(root):
    filename = _base(root) / 'pins.json'
    if filename.resolve() != filename:
        raise BackupError('Manifeste des images symbolique refusé.')
    if not filename.exists():
        return {'version': 1, 'backups': {}}
    try:
        if filename.stat().st_size > 2 * 1024**2:
            raise ValueError()
        value = json.loads(filename.read_text())
        if value.get('version') != 1 or not isinstance(value.get('backups'), dict) or len(value['backups']) > 10000:
            raise ValueError()
        for backup_id, pin in value['backups'].items():
            _id(backup_id)
            _id(pin['appId'])
            if not _image(pin['imageId'], pin['appId']) or not pin['imageId'].startswith('sha256:') or not _image(pin['imageRef'], pin['appId']) or not isinstance(pin['sizeBytes'], int) or pin['sizeBytes'] < 0:
                raise ValueError()
            images, sizes = _images(pin.get('images')), _sizes(pin.get('imageSizes'))
            if images and (pin['imageId'] not in images.values() or set(sizes) != set(images.values())): raise ValueError()
        return value
    except (ValueError, KeyError, TypeError, OSError):
        raise BackupError('Manifeste des images invalide : nettoyage des images suspendu.') from None


def _pins(api, app_id=None):
    value = api('backups/pins', {'appId': app_id} if app_id else {})
    if not isinstance(value, dict) or not isinstance(value.get('items'), list) or len(value['items']) > 10000:
        raise BackupError('Le contrôleur ne confirme pas les images à conserver.')
    for pin in value['items']:
        try:
            _id(pin['id'])
            _id(pin['appId'])
            images, sizes = _images(pin.get('images')), _sizes(pin.get('imageSizes'))
            if any(ref not in images.values() for ref in sizes): raise ValueError()
            if not _image(pin['imageRef'], pin['appId']) or (pin.get('imageId') and not re.fullmatch('sha256:[a-f0-9]{64}', pin['imageId'])) or not isinstance(pin.get('imageBytes', 0), (int, float)) or pin.get('imageBytes', 0) < 0:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise BackupError('Référence d’image de sauvegarde invalide : nettoyage suspendu.') from None
    return value['items']


def protected_images(root, app_id, api):
    """Called before image pruning. A controller/manifest failure must stop pruning."""
    _id(app_id)
    pins = _manifest(root)['backups'].values()
    result = {reference for pin in pins if pin['appId'] == app_id for reference in [pin['imageId'], pin['imageRef']]}
    for pin in pins:
        if pin['appId'] == app_id: result.update(_images(pin.get('images')).values())
    # The controller includes queued backups, closing the enqueue/prune race.
    for pin in _pins(api, app_id):
        if pin['appId'] != app_id:
            raise BackupError('Le contrôleur a renvoyé une image d’une autre application.')
        result.update(_images(pin.get('images')).values())
        result.add(pin['imageRef'])
        if pin.get('imageId'):
            result.add(pin['imageId'])
    return result


def read_archive(root, app_id, backup_id, expected_checksum=None):
    filename = _path(root, app_id, backup_id)
    try:
        if not filename.is_file() or filename.stat().st_size > MAX_ARCHIVE:
            raise ValueError()
        raw = filename.read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        if expected_checksum and checksum != expected_checksum:
            raise ValueError()
        value = json.loads(raw)
        if value.get('format') != 'pockethost-web-backup' or value.get('version') != 1 or value.get('appId') != app_id or value.get('id') != backup_id or not re.fullmatch('sha256:[a-f0-9]{64}', value.get('imageId', '')) or not _image(value.get('imageRef'), app_id) or not isinstance(value.get('payloadEncrypted'), str) or hashlib.sha256(value['payloadEncrypted'].encode()).hexdigest() != value.get('payloadSha256'):
            raise ValueError()
        images, sizes = _images(value.get('images')), _sizes(value.get('imageSizes'))
        if images and (value['imageId'] not in images.values() or set(sizes) != set(images.values()) or value.get('totalImageBytes') != sum(sizes.values())): raise ValueError()
        return value, checksum, len(raw)
    except (OSError, ValueError, KeyError, TypeError):
        raise BackupError('Archive absente ou contrôle d’intégrité incorrect.') from None


def create_archive(root, job, api, docker):
    app_id, backup_id = _id(job['appId']), _id(job['id'])
    if not _image(job.get('imageRef'), app_id) or not isinstance(job.get('payloadEncrypted'), str) or len(job['payloadEncrypted']) > 400000:
        raise BackupError('Contenu de sauvegarde invalide.')
    base = _base(root)
    if shutil.disk_usage(base).free < MIN_FREE:
        raise BackupError('Sauvegarde suspendue : moins de 15 Gio d’espace libre.')
    if sum(p.stat().st_size for p in base.rglob('*.phwb') if p.is_file()) > MAX_ARCHIVES:
        raise BackupError('La limite locale de 64 Mio d’archives est atteinte.')
    try:
        image = json.loads(docker(['image', 'inspect', job['imageRef']]))[0]
        image_id, image_bytes = image['Id'], image['Size']
        if not re.fullmatch('sha256:[a-f0-9]{64}', image_id) or not isinstance(image_bytes, int) or image_bytes < 0 or image.get('Config', {}).get('Labels', {}).get('pockethost.web.app') != app_id:
            raise ValueError()
    except Exception:
        raise BackupError('L’image publiée est absente ou n’appartient pas à cette application.') from None
    requested = _images(job.get('images'))
    images, image_sizes = {}, {image_id: image_bytes}
    if requested:
        if image_id not in requested.values(): raise BackupError('L’image frontale manque dans l’inventaire des services.')
        for service, reference in requested.items():
            if reference not in image_sizes:
                try:
                    info = json.loads(docker(['image', 'inspect', reference]))[0]
                    if info.get('Id') != reference or info.get('Config', {}).get('Labels', {}).get('pockethost.web.app') != app_id or type(info.get('Size')) is not int or info['Size'] < 0: raise ValueError()
                    image_sizes[reference] = info['Size']
                except Exception: raise BackupError('Une image auxiliaire est absente ou étrangère à cette application.') from None
            images[service] = reference
    manifest = _manifest(root)
    unique = {}
    for pin in manifest['backups'].values():
        for reference, size in _pin_sizes(pin).items(): unique[reference] = max(unique.get(reference, 0), size)
    for pin in _pins(api):
        for reference, size in _sizes(pin.get('imageSizes')).items(): unique[reference] = max(unique.get(reference, 0), size)
        if pin.get('imageId'): unique[pin['imageId']] = max(unique.get(pin['imageId'], 0), int(pin.get('imageBytes', 0)))
    unique.update(image_sizes)
    if sum(unique.values()) > IMAGE_BUDGET:
        raise BackupError('Les images conservées dépasseraient 6 Gio. Réduisez la rétention ou supprimez une ancienne sauvegarde.')
    archive = {'format': 'pockethost-web-backup', 'version': 1, 'id': backup_id, 'appId': app_id, 'createdAt': job['createdAt'], 'commit': job['commit'], 'imageRef': job['imageRef'], 'imageId': image_id, 'imageBytes': image_bytes, 'payloadEncrypted': job['payloadEncrypted'], 'payloadSha256': hashlib.sha256(job['payloadEncrypted'].encode()).hexdigest()}
    if requested: archive.update(images=images, imageSizes=image_sizes, totalImageBytes=sum(image_sizes.values()))
    raw = _encode(archive)
    if len(raw) > MAX_ARCHIVE:
        raise BackupError('Archive trop volumineuse.')
    filename = _path(root, app_id, backup_id)
    if filename.exists():
        existing, _, _ = read_archive(root, app_id, backup_id)
        if existing != archive:
            raise BackupError('Une archive différente existe déjà pour cette sauvegarde.')
    manifest['backups'][backup_id] = {'appId': app_id, 'imageId': image_id, 'imageRef': job['imageRef'], 'sizeBytes': image_bytes, **({'images': images, 'imageSizes': image_sizes} if requested else {})}
    _atomic(base / 'pins.json', _encode(manifest))
    try:
        if not filename.exists():
            _atomic(filename, raw)
    except Exception:
        if not filename.exists():
            manifest['backups'].pop(backup_id, None)
            _atomic(base / 'pins.json', _encode(manifest))
        raise
    _, checksum, size = read_archive(root, app_id, backup_id)
    return {'imageId': image_id, 'imageBytes': image_bytes, 'archiveBytes': size, 'checksum': checksum, **({'images': images, 'imageSizes': image_sizes, 'totalImageBytes': sum(image_sizes.values())} if requested else {})}


def delete_archive(root, job):
    filename = _path(root, job['appId'], job['id'])
    manifest = _manifest(root)
    if filename.exists():
        # Deletion is allowed even for a corrupt archive, but never follows links.
        if not filename.is_file():
            raise BackupError('Type de fichier de sauvegarde invalide.')
        filename.unlink()
    manifest['backups'].pop(job['id'], None)
    _atomic(_base(root) / 'pins.json', _encode(manifest))


def _ledger_path(root):
    filename = _base(root) / 'attempts.json'
    if filename.resolve() != filename:
        raise BackupError('Registre des tentatives symbolique refusé.')
    return filename


def _ledger(root):
    filename = _ledger_path(root)
    try:
        if not filename.exists() or filename.stat().st_size > 1024**2:
            return {}
        value = json.loads(filename.read_text())
        return {key: item for key, item in value.items() if isinstance(key, str) and re.fullmatch('[a-z0-9]{15}:(create|recover|delete)', key)
                and isinstance(item, dict) and type(item.get('count')) is int and isinstance(item.get('last'), (int, float))} if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}  # A corrupt ledger only loses counts; it never blocks backups.


def _ledger_key(job):
    operation = job.get('operation') if job.get('operation') in ('delete', 'recover') else 'create'
    return _id(job['id']) + ':' + operation


def begin_attempt(root, job):
    """Persist the attempt before running it, so a crash during the job still counts."""
    values = _ledger(root)
    now = time.time()
    values = {key: item for key, item in values.items() if now - item['last'] < LEDGER_KEEP}
    key = _ledger_key(job)
    item = values.get(key) or {'count': 0, 'first': now}
    item.update(count=item['count'] + 1, last=now)
    values[key] = item
    if len(values) > 2000:
        values = dict(sorted(values.items(), key=lambda pair: pair[1]['last'])[-2000:])
    _atomic(_ledger_path(root), _encode(values))
    return item['count']


def end_attempt(root, job):
    values = _ledger(root)
    if values.pop(_ledger_key(job), None) is not None:
        _atomic(_ledger_path(root), _encode(values))


def attempt_limit(job):
    value = job.get('maxAttempts')
    return value if type(value) is int and 1 <= value <= 10 else MAX_ATTEMPTS


def tick(root, api, docker, force=False):
    key = str(pathlib.Path(root).absolute())
    if not force and time.monotonic() - _last_tick.get(key, 0) < 30:
        return None
    if not _tick_lock.acquire(blocking=False):
        return None
    try:
        _last_tick[key] = time.monotonic()
        job = api('backups/claim', {}).get('job')
        if not job:
            return None
        try:
            attempts = begin_attempt(root, job)
        except Exception:
            attempts = 0  # Ledger unavailable: fall back to the controller count.
        controller = job.get('attempt')
        attempts = max(attempts, controller if type(controller) is int and 0 <= controller <= 1000 else 0)
        limit = attempt_limit(job)
        if attempts > limit:
            result = {'status': 'failed', 'failureCode': 'attempts_exhausted', 'attempts': attempts,
                      'error': f'Sauvegarde abandonnée après {limit} tentatives interrompues (arrêt ou redémarrage du worker pendant le traitement). Vérifiez la mémoire et le disque du serveur, puis relancez-la.'}
            if job.get('operation') not in ('delete', 'recover'):
                try:
                    delete_archive(root, job)  # A failed backup must not keep a partial archive or image pin.
                except Exception:
                    pass
            api('backups/report', {'id': job['id'], 'lease': job['lease'], **result})
            try:
                end_attempt(root, job)  # A later manual retry or deletion pass starts a fresh budget.
            except Exception:
                pass
            return {'id': job['id'], 'status': 'failed', 'failureCode': 'attempts_exhausted'}
        try:
            if job.get('operation') == 'delete':
                from web_app_backup_s3 import delete_remote_backup
                remote = delete_remote_backup(root, job, api, docker) or {'s3State': 'deleted'}
                delete_archive(root, job)
                result = {'status': 'deleted', **remote}
            elif job.get('operation') == 'recover':
                from web_app_backup_s3 import ensure_backup_archive
                result = {'status': 'ready', **ensure_backup_archive(root, job, docker, api=api)}
                archive, checksum, archive_bytes = read_archive(root, job['appId'], job['id'], job['checksum'])
                if archive['payloadEncrypted'] != job['payloadEncrypted']:
                    raise BackupError('Le contenu récupéré ne correspond pas à cette sauvegarde.')
                manifest = _manifest(root)
                images, sizes = _images(archive.get('images')), _sizes(archive.get('imageSizes'))
                manifest['backups'][job['id']] = {'appId': job['appId'], 'imageId': archive['imageId'], 'imageRef': archive['imageRef'], 'sizeBytes': archive['imageBytes'], **({'images': images, 'imageSizes': sizes} if images else {})}
                _atomic(_base(root) / 'pins.json', _encode(manifest))
                result.update(imageId=archive['imageId'], imageBytes=archive['imageBytes'], checksum=checksum, archiveBytes=archive_bytes)
                if images: result.update(images=images, imageSizes=sizes, totalImageBytes=sum(sizes.values()))
            else:
                local = create_archive(root, job, api, docker)
                result = {'status': 'ready', **local}
        except Exception as error:
            result = {'status': 'failed', 'failureCode': 'backup_error' if isinstance(error, BackupError) else 'engine_error', 'attempts': attempts,
                      'error': str(error) if isinstance(error, BackupError) else 'Échec de la sauvegarde locale. Vérifiez les ressources du moteur.'}
        if result['status'] == 'ready' and job.get('operation') != 'recover':
            # Losing controller contact must not downgrade a completed local archive.
            api('backups/report', {'id': job['id'], 'lease': job['lease'], 'status': 'running', 's3State': 'uploading', **local})
            try:
                from web_app_backup_s3 import mirror_backup
                remote = mirror_backup(root, job, local, docker, api=api)
            except Exception:
                remote = {'s3State': 'failed', 's3CheckedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(), 's3Error': 'La copie S3 a échoué. La sauvegarde locale reste disponible ; vérifiez la configuration et les droits du bucket.'}
            result.update(remote)
        # A lost acknowledgement keeps the archive pinned and the lease recoverable.
        api('backups/report', {'id': job['id'], 'lease': job['lease'], **result})
        try:
            end_attempt(root, job)
        except Exception:
            pass
        return {'id': job['id'], 'status': result['status']}
    finally:
        _tick_lock.release()
