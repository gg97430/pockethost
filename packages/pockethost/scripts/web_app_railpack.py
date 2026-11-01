"""Construction automatique (Railpack) : analyse conteneurisée, plan validé, build BuildKit isolé."""
import hashlib
import hmac
import json
import os
import pathlib
import re
import shutil
import stat
import threading
import time

# Seule source de vérité. install-railpack.sh porte les mêmes valeurs (vérifié par test-web-app-railpack.py).
# Monter de version : changer ensemble version/sha256, mise/miseSha256, frontend et images (WEB_HOSTING.md).
RAILPACK={'version':'0.42.0',
 'binary':'/opt/pockethost-web/bin/railpack','sha256':'90a5041d2cedd2bad09e38f9bd6697a9aedce562cd4e7bc77603ab850f0e8647',
 'mise':'2026.10.8','miseBinary':'/opt/pockethost-web/lib/railpack/mise-2026.10.8','miseSha256':'043dc6b9077db988269ca2f85059851cb09fc080355350021363e95a5e227f84',
 'frontend':'ghcr.io/railwayapp/railpack-frontend:v0.42.0@sha256:a42a12399fa9e83a09e475f69dbf8a45a8a74ea38a4485fcb401356d84317a14',
 'images':{'ghcr.io/railwayapp/railpack-builder:mise-2026.10.8':'sha256:4ed1984b188ac56bfc0d7d0ec1eaf27faff36c3cee7a6aaa0e8f10bf6b823e94',
           'ghcr.io/railwayapp/railpack-runtime:mise-2026.10.8':'sha256:218b9a2a318da1bdebeeb8ad8fd87547bf85af4d06faee538800ad12862791f3'}}
OWNER=0
SECRET_NAME=re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')
PREVIOUS=re.compile(r'^[A-Za-z0-9_.+-]{1,64}@[A-Za-z0-9_.+-]{1,64}$')
PLAN_KEYS={'steps','caches','secrets','deploy','exclude'}
IGNORED={'.git','node_modules','.next'}
UNEXPECTED='Plan Railpack inattendu (version incompatible ?).'
# Seules les entrées illisibles pour l'UID 10001 changent : un chmod -R recopierait tout /app dans une couche (≈ 30 s).
READABLE="sh -c 'find /app -path /app/node_modules -prune -o \\( -type d ! -perm -o=rx -o ! -type d ! -perm -o=r \\) -exec chmod a+rX {} +'"
OUTPUT_LIMIT=2*1024**2
# Images que le fournisseur PHP de Railpack impose par tag mobile Docker Hub (php.go) : ni choisies par
# l'utilisateur, ni épinglables d'avance (le tag suit la version PHP résolue). Les épingler = les ajouter à RAILPACK['images'].
PROVIDER_IMAGE=re.compile(r'^(?:dunglas/frankenphp:php[0-9][0-9.]*-trixie|composer:latest)$')
_hashes={};_pull_after=[0.0];_pulling=[]


class CommandTimeout(RuntimeError):
    """Levée par Job.run(limit=…) : le finally du worker tue le groupe de processus."""


def pinned(reference):
    """repo:tag@sha256:… -> repo@sha256:… pour les commandes docker locales (inspect, pull, run)."""
    name,digest=reference.split('@',1);last=name.rsplit('/',1)
    return (last[0]+'/' if len(last)==2 else '')+last[-1].split(':')[0]+'@'+digest


def _verified(path,expected):
    info=os.lstat(path);parent=os.lstat(os.path.dirname(path))
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=OWNER or info.st_mode&0o022 or parent.st_uid!=OWNER or parent.st_mode&0o022:return False
    key=(info.st_ino,info.st_size,info.st_mtime_ns);cached=_hashes.get(path)
    if not cached or cached[0]!=key:
        digest=hashlib.sha256()
        with open(path,'rb') as source:
            for chunk in iter(lambda:source.read(1024**2),b''):digest.update(chunk)
        cached=_hashes[path]=(key,digest.hexdigest())
    return hmac.compare_digest(cached[1],expected)


