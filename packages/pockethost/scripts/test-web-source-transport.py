#!/usr/bin/env python3
"""V3 source transport: fake GitHub HTTPS, real RSA signing, no network or remote writes."""
import io,json,pathlib,re,signal,subprocess,sys,tempfile,unittest,urllib.error,urllib.parse,urllib.request
from unittest import mock
FILE=pathlib.Path(__file__).resolve().parents[1]/'src/mothership-app/src/lib/handlers/webApps/github.ts'
SCRIPT=re.search(r'GITHUB_READER = String.raw`(.*?)`',FILE.read_text(),re.S).group(1)
class SourceTransportTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory(prefix='ph-source-transport-');cls.folder=pathlib.Path(cls.tmp.name);cls.key=cls.folder/'key.pem'
  subprocess.run(['openssl','genrsa','-out',str(cls.key),'2048'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def setUp(self):
  self.requests=[];self.total=125;self.suspended=False;self.account_type='Organization';self.install_app='42';self.denied_repository=False;self.install_permissions={'contents':'read','metadata':'read'};self.install_events=[]
 def repository(self,index):return {'id':index,'full_name':f'owner/project-{index:04}','default_branch':'main','private':True}
 def opener(self,req,timeout):
  self.requests.append(req);url=urllib.parse.urlsplit(req.full_url);self.assertEqual(url.scheme,'https');self.assertEqual(url.netloc,'api.github.com');q=urllib.parse.parse_qs(url.query);path=url.path
  if path=='/app':return io.BytesIO(json.dumps({'id':42,'slug':'pockethost-test','owner':{'login':'owner'}}).encode())
  if path=='/app/installations/123':return io.BytesIO(json.dumps({'id':123,'app_id':self.install_app,'account':{'login':'owner','type':self.account_type},'repository_selection':'selected','permissions':self.install_permissions,'events':self.install_events,'suspended_at':'2026-01-01T00:00:00Z'if self.suspended else None}).encode())
  if path=='/app/installations/123/access_tokens':
   if self.denied_repository and json.loads(req.data).get('repository_ids'):raise urllib.error.HTTPError(req.full_url,404,'PRIVATE-GITHUB-ERROR',{},io.BytesIO(b'PRIVATE-GITHUB-ERROR'))
   return io.BytesIO(b'{"token":"SCOPED-INSTALLATION-TOKEN"}')
  if path=='/installation/repositories':
   per=int(q.get('per_page',['30'])[0]);page=int(q.get('page',['1'])[0]);return io.BytesIO(json.dumps({'total_count':self.total,'repositories':[self.repository(i)for i in range((page-1)*per+1,min(page*per,self.total)+1)]}).encode())
  if path=='/repositories/7':return io.BytesIO(b'{"full_name":"owner/project-0007"}')
  if path=='/repos/owner/project-0007/branches':
   per=int(q.get('per_page',['30'])[0]);page=int(q.get('page',['1'])[0]);return io.BytesIO(json.dumps([{'name':f'branch-{i}'}for i in range((page-1)*per+1,min(page*per,53)+1)]).encode())
  raise AssertionError(req.full_url)
 def run_reader(self,action,config=None,opener=None,**extra):
  file=self.folder/'payload.json';payload={'action':action,'config':{'githubAppId':'42','githubInstallationId':'123','privateKey':self.key.read_text()}if config is None else config,**extra};file.write_text(json.dumps(payload));file.chmod(0o600)
  output=io.StringIO();before=sys.argv;sys.argv=['reader',str(file)]
  try:
   with mock.patch.object(urllib.request,'urlopen',side_effect=opener or self.opener),mock.patch('sys.stdout',output):exec(compile(SCRIPT,'reader','exec'),{'__name__':'__main__'})
  finally:sys.argv=before;signal.alarm(0)
  self.assertFalse(file.exists());result=json.loads(output.getvalue());self.assertNotIn('SCOPED-INSTALLATION-TOKEN',json.dumps(result));self.assertNotIn('PRIVATE-GITHUB-ERROR',json.dumps(result));self.assertNotIn('BEGIN PRIVATE KEY',json.dumps(result));return result
 def test_repositories_second_page_is_installation_scoped(self):
  value=self.run_reader('repositories',page=2,perPage=50);self.assertEqual(value['page'],2);self.assertEqual(value['totalItems'],125);self.assertTrue(value['hasMore']);self.assertEqual([r['id']for r in value['items']],list(range(51,101)));self.assertEqual(value['searchScope'],'authorized-repositories')
  self.assertEqual(json.loads(self.requests[0].data),{'permissions':{'contents':'read','metadata':'read'}});self.assertIn('/installation/repositories?',self.requests[1].full_url);self.assertEqual(self.requests[1].headers['Authorization'],'Bearer SCOPED-INSTALLATION-TOKEN');self.assertEqual(self.requests[0].headers['Authorization'].count('.'),2)
 def test_last_page_bounds_and_unconfigured_inventory(self):
  value=self.run_reader('repositories',page=3,perPage=50);self.assertEqual(len(value['items']),25);self.assertFalse(value['hasMore']);self.requests=[]
  value=self.run_reader('repositories',page=-10,perPage=10000);self.assertEqual(value['page'],1);self.assertEqual(value['perPage'],100);self.assertEqual(len(value['items']),100);self.requests=[]
  value=self.run_reader('repositories',config={});self.assertFalse(value['configured']);self.assertEqual(value['items'],[]);self.assertEqual(self.requests,[])
 def test_search_reads_only_authorized_repository_pages(self):
  value=self.run_reader('repositories',search='PROJECT-0125',page=1,perPage=50);self.assertEqual([r['id']for r in value['items']],[125]);self.assertEqual(value['totalItems'],1);self.assertFalse(value['hasMore']);self.assertFalse(value['truncated']);self.assertEqual(value['searchScope'],'authorized-repositories');self.assertEqual(len(self.requests),3)
  self.assertFalse(any('/search/'in req.full_url for req in self.requests))
 def test_search_paginates_matches_and_marks_bounded_inventory(self):
  value=self.run_reader('repositories',search='project',page=3,perPage=50);self.assertEqual(len(value['items']),25);self.assertFalse(value['hasMore']);self.total=1200;self.requests=[]
  value=self.run_reader('repositories',search='project',page=20,perPage=50);self.assertTrue(value['truncated']);self.assertEqual(value['totalItems'],1000);self.assertEqual(len(value['items']),50);self.assertEqual(len(self.requests),11)
 def test_branches_use_repository_scoped_token_and_paginate(self):
  value=self.run_reader('branches',repositoryId='7',page=2,perPage=50);self.assertEqual(value['repository'],'owner/project-0007');self.assertEqual([r['name']for r in value['items']],['branch-51','branch-52','branch-53']);self.assertFalse(value['hasMore']);self.assertEqual(json.loads(self.requests[0].data),{'permissions':{'contents':'read','metadata':'read'},'repository_ids':[7]});self.assertEqual(urllib.parse.urlsplit(self.requests[1].full_url).path,'/repositories/7')
 def test_denied_repository_stops_before_metadata_or_branches(self):
  self.denied_repository=True;value=self.run_reader('branches',repositoryId='7',page=1);self.assertIn('error',value);self.assertEqual(len(self.requests),1)
  self.requests=[];value=self.run_reader('branches',config={},repositoryId='7');self.assertIn('error',value);self.assertEqual(self.requests,[])
 def test_health_verifies_app_installation_account_and_permissions(self):
  value=self.run_reader('health');self.assertTrue(value['configured']);self.assertTrue(value['ok']);self.assertEqual(value['app']['slug'],'pockethost-test');self.assertEqual(value['installation'],{'id':'123','account':'owner','repositorySelection':'selected'});self.assertEqual(value['permissions'],{'contents':'read','metadata':'read','pull_requests':'none'});self.assertFalse(value['previewSupported']);self.assertEqual(value['repositoryCount'],125);self.assertEqual(value['manageUrl'],'https://github.com/organizations/owner/settings/installations/123');self.assertEqual(len(self.requests),4)
 def test_health_reports_pull_request_preview_support_only_with_permission_and_event(self):
  self.install_permissions={'contents':'read','metadata':'read','pull_requests':'read'};value=self.run_reader('health');self.assertEqual(value['permissions']['pull_requests'],'read');self.assertFalse(value['previewSupported'])
  self.install_events=['push','pull_request'];value=self.run_reader('health');self.assertTrue(value['previewSupported'])
 def test_health_zero_repositories_suspension_and_account_management(self):
  self.total=0;value=self.run_reader('health');self.assertFalse(value['ok']);self.assertIn('Aucun dépôt',value['error']);self.requests=[];self.suspended=True;value=self.run_reader('health');self.assertFalse(value['ok']);self.assertIn('suspendue',value['error']);self.assertEqual(len(self.requests),2)
  self.suspended=False;self.account_type='User';self.total=1;value=self.run_reader('health');self.assertEqual(value['manageUrl'],'https://github.com/settings/installations/123')
 def test_health_rejects_mismatched_installation_and_never_echoes_network_errors(self):
  self.install_app='99';value=self.run_reader('health');self.assertIn('error',value);self.assertEqual(len(self.requests),2)
  def denied(req,timeout):raise urllib.error.HTTPError(req.full_url,403,'PRIVATE-GITHUB-ERROR',{},io.BytesIO(b'PRIVATE-GITHUB-ERROR'))
  self.assertIn('error',self.run_reader('health',opener=denied))
 def test_unconfigured_health_and_rate_limit_are_explicit(self):
  value=self.run_reader('health',config={});self.assertFalse(value['configured']);self.assertFalse(value['ok']);self.assertEqual(self.requests,[])
  def rate_limited(req,timeout):raise urllib.error.HTTPError(req.full_url,429,'PRIVATE-GITHUB-ERROR',{},io.BytesIO(b'PRIVATE-GITHUB-ERROR'))
  self.assertIn('limite de requêtes',self.run_reader('repositories',opener=rate_limited)['error'])
if __name__=='__main__':unittest.main()
