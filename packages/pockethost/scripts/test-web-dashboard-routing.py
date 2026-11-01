#!/usr/bin/env python3
"""Direct navigation/refresh of web hosting pages must return the SPA with HTTP200."""
import pathlib,tempfile,subprocess,urllib.request,urllib.error,socket,os,time,json
script=pathlib.Path(__file__).resolve().parents[2]/'dashboard/scripts/serve-static-build.cjs'
with tempfile.TemporaryDirectory(prefix='web-dashboard-route-')as directory:
 root=pathlib.Path(directory);(root/'404.html').write_text('SPA fallback');(root/'index.html').write_text('Home')
 with socket.socket()as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 process=subprocess.Popen(['node',str(script),str(root)],env={**os.environ,'HOST':'127.0.0.1','PORT':str(port)},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 try:
  for _ in range(50):
   try:urllib.request.urlopen(f'http://127.0.0.1:{port}/',timeout=1);break
   except OSError:time.sleep(.1)
  for path in ['/applications','/applications/settings','/applications/abcdefghijklmno','/instances/test','/account/security']:
   with urllib.request.urlopen(f'http://127.0.0.1:{port}'+path,timeout=3)as r:assert r.status==200 and r.read()==b'SPA fallback',path
  for step in ['manifest','install']:
   with urllib.request.urlopen(f'http://127.0.0.1:{port}/applications/settings/github/'+step+'?code=one-time-test&state=test-state',timeout=3)as r:
    assert r.status==200 and r.headers['Referrer-Policy']=='no-referrer' and r.headers['X-Frame-Options']=='DENY' and 'no-store' in r.headers['Cache-Control']
    assert b'one-time-test' not in r.read()
  try:urllib.request.urlopen(f'http://127.0.0.1:{port}/unrelated-file.js',timeout=3)
  except urllib.error.HTTPError as e:assert e.code==404
  else:raise AssertionError('Unknown files must remain404')
  print(json.dumps({'dashboardDirectRoutes':'passed','unknownAssetsRemain404':True}))
 finally:process.terminate();process.wait(timeout=5)
