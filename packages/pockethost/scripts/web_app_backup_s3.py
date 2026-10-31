"""Private S3 mirrors: encrypted metadata archives and streaming AES-GCM Docker images.

No bucket listing, host shell, image temporary file or container start is performed.
Restore authenticates a full first GET before Docker LOAD; the second GET is pinned
with IfMatch to that same object, then the imported immutable ID is checked again.
"""
import datetime
import hashlib
import hmac
import json
import os
import pathlib
import re
import signal
import shutil
import stat
import subprocess
import threading
import time
import urllib.parse
import zlib

CHUNK = 64 * 1024
PART = 8 * 1024**2
MAX_COMPRESSED = 2 * 1024**3
MAX_TAR = 8 * 1024**3
MAX_ARCHIVE = 512 * 1024
MIN_FREE = 15 * 1024**3
DEADLINE = 1800
MAGIC = b'PHWI1'
ID = re.compile(r'^[a-z0-9]{15}$')
IMAGE = re.compile(r'^sha256:[a-f0-9]{64}$')


class BackupS3Error(RuntimeError):
    pass


def _stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')


def _settings():
    names = ['ENDPOINT', 'BUCKET', 'ACCESS_KEY_ID', 'SECRET_ACCESS_KEY', 'IMAGE_KEY']
    values = {key: os.environ.get('WEB_BACKUPS_S3_' + key, '') for key in names}
    if not any(values.values()):
        return None
    try:
        url = urllib.parse.urlsplit(values['ENDPOINT'])
        if (url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment
            or url.path not in ('', '/') or url.port not in (None, 443)
            or not re.fullmatch(r'[A-Za-z0-9.-]+', url.hostname) or '.' not in url.hostname):
            raise ValueError()
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', values['BUCKET']):
            raise ValueError()
        if not re.fullmatch(r'[a-fA-F0-9]{64}', values['IMAGE_KEY']):
            raise ValueError()
        if any(not values[key] or len(values[key]) > 512 or any(char in values[key] for char in '\r\n\x00') for key in ('ACCESS_KEY_ID', 'SECRET_ACCESS_KEY')):
            raise ValueError()
        # No default: several hosts share one bucket, each in its own directory (app1/…, app2/…).
        # A missing prefix must never silently write into another host's folder.
        prefix = os.environ.get('WEB_BACKUPS_S3_PREFIX', '')
        if not prefix:
            raise BackupS3Error('WEB_BACKUPS_S3_PREFIX est requis : un répertoire propre à cet hôte, par exemple app2/web-app-backups.')
        region = os.environ.get('WEB_BACKUPS_S3_REGION', 'gra')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/_-]{0,199}', prefix) or '//' in prefix or prefix.endswith('/') or not re.fullmatch(r'[a-z0-9-]{1,64}', region):
            raise ValueError()
        return {**values, 'ENDPOINT': values['ENDPOINT'].rstrip('/'), 'PREFIX': prefix, 'REGION': region, 'KEY': bytes.fromhex(values['IMAGE_KEY'])}
    except (ValueError, KeyError, TypeError):
        raise BackupS3Error('Configuration S3 des sauvegardes incomplète ou invalide.') from None


def _client(config):
    try:
        import boto3
        from botocore.config import Config
        return boto3.client('s3', endpoint_url=config['ENDPOINT'], region_name=config['REGION'],
            aws_access_key_id=config['ACCESS_KEY_ID'], aws_secret_access_key=config['SECRET_ACCESS_KEY'],
            config=Config(signature_version='s3v4', connect_timeout=5, read_timeout=15,
                retries={'max_attempts': 1}, s3={'addressing_style': 'path'}, proxies={}))
    except Exception:
        raise BackupS3Error('Le client S3 des sauvegardes est indisponible.') from None


def _identifiers(app_id, backup_id=None, image_id=None):
    if not isinstance(app_id, str) or not ID.fullmatch(app_id):
        raise BackupS3Error('Application de sauvegarde invalide.')
    if backup_id is not None and (not isinstance(backup_id, str) or not ID.fullmatch(backup_id)):
        raise BackupS3Error('Identifiant de sauvegarde invalide.')
    if image_id is not None and (not isinstance(image_id, str) or not IMAGE.fullmatch(image_id)):
        raise BackupS3Error('Identifiant immuable de l’image invalide.')


