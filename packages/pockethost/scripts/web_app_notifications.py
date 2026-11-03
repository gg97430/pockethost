"""Explicitly configured HTTPS/SMTP notification dispatch, bounded retries owned by PocketBase.

Worker contract (controller side, see notifications.ts):
 claim job  {id, proof, channel:'webhook'|'email', payload, trigger?:'auto'|'test', url?, secret?, format?:'generic'|'slack'|'discord'}
 report     {id, proof, success, httpStatus, errorCode, errorClass, durationMs, final}
   errorCode  coarse, stable: '' | ssrf | timeout | network | http_refused | smtp | invalid_format
   errorClass fine, for display: '' | url_refused | dns | dns_timeout | address_refused | connect | tls | timeout
              | redirect | rate_limited | http_4xx | http_5xx | format | payload | send | smtp
   final      true when retrying cannot help (test send, SSRF refusal, invalid format): no retry expected.
 claim resp {job, platformWebhook?:{url, secret?, format?, host?}|null}: platform channel webhook, memory only.
            After UNREACHABLE_AFTER s of failed claims, one 'controller_unreachable' alert goes to it (up to 3 attempts).
 Tests      the controller enqueues a delivery with trigger 'test' and polls it: the real worker path is what is tested.
 Signing    generic only (X-PocketHost-*); slack/discord carry text built from title, message, details.summary
            (already cleaned by the controller), dashboardUrl (https only); never other details.
"""
import datetime,hashlib,hmac,http.client,ipaddress,json,pathlib,queue,re,secrets,shutil,socket,ssl,threading,time,urllib.parse,urllib.request
BRAND='Kolab Hosting';FORMATS=('generic','slack','discord');DEADLINE=15;PERMANENT={'ssrf','invalid_format'};UNREACHABLE_AFTER=180;UNREACHABLE_ATTEMPTS=3
HTTPS_LINK=re.compile(r'https://[A-Za-z0-9.-]+\.[A-Za-z]{2,63}(:[0-9]{1,5})?(/[^\s]*)?');HOST=re.compile(r'[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+')
SLACK=re.compile(r'https://hooks\.slack\.com/services/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+')
DISCORD=re.compile(r'https://(?:discord\.com|discordapp\.com)/api/webhooks/[0-9]+/[A-Za-z0-9._-]+')
class NotificationError(RuntimeError):
 def __init__(self,code,kind=''):self.code=code;self.kind=kind or code;super().__init__('Notification unavailable')
def public_addresses(host,port=443):
 result=queue.Queue(maxsize=1)
 def resolve():
  try:result.put(socket.getaddrinfo(host,port,type=socket.SOCK_STREAM))
  except Exception:result.put(None)
 thread=threading.Thread(target=resolve,name='web-notification-dns',daemon=True);thread.start()
 try:values=result.get(timeout=3)
 except queue.Empty:raise NotificationError('timeout','dns_timeout')from None
 if not values:raise NotificationError('network','dns')
 checked=[]
 for family,kind,proto,_,address in values:
  try:ip=ipaddress.ip_address(address[0])
  except ValueError:raise NotificationError('ssrf','address_refused')from None
  if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback or getattr(ip,'ipv4_mapped',None)and not ip.ipv4_mapped.is_global or getattr(ip,'sixtofour',None)or getattr(ip,'teredo',None)or ip in ipaddress.ip_network('64:ff9b::/96'):raise NotificationError('ssrf','address_refused')
  if family not in [socket.AF_INET,socket.AF_INET6]:raise NotificationError('ssrf','address_refused')
  if (family,address)not in [(f,a)for f,_,_,a in checked]:checked.append((family,kind,proto,address))
 return checked[:4]
def public_url(value):
 """Shared URL policy: https, :443, FQDN only, no userinfo/fragment/control characters."""
 if not isinstance(value,str)or len(value)>2000 or any(char in value for char in '\r\n\0'):raise NotificationError('ssrf','url_refused')
 url=urllib.parse.urlsplit(value)
 try:port=url.port
 except ValueError:raise NotificationError('ssrf','url_refused')from None
 if url.scheme!='https'or not url.hostname or url.username or url.password or url.fragment or port not in [None,443]:raise NotificationError('ssrf','url_refused')
 try:ipaddress.ip_address(url.hostname.strip('[]'));raise NotificationError('ssrf','url_refused')
 except ValueError:pass
 return url
