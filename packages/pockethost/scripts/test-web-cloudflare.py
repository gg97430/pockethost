"""No-network DNS safety tests: only explicit, fresh previews may mutate exact names."""
import copy,importlib.util,pathlib,ssl,unittest
from unittest.mock import MagicMock,patch
spec=importlib.util.spec_from_file_location('cf',pathlib.Path(__file__).with_name('web_cloudflare.py'));cf=importlib.util.module_from_spec(spec);spec.loader.exec_module(cf)
HOST='1.lapub.re';IP='135.125.67.39';TXT='pockethost-verification='+'x'*40
ORIGIN_TLS=cf.origin_tls;PUBLIC_OWNERSHIP=cf.public_ownership
class CloudflareTests(unittest.TestCase):
 def setUp(self):
  self.rows=[];self.calls=[];self.next_id=0;self.after_batch=None
  tls=patch.object(cf,'origin_tls');self.tls=tls.start();self.addCleanup(tls.stop)
  public=patch.object(cf,'public_ownership');self.public=public.start();self.addCleanup(public.stop)
 def api(self,token,path,body=None):
  self.calls.append((path,body))
  if path.startswith('/zones?'):
   from urllib.parse import parse_qs,urlsplit
   name=parse_qs(urlsplit(path).query)['name'][0]
   return {'result':[{'id':'zone1','name':'lapub.re','status':'active'}] if name=='lapub.re' else []}
  if '/dns_records?' in path:
   from urllib.parse import parse_qs,urlsplit
   name=parse_qs(urlsplit(path).query)['name'][0]
   return {'result':copy.deepcopy([r for r in self.rows if r['name']==name]),'result_info':{'total_pages':1}}
  if path.startswith('/zones/zone1/dns_records/')and path!='/zones/zone1/dns_records/batch':
   return {'result':copy.deepcopy(next(r for r in self.rows if r['id']==path.rsplit('/',1)[1]))}
  assert path=='/zones/zone1/dns_records/batch'
  deleted={r['id']for r in body.get('deletes',[])};self.rows=[r for r in self.rows if r['id']not in deleted]
  for change in body.get('patches',[]):next(r for r in self.rows if r['id']==change['id']).update(change)
  for change in body.get('posts',[]):
   self.next_id+=1;self.rows.append({'id':'created'+str(self.next_id),**change})
  if self.after_batch:self.after_batch()
  return {'success':True}
 def runplan(self,action='preview',revision=None,**extra):
  with patch.object(cf,'request',self.api):return cf.run({'token':'secret','hostname':HOST,'publicIp':IP,'ownershipValue':TXT,'action':action,'revision':revision,**extra})
 def row(self,kind,content,name=HOST,**extra):
  self.next_id+=1;return {'id':str(self.next_id),'type':kind,'name':name,'content':content,'ttl':1,**extra}
 def valid(self,proxied=False):
  self.rows=[self.row('A',IP,proxied=proxied),self.row('TXT',TXT,'_pockethost.'+HOST)]
  return self.rows[0]['id']
 def writes(self):return [body for _,body in self.calls if body is not None]
 def activation(self,**extra):
  return self.runplan('activate',zoneId='zone1',recordId=self.rows[0]['id'],**extra)
 def test_preview_never_writes_and_matches_parent_zone(self):
  p=self.runplan();self.assertEqual(p['zone']['name'],'lapub.re');self.assertEqual(len(p['batch']['posts']),2);self.assertFalse(any(b for _,b in self.calls))
 def test_proxied_a_and_ipv6_are_explicit(self):
  self.rows=[self.row('A','192.0.2.9',proxied=True),self.row('AAAA','2001:db8::1')]
  p=self.runplan();self.assertEqual(len(p['batch']['deletes']),1);self.assertFalse(p['batch']['patches'][0]['proxied']);self.assertEqual(p['batch']['patches'][0]['content'],IP)
 def test_cname_replaced_but_unrelated_txt_and_mx_preserved(self):
  self.rows=[self.row('CNAME','old.example.com'),self.row('MX','mail.example.com'),self.row('TXT','other=value','_pockethost.'+HOST)]
  p=self.runplan();self.assertEqual(len(p['batch']['deletes']),1);self.assertEqual(p['batch']['deletes'][0]['id'],self.rows[0]['id']);self.assertEqual(len(p['batch']['posts']),2)
 def test_changed_dns_rejected_without_write(self):
  p=self.runplan();self.rows=[self.row('A','192.0.2.3',proxied=False)]
  with self.assertRaisesRegex(ValueError,'ont changé'):self.runplan('apply',p['revision'])
  self.assertFalse(any(b for _,b in self.calls))
 def test_apply_uses_one_batch(self):
  p=self.runplan();result=self.runplan('apply',p['revision']);self.assertTrue(result['applied']);self.assertEqual(sum(b is not None for _,b in self.calls),1)
  self.assertEqual(result['zoneId'],'zone1');self.assertEqual(result['recordId'],self.rows[0]['id']);self.assertIs(result['proxied'],False)
 def test_idempotent_correct_records(self):
  self.rows=[self.row('A',IP,proxied=False),self.row('TXT',TXT,'_pockethost.'+HOST)]
  p=self.runplan();self.assertEqual(p['changes'],[]);self.runplan('apply',p['revision']);self.assertFalse(any(b for _,b in self.calls))
 def test_ns_delegation_refused(self):
  self.rows=[self.row('NS','ns.example.com')]
  with self.assertRaisesRegex(ValueError,'délégation'):self.runplan()
 def test_no_accessible_zone(self):
  with patch.object(cf,'request',return_value={'result':[]}):
   with self.assertRaisesRegex(ValueError,'aucune zone'):cf.preview('secret',HOST,IP,TXT)
 def test_invalid_input_never_requests(self):
  with patch.object(cf,'request') as request:
   for host,ip in [('lapub.re.evil/',IP),(HOST,'127.0.0.1')]:
    with self.assertRaises(ValueError):cf.preview('secret',host,ip,TXT)
   request.assert_not_called()
 def test_proxied_preview_and_apply_require_tls_and_bind_revision(self):
  p=self.runplan(proxied=True);self.assertTrue(p['proxied']);self.assertTrue(p['batch']['posts'][0]['proxied'])
  plain=self.runplan();self.assertNotEqual(p['revision'],plain['revision'])
  with self.assertRaisesRegex(ValueError,'ont changé'):self.runplan('apply',plain['revision'],proxied=True)
  self.assertEqual(self.writes(),[])
  result=self.runplan('apply',p['revision'],proxied=True);self.assertIs(result['proxied'],True)
  self.assertEqual(self.tls.call_args.args,(HOST,))
 def test_invalid_proxy_choice_is_never_coerced(self):
  for value in ('false','true',0,1,None,[]):
   with self.subTest(value=value),self.assertRaisesRegex(ValueError,'booléen'):self.runplan(proxied=value)
  self.assertEqual(self.calls,[])
 def test_tls_failure_prevents_proxied_preview_and_apply(self):
  plan=self.runplan(proxied=True)
  self.calls.clear();self.tls.side_effect=ValueError('Certificat HTTPS invalide')
  for action in ('preview','apply'):
   with self.assertRaisesRegex(ValueError,'HTTPS'):self.runplan(action,plan['revision'],proxied=True)
  self.assertEqual(self.calls,[])
 def test_apply_postread_checks_actual_provider_state(self):
  p=self.runplan()
  self.after_batch=lambda:self.rows[0].update(content='192.0.2.1')
  with self.assertRaisesRegex(ValueError,'enregistrement A'):self.runplan('apply',p['revision'])
  self.assertEqual(len(self.writes()),1)
 def test_verify_provider_identity_and_public_proof_without_writes(self):
  rid=self.valid();r=self.runplan('verify')
  self.assertEqual(r,{'verified':True,'zoneId':'zone1','recordId':rid,'proxied':False})
  self.public.assert_called_once_with(HOST,TXT);self.tls.assert_not_called();self.assertEqual(self.writes(),[])
 def test_verify_observed_proxy_checks_dns_without_requiring_ready_tls(self):
  self.valid(True);self.tls.side_effect=ValueError('HTTPS non valide')
  self.assertTrue(self.runplan('verify')['proxied']);self.tls.assert_not_called();self.assertEqual(self.writes(),[])
 def test_verify_rejects_missing_or_wrong_provider_proof(self):
  for value in ('wrong','pockethost-verification='+'z'*40):
   self.valid();self.rows[1]['content']=value
   with self.assertRaisesRegex(ValueError,'TXT Cloudflare'):self.runplan('verify')
  self.valid();self.rows.pop()
  with self.assertRaisesRegex(ValueError,'TXT Cloudflare'):self.runplan('verify')
  self.assertEqual(self.writes(),[])
 def test_verify_rejects_wrong_a_duplicates_and_conflicting_types(self):
  self.valid();self.rows[0]['content']='192.0.2.1'
  with self.assertRaisesRegex(ValueError,'enregistrement A'):self.runplan('verify')
  self.valid();self.rows.append(self.row('A',IP,proxied=False))
  with self.assertRaisesRegex(ValueError,'enregistrement A'):self.runplan('verify')
  for kind in ('AAAA','CNAME','NS'):
   self.valid();self.rows.append(self.row(kind,'other.example.com'))
   with self.subTest(kind=kind),self.assertRaises(ValueError):self.runplan('verify')
  self.assertEqual(self.writes(),[])
 def test_verify_pinned_ids_and_public_txt_must_match(self):
  rid=self.valid()
  for values in ({'zoneId':'other'},{'recordId':'other'}):
   with self.assertRaisesRegex(ValueError,'changé'):self.runplan('verify',**values)
  self.public.side_effect=ValueError('TXT public absent')
  with self.assertRaisesRegex(ValueError,'TXT public'):self.runplan('verify',zoneId='zone1',recordId=rid)
  self.assertEqual(self.writes(),[])
 def test_activate_only_patches_pinned_a_proxy_and_preserves_other_records(self):
  rid=self.valid();extras=[self.row('MX','mail.example.com'),self.row('TXT','other=value','_pockethost.'+HOST),self.row('A','192.0.2.9','other.lapub.re',proxied=False)]
  self.rows.extend(extras);saved=copy.deepcopy(extras)
  r=self.activation();self.assertTrue(r['activated']);self.assertTrue(r['proxied'])
  self.assertEqual(self.writes(),[{'patches':[{'id':rid,'proxied':True}]}])
  self.assertEqual(self.rows[2:],saved);self.assertEqual(self.rows[1]['content'],TXT)
  self.assertEqual(self.tls.call_count,2);self.assertEqual(self.public.call_count,2)
 def test_activate_already_proxied_is_idempotent(self):
  self.valid(True);self.assertTrue(self.activation()['activated']);self.assertEqual(self.writes(),[])
 def test_activate_requires_valid_pinned_ids_before_any_request(self):
  self.valid()
  for values in ({},{'zoneId':'zone1'},{'zoneId':'../bad','recordId':'one'}):
   with self.assertRaisesRegex(ValueError,'Identifiant'):self.runplan('activate',**values)
  self.assertEqual(self.calls,[])
 def test_activate_wrong_pins_or_tls_never_mutate(self):
  rid=self.valid()
  for values in ({'zoneId':'other','recordId':rid},{'zoneId':'zone1','recordId':'other'}):
   with self.assertRaisesRegex(ValueError,'changé'):self.runplan('activate',**values)
  self.tls.side_effect=ValueError('HTTPS non valide')
  with self.assertRaisesRegex(ValueError,'HTTPS'):self.activation()
  self.assertEqual(self.writes(),[])
 def test_activate_rejects_drift_in_exact_record_read_before_batch(self):
  rid=self.valid();api=self.api
  def drifting(token,path,body=None):
   value=api(token,path,body)
   if path=='/zones/zone1/dns_records/'+rid:value['result']['modified_on']='changed'
   return value
  self.api=drifting
  with self.assertRaisesRegex(ValueError,'changé pendant'):self.activation()
  self.assertEqual(self.writes(),[])
 def test_activate_rejects_drift_between_snapshot_and_final_record_read(self):
  self.valid();api=self.api;host_reads=0
  def drifting(token,path,body=None):
   nonlocal host_reads
   value=api(token,path,body)
   if '/dns_records?'in path and value['result']and value['result'][0]['type']=='A':
    host_reads+=1
    if host_reads==2:self.rows[0]['modified_on']='changed';value['result'][0]['modified_on']='changed'
   return value
  self.api=drifting
  with self.assertRaisesRegex(ValueError,'changé pendant'):self.activation()
  self.assertEqual(self.writes(),[])
 def test_activate_postverify_failure_never_rewrites_external_drift(self):
  self.valid();self.after_batch=lambda:self.rows[0].update(content='192.0.2.9')
  with self.assertRaisesRegex(ValueError,'enregistrement A'):self.activation()
  self.assertEqual(len(self.writes()),1);self.assertEqual(self.rows[0]['content'],'192.0.2.9')
 def test_activate_public_txt_failure_prevents_write(self):
  self.valid();self.public.side_effect=ValueError('TXT public incorrect')
  with self.assertRaisesRegex(ValueError,'TXT public'):self.activation()
  self.assertEqual(self.writes(),[])
 def test_unexpected_provider_name_is_rejected_without_write(self):
  api=self.api
  def wrong_name(token,path,body=None):
   value=api(token,path,body)
   if '/dns_records?'in path:value['result']=[self.row('A',IP,'other.lapub.re',proxied=False)]
   return value
  self.api=wrong_name
  with self.assertRaisesRegex(ValueError,'nom demandé'):self.runplan()
  self.assertEqual(self.writes(),[])
 def test_public_txt_uses_shared_resolver_and_accepts_split_strings(self):
  import web_app_domains
  with patch.object(web_app_domains,'_dns',return_value=['"'+TXT[:25]+'" "'+TXT[25:]+'"'])as dns:
   PUBLIC_OWNERSHIP(HOST,TXT);dns.assert_called_once_with('_pockethost.'+HOST,'TXT')
  with patch.object(web_app_domains,'_dns',return_value=['"wrong"']):
   with self.assertRaisesRegex(ValueError,'TXT public'):PUBLIC_OWNERSHIP(HOST,TXT)
 def test_origin_tls_uses_loopback_and_ca_hostname_verification(self):
  context=ssl.create_default_context();wrapped=MagicMock(wraps=context);wrapped.wrap_socket.return_value=MagicMock()
  connection=MagicMock();raw=connection.return_value.__enter__.return_value
  with patch.object(cf.ssl,'create_default_context',return_value=wrapped),patch.object(cf.socket,'create_connection',connection):ORIGIN_TLS(HOST)
  self.assertTrue(context.check_hostname);self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
  connection.assert_called_once_with(('127.0.0.1',443),timeout=4);wrapped.wrap_socket.assert_called_once_with(raw,server_hostname=HOST)
 def test_origin_tls_certificate_errors_are_safe_and_do_not_disable_validation(self):
  context=MagicMock();context.wrap_socket.side_effect=ssl.SSLCertVerificationError('secret-canary')
  with patch.object(cf.ssl,'create_default_context',return_value=context),patch.object(cf.socket,'create_connection',MagicMock()):
   with self.assertRaisesRegex(ValueError,'certificat HTTPS')as error:ORIGIN_TLS(HOST)
  self.assertNotIn('secret-canary',str(error.exception))
if __name__=='__main__':unittest.main()
