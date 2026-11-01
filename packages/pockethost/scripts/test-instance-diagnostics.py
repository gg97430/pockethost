#!/usr/bin/env python3
"""Actual read-only diagnostic reader, tested on disposable SQLite fixtures."""
import datetime,hashlib,json,pathlib,re,sqlite3,subprocess,tempfile,unittest,urllib.parse
SOURCE=pathlib.Path(__file__).resolve().parents[1]/'src/mothership-app/src/lib/handlers/instance/api/HandleInstanceDiagnostics.ts'
SCRIPT=re.search(r'DIAGNOSTIC_READER = String.raw`(.*?)`',SOURCE.read_text(),re.S).group(1)
class DiagnosticsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);self.ident='a'*15;self.folder=self.root/'instances'/self.ident/'pb_data';self.folder.mkdir(parents=True);self.db=self.folder/'data.db'
  with sqlite3.connect(self.db) as db:
   db.execute('CREATE TABLE _collections (id TEXT,name TEXT,type TEXT,fields JSON)')
   db.execute('INSERT INTO _collections VALUES (?,?,?,?)',('collectionid','clients','base',json.dumps([{'name':x,'type':'text'} for x in ['id','agence','statut','created','email']])))
   db.execute('CREATE TABLE clients (id TEXT PRIMARY KEY,agence TEXT,statut TEXT,created TEXT,email TEXT)')
   db.executemany('INSERT INTO clients VALUES (?,?,?,?,?)',[(str(i),'agency-'+str(i%10),'active','2026-01-01','private@example.com') for i in range(100)])
  with sqlite3.connect(self.folder/'auxiliary.db') as db:db.execute('CREATE TABLE _logs (created TEXT,data JSON)')
 def tearDown(self):self.tmp.cleanup()
 def log(self,filter='agence="secret-agency" && statut="secret-status"',sort='-created',method='GET',status=200,**params):
  query=urllib.parse.urlencode({'filter':filter,'sort':sort,**params})
  data={'type':'request','method':method,'url':'/api/collections/clients/records?'+query,'execTime':900,'status':status,'userIP':'private-ip'}
  with sqlite3.connect(self.folder/'auxiliary.db') as db:db.execute('INSERT INTO _logs VALUES (?,?)',(datetime.datetime.now(datetime.timezone.utc).isoformat(),json.dumps(data)))
 def read(self,collection=''):
  return json.loads(subprocess.check_output(['python3','-c',SCRIPT,str(self.root),self.ident,'24','500',collection],text=True))
 def test_candidate_has_plan_evidence_no_writes_or_values(self):
  self.log();before=hashlib.sha256(self.db.read_bytes()).hexdigest();r=self.read();p=r['patterns'][0]
  self.assertIsNotNone(p['proposal']);self.assertTrue(any('SCAN' in x for x in p['beforePlan']));self.assertTrue(any('SEARCH' in x for x in p['afterPlan']))
  self.assertEqual([x['name'] for x in p['proposal']['columns']],['agence','statut','created'])
  self.assertEqual(before,hashlib.sha256(self.db.read_bytes()).hexdigest())
  for secret in ['secret-agency','secret-status','private@example.com','private-ip']:self.assertNotIn(secret,json.dumps(r))
  with sqlite3.connect(self.db) as db:self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE type='index' AND name LIKE 'ph_diag_%'").fetchone()[0],0)
 def test_existing_index_prevents_duplicate_proposal(self):
  with sqlite3.connect(self.db) as db:db.execute('CREATE INDEX existing ON clients(agence,statut,created DESC)')
  self.log();r=self.read();self.assertIsNone(r['patterns'][0]['proposal']);self.assertEqual(len(r['collections'][0]['indexes']),2)
 def test_complex_filters_are_not_translated_or_executed(self):
  for value in ['agence="x" || statut="y"','agence.name="x"','email~"secret"','agence="x"; DROP TABLE clients','agence=null']:
   self.log(filter=value)
  r=self.read();self.assertTrue(all(not p['supported'] and p['proposal'] is None for p in r['patterns']))
  with sqlite3.connect(self.db) as db:self.assertEqual(db.execute('SELECT count(*) FROM clients').fetchone()[0],100)
 def test_literal_escapes_parentheses_and_selectors(self):
  self.log(filter='(agence="a && b" && statut="safe")',perPage='1000',expand='agency')
  r=self.read('collectionid');self.assertTrue(r['patterns'][0]['supported']);self.assertEqual(r['collections'][0]['usage']['largePages'],1)
  self.assertEqual(r['selectedCollection'],'clients');self.assertFalse(self.read('absent')['collections'])
 def test_api_usage_and_hook_hints(self):
  for _ in range(3):self.log(perPage='500',expand='agency')
  self.log(method='PATCH');hooks=self.folder.parent/'pb_hooks';hooks.mkdir();(hooks/'business.pb.js').write_text('const collection = "clients"; const password="private-hook-secret";')
  r=self.read();u=r['collections'][0]['usage'];self.assertEqual(u['withoutFields'],3);self.assertEqual(u['withTotal'],3);self.assertEqual(u['writes'],1);self.assertGreaterEqual(u['repeatedWithinSecond'],1)
  self.assertEqual(r['collections'][0]['hookFiles'],['business.pb.js']);self.assertNotIn('private-hook-secret',json.dumps(r))
 def test_multivalue_relation_is_not_misrepresented(self):
  with sqlite3.connect(self.db) as db:db.execute('UPDATE _collections SET fields=?',(json.dumps([{'name':'agence','type':'relation','maxSelect':5,'options':None}]),))
  self.log();r=self.read();self.assertFalse(r['patterns'][0]['supported']);self.assertIsNone(r['patterns'][0]['proposal'])
 def test_unbounded_multiselect_is_manual(self):
  with sqlite3.connect(self.db) as db:db.execute('UPDATE _collections SET fields=?',(json.dumps([{'name':'agence','type':'select','maxSelect':0}]),))
  self.log();self.assertFalse(self.read()['patterns'][0]['supported'])
 def test_empty_logs_and_database_missing(self):
  self.assertTrue(self.read()['available']);self.assertEqual(self.read()['patterns'],[]);self.db.unlink();self.assertFalse(self.read()['available'])
 def test_symlinks_rejected(self):
  other=self.folder/'other';self.db.rename(other);self.db.symlink_to(other)
  r=subprocess.run(['python3','-c',SCRIPT,str(self.root),self.ident,'24','500',''],capture_output=True);self.assertNotEqual(r.returncode,0)
if __name__=='__main__':unittest.main()
