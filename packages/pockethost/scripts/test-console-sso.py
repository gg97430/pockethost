#!/usr/bin/env python3
"""Validate dashboard-to-console single sign-on against disposable real PocketBase processes."""
import argparse, base64, concurrent.futures, hashlib, hmac, json, os, secrets, shutil, socket, struct, subprocess, tempfile, time, urllib.error, urllib.request
from pathlib import Path

def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); return sock.getsockname()[1]

def totp(secret):
    counter=int(time.time()//30)
    digest=hmac.new(base64.b32decode(secret), struct.pack('>Q',counter), hashlib.sha1).digest()
    return str((struct.unpack('>I',digest[digest[-1]&15:][:4])[0]&0x7fffffff)%1000000).zfill(6)

def request(base,path,token='',body=None,expected=200,method=None):
    req=urllib.request.Request(base+path, method=method or ('POST' if body is not None else 'GET'),headers={'Content-Type':'application/json',**({'Authorization':token} if token else {})},data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req,timeout=30) as response:
            value=json.load(response); assert response.status==expected,(path,response.status,expected); return value
    except urllib.error.HTTPError as error:
        value=json.load(error); assert error.code==expected,(path,error.code,expected,value); return value

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pocketbase',required=True)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--migrations',type=Path,required=True)
    parser.add_argument('--instance-hooks',type=Path,required=True)
    args=parser.parse_args();os.umask(0o077)
    root=Path(tempfile.mkdtemp(prefix='pockethost-console-sso-'));processes=[];logs=[];checks={}
    password=secrets.token_urlsafe(32);email='console-owner@example.invalid'
    def launch(folder,env=None):
        base='http://127.0.0.1:'+str(port());log=(folder/'process.log').open('ab');logs.append(log)
        p=subprocess.Popen([args.pocketbase,'serve','--dir',str(folder/'data'),'--hooksDir',str(folder/'hooks'),'--migrationsDir',str(folder/'migrations'),'--http',base.removeprefix('http://')],env=dict(os.environ,**(env or {})),stdout=log,stderr=log);processes.append(p)
        for _ in range(100):
            if p.poll() is not None:raise RuntimeError('PocketBase exited; see '+str(folder/'process.log'))
            try:request(base,'/api/health');return base
            except urllib.error.URLError:time.sleep(.1)
        raise TimeoutError('PocketBase did not start')
    def initialize(folder,native=False,identity=email):
        (folder/'hooks').mkdir(parents=True);(folder/'migrations').mkdir()
        if native:
            for filename in ['_ph_totp.js','_ph_totp.pb.js','_ph_lib.js']:
                shutil.copy(args.instance_hooks/filename,folder/'hooks'/filename)
        else:
            shutil.copy(args.bundle,folder/'hooks/mothership.js')
            shutil.copytree(args.migrations,folder/'migrations',dirs_exist_ok=True)
            script='onBootstrap((e)=>{e.next();require(`${__hooks}/mothership`).InitializeDashboardTotp()});\n'
            script+='routerUse((e)=>require(`${__hooks}/mothership`).GuardDashboardTotp(e));\n'
            script+='onRecordAuthRequest((e)=>require(`${__hooks}/mothership`).HandleDashboardTotpAuth(e),"users");\n'
            for method,path,handler,auth in [
                ('GET','/api/account/totp','HandleDashboardTotpStatus',True),('POST','/api/account/totp/setup','HandleDashboardTotpSetup',True),('POST','/api/account/totp/confirm','HandleDashboardTotpConfirm',True),
                ('GET','/api/instance/{id}/console','HandleInstanceConsoleInfo',False),('POST','/api/instance/{id}/console','HandleInstanceConsoleTicket',True),('POST','/api/instance/{id}/console/consume','HandleInstanceConsoleConsume',False)]:
                middleware=',$apis.requireAuth("users")' if auth else ''
                script+=f'routerAdd("{method}","{path}",(e)=>require(`${{__hooks}}/mothership`).{handler}(e){middleware});\n'
            script+='routerAdd("POST","/__test/expire",(e)=>{$app.store().setFunc("ph_console_auth_tickets",raw=>{const items=JSON.parse(raw||"{}");Object.values(items).forEach(x=>x.expiresAt=0);return JSON.stringify(items)});return e.json(200,{ok:true})},$apis.requireSuperuserAuth());\n'
            script+='routerAdd("GET","/__test/unmarked/{id}",(e)=>e.json(200,{token:$app.findRecordById("users",e.request.pathValue("id")).newAuthToken()}),$apis.requireSuperuserAuth());\n'
            (folder/'hooks/test.pb.js').write_text(script)
        subprocess.run([args.pocketbase,'superuser','upsert',identity,password,'--dir',str(folder/'data'),'--hooksDir',str(folder/'empty-hooks'),'--migrationsDir',str(folder/'migrations')],check=True,capture_output=True)
    def authenticate(base,identity=email):
        return request(base,'/api/collections/_superusers/auth-with-password',body={'identity':identity,'password':password})
    def enable(base,api,token):
        setup=request(base,api+'/setup',token,{'password':password})
        return request(base,api+'/confirm',token,{'password':password,'code':totp(setup['secret'])})
    report={'work_dir':str(root),'checks':checks}
    try:
        control=root/'dashboard';initialize(control);dashboard=launch(control,{'APEX_DOMAIN':'example.invalid','APP_URL':'https://dashboard.example.invalid'})
        admin=authenticate(dashboard)['token']
        users=[]
        for name in ['owner','other']:
            identity=email if name=='owner' else 'other@example.invalid'
            user=request(dashboard,'/api/collections/users/records',admin,{'email':identity,'password':password,'passwordConfirm':password,'verified':True,'username':name+'test','subscription':'premium','subscription_quantity':10})
            auth=request(dashboard,'/api/collections/users/auth-with-password',body={'identity':identity,'password':password})
            users.append((user,auth['token']))
        owner,token=users[0];enabled=enable(dashboard,'/api/account/totp',token);token=enabled['token'];codes=enabled['recoveryCodes']
        native=[];instances=[]
        for n in range(2):
            instance=request(dashboard,'/api/collections/instances/records',admin,{'uid':owner['id'],'subdomain':'console-probe-'+str(n),'version':'0.39.*','power':True,'status':'idle'})
            instances.append(instance)
            folder=root/('instance'+str(n));identity=email if n==0 else 'machine@example.invalid';initialize(folder,True,identity)
            base=launch(folder,{'PH_INSTANCE_ID':instance['id'],'PH_MOTHERSHIP_URL':dashboard,'PH_CONSOLE_SSO_ALLOW_HTTP':'1'})
            native.append(base)
        native_auth=authenticate(native[0]);native_enabled=enable(native[0],'/api/ph-totp',native_auth['token'])
        instance=instances[0];path='/api/instance/'+instance['id']+'/console'
        info=request(dashboard,path);assert info['url'].endswith('/_/ph-auth') and 'ticket' not in info
        request(dashboard,path,body={},expected=401)
        request(dashboard,path,users[1][1],{},expected=403)
        unmarked=request(dashboard,'/__test/unmarked/'+owner['id'],admin)['token'];request(dashboard,path,unmarked,{},expected=401)
        checks['dashboard_session_mfa_and_ownership_required']=True
        def issue(index=0):return request(dashboard,'/api/instance/'+instances[index]['id']+'/console',token,{})['ticket']
        def exchange(ticket,index=0,status=200):return request(native[index],'/api/ph-console/login',body={'ticket':ticket},expected=status)
        ticket=issue();exchange(ticket,1,401);auth=exchange(ticket)
        assert auth['record']['id']==native_auth['record']['id']
        assert request(native[0],'/api/ph-totp/status',auth['token'])['enabled']
        assert request(native[0],'/api/ph-totp/status',auth['token'])['recoveryCodesRemaining']==10
        request(native[0],'/api/collections',auth['token'])
        refreshed=request(native[0],'/api/collections/_superusers/auth-refresh',auth['token'],{})
        request(native[0],'/api/collections',refreshed['token'])
        exchange(ticket,status=401)
        request(native[0],'/api/collections/_superusers/auth-with-password',body={'identity':email,'password':password},expected=401)
        checks['native_console_opens_without_password_or_totp_and_refreshes']=True
        checks['tickets_are_instance_bound_single_use_and_leave_totp_enabled']=True
        ticket=issue();request(dashboard,'/__test/expire',admin,{})
        exchange(ticket,status=401);checks['expired_tickets_rejected']=True
        ticket=issue()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            def consume(_):
                try:return exchange(ticket)['token']!=''
                except AssertionError:return False
            successes=list(pool.map(consume,range(2)))
        assert sum(successes)==1,successes;checks['concurrent_replay_only_one_succeeds']=True
        ticket=issue()
        changed=secrets.token_urlsafe(32)
        request(dashboard,'/api/collections/users/records/'+owner['id'],admin,{'password':changed,'passwordConfirm':changed},method='PATCH')
        exchange(ticket,status=401)
        token=request(dashboard,'/api/collections/users/auth-with-password',body={'identity':email,'password':changed,'totpCode':codes[0]})['token']
        checks['dashboard_password_change_revokes_pending_ticket']=True
        ticket=issue();request(dashboard,'/api/collections/instances/records/'+instance['id'],admin,{'power':False},method='PATCH');exchange(ticket,status=401)
        request(dashboard,path,token,{},expected=400)
        request(dashboard,'/api/collections/instances/records/'+instance['id'],admin,{'power':True},method='PATCH');checks['powered_off_instance_not_opened']=True
        auth=exchange(issue(1),1)
        assert auth['record']['email'].startswith('dashboard-'),auth['record']
        request(native[1],'/api/collections',auth['token']);authenticate(native[1],'machine@example.invalid')
        checks['disabled_admin_sync_uses_separate_console_identity_preserving_machine_account']=True
        report['result']='passed'
    except Exception as error:
        report['result']='failed';report['error']=str(error);raise
    finally:
        for p in reversed(processes):
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=10)
                except subprocess.TimeoutExpired:p.kill();p.wait(timeout=10)
        for log in logs:log.close()
        (root/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2),flush=True)
if __name__=='__main__':main()
