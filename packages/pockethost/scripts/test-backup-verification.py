#!/usr/bin/env python3
"""Exercise the embedded production verifier against disposable valid and hostile archives."""
import hashlib,io,json,pathlib,re,sqlite3,subprocess,sys,tarfile,tempfile,unittest,zipfile
CODE=re.search(r'BACKUP_VERIFIER = String.raw`(.*?)`', (pathlib.Path(__file__).parents[1]/'src/mothership-app/src/lib/handlers/instance/api/backupVerification.ts').read_text(),re.S)[1]
class VerificationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name)
  self.db=self.root/'data.db'
  with sqlite3.connect(self.db) as db:db.execute('CREATE TABLE _collections (id TEXT)');db.execute("INSERT INTO _collections VALUES ('safe')")
  self.before=self.db.read_bytes()
 def tearDown(self):self.tmp.cleanup()
 def archive(self,entries=None):
  path=self.root/'backup.tar.gz'
  with tarfile.open(path,'w:gz') as t:
   t.add(self.db,arcname='pb_data/data.db')
   for name,data in entries or [('pb_hooks/must-not-execute.pb.js',b'throw new Error("private-hook-secret")')]:
    entry=tarfile.TarInfo(name);entry.size=len(data);t.addfile(entry,io.BytesIO(data))
  return path
 def run_check(self,path,checksum=None):
  checksum=hashlib.sha256(path.read_bytes()).hexdigest() if checksum is None else checksum
  result=json.loads(subprocess.check_output([sys.executable,'-c',CODE,str(path),checksum,str(self.root/'progress.json'),str(self.root/'workspace')],text=True))
  self.assertEqual(self.db.read_bytes(),self.before);self.assertEqual(list((self.root/'workspace').glob('restore-check-*')),[])
  self.assertNotIn('private-hook-secret',json.dumps(result));return result
 def test_restore_and_sqlite_integrity(self):
  result=self.run_check(self.archive());self.assertTrue(result['ok']);self.assertTrue(result['databaseIntegrity']);self.assertTrue(result['checksumVerified']);self.assertEqual(result['files'],2)
 def test_sha256_mismatch(self):self.assertFalse(self.run_check(self.archive(),'0'*64)['ok'])
 def test_path_traversal(self):self.assertFalse(self.run_check(self.archive([('../escape',b'x')]))['ok']);self.assertFalse((self.root/'escape').exists())
 def test_symlink_rejected(self):
  path=self.archive()
  with tarfile.open(self.root/'symlink.tar.gz','w:gz') as t:
   entry=tarfile.TarInfo('pb_data/data.db');entry.type=tarfile.SYMTYPE;entry.linkname='/etc/passwd';t.addfile(entry)
  self.assertFalse(self.run_check(self.root/'symlink.tar.gz')['ok'])
 def test_corrupt_sqlite(self):
  path=self.root/'corrupt.tar.gz'
  with tarfile.open(path,'w:gz') as t:
   entry=tarfile.TarInfo('pb_data/data.db');entry.size=6;t.addfile(entry,io.BytesIO(b'broken'))
  self.assertFalse(self.run_check(path)['ok'])
 def test_corrupt_gzip_footer_without_recorded_checksum(self):
  path=self.archive();raw=bytearray(path.read_bytes());raw[-8]^=1;path.write_bytes(raw)
  self.assertFalse(self.run_check(path,'')['ok'])
 def test_zip_supported(self):
  path=self.root/'backup.zip'
  with zipfile.ZipFile(path,'w') as z:z.write(self.db,'pb_data/data.db');z.writestr('pb_public/test.txt','safe')
  self.assertTrue(self.run_check(path)['ok'])
 def test_no_original_checksum_is_explicit(self):self.assertFalse(self.run_check(self.archive(),'')['checksumVerified'])
if __name__=='__main__':unittest.main()
