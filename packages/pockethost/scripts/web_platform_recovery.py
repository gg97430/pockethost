#!/usr/bin/env python3
"""Streaming, authenticated PocketHost recovery captures. Restore is always isolated and paused.

Plan (private JSON): {version:1, platform:"app1", controller:{database:".../data.db",
storage:".../storage"}, instances:[{id:"15chars",database:".../data.db",storage:"..."}],
hostPaths:[{source:"/etc/private.env",target:"configuration/private.env"}],
nativeBackups:[{source:"...tar.gz",target:"id.tar.gz",snapshotAt:"...",checksum:"..."}],
s3Refs:[{key:"app1/web-app-backups/...",prefix:"app1/web-app-backups",group:"web-backups"}],
metadata:{versions:{...}}}. Omit optional lists when empty. All databases are captured
fresh in read-only SQLite transactions; provided native archives retain their own dates.
An instance marked withoutDatabase:true (generated plans, never-started instance) is
reported in coverage.instancesWithoutDatabase only while its file is absent.
`inspect` reports restoreEstimate (compressed + materialised bytes) and, with --target
and the restore options, whether that selection fits beside the disk reserve.

No service is stopped or started. No hooks, shell commands, emails or remote services
are executed by restore. A restored PocketBase must additionally run with a disabled
hooks directory AND an OS network namespace (systemd PrivateNetwork=true).
"""
import argparse
import base64
import datetime
import functools
import hashlib
import io
import json
import os
import pathlib
import re
import secrets
import shutil
import signal
import sqlite3
import stat
import struct
import sys
import tarfile
import threading
import time
import urllib.parse
import zlib

MAGIC = b'PHPR1'
CHUNK = 64 * 1024
PART = 8 * 1024**2
SQL_PART = 8 * 1024**2
MAX_ROW = 64 * 1024**2
MAX_MANIFEST = 128 * 1024**2
MAX_OBJECTS = 250000
MIN_FREE = 15 * 1024**3
MAX_WAL_GROWTH = 512 * 1024**2
MAX_EXPANDED = 512 * 1024**3
ID = re.compile(r'^[a-z0-9]{15}$')
TOTP_SIDECAR = '.pockethost-totp.key'
PAUSE_STATE = 'recovery-pause-state.json'


class RecoveryError(RuntimeError):
    pass


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise RecoveryError('Chemin de restauration invalide.')
    result = pathlib.PurePosixPath(value)
    if result.is_absolute() or any(part in ('', '.', '..') for part in result.parts) or str(result) != value:
        raise RecoveryError('Chemin de restauration hors du répertoire isolé.')
    return value


def private_file(filename, owners=None):
    # A root launcher may check files of the restoring user by passing that uid explicitly.
    filename = pathlib.Path(filename).absolute()
    if filename.resolve() != filename:
        raise RecoveryError('Fichier privé symbolique refusé.')
    info = filename.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid not in (owners or (0, os.geteuid())):
        raise RecoveryError('Le fichier privé doit appartenir à l’administrateur et être en mode 600.')
    return filename


def load_key(filename):
    value = private_file(filename).read_text().strip()
    if not re.fullmatch(r'[a-fA-F0-9]{64}', value):
        raise RecoveryError('Clé de reprise attendue : 64 caractères hexadécimaux dans un fichier privé.')
    key = bytes.fromhex(value)
    image = os.environ.get('WEB_BACKUPS_S3_IMAGE_KEY', '')
    if (re.fullmatch(r'[a-fA-F0-9]{64}', image) and key == bytes.fromhex(image)) or key == os.environ.get('WEB_APPS_MASTER_KEY', '').encode():
        raise RecoveryError('La clé globale doit être différente des clés PocketHost et des images.')
    return key


def require_space(path, required=MIN_FREE):
    parent = pathlib.Path(path)
    while not parent.exists(): parent = parent.parent
    if shutil.disk_usage(parent).free < required:
        raise RecoveryError(f'Espace insuffisant : minimum {(required + 1024**3 - 1) // 1024**3} Gio demandé pour cette opération.')


class MultipartSink:
    def __init__(self, client, config, key, metadata, notify=None):
        self.client, self.config, self.key = client, config, key
        self.upload = client.create_multipart_upload(Bucket=config['bucket'], Key=key, ContentType='application/octet-stream', Metadata=metadata)['UploadId']
        self.buffer, self.parts, self.size, self.done = bytearray(), [], 0, False
        self.uploaded, self.notify = 0, notify or (lambda _count: None)

    def write(self, data):
        self.size += len(data)
        self.buffer.extend(data)
        while len(self.buffer) >= PART:
            self._part(bytes(self.buffer[:PART])); del self.buffer[:PART]
        return len(data)

    def _part(self, data):
        number = len(self.parts) + 1
        if number > 10000: raise RecoveryError('La capture dépasse la limite multipart S3.')
        value = self.client.upload_part(Bucket=self.config['bucket'], Key=self.key, UploadId=self.upload, PartNumber=number, Body=data)
        self.parts.append({'PartNumber': number, 'ETag': value['ETag']})
        self.uploaded += len(data); self.notify(self.uploaded)

    def finish(self):
        if self.buffer: self._part(bytes(self.buffer)); self.buffer.clear()
        self.client.complete_multipart_upload(Bucket=self.config['bucket'], Key=self.key, UploadId=self.upload, MultipartUpload={'Parts': self.parts})
        self.done = True

    def abort(self):
        if not self.done:
            try: self.client.abort_multipart_upload(Bucket=self.config['bucket'], Key=self.key, UploadId=self.upload)
            except Exception: pass


class EncryptingWriter:
    def __init__(self, sink, key, platform, backup_id):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        nonce = secrets.token_bytes(12)
        self.header = {'version': 1, 'platform': platform, 'backupId': backup_id, 'keyId': hashlib.sha256(key).hexdigest()[:24], 'nonce': base64.b64encode(nonce).decode(), 'compression': 'gzip'}
        raw = encoded(self.header)
        self.aad = MAGIC + struct.pack('>I', len(raw)) + raw
        self.sink = sink
        self.cipher = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        self.cipher.authenticate_additional_data(self.aad)
        self.compress = zlib.compressobj(level=3, wbits=31)
        sink.write(self.aad)

    def write(self, value):
        compressed = self.compress.compress(value)
        if compressed: self.sink.write(self.cipher.update(compressed))
        return len(value)

    def flush(self):
        pass

    def finish(self):
        self.sink.write(self.cipher.update(self.compress.flush()))
        self.sink.write(self.cipher.finalize())
        self.sink.write(self.cipher.tag)


class IteratorReader(io.RawIOBase):
    def __init__(self, chunks):
        self.chunks, self.pending = iter(chunks), bytearray()
    def readable(self): return True
    def readinto(self, output):
        while not self.pending:
            try: self.pending.extend(next(self.chunks))
            except StopIteration: return 0
        count = min(len(output), len(self.pending)); output[:count] = self.pending[:count]; del self.pending[:count]
        return count


def exact_read(stream, size):
    value = bytearray()
    while len(value) < size:
        chunk = stream.read(size - len(value))
        if not chunk: raise RecoveryError('Archive de reprise tronquée.')
        value.extend(chunk)
    return bytes(value)


def decrypt_chunks(stream, key, header_out=None):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    if exact_read(stream, len(MAGIC)) != MAGIC: raise RecoveryError('Format de reprise inconnu.')
    length = exact_read(stream, 4); size = struct.unpack('>I', length)[0]
    if not 1 <= size <= 4096: raise RecoveryError('En-tête de reprise invalide.')
    raw = exact_read(stream, size)
    try:
        header = json.loads(raw); nonce = base64.b64decode(header['nonce'], validate=True)
        if header['version'] != 1 or len(nonce) != 12 or header['compression'] != 'gzip' or header['keyId'] != hashlib.sha256(key).hexdigest()[:24]: raise ValueError()
    except Exception: raise RecoveryError('Clé ou en-tête de reprise incorrect.') from None
    if header_out is not None: header_out.update(header)
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce)).decryptor()
    cipher.authenticate_additional_data(MAGIC + length + raw)
    inflate = zlib.decompressobj(31); tail = bytearray(); expanded = 0
    try:
        while True:
            chunk = stream.read(CHUNK)
            if not chunk: break
            tail.extend(chunk)
            if len(tail) <= 16: continue
            encrypted = bytes(tail[:-16]); del tail[:-16]
            compressed = cipher.update(encrypted)
            while compressed:
                data = inflate.decompress(compressed, CHUNK); compressed = inflate.unconsumed_tail
                expanded += len(data)
                if expanded > MAX_EXPANDED: raise RecoveryError('Archive décompressée trop volumineuse.')
                if data: yield data
        if len(tail) != 16: raise RecoveryError('Balise d’intégrité absente.')
        final = cipher.finalize_with_tag(bytes(tail))
        if final:
            data = inflate.decompress(final); expanded += len(data)
            if data: yield data
        if not inflate.eof or inflate.unused_data: raise RecoveryError('Flux compressé incomplet ou supplémentaire.')
    except RecoveryError: raise
    except Exception: raise RecoveryError('L’authentification complète de la reprise a échoué.') from None


class SourceGuard:
    def __init__(self, root, database=None, wal_limit=MAX_WAL_GROWTH):
        self.root, self.database, self.wal_limit, self.last = pathlib.Path(root), database, wal_limit, 0
        self.wal_start = self.wal_size(); self.failure = None; self.stopped = threading.Event(); self.thread = None
    def wal_size(self):
        try: return pathlib.Path(str(self.database) + '-wal').stat().st_size if self.database else 0
        except FileNotFoundError: return 0
    def check(self, force=False):
        if self.failure: raise self.failure
        if not force and time.monotonic() - self.last < 1: return
        self.last = time.monotonic(); require_space(self.root)
        if self.wal_size() - self.wal_start > self.wal_limit:
            raise RecoveryError('Capture interrompue : croissance du WAL supérieure à la limite autorisée.')
    def start(self, connection):
        def monitor():
            while not self.stopped.wait(.5):
                try: self.check(force=True)
                except RecoveryError as error:
                    self.failure = error
                    # Release the read snapshot even if the main thread is blocked on S3.
                    try: connection.interrupt(); connection.close()
                    except Exception: pass
                    return
        self.thread = threading.Thread(target=monitor, name='platform-snapshot-disk-guard', daemon=True); self.thread.start()
    def close(self):
        self.stopped.set()
        if self.thread: self.thread.join(timeout=2)


class StableReader:
    """A shrinking file is padded only inside a discarded attempt, never restored."""
    def __init__(self, stream, size, guard):
        self.stream, self.remaining, self.guard = stream, size, guard
        self.hash, self.failed = hashlib.sha256(), False
    def read(self, size=-1):
        self.guard.check()
        count = self.remaining if size < 0 else min(size, self.remaining)
        if count <= 0: return b''
        try: value = self.stream.read(count)
        except OSError: value = b''; self.failed = True
        if len(value) < count: self.failed = True; value += bytes(count - len(value))
        self.remaining -= len(value); self.hash.update(value)
        return value


def regular_tree(directory, exclude=()):
    directory = pathlib.Path(directory).absolute()
    if directory.resolve() != directory: raise RecoveryError('Répertoire source symbolique refusé.')
    result = {}
    if not directory.exists(): return result
    def unreadable(_error): raise RecoveryError('Un répertoire source est illisible. Capture abandonnée.')
    for current, directories, files in os.walk(directory, followlinks=False, onerror=unreadable):
        directories[:] = [name for name in directories if name not in exclude]
        files = [name for name in files if name not in exclude]
        directories.sort(); files.sort()
        for name in directories:
            if pathlib.Path(current, name).is_symlink(): raise RecoveryError('Lien symbolique dans une source de fichiers.')
        for name in files:
            filename = pathlib.Path(current, name); info = filename.lstat()
            if not stat.S_ISREG(info.st_mode): raise RecoveryError('Fichier source non régulier refusé.')
            relative = filename.relative_to(directory).as_posix(); safe_relative(relative)
            result[relative] = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return result


