"""Fail early on declared configuration errors; never print credentials or response bodies."""
import http.client,json,pathlib,re,socket,time,threading,urllib.parse

ID=re.compile(r'[a-z0-9]{15}\Z')
VARIABLE=re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,119}\Z')
PUBLIC=re.compile(r'^(NEXT_PUBLIC_|PUBLIC_|VITE_)')
SOCKET_ROOT=pathlib.Path('/var/lib/kolab-db-bridge')


def requirements(rows):
    if not isinstance(rows,list)or len(rows)>100:raise ValueError('Invalid environment contract')
    seen=set()
    for row in rows:
        if (not isinstance(row,dict)or set(row)!={'name','scope'}or not isinstance(row['name'],str)or not VARIABLE.fullmatch(row['name'])or
            row['scope']not in ('build','runtime','both')or row['name']in seen):raise ValueError('Invalid environment contract')
        seen.add(row['name'])
    return rows


def environment_checks(rows,env):
    result=[]
    for row in requirements(rows):
        entry=env.get(row['name'],{});scopes=['build','runtime']if row['scope']=='both'else[row['scope']]
        good=isinstance(entry,dict)and isinstance(entry.get('value'),str)and bool(entry['value'])and all(entry.get(scope)is True for scope in scopes)
        result.append({'name':row['name'],'status':'passed'if good else'failed','code':''if good else'environment_missing','scope':row['scope']})
    return result


def private_request(app,binding,method,path,body=None):
    if (not isinstance(app,str)or not ID.fullmatch(app)or not isinstance(binding,dict)or not ID.fullmatch(binding.get('id',''))or
        not re.fullmatch(r'[A-Za-z0-9]{64}',binding.get('token',''))or method not in ('GET','POST')or
        path!='/api/health'and not re.fullmatch(r'/api/collections/[_a-zA-Z][_a-zA-Z0-9]{0,99}/auth-with-password',path)):
        raise ValueError('Invalid private check')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)as peer:
        def expire():
            try:peer.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        timer=threading.Timer(4,expire);timer.daemon=True;timer.start()
        try:
            peer.settimeout(3);peer.connect(str(SOCKET_ROOT/(app+'.sock')))
            peer.sendall((json.dumps({'version':1,'bindingId':binding['id'],'token':binding['token']})+'\n').encode())
            acknowledgement=bytearray()
            while b'\n'not in acknowledgement and len(acknowledgement)<=1024:
                part=peer.recv(1)
                if not part:raise OSError('Bridge unavailable')
                acknowledgement.extend(part)
            if acknowledgement!=b'OK\n':raise OSError('Bridge refused')
            raw=json.dumps(body).encode()if body is not None else b''
            peer.sendall((method+' '+path+' HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: '+str(len(raw))+'\r\n\r\n').encode()+raw)
            response=http.client.HTTPResponse(peer);response.begin();content=response.read(65537)
            if len(content)>65536:raise ValueError('Response too large')
            if method=='POST':
                data=json.loads(content)
                if response.status==200 and(not isinstance(data,dict)or not isinstance(data.get('token'),str)or not 1<=len(data['token'])<=8192):return 502
            return response.status
        finally:timer.cancel()


