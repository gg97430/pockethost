"""Application-only named volumes, bounded snapshots, and monitored growth limits."""
import contextlib
import hashlib
import hmac
import io
import datetime
import shutil
import json
import os
import pathlib
import re
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zlib
from web_app_backup_s3 import BackupS3Error

NAME=re.compile(r'^[a-z][a-z0-9-]{0,30}$')
ID=re.compile(r'^[a-z0-9]{15}$')
MAGIC=b'PHWV1'


def validate_volumes(value):
    if not isinstance(value,list)or len(value)>4:raise RuntimeError('Quatre volumes au maximum par site.')
    seen=set();result=[]
    for item in value:
        if not isinstance(item,dict)or set(item)-{'name','target','sizeMiB','readOnly'}or not NAME.fullmatch(item.get('name',''))or item['name']in seen:raise RuntimeError('Nom ou options de volume invalides.')
        target=item.get('target','')
        if not isinstance(target,str)or (target!='/app/.next/cache'and not re.fullmatch(r'/(?:data|mnt|app/(?:data|uploads|storage))(?:/[A-Za-z0-9_.-]+)*',target))or any(part in ('.','..')for part in target.split('/')):raise RuntimeError('Le volume doit cibler /data, /mnt, data/uploads/storage de l’application ou le cache Next exact /app/.next/cache.')
        size=item.get('sizeMiB',256)
        if type(size)is not int or not 16<=size<=2048 or type(item.get('readOnly',False))is not bool:raise RuntimeError('Limite de volume invalide (16 à 2048 Mio).')
        if any(target==row['target']or target.startswith(row['target']+'/')or row['target'].startswith(target+'/')for row in result):raise RuntimeError('Les montages ne peuvent pas se recouvrir.')
        seen.add(item['name']);result.append({'name':item['name'],'target':target,'sizeMiB':size,'readOnly':item.get('readOnly',False)})
    if sum(item['sizeMiB']for item in result)>4096:raise RuntimeError('Le budget des volumes du site est limité à 4 Gio.')
    return result


def _file(root,app):
    if not ID.fullmatch(app):raise RuntimeError('Application de stockage invalide.')
    path=pathlib.Path(root).absolute()/'storage'/(app+'.json')
    if path.resolve()!=path:raise RuntimeError('Manifeste de stockage symbolique refusé.')
    return path


def _load(root,app):
    path=_file(root,app)
    if not path.exists():return {'version':1,'appId':app,'volumes':{}}
    if path.stat().st_size>65536:raise RuntimeError('Manifeste de stockage trop volumineux.')
    value=json.loads(path.read_text())
    if value.get('appId')!=app or value.get('version')!=1 or not isinstance(value.get('volumes'),dict):raise RuntimeError('Manifeste de stockage invalide.')
    for name,item in value['volumes'].items():
        if not NAME.fullmatch(name)or not isinstance(item,dict)or not re.fullmatch('ph-web-data-'+app+'-'+name+r'-(?:v1|r[a-z0-9]{15})',item.get('activeVolume','')):raise RuntimeError('Référence de volume étrangère refusée.')
    return value