def validate_file_reference_exclusions(value, instance_ids=None):
    """Exact, private operator approvals; absence preserves strict default behavior."""
    if value is None: return None
    try:
        if not isinstance(value, dict) or set(value) != {'version', 'reason', 'approvedAt', 'entries'} or type(value['version']) is not int or value['version'] != 1 or value['reason'] != 'operator-approved-missing-assets': raise ValueError()
        approved_at = value['approvedAt']
        if not isinstance(approved_at, str) or not 1 <= len(approved_at) <= 40: raise ValueError()
        approved = datetime.datetime.fromisoformat(approved_at.replace('Z', '+00:00'))
        if approved.utcoffset() != datetime.timedelta(0): raise ValueError()
        entries, seen = value['entries'], set()
        if not isinstance(entries, list) or not 1 <= len(entries) <= 256: raise ValueError()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {'instanceId', 'collectionId', 'recordId', 'field', 'rawValueSha256'} or any(not isinstance(v, str) for v in entry.values()): raise ValueError()
            if not ID.fullmatch(entry['instanceId']) or not ID.fullmatch(entry['recordId']) or not re.fullmatch(r'[A-Za-z0-9_]{1,100}', entry['collectionId']) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,254}', entry['field']) or not re.fullmatch(r'[a-f0-9]{64}', entry['rawValueSha256']): raise ValueError()
            if instance_ids is not None and entry['instanceId'] not in instance_ids: raise ValueError()
            identity = tuple(entry[name] for name in ('instanceId', 'collectionId', 'recordId', 'field'))
            if identity in seen: raise ValueError()
            seen.add(identity)
        return {'version': 1, 'reason': value['reason'], 'approvedAt': approved_at, 'entries': sorted([dict(entry) for entry in entries], key=lambda entry: tuple(entry[name] for name in ('instanceId', 'collectionId', 'recordId', 'field')))}
    except (ValueError, TypeError, KeyError, OverflowError):
        raise RecoveryError('Exceptions de fichiers privées invalides ou hors périmètre.') from None


def file_exclusion_coverage(policy):
    return {'count': len(policy['entries']), 'reason': policy['reason'], 'approvedAt': policy['approvedAt'], 'policySha256': hashlib.sha256(encoded(policy)).hexdigest()}


def file_references(connection, exclusions=None, database_id=None):
    policy = validate_file_reference_exclusions(exclusions)
    if policy and database_id != 'controller' and not ID.fullmatch(database_id or ''): raise RecoveryError('Périmètre des exceptions de fichiers absent.')
    expected = {(entry['collectionId'], entry['recordId'], entry['field']): entry for entry in (policy or {}).get('entries', []) if entry['instanceId'] == database_id}
    used = set()
    try:
        columns = {row[1] for row in connection.execute('PRAGMA table_info("_collections")')}
        if not columns:
            if expected: raise RecoveryError('Une référence de fichier approuvée a disparu.')
            return set()
        field = 'fields' if 'fields' in columns else 'schema' if 'schema' in columns else None
        if not field: raise RecoveryError('Schéma de collections impossible à vérifier.')
        references = set()
        for collection_id, table, kind, raw in connection.execute(f'SELECT id,name,type,"{field}" FROM _collections'):
            if kind == 'view': continue
            try: fields = json.loads(raw)
            except Exception:
                try: fields = json.loads(connection.execute('SELECT json(?)', (raw,)).fetchone()[0])
                except Exception: raise RecoveryError('Schéma des fichiers impossible à décoder.') from None
            names = [value['name'] for value in fields if value.get('type') == 'file']
            if not names: continue
            quote = lambda value: '"' + str(value).replace('"', '""') + '"'
            sql = 'SELECT id,' + ','.join(quote(name) for name in names) + ' FROM ' + quote(table)
            for record in connection.execute(sql):
                for field_name, raw_value in zip(names, record[1:]):
                    identity = (str(collection_id), str(record[0]), field_name)
                    if identity in expected:
                        if not isinstance(raw_value, str) or not raw_value.startswith('/uploads/') or any(char in raw_value for char in ('\\', '\x00', '?', '#')): raise RecoveryError('Une référence de fichier approuvée a changé.')
                        safe_relative(raw_value[1:])
                        if hashlib.sha256(raw_value.encode('utf8')).hexdigest() != expected[identity]['rawValueSha256']: raise RecoveryError('Une référence de fichier approuvée a changé.')
                        used.add(identity)
                        continue
                    if raw_value in (None, '', '[]'): continue
                    try:
                        if isinstance(raw_value, bytes):
                            # SQLite JSONB file arrays are BLOBs. Decode in SQLite;
                            # the stored value and the SQL snapshot remain untouched.
                            values = json.loads(connection.execute('SELECT json(?)', (raw_value,)).fetchone()[0])
                            if not isinstance(values, list): raise ValueError('File array required')
                        else:
                            values = json.loads(raw_value) if isinstance(raw_value, str) and raw_value.startswith('[') else [raw_value]
                    except Exception: raise RecoveryError('Référence de fichier PocketBase invalide.') from None
                    for filename in values:
                        if not isinstance(filename, str) or not filename or '/' in filename or '\\' in filename: raise RecoveryError('Nom de fichier PocketBase invalide.')
                        references.add(safe_relative('/'.join([str(collection_id), str(record[0]), filename])))
        if used != set(expected): raise RecoveryError('Une référence de fichier approuvée a disparu.')
        return references
    except sqlite3.Error: raise RecoveryError('Les références aux fichiers ne peuvent pas être vérifiées.') from None


def json_column(connection, value):
    if value in (None, ''): return {}
    try: return json.loads(value)
    except Exception:
        try: return json.loads(connection.execute('SELECT json(?)', (value,)).fetchone()[0])
        except Exception: raise RecoveryError('Une configuration de dépendance ne peut pas être décodée.') from None


def wal_header(filename):
    """True when a SQLite file header says WAL (byte 18 == 2), e.g. a copy PocketBase started."""
    with open(filename, 'rb') as source: header = source.read(20)
    return len(header) == 20 and header[:16] == b'SQLite format 3\x00' and header[18] == 2


def connect_copy(filename, write=False, timeout=5):
    """Open a restored copy. A WAL copy without its -shm (clean native stop) cannot open
    with mode=ro: SQLite must rebuild the index, so it opens rw but stays query_only."""
    filename = pathlib.Path(filename)
    if not filename.is_file() or filename.resolve() != filename: raise RecoveryError('Base restaurée absente ou symbolique.')
    connection = sqlite3.connect(filename.as_uri() + ('?mode=rw' if write or wal_header(filename) else '?mode=ro'), uri=True, timeout=timeout)
    if not write: connection.execute('PRAGMA query_only=ON')
    return connection


def settings_records(connection):
    """Native settings rows: by `id` (PocketBase >=0.23), then legacy `key` (<0.23).
    Shared by capture, pause, un-pause and probe so all read the same row."""
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='_params'").fetchone(): return []
    columns = {row[1] for row in connection.execute('PRAGMA table_info(_params)')}
    if 'value' not in columns: return []
    for column in ('id', 'key'):
        if column not in columns: continue
        rows = connection.execute('SELECT "' + column + '",value FROM _params WHERE "' + column + '" IN (?,?) ORDER BY "' + column + '" DESC', ('settings', '_settings')).fetchall()
        if rows: return [(column, name, raw) for name, raw in rows]
    return []


def native_storage_config(connection):
    rows = settings_records(connection)
    if not rows: return None
    settings = json_column(connection, rows[0][2]); value = settings.get('s3', {})
    if not isinstance(value, dict) or type(value.get('enabled', False)) is not bool: raise RecoveryError('Configuration du stockage natif invalide.')
    return native_s3_settings(value) if value.get('enabled') else None


def totp_configured(connection):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ph_two_factor'").fetchone(): return False
    columns = {row[1] for row in connection.execute('PRAGMA table_info(ph_two_factor)')}
    checks = (["coalesce(enabled,0)!=0"] if 'enabled' in columns else []) + ['length(coalesce("' + name + '",\'\'))>0' for name in ['secret', 'pendingSecret'] if name in columns]
    return bool(checks and connection.execute('SELECT 1 FROM ph_two_factor WHERE ' + ' OR '.join(checks) + ' LIMIT 1').fetchone())


def totp_sidecar_state(database):
    filename = pathlib.Path(database).parent / TOTP_SIDECAR
    try:
        if filename.resolve() != filename: raise RecoveryError('Sidecar TOTP symbolique refusé.')
        descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError: return None
    except RecoveryError: raise
    except Exception: raise RecoveryError('Le sidecar TOTP ne peut pas être lu.') from None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= 4096: raise RecoveryError('Sidecar TOTP invalide.')
        with os.fdopen(os.dup(descriptor), 'rb') as source: value = source.read(4097)
        after = os.fstat(descriptor)
        info = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        if len(value) != before.st_size or info(before) != info(after): raise RecoveryError('Le sidecar TOTP change pendant sa lecture.')
        return {'stat': info(before), 'sha256': hashlib.sha256(value).hexdigest()}
    finally: os.close(descriptor)


def native_s3_settings(value):
    # Deliberately use only the credentials held in the database snapshot.
    # Platform/image backup credentials must never be a fallback here.
    endpoint = value.get('endpoint', ''); bucket = value.get('bucket', '')
    if not isinstance(endpoint, str) or not isinstance(bucket, str): raise RecoveryError('Configuration S3 native invalide.')
    try: url = urllib.parse.urlsplit(endpoint); port = url.port
    except Exception: raise RecoveryError('Configuration S3 native invalide.') from None
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/') or port not in (None, 443) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket): raise RecoveryError('Configuration S3 native invalide.')
    access, secret, region = value.get('accessKey'), value.get('secret'), value.get('region') or 'us-east-1'
    if any(not isinstance(item, str) or not item or len(item) > 512 or any(char in item for char in '\r\n\x00') for item in [access, secret]) or not isinstance(region, str) or not re.fullmatch('[A-Za-z0-9-]{1,60}', region) or type(value.get('forcePathStyle', False)) is not bool: raise RecoveryError('Identifiants S3 natifs absents ou invalides.')
    return {'endpoint': endpoint.rstrip('/'), 'bucket': bucket, 'access': access, 'secret': secret, 'region': region, 'pathStyle': value.get('forcePathStyle', False)}


def native_s3_client(config):
    import boto3
    from botocore.config import Config
    return boto3.client('s3', endpoint_url=config['endpoint'], region_name=config['region'], aws_access_key_id=config['access'], aws_secret_access_key=config['secret'], config=Config(signature_version='s3v4', connect_timeout=5, read_timeout=30, retries={'max_attempts': 1}, s3={'addressing_style': 'path' if config['pathStyle'] else 'virtual'}, proxies={}))


def native_head(client, config, key, expected=None):
    safe_relative(key)
    try:
        head = client.head_object(Bucket=config['bucket'], Key=key, **({'IfMatch': expected['etag']} if expected else {}))
        etag, size, version = head.get('ETag'), head.get('ContentLength'), head.get('VersionId')
        if not isinstance(etag, str) or not etag or len(etag) > 1024 or any(c in etag for c in '\r\n\x00') or type(size) is not int or not 0 <= size < 8 * 1024**3 or version is not None and (not isinstance(version, str) or not version): raise ValueError()
        metadata = hashlib.sha256(encoded({name: head.get(name) for name in ['Metadata', 'ContentType', 'CacheControl', 'ContentEncoding', 'ContentDisposition']})).hexdigest()
        modified = head.get('LastModified')
        if modified is not None and not isinstance(modified, datetime.datetime): raise ValueError()
        result = {'key': key, 'etag': etag, 'size': size, 'version': version, 'modified': modified.isoformat() if modified is not None else None, 'metadataSha256': metadata}
        if expected and any(result[name] != expected[name] for name in ['key', 'etag', 'size', 'version', 'modified', 'metadataSha256']): raise ValueError()
        return result
    except Exception: raise RecoveryError('Un fichier S3 natif référencé est absent, inaccessible ou a changé. Capture abandonnée.') from None


def native_body(client, config, expected):
    body = None
    try:
        # Read the current object conditionally, without requiring the extra
        # GetObjectVersion permission. Reject a changed version in its response.
        response = client.get_object(Bucket=config['bucket'], Key=expected['key'], IfMatch=expected['etag']); body = response.get('Body')
        modified = response.get('LastModified')
        if modified is not None and not isinstance(modified, datetime.datetime): raise ValueError()
        if body is None or response.get('ETag') != expected['etag'] or response.get('ContentLength') != expected['size'] or response.get('VersionId') != expected['version'] or (modified.isoformat() if isinstance(modified, datetime.datetime) else None) != expected['modified']: raise ValueError()
        return body
    except Exception:
        if body is not None: close_native(body)
        raise RecoveryError('La lecture conditionnelle d’un fichier S3 natif a échoué. Capture abandonnée.') from None


