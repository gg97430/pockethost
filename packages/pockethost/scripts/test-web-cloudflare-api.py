"""Disposable native PocketBase Cloudflare workflow. Synthetic credentials/helpers only."""
import argparse
import datetime
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
import urllib.parse
import urllib.request

ROUTES = r'''
routerAdd('GET','/api/web-apps/cloudflare/settings',e=>require(`${__hooks}/mothership`).HandleWebCloudflareGet(e),$apis.requireAuth('users'))
routerAdd('PUT','/api/web-apps/cloudflare/settings',e=>require(`${__hooks}/mothership`).HandleWebCloudflarePut(e),$apis.requireAuth('users'))
routerAdd('POST','/api/web-apps/{id}/domains/{domainId}/cloudflare/preview',e=>require(`${__hooks}/mothership`).HandleWebCloudflarePreview(e),$apis.requireAuth('users'))
routerAdd('POST','/api/web-apps/{id}/domains/{domainId}/cloudflare/apply',e=>require(`${__hooks}/mothership`).HandleWebCloudflareApply(e),$apis.requireAuth('users'))
routerAdd('GET','/api/web-apps/{id}/domains',e=>require(`${__hooks}/mothership`).HandleWebDomainsList(e),$apis.requireAuth('users'))
routerAdd('POST','/api/web-apps/{id}/domains/{domainId}/verify',e=>require(`${__hooks}/mothership`).HandleWebDomainVerify(e),$apis.requireAuth('users'))
routerAdd('DELETE','/api/web-apps/{id}/domains/{domainId}',e=>require(`${__hooks}/mothership`).HandleWebDomainDelete(e),$apis.requireAuth('users'))
// TEST ONLY. No production cron runs in this fixture; every maintenance tick is explicit.
routerAdd('POST','/__test/cloudflare/tick',e=>{
  require(`${__hooks}/mothership`).HandleWebCloudflareMaintenance()
  return e.json(200,{ran:true})
},$apis.requireAuth('_superusers'))
routerAdd('POST','/__test/cloudflare/state',e=>{
  const input=e.requestInfo().body, lib=require(`${__hooks}/mothership`)
  const find=key=>$app.findRecordsByFilter('web_app_settings','key={:key}','',1,0,{key})[0]
  if(input.clearPending){
    for(const row of $app.findRecordsByFilter('web_app_settings','key ~ "cloudflare-pending:"','',1000,0))$app.delete(row)
    return e.json(200,{cleared:true})
  }
  if(!/^[a-z0-9]{15}$/.test(input.domainId||''))throw new BadRequestError('Fixture domain ID')
  const row=find('cloudflare-domain:'+input.domainId)
  let state=row?lib.decryptWebValue(row.getString('valueEncrypted')):null
  if(input.patch&&row){
    state={...state,...input.patch}
    row.set('valueEncrypted',lib.encryptWebValue(state));$app.save(row)
  }
  return e.json(200,{state,marker:!!find('cloudflare-pending:'+input.domainId)})
},$apis.requireAuth('_superusers'))
'''

