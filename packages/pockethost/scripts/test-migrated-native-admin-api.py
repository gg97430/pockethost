#!/usr/bin/env python3
"""Synthetic native-controller/native-instance proof; never touches source data."""
import argparse,base64,hashlib,hmac,json,os,pathlib,secrets,shutil,socket,sqlite3,struct,subprocess,tempfile,time,urllib.error,urllib.request
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BASE=pathlib.Path(__file__).resolve().parent
DEFAULT_SOURCE=BASE.parents[2]

def available_port():
    with socket.socket()as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]
def encrypt(value,key):
    nonce=os.urandom(12);return base64.b64encode(nonce+AESGCM(key).encrypt(nonce,json.dumps(value,separators=(',',':')).encode(),None)).decode()
def totp(secret):
    raw=hmac.new(base64.b32decode(secret),struct.pack('>Q',int(time.time())//30),hashlib.sha1).digest();offset=raw[-1]&15
    return str((struct.unpack('>I',raw[offset:offset+4])[0]&0x7fffffff)%1000000).zfill(6)
def database_state(folder):
    connection=sqlite3.connect((folder/'data.db').as_uri()+'?mode=ro',uri=True);connection.row_factory=sqlite3.Row
    admin=dict(connection.execute('SELECT * FROM _superusers').fetchone())
    state=[dict(row)for row in connection.execute('SELECT * FROM ph_two_factor ORDER BY id')]
    connection.close();return {'admin':admin,'mfa':state}

def run(args):
    os.umask(0o077);processes=[];checks=[];master=b'h'*32
    if args.instance_pocketbase is None: args.instance_pocketbase=args.pocketbase
    with tempfile.TemporaryDirectory(prefix='kolab-native-identity-proof-')as temporary:
        root=pathlib.Path(temporary);source=args.source/'packages/pockethost/src/mothership-app'
        empty=root/'empty';empty.mkdir();ctrl=root/'controller';native=root/'native';hooks=root/'controller-hooks';hooks.mkdir()
        migrations=root/'migrations';migrations.mkdir()
        for file in(source/'pb_migrations').glob('*.js'):
            if file.name.split('_')[0].isdigit()and int(file.name.split('_')[0])<=1793272000:shutil.copyfile(file,migrations/file.name)
        shutil.copyfile(source/'pb_hooks/mothership.js',hooks/'mothership.js')
        (hooks/'fixture.pb.js').write_text("""
routerAdd('GET','/api/userToken/{id}',e=>require(`${__hooks}/mothership`).HandleUserTokenRequest(e),$apis.requireSuperuserAuth());
routerAdd('GET','/__direct/userToken/{id}',e=>require(`${__hooks}/mothership`).HandleUserTokenRequest(e));
routerAdd('POST','/api/instance/{id}/console/ticket',e=>require(`${__hooks}/mothership`).HandleInstanceConsoleTicket(e),$apis.requireAuth('users'));
routerAdd('POST','/api/instance/{id}/console/consume',e=>require(`${__hooks}/mothership`).HandleInstanceConsoleConsume(e));
""")
        ctrl_port,native_port=available_port(),available_port();ctrl_url=f'http://127.0.0.1:{ctrl_port}';native_url=f'http://127.0.0.1:{native_port}'
        env={'PATH':'/usr/bin:/bin','HOME':str(root),'LANG':'C.UTF-8','WEB_APPS_MASTER_KEY':master.decode(),'APP_URL':ctrl_url,'APEX_DOMAIN':'fixture.invalid','APP_HOST':'fixture.invalid','WEB_APPS_ENABLED':'true'}
        root_password='P'+secrets.token_urlsafe(30);native_password='N'+secrets.token_urlsafe(30)
        for binary,folder,email,password,migration_dir in [(args.pocketbase,ctrl,'root@example.invalid',root_password,migrations),(args.instance_pocketbase,native,'native-b@example.invalid',native_password,empty)]:
            subprocess.run([str(binary),'superuser','upsert',email,password,'--dir',str(folder),'--hooksDir',str(empty),'--migrationsDir',str(migration_dir)],env=env,capture_output=True,check=True)
        subprocess.run([str(args.pocketbase),'migrate','up','--dir',str(ctrl),'--hooksDir',str(empty),'--migrationsDir',str(migrations)],env=env,capture_output=True,check=True)
        def api(origin,path,token='',body=None,method=None,status=200):
            request=urllib.request.Request(origin+path,data=None if body is None else json.dumps(body).encode(),method=method or('POST'if body is not None else'GET'),headers={'Content-Type':'application/json',**({'Authorization':token}if token else{})})
            try:
                with urllib.request.urlopen(request,timeout=10)as response:
                    raw=response.read();assert response.status==status,(path,response.status,status)
                    return json.loads(raw)if raw else None
            except urllib.error.HTTPError as error:
                raw=error.read()
                if error.code!=status:
                    failure=json.loads(raw)
                    raise AssertionError((path,error.code,status,failure.get('message'),list((failure.get('data')or{}))))from None
                return json.loads(raw)if raw else None
        def start(binary,folder,hook_dir,port,extra=None,label='server'):
            log=(root/(label+'.log')).open('ab')
            process=subprocess.Popen([str(binary),'serve','--http',f'127.0.0.1:{port}','--dir',str(folder),'--hooksDir',str(hook_dir),'--migrationsDir',str(empty)],env={**env,**(extra or{})},stdout=log,stderr=subprocess.STDOUT)
            processes.append(process)
            for _ in range(150):
                if process.poll()is not None:raise RuntimeError(label+' fixture exited')
                try:api(f'http://127.0.0.1:{port}','/api/health');return process
                except OSError:time.sleep(.05)
            raise RuntimeError(label+' fixture timeout')
        def stop(process):
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        try:
            controller=start(args.pocketbase,ctrl,hooks,ctrl_port,label='controller')
            su=api(ctrl_url,'/api/collections/_superusers/auth-with-password',body={'identity':'root@example.invalid','password':root_password})['token']
            def user(name,platform=False):
                record=api(ctrl_url,'/api/collections/users/records',su,{'email':name+'@example.invalid','username':name,'verified':True,'superAdmin':platform,'subscription':'premium','subscription_quantity':20,'password':root_password,'passwordConfirm':root_password})
                token=api(ctrl_url,'/api/collections/users/auth-with-password',body={'identity':record['email'],'password':root_password})['token'];return record,token
            a,a_token=user('owner-a');c,c_token=user('owner-c');operator,operator_token=user('operator',True)
            instance=api(ctrl_url,'/api/collections/instances/records',su,{'subdomain':'imported-b','uid':a['id'],'version':'0.39.11','power':True,'status':'running','syncAdmin':True})
            native_hooks=root/'native-hooks';native_hooks.mkdir()
            original=args.source/'packages/pockethost/src/instance-app/v23/pb_hooks'
            for name in ['_ph_lib.js','_ph_totp.js','_ph_totp.pb.js']:shutil.copyfile(original/name,native_hooks/name)
            native_extra={'PH_INSTANCE_ID':instance['id'],'PH_MOTHERSHIP_URL':ctrl_url,'PH_CONSOLE_SSO_ALLOW_HTTP':'1'}
            native_process=start(args.instance_pocketbase,native,native_hooks,native_port,native_extra,'native-enrollment')
            n_token=api(native_url,'/api/collections/_superusers/auth-with-password',body={'identity':'native-b@example.invalid','password':native_password})['token']
            setup=api(native_url,'/api/ph-totp/setup',n_token,{'password':native_password})
            confirmed=api(native_url,'/api/ph-totp/confirm',n_token,{'password':native_password,'code':totp(setup['secret'])})
            old_token=confirmed['token'];before=database_state(native);n=before['admin']
            assert before['mfa'][0]['enabled']==1
            payload={'ownerId':a['id'],'nativeId':n['id'],'email':n['email'],'tokenKey':n['tokenKey'],'passwordHash':n['password']}
            binding=api(ctrl_url,'/api/collections/web_app_settings/records',su,{'key':'migration:native-admin:'+instance['id'],'valueEncrypted':encrypt(payload,master)})
            for token in ['',a_token,operator_token]:api(ctrl_url,'/api/userToken/'+a['id']+'?instanceId='+instance['id'],token,status=401 if not token else 403)
            for token in ['',a_token,operator_token]:api(ctrl_url,'/__direct/userToken/'+a['id']+'?instanceId='+instance['id'],token,status=403)
            api(ctrl_url,'/api/collections/web_app_settings/records',a_token,status=403)
            resolved=api(ctrl_url,'/api/userToken/'+a['id']+'?instanceId='+instance['id'],su)
            assert resolved=={'id':n['id'],'email':n['email'],'tokenKey':n['tokenKey'],'passwordHash':n['password'],'preserveExistingNativeIdentity':True}
            legacy=api(ctrl_url,'/api/userToken/'+a['id'],su);assert'id'not in legacy and legacy['email']==a['email']
            api(ctrl_url,'/api/userToken/'+c['id']+'?instanceId='+instance['id'],su,status=403)
            checks.append('native_identity_route_requires_technical_superuser_and_exact_owner_instance_pair_legacy_unchanged')
            stop(native_process);shutil.copyfile(original/'_ph_admin_sync.pb.js',native_hooks/'_ph_admin_sync.pb.js')
            # Deliberately wrong proposed credentials prove preservation mode does not write at all.
            admin_sync={**resolved,'email':'must-not-write@example.invalid','tokenKey':'x'*50,'passwordHash':legacy['passwordHash']}
            native_process=start(args.instance_pocketbase,native,native_hooks,native_port,{**native_extra,'ADMIN_SYNC':json.dumps(admin_sync)},'native-preserved')
            assert 'Native admin identity preserved - sync skipped' in (root/'native-preserved.log').read_text()
            after=database_state(native);assert after==before
            assert api(native_url,'/api/ph-totp/status',old_token)['enabled']is True
            checks.append('admin_sync_preservation_performs_zero_native_account_or_mfa_writes_and_old_jwt_remains_valid')
            ticket=api(ctrl_url,'/api/instance/'+instance['id']+'/console/ticket',a_token,{})
            login=api(native_url,'/api/ph-console/login',body={'ticket':ticket['ticket']})
            assert login['record']['id']==n['id']and login['record']['email']==n['email']
            assert api(native_url,'/api/ph-totp/status',login['token'])['enabled']is True
            assert database_state(native)==before
            checks.append('dashboard_console_consumes_owner_capability_as_native_b_identity_without_rotating_mfa_or_credentials')
            pending=api(ctrl_url,'/api/instance/'+instance['id']+'/console/ticket',a_token,{})
            api(ctrl_url,'/api/collections/instances/records/'+instance['id'],su,{'uid':c['id']},'PATCH')
            api(ctrl_url,'/api/instance/'+instance['id']+'/console/consume',body={'ticket':pending['ticket']},status=403)
            changed=api(ctrl_url,'/api/userToken/'+c['id']+'?instanceId='+instance['id'],su)
            assert changed['id']==c['id']and'preserveExistingNativeIdentity'not in changed and changed['email']==c['email']
            api(ctrl_url,'/api/userToken/'+a['id']+'?instanceId='+instance['id'],su,status=403)
            fresh=api(ctrl_url,'/api/instance/'+instance['id']+'/console/ticket',c_token,{})
            consumed=api(ctrl_url,'/api/instance/'+instance['id']+'/console/consume',body={'ticket':fresh['ticket']})
            assert consumed['user']==c['id']and consumed['email']==c['email']
            checks.append('owner_change_revokes_old_console_ticket_and_never_exposes_legacy_native_identity_to_new_owner')
            api(ctrl_url,'/api/collections/instances/records/'+instance['id'],su,{'uid':a['id']},'PATCH')
            api(ctrl_url,'/api/collections/web_app_settings/records/'+binding['id'],su,{'valueEncrypted':'invalid-ciphertext'},'PATCH')
            api(ctrl_url,'/api/userToken/'+a['id']+'?instanceId='+instance['id'],su,status=500)
            assert database_state(native)==before
            checks.append('corrupt_active_mapping_fails_closed_without_fallback_admin_sync')
            result={'passed':True,'checks':checks,'nativeInstanceVersion':subprocess.check_output([str(args.instance_pocketbase),'--version'],text=True).strip(),'sourceRecordsChanged':False,'nativeCredentialsRotatedByMigration':False}
            if args.output:
                args.output.write_text(json.dumps(result,indent=2)+'\n');args.output.chmod(0o600)
            print(json.dumps(result,indent=2))
        finally:
            for process in processes:
                if process.poll()is None:stop(process)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=pathlib.Path,default=DEFAULT_SOURCE);parser.add_argument('--pocketbase',type=pathlib.Path,required=True);parser.add_argument('--instance-pocketbase',type=pathlib.Path);parser.add_argument('--output',type=pathlib.Path);run(parser.parse_args())
