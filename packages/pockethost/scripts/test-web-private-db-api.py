#!/usr/bin/env python3
"""Disposable PocketBase: private database grants, two-sided RBAC and a fake rootful Docker provider."""
import argparse
import hashlib
import json
import os
import pathlib
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=pathlib.Path, required=True)
parser.add_argument('--pocketbase', required=True)
args = parser.parse_args()
os.umask(0o077)
temporary = tempfile.TemporaryDirectory(prefix='ph-private-db-')
root = pathlib.Path(temporary.name).resolve()
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
origin = f'http://127.0.0.1:{port}'
worker_secret, broker_secret = secrets.token_urlsafe(40), secrets.token_urlsafe(40)
process = None


def request(path, token='', body=None, method=None, status=200, headers=None):
    req = urllib.request.Request(origin + path, data=None if body is None else json.dumps(body).encode(), method=method or ('POST' if body is not None else 'GET'), headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {}), **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            data = response.read()
            assert response.status == status, (path, response.status, status)
            return json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        data = error.read()
        assert error.code == status, (path, error.code, status, data.decode())
        return json.loads(data)


try:
    source = args.source / 'packages/pockethost/src/mothership-app'
    hooks = root / 'hooks'
    hooks.mkdir()
    shutil.copy(source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
    shutil.copy(source / 'src/lib/handlers/webApps/hooks.ts', hooks / 'web.pb.js')
    data_root = root / 'resources'
    control = root / 'docker.json'
    control.write_text('{}')
    bin_dir = root / 'bin'
    bin_dir.mkdir()
    fake_docker = bin_dir / 'docker'
    fake_docker.write_text("#!/usr/bin/env python3\n" + r'''
import hashlib,json,pathlib,sys
control=pathlib.Path(CONTROL)
state=json.loads(control.read_text());args=sys.argv[3:];command=args.pop(0)
def save(): control.write_text(json.dumps(state))
def find(key): return state.get(key) or next((x for x in state.values() if x.get('Name')=='/'+key),None)
if command=='inspect':
 print(json.dumps([find(key)for key in args if find(key)]))
elif command=='ps':
 print('\n'.join(x['Id']for x in state.values()if (x.get('Config',{}).get('Labels')or{}).get('kolab.db-target.role')=='target'))
elif command=='rm':
 for key in args:
  item=find(key)
  if item:
   for old in [k for k,v in state.items()if v is item]: del state[old]
 save()
elif command=='start':
 item=find(args[0]);item['State']={'Running':True,'Health':{'Status':'healthy'}};save();print(item['Id'])
elif command=='create':
 values={};labels={};mounts=[];i=0
 while i<len(args)and args[i].startswith('--'):
  key=args[i];i+=1
  if key=='--read-only': values[key]=True;continue
  value=args[i];i+=1
  if key=='--label':
   k,v=value.split('=',1);labels[k]=v
  elif key=='--mount':
   m=dict(part.split('=',1)if '='in part else(part,True)for part in value.split(','));mounts.append({'Type':'bind','Source':m['src'],'Destination':m['dst'],'RW':not m.get('readonly',False)})
  else: values[key]=value
 image=args[i];cmd=args[i+1:];cid=hashlib.sha256(values['--name'].encode()).hexdigest()
 state[cid]={'Id':cid,'Name':'/'+values['--name'],'Config':{'Image':image,'User':values['--user'],'Entrypoint':[values['--entrypoint']],'Cmd':cmd,'Labels':labels,'Healthcheck':{'Test':['CMD-SHELL',values['--health-cmd']],'Interval':5000000000,'Timeout':int(values['--health-timeout'].rstrip('s'))*1000000000,'Retries':1,'StartPeriod':1000000000}},
 'HostConfig':{'NetworkMode':values['--network'],'ReadonlyRootfs':bool(values.get('--read-only')),'Privileged':False,'CapDrop':[values['--cap-drop']],'SecurityOpt':[values['--security-opt']],'Memory':96*1024*1024,'NanoCpus':50000000,'PidsLimit':int(values['--pids-limit'])},'State':{'Running':False},'Mounts':mounts}
 save();print(cid)
'''.replace('CONTROL', repr(str(control))))
    fake_docker.chmod(0o700)
    provider = root / 'provider_fixture.py'
    provider.write_text("import importlib.util,pathlib,sys\np=" + repr(str(args.source / 'packages/pockethost/scripts/web_private_db_provider.py')) + "\ns=importlib.util.spec_from_file_location('provider',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\nm.Provider.directory=lambda self,p,mode:p.mkdir(mode=mode,exist_ok=True)\nm.Provider.available=lambda self,info,target:info.get('State',{}).get('Running') and info.get('State',{}).get('Health',{}).get('Status')=='healthy'\nm.main()\n")
    target_script = root / 'target.cjs'
    target_script.write_text('// trusted static fixture; never executed')
    # Socket UID and permissions are covered by the independent provider suite;
    # this API fixture never starts an actual container or privileged process.
    env = {**os.environ, 'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'], 'WEB_APPS_ENABLED': 'true', 'WEB_DB_BRIDGE_ENABLED': 'true', 'WEB_APPS_MASTER_KEY': 'k' * 32, 'WEB_APPS_WORKER_SECRET': worker_secret, 'WEB_DB_BRIDGE_SECRET': broker_secret, 'WEB_APPS_ROOT': str(root / 'web'), 'DATA_ROOT': str(data_root), 'DOCKER_INSTANCE_IMAGE_NAME': 'fixture/pocketbase', 'APEX_DOMAIN': 'example.invalid', 'WEB_DB_PROVIDER_SCRIPT': str(provider), 'WEB_DB_TARGET_SCRIPT': str(target_script), 'WEB_DB_TARGET_ROOT': str(root / 'targets'), 'WEB_DB_TARGET_IMAGE': 'fixture/node@sha256:' + 'd' * 64}
    password = 'P' + secrets.token_urlsafe(32)
    data = root / 'data'
    subprocess.run([args.pocketbase, 'superuser', 'upsert', 'su@example.invalid', password, '--dir', str(data), '--hooksDir', str(root / 'empty'), '--migrationsDir', str(source / 'pb_migrations')], env=env, check=True, capture_output=True)
    with (root / 'process.log').open('wb') as log:
        process = subprocess.Popen([args.pocketbase, 'serve', '--http', f'127.0.0.1:{port}', '--dir', str(data), '--hooksDir', str(hooks), '--migrationsDir', str(source / 'pb_migrations')], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError((root / 'process.log').read_text()[-3000:])
        try:
            request('/api/health')
            break
        except OSError:
            time.sleep(.1)
    su = request('/api/collections/_superusers/auth-with-password', body={'identity': 'su@example.invalid', 'password': password})['token']
    initial_limits = request('/api/settings', su)['rateLimits']
    tokens, users = {}, {}
    for name in ['platform', 'owner', 'admin', 'developer', 'viewer', 'outsider']:
        user = request('/api/collections/users/records', su, {'email': name + '@example.invalid', 'username': name, 'password': password, 'passwordConfirm': password, 'verified': True, 'superAdmin': name == 'platform', 'subscription': 'premium', 'subscription_quantity': 1})
        users[name] = user
        tokens[name] = request('/api/collections/users/auth-with-password', body={'identity': user['email'], 'password': password})['token']

    def site(name):
        return request('/api/web-apps', tokens['platform'], {'name': name, 'runtime': {'kind': 'image', 'image': 'ghcr.io/team/site@sha256:' + 'a' * 64}}, status=201)['app']['id']

    a, other, preview, limited = site('A'), site('Other'), site('Preview without grant'), site('Limit')
    project = request('/api/web-apps/' + a + '/project', tokens['platform'], {'name': 'Private project', 'environment': 'prod'}, status=201)['project']['id']
    for app, name in [(preview, 'dev'), (limited, 'staging')]:
        request('/api/web-apps/' + app + '/project/attach', tokens['platform'], {'projectId': project, 'environment': name, 'slug': name})
    preview_member = next(x for x in request('/api/collections/web_app_environments/records?perPage=100', su)['items'] if x['appId'] == preview)
    request('/api/collections/web_app_environments/records/' + preview_member['id'], su, {'environment': 'preview'}, method='PATCH')
    member_ids = {}
    for name, role in [('owner', 'owner'), ('admin', 'admin'), ('developer', 'developer'), ('viewer', 'viewer')]:
        value = request('/api/web-projects/' + project + '/members', tokens['platform'], {'email': name + '@example.invalid', 'role': role}, status=201)
        member_ids[name] = value.get('member', {}).get('id')

    def base(name, owner='owner'):
        return request('/api/collections/instances/records', su, {'subdomain': name, 'uid': users[owner]['id'], 'power': True, 'version': '0.39.11', 'status': 'idle'})['id']

    db = base('private-one')
    outsider_db = base('private-other', 'outsider')
    developer_db = base('private-developer', 'developer')
    extra = [base('private-extra-' + str(i)) for i in range(5)]
    docker_rows = {}

    def docker_state(ident, **changes):
        row = {'Id': hashlib.sha256(ident.encode()).hexdigest(), 'Name': '/' + ident, 'State': {'Running': True, 'StartedAt': '2026-10-31T12:34:56.123456789Z'}, 'Config': {'Image': 'fixture/pocketbase:latest'}, 'Mounts': [{'Destination': '/home/pockethost', 'Source': str(data_root / 'instances' / ident)}], 'NetworkSettings': {'Ports': {'8090/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '33525'}]}}}
        row.update(changes)
        docker_rows = json.loads(control.read_text())
        docker_rows[ident] = row
        control.write_text(json.dumps(docker_rows))

    docker_state(db)
    path = '/api/instance/' + db + '/private-access'
    request(path, status=401)
    request(path, tokens['outsider'], status=403)
    view = request(path, tokens['owner'])
    assert view['supported'] and not view['bindings']
    assert a in [x['id'] for x in view['applications']] and other not in [x['id'] for x in view['applications']]
    request(path, tokens['admin'], {'appId': a, 'port': 4825, 'enabled': True}, status=403)
    request('/api/instance/' + developer_db + '/private-access', tokens['developer'], {'appId': a, 'port': 4825, 'enabled': True}, status=403)
    request(path, tokens['owner'], {'appId': other, 'port': 4825, 'enabled': True}, status=403)
    for payload in [{'appId': a, 'port': 80, 'enabled': True}, {'appId': a, 'port': 3000, 'enabled': True}, {'appId': a, 'port': 65536, 'enabled': True}, {'appId': a, 'port': 4825.5, 'enabled': True}, {'appId': a, 'port': True, 'enabled': True}, {'appId': a, 'port': 4825, 'enabled': 'true'}, {'appId': a, 'port': 4825, 'enabled': True, 'targetPort': 8091}]:
        request(path, tokens['owner'], payload, status=400)
    binding = request(path, tokens['owner'], {'appId': a, 'port': 4825, 'enabled': True})['binding']
    ident = binding['id']
    assert binding['url'] == 'http://127.0.0.1:4825' and binding['envName'] == 'KOLAB_DB_4825_URL'
    assert binding['authorized'] and binding['enabled'] and binding['needsRestart']
    assert not any('token' in key.lower() or key == 'revision' for key in binding)
    request('/api/collections/web_private_db_bindings/records', tokens['owner'], status=403)
    request('/api/web-apps/' + other + '/private-databases/' + ident, tokens['platform'], method='DELETE', status=404)
    request('/api/instance/' + outsider_db + '/private-access/' + ident, tokens['outsider'], method='DELETE', status=404)
    request('/api/web-apps/' + a + '/private-databases/' + ident, tokens['viewer'], method='DELETE', status=403)
    app_view = request('/api/web-apps/' + a + '/private-databases', tokens['viewer'])
    assert app_view['bindings'][0]['id'] == ident

    worker_path, broker_path = '/api/web-apps/_private-db/worker', '/api/web-apps/_private-db/broker'
    wh, bh = {'X-Web-Worker': worker_secret}, {'X-DB-Bridge': broker_secret}
    request(worker_path, status=401)
    request(broker_path, headers=wh, status=401)
    request(worker_path, headers={**wh, 'X-Forwarded-For': '127.0.0.1'}, status=403)
    request(broker_path, headers={**bh, 'Via': 'localhost'}, status=403)
    worker = request(worker_path, headers=wh)
    secret_grant = next(x for x in worker['bindings'] if x['id'] == ident)
    assert len(secret_grant['token']) == 64 and len(secret_grant['revision']) == 24
    assert secret_grant['databaseRunning'] and not secret_grant['available'], 'A warming target must retain database power state'
    assert preview in worker['applications'] and not any(x['appId'] == preview for x in worker['bindings'])
    broker = request(broker_path, headers=bh)
    route = next(x for x in broker['bindings'] if x['id'] == ident)
    assert route['available'] and route['targetSocket'].startswith(str(root / 'targets' / db))
    assert route['targetContainerId'] == hashlib.sha256(db.encode()).hexdigest()
    assert len(route['targetEpoch']) == 64 and 'targetPort' not in route
    assert route['tokenHash'] == hashlib.sha256(secret_grant['token'].encode()).hexdigest()
    assert secret_grant['token'] not in json.dumps(broker)
    assert secret_grant['token'] not in json.dumps(request(path, tokens['owner']))

    status_path = '/api/web-apps/_private-db/status'
    report = {'id': ident, 'appId': a, 'revision': secret_grant['revision'], 'status': 'ready', 'errorCode': '', 'serviceCount': 1, 'needsRestart': False}
    request(status_path, body={'bindings': [report]}, status=401)
    assert request(status_path, body={'bindings': [report]}, headers=wh)['updated'] == 1
    assert request(path, tokens['owner'])['bindings'][0]['status'] == 'ready'
    for key, value in [('id', [ident]), ('appId', [a]), ('revision', [secret_grant['revision']]), ('serviceCount', -1), ('needsRestart', 'false'), ('errorCode', 'private-secret'), ('status', 'running')]:
        request(status_path, body={'bindings': [{**report, key: value}]}, headers=wh, status=400)
    request(path, tokens['owner'], {'appId': a, 'port': 4825, 'enabled': True})
    rotated = next(x for x in request(worker_path, headers=wh)['bindings'] if x['id'] == ident)
    assert rotated['token'] != secret_grant['token'] and rotated['revision'] != secret_grant['revision']
    assert request(status_path, body={'bindings': [report]}, headers=wh)['updated'] == 0

    request('/api/collections/instances/records/' + db, su, {'power': False}, method='PATCH')
    off = next(x for x in request(worker_path, headers=wh)['bindings'] if x['id'] == ident)
    assert not off['available'] and not off['databaseRunning'] and off['token'] == rotated['token']
    off = next(x for x in request(broker_path, headers=bh)['bindings'] if x['id'] == ident)
    assert not off['available'] and off['targetSocket'] is None and off['targetContainerId'] is None
    assert request(path, tokens['owner'])['bindings'][0]['errorCode'] == 'base_stopped'
    request('/api/collections/instances/records/' + db, su, {'power': True}, method='PATCH')
    for change in [
        {'Config': {'Image': 'fixture/pocketbase-evil:latest'}},
        {'Mounts': [{'Destination': '/home/pockethost', 'Source': str(data_root / 'instances' / outsider_db)}]},
        {'State': {'Running': False}},
        {'Id': 'bad-container-id'},
    ]:
        docker_state(db, **change)
        rejected = next(x for x in request(broker_path, headers=bh)['bindings'] if x['id'] == ident)
        assert not rejected['available'] and rejected['targetSocket'] is None
    docker_state(db)

    request('/api/collections/instances/records/' + db, su, {'suspension': 'manual'}, method='PATCH')
    assert not any(x['id'] == ident for x in request(worker_path, headers=wh)['bindings'])
    request('/api/collections/instances/records/' + db, su, {'suspension': ''}, method='PATCH')
    # Explicit preview grants never carry back into the production application.
    preview_binding = request(path, tokens['owner'], {'appId': preview, 'port': 4825, 'enabled': True})['binding']['id']
    assert any(x['id'] == preview_binding for x in request(worker_path, headers=wh)['bindings'])
    request('/api/web-apps/' + preview, tokens['owner'], {'confirmation': 'Preview without grant'}, method='DELETE', status=202)
    assert not any(x['id'] == preview_binding for x in request(worker_path, headers=wh)['bindings'])
    dead_db = base('private-deleted')
    orphan = request('/api/instance/' + dead_db + '/private-access', tokens['owner'], {'appId': a, 'port': 4827, 'enabled': True})['binding']['id']
    request('/api/collections/instances/records/' + dead_db, su, method='DELETE', status=204)
    assert not any(x['id'] == orphan for x in request(worker_path, headers=wh)['bindings'])
    request('/api/web-apps/' + a + '/private-databases/' + orphan, tokens['owner'], method='DELETE')
    request(path, tokens['owner'], {'appId': a, 'port': 4825, 'enabled': False})
    assert not any(x['id'] == ident for x in request(worker_path, headers=wh)['bindings'])
    request(path, tokens['owner'], {'appId': a, 'port': 4825, 'enabled': True})
    for index, dbid in enumerate(extra[:4]):
        request('/api/instance/' + dbid + '/private-access', tokens['owner'], {'appId': limited, 'port': 4825 + index, 'enabled': True})
    request('/api/instance/' + extra[4] + '/private-access', tokens['owner'], {'appId': limited, 'port': 4830, 'enabled': True}, status=400)
    request('/api/instance/' + extra[0] + '/private-access', tokens['owner'], {'appId': a, 'port': 4825, 'enabled': True}, status=400)

    request('/api/collections/users/records/' + users['owner']['id'], su, {'suspension': 'manual'}, method='PATCH')
    assert not any(x['id'] == ident for x in request(worker_path, headers=wh)['bindings'])
    request('/api/collections/users/records/' + users['owner']['id'], su, {'suspension': ''}, method='PATCH')
    request('/api/collections/instances/records/' + db, su, {'uid': users['outsider']['id']}, method='PATCH')
    assert not any(x['id'] == ident for x in request(worker_path, headers=wh)['bindings'])
    # The app owner can revoke after the base changed ownership.
    assert request('/api/web-apps/' + a + '/private-databases/' + ident, tokens['owner'], method='DELETE')['removed']
    request('/api/collections/instances/records/' + db, su, {'uid': users['owner']['id']}, method='PATCH')
    ident = request(path, tokens['owner'], {'appId': a, 'port': 4825, 'enabled': True})['binding']['id']
    memberships = request('/api/collections/web_project_members/records?perPage=100', su)['items']
    member = next(x for x in memberships if x['user'] == users['owner']['id'] and x['project'] == project)
    request('/api/collections/web_project_members/records/' + member['id'], su, method='DELETE', status=204)
    assert not any(x['id'] == ident for x in request(worker_path, headers=wh)['bindings'])
    # The base owner can revoke after losing all rights on the app.
    assert request(path + '/' + ident, tokens['owner'], method='DELETE')['removed']
    assert request('/api/settings', su)['rateLimits'] == initial_limits
    print(json.dumps({'passed': True, 'checks': ['two_sided_authorization', 'outsider_and_cross_resource_denial', 'no_preview_inheritance', 'strict_ports_and_caps', 'locked_collection_and_no_public_tokens', 'separate_private_secrets_and_loopback', 'rotation_and_stale_reports', 'base_stopped_preserves_url_without_route', 'docker_mount_image_running_and_namespace_provenance', 'live_suspension_deletion_ownership_and_membership_revocation', 'revoke_from_either_resource', 'global_limiter_unchanged']}))
except Exception:
    if (root / 'process.log').exists():
        print((root / 'process.log').read_text()[-3000:])
    raise
finally:
    if process:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    temporary.cleanup()