def close_native(value):
    try: value.close()
    except Exception: pass


class NativeReader:
    def __init__(self, body, size, guard): self.body, self.size, self.guard, self.count, self.hash = body, size, guard, 0, hashlib.sha256()
    def read(self, size=CHUNK):
        self.guard.check()
        try: value = self.body.read(size)
        except Exception: raise RecoveryError('La lecture d’un fichier S3 natif est incomplète.') from None
        if not isinstance(value, bytes) or len(value) > size or self.count + len(value) > self.size: raise RecoveryError('La taille d’un fichier S3 natif a changé.')
        self.count += len(value); self.hash.update(value); return value
    def complete(self):
        if self.count != self.size: raise RecoveryError('Un fichier S3 natif est tronqué.')
        return self.hash.hexdigest()


def dump_rows(connection, table):
    quote_name = lambda value: '"' + value.replace('"', '""') + '"'
    name = quote_name(table)
    columns = [row[1] for row in connection.execute('PRAGMA table_info(' + name + ')')]
    if not columns: raise RecoveryError('Table SQLite sans colonnes exportables.')
    # SQLite quote(TEXT) truncates at embedded NUL: TEXT comes back as its raw bytes (NUL and invalid
    # UTF-8 kept) and is hexed here. Building the statement in SQL (hex()||...) held about 13 times each
    # large TEXT value in native memory, out of tracemalloc's sight; the emitted SQL is byte-identical.
    sql = 'SELECT ' + ','.join("CASE typeof(" + field + ") WHEN 'text' THEN CAST(" + field + " AS BLOB) ELSE quote(" + field + ") END" for field in map(quote_name, columns)) + ' FROM ' + name
    prefix = ('INSERT INTO ' + name + ' VALUES(').encode()
    for row in connection.execute(sql):
        # Lower bound of the statement size: refuse before hexing an oversized row.
        if len(prefix) + sum(2 * len(value) if isinstance(value, bytes) else len(value) for value in row) > MAX_ROW: raise RecoveryError('Une ligne SQL dépasse la limite de mémoire de 64 Mio.')
        parts = [prefix]
        for index, value in enumerate(row):
            if index: parts.append(b',')
            parts += [b"CAST(X'", base64.b16encode(value), b"' AS TEXT)"] if isinstance(value, bytes) else [value.encode()]
        row = value = None; parts.append(b');'); yield parts


def dump_database(connection):
    # Each statement is yielded as byte pieces (joined, they form the statement): a large row
    # is never copied into one string. Schema first, data next, indexes/triggers last. Virtual
    # extension tables require a dedicated exporter; refuse rather than claim a partial clone.
    if connection.execute('PRAGMA encoding').fetchone()[0] != 'UTF-8': raise RecoveryError('L’export de cette base exige un encodage SQLite UTF-8.')
    definitions = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL ORDER BY name").fetchall()
    if any(re.match(r'CREATE\s+VIRTUAL\s+TABLE\b', sql, re.IGNORECASE) for _, sql in definitions):
        raise RecoveryError('Une table virtuelle nécessite un export natif dédié. Aucune capture complète n’est déclarée.')
    deferred = []
    for table, sql in definitions:
        if table in ('sqlite_sequence', 'sqlite_stat1'): deferred.append(table); continue
        if table.startswith('sqlite_'): continue
        yield [(sql + ';').encode()]
        yield from dump_rows(connection, table)
    for table in deferred:
        if table == 'sqlite_sequence':
            # Dropping the last AUTOINCREMENT table leaves sqlite_sequence in
            # the source. Recreate that internal table even without current DDL.
            existing = {row[0] for row in connection.execute('SELECT name FROM sqlite_master')}
            helper = '__pockethost_recovery_sequence_init'
            while helper in existing: helper += '_'
            yield [('CREATE TABLE "' + helper + '"(id INTEGER PRIMARY KEY AUTOINCREMENT);').encode()]
            yield [('DROP TABLE "' + helper + '";').encode()]
        if table == 'sqlite_stat1': yield [b'ANALYZE "sqlite_master";']
        yield [('DELETE FROM "' + table + '";').encode()]
        yield from dump_rows(connection, table)
    for row in connection.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND type IN ('index','trigger','view') ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name"):
        yield [(row[0] + ';').encode()]


def web_dependency_coverage(connection, plan):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # Only successful immutable copies protect the capture. Declaring a source
    # object in a plan (or writing a local SQL archive) does not preserve it.
    references = plan.get('capturedS3Refs', [])
    missing_images, missing_volumes = [], []
    previews = {row[0] for row in connection.execute("SELECT appId FROM web_app_environments WHERE environment='preview' AND appId!=''")} if 'web_app_environments' in tables else set()
    active_apps = None
    if {'web_apps', 'web_deployments'} <= tables and 'activeDeployment' in {row[1] for row in connection.execute('PRAGMA table_info(web_apps)')}:
        active_apps = set()
        for app_id, deployment_id in connection.execute("SELECT id,activeDeployment FROM web_apps WHERE activeDeployment!=''"):
            if app_id in previews: continue
            active_apps.add(app_id)
            row = connection.execute('SELECT result FROM web_deployments WHERE id=?', (deployment_id,)).fetchone()
            result = json_column(connection, row[0]) if row else {}
            images = result.get('images') or ({'front': result['imageId']} if result.get('imageId') else {})
            if not images and 'web_app_backups' in tables:
                archived = connection.execute("SELECT imageId FROM web_app_backups WHERE appId=? AND sourceDeploymentId=? AND status='ready' AND s3State='ready' ORDER BY created DESC LIMIT 1", (app_id, deployment_id)).fetchone()
                if archived and archived[0]: images = {'front': archived[0]}
            if not images:
                missing_images.append({'appId': app_id, 'imageId': 'unknown-published-image'}); continue
            for digest in images.values():
                if not any((item.get('appId') or item.get('metadata', {}).get('app-id')) == app_id and (item.get('imageId') or item.get('metadata', {}).get('image-id')) == digest for item in references): missing_images.append({'appId': app_id, 'imageId': digest})
    if 'web_storage_settings' in tables:
        for app_id, raw in connection.execute('SELECT appId,volumes FROM web_storage_settings'):
            if app_id in previews or active_apps is not None and app_id not in active_apps: continue
            for volume in json_column(connection, raw):
                matching = [item for item in references if (item.get('appId') or item.get('metadata', {}).get('app-id')) == app_id and (item.get('volume') or item.get('metadata', {}).get('volume-name')) == volume['name']]
                valid = False
                for item in matching:
                    try:
                        date = datetime.datetime.fromisoformat(item['snapshotAt'].replace('Z', '+00:00'))
                        age = (datetime.datetime.now(datetime.timezone.utc) - date).total_seconds()
                        valid = 0 <= age <= plan.get('maxVolumeSnapshotAgeSeconds', 86400)
                    except Exception: pass
                    if valid: break
                if not valid: missing_volumes.append({'appId': app_id, 'volume': volume['name']})
    if (missing_images or missing_volumes) and not plan.get('allowPartial'):
        raise RecoveryError('Des images web ou snapshots de volumes ne sont pas couverts par les références S3 du plan.')
    return missing_images, missing_volumes, sorted(previews)


