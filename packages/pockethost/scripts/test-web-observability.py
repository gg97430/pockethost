#!/usr/bin/env python3
"""No network writes: runtime multiplex/UTF8/masks and pinned HTTPS notification transport (local TLS only)."""
import datetime,http.server,shutil,ssl,threading,hashlib,hmac,importlib.util,io,json,pathlib,re,secrets,socket,struct,subprocess,sys,tempfile,unittest,unittest.mock as mock,urllib.parse
sys.path.insert(0,str(pathlib.Path(__file__).parent));import web_app_observability as logs;import web_app_notifications as notify
ROOT=pathlib.Path(__file__).resolve().parents[3]
READER=re.search(r'RUNTIME_LOG_READER = String.raw`(.*?)`', (ROOT/'packages/pockethost/src/mothership-app/src/lib/handlers/webApps/observability.ts').read_text(),re.S).group(1)
class LogTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name).resolve();self.worker=logs.RuntimeLogs('http://127.0.0.1:8091','s'*40,'/run/user/1001/docker.sock',self.root);self.app='a'*15;self.dep='b'*15;self.job={'appId':self.app,'deploymentId':self.dep,'containerName':'ph-web-'+self.app+'-'+self.dep,'secrets':['private-token','multiline\nsecret']};self.info={'Id':'f'*64,'Name':'/'+self.job['containerName'],'State':{'Running':True},'Config':{'User':'10001:10001','Labels':{'pockethost.web':'true','pockethost.web.app':self.app,'pockethost.web.deployment':self.dep}},'HostConfig':{'ReadonlyRootfs':True},'Mounts':[]};self.worker.engine.request=lambda *args:self.info
 def tearDown(self):self.tmp.cleanup()
 def test_exact_publication_nonroot_no_bind_and_stopped_logs_allowed(self):
  self.assertEqual(self.worker.inspect(self.job)['Id'],'f'*64);self.info['State']['Running']=False;self.assertEqual(self.worker.inspect(self.job)['Id'],'f'*64)
  for change in [{'User':'0:0'},{'Labels':{'pockethost.web.app':'x'*15}}]:
   with mock.patch.dict(self.info['Config'],change):
    with self.assertRaises(ValueError):self.worker.inspect(self.job)
  self.info['Mounts']=[{'Type':'bind','Source':'/var/run/docker.sock'}]
  with self.assertRaises(ValueError):self.worker.inspect(self.job)
 def test_masks_values_split_between_records_multiline_and_pending_prefix(self):
  rows=[{'message':'hello private-'},{'message':'token and multiline\n'},{'message':'secret tail private-'}];value=logs.mask_records(rows,self.job['secrets']);text=''.join(row['message']for row in value);self.assertNotIn('private-',text);self.assertNotIn('token',text);self.assertNotIn('multiline',text);self.assertNotIn('secret',text);self.assertIn('[masqué]',text)
 def test_native_docker_frames_split_utf8_timestamp_and_level(self):
  stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+'.123456789Z ';payload=(stamp+'{"level":50,"message":"échec"}\n').encode();chunks=[payload[:17],payload[17:-4],payload[-4:]];raw=b''.join(bytes([2,0,0,0])+struct.pack('>I',len(chunk))+chunk for chunk in chunks)
  class Response(io.BytesIO):status=200
  class Connection:
   def __init__(self,*args):self.path=''
   def request(self,method,path):self.path=path
   def getresponse(self):return Response(raw)
   def close(self):pass
  with mock.patch.object(logs,'_UnixHTTP',Connection):rows=self.worker.raw_logs('f'*64,int(logs.date(stamp.strip()).timestamp()))
  self.assertEqual(len(rows),1);self.assertIn('échec',rows[0]['message']);self.assertEqual(rows[0]['level'],'error');self.assertEqual(rows[0]['stream'],'stderr')
 def test_collect_never_persists_plaintext_and_resets_on_publication(self):
  timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z');self.worker.raw_logs=lambda *args:[{'id':'c'*24,'timestamp':timestamp,'stream':'stdout','level':'info','message':'private-token\n'}];self.worker.collect(self.job);path=self.root/'runtime-logs'/(self.app+'.json');raw=path.read_text();self.assertNotIn('private-token',raw);self.assertIn('[masqué]',raw);self.assertEqual(path.stat().st_mode&0o777,0o640);before=json.loads(raw)['generation']
  self.job['deploymentId']='d'*15;self.job['containerName']='ph-web-'+self.app+'-'+self.job['deploymentId'];self.info['Name']='/'+self.job['containerName'];self.info['Config']['Labels']['pockethost.web.deployment']=self.job['deploymentId'];self.worker.collect(self.job);self.assertNotEqual(json.loads(path.read_text())['generation'],before)
 def test_second_docker_collection_uses_unix_seconds_overlap_and_deduplicates(self):
  base=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S');first=base+'.123456789Z';queries=[];responses=[]
  def frame(stream,timestamp,message):
   payload=(timestamp+' '+message+'\n').encode();return bytes([stream,0,0,0])+struct.pack('>I',len(payload))+payload
  initial=frame(1,first,'private-token');updated=initial+frame(1,base+'.223456789Z','PH_OBS_QA_PAGE_2')+frame(2,base+'.323456789Z','PH_OBS_QA_STDERR_3');responses.extend([initial,updated,updated])
  class Response(io.BytesIO):status=200
  class Connection:
   def __init__(self,*args):self.valid=False
   def request(self,method,path):
    since=urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)['since'][0];queries.append(since);self.valid=bool(re.fullmatch('[0-9]+',since))
   def getresponse(self):
    response=Response(responses.pop(0)if self.valid else b'invalid since');response.status=200 if self.valid else 400;return response
   def close(self):pass
  with mock.patch.object(logs,'_UnixHTTP',Connection):
   self.worker.collect(self.job);self.worker.collect(self.job);self.worker.collect(self.job)
  stored=json.loads((self.root/'runtime-logs'/(self.app+'.json')).read_text());rows=stored['records']
  self.assertEqual(len(rows),3);self.assertEqual(len({row['id']for row in rows}),3);self.assertEqual({row['stream']for row in rows},{'stdout','stderr'})
  self.assertEqual(int(queries[1]),int(logs.date(first).timestamp())-1);self.assertEqual(queries[1],queries[2]);self.assertNotIn('private-token',json.dumps(stored));self.assertIn('[masqué]',json.dumps(stored,ensure_ascii=False))
 def reader(self,options):
  arguments={'limit':100,'direction':'older',**options};result=subprocess.run([sys.executable,'-c',READER,str(self.root),self.app,self.dep,json.dumps(arguments)],check=True,capture_output=True);return json.loads(result.stdout)
 def fixture(self):
  folder=self.root/'runtime-logs';folder.mkdir(exist_ok=True);rows=[{'id':format(i,'024x'),'timestamp':datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:')+str(i%60).zfill(2)+'.123456789Z','stream':'stderr'if i%2 else'stdout','level':'error'if i%2 else'info','message':'échec '+str(i)+'\n'}for i in range(250)];value={'version':1,'appId':self.app,'deploymentId':self.dep,'generation':'a'*16,'checkedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'state':'running','records':rows};path=folder/(self.app+'.json');path.write_text(json.dumps(value));return path
 def test_reader_paginates_all_retained_rows_and_literal_unicode_filter(self):
  self.fixture();last=self.reader({});self.assertEqual(len(last['items']),100);self.assertTrue(last['hasOlder']);older=self.reader({'cursor':last['olderCursor']});self.assertEqual(len(older['items']),100);self.assertNotEqual(last['items'][0]['id'],older['items'][0]['id']);first=self.reader({'cursor':older['olderCursor']});self.assertEqual(len(first['items']),50);self.assertFalse(first['hasOlder']);newer=self.reader({'direction':'newer','cursor':older['newerCursor']});self.assertEqual(newer['items'],last['items']);filtered=self.reader({'q':'ÉCHEC 249','level':'error'});self.assertEqual(len(filtered['items']),1)
 def test_reader_rejects_symlink_and_handles_nanoseconds_and_rotation(self):
  path=self.fixture();value=self.reader({'from':'2020-01-01T00:00:00Z'});self.assertTrue(value['items']);rotated=self.reader({'cursor':'b'*16+':'+'a'*24,'direction':'newer'});self.assertTrue(rotated['reset']);self.assertEqual(rotated['items'],value['items']);path.unlink();private=self.root/'private';private.write_text('private-file');path.symlink_to(private);self.assertEqual(self.reader({})['items'],[])
class Response:
 def __init__(self,status=204):self.status=status
 def read(self,n):return b'never expose private receiver response'
class Connection:
 def __init__(self,status=204):self.status=status;self.requests=[]
 def request(self,*args,**kwargs):self.requests.append((args,kwargs))
 def getresponse(self):return Response(self.status)
 def close(self):pass
class NotificationTests(unittest.TestCase):
 def setUp(self):self.job={'id':'a'*15,'proof':'P'*80,'channel':'webhook','url':'https://receiver.example.com/hook','secret':'s'*32,'payload':{'event':'health_degraded','message':'Site unavailable'}};self.public=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
 def test_dns_rejects_private_mixed_metadata_and_loopback_destinations(self):
  for ip in ['127.0.0.1','10.0.0.1','169.254.169.254','::1','fc00::1','64:ff9b::a00:1']:
   with mock.patch.object(socket,'getaddrinfo',return_value=self.public+[(socket.AF_INET6 if':'in ip else socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443))]):
    with self.assertRaises(notify.NotificationError)as ctx:notify.public_addresses('receiver.example.com')
    self.assertEqual(ctx.exception.code,'ssrf')
 def test_signature_payload_and_stable_id_never_include_worker_proof(self):
  connection=Connection()
  with mock.patch.object(socket,'getaddrinfo',return_value=self.public):result=notify.send_webhook(self.job,connector=lambda *args:connection)
  self.assertTrue(result['success']);args,options=connection.requests[0];headers=options['headers'];body=options['body'];expected=hmac.new(self.job['secret'].encode(),headers['X-PocketHost-Timestamp'].encode()+b'.'+body,hashlib.sha256).hexdigest();self.assertEqual(headers['X-PocketHost-Signature'],'sha256='+expected);self.assertEqual(headers['X-PocketHost-Delivery'],self.job['id']);self.assertNotIn(self.job['proof'].encode(),body);self.assertNotIn(self.job['secret'].encode(),body)
 def test_redirect_refused_without_following_and_non_https_never_contacts(self):
  connection=Connection(302)
  with mock.patch.object(socket,'getaddrinfo',return_value=self.public):result=notify.send_webhook(self.job,connector=lambda *args:connection)
  self.assertFalse(result['success']);self.assertEqual(len(connection.requests),1)
  for url in ['http://receiver.example.com','https://user:pass@receiver.example.com','https://receiver.example.com:8443','https://127.0.0.1/hook','https://receiver.example.com/\r\n']:
   with mock.patch.object(socket,'getaddrinfo')as resolver:
    with self.assertRaises(notify.NotificationError):notify.send_webhook({**self.job,'url':url})
    resolver.assert_not_called()
 def test_dispatch_reports_failure_without_private_exception_material(self):
  worker=notify.Notifications('http://127.0.0.1:8091','s'*40,pathlib.Path('/tmp'));calls=[];worker.api=lambda path,body:calls.append((path,body))or{}
  with mock.patch.object(notify,'send_webhook',side_effect=notify.NotificationError('ssrf')):worker.dispatch(self.job)
  self.assertEqual(calls[0][0],'report');self.assertEqual(calls[0][1]['errorCode'],'ssrf');self.assertNotIn('url',calls[0][1]);self.assertFalse(calls[0][1]['success'])
