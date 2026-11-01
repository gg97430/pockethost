#!/usr/bin/env python3
"""Focused tests for the optional APP4 BuildKit image pin; no engine/network."""
import importlib.util,json,os,pathlib,subprocess,sys,types,unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('worker_image_pin',ROOT/'web-app-worker.py');worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
IMAGE='moby/buildkit@sha256:'+'a'*64

class ImagePinTests(unittest.TestCase):
 def test_default_preserves_existing_driver_options(self):
  with mock.patch.dict(os.environ,{},clear=True):
   self.assertEqual(worker.builder_options(),{'memory':'3g','cpu-period':'100000','cpu-quota':'100000','network':worker.BUILDER_NETWORK})
 def test_only_official_digest_references_are_accepted(self):
  with mock.patch.dict(os.environ,{'WEB_APPS_BUILDKIT_IMAGE':IMAGE}):self.assertEqual(worker.builder_options()['image'],IMAGE)
  for image in ['moby/buildkit:buildx-stable-1','moby/buildkit@sha256:abc','other/buildkit@sha256:'+'a'*64,'moby/buildkit@sha256:'+'A'*64,IMAGE+';true']:
   with self.subTest(image=image),mock.patch.dict(os.environ,{'WEB_APPS_BUILDKIT_IMAGE':image}),self.assertRaises(RuntimeError):worker.builder_options()
 def assert_prerequisites(self,actual_image):
  calls=[]
  def docker(args,**kwargs):
   calls.append(args)
   if args[0]=='info':return json.dumps({'SecurityOptions':['name=rootless'],'CgroupVersion':'2','CgroupDriver':'systemd'})
   if args[:2]==['network','inspect']:return json.dumps([{'Driver':'bridge','Labels':{'pockethost.web.builder':'true'}}])
   if args[:2]==['buildx','inspect']and len(args)==3:raise subprocess.CalledProcessError(1,args)
   if args[:2]==['ps','-aq']:return'abc\n'
   if args[:2]==['image','inspect']:return json.dumps([{'Id':'sha256:'+'b'*64}])
   if args[0]=='inspect':return json.dumps([{'Image':actual_image,'HostConfig':{'Memory':3*1024**3,'CpuQuota':100000,'CpuPeriod':100000,'NetworkMode':worker.BUILDER_NETWORK},'Config':{'Cmd':worker.BUILDER_FLAGS}}])
   return''
  with mock.patch.dict(os.environ,{'WEB_APPS_BUILDKIT_IMAGE':IMAGE,'WEB_APPS_BUILD_CPUS':'1','WEB_APPS_BUILD_MEMORY':'3g'}),mock.patch.object(worker,'secure_builder_definition'),mock.patch.object(worker,'validate_socket'),mock.patch.object(worker,'docker',side_effect=docker),mock.patch.dict(sys.modules,{'web_app_railpack':types.SimpleNamespace(railpack_ready=lambda d:False)}):
   result=worker.prerequisites()
  return result,calls
 def test_pin_is_sent_to_driver_and_actual_image_is_verified(self):
  result,calls=self.assert_prerequisites('sha256:'+'b'*64);self.assertTrue(result)
  self.assertTrue(any(args[:2]==['buildx','create']and'image='+IMAGE in args for args in calls))
 def test_different_actual_image_refuses_readiness(self):
  with self.assertRaises(RuntimeError):self.assert_prerequisites('sha256:'+'c'*64)

if __name__=='__main__':unittest.main()