class Capture:
    def __init__(self, archive, key_path, plan, notify=None):
        self.archive, self.key_path, self.plan, self.notify = archive, pathlib.Path(key_path).resolve(), plan, notify or (lambda *args: None)
        self.manifest = {'version': 1, 'platform': plan['platform'], 'backupId': plan['backupId'], 'startedAt': stamp(), 'databases': [], 'files': [], 'objects': [], 'nativeBackups': [], 's3Refs': plan.get('capturedS3Refs', []), 'metadata': plan.get('metadata', {}), 'coverage': {'complete': True, 'databaseMode': 'sqlite-read-transaction', 'filesMode': 'stable-per-file-two-pass', 'databaseAndFilesAtomic': False, 'instancesMissing': [], 'externalStorage': [], 'unprotectedWebImages': [], 'unprotectedVolumes': []}}
        self.file_exclusions = validate_file_reference_exclusions(plan.get('fileReferenceExclusions'), {item['id'] for item in plan.get('instances', [])})
        if self.file_exclusions:
            self.manifest['fileReferenceExclusions'] = self.file_exclusions
            self.manifest['coverage']['approvedFileReferenceExclusions'] = file_exclusion_coverage(self.file_exclusions)
        self.sequence = 0

    def add_bytes(self, parts, stable=True):
        # Byte pieces streamed through 64 KiB views: a block or a large row is never copied whole again.
        name = f'payload/{self.sequence:08d}'; self.sequence += 1
        if self.sequence > MAX_OBJECTS: raise RecoveryError('Trop de fichiers dans la capture.')
        size, digest = sum(map(len, parts)), hashlib.sha256()
        for part in parts: digest.update(part)
        info = tarfile.TarInfo(name); info.size = size; info.mode = 0o600; info.mtime = int(time.time())
        self.archive.addfile(info, io.BufferedReader(IteratorReader(memoryview(part)[offset:offset + CHUNK] for part in parts for offset in range(0, len(part), CHUNK)), CHUNK))
        self.manifest['objects'].append({'name': name, 'size': size, 'sha256': digest.hexdigest(), 'stable': stable})
        return name

    def add_file(self, filename, target, source_root, extra=None):
        filename = pathlib.Path(filename).absolute(); safe_relative(target)
        if filename.resolve() == self.key_path: raise RecoveryError('La clé externe ne doit jamais entrer dans sa propre archive.')
        for attempt in range(3):
            if filename.resolve() != filename: raise RecoveryError('Fichier source symbolique refusé.')
            descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode): raise RecoveryError('Source non régulière refusée.')
                key_info = self.key_path.stat()
                if (before.st_dev, before.st_ino) == (key_info.st_dev, key_info.st_ino): raise RecoveryError('La clé externe ne doit jamais entrer dans sa propre archive, même par lien physique.')
                guard = SourceGuard(source_root); guard.check(force=True)
                name = f'payload/{self.sequence:08d}'; self.sequence += 1
                if self.sequence > MAX_OBJECTS: raise RecoveryError('Trop de fichiers dans la capture.')
                info = tarfile.TarInfo(name); info.size = before.st_size; info.mode = 0o600; info.mtime = int(before.st_mtime)
                with os.fdopen(os.dup(descriptor), 'rb') as stream:
                    reader = StableReader(stream, before.st_size, guard); self.archive.addfile(info, reader)
                after = os.fstat(descriptor)
                stable = not reader.failed and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                self.manifest['objects'].append({'name': name, 'size': before.st_size, 'sha256': reader.hash.hexdigest(), 'stable': stable})
                if stable:
                    self.manifest['files'].append({'target': target, 'object': name, 'size': before.st_size, 'sha256': reader.hash.hexdigest(), 'capturedAt': stamp(), **(extra or {})})
                    return
            finally: os.close(descriptor)
        raise RecoveryError('Un fichier change pendant sa capture après trois tentatives. Capture abandonnée.')

    def native_files(self, config, references, database, source_root):
        try: client = native_s3_client(config)
        except Exception: raise RecoveryError('Le client du stockage S3 natif ne peut pas être initialisé.') from None
        guard = SourceGuard(source_root)
        try:
            initial = [native_head(client, config, key) for key in sorted(references)]
            for expected in initial:
                body = native_body(client, config, expected)
                try:
                    first = NativeReader(body, expected['size'], guard)
                    while first.read(CHUNK): pass
                    source_sha = first.complete()
                finally: close_native(body)
                body = native_body(client, config, expected)
                try:
                    reader = NativeReader(body, expected['size'], guard)
                    name = f'payload/{self.sequence:08d}'; self.sequence += 1
                    if self.sequence > MAX_OBJECTS: raise RecoveryError('Trop de fichiers dans la capture.')
                    info = tarfile.TarInfo(name); info.size = expected['size']; info.mode = 0o600; info.mtime = int(time.time())
                    self.archive.addfile(info, reader)
                    if reader.read(1) or reader.complete() != source_sha: raise RecoveryError('Les deux lectures d’un fichier S3 natif ne correspondent pas.')
                    self.manifest['objects'].append({'name': name, 'size': expected['size'], 'sha256': source_sha, 'stable': True})
                    self.manifest['files'].append({'target': database['target'] + '/storage/' + expected['key'], 'object': name, 'size': expected['size'], 'sha256': source_sha, 'capturedAt': stamp(), 'source': 'pocketbase-s3', 'sourceETag': expected['etag'], 'sourceVersionId': expected['version'], 'sourceLastModified': expected['modified'], 'sourceMetadataSha256': expected['metadataSha256']})
                finally: close_native(body)
            for expected in initial: native_head(client, config, expected['key'], expected)
            database['files'] = {'count': len(initial), 'referenced': len(references), 'source': 'native-s3', 'validation': 'two-get-sha256-ifmatch-and-final-head', 'checkedAt': stamp()}
            self.manifest['coverage'].setdefault('nativeS3Databases', []).append(database['id'])
            self.manifest['coverage']['nativeS3Files'] = self.manifest['coverage'].get('nativeS3Files', 0) + len(initial)
        except RecoveryError: raise
        except Exception: raise RecoveryError('La capture des fichiers S3 natifs n’est pas complète.') from None
        finally:
            if hasattr(client, 'close'): close_native(client)

    def database(self, definition, kind):
        source = pathlib.Path(definition['database']).absolute()
        ident = 'controller' if kind == 'controller' else definition['id']
        if kind != 'controller' and definition.get('withoutDatabase') is True and not os.path.lexists(source):
            # Generated plans mark instances never started: nothing exists to lose. An
            # existing file is always captured, so a stale flag cannot hide data.
            if not ID.fullmatch(ident): raise RecoveryError('Identifiant d’instance invalide.')
            self.manifest['coverage'].setdefault('instancesWithoutDatabase', []).append(ident); self.notify('database-complete', ident); return
        if source.resolve() != source or not source.is_file(): raise RecoveryError('Base SQLite source absente ou symbolique.')
        if kind != 'controller' and not ID.fullmatch(ident): raise RecoveryError('Identifiant d’instance invalide.')
        base = 'controller/pb_data' if kind == 'controller' else f'instances/{ident}/pb_data'
        guard = SourceGuard(source.parent, source, int(self.plan.get('maxWalGrowthBytes', MAX_WAL_GROWTH)))
        guard.check(force=True)
        initial_mfa_key = totp_sidecar_state(source)
        connection = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, isolation_level=None, timeout=5, check_same_thread=False)
        try:
            connection.execute('PRAGMA query_only=ON'); connection.execute('BEGIN')
            source_mode = 'read-only'
            try: page_count = connection.execute('PRAGMA page_count').fetchone()[0]
            except sqlite3.OperationalError:
                wal = pathlib.Path(str(source) + '-wal'); journal = pathlib.Path(str(source) + '-journal')
                if definition.get('initializeWalIndex') is not True or (wal.exists() and wal.stat().st_size > 0) or journal.exists(): raise
                connection.close()
                connection = sqlite3.connect(source.as_uri() + '?mode=rw', uri=True, isolation_level=None, timeout=5, check_same_thread=False)
                connection.execute('PRAGMA query_only=ON'); connection.execute('BEGIN')
                page_count = connection.execute('PRAGMA page_count').fetchone()[0]
                source_mode = 'query-only-wal-index-initialized'
            if hasattr(connection, 'setlimit'): connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_ROW)
            guard.start(connection)
            page_size = connection.execute('PRAGMA page_size').fetchone()[0]
            captured = stamp(); references = file_references(connection, self.file_exclusions, ident); native_storage = native_storage_config(connection)
            mfa_required, mfa_key = totp_configured(connection), totp_sidecar_state(source)
            if mfa_key != initial_mfa_key: raise RecoveryError('La clé TOTP a changé pendant l’ouverture du snapshot SQLite.')
            if mfa_required and mfa_key is None: raise RecoveryError('Clé TOTP manquante pour une base contenant des comptes MFA. Capture abandonnée.')
            tables = {row[0]: 0 for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND sql NOT LIKE 'CREATE VIRTUAL TABLE%'")}
            database = {'id': ident, 'kind': kind, 'target': base, 'snapshotAt': captured, 'sourceMode': source_mode, 'databaseBytes': page_count * page_size, 'sqlObjects': [], 'tables': tables}
            database['mfa'] = {'configured': mfa_required, 'keyCaptured': mfa_key is not None}
            if mfa_key is not None:
                self.add_file(source.parent / TOTP_SIDECAR, base + '/' + TOTP_SIDECAR, source.parent, {'source': 'pockethost-totp-key'})
                if self.manifest['files'][-1]['sha256'] != mfa_key['sha256']: raise RecoveryError('La clé TOTP a changé depuis le snapshot SQLite.')
            if kind == 'controller' and 'instances' in tables:
                expected = {row[0] for row in connection.execute('SELECT id FROM instances')}
                missing = sorted(expected - {value['id'] for value in self.plan.get('instances', [])})
                if missing:
                    self.manifest['coverage']['instancesMissing'] = missing; self.manifest['coverage']['complete'] = False
                    if not self.plan.get('allowPartial'): raise RecoveryError('Des instances du contrôleur ne sont pas incluses dans le plan.')
            if kind == 'controller':
                images, volumes, previews = web_dependency_coverage(connection, self.plan)
                self.manifest['coverage']['unprotectedWebImages'] = images; self.manifest['coverage']['unprotectedVolumes'] = volumes
                self.manifest['coverage']['ephemeralPreviewsExcluded'] = previews
                self.manifest['coverage']['scope'] = 'persistent-platform-resources'
                if images or volumes: self.manifest['coverage']['complete'] = False
            self.notify('snapshot', ident)
            block = bytearray(); digest = hashlib.sha256()
            def flush(parts):
                database['sqlObjects'].append(self.add_bytes(parts))
                if len(database['sqlObjects']) % 8 == 0: self.notify('sql-progress', ident)
            for parts in dump_database(connection):
                guard.check(); parts.append(b'\n'); size = sum(map(len, parts))
                if size > MAX_ROW: raise RecoveryError('Une ligne SQL dépasse la limite de mémoire de 64 Mio.')
                if block and len(block) + size > SQL_PART: flush([block]); block = bytearray()
                for part in parts: digest.update(part)
                match = re.fullmatch(rb'INSERT INTO "((?:[^"]|"")+)" VALUES\(', parts[0])
                if match and (table := match.group(1).decode().replace('""', '"')) in tables: tables[table] += 1
                # A row above SQL_PART always formed its own object: it is now streamed from its pieces.
                if size > SQL_PART: flush(parts)
                else: block += b''.join(parts)
                parts.clear()  # Release the row (shared with dump_rows) before SQLite reads the next one.
            if block: flush([block])
            if totp_sidecar_state(source) != mfa_key: raise RecoveryError('La clé TOTP a changé pendant le snapshot SQLite. Capture abandonnée.')
            guard.check(force=True); database['sqlSha256'] = digest.hexdigest(); self.manifest['databases'].append(database)
        except sqlite3.Error as error: raise RecoveryError('La lecture cohérente SQLite a échoué ; aucune capture complète n’est déclarée.') from error
        finally: guard.close(); connection.close()
        storage = definition.get('storage')
        if native_storage is not None:
            self.native_files(native_storage, references, database, source.parent)
        elif definition.get('storageMode', 'local') == 'external':
            self.manifest['coverage']['externalStorage'].append(ident); self.manifest['coverage']['complete'] = False
            if not self.plan.get('allowPartial'): raise RecoveryError('Un stockage externe n’est pas inclus dans la capture.')
        elif references and not storage: raise RecoveryError('Une base référence des fichiers mais son stockage local manque dans le plan.')
        if storage and native_storage is None:
            first = regular_tree(storage)
            if references - set(first): raise RecoveryError('Des fichiers référencés par le snapshot SQLite sont absents. Capture abandonnée.')
            for relative in first: self.add_file(pathlib.Path(storage) / relative, base + '/storage/' + relative, pathlib.Path(storage).parent)
            second = regular_tree(storage)
            database['files'] = {'count': len(first), 'referenced': len(references), 'addedDuringCapture': len(set(second) - set(first)), 'removedAfterCapture': len(set(first) - set(second)), 'changedAfterCapture': sum(first[name] != second[name] for name in set(first) & set(second)), 'checkedAt': stamp()}
        if totp_sidecar_state(source) != mfa_key: raise RecoveryError('La clé TOTP a changé pendant la capture des fichiers. Capture abandonnée.')
        self.notify('database-complete', ident)

    def finish(self):
        self.database(self.plan['controller'], 'controller')
        for instance in self.plan.get('instances', []): self.database(instance, 'instance')
        for entry in self.plan.get('hostPaths', []):
            source = pathlib.Path(entry['source']).absolute(); target = safe_relative(entry['target'])
            if source.is_dir():
                for relative in regular_tree(source, exclude=('.git', 'node_modules', '__pycache__', '.secret')):
                    self.add_file(source / relative, 'host-files/' + target + '/' + relative, source.parent)
            else: self.add_file(source, 'host-files/' + target, source.parent)
        for entry in self.plan.get('nativeBackups', []):
            if not isinstance(entry.get('snapshotAt'), str) or not re.fullmatch('[a-f0-9]{64}', entry.get('checksum', '')): raise RecoveryError('Archive native sans date ni empreinte explicites.')
            before = len(self.manifest['files']); self.add_file(entry['source'], 'native-backups/' + safe_relative(entry['target']), pathlib.Path(entry['source']).parent, {'snapshotAt': entry['snapshotAt']})
            captured = self.manifest['files'][before]
            if captured['sha256'] != entry['checksum']: raise RecoveryError('L’archive native ne correspond pas à son empreinte.')
            self.manifest['nativeBackups'].append({'target': captured['target'], 'snapshotAt': entry['snapshotAt'], 'checksum': captured['sha256']})
        self.manifest['finishedAt'] = stamp()
        validate_manifest(self.manifest, {item['name']: item for item in self.manifest['objects']})
        raw = encoded(self.manifest)
        if len(raw) > MAX_MANIFEST: raise RecoveryError('Manifeste de reprise trop volumineux.')
        info = tarfile.TarInfo('manifest.json'); info.size = len(raw); info.mode = 0o600; self.archive.addfile(info, io.BytesIO(raw))
        return self.manifest


def validate_manifest(manifest, observed):
    if manifest.get('version') != 1 or not isinstance(manifest.get('databases'), list) or not isinstance(manifest.get('files'), list) or not isinstance(manifest.get('objects'), list) or len(manifest['objects']) > MAX_OBJECTS:
        raise RecoveryError('Manifeste de reprise invalide.')
    objects = {}
    for item in manifest['objects']:
        if not isinstance(item, dict) or not re.fullmatch(r'payload/[0-9]{8}', item.get('name', '')) or item['name'] in objects or type(item.get('size')) is not int or item['size'] < 0 or not re.fullmatch('[a-f0-9]{64}', item.get('sha256', '')):
            raise RecoveryError('Objet de reprise invalide.')
        objects[item['name']] = item
    if set(objects) != set(observed): raise RecoveryError('Objets absents ou supplémentaires dans la capture.')
    for name, item in objects.items():
        if observed[name]['size'] != item['size'] or observed[name]['sha256'] != item['sha256']:
            raise RecoveryError('Une empreinte de fichier ne correspond pas au manifeste.')
    targets, referenced = set(), set()
    for database in manifest['databases']:
        ident = database.get('id')
        if ident != 'controller' and not ID.fullmatch(ident or ''): raise RecoveryError('Identifiant de base restaurée invalide.')
        expected = 'controller/pb_data' if ident == 'controller' else f'instances/{ident}/pb_data'
        if database.get('target') != expected or not isinstance(database.get('sqlObjects'), list) or not re.fullmatch('[a-f0-9]{64}', database.get('sqlSha256', '')) or type(database.get('databaseBytes')) is not int or database['databaseBytes'] < 0:
            raise RecoveryError('Description de base de données invalide.')
        if expected in targets: raise RecoveryError('Base de données dupliquée.')
        targets.add(expected)
        for name in database['sqlObjects']:
            if name in referenced or name not in objects or not objects[name].get('stable') or objects[name]['size'] > MAX_ROW + SQL_PART:
                raise RecoveryError('Ordre SQL ou objet de base invalide.')
            referenced.add(name)
    policy = validate_file_reference_exclusions(manifest.get('fileReferenceExclusions'), {value['id'] for value in manifest['databases'] if value['id'] != 'controller'})
    reported_exclusions = manifest.get('coverage', {}).get('approvedFileReferenceExclusions')
    if (file_exclusion_coverage(policy) if policy else None) != reported_exclusions: raise RecoveryError('Les exclusions déclarées ne correspondent pas à la politique approuvée.')
    file_targets = set()
    for value in manifest['files']:
        target = safe_relative(value.get('target'))
        if not (target in {base + '/' + TOTP_SIDECAR for base in targets} or target.startswith('controller/pb_data/storage/') or re.match(r'^instances/[a-z0-9]{15}/pb_data/storage/', target) or target.startswith('host-files/') or target.startswith('native-backups/')) or target in file_targets:
            raise RecoveryError('Cible de fichier dupliquée ou hors du périmètre de reprise.')
        name = value.get('object')
        if name not in objects or not objects[name].get('stable') or name in referenced or value.get('size') != objects[name]['size'] or value.get('sha256') != objects[name]['sha256']:
            raise RecoveryError('Référence de fichier stable invalide.')
        file_targets.add(target); referenced.add(name)
    for database in manifest['databases']:
        mfa = database.get('mfa', {})
        if mfa.get('configured') and not mfa.get('keyCaptured') or mfa.get('keyCaptured') and database['target'] + '/' + TOTP_SIDECAR not in file_targets:
            raise RecoveryError('Le manifeste omet une clé TOTP nécessaire à la base restaurée.')
    for name, item in objects.items():
        if item.get('stable') and name not in referenced: raise RecoveryError('Objet stable sans cible déclarée.')


def inspect_archive(factory, key):
    observed, manifest, header = {}, None, {}
    source = factory()
    reader = io.BufferedReader(IteratorReader(decrypt_chunks(source, key, header)), buffer_size=CHUNK)
    try:
        with tarfile.open(fileobj=reader, mode='r|') as archive:
            for member in archive:
                if not member.isfile() or member.pax_headers or member.name in observed:
                    raise RecoveryError('Archive contenant un lien, une entrée spéciale ou dupliquée.')
                if member.name == 'manifest.json':
                    if manifest is not None or not 1 <= member.size <= MAX_MANIFEST: raise RecoveryError('Manifeste absent ou trop volumineux.')
                    manifest = json.loads(archive.extractfile(member).read())
                    continue
                if manifest is not None or not re.fullmatch(r'payload/[0-9]{8}', member.name) or len(observed) >= MAX_OBJECTS or not 0 <= member.size <= MAX_EXPANDED:
                    raise RecoveryError('Entrée inattendue dans la capture.')
                digest = hashlib.sha256(); count = 0
                with archive.extractfile(member) as stream:
                    while True:
                        chunk = stream.read(CHUNK)
                        if not chunk: break
                        count += len(chunk); digest.update(chunk)
                if count != member.size: raise RecoveryError('Fichier de capture incomplet.')
                observed[member.name] = {'size': count, 'sha256': digest.hexdigest()}
        # tarfile stops at its zero blocks; drain to force GCM authentication of the full ciphertext.
        while reader.read(CHUNK): pass
        if not manifest: raise RecoveryError('Manifeste de reprise absent.')
        validate_manifest(manifest, observed)
        if manifest.get('platform') != header.get('platform') or manifest.get('backupId') != header.get('backupId'):
            raise RecoveryError('L’identité de la capture ne correspond pas à son en-tête authentifié.')
        return manifest
    except RecoveryError: raise
    except Exception: raise RecoveryError('L’archive de reprise ne peut pas être validée entièrement.') from None
    finally:
        reader.close(); source.close()


def recovery_prefixes(manifest):
    mapping, prefixes, environment = {}, {}, {}
    for item in manifest.get('s3Refs', []):
        original, recovered = item.get('originalKey'), item.get('recoveryKey')
        source_prefix, recovered_prefix = item.get('sourcePrefix'), item.get('recoveryPrefix')
        if not all(isinstance(value, str) and value for value in [original, recovered, source_prefix, recovered_prefix]) or not original.startswith(source_prefix + '/') or not recovered.startswith(recovered_prefix + '/'):
            raise RecoveryError('Mappage des dépendances S3 invalide.')
        if original in mapping and mapping[original] != recovered or source_prefix in prefixes and prefixes[source_prefix] != recovered_prefix:
            raise RecoveryError('Un préfixe S3 source possède plusieurs destinations incompatibles.')
        mapping[original] = recovered; prefixes[source_prefix] = recovered_prefix
        if item.get('group') == 'web' or recovered_prefix.endswith('/refs/web'): environment['WEB_BACKUPS_S3_PREFIX'] = recovered_prefix
    return mapping, prefixes, environment


def remap_metadata(connection, manifest):
    mapping, prefixes, environment = recovery_prefixes(manifest)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    def replace(value):
        if isinstance(value, str): return mapping.get(value, value)
        if isinstance(value, list): return [replace(item) for item in value]
        if isinstance(value, dict): return {name: replace(item) for name, item in value.items()}
        return value
    for table, fields in [('web_app_backups', ['s3Object', 's3ImageObject', 's3ImageObjects']), ('web_storage_jobs', ['result'])]:
        if table not in tables: continue
        columns = {row[1] for row in connection.execute('PRAGMA table_info("' + table + '")')}
        for field in fields:
            if field not in columns: continue
            for record_id, raw in connection.execute(f'SELECT id,"{field}" FROM "{table}"').fetchall():
                if raw in (None, ''): continue
                if field in ('s3Object', 's3ImageObject'): changed = mapping.get(raw, raw)
                else: changed = json.dumps(replace(json_column(connection, raw)))
                connection.execute(f'UPDATE "{table}" SET "{field}"=? WHERE id=?', (changed, record_id))
    connection.commit()
    return {'prefixes': prefixes, 'environment': environment, 'referencesMapped': len(mapping)}


def pause_database(connection, state=None):
    # `state` receives the pre-pause values so a real-loss un-pause can restore them exactly.
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    changed, native_settings = {}, False
    state = {} if state is None else state; state.update(version=1, tables={})
    previews = [row[0] for row in connection.execute("SELECT appId FROM web_app_environments WHERE environment='preview' AND appId!=''")] if 'web_app_environments' in tables else []
    if previews:
        # Recreate the same tombstones as a completed preview cleanup, never a legacy app that could restart.
        for app_id in previews:
            if 'web_apps' in tables: connection.execute("UPDATE web_apps SET activeDeployment='',desiredCommit='',status='stopped' WHERE id=?", (app_id,))
            for table in ['web_app_health', 'web_app_health_events', 'web_scheduled_tasks', 'web_task_runs', 'web_terminal_sessions', 'web_environment_audit', 'web_app_backup_settings', 'web_app_backups', 'web_runtime_profiles', 'web_storage_settings', 'web_storage_jobs', 'web_deployments', 'web_app_domains']:
                if table in tables and 'appId' in {row[1] for row in connection.execute('PRAGMA table_info("' + table + '")')}: connection.execute('DELETE FROM "' + table + '" WHERE appId=?', (app_id,))
            if 'web_apps' in tables: connection.execute('DELETE FROM web_apps WHERE id=?', (app_id,))
        connection.execute("UPDATE web_app_environments SET state='closed',appId='' WHERE environment='preview'")
        changed['ephemeralPreviewsClosed'] = len(previews)
    for table, column in PAUSED_FLAGS:
        columns = {row[1] for row in connection.execute('PRAGMA table_info("' + table + '")')} if table in tables else set()
        if column in columns:
            state['tables'][table] = {'column': column, 'enabledIds': [row[0] for row in connection.execute(f'SELECT id FROM "{table}" WHERE coalesce("{column}",0) NOT IN (0,\'\',\'0\',\'false\')')] if 'id' in columns else None}
            changed[table] = connection.execute(f'UPDATE "{table}" SET "{column}"=0').rowcount
    for table in ['web_deployments', 'web_task_runs', 'web_app_backups', 'web_storage_jobs', 'web_notification_deliveries']:
        if table in tables and 'status' in {row[1] for row in connection.execute('PRAGMA table_info("' + table + '")')}:
            connection.execute(f"UPDATE \"{table}\" SET status='cancelled' WHERE status IN ('queued','running','sending','deleting','issued')")
    if 'web_apps' in tables:
        if {'id', 'status'} <= {row[1] for row in connection.execute('PRAGMA table_info(web_apps)')}:
            state['webAppsStatus'] = {row[0]: row[1] for row in connection.execute('SELECT id,status FROM web_apps')}
        connection.execute("UPDATE web_apps SET status='stopped'")
    if 'backups' in tables:
        columns = {row[1] for row in connection.execute('PRAGMA table_info(backups)')}
        updates = []
        if 'localPath' in columns: updates.append("localPath=''")
        if 'status' in columns: updates.append("status='unavailable'")
        if 'restoreState' in columns: updates.append("restoreState='paused'")
        if 'error' in columns: updates.append("error='Historique restauré : ancienne archive locale non incluse. Les bases fraîches sont reconstruites séparément.'")
        if updates: connection.execute('UPDATE backups SET ' + ','.join(updates))
    if 'web_terminal_sessions' in tables:
        columns = {row[1] for row in connection.execute('PRAGMA table_info(web_terminal_sessions)')}
        if 'status' in columns: connection.execute("UPDATE web_terminal_sessions SET status='closed'")
        if 'grantHash' in columns: connection.execute("UPDATE web_terminal_sessions SET grantHash=''")
    for parameter_column, parameter, raw in settings_records(connection):
        try: value = json.loads(raw)
        except Exception: continue
        if not isinstance(value, dict): continue
        state.setdefault('nativeSettings', []).append({'column': parameter_column, 'name': parameter, **native_flags(value)})
        for name in ['smtp', 's3']:
            if isinstance(value.get(name), dict): value[name]['enabled'] = False
        if isinstance(value.get('backups'), dict):
            value['backups']['cron'] = ''
            if isinstance(value['backups'].get('s3'), dict): value['backups']['s3']['enabled'] = False
        if isinstance(value.get('filesystem'), dict) and isinstance(value['filesystem'].get('s3'), dict): value['filesystem']['s3']['enabled'] = False
        # Keep PocketBase's own storage class (TEXT or BLOB) for the rewritten JSON.
        written = json.dumps(value) if isinstance(raw, str) else json.dumps(value).encode()
        connection.execute('UPDATE _params SET value=? WHERE "' + parameter_column + '"=?', (written, parameter)); native_settings = True
    # Only authentication collections hold signing token keys. An unrelated
    # business column named tokenKey must remain byte-for-byte intact.
    auth_tables = {'_superusers', '_admins'} & tables
    if '_collections' in tables:
        columns = {row[1] for row in connection.execute('PRAGMA table_info(_collections)')}
        if {'name', 'type'} <= columns:
            auth_tables.update(row[0] for row in connection.execute("SELECT name FROM _collections WHERE type='auth'") if row[0] in tables)
    for table in auth_tables:
        quote = '"' + table.replace('"', '""') + '"'
        columns = {row[1] for row in connection.execute('PRAGMA table_info(' + quote + ')')}
        if 'tokenKey' in columns: connection.execute('UPDATE ' + quote + ' SET tokenKey=lower(hex(randomblob(32)))')
    connection.commit()
    report = {'disabledTables': changed, 'nativeSmtpAndBackupsDisabled': native_settings, 'requiresNetworkIsolation': True}
    kept = external_credentials(connection, tables)
    if kept: report['credentialsKept'] = kept
    return report


def external_credentials(connection, tables):
    """Report only (no change): credentials still valid outside this copy. A real-loss restore needs
    them; a clone must rotate them at un-pause (--rotate-webhooks, --disable-git-sources,
    --revoke-ci-tokens). Hosts only: canonical and path can name private repositories."""
    result = {}
    columns = lambda table: {row[1] for row in connection.execute('PRAGMA table_info("' + table + '")')}
    if 'web_git_sources' in tables and {'host', 'webhookSecretEncrypted', 'privateKeyEncrypted'} <= columns('web_git_sources'):
        rows = connection.execute("SELECT host, coalesce(webhookSecretEncrypted,'')!='', coalesce(privateKeyEncrypted,'')!='' FROM web_git_sources").fetchall()
        if rows: result['gitSources'] = {'count': len(rows), 'hosts': sorted({str(row[0]) for row in rows}), 'webhooks': sum(1 for row in rows if row[1]), 'deployKeys': sum(1 for row in rows if row[2])}
    if 'web_ci_tokens' in tables and 'revokedAt' in columns('web_ci_tokens'):
        active = connection.execute("SELECT count(*) FROM web_ci_tokens WHERE coalesce(revokedAt,'')=''").fetchone()[0]
        if active: result['ciTokensActive'] = active
    if 'web_app_settings' in tables and 'key' in columns('web_app_settings'):
        # GitHub App connections ('github' = principal, 'github:<id>'): counted by key, never decrypted
        # (no master key here). Their webhook secrets rotate at un-pause (--rotate-webhooks).
        keys = [row[0] for row in connection.execute("SELECT key FROM web_app_settings WHERE key='github' OR key LIKE 'github:%'")]
        count = sum(1 for key in keys if isinstance(key, str) and GITHUB_SETTINGS_KEY.fullmatch(key))
        if count: result['githubConnections'] = {'count': count}
    return result


GITHUB_SETTINGS_KEY = re.compile(r'github(:[a-z0-9]{15})?')
PAUSED_FLAGS = [('web_app_health', 'enabled'), ('web_scheduled_tasks', 'enabled'), ('web_app_backup_settings', 'enabled'), ('web_projects', 'previewEnabled'), ('web_notification_settings', 'enabled')]


def native_flags(value):
    section = lambda *path: (lambda item: item if isinstance(item, dict) else None)(functools.reduce(lambda node, name: node.get(name) if isinstance(node, dict) else None, path, value))
    flag = lambda item: item.get('enabled') if item is not None and type(item.get('enabled')) is bool else None
    backups = section('backups')
    return {'smtp': flag(section('smtp')), 's3': flag(section('s3')), 'filesystemS3': flag(section('filesystem', 's3')), 'backupsCron': backups.get('cron') if backups is not None and isinstance(backups.get('cron'), str) else None, 'backupsS3': flag(section('backups', 's3'))}


def prepare_restored_database(connection, manifest, controller=False, state=None):
    # Offline pauses must not fire application SQL triggers (audit rows,
    # counters, business hooks). Restore their exact definitions afterward.
    triggers = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL").fetchall()
    for name, _ in triggers: connection.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
    try:
        if controller: remap_metadata(connection, manifest)
        return pause_database(connection, state)
    finally:
        for _, sql in triggers: connection.execute(sql)
        connection.commit()


def restore_budget(manifest, select=None, host_files=False, native_archives=False):
    """Bytes a restore materialises; the single source for restore checks and inspect output."""
    chosen = set(select or [value['id'] for value in manifest.get('databases', [])])
    databases = sum(value['databaseBytes'] for value in manifest.get('databases', []) if value['id'] in chosen)
    scopes = {'databaseFiles': 0, 'hostFiles': 0, 'nativeArchives': 0}
    for value in manifest.get('files', []):
        target = value['target']
        if target.startswith('host-files/'): scopes['hostFiles'] += value['size']
        elif target.startswith('native-backups/'): scopes['nativeArchives'] += value['size']
        elif target.startswith('controller/') and 'controller' in chosen or any(target.startswith(f'instances/{ident}/') for ident in chosen if ident != 'controller'): scopes['databaseFiles'] += value['size']
    total = databases + scopes['databaseFiles'] + (scopes['hostFiles'] if host_files else 0) + (scopes['nativeArchives'] if native_archives else 0)
    return {'databases': len(chosen), 'databaseBytes': databases, **{name + 'Bytes': size for name, size in scopes.items()}, 'hostFilesIncluded': bool(host_files), 'nativeArchivesIncluded': bool(native_archives), 'restoreBytes': total}


def restore_archive(factory, key, destination, select=None, host_files=False, native_archives=False, min_free_bytes=MIN_FREE, max_restore_bytes=None):
    manifest = inspect_archive(factory, key)  # No destination materialization before full authentication.
    destination = pathlib.Path(destination).absolute()
    if destination.resolve() != destination or (destination.exists() and (not destination.is_dir() or any(destination.iterdir()))):
        raise RecoveryError('La restauration exige un répertoire vierge sans lien symbolique.')
    available = {value['id'] for value in manifest['databases']}
    chosen = set(select or available)
    if not chosen or chosen - available: raise RecoveryError('Sélection de bases absente de cette capture.')
    databases = {value['id']: value for value in manifest['databases'] if value['id'] in chosen}
    def include_file(value):
        target = value['target']
        if target.startswith('host-files/'): return host_files
        if target.startswith('native-backups/'): return native_archives
        return target.startswith('controller/') and 'controller' in chosen or any(target.startswith(f'instances/{ident}/') for ident in chosen if ident != 'controller')
    files = {value['object']: value for value in manifest['files'] if include_file(value)}
    if not isinstance(min_free_bytes, int) or min_free_bytes < 1024**3: raise RecoveryError('Réserve disque de restauration invalide.')
    restored_bytes = restore_budget(manifest, chosen, host_files, native_archives)['restoreBytes']
    if max_restore_bytes is not None and (not isinstance(max_restore_bytes, int) or max_restore_bytes < 1 or restored_bytes > max_restore_bytes): raise RecoveryError('La sélection dépasse le budget fixe du test de reconstruction.')
    required = min_free_bytes + restored_bytes
    require_space(destination.parent, required)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = destination / ('.recovery-staging-' + secrets.token_hex(8)); staging.mkdir(mode=0o700)
    sql = {name: (ident, index) for ident, value in databases.items() for index, name in enumerate(value['sqlObjects'])}
    connections, positions, hashes = {}, {ident: 0 for ident in databases}, {ident: hashlib.sha256() for ident in databases}
    source = factory(); reader = io.BufferedReader(IteratorReader(decrypt_chunks(source, key)), buffer_size=CHUNK)
    paused, pause_states = {}, {}
    disk_stopped, disk_failed = threading.Event(), threading.Event()
    def check_restore_disk():
        if disk_failed.is_set(): raise RecoveryError('Reconstruction interrompue pour préserver la réserve disque demandée.')
        require_space(destination, min_free_bytes)
    def watch_restore_disk():
        while not disk_stopped.wait(.5):
            try: require_space(destination, min_free_bytes)
            except RecoveryError:
                disk_failed.set()
                for connection in list(connections.values()):
                    try: connection.interrupt()
                    except Exception: pass
                return
    disk_thread = threading.Thread(target=watch_restore_disk, name='platform-restore-disk-guard', daemon=True); disk_thread.start()
    def authorize(action, arg1, arg2, _database, _trigger):
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH) or action == sqlite3.SQLITE_FUNCTION and (arg2 or '').lower() == 'load_extension': return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA and str(arg1).lower() not in ('writable_schema', 'foreign_keys', 'defer_foreign_keys'): return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    try:
        with tarfile.open(fileobj=reader, mode='r|') as archive:
            for member in archive:
                if not member.isfile(): raise RecoveryError('Entrée non régulière dans la seconde lecture.')
                check_restore_disk()
                if member.name in sql:
                    ident, index = sql[member.name]
                    if index != positions[ident] or member.size > MAX_ROW + SQL_PART: raise RecoveryError('Ordre de reconstruction SQL invalide.')
                    if ident not in connections:
                        target = staging / databases[ident]['target']; target.mkdir(mode=0o700, parents=True, exist_ok=True)
                        filename = target / 'data.db.reconstructing'; connection = sqlite3.connect(filename); os.chmod(filename, 0o600)
                        if hasattr(connection, 'enable_load_extension'): connection.enable_load_extension(False)
                        connection.set_authorizer(authorize); connections[ident] = connection
                    raw = archive.extractfile(member).read(); hashes[ident].update(raw)
                    # executescript otherwise autocommits every INSERT. Bound a
                    # transaction to each authenticated block, keeping restores
                    # fast without a second full-size SQL/database temp copy.
                    connections[ident].executescript('BEGIN;\n' + raw.decode('utf8') + '\nCOMMIT;'); check_restore_disk(); positions[ident] += 1
                elif member.name in files:
                    description = files[member.name]; target = staging / safe_relative(description['target']); target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    with open(target, 'xb') as output, archive.extractfile(member) as content:
                        os.chmod(target, 0o600)
                        while True:
                            chunk = content.read(CHUNK)
                            if not chunk: break
                            check_restore_disk(); output.write(chunk); digest.update(chunk)
                    if target.stat().st_size != description['size'] or digest.hexdigest() != description['sha256']: raise RecoveryError('Fichier restauré incomplet.')
        while reader.read(CHUNK): pass
        for ident, database in databases.items():
            if ident not in connections or positions[ident] != len(database['sqlObjects']) or hashes[ident].hexdigest() != database['sqlSha256']: raise RecoveryError('Reconstruction SQL incomplète.')
            connection = connections[ident]; connection.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]: raise RecoveryError('Le contrôle SQLite de la base restaurée a échoué.')
            for table, expected in database['tables'].items():
                quote = '"' + table.replace('"', '""') + '"'
                if connection.execute('SELECT count(*) FROM ' + quote).fetchone()[0] != expected: raise RecoveryError('Le nombre de lignes restaurées diffère du snapshot.')
            policy = manifest.get('fileReferenceExclusions')
            if policy and any(entry['instanceId'] == ident for entry in policy['entries']): file_references(connection, policy, ident)
            pause_states[ident] = {}
            paused[ident] = prepare_restored_database(connection, manifest, ident == 'controller', pause_states[ident]); connection.close(); del connections[ident]
            filename = staging / database['target'] / 'data.db.reconstructing'; filename.rename(filename.with_name('data.db'))
        _, prefix_map, recovery_environment = recovery_prefixes(manifest)
        marker = {'version': 1, 'backupId': manifest['backupId'], 'platform': manifest['platform'], 'paused': True, 'noServicesStarted': True, 'hostFilesRestored': host_files, 'nativeArchivesRestored': native_archives, 'hooksDirectory': '.disabled-hooks', 'requiredSystemd': {'PrivateNetwork': True, 'NoNewPrivileges': True}, 'databasesRestored': sorted(chosen), 'coverage': manifest['coverage'], 'databasePauses': paused, 'minFreeBytes': min_free_bytes, 's3PrefixMappings': prefix_map, 'webImagesAndVolumesRequireExplicitRecovery': bool(manifest.get('s3Refs')), 'pauseStateFile': PAUSE_STATE, 'restoredAt': stamp()}
        (staging / '.disabled-hooks').mkdir(mode=0o700)
        (staging / '.disabled-migrations').mkdir(mode=0o700)
        for name, value in [('RECOVERY_PAUSED.json', marker), ('recovery-manifest.json', manifest), ('recovery-env-overrides.json', recovery_environment), (PAUSE_STATE, {'version': 1, 'backupId': manifest['backupId'], 'databases': pause_states})]:
            filename = staging / name; filename.write_bytes(encoded(value)); filename.chmod(0o600)
        for item in staging.iterdir(): item.rename(destination / item.name)
        staging.rmdir()
        return marker
    except RecoveryError: raise
    except Exception as error: raise RecoveryError('La reconstruction isolée a échoué ; aucun service d’origine n’a été modifié.') from error
    finally:
        disk_stopped.set(); disk_thread.join(timeout=2)
        reader.close(); source.close()
        for connection in connections.values(): connection.close()
        if staging.exists(): shutil.rmtree(staging)


