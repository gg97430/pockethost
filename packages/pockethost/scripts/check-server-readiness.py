#!/usr/bin/env python3
"""Read-only, fail-closed host acceptance. Never prints environment values or command output.

Run from the exact release checkout to detect installed/source drift. This does
not replace the disposable app/database, external network and reboot acceptance.
"""
import argparse
import hashlib
import json
import math
import os
import pathlib
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent
REQUIRED_TOOLS = ('cpulimit', 'sqlite3', 'tar', 'gzip', 'unzip', 'nice', 'ionice',
                  'git', 'ssh', 'ssh-keygen', 'ssh-keyscan', 'certbot', 'docker')


def budget_errors(profile, host_cpus, host_memory):
    keys = ('webCpu', 'webMemoryGiB', 'buildCpu', 'buildMemoryGiB',
            'siteCpu', 'siteMemoryGiB', 'reserveCpu', 'reserveMemoryGiB')
    if any(type(profile.get(k)) not in (int, float) or not math.isfinite(profile[k])
           or profile[k] <= 0 for k in keys):
        return ['Budgets must be explicit, finite positive numbers.']
    errors = []
    if profile['webCpu'] > host_cpus or profile['webMemoryGiB'] + 1 > host_memory:
        errors.append('Web budget exceeds host capacity or leaves less than 1 GiB for the platform.')
    mode = profile.get('allocationMode', 'fixed')
    if mode not in ('adaptive', 'fixed'):
        errors.append('Invalid allocationMode.')
    if mode == 'fixed':
        if profile['buildCpu'] + profile['siteCpu'] + profile['reserveCpu'] > profile['webCpu']:
            errors.append('Web CPU cannot fit builder + planned sites/auxiliaries + reserve.')
        if profile['buildMemoryGiB'] + profile['siteMemoryGiB'] + profile['reserveMemoryGiB'] > profile['webMemoryGiB']:
            errors.append('Web RAM cannot fit builder + planned sites/auxiliaries + reserve.')
    elif max(profile['buildMemoryGiB'], profile['siteMemoryGiB']) + max(profile['reserveMemoryGiB'], profile['webMemoryGiB']/10) > profile['webMemoryGiB']:
        errors.append('Web RAM cannot fit a build or planned admission request plus safety reserve.')
    return errors


def secure_file(path, owner=0, private=False):
    path = pathlib.Path(path)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != owner:
            return False
        if info.st_mode & (0o077 if private else 0o022):
            return False
        # Trusted code must not be replaceable through a writable parent directory.
        if not private:
            for parent in path.parents:
                info = parent.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                    return False
        return True
    except OSError:
        return False


def runtime_artifacts():
    artifacts = sorted(SCRIPTS.glob('web_app_*.py')) + sorted(SCRIPTS.glob('web_platform_recovery*.py')) + [SCRIPTS / 'web-app-worker.py']
    for name in ('web_private_db_proxy.cjs', 'web_private_db_wait.cjs', 'web_private_db_broker.py',
                 'web_private_db_provider.py', 'web_private_db_target.cjs', 'platform_resource_collector.py'):
        artifacts.append(SCRIPTS / name)
    return artifacts


def artifact_matches(source, target):
    return (source.is_file() and secure_file(target) and
            hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(target.read_bytes()).digest())


def env_values(path):
    # Only parse literal KEY=value lines, never source shell code or expand variables.
    values = {}
    for line in pathlib.Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'(?:export\s+)?([A-Z_][A-Z0-9_]*)=(.*)', line)
        if not match:
            raise ValueError('Unsupported environment syntax')
        parts = shlex.split(match[2], comments=True)
        if len(parts) > 1:
            raise ValueError('Unsupported environment value')
        values[match[1]] = parts[0] if parts else ''
    return values


def memory_bytes(value):
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([bkmg]?)', value.lower())
    if not match:
        raise ValueError('Invalid memory value')
    return float(match[1]) * 1024 ** {'': 0, 'b': 0, 'k': 1, 'm': 2, 'g': 3}[match[2]]


def loopback_listeners(output, required=('8091', '3000')):
    ports = {str(port): [] for port in required}
    for line in output.splitlines():
        address = line.split()[3]
        host, _, port = address.rpartition(':')
        if port in ports:
            ports[port].append(host in ('127.0.0.1', '[::1]', '::1'))
    return {port: bool(values) and all(values) for port, values in ports.items()}


