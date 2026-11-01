#!/usr/bin/env python3
"""Exercise the actual bounded, read-only log reader with realistic PocketBase logs."""
import datetime,json,pathlib,re,sqlite3,subprocess,tempfile,unittest
SOURCE=pathlib.Path(__file__).resolve().parents[1]/'src/mothership-app/src/lib/handlers/instance/api/HandleInstancePerformance.ts'
SCRIPT=re.search(r'PERFORMANCE_READER = String.raw`(.*?)`',SOURCE.read_text(),re.S).group(1)
class PerformanceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);self.ident='a'*15;self.folder=self.root/'instances'/self.ident/'pb_data';self.folder.mkdir(parents=True);self.db=self.folder/'auxiliary.db'
 def tearDown(self):self.tmp.cleanup()
 def seed(self,rows):
  with sqlite3.connect(self.db) as db:
   db.execute('CREATE TABLE IF NOT EXISTS _logs (created TEXT,data JSON)');db.executemany('INSERT INTO _logs VALUES (?,?)',rows)
 def row(self,ms,url='/api/collections/clients/records/private-record?filter=email%3Dsecret@example.com',status=200,age=0):
  return ((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(hours=age)).isoformat(),json.dumps({'type':'request','method':'GET','url':url,'status':status,'execTime':ms,'userIP':'private-ip','token':'private-token'}))
 def read(self,hours=24,threshold=500):return json.loads(subprocess.check_output(['python3','-c',SCRIPT,str(self.root),self.ident,str(hours),str(threshold)],text=True))
 def test_metrics_privacy_and_time_filter(self):
  self.seed([self.row(100),self.row(700),self.row(900,status=500),self.row(5000,age=30),self.row(50000,url='/api/files/clients/record/private-name.pdf?token=secret')]);r=self.read();self.assertEqual(r['total'],4);self.assertEqual(r['slow'],3);self.assertEqual(r['errors'],1);g=next(g for g in r['groups'] if g['collection']=='clients');self.assertEqual(g['count'],3);self.assertEqual(g['p95Ms'],900);self.assertEqual(g['averageMs'],566.67)
  for secret in ['secret@example.com','private-record','private-ip','private-token','private-name.pdf','?token=']:self.assertNotIn(secret,json.dumps(r))
  self.assertEqual(self.read(hours=1,threshold=1000)['slow'],1)
 def test_empty_and_missing(self):
  self.assertFalse(self.read()['available']);self.seed([]);self.assertTrue(self.read()['available']);self.assertEqual(self.read()['total'],0)
 def test_row_limit_and_coverage(self):
  self.seed([self.row(1)]*50002);r=self.read();self.assertEqual(r['total'],50000);self.assertTrue(r['truncated'])
 def test_symlink_rejected(self):
  elsewhere=self.root/'other.db';elsewhere.write_text('');self.db.symlink_to(elsewhere);r=subprocess.run(['python3','-c',SCRIPT,str(self.root),self.ident,'24','500'],capture_output=True);self.assertNotEqual(r.returncode,0)
 def test_custom_paths_are_redacted(self):
  self.seed([self.row(1000,url='/api/customer/secret@example.com'),self.row(500,url='http://[broken')]);r=self.read();self.assertTrue(all(g['endpoint']=='/api/autres' for g in r['groups']))
if __name__=='__main__':unittest.main()
