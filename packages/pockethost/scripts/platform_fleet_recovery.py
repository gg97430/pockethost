#!/usr/bin/env python3
"""Seal fixed fleet trust files for the unprivileged, encrypted platform backup.

Private SSH keys remain root-only. Only authenticated ciphertext is readable by
the backup account. Unseal writes a new private quarantine directory; it never
installs SSH keys or changes live trust.
"""
import argparse,base64,hashlib,hmac,json,os,pathlib,re,secrets,stat,tempfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

TRUST=pathlib.Path('/etc/kolab/fleet')
AUTHORIZED=pathlib.Path('/var/lib/kolab-fleet-reader/.ssh/authorized_keys')
SUDOERS=pathlib.Path('/etc/sudoers.d/kolab-fleet-reader')
OUTPUT=pathlib.Path('/var/lib/kolab-fleet-reader-recovery/trust.json.enc')
CONFIG=pathlib.Path('/home/ubuntu/kolab-secrets/web-platform-recovery-config.json')
PEERS=('app3','app4','app5')
MAXIMUM=131072


def read(path,maximum=MAXIMUM,owners=(0,),private=False):
    path=pathlib.Path(path)
    if path.resolve()!=path:raise ValueError('Symbolic path refused')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode)or info.st_uid not in owners or info.st_mode&(0o077 if private else 0o022)or info.st_size>maximum:raise ValueError('Untrusted file')
        value=os.read(fd,maximum+1)
        if len(value)>maximum:raise ValueError('Oversized file')
        return value
    finally:os.close(fd)


def key(filename,owners=(0,1000)):
    value=read(filename,128,owners,True).decode().strip()
    if not re.fullmatch('[a-fA-F0-9]{64}',value):raise ValueError('Invalid recovery key')
    return hmac.new(bytes.fromhex(value),b'Kolab fleet trust recovery v1',hashlib.sha256).digest()


def paths(self):
    return {'fleet/config.json':TRUST/'config.json','fleet/known_hosts':TRUST/'known_hosts',
            **{'fleet/keys/'+peer:TRUST/'keys'/peer for peer in PEERS if peer!=self},
            **{'fleet/keys/'+peer+'.pub':TRUST/'keys'/(peer+'.pub') for peer in PEERS if peer!=self},
            'reader/authorized_keys':AUTHORIZED,'reader/sudoers':SUDOERS}


def encode(files,hostname,secret):
    if hostname not in PEERS or set(files)!=set(paths(hostname)):raise ValueError('Incomplete fixed trust inventory')
    raw=json.dumps({'version':1,'self':hostname,'files':{name:base64.b64encode(value).decode()for name,value in sorted(files.items())}},sort_keys=True,separators=(',',':')).encode()
    if len(raw)>MAXIMUM:raise ValueError('Oversized trust inventory')
    nonce=secrets.token_bytes(12);aad=('Kolab fleet trust v1 '+hostname).encode()
    return json.dumps({'version':1,'self':hostname,'nonce':base64.b64encode(nonce).decode(),'ciphertext':base64.b64encode(AESGCM(secret).encrypt(nonce,raw,aad)).decode()},separators=(',',':')).encode()


def decode(raw,hostname,secret):
    if len(raw)>MAXIMUM*2 or hostname not in PEERS:raise ValueError('Invalid sealed inventory')
    value=json.loads(raw)
    if not isinstance(value,dict)or set(value)!={'version','self','nonce','ciphertext'}or value['version']!=1 or value['self']!=hostname:raise ValueError('Wrong server inventory')
    plain=AESGCM(secret).decrypt(base64.b64decode(value['nonce'],validate=True),base64.b64decode(value['ciphertext'],validate=True),('Kolab fleet trust v1 '+hostname).encode())
    value=json.loads(plain)
    if set(value)!={'version','self','files'}or value['version']!=1 or value['self']!=hostname or not isinstance(value['files'],dict)or set(value['files'])!=set(paths(hostname)):raise ValueError('Incomplete trust inventory')
    return {name:base64.b64decode(content,validate=True)for name,content in value['files'].items()}


def seal(config=CONFIG,output=OUTPUT):
    if os.geteuid()!=0:raise PermissionError('Root sealing required')
    config=json.loads(read(config,16384,(0,1000),True))
    local=json.loads(read(TRUST/'config.json',8192,private=True))['self']
    files={name:read(path,16384,private='/keys/'in str(path)and not str(path).endswith('.pub'))for name,path in paths(local).items()}
    raw=encode(files,local,key(config['keyFile']))
    assert decode(raw,local,key(config['keyFile']))==files
    output=pathlib.Path(output)
    output.parent.mkdir(parents=True,mode=0o750,exist_ok=True)
    info=output.parent.lstat()
    if output.parent.resolve()!=output.parent or not stat.S_ISDIR(info.st_mode)or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Untrusted export directory')
    os.chown(output.parent,0,1000);os.chmod(output.parent,0o750)
    if output.is_symlink():raise ValueError('Symbolic output refused')
    fd,name=tempfile.mkstemp(prefix='.trust-',dir=output.parent)
    try:
        with os.fdopen(fd,'wb')as stream:os.fchmod(stream.fileno(),0o640);os.fchown(stream.fileno(),0,1000);stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(name,output)
    finally:
        if os.path.exists(name):os.unlink(name)
    return {'sealed':True,'self':local,'files':len(files),'sha256':hashlib.sha256(raw).hexdigest(),'privateFilesChanged':False}


def unseal(source,key_file,hostname,destination):
    # Authenticate and validate the entire inventory before creating any output.
    files=decode(read(source,MAXIMUM*2,(0,1000,os.geteuid())),hostname,key(key_file,(0,1000,os.geteuid())))
    destination=pathlib.Path(destination).absolute()
    if destination.resolve()!=destination or destination.exists():raise ValueError('New private quarantine required')
    parent=destination.parent.stat()
    if not stat.S_ISDIR(parent.st_mode)or parent.st_uid!=os.geteuid()or parent.st_mode&0o022:raise ValueError('Caller-owned private parent required')
    destination.mkdir(mode=0o700)
    for name,raw in files.items():
        path=destination/name;path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
        with path.open('xb')as out:os.fchmod(out.fileno(),0o600);out.write(raw)
    return {'unsealed':True,'self':hostname,'files':len(files),'liveTrustChanged':False}


def main():
    os.umask(0o077);p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='action',required=True)
    a=s.add_parser('seal');a.add_argument('--config',default=str(CONFIG));a.add_argument('--output',default=str(OUTPUT))
    a=s.add_parser('unseal');a.add_argument('--source',required=True);a.add_argument('--key-file',required=True);a.add_argument('--server',choices=PEERS,required=True);a.add_argument('--destination',required=True)
    a=p.parse_args()
    print(json.dumps(seal(a.config,a.output)if a.action=='seal'else unseal(a.source,a.key_file,a.server,a.destination)))


if __name__=='__main__':
    try:main()
    except Exception:print('{"error":"fleet_recovery_export_failed"}');raise SystemExit(1)
