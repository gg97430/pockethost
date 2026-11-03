#!/usr/bin/env python3
"""Disposable PocketBase: several GitHub App connections per server (spec github-multi §12).

Phase 1 starts PocketBase with the migrations that precede the GitHub-connections migration and stores a principal
'github' row exactly as the singleton code wrote it (no 'meta'), plus deliveries and an implicit application. Phase 2
restarts the same data directory with every migration and the real route table (hooks.ts copied as a hook file).

Every GitHub HTTP call is stubbed: a `python3` shim placed first on PATH intercepts the controller readers run as
`python3 -c <reader> <file>` (GITHUB_READER, GITHUB_CONNECT_READER, GIT_SOURCE_READER) and answers from a local control
file, per GitHub App id; it logs {reader, action, githubAppId, key marker, config keys, repository} for each call. Git
keygen and every other python invocation (backup archive reader, ...) run the real interpreter. No network access.
"""
import argparse, base64, datetime, hashlib, hmac, importlib.util, json, os, pathlib, re, secrets, shutil, socket, sqlite3, struct, subprocess, sys, tempfile, time, types, urllib.error, urllib.request
from unittest import mock

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True, type=pathlib.Path)
parser.add_argument('--pocketbase', required=True)
args = parser.parse_args()
os.umask(0o077)
temporary = tempfile.TemporaryDirectory(prefix='ph-web-github-connections-')
root = pathlib.Path(temporary.name).resolve()
process = None
checks = []
responses = []
MASTER = 'k' * 32
HOST = 'app.example.invalid'
LEGACY_PATH = '/api/web-apps/github/webhook'
LEGACY_URL = f'https://{HOST}{LEGACY_PATH}'
CONNECTION_ID = re.compile(r'^[a-z0-9]{15}$')
UNKNOWN = 'zzzzzzzzzzzzzzz'
PLATFORM_ONLY = 'Action réservée aux administrateurs de la plateforme.'
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
origin = f'http://127.0.0.1:{port}'


def request(path, token='', body=None, method=None, status=200, headers=None, raw=None, keep=True):
    statuses = status if isinstance(status, (list, tuple, set)) else [status]
    data = raw if raw is not None else None if body is None else json.dumps(body).encode()
    call = urllib.request.Request(origin + path, method=method or ('POST' if data is not None else 'GET'), headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {}), **(headers or {})}, data=data)
    try:
        with urllib.request.urlopen(call, timeout=30) as response:
            text = response.read()
            assert response.status in statuses, (method, path, response.status, status, text[:300])
            if keep: responses.append(text.decode())
            return json.loads(text) if text else None
    except urllib.error.HTTPError as error:
        text = error.read()
        if error.code in statuses:
            if keep: responses.append(text.decode())
            return json.loads(text) if text else None
        raise AssertionError(f'{method or ""} {path}: {error.code} != {status}: ' + text.decode()[:600]) from None


def aes():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(MASTER.encode())


def decrypt(value):
    blob = base64.b64decode(value)
    return json.loads(aes().decrypt(blob[:12], blob[12:], None))


def encrypt(value):
    # Same format as PocketBase $security.encrypt: base64(nonce12 || AES-256-GCM).
    nonce = secrets.token_bytes(12)
    return base64.b64encode(nonce + aes().encrypt(nonce, json.dumps(value).encode(), None)).decode()


def sign(secret, raw):
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def pem(marker):
    return f'-----BEGIN RSA PRIVATE KEY-----\n{marker}\n-----END RSA PRIVATE KEY-----'


counter = [0]


def sha():
    counter[0] += 1
    return hashlib.sha1(str(counter[0]).encode()).hexdigest()


# Connection A: the principal row as the singleton code stored it (no 'meta'). Connection B: added through the API.
A = {'githubAppId': '1001', 'githubAppSlug': 'app-a', 'githubInstallationId': '2001', 'privateKey': pem('KEY-A-PRIVATE-MARKER'), 'webhookSecret': 'SECRET-A-' + 'a' * 40, 'ownerId': '501', 'ownerLogin': 'owner-a'}
B_INPUT = {'label': 'Org B', 'githubAppId': '1002', 'githubAppSlug': 'app-b', 'githubInstallationId': '2002', 'privateKey': pem('KEY-B-PRIVATE-MARKER'), 'webhookSecret': 'SECRET-B-' + 'b' * 40}
SECRET_MARKERS = ['KEY-A-PRIVATE-MARKER', 'KEY-B-PRIVATE-MARKER', 'KEY-W-PRIVATE-MARKER', 'KEY-R-PRIVATE-MARKER', 'KEY-D-PRIVATE-MARKER', 'KEY-M-PRIVATE-MARKER', A['webhookSecret'], B_INPUT['webhookSecret'], 'SECRET-W-', 'SECRET-R-', 'SECRET-D-', 'SECRET-M-', 'SECRET-L-', 'PRIVATE KEY', 'valueEncrypted', 'pendingEncrypted']
REPOS = {'acme/web': 11, 'acme/site': 13, 'acme/allowed': 14, 'acme/ci': 15, 'acme/a-only': 16}
GITHUB_ED25519 = {'type': 'ssh-ed25519', 'key': 'AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl', 'fingerprint': 'SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU'}
control_file = root / 'github-control.json'
reader_log = root / 'github-reader.log'
control_state = {
    # Repositories each installation can read (keyed by GitHub App id): GitHub refuses the others.
    'repos': {'1001': [{'id': REPOS[n], 'full_name': n} for n in ['acme/web', 'acme/site', 'acme/allowed', 'acme/a-only']],
              '1002': [{'id': REPOS[n], 'full_name': n} for n in ['acme/web', 'acme/allowed', 'acme/ci']]},
    'commits': {}, 'prs': {}, 'hooks': {}, 'conversion': {}, 'discover': '',
    'gitHostKeys': [GITHUB_ED25519], 'gitCommit': 'e' * 40,
}


def control(**values):
    control_state.update(values)
    control_file.write_text(json.dumps(control_state))


def commit_for(app_id, value=None):
    value = value or sha()
    control_state['commits'][app_id] = value; control(commits=control_state['commits'])
    return value


SHIM = r'''#!__PYTHON__
import sys,json,pathlib,os
REAL=__REAL__;CONTROL=__CONTROL__;LOG=__LOG__
code=sys.argv[2] if len(sys.argv)>3 and sys.argv[1]=='-c' else ''
kind='connect' if 'PocketHost-Web-Connect' in code else 'git' if "ALIAS='pockethost-git'" in code else 'github' if 'api.github.com' in code else ''
if not kind:os.execv(REAL,[REAL]+sys.argv[1:])
p=pathlib.Path(sys.argv[-1])
try:d=json.loads(p.read_text())
except Exception:d={}
a=d.get('action')
if kind=='git' and a=='keygen':os.execv(REAL,[REAL]+sys.argv[1:])
def out(v):
 try:p.unlink()
 except Exception:pass
 print(json.dumps(v));sys.exit(0)
c=json.loads(pathlib.Path(CONTROL).read_text())
config=d.get('config') if isinstance(d.get('config'),dict) else {}
key=str(config.get('privateKey') or '')
marker=key.split('\n')[1] if key.count('\n')>=2 else key[:40]
app=str(config.get('githubAppId') or '')
with open(LOG,'a') as f:f.write(json.dumps({'reader':kind,'action':a,'githubAppId':app,'installationId':str(config.get('githubInstallationId') or ''),'key':marker,'configKeys':sorted(config),'repository':str(d.get('repository') or ''),'branch':str(d.get('branch') or ''),'repositoryId':str(d.get('repositoryId') or ''),'number':d.get('number'),'requestedInstallation':str(d.get('installationId') or '')})+'\n')
if kind=='git':
 if a=='keyscan':out({'hostKeys':c['gitHostKeys']})
 if d.get('mode')=='resolve':out({'commit':c['gitCommit']})
 if d.get('mode')=='heads':out({'branches':[{'name':'main','commit':c['gitCommit']}],'defaultBranch':'main','truncated':False,'skipped':0})
 out({'ok':True,'defaultBranch':'main','head':c['gitCommit'],'branchFound':True,'commit':c['gitCommit']})
if kind=='connect':
 if a=='convert':
  if d.get('code')=='b'*40:out({'error':'Le code GitHub est expiré.'})
  out({'result':c['conversion']})
 ident=str(d.get('installationId') or c['discover'])
 out({'result':{'installationId':ident,'accountId':str(config.get('ownerId') or ''),'account':str(config.get('ownerLogin') or ''),'repositoryCount':12}})
configured=bool(key and app and config.get('githubInstallationId'))
denied='GitHub refuse l’accès au dépôt ou à la branche. Vérifiez l’installation et ses autorisations.'
broken='GitHub est indisponible ou sa configuration est invalide.'
rows=c['repos'].get(app,[]) if configured else []
if a=='health':
 if not configured:out({'configured':False,'ok':False,'error':'Connectez GitHub pour accéder aux dépôts privés et aux déploiements automatiques.'})
 inst=str(config['githubInstallationId'])
 out({'configured':True,'ok':True,'app':{'id':app,'slug':str(config.get('githubAppSlug') or ''),'owner':str(config.get('ownerLogin') or '')},'installation':{'id':inst,'account':str(config.get('ownerLogin') or ''),'repositorySelection':'selected'},'permissions':{'contents':'read','metadata':'read','pull_requests':'read'},'repositoryCount':len(rows),'manageUrl':'https://github.com/settings/installations/'+inst,'previewSupported':True})
if a=='repositories':
 out({'items':[{'id':r['id'],'fullName':r['full_name'],'defaultBranch':'main','private':True} for r in rows],'configured':configured,'page':1,'perPage':50,'totalItems':len(rows),'hasMore':False,'truncated':False,'searchScope':'authorized-repositories'})
if not configured:out({'error':broken})
if a=='hook-config':out({'url':c['hooks'].get(app,'')})
if a=='token':out({'token':'installation-token-'+app})
if a=='branches':
 repo=next((r for r in rows if str(r['id'])==str(d.get('repositoryId'))),None)
 if not repo:out({'error':denied})
 out({'items':[{'name':'main'},{'name':'develop'}],'page':1,'perPage':50,'hasMore':False,'repository':repo['full_name']})
visible=any(r['full_name'].lower()==str(d.get('repository') or '').lower() for r in rows)
if a=='pull-request':
 pr=c['prs'].get(app,{}).get(str(d.get('number')))
 if not visible or not pr:out({'error':denied})
 out(pr)
if a=='commit':
 if not visible:out({'error':denied})
 out({'sha':c['commits'].get(app,'d'*40)})
out({'error':broken})
'''