def capture_prefix(config):
    """After a failover WEB_BACKUPS_S3_PREFIX is a platform-recovery capture's
    `<recovery prefix>/<captureId>/refs/<group>` directory (WEB_RECOVERY_RUNBOOK §5.10). Its objects
    belong to that capture's manifest: retention and manual deletion drop the row and the local
    archive but never delete them (a second restore from the same capture must stay possible)."""
    return bool(re.search(r'/refs/[a-z0-9][a-z0-9-]{0,40}$', config['PREFIX']))


def _keys(config, app_id, backup_id=None, image_id=None):
    _identifiers(app_id, backup_id, image_id)
    base = config['PREFIX'] + '/apps/' + app_id
    return (base + '/archives/' + backup_id + '.phwb' if backup_id else '',
            base + '/images/' + image_id[7:] + '.phwi' if image_id else '')


def _image_metadata(config, app_id, image_id):
    return {'format': 'PHWI1', 'app-id': app_id, 'image-id': image_id,
            'key-id': hashlib.sha256(config['KEY']).hexdigest()[:24]}


BUCKET_UNAVAILABLE = 'Le bucket S3 des sauvegardes est introuvable ou inaccessible. Vérifiez WEB_BACKUPS_S3_BUCKET.'


def _missing(error, client=None, config=None):
    """True only for an object that is really absent from a bucket that answers.

    A mistyped, deleted or no longer accessible bucket also answers HEAD object with a bare 404. Trusting it would skip
    an upload as "already there" or mark a copy deleted while its objects stay orphaned, so every 404 is confirmed with
    head_bucket at the moment it is seen (the bucket can vanish in the middle of a job); otherwise this raises."""
    response = getattr(error, 'response', None)
    code = (response.get('Error') or {}).get('Code') if isinstance(response, dict) else None
    if code == 'NoSuchBucket':
        raise BackupS3Error(BUCKET_UNAVAILABLE) from None
    if code not in ('404', 'NoSuchKey', 'NotFound'):
        return False
    if client is None or config is None:
        raise BackupS3Error(BUCKET_UNAVAILABLE) from None
    _require_bucket(client, config)
    return True


def _require_bucket(client, config):
    """A missing bucket answers HEAD object with a bare 404, like a missing key. Confirm the
    bucket once per job before any 'absent' answer is trusted (skip upload, mark deleted)."""
    try:
        client.head_bucket(Bucket=config['BUCKET'])
    except Exception:
        raise BackupS3Error(BUCKET_UNAVAILABLE) from None


def _head(client, config, key, expected, absent=False):
    try:
        value = client.head_object(Bucket=config['BUCKET'], Key=key)
    except Exception as error:
        if absent and _missing(error, client, config):
            return None
        raise BackupS3Error('Le stockage S3 ne confirme pas l’objet demandé.') from None
    metadata = value.get('Metadata') or {}
    size, etag = value.get('ContentLength'), value.get('ETag')
    if (not all(metadata.get(name) == content for name, content in expected.items())
        or type(size) is not int or size < 1 or not isinstance(etag, str) or not 1 <= len(etag) <= 200
        or any(char in etag for char in '\r\n\x00')):
        raise BackupS3Error('Les métadonnées de la sauvegarde distante ne correspondent pas.')
    return value


def _root(root):
    root = pathlib.Path(root).absolute()
    if root.resolve() != root:
        raise BackupS3Error('Répertoire de sauvegarde symbolique refusé.')
    return root


def _archive(root, job, result):
    app_id, backup_id, image_id = job['appId'], job['id'], result['imageId']
    _identifiers(app_id, backup_id, image_id)
    path = _root(root) / 'backups' / app_id / (backup_id + '.phwb')
    try:
        if path.resolve() != path or not path.is_file() or path.stat().st_size > MAX_ARCHIVE:
            raise ValueError()
        raw = path.read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        value = json.loads(raw)
        if (checksum != result['checksum'] or value.get('format') != 'pockethost-web-backup' or value.get('version') != 1
            or value.get('appId') != app_id or value.get('id') != backup_id or value.get('imageId') != image_id
            or not isinstance(value.get('payloadEncrypted'), str)
            or hashlib.sha256(value['payloadEncrypted'].encode()).hexdigest() != value.get('payloadSha256')):
            raise ValueError()
        return raw, checksum
    except (OSError, ValueError, TypeError, KeyError):
        raise BackupS3Error('L’archive locale ne passe pas le contrôle d’intégrité.') from None