def ingress_protected(user_rules, forward_rules, guard_rules, interfaces):
    def first_rule(value):
        return next((line for line in value.splitlines() if line.startswith('-A ')), '')
    return (bool(interfaces) and first_rule(user_rules) == '-A DOCKER-USER -j KOLAB-DOCKER-IN' and
            first_rule(forward_rules) == '-A FORWARD -j DOCKER-USER' and
            all('-A KOLAB-DOCKER-IN -i ' + interface + ' -m conntrack --ctdir ORIGINAL -j DROP' in guard_rules.splitlines()
                for interface in interfaces))


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=20, check=False, env={**os.environ, 'LC_ALL': 'C'})
    if result.returncode:
        raise RuntimeError('Command failed')
    return result.stdout.strip()


def run(profile, check):
    mem = int(next(line.split()[1] for line in pathlib.Path('/proc/meminfo').read_text().splitlines()
                   if line.startswith('MemTotal:'))) / 1024**2
    errors = budget_errors(profile, os.cpu_count() or 1, mem)
    check('budgets', not errors, '; '.join(errors) or 'Builder, sites and reserve fit the host profile.')
    check('cgroup-v2', pathlib.Path('/sys/fs/cgroup/cgroup.controllers').is_file(), 'cgroup v2 required.')
    for binary in REQUIRED_TOOLS:
        check('tool:' + binary, shutil.which(binary) is not None, 'Required executable in the operator PATH.')
    for module in ('boto3', 'cryptography', 'sqlite3'):
        command(['/usr/bin/python3', '-c', 'import ' + module])
        check('python:' + module, True, 'System Python import available.')
    trusted_node = secure_file(profile['nodeBinary'])
    check('node-permissions', trusted_node, 'Node must be root-owned in a trusted directory.')
    if not trusted_node:
        return  # Never execute a user-writable binary as root during an inspection.
    check('node', int(command([profile['nodeBinary'], '-p', 'process.versions.node']).split('.')[0]) >= 24,
          'Controller and terminal require Node >=24 at the configured path.')
    user = pwd.getpwnam(profile['webUser'])
    env_path = profile['workerEnv']
    check('worker-env-permissions', secure_file(env_path, user.pw_uid, private=True), 'Worker env must be owned by web user, mode 0600, no symlink.')
    env = env_values(env_path)
    check('allocation-mode', env.get('WEB_APPS_ALLOCATION_MODE', 'adaptive') == profile.get('allocationMode', 'fixed'),
          'Worker allocation mode must match the host profile.')
    check('builder-budget', float(env['WEB_APPS_BUILD_CPUS']) == profile['buildCpu'] and
          memory_bytes(env['WEB_APPS_BUILD_MEMORY']) == profile['buildMemoryGiB'] * 1024**3,
          'Worker builder budgets must match the host profile.')
    installed = pathlib.Path(profile['installedScripts'])
    for source in runtime_artifacts():
        check('artifact:' + source.name, artifact_matches(source, installed / source.name),
              'Installed file must match this release, root-owned and non-writable by services.')
    rootless = json.loads(command(['runuser', '-u', profile['webUser'], '--', 'env',
                                  'DOCKER_HOST=unix:///run/user/' + str(user.pw_uid) + '/docker.sock',
                                  'docker', 'info', '--format', '{{json .}}']))
    check('rootless-docker', any('rootless' in x for x in rootless['SecurityOptions']) and
          str(rootless['CgroupVersion']) == '2' and rootless['CgroupDriver'] == 'systemd', 'Dedicated rootless Docker with systemd cgroups required.')
    slice_name = 'user-' + str(user.pw_uid) + '.slice'
    for prop, expected in (('MemoryMax', profile['webMemoryGiB'] * 1024**3), ('MemorySwapMax', 0)):
        check('slice:' + prop, int(command(['systemctl', 'show', slice_name, '-p', prop, '--value'])) == expected,
              'Effective cgroup limits must match the host profile.')
    # systemd prints durations with units; read the kernel quota instead for exact CPU count.
    group = command(['systemctl', 'show', slice_name, '-p', 'ControlGroup', '--value'])
    cpu_max = (pathlib.Path('/sys/fs/cgroup') / group.lstrip('/') / 'cpu.max').read_text().split()
    check('slice:cpu', cpu_max[0] != 'max' and float(cpu_max[0]) / float(cpu_max[1]) == profile['webCpu'], 'Effective CPU quota must match the host profile.')
    for unit in ('pockethost-web-worker', 'pockethost-web-terminal', 'pockethost-platform-resources',
                 'pockethost-platform-recovery', 'kolab-db-bridge', 'fail2ban'):
        check('service:' + unit, command(['systemctl', 'is-active', unit]) == 'active', 'Required service must be active.')
    ssh = dict(line.split(None, 1) for line in command(['/usr/sbin/sshd', '-T']).splitlines() if ' ' in line)
    check('ssh', ssh.get('passwordauthentication') == 'no' and ssh.get('kbdinteractiveauthentication') == 'no',
          'SSH keys only. Also verify any Match rules and a second real SSH session.')
    config = json.loads(pathlib.Path('/etc/docker/daemon.json').read_text())
    check('docker-loopback', config.get('ip') == '127.0.0.1', 'Rootful Docker must publish to loopback by default.')
    interfaces = set()
    for family in ('-4', '-6'):
        for route in json.loads(command(['ip', '-j', family, 'route', 'show', 'default'])):
            interfaces.add(route['dev'])
    check('public-interface', bool(interfaces), 'At least one public default interface required.')
    for binary in ('iptables', 'ip6tables'):
        rules = command([binary, '-S', 'DOCKER-USER'])
        forward = command([binary, '-S', 'FORWARD'])
        guard_rules = command([binary, '-S', 'KOLAB-DOCKER-IN'])
        check(binary + ':guard', ingress_protected(rules, forward, guard_rules, interfaces),
              'Ingress drops must precede Docker accepts on every public interface, IPv4 and IPv6.')
    guard = pathlib.Path('/usr/local/sbin/kolab-docker-ingress-guard.py')
    check('docker-guard-artifact', artifact_matches(SCRIPTS / 'docker-ingress-guard.py', guard),
          'Installed ingress guard must match this release.')
    check('docker-guard-persistence', str(guard) in command(['systemctl', 'show', 'docker.service', '-p', 'ExecStartPost', '--value']),
          'Docker must reapply the ingress guard after restart.')
    ids = command(['docker', '--host', 'unix:///run/docker.sock', 'ps', '-q']).splitlines()
    for container in ids:
        ports = json.loads(command(['docker', '--host', 'unix:///run/docker.sock', 'inspect',
                                    '--format', '{{json .NetworkSettings.Ports}}', container])) or {}
        check('rootful-container:' + container[:12], all(binding.get('HostIp') in ('127.0.0.1', '::1')
              for bindings in ports.values() for binding in (bindings or [])), 'Live rootful published ports must be loopback-only.')
    listeners = command(['ss', '-ltnH'])
    for port, ok in loopback_listeners(listeners, (profile.get('controllerPort',8091),profile.get('dashboardPort',5174))).items():
        check('listen:' + port, ok, 'Controller/dashboard must listen on loopback only (and be running).')
    check('ufw', 'Status: active' in command(['ufw', 'status']), 'Host firewall must be active.')
    command(['fail2ban-client', 'status', 'sshd'])
    check('ssh-jail', True, 'Fail2ban SSH jail available.')
    import web_app_railpack
    check('railpack', web_app_railpack.tools_ready(), 'Pinned Railpack and mise binaries, hashes and permissions required.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, type=pathlib.Path, help='Non-secret JSON host profile; see server-profile.example.json.')
    args = parser.parse_args()
    results = []
    def check(name, ok, detail):
        results.append({'check': name, 'ok': bool(ok), 'detail': detail})
    try:
        if os.geteuid() != 0:
            raise RuntimeError('Run as root for read-only access to service configuration.')
        run(json.loads(args.profile.read_text()), check)
    except Exception as error:
        # Never include subprocess output, config values or exception messages (may contain secrets).
        check('inspection-completed', False, 'Inspection interrupted (' + type(error).__name__ + '); inspect required configuration/tools locally and rerun.')
    ok = bool(results) and all(item['ok'] for item in results)
    print(json.dumps({'hostChecksPassed': ok, 'functionalAcceptanceStillRequired': True, 'checks': results}, indent=2))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
