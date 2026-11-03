"""Independent declarative check runner. No tenant code, Docker exec or filesystem input."""
import datetime,json,os,pathlib,re,signal,stat,subprocess,sys,tempfile,threading,time,urllib.request
from platform_maintenance import claim_response, status as maintenance_status


class Checks:
    def __init__(self,control,secret,root):
        if not re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{3,4}',control)or len(secret)<32:raise ValueError('Local check controller required')
        self.control,self.secret,self.root=control,secret,pathlib.Path(root);self.stop=threading.Event();self.process=None;self.ready=False
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def api(self,path,body):
        if path not in ('claim','report'):raise ValueError('Invalid check action')
        paused=claim_response(path,'checks')
        if paused is not None and body.get('cleanupOnly')is not True:return paused
        request=urllib.request.Request(self.control+'/api/web-apps/_checks/'+path,headers={'Content-Type':'application/json','X-Web-Worker':self.secret},data=json.dumps(body).encode())
        with self.opener.open(request,timeout=8)as response:
            raw=response.read(65537)
            if len(raw)>65536:raise ValueError('Check response too large')
            return json.loads(raw)
    def publish(self,run=''):
        value={'ready':self.ready,'runId':run,'checkedAt':datetime.datetime.now(datetime.timezone.utc).isoformat()}
        fd,name=tempfile.mkstemp(prefix='.checks-status-',dir=self.root)
        try:
            with os.fdopen(fd,'w')as f:os.fchmod(f.fileno(),0o640);json.dump(value,f)
            os.replace(name,self.root/'checks-status.json')
        finally:
            if os.path.exists(name):os.unlink(name)
    def execute(self,job):
        if (not isinstance(job,dict)or not re.fullmatch(r'[a-z0-9]{15}',job.get('id',''))or not re.fullmatch(r'[A-Za-z0-9]{80}',job.get('proof',''))or
            not isinstance(job.get('input'),dict)or len(json.dumps(job['input']))>65536):raise ValueError('Invalid check lease')
        script=pathlib.Path(__file__).resolve().with_name('web_app_checks_runner.py');info=script.lstat()
        if not stat.S_ISREG(info.st_mode)or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Trusted read-only runner required')
        started=time.monotonic()
        with tempfile.TemporaryFile()as output:
            process=subprocess.Popen([sys.executable,'-I',str(script)],stdin=subprocess.PIPE,stdout=output,stderr=subprocess.DEVNULL,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},start_new_session=True)
            self.process=process
            try:
                try:process.communicate(json.dumps(job['input']).encode(),timeout=60)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=3)
                    return {'status':'failed','errorCode':'timeout','steps':[],'durationMs':60000}
                output.seek(0);raw=output.read(30001)
                if process.returncode or len(raw)>30000:raise ValueError('Invalid check output')
                result=json.loads(raw)
                if not isinstance(result,dict)or result.get('status')not in ('passed','failed'):raise ValueError('Invalid check result')
                return result
            finally:
                if process.poll()is None:
                    os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=3)
                self.process=None
    def loop(self):
        while not self.stop.is_set():
            run=''
            try:
                self.publish()
                value=self.api('claim',{'cleanupOnly':maintenance_status()['paused']})
                self.ready=True
                job=value.get('job')
                if job:
                    run=job['id'];self.publish(run)
                    try:result=self.execute(job)
                    except Exception:result={'status':'failed','errorCode':'configuration','steps':[],'durationMs':0}
                    for attempt in range(3):
                        try:self.api('report',{'id':job['id'],'proof':job['proof'],'result':result});break
                        except Exception:
                            if attempt==2:raise
                            self.stop.wait(1)
            except Exception:self.ready=False
            finally:
                try:self.publish()
                except Exception:pass
            self.stop.wait(3)


def start_check_runner(control,secret,root):
    runner=Checks(control,secret,root);thread=threading.Thread(target=runner.loop,name='web-read-only-checks',daemon=True);thread.start();return thread
