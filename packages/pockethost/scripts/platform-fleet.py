#!/usr/bin/env python3
"""Publish one tested platform version to all active servers, then verify their recorded code hashes.

No environment variables or business data are copied. An installer implements idempotent
prepare/activate/verify/commit/rollback phases, with host-local configuration and code rollback.
"""
import argparse
import hashlib
import json
import pathlib
import re
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

ACTIVE = ('app3', 'app4', 'app5')
RECORD = '/var/lib/kolab-platform-release.json'


def configuration():
    value = json.loads(pathlib.Path(__file__).with_suffix('.json').read_text())['targets']
    if set(value) != set(ACTIVE):
        raise ValueError('La flotte doit contenir exactement app3, app4 et app5 ; app1/app2 sont retirés.')
    return value


def run(args, **options):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=options.pop('timeout', 180), **options)


def destination(target):
    return target['user'] + '@' + target['address']


def ssh(target, command, **options):
    return run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', destination(target), command], **options)


def valid_bundle(directory, targets):
    directory = directory.resolve()
    data = json.loads((directory / 'release.json').read_text())
    if set(data.get('targets', {})) != set(ACTIVE) or set(targets) != set(ACTIVE):
        raise ValueError('Une publication doit inclure app3 ET app4 ET app5.')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{4,80}', data.get('id', '')):
        raise ValueError('Identifiant de version invalide.')
    if not re.fullmatch(r'[a-f0-9]{64}', data.get('sourceHash', '')):
        raise ValueError('Empreinte commune manquante.')
    waves = data.get('waves', [list(ACTIVE)])
    if (not isinstance(waves, list) or not waves or
            any(not isinstance(wave, list) or not wave for wave in waves) or
            any(not isinstance(host,str)or host not in ACTIVE for wave in waves for host in wave) or
            sorted(host for wave in waves for host in wave) != sorted(ACTIVE)):
        raise ValueError('Les vagues doivent inclure chaque serveur actif exactement une fois.')
    for row in [data['installer'], *data['targets'].values()]:
        name = row['file']
        if pathlib.PurePosixPath(name).name != name:
            raise ValueError('Nom de fichier de publication invalide.')
        p = directory / name
        if p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Archive modifiée ou absente : ' + name)
    return data


def publish(directory, targets, upload, phase):
    data = valid_bundle(directory, targets)
    touched = []
    try:
        waves = data.get('waves', [list(ACTIVE)])
        for wave in waves:
            # Every pilot in this wave passes before any later host is contacted.
            for host in wave:
                upload(host, targets[host], directory, data)
            for host in wave:
                phase(host, targets[host], data, 'prepare')
            for host in wave:
                touched.append(host)  # Include a partially activated server.
                phase(host, targets[host], data, 'activate')
            for host in wave:
                phase(host, targets[host], data, 'verify')
        for host in touched:
            phase(host, targets[host], data, 'commit')
    except BaseException as original:
        failures = []
        for host in reversed(touched):
            try:
                phase(host, targets[host], data, 'rollback')
            except Exception as error:
                failures.append(host + ': ' + str(error))
        if failures:
            raise RuntimeError('Publication échouée ; retour arrière incomplet : ' + '; '.join(failures)) from original
        raise
    return data


CHECK = r'''
import hashlib,http.client,json,pathlib,socket,ssl,subprocess,urllib.request
p=pathlib.Path('/var/lib/kolab-platform-release.json')
if not p.is_file():
 print(json.dumps({'ok':False,'reason':'Version commune non enregistrée'}));raise SystemExit()
m=json.loads(p.read_text());root=pathlib.Path(m['releaseRoot']);drift=[]
bases={'release':root,'installed':pathlib.Path('/opt/pockethost-web'),'unit':pathlib.Path('/etc/systemd/system')}
bases.update({k:pathlib.Path(v)for k,v in m.get('runtimeRoots',{}).items()})
for name,digest in m['sharedFiles'].items():
 scope,rel=name.split(':',1);base=bases[scope]
 p=base/rel
 if not p.is_file()or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:drift.append(name)
for name,digest in m['uiFiles'].items():
 p=root/'packages/dashboard/build'/name
 if not p.is_file()or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:drift.append('ui:'+name)
health=False;services=True;origin=False
try:
 with urllib.request.urlopen('http://127.0.0.1:8091/api/health',timeout=8)as r:health=r.status==200
except Exception:pass
for unit in m.get('services',{}).get('systemd',[]):
 if subprocess.run(['systemctl','is-active','--quiet',unit]).returncode:services=False
expected=m.get('services',{}).get('pm2',[])
if expected:
 rows=json.loads(subprocess.check_output(['runuser','-u','ubuntu','--','pm2','jlist'],text=True))
 states={row['name']:row['pm2_env']['status']for row in rows}
 services=services and all(states.get(name)=='online'for name in expected)
try:
 class Origin(http.client.HTTPSConnection):
  def connect(self):self.sock=self._context.wrap_socket(socket.create_connection(('127.0.0.1',443),6),server_hostname=self.host)
 c=Origin(m['hostname'],timeout=8,context=ssl.create_default_context());c.request('GET','/home');r=c.getresponse();origin=r.status==200;c.close()
except Exception:pass
print(json.dumps({'ok':not drift and health and services and origin,'id':m['id'],'sourceHash':m['sourceHash'],'files':len(m['sharedFiles']),'drift':drift,'controllerHealthy':health,'servicesHealthy':services,'originHealthy':origin}))
'''


def inspect(targets):
    def one(host):
        result = ssh(targets[host], "sudo -n python3 - <<'PY'\n" + CHECK + '\nPY', timeout=40)
        return host, json.loads(result.stdout)
    with ThreadPoolExecutor(max_workers=len(ACTIVE)) as pool:
        values = dict(pool.map(one, ACTIVE))
    ready = all(v.get('ok') for v in values.values())
    same = len({(v.get('id'), v.get('sourceHash')) for v in values.values()}) == 1
    return {'ok': ready and same, 'sameVersion': same and ready, 'targets': values}


def upload(host, target, directory, data):
    remote = '/home/ubuntu/platform-fleet/' + data['id']
    ssh(target, 'mkdir -p ' + shlex.quote(remote))
    names = [directory / data['installer']['file'], directory / data['targets'][host]['file']]
    run(['scp', '-q', *map(str, names), destination(target) + ':' + remote + '/'], timeout=120)


def phase(host, target, data, name):
    remote = '/home/ubuntu/platform-fleet/' + data['id']
    command = ['sudo', '-n', 'python3', remote + '/' + data['installer']['file'], remote + '/' + data['targets'][host]['file'], name]
    try:
        result = ssh(target, shlex.join(command), timeout=240)
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or '').strip().splitlines()
        last = detail[-1] if detail else 'Aucun détail fourni par le serveur.'
        raise RuntimeError(host + ' / ' + name + ' : ' + last[:600]) from error
    # Installer output contains aggregate verification results only.
    print(json.dumps({'host': host, 'phase': name, 'result': json.loads(result.stdout)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check', help='Vérifier app3, app4 et app5 sans modifier leur configuration.')
    release = sub.add_parser('publish', help='Publier sur les trois serveurs, avec vagues et retour arrière commun.')
    release.add_argument('directory', type=pathlib.Path)
    args = parser.parse_args()
    targets = configuration()
    if args.command == 'publish':
        publish(args.directory, targets, upload, phase)
    result = inspect(targets)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, subprocess.SubprocessError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