def certificate(folder,name):
 """Self-signed certificate (SAN=name) used both as server chain and as the client's only trust anchor."""
 config=folder/'openssl.cnf';config.write_text('[req]\ndistinguished_name=dn\nprompt=no\nx509_extensions=ext\n[dn]\nCN='+name+'\n[ext]\nsubjectAltName=DNS:'+name+'\nbasicConstraints=critical,CA:TRUE\n')
 subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','2','-keyout',str(folder/'key.pem'),'-out',str(folder/'cert.pem'),'-config',str(config)],check=True,capture_output=True);return folder/'cert.pem',folder/'key.pem'
class FormatTests(unittest.TestCase):
 def setUp(self):self.public=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))];self.payload={'version':1,'event':'deployment_failed','occurredAt':'2026-10-29T10:00:00.000Z','application':{'id':'a'*15,'name':'Shop <@everyone> & co','url':'https://shop.example.com'},'message':'Un déploiement a échoué.','details':{'deploymentId':'d'*15}}
 def send(self,job,status=204):
  connection=Connection(status)
  with mock.patch.object(socket,'getaddrinfo',return_value=self.public):result=notify.send_webhook({'id':'a'*15,'secret':'s'*32,'payload':self.payload,**job},connector=lambda *args:connection)
  return result,connection
 def test_slack_body_is_text_only_escaped_branded_and_unsigned(self):
  result,connection=self.send({'url':'https://hooks.slack.com/services/T1/B2/xyz','format':'slack','secret':''},200);self.assertTrue(result['success'])
  args,options=connection.requests[0];body=json.loads(options['body']);self.assertEqual(set(body),{'text'});self.assertIn('Kolab Hosting',body['text']);self.assertIn('&lt;@everyone&gt; &amp; co',body['text']);self.assertIn('deployment_failed',body['text']);self.assertNotIn('X-PocketHost-Signature',options['headers']);self.assertEqual(args[1],'/services/T1/B2/xyz')
 def test_discord_body_has_content_without_mentions_and_is_bounded(self):
  self.payload['application']['name']='x'*5000;result,connection=self.send({'url':'https://discord.com/api/webhooks/123/tok_en','format':'discord'});self.assertTrue(result['success'])
  body=json.loads(connection.requests[0][1]['body']);self.assertEqual(set(body),{'content','allowed_mentions'});self.assertEqual(body['allowed_mentions'],{'parse':[]});self.assertLessEqual(len(body['content']),2000);self.assertTrue(body['content'].startswith('**Kolab Hosting'))
 def test_generic_stays_signed_and_unknown_or_mismatched_format_never_resolves(self):
  result,connection=self.send({'url':'https://receiver.example.com/hook','format':'generic'});headers=connection.requests[0][1]['headers'];self.assertTrue(result['success']);self.assertTrue(headers['X-PocketHost-Signature'].startswith('sha256='));self.assertEqual(json.loads(connection.requests[0][1]['body']),self.payload);self.assertTrue(headers['User-Agent'].startswith('KolabHosting'))
  for job in [{'url':'https://receiver.example.com/hook','format':'teams'},{'url':'https://receiver.example.com/services/T/B/x','format':'slack'},{'url':'https://discord.com.evil.example/api/webhooks/1/x','format':'discord'}]:
   with mock.patch.object(socket,'getaddrinfo')as resolver:
    with self.assertRaises(notify.NotificationError)as ctx:notify.send_webhook({'id':'a'*15,'secret':'s'*32,'payload':self.payload,**job})
    self.assertEqual(ctx.exception.code,'invalid_format');resolver.assert_not_called()
  with self.assertRaises(notify.NotificationError)as ctx:notify.send_webhook({'id':'a'*15,'secret':'short','payload':self.payload,'url':'https://receiver.example.com/hook'})
  self.assertEqual(ctx.exception.kind,'payload')
 def test_status_classes_and_final_flag(self):
  for status,code,kind in [(302,'http_refused','redirect'),(429,'http_refused','rate_limited'),(404,'http_refused','http_4xx'),(503,'http_refused','http_5xx'),(200,'','')]:
   connection=Connection(status)
   with mock.patch.object(socket,'getaddrinfo',return_value=self.public):result=notify.deliver({'id':'a'*15,'secret':'s'*32,'payload':self.payload,'url':'https://receiver.example.com/hook'},connector=lambda *args:connection)
   self.assertEqual((result['errorCode'],result['errorClass'],result['final']),(code,kind,False));self.assertIsInstance(result['durationMs'],int)
  self.assertTrue(notify.deliver({'id':'a'*15,'payload':self.payload,'url':'https://10.0.0.1/hook'})['final'])
  with mock.patch.object(socket,'getaddrinfo',side_effect=socket.gaierror()):
   result=notify.deliver({'id':'a'*15,'secret':'s'*32,'payload':self.payload,'url':'https://receiver.example.com/hook','trigger':'test'})
  self.assertEqual((result['errorCode'],result['errorClass'],result['final']),('network','dns',True))
 def test_test_trigger_is_one_attempt_and_final_and_no_send_test_cli_remains(self):
  connection=Connection(200)
  with mock.patch.object(socket,'getaddrinfo',return_value=self.public):value=notify.deliver({'id':'a'*15,'url':'https://receiver.example.com/hook','secret':'s'*32,'payload':self.payload,'trigger':'test'},connector=lambda *args:connection)
  self.assertEqual((value['success'],value['httpStatus'],value['final']),(True,200,True));self.assertEqual(len(connection.requests),1)
  self.assertFalse(hasattr(notify,'send_test'));self.assertNotIn("__main__",pathlib.Path(notify.__file__).read_text())
 def test_chat_text_uses_title_summary_dashboard_link_and_platform_fallback(self):
  payload={**self.payload,'version':2,'title':'Échec de déploiement','application':{**self.payload['application'],'dashboardUrl':'https://app.example.com/applications/'+'a'*15+'?tab=deployments'},'details':{'summary':'Exemple :\nla commande de build a échoué (code 1).','env':'SECRET=value','drill':True}}
  text=notify.text_summary(payload);lines=text.split('\n')
  self.assertEqual(lines[0],'Kolab Hosting · Échec de déploiement · Shop <@everyone> & co');self.assertIn('Événement : deployment_failed (test)',text)
  self.assertIn('Application : https://shop.example.com',text);self.assertIn('Détail : Exemple : la commande de build a échoué (code 1).',text);self.assertIn('Ouvrir dans le dashboard : https://app.example.com/applications/'+'a'*15+'?tab=deployments',text);self.assertNotIn('SECRET',text)
  for bad in ['javascript:alert(1)','http://app.example.com/x','https://app.example.com/x y']:
   self.assertNotIn('dashboard',notify.text_summary({**payload,'application':{**payload['application'],'dashboardUrl':bad}}))
  platform=notify.text_summary({'event':'worker_unresponsive','title':'Worker web injoignable','message':'Le worker ne répond plus.','application':None,'platform':{'host':'app1.example.com'},'dashboardUrl':'https://app.example.com/applications'})
  self.assertTrue(platform.startswith('Kolab Hosting · Worker web injoignable · Plateforme app1.example.com'));self.assertNotIn('Site web',platform);self.assertIn('Ouvrir dans le dashboard : https://app.example.com/applications',platform)
  self.assertTrue(notify.text_summary({'event':'x','application':None,'platform':{'host':'bad host\n'}}).startswith('Kolab Hosting · Plateforme\n'))
 def test_controller_unreachable_goes_once_to_the_cached_platform_webhook(self):
  worker=notify.Notifications('http://127.0.0.1:8091','s'*40,pathlib.Path('/tmp'));sent=[]
  ok=lambda job:sent.append(job)or{'success':True,'final':False}
  with mock.patch.object(notify,'deliver',side_effect=ok):
   self.assertIsNone(worker.unreachable(0));self.assertIsNone(worker.unreachable(500))  # No platform webhook known: nothing.
   worker.reachable({'job':None,'platformWebhook':{'url':'https://discord.com/api/webhooks/1/x','format':'discord','host':'app1.example.com'}})
   self.assertIsNone(worker.unreachable(1000));self.assertIsNone(worker.unreachable(1000+notify.UNREACHABLE_AFTER-1))
   self.assertTrue(worker.unreachable(1000+notify.UNREACHABLE_AFTER)['success']);self.assertIsNone(worker.unreachable(5000))
  self.assertEqual(len(sent),1);job=sent[0];self.assertEqual(job['payload']['event'],'controller_unreachable');self.assertIsNone(job['payload']['application']);self.assertRegex(job['id'],'^[a-z0-9]{15}$');self.assertEqual(job['format'],'discord')
  failing=lambda job:sent.append(job)or{'success':False,'final':False}
  worker.reachable({'platformWebhook':{'url':'https://receiver.example.com/hook','secret':'s'*32}})
  with mock.patch.object(notify,'deliver',side_effect=failing):
   for now in range(0,2000,10):worker.unreachable(now)
  self.assertEqual(len(sent),1+notify.UNREACHABLE_ATTEMPTS)  # Bounded retries, one minute apart.
  worker.reachable({'platformWebhook':None});self.assertIsNone(worker.platform)
  worker.reachable({'platformWebhook':{'url':'https://x.example.com','format':'teams'}});self.assertIsNone(worker.platform)
 def test_loop_counts_only_claim_failures_as_unreachable(self):
  worker=notify.Notifications('http://127.0.0.1:8091','s'*40,pathlib.Path('/tmp'));seen=[]
  def api(path,body):
   seen.append(path)
   if len(seen)==1:raise OSError('down')
   worker.stop.set();return {'job':None}
  worker.api=api;worker.stop.wait=lambda _seconds:None
  with mock.patch.object(worker,'unreachable')as down:worker.loop()
  down.assert_called_once();self.assertIsNone(worker.down_since)
 def test_dispatch_marks_test_email_final_and_reports_class(self):
  worker=notify.Notifications('http://127.0.0.1:8091','s'*40,pathlib.Path('/tmp'));calls=[]
  def api(path,body):
   calls.append((path,body))
   if path=='email':raise OSError('smtp private text')
   return {}
  worker.api=api;worker.dispatch({'id':'a'*15,'proof':'P'*80,'channel':'email','trigger':'test','payload':self.payload})
  report=calls[-1][1];self.assertEqual((report['errorCode'],report['errorClass'],report['final'],report['success']),('smtp','smtp',True,False));self.assertNotIn('private',json.dumps(report))