def reader_calls(**match):
    calls = [json.loads(line) for line in reader_log.read_text().splitlines()] if reader_log.exists() else []
    return [c for c in calls if all(c.get(k) == v for k, v in match.items())]


def last_call(**match):
    calls = reader_calls(**match)
    assert calls, match
    return calls[-1]


def no_secrets(value):
    text = value if isinstance(value, str) else json.dumps(value)
    for marker in SECRET_MARKERS:
        assert marker not in text, (marker, text[:300])


def connection_id_of(result):
    for value in (result.get('connection'), result.get('item'), result):
        if isinstance(value, dict) and isinstance(value.get('id'), str): return value['id']
    raise AssertionError(result)


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    scripts = args.source / 'packages/pockethost/scripts'
    migrations = source / 'pb_migrations'
    target_migration = sorted(migrations.glob('*_updated_web_github_connections.js'))
    assert len(target_migration) == 1, target_migration
    cutoff = int(target_migration[0].name.split('_', 1)[0])
    before_dir = root / 'migrations-before'; before_dir.mkdir()
    for item in migrations.iterdir():
        prefix = item.name.split('_', 1)[0]
        if item.is_file() and (not prefix.isdigit() or int(prefix) < cutoff): shutil.copy(item, before_dir / item.name)
    hooks = root / 'hooks'; hooks.mkdir(); (root / 'empty-hooks').mkdir()
    shutil.copy(source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    shutil.copy(source / 'src/lib/handlers/webApps/hooks.ts', hooks / 'web.pb.js')
    # Test-only fixture: publish a ready deployment (backup restore scope). Superusers of the platform only.
    (hooks / 'test.pb.js').write_text("""routerAdd('POST','/test/fixture',e=>{if(!e.auth?.getBool('superAdmin'))throw new ForbiddenError();const m=require(`${__hooks}/mothership`),i=e.requestInfo().body,r=$app.findRecordById('web_apps',i.id);if(i.action==='publish'){const resolved=m.resolveWebEnvironment(r),d=new Record($app.findCollectionByNameOrId('web_deployments'));d.set('appId',r.id);d.set('action','deploy');d.set('status','ready');d.set('commit','c'.repeat(40));d.set('snapshotEncrypted',m.encryptWebValue({config:JSON.parse(r.getString('config')),env:resolved.env,projectEnvironment:resolved.context}));d.set('result',{image:'ph-web/'+r.id+':'+'d'.repeat(15),available:true});$app.save(d);r.set('activeDeployment',d.id);r.set('desiredCommit','c'.repeat(40));r.set('status','running');$app.save(r)}return e.json(200,{config:JSON.parse(r.getString('config'))})},$apis.requireAuth('users'));\n""")
    with (hooks / 'test.pb.js').open('a') as fixture:
        fixture.write('onBootstrap(e=>{e.next();require(`${__hooks}/mothership`).InitializeDashboardTotp()});\n')
        fixture.write('routerUse(e=>require(`${__hooks}/mothership`).GuardDashboardTotp(e));\n')
        fixture.write('onRecordAuthRequest(e=>require(`${__hooks}/mothership`).HandleDashboardTotpAuth(e),"users");\n')
        for suffix, handler in [('setup', 'HandleDashboardTotpSetup'), ('confirm', 'HandleDashboardTotpConfirm')]:
            fixture.write(f'routerAdd("POST","/api/account/totp/{suffix}",e=>require(`${{__hooks}}/mothership`).{handler}(e),$apis.requireAuth("users"));\n')
    tools = root / 'tools'; tools.mkdir()
    shim = tools / 'python3'
    shim.write_text(SHIM.replace('__PYTHON__', sys.executable).replace('__REAL__', repr(sys.executable)).replace('__CONTROL__', repr(str(control_file))).replace('__LOG__', repr(str(reader_log))))
    shim.chmod(0o700)
    control()
    env = {**os.environ, 'PATH': str(tools) + os.pathsep + os.environ.get('PATH', ''), 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_MAX_SITES': '5000', 'WEB_APPS_MASTER_KEY': MASTER, 'WEB_APPS_WORKER_SECRET': 'W' + secrets.token_urlsafe(40), 'WEB_APPS_ROOT': str(root / 'web'), 'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid', 'APP_HOST': HOST}
    worker = {'X-Web-Worker': env['WEB_APPS_WORKER_SECRET']}
    password, email = 'P' + secrets.token_urlsafe(32), 'su@example.invalid'
    data = root / 'pb_data'
    database = data / 'data.db'

    def serve(migrations_dir, hooks_dir):
        global process
        log = (root / 'process.log').open('ab')
        process = subprocess.Popen([args.pocketbase, 'serve', '--dir', str(data), '--hooksDir', str(hooks_dir), '--migrationsDir', str(migrations_dir), '--http', f'127.0.0.1:{port}'], env=env, stdout=log, stderr=subprocess.STDOUT)
        for _ in range(150):
            if process.poll() is not None: raise RuntimeError('PocketBase failed to start: ' + (root / 'process.log').read_text()[-3000:])
            try: request('/api/health', keep=False); break
            except OSError: time.sleep(.1)
        else: raise RuntimeError('PocketBase did not answer')
        return request('/api/collections/_superusers/auth-with-password', body={'identity': email, 'password': password}, keep=False)['token']

    def stop():
        global process
        if process:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
            process = None

    def db_rows(sql, params=()):
        with sqlite3.connect(str(database), timeout=10) as db:
            return db.execute(sql, params).fetchall()

    def settings_rows():
        return dict(db_rows('SELECT key, valueEncrypted FROM web_app_settings'))

    def app_config(app_id):
        return json.loads(db_rows('SELECT config FROM web_apps WHERE id=?', (app_id,))[0][0] or '{}')

    # ---------------------------------------------------------------- Phase 1: singleton schema, existing principal.
    subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(data), '--hooksDir', str(root / 'empty-hooks'), '--migrationsDir', str(before_dir)], env=env, check=True, capture_output=True)
    su = serve(before_dir, root / 'empty-hooks')
    platform_row = request('/api/collections/users/records', su, {'email': 'platform@example.invalid', 'username': 'platform', 'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': True, 'subscription': 'premium', 'subscription_quantity': 1}, keep=False)
    request('/api/collections/web_app_settings/records', su, {'key': 'github', 'valueEncrypted': encrypt(A)}, keep=False)
    x_config = {'repository': 'acme/web', 'repositoryId': REPOS['acme/web'], 'branch': 'main', 'rootDirectory': '.', 'healthPath': '/', 'generatedDomain': 'web-x.example.invalid', 'autoDeploy': True}
    X = request('/api/collections/web_apps/records', su, {'name': 'Site X', 'uid': platform_row['id'], 'status': 'stopped', 'config': x_config}, keep=False)['id']
    for delivery in ['legacy-gh-1', 'git-' + 'a' * 15 + '-legacy']:
        request('/api/collections/web_webhook_deliveries/records', su, {'deliveryId': delivery, 'event': 'push', 'repository': 'acme/web' if delivery.startswith('legacy') else 'git:github.com/acme/x', 'branch': 'main', 'status': 'ignored'}, keep=False)
    stop()
    A_CIPHER = settings_rows()['github']
    X_RAW = db_rows('SELECT config FROM web_apps WHERE id=?', (X,))[0][0]

    # ---------------------------------------------------------------- Phase 2: every migration, real routes.
    su = serve(migrations, hooks)
    assert settings_rows() == {'github': A_CIPHER}, 'principal row must be untouched byte for byte'
    assert dict(db_rows('SELECT deliveryId, connectionId FROM web_webhook_deliveries')) == {'legacy-gh-1': 'default', 'git-' + 'a' * 15 + '-legacy': ''}
    assert db_rows('SELECT config FROM web_apps WHERE id=?', (X,))[0][0] == X_RAW
    checks.append('migration_keeps_principal_ciphertext_and_app_config_and_backfills_delivery_connection')

    def user(name, admin=False):
        row = request('/api/collections/users/records', su, {'email': name + '@example.invalid', 'username': name, 'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': admin, 'subscription': 'premium', 'subscription_quantity': 1}, keep=False)
        return row, login(row['email'])

    recovery_by_mail = {}
    def login(mail):
        body = {'identity': mail, 'password': password}
        if mail in recovery_by_mail:
            body['totpCode'] = recovery_by_mail[mail].pop()
            return request('/api/collections/users/auth-with-password', body=body, keep=False)['token']
        token = request('/api/collections/users/auth-with-password', body=body, keep=False)['token']
        setup = request('/api/account/totp/setup', token, {'password': password}, keep=False)
        digest = hmac.new(base64.b32decode(setup['secret']), struct.pack('>Q', int(time.time() // 30)), hashlib.sha1).digest()
        offset = digest[-1] & 15
        code = str((struct.unpack('>I', digest[offset:offset+4])[0] & 0x7fffffff) % 1000000).zfill(6)
        enabled = request('/api/account/totp/confirm', token, {'password': password, 'code': code}, keep=False)
        recovery_by_mail[mail] = enabled['recoveryCodes']
        return enabled['token']
    platform = login('platform@example.invalid')
    tok = {name: user(name)[1] for name in ['owner', 'admin', 'dev', 'viewer', 'outsider']}
    CONN = '/api/web-apps/github/connections'

    def connections(token=platform):
        listing = request(CONN, token)
        assert listing['max'] == 10, listing
        return {item['id']: item for item in listing['items']}

    def claim():
        return request('/api/web-apps/_worker/claim', body={}, headers=worker, keep=False)['job']

    def fail(job):
        request('/api/web-apps/_worker/report', body={'id': job['id'], 'status': 'failed', 'error': 'Fixture'}, headers=worker, keep=False)

    def drain():
        jobs = []
        for _ in range(60):
            job = claim()
            if job: jobs.append(job); fail(job); continue
            if not db_rows("SELECT count(*) FROM web_deployments WHERE status='queued'")[0][0]: return jobs
        raise AssertionError('worker queue did not drain')

    sent = {}

    def delivery_key(connection, raw): return f'gh-{connection}-{hashlib.sha256(raw).hexdigest()}'

    def hook(path, secret, event, payload, delivery=None, status=202):
        raw = json.dumps(payload).encode()
        delivery = delivery or 'd-' + secrets.token_hex(8)
        sent[delivery] = raw
        headers = {'X-GitHub-Event': event, 'X-GitHub-Delivery': delivery, 'X-Hub-Signature-256': 'sha256=' + sign(secret, raw)}
        return request(path, raw=raw, headers=headers, status=status)

    def push(path, secret, installation, repository='acme/web', branch='main', delivery=None, status=202):
        return hook(path, secret, 'push', {'ref': 'refs/heads/' + branch, 'after': sha(), 'repository': {'full_name': repository}, 'installation': {'id': installation}}, delivery, status)

    # The principal is listed as such right after the migration, without any write.
    listed = connections()
    assert list(listed) == ['default'], listed
    principal = listed['default']
    assert principal['isPrincipal'] is True and principal['label'] == 'Connexion principale' and principal['disabled'] is False and principal['configured'] is True
    assert principal['webhookUrl'] == LEGACY_URL and principal['app'] == {'id': '1001', 'slug': 'app-a', 'owner': 'owner-a'} and principal['installationId'] == '2001'
    assert principal['hasPrivateKey'] is True and principal['hasWebhookSecret'] is True and principal['appCount'] == 1
    assert settings_rows() == {'github': A_CIPHER}
    commit_for('1001')
    assert push(LEGACY_PATH, A['webhookSecret'], 2001)['queued'] == 1
    assert [job['appId'] for job in drain()] == [X]
    checks.append('principal_listed_after_migration_and_legacy_webhook_still_queues')

    # ---------------------------------------------------------------- 1. Creation, listing, no secret ever returned.
    request(CONN, status=401)
    created = request(CONN, platform, B_INPUT, status=(200, 201))
    B = connection_id_of(created)
    assert CONNECTION_ID.match(B), B
    listed = connections()
    assert set(listed) == {'default', B}
    assert listed['default']['webhookUrl'] == LEGACY_URL and listed[B]['webhookUrl'] == f'{LEGACY_URL}/{B}'
    assert listed[B]['label'] == 'Org B' and listed[B]['isPrincipal'] is False and listed[B]['disabled'] is False and listed[B]['configured'] is True
    assert listed[B]['app']['id'] == '1002' and listed[B]['app']['slug'] == 'app-b' and listed[B]['installationId'] == '2002' and listed[B]['appCount'] == 0
    rows = settings_rows()
    assert set(rows) == {'github', 'github:' + B} and rows['github'] == A_CIPHER
    b_value = decrypt(rows['github:' + B])
    assert b_value['privateKey'] == B_INPUT['privateKey'] and b_value['webhookSecret'] == B_INPUT['webhookSecret']
    assert b_value['meta']['label'] == 'Org B' and b_value['meta']['disabled'] is False and b_value['meta']['createdBy'] == platform_row['id'] and b_value['meta']['createdAt']
    for token in [platform] + list(tok.values()):
        request('/api/collections/web_app_settings/records', token, status=(403, 404))
    shim_read = [request('/api/web-apps/settings', platform), request('/api/web-apps/github/health', platform), request(f'/api/web-apps/github/health?connectionId={B}', platform), request('/api/web-apps/github/connect/status', platform), request('/api/web-apps', platform)]
    assert shim_read[0]['settings']['webhookUrl'] == LEGACY_URL and shim_read[0]['settings']['githubAppId'] == '1001'
    assert shim_read[1]['connectionId'] == 'default' and shim_read[1]['webhook']['url'] == LEGACY_URL
    assert shim_read[2]['connectionId'] == B and shim_read[2]['label'] == 'Org B' and shim_read[2]['disabled'] is False and shim_read[2]['webhook']['url'] == f'{LEGACY_URL}/{B}'
    assert shim_read[3]['connections'] == 2 and shim_read[3]['configured'] is True
    assert shim_read[4]['capabilities']['githubConfigured'] is True and shim_read[4]['capabilities']['githubConnectionCount'] == 2
    # The reader file only ever holds the six credential fields: never the webhook secret nor 'meta'.
    allowed_keys = {'githubAppId', 'githubAppSlug', 'githubInstallationId', 'privateKey', 'ownerId', 'ownerLogin'}
    assert last_call(action='health', githubAppId='1002')['key'] == 'KEY-B-PRIVATE-MARKER'
    for call in reader_calls(reader='github'):
        assert set(call['configKeys']) <= allowed_keys, call
    no_secrets(shim_read)
    checks.append('second_connection_created_listed_with_its_own_webhook_url_secrets_encrypted_and_never_returned')

    # ---------------------------------------------------------------- Apps: implicit principal, explicit B, 'default'.
    Y = request('/api/web-apps', platform, {'name': 'Site Y', 'repository': 'acme/web', 'repositoryId': REPOS['acme/web'], 'branch': 'main', 'githubConnectionId': B}, status=201)['app']
    assert Y['githubConnectionId'] == B
    Y = Y['id']
    Z = request('/api/web-apps', platform, {'name': 'Site Z', 'repository': 'acme/site', 'repositoryId': REPOS['acme/site'], 'branch': 'main', 'autoDeploy': False, 'githubConnectionId': 'default'}, status=201)['app']
    assert 'githubConnectionId' not in Z and 'githubConnectionId' not in app_config(Z['id'])
    Z = Z['id']
    for bad in [UNKNOWN, 'BAD', 'abc', 'x' * 16]:
        request('/api/web-apps', platform, {'name': 'Bad', 'repository': 'acme/web', 'branch': 'main', 'githubConnectionId': bad}, status=400)
    listed = connections()
    assert listed['default']['appCount'] == 2 and listed[B]['appCount'] == 1, listed
    checks.append('apps_bind_to_a_connection_default_is_normalized_away_and_unknown_ids_are_refused')

    # ---------------------------------------------------------------- 2-3. Webhook routing per connection.
    b_path = f'{LEGACY_PATH}/{B}'
    a_sha, b_sha = commit_for('1001'), commit_for('1002')
    a_header, b_header = 'route-a-' + secrets.token_hex(6), 'route-b-' + secrets.token_hex(6)
    assert push(LEGACY_PATH, A['webhookSecret'], 2001, delivery=a_header)['queued'] == 1
    assert last_call(action='commit')['key'] == 'KEY-A-PRIVATE-MARKER'
    assert [job['appId'] for job in drain()] == [X]
    assert push(b_path, B_INPUT['webhookSecret'], 2002, delivery=b_header)['queued'] == 1
    a_delivery, b_delivery = delivery_key('default', sent[a_header]), delivery_key(B, sent[b_header])
    call = last_call(action='commit')
    assert call['key'] == 'KEY-B-PRIVATE-MARKER' and call['githubAppId'] == '1002' and call['repository'] == 'acme/web'
    jobs = drain()
    assert [job['appId'] for job in jobs] == [Y] and jobs[0]['commit'] == b_sha
    assert request(f'/api/web-apps/{X}', platform)['app']['desiredCommit'] == a_sha
    bad_signature = push(b_path, A['webhookSecret'], 2002, status=401)
    assert push(LEGACY_PATH, B_INPUT['webhookSecret'], 2001, status=401) == bad_signature
    assert push(f'{LEGACY_PATH}/{UNKNOWN}', A['webhookSecret'], 2001, status=401) == bad_signature
    assert push(f'{LEGACY_PATH}/{UNKNOWN}', B_INPUT['webhookSecret'], 2002, status=401) == bad_signature
    assert push(f'{LEGACY_PATH}/default', A['webhookSecret'], 2001, status=401) == bad_signature  # No second URL for the principal.
    assert push(f'{LEGACY_PATH}/BAD', A['webhookSecret'], 2001, status=401) == bad_signature
    raw = json.dumps({'ref': 'refs/heads/main', 'after': sha()}).encode()
    assert request(b_path, raw=raw, headers={'X-GitHub-Event': 'push', 'X-GitHub-Delivery': 'unsigned-1'}, status=401) == bad_signature
    assert bad_signature['message'] == 'Signature GitHub invalide.'
    push(b_path, B_INPUT['webhookSecret'], 2001, status=403)
    # A signed body replayed under another (unsigned) delivery id stays a duplicate; the same body on A is another delivery.
    raw = sent[b_header]
    replay = {'X-GitHub-Event': 'push', 'X-GitHub-Delivery': 'replayed-' + secrets.token_hex(6), 'X-Hub-Signature-256': 'sha256=' + sign(B_INPUT['webhookSecret'], raw)}
    assert request(b_path, raw=raw, headers=replay) == {'accepted': True, 'duplicate': True}
    assert not drain()
    stored = dict(db_rows('SELECT deliveryId, connectionId FROM web_webhook_deliveries'))
    assert stored[a_delivery] == 'default' and stored[b_delivery] == B
    assert db_rows('SELECT duplicateCount FROM web_webhook_deliveries WHERE deliveryId=?', (b_delivery,))[0][0] == 1
    x_seen = {d['deliveryId'] for d in request(f'/api/web-apps/{X}/webhooks', platform)['items']}
    y_seen = {d['deliveryId'] for d in request(f'/api/web-apps/{Y}/webhooks', platform)['items']}
    assert a_delivery in x_seen and 'legacy-gh-1' in x_seen and b_delivery not in x_seen
    assert b_delivery in y_seen and not (y_seen & {a_delivery, 'legacy-gh-1'}), (x_seen, y_seen)
    checks.append('webhook_routed_by_path_uniform_401_installation_checked_per_row_same_repository_split_by_connection')

    # Repository hooks omit installation: only an explicitly bound connection accepts them.
    def repository_push(body=None, hook='55', signature=None, status=202):
        value = body or {'ref': 'refs/heads/main', 'after': sha(), 'repository': {'id': REPOS['acme/web'], 'full_name': 'acme/web'}}
        raw_repo = json.dumps(value).encode()
        headers = {'X-GitHub-Event': 'push', 'X-GitHub-Delivery': 'repository-' + secrets.token_hex(6), 'X-Hub-Signature-256': 'sha256=' + sign(signature or B_INPUT['webhookSecret'], raw_repo)}
        if hook is not None: headers['X-GitHub-Hook-ID'] = hook
        return request(b_path, raw=raw_repo, headers=headers, status=status)
    repository_push(status=403)
    binding = {'repository': 'acme/web', 'repositoryId': REPOS['acme/web'], 'hookId': '55'}
    request(f'/api/web-apps/github/connections/{B}', tok['viewer'], {'repositoryWebhook': binding}, 'PATCH', status=403)
    request('/api/web-apps/github/connections/default', platform, {'repositoryWebhook': binding}, 'PATCH', status=400)
    for invalid in [{**binding, 'hookId': ''}, {**binding, 'repository': '../wrong'}, {**binding, 'extra': True}]:
        request(f'/api/web-apps/github/connections/{B}', platform, {'repositoryWebhook': invalid}, 'PATCH', status=400)
    bound = request(f'/api/web-apps/github/connections/{B}', platform, {'repositoryWebhook': binding}, 'PATCH')['connection']['repositoryWebhook']
    assert bound == {**binding, 'repositoryId': str(REPOS['acme/web'])}
    repository_head = commit_for('1002')
    payload = {'ref': 'refs/heads/main', 'after': sha(), 'repository': {'id': REPOS['acme/web'], 'full_name': 'acme/web'}}
    assert repository_push(payload)['queued'] == 1
    assert [(j['appId'], j['commit']) for j in drain()] == [(Y, repository_head)]
    assert repository_push(payload, status=200)['duplicate'] is True
    repository_push(hook='56', status=403)
    repository_push(hook=None, status=403)
    repository_push({**payload, 'after': sha(), 'repository': {'id': REPOS['acme/site'], 'full_name': 'acme/web'}}, status=403)
    repository_push({**payload, 'after': sha(), 'repository': {'id': REPOS['acme/web'], 'full_name': 'acme/site'}}, status=403)
    repository_push({**payload, 'after': sha(), 'installation': {'id': 2001}}, status=403)
    repository_push(signature=A['webhookSecret'], status=401)
    assert not drain()
    request(f'/api/web-apps/github/connections/{B}', platform, {'label': 'Org B renamed'}, 'PATCH')
    assert connections()[B]['repositoryWebhook'] == bound
    request(f'/api/web-apps/github/connections/{B}', platform, {'repositoryWebhook': None, 'label': 'Org B'}, 'PATCH')
    assert connections()[B]['repositoryWebhook'] is None
    repository_push(status=403)
    commit_for('1002', b_sha)
    push(b_path, B_INPUT['webhookSecret'], 2002)
    drain()
    checks.append('repository_hook_opt_in_hmac_repository_hook_id_installation_isolation_and_replay')

    # ---------------------------------------------------------------- 4. Credential isolation in the claim.
    y_sha = commit_for('1002')
    request(f'/api/web-apps/{Y}/deploy', platform, {}, status=202)
    assert last_call(action='commit')['key'] == 'KEY-B-PRIVATE-MARKER'
    job = claim()
    assert job['appId'] == Y and job['commit'] == y_sha and set(job['github']) == {'githubAppId', 'githubInstallationId', 'privateKey'}
    assert job['github'] == {'githubAppId': '1002', 'githubInstallationId': '2002', 'privateKey': B_INPUT['privateKey']}
    assert 'KEY-A-PRIVATE-MARKER' not in json.dumps(job) and 'SECRET-B-' not in json.dumps(job) and 'meta' not in job['github']
    Y_FAILED = job['id']; fail(job)
    commit_for('1001')
    request(f'/api/web-apps/{X}/deploy', platform, {}, status=202)
    job = claim()
    assert job['appId'] == X and job['github'] == {'githubAppId': '1001', 'githubInstallationId': '2001', 'privateKey': A['privateKey']} and 'KEY-B-PRIVATE-MARKER' not in json.dumps(job)
    fail(job)
    git_source = request('/api/web-git-sources', platform, {'url': 'git@github.com:acme/gitapp.git'}, status=201)['source']
    G = request('/api/web-apps', platform, {'name': 'Git app', 'source': 'git', 'gitSourceId': git_source['id'], 'branch': 'main', 'githubConnectionId': B}, status=201)['app']
    assert G['source'] == 'git' and 'githubConnectionId' not in G
    G = G['id']
    request(f'/api/web-apps/{G}/deploy', platform, {}, status=202)
    job = claim()
    assert job['appId'] == G and 'git' in job and 'github' not in job and '-PRIVATE-MARKER' not in json.dumps(job)  # The Git deploy key PEM is expected; no GitHub App key.
    fail(job)
    checks.append('claim_carries_the_apps_connection_credentials_only_three_keys_and_git_apps_get_none')

    # ---------------------------------------------------------------- 5a. CI checks the commit with the app's connection.
    CI = request('/api/web-apps', platform, {'name': 'CI app', 'repository': 'acme/ci', 'repositoryId': REPOS['acme/ci'], 'branch': 'main', 'autoDeploy': False, 'githubConnectionId': B}, status=201)['app']['id']
    image_repo = 'ghcr.io/acme/ci'
    request(f'/api/web-apps/{CI}/runtime/profile', platform, {'profile': {'kind': 'image', 'image': image_repo + '@sha256:' + 'a' * 64}, 'revision': request(f'/api/web-apps/{CI}/runtime/profile', platform)['revision']}, method='PATCH')
    ci_config = app_config(CI)
    if ci_config.get('githubConnectionId') != B or ci_config.get('repository') != 'acme/ci':
        # An image engine may drop the source on update; the CI head check only needs repository, branch and connection.
        raw_row = request(f'/api/collections/web_apps/records/{CI}', su, keep=False)
        request(f'/api/collections/web_apps/records/{CI}', su, {'config': {**raw_row['config'], 'repository': 'acme/ci', 'branch': 'main', 'githubConnectionId': B}}, method='PATCH', keep=False)
    issued = request(f'/api/web-apps/{CI}/deploy-tokens', platform, {'name': 'Workflow', 'imageRepository': image_repo, 'expiresInDays': 7}, status=201)
    bearer = 'Bearer ' + issued['token']
    ci_sha = commit_for('1002')
    request('/api/web-apps/ci/deploy', bearer, {'image': image_repo + '@sha256:' + 'b' * 64, 'commit': sha(), 'branch': 'main', 'requestId': 'run:stale'}, status=400)
    request('/api/web-apps/ci/deploy', bearer, {'image': image_repo + '@sha256:' + 'b' * 64, 'commit': ci_sha, 'branch': 'main', 'requestId': 'run:1'}, status=202)
    call = last_call(action='commit')
    assert call['key'] == 'KEY-B-PRIVATE-MARKER' and call['repository'] == 'acme/ci'
    drain()
    checks.append('ci_deploy_checks_the_branch_head_with_the_apps_connection')

    # ---------------------------------------------------------------- 5b. Previews follow the template app's connection.
    project = request(f'/api/web-apps/{Y}/project', platform, {'name': 'Équipe B', 'environment': 'prod'}, status=201)['project']
    P = project['id']
    for name, role in [('owner', 'owner'), ('admin', 'admin'), ('dev', 'developer'), ('viewer', 'viewer')]:
        request(f'/api/web-projects/{P}/members', platform, {'email': name + '@example.invalid', 'role': role}, status=201)
    request(f'/api/web-projects/{P}', platform, {'name': 'Équipe B', 'previewEnabled': True, 'previewSourceAppId': Y, 'previewMax': 1, 'previewTtlHours': 1}, method='PATCH')
    pr_sha = sha()
    pr = {'number': 5, 'state': 'open', 'draft': False, 'author_association': 'MEMBER', 'head': {'sha': pr_sha, 'ref': 'feature-5', 'repo': {'id': REPOS['acme/web'], 'full_name': 'acme/web'}}, 'base': {'repo': {'id': REPOS['acme/web'], 'full_name': 'acme/web'}}}
    control(prs={'1001': {'5': pr}, '1002': {'5': pr}})
    pull = lambda installation: {'action': 'synchronize', 'number': 5, 'repository': {'full_name': 'acme/web'}, 'installation': {'id': installation}, 'pull_request': {'head': {'sha': '0' * 40}}}
    before = len(reader_calls(action='pull-request'))
    via_a = hook(LEGACY_PATH, A['webhookSecret'], 'pull_request', pull(2001), status=(200, 202))
    assert via_a.get('queued', 0) == 0 and len(reader_calls(action='pull-request')) == before
    pr_delivery = 'pr-b-' + secrets.token_hex(6)
    via_b = hook(b_path, B_INPUT['webhookSecret'], 'pull_request', pull(2002), delivery=pr_delivery, status=(200, 202))
    assert via_b['queued'] == 1 and last_call(action='pull-request')['key'] == 'KEY-B-PRIVATE-MARKER'
    hook(b_path, B_INPUT['webhookSecret'], 'pull_request', pull(2001), status=403)
    assert db_rows('SELECT connectionId FROM web_preview_deliveries WHERE deliveryId=?', (delivery_key(B, sent[pr_delivery]),))[0][0] == B
    # Pull request: same rule, the reader is not called again for a replayed body.
    before = len(reader_calls(action='pull-request'))
    assert hook(b_path, B_INPUT['webhookSecret'], 'pull_request', json.loads(sent[pr_delivery]), status=(200, 202)).get('duplicate') is True
    assert len(reader_calls(action='pull-request')) == before
    PREVIEW = db_rows("SELECT appId FROM web_app_environments WHERE projectId=? AND environment='preview'", (P,))[0][0]
    assert app_config(PREVIEW).get('githubConnectionId') == B
    preview_jobs = [j for j in drain() if j['appId'] == PREVIEW]
    assert preview_jobs and preview_jobs[0]['github']['privateKey'] == B_INPUT['privateKey'] and 'KEY-A-PRIVATE-MARKER' not in json.dumps(preview_jobs)
    request(f'/api/web-projects/{P}/previews/5/refresh', platform, {}, status=202)
    assert last_call(action='pull-request')['key'] == 'KEY-B-PRIVATE-MARKER'
    drain()
    checks.append('pull_requests_use_the_template_connection_other_connection_ignored_and_preview_inherits_it')

    # ---------------------------------------------------------------- 5c. Repositories and branches per connection.
    assert {r['fullName'] for r in request('/api/web-apps/repositories', platform)['items']} == {'acme/web', 'acme/site', 'acme/allowed', 'acme/a-only'}
    assert last_call(action='repositories')['key'] == 'KEY-A-PRIVATE-MARKER'
    assert {r['fullName'] for r in request(f'/api/web-apps/repositories?connectionId={B}', platform)['items']} == {'acme/web', 'acme/allowed', 'acme/ci'}
    assert last_call(action='repositories')['key'] == 'KEY-B-PRIVATE-MARKER'
    request(f'/api/web-apps/repositories?connectionId={UNKNOWN}', platform, status=400)
    request(f'/api/web-apps/repositories/{REPOS["acme/ci"]}/branches?connectionId={B}', platform)
    assert last_call(action='branches')['key'] == 'KEY-B-PRIVATE-MARKER'
    request(f'/api/web-apps/repositories/{REPOS["acme/ci"]}/branches', platform, status=400)  # Not readable by the principal App.
    branches = request(f'/api/web-apps/repositories/{REPOS["acme/web"]}/branches?appId={Y}&connectionId=default', tok['admin'])
    assert branches['repository'] == 'acme/web' and last_call(action='branches')['key'] == 'KEY-B-PRIVATE-MARKER'
    checks.append('repositories_and_branches_read_with_the_requested_connection_and_app_branches_always_with_the_apps')

    # ---------------------------------------------------------------- hook-config: webhook URL configured on GitHub.
    control(hooks={'1001': LEGACY_URL, '1002': LEGACY_URL})
    health = request(f'/api/web-apps/github/health?connectionId={B}', platform)
    assert health['webhook']['configuredUrl'] == LEGACY_URL and health['webhook']['urlMismatch'] is True
    assert health['webhook']['lastReceivedAt'] and last_call(action='hook-config')['key'] == 'KEY-B-PRIVATE-MARKER'
    assert request('/api/web-apps/github/health', platform)['webhook']['urlMismatch'] is False
    control(hooks={'1001': LEGACY_URL, '1002': f'{LEGACY_URL}/{B}'})
    assert request(f'/api/web-apps/github/health?connectionId={B}', platform)['webhook']['urlMismatch'] is False
    assert connections()[B]['lastDelivery']['status'] and connections()[B]['lastDelivery']['at']
    # Case, default port, trailing slash and query string are not a mismatch; another path always is.
    principal_health = lambda: request('/api/web-apps/github/health', platform)['webhook']['urlMismatch']
    control(hooks={'1001': 'HTTPS://APP.Example.Invalid:443/api/web-apps/github/webhook/?from=manual', '1002': f'{LEGACY_URL}/{B}'})
    assert principal_health() is False
    control(hooks={'1001': f'{LEGACY_URL}/{B}', '1002': f'{LEGACY_URL}/{B}'})
    assert principal_health() is True
    # Another host alias that delivered to this connection less than 24 h ago reaches this server: no false alarm.
    control(hooks={'1001': 'https://alias.example.invalid/api/web-apps/github/webhook', '1002': f'https://alias.example.invalid/api/web-apps/github/webhook/{B}'})
    assert principal_health() is False
    with sqlite3.connect(str(database), timeout=10) as db:
        db.execute("UPDATE web_webhook_deliveries SET lastReceivedAt=? WHERE connectionId=?", ((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)).isoformat(), B))
    assert request(f'/api/web-apps/github/health?connectionId={B}', platform)['webhook']['urlMismatch'] is True
    control(hooks={'1001': LEGACY_URL, '1002': f'{LEGACY_URL}/{B}'})
    checks.append('health_per_connection_reports_github_side_webhook_url_mismatch_and_last_delivery')
    checks.append('webhook_url_mismatch_ignores_case_default_port_slash_query_and_a_delivering_host_alias')

    # ---------------------------------------------------------------- 6. RBAC.
    for name in ['owner', 'admin', 'dev', 'viewer', 'outsider']:
        token = tok[name]
        request(CONN, token, status=403)
        request(CONN, token, {**B_INPUT, 'githubAppId': '1999', 'githubInstallationId': '2999'}, status=403)
        same = request(f'{CONN}/{B}', token, {'label': 'Pirate'}, method='PATCH', status=403)
        assert request(f'{CONN}/{UNKNOWN}', token, {'label': 'Pirate'}, method='PATCH', status=403) == same
        same = request(f'{CONN}/{B}', token, method='DELETE', status=403)
        assert request(f'{CONN}/{UNKNOWN}', token, method='DELETE', status=403) == same
        request(f'/api/web-apps/github/health?connectionId={B}', token, status=403)
        request(f'/api/web-apps/repositories?connectionId={B}', token, status=403)
    assert connections()[B]['label'] == 'Org B'
    for name in ['owner', 'admin', 'dev', 'viewer']:
        seen = request('/api/web-apps', tok[name])
        assert isinstance(seen['capabilities'].get('githubConnectionCount'), int)
        text = json.dumps(seen)
        assert 'Org B' not in text and 'app-b' not in text and 'Connexion principale' not in text
    message = request(f'/api/web-apps/{Y}', tok['admin'], {'githubConnectionId': 'default'}, method='PATCH', status=403)['message']
    assert message == PLATFORM_ONLY, message
    # The permission is checked before the id is resolved: no existence or activation oracle for project members.
    for name in ['admin', 'viewer']:
        same = request(f'/api/web-apps/{Y}', tok[name], {'githubConnectionId': 'default'}, method='PATCH', status=403)
        for wanted in [UNKNOWN, 'BAD']:
            assert request(f'/api/web-apps/{Y}', tok[name], {'githubConnectionId': wanted}, method='PATCH', status=403) == same, (name, wanted)
    checks.append('connection_change_permission_checked_before_the_connection_id_is_resolved')
    assert app_config(Y)['githubConnectionId'] == B
    # An unchanged connection is not a change: the project admin can still edit the app.
    request(f'/api/web-apps/{Y}', tok['admin'], {'name': 'Site Y', 'githubConnectionId': B}, method='PATCH')
    # Repository allowlist: a project admin picks only allowed repositories and never moves the app to another connection.
    request(f'/api/web-projects/{P}', platform, {'name': 'Équipe B', 'previewEnabled': True, 'previewSourceAppId': Y, 'previewMax': 1, 'previewTtlHours': 1, 'allowedRepositories': ['acme/allowed']}, method='PATCH')
    request(f'/api/web-apps/{Y}', tok['admin'], {'repository': 'acme/a-only', 'repositoryId': REPOS['acme/a-only']}, method='PATCH', status=403)
    request(f'/api/web-apps/{Y}', tok['admin'], {'repository': 'acme/allowed', 'repositoryId': REPOS['acme/allowed']}, method='PATCH', status=(200, 400))
    assert app_config(Y)['githubConnectionId'] == B
    request(f'/api/web-apps/{Y}', platform, {'repository': 'acme/web', 'repositoryId': REPOS['acme/web']}, method='PATCH')
    # Platform: unknown id 400, valid id 200, 'default' normalized to absence.
    request(f'/api/web-apps/{Z}', platform, {'githubConnectionId': UNKNOWN}, method='PATCH', status=400)
    assert request(f'/api/web-apps/{Z}', platform, {'githubConnectionId': B}, method='PATCH')['app']['githubConnectionId'] == B
    assert 'githubConnectionId' not in request(f'/api/web-apps/{Z}', platform, {'githubConnectionId': 'default'}, method='PATCH')['app']
    assert 'githubConnectionId' not in app_config(Z)
    # Non-platform retry after a connection change: refused; the platform retry keeps the original connection.
    request(f'/api/web-apps/{Y}', platform, {'githubConnectionId': 'default'}, method='PATCH')
    request(f'/api/web-apps/{Y}/deployments/{Y_FAILED}/retry', tok['dev'], {}, status=403)
    request(f'/api/web-apps/{Y}/deployments/{Y_FAILED}/retry', platform, {}, status=202)
    job = claim()
    assert job['appId'] == Y and job['github']['privateKey'] == B_INPUT['privateKey'], 'a retry replays the frozen connection'
    fail(job)
    request(f'/api/web-apps/{Y}', platform, {'githubConnectionId': B}, method='PATCH')
    checks.append('project_members_never_see_or_manage_connections_connection_change_is_platform_only_and_retry_is_scoped')

    # Non-platform restore of a backup taken on another connection: refused.
    request('/test/fixture', platform, {'id': Y, 'action': 'publish'})
    backup = request(f'/api/web-apps/{Y}/backups', platform, {}, status=202)['backup']
    backup_job = request('/api/web-apps/_worker/backups/claim', body={}, headers=worker, keep=False)['job']
    assert backup_job['id'] == backup['id']
    spec = importlib.util.spec_from_file_location('web_app_backups_under_test', scripts / 'web_app_backups.py')
    backups = importlib.util.module_from_spec(spec); spec.loader.exec_module(backups)
    worker_api = lambda path, body: request('/api/web-apps/_worker/' + path, body=body, headers=worker, keep=False)
    image_id = 'sha256:' + 'a' * 64
    docker = lambda _args: json.dumps([{'Id': image_id, 'Size': 1024 ** 2, 'Config': {'Labels': {'pockethost.web.app': Y}}}])
    with mock.patch.object(backups.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=30 * 1024 ** 3)):
        local = backups.create_archive(root / 'web', backup_job, worker_api, docker)
    worker_api('backups/report', {'id': backup['id'], 'lease': backup_job['lease'], 'status': 'ready', 's3State': 'disabled', **local})
    restore_preview = f'/api/web-apps/{Y}/backups/{backup["id"]}/restore/preview'
    request(restore_preview, tok['admin'], {})  # Same connection: the project admin may restore.
    request(f'/api/web-apps/{Y}', platform, {'githubConnectionId': 'default'}, method='PATCH')
    message = request(restore_preview, tok['admin'], {}, status=403)['message']
    assert message == 'Cette sauvegarde utilise une autre connexion GitHub. Sa restauration est réservée aux administrateurs de la plateforme.', message
    request(restore_preview, platform, {})
    request(f'/api/web-apps/{Y}', platform, {'githubConnectionId': B}, method='PATCH')
    checks.append('non_platform_restore_of_a_backup_on_another_connection_is_refused')

    # ---------------------------------------------------------------- 7. Disabled connection.
    drain()
    commit_for('1002')
    request(f'/api/web-apps/{Y}/deploy', platform, {}, status=202)
    request(f'/api/web-apps/{Y}/stop', platform, {}, status=202)
    request(f'{CONN}/{B}', platform, {'disabled': True}, method='PATCH')
    assert connections()[B]['disabled'] is True
    disabled_delivery = 'disabled-' + secrets.token_hex(6)
    ignored = push(b_path, B_INPUT['webhookSecret'], 2002, delivery=disabled_delivery, status=200)
    assert ignored.get('accepted') is True and ignored.get('ignored') is True and not ignored.get('queued')
    row = db_rows('SELECT status, note, connectionId FROM web_webhook_deliveries WHERE deliveryId=?', (delivery_key(B, sent[disabled_delivery]),))[0]
    assert row == ('ignored', 'Connexion GitHub désactivée : aucun déploiement.', B), row
    push(b_path, A['webhookSecret'], 2002, status=401)  # Disabled is only revealed after a valid signature.
    request(f'/api/web-apps/repositories?connectionId={B}', platform, status=400)
    request(f'/api/web-apps/repositories/{REPOS["acme/web"]}/branches?connectionId={B}', platform, status=400)
    request(f'/api/web-apps/{Y}/deploy', platform, {}, status=400)
    assert claim() is None  # The queued source deploy fails inside the claim transaction.
    failed = db_rows("SELECT status, error FROM web_deployments WHERE appId=? AND action='deploy' ORDER BY created DESC LIMIT 1", (Y,))[0]
    assert failed == ('failed', 'Connexion GitHub indisponible : rattachez l’application à une connexion active, puis redéployez.'), failed
    assert db_rows('SELECT lastError FROM web_apps WHERE id=?', (Y,))[0][0] == failed[1]
    job = claim()
    assert job['appId'] == Y and job['action'] == 'stop', job
    assert job.get('github', {'githubAppId': '', 'githubInstallationId': '', 'privateKey': ''}) == {'githubAppId': '', 'githubInstallationId': '', 'privateKey': ''}
    fail(job)
    health = request(f'/api/web-apps/github/health?connectionId={B}', platform)
    assert health['connectionId'] == B and health['disabled'] is True
    request(f'/api/web-apps/{Z}', platform, {'githubConnectionId': B}, method='PATCH', status=400)
    request(f'{CONN}/{B}', platform, {'disabled': False}, method='PATCH')
    assert connections()[B]['disabled'] is False
    checks.append('disabled_connection_ignores_signed_webhooks_refuses_reads_deploys_and_attachment_fails_claimed_source_deploys')

    # ---------------------------------------------------------------- 8. Rename, deletion guard.
    for label in ['x' * 61, '', 'ligne\nsuivante', 'cloche\x07']:
        request(f'{CONN}/{B}', platform, {'label': label}, method='PATCH', status=400)
    request(CONN, platform, {**B_INPUT, 'label': 'x' * 61, 'githubAppId': '1998', 'githubInstallationId': '2998'}, status=400)
    request(f'{CONN}/{B}', platform, {'label': 'Organisation B'}, method='PATCH')
    assert connections()[B]['label'] == 'Organisation B'
    before_delete = settings_rows()['github:' + B]
    message = request(f'{CONN}/{B}', platform, method='DELETE', status=400)['message']
    assert 'Cette connexion GitHub est encore utilisée par' in message and 'Rattachez-les à une autre connexion avant de la supprimer.' in message, message
    assert settings_rows()['github:' + B] == before_delete
    # A queued job whose snapshot references B also keeps it.
    commit_for('1002')
    request(f'/api/web-apps/{Y}/deploy', platform, {}, status=202)
    request(f'/api/web-apps/{Y}', platform, {'githubConnectionId': 'default'}, method='PATCH')
    for app_id in [CI, PREVIEW]:
        raw_row = request(f'/api/collections/web_apps/records/{app_id}', su, keep=False)
        config = dict(raw_row['config']); config.pop('githubConnectionId', None)
        request(f'/api/collections/web_apps/records/{app_id}', su, {'config': config}, method='PATCH', keep=False)
    request(f'{CONN}/{B}', platform, method='DELETE', status=400)
    assert 'github:' + B in settings_rows()
    drain()
    assert connections()[B]['appCount'] == 0
    deleted = request(f'{CONN}/{B}', platform, method='DELETE')
    assert deleted['deleted'] is True and deleted['notice'] == 'Supprimez aussi la GitHub App sur GitHub.' and 'manageUrl' in deleted
    assert 'github:' + B not in settings_rows() and set(connections()) == {'default'}
    assert push(b_path, B_INPUT['webhookSecret'], 2002, status=401) == bad_signature
    assert 'encore utilisée' in request(f'{CONN}/default', platform, method='DELETE', status=400)['message']  # Implicit apps.
    assert settings_rows()['github'] == A_CIPHER
    checks.append('labels_validated_and_deletion_refused_while_apps_or_queued_jobs_use_the_connection')

    # ---------------------------------------------------------------- 9. Limits and uniqueness.
    def manual(n, **extra):
        return {'label': f'Extra {n}', 'githubAppId': str(3000 + n), 'githubAppSlug': f'extra-{n}', 'githubInstallationId': str(4000 + n), 'privateKey': pem(f'KEY-M-PRIVATE-MARKER-{n}'), 'webhookSecret': f'SECRET-M-{n}-' + 'm' * 32, **extra}
    request(CONN, platform, manual(90, githubAppId='1001'), status=400)
    request(CONN, platform, manual(91, githubInstallationId='2001'), status=400)
    extras = [connection_id_of(request(CONN, platform, manual(n), status=(200, 201))) for n in range(1, 10)]
    assert len(connections()) == 10
    request(CONN, platform, manual(10), status=400)
    request(f'{CONN}/{extras[0]}', platform, {'githubAppId': str(3002)}, method='PATCH', status=400)  # Duplicate on update.
    request('/api/web-apps/github/connect/start', platform, {}, status=400)  # Wizard add refused at the limit.
    for ident in extras: request(f'{CONN}/{ident}', platform, method='DELETE')
    assert list(connections()) == ['default']
    checks.append('ten_connections_maximum_and_app_and_installation_ids_unique_across_connections')

    # ---------------------------------------------------------------- Wizard: add by default, replace on request.
    def conversion(app_id, marker, owner_id=77, owner_login='acme-org'):
        control(conversion={'id': app_id, 'slug': f'pockethost-{app_id}', 'owner': {'id': owner_id, 'login': owner_login}, 'pem': pem(f'KEY-{marker}-PRIVATE-MARKER'), 'webhook_secret': f'SECRET-{marker}-' + 'w' * 30, 'permissions': {'contents': 'read', 'metadata': 'read', 'pull_requests': 'read'}, 'events': ['push', 'pull_request']})

    def wizard_start(**body):
        return request('/api/web-apps/github/connect/start', platform, body, status=201)

    def wizard_convert(session, status=200):
        return request('/api/web-apps/github/connect/manifest', platform, {'state': session['state'], 'code': 'a' * 40}, status=status)

    def wizard_finish(step, installation, status=200):
        return request('/api/web-apps/github/connect/install', platform, {'state': step['state'], 'installationId': installation}, status=status)

    session = wizard_start(organization='acme-org', label='Organisation Acme')
    hook_url = session['manifest']['hook_attributes']['url']
    assert hook_url.startswith(LEGACY_URL + '/') and CONNECTION_ID.match(hook_url.rsplit('/', 1)[1]), hook_url
    W = hook_url.rsplit('/', 1)[1]
    pending = request('/api/web-apps/github/connect/status', platform)['pending']
    assert pending['connectionId'] == W and pending['mode'] == 'add' and pending['label'] == 'Organisation Acme'
    conversion(4242, 'W')
    step = wizard_convert(session)
    request(f'{CONN}/default', platform, {'label': 'Principale renommée'}, method='PATCH')  # Another connection changes.
    renamed_cipher = settings_rows()['github']
    assert decrypt(renamed_cipher)['meta']['label'] == 'Principale renommée' and decrypt(renamed_cipher)['privateKey'] == A['privateKey']
    done = wizard_finish(step, '9001')
    assert done['connected'] is True and done['connectionId'] == W and done['label'] == 'Organisation Acme'
    rows = settings_rows()
    assert rows['github'] == renamed_cipher and decrypt(rows['github:' + W])['githubAppId'] == '4242'
    assert decrypt(rows['github:' + W])['meta']['label'] == 'Organisation Acme'
    status = request('/api/web-apps/github/connect/status', platform)
    assert status['connections'] == 2 and status['pending'] is None
    assert connections()[W]['webhookUrl'] == hook_url
    no_secrets(done)
    checks.append('wizard_adds_a_connection_by_default_without_touching_the_principal_even_if_it_changes_meanwhile')

    session = wizard_start(mode='replace', connectionId='default')
    assert session['manifest']['hook_attributes']['url'] == LEGACY_URL
    conversion(4343, 'R', owner_id=501, owner_login='owner-a')
    done = wizard_finish(wizard_convert(session), '9002')
    assert done['connectionId'] == 'default'
    rows = settings_rows()
    replaced = decrypt(rows['github'])
    assert rows['github'] != renamed_cipher and replaced['githubAppId'] == '4343' and replaced['githubInstallationId'] == '9002'
    assert replaced['meta']['label'] == 'Principale renommée', 'replace keeps the existing meta'
    assert decrypt(rows['github:' + W])['githubAppId'] == '4242'
    w_cipher = rows['github:' + W]
    session = wizard_start(mode='replace', connectionId='default')
    conversion(4444, 'R', owner_id=501, owner_login='owner-a')
    step = wizard_convert(session)
    request(f'{CONN}/default', platform, {'label': 'Encore renommée'}, method='PATCH')
    concurrent = settings_rows()['github']
    wizard_finish(step, '9003', status=400)
    assert settings_rows()['github'] == concurrent and decrypt(concurrent)['githubAppId'] == '4343'
    request('/api/web-apps/github/connect/start', platform, {'mode': 'replace', 'connectionId': UNKNOWN}, status=400)
    session = wizard_start()
    conversion(4242, 'D')  # Same App as W: refused.
    step = wizard_convert(session, status=(200, 400))
    if step and step.get('state'): wizard_finish(step, '9004', status=400)
    session = wizard_start(organization='other-org')
    conversion(4545, 'D')
    wizard_convert(session, status=400)
    assert set(settings_rows()) == {'github', 'github:' + W} and settings_rows()['github:' + W] == w_cipher
    checks.append('wizard_replace_rewrites_only_its_target_keeps_meta_and_refuses_concurrent_change_known_app_or_org')

    # ---------------------------------------------------------------- Unpause rotation covers every connection.
    sys.path.insert(0, str(scripts))
    import web_platform_recovery_unpause as unpause
    copy = root / 'unpause-copy.db'
    live, target = sqlite3.connect(str(database), timeout=10), sqlite3.connect(copy)
    live.backup(target); live.close(); target.close()
    read_copy = lambda: {k: decrypt(v) for k, v in sqlite3.connect(copy).execute('SELECT key, valueEncrypted FROM web_app_settings').fetchall()}
    before = read_copy()
    connection = sqlite3.connect(copy)
    hard = unpause.hardening(connection, False, True, MASTER.encode())
    rotated = unpause.apply(connection, {}, hard, MASTER.encode(), datetime.datetime.now(datetime.timezone.utc))
    summary = unpause.summarize({}, hard)
    connection.close()
    after = read_copy()
    assert set(rotated['githubWebhookSecrets']) == {'default', W}
    assert rotated['githubWebhookSecret'] == after['github']['webhookSecret'] != before['github']['webhookSecret']
    for ident, item in rotated['githubWebhookSecrets'].items():
        name = 'github' if ident == 'default' else 'github:' + ident
        assert item == {'slug': after[name]['githubAppSlug'], 'secret': after[name]['webhookSecret']} and item['secret'] != before[name]['webhookSecret']
        assert {k: v for k, v in after[name].items() if k != 'webhookSecret'} == {k: v for k, v in before[name].items() if k != 'webhookSecret'}, 'meta and credentials kept'
    assert summary['githubWebhookSecretsRotated'] == 2 and summary['githubWebhookSecretRotated'] is True
    checks.append('unpause_rotates_every_connection_webhook_secret_keeps_meta_and_reports_each_connection')

    # ---------------------------------------------------------------- Principal deleted when unused; a new one is 'default'.
    drain()
    for (app_id, raw_config) in db_rows('SELECT id, config FROM web_apps'):
        config = json.loads(raw_config or '{}')
        if config.get('source') != 'git' and config.get('repository') and not config.get('githubConnectionId'):
            request(f'/api/collections/web_apps/records/{app_id}', su, {'config': {**config, 'githubConnectionId': W}}, method='PATCH', keep=False)
    assert connections()['default']['appCount'] == 0
    request(f'{CONN}/default', platform, method='DELETE')
    assert set(settings_rows()) == {'github:' + W}
    assert push(LEGACY_PATH, A['webhookSecret'], 2001, status=401) == bad_signature
    assert push(LEGACY_PATH, replaced['webhookSecret'], 9002, status=401) == bad_signature
    session = wizard_start(label='Nouvelle principale')
    assert session['manifest']['hook_attributes']['url'] == LEGACY_URL
    conversion(4646, 'D', owner_id=601, owner_login='owner-new')
    done = wizard_finish(wizard_convert(session), '9006')
    assert done['connectionId'] == 'default' and 'github' in settings_rows()
    listed = connections()
    assert listed['default']['isPrincipal'] is True and listed['default']['webhookUrl'] == LEGACY_URL and listed['default']['label'] == 'Nouvelle principale'
    request(f'{CONN}/default', platform, method='DELETE')
    # A wizard session started before the upgrade (publicInfo without connectionId) on a server without principal
    # still completes: the principal is created, without meta, as the old code did.
    session = wizard_start(label='Ancienne session')
    with sqlite3.connect(str(database), timeout=10) as db:
        assert db.execute("UPDATE web_github_setup_sessions SET publicInfo=? WHERE status='created'", (json.dumps({'organization': ''}),)).rowcount == 1
    conversion(4848, 'G', owner_id=801, owner_login='owner-legacy')
    done = wizard_finish(wizard_convert(session), '9008')
    assert done['connected'] is True and done['connectionId'] == 'default', done
    legacy_value = decrypt(settings_rows()['github'])
    assert legacy_value['githubAppId'] == '4848' and legacy_value['githubInstallationId'] == '9008' and 'meta' not in legacy_value
    assert connections()['default']['label'] == 'Connexion principale'
    request(f'{CONN}/default', platform, method='DELETE')
    checks.append('legacy_wizard_session_without_principal_still_creates_it')
    created = request(CONN, platform, {'label': 'Saisie manuelle', 'githubAppId': '4747', 'githubAppSlug': 'manual-app', 'githubInstallationId': '9007', 'privateKey': pem('KEY-M-PRIVATE-MARKER-principal'), 'webhookSecret': 'SECRET-L-' + 'l' * 40}, status=(200, 201))
    assert connection_id_of(created) == 'default' and connections()['default']['webhookUrl'] == LEGACY_URL
    raw = json.dumps({'ref': 'refs/heads/main', 'after': sha(), 'repository': {'full_name': 'acme/none'}, 'installation': {'id': 9007}}).encode()
    request(LEGACY_PATH, raw=raw, headers={'X-GitHub-Event': 'push', 'X-GitHub-Delivery': 'new-principal-1', 'X-Hub-Signature-256': 'sha256=' + sign('SECRET-L-' + 'l' * 40, raw)}, status=(200, 202))
    checks.append('unused_principal_deletable_legacy_path_then_401_and_next_connection_becomes_default_on_the_legacy_url')

    # ---------------------------------------------------------------- Previews: a dangling or Git template never widens a delivery.
    w_secret, l_secret, w_path = 'SECRET-W-' + 'w' * 30, 'SECRET-L-' + 'l' * 40, f'{LEGACY_PATH}/{W}'
    control(repos={**control_state['repos'], '4242': [{'id': REPOS['acme/web'], 'full_name': 'acme/web'}], '4747': [{'id': REPOS['acme/web'], 'full_name': 'acme/web'}]})
    teams = []
    for index in (1, 2):
        template = request('/api/web-apps', platform, {'name': f'Modèle Q{index}', 'repository': 'acme/web', 'repositoryId': REPOS['acme/web'], 'branch': 'main', 'autoDeploy': False, 'githubConnectionId': W}, status=201)['app']['id']
        team = request(f'/api/web-apps/{template}/project', platform, {'name': f'Équipe Q{index}', 'environment': 'prod'}, status=201)['project']['id']
        request(f'/api/web-projects/{team}', platform, {'name': f'Équipe Q{index}', 'previewEnabled': True, 'previewSourceAppId': template, 'previewMax': 2, 'previewTtlHours': 1}, method='PATCH')
        teams.append(team)
    repo9 = {'id': REPOS['acme/web'], 'full_name': 'acme/web'}
    pr9 = {'number': 9, 'state': 'open', 'draft': False, 'author_association': 'MEMBER', 'head': {'sha': sha(), 'ref': 'feature-9', 'repo': repo9}, 'base': {'repo': repo9}}
    control(prs={'4242': {'9': pr9}, '4747': {'9': {**pr9, 'state': 'closed'}}})
    pull9 = lambda installation, action='synchronize': {'action': action, 'number': 9, 'repository': {'full_name': 'acme/web'}, 'installation': {'id': installation}, 'pull_request': {'head': {'sha': '0' * 40}}}
    preview9 = lambda team: (db_rows("SELECT state, appId FROM web_app_environments WHERE projectId=? AND environment='preview' AND previewNumber=9", (team,)) or [(None, None)])[0]
    hook(w_path, w_secret, 'pull_request', pull9(9001, 'opened'), status=(200, 202))
    assert [preview9(team)[0] for team in teams] == ['active', 'active']
    q_previews = [preview9(team)[1] for team in teams]
    assert all(app_config(app_id).get('githubConnectionId') == W for app_id in q_previews)
    drain()
    # Q2's template disappears: its id now points to no application.
    with sqlite3.connect(str(database), timeout=10) as db:
        db.execute('UPDATE web_projects SET previewSourceAppId=? WHERE id=?', (UNKNOWN, teams[1]))
    # A signed close from ANOTHER connection (the principal) never touches previews served by W.
    hook(LEGACY_PATH, l_secret, 'pull_request', pull9(9007, 'closed'), status=(200, 202))
    assert [preview9(team)[0] for team in teams] == ['active', 'active']
    # A trusted synchronize from W still updates Q1: Q2's dangling template no longer fails the whole transaction.
    pr9['head']['sha'] = sha()
    control(prs={'4242': {'9': pr9}, '4747': {'9': {**pr9, 'state': 'closed'}}})
    hook(w_path, w_secret, 'pull_request', pull9(9001), status=(200, 202))
    assert db_rows("SELECT count(*) FROM web_deployments WHERE appId=? AND status='queued' AND action='deploy'", (q_previews[0],))[0][0] == 1
    assert db_rows("SELECT count(*) FROM web_deployments WHERE appId=? AND status='queued' AND action='deploy'", (q_previews[1],))[0][0] == 0
    drain()
    # W's own close closes both: Q2 is governed by its preview application's connection.
    control(prs={'4242': {'9': {**pr9, 'state': 'closed'}}, '4747': {'9': {**pr9, 'state': 'closed'}}})
    hook(w_path, w_secret, 'pull_request', pull9(9001, 'closed'), status=(200, 202))
    assert [preview9(team)[0] for team in teams] == ['closing', 'closing']
    drain()
    checks.append('preview_deliveries_never_act_across_connections_and_a_dangling_template_does_not_fail_other_projects')

    # ---------------------------------------------------------------- Usage counts every application (no fixed cap).
    volume = connection_id_of(request(CONN, platform, {'label': 'Volume', 'githubAppId': '4949', 'githubAppSlug': 'volume-app', 'githubInstallationId': '9009', 'privateKey': pem('KEY-M-PRIVATE-MARKER-volume'), 'webhookSecret': 'SECRET-M-volume-' + 'v' * 32}, status=(200, 201)))
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S.000Z')
    with sqlite3.connect(str(database), timeout=10) as db:
        db.executemany('INSERT INTO web_apps (id, name, uid, status, config, created, updated) VALUES (?, ?, ?, ?, ?, ?, ?)', [(f'bulk{i:011d}', f'Bulk {i}', platform_row['id'], 'stopped', '{}', stamp, stamp) for i in range(2100)])
    late = request('/api/web-apps', platform, {'name': 'Après 2100', 'repository': 'acme/web', 'repositoryId': REPOS['acme/web'], 'branch': 'main', 'autoDeploy': False, 'githubConnectionId': volume}, status=201)['app']['id']
    assert connections()[volume]['appCount'] == 1
    assert 'encore utilisée par 1 application' in request(f'{CONN}/{volume}', platform, method='DELETE', status=400)['message']
    assert 'github:' + volume in settings_rows()
    with sqlite3.connect(str(database), timeout=10) as db:
        db.execute("DELETE FROM web_apps WHERE id LIKE 'bulk%'")
    request(f'/api/web-apps/{late}', platform, {'githubConnectionId': W}, method='PATCH')
    request(f'{CONN}/{volume}', platform, method='DELETE')
    # Attaching re-reads the connection inside the write transaction: a deleted one is refused, nothing is saved.
    request(f'/api/web-apps/{late}', platform, {'githubConnectionId': volume}, method='PATCH', status=400)
    request('/api/web-apps', platform, {'name': 'Orpheline', 'repository': 'acme/web', 'branch': 'main', 'githubConnectionId': volume}, status=400)
    assert app_config(late)['githubConnectionId'] == W
    checks.append('connection_usage_counts_every_application_beyond_any_page_and_a_deleted_connection_cannot_be_attached')

    # ---------------------------------------------------------------- Deliveries stored without connection by the old code.
    with sqlite3.connect(str(database), timeout=10) as db:
        db.execute("INSERT INTO web_webhook_deliveries (id, deliveryId, event, repository, branch, status, connectionId, created) VALUES ('rollbackpush001', 'rollback-push-1', 'push', 'acme/web', 'main', 'ignored', '', ?)", (stamp,))
        db.execute("INSERT INTO web_webhook_deliveries (id, deliveryId, event, repository, branch, status, connectionId, created) VALUES ('rollbackgit0001', ?, 'push', 'git:github.com/acme/x', 'main', 'ignored', '', ?)", ('git-' + 'b' * 15 + '-rollback', stamp))
    stop()
    su = serve(migrations, hooks)
    assert dict(db_rows("SELECT deliveryId, connectionId FROM web_webhook_deliveries WHERE id IN ('rollbackpush001', 'rollbackgit0001')")) == {'rollback-push-1': 'default', 'git-' + 'b' * 15 + '-rollback': ''}
    checks.append('github_deliveries_recorded_without_connection_during_a_code_rollback_are_reattached_to_the_principal_at_boot')

    # ---------------------------------------------------------------- No secret in any browser response, none in clear at rest.
    for text in responses:
        no_secrets(text)
    blob = b''.join(path.read_bytes() for path in data.glob('data.db*'))
    for marker in [b'KEY-A-PRIVATE-MARKER', b'KEY-B-PRIVATE-MARKER', b'KEY-W-PRIVATE-MARKER', A['webhookSecret'].encode(), B_INPUT['webhookSecret'].encode(), b'SECRET-W-']:
        assert marker not in blob, marker
    checks.append('no_route_returns_a_private_key_or_webhook_secret_and_connections_are_ciphertext_at_rest')

    # ---------------------------------------------------------------- Rollback refused while the principal is disabled.
    request(f'{CONN}/default', platform, {'disabled': True}, method='PATCH')
    stop()
    # Later migrations (1793271400_… and after) are reverted first; the GitHub connections one must then refuse.
    steps = 1 + sum(1 for path in migrations.glob('*.js') if path.name.split('_', 1)[0] > '1793271300')
    down = subprocess.run([args.pocketbase, 'migrate', 'down', str(steps), '--dir', str(data), '--hooksDir', str(root / 'empty-hooks'), '--migrationsDir', str(migrations)], env=env, input='y\n', capture_output=True, text=True, timeout=60)
    assert down.returncode != 0 and 'principale est désactivée' in down.stdout + down.stderr, (down.returncode, down.stdout[-800:], down.stderr[-800:])
    assert 'connectionId' in [row[1] for row in db_rows('PRAGMA table_info(web_webhook_deliveries)')]
    checks.append('migration_down_refused_while_the_principal_connection_is_disabled')
    print(json.dumps({'passed': True, 'checks': checks}))
except Exception:
    if (root / 'process.log').exists(): print((root / 'process.log').read_text()[-4000:], file=sys.stderr)
    raise
finally:
    if process:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
    temporary.cleanup()