def evaluate(app,spec,env,domains,bindings,request=private_request):
    if not isinstance(spec,dict)or spec.get('enabled')is not True:return []
    result=environment_checks(spec.get('requiredEnvironment',[]),env)
    by_id={row['id']:row for row in bindings}
    for item in spec.get('publicUrls',[]):
        name=item['name'];entry=env.get(name,{});good=False
        try:
            parsed=urllib.parse.urlsplit(entry.get('value',''))
            if parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('Credentials or query refused')
            if item['target']=='application':good=parsed.scheme=='https'and parsed.hostname in domains and parsed.port in (None,443)
            else:
                binding=by_id[item['bindingId']]
                good=not PUBLIC.match(name)and entry.get('runtime')is True and parsed.scheme=='http'and parsed.hostname in ('127.0.0.1','localhost')and parsed.port==binding['port']and parsed.path in ('','/')
        except (KeyError,ValueError,TypeError):pass
        result.append({'name':name,'status':'passed'if good else'failed','code':''if good else'url_mismatch'})
    selected=set(by_id)if spec.get('privateDatabases')else set()
    if spec.get('privateDatabases')and not selected:
        result.append({'name':'Liaisons BDD privées','status':'failed','code':'database_unavailable'})
    selected.update(row['bindingId']for row in spec.get('authentication',[]))
    for ident in sorted(selected):
        binding=by_id.get(ident);good=False
        if binding and binding.get('available'):
            try:good=request(app,binding,'GET','/api/health')==200
            except (OSError,ValueError,http.client.HTTPException):pass
        result.append({'name':'Liaison BDD '+ident,'status':'passed'if good else'failed','code':''if good else'database_unavailable'})
    for item in spec.get('authentication',[]):
        binding=by_id.get(item['bindingId']);good=False
        identity_entry=env.get(item['identityEnv'],{});password_entry=env.get(item['passwordEnv'],{})
        identity=identity_entry.get('value','');password=password_entry.get('value','')
        if binding and binding.get('available')and identity and password and identity_entry.get('runtime')is True and password_entry.get('runtime')is True and not PUBLIC.match(item['passwordEnv']):
            try:good=request(app,binding,'POST','/api/collections/'+item['collection']+'/auth-with-password',{'identity':identity,'password':password})==200
            except (OSError,ValueError,http.client.HTTPException):pass
        result.append({'name':'Authentification BDD '+item['bindingId'],'status':'passed'if good else'failed','code':''if good else'database_authentication'})
    return result


def report(job,results):
    failed=[row for row in results if row['status']=='failed']
    if results:job.log('Contrôles avant déploiement : '+str(len(results)-len(failed))+'/'+str(len(results))+' réussis.\n')
    for row in failed:job.log('Contrôle refusé : '+row['name']+' ('+row['code']+').\n')
    if failed:raise RuntimeError('Les contrôles avant déploiement ont échoué. Corrigez la configuration indiquée dans le journal ; la version publiée est conservée.')


def run(job):
    spec=(job.snapshot.get('checks')or{}).get('preflight',{})
    if spec.get('enabled')is not True:return
    bindings=[]
    if spec.get('privateDatabases')or spec.get('authentication')or any(row['target']=='privateDatabase'for row in spec.get('publicUrls',[])):
        import web_app_private_db as private
        if private.enabled()and private._manager is not None:
            try:bindings=private._manager.for_app(job.app,force=True)
            except Exception:raise RuntimeError('Les liaisons privées ne peuvent pas être vérifiées avant le déploiement.')from None
    hosts={row['host']for row in job.snapshot.get('domains',[])if isinstance(row,dict)and isinstance(row.get('host'),str)}
    generated=job.snapshot.get('config',{}).get('generatedDomain')
    if generated:hosts.add(generated)
    with job.blocking_heartbeat():results=evaluate(job.app,spec,job.snapshot.get('env',{}),hosts,bindings)
    report(job,results)


def source_contract(job,context):
    directory=(pathlib.Path(context)/(job.snapshot.get('config',{}).get('rootDirectory')or'.')).resolve()
    root=pathlib.Path(context).resolve()
    if not directory.is_relative_to(root):raise RuntimeError('Le contrat de variables doit rester dans le dépôt.')
    file=directory/'.kolab/checks.json'
    if not file.exists():return
    try:
        if file.is_symlink()or not file.resolve().is_relative_to(root)or file.stat().st_size>65536:raise ValueError('Invalid contract')
        value=json.loads(file.read_bytes())
        if not isinstance(value,dict)or set(value)!={'version','requiredEnvironment'}or type(value['version'])is not int or value['version']!=1:raise ValueError('Invalid contract')
        results=environment_checks(value['requiredEnvironment'],job.snapshot.get('env',{}))
    except (ValueError,TypeError,KeyError,OSError):raise RuntimeError('Le contrat .kolab/checks.json est invalide (version et variables obligatoires attendues).')from None
    report(job,results)