def prefixes_overlap(first, second):
    # S3 "directories": app1/x and app1/x/y overlap, app1/x and app1/xy do not.
    return first == second or first.startswith(second + '/') or second.startswith(first + '/')


def s3_settings():
    # No default: one bucket serves several hosts, each in its own directory. The service derives
    # <platform>/platform-recovery from its config; a direct CLI run must name the directory.
    prefix = os.environ.get('WEB_PLATFORM_RECOVERY_PREFIX', '')
    if not prefix: raise RecoveryError('WEB_PLATFORM_RECOVERY_PREFIX est requis : un répertoire propre à cet hôte, par exemple app2/platform-recovery.')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/_-]{0,199}', prefix) or '//' in prefix or prefix.endswith('/'): raise RecoveryError('Préfixe S3 de reprise invalide.')
    # One bucket may serve several platforms (app1, app2, ...) in separate directories: the sweep
    # and the purges act on everything under this prefix, so it must never contain the site backups.
    web = os.environ.get('WEB_BACKUPS_S3_PREFIX', '').strip('/')
    # After a failover the site backups live in <old capture>/refs/web: the new host then needs a new,
    # non-nested recovery directory (WEB_RECOVERY_RUNBOOK §5.13), never the old one.
    if web and prefixes_overlap(prefix, web): raise RecoveryError('Le préfixe S3 de reprise doit être un répertoire distinct de celui des sauvegardes des sites. Après une bascule, choisir un nouveau WEB_PLATFORM_RECOVERY_PREFIX.')
    values = {name: os.environ.get('WEB_PLATFORM_S3_' + name) or os.environ.get('WEB_BACKUPS_S3_' + name, '') for name in ['ENDPOINT', 'BUCKET', 'ACCESS_KEY_ID', 'SECRET_ACCESS_KEY', 'REGION']}
    url = urllib.parse.urlsplit(values['ENDPOINT'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/') or url.port not in (None, 443) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', values['BUCKET']): raise RecoveryError('Configuration S3 privée de reprise invalide.')
    if any(not values[name] or len(values[name]) > 512 or any(c in values[name] for c in '\r\n\x00') for name in ['ACCESS_KEY_ID', 'SECRET_ACCESS_KEY']): raise RecoveryError('Identifiants S3 de reprise absents ou invalides.')
    return {'endpoint': values['ENDPOINT'].rstrip('/'), 'bucket': values['BUCKET'], 'access': values['ACCESS_KEY_ID'], 'secret': values['SECRET_ACCESS_KEY'], 'region': values['REGION'] or 'gra', 'prefix': prefix}


def s3_client(config):
    import boto3
    from botocore.config import Config
    return boto3.client('s3', endpoint_url=config['endpoint'], region_name=config['region'], aws_access_key_id=config['access'], aws_secret_access_key=config['secret'], config=Config(signature_version='s3v4', connect_timeout=5, read_timeout=30, retries={'max_attempts': 1}, s3={'addressing_style': 'path'}, proxies={}))


def s3_factory(client, config, object_key):
    if not object_key.startswith(config['prefix'] + '/') or not re.fullmatch(r'[A-Za-z0-9/_@:.+-]+', object_key) or any(part == '..' for part in object_key.split('/')): raise RecoveryError('Objet S3 hors du préfixe de reprise.')
    head = client.head_object(Bucket=config['bucket'], Key=object_key)
    if head.get('ContentLength', 0) < 50 or not isinstance(head.get('ETag'), str): raise RecoveryError('Objet de reprise absent ou incomplet.')
    etag = head['ETag']
    def factory():
        value = client.get_object(Bucket=config['bucket'], Key=object_key, IfMatch=etag)
        if value.get('ETag') != etag: raise RecoveryError('La capture S3 a changé entre les lectures.')
        return value['Body']
    factory.size = head.get('ContentLength')
    return factory


def copy_unchanged(client, config, key, etag, copy):
    """Server-side copy pinned to the source ETag. Some S3 providers (OVH) reject the
    x-amz-copy-source-if-match condition with NotImplemented: the copy then runs plain and the
    source is read again, so a replaced object never passes as the planned one."""
    try: return copy(CopySourceIfMatch=etag)
    except Exception as error:
        if ((getattr(error, 'response', None) or {}).get('Error') or {}).get('Code') != 'NotImplemented': raise
    result = copy()
    if client.head_object(Bucket=config['bucket'], Key=key).get('ETag') != etag: raise RecoveryError('Une dépendance S3 a changé pendant sa copie.')
    return result


def _copy_references(client, config, plan, backup_id, created):
    result, prefix_groups = [], {}
    for reference in plan.get('s3Refs', []):
        key, prefix, group = reference.get('key'), reference.get('prefix'), reference.get('group')
        if not isinstance(key, str) or not isinstance(prefix, str) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,40}', group or '') or not key.startswith(prefix + '/') or not re.fullmatch(r'[A-Za-z0-9/_@:.+-]+', key) or '..' in key.split('/') or prefixes_overlap(prefix, config['prefix']):
            raise RecoveryError('Référence S3 source invalide.')
        if prefix in prefix_groups and prefix_groups[prefix] != group: raise RecoveryError('Images et volumes partageant un préfixe doivent rester dans le même groupe de reprise.')
        prefix_groups[prefix] = group
        head = client.head_object(Bucket=config['bucket'], Key=key)
        if head.get('ContentLength', 0) <= 0: raise RecoveryError('Une dépendance S3 est absente.')
        for name, value in reference.get('metadata', {}).items():
            if head.get('Metadata', {}).get(name) != value: raise RecoveryError('Une dépendance S3 ne correspond pas à son identité.')
        target = f"{config['prefix']}/{backup_id}/refs/{group}/" + key[len(prefix) + 1:]
        created.append(target)
        if head['ContentLength'] <= 5 * 1024**3:
            copy_unchanged(client, config, key, head['ETag'], lambda **condition: client.copy_object(Bucket=config['bucket'], Key=target, CopySource={'Bucket': config['bucket'], 'Key': key}, MetadataDirective='COPY', **condition))
        else:
            upload = client.create_multipart_upload(Bucket=config['bucket'], Key=target, Metadata=head.get('Metadata', {}))['UploadId']; parts = []
            try:
                for offset in range(0, head['ContentLength'], 64 * 1024**2):
                    number = len(parts) + 1; end = min(head['ContentLength'], offset + 64 * 1024**2) - 1
                    part = copy_unchanged(client, config, key, head['ETag'], lambda **condition: client.upload_part_copy(Bucket=config['bucket'], Key=target, UploadId=upload, PartNumber=number, CopySource={'Bucket': config['bucket'], 'Key': key}, CopySourceRange=f'bytes={offset}-{end}', **condition))
                    parts.append({'PartNumber': number, 'ETag': part['CopyPartResult']['ETag']})
                client.complete_multipart_upload(Bucket=config['bucket'], Key=target, UploadId=upload, MultipartUpload={'Parts': parts})
            except BaseException:
                client.abort_multipart_upload(Bucket=config['bucket'], Key=target, UploadId=upload); raise
        copied = client.head_object(Bucket=config['bucket'], Key=target)
        if copied.get('ContentLength') != head['ContentLength']: raise RecoveryError('Copie S3 de dépendance incomplète.')
        result.append({'originalKey': key, 'recoveryKey': target, 'sourcePrefix': prefix, 'recoveryPrefix': f"{config['prefix']}/{backup_id}/refs/{group}", 'group': group, 'size': head['ContentLength'], 'etag': copied['ETag'], 'snapshotAt': reference.get('snapshotAt') or str(head.get('LastModified') or ''), 'kind': reference.get('kind', group), **{name: reference[name] for name in ['appId', 'imageId', 'volume', 'metadata'] if name in reference}})
    return result


