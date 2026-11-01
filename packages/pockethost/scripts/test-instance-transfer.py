#!/usr/bin/env python3
"""Run the actual transfer handlers between two disposable Linux PocketBase installations."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def request(base, path, token='', method='GET', body=None, headers=None):
    headers = dict(headers or {})
    if token:
        headers['Authorization'] = token
    if body is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(base + path, method=method, headers=headers,
        data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            data = response.read()
            return json.loads(data) if 'json' in response.headers.get('Content-Type', '') else data
    except urllib.error.HTTPError as error:
        data = error.read().decode()
        raise RuntimeError(f'HTTP {error.code} {method} {path}: {data}') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pocketbase', required=True)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--migrations', required=True, type=Path)
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--keep-running', action='store_true', help='Leave disposable control planes available for browser QA')
    args = parser.parse_args()
    os.umask(0o077)
    root = args.work_dir.resolve() if args.work_dir else Path(tempfile.mkdtemp(prefix='pockethost-transfer-'))
    root.mkdir(parents=True, exist_ok=True)
    processes, logs = [], []
    report = {'checks': {}, 'work_dir': str(root)}
    password = secrets.token_urlsafe(30)
    email = 'transfer-test@example.invalid'
    ports = [port(), port()]
    bases = [f'http://127.0.0.1:{value}' for value in ports]

    def launch(command, folder, env=None):
        log = (folder / 'process.log').open('ab')
        logs.append(log)
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
        processes.append(process)
        return process

    def ready(base, process):
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError('PocketBase exited; inspect process.log')
            try:
                request(base, '/api/health')
                return
            except (urllib.error.URLError, RuntimeError):
                time.sleep(.1)
        raise TimeoutError('PocketBase did not start')

    def expect_failure(call, status):
        try:
            call()
        except RuntimeError as error:
            assert f'HTTP {status} ' in str(error), str(error)
            return
        raise AssertionError(f'Expected HTTP {status}')

    try:
        auths, admins, ordinary = [], [], []
        routes = [
            ('GET', '/api/instance/{id}/backups', 'HandleInstanceBackupsList', 'user'),
            ('GET', '/api/instance/{id}/backups/policy', 'HandleInstanceBackupPolicyGet', 'user'),
            ('GET', '/api/instance/{id}/backups/litestream', 'HandleInstanceLitestreamPolicyGet', 'user'),
            ('DELETE', '/api/instance/{id}/backups/{backupId}', 'HandleInstanceBackupDelete', 'user'),
            ('POST', '/api/instance/{id}/transfers', 'HandleInstanceTransferCreate', 'user'),
            ('GET', '/api/instance-transfers', 'HandleInstanceTransfersList', 'user'),
            ('POST', '/api/instance-transfers/inspect', 'HandleInstanceTransferInspect', 'user'),
            ('POST', '/api/instance-transfers/import', 'HandleInstanceTransferImport', 'user'),
            ('POST', '/api/instance-transfers/{transferId}/cancel', 'HandleInstanceTransferCancel', 'user'),
            ('POST', '/api/instance-transfers/{transferId}/retry', 'HandleInstanceTransferRetry', 'user'),
            ('POST', '/api/instance-transfers/{transferId}/link', 'HandleInstanceTransferRenewLink', 'user'),
            ('GET', '/api/instance-transfers/{transferId}/metadata', 'HandleInstanceTransferMetadata', ''),
            ('POST', '/api/instance-transfers/{transferId}/claim', 'HandleInstanceTransferClaim', ''),
            ('GET', '/api/instance-transfers/{transferId}/archive', 'HandleInstanceTransferArchive', ''),
            ('POST', '/api/instance-transfers/{transferId}/complete', 'HandleInstanceTransferComplete', ''),
            ('POST', '/api/instance-transfers/{transferId}/progress', 'HandleInstanceTransferProgress', ''),
            ('POST', '/__test/run', 'HandleInstanceTransferDispatcher', 'superuser'),
        ]
        for index, base in enumerate(bases):
            folder = root / str(index)
            folder.mkdir()
            hooks = folder / 'hooks'
            hooks.mkdir()
            shutil.copy(args.bundle, hooks / 'mothership.js')
            shutil.copytree(args.migrations, folder / 'migrations', ignore=shutil.ignore_patterns('._*'))
            script = 'routerAdd("GET","/api/versions",(e)=>e.json(200,{versions:["0.39.*"]}));\n'
            script += 'onBootstrap((e)=>{e.next();require(`${__hooks}/mothership`).HandleInstancesResetIdle(e)});\n'
            for method, path, handler, auth in routes:
                middleware = ', $apis.requireAuth()' if auth == 'user' else ', $apis.requireSuperuserAuth()' if auth else ''
                instrument = ''
                if handler == 'HandleInstanceTransferArchive':
                    instrument = '$os.writeFile($os.getenv("DATA_ROOT")+"/range.txt", e.request.header.get("Range") || "none", 0o600);'
                tail = 'return e.json(200, {status:"ok"});' if path == '/__test/run' else 'return result;'
                if handler == 'HandleInstanceTransferProgress':
                    tail = '$os.writeFile($os.getenv("DATA_ROOT")+"/progress-"+e.requestInfo().body.phase+".json", JSON.stringify(e.requestInfo().body), 0o600);' + tail
                script += f'routerAdd({json.dumps(method)}, {json.dumps(path)}, (e) => {{ {instrument} const result = require(`${{__hooks}}/mothership`).{handler}(e); {tail} }}{middleware});\n'
            script += 'onRecordUpdate((e)=>{require(`${__hooks}/mothership`).BeforeUpdate_instanceTransferPower(e);e.next()}, "instances");\n'
            script += 'onRecordDelete((e)=>{require(`${__hooks}/mothership`).BeforeDelete_instanceTransfer(e);e.next()}, "instances");\n'
            script += 'onRecordAfterDeleteSuccess((e)=>{require(`${__hooks}/mothership`).AfterDelete_instanceTransfer(e);e.next()}, "instance_transfers");\n'
            (hooks / 'test.pb.js').write_text(script)
            data = folder / 'data'
            env = dict(os.environ, DATA_ROOT=str(data), APP_URL=base,
                INSTANCE_TRANSFER_ALLOWED_HOSTS=f'127.0.0.1:{ports[1-index]}', INSTANCE_TRANSFER_ALLOW_HTTP='1')
            env.pop('PH_SECRET', None)
            if index == 1:
                # Slow real curl only in this disposable fixture so polling must observe intermediate byte counts.
                tools = folder / 'bin'
                tools.mkdir()
                curl = tools / 'curl'
                curl.write_text('#!/bin/sh\nexec /usr/bin/curl --limit-rate 512K "$@"\n')
                curl.chmod(0o700)
                env['PATH'] = str(tools) + ':' + env['PATH']
            db = data / 'mothership/pb_data'
            command = [args.pocketbase, 'serve', '--dir', str(db), '--http', f'127.0.0.1:{ports[index]}',
                '--migrationsDir', str(folder / 'migrations'), '--hooksDir', str(hooks)]
            process = launch(command, folder, env)
            ready(base, process)
            # Superuser upsert uses the already migrated disposable database.
            subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(db),
                '--migrationsDir', str(folder / 'migrations'), '--hooksDir', str(folder / 'empty-hooks')],
                env=env, check=True, capture_output=True)
            su = request(base, '/api/collections/_superusers/auth-with-password', method='POST', body={'identity': email, 'password': password})['token']
            admins.append(su)
            user = request(base, '/api/collections/users/records', su, 'POST', {
                'email': email, 'password': password, 'passwordConfirm': password, 'verified': True,
                'username': 'transferadmin', 'superAdmin': True, 'subscription_quantity': 100, 'subscription': 'premium',
            })
            auths.append(request(base, '/api/collections/users/auth-with-password', method='POST', body={'identity': email, 'password': password}))
            request(base, '/api/collections/users/records', su, 'POST', {
                'email': 'ordinary@example.invalid', 'password': password, 'passwordConfirm': password,
                'verified': True, 'username': 'ordinaryuser', 'superAdmin': False, 'subscription': 'free',
            })
            ordinary.append(request(base, '/api/collections/users/auth-with-password', method='POST',
                body={'identity': 'ordinary@example.invalid', 'password': password})['token'])

            if index == 0:
                # An upgrade resets running instance statuses before custom migrations create the new collection.
                request(base, '/api/collections/instance_transfers', su, 'DELETE')
                probe = request(base, '/api/collections/instances/records', su, 'POST', {
                    'uid': user['id'], 'subdomain': 'bootstrap-probe', 'version': '0.39.*', 'power': True, 'status': 'running',
                })
                process.terminate()
                process.wait(timeout=10)
                with sqlite3.connect(db / 'data.db') as database:
                    database.execute('DELETE FROM _migrations WHERE file = ?', ('1793145600_created_instance_transfers.js',))
                process = launch(command, folder, env)
                ready(base, process)
                restored_probe = request(base, '/api/collections/instances/records/' + probe['id'], su)
                assert restored_probe['power'] and restored_probe['status'] == 'idle'
                request(base, '/api/collections/instance_transfers', su)
                request(base, '/api/collections/instances/records/' + probe['id'], su, 'DELETE')
                report['checks']['existing_running_instances_bootstrap_before_transfer_migration'] = True

        source, target = bases
        source_auth, target_auth = auths[0]['token'], auths[1]['token']
        instance = request(source, '/api/collections/instances/records', admins[0], 'POST', {
            'uid': auths[0]['record']['id'], 'subdomain': 'transfer-source', 'version': '0.39.*',
            'status': 'idle', 'power': True, 'syncAdmin': False, 'autoVacuum': True,
            'secrets': [{'key': 'PROBE', 'value': 'preserved'}], 'webhooks': [],
        })
        instance_root = root / '0/data/instances' / instance['id']
        business_data = instance_root / 'pb_data'
        business_data.mkdir(parents=True)
        subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(business_data)], check=True, capture_output=True)
        business_port = port()
        business_base = f'http://127.0.0.1:{business_port}'
        business = launch([args.pocketbase, 'serve', '--dir', str(business_data), '--http', f'127.0.0.1:{business_port}'], instance_root)
        ready(business_base, business)
        business_token = request(business_base, '/api/collections/_superusers/auth-with-password', method='POST', body={'identity': email, 'password': password})['token']
        request(business_base, '/api/collections', business_token, 'POST', {
            'name': 'transfer_probe', 'type': 'base', 'fields': [{'name': 'message', 'type': 'text'}],
        })
        record = request(business_base, '/api/collections/transfer_probe/records', business_token, 'POST', {'message': 'kept across servers'})
        business.terminate()
        business.wait(timeout=10)
        for directory in ['pb_public', 'pb_hooks', 'pb_migrations', 'pb_data/storage']:
            (instance_root / directory).mkdir(parents=True, exist_ok=True)
        (instance_root / 'pb_public/probe.txt').write_text('public file preserved')
        (instance_root / 'pb_hooks/probe.pb.js').write_text('// hook preserved\n')
        (instance_root / 'pb_data/storage/probe.bin').write_bytes(os.urandom(4 * 1024 * 1024))
        (instance_root / 'pb_data/.pockethost-totp.key').write_text(secrets.token_hex(16))
        source_hash = hashlib.sha256((business_data / 'data.db').read_bytes()).hexdigest()
        print('Fixtures ready; testing real transfer handlers', flush=True)

        expect_failure(lambda: request(source, f'/api/instance/{instance["id"]}/transfers', ordinary[0], 'POST'), 403)
        report['checks']['superadmin_required'] = True
        result = request(source, f'/api/instance/{instance["id"]}/transfers', source_auth, 'POST')
        export_id, link = result['transfer']['id'], result['link']
        expect_failure(lambda: request(source, f'/api/collections/instances/records/{instance["id"]}', admins[0], 'DELETE'), 400)
        expect_failure(lambda: request(source, f'/api/collections/instances/records/{instance["id"]}', admins[0], 'PATCH', {'power': True}), 400)
        expect_failure(lambda: request(source, f'/api/instance-transfers/{export_id}/metadata', headers={'X-PocketHost-Transfer': 'X'*48}), 403)
        request(source, '/__test/run', admins[0], 'POST')
        source_transfers = request(source, '/api/instance-transfers', source_auth)['transfers']
        assert source_transfers[0]['status'] == 'ready', source_transfers
        assert source_transfers[0]['preparedBytes'] == source_transfers[0]['totalBytes'] > 0
        expect_failure(lambda: request(source, f'/api/instance/{instance["id"]}/backups/{source_transfers[0]["backup"]}', source_auth, 'DELETE'), 400)
        report['checks']['export_and_source_write_lock'] = True

        previous_code = link.split('#')[1]
        renewed_link = request(source, f'/api/instance-transfers/{export_id}/link', source_auth, 'POST')['link']
        expect_failure(lambda: request(source, f'/api/instance-transfers/{export_id}/metadata', headers={'X-PocketHost-Transfer': previous_code}), 403)
        link = renewed_link
        expect_failure(lambda: request(target, '/api/instance-transfers/inspect', target_auth, 'POST', {'link': link.replace(source, 'http://127.0.0.1:1')}), 400)
        request(source, f'/api/collections/instance_transfers/records/{export_id}', admins[0], 'PATCH', {'expiresAt': '2020-01-01 00:00:00.000Z'})
        expect_failure(lambda: request(target, '/api/instance-transfers/inspect', target_auth, 'POST', {'link': link}), 400)
        link = request(source, f'/api/instance-transfers/{export_id}/link', source_auth, 'POST')['link']
        report['checks']['link_rotation_expiry_peer_allowlist_and_archive_pinning'] = True

        preview = request(target, '/api/instance-transfers/inspect', target_auth, 'POST', {'link': link})
        assert preview['archive']['checksum']
        assert 'secrets' not in preview['instance']
        imported = request(target, '/api/instance-transfers/import', target_auth, 'POST', {'link': link, 'subdomain': 'transferred-base'})['transfer']
        import_id = imported['id']
        expect_failure(lambda: request(target, '/api/instance-transfers/import', target_auth, 'POST', {'link': link, 'subdomain': 'duplicate-destination'}), 400)
        report['checks']['single_destination_claim'] = True

        # Fail a first attempt, then resume the same job with an already received archive prefix.
        request(target, f'/api/collections/instance_transfers/records/{import_id}', admins[1], 'PATCH', {'sourceUrl': 'http://127.0.0.1:1'})
        request(target, '/__test/run', admins[1], 'POST')
        failed = request(target, '/api/instance-transfers', target_auth)['transfers'][0]
        assert failed['status'] == 'failed', failed
        request(target, f'/api/collections/instance_transfers/records/{import_id}', admins[1], 'PATCH', {'sourceUrl': source})
        auth_file = root / f'1/data/transfers/{import_id}/curl-auth.conf'
        text = auth_file.read_text()
        headers = dict(re.findall(r'header = "([^:]+): ([^"]+)"', text))
        archive = request(source, f'/api/instance-transfers/{export_id}/archive', headers=headers)
        # A full-sized corrupt partial must fail verification and be removed, so retry can download afresh.
        partial_path = auth_file.parent / 'archive.tar.gz.part'
        partial_path.write_bytes(b'x' * len(archive))
        request(target, f'/api/instance-transfers/{import_id}/retry', target_auth, 'POST')
        request(target, '/__test/run', admins[1], 'POST')
        corrupt = request(target, '/api/instance-transfers', target_auth)['transfers'][0]
        assert corrupt['status'] == 'failed' and not partial_path.exists(), corrupt
        source_state = request(source, '/api/instance-transfers', source_auth)['transfers'][0]
        assert source_state['phase'] == 'failed' and source_state['status'] == 'ready'
        report['checks']['corrupt_complete_partial_is_removed_before_retry'] = True
        progress_path = f'/api/instance-transfers/{export_id}/progress'
        expect_failure(lambda: request(source, progress_path, method='POST', body={'phase': 'downloading', 'downloadedBytes': 1}), 403)
        expect_failure(lambda: request(source, progress_path, method='POST', headers={**headers, 'X-PocketHost-Claim': 'x'*48}, body={'phase': 'downloading', 'downloadedBytes': 1}), 403)
        expect_failure(lambda: request(source, progress_path, method='POST', headers=headers, body={'phase': 'downloading', 'downloadedBytes': len(archive)+1}), 400)
        expect_failure(lambda: request(source, progress_path, method='POST', headers=headers, body={'phase': 'complete', 'downloadedBytes': 1}), 400)
        report['checks']['progress_requires_code_claim_and_valid_byte_counts'] = True
        partial_size = len(archive) // 3
        (auth_file.parent / 'archive.tar.gz.part').write_bytes(archive[:partial_size])
        request(target, f'/api/instance-transfers/{import_id}/retry', target_auth, 'POST')
        observed = {'source': False, 'target': False}
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            worker = executor.submit(request, target, '/__test/run', admins[1], 'POST')
            while not worker.done():
                for label, base, token in [('source', source, source_auth), ('target', target, target_auth)]:
                    live = request(base, '/api/instance-transfers', token)['transfers'][0]
                    if live['phase'] == 'downloading' and partial_size < live['downloadedBytes'] < live['totalBytes']:
                        observed[label] = True
                time.sleep(.15)
            worker.result()
        assert all(observed.values()), observed
        report['checks']['live_byte_progress_visible_on_both_servers'] = True
        for phase in ['verifying', 'restoring']:
            reported = json.loads((root / f'0/data/progress-{phase}.json').read_text())
            assert reported['downloadedBytes'] == len(archive), reported
        completed = request(target, '/api/instance-transfers', target_auth)['transfers'][0]
        assert completed['downloadedBytes'] == completed['totalBytes'] == len(archive)
        source_completed = request(source, '/api/instance-transfers', source_auth)['transfers'][0]
        assert source_completed['phase'] == 'complete' and source_completed['downloadedBytes'] == len(archive)
        assert completed['status'] == 'complete', completed
        assert (root / '0/data/range.txt').read_text() == f'bytes={partial_size}-'
        assert not auth_file.exists()
        report['checks']['retry_and_range_resume'] = True
        target_root = root / '1/data/instances' / imported['instance']
        for relative in ['pb_public/probe.txt', 'pb_hooks/probe.pb.js', 'pb_data/storage/probe.bin', 'pb_data/.pockethost-totp.key']:
            assert (target_root / relative).read_bytes() == (instance_root / relative).read_bytes()
        assert hashlib.sha256((target_root / 'pb_data/data.db').read_bytes()).hexdigest() == source_hash
        assert hashlib.sha256((business_data / 'data.db').read_bytes()).hexdigest() == source_hash
        target_instance = request(target, f'/api/collections/instances/records/{imported["instance"]}', admins[1])
        assert not target_instance['power'] and target_instance['secrets'] == instance['secrets']
        expect_failure(lambda: request(target, '/api/instance-transfers/inspect', target_auth, 'POST', {'link': link}), 400)
        report['checks']['database_files_hooks_and_secrets_preserved'] = True
        report['checks']['source_preserved_and_link_consumed'] = True

        recovered_port = port()
        recovered_base = f'http://127.0.0.1:{recovered_port}'
        recovered = launch([args.pocketbase, 'serve', '--dir', str(target_root / 'pb_data'), '--hooksDir', str(target_root / 'pb_hooks'), '--http', f'127.0.0.1:{recovered_port}'], target_root)
        ready(recovered_base, recovered)
        recovered_token = request(recovered_base, '/api/collections/_superusers/auth-with-password', method='POST', body={'identity': email, 'password': password})['token']
        actual = request(recovered_base, f'/api/collections/transfer_probe/records/{record["id"]}', recovered_token)
        assert actual['message'] == 'kept across servers'
        recovered.terminate()
        recovered.wait(timeout=10)
        report['checks']['restored_pocketbase_runs_and_serves_records'] = True

        # A second export can be cancelled; its original power state is restored.
        request(source, f'/api/collections/instances/records/{instance["id"]}', admins[0], 'PATCH', {'power': True})
        second = request(source, f'/api/instance/{instance["id"]}/transfers', source_auth, 'POST')
        request(source, '/__test/run', admins[0], 'POST')
        request(source, f'/api/instance-transfers/{second["transfer"]["id"]}/cancel', source_auth, 'POST')
        restored_source = request(source, f'/api/collections/instances/records/{instance["id"]}', admins[0])
        assert restored_source['power'] is True
        report['checks']['cancel_restores_source_power'] = True
        disposable = request(target, '/api/collections/instances/records', admins[1], 'POST', {
            'uid': auths[1]['record']['id'], 'subdomain': 'deletion-probe', 'version': '0.39.*', 'status': 'idle', 'power': False,
        })
        cleanup_job = request(target, '/api/collections/instance_transfers/records', admins[1], 'POST', {
            'instance': disposable['id'], 'user': auths[1]['record']['id'], 'direction': 'import', 'status': 'failed', 'expiresAt': '2030-01-01 00:00:00Z',
        })
        cleanup_dir = root / f'1/data/transfers/{cleanup_job["id"]}'
        cleanup_dir.mkdir(parents=True)
        (cleanup_dir / 'curl-auth.conf').write_text('disposable credential')
        request(target, f'/api/collections/instances/records/{disposable["id"]}', admins[1], 'DELETE')
        expect_failure(lambda: request(target, f'/api/collections/instance_transfers/records/{cleanup_job["id"]}', admins[1]), 404)
        assert not cleanup_dir.exists()
        report['checks']['active_deletion_blocked_completed_or_failed_jobs_cleaned'] = True
        report['result'] = 'passed'
        # Fake credentials are stored privately only to support optional browser QA.
        (root / 'browser-fixture.json').write_text(json.dumps({'bases': bases, 'email': email, 'password': password, 'source': instance['id'], 'target': imported['instance']}))
    except Exception as error:
        report['result'] = 'failed'
        report['error'] = str(error)
        raise
    finally:
        if not (args.keep_running and report.get('result') == 'passed'):
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
        for log in logs:
            log.close()
        (root / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