def tools_ready(config=RAILPACK):
    """Binaire + mise vérifiés (root, non inscriptibles, sha256). N'échoue jamais."""
    try:return _verified(config['binary'],config['sha256']) and _verified(config['miseBinary'],config['miseSha256'])
    except Exception:return False


def _pull_later(docker,reference):
    # Jamais dans la boucle principale : un ghcr.io lent figerait worker-status.json (« Worker non démarré. »).
    if time.monotonic()<_pull_after[0] or any(thread.is_alive() for thread in _pulling):return
    def pull():
        try:docker(['pull',reference],timeout=180)
        except Exception:_pull_after[0]=time.monotonic()+600
    thread=threading.Thread(target=pull,name='web-railpack-pull',daemon=True);_pulling[:]=[thread];thread.start()


def railpack_ready(docker,config=RAILPACK):
    """Outils vérifiés et frontend présent (inspect rapide ; s'il manque, téléchargement en arrière-plan). N'échoue jamais."""
    try:
        if not tools_ready(config):return False
        try:docker(['image','inspect',pinned(config['frontend'])],timeout=10);return True
        except Exception:_pull_later(docker,pinned(config['frontend']));return False
    except Exception:return False


def _ignore(folder,names):
    skipped=[]
    for name in names:
        if name in IGNORED or name=='.env' or name.startswith('.env.'):skipped.append(name);continue
        mode=os.lstat(os.path.join(folder,name)).st_mode
        if not(stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):skipped.append(name)
    return skipped


def copy_context(context,root_directory,work):
    """Copie du seul dossier du projet, sans métadonnées Git, dépendances, cache Next ni fichiers .env."""
    context=pathlib.Path(context).resolve();app=(context/(root_directory or '.')).resolve()
    if not app.is_relative_to(context) or not app.is_dir():raise RuntimeError('Dossier du projet invalide.')
    src=pathlib.Path(work)/'railpack-src'
    shutil.copytree(app,src,symlinks=True,ignore=_ignore)
    base=src.resolve();count=0
    for folder,dirs,files in os.walk(src):
        for name in dirs+files:
            count+=1;path=pathlib.Path(folder)/name
            if count>100000:raise RuntimeError('Archive source trop volumineuse.')
            if path.is_symlink():
                target=os.readlink(path)
                try:inside=not os.path.isabs(target) and (path.parent/target).resolve().is_relative_to(base)
                except (OSError,RuntimeError):inside=False
                if not inside:raise RuntimeError('Lien symbolique hors du projet refusé.')
            # Le worker tourne en umask 027 : sans ces droits, l'image (fichiers root) serait illisible pour l'UID 10001 du site.
            elif path.is_dir():path.chmod(0o755)
            else:path.chmod(0o755 if path.stat().st_mode&0o111 else 0o644)
    src.chmod(0o755)
    return src


def _read(path):
    # O_NOFOLLOW|O_NONBLOCK + fstat : ni lien, ni FIFO (open bloquant hors de Job.run), ni dossier ; lecture bornée.
    try:
        with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as source:
            info=os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size>OUTPUT_LIMIT:raise ValueError()
            raw=source.read(OUTPUT_LIMIT+1)
        if len(raw)>OUTPUT_LIMIT:raise ValueError()
        value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError()
        return value
    except (OSError,ValueError):raise RuntimeError('Plan Railpack illisible.') from None


def _written(path):
    try:return os.lstat(path).st_size>0
    except OSError:return False


def providers(info):
    rows=info.get('detectedProviders')
    if not isinstance(rows,list):return []
    return [row if isinstance(row,str) else row['name'] for row in rows if isinstance(row,str) and row or isinstance(row,dict) and isinstance(row.get('name'),str) and row['name']]


