#!/usr/bin/env python3
"""Reconcile trusted DB namespace sidecars. Never routes through published Docker ports.

This controller helper is invoked only by the authenticated local PocketBase routes.
Its input is trusted server configuration plus validated database record IDs. The
broker receives only verified Unix sockets, and never gets a Docker credential.
"""
import fcntl
import hashlib
import json
import os
import pathlib
import re
import signal
import stat
import subprocess
import sys
import time

ID = re.compile(r"[a-z0-9]{15}\Z")
HEX = re.compile(r"[a-f0-9]{64}\Z")
DATABASE_SHA = re.compile(r"sha256:[a-f0-9]{64}\Z")
IMAGE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9./_:-]*@sha256:[a-f0-9]{64}\Z")
ROLE = 'kolab.db-target.role'
INSTANCE = 'kolab.db-target.instance'
PARENT = 'kolab.db-target.parent'
EPOCH = 'kolab.db-target.epoch'
RECIPE = 'kolab.db-target.recipe'
SOCKET = 'unix:///var/run/docker.sock'
TARGET_SCRIPT = '/opt/pockethost-web/web_private_db_target.cjs'
TARGET_ROOT = '/run/kolab-db-targets'
NAME = re.compile(r'/kolab-db-target-[a-z0-9]{15}-[a-f0-9]{12}-[a-f0-9]{8}\Z')