def retired_web_archive(plan, reference):
    """True for a site-backup reference that retention retired during the capture: an archive whose
    row is deleting/deleted, or an image whose every owning backup (backupIds, set by the service's
    prepare step) is retired. An image still pinned by a live backup is always kept."""
    kind, metadata = reference.get('kind'), reference.get('metadata')
    if kind not in ('archive', 'image') or reference.get('required') is True or not isinstance(metadata, dict): return False
    app_id = reference.get('appId')
    owners = [metadata.get('backup-id')] if kind == 'archive' else reference.get('backupIds')
    if not ID.fullmatch(app_id or '') or not isinstance(owners, list) or not owners or len(owners) > 1000 or any(not isinstance(item, str) or not ID.fullmatch(item) for item in owners): return False
    database = pathlib.Path(plan['controller']['database']).absolute()
    connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        connection.execute('PRAGMA query_only=ON')
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_app_backups'").fetchone(): return False
        if kind == 'archive':
            row = connection.execute('SELECT status,operation FROM web_app_backups WHERE id=? AND appId=? AND s3Object=?', (owners[0], app_id, reference.get('key'))).fetchone()
            return bool(row and (row[0] in ('deleting', 'deleted') or row[1] == 'delete'))
        for owner in owners:
            row = connection.execute('SELECT status,operation FROM web_app_backups WHERE id=? AND appId=?', (owner, app_id)).fetchone()
            # A removed row is retired too; any other state still pins the image.
            if row and row[0] not in ('deleting', 'deleted') and row[1] != 'delete': return False
        return True
    finally: connection.close()