def _save(root,app,value):
    path=_file(root,app);path.parent.mkdir(mode=0o750,parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+os.urandom(8).hex()+'.tmp')
    try:
        with open(temporary,'x')as out:os.chmod(temporary,0o640);json.dump(value,out);out.flush();os.fsync(out.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def _helper(docker):
    image=os.environ.get('WEB_APPS_STORAGE_HELPER_IMAGE','')
    if not re.fullmatch('sha256:[a-f0-9]{64}',image):raise RuntimeError('Configurez l’image d’outillage stockage approuvée par son identifiant SHA256.')
    info=json.loads(docker(['image','inspect',image],timeout=10))[0]
    if info.get('Id')!=image:raise RuntimeError('Image d’outillage stockage incorrecte.')
    return image


def _helper_args(docker,app,volume,args,write=False,initialize=False,stdin=False):
    if not (re.fullmatch('ph-web-data-'+app+r'-[a-z][a-z0-9-]{0,30}-(?:v1|r[a-z0-9]{15})',volume)or re.fullmatch('ph-web-next-'+app+r'-[a-z0-9]{15}(?:-[a-z][a-z0-9-]{0,30})?',volume)):raise RuntimeError('Volume étranger refusé.')
    return ['run',*(['--interactive']if stdin else[]),'--rm','--network','none','--read-only','--user','0:0'if initialize else'10001:10001','--cap-drop','ALL',*(['--cap-add','CHOWN','--cap-add','FOWNER']if initialize else[]),
        '--security-opt','no-new-privileges','--memory','64m','--memory-swap','64m','--cpus','0.1','--pids-limit','32',
        '--label','pockethost.storage-helper=true','--label','pockethost.web.app='+app,
        '--mount','type=volume,source='+volume+',target=/volume'+(''if write else',readonly'),_helper(docker),*args]


def _create(docker,app,name,volume):
    try:
        info=json.loads(docker(['volume','inspect',volume]))[0]
        labels=info.get('Labels')or{}
        if labels.get('pockethost.web.app')!=app or labels.get('pockethost.web.volume')!=name or info.get('Driver')!='local' or info.get('Options'):raise RuntimeError('Volume existant étranger ou bind indirect refusé.')
    except subprocess.CalledProcessError:
        docker(['volume','create','--label','pockethost.web=true','--label','pockethost.web.app='+app,'--label','pockethost.web.volume='+name,volume])
    docker(_helper_args(docker,app,volume,['chown','10001:10001','/volume'],write=True,initialize=True),timeout=15)


def prepare_mounts(root,app,volumes,service_mounts,docker):
    volumes=validate_volumes(volumes)
    if not volumes:
        if service_mounts:raise RuntimeError('Compose référence un volume non enregistré.')
        return []
    manifest=_load(root,app);selected=[]
    if service_mounts is None:selected=[(item,item['target'],item['readOnly'])for item in volumes]
    else:
        for text in service_mounts:
            parts=text.split(':');item=next((item for item in volumes if item['name']==parts[0]),None)
            if not item:raise RuntimeError('Compose référence un volume non enregistré.')
            validate_volumes([{**item,'target':parts[1]}]);selected.append((item,parts[1],item['readOnly']or len(parts)==3))
    validate_volumes([{**item,'target':target,'readOnly':readonly}for item,target,readonly in selected])
    for item,target,readonly in selected:
        state=manifest['volumes'].setdefault(item['name'],{'activeVolume':'ph-web-data-'+app+'-'+item['name']+'-v1'})
        _create(docker,app,item['name'],state['activeVolume']);state.update(item)
    _save(root,app,manifest)
    return [{'Type':'volume','Source':manifest['volumes'][item['name']]['activeVolume'],'Target':target,'ReadOnly':readonly,'VolumeOptions':{'NoCopy':True}}for item,target,readonly in selected]


def _measure(docker,app,volume):
    info=json.loads(docker(['volume','inspect',volume]))[0]
    if info.get('Driver')!='local'or info.get('Options')or info.get('Labels',{}).get('pockethost.web.app')!=app:raise RuntimeError('Volume de mesure étranger ou bind indirect refusé.')
    value=docker(_helper_args(docker,app,volume,['du','-sk','/volume']),timeout=15).split()[0]
    if not value.isdigit():raise RuntimeError('Mesure du volume indisponible.')
    return int(value)*1024


def _private_database_mounts(info,app):
    """Only the three read-only mounts installed by the trusted private DB wrapper."""
    config=info.get('Config')or{};host=info.get('HostConfig')or{};labels=config.get('Labels')or{}
    observed=[mount for mount in info.get('Mounts',[])if mount.get('Type')=='bind']
    defined=[mount for mount in host.get('Mounts',[])or[]if mount.get('Type')=='bind']
    if not observed and not defined:return labels.get('pockethost.web.private-db.wait')!='true'
    deployment=labels.get('pockethost.web.deployment','');service=labels.get('pockethost.web.service')or'web'
    if labels.get('pockethost.web.private-db.wait')!='true'or labels.get('pockethost.web.private-db')!='false'or not ID.fullmatch(deployment)or not NAME.fullmatch(service):return False
    base='ph-web-'+app+'-'+deployment;front=labels.get('pockethost.web.front')
    expected_name=base if front=='true'or(front is None and service=='web')else base+'-'+service if front=='false'else''
    if info.get('Name','').lstrip('/')!=expected_name:return False
    root=pathlib.Path(os.environ.get('WEB_APPS_ROOT','/home/pockethost-web/data'))
    from web_app_private_db import SCRIPT_ROOT
    home=root/'private-db-runtime'/app/(deployment+'-'+service)
    expected={str(home/'ready'):'/run/kolab-ready',str(home/'wait-env'):'/run/kolab-wait-env',str(SCRIPT_ROOT/'web_private_db_wait.cjs'):'/run/kolab-wait.cjs'}
    try:
        if not root.is_absolute()or root.resolve(strict=True)!=root:return False
        for source,target in expected.items():
            path=pathlib.Path(source)
            if path.resolve(strict=True)!=path or(path.is_dir()if target!='/run/kolab-wait.cjs'else path.is_file())is not True:return False
    except OSError:return False
    return(len(observed)==len(defined)==3
        and all(mount.get('RW')is False and isinstance(mount.get('Source'),str)and mount['Source']in expected and expected[mount['Source']]==mount.get('Destination')for mount in observed)
        and len({mount['Destination']for mount in observed})==3
        and all(mount.get('ReadOnly')is True and isinstance(mount.get('Source'),str)and mount['Source']in expected and expected[mount['Source']]==mount.get('Target')for mount in defined)
        and len({mount['Target']for mount in defined})==3)


def restartable_container(info,app,running=True):
    config=info.get('Config')or{};host=info.get('HostConfig')or{};labels=config.get('Labels')or{}
    if not ID.fullmatch(app)or not re.fullmatch('[a-f0-9]{64}',info.get('Id',''))or not re.fullmatch('ph-web-'+app+'-[a-z0-9]{15}(?:-[a-z][a-z0-9-]{0,30})?',info.get('Name','').lstrip('/'))or labels.get('pockethost.web.app')!=app or labels.get('pockethost.web')!='true'or labels.get('pockethost.web.private-db')=='true'or config.get('User')!='10001:10001'or host.get('Privileged')or not host.get('ReadonlyRootfs')or host.get('Binds')or not _private_database_mounts(info,app):
        raise RuntimeError('Conteneur étranger ou isolation invalide avant la pause du stockage.')
    if info.get('State',{}).get('Paused'):raise RuntimeError('Un service est déjà gelé. Rétablissez son état avant le snapshot.')
    bindings=host.get('PortBindings')or{};actual=info.get('NetworkSettings',{}).get('Ports')or{};targets=[]
    for key,rows in bindings.items():
        if not rows:continue
        if not re.fullmatch('[1-9][0-9]{0,4}/tcp',key)or len(rows)!=1 or rows[0].get('HostIp')!='127.0.0.1'or not re.fullmatch('[1-9][0-9]{3,4}',str(rows[0].get('HostPort','')))or not 1024<=int(rows[0]['HostPort'])<=65535:
            raise RuntimeError('Arrêtez puis redéployez cette application avant le snapshot : son ancien port dynamique ne peut pas être conservé à la reprise.')
        expected=[{'HostIp':'127.0.0.1','HostPort':str(rows[0]['HostPort'])}]
        if running and actual.get(key)!=expected:raise RuntimeError('Le port réel ne correspond pas au port fixe publié. Aucun service n’a été arrêté.')
        targets.append('http://127.0.0.1:'+str(rows[0]['HostPort']))
    if any(rows and key not in bindings for key,rows in actual.items()):raise RuntimeError('Publication réseau inattendue avant la pause du stockage.')
    return targets


def _wait_resumed(docker,app,ident,targets,timeout=30):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    deadline=time.monotonic()+timeout;good=0
    while time.monotonic()<deadline:
        info=json.loads(docker(['inspect',ident]))[0]
        if info.get('Id')!=ident:raise RuntimeError('L’identité du service a changé pendant la reprise.')
        current=restartable_container(info,app,running=bool(info.get('State',{}).get('Running')))
        if current!=targets:raise RuntimeError('Le port publié a changé pendant la reprise du stockage.')
        ready=info.get('State',{}).get('Running')and info.get('State',{}).get('Health',{}).get('Status','healthy')=='healthy'
        path=(info.get('Config',{}).get('Labels')or{}).get('pockethost.web.healthpath','/')
        if not re.fullmatch(r'/[A-Za-z0-9_./-]{0,200}',path):raise RuntimeError('Chemin de santé du service invalide pendant la reprise.')
        if ready:
            for target in targets:
                try:
                    with opener.open(target+path,timeout=1)as response:ok=200<=response.status<400
                except urllib.error.HTTPError as error:ok=200<=error.code<400
                except OSError:ok=False
                ready=ready and ok
        good=good+1 if ready else 0
        if good>=2:return
        time.sleep(.2)
    raise RuntimeError('La reprise du service n’est pas saine sur son port publié inchangé.')


@contextlib.contextmanager
def quiesce(docker,app,resume=True,journal=None):
    previous=[];success=False
    try:
        candidates=[]
        for ident in docker(['ps','-q','--no-trunc','--filter','label=pockethost.web=true','--filter','label=pockethost.web.app='+app]).split():
            info=json.loads(docker(['inspect',ident]))[0]
            if info.get('Id')!=ident:raise RuntimeError('Identité du service invalide avant le snapshot.')
            labels=info.get('Config',{}).get('Labels')or{}
            if labels.get('pockethost.web.app')!=app or labels.get('pockethost.web')!='true':raise RuntimeError('Service étranger avant le snapshot.')
            if labels.get('pockethost.web.private-db')=='true':continue
            restartable_container(info,app);candidates.append(ident)
        if len(candidates)>4:raise RuntimeError('Le snapshot accepte au maximum quatre services publiés.')
        for ident in candidates:
            previous.append(ident)
            if journal:journal(previous)
            docker(['stop','-t','30',ident],timeout=40)
        yield previous;success=True
    finally:
        if (resume or not success)and previous:_resume(docker,app,previous)


def _object(config,app,name,backup):
    if not ID.fullmatch(app)or not NAME.fullmatch(name)or not ID.fullmatch(backup):raise RuntimeError('Identifiants de snapshot invalides.')
    return config['PREFIX']+'/apps/'+app+'/volumes/'+name+'/'+backup+'.phwv'


def _key(config,app,name):return hmac.new(config['KEY'],b'volume\0'+app.encode()+b'\0'+name.encode(),hashlib.sha256).digest()
def _aad(app,name,backup):return MAGIC+b'\0'+app.encode()+b'\0'+name.encode()+b'\0'+backup.encode()


def _member(member,size):
    path=pathlib.PurePosixPath(member.name)
    if path.is_absolute()or '..'in path.parts or not(member.isfile()or member.isdir())or member.size<0 or member.size>size:raise RuntimeError('Le snapshot accepte uniquement des fichiers réguliers et des dossiers dans ce volume.')


class _Reader:
    def __init__(self,chunks):self.chunks=iter(chunks);self.buffer=bytearray();self.total=0
    def read(self,n):
        while len(self.buffer)<n:
            try:self.buffer.extend(next(self.chunks))
            except StopIteration:break
        value=bytes(self.buffer[:n]);del self.buffer[:n];self.total+=len(value);return value
    def finish(self):
        if any(self.buffer):raise RuntimeError('Données après la fin du tar refusées.')
        self.buffer.clear()
        for chunk in self.chunks:
            if any(chunk):raise RuntimeError('Données après la fin du tar refusées.')


def _decrypted(body,key,aad,size,deadline):
    from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
    header=body.read(17)
    if len(header)!=17 or header[:5]!=MAGIC:raise RuntimeError('Format du snapshot de volume invalide.')
    decoder=Cipher(algorithms.AES(key),modes.GCM(header[5:])).decryptor();decoder.authenticate_additional_data(aad)
    total=17;tail=b''
    while True:
        deadline.check();chunk=body.read(65536)
        if not chunk:break
        total+=len(chunk)
        if total>size:raise RuntimeError('Snapshot de volume trop volumineux.')
        block=tail+chunk
        if len(block)>16:yield decoder.update(block[:-16]);tail=block[-16:]
        else:tail=block
    if total!=size or len(tail)!=16:raise RuntimeError('Snapshot de volume tronqué.')
    yield decoder.finalize_with_tag(tail)


def snapshot(root,job,docker,api):
    import web_app_backup_s3 as remote
    from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
    config=remote._settings()
    if not config:raise RuntimeError('Les snapshots de volumes nécessitent le stockage S3 configuré.')
    app,name=job['appId'],job['name'];manifest=_load(root,app);state=manifest['volumes'].get(name)
    if not state:raise RuntimeError('Le volume n’a pas encore été initialisé par un déploiement.')
    if _measure(docker,app,state['activeVolume'])>state['sizeMiB']*1024**2:raise RuntimeError('Le volume dépasse sa limite. Le snapshot est suspendu.')
    object_key=_object(config,app,name,job['id']);metadata={'format':'PHWV1','app-id':app,'volume-name':name,'backup-id':job['id'],'key-id':hashlib.sha256(_key(config,app,name)).hexdigest()[:24]}
    deadline=remote._Deadline();heartbeat=remote._Heartbeat(lambda _,body:api('storage/report',body),job);writer=None;process=None
    try:
        client=remote._client(config);writer=remote._Multipart(client,config,object_key,metadata,deadline,heartbeat)
        nonce=os.urandom(12);encoder=Cipher(algorithms.AES(_key(config,app,name)),modes.GCM(nonce)).encryptor();encoder.authenticate_additional_data(_aad(app,name,job['id']));compressor=zlib.compressobj(level=1,wbits=31);writer.write(MAGIC+nonce)
        class Sink:
            total=0
            def write(self,data):
                self.total+=len(data)
                if self.total>state['sizeMiB']*1024**2+64*1024**2:raise RuntimeError('Le snapshot dépasse le budget du volume.')
                writer.write(encoder.update(compressor.compress(data)))
                return len(data)
            def flush(self):pass
        sink=Sink();file_bytes=0;count=0
        with quiesce(docker,app,journal=lambda paused:_journal(root,job,pausedContainers=paused)):
            args=remote._docker_command('load')[:3]+_helper_args(docker,app,state['activeVolume'],['tar','-C','/volume','-cf','-','.'])
            process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True,cwd=root,env={'PATH':'/usr/local/bin:/usr/bin:/bin'});deadline.processes.append(process)
            with tarfile.open(fileobj=process.stdout,mode='r|')as source,tarfile.open(fileobj=sink,mode='w|')as destination:
                for member in source:
                    deadline.check();heartbeat.check();_member(member,state['sizeMiB']*1024**2);file_bytes+=member.size;count+=1
                    if file_bytes>state['sizeMiB']*1024**2 or count>100000:raise RuntimeError('Le volume dépasse sa limite de snapshot.')
                    member.uid=10001;member.gid=10001;member.uname='';member.gname='';member.mode&=0o777
                    destination.addfile(member,source.extractfile(member)if member.isfile()else None)
            if process.wait(timeout=deadline.remaining()):raise RuntimeError('L’export du volume a échoué.')
        writer.write(encoder.update(compressor.flush()));writer.write(encoder.finalize()+encoder.tag);writer.complete()
        head=remote._head(client,config,object_key,metadata)
        if head['ContentLength']!=writer.size:raise RuntimeError('La taille du snapshot distant ne correspond pas.')
        return {'object':object_key,'bytes':writer.size,'dataBytes':file_bytes,'files':count,'sourceVolume':state['activeVolume'],'backupId':job['id'],'checkedAt':remote._stamp()}
    finally:
        deadline.close();heartbeat.close()
        if process:
            remote._kill(process)
            if process.stdout:process.stdout.close()
        if writer:writer.abort()


