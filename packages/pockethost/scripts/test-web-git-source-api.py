#!/usr/bin/env python3
"""Disposable PocketBase: « Dépôt Git (clé de déploiement) » routes, webhook, claim and app wiring.

The controller's Git reader (GIT_SOURCE_READER, run as `python3 -c`) is intercepted by a `python3` shim placed first on
PATH: keygen runs the real reader (real Ed25519 key), keyscan and ls-remote answer from a local control file. No DNS,
SSH or network access happens. The GitHub reader's `commit` lookup is stubbed the same way.
"""
import argparse, base64, hashlib, hmac, json, os, pathlib, secrets, shutil, socket, sqlite3, struct, subprocess, sys, tempfile, time, urllib.error, urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True, type=pathlib.Path)
parser.add_argument('--pocketbase', required=True)
args = parser.parse_args()
os.umask(0o077)
temporary = tempfile.TemporaryDirectory(prefix='ph-web-git-source-')
root = pathlib.Path(temporary.name).resolve()
process = None
checks = []
responses = []
MASTER = 'g' * 32
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
    nonce = secrets.token_bytes(12)
    return base64.b64encode(nonce + aes().encrypt(nonce, json.dumps(value).encode(), None)).decode()


def fake_key(seed):
    blob = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + hashlib.sha256(seed.encode()).digest()
    return {'type': 'ssh-ed25519', 'key': base64.b64encode(blob).decode(), 'fingerprint': 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')}


GITHUB_ED25519 = {'type': 'ssh-ed25519', 'key': 'AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl', 'fingerprint': 'SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU'}
control_file = root / 'git-control.json'
reader_log = root / 'git-reader.log'
control_state = {'scan': {'*': [fake_key('host-a'), fake_key('host-b')], 'github.com': [GITHUB_ED25519]}, 'heads': {}, 'fail': ''}


def control(**values):
    control_state.update(values)
    control_file.write_text(json.dumps(control_state))


def head(path, branch, sha):
    control_state['heads'].setdefault(path, {})[branch] = sha
    control(heads=control_state['heads'])


SHIM = r'''#!{python}
import sys,json,pathlib,os
REAL={python!r};CONTROL={control!r};LOG={log!r}
code=sys.argv[2] if len(sys.argv)>3 and sys.argv[1]=='-c' else ''
p=pathlib.Path(sys.argv[-1])
try:d=json.loads(p.read_text())
except Exception:d={{}}
def out(v):
 try:p.unlink()
 except Exception:pass
 print(json.dumps(v));sys.exit(0)
if "ALIAS='pockethost-git'" in code:
 a=d.get('action')
 with open(LOG,'a') as f:f.write(json.dumps({{'action':a,'mode':d.get('mode'),'branch':d.get('branch'),'remote':d.get('remote'),'privateKey':str(d.get('privateKey') or '')[:36],'hostKeys':d.get('hostKeys')}})+'\n')
 if a=='keygen':os.execv(REAL,[REAL]+sys.argv[1:])
 c=json.loads(pathlib.Path(CONTROL).read_text())
 if c.get('fail'):out({{'error':c['fail'],'code':'publickey'}})
 if a=='keyscan':out({{'hostKeys':c['scan'].get(d['remote']['host'],c['scan']['*'])}})
 heads=c['heads'].get(d['remote']['path'],{{}});b=d.get('branch') or ''
 mode=d.get('mode') if a=='ls-remote' else 'test'
 if mode=='heads':out({{'branches':[{{'name':k,'commit':v}} for k,v in heads.items()],'defaultBranch':'main' if 'main' in heads else '','truncated':False,'skipped':0}})
 if mode=='resolve':
  if b not in heads:out({{'error':'Branche introuvable dans ce dépôt.','code':'branch'}})
  out({{'commit':heads[b]}})
 out({{'ok':True,'defaultBranch':'main','head':heads.get('main',''),'branchFound':(b in heads) if b else None,'commit':heads.get(b,'')}})
if d.get('action')=='commit':out({{'sha':'d'*40}})
os.execv(REAL,[REAL]+sys.argv[1:])
'''


def reader_calls():
    return [json.loads(line) for line in reader_log.read_text().splitlines()] if reader_log.exists() else []


def sign(secret, raw):
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks = root / 'hooks'; hooks.mkdir()
    shutil.copy(source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    shutil.copy(source / 'src/lib/handlers/webApps/hooks.ts', hooks / 'web.pb.js')
    tools = root / 'tools'; tools.mkdir()
    shim = tools / 'python3'; shim.write_text(SHIM.format(python=sys.executable, control=str(control_file), log=str(reader_log))); shim.chmod(0o700)
    control()
    env = {**os.environ, 'PATH': str(tools) + os.pathsep + os.environ.get('PATH', ''), 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_MASTER_KEY': MASTER, 'WEB_APPS_WORKER_SECRET': 'W' + secrets.token_urlsafe(40), 'WEB_APPS_ROOT': str(root / 'web'), 'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid', 'APP_HOST': 'app.example.invalid'}
    worker = {'X-Web-Worker': env['WEB_APPS_WORKER_SECRET']}
    password, email = 'P' + secrets.token_urlsafe(32), 'su@example.invalid'
    data = root / 'pb_data'
    subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(data), '--hooksDir', str(root / 'empty-hooks'), '--migrationsDir', str(source / 'pb_migrations')], env=env, check=True, capture_output=True)
    log = (root / 'process.log').open('wb')
    process = subprocess.Popen([args.pocketbase, 'serve', '--dir', str(data), '--hooksDir', str(hooks), '--migrationsDir', str(source / 'pb_migrations'), '--http', f'127.0.0.1:{port}'], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(150):
        if process.poll() is not None: raise RuntimeError('PocketBase failed to start: ' + (root / 'process.log').read_text()[-3000:])
        try: request('/api/health', keep=False); break
        except OSError: time.sleep(.1)
    su = request('/api/collections/_superusers/auth-with-password', body={'identity': email, 'password': password}, keep=False)['token']

    def user(name, admin=False):
        row = request('/api/collections/users/records', su, {'email': name + '@example.invalid', 'username': name, 'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': admin, 'subscription': 'premium', 'subscription_quantity': 1}, keep=False)
        return row, request('/api/collections/users/auth-with-password', body={'identity': row['email'], 'password': password}, keep=False)['token']
    platform_row, platform = user('platform', True)
    tok = {name: user(name)[1] for name in ['owner', 'admin', 'dev', 'viewer', 'outsider']}
    API = '/api/web-git-sources'

    def create(url, token=platform, status=(200, 201), project=''):
        return request(API, token, {'url': url, **({'projectId': project} if project else {})}, status=status)

    def drain():
        jobs = []
        for _ in range(50):
            job = request('/api/web-apps/_worker/claim', body={}, headers=worker, keep=False)['job']
            if not job: return jobs
            jobs.append(job)
            request('/api/web-apps/_worker/report', body={'id': job['id'], 'status': 'failed', 'error': 'Fixture drained'}, headers=worker, keep=False)
        raise AssertionError('worker queue did not drain')

    # 3. Create: one row per canonical repository, preset pins on the forges, refused remotes never echoed.
    first = request(API, platform, {'url': 'git@github.com:Owner/Repo.git'}, status=201)
    assert first['created'] is True
    gh = first['source']
    assert gh['canonical'] == 'github.com/owner/repo' and gh['repository'] == 'git:github.com/owner/repo' and gh['provider'] == 'github'
    assert gh['hostKeysConfirmed'] and gh['hostKeysPreset'] and gh['ready'] and gh['publicKey'].startswith('ssh-ed25519 ') and gh['fingerprint'].startswith('SHA256:')
    again = request(API, platform, {'url': 'ssh://git@github.com/owner/repo'})
    assert again['created'] is False and again['source']['id'] == gh['id']
    alice = create('alice@srv.example.com:app.git')['source']; bob = create('bob@srv.example.com:app.git')['source']
    assert alice['id'] != bob['id'] and alice['canonical'].startswith('alice@') and alice['canonical'] != bob['canonical']
    absolute = create('git@srv.example.com:/srv/app.git')['source']; relative = create('git@srv.example.com:srv/app.git')['source']
    assert absolute['id'] != relative['id'] and absolute['canonical'] == 'srv.example.com//srv/app' and relative['canonical'] == 'srv.example.com/srv/app'
    upper = create('git@srv.example.com:Team/Site.git')['source']; lower = create('git@srv.example.com:team/site.git')['source']
    assert upper['id'] != lower['id'] and upper['canonical'] == 'srv.example.com/Team/Site'
    for bad in ['https://github.com/owner/repo.git', 'ext::sh -c touch% /tmp/pwned', 'file:///etc/passwd', 'git@1.2.3.4:owner/repo.git', 'git@[::1]:owner/repo.git', 'git@localhost:owner/repo.git', 'git@srv.example.com:../escape.git', 'git@srv.example.com:owner/../../x.git', '-oProxyCommand=touch@srv.example.com:x.git', 'ssh://-oProxyCommand=x/repo']:
        message = request(API, platform, {'url': bad}, status=400)['message']
        assert bad not in message and 'Adresse' in message, (bad, message)
    request(API, body={'url': 'git@srv.example.com:x/y.git'}, status=401)
    request(API, tok['outsider'], {'url': 'git@srv.example.com:x/y.git'}, status=403)
    checks.append('create_is_idempotent_per_canonical_keeps_user_case_and_absolute_paths_and_refuses_unsafe_remotes')

    # Project wiring: a projectless source is adopted by the project of the app that uses it.
    p = lower  # git@srv.example.com:team/site.git, TOFU host
    app_a = request('/api/web-apps', platform, {'name': 'Site A', 'source': 'git', 'gitSourceId': p['id'], 'branch': 'main', 'repository': 'forged/value'}, status=201)['app']
    assert app_a['source'] == 'git' and app_a['gitSourceId'] == p['id'] and app_a['repository'] == 'git:srv.example.com/team/site' and app_a['repositoryId'] == 0
    team = request(f'/api/web-apps/{app_a["id"]}/project', platform, {'name': 'Équipe A', 'environment': 'prod'}, status=201)['project']
    assert team['repository'] == 'git:srv.example.com/team/site'
    for name, role in [('owner', 'owner'), ('admin', 'admin'), ('dev', 'developer'), ('viewer', 'viewer')]:
        request(f'/api/web-projects/{team["id"]}/members', platform, {'email': name + '@example.invalid', 'role': role}, status=201)
    assert request(f'{API}/{p["id"]}', platform)['source']['projectId'] == team['id']
    checks.append('git_app_create_derives_repository_and_project_creation_adopts_the_source')

    # 1. Auth matrix. 401 without session; viewer reads public material only; project admin cannot create/delete.
    sid = p['id']
    for method, path, body in [('GET', f'{API}/{sid}', None), ('DELETE', f'{API}/{sid}', None), ('GET', f'{API}/{sid}/branches', None)] + [('POST', f'{API}/{sid}/{a}', {}) for a in ['scan', 'confirm-host-keys', 'test', 'rotate-key', 'rotate-webhook-secret']]:
        request(path, body=body, method=method, status=401)
    viewed = request(f'{API}/{sid}', tok['viewer'])['source']
    assert viewed['publicKey'] == p['publicKey'] and viewed['appCount'] == 1 and 'privateKey' not in json.dumps(viewed) and 'secret' not in json.dumps(viewed).lower().replace('webhook', '')
    for name in ['viewer', 'dev']:
        for action in ['scan', 'confirm-host-keys', 'test', 'rotate-key', 'rotate-webhook-secret']:
            request(f'{API}/{sid}/{action}', tok[name], {}, status=403)
        request(f'{API}/{sid}/branches', tok[name], status=403)
    request(API, tok['admin'], {'url': 'git@srv.example.com:team/other.git', 'projectId': team['id']}, status=403)
    request(f'{API}/{sid}', tok['admin'], method='DELETE', status=403)
    request(f'{API}/{sid}', tok['owner'], method='DELETE', status=403)
    for path, body in [(f'{API}/{sid}', None), (f'{API}/{sid}/scan', {}), (f'{API}/{sid}/branches', None), (f'{API}/{sid}/unknown', {})]:
        request(path, tok['outsider'], body=body, status=404)
    solo = create('git@srv.example.com:solo/app.git')['source']
    for name in ['owner', 'admin', 'viewer', 'outsider']:
        request(f'{API}/{solo["id"]}', tok[name], status=404)
        request(f'{API}/{solo["id"]}/scan', tok[name], {}, status=404)
    assert request(f'{API}/{solo["id"]}', platform)['source']['id'] == solo['id']
    request(f'{API}/{sid}/unknown', platform, {}, status=404)
    request(f'{API}/abc/scan', platform, {}, status=400)
    checks.append('routes_401_without_session_403_below_admin_or_platform_404_outside_project_and_projectless_superadmin_only')

    # 2. Native collection access refused.
    for name in ['viewer', 'admin', 'outsider']:
        request('/api/collections/web_git_sources/records', tok[name], status=(403, 404))
        request(f'/api/collections/web_git_sources/records/{sid}', tok[name], status=(403, 404))
    checks.append('native_collection_api_refused_to_users')

    # 4. Scan -> confirm on a TOFU host. Nonce single-use, expiry, exact set (on `bob`: the per-source rate budget of
    # the project source is kept for the admin flow below).
    tofu = bob['id']
    scanned = request(f'{API}/{tofu}/scan', platform, {})
    assert scanned['preset'] is False and len(scanned['nonce']) == 48 and scanned['changed'] is False
    prints = [k['fingerprint'] for k in scanned['hostKeys']]
    assert sorted(prints) == sorted(k['fingerprint'] for k in control_state['scan']['*'])
    confirm = f'{API}/{tofu}/confirm-host-keys'
    request(confirm, platform, {'nonce': 'x' * 48, 'fingerprints': prints}, status=400)
    request(confirm, platform, {'nonce': scanned['nonce'], 'fingerprints': prints[:1]}, status=400)
    request(confirm, platform, {'nonce': scanned['nonce'], 'fingerprints': prints + [fake_key('extra')['fingerprint']]}, status=400)
    request(confirm, platform, {'nonce': 'short', 'fingerprints': prints}, status=400)
    second = request(f'{API}/{tofu}/scan', platform, {})
    request(confirm, platform, {'nonce': scanned['nonce'], 'fingerprints': prints}, status=400)  # superseded
    row = request(f'/api/collections/web_git_sources/records/{tofu}', su, keep=False)
    pending = decrypt(row['pendingHostKeysEncrypted']); pending['expiresAt'] = '2000-01-01T00:00:00.000Z'
    request(f'/api/collections/web_git_sources/records/{tofu}', su, {'pendingHostKeysEncrypted': encrypt(pending)}, method='PATCH', keep=False)
    request(confirm, platform, {'nonce': second['nonce'], 'fingerprints': prints}, status=400)  # expired
    third = request(f'{API}/{tofu}/scan', platform, {})
    pinned = request(confirm, platform, {'nonce': third['nonce'], 'fingerprints': list(reversed(prints))})['source']
    assert pinned['hostKeysConfirmed'] and pinned['ready'] and not pinned['hostKeysPreset'] and sorted(k['fingerprint'] for k in pinned['hostKeys']) == sorted(prints)
    request(confirm, platform, {'nonce': third['nonce'], 'fingerprints': prints}, status=400)  # single-use
    request(f'{API}/{gh["id"]}/confirm-host-keys', platform, {'nonce': 'A' * 48, 'fingerprints': [GITHUB_ED25519['fingerprint']]}, status=400)
    # Project admin pins the project source.
    scanned = request(f'{API}/{sid}/scan', tok['admin'], {})
    pinned = request(f'{API}/{sid}/confirm-host-keys', tok['admin'], {'nonce': scanned['nonce'], 'fingerprints': [k['fingerprint'] for k in scanned['hostKeys']]})['source']
    assert pinned['ready'] and pinned['projectId'] == team['id']
    checks.append('tofu_scan_confirm_requires_exact_fresh_single_use_nonce_and_preset_hosts_refuse_confirmation')

    # Preset host scan: published key matches; a different key is refused (no TOFU on the forges).
    preset_scan = request(f'{API}/{gh["id"]}/scan', platform, {})
    assert preset_scan['preset'] and preset_scan['confirmed'] and 'nonce' not in preset_scan
    control(scan={**control_state['scan'], 'github.com': [fake_key('mitm')]})
    assert 'ne correspondent pas' in request(f'{API}/{gh["id"]}/scan', platform, {}, status=400)['message']
    control(scan={**control_state['scan'], 'github.com': [GITHUB_ED25519]})
    checks.append('preset_scan_matches_published_keys_and_refuses_substituted_host_keys')

    # Test and branches (admin) with the pinned keys and the decrypted deploy key.
    head(p['path'], 'main', 'a' * 40); head(p['path'], 'feature/x', 'b' * 40)
    tested = request(f'{API}/{sid}/test', tok['admin'], {'branch': 'main'})
    assert tested['ok'] and tested['branchFound'] is True and tested['commit'] == 'a' * 40 and tested['source']['lastCheckStatus'] == 'ok'
    branches = request(f'{API}/{sid}/branches?perPage=5000', tok['admin'])
    assert [b['name'] for b in branches['items']] == ['main', 'feature/x'] and branches['defaultBranch'] == 'main'
    call = [c for c in reader_calls() if c['action'] in ('ls-remote', 'test')][-1]
    assert call['privateKey'].startswith('-----BEGIN OPENSSH PRIVATE KEY') and sorted(k['key'] for k in call['hostKeys']) == sorted(k['key'] for k in control_state['scan']['*'])
    assert all(set(k) == {'type', 'key'} for k in call['hostKeys']) and call['remote'] == {'host': 'srv.example.com', 'port': 22, 'sshUser': 'git', 'path': 'team/site.git'}, call
    checks.append('test_and_branches_use_pinned_host_keys_and_decrypted_key_only_inside_the_reader')

    # 5. Rate limit: 6 controls per minute and per source, the 7th is 429 (scan/test/branches share the budget).
    rate = create('git@github.com:rate/limit.git')['source']
    head(rate['path'], 'main', 'c' * 40)
    for path, body in [('/scan', {}), ('/test', {}), ('/test', {}), ('/test', {}), ('/test', {})]:
        request(f'{API}/{rate["id"]}{path}', platform, body)
    request(f'{API}/{rate["id"]}/branches', platform)
    request(f'{API}/{rate["id"]}/scan', platform, {}, status=429)
    request(f'{API}/{rate["id"]}/branches', platform, status=429)
    checks.append('seventh_scan_test_or_branches_call_per_minute_is_429')

    # 7. Webhook secret: shown once, never readable afterwards; disable clears it.
    revealed = request(f'{API}/{sid}/rotate-webhook-secret', tok['admin'], {})
    secret = revealed['secret']
    assert len(secret) >= 32 and revealed['url'] == f'https://app.example.invalid/api/web-git-sources/webhook/{revealed["url"].rsplit("/", 1)[1]}'
    hook_id = revealed['url'].rsplit('/', 1)[1]
    assert len(hook_id) == 24 and revealed['source']['webhook']['configured']
    seen = request(f'{API}/{sid}', tok['viewer'])['source']
    assert seen['webhook']['configured'] and secret not in json.dumps(seen)
    checks.append('webhook_secret_returned_once_and_only_its_configured_flag_afterwards')

    # 6. Rotate-key needs confirm while apps use the key; delete refused while referenced.
    message = request(f'{API}/{sid}/rotate-key', tok['admin'], {}, status=400)['message']
    assert '1 application' in message
    rotated = request(f'{API}/{sid}/rotate-key', tok['admin'], {'confirm': True})
    assert rotated['publicKey'] != p['publicKey'] and rotated['appCount'] == 1 and rotated['source']['rotatedAt'] and rotated['source']['ready']
    assert 'encore utilisé' in request(f'{API}/{sid}', platform, method='DELETE', status=400)['message']
    removed = request(f'{API}/{solo["id"]}', platform, method='DELETE')
    assert removed['deleted'] and 'hébergeur' in removed['notice']
    request(f'{API}/{solo["id"]}', platform, status=404)
    checks.append('rotate_key_requires_confirmation_when_used_and_delete_refused_while_referenced')

    # 10. validateConfig: source changes are platform-only, repository always derived, project binding enforced.
    request(f'/api/web-apps/{app_a["id"]}', tok['admin'], {'source': 'github', 'repository': 'owner/repo'}, method='PATCH', status=403)
    request(f'/api/web-apps/{app_a["id"]}', tok['admin'], {'gitSourceId': upper['id']}, method='PATCH', status=403)
    request(f'/api/web-apps/{app_a["id"]}', tok['admin'], {'source': 'git', 'gitSourceId': upper['id']}, method='PATCH', status=403)
    kept = request(f'/api/web-apps/{app_a["id"]}', tok['admin'], {'repository': 'git:evil.example.com/x', 'name': 'Site A'}, method='PATCH')['app']
    assert kept['repository'] == 'git:srv.example.com/team/site' and kept['gitSourceId'] == sid
    request(f'/api/web-apps/{app_a["id"]}', platform, {'source': 'svn'}, method='PATCH', status=400)
    request(f'/api/web-apps/{app_a["id"]}', platform, {'source': 'git', 'gitSourceId': 'zzzzzzzzzzzzzzz'}, method='PATCH', status=404)
    gh_app = request('/api/web-apps', platform, {'name': 'GitHub app', 'repository': 'owner/repo', 'branch': 'main'}, status=201)['app']
    renamed = request(f'/api/web-apps/{gh_app["id"]}', platform, {'name': 'GitHub app 2'}, method='PATCH')['app']
    assert 'source' not in renamed and 'gitSourceId' not in renamed and renamed['repository'] == 'owner/repo'
    legacy = request('/api/web-apps', platform, {'name': 'Legacy', 'repository': 'legacy/site', 'branch': 'main'}, status=201)['app']
    switched = request(f'/api/web-apps/{legacy["id"]}', platform, {'source': 'git', 'gitSourceId': upper['id'], 'repository': 'forged/repo'}, method='PATCH')['app']
    assert switched['source'] == 'git' and switched['repository'] == 'git:srv.example.com/Team/Site'
    # Exact git: equality: Team/Site cannot join the team/site project.
    assert 'même dépôt' in request(f'/api/web-apps/{legacy["id"]}/project/attach', platform, {'projectId': team['id'], 'environment': 'staging'}, status=400)['message']
    # Same source, different project: refused at project creation, accepted when attaching to its own project.
    twin = request('/api/web-apps', platform, {'name': 'Twin', 'source': 'git', 'gitSourceId': sid, 'branch': 'main', 'autoDeploy': False}, status=201)['app']
    assert 'autre projet' in request(f'/api/web-apps/{twin["id"]}/project', platform, {'name': 'Équipe B', 'environment': 'prod'}, status=400)['message']
    request(f'/api/web-apps/{twin["id"]}/project/attach', platform, {'projectId': team['id'], 'environment': 'staging', 'slug': 'staging'})
    assert request(f'{API}/{sid}', tok['viewer'])['source']['appCount'] == 2
    back = request(f'/api/web-apps/{legacy["id"]}', platform, {'source': 'github', 'repository': 'legacy/site'}, method='PATCH')['app']
    assert 'source' not in back and back['repository'] == 'legacy/site'
    checks.append('source_changes_platform_only_repository_derived_from_source_exact_git_equality_and_single_project')

    # Previews refused for git sources.
    assert 'GitHub App' in request(f'/api/web-projects/{team["id"]}', platform, {'name': 'Équipe A', 'previewEnabled': True, 'previewSourceAppId': app_a['id'], 'previewMax': 1, 'previewTtlHours': 24}, method='PATCH', status=400)['message']
    request(f'/api/web-apps/repositories/1/branches?appId={app_a["id"]}', tok['admin'], status=403)
    checks.append('pull_request_previews_and_github_branch_listing_refused_for_git_sources')

    # 8. Signed webhooks per host; replay; ignored events; unknown id.
    drain()
    url = f'{API}/webhook/{hook_id}'
    app_hook = app_a['id']
    count = lambda: len(request(f'/api/web-apps/{app_hook}/deployments', platform, keep=False)['items'])
    schemes = [
        ('github', lambda raw: {'X-GitHub-Event': 'push', 'X-GitHub-Delivery': secrets.token_hex(8), 'X-Hub-Signature-256': 'sha256=' + sign(secret, raw)}),
        ('gitea', lambda raw: {'X-Gitea-Event': 'push', 'X-Gitea-Delivery': secrets.token_hex(8), 'X-Gitea-Signature': sign(secret, raw)}),
        ('forgejo', lambda raw: {'X-Forgejo-Event': 'push', 'X-Forgejo-Signature': sign(secret, raw)}),
        ('bitbucket', lambda raw: {'X-Event-Key': 'repo:push', 'X-Request-UUID': secrets.token_hex(8), 'X-Hub-Signature': 'sha256=' + sign(secret, raw)}),
        ('gitlab', lambda raw: {'X-Gitlab-Event': 'Push Hook', 'X-Gitlab-Token': secret}),
    ]
    for index, (name, headers_of) in enumerate(schemes):
        sha = format(index + 1, 'x') * 40
        head(p['path'], 'main', sha)
        payload = {'push': {'changes': [{'new': {'type': 'branch', 'name': 'main', 'target': {'hash': sha}}}]}} if name == 'bitbucket' else {'ref': 'refs/heads/main', 'after': sha, 'scheme': name}
        raw = json.dumps(payload).encode()
        before = count()
        result = request(url, raw=raw, headers=headers_of(raw), status=202)
        assert result == {'accepted': True, 'queued': 1}, (name, result)
        assert count() == before + 1
        bad = {**headers_of(raw)}
        for key in list(bad):
            if 'Signature' in key or 'Token' in key: bad[key] = ('sha256=' if bad[key].startswith('sha256=') else '') + '0' * 64
        request(url, raw=raw, headers=bad, status=401)
        if name == 'github':
            replay = request(url, raw=raw, headers=headers_of(raw))
            assert replay == {'accepted': True, 'duplicate': True} and count() == before + 1
    assert request(f'/api/web-apps/{app_hook}', platform, keep=False)['app']['desiredCommit'] == '5' * 40
    for payload, event in [({'ref': 'refs/tags/v1', 'after': 'e' * 40}, 'push'), ({'ref': 'refs/heads/main', 'after': '0' * 40, 'deleted': True}, 'push'), ({'ref': 'refs/heads/other', 'after': 'e' * 40}, 'push'), ({'ref': 'refs/heads/main', 'after': 'e' * 40, 'x': 1}, 'issues')]:
        raw = json.dumps(payload).encode()
        assert request(url, raw=raw, headers={'X-GitHub-Event': event, 'X-Hub-Signature-256': 'sha256=' + sign(secret, raw)}) == {'accepted': True, 'queued': 0}
    request(f'{API}/webhook/' + 'Z' * 24, raw=b'{}', headers={'X-GitHub-Event': 'push'}, status=404)
    request(f'{API}/webhook/short', raw=b'{}', status=404)
    deliveries = request(f'/api/web-apps/{app_hook}/webhooks', tok['viewer'])['items']
    assert deliveries and all(d['repository'] == 'git:srv.example.com/team/site' for d in deliveries)
    assert all(d['deliveryId'].startswith('git-' + sid + '-') and hook_id not in d['deliveryId'] for d in deliveries)
    assert any(d['duplicateCount'] == 1 for d in deliveries)
    checks.append('signed_push_from_github_gitea_forgejo_bitbucket_gitlab_queues_replay_deduplicates_and_others_ignored')

    # Webhook cap: 20 deliveries per minute and per source reach ls-remote, the 21st is 429.
    cap = create('git@github.com:hook/cap.git')['source']
    head(cap['path'], 'main', 'f' * 40)
    request('/api/web-apps', platform, {'name': 'Cap', 'source': 'git', 'gitSourceId': cap['id'], 'branch': 'main'}, status=201)
    cap_secret = request(f'{API}/{cap["id"]}/rotate-webhook-secret', platform, {})
    for index in range(21):
        raw = json.dumps({'ref': 'refs/heads/main', 'after': 'f' * 40, 'n': index}).encode()
        request(cap_secret['url'].replace('https://app.example.invalid', ''), raw=raw, headers={'X-GitHub-Event': 'push', 'X-Hub-Signature-256': 'sha256=' + sign(cap_secret['secret'], raw)}, status=(200, 202) if index < 20 else 429)
    disabled = request(f'{API}/{cap["id"]}/rotate-webhook-secret', platform, {'disable': True})['source']
    assert disabled['webhook']['configured'] is False
    raw = json.dumps({'ref': 'refs/heads/main', 'after': 'f' * 40, 'n': 'disabled'}).encode()
    request(cap_secret['url'].replace('https://app.example.invalid', ''), raw=raw, headers={'X-GitHub-Event': 'push', 'X-Hub-Signature-256': 'sha256=' + sign(cap_secret['secret'], raw)}, status=401)
    checks.append('twenty_first_webhook_delivery_per_minute_is_429_and_disabled_secret_refuses_deliveries')

    # 11. A GitHub App push never queues a git-sourced app, even with an identical repository string.
    gh_secret = 'webhook-secret-test-' + secrets.token_urlsafe(32)
    request('/api/web-apps/settings', platform, {'githubAppId': '123', 'githubAppSlug': 'example-app', 'githubInstallationId': '456', 'webhookSecret': gh_secret}, method='PUT')
    disguised = request('/api/web-apps', platform, {'name': 'Disguised', 'source': 'git', 'gitSourceId': gh['id'], 'branch': 'main'}, status=201)['app']
    raw_row = request(f'/api/collections/web_apps/records/{disguised["id"]}', su, keep=False)
    request(f'/api/collections/web_apps/records/{disguised["id"]}', su, {'config': {**raw_row['config'], 'repository': 'owner/repo'}}, method='PATCH', keep=False)
    drain()
    raw = json.dumps({'ref': 'refs/heads/main', 'after': '9' * 40, 'repository': {'full_name': 'owner/repo'}, 'installation': {'id': 456}}).encode()
    result = request('/api/web-apps/github/webhook', raw=raw, headers={'X-GitHub-Event': 'push', 'X-GitHub-Delivery': 'gh-delivery-1', 'X-Hub-Signature-256': 'sha256=' + sign(gh_secret, raw)}, status=202)
    assert result['queued'] == 1
    queued = drain()
    assert [j['appId'] for j in queued] == [gh_app['id']], queued
    checks.append('github_app_push_never_queues_a_git_sourced_app')

    # 9. Claim: git apps get job.git (current key, pinned keys) and never job.github; GitHub apps unchanged.
    head(p['path'], 'main', '7' * 40)
    request(f'/api/web-apps/{app_a["id"]}/deploy', tok['dev'], {}, status=202)
    job = request('/api/web-apps/_worker/claim', body={}, headers=worker, keep=False)['job']
    assert job['appId'] == app_a['id'] and job['commit'] == '7' * 40 and 'github' not in job
    git = job['git']
    assert set(git) == {'host', 'port', 'sshUser', 'path', 'privateKey', 'hostKeys', 'commit', 'branch'}
    assert git['host'] == 'srv.example.com' and git['port'] == 22 and isinstance(git['port'], int) and git['sshUser'] == 'git' and git['path'] == 'team/site.git'
    assert git['commit'] == '7' * 40 and git['branch'] == 'main' and git['privateKey'].startswith('-----BEGIN OPENSSH PRIVATE KEY-----')
    assert all(set(k) == {'type', 'key'} for k in git['hostKeys']) and sorted(k['key'] for k in git['hostKeys']) == sorted(k['key'] for k in control_state['scan']['*'])
    assert job['snapshot']['config']['source'] == 'git' and 'privateKey' not in json.dumps(job['snapshot'])
    request('/api/web-apps/_worker/report', body={'id': job['id'], 'status': 'failed', 'error': 'Fixture'}, headers=worker, keep=False)
    request(f'/api/web-apps/{gh_app["id"]}/deploy', platform, {}, status=202)
    job = request('/api/web-apps/_worker/claim', body={}, headers=worker, keep=False)['job']
    assert job['appId'] == gh_app['id'] and 'git' not in job and set(job['github']) == {'githubAppId', 'githubInstallationId', 'privateKey'}
    request('/api/web-apps/_worker/report', body={'id': job['id'], 'status': 'failed', 'error': 'Fixture'}, headers=worker, keep=False)
    # Retry of a git deploy stays git and reads the current key.
    failed = next(d for d in request(f'/api/web-apps/{app_a["id"]}/deployments', platform, keep=False)['items'] if d['commit'] == '7' * 40)
    request(f'/api/web-apps/{app_a["id"]}/deployments/{failed["id"]}/retry', tok['dev'], {}, status=202)
    job = request('/api/web-apps/_worker/claim', body={}, headers=worker, keep=False)['job']
    assert job['git']['commit'] == '7' * 40 and 'github' not in job
    request('/api/web-apps/_worker/report', body={'id': job['id'], 'status': 'failed', 'error': 'Fixture'}, headers=worker, keep=False)
    # Unconfirmed source between queue and claim: the job fails in the claim transaction with a French message.
    request(f'/api/web-apps/{app_a["id"]}/deploy', tok['dev'], {}, status=202)
    request(f'/api/collections/web_git_sources/records/{sid}', su, {'hostKeysConfirmedAt': ''}, method='PATCH', keep=False)
    assert request('/api/web-apps/_worker/claim', body={}, headers=worker, keep=False)['job'] is None
    latest = request(f'/api/web-apps/{app_a["id"]}/deployments', platform, keep=False)['items'][0]
    assert latest['status'] == 'failed' and latest['error'] == 'Source Git indisponible : confirmez l’empreinte et la clé de déploiement.'
    assert 'Confirmez' in request(f'/api/web-apps/{app_a["id"]}/deploy', tok['dev'], {}, status=400)['message']
    checks.append('claim_sends_job_git_only_for_git_apps_github_unchanged_retry_stays_git_and_unready_source_fails_job')

    # Capability exposed to the dashboard.
    capabilities = request(f'/api/web-apps/{app_a["id"]}', tok['viewer'])['capabilities']
    assert capabilities['gitAvailable'] is False
    checks.append('app_detail_exposes_git_worker_capability')

    claim = lambda: request('/api/web-apps/_worker/claim', body={}, headers=worker, keep=False)['job']
    fail = lambda job: request('/api/web-apps/_worker/report', body={'id': job['id'], 'status': 'failed', 'error': 'Fixture'}, headers=worker, keep=False)

    # Deploy head reads (ls-remote with the deploy key, up to 45 s each) are capped per source: the 13th in a minute is 429.
    drain()
    limited = create('git@github.com:resolve/limit.git')['source']
    head(limited['path'], 'main', '8' * 40)
    limited_app = request('/api/web-apps', platform, {'name': 'Resolve limit', 'source': 'git', 'gitSourceId': limited['id'], 'branch': 'main', 'autoDeploy': False}, status=201)['app']
    for _ in range(12):
        request(f'/api/web-apps/{limited_app["id"]}/deploy', platform, {}, status=202)
    assert 'Trop de contrôles Git' in request(f'/api/web-apps/{limited_app["id"]}/deploy', platform, {}, status=429)['message']
    assert len([c for c in reader_calls() if c['action'] == 'ls-remote' and c['mode'] == 'resolve' and (c['remote'] or {}).get('path') == limited['path']]) == 12
    drain()
    checks.append('thirteenth_deploy_head_read_per_minute_and_source_is_429')

    # Clone, build-mode promotion, start and rollback of git apps. The worker's branch fallback (server refusing a fetch by
    # commit) must be the branch the promoted commit came from, never the target's branch.
    request(f'/api/collections/web_git_sources/records/{sid}', su, {'hostKeysConfirmedAt': '2026-01-01 00:00:00.000Z'}, method='PATCH', keep=False)
    dev_env = request(f'/api/web-apps/{app_a["id"]}/environments/clone', tok['admin'], {'environment': 'dev', 'branch': 'develop'}, status=201)['app']
    assert dev_env['source'] == 'git' and dev_env['gitSourceId'] == sid and dev_env['branch'] == 'develop' and dev_env['repository'] == 'git:srv.example.com/team/site'
    assert request(f'{API}/{sid}', tok['viewer'])['source']['appCount'] == 3
    head(p['path'], 'develop', '6' * 40)
    request(f'/api/web-apps/{dev_env["id"]}/deploy', tok['dev'], {}, status=202)
    job = claim()
    assert job['appId'] == dev_env['id'] and job['git']['branch'] == 'develop' and job['git']['commit'] == '6' * 40
    request('/api/web-apps/_worker/report', body={'id': job['id'], 'status': 'ready', 'result': {'image': f'ph-web/{dev_env["id"]}:{job["id"]}'}}, headers=worker, keep=False)
    published = job['id']
    head(p['path'], 'develop', '4' * 40)  # develop moves on after the publication
    preview = request(f'/api/web-apps/{dev_env["id"]}/environments/promotion/preview', tok['dev'], {'targetAppId': app_a['id']})
    assert preview['mode'] == 'build' and preview['commit'] == '6' * 40
    request(f'/api/web-apps/{dev_env["id"]}/environments/promote', tok['dev'], preview, status=202)
    job = claim()
    assert job['appId'] == app_a['id'] and job['action'] == 'deploy' and job['git']['commit'] == '6' * 40 and 'github' not in job
    assert job['git']['branch'] == 'develop', job['git']['branch']
    fail(job)
    for action, body in [('start', {}), ('rollback', {'deploymentId': published})]:
        request(f'/api/web-apps/{dev_env["id"]}/{action}', tok['dev'], body, status=202)
        job = claim()
        assert job['action'] == action and 'git' not in job and 'github' not in job, (action, sorted(job))
        fail(job)
    checks.append('clone_keeps_git_source_build_promotion_falls_back_to_source_branch_start_and_rollback_get_no_credentials')

    # Detach / re-attach. A project never keeps control of a key that an app outside it uses, and the binding follows the
    # apps (a detached app can join another project).
    drain()
    request(f'/api/web-apps/{twin["id"]}/project', platform, method='DELETE')
    assert request(f'{API}/{sid}', platform)['source']['projectId'] == team['id']  # app_a and the dev clone still use it
    for action, body in [('rotate-key', {'confirm': True}), ('rotate-webhook-secret', {'disable': True}), ('scan', {}), ('test', {})]:
        assert 'hors de ce projet' in request(f'{API}/{sid}/{action}', tok['admin'], body, status=403)['message'], action
    request(f'{API}/{sid}', tok['viewer'], status=403)
    request(f'{API}/{sid}/branches', tok['owner'], status=403)
    assert request(f'{API}/{sid}', platform)['source']['webhook']['configured']  # nothing was disabled
    assert 'autre projet' in request(f'/api/web-apps/{twin["id"]}/project', platform, {'name': 'Équipe B', 'environment': 'prod'}, status=400)['message']
    for app_id in [dev_env['id'], app_a['id']]:
        request(f'/api/web-apps/{app_id}/project', platform, method='DELETE')
    assert request(f'{API}/{sid}', platform)['source']['projectId'] == ''
    for name in ['owner', 'admin', 'viewer']:
        request(f'{API}/{sid}', tok[name], status=404)
    # A binding left behind with no app of that project (older version) does not block another project either.
    request(f'/api/collections/web_git_sources/records/{sid}', su, {'projectId': team['id']}, method='PATCH', keep=False)
    second_team = request(f'/api/web-apps/{twin["id"]}/project', platform, {'name': 'Équipe B', 'environment': 'prod'}, status=201)['project']
    assert request(f'{API}/{sid}', platform)['source']['projectId'] == second_team['id']
    request(f'/api/web-apps/{twin["id"]}/project', platform, method='DELETE')
    assert request(f'{API}/{sid}', platform)['source']['projectId'] == ''
    request(f'/api/web-apps/{twin["id"]}/project/attach', platform, {'projectId': team['id'], 'environment': 'staging', 'slug': 'staging'})
    assert request(f'{API}/{sid}', platform)['source']['projectId'] == team['id']
    # app_a and the dev clone are legacy apps on the same key: the project admin still cannot rotate it.
    assert 'hors de ce projet' in request(f'{API}/{sid}/rotate-key', tok['admin'], {'confirm': True}, status=403)['message']
    for app_id in [app_a['id'], dev_env['id']]:
        request(f'/api/web-apps/{app_id}/project/attach', platform, {'projectId': team['id'], 'environment': 'prod' if app_id == app_a['id'] else 'dev', 'slug': 'prod' if app_id == app_a['id'] else 'dev', 'keepVariables': True})
    assert request(f'{API}/{sid}', tok['viewer'])['source']['appCount'] == 3
    request(f'{API}/{sid}/rotate-key', tok['admin'], {}, status=400)  # back in the project: only the usual confirmation
    checks.append('detach_releases_or_keeps_the_source_binding_reattach_rebinds_and_foreign_apps_make_the_source_platform_only')

    # 2. Encrypted at rest: no private key, secret or pending scan in plaintext anywhere in the database.
    pending_only = fake_key('pending-only')
    control(scan={**control_state['scan'], '*': [pending_only]})
    leftover = request(f'{API}/{relative["id"]}/scan', platform, {})  # leaves a pending TOFU scan
    assert leftover['nonce']
    blob = b''.join(path.read_bytes() for path in data.glob('data.db*'))
    for needle in [b'OPENSSH', b'PRIVATE KEY', secret.encode(), cap_secret['secret'].encode(), pending_only['key'].encode(), leftover['nonce'].encode()]:
        assert needle not in blob, needle
    with sqlite3.connect(f'file:{data / "data.db"}?mode=ro', uri=True) as db:
        rows = db.execute('select id, privateKeyEncrypted, pendingHostKeysEncrypted, webhookSecretEncrypted from web_git_sources').fetchall()
    assert rows and all(r[1] for r in rows)
    for ident, private, pending_value, webhook_value in rows:
        assert decrypt(private)['privateKey'].startswith('-----BEGIN OPENSSH PRIVATE KEY-----')
        if ident == sid: assert decrypt(webhook_value) == {'secret': secret}
        if ident == relative['id']: assert [k['key'] for k in decrypt(pending_value)['keys']] == [pending_only['key']]
    checks.append('private_key_webhook_secret_and_pending_scan_are_ciphertext_at_rest')

    # Private material never leaves the API except the one-time secret reveal.
    for text in responses:
        assert 'PRIVATE KEY' not in text and 'privateKeyEncrypted' not in text and 'pendingHostKeys' not in text and 'webhookSecretEncrypted' not in text, text[:300]
        if '"secret"' not in text: assert secret not in text and cap_secret['secret'] not in text
    checks.append('no_route_returns_private_key_ciphertext_or_secret_after_reveal')
    print(json.dumps({'passed': True, 'checks': checks}))
except Exception:
    if (root / 'process.log').exists(): print((root / 'process.log').read_text()[-4000:])
    raise
finally:
    if process:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
    temporary.cleanup()
