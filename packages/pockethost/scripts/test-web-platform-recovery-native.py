#!/usr/bin/env python3
"""Real PocketBase databases reconstructed without starting any restored service."""
import argparse, hashlib, importlib.util, io, json, os, pathlib, secrets, sqlite3, subprocess, tempfile
from unittest import mock
import types

p = argparse.ArgumentParser(); p.add_argument('--source', type=pathlib.Path, required=True); p.add_argument('--pocketbase', required=True)
p.add_argument('--native-start', action='store_true', help='Also start PocketBase on the restored paused copies (loopback only; namespace check mocked off-Linux)'); args = p.parse_args()
spec = importlib.util.spec_from_file_location('platform_recovery', args.source / 'packages/pockethost/scripts/web_platform_recovery.py')
r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)
import contextlib, sys; sys.modules['web_platform_recovery'] = r  # The probe and the un-pause import the engine by name.
unpause_spec = importlib.util.spec_from_file_location('web_platform_recovery_unpause', args.source / 'packages/pockethost/scripts/web_platform_recovery_unpause.py')
unpause = importlib.util.module_from_spec(unpause_spec); unpause_spec.loader.exec_module(unpause)
os.umask(0o077)
GIT_MASTER, GIT_CANONICAL = 'G' * 32, 'gitlab.com/team/private-site'


def run_unpause(*arguments):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err): code = unpause.main(['--root', str(root / 'restored'), *arguments])
    assert code == 0, err.getvalue()
    return json.loads(out.getvalue())