def text_summary(payload):
 """Chat text: brand · title · app (or platform host), message, event, date, app URL, summary, dashboard link."""
 clean=lambda value,size:' '.join(str(value).split())[:size]
 application=payload.get('application')if isinstance(payload.get('application'),dict)else None
 details=payload.get('details')if isinstance(payload.get('details'),dict)else{}
 platform=payload.get('platform')if isinstance(payload.get('platform'),dict)else{}
 host=str(platform.get('host')or'');host=host if len(host)<=253 and HOST.fullmatch(host)else''
 name=clean(application.get('name')or'Site web',200)if application is not None else'Plateforme'+(' '+host if host else'')
 title=clean(payload['title'],120)if isinstance(payload.get('title'),str)and payload['title'].strip()else''
 lines=[BRAND+' · '+(title+' · 'if title else'')+name,clean(payload.get('message')or'Notification du site.',500),'Événement : '+clean(payload.get('event')or'notification',64)+(' (test)'if details.get('drill')is True else'')]
 if payload.get('occurredAt'):lines.append('Date : '+clean(payload['occurredAt'],64))
 link=lambda value:str(value)[:500]if isinstance(value,str)and HTTPS_LINK.fullmatch(value[:500])else''
 if application is not None and link(application.get('url')):lines.append('Application : '+link(application['url']))
 if isinstance(details.get('summary'),str)and details['summary'].strip():lines.append('Détail : '+clean(details['summary'],300))
 if application is not None and link(application.get('dashboardUrl')):lines.append('Ouvrir dans le dashboard : '+link(application['dashboardUrl']))
 elif isinstance(payload.get('dashboardUrl'),str)and link(payload['dashboardUrl']):lines.append('Ouvrir dans le dashboard : '+link(payload['dashboardUrl']))
 return'\n'.join(lines)
def webhook_body(job):
 """generic: signed JSON payload; slack: {text}; discord: {content} without mentions."""
 fmt=job.get('format')or'generic';payload=job.get('payload')
 if fmt not in FORMATS:raise NotificationError('invalid_format','format')
 if not isinstance(payload,dict)or not isinstance(payload.get('event'),str):raise NotificationError('network','payload')
 if fmt=='slack':
  if not SLACK.fullmatch(job['url']):raise NotificationError('invalid_format','format')
  lines=text_summary(payload).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').split('\n',1);value={'text':'*'+lines[0]+'*'+('\n'+lines[1]if len(lines)>1 else'')}
 elif fmt=='discord':
  if not DISCORD.fullmatch(job['url']):raise NotificationError('invalid_format','format')
  lines=text_summary(payload).split('\n',1);value={'content':('**'+lines[0]+'**'+('\n'+lines[1]if len(lines)>1 else''))[:2000],'allowed_mentions':{'parse':[]}}
 else:value=payload
 return fmt,json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
def classify_status(status):
 if 200<=status<300:return'',''
 return'http_refused','redirect'if 300<=status<400 else'rate_limited'if status==429 else'http_5xx'if status>=500 else'http_4xx'
def send_webhook(job,connector=None):
 started=time.monotonic()
 try:
  url=public_url(job.get('url'))
  if not re.fullmatch('[a-z0-9]{15}',str(job.get('id',''))):raise NotificationError('network','payload')
  fmt,payload=webhook_body(job)
  if len(payload)>12000:raise NotificationError('network','payload')
  if fmt=='generic'and(not isinstance(job.get('secret'),str)or not 16<=len(job['secret'])<=256):raise NotificationError('network','payload')
  addresses=public_addresses(url.hostname);headers={'Content-Type':'application/json','User-Agent':'KolabHosting-Notifications/1'}
  if fmt=='generic':
   timestamp=str(int(time.time()));signature=hmac.new(job['secret'].encode(),timestamp.encode()+b'.'+payload,hashlib.sha256).hexdigest()
   headers.update({'X-PocketHost-Delivery':job['id'],'X-PocketHost-Event':job['payload']['event'][:64],'X-PocketHost-Timestamp':timestamp,'X-PocketHost-Signature':'sha256='+signature})
  connection=http.client.HTTPSConnection(url.hostname,443,timeout=8,context=ssl.create_default_context())
  def connect():
   # Pinned to vetted addresses only (no rebinding); the next one is tried only when TCP connect fails.
   for index,(family,kind,proto,address)in enumerate(addresses):
    remaining=DEADLINE-(time.monotonic()-started)
    if remaining<=1:raise NotificationError('timeout','timeout')
    raw=socket.socket(family,kind,proto);raw.settimeout(min(8,remaining))
    try:raw.connect(address)
    except(socket.timeout,TimeoutError,OSError)as error:
     raw.close()
     if index==len(addresses)-1:raise NotificationError('timeout'if isinstance(error,(socket.timeout,TimeoutError))else'network','timeout'if isinstance(error,(socket.timeout,TimeoutError))else'connect')from None
     continue
    try:connection.sock=ssl.create_default_context().wrap_socket(raw,server_hostname=url.hostname);return
    except Exception:raw.close();raise
  connection.connect=connect
  if connector:connection=connector(url.hostname,addresses)
  try:
   target=urllib.parse.quote(url.path or'/',safe='/%:@!$&\'()*+,;=-._~')+('?' + url.query if url.query else'')
   connection.request('POST',target,body=payload,headers=headers)
   response=connection.getresponse();response.read(4096);code,kind=classify_status(response.status)
   return {'success':not code,'httpStatus':response.status,'errorCode':code,'errorClass':kind,'durationMs':round((time.monotonic()-started)*1000)}
  finally:connection.close()
 except NotificationError:raise
 except(socket.timeout,TimeoutError):raise NotificationError('timeout','timeout')from None
 except ssl.SSLError:raise NotificationError('network','tls')from None
 except OSError:raise NotificationError('network','connect')from None
 except Exception:raise NotificationError('network','send')from None