def _inspect(docker, image_id, app_id, missing=False):
    _identifiers(app_id, image_id=image_id)
    try:
        raw = docker(['image', 'inspect', image_id], timeout=10)
    except subprocess.CalledProcessError:
        if missing:
            return False
        raise BackupS3Error('L’image immuable locale est indisponible.') from None
    except Exception:
        raise BackupS3Error('Le moteur d’images web est indisponible.') from None
    try:
        if not isinstance(raw, str) or len(raw) > 2 * 1024**2:
            raise ValueError()
        rows = json.loads(raw)
        labels = rows[0].get('Config', {}).get('Labels') or {}
        if len(rows) != 1 or rows[0].get('Id') != image_id or labels.get('pockethost.web') != 'true' or labels.get('pockethost.web.app') != app_id:
            raise ValueError()
        return True
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise BackupS3Error('L’image ne correspond pas à cette application.') from None


def _docker_command(action, image_id=None):
    socket = os.environ.get('WEB_APPS_DOCKER_SOCKET', '')
    match = re.fullmatch(r'/run/user/([1-9][0-9]*)/docker\.sock', socket)
    try:
        if not match or os.getuid() == 0 or os.getuid() != int(match[1]) or pathlib.Path(socket).resolve() != pathlib.Path(socket) or not stat.S_ISSOCK(os.stat(socket).st_mode):
            raise ValueError()
    except (OSError, ValueError):
        raise BackupS3Error('Le socket Docker rootless dédié est requis.') from None
    if action not in ('save', 'load') or (action == 'save' and not IMAGE.fullmatch(image_id or '')):
        raise BackupS3Error('Opération d’image invalide.')
    return ['/usr/bin/docker', '--host', 'unix://' + socket, 'image', action] + ([image_id] if action == 'save' else [])


def _kill(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        except OSError:
            pass


class _Deadline:
    def __init__(self, max_seconds=None):
        seconds=DEADLINE if max_seconds is None else max(.1,min(DEADLINE,max_seconds))
        self.end = time.monotonic() + seconds
        self.expired = threading.Event()
        self.processes, self.streams = [], []
        self.timer = threading.Timer(seconds, self._expire)
        self.timer.daemon = True
        self.timer.start()

    def _expire(self):
        self.expired.set()
        for process in self.processes:
            _kill(process)
        for stream in self.streams:
            try: stream.close()
            except Exception: pass

    def check(self):
        if self.expired.is_set() or time.monotonic() >= self.end:
            raise BackupS3Error('Délai maximal du transfert de sauvegarde dépassé.')

    def remaining(self):
        self.check()
        return max(.1, self.end - time.monotonic())

    def close(self):
        self.timer.cancel()
        for stream in self.streams:
            try: stream.close()
            except Exception: pass


class _Heartbeat:
    def __init__(self, api, job):
        self.api, self.job, self.bytes, self.total = api, job, 0, 0
        self.stopped, self.failed = threading.Event(), threading.Event()
        self.thread = None
        if api:
            self._report()
            self.thread = threading.Thread(target=self._run, name='web-backup-s3-heartbeat', daemon=True)
            self.thread.start()

    def _report(self):
        body = {'id': self.job['id'], 'lease': self.job['lease'], 'status': 'running',
            's3State': 'uploading', 's3Bytes': self.bytes, 's3TotalBytes': self.total}
        for name in ('s3Object', 's3ImageObject', 's3ImageObjects'):
            if self.job.get(name): body[name] = self.job[name]
        self.api('backups/report', body)

    def _run(self):
        while not self.stopped.wait(5):
            try: self._report()
            except Exception:
                self.failed.set()
                return

    def check(self):
        if self.failed.is_set():
            raise BackupS3Error('Le contrôleur ne confirme plus le transfert S3.')

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=35)
            if self.thread.is_alive():
                raise BackupS3Error('Le contrôleur ne confirme plus le transfert S3.')


