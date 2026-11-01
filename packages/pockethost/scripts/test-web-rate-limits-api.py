#!/usr/bin/env python3
"""Disposable native PocketBase: per-IP rate limits behind the firewall, without ever limiting local services."""
import argparse
import json
import os
import pathlib
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

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True, type=pathlib.Path)
parser.add_argument('--pocketbase', required=True)
args = parser.parse_args()
os.umask(0o077)
root = pathlib.Path(tempfile.mkdtemp(prefix='ph-web-rate-limits-')).resolve()
process = None
checks = []
with socket.socket() as server:
    server.bind(('127.0.0.1', 0))
    port = server.getsockname()[1]
base = f'http://127.0.0.1:{port}'
secret = secrets.token_urlsafe(40)
HEADER = 'X-Pockethost-Firewall-Client-Ip'
MIGRATION = '1793271500_enable_web_rate_limits.js'
AUTH = '/api/collections/users/auth-with-password'
FORM = {'identity': 'nobody@example.invalid', 'password': 'wrong-password'}
BASE_RULES = {'*:auth': (2, 3), '*:create': (20, 5), '/api/batch': (3, 1), '/api/': (300, 10)}
# Ce que le pare-feu retire ou remplace (proxyHeaders.ts) : aucune valeur du client n'atteint le contrôleur.
CLIENT_CONTROLLED = {HEADER.lower(), 'x-forwarded-for', 'x-real-ip', 'forwarded', 'via', 'cf-connecting-ip',
    'true-client-ip', 'x-pockethost-client-ip'}


