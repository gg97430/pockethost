#!/usr/bin/env python3
"""Intégration Railpack FACULTATIVE (ignorée par défaut) : RAILPACK_INTEGRATION=1, Docker + buildx locaux requis.

RAILPACK_BIN / RAILPACK_MISE : binaires Linux de l'architecture du moteur Docker (contrôle d'empreinte désactivé
UNIQUEMENT ici). DOCKER_SOCKET : socket du moteur local (défaut ~/.docker/run/docker.sock, puis /var/run/docker.sock).
Tranche les points [À VÉRIFIER] : format de info.json, BUILDKIT_SYNTAX épinglé par digest, UID 10001 + racine en lecture seule.
"""
import http.client,importlib.util,json,os,pathlib,socket,subprocess,sys,tempfile,time,unittest,unittest.mock as mock,urllib.parse,urllib.request
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import web_app_railpack as rp
import web_app_runtime as runtime

def candidates():
    return [os.environ.get('DOCKER_SOCKET',''),str(pathlib.Path.home()/'.docker/run/docker.sock'),'/var/run/docker.sock']
SOCKET=next((path for path in candidates() if path and pathlib.Path(path).is_socket()),'')
def available():
    if os.environ.get('RAILPACK_INTEGRATION')!='1' or not SOCKET or not os.environ.get('RAILPACK_BIN') or not os.environ.get('RAILPACK_MISE'):return False
    try:
        for args in (['info'],['buildx','version']):subprocess.run(['docker','--host','unix://'+SOCKET,*args],check=True,capture_output=True,timeout=30)
        return True
    except (OSError,subprocess.SubprocessError):return False

PROJECTS={
 'static':{'index.html':'<!doctype html><title>ok</title><p>railpack static</p>'},
 'vite':{'package.json':json.dumps({'name':'v','private':True,'type':'module','scripts':{'build':'vite build'},'devDependencies':{'vite':'5.4.11'}}),'index.html':'<!doctype html><div id="app">vite</div><script type="module" src="/main.js"></script>','main.js':'document.getElementById("app").textContent="vite ok"'},
 'python':{'requirements.txt':'uvicorn==0.32.1\n','main.py':'import os,uvicorn\nasync def app(scope,receive,send):\n    await send({"type":"http.response.start","status":200,"headers":[(b"content-type",b"text/plain")]})\n    await send({"type":"http.response.body","body":b"python ok"})\nif __name__=="__main__":uvicorn.run(app,host="0.0.0.0",port=int(os.environ.get("PORT","3000")))\n'},
}


@unittest.skipUnless(available(),'RAILPACK_INTEGRATION=1, RAILPACK_BIN, RAILPACK_MISE et un Docker local avec buildx sont requis.')
class RailpackIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('worker_integration',pathlib.Path(__file__).with_name('web-app-worker.py'));cls.w=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.w)
        try:cls.docker(['buildx','inspect','ph-web-builder'])
        except subprocess.CalledProcessError:cls.docker(['buildx','create','--name','ph-web-builder','--driver','docker-container','--driver-opt','network=bridge'],timeout=120)
    @staticmethod
    def docker(args,**kwargs):
        return subprocess.check_output(['docker','--host','unix://'+SOCKET,*args],text=True,stderr=subprocess.DEVNULL,timeout=kwargs.pop('timeout',120),**kwargs)
    @staticmethod
    def request(method,path,body=None):
        class Unix(http.client.HTTPConnection):
            def connect(self):self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.connect(SOCKET)
        connection=Unix('localhost',timeout=60)
        try:
            connection.request(method,'/v1.47'+path,body=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
            response=connection.getresponse();raw=response.read()
            if response.status>=300:raise RuntimeError(path+': '+raw.decode(errors='replace'))
            return json.loads(raw) if raw else {}
        finally:connection.close()

    def build_and_serve(self,name):
        w=self.w
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder).resolve();w.ROOT=root;source=root/'source';source.mkdir()
            for filename,content in PROJECTS[name].items():(source/filename).write_text(content)
            app=('i'+name+'x'*15)[:15];job=w.Job({'id':'j'*15,'appId':app,'action':'deploy','commit':'c'*40,'snapshot':{'config':{'rootDirectory':'.'},'env':{},'runtime':{'kind':'auto'}}})
            engine=runtime.RuntimeEngine(job,self.docker,self.request,root,SOCKET);engine.hmac_key='k'*40
            pinned={**rp.RAILPACK,'binary':os.environ['RAILPACK_BIN'],'miseBinary':os.environ['RAILPACK_MISE']}
            with mock.patch.object(rp,'RAILPACK',pinned),mock.patch.object(rp,'tools_ready',return_value=True),mock.patch.object(w,'resources'),mock.patch.object(w,'check_runtime_budgets'),mock.patch.object(job,'heartbeat',return_value={}):
                image=engine.build(source,root)
            journal=job.logpath.read_text();print('\n--- '+name+' ---\n'+journal[-3000:],flush=True)
            self.assertIn('fournisseur détecté',journal);self.assertRegex(image,'^sha256:[a-f0-9]{64}$')
            info=json.loads(self.docker(['image','inspect',image]))[0]['Config'];self.assertEqual(info['User'],'10001:10001')
            container=self.docker(['run','-d','--user','10001:10001','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=128m,uid=10001,gid=10001','--cap-drop','ALL','--security-opt','no-new-privileges',
                '-e','PORT=3000','-e','HOME=/tmp','-e','XDG_CONFIG_HOME=/tmp/.config','-e','XDG_DATA_HOME=/tmp/.local/share','-e','XDG_CACHE_HOME=/tmp/.cache','-p','127.0.0.1::3000',image]).strip()
            try:
                port=self.docker(['port',container,'3000/tcp']).split(':')[-1].strip();deadline=time.monotonic()+90;status=None
                while time.monotonic()<deadline:
                    try:
                        with urllib.request.urlopen('http://127.0.0.1:'+port+'/',timeout=5) as response:status=response.status;break
                    except Exception:time.sleep(2)
                if status!=200:print(self.docker(['logs',container],stderr=subprocess.STDOUT)[-3000:],flush=True)
                self.assertEqual(status,200)
            finally:
                subprocess.run(['docker','--host','unix://'+SOCKET,'rm','-f',container],capture_output=True)
                subprocess.run(['docker','--host','unix://'+SOCKET,'image','rm','-f','ph-web/'+app+':'+'j'*15],capture_output=True)

    def test_static_site(self):self.build_and_serve('static')
    def test_vite_without_start_script(self):self.build_and_serve('vite')
    def test_python_uvicorn(self):self.build_and_serve('python')


if __name__=='__main__':unittest.main()
