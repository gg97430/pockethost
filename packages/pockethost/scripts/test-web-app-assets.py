#!/usr/bin/env python3
import io,pathlib,subprocess,sys,tarfile,tempfile,time,types,unittest
from unittest import mock
import web_app_assets as a

class AssetsTests(unittest.TestCase):
 def setUp(self):
  self.disk=mock.patch.object(a.shutil,'disk_usage',return_value=types.SimpleNamespace(free=64*1024**3));self.disk.start();self.addCleanup(self.disk.stop)
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=pathlib.Path(self.temp.name);self.folder=self.root/'export';self.folder.mkdir()
 def archive(self,items):
  out=io.BytesIO()
  with tarfile.open(fileobj=out,mode='w')as tar:
   for name,payload,kind in items:
    info=tarfile.TarInfo(name);info.type=kind;info.size=len(payload);info.linkname='../../outside';tar.addfile(info,io.BytesIO(payload))
  return out.getvalue()
 def extract(self,raw):a.extract_assets(a.AssetReader(io.BytesIO(raw),self.root,lambda:None),self.folder)
 def test_real_next_paths_and_pax_long_names_are_preserved(self):
  name='static/chunks/'+('a'*150)+'.js';self.extract(self.archive([(name,b'compiled',tarfile.REGTYPE)]));self.assertEqual((self.folder/name.removeprefix('static/')).read_bytes(),b'compiled')
 def test_total_size_and_header_size_fail_before_writing_beyond_limit(self):
  raw=self.archive([('static/a',b'abc',tarfile.REGTYPE),('static/b',b'def',tarfile.REGTYPE)])
  with mock.patch.object(a,'MAX_BYTES',5),self.assertRaisesRegex(RuntimeError,'256 Mio'):self.extract(raw)
  self.assertFalse((self.folder/'b').exists());self.assertEqual((self.folder/'a').read_bytes(),b'abc')
  header=tarfile.TarInfo('static/huge');header.size=a.MAX_BYTES+1
  with self.assertRaisesRegex(RuntimeError,'256 Mio'):self.extract(header.tobuf())
 def test_entry_ceiling_counts_empty_files(self):
  raw=self.archive([('static/'+str(i),b'',tarfile.REGTYPE)for i in range(4)])
  with mock.patch.object(a,'MAX_ENTRIES',3),self.assertRaisesRegex(RuntimeError,'entrées'):self.extract(raw)
  self.assertEqual(len(list(self.folder.iterdir())),3)
 def test_links_sparse_devices_duplicates_and_traversal_are_refused(self):
  for name,kind in [('static/../outside',tarfile.REGTYPE),('/outside',tarfile.REGTYPE),('static/link',tarfile.SYMTYPE),('static/hard',tarfile.LNKTYPE),('static/dev',tarfile.CHRTYPE),('static/sparse',tarfile.GNUTYPE_SPARSE)]:
   with self.subTest(name=name,kind=kind),self.assertRaises(RuntimeError):self.extract(self.archive([(name,b'',kind)]))
  with self.assertRaises(FileExistsError):self.extract(self.archive([('static/a',b'a',tarfile.REGTYPE),('static/a',b'b',tarfile.REGTYPE)]))
  self.assertEqual((self.folder/'a').read_bytes(),b'a');self.assertFalse((self.root/'outside').exists())
 def test_oversized_extended_header_is_refused_before_payload_read(self):
  info=tarfile.TarInfo('pax');info.type=tarfile.XHDTYPE;info.size=a.MAX_METADATA+1
  with self.assertRaisesRegex(RuntimeError,'Métadonnées'):self.extract(info.tobuf())
 def test_truncated_archive_and_disk_reserve_fail(self):
  info=tarfile.TarInfo('static/a');info.size=50000
  with self.assertRaises(tarfile.TarError):self.extract(info.tobuf()+b'abc')
  with mock.patch.object(a.shutil,'disk_usage',return_value=type('Disk',(),{'free':a.MIN_FREE-1})()),self.assertRaisesRegex(RuntimeError,'15 Gio'):self.extract(b'')
 def test_pipe_without_output_times_out_and_keeps_heartbeat_cancellable(self):
  process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],stdout=subprocess.PIPE)
  try:
   beats=[];reader=a.AssetReader(process.stdout,self.root,lambda:beats.append(1));started=time.monotonic()
   with mock.patch.object(a,'MAX_SECONDS',.15),self.assertRaisesRegex(RuntimeError,'délai maximal'):a.extract_assets(reader,self.folder)
   self.assertLess(time.monotonic()-started,2);self.assertGreaterEqual(len(beats),2)
   def cancel():raise RuntimeError('cancelled')
   with self.assertRaisesRegex(RuntimeError,'cancelled'):a.extract_assets(a.AssetReader(process.stdout,self.root,cancel),self.folder)
  finally:process.kill();process.wait();process.stdout.close()

if __name__=='__main__':unittest.main()