def reconcile_retired_archives(plan):
    references = plan.get('s3Refs', []); retained = [item for item in references if not retired_web_archive(plan, item)]
    removed = len(references) - len(retained)
    if removed: plan['s3Refs'] = retained; plan['retiredArchiveReferencesExcluded'] = plan.get('retiredArchiveReferencesExcluded', 0) + removed
    return removed


def copy_references(client, config, plan, backup_id):
    reconcile_retired_archives(plan)
    created = []
    try: return _copy_references(client, config, plan, backup_id, created)
    except BaseException as error:
        for key in created:
            try: client.delete_object(Bucket=config['bucket'], Key=key)
            except Exception: pass
        code = (getattr(error, 'response', {}) or {}).get('Error', {}).get('Code')
        if code in ('404', 'NoSuchKey', 'NotFound') and reconcile_retired_archives(plan):
            return copy_references(client, config, plan, backup_id)
        raise


def purge_capture(client, config, object_key, references=None):
    prefix = config['prefix'] + '/'
    if not isinstance(object_key, str) or not object_key.startswith(prefix) or not object_key.endswith('/platform.phpr'):
        raise RecoveryError('Capture à purger hors du préfixe plateforme.')
    suffix = object_key[len(prefix):]; parts = suffix.split('/')
    if len(parts) != 2 or not re.fullmatch('[A-Za-z0-9-]{1,80}', parts[0]): raise RecoveryError('Identifiant de purge invalide.')
    base = prefix + parts[0] + '/'; commit_key = base + 'commit.json'
    try:
        head = client.head_object(Bucket=config['bucket'], Key=commit_key)
        response = client.get_object(Bucket=config['bucket'], Key=commit_key, IfMatch=head['ETag'])
        try: raw = response['Body'].read(2 * 1024**2 + 1)
        finally: response['Body'].close()
        if len(raw) > 2 * 1024**2: raise RecoveryError('Manifeste de purge trop volumineux.')
        commit = json.loads(raw)
        if commit.get('object') != object_key or commit.get('backupId') != parts[0] or not isinstance(commit.get('references'), list): raise RecoveryError('Manifeste de purge incorrect.')
        keys = commit['references']
    except RecoveryError: raise
    except Exception:
        if not isinstance(references, list): raise RecoveryError('Le stockage ne confirme pas les objets autorisés à purger.') from None
        keys = references
    if len(keys) > 10000 or any(not isinstance(key, str) or not key.startswith(base + 'refs/') or not re.fullmatch(r'[A-Za-z0-9/_@:.+-]+', key) or '..' in key.split('/') for key in keys): raise RecoveryError('Références de purge hors de cette capture.')
    for key in list(dict.fromkeys(keys)) + [object_key, commit_key]: client.delete_object(Bucket=config['bucket'], Key=key)
    return parts[0]