class _Multipart:
    def __init__(self, client, config, key, metadata, deadline, heartbeat):
        self.client, self.config, self.key = client, config, key
        self.deadline, self.heartbeat = deadline, heartbeat
        response = client.create_multipart_upload(Bucket=config['BUCKET'], Key=key, ACL='private', Metadata=metadata, ContentType='application/octet-stream')
        self.upload_id = response['UploadId']
        self.parts, self.buffer, self.size, self.uploaded, self.completed = [], bytearray(), 0, 0, False

    def write(self, data):
        self.deadline.check(); self.heartbeat.check()
        self.size += len(data)
        if self.size > MAX_COMPRESSED:
            raise BackupS3Error('L’image compressée dépasse la limite de 2 Gio.')
        self.buffer.extend(data)
        while len(self.buffer) >= PART:
            value = bytes(self.buffer[:PART]); del self.buffer[:PART]
            self._part(value)

    def _part(self, data):
        self.deadline.check(); self.heartbeat.check()
        response = self.client.upload_part(Bucket=self.config['BUCKET'], Key=self.key, UploadId=self.upload_id,
            PartNumber=len(self.parts) + 1, Body=data)
        self.parts.append({'PartNumber': len(self.parts) + 1, 'ETag': response['ETag']})
        self.uploaded += len(data)
        self.heartbeat.bytes = getattr(self.heartbeat,'offset',0) + self.uploaded

    def complete(self):
        if self.buffer:
            self._part(bytes(self.buffer)); self.buffer.clear()
        self.deadline.check(); self.heartbeat.check()
        self.client.complete_multipart_upload(Bucket=self.config['BUCKET'], Key=self.key, UploadId=self.upload_id, MultipartUpload={'Parts': self.parts})
        self.completed = True

    def abort(self):
        if not self.completed:
            try: self.client.abort_multipart_upload(Bucket=self.config['BUCKET'], Key=self.key, UploadId=self.upload_id)
            except Exception: pass


def _aad(app_id, image_id):
    return MAGIC + b'\x00' + app_id.encode() + b'\x00' + image_id.encode()