with tempfile.TemporaryDirectory(prefix='ph-platform-native-') as temporary:
    root = pathlib.Path(temporary).resolve(); key = secrets.token_bytes(32); keyfile = root / 'external.key'; keyfile.write_text(key.hex()); keyfile.chmod(0o600)
    controller, instance = root / 'controller', root / 'instance'; ident = 'a' * 15
    password = 'P' + secrets.token_urlsafe(32)
    command = [args.pocketbase, 'superuser', 'upsert', 'isolated@example.invalid', password]
    subprocess.run(command + ['--dir', str(controller), '--hooksDir', str(root / 'empty-hooks'), '--migrationsDir', str(args.source / 'packages/pockethost/src/mothership-app/pb_migrations')], check=True, capture_output=True)
    subprocess.run([args.pocketbase, 'migrate', 'up', '--dir', str(controller), '--hooksDir', str(root / 'empty-hooks'), '--migrationsDir', str(args.source / 'packages/pockethost/src/mothership-app/pb_migrations')], check=True, capture_output=True)
    subprocess.run(command + ['--dir', str(instance), '--hooksDir', str(root / 'empty-hooks'), '--migrationsDir', str(root / 'empty-migrations')], check=True, capture_output=True)
    with sqlite3.connect(controller / 'data.db') as connection:
        columns = {row[1] for row in connection.execute('PRAGMA table_info(instances)')}
        values = {'id': ident}
        if 'name' in columns: values['name'] = 'isolated-native-fixture'
        if 'subdomain' in columns: values['subdomain'] = 'isolated-native-fixture'
        if 'status' in columns: values['status'] = 'stopped'
        if 'uid' in columns: values['uid'] = 'b' * 15
        version = subprocess.run([args.pocketbase, '--version'], check=True, capture_output=True, text=True).stdout.split()[-1].lstrip('v')
        if 'version' in columns: values['version'] = version
        fields = ','.join('"' + name + '"' for name in values); placeholders = ','.join('?' for _ in values)
        connection.execute('INSERT INTO instances (' + fields + ') VALUES (' + placeholders + ')', list(values.values()))
        # Real migrated schema: one Git deploy-key source with a live webhook and one usable CI token.
        deploy_key = unpause.pb_encrypt({'privateKey': '-----BEGIN OPENSSH PRIVATE KEY-----\nnative-fixture\n'}, GIT_MASTER.encode())
        connection.execute('INSERT INTO web_git_sources (id,host,port,sshUser,path,canonical,webhookId,privateKeyEncrypted,webhookSecretEncrypted,pendingHostKeysEncrypted) VALUES (?,?,?,?,?,?,?,?,?,?)', ('c' * 15, 'gitlab.com', 22, 'git', 'team/private-site', GIT_CANONICAL, 'C' * 24, deploy_key, unpause.pb_encrypt({'secret': 's' * 48}, GIT_MASTER.encode()), 'pending-scan'))
        connection.execute('INSERT INTO web_ci_tokens (id,appId,actorId,name,tokenHash,imageRepository,expiresAt) VALUES (?,?,?,?,?,?,?)', ('d' * 15, 'e' * 15, 'f' * 15, 'ci', '0' * 64, 'ghcr.io/team/private-site', '2999-01-01 00:00:00.000Z'))
        source_collections = connection.execute('SELECT count(*) FROM _collections').fetchone()[0]
    connection.close()
    # Real, default-size SQL parts: >12 MiB of TEXT becomes >24 MiB SQL,
    # spanning at least four independently committed blocks on restoration.
    large_value = 'native-block-boundary-' + 'x' * (1024**2) + '\x00after-nul-😀'
    with sqlite3.connect(instance / 'data.db') as connection:
        connection.execute('CREATE TABLE recovery_large_rows(id INTEGER PRIMARY KEY,payload TEXT)')
        connection.executemany('INSERT INTO recovery_large_rows VALUES(?,?)', [(index, large_value + str(index)) for index in range(12)])
    connection.close()
    sidecar_values = {}
    for directory in [controller, instance]:
        value = secrets.token_urlsafe(24).encode(); key_sidecar = directory / r.TOTP_SIDECAR; key_sidecar.write_bytes(value); key_sidecar.chmod(0o600); sidecar_values[directory] = value
    plan = {'version': 1, 'platform': 'app1', 'backupId': 'native-fixture', 'controller': {'database': str(controller / 'data.db'), 'initializeWalIndex': True}, 'instances': [{'id': ident, 'database': str(instance / 'data.db'), 'initializeWalIndex': True}], 'metadata': {'test': 'native-pocketbase'}}
    if args.native_start:
        probe_spec = importlib.util.spec_from_file_location('platform_probe', args.source / 'packages/pockethost/scripts/web_platform_recovery_probe.py'); probe = importlib.util.module_from_spec(probe_spec); probe_spec.loader.exec_module(probe)
        arch = probe.native_arch(); runtime = root / 'runtime'; binary = runtime / version / arch / 'pocketbase'; binary.parent.mkdir(parents=True)
        import shutil; shutil.copyfile(args.pocketbase, binary); binary.chmod(0o755)
        plan['hostPaths'] = [{'source': str(runtime), 'target': 'platform/pocketbase'}]
    archive = io.BytesIO()
    source_hashes = {source: hashlib.sha256(source.read_bytes()).hexdigest() for source in [controller / 'data.db', instance / 'data.db']}
    with mock.patch.object(r.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=100 * 1024**3)):
        manifest = r.capture_to_sink(plan, key, keyfile, archive)
        large_blocks = len(next(value for value in manifest['databases'] if value['id'] == ident)['sqlObjects'])
        assert large_blocks > 3, 'The real native fixture did not cross enough default SQL block boundaries'
        assert all(hashlib.sha256(source.read_bytes()).hexdigest() == checksum for source, checksum in source_hashes.items()), 'Source database file changed during read-only capture'
        content = archive.getvalue()
        r.inspect_archive(lambda: io.BytesIO(content), key)
        marker = r.restore_archive(lambda: io.BytesIO(content), key, root / 'restored')
    for path in [root / 'restored/controller/pb_data/data.db', root / f'restored/instances/{ident}/pb_data/data.db']:
        with sqlite3.connect(path) as connection:
            assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert connection.execute('SELECT count(*) FROM _superusers').fetchone()[0] == 1
            settings = json.loads(connection.execute("SELECT value FROM _params WHERE id='settings'").fetchone()[0])
            assert settings['smtp']['enabled'] is False and settings['s3']['enabled'] is False and settings['backups']['cron'] == ''
        source_dir = controller if path.is_relative_to(root / 'restored/controller') else instance
        restored_key = path.parent / r.TOTP_SIDECAR
        assert restored_key.read_bytes() == sidecar_values[source_dir] and restored_key.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(root / 'restored/controller/pb_data/data.db') as connection:
        assert connection.execute('SELECT count(*) FROM _collections').fetchone()[0] == source_collections
        assert connection.execute('SELECT id FROM instances').fetchone()[0] == ident
    with sqlite3.connect(root / f'restored/instances/{ident}/pb_data/data.db') as connection:
        assert connection.execute('SELECT id,payload FROM recovery_large_rows ORDER BY id').fetchall() == [(index, large_value + str(index)) for index in range(12)]
    assert marker['paused'] and marker['requiredSystemd']['PrivateNetwork']
    assert all(value['nativeSmtpAndBackupsDisabled'] for value in marker['databasePauses'].values())
    pauses = json.loads((root / 'restored/RECOVERY_PAUSED.json').read_text())['databasePauses']['controller']
    assert pauses['credentialsKept'] == {'gitSources': {'count': 1, 'hosts': ['gitlab.com'], 'webhooks': 1, 'deployKeys': 1}, 'ciTokensActive': 1}, pauses
    assert 'private-site' not in (root / 'restored/RECOVERY_PAUSED.json').read_text()  # Hosts only, never repository names.
    assert not (root / 'restored/controller/pb_hooks').exists()
    native_healthy = None
    if args.native_start:
        # The engine's paused BLOB/TEXT settings, rotated tokenKeys and empty migrations must boot.
        restored_root, _marker, restored_manifest, definitions, proof = probe.verify_restore(root / 'restored', 2)
        with mock.patch.object(probe, 'isolated_network', return_value=True):
            probe.native_probe(restored_root, restored_manifest, definitions, runtime, version, proof, arch=arch)
            probe.native_probe(restored_root, restored_manifest, definitions, runtime, version, proof, arch=arch)  # Retry on the same copy.
        assert proof['nativeHealthy'] == 4 and proof['previousNativeAttemptsRemoved'] == 1 and proof['nativeProcessesStopped'], proof
        # Real-loss order (runbook §5.6 then §5.8): the un-pause dry-run must open the copies PocketBase left in WAL.
        wal = [r.wal_header(path) for path in [root / 'restored/controller/pb_data/data.db', root / f'restored/instances/{ident}/pb_data/data.db']]
        assert run_unpause('--native-smtp', '--native-s3', '--native-backups')['mode'] == 'dry-run' and json.loads((root / 'restored/RECOVERY_PAUSED.json').read_text())['paused'] is True
        native_healthy = 2
    # Restored-copy hardening on the real controller schema (after the native start: WAL copy, checkpointed by --apply).
    master = root / 'web-master.key'; master.write_text(GIT_MASTER); master.chmod(0o600); secrets_file = root / 'rotated-secrets.json'
    restored_controller = root / 'restored/controller/pb_data/data.db'; before = restored_controller.read_bytes()
    dry = run_unpause('--rotate-webhooks', '--master-key-file', str(master))
    assert dry['databases']['controller'] == {'gitWebhookSecretsRotated': 1} and unpause.GIT_KEY_WARNING in dry['warnings'] and any('--revoke-ci-tokens' in item for item in dry['warnings']), dry
    assert restored_controller.read_bytes() == before and GIT_CANONICAL not in json.dumps(dry)
    applied = run_unpause('--revoke-ci-tokens', '--rotate-webhooks', '--master-key-file', str(master), '--secrets-out', str(secrets_file), '--apply')
    assert applied['databases']['controller'] == {'ciTokensRevoked': 1, 'gitWebhookSecretsRotated': 1}, applied
    rotated = json.loads(secrets_file.read_text()); assert secrets_file.stat().st_mode & 0o777 == 0o600 and rotated['gitWebhookIds'] == {GIT_CANONICAL: 'C' * 24}
    for suffix in ('-wal', '-shm'): pathlib.Path(str(restored_controller) + suffix).unlink(missing_ok=True)  # data.db alone carries the edit.
    with sqlite3.connect(restored_controller) as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        private_key, webhook, pending = connection.execute('SELECT privateKeyEncrypted,webhookSecretEncrypted,pendingHostKeysEncrypted FROM web_git_sources').fetchone()
        assert connection.execute("SELECT count(*) FROM web_ci_tokens WHERE revokedAt=''").fetchone()[0] == 0
    connection.close()
    secret = unpause.pb_decrypt(webhook, GIT_MASTER.encode())['secret']
    assert secret == rotated['gitWebhookSecrets'][GIT_CANONICAL] and len(secret) >= 32 and secret != 's' * 48 and secret not in json.dumps(applied)
    assert private_key == deploy_key and pending == '' and json.loads((root / 'restored/RECOVERY_PAUSED.json').read_text())['paused'] is True
    print(json.dumps({'passed': True, 'nativeStartHealthy': native_healthy, 'walAfterNativeStart': wal if args.native_start else None, 'nativePocketBaseDatabases': 2, 'collectionSchemasPreserved': source_collections, 'sourceDatabaseChecksumsUnchanged': True, 'nativeSmtpBackupsAndS3Disabled': True, 'mfaSidecarsRestoredWithDatabase': 2, 'largeDefaultSqlBlocksRestored': large_blocks, 'largeTextBytes': len(large_value.encode()) * 12, 'integrity': 'ok', 'restoredServicesStarted': 0, 'paused': True, 'gitWebhooksRotatedOnRealSchema': 1, 'ciTokensRevokedOnRealSchema': 1}))