def packages(info):
    rows=info.get('resolvedPackages');items=rows.items() if isinstance(rows,dict) else [(None,row) for row in rows] if isinstance(rows,list) else []
    found=[]
    for key,row in items:
        if isinstance(row,dict):
            name=row.get('name') or key;version=row.get('resolvedVersion')
            if isinstance(name,str) and isinstance(version,str) and name and version:found.append((name,version))
    return found


def messages(info):
    rows=info.get('logs')
    if not isinstance(rows,list):return []
    found=[]
    for row in rows:
        if isinstance(row,str):found.append(('info',row))
        elif isinstance(row,dict) and isinstance(row.get('msg',row.get('message')),str):found.append((str(row.get('level','info')),row.get('msg',row.get('message'))))
    return found


def _image(value,custom):
    if not isinstance(value,str) or not value or len(value)>255:raise RuntimeError(UNEXPECTED)
    if value in RAILPACK['images']:return value+'@'+RAILPACK['images'][value]
    custom.append(value);return value


def validate_plan(plan,app,profile,names):
    """Fonction pure : renvoie (plan réécrit, images personnalisées). Caches préfixés, secrets imposés, images épinglées."""
    # Railpack ≥ 0.42 annonce son schéma JSON ("$schema") : simple métadonnée, retirée avant transmission au frontend.
    if isinstance(plan,dict) and isinstance(plan.get('$schema',''),str):plan={key:value for key,value in plan.items() if key!='$schema'}
    if not isinstance(plan,dict) or set(plan)-PLAN_KEYS:raise RuntimeError(UNEXPECTED)
    steps=plan.get('steps') or [];caches=plan.get('caches') or {};deploy=plan.get('deploy')
    if not isinstance(steps,list) or not isinstance(caches,dict) or not isinstance(plan.get('exclude',[]),list) or not isinstance(plan.get('secrets',[]),list):raise RuntimeError(UNEXPECTED)
    if not isinstance(deploy,dict) or not isinstance(deploy.get('startCommand'),str) or not deploy['startCommand'].strip():
        raise RuntimeError('Railpack n’a trouvé aucune commande de démarrage. Ajoutez un script « start », un Procfile, ou renseignez la commande de démarrage du profil.')
    for name in names:
        if not isinstance(name,str) or not SECRET_NAME.fullmatch(name):raise RuntimeError('Nom de variable de build invalide pour BuildKit.')
    names=sorted(names);prefix='ph-web-'+app+'-';custom=[]
    renamed={key:prefix+key for key in caches}
    def rename(values):
        if not isinstance(values,list) or any(value not in renamed for value in values):raise RuntimeError(UNEXPECTED)
        return [renamed[value] for value in values]
    result={**plan,'caches':{renamed[key]:value for key,value in caches.items()},'secrets':names,'steps':[]}
    for step in steps:
        if not isinstance(step,dict):raise RuntimeError(UNEXPECTED)
        step=dict(step)
        if 'caches' in step:step['caches']=rename(step['caches'])
        # Toute liste non vide devient ['*'] : une sous-liste fait exécuter par le frontend un hachage dans alpine:latest
        # (non épinglé, réseau BuildKit) avec TOUS les secrets en variables. Aucune perte : chaque étape reçoit déjà Plan.Secrets.
        if 'secrets' in step and step['secrets']!=[]:step['secrets']=['*']
        inputs=[];commands=[]
        for item in step.get('inputs') or []:
            if not isinstance(item,dict):raise RuntimeError(UNEXPECTED)
            inputs.append({**item,'image':_image(item['image'],custom)} if 'image' in item else item)
        for item in step.get('commands') or []:
            if not isinstance(item,dict):raise RuntimeError(UNEXPECTED)
            item=dict(item)
            if 'image' in item:item['image']=_image(item['image'],custom)
            if 'caches' in item:item['caches']=rename(item['caches'])
            commands.append(item)
        # Le site tourne sous l'UID 10001 : après la copie du projet (dossiers 0750 d'une archive extraite en umask 027),
        # tout /app doit être lisible. Dans le plan, pour aussi invalider le cache BuildKit (insensible aux droits des dossiers).
        if any(item.get('local') is True for item in inputs):commands.append({'cmd':READABLE,'customName':'droits de lecture pour le site'})
        if 'inputs' in step:step['inputs']=inputs
        if 'commands' in step or commands:step['commands']=commands
        result['steps'].append(step)
    deploy=dict(deploy)
    if isinstance(deploy.get('base'),dict) and 'image' in deploy['base']:deploy['base']={**deploy['base'],'image':_image(deploy['base']['image'],custom)}
    if 'inputs' in deploy:
        if not isinstance(deploy['inputs'],list) or any(not isinstance(item,dict) for item in deploy['inputs']):raise RuntimeError(UNEXPECTED)
        deploy['inputs']=[{**item,'image':_image(item['image'],custom)} if 'image' in item else item for item in deploy['inputs']]
    result['deploy']=deploy
    return result,sorted(set(custom))