def restore(root,job,docker,api):
    import web_app_backup_s3 as remote
    config=remote._settings()
    if not config:raise RuntimeError('Le stockage S3 des volumes est indisponible.')
    app,name=job['appId'],job['name'];before=_load(root,app);state=before['volumes'].get(name)
    if not state:raise RuntimeError('Volume non initialisé.')
    old_volume=state['activeVolume']
    backup=job['backup'];source_id=backup['backupId'];expected=_object(config,app,name,source_id)
    if backup.get('object')!=expected:raise RuntimeError('Snapshot d’un autre volume refusé.')
    client=remote._client(config);metadata={'format':'PHWV1','app-id':app,'volume-name':name,'backup-id':source_id,'key-id':hashlib.sha256(_key(config,app,name)).hexdigest()[:24]}
    deadline=remote._Deadline();heartbeat=remote._Heartbeat(lambda _,body:api('storage/report',body),job);process=None;new_volume='ph-web-data-'+app+'-'+name+'-r'+job['id'];succeeded=False
    try:
        head=remote._head(client,config,expected,metadata)
        if not 33<head['ContentLength']<=remote.MAX_COMPRESSED:raise RuntimeError('Taille du snapshot invalide.')
        body=remote._download(client,config,expected,head,metadata,deadline)
        total=0;files=0
        try:
            reader=_Reader(remote._inflate(_decrypted(body,_key(config,app,name),_aad(app,name,source_id),head['ContentLength'],deadline),deadline))
            with tarfile.open(fileobj=reader,mode='r|')as archive:
                for member in archive:
                    _member(member,state['sizeMiB']*1024**2);total+=member.size;files+=1
                    if total>state['sizeMiB']*1024**2 or files>100000:raise RuntimeError('Le snapshot dépasse la limite du volume.')
            reader.finish()
        finally:body.close()
        remote._require_space(root,remote.MIN_FREE+2*total)
        _create(docker,app,name,new_volume)
        plan={'name':name,'activeVolume':new_volume,'previousVolume':old_volume}
        with quiesce(docker,app,resume=False,journal=lambda paused:_journal(root,job,pausedContainers=paused,restorePlan={**plan,'previousContainers':paused}))as paused:
            body=remote._download(client,config,expected,head,metadata,deadline)
            guard=None
            try:
                args=remote._docker_command('load')[:3]+_helper_args(docker,app,new_volume,['tar','--no-same-owner','--no-same-permissions','-C','/volume','-xf','-'],write=True,stdin=True)
                process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,cwd=root,env={'PATH':'/usr/local/bin:/usr/bin:/bin'});deadline.processes.append(process);guard=remote._LoadDiskGuard(root,process)
                for chunk in remote._inflate(_decrypted(body,_key(config,app,name),_aad(app,name,source_id),head['ContentLength'],deadline),deadline):guard.check(len(chunk));process.stdin.write(chunk)
                process.stdin.close()
                while True:
                    guard.check(force=True)
                    try:code=process.wait(timeout=min(1,deadline.remaining()));break
                    except subprocess.TimeoutExpired:continue
                if code:raise RuntimeError('L’extraction du volume a échoué.')
            finally:
                body.close()
                if guard:guard.close()
            state['activeVolume']=new_volume;_save(root,app,before);succeeded=True
        return {'activeVolume':new_volume,'previousVolume':old_volume,'previousContainers':paused,'dataBytes':total,'backupId':source_id,'restartRequired':True}
    finally:
        deadline.close();heartbeat.close()
        if process:
            remote._kill(process)
            if process.stdin and not process.stdin.closed:process.stdin.close()
        if not succeeded:
            try:docker(['volume','rm',new_volume])
            except Exception:pass