def _upload_image(root, config, client, key, app_id, image_id, deadline, heartbeat):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    metadata = _image_metadata(config, app_id, image_id)
    existing = _head(client, config, key, metadata, absent=True)
    if existing:
        if existing['ContentLength'] > MAX_COMPRESSED:
            raise BackupS3Error('La copie distante de l’image dépasse la limite autorisée.')
        return 0
    writer, process = None, None
    try:
        writer = _Multipart(client, config, key, metadata, deadline, heartbeat)
        nonce = os.urandom(12)
        encryptor = Cipher(algorithms.AES(config['KEY']), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(_aad(app_id, image_id))
        compressor = zlib.compressobj(level=6, wbits=31)
        writer.write(MAGIC + nonce)
        process = subprocess.Popen(_docker_command('save', image_id), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True, cwd=_root(root), env={'PATH': '/usr/local/bin:/usr/bin:/bin'})
        deadline.processes.append(process)
        tar_bytes = 0
        while True:
            deadline.check(); heartbeat.check()
            chunk = process.stdout.read(CHUNK)
            if not chunk: break
            tar_bytes += len(chunk)
            if tar_bytes > MAX_TAR:
                raise BackupS3Error('L’image exportée dépasse la limite autorisée.')
            writer.write(encryptor.update(compressor.compress(chunk)))
        if process.wait(timeout=deadline.remaining()) != 0:
            raise BackupS3Error('L’export de l’image Docker a échoué.')
        writer.write(encryptor.update(compressor.flush()))
        writer.write(encryptor.finalize() + encryptor.tag)
        writer.complete()
        head = _head(client, config, key, metadata)
        if head['ContentLength'] != writer.size:
            raise BackupS3Error('La taille de l’image distante ne correspond pas.')
        return writer.size
    finally:
        if process:
            _kill(process)
            if process.stdout: process.stdout.close()
        if writer: writer.abort()


def _runtime_images(value, front):
    if value is None or value == {}:
        return {}
    if not isinstance(value,dict) or len(value)>4 or any(not isinstance(name,str) or not re.fullmatch('[a-z][a-z0-9-]{0,30}',name) or not isinstance(image,str) or not IMAGE.fullmatch(image) for name,image in value.items()) or front not in value.values():
        raise BackupS3Error('Images de services invalides ou image frontale absente.')
    return value


def mirror_backup(root, job, result, docker, api=None):
    config = _settings()
    if not config:
        return {'s3State': 'disabled', 's3Object': '', 's3ImageObject': '', 's3CheckedAt': '', 's3Error': ''}
    deadline, heartbeat = _Deadline(), None
    try:
        raw, checksum = _archive(root, job, result)
        app_id, image_id = job['appId'], result['imageId']
        images=_runtime_images(json.loads(raw).get('images'),image_id)
        if result.get('images') is not None and result['images']!=images:raise BackupS3Error('Les images de l’archive ne correspondent pas au résultat local.')
        for reference in set([image_id,*images.values()]):_inspect(docker,reference,app_id)
        archive_key, image_key = _keys(config, app_id, job['id'], image_id)
        client = _client(config)
        _require_bucket(client, config)
        objects={reference:_keys(config,app_id,image_id=reference)[1]for reference in set([image_id,*images.values()])}
        heartbeat = _Heartbeat(api, {**job, 's3Object': archive_key, 's3ImageObject': image_key, **({'s3ImageObjects':objects}if images else{})})
        image_bytes=0
        for reference in sorted(objects):
            heartbeat.offset=image_bytes
            image_bytes += _upload_image(root, config, client, objects[reference], app_id, reference, deadline, heartbeat)
        heartbeat.total = image_bytes + len(raw)
        metadata = {'format': 'PHWB1', 'app-id': app_id, 'backup-id': job['id'], 'sha256': checksum}
        if not _head(client, config, archive_key, metadata, absent=True):
            deadline.check(); heartbeat.check()
            client.put_object(Bucket=config['BUCKET'], Key=archive_key, ACL='private', Metadata=metadata,
                ContentType='application/json', ContentLength=len(raw), Body=raw)
        head = _head(client, config, archive_key, metadata)
        if head['ContentLength'] != len(raw):
            raise BackupS3Error('La taille de l’archive distante ne correspond pas.')
        heartbeat.bytes = heartbeat.total
        deadline.check(); heartbeat.check()
        return {'s3State': 'ready', 's3Object': archive_key, 's3ImageObject': image_key, 's3CheckedAt': _stamp(), 's3Error': '', 's3Bytes': heartbeat.bytes, 's3TotalBytes': heartbeat.total, **({'s3ImageObjects':objects}if images else{})}
    except BackupS3Error:
        raise
    except Exception:
        raise BackupS3Error('La copie S3 a échoué. La sauvegarde locale est conservée.') from None
    finally:
        deadline.close()
        if heartbeat: heartbeat.close()


def _download(client, config, key, head, metadata, deadline):
    response = client.get_object(Bucket=config['BUCKET'], Key=key, IfMatch=head['ETag'])
    body = response['Body']; deadline.streams.append(body)
    if response.get('ETag') != head['ETag'] or response.get('ContentLength') != head['ContentLength'] or not all((response.get('Metadata') or {}).get(name) == value for name, value in metadata.items()):
        body.close()
        raise BackupS3Error('La copie distante a changé pendant sa lecture.')
    return body


def _decrypt(body, config, app_id, image_id, size, deadline):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    header = bytearray()
    while len(header) < 17:
        deadline.check()
        chunk = body.read(17 - len(header))
        if not chunk: raise BackupS3Error('En-tête d’image chiffrée incomplet.')
        header.extend(chunk)
    if header[:5] != MAGIC:
        raise BackupS3Error('Format d’image chiffrée invalide.')
    decryptor = Cipher(algorithms.AES(config['KEY']), modes.GCM(bytes(header[5:]))).decryptor()
    decryptor.authenticate_additional_data(_aad(app_id, image_id))
    total, tail = 17, b''
    while True:
        deadline.check()
        chunk = body.read(CHUNK)
        if not chunk: break
        total += len(chunk)
        if total > size or total > MAX_COMPRESSED:
            raise BackupS3Error('Taille de l’image chiffrée invalide.')
        pending = tail + chunk
        if len(pending) > 16:
            yield decryptor.update(pending[:-16])
            tail = pending[-16:]
        else: tail = pending
    if total != size or len(tail) != 16:
        raise BackupS3Error('Image chiffrée tronquée.')
    yield decryptor.finalize_with_tag(tail)
    deadline.check()


def _inflate(chunks, deadline):
    """Validate gzip and cap every yielded allocation before any Docker import."""
    decoder, total = zlib.decompressobj(wbits=31), 0
    for compressed in chunks:
        pending = compressed
        while pending:
            deadline.check()
            data = decoder.decompress(pending, CHUNK)
            pending = decoder.unconsumed_tail
            total += len(data)
            if total > MAX_TAR or decoder.unused_data:
                raise BackupS3Error('Contenu de l’image compressée invalide ou trop volumineux.')
            if data:
                yield data
    if not decoder.eof or total == 0:
        raise BackupS3Error('Image compressée incomplète ou vide.')


def _require_space(root, required):
    try:
        available = shutil.disk_usage(root).free
    except OSError:
        raise BackupS3Error('Impossible de vérifier l’espace disponible avant l’import.') from None
    if available < required:
        raise BackupS3Error('Import suspendu : la réserve de 15 Gio et l’espace requis pour l’image ne sont pas disponibles.')


class _LoadDiskGuard:
    """Watch disk even while Docker LOAD or a pipe/network write is blocked."""
    def __init__(self, root, process):
        self.root, self.process = root, process
        self.failed, self.stopped = threading.Event(), threading.Event()
        self.lock, self.bytes = threading.Lock(), 0
        self.thread = threading.Thread(target=self._run, name='web-backup-import-disk', daemon=True)
        self.thread.start()

    def _sample(self):
        with self.lock:
            try:
                _require_space(self.root, MIN_FREE)
            except Exception:
                self.failed.set()
        if self.failed.is_set():
            _kill(self.process)

    def _run(self):
        while not self.stopped.wait(1):
            self._sample()
            if self.failed.is_set():
                return

    def check(self, written=0, force=False):
        self.bytes += written
        if force or self.bytes >= PART:
            self.bytes = 0
            self._sample()
        if self.failed.is_set():
            raise BackupS3Error('Import interrompu : moins de 15 Gio d’espace libre restent disponibles.')

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=3)