def _private(path,text):
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w') as output:output.write(text)


def _mask(job,text):
    for secret in getattr(job,'secrets',[]):
        if secret:text=text.replace(secret,'[masqué]')
    return text


def previous_versions(root,app):
    try:value=json.loads((pathlib.Path(root)/'railpack-versions'/(app+'.json')).read_text())
    except (OSError,ValueError):return []
    return [item for item in value if isinstance(item,str) and PREVIOUS.fullmatch(item)][:50] if isinstance(value,list) else []


def save_versions(root,app,info):
    values=[name+'@'+version for name,version in packages(info) if PREVIOUS.fullmatch(name+'@'+version)][:50]
    if not values:return
    folder=pathlib.Path(root)/'railpack-versions';folder.mkdir(mode=0o750,exist_ok=True);path=folder/(app+'.json');temporary=path.with_name(path.name+'.tmp')
    temporary.unlink(missing_ok=True);temporary.write_text(json.dumps(values));temporary.chmod(0o640);os.replace(temporary,path)


def build(engine,context,work):
    """Appelée par RuntimeEngine.build pour kind=auto ; renvoie l'identifiant sha256 de l'image ré-appropriée."""
    from web_app_runtime import build_environment
    job=engine.job;profile=engine.profile;work=pathlib.Path(work);app=job.app
    if not tools_ready():raise RuntimeError('Railpack n’est pas installé sur ce serveur ou son empreinte ne correspond pas à la version épinglée ('+RAILPACK['version']+'). Lancez install-railpack.sh.')
    key=getattr(engine,'hmac_key','') or ''
    if len(key)<32:raise RuntimeError('Secret du worker requis pour la construction automatique.')
    root_directory=(job.snapshot.get('config') or {}).get('rootDirectory') or '.'
    src=copy_context(context,root_directory,work)
    variables={name:item.get('value','') for name,item in (job.snapshot.get('env') or {}).items() if item.get('build')}
    for name in variables:
        if not isinstance(name,str) or not SECRET_NAME.fullmatch(name):raise RuntimeError('Nom de variable de build invalide pour BuildKit.')
    environment={key:value for key,value in build_environment(job,work).items() if key!='GITHUB_TOKEN'}
    frontend=pinned(RAILPACK['frontend'])
    try:engine.docker(['image','inspect',frontend],timeout=10)
    except Exception:
        # Dans le job (battements, journal, délai) et non dans la boucle principale du worker.
        job.log('Téléchargement du frontend Railpack épinglé '+RAILPACK['version']+'.\n',True)
        try:job.run(['docker','--host','unix://'+engine.socket,'pull',frontend],env=environment,limit=300)
        except CommandTimeout:raise RuntimeError('Téléchargement du frontend Railpack trop long (ghcr.io). Relancez le déploiement.') from None
    name='ph-web-'+app+'-'+job.id+'-railpack'
    args=['docker','--host','unix://'+engine.socket,'run','--rm','--name',name,
        '--label','pockethost.web.app='+app,'--label','pockethost.web.deployment='+job.id,'--label','pockethost.web.role=railpack-prepare',
        '--network','bridge','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--user','0:0',
        # exec explicite : Docker part de noexec,nosuid,nodev pour --tmpfs ; mise exécute ses outils depuis /tmp.
        '--memory','512m','--memory-swap','512m','--cpus','0.5','--pids-limit','128','--tmpfs','/tmp:rw,exec,nosuid,nodev,size=256m',
        '--mount','type=bind,src='+RAILPACK['binary']+',dst=/usr/local/bin/ph-railpack,readonly',
        '--mount','type=bind,src='+RAILPACK['miseBinary']+',dst=/tmp/railpack/mise/mise-'+RAILPACK['mise']+',readonly',
        '--mount','type=bind,src='+str(src)+',dst=/app,readonly']
    options=['-e','HOME=/tmp','--entrypoint','/usr/local/bin/ph-railpack',frontend,
        'prepare','/app','--plan-out','/out/plan.json','--info-out','/out/info.json','--hide-pretty-plan']
    if profile.get('startCommand'):options+=['--start-cmd',profile['startCommand']]
    if profile.get('buildCommand'):options+=['--build-cmd',profile['buildCommand']]
    # Une variable RAILPACK_PRUNE_DEPS du site garde la priorité sur la case « Alléger l'image ».
    if profile.get('pruneDependencies') and 'RAILPACK_PRUNE_DEPS' not in variables:options+=['--env','RAILPACK_PRUNE_DEPS=true']
    for variable,value in sorted(variables.items()):
        if variable.startswith('RAILPACK_'):
            if not isinstance(value,str) or len(value)>4096 or re.search(r'[\0\r\n]',value):raise RuntimeError('Variable '+variable+' invalide (une ligne, 4096 caractères maximum).')
            options+=['--env',variable+'='+value]
    for item in previous_versions(engine.root,app):options+=['--previous',item]
    for attempt in (0,1):
        # Deux fichiers pré-créés montés un à un : /out reste en lecture seule (aucune entrée, FIFO ni inode
        # créable par le code du dépôt) ; leur taille est surveillée à chaque seconde par Job.run(watch=…).
        out=work/('railpack-out-'+str(attempt));out.mkdir(mode=0o700);files={filename:out/filename for filename in ('plan.json','info.json')}
        for path in files.values():_private(path,'')
        def watch():
            for path in files.values():
                if os.lstat(path).st_size>OUTPUT_LIMIT:raise RuntimeError('Analyse Railpack refusée : sortie trop volumineuse (2 Mio maximum).')
        mounts=[part for filename,path in files.items() for part in ('--mount','type=bind,src='+str(path)+',dst=/out/'+filename)]
        try:code=job.run(args+mounts+options,env=environment,limit=300,ok=(0,1,75),watch=watch)
        except CommandTimeout:raise RuntimeError('Analyse Railpack trop longue (5 minutes maximum).') from None
        finally:
            # Tuer le client CLI n'arrête pas le conteneur : suppression systématique.
            try:engine.docker(['rm','-f',name],timeout=30)
            except Exception:pass
        if code!=75:break
        if not attempt:job.log('Problème réseau temporaire pendant l’analyse Railpack : nouvelle tentative dans 10 secondes.\n',True);time.sleep(10)
    else:raise RuntimeError('Analyse Railpack interrompue par un problème réseau temporaire (résolution des versions). Relancez le déploiement.')
    # Code 1 sans info.json : erreur de génération (railpack.json invalide, configuration…) écrite sur stderr, déjà journalisée et masquée.
    if code==1 and not _written(files['info.json']):raise RuntimeError('Analyse Railpack refusée : consultez le journal de construction.')
    info=_read(files['info.json'])
    if code==1:
        if not providers(info):raise RuntimeError('Railpack n’a reconnu aucun langage dans « '+root_directory+' ». Ajoutez un fichier de projet (package.json, requirements.txt, pyproject.toml, go.mod, composer.json, Gemfile, index.html…) ou choisissez le runtime Dockerfile.')
        error=next((text for level,text in messages(info) if level.lower()=='error'),'consultez le journal de construction.')
        # Masquage AVANT troncature : jamais de fragment de secret dans l'erreur remontée au contrôleur.
        raise RuntimeError('Analyse Railpack refusée : '+' '.join(_mask(job,error).split())[:300])
    plan,custom=validate_plan(_read(files['plan.json']),app,profile,list(variables))
    found=providers(info);tools=packages(info)
    bases={item['image'] for step in plan['steps'] for item in step.get('inputs',[]) if 'image' in item}
    if isinstance(plan['deploy'].get('base'),dict) and 'image' in plan['deploy']['base']:bases.add(plan['deploy']['base']['image'])
    lines=['Railpack '+RAILPACK['version']+' · fournisseur détecté : '+(', '.join(found) or 'aucun'),
        'Outils résolus : '+(', '.join(name+' '+version for name,version in tools) or 'aucun'),
        'Commande de démarrage : '+plan['deploy']['startCommand'],'Images de base : '+(', '.join(sorted(bases)) or 'aucune')]
    lines+=[('Image imposée par le fournisseur Railpack, tag mobile non épinglé par la plateforme : ' if PROVIDER_IMAGE.fullmatch(item) else 'Image de base personnalisée (non épinglée par la plateforme) : ')+item for item in custom]
    lines+=['Railpack ['+level+'] '+text for level,text in messages(info)[:20]]
    job.log('\n'+''.join(' '.join(_mask(job,line).split())[:300]+'\n' for line in lines),True)
    planfile=work/'railpack-plan.json';_private(planfile,json.dumps(plan))
    folder=work/'railpack-secrets';folder.mkdir(mode=0o700)
    for variable,value in variables.items():_private(folder/variable,value)
    digest=hmac.new(key.encode(),json.dumps(sorted(variables.items())).encode(),hashlib.sha256).hexdigest() if variables else ''
    # Identifiants de cache BuildKit = cache-key + '-' + clé : jeton HMAC non devinable, car un Dockerfile d'un autre client
    # sur le builder partagé pourrait sinon viser ph-web-<app>-… avec RUN --mount=type=cache,id=….
    cache='ph-web-'+app+'-'+hmac.new(key.encode(),('railpack-cache:'+app).encode(),hashlib.sha256).hexdigest()[:16]
    tag='ph-web/'+app+':'+job.id
    # Reconstruction complète : ni couches en cache ni caches de paquets (npm, pip…) de la construction précédente.
    full=job.snapshot.get('fullRebuild') is True
    if full:cache+='-'+job.id
    args=['docker','--host','unix://'+engine.socket,'buildx','build','--builder','ph-web-builder','--output','type=docker,compression=uncompressed','--progress','plain',*(['--no-cache'] if full else []),
        '--build-arg','BUILDKIT_SYNTAX='+RAILPACK['frontend'],'--build-arg','cache-key='+cache,'--build-arg','secrets-hash='+digest,
        '--label','pockethost.web=true','--label','pockethost.web.app='+app,'--label','pockethost.web.deployment='+job.id]
    for secret in sorted(variables):args+=['--secret','id='+secret+',src='+str(folder/secret)]
    args+=['-f',str(planfile),'-t',tag,str(src)]
    job.run(args,env=environment)
    image=engine.own_image(tag)
    save_versions(engine.root,app,info)
    return image