def _journal(root,job,**state):
    path=pathlib.Path(root).absolute()/'storage-operation.json'
    if path.resolve()!=path:raise RuntimeError('Journal de stockage symbolique refusé.')
    temporary=path.with_suffix('.tmp')
    with open(temporary,'w')as output:
        os.chmod(temporary,0o600);json.dump({'job':job,**state},output);output.flush();os.fsync(output.fileno())
    os.replace(temporary,path)


def _resume(docker,app,containers):
    if not ID.fullmatch(app)or not isinstance(containers,list)or len(containers)>4:raise RuntimeError('Reprise du stockage invalide.')
    plans=[]
    for ident in containers:
        if not isinstance(ident,str)or not re.fullmatch('[a-f0-9]{64}',ident):raise RuntimeError('Conteneur de reprise invalide.')
        info=json.loads(docker(['inspect',ident]))[0]
        if info.get('Id')!=ident:raise RuntimeError('Reprise d’un autre conteneur refusée.')
        plans.append((ident,restartable_container(info,app,running=bool(info.get('State',{}).get('Running')))))
    for ident,_ in plans:
        docker(['start',ident],timeout=30)
    for ident,targets in plans:_wait_resumed(docker,app,ident,targets)


def rollback_restore(root,app,result,docker):
    name=result.get('name','');prefix='ph-web-data-'+app+'-'+name+'-'
    if not NAME.fullmatch(name)or not re.fullmatch(prefix+r'(?:v1|r[a-z0-9]{15})',result.get('previousVolume',''))or not re.fullmatch(prefix+r'r[a-z0-9]{15}',result.get('activeVolume','')):raise RuntimeError('Version précédente du volume invalide.')
    manifest=_load(root,app)
    if manifest['volumes'].get(name,{}).get('activeVolume')==result['activeVolume']:
        manifest['volumes'][name]['activeVolume']=result['previousVolume'];_save(root,app,manifest)
    _resume(docker,app,result.get('previousContainers',[]))


