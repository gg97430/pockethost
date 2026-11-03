"""Restricted application runtimes on the dedicated rootless Docker engine."""
import base64
import http.client
import json
import os
import pathlib
import re
import socket
import stat
import time
import urllib.parse

KINDS = ('nextjs', 'auto', 'dockerfile', 'image', 'compose', 'worker')
ID = re.compile(r'^[a-z0-9]{15}$')
NAME = re.compile(r'^[a-z][a-z0-9-]{0,30}$')
DIGEST = re.compile(r'^(?:[a-z0-9.-]+(?::[0-9]{1,5})?/)?[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$')
RESERVE = 512 * 1024**2
CGROUP_ROOT = pathlib.Path('/sys/fs/cgroup')
PROC_MEMINFO = pathlib.Path('/proc/meminfo')
PROC_ROOT = pathlib.Path('/proc')
COMPOSE_LIMITS = {'cpuMillicores': 4000, 'memoryMiB': 8192, 'pids': 512}
DEPENDENCY_TIMEOUT_SECONDS = 120


def number(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise RuntimeError('Quota ou port du profil invalide.')
    return value


def relative(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_./-]{1,200}', value) or value.startswith('/') or '..' in pathlib.PurePosixPath(value).parts:
        raise RuntimeError('Chemin de construction invalide.')
    return value


def build_context(repository, root_directory='.'):
    """Dockerfile COPY and ignore rules are relative to the selected application directory."""
    repository = pathlib.Path(repository).resolve(strict=True)
    selected = repository
    for part in pathlib.PurePosixPath(relative(root_directory)).parts:
        selected = selected / part
        if selected.is_symlink():
            raise RuntimeError('Dossier de construction symbolique interdit.')
    if not selected.is_dir() or not selected.resolve().is_relative_to(repository):
        raise RuntimeError('Dossier de construction absent ou hors du dépôt.')
    return selected


def command(value):
    if value is None: return []
    if not isinstance(value, list) or len(value) > 50 or any(not isinstance(item, str) or not item or len(item) > 4096 or '\x00' in item for item in value):
        raise RuntimeError('Commande attendue sous forme de tableau, sans interprétation sur l’hôte.')
    return value


def text(value):
    if value is None or value == '': return ''
    if not isinstance(value, str) or len(value) > 1000 or any(char in value for char in '\x00\r\n'):
        raise RuntimeError('Commande de démarrage ou de construction invalide (une ligne, 1000 caractères maximum).')
    return value


def quotas(value=None, compose=False, adaptive=False):
    value = value or {}
    if not isinstance(value, dict) or set(value) - {'cpuMillicores','memoryMiB','pids'}:
        raise RuntimeError('Quotas invalides.')
    limits = COMPOSE_LIMITS if compose or adaptive else {'cpuMillicores':1000,'memoryMiB':1536,'pids':512}
    return {'cpuMillicores':number(value.get('cpuMillicores',500),50,limits['cpuMillicores']),
            'memoryMiB':number(value.get('memoryMiB',768),64,limits['memoryMiB']),
            'pids':number(value.get('pids',256),16,limits['pids'])}


def dependency_names(value):
    return list(value) if isinstance(value, dict) else value


def validate_profile(value=None):
    value = value or {}
    allowed = {'kind','port','dockerfile','image','command','entrypoint','registry','compose','resources','buildTarget','startCommand','buildCommand','pruneDependencies','allocation','priority','memoryRequestMiB'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise RuntimeError('Options de runtime non prises en charge.')
    kind = value.get('kind', 'nextjs')
    if kind not in KINDS: raise RuntimeError('Runtime inconnu.')
    allocation=value.get('allocation','adaptive');priority=value.get('priority','normal')
    if allocation not in ('adaptive','fixed') or priority not in ('low','normal','high'):raise RuntimeError('Politique d’allocation invalide.')
    result = {**value, 'allocation':allocation, 'priority':priority, 'kind': kind, 'port': None if kind == 'worker' else number(value.get('port',3000),1,65535),
        'command': command(value.get('command')), 'entrypoint': command(value.get('entrypoint')),
        'resources': quotas(value.get('resources'),kind=='compose',allocation=='adaptive')}
    result['dockerfile'] = relative(value.get('dockerfile','Dockerfile'))
    image = value.get('image','')if kind in('image','worker')else''
    result['image']=image
    if kind in ('image','worker') and image and not DIGEST.fullmatch(image): raise RuntimeError('L’image doit être épinglée par digest SHA256.')
    if kind == 'image' and not image: raise RuntimeError('Image immuable requise.')
    # Railpack : image à Entrypoint ["/bin/bash","-c"], seule la commande texte a un sens.
    if kind == 'auto' and (result['command'] or result['entrypoint']): raise RuntimeError('Pour la construction automatique, utilisez la commande de démarrage (champ texte).')
    if kind == 'auto' and value.get('buildTarget'): raise RuntimeError('Étape Dockerfile sans objet pour la construction automatique.')
    for key in ('startCommand', 'buildCommand'):
        result.pop(key, None); item = text(value.get(key))
        if item and kind != 'auto': raise RuntimeError('Options de runtime non prises en charge.')
        if item: result[key] = item
    # Construction automatique : retirer les dépendances de développement de l'image (RAILPACK_PRUNE_DEPS).
    result.pop('pruneDependencies', None)
    if (value.get('pruneDependencies') is not None and not isinstance(value['pruneDependencies'], bool)) or (value.get('pruneDependencies') and kind != 'auto'): raise RuntimeError('Options de runtime non prises en charge.')
    if value.get('pruneDependencies'): result['pruneDependencies'] = True
    if value.get('buildTarget') and not NAME.fullmatch(value['buildTarget']): raise RuntimeError('Étape Dockerfile invalide.')
    registry = value.get('registry') or {}
    if registry:
        if (not isinstance(registry,dict) or set(registry)-{'server','username','password'} or not re.fullmatch(r'[a-z0-9.-]+(?::[0-9]{1,5})?',registry.get('server',''))
            or not isinstance(registry.get('username'),str) or not isinstance(registry.get('password'),str)
            or any('\x00' in str(item) or '\n' in str(item) for item in registry.values())):
            raise RuntimeError('Identifiants de registre invalides.')
    if kind == 'compose':
        result['compose'] = validate_compose(value.get('compose'), result['resources'])
        # Report the actual aggregate, also for older profiles whose explicit
        # service quotas already exceeded the default top-level budget.
        result['resources'] = {key:sum(item['resources'][key]for item in result['compose']['services'].values())for key in COMPOSE_LIMITS}
    if allocation=='adaptive':
        result['memoryRequestMiB']=number(value.get('memoryRequestMiB',min(256,result['resources']['memoryMiB']//2)),32,result['resources']['memoryMiB'])
    elif value.get('memoryRequestMiB')is not None:raise RuntimeError('Demande mémoire réservée au mode adaptatif.')
    return result


def validate_compose(value, resources):
    if not isinstance(value,dict) or set(value)-{'front','services'} or not isinstance(value.get('services'),dict) or not 1 <= len(value['services']) <= 4:
        raise RuntimeError('Compose exige un frontal et de un à quatre services.')
    front = value.get('front')
    if front not in value['services']: raise RuntimeError('Service frontal Compose manquant.')
    services = {}
    for name, item in value['services'].items():
        if not NAME.fullmatch(name) or not isinstance(item,dict) or set(item)-{'image','dockerfile','port','command','entrypoint','environment','depends_on','volumes','resources','healthcheck'}:
            raise RuntimeError('Service Compose non autorisé : seuls images, commandes, dépendances et volumes nommés sont acceptés.')
        if bool(item.get('image')) == bool(item.get('dockerfile')): raise RuntimeError('Chaque service exige une image immuable ou un Dockerfile.')
        if item.get('image') and not DIGEST.fullmatch(item['image']): raise RuntimeError('Chaque image Compose doit être épinglée par digest.')
        if item.get('dockerfile'): relative(item['dockerfile'])
        env = item.get('environment',[])
        if not isinstance(env,list) or len(env)>100 or any(not isinstance(key,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,100}',key) for key in env):
            raise RuntimeError('Compose référence uniquement les noms des variables enregistrées, sans valeurs littérales.')
        depends = item.get('depends_on',[])
        if not isinstance(depends,(list,dict)) or len(depends)>4 or any(not isinstance(key,str)or key not in value['services'] or key==name for key in depends): raise RuntimeError('Dépendance Compose invalide.')
        if isinstance(depends,dict):
            if any(not isinstance(condition,dict)or set(condition)!={'condition'}or condition['condition']not in('service_started','service_healthy')for condition in depends.values()):raise RuntimeError('Condition de dépendance Compose invalide.')
        quota = item.get('resources') or {'cpuMillicores':max(50,resources['cpuMillicores']//len(value['services'])), 'memoryMiB':max(64,resources['memoryMiB']//len(value['services'])), 'pids':max(16,resources['pids']//len(value['services']))}
        quota = quotas(quota,compose=True)
        mounts = item.get('volumes',[])
        if not isinstance(mounts,list) or len(mounts)>4 or any(not isinstance(mount,str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,30}:/[A-Za-z0-9_./-]+(?::ro)?',mount) or '..' in mount.split(':')[1].split('/') for mount in mounts):
            raise RuntimeError('Compose accepte seulement les volumes nommés enregistrés ; aucun bind hôte.')
        check = item.get('healthcheck')
        if check:
            if not isinstance(check,dict) or set(check)-{'test','timeoutSeconds','intervalSeconds','retries','startPeriodSeconds'}: raise RuntimeError('Contrôle de service invalide.')
            test=command(check.get('test'))
            if not test or test[0]!='CMD' or len(test)<2: raise RuntimeError('Le contrôle exige un tableau CMD.')
            check={'Test':test,'Timeout':number(check.get('timeoutSeconds',5),1,10)*10**9,'Interval':number(check.get('intervalSeconds',10),5,60)*10**9,'Retries':number(check.get('retries',3),1,10),'StartPeriod':number(check.get('startPeriodSeconds',0),0,60)*10**9}
        services[name]={**item,'command':command(item.get('command')),'entrypoint':command(item.get('entrypoint')),'environment':env,'depends_on':depends,'volumes':mounts,'resources':quota,'port':number(item.get('port',3000),1,65535),'healthcheck':check}
    for key, maximum in COMPOSE_LIMITS.items():
        if sum(item['resources'][key] for item in services.values())>maximum: raise RuntimeError('La somme des quotas Compose dépasse celle du site.')
    ordered=[]
    while len(ordered)<len(services):
        ready=[name for name,item in services.items() if name not in ordered and all(dep in ordered for dep in dependency_names(item['depends_on']))]
        if not ready: raise RuntimeError('Cycle de dépendances Compose refusé.')
        ordered.extend(sorted(ready))
    return {'front':front,'services':services,'order':ordered}


def effective_memory():
    uid=os.getuid(); base=CGROUP_ROOT/'user.slice'/('user-'+str(uid)+'.slice')
    values=[]
    path=base
    while True:
        try:
            maximum=(path/'memory.max').read_text().strip()
        except FileNotFoundError:
            # The root cgroup has no memory.max; every non-root limit must remain observable.
            if path!=CGROUP_ROOT: raise RuntimeError('Admission refusée : la limite mémoire effective du groupe web ne peut pas être vérifiée.') from None
            maximum='max'
        except OSError:
            raise RuntimeError('Admission refusée : la limite mémoire effective du groupe web ne peut pas être vérifiée.') from None
        if maximum!='max':
            try:
                maximum=int(maximum);current=int((path/'memory.current').read_text())
                if maximum<=0 or current<0:raise ValueError()
            except (OSError,ValueError):
                raise RuntimeError('Admission refusée : la limite mémoire effective du groupe web ne peut pas être vérifiée.') from None
            reclaimable=0
            try:
                # memory.stat is keyed and may gain/reorder counters. Only the inactive
                # filesystem LRU is discounted, never anonymous/active/slab memory.
                stats={}
                for line in (path/'memory.stat').read_text().splitlines():
                    key,value=line.split()
                    if key in stats:raise ValueError()
                    stats[key]=int(value)
                    if stats[key]<0:raise ValueError()
                reclaimable=min(current,stats.get('inactive_file',0))
            except (OSError,ValueError):pass
            values.append({'scope':str(path),'limitBytes':maximum,'currentBytes':current,'reclaimableBytes':reclaimable,'workingSetBytes':max(0,current-reclaimable)})
        if path==CGROUP_ROOT:break
        path=path.parent
    if not values: raise RuntimeError('Admission refusée : la limite mémoire effective du groupe web ne peut pas être vérifiée.')
    return values


def build_reservation():
    """Réservation d'une construction = quotas du builder (WEB_APPS_BUILD_MEMORY / WEB_APPS_BUILD_CPUS, défaut 3 Gio / 1 CPU)."""
    memory=os.environ.get('WEB_APPS_BUILD_MEMORY','3g').strip().lower(); cpus=os.environ.get('WEB_APPS_BUILD_CPUS','1').strip()
    mib=int(memory[:-1])*(1024 if memory.endswith('g') else 1) if re.fullmatch(r'[1-9][0-9]{0,4}[mg]',memory) else 3072
    millicores=int(float(cpus)*1000) if re.fullmatch(r'[0-9]{1,2}(\.[0-9])?',cpus) and 0.5<=float(cpus)<=32 else 1000
    return mib,millicores


def effective_cpu_millicores():
    """Plus petit quota cpu.max du groupe web et de ses parents ; 2 CPU (quota historique) s'il est illisible."""
    path=CGROUP_ROOT/'user.slice'/('user-'+str(os.getuid())+'.slice'); found=False; limits=[]
    while path!=CGROUP_ROOT:
        try:quota,period=(path/'cpu.max').read_text().split()[:2];found=True
        except (OSError,ValueError):quota=period=None
        if quota and quota!='max' and quota.isdigit() and period.isdigit() and int(period)>0:limits.append(int(quota)*1000//int(period))
        path=path.parent
    if limits:return min(limits)
    return (os.cpu_count() or 2)*1000 if found else 2000


class ResourcePressure(RuntimeError):
    """Temporary pressure: a build may wait without stopping published containers."""


def allocation_mode(value=None):
    configured=os.environ.get('WEB_APPS_ALLOCATION_MODE','adaptive')
    if configured not in ('adaptive','fixed') or value not in (None,'adaptive','fixed'):
        raise RuntimeError('Politique d’allocation invalide.')
    # An operator can restore conservative admission globally without rewriting profiles.
    return 'fixed' if configured=='fixed' or value=='fixed' else 'adaptive'


def memory_pressure(constraints):
    for row in constraints:
        path=pathlib.Path(row['scope'])/'memory.pressure'
        try:
            found=False
            for line in path.read_text().splitlines():
                parts=line.split()
                if parts and parts[0]=='some':
                    found=True
                    average=float(dict(item.split('=',1)for item in parts[1:])['avg10'])
                    if not 0<=average<=100:raise ValueError()
                    if average>=10:return True
            if not found:raise ValueError()
        except FileNotFoundError:pass  # PSI is optional; live memory counters remain mandatory.
        except (OSError,ValueError,KeyError):raise RuntimeError('Pression mémoire invérifiable.')from None
    return False


def host_memory():
    try:
        values={line.split(':',1)[0]:line.split(':',1)[1].split()for line in PROC_MEMINFO.read_text().splitlines()if ':'in line}
        total,available=(int(values[key][0])*1024 for key in ('MemTotal','MemAvailable'))
        if any(values[key][1]!='kB'for key in ('MemTotal','MemAvailable')) or not 0<=available<=total or total<=0:raise ValueError()
        return {'totalBytes':total,'availableBytes':available}
    except (OSError,ValueError,KeyError,IndexError):raise RuntimeError('Mémoire disponible du serveur invérifiable.')from None


def memory_requests(profile):
    if profile['kind']!='compose':return {'':profile['memoryRequestMiB']}
    services=profile['compose']['services'];request=profile['memoryRequestMiB']
    # Docker requires >=6 MiB per reservation. Split the remainder by available
    # headroom so no service can exceed its own limit, even at full reservation.
    remaining=request-6*len(services)
    capacities={name:item['resources']['memoryMiB']-6 for name,item in services.items()}
    total=sum(capacities.values())
    if remaining<0 or remaining>total:raise RuntimeError('Demande mémoire du groupe invalide.')
    result={name:6+remaining*capacity//total for name,capacity in capacities.items()}
    order=sorted(capacities,key=lambda name:(-(remaining*capacities[name]%total),name))
    for name in order[:request-sum(result.values())]:result[name]+=1
    return result


def resource_config(profile, resources, service=''):
    """Memory is allocated on demand; reservations are soft protections, never guarantees."""
    result={'NanoCpus':resources['cpuMillicores']*1000000,'Memory':resources['memoryMiB']*1024**2,
            'MemorySwap':resources['memoryMiB']*1024**2,'PidsLimit':resources['pids']}
    if allocation_mode(profile.get('allocation'))=='adaptive':
        count=len(profile['compose']['services'])if profile['kind']=='compose'else 1
        result['CpuShares']=max(2,{'low':256,'normal':1024,'high':2048}[profile.get('priority','normal')]//count)
        result['MemoryReservation']=memory_requests(profile)[service if profile['kind']=='compose'else'']*1024**2
    return result


def admission_memory(profile):
    if allocation_mode(profile.get('allocation'))!='adaptive':return profile['resources']['memoryMiB']
    return sum(memory_requests(profile).values())


def builder_resident_memory(docker, maximum):
    """Locate our existing builder's complete cgroup; a failed check grants no credit.

    Its resident memory is already included in ancestor working sets. Replace it
    with the full build reservation, rather than adding that reservation twice.
    Include nested BuildKit workers, not only the container's /init subgroup.
    """
    try:
        rows=json.loads(docker(['inspect','buildx_buildkit_ph-web-builder0']))
        if not isinstance(rows,list) or len(rows)!=1:return None
        info=rows[0];cid=info.get('Id','');state=info.get('State')or{};host=info.get('HostConfig')or{}
        if (not isinstance(cid,str) or not re.fullmatch(r'[a-f0-9]{64}',cid)
                or info.get('Name')!='/buildx_buildkit_ph-web-builder0' or state.get('Running')is not True
                or type(state.get('Pid'))is not int or state['Pid']<=1 or host.get('Memory')!=maximum
                or host.get('NetworkMode')!='ph-web-build-network'
                or (info.get('Config')or{}).get('Cmd')!=['--oci-worker-net=bridge']):return None
        text=(PROC_ROOT/str(state['Pid'])/'cgroup').read_text().strip()
        if not text.startswith('0::/') or '\n'in text:return None
        parts=pathlib.PurePosixPath(text[3:]).parts
        if '..'in parts:return None
        index=parts.index('docker-'+cid+'.scope')
        scope=CGROUP_ROOT.joinpath(*parts[1:index+1])
        expected=CGROUP_ROOT/'user.slice'/('user-'+str(os.getuid())+'.slice')
        if not scope.is_relative_to(expected) or scope.resolve()!=scope:return None
        if int((scope/'memory.max').read_text())!=maximum:return None
        current=int((scope/'memory.current').read_text());stats={}
        for line in(scope/'memory.stat').read_text().splitlines():
            key,value=line.split()
            if key in stats:raise ValueError()
            stats[key]=int(value)
            if stats[key]<0:raise ValueError()
        if current<0 or 'inactive_file'not in stats:return None
        return {'scope':scope,'workingSetBytes':min(maximum,max(0,current-stats['inactive_file']))}
    except Exception:
        # Keep conservative admission if Docker/proc/cgroup identity or counters
        # cannot be verified; never mistake an unrelated process for the builder.
        return None


def admit(docker, memory_mib=0, cpu_millicores=0, build=False, mode=None):
    mode=allocation_mode(mode)
    constraints=effective_memory(); reserved=0; requested=0; cpu=0
    ids=docker(['ps','-q','--filter','label=pockethost.web=true']).split()
    if ids:
        for info in json.loads(docker(['inspect',*ids])):
            host=info.get('HostConfig') or {}
            from web_private_db_cpu import accounted_nano_cpus
            memory=host.get('Memory',0); nano=accounted_nano_cpus(info)
            if type(memory)is not int or memory<=0 or type(nano)is not int or nano<=0:
                raise RuntimeError('Admission refusée : un conteneur web n’a pas de quota explicite.')
            reservation=host.get('MemoryReservation')or memory
            if type(reservation)is not int or not 0<reservation<=memory:raise RuntimeError('Réservation mémoire du service invalide.')
            reserved+=memory;requested+=reservation;cpu+=nano//1000000
    build_mib,build_millicores=build_reservation() if build else (0,0)
    extra=(memory_mib+build_mib)*1024**2
    if mode=='fixed' and cpu_millicores+build_millicores>effective_cpu_millicores():
        raise RuntimeError('Le plafond CPU demandé dépasse la capacité du groupe web.')
    if mode=='fixed':
        if any(reserved+extra+RESERVE>row['limitBytes']or row['workingSetBytes']+extra+RESERVE>row['limitBytes']for row in constraints)or cpu+cpu_millicores+build_millicores>effective_cpu_millicores():
            raise RuntimeError('Admission suspendue : capacité insuffisante dans le budget web. Arrêtez un site ou réessayez après la construction en cours.')
    else:
        host=host_memory()
        if host['availableBytes']<extra+max(1024**3,host['totalBytes']//10):
            raise ResourcePressure('Admission différée : mémoire disponible du serveur insuffisante ; priorité aux services et aux bases de données.')
        builder=builder_resident_memory(docker,build_mib*1024**2)if build else None
        # Requests constrain admission; limits constrain peaks. Existing fixed
        # containers retain their full commitment until explicitly redeployed.
        for row in constraints:
            resident=builder['workingSetBytes']if builder and builder['scope'].is_relative_to(pathlib.Path(row['scope']))else 0
            required=max(max(0,row['workingSetBytes']-resident),requested)+extra+max(RESERVE,row['limitBytes']//10)
            if required>row['limitBytes']:
                raise ResourcePressure('Admission différée : budget mémoire de l’hébergement insuffisant ('+
                                       str((required+1024**2-1)//1024**2)+' Mio nécessaires, '+
                                       str(row['limitBytes']//1024**2)+' Mio autorisés, construction et marge comprises).')
        if extra and memory_pressure(constraints):
            raise ResourcePressure('Admission différée : pression mémoire élevée ; attente avant de démarrer la nouvelle charge.')
    tightest=min(constraints,key=lambda row:row['limitBytes']-row['workingSetBytes'])
    return {**tightest,'reservedBytes':reserved,'candidateBytes':extra,'requestedBytes':requested,'constraints':constraints,'allocation':mode}


def loopback_port(root):
    # Persist a concrete binding: Docker reassigns HostPort="" after stop/start.
    # Never reuse a publication's prior target while its services are quiescent.
    excluded=set()
    for filename in ('routes.json','runtime-publications.json'):
        path=pathlib.Path(root)/filename
        if path.is_symlink():raise RuntimeError('Manifeste de publication symbolique refusé.')
        try:value=json.loads(path.read_text())
        except FileNotFoundError:continue
        except (OSError,ValueError):raise RuntimeError('Les ports des publications ne peuvent pas être vérifiés.')from None
        rows=value.get('routes',[])if filename=='routes.json'else value.values()
        for row in rows:
            match=re.fullmatch(r'http://127\.0\.0\.1:([1-9][0-9]{3,4})',str(row.get('target','')))
            if match:excluded.add(int(match[1]))
    for _ in range(32):
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM)as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
            if 1024<=port<=65535 and port not in excluded:return str(port)
    raise RuntimeError('Aucun port loopback libre ne peut être réservé.')


def validate_image_volumes(info,targets):
    declarations=(info.get('Config')or{}).get('Volumes')or{}
    if not isinstance(declarations,dict)or len(declarations)>8:raise RuntimeError('Déclarations de volumes de l’image invalides.')
    for path in declarations:
        if not isinstance(path,str)or path not in targets or path=='/__pockethost_tools'or path.startswith('/__pockethost_tools/'):
            raise RuntimeError('L’image déclare un volume non géré. Enregistrez un volume nommé exactement à chaque cible avant de déployer.')
    return declarations


def readable_source(context):
    """Normalize repository modes before COPY runs as root and runtime uses UID 10001.

    Only the isolated checkout changes; never follow symlinks or touch Git metadata.
    Secret mounts and worker credentials live outside this directory.
    """
    context=pathlib.Path(context);count=0
    for folder,dirs,files in os.walk(context,followlinks=False):
        dirs[:]=[name for name in dirs if name!='.git']
        for name in dirs+files:
            if name=='.git':continue
            count+=1
            if count>100000:raise RuntimeError('Archive source trop volumineuse.')
            path=pathlib.Path(folder)/name;mode=path.lstat().st_mode
            if stat.S_ISLNK(mode):continue
            if stat.S_ISDIR(mode):path.chmod(0o755)
            elif stat.S_ISREG(mode):path.chmod(0o755 if mode&0o111 else 0o644)
            else:raise RuntimeError('Type de fichier source non autorisé.')
    context.chmod(0o755)


def exclude_vcs(context,dockerfile):
    """Never send Git metadata to BuildKit: root .dockerignore and a Dockerfile-specific one (which wins) both end with .git rules."""
    context=pathlib.Path(context);rules='\n.git\n**/.git\n'
    for ignore in [context/'.dockerignore',pathlib.Path(dockerfile).with_name(pathlib.Path(dockerfile).name+'.dockerignore')]:
        if ignore.is_symlink() or not ignore.resolve().is_relative_to(context.resolve()): raise RuntimeError('Fichier .dockerignore hors du dépôt.')
        if ignore==context/'.dockerignore' or ignore.is_file():
            previous=ignore.read_text(errors='replace') if ignore.is_file() else ''
            if not previous.endswith(rules): ignore.write_text(previous+rules)


class RuntimeEngine:
    def __init__(self, job, docker, request, root, socket_path):
        self.job,self.docker,self.request,self.root,self.socket=job,docker,request,pathlib.Path(root),socket_path
        self.profile=validate_profile(job.snapshot.get('runtime'))
        self.created=[]; self.network='ph-web-net-'+job.app+'-'+job.id

    def wait_dependencies(self,item,started,deadline):
        depends=item.get('depends_on',[])
        if not isinstance(depends,dict):return  # historical list: start order only
        for name,condition in depends.items():
            ident=started.get(name,'')
            if not re.fullmatch(r'[a-f0-9]{64}',ident):raise RuntimeError('Dépendance du candidat absente.')
            while time.monotonic()<deadline:
                info=json.loads(self.docker(['inspect',ident],timeout=min(5,max(.1,deadline-time.monotonic()))))[0]
                labels=(info.get('Config')or{}).get('Labels')or{}
                if info.get('Id')!=ident or any(labels.get(key)!=value for key,value in [('pockethost.web.app',self.job.app),('pockethost.web.deployment',self.job.id),('pockethost.web.service',name)]):raise RuntimeError('Dépendance étrangère au candidat refusée.')
                state=info.get('State')or{}
                if not state.get('Running'):raise RuntimeError('Une dépendance du candidat s’est arrêtée.')
                if condition['condition']=='service_started':break
                health=(state.get('Health')or{}).get('Status')
                if health=='healthy':break
                if health not in('starting',):raise RuntimeError('Une dépendance exige un contrôle de santé valide et réussi.')
                self.job.heartbeat()
                time.sleep(min(1,max(0,deadline-time.monotonic())))
            else:raise RuntimeError('Le contrôle de santé d’une dépendance dépasse le délai de 120 secondes.')

    def pulled_tag(self, image):
        # Per-application proof that this app obtained the digest from its registry (own credentials or public).
        return 'ph-web-pull/'+self.job.app+':'+image.rsplit('@sha256:',1)[1]

    def pull(self, image):
        if not DIGEST.fullmatch(image): raise RuntimeError('Image immuable requise.')
        # A completed CI delivery remains deployable after its short-lived registry
        # credentials expire. Only an exact repo@digest reference that THIS application
        # pulled itself qualifies: the Docker cache is shared by every project, and a digest
        # read from another team's profile must never bypass that team's registry auth.
        try:
            cached=json.loads(self.docker(['image','inspect',image],timeout=10))[0]
            owned=json.loads(self.docker(['image','inspect',self.pulled_tag(image)],timeout=10))[0]
        except __import__('subprocess').CalledProcessError:
            cached=owned=None
        if cached and owned and image in cached.get('RepoDigests',[])and re.fullmatch('sha256:[a-f0-9]{64}',cached.get('Id',''))and owned.get('Id')==cached['Id']:
            return cached['Id']
        registry=self.profile.get('registry') or {}
        repository=image.split('@')[0];first=repository.split('/')[0]
        host=first if '/'in repository and('.'in first or':'in first)else'index.docker.io'
        aliases={'docker.io','index.docker.io','registry-1.docker.io'}
        if registry and registry['server']!=host and not(registry['server']in aliases and host in aliases):registry={}
        engine=self
        class Unix(http.client.HTTPConnection):
            def connect(self):
                self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); self.sock.settimeout(30); self.sock.connect(engine.socket)
        connection=Unix('localhost',timeout=30)
        deadline=min(getattr(self.job,'started',time.monotonic())+1800,time.monotonic()+900)
        try:
            headers={}
            if registry:
                headers['X-Registry-Auth']=base64.urlsafe_b64encode(json.dumps({'serveraddress':host,'username':registry['username'],'password':registry['password']}).encode()).decode()
                self.job.secrets.append(headers['X-Registry-Auth'])
            connection.request('POST','/v1.47/images/create?'+urllib.parse.urlencode({'fromImage':image}),headers=headers)
            response=connection.getresponse()
            if response.status>=300: raise RuntimeError('Le registre refuse le téléchargement de l’image.')
            total=0
            while True:
                if time.monotonic()>deadline:raise RuntimeError('Délai maximal de téléchargement de l’image dépassé.')
                if __import__('shutil').disk_usage(self.root).free<15*1024**3:raise RuntimeError('Téléchargement suspendu : la réserve de 15 Gio doit être conservée.')
                row=response.readline(65537)
                if not row: break
                total+=len(row)
                if len(row)>65536 or total>16*1024**2: raise RuntimeError('Réponse du registre trop volumineuse.')
                if json.loads(row).get('error'): raise RuntimeError('Le registre n’a pas pu fournir cette image.')
            info=json.loads(self.docker(['image','inspect',image],timeout=10))[0]
            if not any(ref.split('@')[-1]==image.split('@')[-1]for ref in info.get('RepoDigests',[])): raise RuntimeError('Le digest téléchargé ne correspond pas à la demande.')
            if not re.fullmatch('sha256:[a-f0-9]{64}',info.get('Id','')): raise RuntimeError('Image téléchargée invalide.')
            self.docker(['image','tag',info['Id'],self.pulled_tag(image)],timeout=10)
            return info['Id']
        finally: connection.close()

    def managed_targets(self,service,info):
        from web_app_storage import validate_volumes
        storage=validate_volumes(self.job.snapshot.get('storage',[]))
        if self.profile['kind']=='compose':
            key=service or self.profile['compose']['front'];mounts=self.profile['compose']['services'][key].get('volumes',[]);targets=set()
            for mount in mounts:
                parts=mount.split(':');item=next((item for item in storage if item['name']==parts[0]),None)
                if not item:raise RuntimeError('Volume Compose non enregistré.')
                validate_volumes([{**item,'target':parts[1]}]);targets.add(parts[1])
        else:targets={item['target']for item in storage}
        labels=(info.get('Config')or{}).get('Labels')or{}
        targets.add('/tmp')
        if labels.get('pockethost.framework')=='nextjs'and labels.get('pockethost.next.directory'):
            directory=labels['pockethost.next.directory']
            if not isinstance(directory,str)or len(directory)>200 or not re.fullmatch(r'/app(?:/[A-Za-z0-9_.-]+)*/\.next',directory)or any(part in('.','..')for part in directory.split('/')):raise RuntimeError('Répertoire Next déclaré par l’image invalide.')
            targets.update([directory,directory+'/cache'])
        return targets

    def own_image(self,image,service='',source_app=None):
        info=json.loads(self.docker(['image','inspect',image],timeout=10))[0]
        if source_app and info.get('Config',{}).get('Labels',{}).get('pockethost.web.app')!=source_app: raise RuntimeError('Image de promotion étrangère à l’application source.')
        if not re.fullmatch('sha256:[a-f0-9]{64}',info.get('Id','')): raise RuntimeError('Image locale invalide.')
        declared=validate_image_volumes(info,self.managed_targets(service,info))
        labels={'pockethost.web':'true','pockethost.web.app':self.job.app,'pockethost.web.runtime':self.profile['kind'],'pockethost.web.deployment':self.job.id,'pockethost.web.service':service or 'web'}
        config={**(info.get('Config') or {}),'Labels':{**(info.get('Config',{}).get('Labels') or {}),**labels},'User':'10001:10001'}
        temporary=self.request('POST','/containers/create',{'Image':info['Id'],'User':'10001:10001','HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges'],'Memory':64*1024**2,'PidsLimit':32,'Tmpfs':{path:'rw,nosuid,noexec,size=1m'for path in declared}}})['Id']
        try:
            tag=self.job.id+('-'+service if service else '')
            value=self.request('POST','/commit?'+urllib.parse.urlencode({'container':temporary,'repo':'ph-web/'+self.job.app,'tag':tag,'pause':'false'}),config)
            return value['Id']
        finally:
            self.request('DELETE','/containers/'+temporary+'?force=true&v=true')

    def build(self,context,work,service=None):
        if self.profile['kind']=='auto' and not service:
            from web_app_railpack import build as railpack_build; return railpack_build(self,context,work)
        profile=service or self.profile
        if profile.get('image'):
            with self.job.blocking_heartbeat(): image=self.pull(profile['image'])
            return self.own_image(image,profile.get('_service',''))
        context=build_context(context,(self.job.snapshot.get('config')or{}).get('rootDirectory')or'.')
        path=pathlib.Path(context)/relative(profile.get('dockerfile','Dockerfile'))
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(pathlib.Path(context).resolve()): raise RuntimeError('Dockerfile absent ou hors du dépôt.')
        exclude_vcs(context,path)
        readable_source(context)
        tag='ph-web/'+self.job.app+':'+self.job.id+('-'+profile['_service'] if profile.get('_service') else '')
        envfile=pathlib.Path(work)/'runtime-build.env'
        import shlex
        envfile.write_text('\n'.join(name+'='+shlex.quote(value['value']) for name,value in self.job.snapshot.get('env',{}).items() if value.get('build'))+'\n'); envfile.chmod(0o600)
        args=['docker','--host','unix://'+self.socket,'buildx','build','--builder','ph-web-builder','--output','type=docker,compression=uncompressed','--progress','plain',*(['--no-cache'] if self.job.snapshot.get('fullRebuild') is True else []),'--label','pockethost.web=true','--label','pockethost.web.app='+self.job.app,'--label','pockethost.web.runtime='+self.profile['kind'],'--label','pockethost.web.deployment='+self.job.id,'--secret','id=env,src='+str(envfile),'-f',str(path),'-t',tag]
        if profile.get('buildTarget'): args+=['--target',profile['buildTarget']]
        public=public_build_arguments(self.job.snapshot.get('env',{}))
        for name in public:args+=['--build-arg',name]
        from web_app_build import cache_namespace
        cache_scope=cache_namespace(self.job.app,getattr(self,'hmac_key',''))
        if self.job.snapshot.get('fullRebuild')is True:cache_scope+='-'+self.job.id
        self.job.secrets.append(cache_scope)
        args+=['--build-arg','BUILDKIT_CACHE_MOUNT_NS']
        args.append(str(context))
        self.job.run(args,env={**build_environment(self.job,work),**public,'BUILDKIT_CACHE_MOUNT_NS':cache_scope})
        return tag

    def start(self,image,storage):
        profile=self.profile
        services=profile['compose']['services'] if profile['kind']=='compose' else {'web':profile}
        front=profile['compose']['front'] if profile['kind']=='compose' else 'web'
        order=profile['compose']['order'] if profile['kind']=='compose' else ['web']
        public=profile['kind']!='worker'
        quota={key:sum(item['resources'][key] for item in services.values()) for key in ('cpuMillicores','memoryMiB','pids')}
        admit(self.docker,admission_memory(profile),quota['cpuMillicores'],mode=profile['allocation'])
        tooling=prepare_tooling(self.docker)
        network=self.request('POST','/networks/create',{'Name':self.network,'Driver':'bridge','Internal':False,'Labels':{'pockethost.web':'true','pockethost.web.app':self.job.app,'pockethost.web.deployment':self.job.id}})['Id']
        result={'runtimeKind':profile['kind'],'public':public,'services':[],'images':{},'network':self.network,'storage':storage,'target':''}
        started={};dependency_deadline=time.monotonic()+DEPENDENCY_TIMEOUT_SECONDS
        from web_app_storage import prepare_mounts
        for name in order:
            item=services[name]; is_front=name==front
            self.wait_dependencies(item,started,dependency_deadline)
            container='ph-web-'+self.job.app+'-'+self.job.id+('' if is_front else '-'+name)
            current_image=image[name] if isinstance(image,dict) else image
            owned=json.loads(self.docker(['image','inspect',current_image]))[0]
            if owned.get('Config',{}).get('Labels',{}).get('pockethost.web.app')!=self.job.app or owned.get('Config',{}).get('Labels',{}).get('pockethost.web')!='true':raise RuntimeError('Image d’une autre application refusée.')
            validate_image_volumes(owned,self.managed_targets(name if profile['kind']=='compose'else'',owned))
            # Railpack (mise, Caddy…) : configuration inscriptible dans /tmp, surchargeable par les variables du site.
            values={'HOME':'/tmp','XDG_CONFIG_HOME':'/tmp/.config','XDG_DATA_HOME':'/tmp/.local/share','XDG_CACHE_HOME':'/tmp/.cache','MISE_CACHE_DIR':'/tmp/.cache/mise','MISE_STATE_DIR':'/tmp/.local/state/mise'}if profile['kind']=='auto'else{}
            values.update({key:value['value'] for key,value in self.job.snapshot.get('env',{}).items() if value.get('runtime') and (profile['kind']!='compose' or key in item['environment'])})
            values.update(NODE_ENV='production',HOSTNAME='0.0.0.0',PORT=str(item['port'] if profile['kind']=='compose' else profile.get('port') or 3000))
            mounts=prepare_mounts(self.root,self.job.app,storage,item.get('volumes') if profile['kind']=='compose' else None,self.docker)
            next_directory=(owned.get('Config',{}).get('Labels')or{}).get('pockethost.next.directory','')if(owned.get('Config',{}).get('Labels')or{}).get('pockethost.framework')=='nextjs'else''
            next_volume=''
            if next_directory:
                if not re.fullmatch(r'/app(?:/[A-Za-z0-9_.-]+)*/\.next',next_directory)or any(part in ('.','..')for part in next_directory.split('/')):raise RuntimeError('Répertoire Next déclaré par l’image invalide.')
                next_volume='ph-web-next-'+self.job.app+'-'+self.job.id+(''if is_front else'-'+name)
                self.docker(['volume','create','--label','pockethost.web=true','--label','pockethost.web.app='+self.job.app,'--label','pockethost.web.deployment='+self.job.id,next_volume])
                mounts.append({'Type':'volume','Source':next_volume,'Target':next_directory})
            mounts.append({'Type':'volume','Source':tooling,'Target':'/__pockethost_tools','ReadOnly':True})
            resources=item['resources']; port=str(item['port'] if profile['kind']=='compose' else profile.get('port') or 3000)
            from web_app_project_limits import admit as admit_project
            admit_project(self.root,self.job,self.docker,resources['memoryMiB'],resources['cpuMillicores'])
            config={'Image':current_image,'User':'10001:10001','Env':[key+'='+value for key,value in values.items()],
                'Labels':{'pockethost.web':'true','pockethost.web.app':self.job.app,'pockethost.web.deployment':self.job.id,'pockethost.web.private-db':'false','pockethost.web.private-db.wait':'false','pockethost.web.service':name,'pockethost.web.front':str(is_front).lower(),'pockethost.web.allocation':allocation_mode(profile['allocation']),'pockethost.web.priority':profile['priority'],'pockethost.web.tooling':tooling,'pockethost.web.healthpath':self.job.snapshot.get('config',{}).get('healthPath','/')},
                'HostConfig':{'NetworkMode':self.network,**resource_config(profile,resources,name),
                    'SecurityOpt':['no-new-privileges'],'CapDrop':['ALL'],'ReadonlyRootfs':True,'Mounts':mounts,'Tmpfs':{'/tmp':'rw,noexec,nosuid,size=128m,uid=10001,gid=10001'},'RestartPolicy':{'Name':'unless-stopped'},'LogConfig':{'Type':'json-file','Config':{'max-size':'10m','max-file':'3'}}},
                'NetworkingConfig':{'EndpointsConfig':{self.network:{'Aliases':[name]}}}}
            if next_directory:config['HostConfig']['Tmpfs'][next_directory+'/cache']='rw,nosuid,uid=10001,gid=10001,size=128m'
            if public and is_front:
                host_port=loopback_port(self.root)
                config['ExposedPorts']={port+'/tcp':{}}; config['HostConfig']['PortBindings']={port+'/tcp':[{'HostIp':'127.0.0.1','HostPort':host_port}]}
            validate_image_volumes(owned,{mount['Target']for mount in mounts}|set(config['HostConfig']['Tmpfs']))
            if item.get('command'): config['Cmd']=item['command']
            if item.get('entrypoint'): config['Entrypoint']=item['entrypoint']
            if item.get('healthcheck'): config['Healthcheck']=item['healthcheck']
            from web_app_private_db import prepare_app,attach_app
            private_database=prepare_app(self.job,config,self.root,self.docker,name,tooling)
            created=self.request('POST','/containers/create?'+urllib.parse.urlencode({'name':container}),config)['Id']; self.created.append(created)
            self.request('POST','/containers/'+created+'/start')
            started[name]=created
            if private_database:attach_app(created)
            info=json.loads(self.docker(['inspect',container]))[0]
            if next_directory:
                from web_app_storage import _measure
                baseline=_measure(self.docker,self.job.app,next_volume)
                budget={'appId':self.job.app,'deploymentId':self.job.id,'container':container,'volume':next_volume,'path':next_directory,'baselineBytes':baseline,**({}if is_front else{'service':name})}
                folder=self.root/'runtime-budgets';folder.mkdir(mode=0o750,exist_ok=True);file=folder/(self.job.id+(''if is_front else'-'+name)+'.json');temporary=file.with_suffix('.tmp');temporary.write_text(json.dumps(budget));temporary.chmod(0o640);os.replace(temporary,file)
            result['services'].append({'name':name,'container':container,'id':created,'image':current_image,'front':is_front})
            result['images'][name]=json.loads(self.docker(['image','inspect',current_image]))[0]['Id']
            if is_front:
                result.update(image=owned['Id'],container=container,port=int(port))
                if next_directory:result.update(framework='nextjs',serverDirectory=str(pathlib.PurePosixPath(next_directory).parent.relative_to('/app')))
                if public:
                    bindings=info.get('NetworkSettings',{}).get('Ports',{}).get(port+'/tcp') or []
                    if len(bindings)!=1 or bindings[0].get('HostIp')!='127.0.0.1'or bindings[0].get('HostPort')!=host_port: raise RuntimeError('Le frontal doit conserver son port loopback réservé.')
                    result['target']='http://127.0.0.1:'+bindings[0]['HostPort']
        return result


def public_build_arguments(environment):
    """Only explicit public build-scope values enter ARG; values never enter argv."""
    result={}
    for name,value in environment.items():
        if not isinstance(name,str)or not re.fullmatch(r'NEXT_PUBLIC_[A-Za-z0-9_]{0,89}',name)or not isinstance(value,dict)or value.get('build')is not True:continue
        raw=value.get('value')
        if not isinstance(raw,str)or '\x00'in raw or len(raw)>10000:raise RuntimeError('Variable publique de construction invalide.')
        result[name]=raw
    return {key:result[key]for key in sorted(result)}


def build_environment(job,work):
    """Per-job Docker auth, with the existing isolated Buildx metadata retained."""
    profile=validate_profile(job.snapshot.get('runtime'));registry=profile.get('registry')or{}
    environment=dict(os.environ)
    environment['BUILDX_CONFIG']=environment.get('BUILDX_CONFIG')or str(pathlib.Path(environment.get('DOCKER_CONFIG',str(pathlib.Path.home()/'.docker')))/'buildx')
    folder=pathlib.Path(work)/'docker-auth';folder.mkdir(mode=0o700,exist_ok=True);auths={}
    if registry:
        auth=base64.b64encode((registry['username']+':'+registry['password']).encode()).decode()
        server='https://index.docker.io/v1/'if registry['server']in('docker.io','index.docker.io','registry-1.docker.io')else registry['server']
        auths[server]={'auth':auth};job.secrets.append(auth)
    file=folder/'config.json';file.write_text(json.dumps({'auths':auths}));file.chmod(0o600);environment['DOCKER_CONFIG']=str(folder)
    return environment


def tooling_volume_name(image):
    if not isinstance(image,str)or not re.fullmatch(r'sha256:[a-f0-9]{64}',image):raise RuntimeError('Image d’outillage immuable invalide.')
    return 'ph-web-tools-'+image[7:27]


def prepare_tooling(docker):
    from web_app_storage import _helper
    image=_helper(docker);volume=tooling_volume_name(image)
    try:
        info=json.loads(docker(['volume','inspect',volume]))[0]
        if info.get('Driver')!='local'or info.get('Options')or info.get('Labels',{}).get('pockethost.tools.image')!=image:raise RuntimeError('Volume d’outillage existant non approuvé.')
    except __import__('subprocess').CalledProcessError:
        docker(['volume','create','--label','pockethost.tools=true','--label','pockethost.tools.image='+image,volume])
    base=['run','--rm','--network','none','--read-only','--user','0:0','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','128m','--memory-swap','128m','--cpus','0.1','--pids-limit','32']
    try:
        ready=json.loads(docker(base+['--mount','type=volume,source='+volume+',target=/tools,readonly',image,'cat','/tools/ready.json'],timeout=15))
        if ready!={'format':1,'image':image}:raise RuntimeError('Le manifeste d’outillage ne correspond pas à l’image approuvée.')
    except __import__('subprocess').CalledProcessError:
        script=r'''set -eu
mkdir -p /tools/lib
cp -L /usr/local/bin/node /tools/node
cp -L /bin/sh /tools/sh
for lib in $(ldd /usr/local/bin/node /bin/sh | awk '/=> \// {print $3} /^[[:space:]]*\/.*ld-linux/ {print $1}'); do cp -L "$lib" "/tools/lib/$(basename "$lib")"; done
loader=$(ldd /usr/local/bin/node | awk '/^[[:space:]]*\/.*ld-linux/ {print $1;exit}')
test -n "$loader"
cp -L "$loader" /tools/ld.so
chmod -R a+rX /tools
/tools/ld.so --library-path /tools/lib /tools/node -e 'process.exit(process.versions.node.startsWith("24.")?0:1)'
'''
        script+="printf '%s' "+__import__('shlex').quote(json.dumps({'format':1,'image':image},separators=(',',':')))+" > /tools/ready.json\nchmod 644 /tools/ready.json\n"
        docker(base+['--mount','type=volume,source='+volume+',target=/tools',image,'/bin/sh','-ec',script],timeout=60)
    return volume


def tooling_node_prefix():
    return ['/__pockethost_tools/ld.so','--library-path','/__pockethost_tools/lib','/__pockethost_tools/node']
