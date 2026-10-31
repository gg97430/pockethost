#!/usr/bin/env python3
"""Fixed GitHub transport validation with fake HTTPS responses, actual RSA signing and no remote writes."""
import pathlib,re,tempfile,subprocess,unittest,json,sys,io,urllib.request,urllib.error,signal
from unittest import mock
FILE=pathlib.Path(__file__).resolve().parents[1]/'src/mothership-app/src/lib/handlers/webApps/connect.ts'
SCRIPT=re.search(r'GITHUB_CONNECT_READER = String.raw`(.*?)`',FILE.read_text(),re.S).group(1)
class GitHubTransportTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.folder=pathlib.Path(cls.tmp.name).resolve();cls.key=cls.folder/'key.pem'
  subprocess.run(['openssl','genrsa','-out',str(cls.key),'2048'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def setUp(self):self.requests=[];self.owner='77';self.app='42';self.contents='read';self.extra={};self.events=['push'];self.count=3
 def opener(self,request,timeout):
  self.requests.append(request);path=urllib.parse.urlsplit(request.full_url).path
  if path.startswith('/app-manifests/'):
   return io.BytesIO(json.dumps({'id':42,'pem':'PRIVATE-KEY-FIXTURE','client_secret':'SHOULD-NOT-BE-RETURNED','client_id':'unused','slug':'pockethost-test','owner':{'id':77,'login':'test-owner'},'webhook_secret':'fixture-secret','permissions':{},'events':[]}).encode())
  if path=='/app/installations':return io.BytesIO(json.dumps([{'id':123,'app_id':self.app,'account':{'id':self.owner,'login':'test-owner'}}]).encode())
  if path=='/app/installations/123':return io.BytesIO(json.dumps({'app_id':self.app,'account':{'id':self.owner,'login':'test-owner'},'events':self.events,'permissions':{'metadata':'read','contents':self.contents,**self.extra}}).encode())
  if path.endswith('/access_tokens'):return io.BytesIO(b'{"token":"SCOPED-INSTALLATION-TOKEN"}')
  if path=='/installation/repositories':return io.BytesIO(json.dumps({'total_count':self.count,'repositories':[]}).encode())
  raise AssertionError(path)
 def run_reader(self,action,opener=None):
  file=self.folder/'payload.json';payload={'action':action,'code':'a'*40,'installationId':'123','config':{'githubAppId':'42','ownerId':'77','privateKey':self.key.read_text()}};file.write_text(json.dumps(payload));file.chmod(0o600)
  output=io.StringIO();before=sys.argv;sys.argv=['reader',str(file)]
  try:
   with mock.patch.object(urllib.request,'urlopen',side_effect=opener or self.opener),mock.patch('sys.stdout',output):exec(compile(SCRIPT,'reader','exec'),{'__name__':'__main__'})
  finally:sys.argv=before;signal.alarm(0)
  self.assertFalse(file.exists());return json.loads(output.getvalue())
 def test_conversion_omits_unused_oauth_credentials(self):
  r=self.run_reader('convert');self.assertNotIn('client_id',r['result']);self.assertNotIn('client_secret',r['result']);self.assertNotIn('SHOULD-NOT-BE-RETURNED',json.dumps(r));self.assertEqual(self.requests[0].get_method(),'POST')
 def test_signed_app_and_scoped_token_validate_repositories(self):
  r=self.run_reader('verify-installation');self.assertEqual(r['result']['repositoryCount'],3);self.assertEqual(r['result']['accountId'],'77');self.assertNotIn('SCOPED-INSTALLATION-TOKEN',json.dumps(r));self.assertEqual(len(self.requests),3)
  header=self.requests[0].headers['Authorization'];self.assertTrue(header.startswith('Bearer '));self.assertEqual(header.count('.'),2)
  self.assertEqual(json.loads(self.requests[1].data),{'permissions':{'contents':'read','metadata':'read'}});self.assertEqual(self.requests[2].headers['Authorization'],'Bearer SCOPED-INSTALLATION-TOKEN')
 def test_existing_installation_is_discovered_only_for_expected_private_app_owner(self):
  result=self.run_reader('discover-installation');self.assertEqual(result['result']['installationId'],'123');self.assertEqual(len(self.requests),4)
  self.requests=[];self.owner='99';self.assertIn('error',self.run_reader('discover-installation'));self.assertEqual(len(self.requests),1)
 def test_different_app_or_account_cannot_activate(self):
  self.app='99';r=self.run_reader('verify-installation');self.assertIn('error',r);self.assertEqual(len(self.requests),1)
  self.requests=[];self.app='42';self.owner='99';r=self.run_reader('verify-installation');self.assertIn('error',r);self.assertEqual(len(self.requests),1)
 def test_write_permission_or_missing_push_rejected(self):
  self.contents='write';self.assertIn('error',self.run_reader('verify-installation'));self.contents='read';self.extra={'issues':'write'};self.assertIn('error',self.run_reader('verify-installation'));self.extra={};self.events=[];self.assertIn('error',self.run_reader('verify-installation'))
 def test_no_selected_repository_does_not_activate(self):
  self.count=0;self.assertIn('error',self.run_reader('verify-installation'))
 def test_network_errors_never_echo_response_or_private_material(self):
  def denied(request,timeout):raise urllib.error.HTTPError(request.full_url,403,'PRIVATE-KEY-FIXTURE',{},io.BytesIO(b'PRIVATE-KEY-FIXTURE'))
  r=self.run_reader('convert',denied);self.assertIn('error',r);self.assertNotIn('PRIVATE-KEY-FIXTURE',json.dumps(r))
if __name__=='__main__':unittest.main()
