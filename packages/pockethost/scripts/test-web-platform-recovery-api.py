#!/usr/bin/env python3
"""Native PocketBase recovery control: administrator-only fixed runner and private audit."""
import argparse
import json
import os
import pathlib
import secrets
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True, type=pathlib.Path)
parser.add_argument('--pocketbase', required=True)
parser.add_argument('--bundle', type=pathlib.Path)
args = parser.parse_args()
os.umask(0o077)
temporary = tempfile.TemporaryDirectory(prefix='ph-platform-recovery-api-')
root = pathlib.Path(temporary.name).resolve()
process = None
checks = []
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
origin = f'http://127.0.0.1:{port}'


def request(path, token='', body=None, method=None, status=200):
    call = urllib.request.Request(origin + path, method=method or ('POST' if body is not None else 'GET'), headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {})}, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(call, timeout=15) as response:
            raw = response.read(); assert response.status == status, (path, response.status, status)
            if path.startswith('/api/web-platform-recovery'): assert response.headers.get('Cache-Control') == 'no-store'
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        if error.code == status: return json.loads(raw)
        raise AssertionError(f'{path}: {error.code}: ' + raw.decode()) from None


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks = root / 'hooks'; hooks.mkdir()
    shutil.copy(args.bundle or source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    routes = [('GET', '', 'HandleWebPlatformRecoveryStatus'), ('GET', '/runs', 'HandleWebPlatformRecoveryRuns'), ('PUT', '/settings', 'HandleWebPlatformRecoverySettings'), ('POST', '/captures', 'HandleWebPlatformRecoveryCapture'), ('POST', '/captures/{backupId}/drill', 'HandleWebPlatformRecoveryDrill')]
    (hooks / 'test.pb.js').write_text('\n'.join('routerAdd(' + json.dumps(method) + ',' + json.dumps('/api/web-platform-recovery' + path) + ',e=>require(`${__hooks}/mothership`).' + handler + '(e),$apis.requireAuth("users"));' for method, path, handler in routes))
    state = root / 'private-state'; state.mkdir(mode=0o700)
    runner = root / 'fixed_runner.py'
    runner.write_text('''import json,os,pathlib,sys
state=pathlib.Path(os.environ['WEB_PLATFORM_RECOVERY_STATE_DIR'])
if (state/'fail').exists():sys.exit(2)
assert sys.argv[1]=='api'
op=sys.argv[2];value=json.loads(sys.argv[3]);status=200
with(state/'calls.jsonl').open('a')as stream:stream.write(json.dumps({'operation':op,'input':value})+'\\n')
job={'id':'1'*32,'operation':'capture','status':'queued'}
if op=='status':response={'configured':True,'executionEnabled':False,'planCheck':{'checked':True,'drift':True,'added':2,'removed':1,'checkedAt':'2026-10-29T02:00:00Z'},'worker':{'online':True},'settings':{'enabled':True,'hourUTC':2,'minuteUTC':30,'retention':3,'nextRunAt':'','plannedRunAt':'2026-10-30T02:30:00Z','status':'executionDisabled','statusMessage':'Verrou.'},'latest':{**job,'status':'ready','trigger':'scheduled','webBackups':{'created':1,'reused':2,'staleReused':1}},'active':None}
elif op=='runs':response={'items':[job],'page':value['page'],'perPage':20,'totalItems':21,'totalPages':2}
elif op=='settings':response={'settings':{k:v for k,v in value.items()if k not in('actorId','runToday')},'immediate':value.get('runToday') is True}
elif op=='capture':
 status=409 if(state/'active').exists()else202;response={'error':'Une opération est déjà active.'}if status==409 else{'job':job}
elif op=='drill':status=202;response={'job':{**job,'operation':'drill'}}
else:raise AssertionError()
print(json.dumps({'status':status,'response':response}))
'''.replace('else202', 'else 202'))
    env = {**os.environ, 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_MASTER_KEY': 'r' * 32, 'WEB_PLATFORM_RECOVERY_RUNNER': str(runner), 'WEB_PLATFORM_RECOVERY_STATE_DIR': str(state), 'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid'}
    data = root / 'pb_data'; email, password = 'recovery@example.invalid', 'P' + secrets.token_urlsafe(32)
    subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(data), '--hooksDir', str(root / 'empty'), '--migrationsDir', str(source / 'pb_migrations')], env=env, check=True, capture_output=True)
    log = (root / 'process.log').open('wb')
    process = subprocess.Popen([args.pocketbase, 'serve', '--dir', str(data), '--hooksDir', str(hooks), '--migrationsDir', str(source / 'pb_migrations'), '--http', f'127.0.0.1:{port}'], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        if process.poll() is not None: raise RuntimeError('PocketBase startup failed')
        try: request('/api/health'); break
        except OSError: time.sleep(.1)
    su = request('/api/collections/_superusers/auth-with-password', body={'identity': email, 'password': password})['token']
    def user(name, admin):
        row = request('/api/collections/users/records', su, {'email': name + '@example.invalid', 'username': name, 'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': admin, 'subscription': 'premium', 'subscription_quantity': 1})
        return row, request('/api/collections/users/auth-with-password', body={'identity': row['email'], 'password': password})['token']
    actor, token = user('recoveryadmin', True); _, plain = user('recoveryplain', False)
    base = '/api/web-platform-recovery'; backup = 'a' * 32
    for method, endpoint, body in [('GET', '', None), ('GET', '/runs', None), ('PUT', '/settings', {}), ('POST', '/captures', {}), ('POST', '/captures/' + backup + '/drill', {})]:
        request(base + endpoint, body=body, method=method, status=401)
        request(base + endpoint, plain, body, method=method, status=403)
    request('/api/collections/web_platform_recovery_audit/records', token, status=403)
    assert not (state / 'calls.jsonl').exists()
    checks.append('all_endpoints_administrator_only_and_audit_collection_locked')
    status = request(base, token); assert status['configured']
    assert status['executionEnabled'] is False and status['planCheck'] == {'checked': True, 'drift': True, 'added': 2, 'removed': 1, 'checkedAt': '2026-10-29T02:00:00Z'}
    assert status['settings']['status'] == 'executionDisabled' and status['settings']['statusMessage'] == 'Verrou.' and status['settings']['nextRunAt'] == '' and status['settings']['plannedRunAt'] == '2026-10-30T02:30:00Z'
    assert status['latest']['trigger'] == 'scheduled' and status['latest']['webBackups'] == {'created': 1, 'reused': 2, 'staleReused': 1}
    checks.append('status_passes_schedule_status_plan_check_and_execution_lock_unchanged')
    history = request(base + '/runs?page=2', token); assert history['page'] == 2 and history['totalPages'] == 2
    count = len((state / 'calls.jsonl').read_text().splitlines())
    settings = {'enabled': True, 'hourUTC': 2, 'minuteUTC': 30, 'retention': 30}
    for invalid in [{**settings, 'hourUTC': 24}, {**settings, 'minuteUTC': 60}, {**settings, 'retention': 31}, {**settings, 'retention': 0}, {**settings, 'enabled': 'true'}, {**settings, 'plan': '/etc/shadow'}, {**settings, 'runToday': 'yes'}, {**settings, 'runToday': 1}, {**settings, 'runNow': True}]: request(base + '/settings', token, invalid, method='PUT', status=400)
    for query in ['0', '-1', '1.5', 'abc', '100001']: request(base + '/runs?page=' + query, token, status=400)
    request(base + '/captures', token, {'command': 'cat /etc/shadow'}, status=400)
    request(base + '/captures/' + backup + '/drill', token, {'target': '/srv/production'}, status=400)
    request(base + '/captures/not-a-valid-id/drill', token, {}, status=400)
    assert len((state / 'calls.jsonl').read_text().splitlines()) == count
    checks.append('bounded_utc_retention_pagination_and_no_arbitrary_paths_commands_or_restore_target')
    saved = request(base + '/settings', token, settings, method='PUT'); assert saved['settings'] == settings and saved['immediate'] is False
    assert request(base + '/captures', token, {}, status=202)['job']['operation'] == 'capture'
    assert request(base + '/captures/' + backup + '/drill', token, {}, status=202)['job']['operation'] == 'drill'
    calls = [json.loads(line) for line in (state / 'calls.jsonl').read_text().splitlines()]
    assert 'runToday' not in calls[-3]['input']  # Forwarded only when present.
    assert calls[-3:] == [{'operation': 'settings', 'input': {**settings, 'actorId': actor['id']}}, {'operation': 'capture', 'input': {'actorId': actor['id']}}, {'operation': 'drill', 'input': {'backupId': backup, 'actorId': actor['id']}}]
    with sqlite3.connect(data / 'data.db') as db:
        records = db.execute('SELECT actorId,action,jobId,details FROM web_platform_recovery_audit ORDER BY created,id').fetchall()
        assert len(records) == 3 and {record[1] for record in records} == {'settings', 'capture', 'drill'} and all(record[0] == actor['id'] for record in records)
        assert '/etc' not in json.dumps(records) and '/srv/production' not in json.dumps(records)
    today = request(base + '/settings', token, {**settings, 'runToday': True}, method='PUT'); assert today['immediate'] is True and today['settings'] == settings
    assert json.loads((state / 'calls.jsonl').read_text().splitlines()[-1]) == {'operation': 'settings', 'input': {**settings, 'runToday': True, 'actorId': actor['id']}}
    with sqlite3.connect(data / 'data.db') as db:
        details = db.execute("SELECT details FROM web_platform_recovery_audit WHERE action='settings' ORDER BY created DESC,id DESC LIMIT 1").fetchone()[0]
        assert json.loads(details) == {**settings, 'runToday': True}
    checks.append('run_today_forwarded_only_when_present_and_audited')
    (state / 'active').touch(); request(base + '/captures', token, {}, status=409)
    with sqlite3.connect(data / 'data.db') as db: assert db.execute('SELECT COUNT(*) FROM web_platform_recovery_audit').fetchone()[0] == 4
    checks.append('fixed_runner_actor_injection_audit_and_busy_status_without_duplicate_execution')
    (state / 'fail').touch()
    fallback = request(base, token); assert fallback['configured'] is False and fallback['unavailable'] is True
    request(base + '/captures', token, {}, status=400)
    checks.append('unavailable_service_fails_closed_without_exposing_private_configuration')
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
