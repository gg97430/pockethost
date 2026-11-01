#!/usr/bin/env python3
"""Disposable PocketBase: per-tenant caps (environments, sites, queue, terminals), GitHub/DNS rates, variable oracle, viewer leaks, private registries."""
import argparse, hashlib, hmac, json, os, pathlib, secrets, shutil, socket, sqlite3, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True, type=pathlib.Path)
parser.add_argument('--pocketbase', required=True)
args = parser.parse_args()
os.umask(0o077)
temporary = tempfile.TemporaryDirectory(prefix='ph-web-limits-')
root = pathlib.Path(temporary.name).resolve()
process = None
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
origin = f'http://127.0.0.1:{port}'
worker = 'W' + secrets.token_urlsafe(40)
wh = {'X-Web-Worker': worker}
IMAGE = 'ghcr.io/team/site@sha256:' + 'a' * 64
UNAVAILABLE = 'Ce domaine n’est pas disponible.'
SITE_MAX = 80
HEAD = 'd' * 40
checks = []


def request(path, token='', body=None, method=None, status=200, headers=None):
    value = urllib.request.Request(origin + path, method=method or ('POST' if body is not None else 'GET'), headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {}), **(headers or {})}, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(value, timeout=25) as response:
            assert response.status == status, (path, response.status, status)
            data = response.read(); return json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        data = error.read()
        if error.code == status: return json.loads(data) if data else None
        raise AssertionError(f'{method or ""} {path}: {error.code} != {status}: ' + data.decode()) from None


