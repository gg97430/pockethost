#!/usr/bin/env python3
"""Disposable controller: resource snapshots are bounded, sanitized and operator-only."""
import argparse, datetime, hashlib, json, os, pathlib, secrets, shutil, socket, subprocess, tempfile, time, urllib.error, urllib.parse, urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=pathlib.Path, required=True)
parser.add_argument('--pocketbase', type=pathlib.Path, required=True)
args = parser.parse_args()
os.umask(0o077)
temporary = tempfile.TemporaryDirectory(prefix='kolab-resource-api-')
root = pathlib.Path(temporary.name)
process = None
with socket.socket() as bound:
    bound.bind(('127.0.0.1', 0)); port = bound.getsockname()[1]


def api(path, token='', body=None, expected=200):
    request = urllib.request.Request('http://127.0.0.1:' + str(port) + path, data=None if body is None else json.dumps(body).encode(), headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {})})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            assert response.status == expected
            return json.load(response), response.headers
    except urllib.error.HTTPError as error:
        assert error.code == expected, (path, error.code, error.read().decode())
        return None, error.headers


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks = root / 'hooks'; hooks.mkdir()
    shutil.copy(source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    (hooks / 'resources.pb.js').write_text("routerAdd('GET','/api/admin/monitoring/resources',e=>require(`${__hooks}/mothership`).HandleOperatorResourceMetrics(e),$apis.requireAuth('users'))")
    caches = root / 'resources'; caches.mkdir()
    env = {**os.environ, 'DATA_ROOT': str(root), 'WEB_APPS_ENABLED': 'false', 'PH_DISABLE_INSTANCE_WEBHOOKS': 'true', 'PLATFORM_RESOURCES_ROOT': str(caches), 'WEB_APPS_MASTER_KEY': 'm' * 32}
    password = 'P' + secrets.token_urlsafe(32); data = root / 'data'
    subprocess.run([str(args.pocketbase), 'superuser', 'upsert', 'resources@example.invalid', password, '--dir', str(data), '--hooksDir', str(root / 'empty'), '--migrationsDir', str(source / 'pb_migrations')], env=env, capture_output=True, check=True)
    with (root / 'process.log').open('wb') as log:
        process = subprocess.Popen([str(args.pocketbase), 'serve', '--http', '127.0.0.1:' + str(port), '--dir', str(data), '--hooksDir', str(hooks), '--migrationsDir', str(source / 'pb_migrations')], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        try: api('/api/health'); break
        except OSError: time.sleep(.1)
    su = api('/api/collections/_superusers/auth-with-password', body={'identity': 'resources@example.invalid', 'password': password})[0]['token']
    tokens, users = {}, {}
    for name, admin, suspended in [('admin', True, ''), ('member', False, ''), ('suspended', True, 'paused')]:
        record = api('/api/collections/users/records', su, {'email': name + '@example.invalid', 'username': name, 'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': admin, 'suspension': suspended, 'subscription': 'premium', 'subscription_quantity': 1})[0]
        users[name] = record['id']
        tokens[name] = api('/api/collections/users/auth-with-password', body={'identity': name + '@example.invalid', 'password': password})[0]['token']
    path = '/api/admin/monitoring/resources'
    api(path, expected=401); api(path, tokens['member'], expected=403); api(path, tokens['suspended'], expected=403)
    missing, headers = api(path, tokens['admin'])
    assert missing['available'] is False and missing['stale'] is True and missing['sample'] is None and headers.get('Cache-Control') == 'no-store'
    for invalid in ['../../private', 'all', '1h/../../secret']:
        api(path + '?' + urllib.parse.urlencode({'range': invalid}), tokens['admin'], expected=400)
    app = api('/api/collections/web_apps/records', su, {'name': 'Site fixture', 'uid': users['admin'], 'status': 'stopped', 'config': {}})[0]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    metric = {'cpuPercent': 0, 'memoryBytes': 0, 'diskBytes': None}
    categories = ['databases', 'hosting', 'builds', 'platform', 'backups', 'system']
    value = {'range': '1h', 'available': True, 'stale': False, 'password': 'never-public', 'sample': {
        'version': 1, 'collectedAt': now, 'diskCollectedAt': None, 'diskAttribution': 'directory-estimate',
        'host': {'hostname': 'fixture', 'uptimeSeconds': 1, 'cpuCount': 12, 'cpuPercent': 8.3, 'memoryTotalBytes': 1000, 'memoryUsedBytes': 200, 'memoryAvailableBytes': 800, 'diskTotalBytes': 10000, 'diskUsedBytes': 3000, 'diskAvailableBytes': 7000},
        'categories': [{'id': name, 'resourceCount': 1, **metric} for name in categories],
        'resources': [{'id': 'a' * 15, 'category': 'hosting', 'kind': 'application', 'state': 'running', 'memoryLimitBytes': 768, 'name': 'never-public', 'env': 'never-public', 'href': 'https://outside.invalid', **metric}],
        'filesystems': [{'mount': '/', 'totalBytes': 10000, 'usedBytes': 3000, 'availableBytes': 7000, 'usedPercent': 30}], 'errors': []},
        'history': {'points': [{'timestamp': int(time.time() * 1000), **metric, 'categories': {name: metric for name in categories}}], 'firstCollectedAt': now, 'intervalSeconds': 15, 'retentionDays': 7}}
    file = caches / 'resources-1h.json'; file.write_text(json.dumps(value)); original = file.read_bytes()
    result, headers = api(path, tokens['admin'])
    assert result['available'] and not result['stale'] and headers.get('Cache-Control') == 'no-store'
    assert 'never-public' not in json.dumps(result) and 'outside.invalid' not in json.dumps(result)
    row = next(row for row in result['sample']['resources'] if row['id'] == app['id'])
    assert row['name'] == 'Site fixture' and row['href'] == '/applications/' + app['id'] and row['cpuPercent'] is None
    assert file.read_bytes() == original
    value['sample']['collectedAt'] = '2000-01-01T00:00:00Z'; file.write_text(json.dumps(value))
    assert api(path, tokens['admin'])[0]['stale']
    file.write_text('{bad'); assert api(path, tokens['admin'])[0]['available'] is False
    for selected in ['15m', '24h', '7d']:
        response = api(path + '?range=' + selected, tokens['admin'])[0]
        assert response['range'] == selected and not response['available']
    assert not any(caches.glob('*.sqlite*'))
    print(json.dumps({'passed': True, 'checks': ['auth-required', 'admin-only', 'suspended-denied', 'ranges-allowlisted', 'missing-not-healthy', 'cached-data-read-only', 'no-secret-fields', 'safe-controller-labels', 'real-null-values', 'stale-visible', 'corrupt-not-healthy', 'all-ranges-explicit', 'no-collector-or-database-spawned']}))
except Exception:
    if (root / 'process.log').exists(): print((root / 'process.log').read_text()[-3000:])
    raise
finally:
    if process:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
    temporary.cleanup()
