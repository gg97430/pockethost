#!/usr/bin/env python3
"""Disposable native controller under NoNewPrivileges, with real mandatory TOTP.

--installed-read-only tests the installed operator using only status and rejected
writes. --isolated tests real memory/pause writes through a private test socket,
temporary policies and a labelled disposable Docker container. No customer
account, policy, data, volume, process or admission lease is changed.
"""
import argparse,base64,hashlib,hmac,json,os,pathlib,re,secrets,shutil,socket,sqlite3,struct,subprocess,tempfile,time,urllib.error,urllib.request

SCRIPT_DIR=pathlib.Path(__file__).resolve().parent


def run(args,**kwargs):
    return subprocess.run(args,check=True,capture_output=True,text=True,timeout=kwargs.pop('timeout',20),**kwargs)


class NativeFixture:
    def __init__(self):self.process=None
    def start(self,source,binary,root,client):
        self.root=root;self.process=None;self.env={**os.environ,'DATA_ROOT':str(root/'data'),'APEX_DOMAIN':'example.invalid','WEB_APPS_ENABLED':'false'}
        self.source=source/'packages/pockethost/src/mothership-app';self.password='P'+secrets.token_urlsafe(32)
        root.mkdir();root.chmod(0o700);os.chown(root,1000,1000)
        hooks=root/'hooks';hooks.mkdir();hooks.chmod(0o755)
        compiled=(self.source/'pb_hooks/mothership.js').read_text()
        (hooks/'mothership.js').write_text(compiled.replace('/opt/pockethost-web/platform_operator_socket.py',str(client)))
        (hooks/'mothership.js').chmod(0o644)
        routes=[('GET','/api/admin/controls','HandleOperatorControls'),('PUT','/api/admin/controls/memory','HandleOperatorMemory'),('PUT','/api/admin/controls/maintenance','HandleOperatorDeploymentMaintenance'),('POST','/api/account/totp/setup','HandleDashboardTotpSetup'),('POST','/api/account/totp/confirm','HandleDashboardTotpConfirm')]
        code='\n'.join('routerAdd('+json.dumps(method)+','+json.dumps(path)+',e=>require(`${__hooks}/mothership`).'+handler+'(e),$apis.requireAuth("users"));'for method,path,handler in routes)
        code+='\nonBootstrap(e=>{e.next();require(`${__hooks}/mothership`).InitializeDashboardTotp()});routerUse(e=>require(`${__hooks}/mothership`).GuardDashboardTotp(e));onRecordAuthRequest(e=>require(`${__hooks}/mothership`).HandleDashboardTotpAuth(e),"users");\n'
        (hooks/'fixture.pb.js').write_text(code);(hooks/'fixture.pb.js').chmod(0o644)
        with socket.socket()as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        self.origin='http://127.0.0.1:'+str(port);self.database=root/'pb_data/data.db'
        su_email='operator-'+secrets.token_hex(12)+'@example.invalid'
        identity={'user':1000,'group':1000,'extra_groups':[]}
        run([binary,'superuser','upsert',su_email,self.password,'--dir',str(root/'pb_data'),'--hooksDir',str(root/'empty'),'--migrationsDir',str(self.source/'pb_migrations')],env=self.env,**identity)
        log=(root/'process.log').open('wb')
        try:self.process=subprocess.Popen(['setpriv','--no-new-privs',binary,'serve','--http','127.0.0.1:'+str(port),'--dir',str(root/'pb_data'),'--hooksDir',str(hooks),'--migrationsDir',str(self.source/'pb_migrations')],env=self.env,stdout=log,stderr=subprocess.STDOUT,**identity)
        finally:log.close()
        for _ in range(100):
            if self.process.poll()is not None:raise RuntimeError('Disposable controller failed')
            try:self.api('/api/health');break
            except OSError:time.sleep(.1)
        assert 'NoNewPrivs:\t1'in pathlib.Path('/proc/'+str(self.process.pid)+'/status').read_text()
        self.su=self.api('/api/collections/_superusers/auth-with-password',body={'identity':su_email,'password':self.password})['token']
        self.api('/api/admin/controls',status=401);self.api('/api/admin/controls',self.su,status=403)
        self.tokens={};self.admin=None
        for label,admin,suspension in [('admin',True,''),('member',False,''),('suspended',True,'blocked')]:
            email=label+'-'+secrets.token_hex(10)+'@example.invalid'
            user=self.api('/api/collections/users/records',self.su,{'email':email,'username':'operator'+label,'password':self.password,'passwordConfirm':self.password,'verified':True,'superAdmin':admin,'suspension':suspension,'subscription':'premium','subscription_quantity':1})
            token=self.api('/api/collections/users/auth-with-password',body={'identity':email,'password':self.password})['token']
            for path,body,method in [('/api/admin/controls',None,'GET'),('/api/admin/controls/memory',{},'PUT'),('/api/admin/controls/maintenance',{'paused':True,'seconds':30},'PUT')]:
                denied=self.api(path,token,body,method,403)
                assert denied['data']['totpEnrollmentRequired']['code']=='totp_enrollment_required'
            setup=self.api('/api/account/totp/setup',token,{'password':self.password});key=base64.b32decode(setup['secret']+'='*(-len(setup['secret'])%8))
            digest=hmac.new(key,struct.pack('>Q',int(time.time())//30),hashlib.sha1).digest();offset=digest[-1]&15
            otp=str((struct.unpack('>I',digest[offset:offset+4])[0]&0x7fffffff)%1000000).zfill(6)
            self.tokens[label]=self.api('/api/account/totp/confirm',token,{'password':self.password,'code':otp})['token']
            if label=='admin':self.admin=user
        for label in ['member','suspended']:
            for path,body,method in [('/api/admin/controls',None,'GET'),('/api/admin/controls/memory',{},'PUT'),('/api/admin/controls/maintenance',{'paused':True,'seconds':30},'PUT')]:self.api(path,self.tokens[label],body,method,403)
        self.ident=secrets.token_hex(8)[:15]
        self.api('/api/collections/instances/records',self.su,{'id':self.ident,'uid':self.admin['id'],'subdomain':'operator-qa-'+secrets.token_hex(4),'version':'0.39.*','power':False,'status':'idle'})

    def api(self,path,token='',body=None,method=None,status=200):
        request=urllib.request.Request(self.origin+path,method=method or('POST'if body is not None else'GET'),data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json',**({'Authorization':token}if token else{})})
        try:
            with urllib.request.urlopen(request,timeout=35)as response:
                assert response.status==status,(path,response.status,status)
                return json.load(response)
        except urllib.error.HTTPError as error:
            data=error.read();assert error.code==status,(path,error.code,status,data.decode()[:500])
            return json.loads(data)

    def controls(self,path='',body=None,status=200):return self.api('/api/admin/controls'+path,self.tokens['admin'],body,'GET'if body is None else'PUT',status)
    def close(self):
        if self.process:
            self.process.terminate()
            try:self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()


def installed_readonly(fixture):
    value=fixture.controls();assert re.fullmatch('[a-f0-9]{64}',value['revision'])and isinstance(value['databases'],list)
    assert not any(row['id']==fixture.ident for row in value['databases'])
    body={'id':fixture.ident,'mode':'fixed','maximumMiB':1024,'revision':value['revision']}
    fixture.controls('/memory',{**body,'revision':'0'*64},409)
    fixture.controls('/memory',body,400)
    fixture.controls('/maintenance',{'paused':True,'seconds':0},400)
    fixture.controls('/maintenance',{'paused':False,'owner':'invalid-owner'},400)
    assert fixture.controls()['revision']==value['revision']
    return {'installedReadOnly':True,'databaseCount':len(value['databases']),'rejectedWritesLeaveRevisionUnchanged':True}


def isolated(fixture,root,source,socket_path):
    cid=None;name=pathlib.Path(socket_path).name.removesuffix('.sock');units=[]
    private=root/'private';private.mkdir(mode=0o700);helpers=root/'helpers';helpers.mkdir(mode=0o755)
    for filename in ['platform_operator.py','platform_operator_socket.py']:
        shutil.copyfile(source/'packages/pockethost/scripts'/filename,helpers/filename);(helpers/filename).chmod(0o644)
    for filename in ['platform_memory_allocator.py','database_memory_reservations.py','database_memory_management.py','platform_maintenance.py']:
        shutil.copyfile(pathlib.Path('/opt/pockethost-web')/filename,helpers/filename);(helpers/filename).chmod(0o644)
    config=private/'configuration.json';policy=private/'reservations.json';management=private/'management.json';report=private/'status.json';admission=private/'admission/admission.json'
    config.write_text(json.dumps({'database':str(fixture.database),'instancesRoot':str(root/'instances'),'webRoot':str(root),'webMaximumMiB':8192,'databaseMaximumMiB':16384}))
    for p in [policy,management]:p.write_text('{"version":1,"databases":{}}')
    report.write_text(json.dumps({'checkedAt':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'resources':[]}))
    for p in [config,policy,management,report]:p.chmod(0o600)
    wrapper=helpers/'server.py'
    wrapper.write_text("import sys,pathlib\nsys.path.insert(0,"+repr(str(helpers))+ ")\nimport platform_operator as op,database_memory_reservations as r,database_memory_management as d,platform_maintenance as a,platform_operator_socket as s\nop.CONFIG=pathlib.Path("+repr(str(config))+");op.STATE=pathlib.Path("+repr(str(report))+")\nop.POLICY=r.POLICY=pathlib.Path("+repr(str(policy))+");op.MANAGEMENT=d.PATH=pathlib.Path("+repr(str(management))+");d.LOCK=pathlib.Path("+repr(str(admission.parent/'memory.lock'))+")\nq=pathlib.Path("+repr(str(admission))+")\nop.maintenance_status=lambda:a.status(q)\nop.acquire=lambda owner,seconds:a.acquire(owner,seconds,q)\nop.release=lambda owner:a.release(owner,q)\ns.main()\n");wrapper.chmod(0o644)
    token=secrets.token_hex(16);folder=root/'instances'/fixture.ident;folder.mkdir(parents=True)
    native_ids=json.loads(run(['python3','-I','-c',"import sqlite3,json\nwith sqlite3.connect('file:/home/ubuntu/.local/share/pockethost/data/mothership/pb_data/data.db?mode=ro',uri=True)as db:print(json.dumps([r[0]for r in db.execute('SELECT id FROM instances')]))"],user=1000,group=1000,extra_groups=[]).stdout)
    assert fixture.ident not in native_ids
    running=run(['docker','ps','--format','{{.Names}}']).stdout.splitlines();native=next(ident for ident in native_ids if ident in running)
    image=run(['docker','inspect','--format','{{.Image}}',native]).stdout.strip();assert re.fullmatch('sha256:[a-f0-9]{64}',image)
    real_paths=[pathlib.Path('/etc/kolab/database-memory-reservations.json'),pathlib.Path('/etc/kolab/database-memory-management.json'),pathlib.Path('/run/kolab-platform-maintenance/admission.json')]
    before_policies={str(p):p.read_bytes()if p.exists()else None for p in real_paths}
    try:
        cid=run(['docker','run','--pull','never','-d','--name',fixture.ident,'--label','kolab.operator-qa='+token,'--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','16','--cpus','.1','--memory','512m','--memory-swap','-1','--mount','type=bind,src='+str(folder)+',dst=/home/pockethost','--entrypoint','/bin/sh',image,'-c','sleep 240']).stdout.strip()
        before=json.loads(run(['docker','inspect',cid]).stdout)[0]
        for suffix in ['.socket','@.service']:
            raw=(source/'packages/pockethost/scripts/systemd'/('pockethost-platform-operator'+suffix)).read_text().replace('/run/kolab-platform-operator.sock',socket_path).replace('/opt/pockethost-web/platform_operator_socket.py',str(wrapper))
            if suffix=='@.service':raw+='\nReadWritePaths='+str(root)+'\n'
            path=pathlib.Path('/run/systemd/system')/(name+suffix);path.write_text(raw);path.chmod(0o644);units.append(path)
        run(['systemctl','daemon-reload']);run(['systemctl','start',name+'.socket'])
        state=fixture.controls();assert len(state['databases'])==1 and state['databases'][0]['id']==fixture.ident
        body={'id':fixture.ident,'mode':'fixed','maximumMiB':1024,'revision':state['revision']}
        result=fixture.controls('/memory',body);assert result['restartRequired']is False
        actual=json.loads(run(['docker','inspect',cid]).stdout)[0];assert actual['HostConfig']['Memory']==actual['HostConfig']['MemoryReservation']==1024*1024**2
        fixture.controls('/memory',body,409)
        state=fixture.controls();assert state['databases'][0]['mode']=='fixed'
        fixture.controls('/memory',{'id':fixture.ident,'mode':'adaptive','maximumMiB':state['adaptiveMaximumMiB'],'revision':state['revision']})
        actual=json.loads(run(['docker','inspect',cid]).stdout)[0];assert actual['HostConfig']['MemoryReservation']==6*1024**2
        state=fixture.controls();assert state['databases'][0]['mode']=='adaptive'
        fixture.api('/api/collections/instances/records/'+fixture.ident,fixture.su,{'migrationLock':'qa-lock'},'PATCH')
        fixture.controls('/memory',{'id':fixture.ident,'mode':'fixed','maximumMiB':1024,'revision':state['revision']},409)
        assert fixture.controls()['revision']==state['revision']
        fixture.api('/api/collections/instances/records/'+fixture.ident,fixture.su,{'migrationLock':''},'PATCH')
        fixture.controls('/maintenance',{'paused':True,'seconds':30});state=fixture.controls()
        assert state['maintenance']['paused']and state['maintenance']['owner'].startswith('admin-'+fixture.admin['id']+'-')
        fixture.controls('/maintenance',{'paused':False,'owner':'admin-another-owner'},503)
        assert fixture.controls()['maintenance']['owner']==state['maintenance']['owner']
        fixture.controls('/maintenance',{'paused':False,'owner':state['maintenance']['owner']});assert fixture.controls()['maintenance']['paused']is False
        after=json.loads(run(['docker','inspect',cid]).stdout)[0]
        assert before['Id']==after['Id']and before['State']['Pid']==after['State']['Pid']and before['State']['StartedAt']==after['State']['StartedAt']and after['State']['Running']
        assert before['HostConfig']['NanoCpus']==after['HostConfig']['NanoCpus']and after['HostConfig']['NetworkMode']=='none'
        assert all(p.stat().st_uid==1000 for p in fixture.database.parent.glob('data.db*'))
        assert before_policies=={str(p):p.read_bytes()if p.exists()else None for p in real_paths}
        audits=fixture.api('/api/collections/instance_action_log/records?perPage=50',fixture.su)['items']
        assert len(audits)==2 and all(row['action']=='memory.updated'and row['actorId']==fixture.admin['id']for row in audits)
        return {'fixedThenAdaptive':True,'samePidAndStartedAt':True,'migrationLockAndRevisionEnforced':True,'pauseResumeIsolated':True,'customerPoliciesAndAdmissionUnchanged':True,'sqliteFilesUid1000':True,'successfulWritesAudited':True}
    finally:
        try:
            if units:run(['systemctl','stop',name+'.socket',name+'@*.service'])
        finally:
            for path in units:path.unlink(missing_ok=True)
            if units:run(['systemctl','daemon-reload']);subprocess.run(['systemctl','reset-failed',name+'@*.service'],capture_output=True)
            if cid:
                value=json.loads(run(['docker','inspect',cid]).stdout)[0];assert value['Name']=='/'+fixture.ident and value['Config']['Labels'].get('kolab.operator-qa')==token
                run(['docker','rm','-f',cid])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=pathlib.Path,required=True);parser.add_argument('--pocketbase',required=True)
    parser.add_argument('--installed-read-only',action='store_true');parser.add_argument('--isolated',action='store_true');args=parser.parse_args()
    assert os.geteuid()==0 and args.installed_read_only!=args.isolated
    root=pathlib.Path(tempfile.mkdtemp(prefix='kolab-operator-api-',dir='/var/lib'));root.chmod(0o755);fixture=NativeFixture()
    try:
        client=pathlib.Path('/opt/pockethost-web/platform_operator_socket.py')
        if args.isolated:
            # The random socket path is fixed in this immutable TEST COPY only.
            client=root/'client.py';raw=(args.source/'packages/pockethost/scripts/platform_operator_socket.py').read_text()
            socket_name='kolab-operator-qa-client-'+secrets.token_hex(6)
            client.write_text(raw.replace('/run/kolab-platform-operator.sock','/run/'+socket_name+'.sock'));client.chmod(0o644)
        fixture.start(args.source,args.pocketbase,root/'native',client)
        if args.isolated:
            # Share the path with the separate root test unit, without adding a production override.
            raw=client.read_text();socket_path=re.search("SOCKET = pathlib.Path\\('([^']+)'\\)",raw)[1]
            result=isolated(fixture,root,args.source,socket_path)
        else:result=installed_readonly(fixture)
        print(json.dumps({'passed':True,'realControllerUid1000NoNewPrivileges':True,'mandatoryTotpAndRoleRefusals':True,**result}))
    finally:
        if fixture:fixture.close()
        shutil.rmtree(root)


if __name__=='__main__':main()