def ensure_backup_image(root, imageId, appId, docker, max_seconds=None):
    _identifiers(appId, image_id=imageId)
    if _inspect(docker, imageId, appId, missing=True):
        return imageId
    config = _settings()
    if not config:
        raise BackupS3Error('L’image est absente du serveur et aucune copie S3 n’est configurée.')
    root = _root(root)
    deadline, process, disk_guard = _Deadline(max_seconds), None, None
    try:
        client = _client(config)
        _, key = _keys(config, appId, image_id=imageId)
        metadata = _image_metadata(config, appId, imageId)
        head = _head(client, config, key, metadata)
        if not 33 < head['ContentLength'] <= MAX_COMPRESSED:
            raise BackupS3Error('Taille de l’image distante invalide.')
        # Authenticate all ciphertext, validate gzip, and count bounded tar output
        # before a second GET or Docker LOAD can allocate disk space.
        body = _download(client, config, key, head, metadata, deadline)
        tar_bytes = 0
        try:
            decrypted = _decrypt(body, config, appId, imageId, head['ContentLength'], deadline)
            for data in _inflate(decrypted, deadline):
                tar_bytes += len(data)
        finally:
            body.close()
        _require_space(root, MIN_FREE + 2 * tar_bytes)
        # IfMatch and the response ETag pin this read to the authenticated object.
        body = _download(client, config, key, head, metadata, deadline)
        try:
            _require_space(root, MIN_FREE)
            process = subprocess.Popen(_docker_command('load'), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True, cwd=root, env={'PATH': '/usr/local/bin:/usr/bin:/bin'})
            deadline.processes.append(process)
            disk_guard = _LoadDiskGuard(root, process)
            decrypted = _decrypt(body, config, appId, imageId, head['ContentLength'], deadline)
            for data in _inflate(decrypted, deadline):
                disk_guard.check(len(data))
                process.stdin.write(data)
            disk_guard.check(force=True)
            process.stdin.close()
            while True:
                disk_guard.check(force=True)
                try:
                    code = process.wait(timeout=min(1, deadline.remaining()))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if code != 0:
                raise BackupS3Error('L’import de l’image Docker a échoué.')
            disk_guard.check(force=True)
        finally:
            body.close()
        _inspect(docker, imageId, appId)
        return imageId
    except BackupS3Error:
        raise
    except Exception:
        raise BackupS3Error('La récupération de l’image S3 ou son contrôle d’intégrité a échoué.') from None
    finally:
        if disk_guard:
            disk_guard.close()
        deadline.close()
        if process:
            _kill(process)
            if process.stdin and not process.stdin.closed:
                process.stdin.close()