def sweep_orphans(client, config, protected=(), failed=(), min_age_seconds=48 * 3600, now=None, dry_run=False):
    """Remove only provably unusable objects under the platform prefix: incomplete multipart
    uploads and capture folders without commit.json (commit is written last). A committed
    capture unknown to the service (operator CLI, older install) is always kept."""
    prefix = config['prefix'] + '/'; now = now or datetime.datetime.now(datetime.timezone.utc)
    protected, failed = set(protected), set(failed) - set(protected)
    result = {'uploadsAborted': 0, 'objectsDeleted': 0, 'capturesRemoved': [], 'committedKept': 0, 'recentKept': 0, 'foreignKept': 0, 'dryRun': bool(dry_run)}
    def ident_of(key):
        parts = key[len(prefix):].split('/', 1) if isinstance(key, str) and key.startswith(prefix) else []
        return parts[0] if len(parts) == 2 and re.fullmatch('[A-Za-z0-9-]{1,80}', parts[0]) else None
    def old(date): return isinstance(date, datetime.datetime) and (now - date).total_seconds() >= min_age_seconds
    def written(key, ident): return key == prefix + ident + '/platform.phpr' or key.startswith(prefix + ident + '/refs/')
    marker = {}
    for _ in range(1000):
        page = client.list_multipart_uploads(Bucket=config['bucket'], Prefix=prefix, **marker)
        for upload in page.get('Uploads', []) or []:
            ident = ident_of(upload.get('Key'))
            if not ident or ident in protected or not written(upload['Key'], ident) or not (ident in failed or old(upload.get('Initiated'))): continue
            if not dry_run: client.abort_multipart_upload(Bucket=config['bucket'], Key=upload['Key'], UploadId=upload['UploadId'])
            result['uploadsAborted'] += 1
        if not page.get('IsTruncated'): break
        marker = {'KeyMarker': page.get('NextKeyMarker', ''), 'UploadIdMarker': page.get('NextUploadIdMarker', '')}
    folders, token = {}, {}
    for _ in range(100000):
        page = client.list_objects_v2(Bucket=config['bucket'], Prefix=prefix, **token)
        for item in page.get('Contents', []) or []:
            ident = ident_of(item.get('Key'))
            if ident: folders.setdefault(ident, []).append((item['Key'], item.get('LastModified')))
        if not page.get('IsTruncated'): break
        token = {'ContinuationToken': page['NextContinuationToken']}
    for ident, items in sorted(folders.items()):
        if ident in protected: continue
        keys = sorted({key for key, _ in items})
        if prefix + ident + '/commit.json' in keys: result['committedKept'] += 1; continue
        # Only what a capture writes (platform.phpr, refs/...) is ever swept: a folder of another
        # tool or platform sharing the bucket under this prefix by mistake is left untouched.
        if not all(written(key, ident) for key in keys): result['foreignKept'] += 1; continue
        if ident not in failed and not all(old(date) for _, date in items): result['recentKept'] += 1; continue
        if len(keys) > 10000 or any(not re.fullmatch(r'[A-Za-z0-9/_@:.+-]+', key) or '..' in key.split('/') for key in keys): raise RecoveryError('Objets orphelins hors du préfixe de reprise.')
        for key in keys:
            if not dry_run: client.delete_object(Bucket=config['bucket'], Key=key)
            result['objectsDeleted'] += 1
        result['capturesRemoved'].append(ident)
    return result


def capture_to_sink(plan, key, key_path, sink, notify=None):
    writer = EncryptingWriter(sink, key, plan['platform'], plan['backupId'])
    with tarfile.open(fileobj=writer, mode='w|', format=tarfile.USTAR_FORMAT) as archive:
        manifest = Capture(archive, key_path, plan, notify).finish()
    writer.finish()
    return manifest


def load_plan(filename):
    try: plan = json.loads(private_file(filename).read_bytes())
    except RecoveryError: raise
    except Exception: raise RecoveryError('Plan privé de reprise invalide.') from None
    if plan.get('version') != 1 or not re.fullmatch('[a-z0-9][a-z0-9-]{0,40}', plan.get('platform', '')) or not isinstance(plan.get('controller'), dict) or not isinstance(plan['controller'].get('database'), str) or not isinstance(plan.get('instances', []), list): raise RecoveryError('Le plan doit décrire le contrôleur et les instances.')
    ids = [value.get('id') for value in plan.get('instances', [])]
    if len(ids) != len(set(ids)) or len(ids) > 1000 or any(not ID.fullmatch(value or '') for value in ids): raise RecoveryError('Liste d’instances invalide ou dupliquée.')
    for definition in [plan['controller'], *plan.get('instances', [])]:
        if not isinstance(definition.get('database'), str) or definition.get('storageMode', 'local') not in ('local', 'external'): raise RecoveryError('Source de données invalide.')
    policy = validate_file_reference_exclusions(plan.get('fileReferenceExclusions'), set(ids))
    if policy: plan['fileReferenceExclusions'] = policy
    plan['backupId'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(6)
    return plan


def summary(manifest, compressed_bytes=None):
    file_bytes = lambda ident: sum(value['size'] for value in manifest['files'] if value['target'].startswith(('controller/' if ident == 'controller' else f'instances/{ident}/')))
    estimate = {'compressedBytes': compressed_bytes if type(compressed_bytes) is int else None, 'minFreeBytes': MIN_FREE, 'all': restore_budget(manifest), 'allWithHostFiles': restore_budget(manifest, None, True), 'allWithHostFilesAndNativeArchives': restore_budget(manifest, None, True, True)}
    estimate['requiredFreeBytesDefault'] = MIN_FREE + estimate['all']['restoreBytes']
    return {'platform': manifest['platform'], 'backupId': manifest['backupId'], 'startedAt': manifest['startedAt'], 'finishedAt': manifest['finishedAt'], 'databases': [{'id': value['id'], 'snapshotAt': value['snapshotAt'], 'databaseBytes': value['databaseBytes'], 'fileBytes': file_bytes(value['id'])} for value in manifest['databases']], 'files': len(manifest['files']), 'coverage': manifest['coverage'], 'copiedS3Dependencies': len(manifest.get('s3Refs', [])), 'restoreEstimate': estimate}


def budget_check(manifest, target, select=None, host_files=False, native_archives=False, min_free_bytes=MIN_FREE):
    """Answer before any restore: does this selection fit next to the reserve on the target disk?"""
    chosen = set(select or [value['id'] for value in manifest['databases']])
    if chosen - {value['id'] for value in manifest['databases']}: raise RecoveryError('Sélection de bases absente de cette capture.')
    value = restore_budget(manifest, chosen, host_files, native_archives); value['minFreeBytes'] = min_free_bytes; value['requiredFreeBytes'] = min_free_bytes + value['restoreBytes']
    if target:
        parent = pathlib.Path(target).absolute()
        while not parent.exists(): parent = parent.parent
        value['freeBytes'] = shutil.disk_usage(parent).free; value['fits'] = value['freeBytes'] >= value['requiredFreeBytes']
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='operation', required=True)
    backup = sub.add_parser('backup'); backup.add_argument('--plan', required=True); backup.add_argument('--key-file', required=True); backup.add_argument('--output'); backup.add_argument('--no-verify', action='store_true')
    for name in ['inspect', 'restore']:
        command = sub.add_parser(name); command.add_argument('--key-file', required=True); source = command.add_mutually_exclusive_group(required=True); source.add_argument('--input'); source.add_argument('--object')
        command.add_argument('--select', help='controller,instance-id,... (default: all databases)'); command.add_argument('--with-host-files', action='store_true'); command.add_argument('--with-native-archives', action='store_true')
        command.add_argument('--min-free-gib', type=int, default=15, help='Reserve below15 requires --offline; source backups always require15')
        if name == 'inspect': command.add_argument('--target', help='Planned restore target: reports free space and whether the selection fits')
        if name == 'restore':
            command.add_argument('--target', required=True)
            command.add_argument('--offline', action='store_true', help='Explicit isolated/offline drill; never starts services')
            command.add_argument('--max-bytes', type=int, help='Fixed maximum materialized database+files for a service drill')
    arguments = parser.parse_args()
    key = load_key(arguments.key_file)
    if arguments.operation == 'backup':
        plan = load_plan(arguments.plan)
        progress = lambda phase, ident: print(json.dumps({'phase': phase, 'databaseId': ident, 'at': stamp()}), file=sys.stderr, flush=True)
        if arguments.output:
            filename = pathlib.Path(arguments.output).absolute()
            if filename.exists() or filename.resolve() != filename: raise RecoveryError('Le fichier de sortie doit être nouveau, sans lien symbolique.')
            require_space(filename.parent)
            try:
                with open(filename, 'xb') as sink:
                    filename.chmod(0o600); manifest = capture_to_sink(plan, key, arguments.key_file, sink, progress)
                if not arguments.no_verify: inspect_archive(lambda: open(filename, 'rb'), key)
            except BaseException:
                filename.unlink(missing_ok=True); raise
            print(json.dumps({'status': 'complete' if manifest['coverage']['complete'] else 'partial', 'verified': not arguments.no_verify, **summary(manifest, filename.stat().st_size)}))
        else:
            config = s3_settings(); client = s3_client(config); copied = []; sink = None
            object_key = f"{config['prefix']}/{plan['backupId']}/platform.phpr"
            # Announced first so the service can sweep this exact folder after a SIGKILL.
            print(json.dumps({'phase': 'start', 'backupId': plan['backupId'], 'at': stamp()}), file=sys.stderr, flush=True)
            try:
                copied = copy_references(client, config, plan, plan['backupId']); plan['capturedS3Refs'] = copied
                sink = MultipartSink(client, config, object_key, {'format': 'PHPR1', 'platform': plan['platform'], 'backup-id': plan['backupId'], 'key-id': hashlib.sha256(key).hexdigest()[:24]}, notify=lambda count: print(json.dumps({'phase': 'upload', 'encryptedBytes': count, 'at': stamp()}), file=sys.stderr, flush=True))
                manifest = capture_to_sink(plan, key, arguments.key_file, sink, progress); sink.finish()
                if not arguments.no_verify:
                    print(json.dumps({'phase': 'verify', 'at': stamp()}), file=sys.stderr, flush=True)
                    inspect_archive(s3_factory(client, config, object_key), key)
                commit = {'version': 1, 'backupId': plan['backupId'], 'object': object_key, 'createdAt': stamp(), 'complete': manifest['coverage']['complete'], 'verified': not arguments.no_verify, 'references': [value['recoveryKey'] for value in copied]}
                client.put_object(Bucket=config['bucket'], Key=f"{config['prefix']}/{plan['backupId']}/commit.json", Body=encoded(commit), ContentType='application/json')
                print(json.dumps({'status': 'complete' if manifest['coverage']['complete'] else 'partial', 'verified': not arguments.no_verify, 'object': object_key, 'references': [value['recoveryKey'] for value in copied], 'encryptedBytes': sink.size, **summary(manifest, sink.size)}))
            except BaseException:
                if sink: sink.abort()
                for object_to_remove in [object_key, *[value['recoveryKey'] for value in copied]]:
                    try: client.delete_object(Bucket=config['bucket'], Key=object_to_remove)
                    except Exception: pass
                raise
    else:
        if arguments.input:
            filename = pathlib.Path(arguments.input).absolute()
            if filename.resolve() != filename or not filename.is_file(): raise RecoveryError('Archive locale absente ou symbolique.')
            baseline = filename.stat(); compressed = baseline.st_size
            def factory():
                current = filename.stat()
                if (current.st_ino, current.st_size, current.st_mtime_ns) != (baseline.st_ino, baseline.st_size, baseline.st_mtime_ns): raise RecoveryError('L’archive locale a changé entre les lectures.')
                return open(filename, 'rb')
        else:
            config = s3_settings(); factory = s3_factory(s3_client(config), config, arguments.object); compressed = getattr(factory, 'size', None)
        if arguments.operation == 'inspect':
            if not 1 <= arguments.min_free_gib <= 1024: raise RecoveryError('Réserve disque de restauration invalide.')
            manifest = inspect_archive(factory, key)
            budget = budget_check(manifest, arguments.target, arguments.select.split(',') if arguments.select else None, arguments.with_host_files, arguments.with_native_archives, arguments.min_free_gib * 1024**3)
            print(json.dumps({'status': 'authenticated', **summary(manifest, compressed), 'budget': budget}))
        else:
            if not 1 <= arguments.min_free_gib <= 1024 or arguments.min_free_gib < 15 and not arguments.offline: raise RecoveryError('Une réserve inférieure à 15 Gio exige explicitement --offline.')
            result = restore_archive(factory, key, arguments.target, arguments.select.split(',') if arguments.select else None, arguments.with_host_files, arguments.with_native_archives, arguments.min_free_gib * 1024**3, arguments.max_bytes)
            print(json.dumps({'status': 'restored-paused', **result}))


if __name__ == '__main__':
    def interrupted(*_args): raise RecoveryError('Capture interrompue. Les services sources sont conservés.')
    signal.signal(signal.SIGTERM, interrupted)
    try: main()
    except RecoveryError as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}), file=sys.stderr); sys.exit(1)
    except Exception:
        print(json.dumps({'status': 'failed', 'error': 'La reprise a échoué. Vérifiez les permissions, les ressources et la configuration privée.'}), file=sys.stderr); sys.exit(1)
    except KeyboardInterrupt:
        print(json.dumps({'status': 'failed', 'error': 'Capture interrompue par l’administrateur.'}), file=sys.stderr); sys.exit(1)