def code_of(path, token, body):
    try:
        with urllib.request.urlopen(urllib.request.Request(origin + path, method='POST', headers={'Content-Type': 'application/json', 'Authorization': token}, data=json.dumps(body).encode()), timeout=25) as response: return response.status
    except urllib.error.HTTPError as error: return error.code


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks = root / 'hooks'; hooks.mkdir()
    shutil.copy(source / 'pb_hooks/mothership.js', hooks / 'mothership.js'); shutil.copy(source / 'src/lib/handlers/webApps/hooks.ts', hooks / 'web.pb.js')
    # python3 shim: the GitHub reader is a fixture; every call is counted (each one is a blocking process in production).
    tools = root / 'tools'; tools.mkdir(); calls = root / 'github-calls.log'
    replies = {'commit': {'sha': HEAD}, 'branches': {'items': [], 'repository': 'team-a/site'}, 'pull-request': {'state': 'open', 'draft': False, 'author_association': 'MEMBER', 'head': {'sha': 'e' * 40, 'ref': 'feature', 'repo': {'id': 77, 'full_name': 'team-a/site'}}, 'base': {'repo': {'id': 77, 'full_name': 'team-a/site'}}}}
    shim = tools / 'python3'
    shim.write_text('#!' + sys.executable + '\nimport sys,json,pathlib,os\np=pathlib.Path(sys.argv[-1])\ntry:d=json.loads(p.read_text())\nexcept Exception:d={}\nr=' + repr(replies) + '\nif isinstance(d,dict) and d.get("action") in r:\n open(' + repr(str(calls)) + ',"a").write(d["action"]+"\\n");print(json.dumps(r[d["action"]]));sys.exit()\nos.execv(' + repr(sys.executable) + ',[' + repr(sys.executable) + ']+sys.argv[1:])\n'); shim.chmod(0o700)
    domain_helper = root / 'domain-helper.py'; domain_helper.write_text('import json\nprint(json.dumps({"verified": False, "error": "Enregistrement absent."}))\n')
    env = {**os.environ, 'PATH': str(tools) + os.pathsep + os.environ.get('PATH', ''), 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_MAX_SITES': str(SITE_MAX), 'WEB_APPS_MASTER_KEY': 'l' * 32, 'WEB_APPS_WORKER_SECRET': worker, 'WEB_APPS_ROOT': str(root / 'web'), 'DATA_ROOT': str(root / 'data'), 'APEX_DOMAIN': 'example.invalid', 'APP_HOST': 'app.example.invalid', 'WEB_APPS_PUBLIC_IP': '203.0.113.10', 'WEB_APPS_DOMAIN_HELPER': str(domain_helper)}
    (root / 'web').mkdir(exist_ok=True)
    (root / 'web/worker-status.json').write_text(json.dumps({'httpsReady': True}))
    password, email = 'P' + secrets.token_urlsafe(32), 'su@example.invalid'
    data = root / 'pb_data'
    subprocess.run([args.pocketbase, 'superuser', 'upsert', email, password, '--dir', str(data), '--hooksDir', str(root / 'empty-hooks'), '--migrationsDir', str(source / 'pb_migrations')], env=env, check=True, capture_output=True)
    log = (root / 'process.log').open('wb')
    process = subprocess.Popen([args.pocketbase, 'serve', '--dir', str(data), '--hooksDir', str(hooks), '--migrationsDir', str(source / 'pb_migrations'), '--http', f'127.0.0.1:{port}'], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        if process.poll() is not None: raise RuntimeError('PocketBase failed to start: ' + (root / 'process.log').read_text()[-2000:])
        try: request('/api/health'); break
        except OSError: time.sleep(.1)
    su = request('/api/collections/_superusers/auth-with-password', body={'identity': email, 'password': password})['token']
    database = data / 'data.db'

    def user(name, admin=False):
        row = request('/api/collections/users/records', su, {'email': name + '@example.invalid', 'username': name, 'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': admin, 'subscription': 'premium', 'subscription_quantity': 1})
        return row['id'], request('/api/collections/users/auth-with-password', body={'identity': row['email'], 'password': password})['token']
    platform_id, platform = user('platform', True)
    ids, tok = {}, {}
    for name in ['owner', 'admin', 'dev', 'dev2', 'viewer', 'owner_b', 'dev_b', 'r_dev1', 'r_dev2', 'r_admin', 'r_admin2', 'r_dev3', 'r_dev4']:
        ids[name], tok[name] = user(name)

    def site(name, repository='', image=False):
        body = {'name': name, 'branch': 'main', **({'repository': repository, 'repositoryId': 77} if repository else {}), **({'runtime': {'kind': 'image', 'image': IMAGE}} if image else {})}
        return request('/api/web-apps', platform, body, status=201)['app']['id']

    def project(app, name, environment='prod'):
        return request(f'/api/web-apps/{app}/project', platform, {'name': name, 'environment': environment}, status=201)['project']['id']

    def attach(app, project_id, slug, environment='dev', status=200):
        return request(f'/api/web-apps/{app}/project/attach', platform, {'projectId': project_id, 'environment': environment, 'slug': slug}, status=status)

    def member(project_id, name, role): request(f'/api/web-projects/{project_id}/members', platform, {'email': name + '@example.invalid', 'role': role}, status=201)

    def claim(): return request('/api/web-apps/_worker/claim', body={}, headers=wh)['job']

    def finish(job, status='failed', result=None):
        request('/api/web-apps/_worker/report', body={'id': job['id'], 'status': status, **({'result': result} if result else {'error': 'Fixture'})}, headers=wh)

    def drain():
        served = []
        for _ in range(200):
            job = claim()
            if not job: return served
            served.append(job['appId']); finish(job)
        raise AssertionError('queue did not drain')

    def github_calls(action): return calls.read_text().split().count(action) if calls.exists() else 0

    # ---------------------------------------------------------------- 3. Queue: per-project cap and fairness.
    q1, q2, q3 = site('Q1', image=True), site('Q2', image=True), site('Q3', image=True)
    team_a, team_b = project(q1, 'Équipe A'), project(q3, 'Équipe B')
    attach(q2, team_a, 'q2')
    request(f'/api/web-projects/{team_a}', platform, {'limits': {'resources': {'enabled': True, 'queuedBuilds': 3}}}, method='PATCH')
    for name, role in [('owner', 'owner'), ('admin', 'admin'), ('dev', 'developer'), ('dev2', 'developer'), ('viewer', 'viewer')]: member(team_a, name, role)
    for name, role in [('owner_b', 'owner'), ('dev_b', 'developer')]: member(team_b, name, role)
    stop = lambda app, who='dev', status=202: request(f'/api/web-apps/{app}/stop', tok[who], {}, status=status)
    queued = request(f'/api/web-apps/{q1}/deploy', tok['dev'], {}, status=202)['deployment']['id']
    stop(q1); stop(q1)
    full = stop(q1, status=429); assert full['status'] == 429 and 'en attente pour ce projet' in full['message'], full
    stop(q2, status=429)
    stop(q3, 'dev_b')
    request(f'/api/web-apps/{q1}/deployments/{queued}/cancel', tok['dev'], {}, status=202)
    stop(q2)
    stop(q2, status=429)
    drain()
    # A deployment replaces this application's queued deployments: it does not count against itself, even at the cap.
    request(f'/api/web-apps/{q1}/deploy', tok['dev'], {}, status=202); stop(q2); stop(q2)
    replaced = request(f'/api/web-apps/{q1}/deploy', tok['dev'], {}, status=202)['deployment']['id']
    request(f'/api/web-apps/{q2}/deploy', tok['dev'], {}, status=429)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM web_deployments WHERE status='queued' AND appId IN (?,?)", (q1, q2)).fetchone()[0] == 3
        assert db.execute('SELECT status FROM web_deployments WHERE id=?', (replaced,)).fetchone()[0] == 'queued'
    drain()
    # Simultaneous requests: the cap is read again in the write transaction.
    results = []
    threads = [threading.Thread(target=lambda: results.append(code_of(f'/api/web-apps/{q1}/stop', tok['dev'], {}))) for _ in range(12)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sorted(set(results)) == [202, 429] and results.count(202) == 3, results
    drain()
    checks.append('queue_cap_per_project_429_cancel_frees_a_slot_deploy_replaces_and_concurrent_requests_stay_capped')
    f1, f2, f3 = site('F1', image=True), site('F2', image=True), site('F3', image=True)
    attach(f1, team_a, 'f1'); attach(f3, team_a, 'f3'); attach(f2, team_b, 'f2')
    for _ in range(3): stop(f1)
    stop(f2, 'dev_b')
    order = []
    for _ in range(4):
        job = claim(); order.append(job['appId']); finish(job)
    assert order == [f1, f2, f1, f1], order
    assert claim() is None
    # One tenant only: strict FIFO, the never-served application waits its turn.
    stop(f1); stop(f1); stop(f3)
    order = []
    for _ in range(3):
        job = claim(); order.append(job['appId']); finish(job)
    assert order == [f1, f1, f3], order
    # Two tenants: the application served last waits, even with an older job.
    stop(f1); stop(f1); stop(f2, 'dev_b')
    job = claim(); assert job['appId'] == f2, (job['appId'], f1, f2); finish(job)
    drain()
    checks.append('claim_round_robin_between_tenants_and_strict_fifo_for_one_tenant')

    # ---------------------------------------------------------------- 1. Permanent environments per project and sites per server.
    c1 = site('Cap', 'team-c/site')
    capped = project(c1, 'Équipe C')
    member(capped, 'owner_b', 'owner')
    clone = lambda slug, status=201: request(f'/api/web-apps/{c1}/environments/clone', tok['owner_b'], {'environment': 'dev', 'slug': slug}, status=status)
    for index in range(9): clone(f'e{index}')
    assert clone('e9', 400)['message'] == 'Dix environnements permanents maximum par projet.'
    extra = site('Extra', 'team-c/site')
    assert attach(extra, capped, 'extra', status=400)['message'] == 'Dix environnements permanents maximum par projet.'
    elsewhere = site('Elsewhere', 'team-c/site')
    other_team = project(elsewhere, 'Équipe D')
    assert request(f'/api/web-apps/{elsewhere}/project', platform, {'projectId': capped, 'environment': 'dev', 'slug': 'moved'}, method='PUT', status=400)['message'] == 'Dix environnements permanents maximum par projet.'
    assert request(f'/api/web-apps/{elsewhere}/project', platform, {'projectId': team_a}, method='PUT', status=400)['message'] != 'Dix environnements permanents maximum par projet.'
    checks.append('ten_permanent_environments_per_project_on_clone_attach_and_move')

    # ---------------------------------------------------------------- 2. Synchronous GitHub calls: per application AND per actor.
    r1, r2, r3, r4, r5, r6 = (site(f'R{index}', 'team-a/site') for index in range(1, 7))
    team_r = project(r1, 'Équipe R')
    for index, app in enumerate([r2, r3, r4, r5, r6]): attach(app, team_r, f'r{index}', 'staging')
    for name, role in [('r_dev1', 'developer'), ('r_dev2', 'developer'), ('r_dev3', 'developer'), ('r_dev4', 'developer'), ('r_admin', 'admin'), ('r_admin2', 'admin')]: member(team_r, name, role)
    deploy = lambda app, who, status=202: request(f'/api/web-apps/{app}/deploy', tok[who], {}, status=status)
    for _ in range(12): deploy(r1, 'r_dev1')
    assert github_calls('commit') == 12
    limited = deploy(r1, 'r_dev1', 429); assert limited['status'] == 429 and limited['message'] == 'Trop de demandes pour cette application. Réessayez dans une minute.', limited
    deploy(r1, 'r_dev2', 429)  # Application at its cap.
    deploy(r2, 'r_dev1', 429)  # Actor at their cap.
    assert github_calls('commit') == 12
    deploy(r2, 'r_dev2')
    drain()
    branches = f'/api/web-apps/repositories/77/branches?appId={r3}'
    for _ in range(12): request(branches, tok['r_admin'])
    request(branches, tok['r_admin'], status=429)
    assert github_calls('branches') == 12
    request(f'/api/web-projects/{team_r}', platform, {'name': 'Équipe R', 'previewEnabled': True, 'previewSourceAppId': r1, 'previewMax': 3, 'previewTtlHours': 1}, method='PATCH')
    refresh = f'/api/web-projects/{team_r}/previews/5/refresh'
    for _ in range(12): request(refresh, tok['r_dev3'], {}, status=202)
    request(refresh, tok['r_dev3'], {}, status=429)
    assert github_calls('pull-request') == 12
    drain()
    token_url = f'/api/web-apps/{r4}/deploy-tokens'
    profile = request(f'/api/web-apps/{r4}/runtime/profile', tok['r_admin2'])
    request(f'/api/web-apps/{r4}/runtime/profile', tok['r_admin2'], {'profile': {'kind': 'image', 'image': IMAGE}, 'revision': profile['revision']}, method='PATCH')
    ci = 'Bearer ' + request(token_url, tok['r_admin2'], {'name': 'ci', 'imageRepository': 'ghcr.io/team/site'}, status=201)['token']
    ci_body = lambda index: {'image': 'ghcr.io/team/site@sha256:' + 'c' * 64, 'commit': HEAD, 'requestId': f'run-{index}'}
    before = github_calls('commit')
    for index in range(12): request('/api/web-apps/ci/deploy', ci, ci_body(index), status=202)
    assert github_calls('commit') == before + 12
    request('/api/web-apps/ci/deploy', ci, ci_body(99), status=429)
    assert github_calls('commit') == before + 12
    drain()
    promote = f'/api/web-apps/{r5}/environments/promote'
    for _ in range(12): request(promote, tok['r_dev4'], {'targetAppId': r6}, status=400)
    request(promote, tok['r_dev4'], {'targetAppId': r6}, status=429)
    checks.append('deploy_branches_preview_refresh_ci_and_promotion_capped_per_app_and_actor_before_any_github_process')

    # ---------------------------------------------------------------- 4. Domain verification and 9. uniform availability message.
    d1 = q1
    domains = f'/api/web-apps/{d1}/domains'
    shop = request(domains, tok['admin'], {'hostname': 'shop.example.org'}, status=201)['domain']
    for _ in range(6): assert request(f'{domains}/{shop["id"]}/verify', tok['admin'], {}, status=400)['message'] == 'Enregistrement absent.'
    limited = request(f'{domains}/{shop["id"]}/verify', tok['admin'], {}, status=429); assert limited['message'] == 'Trop de vérifications DNS pour cette application. Réessayez dans une minute.'
    checks.append('domain_verification_six_per_minute_per_application')
    request('/api/collections/web_app_domains/records', su, {'appId': q3, 'host': 'claimed.example.org', 'status': 'active', 'ownershipToken': 'z' * 40})
    request('/api/collections/instances/records', su, {'uid': platform_id, 'subdomain': 'pbdb', 'cname': 'instance.example.org', 'status': 'idle', 'version': '0.39.*'})
    for host in ['claimed.example.org', 'instance.example.org', 'pbdb.example.invalid', 'anything.example.invalid', 'example.invalid']:
        assert request(domains, tok['admin'], {'hostname': host}, status=400)['message'] == UNAVAILABLE, host
    checks.append('assert_free_answers_one_message_whoever_holds_the_name')

    # Optional project caps also cover signed pushes; cleanup and route removal must remain possible.
    request(f'/api/web-projects/{team_r}', platform, {'limits': {'resources': {'enabled': True, 'queuedBuilds': 3}}}, method='PATCH')
    for _ in range(3): stop(r6, 'r_dev3')
    stop(r6, 'r_dev3', 429)
    secret = 'webhook-secret-' + secrets.token_urlsafe(32)
    request('/api/web-apps/settings', platform, {'githubAppId': '123', 'githubAppSlug': 'app', 'githubInstallationId': '456', 'webhookSecret': secret}, method='PUT')
    pushed = {'ref': 'refs/heads/main', 'after': 'f' * 40, 'repository': {'full_name': 'team-a/site'}, 'installation': {'id': 456}}
    signature = 'sha256=' + hmac.new(secret.encode(), json.dumps(pushed).encode(), hashlib.sha256).hexdigest()
    assert 'en attente pour ce projet' in request('/api/web-apps/github/webhook', body=pushed, headers={'X-GitHub-Delivery': 'push-at-cap', 'X-GitHub-Event': 'push', 'X-Hub-Signature-256': signature}, status=429)['message']
    with sqlite3.connect(database) as db:
        preview_row = db.execute("SELECT id FROM web_app_environments WHERE projectId=? AND environment='preview'", (team_r,)).fetchone()[0]
    request(f'/api/web-projects/{team_r}/previews/{preview_row}/close', tok['r_dev3'], {}, status=202)
    drain()
    routed = request('/api/collections/web_app_domains/records', su, {'appId': q2, 'host': 'routed.example.org', 'status': 'active', 'ownershipToken': 'y' * 40})
    for _ in range(3): stop(q1)
    stop(q1, status=429)
    # At the cap a verification is refused before the DNS call and before the domain changes state.
    assert 'en attente pour ce projet' in request(f'{domains}/{shop["id"]}/verify', tok['admin'], {}, status=429)['message']
    request(f'/api/web-apps/{q2}/domains/{routed["id"]}', tok['admin'], method='DELETE')
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM web_deployments WHERE appId=? AND action='domains-sync' AND status='queued'", (q2,)).fetchone()[0] == 1
    drain()
    checks.append('signed_pushes_respect_optional_queue_cap_and_cleanup_bypasses_it')

    # ---------------------------------------------------------------- 5. No oracle on values for roles without variables.reveal.
    v1, v2 = site('V1', image=True), site('V2', image=True)
    attach(v1, team_a, 'v1'); attach(v2, team_a, 'v2')
    env_url = lambda app: f'/api/web-apps/{app}/environment'

    def write(app, who, text):
        current = request(env_url(app), tok[who])
        body = {'revision': current['revision'], 'text': text, 'mode': 'merge'}
        plan = request(env_url(app) + '/preview', tok[who], body)
        return request(env_url(app), tok[who], {**body, 'previewToken': plan['previewToken']}, method='PUT')

    def publish(app):
        request(f'/api/web-apps/{app}/deploy', tok['dev'], {}, status=202)
        job = claim(); assert job['appId'] == app
        finish(job, 'ready', {'image': 'sha256:' + 'b' * 64, 'target': 'http://127.0.0.1:12345'})
    keys = ['pendingChanges', 'requiresRebuild', 'runtimeOnlyChanges', 'canApplyRuntime']
    state = lambda app, who: {key: request(env_url(app), tok[who])[key] for key in keys}
    clean = dict.fromkeys(keys, False)
    # Build variable: same answer for the deployed value and for another one.
    write(v1, 'admin', '# pockethost: build=true runtime=true\nAPI_SECRET=the-real-secret\n')
    publish(v1)
    assert state(v1, 'dev') == clean and state(v1, 'viewer') == clean and state(v1, 'admin') == clean
    write(v1, 'dev', 'API_SECRET=the-real-secret\n')
    same = state(v1, 'dev'), state(v1, 'viewer')
    write(v1, 'dev', 'API_SECRET=a-wrong-guess\n')
    assert (state(v1, 'dev'), state(v1, 'viewer')) == same, same
    assert same[0] == {'pendingChanges': True, 'requiresRebuild': True, 'runtimeOnlyChanges': False, 'canApplyRuntime': False}, same
    write(v1, 'admin', 'API_SECRET=the-real-secret\n')
    assert state(v1, 'admin') == clean  # A role with variables.reveal keeps the exact comparison.
    assert state(v1, 'dev') == same[0]
    # The apply route confirmed a value too: same refusal whatever the value, for this role.
    assert 'Aucune modification' in request(f'/api/web-apps/{v1}/apply-runtime', tok['dev'], {}, status=400)['message']
    # Runtime variables only: the shortcut stays available, whatever the value.
    write(v2, 'admin', '# pockethost: build=false runtime=true\nAPI_SECRET=the-real-secret\n')
    publish(v2)
    assert state(v2, 'dev') == clean
    write(v2, 'dev', 'API_SECRET=the-real-secret\n')
    runtime_only = {'pendingChanges': True, 'requiresRebuild': False, 'runtimeOnlyChanges': True, 'canApplyRuntime': True}
    assert state(v2, 'dev') == runtime_only
    write(v2, 'dev', 'API_SECRET=a-wrong-guess\n')
    assert state(v2, 'dev') == runtime_only
    request(f'/api/web-apps/{v2}/apply-runtime', tok['dev'], {}, status=202)
    drain()
    checks.append('deployment_state_and_apply_route_do_not_depend_on_values_without_variables_reveal')

    # ---------------------------------------------------------------- 6. Viewer leaks.
    tasks = f'/api/web-apps/{q1}/tasks'
    request(tasks, tok['admin'], {'name': 'Purge', 'command': 'curl -H "Key: super-secret" https://x', 'schedule': '0 * * * *', 'enabled': False, 'timeoutSeconds': 60, 'timezone': 'UTC'}, status=201)
    assert request(tasks, tok['admin'])['items'][0]['command'].startswith('curl')
    for who in ['viewer', 'dev']:
        listed = request(tasks, tok[who]); assert 'command' not in listed['items'][0] and 'super-secret' not in json.dumps(listed) and listed['items'][0]['name'] == 'Purge'
    runtime = f'/api/web-apps/{q1}/runtime/profile'
    current = request(runtime, tok['admin'])
    request(runtime, tok['admin'], {'profile': current['profile'], 'revision': current['revision'], 'registry': {'server': 'ghcr.io', 'username': 'bot-account', 'password': 'registry-password'}}, method='PATCH')
    assert request(runtime, tok['admin'])['registry'] == {'server': 'ghcr.io', 'username': 'bot-account', 'hasPassword': True}
    for who in ['viewer', 'dev']:
        seen = request(runtime, tok[who]); assert seen['registry'] == {'server': '', 'username': '', 'hasPassword': False} and 'bot-account' not in json.dumps(seen)
    checks.append('task_command_needs_tasks_manage_and_registry_needs_app_configure')

    # ---------------------------------------------------------------- 7. `env` of PATCH/POST: same plan as /environment, always audited.
    audits = lambda app: [json.loads(row[0]) for row in sqlite3.connect(database).execute('SELECT details FROM web_environment_audit WHERE appId=? ORDER BY created', (app,))]
    p1 = site('P1', image=True)
    before = len(audits(q2))
    request(f'/api/web-apps/{q2}', tok['dev'], {'env': [{'name': 'KEEP', 'value': 'one', 'runtime': True}, {'name': 'DROP', 'value': 'two', 'build': True}]}, method='PATCH')
    request(f'/api/web-apps/{q2}', tok['dev'], {'env': [{'name': 'DROP', 'remove': True}, {'name': 'KEEP', 'value': '', 'runtime': True}]}, method='PATCH')
    logged = audits(q2)[before:]
    assert [row['mode'] for row in logged] == ['direct', 'direct'] and logged[0]['changes']['added'] == ['DROP', 'KEEP'], logged
    assert logged[1]['changes']['removed'] == ['DROP'] and logged[1]['changes']['preserved'] == ['KEEP'], logged
    assert [item['name'] for item in request(f'/api/web-apps/{q2}/environment', tok['viewer'])['variables']] == ['KEEP']
    for bad in [{'name': 'NODE_OPTIONS', 'value': 'x', 'runtime': True}, {'name': '__proto__', 'value': 'x', 'runtime': True}, {'name': 'NOWHERE', 'value': 'x'}, {'name': 'EMPTY', 'runtime': True}]:
        request(f'/api/web-apps/{q2}', tok['dev'], {'env': [bad]}, method='PATCH', status=400)
    assert len(audits(q2)) == before + 2
    created = request('/api/web-apps', platform, {'name': 'Audit', 'runtime': {'kind': 'image', 'image': IMAGE}, 'env': [{'name': 'FIRST', 'value': 'value', 'build': True, 'runtime': True}]}, status=201)['app']['id']
    assert audits(created)[0]['changes']['added'] == ['FIRST']
    request(f'/api/web-apps/{p1}', tok['dev'], {'env': [{'name': 'X', 'value': 'y', 'runtime': True}]}, method='PATCH', status=404)
    checks.append('legacy_env_field_uses_the_environment_plan_and_audit')

    # ---------------------------------------------------------------- 8. Terminal caps.
    def publish_record(app):
        job = request('/api/collections/web_deployments/records', su, {'appId': app, 'action': 'deploy', 'status': 'ready'})['id']
        request(f'/api/collections/web_deployments/records/{job}', su, {'result': {'container': f'ph-web-{app}-{job}', 'image': f'ph-web/{app}:{job}'}}, method='PATCH')
        request(f'/api/collections/web_apps/records/{app}', su, {'activeDeployment': job, 'status': 'running'}, method='PATCH')
    t1, t2 = q1, q2
    publish_record(t1); publish_record(t2)
    terminal = lambda app, who, status=201: request(f'/api/web-apps/{app}/terminal/session', tok[who], {}, status=status)
    terminal(t1, 'owner'); terminal(t1, 'admin')
    assert 'sur cette application' in terminal(t1, 'owner', 400)['message']
    terminal(t2, 'owner')
    assert 'pour ce compte' in terminal(t2, 'owner', 400)['message']
    terminal(t2, 'admin')
    stamp = lambda seconds: time.strftime('%Y-%m-%d %H:%M:%S.000Z', time.gmtime(time.time() + seconds))
    rows = [(secrets.token_hex(8)[:15], 'zzzzzzzzzzzzzzz', ids['dev'], 'zzzzzzzzzzzzzzy', 'ph-web-fixture', 'issued', '/bin/sh', stamp(60), stamp(1800), stamp(0), stamp(0)) for _ in range(12)]
    with sqlite3.connect(database) as db:
        db.executemany('INSERT INTO web_terminal_sessions (id, appId, actorId, deploymentId, containerName, status, shell, expiresAt, absoluteExpiresAt, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)', rows)
    other_terminal = q3
    publish_record(other_terminal)
    assert request(f'/api/web-apps/{other_terminal}/terminal/session', tok['owner_b'], {}, status=400)['message'] == 'Seize terminaux sont déjà ouverts sur ce serveur.'
    # Grants left unclaimed for more than 60 seconds no longer count.
    with sqlite3.connect(database) as db:
        db.execute("UPDATE web_terminal_sessions SET expiresAt=? WHERE appId='zzzzzzzzzzzzzzz'", (stamp(-1),))
    request(f'/api/web-apps/{other_terminal}/terminal/session', tok['owner_b'], {}, status=201)
    checks.append('terminal_two_per_application_two_per_actor_sixteen_total_stale_grants_ignored')

    # ---------------------------------------------------------------- 10. Private registries refused.
    profile_url = f'/api/web-apps/{f3}/runtime/profile'
    private = ['localhost/site', 'localhost:5000/site', 'registry:5000/site', '127.0.0.1:5000/site', '10.0.0.1/site', '169.254.169.254/site', 'registry.local/site', 'registry.internal/org/site']

    def update_profile(profile, status=200, registry=None):
        current = request(profile_url, tok['admin'])
        return request(profile_url, tok['admin'], {'profile': profile, 'revision': current['revision'], **({'registry': registry} if registry else {})}, method='PATCH', status=status)
    for image in private:
        update_profile({'kind': 'image', 'image': image + '@sha256:' + 'a' * 64}, 400)
        update_profile({'kind': 'compose', 'compose': {'front': 'web', 'services': {'web': {'image': image + '@sha256:' + 'a' * 64}}}}, 400)
    for image in ['nginx', 'library/nginx', 'ghcr.io/org/site', 'registry.example.com:5000/org/site']:
        update_profile({'kind': 'image', 'image': image + '@sha256:' + 'a' * 64})
    for server in ['localhost', 'localhost:5000', '127.0.0.1', '10.0.0.1:5000', 'registry', 'registry.local', 'registry.internal:5000']:
        update_profile({'kind': 'image', 'image': IMAGE}, 400, {'server': server, 'username': 'bot', 'password': 'registry-password'})
    update_profile({'kind': 'image', 'image': IMAGE}, 200, {'server': 'registry.example.com:5000', 'username': 'bot', 'password': 'registry-password'})
    for repository in ['registry.internal/org/site', 'registry.local/site', 'foo.localhost/site', 'localhost/site', '127.0.0.1:5000/site']:
        request(f'/api/web-apps/{q1}/deploy-tokens', tok['admin'], {'name': 'ci', 'imageRepository': repository}, status=400)
    issued = request(f'/api/web-apps/{q1}/deploy-tokens', tok['admin'], {'name': 'ci', 'imageRepository': 'ghcr.io/team/site'}, status=201)
    # A token issued before this rule keeps its stored repository: the image is checked again at each deployment.
    with sqlite3.connect(database) as db:
        db.execute("UPDATE web_ci_tokens SET imageRepository='registry.internal/site' WHERE id=?", (issued['item']['id'],))
    request('/api/web-apps/ci/deploy', 'Bearer ' + issued['token'], {'image': 'registry.internal/site@sha256:' + 'c' * 64, 'commit': HEAD, 'requestId': 'stale'}, status=400)
    checks.append('private_registries_refused_for_images_compose_credentials_and_ci_tokens')

    # ---------------------------------------------------------------- 1 (end). Server-wide cap of sites, including previews.
    s1 = site('S1', 'team-a/site')
    team_s = project(s1, 'Équipe S')
    member(team_s, 'dev2', 'developer')
    request(f'/api/web-projects/{team_s}', platform, {'name': 'Équipe S', 'previewEnabled': True, 'previewSourceAppId': s1, 'previewMax': 3, 'previewTtlHours': 1}, method='PATCH')
    now = time.strftime('%Y-%m-%d %H:%M:%S.000Z', time.gmtime())
    with sqlite3.connect(database) as db:
        count = db.execute('SELECT count(*) FROM web_apps').fetchone()[0]
        assert count < SITE_MAX, count
        db.executemany('INSERT INTO web_apps (id, name, uid, status, config, created, updated) VALUES (?, ?, ?, ?, ?, ?, ?)', [(f'bulk{index:011d}', f'Bulk {index}', platform_id, 'stopped', '{}', now, now) for index in range(SITE_MAX - count)])
    message = f'Limite de {SITE_MAX} sites atteinte sur ce serveur. Contactez la plateforme.'
    assert request('/api/web-apps', platform, {'name': 'Trop', 'repository': 'team-c/site', 'branch': 'main'}, status=400)['message'] == message
    with sqlite3.connect(database) as db:
        db.execute("DELETE FROM web_app_environments WHERE slug='e0'")
    assert clone('e0-again', 400)['message'] == message
    # In the signed webhook a preview is noted, never an error: the delivery stays recorded.
    before = github_calls('pull-request')
    result = request(f'/api/web-projects/{team_s}/previews/6/refresh', tok['dev2'], {}, status=202)
    assert result['queued'] == 0 and 'Quota de previews' in result['note'], result
    assert github_calls('pull-request') == before + 1
    checks.append('sites_per_server_cap_on_create_clone_and_preview')

    print(json.dumps({'passed': True, 'checks': checks}))
finally:
    if process:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
    temporary.cleanup()