def delete_remote_backup(root, job, api, docker):
    _root(root)
    app_id, backup_id = job.get('appId'), job.get('id')
    _identifiers(app_id, backup_id)
    archive_object, image_object = job.get('s3Object') or '', job.get('s3ImageObject') or ''
    config = _settings()
    if not config:
        if archive_object or image_object:
            raise BackupS3Error('La configuration S3 est requise avant de supprimer cette copie distante.')
        return {'s3State': 'deleted', 's3CheckedAt': _stamp(), 's3Error': ''}
    image_id = job.get('imageId')
    if not image_id and not archive_object and not image_object:
        return {'s3State': 'deleted', 's3CheckedAt': _stamp(), 's3Error': ''}
    _identifiers(app_id, backup_id, image_id)
    expected_archive, expected_image = _keys(config, app_id, backup_id, image_id)
    if (archive_object and archive_object != expected_archive) or (image_object and image_object != expected_image):
        raise BackupS3Error('Suppression d’un objet hors de cette sauvegarde refusée.')
    # A transfer may fail after upload but before its final acknowledgement. Exact
    # deterministic keys also find such partial mirrors without listing the bucket.
    archive_object, image_object = expected_archive, expected_image
    images=_runtime_images(job.get('images'),image_id)
    objects={reference:_keys(config,app_id,image_id=reference)[1]for reference in set([image_id,*images.values()])}
    provided=job.get('s3ImageObjects')or{}
    if not isinstance(provided,dict)or len(provided)>4 or any(reference not in objects or key!=objects[reference]for reference,key in provided.items()):
        raise BackupS3Error('Références S3 des services invalides.')
    if capture_prefix(config):
        return {'s3State': 'deleted', 's3CheckedAt': _stamp(), 's3Error': ''}
    pins = api('backups/pins', {'appId': app_id})
    if not isinstance(pins, dict) or not isinstance(pins.get('items'), list) or len(pins['items']) > 10000:
        raise BackupS3Error('Le contrôleur ne confirme pas les images encore utilisées.')
    protected=set()
    for pin in pins['items']:
        if (not isinstance(pin, dict) or not isinstance(pin.get('id'), str) or not ID.fullmatch(pin['id']) or pin.get('appId') != app_id
            or not isinstance(pin.get('imageRef'), str) or not (IMAGE.fullmatch(pin['imageRef']) or re.fullmatch('ph-web/' + app_id + ':[a-z0-9]{15}', pin['imageRef']))
            or (pin.get('imageId') and (not isinstance(pin['imageId'], str) or not IMAGE.fullmatch(pin['imageId'])))):
            raise BackupS3Error('Référence de conservation invalide.')
        extra=pin.get('images')or{}
        if not isinstance(extra,dict)or len(extra)>4 or any(not isinstance(value,str)or not IMAGE.fullmatch(value)for value in extra.values()):raise BackupS3Error('Images de conservation invalides.')
        if pin['id']!=backup_id:
            protected.update(extra.values())
            if pin.get('imageId'):protected.add(pin['imageId'])
            if pin.get('imageRef')==job.get('imageRef'):protected.add(image_id)
    client = _client(config)
    _require_bucket(client, config)
    try:
        if archive_object and _head(client, config, archive_object, {'format': 'PHWB1', 'app-id': app_id, 'backup-id': backup_id}, absent=True):
            client.delete_object(Bucket=config['BUCKET'], Key=archive_object)
        for reference,key in objects.items():
            if reference not in protected and _head(client, config, key, _image_metadata(config, app_id, reference), absent=True):
                client.delete_object(Bucket=config['BUCKET'], Key=key)
    except BackupS3Error:
        raise
    except Exception:
        raise BackupS3Error('La suppression S3 a échoué. La copie locale est conservée.') from None
    return {'s3State': 'deleted', 's3CheckedAt': _stamp(), 's3Error': ''}