def _delete_snapshot(job):
    import web_app_backup_s3 as remote
    config=remote._settings()
    if not config:raise RuntimeError('S3 requis pour supprimer ce snapshot.')
    app,name=job['appId'],job['name'];key=_object(config,app,name,job['id'])
    # Préfixe d'une capture de reprise (après bascule) : ses objets appartiennent à la capture, jamais supprimés ici.
    if remote.capture_prefix(config):return {'deleted':True}
    client=remote._client(config);remote._require_bucket(client,config)
    expected={'format':'PHWV1','app-id':app,'volume-name':name,'backup-id':job['id']}
    if remote._head(client,config,key,expected,absent=True):client.delete_object(Bucket=config['BUCKET'],Key=key)
    return {'deleted':True}


def tick(root,api,docker):
    path=pathlib.Path(root).absolute()/'storage-operation.json'
    if path.resolve()!=path:raise RuntimeError('Journal de stockage symbolique refusé.')
    if path.exists():
        if path.stat().st_size>200000:raise RuntimeError('Journal de stockage trop volumineux.')
        pending=json.loads(path.read_text());job=pending['job']
        if not ID.fullmatch(job.get('appId',''))or not ID.fullmatch(job.get('id','')):raise RuntimeError('Journal de stockage invalide.')
        proof={'id':job['id'],'lease':job['lease']}
        current=api('storage/report',{**proof,'inspect':True})
        if current.get('status')not in ('ready','deleted','failed')and pending.get('result'):
            try:api('storage/report',{**proof,'status':'ready','result':pending['result']})
            except Exception:
                current=api('storage/report',{**proof,'inspect':True})
            else:current={'status':'ready'}
        if current.get('status')not in ('ready','deleted'):
            if pending.get('restorePlan'):rollback_restore(root,job['appId'],pending['restorePlan'],docker)
            else:_resume(docker,job['appId'],pending.get('pausedContainers',[]))
            if current.get('status')!='failed':api('storage/report',{**proof,'status':'failed'})
        path.unlink();return True
    job=api('storage/claim',{}).get('job')
    if not job:return False
    _journal(root,job)
    try:
        if job['operation']=='backup':result=snapshot(root,job,docker,api)
        elif job['operation']=='restore':result=restore(root,job,docker,api)
        elif job['operation']=='delete':result=_delete_snapshot(job)
        else:raise RuntimeError('Opération de stockage inconnue.')
    except Exception:
        # Keep the journal until the controller acknowledges failure; restart can resume services.
        api('storage/report',{'id':job['id'],'lease':job['lease'],'status':'failed'})
        path.unlink();return True
    pending=json.loads(path.read_text());_journal(root,job,**{key:value for key,value in pending.items()if key!='job'},result=result)
    api('storage/report',{'id':job['id'],'lease':job['lease'],'status':'ready','result':result})
    path.unlink();return True