class Provider:
    def __init__(self, config, runner=None):
        self.root = pathlib.Path(config['dataRoot']).resolve()
        self.target_root = pathlib.Path(config.get('targetRoot', TARGET_ROOT))
        self.target_script = pathlib.Path(config.get('targetScript', TARGET_SCRIPT))
        self.image = config['targetImage']
        self.database_image = config['databaseImage']
        ids = config['ids']
        if not isinstance(ids, list) or len(ids) > 500 or any(not isinstance(i, str) or not ID.fullmatch(i) for i in ids):
            raise ValueError('Invalid database IDs')
        self.ids = set(ids)
        overrides = config.get('databaseImages', {})
        if (not isinstance(overrides, dict) or len(overrides) > 256
                or any(not isinstance(i, str) or not ID.fullmatch(i) or i not in self.ids
                       or not isinstance(ref, str) or not DATABASE_SHA.fullmatch(ref)
                       for i, ref in overrides.items())):
            raise ValueError('Invalid trusted database image assignments')
        self.database_images = overrides

        if not self.target_root.is_absolute() or not self.target_script.is_absolute() or not IMAGE.fullmatch(self.image):
            raise ValueError('Invalid trusted target configuration')
        if not self.database_image or not isinstance(self.database_image, str):
            raise ValueError('Invalid database image')
        script = self.target_script.read_bytes()
        if len(script) > 512 * 1024:
            raise ValueError('Invalid trusted target script')
        self.recipe = hashlib.sha256(script + self.image.encode()).hexdigest()
        self.runner = runner or subprocess.run
        self.deadline = time.monotonic() + 2.2
        self.result = {}

    def docker(self, *args, parse=False):
        remaining = self.deadline - time.monotonic()
        if remaining < .05:
            raise TimeoutError('Provider deadline')
        result = self.runner(['docker', '--host', SOCKET, *args], capture_output=True, text=True, timeout=min(.8, remaining))
        if parse:
            # Docker inspect can return valid JSON for existing IDs alongside a
            # nonzero exit status for a deleted record. Missing IDs stay absent.
            try:
                data = json.loads(result.stdout)
                return data if isinstance(data, list) else []
            except (ValueError, TypeError):
                return []
        return result.returncode == 0, result.stdout.strip()

    def inspect(self, ids):
        return self.docker('inspect', '--type', 'container', *ids, parse=True) if ids else []

    def database(self, info):
        name = info.get('Name', '')
        ident = name[1:] if isinstance(name, str) and name.startswith('/') else ''
        config, state = info.get('Config') or {}, info.get('State') or {}
        image = config.get('Image', '')
        cid, started = info.get('Id', ''), state.get('StartedAt', '')
        if ident not in self.ids or name != '/' + ident or not state.get('Running') or not HEX.fullmatch(cid):
            return None
        expected_image = self.database_images.get(ident)
        if expected_image:
            # Both the immutable image and the creation reference must be the exact reviewed hash.
            if image != expected_image or info.get('Image') != expected_image:
                return None
        elif image != self.database_image and not image.startswith(self.database_image + ':') and not image.startswith(self.database_image + '@'):
            return None
        if not isinstance(started, str) or not started or started.startswith('0001-'):
            return None
        expected = self.root / 'instances' / ident
        if not any(m.get('Destination') == '/home/pockethost' and pathlib.Path(m.get('Source', '')).resolve() == expected for m in info.get('Mounts', [])):
            return None
        epoch = hashlib.sha256(started.encode()).hexdigest()
        digest = hashlib.sha256((cid + ':' + epoch).encode()).hexdigest()[:32]
        directory = self.target_root / ident / digest
        return {'instanceId': ident, 'containerId': cid, 'epoch': epoch, 'directory': directory,
                'name': 'kolab-db-target-' + ident + '-' + cid[:12] + '-' + epoch[:8]}

    def directory(self, directory, mode):
        try:
            directory.mkdir(mode=mode)
            os.chown(str(directory), -1, 1000)
            directory.chmod(mode)
        except FileExistsError:
            pass
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_gid != 1000 or stat.S_IMODE(info.st_mode) != mode:
            raise ValueError('Unsafe target directory')

    def leaf(self, target):
        # Never follow symlinks in runtime paths; even a bad/stale local file
        # must only make this database unavailable.
        for directory, mode in [(self.target_root, 0o700), (self.target_root / target['instanceId'], 0o700), (target['directory'], 0o770)]:
            self.directory(directory, mode)

    def managed(self, info):
        labels = (info.get('Config') or {}).get('Labels') or {}
        return (labels.get(ROLE) == 'target' and bool(ID.fullmatch(labels.get(INSTANCE, '')))
                and bool(HEX.fullmatch(labels.get(PARENT, ''))) and bool(HEX.fullmatch(labels.get(EPOCH, '')))
                and bool(NAME.fullmatch(info.get('Name', ''))) and bool(HEX.fullmatch(info.get('Id', ''))))

    def trusted(self, info, target):
        config, host = info.get('Config') or {}, info.get('HostConfig') or {}
        labels = config.get('Labels') or {}
        if not self.managed(info) or info.get('Name') != '/' + target['name']:
            return False
        if any(labels.get(key) != value for key, value in [(INSTANCE, target['instanceId']), (PARENT, target['containerId']), (EPOCH, target['epoch'])]):
            return False
        if labels.get(RECIPE) != self.recipe:
            return False
        if config.get('Image') != self.image or config.get('User') != '10001:1000':
            return False
        if config.get('Entrypoint') != ['/usr/local/bin/node'] or config.get('Cmd') != ['/run/kolab-target.cjs']:
            return False
        health = config.get('Healthcheck') or {}
        if (health.get('Test') != ['CMD-SHELL', '/usr/local/bin/node /run/kolab-target.cjs --check']
                or health.get('Interval') != 5000000000 or health.get('Timeout') != 5000000000
                or health.get('Retries') != 1 or health.get('StartPeriod') != 1000000000):
            return False
        if host.get('NetworkMode') != 'container:' + target['containerId'] or not host.get('ReadonlyRootfs') or host.get('Privileged'):
            return False
        if host.get('CapAdd') or {str(x).upper() for x in host.get('CapDrop') or []} != {'ALL'}:
            return False
        if host.get('SecurityOpt') not in [['no-new-privileges'], ['no-new-privileges:true']]:
            return False
        if host.get('Memory') != 96 * 1024 * 1024 or host.get('NanoCpus') != 50000000 or host.get('PidsLimit') != 64:
            return False
        mounts = info.get('Mounts') or []
        if len(mounts) != 2:
            return False
        expected = {'/run/target': (str(target['directory']), True), '/run/kolab-target.cjs': (str(self.target_script), False)}
        if {m.get('Destination') for m in mounts} != set(expected):
            return False
        for mount in mounts:
            dest = mount.get('Destination')
            if dest not in expected or mount.get('Type') != 'bind' or (mount.get('Source'), mount.get('RW')) != expected[dest]:
                return False
        return True

    def available(self, info, target):
        state = info.get('State') or {}
        if not state.get('Running') or (state.get('Health') or {}).get('Status') != 'healthy':
            return False
        try:
            sock = (target['directory'] / 't.sock').lstat()
            return stat.S_ISSOCK(sock.st_mode) and sock.st_uid == 10001 and sock.st_gid == 1000 and stat.S_IMODE(sock.st_mode) == 0o660
        except OSError:
            return False

    def create(self, target):
        self.leaf(target)
        args = ['create', '--name', target['name'], '--label', ROLE + '=target']
        for key, value in [(INSTANCE, target['instanceId']), (PARENT, target['containerId']), (EPOCH, target['epoch']), (RECIPE, self.recipe)]:
            args += ['--label', key + '=' + value]
        args += ['--network', 'container:' + target['containerId'], '--user', '10001:1000', '--read-only', '--cap-drop', 'ALL',
                 '--security-opt', 'no-new-privileges', '--memory', '96m', '--cpus', '0.05', '--pids-limit', '64',
                 '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=4m,uid=10001,gid=1000,mode=0700',
                 '--mount', 'type=bind,src=' + str(target['directory']) + ',dst=/run/target',
                 '--mount', 'type=bind,src=' + str(self.target_script) + ',dst=/run/kolab-target.cjs,readonly',
                 '--health-cmd', '/usr/local/bin/node /run/kolab-target.cjs --check', '--health-interval', '5s', '--health-timeout', '5s',
                 '--health-retries', '1', '--health-start-period', '1s', '--entrypoint', '/usr/local/bin/node', self.image, '/run/kolab-target.cjs']
        ok, cid = self.docker(*args)
        if ok and HEX.fullmatch(cid):
            self.docker('start', cid)

    def cache(self):
        """Only lock contention may reuse a short-lived, inode-pinned target.

        Permissions are never cached: the caller independently reauthorizes
        every binding. An old target remains in its immutable DB namespace.
        """
        result = {}
        try:
            fd = os.open(str(self.target_root / '.provider-state.json'), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_gid != 1000 or stat.S_IMODE(info.st_mode) != 0o600:
                    return result
                raw = source.read(512 * 1024 + 1)
            if len(raw) > 512 * 1024:
                return result
            value = json.loads(raw)
            age = time.time() - value['checkedAt']
            if not 0 <= age < 6 or not isinstance(value['targets'], dict):
                return result
            for ident, target in value['targets'].items():
                if ident not in self.ids or not isinstance(target, dict) or target.get('available') is not True:
                    continue
                if target.get('_databaseImage') != self.database_images.get(ident, self.database_image):
                    continue
                cid, epoch = target.get('targetContainerId'), target.get('targetEpoch')
                if not isinstance(cid, str) or not HEX.fullmatch(cid) or not isinstance(epoch, str) or not HEX.fullmatch(epoch):
                    continue
                digest = hashlib.sha256((cid + ':' + epoch).encode()).hexdigest()[:32]
                directory = self.target_root / ident / digest
                if target.get('targetSocket') != str(directory / 't.sock'):
                    continue
                valid = True
                for parent, mode in [(self.target_root, 0o700), (self.target_root / ident, 0o700), (directory, 0o770)]:
                    info = parent.lstat()
                    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_gid != 1000 or stat.S_IMODE(info.st_mode) != mode:
                        valid = False
                sock = (directory / 't.sock').lstat()
                if (valid and stat.S_ISSOCK(sock.st_mode) and sock.st_uid == 10001 and sock.st_gid == 1000 and stat.S_IMODE(sock.st_mode) == 0o660
                        and sock.st_dev == target.get('_device') and sock.st_ino == target.get('_inode')):
                    result[ident] = {key: target[key] for key in ['available', 'targetSocket', 'targetContainerId', 'targetEpoch']}
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return result

    def save_cache(self):
        targets = {}
        for ident, target in self.result.items():
            try:
                sock = pathlib.Path(target['targetSocket']).lstat()
                targets[ident] = {**target, '_device': sock.st_dev, '_inode': sock.st_ino, '_databaseImage': self.database_images.get(ident, self.database_image)}
            except OSError:
                pass
        temporary = self.target_root / ('.provider-state.' + str(os.getpid()) + '.tmp')
        try:
            fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as output:
                os.fchown(output.fileno(), -1, 1000)
                json.dump({'checkedAt': time.time(), 'targets': targets}, output, separators=(',', ':'))
            os.replace(str(temporary), str(self.target_root / '.provider-state.json'))
        except OSError:
            pass
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def reconcile(self):
        # Serialize the broker and worker polls. A concurrent poll safely
        # reports unavailable rather than waiting behind Docker.
        self.directory(self.target_root, 0o700)
        lock = os.open(str(self.target_root / '.provider.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return self.cache()
            targets = {}
            for info in self.inspect(sorted(self.ids)):
                target = self.database(info)
                if target:
                    targets[target['name']] = target
            ok, listing = self.docker('ps', '-a', '--filter', 'label=' + ROLE + '=target', '--format', '{{.ID}}', '--no-trunc')
            listed = [cid for cid in listing.splitlines() if HEX.fullmatch(cid)] if ok else []
            current = {}
            for info in self.inspect(listed):
                if not isinstance(info, dict):
                    continue
                if not self.managed(info):
                    continue
                name = info.get('Name', '').lstrip('/')
                target = targets.get(name)
                if target and self.trusted(info, target):
                    self.leaf(target)
                    current[name] = info
                    if self.available(info, target):
                        self.result[target['instanceId']] = {'available': True, 'targetSocket': str(target['directory'] / 't.sock'),
                            'targetContainerId': target['containerId'], 'targetEpoch': target['epoch']}
                    elif not (info.get('State') or {}).get('Running'):
                        self.docker('start', info['Id'])
                else:
                    self.docker('rm', '--force', info['Id'])
            # Create at most two per request; remaining databases converge on
            # subsequent polls, without exceeding the broker's HTTP timeout.
            created = 0
            for name, target in targets.items():
                if name not in current and created < 2:
                    self.create(target)
                    created += 1
            self.save_cache()
        finally:
            os.close(lock)
        return self.result


def main():
    result = {}
    provider = None
    try:
        signal.alarm(3)
        config = json.loads(sys.argv[1])
        provider = Provider(config)
        result = provider.reconcile()
    except (Exception, SystemExit):
        # No Docker stderr or private host metadata enters logs or an API
        # response. A later poll retries; every failure is closed.
        if provider is not None:
            result = provider.result
    finally:
        print(json.dumps(result, separators=(',', ':')))


if __name__ == '__main__':
    main()