def deliver(job,connector=None):
 """Never raises and never returns exception text: only stable codes leave this function."""
 started=time.monotonic()
 try:result=send_webhook(job,connector)
 except NotificationError as error:result={'success':False,'httpStatus':0,'errorCode':error.code,'errorClass':error.kind}
 except Exception:result={'success':False,'httpStatus':0,'errorCode':'network','errorClass':'send'}
 result.setdefault('durationMs',round((time.monotonic()-started)*1000));result['durationMs']=max(0,min(60000,result['durationMs']))
 result['final']=job.get('trigger')=='test'or result['errorCode']in PERMANENT;return result
class Notifications:
 def __init__(self,control,secret,root):
  if not re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{3,4}/?',control)or len(secret)<32:raise ValueError('Contrôleur privé requis.')
  self.control=control.rstrip('/');self.secret=secret;self.root=pathlib.Path(root).absolute();self.stop=threading.Event()
  self.platform=None;self.down_since=None;self.unreachable_attempts=0;self.next_unreachable=0
 def remember_platform(self,value):
  """Platform webhook from the claim, kept in memory only: it is the one path left when the controller is down."""
  if not isinstance(value,dict)or'platformWebhook'not in value:return
  hook=value['platformWebhook']
  ok=isinstance(hook,dict)and isinstance(hook.get('url'),str)and(hook.get('format')or'generic')in FORMATS and isinstance(hook.get('secret',''),(str,type(None)))
  self.platform={'url':hook['url'],'secret':hook.get('secret')or'','format':hook.get('format')or'generic','host':str(hook.get('host')or'')[:253]}if ok else None
 def reachable(self,value):
  self.down_since=None;self.unreachable_attempts=0;self.next_unreachable=0;self.remember_platform(value)
 def unreachable(self,now=None):
  """Called after each failed claim; sends controller_unreachable once the outage lasts UNREACHABLE_AFTER s."""
  now=time.monotonic()if now is None else now
  if self.down_since is None:self.down_since=now
  if not self.platform or now-self.down_since<UNREACHABLE_AFTER or now<self.next_unreachable or self.unreachable_attempts>=UNREACHABLE_ATTEMPTS:return None
  self.unreachable_attempts+=1;self.next_unreachable=now+60
  payload={'version':2,'event':'controller_unreachable','occurredAt':datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z'),'title':'Contrôleur injoignable','message':'Le worker ne parvient plus à joindre le contrôleur de la plateforme : déploiements, sauvegardes, sondes et alertes par email sont à l’arrêt.','application':None,'platform':{'host':self.platform['host']},'details':{'unreachableSeconds':int(now-self.down_since)}}
  result=deliver({'id':''.join(secrets.choice('abcdefghijklmnopqrstuvwxyz0123456789')for _ in range(15)),'url':self.platform['url'],'secret':self.platform['secret'],'format':self.platform['format'],'payload':payload})
  if result['success']or result['final']:self.unreachable_attempts=UNREACHABLE_ATTEMPTS
  return result
 def api(self,path,body):
  from platform_maintenance import claim_response
  paused=claim_response(path,'notifications')
  if paused is not None:return paused
  class NoRedirect(urllib.request.HTTPRedirectHandler):
   def redirect_request(self,*args,**kwargs):return None
  request=urllib.request.Request(self.control+'/api/web-apps/_notifications/'+path,headers={'Content-Type':'application/json','X-Web-Worker':self.secret},data=json.dumps(body).encode())
  with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect).open(request,timeout=15)as response:return json.loads(response.read(1000000))
 def dispatch(self,job):
  if not isinstance(job,dict)or not re.fullmatch('[a-z0-9]{15}',str(job.get('id','')))or not re.fullmatch('[A-Za-z0-9]{80}',str(job.get('proof','')))or job.get('channel')not in ['webhook','email']:raise ValueError('Mission invalide.')
  if job['channel']=='email':
   started=time.monotonic();result={'success':False,'httpStatus':0,'errorCode':'smtp','errorClass':'smtp'}
   try:
    if self.api('email',{'id':job['id'],'proof':job['proof']}).get('sent'):result.update(success=True,errorCode='',errorClass='')
   except Exception:pass
   result.update(durationMs=min(60000,round((time.monotonic()-started)*1000)),final=job.get('trigger')=='test')
  else:result=deliver(job)
  self.api('report',{'id':job['id'],'proof':job['proof'],**result})
 def loop(self):
  while not self.stop.is_set():
   try:
    value=self.api('claim',{'diskFreeBytes':shutil.disk_usage(self.root).free})
    if value.get('maintenance',{}).get('paused'):self.stop.wait(1);continue
    self.reachable(value)
   except Exception:
    value=None
    try:self.unreachable()
    except Exception:pass
   try:
    if isinstance(value,dict)and value.get('job'):self.dispatch(value['job'])
   except Exception:pass
   self.stop.wait(3)
def start_notification_dispatcher(control,secret,root):
 worker=Notifications(control,secret,root);thread=threading.Thread(target=worker.loop,name='web-notifications',daemon=True);thread.start();return thread
