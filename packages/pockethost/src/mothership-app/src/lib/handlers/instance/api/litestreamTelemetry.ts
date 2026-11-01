/** Private, bounded telemetry. Credentials stay in the controller DB / managed config. */
export const LITESTREAM_TELEMETRY = String.raw`
import sys,os,json,pathlib,sqlite3,subprocess,time,datetime,re,signal,shutil,tempfile,fcntl,urllib.request,hashlib
os.umask(0o077)
root=pathlib.Path(sys.argv[1]).absolute();lsroot=pathlib.Path(sys.argv[2]).absolute();ident=sys.argv[3];action=sys.argv[4]
if not re.fullmatch('[a-z0-9]{15}',ident):raise ValueError('Invalid id')
def safe(path):
 if path.resolve()!=path:raise ValueError('Invalid location')
 return path
safe(root);safe(lsroot)
folder=safe(root/'instances'/ident/'pb_data');source=safe(folder/'data.db')
private=safe(lsroot/'telemetry');private.mkdir(parents=True,exist_ok=True,mode=0o700);private.chmod(0o700)
def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def load(path):
 safe(path)
 try:return json.loads(path.read_text())
 except (FileNotFoundError,ValueError):return {}
def save(path,value):
 safe(path);tmp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
 with open(tmp,'x') as f:json.dump(value,f)
 os.replace(tmp,path)
def command(args,timeout=5):return subprocess.check_output(args,timeout=timeout,stderr=subprocess.DEVNULL,text=True)
def policy():
 db=sqlite3.connect((root/'mothership/pb_data/data.db').as_uri()+'?mode=ro',uri=True,timeout=2);db.row_factory=sqlite3.Row
 try:
  row=db.execute('SELECT * FROM instance_litestream_replicas WHERE instance=? LIMIT 1',(ident,)).fetchone()
  return dict(row) if row else {}
 finally:db.close()
p=policy();binding=hashlib.sha256(json.dumps([p.get(k) for k in ['s3Endpoint','s3Bucket','replicaPath']],sort_keys=True).encode()).hexdigest();statefile=private/(ident+'-verification.json');remotefile=private/(ident+'-remote.json');config=lsroot/'litestream.yml'
def failure(e):
 code=str(getattr(e,'response',{}).get('Error',{}).get('Code',''))
 if code in ['AccessDenied','InvalidAccessKeyId','SignatureDoesNotMatch']:return 'Accès S3 refusé : vérifiez les clés et les droits sur le préfixe.'
 if code in ['NoSuchBucket','404']:return 'Bucket S3 introuvable.'
 if isinstance(e,ImportError):return 'Le module Python botocore est requis pour contrôler S3.'
 if isinstance(e,(TimeoutError,subprocess.TimeoutExpired)):return 'Délai de contrôle dépassé. Réessayez.'
 return 'Contrôle indisponible : vérifiez le réseau, le stockage et la configuration Litestream.'
def verification():
 v=load(statefile)
 if v.get('destinationBinding') and v.get('destinationBinding')!=binding:return {}
 if v.get('status') in ['queued','running']:
  try:
   if not v.get('pid'):
    if time.time()-datetime.datetime.fromisoformat(v['startedAt']).timestamp()>30:raise ProcessLookupError()
   else:os.kill(v['pid'],0)
  except (ProcessLookupError,PermissionError):v.update(status='interrupted',error='Le processus de vérification a été interrompu.')
 return v
if action=='worker':
 # Lock fd is inherited from the launcher: at most one disk-heavy restore on this server.
 lockfd=int(sys.argv[5]);started=time.monotonic();v=load(statefile);temporary=None;child=None
 signal.signal(signal.SIGTERM,lambda *args:(_ for _ in ()).throw(InterruptedError()))
 signal.signal(signal.SIGINT,lambda *args:(_ for _ in ()).throw(InterruptedError()))
 try:
  os.nice(19)
  if v.get('destinationBinding')!=binding:raise ValueError()
  v.update(status='running',pid=os.getpid(),startedAt=stamp());save(statefile,v)
  temporary=pathlib.Path(tempfile.mkdtemp(prefix='restore-',dir=private));target=temporary/'data.db'
  with open(temporary/'restore.log','w') as log:
   child=subprocess.Popen(['litestream','restore','-no-expand-env','-config',str(config),'-o',str(target),'-parallelism','2','-integrity-check','full','-json',str(source)],stdout=log,stderr=log)
   while child.poll() is None:
    if time.monotonic()-started>3600:raise TimeoutError()
    v.update(restoredBytes=max([x.stat().st_size for x in [target,temporary/'data.db.tmp'] if x.exists()]+[0]),elapsedSeconds=round(time.monotonic()-started),updatedAt=stamp());save(statefile,v);time.sleep(5)
  if child.returncode:raise RuntimeError()
  # The native restore has already performed the full integrity check. Never execute restored hooks.
  db=sqlite3.connect(target.as_uri()+'?mode=ro',uri=True)
  count=db.execute('SELECT count(*) FROM _collections').fetchone()[0];db.close()
  success={'finishedAt':stamp(),'restoredBytes':target.stat().st_size,'collections':count,'integrity':'ok','seconds':round(time.monotonic()-started)}
  v.update(status='passed',finishedAt=success['finishedAt'],restoredBytes=success['restoredBytes'],elapsedSeconds=success['seconds'],lastSuccess=success,error='')
 except Exception as e:v.update(status='failed',finishedAt=stamp(),error=failure(e))
 finally:
  if child and child.poll() is None:
   child.terminate()
   try:child.wait(timeout=10)
   except subprocess.TimeoutExpired:child.kill();child.wait()
  if temporary:shutil.rmtree(temporary,ignore_errors=True)
  v['history']=([ {k:v[k] for k in ['status','startedAt','finishedAt','restoredBytes','elapsedSeconds','error'] if k in v}]+v.get('history',[]))[:10]
  save(statefile,v);os.close(lockfd)
 sys.exit()
if action=='verify':
 if not p.get('enabled') or not source.is_file() or not config.is_file():
  print(json.dumps({'error':'Activez Litestream et configurez une destination avant ce contrôle.'}));sys.exit()
 lock=open(safe(private/'restore.lock'),'a+')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:
  print(json.dumps({'error':'Une vérification Litestream est déjà en cours sur ce serveur.'}));sys.exit()
 size=source.stat().st_size
 if shutil.disk_usage(private).free<size*2.5+1073741824:
  print(json.dumps({'error':'Espace temporaire insuffisant : prévoyez 2,5 fois la taille de la base plus 1 Gio.'}));sys.exit()
 v=verification();v={k:v[k] for k in ['lastSuccess','history'] if k in v};v.update(status='queued',startedAt=stamp(),restoredBytes=0,sourceBytes=size,destinationBinding=binding)
 save(statefile,v)
 child=subprocess.Popen([sys.executable,__file__,str(root),str(lsroot),ident,'worker',str(lock.fileno())],pass_fds=(lock.fileno(),),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
 # Worker writes its PID itself; no parent write can race its progress.
 print(json.dumps({'started':True}));sys.exit()
result={'checkedAt':stamp(),'enabled':bool(p.get('enabled')),'policy':{k:p.get(k) for k in ['status','s3Bucket','replicaPath','syncInterval','monitorInterval','checkpointInterval','snapshotInterval','snapshotRetention','validationInterval']},'sourceBytes':source.stat().st_size if source.exists() else 0,'service':{'state':'unavailable'},'local':{},'metrics':{},'remote':{},'verification':verification()}
try:
 processes=json.loads(command(['pm2','jlist']))
 for proc in processes:
  if proc.get('name')=='pockethost-litestream':
   env=proc.get('pm2_env',{});monit=proc.get('monit',{})
   result['service']={'state':env.get('status','unknown'),'restarts':env.get('restart_time',0),'since':env.get('pm_uptime'),'cpu':monit.get('cpu'),'memoryBytes':monit.get('memory')}
except Exception:pass
if not result['enabled']:
 print(json.dumps(result));sys.exit()
try:
 rows=json.loads(command(['litestream','status','-no-expand-env','-config',str(config),'-json',str(source)]))
 if isinstance(rows,list) and rows:result['local']={k:rows[0].get(k) for k in ['status','local_txid','wal_size']}
except Exception:result['local']={'status':'unavailable'}
try:
 port=int(os.environ.get('LITESTREAM_METRICS_PORT','9199'))
 if not 1024<=port<=65535:raise ValueError()
 text=urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/metrics',timeout=2).read(4000000).decode()
 allow=['litestream_db_size','litestream_wal_size','litestream_total_wal_bytes','litestream_txid','litestream_sync_count','litestream_sync_error_count','litestream_sync_seconds','litestream_disk_full','litestream_compaction_verify_error_count']
 for line in text.splitlines():
  match=re.fullmatch(r'([a-z_]+)\{(.*)\}\s+([0-9.eE+\-]+)',line)
  if not match or match[1] not in allow:continue
  labels={m[1]:json.loads('"'+m[2]+'"') for m in re.finditer(r'(\w+)="((?:[^"\\]|\\.)*)"',match[2])}
  if labels.get('db')==str(source):result['metrics'][match[1].replace('litestream_','',1)]=float(match[3])
except Exception:pass
cached=load(remotefile)
if cached.get('destinationBinding')!=binding:cached={}
if time.time()-cached.get('cachedAt',0)>30:
 lock=open(safe(private/(ident+'-remote.lock')),'a+')
 try:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  # Recheck after locking: concurrent readers share one bounded S3 scan.
  cached=load(remotefile)
  if cached.get('destinationBinding')!=binding:cached={}
  if time.time()-cached.get('cachedAt',0)>30:
   remote={'checkedAt':stamp(),'ok':False,'objects':0,'bytes':0,'partial':False,'maxTxid':None,'newestObjectAt':None,'uploads':[],'multipartAvailable':False}
   signal.signal(signal.SIGALRM,lambda *args:(_ for _ in ()).throw(TimeoutError()));signal.alarm(20)
   try:
    import botocore.session,botocore.config
    client=botocore.session.get_session().create_client('s3',region_name=p.get('s3Region') or 'auto',endpoint_url=p.get('s3Endpoint') or None,aws_access_key_id=p.get('s3AccessKeyId'),aws_secret_access_key=p.get('s3SecretAccessKey'),config=botocore.config.Config(s3={'addressing_style':'path' if p.get('s3ForcePathStyle') else 'auto'},connect_timeout=4,read_timeout=6,retries={'max_attempts':0},request_checksum_calculation='when_required',response_checksum_validation='when_required'))
    prefix=(p.get('replicaPath') or '').rstrip('/')+'/'
    if prefix=='/':raise ValueError()
    token=None
    for page_no in range(3):
     args={'Bucket':p['s3Bucket'],'Prefix':prefix,'MaxKeys':1000}
     if token:args['ContinuationToken']=token
     page=client.list_objects_v2(**args)
     for obj in page.get('Contents',[]):
      remote['objects']+=1;remote['bytes']+=obj['Size'];date=obj['LastModified'].isoformat()
      if not remote['newestObjectAt'] or date>remote['newestObjectAt']:remote['newestObjectAt']=date
      m=re.search(r'/([0-9a-f]{16})-([0-9a-f]{16})\.ltx$',obj['Key'])
      if m and (not remote['maxTxid'] or m[2]>remote['maxTxid']):remote['maxTxid']=m[2]
     token=page.get('NextContinuationToken')
     if not token:break
    remote['partial']=bool(token);remote['ok']=True
    # Optional multipart permissions must not turn a successful object listing into a false failure.
    try:
     page=client.list_multipart_uploads(Bucket=p['s3Bucket'],Prefix=prefix,MaxUploads=10)
     remote['multipartAvailable']=True;remote['multipartPartial']=bool(page.get('IsTruncated'))
     for upload in page.get('Uploads',[])[:10]:
      partpage=client.list_parts(Bucket=p['s3Bucket'],Key=upload['Key'],UploadId=upload['UploadId'],MaxParts=1000)
      name=upload['Key'].split('/')[-1];localfile=folder/'.data.db-litestream'/'ltx'/'0'/name
      total=localfile.stat().st_size if localfile.is_file() and localfile.resolve()==localfile else None
      sent=sum(x['Size'] for x in partpage.get('Parts',[]))
      remote['uploads'].append({'bytes':sent,'totalBytes':total,'partial':bool(partpage.get('IsTruncated')),'startedAt':upload['Initiated'].isoformat()})
    except Exception:remote['multipartAvailable']=False
   except Exception as e:remote.update(ok=False,error=failure(e))
   finally:signal.alarm(0)
   history=cached.get('history',[])
   sample={k:remote.get(k) for k in ['checkedAt','ok','objects','bytes','maxTxid','partial']}
   if not history or history[-1].get('checkedAt')!=sample['checkedAt']:history=(history+[sample])[-60:]
   cached={'cachedAt':time.time(),'value':remote,'history':history,'destinationBinding':binding};save(remotefile,cached)
 except BlockingIOError:pass
 finally:lock.close()
result['remote']=cached.get('value',{'ok':False,'error':'Premier contrôle S3 en cours.'});result['history']=cached.get('history',[])
# Positions are compared only after a complete listing. Idle databases do not acquire fake time lag.
local=result['local'].get('local_txid');remote=result['remote']
result['positionState']='unknown'
if remote.get('ok') and not remote.get('partial') and re.fullmatch('[0-9a-f]{16}',local or '') and remote.get('maxTxid'):
 result['positionState']='caught-up' if remote['maxTxid']>=local else 'pending'
print(json.dumps(result))
`
