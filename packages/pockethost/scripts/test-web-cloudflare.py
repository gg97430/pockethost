"""No-network DNS safety tests: only explicit, fresh previews may mutate exact names."""
import importlib.util,pathlib,unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('cf',pathlib.Path(__file__).with_name('web_cloudflare.py'));cf=importlib.util.module_from_spec(spec);spec.loader.exec_module(cf)
HOST='1.lapub.re';IP='135.125.67.39';TXT='pockethost-verification='+'x'*40
class CloudflareTests(unittest.TestCase):
 def setUp(self):self.rows=[];self.calls=[]
 def api(self,token,path,body=None):
  self.calls.append((path,body))
  if path.startswith('/zones?'):
   from urllib.parse import parse_qs,urlsplit
   name=parse_qs(urlsplit(path).query)['name'][0]
   return {'result':[{'id':'zone1','name':'lapub.re','status':'active'}] if name=='lapub.re' else []}
  if '/dns_records?' in path:
   from urllib.parse import parse_qs,urlsplit
   name=parse_qs(urlsplit(path).query)['name'][0]
   return {'result':[r for r in self.rows if r['name']==name],'result_info':{'total_pages':1}}
  assert path=='/zones/zone1/dns_records/batch';return {'success':True}
 def runplan(self,action='preview',revision=None):
  with patch.object(cf,'request',self.api):return cf.run({'token':'secret','hostname':HOST,'publicIp':IP,'ownershipValue':TXT,'action':action,'revision':revision})
 def row(self,kind,content,name=HOST,**extra):return {'id':str(len(self.rows)), 'type':kind,'name':name,'content':content,'ttl':1,**extra}
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
if __name__=='__main__':unittest.main()
