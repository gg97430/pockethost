"""Explicit, resumable deletion. Only exact controller-approved application IDs."""
import datetime,json,pathlib,re,shutil,time
ID=re.compile(r'[a-z0-9]{15}\Z')

def validate(job):
 if not isinstance(job,dict)or not ID.fullmatch(job.get('id',''))or not re.fullmatch(r'[A-Za-z0-9]{40}',job.get('lease','')):raise RuntimeError('Bail de suppression invalide.')
 if job.get('kind')not in ('app','project')or not ID.fullmatch(job.get('targetId',''))or not isinstance(job.get('apps'),list)or len(job['apps'])>500:raise RuntimeError('Périmètre de suppression invalide.')
 seen=set()
 for item in job['apps']:
  if not isinstance(item,dict)or not ID.fullmatch(item.get('id',''))or item['id']in seen:raise RuntimeError('Application à supprimer invalide.')
  seen.add(item['id'])
  for key in ('deployments','taskRuns'):
   if not isinstance(item.get(key),list)or len(item[key])>100000 or not all(isinstance(v,str)and ID.fullmatch(v)for v in item[key]):raise RuntimeError('Historique à supprimer invalide.')
 if job['kind']=='app'and seen!={job['targetId']}:raise RuntimeError('La suppression doit viser une seule application.')

def remove_app(root,item,docker,atomic,load,pulse):
 from web_app_private_db import private_database_operations
 with private_database_operations():return _remove_app(root,item,docker,atomic,load,pulse)

def _remove_app(root,item,docker,atomic,load,pulse):
 app=item['id'];pulse('Retrait des routes')
 atomic(root/'preview-deletions'/(app+'.json'),{'appId':app,'reason':'explicit-deletion'})
 routes=load(root/'routes.json',{'version':1,'routes':[]});routes['routes']=[r for r in routes.get('routes',[])if r.get('appId')!=app];atomic(root/'routes.json',routes)
 for filename in ['runtime-publications.json','runtime-alerts.json']:
  current=load(root/filename);current.pop(app,None);atomic(root/filename,current)
 pulse('Suppression des conteneurs')
 for ident in docker(['ps','-aq','--filter','label=pockethost.web=true','--filter','label=pockethost.web.app='+app]).split():
  info=json.loads(docker(['inspect',ident]))[0];labels=info.get('Config',{}).get('Labels')or{}
  if labels.get('pockethost.web.app')!=app or labels.get('pockethost.web')!='true'or not re.fullmatch('/ph-web-'+app+r'-[a-z0-9]{15}(?:-[a-z][a-z0-9-]{0,30})?',info.get('Name','')):raise RuntimeError('Conteneur étranger : suppression refusée.')
  docker(['rm','-f',info['Id']]);pulse('Suppression des conteneurs')
 pulse('Suppression des volumes')
 for name in docker(['volume','ls','-q','--filter','label=pockethost.web.app='+app]).split():
  info=json.loads(docker(['volume','inspect',name]))[0]
  if info.get('Labels',{}).get('pockethost.web.app')!=app or info.get('Driver')!='local'or info.get('Options')or not re.fullmatch('ph-web-(?:next|data)-'+app+'-[a-z0-9-]{1,160}',name):raise RuntimeError('Volume étranger : suppression refusée.')
  docker(['volume','rm',name]);pulse('Suppression des volumes')
 pulse('Suppression des images')
 for ident in set(docker(['image','ls','-q','--filter','label=pockethost.web.app='+app]).split()):
  info=json.loads(docker(['image','inspect',ident]))[0]
  if (info.get('Config',{}).get('Labels')or{}).get('pockethost.web.app')!=app:raise RuntimeError('Image étrangère : suppression refusée.')
  tags=[tag for tag in info.get('RepoTags')or[]if tag.startswith('ph-web/'+app+':')]
  for tag in tags:docker(['image','rm',tag])
  if not info.get('RepoTags'):docker(['image','rm',info['Id']])
  pulse('Suppression des images')
 for ident in docker(['network','ls','-q','--filter','label=pockethost.web.app='+app]).split():
  info=json.loads(docker(['network','inspect',ident]))[0]
  if info.get('Labels',{}).get('pockethost.web.app')!=app or not info.get('Name','').startswith('ph-web-net-'+app+'-')or info.get('Containers'):raise RuntimeError('Réseau étranger ou encore utilisé : suppression refusée.')
  docker(['network','rm',info['Id']]);pulse('Suppression des réseaux')
 def remove(path):
  if path.resolve()!=path:raise RuntimeError('Chemin de nettoyage symbolique refusé.')
  if path.is_dir():shutil.rmtree(path)
  else:path.unlink(missing_ok=True)
 pulse('Suppression des fichiers locaux')
 for namespace in ['assets','backups','private-db-runtime']:
  remove(root/namespace/app)
 for namespace in ['storage','runtime-logs','image-reuse']:
  remove(root/namespace/(app+'.json'))
 for dep in item['deployments']:
  remove(root/'logs'/(dep+'.log'));remove(root/'runtime-budgets'/(dep+'.json'))

def tick(root,api,docker,atomic,load):
 job=api('deletions/claim',{}).get('job')
 if not job:return False
 validate(job);root=pathlib.Path(root);last=0
 def pulse(stage,force=False):
  nonlocal last
  if not force and time.monotonic()-last<5:return
  api('deletions/report',{'id':job['id'],'lease':job['lease'],'status':'running','stage':stage});last=time.monotonic()
  checked=datetime.datetime.now(datetime.timezone.utc).isoformat();status=load(root/'worker-status.json');status.update({'ready':True,'checkedAt':checked,'deletion':job['id']});atomic(root/'worker-status.json',status)
 try:
  for item in job['apps']:
   pulse('Nettoyage de l’application',True);remove_app(root,item,docker,atomic,load,pulse)
  api('deletions/report',{'id':job['id'],'lease':job['lease'],'status':'ready','removed':True})
 except Exception as error:
  # Partial removal is intentionally not rolled back. The same immutable job can
  # be retried; deleted resources are absent from the next exact-label inventory.
  message=str(error)if isinstance(error,RuntimeError)else'Le nettoyage a été interrompu. Réessayez la suppression.'
  api('deletions/report',{'id':job['id'],'lease':job['lease'],'status':'failed','error':message})
 return True