def status_of(path, token='', body=None, method=None, headers=None):
    call = urllib.request.Request(base + path, method=method or ('POST' if body is not None else 'GET'),
        headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {}), **(headers or {})},
        data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(call, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def api(path, token='', body=None, method=None, status=200):
    code, raw = status_of(path, token, body, method)
    assert code == status, (path, code, raw[:200])
    return json.loads(raw) if raw else None


def through_firewall(client_ip, path, body=None, forged=None):
    """Simulated firewall: whatever the client sent, the controller only receives the computed IP."""
    kept = {name: value for name, value in (forged or {}).items() if name.lower() not in CLIENT_CONTROLLED}
    return status_of(path, body=body, headers={**kept, HEADER: client_ip, 'X-Forwarded-For': client_ip})[0]


def bucket(codes):
    return {code: codes.count(code) for code in sorted(set(codes))}


def serve(hooks, migrations, data, env):
    global process
    log = (root / 'process.log').open('ab')
    process = subprocess.Popen([args.pocketbase, 'serve', '--dir', str(data), '--hooksDir', str(hooks), '--migrationsDir', str(migrations), '--http', f'127.0.0.1:{port}'], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError((root / 'process.log').read_text()[-2500:])
        try:
            if status_of('/api/health')[0] == 200:
                return
        except OSError:
            time.sleep(.1)
    raise RuntimeError('PocketBase did not start')


def stop():
    global process
    if process:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
        process = None


def stored_settings(data):
    with sqlite3.connect(data / 'data.db') as db:
        return json.loads(db.execute("SELECT value FROM _params WHERE id = 'settings'").fetchone()[0])


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    bundle = (source / 'pb_hooks/mothership.pb.js').read_text()
    # Vraies routes locales, vrais handlers ; ni tâches planifiées ni reste du contrôleur.
    local_routes = re.findall(r'^routerAdd\("(?:GET|POST|PATCH|PUT|DELETE)", "(/api/web-apps/_[^"]+)".*$', bundle, re.M)
    route_lines = [line for line in bundle.splitlines() if re.match(r'routerAdd\("[A-Z]+", "/api/web-apps/_', line)]
    assert len(route_lines) >= 20 and all(line.rstrip().endswith(');') for line in route_lines)
    hooks = root / 'hooks'
    hooks.mkdir()
    shutil.copy(source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    (hooks / 'local.pb.js').write_text('\n'.join(route_lines) + '\n')
    migrations = source / 'pb_migrations'
    env = {**os.environ, 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_WORKER_SECRET': secret, 'WEB_APPS_MASTER_KEY': 'h' * 32,
        'WEB_APPS_ROOT': str(root / 'web'), 'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid'}
    password, email = 'P' + secrets.token_urlsafe(32), 'limits@example.invalid'
    data = root / 'pb_data'
    subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(data), '--hooksDir', str(root / 'empty'), '--migrationsDir', str(migrations)], env=env, check=True, capture_output=True)
    serve(hooks, migrations, data, env)
    su = api('/api/collections/_superusers/auth-with-password', body={'identity': email, 'password': password})['token']

    settings = api('/api/settings', su)
    limits, proxy = settings['rateLimits'], settings['trustedProxy']
    assert limits['enabled'] is False and proxy == {'headers': [HEADER], 'useLeftmostIP': False}
    checks.append('migration_preserves_disabled_global_limits')
    # Cette suite teste le moteur natif après un choix explicite de l'opérateur ; la migration ne l'active pas.
    limits['enabled'] = True
    api('/api/settings', su, {'rateLimits': limits}, 'PATCH')
    rules = {rule['label']: (rule['maxRequests'], rule['duration']) for rule in limits['rules']}
    assert all(rules[label] == value for label, value in BASE_RULES.items()), rules
    assert {'127.0.0.1', '::1'} <= set(limits['excludedIPs'])
    labels = [rule['label'] for rule in limits['rules']]
    local_labels = [label for label in labels if label.startswith('/api/web-apps/_')]
    # L'ordre est le contrat : PocketBase prend le premier préfixe enregistré, pas le plus long.
    assert all(labels.index(label) < labels.index('/api/') for label in local_labels), labels
    assert all(label.endswith('/') for label in local_labels) and all(rules[label][0] >= 1000000 for label in local_labels)
    namespaces = sorted({'/'.join(route.split('/')[:4]) + '/' for route in local_routes})
    assert set(namespaces) <= set(local_labels), ('namespace without a local rule', set(namespaces) - set(local_labels))
    checks.append('migration_trusts_only_the_firewall_header_keeps_base_rules_and_covers_every_local_namespace')

    first = [through_firewall('203.0.113.1', AUTH, FORM) for _ in range(4)]
    assert first == [400, 400, 429, 429], first
    assert through_firewall('203.0.113.2', AUTH, FORM) == 400
    checks.append('auth_with_password_limited_per_forwarded_ip_with_separate_buckets')

    # Un client qui change d'identité à chaque essai reste dans son seau : le pare-feu remplace ces valeurs.
    forged = [through_firewall('203.0.113.3', AUTH, FORM, {HEADER: f'198.51.100.{index}', 'X-Forwarded-For': f'198.51.100.{index}',
        'CF-Connecting-IP': f'198.51.100.{index}', 'X-Pockethost-Client-Ip': f'198.51.100.{index}'}) for index in range(6)]
    assert forged[:2] == [400, 400] and set(forged[2:]) == {429}, forged
    # Sans pare-feu, l'en-tête serait cru : d'où la boucle locale obligatoire du contrôleur.
    direct = [status_of(AUTH, body=FORM, headers={HEADER: f'198.51.100.{index}'})[0] for index in range(6)]
    assert set(direct) == {400}, direct
    checks.append('forged_identity_headers_are_replaced_by_the_firewall_and_direct_access_is_why_the_bind_is_loopback')

    # Connexions FTP et superusers des services : même route, depuis 127.0.0.1, sans en-tête.
    assert set(status_of(AUTH, body=FORM)[0] for _ in range(20)) == {400}
    claims = [status_of('/api/web-apps/_worker/claim', body={}, headers={'X-Web-Worker': secret})[0] for _ in range(400)]
    assert bucket(claims) == {200: 400}, bucket(claims)
    checks.append('local_logins_and_worker_are_never_limited_by_excluded_addresses')

    # Seconde protection, à exclusions vides : les règles dédiées passent seules devant `/api/`.
    api('/api/settings', su, {'rateLimits': {**limits, 'excludedIPs': []}}, 'PATCH')
    for namespace in namespaces:
        route = next(item for item in local_routes if item.startswith(namespace))
        codes = [status_of(route, body={}, headers={'X-Web-Worker': 'wrong-secret'})[0] for _ in range(320)]
        assert bucket(codes) == {401: 320}, (route, bucket(codes))
    worker = [status_of('/api/web-apps/_worker/claim', body={}, headers={'X-Web-Worker': secret})[0] for _ in range(320)]
    assert bucket(worker) == {200: 320}, bucket(worker)
    control = [status_of('/api/health')[0] for _ in range(320)]
    assert control.count(429) >= 20, bucket(control)
    time.sleep(10.1)
    api('/api/settings', su, {'rateLimits': limits}, 'PATCH')
    checks.append('dedicated_rules_alone_protect_every_local_namespace_while_the_generic_rule_is_active')

    # L'en-tête de relais reste présent : une requête relayée n'est jamais locale, même avec le bon secret.
    relayed = through_firewall('203.0.113.4', '/api/web-apps/_worker/claim', {}, {'X-Web-Worker': secret})
    assert relayed == 403, relayed
    checks.append('relayed_requests_stay_refused_on_local_routes')

    stop()
    later = [path.name for path in migrations.glob('*.js') if path.name.split('_')[0].isdigit() and path.name > MIGRATION]
    subprocess.run([args.pocketbase, 'migrate', 'down', str(len(later) + 1), '--dir', str(data), '--hooksDir', str(root / 'empty'), '--migrationsDir', str(migrations)], env=env, input=b'y\n', check=True, capture_output=True)
    reverted = stored_settings(data)
    assert reverted['rateLimits']['enabled'] is True and reverted['trustedProxy']['headers'] == []
    assert reverted['rateLimits']['excludedIPs'] == []
    assert sorted(rule['label'] for rule in reverted['rateLimits']['rules']) == sorted(BASE_RULES), reverted['rateLimits']['rules']
    checks.append('down_keeps_operator_activation_and_restores_empty_trusted_headers')

    # Réglages d'exploitation : une règle modifiée est conservée, une règle absente est recréée, une règle tierce reste.
    custom = json.loads(json.dumps(reverted))
    custom['rateLimits']['rules'] = [{'label': '/api/', 'maxRequests': 5000, 'duration': 10, 'audience': ''},
        {'label': '*:auth', 'maxRequests': 7, 'duration': 9, 'audience': '@guest'},
        {'label': 'users:create', 'maxRequests': 1, 'duration': 60, 'audience': ''}]
    custom['rateLimits']['excludedIPs'] = ['192.0.2.1']
    with sqlite3.connect(data / 'data.db') as db:
        db.execute("UPDATE _params SET value = ? WHERE id = 'settings'", (json.dumps(custom),))
    subprocess.run([args.pocketbase, 'migrate', 'up', '--dir', str(data), '--hooksDir', str(root / 'empty'), '--migrationsDir', str(migrations)], env=env, check=True, capture_output=True)
    upgraded = stored_settings(data)['rateLimits']
    assert upgraded['enabled'] is True
    found = {rule['label']: rule for rule in upgraded['rules']}
    assert found['*:auth']['maxRequests'] == 7 and found['*:auth']['duration'] == 9 and found['*:auth']['audience'] == '@guest'
    assert found['/api/']['maxRequests'] == 5000 and found['users:create']['maxRequests'] == 1
    assert (found['*:create']['maxRequests'], found['*:create']['duration']) == BASE_RULES['*:create']
    assert (found['/api/batch']['maxRequests'], found['/api/batch']['duration']) == BASE_RULES['/api/batch']
    order = [rule['label'] for rule in upgraded['rules']]
    assert all(order.index(label) < order.index('/api/') for label in local_labels), order
    assert set(upgraded['excludedIPs']) == {'192.0.2.1', '127.0.0.1', '::1'}
    checks.append('up_keeps_operator_values_recreates_missing_rules_and_keeps_local_rules_first')

    # Un redémarrage suffit : la configuration enregistrée s'applique sans intervention.
    serve(hooks, migrations, data, env)
    restarted = [through_firewall('203.0.113.5', AUTH, FORM) for _ in range(9)]
    assert restarted[:7] == [400] * 7 and set(restarted[7:]) == {429}, restarted
    checks.append('settings_survive_a_restart')
    print(json.dumps({'passed': True, 'checks': checks}), flush=True)
except Exception:
    if (root / 'process.log').exists():
        print((root / 'process.log').read_text()[-2500:], flush=True)
    raise
finally:
    stop()
    shutil.rmtree(root, ignore_errors=True)
