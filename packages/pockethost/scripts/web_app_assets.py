"""Bounded, cancellable export of untrusted Next.js static assets."""
import io,os,pathlib,select,shutil,tarfile,time

MAX_BYTES=256*1024**2
MAX_ENTRIES=20000
MAX_SECONDS=120
MIN_FREE=15*1024**3
MAX_METADATA=16*1024

class AssetReader:
 def __init__(self,source,root,heartbeat):
  self.source=source;self.root=root;self.heartbeat=heartbeat;self.started=time.monotonic();self.bytes=0;self.headers=0;self.depth=0;self.last_space=0;self.metadata=0
  try:self.fd=source.fileno()
  except (AttributeError,io.UnsupportedOperation):self.fd=None
 def check(self,space=False):
  if time.monotonic()-self.started>MAX_SECONDS:raise RuntimeError('Export statique interrompu : délai maximal dépassé.')
  self.heartbeat()
  if space or self.bytes-self.last_space>=1024**2:
   if shutil.disk_usage(self.root).free<MIN_FREE:raise RuntimeError('Export statique interrompu : moins de 15 Gio d’espace libre.')
   self.last_space=self.bytes
 def read(self,size):
  self.check()
  # Bound raw bytes too: extended headers and padding never escape the file budget.
  size=min(size,64*1024,MAX_BYTES+32*1024**2-self.bytes+1)
  if self.fd is None:data=self.source.read(size)
  else:
   while not select.select([self.fd],[],[],.25)[0]:self.check()
   data=os.read(self.fd,size)
  self.bytes+=len(data)
  if self.bytes>MAX_BYTES+32*1024**2:raise RuntimeError('Archive statique trop volumineuse.')
  return data

class AssetInfo(tarfile.TarInfo):
 def _proc_member(self,archive):
  reader=archive.asset_reader;reader.headers+=1;reader.depth+=1
  try:
   if reader.headers>MAX_ENTRIES:raise RuntimeError('Export statique limité à 20 000 entrées.')
   if reader.depth>8:raise RuntimeError('En-têtes statiques imbriqués refusés.')
   if self.size<0 or self.size>MAX_BYTES:raise RuntimeError('Export statique limité à 256 Mio.')
   if self.type in (tarfile.XHDTYPE,tarfile.XGLTYPE,tarfile.SOLARIS_XHDTYPE,tarfile.GNUTYPE_LONGNAME):
    reader.metadata+=self.size
    if self.size>MAX_METADATA or reader.metadata>1024**2:raise RuntimeError('Métadonnées statiques trop volumineuses.')
   elif self.type not in (tarfile.REGTYPE,tarfile.AREGTYPE,tarfile.DIRTYPE):raise RuntimeError('Fichier statique non régulier refusé.')
   return super()._proc_member(archive)
  finally:reader.depth-=1

def extract_assets(reader,folder):
 # TarFile reads its first member during construction. Pass the reader through a
 # local subclass so header bounds also apply before the first yielded member.
 class Archive(tarfile.TarFile):asset_reader=reader
 reader.check(space=True);total=0
 with Archive.open(fileobj=reader,mode='r|',tarinfo=AssetInfo)as archive:
  for member in archive:
   reader.check(space=True)
   if member.sparse is not None or any(k.startswith('GNU.sparse')for k in member.pax_headers):raise RuntimeError('Fichier statique sparse refusé.')
   if not member.isfile()and not member.isdir():raise RuntimeError('Fichier statique non régulier refusé.')
   total+=member.size
   if member.size<0 or total>MAX_BYTES:raise RuntimeError('Export statique limité à 256 Mio.')
   name=pathlib.PurePosixPath(member.name)
   if name.is_absolute()or '..'in name.parts or len(member.name)>4096 or len(name.parts)>32:raise RuntimeError('Chemin statique invalide.')
   if name.parts and name.parts[0]=='static':name=pathlib.PurePosixPath(*name.parts[1:])
   target=folder/str(name)
   if member.isdir():target.mkdir(parents=True,exist_ok=True,mode=0o750);continue
   if not name.parts:raise RuntimeError('Chemin statique invalide.')
   target.parent.mkdir(parents=True,exist_ok=True,mode=0o750)
   # Duplicate names are refused; no links or existing files can be overwritten.
   with archive.extractfile(member)as source,open(target,'xb')as out:
    remaining=member.size
    while remaining:
     reader.check();chunk=source.read(min(64*1024,remaining))
     if not chunk:raise RuntimeError('Export statique tronqué.')
     out.write(chunk);remaining-=len(chunk)
   target.chmod(0o640)
 # Drain the bounded trailing padding; a successful tar terminator alone does
 # not imply docker cp completed successfully.
 while reader.read(64*1024):pass
 reader.check(space=True)