HELPER = r'''
import hashlib,json,pathlib,sys
path=pathlib.Path(sys.argv[1]);data=json.loads(path.read_text());path.unlink()
root=pathlib.Path(__file__).parent
control=json.loads((root/'helper-control.json').read_text())
host=data['hostname'];action=data['action'];options=control.get(host,{})
state_path=root/'helper-dns.json';dns=json.loads(state_path.read_text())
record=hashlib.sha256(host.encode()).hexdigest()[:32]
with (root/'helper-actions.jsonl').open('a')as log:
    log.write(json.dumps({'action':action,'hostname':host,'proxied':data.get('proxied')})+'\n')
result={'zoneId':'1'*32,'recordId':record,'proxied':dns.get(host,{}).get('proxied',False)}
error=''
if action=='preview':
    assert type(data.get('proxied'))is bool,'Expected explicit requested proxy mode'
    result={'hostname':host,'zone':{'id':'1'*32,'name':'lapub.re'},'current':[],
            'changes':[{'action':'create','before':None,'after':{'type':'A','name':host,'content':data['publicIp'],'proxied':data['proxied']}}],
            'revision':'synthetic-revision'}
elif action=='apply':
    assert type(data.get('proxied'))is bool,'Expected explicit effective proxy mode'
    dns[host]={'proxied':data['proxied'],'recordId':record}
    result.update(applied=True,changes=1,proxied=data['proxied'])
elif action=='verify':
    if options.get('verifyError'):error='Synthetic DNS ownership/origin drift'
    else:result.update(verified=True,recordId=options.get('verifyRecordId',record),proxied=options.get('publicProxied',result['proxied']))
elif action=='activate':
    if options.get('activationFailures',0):
        options['activationFailures']-=1;control[host]=options
        (root/'helper-control.json').write_text(json.dumps(control))
        error='Synthetic retryable activation failure'
    else:
        assert data.get('zoneId')=='1'*32 and data.get('recordId')==record,'Exact recorded origin identity required'
        dns[host]={'proxied':True,'recordId':record};result.update(activated=True,proxied=True)
else:raise AssertionError('Unexpected helper operation')
state_path.write_text(json.dumps(dns))
print(json.dumps({'error':error}if error else{'result':result}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=pathlib.Path, required=True)
    parser.add_argument('--pocketbase', required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='kolab-cloudflare-') as tmp:
        root = pathlib.Path(tmp).resolve()
        source = args.source.resolve() / 'packages/pockethost/src/mothership-app'
        hooks = root / 'hooks'; hooks.mkdir()
        empty = root / 'empty'; empty.mkdir()
        web = root / 'web'; web.mkdir()
        (web / 'worker-status.json').write_text(json.dumps({'httpsReady': False}))
        shutil.copy(source / 'pb_hooks/mothership.js', hooks / 'mothership.js')
        (hooks / 'web.pb.js').write_text(ROUTES)
        (root / 'helper.py').write_text(HELPER)
        (root / 'domain-helper.py').write_text("import json\nprint(json.dumps({'verified':False,'error':'Synthetic proxied edge IP requires provider verification'}))\n")
        (root / 'helper-control.json').write_text('{}')
        (root / 'helper-dns.json').write_text('{}')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
        env = {**os.environ, 'WEB_APPS_ENABLED': 'true', 'WEB_APPS_MASTER_KEY': 'k' * 32,
               'WEB_APPS_ROOT': str(web), 'WEB_APPS_PUBLIC_IP': '135.125.67.39',
               'WEB_APPS_CLOUDFLARE_HELPER': str(root / 'helper.py'), 'MOTHERSHIP_CLOUDFLARE_API_TOKEN': 'secret-canary-cloudflare-token',
               'WEB_APPS_DOMAIN_HELPER': str(root / 'domain-helper.py'),
               'APEX_DOMAIN': 'example.invalid', 'APP_HOST': 'app.example.invalid',
               'PH_SUPPORT_EMAIL': 'support@example.invalid', 'DATA_ROOT': str(root / 'data')}
        password = 'P' + secrets.token_urlsafe(24)
        data = root / 'pb_data'; migrations = source / 'pb_migrations'
        subprocess.run([args.pocketbase, 'superuser', 'upsert', 'su@example.invalid', password,
                        '--dir', str(data), '--hooksDir', str(empty), '--migrationsDir', str(migrations)],
                       env=env, check=True, capture_output=True)
        log = (root / 'log').open('wb')
        process = subprocess.Popen([args.pocketbase, 'serve', '--dir', str(data), '--hooksDir', str(hooks),
                                    '--migrationsDir', str(migrations), '--http', f'127.0.0.1:{port}'],
                                   env=env, stdout=log, stderr=subprocess.STDOUT)
        responses = []

        def req(path, token='', body=None, method=None, status=200):
            query = urllib.request.Request(f'http://127.0.0.1:{port}' + path,
                data=None if body is None else json.dumps(body).encode(), method=method,
                headers={'Content-Type': 'application/json', **({'Authorization': token} if token else {})})
            try:
                with urllib.request.urlopen(query, timeout=25) as response:
                    code, raw = response.status, response.read()
            except urllib.error.HTTPError as error:
                code, raw = error.code, error.read()
            wanted = (status,) if isinstance(status, int) else status
            assert code in wanted, (path, code, raw[:500])
            responses.append(raw.decode())
            return json.loads(raw) if raw else {}

        def patch(collection, ident, values):
            return req(f'/api/collections/{collection}/records/{ident}', su, values, method='PATCH')

        def create(collection, values):
            return req(f'/api/collections/{collection}/records', su, values)

        def actions(host):
            path = root / 'helper-actions.jsonl'
            return [row for line in path.read_text().splitlines() if (row := json.loads(line))['hostname'] == host] if path.exists() else []

        def control(host, **values):
            path = root / 'helper-control.json'; current = json.loads(path.read_text())
            current[host] = values; path.write_text(json.dumps(current))

        def state(domain, **values):
            return req('/__test/cloudflare/state', su, {'domainId': domain, **({'patch': values} if values else {})})

        def tick(domain=None):
            if domain:state(domain, nextAttemptAt=0)
            req('/__test/cloudflare/tick', su, {})

        def quiet():
            # Test-only cleanup between independent cases; production cleanup is asserted first.
            req('/__test/cloudflare/state', su, {'clearPending': True})

        def jobs(app):
            value = urllib.parse.urlencode({'filter': f'appId="{app}" && action="domain"', 'perPage': 100})
            return req('/api/collections/web_deployments/records?' + value, su)['items']

        def active(domain, app):
            for job in jobs(app):
                if job['status'] in ('queued', 'running'):patch('web_deployments', job['id'], {'status': 'ready'})
            expiry = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)).isoformat()
            patch('web_app_domains', domain, {'status': 'active', 'certificateExpiresAt': expiry})

        def view(app, domain):
            return next(row for row in req(f'/api/web-apps/{app}/domains', admin)['items'] if row['id'] == domain)

        def user(name, platform=True):
            row = create('users', {'email': name + '@example.invalid', 'username': name, 'password': password,
                'passwordConfirm': password, 'verified': True, 'superAdmin': platform,
                'subscription': 'premium', 'subscription_quantity': 5})
            token = req('/api/collections/users/auth-with-password', body={'identity': row['email'], 'password': password})['token']
            return row['id'], token

        counter = 0
        def fixture(owner=None):
            nonlocal counter
            counter += 1
            app = create('web_apps', {'name': 'Synthetic DNS ' + str(counter), 'uid': owner or uid, 'status': 'running',
                'config': {'repository': 'acme/web', 'repositoryId': 1, 'branch': 'main', 'rootDirectory': '.', 'healthPath': '/'}})['id']
            deployed = create('web_deployments', {'appId': app, 'action': 'deploy', 'status': 'ready', 'actorId': owner or uid, 'commit': 'a' * 40})['id']
            patch('web_apps', app, {'activeDeployment': deployed, 'desiredCommit': 'a' * 40})
            host = 'native-' + str(counter) + '.lapub.re'
            domain = create('web_app_domains', {'appId': app, 'host': host, 'status': 'pending_dns', 'ownershipToken': 'x' * 40})['id']
            return app, domain, host, f'/api/web-apps/{app}/domains/{domain}'

        def apply(base, host, token=None):
            token = token or admin
            preview = req(base + '/cloudflare/preview', token, {})
            result = req(base + '/cloudflare/apply', token, {'previewId': preview['previewId'], 'confirmHostname': host})
            return preview, result

        try:
            for _ in range(100):
                if process.poll() is not None:raise RuntimeError((root / 'log').read_text()[-2500:])
                try:req('/api/health'); break
                except OSError:time.sleep(.1)
            su = req('/api/collections/_superusers/auth-with-password', body={'identity': 'su@example.invalid', 'password': password})['token']
            uid, admin = user('admin'); _, other = user('other'); member_id, member = user('member', False)
            req('/api/web-apps/cloudflare/settings', status=401)
            req('/api/web-apps/cloudflare/settings', member, status=403)
            assert req('/api/web-apps/cloudflare/settings', admin)['configured']
            req('/__test/cloudflare/tick', member, {}, status=403)

            # A project owner can manage domains, but cannot use global credentials.
            app, domain, host, base = fixture(member_id)
            project = create('web_projects', {'name': 'Member project', 'repository': 'acme/web', 'actorId': member_id})['id']
            create('web_project_members', {'project': project, 'user': member_id, 'role': 'owner'})
            create('web_app_environments', {'projectId': project, 'appId': app, 'environment': 'production', 'slug': 'production', 'state': 'active'})
            req(f'/api/web-apps/{app}/domains', member)
            req(base + '/cloudflare/preview', member, {}, status=403)
            req(base + '/verify', admin, {}, status=400)
            preview = req(base + '/cloudflare/preview', admin, {})
            req(base + '/cloudflare/apply', admin, {'previewId': preview['previewId'], 'confirmHostname': 'wrong.lapub.re'}, status=400)
            req(base + '/cloudflare/apply', other, {'previewId': preview['previewId'], 'confirmHostname': host}, status=400)
            assert not any(row['action'] == 'apply' for row in actions(host))
            req(base + '/cloudflare/apply', admin, {'previewId': preview['previewId'], 'confirmHostname': host})
            req(base + '/cloudflare/apply', admin, {'previewId': preview['previewId'], 'confirmHostname': host}, status=400)
            assert [row['proxied'] for row in actions(host) if row['action'] == 'apply'] == [False]
            assert state(domain)['marker'] and state(domain)['state']['enabled'] is True
            assert view(app, domain)['cloudflare']['status'] in ('pending_validation', 'pending_https')
            quiet()

            # TLS first, durable workflow, then activate the unique recorded A.
            (web / 'worker-status.json').write_text(json.dumps({'httpsReady': True}))
            app, domain, host, base = fixture(); apply(base, host); tick(domain)
            assert len(jobs(app)) == 1 and jobs(app)[0]['status'] == 'queued'
            assert not any(row['action'] == 'activate' for row in actions(host))
            assert state(domain)['state']['status'] == 'pending_https'
            active(domain, app); tick(domain)
            assert state(domain)['state']['status'] == 'active' and not state(domain)['marker']
            assert view(app, domain)['cloudflare'] == {'enabled': True, 'status': 'active', 'lastError': ''}
            assert [row['action'] for row in actions(host)].count('apply') == 1
            assert [row['action'] for row in actions(host)].count('activate') == 1
            quiet()

            # Already-valid TLS never gets downgraded to DNS-only.
            app, domain, host, base = fixture(); active(domain, app); apply(base, host)
            assert [row['proxied'] for row in actions(host) if row['action'] in ('preview', 'apply')] == [True, True]
            assert req(f'/api/collections/web_app_domains/records/{domain}', su)['status'] == 'active'
            quiet()

            # A legacy proxied domain with no saved workflow can still renew via provider-origin verification.
            app, domain, host, base = fixture(); active(domain, app); control(host, publicProxied=True)
            assert state(domain)['state'] is None
            req(base + '/verify', admin, {}, status=202)
            assert any(row['action'] == 'verify' for row in actions(host)) and len(jobs(app)) == 1
            assert req(f'/api/collections/web_app_domains/records/{domain}', su)['status'] == 'active'

            # Expired TLS cannot enable proxy early; an explicit proxy-disabled choice is preserved.
            app, domain, host, base = fixture(); active(domain, app)
            expired = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).isoformat()
            patch('web_app_domains', domain, {'certificateExpiresAt': expired}); apply(base, host)
            assert [row['proxied'] for row in actions(host) if row['action'] == 'apply'] == [False]
            tick(domain)
            assert len(jobs(app)) == 1 and req(f'/api/collections/web_app_domains/records/{domain}', su)['status'] == 'pending_certificate'
            active(domain, app); tick(domain)
            assert state(domain)['state']['status'] == 'active'
            assert [row['action'] for row in actions(host)].count('activate') == 1
            quiet()
            app, domain, host, base = fixture(); active(domain, app)
            preview = req(base + '/cloudflare/preview', admin, {'proxyEnabled': False})
            req(base + '/cloudflare/apply', admin, {'previewId': preview['previewId'], 'confirmHostname': host})
            assert state(domain)['state']['enabled'] is False and state(domain)['state']['status'] == 'disabled'
            assert not state(domain)['marker']
            assert [row['proxied'] for row in actions(host) if row['action'] == 'apply'] == [False]
            quiet()

            # No-proxy mode still completes HTTPS, without ever activating Cloudflare's proxy.
            app, domain, host, base = fixture()
            preview = req(base + '/cloudflare/preview', admin, {'proxyEnabled': False})
            req(base + '/cloudflare/apply', admin, {'previewId': preview['previewId'], 'confirmHostname': host})
            tick(domain); assert state(domain)['state']['status'] == 'pending_https'
            active(domain, app); tick(domain)
            assert state(domain)['state']['status'] == 'disabled' and not state(domain)['marker']
            assert state(domain)['state']['enabled'] is False and not any(row['action'] == 'activate' for row in actions(host))
            quiet()

            for name, revoked in [('revoked', {'superAdmin': False}), ('suspended', {'suspension': 'synthetic-test'})]:
                actor, token = user(name)
                app, domain, host, base = fixture(actor); apply(base, host, token)
                before = len(actions(host)); patch('users', actor, revoked); tick(domain)
                current = state(domain)['state']
                assert current['enabled'] is True and current['status'] == 'error' and current['lastError']
                assert not state(domain)['marker']
                assert len(actions(host)) == before and not jobs(app)
                quiet()

            # DNS/ownership drift never queues or activates early, and can retry.
            app, domain, host, base = fixture(); apply(base, host); control(host, verifyError=True); tick(domain)
            assert state(domain)['state']['status'] == 'error' and state(domain)['state']['lastError']
            assert not jobs(app) and not any(row['action'] == 'activate' for row in actions(host))
            control(host, verifyRecordId='9' * 32); tick(domain)
            assert state(domain)['state']['status'] == 'error' and not jobs(app)
            control(host); tick(domain); assert len(jobs(app)) == 1
            quiet()

            # Activation failures preserve active TLS and do not reapply DNS.
            app, domain, host, base = fixture(); apply(base, host); active(domain, app)
            control(host, activationFailures=1); tick(domain)
            assert state(domain)['state']['status'] == 'error'
            assert req(f'/api/collections/web_app_domains/records/{domain}', su)['status'] == 'active'
            tick(domain); assert state(domain)['state']['status'] == 'active'
            assert [row['action'] for row in actions(host)].count('apply') == 1
            assert [row['action'] for row in actions(host)].count('activate') == 2
            quiet()

            # Live leases block preview/delete; expired leases can be reclaimed.
            app, domain, host, base = fixture(); apply(base, host)
            state(domain, leaseUntil=int(time.time() * 1000) + 180000, leaseToken='synthetic-live-lease', nextAttemptAt=0)
            before = len(actions(host)); req(base + '/cloudflare/preview', admin, {}, status=(400, 409))
            req(base, admin, method='DELETE', status=(400, 409)); tick()
            assert len(actions(host)) == before and not jobs(app)
            state(domain, leaseUntil=1, nextAttemptAt=0); tick(); assert len(jobs(app)) == 1
            quiet()

            # Recheck the current app/domain relationship and deletion tombstone.
            for changed in ('relationship', 'deleting'):
                app, domain, host, base = fixture(); apply(base, host); before = len(actions(host))
                if changed == 'relationship':
                    another, _, _, _ = fixture(); patch('web_app_domains', domain, {'appId': another})
                else:patch('web_apps', app, {'deletionId': 'z' * 15})
                tick(domain)
                assert state(domain)['state']['status'] == 'error' and not state(domain)['marker']
                assert len(actions(host)) == before and not jobs(app)
                quiet()

            # Crash after the DNS write but before recording its IDs: recover by read-only proof, never replay apply.
            app, domain, host, base = fixture(); apply(base, host)
            state(domain, zoneId='', recordId='', leaseUntil=1, leaseToken='interrupted-lease', expectedProxied=False, nextAttemptAt=0)
            tick()
            assert state(domain)['state']['zoneId'] == '1' * 32 and state(domain)['state']['recordId']
            assert len(jobs(app)) == 1
            assert [row['action'] for row in actions(host)].count('apply') == 1
            assert not any(row['action'] == 'activate' for row in actions(host))
            quiet()
            app, domain, host, base = fixture(); apply(base, host)
            state(domain, zoneId='', recordId='', leaseUntil=1, leaseToken='interrupted-lease', expectedProxied=False, nextAttemptAt=0)
            control(host, publicProxied=True); tick()
            assert state(domain)['state']['status'] == 'error' and not state(domain)['state'].get('recordId')
            assert not jobs(app) and [row['action'] for row in actions(host)].count('apply') == 1
            quiet()

            # Exhausted propagation retries stop scheduling, without changing the user's proxy preference.
            app, domain, host, base = fixture(); apply(base, host)
            control(host, verifyError=True); state(domain, attempts=7, nextAttemptAt=0); tick()
            assert state(domain)['state']['attempts'] == 8 and state(domain)['state']['status'] == 'error'
            assert state(domain)['state']['enabled'] is True and not state(domain)['marker']
            quiet()

            # Asynchronous ACME failures have their own three-request budget, independent of eight DNS/API errors.
            for late_success in (False, True):
                app, domain, host, base = fixture(); apply(base, host)
                for attempt in range(1, 4):
                    tick(domain)
                    current = state(domain)['state']
                    assert current['certificateAttempts'] == attempt and current['attempts'] == 0
                    pending = [row for row in jobs(app) if row['status'] in ('queued', 'running')]
                    assert len(pending) == 1 and len(jobs(app)) == attempt
                    before = len(actions(host)); tick(domain)
                    assert state(domain)['state']['certificateAttempts'] == attempt and len(jobs(app)) == attempt
                    assert len(actions(host)) == before, 'Polling an active ACME job must not spend another attempt or touch DNS'
                    if late_success and attempt == 3:
                        active(domain, app)
                    else:
                        patch('web_deployments', pending[0]['id'], {'status': 'failed', 'error': 'Synthetic asynchronous ACME failure'})
                        patch('web_app_domains', domain, {'status': 'error', 'lastError': 'Synthetic asynchronous ACME failure'})
                tick(domain)
                current = state(domain)['state']
                assert current['certificateAttempts'] == 3 and len(jobs(app)) == 3 and not state(domain)['marker']
                if late_success:
                    assert current['status'] == 'active'
                    assert [row['action'] for row in actions(host)].count('activate') == 1
                else:
                    assert current['status'] == 'error' and '3' in current['lastError'] and 'HTTPS' in current['lastError']
                    assert not any(row['action'] == 'activate' for row in actions(host))
                    before = len(actions(host)); tick(domain)
                    assert len(actions(host)) == before and len(jobs(app)) == 3
                quiet()

            # Limit work per tick even with more durable pending markers.
            waiting = []
            for _ in range(3):
                app, domain, host, base = fixture(); apply(base, host); state(domain, nextAttemptAt=0)
                waiting.append((app, domain, host))
            tick()
            assert sum(len(jobs(app)) for app, _, _ in waiting) == 2
            assert sum(any(row['action'] == 'verify' for row in actions(host)) for _, _, host in waiting) == 2
            quiet()

            req('/api/web-apps/cloudflare/settings', admin, {'token': 'new-cloudflare-secret-' + 'x' * 30}, method='PUT')
            joined = ''.join(responses)
            assert 'new-cloudflare-secret-' not in joined and 'secret-canary-cloudflare-token' not in joined
            with sqlite3.connect(data / 'data.db') as database:
                value = database.execute("SELECT valueEncrypted FROM web_app_settings WHERE key='cloudflare'").fetchone()[0]
                assert 'new-cloudflare-secret-' not in value
                encrypted = database.execute("SELECT valueEncrypted FROM web_app_settings WHERE key LIKE 'cloudflare-domain:%'").fetchall()
                assert encrypted and all('"actorId"' not in row[0] and '"zoneId"' not in row[0] for row in encrypted)
            print('Native Cloudflare API passed: auth, encrypted state, TLS-first/active proxy, retries, actor revocation, drift, leases, deletion and cycle bound')
        finally:
            process.terminate(); process.wait(timeout=10); log.close()


if __name__ == '__main__':main()
