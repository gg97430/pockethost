#!/usr/bin/env python3
import importlib.util,pathlib,unittest,tempfile,os,stat,types
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('installer',pathlib.Path(__file__).with_name('install-project-recovery-engine.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class SubordinateTests(unittest.TestCase):
 def test_existing_ranges_preserved_new_range_aligned_and_disjoint(self):
  rows=module.subids('ubuntu:100000:65536\npockethost-web:165536:65536\n');start=module.next_range(rows,rows)
  self.assertEqual(start%65536,0);self.assertGreaterEqual(start,231072);self.assertEqual(rows[0],('ubuntu',100000,65536))
  self.assertEqual(module.subids('x:1048576:65536\ny:1114112:65536'),[('x',1048576,65536),('y',1114112,65536)])
 def test_overlap_malformed_overflow_and_real_system_uid_ranges_refused(self):
  for value in ['x:100000:65536\ny:110000:65536','x:1000:65536','x:4294967295:2','x:1:0','x:not-a-number:65536']:
   with self.assertRaises(ValueError):module.subids(value)
 def test_empty_files_have_safe_non_system_range(self):
  self.assertEqual(module.subids(''),[]);self.assertGreaterEqual(module.next_range([],[]),1048576)
 def test_rootless_setup_uses_existing_usr_local_entry_instead_of_missing_usr_bin(self):
  with tempfile.TemporaryDirectory()as temporary:
   root=pathlib.Path(temporary);package=root/'package';package.mkdir();target=package/'dockerd-rootless-setuptool.sh';target.write_text('#!/bin/sh\nexit 0\n');target.chmod(0o755)
   usr=root/'usr-bin';local=root/'usr-local-bin';usr.mkdir();local.mkdir();(local/target.name).symlink_to(target)
   with patch.object(module,'protected_executable',side_effect=lambda path:str(path))as checked:
    self.assertEqual(module.system_executable(target.name,(str(usr),str(local))),str(local/target.name))
   checked.assert_called_once_with(local/target.name)
 def test_executable_resolution_ignores_inherited_path_and_rejects_root_chain_writes(self):
  with tempfile.TemporaryDirectory()as temporary:
   root=pathlib.Path(temporary);tool=root/'dockerd-rootless-setuptool.sh';tool.write_text('fixture');tool.chmod(0o755)
   with patch.dict(os.environ,{'PATH':str(root)}):
    with self.assertRaises(ValueError):module.system_executable(tool.name,())
   real_stat=pathlib.Path.stat
   def unsafe_parent(path,*args,**kwargs):
    value=real_stat(path,*args,**kwargs)
    return types.SimpleNamespace(st_uid=0,st_mode=(stat.S_IFDIR|0o777)if path==root else value.st_mode)
   with patch.object(pathlib.Path,'stat',unsafe_parent),patch.object(pathlib.Path,'lstat',return_value=types.SimpleNamespace(st_uid=0)):
    with self.assertRaisesRegex(ValueError,'writable outside root'):module.protected_executable(tool)
 def test_untrusted_executable_owner_and_names_are_rejected(self):
  with patch.object(pathlib.Path,'lstat',return_value=types.SimpleNamespace(st_uid=1001)):
   with self.assertRaisesRegex(ValueError,'belong to root'):module.protected_executable('/usr/bin/fixture')
  for name in ('../docker','/usr/bin/docker','docker;id'):
   with self.assertRaises(ValueError):module.system_executable(name)
if __name__=='__main__':unittest.main()
