#!/usr/bin/env python3
"""Installer-owned root service for selected, non-disruptive project rehearsals.

Source roots and recovery key come from one root-owned 0600 configuration file.
Browser/controller jobs provide IDs and sealed snapshots only, never filesystem
paths, Docker sockets, executables or commands for the host. The service never
publishes imported applications or changes DNS. All tenant execution is rootless.
"""
import contextlib
import fcntl
import json
import os
import pathlib
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import web_project_recovery as engine
from platform_maintenance import status as maintenance_status

CONFIG = pathlib.Path('/etc/kolab/project-recovery.json')
STOP = False
LAST_API_AT = 0.0


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs): return None


def config_file(path=CONFIG):
    path = engine.recovery.private_file(path)
    if path.stat().st_uid != 0: raise engine.ProjectRecoveryError('Configuration root requise.')
    value = json.loads(path.read_text())
    if set(value) != {'version', 'dataRoot', 'binaryRoot', 'stateRoot', 'keyFile', 'sourceDockerSocket', 'sandboxDockerSocket', 'helperImage', 'instanceAppRoot', 'controlUrl'} or value['version'] != 1:
        raise engine.ProjectRecoveryError('Configuration de service invalide.')
    for name in ('dataRoot', 'binaryRoot', 'stateRoot', 'instanceAppRoot'):
        engine.canonical(value[name], True)
    engine.recovery.load_key(value['keyFile'])
    if not engine.IMAGE.fullmatch(value['helperImage']): raise engine.ProjectRecoveryError('Image approuvée requise.')
    if value['sourceDockerSocket'] != '/run/user/1001/docker.sock' or value['sandboxDockerSocket'] != '/run/user/1002/docker.sock': raise engine.ProjectRecoveryError('Moteurs source et copie distincts requis.')
    engine.Docker(value['sourceDockerSocket']); engine.Docker(value['sandboxDockerSocket'])
    for name, uid in [('sourceDockerSocket',1001),('sandboxDockerSocket',1002)]:
        filename=pathlib.Path(value[name]); info=filename.stat(); parent=filename.parent.stat()
        if filename.resolve()!=filename or info.st_uid!=uid or parent.st_uid!=uid or parent.st_mode&0o077: raise engine.ProjectRecoveryError('Socket rootless privé non confirmé.')
    if value['controlUrl']!='http://127.0.0.1:8091': raise engine.ProjectRecoveryError('Contrôleur de reprise local requis.')
    state = pathlib.Path(value['stateRoot'])
    if state.stat().st_uid != 0 or state.stat().st_mode & 0o077: raise engine.ProjectRecoveryError('État root privé requis.')
    if state == pathlib.Path(value['dataRoot']) or pathlib.Path(value['dataRoot']) in state.parents: raise engine.ProjectRecoveryError('État des essais hors des données sources requis.')
    return value


def api(path, body):
    global LAST_API_AT
    control = 'http://127.0.0.1:8091'
    key = pathlib.Path('/etc/kolab/project-recovery-controller.key')
    descriptor = os.open(key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor) as stream:
        info = os.fstat(stream.fileno())
        if info.st_uid != 0 or info.st_mode & 0o777 != 0o640 or info.st_size > 128: raise engine.ProjectRecoveryError('Clé de service root requise.')
        secret = stream.read(129).strip()
    if not re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{3,4}', control) or not re.fullmatch(r'[a-f0-9]{64}', secret):
        raise engine.ProjectRecoveryError('Contrôleur local privé requis.')
    request = urllib.request.Request(control + '/api/web-project-recovery/_worker/' + path, data=json.dumps(body).encode(), headers={'X-Project-Recovery': secret, 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=15) as response:
        raw = response.read(4 * 1024**2 + 1)
        if len(raw) > 4 * 1024**2: raise engine.ProjectRecoveryError('Snapshot trop volumineux.')
        value=json.loads(raw);LAST_API_AT=time.monotonic();return value


def status(config, value):
    engine.private_json(pathlib.Path(config['stateRoot']) / 'status.json', {**value, 'checkedAt': engine.recovery.stamp()})


def publish_health(config, stopped=False):
    ready, denied = False, False
    try:
        socket_path=pathlib.Path(config['sandboxDockerSocket']);socket_info=socket_path.stat();parent=socket_path.parent.stat()
        denied=socket_info.st_uid==1002 and parent.st_uid==1002 and parent.st_mode&0o077==0
        meta=engine.Docker(config['sandboxDockerSocket']).info()
        ready=denied and any('rootless'in value for value in meta.get('SecurityOptions',[])) and meta.get('CgroupVersion')=='2' and meta.get('CgroupDriver')=='systemd'
    except Exception:pass
    blocked=(pathlib.Path(config['stateRoot'])/'cleanup-pending.json').exists()
    report={'checkedAt':engine.recovery.stamp(),'ready':not stopped and ready and not blocked and 0<=time.monotonic()-LAST_API_AT<30,'blocked':blocked,'sandboxReady':ready,'hostingUid1001Denied':denied}
    directory=pathlib.Path('/run/kolab-project-recovery');directory.mkdir(mode=0o755,exist_ok=True)
    if directory.resolve()!=directory or directory.stat().st_uid!=0 or directory.stat().st_mode&0o022:raise engine.ProjectRecoveryError('Répertoire de santé root non confirmé.')
    target=directory/'health.json';temporary=directory/('health-'+os.urandom(6).hex()+'.tmp')
    with open(temporary,'x')as stream:
        os.fchmod(stream.fileno(),0o640);os.fchown(stream.fileno(),0,1000);json.dump(report,stream);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,target)


