/** Restores only into a private temporary directory. No PocketBase process or hook is started. */
export const BACKUP_VERIFIER = String.raw`
import os,sys,json,pathlib,tempfile,tarfile,zipfile,sqlite3,hashlib,shutil,signal,time,threading,gzip
archive,expected,progress,workspace=sys.argv[1:5]
os.umask(0o077)
try: os.nice(19)
except OSError: pass
signal.signal(signal.SIGALRM,lambda *args: (_ for _ in ()).throw(TimeoutError()))
signal.alarm(7200)
parent=os.getppid()
def watch_parent():
 while True:
  time.sleep(3)
  if os.getppid()!=parent:os.kill(os.getpid(),signal.SIGALRM);return
threading.Thread(target=watch_parent,daemon=True).start()
state={'phase':'checksum','files':0,'restoredBytes':0,'checksumVerified':False,'databaseIntegrity':False,'percent':0}
last=0
def report(force=False):
 global last
 if not force and time.monotonic()-last<2:return
 last=time.monotonic();p=pathlib.Path(progress);tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(state));tmp.replace(p)
class VerificationError(Exception):pass
def fail(message):raise VerificationError(message)
def restore(name,size,source,folder,seen,limit):
 name=name.removeprefix('./');part=pathlib.PurePosixPath(name)
 if not name or part.is_absolute() or '..' in part.parts or '\\' in name:fail('Chemin interdit dans l’archive.')
 if part.parts[0] not in ('pb_data','pb_hooks','pb_migrations','pb_public','manifest.json'):fail('Contenu d’archive non reconnu.')
 if str(part) in seen:fail('Entrée dupliquée dans l’archive.')
 seen.add(str(part))
 if len(seen)>1000000 or size<0 or state['restoredBytes']+size>limit:fail('Espace temporaire insuffisant ou archive trop volumineuse.')
 target=folder.joinpath(*part.parts);target.parent.mkdir(parents=True,exist_ok=True)
 with target.open('xb') as out:
  remaining=size
  while remaining:
   chunk=source.read(min(1024*1024,remaining))
   if not chunk:fail('Archive tronquée.')
   out.write(chunk);remaining-=len(chunk);state['restoredBytes']+=len(chunk);report()
  if source.read(1):fail('Taille d’entrée incohérente.')
 state['files']+=1;report()
try:
 base=pathlib.Path(workspace);base.mkdir(parents=True,exist_ok=True)
 if sys.platform.startswith('linux'):
  import fcntl
  worker_lock=(base/'worker.lock').open('a')
  try:fcntl.flock(worker_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:fail('Un autre contrôle est déjà actif sur le serveur.')
 path=pathlib.Path(archive)
 if path.is_symlink() or not path.is_file():fail('Archive absente ou non régulière.')
 digest=hashlib.sha256();total=path.stat().st_size;read=0;report(True)
 with path.open('rb') as f:
  while True:
   chunk=f.read(8*1024*1024)
   if not chunk:break
   digest.update(chunk);read+=len(chunk);state['percent']=int(read/max(1,total)*30);report()
 if expected and digest.hexdigest()!=expected:fail('La somme SHA-256 ne correspond pas à la sauvegarde.')
 state['checksumVerified']=bool(expected);state['phase']='restore';state['percent']=30;report(True)
 base=pathlib.Path(workspace);base.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='restore-check-',dir=base) as temporary:
  folder=pathlib.Path(temporary);seen=set();limit=int(shutil.disk_usage(folder).free*0.8)
  if zipfile.is_zipfile(path):
   with zipfile.ZipFile(path) as z:
    for member in z.infolist():
     mode=member.external_attr>>16
     if mode & 0o170000 == 0o120000:fail('Lien interdit dans l’archive.')
     if member.is_dir():continue
     with z.open(member) as source:restore(member.filename,member.file_size,source,folder,seen,limit)
  else:
   with gzip.open(path,'rb') as compressed, tarfile.open(fileobj=compressed,mode='r|') as t:
    for member in t:
     if member.isdir():
      name=pathlib.PurePosixPath(member.name)
      if name.is_absolute() or '..' in name.parts:fail('Chemin interdit dans l’archive.')
      continue
     if not member.isfile():fail('Lien ou fichier spécial interdit dans l’archive.')
     with t.extractfile(member) as source:restore(member.name,member.size,source,folder,seen,limit)
    while compressed.read(8*1024*1024):pass
  state['phase']='sqlite';state['percent']=90;report(True)
  db=folder/'pb_data/data.db'
  if not db.is_file():fail('La sauvegarde ne contient pas pb_data/data.db.')
  connection=sqlite3.connect(db,timeout=10)
  connection.execute('PRAGMA trusted_schema=OFF')
  result=connection.execute('PRAGMA integrity_check').fetchall()
  if result!=[('ok',)]:fail('Le contrôle d’intégrité SQLite a échoué.')
  if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='_collections'").fetchone():fail('Métadonnées PocketBase absentes.')
  connection.close();state['databaseIntegrity']=True
 state.update(phase='complete',percent=100,ok=True,message='Archive restaurée et intégrité SQLite vérifiée. Les hooks et tâches n’ont pas été exécutés.')
except Exception as error:
 state.update(phase='failed',ok=False,message=str(error) if isinstance(error,VerificationError) else ('Délai maximal du contrôle dépassé.' if isinstance(error,TimeoutError) else 'Lecture, restauration temporaire ou contrôle SQLite impossible.'))
finally:
 report(True);print(json.dumps(state))
`
