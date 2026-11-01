"""Bounded, redacted runtime logs for exact published web containers, independent of builds."""
import datetime,hashlib,json,os,pathlib,re,secrets,struct,threading,time,urllib.request,urllib.parse
from web_app_tasks import DockerTasks,_UnixHTTP
MAX_BYTES=2097152;MAX_RECORDS=10000;MAX_MESSAGE=12000
ID=re.compile(r'^[a-z0-9]{15}$');ANSI=re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))');STAMP=re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z) (.*)$',re.S)
def date(value):return datetime.datetime.fromisoformat(re.sub(r'(\.\d{6})\d+',r'\1',value).replace('Z','+00:00'))
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def level(message,stream):
 try:
  value=json.loads(message);found=str(value.get('level','')).lower()if isinstance(value,dict)else'';found={'10':'debug','20':'debug','30':'info','40':'warn','50':'error','60':'error','warning':'warn','fatal':'error'}.get(found,found)
  if found in ['debug','info','warn','error']:return found
 except(ValueError,TypeError):pass
 hit=re.search(r'(?i)\b(error|exception|fatal|warning|warn|debug|info)\b',message)
 return {'warning':'warn','fatal':'error','exception':'error'}.get(hit[1].lower(),hit[1].lower())if hit else'error'if stream=='stderr'else'unknown'
def mask_records(rows,values):
 # Match across Docker frames/lines without placing timestamps between secret bytes.
 values=sorted({part for value in values if value for part in [value,*value.splitlines()]if part},key=len,reverse=True)
 text=''.join(row['message']for row in rows);ranges=[]
 for value in values:
  start=0
  while True:
   index=text.find(value,start)
   if index<0:break
   ranges.append((index,index+len(value)));start=index+max(1,len(value))
  # A value split at the end of a fetch must not expose its prefix while waiting.
  for size in range(min(len(value)-1,len(text)),0,-1):
   if text.endswith(value[:size]):ranges.append((len(text)-size,len(text)));break
 offset=0;result=[]
 for row in rows:
  message=row['message'];end=offset+len(message);parts=[];cursor=0
  for low,high in sorted(ranges):
   left=max(low,offset)-offset;right=min(high,end)-offset
   if left<right and right>cursor:
    parts.extend([message[cursor:max(cursor,left)],'[masqué]']);cursor=right
  parts.append(message[cursor:]);result.append({**row,'message':''.join(parts)[:MAX_MESSAGE]});offset=end
 return result
