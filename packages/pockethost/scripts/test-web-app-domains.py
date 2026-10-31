#!/usr/bin/env python3
import importlib.util,pathlib,tempfile,unittest,unittest.mock as mock,subprocess,os
SCRIPT=pathlib.Path(__file__).with_name('web_app_domains.py');spec=importlib.util.spec_from_file_location('domains',SCRIPT);domains=importlib.util.module_from_spec(spec);spec.loader.exec_module(domains)
class DomainTests(unittest.TestCase):
 def dns(self,hostname,kind):return {'TXT':['"pockethost-verification='+('a'*40)+'"'],'A':['141.94.92.92'],'AAAA':[]}[kind]
 def test_ownership_and_exact_dns(self):
  with mock.patch.object(domains,'_dns',self.dns):self.assertTrue(domains.verify_web_domain_dns('www.example.com','141.94.92.92','a'*40)['verified'])
 def test_missing_ownership_rejected(self):
  with mock.patch.object(domains,'_dns',lambda host,kind:[]):
   with self.assertRaisesRegex(ValueError,'TXT'):domains.verify_web_domain_dns('www.example.com','141.94.92.92','a'*40)
 def test_wrong_or_additional_addresses_rejected(self):
  for kind,value in [('A',['141.94.92.92','1.1.1.1']),('AAAA',['2606:4700:4700::1111'])]:
   with mock.patch.object(domains,'_dns',lambda host,k:self.dns(host,k) if k!=kind else value):
    with self.assertRaisesRegex(ValueError,'A/AAAA'):domains.verify_web_domain_dns('www.example.com','141.94.92.92','a'*40)
 def test_invalid_host_and_private_server_ip_rejected(self):
  for host,ip in [('example.com/x','141.94.92.92'),('www.example.com','127.0.0.1')]:
   with self.assertRaises(ValueError):domains.verify_web_domain_dns(host,ip,'a'*40)
 def test_only_explicit_platform_alias_skips_txt_while_address_stays_required(self):
  with mock.patch.dict(os.environ,{'APEX_DOMAIN':'example.com','WEB_APPS_PLATFORM_ALIASES':'testgg.example.com,app.example.com'},clear=False):
   with mock.patch.object(domains,'_dns',lambda host,kind:['141.94.92.92']if kind=='A'else[]):
    self.assertTrue(domains.verify_web_domain_dns('testgg.example.com','141.94.92.92','a'*40)['verified'])
    for host in ['other.example.com','app.example.com','testgg.evil.com']:
     with self.assertRaisesRegex(ValueError,'TXT'):domains.verify_web_domain_dns(host,'141.94.92.92','a'*40)
   with mock.patch.object(domains,'_dns',lambda host,kind:['1.1.1.1']if kind=='A'else[]):
    with self.assertRaisesRegex(ValueError,'A/AAAA'):domains.verify_web_domain_dns('testgg.example.com','141.94.92.92','a'*40)
 def test_missing_https_configuration_has_explicit_safe_errors(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp).resolve()
   with self.assertRaisesRegex(RuntimeError,'contact'):domains.provision_web_domain_certificate(root,'www.example.com','141.94.92.92','')
   with self.assertRaisesRegex(RuntimeError,'IP publique'):domains.provision_web_domain_certificate(root,'www.example.com','','ops@example.com')
 def test_provision_isolated_paths_group_readable_pair_and_no_service_restart(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp).resolve();calls=[]
   def run(command,**kwargs):
    calls.append(command);live=root/'certbot/config/live/www.example.com';live.mkdir(parents=True)
    subprocess.check_call(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-subj','/CN=www.example.com','-keyout',str(live/'privkey.pem'),'-out',str(live/'fullchain.pem'),'-days','2'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    (live/'cert.pem').write_bytes((live/'fullchain.pem').read_bytes())
    return mock.Mock(returncode=0)
   with mock.patch.object(domains.shutil,'which',lambda name:'/usr/bin/certbot'),mock.patch.object(domains.subprocess,'run',run):
    value=domains.provision_web_domain_certificate(root,'www.example.com','141.94.92.92','ops@example.com')
   self.assertTrue(value['certificate']);self.assertIn('certificateExpiresAt',value)
   self.assertEqual((root/'tls/www.example.com/privkey.pem').stat().st_mode&0o777,0o640)
   command=calls[0];self.assertIn('--webroot',command);self.assertNotIn('--standalone',command);self.assertFalse(any('restart' in arg for arg in command));self.assertEqual(command[command.index('--config-dir')+1],str(root/'certbot/config'))
if __name__=='__main__':unittest.main()