def process(config, job):
    if not isinstance(job, dict) or set(job) != {'id', 'lease', 'snapshot'} or not engine.ID.fullmatch(job['id']) or not isinstance(job['lease'], str) or len(job['lease']) != 80:
        raise engine.ProjectRecoveryError('Bail de reprise invalide.')
    engine.validate_snapshot(job['snapshot'])
    root = pathlib.Path(config['stateRoot']); directory = root / job['id']
    if directory.exists() or directory.resolve() != directory: raise engine.ProjectRecoveryError('Une copie de cet essai existe déjà.')
    directory.mkdir(mode=0o700)
    proof = {'id': job['id'], 'lease': job['lease']}
    result, error, response_status = {}, '', 'failed'
    source_docker = engine.Docker(config['sourceDockerSocket'])
    sandbox_docker = engine.Docker(config['sandboxDockerSocket'])
    heartbeat_stop = threading.Event()
    def heartbeat():
        while not heartbeat_stop.wait(5):
            try:
                api('claim', {'cleanupOnly': True})
                current = json.loads((root / 'status.json').read_text())
                api('report', {**proof, 'status': 'running', 'stage': current.get('stage', 'capture')})
            except Exception: pass
    thread = threading.Thread(target=heartbeat, name='project-recovery-heartbeat', daemon=True); thread.start()
    try:
        status(config, {'runId': job['id'], 'stage': 'capture'})
        capsule = directory / 'project.phpr'
        captured = engine.capture(job['snapshot'], config['dataRoot'], config['binaryRoot'], capsule, config['keyFile'], source_docker, config['instanceAppRoot'])
        status(config, {'runId': job['id'], 'stage': 'restore'})
        engine.restore(capsule, config['keyFile'], directory / 'restored', captured['sha256'])
        status(config, {'runId': job['id'], 'stage': 'probe'})
        probe = engine.sandbox_drill(directory / 'restored', sandbox_docker, config['helperImage'])
        result = {**captured, **probe}
        response_status = 'ready' if probe['runtimeStarted'] else 'partial'
    except Exception:
        # No stderr, body, environment values or file paths enter the API.
        error = 'project_recovery_failed'
    finally:
        try:
            if directory.resolve() != directory or directory.parent != root: raise ValueError()
            journal = directory / 'restored/sandbox-state.json'
            if journal.exists():
                engine.cleanup_sandbox(json.loads(engine.recovery.private_file(journal).read_text()), sandbox_docker)
                journal.unlink()
            shutil.rmtree(directory)
            result['cleanedUp'] = not directory.exists()
        except Exception:
            result['cleanedUp'] = False; error = 'cleanup_incomplete'; response_status = 'failed'
            engine.private_json(root / 'cleanup-pending.json', {'id': job['id']})
        heartbeat_stop.set(); thread.join(timeout=20)
        status(config, {'runId': job['id'], 'stage': 'report'})
        # Keep marker until the controller accepts the terminal result, so a
        # platform replacement cannot race finalization.
        for attempt in range(3):
            try:
                api('report', {**proof, 'status': response_status, 'result': result, 'errorCode': error}); break
            except Exception:
                if attempt == 2: raise
                time.sleep(1)
        status(config, {'runId': '' if result.get('cleanedUp') else job['id'], 'stage': 'idle' if result.get('cleanedUp') else 'cleanup'})
def reconcile(config):
    root = pathlib.Path(config['stateRoot'])
    for directory in root.iterdir():
        if not directory.is_dir() or not engine.ID.fullmatch(directory.name): continue
        if directory.resolve() != directory: raise engine.ProjectRecoveryError('Copie de reprise non canonique.')
        journal = directory / 'restored/sandbox-state.json'
        if journal.exists(): engine.cleanup_sandbox(json.loads(engine.recovery.private_file(journal).read_text()), engine.Docker(config['sandboxDockerSocket']))
        shutil.rmtree(directory)
    # A dead coordinator's future lease must not hold subsequent work for
    # two hours. The exclusive daemon lock and completed copy cleanup are
    # required before this dedicated-root reconciliation.
    api('claim', {'cleanupOnly': True, 'recoverOrphans': True})
    (root / 'cleanup-pending.json').unlink(missing_ok=True)
    status(config, {'runId': '', 'stage': 'idle'})


def main():
    if os.geteuid() != 0: raise engine.ProjectRecoveryError('Service root requis pour les lectures natives sélectionnées.')
    config = config_file(); lock = open(pathlib.Path(config['stateRoot']) / 'service.lock', 'a')
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    def stop(_signum, _frame):
        global STOP
        STOP = True
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    pending = True
    health_stop=threading.Event()
    def health_loop():
        while not health_stop.is_set():
            try:publish_health(config)
            except Exception:pass
            health_stop.wait(15)
    health_thread=threading.Thread(target=health_loop,name='project-recovery-health',daemon=True);health_thread.start()
    while not STOP:
        try:
            if pending or (pathlib.Path(config['stateRoot']) / 'cleanup-pending.json').exists():
                try: reconcile(config); pending = False
                except Exception:
                    api('claim', {'cleanupOnly': True, 'blocked': True}); raise
            paused = maintenance_status()['paused']
            job = api('claim', {'cleanupOnly': paused, 'blocked': False}).get('job')
            if job: process(config, job)
        except Exception:
            # Retry cleanup/root reconciliation before the next claim, including
            # an accepted terminal report whose HTTP acknowledgement was lost.
            pending = True
        for _ in range(30):
            if STOP: break
            time.sleep(.1)
    health_stop.set();health_thread.join(timeout=5)
    publish_health(config,stopped=True)


if __name__ == '__main__': main()