@unittest.skipUnless(shutil.which('openssl'),'openssl requis')
class PinnedTlsTests(unittest.TestCase):
 """Real TLS on 127.0.0.1: vetted-address pinning, SNI, certificate verification and address fallback."""
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();folder=pathlib.Path(self.tmp.name);self.cert,key=certificate(folder,'receiver.example.com');self.seen=[];seen=self.seen
  class Handler(http.server.BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_POST(self):seen.append((self.path,dict(self.headers),self.rfile.read(int(self.headers['Content-Length']))));self.send_response(204);self.end_headers()
  context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(self.cert,key);self.sni=[];context.sni_callback=lambda sock,name,ctx:self.sni.append(name)
  self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.socket=context.wrap_socket(self.server.socket,server_side=True);threading.Thread(target=self.server.serve_forever,daemon=True).start()
  closed=socket.socket();closed.bind(('127.0.0.1',0));self.closed=closed.getsockname()[1];closed.close()
 def tearDown(self):self.server.shutdown();self.server.server_close();self.tmp.cleanup()
 def client(self,original=ssl.create_default_context):return original(cafile=str(self.cert))
 def job(self,host='receiver.example.com'):return {'id':'a'*15,'secret':'s'*32,'url':'https://'+host+'/hook?x=1','payload':{'event':'test','message':'Test'}}
 def test_pinned_tls_with_sni_and_fallback_to_next_vetted_address(self):
  addresses=[(socket.AF_INET,socket.SOCK_STREAM,6,('127.0.0.1',self.closed)),(socket.AF_INET,socket.SOCK_STREAM,6,('127.0.0.1',self.server.server_port))]
  with mock.patch.object(notify,'public_addresses',return_value=addresses),mock.patch.object(notify.ssl,'create_default_context',self.client):result=notify.deliver(self.job())
  self.assertTrue(result['success'],result);self.assertEqual(self.sni,['receiver.example.com']);path,headers,body=self.seen[0];self.assertEqual(path,'/hook?x=1');self.assertEqual(headers['Host'],'receiver.example.com');self.assertEqual(json.loads(body)['event'],'test')
 def test_certificate_for_another_name_is_a_tls_failure(self):
  addresses=[(socket.AF_INET,socket.SOCK_STREAM,6,('127.0.0.1',self.server.server_port))]
  with mock.patch.object(notify,'public_addresses',return_value=addresses),mock.patch.object(notify.ssl,'create_default_context',self.client):result=notify.deliver(self.job('other.example.com'))
  self.assertEqual((result['success'],result['errorCode'],result['errorClass']),(False,'network','tls'));self.assertEqual(self.seen,[])
 def test_all_addresses_refused_is_a_connect_failure(self):
  addresses=[(socket.AF_INET,socket.SOCK_STREAM,6,('127.0.0.1',self.closed))]
  with mock.patch.object(notify,'public_addresses',return_value=addresses):result=notify.deliver(self.job())
  self.assertEqual((result['errorCode'],result['errorClass']),('network','connect'))
if __name__=='__main__':unittest.main()
