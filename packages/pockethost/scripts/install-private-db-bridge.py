#!/usr/bin/env python3
"""Install private DB bridge configuration without restarting Docker or applications.

Run as root after deploying the broker/provider/proxy scripts. The caller owns the
controller/worker restart, so a deployment can guard jobs and keep its rollback.
"""
import argparse
import json
import os
import pathlib
import pwd
import re
import secrets
import stat
import subprocess


def write(path, text, mode, uid=0, gid=0):
    if path.is_symlink():
        raise RuntimeError('Refusing symbolic configuration path')
    temporary = path.with_name(path.name + '.private-db-tmp')
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'w') as output:
        output.write(text)
        os.fchmod(output.fileno(), mode)
        os.fchown(output.fileno(), uid, gid)
    os.replace(temporary, path)


def update_env(path, values, uid, gid):
    original = path.read_text() if path.exists() else ''
    names = set(values)
    lines = [line for line in original.splitlines() if not any(re.match(r'^\s*(?:export\s+)?' + re.escape(name) + r'\s*=', line) for name in names)]
    lines.extend(name + '=' + json.dumps(value) for name, value in values.items())
    write(path, '\n'.join(lines) + '\n', 0o600, uid, gid)


def controller_env(controller, operator):
    # Fleet releases intentionally link .env to the operator's shared host
    # configuration. Keep that link intact and update only its trusted target.
    source = controller / '.env'
    path = source.resolve(strict=True) if source.exists() or source.is_symlink() else source
    if not path.is_relative_to(pathlib.Path(operator.pw_dir)) or path.name != '.env':
        raise RuntimeError('Unsafe controller environment location')
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != operator.pw_uid or info.st_mode & 0o077:
            raise RuntimeError('Unsafe controller environment permissions')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controller-root', type=pathlib.Path, required=True)
    parser.add_argument('--target-image', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or not re.fullmatch(r'node@sha256:[a-f0-9]{64}', args.target_image):
        raise RuntimeError('Root and a pinned official Node image are required')
    operator, worker = pwd.getpwnam('ubuntu'), pwd.getpwnam('pockethost-web')
    if (operator.pw_uid, operator.pw_gid, worker.pw_uid, worker.pw_gid) != (1000, 1000, 1001, 1001):
        raise RuntimeError('Unsupported service identities')
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', args.target_image], text=True))[0]
    if args.target_image not in image.get('RepoDigests', []):
        raise RuntimeError('Pinned Node image must already be installed')
    script_root = pathlib.Path(__file__).resolve().parent
    if script_root != pathlib.Path('/opt/pockethost-web'):
        raise RuntimeError('Run the installed trusted script')
    for name in ['web_private_db_broker.py', 'web_private_db_provider.py', 'web_private_db_cpu.py', 'web_private_db_limits.py', 'instance_request_limits.py', 'web_private_db_proxy.cjs', 'web_private_db_target.cjs', 'web_private_db_wait.cjs', 'web-private-db-broker.service']:
        item = script_root / name
        info = item.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError('Untrusted bridge source')
    controller = args.controller_root.resolve(strict=True)
    if not (controller / 'packages/pockethost/src/mothership-app/pb_hooks/mothership.js').is_file():
        raise RuntimeError('Controller release not found')
    environment_path = controller_env(controller, operator)
    bridge = pathlib.Path('/var/lib/kolab-db-bridge')
    if bridge.is_symlink():
        raise RuntimeError('Unsafe bridge directory')
    bridge.mkdir(mode=0o710, exist_ok=True)
    os.chown(bridge, operator.pw_uid, worker.pw_gid)
    bridge.chmod(0o710)
    target = pathlib.Path('/run/kolab-db-targets')
    if target.is_symlink():
        raise RuntimeError('Unsafe target directory')
    target.mkdir(mode=0o700, exist_ok=True)
    os.chown(target, operator.pw_uid, operator.pw_gid)
    target.chmod(0o700)
    write(pathlib.Path('/etc/tmpfiles.d/kolab-db-targets.conf'), 'd /run/kolab-db-targets 0700 ubuntu ubuntu -\n', 0o644)
    secret_file = pathlib.Path('/etc/kolab-db-bridge.env')
    secret = secrets.token_hex(32)
    if secret_file.exists():
        match = re.search(r'^WEB_DB_BRIDGE_SECRET="?([a-f0-9]{64})"?$', secret_file.read_text(), re.M)
        if match:
            secret = match.group(1)
    write(secret_file, 'WEB_DB_BRIDGE_CONTROL=http://127.0.0.1:8091\nWEB_DB_BRIDGE_SECRET=' + secret + '\n', 0o600)
    # The CLI's constants.ts loads the project .env, not Next.js .env.local.
    update_env(environment_path, {'WEB_DB_BRIDGE_ENABLED': 'true', 'WEB_DB_BRIDGE_SECRET': secret, 'WEB_DB_TARGET_IMAGE': args.target_image}, operator.pw_uid, operator.pw_gid)
    update_env(pathlib.Path('/home/pockethost-web/web-apps.env'), {'WEB_DB_BRIDGE_ENABLED': 'true'}, worker.pw_uid, worker.pw_gid)
    write(pathlib.Path('/etc/systemd/system/kolab-db-bridge.service'), (script_root / 'web-private-db-broker.service').read_text(), 0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True, capture_output=True)
    subprocess.run(['systemctl', 'enable', 'kolab-db-bridge.service'], check=True, capture_output=True)
    print(json.dumps({'configured': True, 'servicesRestarted': False, 'targetRootMode': '0700', 'dockerRestarted': False}))


if __name__ == '__main__':
    os.umask(0o077)
    main()
