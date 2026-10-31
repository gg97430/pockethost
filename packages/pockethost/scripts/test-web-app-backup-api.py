#!/usr/bin/env python3
"""Disposable real PocketBase: encrypted web backups, restore concurrency and retention."""
import argparse, hashlib, importlib.util, json, os, pathlib, secrets, shutil, socket, subprocess, sys, tempfile, time, types, urllib.error, urllib.request
from unittest import mock

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True, type=pathlib.Path)
parser.add_argument('--pocketbase', required=True)
args = parser.parse_args()
os.umask(0o077)
temporary = tempfile.TemporaryDirectory(prefix='ph-web-backup-api-')
root = pathlib.Path(temporary.name).resolve()
process = None
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
origin = f'http://127.0.0.1:{port}'
worker_secret = secrets.token_urlsafe(36)
master_key = 'b' * 32


def request(path, token='', body=None, method=None, status=200, private=False, raw=False):
    headers = {'Content-Type': 'application/json', **({'Authorization': token} if token else {}), **({'X-Web-Worker': worker_secret} if private else {})}
    value = urllib.request.Request(origin + path, method=method or ('POST' if body is not None else 'GET'), headers=headers, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(value, timeout=25) as response:
            data = response.read()
            assert response.status == status, (path, response.status, status)
            return (data, response.headers) if raw else json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        data = error.read()
        if error.code == status:
            return json.loads(data)
        raise AssertionError(f'{path}: {error.code}: ' + data.decode()) from None


def api(path, body):
    return request('/api/web-apps/_worker/' + path, body=body, private=True)


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks = root / 'hooks'; hooks.mkdir()
    shutil.copy(source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    routes = [
        ('GET', '/api/web-apps/{id}/backups', 'HandleWebBackupsList'),
        ('POST', '/api/web-apps/{id}/backups', 'HandleWebBackupCreate'),
        ('PUT', '/api/web-apps/{id}/backups/settings', 'HandleWebBackupSettings'),
        ('DELETE', '/api/web-apps/{id}/backups/{backupId}', 'HandleWebBackupDelete'),
        ('GET', '/api/web-apps/{id}/backups/{backupId}/download', 'HandleWebBackupDownload'),
        ('POST', '/api/web-apps/{id}/backups/{backupId}/restore/preview', 'HandleWebBackupRestorePreview'),
        ('POST', '/api/web-apps/{id}/backups/{backupId}/restore', 'HandleWebBackupRestore'),
        ('POST', '/api/web-apps/{id}/backups/{backupId}/recover', 'HandleWebBackupRecover'),
        ('POST', '/api/web-apps/_worker/backups/claim', 'HandleWebBackupClaim'),
        ('POST', '/api/web-apps/_worker/backups/report', 'HandleWebBackupReport'),
        ('POST', '/api/web-apps/_worker/backups/pins', 'HandleWebBackupPins'),
        ('POST', '/api/web-apps/_worker/claim', 'HandleWebWorkerClaim'),
        ('POST', '/api/web-apps/_worker/report', 'HandleWebWorkerReport'),
    ]
    text = ''
    for method, path, handler in routes:
        middleware = '' if '/_worker/' in path else ',$apis.requireAuth("users")'
        text += f'routerAdd({json.dumps(method)},{json.dumps(path)},e=>require(`${{__hooks}}/mothership`).{handler}(e){middleware});\n'
    text += """routerAdd('POST','/test/fixture',e=>{if(!e.auth?.getBool('superAdmin'))throw new ForbiddenError();const m=require(`${__hooks}/mothership`),i=e.requestInfo().body,r=$app.findRecordById('web_apps',i.id);if(i.runtime)m.restoreWebRuntimeBackup(r,i.runtime);if(i.storage)m.restoreWebStorageBackup(r,i.storage);if(i.action==='publish'){r.set('envEncrypted',m.encryptWebValue(i.env));const d=new Record($app.findCollectionByNameOrId('web_deployments'));d.set('appId',r.id);d.set('action','deploy');d.set('status','ready');d.set('commit','c'.repeat(40));d.set('snapshotEncrypted',m.encryptWebValue({config:JSON.parse(r.getString('config')),env:i.env,...(i.runtime?{runtime:i.runtime}:{}),...(i.storage?{storage:i.storage}:{})}));d.set('result',{image:i.images?.web||'ph-web/'+r.id+':'+('d'.repeat(15)),...(i.images?{images:i.images,services:Object.entries(i.images).map(([name,image])=>({name,image,front:name==='web'}))}:{}),available:true});$app.save(d);r.set('activeDeployment',d.id);r.set('desiredCommit','c'.repeat(40));r.set('status','running');}else if(i.action==='change'){if(i.env)r.set('envEncrypted',m.encryptWebValue(i.env));if(i.config)r.set('config',i.config);if(i.name)r.set('name',i.name);}$app.save(r);return e.json(200,{name:r.getString('name'),config:JSON.parse(r.getString('config')),env:m.decryptWebValue(r.getString('envEncrypted')),desiredCommit:r.getString('desiredCommit'),activeDeployment:r.getString('activeDeployment'),runtime:m.runtimeWorkerSnapshot(r),storage:m.storageWorkerSnapshot(r)})},$apis.requireAuth('users'));\n"""
    (hooks / 'test.pb.js').write_text(text)
    env = {**os.environ, 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_MASTER_KEY': master_key, 'WEB_APPS_WORKER_SECRET': worker_secret, 'WEB_APPS_ROOT': str(root / 'web'), 'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid'}
    email, password = 'backup@example.invalid', 'P' + secrets.token_urlsafe(32)
    data = root / 'pb_data'
    subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(data), '--hooksDir', str(root / 'empty-hooks'), '--migrationsDir', str(source / 'pb_migrations')], env=env, check=True, capture_output=True)
    log = (root / 'process.log').open('wb')
    process = subprocess.Popen([args.pocketbase, 'serve', '--dir', str(data), '--hooksDir', str(hooks), '--migrationsDir', str(source / 'pb_migrations'), '--http', f'127.0.0.1:{port}'], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        if process.poll() is not None: raise RuntimeError('PocketBase startup failed')
        try: request('/api/health'); break
        except OSError: time.sleep(.1)
    superuser = request('/api/collections/_superusers/auth-with-password', body={'identity': email, 'password': password})['token']
    def user(name, admin):
        record = request('/api/collections/users/records', superuser, {'email': name + '@example.invalid', 'username': name, 'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': admin, 'subscription': 'premium', 'subscription_quantity': 1})
        token = request('/api/collections/users/auth-with-password', body={'identity': record['email'], 'password': password})['token']
        return record, token
    actor, token = user('backupadmin', True); _, plain = user('backupreader', False); _, other = user('backupother', True)
    config = {'repository': 'owner/repository', 'branch': 'main', 'rootDirectory': '.', 'healthPath': '/', 'generatedDomain': 'web-backup.example.invalid'}
    app = request('/api/collections/web_apps/records', superuser, {'name': 'Backup fixture', 'uid': actor['id'], 'status': 'stopped', 'config': config})
    ident = app['id']; base = '/api/web-apps/' + ident + '/backups'
    published_env = {'SECRET': {'value': 'backup-private-$HOME-\nquotes\'"', 'build': True, 'runtime': True}}
    current_env = {**published_env, 'PENDING': {'value': 'pending-private-value', 'build': False, 'runtime': True}}
    request('/test/fixture', token, {'id': ident, 'action': 'publish', 'env': published_env})
    saved = request('/test/fixture', token, {'id': ident, 'action': 'change', 'env': current_env, 'config': {**config, 'healthPath': '/pending'}})
    request(base, status=401); request(base, plain, status=404); request('/api/collections/web_app_backups/records', token, status=403)
    request('/api/web-apps/_worker/backups/pins', body={}, status=401)
    initial = request(base, token); assert initial['items'] == [] and initial['settings']['retention'] == 3
    # Capability read by the platform recovery service: trigger=platform archives are outside the customer retention.
    assert initial['platformRetention'] == 'excluded'
    created = request(base, token, {}, status=202)['backup']; backup_id = created['id']; path = base + '/' + backup_id
    request(base, token, {}, status=400)
    assert api('backups/pins', {'appId': ident})['items'][0]['imageRef'].startswith('ph-web/' + ident)
    claimed = api('backups/claim', {})['job']; assert claimed['id'] == backup_id and 'payloadEncrypted' in claimed
    spec = importlib.util.spec_from_file_location('backup_module', args.source / 'packages/pockethost/scripts/web_app_backups.py')
    backups = importlib.util.module_from_spec(spec); spec.loader.exec_module(backups)
    image_id = 'sha256:' + 'a' * 64
    def docker(_args): return json.dumps([{'Id': image_id, 'Size': 1024**3, 'Config': {'Labels': {'pockethost.web.app': ident}}}])
    def archive(job):
        with mock.patch.object(backups.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=30 * 1024**3)):
            return backups.create_archive(root / 'web', job, api, docker)
    local = archive(claimed)
    api('backups/report', {'id': backup_id, 'lease': claimed['lease'], 'status': 'running', 's3State': 'uploading', 's3Bytes': 100, 's3TotalBytes': 1000, **local})
    running = request(base, token)['items'][0]; assert running['downloadable'] and running['s3Bytes'] == 100
    content, headers = request(path + '/download', token, raw=True)
    assert headers['Cache-Control'] == 'no-store' and headers['X-Archive-SHA256'] == hashlib.sha256(content).hexdigest()
    assert master_key.encode() not in content and b'backup-private' not in content and b'pending-private' not in content
    api('backups/report', {'id': backup_id, 'lease': claimed['lease'], 'status': 'ready', 's3State': 'failed', 's3Error': 'Copie externe indisponible.', **local})
    ready = request(base, token)['items'][0]; assert ready['downloadable'] and ready['restorable'] and ready['s3State'] == 'failed'
    assert 'payloadEncrypted' not in json.dumps(request(base, token)) and 'private-value' not in json.dumps(request(base, token))
    checks = ['roles_locked_records_queued_pins_encrypted_archive_progress_partial_s3']
    filename = root / 'web/backups' / ident / (backup_id + '.phwb')
    filename.write_bytes(content + b' ')
    request(path + '/restore/preview', token, {}, status=400)
    filename.write_bytes(content)
    preview = request(path + '/restore/preview', token, {})
    assert preview['storedConfigurationIncludesPendingChanges'] and preview['environmentChanges'] == {'added': [], 'updated': [], 'removed': []}
    assert 'backup-private' not in json.dumps(preview) and 'pending-private' not in json.dumps(preview)
    request(path + '/restore', other, {key: preview[key] for key in ['revision', 'expiresAt', 'previewToken']}, status=400)
    request('/test/fixture', token, {'id': ident, 'action': 'change', 'name': 'Concurrent user edit'})
    request(path + '/restore', token, {key: preview[key] for key in ['revision', 'expiresAt', 'previewToken']}, status=400)
    checks.append('archive_integrity_preview_masked_actor_bound_and_stale_revision')
    def restore():
        candidate = request(path + '/restore/preview', token, {})
        return request(path + '/restore', token, {key: candidate[key] for key in ['revision', 'expiresAt', 'previewToken']}, status=202)['deployment']
    job = restore(); request(path, token, method='DELETE', status=400)
    claim = api('claim', {})['job']; assert claim['id'] == job['id'] and claim['action'] == 'restore-backup' and claim['snapshot']['rollbackResult']['image'] == image_id
    assert claim['snapshot']['config']['healthPath'] == '/' and claim['snapshot']['restoreBackup']['app']['config']['healthPath'] == '/pending'
    assert claim['snapshot']['env'] == published_env and claim['snapshot']['restoreBackup']['app']['env'] == current_env
    request('/test/fixture', token, {'id': ident, 'action': 'change', 'name': 'Edit after restore queued'})
    request('/api/web-apps/_worker/report', body={'id': job['id'], 'stageKey': 'publish', 'stage': 'Publication vérifiée'}, private=True, status=400)
    api('report', {'id': job['id'], 'status': 'failed', 'error': 'Concurrent edit prevented restore'})
    state = request('/test/fixture', token, {'id': ident, 'action': 'state'}); assert state['name'] == 'Edit after restore queued'
    job = restore(); api('claim', {}); api('report', {'id': job['id'], 'stageKey': 'publish', 'stage': 'Publication vérifiée'})
    api('report', {'id': job['id'], 'status': 'ready', 'result': {'image': image_id, 'container': 'restored', 'target': 'http://127.0.0.1:1234'}})
    state = request('/test/fixture', token, {'id': ident, 'action': 'state'}); assert state['name'] == saved['name'] and state['config'] == saved['config'] and state['env'] == current_env and state['activeDeployment'] == job['id']
    checks.append('restore_published_image_and_pending_settings_only_after_health_with_publish_conflict_guard')
    settings = {'enabled': True, 'hourUTC': (time.gmtime().tm_hour + 1) % 24, 'minuteUTC': 0, 'retention': 14}
    configured = request(base + '/settings', token, settings, method='PUT')['settings']; assert configured['nextRunAt']; assert api('backups/claim', {})['job'] is None
    for invalid in [{**settings, 'retention': 0}, {**settings, 'retention': 15}, {**settings, 'hourUTC': 24}]: request(base + '/settings', token, invalid, method='PUT', status=400)
    schedule = request('/api/collections/web_app_backup_settings/records', superuser)['items'][0]
    request('/api/collections/web_app_backup_settings/records/' + schedule['id'], superuser, {'nextRunAt': '2020-01-01T00:00:00.000Z'}, method='PATCH')
    scheduled = api('backups/claim', {})['job']; assert scheduled and scheduled['operation'] == 'create'
    record = request('/api/collections/web_app_backups/records/' + scheduled['id'], superuser); assert record['trigger'] == 'scheduled'
    scheduled_local = archive(scheduled); api('backups/report', {'id': scheduled['id'], 'lease': scheduled['lease'], 'status': 'ready', 's3State': 'ready', 's3Object': 'prefix/archive', 's3ImageObject': 'prefix/image', **scheduled_local})
    assert api('backups/claim', {})['job'] is None
    scheduled_path = root / 'web/backups' / ident / (scheduled['id'] + '.phwb')
    remote_copy = scheduled_path.read_bytes(); scheduled_path.unlink()
    absent = next(row for row in request(base, token)['items'] if row['id'] == scheduled['id'])
    assert absent['recoverable'] and not absent['localAvailable'] and not absent['restorable']
    request(base + '/' + scheduled['id'] + '/restore/preview', token, {}, status=400)
    request(base + '/' + scheduled['id'] + '/recover', token, {}, status=202)
    def recover_archive(_root, job, _docker, api=None):
        assert job['operation'] == 'recover' and job['checksum'] == hashlib.sha256(remote_copy).hexdigest() and job['s3Object'] == 'prefix/archive'
        backups._atomic(scheduled_path, remote_copy)
        return {**scheduled_local, 's3State': 'ready', 's3Object': 'prefix/archive', 's3ImageObject': 'prefix/image'}
    with mock.patch.dict(sys.modules, {'web_app_backup_s3': types.SimpleNamespace(ensure_backup_archive=recover_archive)}):
        assert backups.tick(root / 'web', api, docker, force=True)['status'] == 'ready'
    recovered = next(row for row in request(base, token)['items'] if row['id'] == scheduled['id'])
    assert recovered['localAvailable'] and recovered['restorable'] and not recovered['recoverable']
    request(base + '/' + scheduled['id'] + '/restore/preview', token, {})
    checks.append('missing_local_archive_recovered_from_s3_and_restore_preview_available')
    request(base + '/settings', token, {**settings, 'enabled': False, 'retention': 1}, method='PUT')
    deletion = api('backups/claim', {})['job']; assert deletion['id'] == backup_id and deletion['operation'] == 'delete'
    assert any(row['id'] == backup_id for row in api('backups/pins', {'appId': ident})['items'])
    backups.delete_archive(root / 'web', deletion)
    api('backups/report', {'id': deletion['id'], 'lease': deletion['lease'], 'status': 'deleted', 's3State': 'deleted'})
    assert not filename.exists() and all(row['id'] != backup_id for row in api('backups/pins', {'appId': ident})['items'])
    assert request(base, token)['items'][0]['status'] == 'ready'
    checks.append('daily_schedule_once_bounded_retention_delete_and_pin_release')
    for _ in range(24): request('/api/collections/web_app_backups/records', superuser, {'appId': ident, 'status': 'deleted', 'trigger': 'manual'})
    first, second = request(base, token), request(base + '?page=2', token)
    assert len(first['items']) == 20 and len(second['items']) == 6 and first['totalItems'] == 26 and first['totalPages'] == 2
    assert len({row['id'] for row in first['items'] + second['items']}) == 26
    checks.append('full_metadata_pagination_without_secret_fields')
    # Four services, three unique immutable images: every auxiliary is pinned and archived.
    compose_app = request('/api/collections/web_apps/records', superuser, {'name': 'Compose backup fixture', 'uid': actor['id'], 'status': 'stopped', 'config': config})
    compose_id = compose_app['id']; compose_base = '/api/web-apps/' + compose_id + '/backups'
    images = {'web': image_id, 'api': 'sha256:' + 'b' * 64, 'worker': 'sha256:' + 'b' * 64, 'cache': 'sha256:' + 'c' * 64}
    profile = {'kind': 'compose', 'compose': {'front': 'web', 'services': {name: {'image': 'ghcr.io/owner/' + name + '@' + ref} for name, ref in images.items()}}, 'registry': {'server': 'ghcr.io', 'username': 'backup-owner', 'password': 'registry-only-encrypted-value'}}
    storage = [{'name': 'uploads', 'target': '/data/uploads', 'sizeMiB': 128, 'readOnly': False}]
    request('/test/fixture', token, {'id': compose_id, 'action': 'publish', 'env': published_env, 'runtime': profile, 'storage': storage, 'images': images})
    compose_backup = request(compose_base, token, {}, status=202)['backup']; compose_path = compose_base + '/' + compose_backup['id']
    pin = next(row for row in api('backups/pins', {'appId': compose_id})['items'] if row['id'] == compose_backup['id']); assert pin['images'] == images
    compose_claim = api('backups/claim', {})['job']; assert compose_claim['images'] == images and compose_claim['imageRef'] == image_id
    def compose_docker(command):
        ref = command[-1]
        return json.dumps([{'Id': ref, 'Size': 1000 + list(sorted(set(images.values()))).index(ref), 'Config': {'Labels': {'pockethost.web.app': compose_id}}}])
    with mock.patch.object(backups.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=30 * 1024**3)):
        compose_local = backups.create_archive(root / 'web', compose_claim, api, compose_docker)
    assert compose_local['images'] == images and len(compose_local['imageSizes']) == 3 and compose_local['totalImageBytes'] == 3003
    # A partial or foreign image inventory must never become a ready backup.
    for invalid in [{key: value for key, value in compose_local.items() if key not in ['images', 'imageSizes', 'totalImageBytes']}, {**compose_local, 'images': {'web': image_id}}, {**compose_local, 'imageId': 'sha256:' + 'f' * 64}, {**compose_local, 'totalImageBytes': 1}, {**compose_local, 's3State': 'ready', 's3ImageObjects': {image_id: 'prefix/front'}}]:
        request('/api/web-apps/_worker/backups/report', body={'id': compose_backup['id'], 'lease': compose_claim['lease'], 'status': 'ready', **invalid}, private=True, status=400)
    s3_objects = {ref: 'prefix/images/' + ref.split(':')[1] for ref in set(images.values())}
    api('backups/report', {'id': compose_backup['id'], 'lease': compose_claim['lease'], 'status': 'ready', 's3State': 'ready', 's3Object': 'prefix/compose/archive', 's3ImageObjects': s3_objects, **compose_local})
    compose_ready = request(compose_base, token)['items'][0]
    assert compose_ready['imageCount'] == 3 and compose_ready['totalImageBytes'] == 3003 and compose_ready['restorable']
    assert 'registry-only-encrypted-value' not in json.dumps(request(compose_base, token))
    archive_path = root / 'web/backups' / compose_id / (compose_backup['id'] + '.phwb')
    archive_data = json.loads(archive_path.read_bytes()); assert archive_data['images'] == images
    assert b'registry-only-encrypted-value' not in archive_path.read_bytes()
    request('/test/fixture', token, {'id': compose_id, 'action': 'change', 'runtime': {'kind': 'nextjs'}, 'storage': []})
    candidate = request(compose_path + '/restore/preview', token, {})
    assert 'registry-only-encrypted-value' not in json.dumps(candidate)
    restore_job = request(compose_path + '/restore', token, {key: candidate[key] for key in ['revision', 'expiresAt', 'previewToken']}, status=202)['deployment']
    restore_claim = api('claim', {})['job']; snapshot = restore_claim['snapshot']
    assert restore_claim['id'] == restore_job['id'] and snapshot['rollbackResult']['images'] == images
    assert snapshot['runtime'] == profile and snapshot['storage'] == storage
    assert snapshot['restoreBackup']['runtime']['registry']['password'] == 'registry-only-encrypted-value'
    assert snapshot['restoreBackup']['s3ImageObjects'] == s3_objects
    before_ready = request('/test/fixture', token, {'id': compose_id, 'action': 'state'})
    assert before_ready['runtime']['kind'] == 'nextjs' and before_ready['storage'] == []
    api('report', {'id': restore_job['id'], 'stageKey': 'publish', 'stage': 'Publication Compose vérifiée'})
    api('report', {'id': restore_job['id'], 'status': 'ready', 'result': {'image': image_id, 'images': images, 'services': [{'name': name, 'image': ref, 'front': name == 'web'} for name, ref in images.items()], 'container': 'restored-compose', 'target': 'http://127.0.0.1:1234'}})
    after_ready = request('/test/fixture', token, {'id': compose_id, 'action': 'state'})
    assert after_ready['runtime']['kind'] == 'compose' and after_ready['runtime']['registry']['password'] == 'registry-only-encrypted-value'
    assert after_ready['storage'] == storage and after_ready['activeDeployment'] == restore_job['id']
    checks.append('compose_all_images_pinned_encrypted_sized_unique_and_restorable_without_registry')
    # A stopped restored Compose can retain a stale availability report even
    # while both immutable images exist. Revalidation must happen in the worker.
    stopped_app = request('/api/collections/web_apps/records', superuser, {'name': 'Stopped image revalidation', 'uid': actor['id'], 'status': 'stopped', 'config': config})
    stopped_id = stopped_app['id']; stopped_base = '/api/web-apps/' + stopped_id + '/backups'
    stopped_images = {'web': 'sha256:' + 'd' * 64, 'cache': 'sha256:' + 'e' * 64}
    stopped_profile = {'kind': 'compose', 'compose': {'front': 'web', 'services': {name: {'image': 'ghcr.io/owner/' + name + '@' + ref} for name, ref in stopped_images.items()}}}
    published = request('/test/fixture', token, {'id': stopped_id, 'action': 'publish', 'env': published_env, 'runtime': stopped_profile, 'images': stopped_images})
    api('report', {'id': published['activeDeployment'], 'retainedImages': []})
    # Active applications with unavailable metadata keep the original guard.
    request(stopped_base, token, {}, status=400)
    request('/api/collections/web_apps/records/' + stopped_id, superuser, {'status': 'stopped'}, method='PATCH')
    stopped_backup = request(stopped_base, token, {}, status=202)['backup']; missing_claim = api('backups/claim', {})['job']
    assert missing_claim['id'] == stopped_backup['id'] and missing_claim['images'] == stopped_images
    def stopped_docker(command):
        ref = command[-1]
        return json.dumps([{'Id': ref, 'Size': 2048, 'Config': {'Labels': {'pockethost.web.app': stopped_id}}}])
    def missing_auxiliary(command):
        if command[-1] == stopped_images['cache']: raise RuntimeError('Missing physical cache image')
        return stopped_docker(command)
    try:
        with mock.patch.object(backups.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=30 * 1024**3)):
            backups.create_archive(root / 'web', missing_claim, api, missing_auxiliary)
        raise AssertionError('An absent auxiliary image was accepted')
    except backups.BackupError:
        pass
    assert not (root / 'web/backups' / stopped_id / (stopped_backup['id'] + '.phwb')).exists()
    api('backups/report', {'id': stopped_backup['id'], 'lease': missing_claim['lease'], 'status': 'failed', 'error': 'Image auxiliaire absente.'})
    assert request('/api/collections/web_app_backups/records/' + stopped_backup['id'], superuser)['status'] == 'failed'
    verified_backup = request(stopped_base, token, {}, status=202)['backup']; verified_claim = api('backups/claim', {})['job']
    with mock.patch.object(backups.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=30 * 1024**3)):
        verified_local = backups.create_archive(root / 'web', verified_claim, api, stopped_docker)
    assert verified_local['images'] == stopped_images and verified_local['totalImageBytes'] == 4096
    api('backups/report', {'id': verified_backup['id'], 'lease': verified_claim['lease'], 'status': 'ready', 's3State': 'ready', 's3Object': 'prefix/stopped/archive', 's3ImageObjects': {ref: 'prefix/stopped/' + ref.split(':')[1] for ref in stopped_images.values()}, **verified_local})
    verified_row = next(item for item in request(stopped_base, token)['items'] if item['id'] == verified_backup['id'])
    assert verified_row['imageCount'] == 2 and verified_row['status'] == 'ready' and verified_row['restorable']
    assert set(stopped_images.values()) <= backups.protected_images(root / 'web', stopped_id, api)
    checks.append('stopped_stale_availability_requires_both_physical_owned_images_before_ready_while_active_guard_remains')
    def backup_row(identifier): return request('/api/collections/web_app_backups/records/' + identifier, superuser)
    # Crash loop: every claim counts a lease; the third lost lease ends in failed (one backup_failed alert) instead of
    # an endless retry.
    crash = request(stopped_base, token, {}, status=202)['backup']
    for attempt in (1, 2, 3):
        job = api('backups/claim', {})['job']
        assert job['id'] == crash['id'] and job['attempt'] == attempt and job['maxAttempts'] == 3, job
        request('/api/collections/web_app_backups/records/' + crash['id'], superuser, {'leaseAt': '2000-01-01 00:00:00.000Z'}, method='PATCH')
    assert api('backups/claim', {})['job'] is None
    row = backup_row(crash['id']); assert row['status'] == 'failed' and 'interrompue trois fois' in row['error'] and row['leaseAttempts'] == 3
    checks.append('three_lost_leases_end_in_failed_with_attempt_and_max_attempts_in_each_claim')
    exhausted = request(stopped_base, token, {}, status=202)['backup']; job = api('backups/claim', {})['job']
    request('/api/web-apps/_worker/backups/report', body={'id': exhausted['id'], 'lease': job['lease'], 'status': 'failed', 'failureCode': 'oops'}, private=True, status=400)
    api('backups/report', {'id': exhausted['id'], 'lease': job['lease'], 'status': 'failed', 'failureCode': 'attempts_exhausted', 'attempts': 4, 'error': 'Arrêt du worker.'})
    assert backup_row(exhausted['id'])['error'].startswith('Sauvegarde abandonnée après 4 tentatives interrompues.')
    request(stopped_base + '/' + verified_backup['id'], token, method='DELETE', status=202)
    job = api('backups/claim', {})['job']; assert job['id'] == verified_backup['id'] and job['operation'] == 'delete'
    api('backups/report', {'id': job['id'], 'lease': job['lease'], 'status': 'failed', 'failureCode': 'attempts_exhausted', 'attempts': 4, 'error': 'Suppression abandonnée après 4 tentatives.'})
    assert backup_row(job['id'])['status'] == 'failed' and api('backups/claim', {})['job'] is None
    checks.append('worker_failure_codes_validated_and_exhausted_delete_ends_failed_instead_of_looping')
    # Scheduled queue failure (no backup row is created): one pushed backup_failed event per occurrence.
    blocking = request(stopped_base, token, {}, status=202)['backup']
    request(stopped_base + '/settings', token, {'enabled': True, 'hourUTC': 3, 'minuteUTC': 0, 'retention': 3}, method='PUT')
    schedule = next(item for item in request('/api/collections/web_app_backup_settings/records?perPage=100', superuser)['items'] if item['appId'] == stopped_id)
    request('/api/collections/web_app_backup_settings/records/' + schedule['id'], superuser, {'nextRunAt': '2020-01-01T00:00:00.000Z'}, method='PATCH')
    assert api('backups/claim', {})['job']['id'] == blocking['id']
    api('backups/claim', {})
    events = [item for item in request('/api/collections/web_alert_events/records?perPage=100', superuser)['items'] if item['appId'] == stopped_id]
    assert len(events) == 1 and events[0]['event'] == 'backup_failed' and events[0]['details']['reason'] == 'queue_failed', events
    assert events[0]['scope'] == 'app' and 'déjà en attente' in events[0]['details']['summary'], events
    checks.append('scheduled_queue_failure_pushes_exactly_one_backup_failed_event')
    # Platform recovery archives: superAdmin only, never counted in the customer's retention, newest one kept.
    request(compose_base, token, {'trigger': 'manual'}, status=400); request(compose_base, token, {'other': True}, status=400)
    platform_backup = request(compose_base, token, {'trigger': 'platform'}, status=202)['backup']
    assert backup_row(platform_backup['id'])['trigger'] == 'platform'
    request('/api/collections/web_app_backups/records/' + platform_backup['id'], superuser, {'status': 'failed'}, method='PATCH')
    extra = []
    for trigger in ['manual', 'manual', 'platform', 'platform']:
        extra.append(request('/api/collections/web_app_backups/records', superuser, {'appId': compose_id, 'status': 'ready', 'trigger': trigger})['id']); time.sleep(0.02)
    api('backups/claim', {})
    states = {identifier: backup_row(identifier)['status'] for identifier in [compose_backup['id'], *extra]}
    assert [states[i] for i in [compose_backup['id'], extra[0], extra[1], extra[3]]] == ['ready'] * 4 and states[extra[2]] == 'deleting', states
    checks.append('platform_trigger_superadmin_only_outside_customer_retention_newest_platform_archive_kept')
    print(json.dumps({'passed': True, 'checks': checks}), flush=True)
except Exception:
    if (root / 'process.log').exists(): print((root / 'process.log').read_text()[-2500:])
    raise
finally:
    if process:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
    temporary.cleanup()