def ensure_backup_archive(root, job, docker, api=None):
    """Recover only the small metadata archive; no Docker image is read or loaded."""
    app_id, backup_id, image_id = job.get('appId'), job.get('id'), job.get('imageId')
    _identifiers(app_id, backup_id, image_id)
    config = _settings()
    if not config:
        raise BackupS3Error('La configuration S3 est requise pour récupérer cette archive.')
    archive_key, image_key = _keys(config, app_id, backup_id, image_id)
    if job.get('s3Object') != archive_key or (job.get('s3ImageObject') and job['s3ImageObject'] != image_key):
        raise BackupS3Error('L’archive distante ne correspond pas à cette sauvegarde.')
    checksum, expected_payload = job.get('checksum'), job.get('payloadEncrypted')
    if (not isinstance(checksum, str) or not re.fullmatch('[a-f0-9]{64}', checksum)
        or not isinstance(expected_payload, str) or not expected_payload or len(expected_payload) > 400000
        or type(job.get('archiveBytes')) is not int or not 0 < job['archiveBytes'] <= MAX_ARCHIVE
        or type(job.get('imageBytes')) is not int or job['imageBytes'] < 0):
        raise BackupS3Error('Les références de récupération de l’archive sont invalides.')
    directory = _root(root) / 'backups' / app_id
    filename = directory / (backup_id + '.phwb')
    if filename.resolve() != filename:
        raise BackupS3Error('Archive symbolique refusée.')
    deadline, heartbeat, temporary = _Deadline(), None, None
    try:
        client = _client(config)
        metadata = {'format': 'PHWB1', 'app-id': app_id, 'backup-id': backup_id, 'sha256': checksum}
        head = _head(client, config, archive_key, metadata)
        if head['ContentLength'] != job['archiveBytes'] or head['ContentLength'] > MAX_ARCHIVE:
            raise BackupS3Error('La taille de l’archive distante ne correspond pas.')
        heartbeat = _Heartbeat(api, job)
        heartbeat.total = head['ContentLength']
        body = _download(client, config, archive_key, head, metadata, deadline)
        try:
            data = bytearray()
            while True:
                deadline.check(); heartbeat.check()
                chunk = body.read(CHUNK)
                if not chunk: break
                data.extend(chunk)
                if len(data) > MAX_ARCHIVE or len(data) > head['ContentLength']:
                    raise BackupS3Error('L’archive distante dépasse la taille autorisée.')
                heartbeat.bytes = len(data)
        finally: body.close()
        raw = bytes(data)
        if len(raw) != job['archiveBytes'] or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), checksum):
            raise BackupS3Error('Le contrôle d’intégrité de l’archive distante a échoué.')
        value = json.loads(raw)
        reference = value.get('imageRef', '')
        if (value.get('format') != 'pockethost-web-backup' or value.get('version') != 1
            or value.get('id') != backup_id or value.get('appId') != app_id or value.get('imageId') != image_id
            or value.get('imageBytes') != job['imageBytes'] or not isinstance(reference, str)
            or not (IMAGE.fullmatch(reference) or re.fullmatch('ph-web/' + app_id + ':[a-z0-9]{15}', reference))
            or (job.get('imageRef') and reference != job['imageRef'])
            or not isinstance(value.get('payloadEncrypted'), str)
            or not hmac.compare_digest(hashlib.sha256(value['payloadEncrypted'].encode()).hexdigest(), hashlib.sha256(expected_payload.encode()).hexdigest())
            or value.get('payloadSha256') != hashlib.sha256(value['payloadEncrypted'].encode()).hexdigest()):
            raise BackupS3Error('Le contenu de l’archive distante ne correspond pas à cette application.')
        directory.mkdir(mode=0o750, parents=True, exist_ok=True)
        if directory.resolve() != directory or filename.resolve() != filename:
            raise BackupS3Error('Répertoire de récupération symbolique refusé.')
        temporary = filename.with_name(filename.name + '.' + os.urandom(12).hex() + '.tmp')
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o640)
        with os.fdopen(fd, 'wb') as output:
            os.fchmod(output.fileno(), 0o640)
            output.write(raw); output.flush(); os.fsync(output.fileno())
        deadline.check(); heartbeat.check()
        images=_runtime_images(value.get('images'),image_id)
        os.replace(temporary, filename)
        return {'imageId': image_id, 'imageBytes': job['imageBytes'], 'archiveBytes': len(raw), 'checksum': checksum,
            's3State': 'ready', 's3Object': archive_key, 's3ImageObject': job.get('s3ImageObject') or image_key,
            's3CheckedAt': _stamp(), 's3Error': '', 's3Bytes': len(raw), 's3TotalBytes': len(raw), **({'images':images,'imageSizes':value.get('imageSizes',{}),'totalImageBytes':value.get('totalImageBytes',0),'s3ImageObjects':job.get('s3ImageObjects')or{ref:_keys(config,app_id,image_id=ref)[1]for ref in set(images.values())}}if images else{})}
    except BackupS3Error:
        raise
    except Exception:
        raise BackupS3Error('La récupération de l’archive S3 a échoué. Aucun contenu non vérifié n’a été installé.') from None
    finally:
        deadline.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if heartbeat: heartbeat.close()