def start_storage_monitor(root,docker,api=None):
    stop=threading.Event()
    def loop():
        while not stop.is_set():
            applications={}
            for file in list((pathlib.Path(root)/'storage').glob('*.json'))[:1000]:
                app=file.stem
                if (pathlib.Path(root)/'preview-deletions'/(app+'.json')).exists():continue
                try:
                    manifest=_load(root,app);rows=[]
                    for name,state in manifest['volumes'].items():
                        if (pathlib.Path(root)/'preview-deletions'/(app+'.json')).exists():break
                        used=_measure(docker,app,state['activeVolume']);over=used>state['sizeMiB']*1024**2 or shutil.disk_usage(root).free<15*1024**3
                        rows.append({'name':name,'target':state['target'],'sizeMiB':state['sizeMiB'],'usedBytes':used,'activeVolume':state['activeVolume'],'overLimit':over})
                        if over:
                            for ident in docker(['ps','-q','--filter','label=pockethost.web=true','--filter','label=pockethost.web.app='+app]).split():docker(['stop','-t','10',ident],timeout=15)
                    applications[app]={'volumes':rows,'checkedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'available':True}
                except Exception:applications[app]={'volumes':[],'available':False,'lastError':'Mesures du stockage indisponibles.'}
            path=pathlib.Path(root)/'storage-status.json';temporary=path.with_suffix('.tmp')
            try:
                with open(temporary,'w')as output:os.chmod(temporary,0o640);json.dump({'applications':applications},output)
                os.replace(temporary,path)
            except OSError:pass
            stop.wait(15)
    def disk_loop():
        while not stop.is_set():
            try:
                if shutil.disk_usage(root).free<15*1024**3:
                    ids=docker(['ps','-q','--filter','label=pockethost.web=true']).split()[:200]
                    for info in json.loads(docker(['inspect',*ids]))if ids else[]:
                        labels=info.get('Config',{}).get('Labels')or{};app=labels.get('pockethost.web.app','');dep=labels.get('pockethost.web.deployment','')
                        if not ID.fullmatch(app)or not ID.fullmatch(dep)or not info.get('Name','').lstrip('/').startswith('ph-web-'+app+'-'+dep):continue
                        docker(['stop','-t','10',info['Id']],timeout=15)
                        if api:
                            try:api('report',{'id':dep,'runtimeStopped':'Site arrêté : la réserve globale de 15 Gio d’espace libre doit être conservée.'})
                            except Exception:pass
            except Exception:pass
            stop.wait(5)
    thread=threading.Thread(target=loop,name='web-storage-monitor',daemon=True);thread.start()
    disk=threading.Thread(target=disk_loop,name='web-storage-disk-guard',daemon=True);disk.start();return stop


def prune_unused_volumes(root,app,docker,delete_all=False):
    manifest=_load(root,app);protected=set()if delete_all else{item['activeVolume']for item in manifest['volumes'].values()}
    for ident in docker(['ps','-aq','--filter','label=pockethost.web.app='+app]).split():
        info=json.loads(docker(['inspect',ident]))[0]
        if info.get('Config',{}).get('Labels',{}).get('pockethost.web.app')!=app:raise RuntimeError('Conteneur étranger pendant le nettoyage du stockage.')
        protected.update(mount.get('Name')for mount in info.get('Mounts',[])if mount.get('Type')=='volume')
    for volume in docker(['volume','ls','-q','--filter','label=pockethost.web.app='+app]).split():
        if not volume.startswith('ph-web-data-'+app+'-')or volume in protected:continue
        info=json.loads(docker(['volume','inspect',volume]))[0]
        if info.get('Labels',{}).get('pockethost.web.app')!=app or info.get('Driver')!='local'or info.get('Options'):raise RuntimeError('Volume étranger pendant le nettoyage.')
        try:docker(['volume','rm',volume])
        except subprocess.CalledProcessError:pass