class RuntimeLogs:
 def __init__(self,control,secret,docker_socket,root):
  if not re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{3,4}/?',control)or len(secret)<32:raise ValueError('Contrôleur privé requis.')
  self.control=control.rstrip('/');self.secret=secret;self.root=pathlib.Path(root).absolute();self.engine=DockerTasks(docker_socket);self.stop=threading.Event();self.context={}
 def api(self,page):
  class NoRedirect(urllib.request.HTTPRedirectHandler):
   def redirect_request(self,*args,**kwargs):return None
  request=urllib.request.Request(self.control+'/api/web-apps/_observability/config',headers={'Content-Type':'application/json','X-Web-Worker':self.secret},data=json.dumps({'page':page}).encode())
  with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect).open(request,timeout=8)as response:
   raw=response.read(8*1024**2+1)
   if len(raw)>8*1024**2:raise ValueError('Configuration de journaux trop volumineuse.')
   return json.loads(raw)
 def inspect(self,job):
  if not all(ID.fullmatch(str(job.get(key,'')))for key in ['appId','deploymentId'])or job.get('containerName')!='ph-web-'+job['appId']+'-'+job['deploymentId']or not isinstance(job.get('secrets'),list)or any(not isinstance(value,str)for value in job['secrets']):raise ValueError('Publication invalide.')
  info=self.engine.request('GET','/containers/'+job['containerName']+'/json');config=info.get('Config',{});host=info.get('HostConfig',{});labels=config.get('Labels',{})or{}
  if not re.fullmatch('[a-f0-9]{64}',info.get('Id',''))or info.get('Name','').lstrip('/')!=job['containerName']or config.get('User')!='10001:10001'or labels.get('pockethost.web')!='true'or labels.get('pockethost.web.app')!=job['appId']or labels.get('pockethost.web.deployment')!=job['deploymentId']or host.get('Privileged')or not host.get('ReadonlyRootfs')or host.get('Binds')or any(mount.get('Type')=='bind'or'docker.sock'in str(mount.get('Source',''))for mount in info.get('Mounts',[])):raise ValueError('Isolation du conteneur invalide.')
  return info
 def raw_logs(self,container,since):
  if type(since)is not int or since<0:raise ValueError('Curseur Docker attendu en secondes Unix.')
  connection=_UnixHTTP(self.engine.path,3)
  try:
   connection.request('GET','/v1.47/containers/'+container+'/logs?stdout=true&stderr=true&timestamps=true&tail=all&since='+urllib.parse.quote(str(since),safe=''));response=connection.getresponse()
   if response.status!=200:raise ValueError('Journaux Docker indisponibles.')
   raw=response.read(524288+1)
   if len(raw)>524288:raw=raw[:524288]
   rows=[];offset=0;payloads={1:b'',2:b''}
   while offset+8<=len(raw):
    stream=raw[offset];size=struct.unpack('>I',raw[offset+4:offset+8])[0]
    if stream not in [1,2]or raw[offset+1:offset+4]!=b'\0\0\0'or size>1048576:raise ValueError('Flux Docker invalide.')
    if offset+8+size>len(raw):break
    payloads[stream]+=raw[offset+8:offset+8+size];offset+=8+size
   counts={}
   for stream,payload in payloads.items():
    for line in payload.decode('utf-8',errors='replace').splitlines(keepends=True):
     match=STAMP.match(line)
     if not match:continue
     timestamp,message=match.groups();message=ANSI.sub('',message).replace('\0','');kind='stdout'if stream==1 else'stderr';base=timestamp+'\0'+kind+'\0'+message;ordinal=counts.get(base,0);counts[base]=ordinal+1;ident=hashlib.sha256((base+'\0'+str(ordinal)).encode()).hexdigest()[:24]
     rows.append({'id':ident,'timestamp':timestamp,'stream':kind,'level':level(message,kind),'message':message})
   return rows
  finally:connection.close()
 def collect(self,job):
  if (self.root/'preview-deletions'/(job['appId']+'.json')).exists():return
  info=self.inspect(job);folder=self.root/'runtime-logs';folder.mkdir(mode=0o750,parents=True,exist_ok=True)
  if self.root.resolve()!=self.root or folder.resolve()!=folder:raise ValueError('Répertoire de journaux invalide.')
  path=folder/(job['appId']+'.json');value={}
  if path.exists():
   if path.is_symlink()or path.stat().st_size>3145728:raise ValueError('Journal invalide.')
   try:value=json.loads(path.read_text())
   except ValueError:value={}
  if value.get('deploymentId')!=job['deploymentId']:value={'version':1,'appId':job['appId'],'deploymentId':job['deploymentId'],'generation':secrets.token_hex(8),'records':[],'truncated':False}
  latest=value.get('records',[])[-1]['timestamp']if value.get('records')else''
  # Docker logs accepts Unix seconds, not its own RFC3339Nano output timestamps.
  # Re-read the preceding second; stable record IDs deduplicate the overlap.
  try:since=max(0,int(date(latest).timestamp())-1)if latest else max(0,int(time.time())-300)
  except(ValueError,TypeError,OverflowError):since=max(0,int(time.time())-300)
  raw=self.raw_logs(info['Id'],since);context=self.context.get(job['appId'],[])
  if context and context[0].get('deploymentId')!=job['deploymentId']:context=[]
  merged={row['id']:row for row in [*context,*raw]};new=sorted(merged.values(),key=lambda row:(row['timestamp'],row['id']))[-1000:];context=[{**row,'deploymentId':job['deploymentId']}for row in new[-100:]]
  while len(context)>1 and sum(len(row['message'])for row in context)>262144:context.pop(0)
  self.context[job['appId']]=context
  sanitized=mask_records(new,job['secrets']);stored={row['id']:row for row in value.get('records',[])}
  for row in sanitized:stored[row['id']]={key:item for key,item in row.items()if key!='deploymentId'}
  cutoff=datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(hours=24);records=[]
  for row in sorted(stored.values(),key=lambda row:(row['timestamp'],row['id'])):
   try:
    if date(row['timestamp'])>=cutoff:records.append(row)
   except ValueError:continue
  size=0;kept=[]
  for row in reversed(records):
   count=len(json.dumps(row,ensure_ascii=False).encode())
   if size+count>MAX_BYTES or len(kept)>=MAX_RECORDS:value['truncated']=True;break
   kept.append(row);size+=count
  value.update(records=list(reversed(kept)),checkedAt=now(),state='running'if info.get('State',{}).get('Running')else'stopped');temporary=path.with_name('.'+path.name+'.'+secrets.token_hex(8));fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o640)
  try:
   with os.fdopen(fd,'w')as out:json.dump(value,out,ensure_ascii=False)
   if (self.root/'preview-deletions'/(job['appId']+'.json')).exists():return
   os.replace(temporary,path)
  finally:temporary.unlink(missing_ok=True)
 def loop(self):
  while not self.stop.is_set():
   try:
    self.engine.verify();page=1
    while not self.stop.is_set():
     result=self.api(page)
     for job in result.get('items',[]):
      try:self.collect(job)
      except Exception:continue
     if not result.get('hasMore'):break
     page+=1
   except Exception:pass
   self.stop.wait(5)
def start_runtime_log_collector(control,secret,docker_socket,root):
 worker=RuntimeLogs(control,secret,docker_socket,root);thread=threading.Thread(target=worker.loop,name='web-runtime-logs',daemon=True);thread.start();return thread
